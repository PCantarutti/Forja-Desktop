"""Tela Voz (texto para fala). Motores plugáveis, cada um com o seu runtime instalado só quando a pessoa pede:

- "f5": F5-TTS/E2-TTS (o oficial ou qualquer fine-tune, ex.: pt-br) — tts_f5.py.
- "fish": Fish Audio no formato do fish-speech (S2-pro e afins; aceita [tags] de emoção no texto) — tts_fish.py.

Runtime em RUNTIMES/tts/<motor>, montado na máquina de quem usa (nada é empacotado): o `uv` (o do PATH ou baixado do
GitHub) cria o venv e instala os pacotes com o PyTorch da GPU detectada (--torch-backend: NVIDIA = CUDA pelo driver,
Intel = XPU, o resto = CPU). Python e cache do uv ficam em RUNTIMES/tts e são divididos entre os motores; apagar a
pasta do motor desinstala ele.

Modelo = {nome, motor, ...campos do motor} (f5: arquitetura, ckpt, vocab, minusculas, numeros; fish: modelo = dono/repo
do Hugging Face ou pasta). Voz = áudio de referência + a transcrição (vazia: o Whisper transcreve e ela é guardada).
Ficam em DATA_DIR/tts (config.json, vozes/, saida/<conversa>/).

O motor sobe uma vez e fica de pé com o modelo na memória (carregar o Fish leva mais de um minuto): um por vez, trocado
quando o pedido usa outro modelo e derrubado depois de OCIOSO segundos parado, ou quando um LLM/imagem precisa da GPU
(liberar_gpu). Cada geração é um par de mensagens de uma Conversation(kind="tts"): a do usuário com o texto, a do
assistente com meta["tts"] (estado, fase, arquivo, duração...).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from . import comfy, config, db, downloads, localai, native
from .tools import ToolError

PASTA = localai.RUNTIMES / "tts"
DADOS = config.DATA_DIR / "tts"
VOZES = DADOS / "vozes"
SAIDA = DADOS / "saida"
CONFIG = DADOS / "config.json"
UV_URL = {"win32": "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",
          "linux": "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz"}
BACKEND = {"nvidia": "auto", "intel": "xpu"}  # amd/nenhuma: cpu (ROCm no Windows ainda não tem roda estável)
ARQUITETURAS = ["F5TTS_v1_Base", "F5TTS_Base", "E2TTS_Base", "F5TTS_v1_Small", "F5TTS_Small", "E2TTS_Small"]
EXT_AUDIO = (".wav", ".mp3", ".flac", ".ogg", ".m4a")
OCIOSO = 300  # segundos parado até o motor sair e devolver a VRAM
FISH_COMMIT = "214da3cd841bda85da2496b96cd3c4d7edb1337e"  # testado na Arc B580 em 2026-10-06 (S2-pro em int8)
MOTORES = {
    "f5": {"nome": "F5-TTS / E2-TTS", "script": "tts_f5.py", "python": "3.11", "gb": 5,
           "pacotes": ["f5-tts", "num2words"]},
    "fish": {"nome": "Fish Audio (S2-pro e afins)", "script": "tts_fish.py", "python": "3.12", "gb": 6,
             "fonte": f"https://github.com/fishaudio/fish-speech/archive/{FISH_COMMIT}.zip",
             # o fish-speech fixa protobuf<6, mas o tensorboard que ele puxa precisa do 6.31; transformers novo
             # demais não lê o tokenizer dele, e sem o limite o resolvedor volta até o 4.12 (que nem compila)
             "pacotes": ["transformers>=4.50,<=4.57.3"], "override": ["protobuf==6.31.1"],
             "antes": ["randomname"]},  # só compila sozinho: no build em paralelo falha no Windows (WinError 127)
}

_cfg = threading.Lock()   # config.json e as instalações
_lock = threading.Lock()  # ponytail: uma geração por vez numa trava global; fila de verdade se precisar de ordem
_rodando: dict[int, int] = {}  # conv_id -> gerações pendentes (o /api/activity acende a bolinha)
_instalando: dict[str, dict] = {}
_motor: dict = {}  # o processo de pé: proc, chave, nome, dispositivo, ultimo (uso), ocupado


# ------------------------------------------------------------------ runtime

def _py(motor: str) -> Path:
    return PASTA / motor / "venv" / ("Scripts/python.exe" if native.WINDOWS else "bin/python")


def instalado(motor: str) -> bool:
    return _py(motor).is_file() and (PASTA / motor / "pronto").is_file()


def estado() -> dict:
    g = comfy.gpu()
    motores = {}
    for k, m in MOTORES.items():
        job = _instalando.get(k)
        motores[k] = {"nome": m["nome"], "instalado": instalado(k), "gb": m["gb"],
                      "instalando": job["id"] if job and job["status"] == "running" else ""}
    vivo = _motor.get("proc") and _motor["proc"].poll() is None
    return {"motores": motores, "gpu": g, "backend": BACKEND.get(g, "cpu"), "modelos": modelos(), "vozes": vozes(),
            "arquiteturas": ARQUITETURAS, "carregado": {"nome": _motor["nome"], "dispositivo": _motor["dispositivo"]}
            if vivo else None}


def _uv(job: dict) -> str:
    achado = shutil.which("uv")
    if achado:
        return achado
    exe = PASTA / "uv" / ("uv.exe" if native.WINDOWS else "uv")
    if exe.is_file():
        return str(exe)
    url = UV_URL.get(sys.platform)
    if not url:
        raise ToolError(f"Sem uv para {sys.platform}: instale o uv (docs.astral.sh/uv) e tente de novo.")
    arq = PASTA / "uv" / url.rsplit("/", 1)[-1]
    downloads._fetch(url, arq, job, 0, 0)
    if arq.suffix == ".zip":
        with zipfile.ZipFile(arq) as z:
            for m in z.namelist():
                if Path(m).name in ("uv.exe", "uvx.exe"):
                    (PASTA / "uv" / Path(m).name).write_bytes(z.read(m))
    else:
        import tarfile
        with tarfile.open(arq) as t:
            for m in t.getmembers():
                if Path(m.name).name == "uv":
                    exe.write_bytes(t.extractfile(m).read())  # type: ignore[union-attr]
                    exe.chmod(0o755)
    arq.unlink(missing_ok=True)
    return str(exe)


def _passo(job: dict, argv: list[str], detalhe: str) -> None:
    downloads.update(job["id"], detail=detalhe)
    env = {**os.environ, "UV_CACHE_DIR": str(PASTA / "cache"), "UV_PYTHON_INSTALL_DIR": str(PASTA / "python")}
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", env=env, **native.popen_kwargs())
    fim: list[str] = []
    for linha in proc.stdout:  # type: ignore[union-attr]
        if linha.strip():
            fim = (fim + [linha.strip()])[-8:]
            downloads.update(job["id"], detail=f"{detalhe} · {linha.strip()[:90]}")
        if downloads.cancelled(job["id"]):
            native.kill_tree(proc)
            raise ToolError("Instalação cancelada.")
    if proc.wait():
        raise ToolError(f"{detalhe} falhou: " + " | ".join(fim)[-600:])


def _fonte(job: dict, url: str, destino: Path) -> Path:
    """Código-fonte de um commit do GitHub (o pacote não está no PyPI), aberto sem a pasta raiz do zip."""
    arq = destino.with_suffix(".zip")
    downloads._fetch(url, arq, job, 0, 0)
    shutil.rmtree(destino, ignore_errors=True)
    with zipfile.ZipFile(arq) as z:
        for m in z.infolist():
            rel = Path(*Path(m.filename).parts[1:])
            if m.is_dir() or not rel.parts:
                continue
            (destino / rel).parent.mkdir(parents=True, exist_ok=True)
            (destino / rel).write_bytes(z.read(m))
    arq.unlink(missing_ok=True)
    return destino


def _instalar(motor: str, job: dict) -> None:
    m = MOTORES[motor]
    base = PASTA / motor
    try:
        base.mkdir(parents=True, exist_ok=True)
        (base / "pronto").unlink(missing_ok=True)
        uv = _uv(job)
        _passo(job, [uv, "venv", str(base / "venv"), "--python", m["python"], "--clear"], "criando o Python")
        py = ["--python", str(_py(motor))]
        for pacote in m.get("antes", []):
            _passo(job, [uv, "pip", "install", *py, pacote], f"compilando {pacote}")
        extra = []
        if m.get("override"):
            (base / "override.txt").write_text("\n".join(m["override"]), "utf-8")
            extra = ["--override", str(base / "override.txt")]
        if m.get("fonte"):
            downloads.update(job["id"], detail="baixando o código do motor")
            extra += ["-e", str(_fonte(job, m["fonte"], base / "src"))]
        _passo(job, [uv, "pip", "install", *py, "--torch-backend", BACKEND.get(comfy.gpu(), "cpu"), *extra,
                     *m["pacotes"]], f"instalando PyTorch e {m['nome']} (uns {m['gb']} GB)")
        (base / "pronto").write_text(time.strftime("%Y-%m-%d %H:%M"), "utf-8")
        downloads.finish(job["id"], result=str(base))
    except Exception as e:
        downloads.finish(job["id"], error=str(e) if isinstance(e, ToolError) else f"{e.__class__.__name__}: {e}")


def instalar(motor: str) -> dict:
    """Também serve para atualizar: refaz o venv (o cache do uv evita baixar tudo de novo)."""
    if motor not in MOTORES:
        raise ToolError("Motor desconhecido: " + ", ".join(MOTORES))
    with _cfg:
        job = _instalando.get(motor)
        if job and job["status"] == "running":
            return job
        if _motor.get("chave", ("",))[0] == motor:
            descarregar()  # o venv vai ser refeito: o processo de pé segura os arquivos
        job = _instalando[motor] = downloads.create("runtime", f"Motor de voz: {MOTORES[motor]['nome']}")
    threading.Thread(target=_instalar, args=(motor, job), daemon=True).start()
    return job


# ------------------------------------------------------------------ modelos e vozes

def _ler() -> dict:
    try:
        return json.loads(CONFIG.read_text("utf-8"))
    except (OSError, ValueError):
        return {"modelos": []}


def _gravar(d: dict) -> None:
    DADOS.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(CONFIG)


def modelos() -> list[dict]:
    return [{"motor": "f5", **m} for m in _ler().get("modelos") or []]  # os de antes do Fish não tinham motor


def salvar_modelo(m: dict) -> list[dict]:
    nome = str(m.get("nome") or "").strip()
    if not nome:
        raise ToolError("Dê um nome ao modelo.")
    motor = m.get("motor") or "f5"
    texto = {k: str(m.get(k) or "").strip().strip('"') for k in ("ckpt", "vocab", "numeros", "modelo")}
    if motor == "f5":
        if m.get("arquitetura") not in ARQUITETURAS:
            raise ToolError("Arquitetura desconhecida: " + ", ".join(ARQUITETURAS))
        for k in ("ckpt", "vocab"):
            v = texto[k]
            if v and not v.startswith("hf://") and not Path(v).is_file():
                raise ToolError(f"Arquivo não encontrado: {v} (use um caminho local ou hf://dono/repo/arquivo).")
        novo = {"nome": nome, "motor": motor, "arquitetura": m["arquitetura"], "ckpt": texto["ckpt"],
                "vocab": texto["vocab"], "numeros": texto["numeros"], "minusculas": bool(m.get("minusculas"))}
    elif motor == "fish":
        v = texto["modelo"]
        if not (Path(v).is_dir() or re.fullmatch(r"[\w.-]+/[\w.-]+", v)):
            raise ToolError("Informe o repositório do Hugging Face (ex.: fishaudio/s2-pro) ou a pasta do modelo.")
        novo = {"nome": nome, "motor": motor, "modelo": v}
    else:
        raise ToolError("Motor desconhecido: " + ", ".join(MOTORES))
    with _cfg:
        d = _ler()
        d["modelos"] = [x for x in d.get("modelos") or [] if x["nome"] != nome] + [novo]
        _gravar(d)
    return modelos()


def apagar_modelo(nome: str) -> list[dict]:
    with _cfg:
        d = _ler()
        d["modelos"] = [x for x in d.get("modelos") or [] if x["nome"] != nome]
        _gravar(d)
    return modelos()


def _slug(nome: str) -> str:
    return re.sub(r"[^\w-]+", "-", nome.strip()).strip("-")[:60] or "voz"


def vozes() -> list[dict]:
    out = []
    for f in sorted(VOZES.glob("*.json")):
        try:
            v = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if (VOZES / v.get("arquivo", "")).is_file():
            out.append({**v, "id": f.stem, "caminho": str(VOZES / v["arquivo"])})
    return out


def salvar_voz(nome: str, texto: str, arquivo: str, dados: bytes) -> list[dict]:
    ext = Path(arquivo).suffix.lower()
    if ext not in EXT_AUDIO:
        raise ToolError("Envie um áudio (" + ", ".join(EXT_AUDIO) + ").")
    if len(dados) > 30_000_000:
        raise ToolError("Áudio maior que 30 MB: a referência boa tem de 5 a 15 segundos.")
    vid = _slug(nome)
    VOZES.mkdir(parents=True, exist_ok=True)
    for velho in VOZES.glob(vid + ".*"):
        velho.unlink()
    (VOZES / (vid + ext)).write_bytes(dados)
    (VOZES / (vid + ".json")).write_text(json.dumps({"nome": nome.strip() or vid, "texto": texto.strip(),
                                                     "arquivo": vid + ext}, ensure_ascii=False), "utf-8")
    return vozes()


def _transcrita(vid: str, texto: str) -> None:
    """O Whisper transcreveu a referência: fica guardado, a próxima geração não paga de novo."""
    f = VOZES / (vid + ".json")
    try:
        v = json.loads(f.read_text("utf-8"))
        if not v.get("texto"):
            f.write_text(json.dumps({**v, "texto": texto}, ensure_ascii=False), "utf-8")
    except (OSError, ValueError):
        pass


def apagar_voz(vid: str) -> list[dict]:
    for f in VOZES.glob(_slug(vid) + ".*"):
        f.unlink()
    return vozes()


def servivel(path: str) -> Path:
    """A rota de arquivo só entrega áudio de dentro de DATA_DIR/tts."""
    f = Path(path).resolve()
    if not f.is_relative_to(DADOS.resolve()) or f.suffix.lower() not in EXT_AUDIO or not f.is_file():
        raise ToolError("Arquivo não encontrado.")
    return f


# ------------------------------------------------------------------ o motor de pé

def descarregar() -> None:
    p = _motor.get("proc")
    if p and p.poll() is None:
        native.kill_tree(p)
    _motor.clear()


def liberar_gpu() -> None:
    """Um LLM ou o sd.cpp vai precisar da VRAM: o motor de voz parado sai; gerando, quem chamou espera ou desiste."""
    if _motor.get("ocupado"):
        raise ToolError("A tela Voz está gerando um áudio agora. Espere terminar: os dois disputam a mesma VRAM.")
    descarregar()


def _vigia_ocioso() -> None:
    while True:
        time.sleep(30)
        if _motor and not _motor.get("ocupado") and time.time() - _motor.get("ultimo", 0) > OCIOSO:
            with _lock:
                if _motor and not _motor.get("ocupado") and time.time() - _motor.get("ultimo", 0) > OCIOSO:
                    descarregar()


threading.Thread(target=_vigia_ocioso, daemon=True).start()


def _argv(m: dict) -> list[str]:
    if m["motor"] == "fish":
        return ["--modelo", m["modelo"]]
    return ["--arquitetura", m["arquitetura"], "--ckpt", m.get("ckpt", ""), "--vocab", m.get("vocab", "")]


def _ler_ate(proc, fim: tuple[str, ...], mid: int, voz: str = "") -> str:
    """Lê o stdout do motor até uma linha de `fim`, levando FASE/PROGRESSO à mensagem. O processo morto (cancelado,
    sem memória) acaba o laço: devolve as últimas linhas soltas, que dizem o porquê."""
    outras: list[str] = []
    for linha in proc.stdout:
        linha = linha.strip()
        if linha.startswith("FASE "):
            _patch(mid, fase=linha[5:])
        elif linha.startswith("PROGRESSO "):
            try:
                _patch(mid, progresso=float(linha.split()[1]))
            except (IndexError, ValueError):
                pass
        elif linha.startswith("TRANSCRICAO ") and voz:
            _transcrita(voz, linha[12:])
        elif linha.startswith(fim):
            return linha
        elif linha:
            outras = (outras + [linha])[-6:]
    return "ERRO O motor de voz saiu no meio. " + " | ".join(outras)[-500:]


def _subir(m: dict, mid: int) -> subprocess.Popen:
    chave = (m["motor"], json.dumps(_argv(m)))
    p = _motor.get("proc")
    if p and p.poll() is None and _motor.get("chave") == chave:
        return p
    descarregar()
    env = dict(os.environ)
    ff = localai.find_exe("ffmpeg")
    if ff:  # o pydub do F5 usa o ffmpeg do PATH quando acha
        env["PATH"] = str(Path(ff).parent) + os.pathsep + env.get("PATH", "")
    env["HF_HUB_CACHE"] = str(PASTA / "hf")  # modelos e Whisper ficam no runtime, não no perfil
    if localai.hf_token():
        env["HF_TOKEN"] = localai.hf_token()  # modelos com termos aceitos na conta (ex.: fishaudio/s1-mini)
    proc = subprocess.Popen([str(_py(m["motor"])), "-X", "utf8", "-s",
                             str(Path(__file__).with_name(MOTORES[m["motor"]]["script"])), *_argv(m)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=env, cwd=str(PASTA / m["motor"]),
                            **native.popen_kwargs())
    _motor.update(proc=proc, chave=chave, nome=m["nome"], dispositivo="", ultimo=time.time(), ocupado=True)
    _patch(mid, fase="carregando o modelo")
    fim = _ler_ate(proc, ("PRONTO", "ERRO"), mid)
    if not fim.startswith("PRONTO"):
        descarregar()
        raise ToolError(fim[5:])
    _motor["dispositivo"] = fim[7:]
    return proc


# ------------------------------------------------------------------ geração

def pendentes() -> list[int]:
    return [c for c, n in list(_rodando.items()) if n > 0]


def _patch(mid: int, **tts) -> None:
    with db.session() as s:
        m = s.get(db.Message, mid)
        if m:
            meta = dict(m.meta or {})
            meta["tts"] = {**meta.get("tts", {}), **tts}
            m.meta = meta  # JSON puro: só persiste reatribuindo
            if tts.get("estado") in ("pronto", "erro", "cancelado"):
                m.status = "pronto"
            s.commit()


PARAMS = {"f5": {"velocidade": 1.0, "passos": 32, "sem_silencio": False},
          "fish": {"temperatura": 1.0, "top_p": 0.9}}


def gerar(conv_id: int, body: dict) -> dict:
    texto = str(body.get("texto") or "").strip()
    if not texto:
        raise ToolError("Escreva o texto a falar.")
    m = next((x for x in modelos() if x["nome"] == body.get("modelo")), None)
    if not m:
        raise ToolError("Escolha um modelo (Modelos › Adicionar).")
    if not instalado(m["motor"]):
        raise ToolError(f"Falta o motor {MOTORES[m['motor']]['nome']}: instale no topo da tela.")
    v = next((x for x in vozes() if x["id"] == body.get("voz")), None)
    if not v:
        raise ToolError("Escolha uma voz de referência (Vozes › Adicionar).")
    if not (_motor.get("proc") and _motor["proc"].poll() is None):  # de pé, a VRAM já é nossa (troca de modelo é aqui)
        from .lotes import _liberar_vram
        _liberar_vram(bool(body.get("confirm")))  # LLM na VRAM: a tela pergunta (409) antes de descarregar
    params = {"modelo": m["nome"], "voz": v["nome"], "semente": int(body.get("semente", -1)),
              **{k: type(d)(body.get(k, d)) for k, d in PARAMS[m["motor"]].items()}}
    with db.session() as s:
        conv = s.get(db.Conversation, conv_id)
        if not conv or conv.kind != "tts":
            raise ToolError("Conversa de voz não encontrada.")
        if conv.title == "Nova conversa":
            conv.title = texto.splitlines()[0][:60]
        conv.updated_at = db._now()
        s.add(db.Message(conversation_id=conv_id, role="user", content=texto, meta={"tts": params}))
        a = db.Message(conversation_id=conv_id, role="assistant", content="", status="running",
                       meta={"tts": {**params, "estado": "na fila"}})
        s.add(a)
        s.commit()
        mid = a.id
    job = downloads.create("tts", f"Voz: {texto[:40]}")
    _rodando[conv_id] = _rodando.get(conv_id, 0) + 1
    _patch(mid, job=job["id"])
    threading.Thread(target=_rodar, args=(conv_id, mid, job, texto, m, v, params), daemon=True).start()
    return {"message_id": mid, "job": job["id"]}


def _rodar(conv_id: int, mid: int, job: dict, texto: str, m: dict, v: dict, p: dict) -> None:
    vigia = None
    try:
        with _lock:
            if downloads.cancelled(job["id"]):
                raise ToolError("Cancelado.")
            _patch(mid, estado="gerando", fase="começando")

            def cancela() -> None:  # carregar passa um minuto calado: cancelar não espera a próxima linha
                while not fim.is_set():
                    if downloads.cancelled(job["id"]):
                        descarregar()  # o modelo sai junto; o próximo pedido carrega de novo
                        return
                    time.sleep(0.5)
            fim = threading.Event()
            vigia = threading.Thread(target=cancela, daemon=True)
            vigia.start()
            proc = _subir(m, mid)
            _motor.update(ocupado=True)
            saida = SAIDA / str(conv_id) / f"{mid}.wav"
            saida.parent.mkdir(parents=True, exist_ok=True)
            pedido = {**p, **{k: m[k] for k in ("minusculas", "numeros") if k in m}, "texto": texto,
                      "ref": v["caminho"], "ref_texto": v.get("texto", ""), "saida": str(saida)}
            proc.stdin.write(json.dumps(pedido, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            linha = _ler_ate(proc, ("OK", "ERRO"), mid, v["id"])
            if downloads.cancelled(job["id"]):
                raise ToolError("Cancelado.")
            if not linha.startswith("OK"):
                if proc.poll() is not None or "DEVICE_LOST" in linha:
                    descarregar()  # o contexto da GPU morreu: o próximo pedido sobe um processo novo
                if "DEVICE_LOST" in linha:
                    raise ToolError("A GPU perdeu o contexto (device lost), provavelmente por falta de VRAM. O motor foi "
                                    "encerrado; feche o que estiver usando a GPU antes de tentar de novo.")
                raise ToolError(linha[5:])
            duracao, semente, segundos = linha.split()[1:4]
            _patch(mid, estado="pronto", arquivo=str(saida), duracao=float(duracao), semente_usada=int(semente),
                   segundos=float(segundos), fase="", dispositivo=_motor.get("dispositivo", ""))
            downloads.finish(job["id"], result=str(saida))
    except Exception as e:
        erro = str(e) if isinstance(e, ToolError) else f"{e.__class__.__name__}: {e}"
        if downloads.cancelled(job["id"]):
            erro = "Cancelado."
        _patch(mid, estado="cancelado" if erro == "Cancelado." else "erro", erro=erro, fase="")
        downloads.finish(job["id"], error=erro)
    finally:
        if vigia:
            fim.set()
        if _motor:
            _motor.update(ocupado=False, ultimo=time.time())
        _rodando[conv_id] = _rodando.get(conv_id, 1) - 1


def cancelar(mid: int) -> None:
    with db.session() as s:
        m = s.get(db.Message, mid)
        job = ((m.meta or {}).get("tts") or {}).get("job") if m else None
    if job:
        downloads.cancel(job)

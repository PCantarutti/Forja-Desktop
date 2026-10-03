"""Tela Voz (texto para fala) com modelos da família F5-TTS/E2-TTS: o oficial ou qualquer fine-tune (ex.: pt-br).

Runtime próprio em RUNTIMES/tts, montado na máquina de quem usa (nada é empacotado): o `uv` (o do PATH ou baixado
do GitHub) cria um venv com Python 3.11 e instala f5-tts + num2words, com o PyTorch da GPU detectada
(--torch-backend: NVIDIA = CUDA pelo driver, Intel = XPU, o resto = CPU). Tudo — Python, cache, venv — fica dentro
da pasta: apagar a pasta desinstala.

Modelo = {nome, arquitetura (um config do f5-tts: F5TTS_v1_Base, F5TTS_Base, E2TTS_Base...), ckpt, vocab,
minusculas, numeros}; ckpt/vocab aceitam caminho local ou hf://dono/repo/arquivo (baixa na 1ª geração). Vazio =
o oficial da arquitetura. Voz = áudio de referência + a transcrição dele (vazia: o Whisper transcreve).
Ficam em DATA_DIR/tts (config.json, vozes/, saida/<conversa>/).

Cada geração é um par de mensagens de uma Conversation(kind="tts"): a do usuário com o texto, a do assistente com
meta["tts"] (estado, arquivo, duração...). Uma por vez (a GPU é uma só), em thread; cada uma sobe o tts_job.py e
derruba no fim, como o ComfyUI.
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
JOB = Path(__file__).with_name("tts_job.py")
PACOTES = ["f5-tts", "num2words"]
UV_URL = {"win32": "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",
          "linux": "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz"}
BACKEND = {"nvidia": "auto", "intel": "xpu"}  # amd/nenhuma: cpu (ROCm no Windows ainda não tem roda estável)
ARQUITETURAS = ["F5TTS_v1_Base", "F5TTS_Base", "E2TTS_Base", "F5TTS_v1_Small", "F5TTS_Small", "E2TTS_Small"]
EXT_AUDIO = (".wav", ".mp3", ".flac", ".ogg", ".m4a")

_cfg = threading.Lock()  # config.json e a instalação
_lock = threading.Lock()   # ponytail: uma geração por vez numa trava global; fila de verdade se precisar de ordem
_rodando: dict[int, int] = {}  # conv_id -> gerações pendentes (o /api/activity acende a bolinha)
_instalando: dict = {}


# ------------------------------------------------------------------ runtime

def python() -> Path | None:
    exe = PASTA / "venv" / ("Scripts/python.exe" if native.WINDOWS else "bin/python")
    return exe if exe.is_file() and (PASTA / "pronto").is_file() else None


def estado() -> dict:
    job = _instalando.get("job")
    return {"instalado": bool(python()), "gpu": comfy.gpu(), "backend": BACKEND.get(comfy.gpu(), "cpu"),
            "instalando": job["id"] if job and job["status"] == "running" else ""}


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
    env = {**os.environ, "UV_CACHE_DIR": str(PASTA / "cache"),
           "UV_PYTHON_INSTALL_DIR": str(PASTA / "python")}
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


def _instalar(job: dict) -> None:
    try:
        PASTA.mkdir(parents=True, exist_ok=True)
        (PASTA / "pronto").unlink(missing_ok=True)
        uv = _uv(job)
        venv = PASTA / "venv"
        _passo(job, [uv, "venv", str(venv), "--python", "3.11", "--clear"], "criando o Python")
        py = venv / ("Scripts/python.exe" if native.WINDOWS else "bin/python")
        _passo(job, [uv, "pip", "install", "--python", str(py), "--torch-backend", BACKEND.get(comfy.gpu(), "cpu"),
                     *PACOTES], "instalando PyTorch e F5-TTS (uns 3 GB)")
        (PASTA / "pronto").write_text(time.strftime("%Y-%m-%d %H:%M"), "utf-8")
        downloads.finish(job["id"], result=str(PASTA))
    except Exception as e:
        downloads.finish(job["id"], error=str(e) if isinstance(e, ToolError) else f"{e.__class__.__name__}: {e}")


def instalar() -> dict:
    """Também serve para atualizar: refaz o venv (o cache do uv evita baixar tudo de novo)."""
    with _cfg:
        job = _instalando.get("job")
        if job and job["status"] == "running":
            return job
        job = _instalando["job"] = downloads.create("runtime", "Motor de voz (F5-TTS)")
    threading.Thread(target=_instalar, args=(job,), daemon=True).start()
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
    return _ler().get("modelos") or []


def salvar_modelo(m: dict) -> list[dict]:
    nome = str(m.get("nome") or "").strip()
    if not nome:
        raise ToolError("Dê um nome ao modelo.")
    if m.get("arquitetura") not in ARQUITETURAS:
        raise ToolError("Arquitetura desconhecida: " + ", ".join(ARQUITETURAS))
    novo = {"nome": nome, "arquitetura": m["arquitetura"],
            **{k: str(m.get(k) or "").strip().strip('"') for k in ("ckpt", "vocab", "numeros")},
            "minusculas": bool(m.get("minusculas"))}
    for k in ("ckpt", "vocab"):
        v = novo[k]
        if v and not v.startswith("hf://") and not Path(v).is_file():
            raise ToolError(f"Arquivo não encontrado: {v} (use um caminho local ou hf://dono/repo/arquivo).")
    with _cfg:
        d = _ler()
        d["modelos"] = [x for x in d.get("modelos") or [] if x["nome"] != nome] + [novo]
        _gravar(d)
    return d["modelos"]


def apagar_modelo(nome: str) -> list[dict]:
    with _cfg:
        d = _ler()
        d["modelos"] = [x for x in d.get("modelos") or [] if x["nome"] != nome]
        _gravar(d)
    return d["modelos"]


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
        raise ToolError("Áudio maior que 30 MB: a referência boa tem de 5 a 12 segundos.")
    vid = _slug(nome)
    VOZES.mkdir(parents=True, exist_ok=True)
    for velho in VOZES.glob(vid + ".*"):
        velho.unlink()
    (VOZES / (vid + ext)).write_bytes(dados)
    (VOZES / (vid + ".json")).write_text(json.dumps({"nome": nome.strip() or vid, "texto": texto.strip(),
                                                     "arquivo": vid + ext}, ensure_ascii=False), "utf-8")
    return vozes()


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


def gerar(conv_id: int, texto: str, modelo: str, voz: str, velocidade: float = 1.0, passos: int = 32,
          semente: int = -1, sem_silencio: bool = False) -> dict:
    texto = texto.strip()
    if not texto:
        raise ToolError("Escreva o texto a falar.")
    if not python():
        raise ToolError("Falta o motor de voz: instale em Instalar motor, no topo da tela.")
    m = next((x for x in modelos() if x["nome"] == modelo), None)
    if not m:
        raise ToolError("Escolha um modelo (Modelos › Adicionar).")
    v = next((x for x in vozes() if x["id"] == voz), None)
    if not v:
        raise ToolError("Escolha uma voz de referência (Vozes › Adicionar).")
    params = {"modelo": modelo, "voz": v["nome"], "velocidade": velocidade, "passos": passos, "semente": semente,
              "sem_silencio": sem_silencio}
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
    try:
        with _lock:
            if downloads.cancelled(job["id"]):
                raise ToolError("Cancelado.")
            _patch(mid, estado="gerando", fase="começando")
            saida = SAIDA / str(conv_id) / f"{mid}.wav"
            saida.parent.mkdir(parents=True, exist_ok=True)
            txt = saida.with_suffix(".txt")
            txt.write_text(texto, "utf-8")  # pela linha de comando o texto longo/acentuado sofre no Windows
            t0 = time.time()
            fim = _processo(job["id"], mid, [
                "--arquitetura", m["arquitetura"], "--ckpt", m.get("ckpt", ""), "--vocab", m.get("vocab", ""),
                "--ref", v["caminho"], "--ref-texto", v.get("texto", ""), "--texto", str(txt), "--saida", str(saida),
                "--velocidade", f"{p['velocidade']:.2f}", "--passos", str(int(p["passos"])),
                "--semente", str(int(p["semente"])), *(["--sem-silencio"] if p["sem_silencio"] else []),
                *(["--minusculas"] if m.get("minusculas") else []), *(["--numeros", m["numeros"]] if m.get("numeros") else [])])
            duracao, semente = fim.split()[1:3]
            _patch(mid, estado="pronto", arquivo=str(saida), duracao=float(duracao), semente_usada=int(semente),
                   segundos=round(time.time() - t0, 1), fase="")
            downloads.finish(job["id"], result=str(saida))
    except Exception as e:
        erro = str(e) if isinstance(e, ToolError) else f"{e.__class__.__name__}: {e}"
        _patch(mid, estado="cancelado" if downloads.cancelled(job["id"]) else "erro", erro=erro, fase="")
        downloads.finish(job["id"], error=erro)
    finally:
        _rodando[conv_id] = _rodando.get(conv_id, 1) - 1


def _processo(job_id: str, mid: int, args: list[str]) -> str:
    env = dict(os.environ)
    ff = localai.find_exe("ffmpeg")
    if ff:  # o pydub do F5 usa o ffmpeg do PATH quando acha
        env["PATH"] = str(Path(ff).parent) + os.pathsep + env.get("PATH", "")
    env["HF_HUB_CACHE"] = str(PASTA / "hf")  # checkpoints e Whisper ficam no runtime, não no perfil
    proc = subprocess.Popen([str(python()), "-X", "utf8", "-s", str(JOB), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", env=env, **native.popen_kwargs())

    def vigia() -> None:  # carregar o modelo passa segundos calado: cancelar não espera a próxima linha
        while proc.poll() is None:
            if downloads.cancelled(job_id):
                native.kill_tree(proc)
                return
            time.sleep(0.5)
    threading.Thread(target=vigia, daemon=True).start()
    fim, outras = "", []
    for linha in proc.stdout:  # type: ignore[union-attr]
        linha = linha.strip()
        if linha.startswith("FASE "):
            _patch(mid, fase=linha[5:])
            downloads.update(job_id, detail=linha[5:])
        elif linha.startswith("PROGRESSO "):
            try:
                _patch(mid, progresso=float(linha.split()[1]))
            except (IndexError, ValueError):
                pass
        elif linha.startswith(("OK", "ERRO")):
            fim = linha
        elif linha:
            outras = (outras + [linha])[-6:]
    proc.wait()
    if downloads.cancelled(job_id):
        raise ToolError("Cancelado.")
    if not fim.startswith("OK"):
        raise ToolError(fim[5:] if fim.startswith("ERRO") else
                        f"O motor de voz saiu sem resultado (código {proc.returncode}). " + " | ".join(outras)[-500:])
    return fim


def cancelar(mid: int) -> None:
    with db.session() as s:
        m = s.get(db.Message, mid)
        job = ((m.meta or {}).get("tts") or {}).get("job") if m else None
    if job:
        downloads.cancel(job)

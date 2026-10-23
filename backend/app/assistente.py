"""Assistente de voz F.O.R.J.A (Ferramenta Operacional Relativamente Jumenta e Autônoma).

O microfone, a palavra "Forja", o Whisper e a voz rápida (Kokoro) moram num processo à parte, assistente_ouvido.py, no
venv RUNTIMES/assistente (instalado pela tela Voz, como os motores dela). Aqui fica o resto: liga/desliga, o que fazer
com cada comando ouvido e o que falar de volta.

Comandos ("Forja, ..."):
- "crie uma imagem de um gato esquiando": conversa nova em Imagens, gerada com o modelo favorito de imagem.
- "gere um vídeo de ...": o mesmo em Vídeo, com o modelo favorito de vídeo.
- "no modo agente crie um site de ecommerce": conversa nova no Agente. Se uma conversa já trabalha numa pasta com nome
  parecido, pergunta (por voz) se continua nela ou cria uma pasta nova; senão cria a pasta em `pasta_projetos`.
- o resto: só responde falando, com o modelo e a busca na web, sem abrir nem criar nada no Forja.

- "abra um chat no Forja sobre X": pergunta o modelo e abre a conversa no Chat.
- "para", "cancela", "silêncio": cala e cancela o que estiver fazendo.

Modelo do assistente: o escolhido na página (`modelo`) ou, vazio, o do último turno iniciado no PC ou no celular
(mobile.defaults). Pergunta falada nunca derruba trabalho do Forja: com outro modelo local no ar e algo rodando, usa o que
está no ar. O modelo que o assistente carregou sai da VRAM depois de `ocioso_min` minutos sem uso (se ninguém mais usou).
Atalho global (Electron) chama /api/assistente/chamar: interrompe a fala e ouve o próximo pedido sem "Forja".
A interface acompanha pelo /api/activity: `voz.ultimo` muda e o PC abre a conversa criada.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import subprocess
import threading
import time
import unicodedata
from pathlib import Path

from sqlalchemy import select

from . import config, db, downloads, localai, mobile, native, tts
from .tools import ToolError

PASTA = localai.RUNTIMES / "assistente"
MODELOS = PASTA / "kokoro"
CONFIG = config.DATA_DIR / "assistente.json"
PACOTES = ["faster-whisper", "sounddevice", "numpy", "kokoro-onnx", "openwakeword", "scikit-learn"]
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_ARQUIVOS = ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"]
# detector treinado de "Forja": openWakeWord só pelas características (os dois .onnx), classificador nosso
DETECTOR = PASTA / "detector" / "forja.npz"
METRICAS = PASTA / "detector" / "metricas.json"
OWW = PASTA / "oww"
OWW_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
OWW_ARQUIVOS = ["melspectrogram.onnx", "embedding_model.onnx"]
PACOTES_DETECTOR = ["openwakeword", "scikit-learn"]
MINHA_VOZ = PASTA / "minha_voz"
GRAVA = ("import sys,wave,sounddevice as sd;"
         "a=sd.rec(int(float(sys.argv[3])*16000),samplerate=16000,channels=1,dtype='int16',device=sys.argv[2] or None);"
         "sd.wait();w=wave.open(sys.argv[1],'wb');w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);"
         "w.writeframes(a.tobytes());w.close()")
VOZES_KOKORO = [{"id": "pf_dora", "nome": "Dora (feminina)"}, {"id": "pm_alex", "nome": "Alex (masculina)"},
                {"id": "pm_santa", "nome": "Santa (masculina, grave)"}]
# A conversa falada mora só na memória (nunca no banco nem no log): some quando o modelo do assistente sai da placa,
# ou quando a pessoa pede para transformá-la num chat do Forja ("salva nossa conversa num chat").
TRANSCRICAO_MAX = 60
NOTA_FALADA = ("A resposta será FALADA em voz alta por você, o assistente de voz F.O.R.J.A (Ferramenta Operacional "
               "Relativamente Jumenta e Autônoma). Responda curto (1 a 3 frases), em português falado e natural, sem "
               "markdown, listas, tabelas, links, emojis ou citações entre colchetes. Escreva números e siglas como se "
               "lê. Se usou a web, diga de onde veio em poucas palavras. A data e a hora que vêm com cada pergunta são "
               "as reais de agora: sua memória de treino pode ser mais antiga, então para fatos recentes confie na busca "
               "e nunca diga que a data é do futuro.")
REGRA_HUMOR = ("Regra do humor: o PADRÃO é responder sem piada, só a informação, com a personalidade apenas no tom. "
               "Faça uma piada SOMENTE se a pergunta for boba, óbvia ou pedir brincadeira E você tiver uma tirada "
               "realmente boa e curta; uma piada mais ou menos é pior que nenhuma. Nunca em notícias, assuntos sérios "
               "(saúde, dinheiro, tragédia, problema, erro), pedidos práticos ou perguntas de informação comum. A "
               "resposta certa vem primeiro; a piada, se houver, é UMA frase no fim e começa com a marca [piada] "
               "(ex.: \"Quatro. [piada] Quer que eu prepare a papinha também?\"). Nunca ofenda de verdade.")
SEM_PIADA = "Nesta resposta: nenhuma piada, ironia ou comentário engraçado. Só responda o que foi perguntado."
PAUSA_PIADA = 3  # depois de uma piada, as próximas respostas saem sem: humor que aparece sempre cansa
HUMORES = {
    "nenhum": {"nome": "Sem humor", "prompt": ""},
    "sarcastico": {"nome": "Sarcástico (estilo J.A.R.V.I.S.)", "prompt": (
        "Personalidade: sarcasmo elegante e seco, de mordomo digital britânico impecavelmente educado, no espírito do "
        "J.A.R.V.I.S. dos filmes do Homem de Ferro. Ironia fina, cortesia exagerada de propósito. Exemplo: pergunta "
        "\"quanto é 2 mais 2?\" -> \"Quatro. Quer que eu prepare a papinha também?\"")},
    "acido": {"nome": "Ácido (estilo GLaDOS)", "prompt": (
        "Personalidade: humor ácido de IA de laboratório, no espírito da GLaDOS de Portal 2: doçura falsa, elogios "
        "envenenados, condescendência científica, menções a testes e a ciência. Exemplo: pergunta \"quanto é 2 mais "
        "2?\" -> \"Quatro. Anotei no seu prontuário: completou a soma sem ajuda. Estou muito orgulhosa.\"")},
    "leve": {"nome": "Leve (bem-humorado)", "prompt": (
        "Personalidade: simpático e bem-humorado, com um trocadilho ou comentário divertido de vez em quando, sem "
        "alfinetar.")},
}
PADRAO = {"ligado": False, "mic": "", "saida": "", "whisper": "small", "motor": "kokoro", "voz": "pf_dora", "modelo_tts": "", "voz_tts": "",
          "humor": "sarcastico", "favoritos": [], "modelo": None, "ocioso_min": 5, "atalho": "",
          "detector": "whisper", "sensibilidade": 0.5, "raciocinio": "desligado", "permitidos": [],
          "imagem": "", "video": "", "pasta_projetos": ""}
MAX_TOKENS_FALA = 500  # resposta falada é curta; o teto evita um monólogo se o modelo desandar
# raciocínio do modelo antes de falar: desligado é o padrão (resposta falada quer velocidade); os outros são os
# esforços do Forja (config.REASONING_BUDGET), e o pensamento nunca é falado
RACIOCINIO = ("desligado", "baixo", "medio", "alto")
ESPERA_LIVRE_MIN = 30  # chat/agente com outro modelo espera no máximo isto o trabalho atual terminar
PENDENTE_S = 60  # uma pergunta dele ("Com qual modelo?", "continuo nessa pasta?") vale por isto
PARAR = re.compile(r"^\s*(?:para|pare|parar|cancela|cancele|cancelar|chega|silencio|cala a boca|cale-se|esquece|"
                   r"deixa pra la|deixa para la|nada|nao precisa|tchau)\b[\s.!,]*$")

_lock = threading.Lock()
_job: dict = {}
_estado: dict = {"estado": "desligado", "mic_nome": "", "erro": "", "ultimo": None, "ouviu": ""}
_proc: dict = {}  # o processo de pé: proc
_loop: list[asyncio.AbstractEventLoop] = []
_pendente: dict = {}  # pergunta feita por voz esperando resposta: {"tipo": "pasta", ...}
_atendendo = [0]  # comandos em andamento (estado "pensando")
_humor = {"pausa": 0}  # respostas que ainda saem sem piada depois da última (PAUSA_PIADA)
_conversa_chat: dict = {}  # conversa falada em memória: {"msgs": [...]} para o modelo (nada vai para o banco)
_transcricao: list[dict] = []  # o que a tela mostra: [{"quem": "voce"|"forja", "texto", "t"}], também só em memória
_tarefa: dict = {}  # o comando em andamento ({"f": Future}): comando novo, atalho ou "para" cancelam ele
_uso: dict = {}  # o modelo que o assistente usou por último: {"spec", "t" (monotonic), "llm_t"} (ociosidade)
_quedas: list[float] = []  # quando o ouvido caiu sozinho (reinício automático com limite)
_ao_subir: dict = {}  # o que mandar ao ouvido quando ele ficar pronto (atalho com ele desligado: escuta)
_carga: list[asyncio.Lock] = []  # uma carga de modelo do assistente por vez (atalho e pergunta chegam juntos)


def _trava_carga() -> asyncio.Lock:
    if not _carga:
        _carga.append(asyncio.Lock())  # criada no loop do backend, na primeira vez que alguém carrega
    return _carga[0]


# ------------------------------------------------------------------ configuração

def ler() -> dict:
    try:
        return {**PADRAO, **json.loads(CONFIG.read_text("utf-8"))}
    except (OSError, ValueError):
        return dict(PADRAO)


def gravar(patch: dict) -> dict:
    with _lock:
        d = {**ler(), **{k: v for k, v in patch.items() if k in PADRAO}}
        if d["motor"] not in ("kokoro", "tela"):
            raise ToolError("motor deve ser kokoro ou tela")
        if d["humor"] not in HUMORES:
            raise ToolError("humor deve ser " + ", ".join(HUMORES))
        favs = d["favoritos"]
        if not isinstance(favs, list) or len(favs) > 12 or any(
                not (isinstance(f, dict) and str(f.get("nome") or "").strip() and f.get("provider") and f.get("model"))
                for f in favs):
            raise ToolError("Cada favorito precisa de um nome de uso (ex.: código) e de um modelo; no máximo 12.")
        d["favoritos"] = [{"nome": str(f["nome"]).strip(), "provider": f["provider"], "model": f["model"]} for f in favs]
        perm = d["permitidos"]
        if not isinstance(perm, list) or len(perm) > 30 or any(
                not (isinstance(p, dict) and p.get("provider") and p.get("model")) for p in perm):
            raise ToolError("Cada modelo permitido precisa de provider e model; no máximo 30.")
        d["permitidos"] = [{"provider": p["provider"], "model": p["model"]} for p in perm]
        m = d["modelo"]
        if m is not None and not (isinstance(m, dict) and m.get("provider") and m.get("model")):
            raise ToolError("modelo precisa de provider e model (ou null para o do último envio).")
        d["modelo"] = {"provider": m["provider"], "model": m["model"]} if m else None
        try:
            d["ocioso_min"] = max(0, min(240, int(d["ocioso_min"])))
        except (TypeError, ValueError):
            raise ToolError("ocioso_min deve ser um número de minutos (0 = nunca).") from None
        d["atalho"] = str(d["atalho"] or "").strip()[:60]
        if d["raciocinio"] not in RACIOCINIO:
            raise ToolError("raciocinio deve ser " + ", ".join(RACIOCINIO))
        if d["detector"] not in ("whisper", "treinado", "ambos"):
            raise ToolError("detector deve ser whisper, treinado ou ambos")
        if d["detector"] != "whisper" and not DETECTOR.is_file():
            raise ToolError("Treine o detector de “Forja” antes de ligá-lo.")
        try:
            d["sensibilidade"] = max(0.0, min(1.0, float(d["sensibilidade"])))
        except (TypeError, ValueError):
            raise ToolError("sensibilidade vai de 0 a 1.") from None
        CONFIG.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
    reinicia = {"mic", "saida", "whisper", "voz", "detector", "sensibilidade"} & set(patch)
    if reinicia and _vivo():  # o ouvido recebe estes na linha de comando
        parar()
        iniciar()
    return d


def _py() -> Path:
    return PASTA / "venv" / ("Scripts/python.exe" if native.WINDOWS else "bin/python")


def instalado() -> bool:
    return _py().is_file() and (PASTA / "pronto").is_file()


def estado() -> dict:
    job, treino = _job.get("j"), _job.get("treino")
    try:
        metricas = json.loads(METRICAS.read_text("utf-8"))
    except (OSError, ValueError):
        metricas = None
    return {**ler(), **_estado, "ligado": _vivo(), "instalado": instalado(),
            "instalando": job["id"] if job and job["status"] == "running" else "",
            "detector_treinado": DETECTOR.is_file(), "metricas": metricas,
            "treinando": treino["id"] if treino and treino["status"] == "running" else "",
            "minha_voz": len(list(MINHA_VOZ.glob("*.wav"))) if MINHA_VOZ.is_dir() else 0,
            "vozes_kokoro": VOZES_KOKORO, "humores": [{"id": k, "nome": h["nome"]} for k, h in HUMORES.items()]}


def nota_falada(humor: str, pode_brincar: bool = True) -> str:
    """O que vai no prompt dos turnos falados: o jeito de falar e, se escolhida, a personalidade. `pode_brincar`
    falso (piada recente): a personalidade sai e a resposta é proibida de brincar."""
    h = HUMORES.get(humor, HUMORES["nenhum"])["prompt"]
    if not h:
        return NOTA_FALADA
    return NOTA_FALADA + (f"\n\n{h}\n{REGRA_HUMOR}" if pode_brincar else f"\n\n{SEM_PIADA}")


def sem_marca(texto: str) -> str:
    """O que é falado: a marca [piada] não se lê."""
    return re.sub(r"\s*\[piada\]\s*", " ", texto).strip()


def resumo() -> dict:
    """O pedaço do /api/activity: barato, sem tocar no disco."""
    return {"ligado": _vivo(), "estado": _estado["estado"], "ultimo": _estado["ultimo"], "instalado": instalado()}


def emprestavel() -> dict | None:
    """Um modelo de texto JÁ carregado, parado (nada do Forja usando) e que a pessoa deixou o assistente usar
    (`permitidos`): responde com ele em vez de carregar ou trocar o do assistente. llama.cpp do Forja ou LM Studio /
    Ollama desta máquina. None quando não há nenhum assim."""
    from . import llm, modelctl
    perm = ler()["permitidos"]
    if not perm or modelctl.em_uso() or localai._loading or localai.image_busy():
        return None
    st = localai.status()
    alias = st.get("alias") if st.get("running") else ""
    if alias:
        for p in perm:
            if p["provider"] == "local" and Path(p["model"]).stem in (alias, Path(alias).stem):
                return {"provider": "local", "model": alias}
    for p in perm:
        if p["provider"] != "local" and llm.servidor_local_externo(p["provider"]) \
                and llm.carregado_externo(p["provider"]) == p["model"]:
            return dict(p)
    return None


def modelo_assistente() -> dict:
    """O modelo com que o assistente responde: o escolhido na página ou o do último envio no Forja."""
    m = ler()["modelo"]
    if m:
        return dict(m)
    d = mobile.defaults()
    if not d.get("provider") or not d.get("model"):
        raise ToolError("Escolha o modelo do assistente na página F.O.R.J.A, ou mande uma mensagem qualquer no Forja: "
                        "eu uso o mesmo.")
    return {"provider": d["provider"], "model": d["model"]}


# ------------------------------------------------------------------ instalação (botão na tela Voz)

def _instalar(job: dict) -> None:
    try:
        PASTA.mkdir(parents=True, exist_ok=True)
        (PASTA / "pronto").unlink(missing_ok=True)
        uv = tts._uv(job)
        tts._passo(job, [uv, "venv", str(PASTA / "venv"), "--python", "3.12", "--clear"], "criando o Python")
        tts._passo(job, [uv, "pip", "install", "--python", str(_py()), *PACOTES], "instalando Whisper e Kokoro")
        MODELOS.mkdir(parents=True, exist_ok=True)
        for nome in KOKORO_ARQUIVOS:
            if not (MODELOS / nome).is_file():
                downloads.update(job["id"], detail=f"baixando a voz ({nome})")
                downloads._fetch(KOKORO_URL + nome, MODELOS / nome, job, 0, 0)
        (PASTA / "pronto").write_text(time.strftime("%Y-%m-%d %H:%M"), "utf-8")
        downloads.finish(job["id"], result=str(PASTA))
    except Exception as e:
        downloads.finish(job["id"], error=str(e) if isinstance(e, ToolError) else f"{e.__class__.__name__}: {e}")


def instalar() -> dict:
    with _lock:
        job = _job.get("j")
        if job and job["status"] == "running":
            return job
        parar()  # o venv vai ser refeito: o processo de pé segura os arquivos
        job = _job["j"] = downloads.create("runtime", "Assistente de voz (Whisper + Kokoro)")
    threading.Thread(target=_instalar, args=(job,), daemon=True).start()
    return job


def _treinar(job: dict) -> None:
    """Instala o que falta, grava o som ambiente, gera os exemplos sintéticos e treina (assistente_treino.py)."""
    py = str(_py())
    try:
        uv = tts._uv(job)
        tts._passo(job, [uv, "pip", "install", "--python", py, *PACOTES_DETECTOR], "instalando o openWakeWord")
        OWW.mkdir(parents=True, exist_ok=True)
        for nome in OWW_ARQUIVOS:
            if not (OWW / nome).is_file():
                downloads._fetch(OWW_URL + nome, OWW / nome, job, 0, 0)
        ambiente = PASTA / "ambiente.wav"
        downloads.update(job["id"], detail="gravando 20 s do som da casa (o detector aprende a ignorar)")
        _manda("surdo", s=22)
        subprocess.run([py, "-c", GRAVA, str(ambiente), ler()["mic"], "20"], capture_output=True, timeout=60,
                       **native.popen_kwargs())
        proc = subprocess.Popen([py, "-X", "utf8", "-s", str(Path(__file__).with_name("assistente_treino.py")),
                                 "--kokoro", str(MODELOS), "--oww", str(OWW), "--saida", str(DETECTOR),
                                 "--ambiente", str(ambiente), "--minha-voz", str(MINHA_VOZ)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", cwd=str(PASTA), **native.popen_kwargs())
        fim, ultimas = "", []
        for linha in proc.stdout:  # type: ignore[union-attr]
            linha = linha.strip()
            if linha.startswith("FASE "):
                downloads.update(job["id"], detail=linha[5:])
            elif linha.startswith("PROGRESSO "):
                downloads.update(job["id"], done=int(float(linha.split()[1]) * 100), total=100)
            elif linha.startswith(("OK ", "ERRO ")):
                fim = linha
            elif linha:
                ultimas = (ultimas + [linha])[-5:]
            if downloads.cancelled(job["id"]):
                native.kill_tree(proc)
                raise ToolError("Treino cancelado.")
        proc.wait()
        if not fim.startswith("OK "):
            raise ToolError(fim[5:] or " | ".join(ultimas)[-400:] or "O treino saiu sem resultado.")
        m = json.loads(fim[3:])
        m["quando"] = time.strftime("%Y-%m-%d %H:%M")
        METRICAS.write_text(json.dumps(m, ensure_ascii=False), "utf-8")
        if m["acerto"] >= 0.85 and m["falsos"] <= 0.03:  # bom o bastante: já passa a usar (dá para voltar na página)
            gravar({"detector": "treinado"})
        downloads.finish(job["id"], result=json.dumps(m))
    except Exception as e:
        downloads.finish(job["id"], error=str(e) if isinstance(e, ToolError) else f"{e.__class__.__name__}: {e}")


def treinar() -> dict:
    if not instalado():
        raise ToolError("Instale o assistente primeiro.")
    with _lock:
        job = _job.get("treino")
        if job and job["status"] == "running":
            return job
        job = _job["treino"] = downloads.create("runtime", "Detector de “Forja”")
    threading.Thread(target=_treinar, args=(job,), daemon=True).start()
    return job


def _qualidade(wav: Path) -> dict:
    """Quanto da gravação é fala e o pico: avisa na hora a gravação que não vai ajudar o treino."""
    import array
    import wave
    with wave.open(str(wav)) as w:
        amostras = array.array("h", w.readframes(w.getnframes()))
    if not amostras:
        return {"segundos_voz": 0.0, "pico": 0.0, "aviso": "gravação vazia"}
    pico = max(abs(x) for x in amostras) / 32768
    janelas = [amostras[i:i + 480] for i in range(0, len(amostras) - 480, 480)]
    energia = [sum(x * x for x in j) / len(j) for j in janelas]
    piso = sorted(energia)[len(energia) // 10] if energia else 0
    voz = sum(1 for e in energia if e > max(piso * 8, 120 ** 2)) * 0.03
    aviso = ("não ouvi fala: fale mais perto do microfone" if voz < 0.2 else
             "muito baixo: fale mais alto ou mais perto" if pico < 0.05 else
             "estourou (alto demais): fale um pouco mais longe" if pico > 0.98 else
             "longo demais: diga só “Forja”" if voz > 1.6 else "")
    return {"segundos_voz": round(voz, 2), "pico": round(pico, 2), "aviso": aviso}


def gravacoes() -> list[dict]:
    """As gravações da pessoa para o treino, com a qualidade e se o último treino reconheceu cada uma."""
    try:
        reconhece = json.loads(METRICAS.read_text("utf-8")).get("minha_voz_detalhe") or {}
    except (OSError, ValueError):
        reconhece = {}
    out = []
    for p in sorted(MINHA_VOZ.glob("*.wav")) if MINHA_VOZ.is_dir() else []:
        out.append({"id": p.stem, **_qualidade(p), "reconhecida": reconhece.get(p.stem)})
    return out


def apagar_gravacao(gid: str) -> list[dict]:
    if not re.fullmatch(r"\d{3}", gid):
        raise ToolError("Gravação inválida.")
    (MINHA_VOZ / f"{gid}.wav").unlink(missing_ok=True)
    return gravacoes()


def arquivo_gravacao(gid: str) -> Path:
    p = MINHA_VOZ / f"{gid}.wav"
    if not re.fullmatch(r"\d{3}", gid) or not p.is_file():
        raise ToolError("Gravação não encontrada.")
    return p


def gravar_voz() -> dict:
    """Uma amostra de 2 s da pessoa dizendo "Forja" (vira exemplo positivo no próximo treino). O ouvido fica surdo
    enquanto isso: senão ele responderia ao "Forja" da gravação."""
    if not instalado():
        raise ToolError("Instale o assistente primeiro.")
    MINHA_VOZ.mkdir(parents=True, exist_ok=True)
    existentes = sorted(int(p.stem) for p in MINHA_VOZ.glob("*.wav") if p.stem.isdigit())
    if len(existentes) >= 30:
        raise ToolError("Já tem 30 gravações: é o bastante. Treine de novo.")
    _manda("surdo", s=3)
    n = len(existentes)
    destino = MINHA_VOZ / f"{(existentes[-1] if existentes else 0) + 1:03d}.wav"  # apagar uma no meio não sobrescreve outra
    r = subprocess.run([str(_py()), "-c", GRAVA, str(destino), ler()["mic"], "2"], capture_output=True, text=True,
                       timeout=30, **native.popen_kwargs())
    if r.returncode or not destino.is_file():
        raise ToolError("Não consegui gravar: " + (r.stderr.strip().splitlines() or ["microfone indisponível"])[-1])
    return {"minha_voz": n + 1, "gravacao": {"id": destino.stem, **_qualidade(destino)}}


def apagar_voz() -> dict:
    import shutil
    shutil.rmtree(MINHA_VOZ, ignore_errors=True)
    return {"minha_voz": 0}


def microfones() -> list[dict]:
    if not instalado():
        return []
    r = subprocess.run([str(_py()), "-X", "utf8", "-s", str(Path(__file__).with_name("assistente_ouvido.py")), "--listar"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                       **native.popen_kwargs())
    return [json.loads(x) for x in r.stdout.splitlines() if x.startswith("{")]


# ------------------------------------------------------------------ o processo do ouvido

def _vivo() -> bool:
    p = _proc.get("p")
    return bool(p and p.poll() is None)


def _manda(cmd: str, **dados) -> None:
    if cmd == "escuta" and _pendente:
        _pendente["t"] = time.time()  # a pergunta pendente vale a partir de quando ele terminou de perguntar
    p = _proc.get("p")
    if p and p.poll() is None:
        try:
            p.stdin.write(json.dumps({"cmd": cmd, **dados}, ensure_ascii=False) + "\n")
            p.stdin.flush()
        except OSError:
            pass


def ligar(ligado: bool) -> dict:
    """Liga e desliga pelo botão (trilho ou bandeja). A escolha fica gravada: o Forja reabre ouvindo."""
    if ligado and not instalado():
        raise ToolError("Instale o assistente de voz na tela Voz primeiro.")
    gravar({"ligado": ligado})
    if ligado:
        iniciar()
    else:
        parar()
    return estado()


def iniciar() -> None:
    if _vivo():
        return
    cfg = ler()
    argv = [str(_py()), "-X", "utf8", "-s", str(Path(__file__).with_name("assistente_ouvido.py")),
            "--modelos", str(MODELOS), "--whisper", cfg["whisper"], "--voz", cfg["voz"]]
    if cfg["mic"]:
        argv += ["--mic", cfg["mic"]]
    if cfg["saida"]:
        argv += ["--saida", cfg["saida"]]
    if cfg["detector"] != "whisper" and DETECTOR.is_file():
        argv += ["--detector", str(DETECTOR), "--oww", str(OWW), "--sensibilidade", str(cfg["sensibilidade"])]
        if cfg["detector"] == "ambos":
            argv.append("--com-whisper")
    # o Whisper fica no runtime, não no perfil; sem symlink no Windows o hub reclama a cada subida (funciona igual)
    env = {**os.environ, "HF_HUB_CACHE": str(PASTA / "hf"), "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env=env, cwd=str(PASTA), **native.popen_kwargs())
    _proc["p"] = p
    _estado.update(estado="carregando", erro="")
    threading.Thread(target=_le, args=(p,), daemon=True).start()


def parar() -> None:
    p = _proc.pop("p", None)
    _ao_subir.clear()
    if p and p.poll() is None:
        native.kill_tree(p)
    _estado.update(estado="desligado")


def _interrompe() -> None:
    """Cala a fala e cancela o comando em andamento (o turno que ele abriu no Forja continua: aquele é do Forja)."""
    _manda("cala")
    f = _tarefa.pop("f", None)
    if f and not f.done():
        f.cancel()


def chamar() -> dict:
    """Atalho global: ouve o próximo pedido sem precisar de "Forja". Falando, interrompe; desligado, liga e ouve."""
    if not instalado():
        raise ToolError("Instale o assistente na página F.O.R.J.A primeiro.")
    if not _vivo():
        gravar({"ligado": True})
        _ao_subir["escuta"] = True  # o Whisper leva uns segundos para carregar: escuta quando ficar pronto
        iniciar()
    else:
        _interrompe()
        _manda("escuta", agora=True)
    if _loop:  # enquanto a pessoa fala o pedido, o modelo já vai subindo
        asyncio.run_coroutine_threadsafe(preaquece(), _loop[0])
    return resumo()


async def preaquece() -> str:
    """Atalho ou botão do microfone: carrega o modelo do assistente antes de o pedido chegar. Sem nada na placa,
    carrega; com outro modelo parado (nada do Forja usando), troca; com outro em uso, não mexe (a pergunta vai
    responder com o que estiver no ar). Devolve o que fez, para o log e os testes."""
    from . import llm, modelctl
    try:
        spec = modelo_assistente()
    except ToolError:
        return "sem modelo"
    if await asyncio.to_thread(emprestavel):
        return "permitido no ar"  # já tem um modelo permitido carregado e parado: a pergunta usa ele, nada a carregar
    if not modelctl.gerenciavel(spec):
        return "fora do Forja"  # nuvem ou LM Studio/Ollama: carregam sozinhos no primeiro pedido
    if _trava_carga().locked():
        return "já carregando"
    async with _trava_carga():
        st = localai.status()
        no_ar = st.get("alias") if st.get("running") else ""
        if no_ar == spec["model"]:
            return "já no ar"
        if localai.image_busy() or localai._loading or (no_ar and modelctl.em_uso()):
            return "placa ocupada"
        _conversa_chat.clear()  # outro modelo (ou nenhum) na placa: a memória da conversa falada não existe mais
        try:
            async for _ in modelctl.ensure(spec, {}, asyncio.Event()):
                pass
        except ToolError as e:
            erro = e
        else:
            erro = None
    if erro:
        print(f"Forja: voz: não consegui pré-carregar o modelo: {erro}", flush=True)
        fala(f"Não consegui carregar o {falavel(spec['model'])}: {str(erro)[:160]}")  # a pessoa apertou o atalho: avisa
        return "erro"
    _uso.update(spec=spec, t=time.monotonic(), llm_t=llm.ULTIMO_USO["t"])  # a ociosidade conta daqui
    print(f"Forja: voz: {spec['model']} pré-carregado pelo atalho", flush=True)
    return "carregado"


def _reinicia_se_caiu() -> None:
    """O ouvido caiu sozinho (microfone desconectado, driver): tenta de novo, até 3 vezes em 5 minutos."""
    agora = time.time()
    _quedas[:] = [t for t in _quedas if agora - t < 300] + [agora]
    if len(_quedas) > 3 or not ler()["ligado"]:
        return
    espera = 5 * len(_quedas)
    print(f"Forja: voz: o ouvido caiu; tentando de novo em {espera} s", flush=True)
    threading.Timer(espera, lambda: ler()["ligado"] and not _vivo() and iniciar()).start()


def _le(p: subprocess.Popen) -> None:
    ultimas: list[str] = []
    for linha in p.stdout:  # type: ignore[union-attr]
        linha = linha.rstrip()
        tipo, _, resto = linha.partition(" ")
        if tipo == "PRONTO":
            _estado.update(mic_nome=resto, erro="")
            if _ao_subir.pop("escuta", False):
                _manda("escuta", agora=True)
        elif tipo == "ESTADO":
            if not (_atendendo[0] and resto == "ouvindo"):  # pensando na resposta: o "ouvindo" do ouvido não apaga isso
                _estado["estado"] = resto
        elif tipo == "ERRO":
            _estado["erro"] = resto
        elif tipo == "OUVIU":
            ouviu = json.loads(resto)
            _estado["ouviu"] = ouviu["texto"]
            # o log não guarda o que a pessoa disse (a conversa falada não persiste no PC)
            print(f"Forja: voz ouviu um pedido ({len(ouviu['texto'])} caracteres, wake={ouviu['wake']}, "
                  f"resposta={ouviu['resposta']})", flush=True)
            if _loop:
                if not ouviu.get("resposta"):
                    _interrompe()  # comando novo vence o anterior (que pode estar falando ou pensando ainda)
                _tarefa["f"] = asyncio.run_coroutine_threadsafe(_atende(ouviu), _loop[0])
        elif linha and tipo not in ("FALOU",):
            ultimas = (ultimas + [linha])[-6:]
            print(f"Forja: voz: {linha}", flush=True)
    if _proc.get("p") is p:  # caiu sozinho (não foi o botão)
        _proc.pop("p", None)
        _estado.update(estado="desligado", erro=_estado["erro"] or " | ".join(ultimas)[-400:] or "O ouvido saiu.")
        _reinicia_se_caiu()


def ao_subir(loop: asyncio.AbstractEventLoop) -> None:
    """Lifespan do backend: guarda o loop (os comandos viram turnos nele), reabre ouvindo se estava ligado e começa a
    vigiar a ociosidade do modelo do assistente."""
    _loop[:] = [loop]
    _tarefa["vigia"] = loop.create_task(_vigia_ocioso())
    if ler()["ligado"] and instalado():
        iniciar()


async def _vigia_ocioso() -> None:
    while True:
        await asyncio.sleep(20)
        try:
            await descarrega_se_ocioso()
        except Exception as e:  # a vigia nunca morre por um erro de uma volta
            print(f"Forja: voz: descarga por ociosidade falhou: {e}", flush=True)


async def descarrega_se_ocioso(agora: float | None = None) -> bool:
    """O modelo que o assistente pôs na VRAM sai depois de `ocioso_min` minutos sem pergunta falada. Só se ele ainda
    for do assistente: com algo do Forja rodando, ou alguém tendo usado o modelo depois da última pergunta, quem decide
    é a ociosidade do próprio Forja (Configurações), não esta."""
    from . import llm, modelctl
    minutos = ler()["ocioso_min"]
    u = dict(_uso)
    agora = time.monotonic() if agora is None else agora
    if not minutos or not u or _atendendo[0] or agora - u["t"] < minutos * 60:
        return False
    _uso.clear()
    esquecer()  # parado o bastante: a conversa falada some junto com o modelo (mesmo de nuvem, que não descarrega)
    spec = u["spec"]
    alheio = llm.ULTIMO_USO["t"] > u["llm_t"] + 1 or modelctl.em_uso()
    if modelctl.gerenciavel(spec):
        if alheio or localai.status().get("alias") != spec["model"]:
            return False
        async for _ in modelctl.unload("assistente ocioso"):
            pass
    elif llm.servidor_local_externo(spec["provider"]):
        if alheio or not await asyncio.to_thread(llm.descarrega_externo, spec["provider"], spec["model"]):
            return False
    else:
        return False  # nuvem: não ocupa a placa
    print(f"Forja: voz: {spec['model']} descarregado ({minutos} min sem pergunta falada)", flush=True)
    return True


# ------------------------------------------------------------------ o que fazer com cada comando

def _sem_acento(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


IMAGEM = re.compile(r"\b(?:crie|cria|criar|gere|gera|gerar|faca|faz|fazer|desenhe|desenha|desenhar|mostre|quero|preciso)\b"
                    r".*?\b(?:uma\s+|umas\s+)?(?:imagem|imagens|foto|figura|ilustracao|desenho)\b\s*(?:de|do|da|dos|das|com)?\s*(.*)")
VIDEO = re.compile(r"\b(?:crie|cria|criar|gere|gera|gerar|faca|faz|fazer|anime|quero|preciso)\b.*?\b(?:um\s+)?"
                   r"(?:video|animacao)\b\s*(?:de|do|da|dos|das|com)?\s*(.*)")
# "no modo agente", "pelo agente", "pede pro agente", "agente, crie..." e o "modo a gente" que o Whisper escreve
AGENTE = re.compile(r"\b(?:n[oa]\s+|em\s+|pelo\s+|usando\s+o\s+)?modo\s+(?:de\s+)?(?:agente|a\s+gente|agent)\b[\s,:]*"
                    r"|\b(?:no|pelo|com\s+o|pro|pra\s+o|para\s+o|ao|usando\s+o)\s+agente\b(?:\s+do\s+forja)?[\s,:]*"
                    r"|\b(?:pede|peca|manda|mande|fala)\s+(?:pro|pra\s+o|para\s+o|ao|o)\s+agente\b[\s,:]*|^\s*agente\b[\s,:]*")
MAESTRO = re.compile(r"\b(?:n[oa]\s+|em\s+|pel[oa]\s+|com\s+[oa]\s+|usando\s+[oa]\s+|pr[oa]\s+)?(?:modo\s+(?:de\s+)?)?"
                     r"maestro\b(?:\s+do\s+forja)?[\s,:]*")
# pesquisa profunda vai para a tela Pesquisa; "pesquise X" simples ele mesmo responde (com a busca na web)
PESQUISA = re.compile(r"(?:\b(?:fa[cz]\w*|quero|preciso|cri\w*|comec\w*|inici\w*|abr\w*|rod\w*|gera?\w*)\s+)?(?:uma?\s+)?"
                      r"(?:(?:pesquis\w*|investig\w*|levantamento)\s+(?:bem\s+)?"
                      r"(?:profunda|funda|aprofundada|completa|completo|detalhada|detalhado|a\s+fundo)"
                      r"|relatorio\s+(?:completo|detalhado)|(?:modo|tela)\s+(?:de\s+)?pesquisa)\b"
                      r"(?:\s+n[oa]\s+(?:aplicativo\s+)?(?:forja|aplicativo|app)(?:\s+desktop)?)?(?:\s+(?:pra|para)\s+mim)?[\s,:]*")
# "salva nossa conversa num chat", "transforma essa conversa em um chat do Forja": a falada vai para o disco
SALVAR = re.compile(r"\b(?:salv\w*|transform\w*|guard\w*|vir\w*|pass\w*|coloc\w*|bot\w*|registr\w*)\b.{0,30}?"
                    r"\b(?:nossa|essa|esta|a)\s+(?:conversa|papo|conversinha)\b")
# "abra um chat no Forja sobre X", "abre o chat", "inicia uma conversa nova": aí sim abre conversa
CHAT_FORJA = re.compile(r"\b(?:abr\w*|inici\w*|comec\w*|cri\w*|bot\w*|coloc\w*)\b\s+(?:(?:um|uma|o|a|outro|outra|novo|nova)\s+){0,2}"
                        r"(?:chat|conversa)\b(?:\s+(?:nov[oa]|do\s+forja|n[oa]\s+(?:forja(?:\s+desktop)?|aplicativo|app)))*"
                        r"[\s,:]*(?:(?:sobre|para|pra|de|com|acerca\s+de)\s+)?(.*)")
# "troca seu modelo para o Qwen 3.6", "use o modelo Ornith para responder": o modelo DO ASSISTENTE (não de um chat)
TROCA_MODELO = re.compile(r"\b(?:troc\w*|mud\w*|alter\w*|coloc\w*|bot\w*|volt\w*|use|usa|usar|quero\s+que\s+voce\s+use|"
                          r"passa\w*\s+a\s+usar|responde\w*\s+com)\b(?:\s+(?:o|seu|teu|de|do|pro|pra|para|ao))*\s+(?:proprio\s+)?modelo\b"
                          r"(?:\s+(?:do|de)\s+(?:assistente|voce|forja))?(?:\s+(?:para|pra|pro|por|pelo))?\s*(?:o\s+|a\s+)?"
                          r"(.*?)(?:\s+(?:para|pra)\s+(?:responder|falar|conversar)(?:\s+comigo)?)?[\s.!?]*$")
QUAL_MODELO = re.compile(r"\b(?:qual|que)\s+(?:e\s+)?(?:o\s+)?(?:seu\s+|teu\s+)?modelo\s+(?:voce|vc|ta|esta|e\s+o\s+seu)\b"
                         r"|\bqual\s+(?:e\s+)?(?:o\s+)?seu\s+modelo\b|\bcom\s+qual\s+modelo\s+voce\b")
# o que indica que a pessoa quer algo DO Forja: sem regra casada, o modelo decide (classifica_rota)
CITA_FORJA = re.compile(r"\b(?:forja|aplicativo|app|chat|conversa|agente|a\s+gente|maestro|pesquisa\s+\w+|imagem|foto|"
                        r"desenho|video)\b")
# "usando o modelo Qwen 3.6": o modelo já vem no pedido e a pergunta "com qual modelo?" não precisa ser feita
MODELO_DITO = re.compile(r"[\s,]*\b(?:usando|use|usa|com|pelo|no|via)\s+(?:o\s+)?modelo\s+(.+?)\s*(?=[,;!?]|\.(?:\s|$)|$)")
CORTESIA = re.compile(r"^\s*(?:(?:por\s+favor|pra\s+mim|para\s+mim|ai|agora)[\s,]+)+|[\s,]+(?:por\s+favor|pra\s+mim|para\s+mim)\s*$")


def _corta(comando: str, c: str, m: re.Match) -> str:
    """O pedido sem o trecho que disse o tipo ("no modo agente"), sem cortesia solta nas pontas."""
    resto = (comando[:m.start()] + " " + comando[m.end():]).strip(" ,.;:")
    resto = re.sub(r"\s{2,}", " ", resto)
    return CORTESIA.sub("", resto).strip(" ,.;:") or resto


def rota(comando: str) -> tuple[str, str]:
    """(tipo, pedido): parar, salvar_conversa, pesquisa, maestro, agente, chat_forja, video, imagem ou chat.
    Por palavra-chave, testada com várias formas de pedir (tests/test_assistente.py). O que fica de fora mas cita
    algo do Forja vai para classifica_rota (o modelo decide)."""
    c = _sem_acento(comando)
    # a posição do casamento no texto sem acento vale no original: NFKD + tirar acento não muda o tamanho
    if PARAR.match(c):
        return "parar", ""
    if SALVAR.search(c):
        return "salvar_conversa", ""
    if m := PESQUISA.search(c):
        return "pesquisa", re.sub(r"^(?:sobre|de|do|da|dos|das|acerca\s+de)\s+", "", _corta(comando, c, m), flags=re.I)
    if m := MAESTRO.search(c):
        return "maestro", _corta(comando, c, m)
    if m := AGENTE.search(c):
        return "agente", _corta(comando, c, m)
    if m := CHAT_FORJA.search(c):
        return "chat_forja", comando[m.start(1):].strip(" ,.?")
    if m := VIDEO.search(c):
        return "video", comando[m.start(1):].strip(" ,.") or comando
    if m := IMAGEM.search(c):
        return "imagem", comando[m.start(1):].strip(" ,.") or comando
    if QUAL_MODELO.search(c):
        return "qual_modelo", ""
    if m := TROCA_MODELO.search(c):
        return "trocar_modelo", comando[m.start(1):m.end(1)].strip(" ,.?")
    return "chat", comando


def _parece_pedido(texto: str) -> bool:
    """Resposta a "Com qual modelo?" que na verdade é um pedido novo: casa com outra rota, ou é uma frase/pergunta
    longa demais para ser só um nome de modelo."""
    return rota(texto)[0] != "chat" or len(texto.split()) >= 6 or texto.strip().endswith("?")


def modelo_dito(comando: str) -> tuple[str, str]:
    """("pedido sem a menção", "nome do modelo dito") ou (comando, "") se não falou de modelo."""
    m = MODELO_DITO.search(_sem_acento(comando))
    if not m:
        return comando, ""
    return (comando[:m.start()] + comando[m.end():]).strip(" ,."), comando[m.start(1):m.end(1)]


ROTAS_LLM = ("responder", "chat", "agente", "maestro", "pesquisa", "imagem", "video")
CLASSIFICA = """Classifique o pedido falado ao assistente de voz do app Forja. Responda SÓ uma palavra:
responder, chat, agente, maestro, pesquisa, imagem ou video.
- responder: pergunta, conversa, explicação, ou pesquisa simples que o assistente resolve falando.
- chat: abrir uma conversa de chat dentro do app Forja.
- agente: pedir ao agente do Forja para criar ou mexer em arquivos, programar um site, jogo, app, script ou projeto.
- maestro: usar a Maestro do Forja (planeja e delega para workers).
- pesquisa: pesquisa profunda, relatório completo com fontes.
- imagem / video: gerar uma imagem ou um vídeo.
Só escolha uma ação do app quando o pedido for para FAZER algo no Forja. Na dúvida: responder.
Exemplos:
"me explica o que é um agente de IA" -> responder
"cria um jogo da cobrinha com o agente do forja" -> agente
"quero que o forja programe um site de receitas" -> agente
"abre uma conversa lá no aplicativo sobre investimentos" -> chat
"faz um levantamento completo sobre carros elétricos no forja" -> pesquisa
"desenha um dragão de cartola" -> imagem
"o que é um vídeo vertical?" -> responder"""


async def classifica_rota(comando: str) -> str:
    """O pedido cita o Forja e nenhuma regra pegou (o Whisper ouviu torto, ou um jeito novo de pedir): o modelo do
    assistente decide, numa chamada curta. Erro ou resposta estranha: responder (o caminho que não abre nada)."""
    from . import llm
    try:
        spec = modelo_assistente()
        texto = ""
        async for tipo, val in llm.chat_stream(spec["provider"], spec["model"],
                                               [{"role": "system", "content": CLASSIFICA},
                                                {"role": "user", "content": comando}],
                                               None, 2048, think=False, max_tokens=8):
            if tipo == "content":
                texto += val
        palavra = _sem_acento(texto).strip(" .\"'\n").split()[0] if texto.strip() else ""
        return palavra if palavra in ROTAS_LLM else "responder"
    except Exception as e:
        print(f"Forja: voz: classificação do pedido falhou: {e.__class__.__name__}", flush=True)
        return "responder"


VERBOS = {"crie", "cria", "criar", "faca", "faz", "fazer", "monte", "monta", "montar", "desenvolva", "construa",
          "programe", "continue", "continua", "trabalhe", "implemente", "quero", "preciso", "gostaria"}
VAZIAS = {"um", "uma", "o", "a", "os", "as", "de", "do", "da", "dos", "das", "para", "pra", "com", "e", "no", "na",
          "meu", "minha", "novo", "nova", "simples", "projeto", "pasta", "mim", "me", "chat", "conversa", "forja",
          "que", "voce", "ai", "agora", "favor", "por", "em", "nesse", "nessa", "esse", "essa", "isso", "sobre",
          "abra", "abre", "abrir", "inicie", "inicia", "iniciar", "comece", "comeca"}


def nome_projeto(pedido: str) -> str:
    """"crie um site de ecommerce" -> "site-ecommerce": até 4 palavras que dizem o que é."""
    palavras = [p for p in re.findall(r"[a-z0-9]+", _sem_acento(pedido)) if p not in VERBOS | VAZIAS]
    return "-".join(palavras[:4]) or "projeto"

def pasta_parecida(nome: str, pastas: list[str]) -> str | None:
    """A pasta cujo nome lembra o projeto pedido: palavra significativa em comum ou nomes quase iguais."""
    alvo = set(w for w in nome.split("-") if len(w) >= 4)
    melhor, nota = None, 0.0
    for p in pastas:
        base = _sem_acento(Path(p).name)
        palavras = set(re.findall(r"[a-z0-9]+", base))
        comum = len(alvo & palavras) + sum(1 for a in alvo for w in palavras if a != w and (a in w or w in a) and len(w) >= 4)
        n = comum + difflib.SequenceMatcher(None, nome, re.sub(r"[^a-z0-9]+", "-", base)).ratio()
        if (comum or n >= 0.8) and n > nota:
            melhor, nota = p, n
    return melhor


def _projetos() -> dict[str, int]:
    """Pasta -> conversa mais recente do Agente/Maestro que trabalha nela (fora as arquivadas)."""
    out: dict[str, int] = {}
    with db.session() as s:
        for cid, ws in s.execute(select(db.Conversation.id, db.Conversation.workspace)
                                 .where(db.Conversation.kind.in_(("agent", "maestro")), db.Conversation.workspace.isnot(None),
                                        db.Conversation.archived.isnot(True))
                                 .order_by(db.Conversation.updated_at.desc())):
            out.setdefault(ws, cid)
    return out


def _pasta_nova(nome: str, kind: str = "agent") -> Path:
    """Projeto novo pedido por voz: <raiz>/chat-agente/<nome> ou <raiz>/chat-maestro/<nome>, nunca solto na raiz.
    Raiz: a "pasta dos projetos" da página, ou ~/Forja."""
    cfg = ler()
    raiz = Path(cfg["pasta_projetos"] or Path.home() / "Forja") / ("chat-maestro" if kind == "maestro" else "chat-agente")
    destino, n = raiz / nome, 2
    while destino.exists():
        destino, n = raiz / f"{nome}-{n}", n + 1
    destino.mkdir(parents=True)
    return destino


def _nova_conversa(kind: str, workspace: str | None = None, titulo: str = "") -> int:
    with db.session() as s:
        c = db.Conversation(kind=kind, workspace=workspace, title=titulo[:60] or "Nova conversa")
        s.add(c)
        s.commit()
        return c.id


def _abre(conv: int, kind: str) -> None:
    n = (_estado["ultimo"] or {}).get("n", 0) + 1
    _estado["ultimo"] = {"conv": conv, "kind": kind, "n": n}


def fala(texto: str) -> None:
    _anota("forja", texto)
    _manda("fala", texto=texto)


def _anota(quem: str, texto: str) -> None:
    texto = (texto or "").strip()
    if not texto:
        return
    ultima = _transcricao[-1] if _transcricao else None
    # só as frases de uma mesma resposta viram um balão: aviso que chega sozinho depois (pesquisa terminou) é outro
    if ultima and ultima["quem"] == quem == "forja" and _atendendo[0] and time.time() - ultima["t"] < 30:
        ultima["texto"] += " " + texto  # as frases de uma mesma resposta viram um balão só
        ultima["t"] = time.time()
    else:
        _transcricao.append({"quem": quem, "texto": texto, "t": time.time()})
    del _transcricao[:-TRANSCRICAO_MAX]


def esquecer() -> dict:
    """Apaga a conversa falada da memória (o que o modelo lembra e o que a tela mostra)."""
    _conversa_chat.clear()
    _transcricao.clear()
    return {"ok": True}


def conversa() -> dict:
    return {"transcricao": list(_transcricao), "ouviu": _estado.get("ouviu", "")}


def salvar_conversa() -> int:
    """A conversa falada vira um chat do Forja (pedido explícito: é o único jeito de ela ir para o disco)."""
    falas = [x for x in _transcricao if x["texto"]]
    if not any(x["quem"] == "voce" for x in falas):
        raise ToolError("Ainda não conversamos nada para salvar.")
    titulo = next(x["texto"] for x in falas if x["quem"] == "voce")
    with db.session() as s:
        c = db.Conversation(kind="chat", title="🎙 " + titulo[:57])
        s.add(c)
        s.flush()
        for x in falas:
            s.add(db.Message(conversation_id=c.id, role="user" if x["quem"] == "voce" else "assistant", content=x["texto"]))
        s.commit()
        conv = c.id
    _abre(conv, "chat")
    return conv


async def _turno(conv: int, kind: str, texto: str, spec: dict | None = None) -> object:
    """Turno numa conversa do Forja (só quando o comando pede para trabalhar lá), com o modelo escolhido por voz.
    A carga do modelo é a do próprio turno (agent._garante_modelo): ele não troca o modelo no meio de outra execução."""
    from . import agent
    d = mobile.defaults()
    spec = spec or {"provider": d.get("provider"), "model": d.get("model")}
    if not spec.get("provider") or not spec.get("model"):
        raise ToolError("Escolha um modelo no Forja e mande uma mensagem qualquer antes: eu uso o mesmo.")
    if agent.active_run(conv):
        raise ToolError("Essa conversa já está trabalhando. Espere ela terminar.")
    run = agent.Run(conv)
    agent.RUNS[run.id] = run
    run.start(agent.RunRequest(content=texto, provider=spec["provider"], model=spec["model"], mode=kind,
                               permission=d.get("permission") or "manual", effort=d.get("effort") or "medio"))
    return run


# ------------------------------------------------------------------ qual modelo (perguntado por voz)

PADRAO_DITO = re.compile(r"\b(mesmo|sempre|atual|padrao|tanto faz|qualquer|esse ai|o que estiver)\b")
NUMEROS = {"zero": "0", "um": "1", "uma": "1", "dois": "2", "duas": "2", "tres": "3", "quatro": "4", "cinco": "5",
           "seis": "6", "sete": "7", "oito": "8", "nove": "9", "ponto": "", "virgula": ""}
# como o Whisper escreve os nomes em português (das conversas salvas: "Comem 3.6", "WEN 3.6", "KEWEN3.6", "gema")
APELIDOS = {
    "qwen": ["que wen", "que e wen", "kewen", "kiwen", "kwen", "quen", "kuen", "cuen", "cowen", "comem", "queen", "quem",
             "wen", "uen", "quin", "qwen"],
    "gemma": ["gema", "gemas", "jema", "jemma", "guema", "gemar", "gemma"],
    "llama": ["lhama", "lama", "iama", "llama"], "mistral": ["mistrau", "mistral"],
    "deepseek": ["deep seek", "dip seek", "dip sik", "dipsik", "dipseek", "deepseek"],
    "ornith": ["ornitio", "ornite", "ornit", "ornif", "ornith"], "phi": ["fai", "phi"],
    "gpt": ["gepete", "jipiti", "gpt"],
}
_APELIDO = re.compile(r"\b(" + "|".join(sorted({a for v in APELIDOS.values() for a in v}, key=len, reverse=True))
                      .replace(" ", r"\s+") + r")\b")


def falavel(nome: str) -> str:
    """"Qwen3.6-35B-A3B-Q4_K_M" -> "Qwen3.6 35B A3B": sem a quantização, que ninguém quer ouvir."""
    nome = Path(nome).stem if ("\\" in nome or "/" in nome) else nome
    nome = re.sub(r"[-_.](?:UD[-_])?(?:I?Q\d\w*|F16|BF16|F32)$", "", nome, flags=re.I)
    return re.sub(r"[-_]+", " ", nome).strip()


def modelos_disponiveis() -> list[dict]:
    """Os que dá para escolher falando: os GGUF de texto das pastas, o modelo do último turno e os favoritos."""
    out = [{"provider": "local", "model": m["name"], "nome": m["name"]}
           for m in localai.scan() if m.get("kind") == "chat"]
    d = mobile.defaults()
    extras = ([{"provider": d["provider"], "model": d["model"], "nome": d["model"]}] if d.get("model") else []) + \
        [{"provider": f["provider"], "model": f["model"], "nome": f["model"]} for f in ler()["favoritos"]]
    for x in extras:
        if not any(o["provider"] == x["provider"] and o["model"] == x["model"] for o in out):
            out.append(x)
    return out


def _compacto(t: str) -> str:
    palavras = re.findall(r"[a-z0-9]+", _sem_acento(t))
    return "".join(NUMEROS.get(p, p) for p in palavras)


def _desapelida(r: str) -> str:
    """"que e wen 3.6" -> "qwen 3.6": os apelidos viram o nome do modelo."""
    def nome(m: re.Match) -> str:
        dito = re.sub(r"\s+", " ", m.group(1))
        return next(k for k, v in APELIDOS.items() if dito in v)
    return _APELIDO.sub(nome, r)


def escolher_modelo(resposta: str, favoritos: list[dict], modelos: list[dict], padrao: dict) -> dict | None:
    """O modelo que a pessoa disse: "o de sempre", um favorito pelo nome do uso ("código") ou o nome do modelo
    ("qwen 3.6", "gemma 12b", e como o Whisper ouve: "kewen 3.6", "gema"). None quando nada bate."""
    r = _sem_acento(resposta)
    if PADRAO_DITO.search(r) and padrao.get("model"):
        return {"provider": padrao["provider"], "model": padrao["model"]}
    for f in favoritos:
        if re.search(rf"\b{re.escape(_sem_acento(f['nome']))}", r):
            return {"provider": f["provider"], "model": f["model"]}
    alvos = [(m, _compacto(falavel(m["model"]))) for m in modelos]
    # só fica o que pode ser parte de um nome: número, ou palavra que aparece em algum modelo ("que", "é" saem)
    separado = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", r)  # "kewen3.6" -> "kewen 3.6"
    palavras = [NUMEROS.get(p, p) for p in re.findall(r"[a-z0-9]+", _desapelida(separado))]
    palavras = [p for p in palavras if p and (p.isdigit() or any(p in a for _, a in alvos))]
    falado = "".join(palavras)
    if len(falado) < 2:
        return None
    letras = [p for p in palavras if not p.isdigit() and len(p) >= 3]
    candidatos = [(m, a) for m, a in alvos if all(w in a for w in letras)]
    if not letras:  # só o número ("três ponto seis"): vale se um modelo só tem ele
        candidatos = [(m, a) for m, a in candidatos if falado in a]
        return {"provider": candidatos[0][0]["provider"], "model": candidatos[0][0]["model"]} if len(candidatos) == 1 else None
    melhor, nota = None, 0.0
    for m, alvo in candidatos:
        n = difflib.SequenceMatcher(None, falado, alvo[:len(falado) + 4]).ratio() + (0.5 if falado in alvo else 0)
        if n > 0 and m.get("provider") == "local":
            n += 0.15  # empate com o mesmo modelo no LM Studio/Ollama ("gemma 12b"): o GGUF do Forja ganha
        if n > nota:
            melhor, nota = m, n
    return {"provider": melhor["provider"], "model": melhor["model"]} if melhor and nota >= 0.55 else None


def _nomes_para_dizer(modelos: list[dict], n: int = 4) -> str:
    nomes = list(dict.fromkeys(re.sub(r"\s+it$", "", falavel(m["model"]), flags=re.I) for m in modelos))[:n]
    return ", ".join(nomes[:-1]) + f" e {nomes[-1]}" if len(nomes) > 1 else "".join(nomes)


async def _pergunta_modelo(ctx: dict) -> None:
    """Antes de abrir chat, agente, maestro ou pesquisa no Forja: com qual modelo? Se o pedido já disse ("usando o
    modelo Qwen 3.6"), não pergunta. A resposta chega em _modelo_respondido."""
    _pendente.clear()
    _pendente.update(tipo="modelo", tentativas=0, **ctx)
    if ctx.get("modelo_dito"):
        spec = escolher_modelo(ctx["modelo_dito"], ler()["favoritos"], await asyncio.to_thread(modelos_disponiveis),
                               mobile.defaults())
        if spec:
            _pendente.clear()
            await _comeca_com(ctx, spec)
            return
    favs = [f["nome"] for f in ler()["favoritos"]]
    dica = f" Ou um favorito: {', '.join(favs)}." if favs else ""
    fala(f"Com qual modelo? Diga o nome, ou o de sempre.{dica}")
    _manda("escuta")


async def _modelo_respondido(resposta: str) -> None:
    p = dict(_pendente)
    modelos = await asyncio.to_thread(modelos_disponiveis)
    spec = escolher_modelo(resposta, ler()["favoritos"], modelos, mobile.defaults())
    if not spec:
        if p["tentativas"] < 2:
            _pendente["tentativas"] += 1
            fala(f"Não achei esse. Tenho {_nomes_para_dizer(modelos)}. Qual? Ou diga o de sempre.")
            _manda("escuta")
        else:
            _pendente.clear()
            fala("Não achei. Pode pedir de novo quando quiser.")
        return
    _pendente.clear()
    await _comeca_com(p, spec)


TIPO_CONVERSA = {"chat": "chat", "agent": "agent", "maestro": "maestro", "pesquisa": "pesquisa"}


async def _comeca_com(p: dict, spec: dict) -> None:
    """O modelo foi escolhido: cria a conversa certa (e a pasta, no agente/maestro) e começa."""
    nome = falavel(spec["model"])
    kind = TIPO_CONVERSA[p["kind"]]
    if kind == "pesquisa":
        pergunta = await _pergunta_da_pesquisa(p["pedido"])
        conv = _nova_conversa("pesquisa", titulo=pergunta)
        aviso = f"Vou fazer uma pesquisa profunda sobre {pergunta}, com o {nome}. Te aviso quando terminar."
    elif kind == "chat":
        conv = _nova_conversa("chat", titulo=p["pedido"])
        aviso = f"Abri o chat com o {nome}."
    elif p.get("conv"):
        conv = p["conv"]
        aviso = f"Continuando em {Path(p['pasta']).name}, com o {nome}."
    else:
        pasta = _pasta_nova(p["nome"], kind)
        conv = _nova_conversa(kind, str(pasta), titulo=p["pedido"])
        modo = "na Maestro" if kind == "maestro" else "no modo agente"
        aviso = f"Criei a pasta {pasta.name} e vou trabalhar nela {modo}, com o {nome}."
    _abre(conv, kind)
    if not p["pedido"] and kind != "pesquisa":
        fala(aviso)
        return
    await _troca_e_comeca(conv, kind, pergunta if kind == "pesquisa" else p["pedido"], spec, aviso)


REFERENCIA = re.compile(r"\b(isso|disso|nisso|esse assunto|desse assunto|esse tema|o que (?:a gente )?conversamos|"
                        r"o que falamos|a nossa conversa|essa conversa)\b")


async def _pergunta_da_pesquisa(pedido: str) -> str:
    """"sobre isso que conversamos": a pergunta da pesquisa sai da conversa falada (o modelo do assistente reescreve
    numa pergunta que se sustenta sozinha). Pedido com assunto próprio vai como está."""
    from . import llm
    if pedido and not REFERENCIA.search(_sem_acento(pedido)):
        return pedido
    conversa_txt = "\n".join(f"{'Pessoa' if x['quem'] == 'voce' else 'Assistente'}: {x['texto']}" for x in _transcricao[-12:])
    if not conversa_txt:
        return pedido or "o assunto da conversa"
    try:
        spec = modelo_assistente()
        texto = ""
        async for tipo, val in llm.chat_stream(spec["provider"], spec["model"], [
                {"role": "system", "content": "Escreva UMA pergunta de pesquisa completa, que se entenda sem a conversa, "
                                              "sobre o assunto que a pessoa quer pesquisar. Só a pergunta, sem aspas."},
                {"role": "user", "content": f"Conversa:\n{conversa_txt}\n\nPedido: {pedido or 'pesquise isso'}"}],
                None, 4096, think=False, max_tokens=80):
            if tipo == "content":
                texto += val
        return texto.strip().strip('"') or pedido
    except Exception:
        return pedido or conversa_txt.splitlines()[-1][:120]


async def _avisa_pesquisa(message_id: int, pergunta: str) -> None:
    """Fala quando a pesquisa profunda termina (ela roda sozinha na tela Pesquisa, pode levar dezenas de minutos)."""
    from . import pesquisa
    while True:
        await asyncio.sleep(15)
        try:
            st = pesquisa.estado(message_id)
        except ToolError:
            return
        if st.get("status") != "rodando":
            fala(f"A pesquisa sobre {pergunta[:80]} terminou." if st.get("status") == "pronto"
                 else f"A pesquisa sobre {pergunta[:80]} parou: {st.get('aviso') or st.get('status')}.")
            return

def _precisa_esperar(spec: dict) -> str:
    """Outro modelo local no ar com trabalho do Forja usando ele: o turno com `spec` não pode trocar agora (derrubaria
    esse trabalho; o Forja nem troca, e o turno rodaria calado no modelo errado). Devolve o nome do que está no ar."""
    from . import modelctl
    if not modelctl.gerenciavel(spec):
        return ""
    st = localai.status()
    no_ar = st.get("alias") if st.get("running") else ""
    ocupado = localai._loading or localai.image_busy() or (no_ar and no_ar != spec["model"] and modelctl.em_uso())
    return (no_ar or "outro modelo") if ocupado else ""


async def _troca_e_comeca(conv: int, kind: str, pedido: str, spec: dict, aviso: str) -> None:
    """Chat/agente com um modelo diferente do do assistente: o assistente sai da VRAM e o novo modelo entra com o
    pedido (a carga é a do próprio turno, agent._garante_modelo). Ollama local não é do Forja: descarrega pela API dele.
    Com trabalho usando o modelo do ar, espera ele terminar (até ESPERA_LIVRE_MIN) em vez de derrubar."""
    from . import llm, modelctl
    u = _uso.get("spec")
    if u and u != spec and llm.servidor_local_externo(u["provider"]):
        await asyncio.to_thread(llm.descarrega_externo, u["provider"], u["model"])  # libera a VRAM para o novo
        _uso.clear()
    elif (u and u != spec and modelctl.gerenciavel(u) and llm.servidor_local_externo(spec["provider"])
          and localai.status().get("alias") == u["model"] and not modelctl.em_uso()):
        # o novo roda no LM Studio/Ollama desta máquina: o llama.cpp do assistente sai para dar lugar a ele
        async for _ in modelctl.unload("chat com outro modelo"):
            pass
        _uso.clear()
    if u and u != spec:
        _conversa_chat.clear()  # o modelo do assistente sai da placa: o que ele lembrava da conversa falada vai junto
    if ocupado := _precisa_esperar(spec):
        fala(f"{aviso} Tem um trabalho usando o {falavel(ocupado)} agora: começo assim que ele terminar.")
        limite = time.monotonic() + ESPERA_LIVRE_MIN * 60
        while _precisa_esperar(spec):
            if time.monotonic() > limite:
                fala("Cansei de esperar o outro trabalho. O pedido ficou na conversa: mande de novo quando quiser.")
                return
            await asyncio.sleep(5)
        fala(f"Começando agora com o {falavel(spec['model'])}.")
    else:
        troca = modelctl.gerenciavel(spec) and localai.status().get("alias") not in (None, "", spec["model"])
        fala(aviso + (" Trocando de modelo, leva um instante." if troca else ""))
    if u == spec or (u and modelctl.gerenciavel(u)):
        _uso.clear()  # o modelo agora é do turno (ou vai sair dele): a ociosidade do assistente não mexe mais
    if kind == "pesquisa":
        from . import pesquisa
        contexto = "\n".join(f"{'Pessoa' if x['quem'] == 'voce' else 'Assistente'}: {x['texto']}" for x in _transcricao[-12:])
        msg = pesquisa.start(conv, pedido, spec["provider"], spec["model"], "funda", contexto=contexto[:4000])
        _tarefa.setdefault("pesquisas", set()).add(asyncio.create_task(_avisa_pesquisa(msg["id"], pedido)))
        return
    await _turno(conv, kind, pedido, spec)


BUSCA = {"type": "function", "function": {
    "name": "web_search", "description": "Pesquisa na web. Use para informação atual (notícias, preços, cotações, clima, "
    "resultados, lançamentos) ou o que você não sabe com certeza.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}


async def _chat(texto: str) -> None:
    """Pergunta comum: só responde falando. Não cria conversa nem mexe na tela: o modelo é chamado direto (o mesmo do
    último turno), com a busca na web como ferramenta, e o histórico fica em memória enquanto a conversa falada durar.
    Kokoro: fala frase a frase enquanto o modelo escreve. Voz da tela Voz: a resposta inteira no fim (o motor dela
    tira o LLM da VRAM, então só depois que ele terminou)."""
    from . import llm, modelctl, web
    from datetime import datetime
    spec = modelo_assistente()
    escolhido = bool(ler()["modelo"])
    emprestado = await asyncio.to_thread(emprestavel)
    if emprestado:
        spec = emprestado  # já carregado, parado e permitido: responde com ele, sem carregar nem trocar nada
    if modelctl.gerenciavel(spec):
        # Guarda de VRAM: a pergunta falada nunca derruba trabalho do Forja. Com outro modelo local no ar: se algo usa
        # ele (ou o assistente segue "o do último envio"), responde com ele mesmo; parado, troca para o do assistente.
        if localai.image_busy():
            raise ToolError("A placa de vídeo está ocupada gerando uma imagem. Pergunte de novo daqui a pouco.")
        avisou = localai._loading or _trava_carga().locked()
        if avisou:  # o atalho já começou a carregar (preaquece): espera ele
            fala("Só um instante, o modelo está carregando.")
        async with _trava_carga():
            limite = time.monotonic() + 180
            while localai._loading and time.monotonic() < limite:  # carga de fora do assistente
                await asyncio.sleep(0.5)
            st = localai.status()
            no_ar = st.get("alias") if st.get("running") else ""
            if no_ar and no_ar != spec["model"] and (modelctl.em_uso() or not escolhido):
                spec = {"provider": spec["provider"], "model": no_ar}
            elif no_ar != spec["model"]:
                _conversa_chat.clear()  # o modelo saiu da placa (por fora): a memória da conversa falada foi junto
                if not avisou:
                    fala("Carregando o modelo, um instante.")
                async for _ in modelctl.ensure(spec, {}, asyncio.Event()):
                    pass
    hist = _conversa_chat.setdefault("msgs", [])
    hist.append({"role": "user", "content": texto})
    cfg = ler()
    kokoro = cfg["motor"] == "kokoro"
    pode_brincar = _humor["pausa"] == 0
    # O system fica fixo e o que muda a cada pergunta (hora, pausa do humor) vai junto dela: o prefixo da conversa
    # não muda e o llama-server reaproveita o cache (antes reprocessava ~600 tokens toda vez: 10 s num MoE grande).
    agora_txt = f"[Agora: {datetime.now():%d/%m/%Y %H:%M} neste PC.{'' if pode_brincar else ' ' + SEM_PIADA}]"
    msgs = [{"role": "system", "content": nota_falada(cfg["humor"])}, *hist[-12:-1],
            {"role": "user", "content": f"{agora_txt}\n{texto}"}]
    esforco = cfg["raciocinio"]
    slot = modelctl.slot_auxiliar() if modelctl.gerenciavel(spec) else None  # slot fixo: o cache fica nele
    buf, tudo, pesquisou = "", "", False
    for _ in range(3):  # até duas rodadas de busca e a resposta
        chamadas: list[dict] = []
        buf = ""
        async for tipo, val in llm.chat_stream(spec["provider"], spec["model"], msgs, [BUSCA], 8192,
                                               effort=None if esforco == "desligado" else esforco,
                                               think=False if esforco == "desligado" else None, slot=slot,
                                               max_tokens=MAX_TOKENS_FALA + (0 if esforco == "desligado" else 4000)):
            if tipo == "content":
                buf += val
                tudo += val
                # parece chamada de ferramenta escrita como texto (modelo pequeno): segura até ver o que é
                segura = tudo.lstrip()[:1] in ("{", "[", "<", "`")
                while kokoro and not segura and (m := re.match(r"(.+?[.!?…:;])(\s+)", buf, re.S)) and len(m.group(1)) > 12:
                    fala(sem_marca(m.group(1)))
                    buf = buf[m.end():]
            elif tipo == "done":
                chamadas = [c for c in val["tool_calls"] if c["name"] == "web_search"]
        if not chamadas:
            chamadas, resto = _chamadas_em_texto(tudo)
            if chamadas:
                tudo = buf = resto
        if not chamadas:
            break
        if not pesquisou:
            pesquisou = True
            fala("Pesquisando.")
        tudo, buf = "", ""  # o que veio antes da busca ("vou pesquisar") não entra na resposta
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
            for c in chamadas]})
        for c in chamadas:
            try:
                r = await asyncio.to_thread(web.web_search, None, {"query": str(c["arguments"].get("query") or texto)})
                saida = r["text"]
            except ToolError as e:
                saida = f"Busca falhou: {e}"
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": saida[:6000]})
    # a piada não entra no histórico: piadas antigas no contexto puxam o modelo a brincar de novo
    hist.append({"role": "assistant", "content": tudo.split("[piada]")[0].strip()})
    if not emprestado or emprestado == modelo_assistente():
        _uso.update(spec=spec, t=time.monotonic(), llm_t=llm.ULTIMO_USO["t"])  # ociosidade conta daqui
    # (o modelo emprestado não é do assistente: quem o descarrega é a ociosidade do próprio Forja)
    _humor["pausa"] = PAUSA_PIADA if "[piada]" in tudo else max(0, _humor["pausa"] - 1)
    if not sem_marca(tudo):
        fala("Fiquei sem resposta dessa vez. Pode repetir?")
    elif kokoro:
        fala(sem_marca(buf))
    else:
        await _fala_tela(sem_marca(tudo))

def _chamadas_em_texto(texto: str) -> tuple[list[dict], str]:
    """Modelo pequeno que escreve a chamada como texto ({"name": "web_search", ...}, <tool_call>...): o mesmo parser
    do agente, depois de desfazer as chaves dobradas que alguns templates deixam escapar."""
    from .parsing import parse_text_tool_calls
    t = re.sub(r"^\s*\{\{(.*)\}\}\s*$", r"{\1}", texto, flags=re.S)
    calls, resto = parse_text_tool_calls(t, ["web_search"])
    return [{"id": c.get("id") or f"busca{i}", "name": c["name"], "arguments": c.get("arguments") or {}}
            for i, c in enumerate(calls)], resto


async def _fala_tela(texto: str) -> None:
    """Voz clonada da tela Voz. O motor dela tira o LLM da VRAM: com trabalho usando o modelo, fala na rápida."""
    from . import modelctl
    if modelctl.em_uso():
        print("Forja: voz: modelo em uso; a resposta sai na voz rápida para não descarregá-lo", flush=True)
        fala(texto)
        return
    cfg = ler()
    try:
        r = await asyncio.to_thread(tts.falar, texto, cfg["modelo_tts"], cfg["voz_tts"], False)
        _manda("toca", arquivo=r["arquivo"])
    except ToolError as e:
        fala(f"Não consegui usar a voz da tela Voz: {e}")


def _trabalho_no_modelo() -> bool:
    """Execução usando o LLM local agora (gerar imagem descarrega ele). Imagem já gerando não conta: entra na fila."""
    from . import modelctl
    no_ar = bool(localai.status().get("running")) or localai._loading  # carregando para um turno também conta
    return no_ar and modelctl.em_uso() and not localai.image_busy()


async def _midia(kind: str, pedido: str, confirmado: bool = False) -> None:
    from . import imagegen, lotes
    cfg = ler()
    modelo = cfg["imagem" if kind == "imagem" else "video"]
    nome = "imagem" if kind == "imagem" else "vídeo"
    if not modelo:
        fala(f"Escolha o seu modelo favorito de {nome} na página F.O.R.J.A.")
        return
    if not confirmado and _trabalho_no_modelo():  # guarda de VRAM: não derruba um trabalho sem perguntar
        _pendente.clear()
        _pendente.update(tipo="vram", kind=kind, pedido=pedido, tentativas=1)
        fala(f"Tem um trabalho usando o modelo de texto agora. Para gerar {'a imagem' if kind == 'imagem' else 'o vídeo'} "
             "eu descarrego ele, e esse trabalho para. Gero mesmo assim?")
        _manda("escuta")
        return
    conv = _nova_conversa(kind)
    _abre(conv, kind)
    fala(f"Criando {'a imagem' if kind == 'imagem' else 'o vídeo'}: {pedido}.")
    try:
        # os parâmetros salvos da tela (tamanho, passos; no vídeo também quadros e fps), como ela mandaria
        opts = localai.read_config().get("image" if kind == "imagem" else "video") or {}
        await asyncio.to_thread(lotes.start, conv, pedido, {**opts, "model": modelo}, [modelo], 1, 0, "aleatoria", True)
    except (ToolError, imagegen.ModeloCarregado) as e:
        with db.session() as s:  # nada foi gerado: a conversa vazia não fica para trás
            c = s.get(db.Conversation, conv)
            if c and not c.messages:
                s.delete(c)
                s.commit()
        fala(f"Não deu para gerar: {e}")


async def _agente(pedido: str, resposta: str | None = None, kind: str = "agent", modelo: str = "") -> None:
    """Agente ou Maestro: pasta parecida já em uso? Pergunta se continua nela; depois, o modelo."""
    if resposta is None:
        nome = nome_projeto(pedido)
        projetos = _projetos()
        achada = pasta_parecida(nome, list(projetos))
        if achada:
            _pendente.clear()
            _pendente.update(tipo="pasta", pedido=pedido, nome=nome, pasta=achada, conv=projetos[achada], tentativas=0,
                             kind=kind, modelo_dito=modelo)
            fala(f"Já tem uma conversa trabalhando na pasta {Path(achada).name}. Continuo nela, ou crio uma pasta nova?")
            _manda("escuta")
            return
        await _pergunta_modelo({"kind": kind, "pedido": pedido, "nome": nome, "modelo_dito": modelo})
        return
    r = _sem_acento(resposta)
    p = dict(_pendente)
    base = {"kind": p.get("kind", "agent"), "pedido": p["pedido"], "modelo_dito": p.get("modelo_dito", "")}
    if re.search(r"\b(contin\w*|mesma|nessa|essa|ela|sim|pode)\b", r):
        await _pergunta_modelo({**base, "conv": p["conv"], "pasta": p["pasta"]})
    elif re.search(r"\b(nova|novo|outra|outro|cria|crie|nao)\b", r):
        await _pergunta_modelo({**base, "nome": p["nome"]})
    elif p["tentativas"] < 1:
        _pendente["tentativas"] += 1
        fala("Não entendi. Diga continuar, ou pasta nova.")
        _manda("escuta")
    else:
        _pendente.clear()
        fala("Deixa pra lá. Pode pedir de novo quando quiser.")


VOLTA_PADRAO = re.compile(r"\b(?:(?:o\s+)?de\s+sempre|padrao|ultimo\s+envio|o\s+mesmo\s+do\s+forja|o\s+do\s+forja|o\s+normal)\b")


async def _trocar_modelo(dito: str, pendente: bool = False) -> None:
    """"Troca seu modelo para o Qwen 3.6": muda o modelo do assistente (o mesmo campo da página) e já começa a
    carregar. "O de sempre" volta a seguir o do último envio no Forja. Sem nome, ou nome que não bate: pergunta."""
    if not dito.strip():
        _pendente.clear()
        _pendente.update(tipo="trocar", tentativas=0)
        modelos = await asyncio.to_thread(modelos_disponiveis)
        fala(f"Para qual modelo? Tenho {_nomes_para_dizer(modelos)}. Ou o de sempre.")
        _manda("escuta")
        return
    if VOLTA_PADRAO.search(_sem_acento(dito)):
        _pendente.clear()
        gravar({"modelo": None})
        _conversa_chat.clear()
        fala("Pronto, volto a usar o modelo do último envio no Forja.")
        return
    modelos = await asyncio.to_thread(modelos_disponiveis)
    spec = escolher_modelo(dito, ler()["favoritos"], modelos, {})
    if not spec:
        tentativas = _pendente.get("tentativas", 0) if pendente else 0
        if tentativas < 2:
            _pendente.clear()
            _pendente.update(tipo="trocar", tentativas=tentativas + 1)
            fala(f"Não achei esse. Tenho {_nomes_para_dizer(modelos)}. Qual?")
            _manda("escuta")
        else:
            _pendente.clear()
            fala("Não achei. Deixei o modelo como estava.")
        return
    _pendente.clear()
    gravar({"modelo": spec})
    _conversa_chat.clear()  # outro modelo: o que o anterior lembrava da conversa falada não vem junto
    fala(f"Pronto, agora respondo com o {falavel(spec['model'])}.")
    if _loop:
        _tarefa.setdefault("pesquisas", set()).add(asyncio.create_task(preaquece()))  # já vai carregando


async def _qual_modelo() -> None:
    try:
        spec = modelo_assistente()
    except ToolError as e:
        fala(str(e))
        return
    emprestado = await asyncio.to_thread(emprestavel)
    agora = emprestado or spec
    extra = "" if ler()["modelo"] else ", o do último envio no Forja"
    if emprestado and emprestado != spec:
        fala(f"Agora estou usando o {falavel(agora['model'])}, que já estava carregado. O meu é o {falavel(spec['model'])}{extra}.")
    else:
        fala(f"Estou com o {falavel(spec['model'])}{extra}.")


async def _vram_respondido(resposta: str) -> None:
    p = dict(_pendente)
    _pendente.clear()
    if re.search(r"\b(sim|pode|gera|gere|manda|vai|claro|ok)\b", _sem_acento(resposta)):
        await _midia(p["kind"], p["pedido"], confirmado=True)
    else:
        fala("Beleza, deixo para depois.")


async def _atende(ouviu: dict) -> None:
    from .llm import LLMError
    comando = (ouviu.get("comando") or "").strip()
    _atendendo[0] += 1
    _estado["estado"] = "pensando"  # o overlay pulsa devagar até a primeira fala
    _anota("voce", comando or ouviu.get("texto", ""))
    try:
        if _pendente and time.time() - _pendente.get("t", time.time()) > PENDENTE_S:
            _pendente.clear()  # perguntou faz tempo e ninguém respondeu: o que chega agora é pedido novo
        if _pendente and _pendente["tipo"] == "modelo" and ouviu.get("resposta") and _parece_pedido(comando):
            _pendente.clear()  # "Com qual modelo?" e veio um pedido completo, não um nome: larga a pergunta
        if _pendente and ouviu.get("resposta") and PARAR.match(_sem_acento(comando)):
            _pendente.clear()  # "cancela" respondendo a uma pergunta: desiste dela
            fala("Ok, deixei pra lá.")
            return
        if _pendente and ouviu.get("resposta"):
            tipo = _pendente["tipo"]
            await (_agente(_pendente["pedido"], comando) if tipo == "pasta" else
                   _modelo_respondido(comando) if tipo == "modelo" else
                   _trocar_modelo(comando, pendente=True) if tipo == "trocar" else _vram_respondido(comando))
            return
        _pendente.clear()  # chamou "Forja" de novo no meio de uma pergunta: vale o comando novo
        if not comando:
            # só "Forja": nada de "Pois não?" (falando, o microfone fecha e come o começo do pedido): bip e escuta
            _manda("bip")
            _manda("escuta")
            return
        tipo, pedido = rota(comando)
        modelo = ""
        if tipo not in ("trocar_modelo", "qual_modelo"):  # "use o modelo X" sozinho é trocar o do assistente
            sem_modelo, modelo = modelo_dito(comando)
            tipo, pedido = rota(sem_modelo)
        if tipo == "chat" and CITA_FORJA.search(_sem_acento(comando)):
            # cita o Forja e nenhuma regra pegou (o Whisper ouviu torto, ou jeito novo de pedir): o modelo decide
            tipo = {"responder": "chat", "chat": "chat_forja"}.get(r := await classifica_rota(sem_modelo), r)
            print(f"Forja: voz: o modelo classificou o pedido como {tipo}", flush=True)
        if tipo == "parar":
            _manda("cala")  # o comando anterior já foi cancelado ao chegar este (_le)
            return
        if tipo == "salvar_conversa":
            _transcricao.pop()  # o próprio pedido de salvar não entra no chat
            salvar_conversa()
            fala("Pronto, nossa conversa virou um chat do Forja.")
            return
        if tipo == "trocar_modelo":
            await _trocar_modelo(pedido)
        elif tipo == "qual_modelo":
            await _qual_modelo()
        elif tipo in ("agente", "maestro"):
            await _agente(pedido, kind="maestro" if tipo == "maestro" else "agent", modelo=modelo)
        elif tipo in ("imagem", "video"):
            await _midia(tipo, pedido)
        elif tipo == "chat_forja":
            await _pergunta_modelo({"kind": "chat", "pedido": pedido, "modelo_dito": modelo})
        elif tipo == "pesquisa":
            await _pergunta_modelo({"kind": "pesquisa", "pedido": pedido, "modelo_dito": modelo})
        else:
            await _chat(comando)
    except asyncio.CancelledError:
        print("Forja: voz: comando cancelado", flush=True)  # comando novo, atalho ou "para" (sem o texto: privacidade)
    except ToolError as e:
        fala(str(e))
    except LLMError as e:
        fala(f"O modelo não respondeu: {str(e)[:160]}")
    except Exception as e:  # bug aqui não pode derrubar o ouvido: fala e registra
        print(f"Forja: voz: erro atendendo um pedido: {e.__class__.__name__}: {e}", flush=True)
        fala("Deu um erro aqui dentro. Olhe o log do Forja.")
    finally:
        _atendendo[0] -= 1
        if _estado["estado"] == "pensando":
            _estado["estado"] = "ouvindo"  # ainda pode ter fala na fila: o ouvido manda "falando" quando tocar


def simular(texto: str) -> None:
    """Teste sem microfone (e o campo "testar comando" da tela Voz): como se tivesse ouvido "Forja, <texto>"."""
    if not _loop:
        raise ToolError("Backend sem loop ainda.")
    resposta = bool(_pendente)
    if not resposta:
        _interrompe()
    _tarefa["f"] = asyncio.run_coroutine_threadsafe(_atende({"texto": texto, "comando": texto, "wake": True,
                                                             "resposta": resposta}), _loop[0])

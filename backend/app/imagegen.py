"""Geração de imagem com stable-diffusion.cpp.

Sem servidor: cada imagem é uma chamada do `sd-cli.exe` que termina e libera a VRAM. Dois caminhos
para a mesma função — a ferramenta `image_generate` (o agente gera e a imagem aparece no chat) e
POST /api/local/image (o painel, com prompt e parâmetros na mão).
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import config, downloads, localai, native, uploads
from .tools import Tool, ToolError, register

OUT_DIR = config.DATA_DIR / "imagens"   # padrão; a tela Imagem pode apontar outra pasta


class ModeloCarregado(ToolError):
    """Tem um LLM na VRAM. Quem chamou decide: descarregar (perde o cache do chat) ou desistir."""


def out_dir() -> Path:
    escolhida = localai.read_config()["image"].get("out_dir")
    return Path(escolhida) if escolhida else OUT_DIR
# Barra de amostragem do sd.cpp: "  |=====>   | 3/8 - 11.5it/s". As barras de carregamento do modelo
# usam MB/s e ficam de fora — senão a barra da UI andaria para trás.
PROGRESS = re.compile(r"\|\s*(\d+)/(\d+) - [\d.]+\s*(?:it/s|s/it)")
TIMEOUT = 1800  # 30 min: CPU puro com modelo grande é lento mesmo


def _opts(patch: dict | None = None) -> dict:
    """Padrão da aba Imagem + ajustes daquele modelo + o que veio na chamada."""
    base = localai.read_config()["image"]
    # Os ajustes são do modelo que vai gerar — num lote multi-modelo o patch troca o modelo a cada
    # imagem, e usar os ajustes do padrão vazaria o VAE/clip do modelo errado.
    alvo = (patch or {}).get("model") or base.get("model", "")
    do_modelo = localai.image_params(alvo) if alvo else {}
    return {**base, **do_modelo, **{k: v for k, v in (patch or {}).items() if v not in (None, "")}}


def _flag_modelo(path: str) -> str:
    """GGUF com `general.architecture` (flux, qwen_image...) é só o unet: vai em --diffusion-model.

    Com -m o sd.cpp procura os pesos com o prefixo de checkpoint completo e não acha nada.
    """
    # ponytail: heurística pelo metadado; o `convert` do sd.cpp (checkpoint inteiro) não grava arquitetura
    if path.lower().endswith(".gguf") and localai.gguf_info(path)["arch"]:
        return "--diffusion-model"
    return "-m"


def argv(exe: Path, prompt: str, out: Path, o: dict) -> list[str]:
    # Sem -M: o modo padrão do sd.cpp é a geração de imagem (img_gen nas builds novas, txt2img nas antigas).
    a = [str(exe), "-p", prompt, "-o", str(out),
         "--steps", str(int(o["steps"])), "--cfg-scale", str(float(o["cfg"])),
         "-W", str(int(o["width"])), "-H", str(int(o["height"])), "--sampling-method", str(o["sampler"])]
    if o.get("diffusion_model"):      # Flux/SD3: o unet vem separado do resto
        a += ["--diffusion-model", str(o["diffusion_model"])]
    elif o.get("model"):
        a += [_flag_modelo(str(o["model"])), str(o["model"])]
    else:
        raise ToolError("Escolha um modelo de imagem no painel IA local › Imagem.")
    for key, flag in (("vae", "--vae"), ("clip_l", "--clip_l"), ("t5xxl", "--t5xxl"), ("llm", "--llm")):
        if o.get(key):
            a += [flag, str(o[key])]
    if o.get("negative"):
        a += ["-n", str(o["negative"])]
    # -s 0 é uma semente válida para o sd.cpp (o padrão dele é 42, sempre a mesma imagem): 0 aqui = aleatória.
    a += ["-s", str(int(o["seed"])) if int(o.get("seed") or 0) else "-1"]
    return a


def _exe() -> Path:
    exe = localai.find_exe("sd")
    if not exe:
        raise ToolError("stable-diffusion.cpp não instalado. Baixe o runtime no painel IA local.")
    return exe


def generate(prompt: str, out: Path, opts: dict | None = None, job_id: str = "") -> Path:
    """Roda o sd-cli até o fim. Bloqueante: quem chama usa thread."""
    exe = _exe()
    if not prompt.strip():
        raise ToolError("Descreva a imagem (prompt vazio).")
    o = _opts(opts)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(argv(exe, prompt, out, o), cwd=str(exe.parent), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace", **native.popen_kwargs())
    tail: list[str] = []
    timer = threading.Timer(TIMEOUT, lambda: native.kill_tree(proc))
    timer.start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            tail.append(line.rstrip())
            del tail[:-40]
            m = PROGRESS.search(line)
            if job_id and m:
                downloads.update(job_id, done=int(m.group(1)), total=int(m.group(2)))
            if job_id and downloads.cancelled(job_id):
                native.kill_tree(proc)
                raise ToolError("Geração cancelada.")
        proc.wait()
    finally:
        timer.cancel()
    if proc.returncode != 0 or not out.exists():
        raise ToolError(f"sd falhou (código {proc.returncode}):\n" + "\n".join(tail[-12:]))
    return out


def start_job(prompt: str, opts: dict | None = None, confirm: bool = False) -> dict:
    """Versão do painel: job com progresso, na pasta escolhida na aba Imagem.

    Com um LLM carregado, os dois disputam a VRAM — então descarregamos antes, mas só depois de a
    pessoa confirmar, porque isso derruba o cache de contexto do chat que estiver aberto.
    """
    argv(_exe(), prompt or " ", OUT_DIR / "x.png", _opts(opts))  # valida runtime, modelo e prompt ANTES
    if localai.status()["running"]:                                # de descarregar o LLM por nada
        if not confirm:
            raise ModeloCarregado(localai.status().get("alias") or "um modelo")
        localai.unload()
    localai.set_image_busy(True)
    job = downloads.create("imagem", prompt[:60])
    out = out_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}.png"

    def work():
        try:
            generate(prompt, out, opts, job["id"])
            downloads.finish(job["id"], result=str(out))
        except Exception as e:
            downloads.finish(job["id"], error=str(e))
        finally:
            localai.set_image_busy(False)

    threading.Thread(target=work, daemon=True).start()
    return job


# ---------------------------------------------------------------- ferramenta

def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


async def image_generate(root: Path, args: dict) -> dict:
    # Aqui não se descarrega nada: a conversa pode estar rodando justamente no modelo local.
    localai.set_image_busy(True)
    prompt = str(args.get("prompt") or "")
    opts = {k: args.get(k) for k in ("negative", "steps", "width", "height", "seed")}
    # A imagem do agente mora na pasta de trabalho da conversa; o arquivo aqui é só passagem.
    out = Path(tempfile.gettempdir()) / "forja-sd" / f"{time.strftime('%Y%m%d-%H%M%S')}.png"
    try:
        await asyncio.to_thread(generate, prompt, out, opts)
    finally:
        localai.set_image_busy(False)
    dados = out.read_bytes()
    out.unlink(missing_ok=True)
    att = uploads.save("imagem.png", dados, "image/png", root)
    return {"text": f"Imagem gerada em {att['path']} ({len(dados) // 1024} KB).", "attachments": [att]}


def _preview(_root: Path, args: dict) -> dict:
    """`kind` tem que ser um dos três que o front conhece (diff | new | command).

    Isto devolvia `kind: "text"` com `title`/`body`, campos que o `Preview` do frontend não tem:
    o card caía no ramo do diff e fazia split num `text` inexistente.
    """
    return {"kind": "new", "path": "imagem.png", "text": str(args.get("prompt") or "")}


register(Tool(
    "image_generate",
    "Gera uma imagem a partir de uma descrição, com o stable-diffusion.cpp local. A imagem é salva "
    "na pasta de trabalho e aparece no chat. Use quando o usuário pedir uma imagem, ilustração ou arte.",
    _obj({"prompt": {"type": "string", "description": "Descrição da imagem, em inglês funciona melhor"},
          "negative": {"type": "string", "description": "O que evitar na imagem"},
          "steps": {"type": "integer", "description": "Passos de amostragem (padrão: o do painel)"},
          "width": {"type": "integer"}, "height": {"type": "integer"},
          "seed": {"type": "integer", "description": "Semente para repetir a mesma imagem"}},
         ["prompt"]),
    image_generate, mutating=True, preview=_preview,
    available=lambda: bool(localai.find_exe("sd"))))

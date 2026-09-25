"""ComfyUI portátil como runtime: é o motor do SeedVR2 (ampliação por difusão), que o sd.cpp não roda.

Baixado como os outros runtimes (IA local), não empacotado: o .7z oficial do ComfyUI para a marca da GPU
(1,4-1,8 GB, com Python e PyTorch dentro), aberto pelo `tar` do Windows (libarchive lê 7z). Versão fixa: o
ComfyUI muda toda semana, e um fluxo que funciona não pode quebrar sozinho. Cada ampliação sobe o servidor,
roda e derruba (comfy_job.py), então a VRAM fica livre entre uma e outra — como o sd-cli.
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from . import downloads, localai, native
from .tools import ToolError

VERSAO = "v0.37.0"  # testado na Arc B580 em 2026-09-25 (SeedVR2 3B, 256 → 1024 em 80 s)
URL = "https://github.com/Comfy-Org/ComfyUI/releases/download/{versao}/ComfyUI_windows_portable_{gpu}.7z"
MB = {"intel": 1410, "amd": 1490, "nvidia": 1790}
PASTA = localai.RUNTIMES / "comfy"
JOB = Path(__file__).with_name("comfy_job.py")


VENDOR = {0x10DE: "nvidia", 0x1002: "amd", 0x8086: "intel"}  # VendorId do PCI


def gpu() -> str:
    """A marca da GPU que o portátil deve usar: a placa de mais VRAM dedicada que o Windows lista (a integrada e
    as virtuais ficam para trás). Sem nenhuma conhecida, NVIDIA: o pacote que também roda só na CPU."""
    return next((VENDOR[p["vendor"]] for p in native.placas() if p["vendor"] in VENDOR and p["vram"]), "nvidia")


def python() -> Path | None:
    exe = PASTA / "python_embeded" / "python.exe"
    return exe if exe.is_file() and (PASTA / "ComfyUI" / "main.py").is_file() else None


def estado() -> dict:
    g = gpu()
    return {"instalado": str(PASTA) if python() else "", "gpu": g, "mb": MB[g], "versao": VERSAO}


def instalar() -> dict:
    if not native.WINDOWS:
        raise ToolError("O ComfyUI portátil só está pronto para Windows.")
    if localai.image_busy():
        raise ToolError("Espere a imagem em andamento terminar antes de instalar o ComfyUI.")
    g = gpu()
    return downloads.start("runtime", f"ComfyUI {g} {VERSAO}", [URL.format(versao=VERSAO, gpu=g)], PASTA, extract=True)


def ampliar(entrada: str, saida: Path, fator: int, modelo: str, job_id: str = "", progresso=None) -> dict:
    """Uma imagem pelo SeedVR2 (comfy_job.py no Python do portátil). `progresso(fase)` a cada fase."""
    from .ampliar import vae_seedvr2
    py = python()
    if not py:
        raise ToolError("Falta o ComfyUI (motor do SeedVR2): baixe na lista de ampliação, em Baixar o que falta.")
    vae = vae_seedvr2(modelo)
    if not vae:
        raise ToolError("Falta o VAE do SeedVR2 (seedvr2_ema_vae_fp16.safetensors): baixe o modelo de novo pelo catálogo.")
    # -X utf8: com o stdout num pipe, o Python do Windows escreve em cp1252 e os acentos chegavam quebrados
    proc = subprocess.Popen([str(py), "-X", "utf8", "-s", str(JOB), "--comfy", str(PASTA), "--modelo", modelo, "--vae", vae,
                             "--entrada", entrada, "--saida", str(saida), "--fator", str(int(fator))],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace", **native.popen_kwargs())
    def vigia() -> None:
        # o driver passa minutos calado na fase "ampliando": o cancelamento não pode esperar a próxima linha
        while proc.poll() is None:
            if job_id and downloads.cancelled(job_id):
                native.kill_tree(proc)  # o driver e o servidor do ComfyUI juntos: a VRAM volta na hora
                return
            time.sleep(0.5)
    threading.Thread(target=vigia, daemon=True).start()
    fim = ""
    for linha in proc.stdout:  # type: ignore[union-attr]
        linha = linha.strip()
        if linha.startswith("FASE ") and progresso:
            progresso(linha[5:])
        elif linha.startswith(("OK", "ERRO")):
            fim = linha
    proc.wait()
    if job_id and downloads.cancelled(job_id):
        raise ToolError("Ampliação cancelada.")
    if not fim.startswith("OK"):
        raise ToolError(fim[5:] if fim.startswith("ERRO") else f"O ComfyUI saiu sem resultado (código {proc.returncode}).")
    w, h = (int(x) for x in fim.split()[1].split("x"))
    return {"w": w, "h": h}

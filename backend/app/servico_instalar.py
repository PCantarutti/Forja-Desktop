"""A tarefa agendada que sobe o serviço sem janela quando o Windows liga (servico.py).

Gatilho "ao iniciar o sistema" exige administrador: por isso o botão só abre o instalar_servico.ps1 num PowerShell
elevado, e o UAC fica com o usuário. A tarefa é S4U (sem senha guardada): com conta Microsoft e "só Windows Hello"
ligado, o Windows recusa a senha. Preço do S4U: segredos DPAPI (chaves de nuvem) não abrem antes do login.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import config
from .tools import ToolError

NOME = "Forja Automatico"   # sem acento: o PowerShell 5.1 lê o .ps1 em ANSI
SCRIPT = Path(__file__).with_name("instalar_servico.ps1")
SEM_JANELA = 0x08000000   # CREATE_NO_WINDOW


def estado() -> dict:
    if sys.platform != "win32":
        return {"suportado": False, "instalado": False}
    r = subprocess.run(["schtasks", "/Query", "/TN", NOME, "/V", "/FO", "LIST"], capture_output=True,
                       creationflags=SEM_JANELA)
    texto = r.stdout.decode("mbcs", errors="replace")
    pasta = str(config.DATA_DIR)
    return {"suportado": True, "instalado": r.returncode == 0,
            "desta_pasta": r.returncode == 0 and pasta.lower() in texto.lower(), "pasta": pasta}


def abrir(acao: str) -> dict:
    if sys.platform != "win32":
        raise ToolError("Só no Windows.")
    if acao not in ("instalar", "remover"):
        raise ToolError("Ação inválida.")
    argumentos = (f'-NoProfile -ExecutionPolicy Bypass -File "{SCRIPT}" -Python "{sys.executable}" '
                  f'-Backend "{Path(__file__).resolve().parents[1]}" -Data "{config.DATA_DIR}" -Port {os.getenv("FORJA_PORT") or 47810}'
                  + (" -Remover" if acao == "remover" else ""))
    subprocess.Popen(["powershell", "-NoProfile", "-Command",
                      f"Start-Process powershell -Verb RunAs -ArgumentList '{argumentos}'"], creationflags=SEM_JANELA)
    return {"aberto": True}

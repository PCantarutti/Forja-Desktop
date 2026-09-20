"""Execução local: shell do sistema, árvore de processos e abrir no editor/Explorer.

O Forja Desktop roda nativo, então existe um único lugar onde comandos rodam: a máquina do usuário.
PowerShell no Windows, bash no Linux/macOS, sempre na pasta da conversa. Este módulo é o código do
antigo `tools/forja_runner.py` trazido para dentro do backend — sem HTTP, sem token, sem um segundo
processo para o usuário iniciar.

Como no Claude Desktop, a proteção é a aprovação (always_ask), não um sandbox: o shell enxerga o
sistema inteiro.
"""
from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
from pathlib import Path

SYSTEM = platform.system()  # Windows | Linux | Darwin
WINDOWS = SYSTEM == "Windows"
PS_PREAMBLE = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; "
               "$ErrorActionPreference='Continue'; ")


def shell_name() -> str:
    if WINDOWS:
        return "pwsh" if shutil.which("pwsh") else "powershell"
    return "bash"


def shell_argv(command: str) -> list[str]:
    """Argv para rodar `command` uma vez e sair, propagando o código de saída."""
    if WINDOWS:
        # Exit code: comandos nativos deixam $LASTEXITCODE; cmdlets que falham deixam $? falso.
        script = PS_PREAMBLE + command + "\nif ($LASTEXITCODE) { exit $LASTEXITCODE } elseif (-not $?) { exit 1 }"
        return [shell_name(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
    return ["bash", "-lc", command]


def term_argv() -> list[str]:
    """Argv do shell interativo do terminal da UI (sem PTY: lê comandos do stdin)."""
    if WINDOWS:
        return [shell_name(), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]
    return ["bash", "-l"]


def popen_kwargs() -> dict:
    """Grupo próprio de processos, para matar a árvore inteira, e sem janela de console no Windows."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """Mata o processo e tudo o que ele abriu (npm -> node, venv -> python...)."""
    if proc.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def decode(raw: bytes) -> str:
    """Saída do processo como texto, com quebras de linha do Windows normalizadas."""
    return raw.decode("utf-8", "replace").replace("\r\n", "\n")


# ------------------------------------------------------------------ ambiente (para o system prompt e o painel)

def version(cmd: str) -> str | None:
    """Pelo shell: no Windows, npm/npx são .cmd e não resolvem sem ele."""
    try:
        r = subprocess.run(shell_argv(cmd), capture_output=True, timeout=15, **popen_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    line = decode(r.stdout or r.stderr).strip().splitlines()
    return line[0][:60] if r.returncode == 0 and line else None


_INFO: dict | None = None
TOOLS = {"node": "node --version", "npm": "npm --version", "python": "python --version", "git": "git --version"}


def info(force: bool = False) -> dict:
    """Sistema, shell e versões do que o agente costuma chamar. Cacheado: sondar custa ~1 s."""
    global _INFO
    if _INFO is None or force:
        _INFO = {"system": SYSTEM, "release": platform.release(), "machine": platform.machine(),
                 "shell": shell_name(), "home": str(Path.home()),
                 "versions": {k: version(v) for k, v in TOOLS.items()}}
    return _INFO


def describe(i: dict | None = None) -> str:
    """Uma linha para o painel e o prompt: 'Windows 11 (powershell)'."""
    i = i or info()
    system = {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(i.get("system"), i.get("system"))
    return " ".join(x for x in (system, i.get("release") or "") if x) + f" ({i.get('shell')})"


# ------------------------------------------------------------------ abrir no editor / revelar

def open_path(path: str, mode: str) -> str:
    if not os.path.exists(path):
        raise ValueError(f"Caminho não existe: {path}")
    if mode == "reveal":
        if WINDOWS:
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
        elif SYSTEM == "Darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
        return "revelado"
    # editor: VS Code se existir, senão o programa padrão do sistema
    if shutil.which("code") or (WINDOWS and shutil.which("code.cmd")):
        subprocess.Popen(shell_argv(f'code "{path}"'), **popen_kwargs())
        return "code"
    if WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
        return "padrão"
    subprocess.Popen(["open" if SYSTEM == "Darwin" else "xdg-open", path])
    return "padrão"

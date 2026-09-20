"""Pasta de trabalho por conversa (como no Claude Desktop).

Rodando nativo, o caminho que a conversa guarda é o caminho de verdade: não há tradução de disco
montado. Cada conversa guarda o caminho escolhido (ex.: `C:/Users/pedro/Dev/app`) e, durante a
execução, `CURRENT` aponta para ele; toda ferramenta de arquivo usa essa raiz.

As ferramentas de arquivo ficam confinadas à pasta escolhida (resolve_path). O run_command, como no
Claude Desktop, é protegido pela aprovação, não por sandbox: ele enxerga o sistema inteiro.
"""
from __future__ import annotations

import contextvars
import os
import re
import string
import sys
from pathlib import Path

from . import config

CURRENT: contextvars.ContextVar[Path | None] = contextvars.ContextVar("forja_workspace", default=None)

DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/]*(.*)$")
WINDOWS = sys.platform == "win32"


class WorkspaceError(ValueError):
    pass


def normalize(host_path: str) -> str:
    """Caminho normalizado.

    Windows: 'c:\\Users\\x\\' -> 'C:/Users/x'   Linux/macOS: '/home/x/' -> '/home/x'
    """
    raw = (host_path or "").strip().strip('"')
    m = DRIVE_RE.match(raw)
    if m:
        prefix, rest = f"{m.group(1).upper()}:/", m.group(2)
    elif raw.startswith("/"):
        prefix, rest = "/", raw
    else:
        raise WorkspaceError("Use um caminho completo, ex.: C:/Users/voce/Projetos/app ou /home/voce/projetos/app")
    parts = [x for x in rest.replace("\\", "/").split("/") if x not in ("", ".")]
    if ".." in parts:
        raise WorkspaceError("Caminho não pode conter '..'.")
    return prefix + "/".join(parts)


def root() -> Path:
    """Raiz da execução atual (conversa); fora de execução, a pasta padrão."""
    return CURRENT.get() or default_root()


def default_root() -> Path:
    return config.WORKSPACE_ROOT


def to_host(path: Path) -> str:
    """Caminho para exibir ao usuário. Nativo: é o próprio caminho, só normalizado."""
    return normalize(str(path))


def resolve(host_path: str | None) -> Path:
    """Pasta da conversa -> diretório existente. Vazio = pasta padrão."""
    if not host_path:
        return default_root()
    p = Path(normalize(host_path))
    if not p.is_dir():
        raise WorkspaceError(f"A pasta não existe: {normalize(host_path)}")
    return p


def label(host_path: str | None) -> str:
    return normalize(str(host_path or default_root()))


# ------------------------------------------------------------------ navegação (seletor interno de pasta)

HIDDEN = {"$Recycle.Bin", "System Volume Information", "$WinREAgent", "Recovery", "Config.Msi"}


def roots() -> list[dict]:
    """Discos (Windows) ou a raiz e a home (Linux/macOS), para o seletor interno."""
    if not WINDOWS:
        return [{"name": "/", "path": "/"}, {"name": "~", "path": normalize(str(Path.home()))}]
    out = []
    for letter in string.ascii_uppercase:
        if Path(f"{letter}:/").is_dir():
            out.append({"name": f"{letter}:", "path": f"{letter}:/"})
    return out


def _parent(host: str) -> str | None:
    if host.endswith(":/") or host == "/":
        return None
    parent = host.rsplit("/", 1)[0]
    return parent + "/" if parent.endswith(":") else (parent or "/")


def list_dirs(host_path: str) -> dict:
    host = normalize(host_path)
    p = Path(host)
    if not p.is_dir():
        raise WorkspaceError(f"Pasta não encontrada: {host}")
    dirs = []
    try:
        with os.scandir(p) as it:
            for e in it:
                try:
                    if e.is_dir() and e.name not in HIDDEN and not e.name.startswith((".", "$")):
                        dirs.append(e.name)
                except OSError:
                    continue
    except PermissionError:
        raise WorkspaceError(f"Sem permissão para listar {host}") from None
    return {"path": host, "parent": _parent(host),
            "dirs": [{"name": d, "path": host.rstrip("/") + "/" + d} for d in sorted(dirs, key=str.lower)]}

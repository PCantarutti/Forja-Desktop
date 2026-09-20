"""Hooks do projeto: comandos que rodam depois de uma ferramenta, definidos em `.forja/hooks.json`.

    {"post_tool": [
        {"tools": ["write_file", "edit_file"], "command": "npx prettier --write \\"{path}\\""},
        {"tools": ["run_command"], "command": "echo rodou {tool}"}
    ]}

`{path}` e `{tool}` são substituídos. O comando roda como o run_command, na
pasta da conversa, com timeout curto; a saída (resumida) é anexada ao resultado da ferramenta para o
modelo ver (ex.: erro de lint). Só ferramentas que terminaram com sucesso disparam hooks.
"""
from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path

from . import shell

FILE = ".forja/hooks.json"
TIMEOUT = 60
MAX_OUT = 1_500


def load(root: Path) -> dict:
    p = root / FILE
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _matches(entry: dict, tool: str) -> bool:
    tools = entry.get("tools") or entry.get("tool") or "*"
    if isinstance(tools, str):
        tools = [tools]
    return any(fnmatch(tool, str(t)) for t in tools)


def run_post(tool: str, args: dict, root: Path) -> str | None:
    """Roda os hooks post_tool que casam com `tool`; devolve texto para anexar ao resultado (ou None)."""
    entries = [e for e in load(root).get("post_tool", []) if isinstance(e, dict) and e.get("command") and _matches(e, tool)]
    if not entries:
        return None
    parts = []
    for e in entries:
        command = str(e["command"]).replace("{path}", str(args.get("path") or "")).replace("{tool}", tool)
        try:
            code, out = shell.exec_in(root, command, int(e.get("timeout") or TIMEOUT))
        except Exception as ex:  # hook quebrado não derruba a ferramenta
            code, out = -1, f"{ex.__class__.__name__}: {ex}"
        out = out.strip()
        if len(out) > MAX_OUT:
            out = "...\n" + out[-MAX_OUT:]
        parts.append(f"[hook `{command}` → exit {code}]" + (f"\n{out}" if out else ""))
    return "\n".join(parts)

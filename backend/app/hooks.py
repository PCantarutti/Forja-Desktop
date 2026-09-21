"""Hooks do projeto: comandos que rodam depois de uma ferramenta, definidos em `.forja/hooks.json`.

    {"post_tool": [
        {"tools": ["write_file", "edit_file"], "command": "npx prettier --write \\"{path}\\""},
        {"tools": ["run_command"], "command": "echo rodou {tool}"}
    ]}

`{path}` e `{tool}` são substituídos. O comando roda como o run_command, na
pasta da conversa, com timeout curto; a saída (resumida) é anexada ao resultado da ferramenta para o
modelo ver (ex.: erro de lint). Só ferramentas que terminaram com sucesso disparam hooks.

**A pasta precisa ser confiável.** O arquivo vem da pasta de trabalho, então ele pode ter vindo junto
num `git clone`: sem essa trava, abrir um repositório de terceiros e pedir um `read_file` já rodaria o
comando que o repositório escolheu, sem card e sem política — o caminho mais curto para fora do
modelo de aprovação do Forja. A pasta é liberada em Configurações › Permissões, uma vez.
"""
from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path

from . import config, shell, workspace

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


def trusted(root: Path) -> bool:
    """A pasta (ou uma acima dela) foi liberada em Configurações › Permissões?"""
    alvo = workspace.normalize(str(root))
    return any(alvo == liberada or alvo.startswith(liberada.rstrip("/") + "/")
               for liberada in (workspace.normalize(p) for p in config.TRUSTED_HOOKS if p.strip()))


def aviso(root: Path) -> str | None:
    """Texto para a conversa quando a pasta tem hooks e ainda não foi liberada."""
    if not (root / FILE).is_file() or trusted(root):
        return None
    caminho = workspace.normalize(str(root))
    return (f"Esta pasta tem `{FILE}`, que roda comandos automaticamente depois das ferramentas. "
            "Nenhum deles vai rodar enquanto você não liberar a pasta em Configurações › "
            "Permissões › Pastas confiáveis:" + chr(10) + chr(10) + f"`{caminho}`")


def run_post(tool: str, args: dict, root: Path) -> str | None:
    """Roda os hooks post_tool que casam com `tool`; devolve texto para anexar ao resultado (ou None)."""
    if not trusted(root):
        return None  # pasta não liberada: quem avisa o usuário é o agente, uma vez por execução
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

"""Hooks do projeto: comandos que rodam em pontos do ciclo do agente, definidos em `.forja/hooks.json`.

    {"post_tool": [
        {"tools": ["write_file", "edit_file"], "command": "npx prettier --write \\"{path}\\""},
        {"tools": ["run_command"], "command": "echo rodou {tool}"}
    ],
     "pre_tool": [{"tools": ["run_command"], "command": "python .forja/checa.py \\"{command}\\""}],
     "stop": [{"command": "npm test --silent"}]}

Eventos: pre_tool, post_tool, user_prompt, session_start, stop, subagent_start, subagent_stop (ver
EVENTOS). `{path}`, `{tool}`, `{command}`, `{prompt}` e `{task}` são substituídos quando fazem
sentido. O comando roda como o run_command, na pasta da conversa, com timeout curto; a saída
(resumida) chega ao modelo. post_tool só dispara para ferramenta que terminou com sucesso.

**A pasta precisa ser confiável.** O arquivo vem da pasta de trabalho, então ele pode ter vindo junto
num `git clone`: sem essa trava, abrir um repositório de terceiros e pedir um `read_file` já rodaria o
comando que o repositório escolheu, sem card e sem política — o caminho mais curto para fora do
modelo de aprovação do Forja. A pasta é liberada em Configurações › Permissões, uma vez.
"""
from __future__ import annotations

import asyncio
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


# Eventos (os do DeepSeek Harness / Claude Code). Código de saída do comando:
#   0 = segue; 2 = BLOQUEIA (pre_tool nega a ferramenta; stop obriga a continuar; a saída é o motivo);
#   3 = só no pre_tool: pede aprovação no card mesmo em modo automático. Outro código: erro do hook,
#   mostrado, mas não bloqueia nada.
EVENTOS = ("pre_tool", "post_tool", "user_prompt", "session_start", "stop", "subagent_start", "subagent_stop")
BLOQUEIA, PERGUNTA = 2, 3


def tem(root: Path) -> bool:
    """Checagem barata antes de mandar um hook para thread: quase nenhuma pasta tem hooks."""
    return (root / FILE).is_file() and trusted(root)


def rodar(evento: str, root: Path, tool: str = "", subs: dict | None = None) -> list[tuple[str, int, str]]:
    """Roda os hooks de `evento` (os de ferramenta, só os que casam com `tool`). [(comando, exit, saída)]."""
    if not trusted(root):
        return []  # pasta não liberada: quem avisa o usuário é o agente, uma vez por execução
    entradas = [e for e in load(root).get(evento, []) if isinstance(e, dict) and e.get("command")
                and (not tool or _matches(e, tool))]
    feitos = []
    for e in entradas:
        command = str(e["command"]).replace("{tool}", tool)
        for k, v in (subs or {}).items():
            command = command.replace("{" + k + "}", str(v or ""))
        try:
            code, out = shell.exec_in(root, command, int(e.get("timeout") or TIMEOUT))
        except Exception as ex:  # hook quebrado não derruba nada
            code, out = -1, f"{ex.__class__.__name__}: {ex}"
        out = out.strip()
        if len(out) > MAX_OUT:
            out = "...\n" + out[-MAX_OUT:]
        feitos.append((command, code, out))
    return feitos


def texto(feitos: list[tuple[str, int, str]]) -> str | None:
    return "\n".join(f"[hook `{c}` → exit {code}]" + (f"\n{out}" if out else "") for c, code, out in feitos) or None


def pre_tool(tool: str, args: dict, root: Path) -> tuple[str, str]:
    """('segue'|'nega'|'pergunta', motivo)."""
    feitos = rodar("pre_tool", root, tool, {"path": args.get("path") or "", "command": args.get("command") or ""})
    if negou := [f for f in feitos if f[1] == BLOQUEIA]:
        return "nega", "\n".join(o or f"bloqueado pelo hook `{c}`" for c, _, o in negou)
    if pediu := [f for f in feitos if f[1] == PERGUNTA]:
        return "pergunta", "\n".join(o or f"o hook `{c}` pediu aprovação" for c, _, o in pediu)
    return "segue", ""


async def rodar_async(evento: str, root: Path, tool: str = "", subs: dict | None = None) -> list:
    return await asyncio.to_thread(rodar, evento, root, tool, subs) if tem(root) else []


async def pre_tool_async(tool: str, args: dict, root: Path) -> tuple[str, str]:
    return await asyncio.to_thread(pre_tool, tool, args, root) if tem(root) else ("segue", "")


def run_post(tool: str, args: dict, root: Path) -> str | None:
    """Roda os hooks post_tool que casam com `tool`; devolve texto para anexar ao resultado (ou None)."""
    return texto(rodar("post_tool", root, tool, {"path": args.get("path") or ""}))

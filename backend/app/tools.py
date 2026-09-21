"""Registry de ferramentas do agente.

Cada Tool declara schema (formato OpenAI), handler, flag `mutating` e opcionalmente
`preview` (usado no card de aprovação). Novas ferramentas (web, shell, MCP...) entram
só com um `register(Tool(...))`, sem mexer no loop do agente.
"""
from __future__ import annotations

import asyncio
import difflib
import inspect
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config, workspace


class ToolError(Exception):
    """Erro devolvido ao modelo como resultado da ferramenta (não derruba o loop)."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[Path, dict], str]  # pode ser async (ex.: MCP); pode devolver {"text", "attachments"}
    mutating: bool = False
    preview: Callable[[Path, dict], dict] | None = None
    always_ask: bool = False  # pede aprovação mesmo com escrita "automática" (ex.: shell)
    source: str = "builtin"   # builtin | mcp:<servidor>
    requires: frozenset[str] = frozenset()  # capacidades do modelo exigidas, ex.: {"vision"}
    available: Callable[[], bool] | None = None  # some da lista quando False (ex.: delegate_task sem subagente)

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def active(caps: set[str] | None = None) -> list[Tool]:
    """Ferramentas ligadas nas Configurações e, se `caps` for dado, que o modelo consegue usar.

    `caps=None` ignora capacidades (listagens da UI). As desligadas/bloqueadas não vão para o modelo.
    """
    return [t for t in REGISTRY.values()
            if t.name not in config.DISABLED_TOOLS and (caps is None or t.requires <= caps)
            and (t.available is None or t.available())]


def blocked(caps: set[str]) -> list[dict]:
    """Ferramentas ligadas mas bloqueadas pelo modelo atual (aparecem no painel como bloqueadas)."""
    return [{"name": t.name, "missing": sorted(t.requires - caps)}
            for t in REGISTRY.values() if t.name not in config.DISABLED_TOOLS and not t.requires <= caps]


def vision_caps(detected: set[str] | None, override: str) -> set[str]:
    """Capacidades efetivas: o que o provider informou (ou nada) ajustado pelo override do usuário."""
    caps = set(detected or ())
    if override == "yes":
        caps.add("vision")
    elif override == "no":
        caps.discard("vision")
    return caps


def get_tool(name: str, caps: set[str] | None = None) -> Tool:
    if name in config.DISABLED_TOOLS:
        raise ToolError(f"A ferramenta '{name}' está desativada nas configurações do Forja.")
    tool = REGISTRY.get(name)
    if not tool:
        raise ToolError(f"Ferramenta desconhecida: '{name}'. Disponíveis: {', '.join(REGISTRY)}")
    if caps is not None and not tool.requires <= caps:
        faltam = ", ".join(sorted(tool.requires - caps))
        raise ToolError(f"'{name}' exige {faltam} e o modelo atual não tem (ou marque 'Visão: sim' no painel). "
                        "Valide pela estrutura da página: browser_read e browser_console.")
    return tool


def coerce_args(tool: Tool, args: dict) -> dict:
    """Converte strings vindas do fallback XML ("true", "10") para o tipo do schema."""
    props = tool.parameters.get("properties", {})
    out = dict(args)
    for k, v in args.items():
        t = props.get(k, {}).get("type")
        if isinstance(v, str) and t == "boolean":
            out[k] = v.strip().lower() in ("true", "1", "yes", "sim")
        elif isinstance(v, str) and t == "integer" and v.strip().lstrip("-").isdigit():
            out[k] = int(v)
    return out


def _call(name: str, fn_attr: str, args: dict, root: Path | None):
    tool = get_tool(name)
    try:
        return getattr(tool, fn_attr)(root or workspace.root(), coerce_args(tool, args))
    except (KeyError, TypeError, ValueError) as e:  # argumento faltando/errado
        raise ToolError(f"Argumentos inválidos para {name}: faltando ou incorreto {e}") from e


def run_tool(name: str, args: dict, root: Path | None = None) -> str:
    return _call(name, "handler", args, root)


async def execute(name: str, args: dict, root: Path | None = None) -> str:
    """Executa handler sync (em thread) ou async (direto)."""
    tool = get_tool(name)
    if not inspect.iscoroutinefunction(tool.handler):
        return await asyncio.to_thread(run_tool, name, args, root)
    try:
        return await tool.handler(root or workspace.root(), coerce_args(tool, args))
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError(f"Argumentos inválidos para {name}: faltando ou incorreto {e}") from e


def unregister_source(source: str) -> None:
    for name in [n for n, t in REGISTRY.items() if t.source == source]:
        del REGISTRY[name]


def preview_tool(name: str, args: dict, root: Path | None = None) -> dict | None:
    return _call(name, "preview", args, root) if get_tool(name).preview else None


# ---------------------------------------------------------------- confinamento

def resolve_path(root: Path, path: str | None) -> Path:
    """Resolve `path` dentro de `root`. Bloqueia `..`, absolutos fora da raiz e symlinks que escapem."""
    root_r = root.resolve()
    raw = (path or ".").strip() or "."
    # Modelos às vezes mandam "/workspace/x"; trate como relativo à raiz. Caminho absoluto de verdade
    # (C:/... ou /home/...) passa direto: o join abaixo o mantém e o confinamento decide.
    if raw == "/workspace" or raw.startswith("/workspace/"):
        raw = raw[len("/workspace"):].lstrip("/") or "."
    target = (root_r / raw).resolve()  # resolve() segue symlinks
    if not target.is_relative_to(root_r):
        raise ToolError(
            f"Acesso negado: '{path}' está fora da pasta de trabalho. "
            "Use caminhos relativos à pasta de trabalho.")
    return target


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root.resolve()).as_posix() or "."


def _read_text(p: Path) -> str:
    if not p.is_file():
        raise ToolError(f"Arquivo não encontrado: '{p.name}'. Use list_dir para ver o que existe.")
    size = p.stat().st_size
    if size > config.MAX_FILE_BYTES:
        raise ToolError(f"Arquivo grande demais ({size} bytes, limite {config.MAX_FILE_BYTES}).")
    data = p.read_bytes()
    if b"\x00" in data[:8192]:
        raise ToolError("Arquivo binário; só arquivos de texto são suportados.")
    return data.decode("utf-8", errors="replace")


def _diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{name}", tofile=f"b/{name}"))


# ---------------------------------------------------------------- list_dir

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
MAX_LIST_ENTRIES = 500


def list_dir(root: Path, args: dict) -> str:
    base = resolve_path(root, args.get("path"))
    if not base.is_dir():
        raise ToolError(f"Não é um diretório: '{args.get('path')}'.")
    out: list[str] = []
    if args.get("recursive"):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            d = Path(dirpath)
            for name in dirnames:
                out.append(_rel(root, d / name) + "/")
            for name in sorted(filenames):
                out.append(_rel(root, d / name))
            if len(out) >= MAX_LIST_ENTRIES:
                break
    else:
        for p in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            out.append(_rel(root, p) + ("/" if p.is_dir() else f"  ({p.stat().st_size} B)"))
    if not out:
        return f"(diretório vazio: {_rel(root, base)})"
    note = f"\n(listagem truncada em {MAX_LIST_ENTRIES} entradas)" if len(out) >= MAX_LIST_ENTRIES else ""
    return "\n".join(out[:MAX_LIST_ENTRIES]) + note


# ---------------------------------------------------------------- read_file

MAX_READ_LINES = 2000


def read_file(root: Path, args: dict) -> str:
    p = resolve_path(root, args.get("path"))
    lines = _read_text(p).splitlines()
    start = max(int(args.get("start_line") or 1), 1)
    end = int(args.get("end_line") or len(lines))
    end = min(end, len(lines), start + MAX_READ_LINES - 1)
    if not lines:
        return f"(arquivo vazio: {_rel(root, p)})"
    body = "\n".join(f"{i:>5}\t{lines[i - 1]}" for i in range(start, end + 1))
    if end < len(lines):
        body += f"\n(mostrando linhas {start}-{end} de {len(lines)}; use start_line/end_line para ver o resto)"
    return body


# ---------------------------------------------------------------- write_file

def _check_size(content: str) -> None:
    n = len(content.encode("utf-8"))
    if n > config.MAX_FILE_BYTES:
        raise ToolError(f"Conteúdo grande demais ({n} bytes, limite {config.MAX_FILE_BYTES}).")


def write_file(root: Path, args: dict) -> str:
    p = resolve_path(root, args.get("path"))
    content = args["content"]
    _check_size(content)
    if p.is_dir():
        raise ToolError(f"'{args.get('path')}' é um diretório.")
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="")
    lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    return f"Arquivo {'sobrescrito' if existed else 'criado'}: {_rel(root, p)} ({lines} linhas)"


def write_preview(root: Path, args: dict) -> dict:
    p = resolve_path(root, args.get("path"))
    content = args.get("content", "")
    _check_size(content)
    if p.is_file():
        return {"kind": "diff", "path": _rel(root, p), "text": _diff(_read_text(p), content, _rel(root, p))}
    return {"kind": "new", "path": _rel(root, p), "text": content}


# ---------------------------------------------------------------- edit_file

def _edits(args: dict) -> list[dict]:
    """As edições desta chamada: a lista `edits`, ou o par old_str/new_str do topo (uma edição)."""
    raw = args.get("edits")
    if isinstance(raw, list) and raw:
        return [e for e in raw if isinstance(e, dict)]
    return [{"old_str": args.get("old_str"), "new_str": args.get("new_str"),
             "replace_all": args.get("replace_all")}]


def _one_edit(text: str, edit: dict, onde: str) -> tuple[str, int]:
    old_str, new_str = edit.get("old_str"), edit.get("new_str")
    if not old_str:
        raise ToolError(f"{onde}old_str vazio. Para criar ou reescrever o arquivo inteiro use write_file.")
    count = text.count(old_str)
    if count == 0:
        raise ToolError(
            f"{onde}old_str não encontrado no arquivo. Leia o arquivo com read_file e copie o trecho "
            "exatamente (espaços, indentação e quebras de linha), sem os números de linha.")
    if count > 1 and not edit.get("replace_all"):
        raise ToolError(
            f"{onde}old_str aparece {count} vezes. Inclua mais linhas de contexto para que o trecho seja único, "
            "ou mande replace_all=true para trocar todas.")
    trocas = count if edit.get("replace_all") else 1
    return text.replace(old_str, new_str or "", trocas), trocas


def _apply_edit(root: Path, args: dict) -> tuple[Path, str, str, int]:
    """Aplica as edições em sequência, tudo ou nada: se uma não bater, nada é escrito."""
    p = resolve_path(root, args.get("path"))
    edits = _edits(args)
    old = novo = _read_text(p)
    trocas = 0
    for i, edit in enumerate(edits, 1):
        onde = f"Edição {i}: " if len(edits) > 1 else ""
        novo, n = _one_edit(novo, edit, onde)
        trocas += n
    return p, old, novo, trocas


def edit_file(root: Path, args: dict) -> str:
    p, _, new, trocas = _apply_edit(root, args)
    _check_size(new)
    p.write_text(new, encoding="utf-8", newline="")
    detalhe = f" ({trocas} trechos)" if trocas > 1 else ""
    return f"Arquivo editado: {_rel(root, p)}{detalhe}"


def edit_preview(root: Path, args: dict) -> dict:
    p, old, new, _ = _apply_edit(root, args)
    return {"kind": "diff", "path": _rel(root, p), "text": _diff(old, new, _rel(root, p))}


# ---------------------------------------------------------------- registro

def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


register(Tool(
    "list_dir", "Lista arquivos e pastas de um diretório da pasta de trabalho.",
    _obj({"path": {"type": "string", "description": "Diretório relativo à pasta de trabalho. Padrão: '.'"},
          "recursive": {"type": "boolean", "description": "Listar subpastas também. Padrão: false"}}, []),
    list_dir))
register(Tool(
    "read_file", "Lê um arquivo de texto e devolve o conteúdo com números de linha.",
    _obj({"path": {"type": "string"},
          "start_line": {"type": "integer", "description": "Primeira linha (1-based), opcional"},
          "end_line": {"type": "integer", "description": "Última linha (inclusiva), opcional"}}, ["path"]),
    read_file))
register(Tool(
    "write_file", "Cria ou sobrescreve um arquivo com o conteúdo completo. Cria diretórios intermediários.",
    _obj({"path": {"type": "string"}, "content": {"type": "string", "description": "Conteúdo completo do arquivo"}},
         ["path", "content"]),
    write_file, mutating=True, preview=write_preview))
register(Tool(
    "edit_file",
    "Substitui trechos exatos de um arquivo. Por padrão old_str precisa aparecer uma vez só; use "
    "replace_all para trocar todas as ocorrências, e `edits` para várias trocas no mesmo arquivo numa "
    "chamada só (aplicadas em ordem; se uma não bater, nada é escrito).",
    _obj({"path": {"type": "string"},
          "old_str": {"type": "string", "description": "Trecho exato existente (sem números de linha)"},
          "new_str": {"type": "string", "description": "Texto que substitui old_str"},
          "replace_all": {"type": "boolean",
                          "description": "Trocar todas as ocorrências em vez de exigir trecho único"},
          "edits": {"type": "array",
                    "description": "Várias edições no mesmo arquivo, em vez de old_str/new_str soltos",
                    "items": _obj({"old_str": {"type": "string"}, "new_str": {"type": "string"},
                                   "replace_all": {"type": "boolean"}}, ["old_str", "new_str"])}},
         ["path"]),
    edit_file, mutating=True, preview=edit_preview))

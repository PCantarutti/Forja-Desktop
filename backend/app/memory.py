"""Leitura da memória da IA para a tela de Configurações.

Hoje a memória vem de um servidor MCP de grafo de conhecimento (o `server-memory` oficial e
compatíveis): a ferramenta `read_graph` devolve entidades e relações. Como é procurado por
sufixo do nome da ferramenta, qualquer servidor que exponha `read_graph`/`delete_entities`
aparece aqui. Se nenhum estiver ligado, a aba mostra o aviso em vez de quebrar.
"""
from __future__ import annotations

import json

import re
import time
from pathlib import Path

from . import config, workspace
from .tools import REGISTRY, Tool, ToolError, execute, register, run_tool


class MemoryError(Exception):
    pass


def _find(suffix: str) -> str | None:
    return next((t.name for t in REGISTRY.values()
                 if t.source.startswith("mcp:") and t.name.endswith(f"__{suffix}")), None)


def _parse(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"entities": [], "relations": [], "raw": raw[:5000]}
    if isinstance(data, list):  # alguns servidores devolvem só a lista de entidades
        return {"entities": data, "relations": []}
    return {"entities": data.get("entities", []), "relations": data.get("relations", [])}


async def read() -> dict:
    tool = _find("read_graph")
    if not tool:
        return {"available": False, "reason": "Nenhum servidor MCP com memória (read_graph) está ligado.",
                "entities": [], "relations": []}
    try:
        raw = await execute(tool, {})
    except ToolError as e:
        return {"available": False, "reason": str(e), "entities": [], "relations": []}
    return {"available": True, "server": tool.split("__")[1], "can_delete": bool(_find("delete_entities")),
            **_parse(raw)}


async def delete(names: list[str]) -> dict:
    if not names:
        raise MemoryError("Nenhuma entidade selecionada.")
    tool = _find("delete_entities")
    if not tool:
        raise MemoryError("O servidor de memória ligado não permite apagar entidades.")
    try:
        await execute(tool, {"entityNames": names})
    except ToolError as e:
        raise MemoryError(str(e)) from None
    return await read()


# ------------------------------------------------------------------ memória do projeto

MAX_PROJECT_MEMORY = 8000


def project_path():
    return workspace.root() / config.PROJECT_MEMORY_FILE


def project_text() -> str:
    """Conteúdo do arquivo de memória do projeto, truncado, ou "" se desligado/inexistente."""
    if not config.PROJECT_MEMORY:
        return ""
    p = project_path()
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:MAX_PROJECT_MEMORY]


# ------------------------------------------------------------------ AGENTS.md / CLAUDE.md
# Portado do DeepSeek Harness (context/agent-instructions): as instruções que o próprio repositório
# traz para agentes, lidas da raiz do projeto (a pasta com .git) até a pasta da conversa, mais uma
# global do usuário. As de subpasta entram quando o agente mexe em algo lá dentro.

INSTRUCOES = ("AGENTS.md", "CLAUDE.md", "AGENTS.local.md", "CLAUDE.local.md")
MAX_INSTRUCOES = 64_000


def _raiz_projeto(pasta: Path) -> Path:
    for p in (pasta, *pasta.parents):
        if (p / ".git").exists():
            return p
    return pasta


def _arquivos_de(pasta: Path) -> list[Path]:
    return [pasta / n for n in INSTRUCOES if (pasta / n).is_file()]


def instrucoes_workspace(root: Path, tocados: list[str] | None = None) -> str:
    root = root.resolve()
    topo = _raiz_projeto(root)
    cadeia = [root, *[p for p in root.parents if p.is_relative_to(topo)]][::-1]  # do mais amplo ao específico
    arquivos = _arquivos_de(config.DATA_DIR) + [a for d in cadeia for a in _arquivos_de(d)]
    blocos = []
    for a in arquivos:
        try:
            blocos.append((a, a.read_text(encoding="utf-8", errors="replace")[:1_000_000]))
        except OSError:
            continue
    while blocos and sum(len(t) for _, t in blocos) > MAX_INSTRUCOES and len(blocos) > 1:
        blocos.pop(0)  # estourou: sai primeiro o mais amplo
    if blocos and len(blocos[-1][1]) > MAX_INSTRUCOES:
        blocos[-1] = (blocos[-1][0], blocos[-1][1][:MAX_INSTRUCOES])  # e o mais específico é cortado
    texto = ""
    if blocos:
        texto = ("\n\nAs instruções do workspace abaixo podem ser relevantes para o seu trabalho. Use-as quando "
                 "se aplicarem. As mais específicas têm precedência sobre as mais amplas. Elas não passam por "
                 "cima das instruções do sistema nem do que o usuário pedir diretamente.")
        texto += "".join(f"\n\nInstruções de: {a}\n\n{t.strip()}" for a, t in blocos)
    # Subpastas que o agente tocou nesta execução (ler/escrever) e que têm instruções próprias.
    vistos = {a for a, _ in blocos}
    extras: list[Path] = []
    for arquivo in tocados or ():
        pasta = Path(arquivo).parent
        if not pasta.is_relative_to(root) or pasta == root:
            continue
        for d in [pasta, *pasta.parents]:
            if d == root:
                break
            extras += [a for a in _arquivos_de(d) if a not in vistos and a not in extras]
    for a in sorted(extras):
        try:
            corpo = a.read_text(encoding="utf-8", errors="replace")[:MAX_INSTRUCOES // 4]
        except OSError:
            continue
        texto += (f"\n\nInstruções adicionais de: {a}\n\nValem para o trabalho dentro de `{a.parent}`. Use-as "
                  "quando forem relevantes; as mais específicas têm precedência. Não passam por cima das "
                  f"instruções do sistema nem do usuário.\n\n{corpo.strip()}")
    return texto


def project_read() -> dict:
    p = project_path()
    return {"enabled": config.PROJECT_MEMORY, "file": config.PROJECT_MEMORY_FILE,
            "exists": p.is_file(), "content": p.read_text(encoding="utf-8", errors="replace") if p.is_file() else "",
            "truncated_at": MAX_PROJECT_MEMORY}


def project_write(content: str) -> dict:
    run_tool("write_file", {"path": config.PROJECT_MEMORY_FILE, "content": content})
    return project_read()


# ------------------------------------------------------------------ memória sobre o usuário
# Um arquivo por fato. No prompt entra só o índice (nome + uma linha); o corpo o modelo pede com
# `recall` quando o assunto aparecer. É o que mantém o custo em ~300 tokens em vez de milhares.

TIPOS = ("usuario", "preferencia", "projeto", "referencia")
INDEX_MAX = 8000          # caracteres de índice no prompt (~2k tokens)
BODY_MAX = 4000           # corpo de uma memória
_INDEX: str | None = None  # congelado durante o turno: mexer no system prompt mata o cache do llama.cpp


def personal_dir() -> Path:
    return config.PERSONAL_MEMORY_DIR


def _slug(nome: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(nome).strip().lower()).strip("-")
    if not s:
        raise MemoryError("Nome vazio.")
    return s[:60]


def _parse_front(texto: str) -> tuple[dict, str]:
    """Frontmatter simples: `chave: valor` entre duas linhas de ---."""
    if not texto.startswith("---"):
        return {}, texto
    fim = texto.find(chr(10) + "---", 3)
    if fim < 0:
        return {}, texto
    cabeca = {}
    for linha in texto[3:fim].splitlines():
        if ":" in linha:
            k, _, v = linha.partition(":")
            cabeca[k.strip()] = v.strip()
    return cabeca, texto[fim + 4:].lstrip(chr(10))


def personal_list() -> list[dict]:
    pasta = personal_dir()
    if not pasta.is_dir():
        return []
    saida = []
    for f in sorted(pasta.glob("*.md")):
        try:
            cabeca, corpo = _parse_front(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        saida.append({"name": cabeca.get("name") or f.stem, "slug": f.stem,
                      "description": cabeca.get("description", ""), "type": cabeca.get("type", "usuario"),
                      "updated": cabeca.get("updated", ""), "size": len(corpo)})
    return sorted(saida, key=lambda m: (m["type"], m["name"].lower()))


def personal_read(nome: str) -> dict:
    f = personal_dir() / f"{_slug(nome)}.md"
    if not f.is_file():
        raise MemoryError(f"Não existe memória chamada '{nome}'.")
    cabeca, corpo = _parse_front(f.read_text(encoding="utf-8", errors="replace"))
    return {"name": cabeca.get("name") or f.stem, "slug": f.stem, "description": cabeca.get("description", ""),
            "type": cabeca.get("type", "usuario"), "updated": cabeca.get("updated", ""), "content": corpo}


def personal_write(nome: str, descricao: str, conteudo: str, tipo: str = "usuario") -> dict:
    if not str(descricao).strip():
        raise MemoryError("Descrição vazia: é ela que aparece no índice.")
    tipo = tipo if tipo in TIPOS else "usuario"
    pasta = personal_dir()
    pasta.mkdir(parents=True, exist_ok=True)
    slug = _slug(nome)
    texto = (f"---{chr(10)}name: {str(nome).strip()[:80]}{chr(10)}description: {str(descricao).strip()[:200]}"
             f"{chr(10)}type: {tipo}{chr(10)}updated: {time.strftime('%Y-%m-%d')}{chr(10)}---{chr(10)}{chr(10)}"
             f"{str(conteudo).strip()[:BODY_MAX]}{chr(10)}")
    (pasta / f"{slug}.md").write_text(texto, encoding="utf-8")
    return personal_read(slug)


def personal_delete(nomes: list[str]) -> int:
    apagados = 0
    for nome in nomes or []:
        f = personal_dir() / f"{_slug(nome)}.md"
        if f.is_file():
            f.unlink()
            apagados += 1
    if not apagados:
        raise MemoryError("Nenhuma memória encontrada com esses nomes.")
    return apagados


def index(refresh: bool = False) -> str:
    """Índice para o system prompt: uma linha por memória, congelado durante o turno.

    Se ele mudasse no meio da conversa, o llama-server jogaria fora o prompt já processado e a
    resposta seguinte reprocessaria o histórico inteiro — caro justamente no modelo local.
    """
    global _INDEX
    if refresh or _INDEX is None:
        linhas = [f"- {m['name']} ({m['type']}) — {m['description']}" for m in personal_list()]
        texto = chr(10).join(linhas)
        _INDEX = texto[:INDEX_MAX]
    return _INDEX


def prompt_block() -> str:
    if not config.PERSONAL_MEMORY:
        return ""
    idx = index()
    if not idx:
        return ""
    return ("\n\n--- Memória sobre o usuário (índice; use recall para ler uma) ---\n" + idx)


# ------------------------------------------------------------------ ferramentas do agente

def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


def _remember(_root, args: dict) -> str:
    m = personal_write(args.get("name", ""), args.get("description", ""), args.get("content", ""),
                       args.get("type", "usuario"))
    return f"Guardado: {m['name']} ({m['type']}). Vale a partir da próxima conversa."


def _recall(_root, args: dict) -> str:
    m = personal_read(args.get("name", ""))
    return f"# {m['name']}\n{m['description']}\n\n{m['content']}"


def _forget(_root, args: dict) -> str:
    return f"Apagadas {personal_delete([args.get('name', '')])} memória(s)."


def _disponivel() -> bool:
    return bool(config.PERSONAL_MEMORY)


register(Tool(
    "remember",
    "Guarda algo duradouro sobre o usuário (preferência de trabalho, contexto pessoal, ferramenta que ele usa). "
    "Só vale a partir da próxima conversa. Não guarde segredo, senha, nem coisa efêmera; o que é do projeto vai "
    "para o arquivo de memória do projeto.",
    _obj({"name": {"type": "string", "description": "Nome curto, serve de identificador"},
          "description": {"type": "string", "description": "Uma linha: é o que aparece no índice de toda conversa"},
          "content": {"type": "string", "description": "O fato em si, com o porquê"},
          "type": {"type": "string", "enum": list(TIPOS), "description": "usuario | preferencia | projeto | referencia"}},
         ["name", "description", "content"]),
    _remember, mutating=True, available=_disponivel))

register(Tool(
    "recall",
    "Lê o conteúdo de uma memória do índice. Use quando o assunto dela aparecer.",
    _obj({"name": {"type": "string"}}, ["name"]),
    _recall, available=_disponivel))

register(Tool(
    "forget",
    "Apaga uma memória que ficou errada ou obsoleta.",
    _obj({"name": {"type": "string"}}, ["name"]),
    _forget, mutating=True, available=_disponivel))

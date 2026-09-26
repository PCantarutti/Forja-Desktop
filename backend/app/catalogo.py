"""Catálogo de ferramentas enxuto para janela pequena (E4).

Medido em 2026-09-26: 56 ferramentas somam ~12,7 mil tokens de schema, e o prompt base mais ~3,7 mil. Numa
janela de 8k o primeiro turno nem cabe; em 16–32k sobra pouco para a conversa. Quando o catálogo passa de
FRACAO da janela: as descrições encurtam para a primeira frase, e os grupos de nicho ficam atrás de
`mais_ferramentas`, que os liga sob demanda. O que foi ligado vale até o fim da conversa: o catálogo só cresce,
e só quando o modelo pede, para não reescrever o começo do prompt (cache) a cada passo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .tools import Tool, ToolError, register_extra

FRACAO = 0.2
GRUPOS: dict[str, tuple[str, ...]] = {
    "documentos": ("write_document", "edit_document", "preview_document", "write_spreadsheet", "edit_spreadsheet"),
    "imagens": ("image_generate", "imagens_pendentes"),
    "metas": ("create_goal", "get_goal", "update_goal", "workflow"),
    "terminal": ("terminal_open", "terminal_send", "terminal_read", "terminal_close", "terminal_list"),
    "memoria": ("remember", "recall", "forget", "session_search", "session_read"),
    "navegador_extra": ("browser_eval", "browser_tabs", "browser_upload", "browser_scroll"),
    "agentes": ("list_agents", "interrupt_agent"),
    "codigo": ("ast", "imports"),
    "board": ("board_card",),
}
# Janela muito pequena (< MINIMO tokens): também saem estes, e fica o núcleo (arquivos, shell, tarefas, perguntas).
GRUPOS_MINIMO: dict[str, tuple[str, ...]] = {
    "navegador": ("browser_navigate", "browser_read", "browser_screenshot", "browser_click", "browser_type",
                  "browser_console", "browser_validate"),
    "servidores": ("serve_start", "serve_status", "serve_stop"),
    "web": ("web_search", "fetch_url"),
    "subagentes": ("delegate_task", "explore"),
    "busca": ("code_search", "tree"),
}
MINIMO = 12_288
GRUPOS = {**GRUPOS, **GRUPOS_MINIMO}  # mais_ferramentas liga qualquer um deles
_GRUPO_DE = {nome: g for g, nomes in GRUPOS.items() for nome in nomes}
LIGADOS: dict[int, set[str]] = {}   # conversa -> grupos ligados por mais_ferramentas (até o backend reiniciar)


def tokens(tools: list[Tool]) -> int:
    return sum(len(json.dumps(t.openai_schema(), ensure_ascii=False)) // 3 for t in tools)


def enxuto(tools: list[Tool], janela: int | None) -> bool:
    return bool(janela) and tokens(tools) > FRACAO * janela


def filtra(tools: list[Tool], conv: int | None, janela: int | None) -> list[Tool]:
    """No modo enxuto: tira os grupos de nicho não ligados e põe `mais_ferramentas` se sobrou algum escondido."""
    if not enxuto(tools, janela):
        return tools
    ligados = LIGADOS.get(conv or 0, set())
    # Janela média: só os grupos de nicho saem. Muito pequena: o núcleo fica, e o resto sob demanda.
    escondiveis = set(GRUPOS) if janela and janela < MINIMO else set(GRUPOS) - set(GRUPOS_MINIMO)
    ficam = [t for t in tools if _GRUPO_DE.get(t.name) not in escondiveis - ligados]
    if len(ficam) < len(tools):
        ficam.append(MAIS_FERRAMENTAS)
    return ficam


def _primeira_frase(texto: str, maximo: int) -> str:
    frase = re.split(r"(?<=[.!?])\s", texto.strip(), maxsplit=1)[0]
    return frase if len(frase) <= maximo else frase[:maximo - 1].rstrip() + "…"


def _encurta(schema):
    if isinstance(schema, dict):
        return {k: (_primeira_frase(v, 90) if k == "description" and isinstance(v, str) else _encurta(v))
                for k, v in schema.items()}
    if isinstance(schema, list):
        return [_encurta(x) for x in schema]
    return schema


def schema(tool: Tool, curto: bool) -> dict:
    """O schema OpenAI da ferramenta; no modo enxuto, com as descrições na primeira frase."""
    s = tool.openai_schema()
    if not curto:
        return s
    fn = s["function"]
    return {"type": "function", "function": {"name": fn["name"], "description": _primeira_frase(fn["description"], 220),
                                             "parameters": _encurta(fn["parameters"])}}


def _mais_ferramentas(_root: Path, args: dict) -> str:
    from .sessoes import CONV
    grupo = str(args.get("grupo") or "").strip().lower()
    if grupo not in GRUPOS:
        raise ToolError(f"Grupo desconhecido: '{grupo}'. Use um de: {', '.join(GRUPOS)}.")
    LIGADOS.setdefault(CONV.get() or 0, set()).add(grupo)
    return (f"Grupo '{grupo}' ligado: {', '.join(GRUPOS[grupo])}. As ferramentas aparecem a partir do próximo passo "
            "e continuam até o fim da conversa.")


MAIS_FERRAMENTAS = register_extra(Tool(
    "mais_ferramentas",
    "Liga um grupo de ferramentas que ficou escondido para caber na janela do modelo. Grupos: navegador, "
    "servidores (serve_*), web (busca e páginas), subagentes (delegate_task, explore), busca (code_search, tree), documentos (Word, "
    "Excel, PDF), imagens (gerar), metas (goals e workflow), terminal (sessão persistente), memoria (lembrar e "
    "buscar conversas), navegador_extra (eval, abas, upload, rolar), agentes, codigo (ast e imports), board (cards).",
    {"type": "object", "properties": {"grupo": {"type": "string", "enum": list(GRUPOS),
                                                "description": "O grupo a ligar"}},
     "required": ["grupo"]},
    _mais_ferramentas, mutating=False))

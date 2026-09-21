"""Compactação de contexto.

Quando o prompt estimado passa de COMPACT_AT da janela, as mensagens anteriores aos últimos
KEEP_TURNS turnos viram um resumo (gerado pelo próprio modelo). O resumo é salvo como evento
`summary` com `covers_until` = id da última mensagem resumida; o histórico enviado ao modelo
passa a ser: system → resumo → mensagens depois de covers_until. Nada é apagado do banco.
O corte é sempre antes de uma mensagem do usuário, então pares tool_call/resultado nunca se separam.
"""
from __future__ import annotations

import json

from . import llm
from .parsing import split_think

KEEP_TURNS = 2
MAX_RESULT_CHARS = 1500


def last_summary(msgs) -> tuple[str, int] | None:
    for m in reversed(msgs):
        if m.role == "event" and (m.meta or {}).get("kind") == "summary":
            return m.content, m.meta["covers_until"]
    return None


def split_point(msgs) -> int | None:
    """Id da última mensagem a resumir, ou None se não há o que compactar."""
    prev = last_summary(msgs)
    covered = prev[1] if prev else 0
    users = [i for i, m in enumerate(msgs) if m.role == "user"]
    if len(users) <= KEEP_TURNS:
        return None
    cut = users[-KEEP_TURNS]  # primeira mensagem mantida
    candidates = [m for m in msgs[:cut] if m.id > covered and m.role != "event"]
    return candidates[-1].id if candidates else None


def transcript(msgs, until: int, max_chars: int) -> str:
    prev = last_summary(msgs)
    covered = prev[1] if prev else 0
    lines = [f"[Resumo anterior]\n{prev[0]}"] if prev else []
    for m in msgs:
        if m.id <= covered or m.id > until or m.role == "event":
            continue
        if m.role == "user":
            lines.append(f"USUÁRIO: {m.content}")
        elif m.role == "assistant":
            if m.content:
                lines.append(f"ASSISTENTE: {m.content}")
            for c in m.tool_calls or []:
                lines.append(f"ASSISTENTE chamou {c['name']}({json.dumps(c['arguments'], ensure_ascii=False)[:500]})")
        elif m.role == "tool":
            lines.append(f"RESULTADO {m.name} [{m.status}]: {m.content[:MAX_RESULT_CHARS]}")
    text = "\n\n".join(lines)
    return text[-max_chars:]  # se ainda for grande, fica com o fim (mais recente)


PROMPT = ("Resuma a conversa abaixo entre um usuário e um agente de programação, para que o agente continue o "
          "trabalho sem o histórico completo. Inclua: objetivo do usuário, decisões tomadas, arquivos criados ou "
          "alterados (com caminhos), comandos executados e resultados relevantes, erros encontrados e o que falta "
          "fazer. Seja factual e conciso, em tópicos, no idioma da conversa. Não invente nada.")


async def summarize(provider: str, model: str, text: str, num_ctx: int) -> str:
    out = ""
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": text}]
    async for kind, val in llm.chat_stream(provider, model, messages, None, num_ctx, think=False):
        if kind == "content":
            out += val
    return split_think(out)[1].strip()

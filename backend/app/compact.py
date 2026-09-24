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
# Poda sem modelo, antes de gastar uma chamada de resumo (DeepSeek Harness, compaction-tool-result-pruner):
# resultado de ferramenta antigo e grande fica com cabeça e cauda.
PODA_ACIMA = 8192
PODA_CABECA = 4096
PODA_CAUDA = 1024
PODA_MANTEM = 4   # os últimos resultados ficam inteiros: é neles que o modelo está trabalhando


def podar(texto: str) -> str:
    if len(texto) <= PODA_ACIMA:
        return texto
    return texto[:PODA_CABECA] + "\n\n[... meio do resultado podado ...]\n\n" + texto[-PODA_CAUDA:]


def retomada(resumo: str) -> str:
    """Como o resumo entra no histórico mandado ao modelo."""
    return ("Este é um checkpoint gerado automaticamente, que condensa uma parte anterior da conversa para "
            "liberar contexto. Trate o que ele registra como fato estabelecido e siga a partir dele sem "
            "repeti-lo. Continue a tarefa direto das mensagens seguintes, sem comentar este checkpoint.\n\n"
            f"<resumo-compactado>\n{resumo}\n</resumo-compactado>")


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


# Modelo estruturado do DeepSeek Harness (compaction-basic/summarizer.ts), traduzido. Seção fixa em vez
# de "resuma": o que some num resumo livre é justamente pendência, erro resolvido e pedido literal.
PROMPT = """Você é o motor de compactação de um agente de programação. Condense a conversa que vem na próxima mensagem num checkpoint estruturado que permita a outro modelo retomar o trabalho sem perder nada essencial.

Escreva EXATAMENTE a estrutura Markdown abaixo, com todas as seções, nesta ordem. Tópicos curtos, não parágrafos. Seção sem conteúdo leva "(nenhum)" — nunca a omita.

## Pedido e intenção
- [objetivos do usuário, originais e como evoluíram; cite literalmente quando a redação importa]

## Conceitos técnicos
- [tecnologias, frameworks, padrões e convenções em jogo]

## Arquivos e código
- [caminho exato: por que importa, mudanças ou trechos-chave]

## Erros e correções
- [erro: como foi resolvido, e o que o usuário disse sobre ele]

## Pendências
- [o que foi pedido explicitamente e ainda não foi feito]

## Trabalho atual
- [exatamente o que estava em andamento neste ponto]

## Próximo passo
- [a próxima ação, alinhada ao pedido mais recente, ou "(nenhum)"]

## Contexto crítico
- [decisões e motivos, restrições, preferências do usuário, dúvidas abertas, dados necessários para continuar]

Regras:
- Preserve exatamente caminhos, comandos, mensagens de erro, identificadores, números e assinaturas.
- Registre com fidelidade o que o usuário pediu e corrigiu, principalmente as correções.
- Não mencione este pedido de resumo nem que o contexto foi compactado.
- Escreva só o checkpoint: não chame ferramenta nem faça mais nada.
- Se a conversa começar com um [Resumo anterior], ele é um checkpoint ANTERIOR. Não o copie inteiro: mantenha o que ainda vale, descarte o que ficou velho e funda o novo num resumo só, com a mesma estrutura.
- Escreva no idioma da conversa."""


async def summarize(provider: str, model: str, text: str, num_ctx: int) -> str:
    out = ""
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": text}]
    async for kind, val in llm.chat_stream(provider, model, messages, None, num_ctx, think=False):
        if kind == "content":
            out += val
    return split_think(out)[1].strip()

"""Juiz do raciocínio (E16-B): só quando o filtro da parte A fica em dúvida (raciocínio acima de 3× a mediana
do modelo, sem degeneração visível).

Estilo SemIf: o prompt termina com três opções, cada uma com uma letra, e o veredito sai da probabilidade do
próximo token (1 token de saída, `logprobs`). Não há texto nem JSON para interpretar. Roda num slot livre do
mesmo llama-server, em paralelo, sem pausar o principal; sem slot livre (-np 1), não roda.
"""
from __future__ import annotations

import math

import httpx

from . import config, metricas, modelctl

OPCOES = {"A": "progredindo", "B": "girando", "C": "alucinando"}
LIMITE = 0.7  # confiança mínima para agir (a E10 calibra)
TRECHO = 2000

PROMPT = (
    "Você avalia o raciocínio de outro modelo de IA enquanto ele trabalha numa tarefa de programação. "
    "Responda com UMA letra:\n"
    "A) progredindo: o raciocínio avança, descobre coisas novas e se aproxima de uma ação.\n"
    "B) girando: volta às mesmas dúvidas, repete ideias, hesita sem decidir.\n"
    "C) alucinando: afirma fatos sobre o código, arquivos ou resultados que não foram vistos.")


def mensagens(pedido: str, raciocinio: str) -> list[dict]:
    meio = len(raciocinio) // 2
    trechos = (f"[começo]\n{raciocinio[:TRECHO // 2]}\n\n[meio]\n{raciocinio[meio - TRECHO // 4: meio + TRECHO // 4]}"
               f"\n\n[fim]\n{raciocinio[-TRECHO:]}")
    return [{"role": "system", "content": PROMPT},
            {"role": "user", "content": f"Pedido:\n{pedido[:1500]}\n\nRaciocínio:\n{trechos}\n\nLetra (A, B ou C):"}]


def _probs(top: list[dict]) -> dict[str, float]:
    """Probabilidade de cada letra entre as opções do 1º token (normalizada só entre A, B e C)."""
    out: dict[str, float] = {}
    for t in top or []:
        letra = str(t.get("token") or "").strip().strip(")").upper()[:1]
        if letra in OPCOES:
            out[letra] = out.get(letra, 0.0) + math.exp(float(t.get("logprob", -99)))
    soma = sum(out.values())
    return {k: v / soma for k, v in out.items()} if soma else {}


async def julga(pedido: str, raciocinio: str, principal: dict) -> dict | None:
    """{"veredito", "confianca" (None = sem logprobs, veio por gramática)} ou None (não rodou)."""
    rota = modelctl.como_rodar("juiz", principal)
    if rota.caminho != "outro-slot":  # sem slot livre não roda junto: fica só o filtro e o teto
        return None
    corpo = {"messages": mensagens(pedido, raciocinio), "max_tokens": 1, "temperature": 0, "logprobs": True,
             "top_logprobs": 10, "id_slot": rota.slot, "cache_prompt": False, "reasoning_budget_tokens": 0,
             "reasoning_budget": 0, "chat_template_kwargs": {"enable_thinking": False}}
    url = f"http://127.0.0.1:{config.LOCAL_PORT}/v1/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = (await c.post(url, json=corpo)).json()
            escolha = (r.get("choices") or [{}])[0]
            top = ((escolha.get("logprobs") or {}).get("content") or [{}])[0].get("top_logprobs")
            if probs := _probs(top):
                letra = max(probs, key=probs.get)
                res = {"veredito": OPCOES[letra], "confianca": round(probs[letra], 3)}
            else:  # backend sem logprobs: uma letra forçada por gramática, sem confiança
                corpo = {k: v for k, v in corpo.items() if k not in ("logprobs", "top_logprobs")}
                corpo["grammar"] = 'root ::= "A" | "B" | "C"'
                r = (await c.post(url, json=corpo)).json()
                letra = str(((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()[:1].upper()
                if letra not in OPCOES:
                    return None
                res = {"veredito": OPCOES[letra], "confianca": None}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None
    metricas.registra("juiz", veredito=res["veredito"], confianca=res["confianca"], chars=len(raciocinio))
    return res


def decide(res: dict | None, filtro_suspeita: bool) -> str:
    """'abortar', 'estender' ou '' (nada). Sem confiança (gramática), só aborta com o filtro concordando."""
    if not res:
        return ""
    if res["veredito"] == "progredindo":
        return "estender"
    confia = res["confianca"] >= LIMITE if res["confianca"] is not None else filtro_suspeita
    return "abortar" if confia else ""

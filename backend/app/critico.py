"""Revisão de CÓDIGO da tarefa do Maestro (E8): confere cada critério de aceite contra o diff.

Não confundir com `revisor.py`, que aprova ou não uma chamada de ferramenta no modo Automático.

Roda depois de o verify passar (o teste prova que funciona; o critério prova que é o que foi pedido) e
antes do `update_task(completed)`, que é quem vira commit (E2). O modelo sai de `como_rodar("revisor")`:
o da Maestro por padrão, nunca uma troca de modelo por revisão.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, gitops, llm, modelctl
from .parsing import split_think

MODOS = ("off", "avisa", "bloqueia")
MAX_DIFF = 24_000

PROMPT = (
    "Você confere se o diff abaixo atende a CADA critério de aceite da tarefa. Não julgue estilo nem sugira "
    "melhorias: só se o critério está cumprido pelo código. Responda com UM objeto JSON e nada mais:\n"
    '{"criterios":[{"n":1,"atendido":true,"evidencia":"arquivo.py:12 — por quê"}, ...]}\n'
    "Um item por critério, na ordem dada. 'atendido' false quando o diff não mostra o critério cumprido; a "
    "evidência diz onde (arquivo:linha) ou o que falta.")


def modo(root: Path | None = None) -> str:
    """`revisao: off|avisa|bloqueia` no FORJA.md do projeto vence a configuração global."""
    if root is not None:
        try:
            if m := re.search(r"^\s*revis[aã]o\s*:\s*(off|avisa|bloqueia)\b", (root / "FORJA.md").read_text("utf-8"),
                              re.I | re.M):
                return m.group(1).lower()
        except OSError:
            pass
    v = str(getattr(config, "REVISAO", "avisa") or "avisa")
    return v if v in MODOS else "avisa"


def _json(texto: str) -> dict | None:
    texto = split_think(texto)[1]
    for m in re.finditer(r"\{.*\}", texto, re.S):
        try:
            return json.loads(m.group(0))
        except ValueError:
            continue
    return None


def interpreta(texto: str, criterios: list[str]) -> list[dict]:
    """Uma linha por critério. Resposta que não dá para ler vira "sem veredito" (nunca reprova às cegas)."""
    dado = _json(texto) or {}
    itens = dado.get("criterios") if isinstance(dado.get("criterios"), list) else []
    out = []
    for i, c in enumerate(criterios):
        it = itens[i] if i < len(itens) and isinstance(itens[i], dict) else {}
        at = it.get("atendido")
        out.append({"criterio": c, "atendido": at if isinstance(at, bool) else None,
                    "evidencia": str(it.get("evidencia") or "")[:300]})
    return out


def falhas(itens: list[dict]) -> list[dict]:
    return [i for i in itens if i["atendido"] is False]


def para_o_worker(itens: list[dict]) -> str:
    return ("A revisão de código apontou critérios de aceite NÃO atendidos. Corrija só isto e rode o verify:\n"
            + "\n".join(f"- {i['criterio']} — {i['evidencia'] or 'sem evidência no diff'}" for i in falhas(itens)))


async def revisar(root: Path, contrato: dict, paths: set[str], pedido: dict | None) -> dict:
    """{"modelo", "criterios": [...], "motivo"}; criterios vazio = não revisou (motivo diz por quê)."""
    criterios = [str(c) for c in (contrato.get("acceptance_criteria") or []) if str(c).strip()]
    if not criterios:
        return {"modelo": "", "criterios": [], "motivo": "a tarefa não tem critérios de aceite"}
    if not paths or not gitops.is_repo(root):
        return {"modelo": "", "criterios": [], "motivo": "sem diff para revisar"}
    rota = modelctl.como_rodar("revisor", pedido)
    if not rota.spec or rota.caminho in ("pular", "trocar-modelo"):
        return {"modelo": "", "criterios": [], "motivo": f"revisão pulada: {rota.motivo}"}
    diff = "\n".join(gitops.diff(root, p) for p in sorted(paths))[:MAX_DIFF]
    if not diff.strip():
        return {"modelo": "", "criterios": [], "motivo": "sem diff para revisar"}
    lista = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(criterios))
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": f"Objetivo: {contrato.get('goal') or ''}\n\nCritérios:\n{lista}\n\nDiff:\n{diff}"}]
    texto = ""
    spec = rota.spec
    try:
        async for kind, val in llm.chat_stream(spec["provider"], spec["model"], messages, None, config.NUM_CTX,
                                               "baixo", **({"slot": rota.slot} if rota.slot is not None else {})):
            if kind == "content":
                texto += val
    except llm.LLMError as e:
        return {"modelo": spec["model"], "criterios": [], "motivo": f"revisão indisponível: {e}"}
    return {"modelo": spec["model"], "criterios": interpreta(texto, criterios), "motivo": ""}

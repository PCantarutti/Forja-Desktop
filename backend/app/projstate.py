"""Notas de sessão da Maestro: o que carrega a continuidade de uma conversa para a próxima.

As tarefas já moram no banco (taskdb), e a compactação automática já impede a conversa de crescer
sem limite. O que nenhuma das duas guarda é o PORQUÊ: a decisão arquitetural que foi tomada, o
problema que ficou em aberto, o próximo passo combinado. É isso que se perde quando o histórico é
resumido, e é o que uma sessão nova precisa para não refazer uma discussão já encerrada.

A nota vai para `.forja/maestro/SESSION-NNN.md`, dentro do projeto e não do banco do app: é decisão
sobre o código, e o lugar dela é ao lado do código, versionada junto. `.forja/` já é a pasta do Forja
no projeto (agents/, skills/, hooks.json). A Maestro lê a nota mais recente no início de toda
requisição, então uma conversa nova na mesma pasta começa sabendo onde a anterior parou.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import taskdb, workspace
from .tools import Tool, ToolError, register_extra

PASTA = ".forja/maestro"
PADRAO = re.compile(r"^SESSION-(\d{3,})\.md$")
MAX_NOTA_NO_PROMPT = 6000   # a nota entra em TODA requisição da Maestro: teto para não pesar
MAX_ITENS = 15
MAX_TEXTO = 1500


def _pasta(root: Path) -> Path:
    return root / PASTA


def _numeros(root: Path) -> list[int]:
    pasta = _pasta(root)
    if not pasta.is_dir():
        return []
    return sorted(int(m.group(1)) for p in pasta.iterdir() if (m := PADRAO.match(p.name)))


def latest(root: Path | None = None) -> tuple[str, str] | None:
    """(nome, conteúdo) da nota mais recente da pasta, ou None."""
    root = root or workspace.root()
    nums = _numeros(root)
    if not nums:
        return None
    nome = f"SESSION-{nums[-1]:03d}.md"
    try:
        return nome, (_pasta(root) / nome).read_text("utf-8")
    except OSError:
        return None


def _lista(raw) -> list[str]:
    itens = raw if isinstance(raw, list) else ([raw] if raw else [])
    return [str(x).strip()[:MAX_TEXTO] for x in itens[:MAX_ITENS] if str(x).strip()]


def _abertas(conv_id: int | None) -> list[str]:
    """Retrato das tarefas que ainda não fecharam. Vai junto na nota porque as tarefas são desta
    conversa: uma sessão nova, em outra conversa, não as veria pelo list_tasks."""
    if conv_id is None:
        return []
    linhas = []
    for f in taskdb.board(conv_id)["features"]:
        for t in f["tasks"]:
            if t["status"] in taskdb.OPEN:
                motivo = f" — {t['blocked_reason']}" if t.get("blocked_reason") else ""
                linhas.append(f"{t['code']} [{t['status']}] {t['title']}{motivo}")
    return linhas


def write(root: Path, conv_id: int | None, objetivo: str, resultado: str,
          decisoes: list[str], problemas: list[str], proximo: str) -> str:
    """Grava a próxima nota e devolve o nome. Nunca sobrescreve: cada sessão ganha um arquivo."""
    objetivo = str(objetivo or "").strip()[:MAX_TEXTO]
    if not objetivo:
        raise ToolError("Informe 'objective': o que esta sessão estava tentando alcançar.")
    pasta = _pasta(root)
    pasta.mkdir(parents=True, exist_ok=True)
    n = (_numeros(root) or [0])[-1] + 1
    nome = f"SESSION-{n:03d}.md"

    def secao(titulo: str, corpo) -> str:
        if isinstance(corpo, list):
            corpo = "\n".join(f"- {x}" for x in corpo) if corpo else "- (nenhum)"
        return f"## {titulo}\n{corpo or '(não informado)'}\n"

    texto = "\n".join([
        f"# {nome.removesuffix('.md')}",
        f"_{datetime.now():%d/%m/%Y %H:%M}_\n",
        secao("Objetivo", objetivo),
        secao("Resultado", str(resultado or "").strip()[:MAX_TEXTO]),
        secao("Decisões", _lista(decisoes)),
        secao("Problemas", _lista(problemas)),
        secao("Próximo passo", str(proximo or "").strip()[:MAX_TEXTO]),
        secao("Tarefas em aberto", _abertas(conv_id)),
    ])
    # `x` falha se o arquivo existir: duas Maestros gravando na mesma pasta não se sobrescrevem.
    with open(pasta / nome, "x", encoding="utf-8") as fh:
        fh.write(texto)
    return nome


def prompt_block(root: Path | None = None) -> str:
    """Trecho do system prompt com a última nota. Vazio quando não há nota."""
    achada = latest(root)
    if not achada:
        return ""
    nome, texto = achada
    corte = texto[:MAX_NOTA_NO_PROMPT]
    if len(corte) < len(texto):
        corte += f"\n… (nota truncada; leia {PASTA}/{nome} inteira com read_file)"
    return (f"\n\nOnde a sessão anterior parou ({PASTA}/{nome}). Leve em conta as decisões e os "
            "problemas abaixo: não reabra o que já foi decidido sem um motivo novo.\n" + corte)


def _session_note(_root: Path, args: dict) -> str:
    nome = write(workspace.root(), taskdb.CONV.get(), args.get("objective"), args.get("result"),
                 args.get("decisions") or [], args.get("problems") or [], args.get("next_step"))
    return (f"Nota gravada em {PASTA}/{nome}. A próxima sessão da Maestro nesta pasta começa por ela.")


SESSION_NOTE = register_extra(Tool(
    "session_note",
    "Registra onde esta sessão parou, para a próxima continuar sem reler a conversa inteira: "
    "objetivo, resultado, DECISÕES tomadas (e o porquê), PROBLEMAS em aberto e o próximo passo. As "
    "tarefas em aberto entram sozinhas. Chame ao concluir uma funcionalidade, antes de parar com "
    "trabalho pela metade, e quando a conversa estiver longa.",
    {"type": "object", "properties": {
        "objective": {"type": "string", "description": "O que esta sessão estava tentando alcançar"},
        "result": {"type": "string", "description": "O que ficou pronto"},
        "decisions": {"type": "array", "items": {"type": "string"},
                      "description": "Decisões tomadas e o motivo de cada uma"},
        "problems": {"type": "array", "items": {"type": "string"},
                     "description": "O que ficou em aberto ou deu errado"},
        "next_step": {"type": "string", "description": "O que fazer a seguir"}},
     "required": ["objective"]},
    _session_note))
# Escreve só dentro de .forja/maestro/, sempre arquivo novo, com caminho montado aqui e nunca vindo
# do modelo: é escrituração da Maestro, como o update_task. Marcada como mutante, pediria aprovação
# a cada nota e travaria a execução autônoma.

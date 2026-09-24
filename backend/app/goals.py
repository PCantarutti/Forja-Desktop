"""Goals: um objetivo longo por conversa, perseguido em rodadas (porte do DeepSeek Harness, goal/*).

O agente cria a goal quando o pedido é um objetivo de várias etapas. Toda vez que o turno ia acabar
com a goal ativa, o loop (agent.py) injeta um `<goal_round>` e ele continua — até marcar completa
com evidência, ou bloqueada depois de 3 rodadas seguidas no mesmo impedimento, ou bater o teto.
Retomar a conversa desarma a goal: ela só volta a girar quando o agente chama `update_goal resume`.
"""
from __future__ import annotations

import contextvars
from pathlib import Path

from sqlalchemy import select

from . import db
from .tools import Tool, ToolError, _obj, register

MAX_RODADAS = 256
BLOQUEIO_MIN = 3
CONV: contextvars.ContextVar[int | None] = contextvars.ContextVar("forja_goal_conv", default=None)


def atual(conv_id: int) -> db.Goal | None:
    with db.session() as s:
        return s.scalars(select(db.Goal).where(db.Goal.conversation_id == conv_id)
                         .order_by(db.Goal.id.desc())).first()


def _conv() -> int:
    c = CONV.get()
    if c is None:
        raise ToolError("Goals só existem numa conversa do agente.")
    return c


def _desc(g: db.Goal) -> str:
    return (f"goal_id={g.id} revision={g.revisao} status={g.status}{'' if g.armada else ' (desarmada)'} "
            f"rodada={g.rodada}/{MAX_RODADAS}\nObjetivo: {g.objective}"
            + (f"\nMotivo do bloqueio: {g.motivo}" if g.status == "bloqueada" else ""))


def create_goal(_root: Path, args: dict) -> str:
    objetivo = str(args.get("objective") or "").strip()
    if not objetivo:
        raise ToolError("Informe 'objective'.")
    conv = _conv()
    with db.session() as s:
        for g in s.scalars(select(db.Goal).where(db.Goal.conversation_id == conv, db.Goal.status == "ativa")):
            g.status = "pausada"  # uma goal ativa por conversa
        g = db.Goal(conversation_id=conv, objective=objetivo)
        s.add(g)
        s.commit()
        return "Goal criada. Trabalhe nela; quando o turno for acabar, a próxima rodada começa sozinha.\n" + _desc(g)


def get_goal(_root: Path, _args: dict) -> str:
    g = atual(_conv())
    return _desc(g) if g else "Nenhuma goal nesta conversa."


def update_goal(_root: Path, args: dict) -> str:
    conv = _conv()
    acao = str(args.get("action") or "")
    with db.session() as s:
        g = s.get(db.Goal, int(args.get("goal_id") or 0))
        if not g or g.conversation_id != conv:
            raise ToolError("goal_id não existe nesta conversa. Chame get_goal e copie o goal_id.")
        if int(args.get("revision") or 0) != g.revisao:
            raise ToolError(f"revision desatualizada (atual: {g.revisao}). Chame get_goal antes de atualizar.")
        if acao == "complete":
            g.status = "completa"
            fim = ("<goal_complete>\nA goal foi marcada como completa. Relate ao usuário só o que as rodadas e os "
                   "resultados de ferramenta desta conversa comprovam, e o que ficou de fora. Não chame mais "
                   "ferramentas.\n</goal_complete>")
        elif acao == "blocked":
            motivo = str(args.get("blocked_reason") or "").strip()
            if not motivo:
                raise ToolError("blocked exige 'blocked_reason' com o impedimento concreto.")
            if g.rodada_bloqueio != g.rodada:  # na mesma rodada, insistir não conta de novo
                g.bloqueios = g.bloqueios + 1 if g.rodada_bloqueio == g.rodada - 1 else 1
            g.rodada_bloqueio = g.rodada
            if g.bloqueios < BLOQUEIO_MIN:
                g.revisao += 1
                s.commit()
                raise ToolError(f"Ainda não: bloqueada só depois de {BLOQUEIO_MIN} rodadas seguidas com o mesmo "
                                f"impedimento (esta é a {g.bloqueios}ª). Dificuldade, dúvida ou trabalho útil "
                                "restante não é bloqueio — tente outro caminho nesta rodada. Nova revision: "
                                f"{g.revisao}.")
            g.status, g.motivo = "bloqueada", motivo
            fim = ("<goal_blocked>\nA goal foi marcada como bloqueada. Explique ao usuário o impedimento concreto, "
                   "o que foi tentado e o que ele precisa decidir ou fornecer. Relate só o que as ferramentas "
                   "comprovaram. Não chame mais ferramentas.\n</goal_blocked>")
        elif acao == "pause":
            g.status, fim = "pausada", "Goal pausada."
        elif acao == "resume":
            g.status, g.armada, fim = "ativa", True, "Goal retomada e armada: continue o trabalho."
        else:
            raise ToolError("action deve ser complete, blocked, pause ou resume.")
        g.revisao += 1
        s.commit()
        return f"{fim}\n{_desc(g)}"


RETOMADAS_NA_TELA: set[int] = set()  # ponytail: em memória; reiniciar o app só volta a pedir o resume


def desarmar(conv_id: int) -> None:
    """Início de um turno novo do usuário: goal ativa fica desarmada até update_goal resume — a não ser
    que o usuário tenha acabado de retomá-la pela faixa da goal, que é o mesmo pedido."""
    if conv_id in RETOMADAS_NA_TELA:
        RETOMADAS_NA_TELA.discard(conv_id)
        return
    with db.session() as s:
        for g in s.scalars(select(db.Goal).where(db.Goal.conversation_id == conv_id, db.Goal.status == "ativa")):
            g.armada = False
        s.commit()


def proxima_rodada(conv_id: int) -> str | None:
    """Chamado quando o turno ia acabar. Devolve o prompt da próxima rodada, ou None para parar."""
    with db.session() as s:
        g = s.scalars(select(db.Goal).where(db.Goal.conversation_id == conv_id, db.Goal.status == "ativa",
                                            db.Goal.armada.is_(True)).order_by(db.Goal.id.desc())).first()
        if not g:
            return None
        if g.rodada >= MAX_RODADAS:
            g.status, g.motivo = "pausada", f"teto de {MAX_RODADAS} rodadas"
            s.commit()
            return None
        g.rodada += 1
        s.commit()
        return (f"<goal_round>\nObjetivo: \"{g.objective}\"\nRodada: {g.rodada}/{MAX_RODADAS}\n\n"
                "Continue trabalhando no objetivo nesta mesma conversa. O estado atual da pasta, os resultados "
                "de ferramenta e o estado salvo valem mais que a narração anterior: confira em vez de supor. "
                "Faça progresso concreto e verifique o resultado. Antes de dizer que terminou, reúna evidência "
                "de que o objetivo INTEIRO foi atingido, chame get_goal e marque completa com update_goal. Se "
                "ainda falta trabalho, deixe a goal ativa para a próxima rodada. Bloqueio só depois de "
                f"{BLOQUEIO_MIN} rodadas seguidas no mesmo impedimento.\n</goal_round>")


def contexto(conv_id: int) -> str:
    g = atual(conv_id)
    if not g or g.status in ("completa", "cancelada"):
        return ""
    linha = f"\n\nGoal desta conversa ({g.status}{'' if g.armada or g.status != 'ativa' else ', desarmada'}): {g.objective}"
    if g.status == "ativa" and not g.armada:
        linha += ("\nEla foi desarmada porque a conversa foi retomada. Se o usuário pedir para continuar ou "
                  "retomar (em qualquer palavra ou idioma), chame get_goal e update_goal action=resume.")
    return linha


GOAL_TOOLS = [
    register(Tool(
        "create_goal",
        "Cria o objetivo longo desta conversa: o agente segue em rodadas automáticas até provar que terminou. "
        "Use quando o usuário pede, em qualquer idioma, um objetivo de várias etapas para você perseguir até o "
        "fim; não use para trabalho de um turno só.",
        _obj({"objective": {"type": "string", "description": "O objetivo, completo e verificável"}}, ["objective"]),
        create_goal)),
    register(Tool("get_goal", "Mostra a goal da conversa com goal_id e revision (copie-os no update_goal).",
                  _obj({}, []), get_goal, poll=True)),
    register(Tool(
        "update_goal",
        "Atualiza a goal. Chame get_goal antes e copie goal_id e revision. complete: só quando o objetivo foi "
        f"de fato atingido, com evidência. blocked: só depois de {BLOQUEIO_MIN} rodadas seguidas com o mesmo "
        "impedimento, com blocked_reason concreto. resume: rearma depois de retomar a conversa. pause: para.",
        _obj({"goal_id": {"type": "integer"}, "revision": {"type": "integer"},
              "action": {"type": "string", "enum": ["complete", "blocked", "resume", "pause"]},
              "blocked_reason": {"type": "string"}}, ["goal_id", "revision", "action"]),
        update_goal)),
]


def para_tela(conv_id: int) -> dict | None:
    g = atual(conv_id)
    if not g or g.status == "cancelada":
        return None
    return {"id": g.id, "objective": g.objective, "status": g.status, "armada": g.armada, "rodada": g.rodada,
            "max": MAX_RODADAS, "motivo": g.motivo}


def acao_da_tela(conv_id: int, acao: str) -> dict | None:
    """Pausar, retomar ou descartar a goal pela faixa acima do campo de mensagem."""
    with db.session() as s:
        g = s.scalars(select(db.Goal).where(db.Goal.conversation_id == conv_id).order_by(db.Goal.id.desc())).first()
        if not g:
            return None
        if acao == "pause":
            g.status = "pausada"
        elif acao == "resume":
            g.status, g.armada = "ativa", True
            RETOMADAS_NA_TELA.add(conv_id)
        elif acao == "clear":
            g.status = "cancelada"
        else:
            raise ToolError("action deve ser pause, resume ou clear.")
        g.revisao += 1
        s.commit()
    return para_tela(conv_id)

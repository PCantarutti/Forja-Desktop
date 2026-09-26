"""E15 parte C: o board executa o Backlog sozinho, um card por vez, e para no primeiro problema.

Por projeto e desligado por padrão. Só liga com o sandbox isolado (E12) de pé e o projeto num repositório
git, e só pega card que o usuário aceitou (Backlog): o que está em Novo nunca roda sozinho, nem card de
segurança. O resultado sempre para em Revisão; Concluído continua sendo decisão do usuário.

Travas do fim de cada card:
- modo agente: o verify do card é obrigatório; passou, os arquivos que a conversa escreveu viram um
  commit do card (como o commit por tarefa do Maestro, E2); falhou, a execução automática para;
- Maestro: verify, commit por tarefa e regressão já são dele (E1/E2); tarefa em needs_human para tudo.

O tique vem do /api/activity (a cada ~4 s, com o PC ou o celular abertos).
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from sqlalchemy import select

from . import board, db, gitops, sandbox

CHAVE = "board_auto"          # {projeto: {ligado, por_dia, dia, feitos, em_curso, parado}}
POR_DIA = 5
ESCRITAS = ("write_file", "edit_file")
_FINALIZANDO: set[int] = set()


def _todos() -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        return dict(linha.value) if linha and isinstance(linha.value, dict) else {}


def _grava(projeto: str, **campos) -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        dados = dict(linha.value) if linha and isinstance(linha.value, dict) else {}
        dados[projeto] = {**(dados.get(projeto) or {}), **campos}
        s.merge(db.AppSetting(key=CHAVE, value=dados))
        s.commit()
        return dados[projeto]


def estado(projeto: str) -> dict:
    e = {"ligado": False, "por_dia": POR_DIA, "dia": "", "feitos": 0, "em_curso": None, "parado": "",
         **(_todos().get(projeto) or {})}
    if e["dia"] != date.today().isoformat():
        e["feitos"] = 0  # o limite é por dia
    return {**e, "travas": travas(projeto)}


def travas(projeto: str) -> list[str]:
    """Por que não dá para ligar agora ([] = pode)."""
    motivos = []
    if sandbox.modo_isolado() == "desligado":
        motivos.append("Ligue o Sandbox isolado (Configurações › Geral): sem ele um comando do agente, sem ninguém "
                       "aprovando, roda com o disco inteiro.")
    elif not sandbox.motor_ativo():
        motivos.append("O sandbox isolado está ligado, mas nenhum Docker respondeu (nem o Desktop nem o do WSL).")
    if not gitops.is_repo(Path(projeto)):
        motivos.append("O projeto precisa ser um repositório git: cada card vira um commit revisável.")
    return motivos


def define(projeto: str, ligado: bool, por_dia: int | None = None) -> dict:
    if ligado and (t := travas(projeto)):
        raise board.BoardError(t[0])
    campos: dict = {"ligado": bool(ligado)}
    if por_dia is not None:
        campos["por_dia"] = max(1, min(int(por_dia), 50))
    if ligado:
        campos["parado"] = ""  # religar é o "pode seguir" depois de uma parada
    _grava(projeto, **campos)
    return estado(projeto)


def proximo(projeto: str) -> dict | None:
    """Card do Backlog a executar: mais severo primeiro (1 é alta), depois o mais antigo. Card de agente sem
    verify fica de fora: nada provaria que ficou pronto."""
    with db.session() as s:
        if s.scalar(select(db.Issue.id).where(db.Issue.projeto == projeto, db.Issue.status == "andamento")):
            return None  # um de cada vez: dois no mesmo repositório brigariam pelos mesmos arquivos
        for i in s.scalars(select(db.Issue).where(db.Issue.projeto == projeto, db.Issue.status == "backlog")
                           .order_by(db.Issue.severidade, db.Issue.id)):
            c = board._dict(i)
            if c["tipo"] == "seguranca":
                continue  # nunca sem aprovação
            if c["modo_sugerido"] == "agent" and not c["verify_sugerido"].strip():
                continue
            return c
    return None


def _escritos(conv_id: int) -> list[str]:
    with db.session() as s:
        return [str((m.meta or {}).get("arguments", {}).get("path") or "")
                for m in s.scalars(select(db.Message).where(db.Message.conversation_id == conv_id,
                                                            db.Message.role == "tool", db.Message.status == "ok",
                                                            db.Message.name.in_(ESCRITAS)))]


def _verify(historico: list[dict], desde: int) -> str | None:
    """'passou', 'falhou' ou None (ainda rodando), pelo evento que o board.acompanha grava."""
    for h in historico[desde:]:
        t = h.get("texto") or ""
        if t.startswith("verify passou"):
            return "passou"
        if t.startswith(("verify FALHOU", "verify não rodou")):
            return "falhou"
    return None


def _needs_human(conv_id: int) -> str:
    with db.session() as s:
        t = s.scalars(select(db.Task).where(db.Task.conversation_id == conv_id,
                                            db.Task.status == "needs_human")).first()
        return f"{t.code} ({t.blocked_reason or 'precisa de você'})" if t else ""


def _avisa(titulo: str, texto: str, conv_id: int | None) -> None:
    from . import mobile
    mobile.avisa(titulo, texto, conv_id)


def _finaliza(projeto: str, card: dict, resultado: str) -> None:
    """Agente com verify ok: commit dos arquivos que a conversa escreveu. Depois, push e próximo."""
    commit = ""
    root = Path(projeto)
    if resultado == "passou" and card["modo_sugerido"] == "agent" and card["conversa_id"]:
        try:
            commit = gitops.commit_paths(root, _escritos(card["conversa_id"]),
                                         f"board: card #{card['id']} — {card['titulo'][:120]}")
        except Exception as e:
            resultado = f"commit falhou: {e}"
    with db.session() as s:
        if i := s.get(db.Issue, card["id"]):
            if commit:
                i.commit = commit
            board._evento(i, f"execução automática: {'commit ' + commit if commit else resultado}")
            s.commit()
    if resultado == "passou":
        _grava(projeto, em_curso=None)
        _avisa("Card pronto para revisão", f"#{card['id']} {card['titulo'][:120]}", card["conversa_id"])
    else:
        _grava(projeto, em_curso=None, parado=f"card #{card['id']}: {resultado}")
        _avisa("Execução do backlog parou", f"#{card['id']} {card['titulo'][:100]}: {resultado}"[:180],
               card["conversa_id"])


async def tique() -> None:
    """Um passo por projeto ligado. Rápido no caminho comum (nada a fazer); verify e commit vão para thread."""
    for projeto, cfg in _todos().items():
        if not cfg.get("ligado") or cfg.get("parado"):
            continue
        if (cid := cfg.get("em_curso")) is not None:
            try:
                card = board.pega(int(cid))
            except board.BoardError:
                _grava(projeto, em_curso=None)
                continue
            if card["status"] == "andamento":
                if card["conversa_id"] and (nh := _needs_human(card["conversa_id"])):
                    await asyncio.to_thread(_finaliza, projeto, card, f"tarefa em needs_human: {nh}")
                continue
            if card["id"] in _FINALIZANDO:
                continue
            desde = int(cfg.get("hist_inicio") or 0)
            resultado = ("passou" if card["modo_sugerido"] == "maestro" else _verify(card["historico"], desde))
            if resultado is None:
                continue  # o verify do acompanha ainda está rodando
            _FINALIZANDO.add(card["id"])
            try:
                await asyncio.to_thread(_finaliza, projeto, card, resultado)
            finally:
                _FINALIZANDO.discard(card["id"])
            continue
        feitos = int(cfg.get("feitos") or 0) if cfg.get("dia") == date.today().isoformat() else 0
        if feitos >= int(cfg.get("por_dia") or POR_DIA) or not (card := proximo(projeto)):
            continue
        # docker info e git: em thread, e só quando há card para rodar (o tique roda a cada 4 s no laço)
        if await asyncio.to_thread(travas, projeto):
            continue
        try:
            board.iniciar(card["id"], card["modo_sugerido"], permissao="auto", quem=" automaticamente")
        except board.BoardError as err:
            _grava(projeto, parado=f"card #{card['id']}: {err}")
            continue
        _grava(projeto, em_curso=card["id"], dia=date.today().isoformat(), feitos=feitos + 1,
               hist_inicio=len(card["historico"]))

"""E15 parte C: o board executa o Backlog sozinho, com as travas."""
import asyncio
import time

import pytest

from app import board, board_auto, config, db, gitops, mobile, shell


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    projeto = board.projeto_de(str(tmp_path))
    disparos, avisos, commits = [], [], []
    monkeypatch.setattr(board, "_escolha", lambda modo: {"provider": "p", "model": "m", "permission": "manual",
                                                         "effort": "medio"})
    monkeypatch.setattr(board, "_dispara", lambda conv, kind, texto, esc: disparos.append((conv, kind, esc)))
    monkeypatch.setattr(board_auto, "travas", lambda p: [])
    monkeypatch.setattr(mobile, "avisa", lambda t, x, c=None: avisos.append((t, x)))
    monkeypatch.setattr(gitops, "commit_paths", lambda root, paths, msg: commits.append((paths, msg)) or "abc1234")
    monkeypatch.setattr(shell, "executa_do_projeto", lambda r, cmd, t=60: (0, "ok"))
    with db.session() as s:  # outro teste pode ter deixado o estado de um projeto ligado
        s.merge(db.AppSetting(key=board_auto.CHAVE, value={}))
        s.commit()
    return projeto, disparos, avisos, commits


def _tique():
    asyncio.run(board_auto.tique())


def _card(projeto, **d):
    c, _ = board.criar(projeto, {"titulo": d.pop("titulo", "Arrumar"), "verify_sugerido": "pytest -q", **d})
    return c


def _termina(card_id, escreveu="src/a.py"):
    """O agente respondeu e escreveu um arquivo; o acompanha leva para Revisão e roda o verify (thread)."""
    c = board.pega(card_id)
    with db.session() as s:
        s.add(db.Message(conversation_id=c["conversa_id"], role="tool", name="edit_file", status="ok", content="ok",
                         meta={"arguments": {"path": escreveu}}))
        s.add(db.Message(conversation_id=c["conversa_id"], role="assistant", content="feito"))
        s.commit()
    board.acompanha()
    for _ in range(50):
        if any(h["texto"].startswith("verify") for h in board.pega(card_id)["historico"]):
            return
        time.sleep(0.05)


def test_nao_liga_sem_sandbox(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "desligado")
    with pytest.raises(board.BoardError, match="Sandbox isolado"):
        board_auto.define(board.projeto_de(str(tmp_path)), True)


def test_card_em_novo_nunca_roda_e_seguranca_nem_sem_verify(proj):
    projeto, disparos, _, _ = proj
    _card(projeto, titulo="Triar", status="novo")
    _card(projeto, titulo="Vazamento", tipo="seguranca")
    _card(projeto, titulo="Sem prova", verify_sugerido="")
    board_auto.define(projeto, True)
    _tique()
    assert disparos == []


def test_um_por_vez_no_modo_automatico_com_commit_e_push(proj):
    projeto, disparos, avisos, commits = proj
    leve = _card(projeto, titulo="Leve", severidade=3)
    grave = _card(projeto, titulo="Grave", severidade=1)
    board_auto.define(projeto, True)
    _tique()
    _tique()  # o primeiro ainda roda: não dispara outro
    assert len(disparos) == 1 and disparos[0][2]["permission"] == "auto"
    assert board.pega(grave["id"])["status"] == "andamento"  # o mais severo primeiro
    _termina(grave["id"])
    _tique()
    atual = board.pega(grave["id"])
    assert atual["status"] == "revisao" and atual["commit"] == "abc1234"  # Revisão, nunca Concluído sozinho
    assert commits[0][0] == ["src/a.py"] and avisos[0][0] == "Card pronto para revisão"
    _tique()
    assert board.pega(leve["id"])["status"] == "andamento" and len(disparos) == 2


def test_limite_diario(proj):
    projeto, disparos, _, _ = proj
    a, _b = _card(projeto, titulo="A"), _card(projeto, titulo="B")
    board_auto.define(projeto, True, por_dia=1)
    _tique()
    _termina(a["id"])
    _tique()  # finaliza A
    _tique()  # B ficaria para amanhã
    assert len(disparos) == 1 and board_auto.estado(projeto)["feitos"] == 1


def test_verify_falhou_para_a_execucao(proj, monkeypatch):
    projeto, disparos, avisos, commits = proj
    monkeypatch.setattr(shell, "executa_do_projeto", lambda r, cmd, t=60: (1, "1 failed"))
    a, _b = _card(projeto, titulo="A"), _card(projeto, titulo="B")
    board_auto.define(projeto, True)
    _tique()
    _termina(a["id"])
    _tique()
    _tique()
    e = board_auto.estado(projeto)
    assert "falhou" in e["parado"] and len(disparos) == 1 and commits == []
    assert avisos[-1][0] == "Execução do backlog parou"
    board_auto.define(projeto, True)  # religar é o "pode seguir"
    _tique()
    assert len(disparos) == 2


def test_para_em_needs_human(proj, monkeypatch):
    projeto, disparos, avisos, _ = proj
    monkeypatch.setattr(board_auto, "_needs_human", lambda conv: "TASK-002 (teste não passa)")
    _card(projeto, titulo="Tela nova", tipo="feature")
    _card(projeto, titulo="Outra", tipo="feature")
    board_auto.define(projeto, True)
    _tique()
    _tique()  # tarefa em needs_human: para, e não começa a próxima
    _tique()
    assert len(disparos) == 1 and "needs_human" in board_auto.estado(projeto)["parado"]

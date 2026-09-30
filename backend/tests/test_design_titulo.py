"""Título e bolinha do Design como no Agente: resumo do modelo depois da 1ª resposta; run visível no activity."""
import asyncio
import json

from app import db, design, main
from tests.test_design_fase3 import M, PLANO, _esperar, _projeto, pastas  # noqa: F401


def test_titulo_vira_resumo_do_modelo(monkeypatch):
    monkeypatch.setattr(design, "RETITULAR", True)

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        yield "content", "Título: Landing da Padaria Sol." if "dá nome a conversas" in messages[0]["content"] else json.dumps(PLANO)
        yield "done", {}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)
    conv = _projeto(None)
    with db.session() as s:
        s.get(db.Conversation, conv).title = "Nova conversa"
        s.commit()
    _esperar(lambda: design.start(conv, "landing page de uma padaria artesanal com hero, cardápio e contato", M))
    with db.session() as s:
        assert s.get(db.Conversation, conv).title == "Landing da Padaria Sol"


def test_geracao_do_design_acende_a_bolinha():
    design._RUNS[999] = {"conv_id": 4242, "message_id": 999}
    try:
        a = asyncio.run(main.get_activity())
        assert any(c["id"] == 4242 and c["running"] for c in a["conversations"])
    finally:
        design._RUNS.pop(999, None)

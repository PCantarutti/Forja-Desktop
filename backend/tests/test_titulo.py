"""Título da conversa: a primeira mensagem segura o lugar, o modelo resume no fim do turno."""
import asyncio

from app import agent, db, llm


def test_clean_title_strips_the_noise_models_add():
    assert agent._clean_title('"Erro de login no checkout"') == "Erro de login no checkout"
    assert agent._clean_title("Título: Ajuste no cálculo.") == "Ajuste no cálculo"
    assert agent._clean_title("<think>pensando alto</think>\nMigração do banco") == "Migração do banco"
    assert agent._clean_title("## `Refatorar parser`") == "Refatorar parser"
    assert agent._clean_title("Primeira linha\nsegunda linha") == "Primeira linha"
    assert agent._clean_title("   ") == ""
    assert len(agent._clean_title("palavra " * 40)) <= agent.TITLE_MAX


def _conversa() -> int:
    with db.session() as s:
        c = db.Conversation(kind="agent")
        s.add(c)
        s.commit()
        return c.id


def _monkey(monkeypatch, titulo: str):
    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        if messages[0]["content"].startswith("Você dá nome a conversas"):
            assert "user: " in messages[1]["content"] and "assistant: " in messages[1]["content"]
            yield "content", titulo
        else:
            yield "content", "Respondido."
        yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)


def _roda(conv: int, texto: str) -> list[dict]:
    run = agent.Run(conv)
    req = agent.RunRequest(content=texto, provider="lmstudio", model="m", mode="agent", permission="manual")

    async def scenario():
        return [ev async for ev in agent.run_agent(conv, req, run)]

    return asyncio.run(scenario())


def test_model_replaces_the_first_message_as_title(monkeypatch):
    _monkey(monkeypatch, '"Erro 500 no login"')
    conv = _conversa()
    eventos = _roda(conv, "meu login volta erro 500 quando o token expira, me ajuda a achar a causa")
    assert {"type": "title", "title": "Erro 500 no login"} in eventos
    with db.session() as s:
        assert s.get(db.Conversation, conv).title == "Erro 500 no login"


def test_manual_rename_during_the_turn_wins(monkeypatch):
    _monkey(monkeypatch, "Título do modelo")
    conv = _conversa()

    async def scenario():
        run = agent.Run(conv)
        req = agent.RunRequest(content="primeira mensagem longa sobre alguma coisa", provider="lmstudio",
                               model="m", mode="agent", permission="manual")
        eventos = []
        async for ev in agent.run_agent(conv, req, run):
            eventos.append(ev)
            if ev["type"] == "assistant_end":  # usuário renomeia enquanto o turno roda
                with db.session() as s:
                    s.get(db.Conversation, conv).title = "Nome que eu escolhi"
                    s.commit()
        return eventos

    eventos = asyncio.run(scenario())
    assert not [e for e in eventos if e["type"] == "title"]
    with db.session() as s:
        assert s.get(db.Conversation, conv).title == "Nome que eu escolhi"


def test_short_first_message_is_already_the_title(monkeypatch):
    """Mensagem curta vira um título aceitável sozinha: nem chama o modelo."""
    _monkey(monkeypatch, "Título caro demais")
    conv = _conversa()
    eventos = _roda(conv, "roda os testes")
    assert not [e for e in eventos if e["type"] == "title"]
    with db.session() as s:
        assert s.get(db.Conversation, conv).title == "roda os testes"


def test_failure_keeps_the_provisional_title(monkeypatch):
    _monkey(monkeypatch, "   ")  # modelo devolve lixo: fica o que já estava
    conv = _conversa()
    eventos = _roda(conv, "conserta o parser de datas que quebra quando o mês vem abreviado em inglês")
    assert not [e for e in eventos if e["type"] == "title"]
    with db.session() as s:
        assert s.get(db.Conversation, conv).title.startswith("conserta o parser de datas")

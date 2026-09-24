"""Fase 3 do porte do DeepSeek Harness: system prompt fixo, contexto de execução à parte."""
import asyncio

from app import agent, compact, db, llm


def test_base_nao_depende_do_modo_nem_do_plano():
    base = agent.prompt_base("native")
    assert "MODO PLANO" not in base and "Plano aprovado" not in base
    assert base == agent.prompt_base("native")
    plano = agent.contexto_runtime("plan", None, False, ["delegate_task"])
    assert "MODO PLANO ATIVO" in plano and "Concordar na conversa não aprova" in plano
    aprovado = agent.contexto_runtime("edits", "# X\n1. y", False, [])
    assert "Aceitar edições" in aprovado and "1. y" in aprovado


def test_contexto_so_e_regravado_quando_muda(monkeypatch):
    prefixos = []

    async def stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        prefixos.append(messages[0]["content"])
        yield "content", "ok"
        yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)

    async def scenario():
        with db.session() as s:
            c = db.Conversation(kind="agent")
            s.add(c)
            s.commit()
            conv = c.id
        for texto, modo in (("a", "manual"), ("b", "manual"), ("c", "auto")):
            run = agent.Run(conv)
            req = agent.RunRequest(content=texto, provider="lmstudio", model="m", mode="agent", permission=modo)
            async for _ in agent.run_agent(conv, req, run):
                pass
        return conv

    conv = asyncio.run(scenario())
    with db.session() as s:
        ctxs = [m.content for m in s.get(db.Conversation, conv).messages if (m.meta or {}).get("kind") == "contexto"]
    assert len(ctxs) == 2 and "Automático" in ctxs[-1]  # manual (1x, repetido não regrava), depois auto
    assert len(set(prefixos)) == 1


def test_resumo_que_engole_o_contexto_o_repoe():
    msgs = [db.Message(id=1, role="user", content="oi"),
            db.Message(id=2, role="event", content="Contexto atual de execução. X",
                       meta={"kind": "contexto", "to_model": True}),
            db.Message(id=3, role="assistant", content="ok"),
            db.Message(id=4, role="event", content="resumo", meta={"kind": "summary", "covers_until": 3}),
            db.Message(id=5, role="user", content="e agora")]
    hist = agent.build_history(msgs, "native", contexto=True)
    junto = hist[1]["content"]  # mensagens de usuário seguidas são fundidas
    assert junto.index(compact.retomada("resumo")) < junto.index("Contexto atual de execução. X") < junto.index("e agora")


def test_avisa_troca_de_modo_e_de_modelo(monkeypatch):
    async def stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        yield "content", "ok"
        yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)

    async def scenario():
        with db.session() as s:
            c = db.Conversation(kind="agent")
            s.add(c)
            s.commit()
            conv = c.id
        for modelo, modo in (("m1", "manual"), ("m1", "manual"), ("m2", "auto")):
            run = agent.Run(conv)
            req = agent.RunRequest(content="x", provider="lmstudio", model=modelo, mode="agent", permission=modo)
            async for _ in agent.run_agent(conv, req, run):
                pass
        return conv

    conv = asyncio.run(scenario())
    with db.session() as s:
        avisos = [m.content for m in s.get(db.Conversation, conv).messages if (m.meta or {}).get("kind") == "mudanca"]
    assert any("[modelo trocado: as respostas acima foram geradas por m1" in a and "com m2]" in a for a in avisos)
    assert any("mudou de Manual para Automático" in a for a in avisos)
    assert len(avisos) == 2  # o 2º turno, igual ao 1º, não avisa nada

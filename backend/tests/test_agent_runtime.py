import asyncio

from app import compact
from app.agent import Run, build_history
from app.db import Message


def msg(id, role, content="", **kw):
    return Message(id=id, role=role, content=content, thinking="", **kw)


def conversation():
    return [
        msg(1, "user", "crie a.py"),
        msg(2, "assistant", "", tool_calls=[{"id": "c1", "name": "write_file", "arguments": {"path": "a.py"}}]),
        msg(3, "tool", "Arquivo criado", tool_call_id="c1", name="write_file", status="ok"),
        msg(4, "assistant", "Criei a.py."),
        msg(5, "user", "agora b.py"),
        msg(6, "assistant", "Feito b.py."),
        msg(7, "user", "e c.py"),
        msg(8, "assistant", "Feito c.py."),
    ]


# ------------------------------------------------ compactação

def test_split_point_keeps_last_two_turns():
    assert compact.split_point(conversation()) == 4  # resume até antes do penúltimo "user"


def test_split_point_nothing_to_do():
    assert compact.split_point(conversation()[:6]) is None  # só 2 turnos


def test_transcript_includes_tool_calls_and_results():
    t = compact.transcript(conversation(), until=4, max_chars=10_000)
    assert "USUÁRIO: crie a.py" in t and "chamou write_file" in t and "RESULTADO write_file [ok]" in t
    assert "b.py" not in t


def test_history_after_summary_starts_with_summary():
    msgs = conversation() + [msg(9, "event", "Resumo X", meta={"kind": "summary", "covers_until": 4})]
    hist = build_history(msgs, "native")
    assert hist[0]["role"] == "system"
    assert hist[1]["role"] == "user" and "Resumo X" in hist[1]["content"] and "agora b.py" in hist[1]["content"]
    assert all("crie a.py" not in m.get("content", "") for m in hist)
    assert not any(m["role"] == "tool" for m in hist)  # par tool_call/resultado saiu junto


def test_second_compaction_starts_after_previous_summary():
    msgs = conversation() + [msg(9, "event", "Resumo X", meta={"kind": "summary", "covers_until": 4}),
                             msg(10, "user", "d.py"), msg(11, "assistant", "ok")]
    assert compact.split_point(msgs) == 6
    t = compact.transcript(msgs, 6, 10_000)
    assert t.startswith("[Resumo anterior]\nResumo X") and "agora b.py" in t and "crie a.py" not in t


# ------------------------------------------------ execução desacoplada

def test_run_buffer_replay_and_snapshot():
    async def scenario():
        run = Run(conv_id=1)
        await run.publish({"type": "assistant_start"})
        await run.publish({"type": "token", "text": "Olá"})
        await run.publish({"type": "approval_request", "call": {"id": "c1"}, "preview": None})
        snap = run.snapshot()
        assert snap["cursor"] == 3 and snap["draft"]["content"] == "Olá" and snap["approvals"][0]["call"]["id"] == "c1"

        got = []

        async def reader():
            async for ev in run.subscribe(snap["cursor"]):  # reconexão: só o que vem depois
                got.append(ev["type"])

        task = asyncio.create_task(reader())
        await asyncio.sleep(0)
        await run.publish({"type": "tool_result", "message": {"tool_call_id": "c1"}})
        await run.publish({"type": "done"})
        async with run._changed:
            run.finished = True
            run._changed.notify_all()
        await asyncio.wait_for(task, 2)
        assert got == ["tool_result", "done"] and run.snapshot()["approvals"] == []

    asyncio.run(scenario())


# ------------------------------------------------ loop com LLM falso

def test_retry_and_shell_always_asks(monkeypatch):
    from app import agent, db, llm

    calls = {"n": 0}

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:  # conexão cai antes do 1º token
            raise llm.LLMError("Conexão interrompida.")
        if calls["n"] == 2:
            yield "done", {"tool_calls": [{"id": "c1", "name": "run_command", "arguments": {"command": "ls"}}],
                           "prompt_tokens": 10, "completion_tokens": 5}
        else:
            yield "content", "Pronto."
            yield "done", {"tool_calls": [], "prompt_tokens": 20, "completion_tokens": 2}

    async def fake_limit(*a):
        return 32768

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", fake_limit)
    monkeypatch.setattr(agent, "RETRY_DELAY", 0)

    async def scenario():
        with db.session() as s:
            c = db.Conversation()
            s.add(c)
            s.commit()
            conv_id = c.id
        run = agent.Run(conv_id)
        req = agent.RunRequest(content="liste", provider="lmstudio", model="m", mode="agent", permission="auto")
        types = []
        async for ev in agent.run_agent(conv_id, req, run):
            types.append(ev["type"])
            if ev["type"] == "approval_request":  # shell pediu aprovação mesmo em "automático"
                run.resolve("c1", False)
        return types

    types = asyncio.run(scenario())
    assert calls["n"] == 3
    assert "approval_request" in types and types[-1] == "done"


# ------------------------------------------------ compactação sem janela conhecida


def _conversa_grande(db, texto: str, turnos: int) -> int:
    with db.session() as s:
        c = db.Conversation(kind="agent")
        s.add(c)
        s.commit()
        for i in range(turnos):
            s.add(db.Message(conversation_id=c.id, role="user", content=f"{i} {texto}"))
            s.add(db.Message(conversation_id=c.id, role="assistant", content=f"{i} {texto}"))
        s.commit()
        return c.id


def test_compacta_mesmo_sem_o_provider_informar_a_janela(monkeypatch):
    """`context_limit` devolve None em todo provider OpenAI-compatível genérico.

    Com a condição antiga (`if ctx_max and ...`) a compactação nunca disparava nesses providers: o
    prompt crescia sem teto até o servidor recusar a requisição.
    """
    from app import agent, compact, config, db, llm

    resumos = []

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        if messages[0]["content"].startswith(compact.PROMPT[:40]):
            resumos.append(messages[1]["content"])
            yield "content", "Resumo do que veio antes."
        else:
            yield "content", "Respondido."
        yield "done", {"tool_calls": []}

    async def sem_janela(*a):
        return None  # é o que o provider devolve quando não sabe dizer

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", sem_janela)
    monkeypatch.setattr(llm, "capabilities", sem_janela)
    monkeypatch.setattr(config, "NUM_CTX", 2_000)

    conv = _conversa_grande(db, "palavra " * 200, turnos=12)
    run = agent.Run(conv)
    req = agent.RunRequest(content="e agora?", provider="lmstudio", model="m", mode="agent", permission="manual")

    async def scenario():
        return [ev async for ev in agent.run_agent(conv, req, run)]

    eventos = asyncio.run(scenario())
    resumo = [e for e in eventos
              if e.get("type") == "event" and (e["message"].get("meta") or {}).get("kind") == "summary"]
    assert resumos, "o modelo nem foi chamado para resumir"
    assert resumo, "a conversa estourou o teto e mesmo assim nada foi compactado"

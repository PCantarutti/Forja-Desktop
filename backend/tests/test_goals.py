"""Fase 6 do porte do DeepSeek Harness: goals em rodadas e workflow declarativo."""
import asyncio

import pytest

from app import agent, db, goals, llm, settings
from app.tools import ToolError, run_tool


def _conv() -> int:
    with db.session() as s:
        c = db.Conversation(kind="agent")
        s.add(c)
        s.commit()
        return c.id


def _falso(monkeypatch, fake_stream):
    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)


def _roda(conv, texto="vai"):
    async def scenario():
        run = agent.Run(conv)
        req = agent.RunRequest(content=texto, provider="lmstudio", model="m", mode="agent", permission="bypass")
        return [ev async for ev in agent.run_agent(conv, req, run)]

    return asyncio.run(scenario())


def test_goal_gira_rodadas_ate_ser_completada(monkeypatch):
    passo = {"n": 0}

    async def stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        passo["n"] += 1
        n = passo["n"]
        if n == 1:
            yield "done", {"tool_calls": [{"id": "g1", "name": "create_goal", "arguments": {"objective": "3 arquivos"}}]}
        elif n in (2, 3):
            yield "content", f"parte {n - 1} feita"   # sem chamadas: sem a goal, o turno acabaria aqui
            yield "done", {"tool_calls": []}
        elif n == 4:
            assert "<goal_round>" in str(messages[-1]["content"]) and "Rodada: 2/" in str(messages[-1]["content"])
            yield "done", {"tool_calls": [{"id": "g2", "name": "get_goal", "arguments": {}}]}
        elif n == 5:
            yield "done", {"tool_calls": [{"id": "g3", "name": "update_goal",
                                           "arguments": {"goal_id": gid(), "revision": 1, "action": "complete"}}]}
        else:
            assert any("<goal_complete>" in str(m["content"]) for m in messages if m["role"] == "tool")
            yield "content", "Pronto."
            yield "done", {"tool_calls": []}

    conv = _conv()
    gid = lambda: goals.atual(conv).id  # noqa: E731
    _falso(monkeypatch, stream)
    eventos = _roda(conv)
    rodadas = [e for e in eventos if e["type"] == "event" and e["message"]["meta"].get("kind") == "goal"]
    assert len(rodadas) == 2 and goals.atual(conv).status == "completa" and passo["n"] == 6


def test_bloqueio_so_depois_de_tres_rodadas_seguidas():
    conv = _conv()
    tok = goals.CONV.set(conv)
    try:
        run_tool("create_goal", {"objective": "x"})
        g = goals.atual(conv)
        rev = g.revision if hasattr(g, "revision") else g.revisao
        for i in range(1, 3):
            goals.proxima_rodada(conv)
            with pytest.raises(ToolError, match=f"esta é a {i}ª"):
                run_tool("update_goal", {"goal_id": g.id, "revision": rev, "action": "blocked", "blocked_reason": "sem chave"})
            rev += 1
        goals.proxima_rodada(conv)
        out = run_tool("update_goal", {"goal_id": g.id, "revision": rev, "action": "blocked", "blocked_reason": "sem chave"})
        assert "<goal_blocked>" in out and goals.atual(conv).status == "bloqueada"
        with pytest.raises(ToolError, match="revision desatualizada"):
            run_tool("update_goal", {"goal_id": g.id, "revision": 1, "action": "resume"})
    finally:
        goals.CONV.reset(tok)


def test_retomar_a_conversa_desarma_ate_resume():
    conv = _conv()
    tok = goals.CONV.set(conv)
    try:
        run_tool("create_goal", {"objective": "y"})
        goals.desarmar(conv)
        assert goals.proxima_rodada(conv) is None
        assert "update_goal action=resume" in goals.contexto(conv)
        g = goals.atual(conv)
        run_tool("update_goal", {"goal_id": g.id, "revision": g.revisao, "action": "resume"})
        assert "<goal_round>" in goals.proxima_rodada(conv)
    finally:
        goals.CONV.reset(tok)


def test_workflow_roda_fases_em_ordem_e_passa_o_relatorio_adiante(monkeypatch):
    settings.update({"subagents": {"rapido": {"provider": "lmstudio", "model": "mini"},
                                   "capaz": {"provider": "lmstudio", "model": "grande"}}})
    vistos = []

    async def stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        if model == "mini":
            pedido = next(str(m["content"]) for m in messages if m["role"] == "user")
            vistos.append(pedido)
            yield "content", "ACHADO-" + ("A" if "levantar" in pedido else "B")
            yield "done", {"tool_calls": []}
            return
        if not any(m["role"] == "tool" for m in messages):
            yield "done", {"tool_calls": [{"id": "w1", "name": "workflow", "arguments": {"phases": [
                {"name": "um", "agents": [{"name": "a", "task": "levantar"}, {"name": "b", "task": "levantar tb"}]},
                {"name": "dois", "agents": [{"name": "c", "task": "juntar {{um.a}} e {{um.b}}"}]}]}}]}
        else:
            yield "content", "ok"
            yield "done", {"tool_calls": []}

    _falso(monkeypatch, stream)
    eventos = _roda(_conv())
    res = next(e["message"] for e in eventos if e["type"] == "tool_result")
    assert res["status"] == "ok" and "## Fase um" in res["content"] and "### c [ok]" in res["content"]
    assert any("juntar" in v and "ACHADO-A" in v for v in vistos)  # o relatório da fase 1 chegou na 2


def test_workflow_valida_o_formato():
    with pytest.raises(ToolError, match="phases"):
        agent._fases({})
    with pytest.raises(ToolError, match="repetido"):
        agent._fases({"phases": [{"name": "x", "agents": [{"name": "a", "task": "t"}, {"name": "a", "task": "u"}]}]})

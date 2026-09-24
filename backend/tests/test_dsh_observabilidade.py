"""Segunda rodada do DeepSeek Harness: freios vistos na sessão do Qwen, medidor por tipo, cache e faixa da goal."""
import asyncio

from fastapi.testclient import TestClient

from app import agent, db, goals, llm


def _conv() -> int:
    with db.session() as s:
        c = db.Conversation(kind="agent")
        s.add(c)
        s.commit()
        return c.id


def _roda(monkeypatch, stream, conv=None):
    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)
    conv = conv or _conv()

    async def scenario():
        run = agent.Run(conv)
        req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="agent", permission="bypass")
        return [ev async for ev in agent.run_agent(conv, req, run)]

    return conv, asyncio.run(scenario())


def test_lista_de_tarefas_aberta_segura_o_fim(monkeypatch):
    n = {"i": 0}

    async def stream(*a, **kw):
        n["i"] += 1
        if n["i"] == 1:
            yield "done", {"tool_calls": [{"id": "t", "name": "update_tasks", "arguments": {"tasks": [
                {"text": "codar", "status": "done"}, {"text": "testar no navegador", "status": "doing"}]}}]}
        else:
            yield "content", "pronto"
            yield "done", {"tool_calls": []}

    _, eventos = _roda(monkeypatch, stream)
    nudges = [e["message"]["content"] for e in eventos if e["type"] == "event" and e["message"]["meta"].get("kind") == "nudge"]
    assert len(nudges) == 1 and "testar no navegador" in nudges[0]
    assert n["i"] == 3  # um lembrete só; depois deixa acabar


def test_reescrever_o_mesmo_arquivo_varias_vezes_avisa(monkeypatch, tmp_path):
    n = {"i": 0}

    async def stream(*a, **kw):
        n["i"] += 1
        if n["i"] <= agent.MAX_REESCRITAS:
            yield "done", {"tool_calls": [{"id": f"w{n['i']}", "name": "write_file",
                                           "arguments": {"path": "modal.tsx", "content": f"v{n['i']}"}}]}
        else:
            yield "content", "ok"
            yield "done", {"tool_calls": []}

    from app import workspace
    tok = workspace.CURRENT.set(tmp_path)
    try:
        with db.session() as s:
            c = db.Conversation(kind="agent", workspace=str(tmp_path))
            s.add(c)
            s.commit()
        _, eventos = _roda(monkeypatch, stream, c.id)
    finally:
        workspace.CURRENT.reset(tok)
    resultados = [e["message"]["content"] for e in eventos if e["type"] == "tool_result"]
    assert "Pare e reavalie" in resultados[agent.MAX_REESCRITAS - 1]
    assert all("Pare e reavalie" not in r for r in resultados[:agent.MAX_REESCRITAS - 1])


def test_contexto_por_tipo_e_cache_vao_nas_estatisticas(monkeypatch):
    async def stream(*a, **kw):
        yield "content", "oi"
        yield "done", {"tool_calls": [], "prompt_tokens": 1000, "completion_tokens": 5, "cached_tokens": 900}

    conv, eventos = _roda(monkeypatch, stream)
    ctx = next(e for e in eventos if e["type"] == "context")
    assert set(ctx["partes"]) == {"sistema", "ferramentas", "mensagens"} and ctx["partes"]["ferramentas"] > 0
    stats = next(e["message"]["meta"]["stats"] for e in eventos if e["type"] == "assistant_end")
    assert stats["cached"] == 900 and stats["partes"] == ctx["partes"]


def test_faixa_da_goal_pausa_retoma_e_descarta():
    from app.main import app

    conv = _conv()
    tok = goals.CONV.set(conv)
    try:
        goals.create_goal(None, {"objective": "fazer o app"})
    finally:
        goals.CONV.reset(tok)
    from app import config

    c = TestClient(app, headers={"x-forja-token": config.API_TOKEN or ""})
    assert c.get(f"/api/conversations/{conv}/goal").json()["goal"]["objective"] == "fazer o app"
    assert c.post(f"/api/conversations/{conv}/goal", json={"action": "pause"}).json()["goal"]["status"] == "pausada"
    assert c.post(f"/api/conversations/{conv}/goal", json={"action": "resume"}).json()["goal"]["armada"]
    goals.desarmar(conv)  # retomada pela tela: o próximo turno do usuário não desarma
    assert goals.atual(conv).armada
    assert c.post(f"/api/conversations/{conv}/goal", json={"action": "clear"}).json()["goal"] is None

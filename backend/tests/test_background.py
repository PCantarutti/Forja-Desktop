"""Processo em background: esperar o fim numa chamada só, sem o freio de loop atrapalhar."""
import asyncio
import time

from app import agent, db, llm, shell
from app.tools import REGISTRY


def test_serve_status_is_a_polling_tool():
    """Acompanhar é repetir a mesma chamada: o que muda é o resultado, então fica fora do freio."""
    assert REGISTRY["serve_status"].poll
    assert not REGISTRY["read_file"].poll
    assert agent._poll({"name": "serve_status", "arguments": {}})
    assert not agent._poll({"name": "read_file", "arguments": {}})
    assert not agent._poll({"name": "nao_existe", "arguments": {}})


def test_wait_returns_as_soon_as_the_process_ends(monkeypatch, tmp_path):
    """wait segura a chamada até sair, e volta na hora quando o processo já morreu."""
    estados = [[{"name": "build", "alive": True}], [{"name": "build", "alive": True}],
               [{"name": "build", "alive": False, "exit_code": 0}]]
    monkeypatch.setattr(shell, "list_servers", lambda: estados.pop(0) if len(estados) > 1 else estados[0])
    monkeypatch.setattr(shell.time, "sleep", lambda *_: None)  # sem esperar de verdade no teste
    monkeypatch.setattr(shell, "server_log", lambda name, tail: "0\n1\n2")

    t0 = time.monotonic()
    out = shell.serve_status(tmp_path, {"name": "build", "wait": 60})
    assert time.monotonic() - t0 < 2
    assert not estados[0][0]["alive"] and "build" in out and "0\n1\n2" in out


def test_polling_the_same_process_is_not_a_loop(monkeypatch):
    """O bug: acompanhar um processo três vezes derrubava o turno por 'loop detectado'."""
    step = {"n": 0}
    call = {"id": "s1", "name": "serve_status", "arguments": {"name": "build", "wait": 1}}

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None):
        step["n"] += 1
        if step["n"] <= 4:  # quatro consultas seguidas, argumentos idênticos
            yield "done", {"tool_calls": [{**call, "id": f"s{step['n']}"}]}
        else:
            yield "content", "Terminou."
            yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)
    monkeypatch.setattr(shell, "list_servers", lambda: [])  # nada rodando: responde na hora

    async def scenario():
        with db.session() as s:
            c = db.Conversation(kind="agent")
            s.add(c)
            s.commit()
            conv = c.id
        run = agent.Run(conv)
        req = agent.RunRequest(content="acompanhe", provider="lmstudio", model="m", mode="agent",
                               permission="bypass")
        return [ev async for ev in agent.run_agent(conv, req, run)]

    eventos = asyncio.run(scenario())
    avisos = [e for e in eventos if e.get("type") == "event" and "Loop detectado" in str(e)]
    assert not avisos, "serve_status repetida não pode ser tratada como loop"
    assert step["n"] == 5  # o turno seguiu até o modelo responder

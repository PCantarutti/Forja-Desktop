"""Fase 5 do porte do DeepSeek Harness: subagente em segundo plano e aviso de término."""
import asyncio

from app import agent, config, db, llm, settings


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


def test_delegacao_em_fundo_devolve_na_hora_e_o_relatorio_chega_como_aviso(monkeypatch):
    settings.update({"subagents": {"rapido": {"provider": "lmstudio", "model": "mini"},
                                   "capaz": {"provider": "lmstudio", "model": "grande"}}})
    passos = {"main": 0}
    vistos = []

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        if model == "mini":
            await asyncio.sleep(0.3)
            yield "content", "Achei 3 arquivos."
            yield "done", {"tool_calls": []}
            return
        passos["main"] += 1
        vistos.append([str(m.get("content")) for m in messages])
        if passos["main"] == 1:
            yield "done", {"tool_calls": [{"id": "d1", "name": "delegate_task",
                                           "arguments": {"task": "conte os arquivos", "level": "rapido"}}]}
        elif passos["main"] == 2:
            yield "content", "Enquanto isso, nada mais a fazer."  # sem chamadas: o turno espera o filho
            yield "done", {"tool_calls": []}
        else:
            yield "content", "O subagente achou 3 arquivos."
            yield "done", {"tool_calls": []}

    _falso(monkeypatch, fake_stream)

    async def scenario():
        conv = _conv()
        run = agent.Run(conv)
        req = agent.RunRequest(content="delegue", provider="lmstudio", model="main", mode="agent", permission="bypass")
        return [ev async for ev in agent.run_agent(conv, req, run)], run

    eventos, run = asyncio.run(scenario())
    resultado = next(e["message"] for e in eventos if e["type"] == "tool_result")
    assert "iniciado em segundo plano" in resultado["content"]
    assert passos["main"] == 3  # o terceiro passo só existe porque o aviso acordou o turno
    assert any("Achei 3 arquivos." in c for c in vistos[2])
    avisos = [e["message"] for e in eventos if e["type"] == "event" and e["message"]["meta"].get("kind") == "aviso"]
    assert avisos and "Subagente 'd1' em segundo plano terminou" in avisos[0]["content"]
    assert all(f["task"].done() for f in run.filhos.values())


def test_local_que_trocaria_o_modelo_roda_em_primeiro_plano(monkeypatch):
    monkeypatch.setitem(config.PROVIDERS, "local", {"type": "llamacpp", "url": "http://127.0.0.1:1/v1"})
    settings.update({"subagents": {"rapido": {"provider": "local", "model": "outro"}}})
    req = agent.RunRequest(content="x", provider="local", model="principal")
    assert not agent._em_fundo({"arguments": {"task": "t"}}, req)
    assert agent._em_fundo({"arguments": {"task": "t"}}, agent.RunRequest(content="x", provider="local", model="outro"))
    assert not agent._em_fundo({"arguments": {"task": "t", "run_in_background": False}},
                               agent.RunRequest(content="x", provider="local", model="outro"))


def test_aviso_com_a_conversa_parada_abre_turno_novo(monkeypatch):
    abertos = []

    class RunFalso(agent.Run):
        def start(self, req):
            abertos.append(req)
            self.finished = True

    monkeypatch.setattr(agent, "Run", RunFalso)
    req = agent.RunRequest(content="antes", provider="lmstudio", model="m", mode="agent", permission="auto")
    agent.acordar(999_999, "O processo 'build' terminou", req)
    assert abertos and abertos[0].content.startswith("[Aviso automático do Forja] O processo 'build'")
    assert abertos[0].permission == "auto"

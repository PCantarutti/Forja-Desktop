"""Ferramentas que o modelo pede juntas rodam juntas; o histórico continua na ordem em que ele pediu."""
import asyncio
import time

from app import agent, config, db, llm, subagents
from app.tools import REGISTRY, Tool, register


def _call(i: int, name: str = "read_file", **args) -> dict:
    return {"id": f"c{i}", "name": name, "arguments": args or {"path": f"a{i}.txt"}}


def test_batches_groups_reads_and_isolates_the_rest():
    calls = [_call(1), _call(2), _call(3, "write_file", path="x", content="y"), _call(4), _call(5, "run_command")]
    nomes = [[c["name"] for c in lote] for lote in agent.batches(calls)]
    assert nomes == [["read_file", "read_file"], ["write_file"], ["read_file"], ["run_command"]]
    assert agent.batches([]) == []


def test_ask_user_and_plan_never_run_in_parallel():
    calls = [_call(1, "ask_user", questions=[]), _call(2, "exit_plan_mode", plan="x")]
    assert agent.batches(calls) == [[calls[0]], [calls[1]]]


def test_local_subagents_queue_while_remote_ones_share_two_slots(monkeypatch):
    """Duas delegações no mesmo modelo local brigariam pela GPU: ali a vaga é uma só."""
    monkeypatch.setattr(config, "PROVIDERS", {"local": {"type": "llamacpp"}, "nuvem": {"type": "openai"}})
    monkeypatch.setattr(subagents, "slot",
                        lambda lvl: {"provider": "local" if lvl == "rapido" else "nuvem", "model": "m"})
    agent._SEMS.clear()

    async def scenario():
        local = agent._limite(_call(1, "delegate_task", level="rapido"))
        remoto = agent._limite(_call(2, "delegate_task", level="capaz"))
        leitura = agent._limite(_call(3))
        return local._value, remoto._value, leitura._value

    assert asyncio.run(scenario()) == (1, agent.PARALLEL_SUBAGENTS, agent.PARALLEL_READS)
    agent._SEMS.clear()


def test_three_reads_run_at_once_and_are_saved_in_call_order(monkeypatch):
    lentas = []

    def devagar(root, args):
        lentas.append(args["path"])
        time.sleep(0.3)  # a ferramenta roda em thread (execute usa to_thread), então o sleep é bloqueante mesmo
        return f"conteudo de {args['path']}"

    # ferramenta própria: trocar a read_file no REGISTRY vazaria para os outros testes
    register(Tool("ler_devagar", "lê", {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
                  devagar))
    monkeypatch.setattr(agent, "PARALLEL_OK", agent.PARALLEL_OK | {"ler_devagar"})
    step = {"n": 0}

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        step["n"] += 1
        if step["n"] == 1:
            yield "done", {"tool_calls": [_call(1, "ler_devagar"), _call(2, "ler_devagar"),
                                          _call(3, "ler_devagar")]}
        else:
            yield "content", "Li tudo."
            yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)

    async def scenario():
        with db.session() as s:
            c = db.Conversation(kind="agent")
            s.add(c)
            s.commit()
            conv = c.id
        run = agent.Run(conv)
        req = agent.RunRequest(content="leia os três", provider="lmstudio", model="m", mode="agent",
                               permission="bypass")
        t0 = time.monotonic()
        async for _ in agent.run_agent(conv, req, run):
            pass
        return time.monotonic() - t0, conv

    demorou, conv = asyncio.run(scenario())
    assert len(lentas) == 3
    assert demorou < 0.75, f"as três leituras não rodaram juntas ({demorou:.2f}s para 3x0,3s)"
    with db.session() as s:
        msgs = [m for m in s.get(db.Conversation, conv).messages if m.role == "tool"]
    assert [m.tool_call_id for m in msgs] == ["c1", "c2", "c3"]  # ordem do pedido, não da chegada
    REGISTRY.pop("ler_devagar", None)

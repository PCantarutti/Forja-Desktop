"""E0: o registro de métricas (FORJA_METRICAS) que o bench do Maestro lê."""
import asyncio
import json

from app import agent, db, llm, main, metricas, workspace  # noqa: F401  (main registra as ferramentas)


def test_desligado_nao_escreve(tmp_path, monkeypatch):
    monkeypatch.delenv("FORJA_METRICAS", raising=False)
    metricas.registra("llm", x=1)
    assert not list(tmp_path.iterdir())


def test_volta_do_agente_vira_linha_com_os_timings(tmp_path, monkeypatch):
    arq = tmp_path / "m.jsonl"
    monkeypatch.setenv("FORJA_METRICAS", str(arq))

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        yield "content", "ok"
        yield "done", {"tool_calls": [], "prompt_tokens": 120, "completion_tokens": 3, "cached_tokens": 100,
                       "timings": {"cache_n": 100, "prompt_n": 20, "prompt_ms": 12.5, "predicted_ms": 40.0}}

    async def nada(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake)
    monkeypatch.setattr(llm, "context_limit", nada)
    monkeypatch.setattr(llm, "capabilities", nada)

    async def cenario():
        with db.session() as s:
            c = db.Conversation(kind="agent", workspace=str(tmp_path))
            s.add(c)
            s.commit()
            conv = c.id
        tok = workspace.CURRENT.set(tmp_path)
        try:
            run = agent.Run(conv)
            req = agent.RunRequest(content="oi", provider="lmstudio", model="m", mode="agent", permission="manual")
            return [ev async for ev in agent.run_agent(conv, req, run)], conv
        finally:
            workspace.CURRENT.reset(tok)

    _, conv = asyncio.run(cenario())
    linhas = [json.loads(ln) for ln in arq.read_text(encoding="utf-8").splitlines()]
    volta = next(ln for ln in linhas if ln["tipo"] == "llm")
    assert volta["papel"] == "agente" and volta["conv"] == conv and volta["model"] == "m"
    assert volta["timings"]["cache_n"] == 100 and volta["timings"]["prompt_n"] == 20

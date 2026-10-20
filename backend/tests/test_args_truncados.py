"""Chamada com JSON cortado não volta inteira ao modelo; o teto de geração chega ao corpo (conversa 207)."""
import json

from app import llm
from app.agent import build_history


class M:
    def __init__(self, **kw):
        self.id, self.thinking, self.meta, self.content = kw.pop("id"), None, {}, kw.pop("content", "")
        self.tool_calls = self.tool_call_id = self.name = self.status = None
        for k, v in kw.items():
            setattr(self, k, v)


def test_argumento_truncado_fica_curto_no_historico():
    raw = '{"path": "a.py", "content": "' + "x" * 300_000
    msgs = [M(id=1, role="user", content="traduza"),
            M(id=2, role="assistant", tool_calls=[{"id": "c1", "name": "write_file", "arguments": {"__raw__": raw}}]),
            M(id=3, role="tool", tool_call_id="c1", name="write_file", status="erro", content="JSON inválido")]
    for via in ("native", "text"):
        out = json.dumps(build_history(msgs, via), ensure_ascii=False)
        assert len(out) < 50_000 and "_omitido" in out, via
    ok = {"id": "c2", "name": "read_file", "arguments": {"path": "a.py"}}
    assert llm.args_historico(ok) == {"path": "a.py"}


def test_max_tokens_entra_no_corpo(monkeypatch):
    visto = {}

    async def falso(provider, model, messages, tools, num_ctx, extra=None):
        visto.update(extra or {})
        yield "done", {}

    monkeypatch.setattr(llm, "spec", lambda p: {"type": "llamacpp", "url": "http://x"})
    monkeypatch.setattr(llm, "_openai_stream", falso)

    async def _r(*a, **k):
        return None
    monkeypatch.setattr(llm, "_reasoning", _r)
    monkeypatch.setattr(llm, "_inference", lambda p, m, e: e.update(max_tokens=500))

    import asyncio

    async def roda(cap):
        visto.clear()
        async for _ in llm.chat_stream("local", "m", [], None, 8192, max_tokens=cap):
            pass
        return visto.get("max_tokens")
    assert asyncio.run(roda(100)) == 100   # o teto da janela corta o ajuste do modelo
    assert asyncio.run(roda(9000)) == 500  # o ajuste menor do modelo continua valendo

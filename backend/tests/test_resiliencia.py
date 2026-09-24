"""Fase 2 do porte do DeepSeek Harness: retry, loop em degraus, poda antes do resumo, validação."""
import asyncio

import pytest

from app import agent, compact, db, llm
from app.parsing import LoopDetector, aviso_repeticao
from app.tools import REGISTRY, ToolError, validar


def _conversa() -> int:
    with db.session() as s:
        c = db.Conversation()
        s.add(c)
        s.commit()
        return c.id


def _roda(monkeypatch, fake_stream, content="oi"):
    async def fake_limit(*a):
        return 32768

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", fake_limit)
    monkeypatch.setattr(agent, "RETRY_DELAY", 0)

    async def scenario():
        conv_id = _conversa()
        run = agent.Run(conv_id)
        req = agent.RunRequest(content=content, provider="lmstudio", model="m", mode="agent", permission="bypass")
        return [ev async for ev in agent.run_agent(conv_id, req, run)]

    return asyncio.run(scenario())


def _textos(eventos, kind=None):
    return [e["message"]["content"] for e in eventos
            if e["type"] == "event" and (kind is None or e["message"]["meta"].get("kind") == kind)]


def test_degraus_do_lembrete_de_repeticao():
    d = LoopDetector()
    ns = [d.conta("x", {"a": 1}) for _ in range(10)]
    assert ns == list(range(1, 11))
    assert "repetindo exatamente" in aviso_repeticao("x", {}, 3)
    assert "chamadas seguidas: 5" in aviso_repeticao("x", {}, 5)
    assert aviso_repeticao("x", {}, 4) == "" and aviso_repeticao("x", {}, 9) == ""


def test_repeticao_lembra_antes_de_parar(monkeypatch):
    n = {"i": 0}

    async def stream(*a, **kw):
        n["i"] += 1
        yield "done", {"tool_calls": [{"id": f"c{n['i']}", "name": "list_dir", "arguments": {"path": "."}}],
                       "prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(agent.config, "MAX_ITERATIONS", 50)
    eventos = _roda(monkeypatch, stream)
    nudges = _textos(eventos, "nudge")
    assert len(nudges) == 3  # 3ª, 5ª e 8ª
    assert any("Loop detectado: list_dir pedida 10 vezes" in t for t in _textos(eventos, "warning"))


def test_erro_transitorio_tenta_de_novo_ate_dar_certo(monkeypatch):
    n = {"i": 0}

    async def stream(*a, **kw):
        n["i"] += 1
        if n["i"] <= 3:
            raise llm.LLMError("HTTP 503", 503)
        yield "content", "Pronto."
        yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    eventos = _roda(monkeypatch, stream)
    assert n["i"] == 4
    assert not _textos(eventos, "error")


def test_erro_de_cliente_nao_repete(monkeypatch):
    n = {"i": 0}

    async def stream(*a, **kw):
        n["i"] += 1
        raise llm.LLMError("HTTP 401 chave inválida", 401)
        yield  # pragma: no cover

    eventos = _roda(monkeypatch, stream)
    assert n["i"] == 1 and _textos(eventos, "error")


def test_resposta_vazia_tenta_de_novo(monkeypatch):
    n = {"i": 0}

    async def stream(*a, **kw):
        n["i"] += 1
        if n["i"] == 2:
            yield "content", "Agora sim."
        yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    _roda(monkeypatch, stream)
    assert n["i"] == 2


def test_contexto_estourado_compacta_e_tenta_de_novo(monkeypatch):
    n = {"i": 0}
    compactou = {"sim": False}

    async def fake_compact(conv_id, msgs, req, teto):
        compactou["sim"] = True
        return
        yield  # pragma: no cover

    async def stream(*a, **kw):
        n["i"] += 1
        if n["i"] == 1:
            raise llm.LLMError("request exceeds the available context size", 400)
        yield "content", "ok"
        yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(agent, "_compact", fake_compact)
    _roda(monkeypatch, stream)
    assert compactou["sim"] and n["i"] == 2


def test_poda_mantem_os_ultimos_resultados_inteiros():
    grande = "A" * 5000 + "MEIO" + "Z" * 5000
    assert "[... meio do resultado podado ...]" in compact.podar(grande)
    assert compact.podar("curto") == "curto"
    msgs = [db.Message(id=1, role="user", content="oi")]
    for i in range(2, 8):
        msgs.append(db.Message(id=i, role="assistant", content="", tool_calls=[
            {"id": f"t{i}", "name": "read_file", "arguments": {}}]))
        msgs.append(db.Message(id=100 + i, role="tool", tool_call_id=f"t{i}", name="read_file",
                               status="ok", content=grande))
    hist = agent.build_history(msgs, "native", podar=True)
    tools = [m["content"] for m in hist if m["role"] == "tool"]
    assert sum("podado" in t for t in tools) == len(tools) - compact.PODA_MANTEM
    assert "MEIO" in tools[-1]


def test_retomada_do_resumo_usa_checkpoint():
    assert "<resumo-compactado>\nX\n</resumo-compactado>" in compact.retomada("X")
    assert "## Pendências" in compact.PROMPT


def test_validar_obrigatorio_e_tipo():
    t = REGISTRY["read_file"]
    with pytest.raises(ToolError, match="falta 'path'"):
        validar(t, {})
    with pytest.raises(ToolError, match="'start_line' deveria ser integer"):
        validar(t, {"path": "a", "start_line": "x"})
    with pytest.raises(ToolError, match="deveria ser integer"):
        validar(t, {"path": "a", "start_line": True})
    validar(t, {"path": "a", "start_line": 3})


def test_retry_after_do_provedor_vale_ate_o_teto(monkeypatch):
    from app.llm import _raise_for

    with pytest.raises(llm.LLMError) as e:
        _raise_for("p", 429, b"lento", {"retry-after": "7"})
    assert e.value.retry_after == 7.0
    with pytest.raises(llm.LLMError) as e:
        _raise_for("p", 429, b"x", {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert e.value.retry_after is None

    esperas = []

    async def dorme(s):
        esperas.append(s)

    n = {"i": 0}

    async def stream(*a, **kw):
        n["i"] += 1
        if n["i"] == 1:
            raise llm.LLMError("HTTP 429", 429, 60)
        yield "content", "ok"
        yield "done", {"tool_calls": []}

    monkeypatch.setattr(agent.asyncio, "sleep", dorme)
    _roda(monkeypatch, stream)
    assert esperas and esperas[0] == agent.RETRY_MAX  # pediu 60s, o teto é 10s

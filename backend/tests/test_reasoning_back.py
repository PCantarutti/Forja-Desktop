"""O raciocínio dos passos do turno atual volta ao modelo; o de turnos antigos não; Ollama chama `thinking`."""
from app.agent import build_history
from app.db import Message
from app.llm import _to_ollama


def _m(id, role, content="", **kw):
    return Message(id=id, role=role, content=content, **kw)


CALL = [{"id": "c1", "name": "browser_navigate", "arguments": {"url": "http://x"}}]


def _msgs():
    return [
        _m(1, "user", "faz o jogo"),
        _m(2, "assistant", "", thinking="turno antigo", tool_calls=CALL),
        _m(3, "tool", "ok", name="browser_navigate", status="ok", tool_call_id="c1"),
        _m(4, "assistant", "pronto", thinking="turno antigo, fim"),
        _m(5, "user", "aumenta a tela"),
        _m(6, "assistant", "", thinking="o usuário quer a tela maior", tool_calls=CALL),
        _m(7, "tool", "ok", name="browser_navigate", status="ok", tool_call_id="c1"),
    ]


def test_raciocinio_do_turno_atual_volta_e_o_antigo_nao():
    hist = build_history(_msgs(), "native", reasoning_back=True)
    assistentes = [m for m in hist if m["role"] == "assistant"]
    assert "reasoning_content" not in assistentes[0]  # turno antigo (id 2)
    assert assistentes[-1]["reasoning_content"] == "o usuário quer a tela maior"  # id 6, depois do user 5


def test_sem_reasoning_back_nada_muda():
    hist = build_history(_msgs(), "native")
    assert all("reasoning_content" not in m for m in hist)


def test_ollama_renomeia_para_thinking():
    out = _to_ollama([{"role": "assistant", "content": "", "reasoning_content": "pensei", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]}])
    assert out[0]["thinking"] == "pensei" and "reasoning_content" not in out[0]

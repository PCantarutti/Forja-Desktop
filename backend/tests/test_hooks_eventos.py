"""Fase 7 do porte do DeepSeek Harness: hooks pre_tool, user_prompt, session_start e stop."""
import asyncio
import json
import sys

import pytest

from app import agent, config, db, hooks, llm, workspace


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_HOOKS", [str(tmp_path)])
    tok = workspace.CURRENT.set(tmp_path)
    yield tmp_path
    workspace.CURRENT.reset(tok)


def _hooks(pasta, dados):
    (pasta / ".forja").mkdir(exist_ok=True)
    (pasta / ".forja" / "hooks.json").write_text(json.dumps(dados), encoding="utf-8")


def _sai(codigo: int, texto: str = "") -> str:
    py = sys.executable.replace("\\", "/")
    return f'& "{py}" -c "print(\'{texto}\'); raise SystemExit({codigo})"' if sys.platform == "win32" else \
        f'"{py}" -c "print(\'{texto}\'); raise SystemExit({codigo})"'


def test_pre_tool_nega_pergunta_ou_segue(pasta):
    _hooks(pasta, {"pre_tool": [{"tools": ["run_command"], "command": _sai(2, "proibido")},
                                {"tools": ["write_file"], "command": _sai(3, "confira")}]})
    assert hooks.pre_tool("run_command", {"command": "ls"}, pasta) == ("nega", "proibido")
    assert hooks.pre_tool("write_file", {"path": "a"}, pasta) == ("pergunta", "confira")
    assert hooks.pre_tool("read_file", {"path": "a"}, pasta) == ("segue", "")


def test_pasta_nao_confiavel_nao_roda_nada(pasta, monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_HOOKS", [])
    _hooks(pasta, {"pre_tool": [{"command": _sai(2, "x")}]})
    assert hooks.pre_tool("run_command", {}, pasta) == ("segue", "")


def _conv(pasta) -> int:
    with db.session() as s:
        c = db.Conversation(kind="agent", workspace=str(pasta))
        s.add(c)
        s.commit()
        return c.id


def test_hooks_no_loop(pasta, monkeypatch):
    marca = pasta / "parou.txt"
    py = sys.executable.replace("\\", "/")
    # stop: bloqueia só na primeira vez (cria a marca), depois deixa acabar
    stop = (f'& "{py}" -c "import pathlib,sys; p=pathlib.Path(r\'{marca}\'); '
            f'sys.exit(0) if p.exists() else (p.write_text(\'x\'), print(\'faltam testes\'), sys.exit(2))"')
    if sys.platform != "win32":
        stop = stop[2:]
    _hooks(pasta, {"session_start": [{"command": _sai(0, "bem-vindo")}],
                   "user_prompt": [{"command": _sai(0, "lembre do lint")}],
                   "pre_tool": [{"tools": ["write_file"], "command": _sai(2, "nada de escrever")}],
                   "stop": [{"command": stop}]})
    passo = {"n": 0}

    async def stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        passo["n"] += 1
        if passo["n"] == 1:
            assert any("bem-vindo" in str(m["content"]) and "lembre do lint" in str(m["content"]) for m in messages)
            yield "done", {"tool_calls": [{"id": "w", "name": "write_file", "arguments": {"path": "x.txt", "content": "1"}}]}
        else:
            yield "content", "fim"
            yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)

    async def scenario():
        conv = _conv(pasta)
        run = agent.Run(conv)
        req = agent.RunRequest(content="oi", provider="lmstudio", model="m", mode="agent", permission="bypass")
        return [ev async for ev in agent.run_agent(conv, req, run)]

    eventos = asyncio.run(scenario())
    res = next(e["message"] for e in eventos if e["type"] == "tool_result")
    assert res["status"] == "erro" and "nada de escrever" in res["content"] and not (pasta / "x.txt").exists()
    textos = [e["message"]["content"] for e in eventos if e["type"] == "event"]
    assert any("faltam testes" in t for t in textos)
    assert passo["n"] == 3  # o hook stop segurou o fim uma vez

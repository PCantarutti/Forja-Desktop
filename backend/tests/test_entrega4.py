import asyncio
from pathlib import Path

import pytest

from app import agent, checkpoints, config, db, llm, settings, subagents, workspace
from app.tools import ToolError, active, resolve_path


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    """Pasta de conversa de verdade (rodando nativo, o caminho da conversa é o caminho do disco)."""
    folder = tmp_path / "Users" / "pedro" / "app"
    folder.mkdir(parents=True)
    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path / "padrao")
    (tmp_path / "padrao").mkdir()
    settings.reset()
    yield folder
    settings.reset()
    workspace.CURRENT.set(None)


# ------------------------------------------------ pastas de trabalho

@pytest.mark.parametrize("raw,expected", [
    ("C:\\Users\\pedro\\app\\", "C:/Users/pedro/app"),
    ("c:/Users/pedro/app", "C:/Users/pedro/app"),
    ("C:", "C:/"),
])
def test_normalize(raw, expected):
    assert workspace.normalize(raw) == expected


@pytest.mark.parametrize("bad", ["Users/pedro", "relativo/x", "C:/Users/../Windows", "/home/../etc", ""])
def test_normalize_rejects(bad):
    with pytest.raises(workspace.WorkspaceError):
        workspace.normalize(bad)


@pytest.mark.parametrize("raw,expected", [("/home/pedro/app/", "/home/pedro/app"), ("/", "/"), ("//home//x", "/home/x")])
def test_normalize_posix(raw, expected):
    assert workspace.normalize(raw) == expected


def test_resolve_and_to_host(clean):
    assert workspace.resolve(str(clean)) == Path(workspace.normalize(str(clean)))
    assert workspace.to_host(clean) == workspace.normalize(str(clean))
    assert workspace.resolve(None) == config.WORKSPACE_ROOT  # conversa sem pasta escolhida


def test_missing_folder_is_explained(clean):
    with pytest.raises(workspace.WorkspaceError, match="A pasta não existe"):
        workspace.resolve(str(clean / "nao-existe"))


def test_list_dirs_and_parent(clean):
    out = workspace.list_dirs(str(clean.parent))  # .../Users/pedro
    assert out["parent"] == workspace.normalize(str(clean.parent.parent))
    assert [d["name"] for d in out["dirs"]] == ["app"]


def test_roots_are_real(clean):
    assert all(Path(r["path"]).is_dir() for r in workspace.roots())


def test_tools_follow_conversation_folder(clean):
    workspace.CURRENT.set(workspace.resolve(str(clean)))
    from app.tools import run_tool
    run_tool("write_file", {"path": "a.txt", "content": "x"})
    assert (clean / "a.txt").read_text() == "x"
    # caminho absoluto dentro da pasta funciona; fora dela é bloqueado
    assert resolve_path(workspace.root(), str(clean / "a.txt")).name == "a.txt"
    with pytest.raises(ToolError, match="fora da pasta"):
        resolve_path(workspace.root(), str(clean.parent / "segredo.txt"))


def test_system_prompt_shows_folder(clean):
    workspace.CURRENT.set(workspace.resolve(str(clean)))
    assert workspace.normalize(str(clean)) in agent.system_prompt("native")


# ------------------------------------------------ checkpoints

def _conv() -> int:
    with db.session() as s:
        c = db.Conversation()
        s.add(c)
        s.commit()
        return c.id


def test_checkpoint_restores_edit_and_removes_new_file(tmp_path):
    conv = _conv()
    old, new = tmp_path / "old.txt", tmp_path / "new.txt"
    old.write_text("antes")
    checkpoints.record(conv, 10, old)
    checkpoints.record(conv, 10, new)
    old.write_text("depois")
    checkpoints.record(conv, 10, old)  # segunda escrita no mesmo turno não sobrescreve o "antes"
    old.write_text("depois 2")
    new.write_text("criado")
    # summary devolve o caminho como a UI mostra (barras normais)
    assert set(checkpoints.summary(conv)[10]) == {workspace.to_host(old), workspace.to_host(new)}
    restored = checkpoints.restore_from(conv, 10)
    assert old.read_text() == "antes" and not new.exists() and len(restored) == 2
    assert checkpoints.summary(conv) == {}


def test_restore_from_undoes_later_turns_in_reverse(tmp_path):
    conv = _conv()
    f = tmp_path / "f.txt"
    f.write_text("v0")
    checkpoints.record(conv, 1, f); f.write_text("v1")
    checkpoints.record(conv, 2, f); f.write_text("v2")
    checkpoints.record(conv, 3, f); f.write_text("v3")
    checkpoints.restore_from(conv, 2)
    assert f.read_text() == "v1"  # turno 1 continua aplicado
    assert list(checkpoints.summary(conv)) == [1]


# ------------------------------------------------ modelos habilitados

def test_enabled_models_setting():
    settings.update({"enabled_models": {"lmstudio": ["b", "a", "a"], "ollama": None}})
    assert config.ENABLED_MODELS == {"lmstudio": ["a", "b"]}


# ------------------------------------------------ subagentes

def test_delegate_hidden_until_configured():
    assert "delegate_task" not in [t.name for t in active()]
    settings.update({"subagents": {"rapido": {"provider": "lmstudio", "model": "mini"}}})
    assert "delegate_task" in [t.name for t in active()]


def test_subagent_invalid_provider():
    with pytest.raises(settings.SettingsError, match="não existe"):
        settings.update({"subagents": {"capaz": {"provider": "nada", "model": "x"}}})


def test_delegation_runs_subagent_and_returns_report(monkeypatch, tmp_path):
    settings.update({"subagents": {"rapido": {"provider": "lmstudio", "model": "mini"},
                                   "capaz": {"provider": "lmstudio", "model": "grande"}}})
    seen_models = []
    step = {"main": 0, "mini": 0}

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None):
        seen_models.append(model)
        if model == "main":
            step["main"] += 1
            if step["main"] == 1:
                yield "done", {"tool_calls": [{"id": "d1", "name": "delegate_task",
                                               "arguments": {"task": "crie nota.txt", "level": "rapido"}}]}
            else:
                assert any("Relatório do subagente" in (m.get("content") or "") for m in messages)
                yield "content", "Feito pelo subagente."
                yield "done", {"tool_calls": []}
        else:
            step["mini"] += 1
            assert not tools or "delegate_task" not in [t["function"]["name"] for t in tools]
            if step["mini"] == 1:
                yield "done", {"tool_calls": [{"id": "s1", "name": "write_file",
                                               "arguments": {"path": "nota.txt", "content": "oi"}}]}
            else:
                yield "content", "Criei nota.txt."
                yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)

    async def scenario():
        conv = _conv()
        run = agent.Run(conv)
        req = agent.RunRequest(content="use o subagente", provider="lmstudio", model="main",
                               mode="agent", permission="manual")
        events = []
        async for ev in agent.run_agent(conv, req, run):
            events.append(ev)
            if ev["type"] == "approval_request":  # aprovação pedida DE DENTRO do subagente
                assert ev.get("parent") == "d1"
                run.resolve(ev["call"]["id"], True)
        return conv, events

    conv, events = asyncio.run(scenario())
    assert seen_models.count("mini") == 2 and "grande" not in seen_models
    assert (config.WORKSPACE_ROOT / "nota.txt").read_text() == "oi"
    sub_results = [e for e in events if e["type"] == "tool_result" and e.get("parent") == "d1"]
    assert sub_results and sub_results[0]["message"]["status"] == "ok"
    with db.session() as s:
        tool_msgs = [m for m in s.get(db.Conversation, conv).messages if m.role == "tool"]
    assert [m.name for m in tool_msgs] == ["delegate_task"]  # passos do subagente não entram no histórico
    info = tool_msgs[0].meta["sub"]
    assert info["model"] == "mini" and info["steps"][0]["name"] == "write_file"
    # e a escrita do subagente virou checkpoint do turno
    assert any("nota.txt" in p for p in sum(checkpoints.summary(conv).values(), []))

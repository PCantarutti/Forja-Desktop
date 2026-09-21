"""Skills, hooks, tarefas, fila, git (parse) e rotas novas de conversa, sem runner nem git de verdade."""
import asyncio

import pytest

from app import config, gitops, hooks, shell, skills, tasks
from app.agent import Run, _flush_queue
from app.tools import ToolError


# ------------------------------------------------ skills

def test_skills_builtin_and_project(tmp_path):
    (tmp_path / ".forja/skills").mkdir(parents=True)
    (tmp_path / ".forja/skills/deploy.md").write_text("---\ndescription: Faz deploy\n---\nFaça o deploy de $ARGUMENTS agora.", encoding="utf-8")
    (tmp_path / ".forja/skills/revisar.md").write_text("Revisão do projeto (sobrepõe a padrão).", encoding="utf-8")
    lst = skills.list_for(tmp_path)
    names = [s["name"] for s in lst]
    assert "compactar" in names and "deploy" in names and names.count("revisar") == 1
    deploy = next(s for s in lst if s["name"] == "deploy")
    assert deploy["description"] == "Faz deploy" and deploy["source"].endswith("deploy.md")
    assert skills.expand(deploy["prompt"], "  staging ") == "Faça o deploy de staging agora."
    assert next(s for s in lst if s["name"] == "revisar")["prompt"].startswith("Revisão do projeto")


# ------------------------------------------------ hooks

def test_hooks_run_matching_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_HOOKS", [str(tmp_path)])  # hook só roda em pasta liberada
    (tmp_path / ".forja").mkdir()
    (tmp_path / ".forja/hooks.json").write_text(
        '{"post_tool": [{"tools": ["write_file", "edit_file"], "command": "fmt {path}"}, {"tools": "run_*", "command": "x {tool}"}]}',
        encoding="utf-8")
    calls = []
    monkeypatch.setattr(shell, "exec_in", lambda root, cmd, timeout=60: (calls.append(cmd) or (0, "ok\n")))
    out = hooks.run_post("edit_file", {"path": "a.py"}, tmp_path)
    assert calls == ["fmt a.py"] and "[hook `fmt a.py` → exit 0]" in out and "ok" in out
    assert hooks.run_post("read_file", {}, tmp_path) is None
    assert hooks.run_post("run_command", {"command": "ls"}, tmp_path).startswith("[hook `x run_command`")


def test_hooks_missing_or_broken_file(tmp_path):
    assert hooks.run_post("write_file", {}, tmp_path) is None
    (tmp_path / ".forja").mkdir()
    (tmp_path / ".forja/hooks.json").write_text("{nope", encoding="utf-8")
    assert hooks.load(tmp_path) == {}


# ------------------------------------------------ tarefas

def test_tasks_normalize_and_sink():
    got = []
    token = tasks.SINK.set(lambda items: got.append(items))
    try:
        out = tasks.update_tasks(None, {"tasks": [{"text": "ler", "status": "done"}, {"text": "editar", "status": "in_progress"}, "testar"]})
    finally:
        tasks.SINK.reset(token)
    assert got[0] == [{"text": "ler", "status": "done"}, {"text": "editar", "status": "doing"}, {"text": "testar", "status": "pending"}]
    assert "1/3" in out and "editar" in out
    with pytest.raises(ToolError, match="Status inválido"):
        tasks.normalize([{"text": "x", "status": "maybe"}])
    with pytest.raises(ToolError, match="sem texto"):
        tasks.normalize([{"status": "done"}])


# ------------------------------------------------ fila de mensagens

def test_flush_queue_saves_user_turns(monkeypatch):
    from app import agent
    saved = []

    class M:
        def __init__(self, i, content):
            self.id, self.content = i, content

        def to_dict(self):
            return {"id": self.id, "role": "user", "content": self.content}

    monkeypatch.setattr(agent, "_save", lambda conv_id, **f: saved.append(f) or M(len(saved), f["content"]))
    run = Run(1)
    run.queue = ["primeira", "segunda"]
    events = _flush_queue(1, run)
    assert [e["message"]["content"] for e in events] == ["primeira", "segunda"] and run.queue == []
    assert run.turn_id == 2 and all(f["role"] == "user" for f in saved)


# ------------------------------------------------ git (parse do status)

def test_git_status_parses_porcelain(monkeypatch, tmp_path):
    answers = {
        "git rev-parse --is-inside-work-tree": (0, "true\n"),
        "git branch --show-current": (0, "feat/x\n"),
        "git status --porcelain=v1": (0, " M app.py\n?? novo.txt\nA  add.py\nD  velho.py\nR  a -> b\n"),
        "git rev-list --left-right --count @{u}...HEAD": (0, "1\t3\n"),
        "git remote get-url origin": (0, "git@github.com:x/y.git\n"),
        "gh --version": (1, ""),
        "git log -1 --pretty=%h%x09%s": (0, "abc123\tfeat: algo\n"),
    }
    monkeypatch.setattr(shell, "exec_in", lambda root, cmd, timeout=60: answers.get(cmd, (1, "")))
    st = gitops.status(tmp_path)
    assert st["repo"] and st["branch"] == "feat/x" and st["behind"] == 1 and st["ahead"] == 3 and not st["has_gh"]
    assert [(f["status"], f["path"]) for f in st["files"]] == [
        ("modified", "app.py"), ("untracked", "novo.txt"), ("added", "add.py"), ("deleted", "velho.py"), ("renamed", "b")]
    assert st["last_commit"].startswith("abc123")


def test_git_not_a_repo(monkeypatch, tmp_path):
    monkeypatch.setattr(shell, "exec_in", lambda root, cmd, timeout=60: (128, "fatal: not a git repository"))
    assert gitops.status(tmp_path) == {"repo": False}
    with pytest.raises(ToolError, match="não é um repositório"):
        gitops.create_pr(tmp_path, "t", "b")


def test_git_commit_requires_message_and_changes(monkeypatch, tmp_path):
    with pytest.raises(ToolError, match="vazia"):
        gitops.commit(tmp_path, "  ")
    monkeypatch.setattr(shell, "exec_in", lambda root, cmd, timeout=60: (0, "") if "diff --cached --quiet" in cmd else (0, "ok"))
    with pytest.raises(ToolError, match="Nada para commitar"):
        gitops.commit(tmp_path, "feat: x")


def test_generate_message_uses_model(monkeypatch, tmp_path):
    from app import llm
    monkeypatch.setattr(shell, "exec_in", lambda root, cmd, timeout=60: (0, "+linha\n") if "diff --cached" in cmd else (0, ""))

    async def fake_stream(*a, **k):
        yield "content", "feat(app): adiciona linha"
        yield "done", {}

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    assert asyncio.run(gitops.generate_message(tmp_path, "p", "m")) == "feat(app): adiciona linha"


# ------------------------------------------------ conversas em lote (API)

def test_bulk_archive_and_delete():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        a = c.post("/api/conversations", json={"kind": "agent"}).json()["id"]
        b = c.post("/api/conversations", json={"kind": "agent"}).json()["id"]
        r = c.post("/api/conversations/bulk", json={"ids": [a, b], "action": "archive"}).json()
        assert r["done"] == 2 and r["skipped"] == []
        ids_active = [x["id"] for x in c.get("/api/conversations?kind=agent").json()]
        ids_archived = [x["id"] for x in c.get("/api/conversations?kind=agent&archived=true").json()]
        assert a not in ids_active and b not in ids_active and a in ids_archived and b in ids_archived
        assert c.post("/api/conversations/bulk", json={"ids": [a], "action": "pin"}).json()["done"] == 1
        assert c.get(f"/api/conversations/{a}").json()["pinned"] is True
        assert c.post("/api/conversations/bulk", json={"ids": [a, b, 999999], "action": "delete"}).json()["done"] == 2
        assert c.get(f"/api/conversations/{a}").status_code == 404
        assert c.post("/api/conversations/bulk", json={"ids": [1], "action": "explode"}).status_code == 400

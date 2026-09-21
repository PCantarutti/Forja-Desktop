"""Menu do `@` no campo de mensagem: busca de caminhos da pasta da conversa."""
from app import workspace


def test_workspace_files_finds_and_ignores_noise(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "agent.py").write_text("x", encoding="utf-8")
    (tmp_path / "backend" / "app" / "shell.py").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules" / "lixo").mkdir(parents=True)
    (tmp_path / "node_modules" / "lixo" / "agent.py").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "agent.py").write_text("x", encoding="utf-8")
    monkeypatch.setattr(workspace, "default_root", lambda: tmp_path)

    with TestClient(app) as c:
        todos = c.get("/api/workspace/files").json()["files"]
        assert "backend/app/agent.py" in todos and "backend/app/shell.py" in todos
        assert not [f for f in todos if "node_modules" in f or f.startswith(".git")]

        achados = c.get("/api/workspace/files?q=agent").json()["files"]
        assert achados == ["backend/app/agent.py"]  # casa pelo caminho inteiro, não só pelo nome
        assert c.get("/api/workspace/files?q=nao-existe").json()["files"] == []
        assert len(c.get("/api/workspace/files?limit=1").json()["files"]) == 1

import base64
import json

import pytest

from app import config, memory, policy, settings, uploads
from app.agent import build_history, system_prompt
from app.db import Message
from app.tools import REGISTRY


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path)
    settings.reset()
    yield
    settings.reset()


# ------------------------------------------------ permissões (auto-aprovação)

def test_no_rule_means_approval():
    assert policy.auto_rule("run_command", {"command": "pytest -q"}) is None
    assert policy.auto_rule("write_file", {"path": "a.py"}) is None


def test_command_glob_matches():
    settings.update({"auto_approve_commands": ["pytest*", "git status"]})
    assert policy.auto_rule("run_command", {"command": "pytest -q"}) == "comando pytest*"
    assert policy.auto_rule("run_command", {"command": "  git status  "}) == "comando git status"
    assert policy.auto_rule("run_command", {"command": "git push"}) is None
    assert policy.auto_rule("run_command", {"command": "rm -rf /"}) is None


def test_rule_does_not_cover_a_chained_command():
    """O glob casa prefixo: sem esta guarda, `pytest*` liberaria o que viesse depois do `;`."""
    settings.update({"auto_approve_commands": ["pytest*"]})
    assert policy.auto_rule("run_command", {"command": "pytest -q"}) == "comando pytest*"
    encadeados = ["pytest -q; Remove-Item -Recurse C:/x", "pytest -q && rm -rf x",
                  "pytest -q | tee out", "pytest -q" + chr(10) + "rm -rf x", "pytest $(whoami)"]
    for encadeado in encadeados:
        assert policy.auto_rule("run_command", {"command": encadeado}) is None


def test_destructive_asks_even_with_a_rule():
    """Regra é atalho para o que se repete, não cheque em branco: apagar sempre mostra o card."""
    settings.update({"auto_approve_commands": ["git push*"]})
    shell_tool = REGISTRY["run_command"]
    assert policy.decide(shell_tool, {"command": "git push --force"}, "bypass") == (True, None)
    assert policy.decide(shell_tool, {"command": "git push"}, "manual") == (False, "comando git push*")


def test_tool_glob_matches_mcp_prefix():
    settings.update({"auto_approve_tools": ["mcp__memoria__*"]})
    assert policy.auto_rule("mcp__memoria__create_entities", {}) == "ferramenta mcp__memoria__*"
    assert policy.auto_rule("mcp__outro__create", {}) is None


@pytest.mark.parametrize("command,expected", [
    ("pytest -q tests", "pytest*"),
    ("git status --short", "git status*"),
    ("npm run build", "npm run*"),
    ("ls -la", "ls*"),
])
def test_suggestion_for_always_allow_button(command, expected):
    assert policy.suggest("run_command", {"command": command}) == expected


def test_suggestion_for_other_tools_is_the_tool_name():
    assert policy.suggest("write_file", {"path": "a.py"}) == "write_file"


# ------------------------------------------------ memória do projeto

def test_project_memory_enters_system_prompt(tmp_path):
    (tmp_path / "FORJA.md").write_text("# Projeto\n- usa pytest\n", encoding="utf-8")
    assert "usa pytest" in system_prompt("native")
    settings.update({"project_memory": False})
    assert "usa pytest" not in system_prompt("native")


def test_project_memory_is_truncated(tmp_path):
    (tmp_path / "FORJA.md").write_text("x" * (memory.MAX_PROJECT_MEMORY + 500), encoding="utf-8")
    assert len(memory.project_text()) == memory.MAX_PROJECT_MEMORY


def test_project_memory_write_and_read(tmp_path):
    out = memory.project_write("# Forja\n- backend em FastAPI\n")
    assert out["exists"] and "FastAPI" in out["content"]
    assert (tmp_path / "FORJA.md").exists()


def test_project_memory_file_must_be_simple_name():
    with pytest.raises(settings.SettingsError):
        settings.update({"project_memory_file": "../escapar.md"})


# ------------------------------------------------ anexos

def test_save_sanitizes_name_and_stays_in_workspace(tmp_path):
    a = uploads.save("../../evil name.TXT", b"oi", "text/plain")
    assert a["path"].startswith(".forja/uploads/") and a["kind"] == "text"
    assert (tmp_path / a["path"]).read_bytes() == b"oi"


def test_save_respects_size_limit():
    settings.update({"max_file_bytes": 1000})
    with pytest.raises(ValueError, match="limite"):
        uploads.save("grande.txt", b"x" * 1001, "text/plain")


def test_image_becomes_content_parts(tmp_path):
    a = uploads.save("foto.png", b"\x89PNG-fake", "image/png")
    msg = uploads.user_message("o que é isso?", [a])
    assert msg["content"][0]["text"] == "o que é isso?"
    url = msg["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",")[1]) == b"\x89PNG-fake"


def test_non_image_is_mentioned_as_path(tmp_path):
    a = uploads.save("notas.txt", b"conteudo", "text/plain")
    msg = uploads.user_message("resume", [a])
    assert isinstance(msg["content"], str) and ".forja/uploads/" in msg["content"] and "read_file" in msg["content"]


def test_history_carries_attachments(tmp_path):
    a = uploads.save("foto.png", b"123", "image/png")
    msgs = [Message(id=1, role="user", content="veja", thinking="", meta={"attachments": [a]})]
    hist = build_history(msgs, "native")
    assert isinstance(hist[1]["content"], list)


def test_ollama_conversion_moves_images_to_images_field(tmp_path):
    from app.llm import _to_ollama
    a = uploads.save("foto.png", b"123", "image/png")
    out = _to_ollama([uploads.user_message("veja", [a])])
    assert out[0]["content"] == "veja" and out[0]["images"] == [base64.b64encode(b"123").decode()]

import pytest

import app.shell  # noqa: F401  (registra run_command)
from app.tools import REGISTRY, ToolError, preview_tool, run_tool
from app.web import check_public_url, html_to_text


# ------------------------------------------------ run_command

def test_shell_registered_and_always_asks():
    t = REGISTRY["run_command"]
    assert t.mutating and t.always_ask


def test_shell_cwd_confined(tmp_path):
    with pytest.raises(ToolError, match="fora da pasta"):
        preview_tool("run_command", {"command": "ls", "cwd": "../.."}, tmp_path)


def test_shell_preview(tmp_path):
    (tmp_path / "sub").mkdir()
    pv = preview_tool("run_command", {"command": "pytest -q", "cwd": "sub"}, tmp_path)
    assert pv["kind"] == "command" and pv["text"] == "pytest -q" and pv["path"].replace("\\", "/").endswith("/sub")


# ------------------------------------------------ web

@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x.com", "http://localhost:8000",
                                 "http://127.0.0.1/", "http://10.0.0.5/", "http://192.168.1.1", "not a url"])
def test_fetch_blocks_local_and_non_http(url):
    with pytest.raises(ToolError):
        check_public_url(url)


def test_html_to_text():
    title, text = html_to_text(
        "<html><head><title>T</title><script>alert(1)</script></head>"
        "<body><nav>menu</nav><h1>Oi</h1><p>um   texto</p><style>x{}</style><p>dois</p></body></html>")
    assert title == "T"
    assert "alert" not in text and "menu" not in text and "x{}" not in text
    assert text == "Oi\n\num texto\n\ndois"


# ------------------------------------------------ run_command em background

def test_run_command_background_becomes_a_process(tmp_path, monkeypatch):
    """Comando demorado não prende o turno: vira processo, com log e nome, como um servidor."""
    from app import shell

    monkeypatch.setattr(shell.time, "sleep", lambda *_: None)  # serve_start espera o log; no teste não precisa
    out = run_tool("run_command", {"command": "echo ok", "background": True, "name": "build"}, tmp_path)
    assert "Processo 'build'" in out and "http" not in out  # dica de URL é coisa de servidor
    assert any(s["name"] == "build" for s in shell.list_servers())
    shell.stop_server("build")

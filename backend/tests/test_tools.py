import os

import pytest

from app.tools import ToolError, resolve_path, run_tool, edit_preview, write_preview


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "calc.py").write_text("def soma(a, b):\n    return a + b\n", encoding="utf-8")
    return tmp_path


# ------------------------------------------------ confinamento

@pytest.mark.parametrize("bad", ["../../etc/passwd", "..", "sub/../../x", "/etc/passwd", "/workspace/../etc"])
def test_path_escape_blocked(ws, bad):
    with pytest.raises(ToolError, match="fora da pasta de trabalho"):
        resolve_path(ws, bad)


def test_read_passwd_returns_error_not_crash(ws):
    with pytest.raises(ToolError, match="fora da pasta"):
        run_tool("read_file", {"path": "../../etc/passwd"}, ws)


@pytest.mark.parametrize("ok", ["calc.py", "./calc.py", "a/b/../calc.py", "/workspace/calc.py", ".", ""])
def test_path_inside_allowed(ws, ok):
    assert resolve_path(ws, ok).is_relative_to(ws.resolve())


def test_symlink_escape_blocked(ws, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.txt").write_text("x")
    link = ws / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink não permitido neste SO")
    with pytest.raises(ToolError, match="fora da pasta"):
        resolve_path(ws, "link/secret.txt")


# ------------------------------------------------ edit_file

def test_edit_zero_occurrences(ws):
    with pytest.raises(ToolError, match="não encontrado"):
        run_tool("edit_file", {"path": "calc.py", "old_str": "nada", "new_str": "x"}, ws)


def test_edit_one_occurrence(ws):
    out = run_tool("edit_file", {"path": "calc.py", "old_str": "a + b", "new_str": "b + a"}, ws)
    assert "editado" in out
    assert "return b + a" in (ws / "calc.py").read_text(encoding="utf-8")


def test_edit_many_occurrences(ws):
    (ws / "dup.txt").write_text("x\nx\nx\n")
    with pytest.raises(ToolError, match="3 vezes"):
        run_tool("edit_file", {"path": "dup.txt", "old_str": "x", "new_str": "y"}, ws)
    assert (ws / "dup.txt").read_text() == "x\nx\nx\n"


def test_edit_replace_all(ws):
    (ws / "dup.txt").write_text("x\nx\nx\n")
    out = run_tool("edit_file", {"path": "dup.txt", "old_str": "x", "new_str": "y", "replace_all": True}, ws)
    assert "3 trechos" in out
    assert (ws / "dup.txt").read_text() == "y\ny\ny\n"


def test_edit_batch_is_all_or_nothing(ws):
    antes = (ws / "calc.py").read_text(encoding="utf-8")
    with pytest.raises(ToolError, match="Edição 2"):
        run_tool("edit_file", {"path": "calc.py",
                               "edits": [{"old_str": "a + b", "new_str": "b + a"},
                                         {"old_str": "não existe", "new_str": "z"}]}, ws)
    assert (ws / "calc.py").read_text(encoding="utf-8") == antes  # a primeira edição não foi escrita
    run_tool("edit_file", {"path": "calc.py",
                           "edits": [{"old_str": "a + b", "new_str": "b + a"},
                                     {"old_str": "def ", "new_str": "def  "}]}, ws)
    depois = (ws / "calc.py").read_text(encoding="utf-8")
    assert "return b + a" in depois and "def  " in depois


def test_edit_preview_is_diff(ws):
    pv = edit_preview(ws, {"path": "calc.py", "old_str": "a + b", "new_str": "b + a"})
    assert pv["kind"] == "diff" and "-    return a + b" in pv["text"] and "+    return b + a" in pv["text"]


# ------------------------------------------------ write/read/list

def test_write_creates_dirs_and_read_numbers_lines(ws):
    run_tool("write_file", {"path": "pkg/sub/m.py", "content": "a\nb\n"}, ws)
    out = run_tool("read_file", {"path": "pkg/sub/m.py", "start_line": 2}, ws)
    assert out.strip() == "2\tb\n(Fim do arquivo - 2 linhas)"


def test_write_preview_new_vs_existing(ws):
    assert write_preview(ws, {"path": "novo.py", "content": "x"})["kind"] == "new"
    assert write_preview(ws, {"path": "calc.py", "content": "x"})["kind"] == "diff"


def test_file_size_limit(ws, monkeypatch):
    monkeypatch.setattr("app.config.MAX_FILE_BYTES", 5)
    with pytest.raises(ToolError, match="grande demais"):
        run_tool("write_file", {"path": "big.txt", "content": "123456"}, ws)


def test_list_dir(ws):
    run_tool("write_file", {"path": "d/f.txt", "content": "x"}, ws)
    assert "d/" in run_tool("list_dir", {}, ws)
    assert "d/f.txt" in run_tool("list_dir", {"recursive": True}, ws)


def test_missing_arg_is_tool_error(ws):
    with pytest.raises(ToolError, match="Argumentos inválidos"):
        run_tool("write_file", {"path": "a.txt"}, ws)

"""Execução local (native.py + shell.py): é o que substituiu o forja-runner e o bash do container.

Roda comandos de verdade no shell do sistema — é justamente o que precisa ser verificado.
"""
import sys
import time

import pytest

from app import agent, native, shell, workspace
from app.tools import REGISTRY, ToolError, run_tool

WIN = sys.platform == "win32"


def test_shell_argv_builds_system_shell():
    argv = native.shell_argv("echo oi")
    if WIN:
        assert argv[0] in ("pwsh", "powershell") and "-NoProfile" in argv
        assert argv[-1].endswith("if ($LASTEXITCODE) { exit $LASTEXITCODE } elseif (-not $?) { exit 1 }")
        assert "echo oi" in argv[-1]
    else:
        assert argv == ["bash", "-lc", "echo oi"]


def test_run_command_ok_and_cwd(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    out = run_tool("run_command", {"command": "ls a.txt" if not WIN else "Get-ChildItem a.txt"}, tmp_path)
    assert out.startswith("exit code: 0") and "a.txt" in out


def test_run_command_propagates_exit_code(tmp_path):
    with pytest.raises(ToolError, match="exit code: 3"):
        run_tool("run_command", {"command": "exit 3"}, tmp_path)


def test_run_command_timeout_kills_tree(tmp_path):
    started = time.monotonic()
    sleep = "Start-Sleep -Seconds 30" if WIN else "sleep 30"
    with pytest.raises(ToolError, match="Timeout"):
        run_tool("run_command", {"command": sleep, "timeout": 1}, tmp_path)
    assert time.monotonic() - started < 20  # morreu no timeout, não esperou os 30 s


def test_run_command_streams_output_live(tmp_path):
    lines = []
    token = shell.OUTPUT_SINK.set(lines.append)
    try:
        run_tool("run_command", {"command": "echo um; echo dois"}, tmp_path)
    finally:
        shell.OUTPUT_SINK.reset(token)
    assert "".join(lines).count("\n") >= 2 and "um" in "".join(lines)


def test_tools_have_no_target_choice():
    assert REGISTRY["serve_start"].always_ask and REGISTRY["serve_start"].mutating
    assert not REGISTRY["serve_status"].mutating and REGISTRY["serve_stop"].mutating
    assert "target" not in REGISTRY["run_command"].parameters["properties"]


def test_environment_block_describes_the_machine(monkeypatch, tmp_path):
    monkeypatch.setattr(native, "_INFO", {"system": "Windows", "release": "11", "machine": "AMD64",
                                          "shell": "powershell", "home": "C:/Users/pedro",
                                          "versions": {"node": "v22.1.0", "git": None}})
    token = workspace.CURRENT.set(tmp_path)
    try:
        text = "\n".join(agent.environment_block(["run_command", "serve_start"]))
        short = agent.environment_block(["read_file"])
    finally:
        workspace.CURRENT.reset(token)
    assert "Windows 11 (powershell)" in text and "node v22.1.0" in text
    assert "Não encontrado no PATH: git" in text and "http://localhost:PORTA" in text
    assert "';'" in text  # dica de sintaxe do PowerShell
    assert len(short) == 2  # sem ferramentas de shell, só a pasta


# ------------------------------------------------ abrir e revelar (bugs vistos em uso)

@pytest.mark.skipif(not WIN, reason="o caminho do Explorer é do Windows")
def test_revelar_cita_o_caminho_e_nao_o_argumento_inteiro(tmp_path, monkeypatch):
    """Pasta com espaço no nome levava o Explorer para Documentos em vez do arquivo.

    O Popen com lista cita o argumento todo — `"/select,C:/pasta com espaco/x"` —, e o Explorer
    não entende isso: ele abre a pasta padrão e some com o arquivo.
    """
    alvo = tmp_path / "pasta com espaco"
    alvo.mkdir()
    arquivo = alvo / "relatorio.pdf"
    arquivo.write_bytes(b"%PDF-1.4")
    vistos = []
    monkeypatch.setattr(native.subprocess, "Popen", lambda cmd, **kw: vistos.append((cmd, kw)))

    native.open_path(str(arquivo), "reveal")
    cmd, kw = vistos[0]
    assert isinstance(cmd, str), "tem que ser string: lista passaria pelo list2cmdline"
    assert cmd.startswith("explorer /select,")
    assert not kw.get("shell"), "sem shell: a linha vai direto para o CreateProcess"
    assert '"' + str(arquivo) + '"' in cmd  # as aspas em volta do caminho, só


@pytest.mark.skipif(not WIN, reason="o caminho do VS Code é do Windows")
@pytest.mark.parametrize("nome,espera_sistema", [
    ("relatorio.docx", True), ("planilha.xlsx", True), ("leitura.pdf", True),
    ("modulo.py", False), ("notas.md", False),
])
def test_documento_abre_no_programa_do_sistema_e_codigo_no_editor(tmp_path, monkeypatch, nome, espera_sistema):
    """Abrir um .docx no VS Code mostra XML zipado. Quem abre documento é o Word."""
    arquivo = tmp_path / nome
    arquivo.write_text("x", encoding="utf-8")
    chamou_startfile = []
    monkeypatch.setattr(native.os, "startfile", lambda p: chamou_startfile.append(p), raising=False)
    monkeypatch.setattr(native.subprocess, "Popen", lambda *a, **kw: None)
    monkeypatch.setattr(native.shutil, "which", lambda cmd: "C:/code.cmd")  # VS Code instalado

    assert native.open_path(str(arquivo), "editor") == ("padrão" if espera_sistema else "code")
    assert bool(chamou_startfile) is espera_sistema

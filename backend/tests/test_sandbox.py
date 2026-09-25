"""E12 (passos 1 e 2): ambiente limpo e Job Object nos processos do agente."""
import os
import time

import pytest

from app import config, native, sandbox, shell
from app.tools import ToolError, run_tool

WIN = native.WINDOWS


def _ler_var(tmp_path, nome):
    cmd = f"Write-Output \"[$env:{nome}]\"" if WIN else f'echo "[${nome}]"'
    return run_tool("run_command", {"command": cmd, "timeout": 30}, tmp_path)


def test_segredo_do_backend_nao_chega_ao_comando(tmp_path, monkeypatch):
    monkeypatch.setenv("MEU_API_KEY", "sk-segredo")
    monkeypatch.setenv("FORJA_TOKEN_X", "interno")
    monkeypatch.setenv("VARIAVEL_COMUM", "passa")
    assert "[]" in _ler_var(tmp_path, "MEU_API_KEY")
    assert "[]" in _ler_var(tmp_path, "FORJA_TOKEN_X")
    assert "[passa]" in _ler_var(tmp_path, "VARIAVEL_COMUM")
    assert os.environ["PATH"]  # o PATH e o resto do sistema continuam


def test_env_allow_do_forja_md_libera(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET", "sk-test")
    (tmp_path / config.PROJECT_MEMORY_FILE).write_text("# Loja\n- env_allow: STRIPE_SECRET, OUTRA\n", encoding="utf-8")
    assert "[sk-test]" in _ler_var(tmp_path, "STRIPE_SECRET")


def test_lista_de_bloqueio():
    for nome in ("OPENAI_API_KEY", "GITHUB_TOKEN", "HF_TOKEN", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD", "FORJA_DATA"):
        assert sandbox.bloqueada(nome), nome
    for nome in ("PATH", "APPDATA", "PATHEXT", "JAVA_HOME", "SSH_AUTH_SOCK", "USERPROFILE"):
        assert not sandbox.bloqueada(nome), nome


@pytest.mark.skipif(not WIN, reason="Job Object é do Windows")
def test_filho_que_sobra_morre_com_o_comando(tmp_path):
    """Antes um processo aberto em segundo plano pelo comando (daemon, watcher) ficava órfão."""
    marca = tmp_path / "vivo.txt"
    filho = (f"Start-Process -WindowStyle Hidden powershell -ArgumentList '-NoProfile','-Command',"
             f"'Start-Sleep 3; Set-Content -Path \"{marca}\" -Value vivo'")
    run_tool("run_command", {"command": filho, "timeout": 30}, tmp_path)
    time.sleep(5)
    assert not marca.exists()  # o filho foi morto quando o job fechou


@pytest.mark.skipif(not WIN, reason="Job Object é do Windows")
def test_limite_de_processos_barra_fork_bomb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_PROCESSOS", 3)
    muitos = ("$ok=0; 1..6 | ForEach-Object { try { $null = Start-Process -PassThru -WindowStyle Hidden cmd "
              "-ArgumentList '/c','ping -n 6 127.0.0.1 >nul' -ErrorAction Stop; $ok++ } catch { 'barrado' } }; "
              "Write-Output \"abertos=$ok\"")
    try:
        saida = run_tool("run_command", {"command": muitos, "timeout": 30}, tmp_path)
    except ToolError as e:
        saida = str(e)
    assert "barrado" in saida and "abertos=6" not in saida


@pytest.mark.skipif(not WIN, reason="Job Object é do Windows")
def test_limite_de_memoria_avisa(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_MEMORIA_MB", 150)
    come = "$x = New-Object byte[] (400MB); Write-Output ok"
    try:
        saida = run_tool("run_command", {"command": come, "timeout": 60}, tmp_path)
    except ToolError as e:
        saida = str(e)
    assert "limite de memória do sandbox (150 MB)" in saida and "Sandbox: memória por comando" in saida


def test_limite_desligado_nao_atrapalha(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_MEMORIA_MB", 0)
    monkeypatch.setattr(config, "SANDBOX_PROCESSOS", 0)
    monkeypatch.setattr(config, "SANDBOX_CPU", 0)
    assert "ok" in run_tool("run_command", {"command": "echo ok", "timeout": 30}, tmp_path)


def test_memoria_automatica_e_metade_da_ram_ate_4gb():
    assert 0 < sandbox.memoria_padrao_mb() <= 4096

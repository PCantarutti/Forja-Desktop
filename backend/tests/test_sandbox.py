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


# ------------------------------------------------------------------ passo 3: container


def test_modos_do_isolamento(monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "desligado")
    assert not sandbox.isolar()
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "sempre")
    assert sandbox.isolar()
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "autonomo")
    tok = sandbox.AUTONOMO.set(lambda: False)
    assert not sandbox.isolar()                        # modo Manual: você aprova cada comando
    sandbox.AUTONOMO.reset(tok)
    tok = sandbox.AUTONOMO.set(lambda: True)
    assert sandbox.isolar() and "container Linux (bash" in sandbox.nota_para_o_modelo()
    sandbox.AUTONOMO.reset(tok)


def test_argv_do_container_so_monta_o_projeto_e_so_libera_rede_na_instalacao(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_MEMORIA_MB", 2048)
    monkeypatch.setattr(config, "SANDBOX_PROCESSOS", 64)
    (tmp_path / "sub").mkdir()
    teste = sandbox.argv_docker("pytest -q", tmp_path / "sub", tmp_path, "python:3.12-bookworm", "forja-x")
    assert f"{tmp_path.resolve()}:/workspace" in teste and teste[teste.index("-w") + 1] == "/workspace/sub"
    assert teste[teste.index("--network") + 1] == "none"
    assert teste[teste.index("--user") + 1] == "1000:1000" and "ALL" in teste and "no-new-privileges" in teste
    assert teste[teste.index("--memory") + 1] == "2048m" and teste[teste.index("--pids-limit") + 1] == "64"
    assert teste[-3:] == ["bash", "-lc", "pytest -q"]
    instala = sandbox.argv_docker("npm install", tmp_path, tmp_path, "node:22-bookworm", "forja-y")
    assert instala[instala.index("--network") + 1] == "bridge"
    assert not any(":\\" in a and "workspace" not in a and "cache" not in a for a in teste if ":" in a)


def test_imagem_pelo_projeto(tmp_path):
    assert sandbox.imagem(tmp_path) == sandbox.IMAGEM_PYTHON
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert sandbox.imagem(tmp_path) == sandbox.IMAGEM_NODE
    (tmp_path / config.PROJECT_MEMORY_FILE).write_text("# X\nsandbox_image: rust:1.80\n", encoding="utf-8")
    assert sandbox.imagem(tmp_path) == "rust:1.80"


def test_docker_parado_roda_no_windows_com_aviso(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "sempre")
    monkeypatch.setattr(sandbox, "docker_ok", lambda: False)
    saida = run_tool("run_command", {"command": "echo ok", "timeout": 30}, tmp_path)
    assert "Docker não está rodando" in saida and "ok" in saida


def test_imagem_ausente_baixa_em_segundo_plano_e_nao_trava(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "sempre")
    monkeypatch.setattr(sandbox, "docker_ok", lambda: True)
    monkeypatch.setattr(sandbox, "_imagem_presente", lambda img: False)
    pedidas = []
    monkeypatch.setattr(sandbox, "_puxa", lambda img: pedidas.append(img))
    saida = run_tool("run_command", {"command": "echo ok", "timeout": 30}, tmp_path)
    assert "baixando a imagem do sandbox" in saida and pedidas == [sandbox.IMAGEM_PYTHON]


def _docker_pronto(img):
    try:
        return sandbox.docker_ok() and sandbox._imagem_presente(img)
    except Exception:
        return False


@pytest.mark.skipif(not _docker_pronto(sandbox.IMAGEM_PYTHON), reason="Docker parado ou imagem não baixada")
def test_container_de_verdade_isola_disco_e_rede(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "sempre")
    (tmp_path / "dentro.txt").write_text("projeto", encoding="utf-8")
    saida = run_tool("run_command", {"command": "cat dentro.txt; ls /c 2>&1 | head -1; whoami 2>&1; "
                                     "python3 -c 'import urllib.request as u; u.urlopen(\"http://example.com\", timeout=5)' "
                                     "2>&1 | tail -1; echo fim", "timeout": 120}, tmp_path)
    assert "projeto" in saida and "fim" in saida
    assert "No such file" in saida or "cannot access" in saida   # o disco C do Windows não existe lá
    assert "root" not in saida.split("fim")[0].splitlines()[-3:]  # não é root
    assert "Error" in saida or "Temporary failure" in saida       # sem rede fora da instalação

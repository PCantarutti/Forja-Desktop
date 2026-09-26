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
    assert not any(":\\" in a and "workspace" not in a and "cache" not in a and ":/forja:ro" not in a for a in teste if ":" in a)


def test_imagem_pelo_projeto(tmp_path):
    assert sandbox.imagem(tmp_path) == sandbox.IMAGEM_PYTHON
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert sandbox.imagem(tmp_path) == sandbox.IMAGEM_NODE
    (tmp_path / config.PROJECT_MEMORY_FILE).write_text("# X\nsandbox_image: rust:1.80\n", encoding="utf-8")
    assert sandbox.imagem(tmp_path) == "rust:1.80"


def test_docker_parado_roda_no_windows_com_aviso(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "sempre")
    monkeypatch.setattr(sandbox, "docker_ok", lambda motor="desktop": False)
    saida = run_tool("run_command", {"command": "echo ok", "timeout": 30}, tmp_path)
    assert "nenhum Docker está rodando" in saida and "ok" in saida


def test_imagem_ausente_baixa_em_segundo_plano_e_nao_trava(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "sempre")
    monkeypatch.setattr(sandbox, "docker_ok", lambda motor="desktop": motor == "desktop")
    monkeypatch.setattr(sandbox, "_imagem_presente", lambda img, motor="desktop": False)
    pedidas = []
    monkeypatch.setattr(sandbox, "_puxa", lambda img, motor="desktop": pedidas.append(img))
    saida = run_tool("run_command", {"command": "echo ok", "timeout": 30}, tmp_path)
    assert "baixando a imagem do sandbox" in saida and pedidas == [sandbox.IMAGEM_PYTHON]


def _docker_pronto(img, motor="desktop"):
    try:
        return sandbox.docker_ok(motor) and sandbox._imagem_presente(img, motor)
    except Exception:
        return False


def test_motor_wsl_traduz_o_caminho_e_chama_pelo_wsl(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(config, "SANDBOX_WSL_DISTRO", "Ubuntu")
    assert sandbox.prefixo("wsl") == ["wsl.exe", "-d", "Ubuntu", "--exec", "docker"]
    assert sandbox.prefixo("desktop") == ["docker"]
    if WIN:
        assert sandbox.caminho("wsl", Path("C:/Projetos/App")) == "/mnt/c/Projetos/App"
    argv = sandbox.argv_docker("true", tmp_path, tmp_path, "python:3.12-bookworm", "forja-z", "wsl")
    assert argv[:5] == ["wsl.exe", "-d", "Ubuntu", "--exec", "docker"] and argv[5] == "run"
    montagem = argv[argv.index("-v") + 1]
    assert montagem.endswith(":/workspace") and (montagem.startswith("/mnt/") or not WIN)


def test_auto_prefere_o_desktop_e_cai_para_o_wsl(monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_MOTOR", "auto")
    monkeypatch.setattr(sandbox, "docker_ok", lambda motor="desktop": True)
    assert sandbox.motor_ativo() == "desktop"
    monkeypatch.setattr(sandbox, "docker_ok", lambda motor="desktop": motor == "wsl")
    assert sandbox.motor_ativo() == "wsl"
    monkeypatch.setattr(config, "SANDBOX_MOTOR", "desktop")
    assert sandbox.motor_ativo() is None                     # escolheu o Desktop: não cai para o WSL


@pytest.mark.skipif(not _docker_pronto(sandbox.IMAGEM_PYTHON, "wsl"), reason="Docker do WSL parado ou sem a imagem")
def test_container_de_verdade_pelo_wsl(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "sempre")
    monkeypatch.setattr(config, "SANDBOX_MOTOR", "wsl")
    (tmp_path / "dentro.txt").write_text("projeto", encoding="utf-8")
    saida = run_tool("run_command", {"command": "cat dentro.txt; ls /mnt/c 2>&1 | head -1; echo fim",
                                     "timeout": 120}, tmp_path)
    assert "projeto" in saida and "fim" in saida and ("No such file" in saida or "cannot access" in saida)


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


@pytest.mark.skipif(not _docker_pronto(sandbox.IMAGEM_PYTHON, "wsl"), reason="Docker do WSL parado ou sem a imagem")
def test_wsl_preserva_aspas_e_operadores_do_comando(tmp_path, monkeypatch):
    """Com `wsl.exe --`, o $i e as aspas se perdiam no shell do Linux e o comando do agente quebrava."""
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "sempre")
    monkeypatch.setattr(config, "SANDBOX_MOTOR", "wsl")
    saida = run_tool("run_command", {"command": 'for i in 1 2; do echo "n=$i" > f$i.txt; done && cat f1.txt f2.txt',
                                     "timeout": 120}, tmp_path)
    assert "n=1" in saida and "n=2" in saida


# ------------------------------------------------------------------ servidor, terminal e cache (passo 3, resto)

def test_porta_do_servidor(tmp_path):
    assert sandbox.porta_do_servidor("python -m http.server 8123", tmp_path) == 8123
    assert sandbox.porta_do_servidor("npx vite --port 4000", tmp_path) == 4000
    assert sandbox.porta_do_servidor("uvicorn app:app", tmp_path) == 8000
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "vite"}}', encoding="utf-8")
    assert sandbox.porta_do_servidor("npm run dev", tmp_path) == 5173


def test_argv_publica_a_porta_so_no_loopback(tmp_path):
    argv = sandbox.argv_docker("x", tmp_path, tmp_path, "python:3.12-bookworm", "forja-p", rede="bridge",
                               portas={5173: 5180})
    assert argv[argv.index("-p") + 1] == "127.0.0.1:5180:5173" and argv[argv.index("--network") + 1] == "bridge"
    assert any(a.endswith(":/forja:ro") for a in argv)


def test_sem_docker_servidor_e_terminal_ficam_no_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "sempre")
    monkeypatch.setattr(sandbox, "docker_ok", lambda motor="desktop": False)
    pl = sandbox.plano_servidor("npm run dev", tmp_path, tmp_path)
    assert pl["nome"] == "" and "nenhum Docker" in pl["aviso"] and pl["argv"] == native.shell_argv("npm run dev")
    assert sandbox.plano_terminal(tmp_path, tmp_path)["nome"] == ""


def _wsl(monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_ISOLADO", "sempre")
    monkeypatch.setattr(config, "SANDBOX_MOTOR", "wsl")


@pytest.mark.skipif(not _docker_pronto(sandbox.IMAGEM_PYTHON, "wsl"), reason="Docker do WSL parado ou sem a imagem")
def test_servidor_no_container_abre_no_localhost_do_windows(tmp_path, monkeypatch):
    """http.server escuta em todas as interfaces; o 127.0.0.1 exige o repassador. Os dois abrem no Windows."""
    import urllib.request
    _wsl(monkeypatch)
    (tmp_path / "index.html").write_text("<title>dentro do container</title>", encoding="utf-8")
    containers = []
    for nome, cmd in (("todas", "python3 -m http.server 8765"), ("local", "python3 -m http.server 8766 --bind 127.0.0.1")):
        saida = shell.serve_start(tmp_path, {"name": nome, "command": cmd})
        containers.append(shell._SERVERS[nome]["proc"]._forja_container)
        try:
            info = shell._info(nome)
            assert "no container do sandbox" in saida and info["url"].startswith("http://localhost:")
            corpo = ""
            for _ in range(40):
                try:
                    corpo = urllib.request.urlopen(info["url"], timeout=3).read().decode()
                    break
                except OSError:
                    time.sleep(0.5)
            assert "dentro do container" in corpo, saida
        finally:
            shell.stop_server(nome)
    vivos = __import__("subprocess").run(sandbox.prefixo("wsl") + ["ps", "--format", "{{.Names}}"],
                                          capture_output=True, text=True).stdout.split()
    assert not set(containers) & set(vivos)  # serve_stop derrubou os containers


@pytest.mark.skipif(not _docker_pronto(sandbox.IMAGEM_PYTHON, "wsl"), reason="Docker do WSL parado ou sem a imagem")
def test_terminal_do_agente_no_container(tmp_path, monkeypatch):
    from app import terminal
    _wsl(monkeypatch)
    aberto = terminal.terminal_open(tmp_path, {"name": "t"})
    tid = aberto.split("'")[1]
    try:
        assert "container do sandbox" in aberto
        saida = terminal.terminal_send(tmp_path, {"id": tid, "command": "cd /workspace && id -u && ls /c 2>&1 | head -1",
                                                  "wait": 60})
        assert "exit 0" in saida and "1000" in saida and ("No such file" in saida or "cannot access" in saida)
    finally:
        terminal.terminal_close(tmp_path, {"id": tid})


@pytest.mark.skipif(not _docker_pronto(sandbox.IMAGEM_PYTHON, "wsl"), reason="Docker do WSL parado ou sem a imagem")
def test_cache_de_pacotes_num_volume_do_linux(tmp_path, monkeypatch):
    _wsl(monkeypatch)
    saida = run_tool("run_command", {"command": "stat -c %u /cache/pip && df /cache | tail -1", "timeout": 120},
                     tmp_path)
    assert "wsl" in sandbox._VOLUME_OK and "1000" in saida and "9p" not in saida and "drvfs" not in saida
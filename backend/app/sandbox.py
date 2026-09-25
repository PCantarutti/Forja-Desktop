"""Sandbox leve dos processos do agente (E12, passos 1 e 2): Job Object e ambiente limpo.

Hoje a proteção dos comandos é a aprovação (always_ask). No modo Automático ou no Maestro autônomo ela
quase some, e um `postinstall` de pacote roda com tudo o que o backend tem. Aqui, sem dependência nova:

- Ambiente limpo: o processo filho não herda chave de API, token nem as variáveis internas do Forja.
  É uma lista de bloqueio (e não de liberação) de propósito: toolchains dependem de dezenas de
  variáveis do sistema (APPDATA, ProgramFiles, PATHEXT, JAVA_HOME...) e uma lista de liberação quebraria
  builds do usuário. O projeto libera o que precisa com uma linha `env_allow:` no FORJA.md.
- Job Object (Windows): a árvore inteira do comando entra num job que morre junto quando o comando
  termina (nada de processo órfão), com limite de memória, de processos ativos e de CPU. No Linux/macOS
  (Forja Web, runner), o limite de memória vai por setrlimit.

Não isola arquivo nem rede: isso é o passo 3 (WSL/Docker) ou 4 (AppContainer) da E12.
"""
from __future__ import annotations

import contextvars
import os
import re
import subprocess
import threading
import time as _time
from pathlib import Path

from . import config, native

# Nome (em maiúsculas) que contém um destes trechos não passa para o processo filho.
BLOQUEADOS_TRECHO = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "PRIVATE", "SESSION_ID")
BLOQUEADOS_PREFIXO = ("FORJA_", "ANTHROPIC_", "OPENAI_", "AWS_", "AZURE_", "GOOGLE_APPLICATION", "HF_",
                      "OLLAMA_API", "GROQ_", "OPENROUTER_", "DEEPSEEK_", "MISTRAL_")
# Precisa passar mesmo casando com um trecho acima (KEY em "PATHEXT"? não; mas estes, sim, existem):
SEMPRE_PASSA = {"SSH_AUTH_SOCK"}  # o git do usuário por ssh usa o agente dele, não uma chave


def _env_allow(root: Path | None) -> set[str]:
    """Variáveis que o projeto libera: linha `env_allow: A, B` no FORJA.md da raiz."""
    if root is None:
        return set()
    try:
        texto = (root / config.PROJECT_MEMORY_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {v.strip().upper() for m in re.finditer(r"(?im)^\s*[-*]?\s*env_allow\s*:\s*(.+)$", texto)
            for v in m.group(1).split(",") if v.strip()}


def bloqueada(nome: str) -> bool:
    n = nome.upper()
    if n in SEMPRE_PASSA:
        return False
    return n.startswith(BLOQUEADOS_PREFIXO) or any(t in n for t in BLOQUEADOS_TRECHO)


def ambiente(root: Path | None = None, dev: bool = False) -> dict:
    """os.environ sem os segredos. `dev`: o ambiente dos servidores de dev e do terminal."""
    base = native.ambiente_dev() if dev else dict(os.environ)
    libera = _env_allow(root)
    return {k: v for k, v in base.items() if k.upper() in libera or not bloqueada(k)}


# ------------------------------------------------------------------ limites

def limites() -> dict:
    """{memoria_mb, processos, cpu}: 0 desliga cada um. Vêm das Configurações (settings.py)."""
    mem = int(getattr(config, "SANDBOX_MEMORIA_MB", 0) or 0)
    return {"memoria_mb": memoria_padrao_mb() if mem < 0 else mem,
            "processos": int(getattr(config, "SANDBOX_PROCESSOS", 0) or 0),
            "cpu": int(getattr(config, "SANDBOX_CPU", 0) or 0)}


def memoria_padrao_mb() -> int:
    """Metade da RAM, no máximo 4 GB: o PC continua usável mesmo com um build fora de controle."""
    try:
        from . import localai
        ram = localai.system_ram()[0]
    except Exception:
        ram = 0
    return min(4096, ram // 2 // 1_048_576) if ram else 4096


if native.WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                                    "OtherOperationCount", "ReadTransferCount",
                                                    "WriteTransferCount", "OtherTransferCount")]

    class _EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _CPU(ctypes.Structure):
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.c_void_p]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    _EXTENDED_INFO, _CPU_INFO = 9, 15
    _KILL_ON_CLOSE, _JOB_MEMORY, _ACTIVE_PROCESS = 0x2000, 0x200, 0x8
    _CPU_ENABLE, _CPU_HARD_CAP = 0x1, 0x4

    def _cria_job(lim: dict):
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_CLOSE
        if lim["memoria_mb"]:
            info.BasicLimitInformation.LimitFlags |= _JOB_MEMORY
            info.JobMemoryLimit = lim["memoria_mb"] * 1_048_576
        if lim["processos"]:
            info.BasicLimitInformation.LimitFlags |= _ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = lim["processos"]
        if not _k32.SetInformationJobObject(job, _EXTENDED_INFO, ctypes.byref(info), ctypes.sizeof(info)):
            _k32.CloseHandle(job)
            return None
        if lim["cpu"] and 0 < lim["cpu"] < 100:
            cpu = _CPU(_CPU_ENABLE | _CPU_HARD_CAP, lim["cpu"] * 100)  # em centésimos de porcento
            _k32.SetInformationJobObject(job, _CPU_INFO, ctypes.byref(cpu), ctypes.sizeof(cpu))
        return job

    def _pico_mb(job) -> int:
        info = _EXTENDED()
        if _k32.QueryInformationJobObject(job, _EXTENDED_INFO, ctypes.byref(info), ctypes.sizeof(info), None):
            return info.PeakJobMemoryUsed // 1_048_576
        return 0


def popen(argv: list[str], cwd, root: Path | None = None, dev: bool = False, **kw) -> subprocess.Popen:
    """subprocess.Popen com o ambiente limpo, o grupo de processos de sempre e, no Windows, dentro de um
    Job Object com os limites das Configurações. `fecha(proc)` encerra o job (e o que sobrou da árvore).

    ponytail: o processo entra no job logo depois de criado, não suspenso — um filho aberto no primeiro
    milissegundo escapa. Criar suspenso exige CreateProcess na mão; fica para quando isso aparecer."""
    lim = limites()
    kw.setdefault("env", ambiente(root or (Path(cwd) if cwd else None), dev))
    if not native.WINDOWS and lim["memoria_mb"]:
        mem = lim["memoria_mb"] * 1_048_576

        def _limita():
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        kw.setdefault("preexec_fn", _limita)
    proc = subprocess.Popen(argv, cwd=cwd, **native.popen_kwargs(), **kw)
    proc._forja_limite_mb = lim["memoria_mb"]  # type: ignore[attr-defined]
    if native.WINDOWS:
        job = _cria_job(lim)
        if job and _k32.AssignProcessToJobObject(job, int(proc._handle)):  # type: ignore[attr-defined]
            proc._forja_job = job  # type: ignore[attr-defined]
        elif job:
            _k32.CloseHandle(job)
    return proc


def aviso_limite(proc: subprocess.Popen) -> str:
    """Frase para o modelo quando o comando bateu no teto de memória do sandbox, ou ''."""
    job, limite = getattr(proc, "_forja_job", None), getattr(proc, "_forja_limite_mb", 0)
    if not (native.WINDOWS and job and limite):
        return ""
    if _pico_mb(job) >= limite * 0.95:
        return (f"\n[o comando chegou ao limite de memória do sandbox ({limite} MB) e pode ter falhado por isso; "
                "o teto fica em Configurações, no campo Sandbox: memória por comando]")
    return ""


def fecha(proc: subprocess.Popen) -> None:
    """Fecha o job: o Windows mata o que ainda estiver vivo na árvore (KILL_ON_JOB_CLOSE). Comando que
    rodou num container: o container sai junto (matar só o `docker run` deixaria ele vivo)."""
    if native.WINDOWS and (job := getattr(proc, "_forja_job", None)):
        proc._forja_job = None  # type: ignore[attr-defined]
        _k32.CloseHandle(job)
    if nome := getattr(proc, "_forja_container", None):
        proc._forja_container = None  # type: ignore[attr-defined]
        subprocess.run([*getattr(proc, "_forja_docker", ["docker"]), "rm", "-f", nome], capture_output=True,
                       timeout=60, **native.popen_kwargs())


# ------------------------------------------------------------------ passo 3: container (Docker)
# O comando do agente roda num container que só enxerga a pasta do projeto, sem root e sem rede fora da
# fase de instalação. O Forja (backend, interface, modelos) continua no Windows: só o comando vai para
# a caixa. Opcional de propósito: o Docker Desktop come RAM que um PC fraco rodando IA local não tem.
MODOS_ISOLADO = ("desligado", "autonomo", "sempre")
IMAGEM_NODE = "node:22-bookworm"      # tem git e python3; para projeto com package.json
IMAGEM_PYTHON = "python:3.12-bookworm"  # tem git, pip e compiladores; para o resto
# Instalação de dependência: a única fase com rede. Build, teste e verify rodam com --network none.
INSTALADOR = re.compile(
    r"(?:^|[;&|]\s*)(?:(?:npm|pnpm|yarn|bun)\s+(?:install|i|ci|add)\b|(?:yarn|pnpm)\s*$|"
    r"(?:python3?\s+-m\s+)?pip3?\s+install\b|uv\s+(?:sync|add|pip\s+install)\b|poetry\s+(?:install|add)\b|"
    r"pipenv\s+install\b|cargo\s+(?:fetch|build|add)\b|go\s+(?:mod\s+download|get)\b|composer\s+install\b)", re.I)
# () -> bool, posto pelo agente por execução: "esta chamada é de um modo sem aprovação por comando?"
AUTONOMO: contextvars.ContextVar = contextvars.ContextVar("forja_autonomo", default=lambda: False)
MOTORES = ("auto", "desktop", "wsl")
_DOCKER: dict[str, tuple[bool, float]] = {}   # motor -> (respondeu, quando)
_PUXANDO: set[tuple[str, str]] = set()


def modo_isolado() -> str:
    m = str(getattr(config, "SANDBOX_ISOLADO", "desligado") or "desligado")
    return m if m in MODOS_ISOLADO else "desligado"


def isolar() -> bool:
    """O comando desta chamada deve ir para o container? Pelo modo das Configurações e, no 'autonomo',
    pelo modo de permissão da execução: onde ninguém aprova cada comando (Automático, Ignorar
    permissões, Maestro). Perguntado na hora: o modo pode mudar no meio (plano aprovado)."""
    modo = modo_isolado()
    if modo == "sempre":
        return True
    if modo == "autonomo":
        try:
            return bool(AUTONOMO.get()())
        except Exception:
            return False
    return False


def prefixo(motor: str) -> list[str]:
    """Como chamar o `docker` de cada motor. desktop: o CLI do Docker Desktop no Windows. wsl: o Docker
    Engine instalado dentro de uma distro WSL, sem Docker Desktop (o WSL sobe sozinho quando chamado)."""
    if motor == "wsl":
        distro = str(getattr(config, "SANDBOX_WSL_DISTRO", "") or "").strip()
        # --exec e não --: com "--" o wsl.exe passa a linha pelo shell do Linux, que remonta os argumentos e
        # perde as aspas ($i, &&, > quebravam o comando do agente). --exec chama o docker direto.
        return ["wsl.exe", *(["-d", distro] if distro else []), "--exec", "docker"]
    return ["docker"]


def caminho(motor: str, p: Path) -> str:
    """Caminho do Windows como o daemon do motor o enxerga: no WSL, C:/x vira /mnt/c/x."""
    p = p.resolve()
    if motor == "wsl" and p.drive:
        return f"/mnt/{p.drive[0].lower()}/" + "/".join(p.parts[1:])
    return str(p)


def docker_ok(motor: str = "desktop") -> bool:
    """O daemon desse motor respondendo (cache de 30 s: `docker info` leva ~1 s). Nunca abre o Docker
    Desktop nem instala nada: sem motor, os comandos seguem no Windows com aviso."""
    agora = _time.monotonic()
    feito = _DOCKER.get(motor)
    if feito is None or agora - feito[1] > 30:
        try:
            r = subprocess.run([*prefixo(motor), "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                               timeout=30, **native.popen_kwargs())
            _DOCKER[motor] = (r.returncode == 0, agora)
        except (OSError, subprocess.TimeoutExpired):
            _DOCKER[motor] = (False, agora)
    return _DOCKER[motor][0]


def motor_ativo() -> str | None:
    """O motor que vai rodar: o escolhido nas Configurações, ou no 'auto' o Docker Desktop se estiver
    aberto e senão o Docker do WSL. None: nenhum respondendo."""
    escolhido = str(getattr(config, "SANDBOX_MOTOR", "auto") or "auto")
    ordem = ["desktop", "wsl"] if escolhido not in ("desktop", "wsl") else [escolhido]
    return next((m for m in ordem if docker_ok(m)), None)


def imagem(root: Path) -> str:
    """`sandbox_image:` no FORJA.md; senão node (há package.json) ou python."""
    try:
        texto = (root / config.PROJECT_MEMORY_FILE).read_text(encoding="utf-8", errors="replace")
        if m := re.search(r"(?im)^\s*[-*]?\s*sandbox_image\s*:\s*(\S+)", texto):
            return m.group(1)
    except OSError:
        pass
    return IMAGEM_NODE if (root / "package.json").is_file() else IMAGEM_PYTHON


def _imagem_presente(img: str, motor: str = "desktop") -> bool:
    """Cada motor tem as próprias imagens: baixada no Docker Desktop não existe no Docker do WSL."""
    r = subprocess.run([*prefixo(motor), "image", "inspect", img], capture_output=True, timeout=60,
                       **native.popen_kwargs())
    return r.returncode == 0


def _puxa(img: str, motor: str = "desktop") -> None:
    """Baixa a imagem em segundo plano, uma vez: o comando que pediu não espera o download."""
    if (img, motor) in _PUXANDO:
        return
    _PUXANDO.add((img, motor))

    def roda():
        try:
            subprocess.run([*prefixo(motor), "pull", img], capture_output=True, timeout=3600,
                           **native.popen_kwargs())
        finally:
            _PUXANDO.discard((img, motor))
    threading.Thread(target=roda, daemon=True).start()


def _cache_dir() -> Path:
    p = config.DATA_DIR / "sandbox-cache"
    for sub in ("npm", "pip", "pyuser", "home"):
        (p / sub).mkdir(parents=True, exist_ok=True)
    return p


def argv_docker(command: str, cwd: Path, root: Path, img: str, nome: str, motor: str = "desktop") -> list[str]:
    """`docker run` que executa `command` (bash) com só a pasta do projeto montada em /workspace."""
    lim = limites()
    try:
        sub = cwd.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        sub = "."
    trabalho = "/workspace" + ("" if sub in ("", ".") else f"/{sub}")
    cache = _cache_dir()
    argv = [*prefixo(motor), "run", "--rm", "-i", "--init", "--name", nome,
            "-v", f"{caminho(motor, root)}:/workspace", "-w", trabalho, "-v", f"{caminho(motor, cache)}:/cache",
            "--network", "bridge" if INSTALADOR.search(command) else "none",
            "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            # Cache de pacote persistente e pip sem root (vai para o usuário, dentro do cache).
            "-e", "HOME=/cache/home", "-e", "npm_config_cache=/cache/npm", "-e", "PIP_CACHE_DIR=/cache/pip",
            "-e", "PYTHONUSERBASE=/cache/pyuser", "-e", "PIP_USER=1",
            "-e", "PATH=/cache/pyuser/bin:/usr/local/bin:/usr/bin:/bin"]
    if lim["memoria_mb"]:
        argv += ["--memory", f"{lim['memoria_mb']}m"]
    if lim["processos"]:
        argv += ["--pids-limit", str(lim["processos"])]
    if lim["cpu"] and 0 < lim["cpu"] < 100:
        argv += ["--cpus", f"{max(0.5, (os.cpu_count() or 2) * lim['cpu'] / 100):.1f}"]
    for k in sorted(_env_allow(root)):
        if k in os.environ:
            argv += ["-e", f"{k}={os.environ[k]}"]
    return argv + [img, "bash", "-lc", command]


def plano(command: str, cwd: Path, root: Path | None) -> tuple[list[str], str, str, str]:
    """(argv, nome do container ou '', motor ou '', aviso) para rodar `command`. Fora do isolamento, ou
    sem Docker, é o shell do Windows de sempre, com um aviso quando o isolamento foi pedido e não deu."""
    if not (root and isolar()):
        return native.shell_argv(command), "", "", ""
    if not (motor := motor_ativo()):
        return (native.shell_argv(command), "", "",
                "[sandbox isolado ligado, mas nenhum Docker está rodando (nem o Desktop nem o do WSL): este "
                "comando rodou no Windows]\n")
    img = imagem(root)
    if not _imagem_presente(img, motor):
        _puxa(img, motor)
        return (native.shell_argv(command), "", "",
                f"[baixando a imagem do sandbox ({img}, Docker {motor}); até terminar, os comandos rodam no "
                "Windows]\n")
    nome = f"forja-sbx-{os.getpid()}-{_time.time_ns() % 10**12}"  # "forja-*" puro colide com o compose do forja-web
    return argv_docker(command, cwd, root, img, nome, motor), nome, motor, ""


def popen_comando(command: str, cwd: Path, root: Path | None, **kw) -> tuple[subprocess.Popen, str]:
    """Popen de um comando do agente, no Windows ou no container conforme `plano`. (proc, aviso)."""
    argv, nome, motor, aviso = plano(command, cwd, root)
    proc = popen(argv, cwd, root=root, **kw)
    if nome:
        proc._forja_container = nome  # type: ignore[attr-defined]
        proc._forja_docker = prefixo(motor)  # type: ignore[attr-defined]
    return proc, aviso


def aviso_saida(proc: subprocess.Popen) -> str:
    """No container, estouro de memória vira exit 137 (o kernel mata o processo)."""
    if getattr(proc, "_forja_container", None) and proc.returncode == 137:
        return (f"\n[o container passou do limite de memória do sandbox ({limites()['memoria_mb']} MB); o teto "
                "fica em Configurações, no campo Sandbox: memória por comando]")
    return aviso_limite(proc)


def nota_para_o_modelo() -> str:
    """Linha do contexto de execução quando os comandos vão para o container: a sintaxe muda."""
    if not isolar():
        return ""
    return ("- run_command roda num container Linux (bash, não PowerShell), com só a pasta do projeto em "
            "/workspace, sem root, e com rede só em comandos de instalação (npm install, pip install...). "
            "Use sintaxe bash. Servidores de dev (serve_start) e o terminal continuam no Windows.")

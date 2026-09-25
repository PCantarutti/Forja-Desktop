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

import os
import re
import subprocess
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
    """Fecha o job: o Windows mata o que ainda estiver vivo na árvore (KILL_ON_JOB_CLOSE)."""
    if native.WINDOWS and (job := getattr(proc, "_forja_job", None)):
        proc._forja_job = None  # type: ignore[attr-defined]
        _k32.CloseHandle(job)

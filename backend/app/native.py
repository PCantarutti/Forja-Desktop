"""Execução local: shell do sistema, árvore de processos e abrir no editor/Explorer.

O Forja Desktop roda nativo, então existe um único lugar onde comandos rodam: a máquina do usuário.
PowerShell no Windows, bash no Linux/macOS, sempre na pasta da conversa. Este módulo é o código do
antigo `tools/forja_runner.py` trazido para dentro do backend — sem HTTP, sem token, sem um segundo
processo para o usuário iniciar.

Como no Claude Desktop, a proteção é a aprovação (always_ask), não um sandbox: o shell enxerga o
sistema inteiro.
"""
from __future__ import annotations

import os
import platform
import shlex
import shutil
import signal
import subprocess
from pathlib import Path

SYSTEM = platform.system()  # Windows | Linux | Darwin
WINDOWS = SYSTEM == "Windows"
PS_PREAMBLE = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; "
               "$ErrorActionPreference='Continue'; ")


def shell_name() -> str:
    if WINDOWS:
        return "pwsh" if shutil.which("pwsh") else "powershell"
    return "bash"


def shell_argv(command: str) -> list[str]:
    """Argv para rodar `command` uma vez e sair, propagando o código de saída."""
    if WINDOWS:
        # Exit code: comandos nativos deixam $LASTEXITCODE; cmdlets que falham deixam $? falso.
        script = PS_PREAMBLE + command + "\nif ($LASTEXITCODE) { exit $LASTEXITCODE } elseif (-not $?) { exit 1 }"
        return [shell_name(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
    return ["bash", "-lc", command]


def quote(value: str) -> str:
    """Um valor como argumento literal do shell: sem interpolação, sem virar outro comando.

    No PowerShell a aspa simples não interpola nada (a dupla avalia `$(...)` e crase), e a própria
    aspa simples se escapa dobrando. No bash, `shlex.quote` faz o mesmo trabalho.
    """
    text = str(value)
    return "'" + text.replace("'", "''") + "'" if WINDOWS else shlex.quote(text)


def term_argv() -> list[str]:
    """Argv do shell interativo do terminal da UI (sem PTY: lê comandos do stdin)."""
    if WINDOWS:
        return [shell_name(), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]
    return ["bash", "-l"]


def ambiente_dev() -> dict:
    """Ambiente dos servidores de desenvolvimento e do Terminal. O Vite (5.4.12+/6) recusa pedido cujo Host
    não é localhost ("Blocked request. This host is not allowed"): pela tailnet o celular chega como
    pc.<tailnet>.ts.net e a página abria em branco. Esta variável acrescenta o host sem mexer no projeto."""
    return {**os.environ, "__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS": ".ts.net"}


def popen_kwargs() -> dict:
    """Grupo próprio de processos, para matar a árvore inteira, e sem janela de console no Windows."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """Mata o processo e tudo o que ele abriu (npm -> node, venv -> python...)."""
    if proc.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def decode(raw: bytes) -> str:
    """Saída do processo como texto, com quebras de linha do Windows normalizadas."""
    return raw.decode("utf-8", "replace").replace("\r\n", "\n")


# ------------------------------------------------------------------ ambiente (para o system prompt e o painel)

def version(cmd: str) -> str | None:
    """Pelo shell: no Windows, npm/npx são .cmd e não resolvem sem ele."""
    try:
        r = subprocess.run(shell_argv(cmd), capture_output=True, timeout=15, **popen_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    line = decode(r.stdout or r.stderr).strip().splitlines()
    return line[0][:60] if r.returncode == 0 and line else None


_INFO: dict | None = None
TOOLS = {"node": "node --version", "npm": "npm --version", "python": "python --version", "git": "git --version"}


def info(force: bool = False) -> dict:
    """Sistema, shell e versões do que o agente costuma chamar. Cacheado: sondar custa ~1 s."""
    global _INFO
    if _INFO is None or force:
        _INFO = {"system": SYSTEM, "release": platform.release(), "machine": platform.machine(),
                 "shell": shell_name(), "home": str(Path.home()),
                 "versions": {k: version(v) for k, v in TOOLS.items()}}
    return _INFO


def describe(i: dict | None = None) -> str:
    """Uma linha para o painel e o prompt: 'Windows 11 (powershell)'."""
    i = i or info()
    system = {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(i.get("system"), i.get("system"))
    return " ".join(x for x in (system, i.get("release") or "") if x) + f" ({i.get('shell')})"


# ------------------------------------------------------------------ abrir no editor / revelar

# Documento e mídia abrem no programa do sistema (Word, Excel, leitor de PDF). O VS Code só faz
# sentido para o que é texto — abrir um .docx nele mostra XML zipado, que não serve para ninguém.
DO_SISTEMA = {".docx", ".xlsx", ".xlsm", ".pptx", ".pdf", ".odt", ".ods", ".odp", ".csv",
              ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".webm", ".zip"}


def open_path(path: str, mode: str) -> str:
    if not os.path.exists(path):
        raise ValueError(f"Caminho não existe: {path}")
    if mode == "reveal":
        if WINDOWS:
            # As aspas vão em volta do CAMINHO, não do argumento inteiro: o Popen citaria
            # "/select,C:/pasta com espaco/x.pdf" de uma vez, o explorer não entenderia e abriria a
            # pasta padrão (Documentos) — que é exatamente o que acontecia com pasta com espaço.
            # String (não lista) e sem shell: no Windows o Popen manda a linha direto para o
            # CreateProcess, sem o list2cmdline no meio e sem interpretador nenhum.
            alvo = os.path.normpath(path).replace(chr(34), "")
            subprocess.Popen('explorer /select,' + chr(34) + alvo + chr(34))
        elif SYSTEM == "Darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
        return "revelado"
    # editor: VS Code para texto; documento e mídia vão para o programa padrão do sistema
    if Path(path).suffix.lower() not in DO_SISTEMA and (shutil.which("code") or (WINDOWS and shutil.which("code.cmd"))):
        subprocess.Popen(shell_argv(f"code {quote(path)}"), **popen_kwargs())
        return "code"
    if WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
        return "padrão"
    subprocess.Popen(["open" if SYSTEM == "Darwin" else "xdg-open", path])
    return "padrão"


def _adaptadores() -> list[dict]:
    """As placas que o DXGI enumera: nome, fabricante (VendorId do PCI), VRAM dedicada e o LUID. [] fora do Windows."""
    if not WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes as W

    class LUID(ctypes.Structure):
        _fields_ = [("Low", W.DWORD), ("High", W.LONG)]

    class DESC(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", W.UINT), ("DeviceId", W.UINT),
                    ("SubSysId", W.UINT), ("Revision", W.UINT), ("Dedicated", ctypes.c_size_t),
                    ("DedicatedSys", ctypes.c_size_t), ("Shared", ctypes.c_size_t), ("Luid", LUID)]

    class GUID(ctypes.Structure):
        _fields_ = [("a", W.DWORD), ("b", W.WORD), ("c", W.WORD), ("d", ctypes.c_ubyte * 8)]

    iid = GUID(0x7b7166ec, 0x21c7, 0x44ae, (ctypes.c_ubyte * 8)(0xb2, 0x1a, 0xc9, 0xae, 0x32, 0x1a, 0xe3, 0x69))
    fab = ctypes.c_void_p()
    if ctypes.windll.dxgi.CreateDXGIFactory(ctypes.byref(iid), ctypes.byref(fab)):
        return []

    def metodo(obj, i, *tipos):
        vt = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *tipos)(vt[i])

    out = []
    i = 0
    while True:
        ad = ctypes.c_void_p()
        if metodo(fab, 7, W.UINT, ctypes.c_void_p)(fab, i, ctypes.byref(ad)):  # EnumAdapters
            break
        d = DESC()
        metodo(ad, 8, ctypes.c_void_p)(ad, ctypes.byref(d))  # GetDesc
        out.append({"nome": d.Description, "vendor": d.VendorId, "vram": d.Dedicated,
                    "luid": f"luid_0x{d.Luid.High & 0xffffffff:08x}_0x{d.Luid.Low:08x}"})
        metodo(ad, 2)(ad)  # Release
        i += 1
    metodo(fab, 2)(fab)
    return out


def placas() -> list[dict]:
    """As GPUs da máquina ({nome, vendor, vram}), da de mais VRAM dedicada para a de menos: a integrada e as
    virtuais (Parsec, Microsoft Basic Render) ficam no fim sozinhas, têm pouca ou nenhuma."""
    return sorted(({k: a[k] for k in ("nome", "vendor", "vram")} for a in _adaptadores()), key=lambda a: a["vram"], reverse=True)


def vram_em_uso() -> dict[str, int]:
    """Nome do adaptador -> VRAM dedicada em uso no sistema todo (bytes). {} fora do Windows.

    É o número do Gerenciador de Tarefas: contador de desempenho "GPU Adapter Memory", ligado ao nome
    da placa pelo LUID que o DXGI informa. O `--list-devices` do llama.cpp (Vulkan) não serve para
    isso: ele não enxerga a memória de outros processos, e com um modelo de 8 GB na placa ainda dizia
    que ela estava livre.
    """
    if not WINDOWS:
        return {}
    import ctypes
    from ctypes import wintypes as W

    nomes = {a["luid"]: a["nome"] for a in _adaptadores()}
    if not nomes:
        return {}

    pdh = ctypes.windll.pdh
    q, c = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(q)):
        return {}
    try:
        if pdh.PdhAddEnglishCounterW(q, "\\GPU Adapter Memory(*)\\Dedicated Usage", None, ctypes.byref(c)):
            return {}
        pdh.PdhCollectQueryData(q)

        class VAL(ctypes.Structure):
            _fields_ = [("status", W.DWORD), ("valor", ctypes.c_longlong)]

        class ITEM(ctypes.Structure):
            _fields_ = [("nome", ctypes.c_wchar_p), ("v", VAL)]

        tam, n = W.DWORD(0), W.DWORD(0)
        pdh.PdhGetFormattedCounterArrayW(c, 0x400, ctypes.byref(tam), ctypes.byref(n), None)
        buf = ctypes.create_string_buffer(tam.value)
        if pdh.PdhGetFormattedCounterArrayW(c, 0x400, ctypes.byref(tam), ctypes.byref(n), buf):
            return {}
        itens = ctypes.cast(buf, ctypes.POINTER(ITEM))
        uso: dict[str, int] = {}
        for k in range(n.value):
            inst = itens[k].nome.lower()
            nome = next((v for luid, v in nomes.items() if inst.startswith(luid)), None)
            if nome:
                uso[nome] = uso.get(nome, 0) + itens[k].v.valor
        return uso
    finally:
        pdh.PdhCloseQuery(q)

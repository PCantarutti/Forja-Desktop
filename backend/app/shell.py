"""Ferramentas de shell: run_command e servidores em segundo plano (serve_start/serve_status/serve_stop).

Tudo roda na máquina do usuário, na pasta da conversa, com o shell dele (PowerShell no Windows,
bash no Linux/macOS) — ver native.py. Como no Claude Desktop, a proteção é a aprovação
(always_ask), não um sandbox: o shell enxerga o sistema inteiro.
"""
from __future__ import annotations

import contextvars
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from . import config, native
from .tools import Tool, ToolError, register, resolve_path

MAX_OUTPUT = 20_000
LOG_DIR = Path(tempfile.gettempdir()) / "forja-serve"
_SERVERS: dict[str, dict] = {}  # nome -> {proc, log, command, cwd, started}
_servers_lock = threading.Lock()
# Saída ao vivo: o agente define um sink por chamada e cada linha do comando vira evento na UI.
OUTPUT_SINK: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar("forja_output_sink", default=None)
# Conversa do turno atual: fica gravada no processo para a aba Instâncias separar por conversa.
CONV: contextvars.ContextVar[str] = contextvars.ContextVar("forja_conv", default="")


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n\n... ({len(text) - MAX_OUTPUT} caracteres omitidos) ...\n\n{text[-half:]}"


def _execute(command: str, cwd: Path, timeout: int, sink: Callable[[str], None] | None) -> tuple[int, str, bool]:
    """Roda e devolve (exit code, saída, estourou o timeout). Lê linha a linha para a UI mostrar ao vivo."""
    chunks: list[str] = []
    timed_out = threading.Event()
    # `with`: no caminho do timeout o pipe ficava aberto, um descritor por comando estourado.
    with subprocess.Popen(native.shell_argv(command), cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **native.popen_kwargs()) as p:
        def _kill():
            timed_out.set()
            native.kill_tree(p)

        timer = threading.Timer(timeout, _kill)
        timer.start()
        try:
            assert p.stdout
            for raw in p.stdout:
                line = native.decode(raw)
                chunks.append(line)
                if sink:
                    sink(line)
            p.wait()
        finally:
            timer.cancel()
    return p.returncode, "".join(chunks), timed_out.is_set()


def exec_in(root: Path, command: str, timeout: int = 60) -> tuple[int, str]:
    """Roda `command` na pasta e devolve (exit code, saída). Sem ToolError: para gitops e hooks."""
    code, out, timed_out = _execute(command, root, timeout, None)
    return (124, f"Timeout ({timeout}s)") if timed_out else (code, out)


# ------------------------------------------------------------------ run_command

def background(root: Path, args: dict) -> str:
    """Comando demorado (build, suíte de teste) vira processo em segundo plano, o mesmo mecanismo dos
    servidores: o turno não fica preso e o modelo acompanha com serve_status."""
    nome = _safe_name(str(args.get("name") or "").strip() or args["command"].split()[0])
    texto = serve_start(root, {**args, "name": nome}, kind="Processo")
    if _info(nome)["alive"]:  # já terminou dentro da espera do serve_start: o resultado está no texto
        _vigia(nome)
    return texto


def _primeiro_plano(command: str, cwd: Path, timeout: int, sink, nome: str) -> tuple[int | None, str]:
    """Roda gravando num log. Terminou no prazo: (exit code, saída). Não terminou: (None, saída até
    aqui) e o processo SEGUE vivo, registrado como processo em segundo plano `nome`.

    Do DeepSeek Harness: comando que passa do timeout não é morto, vira job. Matar jogava fora um
    build ou uma instalação quase pronta, e o modelo rodava tudo de novo com timeout maior.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"fg-{nome}-{time.time_ns()}.log"
    fh = open(log, "wb")
    proc = subprocess.Popen(native.shell_argv(command), cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **native.popen_kwargs())
    lidos, resto, pedacos = 0, b"", []
    limite = time.monotonic() + timeout

    def puxa() -> None:
        nonlocal lidos, resto
        with open(log, "rb") as f:
            f.seek(lidos)
            novo = f.read()
        lidos += len(novo)
        *linhas, resto = (resto + novo).split(b"\n")
        for raw in linhas:
            linha = native.decode(raw + b"\n")
            pedacos.append(linha)
            if sink:
                sink(linha)

    while proc.poll() is None and time.monotonic() < limite:
        time.sleep(0.2)
        puxa()
    puxa()
    if proc.poll() is None:  # passou do prazo: vira processo de fundo, com o mesmo log
        with _servers_lock:
            if nome in _SERVERS:
                _drop(_SERVERS.pop(nome))
            _SERVERS[nome] = {"proc": proc, "log": str(log), "fh": fh, "command": command, "cwd": str(cwd),
                              "started": time.time() - timeout, "conv": CONV.get(), "kind": "Processo"}
        _vigia(nome)
        return None, "".join(pedacos)
    fh.close()
    if resto:
        pedacos.append(native.decode(resto))
    try:
        log.unlink()
    except OSError:
        pass
    return proc.returncode, "".join(pedacos)


def run_command(root: Path, args: dict) -> str:
    command = args["command"].strip()
    if not command:
        raise ToolError("command vazio.")
    if args.get("background"):
        return background(root, args)
    if parece_servidor(command):
        # Esperaria o timeout inteiro e voltaria como erro (no StockFlow, 180 s por tentativa).
        raise ToolError("Esse comando sobe um servidor que não termina. Use serve_start (ele reaproveita "
                        "um servidor igual que já esteja rodando) e serve_status para ver o que está de pé.")
    cwd = resolve_path(root, args.get("cwd"))
    timeout = max(1, min(int(args.get("timeout") or 60), config.SHELL_TIMEOUT_MAX))
    nome = _safe_name(str(args.get("name") or "").strip() or command.split()[0])
    code, out = _primeiro_plano(command, cwd, timeout, OUTPUT_SINK.get(), nome)
    if code is None:
        return (f"[ainda rodando após {timeout}s; movido para o processo em segundo plano '{nome}']\n"
                "O comando continua rodando. Você recebe um aviso quando ele terminar; enquanto isso siga com o "
                f"que não depende dele. serve_status(name='{nome}') mostra o log, serve_stop encerra.\n"
                f"Saída até aqui:\n{_truncate(out) or '(sem saída)'}")
    body = f"exit code: {code}\n{_truncate(out) or '(sem saída)'}"
    if code != 0:
        raise ToolError(body)
    return body


# ------------------------------------------------------------------ aviso de término
# Quem ligou o comando recebe um aviso quando ele termina (DeepSeek Harness: background job notice),
# sem precisar ficar consultando. O agente define o destino por execução.
AO_TERMINAR: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "forja_ao_terminar", default=None)


def _vigia(nome: str) -> None:
    destino = AO_TERMINAR.get()
    if destino is None:
        return
    with _servers_lock:
        s = _SERVERS.get(nome)
    if not s:
        return
    proc = s["proc"]

    def espera() -> None:
        code = proc.wait()
        with _servers_lock:
            if _SERVERS.get(nome, {}).get("proc") is not proc:  # reiniciado ou encerrado por serve_stop
                return
        destino(f"O processo em segundo plano '{nome}' terminou [código de saída: {code}]. "
                f"Leia o resultado com serve_status(name='{nome}').")

    threading.Thread(target=espera, daemon=True, name=f"vigia-{nome}").start()


def command_preview(root: Path, args: dict) -> dict:
    cwd = resolve_path(root, args.get("cwd"))
    return {"kind": "command", "path": str(cwd), "text": args.get("command", "")}


# ------------------------------------------------------------------ servidores em segundo plano

def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (name or "").strip())[:40] or "server"


def _start(name: str, command: str, cwd: Path) -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _servers_lock:
        if name in _SERVERS:
            _drop(_SERVERS.pop(name))  # mesmo nome = reinicia
        log = LOG_DIR / f"{name}.log"
        fh = open(log, "wb")  # fechado em _drop: sem guardar o handle, vazava um descritor por servidor
        proc = subprocess.Popen(native.shell_argv(command), cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **native.popen_kwargs())
        _SERVERS[name] = {"proc": proc, "log": str(log), "fh": fh, "command": command, "cwd": str(cwd),
                          "started": time.time(), "conv": CONV.get()}
    return _info(name)


def _drop(s: dict) -> None:
    """Encerra o processo e fecha o arquivo de log dele."""
    native.kill_tree(s["proc"])
    try:
        s["fh"].close()
    except (OSError, KeyError):
        pass


def _info(name: str) -> dict:
    s = _SERVERS[name]
    code = s["proc"].poll()
    return {"name": name, "pid": s["proc"].pid, "alive": code is None, "exit_code": code, "command": s["command"],
            "cwd": s["cwd"], "log": s["log"], "uptime": int(time.time() - s["started"]),
            "conv": s.get("conv") or "", "url": url_do_log(name) if code is None else ""}


def _log(name: str, tail: int) -> str:
    try:
        lines = Path(_SERVERS[name]["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, KeyError):
        return ""
    return "\n".join(lines[-max(1, min(int(tail or 40), 500)):])


# Comando que sobe servidor de desenvolvimento e não termina. Rodado por run_command (ou como comando
# de verificação de tarefa) ele só acaba no timeout, e a tarefa vira falha sem ter falhado.
SERVIDOR_DEV = re.compile(
    r"(?:^|[;&|]\s*)(?:npm\s+(?:run\s+)?(?:dev|start|serve|preview)|pnpm\s+(?:run\s+)?(?:dev|start)|"
    r"yarn\s+(?:run\s+)?(?:dev|start)|npx\s+(?:vite|next\s+dev|serve)\b(?!\s+build)|vite(?:\s+(?!build)|\s*$)|"
    r"next\s+dev|python\d?\s+-m\s+http\.server|flask\s+run|uvicorn\s|php\s+-S)", re.I)
# sem ) ] > aspas e vírgula no fim: o http.server anuncia "(http://127.0.0.1:8000/) ..."
URL_NO_LOG = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):\d+[^\s)\]>'\",]*")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def parece_servidor(command: str) -> bool:
    return bool(SERVIDOR_DEV.search(command or ""))


def url_do_log(name: str) -> str:
    """URL que o servidor anunciou ("Local: http://localhost:5174/"). '' se ainda não anunciou."""
    achadas = URL_NO_LOG.findall(ANSI.sub("", _log(name, 200)))
    return achadas[-1].rstrip("/") if achadas else ""


def serve_start(root: Path, args: dict, kind: str = "Servidor") -> str:
    name = _safe_name(str(args.get("name") or "server"))
    command = str(args["command"]).strip()
    if not command:
        raise ToolError("command vazio.")
    cwd = resolve_path(root, args.get("cwd"))
    # O mesmo servidor já está de pé (mesmo comando, mesma pasta): reaproveita. Subir outro só criava
    # instâncias em portas novas (5173, 5174, 5175...) e deixava o navegador apontando para a velha.
    with _servers_lock:
        vivos = [n for n, s in _SERVERS.items() if s["command"] == command and s["cwd"] == str(cwd)
                 and s["proc"].poll() is None]
    if vivos and not args.get("restart") and kind == "Servidor":
        url = url_do_log(vivos[0])
        return (f"{kind} '{vivos[0]}' já está rodando esse comando nesta pasta"
                + (f", em {url}" if url else "") + ". Reaproveitei; nada foi reiniciado. "
                "Para reiniciar (mudou configuração, travou), chame serve_start com restart=true.")
    info = _start(name, command, cwd)
    time.sleep(2.5)  # dá tempo de o servidor imprimir a porta
    log, alive = _log(name, 30), _info(name)["alive"]
    status = "rodando" if alive else ("JÁ ENCERROU (veja o log: provável erro)" if kind == "Servidor"
                                      else "JÁ TERMINOU (o log abaixo é o resultado)")
    dica = ("Abra http://localhost:PORTA — vale para o navegador integrado e para o navegador do usuário.\n"
            if kind == "Servidor" else "")
    url = url_do_log(name) if kind == "Servidor" else ""
    if url:
        dica = f"Endereço: {url} — vale para o navegador integrado e para o navegador do usuário.\n"
    return (f"{kind} '{name}' iniciado (pid {info.get('pid')}), {status}.\n{dica}"
            f"Use serve_status(name='{name}') para acompanhar e serve_stop para encerrar.\n"
            f"--- log ---\n{log or '(vazio ainda)'}")


def list_servers() -> list[dict]:
    """Servidores vivos ou recém-encerrados.

    Sob o lock: stop_server e clear_finished mexem no dict, e ler fora dele dava KeyError
    intermitente em /api/servers e /api/activity, que rodam em thread.
    """
    with _servers_lock:
        return [_info(n) for n in list(_SERVERS)]


def server_log(name: str, tail: int = 40) -> str:
    name = _safe_name(name)
    if name not in _SERVERS:
        raise ToolError(f"Servidor '{name}' não existe.")
    return _log(name, tail)


def stop_server(name: str) -> None:
    name = _safe_name(name)
    with _servers_lock:
        s = _SERVERS.pop(name, None)
    if not s:
        raise ToolError(f"Servidor '{name}' não existe. Veja serve_status.")
    _drop(s)


def close_all() -> None:
    """Encerra tudo o que o agente subiu. Chamado no fim do backend; em dev o Electron não mata."""
    with _servers_lock:
        restantes = list(_SERVERS.values())
        _SERVERS.clear()
    for s in restantes:
        _drop(s)


WAIT_MAX = 120  # teto da espera do serve_status, para o turno nunca ficar preso


def _wait_end(name: str, seconds: int) -> None:
    """Segura a chamada até o processo sair ou o tempo acabar.

    Sem isto, acompanhar um processo em background custa uma inferência por consulta — o modelo
    pergunta, comenta, pergunta de novo. Com a espera, é um serve_status só.
    """
    limite = time.monotonic() + max(1, min(seconds, WAIT_MAX))
    while time.monotonic() < limite:
        vivo = next((s.get("alive") for s in list_servers() if s.get("name") == name), None)
        if not vivo:
            return
        time.sleep(1)


def clear_finished() -> int:
    """Tira da lista os processos que já terminaram. Só a lista: nada é encerrado aqui."""
    with _servers_lock:
        mortos = [n for n, s in list(_SERVERS.items()) if s["proc"].poll() is not None]
        for n in mortos:
            s = _SERVERS.pop(n, None)
            if s:
                _drop(s)
    return len(mortos)


def serve_status(root: Path, args: dict) -> str:
    name = args.get("name")
    tail = int(args.get("tail") or 40)
    if name and int(args.get("wait") or 0) > 0:
        _wait_end(_safe_name(str(name)), int(args["wait"]))
    entries = list_servers()
    if not entries:
        return "Nenhum servidor iniciado por serve_start nesta sessão."
    lines = []
    for s in entries:
        state = "rodando" if s.get("alive") else f"parado (exit {s.get('exit_code')})"
        lines.append(f"{'●' if s.get('alive') else '○'} {s['name']} pid {s.get('pid')} {state}: {s.get('command')}")
    if name:
        try:
            log = server_log(str(name), tail)
        except ToolError as e:
            log = str(e)
        lines += [f"--- log de {_safe_name(str(name))} (últimas {tail} linhas) ---", log or "(vazio)"]
    return "\n".join(lines)


def serve_stop(root: Path, args: dict) -> str:
    name = _safe_name(str(args["name"]))
    stop_server(name)
    return f"Servidor '{name}' encerrado."


def serve_preview(root: Path, args: dict) -> dict:
    pv = command_preview(root, args)
    pv["path"] = f"servidor '{_safe_name(str(args.get('name') or 'server'))}'  ·  " + pv["path"]
    return pv


# ------------------------------------------------------------------ registro

register(Tool(
    "run_command",
    "Executa um comando de shell na pasta da conversa, na máquina do usuário (veja o bloco Ambiente), e devolve "
    "a saída. Use para testes, scripts, git, instalar pacotes. NÃO use para servidores (fica preso até o "
    "timeout): use serve_start. Comando demorado: background=true. Sem stdin.",
    {"type": "object", "properties": {
        "command": {"type": "string", "description": "Comando (PowerShell no Windows, bash no Linux/macOS)"},
        "cwd": {"type": "string", "description": "Subpasta da pasta de trabalho. Padrão: '.'"},
        "timeout": {"type": "integer", "description": f"Segundos (padrão 60, máx {config.SHELL_TIMEOUT_MAX})"},
        "background": {"type": "boolean",
                       "description": "Roda em segundo plano e devolve na hora: use para o que passa de ~1 min "
                                      "(build, suíte de teste longa). Espere o fim com "
                                      "serve_status(name=..., wait=60)."},
        "name": {"type": "string", "description": "Apelido do processo em background, ex.: build, testes"}},
     "required": ["command"]},
    run_command, mutating=True, preview=command_preview, always_ask=True))
register(Tool(
    "serve_start",
    "Inicia um servidor de desenvolvimento em segundo plano (ex.: npm run dev, uvicorn, php artisan serve) e "
    "devolve as primeiras linhas do log. Mesmo nome reinicia.",
    {"type": "object", "properties": {
        "name": {"type": "string", "description": "Apelido curto, ex.: vite, api"},
        "command": {"type": "string"},
        "cwd": {"type": "string", "description": "Subpasta da pasta de trabalho. Padrão: '.'"}},
     "required": ["name", "command"]},
    serve_start, mutating=True, preview=serve_preview, always_ask=True))
register(Tool(
    "serve_status",
    "Lista os processos iniciados por serve_start ou por run_command(background) e, com name, as últimas "
    "linhas do log. Com `wait`, espera o processo terminar antes de responder — é assim que se acompanha "
    "algo demorado sem ficar consultando de novo.",
    {"type": "object", "properties": {
        "name": {"type": "string", "description": "Apelido para ver o log"},
        "tail": {"type": "integer", "description": "Linhas do log (padrão 40)"},
        "wait": {"type": "integer",
                 "description": f"Espera até N segundos (máx {WAIT_MAX}) o processo terminar. Precisa de name."}},
     "required": []},
    serve_status, poll=True))
register(Tool(
    "serve_stop", "Encerra um servidor iniciado por serve_start.",
    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    serve_stop, mutating=True))

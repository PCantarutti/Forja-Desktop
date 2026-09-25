"""Forja no celular: token estável para o app pareado e push pela Expo quando a IA precisa de você.

O token do Electron (config.API_TOKEN) muda a cada execução e só chega à interface pelo preload; o
celular precisa de um que sobreviva ao reinício. Ele fica num arquivo em DATA_DIR e vai para o app
pelo QR da aba Celular. Revogar = gerar outro (o app antigo passa a levar 403).

O acesso remoto é do Tailscale (`tailscale serve` na porta do backend): o backend continua em
127.0.0.1 e quem autentica o aparelho é a tailnet + este token. Em casa, opcionalmente, a rede local
(ver LAN_PORTA): o app tenta ela primeiro e cai para a tailnet.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import subprocess

import httpx

from . import config

EXPO_PUSH = "https://exp.host/--/api/v2/push/send"
# Eventos que viram notificação: a IA parada esperando você, ou o turno terminado.
AVISA = {"approval_request": "Aprovação pendente", "plan_request": "Plano para aprovar",
         "question_request": "Pergunta para você", "done": "Turno terminado"}
_tasks: set[asyncio.Task] = set()  # referência forte: task solta pode ser coletada antes de terminar


def _token_file():
    return config.DATA_DIR / "mobile_token"


def _push_file():
    return config.DATA_DIR / "mobile_push.json"


_atual: str | None = None  # lido uma vez: o middleware consulta a cada requisição


def token() -> str:
    global _atual
    if _atual is None:
        f = _token_file()
        if not f.exists():
            f.write_text(secrets.token_hex(32), encoding="utf-8")
        _atual = f.read_text(encoding="utf-8").strip()
    return _atual


def rotate() -> str:
    """Novo token e nenhum aparelho registrado: o celular antigo precisa parear de novo."""
    global _atual
    _token_file().write_text(secrets.token_hex(32), encoding="utf-8")
    _push_file().unlink(missing_ok=True)
    _atual = None
    return token()


def _defaults_file():
    return config.DATA_DIR / "mobile_defaults.json"


def lembra(escolha: dict) -> None:
    """Provedor/modelo/permissão do último turno iniciado. A escolha mora no localStorage da interface do
    desktop; o celular manda prompt com ela. Em arquivo para sobreviver ao reinício do Forja."""
    _defaults_file().write_text(json.dumps(escolha), encoding="utf-8")


def defaults() -> dict:
    try:
        return json.loads(_defaults_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def devices() -> list[str]:
    try:
        return json.loads(_push_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def register(expo_token: str) -> None:
    lista = devices()
    if expo_token not in lista:
        _push_file().write_text(json.dumps(lista + [expo_token]), encoding="utf-8")


def unregister(expo_token: str) -> None:
    _push_file().write_text(json.dumps([t for t in devices() if t != expo_token]), encoding="utf-8")


def _tailscale() -> str:
    return shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"


def expose(port: int) -> str:
    """Publica a porta de um site do agente na tailnet (`tailscale serve --https=PORTA`); devolve a URL.

    Idempotente: repetir com a mesma porta só reafirma a regra. Fica publicada até `serve --https=PORTA off`.
    """
    base = tailnet_url()
    if not base:
        raise RuntimeError("Tailscale não está ativo neste PC")
    out = subprocess.run([_tailscale(), "serve", "--bg", f"--https={port}", f"http://127.0.0.1:{port}"],
                         capture_output=True, text=True, timeout=15,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode:
        raise RuntimeError((out.stderr or out.stdout).strip() or "tailscale serve falhou")
    return f"{base}:{port}"


def tailnet_url() -> str | None:
    """https://<pc>.<tailnet>.ts.net pelo `tailscale status`; None sem Tailscale instalado/logado.

    A porta do Forja (443 → backend) o usuário publica uma vez; as dos sites saem por `expose`.
    """
    try:
        out = subprocess.run([_tailscale(), "status", "--json"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        dns = json.loads(out.stdout)["Self"]["DNSName"].rstrip(".")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None
    return f"https://{dns}" if dns else None


# ── Rede local ────────────────────────────────────────────────────────────────────────────────────
# Em casa o celular fala direto com o PC, sem ligar a VPN: um 2º uvicorn no mesmo processo (e no mesmo
# loop, porque os Runs usam asyncio.Condition do loop principal) escuta em 0.0.0.0:LAN_PORTA. O app do
# desktop continua em 127.0.0.1. Na LAN não há quem autentique o aparelho além do token, então o porteiro
# exige o token em TODA requisição — inclusive nas rotas que o desktop serve sem token (/api/files,
# imagens, /export) — e só serve /api. HTTP sem TLS: o token trafega aberto, aceitável na rede de casa.
LAN_PORTA = 47811
_lan: dict = {}  # {"server": uvicorn.Server, "task": asyncio.Task} enquanto ligado


def _lan_file():
    return config.DATA_DIR / "mobile_lan"


def lan_quer() -> bool:
    """A escolha da aba Celular (arquivo existe = ligado); sobrevive ao reinício."""
    return _lan_file().exists()


def lan_ip() -> str | None:
    """IP desta máquina na rede local (a interface da rota padrão). Nada de 100.64/10 (Tailscale)."""
    import ipaddress
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # UDP não manda pacote: só escolhe a interface
            ip = s.getsockname()[0]
    except OSError:
        return None
    end = ipaddress.ip_address(ip)
    return ip if end.is_private and end not in ipaddress.ip_network("100.64.0.0/10") else None


def lan_url() -> str | None:
    ip = lan_ip()
    return f"http://{ip}:{LAN_PORTA}" if _lan and ip else None


def _porteiro(app):
    """ASGI: só /api e só com o token do celular (header x-forja-token ou ?t=, para <Image> e vídeo)."""
    from urllib.parse import parse_qs

    async def asgi(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            dado = headers.get(b"x-forja-token", b"").decode() or \
                parse_qs(scope.get("query_string", b"").decode()).get("t", [""])[0]
            if not scope["path"].startswith("/api/") or not secrets.compare_digest(dado, token()):
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"detail":"Token ausente ou invalido"}'})
                return
            if b"x-forja-token" not in headers:  # veio por ?t=: o middleware do app confere o header
                scope = {**scope, "headers": [*scope["headers"], (b"x-forja-token", dado.encode())]}
        await app(scope, receive, send)
    return asgi


async def liga_lan(app) -> None:
    """Sobe o listener da LAN (idempotente). Porta ocupada = fica desligado e loga."""
    if _lan:
        return
    import contextlib
    import socket
    import uvicorn
    # O socket sai daqui: com a porta ocupada o uvicorn faria sys.exit(1), e SystemExit numa task derruba
    # o backend inteiro. Sem SO_REUSEADDR: no Windows ele deixaria dois processos na mesma porta.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", LAN_PORTA))
    except OSError as e:
        sock.close()
        print(f"Forja: rede local indisponível — porta {LAN_PORTA}: {e}", flush=True)
        return
    server = uvicorn.Server(uvicorn.Config(_porteiro(app), lifespan="off", log_level="warning", access_log=False))
    server.capture_signals = contextlib.nullcontext  # Ctrl+C/SIGTERM são do servidor principal
    task = asyncio.get_running_loop().create_task(server.serve(sockets=[sock]))
    _lan.update(server=server, task=task)


async def desliga_lan() -> None:
    if not _lan:
        return
    _lan["server"].should_exit = True
    await asyncio.gather(_lan["task"], return_exceptions=True)
    _lan.clear()


async def define_lan(app, ligado: bool) -> None:
    if ligado:
        _lan_file().write_text("1", encoding="utf-8")
        await liga_lan(app)
    else:
        _lan_file().unlink(missing_ok=True)
        await desliga_lan()


def _mensagem(ev: dict, conv_id: int, run_id: str) -> dict:
    call = ev.get("call") or {}
    corpo = {"approval_request": f"{call.get('name', 'ferramenta')} quer rodar",
             "question_request": str(ev.get("question") or "")}.get(ev["type"], "")
    # Só dados: quem desenha a notificação é o app (src/revoga.ts), com o call_id como identificador. Com
    # título, o Firebase desenhava sozinho com o app em segundo plano e perdia o `data` — e aí não havia como
    # tirar a notificação quando o pedido fosse decidido no desktop (revoga).
    return {"priority": "high", "_contentAvailable": True,
            "data": {"forja": "mostra", "titulo": AVISA[ev["type"]], "texto": corpo[:180] or "Toque para abrir a conversa",
                     "conv_id": conv_id, "run_id": run_id, "call_id": call.get("id")}}


async def _enviar(msgs: list[dict]) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(EXPO_PUSH, json=msgs)
    except httpx.HTTPError as e:  # ponytail: sem fila/retry; push perdido não trava o run
        print(f"Forja: push para o celular falhou: {e}", flush=True)


def revoga(call_ids: list[str]) -> None:
    """Push só de dados (sem título, não aparece): o app, até fechado, tira as notificações desses pedidos."""
    if not call_ids or not (alvos := devices()):
        return
    msg = {"data": {"forja": "revoga", "call_ids": call_ids}, "priority": "high", "_contentAvailable": True}
    task = asyncio.get_running_loop().create_task(_enviar([{**msg, "to": t} for t in alvos]))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def notify(ev: dict, conv_id: int, run_id: str) -> None:
    """Chamado a cada evento publicado; só age nos de AVISA e com aparelho registrado."""
    if ev.get("type") not in AVISA or not (alvos := devices()):
        return
    msg = _mensagem(ev, conv_id, run_id)
    task = asyncio.get_running_loop().create_task(_enviar([{**msg, "to": t} for t in alvos]))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

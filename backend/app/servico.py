"""O backend sem janela: sobe junto com o Windows, antes de qualquer login, para as automações do Conteúdo
rodarem com o PC recém-ligado e ainda na tela de bloqueio.

    python -m app.servico [--data PASTA] [--port 47810]

Quem chama é a tarefa agendada "Forja Automático" (instalar-servico.ps1, botão em Conteúdo › Ajustes). Ele grava
`servico.json` (pid, porta, token) na pasta de dados; o Electron, quando abre, lê esse arquivo: com o serviço no
meio de uma produção, a janela usa este mesmo backend; ocioso, pede para ele sair e sobe o seu. Nunca dois
backends no mesmo forja.db — a `trava()` garante isso até para quem não passou por aqui.

Sem automação ligada, o serviço sai sozinho (talvez_sair, chamado pela agenda a cada tique).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

ARQUIVO = "servico.json"
TRAVA = "backend.lock"
INICIO = time.monotonic()
CARENCIA = 300   # segundos no ar antes de poder sair por falta de automação

_trava = None   # o arquivo aberto e travado vive enquanto o processo viver


def eh_servico() -> bool:
    return os.getenv("FORJA_SERVICO") == "1"


def trava(pasta: Path) -> None:
    """Trava exclusiva da pasta de dados. Um segundo backend na mesma pasta não sobe (dois uvicorn no mesmo SQLite
    já corromperam o banco). No Windows a trava some sozinha quando o processo morre, mesmo morto à força."""
    global _trava
    if _trava is not None or "pytest" in sys.modules:
        return
    f = open(pasta / TRAVA, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RuntimeError(f"Outro backend do Forja já está usando {pasta} (o serviço sem janela ou outra instância).")
    _trava = f


def sair() -> None:
    """Encerra como um Ctrl+C: o uvicorn roda o desligamento (descarrega o modelo, mata os filhos)."""
    log.info("servico: saindo")
    signal.raise_signal(signal.SIGINT)


def talvez_sair() -> None:
    from . import conteudo_agenda
    if eh_servico() and time.monotonic() - INICIO > CARENCIA and not conteudo_agenda.ligadas() \
            and not conteudo_agenda.ocupado():
        sair()


def estado() -> dict:
    from . import conteudo_agenda
    return {"servico": eh_servico(), "ocupado": conteudo_agenda.ocupado(), "pid": os.getpid()}


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Forja sem janela (automações do Conteúdo)")
    ap.add_argument("--data", default=os.getenv("FORJA_DATA") or str(Path(os.environ.get("APPDATA", Path.home())) / "Forja"))
    ap.add_argument("--port", type=int, default=int(os.getenv("FORJA_PORT") or 47810))
    args = ap.parse_args()

    pasta = Path(args.data)
    pasta.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    raiz = Path(__file__).resolve().parents[2]   # backend/app/servico.py -> raiz do app (ou do repo, em dev)
    os.environ.update(FORJA_DATA=str(pasta), FORJA_TOKEN=token, FORJA_PORT=str(args.port), FORJA_SERVICO="1",
                      PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    for web in (raiz / "web", raiz / "frontend" / "dist"):
        if "FORJA_WEB" not in os.environ and (web / "index.html").exists():
            os.environ["FORJA_WEB"] = str(web)
    for nav in (raiz / "ms-playwright", raiz / "resources" / "ms-playwright"):
        if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and nav.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(nav)

    logs = pasta / "logs"
    logs.mkdir(exist_ok=True)
    saida = open(logs / "servico.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = saida
    print(f"\n--- servico {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} porta {args.port}", flush=True)

    # antes de abrir o banco. Pelo módulo `app.servico` (este arquivo roda como __main__): é o mesmo que o lifespan
    # do app chama, e ele vê que a trava já é deste processo
    from app import servico
    servico.trava(pasta)
    info = pasta / ARQUIVO
    info.write_text(json.dumps({"pid": os.getpid(), "port": args.port, "token": token}), encoding="utf-8")
    try:
        import uvicorn
        uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, access_log=False)
    finally:
        try:
            if json.loads(info.read_text(encoding="utf-8")).get("pid") == os.getpid():
                info.unlink()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    _main()

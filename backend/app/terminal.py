"""Terminal do usuário na UI: um shell por sessão, na pasta da conversa.

Sem PTY: é um shell lendo comandos do stdin e escrevendo no stdout (PowerShell `-Command -` ou
bash). Programas interativos de tela cheia não funcionam; comandos comuns, sim. A UI mostra o
prompt por conta própria e faz polling da saída (`poll` espera até 20 s por novidade).
"""
from __future__ import annotations

import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import native
from .tools import ToolError

POLL_WAIT = 20.0
MAX_BUFFER = 400_000


class Term:
    """Shell do sistema lendo do stdin; um thread copia o stdout para o buffer."""

    def __init__(self, cwd: Path):
        self.proc = subprocess.Popen(native.term_argv(), cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, **native.popen_kwargs())
        self.buf = ""
        self.cond = threading.Condition()
        if native.WINDOWS:  # saída em UTF-8 e sem barra de progresso quebrando o texto
            self.write(native.PS_PREAMBLE + "$ProgressPreference='SilentlyContinue'")
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self.proc.stdout
        while True:
            chunk = self.proc.stdout.read1(4096) if hasattr(self.proc.stdout, "read1") else self.proc.stdout.read(1)
            if not chunk:
                break
            with self.cond:
                self.buf = (self.buf + native.decode(chunk))[-MAX_BUFFER:]
                self.cond.notify_all()
        with self.cond:
            self.cond.notify_all()

    def write(self, text: str) -> None:
        if self.proc.poll() is not None or not self.proc.stdin:
            raise ToolError("O shell deste terminal já encerrou. Abra outro.")
        self.proc.stdin.write((text.rstrip("\n") + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def poll(self, cursor: int) -> dict:
        deadline = time.monotonic() + POLL_WAIT
        with self.cond:
            while len(self.buf) <= cursor and self.proc.poll() is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
            text = self.buf[cursor:] if cursor < len(self.buf) else ""
            return {"text": text, "cursor": len(self.buf), "alive": self.proc.poll() is None}

    def close(self) -> None:
        native.kill_tree(self.proc)


SESSIONS: dict[str, Term] = {}


def start(root: Path) -> dict:
    tid = uuid.uuid4().hex[:12]
    SESSIONS[tid] = Term(root)
    return {"id": tid, "where": "local", "cwd": str(root), "shell": native.shell_name()}


def _get(tid: str) -> Term:
    s = SESSIONS.get(tid)
    if not s:
        raise ToolError("Terminal não existe (o backend pode ter reiniciado). Abra outro.")
    return s


def send(tid: str, text: str) -> None:
    _get(tid).write(text)


def poll(tid: str, cursor: int) -> dict:
    return _get(tid).poll(cursor)


def close(tid: str) -> None:
    s = SESSIONS.pop(tid, None)
    if s:
        s.close()

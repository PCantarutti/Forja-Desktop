"""Terminal do usuário na UI: um shell por sessão, na pasta da conversa.

Sem PTY: é um shell lendo comandos do stdin e escrevendo no stdout (PowerShell `-Command -` ou
bash). Programas interativos de tela cheia não funcionam; comandos comuns, sim. A UI mostra o
prompt por conta própria e faz polling da saída (`poll` espera até 20 s por novidade).
"""
from __future__ import annotations

import codecs
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
        # Total já escrito desde o início, não o tamanho do buffer: é ele que vira o cursor do
        # cliente. Com `len(buf)` o cursor empacava em MAX_BUFFER assim que o buffer saturava e
        # `len(buf) <= cursor` virava sempre verdade — o terminal congelava de vez.
        self.written = 0
        self.lock = threading.Lock()  # dois POST de input não podem intercalar no stdin do shell
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
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
            # Decodificador incremental: caractere multibyte partido entre duas leituras do pipe
            # virava U+FFFD com o decode por chunk.
            texto = self.decoder.decode(chunk).replace(chr(13) + chr(10), chr(10))
            if not texto:
                continue
            with self.cond:
                self.written += len(texto)
                self.buf = (self.buf + texto)[-MAX_BUFFER:]
                self.cond.notify_all()
        with self.cond:
            self.cond.notify_all()

    def write(self, text: str) -> None:
        if self.proc.poll() is not None or not self.proc.stdin:
            raise ToolError("O shell deste terminal já encerrou. Abra outro.")
        with self.lock:
            self.proc.stdin.write((text.rstrip(chr(10)) + chr(10)).encode("utf-8"))
            self.proc.stdin.flush()

    def poll(self, cursor: int) -> dict:
        """Saída a partir de `cursor`, que é posição absoluta no total já escrito.

        Cliente que ficou para trás do buffer recebe o que sobrou dele, não o vazio: perde-se o
        miolo de uma saída enorme, mas o terminal continua vivo — que é o ponto.
        """
        deadline = time.monotonic() + POLL_WAIT
        with self.cond:
            while self.written <= cursor and self.proc.poll() is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
            comeco = self.written - len(self.buf)  # posição absoluta do 1º char ainda no buffer
            text = self.buf[max(0, cursor - comeco):] if cursor < self.written else ""
            return {"text": text, "cursor": self.written, "alive": self.proc.poll() is None}

    def close(self) -> None:
        native.kill_tree(self.proc)


SESSIONS: dict[str, Term] = {}


def _reap() -> None:
    """Tira da lista os shells que já morreram. A UI fecha o dela, mas recarregar a página não."""
    for tid in [t for t, s in list(SESSIONS.items()) if s.proc.poll() is not None]:
        SESSIONS.pop(tid, None)


def start(root: Path) -> dict:
    _reap()
    tid = uuid.uuid4().hex[:12]
    SESSIONS[tid] = Term(root)
    return {"id": tid, "where": "local", "cwd": str(root), "shell": native.shell_name()}


def close_all() -> None:
    """Encerra todos os shells. Chamado no fim do backend: sem isto eles só morrem com o Electron."""
    for tid in list(SESSIONS):
        close(tid)


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

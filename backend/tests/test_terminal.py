"""O cursor do terminal é posição absoluta, não índice no buffer.

O buffer é cortado em MAX_BUFFER. Enquanto o cursor era `len(buf)`, ele empacava nesse valor assim
que a saída passava do teto: `len(buf) <= cursor` virava sempre verdade, o long-poll devolvia vazio
para sempre e o terminal daquela conversa morria — bastava um log de build.
"""
import time

import pytest

from app import terminal


class FakeProc:
    """Shell de mentira: `feed` empurra bytes como se viessem do stdout."""

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


@pytest.fixture
def term(monkeypatch):
    t = terminal.Term.__new__(terminal.Term)  # sem subir shell de verdade
    import codecs
    import threading
    t.proc = FakeProc()
    t.buf = ""
    t.written = 0
    t.lock = threading.Lock()
    t.decoder = codecs.getincrementaldecoder("utf-8")("replace")
    t.cond = threading.Condition()
    return t


def alimenta(t, texto):
    with t.cond:
        t.written += len(texto)
        t.buf = (t.buf + texto)[-terminal.MAX_BUFFER:]


def test_poll_entrega_o_que_chegou(term):
    alimenta(term, "oi")
    r = term.poll(0)
    assert r["text"] == "oi" and r["cursor"] == 2


def test_poll_continua_do_cursor(term):
    alimenta(term, "abc")
    cursor = term.poll(0)["cursor"]
    alimenta(term, "def")
    r = term.poll(cursor)
    assert r["text"] == "def" and r["cursor"] == 6


def test_terminal_sobrevive_ao_estouro_do_buffer(term):
    """O bug: depois de MAX_BUFFER caracteres, todo poll voltava vazio para sempre."""
    alimenta(term, "x" * (terminal.MAX_BUFFER + 5_000))
    cursor = term.poll(0)["cursor"]
    assert cursor == terminal.MAX_BUFFER + 5_000
    alimenta(term, "depois-do-estouro")
    r = term.poll(cursor)
    assert r["text"] == "depois-do-estouro"


def test_cliente_atrasado_recebe_o_que_sobrou(term):
    """Cursor mais antigo que o buffer: devolve o começo do buffer, nunca vazio."""
    alimenta(term, "a" * 100)
    alimenta(term, "b" * terminal.MAX_BUFFER)
    r = term.poll(50)
    assert r["text"] == term.buf and len(r["text"]) == terminal.MAX_BUFFER


def test_poll_espera_e_devolve_vazio_sem_novidade(term, monkeypatch):
    monkeypatch.setattr(terminal, "POLL_WAIT", 0.05)
    alimenta(term, "oi")
    t0 = time.monotonic()
    r = term.poll(2)
    assert r["text"] == "" and r["cursor"] == 2 and time.monotonic() - t0 >= 0.04

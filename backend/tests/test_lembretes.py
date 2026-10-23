from datetime import datetime

from app import lembretes

AGORA = datetime(2026, 10, 23, 10, 0)  # sexta-feira, 10h


def test_horario_falado():
    q = lambda t: lembretes.quando(t, AGORA)[0]
    assert q("às 14hrs") == datetime(2026, 10, 23, 14, 0)
    assert q("às 14h30") == datetime(2026, 10, 23, 14, 30)
    assert q("às 14:30") == datetime(2026, 10, 23, 14, 30)
    assert q("às 9 horas") == datetime(2026, 10, 23, 21, 0)       # 9h já passou: as 21h de hoje
    assert q("às 8 da manhã") == datetime(2026, 10, 24, 8, 0)     # disse manhã e já passou: amanhã
    assert q("às duas da tarde") == datetime(2026, 10, 23, 14, 0)
    assert q("às 2") == datetime(2026, 10, 23, 14, 0)
    assert q("ao meio-dia") == datetime(2026, 10, 23, 12, 0)
    assert q("meio dia") is None
    assert q("daqui a 20 minutos") == datetime(2026, 10, 23, 10, 20)
    assert q("em meia hora") == datetime(2026, 10, 23, 10, 30)
    assert q("daqui a uma hora e meia") == datetime(2026, 10, 23, 11, 30)
    assert q("em 2 horas") == datetime(2026, 10, 23, 12, 0)
    assert q("daqui a uns trinta minutos") == datetime(2026, 10, 23, 10, 30)
    assert q("amanhã às 8") == datetime(2026, 10, 24, 8, 0)
    assert q("amanhã") == datetime(2026, 10, 24, 9, 0)
    assert q("amanhã à noite") == datetime(2026, 10, 24, 20, 0)
    assert q("na segunda às 10h") == datetime(2026, 10, 26, 10, 0)
    assert q("sexta às 10h") == datetime(2026, 10, 30, 10, 0)      # hoje é sexta: a próxima
    assert q("tomar 2 comprimidos") is None


def test_pedidos_de_lembrete():
    e = lambda t: lembretes.entende(t, AGORA)
    assert e("me lembre de lavar a louça às 14hrs") == ("criar", (datetime(2026, 10, 23, 14, 0), "lavar a louça"))
    assert e("Me lembra de ligar pro João daqui a 20 minutos.") == ("criar", (datetime(2026, 10, 23, 10, 20), "ligar pro João"))
    assert e("às 15h me lembra que tenho reunião") == ("criar", (datetime(2026, 10, 23, 15, 0), "tenho reunião"))
    assert e("amanhã às 8 me lembre de tomar 2 comprimidos") == ("criar", (datetime(2026, 10, 24, 8, 0), "tomar 2 comprimidos"))
    assert e("cria um lembrete para pagar o boleto na segunda às 10h") == ("criar", (datetime(2026, 10, 26, 10, 0), "pagar o boleto"))
    assert e("me lembre de regar as plantas") == ("pergunta", "regar as plantas")
    assert e("quais são meus lembretes?") == ("listar", "")
    assert e("cancela o lembrete da louça") == ("cancelar", "louça")
    assert e("apaga todos os lembretes") == ("cancelar", "*")
    assert e("você me lembra quem foi Einstein?") is None
    assert e("que horas são?") is None


def test_dito():
    assert lembretes.dito(datetime(2026, 10, 23, 14, 0), AGORA) == "às 14h"
    assert lembretes.dito(datetime(2026, 10, 24, 8, 30), AGORA) == "amanhã às 8h30"
    assert lembretes.dito(datetime(2026, 10, 26, 10, 0), AGORA) == "na segunda às 10h"


def test_guarda_vence_e_cancela(tmp_path, monkeypatch):
    monkeypatch.setattr(lembretes, "ARQUIVO", tmp_path / "l.json")
    a = lembretes.criar(datetime(2026, 10, 23, 14, 0), "lavar a louça")
    lembretes.criar(datetime(2026, 10, 23, 16, 0), "ligar pro João")
    assert [x["texto"] for x in lembretes.parecidos("louça", lembretes.lista())] == ["lavar a louça"]
    assert lembretes.vence(datetime(2026, 10, 23, 13, 59)) == []
    assert [x["id"] for x in lembretes.vence(datetime(2026, 10, 23, 14, 0))] == [a["id"]]
    assert [x["texto"] for x in lembretes.lista()] == ["ligar pro João"]
    tocados, falas, avisos = [], [], []
    monkeypatch.setattr(lembretes.mobile, "avisa", lambda t, x: avisos.append((t, x)))
    n0 = lembretes.disparado["n"]
    lembretes._toca(lembretes.lista()[0], falas.append, 0)
    assert lembretes.disparado["n"] == n0 + 1 and falas == ["Lembrete: ligar pro João."]

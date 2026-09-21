"""Checkpoint não pode crescer para sempre.

Cada um guarda o arquivo INTEIRO como estava antes, num BLOB. Uma conversa que edita um arquivo
grande muitas vezes engordava o forja.db sem teto — e o desfazer de trinta turnos atrás não serve
para nada, porque desfazer um turno antigo desfaz todos os seguintes junto.
"""
from datetime import datetime, timedelta, timezone

from app import checkpoints, db


def _conversa() -> int:
    with db.session() as s:
        c = db.Conversation(kind="agent")
        s.add(c)
        s.commit()
        return c.id


def _turnos(conv: int) -> list[int]:
    return sorted(checkpoints.summary(conv))


def test_guarda_os_turnos_recentes_e_larga_os_antigos(tmp_path):
    conv = _conversa()
    alvo = tmp_path / "a.py"
    for turno in range(1, checkpoints.MAX_TURNOS + 6):
        alvo.write_text(f"versao {turno}", encoding="utf-8")
        checkpoints.record(conv, turno, alvo)
    guardados = _turnos(conv)
    assert len(guardados) == checkpoints.MAX_TURNOS
    assert guardados[-1] == checkpoints.MAX_TURNOS + 5   # o mais novo continua lá
    assert guardados[0] == 6                             # e os cinco primeiros saíram


def test_poda_nao_mexe_em_outra_conversa(tmp_path):
    a, b = _conversa(), _conversa()
    alvo = tmp_path / "b.py"
    alvo.write_text("x", encoding="utf-8")
    checkpoints.record(b, 1, alvo)
    for turno in range(1, checkpoints.MAX_TURNOS + 4):
        checkpoints.record(a, turno, alvo)
    assert _turnos(b) == [1]


def test_varredura_por_idade(tmp_path):
    conv = _conversa()
    alvo = tmp_path / "c.py"
    alvo.write_text("x", encoding="utf-8")
    checkpoints.record(conv, 1, alvo)
    velho = datetime.now(timezone.utc) - timedelta(days=checkpoints.MAX_DIAS + 1)
    with db.session() as s:
        s.query(db.Checkpoint).filter(db.Checkpoint.conversation_id == conv).update({"created_at": velho})
        s.commit()
    assert checkpoints.podar_antigos() >= 1
    assert _turnos(conv) == []


def test_restaurar_continua_funcionando_depois_da_poda(tmp_path):
    conv = _conversa()
    alvo = tmp_path / "d.py"
    alvo.write_text("original", encoding="utf-8")
    for turno in range(1, checkpoints.MAX_TURNOS + 3):
        checkpoints.record(conv, turno, alvo)
        alvo.write_text(f"mexido no turno {turno}", encoding="utf-8")
    ultimo = max(_turnos(conv))
    checkpoints.restore_from(conv, ultimo)
    assert alvo.read_text(encoding="utf-8") == f"mexido no turno {ultimo - 1}"

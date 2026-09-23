"""Checkpoints: desfazer as alterações de arquivo feitas pelo agente.

Antes da PRIMEIRA escrita num arquivo dentro de um turno, o conteúdo anterior é salvo (ou o fato
de que ele não existia). Restaurar um turno volta os arquivos ao estado de antes dele; restaurar
"a partir de" um turno desfaz também os turnos seguintes, na ordem inversa, para não deixar
estados misturados. Só write_file/edit_file são rastreados: run_command, MCP e navegador não
(o README avisa).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from . import config, db, workspace

TRACKED = {"write_file", "edit_file", "write_document", "write_spreadsheet",
           "edit_document", "edit_spreadsheet"}
# Cada checkpoint guarda o arquivo INTEIRO como estava antes, num BLOB. Sem poda, uma conversa que
# edita um arquivo grande muitas vezes engorda o forja.db para sempre — e o desfazer de trinta
# turnos atrás não serve para nada, porque desfazer um turno antigo desfaz todos os seguintes junto.
MAX_TURNOS = 20   # turnos com desfazer guardado, por conversa
MAX_DIAS = 30     # idade máxima; varrido na subida do backend


def record(conv_id: int, turn_id: int, path: Path, attempt_id: int | None = None) -> None:
    """Guarda o arquivo como estava antes da 1ª escrita do turno — ou da tentativa, com `attempt_id`.

    Dentro de um turno da Maestro rodam várias tentativas; cada uma ganha a própria linha com o
    estado de antes DELA. O desfazer do turno continua certo: restaura da mais nova para a mais
    velha, e a última aplicada é a de antes do turno."""
    path = Path(path)
    with db.session() as s:
        filtro = (db.Checkpoint.attempt_id == attempt_id if attempt_id is not None
                  else db.Checkpoint.turn_id == turn_id)
        exists = s.scalar(select(db.Checkpoint.id).where(
            db.Checkpoint.conversation_id == conv_id, filtro, db.Checkpoint.path == str(path)))
        if exists:
            return  # já temos o estado de antes deste turno (ou desta tentativa)
        existed = path.is_file()
        content = path.read_bytes() if existed and path.stat().st_size <= config.MAX_FILE_BYTES else None
        if existed and content is None:
            return  # grande demais para guardar; não finge que dá para desfazer
        s.add(db.Checkpoint(conversation_id=conv_id, turn_id=turn_id, path=str(path),
                            existed=existed, content=content, attempt_id=attempt_id))
        s.commit()
    podar(conv_id)


def podar(conv_id: int) -> int:
    """Descarta os checkpoints dos turnos mais antigos desta conversa. Devolve quantas linhas saíram."""
    with db.session() as s:
        turnos = [t for (t,) in s.execute(
            select(db.Checkpoint.turn_id).where(db.Checkpoint.conversation_id == conv_id)
            .distinct().order_by(db.Checkpoint.turn_id.desc())).all()]
        if len(turnos) <= MAX_TURNOS:
            return 0
        corte = turnos[MAX_TURNOS - 1]
        n = s.query(db.Checkpoint).filter(db.Checkpoint.conversation_id == conv_id,
                                          db.Checkpoint.turn_id < corte).delete()
        s.commit()
        return n


def podar_antigos() -> int:
    """Na subida: descarta checkpoint mais velho que MAX_DIAS, de qualquer conversa.

    Sem VACUUM de propósito: com WAL o SQLite reaproveita as páginas liberadas, e um VACUUM numa
    base grande seguraria a abertura do app por segundos para economizar o que ele já economiza.
    """
    limite = datetime.now(timezone.utc) - timedelta(days=MAX_DIAS)
    with db.session() as s:
        n = s.query(db.Checkpoint).filter(db.Checkpoint.created_at < limite).delete()
        s.commit()
        return n


def summary(conv_id: int) -> dict[int, list[str]]:
    """{turn_id: [arquivos]} para a UI (caminhos do Windows quando possível)."""
    out: dict[int, list[str]] = {}
    with db.session() as s:
        for cp in s.scalars(select(db.Checkpoint).where(db.Checkpoint.conversation_id == conv_id)
                            .order_by(db.Checkpoint.id)):
            out.setdefault(cp.turn_id, []).append(workspace.to_host(Path(cp.path)) or cp.path)
    return out


def restore_from(conv_id: int, turn_id: int) -> list[str]:
    """Desfaz os turnos >= turn_id (mais novos primeiro). Devolve os arquivos restaurados."""
    with db.session() as s:
        rows = list(s.scalars(select(db.Checkpoint).where(
            db.Checkpoint.conversation_id == conv_id, db.Checkpoint.turn_id >= turn_id)
            .order_by(db.Checkpoint.turn_id.desc(), db.Checkpoint.id.desc())))
        restored = _aplica(s, rows)
        s.commit()
    return restored


def _aplica(s, rows) -> list[str]:
    restored: list[str] = []
    for cp in rows:
        p = Path(cp.path)
        if cp.existed:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(cp.content or b"")
        elif p.is_file():
            p.unlink()
        label = workspace.to_host(p) or cp.path
        if label not in restored:
            restored.append(label)
        s.delete(cp)
    return restored


def arquivos_da_tentativa(attempt_id: int) -> list[Path]:
    with db.session() as s:
        return [Path(p) for (p,) in s.execute(select(db.Checkpoint.path).where(
            db.Checkpoint.attempt_id == attempt_id).order_by(db.Checkpoint.id))]


def restore_attempt(attempt_id: int) -> list[str]:
    """Volta os arquivos de UMA tentativa ao estado de antes dela. Mudança feita depois nesses mesmos
    arquivos sai junto — é o preço de voltar o arquivo inteiro, e a interface avisa antes."""
    with db.session() as s:
        rows = list(s.scalars(select(db.Checkpoint).where(db.Checkpoint.attempt_id == attempt_id)
                              .order_by(db.Checkpoint.id.desc())))
        restored = _aplica(s, rows)
        s.commit()
    return restored


def forget_from(conv_id: int, turn_id: int) -> None:
    """Descarta checkpoints de turnos apagados sem restaurar (o usuário escolheu manter os arquivos)."""
    with db.session() as s:
        s.query(db.Checkpoint).filter(db.Checkpoint.conversation_id == conv_id,
                                      db.Checkpoint.turn_id >= turn_id).delete()
        s.commit()

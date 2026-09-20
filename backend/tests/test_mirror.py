"""Espelho das conversas em Markdown: escrever, renomear, apagar e sincronizar."""
import pytest

from app import db, mirror


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    """Nunca a pasta real: o espelho de cada teste vai para um diretório descartável."""
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    return tmp_path / "conversas"


def nova(kind: str, titulo: str, vazia: bool = False) -> int:
    with db.session() as s:
        c = db.Conversation(title=titulo, kind=kind)
        s.add(c)
        s.commit()
        if not vazia:
            s.add(db.Message(conversation_id=c.id, role="user", content="oi"))
            s.add(db.Message(conversation_id=c.id, role="assistant", content="resposta"))
            s.commit()
        return c.id


def mds(kind: str) -> list[str]:
    pasta = mirror.folder(kind)
    return sorted(p.name for p in pasta.glob("*.md")) if pasta.exists() else []


def test_escreve_em_pastas_separadas_por_modo():
    agente, chat = nova("agent", "Refatorar o build"), nova("chat", "Dúvida de SQL")
    mirror.write(agente)
    mirror.write(chat)

    assert mds("agent") == [f"{agente:04d} - Refatorar o build.md"]
    assert mds("chat") == [f"{chat:04d} - Dúvida de SQL.md"]
    texto = (mirror.folder("agent") / mds("agent")[0]).read_text(encoding="utf-8")
    assert "# Refatorar o build" in texto and "## Usuário" in texto


def test_rename_nao_deixa_duplicata_e_limpa_caractere_proibido():
    cid = nova("agent", "Antes")
    mirror.write(cid)
    with db.session() as s:
        s.get(db.Conversation, cid).title = "Build/NSIS: ajustes?"  # / : ? não valem no Windows
        s.commit()
    mirror.write(cid)

    assert mds("agent") == [f"{cid:04d} - BuildNSIS ajustes.md"]


def test_conversa_vazia_nao_vira_arquivo():
    mirror.write(nova("chat", "Nova conversa", vazia=True))
    assert mds("chat") == []


def test_apagar_conversa_apaga_o_md():
    cid = nova("agent", "Some comigo")
    mirror.write(cid)
    mirror.remove(cid)
    assert mds("agent") == []


def test_sync_regera_o_que_falta_e_varre_orfao():
    cid = nova("agent", "Volta no sync")
    orfao = mirror.folder("chat")
    orfao.mkdir(parents=True, exist_ok=True)
    (orfao / "9999 - fantasma.md").write_text("conversa apagada com o app fechado", encoding="utf-8")

    mirror.sync()

    assert f"{cid:04d} - Volta no sync.md" in mds("agent")
    assert not (orfao / "9999 - fantasma.md").exists()

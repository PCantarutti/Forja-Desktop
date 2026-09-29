from fastapi.testclient import TestClient

from app import db, main


def test_conversa_de_imagens_da_ia_so_aparece_depois_de_gerar():
    with db.session() as s:
        c = db.Conversation(kind="imagem", title="Imagens · Padaria", origem={"conv_id": 1, "message_id": 1, "message_ids": [1]})
        s.add(c)
        s.commit()
        cid = c.id
    cli = TestClient(main.app)
    ids = lambda **q: [x["id"] for x in cli.get("/api/conversations", params={"kind": "imagem", **q}).json()]   # noqa: E731
    assert cid not in ids()                 # só a fila de pedidos: não é conversa salva ainda
    assert cid in ids(keep=cid)             # aberta na tela: aparece enquanto está nela
    with db.session() as s:
        s.add(db.Message(conversation_id=cid, role="user", content="pão"))   # o lote grava o pedido ao gerar
        s.commit()
    assert cid in ids()

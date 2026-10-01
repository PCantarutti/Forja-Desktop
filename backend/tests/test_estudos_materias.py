import json

from fastapi.testclient import TestClient

from app import db, estudos
from tests.test_estudos import _estudo, _fake_llm, pastas  # noqa: F401  (pastas: fixture autouse)


def _na(materia, f, *a, **kw):
    marca = estudos.MATERIA.set(materia)
    try:
        return f(*a, **kw)
    finally:
        estudos.MATERIA.reset(marca)


def _meta(mid: int) -> dict:
    with db.session() as s:
        return s.get(db.Message, mid).meta["estudos"]


def test_estudo_antigo_vira_objetivo_com_uma_materia_e_nada_se_perde():
    conv = _estudo()
    with db.session() as s:
        s.get(db.Conversation, conv).title = "Biologia"
        s.commit()
    antigo = estudos._salvar(conv, role="assistant", content="# resumo", status="pronto",
                             meta={"estudos": {"tipo": "resumo", "tema": "Células"}})   # gravado antes das matérias
    mat = estudos._salvar(conv, role="event", content="aula.md",
                          meta={"estudos": {"tipo": "material", "nome": "aula.md", "uso": "conteudo", "n": 1,
                                            "arquivo": "01-aula.md", "ocr": False, "figuras": []}})
    p = estudos.projeto(conv)
    assert [x["nome"] for x in p["materias"]] == ["Biologia"]
    assert _meta(antigo.id)["materia"] == "m1" and _meta(mat.id)["materia"] == "m1"
    assert len(_na("m1", estudos.projeto, conv)["resumos"]) == 1
    # a 2ª abertura não cria outro objetivo
    estudos.projeto(conv)
    with db.session() as s:
        assert sum(1 for m in s.query(db.Message).filter_by(conversation_id=conv) if estudos._tipo(m) == "objetivo") == 1


def test_cada_materia_ve_o_seu_e_o_material_geral_vale_para_todas():
    conv = _estudo()
    estudos.materias(conv)   # objetivo novo, sem matérias
    port = estudos.nova_materia(conv, "Português")["id"]
    adm = estudos.nova_materia(conv, "Direito Administrativo")["id"]
    _na(port, estudos.adicionar_material, conv, "crase.md", texto="crase antes de palavra feminina")
    _na(adm, estudos.adicionar_material, conv, "atos.md", texto="atos administrativos")
    _na(None, estudos.adicionar_material, conv, "edital.md", texto="edital do concurso")   # pelo "Tudo": Geral
    nomes = lambda m: sorted(x["nome"] for x in _na(m, estudos.projeto, conv)["materiais"])  # noqa: E731
    assert nomes(port) == ["crase.md", "edital.md"]
    assert nomes(adm) == ["atos.md", "edital.md"]
    assert nomes(None) == ["atos.md", "crase.md", "edital.md"]
    # mover o material de matéria
    crase = next(x for x in _na(None, estudos.materiais, conv) if x["nome"] == "crase.md")
    estudos.alterar_material(crase["id"], materia=adm)
    assert "crase.md" in nomes(adm) and "crase.md" not in nomes(port)


def test_entrega_herda_a_materia_da_prova_e_o_acerto_sai_por_materia():
    conv = _estudo()
    estudos.materias(conv)
    port = estudos.nova_materia(conv, "Português")["id"]
    prova = _na(port, estudos._save, conv, role="assistant", content="", status="pronto",
                meta={"estudos": {"tipo": "prova", "titulo": "Prova 1", "questoes": []}})
    # entregue pelo "Tudo": continua de Português
    t = _na(None, estudos._save, conv, role="user", content="Entrega", status="pronto",
            meta={"estudos": {"tipo": "tentativa", "prova_id": prova.id, "pontos": 3, "max": 4}})
    assert _meta(t.id)["materia"] == port
    duvida = _na(None, estudos._save, conv, role="user", content="por quê?",
                 meta={"estudos": {"tipo": "duvida", "fio": f"questao:{t.id}:q1"}})
    assert _meta(duvida.id)["materia"] == port
    p = _na(None, estudos.projeto, conv)
    assert next(x for x in p["materias"] if x["id"] == port)["acerto"] == 75


def test_apagar_materia_manda_o_conteudo_para_o_geral():
    conv = _estudo()
    estudos.materias(conv)
    port = estudos.nova_materia(conv, "Português")["id"]
    mat = _na(port, estudos.adicionar_material, conv, "crase.md", texto="crase")
    estudos.apagar_materia(conv, port)
    assert _meta(mat["id"])["materia"] == "" and estudos.materias(conv) == []


def test_api_cabecalho_da_materia_marca_o_resumo_gerado(monkeypatch):
    from app.main import app
    _fake_llm(monkeypatch)
    with TestClient(app) as c:
        conv = c.post("/api/conversations", json={"kind": "estudos"}).json()["id"]
        assert c.get(f"/api/estudos/{conv}").json()["materias"] == []
        bio = c.post(f"/api/estudos/{conv}/materias", json={"nome": "Biologia"}).json()["id"]
        assert c.post(f"/api/estudos/{conv}/materias", json={"nome": "biologia"}).status_code == 400
        h = {"x-forja-materia": bio}
        c.post(f"/api/estudos/{conv}/material/texto", json={"texto": "mitocondria"}, headers=h)
        with c.stream("POST", f"/api/estudos/{conv}/estudar", headers=h,
                      json={"tema": "Células", "web": False, "provider": "fake", "model": "m"}) as r:
            ultimo = [json.loads(x[6:]) for x in r.iter_lines() if x.startswith("data: ")][-1]
        assert ultimo["status"] == "pronto" and _meta(ultimo["message_id"])["materia"] == bio
        assert len(c.get(f"/api/estudos/{conv}", headers=h).json()["resumos"]) == 1
        outra = c.post(f"/api/estudos/{conv}/materias", json={"nome": "Química"}).json()["id"]
        p = c.get(f"/api/estudos/{conv}", headers={"x-forja-materia": outra}).json()
        assert p["materia"] == outra and p["resumos"] == [] and p["resumo"] is None and p["materiais"] == []
        assert c.patch(f"/api/estudos/{conv}/materias/{outra}", json={"nome": "Química geral"}).json()["nome"] == "Química geral"

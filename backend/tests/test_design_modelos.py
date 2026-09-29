import pytest

from app import db, design, design_modelos, mirror
from app.tools import ToolError

DOC = "<!doctype html><html><head></head><body><section data-section=\"hero\"><h1>Modelo</h1></section></body></html>"


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(design_modelos, "PASTA", tmp_path / "modelos")
    design._RUNS.clear()


def _conv(title="Nova conversa") -> int:
    with db.session() as s:
        c = db.Conversation(kind="design", title=title)
        s.add(c)
        s.commit()
        return c.id


def test_guardar_listar_usar_e_apagar():
    origem = _conv("Padaria")
    design._nova_versao(origem, None, DOC, "inicial")
    m = design_modelos.salvar("Landing de padaria", design.projeto(origem)["html"])
    assert m["tipo"] == "site" and [x["nome"] for x in design_modelos.listar()] == ["Landing de padaria"]
    novo = _conv()
    p = design.usar_modelo(novo, m["id"])
    assert p["total"] == 1 and "Modelo" in p["html"] and p["titulo"] == "Landing de padaria"
    assert p["mensagens"][-1]["rota"] == "modelo"
    with pytest.raises(ToolError):
        design.usar_modelo(novo, m["id"])   # já tem design
    assert design_modelos.apagar(m["id"]) == []
    with pytest.raises(ToolError):
        design_modelos.pegar("../../etc")
    with pytest.raises(ToolError):
        design_modelos.salvar("   ", DOC)

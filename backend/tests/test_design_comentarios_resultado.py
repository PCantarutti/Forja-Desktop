"""Comentários aplicados: o chat mostra quais foram ("Comentário 1: …"), o que o modelo não conseguiu fica
pendente, e comportamento (menu hambúrguer) vem como JS no fim do body."""
import json
import re

from app import design, design_html
from tests.test_design_fase3 import M, _esperar, _fake, _projeto, pastas  # noqa: F401


def test_rotulo_mostra_os_comentarios_e_o_nao_feito_fica_pendente(monkeypatch):
    conv = _projeto()
    html = design.projeto(conv)["html"]
    h1 = re.search(r'<h1[^>]*data-fid="(\w+)"', html).group(1)
    p = re.search(r'<p[^>]*data-fid="(\w+)"', html).group(1)
    design.comentar(conv, [h1], "título maior")
    design.comentar(conv, [p], "menu hamburguer não funciona")
    cs = design.projeto(conv)["comentarios"]

    def responder(tipo, user):
        assert "Comentário 1 (elementos" in user and "Comentário 2 (elementos" in user and "nao_feitos" in user
        novo = design_html.outer(design.projeto(conv)["html"], h1).replace(">", ' class="grande">', 1)
        return json.dumps({"patches": [{"fid": h1, "html": novo}], "nao_feitos": [2],
                           "js": "document.querySelector('.topo-toggle')?.addEventListener('click', () => 0)",
                           "mensagem": "Fiz o título; o menu ficou para depois."})
    _fake(monkeypatch, responder, [])
    _esperar(lambda: design.start(conv, "", M, comentarios=[c["id"] for c in cs]))
    pj = design.projeto(conv)
    usuario = [m for m in pj["mensagens"] if m["role"] == "user"][-1]["content"]
    assert usuario == "Comentário 1: título maior\nComentário 2: menu hamburguer não funciona"
    assert [c["status"] for c in pj["comentarios"]] == ["aplicado", "pendente"]
    assert "<script data-forja-js>" in pj["html"] and 'class="grande"' in pj["html"]
    assert any("Não conseguiu o comentário 2" in x for x in pj["mensagens"][-1]["passos"])


def test_js_que_fecha_a_tag_script_e_recusado():
    import pytest
    html = design_html.carimbar("<!doctype html><html><head></head><body><p>x</p></body></html>")
    with pytest.raises(ValueError):
        design_html.aplicar(html, {"js": "alert(1)</script><script>mal()"})

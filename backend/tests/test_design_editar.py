import re

import pytest
from fastapi.testclient import TestClient

from app import db, design, main, mirror
from app.tools import ToolError

DOC = """<!doctype html><html><head><style>:root { --cor: #c75b12; }</style></head><body>
<section data-section="hero"><h1 style="color: red; font-family: &quot;Segoe UI&quot;">Padaria</h1>
<a class="cta">Comprar</a><a class="cta">Ver</a></section></body></html>"""


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    design._RUNS.clear()
    main._JANELAS.clear()


def _projeto() -> int:
    with db.session() as s:
        c = db.Conversation(kind="design", title="Padaria")
        s.add(c)
        s.commit()
        cid = c.id
    design._nova_versao(cid, None, DOC, "inicial")
    return cid


def _fid(html: str, trecho: str) -> list[str]:
    return re.findall(rf'<{trecho}[^>]*data-fid="(\w+)"', html)


def test_editar_estilo_mescla_no_style_e_vira_versao():
    conv = _projeto()
    html = design.projeto(conv)["html"]
    h1, (a1, a2) = _fid(html, "h1")[0], _fid(html, 'a class="cta"')
    r = design.editar_estilo(conv, [h1], {"font-size": "48px", "color": ""})
    novo = r["projeto"]["html"]
    tag = re.search(r"<h1[^>]*>", novo).group(0)
    assert "font-size: 48px" in tag and "color" not in tag and "font-family: 'Segoe UI'" in tag
    assert r["projeto"]["total"] == 2 and r["fim"]["patches"][0]["fid"] == h1
    # vários de uma vez (Semelhantes): uma versão só
    r = design.editar_estilo(conv, [a1, a2], {"border-radius": "999px"})
    assert r["projeto"]["html"].count("border-radius: 999px") == 2 and r["projeto"]["total"] == 3


@pytest.mark.parametrize("estilos", [{"position": "fixed"}, {"color": "red; display:none"},
                                     {"background-color": "url(http://x)"}, {"color": 'a" onclick="x'}])
def test_editar_estilo_recusa_fora_da_lista_e_injecao(estilos):
    conv = _projeto()
    h1 = _fid(design.projeto(conv)["html"], "h1")[0]
    with pytest.raises(ToolError):
        design.editar_estilo(conv, [h1], estilos)
    assert design.projeto(conv)["total"] == 1


def test_link_da_nova_janela_sem_token_e_com_sandbox(monkeypatch):
    monkeypatch.setattr(main.config, "API_TOKEN", "segredo")
    conv = _projeto()
    c = TestClient(main.app)
    assert c.post(f"/api/design/{conv}/janela").status_code == 403   # criar o link exige o token
    url = c.post(f"/api/design/{conv}/janela", headers={"x-forja-token": "segredo"}).json()["url"]
    r = c.get(url)   # o navegador do usuário: sem token nem cookie
    assert r.status_code == 200 and "Padaria" in r.text and "data-fid" not in r.text
    assert r.headers["content-security-policy"].startswith("sandbox allow-scripts")
    assert c.get("/api/design-janela/chave-inventada").status_code == 404

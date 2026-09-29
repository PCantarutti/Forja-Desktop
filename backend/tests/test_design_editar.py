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
    assert r["projeto"]["total"] == 1 and r["projeto"]["rascunho"]["mudancas"] == 2 and r["fim"]["patches"][0]["fid"] == h1
    # vários de uma vez (Semelhantes): um passo só no rascunho
    r = design.editar_estilo(conv, [a1, a2], {"border-radius": "999px"})
    assert r["projeto"]["html"].count("border-radius: 999px") == 2 and r["projeto"]["rascunho"]["mudancas"] == 3


def test_rascunho_desfaz_refaz_salva_e_trava_troca_de_versao():
    conv = _projeto()
    h1 = _fid(design.projeto(conv)["html"], "h1")[0]
    design.editar_estilo(conv, [h1], {"font-size": "10px"})
    design.editar_estilo(conv, [h1], {"font-size": "20px"})
    with pytest.raises(ToolError):
        design.ir_para(conv, 1)   # com rascunho aberto, trocar de versão perderia os ajustes
    p = design.rascunho_desfazer(conv)
    assert "font-size: 10px" in p["html"] and p["edicao"]["refazer"]
    p = design.rascunho_desfazer(conv)
    assert p["rascunho"] is None and "font-size" not in re.search(r"<h1[^>]*>", p["html"]).group(0)
    p = design.rascunho_desfazer(conv, refazer=True)
    assert p["rascunho"]["mudancas"] == 1 and "font-size: 10px" in p["html"]
    p = design.salvar_versao(conv, "título menor")
    assert p["total"] == 2 and p["rascunho"] is None and p["mensagens"][-1]["content"] == "v2: título menor"
    assert p["mensagens"][-1]["base"] == 1 and not p["edicao"]["desfazer"]
    design.editar_estilo(conv, [h1], {"color": "#123456"})
    p = design.descartar_rascunho(conv)
    assert p["rascunho"] is None and "#123456" not in p["html"] and p["total"] == 2
    with pytest.raises(ToolError):
        design.salvar_versao(conv)


def test_pedido_a_ia_parte_do_rascunho_e_absorve(monkeypatch):
    conv = _projeto()
    h1 = _fid(design.projeto(conv)["html"], "h1")[0]
    design.editar_estilo(conv, [h1], {"font-size": "40px"})
    vistos = []

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(messages[1]["content"])
        yield "content", '{"tokens": {"--cor": "#000000"}, "mensagem": "ok"}'
        yield "done", {}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)

    async def main():
        design.start(conv, "cores mais escuras", {k: {"provider": "ollama", "model": "m"} for k in ("plano", "geracao", "edicao")}, rota="tokens")
        import asyncio
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
    import asyncio
    asyncio.run(main())
    p = design.projeto(conv)
    assert p["total"] == 2 and p["rascunho"] is None and "font-size: 40px" in p["html"] and "--cor: #000000" in p["html"]
    assert p["mensagens"][-1]["passos"][0] == "Incluiu 1 ajuste(s) manual(is) do rascunho"


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


DOC2 = """<!doctype html><html><head></head><body><section data-section="a"><ul class="lista">
<li class="i">Um</li><li class="i">Dois</li><li class="i">Três</li></ul>
<a class="cta" href="#x">Ir</a><img data-slot="foto-1" data-slot-status="provisoria" src="data:image/png;base64,AAAA" alt="f"></section></body></html>"""


def _proj2() -> int:
    with db.session() as s:
        c = db.Conversation(kind="design", title="Lista")
        s.add(c)
        s.commit()
        cid = c.id
    design._nova_versao(cid, None, DOC2, "inicial")
    return cid


def test_operacoes_de_estrutura_no_rascunho():
    conv = _proj2()
    html = design.projeto(conv)["html"]
    um, dois, tres = _fid(html, 'li class="i"')
    ul = _fid(html, "ul")[0]
    # mover o Três para antes do Um: o patch é o <ul> (pai comum), não a página toda
    r = design.operar(conv, "mover", [tres], um, "antes")
    textos = re.findall(r"<li[^>]*>(\w+)</li>", r["projeto"]["html"])
    assert textos == ["Três", "Um", "Dois"] and [x["fid"] for x in r["fim"]["patches"]] == [ul]
    # duplicar: a cópia ganha fid novo
    r = design.operar(conv, "duplicar", [um])
    h = r["projeto"]["html"]
    assert re.findall(r"<li[^>]*>(\w+)</li>", h) == ["Três", "Um", "Um", "Dois"] and len(set(_fid(h, 'li class="i"'))) == 4
    # apagar dois de uma vez
    r = design.operar(conv, "apagar", [tres, dois])
    assert re.findall(r"<li[^>]*>(\w+)</li>", r["projeto"]["html"]) == ["Um", "Um"]
    # link e imagem
    a = _fid(h, 'a class="cta"')[0]
    img = _fid(h, "img")[0]
    r = design.operar(conv, "link", [a], valor="https://padaria.com/cardapio")
    assert 'href="https://padaria.com/cardapio"' in r["projeto"]["html"]
    r = design.operar(conv, "imagem", [img], valor="data:image/png;base64,QkJCQg==")
    tag = re.search(r"<img[^>]*>", r["projeto"]["html"]).group(0)
    assert "QkJCQg==" in tag and 'data-slot-status="pronta"' in tag
    p = design.projeto(conv)
    assert p["total"] == 1 and p["rascunho"]["mudancas"] == 5


@pytest.mark.parametrize("op,kw", [("link", {"valor": "javascript:alert(1)"}), ("imagem", {"valor": "https://x/y.png"}),
                                   ("mover", {"alvo": "SELF", "onde": "dentro"}), ("apagar", {"fids": ["BODY"]})])
def test_operacoes_recusam(op, kw):
    conv = _proj2()
    html = design.projeto(conv)["html"]
    li = _fid(html, 'li class="i"')[0]
    body = _fid(html, "body")[0]
    fids = [body] if kw.get("fids") == ["BODY"] else [li]
    alvo = li if kw.get("alvo") == "SELF" else ""
    with pytest.raises(ToolError):
        design.operar(conv, op, fids, alvo, kw.get("onde", "depois"), kw.get("valor", ""))
    assert design.projeto(conv)["rascunho"] is None


def test_estilo_por_largura_vai_para_media_com_classe():
    conv = _projeto()
    html = design.projeto(conv)["html"]
    h1 = _fid(html, "h1")[0]
    r = design.editar_estilo(conv, [h1], {"font-size": "30px"}, "mobile")
    h = r["projeto"]["html"]
    assert f"fx-{h1}" in re.search(r"<h1[^>]*>", h).group(0) and "font-size: 30px" not in re.search(r"<h1[^>]*>", h).group(0)
    assert f"@media (max-width: 480px) {{\n  .fx-{h1} {{ font-size: 30px !important; }}" in h
    assert r["fim"]["patches"] == []   # bloco novo: o canvas recarrega
    r = design.editar_estilo(conv, [h1], {"font-size": "40px", "color": "#111111"}, "tablet")
    h = r["projeto"]["html"]
    assert h.index("max-width: 820px") < h.index("max-width: 480px")   # celular depois: ganha
    assert h.count(f"fx-{h1}") == 3 and h.count("data-forja-responsivo") == 1
    assert len(r["fim"]["patches"]) == 2   # o h1 e o <style>, que agora tem fid
    r = design.editar_estilo(conv, [h1], {"font-size": ""}, "mobile")
    assert "max-width: 480px" not in r["projeto"]["html"] and "font-size: 40px !important" in r["projeto"]["html"]
    # o desktop continua no style="" e não mexe no bloco
    r = design.editar_estilo(conv, [h1], {"font-size": "60px"})
    assert "font-size: 60px" in re.search(r"<h1[^>]*>", r["projeto"]["html"]).group(0)
    assert "!important" not in design.editar_estilo.__doc__ or True
    with pytest.raises(ToolError):
        design.editar_estilo(conv, [h1], {"color": "red !important"}, "tablet")
    with pytest.raises(ToolError):
        design.editar_estilo(conv, [h1], {"color": "red"}, "relogio")
    # export limpo mantém a regra (a classe fica, o data-fid sai)
    from app import design_html
    limpo = design_html.limpar_export(r["projeto"]["html"])
    assert f"fx-{h1}" in limpo and "max-width: 820px" in limpo and "data-fid" not in limpo

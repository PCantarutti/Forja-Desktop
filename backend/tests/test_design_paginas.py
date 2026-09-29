import asyncio
import json
import re

import pytest

from app import db, design, design_html, mirror

DOC = """<!doctype html><html><head><style>:root { --cor: #c75b12; }</style></head><body>
<header data-section="topo"><nav class="menu"><ul><li class="item"><a class="link" href="#">Início</a></li></ul></nav></header>
<section data-section="hero"><h1>Padaria</h1></section>
<section data-section="produtos"><h2>Pães</h2></section>
<footer data-section="rodape">© Padaria</footer>
</body></html>"""
M = {k: {"provider": "ollama", "model": "m"} for k in ("plano", "geracao", "edicao")}


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    design._RUNS.clear()


def test_paginar_marca_comuns_e_insere_runtime():
    h = design_html.paginar(design_html.carimbar(DOC))
    marcas = {e["attrs"]["data-section"]: e["attrs"]["data-pagina"] for e in design_html.secoes(h)}
    assert marcas == {"topo": "*", "hero": "inicio", "produtos": "inicio", "rodape": "*"}
    assert h.count("data-forja-paginas") == 1 and design_html.paginar(h) == h   # idempotente
    assert design_html.paginas(h) == ["inicio"]
    # seção nova de uma página: antes do rodapé comum e do runtime
    h2 = design_html.inserir_secao(h, "sobre", "sobre")
    assert h2.index('data-section="sobre"') < h2.index('data-section="rodape"') and 'data-pagina="sobre"' in h2
    h3, ok = design_html.link_no_menu(h2, "sobre", "Sobre")
    assert ok and re.search(r'<li class="item"[^>]*><a class="link" href="#/sobre"[^>]*>Sobre</a></li>', h3)


def test_pagina_nova_pela_ia_ganha_marca_e_link(monkeypatch):
    with db.session() as s:
        c = db.Conversation(kind="design", title="Padaria")
        s.add(c)
        s.commit()
        conv = c.id
    design._nova_versao(conv, None, DOC, "inicial")
    vistos = []

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(messages[1]["content"])
        yield "content", '<section data-section="sobre"><h2>Nossa história</h2></section>\nMENSAGEM: Criei a página Sobre.'
        yield "done", {}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)

    async def main():
        design.start(conv, "página sobre a história da padaria", M, rota="secao", secao="sobre", pagina="sobre")
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
    asyncio.run(main())
    assert "PÁGINA NOVA" in vistos[0]
    p = design.projeto(conv)
    h = p["html"]
    assert design_html.paginas(h) == ["inicio", "sobre"] and 'href="#/sobre"' in h
    assert re.search(r'<section[^>]*data-pagina="sobre"[^>]*>|<section[^>]*data-section="sobre"[^>]*data-pagina="sobre"', h)
    assert any("menu" in x for x in p["mensagens"][-1]["passos"])


def _chromium() -> bool:
    async def abre():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            await (await pw.chromium.launch(headless=True)).close()
    try:
        asyncio.run(abre())
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _chromium(), reason="sem Chromium headless")
def test_runtime_troca_de_pagina_por_link():
    h = design_html.paginar(design_html.carimbar(DOC))
    h = design_html.inserir_secao(h, "sobre", "sobre").replace("Gerando a seção “sobre”…", "Nossa história")
    h, _ = design_html.link_no_menu(h, "sobre", "Sobre")

    async def roda():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            b = await pw.chromium.launch(headless=True)
            pg = await b.new_page()
            await pg.set_content(h)
            vis = lambda: pg.evaluate("() => [...document.querySelectorAll('body > [data-section]')].filter(e => getComputedStyle(e).display !== 'none').map(e => e.dataset.section)")
            antes = await vis()
            await pg.click('a[href="#/sobre"]')
            depois = await vis()
            await b.close()
            return antes, depois
    antes, depois = asyncio.run(roda())
    assert antes == ["topo", "hero", "produtos", "rodape"] and depois == ["topo", "sobre", "rodape"]


def test_sem_menu_cria_um_cabecalho_com_as_paginas():
    sem_nav = "<!doctype html><html><head></head><body><section data-section=\"hero\"><h1>Oi</h1></section></body></html>"
    h = design_html.inserir_secao(design_html.paginar(design_html.carimbar(sem_nav)), "contato", "contato")
    h, ok = design_html.link_no_menu(h, "contato", "Fale conosco")
    assert ok and 'data-section="menu" data-pagina="*"' in h
    nav = re.search(r"<nav[^>]*>(.*?)</nav>", h).group(1)
    assert re.findall(r'href="#/([\w-]+)"', nav) == ["inicio", "contato"] and "Fale conosco" in nav

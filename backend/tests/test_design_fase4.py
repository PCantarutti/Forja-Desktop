import asyncio
import io
import json
import re

import pytest

from app import db, design, design_export, design_html, mirror

M = {k: {"provider": "ollama", "model": "m"} for k in ("plano", "geracao", "edicao")}
PLANO = {"tipo": "slides", "titulo": "Pitch da Padaria", "tokens": {"--cor-fundo": "#fff", "--cor-texto": "#111", "--cor-primaria": "#c00"},
         "secoes": [{"nome": f"s{i}", "objetivo": f"ideia {i}", "conteudo": f"texto {i}"} for i in range(1, 9)]}


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    design._RUNS.clear()
    yield
    design._RUNS.clear()


def _chromium() -> bool:
    async def abre():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            b = await pw.chromium.launch(headless=True)
            await b.close()
    try:
        asyncio.run(abre())
        return True
    except Exception:
        return False


TEM_CHROMIUM = _chromium()


def _slide(nome: str) -> str:
    # o modelo "esquece" o data-slide: o backend põe
    return (f'<section data-section="{nome}"><div class="{nome}-c"><h2 class="{nome}-t">Slide {nome}</h2>'
            f'<p class="{nome}-p">Uma ideia só</p></div></section><style>.{nome}-t{{font-size:120px}}</style>')


def _fake(monkeypatch, chamadas):
    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        tipo = re.match(r"<!-- (\w+)", messages[0]["content"]).group(1)
        user = messages[1]["content"]
        chamadas.append(tipo)
        if tipo == "plano":
            yield "content", json.dumps(PLANO)
        elif tipo == "slide":
            yield "content", _slide(re.search(r'data-section="(\w+)"', user).group(1))
        else:  # fragmento
            fid = re.search(r"Elemento alvo data-fid=(\w+)", user).group(1)
            yield "content", json.dumps({"patches": [{"fid": fid, "html": '<p class="s3-p destaque">Editado no slide 3</p>'}],
                                         "css": ".destaque{color:var(--cor-primaria)}"})
        yield "done", {"completion_tokens": 10}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)


def _esperar(fn):
    async def main():
        r = fn()
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return r
    return asyncio.run(main())


def _deck(monkeypatch, chamadas) -> int:
    with db.session() as s:
        c = db.Conversation(kind="design")
        s.add(c)
        s.commit()
        conv = c.id
    _fake(monkeypatch, chamadas)
    msg = _esperar(lambda: design.start(conv, "apresentação de 8 slides para investidores da padaria", M))
    _esperar(lambda: design.aprovar(msg["id"], design.estado(msg["id"])["plano"], M))
    return conv


def test_esqueleto_e_secao_de_slides():
    h = design_html.carimbar(design_html.esqueleto(PLANO))
    assert design_html.e_slides(h) and len(design_html.secoes(h)) == 8
    assert "width:1920px;height:1080px" in h and "@page{size:1920px 1080px" in h
    sec, css = design_html.ler_secao(_slide("s4"), "s4", slide=True)
    assert sec.startswith('<section data-section="s4" data-slide=""') and "120px" in css
    assert not design_html.e_slides(design_html.carimbar(design_html.esqueleto({**PLANO, "tipo": "site"})))


def test_deck_de_8_slides_edita_o_3_e_exporta(monkeypatch):
    chamadas = []
    conv = _deck(monkeypatch, chamadas)
    assert chamadas == ["plano"] + ["slide"] * 8
    p = design.projeto(conv)
    assert p["secoes"] == [f"s{i}" for i in range(1, 9)] and design_html.e_slides(p["html"])

    antes = p["html"]
    alvo = re.search(r'<section data-section="s3".*?<p class="s3-p" data-fid="(\w+)"', antes, re.S).group(1)
    _esperar(lambda: design.start(conv, "destaca este texto", M, [alvo]))
    depois = design.projeto(conv)["html"]
    assert "Editado no slide 3" in design_html.outer(depois, alvo)
    fora = lambda h: h.split("</style>")[1].replace(design_html.outer(h, alvo), "")   # noqa: E731
    assert fora(antes) == fora(depois)

    html, tipo, nome = asyncio.run(design_export.exportar(depois, "Pitch da Padaria", "html"))
    texto = html.decode()
    assert tipo.startswith("text/html") and nome == "pitch-da-padaria.html"
    assert "data-fid" not in texto and "data-placeholder" not in texto and "forja-design" not in texto
    assert "data-fid" in asyncio.run(design_export.exportar(depois, "x", "html", com_fids=True))[0].decode()


@pytest.mark.skipif(not TEM_CHROMIUM, reason="Chromium do Playwright não instalado")
def test_pdf_tem_uma_pagina_por_slide_e_png_do_slide(monkeypatch):
    from PIL import Image
    from pypdf import PdfReader
    conv = _deck(monkeypatch, [])
    html = design.projeto(conv)["html"]
    pdf, tipo, _ = asyncio.run(design_export.exportar(html, "Pitch", "pdf"))
    paginas = PdfReader(io.BytesIO(pdf)).pages
    assert tipo == "application/pdf" and len(paginas) == 8
    caixa = paginas[0].mediabox
    assert round(float(caixa.width) / float(caixa.height), 2) == round(1920 / 1080, 2)
    png, tipo, nome = asyncio.run(design_export.exportar(html, "Pitch", "png", slide=3))
    assert tipo == "image/png" and nome == "pitch-slide-3.png"
    assert Image.open(io.BytesIO(png)).size == (1920, 1080)


@pytest.mark.skipif(not TEM_CHROMIUM, reason="Chromium do Playwright não instalado")
def test_png_de_site_na_largura_do_viewport():
    from PIL import Image
    html = ('<!doctype html><html><head><style>body{margin:0}.largo{width:900px;height:300px;background:red}</style></head>'
            '<body><section data-section="a"><div class="largo"></div></section><section data-section="b" style="height:2000px"></section></body></html>')
    png, _, nome = asyncio.run(design_export.exportar(html, "Site", "png", viewport="mobile"))
    assert nome == "site-mobile.png" and Image.open(io.BytesIO(png)).size[0] == 375

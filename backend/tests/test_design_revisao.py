import asyncio

import pytest

from app import db, design, design_revisao, llm, mirror

RUIM = """<!doctype html><html><head><style>
.largo { width: 600px; } .caixa { height: 20px; overflow: hidden; } .mini { font-size: 9px; }
</style></head><body><section data-section="a">
<div class="largo">Muito largo para o celular</div>
<p class="caixa">Linha um<br>Linha dois<br>Linha três</p>
<a href="#x" class="mini">ok</a></section></body></html>"""


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


pytestmark = pytest.mark.skipif(not _chromium(), reason="sem Chromium headless")


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    design._RUNS.clear()


def test_medicao_acha_o_que_da_para_medir():
    from app import design_html
    html = design_html.carimbar(RUIM)
    r = asyncio.run(design_revisao.revisar(html))
    tipos = {(p["largura"], p["tipo"]) for p in r["problemas"]}
    assert ("mobile", "fora da tela") in tipos and ("tablet", "fora da tela") not in tipos   # 600px só estoura no celular
    assert ("desktop", "texto cortado") in tipos
    assert ("mobile", "toque pequeno") in tipos
    assert not r["visao"] and all(p["fonte"] == "medido" and p["fid"] for p in r["problemas"])


def test_modelo_sem_visao_fica_so_a_medicao_e_vai_para_a_fila(monkeypatch):
    with db.session() as s:
        c = db.Conversation(kind="design", title="Ruim")
        s.add(c)
        s.commit()
        conv = c.id
    design._nova_versao(conv, None, RUIM, "inicial")

    async def sem_visao(*a, **k):
        raise llm.LLMError("model does not support image input", 400)
        yield
    monkeypatch.setattr(design_revisao.llm, "chat_stream", sem_visao)
    r = asyncio.run(design_revisao.revisar(design.projeto(conv)["html"], "ollama", "texto-so", "baixo"))
    assert not r["visao"] and "não lê imagem" in r["aviso"] and r["problemas"]
    n = design.comentarios_da_revisao(conv, r["problemas"])
    pend = [x for x in design.projeto(conv)["comentarios"] if x["status"] == "pendente"]
    assert n == len(pend) >= 3 and all(x["texto"].startswith("Revisão visual (") for x in pend)
    assert design.comentarios_da_revisao(conv, r["problemas"]) == 0   # de novo: nada repetido


def test_modelo_com_visao_aponta_por_fid(monkeypatch):
    from app import design_html
    html = design_html.carimbar(RUIM)
    fid = design_html.secoes(html)[0]["attrs"]["data-fid"]
    vistos = []

    async def olha(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(messages[1]["content"])
        yield "content", '{"problemas": [{"fid": "%s", "largura": "mobile", "texto": "Seção sem respiro."}, {"fid": "inventado", "texto": "x"}], "mensagem": "ok"}' % fid
        yield "done", {}
    monkeypatch.setattr(design_revisao.llm, "chat_stream", olha)
    r = asyncio.run(design_revisao.revisar(html, "p", "m"))
    imagens = [x for x in vistos[0] if x["type"] == "image_url"]
    assert len(imagens) == 3 and imagens[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    modelo = [p for p in r["problemas"] if p["fonte"] == "modelo"]
    assert r["visao"] and [p["fid"] for p in modelo] == [fid] and modelo[0]["largura"] == "mobile"


def test_fila_nao_junta_elementos_diferentes_com_o_mesmo_texto():
    with db.session() as s:
        c = db.Conversation(kind="design", title="Dois")
        s.add(c)
        s.commit()
        conv = c.id
    design._nova_versao(conv, None, "<!doctype html><html><body><p>a</p><p>b</p></body></html>", "inicial")
    a, b = __import__("re").findall(r'<p data-fid="(\w+)"', design.projeto(conv)["html"])
    igual = {"largura": "mobile", "detalhe": "Letra miúda.", "fonte": "medido"}
    assert design.comentarios_da_revisao(conv, [{**igual, "fid": a}, {**igual, "fid": b}, {**igual, "fid": a}]) == 2

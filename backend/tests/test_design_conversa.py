import asyncio
import json
import re
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest

from app import db, design, design_html, design_referencias, mirror
from app.tools import ToolError

M = {k: {"provider": "ollama", "model": "m"} for k in ("plano", "geracao", "edicao")}
DOC = """<!doctype html><html><head><style>
:root { --cor-primaria: #c75b12; --raio-md: 8px; }
[data-section="hero"] { padding: 40px; }
</style></head><body>
<section data-section="hero"><h1>Padaria</h1><a class="cta">Comprar</a></section>
<footer data-section="rodape"><p>© 2026</p></footer>
</body></html>"""


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    design._RUNS.clear()
    yield
    design._RUNS.clear()


def _projeto(html=DOC) -> int:
    with db.session() as s:
        c = db.Conversation(kind="design")
        s.add(c)
        s.commit()
        cid = c.id
    if html:
        design._nova_versao(cid, None, html, "inicial")
    return cid


def _fake(monkeypatch, responder, vistos: list):
    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        tipo = re.match(r"<!-- (\w+)", messages[0]["content"]).group(1)
        vistos.append((tipo, messages[1]["content"]))
        yield "content", responder(tipo, messages[1]["content"])
        yield "done", {}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)


def _esperar(fn):
    async def main():
        r = fn()
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return r
    return asyncio.run(main())


def _ultima(conv):
    return design.projeto(conv)["mensagens"][-1]


# ------------------------------------------------------------------ mensagem, sugestões, atividade

def test_fragmento_responde_com_mensagem_sugestoes_passos_e_diff(monkeypatch):
    conv = _projeto()
    fid = re.search(r'<a class="cta" data-fid="(\w+)"', design.projeto(conv)["html"]).group(1)
    _fake(monkeypatch, lambda t, u: json.dumps({
        "patches": [{"fid": fid, "html": '<a class="cta grande">Comprar agora</a>'}], "css": ".grande{font-size:2em}",
        "mensagem": "Deixei o botão maior e mais direto.", "sugestoes": ["Pôr um ícone no botão", "Repetir a CTA no rodapé"]}), [])
    _esperar(lambda: design.start(conv, "botão maior", M, [fid]))
    m = _ultima(conv)
    assert m["mensagem"] == "Deixei o botão maior e mais direto." and m["sugestoes"] == ["Pôr um ícone no botão", "Repetir a CTA no rodapé"]
    assert m["passos"] == ["Alterou <a.cta>", "Acrescentou regras CSS"] and m["mais"] > 0
    at = design.atividade(m["id"])
    assert "+" in at["diff"]["texto"] and "Comprar agora" in at["diff"]["texto"] and "data:" not in at["diff"]["texto"]


def test_secao_le_o_rodape_de_mensagem(monkeypatch):
    conv = _projeto()
    _fake(monkeypatch, lambda t, u: '<section data-section="hero"><h1>Novo</h1></section><style>.x{}</style>\n'
                                    'MENSAGEM: Refiz o hero com mais respiro.\nSUGESTÕES: Trocar a foto | Fonte maior', [])
    _esperar(lambda: design.start(conv, "refaz o hero", M))
    m = _ultima(conv)
    assert m["mensagem"] == "Refiz o hero com mais respiro." and m["sugestoes"] == ["Trocar a foto", "Fonte maior"]
    assert m["passos"][0] == "Refez a seção “hero”"
    assert "MENSAGEM" not in design.projeto(conv)["html"]


def test_texto_e_ajuste_sem_ia_tambem_tem_atividade():
    conv = _projeto()
    h1 = re.search(r'<h1 data-fid="(\w+)"', design.projeto(conv)["html"]).group(1)
    design.editar_texto(conv, h1, "Padaria Sol")
    design.ajustar_tokens(conv, {"--raio-md": "16px"})
    r = design.projeto(conv)["rascunho"]   # à mão: rascunho, não versão
    assert r["mudancas"] == 2 and "Padaria Sol" in r["passos"][0] and design.projeto(conv)["total"] == 1
    design.salvar_versao(conv)
    m = _ultima(conv)
    assert m["rota"] == "manual" and m["content"] == "v2: 2 ajustes manuais"
    assert m["passos"][1] == "--raio-md → 16px (painel de ajustes, sem IA)" and m["mais"] >= 1 and m["menos"] >= 1


# ------------------------------------------------------------------ perguntas antes do design

def test_perguntas_antes_do_plano(monkeypatch):
    conv = _projeto(None)
    vistos = []

    def responder(tipo, user):
        if tipo == "perguntas":
            return json.dumps({"mensagem": "Entendi: landing de padaria.", "perguntas": [
                {"pergunta": "Qual o tom?", "opcoes": ["Aconchegante", "Moderno"]}, {"pergunta": "Tem delivery?", "opcoes": []}]})
        return json.dumps({"titulo": "P", "mensagem": "Vou fazer 2 seções.", "sugestoes": ["Cardápio"], "secoes": [{"nome": "hero"}, {"nome": "sobre"}]})
    _fake(monkeypatch, responder, vistos)
    msg = _esperar(lambda: design.start(conv, "landing de padaria", M, perguntar=True))
    m = _ultima(conv)
    assert m["status"] == "perguntas" and [p["pergunta"] for p in m["perguntas"]] == ["Qual o tom?", "Tem delivery?"]
    assert m["mensagem"] == "Entendi: landing de padaria." and design.estado(msg["id"])["status"] == "perguntas"

    respostas = [{"pergunta": "Qual o tom?", "resposta": "Aconchegante"}, {"pergunta": "Tem delivery?", "resposta": "Sim, no bairro"}]
    _esperar(lambda: design.start(conv, "landing de padaria", M, perguntar=True, respostas=respostas))
    assert vistos[-1][0] == "plano" and "Qual o tom?: Aconchegante" in vistos[-1][1] and "Sim, no bairro" in vistos[-1][1]
    m = _ultima(conv)
    assert m["status"] == "plano" and m["mensagem"] == "Vou fazer 2 seções." and m["sugestoes"] == ["Cardápio"]
    assert design.projeto(conv)["mensagens"][-2]["respostas"] == respostas


def test_sem_nada_a_perguntar_vai_direto_ao_plano(monkeypatch):
    conv = _projeto(None)
    vistos = []
    _fake(monkeypatch, lambda t, u: json.dumps({"perguntas": []}) if t == "perguntas"
          else json.dumps({"titulo": "P", "secoes": [{"nome": "hero"}]}), vistos)
    _esperar(lambda: design.start(conv, "landing completa da Padaria Sol, tom aconchegante, 5 seções", M, perguntar=True))
    assert [v[0] for v in vistos] == ["perguntas", "plano"] and _ultima(conv)["status"] == "plano"


# ------------------------------------------------------------------ ajustes criados pela IA

def test_tweaks_da_ia_viram_tokens_com_faixa_e_css(monkeypatch):
    conv = _projeto()
    _fake(monkeypatch, lambda t, u: json.dumps({
        "mensagem": "Criei 2 ajustes.", "css": '[data-section="hero"]{padding:var(--hero-respiro);min-height:var(--hero-altura)}',
        "tweaks": [{"nome": "--hero-respiro", "rotulo": "Respiro do hero", "min": 0, "max": 120, "passo": 4, "unidade": "px", "valor": 40},
                   {"nome": "hero-altura", "rotulo": "Altura", "min": 30, "max": 100, "unidade": "vh", "valor": 60},
                   {"nome": "--ruim", "min": 10, "max": 0}]}), [])
    msg = _esperar(lambda: design.start(conv, "", M, rota="tweaks"))
    h = design.projeto(conv)["html"]
    assert "--hero-respiro: 40px; /* ajuste: Respiro do hero | 0..120 px | 4 */" in h
    assert "--hero-altura: 60vh; /* ajuste: Altura | 30..100 vh | 1 */" in h and "--ruim" not in h
    assert "padding:var(--hero-respiro)" in h and design.estado(msg["id"])["patches"][0]["html"].startswith("<style")
    design.ajustar_tokens(conv, {"--hero-respiro": "80px"})        # o slider salva sem perder a faixa
    assert "--hero-respiro: 80px; /* ajuste: Respiro do hero | 0..120 px | 4 */" in design.projeto(conv)["html"]


# ------------------------------------------------------------------ 7. referências

def test_imagem_de_referencia_vai_como_parte_de_imagem(monkeypatch):
    conv = _projeto(None)
    vistos = []

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(messages[1]["content"])
        yield "content", json.dumps({"titulo": "P", "secoes": [{"nome": "hero"}]})
        yield "done", {}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)
    img = "data:image/jpeg;base64," + "A" * 200
    refs = [{"tipo": "imagem", "nome": "moodboard.jpg", "data": img},
            {"tipo": "documento", "nome": "briefing.txt", "texto": "Público: famílias do bairro."}]
    _esperar(lambda: design.start(conv, "landing", M, referencias=refs))
    partes = vistos[0]
    assert isinstance(partes, list) and partes[1] == {"type": "image_url", "image_url": {"url": img}}
    assert "Público: famílias do bairro." in partes[0]["text"] and "1 imagem(ns) de referência" in partes[0]["text"]
    assert design.projeto(conv)["mensagens"][0]["referencias"][0]["nome"] == "moodboard.jpg"


def test_documento_de_referencia(tmp_path):
    r = design_referencias.documento("briefing.md", "# Padaria\nPúblico: famílias.".encode())
    assert r["tipo"] == "documento" and "famílias" in r["texto"]
    from docx import Document
    d = Document()
    d.add_paragraph("Cardápio: pão de fermentação natural")
    d.save(tmp_path / "b.docx")
    assert "fermentação" in design_referencias.documento("b.docx", (tmp_path / "b.docx").read_bytes())["texto"]
    with pytest.raises(ToolError):
        design_referencias.documento("x.exe", b"MZ")


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


@pytest.mark.skipif(not _chromium(), reason="Chromium do Playwright não instalado")
def test_pagina_de_referencia_capturada(tmp_path):
    (tmp_path / "index.html").write_text(
        "<html><head><meta charset=\"utf-8\"><title>Café Aurora</title><style>body{background:#101820;color:#f2aa4c;font-family:Georgia}"
        "a{background:#f2aa4c;border-radius:12px}</style></head><body><h1>Café da manhã</h1><a href='#'>Pedir</a></body></html>", "utf-8")
    Handler = lambda *a, **k: SimpleHTTPRequestHandler(*a, directory=str(tmp_path), **k)   # noqa: E731
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        texto, foto = asyncio.run(design_referencias.pagina(f"http://127.0.0.1:{srv.server_port}/"))
    finally:
        srv.shutdown()
    assert texto["nome"] == "Café Aurora" and "rgb(16, 24, 32)" in texto["texto"] and "Georgia" in texto["texto"]
    assert "H1: Café da manhã" in texto["texto"] and "12px" in texto["texto"]
    assert foto["tipo"] == "imagem" and foto["data"].startswith("data:image/jpeg;base64,")
    with pytest.raises(ToolError):
        asyncio.run(design_referencias.pagina("ftp://x"))


# ------------------------------------------------------------------ 8. protótipo interativo

def test_prototipo_telas_com_navegacao(monkeypatch):
    conv = _projeto(None)
    vistos = []
    plano = {"tipo": "prototipo", "titulo": "App Padaria", "tokens": {"--cor-fundo": "#fff"},
             "secoes": [{"nome": "login"}, {"nome": "inicio"}, {"nome": "carrinho"}]}

    def responder(tipo, user):
        if tipo == "plano":
            return json.dumps(plano)
        nome = re.search(r'data-section="(\w+)"', user).group(1)
        return f'<section data-section="{nome}"><button data-ir="inicio">Entrar</button><nav id="m-{nome}" hidden></nav></section>'
    _fake(monkeypatch, responder, vistos)
    msg = _esperar(lambda: design.start(conv, "protótipo de app de padaria", M))
    _esperar(lambda: design.aprovar(msg["id"], design.estado(msg["id"])["plano"], M))
    assert [v[0] for v in vistos] == ["plano", "tela", "tela", "tela"]
    h = design.projeto(conv)["html"]
    assert design_html.e_prototipo(h) and h.count("data-tela=") == 3 and "forjaIrTela" in h
    assert 'data-ir="inicio"' in h and "data-placeholder" not in h
    assert "3 telas" in _ultima(conv)["mensagem"]
    _esperar(lambda: design.start(conv, "adiciona uma seção de perfil", M))      # tela nova
    h = design.projeto(conv)["html"]
    assert vistos[-1][0] == "tela" and design.projeto(conv)["secoes"][-1] == "perfil"
    assert h.index('data-section="perfil"') < h.index("data-forja-prototipo")


def test_modelo_sem_visao_segue_com_o_texto_das_referencias(monkeypatch):
    conv = _projeto(None)
    vistos = []

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(messages[1]["content"])
        if isinstance(messages[1]["content"], list):
            raise design.llm.LLMError('ollama-cloud respondeu HTTP 400: {"error":"this model does not support image input"}')
        yield "content", json.dumps({"titulo": "P", "secoes": [{"nome": "hero"}]})
        yield "done", {}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)
    refs = [{"tipo": "imagem", "nome": "print.jpg", "data": "data:image/jpeg;base64,AAAA"},
            {"tipo": "pagina", "nome": "Café", "texto": "Fundo escuro, laranja nos botões."}]
    _esperar(lambda: design.start(conv, "landing", M, referencias=refs))
    m = _ultima(conv)
    assert m["status"] == "plano" and len(vistos) == 2 and isinstance(vistos[1], str)
    assert "laranja nos botões" in vistos[1] and "não lê imagem" in m["passos"][0]

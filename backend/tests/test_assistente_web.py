import asyncio

from app import assistente, lembretes, web_voz
from app.tools import ToolError

SPEC = {"provider": "local", "model": "m"}


def _falas(monkeypatch):
    falas = []
    monkeypatch.setattr(assistente, "fala", falas.append)
    monkeypatch.setattr(assistente, "_manda", lambda *a, **k: None)
    return falas


def test_site_oficial(monkeypatch):
    monkeypatch.setattr(web_voz.web, "buscar", lambda q, n=6: [
        {"url": "https://pt.wikipedia.org/wiki/Ita%C3%BA", "title": "Itaú – Wikipédia"},
        {"url": "https://www.reclameaqui.com.br/empresa/itau/", "title": "Itaú no Reclame Aqui"},
        {"url": "https://www.itau.com.br/conta", "title": "Itaú: banco"}])
    assert web_voz.site_oficial("Itaú") == "https://www.itau.com.br/"
    assert web_voz.site_oficial("g1.globo.com") == "https://g1.globo.com/"


def test_site_oficial_de_rede_social(monkeypatch):
    # "abre o youtube": o youtube.com está em NAO_OFICIAL e o about.youtube vinha antes dele
    monkeypatch.setattr(web_voz.web, "buscar", lambda q, n=6: [
        {"url": "https://about.youtube/", "title": "Sobre o YouTube"},
        {"url": "https://pt.wikipedia.org/wiki/YouTube", "title": "YouTube – Wikipédia"},
        {"url": "https://www.youtube.com/", "title": "YouTube"}])
    assert web_voz.site_oficial("YouTube") == "https://www.youtube.com/"
    assert web_voz.site_oficial("o canal do fulano") == "https://about.youtube/"  # sem nome casando: o primeiro que vale


def test_para_numero():
    assert web_voz.para_numero("R$ 3.499,90") == 3499.9
    assert web_voz.para_numero("4 mil") == 4000
    assert web_voz.para_numero("2,5 mil") == 2500
    assert web_voz.para_numero(1999) == 1999.0
    assert web_voz.para_numero("R$ 899") == 899.0
    assert web_voz.para_numero("abc") is None


def test_candidatos_de_preco_ignoram_review_e_leem_a_pagina(monkeypatch):
    monkeypatch.setattr(web_voz.web, "buscar", lambda q, n=6: [
        {"url": "https://www.kabum.com.br/produto/1", "title": "PS5 Slim", "content": "PS5 por R$ 3.499,90 à vista"},
        {"url": "https://www.tecmundo.com.br/review", "title": "Review PS5", "content": "custa R$ 4.000"},
        {"url": "https://loja.com/ps5", "title": "PS5", "content": "sem preço aqui"}])

    def ler(url, max_chars=20000):
        if "loja.com" in url:
            return {"text": "Console PS5\nPor R$ 3.299,00 no Pix\nFrete grátis"}
        raise ToolError("bloqueado")
    monkeypatch.setattr(web_voz.web, "ler", ler)
    cands = {c["loja"]: c["linhas"] for c in web_voz.candidatos_preco("PS5")}
    assert set(cands) == {"kabum.com.br", "loja.com"}                     # review fica de fora
    assert cands["loja.com"] == ["Por R$ 3.299,00 no Pix"]


def test_ofertas_descartam_loja_inventada(monkeypatch):
    monkeypatch.setattr(web_voz, "candidatos_preco", lambda p: [{"loja": "kabum.com.br", "url": "", "titulo": "", "linhas": ["R$ 3.499"]},
                                                              {"loja": "pichau.com.br", "url": "", "titulo": "", "linhas": ["R$ 3.599"]}])

    async def gera(spec, slot, sistema, usuario, max_tokens=400):
        assert "<<<DADOS>>>" in usuario
        return '[{"loja":"pichau.com.br","preco":3599},{"loja":"kabum.com.br","preco":"R$ 3.499,00"},{"loja":"golpe.com","preco":10}]'
    monkeypatch.setattr(assistente, "_gera", gera)
    assert asyncio.run(assistente._ofertas("PS5", SPEC, None)) == [("kabum.com.br", 3499.0), ("pichau.com.br", 3599.0)]


def test_abrir_site(monkeypatch):
    falas = _falas(monkeypatch)
    monkeypatch.setattr(web_voz, "site_oficial", lambda alvo: "https://g1.globo.com/")
    abertos = []
    monkeypatch.setattr(assistente.os, "startfile", abertos.append, raising=False)
    n0 = assistente._abrir["n"]
    asyncio.run(assistente._web_voz("web", {"acao": "abrir", "alvo": "g1", "no_forja": True}, SPEC, None))
    assert assistente._abrir == {"n": n0 + 1, "url": "https://g1.globo.com/"} and abertos == []
    assert falas[-1] == "Abrindo g1.globo.com no navegador do Forja."
    asyncio.run(assistente._web_voz("web", {"acao": "abrir", "alvo": "g1"}, SPEC, None))
    assert abertos == ["https://g1.globo.com/"] and falas[-1] == "Abrindo g1.globo.com."


def test_precos_falados(monkeypatch):
    falas = _falas(monkeypatch)

    async def ofertas(produto, spec, slot):
        return [("kabum.com.br", 3499.9), ("pichau.com.br", 3599.0), ("terabyte.com.br", 3649.0)]
    monkeypatch.setattr(assistente, "_ofertas", ofertas)
    asyncio.run(assistente._web_voz("web", {"acao": "precos", "produto": "PS5"}, SPEC, None))
    assert falas[-1].startswith("A mais barata que achei: kabum.com.br, 3.499 reais e 90 centavos. Depois, pichau.com.br")


def test_acompanhar_preco_e_versao(tmp_path, monkeypatch):
    falas = _falas(monkeypatch)
    monkeypatch.setattr(web_voz, "ARQUIVO", tmp_path / "a.json")
    precos = {"v": 4500.0}

    async def ofertas(produto, spec, slot):
        return [("kabum.com.br", precos["v"])]

    async def prepara():
        return SPEC, None, None
    monkeypatch.setattr(assistente, "_ofertas", ofertas)
    monkeypatch.setattr(assistente, "_prepara_modelo", prepara)
    monkeypatch.setattr(web_voz, "repo_github", lambda p: "ggml-org/llama.cpp")
    versoes = iter(["b6000", "b6000", "b6001"])
    monkeypatch.setattr(web_voz, "ultima_versao", lambda repo: next(versoes))
    avisos = []
    monkeypatch.setattr(lembretes, "avisa", lambda titulo, texto, falado, fala, id_="": avisos.append(titulo))
    asyncio.run(assistente._web_voz("acompanhar", {"acao": "preco", "produto": "Galaxy S25", "limite": "4 mil"}, SPEC, None))
    assert "abaixo de 4.000 reais" in falas[-1] and len(web_voz.lista()) == 1
    asyncio.run(assistente._web_voz("acompanhar", {"acao": "versao", "projeto": "llama.cpp"}, SPEC, None))
    assert "b6000" in falas[-1] and len(web_voz.lista()) == 2
    asyncio.run(assistente._confere_acompanhamentos())                 # nada mudou
    assert avisos == [] and len(web_voz.lista()) == 2
    precos["v"] = 3899.0
    asyncio.run(assistente._confere_acompanhamentos())                 # preço caiu e saiu versão
    assert sorted(avisos) == ["Preço baixou: Galaxy S25", "Versão nova"] and web_voz.lista() == []


def test_pesquisa_le_o_resumo(monkeypatch):
    from app import pesquisa
    falas = _falas(monkeypatch)
    monkeypatch.setattr(pesquisa, "estado", lambda mid: {"status": "pronto", "relatorio": "Carros elétricos crescem 40%."})

    async def prepara():
        return SPEC, None, None

    async def gera(spec, slot, sistema, usuario, max_tokens=400):
        return "As vendas crescem 40% ao ano."

    async def dorme(s):
        return None
    monkeypatch.setattr(assistente, "_prepara_modelo", prepara)
    monkeypatch.setattr(assistente, "_gera", gera)
    monkeypatch.setattr(assistente.asyncio, "sleep", dorme)
    monkeypatch.setattr(lembretes, "avisa", lambda *a, **k: None)
    asyncio.run(assistente._avisa_pesquisa(1, "carros elétricos"))
    assert falas[-1] == ("A pesquisa sobre carros elétricos terminou. As vendas crescem 40% ao ano. O relatório "
                         "completo está na tela Pesquisa.")


def test_repo_com_nome_exato(monkeypatch):
    monkeypatch.setattr(web_voz.web, "buscar", lambda q, n=6: [
        {"url": "https://github.com/abetlen/llama-cpp-python"}, {"url": "https://github.com/topics/llama"},
        {"url": "https://github.com/ggml-org/llama.cpp/releases"}])
    assert web_voz.repo_github("llama.cpp") == "ggml-org/llama.cpp"
    assert web_voz.repo_github("ggml-org/whisper.cpp") == "ggml-org/whisper.cpp"

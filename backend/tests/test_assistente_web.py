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


def test_ofertas_descartam_preco_de_acessorio(monkeypatch):
    lojas = ["kabum.com.br", "olx.com.br", "lista.mercadolivre.com.br"]
    monkeypatch.setattr(web_voz, "candidatos_preco", lambda p: [{"loja": l, "url": "", "titulo": "", "linhas": ["R$ 1"]}
                                                              for l in lojas])

    async def gera(spec, slot, sistema, usuario, max_tokens=400):  # o controle de 499 lido como o console
        return '[{"loja":"kabum.com.br","preco":499},{"loja":"olx.com.br","preco":4015},{"loja":"lista.mercadolivre.com.br","preco":5499}]'
    monkeypatch.setattr(assistente, "_gera", gera)
    assert asyncio.run(assistente._ofertas("PS5", SPEC, None)) == [("olx.com.br", 4015.0), ("lista.mercadolivre.com.br", 5499.0)]


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


def test_tela_responde_e_acha_produtos_parecidos(monkeypatch):
    from app import llm
    falas = _falas(monkeypatch)
    monkeypatch.setattr(assistente, "_print_tela", lambda: "data:image/jpeg;base64,AAAA")
    async def caps(p, m):
        return {"vision", "tools"}
    monkeypatch.setattr(llm, "capabilities", caps)
    vistos = []

    async def ve(spec, slot, sistema, pedido, imagem, max_tokens):
        vistos.append((sistema, imagem))
        if sistema == assistente.TELA_PRODUTO:
            return '{"produto":"tênis Nike Air Max 90 branco","descricao":"um tênis Nike Air Max 90 branco"}'
        return "É um erro de permissão: o arquivo está em uso por outro programa."
    monkeypatch.setattr(assistente, "_ve", ve)

    async def ofertas(produto, spec, slot):
        assert produto == "tênis Nike Air Max 90 branco"
        return [("netshoes.com.br", 599.9), ("centauro.com.br", 649.0)]
    monkeypatch.setattr(assistente, "_ofertas", ofertas)
    feito = asyncio.run(assistente._tela_voz({"acao": "perguntar", "pergunta": "o que esse erro quer dizer?"}, SPEC, None))
    assert "erro de permissão" in feito  # o histórico leva o que foi visto: a pergunta seguinte depende disso
    assert falas[-1].startswith("É um erro de permissão") and vistos[-1][1].startswith("data:image/jpeg")
    n = assistente._abrir["n"]
    asyncio.run(assistente._tela_voz({"acao": "produtos", "pergunta": "parecidos com esse"}, SPEC, None))
    assert falas[-2] == "Vi um tênis Nike Air Max 90 branco. Procurando parecidos nas lojas."
    assert "netshoes.com.br" in falas[-1] and falas[-1].endswith("Abri a busca no navegador do Forja.")
    assert assistente._abrir["n"] == n + 1 and "tbm=shop" in assistente._abrir["url"] and "Nike" in assistente._abrir["url"]


def test_tela_com_modelo_sem_visao_avisa(monkeypatch):
    from app import llm
    falas = _falas(monkeypatch)
    async def caps(p, m):
        return {"tools"}
    monkeypatch.setattr(llm, "capabilities", caps)
    monkeypatch.setattr(assistente, "_print_tela", lambda: (_ for _ in ()).throw(AssertionError("não tira print à toa")))
    asyncio.run(assistente._tela_voz({"acao": "perguntar"}, {"provider": "ollama-cloud", "model": "gpt-oss:120b"}, None))
    assert "não enxerga imagem" in falas[-1]


def test_frases_da_tela_vao_para_o_classificador():
    for f in ("o que tem na minha tela?", "acha produtos parecidos com esse que eu tô vendo", "resume essa página pra mim",
              "o que é essa foto na minha tela?", "quanto custa esse tênis que eu tô olhando?",
              "o que esse erro aqui quer dizer?", "quanto custa isso aqui?"):
        c = assistente._sem_acento(f)
        assert assistente.rota(f)[0] == "chat" and assistente.PISTA.search(c), f
        assert not assistente.CITA_FORJA.search(c) or assistente.SABER.search(c), f   # não cai no classifica_rota

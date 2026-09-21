import asyncio
import json
import time

import pytest

from app import config, db, llm, mirror, pesquisa, relatorio
from app.tools import ToolError


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(config, "SUBAGENTS", {})
    pesquisa._RUNS.clear()
    yield
    pesquisa._RUNS.clear()


def _conversa(kind="pesquisa") -> int:
    with db.session() as s:
        c = db.Conversation(kind=kind)
        s.add(c)
        s.commit()
        return c.id


def _fake_llm(monkeypatch, respostas: dict | None = None, pausa=0.0):
    """O LLM falso responde pelo tipo do prompt (plano/buscas/extração/relatório)."""
    respostas = respostas or {}
    chamados: list[tuple[str, str]] = []

    def qual(system: str) -> str:
        if system.startswith("Você é um pesquisador. Recebe"):
            return "plano"
        if system.startswith("Você é um pesquisador. As fontes"):
            return "buscas"
        if system.startswith("Você lê UMA página"):
            return "extracao"
        if system.startswith("O usuário quer uma pesquisa"):
            return "perguntas"
        if system.startswith("Classifique a pergunta"):
            return "classificar"
        if system.startswith("Você mantém um relatório"):
            return "sintese"
        return "relatorio"

    vez = {"n": 0}
    padrao = {
        "plano": '{"perguntas": ["p1", "p2", "p3"], "buscas": ["termo um", "termo dois", "termo três"]}',
        "buscas": None,   # gerado por rodada, logo abaixo
        "extracao": ("RELEVANTE: sim\nRESUMO: A página diz que o preço caiu 12% em 2026 e que a "
                     "fabricante confirmou o lançamento para março, segundo o comunicado oficial.\n"
                     "TRECHO: o preço caiu 12%"),
        "relatorio": ("Resposta curta: o preço caiu e o lançamento é em março.\n\n"
                      "## Preços\nCaiu 12% [fonte](https://a.com/x).\n\n## Conclusão\nMarço."),
        "perguntas": '["Qual período?", "Qual região?"]',
        "classificar": "comparar",
        "sintese": "## 1. Parcial\n\ntexto acumulado",
    }

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None):
        tipo = qual(messages[0]["content"])
        chamados.append((tipo, model))
        if pausa:
            await asyncio.sleep(pausa)
        if tipo == "buscas" and "buscas" not in respostas:
            vez["n"] += 1
            texto = f'["termo {vez["n"]}a", "termo {vez["n"]}b", "termo {vez["n"]}c"]'
        else:
            texto = respostas.get(tipo, padrao[tipo])
        if isinstance(texto, Exception):
            raise texto
        yield ("content", texto)
        yield ("done", {"prompt_tokens": 10, "completion_tokens": 20})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)
    return chamados


def _fake_web(monkeypatch, resultados=None, falhar=(), texto="Conteúdo da página sobre o assunto."):
    """Busca e leitura falsas; anota as URLs efetivamente lidas."""
    lidas: list[str] = []
    padrao = [{"title": f"Página {i}", "url": f"https://site{i}.com/artigo", "content": "trecho"}
              for i in range(1, 7)]

    def buscar(query, n=6):
        saida = resultados if resultados is not None else padrao
        if isinstance(saida, Exception):
            raise saida
        return saida[:n]

    def ler(url, max_chars=20_000):
        lidas.append(url)
        if url in falhar:
            raise ToolError(f"{url} respondeu HTTP 404.")
        return {"url": url, "title": f"Título de {url}", "text": texto, "chars": len(texto),
                "imagem": f"{url}/capa.jpg"}

    monkeypatch.setattr(pesquisa.web, "buscar", buscar)
    monkeypatch.setattr(pesquisa.web, "ler", ler)
    return lidas


def _sem_contagem(original):
    """Provedor que não devolve usage: o 'done' vem sem os campos de token."""
    async def chat_stream(*a, **k):
        async for kind, val in original(*a, **k):
            yield (kind, {} if kind == "done" else val)
    return chat_stream


def _rodar(**kw) -> dict:
    async def main():
        msg = pesquisa.start(**kw)
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return pesquisa.estado(msg["id"])
    return asyncio.run(main())


def _base(**kw) -> dict:
    return {"conv_id": _conversa(), "pergunta": "quanto caiu o preço?", "provider": "ollama",
            "model": "qwen3:8b", "profundidade": "rapida", **kw}


# ------------------------------------------------------------------ parsing

def test_json_de_modelo_tagarela():
    resposta = 'Claro! Aqui está:\n```json\n{"perguntas": ["a",], "buscas": ["b"],}\n```\nEspero ter ajudado.'
    assert pesquisa._json(resposta) == {"perguntas": ["a"], "buscas": ["b"]}
    assert pesquisa._json("nada de json aqui") is None


def test_lista_cai_nos_bullets_quando_nao_ha_json():
    assert pesquisa._lista("Seguem os termos:\n- preço gpu 2026\n- lançamento março\n", 3) == [
        "preço gpu 2026", "lançamento março"]
    # frase longa não é termo de busca
    assert pesquisa._lista("- " + " ".join(["palavra"] * 20), 3) == []


def test_campos_do_extrator():
    ok = pesquisa._campos("RELEVANTE: sim\nRESUMO: dois\nfatos aqui\nTRECHO: citação")
    assert ok["relevante"] and ok["resumo"] == "dois\nfatos aqui" and ok["trecho"] == "citação"
    assert not pesquisa._campos("RELEVANTE: não")["relevante"]
    solto = pesquisa._campos("A página fala sobre o preço do produto.")
    assert solto["relevante"] and solto["resumo"].startswith("A página")


def test_lixo_descarta_cookie_e_resumo_curto():
    assert pesquisa._lixo("curto")
    assert pesquisa._lixo("Este site usa cookie para melhorar sua experiência de navegação aqui, ok?")
    assert not pesquisa._lixo("O preço caiu 12% em 2026 segundo o comunicado oficial da fabricante.")


# ------------------------------------------------------------------ fluxo

def test_fluxo_feliz(monkeypatch):
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    est = _rodar(**_base())
    assert est["status"] == "pronto"
    assert len(est["fontes"]) == 3 and all(f["status"] == "util" for f in est["fontes"])
    assert est["resumo"].startswith("Resposta curta")
    assert est["stats"]["uteis"] == 3
    guardada = pesquisa._mensagem(est["message_id"])
    assert "## Conclusão" in guardada["content"]          # relatório vai no content da mensagem
    assert guardada["meta"]["pesquisa"]["plano"]["perguntas"] == ["p1", "p2", "p3"]


def test_url_que_falha_nao_derruba_a_rodada(monkeypatch):
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch, falhar=("https://site1.com/artigo",))
    est = _rodar(**_base())
    status = {f["url"]: f["status"] for f in est["fontes"]}
    assert status["https://site1.com/artigo"] == "erro"
    assert sum(v == "util" for v in status.values()) == 2
    assert est["status"] == "pronto"


def test_pagina_sem_nada_util_vira_vazia(monkeypatch):
    _fake_llm(monkeypatch, {"extracao": "RELEVANTE: não"})
    _fake_web(monkeypatch)
    est = _rodar(**_base())
    assert all(f["status"] == "vazia" for f in est["fontes"])
    assert est["status"] == "erro" and "reformular" in est["aviso"]


def test_busca_vazia_encerra_com_aviso(monkeypatch):
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch, resultados=[])
    est = _rodar(**_base())
    assert est["status"] == "erro" and est["aviso"]
    assert est["fontes"] == []


def test_buscador_fora_do_ar_vira_aviso(monkeypatch):
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch, resultados=ToolError("Busca indisponível (ConnectError). Sem internet?"))
    est = _rodar(**_base())
    assert est["status"] == "erro" and "Sem internet" in est["aviso"]


def test_dedupe_de_dominio_e_de_url(monkeypatch):
    _fake_llm(monkeypatch)
    repetidos = [{"title": "a", "url": "https://mesmo.com/1", "content": ""},
                 {"title": "b", "url": "https://mesmo.com/2", "content": ""},
                 {"title": "c", "url": "https://outro.com/1", "content": ""},
                 {"title": "c", "url": "https://outro.com/1", "content": ""}]
    lidas = _fake_web(monkeypatch, resultados=repetidos)
    est = _rodar(**_base(profundidade="normal"))
    assert len(lidas) == len(set(lidas))                      # URL nunca é relida na corrida
    assert sorted(lidas[:2]) == ["https://mesmo.com/1", "https://outro.com/1"]  # 1 por domínio/rodada
    assert [f["rodada"] for f in est["fontes"]] == [1, 1, 2]  # a 2ª página do mesmo site só na rodada 2


def test_cancelar_preserva_o_parcial(monkeypatch):
    _fake_llm(monkeypatch, pausa=0.05)
    lidas = _fake_web(monkeypatch)

    async def main():
        msg = pesquisa.start(**_base(profundidade="funda"))
        await asyncio.sleep(0.12)
        pesquisa.cancelar(msg["id"])
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return pesquisa.estado(msg["id"])

    est = asyncio.run(main())
    assert est["status"] == "cancelado" and "interrompida" in est["aviso"]
    assert len(lidas) <= 4                       # não saiu disparando as rodadas seguintes
    assert pesquisa._mensagem(est["message_id"])["status"] == "cancelado"


def test_extrator_usa_o_slot_rapido(monkeypatch):
    monkeypatch.setattr(config, "SUBAGENTS", {"rapido": {"provider": "ollama", "model": "qwen3:1.7b"}})
    chamados = _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    est = _rodar(**_base())
    assert est["stats"] == {**est["stats"], "extrator": "qwen3:1.7b", "escritor": "qwen3:8b"}
    assert {m for tipo, m in chamados if tipo == "extracao"} == {"qwen3:1.7b"}
    assert {m for tipo, m in chamados if tipo in ("plano", "relatorio")} == {"qwen3:8b"}


def test_sem_slot_tudo_no_modelo_do_chat(monkeypatch):
    chamados = _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    _rodar(**_base())
    assert {m for _, m in chamados} == {"qwen3:8b"}


def test_continuar_nao_rele_o_que_ja_foi_lido(monkeypatch):
    _fake_llm(monkeypatch)
    lidas = _fake_web(monkeypatch)
    conv = _conversa()
    primeira = _rodar(**_base(conv_id=conv))
    lidas.clear()
    segunda = _rodar(**_base(conv_id=conv, continuar_de=primeira["message_id"]))
    assert segunda["status"] == "pronto"
    antigas = {f["url"] for f in primeira["fontes"]}
    assert not (antigas & set(lidas))


def test_estado_depois_do_fim_vem_do_banco(monkeypatch):
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    est = _rodar(**_base())
    assert est["message_id"] not in pesquisa._RUNS
    de_novo = pesquisa.estado(est["message_id"])
    assert de_novo["status"] == "pronto" and de_novo["relatorio"] == est["relatorio"]


def test_perguntas_de_esclarecimento(monkeypatch):
    _fake_llm(monkeypatch)
    out = asyncio.run(pesquisa.perguntas("preço de gpu", "ollama", "qwen3:8b"))
    assert out == ["Qual período?", "Qual região?"]


def test_discutir_abre_conversa_de_chat(monkeypatch):
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    est = _rodar(**_base())
    nova = pesquisa.discutir(est["message_id"])["conversation_id"]
    with db.session() as s:
        conv = s.get(db.Conversation, nova)
        assert conv.kind == "chat" and conv.title.startswith("Sobre:")
        assert "## Conclusão" in conv.messages[0].content


def test_mirror_exporta_a_pesquisa(monkeypatch):
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    est = _rodar(**_base())
    conv_id = pesquisa._mensagem(est["message_id"])["conversation_id"]
    with db.session() as s:
        texto = mirror.markdown(s.get(db.Conversation, conv_id))
    assert "## Relatório" in texto and "### Fontes lidas" in texto
    assert (mirror.ROOT / "forja-pesquisas").is_dir()


# ------------------------------------------------------------------ relatório HTML

def test_extrator_escolhido_na_tela_vence_o_slot(monkeypatch):
    monkeypatch.setattr(config, "SUBAGENTS", {"rapido": {"provider": "ollama", "model": "qwen3:1.7b"}})
    chamados = _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    est = _rodar(**_base(ex_provider="lmstudio", ex_model="gemma-3-4b"))
    assert est["stats"]["extrator"] == "gemma-3-4b" and est["stats"]["extrator_provider"] == "lmstudio"
    assert {m for tipo, m in chamados if tipo == "extracao"} == {"gemma-3-4b"}


def test_relatorio_usa_as_imagens_das_fontes(monkeypatch):
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    est = _rodar(**_base())
    assert all(f["imagem"] for f in est["fontes"])
    pagina = relatorio.html_do(est, est["relatorio"])
    assert '<figure class="hero-image">' in pagina          # a primeira vira capa
    assert '<figure class="section-image">' in pagina       # as outras ilustram as seções
    assert est["fontes"][0]["imagem"] in pagina


def test_relatorio_sem_imagem_nao_deixa_buraco():
    pes = {"pergunta": "p", "aviso": "", "stats": {},
           "fontes": [{"status": "util", "url": "https://a.com/x", "titulo": "A", "dominio": "a.com",
                       "resumo": "r", "imagem": ""}]}
    pagina = relatorio.html_do(pes, "## Um\n\ntexto\n\n## Dois\n\ntexto")
    assert "<figure" not in pagina


def test_formato_auto_classifica_e_muda_o_prompt(monkeypatch):
    vistos: list[str] = []
    _fake_llm(monkeypatch)
    original = pesquisa.llm.chat_stream

    async def espiao(provider, model, messages, tools, num_ctx, effort=None):
        vistos.append(messages[0]["content"])
        async for ev in original(provider, model, messages, tools, num_ctx, effort):
            yield ev

    monkeypatch.setattr(pesquisa.llm, "chat_stream", espiao)
    _fake_web(monkeypatch)
    est = _rodar(**_base())
    assert est["formato"] == "auto" and est["formato_usado"] == "comparar"
    assert any("FORMATO OBRIGATÓRIO" in v and "COMPARAÇÃO" in v for v in vistos)


def test_formato_escolhido_pula_a_classificacao(monkeypatch):
    chamados = _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    est = _rodar(**_base(formato="guia"))
    assert est["formato_usado"] == "guia"
    assert not [t for t, _ in chamados if t == "classificar"]  # nada de chamada extra
    with pytest.raises(ToolError):
        pesquisa.start(**_base(formato="inventado"))


def test_personalizado_escolhe_as_rodadas(monkeypatch):
    _fake_llm(monkeypatch)
    pagina = {"n": 0}

    def buscar(query, n=6):   # domínios inéditos a cada busca: as rodadas não param por falta de página
        pagina["n"] += 1
        return [{"title": f"P{pagina['n']}-{i}", "url": f"https://s{pagina['n']}x{i}.com/a",
                 "content": ""} for i in range(n)]

    _fake_web(monkeypatch)
    monkeypatch.setattr(pesquisa.web, "buscar", buscar)
    est = _rodar(**_base(profundidade="personalizado", rodadas=3, teto=600))
    assert est["rodadas_total"] == 3 and est["stats"]["rodadas"] == 3
    assert len(est["rodadas"]) == 3 and est["teto_segundos"] == 600

    # limites, e o número só vale no modo personalizado
    assert _rodar(**_base(profundidade="personalizado", rodadas=99))["rodadas_total"] == pesquisa.RODADAS_MAX
    assert _rodar(**_base(profundidade="rapida", rodadas=7))["rodadas_total"] == 1


def test_tempo_maximo_configuravel(monkeypatch):
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    assert _rodar(**_base())["teto_segundos"] == 300                      # o do preset "rapida"
    assert _rodar(**_base(teto=120))["teto_segundos"] == 120              # o escolhido na tela
    assert _rodar(**_base(teto=5))["teto_segundos"] == pesquisa.TETO_MIN  # limites
    assert _rodar(**_base(teto=99_999))["teto_segundos"] == pesquisa.TETO_MAX


def test_tempo_estourado_ainda_escreve_o_relatorio(monkeypatch):
    monkeypatch.setattr(pesquisa, "TETO_MIN", 0)  # o piso real é 1 min; aqui o teste precisa de 1 s
    # Busca e termos sempre novos: sem isso a corrida acaba por falta de assunto, não por tempo.
    rodada = {"n": 0}
    _fake_llm(monkeypatch, pausa=0.12)
    original = pesquisa.llm.chat_stream

    async def sempre_novo(provider, model, messages, tools, num_ctx, effort=None):
        if messages[0]["content"].startswith("Você é um pesquisador. As fontes"):
            rodada["n"] += 1
            await asyncio.sleep(0.12)
            yield ("content", f'["termo {rodada["n"]}a", "termo {rodada["n"]}b"]')
            yield ("done", {"prompt_tokens": 10, "completion_tokens": 20})
            return
        async for ev in original(provider, model, messages, tools, num_ctx, effort):
            yield ev

    monkeypatch.setattr(pesquisa.llm, "chat_stream", sempre_novo)
    _fake_web(monkeypatch)
    pagina = {"n": 0}

    def buscar(query, n=6):   # cada busca traz domínios inéditos
        pagina["n"] += 1
        return [{"title": f"P{pagina['n']}-{i}", "url": f"https://s{pagina['n']}x{i}.com/a",
                 "content": ""} for i in range(n)]

    monkeypatch.setattr(pesquisa.web, "buscar", buscar)
    est = _rodar(**_base(profundidade="funda", teto=1))
    assert est["status"] == "pronto" and "Tempo esgotado" in est["aviso"]
    assert est["relatorio"]      # o parcial vira relatório, não vai para o lixo


def test_porte_cresce_com_a_profundidade(monkeypatch):
    lidos: list[int] = []
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    monkeypatch.setattr(pesquisa.web, "ler", lambda url, max_chars=20_000: (
        lidos.append(max_chars) or {"url": url, "title": "T", "text": "t", "chars": 1, "imagem": ""}))
    _rodar(**_base(profundidade="rapida"))
    assert set(lidos) == {pesquisa.PRESETS["rapida"]["pagina"]}
    lidos.clear()
    _rodar(**_base(profundidade="funda"))
    assert set(lidos) == {pesquisa.PRESETS["funda"]["pagina"]}   # 15k contra 6k da rápida


def test_relatorio_pede_secoes_numeradas(monkeypatch):
    vistos: list[str] = []
    _fake_llm(monkeypatch)
    original = pesquisa.llm.chat_stream

    async def espiao(provider, model, messages, tools, num_ctx, effort=None):
        vistos.append(messages[0]["content"])
        async for ev in original(provider, model, messages, tools, num_ctx, effort):
            yield ev

    monkeypatch.setattr(pesquisa.llm, "chat_stream", espiao)
    _fake_web(monkeypatch)
    _rodar(**_base(profundidade="funda"))
    prompt = next(v for v in vistos if v.startswith("Você escreve o relatório final"))
    assert "### 1.1" in prompt and "Pelo menos 6 seções" in prompt
    assert "Entre 1800 e 3000 palavras" in prompt


def test_sintese_so_nas_pesquisas_longas(monkeypatch):
    def rodar(rodadas):
        chamados = _fake_llm(monkeypatch)
        pagina = {"n": 0}
        _fake_web(monkeypatch)
        monkeypatch.setattr(pesquisa.web, "buscar", lambda q, n=6: [
            {"title": f"P{(pagina.__setitem__('n', pagina['n'] + 1) or pagina['n'])}-{i}",
             "url": f"https://s{pagina['n']}x{i}.com/a", "content": ""} for i in range(n)])
        est = _rodar(**_base(profundidade="personalizado", rodadas=rodadas))
        return est, [t for t, _ in chamados if t == "sintese"]

    _, sem = rodar(2)
    assert sem == []                       # 2 rodadas: escreve uma vez só, como antes
    est, com = rodar(3)
    assert len(com) == 3                   # 3 rodadas: o relatório cresce a cada uma
    assert est["status"] == "pronto" and est["relatorio"]


def test_provedor_pendurado_nao_ignora_o_tempo(monkeypatch):
    """Sem o wait_for, uma chamada que nunca responde segurava a pesquisa até o timeout do httpx."""
    monkeypatch.setattr(pesquisa, "TETO_MIN", 0)
    monkeypatch.setattr(pesquisa, "MIN_CHAMADA", 1)
    _fake_web(monkeypatch)

    async def pendurado(*a, **k):
        await asyncio.sleep(30)
        yield ("content", "tarde demais")

    monkeypatch.setattr(pesquisa.llm, "chat_stream", pendurado)
    inicio = time.monotonic()
    est = _rodar(**_base(teto=1))
    assert time.monotonic() - inicio < 10          # morreu no tempo, não no read timeout
    assert est["status"] == "erro" and est["aviso"]


def test_contadores_de_token_e_tempo(monkeypatch):
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    est = _rodar(**_base())
    s = est["stats"]
    assert s["tokens"] == 20 * s["chamadas"] and s["chamadas"] >= 5   # plano + 3 páginas + relatório
    assert s["tokens_entrada"] == 10 * s["chamadas"]
    # com LLM e web falsos a corrida inteira leva milissegundos: o que importa é que conta
    assert s["segundos"] >= 0 and s["gerando"] >= 0 and s["estimado"] is False


def test_tokens_estimados_quando_o_provedor_nao_conta(monkeypatch):
    _fake_llm(monkeypatch, {"relatorio": "curto"})
    monkeypatch.setattr(pesquisa.llm, "chat_stream", _sem_contagem(pesquisa.llm.chat_stream))
    _fake_web(monkeypatch)
    est = _rodar(**_base())
    assert est["stats"]["estimado"] is True and est["stats"]["tokens"] > 0


def test_html_escapa_o_que_veio_da_web():
    pes = {"pergunta": "<script>alert(1)</script>", "aviso": "", "stats": {},
           "fontes": [{"status": "util", "url": "https://a.com/x", "titulo": "<b>a</b>",
                       "dominio": "a.com", "resumo": "resumo"}]}
    saida = relatorio.html_do(pes, "## Seção\n\nTexto com <img src=x onerror=alert(1)> e **negrito**.")
    assert "<script>alert" not in saida and "&lt;script&gt;" in saida
    assert "onerror=alert" not in saida or "&lt;img" in saida
    assert "<strong>negrito</strong>" in saida
    assert '<h2 id="s1">Seção</h2>' in saida


def test_html_converte_listas_e_recusa_link_estranho():
    corpo, secoes = relatorio._md("## A\n- um\n- dois\n\n1. passo\n\n[ok](https://x.com) [mau](javascript:alert(1))")
    assert "<ul><li>um</li>" in corpo.replace("\n", "") and "<ol><li>passo</li>" in corpo.replace("\n", "")
    assert '<a href="https://x.com"' in corpo
    assert "javascript:" in corpo and "<a href=\"javascript:" not in corpo  # ficou como texto
    assert secoes == [("s1", "A", 2)]


def test_api_roda_pesquisa_e_devolve_sse(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    _fake_llm(monkeypatch)
    _fake_web(monkeypatch)
    with TestClient(app) as c:
        conv = c.post("/api/conversations", json={"kind": "pesquisa"})
        assert conv.status_code == 200 and conv.json()["kind"] == "pesquisa"
        r = c.post(f"/api/pesquisa/{conv.json()['id']}/rodar",
                   json={"pergunta": "quanto caiu?", "profundidade": "rapida",
                         "provider": "ollama", "model": "qwen3:8b"})
        assert r.status_code == 200
        eventos = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
        assert eventos[-1]["status"] == "pronto"
        mid = eventos[-1]["message_id"]
        pagina = c.get(f"/api/pesquisa/{mid}/relatorio")
        assert pagina.status_code == 200 and "Forja &mdash; Pesquisa profunda" in pagina.text
        assert c.post(f"/api/pesquisa/{mid}/cancelar").json() == {"ok": True}

import asyncio
import json

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
        return "relatorio"

    padrao = {
        "plano": '{"perguntas": ["p1", "p2", "p3"], "buscas": ["termo um", "termo dois", "termo três"]}',
        "buscas": '["termo quatro", "termo cinco", "termo seis"]',
        "extracao": ("RELEVANTE: sim\nRESUMO: A página diz que o preço caiu 12% em 2026 e que a "
                     "fabricante confirmou o lançamento para março, segundo o comunicado oficial.\n"
                     "TRECHO: o preço caiu 12%"),
        "relatorio": ("Resposta curta: o preço caiu e o lançamento é em março.\n\n"
                      "## Preços\nCaiu 12% [fonte](https://a.com/x).\n\n## Conclusão\nMarço."),
        "perguntas": '["Qual período?", "Qual região?"]',
    }

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None):
        tipo = qual(messages[0]["content"])
        chamados.append((tipo, model))
        if pausa:
            await asyncio.sleep(pausa)
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
        return {"url": url, "title": f"Título de {url}", "text": texto, "chars": len(texto)}

    monkeypatch.setattr(pesquisa.web, "buscar", buscar)
    monkeypatch.setattr(pesquisa.web, "ler", ler)
    return lidas


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
    assert secoes == [("s1", "A")]


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
        assert pagina.status_code == 200 and "Pesquisa profunda · Forja" in pagina.text
        assert c.post(f"/api/pesquisa/{mid}/cancelar").json() == {"ok": True}

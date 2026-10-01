import asyncio
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, db, estudos, estudos_duvidas as D, estudos_prova as P, estudos_revisao as R, mirror, pesquisa
from app.tools import ToolError


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(estudos, "RAIZ", tmp_path / "estudos")
    monkeypatch.setattr(config, "SUBAGENTS", {})
    estudos._RUNS.clear()
    yield
    estudos._RUNS.clear()


RESUMO = ("# Biologia\n\nVisão.\n\n## 1. Glicólise\n\nNo citosol, saldo de 2 ATP [p. 2].\n\n"
          "## 2. Fermentação\n\nRegenera NAD+ [p. 5].\n\n## Fontes\n\n- apostila")
VISTO: list[list[dict]] = []


def _fake(monkeypatch, resposta):
    """Modelo falso: devolve `resposta` (texto, ou função das mensagens) em dois pedaços."""
    VISTO.clear()

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        VISTO.append(messages)
        texto = resposta(messages) if callable(resposta) else resposta
        yield ("content", texto[:5])
        yield ("content", texto[5:])
        yield ("done", {"prompt_tokens": 10, "completion_tokens": 5})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)


def _estudo() -> int:
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="Biologia")
        s.add(c)
        s.commit()
        conv = c.id
    topicos = [{"titulo": t, "objetivo": "", "pontos": [], "status": "pronto"} for t in ("Glicólise", "Fermentação")]
    estudos._save(conv, role="assistant", content=RESUMO, status="pronto",
                  meta={"estudos": {"tipo": "resumo", "tema": "Biologia", "topicos": topicos}})
    return conv


QUESTOES = [
    {"tipo": "me", "enunciado": "Onde ocorre a glicólise na célula eucarionte?", "topico": "Glicólise",
     "alternativas": ["No citosol", "Na mitocôndria", "No núcleo", "No ribossomo"], "correta": 0,
     "explicacao": "A glicólise é citosólica.", "por_alternativa": ["certa", "é o Krebs", "não", "não"]},
    {"tipo": "vf", "enunciado": "A fermentação regenera o NAD+ para a glicólise continuar.", "topico": "Fermentação",
     "correta": True, "explicacao": "É a função dela."},
]


def _prova(conv) -> dict:
    P.mcp_salvar_prova(QUESTOES, conv_id=conv)
    return P.lista(conv)[-1]


def _gabarito(prova_id: int) -> dict:
    with db.session() as s:
        return {q["id"]: q for q in s.get(db.Message, prova_id).meta["estudos"]["questoes"]}


def _esperar(coro_msg):
    async def main():
        msg = coro_msg()
        while estudos._TAREFAS:
            await asyncio.gather(*list(estudos._TAREFAS))
        return estudos.estado(msg["id"])
    return asyncio.run(main())


# ------------------------------------------------------------------ Leitner


def test_leitner_sobe_espaca_e_volta_para_a_primeira_caixa():
    hoje = date(2026, 10, 1)
    item = R._novo(hoje)
    item = R.responder_item(item, True, hoje)
    assert (item["caixa"], item["proxima"]) == (2, "2026-10-04")
    item = R.responder_item(item, True, hoje)
    assert (item["caixa"], item["proxima"]) == (3, "2026-10-08")
    item = R.responder_item(item, False, hoje)
    assert (item["caixa"], item["proxima"], item["erros"]) == (1, "2026-10-02", 1)
    for _ in range(4):
        item = R.responder_item(item, True, hoje)
    assert item["caixa"] == 5 and not item["dominada"]
    assert R.responder_item(item, True, hoje)["dominada"]


# ------------------------------------------------------------------ caderno de erros


def test_questao_errada_entra_no_caderno_para_hoje_e_sai_da_fila_ao_acertar():
    conv = _estudo()
    p = _prova(conv)
    g = _gabarito(p["message_id"])
    P.entregar(p["message_id"], {"q1": (g["q1"]["correta"] + 1) % 4, "q2": True})   # erra a 1, acerta a 2
    painel = R.painel(conv)
    erros = [x for x in painel["itens"] if x["tipo"] == "erro"]
    assert [x["chave"] for x in erros] == [f"q:{p['message_id']}:q1"]
    assert erros[0]["vence"] and erros[0]["caixa"] == 1 and erros[0]["questao"]["correta"] == g["q1"]["correta"]
    assert painel["vencem"] == 1
    st = R.revisar(conv, erros[0]["chave"], True)
    assert st["caixa"] == 2
    assert R.painel(conv)["vencem"] == 0
    assert R.revisar(conv, erros[0]["chave"], False, tirar=True)["dominada"]   # questão com defeito: sai de vez
    with pytest.raises(ToolError):
        R.revisar(conv, "q:999:q1", True)


def test_em_branco_e_parcial_tambem_vao_para_o_caderno():
    conv = _estudo()
    p = _prova(conv)
    P.entregar(p["message_id"], {})
    assert len([x for x in R.painel(conv)["itens"] if x["tipo"] == "erro"]) == 2


# ------------------------------------------------------------------ flashcards


def _cartoes_json(*pares):
    return json.dumps({"cartoes": [{"frente": f, "verso": v, "topico": "Glicólise"} for f, v in pares]}, ensure_ascii=False)


def test_gera_cartoes_do_resumo_e_dos_erros_sem_repetir(monkeypatch):
    _fake(monkeypatch, _cartoes_json(("Onde ocorre a glicólise?", "No citosol."), ("Saldo da glicólise?", "2 ATP."),
                                     ("Onde ocorre a glicólise?", "repetido no mesmo lote")))
    conv = _estudo()
    p = _prova(conv)
    P.entregar(p["message_id"], {})
    e = _esperar(lambda: R.start(conv, 6, "fake", "m"))
    assert e["status"] == "pronto" and [c["frente"] for c in e["cartoes"]] == ["Onde ocorre a glicólise?", "Saldo da glicólise?"]
    user = VISTO[0][1]["content"]
    assert "No citosol, saldo de 2 ATP" in user and "Onde ocorre a glicólise na célula eucarionte?" in user   # resumo e erro
    # segunda geração: o modelo repete um cartão que já existe e ele fica de fora
    _fake(monkeypatch, _cartoes_json(("onde ocorre a glicólise", "de novo"), ("O que a fermentação regenera?", "NAD+.")))
    e2 = _esperar(lambda: R.start(conv, 4, "fake", "m"))
    assert [c["frente"] for c in e2["cartoes"]] == ["O que a fermentação regenera?"]
    assert "Saldo da glicólise?" in VISTO[0][1]["content"]   # a lista do que ele já tem foi junto
    cartoes = [x for x in R.painel(conv)["itens"] if x["tipo"] == "cartao"]
    assert len(cartoes) == 3 and all(x["vence"] for x in cartoes)
    csv = R.anki_csv(conv)
    assert "Onde ocorre a glicólise?,No citosol.,Glicólise" in csv.splitlines()


def test_anki_recebe_formula_no_formato_dele():
    assert R._mathjax(r"Vértice: $x_v = -b/2a$ e $$\Delta = b^2 - 4ac$$; custa R$ 300 e R$ 400.") == \
        r"Vértice: \(x_v = -b/2a\) e \[\Delta = b^2 - 4ac\]; custa R$ 300 e R$ 400."


def test_lote_sem_json_vira_erro_e_apagar_cartao(monkeypatch):
    _fake(monkeypatch, "não sei fazer isso")
    conv = _estudo()
    e = _esperar(lambda: R.start(conv, 4, "fake", "m"))
    assert e["status"] == "erro" and "legíveis" in e["aviso"]
    _fake(monkeypatch, _cartoes_json(("Termo A?", "a"), ("Termo B?", "b")))
    e = _esperar(lambda: R.start(conv, 4, "fake", "m"))
    alvo = e["cartoes"][0]["id"]
    R.revisar(conv, f"f:{alvo}", False)
    R.apagar_cartao(conv, alvo)
    assert [x["frente"] for x in R.painel(conv)["itens"]] == ["Termo B?"]
    assert f"f:{alvo}" not in R._ler(conv)["itens"]


def test_sem_resumo_nem_erro_nao_gera():
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="Vazio")
        s.add(c)
        s.commit()
        conv = c.id
    with pytest.raises(ToolError, match="resumo"):
        R.start(conv, 10, "fake", "m")


def test_claude_faz_os_cartoes_pedidos_na_tela(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    conv = _estudo()
    msg = R.start(conv, 10, estudos.MOTOR_CLAUDE, "")
    texto = asyncio.run(estudos.mcp_pedidos(0))
    assert f"PEDIDO {msg['id']} — 10 flashcards" in texto and f"estudos_salvar_flashcards(pedido_id={msg['id']}" in texto
    assert "gravados" in R.mcp_salvar([{"frente": "Onde?", "verso": "Citosol", "topico": "Glicólise"}, {"frente": "x"}],
                                      pedido_id=msg["id"], modelo="Claude Opus 5.5")
    e = estudos.estado(msg["id"])
    assert e["status"] == "pronto" and len(e["cartoes"]) == 1 and e["stats"]["escritor"] == "Claude Opus 5.5"
    assert "já atendido" in R.mcp_salvar([{"frente": "B", "verso": "b"}], pedido_id=msg["id"])
    assert "gravados" in R.mcp_salvar([{"frente": "Saldo?", "verso": "2 ATP"}], conv_id=conv)   # sem pedido
    assert len(R.cartoes(conv)) == 2


# ------------------------------------------------------------------ desempenho e cronograma


def test_desempenho_soma_as_entregas_e_aponta_os_fracos():
    conv = _estudo()
    p = _prova(conv)
    g = _gabarito(p["message_id"])
    certa, errada = g["q1"]["correta"], (g["q1"]["correta"] + 1) % 4
    P.entregar(p["message_id"], {"q1": errada, "q2": True})
    P.entregar(p["message_id"], {"q1": certa, "q2": True}, modo="treino")
    d = R.desempenho(conv)
    assert [(x["nota"], x["modo"]) for x in d["entregas"]] == [(5.0, "prova"), (10.0, "treino")]
    por = {t["topico"]: t for t in d["topicos"]}
    assert por["Glicólise"]["pct"] == 0.5 and por["Glicólise"]["ultima"] == 1.0 and por["Fermentação"]["pct"] == 1.0
    assert d["fracos"] == ["Glicólise"]
    assert d["revisao"]["erros"] == 1


def test_cronograma_ate_a_prova_com_simulado_e_mais_dias_para_o_fraco(monkeypatch):
    conv = _estudo()
    p = _prova(conv)
    g = _gabarito(p["message_id"])
    P.entregar(p["message_id"], {"q1": (g["q1"]["correta"] + 1) % 4, "q2": True})   # Glicólise fraca
    hoje = date.today()
    plano = R.planejar(conv, (hoje + timedelta(days=10)).isoformat(), 90)
    assert len(plano["dias"]) == 10 and plano["dias"][0]["dia"] == hoje.isoformat()
    tipos = [d["tarefas"][0]["tipo"] for d in plano["dias"]]
    assert tipos[-1] == "simulado" and tipos[6] == "simulado" and tipos.count("simulado") == 2
    estudar = [d["tarefas"][0]["topico"] for d in plano["dias"] if d["tarefas"][0]["tipo"] == "estudar"]
    assert estudar.count("Glicólise") > estudar.count("Fermentação")
    assert plano["dias"][0]["tarefas"][0]["minutos"] == 54 and plano["dias"][0]["tarefas"][1]["minutos"] == 36
    primeira = plano["dias"][0]["tarefas"][0]["id"]
    R.marcar(conv, primeira, True)
    refeito = R.planejar(conv, (hoje + timedelta(days=5)).isoformat(), 60)   # refazer mantém o feito de hoje
    assert refeito["dias"][0]["tarefas"][0]["feito"] and len(refeito["dias"]) == 5
    with pytest.raises(ToolError, match="depois de hoje"):
        R.planejar(conv, hoje.isoformat())
    with pytest.raises(ToolError):
        R.marcar(conv, "nao-existe", True)


def test_lembrete_uma_vez_por_dia_e_so_depois_das_8(monkeypatch):
    avisos = []
    monkeypatch.setattr(R.mobile, "avisa", lambda titulo, texto, conv_id=None: avisos.append((titulo, texto, conv_id)))
    conv = _estudo()
    R.planejar(conv, (date.today() + timedelta(days=3)).isoformat())
    cedo = datetime.combine(date.today(), datetime.min.time()).replace(hour=6)
    assert R.lembrar(cedo) == 0 and not avisos
    agora = cedo.replace(hour=9)
    R.lembrar(agora)
    meus = [a for a in avisos if a[2] == conv]   # o banco é de todos os testes: outros estudos também avisam
    assert len(meus) == 1 and meus[0][0] == "Estudos: Biologia" and meus[0][1].startswith("Estudar:")
    R.lembrar(agora)
    assert len([a for a in avisos if a[2] == conv]) == 1   # o de hoje já foi


def test_estudo_sem_cronograma_nao_avisa(monkeypatch):
    avisos = []
    monkeypatch.setattr(R.mobile, "avisa", lambda titulo, texto, conv_id=None: avisos.append(conv_id))
    conv = _estudo()
    p = _prova(conv)
    P.entregar(p["message_id"], {})   # tem revisão vencendo, mas não pediu cronograma
    R.lembrar(datetime.now().replace(hour=10))
    assert conv not in avisos


# ------------------------------------------------------------------ modo treino e dicas


def test_conferir_revela_so_a_questao_respondida():
    conv = _estudo()
    p = _prova(conv)
    g = _gabarito(p["message_id"])
    r = P.conferir(p["message_id"], "q1", g["q1"]["correta"])
    assert r["certa"] is True and r["correta"] == g["q1"]["correta"] and r["explicacao"] and len(r["por_alternativa"]) == 4
    assert P.conferir(p["message_id"], "q2", False)["certa"] is False
    assert not estudos.estado(p["message_id"])["revelada"]   # o resto da prova continua sem gabarito
    with pytest.raises(ToolError):
        P.conferir(p["message_id"], "q9", 0)


def test_dicas_graduais_sem_revelar_e_no_maximo_tres(monkeypatch):
    _fake(monkeypatch, "Lembre onde ficam as enzimas da via.")
    conv = _estudo()
    p = _prova(conv)
    fio = f"dica:{p['message_id']}:q1"
    e = _esperar(lambda: D.perguntar(conv, "Quero uma dica", fio=fio, provider="fake", model="m"))
    assert e["status"] == "pronto" and e["nivel"] == 1
    sistema = VISTO[0][0]["content"]
    assert "dica 1 de 3" in sistema and "NUNCA diga qual alternativa" in sistema and "Gabarito (NÃO revele)" in sistema
    _esperar(lambda: D.perguntar(conv, "Outra", fio=fio, provider="fake", model="m"))
    assert "dica 2 de 3" in VISTO[-1][0]["content"] and VISTO[-1][1]["content"] == "Quero uma dica"   # a anterior vai junto
    _esperar(lambda: D.perguntar(conv, "Mais uma", fio=fio, provider="fake", model="m"))
    with pytest.raises(ToolError, match="três dicas"):
        D.perguntar(conv, "E outra?", fio=fio, provider="fake", model="m")
    with pytest.raises(ToolError):
        D.perguntar(conv, "dica", fio=f"dica:{p['message_id']}:q9", provider="fake", model="m")


def test_claude_da_a_dica(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    conv = _estudo()
    p = _prova(conv)
    msg = D.perguntar(conv, "Quero uma dica", fio=f"dica:{p['message_id']}:q1", provider=estudos.MOTOR_CLAUDE)
    texto = asyncio.run(estudos.mcp_pedidos(0))
    assert f"PEDIDO {msg['id']} — dica 1 de 3" in texto and "NÃO revele" in texto
    estudos.cancelar(msg["id"])   # o banco é de todos os testes: pedido aberto apareceria nos outros


# ------------------------------------------------------------------ API


def test_api_da_revisao_e_do_cronograma():
    from app.main import app
    c = TestClient(app)
    conv = _estudo()
    p = _prova(conv)
    assert c.post(f"/api/estudos/prova/{p['message_id']}/conferir", json={"questao_id": "q2", "resposta": True}).json()["certa"]
    c.post(f"/api/estudos/prova/{p['message_id']}/entregar", json={"respostas": {}, "modo": "treino"})
    painel = c.get(f"/api/estudos/{conv}/revisao").json()
    assert painel["vencem"] == 2
    chave = painel["itens"][0]["chave"]
    assert c.post(f"/api/estudos/{conv}/revisao", json={"chave": chave, "acertou": True}).json()["caixa"] == 2
    assert c.get(f"/api/estudos/{conv}").json()["revisao"]["vencem"] == 1
    assert c.get(f"/api/estudos/{conv}/desempenho").json()["entregas"][0]["modo"] == "treino"
    data = (date.today() + timedelta(days=4)).isoformat()
    plano = c.post(f"/api/estudos/{conv}/cronograma", json={"data": data, "minutos": 30}).json()
    assert len(plano["dias"]) == 4
    marcado = c.post(f"/api/estudos/{conv}/cronograma/marcar", json={"tarefa_id": plano["dias"][1]["tarefas"][0]["id"]}).json()
    assert marcado["dias"][1]["tarefas"][0]["feito"]
    assert c.post(f"/api/estudos/{conv}/cronograma", json={"data": "ontem"}).status_code == 400
    c.delete(f"/api/estudos/{conv}/cronograma")
    assert c.get(f"/api/estudos/{conv}/revisao").json()["plano"] is None
    r = c.get(f"/api/estudos/{conv}/flashcards.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")

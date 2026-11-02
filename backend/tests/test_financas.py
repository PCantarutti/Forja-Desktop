"""Finanças (IPorte): números calculados sem IA, sessão Supabase própria e o token do IPorte preso ao proxy."""
import asyncio
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, financas
from app.main import app


@pytest.fixture
def cliente():
    with TestClient(app) as c:
        yield c


def _dados():
    tx = [
        # 3 meses fechados (jul-set): receita 5000, despesa 3000 em cada
        *[{"id": f"r{m}", "account_id": "a", "category_id": "sal", "amount": 5000, "type": "receita",
           "transaction_date": f"2026-{m:02d}-05", "transfer_group": None} for m in (7, 8, 9)],
        *[{"id": f"d{m}", "account_id": "a", "category_id": "mer", "amount": 3000, "type": "despesa",
           "transaction_date": f"2026-{m:02d}-10", "transfer_group": None} for m in (7, 8, 9)],
        # mês atual (out)
        {"id": "r10", "account_id": "a", "category_id": "sal", "amount": 5000, "type": "receita",
         "transaction_date": "2026-10-05", "transfer_group": None},
        {"id": "d10", "account_id": "a", "category_id": "mer", "amount": 4000, "type": "despesa",
         "transaction_date": "2026-10-10", "description": "Atacadão", "transfer_group": None},
        # transferência: mexe nas contas, não é receita nem despesa
        {"id": "t1", "account_id": "a", "category_id": None, "amount": 500, "type": "despesa",
         "transaction_date": "2026-10-12", "transfer_group": "g"},
        {"id": "t2", "account_id": "b", "category_id": None, "amount": 500, "type": "receita",
         "transaction_date": "2026-10-12", "transfer_group": "g"},
    ]
    return {"transacoes": tx,
            "contas": [{"id": "a", "nome": "Nubank", "kind": "corrente", "saldo_inicial": 1000},
                       {"id": "b", "nome": "Poupança", "kind": "poupanca", "saldo_inicial": 0}],
            "categorias": [{"id": "sal", "nome": "Salário", "tipo": "receita"},
                           {"id": "mer", "nome": "Mercado", "tipo": "despesa", "cor": "#f00"}],
            "orcamentos": [{"category_id": "mer", "month": "2026-10-01", "amount_limit": 3500}],
            "assinaturas": [{"nome": "Streaming", "valor": 40, "ativo": True},
                            {"nome": "Cancelada", "valor": 99, "ativo": False}],
            "metas": []}


def test_agregados_e_projecao():
    a = financas.agregados(_dados(), hoje=date(2026, 10, 20))
    k = a["kpis"]
    assert (k["receitas"], k["despesas"], k["fluxo"]) == (5000, 4000, 1000)
    assert k["media_despesas_3m"] == 3000 and k["media_receitas_3m"] == 5000
    # saldo: 1000 + 4*5000 - 4*3000... (4000 em out) - 500 transferido; poupança recebe 500
    assert {c["nome"]: c["saldo"] for c in a["contas"]} == {"Nubank": 1000 + 20000 - 13000 - 500, "Poupança": 500}
    assert k["caixa"] == 8000 and k["cartoes_a_pagar"] == 0 and k["investido"] == 0
    assert a["historico"][-1] == {"mes": "2026-10", "saldo": 8000}
    assert a["historico"][-2]["saldo"] == 8000 - 1000   # outubro: +5000 -4000 (a transferência fica no caixa)
    assert a["categorias"][0] == {"categoria": "Mercado", "valor": 4000, "cor": "#f00", "media_3m": 3000}
    assert a["orcamentos"] == [{"categoria": "Mercado", "limite": 3500, "gasto": 4000}]
    assert a["fixos_mensais"] == 40   # assinatura cancelada fica fora
    assert [p["mes"] for p in a["projecao"]] == ["2026-11", "2026-12", "2027-01"]
    assert a["projecao"][0]["saldo"] == 8000 + 2000   # média 5000 - 3000 por mês
    assert a["serie"][-1]["mes"] == "2026-10" and len(a["serie"]) == 12


def test_busca_da_ia_soma_e_tira_media_dos_meses_completos():
    d = _dados()
    d["transacoes"] += [
        {"id": f"u{m}", "account_id": "a", "category_id": None, "amount": v, "type": "despesa",
         "transaction_date": f"2026-{m:02d}-15", "description": "Compra aprovada UBER *TRIP", "transfer_group": None}
        for m, v in ((7, 30), (9, 60), (10, 20))]
    gastos: list = []
    a = financas.agregados(d, hoje=date(2026, 10, 20), gastos=gastos)
    assert (a["meses_base"], a["periodo_base"]) == (3, "2026-07 a 2026-09")
    assert a["categorias_por_mes"][0]["mes"] == "2026-09"
    assert {"nome": "Compra aprovada UBER *TRIP", "total": 110, "lancamentos": 3, "media_mensal": 30} in a["por_estabelecimento"]
    [b] = financas.busca("Quanto eu gasto em média com uber mensalmente?", gastos, a)
    # outubro (20, mês atual) fica fora da média: (30 + 60) / 3 meses completos
    assert (b["termo"], b["total_12m"], b["media_mensal"]) == ("uber", 110, 30)
    assert b["media_sobre"] == "3 meses completos (2026-07 a 2026-09), sem o mês atual"
    assert b["por_mes"]["2026-09"] == {"total": 60, "lancamentos": 1}
    assert financas.busca("quanto gastei este mês?", gastos, a) == []


def test_token_do_iporte_so_abre_o_proxy(cliente, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "do-electron")
    tok = financas.token()
    h = {"X-Forja-Token": tok}
    assert cliente.get("/api/config", headers=h).status_code == 403
    assert cliente.get("/api/financas", headers=h).status_code == 403   # nem a tela de finanças do PC
    assert cliente.get("/api/iporte/pareamento", headers=h).status_code == 403
    assert cliente.get("/api/iporte/v1/ping", headers=h).status_code == 200
    assert cliente.get("/api/iporte/v1/ping", headers={"X-Forja-Token": "errado"}).status_code == 403


def test_celular_escolhe_modelo_e_so_carrega_gguf_da_lista(cliente, monkeypatch):
    from app import localai
    monkeypatch.setattr(config, "API_TOKEN", "do-electron")
    h = {"X-Forja-Token": financas.token()}
    gguf = {"path": r"D:\m\qwen.gguf", "name": "qwen", "size": 5 * 2**30, "kind": "chat"}
    monkeypatch.setattr(localai, "scan", lambda *a: [gguf, {"path": r"D:\m\flux.gguf", "kind": "image"}])
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "path": r"d:\M\qwen.gguf", "alias": "qwen"})
    monkeypatch.setattr(localai, "estimate", lambda p, prm: {"ok": True, "gpu": 4 * 2**30, "total": 6 * 2**30})
    monkeypatch.setattr(localai, "params", lambda p: {})
    monkeypatch.setattr(localai, "ctx_de", lambda p: 8192)
    monkeypatch.setattr(localai, "tem_visao", lambda p: False)
    monkeypatch.setattr(localai, "uso", lambda: {"modelo": {"alias": "qwen"}, "gpus": [{"nome": "Arc", "total": 12, "usado": 5}]})
    carregados = []
    monkeypatch.setattr(localai, "load", lambda p, patch=None: carregados.append(p) or {"alias": "qwen"})

    r = cliente.get("/api/iporte/v1/modelos", headers=h).json()
    assert [(m["nome"], m["vram"], m["carregado"]) for m in r["locais"]] == [("qwen", 4 * 2**30, True)]   # imagem fora
    assert r["uso"]["gpus"][0]["nome"] == "Arc"
    # caminho fora da lista do painel: recusado sem tocar na GPU
    assert cliente.post("/api/iporte/v1/carregar", json={"path": r"C:\Windows\x.gguf"}, headers=h).status_code == 400
    assert cliente.post("/api/iporte/v1/carregar", json={"path": r"D:\m\qwen.gguf"}, headers=h).json() == \
        {"provider": "local", "model": "qwen"}
    assert carregados == [r"D:\m\qwen.gguf"] and financas.modelo() == {"provider": "local", "model": "qwen"}
    assert cliente.put("/api/iporte/v1/modelo", json={"provider": "nao-existe", "model": "x"}, headers=h).status_code == 400


def test_porteiro_da_lan_tambem_prende_o_token_do_iporte():
    from app import mobile

    async def roda():
        porta = httpx.AsyncClient(transport=httpx.ASGITransport(app=mobile._porteiro(app)), base_url="http://lan")
        async with porta as c:
            h = {"X-Forja-Token": financas.token()}
            assert (await c.get("/api/config", headers=h)).status_code == 403
            assert (await c.get("/api/iporte/v1/ping", headers=h)).status_code == 200
    asyncio.run(roda())


def test_rotate_invalida_o_token_antigo():
    velho = financas.token()
    novo = financas.rotate()
    assert novo != velho and financas.token_ok(novo) and not financas.token_ok(velho)


def test_jwt_renova_e_guarda_o_refresh_novo(monkeypatch):
    financas._gravar(url="https://x.supabase.co", anon="anon", email="a@b.c", refresh_token="rt1")
    financas._acesso.clear()
    pedidos = []

    def responde(req: httpx.Request):
        pedidos.append(req)
        return httpx.Response(200, json={"access_token": "jwt2", "refresh_token": "rt2", "expires_in": 3600})

    real = httpx.AsyncClient
    monkeypatch.setattr(financas.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(responde), **kw))
    assert asyncio.run(financas._jwt()) == "jwt2"
    assert asyncio.run(financas._jwt()) == "jwt2"   # ainda válido: não renova de novo
    assert len(pedidos) == 1 and b"rt1" in pedidos[0].content
    from app import segredo
    assert segredo.decifrar(financas._ler()["refresh_token"]) == "rt2"   # rotacionou: guarda o novo


def test_jwt_recusado_desconecta(monkeypatch):
    financas._gravar(url="https://x.supabase.co", anon="anon", refresh_token="velho")
    financas._acesso.clear()
    real = httpx.AsyncClient
    monkeypatch.setattr(financas.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={})), **kw))
    with pytest.raises(financas.ToolError, match="expirou"):
        asyncio.run(financas._jwt())
    assert not financas.estado()["conectado"]


def test_proxy_responde_no_formato_openai(monkeypatch):
    from app import llm
    vistos = {}

    async def falso(provider, model, msgs, tools, num_ctx, **kw):
        vistos.update(msgs=msgs, tools=tools)
        yield "content", '```json\n{"e_transacao": true}\n```'

    monkeypatch.setattr(llm, "chat_stream", falso)
    monkeypatch.setattr(financas, "modelo", lambda: {"provider": "p", "model": "m"})
    r = asyncio.run(financas.completar({"messages": [{"role": "user", "content": "Compra R$ 10"}],
                                        "response_format": {"type": "json_object"}}))
    assert r["choices"][0]["message"]["content"] == '{"e_transacao": true}'
    assert vistos["tools"] is None and vistos["msgs"][0]["role"] == "system"
    assert financas.USO["t"] > 0
    with pytest.raises(financas.ToolError):
        asyncio.run(financas.completar({"messages": []}))


def test_analise_manda_os_numeros_calculados_e_guarda(monkeypatch):
    from app import llm
    vistos = {}

    async def falso(provider, model, msgs, tools, num_ctx, **kw):
        vistos["msgs"] = msgs
        for p in ("### Panorama", "\n- ok"):
            yield "content", p

    async def dados():
        return _dados()

    monkeypatch.setattr(llm, "chat_stream", falso)
    monkeypatch.setattr(financas, "dados", dados)
    monkeypatch.setattr(financas, "modelo", lambda: {"provider": "p", "model": "m"})

    async def junta():
        return "".join([p async for p in financas.analise("panorama")])
    financas.limpar_chat()
    assert asyncio.run(junta()) == "### Panorama\n- ok"
    assert '"caixa"' in vistos["msgs"][0]["content"] and "panorama do mês" in vistos["msgs"][-1]["content"]
    c = financas.chat()
    assert [m["papel"] for m in c] == ["user", "assistant"] and c[0]["rotulo"] == "Panorama do mês"
    assert c[1]["texto"] == "### Panorama\n- ok"
    asyncio.run(junta())   # a segunda volta leva a primeira como contexto
    assert [m["role"] for m in vistos["msgs"]] == ["system", "user", "assistant", "user"]


    async def tipo_ruim():
        return [p async for p in financas.analise("horoscopo")]
    with pytest.raises(financas.ToolError):
        asyncio.run(tipo_ruim())


def test_dados_sem_conexao_pede_login(cliente):
    financas.desconectar()
    r = cliente.get("/api/financas/dados")
    assert r.status_code == 400 and "Entre" in r.json()["detail"]


def test_conectar_sem_pareamento_busca_o_projeto_na_api_do_iporte(monkeypatch):
    financas._gravar(url="", anon="", refresh_token="")
    vistos = []

    def responde(req: httpx.Request):
        vistos.append(str(req.url))
        if req.url.path == "/v1/app/config":
            return httpx.Response(200, json={"supabaseUrl": "https://p.supabase.co", "supabaseAnonKey": "anon-pub"})
        assert req.headers["apikey"] == "anon-pub"
        return httpx.Response(200, json={"access_token": "j", "refresh_token": "r", "expires_in": 3600})

    real = httpx.AsyncClient
    monkeypatch.setattr(financas.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(responde), **kw))
    e = asyncio.run(financas.conectar("a@b.c", "senha"))
    assert e["conectado"] and e["url"] == "https://p.supabase.co"
    assert vistos[0].endswith("/v1/app/config") and vistos[1].startswith("https://p.supabase.co/auth/v1/token")


def test_regras_do_iporte_cartao_competencia_e_investimentos():
    d = {"categorias": [{"id": "laz", "nome": "Lazer", "tipo": "despesa"}],
         "contas": [{"id": "cc", "nome": "Inter", "kind": "corrente", "saldo_inicial": 1000, "institution_id": "i"},
                    {"id": "ca", "nome": "Cartão Inter", "kind": "cartao", "saldo_inicial": 0, "institution_id": "i"},
                    {"id": "inv", "nome": "Investimentos", "kind": "investimento", "saldo_inicial": 999, "institution_id": "i"}],
         "transacoes": [
             # compra no cartão em 28/09 que cai na fatura de outubro
             {"account_id": "ca", "category_id": "laz", "amount": 300, "type": "despesa", "transaction_date": "2026-09-28",
              "fatura_competencia": "2026-10", "transfer_group": None, "description": "PS5 (1/10)"},
             {"account_id": "cc", "category_id": None, "amount": 50, "type": "despesa", "transaction_date": "2026-10-03",
              "transfer_group": None, "description": "Aporte: Viagem"}],
         "investimentos": [{"id": "x", "institution_id": "i", "is_reserve": False}],
         "movimentos": [{"investment_id": "x", "type": "DEPOSIT", "amount": 500, "movement_date": "2026-10-02", "account_id": "cc"},
                        {"investment_id": "x", "type": "YIELD", "amount": 7, "movement_date": "2026-10-20", "account_id": None},
                        {"investment_id": "x", "type": "WITHDRAW", "amount": 100, "movement_date": "2026-10-21", "account_id": "cc"}],
         "orcamentos": [], "assinaturas": [], "metas": []}
    a = financas.agregados(d, hoje=date(2026, 10, 25))
    assert a["serie"][-2]["despesas"] == 0 and a["serie"][-1]["despesas"] == 350   # cartão no mês da fatura
    assert a["serie"][-1]["receitas"] == 100   # resgate é receita
    cats = {c["categoria"]: c["valor"] for c in a["categorias"]}
    assert cats == {"Lazer": 300, "Metas": 50, "Investimento": 500}
    contas = {c["nome"]: c["saldo"] for c in a["contas"]}
    assert contas["Investimentos"] == 407 and contas["Cartão Inter"] == -300   # carteira = ledger, não saldo_inicial
    assert a["kpis"]["caixa"] == 950 and a["kpis"]["cartoes_a_pagar"] == 300 and a["kpis"]["investido"] == 407


# ------------------------------------------------------------------ escrita no Supabase (PostgREST falso)

class _Supa:
    """PostgREST mínimo em memória: GET com eq./is.null/is.false, POST de lista, PATCH por filtro."""
    def __init__(self, **tabelas):
        self.t = {k: [dict(x) for x in v] for k, v in tabelas.items()}

    def _filtra(self, nome, params):
        def ok(linha):
            for k, v in params.items():
                if k in ("select", "order", "limit", "offset"):
                    continue
                if v == "is.null" and linha.get(k) is not None:
                    return False
                if v.startswith("eq.") and str(linha.get(k)) != v[3:]:
                    return False
            return True
        return [x for x in self.t.setdefault(nome, []) if ok(x)]

    def __call__(self, req: httpx.Request):
        if req.url.path.startswith("/auth/"):
            return httpx.Response(200, json={"access_token": _JWT, "refresh_token": "r", "expires_in": 3600})
        nome, params = req.url.path.rsplit("/", 1)[1], dict(req.url.params)
        if req.method == "GET":
            return httpx.Response(200, json=self._filtra(nome, params))
        corpo = httpx._models.jsonlib.loads(req.content) if req.content else None
        if req.method == "POST":
            self.t.setdefault(nome, []).extend(corpo)
            return httpx.Response(201, json=corpo)
        achadas = self._filtra(nome, params)
        for x in achadas:
            x.update(corpo)
        return httpx.Response(200, json=achadas)


import base64 as _b64, json as _json
_JWT = "h." + _b64.urlsafe_b64encode(_json.dumps({"sub": "u1"}).encode()).decode().rstrip("=") + ".s"


@pytest.fixture
def supa(monkeypatch):
    s = _Supa(accounts=[{"id": "cc", "kind": "corrente", "deleted_at": None},
                        {"id": "ca", "kind": "cartao", "closing_day": 25, "due_day": 5, "deleted_at": None},
                        {"id": "inv", "kind": "investimento", "institution_id": None, "deleted_at": None}],
              investments=[{"id": "x", "name": "CDB", "institution_id": None, "deleted_at": None}],
              investment_movements=[{"investment_id": "x", "type": "DEPOSIT", "amount": 1000, "deleted_at": None},
                                    {"investment_id": "x", "type": "YIELD", "amount": 50, "deleted_at": None}],
              notification_logs=[{"id": "n1", "ai_verdict": "transacao", "deleted_at": None},
                                 {"id": "n2", "ai_verdict": None, "deleted_at": None}])
    financas._gravar(url="https://x.supabase.co", anon="anon", refresh_token=financas.segredo.cifrar("r"))
    financas._acesso.clear()
    real = httpx.AsyncClient
    monkeypatch.setattr(financas.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(s), **kw))
    return s


def test_competencia_do_cartao_igual_ao_app():
    assert financas.ciclo("2026-10-24", 25) == "2026-10" and financas.ciclo("2026-10-25", 25) == "2026-11"
    assert financas.ciclo("2026-12-30", 25) == "2027-01"
    assert financas.ciclo("2026-02-28", 31) == "2026-03"   # fechamento 31 em fevereiro vira dia 28
    hoje = date(2026, 11, 1)   # fatura aberta = 2026-11; retroativo cai na própria
    assert financas.competencia("2026-09-30", 25, hoje) == "2026-10"
    assert financas.competencia("2026-12-01", 25, hoje) == "2026-11"


def test_nova_transacao_parcelada_no_cartao(supa):
    r = asyncio.run(financas.nova_transacao({"tipo": "despesa", "conta": "ca", "valor": "100,00", "parcelas": 3,
                                             "descricao": "Fone", "data": "2026-10-10", "categoria": "c1"}))
    tx = supa.t["transactions"]
    assert len(r["ids"]) == 3 and [t["amount"] for t in tx] == [33.33, 33.33, 33.34]
    assert [t["fatura_competencia"] for t in tx] == ["2026-10", "2026-11", "2026-12"]
    assert [t["transaction_date"] for t in tx] == ["2026-10-10", "2026-11-10", "2026-12-10"]
    assert tx[0]["user_id"] == "u1" and tx[0]["description"] == "Fone (1/3)" and tx[0]["device_id"] == "forja"
    with pytest.raises(financas.ToolError):
        asyncio.run(financas.nova_transacao({"tipo": "despesa", "conta": "ca", "valor": 0}))
    with pytest.raises(financas.ToolError):
        asyncio.run(financas.nova_transacao({"tipo": "despesa", "conta": "nao-existe", "valor": 5}))


def test_transferencia_e_apagar_leva_as_duas_pernas(supa):
    asyncio.run(financas.nova_transacao({"tipo": "transferencia", "conta": "cc", "destino": "inv", "valor": 10}))
    a, b = supa.t["transactions"]
    assert (a["type"], b["type"]) == ("despesa", "receita") and a["transfer_group"] == b["transfer_group"]
    for t in (a, b):
        t["deleted_at"] = None
    asyncio.run(financas.apagar_transacao(a["id"]))
    assert all(t["deleted_at"] and t["sync_version"] == 1 for t in supa.t["transactions"])


def test_resgate_drena_capital_e_depois_rendimento(supa):
    asyncio.run(financas.movimento_investimento({"tipo": "resgate", "investimento": "x", "valor": 1030, "conta": "cc"}))
    novos = supa.t["investment_movements"][2:]
    assert [(m["type"], m["amount"], m["account_id"]) for m in novos] == [("WITHDRAW", 1000, "cc"),
                                                                          ("ADJUSTMENT", -30, None)]
    perna = supa.t["transactions"]
    assert (perna[0]["account_id"], perna[1]["account_id"]) == ("inv", "cc") and perna[0]["amount"] == 1030
    with pytest.raises(financas.ToolError, match="patrimônio"):
        asyncio.run(financas.movimento_investimento({"tipo": "resgate", "investimento": "x", "valor": 99999}))


def test_revisao_cria_ignora_e_ensina_a_ia(supa):
    asyncio.run(financas.nova_transacao({"tipo": "despesa", "conta": "cc", "valor": 12, "log_id": "n1"}))
    n1, n2 = supa.t["notification_logs"]
    assert n1["status"] == "created" and n1["transaction_id"] == supa.t["transactions"][0]["id"]
    assert n1["feedback"] == "corrigida" and n1["parsed"] is True and n1["sync_version"] == 1
    asyncio.run(financas.revisar_notificacao("n2", "ignorar"))
    assert n2["status"] == "ignored" and "feedback" not in n2   # sem rascunho da IA: nada a aprender

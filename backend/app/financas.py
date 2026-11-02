"""Finanças: os dados do IPorte (Supabase do app de finanças) lidos com o login do próprio usuário, e a IA do
Forja servindo o celular (classificar notificação, analisar gastos) por um token de escopo próprio.

- Sessão Supabase própria (grant_type=password): o Forja tem o SEU refresh token. Dividir o do celular não dá —
  o refresh token rotaciona e quem usa o velho derruba a sessão do outro. A senha nunca é guardada; o refresh
  token fica cifrado (segredo.py, DPAPI). RLS do Supabase garante que só os dados do usuário voltam.
- Os números são calculados aqui (agregados), não pela IA: o modelo recebe o JSON pronto e escreve a análise.
- Token do IPorte: separado do token do celular do Forja e aceito SÓ em /api/iporte/v1/ (main.fronteira e o
  porteiro da LAN). Daqui saem shell e agentes; o app de finanças só precisa do modelo.
- Texto de notificação é dado, nunca ordem: o proxy chama o modelo sem ferramenta nenhuma.
"""
from __future__ import annotations

import asyncio
import base64
import calendar
import json
import os
import secrets
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone

import httpx

from . import db, segredo
from .tools import ToolError

CHAVE = "iporte"   # AppSetting: {url, anon, email, refresh_token, token, modelo, dispositivo}
CHAVE_ANALISE = "financas_analise"
# API do IPorte: GET /v1/app/config diz onde mora o Supabase (URL e anon key, públicas). Assim a pessoa só
# digita e-mail e senha, mesmo sem ter pareado o celular ainda.
IPORTE_API = os.getenv("FORJA_IPORTE_API") or "https://gothic-vulture-negocios-inteligentes-4714b65f.koyeb.app"
TIPOS = ("panorama", "projecao", "economia", "pergunta")

_acesso: dict = {}   # {token, expira}
_trava = asyncio.Lock()
_token: dict = {}    # {"v": token do IPorte decifrado}; o middleware consulta a cada requisição
USO: dict = {"t": 0.0}   # último pedido do IPorte ao proxy: vai para o /api/activity


def _ler() -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        return dict(linha.value) if linha and isinstance(linha.value, dict) else {}


def _gravar(**campos) -> None:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        valor = {**(dict(linha.value) if linha and isinstance(linha.value, dict) else {}), **campos}
        if linha:
            linha.value = valor
        else:
            s.add(db.AppSetting(key=CHAVE, value=valor))
        s.commit()


# ------------------------------------------------------------------ token do IPorte

def token() -> str:
    if "v" not in _token:
        guardado = _ler().get("token") or ""
        aberto = segredo.decifrar(guardado)
        if guardado and not aberto:   # outra conta do Windows ou serviço sem login: não regrava por cima
            _token["v"] = secrets.token_hex(32)   # ponytail: só em memória; o IPorte pareia de novo
            return _token["v"]
        if not aberto:
            aberto = secrets.token_hex(32)
            _gravar(token=segredo.cifrar(aberto))
        _token["v"] = aberto
    return _token["v"]


def rotate() -> str:
    _gravar(token=segredo.cifrar(secrets.token_hex(32)), dispositivo=None)
    _token.clear()
    return token()


def token_ok(dado: str) -> bool:
    return bool(dado) and secrets.compare_digest(dado, token())


def pareamento() -> dict:
    from . import mobile
    return {"tipo": "forja-iporte", "url": mobile.tailnet_url(), "lan": mobile.lan_url(), "token": token()}


def hello(supabase_url: str, anon_key: str, email: str, dispositivo: str = "") -> dict:
    """O IPorte pareado conta onde mora o Supabase dele: a tela Finanças só pede a senha."""
    url = str(supabase_url or "").strip().rstrip("/")
    if not url.startswith("https://") or not anon_key:
        raise ToolError("supabase_url (https) e anon_key são obrigatórios")
    c = _ler()
    campos = {"url": url, "anon": str(anon_key).strip(), "dispositivo": str(dispositivo)[:80] or "IPorte"}
    if email and email != c.get("email"):
        campos["email"] = str(email).strip()
        if c.get("refresh_token"):   # outra conta no celular: a sessão antiga não serve
            campos["refresh_token"] = ""
            _acesso.clear()
    _gravar(**campos)
    return estado()


# ------------------------------------------------------------------ sessão Supabase

def estado() -> dict:
    c = _ler()
    return {"conectado": bool(c.get("refresh_token")), "email": c.get("email") or None, "url": c.get("url") or "",
            "tem_projeto": bool(c.get("url") and c.get("anon")), "dispositivo": c.get("dispositivo") or None,
            "modelo": c.get("modelo") or None, "uso": USO["t"]}


def modelo(spec: dict | None = None) -> dict:
    """Grava (com spec) ou devolve o modelo das finanças; sem escolha, o do assistente de voz."""
    if spec is not None:
        _gravar(modelo={"provider": spec["provider"], "model": spec["model"]} if spec.get("model") else None)
    m = _ler().get("modelo")
    if m:
        return dict(m)
    from . import assistente
    return assistente.modelo_assistente()


def _guarda_sessao(r: dict) -> None:
    _acesso.update(token=r["access_token"], expira=time.time() + int(r.get("expires_in") or 3600))
    _gravar(refresh_token=segredo.cifrar(r["refresh_token"]))


async def _projeto() -> tuple[str, str]:
    """URL e anon key do Supabase: as que o IPorte pareado mandou (hello) ou as da API do IPorte."""
    c = _ler()
    if c.get("url") and c.get("anon"):
        return c["url"], c["anon"]
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.get(f"{IPORTE_API.rstrip('/')}/v1/app/config")
        cfg = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        cfg = {}
    url, anon = str(cfg.get("supabaseUrl") or "").rstrip("/"), str(cfg.get("supabaseAnonKey") or "")
    if not url.startswith("https://") or not anon:
        raise ToolError("Não deu para falar com o IPorte agora. Pareie o celular pelo QR ou tente de novo.")
    return url, anon


async def conectar(email: str, senha: str) -> dict:
    url, anon = await _projeto()
    if not email or not senha:
        raise ToolError("E-mail e senha do IPorte são obrigatórios.")
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post(f"{url}/auth/v1/token", params={"grant_type": "password"}, headers={"apikey": anon},
                           json={"email": email.strip(), "password": senha})
    if r.status_code != 200:
        raise ToolError(_erro(r, "Login recusado pelo IPorte"))
    _gravar(url=url, anon=anon, email=email.strip())
    _guarda_sessao(r.json())
    return estado()


def desconectar() -> dict:
    _acesso.clear()
    _gravar(refresh_token="")
    return estado()


def _erro(r: httpx.Response, padrao: str) -> str:
    try:
        b = r.json()
        return b.get("error_description") or b.get("msg") or b.get("message") or padrao
    except ValueError:
        return f"{padrao} (HTTP {r.status_code})"


async def _jwt() -> str:
    async with _trava:   # o refresh token rotaciona: duas renovações ao mesmo tempo derrubariam a sessão
        if _acesso.get("token") and _acesso["expira"] - time.time() > 60:
            return _acesso["token"]
        c = _ler()
        rt = segredo.decifrar(c.get("refresh_token") or "")
        if not rt:
            raise ToolError("Entre com a conta do IPorte na tela Finanças.")
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.post(f"{c['url']}/auth/v1/token", params={"grant_type": "refresh_token"},
                               headers={"apikey": c["anon"]}, json={"refresh_token": rt})
        if r.status_code in (400, 401):
            desconectar()
            raise ToolError("A sessão do IPorte expirou: entre de novo na tela Finanças.")
        if r.status_code != 200:
            raise ToolError(_erro(r, "Supabase indisponível"))
        _guarda_sessao(r.json())
        return _acesso["token"]


async def _tabela(cli: httpx.AsyncClient, nome: str, select: str = "*", **filtros) -> list[dict]:
    """PostgREST com paginação: o Supabase corta em 1000 linhas por resposta."""
    c = _ler()
    linhas: list[dict] = []
    while True:
        r = await cli.get(f"{c['url']}/rest/v1/{nome}",
                          params={"select": select, **filtros, "limit": 1000, "offset": len(linhas)},
                          headers={"apikey": c["anon"], "Authorization": f"Bearer {await _jwt()}"})
        if r.status_code != 200:
            raise ToolError(_erro(r, f"Falha ao ler {nome}"))
        lote = r.json()
        linhas += lote
        if len(lote) < 1000:
            return linhas


async def dados() -> dict:
    await _jwt()   # sem conexão: a mensagem de entrar, antes de montar URL sem projeto
    vivo = {"deleted_at": "is.null"}
    async with httpx.AsyncClient(timeout=30) as cli:
        tx, contas, cats, orc, assin, metas, invs, movs, notifs = await asyncio.gather(
            _tabela(cli, "transactions", "id,account_id,category_id,amount,type,description,transaction_date,"
                                         "source,transfer_group,fatura_competencia,created_at", **vivo),
            _tabela(cli, "accounts", "id,nome,kind,saldo_inicial,institution_id,created_at,closing_day,due_day,"
                                     "credit_limit", order="created_at.asc", **vivo),
            _tabela(cli, "categories", "id,nome,tipo,cor,icone", **vivo),
            _tabela(cli, "budgets", "category_id,month,amount_limit", **vivo),
            _tabela(cli, "subscriptions", "nome,valor,dia_cobranca,category_id,ativo", **vivo),
            _tabela(cli, "goals", "nome,target_amount,current_amount,deadline", **vivo),
            _tabela(cli, "investments", "id,institution_id,is_reserve,name,investment_type", **vivo),
            _tabela(cli, "investment_movements", "investment_id,type,amount,movement_date,account_id,note",
                    order="movement_date.asc,created_at.asc", **vivo),
            # a fila de Revisão do app (listUnparsed): financeira, sem conta achada e sem transação
            _tabela(cli, "notification_logs", "id,title,text,package_name,received_at,ai_payload,ai_confidence,"
                                              "ai_verdict", parsed="is.false", transaction_id="is.null",
                    order="received_at.desc", **vivo))
    return {"transacoes": tx, "contas": contas, "categorias": cats, "orcamentos": orc, "assinaturas": assin,
            "metas": metas, "investimentos": invs, "movimentos": movs, "notificacoes": notifs}


# ------------------------------------------------------------------ números (sem IA)
# Mesmas regras do dashboardService do IPorte (app-financias/src/modules/dashboard/services), para os números
# daqui baterem com os do app: cartão conta no mês da FATURA, carteira de investimento vale o livro-razão,
# resgate e rendimento de reserva são receita, aporte em meta/investimento aparece como gasto à parte.

CAPITAL_IN, CAPITAL_OUT, RENDE = ("INITIAL_POSITION", "DEPOSIT", "TRANSFER_IN"), ("WITHDRAW", "TRANSFER_OUT"), ("YIELD", "ADJUSTMENT")


def _capital(m: dict) -> float:
    v = float(m["amount"] or 0)
    return v if m["type"] in CAPITAL_IN else -v if m["type"] in CAPITAL_OUT else 0.0


def _rendimento(m: dict) -> float:
    return float(m["amount"] or 0) if m["type"] in RENDE else 0.0


def _receita_reserva(movs: list[dict]) -> list[tuple[str, float]]:
    """Resgate de investimento-reserva vira receita só até o rendimento acumulado (reserve.ts do IPorte)."""
    por_dia: dict[str, list[dict]] = defaultdict(list)
    for m in movs:
        por_dia[str(m["movement_date"])[:10]].append(m)
    saida, pool = [], 0.0
    for d in sorted(por_dia):
        saque = 0.0
        for m in por_dia[d]:
            v = float(m["amount"] or 0)
            if m["type"] == "YIELD":
                pool = max(0.0, pool + v)
            elif m["type"] == "ADJUSTMENT":
                if m.get("note") == "Resgate de rendimento":
                    saque += abs(v)
                elif m.get("note") == "Transferência de rendimento":
                    pool = max(0.0, pool - abs(v))
                else:
                    pool = max(0.0, pool + v)
            elif m["type"] == "WITHDRAW" and m.get("account_id"):
                saque += abs(v)
        if saque > 0 and pool > 0:
            r = min(saque, pool)
            pool -= r
            saida.append((d, round(r, 2)))
    return saida


def _mes(d: str) -> str:
    return str(d)[:7]


def _meses_ate(hoje: date, n: int) -> list[str]:
    a, m = hoje.year, hoje.month
    saida = []
    for _ in range(n):
        saida.append(f"{a:04d}-{m:02d}")
        a, m = (a, m - 1) if m > 1 else (a - 1, 12)
    return saida[::-1]


def _proximos(hoje: date, n: int) -> list[str]:
    a, m = hoje.year, hoje.month
    saida = []
    for _ in range(n):
        a, m = (a, m + 1) if m < 12 else (a + 1, 1)
        saida.append(f"{a:04d}-{m:02d}")
    return saida


def agregados(d: dict, hoje: date | None = None, meses: int = 12) -> dict:
    """Tudo o que a tela desenha e a IA lê. Transferência (transfer_group) mexe no saldo das contas, mas não é
    receita nem despesa."""
    hoje = hoje or date.today()
    cats = {c["id"]: c for c in d["categorias"]}
    contas = {c["id"]: c for c in d["contas"]}
    invs = d.get("investimentos", [])
    movs = d.get("movimentos", [])
    reservas = {i["id"] for i in invs if i.get("is_reserve")}
    janela = _meses_ate(hoje, meses)
    mes_atual = janela[-1]
    por_mes = {m: {"mes": m, "receitas": 0.0, "despesas": 0.0} for m in janela}
    por_cat_mes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    # carteira de investimento = patrimônio do ledger, na carteira mais antiga do banco do investimento
    carteira: dict = {}
    for c in d["contas"]:
        if c.get("kind") == "investimento":
            carteira.setdefault(c.get("institution_id"), c["id"])
    banco = {i["id"]: i.get("institution_id") for i in invs}
    patrimonio_carteira: dict[str, float] = defaultdict(float)
    for m in movs:
        if m["investment_id"] in banco and (cid := carteira.get(banco[m["investment_id"]])):
            patrimonio_carteira[cid] += _capital(m) + _rendimento(m)
    investido_total = round(sum(_capital(m) + _rendimento(m) for m in movs), 2)

    saldo = {cid: (patrimonio_carteira.get(cid, 0.0) if c.get("kind") == "investimento" else float(c.get("saldo_inicial") or 0))
             for cid, c in contas.items()}
    caixa_ids = {cid for cid, c in contas.items() if c.get("kind") not in ("cartao", "investimento")}
    caixa_mes: dict[str, float] = defaultdict(float)   # variação do caixa por mês (para a linha de saldo)
    maiores = []
    for t in d["transacoes"]:
        v = float(t["amount"] or 0)
        tipo = t["type"]
        sinal = v if tipo == "receita" else -v if tipo == "despesa" else 0.0
        conta = contas.get(t["account_id"]) or {}
        if t["account_id"] in saldo and conta.get("kind") != "investimento":
            saldo[t["account_id"]] += sinal
        if t["account_id"] in caixa_ids:
            caixa_mes[_mes(t["transaction_date"])] += sinal
        if tipo == "transferencia" or t.get("transfer_group"):
            continue
        cartao = conta.get("kind") == "cartao" and t.get("fatura_competencia")
        m = t["fatura_competencia"] if cartao else _mes(t["transaction_date"])
        if m not in por_mes:
            continue
        por_mes[m]["receitas" if tipo == "receita" else "despesas"] += v
        if tipo == "despesa":
            meta = not t.get("category_id") and (t.get("description") or "").startswith("Aporte:")
            nome = "Metas" if meta else cats.get(t.get("category_id"), {}).get("nome") or "Sem categoria"
            por_cat_mes[nome][m] += v
            if m == mes_atual:
                maiores.append({"descricao": t.get("description") or nome, "categoria": nome, "valor": v,
                                "data": t["transaction_date"]})
    # movimentos de investimento que tocaram o caixa: aporte é gasto ("Investimento"), resgate é receita
    for mv in movs:
        m = _mes(mv["movement_date"])
        if not mv.get("account_id") or mv["investment_id"] in reservas or m not in por_mes:
            continue
        cap = _capital(mv)
        if cap > 0:
            por_cat_mes["Investimento"][m] += cap
        elif cap < 0:
            por_mes[m]["receitas"] += -cap
    for i in reservas:
        for dia, r in _receita_reserva([mv for mv in movs if mv["investment_id"] == i]):
            if _mes(dia) in por_mes:
                por_mes[_mes(dia)]["receitas"] += r

    serie = list(por_mes.values())
    for s in serie:
        s["receitas"], s["despesas"] = round(s["receitas"], 2), round(s["despesas"], 2)
    fechados = serie[-4:-1]   # os 3 meses completos antes do atual
    media_desp = sum(s["despesas"] for s in fechados) / max(1, len(fechados))
    media_rec = sum(s["receitas"] for s in fechados) / max(1, len(fechados))
    atual = serie[-1]
    assin = [a for a in d["assinaturas"] if a.get("ativo", True)]
    fixos = round(sum(float(a["valor"] or 0) for a in assin), 2)
    caixa = round(sum(saldo[c] for c in caixa_ids), 2)
    # ponytail: cartão a pagar = saldo devedor de cada cartão (o app separa por ciclo de fatura; o total é o mesmo)
    cartoes = round(sum(max(0.0, -saldo[c]) for c, x in contas.items() if x.get("kind") == "cartao"), 2)
    # caixa no fim de cada mês: refeito de trás para frente com o que entrou/saiu das contas de caixa
    historico, resto = [], caixa
    for m in reversed(janela):
        historico.append({"mes": m, "saldo": round(resto, 2) + 0.0})   # + 0.0: sem "-0" na tela
        resto -= caixa_mes.get(m, 0.0)
    historico.reverse()
    # ponytail: projeção linear (média de 3 meses fechados); sazonalidade e parcelas futuras ficam de fora
    fluxo = media_rec - max(media_desp, fixos)
    projecao = [{"mes": m, "saldo": round(caixa + fluxo * (i + 1), 2)} for i, m in enumerate(_proximos(hoje, 3))]
    por_cat = {k: v[mes_atual] for k, v in por_cat_mes.items() if v.get(mes_atual)}

    orcs = []
    for o in d["orcamentos"]:
        if _mes(o["month"]) != mes_atual:
            continue
        nome = cats.get(o["category_id"], {}).get("nome") or "Sem categoria"
        gasto = round(por_cat.get(nome, 0.0), 2)
        orcs.append({"categoria": nome, "limite": float(o["amount_limit"]), "gasto": gasto})

    categorias = sorted(({"categoria": k, "valor": round(v, 2),
                          "cor": next((c.get("cor") for c in cats.values() if c["nome"] == k), None),
                          "media_3m": round(sum(por_cat_mes[k].get(s["mes"], 0) for s in fechados) / max(1, len(fechados)), 2)}
                         for k, v in por_cat.items()), key=lambda x: -x["valor"])
    return {
        "mes_atual": mes_atual,
        "kpis": {"receitas": atual["receitas"], "despesas": atual["despesas"],
                 "fluxo": round(atual["receitas"] - atual["despesas"], 2), "caixa": caixa,
                 "investido": investido_total, "cartoes_a_pagar": cartoes,
                 "poupanca": round((atual["receitas"] - atual["despesas"]) / atual["receitas"], 3) if atual["receitas"] else None,
                 "media_receitas_3m": round(media_rec, 2), "media_despesas_3m": round(media_desp, 2)},
        "serie": serie,
        "categorias": categorias,
        "contas": sorted(({"id": c["id"], "nome": c["nome"], "tipo": c.get("kind"), "saldo": round(saldo[c["id"]], 2)}
                          for c in d["contas"]), key=lambda x: -x["saldo"]),
        "maiores": sorted(maiores, key=lambda x: -x["valor"])[:8],
        "assinaturas": sorted(({"nome": a["nome"], "valor": float(a["valor"] or 0), "dia": a.get("dia_cobranca")}
                               for a in assin), key=lambda x: -x["valor"]),
        "fixos_mensais": fixos,
        "orcamentos": sorted(orcs, key=lambda x: -(x["gasto"] / x["limite"] if x["limite"] else 0)),
        "metas": [{"nome": g["nome"], "alvo": float(g["target_amount"]), "atual": float(g["current_amount"] or 0),
                   "prazo": g.get("deadline")} for g in d["metas"]],
        "historico": historico,
        "projecao": projecao,
    }


# ------------------------------------------------------------------ IA

PEDIDOS = {
    "panorama": "Faça um panorama do mês atual: como estão receitas, despesas e saldo comparados à média; o que "
                "chama atenção (categorias acima da média, orçamentos estourados); 3 a 5 pontos objetivos.",
    "projecao": "Projete os próximos 3 meses a partir da projeção calculada e dos gastos fixos: diga o saldo "
                "esperado, os riscos (meses no vermelho, metas fora do prazo) e o que mudaria o quadro.",
    "economia": "Dê dicas de economia concretas para ESTA pessoa: aponte categorias, assinaturas e gastos "
                "grandes onde cortar, com o valor estimado de economia por mês em cada dica.",
}
SISTEMA = ("Você é um analista financeiro pessoal. Responda em português do Brasil, em markdown curto (títulos ###, "
           "listas). Use SÓ os números do JSON fornecido — não invente valores; quando estimar, diga que é estimativa. "
           "Valores em R$ com vírgula decimal. Não recomende produtos financeiros específicos.")


ROTULOS = {"panorama": "Panorama do mês", "projecao": "Projeção dos próximos meses", "economia": "Onde economizar"}
CHAT_MAX = 60   # mensagens guardadas; as últimas CONTEXTO vão para o modelo junto com os números do dia
CONTEXTO = 12


def chat() -> list[dict]:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE_ANALISE)
        v = linha.value if linha else None
        return list(v) if isinstance(v, list) else []   # o formato antigo ({tipo: análise}) vira conversa vazia


def _grava_chat(msgs: list[dict]) -> None:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE_ANALISE)
        if linha:
            linha.value = msgs[-CHAT_MAX:]
        else:
            s.add(db.AppSetting(key=CHAVE_ANALISE, value=msgs[-CHAT_MAX:]))
        s.commit()


def limpar_chat() -> None:
    _grava_chat([])


async def analise(tipo: str, pergunta: str = ""):
    """Uma volta do chat: os atalhos (panorama, projeção, economia) entram como pergunta pronta. Os números vão
    frescos no system a cada volta; o histórico dá o contexto da conversa."""
    if tipo not in TIPOS:
        raise ToolError("tipo deve ser " + ", ".join(TIPOS))
    if tipo == "pergunta" and not pergunta.strip():
        raise ToolError("Escreva a pergunta.")
    from . import llm
    spec = modelo()
    numeros = agregados(await dados())
    pedido = pergunta.strip()[:2000] if tipo == "pergunta" else PEDIDOS[tipo]
    historico = chat()
    msgs = [{"role": "system", "content": f"{SISTEMA}\n\nDados de hoje (JSON):\n"
                                                  f"{json.dumps(numeros, ensure_ascii=False)}"},
            *({"role": m["papel"], "content": m["texto"]} for m in historico[-CONTEXTO:]),
            {"role": "user", "content": pedido}]
    texto = ""
    async for ev, val in llm.chat_stream(spec["provider"], spec["model"], msgs, None, 16384, think=False,
                                         max_tokens=2000):
        if ev == "content":
            texto += val
            yield val
    agora = time.time()
    _grava_chat([*historico, {"papel": "user", "texto": pedido, "rotulo": ROTULOS.get(tipo), "quando": agora},
                 {"papel": "assistant", "texto": texto, "quando": time.time(), "modelo": spec["model"]}])


async def completar(corpo: dict) -> dict:
    """Proxy compatível com OpenAI para o IPorte (sem streaming e sem ferramentas). O modelo é o da tela
    Finanças: o celular não escolhe o que roda no PC."""
    from . import llm
    msgs = corpo.get("messages")
    if not isinstance(msgs, list) or not msgs or len(json.dumps(msgs)) > 64_000:
        raise ToolError("messages inválido (lista de até 64 KB)")
    limpas = [{"role": m.get("role") if m.get("role") in ("system", "user", "assistant") else "user",
               "content": str(m.get("content") or "")} for m in msgs if isinstance(m, dict)]
    quer_json = (corpo.get("response_format") or {}).get("type") == "json_object"
    if quer_json:
        limpas.insert(0, {"role": "system", "content": "Responda APENAS com um objeto JSON válido, sem texto fora dele."})
    spec = modelo()
    USO["t"] = time.time()
    texto = ""
    async for ev, val in llm.chat_stream(spec["provider"], spec["model"], limpas, None, 16384, think=False,
                                         max_tokens=min(int(corpo.get("max_tokens") or 1024), 4096)):
        if ev == "content":
            texto += val
    if quer_json:   # modelo local às vezes cerca o JSON com ```; o cliente faz JSON.parse direto
        texto = texto.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return {"id": f"forja-{int(time.time() * 1000)}", "object": "chat.completion", "created": int(time.time()),
            "model": spec["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": texto}}]}


# ------------------------------------------------------------------ escrita (as mesmas regras do app)
# O Forja grava direto no Supabase com o JWT da pessoa (RLS); o app puxa pelo updated_at (default now() no
# servidor e trigger no UPDATE). Exclusão é soft delete (deleted_at), igual ao app. Cada regra abaixo espelha um
# serviço do IPorte: transactionsService, accountsService, investmentsService e notificationReviewService.

KINDS = ("corrente", "poupanca", "dinheiro", "carteira", "investimento", "cartao")
TIPOS_INV = ("cdi", "cdb", "lci", "lca", "tesouro_selic", "tesouro_prefixado", "tesouro_ipca", "poupanca")
ORIGEM = "forja"   # device_id: de onde veio a escrita


def _uid(jwt: str) -> str:
    corpo = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))["sub"]


async def _rest(metodo: str, tabela: str, corpo=None, **params) -> list[dict]:
    jwt = await _jwt()
    c = _ler()
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.request(metodo, f"{c['url']}/rest/v1/{tabela}", params=params, json=corpo,
                              headers={"apikey": c["anon"], "Authorization": f"Bearer {jwt}",
                                       "Prefer": "return=representation"})
    if r.status_code >= 300:
        raise ToolError(_erro(r, f"Falha ao gravar em {tabela}"))
    return r.json() if r.content else []


async def _um(tabela: str, id_: str, select: str = "*") -> dict:
    linhas = await _rest("GET", tabela, select=select, id=f"eq.{id_}", deleted_at="is.null") if id_ else []
    if not linhas:
        raise ToolError("Registro não encontrado.")
    return linhas[0]


def _valor(v) -> float:
    try:
        x = round(float(str(v).replace(",", ".")), 2)
    except (TypeError, ValueError):
        x = 0.0
    if x <= 0:
        raise ToolError("Valor tem de ser maior que zero.")
    return x


def _data(v) -> str:
    try:
        return date.fromisoformat(str(v or date.today().isoformat())[:10]).isoformat()
    except ValueError:
        raise ToolError("Data inválida (use AAAA-MM-DD).")


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _soma_mes(chave: str, n: int) -> str:
    a, m = map(int, chave.split("-"))
    t = a * 12 + m - 1 + n
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def _mais_meses(dia: str, n: int) -> str:
    a, m, d = map(int, dia.split("-"))
    a2, m2 = map(int, _soma_mes(f"{a:04d}-{m:02d}", n).split("-"))
    return date(a2, m2, min(d, calendar.monthrange(a2, m2)[1])).isoformat()


def ciclo(data_iso: str, fecha: int) -> str:
    """Competência (yyyy-MM do fechamento) da fatura em que a data cai: cycleOf de billing.ts."""
    a, m, d = map(int, data_iso[:10].split("-"))
    return _soma_mes(f"{a:04d}-{m:02d}", 1 if d >= min(fecha, calendar.monthrange(a, m)[1]) else 0)


def competencia(data_iso: str, fecha: int, hoje: date | None = None) -> str:
    """resolveCompetencia com a fatura aberta de hoje: retroativo cai na própria, o resto na aberta."""
    return min(ciclo(data_iso, fecha), ciclo((hoje or date.today()).isoformat(), fecha))


def _base(uid: str) -> dict:
    return {"user_id": uid, "device_id": ORIGEM}


def _feedback(log: dict, qual: str) -> dict:
    return {"feedback": qual, "feedback_at": _agora()} if log.get("ai_verdict") else {}


def _transferencia(uid: str, de: str, para: str, valor: float, dia: str, desc: str) -> list[dict]:
    """Despesa na origem + receita no destino, ligadas por transfer_group (createTransfer)."""
    base = {**_base(uid), "transaction_date": dia, "source": "manual", "category_id": None,
            "transfer_group": str(uuid.uuid4()), "amount": valor, "description": desc}
    return [{**base, "id": str(uuid.uuid4()), "account_id": de, "type": "despesa"},
            {**base, "id": str(uuid.uuid4()), "account_id": para, "type": "receita"}]


async def nova_transacao(b: dict) -> dict:
    tipo = b.get("tipo")
    if tipo not in ("receita", "despesa", "transferencia"):
        raise ToolError("tipo: receita, despesa ou transferencia")
    valor, dia = _valor(b.get("valor")), _data(b.get("data"))
    uid = _uid(await _jwt())
    conta = await _um("accounts", str(b.get("conta") or ""), "id,kind,closing_day")
    desc = str(b.get("descricao") or "").strip()[:200]
    if tipo == "transferencia":
        destino = await _um("accounts", str(b.get("destino") or ""), "id")
        if destino["id"] == conta["id"]:
            raise ToolError("Origem e destino são a mesma conta.")
        linhas = _transferencia(uid, conta["id"], destino["id"], valor, dia, desc or "Transferência")
    else:
        comp = competencia(dia, conta["closing_day"]) if conta.get("kind") == "cartao" and conta.get("closing_day") else None
        n = max(1, min(int(b.get("parcelas") or 1), 48)) if tipo == "despesa" else 1
        base = {**_base(uid), "account_id": conta["id"], "type": tipo, "source": "manual",
                "category_id": b.get("categoria") or None}
        if n == 1:
            linhas = [{**base, "id": str(uuid.uuid4()), "amount": valor, "description": desc, "transaction_date": dia,
                       "fatura_competencia": comp}]
        else:   # createInstallments: os centavos da divisão vão na última parcela, uma fatura por mês
            centavos, grupo = round(valor * 100), str(uuid.uuid4())
            parte = centavos // n
            linhas = [{**base, "id": str(uuid.uuid4()),
                       "amount": (centavos - parte * (n - 1) if i == n - 1 else parte) / 100,
                       "description": f"{desc} ({i + 1}/{n})", "transaction_date": _mais_meses(dia, i),
                       "installment_group": grupo, "installment_no": i + 1, "installment_total": n,
                       "fatura_competencia": _soma_mes(comp, i) if comp else None} for i in range(n)]
    await _rest("POST", "transactions", linhas)
    if b.get("log_id"):   # veio da Revisão: a notificação fica tratada, e a IA aprende com a correção
        log = await _um("notification_logs", str(b["log_id"]), "id,ai_verdict,sync_version")
        await _atualiza("notification_logs", log, {"parsed": True, "status": "created", "transaction_id": linhas[0]["id"],
                                                   **_feedback(log, "corrigida")})
    return {"ids": [x["id"] for x in linhas]}


async def apagar_transacao(id_: str) -> dict:
    """Transferência sai com as duas pernas; parcela sai sozinha (como no app)."""
    t = await _um("transactions", id_, "id,transfer_group,sync_version")
    pernas = (await _rest("GET", "transactions", select="id,sync_version", transfer_group=f"eq.{t['transfer_group']}",
                          deleted_at="is.null") if t.get("transfer_group") else [t])
    for x in pernas:
        await _atualiza("transactions", x, {"deleted_at": _agora()})
    return {"apagadas": len(pernas)}


async def _atualiza(tabela: str, linha: dict, patch: dict) -> None:
    """UPDATE no contrato do sync do app: sync_version + 1 (desempate do LWW); o updated_at o trigger põe."""
    # ponytail: ler-e-somar, não atômico; duas edições no mesmo instante empatam e o updated_at desempata
    await _rest("PATCH", tabela, {**patch, "device_id": ORIGEM, "sync_version": int(linha.get("sync_version") or 0) + 1},
                id=f"eq.{linha['id']}")


async def nova_conta(b: dict) -> dict:
    nome, kind = str(b.get("nome") or "").strip()[:80], b.get("kind") or "corrente"
    if not nome or kind not in KINDS:
        raise ToolError("Nome e tipo da conta são obrigatórios.")
    linha = {**_base(_uid(await _jwt())), "id": str(uuid.uuid4()), "nome": nome, "kind": kind,
             "saldo_inicial": round(float(b.get("saldo_inicial") or 0), 2), "institution_id": None, "is_default": False}
    if kind == "cartao":
        fecha, vence = int(b.get("closing_day") or 0), int(b.get("due_day") or 0)
        if not (1 <= fecha <= 31 and 1 <= vence <= 31):
            raise ToolError("Cartão precisa do dia de fechamento e do vencimento (1 a 31).")
        linha.update(closing_day=fecha, due_day=vence,
                     credit_limit=round(float(b["credit_limit"]), 2) if b.get("credit_limit") else None)
    return (await _rest("POST", "accounts", [linha]))[0]


async def _carteira(uid: str, banco: str | None) -> str:
    """ensureInvestmentAccount: a carteira de investimento do banco; cria se não houver."""
    filtro = {"institution_id": f"eq.{banco}"} if banco else {"institution_id": "is.null"}
    achadas = await _rest("GET", "accounts", select="id", kind="eq.investimento", deleted_at="is.null",
                          order="created_at.asc", **filtro)
    if achadas:
        return achadas[0]["id"]
    nome = "Investimentos"
    if banco:
        inst = await _rest("GET", "institutions", select="name", id=f"eq.{banco}")
        nome = f"Investimentos · {inst[0]['name']}" if inst else nome
    nova = {**_base(uid), "id": str(uuid.uuid4()), "nome": nome, "kind": "investimento", "institution_id": banco,
            "saldo_inicial": 0, "is_default": False}
    return (await _rest("POST", "accounts", [nova]))[0]["id"]


def _mov(uid: str, inv: str, tipo: str, valor: float, dia: str, conta: str | None, nota: str | None = None) -> dict:
    return {**_base(uid), "id": str(uuid.uuid4()), "investment_id": inv, "type": tipo, "amount": valor,
            "movement_date": dia, "account_id": conta, "note": nota}


async def novo_investimento(b: dict) -> dict:
    nome, tipo = str(b.get("nome") or "").strip()[:80], b.get("tipo") or "cdb"
    if not nome or tipo not in TIPOS_INV:
        raise ToolError("Nome e tipo do investimento são obrigatórios.")
    valor, dia = _valor(b.get("valor")), _data(b.get("data"))
    uid = _uid(await _jwt())
    origem = str(b.get("conta") or "") or None
    if origem:
        await _um("accounts", origem, "id")
    inv = {**_base(uid), "id": str(uuid.uuid4()), "name": nome, "investment_type": tipo,
           "yield_type": "prefixado" if tipo == "tesouro_prefixado" else "cdi",
           "yield_percentage": round(float(b.get("rendimento") or 100), 2), "application_date": dia,
           "institution_id": None, "is_preexisting": origem is None, "is_reserve": bool(b.get("reserva"))}
    await _rest("POST", "investments", [inv])
    # ponytail: sem o YIELD estimado retroativo que o app lança em posição antiga; o app acumula daqui em diante
    if origem:   # dinheiro novo: sai da conta para a carteira; pré-existente não mexe no caixa
        await _rest("POST", "transactions", _transferencia(uid, origem, await _carteira(uid, None), valor, dia,
                                                           f"Aporte: {nome}"))
    await _rest("POST", "investment_movements", [_mov(uid, inv["id"], "INITIAL_POSITION", valor, dia, origem)])
    return {"id": inv["id"]}


async def movimento_investimento(b: dict) -> dict:
    tipo = b.get("tipo")
    if tipo not in ("aporte", "resgate"):
        raise ToolError("tipo: aporte ou resgate")
    valor, dia = _valor(b.get("valor")), _data(b.get("data"))
    uid = _uid(await _jwt())
    inv = await _um("investments", str(b.get("investimento") or ""), "id,name,institution_id")
    conta = str(b.get("conta") or "") or None
    if conta:
        await _um("accounts", conta, "id")
    carteira = await _carteira(uid, inv.get("institution_id")) if conta else None
    if tipo == "aporte":
        if conta:
            await _rest("POST", "transactions", _transferencia(uid, conta, carteira, valor, dia, f"Aporte: {inv['name']}"))
        await _rest("POST", "investment_movements", [_mov(uid, inv["id"], "DEPOSIT", valor, dia, conta)])
        return {"ok": True}
    movs = await _rest("GET", "investment_movements", select="type,amount", investment_id=f"eq.{inv['id']}",
                       deleted_at="is.null")
    capital = sum(_capital(m) for m in movs)
    if valor > capital + sum(_rendimento(m) for m in movs) + 0.005:
        raise ToolError("Valor maior que o patrimônio do investimento.")
    parte_cap = min(valor, max(0.0, capital))   # drena o capital primeiro; o excedente realiza o rendimento
    resto = round(valor - parte_cap, 2)
    novos = []
    if parte_cap > 0.005:
        novos.append(_mov(uid, inv["id"], "WITHDRAW", round(parte_cap, 2), dia, conta))
    if resto > 0.005:
        novos.append(_mov(uid, inv["id"], "ADJUSTMENT", -resto, dia, None if parte_cap > 0.005 else conta,
                          "Resgate de rendimento"))
    if conta:
        await _rest("POST", "transactions", _transferencia(uid, carteira, conta, valor, dia, f"Resgate: {inv['name']}"))
    await _rest("POST", "investment_movements", novos)
    return {"ok": True}


async def revisar_notificacao(id_: str, acao: str) -> dict:
    """Ignorar = não era transação (vira exemplo negativo para a IA); descartar = só sai da fila."""
    if acao not in ("ignorar", "descartar"):
        raise ToolError("acao: ignorar ou descartar")
    log = await _um("notification_logs", id_, "id,ai_verdict,sync_version")
    patch = {"parsed": True, "status": "ignored" if acao == "ignorar" else "dismissed"}
    if acao == "ignorar":
        patch.update(_feedback(log, "desfeita"))
    await _atualiza("notification_logs", log, patch)
    return {"ok": True}


def cadastro(d: dict) -> dict:
    """O que as abas de lançamento usam: listas com id (os agregados só trazem números)."""
    contas = {c["id"]: c for c in d["contas"]}
    cats = {c["id"]: c for c in d["categorias"]}
    patrimonio: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for m in d.get("movimentos", []):
        patrimonio[m["investment_id"]][0] += _capital(m)
        patrimonio[m["investment_id"]][1] += _rendimento(m)
    tx = sorted(d["transacoes"], key=lambda t: (str(t["transaction_date"]), str(t.get("created_at") or "")), reverse=True)
    return {
        "lancamentos": [{"id": t["id"], "data": t["transaction_date"], "descricao": t.get("description") or "",
                         "valor": float(t["amount"] or 0), "tipo": t["type"], "conta": (contas.get(t["account_id"]) or {}).get("nome"),
                         "categoria": (cats.get(t.get("category_id")) or {}).get("nome"),
                         "transferencia": bool(t.get("transfer_group")), "origem": t.get("source")} for t in tx[:400]],
        "contas_lista": [{"id": c["id"], "nome": c["nome"], "kind": c.get("kind"), "fecha": c.get("closing_day"),
                          "vence": c.get("due_day"), "limite": c.get("credit_limit")} for c in d["contas"]],
        "categorias_lista": sorted(({"id": c["id"], "nome": c["nome"], "tipo": c.get("tipo"), "cor": c.get("cor")}
                                    for c in d["categorias"]), key=lambda c: c["nome"]),
        "investimentos_lista": [{"id": i["id"], "nome": i.get("name") or "Investimento", "tipo": i.get("investment_type"),
                                 "reserva": bool(i.get("is_reserve")), "capital": round(patrimonio[i["id"]][0], 2),
                                 "rendimento": round(patrimonio[i["id"]][1], 2),
                                 "patrimonio": round(sum(patrimonio[i["id"]]), 2)} for i in d.get("investimentos", [])],
        "revisao": [{"id": n["id"], "titulo": n.get("title") or "", "texto": n.get("text") or "",
                     "app": n.get("package_name") or "", "quando": n.get("received_at"),
                     "ia": _json_ou_nada(n.get("ai_payload")), "confianca": n.get("ai_confidence"),
                     "veredito": n.get("ai_verdict")} for n in d.get("notificacoes", [])],
    }


def _json_ou_nada(v):
    if isinstance(v, dict) or v is None:
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None

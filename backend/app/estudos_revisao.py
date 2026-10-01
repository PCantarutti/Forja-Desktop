"""Extras da tela Estudos: caderno de erros e flashcards com revisão espaçada, desempenho, cronograma até a
data da prova e o lembrete do dia no celular.

Sem tabela nova, como o resto:
- "revisao" (user, uma por estudo): o estado de cada item revisado, o cronograma e o dia do último aviso —
  {"tipo": "revisao", "itens": {chave: {caixa, proxima, acertos, erros, dominada}}, "plano": {...} | None,
  "avisado": "AAAA-MM-DD"}. Chave "q:<prova>:<questão>" (questão errada numa entrega) ou "f:<cartão>".
- "flashcards" (assistant): um lote de cartões, gerado como a prova (execução com SSE, ou pedido ao Claude).
  meta.cartoes = [{id, frente, verso, topico, origem: resumo|erro}].

O caderno de erros não é guardado: sai das entregas (toda questão que não saiu certa). Item novo entra na
caixa 1, para hoje; acertou sobe de caixa e espaça (INTERVALOS); errou volta para a 1. Acertou na 5: dominado.
"""
from __future__ import annotations

import asyncio
import copy
import csv
import html
import io
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta

from sqlalchemy import select

from . import db, estudos as E, mirror, mobile, pesquisa, web
from .agent import _save
from .tools import ToolError

log = logging.getLogger(__name__)

INTERVALOS = (1, 3, 7, 15, 30)   # dias até a próxima revisão, por caixa
MAX_CARTOES = 60                 # por geração
TETO_PARTE = 300                 # segundos por chamada de cartões
PARTE_CHARS = 12_000             # resumo por chamada
MAX_DIAS = 180                   # cronograma
HORA_AVISO = 8                   # o lembrete não sai antes disto
_TRAVA = threading.Lock()        # ponytail: uma trava para a revisão de todos os estudos; um usuário só

CARTOES_PROMPT = """Você faz flashcards de estudo, no idioma do resumo. Escreva EXATAMENTE os cartões pedidos.
Responda SÓ com um objeto JSON, sem texto antes nem depois:
{"cartoes": [{"frente": "pergunta curta", "verso": "resposta curta", "topico": "título do tópico"}]}
- Frente: uma pergunta direta ou um termo que puxe UMA ideia (definição, fórmula, causa, diferença, exemplo).
- Verso: a resposta em até duas frases, ou a fórmula com o que cada letra é.
- Tudo sustentado no resumo e nos erros abaixo; não invente dado.
- Dos erros do aluno, faça cartões do conceito, da fórmula ou do passo que ele errou — sem os números nem a
  situação da questão (não "qual a probabilidade de 2 meninos entre 8 meninos e 7 meninas?", e sim "probabilidade
  com combinação: como montar?").
- Nada que o aluno já tem (lista no fim) nem dois cartões da mesma ideia.
- Fórmulas em LaTeX ($...$)."""


def _hoje() -> date:
    return date.today()


# ------------------------------------------------------------------ estado da revisão


def _mensagens(conv_id: int) -> list[db.Message]:
    with db.session() as s:
        E._conv(s, conv_id)
        return list(s.scalars(select(db.Message).where(db.Message.conversation_id == conv_id).order_by(db.Message.id)))


def _tipo(m: db.Message) -> str:
    return ((m.meta or {}).get("estudos") or {}).get("tipo") or ""


def _ler(conv_id: int) -> dict:
    m = next((m for m in _mensagens(conv_id) if _tipo(m) == "revisao"), None)
    e = copy.deepcopy(m.meta["estudos"]) if m else {}
    return {"tipo": "revisao", "itens": {}, "plano": None, "avisado": "", **e}


def _mudar(conv_id: int, f) -> dict:
    """Lê, deixa `f` mexer numa cópia e grava o dict inteiro (JSON sem MutableDict). Cria a mensagem na 1ª vez."""
    with _TRAVA:
        rev = _ler(conv_id)
        f(rev)
        with db.session() as s:
            m = next((x for x in s.scalars(select(db.Message).where(db.Message.conversation_id == conv_id,
                                                                     db.Message.role == "user"))
                      if _tipo(x) == "revisao"), None)
            if m:
                m.meta = {**(m.meta or {}), "estudos": rev}
                E._tocar(s, conv_id)
                s.commit()
        if not m:
            _save(conv_id, role="user", content="Revisão e cronograma", status="pronto", meta={"estudos": rev})
    return rev


def _novo(hoje: date) -> dict:
    return {"caixa": 1, "proxima": hoje.isoformat(), "acertos": 0, "erros": 0, "dominada": False}


def responder_item(item: dict, acertou: bool, hoje: date) -> dict:
    """Leitner: acertou sobe de caixa e espaça; errou volta para a 1 e aparece amanhã; acertou na 5, dominado."""
    item = {**item}
    if acertou:
        item["acertos"] += 1
        item["dominada"] = item["caixa"] >= len(INTERVALOS)
        item["caixa"] = min(len(INTERVALOS), item["caixa"] + 1)
        dias = INTERVALOS[item["caixa"] - 1]
    else:
        item["erros"] += 1
        item["caixa"], dias = 1, 1
    item["proxima"] = (hoje + timedelta(days=dias)).isoformat()
    return item


# ------------------------------------------------------------------ caderno de erros e cartões


def erros(conv_id: int, msgs: list[db.Message] | None = None) -> list[dict]:
    """Toda questão que não saiu certa numa entrega (errada, parcial ou em branco), uma vez cada, com a
    questão inteira (a entrega já revelou o gabarito) e a resposta mais recente do aluno."""
    msgs = msgs if msgs is not None else _mensagens(conv_id)
    provas = {m.id: m.meta["estudos"] for m in msgs if _tipo(m) == "prova"}
    out: dict[str, dict] = {}
    for m in msgs:
        e = (m.meta or {}).get("estudos") or {}
        if e.get("tipo") != "tentativa" or m.status not in ("pronto", "cancelado"):
            continue
        p = provas.get(e["prova_id"]) or {}
        questoes = {q["id"]: q for q in p.get("questoes") or []}
        for qid, c in (e.get("correcao") or {}).items():
            if c.get("certa") is True or c.get("pendente") or qid not in questoes:
                continue
            chave = f"q:{e['prova_id']}:{qid}"
            out[chave] = {"chave": chave, "tipo": "erro", "questao": questoes[qid], "resposta": c.get("resposta"),
                          "prova": p.get("titulo", "Prova"), "topico": questoes[qid].get("topico", ""),
                          "tentativa_id": m.id}
    return list(out.values())


def cartoes(conv_id: int, msgs: list[db.Message] | None = None) -> list[dict]:
    msgs = msgs if msgs is not None else _mensagens(conv_id)
    return [{"chave": f"f:{c['id']}", "tipo": "cartao", **c} for m in msgs
            if _tipo(m) == "flashcards" and m.status in ("pronto", "cancelado") for c in m.meta["estudos"].get("cartoes") or []]


def painel(conv_id: int) -> dict:
    """A aba Revisão: os itens (erros e cartões) com o estado de cada um, o cronograma e as gerações de cartões."""
    hoje = _hoje()
    msgs = _mensagens(conv_id)
    rev = next((m.meta["estudos"] for m in msgs if _tipo(m) == "revisao"), {})
    itens = []
    for x in erros(conv_id, msgs) + cartoes(conv_id, msgs):
        st = (rev.get("itens") or {}).get(x["chave"]) or _novo(hoje)
        itens.append({**x, **st, "vence": not st["dominada"] and st["proxima"] <= hoje.isoformat()})
    geracoes = [{"message_id": m.id, "status": E._situacao(m.status), "n": len(m.meta["estudos"].get("cartoes") or []),
                 "motor": m.meta["estudos"].get("motor", "forja"), "aviso": m.meta["estudos"].get("aviso", ""),
                 "criado": E.quando(m.created_at)}
                for m in msgs if _tipo(m) == "flashcards"]
    return {"hoje": hoje.isoformat(), "itens": itens, "vencem": sum(x["vence"] for x in itens),
            "plano": rev.get("plano"), "geracoes": geracoes}


def revisar(conv_id: int, chave: str, acertou: bool, tirar: bool = False) -> dict:
    """Resposta de um item na sessão de revisão. `tirar`: sai da revisão de vez (questão com defeito, ou que o
    aluno já domina) — fica como dominado, e a entrega dele continua igual."""
    hoje = _hoje()
    chaves = {x["chave"] for x in erros(conv_id) + cartoes(conv_id)}
    if chave not in chaves:
        raise ToolError("Item de revisão não encontrado neste estudo.")

    def f(rev):
        atual = rev["itens"].get(chave) or _novo(hoje)
        rev["itens"][chave] = {**atual, "dominada": True} if tirar else responder_item(atual, bool(acertou), hoje)
    rev = _mudar(conv_id, f)
    mirror.write(conv_id)
    return rev["itens"][chave]


def apagar_cartao(conv_id: int, cartao_id: str) -> dict:
    with db.session() as s:
        for m in s.scalars(select(db.Message).where(db.Message.conversation_id == conv_id, db.Message.role == "assistant")):
            e = (m.meta or {}).get("estudos") or {}
            if e.get("tipo") == "flashcards" and any(c["id"] == cartao_id for c in e.get("cartoes") or []):
                if m.id in E._RUNS:
                    raise ToolError("Os cartões ainda estão sendo gerados.")
                m.meta = {**m.meta, "estudos": {**e, "cartoes": [c for c in e["cartoes"] if c["id"] != cartao_id]}}
                E._tocar(s, conv_id)
                s.commit()
                break
        else:
            raise ToolError("Cartão não encontrado.")
    _mudar(conv_id, lambda rev: rev["itens"].pop(f"f:{cartao_id}", None))
    mirror.write(conv_id)
    return {"ok": True}


BLOCO = re.compile(r"\$\$(.+?)\$\$", re.S)
EM_LINHA = re.compile(r"(?<![\w\\$])\$(?!\s)([^$\n]+?)(?<!\s)\$")   # "R$ 300" fica: tem letra antes


def _mathjax(texto: str) -> str:
    r"""O Anki desenha fórmula em \( \) e \[ \] (MathJax), não em $."""
    return EM_LINHA.sub(r"\\(\1\\)", BLOCO.sub(r"\\[\1\\]", texto))


def anki_csv(conv_id: int) -> str:
    """frente,verso,tópico — o Anki importa direto (Arquivo › Importar; campos separados por vírgula)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    for c in cartoes(conv_id):
        w.writerow([_mathjax(c["frente"]), _mathjax(c["verso"]), (c.get("topico") or "").replace(" ", "_")])
    return buf.getvalue()


# ------------------------------------------------------------------ gerar cartões


def _limpar_cartoes(brutos, vistos: set[str], topicos: list[str]) -> list[dict]:
    from .estudos_prova import _norm, _txt
    out = []
    for c in brutos if isinstance(brutos, list) else []:
        if not isinstance(c, dict):
            continue
        frente, verso = _txt(c.get("frente"), 500), _txt(c.get("verso"), 1500)
        chave = _norm(frente).strip(" ?.")
        if len(frente) < 3 or not verso or chave in vistos:
            continue
        vistos.add(chave)
        topico = _txt(c.get("topico"), 120)
        out.append({"frente": frente, "verso": verso, "topico": topico if topico in topicos else (topicos[0] if len(topicos) == 1 else topico)})
    return out


def _partes(secoes: dict[str, str], erros_: list[dict], quantos: int) -> list[dict]:
    """Os tópicos em grupos que cabem numa chamada, cada um com a sua parte dos cartões."""
    grupos: list[list[str]] = []
    tam = 0
    for t, texto in secoes.items():
        if grupos and tam + len(texto) <= PARTE_CHARS:
            grupos[-1].append(t)
            tam += len(texto)
        else:
            grupos.append([t])
            tam = len(texto)
    if not grupos:
        grupos = [[e["topico"] or "Erros"] for e in erros_[:1]]
    total = sum(len(g) for g in grupos) or 1
    return [{"id": f"p{i + 1}", "topicos": g, "n": max(2, round(quantos * len(g) / total)), "status": "fila"}
            for i, g in enumerate(grupos)]


def start(conv_id: int, quantos: int = 20, provider: str = "", model: str = "") -> dict:
    """Cria a mensagem do lote de cartões e dispara a geração (ou deixa o pedido para o Claude)."""
    from . import estudos_prova
    quantos = max(4, min(E._int(quantos) or 20, MAX_CARTOES))
    _, _, _, secoes, _ = estudos_prova._base(conv_id)
    erros_ = erros(conv_id)
    if not secoes and not erros_:
        raise ToolError("Gere o resumo (ou faça uma prova) antes: os cartões saem dele e dos seus erros.")
    _, escritor, claude = E.modelos(provider, model)
    if E.rodando(conv_id):
        raise ToolError("Este estudo já está rodando. Espere terminar ou pare antes.")
    publico = {"tipo": "flashcards", "titulo": "Flashcards", "quantos": quantos, "motor": "claude" if claude else "forja",
               "status": "aguardando" if claude else "rodando", "etapa": "cartoes", "aviso": "", "cartoes": [],
               "partes": _partes(secoes, erros_, quantos), "stats": E.stats_novos(escritor, escritor)}
    msg = _save(conv_id, role="assistant", content="", status="aguardando" if claude else "running", meta={"estudos": publico})
    if claude:
        return msg.to_dict()
    run = {**publico, "message_id": msg.id, "conv_id": conv_id, "cancelar": False, "t0": time.monotonic(),
           "teto": TETO_PARTE, "texto": "", "gravar": E._gravar,
           "_secoes": secoes, "_erros": erros_, "_ja": [c["frente"] for c in cartoes(conv_id)]}
    E.disparar(run, _rodar(run, escritor))
    return msg.to_dict()


def _bloco_erros(erros_: list[dict]) -> str:
    return "\n".join(f"- [{e['topico']}] {e['questao']['enunciado'][:300]} → {e['questao'].get('explicacao', '')[:300]}"
                     for e in erros_)


async def _rodar(run: dict, spec: dict) -> None:
    from . import design, estudos_prova
    from .estudos_prova import _norm
    vistos = {_norm(f).strip(" ?.") for f in run["_ja"]}
    try:
        await design._garantir_local({"spec": spec})
        for parte in run["partes"]:
            if run["cancelar"]:
                break
            E._teto(run, TETO_PARTE)
            parte["status"] = "gerando"
            E._gravar(run)
            resumo = "\n\n".join(f"## {t}\n{run['_secoes'][t]}" for t in parte["topicos"] if t in run["_secoes"])
            seus = [e for e in run["_erros"] if e["topico"] in parte["topicos"] or len(run["partes"]) == 1]
            user = (f"Cartões a escrever: {parte['n']}, dos tópicos: {', '.join(parte['topicos'])}\n\n"
                    + (f"Resumo:\n{resumo}\n\n" if resumo else "")
                    + (f"Questões que o aluno errou (com a explicação):\n{web.UNTRUSTED}{_bloco_erros(seus)}\n\n" if seus else "")
                    + ("Cartões que o aluno já tem:\n" + "\n".join(f"- {f[:120]}" for f in run["_ja"][-80:]) if run["_ja"] else ""))
            try:
                obj = estudos_prova._json(await pesquisa._perguntar(spec, CARTOES_PROMPT, user, run, effort="baixo")) or {}
            except Exception as e:
                obj = {}
                E._avisar(run, f"Uma parte falhou: {e.__class__.__name__}.")
            if run["cancelar"]:
                break
            novos = _limpar_cartoes(obj.get("cartoes") if isinstance(obj, dict) else None, vistos, parte["topicos"])
            base = len(run["cartoes"])
            run["cartoes"] += [{"id": f"{run['message_id']}-{base + i + 1}", **c, "origem": "erro" if seus else "resumo"}
                               for i, c in enumerate(novos)]
            parte["status"] = "ok" if novos else "erro"
            E._gravar(run)
        if run["cancelar"]:
            run["status"] = "cancelado"
            E._avisar(run, "Interrompido: os cartões que já saíram ficaram.")
        elif not run["cartoes"]:
            run["status"] = "erro"
            E._avisar(run, "O modelo não devolveu cartões legíveis.")
        else:
            run["status"] = "pronto"
    except Exception as e:
        run["status"] = "erro"
        E._avisar(run, f"{e.__class__.__name__}: {e}"[:300])
    finally:
        from .estudos_prova import _fechar
        _fechar(run)


# ------------------------------------------------------------------ desempenho e cronograma


def desempenho(conv_id: int) -> dict:
    """Notas na ordem das entregas, acerto por tópico somando todas, os pontos fracos e a revisão."""
    from . import estudos_prova
    msgs = _mensagens(conv_id)
    provas = {m.id: m.meta["estudos"] for m in msgs if _tipo(m) == "prova"}
    entregas, por = [], {}
    for m in msgs:
        e = (m.meta or {}).get("estudos") or {}
        if e.get("tipo") != "tentativa" or m.status != "pronto":
            continue
        entregas.append({"message_id": m.id, "prova_id": e["prova_id"], "titulo": e.get("titulo", "Prova"),
                         "nota": e.get("nota", 0), "acertos": e.get("acertos", 0),
                         "n": len((provas.get(e["prova_id"]) or {}).get("questoes") or []), "modo": e.get("modo", "prova"),
                         "segundos": e.get("segundos", 0), "criado": E.quando(m.created_at)})
        for t in e.get("por_topico") or []:
            x = por.setdefault(t["topico"], {"topico": t["topico"], "pontos": 0.0, "max": 0.0, "ultima": None})
            x["pontos"] += t["pontos"]
            x["max"] += t["max"]
            x["ultima"] = round(t["pontos"] / t["max"], 2) if t["max"] else None
    for t in estudos_prova.topicos(conv_id) if any(_tipo(m) == "resumo" for m in msgs) else []:
        por.setdefault(t, {"topico": t, "pontos": 0.0, "max": 0.0, "ultima": None})
    topicos = [{**x, "pontos": round(x["pontos"], 2), "max": round(x["max"], 2),
                "pct": round(x["pontos"] / x["max"], 2) if x["max"] else None} for x in por.values()]
    fracos = sorted((t for t in topicos if t["pct"] is not None and t["pct"] < 0.7), key=lambda t: t["pct"])[:4]
    p = painel(conv_id)
    return {"entregas": entregas, "topicos": topicos, "fracos": [t["topico"] for t in fracos],
            "lembrete": bool(mobile.devices()),
            "revisao": {"erros": sum(x["tipo"] == "erro" for x in p["itens"]), "cartoes": sum(x["tipo"] == "cartao" for x in p["itens"]),
                        "vencem": p["vencem"], "dominados": sum(x["dominada"] for x in p["itens"])},
            "plano": p["plano"]}


def _pesos(topicos: list[dict]) -> dict[str, float]:
    """Tópico fraco ganha mais dias: 1 + 2 × o que falta para acertar tudo (sem prova feita, conta como 50%)."""
    return {t["topico"]: 1 + 2 * (1 - (t["pct"] if t["pct"] is not None else 0.5)) for t in topicos}


def planejar(conv_id: int, data: str, minutos: int = 60) -> dict:
    """Um plano dia a dia, de hoje até a véspera da prova. Cada dia estuda um tópico (os fracos voltam mais
    vezes) e revisa os cartões e erros que vencem; a cada 7 dias e na véspera, um simulado."""
    from .estudos_prova import _sequencia
    try:
        alvo = date.fromisoformat(str(data)[:10])
    except ValueError:
        raise ToolError("Data da prova inválida (use AAAA-MM-DD).") from None
    hoje = _hoje()
    if alvo <= hoje:
        raise ToolError("A data da prova tem de ser depois de hoje.")
    dias = min((alvo - hoje).days, MAX_DIAS)
    minutos = max(15, min(E._int(minutos) or 60, 600))
    topicos = desempenho(conv_id)["topicos"]
    if not topicos:
        raise ToolError("Gere o resumo antes: o cronograma reparte os tópicos dele.")
    pesos = _pesos(topicos)
    estudo = round(minutos * 0.6)
    simulados = {d for d in range(dias) if (d + 1) % 7 == 0} | {dias - 1}
    seq = iter(_sequencia(list(pesos), pesos, dias - len(simulados)))
    plano = []
    for d in range(dias):
        dia = (hoje + timedelta(days=d)).isoformat()
        if d in simulados:
            tarefas = [{"tipo": "simulado", "texto": "Simulado: uma prova com todos os tópicos, com tempo", "topico": "",
                        "minutos": minutos}]
        else:
            t = next(seq)
            tarefas = [{"tipo": "estudar", "texto": f"Estudar: {t}", "topico": t, "minutos": estudo},
                       {"tipo": "revisar", "texto": "Revisar os cartões e erros do dia", "topico": "", "minutos": minutos - estudo}]
        plano.append({"dia": dia, "tarefas": [{"id": f"{dia}-{k}", **x, "feito": False} for k, x in enumerate(tarefas)]})
    novo = {"data": alvo.isoformat(), "minutos": minutos, "criado": datetime.now().isoformat(timespec="seconds"), "dias": plano}

    def f(rev):
        antes = {x["id"]: x["feito"] for d in (rev.get("plano") or {}).get("dias") or [] for x in d["tarefas"]}
        for d in novo["dias"]:   # refazer o plano não desmarca o que já foi feito hoje
            for x in d["tarefas"]:
                x["feito"] = antes.get(x["id"], False) and x["id"].startswith(hoje.isoformat())
        rev["plano"] = novo
    rev = _mudar(conv_id, f)
    mirror.write(conv_id)
    return rev["plano"]


def marcar(conv_id: int, tarefa_id: str, feito: bool) -> dict:
    def f(rev):
        for d in (rev.get("plano") or {}).get("dias") or []:
            for x in d["tarefas"]:
                if x["id"] == tarefa_id:
                    x["feito"] = bool(feito)
                    return
        raise ToolError("Tarefa do cronograma não encontrada.")
    return _mudar(conv_id, f)["plano"]


def apagar_plano(conv_id: int) -> dict:
    _mudar(conv_id, lambda rev: rev.update(plano=None))
    return {"ok": True}


# ------------------------------------------------------------------ lembrete no celular


def _lembrete(conv_id: int, hoje: date) -> str:
    """O texto do aviso de hoje, ou vazio se não há nada para fazer."""
    p = painel(conv_id)
    dia = next((d for d in (p["plano"] or {}).get("dias") or [] if d["dia"] == hoje.isoformat()), None)
    faltam = [x["texto"] for x in (dia or {}).get("tarefas") or [] if not x["feito"] and x["tipo"] != "revisar"]
    partes = faltam[:1] + ([f"{p['vencem']} revisão(ões) para hoje"] if p["vencem"] else [])
    return " · ".join(partes)


def lembrar(agora: datetime | None = None) -> int:
    """Um aviso por dia por estudo com cronograma, a partir das HORA_AVISO. Devolve quantos foram."""
    agora = agora or datetime.now()
    if agora.hour < HORA_AVISO:
        return 0
    hoje = agora.date()
    with db.session() as s:
        alvos = [(m.conversation_id, c.title) for m, c in s.execute(
            select(db.Message, db.Conversation).join(db.Conversation, db.Message.conversation_id == db.Conversation.id)
            .where(db.Conversation.kind == "estudos", db.Message.role == "user"))
            if _tipo(m) == "revisao" and m.meta["estudos"].get("plano") and m.meta["estudos"].get("avisado") != hoje.isoformat()
            and m.meta["estudos"]["plano"]["data"] > hoje.isoformat()]
    n = 0
    for conv_id, titulo in alvos:
        texto = _lembrete(conv_id, hoje)
        if texto:
            mobile.avisa(f"Estudos: {titulo}"[:80], texto, conv_id)
            n += 1
        _mudar(conv_id, lambda rev: rev.update(avisado=hoje.isoformat()))
    return n


async def vigia() -> None:
    """De hora em hora, no lifespan. O primeiro olhar espera o app subir."""
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(lembrar)
        except Exception:
            log.exception("estudos: lembrete do dia falhou")
        await asyncio.sleep(3600)


# ------------------------------------------------------------------ PDF do resumo

# A tela é escura; no papel, as mesmas cores do tema em claro.
PDF_CSS = (":root{color-scheme:light;--color-bg:#fff;--color-surface:#fff;--color-raised:#f2f2f2;--color-code:#f5f5f5;"
           "--color-line:#ddd;--color-line-strong:#c8c8c8;--color-fg:#111;--color-fg-2:#333;--color-muted:#555;"
           "--color-faint:#777}html,body{background:#fff!important;color:#111;margin:0}")


async def pdf(corpo: str, css: str, titulo: str = "") -> bytes:
    """O resumo como está na tela — fórmulas já desenhadas pelo KaTeX — em PDF, pelo Chromium headless que o
    documentos já usa. O CSS vem da própria tela, com as fontes embutidas (data:): no Docker quem serve os
    arquivos da interface é o nginx, que o backend não alcança. Sem JavaScript e sem rede: o HTML é só leitura."""
    from playwright.async_api import async_playwright
    estilo = (css or "").replace("</", "<\\/")   # nada fecha o <style> antes da hora
    doc = (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>{html.escape(titulo)}</title>'
           f'<style>{estilo}</style><style>{PDF_CSS}</style></head><body>{corpo}</body></html>')
    async with async_playwright() as pw:
        try:
            navegador = await pw.chromium.launch(headless=True)
        except Exception as e:
            raise ToolError("Não consegui abrir o Chromium para gerar o PDF. Ele vem com o Forja instalado; em "
                            "desenvolvimento, rode `python -m playwright install --only-shell chromium`. "
                            f"({e.__class__.__name__})") from e
        try:
            pagina = await navegador.new_page(java_script_enabled=False)
            await pagina.route("**/*", lambda r: r.abort())   # tudo já vem no documento
            await pagina.set_content(doc, wait_until="load")
            return await pagina.pdf(format="A4", print_background=True,
                                    margin={"top": "1.6cm", "bottom": "1.6cm", "left": "1.8cm", "right": "1.8cm"},
                                    display_header_footer=True, header_template="<div></div>",
                                    footer_template='<div style="width:100%;font-size:8pt;color:#777;text-align:center">'
                                                    '<span class="pageNumber"></span>/<span class="totalPages"></span></div>')
        finally:
            await navegador.close()


# ------------------------------------------------------------------ Claude via MCP


def bloco_pedido(p: dict) -> str:
    erros_ = erros(p["conv_id"])
    ja = [c["frente"] for c in cartoes(p["conv_id"])]
    return "\n".join([
        f"PEDIDO {p['pedido_id']} — {p['quantos']} flashcards do estudo {p['conv_id']}",
        "Leia o resumo com estudos_ler_resumo. Dos erros do aluno, faça cartões do conceito errado:",
        _bloco_erros(erros_) or "(nenhum erro ainda)",
        *(["Cartões que ele já tem (não repita):", *[f"- {f[:120]}" for f in ja[-80:]]] if ja else []),
        f"Quando terminar: estudos_salvar_flashcards(pedido_id={p['pedido_id']}, cartoes=[{{frente, verso, topico}}]).",
        "Frente: uma pergunta direta que puxe UMA ideia; verso: até duas frases ou a fórmula. LaTeX em $...$.",
    ])


def mcp_salvar(cartoes_: list | None, conv_id: int = 0, pedido_id: int = 0, modelo: str = "") -> str:
    nome = (modelo or "Claude (MCP)").strip()[:60]
    if pedido_id:
        with db.session() as s:
            m = s.get(db.Message, pedido_id)
            e = ((m.meta or {}).get("estudos") if m else None) or {}
            if e.get("tipo") != "flashcards" or m.status != "aguardando":
                return "ERRO: pedido de flashcards não encontrado ou já atendido."
            conv_id = m.conversation_id
    else:
        try:
            _mensagens(conv_id)
        except ToolError as err:
            return f"ERRO: {err}"
    from .estudos_prova import _norm
    from . import estudos_prova
    topicos = estudos_prova.topicos(conv_id)
    novos = _limpar_cartoes(cartoes_, {_norm(c["frente"]).strip(" ?.") for c in cartoes(conv_id)}, topicos)
    if not novos:
        return "ERRO: nenhum cartão válido (cada um precisa de frente e verso, sem repetir os que já existem)."
    if pedido_id:
        lista = [{"id": f"{pedido_id}-{i + 1}", **c, "origem": "resumo"} for i, c in enumerate(novos)]
        with db.session() as s:
            m = s.get(db.Message, pedido_id)
            e = m.meta["estudos"]
            m.meta = {**m.meta, "estudos": {**e, "cartoes": lista, "status": "pronto", "etapa": "pronto",
                                            "stats": {**e["stats"], "escritor": nome}}}
            m.status = "pronto"
            E._tocar(s, conv_id)
            s.commit()
    else:
        claude = {"provider": E.MOTOR_CLAUDE, "model": nome}
        msg = _save(conv_id, role="assistant", content="", status="pronto", meta={"estudos": {
            "tipo": "flashcards", "titulo": "Flashcards", "quantos": len(novos), "motor": "claude", "status": "pronto",
            "etapa": "pronto", "aviso": "", "partes": [], "cartoes": [], "stats": E.stats_novos(claude, claude)}})
        with db.session() as s:
            m = s.get(db.Message, msg.id)
            m.meta = {**m.meta, "estudos": {**m.meta["estudos"], "cartoes": [
                {"id": f"{msg.id}-{i + 1}", **c, "origem": "resumo"} for i, c in enumerate(novos)]}}
            s.commit()
    mirror.write(conv_id)
    return f"{len(novos)} cartão(ões) gravados no estudo {conv_id}: já aparecem na aba Revisão." + E._aviso_pedidos()


def mcp_desempenho(conv_id: int) -> str:
    """Notas, acerto por tópico, pontos fracos, revisão e cronograma, em texto para o Claude."""
    try:
        d = desempenho(conv_id)
    except ToolError as err:
        return f"ERRO: {err}"
    linhas = [f"Desempenho do estudo {conv_id}"]
    linhas += [f"- {e['titulo']} ({e['modo']}): nota {e['nota']:g} · {e['acertos']}/{e['n']} certas · "
               f"{datetime.fromisoformat(e['criado']).astimezone():%d/%m %H:%M}" if e["criado"] else ""
               for e in d["entregas"]] or ["(nenhuma entrega ainda)"]
    linhas.append("Por tópico (todas as entregas):")
    linhas += [f"- {t['topico']}: " + (f"{round(100 * t['pct'])}% ({t['pontos']:g}/{t['max']:g})" if t["pct"] is not None
                                        else "sem questão feita") for t in d["topicos"]]
    if d["fracos"]:
        linhas.append("Pontos fracos: " + "; ".join(d["fracos"]))
    r = d["revisao"]
    linhas.append(f"Revisão: {r['erros']} erro(s) e {r['cartoes']} cartão(ões); {r['vencem']} para hoje; {r['dominados']} dominado(s).")
    if d["plano"]:
        hoje = _hoje().isoformat()
        dia = next((x for x in d["plano"]["dias"] if x["dia"] == hoje), None)
        linhas.append(f"Cronograma até {d['plano']['data']} ({d['plano']['minutos']} min/dia). Hoje: "
                      + ("; ".join(f"{x['texto']}{' ✓' if x['feito'] else ''}" for x in dia["tarefas"]) if dia else "nada"))
    return "\n".join(linhas) + E._aviso_pedidos()

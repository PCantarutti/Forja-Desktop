"""Desempenho, caderno de erros, flashcards e tutor: tudo lido do corpus e das provas."""
from __future__ import annotations

import time
from datetime import date, timedelta

from ..tools import ToolError
from . import base, corpus
from .material import NIVEIS, limpar

FRACO = 0.6          # abaixo disto (com 3+ respostas) o tópico é fraco
INTERVALOS = [0, 1, 3, 7, 15, 30]   # dias até rever, por caixa de Leitner (1 a 5)

CARTOES_PROMPT = """Você faz FLASHCARDS de revisão a partir dos trechos numerados abaixo, no idioma deles.
Use SÓ o que os trechos dizem. Cada cartão: uma pergunta curta na frente, a resposta exata no verso.
Prefira definições, regras, exceções, prazos e números que caem em prova.
Responda SÓ com um array JSON de até {n} cartões:
[{{"frente": "...", "verso": "...", "fonte": "F3·12", "citacao": "frase copiada literalmente do trecho que prova o verso"}}]"""

TUTOR_PROMPT = """Você é o tutor de um aluno e responde a dúvida dele em Markdown, no idioma da pergunta.
Público: {nivel}.
Use os TRECHOS numerados abaixo, que são as fontes da matéria. Cada parágrafo tirado deles termina com o id
entre colchetes, como [F3·12]. Nunca invente um id.
Se os trechos não respondem à dúvida, comece com "Não encontrei isso nas fontes desta matéria." e, se puder
ajudar com o que você sabe, ponha isso dentro de um bloco
:::complemento
...
:::
Seja direto: explique, dê um exemplo se ajudar, e pare."""


# ------------------------------------------------------------------ desempenho e erros

def _respostas(est: dict):
    """(prova, questão, resultado) de todas as tentativas, da mais antiga para a mais nova."""
    for p in est["provas"]:
        for t in p["tentativas"]:
            for q in p["questoes"]:
                if q["id"] in t["resultado"]:
                    yield p, q, t["resultado"][q["id"]], t


def desempenho(est: dict) -> dict:
    por: dict[str, dict] = {}
    for _, q, r, _ in _respostas(est):
        d = por.setdefault(q["topico"], {"acertos": 0, "total": 0})
        d["total"] += 1
        d["acertos"] += 1 if r.get("certa") else 0
    nomes = {t["id"]: t["titulo"] for t in est["programa"]}
    # questão de simulado real que não casou com nenhum tópico do programa fica em "Fora do programa" (id "")
    topicos = [{"id": k, "titulo": nomes.get(k) or ("Fora do programa" if not k else k), **v,
                "acerto": round(v["acertos"] / v["total"], 2)} for k, v in por.items()]
    tot = sum(v["total"] for v in por.values())
    return {"topicos": topicos, "geral": round(sum(v["acertos"] for v in por.values()) / tot, 2) if tot else None,
            "respondidas": tot, "fracos": [t["id"] for t in topicos if t["id"] and t["total"] >= 3 and t["acerto"] < FRACO]}


def caderno(est: dict) -> list[dict]:
    """Questões cuja ÚLTIMA resposta foi errada (acertou depois, sai do caderno)."""
    ultima: dict[tuple, dict] = {}
    for p, q, r, t in _respostas(est):
        ultima[(p["id"], q["id"])] = {"prova": p["id"], "questao": q["id"], "topico": q["topico"],
                                      "enunciado": q["enunciado"], "alternativas": q.get("alternativas") or [],
                                      "resposta": r.get("resposta"), "correta": q.get("correta") or q.get("resposta_esperada"),
                                      "explicacao": q["explicacao"], "fonte": q["fonte"], "citacao": q["citacao"],
                                      "certa": r.get("certa"), "quando": t["quando"]}
    return sorted((x for x in ultima.values() if not x["certa"]), key=lambda x: -x["quando"])


# ------------------------------------------------------------------ flashcards

async def rodar_flashcards(run: dict) -> None:
    await base.preparar_modelo(run)
    conv_id = run["conv_id"]
    est = base.ler(conv_id)
    alvo = set(run["alvo"].get("topicos") or [])
    topicos = [t for t in est["programa"] if not alvo or t["id"] in alvo]
    if not topicos or not corpus.uteis(est):
        raise ToolError("Monte o programa e o corpus antes dos flashcards.")
    por_topico = max(3, min(15, int(run["alvo"].get("por_topico") or 6)))
    ja = {corpus.norm(c["frente"]) for c in est["flashcards"]}
    run["total"] = len(topicos)
    novos = descartados = 0
    for i, t in enumerate(topicos):
        if base.parar(run):
            break
        run.update(feitos=i, fase=f"flashcards: {t['titulo'][:60]}")
        trechos = corpus.buscar(conv_id, est, " ".join([t["titulo"], *t["subtopicos"]]), t["id"], k=5, teto_chars=9000)
        if not trechos:
            continue
        lista = base.json_de(await base.perguntar(run, CARTOES_PROMPT.format(n=por_topico), corpus.bloco(trechos)), list) or []
        validos = {x["id"]: x["texto"] for x in trechos}
        cartoes = []
        for c in lista[:por_topico]:
            if not isinstance(c, dict):
                continue
            fonte = next((f"F{a}{corpus.SEP}{b}" for a, b in corpus.ID_TRECHO.findall(str(c.get("fonte") or ""))), "")
            frente, verso = str(c.get("frente") or "").strip(), str(c.get("verso") or "").strip()
            if not (frente and verso and fonte in validos and corpus.achar_citacao(str(c.get("citacao") or ""), validos[fonte])):
                descartados += 1
                continue
            if corpus.norm(frente) in ja:
                continue
            ja.add(corpus.norm(frente))
            cartoes.append({"frente": frente[:400], "verso": verso[:800], "fonte": fonte,
                            "citacao": str(c["citacao"]).strip(), "topico": t["id"]})
        with base.mudar(conv_id) as e2:
            for c in cartoes:
                e2["flashcards"].append({**c, "id": f"c{base.proximo(e2, 'cartao')}", "caixa": 1,
                                         "proxima": date.today().isoformat(), "criado": time.time()})
        novos += len(cartoes)
    run["feitos"] = len(topicos)
    base.registrar(run, f"{novos} cartões novos, {descartados} descartados sem fonte.")


def rever_cartao(conv_id: int, cid: str, acertou: bool) -> dict:
    with base.mudar(conv_id) as est:
        c = next((x for x in est["flashcards"] if x["id"] == cid), None)
        if not c:
            raise ToolError("Cartão não encontrado.")
        c["caixa"] = min(5, c["caixa"] + 1) if acertou else 1
        c["proxima"] = (date.today() + timedelta(days=INTERVALOS[c["caixa"]])).isoformat()
        return dict(c)


def apagar_cartao(conv_id: int, cid: str) -> None:
    with base.mudar(conv_id) as est:
        est["flashcards"] = [c for c in est["flashcards"] if c["id"] != cid]


def csv_anki(est: dict) -> str:
    import csv
    import io
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    for c in est["flashcards"]:
        w.writerow([c["frente"], f"{c['verso']} [{c['fonte']}]"])
    return out.getvalue()


# ------------------------------------------------------------------ tutor

async def tutor(conv_id: int, spec: dict, pergunta: str, topico: str = "") -> dict:
    pergunta = (pergunta or "").strip()
    if not pergunta:
        raise ToolError("Escreva a dúvida.")
    est = base.ler(conv_id)
    run = base.novo_run(-conv_id, "duvida", spec)   # chave negativa: não disputa com a tarefa da matéria
    await base.preparar_modelo(run)
    trechos = corpus.buscar(conv_id, est, pergunta, topico or None, k=8, teto_chars=await base.orcamento(run))
    nivel = NIVEIS.get(est.get("nivel") or "concurso", NIVEIS["concurso"])
    user = f"Dúvida: {pergunta}\n\nTRECHOS:\n{corpus.bloco(trechos) if trechos else '(nenhum trecho parecido)'}"
    resposta = await base.perguntar(run, TUTOR_PROMPT.format(nivel=nivel), user)
    md, stats = limpar(resposta, lambda i: bool(corpus.trecho(conv_id, i)))
    d = {"pergunta": pergunta, "resposta": md, "quando": time.time(), "topico": topico,
         "sem_fonte": not stats["citados"]}
    with base.mudar(conv_id) as e2:
        e2["duvidas"] = (e2["duvidas"] + [d])[-200:]
    return d

"""Prova ancorada: cada questão nasce de UM trecho do corpus e só entra se passar nos portões do código.

Portões, nesta ordem:
1. estrutura (alternativas distintas, gabarito válido);
2. a citação literal está no trecho (corpus.achar_citacao);
3. um verificador responde às cegas VENDO SÓ O TRECHO e tem de chegar no mesmo gabarito.
O complemento do material nunca entra: a prova lê o corpus, não a apostila.
"""
from __future__ import annotations

import math
import random
import re
import time

from ..tools import ToolError
from . import base, corpus

TIPOS = ("multipla", "certo_errado", "discursiva")
DIFICULDADES = ("facil", "media", "dificil")
MAX_QUESTOES = 60
TENTATIVAS_POR_QUESTAO = 3   # trechos tentados antes de desistir da vaga
USOS_POR_TRECHO = 3          # corpus pequeno (uma lei curta): o mesmo trecho rende questões sobre pontos diferentes
LETRAS = "ABCDE"

ESTILO = {
    "multipla": """- Múltipla escolha com 5 alternativas (A a E) e uma só correta. As erradas são plausíveis, mas o trecho
  permite descartá-las.
Responda SÓ com JSON: {"enunciado": "...", "alternativas": ["...", "...", "...", "...", "..."], "correta": "A", "explicacao": "...", "citacao": "..."}""",
    "certo_errado": """- Item de CERTO ou ERRADO (estilo Cebraspe): uma afirmação que o aluno julga. Faça itens errados também
  (troque um detalhe que o trecho contradiz), não só certos.
Responda SÓ com JSON: {"enunciado": "a afirmação", "correta": "C", "explicacao": "...", "citacao": "..."}
("correta": "C" para certo, "E" para errado)""",
    "discursiva": """- Questão DISCURSIVA curta (resposta de 3 a 8 linhas).
Responda SÓ com JSON: {"enunciado": "...", "resposta_esperada": "o que uma resposta completa precisa dizer", "explicacao": "...", "citacao": "..."}""",
}

QUESTAO_PROMPT = """Você é examinador de banca e escreve UMA questão de prova baseada SOMENTE no trecho abaixo.
Público: {nivel}. Dificuldade: {dificuldade}.
- A resposta certa tem de estar escrita no trecho. Não use nada que o trecho não diga, nem o que você sabe do assunto.
- Não escreva "segundo o texto" nem "de acordo com o trecho": é questão de prova, não de interpretação de texto.
- Cobre o conteúdo importante do trecho (conceito, regra, exceção, prazo), não um detalhe irrelevante.
- "citacao": copie LITERALMENTE do trecho a frase (8 a 40 palavras) que prova a resposta. Copie, não resuma.
- "explicacao": por que a resposta é essa, com base no trecho.
- Nomes, siglas (e o que elas significam), números, prazos e datas: só como estão no trecho ou no título da fonte.
  Sigla que o trecho não expande fica como sigla.
{estilo}"""

VERIFICAR_PROMPT = {
    "multipla": """Responda a questão usando SOMENTE o trecho abaixo, como se não soubesse nada além dele.
Se o trecho não permite ter certeza de qual alternativa é a certa, responda NAO_DA.
Responda só com a letra (A, B, C, D ou E) ou NAO_DA.""",
    "certo_errado": """Julgue a afirmação usando SOMENTE o trecho abaixo, como se não soubesse nada além dele.
Responda C se o trecho confirma a afirmação, E se o trecho a contradiz, ou NAO_DA se o trecho não permite julgar.
Responda só com C, E ou NAO_DA.""",
    "discursiva": """Você confere uma questão discursiva. Use SOMENTE o trecho abaixo.
O trecho contém tudo o que a resposta esperada diz? Responda só com SIM ou NAO.""",
}

FIEL_PROMPT = """Você confere a fidelidade de uma questão de prova ao trecho de onde ela saiu.
Olhe cada fato que o ENUNCIADO e a RESPOSTA CORRETA afirmam: nomes, siglas e o que significam, números, prazos,
datas, regras. Todos estão escritos no trecho (ou no título da fonte)? Paráfrase vale; fato que não está, não.
Responda com UMA palavra: SIM ou NAO."""

CORRIGIR_PROMPT = """Você corrige a resposta de um aluno a uma questão discursiva. Compare com a resposta esperada e com
o trecho-fonte; o que não está no trecho não conta a favor nem contra.
Responda SÓ com JSON: {"nota": 0 a 10, "comentario": "o que faltou ou o que está errado, em 1 a 3 frases"}"""


# ------------------------------------------------------------------ escolha dos trechos

def _repartir(n: int, pesos: dict[str, int]) -> dict[str, int]:
    """n questões pelos tópicos, proporcional ao peso (maior resto)."""
    total = sum(pesos.values()) or 1
    cota = {k: n * p / total for k, p in pesos.items()}
    out = {k: int(v) for k, v in cota.items()}
    for k in sorted(cota, key=lambda k: -(cota[k] - out[k]))[:n - sum(out.values())]:
        out[k] += 1
    return out


SIGLA = re.compile(r"((?:[A-Za-zÀ-ú]+\s+){1,6}?)\(([A-Z]{2,8})\)")


def siglas_inventadas(texto: str, fonte: str) -> str:
    """"Nome Por Extenso (SIGLA)" cuja sigla a fonte usa mas com outro nome por extenso: devolve a sigla
    (modelo que expande sigla de cabeça inventa: "Imposto sobre Luminárias de Luz (ILM)"). '' quando tudo bate."""
    f = f" {corpus.norm(fonte)} "
    for m in SIGLA.finditer(texto):
        palavras = [w for w in corpus.norm(m.group(1)).split() if len(w) > 2][-2:]   # as 2 últimas palavras fortes
        if palavras and f" {m.group(2).lower()} " in f and not all(f" {w} " in f for w in palavras):
            return m.group(2)
    return ""


def _topico_de(q: dict, topicos: list[dict], padrao: str) -> str:
    """O tópico que a questão de fato cobra (o trecho de uma vaga pode falar de outro tópico): o que mais
    combina, por BM25, com o enunciado e a citação. É o que vale no desempenho por tópico."""
    notas = corpus._bm25(corpus.palavras(f"{q['enunciado']} {q['citacao']}"),
                         [corpus.palavras(" ".join([t["titulo"], *t["subtopicos"]])) for t in topicos])
    melhor = max(range(len(topicos)), key=lambda i: notas[i], default=None)
    return topicos[melhor]["id"] if melhor is not None and notas[melhor] > 0 else padrao


def _ja_usados(est: dict) -> set:
    return {q["fonte"] for p in est["provas"] for q in p["questoes"]}


def _candidatos(conv_id: int, est: dict, t: dict, quantos: int) -> list[dict]:
    """Os trechos do tópico que mais falam dele, com conteúdo de verdade; os ainda não usados em prova primeiro."""
    consulta = " ".join([t["titulo"], *t["subtopicos"]])
    achados = [x for x in corpus.buscar(conv_id, est, consulta, t["id"], k=max(12, quantos * 4))
               if len(x["texto"]) >= 200 and len(corpus.palavras(x["texto"])) >= 20]
    usados = _ja_usados(est)
    novos, velhos = [x for x in achados if x["id"] not in usados], [x for x in achados if x["id"] in usados]
    topo = max(3, quantos * 2)   # sorte só entre os mais relevantes: duas provas do tópico não saem iguais
    novos = random.sample(novos[:topo], len(novos[:topo])) + novos[topo:]
    return novos + velhos


# ------------------------------------------------------------------ portões

ROTULO = re.compile(r"^\s*\(?[A-Ea-e]\s*[).:\-]\s+")


def _letra(texto: str, validas: str) -> str:
    t = corpus.norm(texto).upper().split()
    if not t:
        return ""
    if any(w.startswith("NAO") for w in t[:3]):
        return "NAO_DA"
    # resposta curta ("B", "Letra B", "Resposta: C"): a letra solta; em frase longa só vale a 1ª palavra
    # (o "e" conjunção viraria o E de errado)
    for w in (t[:3] if len(t) <= 3 else t[:1]):
        if len(w) == 1 and w in validas:
            return w
    return ""


REMETE = re.compile(r"(?i)\b(segundo|conforme|de acordo com|com base n|nos termos d)[oa]? (o |a )?(trecho|texto|fragmento|excerto|passagem|gloss[aá]rio|contexto)\b|(texto|trecho|gloss[aá]rio) apresentad|(indicad|mencionad|apresentad|descrit)[oa]s? n[oa] (texto|trecho|contexto)")


def estrutura(q: dict, tipo: str) -> str:
    """'' quando a questão está bem formada; senão o motivo."""
    if len(str(q.get("enunciado") or "").strip()) < 15:
        return "enunciado vazio"
    if REMETE.search(str(q["enunciado"])):
        return "enunciado remete ao trecho"
    if tipo == "multipla":
        alts = [ROTULO.sub("", str(a)).strip() for a in (q.get("alternativas") or [])]
        if len(alts) != 5 or any(not a for a in alts) or len({corpus.norm(a) for a in alts}) < 5:
            return "alternativas inválidas"
        q["alternativas"] = alts
        if str(q.get("correta") or "").strip().upper()[:1] not in LETRAS:
            return "gabarito inválido"
        q["correta"] = str(q["correta"]).strip().upper()[:1]
    elif tipo == "certo_errado":
        c = str(q.get("correta") or "").strip().upper()[:1]
        if c not in "CE":
            return "gabarito inválido"
        q["correta"], q["alternativas"] = c, ["Certo", "Errado"]
    elif len(str(q.get("resposta_esperada") or "").strip()) < 15:
        return "sem resposta esperada"
    return ""


def _pergunta_verificador(q: dict, tipo: str, texto: str) -> str:
    if tipo == "multipla":
        corpo = q["enunciado"] + "\n" + "\n".join(f"{LETRAS[i]}) {a}" for i, a in enumerate(q["alternativas"]))
    elif tipo == "certo_errado":
        corpo = f"Afirmação: {q['enunciado']}"
    else:
        corpo = f"Pergunta: {q['enunciado']}\nResposta esperada: {q['resposta_esperada']}"
    return f"TRECHO:\n{texto}\n\n{corpo}"


async def _uma(run: dict, tipo: str, trecho: dict, nivel: str, dificuldade: str,
               ja: list[str] | None = None) -> tuple[dict | None, str]:
    """Gera e confere uma questão a partir de um trecho. (questão, '') ou (None, motivo).
    `ja`: enunciados que já saíram deste trecho; a nova cobra outra informação."""
    prompt = QUESTAO_PROMPT.format(nivel=nivel, dificuldade=dificuldade, estilo=ESTILO[tipo])
    ja = [e for e in (ja or []) if e]
    outras = ("\n\nJá existem estas questões sobre este trecho; cobre OUTRA informação dele:\n" +
              "\n".join(f"- {e}" for e in ja)) if ja else ""
    # o título e o começo da fonte carregam o nome da lei e o que a sigla quer dizer: o trecho do meio não tem
    inteiro = corpus.texto_da_fonte(run["conv_id"], trecho["fonte"])
    cabeca = inteiro[:300] if not inteiro.startswith(trecho["texto"][:100]) else ""
    fonte = (f"(fonte: {trecho['titulo']})\n" + (f"[começo da fonte: {cabeca}…]\n" if cabeca else "") + trecho["texto"])
    q = base.json_de(await base.perguntar(run, prompt, f"TRECHO [{trecho['id']}] {fonte}{outras}")) or {}
    if motivo := estrutura(q, tipo):
        return None, motivo
    certa_txt = (q["alternativas"][LETRAS.index(q["correta"])] if tipo == "multipla" else
                 q.get("resposta_esperada", "") if tipo == "discursiva" else q["enunciado"] if q["correta"] == "C" else "")
    if sigla := siglas_inventadas(f"{q['enunciado']} {certa_txt}", inteiro or trecho["texto"]):
        return None, f"sigla com nome que a fonte não usa ({sigla})"
    if not corpus.achar_citacao(str(q.get("citacao") or ""), trecho["texto"]):
        return None, "citação não está no trecho"
    veredito = await base.perguntar(run, VERIFICAR_PROMPT[tipo], _pergunta_verificador(q, tipo, fonte))
    if tipo == "discursiva":
        if not re.search(r"\bsim\b", corpus.norm(veredito)):
            return None, "trecho não sustenta a resposta"
    else:
        r = _letra(veredito, LETRAS if tipo == "multipla" else "CE")
        if r == "NAO_DA":
            return None, "trecho não basta para responder"
        if r != q["correta"]:
            return None, "verificador chegou em outra resposta"
        # item ERRADO de certo/errado inventa um detalhe de propósito: a fidelidade só vale para o que é dado como certo
        if tipo == "multipla" or q["correta"] == "C":
            certa = (q["alternativas"][LETRAS.index(q["correta"])] if tipo == "multipla" else "(a própria afirmação)")
            fiel = await base.perguntar(run, FIEL_PROMPT, f"TRECHO {fonte}\n\nENUNCIADO: {q['enunciado']}\n"
                                                         f"RESPOSTA CORRETA: {certa}")
            if not re.search(r"\bsim\b", corpus.norm(fiel)):
                return None, "enunciado afirma o que o trecho não diz"
    return {"tipo": tipo, "enunciado": str(q["enunciado"]).strip(), "alternativas": q.get("alternativas") or [],
            "correta": q.get("correta") or "", "resposta_esperada": str(q.get("resposta_esperada") or "").strip(),
            "explicacao": str(q.get("explicacao") or "").strip(), "citacao": str(q["citacao"]).strip(),
            "fonte": trecho["id"], "fonte_titulo": trecho["titulo"]}, ""


async def rodar(run: dict) -> None:
    await base.preparar_modelo(run)
    conv_id, cfg = run["conv_id"], run["alvo"]
    est = base.ler(conv_id)
    n = max(1, min(MAX_QUESTOES, int(cfg.get("n") or 10)))
    tipo = cfg.get("tipo") if cfg.get("tipo") in TIPOS else "multipla"
    dificuldade = cfg.get("dificuldade") if cfg.get("dificuldade") in DIFICULDADES else "media"
    alvo = set(cfg.get("topicos") or [])
    topicos = [t for t in est["programa"] if not alvo or t["id"] in alvo]
    if not topicos:
        raise ToolError("Monte o programa antes de gerar prova.")
    if not corpus.uteis(est):
        raise ToolError("O corpus está vazio: pesquise ou mande arquivos antes de gerar prova.")
    from .material import NIVEIS
    nivel = NIVEIS.get(est.get("nivel") or "concurso", NIVEIS["concurso"])
    cotas = _repartir(n, {t["id"]: t["peso"] for t in topicos})
    questoes, motivos = [], {}
    run["total"] = n
    usos: dict[str, list[str]] = {}   # trecho -> enunciados que já saíram dele (nesta prova)
    filas = {t["id"]: _candidatos(conv_id, est, t, cotas[t["id"]]) for t in topicos}
    distintos = len({x["id"] for f in filas.values() for x in f}) or 1
    # corpus pequeno (uma lei curta): o mesmo trecho rende mais questões, cada uma sobre outro ponto
    limite = max(USOS_POR_TRECHO, math.ceil(1.5 * n / distintos))
    falta = 0   # vaga que um tópico não preencheu passa para o próximo (e, no fim, volta ao começo)
    for passada in range(2):
        for t in topicos:
            quer = (cotas[t["id"]] if passada == 0 else 0) + falta
            if quer <= 0 or base.parar(run):
                continue
            fila = [x for x in filas[t["id"]] if len(usos.get(x["id"], [])) < limite]
            feitas = falhas = 0
            while feitas < quer and fila and falhas < quer * TENTATIVAS_POR_QUESTAO:
                if base.parar(run):
                    break
                tr = fila.pop(0)
                ja = usos.setdefault(tr["id"], [])
                run["fase"] = f"questão {len(questoes) + 1} de {n}: {t['titulo'][:50]}"
                try:
                    q, motivo = await _uma(run, tipo, tr, nivel, dificuldade, ja)
                except Exception as e:
                    q, motivo = None, f"falha do modelo ({e.__class__.__name__})"
                ja.append(q["enunciado"] if q else "")   # tentativa que falhou também gasta um uso
                if len(ja) < limite:
                    fila.append(tr)   # volta para o fim: os outros trechos vêm antes de repetir este
                if not q:
                    falhas += 1
                    motivos[motivo] = motivos.get(motivo, 0) + 1
                    base.registrar(run, f"✗ [{tr['id']}] {motivo}")
                    continue
                q.update(id=f"q{len(questoes) + 1}", topico=_topico_de(q, topicos, t["id"]))
                questoes.append(q)
                feitas += 1
                run["feitos"] = len(questoes)
                base.registrar(run, f"✓ [{tr['id']}] {q['enunciado'][:70]}")
            falta = quer - feitas
            if falta and passada == 1:
                base.registrar(run, f"{t['titulo'][:50]}: as fontes não renderam mais questões")
        if not falta:
            break
    if not questoes:
        raise ToolError("Nenhuma questão passou na conferência com as fontes. Pesquise mais o tópico ou mande material.")
    descartadas = sum(motivos.values())
    with base.mudar(conv_id) as e2:
        pid = f"P{base.proximo(e2, 'prova')}"
        e2["provas"].append({"id": pid, "criada": time.time(), "config": {"n": n, "tipo": tipo, "dificuldade": dificuldade,
                             "topicos": sorted(alvo)}, "questoes": questoes, "descartadas": descartadas,
                             "motivos": motivos, "tentativas": []})
    run["alvo"]["prova"] = pid
    if len(questoes) < n:
        run["aviso"] = (f"Saíram {len(questoes)} de {n} questões: as fontes não sustentaram o resto "
                        f"({descartadas} descartadas na conferência).")


# ------------------------------------------------------------------ entrega e correção

def para_tela(p: dict) -> dict:
    """Prova nunca entregue sai sem gabarito, explicação nem citação."""
    if p["tentativas"]:
        return p
    esconder = ("correta", "explicacao", "citacao", "resposta_esperada", "fonte", "fonte_titulo")
    return {**p, "questoes": [{k: v for k, v in q.items() if k not in esconder} for q in p["questoes"]]}


async def corrigir(run: dict) -> None:
    conv_id, pid = run["conv_id"], run["alvo"]["prova"]
    respostas = run["alvo"].get("respostas") or {}
    p = next((x for x in base.ler(conv_id)["provas"] if x["id"] == pid), None)
    if not p:
        raise ToolError("Prova não encontrada.")
    resultado, acertos = {}, 0.0
    run["total"] = len(p["questoes"])
    for i, q in enumerate(p["questoes"]):
        r = str(respostas.get(q["id"]) or "").strip()
        run["feitos"] = i
        if q["tipo"] != "discursiva":
            certa = r.upper()[:1] == q["correta"]
            resultado[q["id"]] = {"resposta": r.upper()[:1], "certa": certa}
            acertos += certa
            continue
        if not r:
            resultado[q["id"]] = {"resposta": "", "certa": False, "nota": 0, "comentario": "Sem resposta."}
            continue
        if base.parar(run):
            break
        if not run.get("_modelo"):
            await base.preparar_modelo(run)
            run["_modelo"] = True
        run["fase"] = f"corrigindo a discursiva {q['id']}"
        txt = corpus.trecho(conv_id, q["fonte"])
        obj = base.json_de(await base.perguntar(
            run, CORRIGIR_PROMPT, f"TRECHO-FONTE:\n{txt}\n\nPergunta: {q['enunciado']}\n"
                                  f"Resposta esperada: {q['resposta_esperada']}\n\nResposta do aluno:\n{r}")) or {}
        try:
            nota = max(0.0, min(10.0, float(obj.get("nota"))))
        except (TypeError, ValueError):
            nota = 0.0
        resultado[q["id"]] = {"resposta": r, "certa": nota >= 6, "nota": nota,
                              "comentario": str(obj.get("comentario") or "").strip()[:600]}
        acertos += nota / 10
    with base.mudar(conv_id) as est:
        for x in est["provas"]:
            if x["id"] == pid:
                x["tentativas"].append({"quando": time.time(), "segundos": int(run["alvo"].get("segundos") or 0),
                                        "resultado": resultado, "acertos": round(acertos, 1),
                                        "total": len(x["questoes"])})
    run["feitos"] = len(p["questoes"])


def apagar(conv_id: int, pid: str) -> None:
    with base.mudar(conv_id) as est:
        est["provas"] = [p for p in est["provas"] if p["id"] != pid]

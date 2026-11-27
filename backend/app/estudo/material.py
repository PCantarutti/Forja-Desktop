"""Material por tópico: escrito dos trechos do corpus, com citação [F3·12] conferida pelo código."""
from __future__ import annotations

import re
import time

from ..tools import ToolError
from . import base, corpus

NIVEIS = {
    "concurso": "aluno de concurso público: definições exatas, letra da lei, pegadinhas de banca",
    "vestibular": "aluno de vestibular/ENEM: o que cai e como cai, com exemplos",
    "faculdade": "aluno de faculdade: profundidade conceitual, nuances e exemplos",
    "iniciante": "iniciante: parte do zero e explica cada termo",
}

MATERIAL_PROMPT = """Você é um professor escrevendo a apostila de um aluno, em Markdown, no idioma do tópico.
Público: {nivel}.
Você recebe TRECHOS numerados de fontes, cada um com um id como [F3·12]. Escreva a parte pedida do tópico.

REGRAS DE FONTE (obrigatórias):
- Todo parágrafo, item de lista e tabela com informação tirada dos trechos termina com o id do trecho entre
  colchetes, assim: [F3·12]. Pode citar mais de um: [F3·12][F1·4].
- Só cite um id se aquele trecho diz de fato o que você escreveu. Nunca invente um id.
- Números, prazos, artigos de lei, datas e nomes exatos: só os que estão nos trechos.
- Se precisar acrescentar algo do seu conhecimento (uma analogia, um exemplo, um contexto que falta), ponha
  DENTRO de um bloco assim, sem citação:
:::complemento
o texto acrescentado
:::
  Use pouco: a apostila é feita das fontes.

ESTRUTURA (uma seção ### por parte, nesta ordem):
{estrutura}
Em cada parte: explicação completa em parágrafos, conceitos-chave em **negrito**, listas e tabelas quando
ajudam, exemplos concretos. No fim, quando os trechos permitirem:
#### Como cai na prova
#### Pegadinhas
Tamanho: o que os trechos sustentam ({palavras} palavras), sem encher nem repetir. Nada fora disso."""

JUNTAR_ATE = 8_000   # trechos do tópico inteiro até aqui: uma chamada só

REVISAO_PROMPT = """Você fecha um tópico de apostila com uma revisão, em Markdown, no idioma do texto.
Use SÓ o que está no texto do tópico abaixo e mantenha as citações [F3·12] das frases que você aproveitar.
Escreva exatamente:
#### Quadro-resumo
Uma tabela | Conceito | O que lembrar | com 5 a 10 linhas; cada linha termina com a citação que a sustenta.
Nada além disso."""

COMPLEMENTO = re.compile(r"(?ms)^:::complemento[ \t]*\n(.*?)^:::[ \t]*$")
CITACAO = re.compile(r"\[\s*(F\d+\s*[·.:\-–]\s*\d+(?:\s*[,;]\s*F\d+\s*[·.:\-–]\s*\d+)*)\s*\]")


def _ids(grupo: str) -> list[str]:
    return [f"F{a}{corpus.SEP}{b}" for a, b in corpus.ID_TRECHO.findall(grupo)]


def limpar(md: str, existe) -> tuple[str, dict]:
    """Pós-processo: citação de trecho que não existe sai; bloco com informação e sem citação válida vira
    complemento. `existe(id) -> bool`. Devolve (markdown, {blocos, citados, complemento, removidas})."""
    stats = {"blocos": 0, "citados": 0, "complemento": 0, "removidas": 0}
    saida: list[str] = []
    pos = 0
    partes: list[tuple[bool, str]] = []   # (é_complemento, texto)
    for m in COMPLEMENTO.finditer(md):
        partes.append((False, md[pos:m.start()]))
        partes.append((True, m.group(1).strip()))
        pos = m.end()
    partes.append((False, md[pos:]))
    for compl, texto in partes:
        if compl:
            if texto:
                saida.append(f":::complemento\n{CITACAO.sub('', texto).strip()}\n:::")
                stats["complemento"] += 1
            continue
        for bloco in re.split(r"\n\s*\n", texto):
            bloco = re.sub(r"(?m)^:::.*$\n?", "", bloco).strip()   # bloco de complemento que o modelo não fechou
            if not bloco:
                continue

            def trocar(m):
                bons = [i for i in _ids(m.group(1)) if existe(i)]
                stats["removidas"] += len(_ids(m.group(1))) - len(bons)
                return "".join(f"[{i}]" for i in bons)
            bloco = CITACAO.sub(trocar, bloco)
            so_titulo = all(l.lstrip().startswith("#") or not l.strip() for l in bloco.splitlines())
            if so_titulo or len(re.sub(r"[#*|\-\s:]", "", bloco)) < 40 or bloco.startswith("Não encontrei"):
                saida.append(bloco)
                continue
            stats["blocos"] += 1
            if CITACAO.search(bloco):
                stats["citados"] += 1
                saida.append(bloco)
            else:
                stats["complemento"] += 1
                saida.append(f":::complemento\n{bloco}\n:::")
    return "\n\n".join(saida), stats


async def _escrever_topico(run: dict, t: dict, teto: int) -> dict:
    conv_id = run["conv_id"]
    est = base.ler(conv_id)
    unidades = t["subtopicos"] or [t["titulo"]]
    nivel = NIVEIS.get(est.get("nivel") or "concurso", NIVEIS["concurso"])
    achados = {u: corpus.buscar(conv_id, est, f"{t['titulo']} {u}" if u != t["titulo"] else t["titulo"], t["id"],
                                k=14, teto_chars=teto) for u in unidades}
    uniao = {x["id"]: x for v in achados.values() for x in v}
    if len(unidades) > 1 and sum(len(x["texto"]) for x in uniao.values()) <= JUNTAR_ATE:
        # pouca fonte para o tópico: tudo numa chamada, um ### por subtópico (senão cada um repetiria os mesmos trechos)
        lotes = [(t["titulo"], unidades, list(uniao.values()))]
    else:
        lotes = [(u, [u], achados[u]) for u in unidades]
    partes, usados = [], set()
    for nome, subs, trechos in lotes:
        if base.parar(run):
            break
        run["fase"] = f"escrevendo: {t['titulo'][:50]}" + (f" › {nome[:40]}" if nome != t["titulo"] else "")
        if not trechos:
            partes.append(f"### {nome}\n\n:::complemento\nAs fontes desta matéria não trazem nada sobre isto. "
                          "Pesquise de novo o tópico ou mande um arquivo.\n:::")
            continue
        usados |= {x["id"] for x in trechos}
        # o tamanho acompanha a fonte: pedir 1500 palavras de 400 palavras de lei é pedir para inventar
        alvo = max(250, min(1500, int(sum(len(x["texto"]) for x in trechos) / 6 * 1.3)))
        palavras = f"{alvo // 2} a {alvo}"
        user = (f"Tópico: {t['titulo']}\nParte(s) a escrever: {'; '.join(subs)}\n" +
                (f"Matéria: {t['materia']}\n" if t["materia"] else "") +
                f"\nTRECHOS:\n{corpus.bloco(trechos)}")
        prompt = MATERIAL_PROMPT.format(nivel=nivel, estrutura="\n".join(f"### {u}" for u in subs), palavras=palavras)
        texto = await base.perguntar(run, prompt, user)
        if not texto.lstrip().startswith("###"):
            texto = f"### {subs[0]}\n\n{texto}"
        partes.append(texto)
    corpo = "\n\n".join(partes)
    if corpo and not base.parar(run):
        run["fase"] = f"revisão: {t['titulo'][:60]}"
        try:
            corpo += "\n\n" + await base.perguntar(run, REVISAO_PROMPT, corpo[:teto])
        except Exception as e:
            base.registrar(run, f"Quadro-resumo de {t['titulo'][:40]} falhou ({e.__class__.__name__}).")
    md, stats = limpar(corpo, lambda i: bool(corpus.trecho(conv_id, i)))
    return {"md": md, "stats": stats, "trechos": sorted(usados), "gerado": time.time()}


async def rodar(run: dict) -> None:
    await base.preparar_modelo(run)
    conv_id = run["conv_id"]
    est = base.ler(conv_id)
    alvo = set(run["alvo"].get("topicos") or [])
    topicos = [t for t in est["programa"] if not alvo or t["id"] in alvo]
    if not topicos:
        raise ToolError("Monte o programa antes de escrever o material.")
    if not corpus.uteis(est):
        raise ToolError("O corpus está vazio: pesquise ou mande arquivos antes de escrever o material.")
    teto = await base.orcamento(run)
    run["total"] = len(topicos)
    for i, t in enumerate(topicos):
        if base.parar(run):
            break
        run["feitos"] = i
        sec = await _escrever_topico(run, t, teto)
        with base.mudar(conv_id) as e2:
            e2["material"][t["id"]] = sec
        s = sec["stats"]
        base.registrar(run, f"✓ {t['titulo'][:60]}: {s['citados']}/{s['blocos']} blocos com fonte, "
                            f"{s['complemento']} complemento(s)")
    run["feitos"] = len(topicos)

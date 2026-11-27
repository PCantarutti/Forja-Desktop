"""Recorte de prova real e gabarito (portados do v1, sem modelo) e a busca de PDF."""
import pytest

from app.estudo import edital as ED
from app.estudo import simulado_recorte as S

G = S   # os blocos de gabarito moram no mesmo módulo do recorte


def _questao(n: int, certa: str, assunto: str) -> str:
    alts = "\n".join(f"{l}) opção {l.lower()} da {n}" + (f"\ncontinua na linha de baixo, {assunto}" if l == "B" else "") for l in "ABCDE")
    return (f"QUESTÃO {n}\nEnunciado da questão {n} sobre {assunto}, com texto o bastante para contar.\n{alts}\n"
            f"Resolução\nA conta da {n} mostra que é a {certa}.\nfimResposta: {certa}\n")


CAB = "SIMULADO ENEM 2020 – RESOLUÇÕES – Página {p}"


def _simulado(n=8) -> str:
    partes = []
    for i in range(n):
        if i % 2 == 0:
            partes.append(f"--- página {i // 2 + 1} ---\n{CAB.format(p=i // 2 + 1)}")
        partes.append(_questao(91 + i, "ABCDE"[i % 5], ["cinemática", "óptica", "estequiometria", "genética"][i % 4]))
    return "\n".join(partes)


def test_recorte_tira_cabecalho_resolucao_e_pega_o_gabarito_do_pdf():
    qs = S.questoes_reais(_simulado())
    assert [q["numero"] for q in qs] == list(range(91, 99))
    q = qs[0]
    assert q["oficial"] == "A" and q["pagina"] == 1
    assert q["alternativas"][1] == "opção b da 91 continua na linha de baixo, cinemática"
    assert "Resolução" not in q["enunciado"] and "RESOLUÇÕES" not in q["enunciado"]
    assert "A conta da 91" in q["resolucao"] and "Resposta" not in q["resolucao"]
    assert all(len(x["alternativas"]) == 5 for x in qs)
    # a mesma questão em outro caderno não entra duas vezes
    assert len(S.questoes_reais(_simulado() + "\n" + _questao(91, "E", "x"))) == 8


def test_gabarito_colado_e_material_de_gabarito():
    assert S.ler_gabarito("91 C 92-A 93) B 94: anulada 95 – E 1,5 A") == {91: "C", 92: "A", 93: "B", 94: "X", 95: "E"}
    tabela = "\n".join(f"{n} {'ABCDE'[n % 5]}" for n in range(91, 136))
    assert S.parece_gabarito(tabela) and not S.parece_gabarito(_simulado())


def test_links_pdf_de_uma_pagina_e_baixar_so_pdf(monkeypatch):
    html = ('<a href="/provas/2023/caderno_azul.pdf">Caderno 1 – Azul</a> <a href="noticia.html">x</a>'
            '<a href="https://download.inep.gov.br/gab.PDF"> Gabarito </a>')
    links = ED.links_pdf(html, "https://www.gov.br/inep/pt-br/provas")
    assert [l["url"] for l in links] == ["https://www.gov.br/provas/2023/caderno_azul.pdf", "https://download.inep.gov.br/gab.PDF"]
    assert links[0]["titulo"] == "Caderno 1 – Azul"
    with pytest.raises(Exception):
        ED.baixar_pdf("http://127.0.0.1:8798/api/x.pdf")   # rede local: barrado antes de baixar


def test_resposta_e_resolucao_nunca_ficam_nas_alternativas():
    alts = "A) um\nB) dois\nC) três\nD) quatro\nE) cinco"
    antes = f"QUESTÃO 1\nEnunciado da primeira questão, longo o bastante.\n{alts}\nResposta: C\nResolução\nPorque sim.\n"
    sem_cab = f"QUESTÃO 2\nEnunciado da segunda questão, longo o bastante.\n{alts}\nComo a velocidade dobra, a energia quadruplica e por isso a resposta é a terceira opção, sem dúvida nenhuma.\nResposta: C\n"
    fim = f"QUESTÃO 3\nEnunciado da terceira questão, longo o bastante.\n{alts}\nGABARITO\n1 C 2 C 3 E\n"
    qs = {q["numero"]: q for q in S.questoes_reais(antes + sem_cab + fim)}
    for n in (1, 2, 3):
        assert qs[n]["alternativas"][-1] == "cinco", (n, qs[n]["alternativas"][-1])
        assert not any("Resposta" in a or "GABARITO" in a or "velocidade" in a for a in qs[n]["alternativas"])
    assert qs[1]["oficial"] == "C" and qs[2]["oficial"] == "C"
    assert "velocidade dobra" in qs[2]["resolucao"]


def test_leitura_de_gabarito_nao_cai_em_frase_nem_em_tabela():
    assert S.ler_gabarito("GABARITO — Questões de 91 a 135\n91 C 92 B") == {91: "C", 92: "B"}
    assert S.ler_gabarito("questões 1 e 2 anuladas\n1 C 2 D") == {1: "C", 2: "D"}
    assert S.ler_gabarito("91 92 93 94 95\nC A B D E")[93] == "B"
    assert S.ler_gabarito("91 | C\n92 | A") == {91: "C", 92: "A"}
    assert S.ler_gabarito("168 C\n169 Anulado\n170 E")[169] == "X"
    assert S.aviso_gabarito("\n".join(f"{n} C B D A" for n in range(91, 100)))
    assert S.RESOLUCAO.search("Comentário de um leitor sobre o texto") is None
    assert S.RESPOSTA.search("A resposta: a escolha do autor") is None


def test_caderno_do_inep_letra_dobrada_e_alternativa_em_figura():
    # o ícone da letra sai como a própria letra ("A A texto"); alternativa que é imagem fica só "A A"
    t = ("--- página 1 ---\nCAPA\n--- página 2 ---\n2\nQUESTÃO 91 \nA imunização produzida por esse tipo de vacina é alcançada por meio da\n"
         + "".join(f"{l} {l} opção {l.lower()} da 91\n" for l in "ABCDE")
         + "--- página 3 ---\n3\nQUESTÃO 92 \nQual gráfico representa a situação descrita no texto?\n"
         + "".join(f"{l} {l}  \n" for l in "ABCDE"))
    q91, q92 = S.questoes_reais(t)
    assert q91["pagina"] == 2 and q91["alternativas"] == [f"opção {l} da 91" for l in "abcde"]

CERTAS = {n: "ABCDE"[(n - 91) % 5] for n in range(91, 99)}   # o que o _fake responde


def _linhas(pares: dict[int, str]) -> str:
    itens = sorted(pares.items())
    return "\n".join(" ".join(f"{n:02d}: {l}" for n, l in itens[i:i + 4]) for i in range(0, len(itens), 4))


def _gabarito_da_banca() -> str:
    """Como o 'gabarito definitivo' da Access: cabeçalho em toda folha, cargo com código, PROVA 1..3 de cada."""
    cab = "PREFEITURA MUNICIPAL DE CONTAGEM/MG\nCONCURSO PÚBLICO - 01/2025\nGABARITO DEFINITIVO DA PROVA APLICADA NO DIA 22/02/2026"
    girar = lambda k: {n: "ABCDE"[((n - 91) + k) % 5] for n in range(91, 99)}
    partes = []
    for cod, cargo, provas in (("M1", "AUXILIAR DE LABORATÓRIO", (girar(3), girar(1), CERTAS)),
                               ("M20", "ANALISTA DE SISTEMAS", (girar(2), {**CERTAS, 93: "X"}, girar(4))),
                               ("M21", "ANALISTA DE SISTEMAS JÚNIOR", (CERTAS, girar(1), girar(2)))):
        partes += [cab, f"{cod} - {cargo}"]
        for i, p in enumerate(provas, 1):
            partes += [f"PROVA {i}", _linhas(p)]
        partes.append("Página 1 de 3")
    return "\n".join(partes)


def test_blocos_por_cargo_e_versao_sem_o_cabecalho_de_pagina():
    bs = G.blocos(_gabarito_da_banca())
    assert [G.rotulo(b) for b in bs][:4] == ["AUXILIAR DE LABORATÓRIO · Prova 1", "AUXILIAR DE LABORATÓRIO · Prova 2",
                                             "AUXILIAR DE LABORATÓRIO · Prova 3", "ANALISTA DE SISTEMAS · Prova 1"]
    assert len(bs) == 9 and all(len(b["pares"]) == 8 for b in bs)
    assert G.blocos("91 C 92 A 93 B 94 D 95 E 96 A")[0]["pares"][91] == "C"   # sem cabeçalho: um bloco só, como antes


def test_cargo_pelo_nome_mais_comprido_e_versao_pela_capa_ou_pela_ia():
    bs = G.blocos(_gabarito_da_banca())
    capa = "CADERNO DE PROVA – MANHÃ\nANALISTA DE SISTEMAS\n"
    sem_ia = G.escolher(bs, capa)
    assert sem_ia["bloco"] is None and "ANALISTA DE SISTEMAS" in sem_ia["motivo"]   # 'JÚNIOR' não casa: o nome inteiro tem de estar
    assert G.rotulo(G.escolher(bs, capa + "TIPO 3\n")["bloco"]) == "ANALISTA DE SISTEMAS · Prova 3"   # tipo como texto na capa
    e = G.escolher(bs, capa, ia=CERTAS)
    assert G.rotulo(e["bloco"]) == "ANALISTA DE SISTEMAS · Prova 2" and not e["aviso"]
    # sem o nome do cargo na prova: a melhor concordância de todos os blocos, com aviso
    e2 = G.escolher(bs, "prova sem cargo", ia=CERTAS)
    assert e2["bloco"]["pares"] == CERTAS and "Não achei o nome do cargo" in e2["aviso"]



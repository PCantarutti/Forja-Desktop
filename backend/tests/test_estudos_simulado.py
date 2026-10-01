import asyncio
import json
import re

import pytest

from app import config, db, estudos, estudos_busca as B, estudos_prova as P, estudos_simulado as S, mirror, pesquisa, web


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(estudos, "RAIZ", tmp_path / "estudos")
    monkeypatch.setattr(config, "SUBAGENTS", {})
    estudos._RUNS.clear()
    yield
    estudos._RUNS.clear()


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


def _estudo(texto: str):
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="ENEM")
        s.add(c)
        s.commit()
        conv = c.id
    m = estudos.adicionar_material(conv, "simulado.txt", texto=texto)
    return conv, m


USER: list[str] = []


def _fake(monkeypatch, erra=()):
    """Classifica pelo assunto do enunciado e resolve certo, menos as questões de `erra` (marca A)."""
    USER.clear()

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        user = "\n".join(p.get("text", "") for p in user) if isinstance(user, list) else user
        USER.append(user)
        if system.startswith("Você classifica"):
            out = {"questoes": [{"numero": int(n), "area": "Física" if a in ("cinemática", "óptica") else "Química",
                                 "assunto": a} for n, a in re.findall(r"^\[(\d+)\] Enunciado da questão \d+ sobre (\w+)", user, re.M)]}
        elif system.startswith("Você resolve questões de uma prova real"):
            out = {"respostas": []}
            for qid in re.findall(r"^\[(q\d+)\]", user, re.M):
                n = int(qid[1:])
                out["respostas"].append({"id": qid, "conta": f"conta {n}", "resposta": "A" if n in erra else "ABCDE"[(n - 91) % 5]})
        elif system.startswith("Você junta nomes"):
            out = {"grupos": [{"assunto": "Cinemática", "area": "Física", "de": ["cinemática"]}]}
        else:
            out = {}
        yield ("content", json.dumps(out, ensure_ascii=False))
        yield ("done", {"prompt_tokens": 1, "completion_tokens": 1})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)


def _rodar(fn, *a, **kw):
    async def main():
        msg = fn(*a, **kw)
        while estudos._TAREFAS:
            await asyncio.gather(*list(estudos._TAREFAS))
        return msg["id"]
    return asyncio.run(main())


def test_confere_com_o_gabarito_sem_mostrar_a_resolucao_e_vira_prova(monkeypatch):
    _fake(monkeypatch, erra={93})
    conv, m = _estudo(_simulado())
    sid = _rodar(S.start, conv, m["id"], "", 0, "fake", "m")
    d = S.detalhe(sid)
    assert d["status"] == "pronto", d.get("aviso")
    assert d["placar"]["questoes"] == 8 and d["placar"]["acertos"] == 7 and d["placar"]["resolvidas"] == 8
    errada = next(q for q in d["questoes"] if q["numero"] == 93)
    assert errada["ia"] == "A" and errada["oficial"] == "C" and errada["certa"] is False and errada["conta"] == "conta 93"
    assert {a["area"] for a in d["placar"]["por_area"]} == {"Física", "Química"}
    # quem resolve nunca viu a resolução nem o gabarito do PDF
    resolver = [u for u in USER if "[q9" in u]
    assert resolver and not any("A conta da" in u or "Resposta:" in u or "Resolução" in u for u in resolver)
    # ranking com o nome unificado pelo modelo
    nomes = {i["assunto"]: i for i in d["ranking"]["itens"]}
    assert nomes["Cinemática"]["questoes"] == 2 and nomes["Cinemática"]["simulados"] == 1
    assert estudos.projeto(conv)["ranking"]["questoes"] == 8

    criada = S.criar_prova(sid)
    assert criada["questoes"] == 8
    with db.session() as s:
        e = s.get(db.Message, criada["prova_id"]).meta["estudos"]
    q91 = next(q for q in e["questoes"] if q["id"] == "q91")
    assert q91["correta"] == 0 and q91["alternativas"][0] == "opção a da 91"   # letras do caderno, sem embaralhar
    assert "A conta da 91" in q91["explicacao"] and q91["origem"] == "simulado"
    assert next(q for q in e["questoes"] if q["id"] == "q93")["verificada"] is False
    t = P.entregar(criada["prova_id"], {"q91": 0, "q92": 0}, 60)   # a prova se faz e se corrige como as outras
    assert t["status"] in ("pronto", "rodando")


def test_gabarito_colado_vence_o_do_pdf_e_sem_gabarito_avisa(monkeypatch):
    _fake(monkeypatch)
    sem = "\n".join(_questao(n, "A", "óptica").split("Resolução")[0] for n in range(1, 7))
    conv, m = _estudo(sem)
    sid = _rodar(S.start, conv, m["id"], "", 0, "fake", "m")
    d = S.detalhe(sid)
    assert d["placar"]["com_gabarito"] == 0 and "Sem gabarito oficial" in d["aviso"]
    with pytest.raises(Exception):
        S.criar_prova(sid)
    sid2 = _rodar(S.start, conv, m["id"], "1 B 2 B 3 B 4 B 5 B 6 B", 0, "fake", "m")
    d2 = S.detalhe(sid2)
    assert d2["placar"]["com_gabarito"] == 6 and d2["gabarito"] == "colado"
    # o mesmo PDF conferido de novo não conta duas vezes no ranking; outra prova do estudo entra
    assert d2["ranking"]["simulados"] == 1 and d2["ranking"]["questoes"] == 6
    outra = estudos.adicionar_material(conv, "outra.txt", texto=_simulado(6))
    sid3 = _rodar(S.start, conv, outra["id"], "", 0, "fake", "m")
    d3 = S.detalhe(sid3)
    assert d3["status"] == "pronto", d3.get("aviso")
    r = d3["ranking"]
    assert r["simulados"] == 2 and r["questoes"] == 12


# ------------------------------------------------------------------ busca na web


def _pdf_de(texto: str) -> bytes:
    from tests.test_estudos_figuras import _pdf
    linhas = texto.splitlines()[:45]
    return _pdf(["".join(f"BT /F1 9 Tf 40 {800 - 16 * i} Td ({l.replace('(', '').replace(')', '')}) Tj ET\n" for i, l in enumerate(linhas))])


def test_busca_anexa_so_pdf_de_prova_e_ignora_link_inventado(monkeypatch):
    prova = "\n".join(f"QUESTAO {n} Assinale a alternativa correta sobre o tema {n}" for n in range(1, 30))
    pdfs = {"https://inep.gov.br/prova.pdf": _pdf_de(prova), "https://x.com/apostila.pdf": _pdf_de("Apostila de física\nconteúdo")}
    resultados = [{"url": u, "title": u.rsplit("/", 1)[-1], "content": ""} for u in pdfs] + \
                 [{"url": "https://inep.gov.br/provas", "title": "Provas anteriores", "content": "cadernos"}]
    monkeypatch.setattr(web, "buscar", lambda q, n=8: resultados)

    def baixar(url, teto=B.TETO_PDF, cancelado=None):
        if url not in pdfs:
            raise AssertionError(f"baixou o que não devia: {url}")
        return pdfs[url]
    monkeypatch.setattr(B, "baixar_pdf", baixar)
    monkeypatch.setattr(B, "PAUSA", 0)

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        system = messages[0]["content"]
        if system.startswith("Você procura"):
            out = {"buscas": ["enem 2020 prova pdf", "enem 2020 gabarito pdf"]}
        else:   # escolhe a prova, a apostila (que a checagem barra) e um número que não existe
            lista_ = messages[1]["content"]
            n = {u: int(i) for i, u in re.findall(r"^(\d+)\. \[\w+\] .*? — (\S+)$", lista_, re.M)}
            out = {"escolhidos": [{"n": n["https://inep.gov.br/prova.pdf"], "tipo": "prova", "exame": "ENEM 2020"},
                                  {"n": n["https://x.com/apostila.pdf"], "tipo": "prova"}, {"n": 99, "tipo": "prova"}]}
        yield ("content", json.dumps(out))
        yield ("done", {})
    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)

    with db.session() as s:
        c = db.Conversation(kind="estudos", title="ENEM")
        s.add(c)
        s.commit()
        conv = c.id
    bid = _rodar(B.start, conv, "ENEM 2020", "fake", "m")
    e = estudos.estado(bid)
    assert e["status"] == "pronto", e.get("aviso")
    assert [a["nome"] for a in e["anexados"]] == ["Prova · ENEM 2020 (prova).pdf"]
    motivos = {c["url"]: c for c in e["candidatos"]}
    assert motivos["https://x.com/apostila.pdf"]["status"] == "rejeitado"
    mats = estudos.materiais(conv)
    assert len(mats) == 1 and mats[0]["uso"] == "prova"
    assert estudos.projeto(conv)["busca"]["message_id"] == bid


def test_links_pdf_de_uma_pagina_e_baixar_so_pdf(monkeypatch):
    html = ('<a href="/provas/2023/caderno_azul.pdf">Caderno 1 – Azul</a> <a href="noticia.html">x</a>'
            '<a href="https://download.inep.gov.br/gab.PDF"> Gabarito </a>')
    links = B.links_pdf(html, "https://www.gov.br/inep/pt-br/provas")
    assert [l["url"] for l in links] == ["https://www.gov.br/provas/2023/caderno_azul.pdf", "https://download.inep.gov.br/gab.PDF"]
    assert links[0]["titulo"] == "Caderno 1 – Azul"
    with pytest.raises(Exception):
        B.baixar_pdf("http://127.0.0.1:8798/api/x.pdf")   # rede local: barrado antes de baixar


def test_prova_vai_com_o_gabarito_do_mesmo_caderno():
    base = "https://download.inep.gov.br/enem/provas_e_gabaritos/"
    achados = {base + n: {"url": base + n, "titulo": n, "trecho": ""} for n in ("2023_PV_impresso_D2_CD12.pdf", "2023_GB_impresso_D2_CD7.pdf")}
    escolhidos = [{**achados[base + "2023_PV_impresso_D2_CD12.pdf"], "tipo": "prova", "exame": "ENEM"},
                  {**achados[base + "2023_GB_impresso_D2_CD7.pdf"], "tipo": "gabarito", "exame": "ENEM"}]
    # o modelo juntou o caderno 12 com o gabarito do 7: vai o gabarito do 12 (deduzido no mesmo servidor), o do 7 sai
    assert [c["url"].rsplit("/", 1)[1] for c in B._parear(escolhidos, achados)] == ["2023_PV_impresso_D2_CD12.pdf", "2023_GB_impresso_D2_CD12.pdf"]
    assert B.gabarito_par("https://x.br/fuvest2024_prova_V.pdf", {"https://x.br/fuvest2024_gabarito_V.pdf"}) == "https://x.br/fuvest2024_gabarito_V.pdf"


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
    assert q92["pagina"] == 3 and q92["alternativas"] == ["(alternativa na figura)"] * 5

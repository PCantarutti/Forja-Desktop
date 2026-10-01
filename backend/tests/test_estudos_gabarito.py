"""Gabarito com vários cargos e versões num arquivo só, e gabarito anotado de imagem."""
import asyncio
import io
import json

import pytest

from app import config, db, design, estudos, estudos_figuras as F, estudos_gabarito as G, estudos_simulado as S, mirror, pesquisa

from tests.test_estudos_simulado import _estudo, _fake, _questao, _rodar


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(estudos, "RAIZ", tmp_path / "estudos")
    monkeypatch.setattr(config, "SUBAGENTS", {})
    estudos._RUNS.clear()
    yield
    estudos._RUNS.clear()


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


def test_conferencia_usa_o_bloco_da_prova_e_deixa_trocar_sem_resolver_de_novo(monkeypatch):
    _fake(monkeypatch)
    prova = "CADERNO DE PROVA – MANHÃ\nANALISTA DE SISTEMAS\n" + "\n".join(_questao(n, "A", "óptica").split("Resolução")[0] for n in range(91, 99))
    conv, m = _estudo(prova)
    gab = estudos.adicionar_material(conv, "Gabarito definitivo.pdf.txt", texto=_gabarito_da_banca())
    sid = _rodar(S.start, conv, m["id"], "", gab["id"], "fake", "m")
    d = S.detalhe(sid)
    assert d["status"] == "pronto", d.get("aviso")
    assert d["bloco"]["rotulo"] == "ANALISTA DE SISTEMAS · Prova 2" and "concordância" in d["bloco"]["motivo"]
    assert d["placar"]["com_gabarito"] == 7 and d["placar"]["acertos"] == 7   # a 93 anulada fica de fora
    assert [o["rotulo"] for o in d["bloco"]["opcoes"][:3]] == ["ANALISTA DE SISTEMAS · Prova 1", "ANALISTA DE SISTEMAS · Prova 2",
                                                               "ANALISTA DE SISTEMAS · Prova 3"]
    resolvidas = len(d["questoes"])
    outro = next(o for o in d["bloco"]["opcoes"] if o["rotulo"] == "ANALISTA DE SISTEMAS · Prova 1")
    d2 = S.trocar_bloco(sid, outro["id"])
    assert d2["bloco"]["rotulo"] == "ANALISTA DE SISTEMAS · Prova 1" and d2["placar"]["acertos"] == 0
    assert len(d2["questoes"]) == resolvidas and d2["bloco"]["motivo"] == "escolhido por você"
    with pytest.raises(Exception, match="não encontrado"):
        S.trocar_bloco(sid, "999")


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, "PNG")
    return buf.getvalue()


def test_gabarito_por_imagem_vira_material_que_a_conferencia_le(monkeypatch):
    vistos = []

    async def perguntar(spec, system, user, run=None, effort="baixo"):
        vistos.append((system, user))
        n = len(vistos)
        return json.dumps({"blocos": [{"cargo": "ANALISTA DE SISTEMAS", "prova": "1",
                                       "respostas": {str(k): v for k, v in list(CERTAS.items())[(n - 1) * 4:n * 4]} | {"99": "anulada"}}]})

    async def nada(*a, **k):
        return None

    async def sim(spec):
        return True

    monkeypatch.setattr(pesquisa, "_perguntar", perguntar)
    monkeypatch.setattr(design, "_garantir_local", nada)
    monkeypatch.setattr(F, "enxerga", sim)
    monkeypatch.setattr(estudos, "modelos", lambda *a: ({"provider": "fake", "model": "v"}, {"provider": "fake", "model": "v"}, False))
    conv, _ = _estudo("qualquer coisa")
    r = asyncio.run(G.transcrever(conv, [("print1.png", _png()), ("print2.png", _png())], "fake", "v"))
    # uma chamada por imagem, cada uma só com a imagem dela; as duas metades se juntam no mesmo bloco
    assert len(vistos) == 2 and all(isinstance(u, list) and any(p.get("type") == "image_url" for p in u) for _, u in vistos)
    assert r["questoes"] == 9 and r["blocos"] == [{"rotulo": "ANALISTA DE SISTEMAS · Prova 1", "n": 9}]
    texto = estudos._texto(conv, r["material"])
    b = G.blocos(texto)[0]
    assert b["pares"] == {**CERTAS, 99: "X"} and r["material"]["nome"].startswith("Gabarito (imagem)")
    monkeypatch.setattr(F, "enxerga", lambda spec: asyncio.sleep(0, result=False))
    with pytest.raises(Exception, match="não enxerga"):
        asyncio.run(G.transcrever(conv, [("p.png", _png())], "fake", "v"))


def test_cargo_repetido_antes_de_cada_versao_nao_vira_cabecalho():
    # o que um modelo (ou uma pessoa) escreve ao anotar: o cargo de novo em cada versão
    texto = "\n".join(f"ANALISTA DE SISTEMAS\nPROVA {i}\n{_linhas(CERTAS)}" for i in range(1, 5))
    assert [G.rotulo(b) for b in G.blocos(texto)] == [f"ANALISTA DE SISTEMAS · Prova {i}" for i in range(1, 5)]
    anotado = G.como_texto([{"cargo": "Analista de Sistemas", "prova": str(i), "respostas": CERTAS} for i in (1, 2, 3)])
    assert anotado.count("ANALISTA DE SISTEMAS") == 1 and len(G.blocos(anotado)) == 3

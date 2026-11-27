"""Simulado real pelo HTTP, com modelo falso: PDF + gabarito → prova da aba Provas, tópico, ranking, troca de gabarito."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import db, pesquisa
from app.estudo import base, corpus, simulado as SIM
from tests.test_estudo_simulado import _simulado


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAIZ", tmp_path / "estudo")
    base._RUNS.clear()

    async def nada(run):
        return None
    monkeypatch.setattr(base, "preparar_modelo", nada)
    monkeypatch.setattr(corpus, "extrair", lambda nome, dados: _simulado())   # o "PDF" é o caderno de texto do v1

    async def falso(spec, system, user, run=None, effort="baixo"):
        assert "Resolução" not in user and "Resposta:" not in user   # quem classifica nunca vê a resolução
        if "classifica" in system:
            nums = [int(x) for x in __import__("re").findall(r"\[(\d+)\]", user)]
            return json.dumps({"questoes": [{"numero": n, "area": "Física", "assunto": "óptica" if n % 2 else "cinemática"}
                                            for n in nums]})
        return ""
    monkeypatch.setattr(pesquisa, "_perguntar", falso)
    yield
    base._RUNS.clear()


def _materia() -> int:
    with db.session() as s:
        c = db.Conversation(kind="estudo", title="Física")
        s.add(c)
        s.commit()
        conv = c.id
    with base.mudar(conv) as est:
        est["programa"] = [{"id": "t1", "titulo": "Cinemática", "materia": "Física", "subtopicos": ["movimento uniforme"], "peso": 1},
                           {"id": "t2", "titulo": "Óptica", "materia": "Física", "subtopicos": ["espelhos", "lentes"], "peso": 1}]
    return conv


def _esperar(c, conv):
    for _ in range(200):
        e = c.get(f"/api/estudo/{conv}").json()
        if not e["run"] or e["run"]["status"] != "rodando":
            return e
        time.sleep(0.05)
    raise AssertionError("não terminou")


def test_pdf_com_gabarito_vira_prova_com_topico_e_ranking():
    from app import main
    conv = _materia()
    gab = " ".join(f"{n} {'ABCDE'[(n + 1) % 5]}" for n in range(91, 99))   # gabarito colado vale mais que o do PDF
    with TestClient(main.app) as c:
        r = c.post(f"/api/estudo/{conv}/simulado/arquivo", files={"file": ("enem.pdf", b"%PDF-1.4 x", "application/pdf")},
                   data={"gabarito": gab, "provider": "x", "model": "y"})
        assert r.status_code == 200, r.text
        e = _esperar(c, conv)
        assert e["run"]["status"] == "pronto", e["run"]
        s = e["simulados"][0]
        assert s["questoes"] == 8 and s["com_gabarito"] == 8 and s["prova"]
        p = next(x for x in e["provas"] if x["id"] == s["prova"])
        assert len(p["questoes"]) == 8 and "correta" not in p["questoes"][0]   # gabarito escondido até entregar
        assert {"t1", "t2"} <= {q["topico"] for q in p["questoes"]}   # empate (estequiometria) fica fora do programa
        assert {i["assunto"] for i in e["ranking"]} == {"óptica", "cinemática"}

        # entrega: tudo "A"; o gabarito colado de 91 é "ABCDE"[(92) % 5] = "C"
        c.post(f"/api/estudo/{conv}/prova/{p['id']}/entregar", json={"provider": "x", "model": "y",
                                                                      "respostas": {f"q{n}": "A" for n in range(91, 99)}})
        e = _esperar(c, conv)
        t = next(x for x in e["provas"] if x["id"] == p["id"])["tentativas"][0]
        assert t["total"] == 8 and t["resultado"]["q91"]["certa"] is False
        assert t["acertos"] == sum(1 for n in range(91, 99) if "ABCDE"[(n + 1) % 5] == "A")
        assert e["desempenho"]["respondidas"] == 8 and any(x["titulo"] == "Óptica" for x in e["desempenho"]["topicos"])

        # gabarito trocado depois: prova já feita não muda, sai outra
        r = c.post(f"/api/estudo/{conv}/simulado/{s['id']}/gabarito", json={"texto": " ".join(f"{n} A" for n in range(91, 99))})
        assert r.status_code == 200 and r.json()["prova"] != p["id"]
        assert c.get(f"/api/estudo/{conv}/simulado/{s['id']}/pdf").content.startswith(b"%PDF")


def test_sem_gabarito_usa_o_do_pdf_e_gabarito_ruim_e_recusado():
    from app import main
    conv = _materia()
    with TestClient(main.app) as c:
        r = c.post(f"/api/estudo/{conv}/simulado/arquivo", files={"file": ("x.pdf", b"%PDF x", "application/pdf")},
                   data={"gabarito": "sem pares aqui", "provider": "x", "model": "y"})
        assert r.status_code == 400
        assert c.post(f"/api/estudo/{conv}/simulado/arquivo", files={"file": ("x.txt", b"oi", "text/plain")},
                      data={"provider": "x", "model": "y"}).status_code == 400
        c.post(f"/api/estudo/{conv}/simulado/arquivo", files={"file": ("x.pdf", b"%PDF x", "application/pdf")},
               data={"provider": "x", "model": "y"})
        e = _esperar(c, conv)
        s = e["simulados"][0]
        assert s["gabarito"] == "do próprio PDF" and s["com_gabarito"] == 8   # o "Resposta: X" de cada questão


def test_parear_prova_com_gabarito_do_mesmo_caderno():
    b = "https://download.inep.gov.br/enem/provas_e_gabaritos/"
    achados = {b + n: {"url": b + n, "titulo": n, "trecho": ""} for n in ("2023_PV_impresso_D2_CD12.pdf", "2023_GB_impresso_D2_CD7.pdf")}
    escolhidos = [{**achados[b + "2023_PV_impresso_D2_CD12.pdf"], "tipo": "prova", "exame": "ENEM"},
                  {**achados[b + "2023_GB_impresso_D2_CD7.pdf"], "tipo": "gabarito", "exame": "ENEM"}]
    assert [c["url"].rsplit("/", 1)[1] for c in SIM._parear(escolhidos, achados)] == ["2023_PV_impresso_D2_CD12.pdf",
                                                                                      "2023_GB_impresso_D2_CD12.pdf"]


def test_gabarito_sozinho_puxa_o_caderno_pelo_nome():
    g = "https://arquivos.qconcursos.com/prova/arquivo_gabarito/115341/fcc-2024-trf-3-regiao-tecnico-judiciario-area-administrativa-gabarito.pdf"
    out = SIM._parear([{"url": g, "titulo": "gab", "trecho": "", "tipo": "gabarito", "exame": "TRF3"}], {g: {"url": g, "titulo": "gab", "trecho": ""}})
    assert [c["tipo"] for c in out] == ["prova", "gabarito"]
    assert out[0]["url"] == g.replace("arquivo_gabarito", "arquivo_prova").replace("-gabarito.pdf", "-prova.pdf")
    assert SIM.caderno_par("https://download.inep.gov.br/x/2023_GB_impresso_D2_CD12.pdf", set()).endswith("2023_PV_impresso_D2_CD12.pdf")


def test_gabarito_deduzido_do_caderno_e_tabela_no_fim():
    p = "https://arquivos.qconcursos.com/prova/arquivo_prova/122623/ibfc-2024-trf-5-regiao-tecnico-prova.pdf"
    assert SIM.gabarito_deduzido(p) == "https://arquivos.qconcursos.com/prova/arquivo_gabarito/122623/ibfc-2024-trf-5-regiao-tecnico-gabarito.pdf"
    out = SIM._parear([{"url": p, "titulo": "x", "trecho": "", "tipo": "prova", "exame": "TRF5"}], {p: {"url": p, "titulo": "x", "trecho": ""}})
    assert [c["tipo"] for c in out] == ["prova", "gabarito"]
    texto = "QUESTÃO 1 ...\nGABARITO\n01 C 02 A 03 B 04 D"
    assert SIM.gabarito_no_fim(texto, {1, 2, 3, 4}) == {1: "C", 2: "A", 3: "B", 4: "D"}
    assert SIM.gabarito_no_fim(texto, set(range(1, 30))) == {}   # cobre pouco: não é a tabela desta prova


def test_topico_so_pelo_assunto():
    est = {"programa": [{"id": "t1", "titulo": "Mandado de Segurança", "subtopicos": ["direito líquido e certo"]},
                        {"id": "t2", "titulo": "Habeas Data", "subtopicos": ["informações pessoais"]}]}
    reais = [{"assunto": "poder de polícia", "enunciado": "Lei de direito tribunal segurança"},
             {"assunto": "mandado de segurança: prazo", "enunciado": "x"}]
    SIM._topicos(est, reais)
    assert [q["topico"] for q in reais] == ["", "t1"]


def test_lei_generica_nao_liga_topico_e_enunciado_sem_alternativas():
    est = {"programa": [{"id": "t1", "titulo": "Mandado de Segurança (MS)", "subtopicos": ["MS contra lei em tese", "atos de particulares"]}]}
    reais = [{"assunto": "Lei de Acessibilidade", "enunciado": "x"}, {"assunto": "atos administrativos: revogação", "enunciado": "x"}]
    SIM._topicos(est, reais)
    assert [q["topico"] for q in reais] == ["", ""]
    en = "O trecho que apresenta palavra formada a partir de prefixo está em:\n(A) A vontade de abri-la.\n(8) O resto"
    assert SIM.SEM_ALTERNATIVAS.split(en, 1)[0].strip().endswith("está em:")


def test_uma_palavra_em_comum_nao_basta_para_assunto_composto():
    est = {"programa": [{"id": "t1", "titulo": "Comparativo Prático e Prazos", "subtopicos": []},
                        {"id": "t2", "titulo": "Habeas Data (HD)", "subtopicos": ["segurança de dados pessoais"]}]}
    reais = [{"assunto": "contrato de experiência: prazos", "enunciado": "x"},
             {"assunto": "segurança da informação", "enunciado": "x"},
             {"assunto": "habeas data", "enunciado": "x"}, {"assunto": "prazos", "enunciado": "x"}]
    SIM._topicos(est, reais)
    assert [q["topico"] for q in reais] == ["", "", "t2", "t1"]

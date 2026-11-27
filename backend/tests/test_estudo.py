"""Estudos v2: corpus (picar, BM25, citação), pós-processo do material e portões da prova, com modelo falso."""
import asyncio
import json

import pytest

from app import db, pesquisa
from app.estudo import base, corpus, extras, material, prova

LEI = """Lei Municipal 4.417 de Forjópolis.
Art. 1º O imposto sobre lanternas mágicas é devido por quem acende mais de doze lanternas por noite no perímetro urbano.
Art. 2º A alíquota do imposto é de sete por cento sobre o valor venal de cada lanterna acesa.
Art. 3º Ficam isentas as lanternas acesas em festas religiosas registradas na Secretaria de Luzes até trinta dias antes.
Art. 4º O pagamento vence no quinto dia útil do mês seguinte ao do acendimento, sob pena de multa de dois por cento."""


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAIZ", tmp_path / "estudo")
    base._RUNS.clear()

    async def nada(run):
        return None

    async def teto(run):
        return 20_000
    monkeypatch.setattr(base, "preparar_modelo", nada)
    monkeypatch.setattr(base, "orcamento", teto)
    yield
    base._RUNS.clear()


def _materia() -> int:
    with db.session() as s:
        c = db.Conversation(kind="estudo", title="Nova conversa")
        s.add(c)
        s.commit()
        conv = c.id
    with base.mudar(conv) as est:
        est["programa"] = [{"id": "t1", "titulo": "Imposto sobre lanternas", "materia": "Tributos", "subtopicos": [],
                            "peso": 1}]
    corpus.gravar_fonte(conv, LEI, "arquivo", "Lei 4417")
    return conv


def _modelo(monkeypatch, responder):
    """responder(system, user) -> texto. Substitui a chamada ao modelo."""
    async def falso(spec, system, user, run=None, effort="baixo"):
        return responder(system, user)
    monkeypatch.setattr(pesquisa, "_perguntar", falso)


def _rodar(conv, tipo, alvo, coro_de):
    run = base.novo_run(conv, tipo, {"provider": "x", "model": "y"})
    run["alvo"] = alvo
    asyncio.run(coro_de(run))
    return run


# ------------------------------------------------------------------ corpus

def test_picar_nao_perde_palavra():
    texto = "\n".join(f"Parágrafo {i}. " + "palavra " * 60 for i in range(40))
    partes = corpus.picar(texto)
    assert len(partes) > 5 and all(len(p) <= corpus.TRECHO_MAX for p in partes)
    assert corpus.norm(" ".join(partes)).split() == corpus.norm(texto).split()


def test_citacao_aceita_acento_e_espaco_recusa_parafrase():
    t = "Art. 2º A alíquota do imposto é de sete por cento sobre o valor venal de cada lanterna acesa."
    assert corpus.achar_citacao("a aliquota do imposto e de  sete por cento sobre o valor venal", t)
    assert corpus.achar_citacao("A alíquota do tributo é de sete por cento sobre o valor venal", t)  # 1 palavra trocada
    assert not corpus.achar_citacao("o imposto cobra sete por cento do preço de cada lanterna", t)
    assert not corpus.achar_citacao("sete por cento", t)   # curta demais para ancorar


def test_bm25_acha_o_trecho_certo():
    conv = _materia()
    corpus.gravar_fonte(conv, "Receita de bolo de cenoura com cobertura de chocolate.\n" * 3, "texto", "bolo")
    est = base.ler(conv)
    achados = corpus.buscar(conv, est, "alíquota imposto lanterna", "t1", k=2)
    assert achados and achados[0]["fonte"] == "F1"
    assert corpus.trecho(conv, achados[0]["id"]) == achados[0]["texto"]


# ------------------------------------------------------------------ material

def test_limpar_move_sem_citacao_para_complemento_e_tira_id_falso():
    md = ("### Imposto\n\nA alíquota é de sete por cento sobre o valor venal [F1·1].\n\n"
          "Este parágrafo explica algo que o modelo sabe e não cita fonte nenhuma aqui.\n\n"
          "Outro parágrafo com citação inventada de um trecho que não existe [F9·99].\n\n"
          ":::complemento\nAnalogia livre do professor [F1·1].\n:::")
    out, st = material.limpar(md, lambda i: i == "F1·1")
    assert "[F1·1]" in out.split(":::complemento")[0]
    assert "[F9·99]" not in out and st["removidas"] == 1
    assert out.count(":::complemento") == 3 and st["citados"] == 1
    assert "Analogia livre do professor" in out and "Analogia livre do professor [F1·1]" not in out


# ------------------------------------------------------------------ prova

def _questao(correta="B", citacao="A alíquota do imposto é de sete por cento sobre o valor venal"):
    return json.dumps({"enunciado": "Qual a alíquota do imposto sobre lanternas mágicas?",
                       "alternativas": ["A) 2%", "B) 7%", "C) 12%", "D) 30%", "E) 5%"],
                       "correta": correta, "explicacao": "O art. 2º fixa 7%.", "citacao": citacao})


def test_prova_aprova_questao_ancorada(monkeypatch):
    conv = _materia()
    _modelo(monkeypatch, lambda s, u: _questao() if "examinador" in s else "SIM" if "fidelidade" in s else "B")
    _rodar(conv, "prova", {"n": 1}, prova.rodar)
    p = base.ler(conv)["provas"][0]
    assert len(p["questoes"]) == 1 and p["questoes"][0]["fonte"].startswith("F1")
    assert "correta" not in prova.para_tela(p)["questoes"][0]   # sem gabarito antes de entregar


@pytest.mark.parametrize("questao,verificador,motivo", [
    (_questao(citacao="o tributo municipal custa sete por cento de cada lanterna"), "B", "citação não está no trecho"),
    (_questao(), "NAO_DA", "trecho não basta para responder"),
    (_questao(), "D", "verificador chegou em outra resposta"),
    (_questao(), "FIEL_NAO", "enunciado afirma o que o trecho não diz"),
    (json.dumps({"enunciado": "Qual a alíquota?", "alternativas": ["1", "1", "2", "3", "4"], "correta": "A",
                 "citacao": "x"}), "A", "alternativas inválidas"),
])
def test_prova_descarta_sem_ancora(monkeypatch, questao, verificador, motivo):
    conv = _materia()
    infiel = verificador == "FIEL_NAO"   # o verificador acerta, mas o enunciado afirma o que o trecho não diz
    _modelo(monkeypatch, lambda s, u: questao if "examinador" in s else ("NAO" if infiel else "SIM") if "fidelidade" in s
            else ("B" if infiel else verificador))
    run = base.novo_run(conv, "prova", {"provider": "x", "model": "y"})
    run["alvo"] = {"n": 1}
    with pytest.raises(Exception, match="Nenhuma questão passou"):
        asyncio.run(prova.rodar(run))
    assert any(motivo in linha for linha in run["log"])


def test_entrega_e_desempenho(monkeypatch):
    conv = _materia()
    _modelo(monkeypatch, lambda s, u: _questao() if "examinador" in s else "SIM" if "fidelidade" in s else "B")
    _rodar(conv, "prova", {"n": 1}, prova.rodar)
    pid = base.ler(conv)["provas"][0]["id"]
    _rodar(conv, "correcao", {"prova": pid, "respostas": {"q1": "C"}}, prova.corrigir)
    est = base.ler(conv)
    assert est["provas"][0]["tentativas"][0]["acertos"] == 0
    assert extras.caderno(est)[0]["correta"] == "B"
    assert extras.desempenho(est)["topicos"][0]["acerto"] == 0


def test_letra_do_verificador():
    assert prova._letra("B", "ABCDE") == "B"
    assert prova._letra("Letra c.", "ABCDE") == "C"
    assert prova._letra("NAO_DA", "CE") == "NAO_DA"
    assert prova._letra("O trecho diz que e permitido", "CE") == ""   # o "e" conjunção não é o E de errado


def test_rotas_disparam_e_terminam(monkeypatch):
    """Pelo HTTP: a rota dispara a execução no loop (rota síncrona não tem loop) e o GET mostra o resultado."""
    from fastapi.testclient import TestClient
    from app import main
    conv = _materia()
    _modelo(monkeypatch, lambda s, u: _questao() if "examinador" in s else "SIM" if "fidelidade" in s else "B")
    with TestClient(main.app) as c:
        r = c.post(f"/api/estudo/{conv}/prova", json={"provider": "x", "model": "y", "n": 1})
        assert r.status_code == 200, r.text
        for _ in range(100):
            e = c.get(f"/api/estudo/{conv}").json()
            if e["provas"]:
                break
            import time
            time.sleep(0.05)
        assert len(e["provas"]) == 1 and "correta" not in e["provas"][0]["questoes"][0]


def test_sigla_expandida_de_cabeca_e_barrada():
    fonte = "Institui o Imposto sobre Lanternas Mágicas (ILM). " + LEI
    assert prova.siglas_inventadas("O Imposto sobre Luminárias de Luz (ILM) incide…", fonte) == "ILM"
    assert prova.siglas_inventadas("O Imposto sobre Lanternas Mágicas (ILM) incide…", fonte) == ""
    assert prova.siglas_inventadas("Segundo a Organização Mundial (OMS)…", fonte) == ""   # sigla que a fonte nem usa

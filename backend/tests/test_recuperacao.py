"""E16 partes B e C: a escada (níveis 3–5), o juiz do raciocínio e o trabalho autônomo."""
import asyncio

import pytest

from app import agent, autonomo, config, db, juiz, llm, main, modelctl, progresso, projstate, taskdb, workspace  # noqa: F401
from app.progresso import Placar
from tests.test_progresso import _roda


def test_escada_sobe_e_so_progresso_forte_desce(tmp_path):
    p = Placar()
    assert [p.escala(), p.escala(), p.escala(), p.escala(), p.escala()] == [2, 3, 4, 5, 5]
    p.resultado("list_dir", {"path": "."}, "ok", "algo novo", None, False)   # resultado novo não basta
    p.passo(True)
    assert p.escala() == 5
    (tmp_path / "a.py").write_text("x = 2")
    p.resultado("write_file", {"path": "a.py"}, "ok", "gravado", tmp_path, True)  # arquivo mudou de verdade
    p.passo(True)
    assert p.escala() == 2


def test_recua_ao_ponto_bom_e_sem_ele_ao_inicio_do_turno(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    p = Placar()
    a.write_text("bom")
    p.resultado("write_file", {"path": "a.py"}, "ok", "", tmp_path, True)
    p.resultado("run_command", {"command": "pytest"}, "ok", "exit code: 0", tmp_path, False)  # ponto bom
    a.write_text("quebrado")
    b.write_text("novo")
    p.resultado("write_file", {"path": "a.py"}, "ok", "", tmp_path, True)
    p.resultado("write_file", {"path": "b.py"}, "ok", "", tmp_path, True)
    voltaram = p.recua(tmp_path, lambda c: None)            # b.py não existia antes do turno
    assert sorted(voltaram) == ["a.py", "b.py"]
    assert a.read_text() == "bom" and not b.exists()


@pytest.fixture
def maestro_conv():
    with db.session() as s:
        c = db.Conversation(title="M", kind="maestro")
        s.add(c)
        s.commit()
        conv = c.id
    tok = taskdb.CONV.set(conv)
    yield conv
    taskdb.CONV.reset(tok)


def test_nivel4_no_maestro_estaciona_a_tarefa_e_segue_para_a_proxima(maestro_conv):
    conv = maestro_conv
    taskdb.create_feature(conv, "F", "", [{"title": t, "contract": {"goal": t, "verify_command": "x"}}
                                          for t in ("Trava", "Solta")])
    taskdb.set_status("TASK-001", "queued", conv)
    taskdb.set_status("TASK-001", "implementing", conv)
    texto, nivel = agent._recuo(conv, agent.Run(conv), Placar(), "o mesmo erro voltou 3 vezes", True)
    assert nivel == 4 and "TASK-001" in texto and "TASK-002" in texto
    assert taskdb.get("TASK-001", conv).status == "needs_human"


def test_nivel4_sem_tarefa_independente_vira_nivel5(maestro_conv):
    conv = maestro_conv
    taskdb.create_feature(conv, "F", "", [{"title": "Trava", "contract": {"goal": "a", "verify_command": "x"}},
                                          {"title": "Depende", "contract": {"goal": "b", "verify_command": "x"},
                                           "depends_on": ["1"]}])
    taskdb.set_status("TASK-001", "queued", conv)
    assert agent._recuo(conv, agent.Run(conv), Placar(), "loop", True) == ("", 5)


def test_nivel5_grava_nota_manda_push_e_relatorio(maestro_conv, monkeypatch, tmp_path):
    conv = maestro_conv
    avisos, notas = [], []
    monkeypatch.setattr(autonomo, "avisa_celular", lambda t, x, c: avisos.append((t, x, c)))
    monkeypatch.setattr(projstate, "write", lambda *a: notas.append(a) or "SESSION-009.md")
    run = agent.Run(conv)
    run.auto.niveis.update({2: 3, 4: 1})

    async def cena():
        return [ev async for ev in agent._estaciona(conv, run, "nada funcionou", True, None or __import__("datetime").datetime(2000, 1, 1))]

    evs = asyncio.run(cena())
    textos = " ".join(e["message"]["content"] for e in evs if e.get("type") == "event")
    assert notas and "nada funcionou" in notas[0][5]
    assert avisos and avisos[0][0] == "Forja estacionou"
    assert "3 intervenção(ões)" in textos and "1 recuo(s)" in textos and "SESSION-009.md" in textos


class _Resp:
    def __init__(self, dado):
        self.dado = dado

    def json(self):
        return self.dado


def _servidor_juiz(monkeypatch, respostas):
    fila = list(respostas)

    class Cliente:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            return _Resp(fila.pop(0))

    monkeypatch.setattr(juiz.httpx, "AsyncClient", Cliente)
    monkeypatch.setattr(modelctl, "como_rodar", lambda papel, pedido: modelctl.Rota("outro-slot", pedido, 1, "t"))


def _logprobs(**probs):
    import math
    return {"choices": [{"logprobs": {"content": [{"top_logprobs": [
        {"token": k, "logprob": math.log(v)} for k, v in probs.items()]}]}}]}


def test_juiz_progredindo_estende_e_baixa_confianca_nao_aborta(monkeypatch):
    _servidor_juiz(monkeypatch, [_logprobs(A=0.8, B=0.2), _logprobs(A=0.4, B=0.6)])
    r1 = asyncio.run(juiz.julga("p", "raciocínio", {"provider": "local", "model": "m"}))
    assert r1["veredito"] == "progredindo" and juiz.decide(r1, False) == "estender"
    r2 = asyncio.run(juiz.julga("p", "raciocínio", {"provider": "local", "model": "m"}))
    assert r2["veredito"] == "girando" and r2["confianca"] == 0.6 and juiz.decide(r2, True) == ""


def test_juiz_sem_logprobs_cai_para_gramatica_e_exige_o_filtro(monkeypatch):
    _servidor_juiz(monkeypatch, [{"choices": [{"message": {"content": "B"}}]},
                                 {"choices": [{"message": {"content": "B"}}]}])
    r = asyncio.run(juiz.julga("p", "raciocínio", {"provider": "local", "model": "m"}))
    assert r == {"veredito": "girando", "confianca": None}
    assert juiz.decide(r, filtro_suspeita=False) == "" and juiz.decide(r, filtro_suspeita=True) == "abortar"


def test_juiz_sem_slot_livre_nao_roda(monkeypatch):
    monkeypatch.setattr(modelctl, "como_rodar",
                        lambda papel, pedido: modelctl.Rota("mesmo-slot-sequencial", pedido, 0, "t"))
    assert asyncio.run(juiz.julga("p", "r", {"provider": "local", "model": "m"})) is None


def test_autonomo_ask_user_anota_e_segue(monkeypatch, tmp_path):
    monkeypatch.setattr(autonomo, "ligado", lambda conv: True)
    monkeypatch.setattr(autonomo, "avisa_celular", lambda *a: None)
    n = {"c": 0}

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        n["c"] += 1
        if n["c"] == 1:
            yield "done", {"tool_calls": [{"id": "q1", "name": "ask_user", "arguments": {"questions": [
                {"question": "Qual banco?", "options": [{"label": "SQLite (Recomendado)"}, {"label": "Postgres"}]}]}}]}
        else:
            yield "content", "Segui com SQLite."
            yield "done", {"tool_calls": []}

    eventos = _roda(monkeypatch, tmp_path, fake)
    res = [e["message"] for e in eventos if e.get("type") == "tool_result"]
    assert "anotada para o relatório" in res[0]["content"]
    assert any("Relatório do trabalho autônomo" in (e["message"]["content"] or "") and "Qual banco?" in e["message"]["content"]
               for e in eventos if e.get("type") == "event")


def test_autonomo_limite_de_passos_vira_checkpoint_com_progresso(monkeypatch, tmp_path):
    monkeypatch.setattr(autonomo, "ligado", lambda conv: True)
    monkeypatch.setattr(autonomo, "avisa_celular", lambda *a: None)
    monkeypatch.setattr(config, "MAX_ITERATIONS", 3)
    n = {"c": 0}

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        n["c"] += 1
        if n["c"] <= 8:  # cada passo escreve um arquivo novo: progresso de verdade
            yield "done", {"tool_calls": [{"id": f"w{n['c']}", "name": "write_file",
                                           "arguments": {"path": f"f{n['c']}.txt", "content": str(n["c"])}}]}
        else:
            yield "content", "Pronto."
            yield "done", {"tool_calls": []}

    eventos = _roda(monkeypatch, tmp_path, fake)
    textos = [e["message"]["content"] for e in eventos if e.get("type") == "event"]
    assert any("Checkpoint de" in t for t in textos) and (tmp_path / "f8.txt").exists()
    assert not any("O agente parou" in t for t in textos)


def test_sessao_estoura_orcamento_de_passos(monkeypatch):
    monkeypatch.setattr(config, "AUTONOMO", {"passos": 10})
    s = autonomo.Sessao(1, ligado=True)
    s.passos = 10
    assert "10 passos" in s.estourou()


def test_loop_sem_saida_sobe_a_escada_e_estaciona_sem_silencio(monkeypatch, tmp_path):
    avisos = []
    monkeypatch.setattr(autonomo, "avisa_celular", lambda t, x, c: avisos.append(t))
    monkeypatch.setattr(config, "MAX_ITERATIONS", 200)
    (tmp_path / "a.py").write_text("x = 1")
    n = {"c": 0}

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        n["c"] += 1
        if n["c"] > 150:
            yield "content", "desisto"
            yield "done", {"tool_calls": []}
            return
        alvo = ["list_dir", "read_file"][n["c"] % 2]  # ciclo A,B,A,B que nunca muda nada
        yield "done", {"tool_calls": [{"id": f"c{n['c']}", "name": alvo, "arguments": {"path": "." if alvo == "list_dir" else "a.py"}}]}

    eventos = _roda(monkeypatch, tmp_path, fake)
    textos = [e["message"]["content"] for e in eventos if e.get("type") == "event"]
    assert any("histórico do loop foi resumido" in t for t in textos)
    assert any("Trabalho estacionado" in t for t in textos) and "Forja estacionou" in avisos
    assert n["c"] < 150   # parou pela escada, não pelo fim do roteiro

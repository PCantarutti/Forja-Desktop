"""E11: explorador só de leitura, relatório guardado no projeto e reúso no contrato."""
import asyncio
import os
import time

import pytest

from app import agent, config, db, exploracoes, llm, settings, subagents, taskdb, workspace


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    root = tmp_path / "projeto"
    root.mkdir()
    (root / "calc.py").write_text("def soma(a, b):\n    return a + b\n", encoding="utf-8")
    monkeypatch.setattr(config, "WORKSPACE_ROOT", root)
    workspace.CURRENT.set(root)

    async def nada(*a):
        return None

    monkeypatch.setattr(llm, "capabilities", nada)
    settings.reset()
    config.SUBAGENTS = {"capaz": {"provider": "lmstudio", "model": "coder-14b"},
                        "rapido": {"provider": "", "model": ""}, "nuvem": {"provider": "", "model": ""}}
    return root


@pytest.fixture
def conv():
    with db.session() as s:
        c = db.Conversation(title="Maestro", kind="maestro")
        s.add(c)
        s.commit()
        conv_id = c.id
    token = taskdb.CONV.set(conv_id)
    yield conv_id
    taskdb.CONV.reset(token)


def _explorador_fala(monkeypatch, passos):
    """Cada passo: ('ferramenta', args) ou ('fim', texto)."""
    vistos = []

    async def fala(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append({"tools": [t["function"]["name"] for t in tools or []]})
        tipo, valor = passos.pop(0)
        if tipo == "fim":
            yield "content", valor
            yield "done", {"tool_calls": [], "completion_tokens": 5}
        else:
            yield "done", {"tool_calls": [{"id": f"c{len(vistos)}", "name": tipo, "arguments": valor}],
                           "completion_tokens": 5}

    monkeypatch.setattr(llm, "chat_stream", fala)
    return vistos


def _explora(conv_id, pergunta, paths=None):
    run_obj = agent.Run(conv_id)
    run_obj.permission = "auto"
    req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="maestro", permission="auto")
    out: dict = {}
    call = {"id": "ex1", "name": "explore", "arguments": {"question": pergunta, "paths": paths or []}}

    async def cena():
        async for _ in agent._run_call(conv_id, call, req, run_obj, None, out):
            pass

    asyncio.run(cena())
    return out


RELATORIO = "RESPOSTA: soma está em calc.py.\nARQUIVOS:\ncalc.py:1 — define soma\nNÃO ENCONTRADO: nada"


def test_explorador_so_recebe_ferramentas_de_leitura_e_recusa_escrita(conv, pasta, monkeypatch):
    vistos = _explorador_fala(monkeypatch, [("write_file", {"path": "x.py", "content": "hack"}),
                                            ("fim", RELATORIO)])
    out = _explora(conv, "onde está soma?")
    assert set(vistos[0]["tools"]) <= set(subagents.EXPLORADOR_TOOLS)
    assert "write_file" not in vistos[0]["tools"]
    assert not (pasta / "x.py").exists()                       # nem inventando a chamada ele escreve
    passos = out["meta"]["sub"]["steps"]
    assert passos[0]["name"] == "write_file" and passos[0]["status"] == "erro"


def test_relatorio_fica_guardado_e_entra_no_indice(conv, pasta, monkeypatch):
    _explorador_fala(monkeypatch, [("fim", RELATORIO)])
    out = _explora(conv, "onde está soma?", ["calc.py"])
    assert out["status"] == "ok" and "EXP-001" in out["text"] and "RESPOSTA: soma está em calc.py" in out["text"]
    arquivo = pasta / ".forja/exploracoes/EXP-001.md"
    assert arquivo.is_file() and "calc.py:1" in arquivo.read_text(encoding="utf-8")
    assert "EXP-001: onde está soma?" in exploracoes.indice(pasta)
    assert "DESATUALIZADA" not in exploracoes.indice(pasta)


def test_exploracao_fica_desatualizada_quando_o_arquivo_citado_muda(conv, pasta, monkeypatch):
    _explorador_fala(monkeypatch, [("fim", RELATORIO)])
    _explora(conv, "onde está soma?")
    time.sleep(0.01)
    (pasta / "calc.py").write_text("def soma(a, b, c=0):\n    return a + b + c\n", encoding="utf-8")
    os.utime(pasta / "calc.py", None)
    assert "DESATUALIZADA: calc.py mudou" in exploracoes.indice(pasta)


def test_contrato_leva_o_relatorio_para_o_worker(conv, pasta, monkeypatch):
    _explorador_fala(monkeypatch, [("fim", RELATORIO)])
    _explora(conv, "onde está soma?")
    taskdb.create_feature(conv, "F", "", [{"title": "A", "contract": {
        "goal": "mudar soma", "verify_command": "pytest -q", "explorations": "EXP-001"}}])
    briefing = taskdb.render_contract(taskdb.get("TASK-001", conv))
    assert "O QUE JÁ SE SABE DO CÓDIGO" in briefing and "calc.py:1 — define soma" in briefing


def test_explore_no_maestro_e_no_agente(pasta):
    maestro = {t.name for t in agent.available_tools(None, "auto", maestro_mode=True)}
    comum = {t.name for t in agent.available_tools(None, "auto")}
    assert "explore" in maestro and "delegate_task" not in maestro
    assert "explore" in comum
    assert not agent.bloqueada_no_plano("explore")             # só lê: vale no modo Plano


def test_subagente_nao_abre_outro_explorador(conv, pasta):
    run_obj = agent.Run(conv)
    req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="agent", permission="auto")
    out: dict = {}

    async def cena():
        async for _ in agent._run_call(conv, {"id": "e", "name": "explore", "arguments": {"question": "q"}},
                                       req, run_obj, None, out, parent="p1"):
            pass

    asyncio.run(cena())
    assert out["status"] == "erro"


def test_explorador_que_escreve_a_chamada_como_texto_e_cobrado(conv, pasta, monkeypatch):
    """Na validação o gpt-oss escreveu {"path": ...} como texto e isso virava o relatório."""
    _explorador_fala(monkeypatch, [("fim", '{"path": "calc.py", "start_line": 1}'), ("fim", RELATORIO)])
    out = _explora(conv, "onde está soma?")
    assert out["status"] == "ok" and "RESPOSTA: soma está em calc.py" in out["text"]


def test_sem_relatorio_nada_e_guardado(conv, pasta, monkeypatch):
    _explorador_fala(monkeypatch, [("fim", "{}"), ("fim", "{}"), ("fim", "{}")])
    out = _explora(conv, "onde está soma?")
    assert out["status"] == "erro" and "não entregou o relatório" in out["text"]
    assert not (pasta / ".forja/exploracoes").exists()


def test_explorations_no_nivel_do_plano_vale_para_as_tarefas(conv, pasta, monkeypatch):
    _explorador_fala(monkeypatch, [("fim", RELATORIO)])
    _explora(conv, "onde está soma?")
    (pasta / "FORJA.md").write_text("# Calc\nPython. Testes: pytest -q\n", encoding="utf-8")
    taskdb.PLAN_FEATURE.handler(None, {"explorations": ["EXP-001"], "tasks": [
        {"title": "A", "contract": {"goal": "a", "verify_command": "pytest -q"}},
        {"title": "B", "contract": {"goal": "b", "verify_command": "pytest -q", "explorations": ["EXP-009"]}}]})
    assert taskdb.get("TASK-001", conv).contract["explorations"] == ["EXP-001"]
    assert taskdb.get("TASK-002", conv).contract["explorations"] == ["EXP-009"]  # a da tarefa vence

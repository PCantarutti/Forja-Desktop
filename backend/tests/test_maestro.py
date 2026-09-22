"""Orchestrator do Maestro: despacho, dependências, tentativas e o resultado estruturado.

Chama `maestro.run_task` direto, com um `run_call` de mentira no lugar do laço do agente — o mesmo
arranjo do test_extremo.py, que é onde o Worker (subagents) já é exercitado.
"""
import asyncio

import pytest

from app import agent, config, db, llm, maestro, settings, subagents, taskdb, workspace


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    pasta = tmp_path / "projeto"
    pasta.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_ROOT", pasta)
    workspace.CURRENT.set(pasta)

    async def nada(*a):
        return None

    monkeypatch.setattr(llm, "capabilities", nada)
    settings.reset()  # antes dos slots: reset() relê as configurações do banco e limpa SUBAGENTS
    config.SUBAGENTS = {"capaz": {"provider": "lmstudio", "model": "coder-14b"},
                        "rapido": {"provider": "lmstudio", "model": "small-3b"}}
    yield pasta
    settings.reset()
    config.SUBAGENTS = {}
    workspace.CURRENT.set(None)


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


def _plano(conv_id, tasks=None):
    return taskdb.create_feature(conv_id, "Autenticação", "Login", tasks or [
        {"title": "Modelo", "contract": {"goal": "Criar o modelo User",
                                         "verify_command": "pytest -q tests/test_user.py"}},
        {"title": "Rota", "contract": {"goal": "Criar POST /login"}, "depends_on": ["1"]}])


def _fala(texto="Implementei o modelo User em models.py."):
    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        yield "content", texto
        yield "done", {"tool_calls": [], "completion_tokens": 420}

    return fake


def _run_call_factory(respostas=None):
    """run_call de mentira: devolve o que foi combinado por nome de ferramenta."""
    chamadas = []
    respostas = respostas or {}

    async def run_call(_conv, call, _req, _run, _caps, out, parent=None):
        chamadas.append(call)
        status, texto = respostas.get(call["name"], ("ok", f"[{call['name']}] ok"))
        out.update(status=status, text=texto, meta={"arguments": call["arguments"]})
        return
        yield  # pragma: no cover  (só para a função ser geradora assíncrona)

    return run_call, chamadas


def _despacha(conv_id, code, *, strategy="", run_call=None, permission="auto", cancel=False):
    run_obj = agent.Run(conv_id)
    run_obj.permission = permission
    if cancel:
        run_obj.cancel.set()
    req = agent.RunRequest(content="x", provider="lmstudio", model="maestro-32b",
                           mode="maestro", permission=permission, effort="medio")
    out: dict = {}
    call = {"id": "rt1", "name": "run_task", "arguments": {"code": code, "strategy": strategy}}

    async def scenario():
        eventos = []
        async for ev in maestro.run_task(conv_id, call, req, run_obj, out, run_call or _run_call_factory()[0]):
            eventos.append(ev)
        return eventos

    return out, asyncio.run(scenario())


# ------------------------------------------------------------------ guardas antes do despacho

def test_dependencia_pendente_recusa_o_despacho(conv, monkeypatch):
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    out, _ = _despacha(conv, "TASK-002")
    assert out["status"] == "erro" and "TASK-001" in out["text"]
    assert taskdb.get("TASK-002").attempt_count == 0  # nada foi gasto


def test_tarefa_inexistente(conv):
    out, _ = _despacha(conv, "TASK-404")
    assert out["status"] == "erro" and "não existe" in out["text"]


def test_tarefa_concluida_nao_reexecuta(conv):
    _plano(conv)
    for st in ("queued", "implementing", "testing", "reviewing", "completed"):
        taskdb.set_status("TASK-001", st, conv)
    out, _ = _despacha(conv, "TASK-001")
    assert out["status"] == "erro" and "já está concluída" in out["text"]


def test_limite_de_tentativas_marca_needs_human(conv):
    _plano(conv)
    taskdb._update_task(None, {"code": "TASK-001", "max_attempts": 2})
    for _ in range(2):
        taskdb.new_attempt("TASK-001", {})
    out, _ = _despacha(conv, "TASK-001")
    assert out["status"] == "erro" and "needs_human" in out["text"]
    assert taskdb.get("TASK-001").status == "needs_human"


def test_segunda_tentativa_exige_estrategia(conv):
    """§10: mudar de abordagem entre tentativas, não repetir o mesmo pedido."""
    _plano(conv)
    aid = taskdb.new_attempt("TASK-001", {})
    taskdb.finish_attempt(aid, "failed", {"tests": {"command": "pytest -q", "status": "erro",
                                                    "output": "1 failed"}})
    out, _ = _despacha(conv, "TASK-001")
    assert out["status"] == "erro" and "strategy" in out["text"]
    assert "1 failed" in out["text"]  # o erro anterior vai junto, para ele diagnosticar


def test_sem_worker_configurado_nao_gasta_tentativa(conv):
    config.SUBAGENTS = {}
    _plano(conv)
    out, _ = _despacha(conv, "TASK-001")
    assert out["status"] == "erro" and "Worker" in out["text"]
    assert taskdb.get("TASK-001").attempt_count == 0


# ------------------------------------------------------------------ despacho feliz

def test_despacho_persiste_tentativa_e_devolve_resultado(conv, monkeypatch):
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    out, eventos = _despacha(conv, "TASK-001")
    assert out["status"] == "ok"
    r = out["meta"]["task_result"]
    assert r["type"] == "task_result" and r["task_code"] == "TASK-001" and r["attempt"] == 1
    assert r["model"] == "coder-14b" and r["level"] == "capaz"
    assert taskdb.get("TASK-001").attempt_count == 1
    tent = taskdb.detail(conv, "TASK-001")["attempts"][0]
    assert tent["status"] != "running" and tent["result"]["task_code"] == "TASK-001"


def test_verificacao_que_passa_vira_completed_e_tarefa_fica_em_reviewing(conv, monkeypatch):
    """O Worker não assina o próprio atestado: a tarefa espera o julgamento do Maestro."""
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    out, _ = _despacha(conv, "TASK-001")
    assert out["meta"]["task_result"]["status"] == "completed"
    assert taskdb.get("TASK-001").status == "reviewing"
    assert "update_task(status='completed')" in out["text"]


def test_verificacao_que_falha_vira_failed(conv, monkeypatch):
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    run_call, _ = _run_call_factory({"run_command": ("erro", "2 failed, 1 passed")})
    out, _ = _despacha(conv, "TASK-001", run_call=run_call)
    r = out["meta"]["task_result"]
    assert r["status"] == "failed" and r["tests"]["status"] == "erro"
    assert "2 failed" in r["tests"]["output"]
    assert taskdb.get("TASK-001").status == "failed"


def test_sem_comando_de_verificacao_o_resultado_e_unverified(conv, monkeypatch):
    """Sem comando, nada prova nada — o Maestro é obrigado a julgar em vez de confiar no relatório."""
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv, [{"title": "Sem prova", "contract": {"goal": "fazer algo"}}])
    out, _ = _despacha(conv, "TASK-001")
    assert out["meta"]["task_result"]["status"] == "unverified"
    assert out["meta"]["task_result"]["tests"] is None
    assert "NADA prova que funcionou" in out["text"]


def test_o_worker_recebe_o_contrato_renderizado(conv, monkeypatch):
    """O briefing sai do Implementation Contract, não de texto que o modelo escreveu."""
    vistos = []

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(messages)
        yield "content", "feito"
        yield "done", {"tool_calls": []}

    monkeypatch.setattr(llm, "chat_stream", fake)
    _plano(conv, [{"title": "Filtro", "contract": {
        "goal": "Filtrar produtos", "context": "A tela lista tudo",
        "requirements": ["Select de categorias"], "do_not": ["alterar a API"],
        "acceptance_criteria": ["Bebidas mostra só bebidas"]}}])
    _despacha(conv, "TASK-001")
    brief = vistos[0][1]["content"]
    assert "TAREFA TASK-001" in brief and "NÃO FAÇA" in brief and "alterar a API" in brief
    assert "Bebidas mostra só bebidas" in brief


def test_estrategia_e_erro_anterior_entram_no_briefing(conv, monkeypatch):
    vistos = []

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(messages)
        yield "content", "feito"
        yield "done", {"tool_calls": []}

    monkeypatch.setattr(llm, "chat_stream", fake)
    _plano(conv)
    aid = taskdb.new_attempt("TASK-001", {})
    taskdb.finish_attempt(aid, "failed", {"errors": ["ImportError: no module named user"]})
    _despacha(conv, "TASK-001", strategy="crie o pacote antes")
    brief = vistos[0][1]["content"]
    assert "MUDE A ABORDAGEM" in brief and "crie o pacote antes" in brief
    assert "ImportError" in brief


def test_cancelar_fecha_a_tentativa_e_a_tarefa(conv, monkeypatch):
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    out, _ = _despacha(conv, "TASK-001", cancel=True)
    assert out["status"] == "cancelada"
    assert taskdb.get("TASK-001").status == "cancelled"
    assert taskdb.detail(conv, "TASK-001")["attempts"][0]["status"] == "cancelled"


def test_eventos_de_tarefa_saem_para_a_interface(conv, monkeypatch):
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    _, eventos = _despacha(conv, "TASK-001")
    tipos = [e["type"] for e in eventos]
    assert "task_update" in tipos
    assert any(e.get("parent") == "rt1" for e in eventos)  # passos do Worker, para a coluna WORKER


# ------------------------------------------------------------------ collect_result

def test_mudancas_saem_do_disco_quando_nao_ha_repositorio(conv, monkeypatch, clean):
    """Sem git não há diff, mas os caminhos escritos ainda são fato observado."""
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    task = taskdb.get("TASK-001")
    sub_out = {"status": "ok", "text": "pronto", "meta": {"sub": {
        "model": "coder-14b", "level": "capaz", "seconds": 3.0, "tokens": 10, "iterations": 2,
        "steps": [{"name": "write_file", "status": "ok", "arguments": {"path": "a.py"}, "result": ""},
                  {"name": "read_file", "status": "ok", "arguments": {"path": "b.py"}, "result": ""}]}}}
    r = maestro.collect_result(task, 1, sub_out, clean)
    assert [c["path"] for c in r["changes"]] == ["a.py"]  # só escrita conta como mudança


def test_erros_dos_passos_entram_no_resultado(conv, clean):
    task_stub = type("T", (), {"code": "TASK-001", "contract": {}})()
    sub_out = {"status": "ok", "text": "parcial", "meta": {"sub": {"steps": [
        {"name": "run_command", "status": "erro", "arguments": {"command": "pytest"}, "result": "boom"}]}}}
    r = maestro.collect_result(task_stub, 2, sub_out, clean)
    assert r["errors"] == ["run_command: boom"]
    assert r["commands"] == [{"command": "pytest", "status": "erro"}]
    assert r["attempt"] == 2


def test_worker_que_falhou_vira_status_error(conv, clean):
    task_stub = type("T", (), {"code": "TASK-001", "contract": {}})()
    sub_out = {"status": "erro", "text": "Subagente falhou (coder-14b): timeout", "meta": {"sub": {}}}
    r = maestro.collect_result(task_stub, 1, sub_out, clean)
    assert r["status"] == "error" and "timeout" in r["summary"]


# ------------------------------------------------------------------ integração com o agente

def test_ferramentas_do_maestro_so_existem_no_modo_maestro():
    nomes = lambda **kw: [t.name for t in agent.available_tools(set(), "auto", **kw)]
    assert "run_task" not in nomes()
    assert {"plan_feature", "list_tasks", "update_task", "run_task"} <= set(nomes(maestro_mode=True))


def test_modo_plano_so_deixa_ler_as_tarefas():
    """Planejar não é hora de criar, mudar nem despachar tarefa."""
    nomes = [t.name for t in agent.available_tools(set(), "plan", maestro_mode=True)]
    assert "list_tasks" in nomes
    assert not ({"run_task", "update_task", "plan_feature"} & set(nomes))


def test_prompt_do_maestro_proibe_implementar():
    p = agent.system_prompt("native", set(), permission="auto", maestro_mode=True)
    assert "MAESTRA" in p and "NÃO IMPLEMENTA" in p and "run_task" in p


def test_run_task_e_isenta_do_detector_de_laco():
    """Chamar run_task três vezes na mesma tarefa é o ciclo de tentativas, não um laço."""
    assert taskdb.RUN_TASK.poll
    # _poll procura fora do REGISTRY também: as ferramentas do Maestro não estão nele.
    assert agent._poll({"name": "run_task", "arguments": {"code": "TASK-001"}})
    assert agent._poll({"name": "list_tasks", "arguments": {}})
    assert not agent._poll({"name": "update_task", "arguments": {}})


def test_worker_nao_despacha_tarefas(conv, monkeypatch):
    """run_task com parent = um Worker tentando virar Maestro."""
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    run_obj = agent.Run(conv)
    req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="maestro")
    out: dict = {}

    async def scenario():
        async for _ in agent._run_call(conv, {"id": "c1", "name": "run_task",
                                              "arguments": {"code": "TASK-001"}},
                                       req, run_obj, set(), out, parent="d1"):
            pass

    asyncio.run(scenario())
    assert out["status"] == "erro" and "Worker" in out["text"]


# ------------------------------------------------------------------ rotas HTTP

@pytest.fixture
def cliente():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def test_conversa_maestro_pode_ser_criada(cliente):
    r = cliente.post("/api/conversations", json={"kind": "maestro"})
    assert r.status_code == 200 and r.json()["kind"] == "maestro"


def test_kind_desconhecido_e_recusado(cliente):
    assert cliente.post("/api/conversations", json={"kind": "xpto"}).status_code == 400


def test_board_traz_a_arvore(conv, cliente):
    _plano(conv)
    b = cliente.get(f"/api/maestro/{conv}/board").json()
    assert b["total"] == 2 and len(b["features"]) == 1
    assert b["features"][0]["tasks"][1]["depends_on"] == ["TASK-001"]


def test_detalhe_da_tarefa_e_404(conv, cliente):
    _plano(conv)
    assert cliente.get(f"/api/maestro/{conv}/task/TASK-001").json()["contract"]["goal"]
    assert cliente.get(f"/api/maestro/{conv}/task/TASK-404").status_code == 404


def test_intervencao_humana_edita_a_tarefa(conv, cliente):
    """§27: o usuário corrige o contrato, troca o modelo ou desbloqueia sem passar pelo modelo."""
    _plano(conv)
    r = cliente.post(f"/api/maestro/{conv}/task/TASK-001", json={"model_slot": "capaz"})
    assert r.status_code == 200 and r.json()["task"]["model_slot"] == "capaz"
    assert cliente.post(f"/api/maestro/{conv}/task/TASK-001", json={}).status_code == 400


def test_ferramentas_do_maestro_sao_executaveis(conv):
    """Regressão: elas ficam fora do REGISTRY, e sem tools.EXTRA o get_tool devolvia
    'Ferramenta desconhecida' — o Maestro chamava plan_feature e não acontecia nada."""
    import asyncio as _asyncio

    from app.tools import EXTRA, REGISTRY, execute, get_tool

    assert not (set(EXTRA) & set(REGISTRY))  # nunca nas Configurações nem em /api/tools
    for nome in ("plan_feature", "list_tasks", "update_task", "run_task"):
        assert get_tool(nome).name == nome
    texto = _asyncio.run(execute("plan_feature", {
        "title": "X", "goal": "y", "tasks": [{"contract": {"goal": "fazer algo"}}]}))
    assert "TASK-001" in texto
    assert "TASK-001" in _asyncio.run(execute("list_tasks", {}))


def test_ferramentas_do_maestro_fora_de_api_tools(cliente):
    nomes = {t["name"] for t in cliente.get("/api/tools").json()}
    assert not ({"plan_feature", "list_tasks", "update_task", "run_task"} & nomes)


def test_update_tasks_sai_do_modo_maestro():
    """Duas listas de tarefas na mesma tela fazem o modelo escolher a que não guarda nada."""
    nomes = [t.name for t in agent.available_tools(set(), "auto", maestro_mode=True)]
    assert "update_tasks" not in nomes and "plan_feature" in nomes
    assert "update_tasks" in [t.name for t in agent.available_tools(set(), "auto")]


def test_worker_de_contrato_recebe_so_as_ferramentas_de_implementar(conv, monkeypatch):
    """Orçamento de contexto: os schemas de todas as ferramentas custam ~4900 tokens e não cabem
    junto com o contrato numa janela de 8k — o Worker batia no teto antes de rodar o teste."""
    import json as _json

    from app.tools import Tool

    vistos = []

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(tools or [])
        yield "content", "feito"
        yield "done", {"tool_calls": []}

    monkeypatch.setattr(llm, "chat_stream", fake)
    _plano(conv)
    _despacha(conv, "TASK-001")

    nomes = {t["function"]["name"] for t in vistos[0]}
    assert nomes <= set(subagents.WORKER_TOOLS)
    assert {"read_file", "write_file", "edit_file", "run_command"} <= nomes
    assert not ({"write_document", "image_generate", "remember", "delegate_task"} & nomes)
    # E o ganho é o que justifica a lista: bem abaixo do que custava mandar tudo.
    assert len(_json.dumps(vistos[0], ensure_ascii=False)) // 4 < 2000


def test_delegate_task_comum_continua_com_todas(conv, monkeypatch):
    """A lista enxuta vale só para o Worker de contrato; o delegate_task do agente não muda."""
    vistos = []

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(tools or [])
        yield "content", "feito"
        yield "done", {"tool_calls": []}

    monkeypatch.setattr(llm, "chat_stream", fake)
    run_obj = agent.Run(conv)
    req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="agent")
    out: dict = {}

    async def cenario():
        rc, _ = _run_call_factory()
        async for _ in subagents.run(conv, {"id": "d1", "name": "delegate_task",
                                            "arguments": {"task": "faça algo"}},
                                     req, run_obj, out, rc):
            pass

    asyncio.run(cenario())
    nomes = {t["function"]["name"] for t in vistos[0]}
    assert "write_document" in nomes and len(nomes) > len(subagents.WORKER_TOOLS)


def test_ferramentas_de_escrituracao_nao_pedem_aprovacao():
    """Descoberto rodando de verdade: com `mutating`, a Maestro parava num card a cada tarefa
    fechada. update_task mexe só no banco do Forja (como update_tasks) e run_task delega — cada
    escrita do Worker lá dentro já passa por policy.decide, como no delegate_task."""
    from app.tools import REGISTRY, get_tool

    assert not get_tool("update_task").mutating
    assert not get_tool("run_task").mutating
    assert not get_tool("plan_feature").mutating
    assert not REGISTRY["delegate_task"].mutating  # o precedente
    assert REGISTRY["write_file"].mutating         # o que de fato altera continua pedindo


def test_policy_libera_as_do_maestro_no_modo_automatico():
    from app import policy
    from app.tools import get_tool

    for nome in ("plan_feature", "list_tasks", "update_task", "run_task"):
        precisa, _ = policy.decide(get_tool(nome), {"code": "TASK-001"}, "auto")
        assert not precisa, f"{nome} pararia a execução autônoma"

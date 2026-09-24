"""Orchestrator do Maestro: despacho, dependências, tentativas e o resultado estruturado.

Chama `maestro.run_task` direto, com um `run_call` de mentira no lugar do laço do agente — o mesmo
arranjo do test_extremo.py, que é onde o Worker (subagents) já é exercitado.
"""
import asyncio

import pytest

from app import agent, config, db, llm, maestro, settings, subagents, taskdb, workspace
from app.tools import ToolError


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
    # a tentativa não é gravada como falha: nada provou nem desprovou
    assert taskdb.detail(conv, "TASK-001")["attempts"][0]["status"] == "unverified"


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
    assert not agent.bloqueada_no_plano("list_tasks")
    assert all(agent.bloqueada_no_plano(n) for n in ("run_task", "update_task", "plan_feature", "session_note"))


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


# ------------------------------------------------------------------ ciclo de vida do modelo

class _FakeLocal:
    """llama-server de mentira: o suficiente para modelctl decidir e registrar a troca."""

    def __init__(self, alias="", janela=131072):
        self.alias = alias
        self.janela = janela
        self.chamadas = []

    def ctx_de(self, path):
        return self.janela

    def status(self):
        return {"running": bool(self.alias), "alias": self.alias, "ctx": 8192, "loading": {}}

    def hardware(self):
        return {"vram": 8 << 30, "vram_free": 6 << 30, "ram": 16 << 30, "ram_free": 8 << 30}

    def scan(self):
        return [{"path": r"D:\m\W.gguf", "name": "W", "kind": "chat"}]

    def alias_of(self, path):
        return path.rsplit("\\", 1)[-1].removesuffix(".gguf")

    def image_busy(self):
        return False

    def load(self, path):
        self.chamadas.append(f"load:{self.alias_of(path)}")
        self.alias = self.alias_of(path)

    def unload(self):
        self.chamadas.append("unload")
        self.alias = ""

    def cancel_load(self):
        return True


def _local(monkeypatch, alias="", janela=131072):
    from app import modelctl
    f = _FakeLocal(alias, janela)
    monkeypatch.setattr(modelctl, "localai", f)
    monkeypatch.setattr(subagents, "localai", f)
    monkeypatch.setitem(config.PROVIDERS, "local",
                        {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    config.SUBAGENTS = {"capaz": {"provider": "local", "model": "W"}}
    return f


def test_run_task_carrega_o_modelo_do_worker(conv, monkeypatch):
    """O ciclo que sustenta IA local em máquina apertada: a tarefa carrega o modelo de que precisa."""
    f = _local(monkeypatch, alias="OUTRO")
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    out, eventos = _despacha(conv, "TASK-001")
    assert out["status"] == "ok"
    assert f.chamadas == ["load:W"] and f.alias == "W"
    fases = [e["phase"] for e in eventos if e["type"] == "model"]
    assert fases == ["unloading", "loading", "ready"]
    assert out["meta"]["model_swap"] == {"from": "OUTRO", "to": "W"}
    assert "loading_model" in [e.get("status") for e in eventos if e["type"] == "task_update"]


def test_run_task_nao_recarrega_o_que_ja_esta_no_ar(conv, monkeypatch):
    f = _local(monkeypatch, alias="W")
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    out, eventos = _despacha(conv, "TASK-001")
    assert out["status"] == "ok" and f.chamadas == []
    assert not [e for e in eventos if e["type"] == "model"]
    assert "model_swap" not in out["meta"]


def test_unload_after_task_libera_a_vram_no_fim(conv, monkeypatch):
    f = _local(monkeypatch, alias="W")
    monkeypatch.setattr(config, "MODEL_LIFECYCLE", "unload_after_task")
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    out, eventos = _despacha(conv, "TASK-001")
    assert out["status"] == "ok"
    assert f.alias == "" and "unload" in f.chamadas
    assert [e["phase"] for e in eventos if e["type"] == "model"][-1] == "unloaded"


def test_modelo_que_nao_carrega_falha_a_tarefa_sem_rodar_worker(conv, monkeypatch):
    from app import modelctl
    f = _local(monkeypatch, alias="OUTRO")
    monkeypatch.setattr(f, "load", lambda p: (_ for _ in ()).throw(ToolError("VRAM insuficiente")))
    chamou = []
    monkeypatch.setattr(llm, "chat_stream", _fala())
    monkeypatch.setattr(subagents, "run", lambda *a, **k: chamou.append(1))
    _plano(conv)
    out, _ = _despacha(conv, "TASK-001")
    assert out["status"] == "erro" and "VRAM insuficiente" in out["text"]
    assert not chamou  # não gastou um Worker com o modelo errado
    assert taskdb.get("TASK-001", conv).status == "failed"
    assert taskdb.detail(conv, "TASK-001")["attempts"][0]["status"] == "error"


def test_redespachar_tarefa_em_reviewing_funciona(conv, monkeypatch):
    """O Maestro não gostou do resultado e mandou de novo: antes isso batia em transição ilegal."""
    _local(monkeypatch, alias="W")
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    _despacha(conv, "TASK-001")
    assert taskdb.get("TASK-001", conv).status == "reviewing"
    out, _ = _despacha(conv, "TASK-001", strategy="tente com outra biblioteca")
    assert out["status"] == "ok" and out["meta"]["task_result"]["attempt"] == 2


# ------------------------------------------------------------------ Etapa 5: validação e revisão

def test_browser_validate_e_do_maestro_nao_do_worker():
    """§17: o navegador é ferramenta de validação da Maestro. O Worker implementa."""
    from app.tools import get_tool

    nomes = [t.name for t in agent.available_tools({"vision"}, "auto", maestro_mode=True)]
    assert "browser_validate" in nomes
    assert "browser_validate" not in subagents.WORKER_TOOLS
    assert not get_tool("browser_validate").mutating  # abrir e ler não altera nada


def test_prompt_manda_validar_tela_no_navegador():
    p = agent.system_prompt("native", {"vision"}, permission="auto", maestro_mode=True)
    assert "browser_validate" in p and "plan_feature(feature_id=..., tasks=[correções])" in p


def test_resultado_sem_verificacao_ganha_revisao(conv, monkeypatch):
    """Sem comando que prove nada, a revisão do diff é o único parecer disponível."""
    monkeypatch.setattr(llm, "chat_stream", _fala())

    async def revisao(root, task, paths):
        return "revisor-3b", "VEREDITO: ajustar\nfalta tratar divisão por zero em calc.py"

    monkeypatch.setattr(subagents, "_review", revisao)
    _plano(conv, [{"title": "Sem prova", "contract": {"goal": "fazer algo"}}])
    out, eventos = _despacha(conv, "TASK-001")
    r = out["meta"]["task_result"]
    assert r["status"] == "unverified"
    assert "divisão por zero" in r["review"] and "revisor-3b" in r["review"]
    assert "reviewing" in [e.get("status") for e in eventos if e["type"] == "task_update"]


def test_resultado_verificado_nao_chama_revisao(conv, monkeypatch):
    """Com exit code 0 na mão, o parecer de um modelo menor rende falso-positivo, não bug."""
    monkeypatch.setattr(llm, "chat_stream", _fala())
    chamou = []

    async def revisao(root, task, paths):
        chamou.append(1)
        return "x", "y"

    monkeypatch.setattr(subagents, "_review", revisao)
    _plano(conv)  # TASK-001 tem verify_command
    out, _ = _despacha(conv, "TASK-001")
    assert out["meta"]["task_result"]["status"] == "completed"
    assert not chamou


# ------------------------------------------------------------------ Etapa 6: paralelo e locks

def _rt(code):
    return {"id": f"c-{code}", "name": "run_task", "arguments": {"code": code}}


def test_run_task_so_paraleliza_fora_do_modo_sequencial(monkeypatch):
    """No sequencial (o padrão, e o único que troca modelo local) dois Workers disputariam a VRAM."""
    monkeypatch.setattr(config, "MAX_WORKERS", 1)
    assert [len(b) for b in agent.batches([_rt("TASK-001"), _rt("TASK-002")])] == [1, 1]
    monkeypatch.setattr(config, "MAX_WORKERS", 3)
    assert [len(b) for b in agent.batches([_rt("TASK-001"), _rt("TASK-002")])] == [2]


def test_limite_de_workers_acompanha_a_configuracao(monkeypatch):
    """O semáforo fica em cache: sem o limite na chave, mudar MAX_WORKERS não teria efeito."""
    monkeypatch.setattr(config, "MAX_WORKERS", 2)
    a = agent._limite(_rt("TASK-001"))
    monkeypatch.setattr(config, "MAX_WORKERS", 4)
    b = agent._limite(_rt("TASK-001"))
    assert a is not b and b._value == 4


def test_arquivos_disjuntos_correm_juntos():
    """src/auth/* e src/dashboard/* não se tocam: as duas tarefas ficam ativas ao mesmo tempo."""
    ativos, pico = [0], [0]

    async def tarefa(arquivos):
        async with maestro._travas(9001, arquivos):
            ativos[0] += 1
            pico[0] = max(pico[0], ativos[0])
            await asyncio.sleep(0.05)
            ativos[0] -= 1

    async def cena():
        await asyncio.gather(tarefa(["src/auth/login.py"]), tarefa(["src/dashboard/home.py"]))

    asyncio.run(cena())
    assert pico[0] == 2


def test_mesmo_arquivo_serializa():
    """package.json nas duas: uma espera a outra terminar em vez de escrever por cima."""
    ativos, pico = [0], [0]

    async def tarefa(arquivos):
        async with maestro._travas(9002, arquivos):
            ativos[0] += 1
            pico[0] = max(pico[0], ativos[0])
            await asyncio.sleep(0.05)
            ativos[0] -= 1

    async def cena():
        await asyncio.gather(tarefa(["package.json", "src/a.ts"]), tarefa(["package.json"]))

    asyncio.run(cena())
    assert pico[0] == 1


def test_ordem_inversa_nao_trava():
    """{a, b} e {b, a}: adquirindo na ordem do contrato cada uma pegaria metade e esperaria a outra
    para sempre. A ordem alfabética garante que as duas terminam."""
    async def tarefa(arquivos):
        async with maestro._travas(9003, arquivos):
            await asyncio.sleep(0.02)

    async def cena():
        await asyncio.wait_for(asyncio.gather(tarefa(["b.py", "a.py"]), tarefa(["a.py", "b.py"])), 2)

    asyncio.run(cena())  # sem deadlock: termina dentro do timeout


def test_caminho_normalizado_no_lock():
    """./src/A.py e src\\a.py são o mesmo arquivo para quem escreve nele."""
    assert maestro._chave(1, "./src/A.py") == maestro._chave(1, r"src\a.py")
    assert maestro._chave(1, "a.py") != maestro._chave(2, "a.py")  # conversas não se travam


def test_em_conflito_aponta_o_arquivo_ocupado():
    async def cena():
        async with maestro._travas(9004, ["package.json"]):
            return maestro.em_conflito(9004, ["package.json", "src/x.py"])

    assert asyncio.run(cena()) == ["package.json"]
    assert maestro.em_conflito(9004, ["package.json"]) == []  # liberou ao sair


def test_maestro_so_perde_o_que_atrapalha_por_funcao():
    """Orçamento de contexto não é motivo para tirar ferramenta: modelo de janela pequena é barrado
    na escolha. Fora ficam só os segundos jeitos de fazer o que ela já faz melhor."""
    nomes = {t.name for t in agent.available_tools({"vision"}, "auto", maestro_mode=True)}
    assert not ({"delegate_task", "update_tasks"} & nomes)
    assert {"write_document", "remember", "read_file", "run_command", "browser_validate",
            "plan_feature", "run_task", "session_note", "ask_user"} <= nomes


def test_worker_local_com_janela_curta_nao_gasta_tentativa(conv, monkeypatch):
    """O servidor recusaria o prompt no meio do trabalho; recusar antes não queima max_attempts."""
    _local(monkeypatch, alias="W", janela=8192)
    monkeypatch.setattr(config, "WORKER_MIN_CTX", 16384)
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    out, _ = _despacha(conv, "TASK-001")
    assert out["status"] == "erro" and "8.192" in out["text"] and "16.384" in out["text"]
    assert "IA local" in out["text"]  # diz onde resolver
    assert taskdb.get("TASK-001", conv).attempt_count == 0


def test_worker_de_nuvem_nao_passa_pelo_minimo(conv, monkeypatch):
    """A janela do modelo de nuvem não é o usuário que escolhe: o mínimo vale só para o local."""
    from app import modelctl
    assert modelctl.janela({"provider": "lmstudio", "model": "grande"}) is None
    assert modelctl.janela_curta({"provider": "lmstudio", "model": "grande"}, 10**9, "x") == ""


def _roda_maestro(monkeypatch, janela, provider="local"):
    """run_agent inteiro no modo Maestro, com o provedor e a janela combinados. Devolve os eventos e
    se o modelo chegou a ser chamado."""
    chamado = []

    async def fake_stream(*a, **k):
        chamado.append(1)
        yield "content", "ok"
        yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    async def fake_limit(*a):
        return janela

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", fake_limit)
    monkeypatch.setitem(config.PROVIDERS, "local", {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    from app import modelctl
    monkeypatch.setattr(modelctl, "carregado", lambda spec: True)  # o modelo da Maestro já está no ar

    async def cena():
        with db.session() as s:
            c = db.Conversation(kind="maestro")
            s.add(c)
            s.commit()
            cid = c.id
        req = agent.RunRequest(content="objetivo", provider=provider, model="m", mode="maestro",
                               permission="auto")
        return [ev async for ev in agent.run_agent(cid, req, agent.Run(cid))]

    return asyncio.run(cena()), chamado


def test_maestro_local_com_janela_curta_nem_comeca(monkeypatch):
    """Melhor recusar agora do que o servidor recusar o prompt no meio de uma tarefa."""
    monkeypatch.setattr(config, "MAESTRO_MIN_CTX", 32768)
    eventos, chamado = _roda_maestro(monkeypatch, 8192)
    erros = [e["message"]["content"] for e in eventos if e["type"] == "event"
             and (e["message"].get("meta") or {}).get("kind") == "error"]
    assert erros and "8.192" in erros[0] and "32.768" in erros[0]
    assert not chamado  # o modelo nem foi chamado


def test_maestro_local_com_janela_boa_roda(monkeypatch):
    monkeypatch.setattr(config, "MAESTRO_MIN_CTX", 32768)
    _, chamado = _roda_maestro(monkeypatch, 131072)
    assert chamado


def test_parar_no_meio_guarda_o_que_o_worker_fez(conv, monkeypatch):
    """Parar uma tarefa lenta apagava o rastro: modelo, passos e tokens sumiam, e sobrava só o log do
    llama.cpp para descobrir por que ela estava lenta."""
    passo = {"n": 0}

    async def fala(provider, model, messages, tools, num_ctx, effort=None, **kw):
        passo["n"] += 1
        if passo["n"] == 1:  # primeiro passo: pede uma ferramenta
            yield "done", {"tool_calls": [{"id": "c1", "name": "write_file",
                                           "arguments": {"path": "a.py", "content": "x=1"}}],
                           "completion_tokens": 30}
        else:
            yield "content", "continuando"
            yield "done", {"tool_calls": [], "completion_tokens": 5}

    monkeypatch.setattr(llm, "chat_stream", fala)
    run_obj = agent.Run(conv)

    async def run_call(_c, call, _r, _ru, _caps, out, parent=None):
        out.update(status="ok", text="gravado", meta={"arguments": call["arguments"]})
        run_obj.cancel.set()  # o usuário aperta Parar logo depois do primeiro passo
        return
        yield

    _plano(conv, [{"title": "X", "contract": {"goal": "g"}}])
    req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="maestro", permission="auto")
    out: dict = {}

    async def cena():
        async for _ in maestro.run_task(conv, {"id": "rt1", "name": "run_task",
                                               "arguments": {"code": "TASK-001"}}, req, run_obj, out, run_call):
            pass

    asyncio.run(cena())
    assert out["status"] == "cancelada"
    assert out["meta"]["sub"]["model"] == "coder-14b"
    assert [s["name"] for s in out["meta"]["sub"]["steps"]] == ["write_file"]
    tent = taskdb.detail(conv, "TASK-001")["attempts"][0]
    assert tent["status"] == "cancelled" and tent["result"]["status"] == "cancelled"
    assert tent["result"]["model"] == "coder-14b" and tent["tokens"] == 30


def test_maestro_nao_roda_em_esforco_extremo(monkeypatch):
    """Extremo manda delegar por delegate_task, que a Maestro não tem: vira Máximo."""
    visto = {}

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        visto["effort"] = effort
        yield "content", "ok"
        yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    async def limite(*a):
        return 131072

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", limite)

    async def cena():
        with db.session() as s:
            c = db.Conversation(kind="maestro")
            s.add(c)
            s.commit()
            cid = c.id
        req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="maestro",
                               permission="auto", effort="extremo")
        [ev async for ev in agent.run_agent(cid, req, agent.Run(cid))]
        return req

    req = asyncio.run(cena())
    assert req.effort == "maximo" and visto["effort"] == "maximo"


def test_maestro_local_recoloca_o_proprio_modelo_antes_de_gerar(conv, monkeypatch):
    """O Worker da tarefa anterior deixou o GGUF dele no ar. O llama.cpp ignora o campo `model`:
    sem recarregar, a Maestro rodaria calada no modelo do Worker."""
    f = _local(monkeypatch, alias="W")
    monkeypatch.setattr(f, "scan", lambda: [{"path": r"D:\m\M.gguf", "name": "M", "kind": "chat"}])
    gerou_com = []

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        gerou_com.append(f.alias)  # qual modelo estava no ar quando a Maestro gerou
        yield "content", "ok"
        yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    async def limite(*a):
        return 131072

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", limite)

    async def cena():
        req = agent.RunRequest(content="x", provider="local", model="M", mode="maestro", permission="auto")
        return [ev async for ev in agent.run_agent(conv, req, agent.Run(conv))]

    eventos = asyncio.run(cena())
    assert gerou_com and set(gerou_com) == {"M"} and f.chamadas == ["load:M"]
    assert [e["phase"] for e in eventos if e["type"] == "model"] == ["unloading", "loading", "ready"]


# ------------------------------------------------------------------ conversa do Worker

def test_worker_de_contrato_vira_conversa_gravada_na_tentativa(conv, monkeypatch):
    """O cockpit desenha o Worker como uma tela de agente, e a conversa fica na tarefa para rever."""
    passo = {"n": 0}

    async def fala(provider, model, messages, tools, num_ctx, effort=None, **kw):
        passo["n"] += 1
        if passo["n"] == 1:
            yield "reasoning", "vou criar o arquivo"
            yield "done", {"tool_calls": [{"id": "c1", "name": "write_file",
                                           "arguments": {"path": "a.py", "content": "x = 1"}}],
                           "prompt_tokens": 900, "completion_tokens": 40}
        else:
            yield "content", "Pronto."
            yield "done", {"tool_calls": [], "prompt_tokens": 1000, "completion_tokens": 5}

    monkeypatch.setattr(llm, "chat_stream", fala)
    _plano(conv)  # TASK-001 tem verify_command
    out, eventos = _despacha(conv, "TASK-001")
    assert out["status"] == "ok"

    t = taskdb.detail(conv, "TASK-001")["attempts"][0]["transcript"]
    papeis = [(m["role"], m.get("name")) for m in t]
    # briefing, rodada com a ferramenta, resultado, rodada final, verificação e a saída dela
    assert papeis == [("user", None), ("assistant", None), ("tool", "write_file"),
                      ("assistant", None), ("assistant", None), ("tool", "run_command")]
    assert "TAREFA TASK-001" in t[0]["content"]
    assert t[1]["thinking"] == "vou criar o arquivo"
    assert t[1]["tool_calls"][0]["name"] == "write_file"
    assert t[1]["meta"]["stats"]["tokens"] == 40 and t[1]["meta"]["stats"]["prompt_tokens"] == 900
    assert t[4]["meta"]["verificacao"] and t[4]["tool_calls"][0]["arguments"]["command"]
    assert [m["id"] for m in t] == list(range(1, len(t) + 1))

    # ao vivo, os mesmos passos saem pela SSE marcados com o id da chamada run_task
    tipos = {e["type"] for e in eventos if e.get("parent") == "rt1"}
    assert {"sub_message", "sub_assistant_start", "sub_thinking", "tool_result"} <= tipos


def test_board_nao_carrega_a_conversa_dos_workers(conv, monkeypatch):
    """O /board é consultado a cada 2 s: a conversa de cada Worker iria junto em toda consulta."""
    monkeypatch.setattr(llm, "chat_stream", _fala())
    _plano(conv)
    _despacha(conv, "TASK-001")
    tent = taskdb.board(conv)["features"][0]["tasks"][0]["attempts"][0]
    assert "transcript" not in tent and tent["has_transcript"]


def test_conversa_fica_gravada_mesmo_se_parar_no_meio(conv, monkeypatch):
    """Gravada a cada rodada: o Parar não leva junto o que o Worker já tinha feito."""
    async def fala(provider, model, messages, tools, num_ctx, effort=None, **kw):
        yield "done", {"tool_calls": [{"id": "c1", "name": "write_file",
                                       "arguments": {"path": "a.py", "content": "x"}}], "completion_tokens": 3}

    monkeypatch.setattr(llm, "chat_stream", fala)
    run_obj = agent.Run(conv)

    async def run_call(_c, call, _r, _ru, _caps, out, parent=None):
        out.update(status="ok", text="gravado", meta={"arguments": call["arguments"]})
        run_obj.cancel.set()
        return
        yield

    _plano(conv, [{"title": "X", "contract": {"goal": "g"}}])
    req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="maestro", permission="auto")

    async def cena():
        async for _ in maestro.run_task(conv, {"id": "rt1", "name": "run_task",
                                               "arguments": {"code": "TASK-001"}}, req, run_obj, {}, run_call):
            pass

    asyncio.run(cena())
    t = taskdb.detail(conv, "TASK-001")["attempts"][0]["transcript"]
    assert [m["role"] for m in t] == ["user", "assistant", "tool"]


def test_delegate_task_comum_nao_grava_conversa(conv, monkeypatch):
    """A conversa gravada é do Worker de contrato; o delegate_task do agente segue como sempre."""
    monkeypatch.setattr(llm, "chat_stream", _fala())
    out: dict = {}
    eventos = []

    async def cena():
        rc, _ = _run_call_factory()
        async for ev in subagents.run(conv, {"id": "d1", "name": "delegate_task",
                                             "arguments": {"task": "faça algo"}},
                                      agent.RunRequest(content="x", provider="lmstudio", model="m"),
                                      agent.Run(conv), out, rc):
            eventos.append(ev)

    asyncio.run(cena())
    assert not any(e["type"].startswith("sub_") and e["type"] != "sub_status" for e in eventos)


# ------------------------------------------------------------------ item 3: configurações e intervenção

def test_modelo_padrao_da_maestro_valida_o_provedor():
    out = settings.update({"maestro_model": {"provider": "local", "model": "gemma-4-12b-it-Q4_K_M"}})
    assert out["maestro_model"] == {"provider": "local", "model": "gemma-4-12b-it-Q4_K_M"}
    assert config.MAESTRO_MODEL["model"] == "gemma-4-12b-it-Q4_K_M"
    with pytest.raises(settings.SettingsError, match="não existe"):
        settings.update({"maestro_model": {"provider": "inexistente", "model": "x"}})


def test_validacao_no_navegador_desligada_tira_ferramentas_e_regra(monkeypatch):
    ligado = agent.system_prompt("native", {"vision"}, permission="auto", maestro_mode=True)
    assert "browser_validate" in ligado
    monkeypatch.setattr(config, "MAESTRO_BROWSER", False)
    nomes = {t.name for t in agent.available_tools({"vision"}, "auto", maestro_mode=True)}
    assert not any(n.startswith("browser_") for n in nomes)
    desligado = agent.system_prompt("native", {"vision"}, permission="auto", maestro_mode=True)
    assert "browser_validate" not in desligado and "rode a suíte/build/lint, confira o objetivo" in desligado
    # o agente comum não é afetado
    assert any(t.name.startswith("browser_") for t in agent.available_tools({"vision"}, "auto"))


def test_pausar_segura_o_proximo_passo_e_continuar_solta(monkeypatch):
    chamadas = []

    async def fake_stream(*a, **k):
        chamadas.append(1)
        yield "content", "ok"
        yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    async def fake_limit(*a):
        return 131072

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", fake_limit)
    monkeypatch.setitem(config.PROVIDERS, "local", {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    from app import modelctl
    monkeypatch.setattr(modelctl, "carregado", lambda spec: True)

    async def cena():
        with db.session() as s:
            c = db.Conversation(kind="maestro")
            s.add(c)
            s.commit()
            cid = c.id
        run = agent.Run(cid)
        run.pausar(True)
        req = agent.RunRequest(content="x", provider="local", model="m", mode="maestro", permission="bypass")
        eventos = []

        async def consome():
            async for ev in agent.run_agent(cid, req, run):
                eventos.append(ev)

        tarefa = asyncio.ensure_future(consome())
        await asyncio.sleep(0.3)
        parado = (len(chamadas), run.snapshot()["paused"])
        run.pausar(False)
        await asyncio.wait_for(tarefa, 5)
        return parado, eventos

    (chamadas_pausado, pausado), eventos = asyncio.run(cena())
    assert chamadas_pausado == 0 and pausado        # pausado: o modelo nem foi chamado
    assert chamadas and eventos[-1]["type"] == "done"
    assert any(e.get("text", "").startswith("Pausado") for e in eventos if e["type"] == "status")


def test_parar_solta_uma_execucao_pausada(monkeypatch):
    async def cena():
        run = agent.Run(1)
        run.pausar(True)
        espera = asyncio.ensure_future(run.espera_retomar())
        await asyncio.sleep(0.05)
        run.stop()
        await asyncio.wait_for(espera, 2)
        return run.paused

    assert asyncio.run(cena()) is False



# ------------------------------------------------------------------ item 4: recuperação e Git por tarefa

def test_worker_recarrega_o_modelo_que_caiu_e_repete_o_passo(conv, monkeypatch):
    """O llama-server morreu no meio da tarefa: recarrega e continua, sem perder a tentativa."""
    f = _local(monkeypatch, alias="W")
    chamadas = {"n": 0}

    async def stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            f.alias = ""  # o processo morreu
            raise llm.LLMError("Não consegui conectar ao servidor do modelo.")
        yield "content", "Pronto."
        yield "done", {"tool_calls": [], "completion_tokens": 3}

    monkeypatch.setattr(llm, "chat_stream", stream)
    _plano(conv)
    out, eventos = _despacha(conv, "TASK-001")
    assert out["status"] == "ok" and f.chamadas == ["load:W"] and chamadas["n"] == 2
    assert [e["phase"] for e in eventos if e["type"] == "model"] == ["loading", "ready"]


def test_desfazer_uma_tentativa_nao_mexe_no_resto_do_turno(conv, tmp_path):
    from app import checkpoints
    alvo = tmp_path / "a.txt"
    alvo.write_text("original", "utf-8")
    checkpoints.record(conv, 1, alvo, attempt_id=101)
    alvo.write_text("tentativa 1", "utf-8")
    checkpoints.record(conv, 1, alvo, attempt_id=102)
    alvo.write_text("tentativa 2", "utf-8")
    assert checkpoints.restore_attempt(102) and alvo.read_text("utf-8") == "tentativa 1"
    # o desfazer do turno continua voltando ao estado de antes do turno
    alvo.write_text("de novo", "utf-8")
    checkpoints.restore_from(conv, 1)
    assert alvo.read_text("utf-8") == "original"


def test_mudanca_externa_e_avisada_uma_vez(conv, tmp_path):
    from app import checkpoints
    _plano(conv)
    aid = taskdb.new_attempt("TASK-001", {}, "", conv)
    arq = tmp_path / "calc.py"
    arq.write_text("x = 1", "utf-8")
    checkpoints.record(conv, 1, arq, attempt_id=aid)
    arq.write_text("x = 2", "utf-8")
    maestro.registra_estado(aid, tmp_path)
    assert maestro.alteracoes_externas(conv, tmp_path) == []
    arq.write_text("x = 3  # editado à mão", "utf-8")
    assert maestro.alteracoes_externas(conv, tmp_path) == ["calc.py"]
    assert maestro.alteracoes_externas(conv, tmp_path) == []  # aceita depois de avisar


def test_rota_desfaz_a_tentativa_e_reabre_a_tarefa(conv, cliente, tmp_path):
    from app import checkpoints
    _plano(conv)
    aid = taskdb.new_attempt("TASK-001", {}, "", conv)
    for st in ("queued", "implementing", "testing", "reviewing", "completed"):
        taskdb.set_status("TASK-001", st, conv)
    arq = tmp_path / "novo.py"
    checkpoints.record(conv, 1, arq, attempt_id=aid)  # não existia antes
    arq.write_text("print('oi')", "utf-8")
    r = cliente.post(f"/api/maestro/{conv}/task/TASK-001/attempt/1/rollback")
    assert r.status_code == 200 and not arq.exists()
    assert r.json()["task"]["status"] == "pending"
    assert cliente.post(f"/api/maestro/{conv}/task/TASK-001/attempt/1/rollback").status_code == 400


def test_atividade_conta_as_aprovacoes_esperando(cliente):
    run = agent.Run(987654)
    run.approvals["c1"] = {"call": {"id": "c1"}, "preview": None, "parent": "rt1"}
    agent.RUNS[run.id] = run
    try:
        conv = next(c for c in cliente.get("/api/activity").json()["conversations"] if c["id"] == 987654)
        assert conv["waiting"] == 1 and conv["running"]
    finally:
        agent.RUNS.pop(run.id)


def test_roteador_escolhe_especialista_pelo_tipo_e_pelos_arquivos(monkeypatch):
    from app import subagents
    monkeypatch.setattr(config, "SUBAGENTS", {"capaz": {"provider": "x", "model": "geral"}})
    monkeypatch.setattr(config, "WORKER_ESPECIALIDADES", [
        {"id": "frontend", "nome": "Frontend", "quando": "", "provider": "x", "model": "tela"},
        {"id": "logica", "nome": "Lógica", "quando": "", "provider": "", "model": ""},  # sem modelo
    ])
    def rota(*a):
        return subagents.rota(*a)[0]
    grande = ["a", "b", "c"]
    assert rota("rapido", {"type": "ui"}) == "rapido"                       # escolha da Maestro manda
    assert rota(None, {"type": "ui"}) == "frontend"
    assert rota(None, {"relevant_files": [".forja/knowledge/frontend.md", "src/App.tsx", "style.css"]}) == "frontend"
    assert rota(None, {"relevant_files": ["src/App.tsx", "api.py"], "requirements": grande}) == "capaz"
    assert rota(None, {"type": "bugfix", "requirements": grande}) == "capaz"  # logica sem modelo
    assert [lvl for lvl, _ in subagents.chain("frontend")][:2] == ["frontend", "capaz"]
    assert subagents.nome_do_nivel("frontend") == "Frontend"
    assert "frontend = Frontend" in agent.system_prompt("native", set(), permission="auto", maestro_mode=True)
    assert "logica =" not in agent.system_prompt("native", set(), permission="auto", maestro_mode=True)


def test_contrato_guarda_tipo_valido_e_aceita_especialidade(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKER_ESPECIALIDADES", [{"id": "frontend", "nome": "F", "quando": "", "provider": "", "model": ""}])
    assert taskdb.normalize_contract({"goal": "x", "type": "BUGFIX"})["type"] == "bugfix"
    assert "type" not in taskdb.normalize_contract({"goal": "x", "type": "qualquer"})
    assert taskdb._slot_valido("Frontend") == "frontend"
    with pytest.raises(ToolError, match="frontend"):
        taskdb._slot_valido("design")


@pytest.mark.parametrize("cmd,seguro", [
    ('python -m pytest -q -k "a or b"', True),
    ("Get-Date -Format 'yyyy-MM-dd'", True),
    ('Select-String -Path "x.html" -Pattern "footer|contentinfo" | Select-Object -First 30', True),
    ("where.exe tesseract; python --version; pip --version", True),
    ('Test-Path .\index.html', True),
    ('git status; git diff', True),
    ('python -c "import os; os.remove(\'x\')"', False),       # código arbitrário continua perguntando
    ("python check.py", False),
    ('Get-Content a.txt | Set-Content b.txt', False),
    ('echo "a;b" > saida.txt', False),
    ('git status; Remove-Item -Recurse C:/', False),
    ('echo "$(rm -rf x)"', False),
])
def test_modo_automatico_libera_leitura_e_respeita_aspas(cmd, seguro):
    from app import policy
    assert policy.safe_command(cmd) is seguro


def test_regra_de_comando_nao_vale_para_o_que_vem_grudado():
    from app import policy
    assert policy.chained('pytest -q; Remove-Item -Recurse C:/')
    assert not policy.chained('python -m pytest -k "a; b"')



def test_roteador_escala_depois_de_falha_e_pelo_historico_do_projeto(tmp_path, monkeypatch):
    """Sem escolha da Maestro: tarefa pequena vai ao rápido; falhou nele, a próxima sobe; nível que vai
    mal no projeto sai do automático."""
    from app import subagents
    monkeypatch.setattr(config, "SUBAGENTS", {"rapido": {"provider": "x", "model": "p"}, "capaz": {"provider": "x", "model": "g"}})
    monkeypatch.setattr(config, "WORKER_ESPECIALIDADES", [])
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    with db.session() as s:
        c = db.Conversation(title="r", kind="maestro", workspace=str(tmp_path))
        s.add(c)
        s.commit()
        conv = c.id
    taskdb.create_feature(conv, "F", "", [{"title": "Pequena", "contract": {"type": "chore", "goal": "g", "relevant_files": ["a.py"]}}])
    t = taskdb.get("TASK-001", conv)
    assert subagents.rota(None, t.contract, maestro.evitar_niveis(t, tmp_path))[0] == "rapido"
    aid = taskdb.new_attempt("TASK-001", {"level": "rapido"}, "", conv)
    taskdb.finish_attempt(aid, "failed", {}, error="quebrou")
    nivel, motivo = subagents.rota(None, t.contract, maestro.evitar_niveis(taskdb.get("TASK-001", conv), tmp_path))
    assert nivel == "capaz" and "falhou na tentativa 1" in motivo
    # outra tarefa pequena do mesmo projeto: 1 tentativa só ainda não pesa; com 3 falhas, pesa
    taskdb.create_feature(conv, "G", "", [{"title": "Outra", "contract": {"type": "chore", "goal": "h", "relevant_files": ["b.py"]}},
                                          {"title": "Mais", "contract": {"type": "chore", "goal": "i", "relevant_files": ["c.py"]}},
                                          {"title": "Nova", "contract": {"type": "chore", "goal": "j", "relevant_files": ["d.py"]}}])
    t2 = taskdb.get("TASK-002", conv)
    assert subagents.rota(None, t2.contract, maestro.evitar_niveis(t2, tmp_path))[0] == "rapido"
    for code in ("TASK-002", "TASK-003"):
        taskdb.finish_attempt(taskdb.new_attempt(code, {"level": "rapido"}, "", conv), "failed", {}, error="x")
    t4 = taskdb.get("TASK-004", conv)
    evitar = maestro.evitar_niveis(t4, str(tmp_path).replace("\\", "/"))  # caminho escrito diferente
    assert "0/3" in evitar["rapido"]



def test_modo_paralelo_manda_despachar_junto(monkeypatch):
    monkeypatch.setattr(config, "MAX_WORKERS", 1)
    assert "Modo paralelo" not in agent.system_prompt("native", set(), permission="auto", maestro_mode=True)
    monkeypatch.setattr(config, "MAX_WORKERS", 3)
    assert "até 3 Workers" in agent.system_prompt("native", set(), permission="auto", maestro_mode=True)


def test_run_task_com_codes_vira_uma_chamada_por_tarefa_e_roda_junto(monkeypatch):
    calls = [{"id": "c1", "name": "run_task", "arguments": {"codes": ["TASK-001", "task-002", "TASK-001"], "strategy": "s"}},
             {"id": "c2", "name": "list_tasks", "arguments": {}}]
    out = agent.expande_run_task(calls)
    assert [(c["id"], c["arguments"]) for c in out[:2]] == [
        ("c1", {"strategy": "s", "code": "TASK-001"}), ("c1_1", {"strategy": "s", "code": "TASK-002"})]
    assert out[2]["name"] == "list_tasks"
    assert agent.expande_run_task([{"id": "x", "name": "run_task", "arguments": {"code": "TASK-009"}}])[0]["arguments"] == {"code": "TASK-009"}
    monkeypatch.setattr(config, "MAX_WORKERS", 2)
    assert [len(l) for l in agent.batches(out[:2])] == [2]  # no modo paralelo, um lote só


def test_verificacao_com_pytest_fora_do_path_usa_python_m(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda nome: None)
    assert subagents.sem_path("pytest -q test_x.py") == "python -m pytest -q test_x.py"
    assert subagents.sem_path("npm test") == "npm test"
    monkeypatch.setattr(shutil, "which", lambda nome: "C:/py/Scripts/pytest.exe")
    assert subagents.sem_path("pytest -q") == "pytest -q"


def test_maestro_fecha_tarefa_que_devolveu_para_a_fila_depois_de_conferir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    with db.session() as s:
        c = db.Conversation(title="r", kind="maestro", workspace=str(tmp_path))
        s.add(c)
        s.commit()
        conv = c.id
    taskdb.create_feature(conv, "F", "", [{"title": "A", "contract": {"goal": "a"}}, {"title": "B", "contract": {"goal": "b"}}])
    with pytest.raises(ToolError, match="não pode ir para 'completed'"):
        taskdb.set_status("TASK-002", "completed", conv)  # nunca rodou: não dá para fechar
    taskdb.finish_attempt(taskdb.new_attempt("TASK-001", {"level": "capaz"}, "", conv), "failed", {}, error="verify")
    taskdb.set_status("TASK-001", "queued", conv)
    taskdb.set_status("TASK-001", "pending", conv)
    assert taskdb.set_status("TASK-001", "completed", conv)["status"] == "completed"


def test_workers_usam_o_modelo_da_maestro_com_o_interruptor(conv, monkeypatch):
    """Ligado: o Worker roda no modelo da Maestro (nada de trocar de modelo por um especialista)."""
    monkeypatch.setattr(llm, "chat_stream", _fala())
    monkeypatch.setattr(config, "WORKERS_DO_MAESTRO", True)
    _plano(conv)
    out, _ = _despacha(conv, "TASK-001")
    r = out["meta"]["task_result"]
    assert r["model"] == "maestro-32b" and "modelo da Maestro" in r["route"]
    tent = taskdb.detail(conv, "TASK-001")["attempts"][0]
    assert tent["worker"]["model"] == "maestro-32b"


def test_escrita_da_maestro_diz_no_schema_que_e_so_memoria():
    from app import agent
    nomes = {t.name: t for t in agent.available_tools(None, "auto", maestro_mode=True)}
    assert nomes["write_file"].description.startswith("SÓ para FORJA.md")
    assert nomes["edit_file"].description.startswith("SÓ para FORJA.md")
    comum = {t.name: t for t in agent.available_tools(None, "auto")}
    assert not comum["write_file"].description.startswith("SÓ")   # o chat/agente não muda


def test_guia_visual_nao_trava_tarefas_paralelas():
    from app import maestro
    assert maestro.para_travar([".forja/knowledge/frontend.md", "src/app.js", "./.forja/x.md", "forja/a.py"]) \
        == ["src/app.js", "forja/a.py"]


def test_escrita_fora_do_contrato_vira_aviso():
    from app import maestro
    mud = [{"path": "src/app.js"}, {"path": "src/style.css"}, {"path": "index.html"}]
    assert maestro.fora_do_contrato(mud, [".forja/knowledge/frontend.md", "src/app.js"]) == ["src/style.css", "index.html"]
    assert maestro.fora_do_contrato(mud, [".forja/knowledge/frontend.md"]) == []   # só o guia: sem declaração
    r = {"status": "unverified", "outside_contract": ["src/style.css"]}
    assert "fora do contrato (src/style.css)" in maestro._para_o_maestro(r)

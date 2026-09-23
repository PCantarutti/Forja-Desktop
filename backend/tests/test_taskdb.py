"""Task Manager do Maestro: contrato, máquina de estados, dependências e tentativas."""
import pytest

from app import db, taskdb
from app.tools import ToolError


@pytest.fixture
def conv():
    """Uma conversa Maestro nova por teste, com o contextvar apontado para ela."""
    with db.session() as s:
        c = db.Conversation(title="Maestro", kind="maestro")
        s.add(c)
        s.commit()
        conv_id = c.id
    token = taskdb.CONV.set(conv_id)
    yield conv_id
    taskdb.CONV.reset(token)


def _plano(conv_id, **kw):
    tarefas = kw.pop("tasks", None) or [
        {"title": "Modelo", "contract": {"goal": "Criar o modelo User"}},
        {"title": "Rota", "contract": {"goal": "Criar POST /login"}, "depends_on": ["1"]},
    ]
    return taskdb.create_feature(conv_id, kw.pop("title", "Autenticação"), kw.pop("goal", "Login"), tarefas)


# ------------------------------------------------------------------ contrato

def test_contrato_exige_goal():
    with pytest.raises(ToolError, match="goal"):
        taskdb.normalize_contract({"context": "algo"})


def test_contrato_aceita_lista_em_texto():
    """Modelo pequeno manda 'a.py, b.py' em vez de lista."""
    c = taskdb.normalize_contract({"goal": "x", "relevant_files": "a.py, b.py"})
    assert c["relevant_files"] == ["a.py", "b.py"]


def test_contrato_descarta_campos_vazios():
    c = taskdb.normalize_contract({"goal": "x", "context": "  ", "do_not": []})
    assert c == {"goal": "x"}


def test_render_contract_tem_todas_as_secoes(conv):
    _plano(conv, tasks=[{"title": "Filtro", "contract": {
        "context": "A tela lista tudo", "goal": "Filtrar por categoria",
        "requirements": ["Select acima da lista"], "constraints": ["Usar useProducts"],
        "do_not": ["alterar a API"], "acceptance_criteria": ["Bebidas mostra só bebidas"],
        "expected_result": "Filtro funcionando"}}])
    texto = taskdb.render_contract(taskdb.get("TASK-001"))
    for secao in ("TAREFA TASK-001", "CONTEXTO", "OBJETIVO", "REQUISITOS", "RESTRIÇÕES",
                  "NÃO FAÇA", "CRITÉRIOS DE ACEITAÇÃO", "RESULTADO ESPERADO"):
        assert secao in texto
    assert "1. Select acima da lista" in texto
    assert "- alterar a API" in texto


def test_render_contract_carrega_erro_e_estrategia(conv):
    _plano(conv)
    texto = taskdb.render_contract(taskdb.get("TASK-001"), erro_anterior="pytest falhou",
                                   strategy="use regex em vez de split")
    assert "A TENTATIVA ANTERIOR FALHOU" in texto and "pytest falhou" in texto
    assert "MUDE A ABORDAGEM" in texto and "regex" in texto


# ------------------------------------------------------------------ criação

def test_plan_feature_cria_tarefas_com_codigo_sequencial(conv):
    out = _plano(conv)
    assert [t["code"] for t in out["tasks"]] == ["TASK-001", "TASK-002"]


def test_depends_on_por_posicao_vira_codigo(conv):
    """O modelo escreve o plano antes de ver os códigos, então '1' vale por TASK-001."""
    out = _plano(conv)
    assert out["tasks"][1]["depends_on"] == ["TASK-001"]


def test_depends_on_aceita_codigo_direto(conv):
    out = _plano(conv, tasks=[
        {"title": "A", "contract": {"goal": "a"}},
        {"title": "B", "contract": {"goal": "b"}, "depends_on": ["TASK-001"]}])
    assert out["tasks"][1]["depends_on"] == ["TASK-001"]


def test_dependencia_inexistente_e_erro(conv):
    with pytest.raises(ToolError, match="não existe"):
        _plano(conv, tasks=[{"title": "A", "contract": {"goal": "a"}, "depends_on": ["TASK-099"]}])


def test_model_slot_invalido_e_erro(conv):
    with pytest.raises(ToolError, match="model_slot"):
        _plano(conv, tasks=[{"contract": {"goal": "a"}, "model_slot": "gigante"}])


def test_titulo_da_feature_sai_do_objetivo_quando_falta(conv):
    """Os modelos esquecem o 'title' na primeira chamada; recusar custava uma rodada inteira."""
    out = taskdb.create_feature(conv, "", "Autenticação com JWT em cookie httpOnly",
                                [{"contract": {"goal": "a"}}])
    assert out["title"] == "Autenticação com JWT em cookie httpOnly"


def test_titulo_da_feature_sai_da_primeira_tarefa_sem_objetivo(conv):
    out = taskdb.create_feature(conv, None, "", [{"title": "Modelo User", "contract": {"goal": "a"}}])
    assert out["title"] == "Modelo User"


def test_plan_feature_so_exige_as_tarefas():
    assert taskdb.PLAN_FEATURE.parameters["required"] == ["tasks"]


def test_feature_sem_tarefas_e_erro(conv):
    with pytest.raises(ToolError, match="tasks"):
        taskdb.create_feature(conv, "X", "y", [])


def test_titulo_sai_do_goal_quando_ausente(conv):
    _plano(conv, tasks=[{"contract": {"goal": "Criar o modelo User"}}])
    assert taskdb.get("TASK-001").title == "Criar o modelo User"


# ------------------------------------------------------------------ máquina de estados

def test_transicao_legal(conv):
    _plano(conv)
    taskdb.set_status("TASK-001", "queued")
    taskdb.set_status("TASK-001", "implementing")
    taskdb.set_status("TASK-001", "testing")
    assert taskdb.set_status("TASK-001", "reviewing")["status"] == "reviewing"


def test_transicao_ilegal_e_recusada(conv):
    _plano(conv)
    with pytest.raises(ToolError, match="não pode ir para"):
        taskdb.set_status("TASK-001", "completed")  # pending -> completed pula a verificação


def test_cancelar_vale_de_qualquer_estado(conv):
    _plano(conv)
    taskdb.set_status("TASK-001", "queued")
    taskdb.set_status("TASK-001", "implementing")
    assert taskdb.set_status("TASK-001", "cancelled")["status"] == "cancelled"


def test_needs_human_vale_de_qualquer_estado(conv):
    _plano(conv)
    assert taskdb.set_status("TASK-001", "needs_human", reason="ambíguo")["status"] == "needs_human"
    assert taskdb.get("TASK-001").blocked_reason == "ambíguo"


def test_motivo_some_ao_reabrir(conv):
    _plano(conv)
    taskdb.set_status("TASK-001", "blocked", reason="faltou decisão")
    taskdb.set_status("TASK-001", "queued")
    assert taskdb.get("TASK-001").blocked_reason is None


def test_status_desconhecido_e_erro(conv):
    _plano(conv)
    with pytest.raises(ToolError, match="Status inválido"):
        taskdb.set_status("TASK-001", "quase_pronto")


def test_tarefa_inexistente_e_erro(conv):
    with pytest.raises(ToolError, match="não existe"):
        taskdb.set_status("TASK-404", "queued")


# ------------------------------------------------------------------ dependências

def test_unmet_deps_bloqueia_ate_a_anterior_concluir(conv):
    _plano(conv)
    assert taskdb.unmet_deps(taskdb.get("TASK-002")) == ["TASK-001"]
    _concluir(conv, "TASK-001")
    assert taskdb.unmet_deps(taskdb.get("TASK-002")) == []


def _concluir(conv_id, code):
    for st in ("queued", "implementing", "testing", "reviewing", "completed"):
        taskdb.set_status(code, st, conv_id)


def test_ultima_tarefa_leva_a_feature_para_validacao_nao_para_done(conv):
    _plano(conv)
    _concluir(conv, "TASK-001")
    assert taskdb.board(conv)["features"][0]["status"] == "active"
    _concluir(conv, "TASK-002")
    assert taskdb.board(conv)["features"][0]["status"] == "validating"  # quem encerra é a Maestro, depois de validar a entrega


# ------------------------------------------------------------------ tentativas

def test_new_attempt_numera_e_conta(conv):
    _plano(conv)
    a1 = taskdb.new_attempt("TASK-001", {"model": "coder-14b"})
    a2 = taskdb.new_attempt("TASK-001", {"model": "coder-14b"}, strategy="outra abordagem")
    assert taskdb.get("TASK-001").attempt_count == 2
    tent = taskdb.detail(conv, "TASK-001")["attempts"]
    assert [t["n"] for t in tent] == [1, 2]
    assert tent[1]["strategy"] == "outra abordagem"
    assert a1 != a2


def test_tentativa_nasce_running(conv):
    """Gravada antes do Worker rodar: é o que permite reap() achar o que estava em voo."""
    _plano(conv)
    taskdb.new_attempt("TASK-001", {})
    assert taskdb.detail(conv, "TASK-001")["attempts"][0]["status"] == "running"


def test_finish_attempt_grava_resultado_na_tarefa(conv):
    _plano(conv)
    aid = taskdb.new_attempt("TASK-001", {})
    resultado = {"type": "task_result", "status": "completed", "summary": "feito"}
    taskdb.finish_attempt(aid, "completed", resultado, seconds=12.34, tokens=500)
    assert taskdb.get("TASK-001").result == resultado
    att = taskdb.detail(conv, "TASK-001")["attempts"][0]
    assert (att["status"], att["seconds"], att["tokens"]) == ("completed", 12.3, 500)


def test_last_error_traz_a_falha_do_comando(conv):
    _plano(conv)
    aid = taskdb.new_attempt("TASK-001", {})
    taskdb.finish_attempt(aid, "failed", {"tests": {"command": "pytest -q", "status": "erro",
                                                    "output": "2 failed"}, "errors": ["assert 1 == 2"]})
    erro = taskdb.last_error("TASK-001")
    assert "pytest -q" in erro and "2 failed" in erro and "assert 1 == 2" in erro


def test_last_error_calado_quando_a_ultima_passou(conv):
    _plano(conv)
    taskdb.finish_attempt(taskdb.new_attempt("TASK-001", {}), "completed", {"summary": "ok"})
    assert taskdb.last_error("TASK-001") == ""


def test_reap_devolve_tentativa_orfa_para_queued(conv):
    """App fechado no meio: a tarefa volta para queued, não para failed — houve queda, não bug."""
    _plano(conv)
    taskdb.set_status("TASK-001", "queued")
    taskdb.set_status("TASK-001", "implementing")
    taskdb.new_attempt("TASK-001", {})
    assert taskdb.reap() >= 1  # varredura global: pode achar órfãs de outros testes também
    assert taskdb.get("TASK-001").status == "queued"
    att = taskdb.detail(conv, "TASK-001")["attempts"][0]
    assert att["status"] == "error" and "encerrado" in att["error"]
    assert taskdb.reap() == 0  # idempotente: nada mais ficou running


# ------------------------------------------------------------------ leitura e ferramentas

def test_board_agrupa_e_conta(conv):
    _plano(conv)
    _concluir(conv, "TASK-001")
    b = taskdb.board(conv)
    assert b["total"] == 2 and b["done"] == 1 and b["open"] == 1
    assert len(b["features"][0]["tasks"]) == 2
    assert b["counts"]["completed"] == 1


def test_board_vazio(conv):
    assert taskdb.board(conv) == {"inicio": None, "ultima": None, "features": [], "counts": {}, "total": 0,
                                  "done": 0, "open": 0}


def test_list_tasks_mostra_dependencia_e_tentativa(conv):
    _plano(conv)
    taskdb.new_attempt("TASK-002", {})
    texto = taskdb._list_tasks(None, {})
    assert "TASK-001" in texto and "depende de TASK-001" in texto and "tentativa 1/5" in texto


def test_list_tasks_filtra_por_status(conv):
    _plano(conv)
    _concluir(conv, "TASK-001")
    texto = taskdb._list_tasks(None, {"status": "completed"})
    assert "TASK-001" in texto and "TASK-002" not in texto


def test_update_task_muda_contrato_e_modelo(conv):
    _plano(conv)
    taskdb._update_task(None, {"code": "TASK-001", "model_slot": "capaz",
                               "contract": {"goal": "novo objetivo"}})
    t = taskdb.get("TASK-001")
    assert t.model_slot == "capaz" and t.contract["goal"] == "novo objetivo"


def test_update_task_sem_nada_para_mudar_e_erro(conv):
    _plano(conv)
    with pytest.raises(ToolError, match="Nada para mudar"):
        taskdb._update_task(None, {"code": "TASK-001"})


def test_ferramenta_fora_de_conversa_maestro_e_erro():
    token = taskdb.CONV.set(None)
    try:
        with pytest.raises(ToolError, match="fora de uma conversa"):
            taskdb._list_tasks(None, {})
    finally:
        taskdb.CONV.reset(token)


def test_sink_publica_a_arvore(conv):
    recebido = []
    token = taskdb.SINK.set(recebido.append)
    try:
        _plano(conv)
        taskdb.set_status("TASK-001", "queued")
    finally:
        taskdb.SINK.reset(token)
    assert len(recebido) == 2 and recebido[-1]["counts"]["queued"] == 1


def test_sink_que_explode_nao_derruba_a_tarefa(conv):
    """Publicar é o caminho rápido; o /board por polling é o que sempre funciona."""
    def explode(_):
        raise RuntimeError("UI caiu")

    token = taskdb.SINK.set(explode)
    try:
        _plano(conv)
    finally:
        taskdb.SINK.reset(token)
    assert taskdb.board(conv)["total"] == 2


def test_apagar_conversa_leva_tarefas_e_tentativas(conv):
    _plano(conv)
    taskdb.new_attempt("TASK-001", {})
    with db.session() as s:
        ids = [i for (i,) in s.query(db.Task.id).filter(db.Task.conversation_id == conv)]
        assert s.query(db.Attempt).filter(db.Attempt.task_id.in_(ids)).count() == 1
        s.delete(s.get(db.Conversation, conv))
        s.commit()
        assert s.query(db.Task).filter(db.Task.conversation_id == conv).count() == 0
        assert s.query(db.Feature).filter(db.Feature.conversation_id == conv).count() == 0
        assert s.query(db.Attempt).filter(db.Attempt.task_id.in_(ids)).count() == 0


def test_run_task_e_declarada_mas_nao_executavel_aqui():
    """Quem executa é o loop do agente (maestro.run_task), como o delegate_task."""
    assert taskdb.RUN_TASK.poll and not taskdb.RUN_TASK.mutating
    with pytest.raises(RuntimeError, match="loop do agente"):
        taskdb.RUN_TASK.handler(None, {})


def test_toda_tentativa_recomeca_o_ciclo(conv):
    """O Maestro que não aprovou o resultado redespacha: reviewing volta para queued."""
    _plano(conv)
    for st in ("queued", "implementing", "testing", "reviewing"):
        taskdb.set_status("TASK-001", st, conv)
    assert taskdb.set_status("TASK-001", "queued", conv)["status"] == "queued"
    # e o caminho do modelo continua valendo a partir daí
    taskdb.set_status("TASK-001", "loading_model", conv)
    assert taskdb.set_status("TASK-001", "implementing", conv)["status"] == "implementing"
    assert taskdb.set_status("TASK-001", "queued", conv)["status"] == "queued"


def test_maestro_fecha_direto_uma_tarefa_que_falhou(conv):
    """Ela conferiu e aceitou: antes precisava percorrer a máquina à mão (10 chamadas numa execução real)."""
    _plano(conv)
    for st in ("queued", "implementing", "failed", "completed"):
        taskdb.set_status("TASK-001", st, conv)
    assert taskdb.get("TASK-001", conv).status == "completed"


def test_reabrir_tarefa_devolve_a_feature_para_active(conv):
    """Reenviada pelo usuário depois de concluída: a entrega muda, e a validação tem de acontecer de novo."""
    _plano(conv)
    _concluir(conv, "TASK-001")
    _concluir(conv, "TASK-002")
    assert taskdb.board(conv)["features"][0]["status"] == "validating"
    taskdb.set_status("TASK-001", "queued", conv)
    assert taskdb.board(conv)["features"][0]["status"] == "active"
    _concluir(conv, "TASK-001")
    assert taskdb.board(conv)["features"][0]["status"] == "validating"



def test_copia_exata_no_plano_sai_e_dependencia_remapeia():
    """TaskBoard: a Maestro mandou a TASK-004 duas vezes no mesmo plano."""
    dados = {"title": "Dados", "contract": {"goal": "CRUD"}}
    tarefas = [{"title": "Base", "contract": {"goal": "HTML"}}, dict(dados),
               {"title": "Tela", "contract": {"goal": "render"}, "depends_on": ["3"]}, dict(dados)]
    tarefas[2]["depends_on"] = ["4", "1"]   # aponta para a cópia
    limpas, copias = taskdb.sem_copias(tarefas)
    assert copias == 1 and [t["title"] for t in limpas] == ["Base", "Dados", "Tela"]
    assert limpas[2]["depends_on"] == ["2", "1"]
    parecidas = [{"title": "Tela de login", "contract": {"goal": "a"}},
                 {"title": "Tela de logout", "contract": {"goal": "a"}}]
    assert taskdb.sem_copias(parecidas)[1] == 0   # parecido não é cópia


def test_funcionalidade_sem_titulo_leva_o_pedido_do_usuario(conv):
    with db.session() as s:
        s.add(db.Message(conversation_id=conv, role="user",
                         content="\nCrie uma aplicação chamada **TaskBoard**.\n\nDetalhes..."))
        s.commit()
    assert taskdb._objetivo_da_conversa() == "Crie uma aplicação chamada TaskBoard"


def test_brief_traz_escopo_e_recado_sem_falha(conv):
    _plano(conv, tasks=[{"title": "Dados", "contract": {"goal": "CRUD", "relevant_files": [".forja/knowledge/frontend.md", "src/app.js"]}}])
    texto = taskdb.render_contract(taskdb.get("TASK-001"), strategy="corrija também os IDs list-* do HTML")
    assert "ORIENTAÇÃO DA MAESTRO" in texto and "MUDE A ABORDAGEM" not in texto
    assert "Mexa só em: src/app.js." in texto


def test_board_traz_relogio_da_conversa(conv):
    with db.session() as s:
        s.add(db.Message(conversation_id=conv, role="user", content="Crie o TaskBoard"))
        s.commit()
    _plano(conv)
    b = taskdb.board(conv)
    assert b["inicio"].endswith("Z") and b["ultima"].endswith("Z") and b["ultima"] >= b["inicio"]


def test_cancelada_nao_conta_no_total(conv):
    """"10/11 tarefas" com uma cancelada parecia trabalho faltando."""
    _plano(conv)
    taskdb.set_status("TASK-002", "cancelled", conv)
    b = taskdb.board(conv)
    assert b["total"] == 1 and len(b["features"][0]["tasks"]) == 2
    taskdb.set_status("TASK-001", "cancelled", conv)
    assert taskdb.board(conv)["total"] == 0 and taskdb.board(conv)["features"]   # a árvore continua lá

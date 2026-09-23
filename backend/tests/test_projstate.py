"""Project State da Maestro (.forja/): sessões, espelho das tarefas, prompt e validação da entrega."""
import json

import pytest

from app import agent, db, projstate, taskdb, workspace
from app.tools import ToolError


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    return tmp_path


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


def test_nota_segue_o_formato_de_sessao(pasta):
    nome = projstate.write(pasta, None, "Implementar autenticação", "Login implementado",
                          ["JWT armazenado em httpOnly cookie"], ["Refresh token falha no Safari"],
                          "Testar renovação do token")
    assert nome == "SESSION-001.md"
    texto = (pasta / ".forja/sessions/SESSION-001.md").read_text("utf-8")
    for trecho in ("# SESSION-001", "## Objetivo", "Implementar autenticação", "## Decisões",
                   "- JWT armazenado em httpOnly cookie", "## Problemas", "Safari",
                   "## Próximo passo", "Testar renovação do token"):
        assert trecho in texto


def test_numera_em_sequencia_e_nunca_sobrescreve(pasta):
    assert projstate.write(pasta, None, "a", "", [], [], "") == "SESSION-001.md"
    assert projstate.write(pasta, None, "b", "", [], [], "") == "SESSION-002.md"
    assert "## Objetivo\na" in (pasta / ".forja/sessions/SESSION-001.md").read_text("utf-8")


def test_objetivo_e_obrigatorio(pasta):
    with pytest.raises(ToolError, match="objective"):
        projstate.write(pasta, None, "", "", [], [], "")


def test_tarefas_em_aberto_entram_sozinhas(pasta, conv):
    """A tarefa é desta conversa: uma sessão nova, em outra conversa, não a veria pelo list_tasks."""
    taskdb.create_feature(conv, "Auth", "Login", [
        {"title": "Modelo", "contract": {"goal": "User"}},
        {"title": "Rota", "contract": {"goal": "POST /login"}}])
    taskdb.set_status("TASK-002", "blocked", conv, reason="falta decidir o hash")
    projstate.write(pasta, conv, "Auth", "", [], [], "")
    texto = (pasta / ".forja/sessions/SESSION-001.md").read_text("utf-8")
    assert "TASK-001 [pending] Modelo" in texto
    assert "TASK-002 [blocked] Rota — falta decidir o hash" in texto


def test_tarefa_concluida_nao_polui_a_nota(pasta, conv):
    taskdb.create_feature(conv, "X", "", [{"title": "Feita", "contract": {"goal": "g"}}])
    for st in ("queued", "implementing", "testing", "reviewing", "completed"):
        taskdb.set_status("TASK-001", st, conv)
    projstate.write(pasta, conv, "X", "", [], [], "")
    assert "Feita" not in (pasta / ".forja/sessions/SESSION-001.md").read_text("utf-8")


def test_latest_pega_a_mais_recente(pasta):
    assert projstate.latest(pasta) is None
    projstate.write(pasta, None, "primeira", "", [], [], "")
    projstate.write(pasta, None, "segunda", "", [], [], "")
    nome, texto = projstate.latest(pasta)
    assert nome == "SESSION-002.md" and "segunda" in texto


def test_arquivo_estranho_na_pasta_e_ignorado(pasta):
    (pasta / ".forja/sessions").mkdir(parents=True)
    (pasta / ".forja/sessions/rascunho.md").write_text("x", "utf-8")
    (pasta / ".forja/sessions/SESSION-abc.md").write_text("x", "utf-8")
    assert projstate.write(pasta, None, "ok", "", [], [], "") == "SESSION-001.md"


def test_prompt_da_maestro_carrega_a_ultima_nota(pasta):
    """É isto que dá continuidade: uma conversa nova na pasta começa sabendo onde a outra parou."""
    projstate.write(pasta, None, "Auth", "", ["usar bcrypt, não sha256"], [], "rota de refresh")
    p = agent.system_prompt("native", set(), permission="auto", maestro_mode=True)
    assert "onde a última sessão parou" in p and "usar bcrypt, não sha256" in p
    # e o agente comum não carrega nota de Maestro
    assert "onde a última sessão parou" not in agent.system_prompt("native", set(), permission="auto")


def test_sem_nota_o_prompt_nao_muda(pasta):
    assert projstate.prompt_block(pasta) == ""


def test_nota_gigante_e_cortada_no_prompt(pasta):
    """A nota entra em toda requisição: sem teto, ela comeria a janela que a compactação libera."""
    projstate.write(pasta, None, "x" * 1000, "y" * 1000, ["d" * 1000] * 10, [], "")
    bloco = projstate.prompt_block(pasta)
    assert len(bloco) < sum(projstate.TETO.values()) + 1200 and "cortado; leia .forja/sessions/SESSION-001.md" in bloco


def test_ferramenta_grava_na_pasta_da_conversa(pasta, conv):
    texto = projstate.SESSION_NOTE.handler(None, {"objective": "Fechar login", "decisions": ["JWT"]})
    assert "SESSION-001.md" in texto
    assert (pasta / ".forja/sessions/SESSION-001.md").is_file()


def test_session_note_nao_para_a_execucao_autonoma():
    """Marcada como mutante, pediria aprovação a cada nota."""
    from app import policy
    precisa, _ = policy.decide(projstate.SESSION_NOTE, {"objective": "x"}, "auto")
    assert not precisa


# ------------------------------------------------------------------ Project State (.forja/)

@pytest.fixture
def projeto(tmp_path, monkeypatch):
    """Conversa Maestro com pasta de trabalho própria e o esqueleto de .forja/ criado."""
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    with db.session() as s:
        c = db.Conversation(title="Maestro", kind="maestro", workspace=str(tmp_path))
        s.add(c)
        s.commit()
        conv_id = c.id
    token = taskdb.CONV.set(conv_id)
    projstate.ensure(tmp_path)
    yield tmp_path, conv_id
    taskdb.CONV.reset(token)
    projstate.BLOCO.set(None)


def _conclui(code, conv):
    for st in ("queued", "implementing", "testing", "reviewing", "completed"):
        taskdb.set_status(code, st, conv)


def test_sem_esqueleto_vazio(tmp_path):
    """Arquivo de esqueleto era lido um a um pelo modelo, à toa: a pasta nasce vazia."""
    (tmp_path / "FORJA.md").write_text("# Projeto\nAPI de estoque em FastAPI\n", "utf-8")
    projstate.ensure(tmp_path)
    assert (tmp_path / ".forja").is_dir() and not any((tmp_path / ".forja").iterdir())
    assert "API de estoque" in (tmp_path / "FORJA.md").read_text("utf-8")


def test_so_o_modo_maestro_cria_a_pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    agent.system_prompt("native", set(), permission="auto")
    agent.system_prompt("native", set(), permission="auto", maestro_mode=True)
    assert not (tmp_path / ".forja").exists()  # prompt lê, não cria: quem cria é o congelar da execução


def test_projeto_sem_estado_pede_a_investigacao(projeto):
    root, _ = projeto
    bloco = projstate.prompt_block(root)
    assert "PROJETO AINDA SEM ESTADO" in bloco
    # esqueleto não gasta token: nada de arquivo vazio no índice
    assert "architecture.md (" not in bloco and "knowledge/" in bloco


def test_prompt_leva_o_essencial_e_indexa_o_resto(projeto):
    root, _ = projeto
    (root / "FORJA.md").write_text("# Projeto\nEstoque. Testes: pytest -q\n", "utf-8")
    (root / ".forja/architecture.md").write_text("# Arquitetura\nFastAPI + SQLite\n", "utf-8")
    bloco = projstate.prompt_block(root)
    assert "PROJETO AINDA SEM ESTADO" not in bloco
    # FORJA.md já entra pela memória do projeto: aqui seria o mesmo texto duas vezes no contexto
    assert "Estoque. Testes: pytest -q" not in bloco
    assert "Estoque. Testes: pytest -q" in agent.system_prompt("native", set(), permission="auto", maestro_mode=True)
    assert "architecture.md (" in bloco and "FastAPI + SQLite" not in bloco  # só o índice
    assert "knowledge/backend.md" not in bloco                                # vazio: fora


def test_progresso_e_tasks_json_espelham_o_banco(projeto):
    root, conv = projeto
    taskdb.create_feature(conv, "Auth", "Login com JWT", [
        {"title": "Modelo", "contract": {"goal": "User", "verify_command": "pytest -q tests/test_user.py"}},
        {"title": "Rota", "contract": {"goal": "POST /login"}}])
    _conclui("TASK-001", conv)
    prog = (root / ".forja/progress.md").read_text("utf-8")
    assert "### Auth — em andamento" in prog and "- [x] TASK-001 Modelo" in prog and "- [ ] TASK-002 Rota" in prog
    dados = json.loads((root / ".forja/tasks.json").read_text("utf-8"))
    t1 = dados["features"][0]["tasks"][0]
    assert t1["status"] == "completed" and t1["verify_command"] == "pytest -q tests/test_user.py"
    # e o que está em andamento entra no prompt
    assert "TASK-002 Rota" in projstate.prompt_block(root)


def test_progresso_junta_as_conversas_da_mesma_pasta(projeto):
    """Uma conversa nova não vê as tarefas da anterior pelo list_tasks; pelo progress.md, vê."""
    root, conv = projeto
    taskdb.create_feature(conv, "Auth", "", [{"title": "Login", "contract": {"goal": "g"}}])
    with db.session() as s:
        c = db.Conversation(title="Maestro 2", kind="maestro", workspace=str(root))
        s.add(c)
        s.commit()
        outra = c.id
    taskdb.create_feature(outra, "Dashboard", "", [{"title": "Gráfico", "contract": {"goal": "g"}}])
    prog = (root / ".forja/progress.md").read_text("utf-8")
    assert "### Auth" in prog and "### Dashboard" in prog


def test_decisoes_da_sessao_vao_para_decisions_md(projeto):
    root, conv = projeto
    projstate.write(root, conv, "Auth", "", ["JWT em cookie httpOnly"], [], "")
    dec = (root / ".forja/decisions.md").read_text("utf-8")
    assert "JWT em cookie httpOnly (SESSION-001)" in dec
    assert "JWT em cookie httpOnly" in projstate.prompt_block(root)


def test_bloco_congelado_nao_muda_durante_a_execucao(projeto):
    """progress.md muda a cada tarefa; o system prompt não pode mudar junto (cache do llama.cpp)."""
    root, conv = projeto
    projstate.congelar(root, conv)
    antes = projstate.bloco()
    taskdb.create_feature(conv, "Nova", "", [{"title": "Coisa", "contract": {"goal": "g"}}])
    assert "Coisa" in (root / ".forja/progress.md").read_text("utf-8")
    assert projstate.bloco() == antes


# ------------------------------------------------------------------ validação da entrega

def test_ultima_tarefa_leva_a_funcionalidade_para_validacao(projeto):
    _, conv = projeto
    out = taskdb.create_feature(conv, "Auth", "Login funcionando", [
        {"title": "A", "contract": {"goal": "a", "verify_command": "pytest -q"}}])
    for st in ("queued", "implementing", "testing", "reviewing"):
        taskdb.set_status("TASK-001", st, conv)
    texto = taskdb.UPDATE_TASK.handler(None, {"code": "TASK-001", "status": "completed"})
    assert "VALIDE A ENTREGA" in texto and "`pytest -q`" in texto
    assert "Passou: session_note encerra" in texto
    assert f"plan_feature(feature_id={out['feature_id']}" in texto
    assert taskdb.board(conv)["features"][0]["status"] == "validating"


def _maestro_rodou(conv, ferramenta="run_command"):
    with db.session() as s:
        s.add(db.Message(conversation_id=conv, role="tool", name=ferramenta, content="exit code: 0"))
        s.commit()


def test_session_note_encerra_a_funcionalidade_validada(projeto):
    root, conv = projeto
    taskdb.create_feature(conv, "Auth", "", [{"title": "A", "contract": {"goal": "a"}}])
    _conclui("TASK-001", conv)
    _maestro_rodou(conv)  # a validação: a própria Maestro rodou a suíte depois do 'validating'
    texto = projstate.SESSION_NOTE.handler(None, {"objective": "Auth", "result": "suíte ok"})
    assert "funcionalidade 'Auth' encerrada" in texto
    assert taskdb.board(conv)["features"][0]["status"] == "done"
    assert "## Concluídas\n- Auth — concluída" in (root / ".forja/progress.md").read_text("utf-8")
    assert not taskdb.validando(conv)


def test_nao_encerra_sem_validar_e_nao_grava_nota(projeto):
    """Comando rodado ANTES da funcionalidade entrar em validação não conta."""
    root, conv = projeto
    _maestro_rodou(conv)
    taskdb.create_feature(conv, "Auth", "", [{"title": "A", "contract": {"goal": "a", "verify_command": "pytest -q"}}])
    _conclui("TASK-001", conv)
    with pytest.raises(ToolError, match="ainda não foi validada.*`pytest -q`"):
        projstate.SESSION_NOTE.handler(None, {"objective": "x"})
    assert projstate.latest(root) is None
    assert taskdb.board(conv)["features"][0]["status"] == "validating"


def test_nota_sem_funcionalidade_em_validacao_nao_mexe_em_nada(projeto):
    root, conv = projeto
    taskdb.create_feature(conv, "Auth", "", [{"title": "A", "contract": {"goal": "a"}}])
    assert "encerrada" not in projstate.SESSION_NOTE.handler(None, {"objective": "parar pela metade"})
    assert taskdb.board(conv)["features"][0]["status"] == "active"


def test_plan_feature_exige_o_project_state(projeto):
    root, conv = projeto
    with pytest.raises(ToolError, match="FORJA.md"):
        taskdb.PLAN_FEATURE.handler(None, {"tasks": [{"title": "A", "contract": {"goal": "a"}}]})
    (root / "FORJA.md").write_text("# Projeto\nCalculadora em Python. Testes: pytest -q\n", "utf-8")
    assert "TASK-001" in taskdb.PLAN_FEATURE.handler(None, {"tasks": [{"title": "A", "contract": {"goal": "a"}}]})


def test_correcao_entra_na_mesma_funcionalidade(projeto):
    root, conv = projeto
    (root / "FORJA.md").write_text("# Projeto\nAuth\n", "utf-8")
    out = taskdb.create_feature(conv, "Auth", "", [{"title": "A", "contract": {"goal": "a"}}])
    _conclui("TASK-001", conv)
    texto = taskdb.PLAN_FEATURE.handler(None, {"feature_id": out["feature_id"], "tasks": [
        {"title": "Corrige refresh", "contract": {"goal": "refresh no Safari"}}]})
    assert "tarefas novas" in texto
    feat = taskdb.board(conv)["features"]
    assert len(feat) == 1 and feat[0]["status"] == "active" and len(feat[0]["tasks"]) == 2


def test_memoria_do_projeto_desligada_forja_md_entra_pelo_bloco(projeto, monkeypatch):
    """Sem a memória do projeto nas Configurações, o _extra não põe o FORJA.md no prompt; o bloco põe."""
    from app import config
    root, _ = projeto
    (root / "FORJA.md").write_text("# Projeto\nEstoque\n", "utf-8")
    monkeypatch.setattr(config, "PROJECT_MEMORY", False)
    assert "--- FORJA.md ---\n# Projeto\nEstoque" in projstate.prompt_block(root)


# ------------------------------------------------------------------ arquivos gerados

def test_arquivos_gerados_nao_aceitam_escrita(projeto):
    from app import tools
    root, _ = projeto
    with pytest.raises(ToolError, match="gerado pelo Forja"):
        tools.write_file(root, {"path": ".forja/progress.md", "content": "x"})
    (root / ".forja/tasks.json").write_text("{}", "utf-8")
    with pytest.raises(ToolError, match="gerado pelo Forja"):
        tools.edit_file(root, {"path": ".forja/tasks.json", "old_str": "{}", "new_str": "[]"})
    assert "Arquivo criado" in tools.write_file(root, {"path": ".forja/architecture.md", "content": "# A\nx"})


# ------------------------------------------------------------------ trabalho de outra conversa

def _outra(root):
    with db.session() as s:
        c = db.Conversation(title="Anterior", kind="maestro", workspace=str(root))
        s.add(c)
        s.commit()
        return c.id


def test_conversa_nova_copia_o_trabalho_aberto_e_a_lista_fica(projeto):
    """Cópia, não mudança: a conversa antiga continua mostrando a lista (marcada), e a nova executa."""
    root, conv = projeto
    antiga = _outra(root)
    taskdb.create_feature(antiga, "Feita", "", [{"title": "F", "contract": {"goal": "f"}}])
    with db.session() as sess:
        sess.query(db.Feature).filter(db.Feature.conversation_id == antiga).update({"status": "done"})
        sess.commit()
    taskdb.create_feature(antiga, "Aberta", "", [
        {"title": "A", "contract": {"goal": "a"}},
        {"title": "B", "contract": {"goal": "b"}, "depends_on": ["1"]}])
    taskdb.create_feature(conv, "Daqui", "", [{"title": "X", "contract": {"goal": "x"}},
                                             {"title": "Y", "contract": {"goal": "y"}}])  # TASK-001/002
    assert projstate.congelar(root, conv) == ["Aberta"]
    feats = {f["title"]: f for f in taskdb.board(conv)["features"]}
    assert set(feats) == {"Daqui", "Aberta"}
    todos = [t["code"] for f in feats.values() for t in f["tasks"]]
    assert len(todos) == len(set(todos)) == 4                     # nenhum código repetido
    a = next(t for t in feats["Aberta"]["tasks"] if t["title"] == "A")
    b = next(t for t in feats["Aberta"]["tasks"] if t["title"] == "B")
    assert b["depends_on"] == [a["code"]]                          # dependência renumerada junto
    velhas = {f["title"]: f for f in taskdb.board(antiga)["features"]}
    assert set(velhas) == {"Feita", "Aberta"} and velhas["Aberta"]["copiada_para"] == conv
    assert len(velhas["Aberta"]["tasks"]) == 2                     # a lista continua lá
    taskdb.set_status(a["code"], "queued", conv)                   # e o run_task daqui a enxerga
    # progress.md não mostra a funcionalidade duas vezes
    assert (root / ".forja/progress.md").read_text("utf-8").count("### Aberta") == 1


def test_nao_assume_de_conversa_que_esta_rodando(projeto):
    root, conv = projeto
    antiga = _outra(root)
    taskdb.create_feature(antiga, "Em uso", "", [{"title": "A", "contract": {"goal": "a"}}])
    assert projstate.congelar(root, conv, ocupada=lambda c: c == antiga) == []
    assert taskdb.board(antiga)["total"] == 1


def test_codigo_novo_depois_de_assumir_nao_repete(projeto):
    root, conv = projeto
    antiga = _outra(root)
    taskdb.create_feature(antiga, "Aberta", "", [{"title": f"T{i}", "contract": {"goal": "g"}} for i in range(3)])
    projstate.congelar(root, conv)
    out = taskdb.create_feature(conv, "Nova", "", [{"title": "N", "contract": {"goal": "n"}}])
    assert out["tasks"][0]["code"] == "TASK-004"


# ------------------------------------------------------------------ nova sessão (botão)

def test_nova_sessao_copia_o_trabalho_e_a_antiga_nao_executa_mais(projeto):
    import asyncio

    from app import maestro
    root, conv = projeto
    taskdb.create_feature(conv, "Auth", "", [{"title": "A", "contract": {"goal": "a"}}])
    novo, copiadas = projstate.nova_sessao(conv)
    assert copiadas == ["Auth"] and taskdb.board(novo)["total"] == 1
    assert taskdb.board(conv)["features"][0]["copiada_para"] == novo  # lista fica, marcada
    assert "continuaram em outra conversa" in taskdb.LIST_TASKS.handler(None, {})
    with db.session() as sess:
        assert sess.get(db.Conversation, novo).workspace == str(root)
    run_obj = agent.Run(conv)
    out: dict = {}
    req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="maestro", permission="bypass")

    async def roda():
        async for _ in maestro.run_task(conv, {"id": "r1", "name": "run_task", "arguments": {"code": "TASK-001"}},
                                        req, run_obj, out, None):
            pass

    asyncio.run(roda())
    assert out["status"] == "erro" and f"conversa {novo}" in out["text"]


# ------------------------------------------------------------------ papel da Maestro

@pytest.mark.parametrize("path,recusa", [
    ("calc.py", "não implementa"),
    ("src/app.ts", "não implementa"),
    (".forja/FORJA.md", "na RAIZ"),
    (".forja/project.md", "na RAIZ"),
    ("FORJA.md", None),
    (".forja/architecture.md", None),
    (".forja/knowledge/backend.md", None),
])
def test_maestro_so_escreve_no_project_state(tmp_path, path, recusa):
    motivo = projstate.fora_do_papel(tmp_path, {"name": "write_file", "arguments": {"path": path, "content": "x"}})
    assert (motivo is None) if recusa is None else (recusa in motivo)


def test_leitura_e_comando_nao_passam_pelo_filtro(tmp_path):
    for call in ({"name": "read_file", "arguments": {"path": "calc.py"}},
                 {"name": "run_command", "arguments": {"command": "pytest -q"}}):
        assert projstate.fora_do_papel(tmp_path, call) is None


def test_loop_da_maestro_recusa_escrever_codigo(projeto, monkeypatch):
    import asyncio

    from app import config, llm, modelctl
    root, conv = projeto
    passos = []

    async def fake_stream(provider, model, messages, tools, *a, **k):
        passos.append(1)
        if len(passos) == 1:
            yield "done", {"tool_calls": [
                {"id": "w1", "name": "write_file", "arguments": {"path": "calc.py", "content": "x = 1"}},
                {"id": "w2", "name": "write_file", "arguments": {"path": "FORJA.md", "content": "# P\nCalc"}}],
                "prompt_tokens": 1, "completion_tokens": 1}
        else:
            yield "content", "ok"
            yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    async def fake_limit(*a):
        return 131072

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", fake_limit)
    monkeypatch.setitem(config.PROVIDERS, "local", {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    monkeypatch.setattr(modelctl, "carregado", lambda spec: True)

    async def cena():
        req = agent.RunRequest(content="faça", provider="local", model="m", mode="maestro", permission="bypass")
        return [ev async for ev in agent.run_agent(conv, req, agent.Run(conv))]

    eventos = asyncio.run(cena())
    res = {e["message"]["tool_call_id"]: e["message"] for e in eventos if e["type"] == "tool_result"}
    assert res["w1"]["status"] == "erro" and "não implementa" in res["w1"]["content"]
    assert not (root / "calc.py").exists()
    assert res["w2"]["status"] == "ok" and (root / "FORJA.md").read_text("utf-8").startswith("# P")



# ------------------------------------------------------------------ StockFlow: duplicatas e pendências

def test_plano_repetindo_tarefa_aberta_e_recusado(projeto):
    root, conv = projeto
    (root / "FORJA.md").write_text("# P\nx\n", "utf-8")
    out = taskdb.create_feature(conv, "Categorias", "", [{"title": "Listagem de Categorias", "contract": {"goal": "g"}}])
    with pytest.raises(ToolError, match="TASK-001"):
        taskdb.PLAN_FEATURE.handler(None, {"feature_id": out["feature_id"], "tasks": [
            {"title": "Listagem de categorias", "contract": {"goal": "listar"}}]})


def test_dependencia_cancelada_nao_trava(projeto):
    _, conv = projeto
    taskdb.create_feature(conv, "C", "", [{"title": "A", "contract": {"goal": "a"}},
                                          {"title": "B", "contract": {"goal": "b"}, "depends_on": ["1"]}])
    taskdb.set_status("TASK-001", "cancelled", conv)
    assert taskdb.unmet_deps(taskdb.get("TASK-002", conv), conv) == []


def test_nova_funcionalidade_lembra_o_que_ficou_para_tras(projeto):
    root, conv = projeto
    (root / "FORJA.md").write_text("# P\nx\n", "utf-8")
    taskdb.create_feature(conv, "Produtos", "", [{"title": "Seed", "contract": {"goal": "a"}},
                                                 {"title": "Seed resiliente", "contract": {"goal": "b"}}])
    for st in ("queued", "implementing", "reviewing"):
        taskdb.set_status("TASK-001", st, conv)
    texto = taskdb.PLAN_FEATURE.handler(None, {"title": "Categorias", "tasks": [{"title": "Store", "contract": {"goal": "c"}}]})
    assert "TASK-001" in texto and "espera sua revisão" in texto and "TASK-002" in texto


@pytest.mark.parametrize("cmd", ["npm run dev; browser_validate('http://localhost:5174')", "npm run dev",
                                 "vite", "serve_start(npm run dev)"])
def test_verificacao_que_nao_termina_e_recusada(cmd):
    with pytest.raises(ToolError, match="verify_command"):
        taskdb.normalize_contract({"goal": "g", "verify_command": cmd})
    assert taskdb.normalize_contract({"goal": "g", "verify_command": "npm run build"})["verify_command"] == "npm run build"


def test_run_command_com_servidor_de_desenvolvimento_manda_usar_serve_start(tmp_path):
    from app import shell
    with pytest.raises(ToolError, match="serve_start"):
        shell.run_command(tmp_path, {"command": "npm run dev"})


def test_serve_start_reaproveita_o_servidor_que_ja_roda(tmp_path):
    import sys
    from app import shell
    script = tmp_path / "srv.py"
    script.write_text("import time\nprint('Local: http://localhost:5174/', flush=True)\ntime.sleep(30)\n", "utf-8")
    cmd = f'& "{sys.executable}" "{script}"'
    try:
        primeiro = shell.serve_start(tmp_path, {"command": cmd, "name": "vite"})
        assert "http://localhost:5174" in primeiro
        segundo = shell.serve_start(tmp_path, {"command": cmd, "name": "outro"})
        assert "já está rodando" in segundo and "http://localhost:5174" in segundo
        assert "outro" not in [s["name"] for s in shell.list_servers()]
    finally:
        shell.close_all()


def test_maestro_nao_para_por_turno_mudo_avisa_e_segue(projeto, monkeypatch):
    """No StockFlow a execução morreu num único turno mudo: os lembretes tinham sido gastos 8 min antes."""
    import asyncio

    from app import config, llm, modelctl
    root, conv = projeto
    (root / "FORJA.md").write_text("# P\nx\n", "utf-8")
    passos = []

    async def stream(provider, model, messages, tools, *a, **k):
        passos.append(1)
        n = len(passos)
        if n == 3:
            yield "done", {"tool_calls": [{"id": "c1", "name": "list_tasks", "arguments": {}}],
                           "prompt_tokens": 1, "completion_tokens": 1}
            return
        if n < 10:                           # mudos: 2 no começo, 6 seguidos depois do list_tasks
            yield "reasoning", "pensando..."
        else:
            yield "content", "Pronto."
        yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    async def limite(*a):
        return 131072

    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(llm, "context_limit", limite)
    monkeypatch.setitem(config.PROVIDERS, "local", {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    monkeypatch.setattr(modelctl, "carregado", lambda spec: True)
    run = agent.Run(conv)

    async def cena():
        req = agent.RunRequest(content="faça", provider="local", model="m", mode="maestro", permission="bypass")
        return [ev async for ev in agent.run_agent(conv, req, run)]

    eventos = asyncio.run(cena())
    assert len(passos) == 10                                   # não parou: chegou à resposta final
    assert run.alertas >= 1 and any(e["type"] == "alerta" for e in eventos)
    avisos = [e["message"]["content"] for e in eventos if e["type"] == "event"
              and (e["message"].get("meta") or {}).get("kind") == "warning"]
    assert not any("mesmo após" in a for a in avisos)          # o aviso de encerramento sumiu


def test_verificacao_mencionando_ferramenta_sem_parenteses_tambem_e_recusada():
    """Numa rodada real: 'browser_validate http://localhost:8000 (necessário rodar o servidor primeiro)'."""
    with pytest.raises(ToolError, match="verify_command"):
        taskdb.normalize_contract({"goal": "g", "verify_command": "browser_validate http://localhost:8000"})


def test_erro_de_clique_distingue_elemento_sumido_de_elemento_que_nao_aceita():
    from app import browser
    existe = Exception("Timeout 5000ms exceeded.\n  - locator resolved to <button id=\"b\">+1</button>\n"
                       "  - element is outside of the viewport\n")
    assert "existe, mas não aceitou" in browser._act_err("f1e6", existe) and "viewport" in browser._act_err("f1e6", existe)
    assert "a página mudou" in browser._act_err("f1e6", Exception("Timeout 5000ms exceeded.\n  - waiting for locator"))


def test_correcao_sem_feature_id_durante_a_validacao_entra_nela(projeto):
    """TaskBoard: cada bug achado no navegador virava funcionalidade nova."""
    root, conv = projeto
    (root / "FORJA.md").write_text("# Projeto\nTaskBoard\n", "utf-8")
    out = taskdb.create_feature(conv, "TaskBoard", "", [{"title": "A", "contract": {"goal": "a"}}])
    _conclui("TASK-001", conv)
    texto = taskdb.PLAN_FEATURE.handler(None, {"tasks": [{"title": "Corrige modal", "contract": {"goal": "modal"}}]})
    assert f"funcionalidade {out['feature_id']}, que está em validação" in texto
    feat = taskdb.board(conv)["features"]
    assert len(feat) == 1 and len(feat[0]["tasks"]) == 2

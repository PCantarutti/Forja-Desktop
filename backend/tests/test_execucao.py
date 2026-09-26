"""E3 do Maestro: Worker que corrige sozinho, poda do histórico dele, resultado enxuto e escalonador."""
import asyncio

import pytest

from app import agent, checkpoints, config, db, llm, maestro, settings, subagents, taskdb, workspace


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    root = tmp_path / "projeto"
    root.mkdir()
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


def _conclui(conv_id, code):
    for st in ("queued", "implementing", "testing", "reviewing", "completed"):
        taskdb.set_status(code, st, conv_id)


def _despacha(conv_id, root, code, verifies):
    """run_task com um run_call que escreve de verdade e responde o verify na ordem de `verifies`."""
    fila = list(verifies)
    run_obj = agent.Run(conv_id)
    run_obj.permission = "auto"
    req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="maestro", permission="auto")

    async def run_call(_c, call, _r, _ru, _caps, out, parent=None):
        if call["name"] == "write_file":
            alvo = root / call["arguments"]["path"]
            checkpoints.record(conv_id, 1, alvo, next(iter(run_obj.tentativas.values())))
            alvo.write_text(call["arguments"]["content"], encoding="utf-8")
            out.update(status="ok", text="gravado", meta={"arguments": call["arguments"]})
        else:
            ok = fila.pop(0)
            out.update(status="ok" if ok else "erro", text="exit code: 0" if ok else "exit code: 1\nFALHOU: soma",
                       meta={"arguments": call["arguments"]})
        return
        yield  # pragma: no cover

    out: dict = {}

    async def cena():
        async for _ in maestro.run_task(conv_id, {"id": "rt1", "name": "run_task",
                                                  "arguments": {"code": code}}, req, run_obj, out, run_call):
            pass

    asyncio.run(cena())
    return out


def _worker(monkeypatch, passos):
    """Worker de mentira: cada item é um write_file (caminho, conteúdo) ou None (relatório final)."""
    vistos = []

    async def fala(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append([dict(m) for m in messages])
        passo = passos.pop(0) if passos else None
        if passo:
            yield "done", {"tool_calls": [{"id": f"w{len(vistos)}", "name": "write_file",
                                           "arguments": {"path": passo[0], "content": passo[1]}}],
                           "completion_tokens": 10}
        else:
            yield "content", "feito"
            yield "done", {"tool_calls": [], "completion_tokens": 5}

    monkeypatch.setattr(llm, "chat_stream", fala)
    return vistos


def _uma_tarefa(conv_id):
    taskdb.create_feature(conv_id, "F", "", [{"title": "A", "contract": {"goal": "a", "verify_command": "pytest -q"}}])


def test_worker_corrige_sozinho_quando_o_verify_falha(conv, pasta, monkeypatch):
    """Antes o verify falho encerrava a tentativa e a Maestro tinha de redespachar (e, com modelos
    diferentes, trocar de modelo duas vezes). Agora a saída volta para o próprio Worker."""
    _uma_tarefa(conv)
    vistos = _worker(monkeypatch, [("a.py", "errado"), None, ("a.py", "certo"), None])
    out = _despacha(conv, pasta, "TASK-001", [False, True])
    r = out["meta"]["task_result"]
    assert r["status"] == "completed" and r["attempt"] == 1
    assert out["meta"]["sub"]["voltas"] == 1
    assert (pasta / "a.py").read_text(encoding="utf-8") == "certo"
    assert any("FALHOU" in str(m.get("content")) for m in vistos[2])  # a saída chegou ao Worker


def test_worker_para_de_voltar_no_teto(conv, pasta, monkeypatch):
    _uma_tarefa(conv)
    _worker(monkeypatch, [None, None, None, None, None])
    out = _despacha(conv, pasta, "TASK-001", [False, False, False, False])
    assert out["meta"]["task_result"]["status"] == "failed"
    assert out["meta"]["sub"]["voltas"] == subagents.MAX_VOLTAS_VERIFY


def test_poda_dos_resultados_antigos_do_worker():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "tarefa"}]
    for i in range(5):
        msgs.append({"role": "tool", "content": f"{i}" * 5000})
    msgs.append({"role": "user", "content": "<tool_response>\n" + "p" * 5000})
    subagents._poda_resultados(msgs, manter=2, teto=1500)
    assert [len(m["content"]) <= 1600 for m in msgs[2:]] == [True, True, True, True, False, False]
    assert msgs[1]["content"] == "tarefa"                      # o que não é resultado não muda
    assert "meio cortado" in msgs[2]["content"] and msgs[2]["content"].startswith("0")


def test_janela_estourada_no_meio_poda_e_repete_o_passo(conv, pasta, monkeypatch):
    """Antes só havia saída se a janela estourasse antes do 1º passo; depois disso, a tentativa virava erro."""
    _uma_tarefa(conv)
    chamadas = {"n": 0}

    async def fala(provider, model, messages, tools, num_ctx, effort=None, **kw):
        chamadas["n"] += 1
        if chamadas["n"] == 2:
            raise llm.LLMError("request (40000 tokens) exceeds the available context size", 400)
        if chamadas["n"] == 1:
            yield "done", {"tool_calls": [{"id": "w1", "name": "write_file",
                                           "arguments": {"path": "a.py", "content": "x"}}], "completion_tokens": 3}
        else:
            yield "content", "feito"
            yield "done", {"tool_calls": [], "completion_tokens": 3}

    monkeypatch.setattr(llm, "chat_stream", fala)
    out = _despacha(conv, pasta, "TASK-001", [True])
    assert out["meta"]["task_result"]["status"] == "completed" and chamadas["n"] == 3


def test_resultado_para_a_maestro_e_enxuto_e_o_completo_fica_no_list_tasks(conv, pasta, monkeypatch):
    _uma_tarefa(conv)
    _worker(monkeypatch, [("a.py", "x"), None])
    out = _despacha(conv, pasta, "TASK-001", [True])
    visto = out["text"].split("\n\n")[0]
    assert '"model"' not in visto and '"route"' not in visto and "list_tasks(code=" in visto
    detalhe = taskdb.LIST_TASKS.handler(None, {"code": "TASK-001"})
    assert '"route"' in detalhe and '"verify_command": "pytest -q"' in detalhe


def test_escalonador_escolhe_a_proxima_pronta(conv, pasta):
    taskdb.create_feature(conv, "F", "", [
        {"title": "Base", "contract": {"goal": "a", "verify_command": "x"}},
        {"title": "Depende", "contract": {"goal": "b", "verify_command": "x"}, "depends_on": ["1"], "priority": 9},
        {"title": "Urgente", "contract": {"goal": "c", "verify_command": "x"}, "priority": 5},
        {"title": "Solta", "contract": {"goal": "d", "verify_command": "x"}}])
    assert taskdb.proxima_pronta(conv) == "TASK-003"   # maior priority entre as prontas
    _conclui(conv, "TASK-003")
    assert taskdb.proxima_pronta(conv) == "TASK-001"   # empate: ordem do plano (a 002 espera a 001)
    _conclui(conv, "TASK-001")
    assert taskdb.proxima_pronta(conv) == "TASK-002"   # dependência feita: agora ela passa na frente
    _conclui(conv, "TASK-002")
    _conclui(conv, "TASK-004")
    assert taskdb.proxima_pronta(conv) is None


def test_run_task_sem_code_roda_a_proxima_pronta(conv, pasta, monkeypatch):
    _uma_tarefa(conv)
    _worker(monkeypatch, [None])
    out = _despacha(conv, pasta, "", [True])
    assert out["meta"]["escolhida"] == "TASK-001" and out["meta"]["task_result"]["status"] == "completed"


def test_plano_avisa_tarefa_grande(conv, pasta):
    texto = taskdb.PLAN_FEATURE.handler(None, {"tasks": [
        {"title": "Pequena", "contract": {"goal": "criar soma", "verify_command": "pytest -q"}},
        {"title": "Duas coisas", "contract": {"goal": "criar soma e também o login", "verify_command": "pytest -q"}},
        {"title": "Muitos arquivos", "contract": {"goal": "refatorar", "verify_command": "pytest -q",
                                                  "relevant_files": [f"m{i}.py" for i in range(6)]}}]})
    assert "ATENÇÃO: TASK-002, TASK-003 parece(m) grande(s)" in texto


def test_update_task_com_max_attempts_zero_nao_mexe_no_limite(conv, pasta):
    """O gpt-oss manda todos os campos, vazios: max_attempts=0 virava limite de 1 tentativa."""
    _uma_tarefa(conv)
    antes = taskdb.get("TASK-001", conv).max_attempts
    taskdb.UPDATE_TASK.handler(None, {"code": "TASK-001", "max_attempts": 0, "priority": 0, "model_slot": ""})
    assert taskdb.get("TASK-001", conv).max_attempts == antes
    taskdb.UPDATE_TASK.handler(None, {"code": "TASK-001", "max_attempts": 3})
    assert taskdb.get("TASK-001", conv).max_attempts == 3


def test_resultado_aprovado_nao_leva_erros_intermediarios_do_worker():
    """O Worker roda o teste, vê falhar e conserta: o 'FAILURES' do meio fazia a Maestro bloquear uma
    tarefa aprovada."""
    base = {"task_code": "TASK-001", "attempt": 1, "changes": [], "errors": ["run_command: exit code: 1 FAILURES"]}
    assert "errors" not in maestro._enxuto({**base, "status": "completed"})
    assert maestro._enxuto({**base, "status": "failed"})["errors"] == ["run_command: exit code: 1 FAILURES"]

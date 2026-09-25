"""E2 do Maestro: tentativa que falha é revertida, regressão das tarefas antigas e commit por tarefa."""
import asyncio
import subprocess

import pytest

from app import agent, checkpoints, config, db, gitops, llm, maestro, settings, taskdb, workspace


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


def _worker_escreve(monkeypatch, path, conteudo):
    """Worker de mentira: um write_file e depois o relatório."""
    passo = {"n": 0}

    async def fala(provider, model, messages, tools, num_ctx, effort=None, **kw):
        passo["n"] += 1
        if passo["n"] == 1:
            yield "done", {"tool_calls": [{"id": f"w{passo['n']}", "name": "write_file",
                                           "arguments": {"path": path, "content": conteudo}}],
                           "completion_tokens": 10}
        else:
            yield "content", "feito"
            yield "done", {"tool_calls": [], "completion_tokens": 5}

    monkeypatch.setattr(llm, "chat_stream", fala)


def _despacha(conv_id, root, code, verify_ok=True):
    """run_task com um run_call que escreve de verdade (e grava o checkpoint da tentativa, como o do
    agente) e decide o verify pelo `verify_ok`."""
    run_obj = agent.Run(conv_id)
    run_obj.permission = "auto"
    req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="maestro", permission="auto")

    async def run_call(_c, call, _r, _ru, _caps, out, parent=None):
        if call["name"] == "write_file":
            alvo = root / call["arguments"]["path"]
            checkpoints.record(conv_id, 1, alvo, next(iter(run_obj.tentativas.values())))
            alvo.write_text(call["arguments"]["content"], encoding="utf-8")
            out.update(status="ok", text="gravado", meta={"arguments": call["arguments"]})
        else:  # o verify
            out.update(status="ok" if verify_ok else "erro",
                       text="exit code: 0" if verify_ok else "exit code: 1\nAssertionError",
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


def _conclui(conv_id, code):
    for st in ("queued", "implementing", "testing", "reviewing", "completed"):
        taskdb.set_status(code, st, conv_id)


def test_tentativa_que_falha_e_revertida_e_o_descarte_vai_no_briefing(conv, pasta, monkeypatch):
    (pasta / "a.py").write_text("x = 1\n", encoding="utf-8")
    taskdb.create_feature(conv, "F", "", [{"title": "A", "contract": {"goal": "a", "verify_command": "pytest -q"}}])
    _worker_escreve(monkeypatch, "a.py", "x = 2\n")
    out = _despacha(conv, pasta, "TASK-001", verify_ok=False)
    r = out["meta"]["task_result"]
    assert r["status"] == "failed" and r["rollback"]["files"]
    assert (pasta / "a.py").read_text(encoding="utf-8") == "x = 1\n"   # voltou ao estado de antes
    assert "rollback" in out["text"] and "+x = 2" not in out["text"]      # o diff não ocupa a Maestro
    briefing = taskdb.last_error("TASK-001", conv)
    assert "revertidos" in briefing and "+x = 2" in briefing              # mas vai para o Worker


def test_arquivo_novo_de_tentativa_que_falha_e_apagado(conv, pasta, monkeypatch):
    taskdb.create_feature(conv, "F", "", [{"title": "A", "contract": {"goal": "a", "verify_command": "pytest -q"}}])
    _worker_escreve(monkeypatch, "novo.py", "y = 1\n")
    _despacha(conv, pasta, "TASK-001", verify_ok=False)
    assert not (pasta / "novo.py").exists()


def test_tentativa_que_passa_nao_e_revertida(conv, pasta, monkeypatch):
    taskdb.create_feature(conv, "F", "", [{"title": "A", "contract": {"goal": "a", "verify_command": "pytest -q"}}])
    _worker_escreve(monkeypatch, "a.py", "x = 2\n")
    r = _despacha(conv, pasta, "TASK-001")["meta"]["task_result"]
    assert r["status"] == "completed" and "rollback" not in r
    assert (pasta / "a.py").read_text(encoding="utf-8") == "x = 2\n"


def test_regressao_pega_tarefa_que_quebra_a_anterior(conv, pasta, monkeypatch):
    """A TASK-002 passa no próprio verify, mas estraga o arquivo de que a TASK-001 depende."""
    (pasta / "dado.txt").write_text("ok", encoding="utf-8")
    (pasta / "t1.py").write_text("import sys\nsys.exit(0 if open('dado.txt').read() == 'ok' else 1)\n",
                                 encoding="utf-8")
    taskdb.create_feature(conv, "F", "", [
        {"title": "Um", "contract": {"goal": "um", "verify_command": "python t1.py"}},
        {"title": "Dois", "contract": {"goal": "dois", "verify_command": "pytest -q"}}])
    _conclui(conv, "TASK-001")
    _worker_escreve(monkeypatch, "dado.txt", "quebrado")
    out = _despacha(conv, pasta, "TASK-002")
    r = out["meta"]["task_result"]
    assert r["status"] == "failed" and r["regression"][0]["tasks"] == ["TASK-001"]
    assert "QUEBROU" in out["text"] and "TASK-001" in out["text"]
    assert taskdb.get("TASK-002", conv).blocked_reason == "Quebrou TASK-001 (regressão)."
    assert (pasta / "dado.txt").read_text(encoding="utf-8") == "ok"      # e a tentativa foi revertida
    assert "QUEBROU TASK-001" in taskdb.last_error("TASK-002", conv)


def test_regressao_respeita_o_teto_de_tempo(conv, pasta, monkeypatch):
    monkeypatch.setattr(maestro, "REGRESSAO_TETO", 0)
    taskdb.create_feature(conv, "F", "", [
        {"title": "Um", "contract": {"goal": "um", "verify_command": "python nada.py"}},
        {"title": "Dois", "contract": {"goal": "dois", "verify_command": "pytest -q"}}])
    _conclui(conv, "TASK-001")
    falhas, parcial = maestro.regressao(conv, taskdb.get("TASK-002", conv), pasta)
    assert falhas == [] and parcial


# ------------------------------------------------------------------ commit por tarefa

def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(pasta):
    _git(pasta, "init", "-q")
    _git(pasta, "config", "user.email", "t@t")
    _git(pasta, "config", "user.name", "Teste")
    (pasta / "base.txt").write_text("base", encoding="utf-8")
    _git(pasta, "add", "base.txt")
    _git(pasta, "commit", "-q", "-m", "base")
    return pasta


def test_commit_paths_leva_so_os_arquivos_da_tarefa(repo):
    (repo / "base.txt").write_text("mexido pelo usuário", encoding="utf-8")   # alteração dele, sem commit
    (repo / "a.py").write_text("x = 1", encoding="utf-8")
    (repo / ".env").write_text("SEGREDO=1", encoding="utf-8")
    sha = gitops.commit_paths(repo, ["a.py", ".env", "sumiu.py"], "forja(TASK-001): A")
    assert sha
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["a.py"]
    assert " M base.txt" in _git(repo, "status", "--porcelain")                # continua dele, sem commit
    assert "?? .env" in _git(repo, "status", "--porcelain")
    assert gitops.commit_paths(repo, ["a.py"], "de novo") == ""                # nada mudou


def test_tarefa_concluida_vira_commit(conv, repo):
    taskdb.create_feature(conv, "F", "", [{"title": "Soma", "contract": {"goal": "a", "verify_command": "pytest -q"}}])
    aid = taskdb.new_attempt("TASK-001", {"level": "capaz"}, "", conv)
    (repo / "calc.py").write_text("def soma(a, b): return a + b", encoding="utf-8")
    taskdb.finish_attempt(aid, "completed", {"changes": [{"path": "calc.py"}]})
    for st in ("queued", "implementing", "testing", "reviewing"):
        taskdb.set_status("TASK-001", st, conv)
    texto = taskdb.UPDATE_TASK.handler(None, {"code": "TASK-001", "status": "completed"})
    assert "Commit " in texto
    assert _git(repo, "log", "-1", "--format=%s").strip() == "forja(TASK-001): Soma"
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["calc.py"]


def test_sem_git_avisa_uma_vez_e_nao_quebra(conv, pasta):
    taskdb.create_feature(conv, "F", "", [{"title": "A", "contract": {"goal": "a", "verify_command": "pytest -q"}},
                                          {"title": "B", "contract": {"goal": "b", "verify_command": "pytest -q"}}])
    textos = []
    for code in ("TASK-001", "TASK-002"):
        for st in ("queued", "implementing", "testing", "reviewing"):
            taskdb.set_status(code, st, conv)
        textos.append(taskdb.UPDATE_TASK.handler(None, {"code": code, "status": "completed"}))
    assert "não é um repositório git" in textos[0] and "não é um repositório git" not in textos[1]


def test_verify_coberto_por_comando_que_roda_mais_de_um(conv, pasta, monkeypatch):
    """`pytest -q a.py b.py` roda os dois verify; antes a entrega exigia o texto exato de cada um."""
    from app import qualidade
    monkeypatch.setattr(qualidade, "faltas_para_entregar", lambda *a: [])
    taskdb.create_feature(conv, "F", "", [
        {"title": "A", "contract": {"goal": "a", "verify_command": "python -m pytest -q test_a.py"}},
        {"title": "B", "contract": {"goal": "b", "verify_command": "python -m pytest -q test_b.py"}}])
    _conclui(conv, "TASK-001")
    _conclui(conv, "TASK-002")
    with db.session() as s:
        s.add(db.Message(conversation_id=conv, role="tool", name="run_command", status="ok",
                         meta={"arguments": {"command": "python -m pytest -q test_a.py test_b.py"}}))
        s.commit()
    assert taskdb.encerra_validadas(conv) == ["F"]


def test_diff_do_descarte_usa_caminho_relativo(conv, pasta, monkeypatch):
    taskdb.create_feature(conv, "F", "", [{"title": "A", "contract": {"goal": "a", "verify_command": "pytest -q"}}])
    _worker_escreve(monkeypatch, "pkg/a.py", "x = 2\n")
    (pasta / "pkg").mkdir()
    _despacha(conv, pasta, "TASK-001", verify_ok=False)
    briefing = taskdb.last_error("TASK-001", conv)
    assert "+++ b/pkg/a.py" in briefing and str(pasta) not in briefing

"""E7: Workers em paralelo, cada um no seu worktree, com o merge limpo ou o conflito detectado."""
import asyncio
import subprocess

import pytest

from app import agent, config, db, gitops, llm, localai, maestro, modelctl, perfis, settings, taskdb, workspace

BASE = "".join(f"linha {i}\n" for i in range(1, 11))


def _git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.txt").write_text(BASE, encoding="utf-8", newline="\n")
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    monkeypatch.setattr(config, "WORKSPACE_ROOT", root)
    token = workspace.CURRENT.set(root)

    async def nada(*a):
        return None

    monkeypatch.setattr(llm, "capabilities", nada)
    settings.reset()
    config.SUBAGENTS = {"capaz": {"provider": "lmstudio", "model": "coder"},
                        "rapido": {"provider": "", "model": ""}, "nuvem": {"provider": "", "model": ""}}
    monkeypatch.setattr(config, "MAX_WORKERS", 2)
    monkeypatch.setattr(config, "REVISAO", "off")
    with db.session() as s:
        c = db.Conversation(title="Maestro", kind="maestro")
        s.add(c)
        s.commit()
        conv = c.id
    tconv = taskdb.CONV.set(conv)
    yield root, conv
    taskdb.CONV.reset(tconv)
    workspace.CURRENT.reset(token)


def _worker_por_tarefa(monkeypatch, conteudo: dict):
    """Cada Worker escreve calc.txt com o conteúdo da sua tarefa (achada pelo título no briefing)."""
    feitos = set()

    async def fala(provider, model, messages, tools, num_ctx, effort=None, **kw):
        brief = messages[1]["content"]
        titulo = next(t for t in conteudo if t in brief)
        await asyncio.sleep(0.05)  # os dois Workers ficam no ar ao mesmo tempo
        if titulo not in feitos:
            feitos.add(titulo)
            yield "done", {"tool_calls": [{"id": f"w-{titulo}", "name": "write_file",
                                           "arguments": {"path": "calc.txt", "content": conteudo[titulo]}}],
                           "completion_tokens": 5}
        else:
            yield "content", "feito"
            yield "done", {"tool_calls": [], "completion_tokens": 5}

    monkeypatch.setattr(llm, "chat_stream", fala)


def _paralelo(root, conv, conteudo):
    taskdb.create_feature(conv, "F", "", [{"title": t, "contract": {"goal": t, "verify_command": "echo ok",
                                                                    "relevant_files": ["calc.txt"]}}
                                          for t in conteudo])
    run_obj = agent.Run(conv)
    run_obj.permission = "auto"
    req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="maestro", permission="auto")
    vistos_em = []

    async def run_call(_c, call, _r, _ru, _caps, out, parent=None):
        onde = workspace.root()
        vistos_em.append((call["name"], onde))
        if call["name"] == "write_file":
            (onde / call["arguments"]["path"]).write_text(call["arguments"]["content"], encoding="utf-8", newline="\n")
            out.update(status="ok", text="gravado", meta={"arguments": call["arguments"]})
        else:
            out.update(status="ok", text="exit code: 0", meta={"arguments": call["arguments"]})
        return
        yield  # pragma: no cover

    outs = [{}, {}]

    async def cena():
        async def uma(i, code):
            async for _ in maestro.run_task(conv, {"id": f"rt{i}", "name": "run_task", "arguments": {"code": code}},
                                            req, run_obj, outs[i], run_call):
                pass
        await asyncio.gather(uma(0, "TASK-001"), uma(1, "TASK-002"))

    asyncio.run(cena())
    return outs, vistos_em


def test_dois_workers_no_mesmo_arquivo_em_linhas_diferentes_juntam_limpo(repo, monkeypatch):
    root, conv = repo
    a = BASE.replace("linha 1\n", "linha 1 (A)\n")
    b = BASE.replace("linha 9\n", "linha 9 (B)\n")
    _worker_por_tarefa(monkeypatch, {"Primeira": a, "Segunda": b})
    outs, vistos = _paralelo(root, conv, {"Primeira": a, "Segunda": b})
    assert [o["meta"]["task_result"]["status"] for o in outs] == ["completed", "completed"]
    texto = (root / "calc.txt").read_text(encoding="utf-8")
    assert "linha 1 (A)" in texto and "linha 9 (B)" in texto          # as duas entraram na pasta principal
    escritas = {str(onde) for nome, onde in vistos if nome == "write_file"}
    assert len(escritas) == 2 and all(".forja" in e for e in escritas)  # cada Worker no seu worktree
    assert not (root / gitops.WT_DIR / "TASK-001").exists()            # worktree removido no fim


def test_mesma_linha_detecta_o_conflito_e_nao_suja_a_pasta(repo, monkeypatch):
    root, conv = repo
    a = BASE.replace("linha 1\n", "linha 1 (A)\n")
    b = BASE.replace("linha 1\n", "linha 1 (B)\n")
    _worker_por_tarefa(monkeypatch, {"Primeira": a, "Segunda": b})
    outs, _ = _paralelo(root, conv, {"Primeira": a, "Segunda": b})
    status = sorted(o["meta"]["task_result"]["status"] for o in outs)
    assert status == ["completed", "failed"]
    falhou = next(o for o in outs if o["meta"]["task_result"]["status"] == "failed")["meta"]["task_result"]
    assert "conflito" in falhou["merge_conflict"]
    texto = (root / "calc.txt").read_text(encoding="utf-8")
    assert "<<<<<<<" not in texto and texto.count("linha 1 (") == 1   # só a vencedora, sem marcador


def test_workers_possiveis_nunca_tira_o_slot_do_maestro(monkeypatch):
    monkeypatch.setattr(config, "PERFIL_HARDWARE", "performance")
    perfis.reavaliar()
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "ctx": 32768, "params": {"parallel": 2}})
    w = modelctl.workers_possiveis(3)
    assert w["possiveis"] == 1 and "slot" in w["motivo"] and w["janela_por_slot"] == 16384
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "ctx": 65536,
                                                    "params": {"parallel": 4, "kv_unified": True}})
    assert modelctl.workers_possiveis(3)["possiveis"] == 3
    monkeypatch.setattr(config, "PERFIL_HARDWARE", "low_vram")
    perfis.reavaliar()
    assert modelctl.workers_possiveis(3)["possiveis"] == 1                # Low VRAM: sequencial


def test_trava_por_arquivo_serializa_escritas(tmp_path, monkeypatch):
    workspace.CURRENT.set(tmp_path)
    ordem = []

    async def escreve(i):
        async with agent._trava_arquivo("write_file", {"path": "a.txt"}):
            ordem.append(("entra", i))
            await asyncio.sleep(0.02)
            ordem.append(("sai", i))

    async def cena():
        await asyncio.gather(escreve(1), escreve(2))

    asyncio.run(cena())
    assert ordem[0][0] == "entra" and ordem[1][0] == "sai"   # a segunda só entra depois de a primeira sair


def test_arquivo_gerado_ao_rodar_nao_vira_conflito(repo):
    """Dois Workers rodando pytest criam o mesmo .pyc nos dois lados: é lixo, não conflito."""
    root, _ = repo
    wt = gitops.worktree_tarefa(root, "TASK-009")
    for lado in (root, wt):
        (lado / "pkg" / "__pycache__").mkdir(parents=True)
        (lado / "pkg" / "__pycache__" / "m.cpython-313.pyc").write_bytes(lado.name.encode())
    (wt / "test_novo.py").write_text("def test_x(): pass\n", encoding="utf-8")
    arquivos, conflito = gitops.traz_do_worktree(root, wt)
    gitops.remove_worktree_tarefa(root, "TASK-009")
    assert conflito == "" and arquivos == ["test_novo.py"]

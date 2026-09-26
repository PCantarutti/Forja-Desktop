"""E8: revisão de código por critério de aceite, depois de o verify passar e antes do commit."""
import asyncio

from app import checkpoints, config, critico, gitops, llm, maestro, modelctl, taskdb
from tests.test_execucao import _despacha, _worker, conv, pasta  # noqa: F401  (fixtures)


def _tarefa(conv_id):
    taskdb.create_feature(conv_id, "F", "", [{"title": "A", "contract": {
        "goal": "soma", "verify_command": "pytest -q", "relevant_files": ["a.py"],
        "acceptance_criteria": ["soma aceita negativos", "tem docstring"]}}])


def _revisor(monkeypatch, respostas):
    """Revisor falso: cada item é a lista de 'atendido' por critério; registra o que o Worker recebeu."""
    fila = list(respostas)
    monkeypatch.setattr(gitops, "is_repo", lambda root: True)
    monkeypatch.setattr(gitops, "diff", lambda root, p=None: f"+++ b/{p}\n+def soma(a, b): return a + b\n")
    monkeypatch.setattr(modelctl, "como_rodar", lambda papel, pedido: modelctl.Rota("externo", pedido, None, "teste"))
    real = llm.chat_stream

    async def fala(provider, model, messages, tools, num_ctx, effort=None, **kw):
        if tools is None and "critério" in messages[0]["content"]:
            at = fila.pop(0)
            import json
            yield "content", json.dumps({"criterios": [{"n": i + 1, "atendido": a, "evidencia": "a.py:1"}
                                                       for i, a in enumerate(at)]})
            yield "done", {}
            return
        async for x in real(provider, model, messages, tools, num_ctx, effort, **kw):
            yield x

    monkeypatch.setattr(llm, "chat_stream", fala)


def test_interpreta_resposta_ruim_nunca_reprova():
    itens = critico.interpreta("não sei", ["a", "b"])
    assert [i["atendido"] for i in itens] == [None, None] and not critico.falhas(itens)


def test_modo_do_forja_md_vence_a_configuracao(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REVISAO", "avisa")
    assert critico.modo(tmp_path) == "avisa"
    (tmp_path / "FORJA.md").write_text("# Projeto\nrevisao: bloqueia\n", encoding="utf-8")
    assert critico.modo(tmp_path) == "bloqueia"


def test_avisa_nao_bloqueia_mas_conta_para_a_maestro(conv, pasta, monkeypatch):
    monkeypatch.setattr(config, "REVISAO", "avisa")
    _tarefa(conv)
    _worker(monkeypatch, [("a.py", "x"), None])
    _revisor(monkeypatch, [[True, False]])
    out = _despacha(conv, pasta, "TASK-001", [True])
    r = out["meta"]["task_result"]
    assert r["status"] == "completed" and "ATENÇÃO, REVISÃO DE CÓDIGO" in out["text"] and "tem docstring" in out["text"]


def test_bloqueia_volta_ao_worker_uma_vez_e_depois_falha(conv, pasta, monkeypatch):
    monkeypatch.setattr(config, "REVISAO", "bloqueia")
    _tarefa(conv)
    vistos = _worker(monkeypatch, [("a.py", "x"), None, ("a.py", "y"), None])
    _revisor(monkeypatch, [[True, False], [True, False]])
    out = _despacha(conv, pasta, "TASK-001", [True, True])
    r = out["meta"]["task_result"]
    assert r["status"] == "failed" and r["criteria_blocked"]
    assert any("NÃO atendidos" in (m.get("content") or "") for conversa in vistos for m in conversa)
    assert taskdb.get("TASK-001", conv).status == "failed"


def test_bloqueia_com_a_volta_corrigindo_passa(conv, pasta, monkeypatch):
    monkeypatch.setattr(config, "REVISAO", "bloqueia")
    _tarefa(conv)
    _worker(monkeypatch, [("a.py", "x"), None, ("a.py", "y"), None])
    _revisor(monkeypatch, [[True, False], [True, True]])
    out = _despacha(conv, pasta, "TASK-001", [True, True])
    assert out["meta"]["task_result"]["status"] == "completed" and "REVISÃO DE CÓDIGO" not in out["text"]


def test_off_nem_chama_o_revisor(conv, pasta, monkeypatch):
    monkeypatch.setattr(config, "REVISAO", "off")
    _tarefa(conv)
    _worker(monkeypatch, [("a.py", "x"), None])
    _revisor(monkeypatch, [])   # fila vazia: se chamasse, quebraria
    out = _despacha(conv, pasta, "TASK-001", [True])
    assert out["meta"]["task_result"]["status"] == "completed" and "criteria" not in out["meta"]["task_result"]

"""E17: o Forja como servidor MCP, testado com um cliente MCP de verdade contra um uvicorn local."""
import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from app import board, config, db, main, mcp_servidor, taskdb


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servidor():
    porta = _porta_livre()
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=porta, log_level="warning", lifespan="on"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{porta}"
    srv.should_exit = True
    t.join(timeout=10)


@pytest.fixture
def ligado(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    monkeypatch.setattr(config, "MCP_PERMISSAO", "manual")
    (tmp_path / ".git").mkdir()
    (tmp_path / "app.py").write_text("def soma(a, b):\n    return a - b\n", encoding="utf-8")
    return tmp_path


def _cliente(url, token, fn):
    """Abre uma sessão MCP (streamable HTTP), roda `fn(session)` e devolve o resultado."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def rodar():
        async with streamablehttp_client(url + "/mcp", headers={"x-forja-token": token}) as (ler, escrever, _):
            async with ClientSession(ler, escrever) as sessao:
                await sessao.initialize()
                return await fn(sessao)
    return asyncio.run(rodar())


def _texto(r) -> str:
    return "".join(getattr(c, "text", "") for c in r.content)


def test_sem_token_ou_desligado_recusa(servidor, monkeypatch):
    assert httpx.post(servidor + "/mcp", json={}).status_code == 401
    monkeypatch.setattr(config, "MCP_SERVIDOR", False)
    r = httpx.post(servidor + "/mcp", json={}, headers={"x-forja-token": mcp_servidor.token()})
    assert r.status_code == 403 and "desligado" in r.json()["error"]


def test_ferramentas_e_estado_do_projeto(servidor, ligado):
    async def fn(s):
        nomes = {t.name for t in (await s.list_tools()).tools}
        estado = _texto(await s.call_tool("project_state", {"path": str(ligado)}))
        return nomes, estado
    nomes, estado = _cliente(servidor, mcp_servidor.token(), fn)
    assert {"project_state", "plan_feature", "run_task", "task_status", "issue_create", "issue_update",
            "forja_note", "forja_inbox"} <= nomes
    assert "# Projeto" in estado and "## Tarefas" in estado


def test_issue_create_confere_evidencia_e_tudo_fica_na_conversa_espelho(servidor, ligado):
    async def fn(s):
        ruim = _texto(await s.call_tool("issue_create", {"path": str(ligado), "titulo": "x", "tipo": "bugfix",
                                                         "arquivo": "app.py", "linha": 2, "trecho": "não está lá"}))
        bom = _texto(await s.call_tool("issue_create", {"path": str(ligado), "titulo": "Soma subtrai",
                                                        "tipo": "bug", "arquivo": "app.py", "linha": 2}))
        return ruim, bom
    ruim, bom = _cliente(servidor, mcp_servidor.token(), fn)
    assert ruim.startswith("ERRO") and "não está no arquivo" in ruim
    assert "coluna Novo" in bom
    projeto = board.projeto_de(str(ligado))
    assert [c["titulo"] for c in board.listar(projeto)] == ["Soma subtrai"]
    conv, _ = mcp_servidor.espelho(str(ligado))
    with db.session() as s:
        c = s.get(db.Conversation, conv)
        assert c.kind == "maestro" and mcp_servidor.eh_espelho(c)
        nomes = [m.name for m in c.messages if m.role == "tool"]
    assert nomes == ["board_card", "board_card"]  # toda chamada registrada pelo próprio servidor


def test_plan_feature_passa_pelos_portoes_e_run_task_devolve_na_hora(servidor, ligado):
    async def fn(s):
        sem_verify = _texto(await s.call_tool("plan_feature", {"path": str(ligado), "title": "Soma", "tasks": [
            {"title": "Corrigir soma", "goal": "somar", "relevant_files": ["app.py"]}]}))
        ok = _texto(await s.call_tool("plan_feature", {"path": str(ligado), "title": "Soma", "tasks": [
            {"title": "Corrigir soma", "goal": "somar", "relevant_files": ["app.py"], "verify_command": "python -c 1"},
            {"title": "Depois", "goal": "x", "relevant_files": ["app.py"], "verify_command": "python -c 1",
             "depends_on": ["TASK-001"]}]}))
        t0 = time.monotonic()
        bloqueada = _texto(await s.call_tool("run_task", {"path": str(ligado), "code": "TASK-002"}))
        return sem_verify, ok, bloqueada, time.monotonic() - t0
    sem_verify, ok, bloqueada, segundos = _cliente(servidor, mcp_servidor.token(), fn)
    assert sem_verify.startswith("ERRO") and "verify" in sem_verify.lower()
    assert "TASK-001" in ok and "TASK-002" in ok
    assert bloqueada.startswith("ERRO") and "depende de TASK-001" in bloqueada and segundos < 20


def test_caixa_de_entrada_chega_no_proximo_resultado(servidor, ligado):
    conv, _ = mcp_servidor.espelho(str(ligado))
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:  # o usuário escreve na conversa-espelho (como pela tela ou pelo celular)
        r = c.post(f"/api/conversations/{conv}/run", json={"content": "Não mexa no README", "provider": "p",
                                                             "model": "m"})
        assert r.status_code == 200 and "Guardado para o Claude" in r.text

    async def fn(s):
        return _texto(await s.call_tool("forja_note", {"path": str(ligado), "texto": "Planejando a soma"}))
    resposta = _cliente(servidor, mcp_servidor.token(), fn)
    assert "Mensagem do usuário" in resposta and "Não mexa no README" in resposta
    assert mcp_servidor.caixa(conv) == []  # entregue uma vez só
    with db.session() as s:
        falas = [(m.role, m.content) for m in s.get(db.Conversation, conv).messages]
    assert ("assistant", "Planejando a soma") in falas


def test_hooks_do_claude_code(servidor, ligado, tmp_path):
    t = mcp_servidor.token()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Corrigi a soma."}]}}) + "\n", encoding="utf-8")
    assert httpx.post(servidor + "/mcp/hook", json={}, headers={"x-forja-token": "errado"}).status_code == 401
    for ev in ({"hook_event_name": "UserPromptSubmit", "cwd": str(ligado), "prompt": "conserte a soma"},
               {"hook_event_name": "PostToolUse", "cwd": str(ligado), "tool_name": "Edit",
                "tool_input": {"file_path": "app.py"}},
               {"hook_event_name": "PostToolUse", "cwd": str(ligado), "tool_name": "mcp__forja__run_task"},
               {"hook_event_name": "Stop", "cwd": str(ligado), "transcript_path": str(transcript)}):
        assert httpx.post(servidor + "/mcp/hook", json=ev, headers={"x-forja-token": t}).json()["ok"]
    conv, _ = mcp_servidor.espelho(str(ligado))
    time.sleep(0.5)
    with db.session() as s:
        falas = [(m.role, m.content) for m in s.get(db.Conversation, conv).messages]
    assert ("user", "conserte a soma") in falas and ("assistant", "Corrigi a soma.") in falas
    assert ("event", "Claude usou Edit: app.py") in falas
    assert not any("mcp__forja" in (c or "") for _, c in falas)  # a ferramenta do Forja já está registrada


def test_instala_hooks_sem_guardar_segredo(ligado):
    r = mcp_servidor.instala_hooks(str(ligado))
    cfg = json.loads((ligado / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert set(cfg["hooks"]) == {"UserPromptSubmit", "Stop", "PostToolUse"} and r["eventos"]
    comando = cfg["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "forja_hook.py" in comando and mcp_servidor.token() not in json.dumps(cfg)
    mcp_servidor.instala_hooks(str(ligado))  # de novo: não duplica
    cfg = json.loads((ligado / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert len(cfg["hooks"]["Stop"]) == 1


def test_mensagem_com_o_run_do_claude_aberto_vai_para_a_caixa(servidor, ligado):
    """Com o Claude trabalhando, a tela manda a fala por /runs/{id}/queue: ela não pode virar fila de agente."""
    async def fn(s):
        return _texto(await s.call_tool("forja_note", {"path": str(ligado), "texto": "trabalhando"}))
    _cliente(servidor, mcp_servidor.token(), fn)  # abre o Run da conversa-espelho
    conv, _ = mcp_servidor.espelho(str(ligado))
    run = mcp_servidor._SESSOES[conv].run
    assert not run.finished
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        r = c.post(f"/api/runs/{run.id}/queue", json={"content": "use pytest, não unittest"},
                   headers={"x-forja-token": config.API_TOKEN} if config.API_TOKEN else {})
        assert r.status_code == 200 and r.json()["claude"] is True
    assert run.queue == [] and mcp_servidor.caixa(conv, marcar=False) == ["use pytest, não unittest"]


def test_estatisticas_do_turno_vem_do_claude(tmp_path):
    assert mcp_servidor.nome_do_modelo("claude-opus-5-5") == "Claude Opus 5.5"
    assert mcp_servidor.nome_do_modelo("claude-haiku-4-5-20251001") == "Claude Haiku 4.5"
    assert mcp_servidor.nome_do_modelo("claude-sonnet-5") == "Claude Sonnet 5"
    linhas = [
        {"type": "user", "timestamp": "2026-09-25T10:00:00Z", "message": {"role": "user", "content": "antigo"}},
        {"type": "assistant", "timestamp": "2026-09-25T10:00:05Z", "message": {"model": "claude-sonnet-5",
         "usage": {"output_tokens": 999}, "content": [{"type": "text", "text": "velho"}]}},
        {"type": "user", "timestamp": "2026-09-25T10:01:00Z", "message": {"role": "user", "content": "conserte"}},
        {"type": "assistant", "timestamp": "2026-09-25T10:01:04Z", "message": {"model": "claude-opus-5-5",
         "usage": {"output_tokens": 120}, "content": [{"type": "tool_use", "name": "Read"}]}},
        {"type": "user", "timestamp": "2026-09-25T10:01:05Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "..."}]}},  # resultado de ferramenta não abre turno novo
        {"type": "assistant", "timestamp": "2026-09-25T10:01:30Z", "message": {"model": "claude-opus-5-5",
         "usage": {"output_tokens": 80}, "content": [{"type": "text", "text": "Pronto."}]}},
    ]
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(x) for x in linhas), encoding="utf-8")
    assert mcp_servidor.turno_do_transcript(str(t)) == {"texto": "Pronto.", "modelo": "claude-opus-5-5",
                                                        "tokens": 200, "segundos": 30.0}


def test_stop_do_claude_fecha_o_run_da_conversa(servidor, ligado):
    """O Run da conversa-espelho ficava aberto 15 min e a tela dizia "trabalhando…" o tempo todo."""
    async def fn(s):
        return _texto(await s.call_tool("forja_note", {"path": str(ligado), "texto": "começando"}))
    _cliente(servidor, mcp_servidor.token(), fn)
    conv, _ = mcp_servidor.espelho(str(ligado))
    run = mcp_servidor._SESSOES[conv].run
    assert not run.finished
    httpx.post(servidor + "/mcp/hook", json={"hook_event_name": "Stop", "cwd": str(ligado),
                                             "last_assistant_message": "Feito."},
               headers={"x-forja-token": mcp_servidor.token()})
    for _ in range(50):
        if run.finished:
            break
        time.sleep(0.1)
    assert run.finished
    # e a próxima chamada abre outro Run, sem se perder
    assert "Anotado" in _cliente(servidor, mcp_servidor.token(), fn)
    assert not mcp_servidor._SESSOES[conv].run.finished

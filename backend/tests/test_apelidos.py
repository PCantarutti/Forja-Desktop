"""Nomes de ferramenta de outros harnesses viram a ferramenta do Forja."""
from app import agent, apelidos, main  # noqa: F401  (agent e main registram todas as ferramentas)
from app.parsing import parse_text_tool_calls
from app.tools import EXTRA, REGISTRY

NOMES = list(REGISTRY) + list(EXTRA) + [agent.EXIT_PLAN.name, agent.ASK_USER.name]
_TODAS = {**REGISTRY, **EXTRA, agent.EXIT_PLAN.name: agent.EXIT_PLAN, agent.ASK_USER.name: agent.ASK_USER}


def _r(nome, args, nomes=NOMES):
    return apelidos.resolve({"id": "c1", "name": nome, "arguments": args}, nomes, agent._props)


def test_toda_ferramenta_tem_apelido():
    """Ferramenta nova: ponha em apelidos.APELIDOS os nomes que outros harnesses usam para ela."""
    embutidas = [n for n in NOMES if not _TODAS[n].source.startswith("mcp:")]
    assert not [n for n in embutidas if not apelidos.APELIDOS.get(n)]


def test_apelido_nao_e_nome_real_nem_repetido():
    todos = [a for nomes in apelidos.APELIDOS.values() for a in nomes]
    assert len(todos) == len(set(todos))
    assert not set(todos) & set(NOMES)
    assert all(alvo in NOMES for alvo in apelidos.APELIDOS)


def test_search_do_gpt_oss_vira_code_search():
    c = _r("search", {"path": ".", "query": "LoginScreen", "max_results": 20, "case_sensitive": False})
    assert c["name"] == "code_search" and c["apelido"] == "search"
    assert c["arguments"]["query"] == "LoginScreen" and c["arguments"]["path"] == "."
    assert "se chama 'code_search'" in apelidos.nota(c)


def test_nomes_do_claude_code():
    assert _r("Bash", {"command": "ls", "description": "x"})["name"] == "run_command"
    lido = _r("Read", {"file_path": "a.py", "offset": 10})
    assert lido["name"] == "read_file" and lido["arguments"] == {"path": "a.py", "start_line": 10}
    ed = _r("MultiEdit", {"file_path": "a.py", "edits": [{"old_string": "x", "new_string": "y"}]})
    assert ed["name"] == "edit_file" and ed["arguments"] == {"path": "a.py", "edits": [{"old_str": "x", "new_str": "y"}]}
    assert _r("functions.WebFetch", {"url": "https://x"})["name"] == "fetch_url"
    assert _r("TodoWrite", {"todos": []})["arguments"] == {"tasks": []}


def test_editor_multiplexado():
    v = _r("str_replace_based_edit_tool", {"command": "view", "path": "a.py", "view_range": [5, 9]})
    assert v["name"] == "read_file" and v["arguments"] == {"path": "a.py", "start_line": 5, "end_line": 9}
    c = _r("str_replace_editor", {"command": "create", "path": "n.py", "file_text": "x"})
    assert c["name"] == "write_file" and c["arguments"] == {"path": "n.py", "content": "x"}
    s = _r("str_replace_editor", {"command": "str_replace", "path": "n.py", "old_str": "a", "new_str": "b"})
    assert s["name"] == "edit_file"


def test_nome_certo_com_argumento_de_outro_harness():
    c = _r("read_file", {"file_path": "a.py"})
    assert c["arguments"] == {"path": "a.py"} and "apelido" not in c and apelidos.nota(c) == ""
    cmd = _r("run_command", {"cmd": "dir", "workdir": "src"})
    assert cmd["arguments"] == {"command": "dir", "cwd": "src"}


def test_argumento_real_nao_e_renomeado():
    c = _r("grep", {"pattern": "x", "path": "src", "include": "*.py"})  # nada a mudar: mesma chamada
    assert c == {"id": "c1", "name": "grep", "arguments": {"pattern": "x", "path": "src", "include": "*.py"}}
    # edit_file tem `edits`; run_command não tem `path`: `dir` vira cwd, não path
    assert _r("bash", {"command": "ls", "dir": "x"})["arguments"] == {"command": "ls", "cwd": "x"}


def test_apelido_nao_escapa_da_lista_do_subagente():
    explorador = ["read_file", "grep", "code_search"]
    assert _r("bash", {"command": "rm -rf ."}, explorador)["name"] == "bash"  # continua recusado
    assert _r("search", {"query": "x"}, explorador)["name"] == "code_search"
    assert _r("inexistente", {"a": 1})["name"] == "inexistente"


def test_loop_do_agente_roda_o_apelido_e_ensina_o_nome(monkeypatch, tmp_path):
    """De ponta a ponta: o modelo chama `Read` e `search`; roda read_file e code_search, a UI recebe o nome
    do Forja e o resultado diz qual é o nome certo."""
    import asyncio
    from app import config, db, llm, workspace
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "dados")
    (tmp_path / "notas.txt").write_text("a senha do wifi fica no roteador", encoding="utf-8")
    passo = {"n": 0}

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        passo["n"] += 1
        if passo["n"] == 1:
            yield "done", {"tool_calls": [
                {"id": "a1", "name": "Read", "arguments": {"file_path": "notas.txt"}},
                {"id": "a2", "name": "search", "arguments": {"query": "senha wifi", "case_sensitive": False}}]}
        else:
            yield "content", "Pronto."
            yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)

    async def cenario():
        with db.session() as s:
            c = db.Conversation(kind="agent", workspace=str(tmp_path))
            s.add(c)
            s.commit()
            conv = c.id
        tok = workspace.CURRENT.set(tmp_path)
        try:
            run = agent.Run(conv)
            req = agent.RunRequest(content="leia", provider="lmstudio", model="m", mode="agent", permission="manual")
            return [ev async for ev in agent.run_agent(conv, req, run)]
        finally:
            workspace.CURRENT.reset(tok)

    eventos = asyncio.run(cenario())
    resultados = {e["message"]["name"]: e["message"] for e in eventos if e.get("type") == "tool_result"}
    assert set(resultados) == {"read_file", "code_search"}
    assert resultados["read_file"]["status"] == "ok" and "roteador" in resultados["read_file"]["content"]
    assert "se chama 'read_file'" in resultados["read_file"]["content"]
    assert "notas.txt" in resultados["code_search"]["content"]


def test_parser_de_texto_aceita_apelido():
    nomes = ["read_file", "run_command"]
    calls, _ = parse_text_tool_calls('<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>',
                                     nomes + apelidos.extras(nomes))
    assert [apelidos.resolve(c, nomes)["name"] for c in calls] == ["run_command"]

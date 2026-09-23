"""Portão de qualidade para projeto com tela: erro vira tarefa, revisão visual, provas para encerrar."""
import asyncio
import json

import pytest

from app import agent, config, db, projstate, qualidade, taskdb, workspace
from app.tools import ToolError


@pytest.fixture
def site(tmp_path, monkeypatch):
    """Projeto web (Vite + React, com build) numa conversa Maestro."""
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    monkeypatch.setattr(config, "MAESTRO_BROWSER", True)
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"dev": "vite", "build": "tsc -b && vite build"},
        "dependencies": {"react": "^19"}, "devDependencies": {"vite": "^8"}}), "utf-8")
    (tmp_path / "FORJA.md").write_text("# Site\nLoja\n", "utf-8")
    projstate.ensure(tmp_path)
    with db.session() as s:
        c = db.Conversation(title="Site", kind="maestro", workspace=str(tmp_path))
        s.add(c)
        s.commit()
        conv = c.id
    token = taskdb.CONV.set(conv)
    yield tmp_path, conv
    taskdb.CONV.reset(token)


def _conclui(code, conv):
    for st in ("queued", "implementing", "testing", "reviewing", "completed"):
        taskdb.set_status(code, st, conv)


def _ferramenta(conv, nome, conteudo, argumentos=None, status="ok"):
    with db.session() as s:
        s.add(db.Message(conversation_id=conv, role="tool", name=nome, status=status, content=conteudo,
                         meta={"arguments": argumentos or {}}))
        s.commit()


def test_detecta_projeto_com_tela(tmp_path, site):
    assert qualidade.tem_tela(site[0]) and qualidade.tem_build(site[0])
    vazio = tmp_path / "py"
    vazio.mkdir()
    (vazio / "calc.py").write_text("x = 1", "utf-8")
    assert not qualidade.tem_tela(vazio)
    (vazio / "index.html").write_text("<h1>oi</h1>", "utf-8")
    assert qualidade.tem_tela(vazio) and not qualidade.tem_build(vazio)


def test_projeto_com_tela_exige_guia_visual_e_ele_vai_no_contrato(site):
    root, conv = site
    with pytest.raises(ToolError, match="guia visual"):
        taskdb.PLAN_FEATURE.handler(None, {"tasks": [{"title": "Home", "contract": {"goal": "home"}}]})
    (root / qualidade.GUIA).parent.mkdir(parents=True, exist_ok=True)
    (root / qualidade.GUIA).write_text("# Frontend\nPaleta: #111 e #0af. Fonte Inter.\n", "utf-8")
    taskdb.PLAN_FEATURE.handler(None, {"tasks": [{"title": "Home", "contract": {"goal": "home",
                                                                               "relevant_files": ["src/App.tsx"]}}]})
    assert taskdb.get("TASK-001", conv).contract["relevant_files"] == [qualidade.GUIA, "src/App.tsx"]


def test_erro_de_console_vira_tarefa_sem_duplicar(site):
    _, conv = site
    taskdb.create_feature(conv, "Home", "", [{"title": "Home", "contract": {"goal": "home"}}])
    _conclui("TASK-001", conv)  # funcionalidade em validação
    resultado = "URL: x\n\nERROS DE CONSOLE: 2\n[x] pageerror: TypeError: a is undefined\n[y] requestfailed: /api\n\nESTRUTURA"
    nota = qualidade.pos_validacao(conv, resultado, "http://localhost:5173/")
    assert "Criei a TASK-002" in nota
    t = taskdb.get("TASK-002", conv)
    assert "TypeError" in t.contract["requirements"][0] and taskdb.board(conv)["features"][0]["status"] == "active"
    assert "já estão na TASK-002" in qualidade.pos_validacao(conv, resultado, "http://localhost:5173/")
    assert qualidade.pos_validacao(conv, "ERROS DE CONSOLE: 0 (nenhum)", "http://x") == ""


def test_rodadas_de_correcao_tem_limite(site, monkeypatch):
    _, conv = site
    monkeypatch.setattr(qualidade, "MAX_CORRECOES", 1)
    taskdb.create_feature(conv, "Home", "", [{"title": "Home", "contract": {"goal": "home"}}])
    qualidade.tarefa_de_correcao(conv, "Corrigir A", ["a"], "x")
    assert "ask_user" in qualidade.tarefa_de_correcao(conv, "Corrigir B", ["b"], "x")


def test_encerrar_projeto_com_tela_exige_build_navegador_e_visual(site):
    root, conv = site
    taskdb.create_feature(conv, "Home", "", [{"title": "Home", "contract": {"goal": "home"}}])
    _conclui("TASK-001", conv)
    _ferramenta(conv, "run_command", "exit code: 0\n1 passed", {"command": "npm test"})  # validou, mas pouco
    with pytest.raises(ToolError) as falta:
        projstate.SESSION_NOTE.handler(None, {"objective": "Home"})
    assert "build" in str(falta.value) and "browser_validate" in str(falta.value) and "visual_review" in str(falta.value)
    _ferramenta(conv, "run_command", "exit code: 0\nbuilt in 1s", {"command": "npm run build"})
    _ferramenta(conv, "browser_validate", "ERROS DE CONSOLE: 1\n[x] pageerror: boom")
    _ferramenta(conv, "visual_review", "Revisão visual (m):\nVEREDITO: ok")
    with pytest.raises(ToolError, match="erros de console"):
        projstate.SESSION_NOTE.handler(None, {"objective": "Home"})
    _ferramenta(conv, "browser_validate", "ERROS DE CONSOLE: 0 (nenhum)")
    assert "encerrada" in projstate.SESSION_NOTE.handler(None, {"objective": "Home"})


def test_revisao_visual_indisponivel_e_dita_e_nao_trava(site):
    _, conv = site
    taskdb.create_feature(conv, "Home", "", [{"title": "Home", "contract": {"goal": "home"}}])
    _conclui("TASK-001", conv)
    _ferramenta(conv, "run_command", "exit code: 0", {"command": "npm run build"})
    _ferramenta(conv, "browser_validate", "ERROS DE CONSOLE: 0 (nenhum)")
    _ferramenta(conv, "visual_review", "REVISÃO VISUAL INDISPONÍVEL: nenhum modelo com visão. O visual NÃO foi julgado.")
    assert "encerrada" in projstate.SESSION_NOTE.handler(None, {"objective": "Home"})


def test_projeto_sem_tela_nao_muda(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAESTRO_BROWSER", True)
    assert qualidade.faltas_para_entregar(1, None, tmp_path) == []


def _fotos(monkeypatch):
    from app import browser
    fotos = []

    async def navega(_root, args):
        return "ok"

    async def foto(_root, args):
        fotos.append((args["largura"], args["altura"]))
        return {"text": "ok", "attachments": [{"kind": "image", "path": f"p{len(fotos)}.jpg"}]}

    monkeypatch.setattr(browser, "navigate", navega)
    monkeypatch.setattr(browser, "screenshot", foto)
    return fotos


def test_visual_review_sem_modelo_mostra_os_prints_e_avisa(site, monkeypatch):
    fotos = _fotos(monkeypatch)
    monkeypatch.setattr(config, "MAESTRO_VISUAL", {"provider": "", "model": ""})
    out = asyncio.run(qualidade._visual_review(site[0], {"urls": ["http://localhost:5173/"]}))
    assert fotos == [(1280, 720), (390, 844)] and len(out["attachments"]) == 2
    assert "INDISPONÍVEL" in out["text"]


def test_visual_review_reprovado_vira_tarefa(site, monkeypatch):
    _, conv = site
    taskdb.create_feature(conv, "Home", "", [{"title": "Home", "contract": {"goal": "home"}}])
    _fotos(monkeypatch)
    monkeypatch.setattr(config, "MAESTRO_VISUAL", {"provider": "local", "model": "visao"})

    async def visao(spec, texto, anexos):
        assert len(anexos) == 2 and "mobile" in texto
        return "VEREDITO: ajustar\n- mobile: botão Comprar cortado na direita\n- desktop: título sem contraste"

    monkeypatch.setattr(qualidade, "_pergunta_a_visao", visao)
    out = asyncio.run(qualidade._visual_review(site[0], {"urls": ["http://localhost:5173/"]}))
    assert "VEREDITO: ajustar" in out["text"] and "Criei a TASK-002" in out["text"]
    assert "botão Comprar cortado" in taskdb.get("TASK-002", conv).contract["requirements"][0]


def test_visual_review_so_existe_com_a_validacao_no_navegador(monkeypatch):
    nomes = {t.name for t in agent.available_tools({"vision"}, "auto", maestro_mode=True)}
    assert "visual_review" in nomes
    monkeypatch.setattr(config, "MAESTRO_BROWSER", False)
    nomes = {t.name for t in agent.available_tools({"vision"}, "auto", maestro_mode=True)}
    assert "visual_review" not in nomes
    assert "Projeto com tela" not in agent.system_prompt("native", {"vision"}, permission="auto", maestro_mode=True)


def test_site_novo_exige_guia_pelo_plano(tmp_path, monkeypatch):
    """Pasta vazia (site ainda não existe): o plano com .html/.css já conta como projeto com tela."""
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    (tmp_path / "FORJA.md").write_text("# Cafeteria\nsite\n", "utf-8")
    projstate.ensure(tmp_path)
    with db.session() as s:
        c = db.Conversation(title="Site", kind="maestro", workspace=str(tmp_path))
        s.add(c)
        s.commit()
        conv = c.id
    token = taskdb.CONV.set(conv)
    try:
        with pytest.raises(ToolError, match="guia visual"):
            taskdb.PLAN_FEATURE.handler(None, {"tasks": [{"title": "Landing", "contract": {
                "goal": "landing", "relevant_files": ["index.html", "style.css"]}}]})
        assert "TASK-001" in taskdb.PLAN_FEATURE.handler(None, {"tasks": [{"title": "Script", "contract": {
            "goal": "cli", "relevant_files": ["main.py"]}}]})
    finally:
        taskdb.CONV.reset(token)


def test_lembrete_volta_quando_as_provas_se_completam(site, monkeypatch):
    """Rodada real: revisão visual aprovou, a Maestro escreveu o resumo e não encerrou."""
    from app import llm, modelctl
    root, conv = site
    taskdb.create_feature(conv, "Home", "", [{"title": "Home", "contract": {"goal": "home"}}])
    _conclui("TASK-001", conv)
    lembretes = []
    passos = []

    async def stream(provider, model, messages, tools, *a, **k):
        passos.append(1)
        ultimo = str(messages[-1].get("content") or "")
        lembretes.append(ultimo)
        if len(passos) == 2:  # depois do 1º lembrete, as provas aparecem (o usuário/Worker fez o resto)
            _ferramenta(conv, "run_command", "exit code: 0", {"command": "npm run build"})
            _ferramenta(conv, "browser_validate", "ERROS DE CONSOLE: 0 (nenhum)")
            _ferramenta(conv, "visual_review", "Revisão visual (m):\nVEREDITO: ok")
        yield "content", "Pronto."
        yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    async def limite(*a):
        return 131072

    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(llm, "context_limit", limite)
    monkeypatch.setitem(config.PROVIDERS, "local", {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    monkeypatch.setattr(modelctl, "carregado", lambda spec: True)

    async def cena():
        req = agent.RunRequest(content="valide", provider="local", model="m", mode="maestro", permission="bypass")
        return [ev async for ev in agent.run_agent(conv, req, agent.Run(conv))]

    asyncio.run(cena())
    assert "Ainda falta" in lembretes[1]
    assert any("todas as provas" in l and "session_note" in l for l in lembretes[2:])

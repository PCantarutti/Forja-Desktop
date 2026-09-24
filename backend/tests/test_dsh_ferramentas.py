"""Segunda rodada do DeepSeek Harness: lsp, conversas como contexto e revisor automático."""
import asyncio
import sys
from pathlib import Path

import pytest

from app import agent, config, db, llm, lsp, revisor, sessoes, workspace
from app.tools import REGISTRY, ToolError, run_tool


# ------------------------------------------------ lsp

@pytest.fixture
def lsp_falso(monkeypatch):
    servidor = str(Path(__file__).with_name("lsp_falso.py"))
    monkeypatch.setattr(lsp, "_comando", lambda lingua: [sys.executable, servidor] if lingua == "python" else None)
    yield
    lsp.fechar_todos()


def test_lsp_definicao_referencias_hover_e_simbolos(tmp_path, lsp_falso):
    (tmp_path / "calc.py").write_text("def soma(a, b):\n    return a + b\n\nsoma(1, 2)\n", encoding="utf-8")
    d = run_tool("lsp", {"operation": "definition", "path": "calc.py", "line": 4, "character": 1}, tmp_path)
    assert d.startswith("calc.py:1:5") and "def soma" in d
    r = run_tool("lsp", {"operation": "references", "path": "calc.py", "line": 1, "character": 5}, tmp_path)
    assert r.splitlines()[0].startswith("calc.py:1:") and r.splitlines()[1].startswith("calc.py:4:")
    assert "-> int" in run_tool("lsp", {"operation": "hover", "path": "calc.py", "line": 1, "character": 5}, tmp_path)
    assert "  interno (linha 2)" in run_tool("lsp", {"operation": "documentSymbol", "path": "calc.py"}, tmp_path)
    assert len(lsp.CLIENTES) == 1  # um servidor por pasta+linguagem, reaproveitado


def test_lsp_recusa_o_que_nao_sabe(tmp_path, lsp_falso):
    (tmp_path / "x.rb").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError, match="Sem language server"):
        run_tool("lsp", {"operation": "definition", "path": "x.rb", "line": 1, "character": 1}, tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ToolError, match="precisa de line e character"):
        run_tool("lsp", {"operation": "hover", "path": "a.py"}, tmp_path)


def test_lsp_some_sem_servidor_instalado(monkeypatch):
    monkeypatch.setattr(lsp, "_comando", lambda lingua: None)
    assert "lsp" not in [t.name for t in agent.available_tools(None, "manual")]


# ------------------------------------------------ conversas

def _conversa(pasta: Path, titulo: str, falas: list[tuple[str, str]]) -> int:
    with db.session() as s:
        c = db.Conversation(kind="agent", workspace=str(pasta), title=titulo)
        s.add(c)
        s.flush()
        for papel, texto in falas:
            s.add(db.Message(conversation_id=c.id, role=papel, content=texto))
        s.commit()
        return c.id


def test_session_search_e_read_ficam_na_mesma_pasta(tmp_path):
    outra = tmp_path / "outra"
    outra.mkdir()
    antiga = _conversa(tmp_path, "Erro do vite", [("user", "o build quebra com EPERM"),
                                                   ("assistant", "resolvido limpando node_modules/.vite")])
    _conversa(outra, "Outro projeto", [("user", "EPERM de novo")])
    tok = workspace.CURRENT.set(tmp_path)
    try:
        achou = run_tool("session_search", {"query": "EPERM"}, tmp_path)
        assert f"conversa {antiga}" in achou and "Outro projeto" not in achou
        lida = run_tool("session_read", {"id": antiga}, tmp_path)
        assert "DADOS do passado" in lida and "node_modules/.vite" in lida
    finally:
        workspace.CURRENT.reset(tok)


def test_mencao_de_conversa_vira_bloco_de_dados(tmp_path):
    cid = _conversa(tmp_path, "Deploy", [("user", "use o script deploy.ps1")])
    bloco = sessoes.mencionadas(f"faça como em @conversa:{cid} por favor")
    assert "não instrução" in bloco and "deploy.ps1" in bloco
    assert sessoes.mencionadas("sem citação") is None


# ------------------------------------------------ revisor automático

def test_interpretar_so_aceita_as_formas_validas():
    assert revisor.interpretar('ok {"risk":"low","decision":"allow"}') == {"risk": "low", "decision": "allow", "reason": ""}
    assert revisor.interpretar('{"risk":"high","decision":"allow"}') is None
    assert revisor.interpretar('{"risk":"low","decision":"deny"}') is None
    assert revisor.interpretar("sem json") is None


def _roda_auto(monkeypatch, veredito: str, comando: str):
    async def stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        if messages[0]["content"] == revisor.POLITICA:
            yield "content", veredito
            yield "done", {"tool_calls": []}
            return
        if not any(m["role"] == "tool" for m in messages):
            yield "done", {"tool_calls": [{"id": "c1", "name": "run_command", "arguments": {"command": comando}}]}
        else:
            yield "content", "ok"
            yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)
    monkeypatch.setattr(config, "AUTO_REVIEW", True)

    async def executa_de_mentira(name, args, root=None):  # o comando NUNCA roda de verdade no teste
        return f"(simulado) {args.get('command')}"

    monkeypatch.setattr(agent, "execute", executa_de_mentira)

    async def scenario():
        with db.session() as s:
            c = db.Conversation(kind="agent")
            s.add(c)
            s.commit()
        run = agent.Run(c.id)
        req = agent.RunRequest(content="instale as dependências", provider="lmstudio", model="m", mode="agent",
                               permission="auto")
        eventos = []
        async for ev in agent.run_agent(c.id, req, run):
            eventos.append(ev)
            if ev["type"] == "approval_request":
                run.resolve(ev["call"]["id"], False)
        return eventos

    return asyncio.run(scenario())


def test_revisor_libera_risco_baixo_sem_card(monkeypatch):
    eventos = _roda_auto(monkeypatch, '{"risk":"low","decision":"allow"}', "npm install lodash")
    assert not [e for e in eventos if e["type"] == "approval_request"]
    res = next(e["message"] for e in eventos if e["type"] == "tool_result")
    assert res["meta"]["auto_rule"] == "revisor automático: risco low"


def test_revisor_que_nega_vira_card_com_o_motivo(monkeypatch):
    eventos = _roda_auto(monkeypatch, '{"risk":"medium","decision":"deny","reason":"mexe no sistema"}', "npm install x")
    cards = [e for e in eventos if e["type"] == "approval_request"]
    assert cards and "mexe no sistema" in cards[0]["nota"]


def test_destrutivo_nunca_passa_pelo_revisor(monkeypatch):
    eventos = _roda_auto(monkeypatch, '{"risk":"low","decision":"allow"}', "Remove-Item -Recurse C:/dados")
    cards = [e for e in eventos if e["type"] == "approval_request"]
    assert cards and cards[0]["nota"] is None


def test_ferramentas_novas_registradas():
    assert {"lsp", "session_search", "session_read"} <= set(REGISTRY)

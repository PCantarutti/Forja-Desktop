"""Timeout por ferramenta, terminal persistente do agente e /skill resolvido no backend."""
import asyncio
import sys
import time

import pytest

from app import agent, config, db, llm, policy, shell, skills, terminal
from app.tools import REGISTRY, Tool, ToolError, register, run_tool

WIN = sys.platform == "win32"


@pytest.fixture
def conv_ctx():
    tok = shell.CONV.set("teste-terminal")
    yield
    for tid in list(terminal._meus()):
        terminal.close(tid)
        terminal.AGENTE.pop(tid, None)
    shell.CONV.reset(tok)


def test_terminal_guarda_estado_entre_chamadas(tmp_path, conv_ctx):
    (tmp_path / "sub").mkdir()
    out = run_tool("terminal_open", {"name": "main"}, tmp_path)
    tid = out.split("'")[1]
    run_tool("terminal_send", {"id": tid, "command": "cd sub", "wait": 5}, tmp_path)
    definir = "$env:FORJA_X = 'lembrou'" if WIN else "export FORJA_X=lembrou"
    run_tool("terminal_send", {"id": tid, "command": definir, "wait": 5}, tmp_path)
    ler = "echo $env:FORJA_X; (Get-Location).Path" if WIN else "echo $FORJA_X; pwd"
    saida = run_tool("terminal_send", {"id": tid, "command": ler, "wait": 10}, tmp_path)
    assert "lembrou" in saida and "sub" in saida  # variável e pasta sobreviveram entre chamadas
    assert tid in run_tool("terminal_list", {}, tmp_path)
    run_tool("terminal_close", {"id": tid}, tmp_path)
    with pytest.raises(ToolError, match="não existe"):
        run_tool("terminal_read", {"id": tid}, tmp_path)


def test_terminal_de_outra_conversa_nao_aparece(tmp_path, conv_ctx):
    tid = run_tool("terminal_open", {}, tmp_path).split("'")[1]
    tok = shell.CONV.set("outra")
    try:
        with pytest.raises(ToolError, match="não existe"):
            run_tool("terminal_send", {"id": tid, "command": "echo oi"}, tmp_path)
    finally:
        shell.CONV.reset(tok)


def test_terminal_send_passa_pela_politica_do_shell():
    t = REGISTRY["terminal_send"]
    assert policy.decide(t, {"id": "x", "command": "Remove-Item -Recurse C:/x"}, "bypass")[0]  # destrutivo pergunta
    assert not policy.decide(t, {"id": "x", "command": "git status"}, "auto")[0]  # leitura passa no automático
    assert policy.decide(t, {"id": "x", "command": "npm install"}, "auto")[0]


def test_ferramenta_que_passa_do_teto_e_interrompida(monkeypatch):
    def lenta(root, args):
        time.sleep(3)
        return "tarde"

    register(Tool("lenta_teste", "x", {"type": "object", "properties": {}}, lenta, timeout=0.5))
    passo = {"n": 0}

    async def stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        passo["n"] += 1
        if passo["n"] == 1:
            yield "done", {"tool_calls": [{"id": "l1", "name": "lenta_teste", "arguments": {}}]}
        else:
            yield "content", "ok"
            yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)

    async def scenario():
        with db.session() as s:
            c = db.Conversation(kind="agent")
            s.add(c)
            s.commit()
        run = agent.Run(c.id)
        req = agent.RunRequest(content="x", provider="lmstudio", model="m", mode="agent", permission="bypass")
        t0 = time.monotonic()
        async for ev in agent.run_agent(c.id, req, run):
            if ev["type"] == "tool_result":
                return ev["message"], time.monotonic() - t0

    try:
        res, demorou = asyncio.run(scenario())
        assert res["status"] == "erro" and "foi interrompida" in res["content"]
        assert demorou < 2.5  # o turno segue sem esperar a ferramenta travada
    finally:
        REGISTRY.pop("lenta_teste", None)


def test_padroes_de_timeout():
    assert REGISTRY["read_file"].timeout == 0 and config.TOOL_TIMEOUT > 0  # 0 = usa o padrão
    assert REGISTRY["run_command"].timeout is None and REGISTRY["terminal_send"].timeout is None


def test_barra_skill_vira_bloco_para_o_modelo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "dados")
    d = tmp_path / ".forja" / "skills" / "deploy"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\ndescription: sobe\n---\nFaça deploy de $ARGUMENTS.", encoding="utf-8")
    bloco = skills.invocada(tmp_path, "/deploy produção")
    assert "chamou a skill /deploy com: produção" in bloco and "Faça deploy de produção." in bloco
    assert str(d) in bloco
    assert "skill_resources" not in skills.invocada(tmp_path, "/revisar")  # as do Forja não têm pasta
    assert skills.invocada(tmp_path, "/inexistente") is None
    assert skills.invocada(tmp_path, "texto normal") is None
    assert skills.invocada(tmp_path, "/compactar") is None  # ação da interface, não skill de prompt


def test_maestro_recebe_as_regras_novas():
    p = agent.prompt_base("native", set(), maestro_mode=True)
    for trecho in ("Para achar código use grep", "Resultado grande demais", "Contexto atual de execução",
                   "Terminal persistente"):
        assert trecho in p


def test_skill_inline_no_meio_do_texto(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "dados")
    d = tmp_path / ".forja" / "skills" / "seo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("Use meta tags.", encoding="utf-8")
    bloco = skills.invocada(tmp_path, "landing de velas /skill:gerar-imagens com /skill:seo e /skill:nada")
    assert "/skill:gerar-imagens, /skill:seo" in bloco and "imagens_pendentes" in bloco and "Use meta tags." in bloco
    assert "nada" not in bloco.split("\n")[0]
    assert skills.invocada(tmp_path, "url a/skill:seo") is None  # só conta com espaço antes


def test_skills_do_usuario_pelas_configuracoes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "dados")
    skills.salvar_do_usuario("revisar-textos", "Revisa\nortografia", "Corrija a ortografia de $ARGUMENTS.")
    lista = {s["name"]: s for s in skills.para_configuracoes(tmp_path)}
    minha = lista["revisar-textos"]
    assert minha["origem"] == "usuario" and minha["editavel"] and minha["description"] == "Revisa ortografia"
    assert lista["gerar-imagens"]["origem"] == "forja" and not lista["gerar-imagens"]["editavel"]
    assert "Corrija a ortografia de a ata." in skills.invocada(tmp_path, "/revisar-textos a ata")  # já vale no chat
    skills.salvar_do_usuario("revisar-texto", "", "Outra coisa.", antigo="revisar-textos")  # renomear
    assert not (tmp_path / "dados" / "skills" / "revisar-textos").exists()
    with pytest.raises(ValueError, match="comando do Forja"):
        skills.salvar_do_usuario("commit", "", "x")
    with pytest.raises(ValueError, match="minúsculas"):
        skills.salvar_do_usuario("../fora", "", "x")
    skills.apagar_do_usuario("revisar-texto")
    assert "revisar-texto" not in {s["name"] for s in skills.para_configuracoes(tmp_path)}
    with pytest.raises(ValueError, match="não é sua"):
        skills.apagar_do_usuario("gerar-imagens")

"""Subagentes com persona: `.forja/agents/*.md` define nível, ferramentas e instruções."""
import asyncio

from app import agent, db, llm, subagents, workspace


def _escreve(root, nome, texto):
    pasta = root / subagents.AGENTS_DIR
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{nome}.md").write_text(texto, encoding="utf-8")


def test_agents_for_reads_header_and_body(tmp_path):
    _escreve(tmp_path, "revisor", "---\ndescription: Revisa diff\nlevel: capaz\ntools: read_file, search\n---\n"
                                  "Você revisa código. Não edite nada.\n")
    _escreve(tmp_path, "vazio", "---\ndescription: nada\n---\n\n")  # sem corpo: não vira subagente
    agents = subagents.agents_for(tmp_path)
    assert list(agents) == ["revisor"]
    a = agents["revisor"]
    assert a["level"] == "capaz" and a["tools"] == ["read_file", "search"]
    assert a["description"] == "Revisa diff" and "Não edite nada" in a["prompt"]
    assert subagents.agents_for(tmp_path / "nao-existe") == {}


def test_unknown_level_falls_back_to_rapido(tmp_path):
    _escreve(tmp_path, "x", "---\nlevel: nuvem\n---\nfaça algo\n")  # nuvem é rede de segurança, não escolha
    assert subagents.agents_for(tmp_path)["x"]["level"] == "rapido"


def test_persona_names_reach_the_main_prompt(tmp_path, monkeypatch):
    _escreve(tmp_path, "buscador", "---\ndescription: Acha onde está X\n---\nProcure e responda em uma linha.\n")
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    monkeypatch.setattr(subagents, "configured", lambda: {"rapido": {"provider": "p", "model": "m"}})
    p = agent.system_prompt("native", permission="edits")
    assert "buscador (Acha onde está X)" in p and "delegate_task(agent=" in p


def test_persona_restricts_tools_and_appends_its_prompt(tmp_path, monkeypatch):
    _escreve(tmp_path, "revisor", "---\nlevel: capaz\ntools: read_file\n---\nNão edite nada, só aponte problemas.\n")
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    monkeypatch.setattr(db, "get_model_setting", lambda m: {"tool_mode": "native", "vision": "auto"})

    async def caps(*a):
        return None

    monkeypatch.setattr(llm, "capabilities", caps)
    persona = subagents.agents_for(tmp_path)["revisor"]

    async def scenario():
        run = agent.Run(1)
        run.permission = "edits"
        return await subagents._setup({"provider": "p", "model": "m"}, run, "medio", persona)

    via, auto, caps_, tools, schemas, system = asyncio.run(scenario())
    nomes = [t.name for t in tools]
    assert "read_file" in nomes and "edit_file" not in nomes and "run_command" not in nomes
    assert nomes == [s["function"]["name"] for s in schemas]
    assert "ask_user" in nomes  # perguntar ao usuário existe em qualquer modo, persona não tira
    assert "Não edite nada" in system["content"] and "revisor" in system["content"]
    assert "edit_file" not in system["content"]  # ferramenta fora da persona nem aparece nas regras

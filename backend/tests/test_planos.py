"""Modo Plano parecido com o do Claude Code: esqueleto do plano, ask_user, plano fixo no prompt, espelho .md."""
import asyncio

from app import agent, db, llm, mirror
from app.tools import REGISTRY


def test_ask_user_available_in_every_agent_mode_but_not_in_registry():
    assert "ask_user" in [t.name for t in agent.available_tools(None, "plan")]
    assert "ask_user" in [t.name for t in agent.available_tools(None, "manual")]
    assert "ask_user" not in REGISTRY  # não aparece nas Configurações nem entra nas regras de permissão


def test_plan_prompt_has_skeleton_and_ask_user():
    p = agent.system_prompt("native", permission="plan")
    for sec in ("## Contexto", "## Abordagem", "## Passos", "## Verificação", "## Riscos"):
        assert sec in p
    assert "ask_user" in p
    assert "| Arquivo | Mudança |" in p  # plano rico: tabela quando forem vários arquivos
    assert "de uma em uma" in p          # e perguntas em lote, não uma por rodada


def test_approved_plan_is_pinned_only_outside_plan_mode():
    assert "1. criar x" in agent.system_prompt("native", permission="edits", plan="## Passos\n1. criar x")
    assert "1. criar x" not in agent.system_prompt("native", permission="plan", plan="## Passos\n1. criar x")
    assert "Plano aprovado" not in agent.system_prompt("native", permission="edits")


def test_last_plan_picks_latest_approved():
    class M:
        def __init__(self, role, name=None, meta=None):
            self.role, self.name, self.meta = role, name, meta

    msgs = [M("tool", "exit_plan_mode", {"approved": True, "plan": "A"}),
            M("tool", "exit_plan_mode", {"approved": False, "plan": "B"}), M("user")]
    assert agent.last_plan(msgs) == "A"
    assert agent.last_plan([M("user")]) is None


def test_ask_user_asks_everything_in_one_card(monkeypatch):
    """Até 4 perguntas numa chamada só: um card, todas as respostas voltam juntas ao modelo."""
    perguntas = [{"header": "Banco", "question": "Qual banco?",
                  "options": [{"label": "postgres", "description": "já usado no time"}, {"label": "sqlite"}]},
                 {"header": "Extras", "question": "O que mais entra?", "multi_select": True,
                  "options": [{"label": "cache"}, {"label": "fila"}]},
                 {"header": "Deploy", "question": "Onde sobe?", "options": [{"label": "docker"}, {"label": "vps"}]}]
    step = {"n": 0}

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        step["n"] += 1
        if step["n"] == 1:
            assert "ask_user" in [t["function"]["name"] for t in (tools or [])]
            yield "done", {"tool_calls": [{"id": "q1", "name": "ask_user", "arguments": {"questions": perguntas}}]}
        else:
            texto = "\n".join(str(m.get("content", "")) for m in messages)
            assert "- Qual banco?: postgres" in texto
            assert "- O que mais entra?: cache, fila" in texto  # múltipla escolha vira uma linha
            assert "- Onde sobe?: (sem resposta)" in texto      # pulada
            yield "content", "Fechado."
            yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)

    async def scenario():
        with db.session() as s:
            c = db.Conversation(kind="agent")
            s.add(c)
            s.commit()
            conv = c.id
        run = agent.Run(conv)
        req = agent.RunRequest(content="crie o modelo", provider="lmstudio", model="m", mode="agent", permission="edits")
        asked = []
        async for ev in agent.run_agent(conv, req, run):
            if ev["type"] == "question_request":
                asked.append(ev)
                run.resolve(ev["call"]["id"], {"approved": True, "answers": ["postgres", ["cache", "fila"], ""]})
        return asked, conv

    asked, conv = asyncio.run(scenario())
    assert len(asked) == 1  # um card só, não um por pergunta
    assert [q["question"] for q in asked[0]["questions"]] == ["Qual banco?", "O que mais entra?", "Onde sobe?"]
    assert asked[0]["questions"][1]["multi_select"] and asked[0]["questions"][0]["options"][0]["description"]
    with db.session() as s:
        md = mirror.markdown(s.get(db.Conversation, conv))
    assert "**Pergunta:** Qual banco?" in md and "**Resposta:** postgres" in md
    assert "**Pergunta:** Onde sobe?" in md


def test_ask_user_accepts_the_old_single_question_shape():
    """Conversa salva antes do lote (ou modelo que simplifica) continua virando uma pergunta."""
    qs = agent.ask_questions({"question": "Qual banco?", "options": ["postgres", "sqlite"]})
    assert len(qs) == 1 and qs[0]["question"] == "Qual banco?"
    assert qs[0]["options"] == [{"label": "postgres", "description": ""}, {"label": "sqlite", "description": ""}]
    assert agent.ask_questions({"questions": [{"question": "  ", "options": []}]}) == []


def test_open_question_without_options_still_works():
    """Nem toda dúvida tem alternativa a listar: sem opções o card vira só campo de digitação."""
    qs = agent.ask_questions({"questions": [{"question": "Qual o prazo?"}]})
    assert len(qs) == 1 and qs[0]["options"] == []
    assert agent.ask_questions({"questions": [{"question": "Qual banco?", "options": [{"label": "postgres"}]}]})[0][
        "options"] == [{"label": "postgres", "description": ""}]  # opção sem explicação não quebra


def test_ask_user_tool_demands_options_with_a_recommendation():
    """O que faz o modelo mandar opções explicadas é o texto da ferramenta e as regras do prompt."""
    tool = next(t for t in agent.available_tools(None, "plan") if t.name == "ask_user")
    assert "(Recomendado)" in tool.description
    opcao = tool.parameters["properties"]["questions"]["items"]["properties"]["options"]
    assert opcao["items"]["required"] == ["label", "description"]  # explicação obrigatória em cada opção
    assert tool.parameters["properties"]["questions"]["items"]["required"] == ["question"]  # pergunta aberta vale
    for modo in ("plan", "edits"):
        p = agent.system_prompt("native", permission=modo)
        assert "(Recomendado)" in p and "uma linha" in p


def test_approved_plan_survives_into_next_turn(monkeypatch):
    step = {"n": 0}

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        step["n"] += 1
        if step["n"] == 1:
            yield "done", {"tool_calls": [{"id": "p1", "name": "exit_plan_mode",
                                           "arguments": {"plan": "## Passos\n1. escrever a.txt"}}]}
        elif step["n"] == 2:
            assert any("Plano aprovado" in str(m["content"]) and "1. escrever a.txt" in str(m["content"]) for m in messages)  # plano no contexto após aprovar
            yield "content", "Feito."
            yield "done", {"tool_calls": []}
        else:
            assert any("Plano aprovado" in str(m["content"]) and "1. escrever a.txt" in str(m["content"]) for m in messages)  # e no turno seguinte, em outro run
            yield "content", "Continuando."
            yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)

    async def scenario():
        with db.session() as s:
            c = db.Conversation(kind="agent")
            s.add(c)
            s.commit()
            conv = c.id
        run = agent.Run(conv)
        req = agent.RunRequest(content="faça", provider="lmstudio", model="m", mode="agent", permission="plan")
        async for ev in agent.run_agent(conv, req, run):
            if ev["type"] == "plan_request":
                run.resolve(ev["call"]["id"], {"approved": True, "mode": "edits"})
        assert run.plan and "a.txt" in run.plan
        run2 = agent.Run(conv)
        req2 = agent.RunRequest(content="e agora?", provider="lmstudio", model="m", mode="agent", permission="edits")
        async for _ in agent.run_agent(conv, req2, run2):
            pass
        return run2, conv

    run2, conv = asyncio.run(scenario())
    assert run2.plan and "a.txt" in run2.plan
    with db.session() as s:
        md = mirror.markdown(s.get(db.Conversation, conv))
    assert "### Plano (aprovado, modo edits)" in md and "1. escrever a.txt" in md


def test_plan_written_as_text_becomes_a_plan_card(monkeypatch):
    """Modelo que ignora exit_plan_mode e escreve o plano na resposta: o card aparece mesmo assim."""
    texto = ("## Contexto\nNada no repo.\n\n## Abordagem\nLaravel + Postgres.\n\n## Passos\n"
             + "\n".join(f"{i}. passo {i} em arquivo{i}.php" for i in range(1, 20))
             + "\n\n## Verificação\nRodar phpunit.\n\n## Riscos e dúvidas\nMulti-tenancy.\n")
    step = {"n": 0}

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        step["n"] += 1
        if step["n"] == 1:
            yield "content", texto
            yield "done", {"tool_calls": []}
        else:
            yield "content", "Feito."
            yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)

    async def scenario():
        with db.session() as s:
            c = db.Conversation(kind="agent")
            s.add(c)
            s.commit()
            conv = c.id
        run = agent.Run(conv)
        req = agent.RunRequest(content="planeje", provider="lmstudio", model="m", mode="agent", permission="plan")
        cards = []
        async for ev in agent.run_agent(conv, req, run):
            if ev["type"] == "plan_request":
                cards.append(ev)
                run.resolve(ev["call"]["id"], {"approved": True, "mode": "edits"})
        return cards, run

    cards, run = asyncio.run(scenario())
    assert len(cards) == 1 and "## Passos" in cards[0]["plan"]
    assert run.plan and "passo 1" in run.plan


def test_short_answer_in_plan_mode_is_not_turned_into_a_plan():
    from app.parsing import looks_like_plan
    assert not looks_like_plan("Pronto, o arquivo já existe.")
    assert not looks_like_plan("## Contexto\nrepo vazio.")  # curto demais: resposta comum, não plano

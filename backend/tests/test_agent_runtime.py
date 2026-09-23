import asyncio

from app import compact
from app import agent as agent_mod
from app.agent import Run, build_history
from app.db import Message


def msg(id, role, content="", **kw):
    return Message(id=id, role=role, content=content, thinking="", **kw)


def conversation():
    return [
        msg(1, "user", "crie a.py"),
        msg(2, "assistant", "", tool_calls=[{"id": "c1", "name": "write_file", "arguments": {"path": "a.py"}}]),
        msg(3, "tool", "Arquivo criado", tool_call_id="c1", name="write_file", status="ok"),
        msg(4, "assistant", "Criei a.py."),
        msg(5, "user", "agora b.py"),
        msg(6, "assistant", "Feito b.py."),
        msg(7, "user", "e c.py"),
        msg(8, "assistant", "Feito c.py."),
    ]


# ------------------------------------------------ compactação

def test_split_point_keeps_last_two_turns():
    assert compact.split_point(conversation()) == 4  # resume até antes do penúltimo "user"


def test_split_point_nothing_to_do():
    assert compact.split_point(conversation()[:6]) is None  # só 2 turnos


def test_transcript_includes_tool_calls_and_results():
    t = compact.transcript(conversation(), until=4, max_chars=10_000)
    assert "USUÁRIO: crie a.py" in t and "chamou write_file" in t and "RESULTADO write_file [ok]" in t
    assert "b.py" not in t


def test_history_after_summary_starts_with_summary():
    msgs = conversation() + [msg(9, "event", "Resumo X", meta={"kind": "summary", "covers_until": 4})]
    hist = build_history(msgs, "native")
    assert hist[0]["role"] == "system"
    assert hist[1]["role"] == "user" and "Resumo X" in hist[1]["content"] and "agora b.py" in hist[1]["content"]
    assert all("crie a.py" not in m.get("content", "") for m in hist)
    assert not any(m["role"] == "tool" for m in hist)  # par tool_call/resultado saiu junto


def test_second_compaction_starts_after_previous_summary():
    msgs = conversation() + [msg(9, "event", "Resumo X", meta={"kind": "summary", "covers_until": 4}),
                             msg(10, "user", "d.py"), msg(11, "assistant", "ok")]
    assert compact.split_point(msgs) == 6
    t = compact.transcript(msgs, 6, 10_000)
    assert t.startswith("[Resumo anterior]\nResumo X") and "agora b.py" in t and "crie a.py" not in t


# ------------------------------------------------ execução desacoplada

def test_run_buffer_replay_and_snapshot():
    async def scenario():
        run = Run(conv_id=1)
        await run.publish({"type": "assistant_start"})
        await run.publish({"type": "token", "text": "Olá"})
        await run.publish({"type": "approval_request", "call": {"id": "c1"}, "preview": None})
        snap = run.snapshot()
        assert snap["cursor"] == 3 and snap["draft"]["content"] == "Olá" and snap["approvals"][0]["call"]["id"] == "c1"

        got = []

        async def reader():
            async for ev in run.subscribe(snap["cursor"]):  # reconexão: só o que vem depois
                got.append(ev["type"])

        task = asyncio.create_task(reader())
        await asyncio.sleep(0)
        await run.publish({"type": "tool_result", "message": {"tool_call_id": "c1"}})
        await run.publish({"type": "done"})
        async with run._changed:
            run.finished = True
            run._changed.notify_all()
        await asyncio.wait_for(task, 2)
        assert got == ["tool_result", "done"] and run.snapshot()["approvals"] == []

    asyncio.run(scenario())


# ------------------------------------------------ loop com LLM falso

def test_retry_and_writing_shell_always_asks(monkeypatch):
    from app import agent, db, llm

    calls = {"n": 0}

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:  # conexão cai antes do 1º token
            raise llm.LLMError("Conexão interrompida.")
        if calls["n"] == 2:
            # `npm install` e não `ls`: comando só de leitura passa direto no Automático desde que
            # `safe_command` foi ligado ao policy.decide — quem continua perguntando é quem escreve.
            yield "done", {"tool_calls": [{"id": "c1", "name": "run_command",
                                           "arguments": {"command": "npm install"}}],
                           "prompt_tokens": 10, "completion_tokens": 5}
        else:
            yield "content", "Pronto."
            yield "done", {"tool_calls": [], "prompt_tokens": 20, "completion_tokens": 2}

    async def fake_limit(*a):
        return 32768

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", fake_limit)
    monkeypatch.setattr(agent, "RETRY_DELAY", 0)

    async def scenario():
        with db.session() as s:
            c = db.Conversation()
            s.add(c)
            s.commit()
            conv_id = c.id
        run = agent.Run(conv_id)
        req = agent.RunRequest(content="liste", provider="lmstudio", model="m", mode="agent", permission="auto")
        types = []
        async for ev in agent.run_agent(conv_id, req, run):
            types.append(ev["type"])
            if ev["type"] == "approval_request":  # shell que escreve pede aprovação mesmo em "automático"
                run.resolve("c1", False)
        return types

    types = asyncio.run(scenario())
    assert calls["n"] == 3
    assert "approval_request" in types and types[-1] == "done"



def test_argumentos_de_tool_call_saem_ao_vivo(monkeypatch):
    """Escrever um arquivo grande levava minutos sem um evento sequer: a tela ficava parada e nao
    dava para saber se o modelo tinha travado. Agora cada pedaco dos argumentos vira 'tool_token'."""
    from app import agent, db, llm

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        yield "tool_args", {"name": "write_file", "text": '{"path":"a.py",'}
        yield "tool_args", {"name": "write_file", "text": '"content":"x = 1"}'}
        yield "done", {"tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}

    async def fake_limit(*a):
        return 32768

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", fake_limit)

    async def scenario():
        with db.session() as s:
            c = db.Conversation()
            s.add(c)
            s.commit()
            conv_id = c.id
        run = agent.Run(conv_id)
        req = agent.RunRequest(content="escreva", provider="lmstudio", model="m", mode="agent", permission="auto")
        return [ev async for ev in agent.run_agent(conv_id, req, run)]

    eventos = asyncio.run(scenario())
    vivos = [e for e in eventos if e["type"] == "tool_token"]
    assert [e["text"] for e in vivos] == ['{"path":"a.py",', '"content":"x = 1"}']
    assert all(e["name"] == "write_file" for e in vivos)


def test_draft_guarda_a_cauda_do_que_esta_sendo_escrito():
    """Quem recarrega a pagina no meio da escrita precisa reencontrar o fim dela, nao uma tela vazia."""
    async def scenario():
        run = Run(conv_id=1)
        await run.publish({"type": "assistant_start"})
        await run.publish({"type": "tool_token", "name": "write_file", "text": "a" * (agent_mod.TOOL_TAIL + 50)})
        await run.publish({"type": "tool_token", "name": "", "text": "FIM"})
        tool = run.snapshot()["draft"]["tool"]
        assert tool["name"] == "write_file"
        assert len(tool["text"]) == agent_mod.TOOL_TAIL and tool["text"].endswith("FIM")

    asyncio.run(scenario())


# ------------------------------------------------ compactação sem janela conhecida


def _conversa_grande(db, texto: str, turnos: int) -> int:
    with db.session() as s:
        c = db.Conversation(kind="agent")
        s.add(c)
        s.commit()
        for i in range(turnos):
            s.add(db.Message(conversation_id=c.id, role="user", content=f"{i} {texto}"))
            s.add(db.Message(conversation_id=c.id, role="assistant", content=f"{i} {texto}"))
        s.commit()
        return c.id


def test_compacta_mesmo_sem_o_provider_informar_a_janela(monkeypatch):
    """`context_limit` devolve None em todo provider OpenAI-compatível genérico.

    Com a condição antiga (`if ctx_max and ...`) a compactação nunca disparava nesses providers: o
    prompt crescia sem teto até o servidor recusar a requisição.
    """
    from app import agent, compact, config, db, llm

    resumos = []

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        if messages[0]["content"].startswith(compact.PROMPT[:40]):
            resumos.append(messages[1]["content"])
            yield "content", "Resumo do que veio antes."
        else:
            yield "content", "Respondido."
        yield "done", {"tool_calls": []}

    async def sem_janela(*a):
        return None  # é o que o provider devolve quando não sabe dizer

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", sem_janela)
    monkeypatch.setattr(llm, "capabilities", sem_janela)
    monkeypatch.setattr(config, "NUM_CTX", 2_000)

    conv = _conversa_grande(db, "palavra " * 200, turnos=12)
    run = agent.Run(conv)
    req = agent.RunRequest(content="e agora?", provider="lmstudio", model="m", mode="agent", permission="manual")

    async def scenario():
        return [ev async for ev in agent.run_agent(conv, req, run)]

    eventos = asyncio.run(scenario())
    resumo = [e for e in eventos
              if e.get("type") == "event" and (e["message"].get("meta") or {}).get("kind") == "summary"]
    assert resumos, "o modelo nem foi chamado para resumir"
    assert resumo, "a conversa estourou o teto e mesmo assim nada foi compactado"


def test_planilha_pedida_vai_pela_ferramenta_e_nao_por_script():
    """Aconteceu em uso: pediram a tabela de um PDF em Excel e o modelo escreveu scripts Python,
    gerou um .csv e tentou `pip install openpyxl` — recusado — e desistiu. write_spreadsheet faria
    em uma chamada, com o openpyxl que já está instalado."""
    p = agent_mod.system_prompt("agent")
    regra = next(l for l in p.splitlines() if l.startswith("- Planilha (.xlsx"))
    assert "write_spreadsheet" in regra and "NUNCA gere esses arquivos por script" in regra
    # O nome da ferramenta de shell só entra quando ela está ligada: citar desligada confunde o modelo.
    assert "run_command" in regra


def test_tabela_na_resposta_sai_em_markdown():
    p = agent_mod.system_prompt("agent")
    assert any("Tabela na resposta vai em Markdown" in l for l in p.splitlines())


def test_geracao_em_curso_sobrevive_a_reabertura():
    """Reabrir uma conversa que ficou rodando zerava o contador de t/s: ele nasce no
    `assistant_start`, que já passou. O snapshot agora carrega a geração em andamento."""
    run = Run(conv_id=1)
    asyncio.run(run.publish({"type": "assistant_start"}))
    for _ in range(7):
        asyncio.run(run.publish({"type": "token", "text": "a"}))

    g = run.snapshot()["geracao"]
    assert g["tokens"] == 7
    assert g["segundos"] >= 0 and g["segundos_gerando"] >= 0  # decorridos, não instantes

    asyncio.run(run.publish({"type": "assistant_end", "message": {"role": "assistant"}}))
    assert run.snapshot()["geracao"] is None  # terminou: vale o meta.stats da mensagem


def test_parar_nao_espera_o_modelo_falar():
    """Aconteceu em uso: clicar em parar não fazia nada, ou demorava demais.

    O laço só olhava o cancelamento quando chegava um pedaço; com o modelo calado (prompt grande,
    imagem enorme, travado) ficava tudo preso no `__anext__`. Aqui o stream nunca entrega nada e o
    cancelamento tem que vencer mesmo assim — e o gerador precisa ser fechado, que é o que derruba
    a conexão com o provedor.
    """
    fechado = []

    async def stream_mudo():
        try:
            await asyncio.Event().wait()  # nunca fala
            yield ("content", "nunca chega")
        finally:
            fechado.append(True)

    async def cenario():
        cancelamento = asyncio.Event()
        recebidos = []

        async def consumir():
            async for ev in agent_mod.ate_cancelar(stream_mudo(), cancelamento):
                recebidos.append(ev)

        tarefa = asyncio.create_task(consumir())
        await asyncio.sleep(0.05)
        assert not tarefa.done()  # de fato preso, como na vida real
        cancelamento.set()
        await asyncio.wait_for(tarefa, timeout=2)  # sem a corrida, isto estoura o timeout
        return recebidos

    assert asyncio.run(cenario()) == []
    assert fechado == [True], "o gerador tem que ser fechado: é o que derruba a conexão do provedor"


def test_parar_no_meio_entrega_o_que_ja_veio():
    """Cancelar não pode perder o que o modelo já escreveu — o texto parcial fica na tela."""
    async def stream():
        yield ("content", "um")
        yield ("content", "dois")
        await asyncio.Event().wait()
        yield ("content", "nunca")

    async def cenario():
        cancelamento = asyncio.Event()
        recebidos = []
        async for ev in agent_mod.ate_cancelar(stream(), cancelamento):
            recebidos.append(ev)
            if len(recebidos) == 2:
                cancelamento.set()
        return recebidos

    assert asyncio.run(asyncio.wait_for(cenario(), timeout=2)) == [("content", "um"), ("content", "dois")]


def test_marca_se_o_modelo_enxerga_a_imagem():
    """Quem manda validar um layout precisa saber se o modelo olhou o print ou chutou pelo texto.

    O backend já sabia (é o que decide mandar a imagem ou um aviso em texto), mas só gravava o caso
    negativo, e a UI não tinha o que mostrar.
    """
    from app.agent import _images

    class Msg:
        def __init__(self, meta): self.meta = meta

    anexo = {"kind": "image", "path": "a.jpg"}
    assert _images(Msg({"attachments": [anexo], "model_sees": True})) == [anexo]
    assert _images(Msg({"attachments": [anexo], "model_sees": False})) == []


def test_toda_ferramenta_tem_uma_frase_de_status():
    """A linha "o que está fazendo agora" tem que nomear o alvo, não a ferramenta.

    Sem esta guarda, ferramenta nova nasce caindo no "Usando <nome>" genérico e ninguém percebe —
    foi como `read_file`, `run_command` e o navegador inteiro ficaram sem frase até alguém reclamar.
    Ferramenta de servidor MCP fica de fora: o nome dela nem existe no código do frontend.
    """
    import re
    from pathlib import Path

    from app.tools import REGISTRY

    fonte = Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.tsx"
    corpo = fonte.read_text(encoding="utf-8")
    mapa = corpo[corpo.index("const FASE:"):corpo.index("export default function App()")]
    com_frase = set(re.findall(r"^  ([a-z_]+): \(", mapa, re.M))

    registradas = {n for n, t in REGISTRY.items() if t.source == "builtin"}
    faltando = sorted(registradas - com_frase)
    assert not faltando, f"sem frase em FASE (App.tsx): {faltando}"
    # exit_plan_mode e ask_user vivem fora do REGISTRY mas aparecem como chamada na conversa
    assert {"exit_plan_mode", "ask_user"} <= com_frase


def test_imagem_antiga_nao_reescreve_o_historico_no_local(tmp_path, monkeypatch):
    """Trocar uma imagem antiga por texto reescreve o histórico NO MEIO, e isso invalida o cache de
    prompt do servidor local dali para a frente: o turno seguinte a um print custava 11s, depois
    45s, 82s, 123s, crescendo com o contexto, enquanto qualquer outra ferramenta ficava em 2-3s.

    Com provider local as imagens ficam e o prefixo nunca muda. Na nuvem continuam saindo, porque
    lá o custo é por imagem em cada requisição.
    """
    import io

    from PIL import Image

    from app import agent as ag
    from app import uploads

    monkeypatch.setattr(uploads.workspace, "root", lambda: tmp_path)
    (tmp_path / "prints").mkdir()
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, "JPEG")

    msgs, ident = [], 0

    def nova(role, **kw):
        nonlocal ident
        ident += 1
        return Message(id=ident, role=role, content=kw.pop("content", ""), thinking="", status="ok", **kw)

    msgs.append(nova("user", content="olhe a página"))
    for _ in range(4):  # quatro prints, bem acima do MAX_TOOL_IMAGES
        msgs.append(nova("assistant", content="vou olhar"))
        m = nova("tool", content="Screenshot anexado.", name="browser_screenshot", tool_call_id=f"c{ident}")
        (tmp_path / "prints" / f"a{ident}.jpg").write_bytes(buf.getvalue())
        m.meta = {"attachments": [{"kind": "image", "path": f"prints/a{ident}.jpg", "name": "browser.jpg",
                                   "mime": "image/jpeg"}], "model_sees": True}
        msgs.append(m)

    def imagens(hist):
        return sum(1 for m in hist if isinstance(m.get("content"), list)
                   for p in m["content"] if p.get("type") == "image_url")

    nuvem = ag.build_history(msgs, "native", {"vision"}, prefixo_estavel=False)
    local = ag.build_history(msgs, "native", {"vision"}, prefixo_estavel=True)
    assert imagens(nuvem) == ag.MAX_TOOL_IMAGES, "na nuvem o teto continua valendo"
    assert imagens(local) == 4, "no local nenhuma imagem sai: mexer no meio do histórico custa o cache"


def test_raciocinio_antigo_fica_no_contexto_do_local(tmp_path, monkeypatch):
    """Deixar o raciocínio cair quando chega uma mensagem nova do usuário reescreve o histórico lá
    na segunda mensagem, e o servidor local reprocessa o contexto inteiro. Medido em uso: 165s,
    183s e 205s no primeiro turno depois de uma mensagem, contra 3s de mediana nos outros passos.

    Na nuvem continua caindo: lá o custo é por token enviado, e o cache não é nosso.
    """
    from app import agent as ag

    msgs, ident = [], 0

    def nova(role, **kw):
        nonlocal ident
        ident += 1
        return Message(id=ident, role=role, content=kw.pop("content", ""),
                       thinking=kw.pop("thinking", ""), status="ok", **kw)

    # O raciocínio só volta em mensagem que CHAMOU ferramenta, que é o passo do laço do agente.
    chamada = [{"id": "c1", "name": "list_dir", "arguments": {}}]
    msgs.append(nova("user", content="primeira pergunta"))
    msgs.append(nova("assistant", content="", thinking="pensei no turno antigo", tool_calls=chamada))
    msgs.append(nova("tool", content="ok", name="list_dir", tool_call_id="c1"))
    msgs.append(nova("user", content="segunda pergunta"))
    msgs.append(nova("assistant", content="", thinking="pensei no turno de agora", tool_calls=chamada))

    def raciocinios(hist):
        return [m.get("reasoning_content") for m in hist if m.get("reasoning_content")]

    nuvem = ag.build_history(msgs, "native", reasoning_back=True, prefixo_estavel=False)
    local = ag.build_history(msgs, "native", reasoning_back=True, prefixo_estavel=True)
    assert raciocinios(nuvem) == ["pensei no turno de agora"]
    assert raciocinios(local) == ["pensei no turno antigo", "pensei no turno de agora"]


def test_turno_so_de_raciocinio_leva_lembrete(monkeypatch, tmp_path):
    """Modelo pensante com prompt grande monta o plano todo dentro do <think> e não emite nada.
    Sem lembrete o turno acabava em silêncio: caixa de raciocínio na tela e nenhuma resposta."""
    from app import agent

    assert "raciocínio" in agent.nudge_text("native", mudo=True)
    assert "chamou nenhuma ferramenta" in agent.nudge_text("native", mudo=False)
    # o texto muda conforme o modo de tool calling
    assert "<tool_call>" in agent.nudge_text("prompt", mudo=True)

"""E15-A: board de issues — impressão digital, varredura determinística, Iniciar e acompanhamento."""
import pytest

from app import board, db


@pytest.fixture
def proj(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def f():\n    # TODO: tratar o erro de rede\n    return 1  # FIXME quebra com lista vazia\n"
        "x = 'TODO dentro de string não é comentário'\n", encoding="utf-8")
    (tmp_path / "web.tsx").write_text("// HACK: remover depois do lançamento\n", encoding="utf-8")
    return tmp_path, board.projeto_de(str(tmp_path))


def _varre(projeto, root, monkeypatch, saidas=None):
    from app import shell
    monkeypatch.setattr(shell, "executa_do_projeto",
                        lambda r, cmd, t=60: (saidas or {}).get(cmd, (0, "")))
    board._VARREDURAS[projeto] = {"rodando": True, "criados": 0, "encontrados": 0, "avisos": []}
    board._varre(projeto, root)
    return board._VARREDURAS[projeto]


def test_todo_vira_card_com_arquivo_e_linha(proj, monkeypatch):
    root, projeto = proj
    estado = _varre(projeto, root, monkeypatch)
    cards = board.listar(projeto)
    titulos = {c["titulo"] for c in cards}
    assert {"TODO: tratar o erro de rede", "FIXME: quebra com lista vazia", "HACK: remover depois do lançamento"} <= titulos
    assert not any("string" in t for t in titulos)  # só comentário
    todo = next(c for c in cards if c["titulo"].startswith("TODO"))
    assert todo["evidencias"][0] == {"arquivo": "src/app.py", "linha": 2, "trecho": "# TODO: tratar o erro de rede"}
    assert todo["status"] == "novo" and todo["origem"] == "varredura-deterministica"
    assert next(c for c in cards if c["titulo"].startswith("HACK"))["area"] == "frontend"
    assert estado["criados"] == 3 and any("FORJA.md" in a for a in estado["avisos"])


def test_impressao_evita_duplicado_e_rejeitado_nao_volta(proj, monkeypatch):
    root, projeto = proj
    _varre(projeto, root, monkeypatch)
    todo = next(c for c in board.listar(projeto) if c["titulo"].startswith("TODO"))
    board.rejeitar(todo["id"], "nao_quero")
    # o código ganhou linhas em cima: o número muda, a impressão não
    (root / "src" / "app.py").write_text("import os\n\n" + (root / "src" / "app.py").read_text(), encoding="utf-8")
    estado = _varre(projeto, root, monkeypatch)
    assert estado["criados"] == 0
    cards = board.listar(projeto)
    assert len(cards) == 3 and next(c for c in cards if c["id"] == todo["id"])["status"] == "rejeitado"


def test_problema_que_sumiu_marca_resolvido(proj, monkeypatch):
    root, projeto = proj
    _varre(projeto, root, monkeypatch)
    (root / "web.tsx").write_text("export const x = 1\n", encoding="utf-8")
    _varre(projeto, root, monkeypatch)
    hack = next(c for c in board.listar(projeto) if c["titulo"].startswith("HACK"))
    assert hack["sumiu"] and "resolvido?" in hack["historico"][-1]["texto"]


def test_comandos_do_forja_md_viram_um_card_por_erro(proj, monkeypatch):
    root, projeto = proj
    (root / "FORJA.md").write_text("# App\n- typecheck_command: `npx tsc --noEmit --pretty false`\n"
                                   "test_command: pytest -q\nlint_command: npm run lint\n", encoding="utf-8")
    assert board.comandos_do_projeto(root) == {"typecheck": "npx tsc --noEmit --pretty false",
                                               "lint": "npm run lint", "test": "pytest -q"}
    saidas = {
        "npx tsc --noEmit --pretty false": (2, "src/a.ts(3,5): error TS2322: Type 'x' is not assignable.\n"
                                               "src/b.ts(10,1): error TS2304: Cannot find name 'y'.\n"),
        "pytest -q": (1, "....F\nFAILED tests/test_a.py::test_soma - assert 1 == 2\n1 failed"),
        "npm run lint": (1, "algo que ninguém entende\nexit 1"),
    }
    _varre(projeto, root, monkeypatch, saidas)
    cards = {c["titulo"]: c for c in board.listar(projeto)}
    ts = cards["TS2322: Type 'x' is not assignable."]
    assert ts["evidencias"][0]["arquivo"] == "src/a.ts" and ts["evidencias"][0]["linha"] == 3
    assert ts["verify_sugerido"] == "npx tsc --noEmit --pretty false" and ts["severidade"] == 1
    assert cards["Teste falhando: test_soma"]["area"] == "testes"
    assert "saida" in cards["`npm run lint` falhou"]["evidencias"][0]  # genérico: um card com a saída


def test_eslint_um_card_por_erro():
    saida = "C:/p/src/a.tsx\n  3:7  error  'x' is assigned a value but never used  no-unused-vars\n" \
            "  9:1  warning  algo  regra\n"
    cards = board.erros_de("lint", "npx eslint .", saida)
    assert len(cards) == 1 and cards[0]["evidencias"][0]["linha"] == 3 and cards[0]["area"] == "frontend"


def test_card_de_varredura_sem_evidencia_nao_nasce(proj):
    _, projeto = proj
    with pytest.raises(board.BoardError, match="sem evidência"):
        board.criar(projeto, {"titulo": "bug inventado", "impressao": "x"}, "varredura-ia")


def test_iniciar_cria_conversa_no_modo_certo(proj, monkeypatch):
    _, projeto = proj
    disparos = []
    monkeypatch.setattr(board, "_escolha", lambda modo: {"provider": "p", "model": "m", "permission": "manual",
                                                         "effort": "medio"})
    monkeypatch.setattr(board, "_dispara", lambda conv, kind, texto, esc: disparos.append((conv, kind, texto)))
    bug, _ = board.criar(projeto, {"titulo": "Corrigir login", "tipo": "bugfix", "area": "backend",
                                   "verify_sugerido": "pytest -q",
                                   "evidencias": [{"arquivo": "src/app.py", "linha": 2, "trecho": "x"}]})
    feat, _ = board.criar(projeto, {"titulo": "Tela de relatórios", "tipo": "feature"})
    novo, _ = board.criar(projeto, {"titulo": "Triar", "status": "novo"})
    with pytest.raises(board.BoardError, match="Aceite"):
        board.iniciar(novo["id"])
    c1 = board.iniciar(bug["id"])
    c2 = board.iniciar(feat["id"])
    assert [k for _, k, _ in disparos] == ["agent", "maestro"]
    assert "`src/app.py:2`" in disparos[0][2] and "`pytest -q` passar" in disparos[0][2]
    assert c1["status"] == "andamento" and c1["conversa_id"] == disparos[0][0]
    with db.session() as s:
        conv = s.get(db.Conversation, c2["conversa_id"])
        assert conv.kind == "maestro" and conv.workspace == projeto
    with pytest.raises(board.BoardError, match="já está em andamento"):
        board.iniciar(bug["id"])


def test_card_segue_a_conversa_ate_a_revisao(proj, monkeypatch):
    _, projeto = proj
    monkeypatch.setattr(board, "_escolha", lambda modo: {"provider": "p", "model": "m", "permission": "manual",
                                                         "effort": "medio"})
    monkeypatch.setattr(board, "_dispara", lambda *a: None)
    card, _ = board.criar(projeto, {"titulo": "Arrumar"})
    card = board.iniciar(card["id"])
    board.acompanha()
    assert board.listar(projeto)[0]["status"] == "andamento"  # agente ainda não respondeu
    with db.session() as s:
        s.add(db.Message(conversation_id=card["conversa_id"], role="assistant", content="feito"))
        s.commit()
    board.acompanha()
    atual = board.listar(projeto)[0]
    assert atual["status"] == "revisao" and "revisão" in atual["historico"][-1]["texto"]


def test_maestro_so_vai_para_revisao_com_a_feature_concluida(proj, monkeypatch):
    _, projeto = proj
    monkeypatch.setattr(board, "_escolha", lambda modo: {"provider": "p", "model": "m", "permission": "manual",
                                                         "effort": "medio"})
    monkeypatch.setattr(board, "_dispara", lambda *a: None)
    card, _ = board.criar(projeto, {"titulo": "Nova tela", "tipo": "feature"})
    card = board.iniciar(card["id"])
    with db.session() as s:
        s.add(db.Message(conversation_id=card["conversa_id"], role="assistant", content="planejei"))
        f = db.Feature(conversation_id=card["conversa_id"], title="Nova tela", status="active")
        s.add(f)
        s.commit()
        fid = f.id
    board.acompanha()
    assert board.listar(projeto)[0]["status"] == "andamento"
    with db.session() as s:
        s.get(db.Feature, fid).status = "done"
        s.commit()
    board.acompanha()
    assert board.listar(projeto)[0]["status"] == "revisao"


def test_iniciar_pelo_endpoint_dispara_o_turno_de_verdade(proj, monkeypatch):
    """Na validação o Iniciar dava 500 ("no running event loop"): o endpoint era síncrono e o Run.start
    precisa do laço. Aqui vai pela API, com o Run real e um modelo falso."""
    import time
    from fastapi.testclient import TestClient
    from app import agent, llm, main, mobile
    _, projeto = proj

    async def fake_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        yield "content", "Corrigido."
        yield "done", {"tool_calls": []}

    async def none(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "context_limit", none)
    monkeypatch.setattr(llm, "capabilities", none)
    monkeypatch.setattr(mobile, "defaults", lambda: {"provider": "lmstudio", "model": "m", "permission": "manual"})
    card, _ = board.criar(projeto, {"titulo": "Arrumar a soma"})
    with TestClient(main.app) as c:
        r = c.post(f"/api/board/issues/{card['id']}/iniciar", json={}, headers={"x-forja-token": main.TOKEN}
                   if hasattr(main, "TOKEN") else {})
        assert r.status_code == 200, r.text
        conv = r.json()["conversa_id"]
        for _ in range(50):
            if not agent.active_run(conv) and board.listar(projeto)[0]["status"] == "revisao":
                break
            time.sleep(0.1)
    assert board.listar(projeto)[0]["status"] == "revisao"
    with db.session() as s:
        falas = [m.content for m in s.get(db.Conversation, conv).messages]
    assert any("Arrumar a soma" in f for f in falas) and "Corrigido." in falas


def test_validacao_e_carimbo(proj):
    _, projeto = proj
    antes = board.carimbo()
    card, _ = board.criar(projeto, {"titulo": "x"})
    assert board.carimbo() != antes and card["status"] == "backlog"
    with pytest.raises(board.BoardError):
        board.atualizar(card["id"], {"tipo": "inventado"})
    with pytest.raises(board.BoardError):
        board.rejeitar(card["id"], "porque sim")


# ------------------------------------------------ vínculos entre pastas

def _repo(p):
    (p / ".git").mkdir(parents=True)
    return p


def test_vincular_subpastas_ao_mesmo_board(tmp_path):
    raiz = tmp_path / "projeto"
    back, front = _repo(raiz / "back"), _repo(raiz / "front")
    (back / "api.py").write_text("x = 1  # TODO: validar\n", encoding="utf-8")
    projeto = board.projeto_de(str(raiz))
    assert board.projeto_de(str(back)) != projeto  # sem vínculo: cada repo com o seu board
    card_back, _ = board.criar(board.projeto_de(str(back)), {"titulo": "Do back", "evidencias": [
        {"arquivo": "api.py", "linha": 1, "trecho": "x"}]})
    assert set(board.sugestoes(projeto)) == {board.workspace.normalize(str(back)), board.workspace.normalize(str(front))}
    board.vincular(projeto, str(back))
    board.vincular(projeto, str(front))
    (back / "src").mkdir()
    assert board.projeto_de(str(back / "src")) == projeto  # subpasta da vinculada também
    assert board.sugestoes(projeto) == []
    movido = next(c for c in board.listar(projeto) if c["id"] == card_back["id"])
    assert movido["evidencias"][0]["arquivo"] == "back/api.py" and "vinculada" in movido["historico"][-1]["texto"]
    board.desvincular(str(front))
    assert board.projeto_de(str(front)) != projeto
    with pytest.raises(board.BoardError):
        board.vincular(projeto, str(raiz))


def test_varredura_cobre_as_pastas_vinculadas(tmp_path, monkeypatch):
    raiz = _repo(tmp_path / "mono")  # raiz É repo: a varredura dela pula os repos aninhados…
    back = _repo(raiz / "back")
    fora = tmp_path / "fora"
    fora.mkdir()
    (back / "a.py").write_text("# TODO: no back\n", encoding="utf-8")
    (fora / "b.py").write_text("# FIXME: fora da raiz\n", encoding="utf-8")
    projeto = board.projeto_de(str(raiz))
    board.vincular(projeto, str(back))
    board.vincular(projeto, str(fora))
    _varre(projeto, raiz, monkeypatch)  # …e as vinculadas entram mesmo assim
    ev = {c["titulo"]: c["evidencias"][0]["arquivo"] for c in board.listar(projeto)}
    assert ev == {"TODO: no back": "back/a.py", "FIXME: fora da raiz": "../fora/b.py"}
    assert _varre(projeto, raiz, monkeypatch)["criados"] == 0  # e não duplica na segunda


# ------------------------------------------------ a IA cria card

def test_board_card_exige_evidencia_que_confere(proj):
    from app.tools import ToolError, run_tool
    root, projeto = proj
    ok = run_tool("board_card", {"titulo": "Subtração no lugar da soma", "tipo": "bugfix", "arquivo": "src/app.py",
                                 "trecho": "return 1  # FIXME quebra com lista vazia"}, root)
    assert "coluna Novo" in ok["text"] and "src/app.py:3" in ok["text"]
    assert ok["board_card"]["evidencia"]["linha"] == 3 and ok["board_card"]["status"] == "novo"  # o chat desenha o card
    card = board.listar(projeto)[0]
    assert card["status"] == "novo" and card["origem"] == "varredura-ia" and card["evidencias"][0]["linha"] == 3
    assert "Já existe" in run_tool("board_card", {"titulo": "de novo", "tipo": "bugfix", "arquivo": "src/app.py",
                                                  "trecho": "return 1  # FIXME quebra com lista vazia"}, root)["text"]
    for args, erro in (({"arquivo": "src/nao_existe.py", "linha": 1}, "não encontrado"),
                       ({"arquivo": "src/app.py", "linha": 99}, "não existe"),
                       ({"arquivo": "src/app.py", "linha": 1, "trecho": "codigo que nao esta la"}, "não está no arquivo"),
                       ({"arquivo": ""}, "sem evidência")):
        with pytest.raises(ToolError, match=erro):
            run_tool("board_card", {"titulo": "x", "tipo": "bugfix", **args}, root)


def test_apelido_create_issue_vira_board_card(proj):
    from app import agent, apelidos
    c = apelidos.resolve({"id": "1", "name": "create_issue", "arguments": {"title": "t", "file": "a.py", "line": 2}},
                         ["board_card"], agent._props)
    assert c["name"] == "board_card" and c["arguments"] == {"titulo": "t", "arquivo": "a.py", "linha": 2}


def test_pedir_a_ia_leva_foco_abertos_e_rejeitados(proj, monkeypatch):
    _, projeto = proj
    rej, _ = board.criar(projeto, {"titulo": "Trocar tabs por espaços"})
    board.rejeitar(rej["id"], "nao_quero")
    board.criar(projeto, {"titulo": "Card aberto"})
    disparos = []
    monkeypatch.setattr(board, "_escolha", lambda modo: {"provider": "p", "model": "m", "permission": "auto", "effort": "medio"})
    monkeypatch.setattr(board, "_dispara", lambda conv, kind, texto, esc: disparos.append((kind, texto, esc)))
    r = board.pedir_ia(projeto, "bugs", "src")
    kind, texto, esc = disparos[0]
    assert kind == "agent" and esc["permission"] == "manual"  # só lê: escrita pede aprovação
    assert "bugs prováveis" in texto and "`src`" in texto and "board_card" in texto
    assert "- Card aberto" in texto and "Trocar tabs por espaços (nao quero)" in texto
    with db.session() as s:
        assert s.get(db.Conversation, r["conversa_id"]).workspace == projeto
    with pytest.raises(board.BoardError):
        board.pedir_ia(projeto, "inventado")


# ------------------------------------------------ card visual: prints no card

def test_card_visual_pede_prints_e_recebe_antes_e_depois(proj, monkeypatch):
    _, projeto = proj
    monkeypatch.setattr(board, "_escolha", lambda modo: {"provider": "p", "model": "m", "permission": "manual", "effort": "medio"})
    textos = []
    monkeypatch.setattr(board, "_dispara", lambda conv, kind, texto, esc: textos.append(texto))
    card, _ = board.criar(projeto, {"titulo": "Botão cortado no celular", "tipo": "visual", "area": "frontend"})
    card = board.iniciar(card["id"])
    assert "ANTES" in textos[0] and "DEPOIS" in textos[0] and "browser_screenshot" in textos[0]
    with db.session() as s:
        for n in (1, 2, 3):
            s.add(db.Message(conversation_id=card["conversa_id"], role="tool", name="browser_screenshot",
                             content="ok", meta={"attachments": [{"path": f".forja/uploads/p{n}.jpg", "kind": "image"}]}))
        s.add(db.Message(conversation_id=card["conversa_id"], role="assistant", content="corrigido"))
        s.commit()
    board.acompanha()
    fim = board.listar(projeto)[0]
    imgs = [e for e in fim["evidencias"] if e.get("imagem")]
    assert fim["status"] == "revisao"
    assert [(e["rotulo"], e["imagem"]) for e in imgs] == [("antes", ".forja/uploads/p1.jpg"), ("depois", ".forja/uploads/p3.jpg")]
    assert imgs[0]["conv"] == card["conversa_id"]


# ------------------------------------------------ interruptor do board_card e skill /board

def test_board_card_desligado_some_do_catalogo_e_o_pedido_libera(proj):
    from app import agent, sessoes, skills, workspace
    root, projeto = proj
    with db.session() as s:
        c = db.Conversation(kind="agent", workspace=projeto)
        s.add(c)
        s.commit()
        conv = c.id
    t1, t2 = workspace.CURRENT.set(root), sessoes.CONV.set(conv)
    try:
        nomes = lambda: {t.name for t in agent.available_tools(None, "manual")}  # noqa: E731
        assert "board_card" in nomes()  # ligado por padrão
        board.define_board_card(projeto, False)
        assert not board.board_card_ligado(projeto)
        assert "board_card" not in nomes()  # desligado: fora do catálogo, logo fora do prompt
        assert "Cria um card no board do projeto" not in agent.prompt_base("native", set())
        with db.session() as s:
            s.add(db.Message(conversation_id=conv, role="user", content="/board bugs no login"))
            s.commit()
        assert "board_card" in nomes()  # pedido explícito nesta conversa libera só aqui
    finally:
        workspace.CURRENT.reset(t1)
        sessoes.CONV.reset(t2)
        board.define_board_card(projeto, True)
    bloco = skills.invocada(root, "/board bugs no login")
    assert "board_card" in bloco and "bugs no login" in bloco


def test_board_card_nao_repete_o_mesmo_ponto_e_aceita_tipo_solto(proj):
    """Na validação a IA criou de novo os mesmos 3 bugs apontando a linha do `def` em vez da do `return`, e
    teve 3 chamadas recusadas por tipo "bug" e área inventada."""
    from app.tools import run_tool
    root, projeto = proj
    r = run_tool("board_card", {"titulo": "Retorno fixo", "tipo": "bug", "area": "backend/api",
                                "arquivo": "src/app.py", "linha": 3}, root)
    assert r["board_card"]["tipo"] == "bugfix" and r["board_card"]["area"] == "backend"
    de_novo = run_tool("board_card", {"titulo": "Outro nome", "tipo": "improvement", "arquivo": "src/app.py",
                                      "linha": 1}, root)
    assert "Já existe o card #" in de_novo["text"] and de_novo["board_card"]["id"] == r["board_card"]["id"]
    longe = run_tool("board_card", {"titulo": "Longe", "tipo": "melhoria", "arquivo": "web.tsx", "linha": 1}, root)
    assert "criado" in longe["text"] and longe["board_card"]["tipo"] == "improvement"

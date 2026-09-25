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

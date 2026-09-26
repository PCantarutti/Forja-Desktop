"""E16 parte A: placar de progresso, detector ampliado e filtro do raciocínio."""
import asyncio
from pathlib import Path

from app import agent, db, llm, main, progresso, workspace  # noqa: F401  (main registra as ferramentas)
from app.progresso import Placar


def _ok(p, nome, args, texto="ok", status="ok", root=None, mutante=False):
    return p.resultado(nome, args, status, texto, root, mutante)


def test_ciclo_abab_detectado():
    p = Placar()
    for _ in range(2):
        _ok(p, "read_file", {"path": "a.py"})
        _ok(p, "run_command", {"command": "pytest"})
    nivel, motivo, alvo = p.avalia()
    assert nivel == 2 and "ciclo de 2" in motivo and alvo == progresso.chave("read_file", {"path": "a.py"})


def test_repeticao_exata_nao_e_ciclo():
    p = Placar()
    for _ in range(4):
        _ok(p, "list_dir", {"path": "."})
    assert p.ciclo() is None  # é caso do LoopDetector, com o degrau dele


def test_mesmo_erro_com_chamadas_diferentes():
    p = Placar()
    for i, cmd in enumerate(["python a.py", "python -m a", "py a.py"]):
        _ok(p, "run_command", {"command": cmd}, f"ModuleNotFoundError: No module named 'x{i}' em C:\\p\\a{i}.py",
            status="erro")
    nivel, motivo, _ = p.avalia()
    assert nivel == 2 and "mesmo erro voltou 3" in motivo


def test_passos_sem_progresso_lembra_e_depois_intervem():
    p = Placar()
    niveis = []
    for _ in range(8):
        _ok(p, "list_dir", {"path": "."}, "a.py b.py")
        p.passo(False)
        niveis.append(p.avalia()[0])
    assert niveis.count(1) == 1 and niveis[-1] == 2


def test_desfazer_nao_e_progresso(tmp_path):
    p = Placar()
    f = tmp_path / "a.py"
    f.write_text("x = 1")
    assert _ok(p, "edit_file", {"path": "a.py"}, root=tmp_path, mutante=True)
    f.write_text("x = 2")
    assert _ok(p, "edit_file", {"path": "a.py"}, root=tmp_path, mutante=True)
    f.write_text("x = 1")  # voltou ao conteúdo de antes
    assert not _ok(p, "edit_file", {"path": "a.py"}, root=tmp_path, mutante=True)


def test_comando_que_falhava_e_passou_e_progresso():
    p = Placar()
    _ok(p, "run_command", {"command": "pytest"}, "1 failed", status="erro")
    _ok(p, "run_command", {"command": "pytest"}, "1 passed")  # mesmo texto de antes não importaria
    assert p.testou


def test_alucinacao_soma_pontos():
    p = Placar()
    p.alucinou()  # ferramenta que não existe
    for _ in range(2):
        _ok(p, "read_file", {"path": "nao.py"}, "Arquivo não encontrado: 'nao.py'.", status="erro")
    for _ in range(2):
        _ok(p, "edit_file", {"path": "a.py"}, "old_str não encontrado no arquivo.", status="erro")
    nivel, motivo, _ = p.avalia()
    assert nivel == 2 and "alucinação" in motivo


def test_afirmar_teste_sem_rodar(tmp_path):
    p = Placar()
    assert not p.afirmacao_sem_teste("Testei e passou.")  # não escreveu nada: é conversa
    (tmp_path / "a.py").write_text("x")
    _ok(p, "write_file", {"path": "a.py"}, root=tmp_path, mutante=True)
    assert p.afirmacao_sem_teste("Pronto, testei e os testes passaram.")
    _ok(p, "run_command", {"command": "pytest"}, "3 passed")
    assert not p.afirmacao_sem_teste("Pronto, testei.")


def test_bloqueio_dura_k_passos():
    p = Placar()
    k = progresso.chave("list_dir", {"path": "."})
    p.intervencao("teste", k)
    bloqueado = []
    for _ in range(progresso.BLOQUEIO + 2):
        bloqueado.append(bool(p.bloqueada("list_dir", {"path": "."})))
        p.passo(False)
    assert bloqueado == [True] * progresso.BLOQUEIO + [False, False]
    assert p.teto_menor


# ------------------------------------------------------------ raciocínio

def _prosa() -> str:
    """Comentários do agent.py: prosa técnica variada, do tipo que um raciocínio saudável produz."""
    linhas = Path(agent.__file__).read_text(encoding="utf-8").splitlines()
    return "\n".join(ln.strip().lstrip("# ") for ln in linhas if ln.strip().startswith("#"))[:12000]


def test_raciocinio_longo_e_variado_nao_aborta():
    texto = _prosa()
    assert len(texto) > 8000
    for fim in range(2000, len(texto), 2000):
        assert progresso.degenerado(texto[:fim]) == ""


def test_raciocinio_repetitivo_aborta():
    assert "repetitivo" in progresso.degenerado("Preciso ler o arquivo main.py de novo. " * 200)


def test_frase_repetida_e_hesitacao():
    base = _prosa()[:3000]
    frase = " vou conferir se a função soma devolve o valor certo para os dois argumentos inteiros agora "
    assert "mesma frase" in progresso.degenerado(base + frase * 3)
    hes = " ".join(f"Wait, {p}" for p in base.split(".")[:80])
    assert "hesitação" in progresso.degenerado(hes)


def test_mediana_por_modelo():
    for t in [100, 200, 300, 400, 500]:
        progresso.registra_raciocinio("modelo-mediana", t)
    assert progresso.mediana("modelo-mediana") == 300
    assert progresso.pensa_demais("modelo-mediana", 1000) and not progresso.pensa_demais("modelo-mediana", 800)
    assert progresso.mediana("outro-modelo") is None


# ------------------------------------------------------------ de ponta a ponta no laço do agente

async def _none(*a):
    return None


def _roda(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(llm, "chat_stream", fake)
    monkeypatch.setattr(llm, "context_limit", _none)
    monkeypatch.setattr(llm, "capabilities", _none)

    async def cenario():
        with db.session() as s:
            c = db.Conversation(kind="agent", workspace=str(tmp_path))
            s.add(c)
            s.commit()
            conv = c.id
        tok = workspace.CURRENT.set(tmp_path)
        try:
            run = agent.Run(conv)
            req = agent.RunRequest(content="faça", provider="lmstudio", model="m", mode="agent", permission="auto")
            return [ev async for ev in agent.run_agent(conv, req, run)]
        finally:
            workspace.CURRENT.reset(tok)

    return asyncio.run(cenario())


def test_chamada_bloqueada_e_recusada_nos_k_passos(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    passo = {"n": 0, "mult": []}

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        passo["n"] += 1
        passo["mult"].append(kw.get("budget_mult", 1.0))
        if passo["n"] <= 9:
            yield "done", {"tool_calls": [{"id": f"c{passo['n']}", "name": "list_dir", "arguments": {"path": "."}}]}
        else:
            yield "content", "Pronto."
            yield "done", {"tool_calls": []}

    eventos = _roda(monkeypatch, tmp_path, fake)
    res = [e["message"] for e in eventos if e.get("type") == "tool_result"]
    bloqueadas = [m["tool_call_id"] for m in res if "bloqueada pela recuperação" in (m["content"] or "")]
    assert bloqueadas == [f"c{i}" for i in range(5, 10)]  # a 5ª e as 4 seguintes
    assert any(e.get("type") == "event" and "Você está em loop" in e["message"]["content"] for e in eventos)
    assert passo["mult"][5] == 0.5  # o turno depois da intervenção pensa menos


def test_raciocinio_degenerado_aborta_a_geracao(monkeypatch, tmp_path):
    lidos = {"n": 0, "chamadas": 0}

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        lidos["chamadas"] += 1
        if lidos["chamadas"] == 1:
            for _ in range(500):
                lidos["n"] += 1
                yield "reasoning", "Preciso ler o arquivo main.py de novo. "
            yield "done", {"tool_calls": []}
        else:
            assert any("o raciocínio degenerou" in (m.get("content") or "") for m in messages)
            yield "content", "Vou por outro caminho."
            yield "done", {"tool_calls": []}

    eventos = _roda(monkeypatch, tmp_path, fake)
    assert lidos["n"] < 500 and lidos["chamadas"] == 2
    avisos = [e["message"]["content"] for e in eventos if e.get("type") == "event"]
    assert any("Raciocínio degenerado" in a for a in avisos)

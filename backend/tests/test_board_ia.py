"""E15-B: varredura com IA — achado com prova, incremental, cede o modelo e respeita o teto."""
import asyncio
import json
import subprocess

import pytest

from app import board, board_ia, db, llm, modelctl


def _git(root, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("def soma(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "b.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    monkeypatch.setattr(board_ia, "_modelo", lambda: {"provider": "lmstudio", "model": "m"})
    monkeypatch.setattr(modelctl, "como_rodar", lambda papel, pedido: modelctl.Rota("externo", pedido, None, "t"))

    async def janela(*a):
        return 8192

    monkeypatch.setattr(llm, "context_limit", janela)
    board_ia.ESTADO.clear()
    yield root
    with db.session() as s:
        s.query(db.Issue).filter(db.Issue.projeto == board.projeto_de(str(root))).delete()
        linha = s.get(db.AppSetting, board_ia.CHAVE)
        if linha:
            s.delete(linha)
        s.commit()


def test_achado_sem_prova_e_descartado(repo):
    ok = board_ia.valida(repo, {"tipo": "bugfix", "titulo": "soma subtrai", "arquivo": "a.py", "linha": 2,
                                "trecho": "return a - b"})
    assert ok and ok["evidencias"][0] == {"arquivo": "a.py", "linha": 2, "trecho": "return a - b"}
    assert board_ia.valida(repo, {"arquivo": "a.py", "linha": 2}) is None                    # sem trecho
    assert board_ia.valida(repo, {"arquivo": "a.py", "trecho": "return a - b"}) is None       # sem linha
    assert board_ia.valida(repo, {"arquivo": "a.py", "linha": 2, "trecho": "return a * b"}) is None  # inventado
    assert board_ia.valida(repo, {"arquivo": "../fora.py", "linha": 1, "trecho": "x"}) is None


def test_incremental_so_le_o_que_mudou(repo):
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert board_ia.arquivos_para_ler(repo, None) == ["a.py", "b.py"]
    assert board_ia.arquivos_para_ler(repo, commit) == []
    (repo / "b.py").write_text("x = 2\n", encoding="utf-8")
    assert board_ia.arquivos_para_ler(repo, commit) == ["b.py"]


def _fala(respostas, lidos):
    async def fala(provider, model, messages, tools, num_ctx, effort=None, **kw):
        lidos.append(messages[1]["content"])
        if "triagem" in messages[0]["content"]:
            yield "content", "sem json"  # triagem falha: os achados crus viram cards
            return
        yield "content", json.dumps({"achados": respostas.pop(0) if respostas else []})
    return fala


def _roda(repo, forcar=False):
    async def cena():
        board_ia.varrer(str(repo), forcar)
        projeto = board.projeto_de(str(repo))
        while board_ia.ESTADO[projeto]["rodando"]:
            await asyncio.sleep(0.01)
        return board_ia.ESTADO[projeto]
    return asyncio.run(cena())


def test_varredura_cria_cards_com_prova_e_descarta_o_resto(repo, monkeypatch):
    lidos = []
    monkeypatch.setattr(llm, "chat_stream", _fala([[
        {"tipo": "bugfix", "titulo": "soma subtrai", "arquivo": "a.py", "linha": 2, "trecho": "return a - b"},
        {"tipo": "bugfix", "titulo": "inventado", "arquivo": "a.py", "linha": 9, "trecho": "nada"}]], lidos))
    est = _roda(repo)
    assert est["criados"] == 1 and est["descartados"] == 1 and not est["parou"]
    cards = board.listar(board.projeto_de(str(repo)))
    assert cards[0]["origem"] == "varredura-ia" and cards[0]["status"] == "novo"
    # a próxima varredura é incremental: nada mudou, nada é lido
    lidos.clear()
    _roda(repo)
    assert not any("=== a.py" in l for l in lidos)


def test_cede_o_modelo_e_retoma_de_onde_parou(repo, monkeypatch):
    lidos = []
    monkeypatch.setattr(llm, "chat_stream", _fala([], lidos))
    monkeypatch.setattr(board_ia, "ocupado", lambda: True)
    est = _roda(repo)
    assert "o modelo foi pedido" in est["parou"] and not lidos
    assert board_ia._cfg(board.projeto_de(str(repo)))["pendentes"] == ["a.py", "b.py"]
    monkeypatch.setattr(board_ia, "ocupado", lambda: False)
    _roda(repo)
    assert any("=== a.py" in l for l in lidos) and board_ia._cfg(board.projeto_de(str(repo)))["pendentes"] == []


def test_teto_de_cards_e_a_fila_de_mais_achados(repo, monkeypatch):
    nomes = [a + b for a in "abcde" for b in "fghijk"][:30]  # a impressão ignora números: nomes diferentes
    linhas = "".join(f"{n} = True\n" for n in nomes)
    (repo / "b.py").write_text(linhas, encoding="utf-8")
    _git(repo, "commit", "-qam", "mais")
    achados = [{"tipo": "improvement", "titulo": f"t{i}", "arquivo": "b.py", "linha": i + 1, "trecho": f"{nomes[i]} = True"}
               for i in range(25)]
    monkeypatch.setattr(board_ia, "POR_LOTE", 30)
    monkeypatch.setattr(llm, "chat_stream", _fala([achados], []))
    est = _roda(repo)
    assert est["criados"] == 20 and est["mais"] == 5
    assert board_ia.trazer_mais(str(repo)) == {"criados": 5, "mais": 0}

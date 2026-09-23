"""Fase 1 do porte do DeepSeek Harness: glob/grep, ler antes de escrever, spill de saída grande."""
import os
import time

import pytest

from app import busca, tools  # noqa: F401  (busca registra glob/grep)
from app.tools import LIDOS, ToolError, run_tool, spill


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def soma(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "src" / "b.ts").write_text("export const SOMA = 1\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_text("def soma(): pass\n", encoding="utf-8")
    return tmp_path


def test_glob_sem_barra_casa_nome_em_qualquer_profundidade(ws):
    out = run_tool("glob", {"pattern": "*.py"}, ws)
    assert out.splitlines() == ["src/a.py"]  # node_modules fica de fora


def test_glob_com_barra_casa_caminho(ws):
    assert run_tool("glob", {"pattern": "src/*.ts"}, ws) == "src/b.ts"
    assert "nenhum" in run_tool("glob", {"pattern": "lib/*.ts"}, ws)


def test_grep_devolve_arquivo_linha_trecho(ws):
    out = run_tool("grep", {"pattern": r"def soma"}, ws)
    assert out == "src/a.py:1: def soma(a, b):"
    assert "b.ts" in run_tool("grep", {"pattern": "soma"}, ws)             # sem diferenciar maiúsculas
    assert "b.ts" not in run_tool("grep", {"pattern": "soma", "include": "*.py"}, ws)
    assert "nenhuma" in run_tool("grep", {"pattern": "soma", "case_sensitive": True, "include": "*.ts"}, ws)


def test_grep_regex_invalida_vira_erro_legivel(ws):
    with pytest.raises(ToolError, match="Regex inválida"):
        run_tool("grep", {"pattern": "("}, ws)


def test_editar_sem_ler_recusa_e_depois_de_ler_passa(ws):
    tok = LIDOS.set({})
    try:
        with pytest.raises(ToolError, match="ainda não foi lido"):
            run_tool("edit_file", {"path": "src/a.py", "old_str": "a + b", "new_str": "b + a"}, ws)
        with pytest.raises(ToolError, match="ainda não foi lido"):
            run_tool("write_file", {"path": "src/a.py", "content": "x"}, ws)
        run_tool("write_file", {"path": "novo.py", "content": "x\n"}, ws)       # arquivo novo: livre
        run_tool("edit_file", {"path": "novo.py", "old_str": "x", "new_str": "y"}, ws)  # quem escreveu leu
        run_tool("read_file", {"path": "src/a.py"}, ws)
        run_tool("edit_file", {"path": "src/a.py", "old_str": "a + b", "new_str": "b + a"}, ws)
        run_tool("edit_file", {"path": "src/a.py", "old_str": "b + a", "new_str": "a + b"}, ws)  # segue valendo
    finally:
        LIDOS.reset(tok)


def test_arquivo_que_mudou_depois_da_leitura_pede_releitura(ws):
    tok = LIDOS.set({})
    try:
        run_tool("read_file", {"path": "src/a.py"}, ws)
        alvo = ws / "src" / "a.py"
        alvo.write_text("def soma(a, b):\n    return b + a  # mexido\n", encoding="utf-8")
        os.utime(alvo, ns=(time.time_ns(), time.time_ns() + 10**9))
        with pytest.raises(ToolError, match="mudou depois"):
            run_tool("edit_file", {"path": "src/a.py", "old_str": "b + a", "new_str": "a + b"}, ws)
    finally:
        LIDOS.reset(tok)


def test_spill_guarda_o_inteiro_e_o_read_file_le_de_la(ws):
    grande = "\n".join(f"linha {i}" for i in range(10_000))
    curto = spill(grande, "t/c1")
    assert len(curto) < len(grande) and "Resultado completo em:" in curto
    caminho = curto.rsplit("Resultado completo em: ", 1)[1].split(". Leia", 1)[0]
    assert tools.SPILL_DIR in tools.Path(caminho).parents
    out = run_tool("read_file", {"path": caminho, "start_line": 5000, "end_line": 5000}, ws)
    assert "linha 4999" in out
    assert "linha 9999" in run_tool("grep", {"pattern": "linha 9999$", "path": caminho}, ws)
    assert spill("pouco", "t/c2") == "pouco"


def test_read_file_avisa_fim_e_continuacao(ws):
    (ws / "l.txt").write_text("\n".join(str(i) for i in range(1, 11)), encoding="utf-8")
    assert "Use start_line=4 para continuar" in run_tool("read_file", {"path": "l.txt", "end_line": 3}, ws)
    assert "(Fim do arquivo - 10 linhas)" in run_tool("read_file", {"path": "l.txt", "start_line": 8}, ws)
    with pytest.raises(ToolError, match="passa do fim"):
        run_tool("read_file", {"path": "l.txt", "start_line": 50}, ws)

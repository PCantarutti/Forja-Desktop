"""E6: session_search com FTS5, code_search com ranking e memória por projeto."""
import os
from pathlib import Path

import pytest

from app import config, db, memory, workspace
from app.tools import REGISTRY, ToolError, run_tool


def _conversa(pasta: Path, titulo: str, falas: list[tuple[str, str]]) -> int:
    with db.session() as s:
        c = db.Conversation(kind="agent", workspace=str(pasta), title=titulo)
        s.add(c)
        s.flush()
        for papel, texto in falas:
            s.add(db.Message(conversation_id=c.id, role=papel, content=texto))
        s.commit()
        return c.id


@pytest.fixture
def na_pasta(tmp_path):
    tok = workspace.CURRENT.set(tmp_path)
    yield tmp_path
    workspace.CURRENT.reset(tok)


# ------------------------------------------------ session_search

def test_fts_ranqueia_e_ignora_acento_e_saida_de_ferramenta(na_pasta):
    fraca = _conversa(na_pasta, "Fraca", [("user", "falamos de migração uma vez")])
    forte = _conversa(na_pasta, "Forte", [("user", "a migracao do banco quebrou"),
                                           ("assistant", "migração do banco: rodei o alembic de novo")])
    _conversa(na_pasta, "Só ferramenta", [("tool", "migração banco migração banco")])
    achou = run_tool("session_search", {"query": "migração banco"}, na_pasta)
    linhas = [l for l in achou.splitlines() if l.startswith("- conversa")]
    assert linhas[0].startswith(f"- conversa {forte}") and f"conversa {fraca}" in achou
    assert "Só ferramenta" not in achou


def test_filtro_de_pasta_vem_antes_do_limite(na_pasta, tmp_path_factory):
    outra = tmp_path_factory.mktemp("outra")
    for i in range(3):  # muito mais que o LIMIT em falas de outra pasta
        _conversa(outra, f"Ruído {i}", [("user", "zebra " * 3)] * 150)
    daqui = _conversa(na_pasta, "Daqui", [("user", "a zebra fugiu")])
    achou = run_tool("session_search", {"query": "zebra"}, na_pasta)
    assert f"conversa {daqui}" in achou and "Ruído" not in achou


def test_indice_segue_update_e_delete(na_pasta):
    cid = _conversa(na_pasta, "Muda", [("user", "texto velho abacaxi")])
    with db.session() as s:
        m = s.query(db.Message).filter_by(conversation_id=cid).one()
        m.content = "texto novo pitanga"
        s.commit()
    assert "Nenhuma" in run_tool("session_search", {"query": "abacaxi"}, na_pasta)
    assert f"conversa {cid}" in run_tool("session_search", {"query": "pitanga"}, na_pasta)
    with db.session() as s:
        s.delete(s.get(db.Conversation, cid))
        s.commit()
    with db.engine.connect() as c:  # o cascade do FK dispara o gatilho: nada órfão no índice
        assert not c.exec_driver_sql("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'pitanga'").all()


# ------------------------------------------------ code_search

@pytest.fixture
def projeto(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "dados")
    raiz = tmp_path / "app"
    (raiz / "src" / "auth").mkdir(parents=True)
    (raiz / "src" / "auth" / "sessao.ts").write_text(
        "export function handleUserSignIn(email: string, senha: string) {\n"
        "  return verificaCredenciais(email, senha)\n}\n", encoding="utf-8")
    (raiz / "src" / "carrinho.py").write_text(
        "def total(itens):\n    return sum(i.preco for i in itens)\n", encoding="utf-8")
    (raiz / "README.md").write_text("App de loja. O login fica em algum lugar.\n", encoding="utf-8")
    (raiz / "node_modules" / "lib").mkdir(parents=True)
    (raiz / "node_modules" / "lib" / "signin.js").write_text("function signIn(){}\n", encoding="utf-8")
    return raiz


def test_code_search_acha_pelo_assunto_sem_saber_o_nome(projeto):
    achou = run_tool("code_search", {"query": "sign in senha"}, projeto)
    primeira = achou.splitlines()[0]
    assert primeira.startswith("src/auth/sessao.ts") and "handleUserSignIn" in primeira
    assert "L1:" in achou and "node_modules" not in achou


def test_code_search_reindexa_so_o_que_mudou(projeto):
    run_tool("code_search", {"query": "total"}, projeto)
    assert "relidos" not in run_tool("code_search", {"query": "total"}, projeto)
    alvo = projeto / "src" / "carrinho.py"
    alvo.write_text("def frete(cep):\n    return 10\n", encoding="utf-8")
    os.utime(alvo, ns=(alvo.stat().st_atime_ns, alvo.stat().st_mtime_ns + 10**9))
    achou = run_tool("code_search", {"query": "frete cep"}, projeto)
    assert achou.startswith("src/carrinho.py") and "1 arquivo(s) relidos" in achou
    (projeto / "README.md").unlink()
    assert "README" not in run_tool("code_search", {"query": "loja login"}, projeto)


def test_code_search_so_na_subpasta(projeto):
    achou = run_tool("code_search", {"query": "login total sign", "path": "src/auth"}, projeto)
    assert "sessao.ts" in achou and "carrinho" not in achou and "README" not in achou
    with pytest.raises(ToolError, match="Não é uma pasta"):
        run_tool("code_search", {"query": "login", "path": "Cardapio"}, projeto)


def test_pasta_com_varios_repos_entra_e_worktree_nao(tmp_path, monkeypatch):
    """mesaflow/ = api/ + admin/, cada um com .git: eles são o projeto. Worktree em .claude/ não."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "dados")
    raiz = tmp_path / "grupo"
    for sub in ("api", ".claude/worktrees/x"):
        (raiz / sub / ".git").mkdir(parents=True)
        (raiz / sub / "auth.py").write_text("def login_staff():\n    pass\n", encoding="utf-8")
    (raiz / ".docusaurus").mkdir()
    (raiz / ".docusaurus" / "cache.json").write_text('{"login": 1}', encoding="utf-8")
    achou = run_tool("code_search", {"query": "login"}, raiz)
    assert achou.startswith("api/auth.py") and ".claude" not in achou and ".docusaurus" not in achou


def test_teste_e_doc_perdem_para_a_implementacao(projeto):
    (projeto / "tests").mkdir()
    (projeto / "tests" / "test_signin.py").write_text("def test_sign_in():\n    sign_in('a', 'senha')\n" * 3,
                                                      encoding="utf-8")
    assert run_tool("code_search", {"query": "sign in senha"}, projeto).startswith("src/auth/sessao.ts")


def test_code_search_so_le():
    assert not REGISTRY["code_search"].mutating


# ------------------------------------------------ memória por projeto

def test_memoria_de_projeto_nao_vaza(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PERSONAL_MEMORY_DIR", tmp_path / "global")
    monkeypatch.setattr(config, "PERSONAL_MEMORY", True)
    a, b = tmp_path / "a", tmp_path / "b"
    for p in (a, b):
        (p / ".git").mkdir(parents=True)
    (a / "sub").mkdir()
    tok = workspace.CURRENT.set(a / "sub")  # subpasta do projeto a: a memória vai para a raiz do git
    try:
        memory.personal_write("Porta do dev", "Dev na porta 5174", "O vite roda na 5174.", "projeto")
        memory.personal_write("Nome", "Nome: Pedro", "Pedro.", "usuario")
        assert (a / ".forja" / "memoria" / "porta-do-dev.md").is_file()
        assert "5174" in memory.index(refresh=True) and "Pedro" in memory.index()
        assert {m["slug"]: m["scope"] for m in memory.personal_list()} == {"porta-do-dev": "projeto",
                                                                             "nome": "global"}
    finally:
        workspace.CURRENT.reset(tok)
    tok = workspace.CURRENT.set(b)
    try:
        idx = memory.index(refresh=True)
        assert "5174" not in idx and "Pedro" in idx
        assert [m["slug"] for m in memory.personal_list()] == ["nome"]
        memory.personal_write("Nome", "Nome: Pedro", "Pedro.", "projeto")  # mudou de tipo: sai do global
        assert not (tmp_path / "global" / "nome.md").exists()
    finally:
        workspace.CURRENT.reset(tok)

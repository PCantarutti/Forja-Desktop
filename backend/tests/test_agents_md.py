"""Fase 4 do porte do DeepSeek Harness: AGENTS.md por pasta e skills que o modelo carrega."""
import pytest

from app import agent, config, memory, skills, workspace
from app.tools import REGISTRY, ToolError, run_tool


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("regra da raiz", encoding="utf-8")
    app = tmp_path / "app"
    (app / "sub").mkdir(parents=True)
    (app / "CLAUDE.md").write_text("regra do app", encoding="utf-8")
    (app / "sub" / "AGENTS.md").write_text("regra da sub", encoding="utf-8")
    (app / "sub" / "x.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "dados")
    return tmp_path, app


def test_da_raiz_do_git_ate_a_pasta_da_conversa(proj):
    raiz, app = proj
    t = memory.instrucoes_workspace(app)
    assert t.index("regra da raiz") < t.index("regra do app")  # amplo antes do específico
    assert "regra da sub" not in t                              # subpasta só quando tocada
    assert "não passam por cima" in t


def test_subpasta_tocada_traz_as_instrucoes_dela(proj):
    _, app = proj
    t = memory.instrucoes_workspace(app, [str(app / "sub" / "x.py")])
    assert "Instruções adicionais de:" in t and "regra da sub" in t


def test_orcamento_descarta_o_mais_amplo(proj, monkeypatch):
    _, app = proj
    monkeypatch.setattr(memory, "MAX_INSTRUCOES", 15)
    t = memory.instrucoes_workspace(app)
    assert "regra do app" in t and "regra da raiz" not in t


def test_skill_de_pasta_entra_no_catalogo_e_carrega(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "dados")
    d = tmp_path / ".forja" / "skills" / "deploy"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy\ndescription: Sobe para produção\n---\nRode make deploy.",
                                encoding="utf-8")
    oculta = tmp_path / ".agents" / "skills" / "so-usuario"
    oculta.mkdir(parents=True)
    (oculta / "SKILL.md").write_text("---\ndisable-model-invocation: true\n---\nsó por /", encoding="utf-8")
    cat = skills.catalogo(tmp_path)
    assert "`deploy`: Sobe para produção" in cat and "so-usuario" not in cat
    assert "so-usuario" in [s["name"] for s in skills.list_for(tmp_path)]
    out = run_tool("skill", {"name": "deploy"}, tmp_path)
    assert '<skill_content name="deploy">' in out and "Rode make deploy." in out and str(d) in out
    with pytest.raises(ToolError, match="não existe"):
        run_tool("skill", {"name": "so-usuario"}, tmp_path)


def test_skill_so_aparece_com_skill_no_projeto(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "dados")
    tok = workspace.CURRENT.set(tmp_path)
    try:
        assert "skill" not in [t.name for t in agent.available_tools(None, "manual")]
        (tmp_path / ".forja" / "skills").mkdir(parents=True)
        (tmp_path / ".forja" / "skills" / "rev.md").write_text("Revise tudo.", encoding="utf-8")
        assert "skill" in [t.name for t in agent.available_tools(None, "manual")]
        assert "`rev`" in agent.contexto_runtime("manual", None, False, ["skill"])
    finally:
        workspace.CURRENT.reset(tok)
    assert REGISTRY["skill"].mutating is False

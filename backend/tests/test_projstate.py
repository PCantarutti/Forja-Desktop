"""Notas de sessão da Maestro: persistir o porquê, que as tarefas não guardam."""
import pytest

from app import agent, db, sessions, taskdb, workspace
from app.tools import ToolError


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def conv():
    with db.session() as s:
        c = db.Conversation(title="Maestro", kind="maestro")
        s.add(c)
        s.commit()
        conv_id = c.id
    token = taskdb.CONV.set(conv_id)
    yield conv_id
    taskdb.CONV.reset(token)


def test_nota_segue_o_formato_de_sessao(pasta):
    nome = sessions.write(pasta, None, "Implementar autenticação", "Login implementado",
                          ["JWT armazenado em httpOnly cookie"], ["Refresh token falha no Safari"],
                          "Testar renovação do token")
    assert nome == "SESSION-001.md"
    texto = (pasta / ".forja/maestro/SESSION-001.md").read_text("utf-8")
    for trecho in ("# SESSION-001", "## Objetivo", "Implementar autenticação", "## Decisões",
                   "- JWT armazenado em httpOnly cookie", "## Problemas", "Safari",
                   "## Próximo passo", "Testar renovação do token"):
        assert trecho in texto


def test_numera_em_sequencia_e_nunca_sobrescreve(pasta):
    assert sessions.write(pasta, None, "a", "", [], [], "") == "SESSION-001.md"
    assert sessions.write(pasta, None, "b", "", [], [], "") == "SESSION-002.md"
    assert "## Objetivo\na" in (pasta / ".forja/maestro/SESSION-001.md").read_text("utf-8")


def test_objetivo_e_obrigatorio(pasta):
    with pytest.raises(ToolError, match="objective"):
        sessions.write(pasta, None, "", "", [], [], "")


def test_tarefas_em_aberto_entram_sozinhas(pasta, conv):
    """A tarefa é desta conversa: uma sessão nova, em outra conversa, não a veria pelo list_tasks."""
    taskdb.create_feature(conv, "Auth", "Login", [
        {"title": "Modelo", "contract": {"goal": "User"}},
        {"title": "Rota", "contract": {"goal": "POST /login"}}])
    taskdb.set_status("TASK-002", "blocked", conv, reason="falta decidir o hash")
    sessions.write(pasta, conv, "Auth", "", [], [], "")
    texto = (pasta / ".forja/maestro/SESSION-001.md").read_text("utf-8")
    assert "TASK-001 [pending] Modelo" in texto
    assert "TASK-002 [blocked] Rota — falta decidir o hash" in texto


def test_tarefa_concluida_nao_polui_a_nota(pasta, conv):
    taskdb.create_feature(conv, "X", "", [{"title": "Feita", "contract": {"goal": "g"}}])
    for st in ("queued", "implementing", "testing", "reviewing", "completed"):
        taskdb.set_status("TASK-001", st, conv)
    sessions.write(pasta, conv, "X", "", [], [], "")
    assert "Feita" not in (pasta / ".forja/maestro/SESSION-001.md").read_text("utf-8")


def test_latest_pega_a_mais_recente(pasta):
    assert sessions.latest(pasta) is None
    sessions.write(pasta, None, "primeira", "", [], [], "")
    sessions.write(pasta, None, "segunda", "", [], [], "")
    nome, texto = sessions.latest(pasta)
    assert nome == "SESSION-002.md" and "segunda" in texto


def test_arquivo_estranho_na_pasta_e_ignorado(pasta):
    (pasta / ".forja/maestro").mkdir(parents=True)
    (pasta / ".forja/maestro/rascunho.md").write_text("x", "utf-8")
    (pasta / ".forja/maestro/SESSION-abc.md").write_text("x", "utf-8")
    assert sessions.write(pasta, None, "ok", "", [], [], "") == "SESSION-001.md"


def test_prompt_da_maestro_carrega_a_ultima_nota(pasta):
    """É isto que dá continuidade: uma conversa nova na pasta começa sabendo onde a outra parou."""
    sessions.write(pasta, None, "Auth", "", ["usar bcrypt, não sha256"], [], "rota de refresh")
    p = agent.system_prompt("native", set(), permission="auto", maestro_mode=True)
    assert "Onde a sessão anterior parou" in p and "usar bcrypt, não sha256" in p
    # e o agente comum não carrega nota de Maestro
    assert "Onde a sessão anterior parou" not in agent.system_prompt("native", set(), permission="auto")


def test_sem_nota_o_prompt_nao_muda(pasta):
    assert sessions.prompt_block(pasta) == ""


def test_nota_gigante_e_cortada_no_prompt(pasta):
    """A nota entra em toda requisição: sem teto, ela comeria a janela que a compactação libera."""
    sessions.write(pasta, None, "x" * 1000, "y" * 1000, ["d" * 1000] * 10, [], "")
    bloco = sessions.prompt_block(pasta)
    assert len(bloco) < sessions.MAX_NOTA_NO_PROMPT + 400 and "nota truncada" in bloco


def test_ferramenta_grava_na_pasta_da_conversa(pasta, conv):
    texto = sessions.SESSION_NOTE.handler(None, {"objective": "Fechar login", "decisions": ["JWT"]})
    assert "SESSION-001.md" in texto
    assert (pasta / ".forja/maestro/SESSION-001.md").is_file()


def test_session_note_nao_para_a_execucao_autonoma():
    """Marcada como mutante, pediria aprovação a cada nota."""
    from app import policy
    precisa, _ = policy.decide(sessions.SESSION_NOTE, {"objective": "x"}, "auto")
    assert not precisa

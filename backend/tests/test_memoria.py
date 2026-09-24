"""Memória sobre o usuário: índice barato no prompt, corpo sob demanda."""
import pytest

from app import agent, config, memory


@pytest.fixture(autouse=True)
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PERSONAL_MEMORY_DIR", tmp_path / "memoria")
    monkeypatch.setattr(config, "PERSONAL_MEMORY", True)
    memory.index(refresh=True)
    return tmp_path / "memoria"


def test_guarda_le_e_esquece(pasta):
    memory.personal_write("Prefere resposta curta", "Pedro quer direto ao ponto", "Sem preâmbulo.", "preferencia")

    itens = memory.personal_list()
    assert [(m["name"], m["type"]) for m in itens] == [("Prefere resposta curta", "preferencia")]
    assert (pasta / "prefere-resposta-curta.md").is_file()
    assert memory.personal_read("prefere-resposta-curta")["content"].startswith("Sem preâmbulo")

    assert memory.personal_delete(["prefere-resposta-curta"]) == 1
    assert memory.personal_list() == []


def test_descricao_e_obrigatoria(pasta):
    """A descrição é a única coisa que entra no prompt: sem ela a memória não serve para nada."""
    with pytest.raises(Exception):
        memory.personal_write("x", "  ", "corpo")


def test_indice_e_uma_linha_por_memoria_e_barato(pasta):
    for i in range(20):
        memory.personal_write(f"Fato {i}", f"Descrição curta número {i}", "corpo bem maior " * 50)

    idx = memory.index(refresh=True)

    assert len(idx.splitlines()) == 20
    # o corpo NÃO entra: 20 memórias têm que caber em poucas centenas de tokens
    assert "corpo bem maior" not in idx
    assert len(idx) < 1500


def test_indice_congela_durante_o_turno(pasta):
    memory.personal_write("Um", "primeiro", "corpo")
    antes = memory.index(refresh=True)

    memory.personal_write("Dois", "segundo", "corpo")

    # sem refresh o índice não muda: mexer no system prompt no meio do turno mata o cache do llama.cpp
    assert memory.index() == antes
    assert "Dois" in memory.index(refresh=True)


def test_bloco_do_prompt_respeita_o_interruptor(pasta, monkeypatch):
    memory.personal_write("Um", "primeiro", "corpo")
    memory.index(refresh=True)

    assert "O que você já sabe sobre o usuário" in memory.prompt_block()

    monkeypatch.setattr(config, "PERSONAL_MEMORY", False)
    assert memory.prompt_block() == ""


def test_fato_curto_entra_inteiro_no_indice(pasta):
    """Modelo local quase nunca chama recall: "Nome do usuário" sem o nome não servia para nada."""
    memory.personal_write("nome", "Nome do usuário", "O nome do usuário é Pedro.")
    memory.personal_write("Longa", "Descrição da longa", "detalhe " * 100)

    idx = memory.index(refresh=True)

    assert "- nome (usuario): O nome do usuário é Pedro." in idx
    assert "Descrição da longa (detalhes: recall)" in idx and "detalhe detalhe" not in idx


def test_ferramentas_entram_no_system_prompt(pasta):
    memory.personal_write("Hardware", "Arc B580 de 12 GB", "Vulkan é o backend certo. " * 20)
    memory.index(refresh=True)

    prompt = agent.system_prompt("native")

    assert "remember" in prompt and "Arc B580 de 12 GB" in prompt and agent.REGRA_MEMORIA in prompt
    assert "Vulkan é o backend certo" not in prompt  # corpo longo só com recall


def test_chat_e_maestro_tambem_gravam(pasta):
    chat = agent.system_prompt("native", chat=True)
    assert agent.REGRA_MEMORIA in chat
    assert {"remember", "recall"} <= {t.name for t in agent.chat_tools()}
    assert agent.REGRA_MEMORIA in agent.system_prompt("native", maestro_mode=True)
    assert not memory.REGISTRY["remember"].mutating  # grava sem card de aprovação

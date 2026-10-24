import asyncio
from datetime import datetime, timedelta

from app import assistente, conhecimento, memory

SPEC = {"provider": "local", "model": "m"}


def _falas(monkeypatch):
    falas = []
    monkeypatch.setattr(assistente, "fala", falas.append)
    monkeypatch.setattr(assistente, "_manda", lambda *a, **k: None)
    return falas


def test_indice_de_documentos(tmp_path, monkeypatch):
    monkeypatch.setattr(conhecimento.config, "DATA_DIR", tmp_path / "dados")
    docs = tmp_path / "docs"
    (docs / "Contratos").mkdir(parents=True)
    (docs / ".obsidian").mkdir()
    (docs / "Contratos" / "contrato-aluguel.md").write_text("Contrato de locação. O contrato vence em 31/03/2027.", "utf-8")
    (docs / "receitas.txt").write_text("Bolo de cenoura: 3 cenouras, 4 ovos.", "utf-8")
    (docs / ".obsidian" / "config.md").write_text("contrato vence nada", "utf-8")
    achados = conhecimento.busca("quando vence meu contrato de aluguel?", [str(docs)])
    assert [a["arquivo"] for a in achados] == ["contrato-aluguel.md"]          # .obsidian fica de fora
    assert conhecimento.estado()["arquivos"] == 2
    assert conhecimento.atualiza([str(docs)]) == 0                              # nada mudou: não relê
    (docs / "receitas.txt").unlink()
    conhecimento.atualiza([str(docs)])
    assert conhecimento.busca("bolo de cenoura", [str(docs)]) == []
    assert conhecimento.busca("contrato", [str(tmp_path / "outra")]) == []      # pasta tirada da lista não vale


def test_pergunta_sem_pista_que_esta_nos_documentos(tmp_path, monkeypatch):
    # "quem é o síndico?" não tem palavra da PISTA: só o índice dos documentos leva ao classificador
    monkeypatch.setattr(conhecimento.config, "DATA_DIR", tmp_path / "dados")
    docs = tmp_path / "cofre"
    docs.mkdir()
    (docs / "Síndico e condomínio.md").write_text("O síndico é o Rogério Tavares, apto 802.", "utf-8")
    conhecimento.atualiza([str(docs)])
    monkeypatch.setattr(assistente, "ler", lambda: {**assistente.PADRAO, "pastas_documentos": [str(docs)]})
    assert not assistente.PISTA.search(assistente._sem_acento("quem é o síndico do prédio?"))
    assert asyncio.run(assistente._nos_documentos("quem é o síndico do prédio?"))
    assert not asyncio.run(assistente._nos_documentos("qual a capital da Austrália?"))
    monkeypatch.setattr(assistente, "ler", lambda: dict(assistente.PADRAO))       # sem pastas: nem consulta
    assert not asyncio.run(assistente._nos_documentos("quem é o síndico do prédio?"))


def test_notas(tmp_path, monkeypatch):
    monkeypatch.setattr(conhecimento, "NOTAS", tmp_path / "notas.json")
    falas = _falas(monkeypatch)
    asyncio.run(assistente._saber_voz("nota", {"acao": "anotar", "texto": "comprar café"}, SPEC, None))
    assert falas[-1] == "Anotado: comprar café." and conhecimento.notas()[0]["texto"] == "comprar café"
    asyncio.run(assistente._saber_voz("nota", {"acao": "listar", "quando": "hoje"}, SPEC, None))
    assert falas[-1] == "Hoje você anotou: comprar café."
    asyncio.run(assistente._saber_voz("nota", {"acao": "listar", "quando": "ontem"}, SPEC, None))
    assert falas[-1] == "Ontem você não anotou nada."
    asyncio.run(assistente._saber_voz("nota", {"acao": "apagar", "texto": "café"}, SPEC, None))
    assert conhecimento.notas() == []


def test_memoria_pessoal_guarda_usa_e_esquece(tmp_path, monkeypatch):
    monkeypatch.setattr(memory.config, "PERSONAL_MEMORY_DIR", tmp_path / "memoria")
    monkeypatch.setattr(memory, "project_dir", lambda: tmp_path / "projeto")
    falas = _falas(monkeypatch)
    asyncio.run(assistente._saber_voz("memoria", {"acao": "guardar", "assunto": "carro",
                                                  "texto": "O carro da pessoa é um Onix prata, placa ABC1D23"}, SPEC, None))
    assert falas[-1] == "Guardado."
    assert "Onix prata" in assistente._memorias_txt()                  # o chat falado passa a saber
    asyncio.run(assistente._saber_voz("memoria", {"acao": "esquecer", "assunto": "carro"}, SPEC, None))
    assert falas[-1] == "Esqueci: carro." and assistente._memorias_txt() == ""


def test_documentos_responde_com_os_trechos(monkeypatch):
    falas = _falas(monkeypatch)
    monkeypatch.setattr(assistente, "ler", lambda: {**assistente.PADRAO, "pastas_documentos": ["C:/docs"]})
    monkeypatch.setattr(conhecimento, "busca", lambda p, pastas, n=6: [{"arquivo": "contrato.pdf", "caminho": "x", "trecho": "vence em março"}])
    vistos = []

    async def gera(spec, slot, sistema, usuario, max_tokens=400):
        vistos.append(usuario)
        return "Segundo contrato.pdf, vence em março."
    monkeypatch.setattr(assistente, "_gera", gera)
    asyncio.run(assistente._saber_voz("documentos", {"pergunta": "quando vence o contrato"}, SPEC, None))
    assert falas[-1] == "Segundo contrato.pdf, vence em março." and "<<<DADOS>>>" in vistos[0]
    monkeypatch.setattr(assistente, "ler", lambda: dict(assistente.PADRAO))
    asyncio.run(assistente._saber_voz("documentos", {"pergunta": "x"}, SPEC, None))
    assert "Escolha as pastas" in falas[-1]


def test_card_e_codigo_no_projeto_certo(monkeypatch):
    from app import board, codebusca
    falas = _falas(monkeypatch)
    monkeypatch.setattr(assistente, "_projetos", lambda: {r"C:\Projetos\loja-virtual": 1, r"C:\Projetos\Forja\forja-desktop": 2})
    criados = []
    monkeypatch.setattr(board, "projeto_de", lambda pasta: pasta)
    monkeypatch.setattr(board, "criar", lambda projeto, dados, origem="manual": criados.append((projeto, dados["titulo"])) or ({}, True))
    asyncio.run(assistente._saber_voz("card", {"projeto": "Forja", "titulo": "bug no login"}, SPEC, None))
    assert criados == [(r"C:\Projetos\Forja\forja-desktop", "bug no login")]
    assert falas[-1] == "Criei o card bug no login no board de forja-desktop."
    monkeypatch.setattr(codebusca, "code_search", lambda root, args: f"backend/app/uploads.py — salvar ({args['query']})")

    async def gera(spec, slot, sistema, usuario, max_tokens=400):
        return "Fica em uploads.py." if "uploads.py" in usuario else "?"
    monkeypatch.setattr(assistente, "_gera", gera)
    asyncio.run(assistente._saber_voz("codigo", {"projeto": "", "pergunta": "upload de imagem"}, SPEC, None))
    assert falas[-1] == "Fica em uploads.py."
    asyncio.run(assistente._saber_voz("card", {"projeto": "geladeira", "titulo": "x"}, SPEC, None))
    assert "Não achei o projeto geladeira" in falas[-1]


def test_pergunta_de_codigo_nao_gera_imagem():
    for frase in ["Onde é que tá o código que faz o upload de imagem?", "Qual é a parte do projeto que faz o upload de imagem?"]:
        assert assistente.rota(frase)[0] == "chat", frase
    assert assistente.rota("crie uma imagem de um gato esquiando")[0] == "imagem"


def test_consulta_do_forja_nao_vira_trabalho_novo():
    for frase in ["cria um card no board do Forja: bug no login", "onde o Forja trata o upload de imagem?",
                  "o que o agente fez no projeto e-commerce hoje?", "em que arquivo o forja salva as configurações?"]:
        assert assistente.SABER.search(assistente._sem_acento(frase)), frase
        assert assistente.PISTA.search(assistente._sem_acento(frase)), frase
    assert not assistente.SABER.search(assistente._sem_acento("no modo agente crie um site de receitas"))


def test_historico_junta_conversas_e_commits(monkeypatch):
    falas = _falas(monkeypatch)
    monkeypatch.setattr(assistente, "_projetos", lambda: {r"C:\Projetos\loja": 1})
    monkeypatch.setattr(assistente, "_historico", lambda pasta, dia: ["Conversa (agent) \"checkout\": terminei o carrinho"])

    async def gera(spec, slot, sistema, usuario, max_tokens=400):
        return "Hoje o agente terminou o carrinho." if "carrinho" in usuario else "?"
    monkeypatch.setattr(assistente, "_gera", gera)
    asyncio.run(assistente._saber_voz("historico", {"projeto": "loja", "quando": "hoje"}, SPEC, None))
    assert falas[-1] == "Hoje o agente terminou o carrinho."


def test_fala_sem_markdown():
    assert assistente._falavel("FALADO. O upload fica em **`backend/app/uploads.py`**, na função `salvar`.") == \
        "O upload fica em backend/app/uploads.py, na função salvar."


def test_classificador_memoria_sem_texto_vira_pergunta(monkeypatch):
    # "qual é o meu carro?" chegava ao classificador (casou com o diário) e voltava memoria sem texto: "O que eu guardo?"
    from app import llm
    respostas = []

    async def stream(*a, **k):
        yield "content", respostas.pop(0)
    monkeypatch.setattr(llm, "chat_stream", stream)
    respostas.append('{"tipo":"memoria","acao":"consultar","assunto":"carro"}')
    assert asyncio.run(assistente._classifica_acao(SPEC, None, "qual é o meu carro?")) is None
    respostas.append('{"tipo":"memoria","acao":"guardar","assunto":"carro","texto":"O carro é um Onix"}')
    assert asyncio.run(assistente._classifica_acao(SPEC, None, "lembra que meu carro é um Onix"))[0] == "memoria"
    respostas.append('{"tipo":"memoria","acao":"esquecer","assunto":"carro"}')
    assert asyncio.run(assistente._classifica_acao(SPEC, None, "esquece o carro"))[0] == "memoria"


def test_navegador_do_forja_nao_vira_chat_no_forja():
    s = assistente._sem_acento("abre o youtube no navegador do Forja")
    assert assistente.CITA_FORJA.search(s) and assistente.SABER.search(s)   # vai para o _chat, que abre o site
    assert assistente.PISTA.search(s)

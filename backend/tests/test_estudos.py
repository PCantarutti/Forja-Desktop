import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import config, db, estudos, mirror, pesquisa, web
from app.tools import ToolError


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(estudos, "RAIZ", tmp_path / "estudos")
    monkeypatch.setattr(config, "SUBAGENTS", {})
    estudos._RUNS.clear()
    yield
    estudos._RUNS.clear()


def _estudo() -> int:
    with db.session() as s:
        c = db.Conversation(kind="estudos")
        s.add(c)
        s.commit()
        return c.id


SECAO = re.compile(r'Comece exatamente com "(## .+?)"')


def _fake_llm(monkeypatch, respostas: dict | None = None, pausa=0.0):
    """Responde pelo tipo do prompt de sistema; a seção devolve o título que o prompt pediu."""
    respostas = respostas or {}
    chamados: list[str] = []

    def qual(system: str) -> str:
        for prefixo, tipo in (("Você prepara material", "notas"), ("Você analisa uma prova", "perfil"),
                              ("Você é um professor montando", "plano"), ("Você é um professor escrevendo", "secao"),
                              ("Você fecha um resumo", "revisao"), ("Você é um pesquisador. Recebe", "plano_web"),
                              ("Você é um pesquisador. As fontes", "buscas"), ("Você lê UMA página", "extracao")):
            if system.startswith(prefixo):
                return tipo
        return "?"

    padrao = {
        "notas": "- Mitocôndria produz ATP [p. 2]\n- Respiração celular tem 3 etapas [p. 3]",
        "perfil": '{"banca": "ENEM", "formato": "múltipla escolha", "alternativas": 5, "estilo": "texto-base longo", '
                  '"topicos": ["respiração celular", "fotossíntese"], "questoes": 10}',
        "plano": '{"titulo": "Biologia celular", "visao_geral": "Como a célula produz energia.", "topicos": ['
                 '{"titulo": "Mitocôndria", "objetivo": "entender o ATP", "pontos": ["ATP", "membranas"]},'
                 '{"titulo": "Respiração celular", "objetivo": "as etapas", "pontos": ["glicólise", "Krebs"]}]}',
        "revisao": "## Revisão rápida\n\n| Conceito | O que lembrar |\n|---|---|\n| ATP | energia |",
        "plano_web": '{"perguntas": ["o que é"], "buscas": ["mitocondria atp", "respiracao celular etapas"]}',
        "buscas": '["ciclo de krebs"]',
        "extracao": "RELEVANTE: sim\nRESUMO: A mitocôndria é a usina da célula e produz a maior parte do ATP "
                    "pela fosforilação oxidativa, segundo o livro-texto de referência.\nTRECHO: produz ATP",
    }

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        tipo = qual(system)
        chamados.append(tipo)
        if pausa:
            await asyncio.sleep(pausa)
        if tipo == "secao":
            texto = respostas.get("secao") or f"{SECAO.search(system).group(1)}\n\nTexto da seção [p. 2]."
        else:
            texto = respostas.get(tipo, padrao.get(tipo, ""))
        if isinstance(texto, Exception):
            raise texto
        yield ("content", texto)
        yield ("done", {"prompt_tokens": 10, "completion_tokens": 20})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)
    return chamados


async def _ate_acabar():
    while estudos._TAREFAS:
        await asyncio.gather(*list(estudos._TAREFAS))


def _rodar(conv, **kw):
    async def main():
        msg = estudos.start(conv, kw.pop("tema", "Biologia celular"), kw.pop("preferencias", None),
                            kw.pop("web", False), kw.pop("profundidade", "rapida"), "fake", "m", **kw)
        await _ate_acabar()
        return estudos.estado(msg["id"])
    return asyncio.run(main())


# ------------------------------------------------------------------ material


def test_material_de_texto_vira_arquivo_e_mensagem():
    conv = _estudo()
    m = estudos.adicionar_material(conv, "anotações", texto="A mitocôndria produz ATP.")
    assert m["nome"] == "anotações.txt" and m["uso"] == "conteudo" and m["chars"] == 25
    assert (estudos.pasta(conv) / "material" / "01.txt").read_text(encoding="utf-8") == "A mitocôndria produz ATP."
    assert [x["id"] for x in estudos.materiais(conv)] == [m["id"]]


def test_prova_e_reconhecida_e_pode_ser_trocada():
    conv = _estudo()
    prova = "\n".join(f"Questão {i}\nEnunciado\na) um\nb) dois\nc) três\nd) quatro" for i in range(1, 4))
    m = estudos.adicionar_material(conv, "simulado.txt", texto=prova)
    assert m["uso"] == "prova"
    assert estudos.alterar_material(m["id"], "conteudo")["uso"] == "conteudo"
    with pytest.raises(ToolError):
        estudos.alterar_material(m["id"], "outra")


def test_remover_material_apaga_os_arquivos():
    conv = _estudo()
    m = estudos.adicionar_material(conv, "a.md", texto="texto")
    estudos.remover_material(m["id"])
    assert estudos.materiais(conv) == []
    assert list((estudos.pasta(conv) / "material").iterdir()) == []


def test_formato_desconhecido_e_arquivo_sem_texto_sao_recusados():
    conv = _estudo()
    with pytest.raises(ToolError, match="não é lido"):
        estudos.adicionar_material(conv, "x.exe", dados=b"MZ")
    with pytest.raises(ToolError, match="Não achei texto"):
        estudos.adicionar_material(conv, "vazio.txt", dados=b"   ")
    assert not any((estudos.pasta(conv) / "material").iterdir())


def test_material_so_entra_em_estudo():
    with db.session() as s:
        c = db.Conversation(kind="chat")
        s.add(c)
        s.commit()
        chat = c.id
    with pytest.raises(ToolError, match="Estudo não encontrado"):
        estudos.adicionar_material(chat, "a.txt", texto="x")


def test_pedacos_repetem_a_marca_da_pagina():
    texto = "--- página 1 ---\n" + "\n\n".join(["frase " * 50] * 6) + "\n\n--- página 2 ---\nfim"
    pedacos = estudos._pedacos(texto, tamanho=700)
    assert len(pedacos) > 2
    assert pedacos[1].startswith("--- página 1 (continuação) ---")
    assert "--- página 2 ---" in pedacos[-1]


def test_pdf_escaneado_com_marca_dagua_passa_pelo_ocr(monkeypatch, tmp_path):
    marca = "\n\n".join(f"--- página {i} ---\npcimarkpci MDAwMDo{'A' if i < 7 else 'B'}==\nwww.pciconcursos.com.br"
                        for i in range(1, 13))
    assert estudos._pouco_texto(f"12 página(s).\n\n{marca}")
    assert not estudos._pouco_texto("\n\n".join(f"--- página {i} ---\nconteúdo da página {i}: " + "texto de verdade " * 30
                                                for i in range(1, 4)))
    lido = "\n\n".join(f"--- página {i} ---\nQuestão {i}: enunciado lido pelo OCR " * 5 for i in range(1, 13))
    monkeypatch.setattr(estudos.documentos, "extrair", lambda arq: f"12 página(s).\n\n{marca}")
    monkeypatch.setattr(estudos.documentos, "extrair_ocr", lambda arq: lido)
    assert estudos._extrair(tmp_path / "prova.pdf", b"%PDF") == (lido, True)
    monkeypatch.setattr(estudos.documentos, "extrair_ocr", lambda arq: None)   # sem OCR: fica o pouco que havia
    assert estudos._extrair(tmp_path / "prova.pdf", b"%PDF")[1] is False


def test_glifos_das_alternativas_viram_letras():
    assert estudos._glifos("vale:\n/L57840100m\n/L57842250m") == "vale:\n\nA) 100m\n\nC) 250m"


def test_amostra_cobre_o_texto_inteiro():
    texto = "".join(f"[{i:03d}]" + "x" * 95 for i in range(1000))   # 100 mil caracteres, marcados a cada 100
    a = estudos._amostra(texto, 5000)
    assert "[000]" in a and "[999]" in a and len(a) < 5200
    assert estudos._amostra("curto", 5000) == "curto"


def test_so_com_simulado_o_resumo_le_o_simulado(monkeypatch):
    chamados = _fake_llm(monkeypatch)
    conv = _estudo()
    prova = "\n".join(f"Questão {i}\nEnunciado sobre mitocôndria e ATP\na) x\nb) y\nc) z\nResolução: a mitocôndria [p. 3]"
                      for i in range(1, 6))
    m = estudos.adicionar_material(conv, "simulado.txt", texto=prova)
    assert m["uso"] == "prova"
    e = _rodar(conv)
    assert e["status"] == "pronto"
    assert e["materiais"][0]["pedacos"] == 1 and "perfil" in chamados   # leu como conteúdo E tirou o perfil
    assert "Sem material" not in e["aviso"]


def test_areas_do_perfil_somam_um_e_ignoram_lixo():
    assert estudos.areas([{"area": "Matemática", "peso": 45}, {"area": "Física", "peso": "15%"},
                          {"area": "", "peso": 1}, {"area": "Química", "peso": "muito"}, "lixo"]) == [
        {"area": "Matemática", "peso": 0.75}, {"area": "Física", "peso": 0.25}]
    assert estudos.areas(None) == []


def test_perfil_guarda_as_areas_e_o_roteiro_a_area_do_topico(monkeypatch):
    perfil = ('{"banca": "ENEM", "alternativas": 5, "topicos": ["funções"], '
              '"areas": [{"area": "Matemática", "peso": 0.5}, {"area": "Biologia", "peso": 0.5}]}')
    plano = ('{"titulo": "T", "visao_geral": "v", "topicos": [{"titulo": "Funções", "area": "Matemática", "objetivo": "o", '
             '"pontos": ["p"]}, {"titulo": "Células", "area": "Biologia", "objetivo": "o", "pontos": ["p"]}]}')
    _fake_llm(monkeypatch, {"perfil": perfil, "plano": plano})
    conv = _estudo()
    estudos.adicionar_material(conv, "simulado.txt", texto="\n".join(f"Questão {i}\na) 1\nb) 2\nc) 3" for i in range(5)))
    e = _rodar(conv)
    assert e["perfil"]["areas"] == [{"area": "Matemática", "peso": 0.5}, {"area": "Biologia", "peso": 0.5}]
    assert [t["area"] for t in e["topicos"]] == ["Matemática", "Biologia"]


def test_selecionar_prefere_o_que_fala_do_topico():
    itens = [{"nome": "a", "cabeca": "[a]", "texto": "fotossíntese clorofila luz " * 20},
             {"nome": "b", "cabeca": "[b]", "texto": "mitocôndria respiração energia " * 20}]
    escolhido = estudos._selecionar(itens, "respiração na mitocôndria", teto=700)
    assert "[b]" in escolhido and "[a]" not in escolhido
    assert estudos._selecionar(itens, "qualquer", teto=10_000).count("[") == 2   # cabe tudo: vai tudo


# ------------------------------------------------------------------ resumo


def test_resumo_com_material_pequeno_vai_direto_para_a_escrita(monkeypatch):
    chamados = _fake_llm(monkeypatch)
    conv = _estudo()
    estudos.adicionar_material(conv, "apostila.txt", texto="--- página 2 ---\nA mitocôndria produz ATP.")
    e = _rodar(conv, preferencias={"extras": ["quadro"], "nivel": "iniciante"})
    assert e["status"] == "pronto", e["aviso"]
    assert "notas" not in chamados   # coube inteiro: sem extrator no meio
    assert chamados.count("secao") == 2 and "revisao" in chamados
    t = e["texto"]
    assert t.startswith("# Biologia celular") and "## 1. Mitocôndria" in t and "## 2. Respiração celular" in t
    assert "## Revisão rápida" in t and "## Fontes" in t and "apostila.txt" in t
    assert [x["status"] for x in e["topicos"]] == ["pronto", "pronto"]
    with db.session() as s:
        assert s.get(db.Conversation, conv).title == "Biologia celular"


def test_material_grande_passa_pelo_extrator_e_prova_vira_perfil(monkeypatch):
    chamados = _fake_llm(monkeypatch)
    conv = _estudo()
    grande = "\n\n".join(f"--- página {i} ---\n" + "conteúdo " * 400 for i in range(1, 15))
    estudos.adicionar_material(conv, "livro.txt", texto=grande)
    prova = "\n".join(f"Questão {i}\na) x\nb) y\nc) z" for i in range(1, 5))
    estudos.adicionar_material(conv, "enem.txt", texto=prova)
    e = _rodar(conv)
    assert e["status"] == "pronto", e["aviso"]
    livro = next(m for m in e["materiais"] if m["nome"] == "livro.txt")
    assert livro["pedacos"] > 1 and livro["feitos"] == livro["pedacos"] == chamados.count("notas")
    assert e["perfil"]["banca"] == "ENEM" and "fotossíntese" in e["perfil"]["topicos"]


def test_web_usa_as_etapas_da_pesquisa_sem_gravar_meta_de_pesquisa(monkeypatch):
    _fake_llm(monkeypatch)
    monkeypatch.setattr(web, "buscar", lambda q, n=6: [{"title": f"Página {q}", "url": f"https://{q.split()[0]}.com/x", "content": ""}])
    monkeypatch.setattr(web, "ler", lambda url, n=0: {"url": url, "title": "Mitocôndria", "text": "texto " * 50, "imagem": ""})
    conv = _estudo()
    e = _rodar(conv, web=True)
    assert e["status"] == "pronto", e["aviso"]
    assert any(f["status"] == "util" for f in e["fontes"])
    assert "(https://mitocondria.com/x)" in e["texto"]   # a fonte útil entra no fim
    with db.session() as s:
        m = s.get(db.Message, e["message_id"])
        assert "pesquisa" not in m.meta   # o _persistir da pesquisa foi desviado para o do estudo


def test_roteiro_quebrado_vira_um_topico_so(monkeypatch):
    _fake_llm(monkeypatch, {"plano": "não sei fazer JSON"})
    conv = _estudo()
    e = _rodar(conv, tema="Guerra Fria")
    assert e["status"] == "pronto"
    assert [t["titulo"] for t in e["topicos"]] == ["Guerra Fria"]
    assert "Sem material e sem web" in e["aviso"]


def test_secao_que_falha_nao_derruba_o_resumo(monkeypatch):
    _fake_llm(monkeypatch)
    real = pesquisa.llm.chat_stream
    vez = {"n": 0}

    async def as_vezes(provider, model, messages, *a, **kw):
        if messages[0]["content"].startswith("Você é um professor escrevendo"):
            vez["n"] += 1
            if vez["n"] == 1:
                raise RuntimeError("caiu")
        async for x in real(provider, model, messages, *a, **kw):
            yield x

    monkeypatch.setattr(pesquisa.llm, "chat_stream", as_vezes)
    e = _rodar(_estudo())
    assert e["status"] == "pronto" and "## 2. Respiração celular" in e["texto"]
    assert [t["status"] for t in e["topicos"]] == ["erro", "pronto"]
    assert "seção 1 falhou" in e["aviso"]


def test_cancelar_guarda_o_que_ja_saiu(monkeypatch):
    _fake_llm(monkeypatch, pausa=0.05)
    conv = _estudo()

    async def main():
        msg = estudos.start(conv, "Biologia", None, False, "rapida", "fake", "m")
        while not any(t["status"] == "pronto" for t in estudos._RUNS[msg["id"]]["topicos"]):
            await asyncio.sleep(0.01)
        estudos.cancelar(msg["id"])
        await _ate_acabar()
        return estudos.estado(msg["id"])

    e = asyncio.run(main())
    assert e["status"] == "cancelado" and "## 1. Mitocôndria" in e["texto"]
    assert "## 2." not in e["texto"]   # a seção que estava sendo escrita não entra pela metade
    assert [t["status"] for t in e["topicos"]] == ["pronto", "fila"]


def test_reap_solta_estudo_preso():
    conv = _estudo()
    with db.session() as s:
        m = db.Message(conversation_id=conv, role="assistant", status="running",
                       meta={"estudos": {"tipo": "resumo", "tema": "x", "etapa": "escrita"}})
        s.add(m)
        s.commit()
        mid = m.id
    assert estudos.reap() == 1
    e = estudos.estado(mid)
    assert e["status"] == "erro" and "fechou no meio" in e["aviso"]


def test_validacoes_do_start(monkeypatch):
    conv = _estudo()
    with pytest.raises(ToolError, match="tema"):
        estudos.start(conv, " ", provider="fake", model="m")
    with pytest.raises(ToolError, match="modelo"):
        estudos.start(conv, "x")
    monkeypatch.setattr(config, "MCP_SERVIDOR", False)
    with pytest.raises(ToolError, match="Claude"):
        estudos.start(conv, "x", provider=estudos.MOTOR_CLAUDE)


# ------------------------------------------------------------------ Claude via MCP


def test_pedido_ao_claude_fica_aguardando_e_ele_atende(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    conv = _estudo()
    mat = estudos.adicionar_material(conv, "aula.txt", texto="--- página 1 ---\num\n\n--- página 2 ---\ndois")
    msg = estudos.start(conv, "Citologia", {"tamanho": "curto", "extras": ["quadro"]}, True, "normal", estudos.MOTOR_CLAUDE)
    assert estudos.estado(msg["id"])["status"] == "aguardando" and not estudos._RUNS
    texto = asyncio.run(estudos.mcp_pedidos(0))
    assert f"PEDIDO {msg['id']}" in texto and "Citologia" in texto and f"material {mat['id']}" in texto
    assert "(3 a 4 tópicos)" in texto
    assert "Revisão rápida" in texto   # o extra que no motor do Forja é uma chamada à parte vai como pedido
    assert "dois" in estudos.mcp_ler_material(mat["id"], 2) and "um" not in estudos.mcp_ler_material(mat["id"], 2)
    assert "pedido(s) na tela Estudos" in estudos.mcp_listar()
    md = "# Citologia\n\nVisão.\n\n## 1. Membrana\n\ntexto [p. 1]\n\n## Fontes\n\n- aula"
    r = estudos.mcp_salvar_resumo(md, pedido_id=msg["id"], fontes=[{"titulo": "Wiki", "url": "https://w.org"}],
                                  modelo="Claude Opus 5.5")
    assert "gravado" in r
    e = estudos.estado(msg["id"])
    assert e["status"] == "pronto" and e["texto"] == md and e["stats"]["escritor"] == "Claude Opus 5.5"
    assert [t["titulo"] for t in e["topicos"]] == ["Membrana"] and e["fontes"][0]["url"] == "https://w.org"
    assert asyncio.run(estudos.mcp_pedidos(0)) == "Nenhum pedido pendente na tela Estudos."
    assert "já atendido" in estudos.mcp_salvar_resumo(md, pedido_id=msg["id"])


def test_claude_conduz_sem_pedido(tmp_path):
    arq = tmp_path / "resumo da aula.md"
    arq.write_text("Anotações sobre mitose.", encoding="utf-8")
    cid = int(re.search(r"Estudo (\d+)", estudos.mcp_criar("Mitose")).group(1))
    assert "resumo da aula.md" in estudos.mcp_anexar(cid, caminho=str(arq))
    assert "ERRO" in estudos.mcp_anexar(cid, caminho=str(tmp_path / "nao-existe.pdf"))
    estudos.mcp_salvar_resumo("# Mitose\n\n## 1. Fases\n\nprófase", conv_id=cid)
    p = estudos.projeto(cid)
    assert p["resumo"]["motor"] == "claude" and p["resumo"]["texto"].startswith("# Mitose")
    assert "Mitose" in estudos.mcp_listar() and "resumo da aula.md" in estudos.mcp_abrir(cid)


def test_cancelar_pedido_que_o_claude_nao_pegou(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    conv = _estudo()
    msg = estudos.start(conv, "x", provider=estudos.MOTOR_CLAUDE)
    estudos.cancelar(msg["id"])
    assert estudos.estado(msg["id"])["status"] == "cancelado"
    assert estudos.pedidos() == []


# ------------------------------------------------------------------ API


def test_api_do_estudo(monkeypatch):
    from app.main import app
    _fake_llm(monkeypatch)
    with TestClient(app) as c:
        conv = c.post("/api/conversations", json={"kind": "estudos"}).json()["id"]
        m = c.post(f"/api/estudos/{conv}/material", files={"file": ("aula.md", b"# Aula\nmitocondria")}).json()
        assert m["nome"] == "aula.md"
        assert c.post(f"/api/estudos/{conv}/material/texto", json={"texto": "colado"}).json()["nome"] == "texto colado.txt"
        assert c.patch(f"/api/estudos/material/{m['id']}", json={"uso": "prova"}).json()["uso"] == "prova"
        assert c.post(f"/api/estudos/{conv}/material", files={"file": ("x.exe", b"MZ")}).status_code == 400
        linhas = []
        with c.stream("POST", f"/api/estudos/{conv}/estudar",
                      json={"tema": "Células", "web": False, "provider": "fake", "model": "m"}) as r:
            for linha in r.iter_lines():
                if linha.startswith("data: "):
                    linhas.append(json.loads(linha[6:]))
        assert linhas[-1]["status"] == "pronto" and "## 1. Mitocôndria" in linhas[-1]["texto"]
        p = c.get(f"/api/estudos/{conv}").json()
        assert len(p["materiais"]) == 2 and p["resumo"]["status"] == "pronto" and p["rodando"] is None
        assert c.get(f"/api/estudos/execucao/{p['resumo']['message_id']}/stream").status_code == 200
        pasta = estudos.pasta(conv)
        assert pasta.exists()
        assert c.delete(f"/api/conversations/{conv}").json()["ok"]
        assert not pasta.exists()


def test_activity_acende_durante_o_estudo(monkeypatch):
    from app.main import get_activity
    _fake_llm(monkeypatch, pausa=0.05)
    conv = _estudo()

    async def main():
        estudos.start(conv, "Biologia", None, False, "rapida", "fake", "m")
        await asyncio.sleep(0.02)
        a = await get_activity()
        await _ate_acabar()
        return a

    a = asyncio.run(main())
    assert next(c for c in a["conversations"] if c["id"] == conv)["running"]


def test_ferramentas_mcp_do_estudo_existem():
    from app import mcp_servidor
    nomes = {t.name for t in mcp_servidor.SERVIDOR._tool_manager.list_tools()}
    assert {"estudos_listar", "estudos_criar", "estudos_abrir", "estudos_ler_material", "estudos_anexar",
            "estudos_salvar_resumo", "estudos_pedidos"} <= nomes

import pytest
from fastapi.testclient import TestClient

from app import config, conteudo, db, llm
from app.main import app
from app.tools import ToolError

README = """# Estilos

## Estilos disponíveis

| Estilo | Para quê | Tom | Duração |
|---|---|---|---|
| [`alerta-tech`](alerta-tech.md) | Notícia de IA | Urgente | 50–75s |

## Vídeos já feitos
"""


@pytest.fixture(autouse=True)
def limpo():
    with db.session() as s:
        s.query(db.AppSetting).filter(db.AppSetting.key == conteudo.CHAVE).delete()
        s.query(db.Conversation).filter(db.Conversation.kind == conteudo.KIND).delete()
        s.commit()


@pytest.fixture
def pasta(tmp_path):
    d = tmp_path / "estilos"
    d.mkdir()
    (d / "README.md").write_text(README, encoding="utf-8")
    (d / "alerta-tech.md").write_text("# Estilo: `alerta-tech`\n\nNotícia de IA com virada.\n\n## Roteiro\n",
                                      encoding="utf-8")
    conteudo.salvar_pastas({"pasta_estilos": str(d)})
    return d


@pytest.fixture
def cliente():
    return TestClient(app, headers={"x-forja-token": config.API_TOKEN, "origin": "http://testserver"})


# ------------------------------------------------------------------ pastas

def test_pastas_padrao_e_validacao(tmp_path):
    p = conteudo.pastas()
    assert p["pasta_estilos"] == "" and p["pasta_saida"].endswith("Desktop")
    assert "npx remotion *" in p["comandos"]
    with pytest.raises(ToolError, match="não existe"):
        conteudo.salvar_pastas({"pasta_estilos": str(tmp_path / "nao-existe")})
    with pytest.raises(ToolError, match="não existe"):
        conteudo.salvar_pastas({"pasta_projeto": "relativa/assim"})
    salvo = conteudo.salvar_pastas({"pasta_projeto": str(tmp_path), "comandos": ["npm run *", " ", "ffmpeg *"]})
    assert salvo["pasta_projeto"] == str(tmp_path.resolve())
    assert salvo["comandos"] == ["npm run *", "ffmpeg *"]
    assert conteudo.pastas()["pasta_projeto"] == str(tmp_path.resolve())


# ------------------------------------------------------------------ estilos

def test_estilos_sem_pasta():
    with pytest.raises(ToolError, match="pasta de estilos"):
        conteudo.estilos()


def test_lista_ignora_readme_e_modelo(pasta):
    (pasta / "_modelo.md").write_text("# modelo", encoding="utf-8")
    nomes = [e["nome"] for e in conteudo.estilos()]
    assert nomes == ["alerta-tech"]
    assert conteudo.estilos()[0]["resumo"] == "Notícia de IA com virada."


def test_modelo_criado_quando_falta(pasta):
    assert not (pasta / "_modelo.md").exists()
    texto = conteudo.modelo()
    assert "## Roteiro" in texto and (pasta / "_modelo.md").is_file()
    (pasta / "_modelo.md").write_text("# meu modelo\n", encoding="utf-8")
    assert conteudo.modelo() == "# meu modelo\n"   # o da pasta manda


def test_criar_estilo_indexa_no_readme(pasta):
    conteudo.salvar_estilo("humor-games", "# Estilo: `humor-games`\n\nGames com piada.\n", novo=True,
                           indice={"para_que": "Notícia de games", "tom": "Zoeira | leve", "duracao": "30–45s"})
    assert (pasta / "humor-games.md").read_text(encoding="utf-8").startswith("# Estilo: `humor-games`")
    readme = (pasta / "README.md").read_text(encoding="utf-8")
    linhas = readme.split("\n")
    i = linhas.index("| [`alerta-tech`](alerta-tech.md) | Notícia de IA | Urgente | 50–75s |")
    assert linhas[i + 1] == "| [`humor-games`](humor-games.md) | Notícia de games | Zoeira / leve | 30–45s |"
    assert "## Vídeos já feitos" in readme   # o resto do README intacto


def test_criar_sem_readme_nao_quebra(pasta):
    (pasta / "README.md").unlink()
    conteudo.salvar_estilo("calmo", "# Estilo: `calmo`\n\nExplicação calma.\n", novo=True)
    assert (pasta / "calmo.md").is_file()


def test_nome_invalido_e_traversal(pasta):
    for nome in ("../fora", "Com Espaco", "readme", "_modelo", "a", "x/y"):
        with pytest.raises(ToolError):
            conteudo.salvar_estilo(nome, "# x", novo=True)
    assert not (pasta.parent / "fora.md").exists()


def test_novo_nao_sobrescreve_e_edicao_exige_existir(pasta):
    with pytest.raises(ToolError, match="Já existe"):
        conteudo.salvar_estilo("alerta-tech", "# outro", novo=True)
    with pytest.raises(ToolError, match="não encontrado"):
        conteudo.salvar_estilo("inexistente", "# x")
    conteudo.salvar_estilo("alerta-tech", "# Estilo editado\r\n\r\nNovo resumo.")
    assert conteudo.ler_estilo("alerta-tech")["texto"] == "# Estilo editado\n\nNovo resumo.\n"


def test_apagar_estilo(pasta):
    conteudo.apagar_estilo("alerta-tech")
    assert conteudo.estilos() == []
    with pytest.raises(ToolError):
        conteudo.apagar_estilo("alerta-tech")


def test_limpar_markdown():
    assert conteudo.limpar_markdown("```markdown\n# A\n\nB\n```") == "# A\n\nB\n"
    assert conteudo.limpar_markdown("# A") == "# A\n"


# ------------------------------------------------------------------ especificações

def test_criar_e_editar_especificacao(pasta):
    spec = conteudo.salvar_especificacao({"nome": "Notícias de IA", "tema": "lançamentos e riscos de IA",
                                          "palavras_chave": "OpenAI, Meta,\nAnthropic", "estilo": "alerta-tech"})
    assert spec["nome"] == "Notícias de IA" and spec["palavras_chave"] == ["OpenAI", "Meta", "Anthropic"]
    assert spec["roteiros"] == 3 and spec["automacao"]["modo"] == "desligada"
    with db.session() as s:
        assert s.get(db.Conversation, spec["id"]).kind == "conteudo"

    editada = conteudo.salvar_especificacao({"roteiros": 5, "automacao": {"modo": "aprovacao"}}, spec["id"])
    assert editada["roteiros"] == 5 and editada["tema"] == "lançamentos e riscos de IA"   # o resto fica
    auto = editada["automacao"]
    assert {k: auto[k] for k in ("modo", "hora_roteiros", "hora_producao", "horarios", "dias")} ==         {"modo": "aprovacao", "hora_roteiros": "19:00", "hora_producao": "03:00", "horarios": ["03:00"], "dias": list(range(7))}
    assert auto["ativado_em"]
    assert [e["id"] for e in conteudo.especificacoes()] == [spec["id"]]


def test_especificacao_valida(pasta):
    with pytest.raises(ToolError, match="nome"):
        conteudo.salvar_especificacao({"tema": "x"})
    with pytest.raises(ToolError, match="tema"):
        conteudo.salvar_especificacao({"nome": "x"})
    with pytest.raises(ToolError, match="Horário"):
        conteudo.salvar_especificacao({"nome": "x", "tema": "y", "automacao": {"hora_producao": "25:00"}})
    with pytest.raises(ToolError, match="automação"):
        conteudo.salvar_especificacao({"nome": "x", "tema": "y", "automacao": {"modo": "sempre"}})
    spec = conteudo.salvar_especificacao({"nome": "x", "tema": "y", "roteiros": 99, "dias": 0})
    assert spec["roteiros"] == 10 and spec["dias"] == 1


def test_especificacao_de_outro_tipo_nao_abre():
    with db.session() as s:
        c = db.Conversation(kind="chat")
        s.add(c)
        s.commit()
        cid = c.id
    with pytest.raises(ToolError, match="não encontrada"):
        conteudo.especificacao(cid)


# ------------------------------------------------------------------ API

def test_api_fluxo(pasta, cliente, monkeypatch):
    assert cliente.get("/api/conteudo/estilos").json()[0]["nome"] == "alerta-tech"
    r = cliente.put("/api/conteudo/estilos/novo-estilo", json={"texto": "# Estilo: `novo-estilo`\n\nX.\n", "novo": True})
    assert r.status_code == 200
    assert cliente.put("/api/conteudo/estilos/novo-estilo", json={"texto": "# y", "novo": True}).status_code == 400
    assert cliente.put("/api/conteudo/pastas", json={"pasta_estilos": "C:/nao/existe/mesmo"}).status_code == 400
    r = cliente.post("/api/conteudo/especificacoes", json={"nome": "Games", "tema": "lançamentos de jogos"})
    cid = r.json()["id"]
    assert cliente.get(f"/api/conteudo/especificacoes/{cid}").json()["nome"] == "Games"
    assert cliente.post("/api/conversations", json={"kind": "conteudo"}).status_code == 200

    async def falso(provider, model, messages, tools, num_ctx, effort=None, **kw):
        assert "MODELO:" in messages[1]["content"] and "## Roteiro" in messages[1]["content"]
        yield "content", "<think>pensando</think>```markdown\n# Estilo: `gerado`\n\nGerado.\n```"
        yield "done", {"completion_tokens": 10}

    monkeypatch.setattr(llm, "chat_stream", falso)
    r = cliente.post("/api/conteudo/estilos-gerar", json={"descricao": "games com humor", "provider": "p", "model": "m"})
    assert r.status_code == 200, r.text
    assert r.json()["texto"] == "# Estilo: `gerado`\n\nGerado.\n"
    assert not (pasta / "gerado.md").exists()   # gerar só devolve; quem grava é o salvar

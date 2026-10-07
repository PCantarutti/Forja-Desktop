import asyncio
import json

import pytest

from app import config, conteudo, conteudo_roteiros as R, db, llm, mcp_servidor, mirror, web
from app.tools import ToolError

ROTEIRO = {
    "titulo": "OpenAI pausa", "ideia": "Ângulo do alerta.",
    "noticia": {"resumo": "A OpenAI pausou o treino.", "data": "2026-09-26", "fontes": [1, 99, "https://x.com/a"]},
    "cenas": [{"id": "Hook!", "texto": "A OpenAI parou tudo."}, {"id": "hook", "texto": "De novo."},
              {"id": "cta", "texto": "Comenta aí."}, {"id": "vazia", "texto": "  "}],
    "titulo_youtube": "A OpenAI parou tudo", "descricao": "Fontes: ...", "confianca": "9",
    "motivo_confianca": "Fonte primária.",
}


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(config, "SUBAGENTS", {})
    estilos = tmp_path / "estilos"
    estilos.mkdir()
    (estilos / "alerta-tech.md").write_text("# Estilo: `alerta-tech`\n\nUrgente.\n\n## Roteiro\n7 cenas.\n", encoding="utf-8")
    with db.session() as s:
        s.query(db.AppSetting).filter(db.AppSetting.key == conteudo.CHAVE).delete()
        s.query(db.Conversation).filter(db.Conversation.kind == conteudo.KIND).delete()
        s.commit()
    conteudo.salvar_pastas({"pasta_estilos": str(estilos)})
    R._RUNS.clear()
    yield
    R._RUNS.clear()


def _spec(**extra) -> int:
    return conteudo.salvar_especificacao({"nome": "IA", "tema": "riscos de IA", "estilo": "alerta-tech", "roteiros": 2,
                                          "motor": {"provider": "p", "model": "m"}, **extra})["id"]


def _fakes(monkeypatch, roteiros_resposta=None, juiz=""):
    chamados = []
    respostas = iter(roteiros_resposta or [json.dumps({"roteiros": [ROTEIRO, {**ROTEIRO, "titulo": "Segundo"}]})])

    async def falso(provider, model, messages, tools, num_ctx, effort=None, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        if system.startswith("Você é um pesquisador. Recebe"):
            out = json.dumps({"perguntas": ["o que houve?"], "buscas": ["openai pausa treino"]})
        elif system.startswith("Você é um pesquisador. As fontes"):
            out = '["outra busca"]'
        elif system.startswith("Você lê UMA página"):
            out = "RELEVANTE: sim\nRESUMO: A OpenAI pausou o treinamento dos modelos mais avançados pela segunda vez em setembro.\nTRECHO: pausou"
        elif system.startswith("Você é roteirista"):
            chamados.append((system, user))
            out = next(respostas)
        elif system.startswith("Você é editor-chefe"):
            chamados.append((system, user))
            out = juiz
        else:
            out = ""
        yield "content", out
        yield "done", {"completion_tokens": 5, "prompt_tokens": 5}

    monkeypatch.setattr(llm, "chat_stream", falso)
    monkeypatch.setattr(web, "buscar", lambda q, n=10: [{"title": f"Notícia {q}", "url": f"https://site{len(q)}.com/{q.replace(' ', '-')}", "content": ""}])
    monkeypatch.setattr(web, "ler", lambda url, n=0: {"url": url, "title": "Matéria", "text": "texto da matéria " * 20})
    return chamados


async def _rodar_ate_o_fim(conv_id: int) -> dict:
    est = R.iniciar(conv_id)
    for _ in range(200):
        if est["id"] not in R._RUNS:
            break
        await asyncio.sleep(0.02)
    return R.estado(est["id"])


def test_rodada_completa_gera_roteiros(monkeypatch):
    chamados = _fakes(monkeypatch)
    cid = _spec()
    est = asyncio.run(_rodar_ate_o_fim(cid))
    assert est["status"] == "ok", est.get("aviso")
    assert len(est["roteiros"]) == 2
    r = est["roteiros"][0]
    assert [c["id"] for c in r["cenas"]] == ["hook", "hook-2", "cta"]       # id limpo, repetido desduplicado, vazia fora
    assert r["confianca"] == 5 and r["status"] == "novo" and r["palavras"] == 8
    assert r["noticia"]["fontes"][0]["url"].startswith("https://site")   # [1] vira a 1ª fonte útil
    assert r["noticia"]["fontes"][1]["url"] == "https://x.com/a"          # 99 não existe: fica fora
    system, user = chamados[0]
    assert "exatamente 2 roteiros" in system and "GUIA DE ESTILO (alerta-tech)" in user and "7 cenas." in user
    assert est["stats"]["uteis"] >= 1 and est["fase"] == "pronto"


def test_json_invalido_tenta_de_novo(monkeypatch):
    chamados = _fakes(monkeypatch, ["não sei fazer JSON", json.dumps([ROTEIRO])])
    est = asyncio.run(_rodar_ate_o_fim(_spec()))
    assert est["status"] == "ok" and len(est["roteiros"]) == 1
    assert "ATENÇÃO" in chamados[1][0]


def test_sem_roteiro_legivel_vira_erro(monkeypatch):
    _fakes(monkeypatch, ["nada", "nada ainda"])
    est = asyncio.run(_rodar_ate_o_fim(_spec()))
    assert est["status"] == "erro" and "formato" in est["aviso"]


def test_iniciar_valida(monkeypatch):
    with pytest.raises(ToolError, match="estilo"):
        R.iniciar(conteudo.salvar_especificacao({"nome": "x", "tema": "y"})["id"])
    with pytest.raises(ToolError, match="modelo"):
        R.iniciar(_spec(motor={"provider": "", "model": ""}))


def test_aprovar_um_por_especificacao():
    cid = _spec()
    roteiros = R.normalizar([ROTEIRO, ROTEIRO], [])
    from app.agent import _save
    ids = [_save(cid, role="assistant", name=R.NOME, status="ok", meta={R.CHAVE: {"roteiros": [r]}}).id for r in roteiros]
    R.marcar(ids[0], roteiros[0]["id"], "aprovado")
    R.marcar(ids[1], roteiros[1]["id"], "aprovado")
    assert R.estado(ids[0])["roteiros"][0]["status"] == "novo"     # o anterior perdeu a aprovação
    assert R.aprovado(cid) == (ids[1], {**roteiros[1], "status": "aprovado"})
    with pytest.raises(ToolError):
        R.marcar(ids[0], "nao-existe", "aprovado")
    with pytest.raises(ToolError):
        R.marcar(ids[0], roteiros[0]["id"], "produzido")             # só a produção marca produzido


def test_editar_recalcula():
    cid = _spec()
    r = R.normalizar([ROTEIRO], [])[0]
    from app.agent import _save
    mid = _save(cid, role="assistant", name=R.NOME, status="ok", meta={R.CHAVE: {"roteiros": [r]}}).id
    est = R.editar(mid, r["id"], {"cenas": [{"id": "hook", "texto": "um dois três quatro"}], "titulo_youtube": "Novo"})
    x = est["roteiros"][0]
    assert x["palavras"] == 4 and x["titulo_youtube"] == "Novo" and x["noticia"]["fontes"] == r["noticia"]["fontes"]
    with pytest.raises(ToolError, match="cena"):
        R.editar(mid, r["id"], {"cenas": [{"id": "x", "texto": ""}]})


def test_claude_mcp_fila_e_salvar(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    cid = _spec(motor={"provider": "claude-mcp", "model": "Claude (MCP)"})
    est = R.iniciar(cid)
    assert est["status"] == "aguardando" and est["id"] not in R._RUNS
    texto = asyncio.run(R.mcp_pedidos(0))
    assert f"PEDIDO {est['id']}" in texto and "GUIA DE ESTILO (alerta-tech)" in texto and "conteudo_salvar_roteiros" in texto
    assert "ERRO" in R.mcp_salvar(est["id"], [{"titulo": "sem cenas"}])
    ok = R.mcp_salvar(est["id"], [ROTEIRO], [{"titulo": "Fortune", "url": "https://fortune.com/x"}], "claude-opus")
    assert "1 roteiro" in ok
    feito = R.estado(est["id"])
    assert feito["status"] == "ok" and feito["roteiros"][0]["noticia"]["fontes"][0]["url"] == "https://fortune.com/x"
    assert "ERRO" in R.mcp_salvar(est["id"], [ROTEIRO])                  # não grava duas vezes
    assert asyncio.run(R.mcp_pedidos(0)).startswith("Nenhum pedido")


def test_claude_exige_interruptor(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", False)
    with pytest.raises(ToolError, match="MCP"):
        R.iniciar(_spec(motor={"provider": "claude-mcp", "model": "Claude (MCP)"}))


def test_cancelar_pedido_do_claude(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    est = R.iniciar(_spec(motor={"provider": "claude-mcp", "model": "Claude (MCP)"}))
    R.cancelar(est["id"])
    assert R.estado(est["id"])["status"] == "cancelado" and R.pedidos() == []


def test_reap():
    cid = _spec()
    from app.agent import _save
    mid = _save(cid, role="assistant", name=R.NOME, status="running", meta={R.CHAVE: {"fase": "lendo"}}).id
    assert R.reap() >= 1
    assert R.estado(mid)["status"] == "erro" and "Interrompido" in R.estado(mid)["aviso"]


def test_ferramentas_mcp_registradas():
    nomes = {t.name for t in asyncio.run(mcp_servidor.SERVIDOR.list_tools())}
    assert {"conteudo_pedidos", "conteudo_salvar_roteiros"} <= nomes



def test_garante_modelo_cai_para_carga_leve_sem_vram(monkeypatch):
    import asyncio
    from app import conteudo_roteiros as R, modelctl
    from app.tools import ToolError
    cargas = []

    async def ensure(spec, out=None, cancel=None, temporario=None):
        cargas.append(temporario)
        if len(cargas) < 3:
            raise ToolError("llama-server saiu com código 1. ErrorOutOfDeviceMemory")
        yield {"type": "model", "phase": "ready"}

    monkeypatch.setattr(modelctl, "gerenciavel", lambda spec: True)
    monkeypatch.setattr(modelctl, "ensure", ensure)
    monkeypatch.setattr(R, "ESPERA_VRAM", 0)
    asyncio.run(R.garante_modelo({"provider": "local", "model": "q"}))
    assert cargas == [None, R.CARGA_LEVE, R.CARGA_LEVE]

    cargas.clear()
    async def outro(spec, out=None, cancel=None, temporario=None):
        cargas.append(temporario)
        raise ToolError("Modelo não encontrado")
        yield
    monkeypatch.setattr(modelctl, "ensure", outro)
    try:
        asyncio.run(R.garante_modelo({"provider": "local", "model": "q"}))
    except ToolError:
        pass
    assert cargas == [None]   # erro que não é de VRAM não insiste


def test_video_unico_fora_da_agenda_e_prompt_do_tema(monkeypatch):
    chamados = _fakes(monkeypatch)
    cid = _spec(tipo="unico", automacao={"modo": "automatico"})
    spec = conteudo.especificacao(cid)
    assert spec["tipo"] == "unico" and spec["automacao"]["modo"] == "desligada"   # único nunca entra na agenda
    from app import main
    assert all(i["id"] != cid for i in main.conteudo_agenda_geral())
    est = asyncio.run(_rodar_ate_o_fim(cid))
    assert est["status"] == "ok" and est["pergunta"].startswith("Pesquisa a fundo para um vídeo sobre")
    system = chamados[0][0]
    assert "vídeo ÚNICO" in system and "últimos" not in system
    with pytest.raises(ToolError):
        conteudo.salvar_especificacao({"tipo": "mensal"}, cid)


def test_profundidade_da_pesquisa_vem_da_especificacao(monkeypatch):
    _fakes(monkeypatch)
    cid = _spec(profundidade="personalizado", pesquisa_rodadas=5, pesquisa_minutos=40)
    p, rodadas, teto = R.porte(conteudo.especificacao(cid))
    assert p is __import__("app.pesquisa", fromlist=["x"]).PRESETS["personalizado"] and rodadas == 5 and teto == 2400
    p, rodadas, teto = R.porte(conteudo.especificacao(_spec(profundidade="funda")))
    assert rodadas == 4 and p["fontes"] == 8
    assert R.porte(conteudo.especificacao(_spec()))[1:] == (2, 900)   # padrão = o de antes (normal, 15 min)
    est = asyncio.run(_rodar_ate_o_fim(cid))
    assert est["rodadas_total"] == 5 and est["teto_segundos"] == 2400


def test_video_longo_um_roteiro_por_chamada(monkeypatch):
    um = json.dumps({"roteiros": [ROTEIRO]})
    chamados = _fakes(monkeypatch, [um, json.dumps({"roteiros": [{**ROTEIRO, "titulo": "Outro"}]})])
    est = asyncio.run(_rodar_ate_o_fim(_spec(duracao_min=600)))
    assert est["status"] == "ok" and [r["titulo"] for r in est["roteiros"]] == ["OpenAI pausa", "Outro"]
    chamados = [c for c in chamados if c[0].startswith("Você é roteirista")]
    assert len(chamados) == 2 and "exatamente 1 roteiros" in chamados[0][0]
    assert "JÁ ESCRITOS" in chamados[1][1] and "OpenAI pausa" in chamados[1][1]


# ------------------------------------------------------------------ juiz, retenção e títulos

def test_juiz_da_nota_e_titulos_saem_normalizados(monkeypatch):
    juiz = json.dumps({"notas": [
        {"n": 1, "gancho": 2, "retencao": 2, "clareza": 4, "fatos": 5, "interesse": 2, "comentario": "gancho fraco"},
        {"n": 2, "gancho": 5, "retencao": 4, "clareza": 4, "fatos": 1, "interesse": 5, "comentario": "inventa número"},
        {"n": 9, "gancho": 5}]})
    roteiros = json.dumps({"roteiros": [{**ROTEIRO, "titulo_youtube": "A", "titulos": ["A", "B", "B", "C", "D"]},
                                        {**ROTEIRO, "titulo": "Segundo"}]})
    chamados = _fakes(monkeypatch, [roteiros], juiz)
    est = asyncio.run(_rodar_ate_o_fim(_spec()))
    a, b = est["roteiros"]
    assert a["titulos"] == ["A", "B", "C"]                                   # sem repetir, o título_youtube primeiro, até 3
    assert a["nota"] == {"gancho": 2, "retencao": 2, "clareza": 4, "fatos": 5, "interesse": 2, "total": 2.9,
                         "comentario": "gancho fraco"}
    assert b["nota"]["fatos"] == 1
    system, user = chamados[0]
    assert "Retenção" in system and "`titulos`" in system                  # regras de retenção no roteirista
    juiz_sys, juiz_user = chamados[-1]
    assert juiz_sys.startswith("Você é editor-chefe") and "ACHADOS" in juiz_user and "ROTEIRO 2" in juiz_user
    # fatos fracos perdem mesmo com nota total maior; sem juiz vale a confiança
    assert max([a, b], key=R.pontos) is a
    assert R.pontos({"confianca": 4}) == (True, 4, 4)


def test_juiz_que_falha_nao_derruba_a_rodada(monkeypatch):
    _fakes(monkeypatch, juiz="não sei avaliar")
    est = asyncio.run(_rodar_ate_o_fim(_spec()))
    assert est["status"] == "ok" and all("nota" not in x for x in est["roteiros"])

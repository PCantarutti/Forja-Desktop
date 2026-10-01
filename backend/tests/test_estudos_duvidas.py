import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, estudos, estudos_duvidas as D, estudos_prova as P, mirror, pesquisa
from app.tools import ToolError


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(estudos, "RAIZ", tmp_path / "estudos")
    monkeypatch.setattr(config, "SUBAGENTS", {})
    estudos._RUNS.clear()
    yield
    estudos._RUNS.clear()


RESUMO = "# Biologia\n\nVisão.\n\n## 1. Glicólise\n\nOcorre no citosol, saldo de 2 ATP [p. 2].\n\n## Fontes\n\n- apostila"
VISTO: list[list[dict]] = []   # as mensagens que cada chamada ao modelo falso recebeu


def _fake(monkeypatch, resposta="A glicólise ocorre **no citosol** [p. 2].", pausa=0.0):
    VISTO.clear()

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        VISTO.append(messages)
        for pedaco in (resposta[:10], resposta[10:]):   # chega em pedaços, como o stream de verdade
            if pausa:
                await asyncio.sleep(pausa)
            yield ("content", pedaco)
        yield ("done", {"prompt_tokens": 50, "completion_tokens": 12})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)


def _estudo() -> int:
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="Biologia")
        s.add(c)
        s.commit()
        conv = c.id
    topicos = [{"titulo": "Glicólise", "objetivo": "", "pontos": [], "status": "pronto"}]
    estudos._save(conv, role="assistant", content=RESUMO, status="pronto",
                  meta={"estudos": {"tipo": "resumo", "tema": "Biologia", "topicos": topicos, "preferencias": {"nivel": "iniciante"}}})
    return conv


def _entrega(conv: int) -> tuple[int, dict]:
    """Uma prova de 1 questão (gravada como o Claude grava, sem modelo) e uma entrega errada dela."""
    q = {"tipo": "me", "enunciado": "Onde ocorre a glicólise na célula eucarionte?", "topico": "Glicólise",
         "alternativas": ["No citosol", "Na mitocôndria", "No núcleo", "No ribossomo"], "correta": 0,
         "explicacao": "A glicólise é citosólica.", "por_alternativa": ["certa", "é o Krebs", "não", "não"]}
    P.mcp_salvar_prova([q], conv_id=conv)
    prova = P.lista(conv)[0]
    cheia = estudos.estado(P.entregar(prova["message_id"], {})["id"])   # só para pegar as questões reveladas
    certa = cheia["questoes"][0]["correta"]
    errada = (certa + 1) % 4
    t = P.entregar(prova["message_id"], {"q1": errada})
    return t["id"], {"certa": certa, "errada": errada, "alternativas": cheia["questoes"][0]["alternativas"]}


def _perguntar(conv, pergunta, **kw):
    async def main():
        msg = D.perguntar(conv, pergunta, provider="fake", model="m", **kw)
        while estudos._TAREFAS:
            await asyncio.gather(*list(estudos._TAREFAS))
        return estudos.estado(msg["id"])
    return asyncio.run(main())


def test_duvida_geral_responde_com_o_resumo_no_contexto(monkeypatch):
    _fake(monkeypatch)
    conv = _estudo()
    e = _perguntar(conv, "Onde ocorre a glicólise?")
    assert e["status"] == "pronto" and e["texto"] == "A glicólise ocorre **no citosol** [p. 2]."
    sistema = VISTO[0][0]["content"]
    assert "professor particular" in sistema and "Glicólise" in sistema and "iniciante" in sistema.lower()
    assert [m["role"] for m in D.conversa(conv, "geral")] == ["user", "assistant"] and D.fios(conv) == {"geral": 1}


def test_segunda_pergunta_leva_a_conversa_anterior(monkeypatch):
    _fake(monkeypatch)
    conv = _estudo()
    _perguntar(conv, "Onde ocorre a glicólise?")
    _perguntar(conv, "E quanto ATP ela dá?")
    msgs = VISTO[1]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "Onde ocorre a glicólise?" and msgs[3]["content"] == "E quanto ATP ela dá?"


def test_duvida_de_questao_ve_gabarito_resposta_do_aluno_e_explicacao(monkeypatch):
    _fake(monkeypatch)
    conv = _estudo()
    tid, info = _entrega(conv)
    e = _perguntar(conv, "Por que a minha está errada?", questao={"tentativa_id": tid, "questao_id": "q1"})
    assert e["fio"] == f"questao:{tid}:q1" and e["status"] == "pronto"
    sistema = VISTO[0][0]["content"]
    assert f"Gabarito: {'ABCD'[info['certa']]}) No citosol" in sistema
    assert f"Resposta do aluno: {'ABCD'[info['errada']]}) {info['alternativas'][info['errada']]}" in sistema
    assert "errada (0 de 1)" in sistema and "A glicólise é citosólica." in sistema and "é o Krebs" in sistema
    assert "contestar o gabarito" in sistema   # o professor pode dar razão ao aluno
    assert D.fios(conv) == {f"questao:{tid}:q1": 1}


def test_questao_de_outro_estudo_ou_inexistente_e_recusada(monkeypatch):
    _fake(monkeypatch)
    conv, outro = _estudo(), _estudo()
    tid, _ = _entrega(conv)
    with pytest.raises(ToolError, match="neste estudo"):
        D.perguntar(outro, "?", questao={"tentativa_id": tid, "questao_id": "q1"}, provider="fake", model="m")
    with pytest.raises(ToolError, match="Questão não encontrada"):
        D.perguntar(conv, "?", questao={"tentativa_id": tid, "questao_id": "q9"}, provider="fake", model="m")
    with pytest.raises(ToolError, match="Escreva"):
        D.perguntar(conv, "  ", provider="fake", model="m")


def test_trecho_do_resumo_vai_para_o_professor(monkeypatch):
    _fake(monkeypatch)
    conv = _estudo()
    _perguntar(conv, "Explique de outro jeito, mais simples.", trecho="saldo de 2 ATP")
    assert "«saldo de 2 ATP»" in VISTO[0][0]["content"] and "«saldo de 2 ATP»" in VISTO[0][-1]["content"]
    assert D.conversa(conv, "geral")[0]["trecho"] == "saldo de 2 ATP"


def test_resposta_com_think_mostra_so_a_resposta(monkeypatch):
    _fake(monkeypatch, resposta="<think>vou pensar</think>No citosol.")
    e = _perguntar(_estudo(), "Onde?")
    assert e["texto"] == "No citosol."


def test_cancelar_guarda_o_que_ja_veio(monkeypatch):
    _fake(monkeypatch, resposta="Primeira parte, segunda parte.", pausa=0.05)
    conv = _estudo()

    async def main():
        msg = D.perguntar(conv, "Explique", provider="fake", model="m")
        while not estudos._RUNS[msg["id"]]["texto"]:
            await asyncio.sleep(0.01)
        estudos.cancelar(msg["id"])
        while estudos._TAREFAS:
            await asyncio.gather(*list(estudos._TAREFAS))
        return estudos.estado(msg["id"])

    e = asyncio.run(main())
    assert e["status"] == "cancelado" and e["texto"] == "Primeira p"


def test_duvida_nao_trava_a_prova_nem_acende_a_bolinha(monkeypatch):
    from app.main import get_activity
    _fake(monkeypatch, pausa=0.05)
    conv = _estudo()

    async def main():
        msg = D.perguntar(conv, "Explique", provider="fake", model="m")
        assert estudos.rodando(conv) is None   # o gerar da prova não fica bloqueado
        with pytest.raises(ToolError, match="respondendo"):
            D.perguntar(conv, "outra", provider="fake", model="m")   # mas a mesma conversa espera a vez
        a = await get_activity()
        while estudos._TAREFAS:
            await asyncio.gather(*list(estudos._TAREFAS))
        return msg, a

    _, a = asyncio.run(main())
    assert not any(c["id"] == conv and c["running"] for c in a["conversations"])


def test_claude_responde_a_duvida_pedida_na_tela(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    _fake(monkeypatch)
    conv = _estudo()
    tid, _ = _entrega(conv)
    msg = D.perguntar(conv, "Não entendi a questão", questao={"tentativa_id": tid, "questao_id": "q1"},
                      provider=estudos.MOTOR_CLAUDE)
    assert estudos.estado(msg["id"])["status"] == "aguardando" and not estudos._RUNS
    texto = asyncio.run(estudos.mcp_pedidos(0))
    assert f"PEDIDO {msg['id']} — dúvida do aluno" in texto and "Não entendi a questão" in texto
    assert "Gabarito:" in texto and f"estudos_responder_duvida(duvida_id={msg['id']}" in texto
    assert "Resposta gravada" in D.mcp_responder(msg["id"], "Porque a glicólise é **citosólica**.", "Claude Opus 5.5")
    e = estudos.estado(msg["id"])
    assert e["status"] == "pronto" and e["texto"].startswith("Porque") and e["stats"]["escritor"] == "Claude Opus 5.5"
    assert "já respondida" in D.mcp_responder(msg["id"], "de novo")


def test_api_da_duvida(monkeypatch):
    from app.main import app
    _fake(monkeypatch)
    conv = _estudo()
    with TestClient(app) as c:
        with c.stream("POST", f"/api/estudos/{conv}/duvida", json={"pergunta": "Onde?", "provider": "fake", "model": "m"}) as r:
            eventos = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
        assert eventos[-1]["status"] == "pronto" and "citosol" in eventos[-1]["texto"]
        fio = c.get(f"/api/estudos/{conv}/duvidas", params={"fio": "geral"}).json()
        assert [m["role"] for m in fio] == ["user", "assistant"] and fio[1]["modelo"] == "m"
        assert c.get(f"/api/estudos/{conv}").json()["duvidas"] == {"geral": 1}
        assert c.post(f"/api/estudos/{conv}/duvida", json={"pergunta": "x", "fio": "inventado", "provider": "fake",
                                                           "model": "m"}).status_code == 400


def test_ferramenta_mcp_da_duvida_existe():
    from app import mcp_servidor
    assert "estudos_responder_duvida" in {t.name for t in mcp_servidor.SERVIDOR._tool_manager.list_tools()}

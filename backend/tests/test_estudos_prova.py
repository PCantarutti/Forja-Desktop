import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import config, db, estudos, estudos_prova as P, mirror, pesquisa
from app.tools import ToolError


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(estudos, "RAIZ", tmp_path / "estudos")
    monkeypatch.setattr(config, "SUBAGENTS", {})
    estudos._RUNS.clear()
    yield
    estudos._RUNS.clear()


RESUMO = ("# Biologia\n\nVisão.\n\n## 1. Glicólise\n\nNo citosol, saldo de 2 ATP [p. 2].\n\n"
          "## 2. Fermentação\n\nRegenera NAD+ [p. 5].\n\n## Fontes\n\n- apostila")


def _estudo(resumo=True) -> int:
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="Biologia")
        s.add(c)
        s.commit()
        conv = c.id
    if resumo:
        topicos = [{"titulo": t, "objetivo": "", "pontos": [], "status": "pronto"} for t in ("Glicólise", "Fermentação")]
        estudos._save(conv, role="assistant", content=RESUMO, status="pronto",
                      meta={"estudos": {"tipo": "resumo", "tema": "Biologia", "topicos": topicos,
                                        "perfil": {"alternativas": 4, "banca": "ENEM"}}})
    return conv


def _me(i, correta=1, alts=4):
    return {"tipo": "me", "enunciado": f"Questão {i}: onde ocorre a glicólise, parte {i}?",
            "alternativas": [f"lugar {i}-{k}" for k in range(alts)], "correta": correta,
            "explicacao": "Ocorre no citosol.", "por_alternativa": [f"porque {k}" for k in range(alts)], "pagina": "p. 2"}


def _vf(i, correta=True):
    return {"tipo": "vf", "enunciado": f"Afirmação {i}: a glicólise dá 2 ATP.", "correta": correta, "explicacao": "Saldo 2."}


def _disc(i):
    return {"tipo": "disc", "enunciado": f"Explique {i} por que a fermentação regenera NAD+.",
            "resposta_modelo": "Para a glicólise continuar.", "rubrica": [{"criterio": "cita NAD+", "pontos": 1},
                                                                         {"criterio": "cita glicólise", "pontos": 1}]}


def _fake(monkeypatch, gerar=None, verificar=None, corrigir=None, pausa=0.0):
    """gerar(pedidas: list[str]) -> lista de questões; verificar(questoes do prompt) -> {id: resposta}."""
    chamados: list[str] = []
    ultimas: dict[str, list] = {}

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        if pausa:
            await asyncio.sleep(pausa)
        if system.startswith("Você é um professor que elabora"):
            chamados.append("gerar")
            pedidas = re.findall(r"^\d+\. (me|vf|disc) ", user, re.M)
            qs = gerar(pedidas) if gerar else [{"me": _me, "vf": _vf, "disc": _disc}[t](len(chamados) * 10 + i)
                                                for i, t in enumerate(pedidas)]
            ultimas["gerar"] = qs
            texto = json.dumps({"questoes": qs}, ensure_ascii=False)
        elif system.startswith("Você resolve questões"):
            chamados.append("verificar")
            ids = re.findall(r"^\[(q\d+)\]", user, re.M)
            texto = json.dumps({"respostas": [{"id": i, "resposta": r} for i, r in (verificar(user, ids) if verificar else {}).items()]})
        elif system.startswith("Você corrige uma questão discursiva"):
            chamados.append("corrigir")
            texto = json.dumps(corrigir(user) if corrigir else {"criterios": [{"pontos": 1}, {"pontos": 0.5}], "feedback": "Faltou glicólise."})
        else:
            chamados.append("?")
            texto = ""
        yield ("content", texto)
        yield ("done", {"prompt_tokens": 10, "completion_tokens": 20})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)
    return chamados


def _correto_do_prompt(user: str, ids: list[str]) -> dict:
    """Verificador que acerta: acha, no prompt, a alternativa cujo texto é o "lugar X-1" (a correta do _me)."""
    out = {}
    for qid, bloco in zip(ids, re.split(r"^\[q\d+\]", user, flags=re.M)[1:]):
        m = re.search(r"^([A-E])\) lugar \d+-1$", bloco, re.M)
        out[qid] = m.group(1) if m else "V"
    return out


async def _acabar():
    while estudos._TAREFAS:
        await asyncio.gather(*list(estudos._TAREFAS))


def _gerar(conv, config_, **kw):
    async def main():
        msg = P.start(conv, config_, "fake", "m", **kw)
        await _acabar()
        return msg["id"]
    return asyncio.run(main())


def _cheia(pid):
    with db.session() as s:
        return s.get(db.Message, pid).meta["estudos"]


# ------------------------------------------------------------------ validação


def test_validar_cada_tipo_e_os_erros():
    q, _ = P.validar(_me(1, correta="B"))
    assert q["correta"] == 1 and len(q["por_alternativa"]) == 4
    assert P.validar({**_me(1), "correta": 9})[1] == "sem a alternativa correta"
    assert P.validar({**_me(1), "alternativas": ["a", "a", "b", "c"]})[1] == "alternativas repetidas"
    assert P.validar({**_me(1), "alternativas": ["A) um", "B) dois", "C) três"]})[0]["alternativas"] == ["um", "dois", "três"]
    assert P.validar(_vf(1, "Falso"))[0]["correta"] is False
    assert P.validar({**_vf(1), "correta": "talvez"})[1] == "sem o gabarito verdadeiro/falso"
    d, _ = P.validar({**_disc(1), "rubrica": [{"criterio": "a", "pontos": "3"}, {"criterio": "b", "pontos": 1}]})
    assert sum(r["pontos"] for r in d["rubrica"]) == pytest.approx(P.PONTOS["disc"])   # rubrica reescalada
    assert P.validar(_me(1), "vf")[1] == "veio me no lugar de vf"


def test_embaralhar_mantem_a_correta_certa():
    import random
    q, _ = P.validar(_me(1, correta=2))
    certa = q["alternativas"][2]
    por = q["por_alternativa"][2]
    for seed in range(20):
        x = {**q, "alternativas": list(q["alternativas"]), "por_alternativa": list(q["por_alternativa"])}
        P._embaralhar(x, random.Random(seed))
        assert x["alternativas"][x["correta"]] == certa and x["por_alternativa"][x["correta"]] == por


def test_planejar_distribui_tipos_topicos_e_dificuldade():
    cfg = {"me": 3, "vf": 2, "disc": 1, "topicos": ["A", "B"], "dificuldade": "mista"}
    plano = P._planejar(cfg)
    assert [p["tipo"] for p in plano] == ["me", "me", "me", "vf", "vf", "disc"]
    assert [p["topico"] for p in plano] == ["A", "B", "A", "B", "A", "B"]
    assert [p["dificuldade"] for p in plano][:4] == ["facil", "media", "media", "dificil"]
    assert [len(l) for l in P._lotes(plano)] == [3, 2, 1]


def test_config_valida():
    ctx = P._contexto(_estudo())
    assert ctx["topicos"] == ["Glicólise", "Fermentação"]
    with pytest.raises(ToolError, match="pelo menos uma"):
        P._config({"me": 0}, ctx)
    with pytest.raises(ToolError, match="No máximo"):
        P._config({"me": 41}, ctx)
    cfg = P._config({"me": 2, "topicos": ["Fermentação", "Inventado"], "estilo": True}, ctx)
    assert cfg["topicos"] == ["Fermentação"] and cfg["alternativas"] == 4 and cfg["estilo"]


def test_sem_resumo_nem_material_nao_da_prova():
    with pytest.raises(ToolError, match="Anexe material"):
        P._contexto(_estudo(resumo=False))


# ------------------------------------------------------------------ gerar


def test_gera_confere_e_esconde_o_gabarito(monkeypatch):
    chamados = _fake(monkeypatch, verificar=_correto_do_prompt)
    conv = _estudo()
    pid = _gerar(conv, {"me": 6, "vf": 2, "disc": 1})
    e = estudos.estado(pid)
    assert e["status"] == "pronto", e["aviso"]
    assert len(e["questoes"]) == 9 and [q["id"] for q in e["questoes"]] == [f"q{i}" for i in range(1, 10)]
    assert chamados.count("gerar") == 4 and chamados.count("verificar") == 3   # lotes: 5 me, 1 me, 2 vf, 1 disc (sem conferência)
    assert not e["revelada"] and all("correta" not in q and "explicacao" not in q for q in e["questoes"])
    cheia = _cheia(pid)["questoes"]
    assert all(q["verificada"] for q in cheia if q["tipo"] != "disc")
    assert all(q["alternativas"][q["correta"]].endswith("-1") for q in cheia if q["tipo"] == "me")
    assert len({q["correta"] for q in cheia if q["tipo"] == "me"}) > 1   # embaralhou: a correta não fica sempre em B
    assert [p["titulo"] for p in P.lista(conv)] == ["Prova 1"]


def test_verificador_que_discorda_regera_e_depois_descarta(monkeypatch):
    # verificador sempre responde "A": a correta só cai em A por acaso do embaralhamento
    _fake(monkeypatch, verificar=lambda user, ids: {i: "A" for i in ids})
    pid = _gerar(_estudo(), {"me": 4})
    e, plano = estudos.estado(pid), _cheia(pid)["planejadas"]
    assert all(p["status"] in ("ok", "descartada") for p in plano)
    ok = [p for p in plano if p["status"] == "ok"]
    assert len(e["questoes"]) == len(ok) < 4
    assert all(p["motivo"] == "o verificador chegou a outra resposta" for p in plano if p["status"] == "descartada")
    assert "ficaram de fora" in e["aviso"] or not ok


def test_formato_errado_na_primeira_vez_e_refeito(monkeypatch):
    vez = {"n": 0}

    def gerar(pedidas):
        vez["n"] += 1
        return [{"tipo": "me", "enunciado": "curto"}] * len(pedidas) if vez["n"] == 1 else [_me(i) for i in range(len(pedidas))]

    chamados = _fake(monkeypatch, gerar=gerar)
    pid = _gerar(_estudo(), {"me": 2})
    e = estudos.estado(pid)
    assert e["status"] == "pronto" and len(e["questoes"]) == 2 and chamados.count("gerar") == 2


def test_nenhuma_questao_valida_e_erro(monkeypatch):
    _fake(monkeypatch, gerar=lambda pedidas: [])
    e = estudos.estado(_gerar(_estudo(), {"vf": 2}))
    assert e["status"] == "erro" and not e["questoes"]


def test_repetida_nao_entra_duas_vezes(monkeypatch):
    _fake(monkeypatch, gerar=lambda pedidas: [_vf(1)] * len(pedidas))
    e = estudos.estado(_gerar(_estudo(), {"vf": 3}))
    assert len(e["questoes"]) == 1


# ------------------------------------------------------------------ entregar


def _prova_pronta(monkeypatch, config_=None) -> tuple[int, list[dict]]:
    _fake(monkeypatch, verificar=_correto_do_prompt)
    pid = _gerar(_estudo(), config_ or {"me": 2, "vf": 1, "disc": 1})
    return pid, _cheia(pid)["questoes"]


def test_entrega_corrige_fechadas_e_discursiva(monkeypatch):
    pid, qs = _prova_pronta(monkeypatch)
    me1, me2, vf, disc = qs
    respostas = {me1["id"]: me1["correta"], me2["id"]: (me2["correta"] + 1) % 4, vf["id"]: "V", disc["id"]: "Regenera NAD+."}

    async def main():
        msg = P.entregar(pid, respostas, 125, "fake", "m")
        assert estudos.estado(msg["id"])["status"] == "rodando"
        await _acabar()
        return estudos.estado(msg["id"])

    t = asyncio.run(main())
    c = t["correcao"]
    assert c[me1["id"]]["certa"] and not c[me2["id"]]["certa"] and c[vf["id"]]["certa"] is (vf["correta"] is True)
    assert c[disc["id"]]["pontos"] == 1.5 and c[disc["id"]]["certa"] is None and "Faltou" in c[disc["id"]]["feedback"]
    assert t["max"] == 5 and t["pontos"] == pytest.approx(1 + (1 if vf["correta"] else 0) + 1.5)
    assert t["nota"] == round(10 * t["pontos"] / 5, 1) and t["segundos"] == 125
    assert len(t["questoes"]) == 4 and "correta" in t["questoes"][0]   # a entrega vem com o gabarito
    assert estudos.estado(pid)["revelada"] and "explicacao" in estudos.estado(pid)["questoes"][0]
    assert {x["topico"] for x in t["por_topico"]} == {"Glicólise", "Fermentação"}


def test_entrega_so_de_fechadas_nao_chama_modelo(monkeypatch):
    pid, qs = _prova_pronta(monkeypatch, {"me": 2})
    msg = P.entregar(pid, {qs[0]["id"]: "A"})   # sem modelo: não precisa
    t = estudos.estado(msg["id"])
    assert t["status"] == "pronto" and not estudos._RUNS and t["max"] == 2
    assert t["correcao"][qs[1]["id"]]["resposta"] is None and t["correcao"][qs[1]["id"]]["pontos"] == 0


def test_discursiva_em_branco_nao_vai_para_o_modelo(monkeypatch):
    pid, qs = _prova_pronta(monkeypatch, {"disc": 1})
    t = estudos.estado(P.entregar(pid, {qs[0]["id"]: "   "})["id"])
    assert t["status"] == "pronto" and t["correcao"][qs[0]["id"]]["feedback"] == "Sem resposta."


def test_entregar_prova_que_nao_esta_pronta():
    conv = _estudo()
    msg = estudos._save(conv, role="assistant", content="", status="running",
                        meta={"estudos": {"tipo": "prova", "questoes": []}})
    with pytest.raises(ToolError, match="não está pronta"):
        P.entregar(msg.id, {})


def test_apagar_prova_leva_as_tentativas(monkeypatch):
    pid, qs = _prova_pronta(monkeypatch, {"me": 1})
    tid = P.entregar(pid, {})["id"]
    P.apagar_prova(pid)
    with db.session() as s:
        assert s.get(db.Message, pid) is None and s.get(db.Message, tid) is None


# ------------------------------------------------------------------ Claude via MCP


def test_claude_gera_a_prova_pedida(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    conv = _estudo()
    pid = P.start(conv, {"me": 2, "vf": 1, "instrucoes": "só cálculo"}, estudos.MOTOR_CLAUDE)["id"]
    assert estudos.estado(pid)["status"] == "aguardando" and not estudos._RUNS
    texto = asyncio.run(estudos.mcp_pedidos(0))
    assert f"PEDIDO {pid} — prova" in texto and "2 múltipla escolha, 1 verdadeiro ou falso" in texto and "só cálculo" in texto
    erro = P.mcp_salvar_prova([_me(1), {"tipo": "vf", "enunciado": "sem gabarito aqui"}], pedido_id=pid)
    assert erro.startswith("ERRO") and "questão 2" in erro and estudos.estado(pid)["status"] == "aguardando"
    ok = P.mcp_salvar_prova([_me(1), _me(2), _vf(3)], pedido_id=pid, modelo="Claude Opus 5.5")
    assert "3 questões" in ok
    e = estudos.estado(pid)
    assert e["status"] == "pronto" and len(e["questoes"]) == 3 and e["stats"]["escritor"] == "Claude Opus 5.5"
    assert "já atendido" in P.mcp_salvar_prova([_me(1)], pedido_id=pid)
    assert "Resumo" in estudos.mcp_ler_resumo(conv) and "## 1. Glicólise" in estudos.mcp_ler_resumo(conv)


def test_claude_corrige_a_discursiva(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    conv = _estudo()
    P.mcp_salvar_prova([_disc(1), _vf(2)], conv_id=conv)
    prova = P.lista(conv)[0]
    qs = _cheia(prova["message_id"])["questoes"]
    tid = P.entregar(prova["message_id"], {"q1": "Regenera NAD+ para a glicólise", "q2": "F"}, 0, estudos.MOTOR_CLAUDE)["id"]
    assert estudos.estado(tid)["status"] == "aguardando"
    texto = asyncio.run(estudos.mcp_pedidos(0))
    assert f"PEDIDO {tid} — corrigir" in texto and "Regenera NAD+ para a glicólise" in texto and "[q2]" not in texto
    assert "faltou corrigir q1" in P.mcp_corrigir(tid, [])
    assert "nota" in P.mcp_corrigir(tid, [{"questao_id": "q1", "pontos": "2", "feedback": "Completa."}])
    t = estudos.estado(tid)
    assert t["status"] == "pronto" and t["correcao"]["q1"]["pontos"] == 2 and t["correcao"]["q1"]["certa"]
    assert t["pontos"] == 2 + (0 if qs[1]["correta"] else 1)
    visto = P.mcp_ver_prova(prova["message_id"])
    assert "Resposta-modelo" in visto and f"Entrega {tid}" in visto
    assert "prova " in estudos.mcp_abrir(conv) and "tentativa" in estudos.mcp_abrir(conv)


# ------------------------------------------------------------------ API


def test_api_da_prova(monkeypatch):
    from app.main import app
    _fake(monkeypatch, verificar=_correto_do_prompt)
    conv = _estudo()

    def sse(r):
        return [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]

    with TestClient(app) as c:
        with c.stream("POST", f"/api/estudos/{conv}/prova", json={"config": {"me": 2, "vf": 1}, "provider": "fake", "model": "m"}) as r:
            eventos = sse(r)
        assert eventos[-1]["status"] == "pronto" and "correta" not in eventos[-1]["questoes"][0]
        pid = eventos[-1]["message_id"]
        assert c.get(f"/api/estudos/execucao/{pid}").json()["revelada"] is False
        assert c.post(f"/api/estudos/{conv}/prova", json={"config": {}, "provider": "fake", "model": "m"}).status_code == 400
        with c.stream("POST", f"/api/estudos/prova/{pid}/entregar", json={"respostas": {"q1": 0}, "segundos": 30}) as r:
            t = sse(r)[-1]
        assert t["status"] == "pronto" and t["max"] == 3 and len(t["questoes"]) == 3
        p = c.get(f"/api/estudos/{conv}").json()
        assert p["provas"][0]["tentativas"][0]["message_id"] == t["message_id"]
        assert c.delete(f"/api/estudos/prova/{pid}").json()["ok"]
        assert c.get(f"/api/estudos/{conv}").json()["provas"] == []


def test_ferramentas_mcp_da_prova_existem():
    from app import mcp_servidor
    nomes = {t.name for t in mcp_servidor.SERVIDOR._tool_manager.list_tools()}
    assert {"estudos_ler_resumo", "estudos_salvar_prova", "estudos_corrigir", "estudos_ver_prova"} <= nomes

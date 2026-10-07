from datetime import datetime, timedelta

import pytest

from app import conteudo, conteudo_agenda as A, conteudo_producao as P, conteudo_roteiros as R, db
from app.agent import _save
from app.tools import ToolError


class Relogio:
    def __init__(self, quando: str):
        self.t = datetime.fromisoformat(quando)

    def __call__(self):
        return self.t

    def vai(self, quando: str):
        self.t = datetime.fromisoformat(quando)


@pytest.fixture
def mundo(tmp_path, monkeypatch):
    estilos = tmp_path / "estilos"
    estilos.mkdir()
    (estilos / "alerta-tech.md").write_text("# Estilo\n", encoding="utf-8")
    with db.session() as s:
        for chave in (conteudo.CHAVE, A.CHAVE):
            s.query(db.AppSetting).filter(db.AppSetting.key == chave).delete()
        s.query(db.Conversation).filter(db.Conversation.kind == conteudo.KIND).delete()
        s.commit()
    conteudo.salvar_pastas({"pasta_estilos": str(estilos)})
    relogio = Relogio("2026-10-04T18:00")
    monkeypatch.setattr(A, "agora", relogio)
    monkeypatch.setattr(conteudo, "agora", relogio)   # a agenda conta a partir de quando foi ligada
    avisos, chamadas = [], {"roteiros": [], "producao": []}
    monkeypatch.setattr(A, "_avisa", lambda titulo, texto, cid: avisos.append((titulo, texto)))

    estado_rodada = {}

    def iniciar_roteiros(cid, ampliar=False):
        mid = _save(cid, role="assistant", name=R.NOME, status="running", meta={R.CHAVE: {"roteiros": []}}).id
        chamadas["roteiros"].append(mid)
        chamadas.setdefault("ampliar", []).append(ampliar)
        return {"id": mid}

    def termina_rodada(mid, confiancas):
        roteiros = R.normalizar([{"titulo": f"r{c}", "confianca": c, "cenas": [{"id": "a", "texto": "b"}]} for c in confiancas], [])
        with db.session() as s:
            m = s.get(db.Message, mid)
            m.status, m.meta = "ok", {R.CHAVE: {"roteiros": roteiros}}
            s.commit()
        return roteiros

    estado_producao = {"ocupado": False, "status": "rodando"}

    def iniciar_producao(cid, *a):
        if estado_producao["ocupado"]:
            raise ToolError("Já tem um vídeo sendo produzido; espere terminar ou cancele.")
        chamadas["producao"].append(cid)
        return {"id": 999}

    monkeypatch.setattr(R, "iniciar", iniciar_roteiros)
    monkeypatch.setattr(P, "iniciar", iniciar_producao)
    monkeypatch.setattr(P, "estado", lambda mid: {"status": estado_producao["status"], "aviso": ""})
    return {"relogio": relogio, "avisos": avisos, "chamadas": chamadas, "termina": termina_rodada,
            "producao": estado_producao, "rodada": estado_rodada}


def _spec(modo, hora_r="19:00", hora_p="03:00", **auto) -> int:
    return conteudo.salvar_especificacao({"nome": "IA", "tema": "x", "estilo": "alerta-tech",
                                          "automacao": {"modo": modo, "hora_roteiros": hora_r, "horarios": [hora_p], **auto}})["id"]


def test_desligada_nao_faz_nada(mundo):
    _spec("desligada")
    mundo["relogio"].vai("2026-10-04T19:05")
    assert A.tique() == [] and not A.acordado()


def test_aprovacao_fluxo_completo(mundo):
    cid = _spec("aprovacao")
    assert A.acordado()
    assert A.tique() == []                                  # 18:00: ainda não é hora
    mundo["relogio"].vai("2026-10-04T19:01")
    assert A.tique() == [f"{cid}: roteiros {mundo['chamadas']['roteiros'][0]}"]
    assert A.tique() == []                                  # rodando: espera; e não dispara de novo no mesmo dia
    mid = mundo["chamadas"]["roteiros"][0]
    roteiros = mundo["termina"](mid, [3, 5])
    assert A.tique() == [f"{cid}: roteiros prontos (2)"]
    assert mundo["avisos"][-1][0] == "Roteiros prontos para aprovar"
    assert R.aprovado(cid) is None                          # no modo aprovação quem aprova é o usuário

    R.marcar(mid, roteiros[0]["id"], "aprovado")
    mundo["relogio"].vai("2026-10-05T03:00")
    assert A.tique() == [f"{cid}: produção 999"] and mundo["chamadas"]["producao"] == [cid]
    mundo["producao"]["status"] = "ok"
    assert A.tique() == [f"{cid}: produção ok"]
    assert A.estado(cid)["p"]["etapa"] == "feito"
    assert A.tique() == []                                  # feito no dia: nada mais


def test_aprovacao_sem_aprovado_avisa(mundo):
    cid = _spec("aprovacao")
    mundo["relogio"].vai("2026-10-05T03:10")
    assert A.tique() == [f"{cid}: sem aprovado"]
    assert "Nenhum roteiro aprovado" in mundo["avisos"][-1][1] and mundo["chamadas"]["producao"] == []


def test_horario_perdido_pergunta_e_avisa(mundo):
    cid = _spec("automatico")
    mundo["relogio"].vai("2026-10-05T10:00")               # Forja ligado 7 h depois das 3h: não produz de surpresa
    assert A.tique() == [f"{cid}: 2026-10-05T03:00 perdido"]
    assert "desligado às 03:00" in mundo["avisos"][-1][1] and mundo["chamadas"]["roteiros"] == []
    est = A.estado(cid)
    assert est["perdido"]["slot"] == "2026-10-05T03:00" and est["proximas"]["p"] == "2026-10-06T03:00"
    assert A.tique() == [] and len(mundo["avisos"]) == 1  # pergunta uma vez só
    A.responder_perdido(cid, "rodar")
    assert len(mundo["chamadas"]["roteiros"]) == 1 and A.estado(cid)["perdido"] is None
    with pytest.raises(ToolError, match="Não tem horário perdido"):
        A.responder_perdido(cid, "pular")


def test_horario_perdido_pular(mundo):
    cid = _spec("automatico")
    mundo["relogio"].vai("2026-10-05T10:00")
    A.tique()
    A.responder_perdido(cid, "pular")
    assert mundo["chamadas"]["roteiros"] == [] and A.estado(cid)["perdido"] is None and A.tique() == []


def test_dentro_da_tolerancia_dispara(mundo):
    cid = _spec("automatico")
    mundo["relogio"].vai("2026-10-05T03:14")
    assert A.tique() == [f"{cid}: roteiros {mundo['chamadas']['roteiros'][0]}"]


def test_ligar_depois_do_horario_nao_pergunta(mundo, monkeypatch):
    monkeypatch.setattr(conteudo, "agora", lambda: datetime.fromisoformat("2026-10-05T09:00"))
    cid = _spec("automatico")                               # ligada às 9h: o das 3h de hoje não conta
    mundo["relogio"].vai("2026-10-05T10:00")
    assert A.tique() == [] and A.estado(cid)["proximas"]["p"] == "2026-10-06T03:00"


def test_dias_da_semana_e_varios_horarios(mundo):
    cid = _spec("automatico", hora_p="08:00", horarios=["18:00", "08:00"], dias=[0, 2])   # seg e qua
    spec = conteudo.especificacao(cid)
    assert spec["automacao"]["horarios"] == ["08:00", "18:00"] and spec["automacao"]["dias"] == [0, 2]
    mundo["relogio"].vai("2026-10-06T08:01")                # terça: não roda
    assert A.tique() == [] and A.estado(cid)["proximas"]["p"] == "2026-10-07T08:00"
    mundo["relogio"].vai("2026-10-07T08:01")                # quarta 8h
    assert A.tique()[0].endswith(str(mundo["chamadas"]["roteiros"][0]))
    roteiros = mundo["termina"](mundo["chamadas"]["roteiros"][0], [4])
    A.tique()
    mundo["producao"]["status"] = "ok"
    assert A.tique() == [f"{cid}: produção ok"]
    mundo["relogio"].vai("2026-10-07T18:02")                # e de novo às 18h do mesmo dia
    assert len(A.tique()) == 1 and len(mundo["chamadas"]["roteiros"]) == 2


def test_validacao_da_agenda(mundo):
    with pytest.raises(ToolError, match="pelo menos um dia"):
        _spec("automatico", dias=[])
    with pytest.raises(ToolError, match="Horário inválido"):
        _spec("automatico", hora_p="25:00")


def test_uma_pesquisa_por_vez(mundo, monkeypatch):
    a = _spec("automatico")
    b = conteudo.salvar_especificacao({"nome": "Espaço", "tema": "y", "estilo": "alerta-tech",
                                       "automacao": {"modo": "automatico", "horarios": ["03:00"]}})["id"]
    rodando = {"x": {"status": "rodando"}}
    monkeypatch.setattr(R, "_RUNS", rodando)
    mundo["relogio"].vai("2026-10-05T03:01")
    saida = A.tique()
    assert sorted(saida) == sorted([f"{a}: pesquisa na fila", f"{b}: pesquisa na fila"])
    rodando.clear()
    saida = A.tique()
    assert len(mundo["chamadas"]["roteiros"]) == 2 and all("roteiros" in x for x in saida)


def test_spec_antiga_sem_dias(mundo):
    cid = _spec("automatico")
    with db.session() as s:   # como ficava gravada antes dos dias/horários
        m = conteudo._msg_spec(s, cid)
        m.meta = {"especificacao": {**m.meta["especificacao"], "automacao": {"modo": "automatico", "hora_roteiros": "19:00", "hora_producao": "04:00"}}}
        s.commit()
    a = conteudo.especificacao(cid)["automacao"]
    assert a["horarios"] == ["04:00"] and a["dias"] == list(range(7))
    mundo["relogio"].vai("2026-10-05T04:01")
    assert len(A.tique()) == 1


def test_automatico_escolhe_o_de_maior_confianca(mundo):
    cid = _spec("automatico")
    mundo["relogio"].vai("2026-10-05T03:02")
    mid = mundo["chamadas"]["roteiros"][0] if A.tique() else None
    assert mid
    roteiros = mundo["termina"](mid, [3, 5, 5])
    saida = A.tique()
    assert saida == [f"{cid}: aprovado {roteiros[1]['id']}", f"{cid}: produção 999"]   # 5 vence; empate fica o 1º
    assert R.aprovado(cid)[1]["id"] == roteiros[1]["id"]


def test_automatico_espera_outra_producao(mundo):
    cid = _spec("automatico")
    mundo["relogio"].vai("2026-10-05T03:00")
    A.tique()
    mundo["termina"](mundo["chamadas"]["roteiros"][0], [4])
    mundo["producao"]["ocupado"] = True
    assert A.tique()[-1] == f"{cid}: produção na fila"
    assert A.tique() == [f"{cid}: produção na fila"]
    mundo["producao"]["ocupado"] = False
    assert A.tique() == [f"{cid}: produção 999"]


def test_roteiros_falharam_avisa(mundo):
    cid = _spec("automatico")
    mundo["relogio"].vai("2026-10-05T03:00")
    A.tique()
    def falha(mid):
        with db.session() as s:
            m = s.get(db.Message, mid)
            m.status, m.meta = "erro", {R.CHAVE: {"aviso": "busca fora do ar", "roteiros": []}}
            s.commit()
    falha(mundo["chamadas"]["roteiros"][0])
    # 1ª falha, sem pauta guardada: pesquisa de novo, com o dobro da janela de dias
    segunda = A.tique()
    assert segunda[0] == f"{cid}: 2ª pesquisa (busca fora do ar)" and mundo["chamadas"]["ampliar"] == [False, True]
    falha(mundo["chamadas"]["roteiros"][1])
    assert A.tique() == [f"{cid}: roteiros falharam"]
    assert mundo["avisos"][-1] == ("Conteúdo: sem roteiros hoje", "busca fora do ar")
    assert mundo["chamadas"]["producao"] == []
    # o slot do dia seguinte começa com as segundas chances zeradas
    mundo["relogio"].vai("2026-10-06T03:00")
    A.tique()
    assert mundo["chamadas"]["ampliar"][-1] is False


def test_roteiros_demorados_estouram(mundo):
    cid = _spec("automatico")
    mundo["relogio"].vai("2026-10-05T03:00")
    A.tique()
    mundo["relogio"].vai("2026-10-05T06:30")
    assert A.tique() == [f"{cid}: roteiros estourou o tempo"]
    assert A.estado(cid)["p"]["etapa"] == "falhou"


def test_activity_tem_acordado(mundo):
    from fastapi.testclient import TestClient
    from app import config
    from app.main import app
    c = TestClient(app, headers={"x-forja-token": config.API_TOKEN, "origin": "http://testserver"})
    assert c.get("/api/activity").json()["acordado"] is False
    _spec("aprovacao")
    assert c.get("/api/activity").json()["acordado"] is True


def test_cancelado_pelo_usuario_nao_avisa(mundo):
    cid = _spec("automatico")
    mundo["relogio"].vai("2026-10-05T03:00")
    A.tique()
    with db.session() as s:
        s.get(db.Message, mundo["chamadas"]["roteiros"][0]).status = "cancelado"
        s.commit()
    avisos_antes = len(mundo["avisos"])
    assert A.tique() == [f"{cid}: roteiros cancelados"]
    assert len(mundo["avisos"]) == avisos_antes and A.estado(cid)["p"]["aviso"] == "Pesquisa cancelada."


def test_api_agenda_geral_e_perdido(mundo):
    from fastapi.testclient import TestClient
    from app import config
    from app.main import app
    c = TestClient(app, headers={"x-forja-token": config.API_TOKEN, "origin": "http://testserver"})
    cid = _spec("automatico")
    mundo["relogio"].vai("2026-10-05T10:00")
    A.tique()
    geral = c.get("/api/conteudo/agenda").json()
    assert geral[0]["id"] == cid and geral[0]["perdido"]["slot"] == "2026-10-05T03:00"
    assert c.post(f"/api/conteudo/especificacoes/{cid}/agenda/perdido", json={"acao": "x"}).status_code == 400
    assert c.post(f"/api/conteudo/especificacoes/{cid}/agenda/perdido", json={"acao": "pular"}).json()["perdido"] is None
    assert c.get("/api/servico").json()["servico"] is False
    assert c.post("/api/servico/sair").status_code == 400


def test_servico_sair_agenda_saida(mundo, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config, servico
    from app.main import app
    saiu = []
    monkeypatch.setattr(servico, "eh_servico", lambda: True)
    monkeypatch.setattr(servico, "sair", lambda: saiu.append(1))
    c = TestClient(app, headers={"x-forja-token": config.API_TOKEN, "origin": "http://testserver"})
    assert c.post("/api/servico/sair").json() == {"ok": True}
    import time; time.sleep(0.5)
    assert saiu == [1]


def test_automatico_so_prepara(mundo):
    cid = _spec("automatico", produzir=False)
    assert conteudo.especificacao(cid)["automacao"]["produzir"] is False
    mundo["relogio"].vai("2026-10-05T03:01")
    A.tique()
    roteiros = mundo["termina"](mundo["chamadas"]["roteiros"][0], [3, 5])
    assert A.tique() == [f"{cid}: aprovado {roteiros[1]['id']}", f"{cid}: pronto para gerar"]
    assert mundo["chamadas"]["producao"] == [] and mundo["avisos"][-1][0] == "Roteiro pronto para gerar"
    est = A.estado(cid)
    assert est["p"]["etapa"] == "pronto" and est["pronto"]["roteiro"] == roteiros[1]["id"]
    assert A.tique() == [] and not A.ocupado()   # parado esperando a pessoa: não segura o serviço nem a fila
    R.marcar_produzido(est["pronto"]["rodada"], roteiros[1]["id"], "out/v.mp4")
    assert A.estado(cid)["pronto"] is None        # virou vídeo (gerado pela pessoa): o aviso some


def test_servico_sem_dpapi_nao_despareia_nem_apaga_chaves(monkeypatch, tmp_path):
    from app import config, mobile, segredo, servico, settings
    monkeypatch.setattr(servico, "eh_servico", lambda: True)
    f = tmp_path / "mobile_token"
    f.write_text("dpapi:QUJD", encoding="utf-8")   # cifrado por uma sessão que esta não abre
    monkeypatch.setattr(mobile, "_token_file", lambda: f)
    monkeypatch.setattr(mobile, "_atual", None)
    monkeypatch.setattr(segredo, "_dpapi", lambda dados, cifrar: (_ for _ in ()).throw(OSError("S4U")))
    monkeypatch.setattr(segredo, "falhou", False)
    assert mobile.token() and f.read_text(encoding="utf-8") == "dpapi:QUJD"   # não regravou: celular segue pareado
    assert segredo.falhou
    with pytest.raises(settings.SettingsError, match="sem login"):
        settings.update({"providers": []})


def test_pesquisa_falhou_usa_pauta_guardada(mundo):
    cid = _spec("automatico")
    # rodada de ontem: dois roteiros que ninguém usou
    antiga = _save(cid, role="assistant", name=R.NOME, status="running", meta={R.CHAVE: {"roteiros": []}}).id
    guardados = mundo["termina"](antiga, [2, 4])
    mundo["relogio"].vai("2026-10-05T03:00")
    A.tique()
    mid = mundo["chamadas"]["roteiros"][0]
    with db.session() as s:
        m = s.get(db.Message, mid)
        m.status, m.meta = "erro", {R.CHAVE: {"aviso": "nada novo", "roteiros": []}}
        s.commit()
    linhas = A.tique()
    assert linhas[0] == f"{cid}: pauta guardada (nada novo)" and linhas[-1] == f"{cid}: produção 999"
    assert R.aprovado(cid)[1]["id"] == guardados[1]["id"]          # a de maior confiança
    assert A._todos()[str(cid)]["p"]["aviso"] == "Pauta guardada: nada novo"
    assert mundo["chamadas"]["ampliar"] == [False]                # não precisou pesquisar de novo


def test_producao_que_falha_tenta_mais_uma_vez(mundo):
    cid = _spec("automatico")
    mundo["relogio"].vai("2026-10-05T03:00")
    A.tique()
    mundo["termina"](mundo["chamadas"]["roteiros"][0], [5])
    A.tique()
    assert mundo["chamadas"]["producao"] == [cid]
    mundo["producao"]["status"] = "erro"
    assert A.tique() == [f"{cid}: produção falhou, 2ª tentativa", f"{cid}: produção 999"]
    assert mundo["chamadas"]["producao"] == [cid, cid]
    assert A.tique() == [f"{cid}: produção erro"]                 # a 2ª também falhou: desiste
    assert A._todos()[str(cid)]["p"]["etapa"] == "falhou"

from datetime import datetime

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
    avisos, chamadas = [], {"roteiros": [], "producao": []}
    monkeypatch.setattr(A, "_avisa", lambda titulo, texto, cid: avisos.append((titulo, texto)))

    estado_rodada = {}

    def iniciar_roteiros(cid):
        mid = _save(cid, role="assistant", name=R.NOME, status="running", meta={R.CHAVE: {"roteiros": []}}).id
        chamadas["roteiros"].append(mid)
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


def _spec(modo, hora_r="19:00", hora_p="03:00") -> int:
    return conteudo.salvar_especificacao({"nome": "IA", "tema": "x", "estilo": "alerta-tech",
                                          "automacao": {"modo": modo, "hora_roteiros": hora_r, "hora_producao": hora_p}})["id"]


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


def test_fora_da_janela_nao_dispara(mundo):
    _spec("aprovacao")
    mundo["relogio"].vai("2026-10-05T10:00")               # app aberto 7 h depois das 3h: não produz de surpresa
    assert A.tique() == []
    est = A.estado(conteudo.especificacoes()[0]["id"])
    assert est["proximas"]["p"] == "2026-10-06T03:00" and est["proximas"]["r"] == "2026-10-05T19:00"


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
    mid = mundo["chamadas"]["roteiros"][0]
    with db.session() as s:
        m = s.get(db.Message, mid)
        m.status, m.meta = "erro", {R.CHAVE: {"aviso": "busca fora do ar", "roteiros": []}}
        s.commit()
    assert A.tique() == [f"{cid}: roteiros falharam"]
    assert mundo["avisos"][-1] == ("Conteúdo: sem roteiros hoje", "busca fora do ar")
    assert mundo["chamadas"]["producao"] == []


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

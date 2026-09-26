"""E10: métricas na tabela, cache perdido por auxiliar e o resumo da tela."""
from app import db, metricas


def _limpa():
    with db.session() as s:
        s.query(db.Metrica).delete()
        s.commit()


def test_registra_grava_e_resumo_agrega():
    _limpa()
    metricas.registra("llm", papel="agente", conv=1, timings={"prompt_n": 100, "cache_n": 900})
    metricas.registra("rota", papel="lateral", caminho="mesmo-slot-sequencial")
    metricas.registra("llm", papel="agente", conv=1, timings={"prompt_n": 5000, "cache_n": 0})  # a lateral derrubou
    metricas.registra("ferramenta", conv=1, nome="edit_file", status="erro")
    metricas.registra("ferramenta", conv=1, nome="edit_file", status="ok")
    metricas.registra("troca", de="a", para="b", segundos=12.5)
    metricas.registra("recuperacao", conv=1, nivel=3, motivo="x")
    r = metricas.resumo(1)
    assert r["cache"]["respostas"] == 2 and r["cache"]["derrubado_por_auxiliar"] == 1
    assert r["cache"]["auxiliares"] == [("lateral", 1)]
    assert r["cache"]["hit_pct"] == round(100 * 900 / 6000, 1)
    assert r["ferramentas_falham"][0] == {"nome": "edit_file", "falhas": 1, "total": 2, "pct": 50.0}
    assert r["trocas"] == {"n": 1, "segundos": 12.5} and r["recuperacao"]["3"] == 1
    assert ("lateral → mesmo-slot-sequencial", 1) in r["rotas"]


def test_metrica_nunca_derruba_quem_chama(monkeypatch):
    monkeypatch.setattr(db, "session", lambda: (_ for _ in ()).throw(RuntimeError("banco fora")))
    metricas.registra("ferramenta", nome="x", status="ok")  # não levanta

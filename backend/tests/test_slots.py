"""Slots do llama-server: cada Worker no seu slot auxiliar e a janela do cache KV compartilhado."""
from app import config, localai, modelctl


def _servidor(monkeypatch, n=3, ctx=131072, unificado=True):
    st = {"running": True, "alias": "qwen", "pid": 99, "ctx": ctx,
          "params": {"parallel": n, "kv_unified": unificado, "ctx": ctx}}
    monkeypatch.setattr(localai, "status", lambda: st)
    modelctl._EM_USO.clear()
    modelctl.USO_SLOT.clear()
    modelctl._USO_PID["pid"] = 99


def test_dois_workers_ficam_em_slots_diferentes_e_nunca_no_do_principal(monkeypatch):
    _servidor(monkeypatch)
    local = config.LOCAL_PROVIDER["id"]
    r1 = modelctl.como_rodar("worker", {"provider": local, "model": "qwen"})
    modelctl.ocupa(r1.slot, +1)                   # o 1º Worker reservou o dele
    r2 = modelctl.como_rodar("worker", {"provider": local, "model": "qwen"})
    assert (r1.slot, r2.slot) == (1, 2)
    modelctl.ocupa(r2.slot, +1)
    r3 = modelctl.como_rodar("lateral", {"provider": local, "model": "qwen"})
    assert r3.slot in (1, 2)                      # tudo ocupado: o menos usado, mas nunca o 0
    modelctl.ocupa(r1.slot, -1)
    assert modelctl.como_rodar("revisor", {"provider": local, "model": "qwen"}).slot == 1


def test_janela_livre_desconta_o_que_os_outros_slots_ocupam(monkeypatch):
    _servidor(monkeypatch)
    modelctl.registra_uso(0, {"prompt_n": 1000, "cache_n": 29000, "predicted_n": 500})
    modelctl.registra_uso(1, {"prompt_n": 50000, "cache_n": 0, "predicted_n": 1000})
    modelctl.registra_uso(2, {"prompt_n": 40000, "cache_n": 0, "predicted_n": 0})
    k = modelctl.kv_compartilhado()
    assert k == {"slots": 3, "total": 131072, "usado": 30500 + 51000 + 40000}
    assert modelctl.janela_livre(0, 131072) == 131072 - 91000
    modelctl.registra_uso(1, {"prompt_n": 120000, "cache_n": 0, "predicted_n": 0})
    assert modelctl.janela_livre(0, 131072) == 131072 // 4       # nunca menos que um quarto


def test_sem_kv_unificado_ou_um_slot_a_janela_e_a_do_slot(monkeypatch):
    _servidor(monkeypatch, unificado=False)
    assert modelctl.kv_compartilhado() is None and modelctl.janela_livre(0, 43690) == 43690
    _servidor(monkeypatch, n=1)
    assert modelctl.kv_compartilhado() is None

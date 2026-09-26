"""E4: perfis de hardware (Automático, Performance, Balanced, Low VRAM)."""
from app import config, localai, main, modelctl, perfis, settings  # noqa: F401

GB = 2**30


def _hw(*gpus):
    return {"gpus": [{"name": n, "total": int(t * GB), "free": int(t * GB), "enabled": True} for n, t in gpus]}


def test_8gb_com_modelo_de_7_e_low_vram():
    perfil, motivo = perfis.automatico(_hw(("NVIDIA GeForce RTX 3060 Ti", 8)), 7.0)
    assert perfil == "low_vram" and "8 GB" in motivo


def test_24gb_com_o_mesmo_modelo_e_performance():
    assert perfis.automatico(_hw(("NVIDIA GeForce RTX 4090", 24)), 7.0)[0] == "performance"
    assert perfis.automatico(_hw(("Intel(R) Arc(TM) B580 Graphics", 12)), 7.0)[0] == "balanced"


def test_modelo_maior_que_a_vram_e_low_vram_e_a_integrada_nao_conta():
    hw = _hw(("AMD Radeon(TM) Graphics", 16.8), ("Intel(R) Arc(TM) B580 Graphics", 11.8))
    assert round(perfis.vram_dedicada(hw) / GB, 1) == 11.8      # a integrada divide a RAM
    perfil, motivo = perfis.automatico(hw, 20.1)
    assert perfil == "low_vram" and "CPU" in motivo


def test_escolha_manual_e_nao_reavalia_no_meio(monkeypatch):
    monkeypatch.setattr(config, "PERFIL_HARDWARE", "performance")
    assert perfis.reavaliar()["perfil"] == "performance"
    monkeypatch.setattr(config, "PERFIL_HARDWARE", "auto")
    monkeypatch.setattr(localai, "hardware", lambda: _hw(("RTX 4090", 24)))
    monkeypatch.setattr(perfis, "_modelo_gb", lambda: 7.0)
    perfis.reavaliar()
    assert perfis.vigente()["perfil"] == "performance"
    monkeypatch.setattr(localai, "hardware", lambda: _hw(("RTX 3050", 6)))  # hardware mudou no meio...
    assert perfis.vigente()["perfil"] == "performance"                      # ...só vale no próximo reavaliar
    assert perfis.reavaliar()["perfil"] == "low_vram"


def test_ajuste_manual_sobrevive_a_troca_de_perfil(monkeypatch):
    monkeypatch.setattr(localai, "hardware", lambda: _hw(("RTX 3050", 6)))
    monkeypatch.setattr(perfis, "_modelo_gb", lambda: 5.0)
    settings.update({"cache_disco_gb": 2.5, "perfil_hardware": "low_vram"})
    try:
        assert config.CACHE_DISCO_GB == 2.5                     # o mexido vale por cima do perfil (8 GB)
        assert config.DESCARREGAR_OCIOSO_MIN == 5               # o não mexido vem do perfil
        settings.update({"perfil_hardware": "performance"})
        assert config.CACHE_DISCO_GB == 2.5 and config.DESCARREGAR_OCIOSO_MIN == 30
        settings.voltar_ao_perfil()
        assert config.CACHE_DISCO_GB == 4.0                     # voltou ao do perfil
    finally:
        settings.reset(["perfil_hardware", "cache_disco_gb"])


def test_low_vram_nunca_carrega_um_segundo_modelo_e_o_motivo_cita_o_perfil(monkeypatch):
    monkeypatch.setattr(config, "PERFIL_HARDWARE", "low_vram")
    perfis.reavaliar()
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "alias": "qwen", "pid": 1, "params": {"parallel": 1}})
    local = config.LOCAL_PROVIDER["id"]
    for papel in modelctl.PAPEIS:
        r = modelctl.como_rodar(papel, {"provider": local, "model": "outro"})
        assert r.caminho != "2o-modelo"
        assert "perfil Low VRAM" in r.motivo

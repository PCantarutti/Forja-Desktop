"""Ciclo de vida dos modelos: capacidades por backend, troca entre tarefas e liberação de VRAM.

O `localai` é substituído por um dublê que registra as chamadas — carregar um GGUF de verdade levaria
minutos e dependeria da máquina. O que importa aqui é a ordem das operações e quem é chamado.
"""
import asyncio

import pytest

from app import config, modelctl, subagents
from app.tools import ToolError


class FakeLocalAi:
    """Dublê do localai: guarda o que foi pedido e finge um llama-server."""

    def __init__(self, alias="", modelos=("A", "B"), imagem=False, falha=None, demora=0.0, janela=131072):
        self.alias = alias
        self.janela = janela
        self.modelos = list(modelos)
        self.imagem = imagem
        self.falha = falha
        self.demora = demora
        self.chamadas: list[str] = []
        self.cancelado = False

    # --- o que modelctl usa
    def status(self):
        return {"running": bool(self.alias), "alias": self.alias, "ctx": 8192 if self.alias else None,
                "loading": {}}

    def hardware(self):
        return {"vram": 8 << 30, "vram_free": 6 << 30, "ram": 16 << 30, "ram_free": 8 << 30}

    def scan(self):
        return [{"path": rf"D:\m\{n}.gguf", "name": n, "kind": "chat"} for n in self.modelos]

    def alias_of(self, path):
        return path.rsplit("\\", 1)[-1].removesuffix(".gguf")

    def image_busy(self):
        return self.imagem

    def ctx_de(self, path):
        return self.janela

    def load(self, path):
        self.chamadas.append(f"load:{path}")
        if self.demora:
            import time
            time.sleep(self.demora)
        if self.falha:
            raise self.falha
        self.alias = self.alias_of(path)
        return {"running": True, "alias": self.alias}

    def unload(self):
        self.chamadas.append("unload")
        self.alias = ""

    def cancel_load(self):
        self.cancelado = True
        self.chamadas.append("cancel_load")
        return True


@pytest.fixture(autouse=True)
def limpo(monkeypatch):
    monkeypatch.setitem(config.PROVIDERS, "local",
                        {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    monkeypatch.setattr(config, "MODEL_LIFECYCLE", "persistent")
    monkeypatch.setattr(config, "MAX_WORKERS", 1)
    yield


def _fake(monkeypatch, **kw):
    f = FakeLocalAi(**kw)
    monkeypatch.setattr(modelctl, "localai", f)
    monkeypatch.setattr(subagents, "localai", f)
    return f


def _colhe(gen):
    async def roda():
        return [ev async for ev in gen]

    return asyncio.run(roda())


LOCAL_A = {"provider": "local", "model": "A"}
LOCAL_B = {"provider": "local", "model": "B"}
NUVEM = {"provider": "lmstudio", "model": "grande"}


# ------------------------------------------------------------------ capacidades

def test_capacidades_por_backend():
    """Nem todo backend sabe descarregar: perguntar antes evita tentar o impossível."""
    assert "unload" in modelctl.caps_of("local")          # processo nosso
    assert "unload" not in modelctl.caps_of("lmstudio")   # ele gerencia os próprios modelos
    assert modelctl.caps_of("desconhecido") == frozenset()


def test_so_o_local_e_gerenciavel(monkeypatch):
    _fake(monkeypatch)
    assert modelctl.gerenciavel(LOCAL_A)
    assert not modelctl.gerenciavel(NUVEM)
    assert not modelctl.gerenciavel(None)


def test_status_nao_derruba_a_interface(monkeypatch):
    f = _fake(monkeypatch, alias="A")
    st = modelctl.status()
    assert st["running"] and st["alias"] == "A" and st["manageable"]
    monkeypatch.setattr(f, "hardware", lambda: (_ for _ in ()).throw(OSError("sem driver")))
    assert modelctl.status()["running"] is False  # degrada, não levanta


def test_troca_so_no_modo_sequencial(monkeypatch):
    assert modelctl.pode_trocar()
    monkeypatch.setattr(config, "MAX_WORKERS", 3)
    assert not modelctl.pode_trocar()  # matar o llama-server derrubaria o outro Worker no meio


# ------------------------------------------------------------------ ensure

def test_modelo_ja_carregado_nao_faz_nada(monkeypatch):
    f = _fake(monkeypatch, alias="A")
    assert _colhe(modelctl.ensure(LOCAL_A)) == []
    assert f.chamadas == []


def test_nuvem_nao_tem_ciclo_de_vida(monkeypatch):
    f = _fake(monkeypatch, alias="A")
    assert _colhe(modelctl.ensure(NUVEM)) == []
    assert f.chamadas == []


def test_troca_descarrega_e_carrega_na_ordem(monkeypatch):
    f = _fake(monkeypatch, alias="A")
    out: dict = {}
    fases = [(e["phase"], e.get("previous"), e.get("model")) for e in _colhe(modelctl.ensure(LOCAL_B, out))]
    assert fases == [("unloading", "A", "B"), ("loading", "A", "B"), ("ready", "A", "B")]
    assert f.alias == "B"
    assert out == {"swapped": True, "previous": "A", "model": "B"}
    # load() do localai já descarrega o anterior sozinho: não duplicamos o unload
    assert f.chamadas == [r"load:D:\m\B.gguf"]


def test_primeira_carga_nao_anuncia_descarga(monkeypatch):
    _fake(monkeypatch, alias="")
    fases = [e["phase"] for e in _colhe(modelctl.ensure(LOCAL_A))]
    assert fases == ["loading", "ready"]


def test_modelo_fora_das_pastas_e_erro_claro(monkeypatch):
    _fake(monkeypatch, alias="A", modelos=("A",))
    with pytest.raises(ToolError, match="não está em nenhuma pasta"):
        _colhe(modelctl.ensure({"provider": "local", "model": "sumiu"}))


def test_imagem_gerando_bloqueia_a_troca(monkeypatch):
    """sd.cpp e llama disputam a mesma VRAM."""
    _fake(monkeypatch, alias="A", imagem=True)
    with pytest.raises(ToolError, match="imagem"):
        _colhe(modelctl.ensure(LOCAL_B))


def test_falha_na_carga_vira_evento_e_erro(monkeypatch):
    _fake(monkeypatch, alias="A", falha=ToolError("VRAM insuficiente"))
    eventos = []

    async def roda():
        async for ev in modelctl.ensure(LOCAL_B):
            eventos.append(ev)

    with pytest.raises(ToolError, match="VRAM insuficiente"):
        asyncio.run(roda())
    assert eventos[-1]["phase"] == "error"


def test_parar_durante_a_carga_cancela(monkeypatch):
    """Carregar leva minutos; o botão Parar não pode esperar o fim."""
    f = _fake(monkeypatch, alias="A", demora=0.4)

    async def roda():
        cancel = asyncio.Event()

        async def para():
            await asyncio.sleep(0.05)
            cancel.set()

        asyncio.ensure_future(para())
        return [ev async for ev in modelctl.ensure(LOCAL_B, {}, cancel)]

    asyncio.run(roda())
    assert f.cancelado and "cancel_load" in f.chamadas


# ------------------------------------------------------------------ estratégias

def test_persistent_mantem_o_modelo(monkeypatch):
    f = _fake(monkeypatch, alias="A")
    assert _colhe(modelctl.after_task(LOCAL_A)) == []
    assert f.alias == "A"  # recarregar custa minutos: só descarrega quem pediu


def test_unload_after_task_libera_a_vram(monkeypatch):
    f = _fake(monkeypatch, alias="A")
    monkeypatch.setattr(config, "MODEL_LIFECYCLE", "unload_after_task")
    fases = [e["phase"] for e in _colhe(modelctl.after_task(LOCAL_A))]
    assert fases == ["unloading", "unloaded"]
    assert f.alias == "" and "unload" in f.chamadas


def test_unload_after_task_ignora_a_nuvem(monkeypatch):
    f = _fake(monkeypatch, alias="A")
    monkeypatch.setattr(config, "MODEL_LIFECYCLE", "unload_after_task")
    assert _colhe(modelctl.after_task(NUVEM)) == []
    assert f.alias == "A"


def test_lifecycle_desconhecido_cai_no_padrao(monkeypatch):
    monkeypatch.setattr(config, "MODEL_LIFECYCLE", "inventado")
    assert modelctl.lifecycle() == "persistent"


# ------------------------------------------------------------------ elegibilidade do slot

def test_sem_swap_o_slot_local_errado_sai_da_cadeia(monkeypatch):
    """Comportamento de sempre: o llama.cpp ignora o campo `model`, então pedir outro alias rodaria
    o modelo errado calado."""
    _fake(monkeypatch, alias="A")
    config.SUBAGENTS = {"capaz": LOCAL_B, "nuvem": NUVEM}
    assert [lvl for lvl, _ in subagents.chain("capaz")] == ["nuvem"]


def test_com_swap_o_slot_local_volta_a_valer(monkeypatch):
    """É o que permite o ciclo carrega A → tarefa → carrega B."""
    _fake(monkeypatch, alias="A")
    config.SUBAGENTS = {"capaz": LOCAL_B, "nuvem": NUVEM}
    assert [lvl for lvl, _ in subagents.chain("capaz", swap=True)] == ["capaz", "nuvem"]


def test_com_swap_modelo_inexistente_continua_fora(monkeypatch):
    _fake(monkeypatch, alias="A", modelos=("A",))
    config.SUBAGENTS = {"capaz": {"provider": "local", "model": "sumiu"}, "nuvem": NUVEM}
    assert [lvl for lvl, _ in subagents.chain("capaz", swap=True)] == ["nuvem"]
    assert "não está em nenhuma pasta" in subagents._why_not(swap=True)


def test_nao_descarrega_o_modelo_que_a_maestro_usa(monkeypatch):
    """Máquina inteiramente local: Maestro e Worker dividem o mesmo llama-server. Descarregar ali
    deixaria a Maestro sem servidor justamente ao ler o resultado da tarefa."""
    f = _fake(monkeypatch, alias="A")
    monkeypatch.setattr(config, "MODEL_LIFECYCLE", "unload_after_task")
    assert _colhe(modelctl.after_task(LOCAL_A, maestro=LOCAL_A)) == []
    assert f.alias == "A"


def test_descarrega_quando_a_maestro_esta_na_nuvem(monkeypatch):
    f = _fake(monkeypatch, alias="A")
    monkeypatch.setattr(config, "MODEL_LIFECYCLE", "unload_after_task")
    fases = [e["phase"] for e in _colhe(modelctl.after_task(LOCAL_A, maestro=NUVEM))]
    assert fases == ["unloading", "unloaded"] and f.alias == ""


def test_descarrega_quando_a_maestro_usa_outro_local(monkeypatch):
    """Worker terminou no A, Maestro é o B: nada impede liberar a VRAM do A."""
    f = _fake(monkeypatch, alias="A")
    monkeypatch.setattr(config, "MODEL_LIFECYCLE", "unload_after_task")
    fases = [e["phase"] for e in _colhe(modelctl.after_task(LOCAL_A, maestro=LOCAL_B))]
    assert fases == ["unloading", "unloaded"] and f.alias == ""


def test_janela_curta_diz_o_numero_e_onde_resolver(monkeypatch):
    _fake(monkeypatch, alias="A", janela=8192)
    msg = modelctl.janela_curta(LOCAL_A, 32768, "a Maestro")
    assert "8.192" in msg and "32.768" in msg and "a Maestro" in msg and "parallel" in msg
    assert modelctl.janela_curta(LOCAL_A, 4096, "a Maestro") == ""


def test_janela_por_requisicao_divide_pelo_parallel():
    """ctx=32768 com parallel=4: o servidor recusava acima de 8192. É esse o número que vale."""
    from app import localai
    assert localai.ctx_por_requisicao(32768, {"parallel": 4}) == 8192
    assert localai.ctx_por_requisicao(131072, {"parallel": 1}) == 131072
    assert localai.ctx_por_requisicao(131072, {}) == 131072
    assert localai.ctx_por_requisicao(None, {"parallel": 2}) == 0

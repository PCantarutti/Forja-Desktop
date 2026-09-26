"""E4: a política de execução (modelctl.como_rodar) e o slot fixo do principal."""
import asyncio

import pytest

from app import agent, config, db, llm, localai, main, modelctl, workspace  # noqa: F401

LOCAL = config.LOCAL_PROVIDER["id"]


@pytest.fixture
def servidor(monkeypatch):
    """llama-server simulado: {"alias": carregado ou "", "parallel": -np}."""
    estado = {"alias": "qwen", "parallel": 1}

    def status():
        if not estado["alias"]:
            return {"running": False}
        return {"running": True, "alias": estado["alias"], "pid": 1, "params": {"parallel": estado["parallel"]}}

    monkeypatch.setattr(localai, "status", status)
    monkeypatch.setattr(config, "NUVEM_POR_PAPEL", {"explorador": False, "revisor": False, "visual": False})
    monkeypatch.setattr(config, "SUBAGENTS", {"rapido": {}, "capaz": {}, "nuvem": {"provider": "nuvem-x", "model": "gpt"}})
    monkeypatch.setitem(config.PROVIDERS, "nuvem-x", {"id": "nuvem-x", "type": "openai", "url": "https://x/v1"})
    return estado


def _local(modelo):
    return {"provider": LOCAL, "model": modelo}


def test_principal_tem_slot_fixo(servidor):
    r = modelctl.como_rodar("principal", _local("qwen"))
    assert (r.caminho, r.slot) == ("mesmo-slot", modelctl.SLOT_PRINCIPAL)
    assert modelctl.como_rodar("principal", {"provider": "nuvem-x", "model": "gpt"}).slot is None


def test_auxiliar_nunca_pega_o_slot_do_principal_quando_ha_outro(servidor):
    servidor["parallel"] = 2
    r = modelctl.como_rodar("lateral", _local("qwen"))
    assert (r.caminho, r.slot) == ("outro-slot", modelctl.SLOT_AUXILIAR)
    servidor["parallel"] = 1  # com um slot só, espera e divide (o motivo diz)
    r = modelctl.como_rodar("revisor", _local("qwen"))
    assert r.caminho == "mesmo-slot-sequencial" and "-np 1" in r.motivo


def test_auxiliar_nao_troca_de_modelo(servidor):
    for papel in ("explorador", "revisor", "lateral", "compactar"):
        r = modelctl.como_rodar(papel, _local("outro-modelo"))
        assert r.caminho == "modelo-do-principal" and r.spec == _local("qwen"), papel


def test_visual_pula_em_vez_de_trocar(servidor):
    r = modelctl.como_rodar("visual", _local("modelo-com-visao"))
    assert r.caminho == "pular" and r.spec is None and "VRAM" in r.motivo
    servidor["alias"] = ""  # nada carregado: carregar não derruba ninguém
    assert modelctl.como_rodar("visual", _local("modelo-com-visao")).caminho == "trocar-modelo"


def test_worker_e_a_unica_excecao(servidor):
    assert modelctl.como_rodar("worker", _local("ornith")).caminho == "trocar-modelo"


def test_nuvem_so_quando_liberada(servidor, monkeypatch):
    assert modelctl.como_rodar("explorador", _local("outro")).caminho == "modelo-do-principal"
    monkeypatch.setattr(config, "NUVEM_POR_PAPEL", {"explorador": True, "revisor": False, "visual": False})
    r = modelctl.como_rodar("explorador", _local("outro"))
    assert r.caminho == "nuvem" and r.spec["provider"] == "nuvem-x"
    assert modelctl.como_rodar("revisor", _local("outro")).caminho == "modelo-do-principal"  # não liberado
    assert modelctl.como_rodar("explorador", _local("qwen")).caminho == "mesmo-slot-sequencial"  # carregado vence


def test_provedor_externo_segue_direto(servidor):
    r = modelctl.como_rodar("revisor", {"provider": "nuvem-x", "model": "gpt"})
    assert r.caminho == "externo" and r.spec["model"] == "gpt"


def test_llm_manda_id_slot_so_para_o_llamacpp(monkeypatch):
    vistos = []

    async def fake(provider, model, messages, tools, num_ctx, extra):
        vistos.append(dict(extra))
        yield "done", {"tool_calls": []}

    async def nada(*a, **k):
        return None

    monkeypatch.setattr(llm, "_openai_stream", fake)
    monkeypatch.setattr(llm, "_reasoning", nada)
    monkeypatch.setattr(llm, "_inference", lambda *a: None)
    monkeypatch.setitem(config.PROVIDERS, "nuvem-x", {"id": "nuvem-x", "type": "openai", "url": "https://x/v1"})

    async def roda(provider, **kw):
        return [ev async for ev in llm.chat_stream(provider, "m", [], None, 8192, **kw)]

    asyncio.run(roda(LOCAL, slot=0))
    asyncio.run(roda("nuvem-x", slot=0))
    assert vistos[0]["id_slot"] == 0 and vistos[0]["cache_prompt"] is True
    assert "id_slot" not in vistos[1]


def test_principal_do_agente_vai_no_slot_0(monkeypatch, tmp_path, servidor):
    kws = []

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        kws.append(kw)
        yield "content", "ok"
        yield "done", {"tool_calls": []}

    async def nada(*a):
        return None

    monkeypatch.setattr(llm, "chat_stream", fake)
    monkeypatch.setattr(llm, "context_limit", nada)
    monkeypatch.setattr(llm, "capabilities", nada)

    async def cenario():
        with db.session() as s:
            c = db.Conversation(kind="agent", workspace=str(tmp_path))
            s.add(c)
            s.commit()
            conv = c.id
        tok = workspace.CURRENT.set(tmp_path)
        try:
            run = agent.Run(conv)
            req = agent.RunRequest(content="oi", provider=LOCAL, model="qwen", mode="agent", permission="manual")
            return [ev async for ev in agent.run_agent(conv, req, run)]
        finally:
            workspace.CURRENT.reset(tok)

    asyncio.run(cenario())
    assert kws and kws[0].get("slot") == modelctl.SLOT_PRINCIPAL

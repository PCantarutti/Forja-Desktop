import asyncio

import pytest

from app import design


def _run(modelo="m"):
    return {"spec": {"provider": "p", "model": modelo}}


def test_cabe_nao_mexe():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "x" * 3000}]
    assert design._caber(_run(), msgs, 32768) is msgs


def test_corta_referencias_para_caber():
    user = "Pedido: refaz o hero\n\nReferências que o usuário anexou (use como base):\n" + "codigo " * 20000
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": user}]
    run = _run()
    novo = design._caber(run, msgs, 16384)
    assert design._estimar(novo) <= 16384 - 4915 and "cortado para caber" in novo[1]["content"]
    assert novo[1]["content"].startswith("Pedido: refaz o hero") and "Cortou parte das referências" in run["passos_extra"][0]


def test_documento_grande_demais_recusa_com_o_que_fazer():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "Documento atual:\n" + "<div></div>" * 20000}]
    with pytest.raises(design.JanelaCheia, match="Peça por uma seção"):
        design._caber(_run("qwen-8k"), msgs, 8192)


def test_chamar_leva_a_janela_para_as_estatisticas(monkeypatch):
    async def janela(*a):
        return 32768

    async def fluxo(*a, **k):
        yield "content", "ok"
        yield "done", {}
    monkeypatch.setattr(design.llm, "context_limit", janela)
    design._JANELA.clear()
    monkeypatch.setattr(design.llm, "chat_stream", fluxo)
    run = {"spec": {"provider": "p", "model": "m"}, "esforco": "baixo", "cancelar": False, "parcial": "", "vivos": 0,
           "raciocinio": "", "stats": []}
    asyncio.run(design._chamar(run, [{"role": "system", "content": "s"}, {"role": "user", "content": "oi"}]))
    assert run["stats"][0]["ctx_max"] == 32768

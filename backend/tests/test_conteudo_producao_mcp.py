"""Produção pelo Claude conectado (modo MCP): fila, pegar, entregar, falha e revisão — sem claude -p."""
import asyncio

import pytest

from app import config, conteudo, conteudo_producao as P, mcp_servidor
from app.tools import ToolError
from tests.test_conteudo_producao import _aprovado, ambiente  # noqa: F401 (fixture autouse do módulo de produção)


@pytest.fixture(autouse=True)
def modo_mcp(ambiente, monkeypatch):  # noqa: F811
    monkeypatch.setattr(P, "achar_claude", lambda: None)   # prova que o modo MCP não precisa do Claude Code no PC
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    conteudo.salvar_pastas({"produtor": "mcp"})
    return ambiente


async def _ate(cond, vezes=300):
    for _ in range(vezes):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


def test_exige_o_interruptor_do_mcp(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", False)
    cid, _, _ = _aprovado()
    with pytest.raises(ToolError, match="Permitir que o Claude controle o Forja"):
        P.iniciar(cid)
    with pytest.raises(ToolError, match="Produtor inválido"):
        conteudo.salvar_pastas({"produtor": "robô"})


def test_fila_pegar_e_entregar(modo_mcp):
    cid, _, _ = _aprovado()

    async def fluxo():
        est = P.iniciar(cid)
        mid = est["id"]
        await _ate(lambda: P.estado(mid).get("fase", "").startswith("aguardando"))
        assert P.estado(mid)["status"] == "rodando"
        bloco = await P.mcp_producoes(0)
        run = P._RUNS[mid]
        assert f"PRODUÇÃO {mid}" in bloco and str(modo_mcp["projeto"]) in bloco and f"out/{run['slug']}.mp4" in bloco
        assert "conteudo_entregar_video" in bloco and "Roteiro aprovado" in bloco     # o pedido inteiro vai junto
        assert "Em andamento" in await P.mcp_producoes(0)                              # pego não volta como novo
        assert P.mcp_entregar(mid, "out/nao-existe.mp4").startswith("ERRO")
        assert P.mcp_entregar(999999, "x").startswith("ERRO")
        (modo_mcp["projeto"] / "out").mkdir(exist_ok=True)
        (modo_mcp["projeto"] / "out" / f"{run['slug']}.mp4").write_bytes(b"mp4 do claude conectado")
        assert P.mcp_entregar(mid, f"out/{run['slug']}.mp4", "Pronto.\nMÍDIA: faltou o trailer oficial.").startswith("Recebido")
        await _ate(lambda: mid not in P._RUNS and (P.estado(mid).get("qc") or {}))
        return P.estado(mid)
    est = asyncio.run(fluxo())
    assert est["status"] == "ok" and est["entregue"].endswith(".mp4") and "trailer oficial" in est["aviso"]
    assert est["qc"]["ok"] is True                       # passou pela conferência como uma produção normal


def test_entrega_com_erro_e_revisao_vai_para_a_fila(modo_mcp):
    cid, _, _ = _aprovado()

    async def fluxo():
        mid = P.iniciar(cid)["id"]
        await P.mcp_producoes(0)
        assert P.mcp_entregar(mid, erro="o Remotion não renderizou").startswith("Registrado")
        await _ate(lambda: mid not in P._RUNS)
        falhou = P.estado(mid)
        # uma que dá certo, para revisar
        mid2 = P.iniciar(cid)["id"]
        await P.mcp_producoes(0)
        slug = P._RUNS[mid2]["slug"]
        (modo_mcp["projeto"] / "out").mkdir(exist_ok=True)
        (modo_mcp["projeto"] / "out" / f"{slug}.mp4").write_bytes(b"v1")
        P.mcp_entregar(mid2, f"out/{slug}.mp4", "Pronto.")
        await _ate(lambda: mid2 not in P._RUNS and (P.estado(mid2).get("qc") or {}))
        rev = P.revisar(mid2, [], "deixe a música mais baixa")
        bloco = await P.mcp_producoes(0)
        return falhou, rev, bloco
    falhou, rev, bloco = asyncio.run(fluxo())
    assert falhou["status"] == "erro" and "não renderizou" in falhou["aviso"]
    assert f"PRODUÇÃO {rev['id']}" in bloco and "(versão 2)" in bloco and "música mais baixa" in bloco
    P.cancelar(rev["id"])


def test_ferramentas_mcp_de_producao_registradas():
    nomes = {t.name for t in asyncio.run(mcp_servidor.SERVIDOR.list_tools())}
    assert {"conteudo_producoes", "conteudo_entregar_video"} <= nomes


def test_outras_ferramentas_avisam_do_video_na_fila(modo_mcp):
    from app import conteudo_roteiros as R
    cid, _, _ = _aprovado()

    async def fluxo():
        mid = P.iniciar(cid)["id"]
        await _ate(lambda: P.estado(mid).get("fase", "").startswith("aguardando"))
        antes = R.aviso_pedidos()
        await P.mcp_producoes(0)
        depois = R.aviso_pedidos()
        P.cancelar(mid)
        await _ate(lambda: mid not in P._RUNS)
        return antes, depois
    antes, depois = asyncio.run(fluxo())
    assert "1 vídeo(s) da tela Conteúdo esperando você produzir" in antes and "vídeo" not in depois

"""Celular pareado: token estável aceito pelo middleware e push disparado quando a IA pede aprovação."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import config, mobile
from app.agent import Run
from app.main import app


@pytest.fixture
def isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mobile, "_atual", None)
    monkeypatch.setattr(config, "API_TOKEN", "do-electron")


def test_token_do_celular_passa_e_revogado_nao(isolado):
    with TestClient(app) as c:
        antigo = mobile.token()
        assert c.get("/api/config", headers={"X-Forja-Token": antigo}).status_code == 200
        assert c.get("/api/config", headers={"X-Forja-Token": "errado"}).status_code == 403
        novo = c.post("/api/mobile/rotate", headers={"X-Forja-Token": "do-electron"}).json()["token"]
        assert novo != antigo
        assert c.get("/api/config", headers={"X-Forja-Token": antigo}).status_code == 403
        assert c.get("/api/config", headers={"X-Forja-Token": novo}).status_code == 200


def test_register_recusa_token_de_push_invalido(isolado):
    with TestClient(app) as c:
        h = {"X-Forja-Token": "do-electron"}
        assert c.post("/api/mobile/register", json={"expo_token": "x"}, headers=h).status_code == 400
        assert c.post("/api/mobile/register", json={"expo_token": "ExponentPushToken[a]"}, headers=h).status_code == 200
    assert mobile.devices() == ["ExponentPushToken[a]"]


def test_aprovacao_vira_push_e_token_nao(isolado, monkeypatch):
    enviados = []

    async def falso(msgs):
        enviados.extend(msgs)
    monkeypatch.setattr(mobile, "_enviar", falso)
    mobile.register("ExponentPushToken[a]")

    async def cenario():
        run = Run(7)
        await run.publish({"type": "assistant_start"})
        await run.publish({"type": "token", "text": "oi"})
        await run.publish({"type": "approval_request", "call": {"id": "c1", "name": "run_command", "arguments": {}},
                           "preview": None})
        await asyncio.sleep(0)
    asyncio.run(cenario())
    assert len(enviados) == 1
    assert enviados[0]["to"] == "ExponentPushToken[a]"
    assert "title" not in enviados[0]  # só dados: o app desenha (e pode tirar) a notificação
    assert enviados[0]["data"] == {"forja": "mostra", "titulo": "Aprovação pendente", "texto": "run_command quer rodar",
                                   "conv_id": 7, "run_id": enviados[0]["data"]["run_id"], "call_id": "c1"}


def test_pedido_decidido_revoga_a_notificacao(isolado, monkeypatch):
    """Decidido em qualquer lugar (vira tool_result) ou turno encerrado com pedido aberto: push só de dados."""
    enviados = []

    async def falso(msgs):
        enviados.extend(msgs)
    monkeypatch.setattr(mobile, "_enviar", falso)
    mobile.register("ExponentPushToken[a]")

    async def cenario():
        run = Run(7)
        for cid in ("c1", "c2"):
            await run.publish({"type": "approval_request", "call": {"id": cid, "name": "run_command", "arguments": {}}, "preview": None})
        await run.publish({"type": "tool_result", "message": {"tool_call_id": "c1"}})
        await run.publish({"type": "tool_result", "message": {"tool_call_id": "outra"}})  # não estava esperando
        await run.publish({"type": "done"})
        await asyncio.sleep(0)
    asyncio.run(cenario())
    revogas = [m["data"]["call_ids"] for m in enviados if m.get("data", {}).get("forja") == "revoga"]
    assert revogas == [["c1"], ["c2"]]
    assert all("title" not in m for m in enviados if m.get("data", {}).get("forja") == "revoga")


def test_expose_so_servidor_vivo_do_agente(isolado, monkeypatch):
    """O celular pede pelo nome; porta arbitrária (ex.: o próprio llama-server) nunca é publicada."""
    from app import shell
    chamadas = []
    monkeypatch.setattr(shell, "list_servers", lambda: [
        {"name": "site", "alive": True, "url": "http://127.0.0.1:8791"},
        {"name": "morto", "alive": False, "url": ""}])
    monkeypatch.setattr(mobile, "expose", lambda p: chamadas.append(p) or f"https://pc.ts.net:{p}")
    with TestClient(app) as c:
        h = {"X-Forja-Token": "do-electron"}
        assert c.post("/api/mobile/expose/site", headers=h).json() == {"url": "https://pc.ts.net:8791"}
        assert c.post("/api/mobile/expose/morto", headers=h).status_code == 404
        assert c.post("/api/mobile/expose/8077", headers=h).status_code == 404
    assert chamadas == [8791]


def test_rede_local_exige_token_em_toda_rota(isolado):
    """Na LAN não há tailnet autenticando: até /api/files e as imagens pedem o token (header ou ?t=)."""
    import httpx

    async def roda():
        porta = httpx.AsyncClient(transport=httpx.ASGITransport(app=mobile._porteiro(app)), base_url="http://lan")
        async with porta as c:
            tok = mobile.token()
            assert (await c.get("/api/config")).status_code == 403
            assert (await c.get("/api/files?path=x&conv=1")).status_code == 403  # sem token no desktop, aqui não
            assert (await c.get("/", headers={"X-Forja-Token": tok})).status_code == 403  # só /api
            assert (await c.get("/api/config", headers={"X-Forja-Token": "errado"})).status_code == 403
            assert (await c.get("/api/config", headers={"X-Forja-Token": tok})).status_code == 200
            assert (await c.get(f"/api/config?t={tok}")).status_code == 200  # <Image>/vídeo mandam na URL
    asyncio.run(roda())

import asyncio
import socket

from app import mobile


def test_vigia_lan_religa_quando_a_porta_solta(tmp_path, monkeypatch):
    """Na atualização o app novo sobe com a porta ainda presa pelo antigo: a LAN tem que voltar sozinha."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    monkeypatch.setattr(mobile, "LAN_PORTA", porta)
    monkeypatch.setattr(mobile, "_lan_file", lambda: tmp_path / "mobile_lan")
    (tmp_path / "mobile_lan").write_text("1")
    monkeypatch.setattr(mobile, "_lan", {})

    async def app(scope, receive, send):
        pass

    async def roteiro():
        preso = socket.socket()
        preso.bind(("0.0.0.0", porta))
        preso.listen()
        vigia = asyncio.create_task(mobile.vigia_lan(app, intervalo=0.05))
        await asyncio.sleep(0.3)
        assert not mobile._lan                      # porta presa: desligado, tentando
        preso.close()
        for _ in range(60):
            if mobile._lan:
                break
            await asyncio.sleep(0.05)
        assert mobile._lan                          # soltou: subiu sozinho, sem ninguém mexer
        vigia.cancel()
        await mobile.desliga_lan()

    asyncio.run(roteiro())

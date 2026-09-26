"""Site do agente pela rede local: proxy para localhost com o Host de localhost, só para o celular pareado."""
import asyncio
import http.server
import threading
import time

import httpx
import pytest

from app import mobile


@pytest.fixture
def site():
    visto = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            visto["host"] = self.headers["Host"]
            corpo = b"<html>ok</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Last-Modified", "Fri, 25 Sep 2026 10:00:00 GMT")  # o WebView cacheava por isto
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], visto
    srv.shutdown()


def test_proxy_repasse_com_host_de_localhost_so_para_o_celular(site, monkeypatch):
    porta, visto = site
    app = mobile._proxy_site(porta)

    async def pede():
        transporte = httpx.ASGITransport(app=app, client=("10.0.0.9", 5555))
        async with httpx.AsyncClient(transport=transporte, base_url="http://192.168.1.16:47820") as c:
            assert (await c.get("/")).status_code == 403  # IP que nunca usou o token
            monkeypatch.setitem(mobile._celulares, "10.0.0.9", time.time())
            return await c.get("/index.html?x=1")

    r = asyncio.run(pede())
    assert r.status_code == 200 and r.text == "<html>ok</html>"
    assert visto["host"] == f"localhost:{porta}"  # o Vite recusa host desconhecido
    # a mesma porta do proxy serve outro site depois: o celular não pode mostrar o velho do cache
    assert r.headers["cache-control"] == "no-store" and "last-modified" not in r.headers

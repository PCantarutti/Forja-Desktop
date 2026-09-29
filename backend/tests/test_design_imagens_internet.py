import asyncio
import io
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import design_imagens


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1200), (200, 120, 40)).save(buf, "PNG")
    return buf.getvalue()


class _Site(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/pao.png":
            corpo, tipo = _png(), "image/png"
        else:
            corpo, tipo = b"<html>nada</html>", "text/html"
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


@pytest.fixture
def site(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # o servidor de teste é local: nos testes, 127.0.0.1 conta como "internet" (na vida real é recusado)
    monkeypatch.setattr(design_imagens, "_host_publico", lambda url: "127.0.0.1" in url)
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_baixa_embute_e_link_ruim_vira_slot(site):
    html = (f'<body><img data-slot="pao-1111" data-prompt="bread" src="{site}/pao.png" alt="pão">'
            f'<img src="{site}/pagina.html" alt="Forno a lenha"></body>')
    novo, passos = asyncio.run(design_imagens.fotos_da_internet(html))
    boa, ruim = re.findall(r"<img[^>]*>", novo)
    assert "data:image/webp;base64," in boa and 'data-slot-status="pronta"' in boa and f'data-fonte="{site}/pao.png"' in boa
    assert "src=" not in ruim and re.search(r'data-slot="forno-a-lenha-\d{4}"', ruim) and 'data-prompt="Forno a lenha"' in ruim
    assert any("Baixou" in p for p in passos) and any("não é imagem" in p for p in passos)


def test_rede_local_e_recusada():
    assert not design_imagens._host_publico("http://127.0.0.1:8080/x.png")
    assert not design_imagens._host_publico("http://192.168.0.1/x.png")
    assert not design_imagens._host_publico("file:///C:/x.png")
    novo, passos = asyncio.run(design_imagens.fotos_da_internet('<img data-slot="a-1111" data-prompt="x" src="http://localhost/x.png">'))
    assert "src=" not in novo and "fora da internet pública" in passos[0]


def test_modo_skill_tira_o_link():
    novo = design_imagens.sem_links('<img src="https://images.unsplash.com/photo-1" alt="Pão de queijo"><img src="data:image/png;base64,AA">')
    assert "unsplash" not in novo and 'data-slot="pao-de-queijo-' in novo and "data:image/png;base64,AA" in novo

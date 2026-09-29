import asyncio
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import db, design, design_referencias, mirror

SITE = """<!doctype html><html><head><title>Loja Azul</title><style>
body { margin: 0; background: #0f172a; color: #e2e8f0; font-family: Georgia, serif; }
header { padding: 24px; background: #1e293b; } .hero { padding: 80px 24px; } h1 { font-family: Verdana, sans-serif; font-size: 48px; }
.btn { background: #f97316; color: #fff; padding: 12px 20px; border-radius: 999px; }
</style><script>window.x = 1</script></head><body>
<header><nav><a href="/">Início</a> <a href="javascript:alert(1)">Mau</a></nav></header>
<section class="hero"><h1>Tudo azul</h1><p>Uma loja de exemplo.</p><a class="btn" href="/comprar">Comprar</a>
<img src="/foto.jpg" alt="Vitrine da loja" width="400" height="300"></section>
<footer style="padding:40px">Rodapé da loja</footer></body></html>"""


class _Site(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(SITE.encode())

    def log_message(self, *a):
        pass


def _chromium() -> bool:
    async def abre():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            await (await pw.chromium.launch(headless=True)).close()
    try:
        asyncio.run(abre())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium(), reason="sem Chromium headless")


@pytest.fixture
def site():
    srv = HTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    design._RUNS.clear()


DOC = """<!doctype html><html><head><style>:root { --cor-fundo: #ffffff; --cor-texto: #111111; --cor-primaria: #c75b12;
--fonte-titulo: Georgia, serif; --fonte-texto: system-ui, sans-serif; }</style></head><body><section data-section="a"><h1>Oi</h1></section></body></html>"""


def test_captura_traz_blocos_paleta_e_celular(site):
    refs = asyncio.run(design_referencias.pagina(site))
    pagina, foto, celular = refs
    assert pagina["tipo"] == "pagina" and foto["tipo"] == celular["tipo"] == "imagem" and "celular" in celular["nome"]
    assert [b["rotulo"].split(" ")[0] for b in pagina["blocos"]] == ["header", "section", "footer"]
    assert pagina["paleta"]["fundo"] == "#0f172a" and pagina["paleta"]["destaque"] == "#f97316"
    assert "Verdana" in pagina["paleta"]["fonte_titulo"]

    with db.session() as s:
        c = db.Conversation(kind="design", title="Minha")
        s.add(c)
        s.commit()
        conv = c.id
    design._nova_versao(conv, None, DOC, "inicial")
    r = design.inserir_captura(conv, pagina["captura_id"], [1, 0])
    h = r["projeto"]["html"]
    secoes = re.findall(r'data-section="(captura-[^"]+)"', h)
    assert secoes == ["captura-127-0-0-1-2", "captura-127-0-0-1-1"]
    assert "Tudo azul" in h and "font-size:48px" in h and "<script" not in h.split("</head>")[1]
    assert "javascript:" not in h and 'data-slot="captura-127-0-0-1-2-foto-1"' in h
    assert re.search(r'<img[^>]*data-slot="captura-127-0-0-1-2-foto-1"[^>]*src="data:image/svg', h)   # provisório até gerar
    assert r["projeto"]["total"] == 1 and r["projeto"]["rascunho"]["mudancas"] == 2

    r = design.aplicar_paleta(conv, pagina["paleta"])
    raiz = re.search(r":root\s*\{[^}]*\}", r["projeto"]["html"]).group(0)
    assert "--cor-fundo: #0f172a" in raiz and "--cor-primaria: #f97316" in raiz and "Verdana" in raiz

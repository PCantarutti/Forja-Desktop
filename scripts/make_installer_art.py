"""Gera as imagens do instalador NSIS (installer/sidebar.bmp e installer/header.bmp).

Mesma receita do make_icon.py: o Chromium do Playwright renderiza um HTML com as cores e a fonte do
app (Atkinson Hyperlegible Next, do frontend/node_modules) e o Pillow converte o PNG em BMP 24 bits,
o único formato que o NSIS aceita. Os BMPs ficam versionados em installer/; rode de novo só quando o
visual mudar:  resources/python/python.exe scripts/make_installer_art.py
"""
import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "installer"
FONT = ROOT / "frontend" / "node_modules" / "@fontsource-variable" / "atkinson-hyperlegible-next" / "files" / "atkinson-hyperlegible-next-latin-wght-normal.woff2"
LOGO = ROOT / "frontend" / "public" / "favicon.svg"

# Tokens do frontend/src/index.css (tema padrão).
CSS = f"""
@font-face {{ font-family: Atkinson; src: url("{FONT.as_uri()}") format("woff2"); font-weight: 200 800; }}
* {{ margin: 0; box-sizing: border-box; }}
body {{ font-family: Atkinson, "Segoe UI", sans-serif; color: #ececec; -webkit-font-smoothing: antialiased; }}
.side {{ width: 164px; height: 314px; position: relative; overflow: hidden;
  background: radial-gradient(120% 70% at 50% 0%, #1f2a3d 0%, #0e0e0e 62%); }}
.side .logo {{ position: absolute; top: 58px; left: 42px; width: 80px; height: 80px;
  border-radius: 18px; box-shadow: 0 0 0 1px #2f2f2f, 0 14px 40px rgba(0,0,0,.55); }}
.side .nome {{ position: absolute; top: 158px; width: 100%; text-align: center; font-size: 28px; font-weight: 700; letter-spacing: -.01em; }}
.side .tag {{ position: absolute; top: 196px; width: 100%; text-align: center; font-size: 11.5px; color: #a3a3a3; line-height: 1.35; }}
.side .barra {{ position: absolute; left: 0; bottom: 0; width: 100%; height: 3px; background: #4f8ff7; }}
.head {{ width: 150px; height: 57px; background: #171717; display: flex; align-items: center; justify-content: flex-end; padding-right: 14px; gap: 9px; }}
.head .logo {{ width: 34px; height: 34px; border-radius: 8px; box-shadow: 0 0 0 1px #2f2f2f; }}
.head .nome {{ font-size: 19px; font-weight: 700; }}
"""

SIDE = f"""<div class="side"><img class="logo" src="{LOGO.as_uri()}"><div class="nome">Forja</div>
<div class="tag">Agente de IA<br>no seu PC</div><div class="barra"></div></div>"""
HEAD = f"""<div class="head"><img class="logo" src="{LOGO.as_uri()}"><div class="nome">Forja</div></div>"""


def shot(page, html: str, sel: str, out: Path) -> None:
    page.set_content(f"<!doctype html><meta charset='utf-8'><style>{CSS}</style>{html}")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(300)
    png = page.locator(sel).screenshot()
    Image.open(io.BytesIO(png)).convert("RGB").save(out, "BMP")
    print(f"gerado: {out}")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--allow-file-access-from-files"])
        page = browser.new_page(device_scale_factor=1)
        page.goto(LOGO.parent.as_uri())  # origem file:// para o set_content poder ler a fonte e o SVG
        shot(page, SIDE, ".side", OUT / "sidebar.bmp")
        shot(page, HEAD, ".head", OUT / "header.bmp")
        browser.close()


if __name__ == "__main__":
    main()

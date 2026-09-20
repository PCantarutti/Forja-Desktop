"""Gera build/icon.png (1024x1024) a partir de frontend/public/favicon.svg.

O Chromium do Playwright já vem no bundle, então ele mesmo rasteriza o SVG: nenhuma dependência
nova. O electron-builder converte o PNG em .ico na hora de gerar o instalador.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "frontend" / "public" / "favicon.svg"
OUT = ROOT / "build" / "icon.png"
SIZE = 1024

HTML = """<!doctype html><meta charset="utf-8">
<style>html,body{{margin:0;padding:0;background:transparent}} img{{display:block;width:{size}px;height:{size}px}}</style>
<img src="favicon.svg">"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    page_html = ROOT / "frontend" / "public" / "_icon.html"
    page_html.write_text(HTML.format(size=SIZE), encoding="utf-8")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": SIZE, "height": SIZE})
            page.goto(page_html.as_uri())
            page.wait_for_timeout(300)  # o <img> do SVG precisa carregar antes do print
            page.screenshot(path=str(OUT), omit_background=True)
            browser.close()
    finally:
        page_html.unlink(missing_ok=True)
    print(f"icone gerado: {OUT}")


if __name__ == "__main__":
    main()

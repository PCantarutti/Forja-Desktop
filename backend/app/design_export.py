"""Exportar o design: HTML limpo, PDF (um slide por página num deck) e PNG (slide atual ou página).

PDF e PNG saem de um Chromium headless próprio (o mesmo que documentos.py usa: no Desktop o
navegador integrado é headed e `page.pdf()` só existe headless). O documento entra por
`set_content` e toda requisição é abortada: o design é autocontido, e o que não for não vaza.
"""
from __future__ import annotations

import re

from . import design_html
from .tools import ToolError

FORMATOS = ("html", "pdf", "png")
LARGURAS = {"desktop": 1440, "tablet": 768, "mobile": 375}


def _nome(titulo: str, ext: str, sufixo: str = "") -> str:
    return f"{design_html.slug(titulo) or 'design'}{sufixo}.{ext}"   # o slug corta em 40: sufixo depois


async def exportar(html: str, titulo: str, formato: str, com_fids: bool = False, slide: int = 1,
                   viewport: str = "desktop") -> tuple[bytes, str, str]:
    """(bytes, content-type, nome do arquivo)."""
    if formato not in FORMATOS:
        raise ToolError(f"formato deve ser {', '.join(FORMATOS)}.")
    if not html:
        raise ToolError("Não há documento para exportar.")
    limpo = design_html.limpar_export(html, com_fids)
    if formato == "html":
        return limpo.encode("utf-8"), "text/html; charset=utf-8", _nome(titulo, "html")

    slides = design_html.e_slides(limpo)
    if slides and formato == "pdf":   # impõe 1920x1080 por página, por cima do que o CSS do deck disser
        limpo = re.sub(r"</head>", design_html.IMPRESSAO_SLIDES + "</head>", limpo, count=1, flags=re.I)
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        try:
            navegador = await pw.chromium.launch(headless=True)
        except Exception as e:
            raise ToolError("Não consegui abrir o Chromium para exportar. Ele vem com o Forja instalado; em "
                            "desenvolvimento, rode `python -m playwright install --only-shell chromium`. "
                            f"({e.__class__.__name__}) O HTML exporta sem ele.") from e
        try:
            largura = 1920 if slides else LARGURAS.get(viewport, 1440)
            pagina = await navegador.new_page(viewport={"width": largura, "height": 1080 if slides else 900})
            await pagina.route("**/*", lambda r: r.abort())
            await pagina.set_content(limpo, wait_until="load")
            if formato == "pdf":
                if slides:
                    dados = await pagina.pdf(width="1920px", height="1080px", print_background=True,
                                             prefer_css_page_size=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
                else:
                    dados = await pagina.pdf(format="A4", print_background=True,
                                             margin={"top": "1cm", "bottom": "1cm", "left": "1cm", "right": "1cm"})
                return dados, "application/pdf", _nome(titulo, "pdf")
            if slides:
                alvo = pagina.locator("body > [data-slide]")
                n = await alvo.count()
                if not 1 <= slide <= n:
                    raise ToolError(f"O deck tem {n} slides; não existe o {slide}.")
                dados = await alvo.nth(slide - 1).screenshot()
                return dados, "image/png", _nome(titulo, "png", f"-slide-{slide}")
            # página inteira na largura do viewport (o que transbordar para o lado fica de fora, como na tela)
            altura = await pagina.evaluate("document.documentElement.scrollHeight")
            dados = await pagina.screenshot(full_page=True, clip={"x": 0, "y": 0, "width": largura, "height": altura})
            return dados, "image/png", _nome(titulo, "png", f"-{viewport}")
        finally:
            await navegador.close()

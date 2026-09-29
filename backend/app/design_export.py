"""Exportar o design: HTML limpo, PDF (um slide por página num deck) e PNG (slide atual ou página).

PDF e PNG saem de um Chromium headless próprio (o mesmo que documentos.py usa: no Desktop o
navegador integrado é headed e `page.pdf()` só existe headless). O documento entra por
`set_content` e toda requisição é abortada: o design é autocontido, e o que não for não vaza.
"""
from __future__ import annotations

import re
from pathlib import Path

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


# ------------------------------------------------------------------ handoff para o Agente

README = """# Handoff de design — {titulo}

Gerado pela tela Design do Forja em {quando}. Este pacote é a referência visual; a implementação
é na stack DESTE projeto.

## Arquivos
- `index.html` — o design inteiro, autocontido (abra no navegador para ver).
- `preview.png` — como ele fica em 1440px (ou o deck, slide a slide, no index.html).
- `tokens.css` — os design tokens (cores, fontes, tamanhos, espaços, raios, sombras).
- `img/` — as imagens do design ({n_img} arquivo(s)).

## Estrutura
{estrutura}

## Tokens
```css
{tokens}
```

## Como implementar
1. Leia o projeto antes (framework, pasta de componentes, como o tema/estilos são definidos) e siga o
   padrão que já existe. Não cole o HTML cru se o projeto usa componentes.
2. Traga os tokens para o sistema de tema do projeto (variáveis CSS, tema do Tailwind, theme.ts...),
   com os mesmos nomes ou o equivalente mais próximo, e use-os em vez de valores soltos.
3. Um componente por {unidade} (a lista acima), reaproveitando o que o projeto já tiver.
4. Copie as imagens de `img/` para a pasta de assets do projeto e aponte para elas.
5. Responsivo como no design (confira em 375, 768 e 1440 px) e acessível: contraste, `alt`, foco.
6. No fim, abra a página no navegador e compare com `preview.png` / `index.html`.
"""


def _imagens_em_arquivo(html: str, destino: Path) -> tuple[str, int]:
    import base64

    (destino / "img").mkdir(parents=True, exist_ok=True)
    n = 0

    def um(m: re.Match) -> str:
        nonlocal n
        tag = m.group(0)
        src = re.search(r'\ssrc="data:image/(\w+);base64,([^"]+)"', tag)
        nome = re.search(r'\sdata-slot="([a-z0-9-]+)"', tag)
        if not src:
            return tag
        ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(src.group(1), src.group(1))
        arquivo = f"img/{nome.group(1) if nome else f'imagem-{n + 1}'}.{ext}"
        (destino / arquivo).write_bytes(base64.b64decode(src.group(2)))
        n += 1
        return tag.replace(src.group(0), f' src="{arquivo}"')
    return re.sub(r"<img\b[^>]*>", um, html), n


async def handoff(html: str, titulo: str, pasta: str) -> dict:
    """Pacote para o Agente implementar o design no projeto: <pasta>/design-handoff/<slug>/."""
    from datetime import datetime
    from . import workspace

    if not html:
        raise ToolError("Não há documento para mandar.")
    raiz = workspace.resolve(pasta)
    rel = Path("design-handoff") / (design_html.slug(titulo) or "design")
    destino = raiz / rel
    destino.mkdir(parents=True, exist_ok=True)
    limpo = design_html.limpar_export(html)
    com_arquivos, n_img = _imagens_em_arquivo(limpo, destino)
    (destino / "index.html").write_text(limpo, "utf-8")   # autocontido: abre sozinho
    (destino / "tokens.css").write_text(design_html.root_css(html) + "\n", "utf-8")
    slides = design_html.e_slides(html)
    secs = [e["attrs"]["data-section"] for e in design_html.secoes(html)]
    try:   # a prévia ajuda o Agente a comparar; sem Chromium o pacote sai sem ela
        png, _, _ = await exportar(html, titulo, "png", slide=1)
        (destino / "preview.png").write_bytes(png)
    except ToolError:
        pass
    (destino / "README.md").write_text(README.format(
        titulo=titulo, quando=datetime.now().strftime("%d/%m/%Y %H:%M"), n_img=n_img,
        estrutura="\n".join(f"{i}. `{s}`" for i, s in enumerate(secs, 1)) or "(sem seções marcadas)",
        tokens=design_html.root_css(html), unidade="slide" if slides else "seção"), "utf-8")
    if n_img:   # versão com as imagens em arquivo, para quem preferir não ter base64 no HTML
        (destino / "index.arquivos.html").write_text(com_arquivos, "utf-8")
    rel_txt = rel.as_posix()
    return {"pasta": workspace.to_host(raiz), "rel": rel_txt, "prompt": (
        f"Implemente neste projeto o design que está em `{rel_txt}/` (gerado na tela Design). "
        f"Comece lendo `{rel_txt}/README.md`: ele diz os arquivos, a estrutura, os tokens e como implementar. "
        "Siga a stack e os padrões que este projeto já usa, traga os tokens para o tema do projeto e, no fim, "
        f"confira no navegador comparando com `{rel_txt}/preview.png`.")}

"""Referências para o Design: documento (o texto vira conteúdo do pedido) e página da web capturada
(texto, estrutura, cores e fontes computadas + um screenshot, que vai como imagem para modelo com
visão). Imagem solta não passa por aqui: o navegador já manda o data URI reduzido.

ponytail: a captura abre um Chromium headless por pedido (~1 s a mais); pool se virar rotina.
"""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from . import documentos
from .tools import ToolError

TEXTO_MAX = 12_000
EXTENSOES = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md", ".html", ".htm", ".json"}

_COLETA = """() => {
  const conta = (lista) => Object.entries(lista.reduce((a, v) => (v && (a[v] = (a[v] || 0) + 1), a), {}))
    .sort((a, b) => b[1] - a[1]).slice(0, 8).map(([v, n]) => `${v} (×${n})`);
  const els = [...document.querySelectorAll('body *')].slice(0, 3000);
  const cs = els.map((e) => getComputedStyle(e));
  const botoes = [...document.querySelectorAll('a, button, [role=button]')].slice(0, 200).map((e) => getComputedStyle(e));
  const b = getComputedStyle(document.body);
  return {
    titulo: document.title,
    titulos: [...document.querySelectorAll('h1, h2, h3')].slice(0, 30).map((h) => `${h.tagName}: ${h.innerText.trim().slice(0, 120)}`),
    texto: (document.querySelector('main') || document.body).innerText.slice(0, 6000),
    fundo: b.backgroundColor, cor: b.color, fonte: b.fontFamily,
    cores: conta(cs.map((c) => c.color)), fundos: conta(cs.map((c) => c.backgroundColor).filter((v) => v !== 'rgba(0, 0, 0, 0)')),
    fontes: conta(cs.map((c) => c.fontFamily)), tamanhos: conta(cs.map((c) => c.fontSize)),
    raios: conta(cs.map((c) => c.borderRadius).filter((v) => v !== '0px')), botoes: conta(botoes.map((c) => c.backgroundColor)),
  };
}"""


def documento(nome: str, dados: bytes) -> dict:
    ext = Path(nome).suffix.lower()
    if ext not in EXTENSOES:
        raise ToolError(f"Formato {ext or '(sem extensão)'} não é lido como referência. Use PDF, DOCX, PPTX, XLSX, CSV, TXT ou MD.")
    if ext in (".txt", ".md", ".html", ".htm", ".json"):
        texto = dados.decode("utf-8", "ignore")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            arq = Path(tmp) / f"ref{ext}"
            arq.write_bytes(dados)
            texto = documentos.extrair(arq) or ""
    texto = texto.strip()
    if not texto:
        raise ToolError(f"Não achei texto em {nome} (PDF escaneado? Anexe como imagem).")
    return {"tipo": "documento", "nome": nome, "texto": texto[:TEXTO_MAX], "tamanho": len(texto)}


async def pagina(url: str) -> list[dict]:
    """[referência de texto (estrutura e estilo), referência de imagem (screenshot 1440×900)]."""
    url = (url or "").strip()
    if not urlparse(url).scheme:
        url = "https://" + url
    if urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
        raise ToolError("Endereço inválido: use http:// ou https://.")
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        try:
            navegador = await pw.chromium.launch(headless=True)
        except Exception as e:
            raise ToolError(f"Não consegui abrir o Chromium para capturar a página ({e.__class__.__name__}).") from e
        try:
            pg = await navegador.new_page(viewport={"width": 1440, "height": 900})
            try:
                await pg.goto(url, wait_until="load", timeout=25_000)
            except Exception as e:
                raise ToolError(f"A página não abriu: {e.__class__.__name__}.") from e
            await pg.wait_for_timeout(800)   # fontes e o primeiro pintar
            d = await pg.evaluate(_COLETA)
            foto = await pg.screenshot(type="jpeg", quality=70)
        finally:
            await navegador.close()
    lin = lambda xs: "; ".join(xs) or "—"   # noqa: E731
    texto = (f"URL: {url}\nTítulo: {d['titulo']}\n"
             f"Estilo computado: fundo {d['fundo']}, texto {d['cor']}, fonte {d['fonte']}\n"
             f"Cores de texto: {lin(d['cores'])}\nFundos: {lin(d['fundos'])}\nBotões/links: {lin(d['botoes'])}\n"
             f"Fontes: {lin(d['fontes'])}\nTamanhos: {lin(d['tamanhos'])}\nRaios: {lin(d['raios'])}\n"
             f"Estrutura:\n" + "\n".join(d["titulos"]) + f"\n\nTexto da página:\n{d['texto']}")
    nome = (d["titulo"] or urlparse(url).netloc)[:100]
    return [{"tipo": "pagina", "nome": nome, "texto": texto[:TEXTO_MAX]},
            {"tipo": "imagem", "nome": f"captura de {urlparse(url).netloc}",
             "data": "data:image/jpeg;base64," + base64.b64encode(foto).decode()}]

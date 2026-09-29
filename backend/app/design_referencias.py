"""Referências para o Design: documento (o texto vira conteúdo do pedido) e página da web capturada
(texto, estrutura, cores e fontes computadas + um screenshot, que vai como imagem para modelo com
visão). Imagem solta não passa por aqui: o navegador já manda o data URI reduzido.

Da página capturada também saem os BLOCOS (seções de topo, cada uma copiada com o estilo computado
posto em style="" — sem script, sem classe do site, foto vira slot de imagem do Forja), para o
usuário escolher e trazer para o design, e a PALETA (fundo, texto, destaque, fontes) para os tokens.

ponytail: a captura abre um Chromium headless por pedido (~1 s a mais); pool se virar rotina. Os
blocos ficam na memória (_CAPTURAS) até o backend reiniciar.
"""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path
import os
import re
import secrets
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


ALTURA_MAX = 3200     # captura da página inteira até aqui (o resto sai caro para o modelo e para a tela)
_CAPTURAS: dict[str, dict] = {}   # captura_id -> {url, blocos: [html]}

# Roda na página capturada: blocos de topo (desce por quem embrulha a página inteira) já com o estilo
# computado em style="" e a paleta. Devolve {blocos: [{rotulo, caixa, html}], paleta}.
_BLOCOS = r"""() => {
  const PROPS = ["display", "flex-direction", "flex-wrap", "justify-content", "align-items", "gap", "grid-template-columns",
    "padding", "margin", "max-width", "color", "background-color", "font-family", "font-size", "font-weight", "line-height",
    "letter-spacing", "text-align", "text-transform", "text-decoration", "border", "border-radius", "box-shadow", "list-style",
    "float", "clear", "column-count", "column-gap", "flex-grow", "flex-basis", "align-self", "vertical-align", "white-space"];
  const MIDIA = ["width", "height", "object-fit"];
  const INUTIL = new Set(["none", "normal", "0px", "auto", "rgba(0, 0, 0, 0)", "0px none rgb(0, 0, 0)", "start", "nowrap", "row", "stretch",
    "flex-start", "visible", "baseline", "disc outside none"]);
  const FORA = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "IFRAME", "VIDEO", "AUDIO", "CANVAS", "OBJECT", "EMBED", "TEMPLATE", "LINK", "META"]);
  const hex = (c) => { const m = c.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/); if (!m || m[4] === "0") return "";
    return "#" + [m[1], m[2], m[3]].map((n) => (+n).toString(16).padStart(2, "0")).join(""); };
  let nFoto = 0, nNos = 0;
  const copia = (el) => {
    if (FORA.has(el.tagName) || nNos > 1500) return null;
    const c = getComputedStyle(el);
    if (c.display === "none" || c.visibility === "hidden") return null;
    nNos++;
    const tag = el.tagName.toLowerCase();
    if (tag === "img" || tag === "picture") {
      const img = tag === "img" ? el : el.querySelector("img");
      if (!img) return null;
      const r = img.getBoundingClientRect(), n = document.createElement("img");
      n.setAttribute("data-slot", `captura-foto-${++nFoto}`);
      n.setAttribute("data-prompt", (img.alt || "foto do site de referência").slice(0, 200));
      n.setAttribute("alt", img.alt || "");
      n.setAttribute("width", String(Math.round(r.width) || 400)); n.setAttribute("height", String(Math.round(r.height) || 300));
      n.setAttribute("style", `object-fit:${getComputedStyle(img).objectFit};max-width:100%;height:auto;border-radius:${getComputedStyle(img).borderRadius}`);
      return n;
    }
    const n = tag === "svg" ? el.cloneNode(true) : document.createElement(tag);
    if (tag === "svg") { n.querySelectorAll("script").forEach((x) => x.remove()); [...n.attributes].filter((a) => a.name.startsWith("on")).forEach((a) => n.removeAttribute(a.name)); }
    for (const a of ["href", "alt", "title", "type", "placeholder", "value", "aria-label", "role"]) if (el.hasAttribute(a) && tag !== "svg") n.setAttribute(a, el.getAttribute(a));
    if (n.hasAttribute("href") && /^\s*javascript:/i.test(n.getAttribute("href"))) n.setAttribute("href", "#");
    const st = [...PROPS, ...(tag === "svg" || tag === "button" || tag === "input" ? MIDIA : [])]
      .map((k) => [k, c.getPropertyValue(k)]).filter(([k, v]) => v && !INUTIL.has(v) && !(k === "flex-grow" && v === "0")).map(([k, v]) => `${k}:${v}`);
    // coluna (mais estreita que o pai): a largura vai em % do pai, para o layout não quebrar sem fixar px
    const pai = el.parentElement?.getBoundingClientRect().width || 0, w = el.getBoundingClientRect().width;
    if (tag !== "svg" && !c.display.startsWith("inline") && pai > 0 && w > 0 && w < pai * 0.95) st.push(`width:${(w / pai * 100).toFixed(1)}%`);
    // colunas feitas com filhos em position:absolute lado a lado (position não é copiado): o pai vira flex
    const kids = [...el.children].filter((x) => getComputedStyle(x).display !== "none");
    const abs = kids.filter((x) => /absolute|fixed/.test(getComputedStyle(x).position));
    const topos = abs.map((x) => x.getBoundingClientRect().top);
    if (abs.length >= 2 && abs.length >= kids.length * 0.6 && topos.filter((t) => t - Math.min(...topos) < 40).length >= 2)   // ≥2 na mesma linha
      st.push("display:flex", "flex-wrap:wrap", "align-items:flex-start");
    if (st.length) n.setAttribute("style", st.join(";").replace(/"/g, "'"));
    if (tag !== "svg") for (const f of el.childNodes) {
      if (f.nodeType === 3) n.appendChild(document.createTextNode(f.textContent));
      else if (f.nodeType === 1) { const x = copia(f); if (x) n.appendChild(x); }
    }
    return n;
  };
  // topo: filhos do body; quem cobre quase a página inteira é embrulho — desce nele
  let nivel = [...document.body.children], altura = document.documentElement.scrollHeight;
  for (let k = 0; k < 4; k++) {
    const vis = nivel.filter((e) => e.getBoundingClientRect().height > 40 && !FORA.has(e.tagName));
    const embrulho = vis.find((e) => e.getBoundingClientRect().height > altura * 0.85 && e.children.length > 1);
    if (!embrulho || vis.length > 2) { nivel = vis; break; }
    nivel = [...embrulho.children];
  }
  const blocos = [];
  for (const el of nivel) {
    const r = el.getBoundingClientRect();
    if (r.height < 60 || r.width < innerWidth * 0.5 || r.top + scrollY > 3200 || blocos.length >= 14) continue;
    nNos = 0;
    const html = copia(el);
    if (!html || html.outerHTML.length > 250000) continue;   // bloco gigante: cortar no meio quebraria o HTML
    const titulo = el.querySelector("h1, h2, h3");
    blocos.push({ rotulo: (el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (titulo ? ` · ${titulo.innerText.trim().slice(0, 50)}` : "")),
                  caixa: [Math.round(r.left), Math.round(r.top + scrollY), Math.round(r.width), Math.round(r.height)],
                  html: html.outerHTML });
  }
  const conta = (xs) => Object.entries(xs.reduce((a, v) => (v && (a[v] = (a[v] || 0) + 1), a), {})).sort((a, b) => b[1] - a[1]).map(([v]) => v);
  const b = getComputedStyle(document.body), h = document.querySelector("h1, h2");
  const fundoPagina = hex(b.backgroundColor) || hex(getComputedStyle(document.documentElement).backgroundColor) || "#ffffff";
  // destaque: a cor de botão/link mais comum que tem cor de verdade (cinza de navegação não é marca)
  const saturacao = (h) => { const [r, g, b2] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
    const mx = Math.max(r, g, b2), mn = Math.min(r, g, b2); return mx === 0 ? 0 : (mx - mn) / mx; };
  const botoes = [...document.querySelectorAll("a, button, [role=button]")].slice(0, 300)
    .flatMap((e) => { const c = getComputedStyle(e), f = hex(c.backgroundColor); return [f, f, f, hex(c.color)]; })   // fundo de botão pesa mais
    .filter((c) => c && c !== fundoPagina);
  const vivas = conta(botoes.filter((c) => saturacao(c) > 0.35));
  return { blocos, paleta: { fundo: fundoPagina, texto: hex(b.color) || "#111111", destaque: vivas[0] || conta(botoes)[0] || "",
    fonte_texto: b.fontFamily, fonte_titulo: h ? getComputedStyle(h).fontFamily : b.fontFamily } };
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
            b = await pg.evaluate(_BLOCOS)
            altura = min(await pg.evaluate("() => document.documentElement.scrollHeight"), ALTURA_MAX)
            foto = await pg.screenshot(type="jpeg", quality=65, full_page=True, clip={"x": 0, "y": 0, "width": 1440, "height": altura})
            await pg.set_viewport_size({"width": 375, "height": 812})   # a mesma página no celular
            await pg.wait_for_timeout(500)
            foto_cel = await pg.screenshot(type="jpeg", quality=65)
        finally:
            await navegador.close()
    lin = lambda xs: "; ".join(xs) or "—"   # noqa: E731
    texto = (f"URL: {url}\nTítulo: {d['titulo']}\n"
             f"Estilo computado: fundo {d['fundo']}, texto {d['cor']}, fonte {d['fonte']}\n"
             f"Cores de texto: {lin(d['cores'])}\nFundos: {lin(d['fundos'])}\nBotões/links: {lin(d['botoes'])}\n"
             f"Fontes: {lin(d['fontes'])}\nTamanhos: {lin(d['tamanhos'])}\nRaios: {lin(d['raios'])}\n"
             f"Estrutura:\n" + "\n".join(d["titulos"]) + f"\n\nTexto da página:\n{d['texto']}")
    nome = (d["titulo"] or urlparse(url).netloc)[:100]
    cid = secrets.token_urlsafe(9)
    _CAPTURAS[cid] = {"url": url, "blocos": [x["html"] for x in b["blocos"]]}
    while len(_CAPTURAS) > 20:   # só as recentes
        _CAPTURAS.pop(next(iter(_CAPTURAS)))
    host = urlparse(url).netloc
    return [{"tipo": "pagina", "nome": nome, "texto": texto[:TEXTO_MAX], "captura_id": cid, "paleta": b["paleta"],
             "blocos": [{"rotulo": x["rotulo"], "caixa": x["caixa"]} for x in b["blocos"]], "largura": 1440, "altura": altura},
            {"tipo": "imagem", "nome": f"captura de {host}", "captura_id": cid,
             "data": "data:image/jpeg;base64," + base64.b64encode(foto).decode()},
            {"tipo": "imagem", "nome": f"{host} no celular", "data": "data:image/jpeg;base64," + base64.b64encode(foto_cel).decode()}]


def blocos(captura_id: str, indices: list[int]) -> tuple[str, list[str]]:
    """(HTML das seções escolhidas, já embrulhadas em <section data-section>, e os nomes delas)."""
    c = _CAPTURAS.get(captura_id)
    if not c:
        raise ToolError("Essa captura não está mais na memória (o Forja reiniciou): capture a página de novo.")
    if not indices or any(not isinstance(i, int) or not 0 <= i < len(c["blocos"]) for i in indices):
        raise ToolError("Escolha pelo menos um bloco da captura.")
    host = re.sub(r"[^a-z0-9]+", "-", (urlparse(c["url"]).hostname or "").lower().removeprefix("www.")).strip("-")[:24] or "site"
    partes, nomes = [], []
    for i in dict.fromkeys(indices):
        nome = f"captura-{host}-{i + 1}"
        # o slot de foto ganha o nome do bloco (senão dois blocos brigariam pelo mesmo arquivo)
        html = re.sub(r'data-slot="captura-foto-(\d+)"', lambda m: f'data-slot="{nome}-foto-{m.group(1)}"', c["blocos"][i])
        partes.append(f'<section data-section="{nome}">\n{html}\n</section>')
        nomes.append(nome)
    return "\n".join(partes), nomes


# ------------------------------------------------------------------ pasta de um projeto seu
# "Anexar › Pasta": a IA lê o programa que já existe (telas, componentes, estilo) e faz o pedido em
# cima dele. Não vai o projeto inteiro: a árvore e os arquivos que mais dizem sobre a interface, até
# um orçamento — o resto do contexto é do pedido e da página.

ORCAMENTO_PASTA = 30_000
MAX_POR_ARQUIVO = 6_000
_EXT_UI = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".tsx", ".jsx", ".vue", ".svelte", ".astro", ".ts", ".js", ".md", ".json"}
_IGNORAR = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", "__pycache__", ".venv", "venv", "vendor", "coverage",
            "out", ".forja", "target", "bin", "obj", ".turbo", ".cache"}


def _peso(rel: str) -> int:
    """Quanto um arquivo diz sobre a interface (maior = entra antes)."""
    nome, low = rel.rsplit("/", 1)[-1].lower(), rel.lower()
    if nome in ("design.md", "design-system.md"):
        return 100
    if nome in ("index.html", "tailwind.config.js", "tailwind.config.ts", "globals.css", "index.css", "app.css", "styles.css", "global.css"):
        return 90
    if re.match(r"(app|main|layout|root)\.(tsx|jsx|vue|svelte|astro|ts|js)$", nome):
        return 80
    if nome.endswith((".html", ".htm")):
        return 70
    if nome.endswith((".css", ".scss", ".sass", ".less")):
        return 65
    if re.search(r"/(pages|routes|views|screens|app)/", "/" + low):
        return 60
    if re.search(r"/(components|ui|layouts?)/", "/" + low) and nome.endswith((".tsx", ".jsx", ".vue", ".svelte", ".astro")):
        return 50
    if nome == "readme.md":
        return 40
    if nome.endswith((".tsx", ".jsx", ".vue", ".svelte", ".astro")):
        return 30
    return 0   # .ts/.js/.json soltos: só na árvore


def pasta_projeto(pasta: str) -> dict:
    """Referência de texto com a árvore e o código da interface de uma pasta do computador."""
    from . import design_sistema, workspace
    try:
        raiz = workspace.resolve(pasta)
    except workspace.WorkspaceError as e:
        raise ToolError(f"Não achei a pasta: {e}") from e
    if not raiz.is_dir():
        raise ToolError(f"Não é uma pasta: {pasta}")
    arquivos: list[str] = []
    for atual, pastas, nomes in os.walk(raiz):
        pastas[:] = sorted(p for p in pastas if p not in _IGNORAR and not p.startswith("."))
        for n in sorted(nomes):
            if Path(n).suffix.lower() in _EXT_UI and n not in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"):
                arquivos.append(str((Path(atual) / n).relative_to(raiz)).replace("\\", "/"))
        if len(arquivos) > 3000:
            break
    if not arquivos:
        raise ToolError(f"Não achei arquivos de interface (HTML, CSS, componentes) em {raiz}.")
    partes = [f"Pasta: {raiz.name} ({len(arquivos)} arquivos de interface)",
              "Árvore (parcial):\n" + "\n".join(f"  {a}" for a in arquivos[:150])]
    try:
        partes.append("Estilo que se repete no código:\n" + design_sistema.resumo(raiz)[:3500])
    except ToolError:
        pass
    resto = ORCAMENTO_PASTA - sum(len(p) for p in partes)
    lidos = []
    for rel in sorted((a for a in arquivos if _peso(a) > 0), key=lambda a: (-_peso(a), a.count("/"), a)):
        if resto < 800:
            break
        try:
            t = (raiz / rel).read_text("utf-8", "ignore")
        except OSError:
            continue
        t = re.sub(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=]{200,}", "data:…", t)   # foto embutida não ajuda o modelo
        trecho = t[:min(MAX_POR_ARQUIVO, resto)]
        partes.append(f"--- {rel}{' (início)' if len(t) > len(trecho) else ''} ---\n{trecho}")
        lidos.append(rel)
        resto -= len(trecho) + len(rel) + 12
    return {"tipo": "pasta", "nome": raiz.name, "texto": "\n\n".join(partes), "arquivos": lidos, "pasta": str(raiz)}

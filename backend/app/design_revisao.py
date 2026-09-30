"""Revisão visual do design: a página aberta de verdade em três larguras, num Chromium headless.

Duas camadas:
1. medição (sem IA): um script na página acha o que dá para medir — elemento saindo da tela
   (rolagem horizontal), texto cortado por overflow, textos um por cima do outro, botão/link pequeno
   demais para o dedo e letra miúda no celular;
2. olhar (com IA, se o modelo lê imagem): as capturas vão ao modelo com a lista de elementos e ele
   aponta o que está visivelmente errado. Modelo sem visão só não faz a segunda parte.

Os problemas voltam com o data-fid do elemento e a largura; a tela pode mandar cada um para a fila
de comentários.
"""
from __future__ import annotations

import base64
import json

from . import config, design_html, llm
from .parsing import split_think
from .tools import ToolError

LARGURAS = {"desktop": (1440, 900), "tablet": (768, 1024), "mobile": (375, 812)}
ALTURA_CAPTURA = 2400   # a captura para o modelo pega o começo da página (o resto sai caro em token)
MAX_POR_LARGURA = 12

# Roda na página (via page.evaluate). Devolve {problemas: [...], elementos: [...]}.
MEDIR = r"""
(celular) => {
  const W = innerWidth, out = [], els = [];
  const vivo = (el) => { const c = getComputedStyle(el), r = el.getBoundingClientRect();
    return c.display !== "none" && c.visibility !== "hidden" && +c.opacity > 0.05 && r.width > 0 && r.height > 0; };
  const rot = (el) => el.tagName.toLowerCase() + (el.classList[0] ? "." + [...el.classList].find((c) => !c.startsWith("fx-")) : "");
  const texto = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
  const todos = [...document.querySelectorAll("body [data-fid]")].filter(vivo);
  const add = (el, tipo, detalhe) => out.push({ fid: el.getAttribute("data-fid"), tipo, detalhe, rotulo: rot(el) });
  // 1. saindo da tela: o mais de fora (os filhos de quem já saiu não contam de novo)
  if (document.documentElement.scrollWidth > W + 1) {
    const fora = todos.filter((el) => el.getBoundingClientRect().right > W + 1);
    fora.filter((el) => !fora.some((o) => o !== el && o.contains(el))).slice(0, 3)
      .forEach((el) => add(el, "fora da tela", `Passa ${Math.round(el.getBoundingClientRect().right - W)}px da largura e cria rolagem para o lado.`));
  }
  // 2. texto cortado por overflow (reticências de propósito não contam)
  for (const el of todos) {
    const c = getComputedStyle(el);
    const esconde = /hidden|clip/.test(c.overflowX + c.overflowY);
    if (esconde && c.textOverflow !== "ellipsis" && el.textContent.trim() && (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2)
        && el.clientHeight > 0)
      add(el, "texto cortado", "O conteúdo não cabe na caixa e some (overflow escondido).");
  }
  // 3. textos sobrepostos: folhas com texto cujas caixas se cruzam bastante
  const folhas = todos.filter(texto).slice(0, 400);
  const caixas = folhas.map((el) => el.getBoundingClientRect());
  for (let i = 0; i < folhas.length; i++)
    for (let j = i + 1; j < folhas.length; j++) {
      const a = caixas[i], b = caixas[j];
      if (folhas[i].contains(folhas[j]) || folhas[j].contains(folhas[i])) continue;
      const x = Math.min(a.right, b.right) - Math.max(a.left, b.left), y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (x > 4 && y > 4 && x * y > 0.3 * Math.min(a.width * a.height, b.width * b.height)) {
        add(folhas[i], "sobreposto", `O texto fica por cima de ${rot(folhas[j])} (${Math.round(x)}×${Math.round(y)}px).`);
        break;
      }
    }
  if (celular) {
    // 4. alvo de toque pequeno e 5. letra miúda
    for (const el of todos) {
      const r = el.getBoundingClientRect();
      if (el.matches("a[href], button, input, select, [role=button]") && (r.width < 32 || r.height < 32))
        add(el, "toque pequeno", `Alvo de toque de ${Math.round(r.width)}×${Math.round(r.height)}px; o mínimo confortável é 44×44.`);
      else if (texto(el) && parseFloat(getComputedStyle(el).fontSize) < 12)
        add(el, "letra miúda", `Texto com ${getComputedStyle(el).fontSize} no celular; use pelo menos 14px.`);
    }
  }
  // elementos para o modelo: os que têm texto ou são mídia/controle, com a caixa
  for (const el of todos) {
    if (!(texto(el) || el.matches("img, svg, button, a, input, section, header, footer, nav"))) continue;
    const r = el.getBoundingClientRect();
    if (r.top > 2400) continue;
    els.push({ fid: el.getAttribute("data-fid"), el: rot(el), texto: el.textContent.trim().slice(0, 40),
               caixa: [Math.round(r.left), Math.round(r.top + scrollY), Math.round(r.width), Math.round(r.height)] });
  }
  const vistos = new Set();
  return { problemas: out.filter((p) => { const k = p.fid + p.tipo; if (vistos.has(k)) return false; vistos.add(k); return true; }),
           elementos: els.slice(0, 250) };
}
"""


async def medir(html: str, com_capturas: bool) -> dict:
    """{largura: {problemas, elementos, captura (data URL JPEG) | None}} — com os data-fid do canvas."""
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        try:
            navegador = await pw.chromium.launch(headless=True)
        except Exception as e:
            raise ToolError(f"Não consegui abrir o Chromium para a revisão visual ({e.__class__.__name__}).") from e
        try:
            out = {}
            for nome, (w, h) in LARGURAS.items():
                pagina = await navegador.new_page(viewport={"width": w, "height": h})
                await pagina.route("**/*", lambda r: r.abort())   # o design é autocontido; nada sai
                await pagina.set_content(html, wait_until="load")
                r = await pagina.evaluate(MEDIR, nome == "mobile")
                captura = None
                if com_capturas:
                    total = await pagina.evaluate("() => document.documentElement.scrollHeight")
                    png = await pagina.screenshot(type="jpeg", quality=60, full_page=True,
                                                  clip={"x": 0, "y": 0, "width": w, "height": min(total, ALTURA_CAPTURA)})
                    captura = "data:image/jpeg;base64," + base64.b64encode(png).decode()
                out[nome] = {"problemas": r["problemas"][:MAX_POR_LARGURA], "elementos": r["elementos"], "captura": captura}
                await pagina.close()
            return out
        finally:
            await navegador.close()


async def revisar(html: str, provider: str = "", model: str = "", esforco: str = "baixo") -> dict:
    """Problemas [{fid, largura, tipo, detalhe, rotulo, fonte: medido|modelo}] + se o modelo olhou."""
    if not html:
        raise ToolError("Não há documento para revisar.")
    medido = await medir(html, com_capturas=bool(model))
    problemas = [{**p, "largura": nome, "fonte": "medido"} for nome, m in medido.items() for p in m["problemas"]]
    visao, aviso, mensagem = False, "", ""
    if model:
        fids = {e["fid"] for m in medido.values() for e in m["elementos"]}
        texto = ("Problemas já medidos:\n" + ("\n".join(f"- [{p['largura']}] {p['rotulo']} ({p['fid']}): {p['detalhe']}" for p in problemas) or "(nenhum)")
                 + "\n\nElementos visíveis por largura (fid, elemento, texto, caixa x,y,w,h):\n"
                 + json.dumps({n: m["elementos"][:120] for n, m in medido.items()}, ensure_ascii=False))
        conteudo = [{"type": "text", "text": texto}]
        for nome, m in medido.items():
            conteudo += [{"type": "text", "text": f"Captura {nome} ({LARGURAS[nome][0]}px):"},
                         {"type": "image_url", "image_url": {"url": m["captura"]}}]
        mensagens = [{"role": "system", "content": _prompt()}, {"role": "user", "content": conteudo}]
        try:
            resposta = ""
            async for kind, val in llm.chat_stream(provider, model, mensagens, None, config.NUM_CTX, esforco):
                if kind == "content":
                    resposta += val
            d = design_html.ler_json(split_think(resposta)[1])
            mensagem = str(d.get("mensagem") or "")[:400]
            for p in (d.get("problemas") if isinstance(d.get("problemas"), list) else [])[:6]:
                if not isinstance(p, dict) or str(p.get("fid")) not in fids or not str(p.get("texto") or "").strip():
                    continue
                largura = p.get("largura") if p.get("largura") in LARGURAS else "desktop"
                problemas.append({"fid": str(p["fid"]), "largura": largura, "tipo": "visual", "detalhe": str(p["texto"]).strip()[:400],
                                  "rotulo": next((e["el"] for e in medido[largura]["elementos"] if e["fid"] == p["fid"]), ""), "fonte": "modelo"})
            visao = True
        except llm.LLMError as e:
            aviso = (f"O modelo {model} não lê imagem: a revisão ficou só com o que dá para medir."
                     if "image" in str(e).lower() or "vision" in str(e).lower() else f"O modelo falhou ({e}); ficou só a medição.")
        except ValueError:
            aviso = f"O modelo {model} não devolveu JSON; ficou só a medição."
    return {"problemas": problemas, "visao": visao, "aviso": aviso, "mensagem": mensagem}


def _prompt() -> str:
    from .design import prompt
    return prompt("revisao")


# ------------------------------------------------------------------ autorrevisão (depois de gerar)
# Como o Agente, que abre a página e olha antes de entregar: cada seção vira uma captura e um resumo
# da geometria (o que tem nela, onde, em quantas linhas). Modelo sem visão lê só a geometria — logo
# quebrando em 2 linhas, números empilhados, carrossel com um item só aparecem nela também.
SECOES = r"""
() => {
  const vivo = (el) => { const c = getComputedStyle(el), r = el.getBoundingClientRect();
    return c.display !== "none" && c.visibility !== "hidden" && +c.opacity > 0.05 && r.width > 0 && r.height > 0; };
  const rot = (el) => el.tagName.toLowerCase() + (el.classList[0] ? "." + [...el.classList].find((c) => !c.startsWith("fx-")) : "");
  const proprio = (el) => [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ").trim();
  return [...document.querySelectorAll("body > [data-section]")].filter(vivo).map((sec) => {
    const b = sec.getBoundingClientRect(), y0 = b.top + scrollY;
    const itens = [...sec.querySelectorAll("*")].filter((el) => vivo(el) && (proprio(el) || el.matches("img, svg, button, a, input, details, [data-slot]")))
      .slice(0, 36).map((el) => {
        const r = el.getBoundingClientRect(), c = getComputedStyle(el);
        const lh = parseFloat(c.lineHeight) || parseFloat(c.fontSize) * 1.3;
        const miolo = r.height - parseFloat(c.paddingTop) - parseFloat(c.paddingBottom) - parseFloat(c.borderTopWidth) - parseFloat(c.borderBottomWidth);
        const t = proprio(el), x = Math.round(r.left - b.left);
        return { el: rot(el), texto: t.slice(0, 32), x, y: Math.round(r.top + scrollY - y0),
                 w: Math.round(r.width), h: Math.round(r.height), linhas: t ? Math.max(1, Math.round(miolo / lh)) : 0,
                 slot: el.hasAttribute("data-slot") || undefined, fora: (x > b.width || x + r.width < 0) || undefined };
      });
    return { nome: sec.getAttribute("data-section"), fid: sec.getAttribute("data-fid"),
             altura: Math.round(b.height), largura: Math.round(b.width), itens };
  });
}
"""
LARGURAS_AUTO = {"desktop": 1440, "mobile": 375}


async def por_secao(html: str, capturas: bool = True) -> dict:
    """{desktop: [{nome, fid, altura, itens, captura?}], mobile: [...]}: a página pronta, seção por seção."""
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        try:
            navegador = await pw.chromium.launch(headless=True)
        except Exception as e:
            raise ToolError(f"Não consegui abrir o Chromium para olhar a página ({e.__class__.__name__}).") from e
        try:
            out = {}
            for nome, w in LARGURAS_AUTO.items():
                pagina = await navegador.new_page(viewport={"width": w, "height": 900}, device_scale_factor=0.5 if nome == "desktop" else 1)
                await pagina.route("**/*", lambda r: r.abort())
                await pagina.set_content(html, wait_until="load")
                # animação parada no começo: a captura mostra o layout, não um quadro qualquer do carrossel
                await pagina.add_style_tag(content="*,*::before,*::after{animation-play-state:paused!important;animation-delay:0s!important;transition:none!important}")
                secs = await pagina.evaluate(SECOES)
                if capturas and nome == "desktop":
                    for sec in secs[:12]:
                        el = pagina.locator(f'[data-fid="{sec["fid"]}"]').first
                        try:
                            jpg = await el.screenshot(type="jpeg", quality=55, timeout=8000)
                            sec["captura"] = "data:image/jpeg;base64," + base64.b64encode(jpg).decode()
                        except Exception:
                            pass
                out[nome] = secs
                await pagina.close()
            return out
        finally:
            await navegador.close()


def geometria(medido: dict) -> str:
    """O resumo em texto que vai ao modelo (com ou sem as capturas)."""
    linhas = []
    for largura, secs in medido.items():
        linhas.append(f"== {largura} ({LARGURAS_AUTO[largura]}px)")
        for sec in secs:
            linhas.append(f"[{sec['nome']}] {sec['largura']}×{sec['altura']}px")
            for i in sec["itens"]:
                extra = f' "{i["texto"]}"' if i["texto"] else ""
                extra += f" {i['linhas']} linha(s)" if i["linhas"] > 1 else ""
                extra += " (slot de imagem)" if i.get("slot") else ""
                extra += " (FORA da área visível)" if i.get("fora") else ""
                extra += " (COLADO NA BORDA da tela, sem margem)" if i["x"] < 8 and i["texto"] and sec["largura"] > 600 else ""
                linhas.append(f"  {i['el']} x{i['x']} y{i['y']} {i['w']}×{i['h']}{extra}")
    return "\n".join(linhas)

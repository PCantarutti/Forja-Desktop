// Canvas do Design: o que é injetado no iframe na renderização (CSP + script de inspeção, nunca
// salvos na fonte) e o protocolo postMessage entre o app e o iframe.
//
// O iframe roda com sandbox="allow-scripts" sem allow-same-origin: origem opaca ("null"), então a
// origem não prova nada. Quem vale é `e.source` (a janela do iframe / o parent) + a forma da mensagem.

export type Rect = { x: number; y: number; w: number; h: number };
export type NoCaminho = { fid: string; tag: string; cls: string; sec: string };   // sec = data-section
export type Item = { fid: string; tag: string; cls: string };
export type Modo = "view" | "inspect" | "editText";
export type Pin = { fid: string; n: number };

/** iframe → app. `select` traz o principal (último clicado) e a seleção inteira em `itens`. */
export type DoCanvas =
  | { type: "ready" }
  | { type: "hover"; fid: string | null; rect: Rect | null }
  | { type: "select"; fid: string | null; rect: Rect | null; tag: string; path: NoCaminho[]; itens: Item[] }
  | { type: "textEdited"; fid: string; html: string }
  | { type: "pin"; n: number }
  | { type: "atalho"; acao: "inspect" | "undo" | "redo" };

/** app → iframe. `highlight` define a seleção (o iframe responde com `select`). */
export type ParaCanvas =
  | { type: "setMode"; mode: Modo }
  | { type: "highlight"; fids: string[] }
  | { type: "scrollTo"; fid: string }
  | { type: "showPins"; pins: Pin[] }
  | { type: "patch"; fid: string; html: string };

const MARCA = "forja-design";

const eRect = (r: any) => r === null || (r && ["x", "y", "w", "h"].every((k) => typeof r[k] === "number"));
const eTexto = (v: any) => typeof v === "string";
const eItem = (n: any) => n && eTexto(n.fid) && eTexto(n.tag) && eTexto(n.cls);

/** Só aceita mensagem da janela do canvas e com a forma certa; qualquer outra coisa é ignorada. */
export function lerMensagem(e: MessageEvent, janela: Window | null | undefined): DoCanvas | null {
  const d = e.data;
  if (!janela || e.source !== janela || !d || typeof d !== "object" || d[MARCA] !== 1) return null;
  switch (d.type) {
    case "ready":
      return { type: "ready" };
    case "hover":
      return (d.fid === null || eTexto(d.fid)) && eRect(d.rect) ? { type: "hover", fid: d.fid, rect: d.rect } : null;
    case "select":
      return (d.fid === null || eTexto(d.fid)) && eRect(d.rect) && eTexto(d.tag) && Array.isArray(d.path) &&
        d.path.every((n: any) => eItem(n) && eTexto(n.sec)) && Array.isArray(d.itens) && d.itens.every(eItem)
        ? { type: "select", fid: d.fid, rect: d.rect, tag: d.tag, path: d.path, itens: d.itens } : null;
    case "textEdited":
      return eTexto(d.fid) && eTexto(d.html) && d.html.length < 200_000 ? { type: "textEdited", fid: d.fid, html: d.html } : null;
    case "pin":
      return Number.isInteger(d.n) ? { type: "pin", n: d.n } : null;
    case "atalho":
      return ["inspect", "undo", "redo"].includes(d.acao) ? { type: "atalho", acao: d.acao } : null;
  }
  return null;
}

export function enviar(janela: Window | null | undefined, msg: ParaCanvas) {
  janela?.postMessage({ [MARCA]: 1, ...msg }, "*");   // "*": o destino tem origem opaca
}

/** Roda DENTRO do iframe (vai como texto via toString). Não pode usar nada de fora dele. */
function inspetor() {
  const MARCA = "forja-design";
  const AZUL = "#4f8ff7";
  const INLINE = new Set(["b", "strong", "i", "em", "u", "s", "small", "span", "a", "br", "sup", "sub", "code", "mark", "abbr"]);
  let modo = "view";
  let sel: string[] = [];
  let pins: { fid: string; n: number }[] = [];
  let hover: Element | null = null;
  let editando: { el: HTMLElement; antes: string } | null = null;
  const filhos: string[] = [];   // seta para baixo volta por aqui
  const envia = (m: object) => parent.postMessage({ [MARCA]: 1, ...m }, "*");
  const porFid = (f: string) => document.querySelector(`[data-fid="${CSS.escape(f)}"]`);
  const alvo = (el: Element | null) => {
    while (el && !el.hasAttribute("data-fid")) el = el.parentElement;
    return el && el !== document.documentElement ? el : null;   // <html> não é selecionável
  };
  const retangulo = (el: Element) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  };
  const item = (e: Element) => ({ fid: e.getAttribute("data-fid") || "", tag: e.tagName.toLowerCase(), cls: (e.getAttribute("class") || "").trim() });
  const caminho = (el: Element) => {
    const out = [];
    for (let e: Element | null = el; e && e !== document.documentElement; e = e.parentElement)
      if (e.getAttribute("data-fid")) out.unshift({ ...item(e), sec: e.getAttribute("data-section") || "" });
    return out;
  };
  // texto editável: tem texto e só filhos inline (um <div> dentro já é layout, não texto)
  const deTexto = (el: Element) => !!el.textContent?.trim() &&
    [...el.querySelectorAll("*")].every((f) => INLINE.has(f.tagName.toLowerCase())) &&
    !["html", "body", "section", "style", "script"].includes(el.tagName.toLowerCase());

  const camada = document.createElement("div");
  camada.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
  const caixa = (traco: string) => {
    const d = document.createElement("div");
    d.style.cssText = `position:fixed;display:none;box-sizing:border-box;border:2px ${traco} ${AZUL};background:${AZUL}14;border-radius:2px`;
    camada.appendChild(d);
    return d;
  };
  const caixaHover = caixa("dashed");
  const etiqueta = document.createElement("div");
  etiqueta.style.cssText = `position:fixed;display:none;font:11px/1.6 ui-monospace,monospace;color:#fff;background:${AZUL};padding:0 5px;border-radius:3px;white-space:nowrap`;
  camada.appendChild(etiqueta);
  let temporarios: HTMLElement[] = [];

  const posiciona = (d: HTMLElement, el: Element | null) => {
    if (!el) return void (d.style.display = "none");
    const r = el.getBoundingClientRect();
    Object.assign(d.style, { display: "block", left: `${r.x}px`, top: `${r.y}px`, width: `${r.width}px`, height: `${r.height}px` });
  };
  const desenha = () => {
    posiciona(caixaHover, modo === "inspect" && hover && !editando && !sel.includes(hover.getAttribute("data-fid") || "") ? hover : null);
    temporarios.forEach((d) => d.remove());
    temporarios = sel.map((f) => {
      const d = caixa("solid");
      posiciona(d, porFid(f));
      return d;
    });
    // pins numerados (comentários pendentes), no canto de cima à direita do elemento, como no Figma
    for (const p of pins) {
      const el = porFid(p.fid);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      const b = document.createElement("button");
      b.textContent = String(p.n);
      b.title = `Comentário ${p.n}`;
      b.style.cssText = `position:fixed;left:${Math.min(innerWidth - 24, r.right - 11)}px;top:${Math.max(2, r.top - 11)}px;` +
        `width:22px;height:22px;border-radius:11px 11px 11px 2px;border:2px solid #fff;background:#f59e0b;color:#111;` +
        "font:700 11px/18px system-ui,sans-serif;text-align:center;padding:0;cursor:pointer;pointer-events:auto;box-shadow:0 1px 4px #0006";
      b.onclick = (e) => {
        e.stopPropagation();
        envia({ type: "pin", n: p.n });
      };
      camada.appendChild(b);
      temporarios.push(b);
    }
    const primeiro = sel.length ? porFid(sel[sel.length - 1]) : null;
    if (primeiro && !editando) {
      const r = primeiro.getBoundingClientRect();
      const n = item(primeiro);
      etiqueta.textContent = n.tag + (n.cls ? "." + n.cls.split(/\s+/).join(".") : "") + (sel.length > 1 ? `  +${sel.length - 1}` : "");
      Object.assign(etiqueta.style, { display: "block", left: `${Math.max(0, r.x)}px`, top: `${Math.max(0, r.y - 18)}px` });
    } else etiqueta.style.display = "none";
  };
  const seleciona = (fids: string[]) => {
    sel = fids.filter((f, i) => porFid(f) && fids.indexOf(f) === i);
    desenha();
    const el = sel.length ? porFid(sel[sel.length - 1]) : null;
    const itens = sel.map((f) => item(porFid(f)!));
    envia(el
      ? { type: "select", fid: sel[sel.length - 1], rect: retangulo(el), tag: el.tagName.toLowerCase(), path: caminho(el), itens }
      : { type: "select", fid: null, rect: null, tag: "", path: [], itens: [] });
  };

  // ---- edição de texto (duplo clique): contenteditable no próprio elemento, sai para a fonte
  const terminaEdicao = (salvar: boolean) => {
    if (!editando) return;
    const { el, antes } = editando;
    editando = null;
    el.removeAttribute("contenteditable");
    el.removeAttribute("spellcheck");
    if (!salvar) el.innerHTML = antes;
    else if (el.innerHTML !== antes) envia({ type: "textEdited", fid: el.getAttribute("data-fid"), html: el.innerHTML });
    desenha();
  };
  document.addEventListener("dblclick", (e) => {
    const el = alvo(e.target as Element) as HTMLElement | null;
    if (!el || !deTexto(el) || editando?.el === el) return;
    e.preventDefault();
    terminaEdicao(true);
    editando = { el, antes: el.innerHTML };
    el.setAttribute("contenteditable", "true");
    el.setAttribute("spellcheck", "false");
    el.focus();
    desenha();
  }, true);
  document.addEventListener("focusout", (e) => {
    if (editando && e.target === editando.el) terminaEdicao(true);
  }, true);

  document.addEventListener("mouseover", (e) => {
    if (modo !== "inspect") return;
    hover = alvo(e.target as Element);
    desenha();
    envia({ type: "hover", fid: hover?.getAttribute("data-fid") ?? null, rect: hover ? retangulo(hover) : null });
  }, true);
  document.addEventListener("mouseleave", () => {
    hover = null;
    desenha();
  });
  document.addEventListener("click", (e) => {
    if (editando && editando.el.contains(e.target as Node)) return;   // clique dentro do texto: é o cursor
    if (modo === "inspect") {
      e.preventDefault();
      e.stopPropagation();
      let el = alvo(e.target as Element);
      if (el && e.altKey) el = alvo(el.parentElement) || el;   // Alt+clique: o pai
      filhos.length = 0;
      if (!el) return;
      const f = el.getAttribute("data-fid")!;
      // Shift+clique: entra ou sai da seleção; clique simples: só ele
      seleciona(e.shiftKey ? (sel.includes(f) ? sel.filter((x) => x !== f) : [...sel, f]) : [f]);
      return;
    }
    // modo view: link para fora do documento não tira o canvas do lugar
    const a = (e.target as Element).closest?.("a[href]");
    if (a && !(a.getAttribute("href") || "").startsWith("#")) e.preventDefault();
  }, true);
  document.addEventListener("keydown", (e) => {
    if (editando) {
      if (e.key === "Escape" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        terminaEdicao(e.key === "Enter");
      }
      return;   // o resto (Ctrl+Z inclusive) é do texto
    }
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && k === "c") return envia({ type: "atalho", acao: "inspect" }), e.preventDefault();
    if ((e.ctrlKey || e.metaKey) && (k === "y" || (k === "z" && e.shiftKey))) return envia({ type: "atalho", acao: "redo" }), e.preventDefault();
    if ((e.ctrlKey || e.metaKey) && k === "z") return envia({ type: "atalho", acao: "undo" }), e.preventDefault();
    if (modo !== "inspect" || !sel.length) return;
    const atual = porFid(sel[sel.length - 1]);
    if (e.key === "ArrowUp" && atual) {
      const pai = alvo(atual.parentElement);
      if (pai) {
        filhos.push(sel[sel.length - 1]);
        seleciona([pai.getAttribute("data-fid")!]);
      }
    } else if (e.key === "ArrowDown" && filhos.length) seleciona([filhos.pop()!]);
    else if (e.key === "Escape") seleciona([]);
    else return;
    e.preventDefault();
  });
  addEventListener("scroll", desenha, true);
  addEventListener("resize", desenha);

  addEventListener("message", (e) => {
    const d = e.data;
    if (e.source !== parent || !d || d[MARCA] !== 1) return;
    if (d.type === "setMode" && ["view", "inspect", "editText"].includes(d.mode)) {
      modo = d.mode;
      hover = null;
      document.documentElement.style.cursor = modo === "inspect" ? "crosshair" : "";
      desenha();
    } else if (d.type === "highlight" && Array.isArray(d.fids)) {
      filhos.length = 0;
      seleciona(d.fids.filter((f: unknown) => typeof f === "string"));
    } else if (d.type === "scrollTo" && typeof d.fid === "string") {
      porFid(d.fid)?.scrollIntoView({ block: "center", behavior: "smooth" });
    } else if (d.type === "showPins" && Array.isArray(d.pins)) {
      pins = d.pins.filter((p: any) => p && typeof p.fid === "string" && Number.isInteger(p.n));
      desenha();
    } else if (d.type === "patch" && typeof d.fid === "string" && typeof d.html === "string") {
      // <template> em vez de outerHTML: o parse não depende do pai (head, tr...) e o nó novo entra
      // pronto, <style> inclusive
      const el = porFid(d.fid);
      const t = document.createElement("template");
      t.innerHTML = d.html;
      const novo = t.content.firstElementChild;
      if (el && novo) el.replaceWith(document.importNode(novo, true));
      desenha();
    }
  });

  const pronto = () => {
    document.documentElement.appendChild(camada);
    envia({ type: "ready" });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", pronto);
  else pronto();
}

// O canvas não tem allow-same-origin e o CSP corta qualquer rede: o design fica autocontido de
// verdade (fonte/imagem por URL não carrega) e o script dele não sai do iframe.
const CSP = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:">`;
const SCRIPT = `<script>(${inspetor.toString()})()</script>`;

/** O que vai para o srcdoc: CSP e inspetor logo depois do <head>. Só na renderização. */
export function paraCanvas(html: string, inspecao = true): string {
  const extra = CSP + (inspecao ? SCRIPT : "");
  const i = html.search(/<head[^>]*>/i);
  if (i < 0) return extra + html;
  const fim = html.indexOf(">", i) + 1;
  return html.slice(0, fim) + extra + html.slice(fim);
}

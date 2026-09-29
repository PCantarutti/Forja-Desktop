// Canvas do Design: o que é injetado no iframe na renderização (CSP + script de inspeção, nunca
// salvos na fonte) e o protocolo postMessage entre o app e o iframe.
//
// O iframe roda com sandbox="allow-scripts" sem allow-same-origin: origem opaca ("null"), então a
// origem não prova nada. Quem vale é `e.source` (a janela do iframe / o parent) + a forma da mensagem.

export type Rect = { x: number; y: number; w: number; h: number };
export type NoCaminho = { fid: string; tag: string; cls: string };
export type Modo = "view" | "inspect" | "editText";

/** iframe → app */
export type DoCanvas =
  | { type: "ready" }
  | { type: "hover"; fid: string | null; rect: Rect | null }
  | { type: "select"; fid: string | null; rect: Rect | null; tag: string; path: NoCaminho[] }
  | { type: "textEdited"; fid: string; html: string }
  | { type: "atalho"; acao: "inspect" | "undo" | "redo" };

/** app → iframe. `highlight` define a seleção (o iframe responde com `select`). */
export type ParaCanvas =
  | { type: "setMode"; mode: Modo }
  | { type: "highlight"; fids: string[] }
  | { type: "scrollTo"; fid: string }
  | { type: "showPins"; pins: { fid: string; n: number }[] }
  | { type: "patch"; fid: string; html: string };

const MARCA = "forja-design";

const eRect = (r: any) => r === null || (r && ["x", "y", "w", "h"].every((k) => typeof r[k] === "number"));
const eTexto = (v: any) => typeof v === "string";

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
        d.path.every((n: any) => n && eTexto(n.fid) && eTexto(n.tag) && eTexto(n.cls))
        ? { type: "select", fid: d.fid, rect: d.rect, tag: d.tag, path: d.path } : null;
    case "textEdited":
      return eTexto(d.fid) && eTexto(d.html) ? { type: "textEdited", fid: d.fid, html: d.html } : null;
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
  let modo = "view";
  let sel: string[] = [];
  let hover: Element | null = null;
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
  const caminho = (el: Element) => {
    const out = [];
    for (let e: Element | null = el; e; e = e.parentElement) {
      const f = e.getAttribute("data-fid");
      if (f && e !== document.documentElement) out.unshift({ fid: f, tag: e.tagName.toLowerCase(), cls: (e.getAttribute("class") || "").trim() });
    }
    return out;
  };

  const camada = document.createElement("div");
  camada.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
  const caixa = (cor: string, traco: string) => {
    const d = document.createElement("div");
    d.style.cssText = `position:fixed;display:none;box-sizing:border-box;border:2px ${traco} ${cor};background:${cor}14;border-radius:2px`;
    camada.appendChild(d);
    return d;
  };
  const caixaHover = caixa("#4f8ff7", "dashed");
  const etiqueta = document.createElement("div");
  etiqueta.style.cssText = "position:fixed;display:none;font:11px/1.6 ui-monospace,monospace;color:#fff;background:#4f8ff7;padding:0 5px;border-radius:3px;white-space:nowrap";
  camada.appendChild(etiqueta);
  let caixasSel: HTMLDivElement[] = [];

  const posiciona = (d: HTMLElement, el: Element | null) => {
    if (!el) return void (d.style.display = "none");
    const r = el.getBoundingClientRect();
    Object.assign(d.style, { display: "block", left: `${r.x}px`, top: `${r.y}px`, width: `${r.width}px`, height: `${r.height}px` });
  };
  const desenha = () => {
    posiciona(caixaHover, modo === "inspect" && hover && !sel.includes(hover.getAttribute("data-fid") || "") ? hover : null);
    caixasSel.forEach((d) => d.remove());
    caixasSel = sel.map((f) => {
      const d = caixa("#4f8ff7", "solid");
      posiciona(d, porFid(f));
      return d;
    });
    const primeiro = sel.length ? porFid(sel[0]) : null;
    if (primeiro) {
      const r = primeiro.getBoundingClientRect();
      etiqueta.textContent = caminho(primeiro).slice(-1).map((n) => n.tag + (n.cls ? "." + n.cls.split(/\s+/).join(".") : ""))[0];
      Object.assign(etiqueta.style, { display: "block", left: `${Math.max(0, r.x)}px`, top: `${Math.max(0, r.y - 18)}px` });
    } else etiqueta.style.display = "none";
  };
  const seleciona = (fids: string[]) => {
    sel = fids.filter((f) => porFid(f));
    desenha();
    const el = sel.length ? porFid(sel[0]) : null;
    envia(el
      ? { type: "select", fid: sel[0], rect: retangulo(el), tag: el.tagName.toLowerCase(), path: caminho(el) }
      : { type: "select", fid: null, rect: null, tag: "", path: [] });
  };

  document.addEventListener("mouseover", (e) => {
    if (modo !== "inspect") return;
    hover = alvo(e.target as Element);
    desenha();
    const f = hover?.getAttribute("data-fid") ?? null;
    envia({ type: "hover", fid: f, rect: hover ? retangulo(hover) : null });
  }, true);
  document.addEventListener("mouseleave", () => {
    hover = null;
    desenha();
  });
  document.addEventListener("click", (e) => {
    if (modo === "inspect") {
      e.preventDefault();
      e.stopPropagation();
      let el = alvo(e.target as Element);
      if (el && e.altKey) el = alvo(el.parentElement) || el;   // Alt+clique: o pai
      filhos.length = 0;
      if (el) seleciona([el.getAttribute("data-fid")!]);
      return;
    }
    // modo view: link para fora do documento não tira o canvas do lugar
    const a = (e.target as Element).closest?.("a[href]");
    if (a && !(a.getAttribute("href") || "").startsWith("#")) e.preventDefault();
  }, true);
  document.addEventListener("keydown", (e) => {
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && k === "c") return envia({ type: "atalho", acao: "inspect" }), e.preventDefault();
    if ((e.ctrlKey || e.metaKey) && (k === "y" || (k === "z" && e.shiftKey))) return envia({ type: "atalho", acao: "redo" }), e.preventDefault();
    if ((e.ctrlKey || e.metaKey) && k === "z") return envia({ type: "atalho", acao: "undo" }), e.preventDefault();
    if (modo !== "inspect" || !sel.length) return;
    const atual = porFid(sel[0]);
    if (e.key === "ArrowUp" && atual) {
      const pai = alvo(atual.parentElement);
      if (pai) {
        filhos.push(sel[0]);
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

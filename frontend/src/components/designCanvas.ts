// Canvas do Design: o que é injetado no iframe na renderização (CSP + script de inspeção, nunca
// salvos na fonte) e o protocolo postMessage entre o app e o iframe.
//
// O iframe roda com sandbox="allow-scripts" sem allow-same-origin: origem opaca ("null"), então a
// origem não prova nada. Quem vale é `e.source` (a janela do iframe / o parent) + a forma da mensagem.

export type Rect = { x: number; y: number; w: number; h: number };
export type NoCaminho = { fid: string; tag: string; cls: string; sec: string };   // sec = data-section
export type Item = { fid: string; tag: string; cls: string };
export type Modo = "view" | "inspect" | "comment" | "edit" | "editText";   // comment/edit: o clique escolhe o alvo
/** Propriedades que o modo Editar mostra e mexe (as mesmas que o backend aceita em /estilo). */
export const PROPS_EDITAVEIS = ["color", "background-color", "font-size", "font-weight", "font-family", "line-height", "letter-spacing",
  "text-align", "padding", "margin", "border-radius", "border", "width", "height", "opacity", "gap"] as const;
export type Pin = { fid: string; n: number };
/** Um nó do painel Camadas: em ordem de documento, com a profundidade. */
export type NoArvore = { fid: string; tag: string; cls: string; sec: string; texto: string; nivel: number; oculto: boolean };
export type Problema = { fid: string | null; tipo: string; detalhe: string; gravidade: "erro" | "aviso"; rotulo: string };

/** iframe → app. `select` traz o principal (último clicado) e a seleção inteira em `itens`. */
export type DoCanvas =
  | { type: "ready" }
  | { type: "hover"; fid: string | null; rect: Rect | null }
  | { type: "select"; fid: string | null; rect: Rect | null; tag: string; path: NoCaminho[]; itens: Item[]; estilo: Record<string, string>;
      href: string | null }
  | { type: "mover"; fids: string[]; alvo: string; onde: "antes" | "depois" }     // arrastou no modo Editar
  | { type: "redimensionar"; fid: string; w: number | null; h: number | null }   // puxou uma alça
  | { type: "textEdited"; fid: string; html: string }
  | { type: "pin"; n: number }
  | { type: "slides"; atual: number; total: number }
  | { type: "tela"; nome: string }                          // o runtime do protótipo trocou de tela
  | { type: "auditoria"; itens: Problema[]; escopo: string }
  | { type: "atalho"; acao: "inspect" | "comentar" | "undo" | "redo" | "sair" | "apagar" | "duplicar" }
  | { type: "arvore"; nos: NoArvore[] };

/** app → iframe. `highlight` define a seleção (o iframe responde com `select`). */
export type ParaCanvas =
  | { type: "setMode"; mode: Modo }
  | { type: "highlight"; fids: string[] }
  | { type: "scrollTo"; fid: string }
  | { type: "showPins"; pins: Pin[] }
  | { type: "setSlide"; n: number }
  | { type: "setTela"; nome: string }
  | { type: "auditar" }
  | { type: "setMulti"; on: boolean }        // cada clique soma/tira da seleção (como Shift/Ctrl+clique)
  | { type: "semelhantes"; fid: string }
  | { type: "arvore" }                        // pede os nós para o painel Camadas
  | { type: "realce"; fid: string | null }    // hover vindo de fora (linha do painel)
  | { type: "setTokens"; tokens: Record<string, string> }   // prévia dos sliders; {} limpa
  | { type: "setEstilo"; fids: string[]; estilos: Record<string, string> }   // prévia do modo Editar ("" tira)
  | { type: "patch"; fid: string; html: string };

const MARCA = "forja-design";

/** A ponte do Electron (só no Desktop; no Docker não existe). Tipada aqui para os dois repos. */
export const ponte = () =>
  (window as { forja?: { token?: string; pickFolder?: (inicio?: string) => Promise<string | null> } }).forja;

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
        ? { type: "select", fid: d.fid, rect: d.rect, tag: d.tag, path: d.path, itens: d.itens,
            estilo: Object.fromEntries(PROPS_EDITAVEIS.filter((k) => eTexto(d.estilo?.[k]) && d.estilo[k].length < 300).map((k) => [k, d.estilo[k]])),
            href: eTexto(d.href) && d.href.length < 3000 ? d.href : null }
        : null;
    case "mover":
      return Array.isArray(d.fids) && d.fids.length <= 60 && d.fids.every(eTexto) && eTexto(d.alvo) && ["antes", "depois"].includes(d.onde)
        ? { type: "mover", fids: d.fids, alvo: d.alvo, onde: d.onde } : null;
    case "redimensionar": {
      const n = (v: any) => v === null || (typeof v === "number" && v > 0 && v < 20000);
      return eTexto(d.fid) && n(d.w) && n(d.h) ? { type: "redimensionar", fid: d.fid, w: d.w, h: d.h } : null;
    }
    case "textEdited":
      return eTexto(d.fid) && eTexto(d.html) && d.html.length < 200_000 ? { type: "textEdited", fid: d.fid, html: d.html } : null;
    case "pin":
      return Number.isInteger(d.n) ? { type: "pin", n: d.n } : null;
    case "auditoria":
      return Array.isArray(d.itens) && d.itens.length <= 200 && d.itens.every((x: any) => x && (x.fid === null || eTexto(x.fid)) &&
        eTexto(x.tipo) && eTexto(x.detalhe) && ["erro", "aviso"].includes(x.gravidade) && eTexto(x.rotulo))
        ? { type: "auditoria", itens: d.itens, escopo: eTexto(d.escopo) ? d.escopo : "" } : null;
    case "arvore":
      return Array.isArray(d.nos) && d.nos.length <= 3000 && d.nos.every((n: any) => eItem(n) && eTexto(n.sec) && eTexto(n.texto)
        && Number.isInteger(n.nivel) && typeof n.oculto === "boolean") ? { type: "arvore", nos: d.nos } : null;
    case "tela":
      return eTexto(d.nome) && d.nome.length < 80 ? { type: "tela", nome: d.nome } : null;
    case "slides":
      return Number.isInteger(d.atual) && Number.isInteger(d.total) ? { type: "slides", atual: d.atual, total: d.total } : null;
    case "atalho":
      return ["inspect", "comentar", "undo", "redo", "sair", "apagar", "duplicar"].includes(d.acao) ? { type: "atalho", acao: d.acao } : null;
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
  let slide = 0;
  let multi = false;
  const escolhendo = () => modo === "inspect" || modo === "comment" || modo === "edit";
  // o que o painel do modo Editar mostra como valor atual (a lista é a PROPS_EDITAVEIS: aqui não dá para importar)
  const PROPS = ["color", "background-color", "font-size", "font-weight", "font-family", "line-height", "letter-spacing",
    "text-align", "padding", "margin", "border-radius", "border", "width", "height", "opacity", "gap"];
  const estiloDe = (el: Element) => {
    const c = getComputedStyle(el);
    return Object.fromEntries(PROPS.map((k) => [k, c.getPropertyValue(k)]));
  };
  let previa: string[] = [];   // tokens sobrescritos ao vivo pelo painel de ajustes
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
  // fx-<fid> é a classe que o modo Editar põe para as regras por largura: não é do design
  const classes = (e: Element) => (e.getAttribute("class") || "").split(/\s+/).filter((c) => c && !c.startsWith("fx-"));
  const item = (e: Element) => ({ fid: e.getAttribute("data-fid") || "", tag: e.tagName.toLowerCase(), cls: classes(e).join(" ") });
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

  // ---- deck: um slide por vez, 1920x1080 escalado para caber (só na tela; a fonte não muda)
  const listaSlides = () => [...document.querySelectorAll("body > [data-slide]")];
  const estiloSlides = document.createElement("style");
  const layout = () => {
    const ss = listaSlides();
    if (!ss.length) return void (estiloSlides.textContent = "");
    slide = Math.max(0, Math.min(slide, ss.length - 1));
    const e = Math.min(innerWidth / 1920, innerHeight / 1080);
    estiloSlides.textContent = "html,body{margin:0!important;overflow:hidden!important;background:#3a3a3a!important}" +
      "body>[data-slide]{display:none!important}" +
      `body>[data-slide][data-forja-atual]{display:block!important;position:fixed!important;margin:0!important;` +
      `left:${(innerWidth - 1920 * e) / 2}px!important;top:${(innerHeight - 1080 * e) / 2}px!important;` +
      `transform:scale(${e})!important;transform-origin:0 0!important}`;
    ss.forEach((el, i) => (i === slide ? el.setAttribute("data-forja-atual", "") : el.removeAttribute("data-forja-atual")));
  };
  const irSlide = (n: number) => {
    const ss = listaSlides();
    if (!ss.length) return;
    slide = n;
    layout();
    envia({ type: "slides", atual: slide + 1, total: ss.length });
  };
  const slideDe = (el: Element | null) => listaSlides().findIndex((s) => s.contains(el));

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
  let realceFora = false;   // hover pedido pelo painel Camadas (vale em qualquer modo)
  // modo Editar: puxando uma alça, ou arrastando a seleção para outro lugar
  let redim: { el: HTMLElement; dir: string; x0: number; y0: number; w0: number; h0: number; w: number | null; h: number | null } | null = null;
  let arrasto: { x0: number; y0: number; ativo: boolean; alvo: Element | null; onde: "antes" | "depois" } | null = null;
  let engoleClique = false;
  const linha = document.createElement("div");
  linha.style.cssText = `position:fixed;display:none;background:${AZUL};border-radius:2px;box-shadow:0 0 0 1px #fff`;
  camada.appendChild(linha);

  const posiciona = (d: HTMLElement, el: Element | null) => {
    if (!el) return void (d.style.display = "none");
    const r = el.getBoundingClientRect();
    Object.assign(d.style, { display: "block", left: `${r.x}px`, top: `${r.y}px`, width: `${r.width}px`, height: `${r.height}px` });
  };
  const desenha = () => {
    posiciona(caixaHover, (escolhendo() || realceFora) && hover && !editando && !sel.includes(hover.getAttribute("data-fid") || "") ? hover : null);
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
    if (modo === "edit" && sel.length === 1 && primeiro && !editando && !arrasto) {
      const r = primeiro.getBoundingClientRect();
      for (const [dir, x, y, cursor] of [["e", r.right, r.top + r.height / 2, "ew-resize"], ["s", r.left + r.width / 2, r.bottom, "ns-resize"],
                                         ["se", r.right, r.bottom, "nwse-resize"]] as const) {
        const h = document.createElement("div");
        h.setAttribute("data-alca", dir);
        h.style.cssText = `position:fixed;left:${x - 5}px;top:${y - 5}px;width:10px;height:10px;background:#fff;border:2px solid ${AZUL};` +
          `border-radius:2px;box-sizing:border-box;pointer-events:auto;cursor:${cursor}`;
        h.onmousedown = (e) => {
          e.preventDefault();
          e.stopPropagation();
          const b = primeiro.getBoundingClientRect();
          redim = { el: primeiro as HTMLElement, dir, x0: e.clientX, y0: e.clientY, w0: b.width, h0: b.height, w: null, h: null };
        };
        camada.appendChild(h);
        temporarios.push(h);
      }
    }
    if (primeiro && !editando) {
      const r = primeiro.getBoundingClientRect();
      const n = item(primeiro);
      etiqueta.textContent = n.tag + (n.cls ? "." + n.cls.split(/\s+/).join(".") : "") + (sel.length > 1 ? `  +${sel.length - 1}` : "");
      Object.assign(etiqueta.style, { display: "block", left: `${Math.max(0, r.x)}px`, top: `${Math.max(0, r.y - 18)}px` });
    } else etiqueta.style.display = "none";
  };
  const seleciona = (fids: string[]) => {
    sel = fids.filter((f, i) => porFid(f) && fids.indexOf(f) === i);
    const el = sel.length ? porFid(sel[sel.length - 1]) : null;
    const i = slideDe(el);   // elemento de outro slide (comentário, breadcrumb): vai até ele
    if (i >= 0 && i !== slide) irSlide(i);
    const t = el?.closest("body > [data-tela]");   // protótipo: idem com a tela
    const irTela = (window as { forjaIrTela?: (n: string) => void }).forjaIrTela;
    if (t && !t.hasAttribute("data-tela-atual") && irTela) irTela(t.getAttribute("data-section") || "");
    desenha();
    const itens = sel.map((f) => item(porFid(f)!));
    envia(el
      ? { type: "select", fid: sel[sel.length - 1], rect: retangulo(el), tag: el.tagName.toLowerCase(), path: caminho(el), itens, estilo: estiloDe(el),
          href: el.getAttribute("href") }
      : { type: "select", fid: null, rect: null, tag: "", path: [], itens: [], estilo: {}, href: null });
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
    realceFora = false;
    if (!escolhendo()) return;
    hover = alvo(e.target as Element);
    desenha();
    envia({ type: "hover", fid: hover?.getAttribute("data-fid") ?? null, rect: hover ? retangulo(hover) : null });
  }, true);
  document.addEventListener("mouseleave", () => {
    hover = null;
    desenha();
  });
  document.addEventListener("mousedown", (e) => {
    if (modo !== "edit" || editando || e.button !== 0 || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
    const el = alvo(e.target as Element);
    if (el && sel.some((f) => porFid(f)?.contains(el))) {
      arrasto = { x0: e.clientX, y0: e.clientY, ativo: false, alvo: null, onde: "depois" };
    }
  }, true);
  document.addEventListener("dragstart", (e) => { if (modo === "edit") e.preventDefault(); }, true);   // <img>/<a> nativos
  document.addEventListener("mousemove", (e) => {
    if ((redim || arrasto) && e.buttons === 0) return solta(false);   // soltou fora da página e voltou
    if (redim) {
      const { el, dir, x0, y0, w0, h0 } = redim;
      if (dir !== "s") el.style.setProperty("width", `${(redim.w = Math.max(8, Math.round(w0 + e.clientX - x0)))}px`, "important");
      if (dir !== "e") el.style.setProperty("height", `${(redim.h = Math.max(8, Math.round(h0 + e.clientY - y0)))}px`, "important");
      return desenha();
    }
    if (!arrasto) return;
    if (!arrasto.ativo && Math.hypot(e.clientX - arrasto.x0, e.clientY - arrasto.y0) < 6) return;
    if (!arrasto.ativo) {
      arrasto.ativo = true;
      document.documentElement.style.userSelect = "none";
      getSelection()?.removeAllRanges();
    }
    let t = alvo(document.elementFromPoint(e.clientX, e.clientY));
    // o destino natural é um irmão do que está sendo arrastado (reordenar); fora do pai, o elemento sob o mouse
    const origem = porFid(sel[sel.length - 1]);
    for (let u: Element | null = t; u && u !== document.body; u = u.parentElement)
      if (u.parentElement === origem?.parentElement && u.hasAttribute("data-fid")) { t = u; break; }
    const valido = t && t !== document.body && !sel.some((f) => porFid(f)?.contains(t));
    arrasto.alvo = valido ? t : null;
    if (!valido || !t) return void (linha.style.display = "none");
    const r = t.getBoundingClientRect();
    const pai = t.parentElement ? getComputedStyle(t.parentElement) : null;
    const lado = !!pai && ((pai.display.includes("flex") && !pai.flexDirection.startsWith("column")) || pai.display.includes("grid"));
    arrasto.onde = (lado ? e.clientX < r.left + r.width / 2 : e.clientY < r.top + r.height / 2) ? "antes" : "depois";
    Object.assign(linha.style, lado
      ? { display: "block", left: `${(arrasto.onde === "antes" ? r.left : r.right) - 2}px`, top: `${r.top}px`, width: "4px", height: `${r.height}px` }
      : { display: "block", left: `${r.left}px`, top: `${(arrasto.onde === "antes" ? r.top : r.bottom) - 2}px`, width: `${r.width}px`, height: "4px" });
    desenha();
  }, true);
  document.addEventListener("mouseup", () => solta(true), true);
  // soltar: a alça sempre aplica; o arrasto só se o botão foi solto dentro da página (fora, cancela)
  const solta = (dentro: boolean) => {
    document.documentElement.style.userSelect = "";
    if (redim) {
      const { el, w, h } = redim;
      redim = null;
      engoleClique = true;
      if (w !== null || h !== null) envia({ type: "redimensionar", fid: el.getAttribute("data-fid"), w, h });
    } else if (arrasto) {
      const a = arrasto;
      arrasto = null;
      linha.style.display = "none";
      if (a.ativo) {
        engoleClique = dentro;
        if (a.alvo && dentro) envia({ type: "mover", fids: sel, alvo: a.alvo.getAttribute("data-fid"), onde: a.onde });
      }
      desenha();
    }
  };
  document.addEventListener("click", (e) => {
    if (engoleClique) {   // o clique que fecha um arrasto não é seleção
      engoleClique = false;
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    if (editando && editando.el.contains(e.target as Node)) return;   // clique dentro do texto: é o cursor
    if (escolhendo()) {
      e.preventDefault();
      e.stopPropagation();
      let el = alvo(e.target as Element);
      if (el && e.altKey) el = alvo(el.parentElement) || el;   // Alt+clique: o pai
      filhos.length = 0;
      if (!el) return;
      const f = el.getAttribute("data-fid")!;
      // Shift/Ctrl+clique (ou o modo múltipla ligado): entra ou sai da seleção; clique simples: só ele
      const junta = e.shiftKey || e.ctrlKey || e.metaKey || multi;
      seleciona(junta ? (sel.includes(f) ? sel.filter((x) => x !== f) : [...sel, f]) : [f]);
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
    if (listaSlides().length && ["ArrowLeft", "ArrowRight", "PageUp", "PageDown"].includes(e.key)) {
      irSlide(slide + (e.key === "ArrowLeft" || e.key === "PageUp" ? -1 : 1));
      desenha();
      return e.preventDefault();
    }
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && k === "c") return envia({ type: "atalho", acao: "inspect" }), e.preventDefault();
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && k === "m") return envia({ type: "atalho", acao: "comentar" }), e.preventDefault();
    if ((e.ctrlKey || e.metaKey) && (k === "y" || (k === "z" && e.shiftKey))) return envia({ type: "atalho", acao: "redo" }), e.preventDefault();
    if ((e.ctrlKey || e.metaKey) && k === "z") return envia({ type: "atalho", acao: "undo" }), e.preventDefault();
    // Esc sem nada para limpar: quem está fora (a apresentação em tela cheia) decide o que fazer
    if (e.key === "Escape" && !(escolhendo() && sel.length)) return envia({ type: "atalho", acao: "sair" });
    if (!escolhendo() || !sel.length) return;
    if (modo === "edit" && (e.key === "Delete" || e.key === "Backspace")) return envia({ type: "atalho", acao: "apagar" }), e.preventDefault();
    if (modo === "edit" && (e.ctrlKey || e.metaKey) && k === "d") return envia({ type: "atalho", acao: "duplicar" }), e.preventDefault();
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
  let reposto = 0;
  addEventListener("scroll", () => {
    desenha();
    if (modo !== "comment" || !sel.length || reposto) return;
    reposto = requestAnimationFrame(() => {
      reposto = 0;
      const el = porFid(sel[sel.length - 1]);
      if (el) envia({ type: "select", fid: sel[sel.length - 1], rect: retangulo(el), tag: el.tagName.toLowerCase(), path: caminho(el),
                      itens: sel.map((f) => item(porFid(f)!)), estilo: estiloDe(el), href: el.getAttribute("href") });
    });
  }, true);
  addEventListener("resize", () => {
    layout();
    desenha();
  });

  // ---- acessibilidade, sem IA: contraste pelas cores computadas (WCAG 2.1), alt, nome acessível, títulos
  const lum = (c: number[]) => {
    const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const rgba = (t: string) => {
    const m = /rgba?\(([^)]+)\)/.exec(t);
    if (!m) return null;
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const fundo = (el: Element): number[] | null => {   // compõe as camadas até achar uma opaca; gradiente/imagem = não dá para saber
    const camadas: number[][] = [];
    for (let e: Element | null = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== "none") return null;
      const cor = rgba(cs.backgroundColor);
      if (cor && cor[3] > 0) { camadas.push(cor); if (cor[3] >= 1) break; }
    }
    return camadas.reverse().reduce((acc, c) => [0, 1, 2].map((i) => c[i] * c[3] + acc[i] * (1 - c[3])), [255, 255, 255]);
  };
  const auditar = () => {
    const itens: object[] = [];
    const vistos = new Set<string>();
    const add = (el: Element | null, tipo: string, detalhe: string, gravidade: string) => {
      const f = el ? alvo(el)?.getAttribute("data-fid") ?? null : null;
      if (vistos.has(`${f}|${tipo}`) || itens.length >= 120) return;
      vistos.add(`${f}|${tipo}`);
      itens.push({ fid: f, tipo, detalhe, gravidade, rotulo: el ? item(alvo(el) || el).tag + ((el.getAttribute("class") || "").trim() ? "." + (el.getAttribute("class") || "").trim().split(/\s+/)[0] : "") : "documento" });
    };
    const visivel = (el: Element) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden"; };
    if (!document.documentElement.getAttribute("lang")) add(null, "idioma", "O <html> não diz o idioma (lang=\"pt-BR\"): leitor de tela pronuncia errado.", "aviso");
    for (const el of document.querySelectorAll("body *")) {
      if (camada.contains(el) || !visivel(el)) continue;
      const tag = el.tagName.toLowerCase();
      const texto = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent || "").join("").trim();
      if (texto) {
        const cs = getComputedStyle(el);
        const cor = rgba(cs.color), bg = fundo(el);
        const px = parseFloat(cs.fontSize), grande = px >= 24 || (px >= 18.66 && +cs.fontWeight >= 700);
        if (cor && bg) {
          const c = [0, 1, 2].map((i) => cor[i] * cor[3] + bg[i] * (1 - cor[3]));
          const [a, b] = [lum(c), lum(bg)].sort((x, y) => y - x);
          const razao = (a + 0.05) / (b + 0.05), minimo = grande ? 3 : 4.5;
          if (razao < minimo) add(el, "contraste", `Contraste ${razao.toFixed(2)}:1 (mínimo ${minimo}:1${grande ? ", texto grande" : ""}) em “${texto.slice(0, 40)}”`, razao < minimo - 1 ? "erro" : "aviso");
        }
        if (px < 12) add(el, "texto pequeno", `Texto de ${px}px em “${texto.slice(0, 40)}”: difícil de ler (use pelo menos 12–14px).`, "aviso");
      }
      if (tag === "img" && !el.hasAttribute("alt")) add(el, "sem alt", "Imagem sem alt: descreva o que ela mostra (ou alt=\"\" se for decorativa).", "erro");
      if ((tag === "a" || tag === "button" || el.getAttribute("role") === "button") && !(el.textContent || "").trim()
          && !el.getAttribute("aria-label") && !el.getAttribute("title") && !el.querySelector("img[alt]:not([alt=''])"))
        add(el, "sem nome", `${tag === "a" ? "Link" : "Botão"} sem texto nem aria-label: o leitor de tela diz só “${tag === "a" ? "link" : "botão"}”.`, "erro");
      if ((tag === "input" || tag === "select" || tag === "textarea") && el.getAttribute("type") !== "hidden" && !el.getAttribute("aria-label")
          && !(el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) && !el.closest("label"))
        add(el, "campo sem rótulo", "Campo sem <label> nem aria-label (o placeholder some ao digitar).", "aviso");
    }
    let anterior = 0;
    for (const h of document.querySelectorAll("h1, h2, h3, h4, h5, h6")) {
      if (!visivel(h)) continue;
      const n = +h.tagName[1];
      if (anterior && n > anterior + 1) add(h, "nível de título", `Pula de h${anterior} para h${n}: a estrutura fica confusa para quem navega por títulos.`, "aviso");
      anterior = n;
    }
    if (!document.querySelector("h1")) add(null, "sem h1", "A página não tem nenhum <h1>.", "aviso");
    const escopo = listaSlides().length ? `slide ${slide + 1}` : document.querySelector("body > [data-tela][data-tela-atual]")
      ? `tela “${document.querySelector("body > [data-tela][data-tela-atual]")!.getAttribute("data-section")}”` : "página inteira";
    envia({ type: "auditoria", itens, escopo });
  };

  addEventListener("message", (e) => {
    const d = e.data;
    if (e.source !== parent || !d || d[MARCA] !== 1) return;
    if (d.type === "auditar") return auditar();
    if (d.type === "setMode" && ["view", "inspect", "comment", "edit", "editText"].includes(d.mode)) {
      modo = d.mode;
      hover = null;
      document.documentElement.style.cursor = modo === "inspect" || modo === "edit" ? "crosshair" : modo === "comment" ? "cell" : "";
      desenha();
    } else if (d.type === "setMulti") {
      multi = !!d.on;
    } else if (d.type === "semelhantes" && typeof d.fid === "string") {
      // mesma tag e mesmas classes (os 3 botões dos cards, todos os títulos de seção...); sem classe, mesma
      // tag dentro do mesmo tipo de pai
      const base = porFid(d.fid);
      if (base) {
        const cls = classes(base).sort().join(" ");
        const paiCls = (base.parentElement?.getAttribute("class") || "").trim();
        const iguais = [...document.querySelectorAll(base.tagName)].filter((el) => {
          if (!el.hasAttribute("data-fid") || camada.contains(el)) return false;
          const c = classes(el).sort().join(" ");
          return cls ? c === cls : !c && (el.parentElement?.getAttribute("class") || "").trim() === paiCls;
        }).slice(0, 60).map((el) => el.getAttribute("data-fid")!);
        seleciona([d.fid, ...iguais.filter((f) => f !== d.fid)]);
      }
    } else if (d.type === "arvore") {
      const nos: object[] = [];
      const anda = (el: Element, nivel: number) => {
        for (const f of el.children) {
          if (f === camada || nos.length >= 3000) continue;
          const tem = f.hasAttribute("data-fid");
          if (tem) {
            const direto = [...f.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ").trim().replace(/\s+/g, " ");
            const c = getComputedStyle(f);
            nos.push({ ...item(f), sec: f.getAttribute("data-section") || "", texto: (direto || (f.children.length ? "" : f.textContent || "")).trim().slice(0, 48),
                       nivel, oculto: c.display === "none" || c.visibility === "hidden" });
          }
          anda(f, tem ? nivel + 1 : nivel);
        }
      };
      if (document.body) anda(document.body, 0);
      envia({ type: "arvore", nos });
    } else if (d.type === "realce") {
      hover = typeof d.fid === "string" ? porFid(d.fid) : null;
      realceFora = !!hover;
      desenha();
    } else if (d.type === "highlight" && Array.isArray(d.fids)) {
      filhos.length = 0;
      seleciona(d.fids.filter((f: unknown) => typeof f === "string"));
    } else if (d.type === "scrollTo" && typeof d.fid === "string") {
      const el = porFid(d.fid);
      if (listaSlides().length) irSlide(Math.max(0, slideDe(el)));
      else el?.scrollIntoView({ block: "center", behavior: "smooth" });
      desenha();
    } else if (d.type === "setTokens" && d.tokens && typeof d.tokens === "object") {
      // inline no <html> vence o :root; nada disso vai para a fonte (quem salva é o backend)
      const raiz = document.documentElement.style;
      previa.forEach((k) => raiz.removeProperty(k));
      previa = Object.entries(d.tokens as Record<string, unknown>)
        .filter(([k, v]) => /^--[\w-]+$/.test(k) && typeof v === "string")
        .map(([k, v]) => (raiz.setProperty(k, v as string), k));
      desenha();
    } else if (d.type === "setEstilo" && Array.isArray(d.fids) && d.estilos && typeof d.estilos === "object") {
      // prévia no style do próprio elemento; quem grava é o backend (o patch troca o nó depois)
      for (const f of d.fids) {
        const el = typeof f === "string" ? (porFid(f) as HTMLElement | null) : null;
        if (!el) continue;
        for (const [k, v] of Object.entries(d.estilos as Record<string, unknown>))
          if (!PROPS.includes(k) || typeof v !== "string") continue;
          else if (v) el.style.setProperty(k, v, "important");   // vence as regras @media já salvas
          else el.style.removeProperty(k);
      }
      desenha();
    } else if (d.type === "setTela" && typeof d.nome === "string") {
      (window as { forjaIrTela?: (n: string) => void }).forjaIrTela?.(d.nome);
      desenha();
    } else if (d.type === "setSlide" && Number.isInteger(d.n)) {
      irSlide(d.n - 1);
      desenha();
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
      layout();   // se o nó trocado era um slide, a marca do slide atual foi junto
      desenha();
    }
  });

  const pronto = () => {
    document.documentElement.appendChild(camada);
    document.head.appendChild(estiloSlides);
    envia({ type: "ready" });
    irSlide(slide);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", pronto);
  else pronto();
}

// O canvas não tem allow-same-origin e o CSP corta qualquer rede: o design fica autocontido de
// verdade (fonte/imagem por URL não carrega) e o script dele não sai do iframe.
const CSP = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:">`;
const SCRIPT = `<script>(${inspetor.toString()})()</script>`;

/** Miniatura do slide k (1-based), numa caixa de `largura` px: só CSS, iframe sem script. */
const miniatura = (k: number, largura: number) =>
  `<style>html,body{margin:0!important;padding:0!important;overflow:hidden!important;background:#fff!important}` +
  `body>*{display:none!important}body>[data-slide]:nth-child(${k} of [data-slide]){display:block!important;margin:0!important;` +
  `box-shadow:none!important;transform:scale(${largura / 1920})!important;transform-origin:0 0!important}</style>`;

/** Documento estático para miniatura/comparação: CSP, nenhum script e, se vier, CSS por cima do do design
 *  (as variações de tokens entram assim: um :root depois do :root do documento vence). */
export function docEstatico(html: string, cssExtra = ""): string {
  const i = html.search(/<head[^>]*>/i);
  const comCsp = i < 0 ? CSP + html : html.slice(0, html.indexOf(">", i) + 1) + CSP + html.slice(html.indexOf(">", i) + 1);
  return cssExtra ? comCsp.replace(/<\/head>/i, `<style>${cssExtra}</style></head>`) : comCsp;
}

/** O que vai para o srcdoc: CSP e inspetor (ou o CSS da miniatura) logo depois do <head>. Só na
 *  renderização — nada disso vai para a fonte. */
export function paraCanvas(html: string, inspecao = true, mini?: { slide: number; largura: number }): string {
  const extra = CSP + (mini ? "" : inspecao ? SCRIPT : "");
  const i = html.search(/<head[^>]*>/i);
  if (i < 0) return extra + html;
  const fim = html.indexOf(">", i) + 1;
  // o CSS da miniatura vai no fim do <head>, depois do CSS do próprio deck
  const doc = html.slice(0, fim) + extra + html.slice(fim);
  return mini ? doc.replace(/<\/head>/i, miniatura(mini.slide, mini.largura) + "</head>") : doc;
}

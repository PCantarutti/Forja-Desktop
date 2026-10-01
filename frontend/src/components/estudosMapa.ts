// Mapa mental do resumo da tela Estudos: a árvore sai dos títulos (## tópico, ### subtópico, #### detalhe) e o
// desenho é um SVG de dois lados (tópicos à direita e à esquerda do tema). TS puro, sem DOM: o mesmo arquivo
// roda no desktop, no web e no celular (react-native-svg). Cada nó guarda a `ancora` — o índice do título
// entre os h2/h3/h4 do resumo renderizado — para o clique levar à seção.
// ponytail: SVG próprio em vez do Mermaid (3 MB, sem clique por nó no `mindmap`, e no celular só numa WebView);
// o Mermaid sai como texto em `paraMermaid`, para colar no Obsidian/Notion.

export type Ramo = { id: string; texto: string; nivel: number; ancora: number; filhos: Ramo[] };
export type NoMapa = {
  id: string; texto: string; linhas: string[]; nivel: number; ancora: number;
  x: number; y: number; w: number; h: number;   // canto de cima à esquerda, em px do desenho
  lado: 1 | -1 | 0;                             // à direita, à esquerda, o tema no meio
  ramo: number;                                 // o tópico de que descende (a cor)
  filhos: number; aberto: boolean;
};
export type Ligacao = { id: string; d: string; ramo: number };
export type Mapa = { nos: NoMapa[]; ligacoes: Ligacao[]; largura: number; altura: number };

/** Seções que não são matéria: ficam fora do mapa (mas contam na âncora, porque a tela as renderiza). */
const FORA = /^(fontes( consultadas)?|refer[eê]ncias( bibliogr[aá]ficas)?|bibliografia|revis[aã]o r[aá]pida)\s*:?$/i;   // "Fontes de energia" é matéria
const SIMBOLO: Record<string, string> = {
  Delta: "Δ", delta: "δ", alpha: "α", beta: "β", gamma: "γ", Gamma: "Γ", lambda: "λ", mu: "μ", pi: "π", theta: "θ",
  rho: "ρ", sigma: "σ", Sigma: "Σ", omega: "ω", Omega: "Ω", phi: "φ", epsilon: "ε", eta: "η", tau: "τ", nu: "ν",
  times: "×", cdot: "·", to: "→", rightarrow: "→", Rightarrow: "⇒", leftrightarrow: "↔", leq: "≤", geq: "≥", le: "≤",
  ge: "≥", neq: "≠", approx: "≈", infty: "∞", pm: "±", degree: "°", circ: "°",
};

/** O título como texto de nó: sem os # repetidos, a numeração, o Markdown e o LaTeX (ΔH em vez de $\Delta H$). */
export function limpar(t: string): string {
  return t
    .replace(/^(#+\s*)+/, "")
    .replace(/\s+#+\s*$/, "")                                   // "## Óptica ##": o fechamento opcional do ATX
    .replace(/^(\d+(\.\d+)+\.?|\d+[.)]|[IVX]+[.)])\s+/, "")       // "3.1", "2." e "IV)" — "3 leis de Newton" fica
    .replace(/\\frac\{([^}]*)\}\{([^}]*)\}/g, "$1/$2")
    .replace(/\\sqrt\{([^}]*)\}/g, "√$1")
    .replace(/\\(?:mathrm|text|mathbf|textbf|operatorname|mathit)\{([^}]*)\}/g, "$1")
    // "\Delta s" é Δs (o espaço só separa o comando); "a \times b" continua com espaço
    .replace(/\\([a-zA-Z]+)( ?)/g, (_, c: string, esp: string) => (/^[α-ωΑ-Ω]$/.test(SIMBOLO[c] ?? "") ? SIMBOLO[c] : (SIMBOLO[c] ?? c) + esp))
    .replace(/\\[(),;!]/g, " ")
    .replace(/[$*_`{}]+/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

const CERCA = /^ {0,3}(`{3,}|~{3,})/;
const SETEXT = /^ {0,3}(=+|-+)\s*$/;
const CONTAINER = /^ {0,3}(?:(?:>\s?)+|(?:[-*+]|\d+[.)])\s+)+/;   // citação e item de lista: o título de dentro também vira <hN>

/** A árvore dos títulos. `tema` vale quando o resumo não tem "# " no topo. A `ancora` conta os h2/h3/h4 como o
 *  renderizador conta (CommonMark): título dentro de citação ou de lista e o setext ("texto" + "---") entram na
 *  conta, mas não viram nó; o que está em cerca de código (``` ou ~~~) não conta. O salto ainda confere pelo
 *  texto (`acharTitulo`): a âncora é só o palpite. */
export function arvore(md: string, tema = ""): Ramo {
  const raiz: Ramo = { id: "raiz", texto: "", nivel: 0, ancora: -1, filhos: [] };
  const pilha: Ramo[] = [raiz];
  let cerca = "", ancora = -1, fora = false, anterior = "";
  for (const bruta of md.replace(/\r/g, "").split("\n")) {
    const c = CERCA.exec(bruta);
    if (cerca) {   // fecha só com a mesma marca, do mesmo tamanho ou maior, sem nada depois
      if (c && c[1][0] === cerca[0] && c[1].length >= cerca.length && !bruta.trim().slice(c[1].length).trim()) cerca = "";
      continue;
    }
    if (c) { cerca = c[1]; anterior = ""; continue; }
    const paragrafo = anterior;
    anterior = /^\s*$|^ {0,3}(#|>|[-*+] |\d+[.)] |\||<)/.test(bruta) ? "" : bruta;
    if (paragrafo && SETEXT.exec(bruta)) {   // "Texto" + "---": um h2 (ou h1 com "===") que o modelo nem quis
      if (bruta.trim()[0] === "-") ancora++;
      anterior = "";
      continue;
    }
    const dentro = CONTAINER.exec(bruta);
    const m = /^ {0,3}(#{1,4})\s+(.+)$/.exec(dentro ? bruta.slice(dentro[0].length) : bruta);
    if (!m) continue;
    if (dentro) { if (m[1].length > 1) ancora++; continue; }
    const n = m[1].length;
    if (n === 1) { raiz.texto ||= limpar(m[2]); continue; }
    ancora++;
    const texto = limpar(m[2]);
    if (n === 2) fora = FORA.test(texto);
    if (fora || !texto) continue;
    while (pilha.length > 1 && pilha[pilha.length - 1].nivel >= n - 1) pilha.pop();
    const pai = pilha[pilha.length - 1];
    const no: Ramo = { id: `n${ancora}`, texto, nivel: pai.nivel + 1, ancora, filhos: [] };   // "####" sem "###": vira filho do "##"
    pai.filhos.push(no);
    pilha.push(no);
  }
  raiz.texto = raiz.texto || limpar(tema) || "Resumo";
  return raiz;
}

/** Normaliza para comparar o texto do nó com o do título renderizado (sem acento de diferença de caixa, pontuação
 *  e espaço; a numeração que o `limpar` tirou continua no título, por isso é "contém"). */
export const normal = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

/** Qual dos títulos renderizados (h2/h3/h4, na ordem) é o do nó: os que contêm o texto do nó, o mais perto da
 *  âncora; nenhum (fórmula no título, por exemplo) = a própria âncora. */
export function acharTitulo(titulos: string[], texto: string, ancora: number): number {
  const alvo = normal(texto);
  let melhor = -1;
  titulos.forEach((t, i) => {
    if (alvo && normal(t).includes(alvo) && (melhor < 0 || Math.abs(i - ancora) < Math.abs(melhor - ancora))) melhor = i;
  });
  return melhor >= 0 ? melhor : ancora;
}

/** Todos os ids com filhos: o "abrir tudo". */
export function comFilhos(r: Ramo): string[] {
  return r.filhos.length ? [r.id, ...r.filhos.flatMap(comFilhos)] : [];
}

// ------------------------------------------------------------------ desenho

const FONTE = [16, 13.5, 12.5, 12];
const MAX_CARACTERES = [24, 26, 30, 30];
const PAD = [[16, 10], [12, 8], [10, 6], [9, 5]];
const CARACTERE = 0.56;   // largura média de um caractere em fração da fonte (sans do sistema)
const LINHA = 1.3;
const GAP_X = [56, 40, 32];
const GAP_Y = [18, 8, 6, 5];
const MARGEM = 24;

export const fonteDe = (nivel: number) => FONTE[Math.min(nivel, 3)];

function quebrar(texto: string, max: number): string[] {
  const linhas: string[] = [];
  let atual = "";
  for (const palavra of texto.split(" ")) {
    if (atual && (atual + " " + palavra).length > max) { linhas.push(atual); atual = palavra; }
    else atual = atual ? `${atual} ${palavra}` : palavra;
  }
  if (atual) linhas.push(atual);
  if (linhas.length > 3) linhas.splice(2, linhas.length, linhas[2].slice(0, max - 1) + "…");
  return linhas.map((l) => (l.length > max + 6 ? l.slice(0, max + 5) + "…" : l));
}

type Caixa = { r: Ramo; linhas: string[]; w: number; h: number; filhos: Caixa[]; aberto: boolean; altura: number };

function medir(r: Ramo, abertos: Set<string> | null): Caixa {
  const nv = Math.min(r.nivel, 3);
  const linhas = quebrar(r.texto, MAX_CARACTERES[nv]);
  const [px, py] = PAD[nv];
  const w = Math.ceil(Math.max(...linhas.map((l) => l.length)) * FONTE[nv] * CARACTERE + 2 * px);
  const h = Math.ceil(linhas.length * FONTE[nv] * LINHA + 2 * py);
  const aberto = r.nivel === 0 || !abertos || abertos.has(r.id);
  const filhos = aberto ? r.filhos.map((f) => medir(f, abertos)) : [];
  const gap = GAP_Y[Math.min(r.nivel + 1, 3)];
  const altura = Math.max(h, filhos.reduce((s, f) => s + f.altura, 0) + gap * Math.max(0, filhos.length - 1));
  return { r, linhas, w, h, filhos, aberto, altura };
}

/** As posições de tudo. `abertos` = ids com os filhos à mostra (null = tudo aberto). */
export function desenhar(raiz: Ramo, abertos: Set<string> | null = null): Mapa {
  const c = medir(raiz, abertos);
  // os tópicos se dividem entre os dois lados pelo tamanho, na ordem do resumo: direita de cima para baixo,
  // esquerda depois (o sentido do relógio, como se lê um mapa mental)
  const total = c.filhos.reduce((s, f) => s + f.altura, 0);
  let acum = 0, corte = c.filhos.length, melhor = Infinity;
  for (let i = 1; i <= c.filhos.length; i++) {   // o corte em que os dois lados ficam da altura mais parecida
    acum += c.filhos[i - 1].altura;
    if (Math.abs(2 * acum - total) < melhor) { melhor = Math.abs(2 * acum - total); corte = i; }
  }
  const lados: [1 | -1, Caixa[]][] = [[1, c.filhos.slice(0, corte)], [-1, c.filhos.slice(corte)]];
  const nos: NoMapa[] = [];
  const ligacoes: Ligacao[] = [];
  const no = (k: Caixa, x: number, y: number, lado: 1 | -1 | 0, ramo: number) => {
    const n: NoMapa = { id: k.r.id, texto: k.r.texto, linhas: k.linhas, nivel: k.r.nivel, ancora: k.r.ancora, x, y, w: k.w, h: k.h,
      lado, ramo, filhos: k.r.filhos.length, aberto: k.aberto };
    nos.push(n);
    return n;
  };
  const centro = no(c, -c.w / 2, -c.h / 2, 0, -1);
  for (const [lado, topicos] of lados) {
    // coluna de cada nível: começa onde termina a mais larga do nível de dentro
    const larg: number[] = [];
    const medirColuna = (k: Caixa, d: number) => { larg[d] = Math.max(larg[d] ?? 0, k.w); k.filhos.forEach((f) => medirColuna(f, d + 1)); };
    topicos.forEach((t) => medirColuna(t, 0));
    const coluna: number[] = [c.w / 2 + GAP_X[0]];
    for (let d = 1; d < larg.length; d++) coluna[d] = coluna[d - 1] + larg[d - 1] + GAP_X[Math.min(d, 2)];
    const alturaLado = topicos.reduce((s, t) => s + t.altura, 0) + GAP_Y[1] * Math.max(0, topicos.length - 1);
    const colocar = (k: Caixa, d: number, topo: number, ramo: number, pai: NoMapa) => {
      const cy = topo + k.altura / 2;
      const x = lado === 1 ? coluna[d] : -coluna[d] - k.w;
      const n = no(k, x, cy - k.h / 2, lado, ramo);
      const x1 = lado === 1 ? pai.x + pai.w : pai.x, y1 = pai.y + pai.h / 2;
      const x2 = lado === 1 ? n.x : n.x + n.w, meio = (x1 + x2) / 2;
      ligacoes.push({ id: `${pai.id}-${n.id}`, ramo, d: `M${r1(x1)} ${r1(y1)} C${r1(meio)} ${r1(y1)} ${r1(meio)} ${r1(cy)} ${r1(x2)} ${r1(cy)}` });
      const gap = GAP_Y[Math.min(k.r.nivel + 1, 3)];
      const soma = k.filhos.reduce((s, f) => s + f.altura, 0) + gap * Math.max(0, k.filhos.length - 1);
      let t = cy - soma / 2;
      for (const f of k.filhos) { colocar(f, d + 1, t, ramo, n); t += f.altura + gap; }
    };
    let topo = -alturaLado / 2;
    for (const t of topicos) {
      colocar(t, 0, topo, c.filhos.indexOf(t), centro);
      topo += t.altura + GAP_Y[1];
    }
  }
  // tudo em coordenadas positivas, com margem
  const minX = Math.min(...nos.map((n) => n.x)) - MARGEM, minY = Math.min(...nos.map((n) => n.y)) - MARGEM;
  const maxX = Math.max(...nos.map((n) => n.x + n.w)) + MARGEM, maxY = Math.max(...nos.map((n) => n.y + n.h)) + MARGEM;
  for (const n of nos) { n.x = r1(n.x - minX); n.y = r1(n.y - minY); }
  const mover = (d: string) => d.replace(/(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/g, (_, x, y) => `${r1(Number(x) - minX)} ${r1(Number(y) - minY)}`);
  return { nos, ligacoes: ligacoes.map((l) => ({ ...l, d: mover(l.d) })), largura: Math.ceil(maxX - minX), altura: Math.ceil(maxY - minY) };
}

const r1 = (v: number) => Math.round(v * 10) / 10;

/** Cores dos tópicos: legíveis no tema claro e no escuro (o preenchimento vai com opacidade baixa). */
export const CORES = ["#4f9cf9", "#e879b9", "#34c38f", "#e8a93b", "#9b87f5", "#ef6b6b", "#2bb8d1", "#f08a4b"];
export const corDo = (ramo: number) => (ramo < 0 ? CORES[0] : CORES[ramo % CORES.length]);

/** O mapa como `mindmap` do Mermaid (Obsidian, Notion, GitHub): o texto vai sem os caracteres que viram forma. */
export function paraMermaid(raiz: Ramo): string {
  const texto = (t: string) => t.replace(/[()[\]{}<>"`]/g, " ").replace(/\s+/g, " ").trim();
  const linhas = ["mindmap", `  root((${texto(raiz.texto)}))`];
  const andar = (r: Ramo, nivel: number) => {
    for (const f of r.filhos) { linhas.push(`${"  ".repeat(nivel + 1)}${texto(f.texto)}`); andar(f, nivel + 1); }
  };
  andar(raiz, 1);
  return linhas.join("\n");
}

// Mapa mental do resumo: `npm test` (node --test, sem dependência).
import assert from "node:assert/strict";
import { test } from "node:test";
import { arvore, comFilhos, desenhar, limpar, paraMermaid } from "./estudosMapa.ts";

const MD = `# Física para o ENEM

Abertura.

## 1. Cinemática
### 1.1 Equações $\\Delta s = v\\,t$
### Lançamento **oblíquo**
\`\`\`
## isto é código
\`\`\`
## 2. Óptica
### ### Lei de Snell
#### Exemplo resolvido
## Revisão rápida
### não entra
## Fontes
- x
`;

test("árvore sai dos títulos, limpa numeração, Markdown e LaTeX, e ignora código e seções que não são matéria", () => {
  const r = arvore(MD);
  assert.equal(r.texto, "Física para o ENEM");
  assert.deepEqual(r.filhos.map((f) => f.texto), ["Cinemática", "Óptica"]);
  assert.deepEqual(r.filhos[0].filhos.map((f) => f.texto), ["Equações Δs = v t", "Lançamento oblíquo"]);
  assert.equal(r.filhos[1].filhos[0].texto, "Lei de Snell");
  assert.equal(r.filhos[1].filhos[0].filhos[0].texto, "Exemplo resolvido");
  // âncora = índice entre os h2/h3/h4 renderizados (o "## isto é código" não conta; o "Revisão rápida" conta)
  assert.deepEqual([r.filhos[0].ancora, r.filhos[0].filhos[1].ancora, r.filhos[1].ancora, r.filhos[1].filhos[0].filhos[0].ancora], [0, 2, 3, 5]);
  assert.equal(arvore("## Só seção", "Tema da tela").texto, "Tema da tela");
  assert.equal(limpar("### ### 3.2 Convexos: \\mathrm{CO_2}"), "Convexos: CO2");
});

test("desenho: dois lados, sem sobreposição, e fechar um ramo esconde os filhos", () => {
  const r = arvore(MD);
  const m = desenhar(r);
  assert.equal(m.nos.length, 1 + 2 + 3 + 1);
  assert.equal(m.ligacoes.length, m.nos.length - 1);
  const raiz = m.nos.find((n) => n.nivel === 0)!;
  const lados = new Set(m.nos.filter((n) => n.nivel === 1).map((n) => n.lado));
  assert.deepEqual([...lados].sort(), [-1, 1]);
  for (const n of m.nos) {
    assert.ok(n.x >= 0 && n.y >= 0 && n.x + n.w <= m.largura && n.y + n.h <= m.altura, `${n.texto} fora do desenho`);
    if (n.lado === 1) assert.ok(n.x > raiz.x + raiz.w);
    if (n.lado === -1) assert.ok(n.x + n.w < raiz.x);
  }
  for (const a of m.nos) for (const b of m.nos) {
    if (a === b) continue;
    const separados = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
    assert.ok(separados, `${a.texto} encosta em ${b.texto}`);
  }
  const fechado = desenhar(r, new Set(comFilhos(r).filter((id) => id !== r.filhos[0].id)));
  assert.equal(fechado.nos.length, m.nos.length - 2);
  assert.equal(fechado.nos.find((n) => n.id === r.filhos[0].id)!.aberto, false);
});

test("Mermaid: mindmap com a hierarquia, sem caracteres que viram forma", () => {
  const txt = paraMermaid(arvore("# Tema (geral)\n## A [x]\n### B\n"));
  assert.equal(txt, "mindmap\n  root((Tema geral))\n    A x\n      B");
});

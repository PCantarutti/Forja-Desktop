// Teste do layout do cockpit: `npm test` (node --test, sem dependência — o Node tira os tipos sozinho).
import assert from "node:assert/strict";
import { test } from "node:test";

import { LAYOUT_PADRAO, layoutDe, mover, type Layout } from "./layout.ts";

const P = LAYOUT_PADRAO;  // colunas [arvore, maestro, worker], faixa doca embaixo

test("coluna com coluna troca de lugar e a largura vai junto", () => {
  const l = mover(P, "arvore", { tipo: "trocar", com: "worker" });
  assert.deepEqual(l.colunas, ["worker", "maestro", "arvore"]);
  assert.deepEqual(l.larguras, [30, 56, 14]);
});

test("a doca vira coluna à direita, à esquerda ou no centro", () => {
  const direita = mover(P, "doca", { tipo: "coluna", pos: 3 });
  assert.deepEqual(direita.colunas, ["arvore", "maestro", "worker", "doca"]);
  assert.equal(direita.faixa, null);
  assert.equal(direita.larguras.length, 4);
  assert.deepEqual(mover(P, "doca", { tipo: "coluna", pos: 0 }).colunas, ["doca", "arvore", "maestro", "worker"]);
  // no centro: soltar no meio da Maestro troca os papéis (a Maestro vira a faixa)
  const centro = mover(P, "doca", { tipo: "trocar", com: "maestro" });
  assert.deepEqual(centro.colunas, ["arvore", "doca", "worker"]);
  assert.equal(centro.faixa, "maestro");
});

test("coluna vira faixa em cima e a faixa atual desce para o lugar dela", () => {
  const l = mover(P, "worker", { tipo: "faixa", emCima: true });
  assert.deepEqual(l.colunas, ["arvore", "maestro", "doca"]);
  assert.equal(l.faixa, "worker");
  assert.equal(l.faixaEmCima, true);
});

test("soltar no próprio lugar não muda nada (mesmo objeto)", () => {
  assert.equal(mover(P, "maestro", { tipo: "coluna", pos: 1 }), P);
  assert.equal(mover(P, "maestro", { tipo: "coluna", pos: 2 }), P);
  assert.equal(mover(P, "doca", { tipo: "faixa", emCima: false }), P);
  assert.equal(mover(P, "arvore", { tipo: "trocar", com: "arvore" }), P);
});

test("a última coluna não vira faixa quando já não há faixa", () => {
  const so: Layout = { ...P, colunas: ["arvore"], larguras: [100], faixa: null };
  assert.equal(mover(so, "arvore", { tipo: "faixa", emCima: true }), so);
});

test("mover coluna para a direita desconta a posição que ela deixou", () => {
  const l = mover(P, "arvore", { tipo: "coluna", pos: 3 });
  assert.deepEqual(l.colunas, ["maestro", "worker", "arvore"]);
  assert.deepEqual(l.larguras, [56, 30, 14]);
});

test("conversa nova usa o padrão salvo; a salva usa o dela; inválido cai no original", () => {
  const padrao = { ...P, recolhidos: ["worker"] };
  const salvo = mover(P, "doca", { tipo: "coluna", pos: 0 });
  const todos = { _padrao: padrao, "7": salvo };
  assert.deepEqual(layoutDe("_nova", todos).recolhidos, ["worker"]);
  assert.deepEqual(layoutDe("9", todos).recolhidos, ["worker"]);  // sem layout próprio: padrão
  assert.deepEqual(layoutDe("7", todos).colunas, salvo.colunas);
  assert.deepEqual(layoutDe("_nova", {}), P);
  const faltando = { "3": { ...P, colunas: ["arvore", "maestro"], larguras: [50, 50] } };  // worker sumiu
  assert.equal(layoutDe("3", faltando), P);
});

test("layout do formato antigo (3 colunas + doca em cima) é convertido", () => {
  const antigo = { cols: [20, 50, 30], ordem: ["worker", "maestro", "arvore"], docaEmCima: true, dock: 40 };
  const l = layoutDe("5", { "5": antigo as unknown as Partial<Layout> });
  assert.deepEqual(l.colunas, ["worker", "maestro", "arvore"]);
  assert.deepEqual(l.larguras, [20, 50, 30]);
  assert.equal(l.faixa, "doca");
  assert.equal(l.faixaEmCima, true);
  assert.deepEqual(l.recolhidos, []);
});

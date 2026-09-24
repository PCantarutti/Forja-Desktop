// Teste da grade de tiles: `npm test` (node --test, sem dependência — o Node tira os tipos sozinho).
import assert from "node:assert/strict";
import { test } from "node:test";

import { GRADE_VAZIA, abrir, fechar, mover, type Grade } from "./tiles.ts";

const pilhas = (g: Grade) => g.colunas.map((c) => c.tabs);
const quatro = ["a", "b", "c", "d"].reduce((g: Grade, t) => abrir(g, t, 400), GRADE_VAZIA);

test("abre alternando lado, baixo, lado, baixo, e para em 4", () => {
  assert.deepEqual(pilhas(quatro), [["a", "b"], ["c", "d"]]);
  assert.equal(abrir(quatro, "e", 400), quatro);
  assert.equal(abrir(quatro, "a", 400), quatro);
});

test("fechar tira a coluna vazia e o recolhido", () => {
  const g = fechar(fechar({ ...quatro, recolhidos: ["c"] }, "c"), "d");
  assert.deepEqual(pilhas(g), [["a", "b"]]);
  assert.deepEqual(g.recolhidos, []);
});

test("mover para o lado, para a pilha e trocar", () => {
  assert.deepEqual(pilhas(mover(quatro, "d", "a", "esq")), [["d"], ["a", "b"], ["c"]]);
  assert.deepEqual(pilhas(mover(quatro, "a", "d", "baixo")), [["b"], ["c", "d", "a"]]);
  assert.deepEqual(pilhas(mover(quatro, "c", "c", "dir")), pilhas(quatro));
  assert.deepEqual(pilhas(mover(quatro, "a", "d", "centro")), [["d", "b"], ["c", "a"]]);
  // a coluna que ficou vazia some: a pilha do alvo não sai do lugar
  assert.deepEqual(pilhas(mover(abrir(abrir(abrir(GRADE_VAZIA, "a", 1), "b", 1), "c", 1), "c", "b", "cima")), [["a", "c", "b"]]);
});

test("fixos não fecham nem contam no limite, mas movem", () => {
  const cockpit: Grade = { colunas: [{ tabs: ["arvore"], largura: 1, alturas: [1] }, { tabs: ["maestro"], largura: 1, alturas: [1] }],
                           recolhidos: [], fixos: ["arvore", "maestro"] };
  const g = ["a", "b", "c", "d"].reduce((x: Grade, t) => abrir(x, t, 1), cockpit);
  assert.equal(abrir(g, "e", 1), g);  // 4 soltos: cheio, mesmo com os 2 fixos
  assert.equal(pilhas(abrir(g, "e", 1)).flat().length, 6);
  assert.equal(fechar(g, "maestro"), g);
  const movido = mover(g, "maestro", "a", "baixo");
  // [[arvore],[maestro,a],[b,c],[d]] → a Maestro desce para baixo do "a"
  assert.deepEqual(pilhas(movido), [["arvore"], ["a", "maestro"], ["b", "c"], ["d"]]);
  assert.deepEqual(movido.fixos, ["arvore", "maestro"]);
});

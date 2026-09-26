import { test } from "node:test";
import assert from "node:assert/strict";
import { colunasPara, distribuir } from "./mosaico.ts";

test("retrato alto empurra os próximos para as outras colunas", () => {
  // 0 é retrato (1:2), 1 e 2 são paisagem: os dois caem na coluna 1, que fica mais baixa que a 0
  assert.deepEqual(distribuir([0.5, 2, 2], 2, 0), [[0], [1, 2]]);
});

test("mesma proporção vira linhas em ordem", () => {
  assert.deepEqual(distribuir([1, 1, 1, 1, 1, 1], 3, 0), [[0, 3], [1, 4], [2, 5]]);
});

test("colunas entre 2 e 4", () => {
  assert.equal(colunasPara(300), 2);
  assert.equal(colunasPara(700), 3);
  assert.equal(colunasPara(2000), 4);
});

// Figuras do Livre: `npm test` (node --test, sem dependência).
import assert from "node:assert/strict";
import { test } from "node:test";
import { FIGURAS, N, caminho, reamostra, retangulo } from "./formasLivre.ts";

test("toda figura tem os mesmos N pontos (senão o morph do d não interpola)", () => {
  for (const [nome, f] of Object.entries(FIGURAS)) assert.equal(f.pontos.length, N, nome);
  assert.equal(retangulo(22, 15).length, N);
  // mesmo número de comandos no path
  const cmds = (d: string) => d.split("L").length;
  assert.equal(cmds(caminho(FIGURAS.estrela.pontos)), cmds(caminho(retangulo(22, 15))));
});

test("reamostrar mantém os cantos e põe os pontos extras nas arestas", () => {
  const ps = reamostra([[0, 0], [10, 0], [10, 10], [0, 10]]);
  assert.deepEqual(ps[0], [0, 0]);
  for (const canto of [[10, 0], [10, 10], [0, 10]]) assert.ok(ps.some((p) => p[0] === canto[0] && p[1] === canto[1]));
  assert.ok(ps.every(([x, y]) => x === 0 || x === 10 || y === 0 || y === 10)); // todos no contorno
});

test("figuras cabem na caixa 26 × 20", () => {
  for (const f of Object.values(FIGURAS)) assert.ok(f.pontos.every(([x, y]) => x >= 0 && x <= 26 && y >= 0 && y <= 20));
});

// Texto do resumo de Estudos: `npm test` (node --test, sem dependência).
import assert from "node:assert/strict";
import { test } from "node:test";
import { matematica, sumario } from "./estudosTexto.ts";

test("\\( \\) e \\[ \\] viram $ e $$; dinheiro não vira fórmula", () => {
  assert.equal(matematica("área \\(a^2\\) e \\[E = mc^2\\]"), "área $a^2$ e $$E = mc^2$$");
  assert.equal(matematica("custa R$ 10 ou R$20"), "custa R\\$ 10 ou R\\$20");
  assert.equal(matematica("já está $x$"), "já está $x$");
});

test("sumário pega as seções e ignora ## dentro de bloco de código", () => {
  const t = "# Título\n\n## 1. Célula\n\ntexto\n\n```\n## não\n```\n\n### sub\n\n## 2. Mitose";
  assert.deepEqual(sumario(t), ["1. Célula", "2. Mitose"]);
});

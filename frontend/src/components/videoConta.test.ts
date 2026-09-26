// Contas da aba Vídeo: `npm test` (node --test, sem dependência).
import assert from "node:assert/strict";
import { test } from "node:test";

import { duracoesDe, estimarTempo, outroLado, proporcaoPerto, quadrosDe, tamanhosDe, type Tempo } from "./videoConta.ts";

test("tamanhos saem da resolução de treino e do múltiplo da variante", () => {
  const ti2v = tamanhosDe({ multiplo: 32, resolucoes: { "480p": [832, 480], "720p": [1280, 704] } });
  assert.deepEqual(ti2v["720p"], { "16:9": [1280, 704], "9:16": [704, 1280], "1:1": [960, 960], "4:3": [928, 704] });
  assert.ok(Object.values(ti2v).every((p) => Object.values(p).flat().every((v) => v % 32 === 0)));
  // sempre as quatro qualidades, da menor para a maior; as que o modelo não treinou saem do lado menor
  assert.deepEqual(Object.keys(ti2v), ["480p", "720p", "1080p", "4K"]);
  assert.deepEqual(tamanhosDe({ multiplo: 16 })["4K"]["16:9"], [3840, 2160]);
});

test("proporção perto e o outro lado travado", () => {
  assert.equal(proporcaoPerto(832, 480), "16:9"); // 1,733: a 2,5% de 16:9
  assert.equal(proporcaoPerto(1000, 500), null);
  assert.equal(outroLado(1920, 16 / 9, "w", 16), 1088); // 1080 não é múltiplo de 16: vai para 1088
  assert.equal(outroLado(720, 16 / 9, "h", 16), 1280);
});

test("durações vão até o clipe mais longo do treino, no fps da variante", () => {
  assert.deepEqual(duracoesDe({ quadros_treino: 81 }, 16), [1, 2.5, 5]);
  assert.deepEqual(duracoesDe({ quadros_treino: 121 }, 24), [1, 2.5, 5]);
  assert.equal((quadrosDe(2.5, 16) - 1) % 4, 0);
});

const chave = "g:\\modelos-ia\\wan\\wan2.2-ti2v-5b-q8_0.gguf";
// medido na B580: 832×480, 17 quadros a 3,35 s/passo; 49 quadros a 25 s/passo
const medidos: Tempo[] = [
  { model: chave, w: 832, h: 480, frames: 17, passos: 12, s_passo: 3.35, s_total: 92 },
  { model: chave, w: 832, h: 480, frames: 49, passos: 20, s_passo: 25, s_total: 560 },
];

test("estimativa igual à medida quando o tamanho é o mesmo", () => {
  const e = estimarTempo(medidos, chave, { width: 832, height: 480, frames: 17, steps: 12, high_noise_steps: -1 });
  assert.ok(e && Math.abs(e.s - 92) < 1 && !e.minimo);
});

test("tamanho novo escala pelo expoente que as medições do modelo mostram", () => {
  const e = estimarTempo(medidos, chave, { width: 832, height: 480, frames: 33, steps: 20, high_noise_steps: -1 })!;
  // entre 17 e 49 quadros o s/passo cresceu 7,5× para 2,9× os tokens: o de 33 fica entre os dois, bem acima do linear
  const linear = 3.35 * (33 / 17) * 20;
  assert.ok(e.s > linear && e.s < 560, `${e.s}`);
  assert.equal(e.minimo, false);
});

test("com uma medição só, a estimativa vira 'pelo menos'; sem nenhuma, não há estimativa", () => {
  const e = estimarTempo(medidos.slice(0, 1), chave, { width: 1280, height: 704, frames: 17, steps: 12, high_noise_steps: -1 })!;
  assert.equal(e.minimo, true);
  assert.equal(estimarTempo(medidos, "outro.gguf", { width: 832, height: 480, frames: 17, steps: 12, high_noise_steps: -1 }), null);
});

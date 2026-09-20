/**
 * Degraus de zoom da janela, os mesmos do Chrome/Claude Desktop.
 *
 * Self-check: node electron/zoom.js
 */
const ZOOM_STEPS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5];

/**
 * Próximo zoom a partir do atual: dir +1 sobe um degrau, -1 desce, 0 volta para 100%.
 * Se o valor atual não for exatamente um degrau (arquivo editado à mão), parte do mais próximo.
 */
function nextZoom(atual, dir) {
  if (!dir) return 1;
  const perto = ZOOM_STEPS.reduce((best, v, k) => (Math.abs(v - atual) < Math.abs(ZOOM_STEPS[best] - atual) ? k : best), 0);
  return ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, Math.max(0, perto + dir))];
}

module.exports = { ZOOM_STEPS, nextZoom };

if (require.main === module) {
  const assert = require("node:assert/strict");
  assert.equal(nextZoom(1, +1), 1.1);
  assert.equal(nextZoom(1, -1), 0.9);
  assert.equal(nextZoom(1.25, 0), 1); // Ctrl+0
  assert.equal(nextZoom(0.5, -1), 0.5); // não passa do mínimo
  assert.equal(nextZoom(2.5, +1), 2.5); // nem do máximo
  assert.equal(nextZoom(1.02, +1), 1.1); // valor fora dos degraus: usa o mais próximo
  assert.equal(nextZoom(1.02, -1), 0.9);
  console.log("zoom ok");
}

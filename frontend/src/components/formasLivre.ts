// Figuras do tracejado do formato Livre, todas com os mesmos N pontos (e começando do canto de cima à
// esquerda, em sentido horário): assim o `d` do <path> interpola de uma para outra (morph em CSS).
// Caixa do desenho: 26 × 20, centro em (13, 10).

export const N = 16;
type P = [number, number];

/** Polígono de n vértices vira N pontos: mantém os cantos e espalha os que faltam pelas arestas, pelo comprimento. */
export function reamostra(vertices: P[]): P[] {
  const n = vertices.length;
  if (n >= N) return vertices.slice(0, N);
  const lados = vertices.map((a, i) => {
    const b = vertices[(i + 1) % n];
    return Math.hypot(b[0] - a[0], b[1] - a[1]);
  });
  const total = lados.reduce((s, l) => s + l, 0);
  const extra = N - n;
  // pontos a mais por aresta: proporcional ao comprimento, sobras para as de maior resto
  const cota = lados.map((l) => (l / total) * extra);
  const qt = cota.map(Math.floor);
  const ordem = cota.map((c, i) => [c - Math.floor(c), i] as const).sort((x, y) => y[0] - x[0]);
  const faltam = extra - qt.reduce((s, q) => s + q, 0);
  for (let k = 0; k < faltam; k++) qt[ordem[k][1]]++;
  const out: P[] = [];
  vertices.forEach((a, i) => {
    const b = vertices[(i + 1) % n];
    out.push(a);
    for (let j = 1; j <= qt[i]; j++) {
      const t = j / (qt[i] + 1);
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    }
  });
  return out;
}

/** N pontos em volta do centro, alternando dois raios (estrela) ou não (bola), começando no canto de cima à esquerda. */
function radial(r1: number, r2: number, giro = -135): P[] {
  return Array.from({ length: N }, (_, i) => {
    const a = ((giro + (360 / N) * i) * Math.PI) / 180;
    const r = i % 2 ? r2 : r1;
    return [13 + r * Math.cos(a), 10 + r * Math.sin(a)];
  });
}

/** Retângulo w × h centrado (o desenho normal do Livre). */
export const retangulo = (w: number, h: number): P[] =>
  reamostra([[13 - w / 2, 10 - h / 2], [13 + w / 2, 10 - h / 2], [13 + w / 2, 10 + h / 2], [13 - w / 2, 10 + h / 2]]);

export const FIGURAS: Record<string, { pontos: P[]; dentro?: string }> = {
  estrela: { pontos: radial(9.5, 4.2) },
  bola: { pontos: radial(8, 8) },
  octogono: { pontos: radial(8.6, 8.6 * Math.cos(Math.PI / 8), -112.5) },
  triangulo: { pontos: reamostra([[13, 1.5], [22.5, 17.5], [3.5, 17.5]]) },
  losango: { pontos: reamostra([[13, 1], [23, 10], [13, 19], [3, 10]]) },
  // isométrico: o contorno é um hexágono; as arestas de dentro vêm num segundo traço
  cubo: { pontos: reamostra([[6, 5.5], [13, 1.5], [20, 5.5], [20, 14.5], [13, 18.5], [6, 14.5]]), dentro: "M6 5.5 L13 9.5 L20 5.5 M13 9.5 L13 18.5" },
  paralelepipedo: { pontos: reamostra([[3, 7], [9, 3], [24, 3], [24, 13], [18, 17], [3, 17]]), dentro: "M3 7 L18 7 L24 3 M18 7 L18 17" },
};

/** Pontos → `d` de um path fechado. */
export const caminho = (ps: P[]) => "M" + ps.map(([x, y]) => `${x.toFixed(2)} ${y.toFixed(2)}`).join(" L") + " Z";

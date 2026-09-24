// Contas da aba Vídeo, sem React: tamanhos e durações a partir dos dados da variante, e a estimativa de tempo
// a partir do que a máquina já mediu. Testadas em videoConta.test.ts (`npm test`).

export type Proporcao = "16:9" | "9:16" | "1:1";
export const PROPORCOES: Proporcao[] = ["16:9", "9:16", "1:1"];
export type Tamanhos = Record<string, Record<Proporcao, [number, number]>>;

/** O que da variante (REQUISITOS no backend) entra nas contas. */
export type ReqVideo = { multiplo?: number; resolucoes?: Record<string, [number, number]>; quadros_treino?: number } | null | undefined;
export type Tempo = { model: string; w: number; h: number; frames: number; passos: number; s_passo: number; s_total: number };
export type Pedido = { width: number; height: number; frames: number; steps: number; high_noise_steps: number };

/** Mesma conta do backend (imagegen.quadros): 4k+1, porque o VAE do Wan junta 4 quadros em 1 no tempo. */
export const quadrosDe = (s: number, fps: number) => Math.max(1, Math.round((s * fps) / 4)) * 4 + 1;

/** As predefinições saem da variante (resolução de treino e múltiplo do modelo), não de uma tabela: a
 *  horizontal é a de treino, a vertical é ela deitada, e a quadrada tem a mesma área. */
export function tamanhosDe(req: ReqVideo): Tamanhos {
  const mult = req?.multiplo ?? 16;
  const encaixa = (v: number) => Math.max(mult, Math.round(v / mult) * mult);
  const out: Tamanhos = {};
  for (const [q, [w, h]] of Object.entries(req?.resolucoes ?? { "480p": [832, 480] as [number, number] })) {
    const lado = encaixa(Math.sqrt(w * h));
    out[q] = { "16:9": [encaixa(w), encaixa(h)], "9:16": [encaixa(h), encaixa(w)], "1:1": [lado, lado] };
  }
  return out;
}

/** Atalhos de duração: 1 s, metade e o clipe mais longo do treino da variante (dali para cima o Wan degrada). */
export function duracoesDe(req: ReqVideo, fps: number): number[] {
  const max = Math.round(((req?.quadros_treino ?? 81) / (fps || 16)) * 2) / 2;
  return [...new Set([1, Math.max(1, Math.round(max)) / 2, max])].filter((d) => d >= 1).sort((a, b) => a - b);
}

/** Quanto uma tomada deve levar, pelo que esta máquina já mediu com o mesmo modelo. O tempo por passo
 *  cresce com os tokens (largura × altura × quadros) num expoente que sai das próprias medições do
 *  modelo; com uma medição só, fica o linear, e a estimativa vira "pelo menos". */
export function estimarTempo(tempos: Tempo[], chave: string, o: Pedido): { s: number; minimo: boolean } | null {
  const doModelo = tempos.filter((t) => t.model === chave && t.s_passo > 0);
  if (!doModelo.length) return null;
  const tokens = (t: { w: number; h: number; frames: number }) => t.w * t.h * t.frames;
  const alvo = tokens({ w: o.width, h: o.height, frames: o.frames });
  const ref = doModelo.reduce((a, b) => (Math.abs(Math.log(tokens(b) / alvo)) < Math.abs(Math.log(tokens(a) / alvo)) ? b : a));
  const pares = doModelo.flatMap((a, i) => doModelo.slice(i + 1).filter((b) => tokens(a) !== tokens(b)).map((b) => [a, b]));
  const k = pares.length
    ? pares.reduce((soma, [a, b]) => soma + Math.log(a.s_passo / b.s_passo) / Math.log(tokens(a) / tokens(b)), 0) / pares.length
    : 1;
  const sPasso = ref.s_passo * (alvo / tokens(ref)) ** k;
  const carga = Math.max(0, ref.s_total - ref.passos * ref.s_passo); // carregar pesos, codificar, VAE
  const passos = o.steps + Math.max(0, o.high_noise_steps);
  return { s: carga + passos * sPasso, minimo: tokens(ref) !== alvo && !pares.length };
}

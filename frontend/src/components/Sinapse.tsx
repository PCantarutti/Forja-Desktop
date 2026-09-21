import type { PesquisaEstado, PesquisaFonte } from "../types";

// Grafo da pesquisa acontecendo: a pergunta no centro, uma ramificação por rodada e uma folha por
// página lida, que nasce com um "pop" e acende a aresta. Porte do researchSynapse.js do odysseus,
// mas declarativo: as posições saem do estado, não de manipulação de DOM.
// ponytail: CSS embutido no componente em vez de entrar no index.css — a aba inteira viaja num
// cherry-pick sem tocar em arquivo compartilhado.

const W = 520;
const H = 220;
const CX = W / 2;
const CY = H / 2;
const RAIO_SUB = 78;
const MAX_SUBS = 10;

const FASES: Record<PesquisaEstado["fase"], string> = {
  planejando: "montando o plano",
  buscando: "buscando",
  lendo: "lendo as fontes",
  escrevendo: "escrevendo o relatório",
  pronto: "concluído",
};

const CLASSE: Record<PesquisaFonte["status"], string> = {
  fila: "sin-folha sin-fila",
  lendo: "sin-folha sin-lendo",
  util: "sin-folha sin-util",
  vazia: "sin-folha sin-vazia",
  erro: "sin-folha sin-erro",
};

const corta = (s: string, n: number) => {
  const limpo = (s || "").replace(/\s+/g, " ").trim();
  return limpo.length > n ? limpo.slice(0, n - 1) + "…" : limpo;
};

const relogio = (seg: number) =>
  `${String(Math.floor(seg / 60)).padStart(2, "0")}:${String(Math.floor(seg % 60)).padStart(2, "0")}`;

/** Posição da ramificação de uma rodada: fatias iguais de um círculo, começando no topo. */
function posSub(i: number, total: number) {
  const fatias = Math.max(6, total);
  const ang = (i / fatias) * Math.PI * 2 - Math.PI / 2;
  return { x: CX + Math.cos(ang) * RAIO_SUB, y: CY + Math.sin(ang) * RAIO_SUB, ang };
}

/** Folhas em leque ao redor da sua ramificação; a cada 6 abre um anel mais externo. */
function posFolha(sub: { x: number; y: number }, idx: number) {
  const base = Math.atan2(sub.y - CY, sub.x - CX);
  const anel = Math.floor(idx / 6);
  const vaga = idx % 6;
  const ang = base + (vaga - 2.5) * (2.4 / 6);
  const r = 26 + anel * 14;
  return { x: sub.x + Math.cos(ang) * r, y: sub.y + Math.sin(ang) * r };
}

export default function Sinapse({ estado }: { estado: PesquisaEstado }) {
  const rodadas = Math.max(estado.rodadas.length, 1);
  const subs = Array.from({ length: Math.min(rodadas, MAX_SUBS) }, (_, i) => posSub(i, rodadas));
  const porRodada = new Map<number, number>();

  return (
    <div className={`sin ${estado.status !== "rodando" ? "sin-parado" : ""}`}>
      <style>{CSS}</style>
      <div className="sin-palco">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
          <g className="sin-arestas">
            {subs.map((s, i) => <line key={`e${i}`} x1={CX} y1={CY} x2={s.x} y2={s.y} className="sin-aresta" />)}
            {estado.fontes.map((f) => {
              const sub = subs[Math.min(Math.max(f.rodada, 1), subs.length) - 1] ?? subs[0];
              const idx = porRodada.get(f.rodada) ?? 0;
              const p = posFolha(sub, idx);
              porRodada.set(f.rodada, idx + 1);
              return <line key={`ef${f.id}`} x1={sub.x} y1={sub.y} x2={p.x} y2={p.y} className="sin-aresta" />;
            })}
          </g>
          <g className="sin-nos">
            {subs.map((s, i) => (
              <g key={`s${i}`}>
                <circle cx={s.x} cy={s.y} r={7} className="sin-sub sin-novo" />
                <text
                  x={CX + Math.cos(s.ang) * (RAIO_SUB + 14)}
                  y={CY + Math.sin(s.ang) * (RAIO_SUB + 14) + 3}
                  textAnchor={Math.cos(s.ang) > 0.15 ? "start" : Math.cos(s.ang) < -0.15 ? "end" : "middle"}
                  className="sin-rotulo sin-rotulo-sub"
                >
                  R{i + 1}
                </text>
              </g>
            ))}
            {(() => {
              const contagem = new Map<number, number>();
              return estado.fontes.map((f) => {
                const sub = subs[Math.min(Math.max(f.rodada, 1), subs.length) - 1] ?? subs[0];
                const idx = contagem.get(f.rodada) ?? 0;
                const p = posFolha(sub, idx);
                contagem.set(f.rodada, idx + 1);
                return <circle key={f.id} cx={p.x} cy={p.y} r={4} className={`${CLASSE[f.status]} sin-novo`}>
                  <title>{f.titulo}</title>
                </circle>;
              });
            })()}
            <circle cx={CX} cy={CY} r={11} className="sin-raiz" />
            <text x={CX} y={CY + 28} textAnchor="middle" className="sin-rotulo">{corta(estado.pergunta, 30)}</text>
          </g>
          <circle cx={CX} cy={CY} r={6} className="sin-pulso" />
        </svg>
      </div>
      <div className="sin-meta">
        <span className="sin-fase">{FASES[estado.fase] ?? estado.fase}</span>
        <span className="sin-sep">·</span>
        <span>rodada <b>{estado.rodada || 0}</b> de {estado.rodadas.length || "?"}</span>
        <span className="sin-sep">·</span>
        <span><b>{estado.stats.uteis}</b> fontes úteis de {estado.stats.fontes}</span>
        <span className="sin-sep">·</span>
        <span>{relogio(estado.stats.segundos)}</span>
      </div>
    </div>
  );
}

const CSS = `
.sin { border: 1px solid var(--color-line); border-radius: 1rem; overflow: hidden;
  background: radial-gradient(ellipse at center, rgba(125,211,252,0.07) 0%, transparent 70%), var(--color-surface); }
.sin-palco { height: 200px; }
.sin svg { display: block; width: 100%; height: 100%; }
.sin-aresta { stroke: var(--color-line); stroke-width: 1.2; fill: none; opacity: 0.55; }
.sin-raiz { fill: #7dd3fc; stroke: #7dd3fc; stroke-width: 1.5; }
.sin-sub { fill: var(--color-surface); stroke: #7dd3fc; stroke-width: 1.5; }
.sin-folha { stroke-width: 1.4; }
.sin-fila { fill: var(--color-raised); stroke: var(--color-faint); }
.sin-lendo { fill: rgba(125,211,252,0.3); stroke: #7dd3fc; animation: sin-piscar 1.2s ease-in-out infinite; }
.sin-util { fill: rgba(110,231,183,0.35); stroke: #6ee7b7; }
.sin-vazia { fill: var(--color-raised); stroke: var(--color-faint); opacity: 0.6; }
.sin-erro { fill: rgba(248,113,113,0.25); stroke: #f87171; }
.sin-novo { animation: sin-pop 0.5s ease-out; transform-box: fill-box; transform-origin: center; }
.sin-rotulo { fill: var(--color-muted); font-size: 10px; font-family: ui-monospace, monospace; opacity: 0.9; }
.sin-rotulo-sub { font-size: 9px; opacity: 0.65; }
.sin-pulso { fill: #7dd3fc; opacity: 0; animation: sin-pulso 2.6s ease-out infinite;
  transform-box: fill-box; transform-origin: center; }
.sin-parado .sin-pulso, .sin-parado .sin-lendo { animation: none; }
.sin-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 6px 12px 8px;
  font-family: ui-monospace, monospace; font-size: 11px; color: var(--color-muted);
  border-top: 1px solid var(--color-line); }
.sin-meta b { color: var(--color-fg); font-weight: 600; }
.sin-fase { color: #7dd3fc; font-weight: 600; }
.sin-sep { opacity: 0.4; }
@keyframes sin-pop { 0% { transform: scale(0); opacity: 0; } 60% { transform: scale(1.25); opacity: 1; }
  100% { transform: scale(1); opacity: 1; } }
@keyframes sin-pulso { 0% { transform: scale(1); opacity: 0.6; } 100% { transform: scale(5); opacity: 0; } }
@keyframes sin-piscar { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
@media (prefers-reduced-motion: reduce) {
  .sin-pulso, .sin-novo, .sin-lendo { animation: none; }
}
`;

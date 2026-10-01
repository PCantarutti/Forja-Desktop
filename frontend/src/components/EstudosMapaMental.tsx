import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, Minus, Plus } from "./icons";
import { type NoMapa, arvore, comFilhos, corDo, desenhar, fonteDe, paraMermaid } from "./estudosMapa";
import { btn } from "./estudosUi";

type Vista = { x: number; y: number; k: number };
const K_MIN = 0.25, K_MAX = 2.5, K_PISO = 0.7;

/** O resumo como mapa mental: o tema no meio, os tópicos dos dois lados. Clicar no texto de um nó abre a seção
 *  no resumo; a bolinha na ponta abre e fecha o ramo. Roda do mouse dá zoom no cursor, arrastar move. */
export default function EstudosMapaMental({ md, tema, onAbrir }: { md: string; tema: string; onAbrir: (ancora: number, texto: string) => void }) {
  const raiz = useMemo(() => arvore(md, tema), [md, tema]);
  const todos = useMemo(() => comFilhos(raiz), [raiz]);
  const [abertos, setAbertos] = useState<Set<string>>(() => new Set(todos));
  // o resumo cresce enquanto é escrito: ramo novo chega aberto (o que a pessoa fechou continua fechado)
  const vistos = useRef(new Set(todos));
  useEffect(() => {
    const novos = todos.filter((id) => !vistos.current.has(id));
    novos.forEach((id) => vistos.current.add(id));
    if (novos.length) setAbertos((a) => new Set([...a, ...novos]));
  }, [todos]);
  const mapa = useMemo(() => desenhar(raiz, abertos), [raiz, abertos]);

  const caixa = useRef<HTMLDivElement>(null);
  const [vista, setVista] = useState<Vista | null>(null);
  const [copiado, setCopiado] = useState(false);
  const arrasto = useRef<{ x: number; y: number; vx: number; vy: number; moveu: boolean } | null>(null);

  const ajustar = useCallback(() => {
    const el = caixa.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    // cabe inteiro se der; abaixo de 70% a letra fica miúda — aí fica nos 70%, centrado no tema, e arrasta-se
    const k = Math.max(K_PISO, Math.min(1.1, width / mapa.largura, height / mapa.altura));
    const raiz_ = mapa.nos.find((n) => n.nivel === 0)!;
    setVista({ k, x: width / 2 - (raiz_.x + raiz_.w / 2) * k, y: height / 2 - (raiz_.y + raiz_.h / 2) * k });
  }, [mapa]);
  useLayoutEffect(() => { if (!vista) ajustar(); }, [vista, ajustar]);
  // abrir ou fechar um ramo recompõe o desenho (as coordenadas recomeçam do canto): o tema fica parado na tela
  const centroAntes = useRef<{ x: number; y: number } | null>(null);
  const focar = useRef<string | null>(null);
  useLayoutEffect(() => {
    const r = mapa.nos.find((n) => n.nivel === 0)!;
    const antes = centroAntes.current;
    centroAntes.current = { x: r.x, y: r.y };
    // vista nula neste desenho = acabou de ser ajustada à tela (o "ajustar" já centrou): não desloca de novo
    if (!vista) return;
    let { x, y } = vista;
    if (antes) { x -= (r.x - antes.x) * vista.k; y -= (r.y - antes.y) * vista.k; }
    // o ramo que acabou de abrir: desliza o mínimo para ele e os filhos caberem na tela
    const id = focar.current;
    focar.current = null;
    const el = caixa.current;
    if (id && el) {
      const grupo = mapa.nos.filter((n) => n.id === id || mapa.ligacoes.some((l) => l.id === `${id}-${n.id}`));
      const { width, height } = el.getBoundingClientRect();
      const m = 16, k = vista.k;
      const x0 = Math.min(...grupo.map((n) => n.x)) * k + x, x1 = Math.max(...grupo.map((n) => n.x + n.w)) * k + x;
      const y0 = Math.min(...grupo.map((n) => n.y)) * k + y, y1 = Math.max(...grupo.map((n) => n.y + n.h)) * k + y;
      if (x1 > width - m) x -= Math.min(x1 - (width - m), x0 - m);
      else if (x0 < m) x += Math.min(m - x0, width - m - x1);
      if (y1 > height - m) y -= Math.min(y1 - (height - m), y0 - m);
      else if (y0 < m) y += Math.min(m - y0, height - m - y1);
    }
    if (x !== vista.x || y !== vista.y) setVista({ ...vista, x, y });
  }, [mapa]);   // eslint-disable-line react-hooks/exhaustive-deps

  const zoom = (fator: number, cx?: number, cy?: number) => setVista((v) => {
    if (!v || !caixa.current) return v;
    const r = caixa.current.getBoundingClientRect();
    const px = cx ?? r.width / 2, py = cy ?? r.height / 2;
    const k = Math.min(K_MAX, Math.max(K_MIN, v.k * fator));
    return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
  });
  // a roda é passiva no React: o listener nativo é o que pode impedir a página de rolar junto
  useEffect(() => {
    const el = caixa.current;
    if (!el) return;
    const roda = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoom(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", roda, { passive: false });
    return () => el.removeEventListener("wheel", roda);
  }, []);

  const alternar = (id: string) => {
    if (!abertos.has(id)) focar.current = id;
    setAbertos((a) => {
      const n = new Set(a);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };
  const tudoAberto = todos.every((id) => abertos.has(id));

  async function copiar() {
    await navigator.clipboard.writeText(paraMermaid(raiz));
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1500);
  }

  const v = vista ?? { x: 0, y: 0, k: 1 };
  return (
    <div className="relative overflow-hidden rounded-2xl border border-line bg-surface">
      <div ref={caixa} className="h-[min(72vh,760px)] cursor-grab touch-none select-none active:cursor-grabbing"
           onPointerDown={(e) => {
             if ((e.target as Element).closest("[data-no]")) return;
             (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
             arrasto.current = { x: e.clientX, y: e.clientY, vx: v.x, vy: v.y, moveu: false };
           }}
           onPointerMove={(e) => {
             const a = arrasto.current;
             if (!a) return;
             a.moveu ||= Math.abs(e.clientX - a.x) + Math.abs(e.clientY - a.y) > 3;
             setVista({ k: v.k, x: a.vx + e.clientX - a.x, y: a.vy + e.clientY - a.y });
           }}
           onPointerUp={() => { arrasto.current = null; }}
           onDoubleClick={(e) => { if (!(e.target as Element).closest("[data-no]")) ajustar(); }}>
        <svg width="100%" height="100%" role="img" aria-label={`Mapa mental: ${raiz.texto}`}>
          <g transform={`translate(${v.x} ${v.y}) scale(${v.k})`}>
            {mapa.ligacoes.map((l) => (
              <path key={l.id} d={l.d} fill="none" stroke={corDo(l.ramo)} strokeOpacity={0.55} strokeWidth={l.id.startsWith("raiz-") ? 2.4 : 1.6} />
            ))}
            {mapa.nos.map((n) => <No key={n.id} n={n} onAbrir={onAbrir} onAlternar={alternar} />)}
          </g>
        </svg>
      </div>
      <div className="absolute top-2.5 right-2.5 flex items-center gap-1.5 text-xs">
        <button className={btn} onClick={() => { setAbertos(tudoAberto ? new Set() : new Set(todos)); setVista(null); }}
                title={tudoAberto ? "Mostrar só os tópicos" : "Mostrar todos os subtópicos"}>
          {tudoAberto ? "Só os tópicos" : "Abrir tudo"}
        </button>
        <button className={btn} onClick={copiar} title="O mapa como mindmap do Mermaid (Obsidian, Notion, GitHub)">
          {copiado ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} Mermaid
        </button>
        <div className="flex overflow-hidden rounded-[9px] border border-line bg-raised">
          <button className="px-2 py-1.5 text-muted hover:text-fg" onClick={() => zoom(1 / 1.25)} title="Diminuir" aria-label="Diminuir">
            <Minus className="size-3.5" />
          </button>
          <button className="border-x border-line px-2 py-1.5 font-mono text-muted hover:text-fg" onClick={ajustar} title="Ajustar à tela (ou duplo clique no fundo)">
            {Math.round(v.k * 100)}%
          </button>
          <button className="px-2 py-1.5 text-muted hover:text-fg" onClick={() => zoom(1.25)} title="Aumentar" aria-label="Aumentar">
            <Plus className="size-3.5" />
          </button>
        </div>
      </div>
      <p className="pointer-events-none absolute bottom-2 left-3 text-[11px] text-faint">
        clique num nó para ler a seção · a bolinha abre e fecha o ramo · roda dá zoom, arraste para mover
      </p>
    </div>
  );
}

function No({ n, onAbrir, onAlternar }: { n: NoMapa; onAbrir: (ancora: number, texto: string) => void; onAlternar: (id: string) => void }) {
  const cor = corDo(n.ramo);
  const raiz = n.nivel === 0, topico = n.nivel === 1;
  const fonte = fonteDe(n.nivel);
  const abrir = () => (raiz ? undefined : onAbrir(n.ancora, n.texto));
  // a bolinha fica na ponta de fora do nó (onde os filhos saem)
  const bx = n.lado === -1 ? n.x - 9 : n.x + n.w + 9, by = n.y + n.h / 2;
  return (
    <g data-no>
      <g role={raiz ? undefined : "button"} tabIndex={raiz ? -1 : 0} aria-label={raiz ? undefined : `Ler: ${n.texto}`}
         onClick={abrir} onKeyDown={(e) => {
           if (e.key === "Enter") abrir();
           else if (e.key === " " && n.filhos) { e.preventDefault(); onAlternar(n.id); }
         }}
         className={`${raiz ? "" : "cursor-pointer"} outline-none [&:focus-visible>rect]:stroke-[3px]`}>
        <title>{raiz ? n.texto : `${n.texto} — clique para ler`}</title>
        <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={raiz ? n.h / 2 : topico ? 10 : 7}
              fill={raiz ? "var(--color-accent)" : topico ? cor : "var(--color-raised)"} fillOpacity={raiz ? 1 : topico ? 0.16 : 1}
              stroke={raiz ? "none" : cor} strokeOpacity={topico ? 1 : 0.45} strokeWidth={topico ? 1.5 : 1}
              className={raiz ? "" : "transition-[fill-opacity] hover:fill-opacity-30"} />
        <text x={n.x + n.w / 2} y={n.y + n.h / 2} textAnchor="middle" dominantBaseline="central" fontSize={fonte}
              fontWeight={raiz ? 650 : topico ? 600 : 450} fill={raiz ? "var(--color-accent-fg)" : "var(--color-fg)"}>
          {n.linhas.map((l, i) => (
            <tspan key={i} x={n.x + n.w / 2} dy={i === 0 ? -((n.linhas.length - 1) * fonte * 1.3) / 2 : fonte * 1.3}>{l}</tspan>
          ))}
        </text>
      </g>
      {!raiz && n.filhos > 0 && (
        <g role="button" tabIndex={0} aria-label={n.aberto ? `Fechar ${n.texto}` : `Abrir ${n.texto} (${n.filhos})`} className="cursor-pointer outline-none [&:focus-visible>circle:last-of-type]:stroke-[3px]"
           onClick={() => onAlternar(n.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onAlternar(n.id); } }}>
          <title>{n.aberto ? "Fechar o ramo" : `Abrir: ${n.filhos} subtópico${n.filhos === 1 ? "" : "s"}`}</title>
          <circle cx={bx} cy={by} r={8} fill="var(--color-surface)" stroke={cor} strokeWidth={1.5} />
          <text x={bx} y={by} textAnchor="middle" dominantBaseline="central" fontSize={n.aberto ? 12 : 9.5} fontWeight={600} fill={cor}>
            {n.aberto ? "−" : n.filhos}
          </text>
        </g>
      )}
    </g>
  );
}

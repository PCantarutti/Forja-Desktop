import { useEffect, useRef, useState } from "react";
import { Activity, Clipboard, Cpu, Expandir, GitBranch, Globe, Info, Recolher, Terminal, X } from "./icons";
import { MAX_TILES, abertos, fechar, mover, type Grade, type Lado } from "./tiles";

export type RightTab = "info" | "browser" | "servers" | "plans" | "changes" | "terminal" | "local";

// Largura inicial da coluna que o tile abre; arrastar o divisor à esquerda dela muda.
export const WIDTH: Record<RightTab, number> = { info: 288, browser: 520, servers: 288, plans: 440, changes: 520, terminal: 560, local: 420 };
const TILE_MIN = 240, MAIN_MIN = 320;
const card = "rounded-xl border border-line";
const tileCard = `${card} bg-side`;

export const TABS: { id: RightTab; label: string; icon: React.ReactNode }[] = [
  { id: "info", label: "Info", icon: <Info className="size-4" /> },
  { id: "browser", label: "Navegador", icon: <Globe className="size-4" /> },
  { id: "terminal", label: "Terminal", icon: <Terminal className="size-4" /> },
  { id: "changes", label: "Alterações", icon: <GitBranch className="size-4" /> },
  { id: "servers", label: "Instâncias", icon: <Activity className="size-4" /> },
  { id: "local", label: "IA local", icon: <Cpu className="size-4" /> },
  { id: "plans", label: "Planos", icon: <Clipboard className="size-4" /> },
];

/** O que cada aba sinaliza: ponto verde (navegador aberto, modelo local no ar) ou contador. */
export type EstadoAbas = {
  browserOpen: boolean;
  serversRunning: number;
  plansPending: number; // planos esperando decisão do usuário
  plansTotal: number;
  changesCount: number; // arquivos alterados pelo agente nesta conversa
  localRunning: boolean; // modelo local carregado no llama-server
};

/** O selo de uma aba. Compartilhado com a doca do Maestro: os dois lugares sinalizam igual. */
export function seloDaAba(id: RightTab, e: EstadoAbas): React.ReactNode {
  const n = (v: number, tom = "bg-accent text-accent-fg") => (
    <span className={`h-[15px] min-w-[15px] rounded-full px-1 text-center font-mono text-[9.5px] leading-[15px] font-semibold ${tom}`}>{v}</span>
  );
  const ponto = <span className="mt-1.5 mr-1.5 size-1.5 rounded-full bg-ok" />;
  return id === "changes" && e.changesCount > 0 ? n(e.changesCount)
    : id === "browser" && e.browserOpen ? ponto
    : id === "local" && e.localRunning ? ponto
    : id === "servers" && e.serversRunning > 0 ? n(e.serversRunning)
    : id === "plans" && e.plansPending > 0 ? n(e.plansPending)
    : id === "plans" && e.plansTotal > 0 ? n(e.plansTotal, "bg-raised text-muted")
    : null;
}

/** Botões das abas, sempre visíveis no topo direito. Clicar abre um tile daquela aba à direita (os
 * outros continuam abertos, como no Claude Desktop), até MAX_TILES; clicar de novo fecha. */
export function RightTabsBar(props: EstadoAbas & {
  abertos: string[];
  onSelect: (t: string) => void;
  extras?: { id: string; label: string; icon: React.ReactNode }[];  // abas só de uma tela (a Maestro)
}) {
  return (
    <div className="ml-auto flex shrink-0 items-center gap-0.5" role="toolbar" aria-label="Painéis">
      {[...TABS, ...(props.extras ?? [])].map((t) => {
        const active = props.abertos.includes(t.id);
        const cheio = !active && props.abertos.length >= MAX_TILES;
        const b = seloDaAba(t.id as RightTab, props);
        return (
          <button
            key={t.id}
            aria-pressed={active}
            title={active ? `${t.label} (clique para fechar)` : cheio ? `${t.label}: feche um painel antes (máximo ${MAX_TILES})` : t.label}
            disabled={cheio}
            onClick={() => props.onSelect(t.id)}
            className={`relative grid size-[30px] place-items-center rounded-[7px] transition-colors disabled:opacity-35 ${
              active ? "bg-raised text-accent-text" : "text-muted hover:bg-raised hover:text-fg"
            }`}
          >
            {t.icon}
            {b && <span className={`absolute grid place-items-center ${t.id === "browser" || t.id === "local" ? "top-0 right-0" : "-top-[3px] -right-[3px]"}`}>{b}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** Faixa de arrasto entre dois blocos. Só reporta o deslocamento; quem sabe o que fazer é o pai. */
export function Divisor(props: { eixo: "x" | "y"; onArrasto: (e: PointerEvent) => void; onFim: () => void }) {
  return (
    <div
      role="separator"
      aria-orientation={props.eixo === "x" ? "vertical" : "horizontal"}
      onPointerDown={(e) => {
        e.preventDefault();
        const mover = (ev: PointerEvent) => props.onArrasto(ev);
        const soltar = () => {
          window.removeEventListener("pointermove", mover);
          window.removeEventListener("pointerup", soltar);
          document.body.style.cursor = "";
          document.body.style.userSelect = "";
          props.onFim();
        };
        // cursor e seleção no body: arrastando rápido o ponteiro sai da faixa estreita
        document.body.style.cursor = props.eixo === "x" ? "col-resize" : "row-resize";
        document.body.style.userSelect = "none";
        window.addEventListener("pointermove", mover);
        window.addEventListener("pointerup", soltar);
      }}
      className={`group shrink-0 ${props.eixo === "x" ? "w-2 cursor-col-resize" : "h-2 cursor-row-resize"} flex items-center justify-center`}
    >
      <div className={`rounded-full bg-line transition-colors group-hover:bg-muted ${props.eixo === "x" ? "h-8 w-0.5" : "h-0.5 w-8"}`} />
    </div>
  );
}

type Caixa = { left: number; top: number; width: number; height: number };
/** Rótulo de um item da grade: o que aparece no cabeçalho e na barra recolhida. */
export type Rotulo = { label: string; icon?: React.ReactNode; resumo?: string; ativo?: boolean };
/** O que um item com cabeçalho próprio recebe para pôr no dele: a alça de arrastar e o botão de recolher. */
export type Cabeca = { alca: React.HTMLAttributes<HTMLDivElement>; acao: React.ReactNode };

const COL_MIN = 160;  // sem principal (a Maestro): colunas mais estreitas que um tile, como a árvore

/** A área de conteúdo em tiles: o conteúdo principal num card e, à direita, colunas de tiles (ver
 * tiles.ts). Sem `children` (a Maestro) não há principal: as colunas dividem a tela entre si, por peso.
 * Divisores mudam larguras e alturas; o cabeçalho de um tile arrasta ele para outro lugar; recolhido,
 * ele vira uma barra fina. */
export default function Tiles(props: {
  soPrincipal?: boolean;  // a tela desenha a própria grade (a Maestro): aqui só o principal, sem tiles
  grade: Grade<string>;
  onGrade: (g: Grade<string>) => void;
  painel: (t: string, cabeca: Cabeca) => React.ReactNode;
  rotulo?: (t: string) => Rotulo | undefined;  // fora das abas do topo (TABS)
  proprio?: (t: string) => boolean;             // desenha o próprio cabeçalho (os blocos da Maestro)
  children?: React.ReactNode;
}) {
  // Durante um arrasto de divisor a grade vive aqui (o App inteiro não redesenha a cada pixel) e só
  // sobe no fim.
  const [temp, setTemp] = useState<Grade<string> | null>(null);
  const g = temp ?? props.grade;
  const atual = useRef(g);
  atual.current = g;
  const area = useRef<HTMLDivElement>(null);
  const [arrastando, setArrastando] = useState(false);
  const [indicador, setIndicador] = useState<{ caixa: Caixa; centro: boolean } | null>(null);

  // Abrir/fechar anima pela posição onde o tile cai: coluna nova desliza do lado; empilhado numa coluna
  // que já existe, de baixo. (A ordem não importa: na Maestro o primeiro já cai embaixo do Worker.)
  type Dir = "lado" | "baixo";
  const entrada = useRef(new Map<string, Dir>());
  const vistos = useRef<Set<string> | null>(null);
  if (vistos.current === null) vistos.current = new Set(abertos(props.grade));  // os do começo não animam
  {
    const agora = new Set(abertos(props.grade));
    for (const c of props.grade.colunas)
      for (const t of c.tabs)
        if (!vistos.current.has(t)) entrada.current.set(t, c.tabs.some((x) => vistos.current!.has(x)) ? "baixo" : "lado");
    vistos.current = agora;
  }
  useEffect(() => {
    if (!entrada.current.size) return;
    const ks = [...entrada.current.keys()];
    setTimeout(() => ks.forEach((k) => entrada.current.delete(k)), 400);  // arrastar depois não repete a entrada
  });
  // Fechar: a grade anterior fica na tela enquanto o tile sai (vale para o X e para os ícones do topo).
  const [saindo, setSaindo] = useState<{ grade: Grade<string>; tabs: Map<string, Dir> } | null>(null);
  const anterior = useRef(props.grade);
  useEffect(() => {
    const prev = anterior.current;
    anterior.current = props.grade;
    const agora = new Set(abertos(props.grade));
    const foram = new Map<string, Dir>();
    for (const c of prev.colunas) for (const t of c.tabs) if (!agora.has(t)) foram.set(t, c.tabs.length === 1 ? "lado" : "baixo");
    if (!foram.size) return;
    setSaindo({ grade: prev, tabs: foram });
    const id = setTimeout(() => setSaindo(null), 140);
    return () => {
      clearTimeout(id);
      setSaindo(null);
    };
  }, [props.grade]);
  const gv = saindo?.grade ?? g;  // a que aparece: a anterior enquanto um tile sai

  if (props.soPrincipal) return <div className="flex min-h-0 min-w-0 flex-1 flex-col">{props.children}</div>;

  const semPrincipal = props.children === undefined;
  const info = (t: string): Rotulo => props.rotulo?.(t) ?? TABS.find((x) => x.id === t) ?? { label: t };
  const rec = (t: string) => g.recolhidos.includes(t);
  const fim = () => {
    props.onGrade(atual.current);
    setTemp(null);
  };
  const recolher = (t: string, sim: boolean) =>
    props.onGrade({ ...g, recolhidos: sim ? [...g.recolhidos, t] : g.recolhidos.filter((x) => x !== t) });
  const colEl = (ci: number) => area.current?.querySelectorAll<HTMLElement>(":scope > [data-coluna]")[ci];

  function largura(ci: number, e: PointerEvent) {
    const col = colEl(ci), box = area.current?.getBoundingClientRect();
    if (!col || !box) return;
    if (semPrincipal) {
      // Sem principal as larguras são pesos: o divisor divide o par vizinho, como no cockpit antigo.
      // Os pesos viram as larguras de agora em px, e a soma do par não muda.
      const esq = colEl(ci - 1);
      if (!esq) return;
      const pesos = atual.current.colunas.map((_, j) => colEl(j)?.getBoundingClientRect().width ?? 0);
      const a = esq.getBoundingClientRect(), par = pesos[ci - 1] + pesos[ci];
      pesos[ci - 1] = Math.min(Math.max(e.clientX - a.left, COL_MIN), par - COL_MIN);
      pesos[ci] = par - pesos[ci - 1];
      setTemp({ ...atual.current, colunas: atual.current.colunas.map((c, j) => ({ ...c, largura: Math.round(pesos[j]) })) });
      return;
    }
    const max = box.width - MAIN_MIN - (g.colunas.length - 1) * (TILE_MIN + 8);
    const w = Math.round(Math.max(TILE_MIN, Math.min(max, col.getBoundingClientRect().right - e.clientX)));
    const colunas = atual.current.colunas.map((c, j) => (j === ci ? { ...c, largura: w } : c));
    setTemp({ ...atual.current, colunas });
  }

  function altura(ci: number, i: number, e: PointerEvent) {
    const els = colEl(ci)?.querySelectorAll<HTMLElement>(":scope > [data-tile]");
    if (!els?.[i + 1]) return;
    const cima = els[i].getBoundingClientRect(), baixo = els[i + 1].getBoundingClientRect();
    const colunas = atual.current.colunas.map((c, j) => {
      if (j !== ci) return c;
      const alturas = [...c.alturas], par = alturas[i] + alturas[i + 1];
      const pos = ((e.clientY - cima.top) / (baixo.bottom - cima.top)) * par;
      alturas[i] = Math.min(Math.max(pos, par * 0.15), par * 0.85);
      alturas[i + 1] = par - alturas[i];
      return { ...c, alturas };
    });
    setTemp({ ...atual.current, colunas });
  }

  // Onde o tile cai: sob o ponteiro, as bordas do outro tile abrem coluna ao lado ou entram na pilha
  // dele; o meio troca os dois de lugar.
  function alvoEm(t: string, x: number, y: number): { com: string; lado: Lado; caixa: Caixa } | null {
    const a = area.current?.getBoundingClientRect();
    const el = document.elementsFromPoint(x, y).map((e) => (e as HTMLElement).closest<HTMLElement>("[data-tile]")).find(Boolean);
    const com = el?.dataset.tile;
    if (!a || !el || !com || com === t) return null;
    const r = el.getBoundingClientRect();
    const fx = (x - r.left) / r.width, fy = (y - r.top) / r.height;
    const lado: Lado = fx < 0.25 ? "esq" : fx > 0.75 ? "dir" : fy < 0.3 ? "cima" : fy > 0.7 ? "baixo" : "centro";
    const c = { left: r.left - a.left, top: r.top - a.top, width: r.width, height: r.height };
    const caixa = lado === "esq" ? { ...c, left: c.left - 2, width: 4 } : lado === "dir" ? { ...c, left: c.left + c.width - 2, width: 4 }
      : lado === "cima" ? { ...c, top: c.top - 2, height: 4 } : lado === "baixo" ? { ...c, top: c.top + c.height - 2, height: 4 } : c;
    return { com, lado, caixa };
  }

  /** Cabeçalho do tile (ou a barra dele, recolhido): segurar e arrastar muda de lugar. */
  const alca = (t: string): React.HTMLAttributes<HTMLDivElement> => ({
    style: { cursor: arrastando ? "grabbing" : "grab", userSelect: "none" },
    onPointerDown: (e) => {
      if (e.button !== 0) return;
      const tile = e.currentTarget.closest<HTMLElement>("[data-tile]");
      if (!tile) return;
      const x0 = e.clientX, y0 = e.clientY, estilo = tile.style.cssText;
      let ativo = false;
      let ultimo: ReturnType<typeof alvoEm> = null;
      const mexe = (ev: PointerEvent) => {
        const dx = ev.clientX - x0, dy = ev.clientY - y0;
        if (!ativo) {
          if (Math.hypot(dx, dy) < 6) return;  // clique comum num botão do cabeçalho não vira arrasto
          ativo = true;
          setArrastando(true);
          document.body.style.userSelect = "none";
          tile.style.cssText = estilo + ";position:relative;z-index:50;pointer-events:none;opacity:.94;scale:1.025;"
            + "transition:scale .15s ease-out;box-shadow:0 28px 60px -12px rgb(0 0 0/.65),0 0 0 1px var(--color-accent-line);";
        }
        tile.style.setProperty("translate", `${dx}px ${dy}px`);
        ultimo = alvoEm(t, ev.clientX, ev.clientY);
        setIndicador(ultimo && { caixa: ultimo.caixa, centro: ultimo.lado === "centro" });
      };
      const solta = () => {
        window.removeEventListener("pointermove", mexe);
        window.removeEventListener("pointerup", solta);
        window.removeEventListener("pointercancel", solta);
        if (!ativo) return;
        tile.style.cssText = estilo;
        document.body.style.userSelect = "";
        // o clique que o navegador dispara depois do arrasto não pode acionar o botão sob o ponteiro
        const engole = (c: MouseEvent) => c.stopPropagation();
        window.addEventListener("click", engole, { capture: true, once: true });
        setTimeout(() => window.removeEventListener("click", engole, { capture: true }), 0);
        setArrastando(false);
        setIndicador(null);
        if (ultimo) props.onGrade(mover(atual.current, t, ultimo.com, ultimo.lado));
      };
      window.addEventListener("pointermove", mexe);
      window.addEventListener("pointerup", solta);
      window.addEventListener("pointercancel", solta);
    },
  });

  const botaoRecolher = (t: string) => (
    <button onClick={() => recolher(t, true)} title={`Recolher ${info(t).label} numa barra fina`}
            className="ml-auto shrink-0 rounded-md p-0.5 text-faint hover:bg-raised hover:text-fg">
      <Recolher className="size-3.5" />
    </button>
  );

  const barra = (t: string, vertical: boolean) => {
    const r = info(t);
    return (
      <div {...alca(t)} onClick={() => recolher(t, false)} title={`${r.label} — clique para abrir, segure para mudar de lugar`}
           className={`flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-faint hover:bg-raised hover:text-fg ${
             vertical ? "h-full flex-col py-2" : "h-8 px-3"}`}>
        <Expandir className="size-3.5 shrink-0" />
        {r.ativo && <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-sky-400" />}
        <span className={vertical ? "[writing-mode:vertical-rl]" : ""}>{r.label}</span>
        {r.resumo && <span className={`normal-case text-muted ${vertical ? "[writing-mode:vertical-rl]" : ""}`}>{r.resumo}</span>}
      </div>
    );
  };

  // overflow-clip: o tile deslizando (entrar/sair) passa da borda; sem isso a página ganha rolagem por um instante
  return (
    <div ref={area} className="relative flex min-h-0 min-w-0 flex-1 overflow-clip p-2 pt-0">
      {!semPrincipal && (
        <main className={`${card} flex min-w-0 flex-1 flex-col overflow-hidden`} style={{ minWidth: MAIN_MIN }}>
          {props.children}
        </main>
      )}
      {gv.colunas.map((c, ci) => {
        const fechada = c.tabs.every(rec);  // coluna toda recolhida: vira uma faixa fina em pé
        const vizinhaFechada = ci > 0 && gv.colunas[ci - 1].tabs.every(rec);
        return [
          ...(semPrincipal && ci === 0 ? [] : [fechada || (semPrincipal && vizinhaFechada)
            ? <div key={`d${ci}`} className="w-2 shrink-0" />
            : <Divisor key={`d${ci}`} eixo="x" onArrasto={(e) => largura(ci, e)} onFim={fim} />]),
          <div key={`c${ci}`} data-coluna className="flex min-h-0 min-w-0 flex-col"
               style={fechada ? { flex: "0 0 2rem" }
                 : semPrincipal ? { flex: `${c.largura} 1 0px`, minWidth: COL_MIN }
                 : { flex: `0 1 ${c.largura}px`, minWidth: TILE_MIN }}>
            {c.tabs.flatMap((t, i) => {
              const r = info(t);
              const seu = props.proprio?.(t);
              const sai = saindo?.tabs.get(t), entra = entrada.current.get(t);
              const anim = sai ? `tile-sai-${sai}` : entra ? `tile-entra-${entra}` : "";
              return [
                ...(i === 0 ? [] : [rec(t) || rec(c.tabs[i - 1])
                  ? <div key={`h${i}`} className="h-2 shrink-0" />
                  : <Divisor key={`h${i}`} eixo="y" onArrasto={(e) => altura(ci, i - 1, e)} onFim={fim} />]),
                <section key={t} data-tile={t}
                         className={`${seu && !rec(t) ? "[&>*]:min-h-0 [&>*]:flex-1" : `${tileCard} overflow-hidden`} flex min-h-0 flex-col ${anim}`}
                         style={fechada ? { flex: "1 1 0" } : rec(t) ? { flex: "0 0 auto" } : { flex: `${c.alturas[i]} 1 0`, minHeight: 120 }}>
                  {rec(t) ? barra(t, fechada) : seu ? props.painel(t, { alca: alca(t), acao: botaoRecolher(t) }) : (
                    <>
                      <div {...alca(t)} title="Segure e arraste para mudar este painel de lugar"
                           className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-[7px] text-xs">
                        <span className="text-muted [&>svg]:size-3.5">{r.icon}</span>
                        <span className="truncate font-medium text-fg">{r.label}</span>
                        {r.resumo && <span className="truncate font-mono text-[11px] text-faint">{r.resumo}</span>}
                        {botaoRecolher(t)}
                        {!g.fixos?.includes(t) && (
                          <button onClick={() => props.onGrade(fechar(g, t))} title={`Fechar ${r.label}`}
                                  className="rounded-md p-0.5 text-faint hover:bg-raised hover:text-fg">
                            <X className="size-3.5" />
                          </button>
                        )}
                      </div>
                      <div className="min-h-0 flex-1 overflow-hidden">{props.painel(t, { alca: alca(t), acao: null })}</div>
                    </>
                  )}
                </section>,
              ];
            })}
          </div>,
        ];
      })}

      {arrastando && (
        <>
          {/* cortina: segura o ponteiro durante o arrasto (inclusive sobre a view nativa do navegador,
              que se esconde quando algo cobre o painel) */}
          <div className="fixed inset-0 z-40 cursor-grabbing" />
          {indicador && (
            <div className={`pointer-events-none absolute z-[60] rounded-xl transition-all duration-100 ${
                   indicador.centro ? "bg-sky-500/10 ring-2 ring-sky-500/70" : "bg-sky-500"}`}
                 style={indicador.caixa} />
          )}
        </>
      )}
    </div>
  );
}

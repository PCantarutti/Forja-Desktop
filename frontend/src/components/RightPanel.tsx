import { useEffect, useRef, useState } from "react";
import { Activity, Clipboard, Cpu, GitBranch, Globe, Info, PanelRight, Terminal } from "./icons";

export type RightTab = "info" | "browser" | "servers" | "plans" | "changes" | "terminal" | "local";

// Largura de cada aba. Só o Navegador redimensiona (mínimo MIN_BROWSER, valor salvo).
const WIDTH: Record<Exclude<RightTab, "browser">, number> = { info: 288, servers: 288, plans: 440, changes: 520, terminal: 560, local: 420 };
const MIN_BROWSER = 320;
const KEY = "forja.right.width";

export const TABS: { id: RightTab; label: string; icon: React.ReactNode }[] = [
  { id: "info", label: "Info", icon: <Info className="size-4" /> },
  { id: "browser", label: "Navegador", icon: <Globe className="size-4" /> },
  { id: "terminal", label: "Terminal", icon: <Terminal className="size-4" /> },
  { id: "changes", label: "Alterações", icon: <GitBranch className="size-4" /> },
  { id: "servers", label: "Instâncias", icon: <Activity className="size-4" /> },
  { id: "local", label: "IA local", icon: <Cpu className="size-4" /> },
  { id: "plans", label: "Planos", icon: <Clipboard className="size-4" /> },
];

/** Botões das abas, sempre visíveis no topo direito do chat. Clicar abre o painel naquela aba; de novo, recolhe. */
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
  return id === "changes" && e.changesCount > 0 ? (
    <span className="rounded-full bg-amber-600/80 px-1.5 text-[10px] leading-4 text-white">{e.changesCount}</span>
  ) : id === "browser" && e.browserOpen ? (
    <span className="size-1.5 rounded-full bg-emerald-400" />
  ) : id === "local" && e.localRunning ? (
    <span className="size-1.5 rounded-full bg-emerald-400" />
  ) : id === "servers" && e.serversRunning > 0 ? (
    <span className="rounded-full bg-emerald-600/80 px-1.5 text-[10px] leading-4 text-white">{e.serversRunning}</span>
  ) : id === "plans" && e.plansPending > 0 ? (
    <span className="rounded-full bg-sky-600/80 px-1.5 text-[10px] leading-4 text-white">{e.plansPending}</span>
  ) : id === "plans" && e.plansTotal > 0 ? (
    <span className="rounded-full bg-raised px-1.5 text-[10px] leading-4 text-muted">{e.plansTotal}</span>
  ) : null;
}

export function RightTabsBar(props: EstadoAbas & {
  tab: RightTab;
  collapsed: boolean;
  onSelect: (t: RightTab) => void;
}) {
  const badge = (id: RightTab) => seloDaAba(id, props);

  return (
    <div className="ml-auto flex shrink-0 items-center gap-0.5 rounded-lg border border-line p-1" role="tablist" aria-label="Painel direito">
      {TABS.map((t) => {
        const active = !props.collapsed && props.tab === t.id;
        const b = badge(t.id);
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={active}
            title={active ? `${t.label} (clique para recolher)` : t.label}
            onClick={() => props.onSelect(t.id)}
            className={`relative grid size-7 place-items-center rounded-md ${
              active ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"
            }`}
          >
            {t.icon}
            {b && <span className="absolute -top-1 -right-1 grid place-items-center">{b}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** Coluna direita: só o conteúdo da aba ativa. Recolhida, não ocupa espaço (os botões ficam no cabeçalho). */
export default function RightPanel(props: {
  tab: RightTab;
  collapsed: boolean;
  onCollapse: (c: boolean) => void;
  children: React.ReactNode;
}) {
  const [browserWidth, setBrowserWidth] = useState(() => Math.max(MIN_BROWSER, Number(localStorage.getItem(KEY)) || 520));
  const drag = useRef<{ x: number; w: number } | null>(null);
  const resizable = props.tab === "browser";
  const width = props.tab === "browser" ? browserWidth : WIDTH[props.tab];

  useEffect(() => {
    localStorage.setItem(KEY, String(browserWidth));
  }, [browserWidth]);

  if (props.collapsed) return null;
  const current = TABS.find((t) => t.id === props.tab)!;

  return (
    <aside className="relative flex shrink-0 flex-col border-l border-line bg-bg" style={{ width }}>
      {resizable && (
        <div
          title="Arraste para redimensionar o navegador"
          className="absolute inset-y-0 -left-1 z-10 w-2 cursor-col-resize hover:bg-fg/15"
          onPointerDown={(e) => {
            drag.current = { x: e.clientX, w: browserWidth };
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            if (!drag.current) return;
            const next = drag.current.w + (drag.current.x - e.clientX);
            setBrowserWidth(Math.round(Math.max(MIN_BROWSER, Math.min(window.innerWidth * 0.7, next))));
          }}
          onPointerUp={() => (drag.current = null)}
          onPointerCancel={() => (drag.current = null)}
        />
      )}
      <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-xs">
        <span className="text-muted">{current.icon}</span>
        <span className="font-medium text-fg">{current.label}</span>
        <button
          onClick={() => props.onCollapse(true)}
          title="Recolher painel"
          className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg"
        >
          <PanelRight className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">{props.children}</div>
    </aside>
  );
}

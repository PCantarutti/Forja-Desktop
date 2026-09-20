import { useEffect, useRef, useState } from "react";

const fmt = (n: number) => n.toLocaleString("pt-BR");

/**
 * Anel de contexto (como no Claude): preenche conforme a fração da janela em uso. Clique abre os
 * detalhes (contexto, saída do último turno, média de t/s) e o botão de compactar.
 */
export default function ContextRing(props: {
  used: number | null;
  max: number | null;
  out: number | null;
  avg: number | null;
  canCompact: boolean;
  onCompact: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const pct = props.used != null && props.max ? Math.min(1, props.used / props.max) : 0;
  const r = 7;
  const c = 2 * Math.PI * r;
  const color = pct >= 0.9 ? "#f87171" : pct >= 0.7 ? "#fbbf24" : "#a3a3a3";
  const label = props.used == null ? "Contexto: sem dados ainda" : `Contexto: ${fmt(props.used)}/${props.max ? fmt(props.max) : "?"} (${Math.round(pct * 100)}%)`;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title={label}
        aria-label={label}
        className="grid size-8 place-items-center rounded-full text-muted hover:bg-raised hover:text-fg"
      >
        <svg viewBox="0 0 20 20" className="size-5 -rotate-90">
          <circle cx="10" cy="10" r={r} fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
          <circle
            cx="10"
            cy="10"
            r={r}
            fill="none"
            stroke={color}
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={c * (1 - pct)}
            style={{ transition: "stroke-dashoffset 400ms ease" }}
          />
        </svg>
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-30 mb-2 w-60 rounded-xl border border-line bg-surface p-3 font-mono text-xs text-muted shadow-xl">
          <div className="mb-1 flex items-center justify-between">
            <span>Contexto</span>
            <span className="text-fg">{props.used == null ? "—" : `${fmt(props.used)} / ${props.max ? fmt(props.max) : "?"}`}</span>
          </div>
          <div className="mb-2 h-1.5 overflow-hidden rounded-full bg-raised">
            <div className="h-full rounded-full" style={{ width: `${Math.round(pct * 100)}%`, background: color }} />
          </div>
          <div className="flex items-center justify-between">
            <span>Uso</span>
            <span className="text-fg">{Math.round(pct * 100)}%</span>
          </div>
          {props.out != null && (
            <div className="flex items-center justify-between">
              <span>Saída (último turno)</span>
              <span className="text-fg">{fmt(props.out)}</span>
            </div>
          )}
          {props.avg != null && (
            <div className="flex items-center justify-between">
              <span>Média</span>
              <span className="text-fg">{props.avg.toFixed(1)} t/s</span>
            </div>
          )}
          <button
            onClick={() => {
              setOpen(false);
              props.onCompact();
            }}
            disabled={!props.canCompact}
            title="Resume o histórico antigo agora (também: /compactar)"
            className="mt-2 w-full rounded-full border border-line px-3 py-1 text-fg hover:bg-raised disabled:opacity-40"
          >
            Compactar agora
          </button>
        </div>
      )}
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { UsageBars, useCloudUsage } from "./CloudUsage";

const fmt = (n: number) => n.toLocaleString("pt-BR");
const fmtK = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);

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
  provider: string;   // o que está no seletor de modelo
  models: string[];   // modelos que já responderam nesta conversa
  abaixo?: boolean;   // abre para baixo e alinhado à direita (anel no topo de uma coluna)
  // Divisão do prompt por tipo (estimativa) e números da conversa, como no painel do DeepSeek Harness.
  partes?: { sistema: number; ferramentas: number; mensagens: number } | null;
  sessao?: { turnos: number; passos: number; tokens: number; cache: number | null };
}) {
  const [open, setOpen] = useState(false);
  // Só consulta a cota com o popover aberto. Mostra TODO provedor de nuvem configurado, mesmo
  // quando o modelo desta conversa é local: a cota do mês é do usuário, não da conversa.
  const nuvens = useCloudUsage(open);
  const emUso = (u: (typeof nuvens)[number]) =>
    u.provider === props.provider || u.models.some((m) => props.models.includes(m.name));
  const cotas = [...nuvens].sort((a, b) => Number(emUso(b)) - Number(emUso(a)));
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
  const totalPartes = props.partes ? props.partes.sistema + props.partes.ferramentas + props.partes.mensagens : 0;
  const partesVisiveis = props.partes && totalPartes
    ? [
        { nome: "System prompt", valor: props.partes.sistema, cor: "#a3a3a3" },
        { nome: "Ferramentas", valor: props.partes.ferramentas, cor: "#a78bfa" },
        { nome: "Mensagens", valor: props.partes.mensagens, cor: "#60a5fa" },
      ].filter((p) => p.valor > 0)
    : null;
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
        <div className={`absolute z-30 ${props.abaixo ? "right-0 top-full mt-2" : "bottom-full left-0 mb-2"} w-60 rounded-xl border border-line bg-surface p-3 font-mono text-xs text-muted shadow-xl`}>
          <div className="mb-1 flex items-center justify-between">
            <span>Contexto</span>
            <span className="text-fg">{props.used == null ? "—" : `${fmt(props.used)} / ${props.max ? fmt(props.max) : "?"}`}</span>
          </div>
          {partesVisiveis ? (
            <>
              {/* Barra empilhada: cada parte na proporção do que ocupa da janela. */}
              <div className="mb-2 flex h-1.5 overflow-hidden rounded-full bg-raised">
                {partesVisiveis.map((p) => (
                  <div key={p.nome} className="h-full" style={{ width: `${(p.valor / (props.max || totalPartes)) * 100}%`, background: p.cor }} />
                ))}
              </div>
              {partesVisiveis.map((p) => (
                <div key={p.nome} className="flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block size-2 rounded-sm" style={{ background: p.cor }} />
                    {p.nome}
                  </span>
                  <span className="text-fg">~{fmtK(p.valor)}</span>
                </div>
              ))}
            </>
          ) : (
            <div className="mb-2 h-1.5 overflow-hidden rounded-full bg-raised">
              <div className="h-full rounded-full" style={{ width: `${Math.round(pct * 100)}%`, background: color }} />
            </div>
          )}
          <div className="flex items-center justify-between">
            <span>Uso</span>
            <span className="text-fg">{Math.round(pct * 100)}%</span>
          </div>
          {props.sessao && props.sessao.passos > 0 && (
            <div className="mt-2 border-t border-line pt-2">
              <div className="mb-1 text-faint">Conversa</div>
              <div className="flex items-center justify-between">
                <span>Turnos · passos</span>
                <span className="text-fg">{props.sessao.turnos} · {props.sessao.passos}</span>
              </div>
              <div className="flex items-center justify-between">
                <span>Tokens somados</span>
                <span className="text-fg">{fmtK(props.sessao.tokens)}</span>
              </div>
              <div className="flex items-center justify-between" title="Parte do prompt que o servidor reaproveitou do cache em vez de processar de novo">
                <span>Acerto de cache</span>
                <span className="text-fg">{props.sessao.cache == null ? "—" : `${Math.round(props.sessao.cache * 100)}%`}</span>
              </div>
            </div>
          )}
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
          {cotas.map((cota) => (
            <div key={cota.provider} className="mt-3 border-t border-line pt-2">
              <div className="mb-1.5 text-faint">Cota · {cota.name}</div>
              <UsageBars data={cota} />
            </div>
          ))}
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

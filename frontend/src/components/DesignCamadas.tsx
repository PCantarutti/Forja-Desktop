import { useEffect, useMemo, useRef, useState } from "react";
import type { NoArvore } from "./designCanvas";
import { PAINEL_FLUTUA_ESQ } from "./DesignEditar";
import { ChevronDown, EyeOff, X } from "./icons";

// Painel Camadas da tela Design: a árvore de elementos da página (os que têm data-fid), para achar e
// selecionar o que é difícil clicar no canvas — um container, algo coberto, um nó escondido. Arrastar
// uma linha reorganiza: na borda de cima/baixo põe antes/depois, no meio põe dentro.

type Onde = "antes" | "depois" | "dentro";

export default function DesignCamadas(props: {
  nos: NoArvore[];
  selecionados: string[];
  onSelecionar: (fids: string[]) => void;
  onRealce: (fid: string | null) => void;
  onMover: (fids: string[], alvo: string, onde: Onde) => void;
  onFechar: () => void;
  largura: number;   // px; o divisor à direita (no DesignView) muda
}) {
  const [fechados, setFechados] = useState<Set<string>>(new Set());
  const [solta, setSolta] = useState<{ fid: string; onde: Onde } | null>(null);
  const arrastando = useRef<string[]>([]);
  const lista = useRef<HTMLDivElement>(null);

  // quem tem filho: o próximo nó é mais fundo
  const temFilho = useMemo(() => new Set(props.nos.filter((n, i) => (props.nos[i + 1]?.nivel ?? -1) > n.nivel).map((n) => n.fid)), [props.nos]);
  // visíveis: nenhum ancestral recolhido
  const visiveis = useMemo(() => {
    const out: NoArvore[] = [];
    let corte = Infinity;   // nível do recolhido que está escondendo os de baixo
    for (const n of props.nos) {
      if (n.nivel > corte) continue;
      corte = Infinity;
      out.push(n);
      if (fechados.has(n.fid)) corte = n.nivel;
    }
    return out;
  }, [props.nos, fechados]);

  // selecionou no canvas: abre os ancestrais e rola até a linha
  const principal = props.selecionados[props.selecionados.length - 1];
  useEffect(() => {
    if (!principal) return;
    const i = props.nos.findIndex((n) => n.fid === principal);
    if (i < 0) return;
    const abrir: string[] = [];
    for (let j = i - 1, nivel = props.nos[i].nivel; j >= 0 && nivel > 0; j--)
      if (props.nos[j].nivel < nivel) { abrir.push(props.nos[j].fid); nivel = props.nos[j].nivel; }
    setFechados((f) => (abrir.some((x) => f.has(x)) ? new Set([...f].filter((x) => !abrir.includes(x))) : f));
    requestAnimationFrame(() => lista.current?.querySelector(`[data-no="${principal}"]`)?.scrollIntoView({ block: "nearest" }));
  }, [principal, props.nos]);

  const rotuloNo = (n: NoArvore) => n.tag + (n.cls ? "." + n.cls.split(/\s+/)[0] : "");

  return (
    <aside aria-label="Camadas" style={{ width: props.largura }} className={`flex shrink-0 flex-col overflow-hidden bg-surface text-[12px] ${PAINEL_FLUTUA_ESQ}`}>
      <div className="flex items-center gap-1.5 border-b border-line px-3 py-2 whitespace-nowrap">
        <span className="font-medium text-fg">Camadas</span>
        <span className="text-faint">{props.nos.length}</span>
        <span className="flex-1" />
        <button onClick={() => setFechados(new Set(props.nos.filter((n) => n.nivel >= 1 && temFilho.has(n.fid)).map((n) => n.fid)))}
                className="rounded px-1 text-[11px] text-muted hover:text-fg" title="Recolher tudo abaixo das seções">recolher tudo</button>
        <button onClick={() => setFechados(new Set())} className="rounded px-1 text-[11px] text-muted hover:text-fg">abrir tudo</button>
        <button onClick={props.onFechar} title="Fechar camadas" className="grid size-6 place-items-center rounded text-muted hover:bg-raised hover:text-fg">
          <X className="size-3.5" />
        </button>
      </div>
      <div ref={lista} role="tree" aria-label="Elementos da página" className="min-h-0 flex-1 overflow-y-auto py-1"
           onMouseLeave={() => props.onRealce(null)}>
        {visiveis.map((n) => {
          const sel = props.selecionados.includes(n.fid);
          const alvo = solta?.fid === n.fid ? solta.onde : null;
          return (
            <div key={n.fid} data-no={n.fid} role="treeitem" aria-selected={sel} aria-level={n.nivel + 1}
                 aria-expanded={temFilho.has(n.fid) ? !fechados.has(n.fid) : undefined}
                 draggable
                 onDragStart={(e) => {
                   arrastando.current = sel ? props.selecionados : [n.fid];
                   e.dataTransfer.effectAllowed = "move";
                   e.dataTransfer.setData("text/plain", n.fid);
                 }}
                 onDragOver={(e) => {
                   if (arrastando.current.includes(n.fid)) return;
                   e.preventDefault();
                   const r = e.currentTarget.getBoundingClientRect(), y = (e.clientY - r.top) / r.height;
                   setSolta({ fid: n.fid, onde: y < 0.3 ? "antes" : y > 0.7 ? "depois" : "dentro" });
                 }}
                 onDragLeave={() => setSolta((x) => (x?.fid === n.fid ? null : x))}
                 onDrop={(e) => {
                   e.preventDefault();
                   if (solta) props.onMover(arrastando.current, n.fid, solta.onde);
                   setSolta(null);
                   arrastando.current = [];
                 }}
                 onDragEnd={() => { setSolta(null); arrastando.current = []; }}
                 onMouseEnter={() => props.onRealce(n.fid)}
                 onClick={(e) => props.onSelecionar(e.shiftKey || e.ctrlKey || e.metaKey
                   ? (sel ? props.selecionados.filter((x) => x !== n.fid) : [...props.selecionados, n.fid]) : [n.fid])}
                 style={{ paddingLeft: 6 + n.nivel * 12 }}
                 className={`relative flex cursor-default items-center gap-1 py-[3px] pr-2 whitespace-nowrap ${sel ? "bg-accent-soft text-accent-text" : "text-fg-2 hover:bg-raised"}
                   ${alvo === "dentro" ? "outline outline-1 outline-accent" : ""} ${n.oculto ? "opacity-50" : ""}`}>
              {alvo === "antes" && <span className="absolute inset-x-1 top-0 h-0.5 bg-accent" />}
              {alvo === "depois" && <span className="absolute inset-x-1 bottom-0 h-0.5 bg-accent" />}
              {temFilho.has(n.fid) ? (
                <button onClick={(e) => { e.stopPropagation(); setFechados((f) => { const g = new Set(f); if (g.has(n.fid)) g.delete(n.fid); else g.add(n.fid); return g; }); }}
                        aria-label={fechados.has(n.fid) ? "Abrir" : "Recolher"} className="grid size-4 shrink-0 place-items-center text-faint hover:text-fg">
                  <ChevronDown className={`size-3 transition-transform ${fechados.has(n.fid) ? "-rotate-90" : ""}`} />
                </button>
              ) : <span className="w-4 shrink-0" />}
              <span className="font-mono text-[11.5px]">{rotuloNo(n)}</span>
              {n.sec && <span className="rounded bg-raised px-1 text-[10.5px] text-muted">{n.sec}</span>}
              {n.texto && <span className="min-w-0 truncate text-faint">{n.texto}</span>}
              {n.oculto && <EyeOff className="ml-auto size-3 shrink-0 text-faint" />}
            </div>
          );
        })}
        {!props.nos.length && <p className="px-3 py-2 text-faint">Nada no canvas ainda.</p>}
      </div>
      <p className="border-t border-line px-3 py-1.5 text-[11px] leading-snug text-faint">
        Clique seleciona (Shift junta). Arraste uma linha: na borda põe antes/depois, no meio põe dentro.
      </p>
    </aside>
  );
}

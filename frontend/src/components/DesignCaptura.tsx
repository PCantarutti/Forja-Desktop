import { useState } from "react";
import { X } from "./icons";

// Captura de site (referência por URL): a página capturada com os blocos de topo por cima. Marcar
// blocos e "Trazer" copia essas seções para o fim do design (estilo computado, sem script, fotos
// viram slots de imagem); "Usar a paleta" leva fundo, texto, destaque e fontes para os tokens. Tudo
// sem IA, no rascunho.

export type Bloco = { rotulo: string; caixa: [number, number, number, number] };
export type Paleta = { fundo: string; texto: string; destaque: string; fonte_texto: string; fonte_titulo: string };

const LARGURA_TELA = 560;   // a captura (1440px) aparece nesta largura

export default function DesignCaptura(props: {
  nome: string;
  foto: string;
  largura: number;
  blocos: Bloco[];
  paleta: Paleta;
  onTrazer: (indices: number[]) => void;
  onPaleta: () => void;
  onFechar: () => void;
}) {
  const [marcados, setMarcados] = useState<number[]>([]);
  const k = LARGURA_TELA / props.largura;
  const alterna = (i: number) => setMarcados((m) => (m.includes(i) ? m.filter((x) => x !== i) : [...m, i].sort((a, b) => a - b)));
  const cores = [["Fundo", props.paleta.fundo], ["Texto", props.paleta.texto], ["Destaque", props.paleta.destaque]].filter(([, c]) => c);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-6" onClick={props.onFechar}>
      <div role="dialog" aria-label="Blocos da página capturada" onClick={(e) => e.stopPropagation()}
           className="flex max-h-full w-[min(980px,100%)] flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl">
        <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          <span className="min-w-0 truncate font-medium text-fg">Captura: {props.nome}</span>
          <span className="flex-1" />
          <button onClick={props.onFechar} title="Fechar" className="grid size-7 place-items-center rounded-lg text-muted hover:bg-raised hover:text-fg"><X className="size-4" /></button>
        </div>
        <div className="flex min-h-0 flex-1">
          <div className="min-h-0 overflow-y-auto border-r border-line bg-raised p-3">
            <div className="relative" style={{ width: LARGURA_TELA }}>
              <img src={props.foto} alt={`Captura de ${props.nome}`} style={{ width: LARGURA_TELA }} className="block rounded" />
              {props.blocos.map((b, i) => (
                <button key={i} onClick={() => alterna(i)} title={b.rotulo} aria-pressed={marcados.includes(i)}
                        style={{ left: b.caixa[0] * k, top: b.caixa[1] * k, width: b.caixa[2] * k, height: b.caixa[3] * k }}
                        className={`absolute rounded border-2 transition-colors ${marcados.includes(i)
                          ? "border-accent bg-accent/25" : "border-dashed border-white/40 hover:border-accent hover:bg-accent/10"}`}>
                  <span className="absolute top-1 left-1 rounded bg-black/70 px-1 font-mono text-[10px] text-white">{i + 1}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="flex w-80 shrink-0 flex-col gap-3 overflow-y-auto p-4 text-[12.5px]">
            <section>
              <div className="mb-1.5 font-mono text-[10.5px] tracking-[.08em] text-faint uppercase">Blocos</div>
              <p className="mb-2 text-[11.5px] leading-snug text-faint">Clique na captura ou na lista. Os blocos entram no fim da página como seções novas (estilo copiado, fotos viram espaços de imagem).</p>
              {props.blocos.map((b, i) => (
                <label key={i} className="flex cursor-pointer items-start gap-2 rounded-lg px-1.5 py-1 hover:bg-raised">
                  <input type="checkbox" checked={marcados.includes(i)} onChange={() => alterna(i)} className="mt-0.5 accent-[var(--color-accent)]" />
                  <span className="min-w-0"><span className="font-mono text-faint">{i + 1}</span> <span className="text-fg-2">{b.rotulo}</span></span>
                </label>
              ))}
              {!props.blocos.length && <p className="text-faint">Não achei blocos de topo nesta página.</p>}
              <button disabled={!marcados.length} onClick={() => props.onTrazer(marcados)}
                      className="mt-2 w-full rounded-lg bg-accent px-3 py-1.5 font-medium text-accent-fg hover:brightness-110 disabled:opacity-40">
                Trazer {marcados.length || ""} {marcados.length === 1 ? "bloco" : "blocos"} para o design
              </button>
            </section>
            <section className="border-t border-line pt-3">
              <div className="mb-1.5 font-mono text-[10.5px] tracking-[.08em] text-faint uppercase">Paleta do site</div>
              <div className="flex flex-wrap gap-2">
                {cores.map(([n, c]) => (
                  <span key={n} className="flex items-center gap-1.5 text-[11.5px] text-muted">
                    <span className="size-5 rounded border border-line" style={{ background: c }} />{n} <span className="font-mono text-faint">{c}</span>
                  </span>
                ))}
              </div>
              <p className="mt-1.5 truncate text-[11.5px] text-muted" title={props.paleta.fonte_titulo}>Títulos: {props.paleta.fonte_titulo}</p>
              <p className="truncate text-[11.5px] text-muted" title={props.paleta.fonte_texto}>Texto: {props.paleta.fonte_texto}</p>
              <button onClick={props.onPaleta} className="mt-2 w-full rounded-lg border border-line px-3 py-1.5 text-fg hover:bg-raised">
                Usar a paleta nos tokens do design
              </button>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}

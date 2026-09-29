import { docEstatico } from "./designCanvas";
import { Check } from "./icons";

// Variações de tokens lado a lado: a página atual renderizada três vezes, cada uma com o :root da
// variação por cima. Escolher aplica só os tokens (no rascunho, sem IA).

export type Variacao = { nome: string; descricao: string; tokens: Record<string, string> };

const LARG = 1280, ALT = 860, ESCALA = 0.19;

export function Miniatura(props: { html: string; css?: string; titulo: string }) {
  return (
    <div className="relative overflow-hidden rounded-lg border border-line bg-white" style={{ width: LARG * ESCALA, height: ALT * ESCALA }}>
      <iframe title={props.titulo} sandbox="" srcDoc={docEstatico(props.html, props.css)} tabIndex={-1}
              className="pointer-events-none origin-top-left border-0" style={{ width: LARG, height: ALT, transform: `scale(${ESCALA})` }} />
    </div>
  );
}

export default function DesignVariacoes(props: { html: string; variacoes: Variacao[]; escolhida?: number | null; desabilitado?: boolean;
                                                 onEscolher: (i: number) => void }) {
  return (
    <div className="w-full rounded-2xl border border-line bg-surface p-3">
      <div className="mb-2 font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">Variações · só tokens, sem refazer nada</div>
      <div className="flex flex-wrap gap-3">
        {props.variacoes.map((v, i) => {
          const css = `:root{${Object.entries(v.tokens).map(([k, x]) => `${k}:${x}`).join(";")}}`;
          const on = props.escolhida === i;
          return (
            <div key={i} className={`flex flex-col gap-1.5 rounded-xl p-1.5 ${on ? "bg-accent-soft ring-1 ring-accent-line" : ""}`}>
              <Miniatura html={props.html} css={css} titulo={`Variação ${v.nome}`} />
              <div className="flex items-center gap-1.5">
                {Object.entries(v.tokens).filter(([k]) => k.startsWith("--cor")).slice(0, 5).map(([k, x]) => (
                  <span key={k} title={`${k}: ${x}`} className="size-3 rounded-full border border-line" style={{ background: x }} />
                ))}
              </div>
              <p className="max-w-[243px] text-[12.5px] text-fg">{v.nome}</p>
              {v.descricao && <p className="max-w-[243px] text-[11.5px] text-faint">{v.descricao}</p>}
              <button disabled={props.desabilitado} onClick={() => props.onEscolher(i)}
                      className={`mt-auto inline-flex items-center justify-center gap-1 rounded-lg px-2 py-1 text-xs ${on ? "text-accent-text" : "border border-line text-fg hover:bg-raised"} disabled:opacity-40`}>
                {on ? <><Check className="size-3.5" /> Em uso (dá para trocar)</> : "Usar esta"}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

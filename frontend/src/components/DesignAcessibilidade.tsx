import type { Problema } from "./designCanvas";

// Revisão de acessibilidade feita dentro do canvas, sem IA (cores e tamanhos computados de verdade).
// Clicar num item mostra o elemento; "Corrigir com a IA" manda só esses elementos como fragmento.

export default function DesignAcessibilidade(props: {
  itens: Problema[] | null;
  escopo: string;
  desabilitado: boolean;
  onVerificar: () => void;
  onMostrar: (fid: string) => void;
  onCorrigir: (itens: Problema[]) => void;
}) {
  const itens = props.itens ?? [];
  const erros = itens.filter((x) => x.gravidade === "erro");
  const corrigiveis = itens.filter((x) => x.fid);
  return (
    <div className="flex flex-col gap-2 py-2 text-[13px]">
      <div className="flex items-center gap-2">
        <button onClick={props.onVerificar} className="rounded-lg border border-line px-2.5 py-1 text-xs text-fg hover:bg-raised">Verificar de novo</button>
        {!!corrigiveis.length && (
          <button disabled={props.desabilitado} onClick={() => props.onCorrigir(corrigiveis)}
                  className="rounded-lg bg-accent px-2.5 py-1 text-xs font-medium text-accent-fg hover:brightness-110 disabled:opacity-40">
            Corrigir {corrigiveis.length} com a IA
          </button>
        )}
        <span className="ml-auto text-[11.5px] text-faint">{props.escopo}</span>
      </div>
      {props.itens === null ? (
        <p className="text-xs text-muted">Verificando o canvas…</p>
      ) : !itens.length ? (
        <p className="text-xs text-emerald-300">Nenhum problema encontrado ({props.escopo}): contraste, textos alternativos, nomes de botões e títulos em ordem.</p>
      ) : (
        <>
          <p className="text-xs text-muted">{erros.length} erro(s) e {itens.length - erros.length} aviso(s) — WCAG 2.1 AA, calculado sem IA.</p>
          {itens.map((x, i) => (
            <button key={i} onClick={() => x.fid && props.onMostrar(x.fid)} disabled={!x.fid}
                    className="flex items-start gap-2 rounded-lg border border-line p-2 text-left hover:bg-raised/60 disabled:hover:bg-transparent">
              <span className={`mt-0.5 size-2 shrink-0 rounded-full ${x.gravidade === "erro" ? "bg-red-400" : "bg-amber-400"}`} />
              <span className="min-w-0">
                <span className="block text-fg">{x.tipo} <span className="font-mono text-[11px] text-faint">{x.rotulo}</span></span>
                <span className="block text-[12px] text-muted">{x.detalhe}</span>
              </span>
            </button>
          ))}
        </>
      )}
    </div>
  );
}

import type { Problema } from "./designCanvas";
import { botao, botaoPrincipal, item, tituloSecao } from "./designUi";
import { Check } from "./icons";

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
    <section aria-label="Acessibilidade" className="flex flex-col gap-2.5 border-t border-line py-3">
      <div className="flex items-center gap-2 px-1">
        <h3 className={tituloSecao}>Acessibilidade</h3>
        <span className="truncate text-[11px] text-faint">{props.escopo}</span>
        <span className="flex-1" />
        <button onClick={props.onVerificar} className={botao}>Verificar de novo</button>
        {!!corrigiveis.length && (
          <button disabled={props.desabilitado} onClick={() => props.onCorrigir(corrigiveis)} className={botaoPrincipal}>
            Corrigir {corrigiveis.length} com a IA
          </button>
        )}
      </div>
      {props.itens === null ? (
        <p className="flex items-center gap-2 px-1 text-[12px] text-muted">
          <span className="size-3 animate-spin rounded-full border border-accent/30 border-t-accent" aria-hidden /> Verificando o canvas…
        </p>
      ) : !itens.length ? (
        <p className="flex items-start gap-2 px-1 text-[12px] leading-snug text-fg-2">
          <Check className="mt-px size-4 shrink-0 text-ok" aria-hidden />
          Nenhum problema: contraste, textos alternativos, nomes de botões e títulos em ordem.
        </p>
      ) : (
        <>
          <p className="px-1 text-[12px] text-muted">
            <span className="text-err">{erros.length} erro{erros.length === 1 ? "" : "s"}</span> e {itens.length - erros.length} aviso{itens.length - erros.length === 1 ? "" : "s"} · WCAG 2.1 AA, medido sem IA
          </p>
          <ul className="flex flex-col gap-1.5">
            {itens.map((x, i) => (
              <li key={i}>
                <button onClick={() => x.fid && props.onMostrar(x.fid)} disabled={!x.fid} title={x.fid ? "Mostrar no canvas" : undefined}
                        className={`${item} flex w-full items-start gap-2.5 p-2.5 text-left enabled:hover:bg-raised/40 disabled:cursor-default`}>
                  <span className={`mt-1.5 size-2 shrink-0 rounded-full ${x.gravidade === "erro" ? "bg-err" : "bg-warn"}`} aria-hidden />
                  <span className="min-w-0">
                    <span className="flex min-w-0 items-baseline gap-1.5">
                      <span className="text-[13px] text-fg"><span className="sr-only">{x.gravidade === "erro" ? "Erro: " : "Aviso: "}</span>{x.tipo}</span>
                      <span className="truncate font-mono text-[10.5px] text-faint">{x.rotulo}</span>
                    </span>
                    <span className="mt-0.5 block text-[12px] leading-snug text-muted">{x.detalhe}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

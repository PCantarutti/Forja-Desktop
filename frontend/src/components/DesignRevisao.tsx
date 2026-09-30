import { botao, botaoItem, item, tituloSecao } from "./designUi";

// Revisão visual da tela Design: a página aberta de verdade em Desktop, Tablet e Celular (Chromium
// headless no backend). O que dá para medir vem sem IA; se o modelo de edição lê imagem, ele olha as
// capturas e aponta o resto. Cada achado pode ir para a fila de comentários.

export type ProblemaVisual = { fid: string; largura: "desktop" | "tablet" | "mobile"; tipo: string; detalhe: string; rotulo: string; fonte: "medido" | "modelo" };
export type Revisao = { problemas: ProblemaVisual[]; visao: boolean; aviso: string; mensagem: string; na_fila: number };

const NOME = { desktop: "Desktop", tablet: "Tablet", mobile: "Celular" };

export default function DesignRevisao(props: {
  revisao: Revisao | null;
  rodando: boolean;
  desabilitado: boolean;
  modelo: string;
  comVisao: boolean;
  auto: boolean;
  onComVisao: (v: boolean) => void;
  onAuto: (v: boolean) => void;
  onRevisar: (fila: boolean) => void;
  onMostrar: (p: ProblemaVisual) => void;
  onFila: (ps: ProblemaVisual[]) => void;
}) {
  const r = props.revisao;
  const opcao = (ativo: boolean, set: (v: boolean) => void, texto: string, dica: string) => (
    <label title={dica} className="flex cursor-pointer items-start gap-2 text-[12px] leading-snug text-muted hover:text-fg">
      <input type="checkbox" checked={ativo} onChange={() => set(!ativo)} className="mt-px size-3.5 shrink-0 cursor-pointer accent-[var(--accent)]" />
      <span>{texto}</span>
    </label>
  );
  return (
    <section aria-label="Revisão visual" className="flex flex-col gap-2.5 py-2">
      <div className="flex items-center gap-2 px-1">
        <h3 className={tituloSecao}>Revisão visual</h3>
        <span className="text-[11px] text-faint">Desktop · Tablet · Celular</span>
        <span className="flex-1" />
        <button onClick={() => props.onRevisar(false)} disabled={props.rodando || props.desabilitado} className={botao}>
          {props.rodando && <span className="size-3 animate-spin rounded-full border border-accent/30 border-t-accent" aria-hidden />}
          {props.rodando ? "Revisando…" : "Revisar agora"}
        </button>
      </div>
      <div className="flex flex-col gap-1.5 px-1">
        {opcao(props.comVisao, props.onComVisao, `O modelo olha as capturas (${props.modelo || "modelo de edição"})`,
               "Sem isso, só o que dá para medir: texto cortado, rolagem para o lado, sobreposição, toque pequeno, letra miúda")}
        {opcao(props.auto, props.onAuto, "Revisar sozinho depois de cada geração e pôr os achados na fila",
               "Roda a revisão quando a IA termina uma versão; os problemas viram comentários pendentes (dá para descartar)")}
      </div>
      {!r && !props.rodando && (
        <p className="px-1 text-[11.5px] leading-snug text-faint">Abre a página nas três larguras e aponta o que está quebrado: texto cortado, sobreposição, rolagem para o lado, toque pequeno.</p>
      )}
      {r && (
        <>
          {r.aviso && <p className="rounded-xl border border-warn/35 bg-warn/[.06] px-2.5 py-1.5 text-[12px] leading-snug text-fg-2">{r.aviso}</p>}
          {r.mensagem && <p className="px-1 text-[12.5px] leading-snug text-fg-2">{r.mensagem}</p>}
          <div className="flex items-center gap-2 px-1 text-[12px]">
            <span className={r.problemas.length ? "text-muted" : "text-ok"}>
              {r.problemas.length ? `${r.problemas.length} achado${r.problemas.length > 1 ? "s" : ""}${r.visao ? " · medição e modelo" : " · medição"}` : "Nada de errado nas 3 larguras."}
            </span>
            {!!r.na_fila && <span className="text-ok">· {r.na_fila} na fila</span>}
            <span className="flex-1" />
            {r.problemas.length > 1 && <button onClick={() => props.onFila(r.problemas)} className={botaoItem}>Tudo para a fila</button>}
          </div>
          {!!r.problemas.length && (
            <ul className="flex flex-col gap-1.5">
              {r.problemas.map((p, i) => (
                <li key={i} className={`${item} group flex items-start gap-2 p-2.5 hover:bg-raised/40`}>
                  <button onClick={() => props.onMostrar(p)} className="min-w-0 flex-1 text-left" title="Mostrar no canvas, nessa largura">
                    <span className="flex min-w-0 items-center gap-1.5 text-[11.5px]">
                      <span className="shrink-0 rounded-md bg-raised px-1.5 text-[10.5px] leading-[18px] text-muted">{NOME[p.largura]}</span>
                      <span className={`shrink-0 ${p.fonte === "modelo" ? "text-accent-text" : "text-warn"}`}>{p.tipo}</span>
                      <span className="truncate font-mono text-[10.5px] text-faint">{p.rotulo}</span>
                    </span>
                    <span className="mt-1 block text-[12.5px] leading-snug text-fg-2">{p.detalhe}</span>
                  </button>
                  <button onClick={() => props.onFila([p])}
                          className={`${botaoItem} opacity-60 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100`}>
                    Pôr na fila
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

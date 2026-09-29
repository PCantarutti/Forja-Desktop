import { Check } from "./icons";

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
    <button onClick={() => set(!ativo)} title={dica} aria-pressed={ativo}
            className="flex items-center gap-1.5 text-left text-[12px] text-muted hover:text-fg">
      <span className={`grid size-3.5 shrink-0 place-items-center rounded border ${ativo ? "border-accent bg-accent text-accent-fg" : "border-line-strong"}`}>
        {ativo && <Check className="size-2.5" />}
      </span>
      {texto}
    </button>
  );
  return (
    <section aria-label="Revisão visual" className="flex flex-col gap-2 border-b border-line py-2 text-[13px]">
      <div className="flex items-center gap-2">
        <span className="font-medium text-fg">Revisão visual</span>
        <span className="flex-1" />
        <button onClick={() => props.onRevisar(false)} disabled={props.rodando || props.desabilitado}
                className="rounded-lg border border-line px-2.5 py-1 text-xs text-fg hover:bg-raised disabled:opacity-40">
          {props.rodando ? "Revisando…" : "Revisar agora"}
        </button>
      </div>
      <div className="flex flex-col gap-1">
        {opcao(props.comVisao, props.onComVisao, `Pedir ao modelo para olhar as capturas (${props.modelo || "modelo de edição"})`,
               "Sem isso, só o que dá para medir: texto cortado, rolagem para o lado, sobreposição, toque pequeno, letra miúda")}
        {opcao(props.auto, props.onAuto, "Revisar sozinho depois de cada geração da IA e pôr os achados na fila",
               "Roda a revisão quando a IA termina uma versão; os problemas viram comentários pendentes (dá para descartar)")}
      </div>
      {r && (
        <>
          {r.aviso && <p className="rounded-lg bg-amber-500/10 px-2 py-1 text-[12px] text-amber-200">{r.aviso}</p>}
          {r.mensagem && <p className="text-[12.5px] text-fg-2">{r.mensagem}</p>}
          <div className="flex items-center gap-2 text-[12px] text-muted">
            <span>{r.problemas.length ? `${r.problemas.length} achado(s) nas 3 larguras${r.visao ? " (medição + modelo)" : " (medição)"}` : "Nada de errado nas 3 larguras."}</span>
            {!!r.na_fila && <span className="text-emerald-300">{r.na_fila} foram para a fila</span>}
            <span className="flex-1" />
            {!!r.problemas.length && (
              <button onClick={() => props.onFila(r.problemas)} className="rounded border border-line px-1.5 text-[11px] text-fg hover:bg-raised">Tudo para a fila</button>
            )}
          </div>
          {r.problemas.map((p, i) => (
            <div key={i} className="group flex items-start gap-2 rounded-lg border border-line p-2 hover:bg-raised/60">
              <button onClick={() => props.onMostrar(p)} className="min-w-0 flex-1 text-left" title="Mostrar no canvas, nessa largura">
                <span className="flex items-center gap-1.5 text-[11.5px]">
                  <span className="rounded bg-raised px-1 text-muted">{NOME[p.largura]}</span>
                  <span className={p.fonte === "modelo" ? "text-accent-text" : "text-amber-300"}>{p.tipo}</span>
                  <span className="truncate font-mono text-faint">{p.rotulo}</span>
                </span>
                <span className="mt-0.5 block text-[12.5px] leading-snug text-fg-2">{p.detalhe}</span>
              </button>
              <button onClick={() => props.onFila([p])} className="shrink-0 rounded border border-line px-1.5 text-[11px] opacity-0 group-hover:opacity-100 hover:bg-raised">
                Pôr na fila
              </button>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

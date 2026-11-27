import type { Ctx } from "./EstudoView";
import { btnPrimary, card, corAcerto, pct, rotulo } from "./ui";

export default function Desempenho({ ctx, irParaProvas }: { ctx: Ctx; irParaProvas: () => void }) {
  const d = ctx.estudo.desempenho;
  const por = Object.fromEntries(d.topicos.map((t) => [t.id, t]));
  return (
    <div className="mx-auto grid max-w-3xl gap-5 px-6 py-6">
      <div className="flex items-baseline gap-4">
        <div className="text-[34px] font-bold text-fg">{pct(d.geral)}</div>
        <div className="text-[13px] text-muted">de acerto em {d.respondidas} respostas</div>
        {d.fracos.length > 0 && (
          <button className={`${btnPrimary} ml-auto`} disabled={ctx.rodando}
                  onClick={() => { ctx.disparar("/prova", { n: 10, tipo: "multipla", dificuldade: "media", topicos: d.fracos }); irParaProvas(); }}>
            Prova dos {d.fracos.length} tópicos fracos
          </button>
        )}
      </div>
      <section className={card}>
        <div className={`${rotulo} mb-3`}>Por tópico</div>
        <div className="grid gap-2.5">
          {ctx.estudo.programa.map((t) => {
            const x = por[t.id];
            return (
              <div key={t.id} className="grid grid-cols-[1fr_9rem_4.5rem] items-center gap-3 text-[13px]">
                <span className={`truncate ${d.fracos.includes(t.id) ? "text-warn" : "text-fg-2"}`}>{t.titulo}</span>
                <div className="h-1.5 overflow-hidden rounded-full bg-raised">
                  {x && <div className={`h-full ${corAcerto(x.acerto)}`} style={{ width: `${Math.round(x.acerto * 100)}%` }} />}
                </div>
                <span className="text-right font-mono text-[12px] text-muted">{x ? `${x.acertos}/${x.total}` : "—"}</span>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

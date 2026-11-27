import { useState } from "react";
import { api } from "../../api";
import { X } from "../icons";
import type { Ctx } from "./EstudoView";
import type { Duvida } from "./tipos";
import { Texto, btnLeve, btnPrimary, campo, rotulo } from "./ui";

/** Painel lateral de dúvidas: o tutor responde pelas fontes da matéria e diz quando elas não cobrem. */
export default function Tutor({ ctx, onClose }: { ctx: Ctx; onClose: () => void }) {
  const [pergunta, setPergunta] = useState("");
  const [esperando, setEsperando] = useState("");
  async function perguntar() {
    const p = pergunta.trim();
    if (!p || esperando) return;
    setEsperando(p);
    setPergunta("");
    try {
      await api.post<Duvida>(`/estudo/${ctx.conv}/duvida`, { pergunta: p, ...ctx.motor });
      await ctx.recarregar();
    } catch (e: any) {
      ctx.onError(e.message);
      setPergunta(p);
    }
    setEsperando("");
  }
  return (
    <aside className="flex w-[26rem] shrink-0 flex-col border-l border-line bg-surface">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <div className={rotulo}>Dúvidas</div>
        <span className="text-[11.5px] text-faint">respondidas pelas fontes da matéria</span>
        <button className={`${btnLeve} ml-auto`} onClick={onClose} aria-label="Fechar"><X className="size-3" /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {ctx.estudo.duvidas.map((d, i) => (
          <div key={i} className="mb-5">
            <div className="mb-1.5 rounded-lg bg-raised px-3 py-2 text-[13.5px] text-fg">{d.pergunta}</div>
            <Texto md={d.resposta} onTrecho={(id) => ctx.abrirTrecho(id)} className="[&_.md]:text-[13.5px]!" />
          </div>
        ))}
        {esperando && <div className="mb-5"><div className="mb-1.5 rounded-lg bg-raised px-3 py-2 text-[13.5px] text-fg">{esperando}</div>
          <div className="animate-pulse text-[13px] text-muted">Procurando nas fontes…</div></div>}
        {!ctx.estudo.duvidas.length && !esperando && <div className="text-[13px] text-muted">Pergunte qualquer coisa da matéria.</div>}
      </div>
      <div className="border-t border-line p-3">
        <textarea className={`${campo} min-h-16 resize-none text-[13.5px]`} value={pergunta} onChange={(e) => setPergunta(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); perguntar(); } }}
                  placeholder="Ex.: qual a diferença entre habeas data e mandado de segurança?" />
        <div className="mt-2 flex justify-end"><button className={btnPrimary} disabled={!pergunta.trim() || !!esperando} onClick={perguntar}>Perguntar</button></div>
      </div>
    </aside>
  );
}

import { useState } from "react";

// Perguntas antes do design (como o Claude Design faz): opções clicáveis e sempre um campo livre.
// "Responder" planeja com as respostas; "Pular" planeja só com o pedido.

export type Pergunta = { pergunta: string; opcoes: string[]; multipla: boolean };

export default function DesignPerguntas(props: {
  perguntas: Pergunta[];
  desabilitado?: boolean;
  onResponder: (respostas: { pergunta: string; resposta: string }[]) => void;
}) {
  const [marcadas, setMarcadas] = useState<string[][]>(() => props.perguntas.map(() => []));
  const [livres, setLivres] = useState<string[]>(() => props.perguntas.map(() => ""));
  const alterna = (i: number, o: string, multipla: boolean) =>
    setMarcadas((m) => m.map((x, j) => (j !== i ? x : x.includes(o) ? x.filter((y) => y !== o) : multipla ? [...x, o] : [o])));
  const respostas = props.perguntas.map((q, i) => ({ pergunta: q.pergunta, resposta: [...marcadas[i], livres[i].trim()].filter(Boolean).join(", ") }));

  return (
    <div className="w-full rounded-2xl border border-line bg-surface p-3.5">
      <div className="mb-2 font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">Antes de desenhar</div>
      <div className="flex flex-col gap-3">
        {props.perguntas.map((q, i) => (
          <div key={i}>
            <p className="mb-1.5 text-[13.5px] text-fg">{q.pergunta}{q.multipla && <span className="ml-1 text-[11.5px] text-faint">(pode marcar várias)</span>}</p>
            <div className="flex flex-wrap gap-1.5">
              {q.opcoes.map((o) => {
                const on = marcadas[i].includes(o);
                return (
                  <button key={o} onClick={() => alterna(i, o, q.multipla)} aria-pressed={on}
                          className={`rounded-full border px-2.5 py-0.5 text-[12.5px] ${on ? "border-accent-line bg-accent-soft text-accent-text" : "border-line text-fg-2 hover:bg-raised"}`}>
                    {o}
                  </button>
                );
              })}
              <input value={livres[i]} onChange={(e) => setLivres((l) => l.map((x, j) => (j === i ? e.target.value : x)))}
                     placeholder={q.opcoes.length ? "outra…" : "sua resposta"}
                     className="min-w-32 flex-1 rounded-full border border-line bg-raised px-2.5 py-0.5 text-[12.5px] text-fg focus:border-focus focus:outline-none" />
            </div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        <button disabled={props.desabilitado} onClick={() => props.onResponder([])} className="rounded-[9px] px-3 py-1.5 text-sm text-muted hover:text-fg disabled:opacity-40">
          Pular e planejar
        </button>
        <button disabled={props.desabilitado || !respostas.some((r) => r.resposta)} onClick={() => props.onResponder(respostas.filter((r) => r.resposta))}
                className="rounded-[9px] bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:brightness-110 disabled:opacity-40">
          Responder e planejar
        </button>
      </div>
    </div>
  );
}

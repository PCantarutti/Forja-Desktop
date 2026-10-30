import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

type Receita = { id: string; nome: string; palavras: string[]; objetivo: string; passos: string[]; conferir: string; armadilhas: string;
                 status: "rascunho" | "aprovada"; usos: number; acertos: number; falhas: number; atualizada: string; anterior: unknown };

/** Receitas das tarefas faladas ao F.O.R.J.A (backend/app/receitas.py): na página dele (Dados) e em Configurações › Memória. */
const vezes = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`;

export default function ReceitasTarefas({ onError, nota = true }: { onError: (e: string) => void; nota?: boolean }) {
  const [receitas, setReceitas] = useState<Receita[]>([]);
  const [aberta, setAberta] = useState("");
  const carrega = useCallback(() => api.get<Receita[]>("/assistente/receitas").then(setReceitas).catch(() => {}), []);
  useEffect(() => { carrega(); const t = setInterval(carrega, 5000); return () => clearInterval(t); }, [carrega]);
  return (
    <div className="space-y-1.5">
      {receitas.length === 0 && <span className="text-[11.5px] text-faint">Nenhuma ainda. Peça uma tarefa por voz (“Forja, toca tal música no Spotify”) e diga se ficou certo.</span>}
      {receitas.map((r) => (
        <div key={r.id} className="rounded-[8px] border border-line bg-surface px-2.5 py-1.5">
          <div className="flex items-center gap-2">
            <button className="min-w-0 flex-1 truncate text-left text-[12px] text-fg" title={r.objetivo || r.nome}
                    onClick={() => setAberta(aberta === r.id ? "" : r.id)}>{r.nome}</button>
            <span className={`shrink-0 text-[11px] ${r.status === "aprovada" ? "text-accent-text" : "text-faint"}`}>
              {r.status === "aprovada" ? "aprovada" : r.anterior ? "corrigida, a confirmar" : "a confirmar"} · {r.usos ? `${vezes(r.acertos, "acerto", "acertos")} · ${vezes(r.falhas, "falha", "falhas")}` : "ainda não usada"}
            </span>
            {r.status !== "aprovada" && (
              <button className="text-[11.5px] text-faint hover:text-fg"
                      onClick={() => api.post(`/assistente/receitas/${r.id}/aprovar`).then(carrega).catch((er) => onError(er.message))}>aprovar</button>
            )}
            <button aria-label={`Apagar a receita ${r.nome}`} className="text-[11.5px] text-faint hover:text-diff-del-fg"
                    onClick={() => api.del(`/assistente/receitas/${r.id}`).then(carrega).catch((er) => onError(er.message))}>apagar</button>
          </div>
          {aberta === r.id && (
            <div className="mt-1.5 space-y-1 border-t border-line pt-1.5 text-[11.5px] leading-relaxed text-muted">
              <ol className="list-decimal pl-4">{r.passos.map((p, i) => <li key={i}>{p}</li>)}</ol>
              <p><span className="text-faint">Conferir:</span> {r.conferir}</p>
              {r.armadilhas && <p><span className="text-faint">Armadilhas:</span> {r.armadilhas}</p>}
              <p className="text-faint">Palavras: {r.palavras.join(", ")} · atualizada {r.atualizada}</p>
            </div>
          )}
        </div>
      ))}
      {nota && <p className="text-[11.5px] leading-relaxed text-faint">O caminho que já deu certo em cada TIPO de tarefa falada (“tocar uma música no Spotify”, não a música da vez). Pedido parecido segue a receita direto; caminho novo ou corrigido só vale depois do seu “sim” (“pode salvar”, “funcionou” também valem).</p>}
    </div>
  );
}

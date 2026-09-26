import { useEffect, useState } from "react";
import CartaoEstado from "./CartaoEstado";
import { api } from "../api";

type Goal = { id: number; objective: string; status: string; armada: boolean; rodada: number; max: number; motivo: string };

const ROTULO: Record<string, string> = { ativa: "ativa", pausada: "pausada", bloqueada: "bloqueada", completa: "completa" };

/** Faixa da goal acima do campo de mensagem (como a do DeepSeek Harness): objetivo, rodada e controles. */
export default function GoalStrip({ convId, refreshKey }: { convId: number | null; refreshKey: number }) {
  // Guardada junto com a conversa de onde veio: trocar de conversa não mostra a goal da anterior.
  const [dado, setDado] = useState<{ conv: number; goal: Goal | null } | null>(null);
  const setGoal = (g: Goal | null) => convId && setDado({ conv: convId, goal: g });
  useEffect(() => {
    if (!convId) return;
    api.get<{ goal: Goal | null }>(`/conversations/${convId}/goal`)
      .then((r) => setDado({ conv: convId, goal: r.goal }))
      .catch(() => setDado({ conv: convId, goal: null }));
  }, [convId, refreshKey]);
  const goal = dado && dado.conv === convId ? dado.goal : null;
  if (!goal || goal.status === "completa") return null;

  const agir = (action: string) =>
    api.post<{ goal: Goal | null }>(`/conversations/${convId}/goal`, { action }).then((r) => setGoal(r.goal)).catch(() => {});
  const girando = goal.status === "ativa" && goal.armada;
  const btn = "shrink-0 rounded-[7px] border border-line-strong px-2 py-0.5 text-xs text-fg-2 hover:bg-raised hover:text-fg";
  return (
    <CartaoEstado compacto rotulo="Objetivo" className="mb-2"
      acoes={<>
      {girando || goal.status === "bloqueada" ? (
        <button className={btn} onClick={() => agir("pause")}>Pausar</button>
      ) : (
        <button className={btn} onClick={() => agir("resume")} title="Volta a girar as rodadas no próximo turno">Retomar</button>
      )}
      <button className={btn} onClick={() => agir("clear")} title="Descarta a goal desta conversa">Descartar</button>
      </>}>
      <span className="min-w-0 flex-1 truncate" title={goal.objective}>{goal.objective}</span>
      <span className="shrink-0 font-mono text-[11px] text-faint" title={goal.motivo || undefined}>
        {ROTULO[goal.status] ?? goal.status}
        {goal.status === "ativa" && !goal.armada ? " (parada até retomar)" : ""} · rodada {goal.rodada}
      </span>
    </CartaoEstado>
  );
}

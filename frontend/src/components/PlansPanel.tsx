import { useState } from "react";
import { Markdown } from "./MessageView";
import { Chevron } from "./icons";

/** Plano proposto pelo agente (chamada exit_plan_mode) e o que aconteceu com ele. */
export type PlanEntry = {
  callId: string;
  plan: string;
  status: "pendente" | "aprovado" | "ajustes" | "cancelado";
  mode?: string; // modo de permissão escolhido na aprovação
  order: number; // posição na conversa (para numerar)
};

const STATUS: Record<PlanEntry["status"], [string, string]> = {
  pendente: ["aguardando você", "text-sky-300"],
  aprovado: ["aprovado", "text-emerald-400"],
  ajustes: ["ajustes pedidos", "text-orange-400"],
  cancelado: ["cancelado", "text-faint"],
};

function title(plan: string) {
  const line = plan.split("\n").find((l) => l.trim()) ?? "";
  return line.replace(/^#+\s*/, "").replace(/\*\*/g, "").slice(0, 80) || "(plano vazio)";
}

/** Aba Planos: todos os planos do modo Plano desta conversa, do mais recente ao mais antigo. */
export default function PlansPanel(props: { plans: PlanEntry[]; onJump: (callId: string) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const list = [...props.plans].reverse();
  if (!list.length)
    return (
      <div className="p-3 text-xs">
        <div className="rounded-2xl border border-line bg-surface p-3.5 text-muted">
          Nenhum plano nesta conversa. Com a permissão em <span className="text-fg">Plano</span>, o agente investiga sem
          alterar nada e propõe um plano aqui antes de executar.
        </div>
      </div>
    );
  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto p-3 text-xs">
      <div className="text-faint">
        {list.length} plano{list.length > 1 ? "s" : ""} nesta conversa. Clique para ler; "ir ao chat" rola até o card.
      </div>
      {list.map((p) => {
        const [label, cls] = STATUS[p.status];
        const isOpen = open === p.callId;
        return (
          <section key={p.callId} className="overflow-hidden rounded-2xl border border-line bg-surface">
            <button onClick={() => setOpen(isOpen ? null : p.callId)} className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left hover:bg-raised/50">
              <span className="shrink-0 rounded bg-raised px-1.5 font-mono text-[10px] text-muted">#{p.order}</span>
              <span className="min-w-0 flex-1 truncate text-fg" title={title(p.plan)}>
                {title(p.plan)}
              </span>
              <span className={`shrink-0 ${cls}`}>● {label}</span>
              <Chevron className="size-3.5 shrink-0 text-faint" />
            </button>
            {isOpen && (
              <div className="border-t border-line">
                <div className="max-h-[50vh] overflow-y-auto px-3.5 py-3">
                  <Markdown text={p.plan} />
                </div>
                <div className="flex items-center gap-2 border-t border-line px-3.5 py-2 text-faint">
                  {p.mode && <span>executado em modo {p.mode}</span>}
                  <button onClick={() => props.onJump(p.callId)} className="ml-auto rounded-full border border-line px-3 py-1 text-fg hover:bg-raised">
                    ir ao chat
                  </button>
                </div>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

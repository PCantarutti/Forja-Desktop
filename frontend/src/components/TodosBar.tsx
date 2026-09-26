import { useState } from "react";
import type { Task } from "../types";
import { ChevronDown, Clipboard } from "./icons";

/**
 * Lista de tarefas do agente logo acima do campo de mensagem, recolhível (como a "To-dos" do DeepSeek
 * Harness): fechada, é uma linha com a contagem por estado; aberta, a lista inteira com rolagem.
 */
export default function TodosBar({ tasks, live }: { tasks: Task[]; live?: boolean }) {
  const [aberta, setAberta] = useState(false);
  if (!tasks.length) return null;
  const n = (s: Task["status"]) => tasks.filter((t) => t.status === s).length;
  const resumo = [
    `${n("done")} concluída${n("done") === 1 ? "" : "s"}`,
    n("doing") ? `${n("doing")} em andamento` : "",
    n("pending") ? `${n("pending")} pendente${n("pending") === 1 ? "" : "s"}` : "",
  ].filter(Boolean).join(" · ");
  return (
    <div className="mb-2 rounded-xl border border-line bg-bg text-[13px]">
      <button onClick={() => setAberta((v) => !v)} aria-expanded={aberta}
        className="flex w-full items-center gap-2 px-3 py-[7px] text-left">
        <Clipboard className="size-3.5 shrink-0 text-muted" />
        <span className="font-medium text-fg">Tarefas</span>
        <span className="truncate text-muted">{resumo}</span>
        <span className="h-[3px] w-16 shrink-0 rounded-full bg-line">
          <span className="block h-full rounded-full bg-accent" style={{ width: `${(n("done") / tasks.length) * 100}%` }} />
        </span>
        {live && n("doing") > 0 && <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-sky-400" />}
        <ChevronDown className={`ml-auto size-4 shrink-0 text-faint transition-transform ${aberta ? "rotate-180" : ""}`} />
      </button>
      {aberta && (
        <ul className="max-h-48 space-y-1 overflow-y-auto border-t border-line px-3 py-2">
          {tasks.map((t, i) => (
            <li key={i} className="flex items-center gap-2">
              <span className={`size-1.5 shrink-0 rounded-full ${
                t.status === "done" ? "bg-emerald-400" : t.status === "doing" ? "animate-pulse bg-sky-400" : "bg-faint/60"
              }`} />
              <span className={t.status === "done" ? "text-muted" : t.status === "doing" ? "text-fg" : "text-muted"}>{t.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

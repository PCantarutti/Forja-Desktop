import type { Message, Stats, ToolCall } from "../types";

type Linha = { tipo: "user" | "event"; m: Message } | { tipo: "passo"; m: Message; n: number };

const k = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n));

/**
 * Linha do tempo crua da conversa (como a aba Trajectory do DeepSeek Harness): cada passo do modelo
 * com duração, tokens de entrada/saída, cache e as ferramentas que ele chamou — para entender por que o
 * agente fez o que fez sem abrir bloco por bloco.
 */
export default function Trajetoria({ messages }: { messages: Message[] }) {
  const resultados = new Map(messages.filter((m) => m.role === "tool").map((m) => [m.tool_call_id, m]));
  const numero = new Map(
    messages.filter((m) => m.role === "assistant" && m.meta?.stats).map((m, i) => [m.id, i + 1]),
  );
  const linhas = messages.flatMap((m): Linha[] => {
    if (m.role === "user") return [{ tipo: "user", m }];
    if (m.role === "event" && ["goal", "aviso", "nudge", "mudanca", "hook", "skill"].includes(String(m.meta?.kind)))
      return [{ tipo: "event", m }];
    if (m.role !== "assistant" || !m.meta?.stats) return [];
    return [{ tipo: "passo", m, n: numero.get(m.id) ?? 0 }];
  });
  if (!linhas.length) return <div className="p-4 text-sm text-faint">Nenhum passo ainda nesta conversa.</div>;

  return (
    <div className="h-full overflow-y-auto p-3 font-mono text-[11px] text-muted">
      {linhas.map((l) => {
        if (l.tipo === "user")
          return (
            <div key={l.m.id} className="my-2 truncate border-l-2 border-sky-500/60 pl-2 text-fg" title={l.m.content}>
              usuário: {l.m.content}
            </div>
          );
        if (l.tipo === "event")
          return (
            <div key={l.m.id} className="my-1 truncate pl-2 text-violet-300/80" title={l.m.content}>
              {String(l.m.meta?.kind)}: {l.m.content}
            </div>
          );
        if (l.tipo !== "passo") return null;
        const s = l.m.meta!.stats as Stats;
        const calls: ToolCall[] = l.m.tool_calls ?? [];
        return (
          <div key={l.m.id} className="my-1 rounded-lg border border-line bg-surface px-2 py-1.5">
            <div className="flex flex-wrap items-center gap-x-2 text-faint">
              <span className="text-fg">#{l.n}</span>
              <span>{s.seconds.toFixed(1)}s</span>
              <span title="tokens de entrada (prompt)">in {k(s.prompt_tokens)}{s.estimated ? "~" : ""}</span>
              {s.cached != null && <span title="tokens do prompt vindos do cache">cache {k(s.cached)}</span>}
              <span title="tokens gerados">out {k(s.tokens)}</span>
              {s.tps != null && <span>{s.tps.toFixed(0)} t/s</span>}
              <span className="ml-auto truncate">{s.model}</span>
            </div>
            {l.m.thinking && <div className="mt-0.5 truncate text-faint/80" title={l.m.thinking}>pensou: {l.m.thinking}</div>}
            {calls.map((c) => {
              const r = resultados.get(c.id);
              const cor = !r ? "text-faint" : r.status === "ok" ? "text-emerald-300/80" : "text-red-300/80";
              return (
                <div key={c.id} className="mt-0.5 flex gap-2" title={JSON.stringify(c.arguments)}>
                  <span className={cor}>{c.name}</span>
                  <span className="min-w-0 flex-1 truncate">{JSON.stringify(c.arguments)}</span>
                  {r?.meta?.segundos != null && <span className="text-faint">{Number(r.meta.segundos).toFixed(1)}s</span>}
                  {r && r.status !== "ok" && <span className="text-red-300/80">{r.status}</span>}
                </div>
              );
            })}
            {!calls.length && l.m.content && <div className="mt-0.5 truncate text-fg/80">resposta: {l.m.content}</div>}
          </div>
        );
      })}
    </div>
  );
}

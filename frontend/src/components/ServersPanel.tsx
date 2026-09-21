import { useEffect, useState } from "react";
import { api } from "../api";
import type { ServerInfo, SubagentActive } from "../types";
import { Split, Square } from "./icons";
import { UsageBars, useCloudUsage } from "./CloudUsage";

const NIVEIS: Record<string, string> = { rapido: "Rápido", capaz: "Capaz", nuvem: "Nuvem" };

const POLL_MS = 4000;

function uptime(s?: number) {
  if (s == null) return "";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${s % 60}s`;
  return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
}

/** Aba Instâncias: o que está rodando agora — delegações em andamento (de qualquer conversa) e os
 * servidores que o agente subiu com serve_start. */
export default function ServersPanel(props: { onCount: (alive: number) => void; onOpen?: (convId: number) => void }) {
  const [subs, setSubs] = useState<SubagentActive[]>([]);
  const nuvens = useCloudUsage();
  const [servers, setServers] = useState<ServerInfo[] | null>(null);
  const [environment, setEnvironment] = useState("");
  const [error, setError] = useState("");
  const [openLog, setOpenLog] = useState<string | null>(null);
  const [log, setLog] = useState("");

  async function refresh() {
    try {
      const [r, d] = await Promise.all([
        api.get<{ servers: ServerInfo[]; environment: string }>("/servers"),
        api.get<{ subagents: SubagentActive[] }>("/subagents/active"),
      ]);
      setServers(r.servers);
      setSubs(d.subagents);
      setEnvironment(r.environment);
      props.onCount(r.servers.filter((s) => s.alive).length + d.subagents.length);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!openLog) return;
    const load = () =>
      api
        .get<{ log: string }>(`/servers/${encodeURIComponent(openLog)}/log?tail=120`)
        .then((r) => setLog(r.log || "(log vazio)"))
        .catch((e) => setLog(e.message));
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [openLog]);

  async function stopRun(runId: string) {
    try {
      await api.post(`/runs/${encodeURIComponent(runId)}/stop`);
      await refresh();
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function stop(name: string) {
    try {
      await api.post(`/servers/${encodeURIComponent(name)}/stop`);
      if (openLog === name) setOpenLog(null);
      await refresh();
    } catch (e: any) {
      setError(e.message);
    }
  }

  const list = servers ?? [];
  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3 text-xs">
      <div className="text-faint">
        Subagentes trabalhando e servidores iniciados com <span className="font-mono">serve_start</span>, em{" "}
        {environment || "…"}.
      </div>
      {error && <div className="text-red-300">{error}</div>}
      {subs.map((s) => (
        <section key={s.id} className="rounded-2xl border border-sky-500/30 bg-sky-500/5 p-3.5">
          <div className="flex items-center gap-2">
            <Split className="size-3.5 shrink-0 text-sky-300" />
            <span className="truncate font-medium text-fg">
              subagente {NIVEIS[s.level] ?? s.level} · {s.model}
            </span>
            <span className="ml-auto shrink-0 text-faint">{uptime(s.seconds)}</span>
          </div>
          <div className="mt-1.5 truncate text-muted" title={s.task}>
            {s.task}
          </div>
          <div className="truncate text-faint" title={s.conversation}>
            {s.conversation ? `${s.conversation} · ` : ""}
            {s.steps} passos · {s.iterations} iterações
            {s.tokens ? ` · ${s.tokens.toLocaleString("pt-BR")} tokens` : ""}
          </div>
          {s.status && <div className="mt-1 animate-pulse truncate text-sky-300">{s.status}</div>}
          {nuvens
            .filter((u) => u.provider === s.provider)
            .map((u) => (
              <div key={u.provider} className="mt-2 border-t border-line pt-2">
                <div className="mb-1.5 text-faint">Cota · {u.name}</div>
                <UsageBars data={u} />
              </div>
            ))}
          <div className="mt-2 flex gap-2">
            {props.onOpen && (
              <button
                onClick={() => props.onOpen?.(s.conversation_id)}
                className="rounded-full border border-line px-3 py-1 text-fg hover:bg-raised"
              >
                Abrir conversa
              </button>
            )}
            <button
              onClick={() => stopRun(s.run_id)}
              className="inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1 text-fg hover:bg-raised"
              title="Interrompe o turno inteiro desta conversa"
            >
              <Square className="size-3" /> Parar turno
            </button>
          </div>
        </section>
      ))}
      {servers && !list.length && !subs.length && (
        <div className="rounded-2xl border border-line bg-surface p-3.5 text-muted">
          Nada rodando agora. Quando o agente delegar para um subagente ou subir um servidor (npm run dev, uvicorn…),
          aparece aqui — e você pode parar.
        </div>
      )}
      {list.map((s) => (
        <section key={`${s.where}-${s.name}`} className="rounded-2xl border border-line bg-surface p-3.5">
          <div className="flex items-center gap-2">
            <span className={s.alive ? "text-emerald-400" : "text-faint"}>●</span>
            <span className="truncate font-mono font-medium text-fg" title={s.name}>
              {s.name}
            </span>
            <span className="rounded bg-raised px-1.5 text-[10px] text-muted">{s.where === "host" ? "seu sistema" : "container"}</span>
            <span className="ml-auto shrink-0 text-faint">
              {s.alive ? `rodando · ${uptime(s.uptime)}` : s.error ? "erro" : `parado (exit ${s.exit_code ?? "?"})`}
            </span>
          </div>
          <div className="mt-1.5 truncate font-mono text-muted" title={s.command}>
            $ {s.command || s.error}
          </div>
          {s.cwd && (
            <div className="truncate text-faint" title={s.cwd}>
              {s.cwd}
              {s.pid ? ` · pid ${s.pid}` : ""}
            </div>
          )}
          {!s.error && (
            <div className="mt-2 flex gap-2">
              {s.alive && (
                <button
                  onClick={() => stop(s.name)}
                  className="inline-flex items-center gap-1.5 rounded-full bg-fg px-3 py-1 font-medium text-black hover:bg-white"
                >
                  <Square className="size-3" /> Parar
                </button>
              )}
              <button
                onClick={() => setOpenLog(openLog === s.name ? null : s.name)}
                className="rounded-full border border-line px-3 py-1 text-fg hover:bg-raised"
              >
                {openLog === s.name ? "Ocultar log" : "Log"}
              </button>
            </div>
          )}
          {openLog === s.name && (
            <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-[#0d0d0d] p-2.5 font-mono text-[11px] whitespace-pre-wrap text-muted">
              {log}
            </pre>
          )}
        </section>
      ))}
    </div>
  );
}

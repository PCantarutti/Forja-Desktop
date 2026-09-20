import { useEffect, useState } from "react";
import { api } from "../api";
import type { ServerInfo } from "../types";
import { Square } from "./icons";

const POLL_MS = 4000;

function uptime(s?: number) {
  if (s == null) return "";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${s % 60}s`;
  return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
}

/** Aba Instâncias: servidores que o agente subiu com serve_start, com log e botão Parar. */
export default function ServersPanel(props: { onCount: (alive: number) => void }) {
  const [servers, setServers] = useState<ServerInfo[] | null>(null);
  const [environment, setEnvironment] = useState("");
  const [error, setError] = useState("");
  const [openLog, setOpenLog] = useState<string | null>(null);
  const [log, setLog] = useState("");

  async function refresh() {
    try {
      const r = await api.get<{ servers: ServerInfo[]; environment: string }>("/servers");
      setServers(r.servers);
      setEnvironment(r.environment);
      props.onCount(r.servers.filter((s) => s.alive).length);
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
        Servidores iniciados pelo agente com <span className="font-mono">serve_start</span>, em {environment || "…"}.
      </div>
      {error && <div className="text-red-300">{error}</div>}
      {servers && !list.length && (
        <div className="rounded-2xl border border-line bg-surface p-3.5 text-muted">
          Nenhuma instância rodando. Quando o agente subir um servidor (npm run dev, uvicorn…), ele aparece aqui e você pode
          pará-lo.
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

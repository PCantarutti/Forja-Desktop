import { useEffect, useState } from "react";
import { api } from "../api";
import type { ServerInfo, SubagentActive } from "../types";
import { Chevron, Split, Square, Trash } from "./icons";
import { UsageBars, useCloudUsage } from "./CloudUsage";

const NIVEIS: Record<string, string> = { rapido: "Rápido", capaz: "Capaz", nuvem: "Nuvem" };

const POLL_MS = 4000;

/** Mais recente primeiro: quem está no ar há menos tempo nasceu por último. */
function recentes<T>(itens: T[], idade: (t: T) => number | undefined): T[] {
  return [...itens].sort((a, b) => (idade(a) ?? 0) - (idade(b) ?? 0));
}

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
  const [aberto, setAberto] = useState<string | null>(null); // um cartão expandido por vez: a lista fica legível
  const [verProntos, setVerProntos] = useState(false);

  async function limpar() {
    try {
      await api.post("/servers/clear");
      await refresh();
    } catch (e: any) {
      setError(e.message);
    }
  }
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
  const vivos = list.filter((s) => s.alive);
  const prontos = list.filter((s) => !s.alive);
  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3 text-xs">
      {error && <div className="text-red-300">{error}</div>}
      <div className="text-faint">Em execução</div>
      {recentes(subs, (s) => s.seconds).map((s) => (
        <section key={s.id} className="overflow-hidden rounded-2xl border border-sky-500/30 bg-sky-500/5">
          <button
            onClick={() => setAberto(aberto === s.id ? null : s.id)}
            className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left hover:bg-sky-500/10"
          >
            <Split className="size-3.5 shrink-0 text-sky-300" />
            <span className="truncate font-medium text-fg">
              subagente {NIVEIS[s.level] ?? s.level} · {s.model}
            </span>
            <span className="ml-auto shrink-0 text-faint">{uptime(s.seconds)}</span>
            <Chevron className={`size-3.5 shrink-0 text-faint ${aberto === s.id ? "rotate-180" : ""}`} />
          </button>
          <div className={`px-3.5 pb-3.5 ${aberto === s.id ? "" : "hidden"}`}>
          <div className="truncate text-muted" title={s.task}>
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
          </div>
        </section>
      ))}
      {servers && !vivos.length && !subs.length && (
        <div className="rounded-2xl border border-line bg-surface p-3.5 text-muted">
          Nada rodando agora. Quando o agente delegar para um subagente ou subir um processo (npm run dev, uma
          build…), aparece aqui — e você pode parar.
        </div>
      )}
      {recentes(vivos, (s) => s.uptime).map((s) => (
        <section key={`${s.where}-${s.name}`} className="overflow-hidden rounded-2xl border border-line bg-surface">
          <button
            onClick={() => setAberto(aberto === s.name ? null : s.name)}
            className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left hover:bg-raised/50"
          >
            <span className={s.alive ? "text-emerald-400" : "text-faint"}>●</span>
            <span className="truncate font-mono font-medium text-fg" title={s.name}>
              {s.name}
            </span>
            <span className="rounded bg-raised px-1.5 text-[10px] text-muted">{s.where === "host" ? "seu sistema" : "container"}</span>
            <span className="ml-auto shrink-0 text-faint">
              {s.alive ? `rodando · ${uptime(s.uptime)}` : s.error ? "erro" : `parado (exit ${s.exit_code ?? "?"})`}
            </span>
            <Chevron className={`size-3.5 shrink-0 text-faint ${aberto === s.name ? "rotate-180" : ""}`} />
          </button>
          <div className={`px-3.5 pb-3.5 ${aberto === s.name ? "" : "hidden"}`}>
          <div className="truncate font-mono text-muted" title={s.command}>
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
          </div>
        </section>
      ))}
      {!!prontos.length && (
        <>
          <div className="mt-1 flex items-center gap-2 text-faint">
            <button onClick={() => setVerProntos(!verProntos)} className="inline-flex items-center gap-1.5 hover:text-fg">
              Concluído {prontos.length}
              <Chevron className={`size-3.5 ${verProntos ? "rotate-180" : ""}`} />
            </button>
            <button onClick={limpar} title="Limpar os terminados da lista" className="ml-auto rounded p-1 hover:bg-raised hover:text-fg">
              <Trash className="size-3.5" />
            </button>
          </div>
          {verProntos &&
            recentes(prontos, (s) => s.uptime).map((s) => (
              <section key={`done-${s.where}-${s.name}`} className="overflow-hidden rounded-2xl border border-line bg-surface/60">
                <button
                  onClick={() => setAberto(aberto === s.name ? null : s.name)}
                  className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left hover:bg-raised/40"
                >
                  <span className="text-faint">○</span>
                  <span className="truncate font-mono text-muted" title={s.name}>
                    {s.name}
                  </span>
                  <span className="ml-auto shrink-0 text-faint">
                    {s.error ? "erro" : `exit ${s.exit_code ?? "?"}`}
                  </span>
                  <Chevron className={`size-3.5 shrink-0 text-faint ${aberto === s.name ? "rotate-180" : ""}`} />
                </button>
                <div className={`px-3.5 pb-3.5 ${aberto === s.name ? "" : "hidden"}`}>
                  <div className="truncate font-mono text-muted" title={s.command}>
                    $ {s.command || s.error}
                  </div>
                  {s.cwd && <div className="truncate text-faint">{s.cwd}</div>}
                  <button
                    onClick={() => setOpenLog(openLog === s.name ? null : s.name)}
                    className="mt-2 rounded-full border border-line px-3 py-1 text-fg hover:bg-raised"
                  >
                    {openLog === s.name ? "Ocultar log" : "Log"}
                  </button>
                  {openLog === s.name && (
                    <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-[#0d0d0d] p-2.5 font-mono text-[11px] whitespace-pre-wrap text-muted">
                      {log}
                    </pre>
                  )}
                </div>
              </section>
            ))}
        </>
      )}
      <div className="mt-1 text-faint">
        Subagentes e processos de fundo, em {environment || "…"}.
      </div>
    </div>
  );
}

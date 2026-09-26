import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { ServerInfo, SubagentActive } from "../types";
import { Chevron, Split, Square, Trash } from "./icons";
import { UsageBars, useCloudUsage } from "./CloudUsage";

const NIVEIS: Record<string, string> = { rapido: "Rápido", capaz: "Capaz", nuvem: "Nuvem" };

const POLL_MS = 4000;
const SAIDA_LINHAS = 12; // últimas linhas do log que aparecem dentro do cartão

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

/** Cartão de uma instância: título, uma linha de contexto e, ao abrir, comando e saída. */
function Cartao(props: {
  aberto: boolean;
  onToggle: () => void;
  titulo: string;
  sub: string;
  ponto: ReactNode;
  acao?: ReactNode;
  destaque?: boolean;
  children: ReactNode;
}) {
  return (
    <section
      className={`overflow-hidden rounded-2xl border ${props.destaque ? "border-sky-500/30 bg-sky-500/5" : "border-line bg-surface"}`}
    >
      <div className="flex items-center gap-2 px-3.5 py-2.5">
        <button onClick={props.onToggle} className="flex min-w-0 flex-1 items-center gap-2 text-left">
          {props.ponto}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-fg" title={props.titulo}>
              {props.titulo}
            </span>
            <span className="block truncate text-faint">{props.sub}</span>
          </span>
        </button>
        {props.acao}
        <button onClick={props.onToggle} className="shrink-0 rounded p-1 text-faint hover:bg-raised hover:text-fg">
          <Chevron className={`size-3.5 ${props.aberto ? "rotate-180" : ""}`} />
        </button>
      </div>
      {props.aberto && <div className="space-y-2 px-3.5 pb-3.5">{props.children}</div>}
    </section>
  );
}

/** Comando e saída, no mesmo formato dos blocos de código do chat. */
function Bloco({ texto, vazio, comando }: { texto: string; vazio?: string; comando?: boolean }) {
  return (
    <pre
      className={`max-h-60 overflow-auto rounded-xl p-2.5 font-mono text-[11px] whitespace-pre-wrap ${
        comando ? "bg-code text-fg/90" : "bg-raised/60 text-muted"
      } ${texto ? "" : "text-faint italic"}`}
    >
      {texto || vazio}
    </pre>
  );
}

/** Aba Instâncias: delegações e processos de fundo, separados pela conversa que os iniciou. */
export default function ServersPanel(props: {
  onCount: (alive: number) => void;
  onOpen?: (convId: number) => void;
  current?: number | null;
}) {
  const [subs, setSubs] = useState<SubagentActive[]>([]);
  const nuvens = useCloudUsage();
  const [servers, setServers] = useState<ServerInfo[] | null>(null);
  const [environment, setEnvironment] = useState("");
  const [error, setError] = useState("");
  const [aberto, setAberto] = useState<string | null>(null); // um cartão por vez: a lista fica legível
  const [verProntos, setVerProntos] = useState(false);
  const [verOutras, setVerOutras] = useState(false);
  const [log, setLog] = useState("");

  async function refresh() {
    try {
      const [r, d] = await Promise.all([
        api.get<{ servers: ServerInfo[]; environment: string }>("/servers"),
        api.get<{ subagents: SubagentActive[] }>("/subagents/active"),
      ]);
      setSubs(d.subagents);
      setServers(r.servers);
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

  // O log do cartão aberto vem junto: a saída faz parte do cartão, não de um botão à parte.
  useEffect(() => {
    if (!aberto) return setLog("");
    const load = () =>
      api
        .get<{ log: string }>(`/servers/${encodeURIComponent(aberto)}/log?tail=120`)
        .then((r) => setLog(r.log))
        .catch(() => setLog(""));
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [aberto]);

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
      await refresh();
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function limpar() {
    try {
      await api.post("/servers/clear");
      await refresh();
    } catch (e: any) {
      setError(e.message);
    }
  }

  const list = servers ?? [];
  const atual = props.current ?? null;
  const daConversa = (conv?: string) => atual !== null && String(conv ?? "") === String(atual);
  const vivos = list.filter((s) => s.alive);
  const prontos = list.filter((s) => !s.alive);
  const meusVivos = atual === null ? vivos : vivos.filter((s) => daConversa(s.conv));
  const outrosVivos = atual === null ? [] : vivos.filter((s) => !daConversa(s.conv));
  const meusSubs = atual === null ? subs : subs.filter((s) => s.conversation_id === atual);
  const outrosSubs = atual === null ? [] : subs.filter((s) => s.conversation_id !== atual);
  const ultimas = (texto: string) => texto.split("\n").slice(-SAIDA_LINHAS).join("\n").trim();

  const parar = (onClick: () => void, titulo: string) => (
    <button
      onClick={onClick}
      title={titulo}
      className="shrink-0 rounded-lg border border-line p-1.5 text-fg hover:bg-raised"
    >
      <Square className="size-3" />
    </button>
  );

  function cartaoProcesso(s: ServerInfo, concluido = false) {
    const estado = s.alive ? `rodando · ${uptime(s.uptime)}` : s.error ? "erro" : `exit ${s.exit_code ?? "?"}`;
    return (
      <Cartao
        key={s.name}
        aberto={aberto === s.name}
        onToggle={() => setAberto(aberto === s.name ? null : s.name)}
        titulo={s.name}
        sub={`Processo · ${estado}${s.pid ? ` · pid ${s.pid}` : ""}`}
        ponto={<span className={`shrink-0 ${concluido ? "text-faint" : "text-emerald-400"}`}>{concluido ? "○" : "●"}</span>}
        acao={s.alive && !s.error ? parar(() => stop(s.name), "Encerrar este processo") : undefined}
      >
        <Bloco comando texto={s.command ? `$ ${s.command}` : s.error ?? ""} />
        <Bloco texto={aberto === s.name ? ultimas(log) : ""} vazio="Sem saída ainda" />
        {s.cwd && <div className="truncate text-faint">{s.cwd}</div>}
      </Cartao>
    );
  }

  function cartaoSubagente(s: SubagentActive) {
    return (
      <Cartao
        key={s.id}
        destaque
        aberto={aberto === s.id}
        onToggle={() => setAberto(aberto === s.id ? null : s.id)}
        titulo={s.task}
        sub={`Subagente ${NIVEIS[s.level] ?? s.level} · ${s.model} · ${uptime(s.seconds)}`}
        ponto={<Split className="size-3.5 shrink-0 text-sky-300" />}
        acao={parar(() => stopRun(s.run_id), "Interrompe o turno inteiro desta conversa")}
      >
        {s.status && <div className="animate-pulse truncate text-sky-300">{s.status}</div>}
        <div className="truncate text-faint" title={s.conversation}>
          {s.conversation ? `${s.conversation} · ` : ""}
          {s.steps} passos · {s.iterations} iterações
          {s.tokens ? ` · ${s.tokens.toLocaleString("pt-BR")} tokens` : ""}
        </div>
        {nuvens
          .filter((u) => u.provider === s.provider)
          .map((u) => (
            <div key={u.provider} className="border-t border-line pt-2">
              <div className="mb-1.5 text-faint">Cota · {u.name}</div>
              <UsageBars data={u} />
            </div>
          ))}
        {props.onOpen && (
          <button
            onClick={() => props.onOpen?.(s.conversation_id)}
            className="rounded-[9px] border border-line px-3 py-1 text-fg hover:bg-raised"
          >
            Abrir conversa
          </button>
        )}
      </Cartao>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto p-3 text-xs">
      {error && <div className="text-red-300">{error}</div>}
      <div className="text-faint">Em execução</div>
      {recentes(meusSubs, (s) => s.seconds).map(cartaoSubagente)}
      {servers && !meusVivos.length && !meusSubs.length && (
        <div className="rounded-xl border border-line bg-surface p-3.5 text-muted">
          Nada rodando nesta conversa. Quando o agente delegar para um subagente ou subir um processo (npm run dev,
          uma build…), aparece aqui — e você pode parar.
        </div>
      )}
      {recentes(meusVivos, (s) => s.uptime).map((s) => cartaoProcesso(s))}

      {!!(outrosVivos.length + outrosSubs.length) && (
        <>
          <button
            onClick={() => setVerOutras(!verOutras)}
            className="mt-1 inline-flex items-center gap-1.5 text-faint hover:text-fg"
          >
            Outras conversas {outrosVivos.length + outrosSubs.length}
            <Chevron className={`size-3.5 ${verOutras ? "rotate-180" : ""}`} />
          </button>
          {verOutras && recentes(outrosSubs, (s) => s.seconds).map(cartaoSubagente)}
          {verOutras && recentes(outrosVivos, (s) => s.uptime).map((s) => cartaoProcesso(s))}
        </>
      )}

      {!!prontos.length && (
        <>
          <div className="mt-1 flex items-center gap-2 text-faint">
            <button onClick={() => setVerProntos(!verProntos)} className="inline-flex items-center gap-1.5 hover:text-fg">
              Concluído {prontos.length}
              <Chevron className={`size-3.5 ${verProntos ? "rotate-180" : ""}`} />
            </button>
            <button
              onClick={limpar}
              title="Limpar os terminados da lista"
              className="ml-auto rounded p-1 hover:bg-raised hover:text-fg"
            >
              <Trash className="size-3.5" />
            </button>
          </div>
          {verProntos && recentes(prontos, (s) => s.uptime).map((s) => cartaoProcesso(s, true))}
        </>
      )}
      <div className="mt-1 text-faint">Subagentes e processos de fundo, em {environment || "…"}.</div>
    </div>
  );
}

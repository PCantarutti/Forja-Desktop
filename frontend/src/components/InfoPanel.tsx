import { useState } from "react";
import type { Settings, ToolsSent } from "../types";
import type { Section } from "./Controls";

export type ToolInfo = { name: string; description?: string; mutating: boolean; always_ask?: boolean; source?: string; enabled?: boolean };
export type McpStatus = {
  config: string;
  config_error: string;
  servers: { name: string; status: string; error: string; tools: string[]; transport: string }[];
};

const PERMISSION_LABEL: Record<string, string> = {
  auto: "Automático", manual: "Manual", edits: "Aceitar edições", plan: "Plano", bypass: "Ignorar permissões",
};

const VIA: Record<string, string> = {
  native: "nativo (campo tools da API)",
  prompt: "texto (schema no system prompt)",
  none: "nenhuma",
};

const MONO = "font-mono";

/** Linha rótulo → valor do cartão Estado (valor em mono, à direita, truncado). */
function Row({ k, children, title }: { k: string; children: React.ReactNode; title?: string }) {
  return (
    <div className="flex gap-2">
      <span className="w-[120px] shrink-0 text-faint">{k}</span>
      <span className={`min-w-0 flex-1 truncate text-right text-fg ${MONO}`} title={title ?? (typeof children === "string" ? children : undefined)}>{children}</span>
    </div>
  );
}

type Ferr = ToolInfo & { blocked?: string[] };

/** Ferramenta como chip: MCP em roxo, bloqueada riscada, "sempre pergunta" em âmbar, escrita com ponto. */
function Chip({ t, label, onClick, aberto }: { t: Ferr; label?: string; onClick?: () => void; aberto?: boolean }) {
  const mcp = t.name.startsWith("mcp__");
  const cor = t.blocked ? "text-faint line-through" : t.always_ask ? "text-warn" : mcp ? "text-agent" : "text-fg-2";
  const selo = t.blocked
    ? `bloqueada · modelo sem ${t.blocked.map((m) => (m === "vision" ? "visão" : m)).join(", ")}`
    : t.always_ask ? "sempre pergunta" : t.mutating ? "escrita" : "";
  const dica = [t.name, selo, t.description].filter(Boolean).join("\n");
  const Tag = onClick ? "button" : "span";
  return (
    <Tag onClick={onClick} title={dica} aria-expanded={onClick ? aberto : undefined}
         className={`inline-flex items-center gap-1 rounded-[5px] bg-line px-1.5 py-0.5 ${cor} ${onClick ? "hover:bg-raised" : ""}`}>
      {t.mutating && !t.blocked && !t.always_ask && <span className="size-1 rounded-full bg-current opacity-60" />}
      {label ?? t.name}
    </Tag>
  );
}

/** Nativas como chips; as de MCP num chip por servidor (mcp__servidor__*) que abre as dele. */
function Tools({ list, info }: { list: { name: string; mutating: boolean; blocked?: string[] }[]; info: Map<string, ToolInfo> }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (!list.length) return <div className="text-muted">Nenhuma ferramenta.</div>;
  const full: Ferr[] = list.map((t) => ({ ...t, ...info.get(t.name), blocked: t.blocked }));
  const builtin = full.filter((t) => !t.name.startsWith("mcp__"));
  const groups = new Map<string, Ferr[]>();
  for (const t of full.filter((t) => t.name.startsWith("mcp__"))) {
    const server = t.name.split("__")[1];
    groups.set(server, [...(groups.get(server) ?? []), t]);
  }
  // Legenda só com os tipos que aparecem nesta lista.
  const tem = {
    leitura: builtin.some((t) => !t.blocked && !t.always_ask && !t.mutating),
    escrita: builtin.some((t) => !t.blocked && !t.always_ask && t.mutating),
    pergunta: full.some((t) => !t.blocked && t.always_ask),
    mcp: groups.size > 0,
    bloqueada: full.some((t) => t.blocked),
  };
  return (
    <>
    <div className={`flex flex-wrap gap-1 text-[10.5px] ${MONO}`}>
      {builtin.map((t) => <Chip key={t.name} t={t} />)}
      {[...groups].map(([server, tools]) => (
        <span key={server} className="contents">
          <Chip t={{ name: `mcp__${server}__*`, mutating: false, description: `${tools.length} ferramenta${tools.length > 1 ? "s" : ""} do servidor ${server}` }}
                label={`mcp__${server}__* (${tools.length})`} aberto={!!open[server]}
                onClick={() => setOpen((o) => ({ ...o, [server]: !o[server] }))} />
          {open[server] && tools.map((t) => <Chip key={t.name} t={t} label={t.name.split("__").slice(2).join("__")} />)}
        </span>
      ))}
    </div>
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-faint">
      {tem.leitura && <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-[2px] bg-fg-2" /> leitura: só consulta</span>}
      {tem.escrita && <span className="inline-flex items-center gap-1.5"><span className="size-1 rounded-full bg-fg-2 opacity-60" /> escrita: altera arquivos ou o sistema</span>}
      {tem.pergunta && <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-[2px] bg-warn" /> sempre pede sua aprovação</span>}
      {tem.mcp && <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-[2px] bg-agent" /> de servidor MCP (clique para abrir)</span>}
      {tem.bloqueada && <span className="inline-flex items-center gap-1.5"><span className="line-through">abc</span> bloqueada: o modelo não tem a capacidade</span>}
    </div>
    </>
  );
}

function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-[7px] rounded-xl border border-line bg-surface px-3 py-2.5 text-xs">
      <div className="flex items-center gap-2">
        <h3 className={`text-[10.5px] font-medium tracking-[.08em] text-faint uppercase ${MONO}`}>{title}</h3>
        {action && <span className="ml-auto">{action}</span>}
      </div>
      {children}
    </section>
  );
}

const DOT: Record<string, string> = {
  connected: "bg-ok",
  connecting: "bg-info animate-pulse",
  error: "bg-err",
  stopped: "bg-faint",
};

/** Contagem no canto do título (mono, discreta). */
const Conta = ({ n }: { n: number }) => <span className={`text-[11px] text-muted ${MONO}`}>{n}</span>;

// Seletor com cara de valor de linha: mono, à direita, sem caixa até o hover.
const SELECT = "min-w-0 cursor-pointer appearance-none rounded-[5px] bg-transparent px-1 text-right font-mono text-fg hover:bg-raised disabled:cursor-default";

export default function InfoPanel(props: {
  settings: Settings;
  toolMode: string;
  onToolMode: (m: string) => void;
  vision: string;
  onVision: (v: string) => void;
  allTools: ToolInfo[];
  sent: ToolsSent | null;
  mcp: McpStatus | null;
  onReloadMcp: () => void;
  usage: { model: string; tokens: number; seconds: number; tps: number | null }[];
  section: Section;
}) {
  const { settings, sent, mcp } = props;
  const agent = props.section === "agent";
  const nextVia = !agent ? "none" : props.toolMode === "text" ? "prompt" : "native";
  const info = new Map(props.allTools.map((t) => [t.name, t]));
  const enabled = props.allTools.filter((t) => t.enabled !== false); // desligadas em Configurações não vão
  // Bloqueadas por capacidade: só sabemos depois de uma requisição com este modelo (o backend detecta).
  const sameModel = sent?.model === settings.model;
  const blockedNow = new Map((sameModel ? sent?.blocked ?? [] : []).map((b) => [b.name, b.missing]));
  const withBlocked = (list: { name: string; mutating: boolean }[]) => list.map((t) => ({ ...t, blocked: blockedNow.get(t.name) }));
  const visionLabel = !sent || !sameModel
    ? props.vision === "yes" ? "sim (forçado)" : props.vision === "no" ? "não (forçado)" : "detecta na próxima requisição"
    : `${sent.capabilities?.includes("vision") ? "sim" : "não"} (${sent.vision_source === "override" ? "forçado" : sent.vision_source})`;

  return (
    <aside className="flex h-full flex-col gap-2.5 overflow-y-auto bg-side p-2.5 text-xs">
      <Section title="Estado">
        <Row k="Seção">{agent ? "Agente" : "Chat"}</Row>
        <Row k="Provider">{settings.provider}</Row>
        <Row k="Modelo" title={settings.model}>{settings.model || "—"}</Row>
        {agent && <Row k="Permissão">{sent?.permission_label ?? PERMISSION_LABEL[settings.permission]}</Row>}
        <Row k="Esforço">
          {settings.effort}
          {sent?.max_iterations ? ` · até ${sent.max_iterations} passos` : ""}
        </Row>
        <Row k="Visão">{visionLabel}</Row>
        {agent && (
          <Row k="Comandos em" title="run_command, serve_start e o Terminal rodam na sua máquina, na pasta da conversa.">
            {sent?.environment ?? "…"}
          </Row>
        )}
        {sent && sameModel && (
          <Row k="Capacidades" title="O que o provider informou sobre o modelo (Ollama /api/show ou LM Studio type)">
            {sent.capabilities_detected === null || sent.capabilities_detected === undefined
              ? "provider não informa"
              : sent.capabilities_detected.length
                ? sent.capabilities_detected.join(", ")
                : "nenhuma"}
          </Row>
        )}
        <label className="flex items-center gap-2" title="Tool calling do modelo">
          <span className="w-[120px] shrink-0 text-faint">Tool calling</span>
          <span className="flex min-w-0 flex-1 justify-end">
            <select value={props.toolMode} onChange={(e) => props.onToolMode(e.target.value)} disabled={!settings.model} className={SELECT}>
              <option value="auto">auto</option>
              <option value="native">native</option>
              <option value="text">text</option>
            </select>
          </span>
        </label>
        <label className="flex items-center gap-2" title="Libera ou bloqueia ferramentas que exigem visão (browser_screenshot)">
          <span className="w-[120px] shrink-0 text-faint">Visão do modelo</span>
          <span className="flex min-w-0 flex-1 justify-end">
            <select value={props.vision} onChange={(e) => props.onVision(e.target.value)} disabled={!settings.model} className={SELECT}>
              <option value="auto">auto (detectar)</option>
              <option value="yes">sim</option>
              <option value="no">não</option>
            </select>
          </span>
        </label>
      </Section>

      <Section title="Enviadas na última requisição" action={sent && <Conta n={sent.tools.length} />}>
        {sent ? (
          <>
            <div className="text-[11px] text-faint">
              {sent.model} · via {VIA[sent.via]}
              {!!sent.blocked?.length && <span className="text-err"> · {sent.blocked.length} bloqueada{sent.blocked.length > 1 ? "s" : ""}</span>}
            </div>
            <Tools list={[...sent.tools, ...(sent.blocked ?? []).map((b) => ({ name: b.name, mutating: false, blocked: b.missing }))]} info={info} />
          </>
        ) : (
          <div className="text-muted">Nenhuma requisição nesta sessão ainda.</div>
        )}
      </Section>

      <Section title="Próxima requisição enviará" action={agent && <Conta n={enabled.length - blockedNow.size} />}>
        <div className="text-[11px] text-faint">via {VIA[nextVia]}</div>
        {agent ? <Tools list={withBlocked(enabled)} info={info} /> : <div className="text-muted">Modo Chat não envia ferramentas. Troque para Agente.</div>}
      </Section>

      {!!props.usage.length && (
        <Section title="Modelos nesta conversa">
          {props.usage.map((u) => (
            <div key={u.model} className="flex items-center gap-2">
              <span className={`min-w-0 flex-1 truncate text-fg ${MONO}`} title={u.model}>{u.model}</span>
              <span className="shrink-0 text-faint">
                {u.tokens.toLocaleString("pt-BR")} tok{u.tps ? ` · ${u.tps.toFixed(1)} t/s` : ""}
              </span>
            </div>
          ))}
        </Section>
      )}

      <Section
        title="Servidores MCP"
        action={
          <button onClick={props.onReloadMcp} className="text-[11.5px] text-muted hover:text-fg">
            recarregar
          </button>
        }
      >
        {mcp?.config_error && <div className="break-words text-err">{mcp.config_error}</div>}
        {mcp && mcp.servers.length ? (
          mcp.servers.map((s) => (
            <div key={s.name} className="flex items-center gap-2" title={`${s.transport} · ${s.status}${s.error ? `\n${s.error}` : ""}`}>
              <span className={`size-1.5 shrink-0 rounded-full ${DOT[s.status] ?? "bg-faint"}`} />
              <span className={`truncate text-fg ${MONO}`}>{s.name}</span>
              <span className={`ml-auto min-w-0 truncate text-right ${s.error ? "text-red-300" : "text-faint"}`}>
                {s.error
                  ? `erro: ${s.error}`
                  : s.status === "connected"
                    ? `${s.tools.length} ferramenta${s.tools.length === 1 ? "" : "s"}`
                    : s.status}
              </span>
            </div>
          ))
        ) : (
          <div className="text-muted">
            Nenhum servidor. Crie <span className={MONO}>config/mcp.json</span> (veja o exemplo) e clique em recarregar.
          </div>
        )}
      </Section>
    </aside>
  );
}

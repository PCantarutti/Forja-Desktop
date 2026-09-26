import { useState } from "react";
import type { Settings, ToolsSent } from "../types";
import type { Section } from "./Controls";
import { Chevron, Wrench } from "./icons";

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

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2 py-0.5">
      <span className="text-faint">{k}</span>
      <span className="truncate text-right text-fg">{children}</span>
    </div>
  );
}

function Badge({ t }: { t: ToolInfo & { blocked?: string[] } }) {
  if (t.blocked)
    return (
      <span className="rounded bg-raised px-1.5 text-[10px] text-red-300" title={`Modelo sem: ${t.blocked.join(", ")}`}>
        bloqueada · sem {t.blocked.map((m) => (m === "vision" ? "visão" : m)).join(", ")}
      </span>
    );
  if (t.always_ask) return <span className="rounded bg-raised px-1.5 text-[10px] text-amber-200">sempre pergunta</span>;
  if (t.mutating) return <span className="rounded bg-raised px-1.5 text-[10px] text-muted">escrita</span>;
  return null;
}

function ToolRow({ t, label }: { t: ToolInfo & { blocked?: string[] }; label?: string }) {
  return (
    <li className="flex items-center justify-between gap-2 font-mono">
      <span className={`flex min-w-0 items-center gap-1.5 ${t.blocked ? "text-faint line-through" : "text-fg"}`} title={t.name}>
        <Wrench className="size-3 shrink-0 text-faint" /> <span className="truncate">{label ?? t.name}</span>
      </span>
      <Badge t={t} />
    </li>
  );
}

/** Ferramentas nativas listadas uma a uma; as de MCP agrupadas por servidor (recolhíveis). */
function Tools({ list, info }: { list: { name: string; mutating: boolean; blocked?: string[] }[]; info: Map<string, ToolInfo> }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (!list.length) return <div className="text-muted">Nenhuma ferramenta.</div>;
  const full = list.map((t) => ({ ...t, ...info.get(t.name), blocked: t.blocked }));
  const builtin = full.filter((t) => !t.name.startsWith("mcp__"));
  const groups = new Map<string, ToolInfo[]>();
  for (const t of full.filter((t) => t.name.startsWith("mcp__"))) {
    const server = t.name.split("__")[1];
    groups.set(server, [...(groups.get(server) ?? []), t]);
  }
  return (
    <ul className="space-y-1">
      {builtin.map((t) => (
        <ToolRow key={t.name} t={t} />
      ))}
      {[...groups].map(([server, tools]) => (
        <li key={server}>
          <button
            onClick={() => setOpen((o) => ({ ...o, [server]: !o[server] }))}
            className="flex w-full items-center justify-between font-mono text-fg hover:text-white"
          >
            <span>
              <span className="text-faint">mcp · </span>
              {server} <span className="text-faint">({tools.length})</span>
            </span>
            <Chevron className="size-3 text-faint" />
          </button>
          {open[server] && (
            <ul className="mt-1 space-y-1 border-l border-line pl-2.5">
              {tools.map((t) => (
                <ToolRow key={t.name} t={t} label={t.name.split("__").slice(2).join("__")} />
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-3.5">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-[11px] font-medium tracking-wider text-faint uppercase">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

const DOT: Record<string, string> = {
  connected: "text-emerald-400",
  connecting: "text-sky-400 animate-pulse",
  error: "text-red-400",
  stopped: "text-faint",
};

const SELECT = "rounded-md border border-line bg-raised px-1.5 py-0.5 text-fg";

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
    <aside className="flex h-full flex-col gap-3 overflow-y-auto bg-bg p-3 text-xs">
      <Section title="Estado">
        <Row k="Seção">{agent ? "Agente" : "Chat"}</Row>
        <Row k="Provider">{settings.provider}</Row>
        <Row k="Modelo">
          <span title={settings.model}>{settings.model || "—"}</span>
        </Row>
        {agent && <Row k="Permissão">{sent?.permission_label ?? PERMISSION_LABEL[settings.permission]}</Row>}
        <Row k="Esforço">
          {settings.effort}
          {sent?.max_iterations ? ` · até ${sent.max_iterations} passos` : ""}
        </Row>
        <Row k="Visão">{visionLabel}</Row>
        {agent && (
          <Row k="Comandos em">
            <span title="run_command, serve_start e o Terminal rodam na sua máquina, na pasta da conversa.">
              {sent?.environment ?? "…"}
            </span>
          </Row>
        )}
        {sent && sameModel && (
          <Row k="Capacidades">
            <span title="O que o provider informou sobre o modelo (Ollama /api/show ou LM Studio type)">
              {sent.capabilities_detected === null || sent.capabilities_detected === undefined
                ? "provider não informa"
                : sent.capabilities_detected.length
                  ? sent.capabilities_detected.join(", ")
                  : "nenhuma"}
            </span>
          </Row>
        )}
        <label className="mt-2 flex items-center justify-between gap-2">
          <span className="text-faint">Tool calling do modelo</span>
          <select value={props.toolMode} onChange={(e) => props.onToolMode(e.target.value)} disabled={!settings.model} className={SELECT}>
            <option value="auto">auto</option>
            <option value="native">native</option>
            <option value="text">text</option>
          </select>
        </label>
        <label className="mt-1.5 flex items-center justify-between gap-2" title="Libera ou bloqueia ferramentas que exigem visão (browser_screenshot)">
          <span className="text-faint">Visão do modelo</span>
          <select value={props.vision} onChange={(e) => props.onVision(e.target.value)} disabled={!settings.model} className={SELECT}>
            <option value="auto">auto (detectar)</option>
            <option value="yes">sim</option>
            <option value="no">não</option>
          </select>
        </label>
      </Section>

      <Section title="Enviadas na última requisição">
        {sent ? (
          <>
            <div className="mb-2 text-muted">
              {sent.model} · via {VIA[sent.via]} · {sent.tools.length} ferramentas
              {!!sent.blocked?.length && ` · ${sent.blocked.length} bloqueada${sent.blocked.length > 1 ? "s" : ""}`}
            </div>
            <Tools list={[...sent.tools, ...(sent.blocked ?? []).map((b) => ({ name: b.name, mutating: false, blocked: b.missing }))]} info={info} />
          </>
        ) : (
          <div className="text-muted">Nenhuma requisição nesta sessão ainda.</div>
        )}
      </Section>

      <Section title="Próxima requisição enviará">
        <div className="mb-2 text-muted">
          via {VIA[nextVia]}
          {agent && ` · ${enabled.length - blockedNow.size} ferramentas`}
        </div>
        <Tools list={agent ? withBlocked(enabled) : []} info={info} />
        {!agent && <div className="mt-1 text-muted">Modo Chat não envia ferramentas. Troque para Agente.</div>}
      </Section>

      {!!props.usage.length && (
        <Section title="Modelos nesta conversa">
          <ul className="space-y-1">
            {props.usage.map((u) => (
              <li key={u.model} className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-fg" title={u.model}>
                  {u.model}
                </span>
                <span className="shrink-0 text-faint">
                  {u.tokens.toLocaleString("pt-BR")} tok{u.tps ? ` · ${u.tps.toFixed(1)} t/s` : ""}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section
        title="Servidores MCP"
        action={
          <button onClick={props.onReloadMcp} className="rounded-md px-1.5 py-0.5 text-muted hover:bg-raised hover:text-fg">
            recarregar
          </button>
        }
      >
        {mcp?.config_error && <div className="mb-2 text-red-300">{mcp.config_error}</div>}
        {mcp && mcp.servers.length ? (
          <ul className="space-y-1.5">
            {mcp.servers.map((s) => (
              <li key={s.name}>
                <div className="flex items-center justify-between">
                  <span className="text-fg">
                    <span className={DOT[s.status] ?? "text-faint"}>●</span> {s.name}
                  </span>
                  <span className="text-faint">
                    {s.transport} · {s.status === "connected" ? `${s.tools.length} tools` : s.status}
                  </span>
                </div>
                {s.error && <div className="mt-0.5 break-words text-red-300">{s.error}</div>}
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-muted">
            Nenhum servidor. Crie <span className="font-mono">config/mcp.json</span> (veja o exemplo) e clique em
            recarregar.
          </div>
        )}
      </Section>
    </aside>
  );
}

import { useEffect, useState } from "react";
import { UsageBars, useCloudUsage } from "./CloudUsage";
import { api } from "../api";
import type { Especialidade } from "../types";
import Confirma from "./Confirma";
import { Modal } from "./Modal";
import type { McpStatus, ToolInfo } from "./InfoPanel";
import { Shield, Trash, Wrench, X } from "./icons";
import { FONTES, TEMAS, lerAparencia, salvarAparencia } from "../aparencia";
import ModelPicker from "./ModelPicker";
import qrcode from "qrcode-generator";

export type Provider = {
  id: string;
  name: string;
  type: "ollama" | "lmstudio" | "openai";
  url: string;
  api_key?: string; // só enviado; nunca volta do backend
  has_api_key?: boolean;
  api_key_hint?: string;
  context_window?: number | null; // tokens; obrigatória no tipo openai quando o servidor não informa
};

export type AppSettings = {
  providers: Provider[];
  capacidades?: Record<string, { nao: string[]; parcial: string[] }>; // só leitura, vem do backend
  num_ctx: number;
  max_iterations: number;
  max_file_bytes: number;
  shell_timeout_max: number;
  sandbox_memoria_mb: number;
  sandbox_processos: number;
  sandbox_cpu: number;
  sandbox_isolado: string;
  sandbox_motor: string;
  sandbox_wsl_distro: string;
  compact_at: number;
  searxng_url: string;
  disabled_tools: string[];
  custom_instructions: string;
  auto_approve_tools: string[];
  auto_approve_commands: string[];
  trusted_hooks: string[];
  personal_memory: boolean;
  project_memory: boolean;
  project_memory_file: string;
  enabled_models: Record<string, string[] | undefined>;
  subagents: Record<"rapido" | "capaz" | "nuvem", { provider: string; model: string }>;
  subagent_max_iterations: number;
  browser_idle_minutes: number;
  browser_scale: number;
  browser_stream: "png" | "jpeg";
  maestro_max_iterations: number;
  maestro_max_attempts: number;
  max_workers: number;
  model_lifecycle: string;
  maestro_model: { provider: string; model: string };
  maestro_browser: boolean;
  auto_review: boolean;
  maestro_visual: { provider: string; model: string };
  worker_especialidades: Especialidade[];
};

type Entity = { name: string; entityType?: string; observations?: string[] };
type Memory = {
  available: boolean;
  reason?: string;
  server?: string;
  can_delete?: boolean;
  entities: Entity[];
  relations: { from: string; to: string; relationType?: string }[];
  raw?: string;
};

const BASE_TABS = ["Aplicativo", "Geral", "Pastas", "Runtime", "Hardware", "Provedores", "Subagentes", "Maestro", "Ferramentas", "Skills", "Permissões", "MCP", "Memória", "Celular"] as const;
type Tab = (typeof BASE_TABS)[number];
// Navegação agrupada do redesign. "Aplicativo" sempre existe (tema e fonte); janela, bandeja e início
// com o Windows só aparecem dentro do Electron.
const GRUPOS: { titulo: string; tabs: Tab[] }[] = [
  { titulo: "App", tabs: ["Aplicativo", "Geral", "Pastas"] },
  { titulo: "Máquina", tabs: ["Runtime", "Hardware"] },
  { titulo: "Modelos", tabs: ["Provedores", "Subagentes", "Maestro"] },
  { titulo: "Agente", tabs: ["Ferramentas", "Skills", "Permissões", "MCP", "Memória"] },
  { titulo: "Integrações", tabs: ["Celular"] },
];

const input = "w-full rounded-[9px] border border-line bg-surface px-3 py-1.5 text-[13px] text-fg focus:border-focus focus:outline-none";
const btn = "rounded-[9px] border border-line px-3 py-1.5 text-[12.5px] text-fg hover:bg-raised";
const btnPrimary = "rounded-[9px] bg-accent px-4 py-1.5 text-[12.5px] font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";

function Field({ label, hint, children, div }: { label: string; hint?: string; children: React.ReactNode; div?: boolean }) {
  // `div`: o controle são botões; num <label>, clicar no texto acionaria o primeiro deles.
  const Tag = div ? "div" : "label";
  return (
    <Tag className="grid grid-cols-[280px_minmax(0,1fr)] items-start gap-6 border-b border-line py-3.5">
      <span>
        <span className="block text-[13.5px] text-fg">{label}</span>
        {hint && <span className="mt-0.5 block text-xs leading-snug text-faint">{hint}</span>}
      </span>
      <div className="min-w-0">{children}</div>
    </Tag>
  );
}

// Como instalar o Docker que o sandbox isolado usa. Dois caminhos: o Docker Desktop (mais simples, mas
// precisa ficar aberto e come RAM) ou o Docker Engine dentro do WSL (sem janela, o WSL sobe sozinho).
function TutorialDocker() {
  const cmd = (texto: string) => (
    <code className="mt-1 block whitespace-pre-wrap break-all rounded-lg bg-raised px-3 py-2 font-mono text-xs text-fg">{texto}</code>
  );
  return (
    <details className="rounded-lg border border-line px-3 py-2 text-xs text-muted">
      <summary className="cursor-pointer text-sm text-fg">Como instalar o Docker para o sandbox isolado</summary>
      <div className="mt-3 space-y-4">
        <div>
          <p className="text-fg">Opção 1 — Docker Engine no WSL (recomendado: sem janela aberta, mais leve)</p>
          <ol className="mt-1 list-decimal space-y-1.5 pl-5">
            <li>Tenha o WSL 2 com uma distro Linux (ex.: Ubuntu). No PowerShell, se ainda não tiver:{cmd("wsl --install -d Ubuntu")}</li>
            <li>Se o Docker Desktop estiver instalado, desligue a integração com essa distro em Settings › Resources › WSL integration, e apague os atalhos que ela deixou (dentro do Ubuntu):{cmd("sudo find /usr/bin /usr/local/bin /usr/local/lib/docker/cli-plugins -maxdepth 1 -lname '/mnt/wsl/docker-desktop/*' -print -delete")}</li>
            <li>Instale e ligue o Docker Engine (dentro do Ubuntu):{cmd("sudo apt update && sudo apt install -y docker.io")}{cmd("sudo systemctl enable --now docker")}{cmd("sudo usermod -aG docker $USER")}</li>
            <li>Para o Docker subir junto com o WSL, o systemd precisa estar ligado em /etc/wsl.conf (em [boot], systemd=true); depois rode no PowerShell:{cmd("wsl --shutdown")}</li>
            <li>Confira (dentro do Ubuntu):{cmd("docker info --format '{{.ServerVersion}}'")}</li>
            <li>Aqui no Forja: "Sandbox isolado: qual Docker" em Automático ou Docker Engine no WSL, e a distro (vazio = a padrão).</li>
          </ol>
        </div>
        <div>
          <p className="text-fg">Opção 2 — Docker Desktop</p>
          <ol className="mt-1 list-decimal space-y-1.5 pl-5">
            <li>Instale o Docker Desktop (docker.com/products/docker-desktop) com o motor WSL 2.</li>
            <li>Deixe-o aberto enquanto o agente trabalha: o Forja não o abre sozinho. Em Settings › General dá para abrir junto com o Windows, e o Resource Saver reduz a RAM quando ocioso.</li>
            <li>Confira no PowerShell:{cmd("docker info --format '{{.ServerVersion}}'")}</li>
          </ol>
        </div>
        <p>
          Na primeira vez, o Forja baixa a imagem do sandbox (node:22-bookworm ou python:3.12-bookworm, ~400 MB) em
          segundo plano; até terminar, os comandos rodam no Windows. Comandos no container são mais lentos em
          arquivos (a pasta do projeto é lida através do WSL), e o node_modules que um npm install criar lá é de Linux.
        </p>
      </div>
    </details>
  );
}

function Num({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  return <input type="number" className={input} value={value} onChange={(e) => onChange(Number(e.target.value))} />;
}

export default function Settings(props: {
  onClose: () => void;
  tools: ToolInfo[];
  mcp: McpStatus | null;
  onChanged: () => void; // recarrega ferramentas/MCP no app
}) {
  const [tab, setTab] = useState<Tab>("Geral");
  const [s, setS] = useState<AppSettings | null>(null);
  const [dirty, setDirty] = useState<Partial<AppSettings>>({});
  const [descartar, setDescartar] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<AppSettings>("/settings").then(setS).catch((e) => setError(e.message));
  }, []);

  const set = <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => {
    setS((cur) => cur && { ...cur, [k]: v });
    setDirty((d) => ({ ...d, [k]: v }));
    setSaved("");
  };

  async function save(patch?: Partial<AppSettings>) {
    const body = patch ?? dirty;
    if (!Object.keys(body).length) return;
    setBusy(true);
    setError("");
    try {
      setS(await api.put<AppSettings>("/settings", body));
      setDirty({});
      setDescartar(false);
      setSaved("Salvo.");
      props.onChanged();
    } catch (e: any) {
      setError(e.message);
    }
    setBusy(false);
  }

  async function resetAll() {
    setS(await api.post<AppSettings>("/settings/reset", {}));
    setDirty({});
    props.onChanged();
  }

  return (
    <Modal
      onClose={props.onClose}
      // Clicar fora com campo mexido apagava a edição sem perguntar — um erro de mira custava
      // um mcp.json ou uma instrução personalizada inteira.
      canClose={() => {
        if (!Object.keys(dirty).length) return true;
        setDescartar(true);
        return false;
      }}
      label="Configurações"
      className="flex h-[min(760px,90vh)] w-full max-w-[1080px] overflow-hidden rounded-[18px] border border-line-strong bg-bg shadow-dialog"
    >
        <nav className="flex w-[230px] shrink-0 flex-col overflow-y-auto border-r border-line bg-side p-3">
          <div className="mb-1 px-2.5 pt-1 text-[15px] font-semibold text-fg">Configurações</div>
          {GRUPOS.map((g) => (
            <div key={g.titulo} className="mt-3">
              <div className="px-2.5 pb-1 font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">{g.titulo}</div>
              {g.tabs.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`relative flex w-full rounded-[9px] px-2.5 py-1.5 text-left text-[13px] ${tab === t ? "bg-raised text-fg" : "text-muted hover:bg-surface hover:text-fg"}`}
                >
                  {tab === t && <span className="absolute top-1.5 bottom-1.5 -left-3 w-[3px] rounded-r-[3px] bg-accent" />}
                  {t}
                </button>
              ))}
            </div>
          ))}
          <div className="mt-auto px-2.5 pt-4 pb-1">
            <Confirma
              rotulo="Restaurar padrões"
              pergunta="Voltar tudo ao .env?"
              className="text-left text-xs text-err hover:text-fg"
              onSim={() => void resetAll()}
            />
          </div>
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-3 border-b border-line px-6 py-3.5">
            <h2 className="flex-1 text-[17px] font-semibold text-fg">{tab}</h2>
            {error && <span className="truncate text-sm text-err">{error}</span>}
            {descartar && (
              <span className="inline-flex items-center gap-1.5 text-xs">
                <span className="text-amber-300">Descartar as alterações não salvas?</span>
                <button className={btn} onClick={props.onClose}>Descartar</button>
                <button className="px-2 text-muted hover:text-fg" onClick={() => setDescartar(false)}>Continuar editando</button>
              </span>
            )}
            {saved && <span className="text-sm text-ok">{saved}</span>}
            <button onClick={props.onClose} title="Fechar (Esc)" aria-label="Fechar" className="rounded-[7px] p-1.5 text-faint hover:bg-raised hover:text-fg">
              <X className="size-4" />
            </button>
          </header>

          <div className="flex-1 overflow-y-auto px-6 py-2">
            {tab === "Aplicativo" ? (
              <AppTab />
            ) : tab === "Skills" ? (
              <SkillsTab onError={setError} />
            ) : tab === "Pastas" ? (
              <PastasTab onError={setError} onChanged={props.onChanged} />
            ) : tab === "Runtime" ? (
              <RuntimeTab onError={setError} />
            ) : tab === "Hardware" ? (
              <HardwareTab onError={setError} />
            ) : tab === "Celular" ? (
              <CelularTab onError={setError} />
            ) : !s ? (
              <div className="text-muted">Carregando…</div>
            ) : tab === "Geral" ? (
              <div className="max-w-3xl">
                <Field label="Instruções personalizadas" hint="Vão no fim do system prompt, em todas as conversas.">
                  <textarea
                    rows={5}
                    className={input}
                    value={s.custom_instructions}
                    placeholder="Ex.: responda em pt-BR, prefira funções pequenas, use pytest."
                    onChange={(e) => set("custom_instructions", e.target.value)}
                  />
                </Field>
                <Field label="num_ctx (Ollama)" hint="Janela enviada ao Ollama. No LM Studio, a janela é a do modelo carregado.">
                  <Num value={s.num_ctx} onChange={(v) => set("num_ctx", v)} />
                </Field>
                <Field label="Máximo de iterações por mensagem" hint="Quantos passos o agente pode dar antes de parar sozinho.">
                  <Num value={s.max_iterations} onChange={(v) => set("max_iterations", v)} />
                </Field>
                <Field label="Compactar contexto em" hint="Fração da janela (0.3 a 0.95) que dispara o resumo automático.">
                  <input
                    type="number"
                    step="0.05"
                    className={input}
                    value={s.compact_at}
                    onChange={(e) => set("compact_at", Number(e.target.value))}
                  />
                </Field>
                <Field label="Tamanho máximo de arquivo (bytes)">
                  <Num value={s.max_file_bytes} onChange={(v) => set("max_file_bytes", v)} />
                </Field>
                <Field label="Timeout máximo do run_command (s)">
                  <Num value={s.shell_timeout_max} onChange={(v) => set("shell_timeout_max", v)} />
                </Field>
                <Field label="Sandbox isolado (Docker)" hint="Roda os comandos do agente num container com só a pasta do projeto, sem root e sem rede fora da instalação de pacotes. Servidores de dev (serve_start) e o terminal do agente vão junto, com a porta publicada no localhost do Windows; o Forja continua no Windows. Precisa de um Docker rodando (o Forja não o abre), e ele consome RAM: em PC com pouca memória rodando IA local, deixe desligado.">
                  <select className={input} value={s.sandbox_isolado} onChange={(e) => set("sandbox_isolado", e.target.value)}>
                    <option value="desligado">Desligado</option>
                    <option value="autonomo">Só nos modos autônomos (Automático, Ignorar permissões, Maestro)</option>
                    <option value="sempre">Sempre</option>
                  </select>
                </Field>
                <Field label="Sandbox isolado: qual Docker" hint="Automático usa o Docker Desktop se ele estiver aberto e, se não, o Docker Engine instalado dentro do WSL (sem Docker Desktop, e o WSL sobe sozinho quando o Forja chama).">
                  <select className={input} value={s.sandbox_motor} onChange={(e) => set("sandbox_motor", e.target.value)}>
                    <option value="auto">Automático</option>
                    <option value="desktop">Docker Desktop</option>
                    <option value="wsl">Docker Engine no WSL</option>
                  </select>
                </Field>
                <Field label="Sandbox isolado: distro do WSL" hint="Onde o Docker Engine está instalado (ex.: Ubuntu). Vazio = a distro padrão do WSL.">
                  <input className={input} value={s.sandbox_wsl_distro} onChange={(e) => set("sandbox_wsl_distro", e.target.value)} />
                </Field>
                <TutorialDocker />
                <Field label="Sandbox: memória por comando (MB)" hint="Teto de memória da árvore de um comando do agente (run_command, servidores, terminal). -1 = automático (metade da RAM, até 4 GB); 0 = sem limite.">
                  <Num value={s.sandbox_memoria_mb} onChange={(v) => set("sandbox_memoria_mb", v)} />
                </Field>
                <Field label="Sandbox: processos por comando" hint="Processos vivos ao mesmo tempo na árvore de um comando: barra fork bomb. 0 = sem limite.">
                  <Num value={s.sandbox_processos} onChange={(v) => set("sandbox_processos", v)} />
                </Field>
                <Field label="Sandbox: CPU por comando (%)" hint="Teto de CPU de um comando, para o PC continuar usável num build pesado. 0 = sem limite.">
                  <Num value={s.sandbox_cpu} onChange={(v) => set("sandbox_cpu", v)} />
                </Field>
                <Field label="URL do SearXNG" hint="Instância própria de busca. Vazio = DuckDuckGo, sem chave e sem conta.">
                  <input className={input} value={s.searxng_url} onChange={(e) => set("searxng_url", e.target.value)} />
                </Field>
                <Field label="Navegador: fechar sessão ociosa após (min)" hint="0 = nunca. Sessões com o painel aberto não contam como ociosas.">
                  <Num value={s.browser_idle_minutes} onChange={(v) => set("browser_idle_minutes", v)} />
                </Field>
                <Field label="Navegador: escala de renderização (1 a 3)" hint="2 = nítido em tela HiDPI; 3 se o Windows estiver acima de 200%. Vale quando o Chromium (re)inicia: sem sessões abertas, troca em até 1 min.">
                  <Num value={s.browser_scale} onChange={(v) => set("browser_scale", v)} />
                </Field>
                <Field label="Navegador: formato do espelho" hint="PNG é sem perda; JPEG pesa menos em páginas com vídeo. Vale na próxima vez que a aba Navegador abrir.">
                  <select className={input} value={s.browser_stream} onChange={(e) => set("browser_stream", e.target.value as "png" | "jpeg")}>
                    <option value="png">png (qualidade máxima)</option>
                    <option value="jpeg">jpeg (mais leve)</option>
                  </select>
                </Field>
              </div>
            ) : tab === "Provedores" ? (
              <Providers s={s} set={set} />
            ) : tab === "Subagentes" ? (
              <Subagents s={s} set={set} />
            ) : tab === "Maestro" ? (
              <MaestroTab s={s} set={set} />
            ) : tab === "Ferramentas" ? (
              <Tools tools={props.tools} disabled={s.disabled_tools} onToggle={(d) => save({ disabled_tools: d })} />
            ) : tab === "Permissões" ? (
              <Permissions s={s} save={save} />
            ) : tab === "MCP" ? (
              <Mcp mcp={props.mcp} onChanged={props.onChanged} />
            ) : (
              <>
                <PersonalMemory s={s} set={set} save={save} />
                <ProjectMemory s={s} set={set} save={save} />
                <MemoryTab />
              </>
            )}
          </div>
          <footer className="flex items-center gap-2 border-t border-line px-6 py-3">
            <span className="flex-1 text-xs text-faint">Vale na próxima requisição, sem reiniciar.</span>
            <button onClick={props.onClose} className={btn}>
              {Object.keys(dirty).length ? "Cancelar" : "Fechar"}
            </button>
            {!["MCP", "Memória", "Aplicativo", "Pastas", "Runtime", "Hardware", "Skills"].includes(tab) && (
              <button className={btnPrimary} disabled={busy || !Object.keys(dirty).length} onClick={() => save()}>
                Salvar
              </button>
            )}
          </footer>
        </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ celular (app Forja Mobile)

/** QR que o app do celular lê para parear: endereço na tailnet e/ou na rede local + token estável (backend/app/mobile.py).
 *  Com os dois, o app tenta a rede local primeiro e cai para a tailnet fora de casa. */
function CelularTab(props: { onError: (e: string) => void }) {
  type Info = { token: string; url: string | null; lan: string | null; lan_ligado: boolean; devices: number };
  const [m, setM] = useState<Info | null>(null);
  const [mudando, setMudando] = useState(false);
  useEffect(() => {
    api.get<Info>("/mobile").then(setM).catch((e) => props.onError(e.message));
  }, []);
  const lan = (ligado: boolean) => {
    setMudando(true);
    api.post<Info>("/mobile/lan", { ligado }).then(setM).catch((e) => props.onError(e.message)).finally(() => setMudando(false));
  };
  if (!m) return <div className="text-muted">Carregando…</div>;
  const porta = location.port || "80";
  const qr = qrcode(0, "M");
  qr.addData(JSON.stringify({ url: m.url, lan: m.lan, token: m.token }));
  qr.make();
  return (
    <div className="space-y-4 text-sm">
      <Toggle checked={m.lan_ligado} disabled={mudando} onChange={lan} label="Rede local (Wi‑Fi de casa)"
              hint={`O celular fala direto com o PC pela porta ${m.lan ? m.lan.split(":").pop() : "47811"}, sem ligar a VPN. Fora de casa ele usa o Tailscale. Na primeira vez o Windows pergunta se libera o acesso: permita só em redes privadas.`} />
      {m.lan_ligado && !m.lan && <p className="text-muted">Sem endereço na rede local agora (sem Wi‑Fi/cabo, ou a porta está em uso).</p>}
      {m.url ? (
        <>
          <p className="text-muted">Para usar fora de casa, publique o Forja na sua tailnet (uma vez, no PowerShell):</p>
          <code className="block rounded-lg bg-raised px-3 py-2 text-xs text-fg">tailscale serve --bg --https=443 http://127.0.0.1:{porta}</code>
        </>
      ) : (
        <p className="text-muted">Tailscale não encontrado neste PC: {m.lan ? "o celular só conecta pela rede local." : "ligue a rede local acima ou instale o Tailscale (grátis) aqui e no celular."}</p>
      )}
      {(m.url || m.lan) && (
        <>
          <p className="text-muted">Leia com o app Forja Mobile:</p>
          <div className="inline-block rounded-xl bg-white p-3" dangerouslySetInnerHTML={{ __html: qr.createSvgTag({ cellSize: 5, margin: 0 }) }} />
          <p className="text-muted">{[m.lan, m.url].filter(Boolean).join(" · ")} · {m.devices} aparelho(s) com notificação</p>
          {/* Sem câmera: o mesmo conteúdo do QR num link forja:// (mande para você mesmo e toque no celular). */}
          <button className={btn} onClick={() => navigator.clipboard.writeText(
            `forja://parear?c=${encodeURIComponent(JSON.stringify({ url: m.url, lan: m.lan, token: m.token }))}`)}>
            Copiar link de pareamento
          </button>
        </>
      )}
      <Confirma
        className={btn}
        rotulo="Revogar e gerar novo QR"
        pergunta="O celular pareado perde o acesso. Continuar?"
        onSim={() => api.post<Info>("/mobile/rotate").then(setM).catch((e) => props.onError(e.message))}
      />
    </div>
  );
}

// ------------------------------------------------------------------ aplicativo (Electron)

function Toggle({ checked, onChange, label, hint, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; disabled?: boolean }) {
  return (
    <label className={`flex items-start gap-3 rounded-xl border border-line bg-surface p-3 ${disabled ? "opacity-50" : "cursor-pointer hover:border-focus"}`}>
      <input type="checkbox" className="mt-0.5 size-4 accent-[var(--accent)]" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="min-w-0">
        <span className="block text-sm text-fg">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-muted">{hint}</span>}
      </span>
    </label>
  );
}

/** Motores do llama.cpp e do sd.cpp: qual está instalado, qual está em uso, e baixar/atualizar. */
function RuntimeTab(props: { onError: (e: string) => void }) {
  const [st, setSt] = useState<any>(null);
  const recarrega = () => api.get("/local").then(setSt).catch((e) => props.onError(e.message));

  useEffect(() => {
    recarrega();
    const t = setInterval(recarrega, 3000); // enquanto baixa, a lista muda sozinha
    return () => clearInterval(t);
  }, []);

  if (!st) return <div className="text-muted">Carregando…</div>;

  const acao = (fn: Promise<unknown>) => fn.then(recarrega).catch((e: any) => props.onError(e.message));

  const bloco = (kind: "llama" | "sd" | "ffmpeg" | "comfy", titulo: string, descricao: string) => {
    const r = st.runtimes[kind];
    return (
      <Field key={kind} label={titulo} hint={descricao}>
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted">Em uso</span>
            <select
              className={input}
              value={r.chosen || r.backend || ""}
              onChange={(e) => acao(api.put("/local/runtime", { kind, backend: e.target.value }))}
              disabled={!r.available.length}
            >
              {!r.available.length && <option value="">nenhum instalado</option>}
              {r.available.map((a: any) => (
                <option key={a.backend} value={a.backend}>
                  {a.backend}
                  {a.version ? ` · ${a.version}` : ""}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap gap-2">
            {r.backends.map((b: string) => {
              const tem = r.available.some((a: any) => a.backend === b);
              if (kind === "comfy" && tem) return null; // versão fixa: não há o que atualizar
              return (
                <button
                  key={b}
                  className={btn}
                  onClick={() => acao(api.post("/local/runtime", { kind, backend: b }))}
                  title={kind === "comfy" ? `Pacote portátil oficial para a sua GPU (${b}), com Python e PyTorch: ~${((r.mb ?? 0) / 1000).toFixed(1).replace(".", ",")} GB.` : kind === "ffmpeg" ? "Build LGPL do BtbN (~80 MB): lê e grava o vídeo; o ESRGAN roda no sd.cpp, na GPU." : b === "cuda" ? "NVIDIA. Baixa também o runtime da NVIDIA (~370 MB)." : b === "vulkan" ? "Qualquer GPU: NVIDIA, AMD e Intel." : "Sem GPU: roda na CPU."}
                >
                  {kind === "comfy" ? `Baixar (~${((r.mb ?? 0) / 1000).toFixed(1).replace(".", ",")} GB)` : kind === "ffmpeg" ? (tem ? "Atualizar" : "Baixar") : tem ? `Atualizar ${b}` : `Baixar ${b}`}
                </button>
              );
            })}
          </div>
        </div>
      </Field>
    );
  };

  return (
    <div className="max-w-3xl">
      {bloco("llama", "Motor de chat (llama.cpp)", "CPU, Vulkan e CUDA convivem no disco: dá para trocar a qualquer momento, sem baixar de novo.")}
      {bloco("sd", "Motor de imagem e vídeo (stable-diffusion.cpp)", "Mesma ideia, para gerar imagem e vídeo (e o ESRGAN da ampliação).")}
      {bloco("ffmpeg", "Motor de ampliação de vídeo (ffmpeg)", "Separa os quadros, junta de volta com o áudio e interpola o movimento. Só existe o build de CPU: o pesado (ESRGAN) é na GPU pelo sd.cpp.")}
      {bloco("comfy", "Motor de ampliação por IA pesada (ComfyUI)", "SeedVR2, DAT/HAT/SwinIR e o Redesenhar com um checkpoint SD 1.5/SDXL. O pacote é o da marca da sua GPU, numa versão fixa testada; roda só enquanto amplia e libera a VRAM no fim.")}
      {!!st.jobs?.filter((j: any) => j.kind === "runtime").length && (
        <div className="space-y-1 text-xs text-muted">
          {st.jobs
            .filter((j: any) => j.kind === "runtime")
            .map((j: any) => (
              <p key={j.id}>
                {j.name}: {j.status === "running" ? `${j.total ? Math.round((j.done / j.total) * 100) : 0}%` : j.status}
                {j.error ? ` — ${j.error}` : ""}
              </p>
            ))}
        </div>
      )}
      <p className="text-xs text-faint">
        O motor em uso vale para o próximo carregamento. Trocar de Vulkan para CUDA (ou para CPU) não mexe nos modelos
        baixados.
      </p>
    </div>
  );
}

/** CPU, memória, GPUs e as proteções de carregamento. */
function HardwareTab(props: { onError: (e: string) => void }) {
  const [st, setSt] = useState<any>(null);
  const recarrega = () => api.get("/local").then(setSt).catch((e) => props.onError(e.message));

  useEffect(() => {
    recarrega();
    const t = setInterval(recarrega, 5000); // VRAM livre muda enquanto se usa o computador
    return () => clearInterval(t);
  }, []);

  if (!st) return <div className="text-muted">Carregando…</div>;
  const hw = st.hardware;
  const gb = (n: number) => `${(n / 2 ** 30).toFixed(2)} GB`;
  const acao = (fn: Promise<unknown>) => fn.then(recarrega).catch((e: any) => props.onError(e.message));

  const NIVEIS: [string, string, string][] = [
    ["off", "Desligado", "Nenhuma precaução: carrega o que você mandar."],
    ["relaxado", "Relaxado", "Recusa só o que não cabe nem somando RAM e VRAM."],
    ["rigoroso", "Rigoroso", "Recusa também o que não couber na VRAM livre."],
  ];

  return (
    <div className="max-w-3xl">
      <Field label="Processador">
        <div className="rounded-lg border border-line bg-raised px-3 py-2 text-sm">
          <p className="text-fg">{hw.cpu.name || "desconhecido"}</p>
          <p className="text-xs text-muted">
            {hw.cpu.arch} · {hw.cpu.cores} threads
          </p>
        </div>
      </Field>

      <Field label="Memória">
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div className="rounded-lg border border-line bg-raised px-3 py-2">
            <p className="text-xs text-muted">RAM</p>
            <p className="text-fg">{gb(hw.ram)}</p>
            <p className="text-xs text-faint">{gb(hw.ram_free)} livres</p>
          </div>
          <div className="rounded-lg border border-line bg-raised px-3 py-2">
            <p className="text-xs text-muted">VRAM</p>
            <p className="text-fg">{gb(hw.vram)}</p>
            <p className="text-xs text-faint">{gb(hw.vram_free)} livres</p>
          </div>
        </div>
      </Field>

      <Field label="GPUs" hint="Desligar uma GPU tira ela do próximo carregamento (--device do llama.cpp).">
        <div className="space-y-2">
          {!hw.gpus.length && <p className="text-sm text-muted">Nenhuma GPU detectada pelo motor atual.</p>}
          {hw.gpus.map((g: any) => (
            <div key={g.id} className="flex items-center gap-3 rounded-lg border border-line bg-raised px-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-fg">{g.name}</p>
                <p className="text-xs text-muted">
                  {gb(g.total)} · {gb(g.free)} livres · {g.id}
                </p>
              </div>
              <button
                className={`rounded-[9px] px-3 py-1 text-xs ${g.enabled ? "bg-sky-600 text-white" : "border border-line text-muted"}`}
                onClick={() => acao(api.put("/local/device", { id: g.id, enabled: !g.enabled }))}
              >
                {g.enabled ? "ON" : "OFF"}
              </button>
            </div>
          ))}
        </div>
      </Field>

      <Field label="Cache KV na GPU" hint="Padrão para todo modelo. Desligado, o cache vai para a RAM: libera VRAM e custa velocidade.">
        <button
          role="switch"
          aria-checked={!st.defaults_no_kv_offload}
          onClick={() => acao(api.put("/local/prefs", { defaults: { no_kv_offload: !stDefault(st, "no_kv_offload") } }))}
          className={`h-5 w-10 rounded-full transition-colors ${stDefault(st, "no_kv_offload") ? "bg-raised" : "bg-sky-500"}`}
        >
          <span
            className={`block size-4 rounded-full bg-white transition-transform ${stDefault(st, "no_kv_offload") ? "translate-x-0.5" : "translate-x-5"}`}
          />
        </button>
      </Field>

      <Field label="Proteções de carregamento" hint="O Forja estima a memória antes de subir o modelo; isto diz o que fazer quando não cabe.">
        <div className="space-y-1">
          {NIVEIS.map(([v, titulo, desc]) => (
            <label key={v} className="flex cursor-pointer items-start gap-2 rounded-lg px-1 py-1 hover:bg-raised">
              <input
                type="radio"
                className="mt-1 accent-sky-500"
                checked={st.guardrail === v}
                onChange={() => acao(api.put("/local/prefs", { guardrail: v }))}
              />
              <span>
                <span className="text-sm text-fg">{titulo}</span>
                <span className="block text-xs text-muted">{desc}</span>
              </span>
            </label>
          ))}
        </div>
      </Field>

      <Field label="Carregar o último modelo ao abrir" hint="Sobe sozinho o modelo local usado por último quando o Forja inicia.">
        <button
          role="switch"
          aria-checked={st.autoload}
          onClick={() => acao(api.put("/local/prefs", { autoload: !st.autoload }))}
          className={`h-5 w-10 rounded-full transition-colors ${st.autoload ? "bg-sky-500" : "bg-raised"}`}
        >
          <span className={`block size-4 rounded-full bg-white transition-transform ${st.autoload ? "translate-x-5" : "translate-x-0.5"}`} />
        </button>
      </Field>
    </div>
  );
}

/** Valor de um padrão global vindo do state (o backend já devolve os padrões resolvidos). */
function stDefault(st: any, chave: string) {
  return !!st?.defaults?.[chave];
}

/** Pastas padrão da IA local. Salva na hora, como a aba Aplicativo. */
function PastasTab(props: { onError: (e: string) => void; onChanged: () => void }) {
  const [st, setSt] = useState<{
    models_dir: string;
    image_dir: string;
    video_dir: string;
    data_dir: string;
    dirs: string[];
    hf_token: boolean;
  } | null>(null);
  const [salvo, setSalvo] = useState("");
  const [token, setToken] = useState("");
  const temToken = !!st?.hf_token;

  // Pasta padrão do Agente/Maestro: fica nas Configurações gerais (PUT /settings), não no local.json.
  const [wsPadrao, setWsPadrao] = useState<string | null>(null);

  useEffect(() => {
    api.get<typeof st>("/local").then(setSt).catch((e) => props.onError(e.message));
    api.get<{ workspace_padrao?: string }>("/settings").then((r) => setWsPadrao(r.workspace_padrao ?? "")).catch(() => setWsPadrao(""));
  }, []);

  async function salvarWs(pasta: string) {
    try {
      const r = await api.put<{ workspace_padrao: string }>("/settings", { workspace_padrao: pasta });
      setWsPadrao(r.workspace_padrao);
      setSalvo("Salvo.");
      props.onChanged(); // o app relê /config e a conversa nova já nasce com a pasta
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function salvarToken() {
    try {
      const r = await api.put<{ hf_token: boolean }>("/local/prefs", { hf_token: token });
      setSt((s) => s && { ...s, hf_token: r.hf_token });
      setToken("");
      setSalvo("Token salvo.");
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  if (!st) return <div className="text-muted">Carregando…</div>;

  async function salvar(patch: { models_dir?: string; image_dir?: string; video_dir?: string }) {
    try {
      const r = await api.put<{ models_dir: string; image_dir: string; video_dir: string; dirs: string[] }>("/local/paths", patch);
      setSt({ ...st!, ...r });
      setSalvo("Salvo.");
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  // A 1ª da lista é a padrão (models_dir); as outras são as "a mais", que o PUT /local/dirs recebe.
  async function pastas(extras: string[]) {
    try {
      const r = await api.put<{ dirs: string[] }>("/local/dirs", { dirs: extras });
      setSt({ ...st!, dirs: r.dirs });
      setSalvo("Salvo.");
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function adicionarPasta() {
    const escolhida = window.forja ? await window.forja.pickFolder("") : prompt("Caminho da pasta com os modelos:");
    if (escolhida) pastas([...st!.dirs.slice(1), escolhida]);
  }

  async function escolher(campo: "models_dir" | "image_dir" | "video_dir") {
    const atual = st![campo];
    const escolhida = window.forja ? await window.forja.pickFolder(atual) : prompt("Caminho da pasta:", atual);
    if (escolhida) salvar({ [campo]: escolhida });
  }

  const linha = (campo: "models_dir" | "image_dir" | "video_dir") => (
    <div className="flex items-center gap-2">
      <input
        className={input}
        value={st![campo]}
        spellCheck={false}
        onChange={(e) => setSt({ ...st!, [campo]: e.target.value })}
        onBlur={(e) => e.target.value !== "" && salvar({ [campo]: e.target.value })}
      />
      <button className={btn} onClick={() => escolher(campo)}>
        Escolher…
      </button>
    </div>
  );

  return (
    <div className="max-w-3xl">
      <Field
        label="Modelos baixados"
        hint="Para onde vão os downloads do painel IA local. As outras pastas continuam sendo varridas; troque lá quem é a padrão do download."
      >
        {linha("models_dir")}
      </Field>
      <Field
        label="Pastas de modelos instalados"
        hint="Todas são varridas (com as subpastas): o que estiver nelas aparece no IA local, sem copiar nada. A primeira é a de cima."
      >
        <div className="space-y-1.5">
          {st.dirs.map((d, i) => (
            <div key={d} className="flex items-center gap-2 rounded-lg border border-line bg-raised px-3 py-1.5 text-sm">
              <span className="min-w-0 flex-1 truncate text-fg" title={d}>{d}</span>
              {i === 0 ? (
                <span className="shrink-0 text-xs text-faint">padrão</span>
              ) : (
                <button
                  className="shrink-0 text-xs text-muted hover:text-red-400"
                  title="Parar de varrer esta pasta (os arquivos ficam no disco)"
                  onClick={() => pastas(st.dirs.slice(1).filter((x) => x !== d))}
                >
                  Remover
                </button>
              )}
            </div>
          ))}
          <button className={btn} onClick={adicionarPasta}>
            Adicionar pasta…
          </button>
        </div>
      </Field>
      <Field label="Imagens geradas" hint="Onde o painel salva as imagens. As geradas pelo agente vão para a pasta de trabalho da conversa.">
        {linha("image_dir")}
      </Field>
      <Field label="Vídeos gerados" hint="Onde a aba Vídeo salva as tomadas (e as ampliações de vídeo).">
        {linha("video_dir")}
      </Field>
      <Field
        label="Pasta padrão do Agente e da Maestro"
        hint="Conversa nova já nasce nesta pasta. Vazio: cada conversa nova pede uma pasta antes do primeiro envio."
      >
        <div className="flex items-center gap-2">
          <input
            className={input}
            value={wsPadrao ?? ""}
            placeholder="Nenhuma: escolher a cada conversa"
            spellCheck={false}
            onChange={(e) => setWsPadrao(e.target.value)}
            onBlur={(e) => salvarWs(e.target.value.trim())}
          />
          <button
            className={btn}
            onClick={async () => {
              const p = window.forja ? await window.forja.pickFolder(wsPadrao ?? "") : prompt("Caminho da pasta:", wsPadrao ?? "");
              if (p) salvarWs(p);
            }}
          >
            Escolher…
          </button>
          {!!wsPadrao && (
            <button className={btn} onClick={() => salvarWs("")}>
              Limpar
            </button>
          )}
        </div>
      </Field>
      <Field
        label="Token do Hugging Face"
        hint="Só para baixar modelo restrito (Llama, Gemma oficial...). Aceite os termos no site, cole o token aqui. Fica no seu computador."
      >
        <div className="flex items-center gap-2">
          <input
            className={input}
            type="password"
            placeholder={temToken ? "•••••••• (salvo)" : "hf_..."}
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <button className={btn} disabled={!token} onClick={() => salvarToken()}>
            Salvar
          </button>
        </div>
      </Field>
      <Field label="Dados do Forja" hint="Banco, configurações, logs, runtimes do llama.cpp e do sd.cpp. Não dá para mudar: é a pasta do usuário do app.">
        <div className="flex items-center gap-2">
          <input className={input} value={st.data_dir} readOnly spellCheck={false} />
          {window.forja?.desktop && (
            <button className={btn} onClick={() => window.forja!.desktop.open("data")}>
              Abrir
            </button>
          )}
        </div>
      </Field>
      {st.dirs.length > 1 && (
        <p className="text-xs text-muted">
          Também varrendo: {st.dirs.slice(1).join(" · ")}
        </p>
      )}
      {salvo && <p className="text-xs text-emerald-400">{salvo}</p>}
    </div>
  );
}

/** Janela, zoom, bandeja e início com o Windows. Salva na hora (não passa pelo botão Salvar). */
const ATUALIZACAO: Record<UpdateState["state"], string> = {
  idle: "Ainda não verificado.",
  dev: "Rodando fora do app instalado: não há release para comparar.",
  checking: "Procurando…",
  current: "Você está na versão mais recente.",
  available: "Há uma versão nova.",
  downloading: "Baixando…",
  ready: "Baixada e pronta para instalar.",
  error: "Não consegui verificar.",
};

/**
 * Atualização pelo GitHub Releases. Nada baixa nem instala sem clique: o instalador é grande, e
 * quem decide gastar a internet é quem está pagando por ela.
 */
function Atualizacao() {
  const bridge = window.forja?.update;
  const [u, setU] = useState<UpdateState | null>(null);

  useEffect(() => {
    if (!bridge) return;
    bridge.get().then(setU);
    // Enquanto a aba estiver aberta: é o progresso do download que muda sozinho.
    const t = setInterval(() => bridge.get().then(setU), 1000);
    return () => clearInterval(t);
  }, [bridge]);

  if (!bridge || !u) return null;
  const ocupado = u.state === "checking" || u.state === "downloading";

  return (
    <div className="space-y-2">
      <div className="text-sm text-fg">Atualização</div>
      <div className="space-y-2 rounded-xl border border-line bg-surface p-3">
        <div className="text-xs text-muted">
          {ATUALIZACAO[u.state]}
          {u.version && (u.state === "available" || u.state === "ready") ? ` Versão ${u.version}.` : ""}
          {u.state === "downloading" ? ` ${u.percent}%` : ""}
        </div>
        {u.error && <div className="text-xs text-red-300">{u.error}</div>}
        {/* O que muda na versão nova, como foi escrito no release-notes.md e guardado no latest.yml. */}
        {u.notes && (u.state === "available" || u.state === "ready") && (
          <div className="whitespace-pre-wrap rounded-lg border border-line bg-raised p-2 text-xs text-muted">{u.notes.trim()}</div>
        )}
        <div className="flex flex-wrap gap-2">
          <button className={btn} disabled={ocupado} onClick={() => bridge.check().then(setU)}>
            Procurar atualizações
          </button>
          {u.state === "available" && (
            <button className={btnPrimary} onClick={() => bridge.download().then(setU)}>
              Baixar
            </button>
          )}
          {u.state === "ready" && (
            <button className={btnPrimary} onClick={() => bridge.install()}>
              Reiniciar e instalar
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Tema, destaque e fonte: aplicam na hora, sem Salvar. */
function Aparencia() {
  const [a, setA] = useState(lerAparencia);
  const muda = (p: Partial<typeof a>) => {
    const n = { ...a, ...p };
    setA(n);
    salvarAparencia(n);
  };
  const tema = TEMAS.find((t) => t.id === a.tema) ?? TEMAS[0];
  return (
    <>
      <Field div label="Tema" hint="Cinzas e destaque da interface inteira. Vale na hora.">
        <div className="grid grid-cols-3 gap-2">
          {TEMAS.map((t) => (
            <button key={t.id} type="button" onClick={() => muda({ tema: t.id, destaque: null })}
                    className={`flex items-center gap-2.5 rounded-[9px] border px-2.5 py-2 text-left text-[12.5px] ${
                      a.tema === t.id ? "border-accent-line bg-accent-soft text-fg" : "border-line text-fg-2 hover:border-focus hover:text-fg"}`}>
              <span className="grid size-6 shrink-0 place-items-center rounded-md border border-line-strong" style={{ background: t.bg }}>
                <span className="size-2.5 rounded-full" style={{ background: t.accent }} />
              </span>
              {t.label}
            </button>
          ))}
        </div>
      </Field>
      <Field div label="Cor de destaque" hint="Botão principal, foco, item ativo e progresso. Em branco, a do tema.">
        <div className="flex items-center gap-2">
          <input type="color" value={a.destaque ?? tema.accent} onChange={(e) => muda({ destaque: e.target.value })}
                 className="h-8 w-12 cursor-pointer rounded-[7px] border border-line bg-surface p-0.5" />
          <span className="font-mono text-xs text-muted">{a.destaque ?? tema.accent}</span>
          {a.destaque && <button type="button" className={btn} onClick={() => muda({ destaque: null })}>Usar a do tema</button>}
        </div>
      </Field>
      <Field div label="Fonte" hint="Interface e código (números, caminhos, sementes).">
        <div className="grid gap-2">
          {FONTES.map((f) => (
            <button key={f.id} type="button" onClick={() => muda({ fonte: f.id })}
                    className={`rounded-[9px] border px-3 py-2 text-left ${a.fonte === f.id ? "border-accent-line bg-accent-soft" : "border-line hover:border-focus"}`}>
              <span className="block text-[13px] text-fg">{f.label}</span>
              <span className="block text-xs text-faint">{f.hint}</span>
            </button>
          ))}
        </div>
      </Field>
    </>
  );
}

function AppTab() {
  const bridge = window.forja?.desktop;
  if (!bridge) return <div className="max-w-3xl"><Aparencia /></div>;
  return <AppTabDesktop bridge={bridge} />;
}

function AppTabDesktop({ bridge }: { bridge: NonNullable<NonNullable<typeof window.forja>["desktop"]> }) {
  const [d, setD] = useState<DesktopState | null>(null);

  useEffect(() => {
    bridge.get().then(setD);
  }, [bridge]);

  if (!d) return <div className="text-muted">Carregando…</div>;
  const patch = (p: Parameters<typeof bridge.set>[0]) => bridge.set(p).then(setD);

  return (
    <div className="max-w-3xl">
      <Aparencia />
      <Field div label="Zoom da interface" hint="O mesmo que Ctrl + (+), Ctrl + (−) e Ctrl + 0 na janela, ou Ctrl + roda do mouse.">
        <div className="flex items-center gap-2">
          <button className={btn} onClick={() => bridge.zoom("out").then((zoom) => setD({ ...d, zoom }))} title="Diminuir (Ctrl -)">
            −
          </button>
          <span className="w-16 text-center font-mono text-sm text-fg">{Math.round(d.zoom * 100)}%</span>
          <button className={btn} onClick={() => bridge.zoom("in").then((zoom) => setD({ ...d, zoom }))} title="Aumentar (Ctrl +)">
            +
          </button>
          <button className={btn} onClick={() => bridge.zoom("reset").then((zoom) => setD({ ...d, zoom }))} title="Voltar para 100% (Ctrl 0)">
            100%
          </button>
        </div>
      </Field>

      <div className="space-y-2">
        <div className="text-sm text-fg">Ao fechar a janela</div>
        <div className="text-xs text-muted">O agente continua rodando enquanto o Forja estiver na bandeja.</div>
        <div className="mt-1.5 grid gap-2">
          {[
            { v: false, label: "Fechar o Forja", hint: "O X encerra o app e o backend. Nada fica rodando." },
            { v: true, label: "Minimizar para a bandeja", hint: "O X esconde a janela; o ícone ao lado do relógio reabre ou sai." },
          ].map((o) => (
            <label
              key={String(o.v)}
              className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 ${d.closeToTray === o.v ? "border-accent-line bg-accent-soft" : "border-line bg-surface hover:border-focus"}`}
            >
              <input type="radio" name="close" className="mt-0.5 size-4 accent-[var(--accent)]" checked={d.closeToTray === o.v} onChange={() => patch({ closeToTray: o.v })} />
              <span className="min-w-0">
                <span className="block text-sm text-fg">{o.label}</span>
                <span className="mt-0.5 block text-xs text-muted">{o.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-sm text-fg">Inicialização</div>
        <div className="grid gap-2">
          <Toggle
            label="Abrir o Forja junto com o Windows"
            hint={d.packaged ? undefined : "Só vale no app instalado; em desenvolvimento não mexe no registro."}
            checked={d.startWithWindows}
            disabled={!d.packaged}
            onChange={(v) => patch({ startWithWindows: v })}
          />
          <Toggle
            label="Iniciar direto na bandeja, sem abrir a janela"
            hint="Precisa de abrir com o Windows e de fechar-para-bandeja ligados."
            checked={d.startMinimized}
            disabled={!d.packaged || !d.startWithWindows || !d.closeToTray}
            onChange={(v) => patch({ startMinimized: v })}
          />
          <Toggle
            label="Manter o PC acordado para o celular"
            hint="Com o Forja aberto o Windows não entra em suspensão (a tela ainda apaga e pode ficar bloqueada), então o celular manda pedidos a qualquer hora. Desligado, ele só segura o sono enquanto um turno roda."
            checked={d.manterAcordado}
            onChange={(v) => patch({ manterAcordado: v })}
          />
        </div>
      </div>

      <Atualizacao />

      <div className="space-y-2">
        <div className="text-sm text-fg">Sobre</div>
        <div className="space-y-2 rounded-xl border border-line bg-surface p-3 text-xs">
          <div className="flex gap-2">
            <span className="w-24 shrink-0 text-muted">Versão</span>
            <span className="font-mono text-fg">
              {d.version}
              {d.packaged ? "" : " (desenvolvimento)"}
            </span>
          </div>
          <div className="flex gap-2">
            <span className="w-24 shrink-0 text-muted">Dados</span>
            <span className="min-w-0 flex-1 truncate font-mono text-fg" title={d.paths.data}>
              {d.paths.data}
            </span>
          </div>
          <div className="flex gap-2">
            <span className="w-24 shrink-0 text-muted">Conversas</span>
            <span className="min-w-0 flex-1 truncate font-mono text-fg" title={d.paths.db}>
              {d.paths.db}
            </span>
          </div>
          <div className="flex gap-2">
            <span className="w-24 shrink-0 text-muted">Markdown</span>
            <span className="min-w-0 flex-1 truncate font-mono text-fg" title={d.paths.md}>
              {d.paths.md}
            </span>
          </div>
          <div className="flex gap-2">
            <span className="w-24 shrink-0 text-muted">Log</span>
            <span className="min-w-0 flex-1 truncate font-mono text-fg" title={d.paths.log}>
              {d.paths.log}
            </span>
          </div>
          <div className="flex gap-2 pt-1">
            <button className={btn} onClick={() => bridge.open("data")}>
              Abrir a pasta de dados
            </button>
            <button className={btn} onClick={() => bridge.open("md")}>
              Abrir as conversas em Markdown
            </button>
            <button className={btn} onClick={() => bridge.open("db")}>
              Mostrar o banco
            </button>
            <button className={btn} onClick={() => bridge.open("log")}>
              Mostrar o log
            </button>
          </div>
        </div>
        <p className="text-xs text-muted">
          O banco <span className="font-mono">forja.db</span> guarda as conversas, provedores, chaves e configurações. Em
          paralelo, cada conversa é espelhada em Markdown em <span className="font-mono">conversas\forja-code</span> (agente) e{" "}
          <span className="font-mono">conversas\forja-chat</span> — arquivos soltos, prontos para copiar para outro PC, um backup
          ou o git. O espelho é só de leitura: editar o .md não muda a conversa, e apagar a conversa no Forja apaga o .md junto.
          Desinstalar o Forja não apaga nada disso.
        </p>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ provedores

/** Cota do Ollama Cloud logo abaixo da chave: é onde o usuário decide se aquele provedor ainda cabe. */
function ProviderUsage({ id }: { id: string }) {
  const uso = useCloudUsage().find((u) => u.provider === id);
  if (!uso) return null;
  return (
    <div className="rounded-xl border border-line bg-raised/40 p-2.5">
      <div className="mb-1.5 text-[11px] text-faint">Cota consumida</div>
      <UsageBars data={uso} models />
    </div>
  );
}

/** O que o Forja não controla neste tipo de servidor (tabela llm.CAPACIDADES do backend). */
function Limites({ caps }: { caps?: { nao: string[]; parcial: string[] } }) {
  if (!caps || (!caps.nao.length && !caps.parcial.length)) return null;
  return (
    <p className="text-xs text-muted">
      {caps.nao.length > 0 && <>Neste tipo o Forja não consegue: {caps.nao.join(", ")}. </>}
      {caps.parcial.length > 0 && <>Só em parte: {caps.parcial.join(", ")}.</>}
    </p>
  );
}

function Providers({ s, set }: { s: AppSettings; set: <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => void }) {
  const change = (i: number, patch: Partial<Provider>) =>
    set("providers", s.providers.map((p, k) => (k === i ? { ...p, ...patch } : p)));

  return (
    <div className="max-w-3xl">
      <p className="text-sm text-muted">
        Servidores compatíveis com a API da OpenAI. O tipo muda o jeito de falar: <span className="font-mono">ollama</span> usa
        a API nativa (respeita num_ctx), <span className="font-mono">lmstudio</span> lê a janela do modelo carregado,{" "}
        <span className="font-mono">openai</span> é o padrão para serviços com chave (OpenRouter, OpenAI, Groq...).
      </p>
      {s.providers.map((p, i) => (
        <div key={i} className="space-y-3 rounded-xl border border-line bg-surface p-4">
          <div className="flex gap-2">
            <input
              className={input}
              value={p.name}
              placeholder="Nome"
              onChange={(e) => change(i, { name: e.target.value })}
            />
            <input
              className={`${input} font-mono`}
              value={p.id}
              placeholder="id"
              onChange={(e) => change(i, { id: e.target.value })}
            />
            <select className={input} value={p.type} onChange={(e) => change(i, { type: e.target.value as Provider["type"] })}>
              <option value="ollama">ollama</option>
              <option value="lmstudio">lmstudio</option>
              <option value="openai">openai</option>
            </select>
            <button
              title="Remover"
              className="rounded-lg px-2 text-faint hover:text-red-400"
              onClick={() => set("providers", s.providers.filter((_, k) => k !== i))}
            >
              <Trash />
            </button>
          </div>
          <input
            className={`${input} font-mono`}
            value={p.url}
            placeholder="https://host:porta/v1"
            onChange={(e) => change(i, { url: e.target.value })}
          />
          {p.type === "openai" && (
            <Field
              label="Janela de contexto (tokens)"
              hint="Vazio: o Forja pergunta ao servidor (vLLM, OpenRouter e llama-server informam). Se o servidor não informar, preencha aqui, ou o agente recusa rodar em vez de chutar 32k. No vLLM é o --max-model-len."
            >
              <input
                type="number"
                className={`${input} font-mono`}
                value={p.context_window ?? ""}
                placeholder="perguntar ao servidor"
                onChange={(e) => change(i, { context_window: e.target.value ? Number(e.target.value) : null })}
              />
            </Field>
          )}
          <Limites caps={s.capacidades?.[p.type === "ollama" && p.url.includes("ollama.com") ? "ollama_nuvem" : p.type]} />
          <div className="flex items-center gap-2">
            <input
              type="password"
              className={`${input} font-mono`}
              value={p.api_key ?? ""}
              placeholder={p.has_api_key ? `chave salva ${p.api_key_hint}` : "chave de API (opcional)"}
              onChange={(e) => change(i, { api_key: e.target.value })}
            />
            {p.has_api_key && (
              <button className={btn} onClick={() => change(i, { api_key: "" , has_api_key: false, api_key_hint: "" })}>
                Apagar chave
              </button>
            )}
          </div>
          <ProviderUsage id={p.id} />
          <ProviderModels
            id={p.id}
            chosen={s.enabled_models[p.id]}
            onChange={(list) => set("enabled_models", { ...s.enabled_models, [p.id]: list })}
          />
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <button
          className={btn}
          onClick={() =>
            set("providers", [...s.providers, { id: "novo", name: "Novo provedor", type: "openai", url: "https://" }])
          }
        >
          + Adicionar provedor
        </button>
        <button
          className={btn}
          title="Modelos grandes rodando na nuvem da Ollama; precisa de chave em ollama.com/settings/keys"
          disabled={s.providers.some((p) => p.id === "ollama-cloud")}
          onClick={() =>
            set("providers", [
              ...s.providers,
              { id: "ollama-cloud", name: "Ollama Cloud", type: "ollama", url: "https://ollama.com/v1", api_key: "" },
            ])
          }
        >
          + Ollama Cloud
        </button>
        <button
          className={btn}
          disabled={s.providers.some((p) => p.id === "openrouter")}
          onClick={() =>
            set("providers", [
              ...s.providers,
              { id: "openrouter", name: "OpenRouter", type: "openai", url: "https://openrouter.ai/api/v1", api_key: "" },
            ])
          }
        >
          + OpenRouter
        </button>
      </div>
      <p className="text-xs text-muted">
        <strong className="text-fg">Ollama Cloud:</strong> depois de adicionar, cole a chave criada em ollama.com →
        Settings → Keys e clique em Salvar. Depois liste os modelos e marque os que quer no seletor do chat.
      </p>
    </div>
  );
}

/** Lista todos os modelos do provedor e deixa marcar quais aparecem no seletor do chat. */
function ProviderModels({ id, chosen, onChange }: {
  id: string;
  chosen: string[] | undefined; // undefined = todos
  onChange: (list: string[] | undefined) => void;
}) {
  const [all, setAll] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");

  const load = async () => {
    setError("");
    setOpen(true);
    try {
      setAll((await api.get<{ models: string[] }>(`/models?provider=${encodeURIComponent(id)}&all=1`)).models);
    } catch (e: any) {
      setAll(null);
      setError(e.message);
    }
  };

  const isOn = (m: string) => !chosen || chosen.includes(m);
  const toggle = (m: string) => {
    const base = chosen ?? all ?? [];
    const next = isOn(m) ? base.filter((x) => x !== m) : [...base, m];
    onChange(all && next.length === all.length ? undefined : next);
  };
  const visible = (all ?? []).filter((m) => m.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button className={btn} onClick={() => (open ? setOpen(false) : load())}>
          {open ? "Esconder modelos" : "Modelos no seletor…"}
        </button>
        <span className="text-xs text-muted">
          {chosen ? `${chosen.length} escolhido(s)` : "todos aparecem no seletor"}
          {all && ` · ${all.length} disponíveis`}
        </span>
      </div>
      {error && <p className="text-xs text-red-300">{error}</p>}
      {open && all && (
        <div className="rounded-xl border border-line bg-bg p-2">
          <div className="mb-2 flex items-center gap-2">
            <input className={input} placeholder="Filtrar" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className={btn} onClick={() => onChange(undefined)}>
              Todos
            </button>
            <button className={btn} onClick={() => onChange([])}>
              Nenhum
            </button>
          </div>
          <ul className="max-h-64 space-y-0.5 overflow-y-auto">
            {visible.map((m) => (
              <li key={m}>
                <label className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1 font-mono text-[13px] text-fg hover:bg-surface">
                  <input type="checkbox" checked={isOn(m)} onChange={() => toggle(m)} />
                  <span className="truncate">{m}</span>
                </label>
              </li>
            ))}
          </ul>
          <p className="mt-2 px-1 text-xs text-muted">Clique em Salvar no topo para aplicar.</p>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ subagentes

const SLOTS = [
  { key: "rapido", title: "Rápido", hint: "Modelo menor e rápido para tarefas simples: buscar, listar, resumir, edições óbvias." },
  { key: "capaz", title: "Capaz", hint: "Modelo maior e mais lento para raciocínio difícil: depurar, projetar, código complexo." },
  { key: "nuvem", title: "Nuvem", hint: "Rede de segurança: entra quando o slot escolhido não roda nesta máquina ou falha (ex.: Ollama Cloud). O modelo nunca escolhe este slot sozinho." },
] as const;

// ------------------------------------------------------------------ Maestro

const CICLOS: [string, string, string][] = [
  ["persistent", "Persistente", "O modelo fica carregado entre tarefas. Mais rápido quando o mesmo modelo faz várias."],
  ["unload_after_task", "Descarregar após a tarefa", "Libera VRAM/RAM ao fim de cada tarefa. Para quem troca de modelo com pouca memória."],
  ["unload_clear", "Descarregar e esperar a memória voltar", "Descarrega e só segue quando a VRAM livre para de subir: o driver devolve a memória depois do processo morrer, e o próximo modelo carregado antes disso cairia para a CPU."],
  ["restart_after_task", "Reiniciar o modelo após a tarefa", "Processo novo com o mesmo modelo, cache zerado. Para modelo que fica lento ou instável depois de muitas tarefas."],
];

/** Tudo que o usuário decide sobre o Maestro num lugar só (§30 do plano). Os slots de Worker são os
 * mesmos da aba Subagentes e da doca Modelo · VRAM: um valor, três lugares para mexer nele. */
function MaestroTab({ s, set }: { s: AppSettings; set: <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => void }) {
  const paralelo = s.max_workers > 1;
  const slot = (k: "rapido" | "capaz") => s.subagents[k] ?? { provider: "", model: "" };
  // Mesmos mínimos de janela do cockpit: GGUF local abaixo disso aparece desabilitado com o motivo.
  const [minimo, setMinimo] = useState<{ min_ctx_maestro?: number; min_ctx_worker?: number }>({});
  useEffect(() => {
    api.get<{ min_ctx_maestro?: number; min_ctx_worker?: number }>("/config").then(setMinimo).catch(() => {});
  }, []);
  return (
    <div className="max-w-3xl">
      <Field label="Modelo padrão da Maestro" hint="Usado na seção Maestro. Separado do modelo do chat e do agente: trocar um não troca o outro. Vazio = o modelo escolhido no chat.">
        <div className="flex items-center gap-2 [&>div]:ml-0">
          <ModelPicker
            provider={s.maestro_model?.provider ?? ""}
            model={s.maestro_model?.model ?? ""}
            autoFallback={false}
            loadLocal={false}
            minCtx={minimo.min_ctx_maestro}
            onChange={(provider, model) => set("maestro_model", { provider, model })}
          />
          {s.maestro_model?.model && (
            <button className={btn} onClick={() => set("maestro_model", { provider: "", model: "" })}>
              Limpar
            </button>
          )}
        </div>
      </Field>
      {(["rapido", "capaz"] as const).map((k) => (
        <Field key={k} label={`Worker ${k === "rapido" ? "rápido" : "capaz"}`}
               hint={k === "rapido" ? "Tarefas simples. A Maestro escolhe o nível por tarefa." : "Tarefas difíceis, e o padrão quando a tarefa não diz."}>
          <div className="flex items-center gap-2 [&>div]:ml-0">
            <ModelPicker
              provider={slot(k).provider}
              model={slot(k).model}
              autoFallback={false}
              loadLocal={false}
              minCtx={minimo.min_ctx_worker}
              onChange={(provider, model) => set("subagents", { ...s.subagents, [k]: { provider, model } })}
            />
          </div>
        </Field>
      ))}
      <Especialistas lista={s.worker_especialidades ?? []} minCtx={minimo.min_ctx_worker}
                     onChange={(l) => set("worker_especialidades", l)} />
      <Field label="Execução dos Workers" hint="Sequencial: um por vez — o único modo que troca de modelo local entre tarefas. Paralelo: tarefas independentes e sem arquivo em comum rodam juntas.">
        <div className="flex items-center gap-2">
          <select className={input} value={paralelo ? "paralelo" : "sequencial"}
                  onChange={(e) => set("max_workers", e.target.value === "paralelo" ? Math.max(2, s.max_workers) : 1)}>
            <option value="sequencial">Sequencial</option>
            <option value="paralelo">Paralelo</option>
          </select>
          {paralelo && (
            <label className="flex shrink-0 items-center gap-2 text-sm text-muted">
              até
              <input type="number" min={2} max={8} className={`${input} w-20`} value={s.max_workers}
                     onChange={(e) => set("max_workers", Math.min(8, Math.max(2, Number(e.target.value) || 2)))} />
              Workers
            </label>
          )}
        </div>
      </Field>
      <Field label="Ciclo de vida do modelo local" hint={CICLOS.find((c) => c[0] === s.model_lifecycle)?.[2]}>
        <select className={input} value={s.model_lifecycle} onChange={(e) => set("model_lifecycle", e.target.value)}>
          {CICLOS.map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
      </Field>
      <Toggle
        checked={s.maestro_browser}
        onChange={(v) => set("maestro_browser", v)}
        label="Validar entregas no navegador"
        hint="A Maestro abre a tela no navegador para conferir estrutura e erros de console. Desligado, ela valida só por testes e comandos — e o prompt fica menor."
      />
      {s.maestro_browser && (
        <Field label="Revisão visual (modelo com visão)"
               hint="Julga os prints desktop e mobile de cada entrega com tela (sobreposição, texto cortado, contraste, coerência); o que reprovar vira tarefa. Precisa enxergar imagem — em IA local, um GGUF com projetor mmproj. Vazio: os prints ficam no chat, mas o visual não é julgado.">
          <div className="flex items-center gap-2 [&>div]:ml-0">
            <ModelPicker
              provider={s.maestro_visual?.provider ?? ""}
              model={s.maestro_visual?.model ?? ""}
              autoFallback={false}
              loadLocal={false}
              onChange={(provider, model) => set("maestro_visual", { provider, model })}
            />
            {s.maestro_visual?.model && (
              <button className={btn} onClick={() => set("maestro_visual", { provider: "", model: "" })}>
                Limpar
              </button>
            )}
          </div>
        </Field>
      )}
      <Field label="Máximo de tentativas por tarefa" hint="Esgotou, a tarefa vai para 'precisa de você'. Vale para tarefas novas; dá para mudar uma a uma no painel da tarefa.">
        <Num value={s.maestro_max_attempts} onChange={(v) => set("maestro_max_attempts", v)} />
      </Field>
      <Field label="Máximo de passos da Maestro por mensagem" hint="Teto de segurança da execução autônoma (planejar, despachar, validar...).">
        <Num value={s.maestro_max_iterations} onChange={(v) => set("maestro_max_iterations", v)} />
      </Field>
      <LayoutCockpit />
    </div>
  );
}

/** Workers por especialidade. A Maestro vê só os que têm modelo (id, nome e "quando usar") e escolhe
 * por tarefa; sem escolha, o Forja decide pelo tipo da tarefa e pelos arquivos. */
function Especialistas(props: { lista: Especialidade[]; minCtx?: number; onChange: (l: Especialidade[]) => void }) {
  const muda = (i: number, patch: Partial<Especialidade>) =>
    props.onChange(props.lista.map((e, j) => (j === i ? { ...e, ...patch } : e)));
  return (
    <div>
      <span className="text-sm text-fg">Workers especialistas</span>
      <span className="mt-0.5 block text-xs text-muted">
        Um modelo por tipo de trabalho. A Maestro escolhe o especialista de cada tarefa; quando ela não escolhe, o Forja
        usa o tipo da tarefa (tela → Frontend, correção → Lógica, testes → Testes) e cai no Worker capaz se o
        especialista não tiver modelo. Sem modelo, o especialista não aparece para a Maestro.
      </span>
      <div className="mt-2 space-y-2">
        {props.lista.map((e, i) => (
          <div key={e.id || i} className="rounded-lg border border-line p-2.5">
            <div className="flex items-center gap-2">
              <div className="w-52 shrink-0">
                <input className={input} value={e.nome} placeholder="Nome"
                       onChange={(ev) => muda(i, { nome: ev.target.value })} />
              </div>
              <div className="min-w-0 flex-1 [&>div]:ml-0">
                <ModelPicker provider={e.provider} model={e.model} autoFallback={false} loadLocal={false}
                             minCtx={props.minCtx} onChange={(provider, model) => muda(i, { provider, model })} />
              </div>
              {e.model && (
                <button className="shrink-0 text-xs text-muted hover:text-fg" onClick={() => muda(i, { provider: "", model: "" })}>
                  Limpar
                </button>
              )}
              <button className="shrink-0 text-xs text-faint hover:text-red-300" title="Remover especialidade"
                      onClick={() => props.onChange(props.lista.filter((_, j) => j !== i))}>
                Remover
              </button>
            </div>
            <input className={`${input} mt-1.5 text-xs`} value={e.quando} placeholder="Quando usar (a Maestro lê isto)"
                   onChange={(ev) => muda(i, { quando: ev.target.value })} />
          </div>
        ))}
      </div>
      {props.lista.length < 12 && (
        <button className="mt-2 rounded-md border border-line px-3 py-1.5 text-sm text-muted hover:bg-raised hover:text-fg"
                onClick={() => props.onChange([...props.lista, { id: "", nome: "", quando: "", provider: "", model: "" }])}>
          Adicionar especialidade
        </button>
      )}
    </div>
  );
}

// Mesma chave do MaestroView: o padrão salvo pelo botão "Salvar layout como padrão" fica em `_padrao`.
const LAYOUT_CHAVE = "forja.maestro.layout";

function LayoutCockpit() {
  const ler = (): Record<string, unknown> => {
    try {
      return JSON.parse(localStorage.getItem(LAYOUT_CHAVE) || "{}");
    } catch {
      return {};
    }
  };
  const [temPadrao, setTemPadrao] = useState(() => "_padrao" in ler());
  return (
    // div, não Field: o <label> do Field repassa o clique ao primeiro botão de dentro, e depois do
    // primeiro clique esse botão já é o "Sim" da confirmação — confirmaria sozinho.
    <div>
      <span className="text-sm text-fg">Layout do cockpit</span>
      <span className="mt-0.5 block text-xs text-muted">
        Posição, tamanho e blocos recolhidos com que as conversas novas da Maestro começam. As conversas que já têm
        layout próprio não mudam.
      </span>
      <div className="mt-1.5">
      {temPadrao ? (
        <Confirma
          rotulo="Voltar ao layout original"
          pergunta="Conversas novas voltam ao layout original?"
          className="rounded-md border border-line px-3 py-1.5 text-sm text-muted hover:bg-raised hover:text-fg"
          onSim={() => {
            const { _padrao: _, ...resto } = ler();
            try {
              localStorage.setItem(LAYOUT_CHAVE, JSON.stringify(resto));
            } catch {
              /* sem storage: nada salvo, nada a apagar */
            }
            setTemPadrao(false);
          }}
        />
      ) : (
        <p className="text-sm text-faint">Original. Ajuste o cockpit e use "Salvar layout como padrão", que aparece no cabeçalho.</p>
      )}
      </div>
    </div>
  );
}

function SlotModels({ provider, value, onChange }: { provider: string; value: string; onChange: (m: string) => void }) {
  const [models, setModels] = useState<string[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
    if (!provider) return setModels([]);
    api
      .get<{ models: string[] }>(`/models?provider=${encodeURIComponent(provider)}&all=1`)
      .then((r) => setModels(r.models))
      .catch((e) => {
        setModels([]);
        setError(e.message);
      });
  }, [provider]);
  return (
    <>
      <select className={`${input} font-mono`} value={value} disabled={!provider} onChange={(e) => onChange(e.target.value)}>
        <option value="">(nenhum)</option>
        {value && !models.includes(value) && <option value={value}>{value}</option>}
        {models.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      {error && <p className="text-xs text-red-300">{error}</p>}
    </>
  );
}

function Subagents({ s, set }: { s: AppSettings; set: <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => void }) {
  // slot ausente (banco salvo antes de existir) não pode derrubar a tela inteira
  const spec = (slot: (typeof SLOTS)[number]["key"]) => s.subagents[slot] ?? { provider: "", model: "" };
  const change = (slot: (typeof SLOTS)[number]["key"], patch: Partial<{ provider: string; model: string }>) =>
    set("subagents", { ...s.subagents, [slot]: { ...spec(slot), ...patch } });
  return (
    <div className="max-w-3xl">
      <p className="text-sm text-muted">
        O agente principal pode delegar uma subtarefa com <span className="font-mono">delegate_task</span> e escolhe o nível
        pela dificuldade. O subagente usa as mesmas ferramentas, aprovações e pasta de trabalho; só o relatório final dele
        volta para a conversa, e os passos aparecem dentro do bloco da delegação. Sem nenhum slot configurado, a ferramenta
        não é oferecida ao modelo.
      </p>
      {SLOTS.map((slot) => (
        <section key={slot.key} className="space-y-2 rounded-xl border border-line bg-surface p-4">
          <h3 className="text-sm text-fg">{slot.title}</h3>
          <p className="text-xs text-muted">{slot.hint}</p>
          <div className="grid grid-cols-[10rem_1fr] gap-2">
            <select
              className={input}
              value={spec(slot.key).provider}
              onChange={(e) => change(slot.key, { provider: e.target.value, model: "" })}
            >
              <option value="">(desligado)</option>
              {s.providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <SlotModels
              provider={spec(slot.key).provider}
              value={spec(slot.key).model}
              onChange={(m) => change(slot.key, { model: m })}
            />
          </div>
        </section>
      ))}
      <Field label="Máximo de passos por subagente" hint="Evita que um subagente fique rodando sem fim.">
        <Num value={s.subagent_max_iterations} onChange={(v) => set("subagent_max_iterations", v)} />
      </Field>
    </div>
  );
}

// ------------------------------------------------------------------ ferramentas

function Tools({ tools, disabled, onToggle }: { tools: ToolInfo[]; disabled: string[]; onToggle: (d: string[]) => void }) {
  const off = new Set(disabled);
  const toggle = (name: string) => {
    const next = new Set(off);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onToggle([...next]);
  };
  const groups = new Map<string, ToolInfo[]>();
  for (const t of tools) {
    const g = t.source?.startsWith("mcp:") ? `MCP · ${t.source.slice(4)}` : "Nativas";
    groups.set(g, [...(groups.get(g) ?? []), t]);
  }
  return (
    <div className="max-w-3xl">
      <p className="text-sm text-muted">
        Ferramentas desligadas não são enviadas ao modelo nem podem ser chamadas. O painel lateral sempre mostra a lista real.
      </p>
      {[...groups].map(([group, list]) => (
        <section key={group}>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-[11px] font-medium tracking-wider text-faint uppercase">{group}</h3>
            <button className="text-xs text-muted hover:text-fg" onClick={() => onToggle([...off].filter((n) => !list.some((t) => t.name === n)))}>
              ligar todas
            </button>
          </div>
          <ul className="space-y-1.5">
            {list.map((t) => (
              <li key={t.name} className="flex items-start gap-3 rounded-xl border border-line bg-surface p-3">
                <Wrench className="mt-0.5 size-3.5 shrink-0 text-faint" />
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-sm text-fg">{t.name}</div>
                  <div className="text-xs text-muted">{t.description}</div>
                </div>
                {t.always_ask && <span className="rounded bg-raised px-1.5 py-0.5 text-[10px] text-amber-200">sempre pergunta</span>}
                <button
                  role="switch"
                  aria-checked={!off.has(t.name)}
                  onClick={() => toggle(t.name)}
                  className={`h-5 w-9 shrink-0 rounded-full p-0.5 transition ${off.has(t.name) ? "bg-raised" : "bg-emerald-600"}`}
                >
                  <span className={`block size-4 rounded-full bg-white transition ${off.has(t.name) ? "" : "translate-x-4"}`} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ permissões

function ListEditor({ title, hint, placeholder, value, onChange }: {
  title: string;
  hint: string;
  placeholder: string;
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const [novo, setNovo] = useState("");
  const add = () => {
    const v = novo.trim();
    if (v && !value.includes(v)) onChange([...value, v]);
    setNovo("");
  };
  return (
    <section className="space-y-2">
      <h3 className="text-sm text-fg">{title}</h3>
      <p className="text-xs text-muted">{hint}</p>
      <div className="flex gap-2">
        <input
          className={`${input} font-mono`}
          value={novo}
          placeholder={placeholder}
          onChange={(e) => setNovo(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <button className={btn} onClick={add}>
          Adicionar
        </button>
      </div>
      <ul className="space-y-1">
        {value.map((v) => (
          <li key={v} className="flex items-center justify-between rounded-lg border border-line bg-surface px-3 py-1.5">
            <span className="font-mono text-sm text-fg">{v}</span>
            <button className="text-faint hover:text-red-400" onClick={() => onChange(value.filter((x) => x !== v))}>
              <Trash className="size-3.5" />
            </button>
          </li>
        ))}
        {!value.length && <li className="text-sm text-muted">Nenhuma regra: tudo pede aprovação.</li>}
      </ul>
    </section>
  );
}

function Permissions({ s, save }: { s: AppSettings; save: (patch: Partial<AppSettings>) => void }) {
  return (
    <div className="max-w-3xl">
      <div className="flex items-start gap-2 rounded-xl border border-line bg-surface p-3 text-sm text-muted">
        <Shield className="mt-0.5 size-4 shrink-0 text-amber-200" />
        <span>
          Regras dispensam o card de aprovação. Aceita <span className="font-mono">*</span> como curinga, e a regra que
          liberou fica registrada no bloco da ferramenta. Cuidado com regras largas como{" "}
          <span className="font-mono">*</span> ou <span className="font-mono">git *</span>.
        </span>
      </div>
      <Toggle
        checked={s.auto_review}
        onChange={(v) => save({ auto_review: v })}
        label="Revisor automático no modo Automático"
        hint="Antes de mostrar o card de aprovação, o próprio modelo da conversa avalia o risco da ação (baixo, médio, alto). Risco baixo roda sem perguntar; o resto continua pedindo sua aprovação, com o motivo no card. Comando destrutivo sempre pergunta. Custa uma chamada ao modelo por aprovação."
      />
      <ListEditor
        title="Comandos liberados (run_command)"
        hint="Compara o comando inteiro. Ex.: pytest*, git status, ls *, npm run build"
        placeholder="pytest*"
        value={s.auto_approve_commands}
        onChange={(v) => save({ auto_approve_commands: v })}
      />
      <ListEditor
        title="Ferramentas liberadas"
        hint="Compara o nome da ferramenta. Ex.: write_file, mcp__memoria__*"
        placeholder="mcp__memoria__*"
        value={s.auto_approve_tools}
        onChange={(v) => save({ auto_approve_tools: v })}
      />
      <ListEditor
        title="Pastas confiáveis (hooks)"
        hint="Onde .forja/hooks.json pode rodar. O arquivo vem junto num git clone, então só a pasta que você liberar aqui executa os comandos dele — subpastas incluídas."
        placeholder="C:/Projetos/meu-app"
        value={s.trusted_hooks}
        onChange={(v) => save({ trusted_hooks: v })}
      />
    </div>
  );
}

// ------------------------------------------------------------------ mcp

type McpServidor = { ligado: boolean; permissao: string; url: string; token: string; comando: string; json: unknown };

/** E17: o Claude (Claude Code / Claude Desktop) planeja e escreve os cards; os Workers locais trabalham. */
function ClaudeControla() {
  const [c, setC] = useState<McpServidor | null>(null);
  const [msg, setMsg] = useState("");
  const [pasta, setPasta] = useState("");
  const [verToken, setVerToken] = useState(false);
  const carrega = () => api.get<McpServidor>("/mcp/servidor").then(setC).catch((e) => setMsg(e.message));
  useEffect(() => { carrega(); }, []);
  const muda = async (patch: Record<string, unknown>) => {
    setMsg("");
    try { await api.put("/settings", patch); await carrega(); } catch (e: any) { setMsg(e.message); }
  };
  const copia = (t: string, o: string) => navigator.clipboard.writeText(t).then(() => setMsg(`${o} copiado.`));
  if (!c) return null;
  return (
    <section className="space-y-3 rounded-xl border border-line bg-surface p-4">
      <div className="flex items-start gap-3">
        <button
          role="switch"
          aria-checked={c.ligado}
          aria-label="Permitir que o Claude controle o Forja"
          onClick={() => muda({ mcp_servidor: !c.ligado })}
          className={`mt-0.5 h-5 w-10 shrink-0 rounded-full transition-colors ${c.ligado ? "bg-sky-500" : "bg-raised"}`}
        >
          <span className={`block size-4 rounded-full bg-white transition-transform ${c.ligado ? "translate-x-5" : "translate-x-0.5"}`} />
        </button>
        <span>
          <span className="block text-sm font-medium text-fg">Permitir que o Claude controle o Forja</span>
          <span className="block text-xs text-muted">
            O Claude Code (ou o Claude Desktop) se conecta aqui por MCP: planeja, cria cards no board e despacha tarefas
            para os Workers locais. Tudo o que ele faz aparece numa conversa "Claude · projeto" (tipo Maestro), no PC e no
            celular, e o que você escreve nela chega a ele no resultado da próxima ferramenta.
          </span>
        </span>
      </div>
      <details className="rounded-xl border border-line bg-bg px-3 py-2 text-xs text-muted">
        <summary className="cursor-pointer select-none text-sm text-fg">Como usar e para que serve</summary>
        <div className="mt-2 space-y-3 leading-relaxed">
          <p>
            <span className="text-fg">Para que serve:</span> o Claude pensa e o Forja executa. Você pede a feature ao Claude Code
            como sempre; ele lê o projeto, divide em tarefas com critério de pronto e manda cada uma para um{" "}
            <span className="text-fg">Worker do Forja</span> (os modelos de Configurações › Subagentes, locais ou na nuvem). O
            Worker escreve o código, o Forja roda o verify e faz o commit, e o Claude confere o resultado e fecha. Ele também
            pode criar cards no board com arquivo e linha.
          </p>
          <ol className="list-decimal space-y-1.5 pl-5">
            <li>Ligue <span className="text-fg">Permitir que o Claude controle o Forja</span> (acima).</li>
            <li>
              No terminal, <span className="text-fg">dentro da pasta do projeto</span>, rode uma vez o comando que aparece abaixo
              (<span className="font-mono">claude mcp add …</span>). Para valer em todos os projetos, acrescente{" "}
              <span className="font-mono">--scope user</span>. No Claude Desktop, cole o JSON em Configurações › Desenvolvedor.
            </li>
            <li>
              Opcional: <span className="text-fg">Instalar no Claude Code</span>, mais abaixo, com a pasta do projeto. Aí o seu
              pedido e as respostas dele também aparecem no Forja.
            </li>
            <li>
              Abra o Claude Code no projeto e peça, por exemplo:{" "}
              <span className="font-mono text-fg">"Use o Forja: planeje o filtro por data na lista de pedidos e deixe os Workers
              implementarem."</span> Na primeira vez ele pede para usar as ferramentas do Forja: aceite.
            </li>
            <li>
              Acompanhe em <span className="text-fg">Maestro › "Claude · nome do projeto"</span>, no PC ou no celular: tarefas,
              Worker trabalhando, verify e commit. O que você escrever ali chega ao Claude na próxima ferramenta que ele chamar.
            </li>
          </ol>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg border border-line p-2.5">
              <div className="mb-1 font-medium text-fg">Delegue aos Workers do Forja</div>
              CRUD, tela que segue um padrão que já existe e bug com teste que prove.
            </div>
            <div className="rounded-lg border border-line p-2.5">
              <div className="mb-1 font-medium text-fg">Deixe com o Claude</div>
              Arquitetura, depuração difícil e decisões.
            </div>
          </div>
          <p>
            Assim o ciclo caro (ler arquivos, escrever, rodar teste, corrigir) roda nos Workers e gasta menos do seu plano do
            Claude; em tarefa pequena ou vaga a economia some, porque planejar e revisar custa quase o mesmo que fazer direto.{" "}
            <span className="text-fg">Aprovações:</span> escolha em "Ações do Claude"; no Manual, cada escrita do Worker pede o
            seu ok aqui e no celular.
          </p>
        </div>
      </details>
      {c.ligado && (
        <>
          <Field label="Ações do Claude" hint="Como as ações dele que mexem no projeto são aprovadas. No Manual, a aprovação aparece no Forja e no celular, como qualquer outra.">
            <select className={input} value={c.permissao} onChange={(e) => muda({ mcp_permissao: e.target.value })}>
              <option value="manual">Manual: pergunta antes</option>
              <option value="edits">Edições passam, o resto pergunta</option>
              <option value="auto">Automático</option>
              <option value="bypass">Ignorar permissões</option>
            </select>
          </Field>
          <div className="space-y-1.5">
            <div className="text-xs text-muted">No terminal do projeto, uma vez:</div>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-lg bg-bg px-3 py-2 font-mono text-xs text-fg" title={c.comando}>
                {verToken ? c.comando : c.comando.replace(c.token, "•".repeat(12))}
              </code>
              <button className={btn} onClick={() => copia(c.comando, "Comando")}>Copiar</button>
            </div>
            <details className="text-xs text-muted">
              <summary className="cursor-pointer select-none hover:text-fg">Claude Desktop ou mcp.json</summary>
              <div className="mt-2 flex items-start gap-2">
                <pre className="min-w-0 flex-1 overflow-x-auto rounded-lg bg-bg p-3 font-mono text-[11px] text-fg">
                  {JSON.stringify(c.json, null, 2).replace(verToken ? "\u0000" : c.token, "•".repeat(12))}
                </pre>
                <button className={btn} onClick={() => copia(JSON.stringify(c.json, null, 2), "JSON")}>Copiar</button>
              </div>
            </details>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <button className="text-faint hover:text-fg" onClick={() => setVerToken((v) => !v)}>{verToken ? "Esconder" : "Mostrar"} o token</button>
              <span className="text-faint">·</span>
              <button className="text-faint hover:text-red-300"
                onClick={async () => { setC(await api.post<McpServidor>("/mcp/servidor/token", {})); setMsg("Token novo: atualize o Claude com o comando acima."); }}>
                Revogar e gerar outro token
              </button>
            </div>
          </div>
          <div className="space-y-1.5 border-t border-line pt-3">
            <div className="text-sm text-fg">Conversa inteira no Forja (opcional)</div>
            <div className="text-xs text-muted">
              Instala hooks no Claude Code do projeto (<span className="font-mono">.claude/settings.local.json</span>, fora do
              git): o seu pedido, a resposta final dele e as ferramentas que ele usar aparecem na conversa do Forja. O arquivo
              não guarda o token.
            </div>
            <div className="flex items-center gap-2">
              <input className={`${input} font-mono text-xs`} placeholder="C:/caminho/do/projeto" value={pasta} onChange={(e) => setPasta(e.target.value)} />
              <button className={btn} disabled={!pasta.trim()} onClick={async () => {
                setMsg("");
                try {
                  const r = await api.post<{ arquivo: string }>("/mcp/servidor/hooks", { pasta: pasta.trim() });
                  setMsg(`Hooks gravados em ${r.arquivo}. Vale a partir da próxima sessão do Claude Code.`);
                } catch (e: any) { setMsg(e.message); }
              }}>Instalar no Claude Code</button>
            </div>
          </div>
        </>
      )}
      {msg && <p className="text-xs text-muted">{msg}</p>}
    </section>
  );
}

function Mcp({ mcp, onChanged }: { mcp: McpStatus | null; onChanged: () => void }) {
  const [text, setText] = useState("");
  const [path, setPath] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<{ text: string; path: string }>("/mcp/config").then((r) => {
      setText(r.text);
      setPath(r.path);
    });
  }, []);

  async function save() {
    setBusy(true);
    setMsg("");
    try {
      await api.put("/mcp/config", { text });
      setMsg("Salvo e reconectado.");
      onChanged();
    } catch (e: any) {
      setMsg(e.message);
    }
    setBusy(false);
  }

  return (
    <div className="max-w-3xl">
      <ClaudeControla />
      <h3 className="pt-2 text-sm font-medium text-fg">Servidores que o Forja usa</h3>
      <p className="text-sm text-muted">
        Servidores MCP, no mesmo formato do Claude Desktop. Comandos (<span className="font-mono">command</span>) rodam na
        sua máquina, no seu PATH — precisam do <span className="font-mono">npx</span> (Node.js) ou do{" "}
        <span className="font-mono">uvx</span> (uv) instalados. Para servidores HTTP use{" "}
        <span className="font-mono">url</span>.
      </p>
      <div className="font-mono text-xs text-faint">{path}</div>
      <textarea
        rows={16}
        spellCheck={false}
        className={`${input} font-mono text-xs`}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="flex items-center gap-3">
        <button className={btnPrimary} disabled={busy} onClick={save}>
          Salvar e reconectar
        </button>
        <span className="text-sm text-muted">{msg}</span>
      </div>
      <ul className="space-y-1 text-sm">
        {mcp?.servers.map((sv) => (
          <li key={sv.name} className="flex items-center justify-between rounded-lg border border-line bg-surface px-3 py-2">
            <span className="text-fg">{sv.name}</span>
            <span className="text-xs text-muted">
              {sv.transport} · {sv.status === "connected" ? `${sv.tools.length} ferramentas` : sv.status}
              {sv.error && <span className="text-red-300"> · {sv.error}</span>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------ memória

function ProjectMemory({ s, set, save }: {
  s: AppSettings;
  set: <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => void;
  save: (patch: Partial<AppSettings>) => void;
}) {
  const [file, setFile] = useState<{ content: string; exists: boolean; file: string } | null>(null);
  const [msg, setMsg] = useState("");

  const load = () => api.get<any>("/memory/project").then(setFile).catch(() => {});
  useEffect(() => {
    load();
  }, [s.project_memory_file]);

  return (
    <section className="mb-8 max-w-2xl space-y-3 border-b border-line pb-6">
      <div className="flex items-center justify-between">
        <h3 className="text-sm text-fg">Memória do projeto</h3>
        <label className="flex items-center gap-2 text-xs text-muted">
          <input
            type="checkbox"
            checked={s.project_memory}
            onChange={(e) => save({ project_memory: e.target.checked })}
          />
          enviar ao modelo
        </label>
      </div>
      <p className="text-xs text-muted">
        Arquivo na raiz da pasta de trabalho que vai junto no system prompt. O agente é instruído a mantê-lo com
        decisões, convenções e comandos do projeto. Você também pode editar aqui.
      </p>
      <div className="flex gap-2">
        <input
          className={`${input} font-mono`}
          value={s.project_memory_file}
          onChange={(e) => set("project_memory_file", e.target.value)}
          onBlur={() => save({ project_memory_file: s.project_memory_file })}
        />
        <button className={btn} onClick={load}>
          Recarregar
        </button>
      </div>
      <textarea
        rows={10}
        spellCheck={false}
        className={`${input} font-mono text-xs`}
        placeholder={`# ${s.project_memory_file}\n\n- O que a IA precisa lembrar deste projeto.`}
        value={file?.content ?? ""}
        onChange={(e) => setFile((f) => ({ ...(f ?? { exists: false, file: s.project_memory_file }), content: e.target.value }))}
      />
      <div className="flex items-center gap-3">
        <button
          className={btnPrimary}
          onClick={async () => {
            try {
              setFile(await api.put<any>("/memory/project", { content: file?.content ?? "" }));
              setMsg("Salvo em " + s.project_memory_file);
            } catch (e: any) {
              setMsg(e.message);
            }
          }}
        >
          Salvar arquivo
        </button>
        <span className="text-sm text-muted">{msg || (file?.exists ? "" : "O arquivo ainda não existe.")}</span>
      </div>
    </section>
  );
}

type Lembranca = { name: string; slug: string; description: string; type: string; updated: string; size: number };

/** O que o agente guardou sobre o usuário. No prompt entra só esta lista (uma linha cada); o conteúdo
 *  ele lê com `recall` quando o assunto aparece — é o que segura o custo no modelo local. */
function PersonalMemory({ s, set, save }: {
  s: AppSettings;
  set: <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => void;
  save: (patch?: Partial<AppSettings>) => Promise<void>;
}) {
  const [itens, setItens] = useState<Lembranca[] | null>(null);
  const [pasta, setPasta] = useState("");
  const [aberta, setAberta] = useState<string>("");
  const [corpo, setCorpo] = useState("");

  const carrega = () =>
    api
      .get<{ items: Lembranca[]; dir: string }>("/memory/personal")
      .then((r) => {
        setItens(r.items);
        setPasta(r.dir);
      })
      .catch(() => setItens([]));

  useEffect(() => {
    carrega();
  }, []);

  async function abrir(m: Lembranca) {
    if (aberta === m.slug) return setAberta("");
    setAberta(m.slug);
    setCorpo("");
    const r = await api.get<{ content: string }>(`/memory/personal/${encodeURIComponent(m.slug)}`).catch(() => null);
    setCorpo(r?.content ?? "(vazio)");
  }

  async function apagar(m: Lembranca) {
    await api.post("/memory/personal/delete", { names: [m.slug] }).catch(() => null);
    carrega();
  }

  return (
    <div className="max-w-3xl space-y-3">
      <Field
        label="Memória sobre você"
        hint="O agente guarda o que você contar de duradouro (como gosta de trabalhar, seu hardware, decisões suas). No prompt entra só o índice — uma linha por memória —, e o conteúdo só quando o assunto aparece. Vale a partir da conversa seguinte."
      >
        <Toggle
          checked={s.personal_memory}
          onChange={(v) => {
            set("personal_memory", v);
            save({ personal_memory: v });
          }}
          label={s.personal_memory ? "Ligada" : "Desligada"}
        />
      </Field>

      {itens === null ? (
        <p className="text-sm text-muted">Carregando…</p>
      ) : !itens.length ? (
        <p className="text-sm text-muted">Nada guardado ainda.</p>
      ) : (
        <div className="overflow-hidden rounded-xl border border-line">
          {itens.map((m) => (
            <div key={m.slug} className="border-b border-line last:border-0">
              <div className="flex items-center gap-3 px-3 py-2 hover:bg-raised">
                <button className="min-w-0 flex-1 text-left" onClick={() => abrir(m)}>
                  <span className="block truncate text-sm text-fg">{m.name}</span>
                  <span className="block truncate text-xs text-muted">{m.description}</span>
                </button>
                <span className="shrink-0 text-xs text-faint">{m.type}</span>
                <span className="shrink-0 text-xs text-faint">{m.updated}</span>
                <Confirma
                  rotulo="esquecer"
                  pergunta={`Esquecer "${m.name}"?`}
                  className="shrink-0 text-xs text-faint hover:text-red-400"
                  onSim={() => void apagar(m)}
                />
              </div>
              {aberta === m.slug && (
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap bg-code px-3 py-2 text-xs text-muted">
                  {corpo || "…"}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
      {pasta && <p className="text-xs text-faint">Arquivos em {pasta} — um .md por memória, dá para editar à mão.</p>}
    </div>
  );
}

function MemoryTab() {
  const [m, setM] = useState<Memory | null>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api.get<Memory>("/memory").then(setM).catch(() => setM(null));
  useEffect(() => {
    load();
  }, []);

  async function remove(name: string) {
    setBusy(true);
    setM(await api.post<Memory>("/memory/delete", { names: [name] }).catch(() => m));
    setBusy(false);
  }

  if (!m) return <div className="text-muted">Carregando…</div>;
  if (!m.available)
    return (
      <div className="max-w-xl space-y-3">
        <div className="rounded-xl border border-line bg-surface p-4 text-sm text-muted">{m.reason}</div>
        <p className="text-sm text-muted">
          A memória vem de um servidor MCP de grafo de conhecimento. Ligue um na aba MCP, por exemplo{" "}
          <span className="font-mono">@modelcontextprotocol/server-memory</span>.
        </p>
      </div>
    );

  const entities = m.entities.filter((e) => JSON.stringify(e).toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="max-w-3xl">
      <div className="flex items-center gap-3">
        <input className={input} placeholder="Buscar na memória" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className={btn} onClick={load}>
          Atualizar
        </button>
      </div>
      <div className="text-sm text-muted">
        {m.entities.length} entidades e {m.relations.length} relações · servidor <span className="font-mono">{m.server}</span>
      </div>
      {m.raw && <pre className="rounded-xl border border-line bg-surface p-3 text-xs whitespace-pre-wrap">{m.raw}</pre>}
      <ul className="space-y-2">
        {entities.map((e) => (
          <li key={e.name} className="rounded-xl border border-line bg-surface p-3">
            <div className="flex items-center gap-2">
              <span className="font-medium text-fg">{e.name}</span>
              {e.entityType && <span className="rounded bg-raised px-1.5 text-[10px] text-muted">{e.entityType}</span>}
              {m.can_delete && (
                <span className="ml-auto">
                  <Confirma
                    rotulo={<Trash className="size-3.5" />}
                    pergunta="Apagar da memória da IA?"
                    titulo="Apagar"
                    className="text-faint hover:text-red-400"
                    desabilitado={busy}
                    onSim={() => void remove(e.name)}
                  />
                </span>
              )}
            </div>
            {!!e.observations?.length && (
              <ul className="mt-1.5 space-y-0.5 text-sm text-muted">
                {e.observations.map((o, i) => (
                  <li key={i}>— {o}</li>
                ))}
              </ul>
            )}
            {m.relations.some((r) => r.from === e.name) && (
              <div className="mt-1.5 text-xs text-faint">
                {m.relations
                  .filter((r) => r.from === e.name)
                  .map((r) => `${r.relationType ?? "→"} ${r.to}`)
                  .join(" · ")}
              </div>
            )}
          </li>
        ))}
      </ul>
      {!entities.length && <div className="text-muted">Nada encontrado.</div>}
    </div>
  );
}

// ---------------------------------------------------------------- skills

type SkillCfg = {
  name: string;
  kind: "action" | "prompt";
  description: string;
  prompt?: string;
  origem: "forja" | "usuario" | "projeto";
  editavel: boolean;
  source?: string;
};

const ORIGEM: Record<SkillCfg["origem"], string> = { usuario: "Suas skills", projeto: "Do projeto (pasta padrão)", forja: "Do Forja" };
const vazia = { name: "", description: "", prompt: "", antigo: "" };

/** Skills: as do Forja e as do projeto só aparecem; as do usuário se criam, editam e apagam aqui. */
function SkillsTab({ onError }: { onError: (e: string) => void }) {
  const [lista, setLista] = useState<SkillCfg[] | null>(null);
  const [pasta, setPasta] = useState("");
  const [editando, setEditando] = useState<typeof vazia | null>(null);
  const [aberta, setAberta] = useState<string | null>(null);

  const carregar = () =>
    api.get<{ skills: SkillCfg[]; pasta: string }>("/skills")
      .then((r) => { setLista(r.skills); setPasta(r.pasta); })
      .catch((e) => onError(e.message));
  useEffect(() => { carregar(); }, []);

  async function salvar() {
    if (!editando) return;
    try {
      await api.put("/skills", editando);
      setEditando(null);
      carregar();
    } catch (e: any) {
      onError(e.message);
    }
  }

  async function apagar(nome: string) {
    try {
      await api.del(`/skills/${encodeURIComponent(nome)}`);
      carregar();
    } catch (e: any) {
      onError(e.message);
    }
  }

  if (!lista) return <div className="text-muted">Carregando…</div>;
  return (
    <div className="max-w-2xl space-y-5 text-sm">
      <p className="text-xs text-muted">
        Uma skill é um conjunto de instruções que o agente segue. Chame no começo da mensagem com{" "}
        <code className="text-fg">/nome</code> ou em qualquer ponto com <code className="text-fg">/skill:nome</code>, várias
        por mensagem. As suas ficam em <code className="break-all text-faint">{pasta}</code>, uma pasta por skill com um{" "}
        <code className="text-fg">SKILL.md</code>; arquivos que você puser na pasta viram recurso que a skill pode mandar ler.
      </p>

      {editando ? (
        <div className="space-y-3 rounded-xl border border-line bg-surface p-4">
          <div className="text-sm font-medium">{editando.antigo ? `Editar /${editando.antigo}` : "Nova skill"}</div>
          <Field label="Nome" hint="Vira o comando: minúsculas, números e hífens.">
            <input className={input} value={editando.name} placeholder="revisar-textos" spellCheck={false}
                   onChange={(e) => setEditando({ ...editando, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} />
          </Field>
          <Field label="Descrição" hint="Aparece no menu do / e diz ao agente quando usar.">
            <input className={input} value={editando.description} placeholder="Revisa ortografia e clareza de um texto"
                   onChange={(e) => setEditando({ ...editando, description: e.target.value })} />
          </Field>
          <Field label="Instruções" hint="O que o agente deve fazer. $ARGUMENTS vira o que vier depois do /nome.">
            <textarea rows={9} className={`${input} font-mono text-xs`} value={editando.prompt}
                      placeholder={"Revise o texto de $ARGUMENTS: corrija ortografia e concordância,\nsem mudar o tom. Liste as mudanças no fim."}
                      onChange={(e) => setEditando({ ...editando, prompt: e.target.value })} />
          </Field>
          <div className="flex gap-2">
            <button className={btnPrimary} disabled={!editando.name || !editando.prompt.trim()} onClick={salvar}>Salvar skill</button>
            <button className={btn} onClick={() => setEditando(null)}>Cancelar</button>
          </div>
        </div>
      ) : (
        <button className={btn} onClick={() => setEditando({ ...vazia })}>+ Nova skill</button>
      )}

      {(["usuario", "projeto", "forja"] as const).map((origem) => {
        const grupo = lista.filter((sk) => sk.origem === origem);
        if (!grupo.length && origem !== "usuario") return null;
        return (
          <section key={origem}>
            <div className="mb-1.5 text-xs font-medium text-muted">{ORIGEM[origem]}</div>
            {!grupo.length && <div className="rounded-xl border border-dashed border-line px-3 py-2.5 text-xs text-faint">Nenhuma ainda.</div>}
            <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line">
              {grupo.map((sk) => (
                <li key={sk.name} className="bg-surface">
                  <div className="flex items-center gap-3 px-3 py-2">
                    <button onClick={() => setAberta(aberta === sk.name ? null : sk.name)} className="min-w-0 flex-1 text-left"
                            title={sk.kind === "prompt" ? "Ver as instruções" : undefined}>
                      <span className="font-mono text-fg">/{sk.name}</span>
                      <span className="ml-2 text-xs text-muted">{sk.description}</span>
                    </button>
                    <span className="shrink-0 text-[11px] text-faint">
                      {sk.kind === "action" ? "ação da interface" : `/skill:${sk.name}`}
                    </span>
                    {sk.editavel && (
                      <>
                        <button className="shrink-0 text-xs text-muted hover:text-fg"
                                onClick={() => setEditando({ name: sk.name, description: sk.description, prompt: sk.prompt ?? "", antigo: sk.name })}>
                          Editar
                        </button>
                        <Confirma rotulo="Apagar" pergunta={`Apagar a skill ${sk.name} e a pasta dela?`}
                                  className="shrink-0 text-xs text-muted hover:text-red-300" onSim={() => void apagar(sk.name)} />
                      </>
                    )}
                  </div>
                  {aberta === sk.name && sk.prompt && (
                    <pre className="max-h-60 overflow-auto border-t border-line bg-bg px-3 py-2 text-[11px] whitespace-pre-wrap text-muted">
                      {sk.prompt}
                    </pre>
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

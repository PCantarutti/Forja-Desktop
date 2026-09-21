import { useEffect, useState } from "react";
import { UsageBars, useCloudUsage } from "./CloudUsage";
import { api } from "../api";
import { Modal } from "./Modal";
import type { McpStatus, ToolInfo } from "./InfoPanel";
import { Shield, Trash, Wrench } from "./icons";

export type Provider = {
  id: string;
  name: string;
  type: "ollama" | "lmstudio" | "openai";
  url: string;
  api_key?: string; // só enviado; nunca volta do backend
  has_api_key?: boolean;
  api_key_hint?: string;
};

export type AppSettings = {
  providers: Provider[];
  num_ctx: number;
  max_iterations: number;
  max_file_bytes: number;
  shell_timeout_max: number;
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

const BASE_TABS = ["Geral", "Pastas", "Runtime", "Hardware", "Provedores", "Subagentes", "Ferramentas", "Permissões", "MCP", "Memória"] as const;
type Tab = (typeof BASE_TABS)[number] | "Aplicativo";
// "Aplicativo" (janela, bandeja, início com o Windows) só existe dentro do Electron.
const tabs = (): Tab[] => (window.forja?.desktop ? ["Aplicativo", ...BASE_TABS] : [...BASE_TABS]);

const input = "w-full rounded-lg border border-line bg-raised px-3 py-1.5 text-sm text-fg focus:border-[#555] focus:outline-none";
const btn = "rounded-full border border-line px-3 py-1.5 text-sm text-fg hover:bg-raised";
const btnPrimary = "rounded-full bg-fg px-4 py-1.5 text-sm font-medium text-black hover:bg-white disabled:opacity-40";

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-sm text-fg">{label}</span>
      {hint && <span className="mt-0.5 block text-xs text-muted">{hint}</span>}
      <div className="mt-1.5">{children}</div>
    </label>
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
      setSaved("Salvo.");
      props.onChanged();
    } catch (e: any) {
      setError(e.message);
    }
    setBusy(false);
  }

  async function resetAll() {
    if (!confirm("Voltar todas as configurações para os valores do .env?")) return;
    setS(await api.post<AppSettings>("/settings/reset", {}));
    setDirty({});
    props.onChanged();
  }

  return (
    <Modal
      onClose={props.onClose}
      // Clicar fora com campo mexido apagava a edição sem perguntar — um erro de mira custava
      // um mcp.json ou uma instrução personalizada inteira.
      canClose={() => !Object.keys(dirty).length || confirm("Descartar as alterações não salvas?")}
      label="Configurações"
      className="flex h-[85vh] w-full max-w-4xl overflow-hidden rounded-2xl border border-line bg-bg"
    >
        <nav className="flex w-44 shrink-0 flex-col gap-0.5 border-r border-line bg-side p-3">
          <div className="mb-2 px-2 text-sm font-medium">Configurações</div>
          {tabs().map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-3 py-1.5 text-left text-sm ${tab === t ? "bg-raised text-fg" : "text-muted hover:bg-surface hover:text-fg"}`}
            >
              {t}
            </button>
          ))}
          <button onClick={resetAll} className="mt-auto rounded-lg px-3 py-1.5 text-left text-xs text-muted hover:text-red-300">
            Restaurar padrões
          </button>
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-3 border-b border-line px-5 py-3">
            <h2 className="flex-1 text-sm text-muted">{tab}</h2>
            {error && <span className="truncate text-sm text-red-300">{error}</span>}
            {saved && <span className="text-sm text-emerald-400">{saved}</span>}
            {!["MCP", "Memória", "Aplicativo", "Pastas", "Runtime", "Hardware"].includes(tab) && (
              <button className={btnPrimary} disabled={busy || !Object.keys(dirty).length} onClick={() => save()}>
                Salvar
              </button>
            )}
            <button onClick={props.onClose} className={btn}>
              Fechar
            </button>
          </header>

          <div className="flex-1 overflow-y-auto p-5">
            {tab === "Aplicativo" ? (
              <AppTab />
            ) : tab === "Pastas" ? (
              <PastasTab onError={setError} />
            ) : tab === "Runtime" ? (
              <RuntimeTab onError={setError} />
            ) : tab === "Hardware" ? (
              <HardwareTab onError={setError} />
            ) : !s ? (
              <div className="text-muted">Carregando…</div>
            ) : tab === "Geral" ? (
              <div className="max-w-xl space-y-5">
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
        </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ aplicativo (Electron)

function Toggle({ checked, onChange, label, hint, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; disabled?: boolean }) {
  return (
    <label className={`flex items-start gap-3 rounded-xl border border-line bg-surface p-3 ${disabled ? "opacity-50" : "cursor-pointer hover:border-[#3d3d3d]"}`}>
      <input type="checkbox" className="mt-0.5 size-4 accent-white" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
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

  const bloco = (kind: "llama" | "sd", titulo: string, descricao: string) => {
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
              return (
                <button
                  key={b}
                  className={btn}
                  onClick={() => acao(api.post("/local/runtime", { kind, backend: b }))}
                  title={b === "cuda" ? "NVIDIA. Baixa também o runtime da NVIDIA (~370 MB)." : b === "vulkan" ? "Qualquer GPU: NVIDIA, AMD e Intel." : "Sem GPU: roda na CPU."}
                >
                  {tem ? `Atualizar ${b}` : `Baixar ${b}`}
                </button>
              );
            })}
          </div>
        </div>
      </Field>
    );
  };

  return (
    <div className="max-w-2xl space-y-5">
      {bloco("llama", "Motor de chat (llama.cpp)", "CPU, Vulkan e CUDA convivem no disco: dá para trocar a qualquer momento, sem baixar de novo.")}
      {bloco("sd", "Motor de imagem (stable-diffusion.cpp)", "Mesma ideia, para a geração de imagem.")}
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
    <div className="max-w-2xl space-y-5">
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
                className={`rounded-full px-3 py-1 text-xs ${g.enabled ? "bg-sky-600 text-white" : "border border-line text-muted"}`}
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
function PastasTab(props: { onError: (e: string) => void }) {
  const [st, setSt] = useState<{
    models_dir: string;
    image_dir: string;
    data_dir: string;
    dirs: string[];
    hf_token: boolean;
  } | null>(null);
  const [salvo, setSalvo] = useState("");
  const [token, setToken] = useState("");
  const temToken = !!st?.hf_token;

  useEffect(() => {
    api.get<typeof st>("/local").then(setSt).catch((e) => props.onError(e.message));
  }, []);

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

  async function salvar(patch: { models_dir?: string; image_dir?: string }) {
    try {
      const r = await api.put<{ models_dir: string; image_dir: string; dirs: string[] }>("/local/paths", patch);
      setSt({ ...st!, ...r });
      setSalvo("Salvo.");
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function escolher(campo: "models_dir" | "image_dir") {
    const atual = st![campo];
    const escolhida = window.forja ? await window.forja.pickFolder(atual) : prompt("Caminho da pasta:", atual);
    if (escolhida) salvar({ [campo]: escolhida });
  }

  const linha = (campo: "models_dir" | "image_dir") => (
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
    <div className="max-w-2xl space-y-5">
      <Field
        label="Modelos baixados"
        hint="Para onde vão os downloads do painel IA local. As outras pastas continuam sendo varridas; troque lá quem é a padrão do download."
      >
        {linha("models_dir")}
      </Field>
      <Field label="Imagens geradas" hint="Onde o painel salva as imagens. As geradas pelo agente vão para a pasta de trabalho da conversa.">
        {linha("image_dir")}
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
function AppTab() {
  const bridge = window.forja!.desktop;
  const [d, setD] = useState<DesktopState | null>(null);

  useEffect(() => {
    bridge.get().then(setD);
  }, [bridge]);

  if (!d) return <div className="text-muted">Carregando…</div>;
  const patch = (p: Parameters<typeof bridge.set>[0]) => bridge.set(p).then(setD);

  return (
    <div className="max-w-xl space-y-5">
      <Field label="Zoom da interface" hint="O mesmo que Ctrl + (+), Ctrl + (−) e Ctrl + 0 na janela, ou Ctrl + roda do mouse.">
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
              className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 ${d.closeToTray === o.v ? "border-[#4d4d4d] bg-raised" : "border-line bg-surface hover:border-[#3d3d3d]"}`}
            >
              <input type="radio" name="close" className="mt-0.5 size-4 accent-white" checked={d.closeToTray === o.v} onChange={() => patch({ closeToTray: o.v })} />
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
        </div>
      </div>

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

function Providers({ s, set }: { s: AppSettings; set: <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => void }) {
  const change = (i: number, patch: Partial<Provider>) =>
    set("providers", s.providers.map((p, k) => (k === i ? { ...p, ...patch } : p)));

  return (
    <div className="max-w-2xl space-y-4">
      <p className="text-sm text-muted">
        Servidores compatíveis com a API da OpenAI. O tipo muda o jeito de falar: <span className="font-mono">ollama</span> usa
        a API nativa (respeita num_ctx), <span className="font-mono">lmstudio</span> lê a janela do modelo carregado,{" "}
        <span className="font-mono">openai</span> é o padrão para serviços com chave (OpenRouter, OpenAI, Groq...).
      </p>
      {s.providers.map((p, i) => (
        <div key={i} className="space-y-3 rounded-2xl border border-line bg-surface p-4">
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
    <div className="max-w-2xl space-y-5">
      <p className="text-sm text-muted">
        O agente principal pode delegar uma subtarefa com <span className="font-mono">delegate_task</span> e escolhe o nível
        pela dificuldade. O subagente usa as mesmas ferramentas, aprovações e pasta de trabalho; só o relatório final dele
        volta para a conversa, e os passos aparecem dentro do bloco da delegação. Sem nenhum slot configurado, a ferramenta
        não é oferecida ao modelo.
      </p>
      {SLOTS.map((slot) => (
        <section key={slot.key} className="space-y-2 rounded-2xl border border-line bg-surface p-4">
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
    <div className="max-w-2xl space-y-5">
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
    <div className="max-w-2xl space-y-6">
      <div className="flex items-start gap-2 rounded-xl border border-line bg-surface p-3 text-sm text-muted">
        <Shield className="mt-0.5 size-4 shrink-0 text-amber-200" />
        <span>
          Regras dispensam o card de aprovação. Aceita <span className="font-mono">*</span> como curinga, e a regra que
          liberou fica registrada no bloco da ferramenta. Cuidado com regras largas como{" "}
          <span className="font-mono">*</span> ou <span className="font-mono">git *</span>.
        </span>
      </div>
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
    <div className="max-w-2xl space-y-4">
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
    if (!confirm(`Esquecer "${m.name}"?`)) return;
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
                <button className="shrink-0 text-xs text-faint hover:text-red-400" onClick={() => apagar(m)}>
                  esquecer
                </button>
              </div>
              {aberta === m.slug && (
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap bg-[#0d0d0d] px-3 py-2 text-xs text-muted">
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
    if (!confirm(`Apagar "${name}" da memória da IA?`)) return;
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
    <div className="max-w-2xl space-y-4">
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
                <button
                  disabled={busy}
                  onClick={() => remove(e.name)}
                  className="ml-auto text-faint hover:text-red-400"
                  title="Apagar"
                >
                  <Trash className="size-3.5" />
                </button>
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

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ImageOpts, ImageParams, Inference, InferenceView, Job, LlamaParams, LocalModel, LocalState, ModelView,
  VideoKit } from "../types";
import { Check, ChevronDown, Download, ExternalLink, Film, FolderOpen, Search, Square, Trash, X } from "./icons";
import Confirma from "./Confirma";
import ModelSearch from "./ModelSearch";
import SelosModo from "./SelosModo";
import { BaixarAmpliacao } from "./AmpliarVideo";
import { useStickyBottom } from "../useStickyBottom";

const POLL_MS = 3000;
export const card = "rounded-2xl border border-line bg-surface p-3.5";
export const btn = "rounded-full border border-line px-3 py-1 text-fg hover:bg-raised disabled:opacity-40";
export const btnPrimary = "rounded-full bg-fg px-3 py-1 font-medium text-black hover:bg-white disabled:opacity-40";
// `campo` sem largura: quem precisa de outra (w-24, w-auto) usa a base, senão o w-full do `input` vence
// no CSS e o irmão flex-1 colapsa para zero.
export const campo = "rounded-lg border border-line bg-raised px-2 py-1 text-xs text-fg focus:border-[#555] focus:outline-none";
export const input = `w-full ${campo}`;

const CACHE_TYPES = ["f16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0"];
export const SAMPLERS = ["euler_a", "euler", "heun", "dpm2", "dpm++2s_a", "dpm++2m", "dpm++2mv2", "ipndm", "lcm",
  "ddim_trailing", "tcd", "res_multistep", "er_sde", "dpm++2m_sde", "lms"];
const SUBTABS = ["Modelos", "Inferência", "Baixar", "Imagem", "Vídeo"] as const;
type SubTab = (typeof SUBTABS)[number];

/** O que cada controle faz, em uma frase — é o tooltip do (?), como no LM Studio. */
const AJUDA: Record<string, string> = {
  ctx: "Quantos tokens o modelo enxerga de uma vez (conversa + resposta). Cada token ocupa cache KV: dobrar o contexto dobra essa memória.",
  ngl: "Quantas camadas ficam na GPU. O llama.cpp sobe as últimas; o que não couber roda na CPU, bem mais devagar.",
  flash_attn: "Atenção otimizada: menos memória e mais velocidade. Precisa estar ligada para quantizar o cache KV.",
  cache_type_k: "Quantização do cache KV das chaves. q8_0 corta quase metade da memória com perda pequena; f16 é o seguro.",
  cache_type_v: "Quantização do cache KV dos valores. Se o modelo começar a falar bobagem, volte este para f16.",
  threads: "Threads da CPU na geração. 0 = o llama.cpp escolhe (costuma dar o número de núcleos físicos).",
  batch: "Quantos tokens do prompt entram por rodada lógica. Maior acelera a leitura de prompts grandes.",
  ubatch: "Quantos tokens vão de fato juntos para a GPU. Menor gasta menos memória de cálculo.",
  parallel: "Quantas conversas o servidor atende ao mesmo tempo. 0 = o llama.cpp decide (hoje, 4 slots).",
  ctx_checkpoints: "Pontos de retomada do contexto: evitam reprocessar tudo quando o histórico é reescrito.",
  n_cpu_moe: "Deixa os especialistas (MoE) das primeiras N camadas na RAM. É o jeito de rodar um MoE grande com pouca VRAM.",
  n_expert: "Quantos especialistas o modelo consulta por token. Menos = mais rápido e mais burro.",
  seed: "Semente do sorteio de tokens. 0 = aleatória a cada resposta.",
  rope_freq_base: "Ajuste do RoPE para esticar o contexto além do treinado. 0 = o valor do próprio modelo.",
  rope_freq_scale: "Escala do RoPE, junto com a frequência base. 0 = o valor do próprio modelo.",
  kv_unified: "Um cache KV único para todos os slots, em vez de um pedaço por slot.",
  no_kv_offload: "Cache KV na VRAM. Desligue para deixá-lo na RAM: libera VRAM e custa velocidade.",
  mlock: "Trava o modelo na memória para o Windows não empurrar para o disco.",
  mmap: "Mapeia o arquivo em vez de copiar tudo para a RAM. Ligado carrega mais rápido; com especialistas na CPU o llama.cpp sugere desligar.",
  mmproj: "Arquivo mmproj-*.gguf do mesmo modelo: liga a visão, e aí as imagens do chat chegam ao modelo.",
  fit: "Deixa o llama.cpp reduzir sozinho o que não couber na memória (-fit on). A estimativa acima é estimativa; ele mede na hora.",
  temperature: "Quanto o modelo arrisca. Baixo (0,2) responde sempre parecido e obedece mais; alto (1,0+) inventa mais. Para agente, baixo costuma ser melhor.",
  top_k: "Só os K tokens mais prováveis entram no sorteio. 0 desliga o corte.",
  top_p: "Corta a cauda: sorteia entre os tokens que somam P de probabilidade. 1 desliga.",
  min_p: "Descarta token com probabilidade menor que essa fração do melhor token. Corte mais esperto que o top_p.",
  repeat_penalty: "Penaliza repetir o que já foi dito. 1 = sem penalidade; acima de 1,2 costuma estragar código.",
  max_tokens: "Teto de tokens por resposta. 0 = sem teto (o modelo para quando quiser).",
  stop: "Textos que cortam a resposta assim que aparecerem. Um por linha.",
  think: "Liga o raciocínio do modelo (enable_thinking do template). Desligado, ele responde direto — mais rápido, menos cuidadoso.",
  reasoning_budget: "Teto de tokens de raciocínio antes de responder. -1 = sem teto, 0 = não pensa.",
};

function size(n: number): string {
  if (!n) return "";
  return n >= 1 << 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : `${Math.round(n / 2 ** 20)} MB`;
}

const gb = (n: number) => `${(n / 2 ** 30).toFixed(2)} GB`;

/** Painel IA local: runtimes do llama.cpp/sd.cpp, modelos .gguf, carga e geração de imagem. */
export default function LocalPanel(props: {
  onRunning: (running: boolean, alias: string) => void;
  chatModel?: string; // modelo escolhido no chat: a aba Inferência cai nele quando não há local carregado
}) {
  const [st, setSt] = useState<LocalState | null>(null);
  const [tab, setTab] = useState<SubTab>("Modelos");
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const s = await api.get<LocalState>("/local");
      setSt(s);
      props.onRunning(s.server.running, s.server.alias || "");
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, []);

  // A aba Vídeo sem modelo manda abrir aqui direto em Baixar.
  useEffect(() => {
    const abrir = (e: Event) => {
      const aba = (e as CustomEvent<SubTab>).detail;
      if (SUBTABS.includes(aba)) setTab(aba);
    };
    window.addEventListener("forja:ia-local", abrir);
    return () => window.removeEventListener("forja:ia-local", abrir);
  }, []);

  if (!st) return <div className="p-3 text-xs text-muted">{error || "Carregando…"}</div>;

  return (
    <div className="flex h-full flex-col text-xs">
      <div className="flex shrink-0 gap-1 border-b border-line px-3 py-1.5">
        {SUBTABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-full px-2.5 py-0.5 ${tab === t ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
        {/* Erro só sai daqui quando a pessoa fecha: antes sumia no poll seguinte, sem dar tempo de ler. */}
        {error && <Erro texto={error} onClose={() => setError("")} />}
        {st.server.error?.message && <ErroDeCarga erro={st.server.error} onDone={refresh} />}
        {tab !== "Inferência" && (
          <Runtime st={st} kind={tab === "Imagem" || tab === "Vídeo" ? "sd" : "llama"} onDone={refresh} onError={setError} />
        )}
        {tab === "Vídeo" && <Runtime st={st} kind="ffmpeg" onDone={refresh} onError={setError} />}
        <Jobs jobs={st.jobs} onDone={refresh} />
        {tab === "Modelos" && <Models st={st} onDone={refresh} onError={setError} />}
        {tab === "Inferência" && <Inferencia st={st} chatModel={props.chatModel} onError={setError} />}
        {tab === "Baixar" && <Downloader st={st} onDone={refresh} onError={setError} />}
        {tab === "Imagem" && <ImageTab />}
        {tab === "Vídeo" && <VideoTab st={st} onDone={refresh} onError={setError} />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- avisos

function Erro(props: { texto: string; onClose: () => void }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-red-900/60 bg-red-950/40 p-2 text-red-300">
      <p className="min-w-0 flex-1 whitespace-pre-wrap">{props.texto}</p>
      <button onClick={props.onClose} title="Fechar" className="shrink-0 hover:text-white">
        <X className="size-3.5" />
      </button>
    </div>
  );
}

/** Falha da última carga: fica até a pessoa limpar (com o log junto), não some sozinha. */
function ErroDeCarga(props: { erro: { message: string; log: string; path: string }; onDone: () => void }) {
  const [aberto, setAberto] = useState(false);
  return (
    <div className="rounded-lg border border-red-900/60 bg-red-950/40 p-2 text-red-300">
      <div className="flex items-start gap-2">
        <p className="min-w-0 flex-1 whitespace-pre-wrap">{props.erro.message}</p>
        <button
          title="Limpar o erro e o log"
          className="shrink-0 hover:text-white"
          onClick={() => api.post("/local/log/clear").then(props.onDone)}
        >
          <Trash className="size-3.5" />
        </button>
      </div>
      <button className="mt-1 underline hover:text-white" onClick={() => setAberto(!aberto)}>
        {aberto ? "esconder log" : "ver log do llama-server"}
      </button>
      {aberto && (
        <pre className="mt-1.5 max-h-64 overflow-auto rounded-lg bg-[#0d0d0d] p-2.5 font-mono text-[11px] whitespace-pre-wrap text-muted">
          {props.erro.log || "(vazio)"}
        </pre>
      )}
    </div>
  );
}

/** Carga de modelo aparece em qualquer aba: este fica montado sempre, fora do painel IA local. */
export function LocalLoading() {
  const [loading, setLoading] = useState<LocalState["server"]["loading"] | null>(null);
  const refresh = () =>
    api
      .get<LocalState>("/local")
      .then((s) => setLoading(s.server.loading ?? null))
      .catch(() => {});
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, []);
  if (loading?.percent === undefined) return null;
  return <LoadingOverlay loading={loading} onDone={refresh} />;
}

/** Barra de carregamento no alto da janela, por cima de tudo, enquanto o modelo sobe. */
function LoadingOverlay(props: { loading: NonNullable<LocalState["server"]["loading"]>; onDone: () => void }) {
  const l = props.loading;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-3 z-50 flex justify-center">
      <div className="pointer-events-auto w-80 rounded-2xl border border-line bg-surface/95 p-3 shadow-xl backdrop-blur">
        <div className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-fg">Carregando {l.name}</span>
          <span className="shrink-0 text-muted">{l.percent}%</span>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-raised">
          <div className="h-full bg-sky-500 transition-[width] duration-500" style={{ width: `${l.percent}%` }} />
        </div>
        <div className="mt-1 flex items-center gap-2 text-faint">
          <span>
            {l.elapsed}s de ~{l.eta}s estimados
          </span>
          <button
            className="ml-auto underline hover:text-fg"
            onClick={() => api.post("/local/cancel-load").then(props.onDone)}
          >
            cancelar
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- runtime

function Runtime(props: { st: LocalState; kind: "llama" | "sd" | "ffmpeg"; onDone: () => void; onError: (e: string) => void }) {
  const info = props.st.runtimes[props.kind];
  const [backend, setBackend] = useState(info.backends.includes("vulkan") ? "vulkan" : info.backends[0]);
  const [busy, setBusy] = useState(false);
  const nome = { llama: "llama.cpp", sd: "stable-diffusion.cpp", ffmpeg: "ffmpeg" }[props.kind];

  async function install() {
    setBusy(true);
    try {
      await api.post("/local/runtime", { kind: props.kind, backend });
      props.onDone();
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (info.installed)
    return (
      <div className="flex items-center gap-2 text-faint">
        <span className="shrink-0">{nome}</span>
        <select
          className={`${campo} w-auto py-0.5`}
          value={info.chosen || info.backend}
          title="Motor em uso. Trocar não baixa nada: os instalados ficam lado a lado."
          onChange={(e) =>
            api
              .put("/local/runtime", { kind: props.kind, backend: e.target.value })
              .then(props.onDone)
              .catch((err) => props.onError(err.message))
          }
        >
          {info.available.map((a) => (
            <option key={a.backend} value={a.backend}>
              {a.backend}
            </option>
          ))}
        </select>
        <button className="underline hover:text-fg" onClick={install} disabled={busy}>
          atualizar
        </button>
        <span className="ml-auto truncate" title="Mais motores em Configurações › Runtime">
          {info.available.find((a) => a.backend === (info.chosen || info.backend))?.version}
        </span>
      </div>
    );

  return (
    <section className={card}>
      <p className="text-fg">{nome} não está instalado.</p>
      <p className="mt-1 text-muted">
        {props.kind === "ffmpeg"
          ? "É o motor da ampliação de vídeo: separa os quadros, junta de volta com o áudio e interpola o movimento (~80 MB)."
          : `Vulkan roda em qualquer GPU (NVIDIA, AMD, Intel) e é o menor download. CUDA só para NVIDIA, e baixa
        também o runtime da NVIDIA (~370 MB). CPU funciona em qualquer máquina, devagar.`}
      </p>
      <div className="mt-2.5 flex items-center gap-2">
        <select className={`${campo} w-auto ${info.backends.length < 2 ? "hidden" : ""}`} value={backend} onChange={(e) => setBackend(e.target.value)}>
          {info.backends.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>
        <button className={btnPrimary} onClick={install} disabled={busy}>
          <Download className="mr-1 inline size-3.5" />
          Baixar
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- jobs

function Jobs(props: { jobs: Job[]; onDone: () => void }) {
  if (!props.jobs.length) return null;
  return (
    <div className="flex flex-col gap-2">
      {props.jobs.map((j) => {
        const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
        return (
          <div key={j.id} className={card}>
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-fg">{j.name}</span>
              {j.status === "running" ? (
                <button
                  title="Cancelar"
                  className="text-muted hover:text-fg"
                  onClick={() => api.post(`/local/jobs/${j.id}/cancel`).then(props.onDone)}
                >
                  <X className="size-3.5" />
                </button>
              ) : (
                <>
                  <span className={j.status === "erro" ? "text-red-400" : "text-emerald-400"}>{j.status}</span>
                  <button
                    title="Tirar da lista"
                    className="shrink-0 text-muted hover:text-fg"
                    onClick={() => api.post(`/local/jobs/${j.id}/dismiss`).then(props.onDone)}
                  >
                    <X className="size-3.5" />
                  </button>
                </>
              )}
            </div>
            {j.status === "running" && (
              <>
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-raised">
                  <div className="h-full bg-emerald-500" style={{ width: `${pct}%` }} />
                </div>
                <p className="mt-1 text-faint">
                  {j.detail} {j.total ? `${size(j.done)} / ${size(j.total)} (${pct}%)` : `${j.done}/${j.total || "?"}`}
                </p>
              </>
            )}
            {j.error && <p className="mt-1 whitespace-pre-wrap text-red-400">{j.error}</p>}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- campos

function Dica({ texto }: { texto: string }) {
  if (!texto) return null;
  return (
    <span
      title={texto}
      className="grid size-3.5 shrink-0 cursor-help place-items-center rounded-full border border-line text-[9px] text-faint"
    >
      ?
    </span>
  );
}

/** Rótulo com (?) e, quando o valor saiu do padrão, destaque + lixeira para voltar — igual ao LM Studio. */
function Rotulo(props: { label: string; chave?: string; mudado?: boolean; onReset?: () => void }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={props.mudado ? "text-sky-400" : "text-muted"}>{props.label}</span>
      <Dica texto={props.chave ? AJUDA[props.chave] : ""} />
      {props.mudado && props.onReset && (
        <button onClick={props.onReset} title="Voltar ao padrão" className="text-faint hover:text-fg">
          <Trash className="size-3" />
        </button>
      )}
    </span>
  );
}

export function Field(props: {
  label: string;
  chave?: string;
  hint?: string;
  mudado?: boolean;
  onReset?: () => void;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <Rotulo label={props.label} chave={props.chave} mudado={props.mudado} onReset={props.onReset} />
      {props.children}
      {props.hint && <span className="text-faint">{props.hint}</span>}
    </label>
  );
}

export function Num(props: {
  label: string;
  chave?: string;
  value: number;
  onChange: (v: number) => void;
  hint?: string;
  max?: number;
  step?: number;
  mudado?: boolean;
  onReset?: () => void;
}) {
  return (
    <Field label={props.label} chave={props.chave} hint={props.hint} mudado={props.mudado} onReset={props.onReset}>
      <div className="flex items-center gap-2">
        {props.max ? (
          <input
            type="range"
            min={0}
            max={props.max}
            step={props.step || 1}
            value={Math.min(props.value, props.max)}
            onChange={(e) => props.onChange(Number(e.target.value))}
            className={`min-w-0 flex-1 ${props.mudado ? "accent-sky-400" : "accent-white"}`}
          />
        ) : null}
        <input
          type="number"
          className={`${props.max ? `${campo} w-24` : input} text-right ${props.mudado ? "border-sky-800 text-sky-300" : ""}`}
          value={props.value}
          onChange={(e) => props.onChange(Number(e.target.value))}
        />
      </div>
    </Field>
  );
}

function Toggle(props: {
  label: string;
  chave?: string;
  value: boolean;
  onChange: (v: boolean) => void;
  mudado?: boolean;
  onReset?: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2 py-0.5">
      <Rotulo label={props.label} chave={props.chave} mudado={props.mudado} onReset={props.onReset} />
      <button
        role="switch"
        aria-checked={props.value}
        aria-label={props.label}
        onClick={() => props.onChange(!props.value)}
        className={`h-4 w-8 shrink-0 rounded-full transition-colors ${props.value ? "bg-sky-500" : "bg-raised"}`}
      >
        <span
          className={`block size-3 rounded-full bg-white transition-transform ${props.value ? "translate-x-4" : "translate-x-0.5"}`}
        />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- aba Modelos

function Models(props: { st: LocalState; onDone: () => void; onError: (e: string) => void }) {
  const { st } = props;
  const [sel, setSel] = useState("");
  const [view, setView] = useState<ModelView | null>(null);
  const [form, setForm] = useState<LlamaParams | null>(null);
  const [adv, setAdv] = useState(false);
  const [busy, setBusy] = useState("");
  const [log, setLog] = useState("");
  const [logAberto, setLogAberto] = useState(false);
  // Acompanha a geração ao vivo e já abre na última linha, que é o que interessa num log.
  // Rolar para cima solta; voltar ao fim cola de novo — mesmo comportamento do chat.
  const { ref: caixaDoLog, fim: fimDoLog, onScroll: seguirLog, colar: colarLog } = useStickyBottom<HTMLPreElement>([log]);

  useEffect(() => {
    if (!logAberto) return;
    let vivo = true;
    const puxar = () =>
      api
        .get<{ log: string }>("/local/log?tail=400")
        .then((r) => vivo && setLog(r.log))
        .catch(() => {});
    puxar();
    colarLog();  // abrir sempre mostra o fim, não o começo
    const t = setInterval(puxar, 1500);
    return () => {
      vivo = false;
      clearInterval(t);
    };
  }, [logAberto, colarLog]);
  const pedido = useRef(0);

  /** Padrões + estimativa vêm do backend (ele lê o gguf e o --help do binário). */
  async function consultar(path: string, params?: LlamaParams | null) {
    const meu = ++pedido.current;
    try {
      const v = await api.post<ModelView>("/local/model", { path, params: params || {} });
      if (meu !== pedido.current) return; // chegou fora de ordem: vale sempre a última consulta
      setView(v);
      if (!params) setForm(v.params);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  // Arrastar o slider dispararia um POST por pixel; 350 ms depois de parar já basta para a estimativa.
  useEffect(() => {
    if (!sel || !form) return;
    const t = setTimeout(() => consultar(sel, form), 350);
    return () => clearTimeout(t);
  }, [sel, form]);

  function pick(m: LocalModel) {
    const novo = m.path === sel ? "" : m.path;
    setSel(novo);
    setForm(null);
    setView(null);
    if (novo) consultar(novo);
  }

  const set = <K extends keyof LlamaParams>(k: K, v: LlamaParams[K]) => setForm((f) => f && { ...f, [k]: v });
  const reset = (k: keyof LlamaParams) => view && setForm((f) => (f ? ({ ...f, [k]: view.defaults[k] } as LlamaParams) : f));
  const mudou = (k: keyof LlamaParams) => !!view && !!form && form[k] !== view.defaults[k];

  async function apagar(m: LocalModel) {
    // Apaga de verdade, sem lixeira: modelo tem dezenas de GB e quem apaga quer o espaço de volta.
    try {
      await api.post("/local/model/delete", { path: m.path });
      if (sel === m.path) {
        setSel("");
        setForm(null);
        setView(null);
      }
      props.onDone();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function act(what: "load" | "params") {
    if (!form || !sel) return;
    setBusy(what === "load" ? "Carregando…" : "Salvando…");
    try {
      if (what === "load") await api.post("/local/load", { path: sel, params: form });
      else await api.put("/local/params", { path: sel, params: form });
      props.onDone();
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setBusy("");
      consultar(sel, form);
    }
  }

  const info = view?.info;
  const est = view?.estimate;

  return (
    <>
      {st.server.running && (
        <section className={`${card} border-emerald-800/60`}>
          <div className="flex items-center gap-2">
            <span className="size-1.5 rounded-full bg-emerald-400" />
            <span className="min-w-0 flex-1 truncate font-medium text-fg">{st.server.alias}</span>
            <button className={btn} onClick={() => api.post("/local/unload").then(props.onDone)} title="Descarregar da memória">
              <Square className="mr-1 inline size-3" />
              Descarregar
            </button>
          </div>
          <p className="mt-1 text-faint">
            contexto {st.server.ctx?.toLocaleString("pt-BR")} · porta {st.server.port} · pid {st.server.pid} ·{" "}
            {Math.floor((st.server.uptime || 0) / 60)} min{st.server.vision ? " · com visão" : ""}
          </p>
          {st.server.vision_lenta && (
            <p className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-amber-200/90">
              {st.server.vision_lenta}
            </p>
          )}
          <p className="mt-1 text-faint">Escolha o provedor "IA local" no seletor de modelo do chat.</p>
          <button
            className="mt-1 text-faint underline hover:text-fg"
            onClick={() => setLogAberto((v) => !v)}
          >
            {logAberto ? "esconder log" : "ver log"}
          </button>
        </section>
      )}

      {logAberto && (
        <div className="relative">
          <button
            title="Apagar o log"
            className="absolute top-1.5 right-1.5 z-10 text-faint hover:text-red-400"
            onClick={() => api.post("/local/log/clear").then(() => { setLog(""); props.onDone(); })}
          >
            <Trash className="size-3.5" />
          </button>
          <pre
            ref={caixaDoLog}
            onScroll={seguirLog}
            className="max-h-64 overflow-auto rounded-lg bg-[#0d0d0d] p-2.5 pr-8 font-mono text-[11px] whitespace-pre-wrap text-muted"
          >
            {log || "(vazio)"}
            <div ref={fimDoLog} />
          </pre>
        </div>
      )}

      <section className={card}>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-fg">Modelos ({st.models.length})</span>
          <span className="text-faint">{st.dirs.length} pasta(s)</span>
        </div>
        {!st.models.length && (
          <p className="text-muted">Nenhum .gguf encontrado. Baixe um na aba Baixar, ou aponte a pasta onde já tem modelos.</p>
        )}
        <div className="flex flex-col">
          {st.models.map((m) => (
            <div
              key={m.path}
              className={`flex items-center gap-2 rounded-lg px-2 py-1.5 ${sel === m.path ? "bg-raised" : "hover:bg-raised"}`}
            >
              <button onClick={() => pick(m)} title={m.path} className="min-w-0 flex-1 text-left">
                <span className="block truncate text-fg">{m.name}</span>
                {st.dirs.length > 1 && <span className="block truncate text-faint">{m.folder}</span>}
              </button>
              {st.server.path === m.path && <span className="size-1.5 shrink-0 rounded-full bg-emerald-400" />}
              <span className="shrink-0 text-faint">
                {size(m.size)}
                {m.shards > 1 ? ` · ${m.shards} partes` : ""}
              </span>
              <Confirma
                rotulo={<Trash className="size-3.5" />}
                pergunta="Apagar do disco? Não dá para desfazer"
                titulo={`Apagar do disco: ${m.path}`}
                className="shrink-0 text-faint hover:text-red-400"
                onSim={() => void apagar(m)}
              />
            </div>
          ))}
        </div>
      </section>

      <ModelosDeImagem st={st} onDone={props.onDone} onError={props.onError} />
      {/* também na aba Vídeo: aqui é "tudo o que está instalado", lá é tudo do vídeo */}
      <ModelosDeImagem st={st} onDone={props.onDone} onError={props.onError} video />
      <ListaArquivos
        titulo="LoRAs"
        dica="aplicadas por cima de um modelo de vídeo"
        itens={st.loras.map((l) => ({ ...l, extra: l.passos ? `${l.passos} passos` : l.dim ? `dim ${l.dim}` : "" }))}
        dirs={st.dirs.length}
        onDone={props.onDone}
        onError={props.onError}
      />
      <ListaArquivos
        titulo="Modelos de ampliação"
        dica="ESRGAN e SeedVR2, usados em Ampliar (imagem e vídeo)"
        itens={st.ampliadores ?? []}
        dirs={st.dirs.length}
        onDone={props.onDone}
        onError={props.onError}
      />

      {sel && form && view && (
        <section className={card}>
          <p className="truncate font-medium text-fg">{view.path.split(/[\\/]/).pop()}</p>
          {info?.arch && (
            <p className="mt-0.5 text-faint">
              {info.arch} · {info.n_layer} camadas{info.n_expert ? ` · ${info.n_expert} especialistas` : ""} · {size(info.size)}
            </p>
          )}

          {est?.ok && (
            <div className="mt-2.5 rounded-xl border border-line bg-raised/60 p-2.5">
              <div className="flex items-center gap-1.5">
                <span className="flex-1 text-muted">Uso estimado de memória</span>
                <Dica texto="Soma os pesos que sobem para a GPU, o cache KV e os buffers de cálculo, do jeito que o llama.cpp divide. É estimativa: o número exato aparece no log depois de carregar." />
              </div>
              <div className="mt-1.5 flex gap-2">
                <span className="rounded-lg bg-surface px-2 py-1 text-muted">
                  GPU <span className="ml-1 font-medium text-fg">{gb(est.gpu)}</span>
                </span>
                <span className="rounded-lg bg-surface px-2 py-1 text-muted">
                  Total <span className="ml-1 font-medium text-fg">{gb(est.total)}</span>
                </span>
              </div>
              <p className="mt-1.5 text-faint">
                pesos {gb(est.weights_gpu)} na GPU + {gb(est.weights_cpu)} na RAM · cache KV {gb(est.kv)} ({gb(est.kv_gpu)}{" "}
                na GPU) · {est.layers_gpu}/{est.n_layer} camadas na GPU
              </p>
            </div>
          )}

          <div className="mt-3 flex flex-col gap-2.5">
            <Num
              label="Tamanho do contexto"
              chave="ctx"
              value={form.ctx}
              max={info?.ctx_train || undefined}
              step={1024}
              onChange={(v) => set("ctx", v)}
              mudado={mudou("ctx")}
              onReset={() => reset("ctx")}
              hint={info?.ctx_train ? `O modelo suporta até ${info.ctx_train.toLocaleString("pt-BR")} tokens.` : undefined}
            />
            <Num
              label="Camadas na GPU"
              chave="ngl"
              value={form.ngl}
              max={info?.n_layer || 999}
              onChange={(v) => set("ngl", v)}
              mudado={mudou("ngl")}
              onReset={() => reset("ngl")}
              hint={info?.n_layer ? `${info.n_layer} = todas.` : undefined}
            />
            <Toggle
              label="Flash Attention"
              chave="flash_attn"
              value={form.flash_attn}
              onChange={(v) => set("flash_attn", v)}
              mudado={mudou("flash_attn")}
              onReset={() => reset("flash_attn")}
            />
            <div className="grid grid-cols-2 gap-2">
              <Field label="Cache K" chave="cache_type_k" mudado={mudou("cache_type_k")} onReset={() => reset("cache_type_k")}>
                <select className={input} value={form.cache_type_k} onChange={(e) => set("cache_type_k", e.target.value)}>
                  {CACHE_TYPES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </Field>
              <Field label="Cache V" chave="cache_type_v" mudado={mudou("cache_type_v")} onReset={() => reset("cache_type_v")}>
                <select className={input} value={form.cache_type_v} onChange={(e) => set("cache_type_v", e.target.value)}>
                  {CACHE_TYPES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </Field>
            </div>
            <Num
              label="Threads da CPU"
              chave="threads"
              value={form.threads}
              onChange={(v) => set("threads", v)}
              mudado={mudou("threads")}
              onReset={() => reset("threads")}
              hint="0 = automático"
            />

            <button className="mt-1 self-start text-faint underline hover:text-fg" onClick={() => setAdv(!adv)}>
              {adv ? "esconder avançado" : "avançado"}
            </button>
            {adv && (
              <div className="flex flex-col gap-2.5 border-t border-line pt-2.5">
                <Num label="Lote de avaliação" chave="batch" value={form.batch} onChange={(v) => set("batch", v)} mudado={mudou("batch")} onReset={() => reset("batch")} />
                <Num label="Lote físico" chave="ubatch" value={form.ubatch} onChange={(v) => set("ubatch", v)} mudado={mudou("ubatch")} onReset={() => reset("ubatch")} />
                <Num label="Previsões simultâneas" chave="parallel" value={form.parallel} onChange={(v) => set("parallel", v)} mudado={mudou("parallel")} onReset={() => reset("parallel")} hint="0 = automático" />
                <Num label="Checkpoints de contexto" chave="ctx_checkpoints" value={form.ctx_checkpoints} onChange={(v) => set("ctx_checkpoints", v)} mudado={mudou("ctx_checkpoints")} onReset={() => reset("ctx_checkpoints")} />
                <Num label="Camadas MoE na CPU" chave="n_cpu_moe" value={form.n_cpu_moe} max={info?.n_layer || undefined} onChange={(v) => set("n_cpu_moe", v)} mudado={mudou("n_cpu_moe")} onReset={() => reset("n_cpu_moe")} hint="0 = nenhuma" />
                <Num label="Número de especialistas" chave="n_expert" value={form.n_expert} onChange={(v) => set("n_expert", v)} mudado={mudou("n_expert")} onReset={() => reset("n_expert")} />
                <Num label="Semente" chave="seed" value={form.seed} onChange={(v) => set("seed", v)} mudado={mudou("seed")} onReset={() => reset("seed")} hint="0 = aleatória" />
                <Num label="RoPE freq. base" chave="rope_freq_base" value={form.rope_freq_base} onChange={(v) => set("rope_freq_base", v)} mudado={mudou("rope_freq_base")} onReset={() => reset("rope_freq_base")} hint="0 = automático" />
                <Num label="RoPE escala" chave="rope_freq_scale" value={form.rope_freq_scale} onChange={(v) => set("rope_freq_scale", v)} mudado={mudou("rope_freq_scale")} onReset={() => reset("rope_freq_scale")} hint="0 = automático" />
                <Toggle label="Ajustar para caber na memória" chave="fit" value={form.fit} onChange={(v) => set("fit", v)} mudado={mudou("fit")} onReset={() => reset("fit")} />
                <Toggle label="Cache KV unificado" chave="kv_unified" value={form.kv_unified} onChange={(v) => set("kv_unified", v)} mudado={mudou("kv_unified")} onReset={() => reset("kv_unified")} />
                <Toggle label="Descarregar cache KV para a GPU" chave="no_kv_offload" value={!form.no_kv_offload} onChange={(v) => set("no_kv_offload", !v)} mudado={mudou("no_kv_offload")} onReset={() => reset("no_kv_offload")} />
                <Toggle label="Manter modelo na memória" chave="mlock" value={form.mlock} onChange={(v) => set("mlock", v)} mudado={mudou("mlock")} onReset={() => reset("mlock")} />
                <Toggle label="Tentar mmap()" chave="mmap" value={form.mmap} onChange={(v) => set("mmap", v)} mudado={mudou("mmap")} onReset={() => reset("mmap")} />
                <Field label="Projetor multimodal (mmproj)" chave="mmproj" mudado={mudou("mmproj")} onReset={() => reset("mmproj")}>
                  <input className={input} value={form.mmproj} onChange={(e) => set("mmproj", e.target.value)} placeholder="opcional" />
                </Field>
              </div>
            )}
          </div>

          <div className="mt-3 flex items-center gap-2">
            <button className={btnPrimary} onClick={() => act("load")} disabled={!!busy || st.image_busy}>
              Carregar
            </button>
            <button className={btn} onClick={() => act("params")} disabled={!!busy}>
              Salvar
            </button>
            {!!view.overrides.length && <span className="text-faint">{view.overrides.length} fora do padrão</span>}
            {busy && <span className="text-muted">{busy}</span>}
            {st.image_busy && <span className="text-amber-300">gerando imagem — a VRAM está ocupada</span>}
          </div>
        </section>
      )}
    </>
  );
}

const PREVIA: Record<string, string> = { proj: "projeção", tae: "TAESD", vae: "VAE completo" };

const ROTULO: Record<string, string> = {
  vae: "VAE",
  llm: "Codificador LLM",
  llm_vision: "Visão do LLM (mmproj)",
  clip_l: "clip_l",
  t5xxl: "t5xxl",
  taesd: "TAESD",
  clip_vision: "CLIP Vision",
  high_noise_model: "Modelo HighNoise",
};

/** Modelos de difusão que estão nas pastas. Não têm "Carregar": o sd.cpp sobe e desce a cada imagem —
 *  o que dá para guardar aqui são os ajustes de cada um (o Flux não quer o mesmo CFG que o SD 1.5). */
/** Arquivos que não têm ajustes próprios (LoRA, ESRGAN): nome, pasta, tamanho e apagar. */
function ListaArquivos(props: {
  titulo: string;
  dica: string;
  itens: { path: string; name: string; folder?: string; size: number; extra?: string }[];
  dirs: number;
  onDone: () => void;
  onError: (e: string) => void;
}) {
  if (!props.itens.length) return null;
  async function apagar(path: string) {
    try {
      await api.post("/local/model/delete", { path });
      props.onDone();
    } catch (e: any) {
      props.onError(e.message);
    }
  }
  return (
    <section className={card}>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-fg">{props.titulo} ({props.itens.length})</span>
        <span className="text-faint">{props.dica}</span>
      </div>
      <div className="flex flex-col">
        {props.itens.map((m) => (
          <div key={m.path} className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-raised" title={m.path}>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-fg">{m.name}</span>
              {props.dirs > 1 && m.folder && <span className="block truncate text-faint">{m.folder}</span>}
            </span>
            {m.extra && <span className="shrink-0 text-faint">{m.extra}</span>}
            <span className="shrink-0 text-faint">{size(m.size)}</span>
            <Confirma
              rotulo={<Trash className="size-3.5" />}
              pergunta="Apagar do disco? Não dá para desfazer"
              titulo={`Apagar do disco: ${m.path}`}
              className="shrink-0 text-faint hover:text-red-400"
              onSim={() => void apagar(m.path)}
            />
          </div>
        ))}
      </div>
    </section>
  );
}

function ModelosDeImagem(props: { st: LocalState; onDone: () => void; onError: (e: string) => void; video?: boolean }) {
  const [sel, setSel] = useState("");
  const [form, setForm] = useState<ImageParams | null>(null);
  const [salvo, setSalvo] = useState("");
  const lista = props.video ? props.st.video_models : props.st.image_models;
  const atual = lista.find((m) => m.path === sel);
  // VAE/codificador/mmproj achados perto do modelo (busca no disco, só ao abrir os ajustes).
  // Guardado com o caminho buscado: trocar de modelo não mostra os achados do anterior.
  const [busca, setBusca] = useState<{ path: string; r: Record<string, string[]> }>({ path: "", r: {} });
  const achados = busca.path === sel ? busca.r : {};
  const temReq = !!atual?.req;
  useEffect(() => {
    if (!sel || !temReq) return;
    let vivo = true;
    api
      .get<Record<string, string[]>>(`/local/image/achados?path=${encodeURIComponent(sel)}`)
      .then((r) => vivo && setBusca({ path: sel, r }))
      .catch(() => {}); // sem sugestão, o campo continua valendo
    return () => {
      vivo = false;
    };
  }, [sel, temReq]);
  type Arquivo = "vae" | "llm" | "llm_vision" | "clip_l" | "t5xxl" | "taesd" | "clip_vision" | "high_noise_model";
  const achadoDe = (k: string) => {
    const f = achados[k]?.[0];
    return f && form && form[k as Arquivo] !== f ? f : "";
  };
  const usarAchados = () =>
    setForm((f) => {
      if (!f) return f;
      const novo = { ...f };
      for (const [k, lista] of Object.entries(achados)) if (lista[0] && !novo[k as Arquivo]) novo[k as Arquivo] = lista[0];
      return novo;
    });
  const nome = (p: string) => p.split(/[\\/]/).pop();
  // função e não componente: um componente criado no render remonta a cada desenho (react-hooks/static-components)
  const achado = (k: string) => {
    const f = achadoDe(k);
    if (!f) return null;
    return (
      <span className="mt-0.5 flex items-center gap-1.5 pl-4 text-faint" title={f}>
        encontrado: <span className="truncate text-muted">{nome(f)}</span>
        <button className="shrink-0 underline text-fg" onClick={() => set(k as Arquivo, f)}>
          Usar
        </button>
      </span>
    );
  };

  if (!lista.length) return null;
  const set = <K extends keyof ImageParams>(k: K, v: ImageParams[K]) => {
    setForm((f) => f && { ...f, [k]: v });
    setSalvo("");
  };

  async function apagar(m: LocalModel) {
    try {
      await api.post("/local/model/delete", { path: m.path });
      if (sel === m.path) setSel("");
      props.onDone();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function salvar() {
    try {
      await api.put("/local/image/model", { path: sel, params: form });
      setSalvo("Salvo.");
      props.onDone();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  return (
    <section className={card}>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-fg">{props.video ? "Modelos de vídeo" : "Modelos de imagem"} ({lista.length})</span>
        <span className="text-faint">{props.video ? "usados na aba Vídeo" : "usados na aba Imagem"}</span>
      </div>
      <div className="flex flex-col">
        {lista.map((m) => (
          <div
            key={m.path}
            className={`flex items-center gap-2 rounded-lg px-2 py-1.5 ${sel === m.path ? "bg-raised" : "hover:bg-raised"}`}
          >
            <button
              className="min-w-0 flex-1 text-left"
              title={m.path}
              onClick={() => {
                const novo = sel === m.path ? "" : m.path;
                setSel(novo);
                setForm(novo ? m.params ?? null : null);
                setSalvo("");
              }}
            >
              <span className="block truncate text-fg">{m.name}</span>
              {props.st.dirs.length > 1 && <span className="block truncate text-faint">{m.folder}</span>}
            </button>
            {!!m.falta?.length && (
              <span className="shrink-0 text-amber-400" title={`${m.req?.nome} precisa de arquivos à parte — abra os ajustes`}>
                falta arquivo
              </span>
            )}
            {(props.video ? props.st.video.model : props.st.image.model) === m.path && <span className="shrink-0 text-faint">em uso</span>}
            <span className="shrink-0 text-faint">{size(m.size)}</span>
            <Confirma
              rotulo={<Trash className="size-3.5" />}
              pergunta="Apagar do disco? Não dá para desfazer"
              titulo={`Apagar do disco: ${m.path}`}
              className="shrink-0 text-faint hover:text-red-400"
              onSim={() => void apagar(m)}
            />
          </div>
        ))}
      </div>

      {sel && form && (
        <div className="mt-3 border-t border-line pt-3">
          <p className="mb-2 text-muted">Ajustes deste modelo</p>
          {atual?.req && (
            <div className={`mb-3 rounded-lg border p-2.5 ${atual.falta?.length ? "border-amber-500/40 bg-amber-500/5" : "border-line"}`}>
              <p className="text-fg">
                {atual.req.nome}: este arquivo é só o modelo de difusão. Ele precisa também de:
              </p>
              <ul className="mt-1.5 flex flex-col gap-1">
                {Object.entries(atual.req.precisa).map(([k, [oque, link]]) => (
                  <li key={k}>
                    <span className={atual.falta?.includes(k) ? "text-amber-400" : "text-emerald-400"}>
                      {atual.falta?.includes(k) ? "✗" : "✓"} {ROTULO[k] ?? k}
                    </span>{" "}
                    <span className="text-muted">— {oque}</span>{" "}
                    <a href={link} target="_blank" rel="noreferrer" className="underline text-faint">baixar</a>
                    {achado(k)}
                  </li>
                ))}
                {Object.entries(atual.req.edita ?? {}).map(([k, [oque, link]]) => (
                  <li key={k}>
                    <span className={atual.falta_edicao?.includes(k) ? "text-faint" : "text-emerald-400"}>
                      {atual.falta_edicao?.includes(k) ? "○" : "✓"} {ROTULO[k] ?? k}
                    </span>{" "}
                    <span className="text-muted">— para editar imagens: {oque}</span>{" "}
                    <a href={link} target="_blank" rel="noreferrer" className="underline text-faint">baixar</a>
                    {achado(k)}
                  </li>
                ))}
              </ul>
              <p className="mt-1.5 text-faint">
                O Forja procura esses arquivos perto do modelo; o que não achar, cole o caminho nos campos abaixo.
                Depois de salvos, eles saem da lista de modelos de imagem.{" "}
                <a href={atual.req.doc} target="_blank" rel="noreferrer" className="underline">Guia do sd.cpp</a>
              </p>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                {Object.keys(achados).some((k) => achadoDe(k)) && (
                  <button className="underline text-fg" onClick={usarAchados} title="Preenche os campos vazios com os arquivos encontrados">
                    Usar os encontrados
                  </button>
                )}
                <button
                  className="underline text-fg"
                  onClick={() => setForm((f) => f && { ...f, ...atual.req!.sugere })}
                  title={Object.entries(atual.req.sugere).map(([k, v]) => `${k}: ${v}`).join(", ")}
                >
                  Aplicar ajustes sugeridos
                </button>
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-2">
            <Num label="Passos" value={form.steps} onChange={(v) => set("steps", v)} />
            <Num label="CFG" value={form.cfg} onChange={(v) => set("cfg", v)} />
            <Num label="Largura" value={form.width} onChange={(v) => set("width", v)} />
            <Num label="Altura" value={form.height} onChange={(v) => set("height", v)} />
            {props.video && (
              <>
                <Num label="Quadros" value={form.frames} onChange={(v) => set("frames", Math.max(1, Math.round((v - 1) / 4)) * 4 + 1)} step={4} hint="4k+1" />
                <Num label="FPS" value={form.fps} onChange={(v) => set("fps", v)} />
                <Num label="Flow shift" value={form.flow_shift} onChange={(v) => set("flow_shift", v)} step={0.5} hint="0 = automático" />
              </>
            )}
          </div>
          {props.video ? (
            <CamposVideo form={form} set={set} req={atual?.req} achado={achado} variante={atual?.variante} />
          ) : (
          <div className="mt-2.5 flex flex-col gap-2.5">
            <Field label="Amostrador">
              <select className={input} value={form.sampler} onChange={(e) => set("sampler", e.target.value)}>
                {SAMPLERS.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </Field>
            <Field label="Negativo padrão">
              <input className={input} value={form.negative} onChange={(e) => set("negative", e.target.value)} />
            </Field>
            <Field label="VAE" hint="Opcional. Alguns modelos precisam do VAE em arquivo separado.">
              <input className={input} value={form.vae} onChange={(e) => set("vae", e.target.value)} placeholder="opcional" />
            </Field>
            <Field label="clip_l / t5xxl" hint="Só para Flux e SD3, que trazem os codificadores de texto à parte.">
              <div className="grid grid-cols-2 gap-2">
                <input className={input} value={form.clip_l} onChange={(e) => set("clip_l", e.target.value)} placeholder="clip_l" />
                <input className={input} value={form.t5xxl} onChange={(e) => set("t5xxl", e.target.value)} placeholder="t5xxl" />
              </div>
            </Field>
            <Field label="Codificador LLM" hint="Qwen-Image: Qwen2.5-VL-7B (1.0) ou Qwen3-VL-8B (2.1), em GGUF ou safetensors.">
              <input className={input} value={form.llm ?? ""} onChange={(e) => set("llm", e.target.value)} placeholder="opcional" />
            </Field>
            <label className="flex items-center gap-2 text-muted" title="--offload-to-cpu: pesos na RAM, sobem à GPU sob demanda">
              <input type="checkbox" checked={!!form.offload} onChange={(e) => set("offload", e.target.checked)} />
              Pesos na RAM (modelo maior que a VRAM)
            </label>
            <label className="flex items-center gap-2 text-muted" title="--diffusion-fa: bem menos memória na atenção">
              <input type="checkbox" checked={!!form.flash_attn} onChange={(e) => set("flash_attn", e.target.checked)} />
              Flash attention na difusão
            </label>
            <label className="flex items-center gap-2 text-muted" title="--vae-tiling: decodifica em blocos, sem pico de memória no fim">
              <input type="checkbox" checked={!!form.vae_tiling} onChange={(e) => set("vae_tiling", e.target.checked)} />
              VAE em blocos (evita estourar a VRAM no fim)
            </label>
            <div className="flex items-center gap-2 text-muted" title="--backend te=cpu: libera a VRAM para a difusão. Acelera a geração; na edição fica mais lento, porque a leitura da imagem de referência também vai para a CPU">
              <label className="flex shrink-0 items-center gap-2 whitespace-nowrap">
                <input type="checkbox" checked={!!form.te_cpu} onChange={(e) => set("te_cpu", e.target.checked ? "gerar" : "")} />
                Codificador de texto na CPU
              </label>
              {!!form.te_cpu && (
                <select className={`${input} w-auto py-0.5`} value={form.te_cpu} onChange={(e) => set("te_cpu", e.target.value as ImageParams["te_cpu"])}>
                  <option value="gerar">na geração</option>
                  <option value="editar">na edição</option>
                  <option value="sempre">nas duas</option>
                </select>
              )}
            </div>
            <Field label="Visão do LLM (mmproj)" hint="Só para editar imagem com codificador em GGUF (Qwen-Image 2.1).">
              <input className={input} value={form.llm_vision ?? ""} onChange={(e) => set("llm_vision", e.target.value)} placeholder="opcional" />
            </Field>
            <Field
              label="Prévia enquanto gera"
              hint={`O card da imagem mostra o passo atual. Automática usa o TAESD se houver o arquivo; senão a projeção, e o VAE nos modelos sem projeção (o Forja aprende isso na primeira imagem).${
                atual?.previa_auto ? ` Neste modelo: ${PREVIA[atual.previa_auto]}.` : ""}`}
            >
              <select className={input} value={form.preview ?? ""} onChange={(e) => set("preview", e.target.value as ImageParams["preview"])}>
                <option value="">Automática</option>
                <option value="none">Nenhuma</option>
                <option value="proj">Projeção do latente (de graça, cores aproximadas; nem todo modelo tem)</option>
                <option value="tae">TAESD (rápida e fiel, precisa do arquivo)</option>
                <option value="vae">VAE completo (fiel, deixa cada passo mais lento)</option>
              </select>
            </Field>
            {(form.preview === "tae" || (!form.preview && achadoDe("taesd"))) && (
              <Field label="TAESD" hint="O decodificador pequeno do modelo: taesd (SD 1.5), taesdxl (SDXL), taef1 (Flux). Sem ele, gera sem prévia.">
                <input className={input} value={form.taesd ?? ""} onChange={(e) => set("taesd", e.target.value)} placeholder="caminho do .safetensors" />
                {achado("taesd")}
              </Field>
            )}
          </div>
          )}
          <div className="mt-3 flex items-center gap-2">
            <button className={btnPrimary} onClick={salvar}>
              Salvar
            </button>
            {salvo && <span className="text-emerald-400">{salvo}</span>}
            <span className="text-faint">Valem quando este modelo estiver escolhido na aba {props.video ? "Vídeo" : "Imagem"}.</span>
          </div>
        </div>
      )}
    </section>
  );
}

const VARIANTES_WAN: [string, string][] = [
  ["", "Pelo nome do arquivo"], ["wan21_t2v", "Wan2.1 T2V"], ["wan21_i2v", "Wan2.1 I2V"], ["wan21_flf2v", "Wan2.1 FLF2V"],
  ["wan21_vace", "Wan2.1 VACE"], ["wan22_ti2v", "Wan2.2 TI2V 5B"], ["wan22_a14b_t2v", "Wan2.2 T2V A14B"],
  ["wan22_a14b_i2v", "Wan2.2 I2V A14B"],
];

/** Os arquivos e ligações de memória de um modelo de vídeo (Wan). Os caminhos se preenchem sozinhos
 *  quando o kit baixa tudo junto; aqui é para conferir e trocar. */
function CamposVideo(props: {
  form: ImageParams;
  set: <K extends keyof ImageParams>(k: K, v: ImageParams[K]) => void;
  req?: LocalModel["req"];
  variante?: string;
  achado: (k: string) => React.ReactNode;
}) {
  const { form, set } = props;
  const pede = (k: string) => !!props.req?.precisa?.[k];
  const caminho = (k: "vae" | "t5xxl" | "clip_vision" | "high_noise_model", rotulo: string, dica: string) => (
    <Field label={rotulo} hint={dica}>
      <input className={input} value={form[k] ?? ""} onChange={(e) => set(k, e.target.value)} placeholder="caminho do arquivo" spellCheck={false} />
      {props.achado(k)}
    </Field>
  );
  return (
    <div className="mt-2.5 flex flex-col gap-2.5">
      <Field label="Amostrador">
        <select className={input} value={form.sampler} onChange={(e) => set("sampler", e.target.value)}>
          {SAMPLERS.map((s) => <option key={s}>{s}</option>)}
        </select>
      </Field>
      <Field label="Negativo padrão">
        <input className={input} value={form.negative} onChange={(e) => set("negative", e.target.value)} />
      </Field>
      <Field label="Variante" hint={`Qual Wan é este arquivo: decide os arquivos que ele pede e os modos. Detectada: ${props.variante ?? "?"}.`}>
        <select className={input} value={form.variante ?? ""} onChange={(e) => set("variante", e.target.value)}>
          {VARIANTES_WAN.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
        </select>
      </Field>
      {caminho("vae", "VAE", "wan_2.1_vae (Wan2.1 e A14B) ou wan2.2_vae (TI2V 5B).")}
      {caminho("t5xxl", "Codificador de texto (umt5-xxl)", "O mesmo para todos os Wan; GGUF Q8 é o equilíbrio.")}
      {pede("clip_vision") && caminho("clip_vision", "CLIP Vision", "clip_vision_h: o Wan2.1 I2V e o FLF2V leem a imagem com ele.")}
      {pede("high_noise_model") && (
        <>
          {caminho("high_noise_model", "Modelo HighNoise", "A outra metade do A14B, da mesma quantização.")}
          <div className="grid grid-cols-2 gap-2">
            <Num label="Passos (alto ruído)" value={form.high_noise_steps} onChange={(v) => set("high_noise_steps", v)} hint="-1 = automático" />
            <Num label="CFG (alto ruído)" value={form.high_noise_cfg} onChange={(v) => set("high_noise_cfg", v)} step={0.5} hint="0 = o mesmo" />
          </div>
        </>
      )}
      <label className="flex items-center gap-2 text-muted" title="--offload-to-cpu: pesos na RAM, sobem à GPU sob demanda">
        <input type="checkbox" checked={!!form.offload} onChange={(e) => set("offload", e.target.checked)} />
        Pesos na RAM (modelo maior que a VRAM)
      </label>
      <label className="flex items-center gap-2 text-muted" title="--diffusion-fa: bem menos memória na atenção">
        <input type="checkbox" checked={!!form.flash_attn} onChange={(e) => set("flash_attn", e.target.checked)} />
        Flash attention na difusão
      </label>
      <label className="flex items-center gap-2 text-muted" title="--vae-tiling: decodifica em blocos; com dezenas de quadros, quase obrigatório">
        <input type="checkbox" checked={!!form.vae_tiling} onChange={(e) => set("vae_tiling", e.target.checked)} />
        VAE em blocos (evita estourar a VRAM no fim)
      </label>
      <label className="flex items-center gap-2 text-muted" title="--backend te=cpu: o umt5 (6 GB) sai da VRAM; roda uma vez por vídeo">
        <input type="checkbox" checked={!!form.te_cpu} onChange={(e) => set("te_cpu", e.target.checked ? "sempre" : "")} />
        Codificador de texto na CPU
      </label>
    </div>
  );
}

// ---------------------------------------------------------------- aba Inferência

/** Amostragem por modelo, igual à aba Inference do LM Studio. Fica salva em model_settings, então vale
 *  para esse modelo em qualquer conversa — e também quando ele vier do Ollama ou do LM Studio. */
function Inferencia(props: { st: LocalState; chatModel?: string; onError: (e: string) => void }) {
  // Modelo local carregado > último local usado > o que está escolhido no chat (Ollama, LM Studio...).
  const caminho = props.st.server.running ? props.st.server.path || "" : props.st.last;
  const nome = props.st.server.running ? props.st.server.alias || "" : "";
  const [view, setView] = useState<InferenceView | null>(null);
  const [form, setForm] = useState<Inference | null>(null);
  const [salvo, setSalvo] = useState("");
  const alvo = caminho || props.chatModel || "";

  useEffect(() => {
    if (!alvo) return;
    const nomeModelo = nome || (caminho ? "" : props.chatModel || "");
    const pedido = caminho
      ? api.get<InferenceView>(`/local/inference?path=${encodeURIComponent(caminho)}&model=${encodeURIComponent(nomeModelo || caminho.split(/[\\/]/).pop()!.replace(/\.gguf$/i, ""))}`)
      : api.get<InferenceView>(`/local/inference?model=${encodeURIComponent(props.chatModel!)}`);
    pedido
      .then((v) => {
        setView(v);
        setForm(v.inference);
      })
      .catch((e) => props.onError(e.message));
  }, [alvo]);

  if (!alvo)
    return <p className="text-muted">Escolha um modelo no chat ou carregue um local para ajustar a amostragem dele.</p>;
  if (!view || !form) return <p className="text-muted">Carregando…</p>;

  const d = view.inference_defaults;
  const set = <K extends keyof Inference>(k: K, v: Inference[K]) => {
    setForm({ ...form, [k]: v });
    setSalvo("");
  };
  const reset = (k: keyof Inference) => set(k, d[k] as never);
  const mudou = (k: keyof Inference) => JSON.stringify(form[k]) !== JSON.stringify(d[k]);

  async function salvar(v: InferenceView, atual: Inference) {
    // Só o que saiu do padrão vai para o banco: se o padrão do modelo mudar, o resto acompanha.
    const fora = (Object.keys(d) as (keyof Inference)[]).filter(mudou);
    try {
      await api.put("/model-settings", {
        model: v.model,
        inference: Object.fromEntries(fora.map((k) => [k, atual[k]])),
      });
      setSalvo(fora.length ? `Salvo · ${fora.length} fora do padrão` : "Salvo · tudo no padrão");
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  return (
    <section className={card}>
      <p className="truncate font-medium text-fg">{view.model}</p>
      <p className="mt-0.5 text-faint">
        Vale para este modelo em qualquer conversa e em qualquer provedor. O padrão de cada campo é o que o próprio
        .gguf recomenda — e, quando não há arquivo local, o padrão do llama.cpp.
      </p>
      <div className="mt-3 flex flex-col gap-2.5">
        <Num label="Temperatura" chave="temperature" value={form.temperature} max={2} step={0.05}
          onChange={(v) => set("temperature", v)} mudado={mudou("temperature")} onReset={() => reset("temperature")} />
        <Num label="Top K" chave="top_k" value={form.top_k} max={200} hint="0 = desligado"
          onChange={(v) => set("top_k", v)} mudado={mudou("top_k")} onReset={() => reset("top_k")} />
        <Num label="Top P" chave="top_p" value={form.top_p} max={1} step={0.01} hint="1 = desligado"
          onChange={(v) => set("top_p", v)} mudado={mudou("top_p")} onReset={() => reset("top_p")} />
        <Num label="Min P" chave="min_p" value={form.min_p} max={1} step={0.01} hint="0 = desligado"
          onChange={(v) => set("min_p", v)} mudado={mudou("min_p")} onReset={() => reset("min_p")} />
        <Num label="Penalidade de repetição" chave="repeat_penalty" value={form.repeat_penalty} max={2} step={0.01}
          hint="1 = desligada" onChange={(v) => set("repeat_penalty", v)} mudado={mudou("repeat_penalty")}
          onReset={() => reset("repeat_penalty")} />
        <Num label="Limite da resposta (tokens)" chave="max_tokens" value={form.max_tokens} hint="0 = sem limite"
          onChange={(v) => set("max_tokens", v)} mudado={mudou("max_tokens")} onReset={() => reset("max_tokens")} />
        <Toggle label="Pensar antes de responder" chave="think" value={form.think}
          onChange={(v) => set("think", v)} mudado={mudou("think")} onReset={() => reset("think")} />
        <Num label="Teto de raciocínio (tokens)" chave="reasoning_budget" value={form.reasoning_budget}
          hint="-1 = sem teto" onChange={(v) => set("reasoning_budget", v)} mudado={mudou("reasoning_budget")}
          onReset={() => reset("reasoning_budget")} />
        <Field label="Strings de parada" chave="stop" hint="Uma por linha." mudado={mudou("stop")}
          onReset={() => reset("stop")}>
          <textarea
            className={`${input} h-16 resize-none`}
            value={form.stop.join("\n")}
            onChange={(e) => set("stop", e.target.value.split("\n").filter((s) => s.trim()))}
          />
        </Field>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button className={btnPrimary} onClick={() => salvar(view, form)}>
          Salvar
        </button>
        {salvo && <span className="text-emerald-400">{salvo}</span>}
      </div>
      <p className="mt-2 text-faint">
        Prompt de sistema, truncagem de contexto e saída estruturada do LM Studio não entram aqui: no Forja quem cuida
        disso é o agente (Configurações › Instruções, e a compactação automática de contexto).
      </p>
    </section>
  );
}

// ---------------------------------------------------------------- aba Baixar

function Downloader(props: { st: LocalState; onDone: () => void; onError: (e: string) => void }) {
  const [kind, setKind] = useState<"text" | "image" | "video" | "ampliar">("text");
  const [buscando, setBuscando] = useState(false);
  // Se a pasta salva saiu da lista (removida), cai na primeira em vez de deixar o select vazio.
  const [destino, setDestino] = useState(
    props.st.dirs.includes(props.st.download_dir) ? props.st.download_dir : props.st.dirs[0],
  );

  async function addFolder() {
    const path = window.forja ? await window.forja.pickFolder("") : prompt("Caminho da pasta com os modelos:");
    if (!path) return;
    try {
      await api.put("/local/dirs", { dirs: [...props.st.dirs.slice(1), path] });
      props.onDone();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function removeFolder(path: string) {
    await api.put("/local/dirs", { dirs: props.st.dirs.slice(1).filter((d) => d !== path) });
    if (destino === path) setDestino(props.st.dirs[0]);
    props.onDone();
  }

  function baixar(repo: string, file: string, subpasta = "") {
    api
      .post("/local/download", { repo, file, folder: destino, subpasta })
      .then(props.onDone)
      .catch((e) => props.onError(e.message));
  }

  return (
    <>
      <section className={card}>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-fg">Pastas de modelos</span>
          <button className={btn} onClick={addFolder}>
            <FolderOpen className="mr-1 inline size-3.5" />
            Adicionar
          </button>
        </div>
        {props.st.dirs.map((d, i) => (
          <div key={d} className="flex items-center gap-2 py-0.5">
            <span className="min-w-0 flex-1 truncate text-muted" title={d}>
              {d}
            </span>
            {i === 0 ? (
              <span className="shrink-0 text-faint">padrão</span>
            ) : (
              <button className="shrink-0 text-muted hover:text-fg" title="Remover" onClick={() => removeFolder(d)}>
                <Trash className="size-3.5" />
              </button>
            )}
          </div>
        ))}
        <label className="mt-2 flex items-center gap-2">
          <span className="shrink-0 text-muted">Baixar para</span>
          <select className={input} value={destino} onChange={(e) => setDestino(e.target.value)}>
            {props.st.dirs.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <p className="mt-1 text-faint">Todas as pastas são varridas; o download vai para a escolhida acima.</p>
      </section>

      <button className={btnPrimary} onClick={() => setBuscando(true)}>
        <Search className="mr-1 inline size-3.5" />
        Procurar modelos
      </button>

      <KitsVideo
        st={props.st}
        destino={destino}
        onDone={props.onDone}
        onError={props.onError}
        onProcurar={() => {
          setKind("video");
          setBuscando(true);
        }}
      />
      <KitsVideo st={props.st} destino={destino} onDone={props.onDone} onError={props.onError} auto />


      {buscando && (
        <ModelSearch
          kind={kind}
          onKind={setKind}
          hardware={props.st.hardware}
          destino={destino}
          onDownload={baixar}
          onClose={() => setBuscando(false)}
          onError={props.onError}
        />
      )}
    </>
  );
}

/** Kits do Wan: o modelo e as peças que a variante pede, num clique. O que já está no disco não baixa
 *  de novo; o selo diz se o modelo cabe na GPU desta máquina. */
function KitsVideo(props: {
  st: LocalState;
  destino: string;
  onDone: () => void;
  onError: (e: string) => void;
  onProcurar?: () => void;
  auto?: boolean; // a lista montada sozinha da busca do Hugging Face, em vez da curada
}) {
  const [kits, setKits] = useState<VideoKit[] | null>(null);
  // a automática consulta dezenas de repositórios: só quando a pessoa abre (depois fica em cache no backend)
  const [ligado, setLigado] = useState(!props.auto);
  const [vram, setVram] = useState(0); // GB da GPU que o sd.cpp usa (a integrada não conta)
  const [aberto, setAberto] = useState<string | null>(null);
  // Quantização trocada no cartão (id do kit → quant). Sem troca, vale a do backend: a maior que cabe.
  const [quants, setQuants] = useState<Record<string, string>>({});
  const baixando = props.st.jobs.filter((j) => j.kind === "modelo" && j.status === "running").map((j) => j.name);

  const carregar = useCallback(() => {
    if (!ligado) return;
    api
      .get<{ kits: VideoKit[]; vram_gb: number }>(
        `/local/video/kits?quants=${encodeURIComponent(JSON.stringify(quants))}${props.auto ? "&auto=true" : ""}`,
      )
      .then((r) => {
        setKits(r.kits);
        setVram(r.vram_gb);
      })
      .catch((e) => {
        setKits([]);
        props.onError(e.message);
      });
  }, [quants, ligado]);
  useEffect(carregar, [carregar, baixando.length]);

  // "cabe" vem do backend, pela mesma folga que escolhe a quantização (nada de conta repetida aqui)
  const cabe = (k: VideoKit) => k.opcoes.find((o) => o.quant === k.quant)?.cabe ?? null;
  const recomendado = props.auto ? undefined : kits
    ?.filter((k) => cabe(k) && k.modos.length > 1)
    .sort((a, b) => b.gb_modelo - a.gb_modelo)[0]?.id;

  async function baixar(k: VideoKit) {
    try {
      await api.post("/local/video/kit", { id: k.id, folder: props.destino, quant: k.quant });
      props.onDone();
      carregar();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  if (props.auto && !ligado)
    return (
      <section className={card}>
        <button className="flex w-full items-center gap-1.5 text-left text-fg" onClick={() => setLigado(true)} aria-expanded={false}>
          <Film className="size-3.5" /> Kits automáticos
          <ChevronDown className="ml-auto size-3.5 text-muted" />
        </button>
        <p className="mt-1 text-faint">
          Montados sozinhos a partir dos repositórios de Wan em GGUF mais baixados do Hugging Face: cada família de arquivos
          vira um kit, com as peças que a variante pede. Sem curadoria — confira o repositório antes de baixar.
        </p>
      </section>
    );

  return (
    <section className={card}>
      <div className="mb-1 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-fg">
          <Film className="size-3.5" /> {props.auto ? `Kits automáticos${kits ? ` (${kits.length})` : ""}` : "Geração de vídeo"}
        </span>
        {props.onProcurar && (
          <button className="text-muted underline hover:text-fg" onClick={props.onProcurar}>
            Procurar outros
          </button>
        )}
      </div>
      <p className="mb-2 text-faint">
        {props.auto
          ? "Da busca do Hugging Face (os repositórios de Wan em GGUF mais baixados), sem curadoria: o nome do arquivo diz a variante, e dela saem as peças. O que a lista curada já tem fica de fora."
          : "Modelos Wan, que o stable-diffusion.cpp gera em vídeo. Cada kit traz o modelo e o VAE e codificador que ele pede; os caminhos se configuram sozinhos."}
      </p>
      {!kits && <p className="text-muted">{props.auto ? "Buscando no Hugging Face…" : "Carregando…"}</p>}
      <div className="flex flex-col gap-1.5">
        {kits?.map((k) => {
          const completo = k.gb_falta === 0;
          const emCurso = k.arquivos.some((a) => !a.presente && baixando.some((n) => n.endsWith(a.path.split("/").pop()!)));
          const c = cabe(k);
          return (
            <div key={k.id} className={`rounded-lg border px-2.5 py-2 ${k.id === recomendado ? "border-sky-500/40 bg-sky-500/5" : "border-line"}`}>
              <div className="flex items-center gap-2">
                <button className="min-w-0 flex-1 text-left" onClick={() => setAberto(aberto === k.id ? null : k.id)} title="Ver os arquivos do kit">
                  <span className="flex items-center gap-1.5">
                    <span className="truncate text-fg">{k.nome}</span>
                    {k.id === recomendado && <span className="shrink-0 rounded-full bg-sky-400/15 px-1.5 text-[10px] text-sky-300">recomendado</span>}
                  </span>
                  <span className="block text-faint">{k.resumo}</span>
                </button>
                {k.auto && k.repo && (
                  <a
                    href={`https://huggingface.co/${k.repo}`}
                    target="_blank"
                    rel="noreferrer"
                    title={`Abrir ${k.repo} no Hugging Face`}
                    className="shrink-0 text-faint hover:text-fg"
                  >
                    <ExternalLink className="size-3.5" />
                  </a>
                )}
                <SelosModo modos={k.modos} />
              </div>
              {k.erro ? (
                <p className="mt-1.5 text-amber-400">{k.erro}</p>
              ) : (
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-faint">
                {k.opcoes.length > 1 && (
                  <select
                    aria-label={`Quantização do ${k.nome}`}
                    title="Menor = mais leve e menos fiel. O Forja sugere a maior que cabe na VRAM. Trocar para uma que não está no disco baixa essa também."
                    className="rounded-md border border-line bg-raised px-1.5 py-0.5 text-[11px] text-fg"
                    value={k.quant}
                    // com o kit completo também: é assim que se baixa outra quantização do mesmo modelo
                    disabled={emCurso}
                    onChange={(e) => setQuants((q) => ({ ...q, [k.id]: e.target.value }))}
                  >
                    {k.opcoes.map((o) => (
                      <option key={o.quant} value={o.quant}>
                        {o.quant} · {o.gb.toFixed(1).replace(".", ",")} GB{o.presente ? " · no disco" : o.cabe === false ? " · não cabe" : ""}
                      </option>
                    ))}
                  </select>
                )}
                <span>{completo ? `${k.gb_total.toFixed(1).replace(".", ",")} GB no disco` : `${k.gb_falta.toFixed(1).replace(".", ",")} GB para baixar`}</span>
                {c !== null && (
                  <span className={c ? "text-emerald-400" : "text-amber-400"} title={`Maior modelo de difusão: ${k.gb_modelo} GB; VRAM da GPU do sd.cpp: ${vram.toFixed(1)} GB`}>
                    {c ? "cabe na GPU" : "maior que a VRAM: vai com pesos na RAM, mais lento"}
                  </span>
                )}
                <span className="ml-auto">
                  {completo ? (
                    <span className="text-emerald-400"><Check className="mr-0.5 inline size-3" />pronto</span>
                  ) : emCurso ? (
                    <span className="text-sky-300">baixando…</span>
                  ) : (
                    <button className={btn} onClick={() => baixar(k)}>
                      <Download className="mr-1 inline size-3" />
                      {k.gb_falta < k.gb_total ? "Baixar o que falta" : "Baixar kit"}
                    </button>
                  )}
                </span>
              </div>
              )}
              {aberto === k.id && (
                <ul className="mt-2 flex flex-col gap-0.5 border-t border-line pt-1.5">
                  {k.arquivos.map((a) => (
                    <li key={a.path} className="flex items-center gap-2" title={`${a.repo}/${a.path}`}>
                      <span className={a.presente ? "text-emerald-400" : "text-faint"}>{a.presente ? "✓" : "○"}</span>
                      <span className="min-w-0 flex-1 truncate text-muted">{a.path.split("/").pop()}</span>
                      <span className="shrink-0 text-faint">{ROTULO[a.papel] ?? (a.papel === "modelo" ? "modelo" : a.papel)}</span>
                      <span className="w-12 shrink-0 text-right text-faint">{a.gb.toFixed(1).replace(".", ",")} GB</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- aba Imagem

/** O runtime do sd.cpp mora aqui (fica acima, no card de runtime); gerar é nas abas próprias. */
function ImageTab() {
  return (
    <section className={card}>
      <p className="text-fg">Imagem</p>
      <p className="mt-1 text-muted">
        Acima fica o stable-diffusion.cpp. Para gerar, use a aba <b>Imagens</b> no topo da barra lateral; os modelos e
        os ajustes de cada um ficam em Modelos. Vídeo tem aba própria aqui ao lado.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------- aba Vídeo

/** Tudo do vídeo num lugar: os dois motores (acima), os modelos e os ajustes de cada um, os kits e a ampliação. */
function VideoTab(props: { st: LocalState; onDone: () => void; onError: (e: string) => void }) {
  return (
    <>
      <ModelosDeImagem st={props.st} onDone={props.onDone} onError={props.onError} video />
      <KitsVideo
        st={props.st}
        destino={props.st.download_dir}
        onDone={props.onDone}
        onError={props.onError}
        onProcurar={() => window.dispatchEvent(new CustomEvent("forja:ia-local", { detail: "Baixar" }))}
      />
      <KitsVideo st={props.st} destino={props.st.download_dir} onDone={props.onDone} onError={props.onError} auto />
      <section className={card}>
        <span className="mb-1 flex items-center gap-1.5 text-fg">
          <Film className="size-3.5" /> Ampliação de vídeo
        </span>
        <p className="mb-2 text-faint">
          Mais resolução para as tomadas prontas e para vídeos do PC (aba Vídeo › Ampliar vídeo). O ffmpeg, o motor acima,
          lê e grava o vídeo; os ESRGAN ampliam quadro a quadro na GPU. Sem ESRGAN, amplia por Lanczos.
        </p>
        <BaixarAmpliacao onError={props.onError} soModelos video />
      </section>
    </>
  );
}

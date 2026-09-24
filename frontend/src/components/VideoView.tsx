import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, uploadReferencia } from "../api";
import type { ImageOpts, LocalModel, LocalState, LoteImagem, LoteMeta, Message, ModoVideo, PedidoMeta, SeedMode } from "../types";
import {
  ArrowUp, Camera, Check, ChevronDown, Copy, Download, ExternalLink, Film, FolderOpen, Image, Plus, Refresh,
  Search, Sliders, Square, Trocar, X,
} from "./icons";
import { BotaoEnviar, CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt, larguraNumero, numeroPilula, pilula, pilulaLigada } from "./Composer";
import { btn, btnPrimary, Field, input, Num, SAMPLERS } from "./LocalPanel";
import SelosModo from "./SelosModo";
import { A_REFAZER, AnelProgresso, Chip, CORES, duracao, Fundo, Liquido, rotuloSementes, SEEDS, urlDa, velocidade } from "./ImagensView";
import ModelPicker from "./ModelPicker";
import { VideoPlayer, type VideoPlayerApi } from "./VideoPlayer";

/** Aba Vídeo: o Wan no stable-diffusion.cpp. O motor é o dos lotes de imagem (um lote = uma tomada,
 *  com variações, manter/descartar e "Continuar"); a tela é outra porque vídeo se olha tocando. */

const POLL_MS = 1500;
const KEY_LLM = "forja.video.llm";

const MODOS: { id: ModoVideo; rotulo: string; curto: string; dica: string; exemplo: string }[] = [
  { id: "t2v", rotulo: "Texto", curto: "texto → vídeo", dica: "Só o prompt: o modelo inventa a cena inteira.",
    exemplo: "a red fox trotting through fresh snow at dawn, slow tracking shot, soft golden light" },
  { id: "i2v", rotulo: "Imagem", curto: "imagem → vídeo", dica: "Anima uma imagem: ela é o primeiro quadro.",
    exemplo: "the camera slowly pushes in while the hair and the clouds move gently in the wind" },
  { id: "flf2v", rotulo: "Início → Fim", curto: "primeiro e último quadro", dica: "Liga dois quadros: o modelo inventa o caminho.",
    exemplo: "a smooth continuous transition, the flower slowly blossoms, static camera" },
];

type Proporcao = "16:9" | "9:16" | "1:1";
type Qualidade = "480p" | "720p";
// Múltiplos de 16 (o que o Wan pede); 704 e não 720 porque o VAE do Wan2.2 comprime 32×.
const TAMANHOS: Record<Qualidade, Record<Proporcao, [number, number]>> = {
  "480p": { "16:9": [832, 480], "9:16": [480, 832], "1:1": [624, 624] },
  "720p": { "16:9": [1280, 704], "9:16": [704, 1280], "1:1": [960, 960] },
};
const DURACOES = [2, 3, 5];

/** Mesma conta do backend (imagegen.quadros): 4k+1, porque o VAE do Wan junta 4 quadros em 1 no tempo. */
export const quadrosDe = (s: number, fps: number) => Math.max(1, Math.round((s * fps) / 4)) * 4 + 1;
const segundosDe = (frames: number, fps: number) => (fps ? frames / fps : 0);
const fmtS = (s: number) => `${s.toFixed(1).replace(".", ",")} s`;
const rodando = (m: Message) => m.role === "assistant" && m.status === "running";

function tamanhoAtual(o: ImageOpts): { prop: Proporcao | null; qual: Qualidade | null } {
  for (const q of Object.keys(TAMANHOS) as Qualidade[])
    for (const p of Object.keys(TAMANHOS[q]) as Proporcao[])
      if (TAMANHOS[q][p][0] === o.width && TAMANHOS[q][p][1] === o.height) return { prop: p, qual: q };
  return { prop: null, qual: null };
}

/** Um quadro do vídeo em PNG, tirado num `<video>` fora da tela: arrastar um cartão para o slot de
 *  quadro e "continuar a cena" (o último quadro) funcionam sem abrir o player. */
function quadroDe(src: string, tempo: number | "fim"): Promise<Blob | null> {
  return new Promise((resolve) => {
    const v = document.createElement("video");
    v.muted = true;
    v.preload = "auto";
    v.src = src;
    v.onloadeddata = () => {
      v.currentTime = tempo === "fim" ? Math.max(0, v.duration - 0.001) : Math.min(tempo, v.duration - 0.001);
    };
    v.onseeked = () => {
      const c = document.createElement("canvas");
      c.width = v.videoWidth;
      c.height = v.videoHeight;
      c.getContext("2d")?.drawImage(v, 0, 0);
      c.toBlob((b) => resolve(b), "image/png");
    };
    v.onerror = () => resolve(null);
  });
}

async function subirQuadro(b: Blob, nome: string): Promise<string> {
  return uploadReferencia(new File([b], nome, { type: "image/png" }));
}

type Slots = [string | null, string | null]; // [início, fim]

export default function VideoView(props: {
  conv: number | null;
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
  onAbrirBaixar: () => void;
}) {
  const [st, setSt] = useState<LocalState | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [o, setO] = useState<ImageOpts | null>(null);
  const [modelo, setModelo] = useState("");
  const [modo, setModo] = useState<ModoVideo>("t2v");
  const [slots, setSlots] = useState<Slots>([null, null]);
  const [prompt, setPrompt] = useState("");
  const [count, setCount] = useState(1);
  const [seedMode, setSeedMode] = useState<SeedMode>("incremental");
  const [abrirAjustes, setAbrirAjustes] = useState(false);
  const [perguntando, setPerguntando] = useState(false);
  const [melhorando, setMelhorando] = useState(false);
  const [foco, setFoco] = useState<{ lote: number; item: number } | null>(null);
  const [erro, setErro] = useState("");
  const [llm, setLlm] = useState(() => {
    try {
      const salvo = JSON.parse(localStorage.getItem(KEY_LLM) ?? "null");
      if (salvo?.model) return salvo as { provider: string; model: string };
    } catch {
      /* localStorage corrompido: cai no do Chat */
    }
    return { provider: props.provider, model: props.model };
  });
  const fim = useRef<HTMLDivElement>(null);
  const texto = useRef<HTMLTextAreaElement>(null);
  const mostrarErro = useCallback((e: string) => setErro(e), []);

  useEffect(() => {
    try {
      localStorage.setItem(KEY_LLM, JSON.stringify(llm));
    } catch {
      /* sem localStorage: a escolha vale só nesta sessão */
    }
  }, [llm]);

  const ocupado = messages.some(rodando);

  const aplicarModelo = useCallback((m: LocalModel | undefined) => {
    if (!m) return;
    setModelo(m.path);
    const p = m.params;
    if (!p) return;
    // Os ajustes do modelo (sugeridos pelo docs do sd.cpp, ou os que a pessoa salvou) viram os da tela.
    setO((c) => c && {
      ...c, steps: p.steps, cfg: p.cfg, sampler: p.sampler, width: p.width, height: p.height, frames: p.frames,
      fps: p.fps, flow_shift: p.flow_shift, high_noise_steps: p.high_noise_steps, high_noise_cfg: p.high_noise_cfg,
    });
    const modos = m.req?.modos ?? ["t2v"];
    setModo((atual) => (modos.includes(atual) ? atual : modos[0]));
  }, []);

  const carregarLocal = useCallback(async () => {
    try {
      const novo = await api.get<LocalState>("/local");
      setSt(novo);
      setO((atual) => atual ?? novo.video);
      setModelo((atual) => {
        if (atual && novo.video_models.some((m) => m.path === atual)) return atual;
        const escolhido = novo.video_models.find((m) => m.path === novo.video.model) ?? novo.video_models[0];
        if (escolhido) queueMicrotask(() => aplicarModelo(escolhido));
        return escolhido?.path ?? "";
      });
    } catch (e: any) {
      mostrarErro(e.message);
    }
  }, [mostrarErro, aplicarModelo]);

  useEffect(() => {
    carregarLocal();
  }, [carregarLocal]);

  // Sem modelo, a tela fica de olho: o kit baixando na aba IA local aparece aqui sozinho quando termina.
  useEffect(() => {
    if (!st || st.video_models.length) return;
    const t = setInterval(carregarLocal, 5000);
    return () => clearInterval(t);
  }, [st, carregarLocal]);

  const carregarConversa = useCallback(
    async (id: number | null = props.conv) => {
      if (id === null) return setMessages([]);
      try {
        const c = await api.get<{ messages: Message[] }>(`/conversations/${id}`);
        setMessages(c.messages);
      } catch (e: any) {
        mostrarErro(e.message);
      }
    },
    [props.conv, mostrarErro],
  );

  useEffect(() => {
    carregarConversa();
  }, [carregarConversa]);

  useEffect(() => {
    if (!ocupado) return;
    const t = setInterval(() => carregarConversa(), POLL_MS);
    return () => clearInterval(t);
  }, [ocupado, carregarConversa]);

  useEffect(() => {
    fim.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const lotes = useMemo(() => {
    const out: { pedido: Message; resposta: Message }[] = [];
    for (let i = 0; i < messages.length; i++) {
      const m = messages[i];
      if (m.role === "assistant" && (m.meta as LoteMeta | null)?.images) out.push({ pedido: messages[i - 1] ?? m, resposta: m });
    }
    return out;
  }, [messages]);

  const set = <K extends keyof ImageOpts>(k: K, v: ImageOpts[K]) => setO((c) => c && { ...c, [k]: v });
  const atual = st?.video_models.find((m) => m.path === modelo);
  const modos: ModoVideo[] = atual?.req?.modos ?? ["t2v"];
  const precisaQuadros = modo === "t2v" ? 0 : modo === "i2v" ? 1 : 2;
  const refs = slots.slice(0, precisaQuadros).filter(Boolean) as string[];

  // Estimativa pelo que esta máquina já fez: o último s/passo do mesmo modelo, tamanho e duração.
  const estimativa = useMemo(() => {
    if (!o) return null;
    for (let i = lotes.length - 1; i >= 0; i--) {
      const meta = lotes[i].resposta.meta as LoteMeta;
      if (meta.opts.width !== o.width || meta.opts.height !== o.height || meta.opts.frames !== o.frames) continue;
      const feita = [...meta.images].reverse().find((x) => x.model === modelo && x.s_passo);
      if (feita?.s_passo) return (o.steps + Math.max(0, o.high_noise_steps)) * feita.s_passo * count + 20 * count;
    }
    return null;
  }, [lotes, o, modelo, count]);

  function escolherQuadro(i: 0 | 1, path: string | null) {
    setSlots((s) => (i === 0 ? [path, s[1]] : [s[0], path]));
  }

  async function anexarArquivo(i: 0 | 1, f: File) {
    try {
      const noDisco = window.forja?.caminhoDe?.(f);
      const path = noDisco
        ? (await api.post<{ path: string }>("/imagens/referencia/caminho", { path: noDisco })).path
        : await uploadReferencia(f);
      escolherQuadro(i, path);
    } catch (e: any) {
      mostrarErro(e.message);
    }
  }

  async function usarQuadro(b: Blob | null, onde: "inicio" | "fim") {
    if (!b) return mostrarErro("Não deu para tirar o quadro deste vídeo.");
    try {
      const path = await subirQuadro(b, `quadro-${onde}.png`);
      escolherQuadro(onde === "inicio" ? 0 : 1, path);
      // O modo que o quadro pede; se o modelo da vez não faz, vai para um que faz (o 1.3B só faz texto:
      // o quadro ia para um slot escondido e nada acontecia).
      const quer: ModoVideo = onde === "fim" || (modo === "flf2v" && slots[1]) ? "flf2v" : "i2v";
      if (!modos.includes(quer)) {
        const outro = st?.video_models.find((m) => m.req?.modos?.includes(quer));
        if (outro) aplicarModelo(outro);
        else return mostrarErro(`Nenhum modelo de vídeo nas pastas faz ${quer === "i2v" ? "imagem → vídeo" : "primeiro e último quadro"}. Baixe um em IA local › Baixar.`);
      }
      setModo(quer);
      texto.current?.focus();
    } catch (e: any) {
      mostrarErro(e.message);
    }
  }

  async function gerar(confirm = false) {
    if (!o || !prompt.trim() || !modelo) return;
    if (refs.length < precisaQuadros) {
      mostrarErro(precisaQuadros === 1 ? "Escolha a imagem que vai ser animada." : "Escolha o quadro inicial e o final.");
      return;
    }
    try {
      // O que está na tela também vira o padrão da ferramenta video_generate do agente.
      await api.put("/local/video/defaults", { ...o, model: modelo });
      const conv = await props.ensureConversation();
      await api.post(`/imagens/${conv}/gerar`, {
        prompt,
        // Arquivos e ligações de memória são do modelo (IA local › Modelos): o padrão da aba não passa por cima.
        opts: {
          ...o, model: undefined, seed: undefined, offload: undefined, flash_attn: undefined, vae_tiling: undefined,
          te_cpu: undefined, preview: undefined, taesd: undefined, vae: undefined, clip_l: undefined, t5xxl: undefined,
          llm: undefined, llm_vision: undefined, clip_vision: undefined, high_noise_model: undefined, variante: undefined,
          diffusion_model: undefined, out_dir: undefined, descarte_dias: undefined,
        },
        models: [modelo],
        count,
        seed: o.seed,
        seed_mode: seedMode,
        confirm,
        refs,
      });
      setPerguntando(false);
      setErro("");
      setPrompt("");
      props.onConversationChanged();
      carregarConversa(conv);
    } catch (e: any) {
      if (e.status === 409) setPerguntando(true);
      else mostrarErro(e.message);
    }
  }

  async function melhorar() {
    if (!prompt.trim() || !llm.model) return;
    setMelhorando(true);
    try {
      const r = await api.post<{ prompt: string }>("/imagens/prompt", { prompt, ...llm, video: true });
      setPrompt(r.prompt);
    } catch (e: any) {
      mostrarErro(e.message);
    } finally {
      setMelhorando(false);
    }
  }

  function reaproveitar(meta: LoteMeta, pedido: Message, semente?: number) {
    const pm = pedido.meta as PedidoMeta | null;
    const r = pm?.refs ?? [];
    setSlots([r[0] ?? null, r[1] ?? null]);
    setModo(r.length === 2 ? "flf2v" : r.length === 1 ? "i2v" : "t2v");
    setO((c) => c && { ...c, ...meta.opts, ...(semente ? { seed: semente } : {}) });
    if (semente) setSeedMode("fixa");
    const usado = pm?.models?.[0];
    if (usado && st?.video_models.some((m) => m.path === usado)) setModelo(usado);
    setCount(semente ? 1 : meta.count);
    setPrompt(pedido.content);
    texto.current?.focus();
  }

  async function abrirArquivo(caminho: string, mode: "reveal" | "open") {
    try {
      await api.post("/open", { path: caminho, mode });
    } catch (e: any) {
      mostrarErro(e.message);
    }
  }

  if (!st || !o) return <div className="grid h-full place-items-center text-sm text-faint">Carregando…</div>;

  const semRuntime = !st.runtimes.sd.installed;
  const semModelo = !st.video_models.length;
  const { prop, qual } = tamanhoAtual(o);
  const seg = segundosDe(o.frames, o.fps);
  const focoLote = foco && lotes[foco.lote];

  return (
    <>
      {focoLote && foco && (
        <Foco
          pedido={focoLote.pedido}
          resposta={focoLote.resposta}
          indice={foco.item}
          onIndice={(item) => setFoco({ ...foco, item })}
          onFechar={() => setFoco(null)}
          onUsarQuadro={(b, onde) => {
            setFoco(null);
            usarQuadro(b, onde);
          }}
          onReaproveitar={(semente) => {
            setFoco(null);
            reaproveitar(focoLote.resposta.meta as LoteMeta, focoLote.pedido, semente);
          }}
          onAbrir={abrirArquivo}
          onMudou={carregarConversa}
          onError={mostrarErro}
        />
      )}

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-5 py-6">
          {!lotes.length && (
            <Vazio
              semRuntime={semRuntime}
              semModelo={semModelo}
              modos={modos}
              onAbrirBaixar={props.onAbrirBaixar}
              onExemplo={(m) => {
                setModo(m.id);
                setPrompt(m.exemplo);
                texto.current?.focus();
              }}
            />
          )}

          {lotes.map(({ pedido, resposta }, iLote) => (
            <Tomada
              key={resposta.id}
              pedido={pedido}
              resposta={resposta}
              onAbrir={(item) => setFoco({ lote: iLote, item })}
              onContinuar={(path) => quadroDe(urlDa(path), "fim").then((b) => usarQuadro(b, "inicio"))}
              onPasta={(p) => abrirArquivo(p, "reveal")}
              onError={mostrarErro}
              onMudou={carregarConversa}
              onReaproveitar={(semente) => reaproveitar(resposta.meta as LoteMeta, pedido, semente)}
            />
          ))}
          <div ref={fim} />
        </div>
      </div>

      <div className="shrink-0 px-5 pb-4">
        <div className="mx-auto max-w-3xl">
          {erro && (
            <div className="mb-2 flex items-start gap-2 rounded-xl border border-red-900/70 bg-red-950/30 p-2.5 text-xs text-red-200">
              <p className="min-w-0 flex-1 whitespace-pre-wrap break-words">{erro}</p>
              <button onClick={() => setErro("")} title="Fechar" className="text-red-300 hover:text-red-100">
                <X className="size-3.5" />
              </button>
            </div>
          )}
          {perguntando && (
            <div className="mb-2 rounded-xl border border-amber-800/70 bg-amber-950/30 p-2.5 text-xs text-amber-200">
              <p className="font-medium">O modelo {st.server.alias} está carregado na VRAM.</p>
              <p className="mt-1 text-amber-200/80">
                O sd.cpp precisa dessa memória. Descarregar derruba o cache de contexto do chat: a próxima
                mensagem de lá reprocessa o histórico inteiro. A conversa em si não se perde.
              </p>
              <div className="mt-2 flex gap-2">
                <button className={btnPrimary} onClick={() => gerar(true)}>Descarregar e gerar</button>
                <button className={btn} onClick={() => setPerguntando(false)}>Cancelar</button>
              </div>
            </div>
          )}

          {abrirAjustes && (
            <AjustesVideo
              st={st}
              o={o}
              set={set}
              modelo={modelo}
              onModelo={(p) => aplicarModelo(st.video_models.find((m) => m.path === p))}
              seedMode={seedMode}
              onSeedMode={setSeedMode}
              onFechar={() => setAbrirAjustes(false)}
            />
          )}

          <div
            onPaste={(e) => {
              const f = [...e.clipboardData.files].find((x) => x.type.startsWith("image/"));
              if (!f || modo === "t2v") return;
              e.preventDefault();
              anexarArquivo(!slots[0] || modo === "i2v" ? 0 : 1, f);
            }}
          >
            <CaixaPrompt>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <div className="flex rounded-full border border-line p-0.5 text-xs" role="radiogroup" aria-label="Modo">
                  {MODOS.map((m) => {
                    const pode = modos.includes(m.id);
                    return (
                      <button
                        key={m.id}
                        role="radio"
                        aria-checked={modo === m.id}
                        disabled={!pode}
                        onClick={() => setModo(m.id)}
                        title={pode ? m.dica : `${atual?.req?.nome ?? "Este modelo"} não faz ${m.curto}. ${
                          m.id === "t2v" ? "Use um T2V ou o TI2V 5B." : m.id === "i2v" ? "Use o TI2V 5B ou um I2V." : "Use o Wan2.1 FLF2V."}`}
                        className={`rounded-full px-3 py-1 transition-colors ${
                          modo === m.id ? "bg-raised text-fg" : "text-muted hover:text-fg"
                        } disabled:cursor-not-allowed disabled:text-faint/60 disabled:hover:text-faint/60`}
                      >
                        {m.rotulo}
                      </button>
                    );
                  })}
                </div>
                <span className="text-[11px] text-faint">{MODOS.find((m) => m.id === modo)?.dica}</span>
              </div>

              {precisaQuadros > 0 && (
                <div className="mb-2.5 flex items-end gap-2">
                  <SlotQuadro
                    rotulo={precisaQuadros === 2 ? "Início" : "Imagem"}
                    path={slots[0]}
                    aspecto={o.width / o.height}
                    onArquivo={(f) => anexarArquivo(0, f)}
                    onQuadro={(b) => usarQuadro(b, "inicio")}
                    onLimpar={() => escolherQuadro(0, null)}
                  />
                  {precisaQuadros === 2 && (
                    <>
                      <button
                        onClick={() => setSlots(([a, b]) => [b, a])}
                        title="Trocar início e fim"
                        className="mb-7 grid size-7 place-items-center rounded-full border border-line text-muted hover:bg-raised hover:text-fg"
                      >
                        <Trocar className="size-3.5" />
                      </button>
                      <SlotQuadro
                        rotulo="Fim"
                        path={slots[1]}
                        aspecto={o.width / o.height}
                        onArquivo={(f) => anexarArquivo(1, f)}
                        onQuadro={(b) => usarQuadro(b, "fim")}
                        onLimpar={() => escolherQuadro(1, null)}
                      />
                    </>
                  )}
                  <p className="mb-7 ml-1 max-w-56 text-[11px] leading-snug text-faint">
                    Clique, solte um arquivo, cole (Ctrl+V) ou arraste um vídeo do feed — vale o quadro onde ele estiver.
                  </p>
                </div>
              )}

              <textarea
                ref={texto}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    gerar();
                  }
                }}
                rows={2}
                placeholder={MODOS.find((m) => m.id === modo)?.exemplo + " — em inglês funciona melhor"}
                className={campoPrompt}
              />
              <RodapePrompt>
                <Menu
                  rotulo={<><Film className="size-3.5" /><span className="max-w-40 truncate">{atual ? atual.req?.nome ?? atual.name : "Escolher modelo"}</span><ChevronDown className="size-3" /></>}
                  titulo={atual ? `${atual.name}\n${atual.path}` : "Modelo de vídeo"}
                >
                  {(fechar) => (
                    <div className="w-72">
                      <p className="px-2 pb-1 text-[11px] text-faint">Modelo de vídeo</p>
                      {st.video_models.map((m) => (
                        <button
                          key={m.path}
                          onClick={() => {
                            aplicarModelo(m);
                            fechar();
                          }}
                          className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-raised ${m.path === modelo ? "bg-raised" : ""}`}
                        >
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-fg">{m.req?.nome ?? m.name}</span>
                            <span className="block truncate text-[11px] text-faint">{m.name}</span>
                          </span>
                          <SelosModo modos={m.req?.modos ?? []} />
                          {!!m.falta?.length && <span className="text-[11px] text-amber-400" title="Falta arquivo (IA local › Modelos)">!</span>}
                        </button>
                      ))}
                      <button onClick={() => { fechar(); props.onAbrirBaixar(); }} className="mt-1 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-muted hover:bg-raised hover:text-fg">
                        <Download className="size-3.5" /> Baixar modelos de vídeo
                      </button>
                    </div>
                  )}
                </Menu>
                <Menu rotulo={<>{prop ?? `${o.width}×${o.height}`}{qual && <span className="text-faint">· {qual}</span>}</>} titulo="Proporção e qualidade">
                  {() => (
                    <div className="w-56 space-y-2 p-1">
                      <Segmento
                        rotulo="Proporção"
                        opcoes={["16:9", "9:16", "1:1"] as Proporcao[]}
                        valor={prop}
                        onValor={(p) => {
                          const [w, h] = TAMANHOS[qual ?? "480p"][p];
                          set("width", w);
                          set("height", h);
                        }}
                      />
                      <Segmento
                        rotulo="Qualidade"
                        opcoes={["480p", "720p"] as Qualidade[]}
                        valor={qual}
                        onValor={(q) => {
                          const [w, h] = TAMANHOS[q][prop ?? "16:9"];
                          set("width", w);
                          set("height", h);
                        }}
                      />
                      <p className="px-1 text-[11px] leading-snug text-faint">
                        720p pede bem mais memória e tempo; numa GPU de 12 GB, 480p é o confortável.
                      </p>
                    </div>
                  )}
                </Menu>
                <Menu rotulo={<>{fmtS(seg)}</>} titulo={`${o.frames} quadros a ${o.fps} fps`}>
                  {() => (
                    <div className="w-56 space-y-2 p-1">
                      <Segmento
                        rotulo="Duração"
                        opcoes={DURACOES.map((d) => `${d} s`)}
                        valor={DURACOES.map((d) => `${d} s`).find((d) => quadrosDe(parseInt(d), o.fps) === o.frames) ?? null}
                        onValor={(d) => set("frames", quadrosDe(parseInt(d), o.fps))}
                      />
                      <p className="px-1 text-[11px] leading-snug text-faint">
                        {o.frames} quadros a {o.fps} fps. Cada segundo a mais é memória e tempo a mais — o Wan foi
                        treinado com clipes de até 5 s.
                      </p>
                    </div>
                  )}
                </Menu>
                <label className={`${pilula} focus-within:border-[#555]`} title="Quantas variações gerar">
                  <Copy className="size-3.5" />
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={count}
                    onChange={(e) => setCount(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
                    className={numeroPilula}
                    style={larguraNumero(count)}
                  />
                  {count === 1 ? "variação" : "variações"}
                </label>
                <button
                  onClick={melhorar}
                  disabled={!prompt.trim() || !llm.model || melhorando}
                  title={llm.model ? `Reescrever o prompt com ${llm.model}: sujeito, ação, câmera, luz` : "Escolha à direita o modelo que reescreve"}
                  className={pilula}
                >
                  <Refresh className={`size-3.5 ${melhorando ? "animate-spin" : ""}`} />
                  Melhorar
                </button>
                <button
                  onClick={() => setAbrirAjustes((v) => !v)}
                  title={`Ajustes: passos, CFG, amostrador, sementes\n${o.width}×${o.height} · ${o.steps} passos · CFG ${o.cfg}`}
                  className={`${pilula} ${abrirAjustes ? pilulaLigada : ""} !px-2`}
                  aria-label="Ajustes avançados"
                >
                  <Sliders className="size-3.5" />
                </button>
                <DireitaPrompt>
                  {estimativa !== null && (
                    <span className="shrink-0 text-[11px] tabular-nums text-faint" title="Pelo tempo por passo das tomadas anteriores iguais a esta">
                      {duracao(estimativa).replace("~", "≈ ")}
                    </span>
                  )}
                  <div className="min-w-0" title="Modelo que reescreve o prompt (não é o que gera o vídeo)">
                    <ModelPicker provider={llm.provider} model={llm.model} onChange={(provider, model) => setLlm({ provider, model })} />
                  </div>
                  <BotaoEnviar
                    onEnviar={() => gerar()}
                    desabilitado={!prompt.trim() || !modelo || semRuntime || st.image_busy || ocupado || refs.length < precisaQuadros}
                    titulo={st.image_busy || ocupado ? "Já tem geração em andamento" : refs.length < precisaQuadros ? "Faltam os quadros" : "Gerar"}
                  />
                </DireitaPrompt>
              </RodapePrompt>
            </CaixaPrompt>
          </div>
          <p className="mt-1.5 text-center text-[11px] text-faint">
            Um vídeo por vez, com a GPU só para ele — as variações entram numa fila.
          </p>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- peças pequenas

/** Pílula do rodapé que abre um painel por cima (fecha com Esc ou clique fora). */
function Menu(props: { rotulo: React.ReactNode; titulo?: string; children: (fechar: () => void) => React.ReactNode }) {
  const [aberto, setAberto] = useState(false);
  const caixa = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!aberto) return;
    const fora = (e: MouseEvent) => !caixa.current?.contains(e.target as Node) && setAberto(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setAberto(false);
    document.addEventListener("mousedown", fora);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", fora);
      document.removeEventListener("keydown", esc);
    };
  }, [aberto]);
  return (
    <div ref={caixa} className="relative">
      <button onClick={() => setAberto((v) => !v)} title={props.titulo} className={`${pilula} ${aberto ? pilulaLigada : ""}`} aria-expanded={aberto}>
        {props.rotulo}
      </button>
      {aberto && (
        <div className="absolute bottom-full left-0 z-30 mb-2 rounded-xl border border-line bg-surface p-1.5 text-xs shadow-2xl shadow-black/50">
          {props.children(() => setAberto(false))}
        </div>
      )}
    </div>
  );
}

function Segmento<T extends string>(props: { rotulo: string; opcoes: T[]; valor: T | null; onValor: (v: T) => void }) {
  return (
    <div>
      <p className="mb-1 px-1 text-[11px] text-faint">{props.rotulo}</p>
      <div className="flex rounded-full border border-line p-0.5">
        {props.opcoes.map((op) => (
          <button
            key={op}
            onClick={() => props.onValor(op)}
            className={`flex-1 rounded-full px-2 py-1 ${props.valor === op ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}
          >
            {op}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Slot de quadro do composer: clique, soltar arquivo, ou arrastar um vídeo do feed. */
function SlotQuadro(props: {
  rotulo: string;
  path: string | null;
  aspecto: number;
  onArquivo: (f: File) => void;
  onQuadro: (b: Blob | null) => void;
  onLimpar: () => void;
}) {
  const arquivo = useRef<HTMLInputElement>(null);
  const [sobre, setSobre] = useState(false);
  const [sumida, setSumida] = useState(false);
  const alto = 88;
  const largo = Math.round(Math.min(160, Math.max(56, alto * props.aspecto)));
  useEffect(() => setSumida(false), [props.path]);

  function soltar(e: React.DragEvent) {
    e.preventDefault();
    setSobre(false);
    const video = e.dataTransfer.getData("application/x-forja-video");
    if (video) {
      const { path, tempo } = JSON.parse(video) as { path: string; tempo: number };
      quadroDe(urlDa(path), tempo).then(props.onQuadro);
      return;
    }
    const f = [...e.dataTransfer.files].find((x) => x.type.startsWith("image/"));
    if (f) props.onArquivo(f);
  }

  return (
    <div className="flex flex-col items-center gap-1">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setSobre(true);
        }}
        onDragLeave={() => setSobre(false)}
        onDrop={soltar}
        style={{ width: largo, height: alto }}
        className={`group relative overflow-hidden rounded-xl border transition-colors ${
          sobre ? "border-sky-400 bg-sky-400/10" : props.path ? "border-line" : "border-dashed border-[#444] hover:border-[#666]"
        }`}
      >
        {props.path && !sumida ? (
          <>
            <img src={urlDa(props.path)} alt={props.rotulo} onError={() => setSumida(true)} className="size-full object-cover" />
            <button
              onClick={props.onLimpar}
              title="Tirar"
              className="absolute right-1 top-1 grid size-5 place-items-center rounded-full bg-black/70 text-white/80 opacity-0 transition group-hover:opacity-100 hover:text-white"
            >
              <X className="size-3" />
            </button>
          </>
        ) : (
          <button onClick={() => arquivo.current?.click()} className="grid size-full place-items-center text-faint hover:text-fg">
            <span className="flex flex-col items-center gap-1 text-[10px]">
              {sumida ? <span className="text-amber-300">não achada</span> : <Plus className="size-4" />}
              {sumida ? "trocar" : "imagem"}
            </span>
          </button>
        )}
        <input
          ref={arquivo}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) props.onArquivo(f);
            e.target.value = "";
          }}
        />
      </div>
      <span className="text-[11px] text-muted">{props.rotulo}</span>
    </div>
  );
}

// ---------------------------------------------------------------- estado vazio

function Vazio(props: {
  semRuntime: boolean;
  semModelo: boolean;
  modos: ModoVideo[];
  onAbrirBaixar: () => void;
  onExemplo: (m: (typeof MODOS)[number]) => void;
}) {
  return (
    <div className="mt-[12vh] text-center">
      <div className="text-3xl font-semibold">Vídeo</div>
      <div className="text-3xl text-faint">Descreva a cena, anime uma imagem ou ligue dois quadros.</div>
      {props.semRuntime || props.semModelo ? (
        <div className="mx-auto mt-6 max-w-md rounded-2xl border border-line bg-surface p-4 text-sm">
          <p className="text-muted">
            {props.semRuntime
              ? "O stable-diffusion.cpp ainda não está instalado. Ele vem com o runtime, na aba IA local."
              : "Nenhum modelo de vídeo nas pastas. Os kits do Wan trazem o modelo e as peças que ele pede, num clique."}
          </p>
          <button className={`${btnPrimary} mt-3`} onClick={props.onAbrirBaixar}>
            <Download className="mr-1 inline size-3.5" />
            {props.semRuntime ? "Abrir IA local" : "Baixar um modelo de vídeo"}
          </button>
        </div>
      ) : (
        <div className="mx-auto mt-8 grid max-w-3xl gap-3 text-left sm:grid-cols-3">
          {MODOS.map((m) => {
            const pode = props.modos.includes(m.id);
            return (
              <button
                key={m.id}
                disabled={!pode}
                onClick={() => props.onExemplo(m)}
                className="group rounded-2xl border border-line bg-surface p-3.5 text-left transition hover:border-[#454545] hover:bg-raised disabled:opacity-40 disabled:hover:border-line disabled:hover:bg-surface"
                title={pode ? "Usar este exemplo" : "O modelo escolhido não faz este modo"}
              >
                <IlustracaoModo modo={m.id} />
                <p className="mt-3 text-sm font-medium text-fg">{m.curto[0].toUpperCase() + m.curto.slice(1)}</p>
                <p className="mt-0.5 text-xs text-muted">{m.dica}</p>
                <p className="mt-2 line-clamp-2 font-mono text-[11px] text-faint group-hover:text-muted">{m.exemplo}</p>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Desenho do modo: quadros numa fita. O que o modo recebe fica aceso; o que o modelo inventa, tracejado. */
function IlustracaoModo({ modo }: { modo: ModoVideo }) {
  const dado = (i: number) => (modo === "i2v" && i === 0) || (modo === "flf2v" && (i === 0 || i === 3));
  return (
    <div className="flex items-center gap-1">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className={`grid h-9 flex-1 place-items-center rounded-md ${
            dado(i) ? "bg-sky-400/20 text-sky-300 ring-1 ring-sky-400/40" : "border border-dashed border-[#3a3a3a] text-faint"
          }`}
        >
          {dado(i) ? <Image className="size-3.5" /> : modo === "t2v" && i === 0 ? <span className="text-[10px]">Aa</span> : null}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- ajustes

function AjustesVideo(props: {
  st: LocalState;
  o: ImageOpts;
  set: <K extends keyof ImageOpts>(k: K, v: ImageOpts[K]) => void;
  modelo: string;
  onModelo: (p: string) => void;
  seedMode: SeedMode;
  onSeedMode: (s: SeedMode) => void;
  onFechar: () => void;
}) {
  const { o, set, st } = props;
  const atual = st.video_models.find((m) => m.path === props.modelo);
  const a14b = !!atual?.params?.high_noise_model || atual?.variante?.includes("a14b");
  return (
    <div className="mb-2 rounded-2xl border border-line bg-surface p-3.5 text-xs">
      <div className="mb-2.5 flex items-center gap-2">
        <span className="font-medium text-fg">Ajustes do vídeo</span>
        {atual?.req?.doc && (
          <a href={atual.req.doc} target="_blank" rel="noreferrer" className="text-faint underline hover:text-muted">
            guia do sd.cpp
          </a>
        )}
        <button onClick={props.onFechar} className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg" aria-label="Fechar">
          <X className="size-3.5" />
        </button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="flex flex-col gap-2.5">
          <Field label="Modelo" hint="Os ajustes abaixo partem dos sugeridos para ele.">
            <select className={input} value={props.modelo} onChange={(e) => props.onModelo(e.target.value)}>
              {st.video_models.map((m) => (
                <option key={m.path} value={m.path}>{`${m.req?.nome ?? "Wan"} — ${m.name}`}</option>
              ))}
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Num label="Largura" value={o.width} onChange={(v) => set("width", v)} step={16} />
            <Num label="Altura" value={o.height} onChange={(v) => set("height", v)} step={16} />
            <Num label="Quadros" value={o.frames} onChange={(v) => set("frames", Math.max(1, Math.round((v - 1) / 4)) * 4 + 1)} step={4} hint="Sempre 4k+1." />
            <Num label="FPS" value={o.fps} onChange={(v) => set("fps", v)} hint={`${fmtS(segundosDe(o.frames, o.fps))} de vídeo`} />
          </div>
          <Field label="Negativo" hint="O que evitar no vídeo.">
            <input className={input} value={o.negative} onChange={(e) => set("negative", e.target.value)} placeholder="blurry, static, distorted…" />
          </Field>
        </div>
        <div className="flex flex-col gap-2.5">
          <div className="grid grid-cols-2 gap-2">
            <Num label="Passos" value={o.steps} onChange={(v) => set("steps", v)} />
            <Num label="CFG" value={o.cfg} onChange={(v) => set("cfg", v)} step={0.5} />
            <Num label="Flow shift" value={o.flow_shift} onChange={(v) => set("flow_shift", v)} step={0.5} hint="0 = automático" />
            <Field label="Amostrador">
              <select className={input} value={o.sampler} onChange={(e) => set("sampler", e.target.value)}>
                {SAMPLERS.map((s) => <option key={s}>{s}</option>)}
              </select>
            </Field>
            {a14b && (
              <>
                <Num label="Passos (alto ruído)" value={o.high_noise_steps} onChange={(v) => set("high_noise_steps", v)} hint="-1 = automático" />
                <Num label="CFG (alto ruído)" value={o.high_noise_cfg} onChange={(v) => set("high_noise_cfg", v)} step={0.5} hint="0 = o mesmo" />
              </>
            )}
          </div>
          <Field label="Sementes" hint={SEEDS.find((s) => s.id === props.seedMode)?.hint}>
            <div className="flex gap-1">
              {SEEDS.map((s) => (
                <button
                  key={s.id}
                  onClick={() => props.onSeedMode(s.id)}
                  className={`rounded-full px-2.5 py-0.5 ${props.seedMode === s.id ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </Field>
          {props.seedMode !== "aleatoria" && (
            <Num label="Semente base" value={o.seed} onChange={(v) => set("seed", v)} hint="0 = sorteia uma e anota" />
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- tomada (um lote)

function Tomada(props: {
  pedido: Message;
  resposta: Message;
  onAbrir: (item: number) => void;
  onContinuar: (path: string) => void;
  onPasta: (path: string) => void;
  onError: (e: string) => void;
  onMudou: () => void;
  onReaproveitar: (semente?: number) => void;
}) {
  const meta = props.resposta.meta as LoteMeta;
  const itens = meta.images;
  const pm = props.pedido.meta as PedidoMeta | null;
  const viva = props.resposta.status === "running";
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [salvando, setSalvando] = useState(false);
  const [vram, setVram] = useState(false);
  const faltam = itens.filter((i) => A_REFAZER.includes(i.status)).length;
  const w = meta.opts.width ?? 832;
  const h = meta.opts.height ?? 480;
  const fps = meta.opts.fps ?? 16;
  const frames = meta.opts.frames ?? 33;
  const refs = pm?.refs ?? [];
  const retrato = h > w;

  useEffect(() => {
    setSel(new Set(itens.filter((i) => i.status === "mantida").map((i) => i.path)));
  }, [props.resposta.id, itens.filter((i) => i.status === "mantida").length]);

  const aprovaveis = itens.filter((i) => ["pronta", "mantida", "descartada"].includes(i.status));
  const prontas = itens.filter((i) => i.status === "pronta" || i.status === "mantida").length;

  async function chamar(rota: string, corpo: object = {}) {
    try {
      await api.post(`/imagens/${props.resposta.id}/${rota}`, corpo);
      props.onMudou();
      return true;
    } catch (e: any) {
      if (e.status === 409) setVram(true);
      else props.onError(e.message);
      return false;
    }
  }

  return (
    <section className="my-9">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <p className="min-w-0 flex-1 text-[15px] text-fg">{props.pedido.content}</p>
        <span className="text-xs text-faint">
          {viva ? `gerando ${prontas + 1} de ${itens.length}…`
            : props.resposta.status === "interrompido" ? `interrompida: ${itens.length - faltam} de ${itens.length} prontas`
            : itens.length === 1 ? "1 tomada" : `${itens.length} variações`}
        </span>
      </div>
      <div className="mb-2.5 flex flex-wrap items-center gap-1.5 text-[11px] text-faint">
        {refs.length > 0 && (
          <span className="flex items-center gap-1 rounded-full bg-raised py-0.5 pl-0.5 pr-2">
            {refs.map((r) => <img key={r} src={urlDa(r)} alt="" className="size-4 rounded-full object-cover" />)}
            {refs.length === 2 ? "início → fim" : "imagem → vídeo"}
          </span>
        )}
        <Chip>{`${w}×${h}`}</Chip>
        <Chip>{`${fmtS(segundosDe(frames, fps))} · ${frames} q · ${fps} fps`}</Chip>
        {meta.opts.steps !== undefined && <Chip>{`${meta.opts.steps} passos · CFG ${meta.opts.cfg}`}</Chip>}
        <button
          onClick={() => props.onReaproveitar(itens[0].seed)}
          title={`Sementes: ${itens.map((i) => i.seed).join(", ")}\nClique para refazer com ${itens[0].seed}`}
          className="rounded-full bg-raised px-2 py-0.5 hover:text-fg"
        >
          {rotuloSementes(itens.map((i) => i.seed), meta.seed_mode)}
        </button>
        {[...new Set(itens.map((i) => i.model_name))].map((n) => <Chip key={n}>{n}</Chip>)}
      </div>

      <div className={`grid gap-3 ${retrato ? "grid-cols-2 md:grid-cols-3 xl:grid-cols-4" : itens.length === 1 ? "grid-cols-1 md:max-w-3xl" : "grid-cols-1 md:grid-cols-2"}`}>
        {itens.map((item, i) => (
          <CartaoVideo
            key={item.path}
            item={item}
            aspecto={w / h}
            segundos={segundosDe(frames, fps)}
            origem={refs[0]}
            marcado={sel.has(item.path)}
            onMarcar={() =>
              setSel((s) => {
                const novo = new Set(s);
                if (novo.has(item.path)) novo.delete(item.path);
                else novo.add(item.path);
                return novo;
              })
            }
            onAbrir={() => props.onAbrir(i)}
            onContinuar={() => props.onContinuar(item.path)}
            onPasta={() => props.onPasta(item.path)}
            onSemente={() => props.onReaproveitar(item.seed)}
          />
        ))}
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-2 text-xs">
        {viva ? (
          <button className={btn} onClick={() => chamar("cancelar")}>
            <Square className="mr-1 inline size-3" />
            Cancelar
          </button>
        ) : (
          aprovaveis.length > 0 && (
            <>
              <button
                // sem nada marcado a ação é descartar tudo: não pode ser o botão em destaque
                className={sel.size ? btnPrimary : btn}
                disabled={salvando}
                onClick={async () => {
                  setSalvando(true);
                  await chamar("decidir", { keep: [...sel] });
                  setSalvando(false);
                }}
                title="Os não marcados vão para a subpasta descartadas/"
              >
                <Check className="mr-1 inline size-3.5" />
                {sel.size ? `Manter ${sel.size} · descartar ${aprovaveis.length - sel.size}` : `Descartar ${aprovaveis.length === 1 ? "" : "todos "}(${aprovaveis.length})`}
              </button>
              {aprovaveis.length > 1 && (
                <button className={btn} onClick={() => setSel(new Set(aprovaveis.map((i) => i.path)))}>Marcar todos</button>
              )}
            </>
          )
        )}
        {!viva && faltam > 0 && (
          <button className={btn} onClick={() => chamar("continuar", { confirm: false })} title="Gera só o que faltou, com as mesmas sementes">
            <ArrowUp className="mr-1 inline size-3.5 rotate-90" />
            Continuar ({faltam})
          </button>
        )}
        <button className={btn} onClick={() => props.onReaproveitar()} title="Traz prompt, quadros e ajustes desta tomada para o campo">
          <Refresh className="mr-1 inline size-3.5" />
          Reaproveitar
        </button>
      </div>
      {vram && (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-amber-800/70 bg-amber-950/30 p-2.5 text-xs text-amber-200">
          <span className="flex-1">Tem um modelo carregado na VRAM, e o sd.cpp precisa dessa memória.</span>
          <button className={btnPrimary} onClick={() => chamar("continuar", { confirm: true }).then((ok) => ok && setVram(false))}>
            Descarregar e continuar
          </button>
          <button className={btn} onClick={() => setVram(false)}>Cancelar</button>
        </div>
      )}
    </section>
  );
}

/** Cartão do feed: parado mostra o primeiro quadro; passar o mouse toca mudo, e mexer na horizontal faz
 *  scrub (o ponto do mouse é o ponto do vídeo). Arrastar leva o quadro da vez para um slot do composer. */
function CartaoVideo(props: {
  item: LoteImagem;
  aspecto: number;
  segundos: number;
  origem?: string;
  marcado: boolean;
  onMarcar: () => void;
  onAbrir: () => void;
  onContinuar: () => void;
  onPasta: () => void;
  onSemente: () => void;
}) {
  const { item } = props;
  const v = useRef<HTMLVideoElement>(null);
  const [pos, setPos] = useState(0);
  const ultimoX = useRef<number | null>(null);
  const temArquivo = ["pronta", "mantida", "descartada"].includes(item.status);
  const comPrevia = item.status === "gerando" && (!!item.preview || !!item.com_previa);
  const pct = Math.round(Math.min(1, Math.max(0, item.progress ?? 0)) * 100);

  function mover(e: React.PointerEvent<HTMLDivElement>) {
    const el = v.current;
    if (!el || !el.duration) return;
    const r = e.currentTarget.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    // um empurrão horizontal de verdade vira scrub; tremida do mouse não pausa o vídeo
    if (ultimoX.current !== null && Math.abs(e.clientX - ultimoX.current) > 3) {
      el.pause();
      el.currentTime = f * el.duration;
      setPos(f);
    }
    ultimoX.current = e.clientX;
  }

  return (
    <figure
      className={`group relative overflow-hidden rounded-xl border bg-raised transition-colors ${
        props.marcado ? "border-emerald-500" : "border-line hover:border-[#454545]"
      }`}
    >
      {temArquivo ? (
        <div
          className="relative cursor-pointer bg-black"
          style={{ aspectRatio: props.aspecto }}
          draggable
          onDragStart={(e) => {
            e.dataTransfer.setData("application/x-forja-video", JSON.stringify({ path: item.path, tempo: v.current?.currentTime ?? 0 }));
            e.dataTransfer.effectAllowed = "copy";
          }}
          onPointerEnter={() => {
            ultimoX.current = null;
            v.current?.play().catch(() => {});
          }}
          onPointerMove={mover}
          onPointerLeave={() => {
            const el = v.current;
            if (!el) return;
            el.pause();
            el.currentTime = 0;
            setPos(0);
          }}
          onClick={props.onAbrir}
          title="Clique para abrir no player · arraste para usar o quadro como imagem inicial"
        >
          <video
            ref={v}
            src={urlDa(item.path)}
            muted
            loop
            playsInline
            preload="metadata"
            onTimeUpdate={(e) => setPos(e.currentTarget.duration ? e.currentTarget.currentTime / e.currentTarget.duration : 0)}
            className={`size-full object-cover ${item.status === "descartada" ? "opacity-40 grayscale" : ""}`}
          />
          <span className="pointer-events-none absolute right-2 top-2 rounded-md bg-black/60 px-1.5 py-0.5 text-[10px] tabular-nums text-white/85 backdrop-blur-sm">
            {fmtS(props.segundos)}
          </span>
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-0.5 bg-white/10 opacity-0 transition-opacity group-hover:opacity-100">
            <div className="h-full bg-white/80" style={{ width: `${pos * 100}%` }} />
          </div>
        </div>
      ) : (
        <div className="relative grid w-full place-items-center overflow-hidden" style={{ aspectRatio: props.aspecto }}>
          {item.status !== "erro" && (
            <Fundo
              src={item.status === "gerando" && item.preview ? `${urlDa(item.preview)}&v=${item.progress ?? 0}` : undefined}
              inicial={props.origem && urlDa(props.origem)}
            />
          )}
          {item.status === "erro" ? (
            <span className="px-3 text-center text-[11px] text-red-300" title={item.error}>
              {item.error.split("\n")[0].slice(0, 120)}
            </span>
          ) : item.status === "gerando" && !comPrevia ? (
            <Liquido fracao={item.progress ?? 0} sPasso={item.s_passo} restante={item.restante} />
          ) : item.status === "gerando" && !item.preview ? (
            // Antes da 1ª prévia (carregando pesos, codificando o prompt) o card não pode parecer travado.
            <div className="flex flex-col items-center gap-2.5 text-[11px] text-muted">
              <span className="size-6 animate-spin rounded-full border-2 border-sky-400/20 border-t-sky-400" />
              {item.s_passo ? "primeira prévia a caminho…" : "carregando o modelo e o codificador…"}
            </div>
          ) : item.status === "pendente" ? (
            <span className="text-[11px] text-faint">na fila</span>
          ) : null}
        </div>
      )}

      {comPrevia && <AnelProgresso pct={pct} />}
      {temArquivo && (
        <button
          onClick={props.onMarcar}
          title={props.marcado ? "Desmarcar" : "Marcar para manter"}
          className={`absolute left-2 top-2 grid size-6 place-items-center rounded-full border ${
            props.marcado ? "border-emerald-400 bg-emerald-500 text-black" : "border-white/20 bg-black/60 text-transparent hover:text-white"
          }`}
        >
          <Check className="size-3.5" />
        </button>
      )}

      <figcaption className="flex items-center gap-2 px-2.5 py-1.5 text-[11px]">
        <span className={`min-w-0 flex-1 truncate ${CORES[item.status]}`} title={`${item.model_name} · ${item.path}`}>
          {item.model_name || "—"}
        </span>
        {temArquivo && (
          <>
            <button onClick={props.onSemente} title="Refazer com esta semente" className="text-faint hover:text-fg">
              <Search className="mr-0.5 inline size-3" />
              {item.seed}
            </button>
            <button onClick={props.onContinuar} title="Continuar a cena: o último quadro vira a imagem inicial" className="text-faint hover:text-fg">
              <Camera className="size-3" />
            </button>
            <button onClick={props.onPasta} title="Mostrar na pasta" className="text-faint hover:text-fg">
              <FolderOpen className="size-3" />
            </button>
          </>
        )}
        {comPrevia ? (
          <span className="shrink-0 tabular-nums text-sky-300">
            {item.s_passo ? `${velocidade(item.s_passo)} · ${duracao(item.restante ?? 0)}` : "preparando"}
          </span>
        ) : (
          !temArquivo && item.status !== "gerando" && <span className={CORES[item.status]}>{item.status}</span>
        )}
      </figcaption>
    </figure>
  );
}

// ---------------------------------------------------------------- foco (o player grande)

function Foco(props: {
  pedido: Message;
  resposta: Message;
  indice: number;
  onIndice: (i: number) => void;
  onFechar: () => void;
  onUsarQuadro: (b: Blob | null, onde: "inicio" | "fim") => void;
  onReaproveitar: (semente?: number) => void;
  onAbrir: (path: string, mode: "reveal" | "open") => void;
  onMudou: () => void;
  onError: (e: string) => void;
}) {
  const meta = props.resposta.meta as LoteMeta;
  const pm = props.pedido.meta as PedidoMeta | null;
  const prontos = meta.images.map((x, i) => ({ x, i })).filter(({ x }) => ["pronta", "mantida", "descartada"].includes(x.status));
  const pos = Math.max(0, prontos.findIndex(({ i }) => i === props.indice));
  const atual = prontos[pos]?.x;
  const player = useRef<VideoPlayerApi>(null);
  const w = meta.opts.width ?? 832;
  const h = meta.opts.height ?? 480;
  const fps = meta.opts.fps ?? 16;
  const frames = meta.opts.frames ?? 33;
  const refs = pm?.refs ?? [];
  const [salvo, setSalvo] = useState("");

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea")) return;
      if (e.key === "Escape" && !document.fullscreenElement) props.onFechar();
      else if (e.key === "ArrowUp" && pos > 0) (e.preventDefault(), props.onIndice(prontos[pos - 1].i));
      else if (e.key === "ArrowDown" && pos < prontos.length - 1) (e.preventDefault(), props.onIndice(prontos[pos + 1].i));
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [pos, prontos.length, props.onFechar, props.onIndice]);

  if (!atual) return null;

  async function decidir(manter: boolean) {
    const mantidos = meta.images.filter((x) => x.status === "mantida" && x.path !== atual.path).map((x) => x.path);
    try {
      // só esta tomada muda: as outras continuam como estavam (prontas ficam prontas, sem decisão)
      const pendentes = meta.images.filter((x) => x.status === "pronta" && x.path !== atual.path).map((x) => x.path);
      await api.post(`/imagens/${props.resposta.id}/decidir`, { keep: [...mantidos, ...pendentes, ...(manter ? [atual.path] : [])] });
      props.onMudou();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function salvarQuadro() {
    const b = await player.current?.capturar();
    if (!b) return;
    const url = URL.createObjectURL(b);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${atual.path.split(/[\\/]/).pop()?.replace(/\.webm$/, "")}-quadro.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    setSalvo("Quadro salvo em Downloads.");
  }

  const acao = "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs text-muted hover:bg-raised hover:text-fg";

  return createPortal(
    <div className="fixed inset-0 z-50 flex bg-black/85 backdrop-blur-md" role="dialog" aria-label="Player de vídeo" onClick={props.onFechar}>
      <div className="flex min-w-0 flex-1 items-center justify-center p-6" onClick={(e) => e.stopPropagation()}>
        <VideoPlayer
          key={atual.path}
          ref={player}
          src={urlDa(atual.path)}
          fps={fps}
          quadros={frames}
          autoPlay
          tecladoGlobal
          marcas={refs.length === 2}
          className="rounded-xl shadow-2xl shadow-black"
          // o maior que cabe na área sem cortar nem deixar tarja: largura limitada pela altura da tela
          style={{ aspectRatio: w / h, width: `min(100%, calc((100vh - 48px) * ${w / h}))` }}
        />
      </div>
      <aside className="flex w-80 shrink-0 flex-col border-l border-line bg-panel" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-1 border-b border-line px-3 py-2.5 text-xs">
          <span className="text-fg">Tomada {pos + 1}</span>
          <span className="text-faint">de {prontos.length}</span>
          <div className="ml-auto flex items-center gap-0.5">
            <button disabled={pos === 0} onClick={() => props.onIndice(prontos[pos - 1].i)} title="Anterior (↑)" className="rounded-md p-1 text-muted hover:bg-raised hover:text-fg disabled:opacity-30">
              <ArrowUp className="size-3.5" />
            </button>
            <button disabled={pos >= prontos.length - 1} onClick={() => props.onIndice(prontos[pos + 1].i)} title="Próxima (↓)" className="rounded-md p-1 text-muted hover:bg-raised hover:text-fg disabled:opacity-30">
              <ArrowUp className="size-3.5 rotate-180" />
            </button>
            <button onClick={props.onFechar} title="Fechar (Esc)" className="rounded-md p-1 text-muted hover:bg-raised hover:text-fg">
              <X className="size-3.5" />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          <p className="text-[13px] leading-relaxed text-fg">{props.pedido.content}</p>
          {refs.length > 0 && (
            <div className="mt-3 flex items-center gap-2">
              {refs.map((r, i) => (
                <figure key={r} className="text-center">
                  <img src={urlDa(r)} alt="" className="h-14 rounded-md border border-line object-cover" style={{ aspectRatio: w / h }} />
                  <figcaption className="mt-0.5 text-[10px] text-faint">{refs.length === 2 ? (i ? "fim" : "início") : "imagem"}</figcaption>
                </figure>
              ))}
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-1.5 text-[11px] text-faint">
            <Chip>{atual.model_name}</Chip>
            <Chip>{`${w}×${h}`}</Chip>
            <Chip>{`${frames} q · ${fps} fps`}</Chip>
            <Chip>{`semente ${atual.seed}`}</Chip>
            {meta.opts.steps !== undefined && <Chip>{`${meta.opts.steps} passos · CFG ${meta.opts.cfg}`}</Chip>}
          </div>

          <div className="mt-4 grid grid-cols-2 gap-2">
            <button
              onClick={() => decidir(true)}
              className={`rounded-lg border px-2 py-2 text-xs ${atual.status === "mantida" ? "border-emerald-500 bg-emerald-500/15 text-emerald-300" : "border-line text-muted hover:bg-raised hover:text-fg"}`}
            >
              <Check className="mr-1 inline size-3.5" />
              {atual.status === "mantida" ? "Mantida" : "Manter"}
            </button>
            <button
              onClick={() => decidir(false)}
              className={`rounded-lg border px-2 py-2 text-xs ${atual.status === "descartada" ? "border-line bg-raised text-faint" : "border-line text-muted hover:bg-raised hover:text-fg"}`}
              title="Vai para descartadas/ e some sozinha depois do prazo"
            >
              <X className="mr-1 inline size-3.5" />
              {atual.status === "descartada" ? "Descartada" : "Descartar"}
            </button>
          </div>

          <p className="mb-1 mt-5 px-1 text-[11px] uppercase tracking-wider text-faint">Continuar a partir daqui</p>
          <button className={acao} onClick={async () => props.onUsarQuadro((await player.current?.capturar()) ?? null, "inicio")}>
            <Camera className="size-3.5" /> Usar este quadro como início
          </button>
          <button className={acao} onClick={async () => props.onUsarQuadro((await player.current?.capturar()) ?? null, "fim")}>
            <Camera className="size-3.5" /> Usar este quadro como fim
          </button>
          <button className={acao} onClick={() => props.onReaproveitar(atual.seed)}>
            <Refresh className="size-3.5" /> Refazer com esta semente
          </button>

          <p className="mb-1 mt-4 px-1 text-[11px] uppercase tracking-wider text-faint">Arquivo</p>
          <button className={acao} onClick={salvarQuadro}>
            <Download className="size-3.5" /> Salvar este quadro (PNG)
          </button>
          <button className={acao} onClick={() => props.onAbrir(atual.path, "reveal")}>
            <FolderOpen className="size-3.5" /> Mostrar na pasta
          </button>
          <button className={acao} onClick={() => props.onAbrir(atual.path, "open")}>
            <ExternalLink className="size-3.5" /> Abrir no player do sistema
          </button>
          {salvo && <p className="px-2.5 pt-1 text-[11px] text-emerald-400">{salvo}</p>}
        </div>
        <div className="border-t border-line px-3 py-2 text-[11px] text-faint">
          <span className="font-mono">←→</span> quadro · <span className="font-mono">Espaço</span> tocar · <span className="font-mono">↑↓</span> tomada ·{" "}
          <span className="font-mono">?</span> atalhos
        </div>
      </aside>
    </div>,
    document.body,
  );
}

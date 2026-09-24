import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, uploadReferencia } from "../api";
import type { ImageOpts, LocalModel, LocalState, LoteImagem, LoteMeta, Message, ModoVideo, PedidoMeta, SeedMode } from "../types";
import {
  ArrowUp, Camera, Check, ChevronDown, Copy, Download, ExternalLink, Film, FolderOpen, Image, Plus, Refresh,
  Raio, Sliders, Square, TelaCheia, Trocar, X,
} from "./icons";
import { BotaoEnviar, CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt, larguraNumero, numeroPilula, pilula, pilulaLigada } from "./Composer";
import { btn, btnPrimary, Field, input, Num, SAMPLERS } from "./LocalPanel";
import SelosModo from "./SelosModo";
import { PROPORCOES, duracoesDe, estimarTempo, quadrosDe, tamanhosDe, type Proporcao, type Tamanhos } from "./videoConta";
import { A_REFAZER, AnelProgresso, Chip, CORES, duracao, Fundo, Liquido, rotuloSementes, SEEDS, urlDa, velocidade } from "./ImagensView";
import ModelPicker from "./ModelPicker";
import { VideoPlayer, type VideoPlayerApi } from "./VideoPlayer";
import { AmpliarArquivo, PainelAmpliar } from "./AmpliarVideo";

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

const segundosDe = (frames: number, fps: number) => (fps ? frames / fps : 0);
const fmtS = (s: number) => `${s.toFixed(1).replace(".", ",")} s`;
const rodando = (m: Message) => m.role === "assistant" && m.status === "running";

function tamanhoAtual(o: ImageOpts, tamanhos: Tamanhos): { prop: Proporcao | null; qual: string | null } {
  for (const q of Object.keys(tamanhos))
    for (const p of PROPORCOES)
      if (tamanhos[q][p][0] === o.width && tamanhos[q][p][1] === o.height) return { prop: p, qual: q };
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
      // até a duração exata: no webm ela é o início do último quadro, e "− 1 ms" pegava o penúltimo
      v.currentTime = tempo === "fim" ? v.duration : Math.min(tempo, v.duration);
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
  const [ampliarPc, setAmpliarPc] = useState(false); // aba "Ampliar vídeo": um vídeo do PC, não uma geração
  const [slots, setSlots] = useState<Slots>([null, null]);
  const [prompt, setPrompt] = useState("");
  const [count, setCount] = useState(1);
  const [seedMode, setSeedMode] = useState<SeedMode>("incremental");
  const [abrirAjustes, setAbrirAjustes] = useState(false);
  const [perguntando, setPerguntando] = useState(false);
  const [melhorando, setMelhorando] = useState(false);
  const [foco, setFoco] = useState<{ lote: number; item: number } | null>(null);
  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState(""); // troca automática de modelo: dizer, não fazer em silêncio
  // Acelerador (LoRA de poucos passos) publicado para o modelo da vez, e os ajustes de antes de ligá-lo
  const [acelerador, setAcelerador] = useState<{ arquivos: { path: string; gb: number; presente: string }[]; motivo: string } | null>(null);
  const antesDoAcelerador = useRef<Partial<ImageOpts> | null>(null);
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
      loras: p.loras ?? [],
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

  // A tela fica de olho enquanto algo pode mudar o que ela mostra: sem modelo (o kit baixando aparece
  // sozinho), GPU ocupada por outra geração (o Gerar destrava quando ela acaba) ou download em curso.
  const precisaVigiar =
    !!st && (!st.video_models.length || st.image_busy || st.jobs.some((j) => j.status === "running"));
  useEffect(() => {
    if (!precisaVigiar) return;
    const t = setInterval(carregarLocal, 4000);
    return () => clearInterval(t);
  }, [precisaVigiar, carregarLocal]);
  // O acelerador do modelo da vez; de novo quando a lista de LoRAs muda (download terminou)
  const nLoras = st?.loras?.length ?? 0;
  useEffect(() => {
    setAcelerador(null);
    if (!modelo) return;
    let vivo = true;
    api
      .get<{ arquivos: { path: string; gb: number; presente: string }[]; motivo: string }>(
        `/local/video/aceleradores?model=${encodeURIComponent(modelo)}`,
      )
      .then((r) => vivo && setAcelerador(r))
      .catch(() => {}); // sem acelerador a pílula só não aparece
    return () => {
      vivo = false;
    };
  }, [modelo, nLoras]);

  // e quando a tomada desta aba termina, o estado da GPU e os ajustes podem ter mudado
  useEffect(() => {
    if (!ocupado) carregarLocal();
  }, [ocupado, carregarLocal]);

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

  // Estimativa pelo que esta máquina já mediu com este modelo, em qualquer conversa (ver estimarTempo).
  const estimativa = useMemo(() => {
    if (!o || !st || !atual?.chave) return null;
    const e = estimarTempo(st.tempos_video ?? [], atual.chave, o);
    return e && { s: e.s * count, minimo: e.minimo };
  }, [st, atual, o, count]);

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
        if (outro) {
          aplicarModelo(outro);
          setAviso(`Troquei para o ${outro.req?.nome ?? outro.name}, que faz ${quer === "i2v" ? "imagem → vídeo" : "primeiro e último quadro"}.`);
        }
        else return mostrarErro(`Nenhum modelo de vídeo nas pastas faz ${quer === "i2v" ? "imagem → vídeo" : "primeiro e último quadro"}. Baixe um em IA local › Baixar.`);
      }
      setModo(quer);
      texto.current?.focus();
    } catch (e: any) {
      mostrarErro(e.message);
    }
  }

  async function gerar(confirm = false) {
    // As mesmas travas do botão: pelo Enter, uma 2ª geração subia outro sd-cli disputando a VRAM.
    if (!o || !st || !prompt.trim() || !modelo || ocupado || st.image_busy || !st.runtimes.sd.installed) return;
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

  /** Liga a LoRA de poucos passos com os ajustes que ela pede (os passos saem do arquivo; CFG 1, que é o
   *  que a destilação troca) e, ao desligar, devolve os ajustes de antes. */
  function alternarAcelerador() {
    if (!o || !acelerador || !st) return;
    const caminhos = acelArquivos;
    if (acelerando) {
      const antes = antesDoAcelerador.current ?? {};
      setO({ ...o, ...antes, loras: (o.loras ?? []).filter((l) => !caminhos.some((p) => mesmo(p, l.path))) });
      antesDoAcelerador.current = null;
      return;
    }
    const info = st.loras.filter((l) => caminhos.some((p) => mesmo(p, l.path)));
    const passos = Math.max(0, ...info.map((l) => l.passos)) || o.steps;
    antesDoAcelerador.current = { steps: o.steps, cfg: o.cfg, high_noise_steps: o.high_noise_steps, high_noise_cfg: o.high_noise_cfg };
    setO({
      ...o, steps: passos, cfg: 1, high_noise_cfg: 1, high_noise_steps: -1,  // -1: o sd.cpp divide os passos entre os dois
      loras: [...(o.loras ?? []).filter((l) => !caminhos.some((p) => mesmo(p, l.path))), ...caminhos.map((path) => ({ path, peso: 1 }))],
    });
  }

  async function baixarAcelerador() {
    try {
      await api.post("/local/video/acelerador", { model: modelo });
      setAviso("Baixando o acelerador: quando terminar, o ⚡ liga com um clique.");
      carregarLocal();
    } catch (e: any) {
      mostrarErro(e.message);
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
  const tamanhos = tamanhosDe(atual?.req);
  const qualidades = Object.keys(tamanhos);
  const { prop, qual } = tamanhoAtual(o, tamanhos);
  const duracoes = duracoesDe(atual?.req, o.fps);
  const compativeis = (st.loras ?? []).filter((l) => l.wan && !!atual?.dim && l.dim === atual.dim);
  const mesmo = (a: string, b: string) => a.replace(/\//g, "\\").toLowerCase() === b.replace(/\//g, "\\").toLowerCase();
  const acelArquivos = (acelerador?.arquivos ?? []).map((a) => a.presente).filter(Boolean) as string[];
  const acelPronto = !!acelerador?.arquivos.length && acelArquivos.length === acelerador.arquivos.length;
  const acelerando = acelPronto && acelArquivos.every((p) => o.loras?.some((l) => mesmo(l.path, p)));
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
              compativeis={compativeis}
              llm={llm}
              onLlm={setLlm}
              onFechar={() => setAbrirAjustes(false)}
            />
          )}

          <div
            onPaste={(e) => {
              const f = [...e.clipboardData.files].find((x) => x.type.startsWith("image/"));
              if (!f || modo === "t2v" || ampliarPc) return;
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
                        aria-checked={!ampliarPc && modo === m.id}
                        disabled={!pode}
                        onClick={() => {
                          setModo(m.id);
                          setAmpliarPc(false);
                        }}
                        title={pode ? m.dica : `${atual?.req?.nome ?? "Este modelo"} não faz ${m.curto}. ${
                          m.id === "t2v" ? "Use um T2V ou o TI2V 5B." : m.id === "i2v" ? "Use o TI2V 5B ou um I2V." : "Use o Wan2.1 FLF2V."}`}
                        className={`rounded-full px-3 py-1 transition-colors ${
                          !ampliarPc && modo === m.id ? "bg-raised text-fg" : "text-muted hover:text-fg"
                        } disabled:cursor-not-allowed disabled:text-faint/60 disabled:hover:text-faint/60`}
                      >
                        {m.rotulo}
                      </button>
                    );
                  })}
                </div>
                <button
                  onClick={() => setAmpliarPc((v) => !v)}
                  aria-pressed={ampliarPc}
                  title="Mais resolução (e, se quiser, o dobro de quadros) para qualquer vídeo do PC"
                  className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors ${
                    ampliarPc ? "border-[#454545] bg-raised text-fg" : "border-line text-muted hover:text-fg"
                  }`}
                >
                  <TelaCheia className="size-3.5" /> Ampliar vídeo
                </button>
                {ampliarPc ? (
                  <span className="text-[11px] text-muted">Qualquer vídeo do PC: o original não muda.</span>
                ) : aviso ? (
                  <span className="flex items-center gap-1.5 text-[11px] text-sky-300" role="status">
                    {aviso}
                    <button onClick={() => setAviso("")} aria-label="Dispensar aviso" className="rounded p-0.5 text-sky-300/70 hover:text-sky-200">
                      <X className="size-3" />
                    </button>
                  </span>
                ) : (
                  <span className="text-[11px] text-muted">{MODOS.find((m) => m.id === modo)?.dica}</span>
                )}
              </div>

              {ampliarPc ? (
                <AmpliarArquivo
                  ensureConversation={props.ensureConversation}
                  onError={mostrarErro}
                  onPronto={(conv) => {
                    setAmpliarPc(false);
                    props.onConversationChanged();
                    carregarConversa(conv);
                  }}
                />
              ) : (
              <>
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
                  <p className="mb-7 ml-1 max-w-56 text-[11px] leading-snug text-muted">
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
                    <div className="w-60 space-y-2 p-1">
                      <Segmento
                        rotulo="Proporção"
                        opcoes={PROPORCOES}
                        valor={prop}
                        onValor={(p) => {
                          const [w, h] = tamanhos[qual ?? qualidades[0]][p];
                          set("width", w);
                          set("height", h);
                        }}
                      />
                      {qualidades.length > 1 && (
                        <Segmento
                          rotulo="Qualidade"
                          opcoes={qualidades}
                          valor={qual}
                          onValor={(q) => {
                            const [w, h] = tamanhos[q][prop ?? "16:9"];
                            set("width", w);
                            set("height", h);
                          }}
                        />
                      )}
                      <TamanhoLivre
                        w={o.width}
                        h={o.height}
                        passo={atual?.req?.multiplo ?? 16}
                        ativo={!prop}
                        onAplicar={(w, h) => {
                          set("width", w);
                          set("height", h);
                        }}
                      />
                      <p className="px-1 text-[11px] leading-snug text-muted">{dicaQualidade(st.gpu_video, atual, tamanhos)}</p>
                    </div>
                  )}
                </Menu>
                <Menu rotulo={<>{fmtS(seg)}</>} titulo={`${o.frames} quadros a ${o.fps} fps`}>
                  {() => (
                    <div className="w-56 space-y-2 p-1">
                      <Segmento
                        rotulo="Duração"
                        opcoes={duracoes.map(fmtS)}
                        valor={duracoes.map(fmtS).find((d, i) => quadrosDe(duracoes[i], o.fps) === o.frames) ?? null}
                        onValor={(d) => set("frames", quadrosDe(duracoes[duracoes.map(fmtS).indexOf(d)], o.fps))}
                      />
                      <DuracaoLivre segundos={seg} fps={o.fps} onAplicar={(q) => set("frames", q)} />
                      <p className="px-1 text-[11px] leading-snug text-muted">
                        {o.frames} quadros a {o.fps} fps. {atual?.req?.nome ?? "O modelo"} foi treinado com clipes de até{" "}
                        {fmtS((atual?.req?.quadros_treino ?? 81) / (o.fps || 16))}: mais que isso tende a repetir ou degradar.
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
                {acelerador && acelerador.arquivos.length > 0 && (() => {
                  const faltaGb = acelerador.arquivos.filter((a) => !a.presente).reduce((s, a) => s + a.gb, 0);
                  const baixandoAcel = st.jobs.some((j) => j.status === "running" && acelerador.arquivos.some((a) => j.name.endsWith(a.path.split("/").pop()!)));
                  const passos = Math.max(0, ...st.loras.filter((l) => acelArquivos.some((p) => mesmo(p, l.path))).map((l) => l.passos));
                  return acelPronto ? (
                    <button
                      onClick={alternarAcelerador}
                      aria-pressed={acelerando}
                      title={acelerando
                        ? `LoRA de ${passos} passos ligada (CFG 1). Clique para voltar aos ajustes de antes.`
                        : `Liga a LoRA de ${passos || "poucos"} passos do lightx2v: bem mais rápido, com qualidade próxima.`}
                      className={`${pilula} ${acelerando ? "border-amber-400/50 bg-amber-400/10 text-amber-200 hover:bg-amber-400/15" : ""}`}
                    >
                      <Raio className="size-3.5" />
                      {acelerando ? `${passos} passos` : "Acelerar"}
                    </button>
                  ) : (
                    <button
                      onClick={baixarAcelerador}
                      disabled={baixandoAcel}
                      title="LoRA de destilação do lightx2v para este modelo: gera em poucos passos"
                      className={pilula}
                    >
                      <Raio className="size-3.5" />
                      {baixandoAcel ? "Baixando acelerador…" : `Baixar acelerador (${faltaGb.toFixed(1).replace(".", ",")} GB)`}
                    </button>
                  );
                })()}
                <button
                  onClick={melhorar}
                  disabled={!prompt.trim() || !llm.model || melhorando}
                  title={llm.model ? `Reescrever o prompt com ${llm.model}: sujeito, ação, câmera, luz (troque o modelo nos Ajustes)` : "Escolha nos Ajustes o modelo que reescreve"}
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
                  {estimativa && (
                    <span
                      className="shrink-0 text-[11px] tabular-nums text-muted"
                      title={estimativa.minimo
                        ? "Pelo tempo medido num tamanho diferente com este modelo; com mais medições a conta fica exata"
                        : "Pelo tempo que este modelo levou nesta máquina"}
                    >
                      {estimativa.minimo ? "≥ " : "≈ "}{duracao(estimativa.s).replace("~", "")}
                    </span>
                  )}
                  <BotaoEnviar
                    onEnviar={() => gerar()}
                    desabilitado={!prompt.trim() || !modelo || semRuntime || st.image_busy || ocupado || refs.length < precisaQuadros}
                    titulo={st.image_busy || ocupado ? "Já tem geração em andamento" : refs.length < precisaQuadros ? "Faltam os quadros" : "Gerar"}
                  />
                </DireitaPrompt>
              </RodapePrompt>
              </>
              )}
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

/** A dica sai da conta, não de faixas fixas: o modelo cabe inteiro na VRAM da GPU do sd.cpp? E quanto a
 *  qualidade maior multiplica os pixels (o tempo cresce pelo menos nessa proporção). */
function dicaQualidade(gpu: LocalState["gpu_video"], modelo: LocalModel | undefined, tamanhos: Tamanhos): string {
  const gb = (n: number) => `${n.toFixed(1).replace(".", ",")} GB`;
  const partes: string[] = [];
  if (gpu.gb && modelo) {
    const quem = gpu.nome?.replace(/\(TM\)|\(R\)/g, "").replace(/\s+Graphics$/, "").trim() || "sua GPU";
    const tamanho = modelo.size / 1e9;
    partes.push(tamanho <= gpu.gb * (gpu.folga ?? 0.85)
      ? `O modelo (${gb(tamanho)}) cabe inteiro na ${quem} (${gb(gpu.gb)}).`
      : `O modelo (${gb(tamanho)}) é maior que a ${quem} (${gb(gpu.gb)}): vai com pesos na RAM, mais lento.`);
  }
  const qs = Object.keys(tamanhos);
  if (qs.length > 1) {
    const px = (q: string) => tamanhos[q]["16:9"][0] * tamanhos[q]["16:9"][1];
    const [menor, maior] = [qs[0], qs[qs.length - 1]];
    partes.push(`${maior} tem ${(px(maior) / px(menor)).toFixed(1).replace(".", ",")}× os pixels de ${menor}: pelo menos isso no tempo e na memória.`);
  }
  return partes.join(" ") || "Resolução maior pede mais memória e mais tempo.";
}

/** Duração livre em segundos; vira quadros 4k+1 ao confirmar. */
function DuracaoLivre(props: { segundos: number; fps: number; onAplicar: (quadros: number) => void }) {
  const [v, setV] = useState(props.segundos.toFixed(1).replace(".", ","));
  useEffect(() => setV(props.segundos.toFixed(1).replace(".", ",")), [props.segundos]);
  const aplicar = () => {
    const s = Number(v.replace(",", "."));
    if (s > 0) props.onAplicar(quadrosDe(s, props.fps));
  };
  return (
    <div className="flex items-center gap-1.5 px-1">
      <span className="text-[11px] text-faint">Personalizada</span>
      <input
        aria-label="Duração em segundos"
        inputMode="decimal"
        value={v}
        onChange={(e) => setV(e.target.value.replace(/[^\d,.]/g, ""))}
        onBlur={aplicar}
        onKeyDown={(e) => e.key === "Enter" && aplicar()}
        className="w-14 rounded-md border border-line bg-raised px-1.5 py-1 text-right tabular-nums text-fg outline-none focus:border-[#555]"
      />
      <span className="text-[11px] text-faint">s</span>
    </div>
  );
}

/** Tamanho livre: arredonda para o múltiplo que o modelo exige (vem da variante) só ao confirmar, para não
 *  brigar com quem ainda está digitando. */
function TamanhoLivre(props: { w: number; h: number; passo: number; ativo: boolean; onAplicar: (w: number, h: number) => void }) {
  const [w, setW] = useState(String(props.w));
  const [h, setH] = useState(String(props.h));
  useEffect(() => {
    setW(String(props.w));
    setH(String(props.h));
  }, [props.w, props.h]);
  const encaixa = (v: string) => Math.min(1920, Math.max(props.passo * 8, Math.round((Number(v) || 0) / props.passo) * props.passo));
  const aplicar = () => props.onAplicar(encaixa(w), encaixa(h));
  const campo = "w-16 rounded-md border border-line bg-raised px-1.5 py-1 text-right tabular-nums text-fg outline-none focus:border-[#555]";
  return (
    <div>
      <p className={`mb-1 px-1 text-[11px] ${props.ativo ? "text-fg" : "text-faint"}`}>Personalizada</p>
      <div className="flex items-center gap-1.5 px-1">
        {[
          [w, setW, "Largura"],
          [h, setH, "Altura"],
        ].map(([valor, setValor, rotulo], i) => (
          <label key={rotulo as string} className="contents">
            {i === 1 && <span className="text-faint">×</span>}
            <input
              aria-label={rotulo as string}
              inputMode="numeric"
              value={valor as string}
              onChange={(e) => (setValor as (v: string) => void)(e.target.value.replace(/\D/g, ""))}
              onBlur={aplicar}
              onKeyDown={(e) => e.key === "Enter" && aplicar()}
              className={campo}
            />
          </label>
        ))}
        <span className="ml-1 text-[10px] leading-tight text-faint">múltiplos de {props.passo}</span>
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
              aria-label="Tirar esta imagem"
              className="absolute right-1 top-1 grid size-5 place-items-center rounded-full bg-black/70 text-white/80 opacity-0 transition group-hover:opacity-100 hover:text-white focus-visible:opacity-100"
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
  compativeis: LocalState["loras"];
  llm: { provider: string; model: string };
  onLlm: (l: { provider: string; model: string }) => void;
  onFechar: () => void;
}) {
  const { o, set, st } = props;
  const ativa = (path: string) => (o.loras ?? []).find((l) => l.path.toLowerCase() === path.toLowerCase());
  const alternar = (path: string) =>
    set("loras", ativa(path) ? (o.loras ?? []).filter((l) => l.path.toLowerCase() !== path.toLowerCase()) : [...(o.loras ?? []), { path, peso: 1 }]);
  const peso = (path: string, v: number) => set("loras", (o.loras ?? []).map((l) => (l.path.toLowerCase() === path.toLowerCase() ? { ...l, peso: v } : l)));
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
          <Field
            label="LoRAs"
            hint={props.compativeis.length
              ? "Só as que servem para este modelo (pela dimensão dos tensores). Peso 1 = como foi treinada."
              : "Nenhuma LoRA nas pastas serve para este modelo."}
          >
            {props.compativeis.length > 0 && (
              <div className="flex max-h-36 flex-col gap-1 overflow-y-auto rounded-lg border border-line p-1.5">
                {props.compativeis.map((l) => {
                  const a = ativa(l.path);
                  return (
                    <div key={l.path} className="flex items-center gap-2 rounded-md px-1 py-0.5 hover:bg-raised">
                      <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                        <input type="checkbox" checked={!!a} onChange={() => alternar(l.path)} className="accent-white" />
                        <span className={`min-w-0 flex-1 truncate ${a ? "text-fg" : "text-muted"}`} title={l.path}>{l.name}</span>
                      </label>
                      {l.passos > 0 && <span className="shrink-0 rounded-full bg-amber-400/10 px-1.5 text-[10px] text-amber-200">{l.passos} passos</span>}
                      {l.ruido && <span className="shrink-0 text-[10px] text-faint">{l.ruido === "high" ? "HighNoise" : "LowNoise"}</span>}
                      {a && (
                        <input
                          type="number"
                          step={0.1}
                          min={0}
                          max={2}
                          value={a.peso}
                          onChange={(e) => peso(l.path, Number(e.target.value) || 0)}
                          aria-label={`Peso de ${l.name}`}
                          className="w-14 rounded-md border border-line bg-raised px-1.5 py-0.5 text-right tabular-nums text-fg"
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </Field>
          <Field label="Modelo do “Melhorar”" hint="O que reescreve o prompt: um LLM rápido basta. Não é o que gera o vídeo.">
            <ModelPicker provider={props.llm.provider} model={props.llm.model} onChange={(provider, model) => props.onLlm({ provider, model })} />
          </Field>
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
        {meta.opts.ampliacao && <Chip>{`ampliado ${meta.opts.ampliacao.fator}×${meta.opts.ampliacao.suavizar ? " · suavizado" : ""}`}</Chip>}
        {(meta.opts.loras ?? []).length > 0 && (
          <Chip>
            <span title={(meta.opts.loras ?? []).map((l) => `${l.path} × ${l.peso}`).join("\n")}>
              {(meta.opts.loras ?? []).length === 1 ? "1 LoRA" : `${(meta.opts.loras ?? []).length} LoRAs`}
            </span>
          </Chip>
        )}
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
                {sel.size ? <Check className="mr-1 inline size-3.5" /> : <X className="mr-1 inline size-3.5" />}
                {sel.size
                  ? `Manter ${sel.size} · descartar ${aprovaveis.length - sel.size}`
                  : aprovaveis.length === 1 ? "Descartar" : `Descartar todas (${aprovaveis.length})`}
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
            Gerar as que faltaram ({faltam})
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
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              props.onAbrir();
            }
          }}
          aria-label={`Abrir no player: tomada com semente ${item.seed}${props.marcado ? ", marcada para manter" : ""}`}
          title="Clique para abrir no player · arraste para usar o quadro como imagem inicial"
          className="relative cursor-pointer bg-black outline-none focus-visible:ring-2 focus-visible:ring-sky-400/70"
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
            // A 1ª linha é a explicação em português (dica_de_falha no backend); o log inteiro vai no copiar.
            <div className="flex max-w-[90%] flex-col items-center gap-2 text-center">
              <span className="line-clamp-4 text-[11px] leading-snug text-red-300" title={item.error}>
                {item.error.split("\n")[0]}
              </span>
              <button
                onClick={() => navigator.clipboard.writeText(item.error).catch(() => {})}
                className="rounded-full border border-red-900/70 px-2 py-0.5 text-[10px] text-red-200/80 hover:bg-red-950/40 hover:text-red-100"
              >
                Copiar o erro completo
              </button>
            </div>
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

      {/* Só com a prévia na tela: antes dela o spinner do centro já diz que está carregando (eram três indicadores). */}
      {comPrevia && item.preview && <AnelProgresso pct={pct} />}
      {temArquivo && (
        <button
          onClick={props.onMarcar}
          title={props.marcado ? "Desmarcar" : "Marcar para manter"}
          aria-label="Manter esta tomada"
          aria-pressed={props.marcado}
          className={`absolute left-2 top-2 grid size-6 place-items-center rounded-full border outline-none focus-visible:ring-2 focus-visible:ring-sky-400/70 ${
            props.marcado ? "border-emerald-400 bg-emerald-500 text-black" : "border-white/25 bg-black/60 text-white/45 hover:text-white"
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
            <button onClick={props.onSemente} title="Refazer com esta semente" className="rounded px-1 py-0.5 tabular-nums text-faint hover:bg-raised hover:text-fg">
              #{item.seed}
            </button>
            <button
              onClick={props.onContinuar}
              title="Continuar a cena: o último quadro vira a imagem inicial"
              aria-label="Continuar a cena a partir do último quadro"
              className="rounded p-1 text-faint hover:bg-raised hover:text-fg"
            >
              <Camera className="size-3.5" />
            </button>
            <button onClick={props.onPasta} title="Mostrar na pasta" aria-label="Mostrar na pasta" className="rounded p-1 text-faint hover:bg-raised hover:text-fg">
              <FolderOpen className="size-3.5" />
            </button>
          </>
        )}
        {comPrevia ? (
          <span className="shrink-0 tabular-nums text-sky-300">
            {item.s_passo ? `${velocidade(item.s_passo, item.unidade)} · ${duracao(item.restante ?? 0)}` : "preparando"}
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
  // Fecha no clique fora do vídeo, mas só se o clique também COMEÇOU fora: arrastar a linha do tempo e
  // soltar no fundo escuro não pode fechar o player no meio do scrub.
  const comecouFora = useRef(false);
  const [anuncio, setAnuncio] = useState(""); // leitor de tela: "Tomada 2 mantida"
  const decidirRef = useRef<(manter: boolean, avancar?: boolean) => void>(() => {});
  const w = meta.opts.width ?? 832;
  const h = meta.opts.height ?? 480;
  const fps = meta.opts.fps ?? 16;
  const frames = meta.opts.frames ?? 33;
  const refs = pm?.refs ?? [];
  const [salvo, setSalvo] = useState("");
  const [ampliar, setAmpliar] = useState(false);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      if (e.key === "Escape" && !document.fullscreenElement) props.onFechar();
      else if (e.key === "ArrowUp" && pos > 0) (e.preventDefault(), props.onIndice(prontos[pos - 1].i));
      else if (e.key === "ArrowDown" && pos < prontos.length - 1) (e.preventDefault(), props.onIndice(prontos[pos + 1].i));
      // triagem sem mouse: decide e já passa para a próxima tomada
      else if (e.key === "m" || e.key === "M") (e.preventDefault(), decidirRef.current(true, true));
      else if (e.key === "x" || e.key === "X") (e.preventDefault(), decidirRef.current(false, true));
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [pos, prontos.length, props.onFechar, props.onIndice]);

  // Foco de teclado: entra no diálogo e volta para onde estava (o cartão) quando o foco fecha. No diálogo, não
  // no botão de fechar: com o foco nele, o Espaço (pausar) "clicava" o Fechar.
  const dialogo = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const antes = document.activeElement as HTMLElement | null;
    dialogo.current?.focus();
    return () => antes?.focus?.();
  }, []);

  if (!atual) return null;

  async function decidir(manter: boolean, avancar = false) {
    try {
      // `apenas`: só esta tomada muda. Mandar as outras em "keep" as marcava como mantidas de carona.
      await api.post(`/imagens/${props.resposta.id}/decidir`, { keep: manter ? [atual.path] : [], apenas: [atual.path] });
      setAnuncio(manter ? `Tomada ${pos + 1} mantida` : `Tomada ${pos + 1} descartada`);
      props.onMudou();
      if (avancar && pos < prontos.length - 1) props.onIndice(prontos[pos + 1].i);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  decidirRef.current = decidir;

  // Na pasta de imagens, com nome que diz de onde veio: o <a download> do Electron abria um "Salvar como"
  // e o aviso de "salvo em Downloads" nem sempre era verdade.
  async function salvarQuadro() {
    const b = await player.current?.capturar();
    if (!b) return props.onError("Não deu para tirar o quadro deste vídeo.");
    try {
      const base = atual.path.split(/[\\/]/).pop()?.replace(/\.webm$/, "") ?? "video";
      setSalvo(await subirQuadro(b, `${base}-quadro.png`));
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  const acao = "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs text-muted hover:bg-raised hover:text-fg";

  return createPortal(
    <div
      ref={dialogo}
      tabIndex={-1}
      className="fixed inset-0 z-50 flex bg-black/85 outline-none backdrop-blur-md"
      role="dialog"
      aria-modal="true"
      aria-label="Player de vídeo"
      onPointerDown={(e) => (comecouFora.current = e.target === e.currentTarget || (e.target as HTMLElement).dataset.fundo === "1")}
      onClick={(e) => {
        const fora = e.target === e.currentTarget || (e.target as HTMLElement).dataset.fundo === "1";
        if (fora && comecouFora.current) props.onFechar();
      }}
    >
      {/* sem cursor de zoom nem title aqui: os dois vazavam para o vídeo (o fundo é o pai dele) */}
      <div data-fundo="1" className="flex min-w-0 flex-1 items-center justify-center p-10">
        <VideoPlayer
          key={atual.path}
          ref={player}
          src={urlDa(atual.path)}
          fps={fps}
          quadros={frames}
          autoPlay
          tecladoGlobal
          atalhosExtras={[["↑ · ↓", "tomada anterior · próxima"], ["M · X", "manter · descartar e ir à próxima"], ["Esc", "fechar"]]}
          marcas={refs.length === 2}
          className="rounded-xl shadow-2xl shadow-black"
          // cabe inteiro com folga em volta: 88% da área, e a altura deixa respiro em cima e embaixo
          style={{ aspectRatio: w / h, width: `min(88%, calc((100vh - 200px) * ${w / h}))` }}
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
            <button onClick={props.onFechar} title="Fechar (Esc)" aria-label="Fechar o player" className="rounded-md p-1 text-muted hover:bg-raised hover:text-fg">
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

          <p className="mb-1 mt-4 px-1 text-[11px] uppercase tracking-wider text-faint">Ampliar</p>
          {ampliar ? (
            <PainelAmpliar
              w={w}
              h={h}
              fps={fps}
              onError={props.onError}
              enviar={async (c) => {
                await api.post(`/imagens/${props.resposta.id}/ampliar`, { path: atual.path, ...c });
                props.onMudou();
                props.onFechar();
              }}
            />
          ) : (
            <button className={acao} onClick={() => setAmpliar(true)} title="Mais resolução (ESRGAN quadro a quadro, ou Lanczos) e, se quiser, o dobro de quadros">
              <TelaCheia className="size-3.5" /> Ampliar resolução…
            </button>
          )}

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
          <p className="sr-only" aria-live="polite">{anuncio}</p>
          {salvo && (
            <p className="flex items-center gap-1.5 px-2.5 pt-1 text-[11px] text-emerald-400" title={salvo}>
              <Check className="size-3 shrink-0" />
              <span className="min-w-0 truncate">Salvo: {salvo.split(/[\\/]/).pop()}</span>
              <button className="shrink-0 text-muted underline hover:text-fg" onClick={() => props.onAbrir(salvo, "reveal")}>
                Mostrar na pasta
              </button>
            </p>
          )}
        </div>
        <div className="border-t border-line px-3 py-2 text-[11px] text-faint">
          <span className="font-mono">←→</span> quadro · <span className="font-mono">Espaço</span> tocar · <span className="font-mono">↑↓</span> tomada ·{" "}
          <span className="font-mono">M · X</span> manter · descartar · <span className="font-mono">?</span> atalhos
        </div>
      </aside>
    </div>,
    document.body,
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api, auth } from "../api";
import type { Message } from "../types";
import CartaoEstado, { botaoEstado, botaoEstadoPrimario } from "./CartaoEstado";
import { campoPrompt } from "./Composer";
import { BarraTopo, Caixa, numeroCaixa, Secao } from "./ImagensView";
import { Check, Download, Edit, Pause, Play, Plus, Refresh, Search, Trash, X } from "./icons";
import ModelSearch from "./ModelSearch";
import Saudacao from "./Saudacao";

// Tela Voz: texto para fala com motores plugáveis (backend/app/tts.py): F5-TTS/E2-TTS e Fish Audio. Cada motor tem o
// seu runtime, instalado só quando pedido; o modelo diz qual motor usa. Cada geração é um par de mensagens da conversa
// kind="tts"; a do assistente leva meta.tts (estado, fase, arquivo, duração...). Mesma casca de Imagem e Vídeo: faixa
// do topo, falas no meio, composer embaixo e o painel Parâmetros à direita.

type Motor = "f5" | "fish";
type Modelo = {
  nome: string; motor: Motor; modelo?: string; arquitetura?: string; ckpt?: string; vocab?: string; minusculas?: boolean; numeros?: string;
  a_baixar?: boolean; // ainda não está no disco: desce na primeira geração
};
type Voz = { id: string; nome: string; texto: string; caminho: string };
type InfoMotor = { nome: string; instalado: boolean; gb: number; instalando: string };
type Estado = {
  motores: Record<Motor, InfoMotor>; gpu: string; backend: string; modelos: Modelo[]; vozes: Voz[]; arquiteturas: string[];
  carregado: { nome: string; dispositivo: string } | null;
  baixando: Job[]; // downloads de modelo de voz pela janela de modelos
};
type Job = { id: string; status: string; detail: string; error: string; name?: string; done?: number; total?: number };

const POLL_MS = 1500;
const audioUrl = (p: string) => `/api/tts/arquivo?path=${encodeURIComponent(p)}`;
const textoCaixa = "w-full min-w-0 bg-transparent text-[12.5px] text-fg outline-none placeholder:text-faint";
const linkPainel = "text-[11.5px] text-faint hover:text-fg";
// Sugestões para preencher o formulário (não instalam nada).
const SUGESTOES: Modelo[] = [
  { nome: "Fish Audio S2-pro", motor: "fish", modelo: "fishaudio/s2-pro" },
  { nome: "F5-TTS v1", motor: "f5", arquitetura: "F5TTS_v1_Base", ckpt: "", vocab: "", minusculas: false, numeros: "" },
  { nome: "F5-TTS pt-br", motor: "f5", arquitetura: "F5TTS_Base", ckpt: "hf://firstpixel/F5-TTS-pt-br/pt-br/model_last.safetensors", vocab: "", minusculas: true, numeros: "pt_BR" },
];
const VAZIO: Record<Motor, Modelo> = {
  fish: { nome: "", motor: "fish", modelo: "" },
  f5: { nome: "", motor: "f5", arquitetura: "F5TTS_v1_Base", ckpt: "", vocab: "", minusculas: false, numeros: "" },
};
const TAGS = ["[sigh]", "[short pause]", "[chuckle]", "[laughing]", "[whisper]", "[excited]", "[sad]", "[emphasis]"];
const RODAPE: Record<Motor, string> = {
  fish: "O modelo fica na memória entre um áudio e outro e sai sozinho depois de 5 min parado. Colchetes no texto pedem emoção.",
  f5: "O modelo fica na memória entre um áudio e outro e sai sozinho depois de 5 min parado.",
};

const gb = (n = 0) => (n / 2 ** 30).toFixed(1).replace(".", ",");
const relogio = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

// ---------------------------------------------------------------- tocador

// Um áudio por vez na tela inteira: dar play num pausa o que estava tocando.
let tocandoAgora: HTMLAudioElement | null = null;
const ondas = new Map<string, number[]>();

/** Amplitude em `n` faixas (pico de cada uma), normalizada: o desenho da forma de onda. Cache pelo endereço. */
async function formaDeOnda(url: string, n: number): Promise<number[]> {
  const pronta = ondas.get(url);
  if (pronta) return pronta;
  const dados = await (await fetch(url, { headers: auth() })).arrayBuffer();
  const ctx = new AudioContext();
  try {
    const canal = (await ctx.decodeAudioData(dados)).getChannelData(0);
    const passo = Math.max(1, Math.floor(canal.length / n));
    const picos = Array.from({ length: n }, (_, i) => {
      let m = 0;
      for (let j = i * passo; j < Math.min(canal.length, (i + 1) * passo); j++) m = Math.max(m, Math.abs(canal[j]));
      return m;
    });
    const topo = Math.max(...picos, 1e-6);
    const norm = picos.map((p) => Math.max(0.06, p / topo));
    ondas.set(url, norm);
    return norm;
  } finally {
    ctx.close();
  }
}

function useTocador(url: string) {
  const el = useRef<HTMLAudioElement | null>(null);
  const [tocando, setTocando] = useState(false);
  const [pos, setPos] = useState(0);
  const [dur, setDur] = useState(0);
  useEffect(() => {
    const a = new Audio(url);
    a.preload = "metadata";
    el.current = a;
    let quadro = 0;
    const segue = () => { setPos(a.currentTime); quadro = requestAnimationFrame(segue); };
    const tocou = () => { setTocando(true); quadro = requestAnimationFrame(segue); };
    const parou = () => { setTocando(false); cancelAnimationFrame(quadro); setPos(a.currentTime); };
    a.addEventListener("loadedmetadata", () => setDur(a.duration));
    a.addEventListener("play", tocou);
    a.addEventListener("pause", parou);
    a.addEventListener("ended", () => { parou(); a.currentTime = 0; setPos(0); });
    return () => { cancelAnimationFrame(quadro); a.pause(); if (tocandoAgora === a) tocandoAgora = null; el.current = null; };
  }, [url]);
  const alternar = () => {
    const a = el.current;
    if (!a) return;
    if (!a.paused) return a.pause();
    if (tocandoAgora && tocandoAgora !== a) tocandoAgora.pause();
    tocandoAgora = a;
    a.play().catch(() => {});
  };
  const ir = (seg: number) => {
    const a = el.current;
    if (!a || !dur) return;
    a.currentTime = Math.max(0, Math.min(dur, seg));
    setPos(a.currentTime);
  };
  return { tocando, pos, dur, alternar, ir };
}

const FAIXAS = 96;

/** Play, forma de onda (clique e setas levam a um ponto) e o tempo. */
function Tocador(props: { arquivo: string; duracao?: number }) {
  const url = audioUrl(props.arquivo);
  const t = useTocador(url);
  const [picos, setPicos] = useState<number[] | null>(() => ondas.get(url) ?? null);
  useEffect(() => { formaDeOnda(url, FAIXAS).then(setPicos).catch(() => setPicos([])); }, [url]);
  const dur = t.dur || props.duracao || 0;
  const feito = dur ? t.pos / dur : 0;
  return (
    <div className="flex items-center gap-3">
      <button onClick={t.alternar} aria-label={t.tocando ? "Pausar" : "Ouvir"}
              className="grid size-9 shrink-0 place-items-center rounded-full bg-accent text-accent-fg transition-[filter] hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus">
        {t.tocando ? <Pause className="size-3.5" /> : <Play className="size-3.5 translate-x-px" />}
      </button>
      <div role="slider" tabIndex={0} aria-label="Posição no áudio" aria-valuemin={0} aria-valuemax={Math.round(dur)} aria-valuenow={Math.round(t.pos)}
           aria-valuetext={`${relogio(t.pos)} de ${relogio(dur)}`}
           onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); t.ir(((e.clientX - r.left) / r.width) * dur); }}
           onKeyDown={(e) => {
             if (e.key === "ArrowRight") t.ir(t.pos + 2);
             else if (e.key === "ArrowLeft") t.ir(t.pos - 2);
             else if (e.key === " " || e.key === "Enter") { e.preventDefault(); t.alternar(); }
           }}
           className="group flex h-9 min-w-0 flex-1 cursor-pointer items-center gap-[2px] rounded-[6px] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus">
        {(picos?.length ? picos : Array.from({ length: FAIXAS }, () => 0.12)).map((p, i) => (
          <span key={i} style={{ height: `${Math.round(p * 100)}%` }}
                className={`min-h-[2px] flex-1 rounded-full transition-colors duration-150 ${
                  !picos ? "bg-line" : (i + 0.5) / FAIXAS <= feito ? "bg-accent" : "bg-line-strong group-hover:bg-muted/60"}`} />
        ))}
      </div>
      <span className="w-[76px] shrink-0 text-right font-mono text-[11.5px] tabular-nums text-muted">
        {relogio(t.pos)} <span className="text-faint">/ {relogio(dur)}</span>
      </span>
    </div>
  );
}

/** Só o play (a amostra da voz na lista do painel). */
function PlayPequeno(props: { arquivo: string }) {
  const t = useTocador(audioUrl(props.arquivo));
  return (
    <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); t.alternar(); }} aria-label={t.tocando ? "Pausar a amostra" : "Ouvir a amostra"}
            className={`grid size-6 shrink-0 place-items-center rounded-full border ${t.tocando ? "border-accent bg-accent text-accent-fg" : "border-line-strong text-muted hover:border-fg hover:text-fg"}`}>
      {t.tocando ? <Pause className="size-2.5" /> : <Play className="size-2.5 translate-x-px" />}
    </button>
  );
}

// ---------------------------------------------------------------- uma fala

function Fala(props: { pedido: Message; resposta: Message; onCancelar: () => void; onRefazer: (texto: string) => void; onSemente: (s: number) => void }) {
  const t = props.resposta.meta!.tts;
  const ajustes = t.temperatura != null ? `temperatura ${String(t.temperatura).replace(".", ",")}` : `${String(t.velocidade).replace(".", ",")}× · ${t.passos} passos`;
  const viva = props.resposta.status === "running";
  return (
    <article className="rounded-xl border border-line bg-surface px-4 py-3.5">
      <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-fg">{props.pedido.content}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-[11px] text-faint">
        <span>{t.voz}</span><span aria-hidden>·</span><span>{t.modelo}</span><span aria-hidden>·</span><span>{ajustes}</span>
        {t.semente_usada != null && (
          <>
            <span aria-hidden>·</span>
            <button onClick={() => props.onSemente(t.semente_usada)} title="Usar esta semente de novo (fixa)" className="font-mono hover:text-fg">
              semente {t.semente_usada}
            </button>
          </>
        )}
        {t.segundos != null && <><span aria-hidden>·</span><span className="font-mono">{String(t.segundos).replace(".", ",")} s</span></>}
      </div>

      <div className="mt-3">
        {t.estado === "pronto" && t.arquivo ? (
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1"><Tocador arquivo={t.arquivo} duracao={t.duracao} /></div>
            <button onClick={() => props.onRefazer(props.pedido.content)} title="Pôr este texto no campo de novo"
                    className="grid size-8 shrink-0 place-items-center rounded-[8px] text-faint hover:bg-raised hover:text-fg"><Refresh className="size-3.5" /></button>
            <a href={audioUrl(t.arquivo)} download={`voz-${props.resposta.id}.wav`} title="Baixar o WAV"
               className="grid size-8 shrink-0 place-items-center rounded-[8px] text-faint hover:bg-raised hover:text-fg"><Download className="size-3.5" /></a>
          </div>
        ) : viva ? (
          <div className="flex items-center gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-full border border-line-strong">
              <span className="size-2 animate-pulse rounded-full bg-accent" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2 text-[12px]">
                <span className="truncate text-fg-2">{t.estado === "na fila" ? "Na fila" : t.fase ? t.fase.charAt(0).toUpperCase() + t.fase.slice(1) : "Gerando"}</span>
                {t.progresso != null && <span className="ml-auto font-mono text-[11px] text-faint">{Math.round(t.progresso * 100)}%</span>}
              </div>
              <div className="mt-1.5 h-[3px] overflow-hidden rounded-full bg-line">
                <div className={`h-full rounded-full bg-accent transition-[width] duration-500 ${t.progresso == null ? "w-1/3 animate-pulse" : ""}`}
                     style={t.progresso != null ? { width: `${Math.max(4, t.progresso * 100)}%` } : undefined} />
              </div>
            </div>
            <button onClick={props.onCancelar} className={botaoEstado}>Cancelar</button>
          </div>
        ) : (
          <div className="flex items-start gap-2 text-[12.5px]">
            <span className={t.estado === "cancelado" ? "text-faint" : "text-diff-del-fg"}>
              {t.estado === "cancelado" ? "Cancelado." : t.erro || "Falhou."}
            </span>
            <button onClick={() => props.onRefazer(props.pedido.content)} className={`${linkPainel} ml-auto shrink-0`}>Tentar de novo</button>
          </div>
        )}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------- a tela

export default function TtsView(props: {
  conv: number | null;
  carimbo?: string;
  ensureConversation: () => Promise<number>;
  onError: (msg: string) => void;
  onConversationChanged: () => void;
}) {
  const [estado, setEstado] = useState<Estado | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [texto, setTexto] = useState("");
  const [modelo, setModelo] = useState(() => localStorage.getItem("tts.modelo") ?? "");
  const [voz, setVoz] = useState(() => localStorage.getItem("tts.voz") ?? "");
  const [painel, setPainel] = useState(() => localStorage.getItem("tts.painel") !== "0");
  const [velocidade, setVelocidade] = useState(1);
  const [passos, setPassos] = useState(32);
  const [semSilencio, setSemSilencio] = useState(false);
  const [temperatura, setTemperatura] = useState(1.0);  // 1,0 / 0,9: os que saíram nas amostras aprovadas
  const [topP, setTopP] = useState(0.9);
  const [semente, setSemente] = useState(-1);
  const [editando, setEditando] = useState<Modelo | null>(null);
  const [novaVoz, setNovaVoz] = useState<{ nome: string; texto: string; file: File | null } | null>(null);
  const [apagandoVoz, setApagandoVoz] = useState("");
  const [procurando, setProcurando] = useState(false);
  const [vram, setVram] = useState("");  // 409: o que ocupa a GPU; Gerar repete com confirm=true se a pessoa aceitar
  const campoTexto = useRef<HTMLTextAreaElement>(null);
  const arquivoVoz = useRef<HTMLInputElement>(null);
  const fim = useRef<HTMLDivElement>(null);
  const { onError } = props;

  const instalando = estado ? Object.values(estado.motores).some((m) => m.instalando) || estado.baixando.some((j) => j.status === "running") : false;
  const carregarEstado = useCallback(async () => {
    try {
      const e = await api.get<Estado>("/tts");
      setEstado(e);
      const ids = Object.values(e.motores).map((m) => m.instalando).filter(Boolean);
      if (ids.length) setJobs((await api.get<{ jobs: Job[] }>("/local")).jobs.filter((j) => ids.includes(j.id)));
    } catch (e: any) {
      onError(e.message);
    }
  }, [onError]);

  const carregarConversa = useCallback(async () => {
    if (props.conv === null) return setMessages([]);
    try {
      setMessages((await api.get<{ messages: Message[] }>(`/conversations/${props.conv}`)).messages);
    } catch (e: any) {
      onError(e.message);
    }
  }, [props.conv, onError]);

  useEffect(() => { carregarEstado(); }, [carregarEstado]);
  useEffect(() => { carregarConversa(); }, [carregarConversa, props.carimbo]);
  useEffect(() => {
    if (!instalando) return;
    const t = setInterval(carregarEstado, POLL_MS);
    return () => clearInterval(t);
  }, [instalando, carregarEstado]);

  const rodando = messages.some((m) => m.role === "assistant" && m.status === "running");
  useEffect(() => {
    if (!rodando) return;
    const t = setInterval(carregarConversa, POLL_MS);
    return () => clearInterval(t);
  }, [rodando, carregarConversa]);
  // terminou: o indicador de modelo carregado e a transcrição da voz (o Whisper guarda) mudaram
  useEffect(() => { if (!rodando) carregarEstado(); }, [rodando, carregarEstado]);
  useEffect(() => { fim.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length]);

  const alternarPainel = () => setPainel((v) => { localStorage.setItem("tts.painel", v ? "0" : "1"); return !v; });

  // escolha some quando o item foi apagado: cai no primeiro
  const m = estado?.modelos.find((x) => x.nome === modelo) ?? estado?.modelos[0];
  const v = estado?.vozes.find((x) => x.id === voz) ?? estado?.vozes[0];
  const motorPronto = !!m && !!estado?.motores[m.motor]?.instalado;

  async function gerar(confirm = false) {
    if (!m || !v || !texto.trim()) return;
    try {
      const id = await props.ensureConversation();
      const extras = m.motor === "fish" ? { temperatura, top_p: topP } : { velocidade, passos, sem_silencio: semSilencio };
      await api.post(`/tts/${id}/gerar`, { texto, modelo: m.nome, voz: v.id, semente, confirm, ...extras });
      localStorage.setItem("tts.modelo", m.nome);
      localStorage.setItem("tts.voz", v.id);
      setTexto("");
      setVram("");
      props.onConversationChanged();
      if (id === props.conv) carregarConversa();
    } catch (e: any) {
      if (e.status === 409) setVram(e.message || "x");
      else onError(e.message);
    }
  }

  async function salvarModelo() {
    try {
      const modelos = await api.post<Modelo[]>("/tts/modelos", editando);
      setEstado((e) => e && { ...e, modelos });
      setModelo(editando!.nome);
      setEditando(null);
    } catch (e: any) {
      onError(e.message);
    }
  }

  async function salvarVoz() {
    if (!novaVoz?.file) return onError("Escolha o áudio de referência.");
    const nome = novaVoz.nome.trim() || novaVoz.file.name.replace(/\.\w+$/, "");
    try {
      // nome e texto vão junto no multipart (enviarArquivo só manda `file`)
      const form = new FormData();
      form.append("file", novaVoz.file);
      form.append("nome", nome);
      form.append("texto", novaVoz.texto);
      const r = await fetch("/api/tts/vozes", { method: "POST", body: form, headers: auth() });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
      const vozes: Voz[] = await r.json();
      setEstado((e) => e && { ...e, vozes });
      setVoz(vozes.find((x) => x.nome === nome)?.id ?? voz);
      setNovaVoz(null);
    } catch (e: any) {
      onError(e.message);
    }
  }

  function inserirTag(tag: string) {
    const el = campoTexto.current;
    const i = el?.selectionStart ?? texto.length;
    const antes = texto.slice(0, i);
    setTexto(antes + (antes && !antes.endsWith(" ") ? " " : "") + tag + " " + texto.slice(i));
    requestAnimationFrame(() => el?.focus());
  }

  function refazer(t: string) {
    setTexto(t);
    requestAnimationFrame(() => campoTexto.current?.focus());
  }

  if (!estado) return <div className="flex-1" />;

  const pares: { pedido: Message; resposta: Message }[] = [];
  messages.forEach((x, i) => { if (x.role === "assistant" && x.meta?.tts) pares.push({ pedido: messages[i - 1] ?? x, resposta: x }); });
  const viva = pares.find((p) => p.resposta.status === "running")?.resposta.meta?.tts;
  const aceleracao = estado.backend === "xpu" ? "Intel XPU" : estado.backend === "auto" ? "NVIDIA CUDA" : "CPU";
  const info = m ? estado.motores[m.motor] : null;
  const jobMotor = info?.instalando ? jobs.find((j) => j.id === info.instalando) : undefined;
  const erroInstalar = jobs.find((j) => j.error)?.error;
  const falta = !m ? "modelo" : !v ? "voz" : "";

  const cartaoInstalar = m && info && !motorPronto && (
    <CartaoEstado
      tom={erroInstalar ? "erro" : "neutro"}
      titulo={`Falta o motor ${info.nome}`}
      acoes={info.instalando ? undefined : (
        <button className={botaoEstadoPrimario} onClick={() => api.post(`/tts/instalar/${m.motor}`).then(carregarEstado).catch((e) => onError(e.message))}>
          {erroInstalar ? "Tentar de novo" : "Instalar motor"}
        </button>
      )}>
      {info.instalando ? (
        <span className="font-mono text-[11.5px]">{jobMotor?.detail || "começando…"}</span>
      ) : erroInstalar ? erroInstalar : (
        `Um Python próprio com PyTorch (${aceleracao}, pela GPU ${estado.gpu}), uns ${info.gb} GB na pasta de runtimes. Os pesos do modelo baixam na primeira geração.`
      )}
    </CartaoEstado>
  );

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <BarraTopo
          contagem={pares.length}
          unidade={["áudio", "áudios"]}
          status={viva
            ? { cor: "bg-accent animate-pulse", texto: viva.fase ? `Gerando · ${viva.fase}` : "Na fila", meta: viva.progresso != null ? `${Math.round(viva.progresso * 100)}%` : "" }
            : estado.carregado
              ? { cor: "bg-ok", texto: `${estado.carregado.nome} na memória`, meta: estado.carregado.dispositivo }
              : null}
          parametros={painel}
          onParametros={alternarPainel}
        />
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[820px] px-5 py-5">
            {!pares.length ? (
              <Saudacao
                titulo="Voz"
                sub="Escreva o texto, escolha a voz, ouça."
                nota={falta === "modelo" ? <span className="text-warn">Nenhum modelo de voz ainda — adicione um em Parâmetros › Modelo.</span>
                  : falta === "voz" ? <span className="text-warn">Falta uma voz de referência — envie 5 a 15 s de fala em Parâmetros › Voz.</span>
                  : `${v!.nome} · ${m!.nome}`}>
                {cartaoInstalar && <div className="mt-6 w-full max-w-md text-left">{cartaoInstalar}</div>}
                {falta && !painel && (
                  <button onClick={alternarPainel} className={`${botaoEstado} mt-4`}>Abrir Parâmetros</button>
                )}
              </Saudacao>
            ) : (
              <div className="flex flex-col gap-3">
                {pares.map(({ pedido, resposta }) => (
                  <Fala key={resposta.id} pedido={pedido} resposta={resposta}
                        onCancelar={() => api.post(`/tts/cancelar/${resposta.id}`).then(carregarConversa).catch((e) => onError(e.message))}
                        onRefazer={refazer}
                        onSemente={(s) => { setSemente(s); if (!painel) alternarPainel(); }} />
                ))}
                <div ref={fim} />
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 px-5 pb-4">
          <div className="mx-auto flex max-w-[820px] flex-col gap-2">
            {pares.length > 0 && cartaoInstalar}
            {vram && (
              <CartaoEstado tom="aviso"
                titulo={vram.startsWith("Outro programa") ? vram : `O modelo ${vram} está carregado na VRAM.`}
                onFechar={() => setVram("")}
                acoes={<>
                  <button className={botaoEstadoPrimario} onClick={() => gerar(true)}>
                    {vram.startsWith("Outro programa") ? "Gerar mesmo assim" : "Descarregar e gerar"}
                  </button>
                  <button className={botaoEstado} onClick={() => setVram("")}>Cancelar</button>
                </>}>
                {!vram.startsWith("Outro programa") &&
                  "O motor de voz precisa dessa memória. Descarregar derruba o cache de contexto do chat: a próxima mensagem de lá reprocessa o histórico inteiro. A conversa em si não se perde."}
              </CartaoEstado>
            )}
            <div className="rounded-2xl border border-line bg-surface px-3.5 py-3 shadow-[0_-10px_30px_rgba(0,0,0,.35)] transition-colors duration-150 focus-within:border-focus">
              <textarea
                ref={campoTexto}
                value={texto}
                onChange={(e) => setTexto(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    gerar();
                  }
                }}
                rows={3}
                aria-label="Texto a falar"
                placeholder={m?.motor === "fish" ? "O que a voz vai dizer… [sigh] colchetes pedem emoção" : "O que a voz vai dizer…"}
                className={campoPrompt}
              />
              <div className="mt-2.5 flex items-center gap-3 border-t border-line pt-2.5 text-xs">
                {/* uma linha só: em janela estreita as tags rolam de lado em vez de empurrar o Gerar para baixo. A barra some
                    por estilo inline: o `* { scrollbar-width: thin }` do index.css, fora de camada, vence o utilitário */}
                <div className="flex min-w-0 flex-1 gap-0.5 overflow-x-auto" style={{ scrollbarWidth: "none" }}>
                  {m?.motor === "fish" && TAGS.map((tag) => (
                    <button key={tag} onClick={() => inserirTag(tag)} title={`Inserir ${tag} no cursor`}
                            className="shrink-0 rounded-[7px] px-1.5 py-1 font-mono text-[11px] text-faint hover:bg-raised hover:text-fg">
                      {tag}
                    </button>
                  ))}
                </div>
                <div className="flex min-w-0 shrink-0 items-center gap-3">
                  {m && v && (
                    <span className="hidden max-w-[260px] truncate text-[11.5px] text-muted 2xl:inline" title={`${v.nome} · ${m.nome}`}>
                      {v.nome} <span className="text-faint">·</span> {m.nome}
                    </span>
                  )}
                  <button
                    onClick={() => gerar()}
                    disabled={!texto.trim() || !motorPronto || !v}
                    title={!m ? "Adicione um modelo em Parâmetros" : !motorPronto ? "Instale o motor primeiro" : !v ? "Adicione uma voz em Parâmetros" : "Gerar (Enter) · Shift+Enter quebra a linha"}
                    className="inline-flex shrink-0 items-center gap-2 rounded-[10px] bg-accent px-3.5 py-2 text-[13px] font-semibold text-accent-fg hover:brightness-110 disabled:bg-raised disabled:text-faint disabled:hover:brightness-100"
                  >
                    Gerar
                    <span className="font-mono text-[10.5px] font-medium opacity-65">Enter</span>
                  </button>
                </div>
              </div>
            </div>
            <p className="text-center text-[11px] text-faint">{RODAPE[m?.motor ?? "fish"]}</p>
          </div>
        </div>
      </div>

      {procurando && (
        <ModelSearch
          kind="voz"
          destino=""
          onKind={() => {}}
          onDownload={(repo, file) => api.post("/local/download", { repo, file, kind: "voz" }).then(carregarEstado).catch((e) => onError(e.message))}
          onClose={() => { setProcurando(false); carregarEstado(); }}
          onError={onError}
        />
      )}
      {painel && (
        <aside className="flex w-[300px] shrink-0 flex-col overflow-hidden border-l border-line bg-side text-xs">
          <div className="flex items-center gap-2 px-4 pt-4 pb-1.5">
            <span className="text-[13px] font-semibold text-fg">Parâmetros</span>
            <span className="ml-auto" />
            <button onClick={alternarPainel} className="rounded-[7px] p-1 text-faint hover:bg-raised hover:text-fg" aria-label="Esconder os parâmetros">
              <X className="size-3.5" />
            </button>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-[18px] overflow-y-auto px-4 pt-1.5 pb-4">
            <Secao titulo="Modelo" extra={!editando && (
              <button className={`${linkPainel} inline-flex items-center gap-1`} onClick={() => setEditando({ ...VAZIO.fish })}>
                <Plus className="size-3" /> Adicionar
              </button>
            )}>
              {estado.modelos.length > 0 && (
                <div className="flex flex-col gap-0.5 rounded-[10px] border border-line bg-surface p-1.5" role="radiogroup" aria-label="Modelo">
                  {estado.modelos.map((x) => {
                    const ativo = x.nome === m?.nome;
                    const inst = estado.motores[x.motor]?.instalado;
                    return (
                      <div key={x.nome} role="radio" aria-checked={ativo} aria-label={x.nome} tabIndex={0}
                           onClick={() => setModelo(x.nome)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setModelo(x.nome); } }}
                           className={`group flex cursor-pointer items-center gap-2.5 rounded-[7px] px-1.5 py-[5px] outline-none focus-visible:ring-1 focus-visible:ring-focus ${ativo ? "bg-raised" : "hover:bg-raised/60"}`}>
                        <span className={`grid size-3.5 shrink-0 place-items-center rounded-full border-[1.5px] ${ativo ? "border-accent" : "border-line-strong"}`}>
                          {ativo && <span className="size-1.5 rounded-full bg-accent" />}
                        </span>
                        <span className="flex min-w-0 flex-1 flex-col">
                          <span className={`truncate text-[12.5px] ${ativo ? "text-fg" : "text-muted"}`}>{x.nome}</span>
                          <span className={`truncate text-[10.5px] ${inst ? "text-faint" : "text-warn"}`}>
                            {estado.motores[x.motor]?.nome}{inst ? (x.a_baixar ? " · baixa na 1ª geração" : "") : " · motor não instalado"}
                          </span>
                        </span>
                        <button onClick={(e) => { e.stopPropagation(); setEditando({ ...x }); }} aria-label={`Editar ${x.nome}`}
                                className="rounded-[6px] p-1 text-faint opacity-0 group-hover:opacity-100 hover:bg-raised hover:text-fg focus-visible:opacity-100">
                          <Edit className="size-3" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
              {!estado.modelos.length && !editando && <p className="text-[11.5px] leading-relaxed text-faint">Nenhum modelo. Em Adicionar há sugestões prontas, ou procure um no Hugging Face.</p>}
              {estado.baixando.map((j) => (
                <div key={j.id} className="flex flex-col gap-1">
                  <div className="flex items-baseline gap-2 text-[11.5px]">
                    <span className="min-w-0 flex-1 truncate text-fg-2" title={j.name}>{j.name?.replace(/ \(voz\)$/, "")}</span>
                    <span className={`shrink-0 font-mono text-[11px] ${j.error ? "text-diff-del-fg" : "text-faint"}`}>
                      {j.error ? "falhou" : j.status === "running" ? (j.total ? `${gb(j.done)} de ${gb(j.total)} GB` : "começando…") : "pronto · cadastrado"}
                    </span>
                  </div>
                  {j.status === "running" && (
                    <div className="h-[3px] overflow-hidden rounded-full bg-line">
                      <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${j.total ? Math.max(2, ((j.done ?? 0) / j.total) * 100) : 2}%` }} />
                    </div>
                  )}
                  {j.error && <p className="text-[11px] text-diff-del-fg">{j.error}</p>}
                </div>
              ))}
              {!editando && (
                <button onClick={() => setProcurando(true)} className={`${linkPainel} inline-flex items-center gap-1.5 self-start`}>
                  <Search className="size-3" /> Procurar modelos de voz no Hugging Face
                </button>
              )}
              {editando && (
                <div className="flex flex-col gap-2 rounded-[10px] border border-line-strong p-2.5">
                  <div className="flex flex-wrap gap-1">
                    {SUGESTOES.map((s) => (
                      <button key={s.nome} onClick={() => setEditando({ ...s })}
                              className={`rounded-[7px] border px-2 py-[3px] text-[11px] ${editando.nome === s.nome ? "border-accent-line bg-accent-soft text-accent-text" : "border-line text-muted hover:bg-raised hover:text-fg"}`}>
                        {s.nome}
                      </button>
                    ))}
                  </div>
                  <div className="flex rounded-[8px] border border-line bg-surface p-0.5" role="radiogroup" aria-label="Motor">
                    {(Object.keys(estado.motores) as Motor[]).map((k) => (
                      <button key={k} role="radio" aria-checked={editando.motor === k} onClick={() => setEditando({ ...VAZIO[k], nome: editando.nome })}
                              className={`flex-1 rounded-[6px] py-1 ${editando.motor === k ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>
                        {k === "fish" ? "Fish Audio" : "F5-TTS"}
                      </button>
                    ))}
                  </div>
                  <Caixa rotulo="Nome"><input className={textoCaixa} value={editando.nome} onChange={(e) => setEditando({ ...editando, nome: e.target.value })} /></Caixa>
                  {editando.motor === "fish" ? (
                    <Caixa rotulo="Repositório do Hugging Face ou pasta">
                      <input className={`${textoCaixa} font-mono`} placeholder="fishaudio/s2-pro" value={editando.modelo ?? ""} onChange={(e) => setEditando({ ...editando, modelo: e.target.value })} />
                    </Caixa>
                  ) : (
                    <>
                      <Caixa rotulo="Arquitetura">
                        <select className={`${textoCaixa} cursor-pointer`} value={editando.arquitetura} onChange={(e) => setEditando({ ...editando, arquitetura: e.target.value })}>
                          {estado.arquiteturas.map((a) => <option key={a}>{a}</option>)}
                        </select>
                      </Caixa>
                      <Caixa rotulo="Checkpoint (vazio = oficial)">
                        <input className={`${textoCaixa} font-mono`} placeholder="hf://dono/repo/arquivo ou caminho" value={editando.ckpt ?? ""} onChange={(e) => setEditando({ ...editando, ckpt: e.target.value })} />
                      </Caixa>
                      <Caixa rotulo="vocab.txt (vazio = padrão)">
                        <input className={`${textoCaixa} font-mono`} value={editando.vocab ?? ""} onChange={(e) => setEditando({ ...editando, vocab: e.target.value })} />
                      </Caixa>
                      <div className="grid grid-cols-2 gap-1.5">
                        <Caixa rotulo="Números por extenso">
                          <input className={`${textoCaixa} font-mono`} placeholder="pt_BR" value={editando.numeros ?? ""} onChange={(e) => setEditando({ ...editando, numeros: e.target.value })} />
                        </Caixa>
                        <label className="flex cursor-pointer items-center gap-2 rounded-[8px] border border-line bg-surface px-2.5 text-[12px] text-muted">
                          <input type="checkbox" className="accent-accent" checked={!!editando.minusculas} onChange={(e) => setEditando({ ...editando, minusculas: e.target.checked })} />
                          Minúsculas
                        </label>
                      </div>
                    </>
                  )}
                  <div className="flex items-center gap-1.5 pt-0.5">
                    <button className={botaoEstadoPrimario} onClick={salvarModelo} disabled={!editando.nome.trim()}>Salvar</button>
                    <button className={botaoEstado} onClick={() => setEditando(null)}>Cancelar</button>
                    {estado.modelos.some((x) => x.nome === editando.nome) && (
                      <button aria-label="Apagar o modelo" title="Apagar o modelo (os arquivos baixados ficam)"
                              className="ml-auto rounded-[7px] p-1.5 text-faint hover:bg-raised hover:text-diff-del-fg"
                              onClick={() => api.del<Modelo[]>(`/tts/modelos?nome=${encodeURIComponent(editando.nome)}`).then((modelos) => { setEstado({ ...estado, modelos }); setEditando(null); }).catch((e) => onError(e.message))}>
                        <Trash className="size-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              )}
            </Secao>

            <Secao titulo="Voz" extra={!novaVoz && (
              <button className={`${linkPainel} inline-flex items-center gap-1`} onClick={() => setNovaVoz({ nome: "", texto: "", file: null })}>
                <Plus className="size-3" /> Adicionar
              </button>
            )}>
              {estado.vozes.length > 0 && (
                <div className="flex flex-col gap-0.5 rounded-[10px] border border-line bg-surface p-1.5" role="radiogroup" aria-label="Voz de referência">
                  {estado.vozes.map((x) => {
                    const ativo = x.id === v?.id;
                    return (
                      <div key={x.id} role="radio" aria-checked={ativo} aria-label={x.nome} tabIndex={0}
                           onClick={() => setVoz(x.id)} onKeyDown={(e) => { if (e.key === "Enter") setVoz(x.id); }}
                           className={`group flex cursor-pointer items-center gap-2.5 rounded-[7px] px-1.5 py-[5px] outline-none focus-visible:ring-1 focus-visible:ring-focus ${ativo ? "bg-raised" : "hover:bg-raised/60"}`}>
                        <PlayPequeno arquivo={x.caminho} />
                        <span className={`min-w-0 flex-1 truncate text-[12.5px] ${ativo ? "text-fg" : "text-muted"}`}>{x.nome}</span>
                        {ativo && <Check className="size-3 shrink-0 text-accent-text" />}
                        {apagandoVoz === x.id ? (
                          <button onClick={(e) => { e.stopPropagation(); api.del<Voz[]>(`/tts/vozes/${x.id}`).then((vozes) => { setEstado({ ...estado, vozes }); setApagandoVoz(""); }).catch((er) => onError(er.message)); }}
                                  onBlur={() => setApagandoVoz("")} autoFocus
                                  className="shrink-0 rounded-[6px] px-1.5 py-0.5 text-[11px] text-diff-del-fg hover:bg-err/10">Apagar?</button>
                        ) : (
                          <button onClick={(e) => { e.stopPropagation(); setApagandoVoz(x.id); }} aria-label={`Apagar a voz ${x.nome}`}
                                  className="shrink-0 rounded-[6px] p-1 text-faint opacity-0 group-hover:opacity-100 hover:bg-raised hover:text-diff-del-fg focus-visible:opacity-100">
                            <Trash className="size-3" />
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              {v && !novaVoz && (
                <p className="line-clamp-3 text-[11.5px] leading-relaxed text-faint" title={v.texto}>
                  {v.texto ? <>“{v.texto}”</> : "Sem transcrição: o Whisper transcreve na primeira geração e guarda."}
                </p>
              )}
              {!estado.vozes.length && !novaVoz && (
                <p className="text-[11.5px] leading-relaxed text-faint">Nenhuma voz. Envie 5 a 15 s de fala humana limpa: a saída copia o timbre da referência — voz sintética vira saída robótica.</p>
              )}
              {novaVoz && (
                <div className="flex flex-col gap-2 rounded-[10px] border border-line-strong p-2.5">
                  <input ref={arquivoVoz} type="file" accept=".wav,.mp3,.flac,.ogg,.m4a,audio/*" hidden
                         onChange={(e) => { const f = e.target.files?.[0] ?? null; setNovaVoz({ ...novaVoz, file: f, nome: novaVoz.nome || (f?.name.replace(/\.\w+$/, "") ?? "") }); }} />
                  <button onClick={() => arquivoVoz.current?.click()}
                          className={`flex items-center gap-2 rounded-[8px] border border-dashed px-2.5 py-2 text-left text-[12px] ${novaVoz.file ? "border-line-strong text-fg" : "border-line-strong text-muted hover:border-fg hover:text-fg"}`}>
                    <Plus className="size-3.5 shrink-0" />
                    <span className="truncate">{novaVoz.file ? novaVoz.file.name : "Escolher áudio (WAV, MP3, FLAC…)"}</span>
                  </button>
                  <Caixa rotulo="Nome"><input className={textoCaixa} value={novaVoz.nome} onChange={(e) => setNovaVoz({ ...novaVoz, nome: e.target.value })} /></Caixa>
                  <Caixa rotulo="O que é dito no áudio (vazio = o Whisper transcreve)">
                    <textarea rows={3} className={`${textoCaixa} resize-none`} value={novaVoz.texto} onChange={(e) => setNovaVoz({ ...novaVoz, texto: e.target.value })} />
                  </Caixa>
                  <div className="flex gap-1.5 pt-0.5">
                    <button className={botaoEstadoPrimario} onClick={salvarVoz} disabled={!novaVoz.file}>Salvar</button>
                    <button className={botaoEstado} onClick={() => setNovaVoz(null)}>Cancelar</button>
                  </div>
                </div>
              )}
            </Secao>

            <Secao titulo="Ajustes">
              {m?.motor === "f5" ? (
                <>
                  <div className="grid grid-cols-2 gap-1.5">
                    <Caixa rotulo="Velocidade" passo={0.05}>
                      <input type="number" min={0.5} max={2} step={0.05} value={velocidade} onChange={(e) => setVelocidade(Number(e.target.value) || 1)} className={numeroCaixa} />
                    </Caixa>
                    <Caixa rotulo="Passos" passo={1}>
                      <input type="number" min={4} max={128} value={passos} onChange={(e) => setPassos(Number(e.target.value) || 32)} className={numeroCaixa} />
                    </Caixa>
                  </div>
                  <label className="flex cursor-pointer items-center gap-2 text-[12px] text-muted">
                    <input type="checkbox" className="accent-accent" checked={semSilencio} onChange={(e) => setSemSilencio(e.target.checked)} />
                    Tirar silêncios longos
                  </label>
                </>
              ) : (
                <div className="grid grid-cols-2 gap-1.5">
                  <Caixa rotulo="Temperatura" passo={0.05}>
                    <input type="number" min={0.3} max={1.5} step={0.05} value={temperatura} title="Mais alta = fala mais solta e variada; mais baixa = mais contida"
                           onChange={(e) => setTemperatura(Math.min(1.5, Math.max(0.3, Number(e.target.value) || 1)))} className={numeroCaixa} />
                  </Caixa>
                  <Caixa rotulo="Top-p" passo={0.05}>
                    <input type="number" min={0.5} max={1} step={0.05} value={topP}
                           onChange={(e) => setTopP(Math.min(1, Math.max(0.5, Number(e.target.value) || 0.9)))} className={numeroCaixa} />
                  </Caixa>
                </div>
              )}
              <div className="flex rounded-[8px] border border-line bg-surface p-0.5 text-xs" role="radiogroup" aria-label="Semente">
                {([["aleatoria", "Aleatória", "Cada áudio sai diferente"], ["fixa", "Fixa", "Repete a mesma interpretação do texto"]] as const).map(([id, rot, dica]) => {
                  const on = id === "fixa" ? semente >= 0 : semente < 0;
                  return (
                    <button key={id} role="radio" aria-checked={on} title={dica} onClick={() => setSemente(id === "fixa" ? (semente >= 0 ? semente : 42) : -1)}
                            className={`flex-1 rounded-[6px] py-1 ${on ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>
                      {rot}
                    </button>
                  );
                })}
              </div>
              {semente >= 0 && (
                <Caixa rotulo="Semente" passo={1}>
                  <input type="number" min={0} value={semente} onChange={(e) => setSemente(Math.max(0, Number(e.target.value) || 0))} className={numeroCaixa} />
                </Caixa>
              )}
            </Secao>
          </div>
          <div className="flex items-center gap-2 border-t border-line px-4 py-3 text-[11.5px]">
            <span className={`size-1.5 shrink-0 rounded-full ${estado.carregado ? "bg-ok" : "bg-line-strong"}`} />
            <span className="min-w-0 flex-1 truncate text-faint" title={estado.carregado ? `${estado.carregado.nome} · ${estado.carregado.dispositivo}` : undefined}>
              {estado.carregado ? `Na memória · ${estado.carregado.dispositivo}` : "Nenhum modelo de voz na memória"}
            </span>
            {estado.carregado && (
              <button className={linkPainel} onClick={() => api.post("/tts/descarregar").then(carregarEstado).catch((e) => onError(e.message))}>Descarregar</button>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}

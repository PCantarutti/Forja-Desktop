import { useCallback, useEffect, useRef, useState } from "react";
import { api, auth } from "../api";
import type { Message } from "../types";
import CartaoEstado, { botaoEstado, botaoEstadoPrimario } from "./CartaoEstado";
import { Secao } from "./ImagensView";

// Tela Voz: texto para fala com motores plugáveis (backend/app/tts.py): F5-TTS/E2-TTS e Fish Audio. Cada motor tem o
// seu runtime, instalado só quando pedido; o modelo diz qual motor usa. Cada geração é um par de mensagens da conversa
// kind="tts"; a do assistente leva meta.tts (estado, fase, arquivo, duração...).

type Motor = "f5" | "fish";
type Modelo = { nome: string; motor: Motor; modelo?: string; arquitetura?: string; ckpt?: string; vocab?: string; minusculas?: boolean; numeros?: string };
type Voz = { id: string; nome: string; texto: string; caminho: string };
type InfoMotor = { nome: string; instalado: boolean; gb: number; instalando: string };
type Estado = {
  motores: Record<Motor, InfoMotor>; gpu: string; backend: string; modelos: Modelo[]; vozes: Voz[]; arquiteturas: string[];
  carregado: { nome: string; dispositivo: string } | null;
};
type Job = { id: string; status: string; detail: string; error: string };

const POLL_MS = 1500;
const btn = "inline-flex items-center gap-1.5 rounded-[9px] border border-line px-3 py-1.5 text-[13px] hover:bg-raised disabled:opacity-40";
const btnPrimary = "inline-flex items-center gap-1.5 rounded-[9px] border border-accent bg-accent px-3 py-1.5 font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";
const campo = "w-full rounded-[8px] border border-line bg-surface px-2.5 py-1.5 text-[13px] outline-none focus:border-focus";
const audioUrl = (p: string) => `/api/tts/arquivo?path=${encodeURIComponent(p)}`;
// Sugestões para preencher o formulário (não instalam nada).
const SUGESTOES: Modelo[] = [
  { nome: "Fish Audio S2-pro (multilíngue, aceita [tags])", motor: "fish", modelo: "fishaudio/s2-pro" },
  { nome: "F5-TTS v1 (oficial, inglês/chinês)", motor: "f5", arquitetura: "F5TTS_v1_Base", ckpt: "", vocab: "", minusculas: false, numeros: "" },
  { nome: "F5-TTS pt-br (firstpixel)", motor: "f5", arquitetura: "F5TTS_Base", ckpt: "hf://firstpixel/F5-TTS-pt-br/pt-br/model_last.safetensors", vocab: "", minusculas: true, numeros: "pt_BR" },
];
const VAZIO: Record<Motor, Modelo> = {
  fish: { nome: "", motor: "fish", modelo: "" },
  f5: { nome: "", motor: "f5", arquitetura: "F5TTS_v1_Base", ckpt: "", vocab: "", minusculas: false, numeros: "" },
};
const TAGS = ["[sigh]", "[short pause]", "[chuckle]", "[laughing]", "[whisper]", "[excited]", "[sad]", "[emphasis]"];

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
  const [velocidade, setVelocidade] = useState(1);
  const [passos, setPassos] = useState(32);
  const [semSilencio, setSemSilencio] = useState(false);
  const [temperatura, setTemperatura] = useState(1.0);  // 1,0 / 0,9: os que saíram nas amostras aprovadas
  const [topP, setTopP] = useState(0.9);
  const [semente, setSemente] = useState(-1);
  const [editando, setEditando] = useState<Modelo | null>(null);
  const [novaVoz, setNovaVoz] = useState<{ nome: string; texto: string; file: File | null } | null>(null);
  const [vram, setVram] = useState("");  // 409: o que ocupa a GPU; Gerar repete com confirm=true se a pessoa aceitar
  const campoTexto = useRef<HTMLTextAreaElement>(null);
  const fim = useRef<HTMLDivElement>(null);
  const { onError } = props;

  const instalando = estado ? Object.values(estado.motores).some((m) => m.instalando) : false;
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

  // escolha some quando o item foi apagado: cai no primeiro
  const m = estado?.modelos.find((x) => x.nome === modelo) ?? estado?.modelos[0];
  const vozAtual = estado?.vozes.find((v) => v.id === voz)?.id ?? estado?.vozes[0]?.id ?? "";
  const motorPronto = !!m && !!estado?.motores[m.motor]?.instalado;

  async function gerar(confirm = false) {
    if (!m) return;
    try {
      const id = await props.ensureConversation();
      const extras = m.motor === "fish" ? { temperatura, top_p: topP } : { velocidade, passos, sem_silencio: semSilencio };
      await api.post(`/tts/${id}/gerar`, { texto, modelo: m.nome, voz: vozAtual, semente, confirm, ...extras });
      localStorage.setItem("tts.modelo", m.nome);
      localStorage.setItem("tts.voz", vozAtual);
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
    const nome = novaVoz.nome || novaVoz.file.name.replace(/\.\w+$/, "");
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
      setVoz(vozes.find((v) => v.nome === nome)?.id ?? voz);
      setNovaVoz(null);
    } catch (e: any) {
      onError(e.message);
    }
  }

  function inserirTag(tag: string) {
    const el = campoTexto.current;
    const i = el?.selectionStart ?? texto.length;
    setTexto(texto.slice(0, i) + tag + " " + texto.slice(i));
    requestAnimationFrame(() => el?.focus());
  }

  if (!estado) return <div className="flex-1" />;

  const pares: { pedido: Message; resposta: Message }[] = [];
  messages.forEach((x, i) => { if (x.role === "assistant" && x.meta?.tts) pares.push({ pedido: messages[i - 1] ?? x, resposta: x }); });
  const aceleracao = estado.backend === "xpu" ? "Intel XPU" : estado.backend === "auto" ? "NVIDIA CUDA" : "CPU";

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        {m && !motorPronto && (
          <div className="m-4 rounded-[10px] border border-line bg-surface p-4 text-[13px]">
            <div className="font-medium">Motor {estado.motores[m.motor].nome} não instalado</div>
            <p className="mt-1 text-muted">
              O modelo “{m.nome}” usa esse motor. O Forja monta um Python próprio com PyTorch ({aceleracao}, pela GPU {estado.gpu}),
              uns {estado.motores[m.motor].gb} GB dentro da pasta de runtimes. Os pesos do modelo baixam na primeira geração.
            </p>
            {estado.motores[m.motor].instalando ? (
              <p className="mt-2 font-mono text-[12px] text-muted">{jobs.find((j) => j.id === estado.motores[m.motor].instalando)?.detail || "começando…"}</p>
            ) : (
              <button className={`${btnPrimary} mt-3`} onClick={() => api.post(`/tts/instalar/${m.motor}`).then(carregarEstado).catch((e) => onError(e.message))}>
                Instalar motor
              </button>
            )}
            {jobs.filter((j) => j.error).map((j) => <p key={j.id} className="mt-2 text-[12px] text-red-400">{j.error}</p>)}
          </div>
        )}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {!pares.length && <p className="mt-10 text-center text-[13px] text-faint">Escreva um texto embaixo para gerar a fala com a voz escolhida.</p>}
          <div className="mx-auto flex max-w-[760px] flex-col gap-4">
            {pares.map(({ pedido, resposta }) => {
              const t = resposta.meta!.tts;
              const ajustes = t.temperatura != null ? `temperatura ${t.temperatura}` : `${t.velocidade}× · ${t.passos} passos`;
              return (
                <div key={resposta.id} className="rounded-[10px] border border-line bg-surface p-3">
                  <p className="whitespace-pre-wrap text-[13.5px]">{pedido.content}</p>
                  <div className="mt-1 text-[11px] text-faint">
                    {t.modelo} · {t.voz} · {ajustes}{t.semente_usada != null && ` · semente ${t.semente_usada}`}{t.segundos && ` · ${t.segundos} s`}
                  </div>
                  {t.estado === "pronto" && t.arquivo ? (
                    <div className="mt-2 flex items-center gap-2">
                      <audio controls src={audioUrl(t.arquivo)} className="h-9 flex-1" />
                      <a className={btn} href={audioUrl(t.arquivo)} download={`voz-${resposta.id}.wav`}>Baixar</a>
                    </div>
                  ) : t.estado === "erro" || t.estado === "cancelado" ? (
                    <p className="mt-2 text-[12px] text-red-400">{t.erro || "Cancelado."}</p>
                  ) : (
                    <div className="mt-2 flex items-center gap-2 text-[12px] text-muted">
                      <span className="font-mono">{t.estado}{t.fase && ` · ${t.fase}`}{t.progresso != null && ` · ${Math.round(t.progresso * 100)}%`}</span>
                      <button className={`${btn} ml-auto`} onClick={() => api.post(`/tts/cancelar/${resposta.id}`).then(carregarConversa)}>Cancelar</button>
                    </div>
                  )}
                </div>
              );
            })}
            <div ref={fim} />
          </div>
        </div>
        <div className="border-t border-line p-3">
          <div className="mx-auto flex max-w-[760px] flex-col gap-2">
            {vram && (
              <CartaoEstado tom="aviso"
                titulo={vram.startsWith("Outro programa") ? vram : `O modelo ${vram} está carregado na VRAM.`}
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
            {m?.motor === "fish" && (
              <div className="flex flex-wrap items-center gap-1 text-[11px] text-faint">
                Emoção no texto:
                {TAGS.map((t) => <button key={t} className="rounded-full border border-line px-2 py-0.5 hover:bg-raised hover:text-fg" onClick={() => inserirTag(t)}>{t}</button>)}
                <span>(vale qualquer descrição entre colchetes)</span>
              </div>
            )}
            <div className="flex gap-2">
              <textarea ref={campoTexto} rows={3} value={texto} onChange={(e) => setTexto(e.target.value)} placeholder="Texto a falar…" className={`${campo} resize-y`}
                        onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && texto.trim()) gerar(); }} />
              <button className={`${btnPrimary} self-end`} disabled={!texto.trim() || !motorPronto || !vozAtual} onClick={() => gerar()}>
                Gerar
              </button>
            </div>
          </div>
        </div>
      </div>

      <aside className="flex w-[300px] shrink-0 flex-col gap-5 overflow-y-auto border-l border-line p-4 text-[13px]">
        <Secao titulo="Modelo" extra={<button className="text-[11.5px] text-accent-text" onClick={() => setEditando({ ...VAZIO.fish })}>Adicionar</button>}>
          {estado.modelos.length ? (
            <div className="flex gap-1.5">
              <select value={m?.nome ?? ""} onChange={(e) => setModelo(e.target.value)} className={`${campo} cursor-pointer`}>
                {estado.modelos.map((x) => <option key={x.nome} value={x.nome}>{x.nome}</option>)}
              </select>
              <button className={btn} title="Editar" onClick={() => setEditando(m ?? null)}>…</button>
            </div>
          ) : <p className="text-[12px] text-faint">Nenhum modelo. Adicione um (há sugestões no formulário).</p>}
          {estado.carregado && (
            <div className="flex items-center gap-2 text-[11.5px] text-faint">
              <span className="size-1.5 rounded-full bg-green-500" />
              <span className="min-w-0 flex-1 truncate" title="Fica na memória para o próximo áudio; sai sozinho depois de 5 min parado">
                {estado.carregado.nome} na memória ({estado.carregado.dispositivo})
              </span>
              <button className="text-accent-text" onClick={() => api.post("/tts/descarregar").then(carregarEstado).catch((e) => onError(e.message))}>Descarregar</button>
            </div>
          )}
        </Secao>
        {editando && (
          <div className="flex flex-col gap-2 rounded-[10px] border border-line bg-surface p-3">
            <div className="flex flex-wrap gap-1">
              {SUGESTOES.map((s) => <button key={s.nome} className="rounded-full border border-line px-2 py-0.5 text-[11px] hover:bg-raised" onClick={() => setEditando({ ...s })}>{s.nome}</button>)}
            </div>
            <input className={campo} placeholder="Nome" value={editando.nome} onChange={(e) => setEditando({ ...editando, nome: e.target.value })} />
            <select className={campo} value={editando.motor} onChange={(e) => setEditando({ ...VAZIO[e.target.value as Motor], nome: editando.nome })}>
              {(Object.keys(estado.motores) as Motor[]).map((k) => <option key={k} value={k}>{estado.motores[k].nome}{estado.motores[k].instalado ? "" : " (não instalado)"}</option>)}
            </select>
            {editando.motor === "fish" ? (
              <input className={campo} placeholder="dono/repo do Hugging Face ou pasta do modelo" value={editando.modelo ?? ""} onChange={(e) => setEditando({ ...editando, modelo: e.target.value })} />
            ) : (
              <>
                <select className={campo} value={editando.arquitetura} onChange={(e) => setEditando({ ...editando, arquitetura: e.target.value })}>
                  {estado.arquiteturas.map((a) => <option key={a}>{a}</option>)}
                </select>
                <input className={campo} placeholder="Checkpoint: caminho ou hf://dono/repo/arquivo (vazio = oficial)" value={editando.ckpt ?? ""} onChange={(e) => setEditando({ ...editando, ckpt: e.target.value })} />
                <input className={campo} placeholder="vocab.txt (vazio = o padrão do F5)" value={editando.vocab ?? ""} onChange={(e) => setEditando({ ...editando, vocab: e.target.value })} />
                <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" checked={!!editando.minusculas} onChange={(e) => setEditando({ ...editando, minusculas: e.target.checked })} />Texto em minúsculas</label>
                <label className="flex items-center gap-2 text-[12px]">Números por extenso
                  <input className={`${campo} w-20`} placeholder="pt_BR" value={editando.numeros ?? ""} onChange={(e) => setEditando({ ...editando, numeros: e.target.value })} />
                </label>
              </>
            )}
            <div className="flex gap-1.5">
              <button className={btnPrimary} onClick={salvarModelo}>Salvar</button>
              <button className={btn} onClick={() => setEditando(null)}>Fechar</button>
              {estado.modelos.some((x) => x.nome === editando.nome) && (
                <button className={`${btn} ml-auto text-red-400`} onClick={() => api.del<Modelo[]>(`/tts/modelos?nome=${encodeURIComponent(editando.nome)}`).then((modelos) => { setEstado({ ...estado, modelos }); setEditando(null); })}>Apagar</button>
              )}
            </div>
          </div>
        )}

        <Secao titulo="Voz de referência" extra={<button className="text-[11.5px] text-accent-text" onClick={() => setNovaVoz({ nome: "", texto: "", file: null })}>Adicionar</button>}>
          {estado.vozes.length ? (
            <>
              <select value={vozAtual} onChange={(e) => setVoz(e.target.value)} className={`${campo} cursor-pointer`}>
                {estado.vozes.map((v) => <option key={v.id} value={v.id}>{v.nome}</option>)}
              </select>
              {estado.vozes.filter((v) => v.id === vozAtual).map((v) => (
                <div key={v.id} className="flex flex-col gap-1">
                  <audio controls src={audioUrl(v.caminho)} className="h-8 w-full" />
                  <p className="text-[11.5px] text-faint">{v.texto || "Sem transcrição: o Whisper transcreve na 1ª geração e guarda."}</p>
                  <button className="self-start text-[11.5px] text-red-400" onClick={() => api.del<Voz[]>(`/tts/vozes/${v.id}`).then((vozes) => setEstado({ ...estado, vozes }))}>Apagar voz</button>
                </div>
              ))}
            </>
          ) : <p className="text-[12px] text-faint">Nenhuma voz. Envie 5 a 15 s de fala humana limpa: a saída copia o timbre da referência (voz sintética vira saída robótica).</p>}
        </Secao>
        {novaVoz && (
          <div className="flex flex-col gap-2 rounded-[10px] border border-line bg-surface p-3">
            <input className={campo} placeholder="Nome da voz" value={novaVoz.nome} onChange={(e) => setNovaVoz({ ...novaVoz, nome: e.target.value })} />
            <input type="file" accept=".wav,.mp3,.flac,.ogg,.m4a,audio/*" className="text-[12px]" onChange={(e) => setNovaVoz({ ...novaVoz, file: e.target.files?.[0] ?? null })} />
            <textarea rows={3} className={campo} placeholder="O que é dito no áudio (exato). Vazio = o Whisper transcreve." value={novaVoz.texto} onChange={(e) => setNovaVoz({ ...novaVoz, texto: e.target.value })} />
            <div className="flex gap-1.5">
              <button className={btnPrimary} onClick={salvarVoz}>Salvar</button>
              <button className={btn} onClick={() => setNovaVoz(null)}>Fechar</button>
            </div>
          </div>
        )}

        <Secao titulo="Ajustes">
          {m?.motor === "fish" ? (
            <>
              <label className="flex items-center gap-2" title="Mais alto = fala mais variada e solta; mais baixo = mais contida e previsível">Temperatura
                <input type="range" min={0.3} max={1.5} step={0.05} value={temperatura} onChange={(e) => setTemperatura(Number(e.target.value))} className="flex-1" />
                <span className="w-10 text-right font-mono text-[12px]">{temperatura.toFixed(2)}</span>
              </label>
              <label className="flex items-center gap-2" title="Corte de probabilidade da amostragem (padrão 0,9)">Top-p
                <input type="range" min={0.5} max={1} step={0.05} value={topP} onChange={(e) => setTopP(Number(e.target.value))} className="flex-1" />
                <span className="w-10 text-right font-mono text-[12px]">{topP.toFixed(2)}</span>
              </label>
            </>
          ) : (
            <>
              <label className="flex items-center gap-2">Velocidade
                <input type="range" min={0.5} max={2} step={0.05} value={velocidade} onChange={(e) => setVelocidade(Number(e.target.value))} className="flex-1" />
                <span className="w-10 text-right font-mono text-[12px]">{velocidade.toFixed(2)}</span>
              </label>
              <label className="flex items-center gap-2" title="Mais passos = mais qualidade e mais tempo (padrão 32)">Passos
                <input type="number" min={4} max={128} value={passos} onChange={(e) => setPassos(Number(e.target.value) || 32)} className={`${campo} ml-auto w-20`} />
              </label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={semSilencio} onChange={(e) => setSemSilencio(e.target.checked)} />Tirar silêncios longos</label>
            </>
          )}
          <label className="flex items-center gap-2" title="-1 = aleatória">Semente
            <input type="number" value={semente} onChange={(e) => setSemente(Number(e.target.value))} className={`${campo} ml-auto w-28`} />
          </label>
        </Secao>
      </aside>
    </div>
  );
}

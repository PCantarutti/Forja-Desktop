import { useCallback, useEffect, useRef, useState } from "react";
import { api, auth } from "../api";
import type { Message } from "../types";
import { Secao } from "./ImagensView";

// Tela Voz: texto para fala com modelos da família F5-TTS/E2-TTS (backend/app/tts.py). Cada geração é um par de
// mensagens da conversa kind="tts"; a do assistente leva meta.tts (estado, fase, arquivo, duração...).

type Modelo = { nome: string; arquitetura: string; ckpt: string; vocab: string; minusculas: boolean; numeros: string };
type Voz = { id: string; nome: string; texto: string; caminho: string };
type Estado = { instalado: boolean; gpu: string; backend: string; instalando: string; modelos: Modelo[]; vozes: Voz[]; arquiteturas: string[] };
type Job = { id: string; status: string; detail: string; error: string };

const POLL_MS = 1500;
const btn = "inline-flex items-center gap-1.5 rounded-[9px] border border-line px-3 py-1.5 text-[13px] hover:bg-raised disabled:opacity-40";
const btnPrimary = "inline-flex items-center gap-1.5 rounded-[9px] border border-accent bg-accent px-3 py-1.5 font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";
const campo = "w-full rounded-[8px] border border-line bg-surface px-2.5 py-1.5 text-[13px] outline-none focus:border-focus";
const audioUrl = (p: string) => `/api/tts/arquivo?path=${encodeURIComponent(p)}`;
// Sugestões para preencher o formulário (não instalam nada): o oficial e um fine-tune pt-br conhecido.
const SUGESTOES: Modelo[] = [
  { nome: "F5-TTS v1 (oficial, inglês/chinês)", arquitetura: "F5TTS_v1_Base", ckpt: "", vocab: "", minusculas: false, numeros: "" },
  { nome: "F5-TTS pt-br (firstpixel)", arquitetura: "F5TTS_Base", ckpt: "hf://firstpixel/F5-TTS-pt-br/pt-br/model_last.safetensors", vocab: "", minusculas: true, numeros: "pt_BR" },
];
const VAZIO: Modelo = { nome: "", arquitetura: "F5TTS_v1_Base", ckpt: "", vocab: "", minusculas: false, numeros: "" };

export default function TtsView(props: {
  conv: number | null;
  carimbo?: string;
  ensureConversation: () => Promise<number>;
  onError: (msg: string) => void;
  onConversationChanged: () => void;
}) {
  const [estado, setEstado] = useState<Estado | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [texto, setTexto] = useState("");
  const [modelo, setModelo] = useState(() => localStorage.getItem("tts.modelo") ?? "");
  const [voz, setVoz] = useState(() => localStorage.getItem("tts.voz") ?? "");
  const [velocidade, setVelocidade] = useState(1);
  const [passos, setPassos] = useState(32);
  const [semente, setSemente] = useState(-1);
  const [semSilencio, setSemSilencio] = useState(false);
  const [editando, setEditando] = useState<Modelo | null>(null);
  const [novaVoz, setNovaVoz] = useState<{ nome: string; texto: string; file: File | null } | null>(null);
  const fim = useRef<HTMLDivElement>(null);
  const { onError } = props;

  const carregarEstado = useCallback(async () => {
    try {
      const e = await api.get<Estado>("/tts");
      setEstado(e);
      if (e.instalando) {
        const jobs = (await api.get<{ jobs: Job[] }>("/local")).jobs;
        setJob(jobs.find((j) => j.id === e.instalando) ?? null);
      } else setJob((j) => (j && j.status === "running" ? { ...j, status: "pronto" } : j));
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
    if (!estado?.instalando) return;
    const t = setInterval(carregarEstado, POLL_MS);
    return () => clearInterval(t);
  }, [estado?.instalando, carregarEstado]);

  const rodando = messages.some((m) => m.role === "assistant" && m.status === "running");
  useEffect(() => {
    if (!rodando) return;
    const t = setInterval(carregarConversa, POLL_MS);
    return () => clearInterval(t);
  }, [rodando, carregarConversa]);
  useEffect(() => { fim.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length]);

  // escolha some quando o item foi apagado: cai no primeiro
  const modeloAtual = estado?.modelos.find((m) => m.nome === modelo)?.nome ?? estado?.modelos[0]?.nome ?? "";
  const vozAtual = estado?.vozes.find((v) => v.id === voz)?.id ?? estado?.vozes[0]?.id ?? "";

  async function gerar() {
    try {
      const id = await props.ensureConversation();
      await api.post(`/tts/${id}/gerar`, { texto, modelo: modeloAtual, voz: vozAtual, velocidade, passos, semente, sem_silencio: semSilencio });
      localStorage.setItem("tts.modelo", modeloAtual);
      localStorage.setItem("tts.voz", vozAtual);
      setTexto("");
      props.onConversationChanged();
      if (id === props.conv) carregarConversa();
    } catch (e: any) {
      onError(e.message);
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
    const q = new URLSearchParams({ nome: novaVoz.nome || novaVoz.file.name.replace(/\.\w+$/, ""), texto: novaVoz.texto });
    try {
      // nome e texto vão junto no multipart (enviarArquivo só manda `file`)
      const form = new FormData();
      form.append("file", novaVoz.file);
      q.forEach((v, k) => form.append(k, v));
      const r = await fetch("/api/tts/vozes", { method: "POST", body: form, headers: auth() });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
      const vozes: Voz[] = await r.json();
      setEstado((e) => e && { ...e, vozes });
      setVoz(vozes.find((v) => v.nome === q.get("nome"))?.id ?? voz);
      setNovaVoz(null);
    } catch (e: any) {
      onError(e.message);
    }
  }

  if (!estado) return <div className="flex-1" />;

  const pares: { pedido: Message; resposta: Message }[] = [];
  messages.forEach((m, i) => { if (m.role === "assistant" && m.meta?.tts) pares.push({ pedido: messages[i - 1] ?? m, resposta: m }); });

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        {!estado.instalado && (
          <div className="m-4 rounded-[10px] border border-line bg-surface p-4 text-[13px]">
            <div className="font-medium">Motor de voz não instalado</div>
            <p className="mt-1 text-muted">
              O Forja monta um Python próprio com PyTorch ({estado.backend === "xpu" ? "Intel XPU" : estado.backend === "auto" ? "NVIDIA CUDA" : "CPU"}, pela GPU {estado.gpu}) e o pacote f5-tts.
              Uns 3 GB, tudo dentro da pasta de runtimes.
            </p>
            {job?.status === "running" || estado.instalando ? (
              <p className="mt-2 font-mono text-[12px] text-muted">{job?.detail || "começando…"}</p>
            ) : (
              <button className={`${btnPrimary} mt-3`} onClick={() => api.post("/tts/instalar").then(carregarEstado).catch((e) => onError(e.message))}>
                Instalar motor
              </button>
            )}
            {job?.error && <p className="mt-2 text-[12px] text-red-400">{job.error}</p>}
          </div>
        )}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {!pares.length && <p className="mt-10 text-center text-[13px] text-faint">Escreva um texto embaixo para gerar a fala com a voz escolhida.</p>}
          <div className="mx-auto flex max-w-[760px] flex-col gap-4">
            {pares.map(({ pedido, resposta }) => {
              const t = resposta.meta!.tts;
              return (
                <div key={resposta.id} className="rounded-[10px] border border-line bg-surface p-3">
                  <p className="whitespace-pre-wrap text-[13.5px]">{pedido.content}</p>
                  <div className="mt-1 text-[11px] text-faint">{t.modelo} · {t.voz} · {t.velocidade}× · {t.passos} passos{t.semente_usada != null && ` · semente ${t.semente_usada}`}{t.segundos && ` · ${t.segundos} s`}</div>
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
          <div className="mx-auto flex max-w-[760px] gap-2">
            <textarea rows={3} value={texto} onChange={(e) => setTexto(e.target.value)} placeholder="Texto a falar…" className={`${campo} resize-y`}
                      onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && texto.trim()) gerar(); }} />
            <button className={`${btnPrimary} self-end`} disabled={!texto.trim() || !estado.instalado || !modeloAtual || !vozAtual} onClick={gerar}>
              Gerar
            </button>
          </div>
        </div>
      </div>

      <aside className="flex w-[300px] shrink-0 flex-col gap-5 overflow-y-auto border-l border-line p-4 text-[13px]">
        <Secao titulo="Modelo" extra={<button className="text-[11.5px] text-accent-text" onClick={() => setEditando({ ...VAZIO })}>Adicionar</button>}>
          {estado.modelos.length ? (
            <div className="flex gap-1.5">
              <select value={modeloAtual} onChange={(e) => setModelo(e.target.value)} className={`${campo} cursor-pointer`}>
                {estado.modelos.map((m) => <option key={m.nome}>{m.nome}</option>)}
              </select>
              <button className={btn} title="Editar" onClick={() => setEditando(estado.modelos.find((m) => m.nome === modeloAtual) ?? null)}>…</button>
            </div>
          ) : <p className="text-[12px] text-faint">Nenhum modelo. Adicione um (há sugestões no formulário).</p>}
        </Secao>
        {editando && (
          <div className="flex flex-col gap-2 rounded-[10px] border border-line bg-surface p-3">
            <div className="flex flex-wrap gap-1">
              {SUGESTOES.map((s) => <button key={s.nome} className="rounded-full border border-line px-2 py-0.5 text-[11px] hover:bg-raised" onClick={() => setEditando({ ...s })}>{s.nome}</button>)}
            </div>
            <input className={campo} placeholder="Nome" value={editando.nome} onChange={(e) => setEditando({ ...editando, nome: e.target.value })} />
            <select className={campo} value={editando.arquitetura} onChange={(e) => setEditando({ ...editando, arquitetura: e.target.value })}>
              {estado.arquiteturas.map((a) => <option key={a}>{a}</option>)}
            </select>
            <input className={campo} placeholder="Checkpoint: caminho ou hf://dono/repo/arquivo (vazio = oficial)" value={editando.ckpt} onChange={(e) => setEditando({ ...editando, ckpt: e.target.value })} />
            <input className={campo} placeholder="vocab.txt (vazio = o padrão do F5)" value={editando.vocab} onChange={(e) => setEditando({ ...editando, vocab: e.target.value })} />
            <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" checked={editando.minusculas} onChange={(e) => setEditando({ ...editando, minusculas: e.target.checked })} />Texto em minúsculas</label>
            <label className="flex items-center gap-2 text-[12px]">Números por extenso
              <input className={`${campo} w-20`} placeholder="pt_BR" value={editando.numeros} onChange={(e) => setEditando({ ...editando, numeros: e.target.value })} />
            </label>
            <div className="flex gap-1.5">
              <button className={btnPrimary} onClick={salvarModelo}>Salvar</button>
              <button className={btn} onClick={() => setEditando(null)}>Fechar</button>
              {estado.modelos.some((m) => m.nome === editando.nome) && (
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
                  <p className="text-[11.5px] text-faint">{v.texto || "Sem transcrição: o Whisper transcreve na geração."}</p>
                  <button className="self-start text-[11.5px] text-red-400" onClick={() => api.del<Voz[]>(`/tts/vozes/${v.id}`).then((vozes) => setEstado({ ...estado, vozes }))}>Apagar voz</button>
                </div>
              ))}
            </>
          ) : <p className="text-[12px] text-faint">Nenhuma voz. Envie 5 a 12 s de fala limpa.</p>}
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
          <label className="flex items-center gap-2">Velocidade
            <input type="range" min={0.5} max={2} step={0.05} value={velocidade} onChange={(e) => setVelocidade(Number(e.target.value))} className="flex-1" />
            <span className="w-10 text-right font-mono text-[12px]">{velocidade.toFixed(2)}</span>
          </label>
          <label className="flex items-center gap-2" title="Mais passos = mais qualidade e mais tempo (padrão 32)">Passos
            <input type="number" min={4} max={128} value={passos} onChange={(e) => setPassos(Number(e.target.value) || 32)} className={`${campo} ml-auto w-20`} />
          </label>
          <label className="flex items-center gap-2" title="-1 = aleatória">Semente
            <input type="number" value={semente} onChange={(e) => setSemente(Number(e.target.value))} className={`${campo} ml-auto w-28`} />
          </label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={semSilencio} onChange={(e) => setSemSilencio(e.target.checked)} />Tirar silêncios longos</label>
        </Secao>
      </aside>
    </div>
  );
}

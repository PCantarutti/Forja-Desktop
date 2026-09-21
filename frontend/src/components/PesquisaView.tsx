import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import type { Message, PesquisaEstado, PesquisaFonte } from "../types";
import { ArrowUp, Check, Copy, ExternalLink, Search, Square, X } from "./icons";
import { Markdown } from "./MessageView";

// Estas classes moram no LocalPanel.tsx, que é só do desktop. Repetidas aqui para esta aba viajar
// inteira num cherry-pick para o forja-web. ponytail: 4 linhas custam menos que um módulo de estilo.
const card = "rounded-2xl border border-line bg-surface p-3.5";
const btn = "rounded-full border border-line px-3 py-1 text-fg hover:bg-raised disabled:opacity-40";
const btnPrimary = "rounded-full bg-fg px-3 py-1 font-medium text-black hover:bg-white disabled:opacity-40";
const campo = "rounded-lg border border-line bg-raised px-2 py-1 text-xs text-fg focus:border-[#555] focus:outline-none";

const PROFUNDIDADES = [
  { id: "rapida" as const, label: "Rápida", hint: "1 rodada, 3 páginas — resposta em minutos" },
  { id: "normal" as const, label: "Normal", hint: "2 rodadas, até 8 páginas" },
  { id: "funda" as const, label: "Funda", hint: "4 rodadas, até 16 páginas — pode levar 10 min" },
];

/** As 4 etapas fixas, na ordem em que o backend passa por elas. */
const FASES: { id: PesquisaEstado["fase"]; label: string }[] = [
  { id: "planejando", label: "Planejar" },
  { id: "buscando", label: "Buscar" },
  { id: "lendo", label: "Ler fontes" },
  { id: "escrevendo", label: "Escrever" },
];

const CORES: Record<PesquisaFonte["status"], string> = {
  fila: "text-faint",
  lendo: "text-sky-300",
  util: "text-emerald-300",
  vazia: "text-faint",
  erro: "text-red-300",
};

const ROTULOS: Record<PesquisaFonte["status"], string> = {
  fila: "na fila",
  lendo: "lendo…",
  util: "útil",
  vazia: "nada aproveitável",
  erro: "erro",
};

const situacao = (s: string | null | undefined): PesquisaEstado["status"] =>
  s === "running" ? "rodando" : s === "erro" || s === "cancelado" ? s : "pronto";

const relogio = (seg: number) => `${Math.floor(seg / 60)}:${String(Math.floor(seg % 60)).padStart(2, "0")}`;

function comoMarkdown(e: PesquisaEstado): string {
  const uteis = e.fontes.filter((f) => f.status === "util");
  return [`# ${e.pergunta}`, "", e.relatorio || e.resumo || e.aviso, "", "## Fontes", "",
    ...uteis.map((f) => `- [${f.titulo}](${f.url})`), ""].join("\n");
}

export default function PesquisaView(props: {
  conv: number | null;
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
  onAbrirChat: (conv: number) => void;
}) {
  const [pergunta, setPergunta] = useState("");
  const [profundidade, setProfundidade] = useState<"rapida" | "normal" | "funda">("normal");
  const [perguntarAntes, setPerguntarAntes] = useState(false);
  const [esclarecer, setEsclarecer] = useState<{ perguntas: string[]; respostas: string[] } | null>(null);
  const [preparando, setPreparando] = useState(false);
  const [estado, setEstado] = useState<PesquisaEstado | null>(null);
  const [aberta, setAberta] = useState<string>("");   // fonte expandida
  const [copiado, setCopiado] = useState(false);
  const acompanhando = useRef(0);

  const rodando = estado?.status === "rodando";

  const ouvir = useCallback(
    async (messageId: number) => {
      if (acompanhando.current === messageId) return;
      acompanhando.current = messageId;
      try {
        await streamSSE(`/pesquisa/${messageId}/stream`, {}, (ev) => !ev.erro && setEstado(ev));
      } catch (e: any) {
        props.onError(e.message);
      } finally {
        acompanhando.current = 0;
      }
    },
    [props.onError],
  );

  /** Reabre a última pesquisa da conversa (e volta a ouvir, se ainda estiver rodando). */
  const carregarConversa = useCallback(
    async (id: number | null = props.conv) => {
      if (id === null) {
        setEstado(null);
        return;
      }
      try {
        const c = await api.get<{ messages: Message[] }>(`/conversations/${id}`);
        const i = c.messages.map((m) => !!(m.meta as any)?.pesquisa).lastIndexOf(true);
        if (i < 0) return setEstado(null);
        const m = c.messages[i];
        const p = (m.meta as any).pesquisa as PesquisaEstado;
        setPergunta(p.pergunta);
        setProfundidade(p.profundidade);
        setEstado({ ...p, message_id: m.id, status: situacao(m.status), relatorio: m.content || "" });
        if (situacao(m.status) === "rodando") ouvir(m.id);
      } catch (e: any) {
        props.onError(e.message);
      }
    },
    [props.conv, props.onError, ouvir],
  );

  useEffect(() => {
    carregarConversa();
  }, [carregarConversa]);

  async function rodar(contexto = "", continuar_de = 0) {
    if (!pergunta.trim()) return;
    try {
      const id = await props.ensureConversation();
      setEsclarecer(null);
      setEstado(null);
      await streamSSE(`/pesquisa/${id}/rodar`,
        { method: "POST", body: JSON.stringify({ pergunta, profundidade, contexto, continuar_de,
                                                 provider: props.provider, model: props.model }) },
        (ev) => (ev.erro ? props.onError(ev.erro) : setEstado(ev)));
      props.onConversationChanged();
      carregarConversa(id);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  /** Com "Perguntar antes" ligado, o primeiro envio vira esclarecimento; o segundo pesquisa. */
  async function enviar() {
    if (!pergunta.trim() || rodando) return;
    if (!perguntarAntes) return rodar();
    setPreparando(true);
    try {
      const r = await api.post<{ perguntas: string[] }>("/pesquisa/perguntas",
        { pergunta, provider: props.provider, model: props.model });
      if (!r.perguntas.length) return rodar();
      setEsclarecer({ perguntas: r.perguntas, respostas: r.perguntas.map(() => "") });
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setPreparando(false);
    }
  }

  function comRespostas() {
    const contexto = (esclarecer?.perguntas ?? [])
      .map((q, i) => (esclarecer!.respostas[i].trim() ? `${q} ${esclarecer!.respostas[i].trim()}` : ""))
      .filter(Boolean).join("\n");
    rodar(contexto);
  }

  async function parar() {
    if (estado) await api.post(`/pesquisa/${estado.message_id}/cancelar`, {}).catch(() => {});
  }

  async function discutir() {
    if (!estado) return;
    try {
      const r = await api.post<{ conversation_id: number }>(`/pesquisa/${estado.message_id}/discutir`, {});
      props.onConversationChanged();
      props.onAbrirChat(r.conversation_id);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  function copiar() {
    if (!estado) return;
    navigator.clipboard.writeText(comoMarkdown(estado));
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1500);
  }

  function baixar() {
    if (!estado) return;
    const url = URL.createObjectURL(new Blob([comoMarkdown(estado)], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `pesquisa-${estado.message_id}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const feita = FASES.findIndex((f) => f.id === estado?.fase);
  const atual = estado?.fase === "pronto" ? FASES.length : feita;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mx-auto flex max-w-4xl flex-col gap-3">
          {!estado && (
            <div className={`${card} text-xs text-muted`}>
              <p className="text-sm text-fg">Uma pergunta, várias páginas lidas, um relatório com fontes.</p>
              <p className="mt-1">
                O Forja planeja as buscas, lê as páginas uma a uma e escreve o relatório citando de onde
                tirou cada coisa. Aqui na aba fica só o resumo: o relatório completo abre numa janela do
                navegador, pronto para ler ou virar PDF.
              </p>
            </div>
          )}

          {estado && (
            <>
              <div className={card}>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                  {FASES.map((f, i) => (
                    <span key={f.id} className={i < atual ? "text-muted" : i === atual ? "text-sky-300" : "text-faint"}>
                      <span className="mr-1">{i < atual ? "✓" : i === atual ? "›" : "·"}</span>
                      {f.label}
                    </span>
                  ))}
                  <span className="ml-auto text-faint">
                    {estado.rodada > 0 && `rodada ${estado.rodada} · `}
                    {estado.stats.uteis} úteis de {estado.stats.fontes} · {relogio(estado.stats.segundos)}
                  </span>
                </div>
                {estado.aviso && <p className="mt-2 text-xs text-amber-300">{estado.aviso}</p>}
                {!!estado.plano.perguntas.length && (
                  <details className="mt-2 text-xs text-muted">
                    <summary className="cursor-pointer text-faint hover:text-fg">Plano da pesquisa</summary>
                    <ul className="mt-1 list-disc pl-5">
                      {estado.plano.perguntas.map((p) => <li key={p}>{p}</li>)}
                    </ul>
                    {estado.rodadas.map((r) => (
                      <p key={r.n} className="mt-1 text-faint">Rodada {r.n}: {r.buscas.join(" · ")}</p>
                    ))}
                  </details>
                )}
              </div>

              {!!estado.fontes.length && (
                <div className={card}>
                  {estado.fontes.map((f) => (
                    <div key={f.id} className="border-t border-line py-1.5 text-xs first:border-0 first:pt-0">
                      <div className="flex items-center gap-2">
                        <button className="min-w-0 flex-1 truncate text-left text-fg hover:underline"
                                onClick={() => setAberta(aberta === f.id ? "" : f.id)}>
                          {f.titulo}
                        </button>
                        <span className="shrink-0 text-faint">{f.dominio}</span>
                        <span className={`shrink-0 ${CORES[f.status]}`}>{ROTULOS[f.status]}</span>
                        <a href={f.url} target="_blank" rel="noreferrer" title="Abrir a página"
                           className="shrink-0 text-faint hover:text-fg">
                          <ExternalLink className="size-3.5" />
                        </a>
                      </div>
                      {aberta === f.id && (
                        <div className="mt-1 border-l border-line pl-3 text-muted">
                          <p>{f.resumo || f.erro || "Sem resumo."}</p>
                          {f.trecho && <p className="mt-1 italic text-faint">“{f.trecho}”</p>}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {estado.resumo && (
                <div className={card}>
                  <p className="text-[11px] uppercase tracking-wider text-faint">Resumo</p>
                  <div className="mt-1 text-sm">
                    <Markdown text={estado.resumo} />
                  </div>
                </div>
              )}

              {!rodando && (
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  {estado.relatorio && (
                    <button className={btnPrimary}
                            onClick={() => window.open(`/api/pesquisa/${estado.message_id}/relatorio`)}>
                      <ExternalLink className="mr-1 inline size-3.5" />
                      Abrir relatório
                    </button>
                  )}
                  {estado.relatorio && (
                    <button className={btn} onClick={() => rodar("", estado.message_id)}>Continuar pesquisa</button>
                  )}
                  {estado.relatorio && <button className={btn} onClick={discutir}>Discutir no chat</button>}
                  <button className={btn} onClick={copiar}>
                    {copiado ? <Check className="mr-1 inline size-3.5" /> : <Copy className="mr-1 inline size-3.5" />}
                    Copiar .md
                  </button>
                  <button className={btn} onClick={baixar}>Baixar .md</button>
                  <span className="text-faint">
                    extração: {estado.stats.extrator} · relatório: {estado.stats.escritor}
                  </span>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <div className="shrink-0 px-5 pb-4">
        <div className="mx-auto max-w-4xl">
          {esclarecer && (
            <div className={`${card} mb-2 flex flex-col gap-2`}>
              <div className="flex items-center justify-between text-xs">
                <span className="text-fg">Antes de buscar, para focar a pesquisa:</span>
                <button className="text-faint hover:text-fg" onClick={() => setEsclarecer(null)}>
                  <X className="size-4" />
                </button>
              </div>
              {esclarecer.perguntas.map((q, i) => (
                <label key={q} className="flex flex-col gap-1 text-xs text-muted">
                  {q}
                  <input className={campo} value={esclarecer.respostas[i]}
                         onChange={(e) => setEsclarecer((c) => c && {
                           ...c, respostas: c.respostas.map((r, j) => (j === i ? e.target.value : r)) })} />
                </label>
              ))}
              <div className="flex gap-2">
                <button className={btnPrimary} onClick={comRespostas}>Pesquisar</button>
                <button className={btn} onClick={() => rodar()}>Pular</button>
              </div>
            </div>
          )}

          <div className={`${card} flex flex-col gap-2.5`}>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <div className="flex rounded-full border border-line p-0.5" role="radiogroup" aria-label="Profundidade">
                {PROFUNDIDADES.map((p) => (
                  <button key={p.id} role="radio" aria-checked={profundidade === p.id} title={p.hint}
                          onClick={() => setProfundidade(p.id)}
                          className={`rounded-full px-2.5 py-0.5 ${profundidade === p.id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
                    {p.label}
                  </button>
                ))}
              </div>
              <label className="flex items-center gap-1.5 text-muted">
                <input type="checkbox" checked={perguntarAntes}
                       onChange={(e) => setPerguntarAntes(e.target.checked)} />
                Perguntar antes
              </label>
              <span className="text-faint">{props.model || "escolha um modelo no Chat"}</span>
            </div>

            <div className="flex items-end gap-2">
              <textarea
                rows={2}
                value={pergunta}
                onChange={(e) => setPergunta(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    enviar();
                  }
                }}
                placeholder="O que você quer descobrir?"
                className="flex-1 resize-none rounded-xl border border-line bg-raised px-3 py-2 text-sm text-fg focus:border-[#555] focus:outline-none"
              />
              {rodando ? (
                <button className={btnPrimary} onClick={parar} title="Parar">
                  <Square className="size-3.5" />
                </button>
              ) : (
                <button className={btnPrimary} disabled={!pergunta.trim() || preparando}
                        onClick={enviar} title="Pesquisar">
                  {preparando ? <Search className="size-4 animate-pulse" /> : <ArrowUp className="size-4" />}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import type { Message, PesquisaEstado, PesquisaFonte, PesquisaFormato, PesquisaProfundidade }
  from "../types";
import { UsageBars, useCloudUsage } from "./CloudUsage";
import type { CloudUsage } from "../types";
import { ArrowUp, Check, Copy, ExternalLink, Search, Square, X } from "./icons";
import { Markdown } from "./MessageView";
import ModelPicker from "./ModelPicker";
import Sinapse from "./Sinapse";

// Estas classes moram no LocalPanel.tsx, que é só do desktop. Repetidas aqui para esta aba viajar
// inteira num cherry-pick para o forja-web. ponytail: 4 linhas custam menos que um módulo de estilo.
const card = "rounded-2xl border border-line bg-surface p-3.5";
const btn = "rounded-full border border-line px-3 py-1 text-fg hover:bg-raised disabled:opacity-40";
const btnPrimary = "rounded-full bg-fg px-3 py-1 font-medium text-black hover:bg-white disabled:opacity-40";
const campo = "rounded-lg border border-line bg-raised px-2 py-1 text-xs text-fg focus:border-[#555] focus:outline-none";

// Os dois modelos da pesquisa são escolha desta aba, não do Chat: quem lê 12 páginas costuma
// querer um modelo pequeno na extração e o bom só no relatório.
const KEY_MODELOS = "forja.pesquisa.modelos";

// Os números espelham os PRESETS do backend: cada profundidade é uma promessa diferente, não só
// mais rodadas — muda quanto de cada página é lido e o tamanho do relatório.
const PROFUNDIDADES = [
  { id: "rapida" as const, label: "Rápida", minutos: 5, rodadas: 1,
    hint: "1 rodada, 3 páginas, relatório de ~800 palavras — cabe num modelo local pequeno" },
  { id: "normal" as const, label: "Normal", minutos: 10, rodadas: 2,
    hint: "2 rodadas, 5 páginas por rodada, relatório de ~1200 palavras" },
  { id: "funda" as const, label: "Funda", minutos: 30, rodadas: 4,
    hint: "4 rodadas, 8 páginas por rodada, relatório de 1800 a 3000 palavras com subseções; "
      + "o relatório é refeito a cada rodada. Peça a um modelo grande." },
  { id: "personalizado" as const, label: "Personalizado", minutos: 10, rodadas: 2,
    hint: "Você escolhe rodadas e tempo; leitura e relatório no tamanho da Funda" },
];
const MIN_MINUTOS = 1;
const MAX_MINUTOS = 120;   // mesmos limites do backend (TETO_MIN/TETO_MAX)
const MAX_RODADAS = 8;     // RODADAS_MAX do backend

const FORMATOS: { id: PesquisaFormato; label: string; hint: string }[] = [
  { id: "auto", label: "Auto", hint: "O modelo decide o feitio do relatório pela pergunta" },
  { id: "produto", label: "Produto", hint: "Lista ordenada com preço, prós, contras e veredito" },
  { id: "comparar", label: "Comparar", hint: "Tabela comparativa e uma seção por opção" },
  { id: "guia", label: "Guia", hint: "Resumo rápido, pré-requisitos e passo a passo" },
  { id: "checagem", label: "Checagem", hint: "A afirmação, evidências dos dois lados e veredito" },
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

/** "1.234 tokens · 38 tok/s · 03:12" — o que dá para dizer com o que o provedor devolveu. */
function numeros(e: PesquisaEstado): string {
  const s = e.stats;
  const tps = s.gerando > 0.5 ? Math.round(s.tokens / s.gerando) : 0;
  return [
    s.tokens ? `${s.tokens.toLocaleString("pt-BR")} tokens${s.estimado ? " (estim.)" : ""}` : "",
    tps ? `${tps} tok/s` : "",
    relogio(s.segundos),
  ].filter(Boolean).join(" · ");
}

function comoMarkdown(e: PesquisaEstado): string {
  const uteis = e.fontes.filter((f) => f.status === "util");
  return [`# ${e.pergunta}`, "", e.relatorio || e.resumo || e.aviso, "", "## Fontes", "",
    ...uteis.map((f) => `- [${f.titulo}](${f.url})`), ""].join("\n");
}

type Par = { provider: string; model: string };
type Modelos = { escritor: Par; extrator: Par | null };  // extrator null = slot "rapido" dos subagentes

/** Número com a unidade dentro da mesma pílula, para a linha não virar caixinha solta + texto solto. */
function Numero(props: { valor: number; min: number; max: number; unidade: string; dica: string;
                         onChange: (v: number) => void }) {
  return (
    <label title={props.dica}
           className="flex items-center gap-1 rounded-full border border-line bg-raised px-2 py-0.5
                      text-muted focus-within:border-[#555]">
      <input type="number" min={props.min} max={props.max} value={props.valor}
             onChange={(e) => props.onChange(Number(e.target.value) || props.min)}
             className="w-8 bg-transparent text-right text-fg outline-none" />
      {props.unidade}
    </label>
  );
}

/** Anel da cota do plano, irmão do anel de contexto do chat: preenche com a janela mais apertada. */
function AnelCota({ dados }: { dados: CloudUsage[] }) {
  const [aberto, setAberto] = useState(false);
  const caixa = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!aberto) return;
    const fora = (e: MouseEvent) => !caixa.current?.contains(e.target as Node) && setAberto(false);
    document.addEventListener("mousedown", fora);
    return () => document.removeEventListener("mousedown", fora);
  }, [aberto]);

  const pct = Math.min(1, Math.max(0, ...dados.flatMap((d) => d.limits.map((l) => l.usage))));
  const r = 7;
  const c = 2 * Math.PI * r;
  const cor = pct >= 0.9 ? "#f87171" : pct >= 0.7 ? "#fbbf24" : "#a3a3a3";
  const titulo = `Cota do plano: ${Math.round(pct * 100)}% da janela mais apertada`;

  return (
    <div ref={caixa} className="relative">
      <button onClick={() => setAberto((v) => !v)} title={titulo} aria-label={titulo}
              className="grid size-8 place-items-center rounded-full text-muted hover:bg-raised hover:text-fg">
        <svg viewBox="0 0 20 20" className="size-5 -rotate-90">
          <circle cx="10" cy="10" r={r} fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
          <circle cx="10" cy="10" r={r} fill="none" stroke={cor} strokeWidth="2.5" strokeLinecap="round"
                  strokeDasharray={c} strokeDashoffset={c * (1 - pct)}
                  style={{ transition: "stroke-dashoffset 400ms ease" }} />
        </svg>
      </button>
      {aberto && (
        <div className="absolute bottom-full left-0 z-30 mb-2 w-60 rounded-xl border border-line bg-surface p-3 font-mono text-xs text-muted shadow-xl">
          {dados.map((d) => (
            <div key={d.provider} className="mb-2 last:mb-0">
              <div className="mb-1.5 text-faint">Cota · {d.name}</div>
              <UsageBars data={d} models />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function PesquisaView(props: {
  conv: number | null;
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
  onAbrirChat: (conv: number) => void;
  onTerminou: (titulo: string, corpo: string) => void;   // badge na barra lateral + notificação
}) {
  const [texto, setTexto] = useState("");   // o que está no campo; a pergunta da corrida vive no estado
  const [profundidade, setProfundidade] = useState<PesquisaProfundidade>("normal");
  const [rodadas, setRodadas] = useState(2);   // só vale no modo personalizado
  const [modelos, setModelos] = useState(() => {
    try {
      const salvo = JSON.parse(localStorage.getItem(KEY_MODELOS) ?? "null");
      if (salvo?.escritor?.model) return salvo as Modelos;
    } catch {
      /* localStorage corrompido: cai no par do Chat */
    }
    return { escritor: { provider: props.provider, model: props.model }, extrator: null } as Modelos;
  });
  const [formato, setFormato] = useState<PesquisaFormato>("auto");
  // Tempo máximo: começa no do preset e acompanha a troca de preset até você digitar o seu.
  const [minutos, setMinutos] = useState(10);
  const [perguntarAntes, setPerguntarAntes] = useState(false);
  const [esclarecer, setEsclarecer] =
    useState<{ pergunta: string; perguntas: string[]; respostas: string[] } | null>(null);
  const [preparando, setPreparando] = useState(false);
  const [estado, setEstado] = useState<PesquisaEstado | null>(null);
  const [aberta, setAberta] = useState<string>("");   // fonte expandida
  const [copiado, setCopiado] = useState(false);
  const [terminou, setTerminou] = useState<PesquisaEstado | null>(null);   // faixa de "acabou"
  const statusAnterior = useRef<string>("");
  const acompanhando = useRef(0);   // message_id sendo ouvido: não abre dois SSE para o mesmo
  const corte = useRef<AbortController | null>(null);
  // Os callbacks do App chegam recriados a cada render dele. Guardados em ref, as funções abaixo
  // ficam estáveis — senão o efeito que carrega a conversa reentra em laço a cada render.
  const aoErro = useRef(props.onError);
  const aoTerminar = useRef(props.onTerminou);
  aoErro.current = props.onError;
  aoTerminar.current = props.onTerminou;

  const rodando = estado?.status === "rodando";

  /** Todo evento passa por aqui: é onde a corrida que acaba vira faixa na tela e notificação. */
  const receber = useCallback((novo: PesquisaEstado) => {
    setEstado(novo);
    const antes = statusAnterior.current;
    statusAnterior.current = novo.status;
    if (antes !== "rodando" || novo.status === "rodando") return;
    setTerminou(novo);
    const fim = novo.status === "pronto"
      ? `${novo.stats.uteis} fontes úteis em ${relogio(novo.stats.segundos)}`
      : novo.aviso || novo.status;
    aoTerminar.current(novo.status === "pronto" ? "Pesquisa concluída" : "Pesquisa encerrada",
                       `${novo.pergunta} — ${fim}`);
  }, []);

  /** Acompanha UMA pesquisa. Trocar de conversa aborta o stream anterior: sem isso, o evento da
   *  pesquisa em andamento pintava a tela da conversa recém-aberta. */
  const ouvir = useCallback(
    async (messageId: number) => {
      if (acompanhando.current === messageId) return;
      corte.current?.abort();
      const ctl = new AbortController();
      corte.current = ctl;
      acompanhando.current = messageId;
      try {
        await streamSSE(`/pesquisa/${messageId}/stream`, { signal: ctl.signal },
          (ev) => !ev.erro && !ctl.signal.aborted && receber(ev));
      } catch (e: any) {
        if (!ctl.signal.aborted) aoErro.current(e.message);
      } finally {
        if (acompanhando.current === messageId) acompanhando.current = 0;
      }
    },
    [receber],
  );

  /** Reabre a última pesquisa da conversa (e volta a ouvir, se ainda estiver rodando). */
  const carregarConversa = useCallback(
    async (id: number | null = props.conv) => {
      corte.current?.abort();   // o stream da conversa anterior morre aqui
      acompanhando.current = 0;
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
        setProfundidade(p.profundidade);
        setFormato(p.formato || "auto");
        if (p.teto_segundos) setMinutos(Math.round(p.teto_segundos / 60));
        if (p.rodadas_total) setRodadas(p.rodadas_total);
        statusAnterior.current = situacao(m.status);
        setTerminou(null);
        setEstado({ ...p, message_id: m.id, status: situacao(m.status), relatorio: m.content || "" });
        if (situacao(m.status) === "rodando") ouvir(m.id);
      } catch (e: any) {
        aoErro.current(e.message);
      }
    },
    [props.conv, ouvir],
  );

  useEffect(() => {
    carregarConversa();
  }, [carregarConversa]);

  useEffect(() => {
    localStorage.setItem(KEY_MODELOS, JSON.stringify(modelos));
  }, [modelos]);

  useEffect(() => () => corte.current?.abort(), []);   // sair da aba encerra o stream

  async function rodar(pergunta: string, contexto = "", continuar_de = 0) {
    if (!pergunta.trim()) return;
    // A permissão é pedida aqui porque a pesquisa é justamente o que a pessoa deixa rodando de lado.
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
    try {
      const id = await props.ensureConversation();
      corte.current?.abort();   // pesquisa nova: o stream da anterior não escreve mais aqui
      acompanhando.current = 0;
      setTerminou(null);
      const ctl = new AbortController();
      corte.current = ctl;
      setEsclarecer(null);
      setEstado(null);
      setTexto("");   // a pergunta agora vive na corrida; o campo fica livre para a próxima
      await streamSSE(`/pesquisa/${id}/rodar`,
        { method: "POST", signal: ctl.signal, body: JSON.stringify({
            pergunta, profundidade, formato, contexto, continuar_de,
            provider: modelos.escritor.provider, model: modelos.escritor.model,
            ex_provider: modelos.extrator?.provider ?? "", ex_model: modelos.extrator?.model ?? "",
            teto: Math.min(Math.max(minutos, MIN_MINUTOS), MAX_MINUTOS) * 60,
            rodadas: Math.min(Math.max(rodadas, 1), MAX_RODADAS) }) },
        (ev) => {
          if (ctl.signal.aborted) return;   // trocou de conversa no meio: não pinta a tela nova
          if (ev.erro) props.onError(ev.erro);
          else receber(ev);
        });
      props.onConversationChanged();
      if (!ctl.signal.aborted) carregarConversa(id);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  /** Com "Perguntar antes" ligado, o primeiro envio vira esclarecimento; o segundo pesquisa. */
  async function enviar() {
    const pergunta = texto.trim();
    if (!pergunta || rodando) return;
    if (!perguntarAntes) return rodar(pergunta);
    setPreparando(true);
    try {
      const r = await api.post<{ perguntas: string[] }>("/pesquisa/perguntas",
        { pergunta, provider: modelos.escritor.provider, model: modelos.escritor.model });
      if (!r.perguntas.length) return rodar(pergunta);
      setEsclarecer({ pergunta, perguntas: r.perguntas, respostas: r.perguntas.map(() => "") });
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setPreparando(false);
    }
  }

  function comRespostas() {
    if (!esclarecer) return;
    const contexto = esclarecer.perguntas
      .map((q, i) => (esclarecer.respostas[i].trim() ? `${q} ${esclarecer.respostas[i].trim()}` : ""))
      .filter(Boolean).join("\n");
    rodar(esclarecer.pergunta, contexto);
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

  // Cota do Ollama Cloud: só consulta quando um dos dois modelos é de um provedor da nuvem.
  const nuvem = useCloudUsage(true).filter(
    (u) => u.provider === modelos.escritor.provider || u.provider === modelos.extrator?.provider ||
      u.provider === estado?.stats.escritor_provider || u.provider === estado?.stats.extrator_provider);

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

          {estado && rodando && <Sinapse estado={estado} />}

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
                    {estado.stats.uteis} úteis de {estado.stats.fontes} · {numeros(estado)}
                  </span>
                </div>
                <p className="mt-2 text-sm text-fg">{estado.pergunta}</p>
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

          {terminou && (
            <div className={`flex items-center gap-2 rounded-2xl border px-3.5 py-2.5 text-sm ${
              terminou.status === "pronto"
                ? "border-emerald-800/70 bg-emerald-950/30 text-emerald-200"
                : "border-amber-800/70 bg-amber-950/30 text-amber-200"}`}>
              <Check className="size-4 shrink-0" />
              <span className="min-w-0 flex-1">
                {terminou.status === "pronto"
                  ? `Pesquisa concluída · ${terminou.stats.uteis} fontes úteis · ${relogio(terminou.stats.segundos)}`
                  : `Pesquisa ${terminou.status} · ${terminou.aviso}`}
              </span>
              {terminou.relatorio && (
                <button className={btn}
                        onClick={() => window.open(`/api/pesquisa/${terminou.message_id}/relatorio`)}>
                  Abrir relatório
                </button>
              )}
              <button className="text-faint hover:text-fg" title="Dispensar" onClick={() => setTerminou(null)}>
                <X className="size-4" />
              </button>
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
                    <button className={btn}
                            onClick={() => rodar(estado.pergunta, "", estado.message_id)}>
                      Continuar pesquisa
                    </button>
                  )}
                  {estado.relatorio && <button className={btn} onClick={discutir}>Discutir no chat</button>}
                  <button className={btn} onClick={copiar}>
                    {copiado ? <Check className="mr-1 inline size-3.5" /> : <Copy className="mr-1 inline size-3.5" />}
                    Copiar .md
                  </button>
                  <button className={btn} onClick={baixar}>Baixar .md</button>
                  <span className="text-faint">
                    extração: {estado.stats.extrator} · relatório: {estado.stats.escritor}
                    {estado.formato_usado && ` · formato: ${estado.formato_usado}`} · {numeros(estado)}
                  </span>
                </div>
              )}
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
                <button className={btn} onClick={() => rodar(esclarecer.pergunta)}>Pular</button>
              </div>
            </div>
          )}

          <div className={`${card} flex flex-col gap-2.5`}>
            {/* Duas linhas com papéis distintos: "como pesquisar" em cima, "com quais modelos"
                embaixo. Numa linha só isso quebrava em qualquer largura e virava salada. */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
              <div className="flex shrink-0 rounded-full border border-line p-0.5" role="radiogroup"
                   aria-label="Profundidade">
                {PROFUNDIDADES.map((p) => (
                  <button key={p.id} role="radio" aria-checked={profundidade === p.id} title={p.hint}
                          onClick={() => {
                            setProfundidade(p.id);
                            if (p.id !== "personalizado") {
                              setMinutos(p.minutos);
                              setRodadas(p.rodadas);
                            }
                          }}
                          className={`rounded-full px-2.5 py-0.5 ${profundidade === p.id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
                    {p.label}
                  </button>
                ))}
              </div>

              <div className="flex shrink-0 items-center gap-2">
                {profundidade === "personalizado" && (
                  <Numero valor={rodadas} min={1} max={MAX_RODADAS} unidade="rodadas"
                          dica="Quantas rodadas de busca" onChange={setRodadas} />
                )}
                <Numero valor={minutos} min={MIN_MINUTOS} max={MAX_MINUTOS} unidade="min"
                        dica="Tempo máximo da pesquisa" onChange={setMinutos} />
              </div>

              <span className="h-4 w-px shrink-0 bg-line" />

              <label className="flex shrink-0 items-center gap-1.5 text-faint" title="Feitio do relatório">
                formato
                <select className={campo} value={formato}
                        onChange={(e) => setFormato(e.target.value as PesquisaFormato)}>
                  {FORMATOS.map((f) => <option key={f.id} value={f.id} title={f.hint}>{f.label}</option>)}
                </select>
              </label>

              <label className="flex shrink-0 items-center gap-1.5 text-muted"
                     title="O modelo faz 2 ou 3 perguntas curtas antes de sair buscando">
                <input type="checkbox" checked={perguntarAntes}
                       onChange={(e) => setPerguntarAntes(e.target.checked)} />
                Perguntar antes
              </label>
            </div>

            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
              {/* Div à parte em cada um: o ModelPicker traz ml-auto por dentro. */}
              <div className="flex shrink-0 items-center gap-1.5" title="Modelo que escreve o relatório">
                <span className="text-faint">relatório</span>
                <ModelPicker provider={modelos.escritor.provider} model={modelos.escritor.model}
                             onChange={(provider, model) =>
                               setModelos((m) => ({ ...m, escritor: { provider, model } }))} />
              </div>

              <span className="h-4 w-px shrink-0 bg-line" />

              <div className="flex shrink-0 items-center gap-1.5" title="Modelo que lê e resume cada página">
                <span className="text-faint">extração</span>
                {modelos.extrator ? (
                  <>
                    <ModelPicker provider={modelos.extrator.provider} model={modelos.extrator.model}
                                 onChange={(provider, model) =>
                                   setModelos((m) => ({ ...m, extrator: { provider, model } }))} />
                    <button className="text-faint hover:text-fg" title="Voltar ao automático"
                            onClick={() => setModelos((m) => ({ ...m, extrator: null }))}>
                      <X className="size-3" />
                    </button>
                  </>
                ) : (
                  <button className={`${campo} text-muted`}
                          title="Automático: usa o subagente Rápido, ou o mesmo modelo do relatório"
                          onClick={() => setModelos((m) => ({ ...m, extrator: { ...m.escritor } }))}>
                    automático
                  </button>
                )}
              </div>

              {!!nuvem.length && (
                <div className="ml-auto shrink-0">
                  <AnelCota dados={nuvem} />
                </div>
              )}
            </div>

            <div className="flex items-end gap-2">
              <textarea
                rows={2}
                value={texto}
                onChange={(e) => setTexto(e.target.value)}
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
                <button className={btnPrimary} disabled={!texto.trim() || preparando}
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

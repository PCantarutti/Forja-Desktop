import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import { useStickyBottom } from "../useStickyBottom";
import type { CompararEntrada, CompararEstado, CompararItem, Message, PlacarLinha } from "../types";
import { ArrowUp, Balanca, Check, Copy, Cube, Eye, EyeOff, Gauge, Plus, Refresh, Split, Square, Trash, X } from "./icons";
import { Markdown, TestarCodigo, Thinking } from "./MessageView";
import ModelPicker from "./ModelPicker";
import { BotaoEnviar, CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt, pilula, pilulaLigada } from "./Composer";
import { Menu } from "./Controls";

// Mesma linguagem visual do cockpit da Maestro: cartões escuros com borda fina e títulos em caixa
// alta pequena. Repetidas aqui para esta aba viajar inteira num cherry-pick para o forja-web.
const card = "rounded-xl border border-line bg-panel";
const titulo = "text-[11px] font-medium uppercase tracking-wide text-faint";
const btn = "rounded-full border border-line px-3 py-1 text-fg hover:bg-raised disabled:opacity-40";
const btnPrimary = "rounded-full bg-fg px-3 py-1 font-medium text-black hover:bg-white disabled:opacity-40";
const campo = "rounded-lg border border-line bg-raised px-2 py-1 text-xs text-fg focus:border-[#555] focus:outline-none";

const CORES: Record<CompararItem["status"], string> = {
  pendente: "bg-faint",
  carregando: "bg-amber-400 animate-pulse",
  rodando: "bg-sky-400 animate-pulse",
  pronto: "bg-emerald-400",
  erro: "bg-red-400",
  cancelado: "bg-faint",
};

const ROTULOS: Record<CompararItem["status"], string> = {
  pendente: "na fila",
  carregando: "carregando…",
  rodando: "respondendo…",
  pronto: "pronto",
  erro: "erro",
  cancelado: "cancelado",
};

const MAX_MODELOS = 6; // mesmo teto do backend

// Recorte do /api/local (só o que esta tela usa). Tipar aqui em vez de importar LocalState mantém a
// aba independente da IA local, que não existe na versão web.
type IaLocal = { models: { path: string; name: string; kind: string }[]; server: { alias?: string } };

/** O status da mensagem no banco ("running", "pronto", "erro"…) no vocabulário do estado. */
const situacao = (s: string | null | undefined): CompararEstado["status"] =>
  s === "running" ? "rodando" : s === "erro" || s === "cancelado" ? s : "pronto";

const nomeDoArquivo = (p: string) => p.split(/[\\/]/).pop()?.replace(/\.gguf$/i, "") ?? p;

/** O nome só aparece quando o modo cego não está mais em jogo. */
const rotuloDe = (item: CompararItem, e: CompararEstado) =>
  e.cego && !e.revelado ? `Modelo ${item.rotulo}` : item.nome;

function medida(item: CompararItem): string {
  const s = item.stats;
  if (!s) return "";
  const partes = [`${s.tokens} tokens`, `${s.seconds}s`];
  if (s.tps) partes.splice(1, 0, `${s.tps} tok/s`);
  return partes.join(" · ") + (s.estimated ? " (estimado)" : "");
}

function comoMarkdown(prompt: string, e: CompararEstado): string {
  const linhas = ["# Comparação de modelos", "", "## Prompt", "", prompt, ""];
  for (const item of e.itens) {
    const venceu = item.id === e.voto ? " 🏆" : "";
    linhas.push(`## ${rotuloDe(item, e)}${venceu} (${ROTULOS[item.status]})`, "", `*${medida(item) || "—"}*`, "",
                item.content || item.error || "", "");
  }
  return linhas.join("\n");
}

/** Teste pronto de uma especialidade de Worker (backend: baterias.py). */
type Bateria = {
  titulo: string; mede: string; prompt: string; gabarito: string;
  anexo?: { nome: string; texto: string } | null;
};
const BATERIA_DE: Record<string, string> = { logica: "logica", frontend: "frontend", testes: "testes", docs: "docs" };

export default function CompararView(props: {
  conv: number | null;
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
  // "Testar" num bloco de código: HTML abre no navegador integrado, o resto roda no terminal.
  onAbrirNoNavegador: (url: string) => void;
  onRodarNoTerminal: (comando: string) => void;
}) {
  const [st, setSt] = useState<IaLocal | null>(null); // null também é "esta build não tem IA local"
  const [escolha, setEscolha] = useState({ provider: props.provider, model: props.model });
  const [itens, setItens] = useState<CompararEntrada[]>([]);
  const [modo, setModo] = useState<"paralelo" | "sequencial">("paralelo");
  const [cego, setCego] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [enviado, setEnviado] = useState(""); // o prompt da comparação na tela (o campo fica livre)
  const [promptAberto, setPromptAberto] = useState(false);
  const [estado, setEstado] = useState<CompararEstado | null>(null);
  const [perguntando, setPerguntando] = useState(false);
  const [placar, setPlacar] = useState<PlacarLinha[] | null>(null);
  const [copiado, setCopiado] = useState(false);
  // Teste pronto (vindo do "Testar" da doca Modelo · VRAM) e a análise do juiz.
  const [bateria, setBateria] = useState<{ id: string; b: Bateria } | null>(null);
  const [juiz, setJuiz] = useState({ provider: props.provider, model: props.model });
  // passos: o que o revisor está fazendo (carregar o modelo, abrir cada página, ler), como as
  // chamadas de ferramenta no chat — sem isso a análise parecia travada.
  const [analise, setAnalise] = useState<Analise | null>(null);
  const corteAnalise = useRef<AbortController | null>(null);
  const acompanhando = useRef(0); // message_id que já está sendo ouvido: não abrir dois SSE
  const corte = useRef<AbortController | null>(null); // aborta o stream da conversa anterior

  const rodando = estado?.status === "rodando";
  // Rolagem da página: acompanha a análise do revisor enquanto ela é escrita (a caixa dela fica lá embaixo).
  const { ref: rolagem, fim: fimDaPagina, onScroll: aoRolar, colar: colarPagina } = useStickyBottom<HTMLDivElement>([
    analise?.rodando ? analise.texto.length + analise.pensou.length + analise.passos.length : 0,
  ]);
  const temGguf = itens.some((i) => i.path);
  const ggufs = useMemo(() => (st?.models ?? []).filter((m) => m.kind === "chat"), [st]);

  useEffect(() => {
    api.get<IaLocal>("/local").then(setSt).catch(() => setSt(null));
  }, []);

  // "Testar" num tipo de Worker: prompt e arquivo do teste pronto, e o modelo atual dele já na lista.
  const onError = props.onError;
  useEffect(() => {
    let preset: { id: string; nome: string; spec?: { provider: string; model: string } } | null = null;
    try {
      preset = JSON.parse(localStorage.getItem("forja.comparar.preset") || "null");
      localStorage.removeItem("forja.comparar.preset");
    } catch {
      preset = null;
    }
    if (!preset) return;
    const id = BATERIA_DE[preset.id] ?? "geral";
    Promise.all([api.get<Record<string, Bateria>>("/comparar/baterias"), api.get<IaLocal>("/local").catch(() => null)])
      .then(([todas, local]) => {
        const b = todas[id];
        if (!b) return;
        setBateria({ id, b });
        setPrompt(b.prompt);
        const spec = preset!.spec;
        if (!spec?.model) return;
        // Modelo local escolhido pelo alias: na comparação ele entra como .gguf (carrega e descarrega).
        const gguf = spec.provider === "local"
          ? local?.models.find((m) => m.kind === "chat" && (m.name === spec.model || nomeDoArquivo(m.path) === spec.model))
          : undefined;
        setItens([gguf ? { path: gguf.path, nome: gguf.name } : { provider: spec.provider, model: spec.model, nome: spec.model }]);
      })
      .catch((e) => onError(e.message));
  }, [onError]);

  // Um .gguf por vez: o Forja sobe um llama-server só, então o paralelo deixa de ser opção.
  useEffect(() => {
    if (temGguf) setModo("sequencial");
  }, [temGguf]);

  /** Acompanha UMA comparação. Trocar de conversa aborta o stream anterior: sem isso, o evento da
   *  comparação em andamento pintava a tela da conversa recém-aberta — o mesmo bug que a aba
   *  Pesquisa já tinha corrigido. */
  const ouvir = useCallback(
    async (messageId: number) => {
      if (acompanhando.current === messageId) return;
      corte.current?.abort();
      const ctl = new AbortController();
      corte.current = ctl;
      acompanhando.current = messageId;
      try {
        await streamSSE(`/comparar/${messageId}/stream`, { signal: ctl.signal },
          (ev) => !ev.erro && !ctl.signal.aborted && setEstado(ev));
      } catch (e: any) {
        if (!ctl.signal.aborted) onError(e.message);
      } finally {
        if (acompanhando.current === messageId) acompanhando.current = 0;
      }
    },
    [onError],
  );

  useEffect(() => () => corte.current?.abort(), []);   // sair da aba encerra o stream

  /** A análise roda no servidor: aqui só se acompanha o retrato dela (e se reconecta ao voltar para
   *  a página — sair da tela não a interrompe mais). */
  const acompanharAnalise = useCallback(async (messageId: number, iniciar?: { provider: string; model: string }) => {
    corteAnalise.current?.abort();
    const ctl = new AbortController();
    corteAnalise.current = ctl;
    try {
      await streamSSE(`/comparar/${messageId}/julgar`,
        iniciar ? { method: "POST", body: JSON.stringify(iniciar), signal: ctl.signal } : { signal: ctl.signal },
        (ev) => {
          if (ctl.signal.aborted || ev.status === "nenhum") return;
          if (ev.status === "erro") onError(ev.erro);
          setAnalise({
            texto: ev.texto, pensou: ev.pensou, juiz: ev.juiz, rodando: ev.status === "rodando",
            passos: ev.status === "erro" ? [...ev.passos, `Erro: ${ev.erro}`] : ev.passos,
            parado: ev.status === "parado", stats: ev.stats ?? undefined,
          });
        });
    } catch (e: any) {
      if (!ctl.signal.aborted) onError(e.message);
    } finally {
      if (corteAnalise.current === ctl) corteAnalise.current = null;
    }
  }, [onError]);

  useEffect(() => () => corteAnalise.current?.abort(), []);  // sair da tela só para de ACOMPANHAR

  /** Reabre a última comparação da conversa (e volta a ouvir, se ainda estiver rodando). */
  const carregarConversa = useCallback(
    async (id: number | null = props.conv) => {
      if (id === null) {
        setEstado(null);
        return;
      }
      try {
        const c = await api.get<{ messages: Message[] }>(`/conversations/${id}`);
        const i = c.messages.map((m) => !!(m.meta as any)?.itens).lastIndexOf(true);
        if (i < 0) return setEstado(null);
        const m = c.messages[i];
        const meta = m.meta as any;
        setEnviado(c.messages[i - 1]?.content ?? "");
        const idBateria = (c.messages[i - 1]?.meta as any)?.bateria as string | undefined;
        if (idBateria) {
          api.get<Record<string, Bateria>>("/comparar/baterias")
            .then((t) => t[idBateria] && setBateria({ id: idBateria, b: t[idBateria] })).catch(() => {});
        } else setBateria(null);
        const julgado = c.messages.find((x) => (x.meta as any)?.julgamento?.de === m.id);
        setAnalise(julgado
          ? { texto: julgado.content, pensou: julgado.thinking ?? "", juiz: (julgado.meta as any).julgamento.juiz,
              rodando: false, passos: [], stats: (julgado.meta as any).stats }
          : null);
        acompanharAnalise(m.id);  // análise em andamento no servidor? volta a mostrar
        setEstado({
          message_id: m.id, status: situacao(m.status),
          modo: meta.modo || "paralelo", cego: !!meta.cego, revelado: !!meta.revelado,
          voto: meta.voto || "", itens: meta.itens,
        });
        if (situacao(m.status) === "rodando") ouvir(m.id);
      } catch (e: any) {
        onError(e.message);
      }
    },
    [props.conv, onError, ouvir, acompanharAnalise],
  );

  useEffect(() => {
    carregarConversa();
  }, [carregarConversa]);

  /** Escolher no seletor não carrega nada: modelo local vira o .gguf da comparação, que sobe na hora
   *  de rodar (e desce no fim). O "local" cru seria "o que estiver carregado", que não é o escolhido. */
  function entradaDe(e: { provider: string; model: string }): CompararEntrada {
    const gguf = e.provider === "local"
      ? ggufs.find((m) => m.name === e.model || nomeDoArquivo(m.path) === e.model)
      : undefined;
    return gguf ? { path: gguf.path, nome: gguf.name } : { provider: e.provider, model: e.model, nome: e.model };
  }

  function adicionar(entrada: CompararEntrada) {
    setItens((atual) => {
      if (atual.length >= MAX_MODELOS) return atual;
      if (atual.some((i) => i.nome === entrada.nome && i.provider === entrada.provider)) return atual;
      return [...atual, entrada];
    });
  }

  async function rodar(confirm = false) {
    if (!prompt.trim() || itens.length < 2) return;
    const texto = prompt;
    try {
      const id = await props.ensureConversation();
      setPerguntando(false);
      setEstado(null);
      setAnalise(null);
      setEnviado(texto);
      setPrompt(""); // o prompt vai para o cartão no topo; o campo fica livre para o próximo
      await streamSSE(`/comparar/${id}/rodar`,
        { method: "POST", body: JSON.stringify({ prompt: texto, itens, modo, cego, confirm, bateria: bateria?.id ?? "" }) },
        (ev) => (ev.erro ? onError(ev.erro) : setEstado(ev)));
      props.onConversationChanged();
      carregarConversa(id);
    } catch (e: any) {
      setPrompt(texto); // não perde o que foi escrito se nem chegou a rodar
      if (e.status === 409) setPerguntando(true); // tem modelo na VRAM: a conta é do usuário
      else onError(e.message);
    }
  }

  async function parar() {
    if (estado) await api.post(`/comparar/${estado.message_id}/cancelar`, {}).catch(() => {});
  }

  /** Gera de novo só a resposta deste modelo (alucinou, entrou em laço, deu erro). */
  async function refazer(item: CompararItem) {
    if (!estado) return;
    try {
      await api.post(`/comparar/${estado.message_id}/refazer`, { item: item.id });
      void ouvir(estado.message_id); // comparação já encerrada: volta a acompanhar
    } catch (e: any) {
      onError(e.message);
    }
  }

  /** Mais um modelo nesta comparação: só ele gera, as respostas dos outros ficam. */
  async function adicionarNaComparacao(entrada: CompararEntrada) {
    if (!estado) return;
    try {
      await api.post(`/comparar/${estado.message_id}/adicionar`,
        { provider: entrada.provider ?? "", model: entrada.model ?? "", path: entrada.path ?? "" });
      void ouvir(estado.message_id);
    } catch (e: any) {
      onError(e.message);
    }
  }

  /** Tira o modelo e a resposta dele — para analisar de novo sem ele. */
  async function remover(item: CompararItem) {
    if (!estado) return;
    try {
      await api.post(`/comparar/${estado.message_id}/remover`, { item: item.id });
      setEstado((e) => e && { ...e, itens: e.itens.filter((i) => i.id !== item.id), voto: e.voto === item.id ? "" : e.voto });
    } catch (e: any) {
      onError(e.message);
    }
  }

  async function votar(item: CompararItem) {
    if (!estado) return;
    try {
      const m = await api.post<Message>(`/comparar/${estado.message_id}/voto`, { voto: item.id });
      const meta = m.meta as any;
      setEstado((e) => e && { ...e, voto: meta.voto || "", revelado: !!meta.revelado });
    } catch (e: any) {
      onError(e.message);
    }
  }

  /** Um modelo lê todas as respostas e estatísticas e devolve a tabela comparativa, em forma de chat.
   *  No teste de frontend, com juiz que enxerga, ele recebe também os prints de cada página. */
  // Revisão automática: quando a comparação acompanhada termina (inclusive depois de Refazer ou
  // Adicionar), o revisor começa sozinho. Preferência do usuário, fica neste navegador.
  const [autoAnalise, setAutoAnalise] = useState(() => {
    try {
      return localStorage.getItem("forja.comparar.autoAnalise") === "1";
    } catch {
      return false;
    }
  });
  const statusAnterior = useRef<{ id: number; status: string } | null>(null);
  useEffect(() => {
    if (!estado) return;
    const antes = statusAnterior.current;
    statusAnterior.current = { id: estado.message_id, status: estado.status };
    if (autoAnalise && antes?.id === estado.message_id && antes.status === "rodando" && estado.status === "pronto")
      void analisar();
    // só a transição rodando → pronto dispara; analisar muda a cada render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estado?.status, estado?.message_id]);

  function trocarAutoAnalise(v: boolean) {
    setAutoAnalise(v);
    try {
      localStorage.setItem("forja.comparar.autoAnalise", v ? "1" : "0");
    } catch {
      /* sem armazenamento: vale só nesta sessão */
    }
  }

  async function analisar() {
    if (!estado || !juiz.model) return;
    colarPagina();  // a página desce e acompanha a análise (rolar para cima solta, como no chat)
    setAnalise({ texto: "", pensou: "", juiz: juiz.model, rodando: true, passos: ["Preparando a análise"] });
    acompanharAnalise(estado.message_id, juiz);
  }

  /** Parar de verdade: o servidor cancela e o modelo para de gerar (a carga de um modelo que já
   *  começou termina sozinha — o llama.cpp não interrompe no meio). */
  async function pararAnalise() {
    if (estado) await api.post(`/comparar/${estado.message_id}/julgar/parar`, {}).catch((e) => onError(e.message));
  }

  /** "Testar" num bloco de código de uma resposta: cada modelo tem a própria pasta de teste. */
  async function testar(item: CompararItem, codigo: string, linguagem: string) {
    if (!estado) return;
    try {
      const r = await api.post<{ tipo: string; url?: string; comando?: string }>("/comparar/testar",
        { codigo, linguagem, chave: `${estado.message_id}-${item.rotulo}`, conv: props.conv, bateria: bateria?.id ?? "" });
      if (r.tipo === "web" && r.url) props.onAbrirNoNavegador(r.url);
      else if (r.comando) props.onRodarNoTerminal(r.comando);
    } catch (e: any) {
      onError(e.message);
    }
  }

  function copiar() {
    if (!estado) return;
    navigator.clipboard.writeText(comoMarkdown(enviado, estado));
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1500);
  }

  function baixar() {
    if (!estado) return;
    const url = URL.createObjectURL(new Blob([comoMarkdown(enviado, estado)], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `comparacao-${estado.message_id}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function verPlacar() {
    if (placar) return setPlacar(null);
    try {
      setPlacar((await api.get<{ linhas: PlacarLinha[] }>("/comparar/placar")).linhas);
    } catch (e: any) {
      onError(e.message);
    }
  }

  const colunas = { gridTemplateColumns: `repeat(${Math.max(estado?.itens.length ?? 1, 1)}, minmax(0, 1fr))` };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={rolagem} onScroll={aoRolar} className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <div className="flex w-full flex-col gap-3">
          {!estado && !bateria && (
            <div className={`${card} px-4 py-3 text-xs text-muted`}>
              <p className="text-sm text-fg">Mesmo prompt, vários modelos, lado a lado.</p>
              <p className="mt-1">
                No modo paralelo todos respondem ao mesmo tempo. No sequencial um de cada vez — é o único que
                aceita arquivos .gguf, porque o Forja sobe um llama-server por vez: carrega, responde,
                descarrega e passa para o próximo. Para testes prontos por especialidade, use "Testar" na aba
                Modelo · VRAM da Maestro.
              </p>
            </div>
          )}

          {bateria && (
            <div className={`${card} px-4 py-3 text-xs`}>
              <div className={titulo}>Teste pronto</div>
              <p className="mt-1 text-sm text-fg">{bateria.b.titulo}</p>
              <p className="mt-0.5 text-muted">Mede {bateria.b.mede}.</p>
              {!estado && (
                <p className="mt-1 text-faint">
                  Adicione os modelos (os .gguf rodam um de cada vez) e envie. No fim, "Analisar com IA" monta a tabela:
                  quem acertou, quem alucinou, quem foi mais rápido.
                  {bateria.id === "frontend" && " Com um juiz que enxerga, ele abre cada página e julga também o visual."}
                </p>
              )}
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                {bateria.b.anexo && (
                  <details className="min-w-0 flex-1">
                    <summary className="cursor-pointer text-muted hover:text-fg">
                      Arquivo fornecido pelo Forja: <span className="font-mono">{bateria.b.anexo.nome}</span>
                    </summary>
                    <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg p-2 text-[11px] text-muted">
                      {bateria.b.anexo.texto}
                    </pre>
                  </details>
                )}
                <details className="min-w-0 flex-1">
                  <summary className="cursor-pointer text-muted hover:text-fg">Gabarito (o juiz também recebe)</summary>
                  <p className="mt-1 whitespace-pre-wrap rounded-lg border border-line bg-bg p-2 text-muted">{bateria.b.gabarito}</p>
                </details>
              </div>
            </div>
          )}

          {estado && enviado && (
            <div className={`${card} px-4 py-3`}>
              <div className="flex items-center gap-2">
                <span className={titulo}>Prompt</span>
                <span className="text-[11px] text-faint">
                  {estado.itens.length} modelos · {estado.modo === "sequencial" ? "um de cada vez" : "ao mesmo tempo"}
                  {estado.cego ? " · modo cego" : ""}
                </span>
                <button className="ml-auto text-[11px] text-faint hover:text-fg" onClick={() => setPrompt(enviado)}
                        title="Copiar este prompt para o campo, para rodar de novo ou ajustar">
                  Reusar
                </button>
                {enviado.length > 280 && (
                  <button className="text-[11px] text-faint hover:text-fg" onClick={() => setPromptAberto((v) => !v)}>
                    {promptAberto ? "Recolher" : "Ver tudo"}
                  </button>
                )}
              </div>
              <p className={`mt-1 whitespace-pre-wrap text-sm text-fg ${promptAberto ? "" : "line-clamp-3"}`}>{enviado}</p>
            </div>
          )}

          {placar && (
            <div className={`${card} px-4 py-3`}>
              <div className="mb-2 flex items-center justify-between">
                <span className={titulo}>Placar</span>
                <button className="text-faint hover:text-fg" onClick={() => setPlacar(null)}>
                  <X className="size-4" />
                </button>
              </div>
              {!placar.length && <p className="text-xs text-faint">Nenhuma comparação votada ainda.</p>}
              {placar.map((l) => (
                <div key={l.nome} className="flex items-center gap-3 border-t border-line py-1.5 text-xs first:border-0">
                  <span className="flex-1 truncate text-fg">{l.nome}</span>
                  <span className="text-muted">{l.vitorias} 🏆</span>
                  <span className="text-faint">{l.rodadas} rodadas</span>
                  {l.tps && <span className="text-faint">{l.tps} tok/s</span>}
                  {!!l.erros && <span className="text-red-300">{l.erros} erros</span>}
                </div>
              ))}
            </div>
          )}

          {estado && (
            <div className="grid gap-3" style={colunas}>
              {estado.itens.map((item) => (
                <ColunaResposta key={item.id} item={item} estado={estado} rodando={rodando}
                                onVotar={() => votar(item)} onRefazer={() => refazer(item)}
                                onRemover={estado.itens.length > 2 ? () => remover(item) : undefined}
                                onTestar={(c, l) => testar(item, c, l)} />
              ))}
            </div>
          )}

          {estado && estado.itens.length < MAX_MODELOS && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-faint">
              <span>Adicionar a esta comparação (só ele gera):</span>
              <ModelPicker provider={escolha.provider} model={escolha.model} loadLocal={false}
                           onChange={(provider, model) => setEscolha({ provider, model })} />
              <button className={btn} disabled={!escolha.model}
                      onClick={() => adicionarNaComparacao(entradaDe(escolha))}>
                Adicionar
              </button>
              {estado.modo === "sequencial" && !!ggufs.length && (
                <select className={campo} value=""
                        onChange={(e) => e.target.value && adicionarNaComparacao({ path: e.target.value, nome: nomeDoArquivo(e.target.value) })}>
                  <option value="">Adicionar arquivo .gguf…</option>
                  {ggufs.filter((m) => !estado.itens.some((i) => i.path === m.path))
                    .map((m) => <option key={m.path} value={m.path}>{m.name}</option>)}
                </select>
              )}
            </div>
          )}

          {estado && !rodando && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <button className={btn} onClick={copiar}>
                {copiado ? <Check className="mr-1 inline size-3.5" /> : <Copy className="mr-1 inline size-3.5" />}
                Copiar Markdown
              </button>
              <button className={btn} onClick={baixar}>Baixar .md</button>
              <button className={btn} onClick={verPlacar}>
                <Gauge className="mr-1 inline size-3.5" />
                Placar
              </button>
              <span className="text-faint">Passe o mouse num bloco de código e use "▶ Testar" para vê-lo rodando.</span>
            </div>
          )}

          {estado && (
            <div className={`${card} flex flex-col gap-3 px-4 py-3 text-xs`}>
              <div className="flex flex-wrap items-center gap-2">
                <Balanca className="size-4 text-faint" />
                <span className={titulo}>Analisar com IA</span>
                <span className="text-faint">um modelo que você confia lê as respostas e as estatísticas e compara</span>
                <div className="ml-auto flex items-center gap-1.5">
                  <label className="mr-1 flex items-center gap-1.5 text-muted"
                         title="Quando todas as respostas forem entregues, o revisor começa sozinho">
                    <input type="checkbox" checked={autoAnalise} onChange={(e) => trocarAutoAnalise(e.target.checked)} />
                    Analisar ao terminar
                  </label>
                  <ModelPicker provider={juiz.provider} model={juiz.model} loadLocal={false}
                               onChange={(provider, model) => setJuiz({ provider, model })} />
                  {analise?.rodando ? (
                    <button className={btn} onClick={pararAnalise} title="Parar a análise">
                      <Square className="mr-1 inline size-3" />
                      Parar
                    </button>
                  ) : (
                    <button className={btnPrimary} disabled={!juiz.model || rodando} onClick={analisar}
                            title={rodando ? "Espere as respostas terminarem" : undefined}>
                      {analise ? "Analisar de novo" : "Analisar"}
                    </button>
                  )}
                </div>
              </div>
              {analise && <CaixaAnalise analise={analise} />}
            </div>
          )}
          <div ref={fimDaPagina} />
        </div>
      </div>

      <div className="mx-auto w-full max-w-3xl shrink-0 px-5 pb-4">
        {perguntando && (
          <div className="mb-2 rounded-xl border border-amber-800/70 bg-amber-950/30 p-2.5 text-xs text-amber-200">
            <p className="font-medium">O modelo {st?.server.alias} está carregado na VRAM.</p>
            <p className="mt-1 text-amber-200/80">
              A comparação vai carregar os .gguf escolhidos no lugar dele, um de cada vez, e no fim deixa a
              VRAM livre. O cache de contexto do chat se perde: a próxima mensagem de lá reprocessa o histórico.
            </p>
            <div className="mt-2 flex gap-2">
              <button className={btnPrimary} onClick={() => rodar(true)}>Descarregar e comparar</button>
              <button className={btn} onClick={() => setPerguntando(false)}>Cancelar</button>
            </div>
          </div>
        )}

        <CaixaPrompt>
          {/* Os modelos da comparação, como os anexos do chat: em cima do campo. */}
          <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
            {itens.map((i) => (
              <span key={`${i.provider}:${i.nome}`}
                    className="flex items-center gap-1 rounded-full border border-line bg-raised px-2 py-0.5 text-xs text-fg">
                {i.path && <Cube className="size-3.5 text-faint" />}
                {i.nome}
                <button className="text-faint hover:text-fg" title="Tirar da comparação"
                        onClick={() => setItens((a) => a.filter((x) => x !== i))}>
                  <X className="size-3" />
                </button>
              </span>
            ))}
            {!itens.length && <span className="text-xs text-faint">Escolha de 2 a {MAX_MODELOS} modelos no seletor abaixo.</span>}
            {!!itens.length && (
              <button className="text-faint hover:text-fg" title="Limpar" onClick={() => setItens([])}>
                <Trash className="size-3.5" />
              </button>
            )}
          </div>
          <textarea
            rows={bateria && !estado ? 5 : 2}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!rodando) rodar();
              }
            }}
            placeholder={estado ? "Novo prompt para comparar…" : "O prompt que todos os modelos vão responder…"}
            className={campoPrompt}
          />
          <RodapePrompt>
            {temGguf ? (
              <span className={pilula} title=".gguf roda sempre em sequencial: um llama-server por vez">
                <Split className="size-3.5" />
                Sequencial
              </span>
            ) : (
              <Menu
                title="Modo"
                items={[
                  { id: "paralelo" as const, label: "Paralelo", hint: "Todos ao mesmo tempo" },
                  { id: "sequencial" as const, label: "Sequencial", hint: "Um de cada vez" },
                ]}
                value={modo}
                onChange={setModo}
                button={(label) => (
                  <>
                    <Split className="size-3.5" />
                    {label}
                  </>
                )}
              />
            )}
            <button className={`${pilula} ${cego ? pilulaLigada : ""}`} aria-pressed={cego} onClick={() => setCego((v) => !v)}
                    title="Modo cego: as respostas aparecem sem o nome do modelo até você votar">
              {cego ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
              Modo cego
            </button>
            {!!ggufs.length && (
              <select className={`${pilula} w-24 bg-transparent`} value="" disabled={itens.length >= MAX_MODELOS}
                      title="Adicionar um arquivo .gguf à comparação"
                      onChange={(e) => e.target.value && adicionar({ path: e.target.value, nome: nomeDoArquivo(e.target.value) })}>
                <option value="">+ .gguf</option>
                {ggufs.map((m) => <option key={m.path} value={m.path}>{m.name}</option>)}
              </select>
            )}
            {/* À direita, como no chat: o seletor de modelo (aqui, o que entra na comparação) e o enviar. */}
            <DireitaPrompt>
            <ModelPicker provider={escolha.provider} model={escolha.model} loadLocal={false}
                         onChange={(provider, model) => setEscolha({ provider, model })} />
            <button className={pilula} disabled={!escolha.model || itens.length >= MAX_MODELOS}
                    title="Pôr o modelo escolhido na comparação" onClick={() => adicionar(entradaDe(escolha))}>
              <Plus className="size-3.5" />
              Adicionar
            </button>
            <BotaoEnviar rodando={rodando} onParar={parar} onEnviar={() => rodar()} titulo="Comparar"
                         desabilitado={itens.length < 2 || !prompt.trim()} />
            </DireitaPrompt>
          </RodapePrompt>
        </CaixaPrompt>
      </div>
    </div>
  );
}


/** Raciocínio que veio embutido no texto (<think>…</think>), ainda aberto durante a geração. */
function separarPensamento(texto: string): { pensou: string; resposta: string } {
  const m = /^\s*<think>([\s\S]*?)(?:<\/think>|$)/.exec(texto);
  if (!m) return { pensou: "", resposta: texto };
  return { pensou: m[1], resposta: texto.slice(m[0].length) };
}

/** Uma resposta da comparação: acompanha a geração como o chat (rolar para cima solta, voltar ao fim
 *  cola) e mostra o raciocínio na mesma caixa recolhível das outras telas. */
function ColunaResposta(props: {
  item: CompararItem;
  estado: CompararEstado;
  rodando: boolean;
  onVotar: () => void;
  onRefazer: () => void;
  onRemover?: () => void;
  onTestar: (codigo: string, linguagem: string) => void;
}) {
  const { item, estado } = props;
  const embutido = separarPensamento(item.content || "");
  const pensou = item.reasoning || embutido.pensou;
  const gerando = item.status === "rodando";
  const { ref, fim, onScroll } = useStickyBottom<HTMLDivElement>([item.content, item.reasoning, item.status]);
  return (
    <div className={`${card} flex min-w-0 flex-col overflow-hidden ${estado.voto === item.id ? "border-amber-700/70" : ""}`}>
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className={`size-2 shrink-0 rounded-full ${CORES[item.status]}`} title={ROTULOS[item.status]} />
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg" title={rotuloDe(item, estado)}>
          {rotuloDe(item, estado)}
        </span>
        {item.status !== "pronto" && <span className="text-[11px] text-faint">{ROTULOS[item.status]}</span>}
        {["rodando", "pronto", "erro", "cancelado"].includes(item.status) && (
          <button
            title="Gerar esta resposta de novo (alucinou, entrou em laço, deu erro)"
            onClick={props.onRefazer}
            className="flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-[11px] text-faint hover:bg-raised hover:text-fg"
          >
            <Refresh className="size-3" /> Refazer
          </button>
        )}
        {props.onRemover && !["rodando", "carregando"].includes(item.status) && (
          <button title="Tirar este modelo da comparação (a resposta dele é descartada)" onClick={props.onRemover}
                  className="rounded-full p-1 text-faint hover:bg-raised hover:text-fg">
            <X className="size-3" />
          </button>
        )}
        {!props.rodando && item.status === "pronto" && (
          <button
            title={estado.voto === item.id ? "Desfazer voto" : "Marcar como melhor resposta"}
            onClick={props.onVotar}
            className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] ${
              estado.voto === item.id
                ? "border-amber-700/70 bg-amber-950/40 text-amber-300"
                : "border-line text-faint hover:bg-raised hover:text-fg"
            }`}
          >
            {estado.voto === item.id ? "Vencedor" : "Votar"} 🏆
          </button>
        )}
      </div>
      <div ref={ref} onScroll={onScroll} className="max-h-[62vh] min-h-24 min-w-0 flex-1 overflow-y-auto px-3 py-2 text-sm">
        {item.error && <p className="text-xs text-red-300">{item.error}</p>}
        <Thinking text={pensou} live={gerando && !embutido.resposta.trim()} />
        <TestarCodigo.Provider value={item.status === "pronto" ? props.onTestar : null}>
          <Markdown text={embutido.resposta} />
        </TestarCodigo.Provider>
        <div ref={fim} />
      </div>
      {item.stats && <LinhaStats stats={item.stats} vivo={gerando} />}
    </div>
  );
}


type Analise = {
  texto: string; pensou: string; juiz: string; rodando: boolean; passos: string[]; parado?: boolean;
  stats?: Medida;
};

type Medida = { tokens: number; seconds: number; tps?: number | null; estimated?: boolean };

/** tok/s, tempo e tokens — com "~" enquanto é estimativa de quem ainda está gerando. */
function LinhaStats({ stats, vivo, className = "px-3" }: { stats: Medida; vivo?: boolean; className?: string }) {
  const est = stats.estimated ? "~" : "";
  return (
    <div className={`flex flex-wrap items-center gap-x-3 border-t border-line py-1.5 text-[11px] text-faint ${className}`}>
      {vivo && <span className="size-1.5 animate-pulse rounded-full bg-sky-400" title="gerando: números estimados" />}
      {stats.tps != null && <span className="text-muted">{est}{stats.tps} tok/s</span>}
      <span>{stats.seconds}s</span>
      <span>{est}{stats.tokens} tokens</span>
    </div>
  );
}

/** A resposta do revisor: passos visíveis (como ferramentas no chat), raciocínio recolhível, o texto
 *  acompanhado enquanto é gerado (rolar para cima solta, voltar ao fim cola) e as estatísticas. */
function CaixaAnalise({ analise }: { analise: Analise }) {
  const { ref, fim, onScroll } = useStickyBottom<HTMLDivElement>([analise.texto, analise.pensou, analise.passos.length]);
  const passos = (
    <ol className="space-y-0.5 text-xs">
      {analise.passos.map((p, i) => {
        const atual = analise.rodando && i === analise.passos.length - 1;
        return (
          <li key={i} className={`flex items-center gap-2 ${atual ? "text-sky-300" : "text-faint"}`}>
            <span className={`size-1.5 shrink-0 rounded-full ${
              atual ? "animate-pulse bg-sky-400" : p.startsWith("Erro") ? "bg-red-400" : "bg-emerald-500"}`} />
            {p}
          </li>
        );
      })}
      {analise.parado && <li className="text-amber-300">Análise interrompida.</li>}
    </ol>
  );
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-line bg-bg text-sm">
      <div ref={ref} onScroll={onScroll} className="max-h-[70vh] overflow-y-auto px-4 py-3">
        <p className="mb-2 text-[11px] text-faint">Análise de {analise.juiz}</p>
        {!!analise.passos.length && (analise.rodando || !analise.texto ? (
          <div className="mb-2">{passos}</div>
        ) : (
          <details className="mb-2">
            <summary className="cursor-pointer text-xs text-faint hover:text-fg">{analise.passos.length} passos</summary>
            <div className="mt-1">{passos}</div>
          </details>
        ))}
        <Thinking text={analise.pensou} live={analise.rodando && !analise.texto} />
        {analise.texto && <Markdown text={analise.texto} />}
        <div ref={fim} />
      </div>
      {analise.stats && <LinhaStats stats={analise.stats} vivo={analise.rodando} className="px-4" />}
    </div>
  );
}

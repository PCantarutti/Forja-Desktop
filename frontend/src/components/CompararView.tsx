import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import type { CompararEntrada, CompararEstado, CompararItem, Message, PlacarLinha } from "../types";
import { ArrowUp, Check, Copy, Cube, Gauge, Square, Trash, X } from "./icons";
import { Markdown } from "./MessageView";
import ModelPicker from "./ModelPicker";

// Estas classes moram no LocalPanel.tsx, que é só do desktop. Repetidas aqui para esta aba viajar
// inteira num cherry-pick para o forja-web. ponytail: 4 linhas custam menos que um módulo de estilo.
const card = "rounded-2xl border border-line bg-surface p-3.5";
const btn = "rounded-full border border-line px-3 py-1 text-fg hover:bg-raised disabled:opacity-40";
const btnPrimary = "rounded-full bg-fg px-3 py-1 font-medium text-black hover:bg-white disabled:opacity-40";
const campo = "rounded-lg border border-line bg-raised px-2 py-1 text-xs text-fg focus:border-[#555] focus:outline-none";

const CORES: Record<CompararItem["status"], string> = {
  pendente: "text-faint",
  carregando: "text-amber-300",
  rodando: "text-sky-300",
  pronto: "text-muted",
  erro: "text-red-300",
  cancelado: "text-faint",
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

export default function CompararView(props: {
  conv: number | null;
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
}) {
  const [st, setSt] = useState<IaLocal | null>(null); // null também é "esta build não tem IA local"
  const [escolha, setEscolha] = useState({ provider: props.provider, model: props.model });
  const [itens, setItens] = useState<CompararEntrada[]>([]);
  const [modo, setModo] = useState<"paralelo" | "sequencial">("paralelo");
  const [cego, setCego] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [estado, setEstado] = useState<CompararEstado | null>(null);
  const [perguntando, setPerguntando] = useState(false);
  const [placar, setPlacar] = useState<PlacarLinha[] | null>(null);
  const [copiado, setCopiado] = useState(false);
  const acompanhando = useRef(0); // message_id que já está sendo ouvido: não abrir dois SSE

  const rodando = estado?.status === "rodando";
  const temGguf = itens.some((i) => i.path);
  const ggufs = useMemo(() => (st?.models ?? []).filter((m) => m.kind === "chat"), [st]);

  useEffect(() => {
    api.get<IaLocal>("/local").then(setSt).catch(() => setSt(null));
  }, []);

  // Um .gguf por vez: o Forja sobe um llama-server só, então o paralelo deixa de ser opção.
  useEffect(() => {
    if (temGguf) setModo("sequencial");
  }, [temGguf]);

  const ouvir = useCallback(
    async (messageId: number) => {
      if (acompanhando.current === messageId) return;
      acompanhando.current = messageId;
      try {
        await streamSSE(`/comparar/${messageId}/stream`, {}, (ev) => !ev.erro && setEstado(ev));
      } catch (e: any) {
        props.onError(e.message);
      } finally {
        acompanhando.current = 0;
      }
    },
    [props.onError],
  );

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
        setPrompt(c.messages[i - 1]?.content ?? "");
        setEstado({
          message_id: m.id, status: situacao(m.status),
          modo: meta.modo || "paralelo", cego: !!meta.cego, revelado: !!meta.revelado,
          voto: meta.voto || "", itens: meta.itens,
        });
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

  function adicionar(entrada: CompararEntrada) {
    setItens((atual) => {
      if (atual.length >= MAX_MODELOS) return atual;
      if (atual.some((i) => i.nome === entrada.nome && i.provider === entrada.provider)) return atual;
      return [...atual, entrada];
    });
  }

  async function rodar(confirm = false) {
    if (!prompt.trim() || itens.length < 2) return;
    try {
      const id = await props.ensureConversation();
      setPerguntando(false);
      setEstado(null);
      await streamSSE(`/comparar/${id}/rodar`,
        { method: "POST", body: JSON.stringify({ prompt, itens, modo, cego, confirm }) },
        (ev) => (ev.erro ? props.onError(ev.erro) : setEstado(ev)));
      props.onConversationChanged();
      carregarConversa(id);
    } catch (e: any) {
      if (e.status === 409) setPerguntando(true); // tem modelo na VRAM: a conta é do usuário
      else props.onError(e.message);
    }
  }

  async function parar() {
    if (estado) await api.post(`/comparar/${estado.message_id}/cancelar`, {}).catch(() => {});
  }

  async function votar(item: CompararItem) {
    if (!estado) return;
    try {
      const m = await api.post<Message>(`/comparar/${estado.message_id}/voto`, { voto: item.id });
      const meta = m.meta as any;
      setEstado((e) => e && { ...e, voto: meta.voto || "", revelado: !!meta.revelado });
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  function copiar() {
    if (!estado) return;
    navigator.clipboard.writeText(comoMarkdown(prompt, estado));
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1500);
  }

  function baixar() {
    if (!estado) return;
    const url = URL.createObjectURL(new Blob([comoMarkdown(prompt, estado)], { type: "text/markdown" }));
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
      props.onError(e.message);
    }
  }

  const colunas = { gridTemplateColumns: `repeat(${Math.max(estado?.itens.length ?? 1, 1)}, minmax(0, 1fr))` };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-3">
          {!estado && (
            <div className={`${card} text-xs text-muted`}>
              <p className="text-sm text-fg">Mesmo prompt, vários modelos, lado a lado.</p>
              <p className="mt-1">
                No modo paralelo todos respondem ao mesmo tempo. No sequencial um de cada vez — é o único que
                aceita arquivos .gguf, porque o Forja sobe um llama-server por vez: carrega, responde,
                descarrega e passa para o próximo.
              </p>
            </div>
          )}

          {placar && (
            <div className={card}>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm text-fg">Placar</span>
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
                <div key={item.id} className="flex min-w-0 flex-col rounded-2xl border border-line bg-surface">
                  <div className="flex items-center gap-2 border-b border-line px-3 py-2">
                    <span className="min-w-0 flex-1 truncate text-xs text-fg" title={rotuloDe(item, estado)}>
                      {rotuloDe(item, estado)}
                    </span>
                    <span className={`text-[11px] ${CORES[item.status]}`}>{ROTULOS[item.status]}</span>
                    {!rodando && (
                      <button
                        title={estado.voto === item.id ? "Desfazer voto" : "Votar neste"}
                        onClick={() => votar(item)}
                        className={`rounded-full px-1.5 ${estado.voto === item.id ? "text-amber-300" : "text-faint hover:text-fg"}`}
                      >
                        🏆
                      </button>
                    )}
                  </div>
                  <div className="max-h-[52vh] min-w-0 overflow-y-auto px-3 py-2 text-sm">
                    {item.error && <p className="text-xs text-red-300">{item.error}</p>}
                    <Markdown text={item.content} />
                  </div>
                  {medida(item) && (
                    <div className="border-t border-line px-3 py-1.5 text-[11px] text-faint">{medida(item)}</div>
                  )}
                </div>
              ))}
            </div>
          )}

          {estado && !rodando && (
            <div className="flex items-center gap-2 text-xs">
              <button className={btn} onClick={copiar}>
                {copiado ? <Check className="mr-1 inline size-3.5" /> : <Copy className="mr-1 inline size-3.5" />}
                Copiar Markdown
              </button>
              <button className={btn} onClick={baixar}>Baixar .md</button>
              <button className={btn} onClick={verPlacar}>
                <Gauge className="mr-1 inline size-3.5" />
                Placar
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0 px-5 pb-4">
        <div className="mx-auto max-w-[1400px]">
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

          <div className={`${card} flex flex-col gap-2.5`}>
            <div className="flex flex-wrap items-center gap-1.5">
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
              {!itens.length && <span className="text-xs text-faint">Escolha de 2 a {MAX_MODELOS} modelos.</span>}
              {!!itens.length && (
                <button className="text-faint hover:text-fg" title="Limpar" onClick={() => setItens([])}>
                  <Trash className="size-3.5" />
                </button>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2 text-xs">
              <div className="flex items-center gap-1">
                <ModelPicker provider={escolha.provider} model={escolha.model}
                             onChange={(provider, model) => setEscolha({ provider, model })} />
                <button className={btn} disabled={!escolha.model || itens.length >= MAX_MODELOS}
                        onClick={() => adicionar({ provider: escolha.provider, model: escolha.model, nome: escolha.model })}>
                  Adicionar
                </button>
              </div>

              {!!ggufs.length && (
                <select className={campo} value="" disabled={itens.length >= MAX_MODELOS}
                        onChange={(e) => e.target.value && adicionar({ path: e.target.value, nome: nomeDoArquivo(e.target.value) })}>
                  <option value="">Adicionar arquivo .gguf…</option>
                  {ggufs.map((m) => <option key={m.path} value={m.path}>{m.name}</option>)}
                </select>
              )}

              <div className="flex rounded-full border border-line p-0.5" role="radiogroup" aria-label="Modo">
                {(["paralelo", "sequencial"] as const).map((m) => (
                  <button key={m} role="radio" aria-checked={modo === m} disabled={temGguf && m === "paralelo"}
                          title={m === "paralelo" ? "Todos ao mesmo tempo" : "Um de cada vez (carrega e descarrega os .gguf)"}
                          onClick={() => setModo(m)}
                          className={`rounded-full px-2.5 py-0.5 ${modo === m ? "bg-raised text-fg" : "text-faint hover:text-fg disabled:opacity-40"}`}>
                    {m === "paralelo" ? "Paralelo" : "Sequencial"}
                  </button>
                ))}
              </div>

              <label className="flex items-center gap-1.5 text-muted">
                <input type="checkbox" checked={cego} onChange={(e) => setCego(e.target.checked)} />
                Modo cego
              </label>

              {temGguf && <span className="text-faint">.gguf roda sempre em sequencial: um llama-server por vez.</span>}
            </div>

            <div className="flex items-end gap-2">
              <textarea
                rows={2}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (!rodando) rodar();
                  }
                }}
                placeholder="O prompt que todos os modelos vão responder…"
                className="flex-1 resize-none rounded-xl border border-line bg-raised px-3 py-2 text-sm text-fg focus:border-[#555] focus:outline-none"
              />
              {rodando ? (
                <button className={btnPrimary} onClick={parar} title="Parar">
                  <Square className="size-3.5" />
                </button>
              ) : (
                <button className={btnPrimary} disabled={itens.length < 2 || !prompt.trim()}
                        onClick={() => rodar()} title="Comparar">
                  <ArrowUp className="size-4" />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

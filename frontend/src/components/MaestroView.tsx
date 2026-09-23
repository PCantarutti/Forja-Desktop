/** Cockpit do Maestro: a árvore de tarefas, a Maestro e o Worker lado a lado, com uma doca embaixo.
 *
 * Por que uma tela própria e não o chat com um painel a mais: numa execução autônoma longa as três
 * coisas acontecem ao mesmo tempo — a Maestro decide, um Worker implementa e o navegador valida — e
 * num layout de conversa duas delas ficam sempre fora de vista.
 *
 * Nada aqui mantém estado de execução: as mensagens, o rascunho e os passos do Worker vêm do App,
 * que já é dono do stream SSE. O que é nosso é a árvore, que vem do banco por /maestro/{id}/board —
 * ela sobrevive a recarregar a página, trocar de modelo e fechar o app, porque as tarefas nunca
 * viveram no contexto do modelo.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import { useStickyBottom } from "../useStickyBottom";
import type {
  Approval, Draft, MaestroBoard, MaestroModels, MaestroTask, Message, ModelPhase, Stats, SubState, TaskAttempt,
  TaskStatus,
} from "../types";
import ContextRing from "./ContextRing";
import { Check, Cube, Split, X } from "./icons";
import { aggregate, type TurnStats } from "./MessageView";
import ModelPicker from "./ModelPicker";
import { TABS as ABAS_DIREITA, type RightTab } from "./RightPanel";

const POLL_MS = 2000;

/** Cada estado com seu símbolo e sua cor. A árvore é lida de relance, não estudada. */
const ESTADO: Record<TaskStatus, { marca: string; cor: string; label: string }> = {
  pending: { marca: "○", cor: "text-faint", label: "na fila" },
  queued: { marca: "◔", cor: "text-muted", label: "aguardando" },
  loading_model: { marca: "◑", cor: "text-amber-400", label: "carregando modelo" },
  implementing: { marca: "⟳", cor: "text-sky-400", label: "implementando" },
  testing: { marca: "⟳", cor: "text-sky-400", label: "testando" },
  reviewing: { marca: "◆", cor: "text-violet-400", label: "esperando revisão" },
  completed: { marca: "✓", cor: "text-emerald-400", label: "concluída" },
  failed: { marca: "✗", cor: "text-red-400", label: "falhou" },
  blocked: { marca: "▣", cor: "text-amber-400", label: "bloqueada" },
  needs_human: { marca: "?", cor: "text-amber-400", label: "precisa de você" },
  cancelled: { marca: "—", cor: "text-faint", label: "cancelada" },
};
const ATIVOS: TaskStatus[] = ["queued", "loading_model", "implementing", "testing", "reviewing"];

/** Ordena para leitura: uma tarefa nunca aparece acima de outra de quem ela depende.
 *
 * O backend ordena por prioridade, que é o que vale para executar; na tela isso produzia
 * "TASK-002 ← TASK-001" logo ACIMA da TASK-001, e a seta apontava para trás. Aqui a prioridade
 * continua decidindo entre tarefas independentes — só as dependências furam a fila. */
function emOrdemDeLeitura(tasks: MaestroTask[]): MaestroTask[] {
  const porCodigo = new Map(tasks.map((t) => [t.code, t]));
  const saida: MaestroTask[] = [];
  const posto = new Set<string>();
  const visitando = new Set<string>();
  const coloca = (t: MaestroTask) => {
    if (posto.has(t.code) || visitando.has(t.code)) return; // visitando = ciclo; para e segue
    visitando.add(t.code);
    for (const dep of t.depends_on) {
      const d = porCodigo.get(dep);
      if (d) coloca(d);
    }
    visitando.delete(t.code);
    posto.add(t.code);
    saida.push(t);
  };
  tasks.forEach(coloca);
  return saida;
}

const card = "rounded-xl border border-line bg-panel";
const titulo = "px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-faint";

const gb = (n?: number | null) => (n == null ? "—" : `${(n / 1024 ** 3).toFixed(1)} GB`);
const dur = (s?: number | null) => (s == null ? "" : s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m${String(Math.round(s % 60)).padStart(2, "0")}`);

// ------------------------------------------------------------------ layout redimensionável

/** Larguras das três colunas (em partes proporcionais) e altura da doca (% da área). */
type Layout = { cols: [number, number, number]; dock: number };
// Tarefas estreita de propósito: é uma lista de códigos e títulos curtos, e o espaço rende mais na
// coluna da Maestro, onde está o texto e o composer.
const LAYOUT_PADRAO: Layout = { cols: [14, 56, 30], dock: 36 };
const LAYOUT_CHAVE = "forja.maestro.layout";
const COL_MIN = 10;              // % mínimo de cada coluna
const DOCK_MIN = 12, DOCK_MAX = 75;

function lerLayouts(): Record<string, Layout> {
  try {
    return JSON.parse(localStorage.getItem(LAYOUT_CHAVE) || "{}");
  } catch {
    return {};  // janela anônima ou storage bloqueado: segue no padrão
  }
}

/** Layout por conversa. Conversa sem layout salvo herda o último usado, para a pessoa não ter que
 * reajustar a cada conversa nova; o padrão só vale na primeira vez. */
function layoutDe(chave: string): Layout {
  const todos = lerLayouts();
  return todos[chave] ?? todos._ultimo ?? LAYOUT_PADRAO;
}

function useLayout(convId: number | null) {
  const chave = convId === null ? "_nova" : String(convId);
  const [st, setSt] = useState(() => ({ chave, layout: layoutDe(chave) }));
  // Trocou de conversa: ajusta no próprio render (padrão do React para "estado derivado de prop"),
  // sem um efeito que desenharia um quadro com o layout da conversa anterior.
  if (st.chave !== chave) setSt({ chave, layout: layoutDe(chave) });
  const layout = st.chave === chave ? st.layout : layoutDe(chave);
  const setLayout = (f: (l: Layout) => Layout) => setSt((s) => ({ ...s, layout: f(s.layout) }));
  const salvar = (l: Layout) => {
    try {
      localStorage.setItem(LAYOUT_CHAVE, JSON.stringify({ ...lerLayouts(), [chave]: l, _ultimo: l }));
    } catch {
      /* sem storage: vale só nesta sessão */
    }
  };
  return { layout, setLayout, salvar };
}

/** Faixa de arrasto entre dois blocos. Só reporta o deslocamento; quem sabe o que fazer é o pai. */
function Divisor(props: { eixo: "x" | "y"; onArrasto: (e: PointerEvent) => void; onFim: () => void }) {
  return (
    <div
      role="separator"
      aria-orientation={props.eixo === "x" ? "vertical" : "horizontal"}
      onPointerDown={(e) => {
        e.preventDefault();
        const mover = (ev: PointerEvent) => props.onArrasto(ev);
        const soltar = () => {
          window.removeEventListener("pointermove", mover);
          window.removeEventListener("pointerup", soltar);
          document.body.style.cursor = "";
          document.body.style.userSelect = "";
          props.onFim();
        };
        // cursor e seleção no body: arrastando rápido o ponteiro sai da faixa estreita
        document.body.style.cursor = props.eixo === "x" ? "col-resize" : "row-resize";
        document.body.style.userSelect = "none";
        window.addEventListener("pointermove", mover);
        window.addEventListener("pointerup", soltar);
      }}
      className={`group shrink-0 ${props.eixo === "x" ? "w-2 cursor-col-resize" : "h-2 cursor-row-resize"} flex items-center justify-center`}
    >
      <div className={`rounded-full bg-line transition-colors group-hover:bg-muted ${props.eixo === "x" ? "h-8 w-0.5" : "h-0.5 w-8"}`} />
    </div>
  );
}

type DocaTab = RightTab | "model" | "task";
// Ordem da doca: primeiro o que ela já tinha, depois as abas do painel direito que faltavam.
const DOCA_ORDEM: RightTab[] = ["browser", "terminal", "changes", "info", "servers", "local", "plans"];

export default function MaestroView(props: {
  convId: number | null;
  messages: Message[];
  draft: Draft | null;
  running: boolean;
  approvals: Record<string, Approval>;
  subSteps: Record<string, SubState>;
  board: MaestroBoard | null;
  onBoard: (b: MaestroBoard) => void;
  provider: string;
  model: string;
  modelPhase: ModelPhase | null;
  // A conversa do chat, montada pelo App (rolagem, pasta de trabalho, "Raciocinou ›", blocos de
  // ferramenta, tokens e t/s): a coluna da Maestro é ela, não uma imitação.
  conversa: React.ReactNode;
  // A mesma renderização aplicada a outra lista de mensagens: é como a coluna Worker desenha o Worker.
  renderConversa: Conversar;
  // O composer do App: modos, esforço, anexos, anel de contexto e seletor de modelo idênticos aos do
  // chat e do agente, porque é o mesmo bloco — não uma cópia que vai divergindo.
  composer: React.ReactNode;
  // Conteúdo das abas do painel direito, montado pelo App: a doca mostra os mesmos painéis.
  painel: (tab: RightTab) => React.ReactNode;
  onDecide: (callId: string, aprovado: boolean) => void;
}) {
  const { convId, board, onBoard } = props;
  const { layout, setLayout, salvar } = useLayout(convId);
  const area = useRef<HTMLDivElement>(null);     // tudo abaixo do cabeçalho: colunas + doca
  const colunas = useRef<HTMLDivElement>(null);
  // O último layout, para gravar quando o arrasto termina (o handler do pointerup foi criado no
  // pointerdown e enxergaria o estado daquele instante).
  const atual = useRef(layout);
  useEffect(() => {
    atual.current = layout;
  }, [layout]);

  function arrastaColuna(i: 0 | 1, e: PointerEvent) {
    const box = colunas.current?.getBoundingClientRect();
    if (!box) return;
    setLayout((l) => {
      const [a, b, c] = l.cols;
      // posição do ponteiro em "partes" desde a borda esquerda
      const pos = ((e.clientX - box.left) / box.width) * (a + b + c);
      const cols: [number, number, number] = [a, b, c];
      if (i === 0) {
        const novo = Math.min(Math.max(pos, COL_MIN), a + b - COL_MIN);
        cols[0] = novo;
        cols[1] = a + b - novo;
      } else {
        const novo = Math.min(Math.max(pos - a, COL_MIN), b + c - COL_MIN);
        cols[1] = novo;
        cols[2] = b + c - novo;
      }
      return { ...l, cols };
    });
  }

  function arrastaDoca(e: PointerEvent) {
    const box = area.current?.getBoundingClientRect();
    if (!box) return;
    const dock = ((box.bottom - e.clientY) / box.height) * 100;
    setLayout((l) => ({ ...l, dock: Math.min(Math.max(dock, DOCK_MIN), DOCK_MAX) }));
  }
  const [doca, setDoca] = useState<DocaTab>("browser");
  const [selecionada, setSelecionada] = useState<string | null>(null);
  const [detalhe, setDetalhe] = useState<MaestroTask | null>(null);
  const [modelos, setModelos] = useState<MaestroModels | null>(null);

  // Polling do board: o evento na SSE chega antes, mas é o polling que garante a árvore certa
  // depois de reconectar, de um F5 ou de uma queda no meio de uma tentativa.
  useEffect(() => {
    if (convId === null) return;
    let vivo = true;
    const puxa = () =>
      api.get<MaestroBoard>(`/maestro/${convId}/board`).then((b) => vivo && onBoard(b)).catch(() => {});
    puxa();
    const t = setInterval(puxa, POLL_MS);
    return () => {
      vivo = false;
      clearInterval(t);
    };
  }, [convId, onBoard]);

  useEffect(() => {
    if (doca !== "model") return;
    const puxa = () => api.get<MaestroModels>("/maestro/models").then(setModelos).catch(() => {});
    puxa();
    const t = setInterval(puxa, 3000);
    return () => clearInterval(t);
  }, [doca]);

  // Detalhe da tarefa selecionada (contrato, tentativas e a conversa gravada de cada Worker). Enquanto
  // ela está em andamento, relê no ritmo do board: depois de um F5 no meio da tarefa a SSE não tem o
  // começo da conversa do Worker, e o banco tem.
  // Sem Worker vivo e nada selecionado, a coluna Worker mostra a conversa gravada da última tarefa
  // que rodou: o estado ao vivo some no fim da rodada, o banco não.
  const tarefas = board?.features.flatMap((f) => f.tasks) ?? [];
  const ultima = tarefas.filter((t) => t.attempt_count > 0).sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
  const foco = selecionada ?? (Object.values(props.subSteps).some((w) => w.status) ? null : (ultima?.code ?? null));
  const sel = tarefas.find((t) => t.code === foco);
  const selAtiva = !!sel && ATIVOS.includes(sel.status);
  useEffect(() => {
    if (convId === null || !foco) {
      setDetalhe(null);
      return;
    }
    let vivo = true;
    const puxa = () =>
      api.get<MaestroTask>(`/maestro/${convId}/task/${foco}`)
        .then((t) => vivo && setDetalhe(t))
        .catch(() => vivo && setDetalhe(null));
    puxa();
    const t = selAtiva ? setInterval(puxa, POLL_MS) : undefined;
    return () => {
      vivo = false;
      clearInterval(t);
    };
  }, [convId, foco, selAtiva, sel?.status, sel?.attempt_count]);

  const emAndamento = useMemo(
    () => board?.features.flatMap((f) => f.tasks).find((t) => ATIVOS.includes(t.status)) ?? null,
    [board],
  );

  // Coluna WORKER. Cada run_task abre um bloco de passos, chaveado pelo id da chamada. No modo
  // sequencial há um vivo por vez; no paralelo, vários — e cada um vira uma aba. Vivo = status não
  // vazio: o subagente zera o status ao terminar.
  const workers = useMemo(() => {
    const tarefaDa = new Map<string, string>();
    for (const m of props.messages)
      for (const c of m.tool_calls ?? [])
        if (c.name === "run_task") tarefaDa.set(c.id, String(c.arguments?.code ?? ""));
    const todos = Object.entries(props.subSteps).map(([id, w]) => ({ id, w, code: tarefaDa.get(id) ?? "" }));
    const vivos = todos.filter((x) => x.w.status);
    // Nenhum vivo: mostra o último que terminou, para o resultado não sumir da tela.
    return vivos.length ? vivos : todos.slice(-1);
  }, [props.subSteps, props.messages]);

  // Aba da coluna Worker: um Worker vivo, ou "hist" = a conversa gravada da tarefa selecionada.
  const [abaWorker, setAbaWorker] = useState<string | null>(null);
  const abasWorker: AbaWorker[] = [
    ...workers.map((x) => ({ tipo: "vivo" as const, ...x })),
    ...(foco && !workers.some((x) => x.code === foco)
      ? [{ tipo: "historico" as const, id: "hist" as const, code: foco }]
      : []),
  ];

  function abrirTarefa(code: string) {
    setSelecionada(code);
    setDoca("task");
    const vivo = workers.find((x) => x.code === code && x.w.status);
    setAbaWorker(vivo ? vivo.id : "hist");
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-2">
      <Cabecalho board={board} running={props.running} tarefa={emAndamento} modelPhase={props.modelPhase}
                 model={props.model} />

      <div ref={area} className="flex min-h-0 flex-1 flex-col">
      <div
        ref={colunas}
        className="grid min-h-0"
        style={{
          flex: `${100 - layout.dock} 1 0`,
          gridTemplateColumns: `minmax(0,${layout.cols[0]}fr) auto minmax(0,${layout.cols[1]}fr) auto minmax(0,${layout.cols[2]}fr)`,
        }}
      >
        <Arvore board={board} selecionada={selecionada} onSelect={abrirTarefa} />
        <Divisor eixo="x" onArrasto={(e) => arrastaColuna(0, e)} onFim={() => salvar(atual.current)} />
        <ColunaMaestro conversa={props.conversa} composer={props.composer} />
        <Divisor eixo="x" onArrasto={(e) => arrastaColuna(1, e)} onFim={() => salvar(atual.current)} />
        <ColunaWorker
          key={foco ?? ""}
          abas={abasWorker}
          aba={abaWorker}
          onAba={setAbaWorker}
          detalhe={detalhe}
          approvals={props.approvals}
          conversar={props.renderConversa}
        />
      </div>

      <Divisor eixo="y" onArrasto={arrastaDoca} onFim={() => salvar(atual.current)} />

      <div className={`${card} flex min-h-[120px] flex-col`} style={{ flex: `${layout.dock} 1 0` }}>
        <div className="flex shrink-0 items-center gap-1 border-b border-line px-2 py-1">
          {([
            ...DOCA_ORDEM.slice(0, 3).map((id) => abaDireita(id)),
            ["model", "Modelo · VRAM", <Cube className="size-3.5" />],
            ["task", selecionada ?? "Tarefa", <Split className="size-3.5" />],
            ...DOCA_ORDEM.slice(3).map((id) => abaDireita(id)),
          ] as [DocaTab, string, React.ReactNode][]).map(([id, label, icone]) => (
            <button
              key={id}
              onClick={() => setDoca(id as DocaTab)}
              className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-xs ${
                doca === id ? "bg-raised text-fg" : "text-faint hover:text-fg"
              }`}
            >
              {icone}
              {label}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          {/* BrowserPanel reporta o próprio retângulo ao Electron, então a WebContentsView nativa
              segue a doca sem nenhuma ligação extra daqui. O painel direito fica escondido nesta
              tela: dois BrowserPanel da mesma conversa brigariam pela mesma view nativa. */}
          {doca !== "model" && doca !== "task" ? (
            <div className="h-full overflow-auto">{props.painel(doca)}</div>
          ) : doca === "model" ? (
            <PainelModelos
              modelos={modelos}
              onSlot={async (nivel, provider, model) => {
                const slots = { ...(modelos?.slots ?? {}), [nivel]: { provider, model } };
                setModelos((m) => (m ? { ...m, slots } : m));  // reflete antes do próximo polling
                await api.put("/settings", { subagents: slots }).catch(() => {});
              }}
              onWorkers={async (n) => {
                setModelos((m) => (m ? { ...m, max_workers: n, can_swap: n <= 1 } : m));
                await api.put("/settings", { max_workers: n }).catch(() => {});
              }}
            />
          ) : (
            <PainelTarefa tarefa={detalhe} />
          )}
        </div>
      </div>
      </div>
    </div>
  );
}

/** Rótulo e ícone de uma aba do painel direito, no tamanho da doca. */
function abaDireita(id: RightTab): [DocaTab, string, React.ReactNode] {
  const t = ABAS_DIREITA.find((x) => x.id === id)!;
  return [id, t.label, <span className="[&>svg]:size-3.5">{t.icon}</span>];
}

// ------------------------------------------------------------------ cabeçalho

function Cabecalho(props: {
  board: MaestroBoard | null;
  running: boolean;
  tarefa: MaestroTask | null;
  modelPhase: ModelPhase | null;
  model: string;
}) {
  const b = props.board;
  const pct = b && b.total ? Math.round((b.done / b.total) * 100) : 0;
  return (
    <div className={`${card} flex shrink-0 items-center gap-3 px-3 py-2`}>
      <Split className="size-4 shrink-0 text-violet-400" />
      <span className="text-sm font-medium">Maestro</span>
      <span className="truncate text-xs text-faint">{props.model}</span>
      {b && b.total > 0 && (
        <>
          <div className="h-1.5 w-32 shrink-0 overflow-hidden rounded-full bg-raised">
            <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
          </div>
          <span className="shrink-0 text-xs text-muted">
            {b.done}/{b.total} tarefas
          </span>
        </>
      )}
      <div className="ml-auto flex items-center gap-3 text-xs">
        {/* Trocar de modelo local leva minutos de VRAM indo e voltando. Sem dizer isso, o cockpit
            parece travado justamente quando está fazendo o que torna a IA local viável. */}
        {props.modelPhase ? (
          <TrocaDeModelo fase={props.modelPhase} />
        ) : props.tarefa ? (
          <span className={ESTADO[props.tarefa.status].cor}>
            {props.tarefa.code} · {ESTADO[props.tarefa.status].label}
          </span>
        ) : props.running ? (
          <span className="text-sky-400">pensando…</span>
        ) : (
          <span className="text-faint">parado</span>
        )}
      </div>
    </div>
  );
}

function TrocaDeModelo({ fase }: { fase: ModelPhase }) {
  const texto =
    fase.phase === "unloading"
      ? `Descarregando ${fase.previous || "modelo"}…`
      : fase.phase === "loading"
        ? `Carregando ${fase.model}…`
        : fase.phase === "error"
          ? `Falha ao carregar ${fase.model}`
          : "";
  if (!texto) return null;
  return (
    <span className={`flex items-center gap-1.5 ${fase.phase === "error" ? "text-red-400" : "text-amber-400"}`}>
      {fase.phase !== "error" && <Cube className="size-3.5 animate-pulse" />}
      {texto}
    </span>
  );
}

// ------------------------------------------------------------------ árvore

function Arvore(props: { board: MaestroBoard | null; selecionada: string | null; onSelect: (c: string) => void }) {
  const b = props.board;
  return (
    <div className={`${card} flex min-h-0 flex-col`}>
      <div className={titulo}>Tarefas</div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {!b || !b.total ? (
          <p className="px-2 py-6 text-center text-xs text-faint">
            Nenhuma tarefa ainda.
            <br />
            Diga o objetivo do projeto e a Maestro monta o plano.
          </p>
        ) : (
          b.features.map((f) => (
            <div key={f.id} className="mb-2">
              <div className="flex items-baseline gap-1.5 px-2 py-1">
                <span className="truncate text-xs font-medium">{f.title}</span>
                {f.status === "done" && <Check className="size-3 shrink-0 text-emerald-400" />}
              </div>
              {emOrdemDeLeitura(f.tasks).map((t) => {
                const e = ESTADO[t.status];
                const ativa = ATIVOS.includes(t.status);
                return (
                  <button
                    key={t.code}
                    onClick={() => props.onSelect(t.code)}
                    title={t.contract?.goal || t.title}
                    className={`flex w-full items-start gap-1.5 rounded-md px-2 py-1 text-left text-xs hover:bg-raised ${
                      props.selecionada === t.code ? "bg-raised" : ""
                    }`}
                  >
                    <span className={`w-3 shrink-0 ${e.cor} ${ativa ? "animate-pulse" : ""}`}>{e.marca}</span>
                    <span className="min-w-0 flex-1">
                      <span className="text-faint">{t.code.replace("TASK-", "")}</span>{" "}
                      <span className={t.status === "completed" ? "text-muted line-through" : ""}>{t.title}</span>
                      {t.attempt_count > 1 && (
                        <span className="ml-1 text-[10px] text-amber-400">
                          tent. {t.attempt_count}/{t.max_attempts}
                        </span>
                      )}
                      {!!t.depends_on.length && t.status === "pending" && (
                        <span className="block text-[10px] text-faint">← {t.depends_on.join(", ")}</span>
                      )}
                      {t.blocked_reason && <span className="block text-[10px] text-amber-400">{t.blocked_reason}</span>}
                    </span>
                  </button>
                );
              })}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ colunas

/** Coluna da Maestro: a conversa do chat (o mesmo bloco do App) e o composer do chat. */
function ColunaMaestro(props: { conversa: React.ReactNode; composer: React.ReactNode }) {
  return (
    <div className={`${card} flex min-h-0 flex-col overflow-hidden`}>
      <div className={titulo}>Maestro</div>
      {props.conversa}
      <div className="shrink-0">{props.composer}</div>
    </div>
  );
}

export type Conversar = (
  msgs: Message[],
  o?: { draft?: Draft | null; stats?: TurnStats | null; vivo?: boolean; readonly?: boolean },
) => React.ReactNode;

/** Uma aba da coluna Worker: um Worker vivo (ou o último desta sessão), ou o histórico de uma tarefa. */
type AbaWorker =
  | { tipo: "vivo"; id: string; code: string; w: SubState }
  | { tipo: "historico"; id: "hist"; code: string };

/**
 * Coluna Worker: a conversa do Worker desenhada como a tela do agente — "Raciocinou ›", blocos de
 * ferramenta com diff, saída de comando ao vivo, linha de tokens e t/s, anel de contexto — só que sem
 * campo de mensagem, porque quem conversa com o Worker é a Maestro, pelo contrato.
 *
 * Ao vivo vem da SSE; depois, da tentativa gravada no banco (a conversa é salva a cada rodada), então
 * clicar numa tarefa da árvore mostra o que o Worker fez nela, inclusive em tentativas antigas.
 */
function ColunaWorker(props: {
  abas: AbaWorker[];
  aba: string | null;
  onAba: (id: string) => void;
  detalhe: MaestroTask | null;
  approvals: Record<string, Approval>;
  conversar: Conversar;
}) {
  const atual = props.abas.find((x) => x.id === props.aba) ?? props.abas[0] ?? null;
  const [tentativa, setTentativa] = useState<number | null>(null);

  // histórico: tentativas da tarefa com conversa gravada (a mais recente por padrão)
  const hist = atual?.tipo === "historico" && props.detalhe?.code === atual.code ? props.detalhe : null;
  const comConversa = (hist?.attempts ?? []).filter((a) => a.transcript?.length);
  const escolhida = comConversa.find((a) => a.n === tentativa) ?? comConversa[comConversa.length - 1] ?? null;

  const vivo = atual?.tipo === "vivo" && !!atual.w.status;
  const msgs: Message[] =
    atual?.tipo === "vivo" ? (atual.w.mensagens ?? []) : ((escolhida?.transcript as Message[] | undefined) ?? []);
  const draft = atual?.tipo === "vivo" ? (atual.w.draft ?? null) : null;

  // números da última rodada: janela ocupada e modelo, para o anel de contexto
  const doModelo = msgs.flatMap((m) => (m.role === "assistant" && m.meta?.stats ? [m.meta.stats as Stats] : []));
  const ultima = doModelo[doModelo.length - 1];
  const usado = ultima ? (ultima.prompt_tokens ?? 0) + (ultima.tokens ?? 0) : null;
  const statsAoVivo = vivo && doModelo.length ? aggregate(doModelo) : null;
  // Cola no fim como o chat: segue a geração até a pessoa rolar para cima; trocar de aba volta a colar.
  const { ref: rolagem, fim, onScroll, colar } = useStickyBottom<HTMLDivElement>([msgs, draft, props.approvals]);
  useEffect(() => {
    colar();
  }, [atual?.id, escolhida?.n, colar]);
  const pendente = (w: SubState) =>
    (w.mensagens ?? []).some((m) => (m.tool_calls ?? []).some((c) => props.approvals[c.id]));

  return (
    <div className={`${card} flex min-h-0 flex-col overflow-hidden`}>
      <div className="flex shrink-0 items-center gap-1 px-3 py-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wide text-faint">
          {props.abas.filter((x) => x.tipo === "vivo" && x.w.status).length > 1 ? "Workers" : "Worker"}
        </span>
        <div className="ml-1 flex min-w-0 gap-1 overflow-x-auto">
          {props.abas.map((x) => (
            <button
              key={x.id}
              onClick={() => props.onAba(x.id)}
              title={x.tipo === "historico" ? `Histórico de ${x.code}` : `${x.code}${x.w.status ? " — " + x.w.status : ""}`}
              className={`flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] ${
                x.id === atual?.id ? "bg-raised text-fg" : "text-faint hover:text-fg"
              }`}
            >
              {x.tipo === "vivo" && x.w.status && (
                <span
                  className={`inline-block size-1.5 rounded-full ${pendente(x.w) ? "bg-amber-400" : "animate-pulse bg-sky-400"}`}
                  title={pendente(x.w) ? "esperando aprovação" : undefined}
                />
              )}
              {x.code || "…"}
            </button>
          ))}
        </div>
        {usado != null && (
          <div className="ml-auto shrink-0">
            <ContextRing
              used={usado}
              max={ultima?.ctx_max ?? null}
              out={ultima?.tokens ?? null}
              avg={doModelo.length ? aggregate(doModelo).tps : null}
              canCompact={false}
              onCompact={() => {}}
              provider=""
              models={[...new Set(doModelo.map((s) => s.model).filter(Boolean))]}
              abaixo
            />
          </div>
        )}
      </div>

      {atual?.tipo === "historico" && comConversa.length > 1 && (
        <div className="flex shrink-0 items-center gap-1 px-3 pb-1 text-[10px] text-faint">
          tentativa
          {comConversa.map((a) => (
            <button
              key={a.n}
              onClick={() => setTentativa(a.n)}
              className={`rounded px-1.5 py-0.5 ${a.n === escolhida?.n ? "bg-raised text-fg" : "hover:text-fg"}`}
            >
              #{a.n}
            </button>
          ))}
        </div>
      )}
      {atual?.tipo === "vivo" && atual.w.status && (
        <p className="shrink-0 truncate px-3 pb-1 text-[11px] text-sky-400">{atual.w.status}</p>
      )}

      <div ref={rolagem} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
        {!atual ? (
          <p className="px-4 py-6 text-center text-xs text-faint">
            Nenhum Worker agora.
            <br />A Maestro despacha as tarefas; clique numa tarefa da árvore para ver o que o Worker fez nela.
          </p>
        ) : msgs.length || draft ? (
          <div className="px-4 py-3">
            {props.conversar(msgs, { readonly: true, vivo, draft, stats: statsAoVivo })}
          </div>
        ) : atual.tipo === "vivo" && atual.w.steps.length ? (
          // Worker desta sessão sem conversa (delegação antiga ou reconexão no meio): só os passos.
          <div className="px-2 py-2 text-xs">
            {atual.w.steps.map((st, i) => (
              <div key={st.call?.id ?? i} className="flex items-start gap-1.5 border-b border-line/50 px-1 py-1">
                <span className={st.result?.status === "erro" ? "text-red-400" : st.result ? "text-emerald-400" : "animate-pulse text-sky-400"}>
                  {st.result?.status === "erro" ? "✗" : st.result ? "✓" : "⟳"}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="font-mono text-[11px]">{st.call?.name}</span>
                  <span className="block truncate text-[10px] text-faint">{alvo(st.call?.arguments)}</span>
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="px-4 py-6 text-center text-xs text-faint">
            {atual.tipo === "historico" ? `${atual.code} ainda não tem conversa de Worker gravada.` : "Começando…"}
          </p>
        )}
        <div ref={fim} />
      </div>
    </div>
  );
}

/** O que a chamada está mexendo, para a linha do passo dizer algo além do nome da ferramenta. */
function alvo(args: any): string {
  if (!args || typeof args !== "object") return "";
  return String(args.path ?? args.command ?? args.url ?? args.query ?? args.pattern ?? "");
}

// ------------------------------------------------------------------ doca

function PainelModelos(props: {
  modelos: MaestroModels | null;
  onSlot: (nivel: string, provider: string, model: string) => void;
  onWorkers: (n: number) => void;
}) {
  // Estado local só para o select não "voltar" entre o clique e o próximo polling.
  const [lifecycle, setLifecycle] = useState<string | null>(null);
  const base = props.modelos;
  const m = base && lifecycle ? { ...base, lifecycle } : base;
  useEffect(() => {
    if (base && lifecycle && base.lifecycle === lifecycle) setLifecycle(null);
  }, [base, lifecycle]);
  if (!m) return <p className="p-3 text-xs text-faint">Carregando…</p>;
  const usado = m.vram != null && m.vram_free != null ? 1 - m.vram_free / m.vram : null;
  return (
    <div className="h-full space-y-3 overflow-y-auto p-3 text-xs [&>*]:max-w-xl">
      <div className="flex items-center gap-2">
        <span className={`size-2 rounded-full ${m.running ? "bg-emerald-400" : "bg-faint"}`} />
        <span className="font-medium">{m.running ? m.alias : "nenhum modelo local carregado"}</span>
        {m.ctx && <span className="text-faint">contexto {m.ctx}</span>}
      </div>
      {usado != null && (
        <div>
          <div className="mb-1 flex justify-between text-faint">
            <span>VRAM</span>
            <span>
              {gb(m.vram! - m.vram_free!)} / {gb(m.vram)}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-raised">
            <div
              className={`h-full rounded-full ${usado > 0.9 ? "bg-red-500" : usado > 0.7 ? "bg-amber-500" : "bg-emerald-500"}`}
              style={{ width: `${Math.round(usado * 100)}%` }}
            />
          </div>
        </div>
      )}
      <div className="text-faint">
        RAM {gb(m.ram != null && m.ram_free != null ? m.ram - m.ram_free : null)} / {gb(m.ram)}
      </div>
      <div className="space-y-1.5">
        <div className="text-faint">Modelo dos Workers</div>
        {/* Aqui e não só nas Configurações: escolher o Worker é decisão de execução, e quem está
            olhando o cockpit é quem percebe que o modelo atual não está dando conta da tarefa. */}
        {(["rapido", "capaz", "nuvem"] as const).map((nivel) => {
          const slot = m.slots[nivel];
          return (
            <div key={nivel} className="flex items-center gap-2 [&>div]:ml-0">
              <span className="w-14 shrink-0 capitalize text-faint">{nivel}</span>
              {/* autoFallback desligado: slot vazio é escolha (sem reserva na nuvem = sem gasto de
                  API), e o seletor não pode gravar um modelo só porque o painel foi aberto. */}
              <ModelPicker
                provider={slot?.provider || ""}
                model={slot?.model || ""}
                autoFallback={false}
                loadLocal={false}
                minCtx={m.min_ctx_worker}
                onChange={(provider, model) => props.onSlot(nivel, provider, model)}
              />
              {slot?.model && (
                <button
                  onClick={() => props.onSlot(nivel, "", "")}
                  title={`Deixar o slot ${nivel} sem modelo`}
                  className="shrink-0 rounded p-1 text-faint hover:bg-raised hover:text-fg"
                >
                  <X className="size-3" />
                </button>
              )}
            </div>
          );
        })}
        {Object.keys(m.slots).length === 0 && (
          <p className="text-amber-400">
            Nenhum Worker configurado — sem isso a Maestro não tem a quem delegar.
          </p>
        )}
      </div>
      <div className="space-y-1">
        <div className="text-faint">Ao terminar uma tarefa</div>
        <select
          value={m.lifecycle}
          disabled={!m.manageable}
          onChange={(e) => {
            const lifecycle = e.target.value;
            setLifecycle(lifecycle);
            api.put("/settings", { model_lifecycle: lifecycle }).catch(() => setLifecycle(m.lifecycle));
          }}
          className="w-full rounded-md border border-line bg-bg px-2 py-1 outline-none disabled:text-faint"
        >
          <option value="persistent">Manter o modelo carregado</option>
          <option value="unload_after_task">Descarregar e liberar a VRAM</option>
        </select>
        <p className="text-faint">
          {m.lifecycle === "persistent"
            ? "Recarregar custa minutos: vale manter quando as tarefas usam o mesmo modelo."
            : "Libera a VRAM a cada tarefa. É o modo para máquina apertada ou modelos diferentes por tarefa."}
        </p>
      </div>
      <div className="space-y-1">
        <div className="text-faint">Execução dos Workers</div>
        <select
          value={m.max_workers}
          onChange={(e) => props.onWorkers(Number(e.target.value))}
          className="w-full rounded-md border border-line bg-bg px-2 py-1 outline-none"
        >
          <option value={1}>Sequencial — um por vez</option>
          {[2, 3, 4].map((n) => (
            <option key={n} value={n}>Paralelo — até {n} ao mesmo tempo</option>
          ))}
        </select>
        <p className="text-faint">
          {m.max_workers === 1
            ? "Um Worker por vez, e o modelo local pode ser trocado entre tarefas. É o modo para máquina com pouca VRAM."
            : "Tarefas independentes rodam juntas; as que mexem nos mesmos arquivos esperam a vez. Sem troca automática de modelo local — dois Workers disputariam a mesma VRAM."}
        </p>
      </div>
    </div>
  );
}

function PainelTarefa(props: { tarefa: MaestroTask | null }) {
  const t = props.tarefa;
  if (!t) return <p className="p-3 text-xs text-faint">Clique numa tarefa da árvore para ver o contrato.</p>;
  const c = t.contract || {};
  return (
    <div className="space-y-3 overflow-y-auto p-3 text-xs">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-faint">{t.code}</span>
        <span className="font-medium">{t.title}</span>
        <span className={ESTADO[t.status].cor}>{ESTADO[t.status].label}</span>
        {t.model_slot && <span className="text-faint">· {t.model_slot}</span>}
      </div>
      {t.blocked_reason && <p className="rounded-md bg-amber-500/10 p-2 text-amber-300">{t.blocked_reason}</p>}
      <Campo titulo="Objetivo" texto={c.goal} />
      <Campo titulo="Contexto" texto={c.context} />
      <Lista titulo="Arquivos relevantes" itens={c.relevant_files} mono />
      <Lista titulo="Requisitos" itens={c.requirements} />
      <Lista titulo="Restrições" itens={c.constraints} />
      <Lista titulo="Não faça" itens={c.do_not} />
      <Lista titulo="Critérios de aceitação" itens={c.acceptance_criteria} />
      {c.verify_command && <Campo titulo="Verificação" texto={c.verify_command} mono />}
      {!!t.attempts?.length && (
        <div>
          <div className="mb-1 text-faint">Tentativas</div>
          {t.attempts.map((a) => (
            <Tentativa key={a.n} a={a} />
          ))}
        </div>
      )}
    </div>
  );
}

function Tentativa({ a }: { a: TaskAttempt }) {
  const [aberta, setAberta] = useState(false);
  const r = a.result;
  return (
    <div className="border-b border-line/50 py-1">
      <button onClick={() => setAberta((v) => !v)} className="flex w-full items-center gap-2 text-left hover:text-fg">
        <span
          className={a.status === "completed" ? "text-emerald-400" : a.status === "running" ? "text-sky-400"
            : a.status === "unverified" ? "text-amber-400" : "text-red-400"}
          title={a.status === "unverified" ? "sem comando de verificação: a Maestro julgou" : undefined}
        >
          {a.status === "completed" ? "✓" : a.status === "running" ? "⟳" : a.status === "unverified" ? "?" : "✗"}
        </span>
        <span>#{a.n}</span>
        <span className="truncate text-faint">{a.worker?.model ?? "—"}</span>
        <span className="ml-auto shrink-0 text-faint">{dur(a.seconds)}</span>
      </button>
      {aberta && (
        <div className="space-y-1 py-1 pl-5 text-[11px]">
          {a.strategy && <p className="text-violet-300">estratégia: {a.strategy}</p>}
          {a.error && <p className="text-red-400">{a.error}</p>}
          {r?.tests && (
            <p className={r.tests.status === "ok" ? "text-emerald-400" : "text-red-400"}>
              <span className="font-mono">{r.tests.command}</span> — {r.tests.status === "ok" ? "passou" : "falhou"}
            </p>
          )}
          {r?.changes?.map((ch) => (
            <p key={ch.path} className="font-mono text-faint">
              {ch.path}
              {ch.additions != null && (
                <>
                  {" "}
                  <span className="text-emerald-400">+{ch.additions}</span>{" "}
                  <span className="text-red-400">−{ch.deletions}</span>
                </>
              )}
            </p>
          ))}
          {r?.errors?.map((e, i) => (
            <p key={i} className="whitespace-pre-wrap text-red-400">
              {e}
            </p>
          ))}
          {r?.summary && <p className="whitespace-pre-wrap text-muted">{r.summary}</p>}
        </div>
      )}
    </div>
  );
}

function Campo({ titulo: t, texto, mono }: { titulo: string; texto?: string; mono?: boolean }) {
  if (!texto) return null;
  return (
    <div>
      <div className="text-faint">{t}</div>
      <p className={`whitespace-pre-wrap ${mono ? "font-mono text-[11px]" : ""}`}>{texto}</p>
    </div>
  );
}

function Lista({ titulo: t, itens, mono }: { titulo: string; itens?: string[]; mono?: boolean }) {
  if (!itens?.length) return null;
  return (
    <div>
      <div className="text-faint">{t}</div>
      <ul className="list-inside list-disc space-y-0.5">
        {itens.map((x, i) => (
          <li key={i} className={mono ? "font-mono text-[11px]" : ""}>
            {x}
          </li>
        ))}
      </ul>
    </div>
  );
}

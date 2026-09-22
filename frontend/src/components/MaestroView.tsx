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
import type {
  Approval, Draft, MaestroBoard, MaestroModels, MaestroTask, Message, TaskAttempt, TaskStatus,
} from "../types";
import BrowserPanel from "./BrowserPanel";
import ChangesPanel from "./ChangesPanel";
import { Activity, Check, Clock, Cube, Globe, Split, Terminal, Tokens, X } from "./icons";
import { Markdown, Thinking } from "./MessageView";
import TerminalPanel from "./TerminalPanel";

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

type DocaTab = "browser" | "terminal" | "changes" | "model" | "task";

export default function MaestroView(props: {
  convId: number | null;
  messages: Message[];
  draft: Draft | null;
  running: boolean;
  approvals: Record<string, Approval>;
  subSteps: Record<string, { status: string; steps: { call: any; result?: Message }[] }>;
  board: MaestroBoard | null;
  onBoard: (b: MaestroBoard) => void;
  provider: string;
  model: string;
  onSend: (texto: string) => void;
  onStop: () => void;
  onDecide: (callId: string, aprovado: boolean) => void;
}) {
  const { convId, board, onBoard } = props;
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

  useEffect(() => {
    if (convId === null || !selecionada) {
      setDetalhe(null);
      return;
    }
    let vivo = true;
    api.get<MaestroTask>(`/maestro/${convId}/task/${selecionada}`)
      .then((t) => vivo && setDetalhe(t))
      .catch(() => vivo && setDetalhe(null));
    return () => {
      vivo = false;
    };
  }, [convId, selecionada, board?.total, board?.done]);

  const emAndamento = useMemo(
    () => board?.features.flatMap((f) => f.tasks).find((t) => ATIVOS.includes(t.status)) ?? null,
    [board],
  );

  // Coluna WORKER: o delegate/run_task vivo é o último bloco de passos que o App recebeu.
  const workerId = useMemo(() => {
    const ids = Object.keys(props.subSteps);
    return ids.length ? ids[ids.length - 1] : null;
  }, [props.subSteps]);
  const worker = workerId ? props.subSteps[workerId] : null;

  function abrirTarefa(code: string) {
    setSelecionada(code);
    setDoca("task");
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-2">
      <Cabecalho board={board} running={props.running} model={props.model} tarefa={emAndamento} />

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(230px,1fr)_minmax(0,1.9fr)_minmax(250px,1.1fr)] gap-2">
        <Arvore board={board} selecionada={selecionada} onSelect={abrirTarefa} />
        <ColunaMaestro
          messages={props.messages}
          draft={props.draft}
          running={props.running}
          approvals={props.approvals}
          onSend={props.onSend}
          onStop={props.onStop}
          onDecide={props.onDecide}
        />
        <ColunaWorker worker={worker} tarefa={emAndamento} />
      </div>

      <div className={`${card} flex h-[38%] min-h-[180px] flex-col`}>
        <div className="flex shrink-0 items-center gap-1 border-b border-line px-2 py-1">
          {([
            ["browser", "Navegador", <Globe className="size-3.5" />],
            ["terminal", "Terminal", <Terminal className="size-3.5" />],
            ["changes", "Mudanças", <Activity className="size-3.5" />],
            ["model", "Modelo · VRAM", <Cube className="size-3.5" />],
            ["task", selecionada ?? "Tarefa", <Split className="size-3.5" />],
          ] as const).map(([id, label, icone]) => (
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
              segue a doca sem nenhuma ligação extra daqui. */}
          {doca === "browser" ? (
            <BrowserPanel conv={convId === null ? "0" : String(convId)} onState={() => {}} />
          ) : doca === "terminal" ? (
            <TerminalPanel conv={convId === null ? "0" : String(convId)} />
          ) : doca === "changes" ? (
            <ChangesPanel
              conv={convId}
              provider={props.provider}
              model={props.model}
              // uma tarefa fechou: o diff mudou
              refreshKey={board?.done ?? 0}
              action={null}
              onActionDone={() => {}}
              onCount={() => {}}
              onOpen={() => {}}
              onConversationChanged={() => {}}
            />
          ) : doca === "model" ? (
            <PainelModelos modelos={modelos} />
          ) : (
            <PainelTarefa tarefa={detalhe} />
          )}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ cabeçalho

function Cabecalho(props: { board: MaestroBoard | null; running: boolean; model: string; tarefa: MaestroTask | null }) {
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
      <div className="ml-auto flex items-center gap-2 text-xs">
        {props.tarefa ? (
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

function ColunaMaestro(props: {
  messages: Message[];
  draft: Draft | null;
  running: boolean;
  approvals: Record<string, Approval>;
  onSend: (texto: string) => void;
  onStop: () => void;
  onDecide: (callId: string, aprovado: boolean) => void;
}) {
  const fim = useRef<HTMLDivElement>(null);
  const visiveis = props.messages.filter((m) => m.role === "assistant" || m.role === "user" || m.role === "event");

  useEffect(() => {
    fim.current?.scrollIntoView({ block: "end" });
  }, [props.messages.length, props.draft?.content]);

  return (
    <div className={`${card} flex min-h-0 flex-col`}>
      <div className={titulo}>Maestro</div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 pb-2">
        {visiveis.map((m) => (
          <div key={m.id} className={m.role === "user" ? "rounded-lg bg-raised px-3 py-2 text-sm" : "text-sm"}>
            {m.role === "event" ? (
              <p className="text-xs text-faint">{m.content}</p>
            ) : (
              <>
                {m.thinking && <Thinking text={m.thinking} />}
                <Markdown text={m.content} />
              </>
            )}
          </div>
        ))}
        {props.draft && (
          <div className="text-sm">
            {props.draft.thinking && <Thinking text={props.draft.thinking} live />}
            <Markdown text={props.draft.content} />
          </div>
        )}
        <div ref={fim} />
      </div>
      <Aprovacoes approvals={props.approvals} onDecide={props.onDecide} />
      <Compositor running={props.running} onSend={props.onSend} onStop={props.onStop} />
    </div>
  );
}

/** Human-in-the-loop: uma execução autônoma longa não pode ficar parada num card fora de vista. */
function Aprovacoes(props: { approvals: Record<string, Approval>; onDecide: (id: string, ok: boolean) => void }) {
  const abertas = Object.entries(props.approvals).filter(([, a]) => !a.questions && !a.plan);
  if (!abertas.length) return null;
  return (
    <div className="shrink-0 space-y-2 border-t border-amber-500/30 bg-amber-500/5 p-2">
      {abertas.map(([id, a]) => (
        <div key={id} className="text-xs">
          <div className="mb-1 flex items-center gap-2">
            <span className="font-mono text-amber-300">{a.tool}</span>
            {a.preview?.path && <span className="truncate text-faint">{a.preview.path}</span>}
          </div>
          {a.preview?.text && (
            <pre className="mb-1.5 max-h-28 overflow-auto whitespace-pre-wrap rounded-md bg-bg p-2 font-mono text-[11px]">
              {a.preview.text.slice(0, 2000)}
            </pre>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => props.onDecide(id, true)}
              className="flex items-center gap-1 rounded-md bg-fg px-2 py-1 text-black hover:bg-white"
            >
              <Check className="size-3" /> Permitir
            </button>
            <button
              onClick={() => props.onDecide(id, false)}
              className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-muted hover:text-fg"
            >
              <X className="size-3" /> Recusar
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function Compositor(props: { running: boolean; onSend: (t: string) => void; onStop: () => void }) {
  const [texto, setTexto] = useState("");
  const enviar = () => {
    const t = texto.trim();
    if (!t) return;
    props.onSend(t);
    setTexto("");
  };
  return (
    <div className="flex shrink-0 items-end gap-2 border-t border-line p-2">
      <textarea
        rows={1}
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            enviar();
          }
        }}
        placeholder={props.running ? "Mensagem para o próximo passo da Maestro (entra na fila)…" : "Qual é o objetivo do projeto?"}
        className="max-h-32 min-h-[34px] flex-1 resize-none rounded-lg border border-line bg-bg px-2 py-1.5 text-sm outline-none focus:border-muted"
      />
      {props.running ? (
        <button onClick={props.onStop} title="Parar" className="rounded-lg border border-line px-2 py-1.5 text-xs text-muted hover:text-fg">
          Parar
        </button>
      ) : (
        <button
          onClick={enviar}
          disabled={!texto.trim()}
          className="rounded-lg bg-fg px-3 py-1.5 text-xs text-black hover:bg-white disabled:bg-raised disabled:text-faint"
        >
          Enviar
        </button>
      )}
    </div>
  );
}

function ColunaWorker(props: { worker: { status: string; steps: { call: any; result?: Message }[] } | null; tarefa: MaestroTask | null }) {
  const fim = useRef<HTMLDivElement>(null);
  useEffect(() => {
    fim.current?.scrollIntoView({ block: "end" });
  }, [props.worker?.steps.length, props.worker?.status]);

  return (
    <div className={`${card} flex min-h-0 flex-col`}>
      <div className={titulo}>Worker</div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 text-xs">
        {!props.worker ? (
          <p className="px-1 py-6 text-center text-faint">
            Nenhum Worker agora.
            <br />A Maestro despacha uma tarefa por vez.
          </p>
        ) : (
          <>
            {props.worker.status && <p className="px-1 pb-2 text-sky-400">{props.worker.status}</p>}
            {/* Só operação: ferramenta, alvo e resultado. O raciocínio interno do Worker não sai
                daqui nem entra no histórico da Maestro — é ruído para quem acompanha. */}
            {props.worker.steps.map((st, i) => (
              <div key={st.call?.id ?? i} className="flex items-start gap-1.5 border-b border-line/50 px-1 py-1">
                <span
                  className={
                    st.result?.status === "erro"
                      ? "text-red-400"
                      : st.result
                        ? "text-emerald-400"
                        : "animate-pulse text-sky-400"
                  }
                >
                  {st.result?.status === "erro" ? "✗" : st.result ? "✓" : "⟳"}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="font-mono text-[11px]">{st.call?.name}</span>
                  <span className="block truncate text-[10px] text-faint">{alvo(st.call?.arguments)}</span>
                </span>
              </div>
            ))}
            <div ref={fim} />
          </>
        )}
      </div>
      {props.tarefa?.result && <RodapeWorker r={props.tarefa.result} />}
    </div>
  );
}

function RodapeWorker(props: { r: NonNullable<MaestroTask["result"]> }) {
  return (
    <div className="shrink-0 border-t border-line px-2 py-1.5 text-[10px] text-faint">
      <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        {props.r.model && <span className="truncate">{props.r.model}</span>}
        {props.r.tokens != null && (
          <span className="flex items-center gap-0.5">
            <Tokens className="size-3" />
            {props.r.tokens}
          </span>
        )}
        {props.r.seconds != null && (
          <span className="flex items-center gap-0.5">
            <Clock className="size-3" />
            {dur(props.r.seconds)}
          </span>
        )}
      </span>
    </div>
  );
}

/** O que a chamada está mexendo, para a linha do passo dizer algo além do nome da ferramenta. */
function alvo(args: any): string {
  if (!args || typeof args !== "object") return "";
  return String(args.path ?? args.command ?? args.url ?? args.query ?? args.pattern ?? "");
}

// ------------------------------------------------------------------ doca

function PainelModelos(props: { modelos: MaestroModels | null }) {
  const m = props.modelos;
  if (!m) return <p className="p-3 text-xs text-faint">Carregando…</p>;
  const usado = m.vram != null && m.vram_free != null ? 1 - m.vram_free / m.vram : null;
  return (
    <div className="space-y-3 overflow-y-auto p-3 text-xs">
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
      <div className="space-y-1">
        <div className="text-faint">Workers configurados</div>
        {Object.keys(m.slots).length === 0 ? (
          <p className="text-amber-400">
            Nenhum. Configure em Configurações › Subagentes — sem isso a Maestro não tem a quem delegar.
          </p>
        ) : (
          Object.entries(m.slots).map(([nivel, s]) => (
            <div key={nivel} className="flex justify-between">
              <span className="capitalize">{nivel}</span>
              <span className="truncate text-faint">{s.model}</span>
            </div>
          ))
        )}
      </div>
      <div className="text-faint">
        {m.max_workers === 1 ? "sequencial" : `paralelo (até ${m.max_workers})`} · ciclo de vida: {m.lifecycle}
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
        <span className={a.status === "completed" ? "text-emerald-400" : a.status === "running" ? "text-sky-400" : "text-red-400"}>
          {a.status === "completed" ? "✓" : a.status === "running" ? "⟳" : "✗"}
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

/** Cockpit do Maestro: a árvore de tarefas, a Maestro e o Worker lado a lado, numa grade de tiles em
 * que os painéis da barra do topo (navegador, terminal, Tarefa, Modelo...) entram em qualquer lugar.
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
import {
  TIPOS_TAREFA,
  type Approval, type Draft, type Especialidade, type MaestroBoard, type MaestroModels, type MaestroTask, type Message,
  type ModelPhase, type Stats, type SubState, type TaskAttempt, type TaskStatus, type TipoTarefa,
} from "../types";
import Confirma from "./Confirma";
import ContextRing from "./ContextRing";
import { Balanca, Check, Clock, Cube, Split, X } from "./icons";
import { Modal } from "./Modal";
import { aggregate, type TurnStats } from "./MessageView";
import ModelPicker from "./ModelPicker";
import Tiles, { type Cabeca, type RightTab, type Rotulo } from "./RightPanel";
import { abertos, abrir as abrirTile, type Grade } from "./tiles";

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
  // pelo número (TASK-001, 002…): o /board vem por prioridade, que é ordem de despacho, não de leitura
  const numero = (t: MaestroTask) => Number(t.code.replace(/\D/g, "")) || 0;
  [...tasks].sort((a, b) => numero(a) - numero(b)).forEach(coloca);
  return saida;
}

const card = "rounded-xl border border-line bg-panel";
const titulo = "px-3 py-2 font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint";

const gb = (n?: number | null) => (n == null ? "—" : `${(n / 1024 ** 3).toFixed(1)} GB`);
const dur = (s?: number | null) => (s == null ? "" : s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m${String(Math.round(s % 60)).padStart(2, "0")}`);

// ------------------------------------------------------------------ grade da tela

// Os três blocos do cockpit são tiles fixos (não fecham, não contam no limite); o resto da grade são
// os painéis da barra do topo, que aqui também tem Tarefa e Modelo · VRAM.
const COCKPIT = ["arvore", "maestro", "worker"];
const NOMES: Record<string, string> = { arvore: "Tarefas", maestro: "Maestro", worker: "Worker" };
/** Os itens da grade que só existem na Maestro: fora dela (o rascunho troca de tela sem trocar de
 * grade) eles não aparecem. */
export const SO_MAESTRO = ["arvore", "maestro", "worker", "task", "model"];
export const ABAS_MAESTRO = [
  { id: "task", label: "Tarefa", icon: <Split className="size-4" /> },
  { id: "model", label: "Modelo · VRAM", icon: <Cube className="size-4" /> },
];
// Mesma chave do cockpit antigo: o "_padrao" continua lá (e Configurações › Maestro o apaga); os
// layouts por conversa do formato antigo são lidos uma vez, na primeira abertura depois da mudança.
const LAYOUT_CHAVE = "forja.maestro.layout";
// larguras em peso (px de referência numa tela de ~1400 px): 14 · 56 · 30 como no cockpit antigo
const PADRAO: Grade<string> = {
  colunas: [{ tabs: ["arvore"], largura: 200, alturas: [1] }, { tabs: ["maestro"], largura: 780, alturas: [1] },
            { tabs: ["worker"], largura: 420, alturas: [1] }],
  recolhidos: [], fixos: COCKPIT,
};

function lerLayouts(): Record<string, any> {
  try {
    return JSON.parse(localStorage.getItem(LAYOUT_CHAVE) || "{}");
  } catch {
    return {};  // janela anônima ou storage bloqueado: segue no padrão
  }
}

/** Layout do cockpit antigo (colunas de um bloco + uma faixa) na grade: a faixa, se era um bloco do
 * cockpit, desce para baixo da Maestro; a doca, que virou Tarefa e Modelo na barra do topo, some. */
function doAntigo(l: any): Grade<string> | null {
  if (!Array.isArray(l?.colunas) || typeof l.colunas[0] !== "string") return null;
  const cols = (l.colunas as string[]).map((b, i) => ({ b, peso: Number(l.larguras?.[i]) || 30 })).filter((x) => COCKPIT.includes(x.b));
  const colunas = cols.map((x) => ({ tabs: [x.b], largura: Math.round(x.peso * 14), alturas: [1] }));
  const falta = COCKPIT.filter((b) => !cols.some((x) => x.b === b));
  const alvo = colunas.find((c) => c.tabs[0] === "maestro") ?? colunas[0];
  for (const b of falta) {
    if (alvo) {
      alvo.tabs.push(b);
      alvo.alturas.push(l.faixa === b ? Math.max(1, Number(l.dock) || 36) / 64 : 1);
    } else colunas.push({ tabs: [b], largura: 400, alturas: [1] });
  }
  return { colunas, recolhidos: (l.recolhidos ?? []).filter((b: string) => COCKPIT.includes(b)), fixos: COCKPIT };
}

/** O layout com que uma conversa sem grade da Maestro começa: o salvo como padrão, ou o original. */
function padrao(): Grade<string> {
  const p = lerLayouts()._padrao;
  if (Array.isArray(p?.colunas) && Array.isArray(p.colunas[0]?.tabs)) return { ...p, fixos: COCKPIT };
  return doAntigo(p) ?? PADRAO;
}

/** A grade da conversa com o cockpit dentro. Sem ele (conversa nova, ou salva antes da mudança), o
 * cockpit vem do layout antigo dela ou do padrão, e os painéis que já estavam abertos entram depois. */
function comCockpit(g: Grade<string>, convId: number | null): Grade<string> {
  if (COCKPIT.every((b) => abertos(g).includes(b))) return g.fixos ? g : { ...g, fixos: COCKPIT };
  const base = (convId !== null && doAntigo(lerLayouts()[String(convId)])) || padrao();
  const mesmos = abertos(base);
  return abertos(g).filter((t) => !mesmos.includes(t)).reduce((x, t) => abrirTile(x, t, 420), base);
}

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
  // A grade da tela (a mesma que a barra do topo abre e fecha, guardada por conversa pelo App) e o
  // conteúdo dos painéis dela, que é o mesmo das outras telas.
  grade: Grade<string>;
  onGrade: (g: Grade<string>) => void;
  painel: (tab: RightTab) => React.ReactNode;
  onDecide: (callId: string, aprovado: boolean) => void;
  // Intervenção humana (§27): pausar a execução e pedir algo à Maestro (reenviar uma tarefa).
  pausado: boolean;
  onPausar: (sim: boolean) => void;
  onPedir: (texto: string) => void;
  onNovaSessao: () => void;
  // "Testar" num tipo de Worker: abre o Comparar com o teste pronto daquela especialidade.
  onTestarWorker: (id: string, nome: string, spec?: { provider: string; model: string }) => void;
}) {
  const { convId, board, onBoard } = props;
  const grade = comCockpit(props.grade, convId);
  // Toda mudança de layout feita pela pessoa mostra por alguns segundos, no cabeçalho, o botão
  // discreto de "salvar como padrão" (se já não for o padrão).
  const [avisoEm, setAvisoEm] = useState(0);
  const [perguntaPadrao, setPerguntaPadrao] = useState(false);
  const mudaGrade = (g: Grade<string>) => {
    props.onGrade(g);
    setAvisoEm((n) => n + 1);  // contador: cada mudança reinicia os segundos do aviso
  };
  useEffect(() => {
    if (!avisoEm) return;
    const t = setTimeout(() => setAvisoEm(0), 8000);
    return () => clearTimeout(t);
  }, [avisoEm]);
  const mostraSalvarPadrao = (!!avisoEm || perguntaPadrao) && JSON.stringify(grade) !== JSON.stringify(padrao());
  const salvarPadrao = () => {
    try {
      localStorage.setItem(LAYOUT_CHAVE, JSON.stringify({ ...lerLayouts(), _padrao: grade }));
    } catch {
      /* sem storage: vale só nesta sessão */
    }
  };
  const modeloAberto = abertos(grade).includes("model");
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
    const puxa = () => api.get<MaestroModels>("/maestro/models").then(setModelos).catch(() => {});
    puxa();  // uma vez sempre: o painel da tarefa mostra o nome do especialista
    if (!modeloAberto) return;
    const t = setInterval(puxa, 3000);
    return () => clearInterval(t);
  }, [modeloAberto]);

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

  // Despachou tarefa nova: a coluna vai para o Worker que acabou de nascer, mesmo que a aba estivesse
  // no histórico de outra tarefa ou no Worker anterior. Clicar noutra aba depois continua valendo.
  const vistos = useRef<Set<string> | null>(null);
  useEffect(() => {
    const vivos = workers.filter((x) => x.w.status).map((x) => x.id);
    if (vistos.current === null) {
      vistos.current = new Set(vivos); // ao abrir a tela: nada é "novo"
      return;
    }
    const novo = vivos.find((id) => !vistos.current!.has(id));
    vivos.forEach((id) => vistos.current!.add(id));
    if (novo) setAbaWorker(novo);
  }, [workers]);

  function abrirTarefa(code: string) {
    setSelecionada(code);
    if (!abertos(grade).includes("task")) mudaGrade(abrirTile(grade, "task", 420));  // o contrato dela, num tile
    const vivo = workers.find((x) => x.code === code && x.w.status);
    setAbaWorker(vivo ? vivo.id : "hist");
  }

  // Conteúdo de cada item da grade: os blocos do cockpit trazem o próprio cabeçalho (com a alça e o
  // recolher que a grade manda); Tarefa e Modelo são tiles comuns; o resto são os painéis do App.
  const item = (id: string, cab: Cabeca) =>
    id === "arvore" ? (
      <Arvore board={board} selecionada={selecionada} onSelect={abrirTarefa} alca={cab.alca} acao={cab.acao} />
    ) : id === "maestro" ? (
      <ColunaMaestro conversa={props.conversa} composer={props.composer} alca={cab.alca} acao={cab.acao} />
    ) : id === "worker" ? (
      <ColunaWorker
        key={foco ?? ""}
        abas={abasWorker}
        aba={abaWorker}
        onAba={setAbaWorker}
        detalhe={detalhe}
        approvals={props.approvals}
        conversar={props.renderConversa}
        alca={cab.alca}
        acao={cab.acao}
      />
    ) : id === "model" ? (
      <div className="h-full overflow-auto">
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
          onMesmoModelo={async (sim) => {
            setModelos((m) => (m ? { ...m, workers_do_maestro: sim } : m));
            await api.put("/settings", { workers_do_maestro: sim }).catch(() => {});
          }}
          onTestar={props.onTestarWorker}
          onEspecialidade={async (id, provider, model) => {
            const lista = (modelos?.especialidades ?? []).map((e) => (e.id === id ? { ...e, provider, model } : e));
            setModelos((m) => (m ? { ...m, especialidades: lista } : m));
            await api.put("/settings", { worker_especialidades: lista }).catch(() => {});
          }}
        />
      </div>
    ) : id === "task" ? (
      <div className="h-full overflow-auto">
        <PainelTarefa
          tarefa={detalhe}
          especialidades={modelos?.especialidades ?? []}
          convId={convId}
          onAtualizada={setDetalhe}
          onPedir={props.onPedir}
        />
      </div>
    ) : (
      <div className="h-full overflow-auto">{props.painel(id as RightTab)}</div>
    );
  const rotulo = (id: string): Rotulo | undefined =>
    id === "arvore" ? { label: NOMES.arvore, resumo: board?.total ? `${board.done}/${board.total}` : undefined }
    : id === "maestro" ? { label: NOMES.maestro, ativo: props.running }
    : id === "worker" ? { label: NOMES.worker, ativo: workers.some((x) => x.w.status) }
    : id === "task" ? { label: selecionada ?? "Tarefa", icon: <Split className="size-3.5" /> }
    : id === "model" ? { label: "Modelo · VRAM", icon: <Cube className="size-3.5" /> }
    : undefined;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="px-2">
        <Cabecalho board={board} running={props.running} tarefa={emAndamento} modelPhase={props.modelPhase}
                   model={props.model} pausado={props.pausado} onPausar={props.onPausar}
                   onNovaSessao={props.convId === null ? undefined : props.onNovaSessao}
                   onSalvarLayout={mostraSalvarPadrao ? () => setPerguntaPadrao(true) : undefined} />
      </div>
      {perguntaPadrao && (
        <Modal onClose={() => setPerguntaPadrao(false)} label="Salvar layout como padrão" className="w-[min(28rem,92vw)] rounded-xl border border-line bg-surface p-5 shadow-popover">
          <h2 className="text-sm font-medium">Salvar este layout como padrão?</h2>
          <p className="mt-2 text-xs leading-relaxed text-muted">
            Conversas novas da Maestro vão começar com os blocos e os painéis nesta posição, neste tamanho e
            com os mesmos recolhidos. As conversas que já têm layout próprio continuam como estão. Para voltar
            ao original: Configurações › Maestro › Layout do cockpit.
          </p>
          <div className="mt-4 flex justify-end gap-2 text-xs">
            <button className="rounded-md px-3 py-1.5 text-muted hover:text-fg" onClick={() => setPerguntaPadrao(false)}>
              Cancelar
            </button>
            <button
              className="rounded-[9px] bg-accent px-3 py-1.5 font-medium text-accent-fg hover:brightness-110"
              onClick={() => {
                salvarPadrao();
                setPerguntaPadrao(false);
                setAvisoEm(0);
              }}
            >
              Salvar como padrão
            </button>
          </div>
        </Modal>
      )}
      <Tiles grade={grade} onGrade={mudaGrade} painel={item} rotulo={rotulo} proprio={(t) => COCKPIT.includes(t)} />
    </div>
  );
}

// ------------------------------------------------------------------ cabeçalho

/** Tempo total da conversa no relógio, do primeiro pedido até agora (rodando) ou até a última atividade
 *  (parada). A estatística da Maestro soma só o tempo dela no modelo, sem os Workers. */
function Relogio(props: { inicio: string; ultima: string | null; correndo: boolean }) {
  const [agora, setAgora] = useState(() => Date.now());
  useEffect(() => {
    if (!props.correndo) return;
    const t = setInterval(() => setAgora(Date.now()), 1000);
    return () => clearInterval(t);
  }, [props.correndo]);
  const fim = props.correndo ? agora : Date.parse(props.ultima ?? props.inicio);
  const s = Math.max(0, Math.round((fim - Date.parse(props.inicio)) / 1000));
  const h = Math.floor(s / 3600);
  const texto = h ? `${h}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`
    : `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
  return (
    <span className="flex shrink-0 items-center gap-1 text-xs text-muted tabular-nums"
          title="Tempo total da execução: do seu pedido até agora, contando Maestro e Workers">
      <Clock className="size-3.5 text-faint" />
      {texto}
    </span>
  );
}

function Cabecalho(props: {
  board: MaestroBoard | null;
  running: boolean;
  tarefa: MaestroTask | null;
  modelPhase: ModelPhase | null;
  model: string;
  pausado: boolean;
  onPausar: (sim: boolean) => void;
  onNovaSessao?: () => void;
  onSalvarLayout?: () => void;
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
      {b?.inicio && <Relogio inicio={b.inicio} ultima={b.ultima ?? null} correndo={props.running} />}
      <div className="ml-auto flex items-center gap-3 text-xs">
        {props.onSalvarLayout && (
          <button
            onClick={props.onSalvarLayout}
            title="Conversas novas começam com o layout atual"
            className="rounded-md px-2 py-0.5 text-faint hover:bg-raised hover:text-fg"
          >
            Salvar layout como padrão
          </button>
        )}
        {/* Trocar de modelo local leva minutos de VRAM indo e voltando. Sem dizer isso, o cockpit
            parece travado justamente quando está fazendo o que torna a IA local viável. */}
        {props.modelPhase ? (
          <TrocaDeModelo fase={props.modelPhase} />
        ) : props.tarefa ? (
          <span className={ESTADO[props.tarefa.status].cor}>
            {props.tarefa.code} · {ESTADO[props.tarefa.status].label}
          </span>
        ) : props.running ? (
          <span className={props.pausado ? "text-amber-400" : "text-sky-400"}>{props.pausado ? "pausado" : "pensando…"}</span>
        ) : (
          <span className="text-faint">parado</span>
        )}
        {props.onNovaSessao && !props.running && (
          <Confirma
            rotulo="Nova sessão"
            pergunta="Abrir sessão nova?"
            titulo="Conversa nova com contexto limpo e cópia das tarefas abertas; a lista continua aqui para consulta"
            className="rounded-md border border-line px-2 py-0.5 text-muted hover:bg-raised hover:text-fg"
            onSim={props.onNovaSessao}
          />
        )}
        {props.running && (
          <button
            onClick={() => props.onPausar(!props.pausado)}
            title={props.pausado ? "Retomar de onde parou" : "Termina o passo em curso (inclusive o do Worker) e espera"}
            className={`rounded-md border px-2 py-0.5 ${props.pausado
              ? "border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/10"
              : "border-line text-muted hover:bg-raised hover:text-fg"}`}
          >
            {props.pausado ? "Continuar" : "Pausar"}
          </button>
        )}
      </div>
    </div>
  );
}

function TrocaDeModelo({ fase }: { fase: ModelPhase }) {
  const texto =
    fase.phase === "unloading"
      ? `Descarregando ${fase.previous || "modelo"}…`
      : fase.phase === "clearing"
        ? "Liberando a memória…"
        : fase.phase === "restarting"
          ? `Reiniciando ${fase.model}…`
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

function Arvore(props: {
  board: MaestroBoard | null;
  selecionada: string | null;
  onSelect: (c: string) => void;
  alca?: React.HTMLAttributes<HTMLDivElement>;
  acao?: React.ReactNode;
}) {
  const b = props.board;
  return (
    <div className={`${card} flex min-h-0 flex-col`}>
      <div {...props.alca} className={`${titulo} flex items-center`}>Tarefas{props.acao}</div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {!b || !b.features.length ? (
          <p className="px-2 py-6 text-center text-xs text-faint">
            Nenhuma tarefa ainda.
            <br />
            Diga o objetivo do projeto e a Maestro monta o plano.
          </p>
        ) : (
          b.features.map((f) => (
            <div key={f.id} className={`mb-2 ${f.copiada_para ? "opacity-50" : ""}`}>
              <div className="flex items-baseline gap-1.5 px-2 py-1">
                <span className="truncate text-xs font-medium">{f.title}</span>
                {!!f.copiada_para && (
                  <span className="shrink-0 text-[10px] text-faint" title="O trabalho continua numa sessão nova; aqui é só consulta">
                    continua em outra conversa
                  </span>
                )}
                {f.status === "done" && <Check className="size-3 shrink-0 text-emerald-400" />}
                {f.status === "validating" && (
                  <span className="shrink-0 animate-pulse text-[10px] text-violet-400" title="Todas as tarefas concluíram; a Maestro está validando a entrega">
                    validando
                  </span>
                )}
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
function ColunaMaestro(props: {
  conversa: React.ReactNode;
  composer: React.ReactNode;
  alca?: React.HTMLAttributes<HTMLDivElement>;
  acao?: React.ReactNode;
}) {
  return (
    <div className={`${card} flex min-h-0 flex-col overflow-hidden`}>
      <div {...props.alca} className={`${titulo} flex items-center`}>Maestro{props.acao}</div>
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
  alca?: React.HTMLAttributes<HTMLDivElement>;
  acao?: React.ReactNode;
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
      <div {...props.alca} className="flex shrink-0 items-center gap-1 px-3 py-1.5">
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
        <div className={usado != null ? "flex" : "ml-auto flex"}>{props.acao}</div>
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

const NOME_NIVEL: Record<string, string> = { rapido: "Rápido", capaz: "Capaz", nuvem: "Nuvem (reserva)" };
// O "?" de cada linha: o que faz um modelo ser bom naquele papel e quando o Forja o usa.
const AJUDA_NIVEL: Record<string, string> = {
  rapido: "Modelo pequeno e veloz. O Forja manda para ele só texto e manutenção (documentação, ajustes simples) " +
    "quando a Maestro não escolhe; se falhar, a tarefa sobe para o capaz.",
  capaz: "O generalista: resolve qualquer tarefa de código e é o padrão quando nenhum especialista se aplica. " +
    "Bom capaz = segue o contrato à risca, roda os testes e não inventa API.",
  nuvem: "Reserva paga: só entra quando os outros não estão disponíveis ou falharam. Vazio = nunca gasta API.",
};
const AJUDA_ESPECIALIDADE: Record<string, string> = {
  logica: "Bom em lógica e back-end = acerta regra de negócio e casos de borda (valores zero, vazios, arredondamento), " +
    "valida entradas e escreve código que passa nos testes. Recebe tarefas de funcionalidade, correção e refatoração.",
  frontend: "Bom em frontend e aparência = escreve HTML/CSS/JS que funciona e fica bonito: layout responsivo, " +
    "acessibilidade (contraste, foco), consistência com o guia visual. Recebe tarefas de tela (tipo ui) e as que só " +
    "mexem em arquivos de interface.",
  testes: "Bom em testes = escreve testes a partir do comportamento esperado (não do código), cobre os casos de borda " +
    "e acha o bug que o teste expõe, sem inventar regra. Recebe tarefas do tipo test.",
  docs: "Bom em documentação = lê o material, resume sem perder o essencial e diz 'não consta' em vez de inventar. " +
    "Recebe tarefas do tipo docs (README, guias).",
};

function PainelModelos(props: {
  modelos: MaestroModels | null;
  onSlot: (nivel: string, provider: string, model: string) => void;
  onWorkers: (n: number) => void;
  onEspecialidade: (id: string, provider: string, model: string) => void;
  onMesmoModelo: (sim: boolean) => void;
  onTestar: (id: string, nome: string, spec?: { provider: string; model: string }) => void;
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
        {/* Maestro e Workers no mesmo modelo: nada é descarregado para subir o modelo de um
            especialista, e com IA local os Workers rodam em paralelo nas vagas do mesmo servidor. */}
        <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-line p-2 hover:bg-raised">
          <input type="checkbox" className="mt-0.5" checked={!!m.workers_do_maestro}
                 onChange={(e) => props.onMesmoModelo(e.target.checked)} />
          <span>
            <span className="text-fg">Workers usam o modelo da Maestro</span>
            <span className="block text-faint">
              Ninguém troca de modelo: a Maestro não é descarregada para subir o modelo de um Worker. Com IA local,
              os Workers rodam em paralelo no mesmo servidor. Os modelos abaixo ficam guardados, sem uso.
            </span>
          </span>
        </label>
        {/* Aqui e não só nas Configurações: escolher o Worker é decisão de execução, e quem está
            olhando o cockpit é quem percebe que o modelo atual não está dando conta da tarefa. */}
        <div className={`space-y-1.5 ${m.workers_do_maestro ? "pointer-events-none opacity-40" : ""}`}>
          {[
            ...(["rapido", "capaz", "nuvem"] as const).map((nivel) => ({
              id: nivel as string, nome: NOME_NIVEL[nivel], ajuda: AJUDA_NIVEL[nivel],
              spec: m.slots[nivel], mudar: (pr: string, mo: string) => props.onSlot(nivel, pr, mo),
            })),
            ...(m.especialidades ?? []).map((e) => ({
              id: e.id, nome: e.nome, ajuda: (AJUDA_ESPECIALIDADE[e.id] ?? "Especialista criado por você.") +
                (e.quando ? ` Quando usar: ${e.quando}.` : ""),
              spec: e.model ? { provider: e.provider, model: e.model } : undefined,
              mudar: (pr: string, mo: string) => props.onEspecialidade(e.id, pr, mo),
            })),
          ].map((w) => (
            <div key={w.id} className="flex items-center gap-2 [&>div]:ml-0">
              <span className="flex w-36 shrink-0 items-center gap-1 leading-tight text-faint">
                <span className="min-w-0">{w.nome}</span>
                <span title={w.ajuda} aria-label={w.ajuda}
                      className="flex size-3.5 shrink-0 cursor-help items-center justify-center rounded-full border border-line text-[9px] text-faint hover:text-fg">
                  ?
                </span>
              </span>
              {/* autoFallback desligado: slot vazio é escolha (sem reserva na nuvem = sem gasto de
                  API), e o seletor não pode gravar um modelo só porque o painel foi aberto. */}
              <ModelPicker
                provider={w.spec?.provider || ""}
                model={w.spec?.model || ""}
                autoFallback={false}
                loadLocal={false}
                minCtx={m.min_ctx_worker}
                onChange={w.mudar}
              />
              {w.spec?.model && (
                <button
                  onClick={() => w.mudar("", "")}
                  title={`Deixar ${w.nome} sem modelo`}
                  className="shrink-0 rounded p-1 text-faint hover:bg-raised hover:text-fg"
                >
                  <X className="size-3" />
                </button>
              )}
              <button
                onClick={() => props.onTestar(w.id, w.nome, w.spec)}
                title={`Comparar modelos locais num teste pronto de ${w.nome}`}
                className="ml-auto flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-faint hover:bg-raised hover:text-fg"
              >
                <Balanca className="size-3.5" />
                Testar
              </button>
            </div>
          ))}
        </div>
        {Object.keys(m.slots).length === 0 && !m.workers_do_maestro && (
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
          <option value="unload_after_task">Descarregar após a tarefa</option>
          <option value="unload_clear">Descarregar e esperar a memória voltar</option>
          <option value="restart_after_task">Reiniciar o modelo após a tarefa</option>
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

const LISTAS_CONTRATO = [
  ["relevant_files", "Arquivos relevantes"],
  ["requirements", "Requisitos"],
  ["constraints", "Restrições"],
  ["do_not", "Não faça"],
  ["acceptance_criteria", "Critérios de aceitação"],
] as const;
const ASSUMIDA = "Assumida pelo usuário: não despache esta tarefa.";

/** Contrato, tentativas e as ações humanas sobre a tarefa (§27): editar o contrato, trocar o modelo,
 * mudar o limite de tentativas, reenviar ao Worker, assumir, devolver, cancelar. Tudo passa pela mesma
 * máquina de estados da Maestro (POST /maestro/{conv}/task/{code}); reenviar é um pedido a ela, porque
 * é ela quem despacha e confere o resultado. */
/** Nome de quem faz a tarefa: nível ou especialista. */
function nomeDoSlot(slot: string | null | undefined, esp: Especialidade[]): string {
  if (!slot) return "automático";
  return esp.find((e) => e.id === slot)?.nome ?? ({ rapido: "rápido", capaz: "capaz", nuvem: "nuvem" } as Record<string, string>)[slot] ?? slot;
}

function PainelTarefa(props: {
  tarefa: MaestroTask | null;
  especialidades: Especialidade[];
  convId: number | null;
  onAtualizada: (t: MaestroTask) => void;
  onPedir: (texto: string) => void;
}) {
  const t = props.tarefa;
  const [editando, setEditando] = useState(false);
  const [erro, setErro] = useState("");
  if (!t) return <p className="p-3 text-xs text-faint">Clique numa tarefa da árvore para ver o contrato.</p>;
  const c = t.contract || {};
  const trabalhando = ATIVOS.includes(t.status) && t.status !== "queued";

  async function muda(patch: Record<string, unknown>) {
    setErro("");
    try {
      const r = await api.post<{ task: MaestroTask }>(`/maestro/${props.convId}/task/${t!.code}`, patch);
      props.onAtualizada(r.task);
      return true;
    } catch (e: any) {
      setErro(e.message);
      return false;
    }
  }

  async function reenviar() {
    // A Maestro só despacha o que está na fila: tira de falha/bloqueio antes de pedir.
    if (!["queued", "pending"].includes(t!.status) && !(await muda({ status: "queued" }))) return;
    const ultima = t!.attempts?.[t!.attempts.length - 1];
    props.onPedir(`Execute ${t!.code} de novo com run_task (reenviada por mim`
      + (ultima && ultima.status !== "completed"
        ? `; leia o erro da tentativa ${ultima.n} e mude a estratégia).`
        : ")."));
  }

  const acao = "rounded-md border border-line px-2 py-0.5 text-[11px] text-muted hover:bg-raised hover:text-fg disabled:opacity-40";
  return (
    <div className="h-full space-y-3 overflow-y-auto p-3 text-xs">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-faint">{t.code}</span>
        <span className="font-medium">{t.title}</span>
        <span className={ESTADO[t.status].cor}>{ESTADO[t.status].label}</span>
        {t.contract?.type && (
          <span className="rounded bg-raised px-1.5 text-[10px] text-muted">
            {TIPOS_TAREFA.find(([k]) => k === t.contract?.type)?.[1] ?? t.contract.type}
          </span>
        )}
        <span className="text-faint" title="Quem executa: escolha da Maestro, ou automático pelo tipo e pelos arquivos">
          · {nomeDoSlot(t.model_slot, props.especialidades)}
        </span>
        <span className="text-faint">· tentativas {t.attempt_count}/{t.max_attempts}</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <button className={acao} disabled={trabalhando} onClick={() => setEditando((v) => !v)}>
          {editando ? "Fechar edição" : "Editar contrato"}
        </button>
        <button className={acao} disabled={trabalhando || t.status === "pending"} onClick={reenviar}
                title="Volta para a fila e pede à Maestro para despachar de novo">
          Reenviar ao Worker
        </button>
        {t.status === "needs_human" || t.status === "blocked" ? (
          <button className={acao} onClick={() => muda({ status: "pending" })} title="A Maestro volta a poder despachar">
            {t.blocked_reason === ASSUMIDA ? "Devolver à Maestro" : "Desbloquear"}
          </button>
        ) : (
          t.status !== "completed" && t.status !== "cancelled" && (
            <button className={acao} disabled={trabalhando} onClick={() => muda({ status: "needs_human", reason: ASSUMIDA })}
                    title="Você faz esta tarefa; a Maestro não a despacha">
              Assumir tarefa
            </button>
          )
        )}
        {t.status !== "cancelled" && t.status !== "completed" && (
          <Confirma rotulo="Cancelar tarefa" pergunta={`Cancelar ${t.code}?`} className={`${acao} hover:text-red-300`}
                    desabilitado={trabalhando} onSim={() => void muda({ status: "cancelled" })} />
        )}
      </div>
      {erro && <p className="text-red-400">{erro}</p>}
      {editando && <EditorContrato t={t} especialidades={props.especialidades} onSalvar={async (patch) => (await muda(patch)) && setEditando(false)} />}
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
            <Tentativa key={a.n} a={a} onDesfazer={async () => {
              setErro("");
              try {
                const r = await api.post<{ task: MaestroTask; restored: string[] }>(
                  `/maestro/${props.convId}/task/${t.code}/attempt/${a.n}/rollback`, {});
                props.onAtualizada(r.task);
              } catch (e: any) {
                setErro(e.message);
              }
            }} />
          ))}
        </div>
      )}
    </div>
  );
}

function EditorContrato(props: {
  t: MaestroTask;
  especialidades: Especialidade[];
  onSalvar: (patch: Record<string, unknown>) => void;
}) {
  const c = props.t.contract || {};
  const [tipo, setTipo] = useState<TipoTarefa | "">(c.type ?? "");
  const [goal, setGoal] = useState(c.goal ?? "");
  const [context, setContext] = useState(c.context ?? "");
  const [verify, setVerify] = useState(c.verify_command ?? "");
  const [listas, setListas] = useState<Record<string, string>>(
    Object.fromEntries(LISTAS_CONTRATO.map(([k]) => [k, (c[k] ?? []).join("\n")])));
  const [slot, setSlot] = useState(props.t.model_slot ?? "");
  const [max, setMax] = useState(props.t.max_attempts);
  const campo = "w-full rounded-md border border-line bg-raised px-2 py-1 text-xs text-fg focus:border-focus focus:outline-none";
  const salvar = () =>
    props.onSalvar({
      contract: {
        ...c, type: tipo || undefined, goal, context, verify_command: verify,
        ...Object.fromEntries(LISTAS_CONTRATO.map(([k]) => [k, listas[k].split("\n").map((x) => x.trim()).filter(Boolean)])),
      },
      model_slot: slot,
      max_attempts: max,
    });
  return (
    <div className="space-y-2 rounded-lg border border-line bg-surface p-2">
      <label className="block">
        <span className="text-faint">Objetivo</span>
        <textarea rows={2} className={campo} value={goal} onChange={(e) => setGoal(e.target.value)} />
      </label>
      <label className="block">
        <span className="text-faint">Contexto</span>
        <textarea rows={2} className={campo} value={context} onChange={(e) => setContext(e.target.value)} />
      </label>
      {LISTAS_CONTRATO.map(([k, rotulo]) => (
        <label key={k} className="block">
          <span className="text-faint">{rotulo} (um por linha)</span>
          <textarea rows={2} className={`${campo} ${k === "relevant_files" ? "font-mono" : ""}`} value={listas[k]}
                    onChange={(e) => setListas((l) => ({ ...l, [k]: e.target.value }))} />
        </label>
      ))}
      <label className="block">
        <span className="text-faint">Comando de verificação</span>
        <input className={`${campo} font-mono`} value={verify} onChange={(e) => setVerify(e.target.value)} />
      </label>
      <div className="flex flex-wrap items-end gap-3">
        <label>
          <span className="block text-faint">Tipo</span>
          <select className={campo} value={tipo} onChange={(e) => setTipo(e.target.value as TipoTarefa | "")}>
            <option value="">sem tipo</option>
            {TIPOS_TAREFA.map(([k, rotulo]) => <option key={k} value={k}>{rotulo}</option>)}
          </select>
        </label>
        <label>
          <span className="block text-faint">Worker</span>
          <select className={campo} value={slot} onChange={(e) => setSlot(e.target.value)}>
            <option value="">automático (pelo tipo e arquivos)</option>
            <option value="rapido">rápido</option>
            <option value="capaz">capaz</option>
            <option value="nuvem">nuvem</option>
            {props.especialidades.map((e) => (
              <option key={e.id} value={e.id}>{e.nome}{e.model ? "" : " (sem modelo → capaz)"}</option>
            ))}
          </select>
        </label>
        <label>
          <span className="block text-faint">Máx. de tentativas</span>
          <input type="number" min={1} max={10} className={`${campo} w-20`} value={max}
                 onChange={(e) => setMax(Math.min(10, Math.max(1, Number(e.target.value) || 1)))} />
        </label>
        <button onClick={salvar}
                className="ml-auto rounded-[9px] bg-accent px-3 py-1 text-xs font-medium text-accent-fg hover:brightness-110 disabled:opacity-40"
                disabled={!goal.trim()}>
          Salvar contrato
        </button>
      </div>
    </div>
  );
}

function Tentativa({ a, onDesfazer }: { a: TaskAttempt; onDesfazer: () => void }) {
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
        <span className="truncate text-faint" title={a.worker?.rota ? `Por que este Worker: ${a.worker.rota}` : undefined}>
          {a.worker?.model ?? "—"}
        </span>
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
          {!!r?.changes?.length && a.status !== "running" && (
            <Confirma
              rotulo="Desfazer esta tentativa"
              pergunta="Desfazer? Os arquivos voltam a antes dela"
              titulo="Mudança feita depois nesses mesmos arquivos também sai; a tarefa volta para a fila"
              className="mt-1 rounded-md border border-line px-2 py-0.5 text-[11px] text-muted hover:bg-raised hover:text-red-300"
              onSim={onDesfazer}
            />
          )}
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

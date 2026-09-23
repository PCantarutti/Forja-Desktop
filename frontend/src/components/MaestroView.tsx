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
import {
  TIPOS_TAREFA,
  type Approval, type Draft, type Especialidade, type MaestroBoard, type MaestroModels, type MaestroTask, type Message,
  type ModelPhase, type Stats, type SubState, type TaskAttempt, type TaskStatus, type TipoTarefa,
} from "../types";
import Confirma from "./Confirma";
import ContextRing from "./ContextRing";
import { Check, Cube, Expandir, Recolher, Split, X } from "./icons";
import { Modal } from "./Modal";
import { aggregate, type TurnStats } from "./MessageView";
import { layoutDe, mover, type Alvo, type Bloco, type Layout } from "./layout";
import ModelPicker from "./ModelPicker";
import { TABS as ABAS_DIREITA, seloDaAba, type EstadoAbas, type RightTab } from "./RightPanel";

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

const NOMES: Record<Bloco, string> = { arvore: "Tarefas", maestro: "Maestro", worker: "Worker", doca: "Painéis" };
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

function useLayout(convId: number | null) {
  const chave = convId === null ? "_nova" : String(convId);
  const [st, setSt] = useState(() => ({ chave, layout: layoutDe(chave, lerLayouts()) }));
  // Trocou de conversa: ajusta no próprio render (padrão do React para "estado derivado de prop"),
  // sem um efeito que desenharia um quadro com o layout da conversa anterior.
  // A conversa nova ganha id no primeiro envio: o que foi ajustado antes disso vai junto com ela.
  const herda = st.chave === "_nova" && chave !== "_nova" && !lerLayouts()[chave];
  if (st.chave !== chave) setSt({ chave, layout: herda ? st.layout : layoutDe(chave, lerLayouts()) });
  const layout = st.chave === chave || herda ? st.layout : layoutDe(chave, lerLayouts());
  const setLayout = (f: (l: Layout) => Layout) => setSt((s) => ({ ...s, layout: f(s.layout) }));
  const grava = (k: string, l: Layout) => {
    try {
      localStorage.setItem(LAYOUT_CHAVE, JSON.stringify({ ...lerLayouts(), [k]: l }));
    } catch {
      /* sem storage: vale só nesta sessão */
    }
  };
  // "_nova" não é gravado: cada conversa nova parte do padrão, não do rascunho da anterior.
  const salvar = (l: Layout) => chave !== "_nova" && grava(chave, l);
  const salvarPadrao = (l: Layout) => grava("_padrao", l);
  const ehPadrao = (l: Layout) => JSON.stringify(l) === JSON.stringify(layoutDe("_nova", lerLayouts()));
  return { layout, setLayout, salvar, salvarPadrao, ehPadrao };
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

// Ordem da doca: a mesma do painel direito do chat e do agente, e no fim as duas só da Maestro.
type DocaTab = RightTab | "model" | "task";

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
  // Selos das abas (ponto verde, contadores): os mesmos do painel direito do chat e do agente.
  estadoAbas: EstadoAbas;
  // Intervenção humana (§27): pausar a execução e pedir algo à Maestro (reenviar uma tarefa).
  pausado: boolean;
  onPausar: (sim: boolean) => void;
  onPedir: (texto: string) => void;
  onNovaSessao: () => void;
}) {
  const { convId, board, onBoard } = props;
  const { layout, setLayout, salvar: gravar, salvarPadrao, ehPadrao } = useLayout(convId);
  // Toda mudança de layout feita pela pessoa mostra por alguns segundos, no cabeçalho, o botão
  // discreto de "salvar como padrão" (se já não for o padrão).
  const [avisoEm, setAvisoEm] = useState(0);
  const [perguntaPadrao, setPerguntaPadrao] = useState(false);
  const salvar = (l: Layout) => {
    gravar(l);
    setAvisoEm((n) => n + 1);  // contador: cada mudança reinicia os segundos do aviso
  };
  useEffect(() => {
    if (!avisoEm) return;
    const t = setTimeout(() => setAvisoEm(0), 8000);
    return () => clearTimeout(t);
  }, [avisoEm]);
  const mostraSalvarPadrao = (!!avisoEm || perguntaPadrao) && !ehPadrao(layout);
  function recolher(id: Bloco, sim: boolean) {
    setLayout((l) => {
      const novo = { ...l, recolhidos: sim ? [...l.recolhidos, id] : l.recolhidos.filter((b) => b !== id) };
      salvar(novo);
      return novo;
    });
  }
  const botaoRecolher = (id: Bloco) => (
    <button
      onClick={() => recolher(id, true)}
      title={`Recolher ${NOMES[id]} numa barra fina`}
      className="ml-auto shrink-0 rounded p-0.5 text-faint hover:bg-raised hover:text-fg"
    >
      <Recolher className="size-3.5" />
    </button>
  );
  const area = useRef<HTMLDivElement>(null);     // tudo abaixo do cabeçalho: colunas + doca
  const colunas = useRef<HTMLDivElement>(null);
  // O último layout, para gravar quando o arrasto termina (o handler do pointerup foi criado no
  // pointerdown e enxergaria o estado daquele instante).
  const atual = useRef(layout);
  useEffect(() => {
    atual.current = layout;
  }, [layout]);

  function arrastaColuna(i: number, e: PointerEvent) {
    // Pelos retângulos das duas colunas vizinhas: com colunas recolhidas (largura fixa) no meio, a
    // conta pela soma das partes da grade inteira erraria.
    const els = colunas.current?.querySelectorAll<HTMLElement>(":scope > [data-bloco]");
    if (!els?.[i + 1]) return;
    const esq = els[i].getBoundingClientRect(), dir = els[i + 1].getBoundingClientRect();
    setLayout((l) => {
      const larguras = [...l.larguras];
      const total = larguras.reduce((a, b) => a + b, 0);
      const par = larguras[i] + larguras[i + 1];
      const minimo = Math.min((COL_MIN / 100) * total, par / 2);
      const pos = ((e.clientX - esq.left) / (dir.right - esq.left)) * par;
      larguras[i] = Math.min(Math.max(pos, minimo), par - minimo);
      larguras[i + 1] = par - larguras[i];
      return { ...l, larguras };
    });
  }

  function arrastaDoca(e: PointerEvent) {
    const box = area.current?.getBoundingClientRect();
    if (!box) return;
    setLayout((l) => {
      const dock = ((l.faixaEmCima ? e.clientY - box.top : box.bottom - e.clientY) / box.height) * 100;
      return { ...l, dock: Math.min(Math.max(dock, DOCK_MIN), DOCK_MAX) };
    });
  }

  // Reposicionar: segurar o cabeçalho de um bloco "descola" o bloco da tela (sombra, leve aumento) e
  // ele segue o ponteiro; o indicador azul mostra onde vai cair. Solto: lateral de uma coluna insere
  // ali, o meio troca os dois de lugar, a borda de cima/baixo da área vira a faixa de largura inteira.
  // Salvo na hora, por conversa, junto com o redimensionamento.
  type Caixa = { left: number; top: number; width: number; height: number };
  const [arrastando, setArrastando] = useState<Bloco | null>(null);
  const [indicador, setIndicador] = useState<{ alvo: Alvo; caixa: Caixa } | null>(null);

  function alvoEm(id: Bloco, x: number, y: number): { alvo: Alvo; caixa: Caixa } | null {
    const a = area.current?.getBoundingClientRect();
    if (!a) return null;
    const l = atual.current;
    const achado = ((): { alvo: Alvo; caixa: Caixa } | null => {
      const borda = Math.min(40, a.height * 0.08);
      if (y < a.top + borda || y > a.bottom - borda) {
        const emCima = y < a.top + borda;
        return { alvo: { tipo: "faixa", emCima }, caixa: { left: 0, top: emCima ? 0 : a.height - 4, width: a.width, height: 4 } };
      }
      // elementsFromPoint: o bloco levantado não recebe ponteiro e a cortina do arrasto é ignorada
      const el = document.elementsFromPoint(x, y)
        .map((e) => (e as HTMLElement).closest<HTMLElement>("[data-bloco]")).find(Boolean);
      const com = el?.dataset.bloco as Bloco | undefined;
      if (!el || !com) return null;
      const r = el.getBoundingClientRect();
      const caixa = { left: r.left - a.left, top: r.top - a.top, width: r.width, height: r.height };
      const j = l.colunas.indexOf(com);
      const fx = (x - r.left) / r.width;
      if (j >= 0 && (fx < 0.25 || fx > 0.75)) {
        const depois = fx > 0.75;
        return { alvo: { tipo: "coluna", pos: j + (depois ? 1 : 0) },
                 caixa: { ...caixa, left: caixa.left + (depois ? r.width : 0) - 2, width: 4 } };
      }
      return { alvo: { tipo: "trocar", com }, caixa };
    })();
    // soltar ali não mudaria nada: sem indicador
    return achado && mover(l, id, achado.alvo) !== l ? achado : null;
  }

  /** Alça (cabeçalho) de um bloco: segurar e arrastar. Os botões dentro dela continuam clicáveis. */
  const alca = (id: Bloco): React.HTMLAttributes<HTMLDivElement> => ({
    title: "Segure e arraste para mudar este bloco de lugar",
    style: { cursor: arrastando ? "grabbing" : "grab", userSelect: "none" },
    onPointerDown: (e) => {
      if (e.button !== 0) return;
      const bloco = e.currentTarget.closest<HTMLElement>("[data-bloco]");
      if (!bloco) return;
      const x0 = e.clientX, y0 = e.clientY, estilo = bloco.style.cssText;
      let ativo = false;
      let ultimo: { alvo: Alvo; caixa: Caixa } | null = null;
      const mexe = (ev: PointerEvent) => {
        const dx = ev.clientX - x0, dy = ev.clientY - y0;
        if (!ativo) {
          if (Math.hypot(dx, dy) < 6) return;  // clique comum num botão da alça não vira arrasto
          ativo = true;
          setArrastando(id);
          document.body.style.userSelect = "none";
          bloco.style.cssText = estilo + ";position:relative;z-index:50;pointer-events:none;border-radius:12px;"
            + "opacity:.94;scale:1.025;transition:scale .15s ease-out,box-shadow .15s ease-out;"
            + "box-shadow:0 28px 60px -12px rgb(0 0 0/.65),0 0 0 1px rgb(56 189 248/.55);";
        }
        bloco.style.setProperty("translate", `${dx}px ${dy}px`);
        ultimo = alvoEm(id, ev.clientX, ev.clientY);
        setIndicador(ultimo);
      };
      const solta = () => {
        window.removeEventListener("pointermove", mexe);
        window.removeEventListener("pointerup", solta);
        window.removeEventListener("pointercancel", solta);
        if (!ativo) return;
        bloco.style.cssText = estilo;
        document.body.style.userSelect = "";
        // o clique que o navegador dispara depois do arrasto não pode trocar a aba sob o ponteiro
        const engole = (c: MouseEvent) => c.stopPropagation();
        window.addEventListener("click", engole, { capture: true, once: true });
        setTimeout(() => window.removeEventListener("click", engole, { capture: true }), 0);
        setArrastando(null);
        setIndicador(null);
        const alvo = ultimo?.alvo;
        if (alvo) {
          setLayout((l) => {
            const novo = mover(l, id, alvo);
            if (novo !== l) salvar(novo);
            return novo;
          });
        }
      };
      window.addEventListener("pointermove", mexe);
      window.addEventListener("pointerup", solta);
      window.addEventListener("pointercancel", solta);
    },
  });
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
    const puxa = () => api.get<MaestroModels>("/maestro/models").then(setModelos).catch(() => {});
    puxa();  // uma vez sempre: o painel da tarefa mostra o nome do especialista
    if (doca !== "model") return;
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

  // Conteúdo de cada bloco, para desenhar como coluna ou como faixa.
  const bloco = (id: Bloco) =>
    id === "arvore" ? (
      <Arvore board={board} selecionada={selecionada} onSelect={abrirTarefa} alca={alca("arvore")} acao={botaoRecolher("arvore")} />
    ) : id === "maestro" ? (
      <ColunaMaestro conversa={props.conversa} composer={props.composer} alca={alca("maestro")} acao={botaoRecolher("maestro")} />
    ) : id === "worker" ? (
      <ColunaWorker
        key={foco ?? ""}
        abas={abasWorker}
        aba={abaWorker}
        onAba={setAbaWorker}
        detalhe={detalhe}
        approvals={props.approvals}
        conversar={props.renderConversa}
        alca={alca("worker")}
        acao={botaoRecolher("worker")}
      />
    ) : (
      <div className={`${card} flex min-h-[120px] flex-col`}>
        <div {...alca("doca")} className="@container flex shrink-0 items-center gap-1 overflow-x-auto border-b border-line px-2 py-1">
          {([
            ...ABAS_DIREITA.map((t) => abaDireita(t.id)),
            ["task", selecionada ?? "Tarefa", <Split className="size-3.5" />],
            ["model", "Modelo · VRAM", <Cube className="size-3.5" />],
          ] as [DocaTab, string, React.ReactNode][]).map(([id, label, icone]) => (
            <button
              key={id}
              onClick={() => setDoca(id as DocaTab)}
              title={label}
              className={`flex min-w-0 flex-initial items-center gap-1.5 rounded-md px-2 py-1 text-xs @max-[40rem]:flex-1 @max-[40rem]:justify-center ${
                doca === id ? "bg-raised text-fg" : "text-faint hover:text-fg"
              }`}
            >
              <span className="flex shrink-0">{icone}</span>
              {/* Doca apertando: as abas encolhem por igual e o nome corta com "…"; abaixo de ~40rem
                  ("Nav…" já não cabe) fica só o ícone, com as abas espalhadas pela largura. O nome
                  inteiro fica no title. */}
              <span className="min-w-[3.2em] truncate @max-[40rem]:hidden">{label}</span>
              {id !== "model" && id !== "task" && (() => {
                const selo = seloDaAba(id as RightTab, props.estadoAbas);
                return selo && <span className="flex shrink-0">{selo}</span>;
              })()}
            </button>
          ))}
          {botaoRecolher("doca")}
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
              onEspecialidade={async (id, provider, model) => {
                const lista = (modelos?.especialidades ?? []).map((e) => (e.id === id ? { ...e, provider, model } : e));
                setModelos((m) => (m ? { ...m, especialidades: lista } : m));
                await api.put("/settings", { worker_especialidades: lista }).catch(() => {});
              }}
            />
          ) : (
            <PainelTarefa
              tarefa={detalhe}
              especialidades={modelos?.especialidades ?? []}
              convId={convId}
              onAtualizada={setDetalhe}
              onPedir={props.onPedir}
            />
          )}
        </div>
      </div>
    );
  const recolhido = (id: Bloco) => layout.recolhidos.includes(id);
  // Todas as colunas recolhidas: a faixa ocupa o resto da altura; faixa recolhida: as colunas ocupam.
  const colunasFechadas = layout.colunas.every(recolhido);
  const faixaFechada = !!layout.faixa && recolhido(layout.faixa);
  const barra = (id: Bloco, vertical: boolean) => (
    <BarraRecolhida
      nome={NOMES[id]}
      vertical={vertical}
      alca={alca(id)}
      resumo={id === "arvore" && board?.total ? `${board.done}/${board.total}` : undefined}
      ativo={(id === "maestro" && props.running) || (id === "worker" && workers.some((x) => x.w.status))}
      onAbrir={() => recolher(id, false)}
    />
  );
  // A faixa de largura inteira (em cima ou embaixo das colunas), com o divisor do lado das colunas.
  const divisorFaixa = !faixaFechada && !colunasFechadas && (
    <Divisor key="divisor-faixa" eixo="y" onArrasto={arrastaDoca} onFim={() => salvar(atual.current)} />
  );
  const faixa = layout.faixa && [
    ...(layout.faixaEmCima ? [] : [divisorFaixa || <div key="divisor-faixa" className="h-2 shrink-0" />]),
    <div key="faixa" data-bloco={layout.faixa}
         className={`flex flex-col ${faixaFechada ? "" : "min-h-[120px] [&>*]:min-h-0 [&>*]:flex-1"}`}
         style={{ flex: faixaFechada ? "0 0 auto" : colunasFechadas ? "1 1 0" : `${layout.dock} 1 0` }}>
      {faixaFechada ? barra(layout.faixa, false) : bloco(layout.faixa)}
    </div>,
    ...(layout.faixaEmCima ? [divisorFaixa || <div key="divisor-faixa" className="h-2 shrink-0" />] : []),
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-2">
      <Cabecalho board={board} running={props.running} tarefa={emAndamento} modelPhase={props.modelPhase}
                 model={props.model} pausado={props.pausado} onPausar={props.onPausar}
                 onNovaSessao={props.convId === null ? undefined : props.onNovaSessao}
                 onSalvarLayout={mostraSalvarPadrao ? () => setPerguntaPadrao(true) : undefined} />
      {perguntaPadrao && (
        <Modal onClose={() => setPerguntaPadrao(false)} label="Salvar layout como padrão" className="w-[min(28rem,92vw)] p-5">
          <h2 className="text-sm font-medium">Salvar este layout como padrão?</h2>
          <p className="mt-2 text-xs leading-relaxed text-muted">
            Conversas novas da Maestro vão começar com os blocos nesta posição, neste tamanho e com os
            mesmos recolhidos. As conversas que já têm layout próprio continuam como estão. Para voltar
            ao original: Configurações › Maestro › Layout do cockpit.
          </p>
          <div className="mt-4 flex justify-end gap-2 text-xs">
            <button className="rounded-md px-3 py-1.5 text-muted hover:text-fg" onClick={() => setPerguntaPadrao(false)}>
              Cancelar
            </button>
            <button
              className="rounded-full bg-fg px-3 py-1.5 font-medium text-black hover:bg-white"
              onClick={() => {
                salvarPadrao(layout);
                setPerguntaPadrao(false);
                setAvisoEm(0);
              }}
            >
              Salvar como padrão
            </button>
          </div>
        </Modal>
      )}

      <div ref={area} className="relative flex min-h-0 flex-1 flex-col">
        {layout.faixa && layout.faixaEmCima && faixa}
        <div
          ref={colunas}
          className="grid min-h-0"
          style={{
            flex: colunasFechadas && layout.faixa && !faixaFechada ? "0 0 auto"
              : layout.faixa && !faixaFechada ? `${100 - layout.dock} 1 0` : "1 1 0",
            gridTemplateColumns: layout.colunas
              .map((id, i) => (recolhido(id) ? "2rem" : `minmax(0,${layout.larguras[i]}fr)`)).join(" auto "),
          }}
        >
          {layout.colunas.flatMap((id, i) => [
            // divisor só entre duas colunas abertas; ao lado de uma recolhida fica só o espaço
            ...(i > 0 ? [recolhido(id) || recolhido(layout.colunas[i - 1])
              ? <div key={`d${i}`} className="w-2" />
              : <Divisor key={`d${i}`} eixo="x" onArrasto={(e) => arrastaColuna(i - 1, e)} onFim={() => salvar(atual.current)} />] : []),
            <div key={id} data-bloco={id} className="flex min-h-0 min-w-0 flex-col [&>*]:min-h-0 [&>*]:flex-1">
              {recolhido(id) ? barra(id, true) : bloco(id)}
            </div>,
          ])}
        </div>
        {layout.faixa && !layout.faixaEmCima && faixa}

        {arrastando && (
          <>
            {/* cortina: segura o ponteiro durante o arrasto (inclusive sobre a view nativa do
                navegador, que se esconde quando algo cobre o painel) */}
            <div className="fixed inset-0 z-40 cursor-grabbing" />
            {indicador && (
              <div
                className={`pointer-events-none absolute z-[60] rounded-xl transition-all duration-100 ${
                  indicador.alvo.tipo === "trocar" ? "bg-sky-500/10 ring-2 ring-sky-500/70" : "bg-sky-500"
                }`}
                style={indicador.caixa}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Bloco recolhido: barra fina com o nome (em pé, se for coluna). Clique abre; segurar arrasta. */
function BarraRecolhida(props: {
  nome: string;
  vertical: boolean;
  alca: React.HTMLAttributes<HTMLDivElement>;
  resumo?: string;
  ativo?: boolean;
  onAbrir: () => void;
}) {
  return (
    <div
      {...props.alca}
      onClick={props.onAbrir}
      title={`${props.nome} — clique para abrir, segure para mudar de lugar`}
      className={`${card} group flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-faint hover:bg-raised hover:text-fg ${
        props.vertical ? "h-full flex-col py-2" : "h-8 px-3"
      }`}
    >
      <Expandir className="size-3.5 shrink-0" />
      {props.ativo && <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-sky-400" />}
      <span className={props.vertical ? "[writing-mode:vertical-rl]" : ""}>{props.nome}</span>
      {props.resumo && <span className={`normal-case text-muted ${props.vertical ? "[writing-mode:vertical-rl]" : ""}`}>{props.resumo}</span>}
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
        {!b || !b.total ? (
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

function PainelModelos(props: {
  modelos: MaestroModels | null;
  onSlot: (nivel: string, provider: string, model: string) => void;
  onWorkers: (n: number) => void;
  onEspecialidade: (id: string, provider: string, model: string) => void;
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
        {(m.especialidades ?? []).map((e) => (
          <div key={e.id} className="flex items-center gap-2 [&>div]:ml-0" title={e.quando}>
            <span className="w-14 shrink-0 truncate text-faint">{e.nome}</span>
            <ModelPicker
              provider={e.provider}
              model={e.model}
              autoFallback={false}
              loadLocal={false}
              minCtx={m.min_ctx_worker}
              onChange={(provider, model) => props.onEspecialidade(e.id, provider, model)}
            />
            {e.model && (
              <button
                onClick={() => props.onEspecialidade(e.id, "", "")}
                title={`Deixar ${e.nome} sem modelo (as tarefas dele vão para o capaz)`}
                className="shrink-0 rounded p-1 text-faint hover:bg-raised hover:text-fg"
              >
                <X className="size-3" />
              </button>
            )}
          </div>
        ))}
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
  const campo = "w-full rounded-md border border-line bg-raised px-2 py-1 text-xs text-fg focus:border-[#555] focus:outline-none";
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
                className="ml-auto rounded-full bg-fg px-3 py-1 text-xs font-medium text-black hover:bg-white disabled:opacity-40"
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

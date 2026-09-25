import { useEffect, useMemo, useRef, useState } from "react";
import { api, streamSSE, uploadFile } from "./api";
import { useStickyBottom } from "./useStickyBottom";
import Sidebar from "./components/Sidebar";
import BrowserPanel from "./components/BrowserPanel";
import ServersPanel from "./components/ServersPanel";
import LocalPanel, { LocalLoading } from "./components/LocalPanel";
import ImagensView from "./components/ImagensView";
import VideoView from "./components/VideoView";
import CompararView from "./components/CompararView";
import PesquisaView from "./components/PesquisaView";
import PlansPanel, { type PlanEntry } from "./components/PlansPanel";
import ChangesPanel, { type ChangesAction } from "./components/ChangesPanel";
import TerminalPanel from "./components/TerminalPanel";
import InfoPanel, { type McpStatus, type ToolInfo } from "./components/InfoPanel";
import Tiles, { RightTabsBar, WIDTH, type RightTab } from "./components/RightPanel";
import { CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt, enviarClasse, pararClasse, redondo } from "./components/Composer";
import { GRADE_VAZIA, abertos, abrir as abrirTile, fechar as fecharTile, soltos, type Grade } from "./components/tiles";
import { executarNoTerminal } from "./components/TerminalPanel";
import SettingsDialog from "./components/Settings";
import FolderPicker, { folderName } from "./components/FolderPicker";
import ModelPicker from "./components/ModelPicker";
import ContextRing from "./components/ContextRing";
import GoalStrip from "./components/GoalStrip";
import Trajetoria from "./components/Trajetoria";
import TodosBar from "./components/TodosBar";
import Confirma from "./components/Confirma";
import { Modal } from "./components/Modal";
import { LogoMark } from "./components/Logo";
import {
  EffortMenu,
  ModeWarning,
  nextPermission,
  PermissionMenu,
  SectionTabs,
  type Permission,
  type Section,
} from "./components/Controls";
import {
  Attachments,
  CopyButton,
  EventNotice,
  Reconectando,
  TENTATIVA,
  NOTA_DO_AGENTE,
  Markdown,
  setFileConv,
  ActivityGroup,
  groupActivity,
  PlanCard,
  QuestionCard,
  askQuestions,
  StatsRow,
  SubagentSteps,
  Thinking,
  ToolDraft,
  ToolBlock,
  TasksCard,
  aggregate,
  resultadosDe,
  turnosDe,
  type TurnStats,
} from "./components/MessageView";
import { ArrowUp, ChevronDown, Edit, ExternalLink, FolderOpen, Globe, Laptop, Paperclip, Refresh, Square, Undo, X } from "./components/icons";
import type { Activity, Approval, Attachment, BrowserState, Conversation, Draft, MaestroBoard, Message, ModelPhase, Settings, Skill, Stats, SubState, Task, ToolCall, ToolsSent } from "./types";
import MaestroView, { ABAS_MAESTRO, SO_MAESTRO } from "./components/MaestroView";

/** Notificação do sistema quando o Forja não está em foco (execução terminou, aprovação pendente).
 * "Sem foco", não "minimizada": com a janela só atrás de outro programa, document.hidden é falso e o
 * aviso não saía. Clicar traz a janela para a frente e, com `abrir`, abre a conversa. */
function notify(title: string, body: string, force = false, abrir?: () => void) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (document.hasFocus() && !force) return;
  try {
    const n = new Notification(title, { body: body.slice(0, 160), silent: false });
    n.onclick = () => {
      (window as any).forja?.focus?.();
      window.focus();
      abrir?.();
      n.close();
    };
  } catch {
    /* navegador sem suporte */
  }
}

type Config = {
  providers: { id: string; name: string }[];
  num_ctx: number;
  default_workspace?: string;
  workspace_padrao?: string | null;  // pasta de conversa nova de Agente/Maestro (Configurações); null = escolher
  min_ctx_maestro?: number;  // janela mínima de modelo local para a Maestro (o seletor barra abaixo)
  maestro_model?: { provider: string; model: string };  // modelo padrão da Maestro (Configurações)
};
type Live = {
  messages: Message[];
  run: {
    run_id: string;
    cursor: number;
    draft: Draft | null;
    sent: ToolsSent | null;
    approvals: { call: { id: string; name: string; arguments?: any }; preview: any; suggest?: string; nota?: string | null; parent?: string }[];
    /** Geração em curso, em segundos decorridos — para remontar o contador de t/s ao reabrir. */
    geracao: { segundos: number; segundos_gerando: number; tokens: number } | null;
    paused?: boolean;
  } | null;
};

function loadSettings(): Settings {
  const def: Settings = { provider: "ollama", model: "", permission: "manual", effort: "medio" };
  try {
    return { ...def, ...JSON.parse(localStorage.getItem("forja.settings") ?? "{}") };
  } catch {
    return def;
  }
}

const COMPOSER_MAX = 420; // altura máxima do campo de mensagem; a partir daí o texto rola por dentro

// Fila de um: serializa quem lê o estado do servidor, mexe nele e grava de volta. Duas dessas em
// paralelo leem a mesma coisa e a segunda salva por cima da primeira.
let filaSettings: Promise<unknown> = Promise.resolve();
function enfileirar<T>(fn: () => Promise<T>): Promise<T> {
  const proxima = filaSettings.then(fn, fn);
  filaSettings = proxima.catch(() => {});
  return proxima;
}

/** Tiles abertos à direita, em colunas (ver components/tiles.ts). */
// string e não RightTab: na Maestro a grade também tem os blocos do cockpit, a Tarefa e o Modelo.
type RightState = Grade<string>;

/** Nova conversa começa sem tiles; cada conversa lembra os dela, no lugar e no tamanho. */
const RIGHT_DEFAULT: RightState = GRADE_VAZIA;
const RIGHT_KEY = "forja.right.byConv";

/** Largura inicial do tile (as abas da Maestro, Tarefa e Modelo, não estão em WIDTH). */
const larguraDe = (t: string) => WIDTH[t as RightTab] ?? 420;

function loadRightMap(): Record<string, RightState> {
  try {
    const map = JSON.parse(localStorage.getItem(RIGHT_KEY) ?? "{}");
    // formatos anteriores: { tab, collapsed } (uma aba só) e { abertos } (lista)
    for (const [k, v] of Object.entries<any>(map))
      if (!Array.isArray(v?.colunas)) {
        const lista: string[] = Array.isArray(v?.abertos) ? v.abertos : v?.collapsed === false && v.tab ? [v.tab] : [];
        map[k] = lista.reduce<RightState>((g, t) => abrirTile(g, t, larguraDe(t)), GRADE_VAZIA);
      }
    // Aba que deixou de existir (a Trajetória virou alternância do chat) sai do que ficou salvo.
    for (const [k, v] of Object.entries<RightState>(map))
      map[k] = abertos(v).filter((t) => !(t in WIDTH)).reduce((g, t) => fecharTile(g, t), v);
    return map;
  } catch {
    return {};
  }
}

function saveRight(convId: number, state: RightState) {
  const map = loadRightMap();
  map[String(convId)] = state;
  localStorage.setItem(RIGHT_KEY, JSON.stringify(map));
}


/** Frase da espera por ferramenta: "Buscando X" diz mais que "Usando web_search". */
const TOOL_TAIL = 4000; // cauda dos argumentos guardada na tela; o resto já rolou para fora

/** Caminho do arquivo dentro do JSON ainda pela metade, para o cabeçalho do bloco ao vivo. */
const alvoDaChamada = (bruto: string) => /"(?:path|file|caminho)"\s*:\s*"([^"]+)"/.exec(bruto)?.[1];

/** Texto de um argumento, numa linha s\u00f3 e curto o bastante para caber na frase. */
function trecho(v: unknown, max = 52): string | undefined {
  if (typeof v !== "string") return undefined;
  const limpo = v.trim().replace(/\s+/g, " ");
  if (!limpo) return undefined;
  return limpo.length > max ? limpo.slice(0, max - 1) + "\u2026" : limpo;
}

/** S\u00f3 o nome do arquivo: o caminho inteiro estoura a linha e o que importa \u00e9 QUAL arquivo \u00e9.
 *  O nome sai ANTES do corte \u2014 cortando primeiro, `.../src/App.tsx` virava "Ap\u2026". */
function arquivo(v: unknown): string | undefined {
  if (typeof v !== "string") return undefined;
  return trecho(v.split(/[\\/]/).filter(Boolean).pop(), 40);
}

/** Host e a \u00faltima parte do caminho: "localhost" sozinho n\u00e3o diz qual p\u00e1gina o agente abriu. */
function endereco(v: unknown): string | undefined {
  if (typeof v !== "string") return undefined;
  try {
    const u = new URL(v);
    const host = u.host.replace(/^www\./, "");
    const folha = u.pathname.split("/").filter(Boolean).pop();
    return trecho(folha ? `${host}/${folha}` : host, 44);
  } catch {
    return trecho(v, 40);
  }
}

/**
 * O que cada ferramenta est\u00e1 fazendo, com o alvo concreto: "Lendo config.py", n\u00e3o "Usando read_file".
 *
 * Toda ferramenta registrada tem uma entrada aqui. O que sobrar \u2014 ferramenta de servidor MCP, ou uma
 * nova que ainda n\u00e3o passou por aqui \u2014 cai no "Usando <nome>" de quem chama.
 */
const FASE: Record<string, (a: Record<string, unknown>) => string | undefined> = {
  // arquivos
  read_file: (a) => `Lendo ${arquivo(a.path) ?? "um arquivo"}${a.ocr ? " com OCR" : ""}`,
  write_file: (a) => `Escrevendo ${arquivo(a.path) ?? "um arquivo"}`,
  edit_file: (a) => `Editando ${arquivo(a.path) ?? "um arquivo"}`,
  list_dir: (a) => `Listando ${trecho(a.path, 40) ?? "a pasta"}`,
  list_agents: () => "Conferindo os subagentes",
  lsp: (a) => `Consultando o language server (${String(a.operation ?? "")})`,
  session_search: (a) => `Procurando em conversas anteriores ${trecho(a.query, 30) ?? ""}`.trim(),
  session_read: (a) => `Lendo a conversa ${String(a.id ?? "")}`.trim(),
  terminal_open: (a) => `Abrindo um terminal ${trecho(a.name, 20) ?? ""}`.trim(),
  terminal_send: (a) => `No terminal: ${trecho(a.command) ?? "enviando"}`,
  terminal_read: () => "Lendo o terminal",
  terminal_close: () => "Fechando um terminal",
  terminal_list: () => "Conferindo os terminais",
  workflow: (a) => `Orquestrando ${Array.isArray(a.phases) ? a.phases.length : ""} fases de subagentes`.replace("  ", " "),
  create_goal: () => "Definindo o objetivo",
  get_goal: () => "Conferindo o objetivo",
  update_goal: (a) =>
    ({ complete: "Marcando o objetivo como completo", blocked: "Relatando bloqueio do objetivo",
       resume: "Retomando o objetivo", pause: "Pausando o objetivo" } as Record<string, string>)[String(a.action)] ??
    "Atualizando o objetivo",
  interrupt_agent: (a) => `Parando o subagente ${trecho(a.id, 20) ?? ""}`.trim(),
  skill: (a) =>`Carregando a skill ${trecho(a.name, 30) ?? ""}`.trim(),
  glob: (a) =>`Procurando arquivos ${trecho(a.pattern, 36) ?? ""}`.trim(),
  grep: (a) => (trecho(a.pattern, 36) ? `Procurando “${trecho(a.pattern, 36)}”` : "Procurando no código"),

  // shell e servidores
  run_command: (a) =>
    a.background
      ? `Subindo \u201c${trecho(a.name, 24) ?? trecho(a.command, 32) ?? "um processo"}\u201d em segundo plano`
      : `Rodando ${trecho(a.command) ?? "um comando"}`,
  serve_start: (a) => `Subindo o servidor ${trecho(a.name, 24) ?? ""}`.trim(),
  serve_status: (a) => `Conferindo o servidor ${trecho(a.name, 24) ?? ""}`.trim(),
  serve_stop: (a) => `Parando o servidor ${trecho(a.name, 24) ?? ""}`.trim(),

  // web
  web_search: (a) => (trecho(a.query) ? `Buscando \u201c${trecho(a.query)}\u201d` : "Buscando na web"),
  fetch_url: (a) => `Lendo ${endereco(a.url) ?? "uma p\u00e1gina"}`,

  // navegador
  browser_navigate: (a) => `Abrindo ${endereco(a.url) ?? "uma p\u00e1gina"}`,
  browser_read: () => "Lendo a p\u00e1gina",
  browser_validate: (a) => `Validando ${endereco(a.url) ?? "a p\u00e1gina"}`,
  browser_click: (a) => `Clicando em ${trecho(a.selector, 36) ?? "um elemento"}`,
  browser_type: (a) => `Digitando em ${trecho(a.selector, 30) ?? "um campo"}`,
  browser_upload: (a) => `Enviando ${arquivo(a.path) ?? "um arquivo"} para a p\u00e1gina`,
  browser_screenshot: (a) => (a.selector ? `Fotografando ${trecho(a.selector, 30)}` : "Tirando um print da tela"),
  browser_scroll: (a) =>
    a.selector
      ? `Trazendo ${trecho(a.selector, 30)} para a tela`
      : a.para === "topo"
        ? "Voltando ao topo da p\u00e1gina"
        : a.para === "fim"
          ? "Indo para o fim da p\u00e1gina"
          : a.para === "cima"
            ? "Subindo uma tela"
            : "Descendo uma tela",
  browser_console: () => "Lendo o console da p\u00e1gina",
  browser_eval: () => "Rodando JavaScript na p\u00e1gina",
  browser_tabs: (a) =>
    a.action === "close" ? "Fechando uma aba" : a.action === "new" ? "Abrindo uma aba" : "Trocando de aba",

  // documentos
  preview_document: (a) => `Gerando a pr\u00e9via de ${arquivo(a.path) ?? "um documento"}`,
  write_document: (a) => `Gerando ${arquivo(a.path) ?? "um documento"}`,
  edit_document: (a) => `Alterando ${arquivo(a.path) ?? "um documento"}`,
  write_spreadsheet: (a) => `Gerando a planilha ${arquivo(a.path) ?? ""}`.trim(),
  edit_spreadsheet: (a) => `Alterando a planilha ${arquivo(a.path) ?? ""}`.trim(),

  // mem\u00f3ria, plano e delega\u00e7\u00e3o
  remember: (a) => `Guardando na mem\u00f3ria: ${trecho(a.description, 40) ?? trecho(a.name, 30) ?? ""}`.trim(),
  recall: (a) => `Consultando a mem\u00f3ria ${trecho(a.name, 30) ?? ""}`.trim(),
  forget: (a) => `Apagando a mem\u00f3ria ${trecho(a.name, 30) ?? ""}`.trim(),
  update_tasks: () => "Atualizando a lista de tarefas",
  image_generate: (a) => `Gerando a imagem “${trecho(a.prompt, 40) ?? "pedida"}”`,
  video_generate: (a) => `Gerando o vídeo “${trecho(a.prompt, 40) ?? "pedido"}”`,
  imagens_pendentes: (a) => `Registrando ${Array.isArray(a.slots) ? a.slots.length : ""} slots de imagem`,
  delegate_task: (a) => `Delegando: ${trecho(a.task, 44) ?? "uma tarefa"}`,
  exit_plan_mode: () => "Montando o plano",
  // Maestro: sem frase aqui, a linha de status viraria "Usando run_task" e esconderia o alvo.
  plan_feature: (a) => `Planejando ${trecho(a.title, 34) ?? "a funcionalidade"}`,
  list_tasks: () => "Conferindo as tarefas",
  update_task: (a) => `Atualizando ${a.code ?? "a tarefa"}${a.status ? ` para ${a.status}` : ""}`,
  run_task: (a) => `Despachando ${a.code ?? "a tarefa"} para um Worker`,
  session_note: () => "Registrando onde a sessão parou",
  ask_user: () => "Preparando perguntas para voc\u00ea",
};

export default function App() {
  const [config, setConfig] = useState<Config>({ providers: [], num_ctx: 32768 });
  const [showSettings, setShowSettings] = useState(false);
  const [allTools, setAllTools] = useState<ToolInfo[]>([]);
  const [mcp, setMcp] = useState<McpStatus | null>(null);
  const [geral, setSettings] = useState<Settings>(loadSettings);
  const [catalogKey, setCatalogKey] = useState(0); // força o seletor de modelo a recarregar
  const composer = useRef<HTMLTextAreaElement | null>(null);
  const [showFolder, setShowFolder] = useState(false);
  // Chat e Agente são seções separadas (como no Claude): cada uma lista só as suas conversas.
  const [section, setSection] = useState<Section>(() => (localStorage.getItem("forja.section") as Section) || "agent");
  // Na seção Maestro o modelo é o dela (Configurações › Maestro), não o do chat: trocar um não troca o
  // outro. Sem modelo da Maestro definido, ela usa o do chat até alguém escolher um no seletor dela.
  const modeloMaestro = section === "maestro" && config.maestro_model?.model ? config.maestro_model : null;
  const settings: Settings = modeloMaestro ? { ...geral, ...modeloMaestro } : geral;
  const [pausado, setPausado] = useState(false);
  const [sidebarHidden, setSidebarHidden] = useState(() => localStorage.getItem("forja.sidebar") === "hidden");
  const [nativeError, setNativeError] = useState("");
  const [picking, setPicking] = useState(false); // diálogo nativo aberto no sistema
  // Pasta escolhida antes de a conversa existir (tela inicial); vira a pasta da conversa no 1º envio.
  // Pasta da conversa nova (Agente/Maestro). Começa na pasta padrão das Configurações, se houver; sem ela, o
  // envio é barrado até escolher (a última pasta usada não é mais herdada).
  const [pendingWs, setPendingWs] = useState<string | null>(null);
  const [subSteps, setSubSteps] = useState<Record<string, SubState>>({});
  const [board, setBoard] = useState<MaestroBoard | null>(null);  // árvore de tarefas do Maestro
  // Troca de modelo local em curso. Carregar um GGUF leva minutos: sem isto o cockpit parece travado.
  const [modelPhase, setModelPhase] = useState<ModelPhase | null>(null);
  const [checkpoints, setCheckpoints] = useState<Record<string, string[]>>({});
  const [toolMode, setToolMode] = useState("auto");
  const [vision, setVision] = useState("auto");
  const [right, setRight] = useState<RightState>(RIGHT_DEFAULT);
  // A grade desta tela: fora da Maestro, sem os blocos e as abas que são só dela.
  const gradeTela = section === "maestro" ? right : SO_MAESTRO.reduce((g, t) => fecharTile({ ...g, fixos: [] }, t), right);
  // Abre o tile (já aberto fica onde está; com o máximo aberto, não abre).
  const abrir = (tab: RightTab) => setRight((r) => abrirTile(r, tab, larguraDe(tab)));
  const [activity, setActivity] = useState<Activity>({ conversations: [], servers: 0 });
  const prevConv = useRef<number | null | undefined>(undefined);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [serversRunning, setServersRunning] = useState(0);
  const [localRunning, setLocalRunning] = useState(false);

  /** O provedor "IA local" só tem modelo quando o llama-server está de pé: a lista recarrega na hora,
   *  e quem acabou de carregar um modelo quer conversar com ele — então o chat já muda para ele.
   *  A comparação é num ref: efeito dentro de um updater de estado o React pode rodar duas vezes. */
  const localAntes = useRef(false);
  function onLocalRunning(running: boolean, alias: string) {
    if (localAntes.current === running) return;
    localAntes.current = running;
    setLocalRunning(running);
    setCatalogKey((k) => k + 1);
    // No chat, carregar um modelo no painel é escolher com ele. No Maestro quem carrega é o
    // orquestrador, para o Worker da vez — trocar a seleção aqui fazia a Maestro herdar o modelo do
    // Worker sem ninguém pedir. Lá o modelo da Maestro só muda pelo seletor do composer.
    if (running && alias && secaoRef.current !== "maestro") setSettings((s) => ({ ...s, provider: "local", model: alias }));
  }
  const [liveOutput, setLiveOutput] = useState<Record<string, string>>({}); // saída ao vivo por chamada (run_command)
  const [liveTasks, setLiveTasks] = useState<Task[] | null>(null); // lista de tarefas do run atual
  // Alternância Chat | Trajetória: vale só para a página em que foi escolhida. Guardar a chave (seção +
  // conversa) em vez de "trajetoria" solto faz qualquer navegação voltar ao Chat sem efeito nenhum.
  const [trajetoriaEm, setTrajetoriaEm] = useState<string | null>(null);
  const [queued, setQueued] = useState<string[]>([]); // mensagens na fila (enviadas durante a execução)
  const [unread, setUnread] = useState<Set<number>>(new Set()); // conversas que terminaram em segundo plano
  const [changesKey, setChangesKey] = useState(0); // muda quando um turno termina: aba Alterações recarrega
  const [changesCount, setChangesCount] = useState(0);
  const [changesAction, setChangesAction] = useState<ChangesAction>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [slashIndex, setSlashIndex] = useState(0);
  const [mentionHits, setMentionHits] = useState<string[]>([]);
  const [mentionIndex, setMentionIndex] = useState(0);
  const conversationsRef = useRef<Conversation[]>([]);
  // Estatísticas em tempo real da geração atual: tokens contados conforme chegam, relógio a cada 250 ms.
  const liveGen = useRef<{ t0: number; tFirst: number | null; tokens: number } | null>(null);
  const [tick, setTick] = useState(0);

  // Badge da aba Instâncias: quantos servidores do agente estão vivos (a aba em si atualiza mais rápido).
  useEffect(() => {
    const load = () =>
      api
        .get<{ servers: { alive: boolean }[] }>("/servers")
        .then((r) => setServersRunning(r.servers.filter((s) => s.alive).length))
        .catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setTick((x) => x + 1), 250); // relógio da linha de estatísticas ao vivo
    return () => clearInterval(t);
  }, [running]);
  const [approvals, setApprovals] = useState<Record<string, Approval>>({});
  const [sent, setSent] = useState<ToolsSent | null>(null);
  const [ctx, setCtx] = useState<{ used: number; max: number | null; estimated: boolean } | null>(null);
  const [error, setError] = useState("");
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [rewindAsk, setRewindAsk] = useState<
    { conv: number; messageId: number; keep: boolean; content: string | null; files: string[] } | null
  >(null);
  const [uploading, setUploading] = useState(false);
  const runId = useRef<string | null>(null);
  const streamCtl = useRef<AbortController | null>(null);
  // Só acompanha o fim da conversa enquanto o usuário estiver no fim: se ele subir, a rolagem fica onde está.
  const { ref: scroller, fim: fimDoChat, onScroll: seguirFim, colar } = useStickyBottom<HTMLDivElement>([messages, draft, approvals]);

  const update = (p: Partial<Settings>) => {
    if (section === "maestro" && (p.provider !== undefined || p.model !== undefined)) {
      const novo = { provider: p.provider ?? settings.provider, model: p.model ?? settings.model };
      setConfig((c) => ({ ...c, maestro_model: novo }));
      api.put("/settings", { maestro_model: novo }).catch((e) => setError(e.message));
      const { provider: _p, model: _m, ...resto } = p;
      p = resto;
    }
    setSettings((s) => ({ ...s, ...p }));
  };

  /** Pausar: o passo em curso termina (inclusive o do Worker) e o próximo espera o Continuar. */
  function pausar(sim: boolean) {
    if (!runId.current) return;
    setPausado(sim);
    api.post(`/runs/${runId.current}/pause`, { paused: sim }).catch((e) => setError(e.message));
  }

  /** Trocar o modo no meio da resposta vale já para a próxima ferramenta (e libera o card aberto). */
  function changePermission(permission: Permission) {
    update({ permission });
    if (running && runId.current) {
      api.post(`/runs/${runId.current}/permission`, { permission }).catch((e) => setError(e.message));
    }
  }

  useEffect(() => {
    localStorage.setItem("forja.settings", JSON.stringify(geral));
  }, [geral]);

  useEffect(() => {
    localStorage.setItem("forja.section", section);
    refreshConversations(section);
  }, [section]);

  useEffect(() => {
    localStorage.setItem("forja.sidebar", sidebarHidden ? "hidden" : "visible");
  }, [sidebarHidden]);

  useEffect(() => {
    if (currentId === null && config.workspace_padrao !== undefined) setPendingWs((p) => p ?? config.workspace_padrao ?? null);
  }, [config.workspace_padrao]);

  // Miniaturas/anexos são servidos da pasta da conversa aberta.
  useEffect(() => setFileConv(currentId), [currentId]);

  // Ao trocar de conversa, restaura o estado da coluna direita dela. Rascunho (sem conversa) = recolhida.
  // Conversa recém-criada a partir do rascunho herda o estado atual (ex.: agente abriu o navegador no 1º turno).
  useEffect(() => {
    if (currentId === null) {
      setRight(RIGHT_DEFAULT);
    } else {
      const saved = loadRightMap()[String(currentId)];
      if (saved) setRight(saved);
      else if (prevConv.current === null) saveRight(currentId, right);
      else setRight(RIGHT_DEFAULT);
    }
    prevConv.current = currentId;
  }, [currentId]);

  useEffect(() => {
    if (currentId !== null) saveRight(currentId, right);
  }, [right, currentId]);

  // Cada conversa tem a própria sessão de navegador; "0" é o rascunho da tela inicial.
  const browserKey = currentId === null ? "0" : String(currentId);
  // Link clicado na resposta (Sources.pedeLink): pergunta se abre no navegador do Forja ou no do sistema.
  const [linkAberto, setLinkAberto] = useState<string | null>(null);
  useEffect(() => {
    const pede = (e: Event) => setLinkAberto((e as CustomEvent<string>).detail);
    window.addEventListener("forja:link", pede);
    return () => window.removeEventListener("forja:link", pede);
  }, []);
  function abreLinkNoForja(url: string) {
    setLinkAberto(null);
    setBrowserOpen(true);
    abrir("browser");
    api.post(`/browser/navigate?conv=${browserKey}`, { url }).catch((e) => setError(e.message));
  }
  useEffect(() => {
    api.get<BrowserState>(`/browser?conv=${browserKey}`).then((s) => setBrowserOpen(s.open)).catch(() => setBrowserOpen(false));
  }, [browserKey]);

  useEffect(() => {
    api.get<Config>("/config").then(setConfig).catch(() => {});
    refreshTools();
    // a lista vem do efeito de [section], que também roda na montagem
    // F5: volta para a conversa aberta e reconecta à execução, se houver.
    const saved = Number(localStorage.getItem("forja.current"));
    if (saved) openConversation(saved).catch(() => localStorage.removeItem("forja.current"));
  }, []);

  // Enquanto algum servidor MCP estiver conectando, atualiza status e ferramentas a cada 2s.
  useEffect(() => {
    if (!mcp?.servers.some((s) => s.status === "connecting")) return;
    const t = setTimeout(refreshTools, 2000);
    return () => clearTimeout(t);
  }, [mcp]);

  useEffect(() => {
    if (currentId === null) localStorage.removeItem("forja.current");
    else localStorage.setItem("forja.current", String(currentId));
  }, [currentId]);

  useEffect(() => {
    if (!settings.model) return;
    api
      .get<{ tool_mode: string; vision: string }>(`/model-settings?model=${encodeURIComponent(settings.model)}`)
      .then((r) => {
        setToolMode(r.tool_mode);
        setVision(r.vision ?? "auto");
      })
      .catch(() => {});
  }, [settings.model]);

  // O que está rodando agora (subagentes por conversa e processos vivos): bolinha na lista e chip no chat.
  useEffect(() => {
    const carrega = () =>
      api
        .get<Activity>("/activity")
        .then(setActivity)
        .catch(() => {});
    carrega();
    const t = setInterval(carrega, 4000);
    // Janela escondida tem o timer estrangulado pelo Chromium: ao voltar, confere na hora.
    window.addEventListener("focus", carrega);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", carrega);
    };
  }, []);

  // Avisos que não dependem da conversa aberta na tela: aprovação esperando (inclusive do Worker) e
  // Maestro que terminou. Vêm da atividade, que cobre todas as conversas; a aberta já avisa pelo stream.
  const atividadeAnterior = useRef<Activity | null>(null);
  const turnosVistos = useRef(new Set<string>());
  useEffect(() => {
    const antes = atividadeAnterior.current;
    atividadeAnterior.current = activity;
    if (!antes) return;
    if (activity.lista && antes.lista && activity.lista !== antes.lista) refreshConversations();
    const conv = (id: number) => conversationsRef.current.find((c) => c.id === id);
    const abrir = (id: number) => () => openConversation(id);
    const rodandoAntes = new Map(antes.conversations.filter((c) => c.running).map((c) => [c.id, c]));
    for (const c of activity.conversations) {
      const eram = rodandoAntes.get(c.id)?.waiting ?? 0;
      const alertasAntes = rodandoAntes.get(c.id)?.alertas ?? 0;
      if ((c.alertas ?? 0) > alertasAntes && c.id !== currentId)
        notify("Maestro pode estar travada", `${conv(c.id)?.title ?? "Conversa"}: confira e pare se precisar`, true, abrir(c.id));
      if ((c.waiting ?? 0) > eram && c.id !== currentId) {
        const maestro = conv(c.id)?.kind === "maestro";
        notify(maestro ? "Maestro pede aprovação" : "Forja pede aprovação",
               `${conv(c.id)?.title ?? "Conversa"}: ${c.waiting} esperando você`, true, abrir(c.id));
      }
    }
    // "IA local" com outro nome no seletor (o modelo foi trocado pelo celular ou pela API): o llama-server só
    // tem um modelo, então a resposta viria dele com o rótulo do antigo. O seletor passa a mostrar o carregado.
    if (activity.local && activity.local_alias && settings.provider === "local" && settings.model !== activity.local_alias
        && secaoRef.current !== "maestro")
      setSettings((s) => ({ ...s, model: activity.local_alias! }));
    const agora = new Set(activity.conversations.filter((c) => c.running).map((c) => c.id));
    // Turno que esta tela não disparou (o celular, ou aviso de processo em segundo plano) na conversa aberta:
    // rodando, conecta no stream dele como no F5; já terminado (durou menos que o intervalo), recarrega.
    const turno = activity.conversations.find((c) => c.id === currentId)?.run;
    if (currentId && !running && turno && turno !== runId.current && !turnosVistos.current.has(turno)) {
      turnosVistos.current.add(turno);
      if (agora.has(currentId)) openConversation(currentId);
      else {
        const id = currentId;
        api.get<Live>(`/conversations/${id}/live`).then((l) => abertaRef.current === id && setMessages(l.messages)).catch(() => {});
        refreshConversations();
      }
    }
    for (const id of rodandoAntes.keys()) {
      if (agora.has(id) || conv(id)?.kind !== "maestro") continue;
      api.get<MaestroBoard>(`/maestro/${id}/board`).then((b) => {
        const humano = b.counts?.needs_human ?? 0;
        notify("Maestro terminou",
               `${conv(id)?.title ?? "Maestro"}: ${b.done}/${b.total} tarefas concluídas`
               + (humano ? ` · ${humano} precisa(m) de você` : ""), true, abrir(id));
      }).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activity]);

  // Abrir outra conversa volta a colar no fim.
  useEffect(() => {
    colar();
  }, [currentId, colar]);

  // Trocar de seção dispara uma busca nova; a anterior pode chegar depois e repor a lista errada
  // (era o que fazia a aba de pesquisa abrir com as conversas do chat até trocar de página).
  const pedidoConversas = useRef(0);
  const pedidoAbertura = useRef(0);  // idem para abrir conversa: dois cliques rápidos na lista
  // A seção de agora, legível de dentro de função assíncrona antiga (closure não vê o estado novo).
  const secaoRef = useRef(section);
  const secaoEscolhida = useRef(false);  // true depois que a pessoa clica numa aba
  secaoRef.current = section;
  // A conversa aberta, para a lista não esconder a dela enquanto ainda está vazia (só com anexo).
  const abertaRef = useRef<number | null>(null);
  abertaRef.current = currentId;

  function refreshConversations(kind: Section = section) {
    const meu = ++pedidoConversas.current;
    api
      .get<Conversation[]>(`/conversations?kind=${kind}${abertaRef.current ? `&keep=${abertaRef.current}` : ""}`)
      .then((list) => {
        if (meu !== pedidoConversas.current) return;  // resposta atrasada de outra seção
        conversationsRef.current = list;
        setConversations(list);
      })
      .catch((e) => meu === pedidoConversas.current && setError(e.message));
  }

  function loadCheckpoints(id: number | null) {
    if (id === null) return setCheckpoints({});
    api.get<Record<string, string[]>>(`/conversations/${id}/checkpoints`).then(setCheckpoints).catch(() => {});
  }

  function loadChangesCount(id: number | null) {
    if (id === null) return setChangesCount(0);
    api
      .get<{ files: { status: string }[] }>(`/conversations/${id}/changes`)
      .then((r) => setChangesCount(r.files.filter((f) => f.status !== "unchanged").length))
      .catch(() => {});
  }

  // Comandos `/`: ações do Forja + skills da pasta da conversa.
  useEffect(() => {
    api
      .get<{ skills: Skill[] }>(`/conversations/${currentId ?? 0}/skills`)
      .then((r) => setSkills(r.skills))
      .catch(() => setSkills([]));
  }, [currentId]);

  /** Abre um arquivo/pasta da conversa no editor ou no Explorer do seu sistema. */
  function openPath(path: string, mode: "editor" | "reveal") {
    api.post("/open", { conv: currentId ?? "0", path, mode }).catch((e) => setError(e.message));
  }

  /** Conversa que ficou rodando em segundo plano: avisa quando terminar (badge + notificação). */
  function watchUntilDone(id: number) {
    const tick = async () => {
      const live = await api.get<Live>(`/conversations/${id}/live`).catch(() => null);
      if (!live) return;
      if (live.run) return void setTimeout(tick, 5000);
      setUnread((u) => new Set(u).add(id));
      // Maestro tem aviso próprio (vigia da atividade, com o resumo das tarefas)
      if (conversationsRef.current.find((c) => c.id === id)?.kind !== "maestro")
        notify("Forja terminou", conversationsRef.current.find((c) => c.id === id)?.title ?? "Conversa em segundo plano", true);
      refreshConversations();
    };
    setTimeout(tick, 4000);
  }

  async function compactNow() {
    if (currentId === null || running) return;
    setStatus("Compactando contexto…");
    try {
      const m = await api.post<Message>(`/conversations/${currentId}/compact`, { provider: settings.provider, model: settings.model });
      setMessages((ms) => [...ms, m]);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
    setStatus(null);
  }

  async function patchConversation(id: number, patch: { title?: string; pinned?: boolean; archived?: boolean }) {
    try {
      await api.patch(`/conversations/${id}`, patch);
      refreshConversations();
    } catch (e: any) {
      setError(e.message);
    }
  }

  function refreshTools() {
    setCatalogKey((k) => k + 1);
    api.get<Config>("/config").then(setConfig).catch(() => {});
    api.get<ToolInfo[]>("/tools").then(setAllTools).catch(() => {});
    api.get<McpStatus>("/mcp").then(setMcp).catch(() => {});
  }

  async function reloadMcp() {
    setMcp((m) => m && { ...m, servers: m.servers.map((s) => ({ ...s, status: "connecting" })) });
    try {
      setMcp(await api.post<McpStatus>("/mcp/reload"));
    } catch (e: any) {
      setError(e.message);
    }
    refreshTools();
  }

  function resetLive() {
    setDraft(null);
    setStatus(null);
    setApprovals({});
    setPausado(false);
    setSubSteps({});
    setLiveOutput({});
    setLiveTasks(null);
    setQueued([]);
    liveGen.current = null;
    runId.current = null;
  }

  /** Assina o SSE de uma execução. Trocar de conversa só aborta a assinatura; a execução segue no servidor. */
  async function follow(convId: number, path: string, init?: RequestInit) {
    streamCtl.current?.abort();
    const ctl = new AbortController();
    streamCtl.current = ctl;
    setRunning(true);
    try {
      await streamSSE(path, { ...init, signal: ctl.signal }, onEvent);
    } catch (e: any) {
      if (!ctl.signal.aborted) setError(e.message);
    } finally {
      if (!ctl.signal.aborted) {
        // Recarrega do banco: a tela fica igual ao que foi persistido.
        const live = await api.get<Live>(`/conversations/${convId}/live`).catch(() => null);
        if (live) setMessages(live.messages);
        setRunning(false);
        resetLive();
        refreshConversations();
        loadCheckpoints(convId);
        loadChangesCount(convId);
        setChangesKey((k) => k + 1);
        if (conversationsRef.current.find((c) => c.id === convId)?.kind !== "maestro")
          notify("Forja terminou", conversationsRef.current.find((c) => c.id === convId)?.title ?? "Resposta pronta");
      }
    }
  }

  async function openConversation(id: number) {
    // Mesma guarda do refreshConversations: dois cliques rápidos na barra lateral e a resposta
    // mais lenta da conversa A chegava depois da B, sobrescrevendo as mensagens que estão na tela.
    const meu = ++pedidoAbertura.current;
    if (running && currentId !== null && currentId !== id) watchUntilDone(currentId);
    streamCtl.current?.abort();
    setRunning(false);
    resetLive();
    setCtx(null);
    setError("");
    setCurrentId(id);
    setUnread((u) => {
      if (!u.has(id)) return u;
      const n = new Set(u);
      n.delete(id);
      return n;
    });
    const live = await api.get<Live>(`/conversations/${id}/live`);
    if (meu !== pedidoAbertura.current) return;  // outra conversa foi aberta enquanto isto vinha
    setMessages(live.messages);
    loadCheckpoints(id);
    loadChangesCount(id);
    const kind = (await api.get<{ kind?: string }>(`/conversations/${id}`).catch(() => null))?.kind;
    if (meu !== pedidoAbertura.current) return;
    // Só troca de aba se a pessoa ainda não escolheu uma: na abertura do app esta busca demora e
    // chegava depois do clique, arrastando a seção (e a lista) de volta para a da conversa salva.
    if (kind && kind !== secaoRef.current && !secaoEscolhida.current) setSection(kind as Section);
    const run = live.run;
    if (run) {
      runId.current = run.run_id;
      setPausado(!!run.paused);
      // Reconstrói o cronômetro da geração em curso. Ele nasce no `assistant_start`, que já passou
      // para quem está reabrindo, e sem isto a linha de t/s voltava zerada e parada enquanto a
      // resposta continuava chegando. O servidor manda segundos decorridos, não instantes.
      const g = run.geracao;
      liveGen.current = g
        ? { t0: Date.now() - g.segundos * 1000, tokens: g.tokens,
            tFirst: g.segundos_gerando ? Date.now() - g.segundos_gerando * 1000 : null }
        : null;
      setDraft(run.draft);
      setSent(run.sent);
      setApprovals(Object.fromEntries(run.approvals.map((a) => [a.call.id, { preview: a.preview, suggest: a.suggest, nota: a.nota, tool: a.call.name }])));
      // Aprovação pedida por um subagente: recria o passo dentro do bloco da delegação.
      const subs: Record<string, SubState> = {};
      for (const a of run.approvals.filter((a) => a.parent)) {
        (subs[a.parent!] ??= { status: "aguardando aprovação", steps: [] }).steps.push({ call: a.call });
      }
      setSubSteps(subs);
      follow(id, `/runs/${run.run_id}/stream?cursor=${run.cursor}`);
    }
  }

  function newConversation() {
    if (running && currentId !== null) watchUntilDone(currentId);
    streamCtl.current?.abort();
    setRunning(false);
    resetLive();
    setCurrentId(null);
    setMessages([]);
    setCtx(null);
    setCheckpoints({});
    setChangesCount(0);
    setPendingWs(config.workspace_padrao ?? null);
  }

  async function deleteConversation(id: number) {
    await api.del(`/conversations/${id}`);
    if (id === currentId) newConversation();
    refreshConversations();
  }

  async function changeToolMode(m: string) {
    setToolMode(m);
    await api.put("/model-settings", { model: settings.model, tool_mode: m });
  }

  async function changeVision(v: string) {
    setVision(v);
    await api.put("/model-settings", { model: settings.model, vision: v });
  }

  function onEvent(ev: any) {
    if (ev.parent) {
      // Passo de subagente: fica dentro do bloco do delegate_task, não na lista de mensagens.
      const pid: string = ev.parent;
      setSubSteps((all) => {
        const cur = all[pid] ?? { status: "", steps: [] };
        if (ev.type === "tool_call") return { ...all, [pid]: { ...cur, steps: [...cur.steps, { call: ev.call }] } };
        if (ev.type === "tool_result")
          return {
            ...all,
            [pid]: { ...cur, steps: cur.steps.map((st) => (st.call.id === ev.message.tool_call_id ? { ...st, result: ev.message } : st)) },
          };
        return all;
      });
      if (ev.type === "tool_result") setApprovals(({ [ev.message.tool_call_id]: _, ...rest }) => rest);
      if (ev.type === "sub_status") setSubSteps((all) => ({ ...all, [pid]: { ...all[pid], steps: all[pid]?.steps ?? [], status: ev.text } }));
      // Conversa do Worker de contrato: a coluna Worker do Maestro desenha com o mesmo conversaDe do chat.
      setSubSteps((all) => {
        const cur = all[pid] ?? { status: "", steps: [] };
        const msgs = cur.mensagens ?? [];
        const d = cur.draft ?? null;
        const com = (x: Partial<SubState>) => ({ ...all, [pid]: { ...cur, ...x } });
        switch (ev.type) {
          case "sub_assistant_start":
            return com({ draft: { content: "", thinking: "", tool: null } });
          case "sub_token":
            return d ? com({ draft: { ...d, content: d.content + ev.text } }) : all;
          case "sub_thinking":
            return d ? com({ draft: { ...d, thinking: d.thinking + ev.text } }) : all;
          case "sub_tool_token":
            return d
              ? com({ draft: { ...d, tool: { name: ev.name || d.tool?.name || "", text: ((d.tool?.text ?? "") + ev.text).slice(-TOOL_TAIL) } } })
              : all;
          case "sub_message":
            return com({
              mensagens: [...msgs.filter((m) => m.id !== ev.message.id), ev.message],
              draft: ev.message.role === "assistant" ? null : d,
            });
          case "tool_result":  // só os resultados numerados pertencem à conversa gravada do Worker
            return ev.message.id != null ? com({ mensagens: [...msgs, ev.message] }) : all;
          default:
            return all;
        }
      });
      if (ev.type === "tool_output")  // saída ao vivo de comando do Worker, no bloco dele
        setLiveOutput((o) => ({ ...o, [ev.call_id]: ((o[ev.call_id] ?? "") + ev.text).slice(-20_000) }));
      if (ev.type === "approval_request") {
        setApprovals((a) => ({ ...a, [ev.call.id]: { preview: ev.preview, suggest: ev.suggest, nota: ev.nota, tool: ev.call.name } }));
        notify("Worker pede aprovação", `${ev.call.name}: ${String(ev.call.arguments?.command ?? ev.call.arguments?.path ?? "")}`, true);
      }
      if (ev.type === "tool_call" && typeof ev.call?.name === "string" && ev.call.name.startsWith("browser_")) {
        setBrowserOpen(true);
        abrir("browser");
      }
      return;
    }
    switch (ev.type) {
      case "run_started":
        runId.current = ev.run_id;
        break;
      case "tool_call":
        // O agente foi ao navegador: mostra a aba Navegador para o usuário acompanhar ao vivo.
        if (typeof ev.call?.name === "string" && ev.call.name.startsWith("browser_")) {
          setBrowserOpen(true);
          abrir("browser");
        }
        break;
      case "tools_sent":
        setSent(ev);
        break;
      case "status":
        setStatus(ev.text);
        break;
      case "tool_output": // saída ao vivo de um comando
        setLiveOutput((o) => ({ ...o, [ev.call_id]: ((o[ev.call_id] ?? "") + ev.text).slice(-20_000) }));
        break;
      case "tasks":
        setLiveTasks(ev.tasks);
        break;
      case "board":
        setBoard(ev.board);
        break;
      case "paused":
        setPausado(!!ev.paused);
        break;
      case "alerta":  // "a Maestro pode estar travada": o sistema não para sozinho, avisa você
        notify("Maestro pode estar travada", ev.text, true);
        break;
      case "task_update":
        // Só marca que mudou; o board inteiro vem no evento "board" ou no próximo polling.
        break;
      case "model":
        // "ready"/"unloaded" são o fim da troca: o indicador some em vez de ficar preso na tela.
        setModelPhase(ev.phase === "ready" || ev.phase === "unloaded" || ev.phase === "cleared" ? null : (ev as ModelPhase));
        break;
      case "title": // o modelo resumiu um título melhor no fim do turno
        refreshConversations();
        setStatus(null);
        break;
      case "queued":
        setQueued((q) => (q.includes(ev.content) ? q : [...q, ev.content]));
        break;
      case "message":
        refreshConversations(); // título da conversa nova já existe no servidor
        if (ev.message?.role === "user") setQueued((q) => q.filter((t) => t !== ev.message.content)); // saiu da fila
      // fallthrough
      case "event":
      case "tool_result":
        setStatus(null);
        setMessages((ms) => [...ms, ev.message]);
        if (ev.type === "tool_result") {
          setApprovals(({ [ev.message.tool_call_id]: _, ...rest }) => rest);
          setLiveOutput(({ [ev.message.tool_call_id]: _, ...rest }) => rest);
        }
        break;
      case "assistant_start":
        setStatus(null);
        setDraft({ content: "", thinking: "", tool: null });
        liveGen.current = { t0: Date.now(), tFirst: null, tokens: 0 };
        break;
      case "token":
      case "thinking": {
        const g = liveGen.current;
        if (g) {
          g.tFirst ??= Date.now();
          g.tokens += 1; // provedores locais mandam um chunk por token
        }
        if (ev.type === "token") setDraft((d) => d && { ...d, content: d.content + ev.text });
        else setDraft((d) => d && { ...d, thinking: d.thinking + ev.text });
        break;
      }
      case "tool_token": {
        const g = liveGen.current;
        if (g) {
          g.tFirst ??= Date.now();
          g.tokens += 1; // o contador também parava aqui: o turno parecia morto no meio da escrita
        }
        setDraft((d) => {
          if (!d) return d;
          const texto = (d.tool?.text ?? "") + ev.text;
          return {
            ...d,
            tool: {
              name: ev.name || d.tool?.name || "",
              path: d.tool?.path ?? alvoDaChamada(texto),
              text: texto.slice(-TOOL_TAIL),
              chars: (d.tool?.chars ?? 0) + ev.text.length,
            },
          };
        });
        break;
      }
      case "assistant_end":
        setDraft(null);
        liveGen.current = null; // a partir daqui valem as estatísticas reais da mensagem (meta.stats)
        setMessages((ms) => [...ms, ev.message]);
        break;
      case "approval_request":
        setApprovals((a) => ({ ...a, [ev.call.id]: { preview: ev.preview, suggest: ev.suggest, nota: ev.nota, tool: ev.call.name } }));
        notify("Forja pede aprovação", `${ev.call.name}: ${String(ev.call.arguments?.command ?? ev.call.arguments?.path ?? "")}`, true);
        break;
      case "plan_request":
        setApprovals((a) => ({ ...a, [ev.call.id]: { preview: null, tool: "exit_plan_mode", plan: ev.plan } }));
        notify("Forja propôs um plano", "Abra a conversa para aprovar ou pedir ajustes.", true);
        break;
      case "question_request":
        setApprovals((a) => ({ ...a, [ev.call.id]: { preview: null, tool: "ask_user", questions: ev.questions } }));
        notify("Forja tem uma pergunta", String(ev.question ?? ""), true);
        break;
      case "context":
        setCtx(ev);
        break;
    }
  }

  /** Conversa atual; cria na hora (com a pasta escolhida) se ainda estiver na tela inicial. */
  async function ensureConversation(): Promise<number> {
    if (currentId !== null) return currentId;
    const c = await api.post<Conversation>("/conversations", { workspace: pendingWs, kind: section });
    abertaRef.current = c.id;
    setCurrentId(c.id);
    setMessages([]);
    refreshConversations();
    return c.id;
  }

  // O campo cresce com o texto até COMPOSER_MAX e, daí em diante, rola por dentro (como o Claude Desktop).
  // Contar "\n" não serve: uma linha longa quebra na tela e continuaria de duas linhas de altura.
  useEffect(() => {
    const el = composer.current;
    if (!el) return;
    el.style.height = "auto";
    const teto = Math.min(COMPOSER_MAX, Math.round(window.innerHeight * 0.45));
    el.style.height = `${Math.min(el.scrollHeight, teto)}px`;
    el.style.overflowY = el.scrollHeight > teto ? "auto" : "hidden";
  }, [input, attachments.length]);

  /** Seletor de pasta do sistema (Explorer no Windows), pelo Electron. Fora do app, o seletor interno. */
  async function chooseFolder() {
    const start = (currentId !== null ? conv?.workspace : pendingWs) ?? config.default_workspace ?? "";
    setNativeError("");
    if (!window.forja) {
      setShowFolder(true);
      return;
    }
    setShowFolder(false);
    setPicking(true);
    try {
      const path = await window.forja.pickFolder(start);
      if (path) await pickFolder(path);
    } catch (e: any) {
      setNativeError(String(e.message));
      setShowFolder(true);
    } finally {
      setPicking(false);
    }
  }

  async function pickFolder(path: string | null) {
    setShowFolder(false);
    setError("");
    if (currentId === null) return setPendingWs(path);
    try {
      await api.put(`/conversations/${currentId}/workspace`, { workspace: path });
      refreshConversations();
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function undoTurn(turnId: number) {
    if (currentId === null) return;
    try {
      await api.post(`/conversations/${currentId}/checkpoints/restore`, { turn_id: turnId });
    } catch (e: any) {
      setError(e.message);
    }
    loadCheckpoints(currentId);
  }

  async function addFiles(files: FileList | File[]) {
    // Copia ANTES de qualquer await. O FileList do input é vivo: o `value = ""` do onChange — que
    // existe para deixar escolher o mesmo arquivo de novo — roda assim que esta função cede no
    // primeiro await e esvazia a lista, então o laço não via arquivo nenhum e nada era anexado,
    // sem erro nenhum na tela. O dataTransfer do arrastar tem o mesmo prazo de validade.
    const lista = Array.from(files);
    if (!lista.length) return;
    if (semPasta) return setError("Escolha uma pasta de trabalho antes de anexar: o anexo vai para dentro dela.");
    setUploading(true);
    const conv = await ensureConversation().catch((e) => {
      setError(e.message);
      return null;
    });
    for (const f of lista) {
      if (conv === null) break;
      try {
        const att = await uploadFile(f, conv);
        setAttachments((list) => [...list, att]);
      } catch (e: any) {
        setError(`${f.name}: ${e.message}`);
      }
    }
    setUploading(false);
  }

  /** Reenvia a partir de uma mensagem: apaga o que vem depois e roda de novo. */
  async function rewindAndRun(messageId: number, keep: boolean, content: string | null, restore?: boolean) {
    if (currentId === null || running) return;
    setError("");
    setRewindAsk(null);
    // Turnos que vão sumir e alteraram arquivos: pergunta na tela (confirm() não funciona no
    // Electron, ver Confirma.tsx) se desfaz os arquivos também, e só roda depois da resposta.
    const changed = Object.entries(checkpoints)
      .filter(([turn]) => Number(turn) >= messageId)
      .flatMap(([, files]) => files);
    if (changed.length && restore === undefined)
      return setRewindAsk({ conv: currentId, messageId, keep, content, files: [...new Set(changed)] });
    const restore_files = !!restore;
    try {
      const r = await api.post<{ messages: Message[] }>(`/conversations/${currentId}/rewind`, {
        message_id: messageId,
        keep,
        restore_files,
      });
      loadCheckpoints(currentId);
      setMessages(r.messages);
    } catch (e: any) {
      setError(e.message);
      return;
    }
    await follow(currentId, `/conversations/${currentId}/run`, {
      method: "POST",
      body: JSON.stringify({
        content,
        provider: settings.provider,
        model: settings.model,
        permission: section === "agent" || section === "maestro" ? settings.permission : "manual",
        effort: section === "maestro" && settings.effort === "extremo" ? "maximo" : settings.effort,
      }),
    });
  }

  // Menu `/`: aparece quando o campo começa com "/" e ainda é uma linha só.
  const slashQuery = input.startsWith("/") && !input.startsWith("/skill:") && !input.includes("\n") ? input.slice(1).split(" ")[0].toLowerCase() : null;
  // `/skill:nome` em qualquer ponto do texto (várias por mensagem): só skills de prompt; o menu completa o nome.
  const inlineQuery = /(?:^|\s)\/skill:([\w.-]*)$/.exec(input)?.[1]?.toLowerCase() ?? null;
  const slashMatches = inlineQuery !== null
    ? skills.filter((s) => s.kind === "prompt" && s.name.toLowerCase().startsWith(inlineQuery))
    : slashQuery === null ? [] : skills.filter((s) => s.name.toLowerCase().startsWith(slashQuery));
  const menuSkill = slashQuery !== null || inlineQuery !== null;

  // Menu `@`: caminhos da pasta da conversa, enquanto o @ é a última coisa digitada.
  const mentionQuery = /(?:^|\s)@(\S*)$/.exec(input)?.[1] ?? null;

  useEffect(() => {
    if (mentionQuery === null) return setMentionHits([]);
    const t = setTimeout(() => {
      api
        .get<{ files: string[] }>(
          `/workspace/files?conv=${currentId ?? 0}&q=${encodeURIComponent(mentionQuery)}&limit=8`,
        )
        .then((r) => {
          setMentionHits(r.files);
          setMentionIndex(0);
        })
        .catch(() => setMentionHits([]));
    }, 200); // digitar rápido não dispara uma varredura por tecla
    return () => clearTimeout(t);
  }, [mentionQuery, currentId]);

  /** Escolher no menu troca o `@trecho` pelo caminho: o agente lê o arquivo se precisar. Conversa entra
   *  como `@conversa:ID`, que o backend resolve (sessoes.mencionadas). */
  function applyMention(path: string) {
    setInput((v) => v.replace(/@\S*$/, `${path} `));
    setMentionHits([]);
    // Clicar no menu tira o foco do campo; sem isto o próximo texto digitado ia para o começo.
    requestAnimationFrame(() => {
      const t = composer.current;
      if (!t) return;
      t.focus();
      t.selectionStart = t.selectionEnd = t.value.length;
    });
  }
  // Conversas citáveis por @: título que casa com o que foi digitado depois do @ (da mesma seção).
  const conversasCitaveis =
    mentionQuery && mentionQuery.length >= 2
      ? conversations
          .filter((c) => c.id !== currentId && c.title.toLowerCase().includes(mentionQuery.toLowerCase()))
          .slice(0, 4)
      : [];

  async function applySkill(s: Skill): Promise<void> {
    if (inlineQuery !== null) {
      setInput((v) => v.replace(/\/skill:[\w.-]*$/, `/skill:${s.name} `));
      setSlashIndex(0);
      requestAnimationFrame(() => composer.current?.focus());
      return;
    }
    const args = input.slice(1).split(" ").slice(1).join(" ");
    setInput("");
    setSlashIndex(0);
    if (s.kind === "prompt") {
      // Vai como o usuário escreveu; o backend anexa a skill inteira para o modelo (skills.invocada).
      return send(`/${s.name} ${args}`.trim(), true);
    }
    if (s.action === "compact") return compactNow();
    if (s.action === "commit" || s.action === "pr") {
      setChangesAction(s.action);
      abrir("changes");
      return;
    }
    if (s.action === "changes") abrir("changes");
  }

  async function send(texto?: string, skill = false): Promise<void> {
    const content = (texto ?? input).trim();
    if (!skill && menuSkill && slashMatches.length) return applySkill(slashMatches[slashIndex] ?? slashMatches[0]);
    if (!content && !attachments.length) return;
    colar();  // mandar mensagem é dizer "quero ver o que vem agora": volta para o fim da conversa
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission().catch(() => {});
    if (running) {
      // Execução em andamento: a mensagem entra na fila e o agente a recebe no próximo passo.
      if (!runId.current || !content) return;
      setInput("");
      setQueued((q) => [...q, content]);
      await api.post(`/runs/${runId.current}/queue`, { content }).catch((e) => {
        setQueued((q) => q.filter((t) => t !== content));
        setError(e.message);
      });
      return;
    }
    if (!settings.model) {
      setError("Escolha um modelo primeiro.");
      return;
    }
    if (semPasta) {
      setError("Escolha uma pasta de trabalho antes de enviar (no seletor de pasta, no topo).");
      return;
    }
    setError("");
    setInput("");
    const files = attachments;
    setAttachments([]);
    let id: number;
    try {
      id = await ensureConversation();
    } catch (e: any) {
      setError(e.message);
      return;
    }
    await follow(id, `/conversations/${id}/run`, {
      method: "POST",
      body: JSON.stringify({
        content,
        provider: settings.provider,
        model: settings.model,
        permission: section === "agent" || section === "maestro" ? settings.permission : "manual",
        effort: section === "maestro" && settings.effort === "extremo" ? "maximo" : settings.effort,
        attachments: files,
      }),
    });
  }

  async function stop() {
    if (runId.current) await api.post(`/runs/${runId.current}/stop`).catch(() => {});
  }

  async function decide(callId: string, approved: boolean, alwaysAllow?: boolean) {
    if (!runId.current) return;
    const rule = approvals[callId]?.suggest;
    if (alwaysAllow && rule) {
      // Cria a regra em Configurações › Permissões antes de aprovar.
      // Só o run_command tem regra por comando; as demais (inclusive browser_eval) são por nome de ferramenta.
      const field = approvals[callId]?.tool === "run_command" ? "auto_approve_commands" : "auto_approve_tools";
      // Ler-e-escrever em fila: dois "sempre permitir" seguidos liam a mesma lista e o segundo
      // salvava por cima, perdendo a regra do primeiro sem avisar ninguém.
      await enfileirar(async () => {
        const s = await api.get<any>("/settings");
        const atuais: string[] = s[field] ?? [];
        if (atuais.includes(rule)) return;
        await api.put("/settings", { [field]: [...atuais, rule] });
      }).catch((e) => setError(e.message));
    }
    setApprovals((a) => ({ ...a, [callId]: { ...a[callId], sent: true } })); // evita clique duplo
    await api.post(`/runs/${runId.current}/approve`, { call_id: callId, approved }).catch((e) => setError(e.message));
  }

  /** Respostas de um ask_user (uma por pergunta): voltam para o agente como resultado da ferramenta. */
  async function decideAnswer(callId: string, answers: string[]) {
    if (!runId.current) return;
    setApprovals((a) => ({ ...a, [callId]: { ...a[callId], sent: true } }));
    await api
      .post(`/runs/${runId.current}/approve`, { call_id: callId, approved: true, answers })
      .catch((e) => setError(e.message));
  }

  async function decidePlan(callId: string, approved: boolean, mode?: string, feedback?: string) {
    if (!runId.current) return;
    setApprovals((a) => ({ ...a, [callId]: { ...a[callId], sent: true } }));
    await api
      .post(`/runs/${runId.current}/approve`, { call_id: callId, approved, mode, feedback })
      .catch((e) => setError(e.message));
  }

  const results = useMemo(() => resultadosDe(messages), [messages]);

  const segments = useMemo(() => groupActivity(messages), [messages]);
  const paginaAtual = `${section}:${currentId ?? "nova"}`;
  // Mudou de página (outra conversa, outra seção): a escolha é esquecida, e voltar abre no Chat.
  const [paginaVista, setPaginaVista] = useState(paginaAtual);
  if (paginaVista !== paginaAtual) {
    setPaginaVista(paginaAtual);
    setTrajetoriaEm(null);
  }
  const vista: "chat" | "trajetoria" = trajetoriaEm === paginaAtual ? "trajetoria" : "chat";
  const setVista = (v: "chat" | "trajetoria") => setTrajetoriaEm(v === "trajetoria" ? paginaAtual : null);
  // Tarefas da barra acima do campo: a do turno em andamento, ou a última que a conversa registrou.
  const tarefasAtuais = useMemo<Task[]>(() => {
    if (running && liveTasks) return liveTasks;
    const ultima = [...messages].reverse().find((m) => m.role === "event" && m.meta?.kind === "tasks");
    return (ultima?.meta?.tasks as Task[] | undefined) ?? [];
  }, [messages, running, liveTasks]);

  // Instâncias desta conversa (delegações) + processos vivos, para o indicador embaixo da resposta.
  const daConversa = activity.conversations.find((c) => c.id === currentId);
  const instancias = (daConversa?.subagents ?? 0) + (daConversa?.servers ?? 0);
  const plural = (n: number, um: string, varios: string) => (n ? `${n} ${n > 1 ? varios : um}` : "");
  const rotuloInstancias = [
    plural(daConversa?.subagents ?? 0, "agente em segundo plano", "agentes em segundo plano"),
    plural(daConversa?.servers ?? 0, "processo rodando", "processos rodando"),
  ].filter(Boolean).join(" · ");

  // Planos do modo Plano nesta conversa (chamadas exit_plan_mode), para a aba Planos.
  const plans = useMemo<PlanEntry[]>(() => {
    const out: PlanEntry[] = [];
    for (const m of messages) {
      for (const c of m.tool_calls ?? []) {
        if (c.name !== "exit_plan_mode") continue;
        const done = results.get(c.id);
        const plan = (approvals[c.id]?.plan ?? done?.meta?.plan ?? c.arguments.plan ?? "") as string;
        const status: PlanEntry["status"] = !done
          ? "pendente"
          : done.status === "ok"
            ? "aprovado"
            : done.status === "rejeitada"
              ? "ajustes"
              : "cancelado";
        out.push({ callId: c.id, plan, status, mode: done?.meta?.approved_mode, order: out.length + 1 });
      }
    }
    return out;
  }, [messages, results, approvals]);

  // Estatísticas por turno (todas as iterações do agente até a próxima mensagem do usuário),
  // exibidas embaixo da última resposta do turno.
  const turns = useMemo(() => turnosDe(messages), [messages]);

  // Linha acima do input: contexto atual, saída do último turno e média de t/s da conversa.
  const summary = useMemo(() => {
    const all: Stats[] = messages.flatMap((m) => (m.role === "assistant" && m.meta?.stats ? [m.meta.stats] : []));
    const lastStats = all[all.length - 1];
    const lastTurn = [...turns.values()].pop()?.stats;
    const avg = all.length ? aggregate(all).tps : null;
    // Contexto ocupado após a última resposta = prompt + saída (é o que entra na próxima requisição).
    const used = lastStats ? lastStats.prompt_tokens + lastStats.tokens : (ctx?.used ?? null);
    const max = ctx?.max ?? lastStats?.ctx_max ?? null;
    const models = [...new Set(all.map((s) => s.model).filter(Boolean))];
    // Painel de sessão como o do dsh: turnos, passos, tokens somados e acerto de cache do servidor.
    const comCache = all.filter((s) => s.cached != null);
    const promptComCache = comCache.reduce((n, s) => n + s.prompt_tokens, 0);
    const sessao = {
      turnos: messages.filter((m) => m.role === "user").length,
      passos: all.length,
      tokens: all.reduce((n, s) => n + s.prompt_tokens + s.tokens, 0),
      cache: promptComCache ? comCache.reduce((n, s) => n + (s.cached ?? 0), 0) / promptComCache : null,
    };
    const partes = (ctx as { partes?: Stats["partes"] } | null)?.partes ?? lastStats?.partes ?? null;
    return { used, max, out: lastTurn?.tokens ?? null, avg, models, sessao, partes };
  }, [messages, turns, ctx]);

  /** Abre uma conversa de outra seção (ex.: Imagens ⇄ o chat que pediu as imagens). */
  function irParaConversa(id: number, kind: Section) {
    secaoEscolhida.current = true;
    if (kind !== section) setSection(kind);
    openConversation(id);
    refreshConversations(kind);
  }

  function changeSection(next: Section) {
    secaoEscolhida.current = true;
    if (next === section) return;
    newConversation();
    setSection(next);
  }

  const conv = conversations.find((c) => c.id === currentId);
  // Conversa aberta sem pasta = das antigas, que rodam na raiz interna; a nova sem pasta ainda não pode enviar.
  const wsLabel = conv ? conv.workspace ?? config.default_workspace ?? "pasta padrão" : pendingWs;

  // O turno atual ainda está rodando: não mostra estatísticas dele até terminar.
  const lastUserIndex = messages.map((m) => m.role).lastIndexOf("user");

  // Linha de estatísticas sempre presente enquanto roda: iterações já concluídas do turno (valores reais do
  // provider) + a geração em andamento (tokens contados ao vivo, tempo correndo, t/s atual).
  const liveStats: TurnStats | null = (() => {
    void tick; // recalcula a cada 250 ms
    if (!running) return null;
    const done = messages.slice(lastUserIndex + 1).flatMap((m) => (m.role === "assistant" && m.meta?.stats ? [m.meta.stats as Stats] : []));
    const base: TurnStats = done.length ? aggregate(done) : { model: settings.model, tokens: 0, seconds: 0, tps: null, estimated: true };
    const g = liveGen.current;
    if (!g) return { ...base, model: settings.model || base.model, estimated: true };
    const now = Date.now();
    const gen = g.tFirst ? (now - g.tFirst) / 1000 : 0;
    return {
      model: settings.model || base.model,
      tokens: base.tokens + g.tokens,
      seconds: base.seconds + (now - g.t0) / 1000,
      tps: gen > 0.3 ? g.tokens / gen : base.tps,
      estimated: true,
    };
  })();

  /** O que o agente está fazendo agora, em uma frase — o que o usuário lê enquanto espera.
   *
   *  A frase nomeia o ALVO, não a ferramenta: "Lendo config.py", "Rodando npm test". Quando várias
   *  chamadas correm juntas, a primeira aparece e o resto vira contagem — senão a linha some e fica
   *  parecendo que o agente empacou, quando na verdade são cinco leituras em paralelo.
   */
  const fase = (() => {
    void tick; // acompanha o cronômetro
    if (!running) return "";
    const chamadas = messages.slice(lastUserIndex + 1).flatMap((m) => m.tool_calls ?? []);
    const descreve = (c: ToolCall) => FASE[c.name]?.(c.arguments) ?? `Usando ${c.name}`;

    const esperando = Object.keys(approvals).filter((id) => !results.has(id));
    if (esperando.length) {
      // Dizer O QUE está esperando aprovação: "Esperando você decidir" não dizia se era um comando
      // de shell, uma escrita em arquivo ou um plano — e é isso que muda a resposta da pessoa.
      const alvo = chamadas.find((c) => c.id === esperando[0]);
      const oque = alvo ? descreve(alvo) : approvals[esperando[0]]?.tool;
      const mais = esperando.length > 1 ? ` (+${esperando.length - 1} na fila)` : "";
      return oque ? `Esperando você aprovar: ${oque.toLowerCase()}${mais}` : `Esperando você decidir${mais}`;
    }

    const rodando = chamadas.filter((c) => !results.has(c.id));
    if (rodando.length) {
      const extra = rodando.length > 1 ? ` (+${rodando.length - 1} em paralelo)` : "";
      return descreve(rodando[0]) + extra;
    }

    if (draft?.tool) {
      // O rascunho da chamada: a espera mais longa do turno num write_file grande. Mostrar o quanto
      // já saiu é o que diferencia "escrevendo um arquivo enorme" de "travou".
      const kb = (draft.tool.chars ?? draft.tool.text.length) / 1024;
      const alvo = arquivo(draft.tool.path) ?? (draft.tool.name ? `a chamada de ${draft.tool.name}` : "a chamada");
      return `Escrevendo ${alvo}${kb >= 1 ? ` · ${kb.toFixed(1)} KB` : ""}`;
    }
    if (draft?.content) return "Escrevendo a resposta";
    if (draft?.thinking) return (liveStats?.seconds ?? 0) > 30 ? "Ainda pensando…" : "Pensando…";
    if (sent) {
      // "Esperando o modelo responder" não dizia o que ele está digerindo — e a espera mais longa
      // de todas é justamente a imagem, que num servidor local leva minutos enquanto o texto leva
      // segundos. Dizer qual é faz a diferença entre "está trabalhando" e "travou".
      const ultima = [...messages].reverse().find((m) => m.role === "tool" && !!m.meta);
      const anexos = (ultima?.meta?.attachments ?? []) as Attachment[];
      const imagens = anexos.filter((a) => a.kind === "image").length;
      if (imagens && ultima?.meta?.model_sees !== false) {
        return imagens > 1 ? `Olhando ${imagens} imagens` : "Olhando a imagem";
      }
      const lido = (ultima?.content ?? "").length;
      if (lido > 4000) return `Lendo o resultado de ${ultima?.name ?? "uma ferramenta"} · ${Math.round(lido / 1024)} KB`;
      return "Esperando o modelo responder";
    }
    return status ?? ""; // sem sinal de atividade, nada de spinner girando à toa
  })();

  // Uso por modelo (a conversa pode trocar de modelo no meio).
  const usage = useMemo(() => {
    const by = new Map<string, Stats[]>();
    for (const m of messages) {
      const st: Stats | undefined = m.role === "assistant" ? m.meta?.stats : undefined;
      if (st) by.set(st.model, [...(by.get(st.model) ?? []), st]);
    }
    return [...by].map(([model, list]) => ({ ...aggregate(list), model }));
  }, [messages]);

  // O mesmo composer serve o chat, o agente e o cockpit do Maestro: modos, esforço, anexos, `/` e
  // `@`, anel de contexto e seletor de modelo ficam idênticos porque são literalmente o mesmo bloco.
  // Agente e Maestro agem numa pasta de trabalho: os dois têm seletor de pasta, modos de permissão,
  // aviso de modo e Shift+Tab. Uma condição só, para os dois não divergirem de novo.
  const agentica = section === "agent" || section === "maestro";
  const semPasta = agentica && currentId === null && !pendingWs;
  // A conversa desenhada como no chat. Função de uma lista de mensagens, e não bloco fixo, porque o
  // cockpit do Maestro desenha a do Worker com ela também: o mesmo "Raciocinou ›", os mesmos blocos
  // de ferramenta com diff, a mesma linha de tokens e t/s — igual por construção, não por imitação.
  // `readonly` (Worker): sem editar, desfazer nem regenerar, que são ações da conversa do usuário.
  const conversaDe = (
    msgs: Message[],
    o: { draft?: Draft | null; status?: string | null; stats?: TurnStats | null; fase?: string; vivo?: boolean; readonly?: boolean } = {},
  ) => {
    const proprio = msgs === messages;
    const res = proprio ? results : resultadosDe(msgs);
    const seg = proprio ? segments : groupActivity(msgs);
    const tur = proprio ? turns : turnosDe(msgs);
    const lu = msgs.map((m) => m.role).lastIndexOf("user");
    const la = msgs.reduce((acc, m, i) => (m.role === "assistant" ? i : acc), -1);
    const vivo = o.vivo ?? running;
    const so = !!o.readonly;
    return (
      <>
            {msgs.map((m, i) => {
              if (m.role === "user")
                return (
                  <div key={m.id} className="group my-6 flex flex-col items-end">
                    {!so && editing?.id === m.id ? (
                      <div className="w-full rounded-3xl border border-line bg-surface p-3">
                        <textarea
                          autoFocus
                          rows={Math.min(10, editing.text.split("\n").length + 1)}
                          value={editing.text}
                          onChange={(e) => setEditing({ id: m.id, text: e.target.value })}
                          className="w-full resize-none bg-transparent text-[15px] text-fg focus:outline-none"
                        />
                        <div className="mt-2 flex justify-end gap-2">
                          <button onClick={() => setEditing(null)} className="rounded-full border border-line px-4 py-1.5 text-sm text-fg hover:bg-raised">
                            Cancelar
                          </button>
                          <button
                            onClick={() => {
                              const text = editing.text.trim();
                              setEditing(null);
                              if (text) rewindAndRun(m.id, false, text);
                            }}
                            className="rounded-full bg-fg px-4 py-1.5 text-sm font-medium text-black hover:bg-white"
                          >
                            Enviar de novo
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        {!!m.content && (
                          <div className="max-w-[85%] rounded-3xl bg-raised px-5 py-2.5 whitespace-pre-wrap">{m.content}</div>
                        )}
                        <Attachments list={m.meta?.attachments ?? []} />
                        {!so && <div className="mt-1 flex opacity-0 transition group-hover:opacity-100">
                          <CopyButton text={m.content} />
                          <button
                            title="Editar e enviar de novo"
                            disabled={vivo}
                            onClick={() => setEditing({ id: m.id, text: m.content })}
                            className="rounded-md p-1.5 text-faint hover:bg-raised hover:text-fg disabled:opacity-30"
                          >
                            <Edit />
                          </button>
                        </div>}
                      </>
                    )}
                  </div>
                );
              if (m.role === "event") {
                if (String(m.meta?.kind ?? "") in NOTA_DO_AGENTE || m.meta?.kind === "tasks") return null; // essas vão no bloco de atividade
                // Cada tentativa de reconexão grava um evento: na tela é um cartão só, o da tentativa atual. Some
                // quando conecta (vem outra mensagem); se esgotar, fica o erro final, que já diz o motivo.
                const tentativa = TENTATIVA.exec(m.content ?? "");
                if (tentativa) return i === messages.length - 1 ? <Reconectando key={m.id} texto={m.content ?? ""} n={tentativa[1]} /> : null;
                return <EventNotice key={m.id} m={m} />;
              }
              if (m.role !== "assistant") return null;
              const turn = tur.get(i);
              const showTurn = turn && !(vivo && i > lu);
              const toolNode = (c: ToolCall, queued: boolean) => (
                <ToolBlock
                  call={c}
                  result={res.get(c.id)}
                  approval={approvals[c.id]}
                  running={vivo}
                  queued={queued}
                  live={liveOutput[c.id]}
                  hideImages
                  onOpen={openPath}
                  onDecide={(ok, always) => decide(c.id, ok, always)}
                >
                  {c.name === "delegate_task" && (
                    <SubagentSteps
                      info={res.get(c.id)?.meta?.sub}
                      status={subSteps[c.id]?.status}
                      steps={
                        subSteps[c.id]?.steps ??
                        (res.get(c.id)?.meta?.sub?.steps ?? []).map((st: any) => ({
                          call: { id: st.id, name: st.name, arguments: st.arguments },
                          result: { ...st, content: st.result, tool_call_id: st.id } as Message,
                        }))
                      }
                      approvals={approvals}
                      running={vivo}
                      onDecide={decide}
                    />
                  )}
                </ToolBlock>
              );
              return (
                <div key={m.id} className="my-4">
                  {(seg.get(i) ?? []).map((seg, si) =>
                    seg.kind === "text" ? (
                      <Markdown key={si} text={m.content} />
                    ) : seg.kind === "plan" ? (
                      <div key={si} id={`plan-${seg.call.id}`}>
                        <PlanCard
                          plan={(approvals[seg.call.id]?.plan ?? res.get(seg.call.id)?.meta?.plan ?? seg.call.arguments.plan ?? "") as string}
                          done={res.get(seg.call.id)}
                          onDecide={(ok, mode, feedback) => decidePlan(seg.call.id, ok, mode, feedback)}
                        />
                      </div>
                    ) : seg.kind === "question" ? (
                      <QuestionCard
                        key={si}
                        questions={approvals[seg.call.id]?.questions ?? askQuestions(seg.call.arguments)}
                        done={res.get(seg.call.id)}
                        onAnswer={(a) => decideAnswer(seg.call.id, a)}
                      />
                    ) : (
                      <ActivityGroup
                        key={si}
                        items={seg.items}
                        results={res}
                        live={vivo && i > lu}
                        forceOpen={seg.items.some((p) => p.kind === "tool" && !!approvals[p.call.id] && !res.has(p.call.id))}
                        renderTool={toolNode}
                        onOpen={openPath}
                        onGerarImagens={(p) =>
                          // a conversa de Imagens daquele pedido: a mesma a cada clique, criada no primeiro
                          api
                            .post<{ id: number; kind: Section }>(`/imagens/slots/${p.message_id}/conversa`, {})
                            .then((c) => irParaConversa(c.id, c.kind))
                            .catch((e) => setError(e.message))
                        }
                      />
                    ),
                  )}
                  {showTurn && (
                    <div className="mt-4 space-y-1.5">
                      {turn.stats && (
                        <StatsRow
                          s={turn.stats}
                          instances={i === la ? (so ? 0 : instancias) : 0}
                          instancesLabel={rotuloInstancias}
                          onInstances={() => abrir("servers")}
                        />
                      )}
                      {!so && <div className="flex items-center">
                        <CopyButton text={turn.text} />
                        {turn.userId !== null && checkpoints[String(turn.userId)] && (
                          <Confirma
                            titulo={"Arquivos alterados neste turno:\n" + checkpoints[String(turn.userId)].join("\n")}
                            desabilitado={vivo}
                            onSim={() => void undoTurn(turn.userId!)}
                            pergunta="Desfazer daqui em diante? run_command não volta"
                            className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-faint hover:bg-raised hover:text-fg disabled:opacity-30"
                            rotulo={<>
                              <Undo className="size-3.5" /> desfazer {checkpoints[String(turn.userId)].length} arquivo
                              {checkpoints[String(turn.userId)].length > 1 ? "s" : ""}
                            </>}
                          />
                        )}
                        {i > lu && lu >= 0 && (
                          <button
                            title="Gerar outra resposta"
                            disabled={vivo}
                            onClick={() => rewindAndRun(msgs[lu].id, true, null)}
                            className="rounded-md p-1.5 text-faint hover:bg-raised hover:text-fg disabled:opacity-30"
                          >
                            <Refresh />
                          </button>
                        )}
                      </div>}
                    </div>
                  )}
                </div>
              );
            })}
            {!so && rewindAsk?.conv === currentId && (
              <div className="my-4 rounded-2xl border border-line bg-surface p-3 text-sm">
                <p className="text-amber-300">
                  O agente alterou {rewindAsk.files.length} arquivo(s) a partir desta mensagem. Desfazer essas alterações também?
                </p>
                <ul className="my-2 max-h-40 overflow-y-auto font-mono text-xs text-muted">
                  {rewindAsk.files.map((f) => <li key={f}>• {f}</li>)}
                </ul>
                <div className="flex gap-2">
                  <button className="rounded-full border border-line px-3 py-1 text-fg hover:bg-raised"
                          onClick={() => rewindAndRun(rewindAsk.messageId, rewindAsk.keep, rewindAsk.content, true)}>
                    Desfazer os arquivos
                  </button>
                  <button className="rounded-full border border-line px-3 py-1 text-fg hover:bg-raised"
                          onClick={() => rewindAndRun(rewindAsk.messageId, rewindAsk.keep, rewindAsk.content, false)}>
                    Manter os arquivos
                  </button>
                  <button className="px-3 py-1 text-muted hover:text-fg" onClick={() => setRewindAsk(null)}>
                    Cancelar
                  </button>
                </div>
              </div>
            )}
            {o.draft && (
              <div className="my-4">
                <Thinking text={o.draft.thinking} live={!o.draft.content && !o.draft.tool} />
                {o.draft.tool && <ToolDraft tool={o.draft.tool} />}
                {o.draft.content ? (
                  <Markdown text={o.draft.content} />
                ) : (
                  !o.draft.thinking && !o.draft.tool && <div className="animate-pulse text-faint">●</div>
                )}
              </div>
            )}
            {o.status && !o.draft && <div className="my-4 animate-pulse text-sm text-muted">{o.status}</div>}
            {o.stats && (
              <div className="my-3">
                <StatsRow
                  s={o.stats}
                  live={vivo}
                  phase={o.fase}
                  instances={(so ? 0 : instancias)}
                  instancesLabel={rotuloInstancias}
                  onInstances={() => abrir("servers")}
                />
              </div>
            )}
      </>
    );
  };

  // Conteúdo de cada aba do painel direito. Função e não bloco fixo porque o cockpit do Maestro
  // mostra as mesmas abas na doca dele: um lugar só monta Info, Planos, Instâncias, IA local...
  const painelDe = (tab: RightTab) =>
    tab === "browser" ? (
      <BrowserPanel conv={browserKey} onState={(s) => setBrowserOpen(s.open)} />
    ) : tab === "servers" ? (
      <ServersPanel onCount={setServersRunning} onOpen={openConversation} current={currentId} />
    ) : tab === "local" ? (
      <LocalPanel onRunning={onLocalRunning} chatModel={settings.model} />
    ) : tab === "terminal" ? (
      <TerminalPanel conv={browserKey} />
    ) : tab === "changes" ? (
      <ChangesPanel
        conv={currentId}
        provider={settings.provider}
        model={settings.model}
        refreshKey={changesKey}
        action={changesAction}
        onActionDone={() => setChangesAction(null)}
        onCount={setChangesCount}
        onOpen={openPath}
        onConversationChanged={() => {
          refreshConversations();
          if (currentId !== null) openConversation(currentId);
        }}
      />
    ) : tab === "plans" ? (
      <PlansPanel
        plans={plans}
        onJump={(id) => document.getElementById(`plan-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}
      />
    ) : (
      <InfoPanel
        settings={settings}
        section={section}
        toolMode={toolMode}
        onToolMode={changeToolMode}
        vision={vision}
        onVision={changeVision}
        allTools={allTools}
        sent={sent}
        mcp={mcp}
        onReloadMcp={reloadMcp}
        usage={usage}
      />
    );
  // A conversa inteira (rolagem com cola no fim, soltar arquivos, estado vazio): o cockpit do
  // Maestro mostra esta mesma na coluna da Maestro.
  const conversaBlock = (
  <>
  {linkAberto && (
    <Modal onClose={() => setLinkAberto(null)} label="Abrir link" className="w-full max-w-sm space-y-4 rounded-2xl border border-line bg-surface p-5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-base font-medium text-fg">Abrir link</div>
          <div className="mt-1 truncate font-mono text-xs text-muted" title={linkAberto}>{linkAberto}</div>
        </div>
        <button onClick={() => setLinkAberto(null)} title="Fechar" aria-label="Fechar"
                className="-mr-1 -mt-1 rounded-lg p-1.5 text-faint hover:bg-raised hover:text-fg">
          <X className="size-4" />
        </button>
      </div>
      <div className="flex flex-col gap-2">
        <button autoFocus onClick={() => abreLinkNoForja(linkAberto)}
                className="flex items-center gap-2.5 rounded-xl bg-fg px-4 py-2.5 text-sm font-medium text-black hover:bg-white">
          <Globe className="size-4" /> Navegador do Forja
        </button>
        <button onClick={() => { window.open(linkAberto, "_blank"); setLinkAberto(null); }}
                className="flex items-center gap-2.5 rounded-xl border border-line px-4 py-2.5 text-sm text-fg hover:bg-raised">
          <ExternalLink className="size-4" /> Navegador do sistema
        </button>
      </div>
    </Modal>
  )}
  {showFolder && (
    <FolderPicker
      current={conv ? conv.workspace ?? null : pendingWs}
      onPick={pickFolder}
      onClose={() => setShowFolder(false)}
      nativeError={nativeError}
      onNative={chooseFolder}
    />
  )}

  {vista === "trajetoria" && section !== "maestro" ? (
    <Trajetoria messages={messages} />
  ) : (
  <div
    ref={scroller}
    className="flex-1 overflow-y-auto"
    onScroll={seguirFim}
    onDragOver={(e) => e.preventDefault()}
    onDrop={(e) => {
      e.preventDefault();
      if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    }}
  >
    <div className="mx-auto max-w-3xl px-5 py-6">
      {!messages.length && !draft && (
        <div className="mt-[22vh]">
          <LogoMark className="mb-4 size-14 text-fg" title="Forja" />
          <div className="text-3xl font-semibold">Olá!</div>
          <div className="text-3xl text-faint">Como posso ajudar hoje?</div>
          <div className="mt-4 text-sm text-muted">
            {section === "agent" ? (
              <>
                Agente: {wsLabel ? <>lê e escreve em <span className="font-mono text-fg">{wsLabel}</span></> : <span className="text-amber-300">escolha uma pasta de trabalho no topo para começar.</span>}
              </>
            ) : section === "maestro" ? (
              <>
                Maestro: diga o objetivo; ela planeja, delega aos Workers e valida em{" "}
                {wsLabel ? <span className="font-mono text-fg">{wsLabel}</span> : <span className="text-amber-300">uma pasta — escolha no topo para começar.</span>}
              </>
            ) : (
              "Chat: conversa com busca na web, sem acesso a arquivos."
            )}
          </div>
        </div>
      )}

      {conversaDe(messages, { draft, status, stats: running ? liveStats : null, fase })}
      <div ref={fimDoChat} />
    </div>
  </div>
  )}
  </>
  );
  const composerBlock = (
  <div className="px-5 pb-4">
    <div className="mx-auto max-w-3xl">
      {agentica && <ModeWarning permission={settings.permission} />}
      {error && <div className="mb-2 text-sm text-red-300">{error}</div>}

      <CaixaPrompt>
        {(attachments.length > 0 || uploading) && (
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <Attachments list={attachments} onRemove={(a) => setAttachments((l) => l.filter((x) => x !== a))} />
            {uploading && <span className="text-xs text-muted">enviando…</span>}
          </div>
        )}
        {section === "agent" && <GoalStrip convId={currentId} refreshKey={messages.length} />}
        <TodosBar tasks={tarefasAtuais} live={running} />
        {queued.length > 0 && (
          <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
            <span className="text-faint">na fila:</span>
            {queued.map((q, i) => (
              <span key={i} className="max-w-72 truncate rounded-full bg-raised px-2 py-0.5" title={q}>
                {q}
              </span>
            ))}
          </div>
        )}
        {(mentionHits.length > 0 || conversasCitaveis.length > 0) && (
          <div className="mb-2 max-h-56 overflow-y-auto rounded-xl border border-line bg-bg py-1 text-sm">
            {conversasCitaveis.map((c) => (
              <button
                key={`conv-${c.id}`}
                onClick={() => applyMention(`@conversa:${c.id}`)}
                title="Cita esta conversa: o conteúdo dela vai junto para o agente, como referência"
                className="flex w-full items-center gap-3 px-3 py-1.5 text-left hover:bg-raised/60"
              >
                <span className="shrink-0 text-xs text-faint">conversa</span>
                <span className="truncate text-fg">{c.title}</span>
              </button>
            ))}
            {mentionHits.map((f, i) => (
              <button
                key={f}
                onMouseEnter={() => setMentionIndex(i)}
                onClick={() => applyMention(f)}
                className={`flex w-full items-center gap-3 px-3 py-1.5 text-left ${i === mentionIndex ? "bg-raised" : "hover:bg-raised/60"}`}
              >
                <span className="truncate font-mono text-fg">{f}</span>
              </button>
            ))}
          </div>
        )}
        {menuSkill && slashMatches.length > 0 && (
          <div className="mb-2 max-h-56 overflow-y-auto rounded-xl border border-line bg-bg py-1 text-sm">
            {slashMatches.map((s, i) => (
              <button
                key={s.name}
                onMouseEnter={() => setSlashIndex(i)}
                onClick={() => applySkill(s)}
                className={`flex w-full items-center gap-3 px-3 py-1.5 text-left ${i === slashIndex ? "bg-raised" : "hover:bg-raised/60"}`}
              >
                <span className="font-mono text-fg">/{s.name}</span>
                <span className="truncate text-xs text-muted">{s.description}</span>
                <span className="ml-auto shrink-0 text-[10px] text-faint">{s.kind === "action" ? "ação" : s.source ? "skill do projeto" : "prompt"}</span>
              </button>
            ))}
          </div>
        )}
        <textarea
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setSlashIndex(0);
          }}
          onPaste={(e) => {
            // Colar imagem/arquivo do clipboard vira anexo.
            const files = Array.from(e.clipboardData?.files ?? []);
            if (files.length) {
              e.preventDefault();
              addFiles(files);
            }
          }}
          onKeyDown={(e) => {
            if (mentionHits.length) {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                return setMentionIndex((i) => (i + 1) % mentionHits.length);
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                return setMentionIndex((i) => (i - 1 + mentionHits.length) % mentionHits.length);
              }
              if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
                e.preventDefault();
                return applyMention(mentionHits[mentionIndex] ?? mentionHits[0]);
              }
              if (e.key === "Escape") {
                e.preventDefault();
                return setMentionHits([]);
              }
            }
            if (menuSkill && slashMatches.length) {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                return setSlashIndex((i) => (i + 1) % slashMatches.length);
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                return setSlashIndex((i) => (i - 1 + slashMatches.length) % slashMatches.length);
              }
              if (e.key === "Tab") {
                e.preventDefault();
                if (inlineQuery !== null) return applySkill(slashMatches[slashIndex] ?? slashMatches[0]);
                return setInput(`/${slashMatches[slashIndex]?.name ?? slashMatches[0].name} `);
              }
              if (e.key === "Escape" && inlineQuery === null) {
                e.preventDefault();
                return setInput("");
              }
            }
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
            if (e.key === "Tab" && e.shiftKey && agentica) {
              e.preventDefault();
              changePermission(nextPermission(settings.permission, running));
            }
          }}
          ref={composer}
          rows={2}
          placeholder={running ? "Mensagem para o próximo passo do agente (entra na fila)…" : section === "maestro" ? "Qual é o objetivo? A Maestro planeja e delega ( / para comandos, @ para arquivos )" : section === "agent" ? "Peça algo ao agente... ( / para comandos, @ para arquivos )" : "Digite uma mensagem..."}
          className={campoPrompt}
        />
        <RodapePrompt>
          <label title="Anexar arquivos ou imagens" className={redondo}>
            <Paperclip className="size-4" />
            <input
              type="file"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files?.length) addFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
          {agentica && (
            <PermissionMenu value={settings.permission} onChange={changePermission} running={running} />
          )}
          <EffortMenu value={settings.effort} onChange={(effort) => update({ effort })} semExtremo={section === "maestro"} />
          <ContextRing
            used={summary.used}
            max={summary.max}
            out={summary.out}
            avg={summary.avg}
            partes={summary.partes}
            sessao={summary.sessao}
            canCompact={currentId !== null && !running}
            onCompact={compactNow}
            provider={settings.provider}
            models={summary.models}
          />
          <DireitaPrompt>
          <ModelPicker
            provider={settings.provider}
            model={settings.model}
            refreshKey={catalogKey}
            minCtx={section === "maestro" ? config.min_ctx_maestro : undefined}
            autoFallback={section !== "maestro"}
            onChange={(provider, model) => update({ provider, model })}
          />
          {running ? (
            <>
              {input.trim() && (
                <button
                  onClick={() => send()}
                  title="Enviar para a fila (o agente recebe no próximo passo)"
                  className="grid size-9 place-items-center rounded-full border border-line text-fg hover:bg-raised"
                >
                  <ArrowUp />
                </button>
              )}
              <button onClick={stop} title="Parar" className={pararClasse}>
                <Square />
              </button>
            </>
          ) : (
            <button
              onClick={() => send()}
              disabled={!input.trim() && !attachments.length}
              title="Enviar"
              className={enviarClasse}
            >
              <ArrowUp />
            </button>
          )}
          </DireitaPrompt>
        </RodapePrompt>
      </CaixaPrompt>
    </div>
  </div>
  );

  return (
    <div className="flex h-full">
      {!sidebarHidden && (
      <Sidebar
        section={section}
        onSection={changeSection}
        onHide={() => setSidebarHidden(true)}
        conversations={conversations}
        current={currentId}
        unread={unread}
        busy={activity.conversations}
        onSelect={openConversation}
        onNew={newConversation}
        onNewIn={(ws) => {
          // Nova conversa já na pasta do grupo: vira a pasta da conversa no primeiro envio.
          newConversation();
          if (ws) setPendingWs(ws);
        }}
        onDelete={deleteConversation}
        onBulk={async (ids, action) => {
          try {
            const r = await api.post<{ done: number; skipped: number[] }>("/conversations/bulk", { ids, action });
            if (r.skipped?.length) setError(`${r.skipped.length} conversa(s) em execução não foram apagadas.`);
            if ((action === "delete" || action === "archive") && currentId !== null && ids.includes(currentId)) newConversation();
          } catch (e: any) {
            setError(e.message);
          }
          refreshConversations();
        }}
        onRename={(id, title) => patchConversation(id, { title })}
        onPin={(id, pinned) => patchConversation(id, { pinned })}
        onArchive={(id, archived) => {
          patchConversation(id, { archived });
          if (archived && id === currentId) newConversation();
        }}
        onSettings={() => {
          setTrajetoriaEm(null); // voltar das Configurações abre no Chat
          setShowSettings(true);
        }}
      />
      )}
      {showSettings && (
        <SettingsDialog onClose={() => setShowSettings(false)} tools={allTools} mcp={mcp} onChanged={refreshTools} />
      )}

      {/* Área de conteúdo: faixa superior com os botões do painel (como a barra de janela do Claude Desktop),
          e embaixo o chat com o painel lateral abrindo à direita, logo abaixo dos botões. */}
      <div className="flex min-w-0 flex-1 flex-col bg-bg">
        <div className="arrasta livre-controles flex h-12 shrink-0 items-center gap-2 px-3">
          {/* Esquerda: título, pasta e atalhos; direita: botões do painel (tudo numa faixa só, como no Claude Desktop). */}
          <div className="flex min-w-0 flex-1 items-center gap-2">
          {sidebarHidden && (
            // Ocupa a largura da barra lateral (w-64) menos o px-3 e o gap-2 desta faixa: o título fica
            // no mesmo x com a barra aberta ou fechada.
            <div className="w-[calc(16rem-0.5rem)] shrink-0">
              <SectionTabs
                value={section}
                onChange={changeSection}
                sidebarHidden={sidebarHidden}
                onToggleSidebar={() => setSidebarHidden(false)}
              />
            </div>
          )}
          <Laptop className="size-4 shrink-0 text-muted" />
          <span className="truncate text-sm font-medium text-fg" title={conv?.title}>
            {conv?.title ?? "Nova conversa"}
          </span>
          {agentica && (
          <button
            onClick={chooseFolder}
            disabled={running || picking}
            title={wsLabel ? `Pasta de trabalho: ${wsLabel}\nClique para trocar` : "Nenhuma pasta escolhida: clique para escolher"}
            className={`inline-flex max-w-56 shrink-0 items-center gap-1 rounded-md px-2 py-0.5 text-xs disabled:opacity-50 ${wsLabel ? "bg-raised text-muted hover:text-fg" : "bg-amber-500/15 text-amber-300 hover:text-amber-200"}`}
          >
            <span className="truncate">
              {picking ? "escolhendo…" : wsLabel ? folderName(wsLabel) : "Escolher pasta"}
            </span>
            <ChevronDown className="size-3 shrink-0" />
          </button>
          )}
          {agentica && (
            <>
              <button onClick={() => openPath(".", "editor")} title="Abrir a pasta da conversa no editor" className="rounded-md p-1 text-faint hover:bg-raised hover:text-fg">
                <ExternalLink className="size-3.5" />
              </button>
              <button onClick={() => openPath(".", "reveal")} title="Abrir a pasta no Explorer" className="rounded-md p-1 text-faint hover:bg-raised hover:text-fg">
                <FolderOpen className="size-3.5" />
              </button>
            </>
          )}
          {agentica && section !== "maestro" && currentId !== null && (
            <div className="ml-1 flex shrink-0 items-center rounded-md border border-line p-0.5 text-xs" role="tablist" aria-label="Visão da conversa">
              {(["chat", "trajetoria"] as const).map((v) => (
                <button key={v} role="tab" aria-selected={vista === v} onClick={() => setVista(v)}
                  className={`rounded px-2 py-0.5 ${vista === v ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>
                  {v === "chat" ? "Chat" : "Trajetória"}
                </button>
              ))}
            </div>
          )}
          {picking && <span className="text-xs text-muted">Escolha a pasta na janela do sistema (pode estar atrás do navegador).</span>}
          </div>
          <RightTabsBar
            abertos={soltos(gradeTela)}
            onSelect={(tab) => setRight((r) => (abertos(r).includes(tab) ? fecharTile(r, tab) : abrirTile(r, tab, larguraDe(tab))))}
            extras={section === "maestro" ? ABAS_MAESTRO : undefined}
            browserOpen={browserOpen}
            serversRunning={Math.max(serversRunning, activity.servers)}
            localRunning={localRunning || !!activity.local}
            plansPending={plans.filter((p) => p.status === "pendente").length}
            plansTotal={plans.length}
            changesCount={changesCount}
          />
        </div>
        <Tiles
          soPrincipal={section === "maestro"}
          grade={gradeTela}
          onGrade={setRight}
          painel={(t) => painelDe(t as RightTab)}
        >
        {section === "maestro" ? (
          <MaestroView
            convId={currentId}
            messages={messages}
            draft={draft}
            running={running}
            approvals={approvals}
            subSteps={subSteps}
            board={board}
            onBoard={setBoard}
            modelPhase={modelPhase}
            provider={settings.provider}
            model={settings.model}
            conversa={conversaBlock}
            renderConversa={conversaDe}
            composer={composerBlock}
            grade={right}
            onGrade={setRight}
            painel={painelDe}
            onDecide={decide}
            pausado={pausado}
            onPausar={pausar}
            onPedir={(texto) => send(texto)}
            onTestarWorker={(id, nome, spec) => {
              // O Comparar lê isto ao abrir: teste pronto da especialidade e o modelo atual já na lista.
              try {
                localStorage.setItem("forja.comparar.preset", JSON.stringify({ id, nome, spec }));
              } catch {
                /* sem storage: abre o Comparar vazio */
              }
              changeSection("comparar");
            }}
            onNovaSessao={async () => {
              // Contexto limpo, com cópia do trabalho aberto; a lista fica nesta conversa para consulta.
              if (currentId === null) return;
              try {
                const r = await api.post<{ id: number }>(`/maestro/${currentId}/nova-sessao`, {});
                await refreshConversations();
                openConversation(r.id);
              } catch (e: any) {
                setError(e.message);
              }
            }}
          />
        ) : section === "pesquisa" ? (
          <PesquisaView
            conv={currentId}
            ensureConversation={ensureConversation}
            provider={settings.provider}
            model={settings.model}
            onError={setError}
            onConversationChanged={refreshConversations}
            onAbrirChat={(id) => {
              setSection("chat");
              setCurrentId(id);
              refreshConversations();
            }}
            onTerminou={(titulo, corpo) => {
              notify(titulo, corpo, true);   // force: a pesquisa é longa, o aviso vale mesmo em foco
              if (currentId !== null) setUnread((u) => new Set(u).add(currentId));
            }}
          />
        ) : section === "comparar" ? (
          <CompararView
            conv={currentId}
            ensureConversation={ensureConversation}
            provider={settings.provider}
            model={settings.model}
            onError={setError}
            onConversationChanged={refreshConversations}
            onAbrirNoNavegador={(url) => {
              // a página do teste abre no navegador integrado desta conversa, com o painel à vista
              abrir("browser");
              // aba própria para cada página testada; testar de novo volta para ela (não duplica)
              api.post(`/browser/abrir?conv=${browserKey}`, { url }).catch((e) => setError(e.message));
            }}
            onRodarNoTerminal={(comando) => {
              abrir("terminal");
              executarNoTerminal(browserKey, comando).catch((e) => setError(e.message));
            }}
          />
        ) : section === "imagem" ? (
          <ImagensView
            conv={currentId}
            onAbrirConversa={irParaConversa}
            ensureConversation={ensureConversation}
            provider={settings.provider}
            model={settings.model}
            onError={setError}
            onConversationChanged={refreshConversations}
          />
        ) : section === "video" ? (
          <VideoView
            conv={currentId}
            ensureConversation={ensureConversation}
            provider={settings.provider}
            model={settings.model}
            onError={setError}
            onConversationChanged={refreshConversations}
            onAbrirBaixar={() => {
              abrir("local");
              // o painel IA local escuta e abre direto na aba Vídeo (kits, modelos e ampliação)
              setTimeout(() => window.dispatchEvent(new CustomEvent("forja:ia-local", { detail: "Vídeo" })), 0);
            }}
          />
        ) : (
        <>
        {conversaBlock}

        {composerBlock}
        </>
        )}
        </Tiles>
      </div>
      <LocalLoading />
    </div>
  );
}

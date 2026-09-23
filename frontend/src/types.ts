export type ToolCall = { id: string; name: string; arguments: Record<string, unknown> };

/** Página trazida por web_search/fetch_url: vem em `meta.sources` do resultado da ferramenta. */
export type Source = { url: string; titulo: string; dominio: string; trecho?: string };

export type Preview = { kind: "diff" | "new" | "command"; path: string; text: string };

/** Aprovação pendente: preview é null para ferramentas sem preview (ex.: MCP); sent = decisão já enviada. */
/** Uma pergunta do ask_user: o agente manda até 4 de uma vez. */
export type AskQuestion = {
  header?: string;
  question: string;
  options: { label: string; description?: string }[];
  multi_select?: boolean;
};

export type Approval = {
  preview: Preview | null;
  suggest?: string;
  tool?: string;
  plan?: string; // exit_plan_mode
  questions?: AskQuestion[]; // ask_user
  sent?: boolean;
};

/** O que está rodando agora: turno do agente e delegações por conversa, e processos vivos. */
export type Activity = {
  conversations: { id: number; running: boolean; subagents: number; servers: number; waiting?: number; paused?: boolean }[];
  servers: number;
};

export type Attachment = { path: string; name: string; size: number; mime: string; kind: "image" | "text" | "file" };

export type Message = {
  id: number;
  role: "user" | "assistant" | "tool" | "event";
  content: string;
  thinking: string;
  tool_calls: ToolCall[] | null;
  tool_call_id: string | null;
  name: string | null;
  status: "ok" | "erro" | "rejeitada" | "cancelada" | "running" | "pronto" | "cancelado" | null;
  meta: Record<string, any> | null;
};

export type Conversation = {
  id: number;
  title: string;
  updated_at: string;
  kind?: "chat" | "agent" | "maestro" | "imagem" | "comparar" | "pesquisa";
  workspace?: string | null;
  workspace_label?: string;
  pinned?: boolean;
  archived?: boolean;
  snippet?: string; // trecho que casou na busca por conteúdo
};

/** Arquivo alterado pelo agente nesta conversa (checkpoints), com diff do antes para o agora. */
export type ChangeFile = { path: string; status: "created" | "modified" | "deleted" | "unchanged"; diff: string;
  additions: number; deletions: number;
  /** Nada legível para comparar (imagem, zip, PDF escaneado). Documento de escritório NÃO é binário aqui:
   *  o diff é do texto extraído dele. */
  binary?: boolean };

export type GitStatus = {
  repo: boolean;
  branch?: string;
  files?: { status: string; path: string; staged: boolean }[];
  ahead?: number | null;
  behind?: number | null;
  remote?: string;
  has_gh?: boolean;
  last_commit?: string;
};

export type Task = { text: string; status: "pending" | "doing" | "done" };

export type Skill = { name: string; kind: "action" | "prompt"; description: string; action?: string; prompt?: string; source?: string };

export type ToolsSent = {
  mode: "chat" | "agent";
  provider: string;
  model: string;
  tool_mode: string;
  via: "native" | "prompt" | "none";
  num_ctx: number | null;
  tools: { name: string; mutating: boolean }[];
  permission?: string;
  permission_label?: string;
  effort?: string;
  max_iterations?: number;
  /** Capacidades efetivas do modelo nesta requisição (ex.: "vision") e de onde veio a informação. */
  capabilities?: string[];
  capabilities_detected?: string[] | null;
  vision_source?: "detectado" | "override" | "desconhecido";
  /** Ferramentas ligadas mas não enviadas porque o modelo não tem a capacidade exigida. */
  blocked?: { name: string; missing: string[] }[];
  /** Máquina e shell onde run_command/serve_* executam nesta requisição. */
  environment?: string;
};

/** Servidor iniciado pelo agente com serve_start. No Desktop roda sempre na sua máquina. */
/** Cota consumida num provedor do Ollama Cloud (GET /api/usage). */
export type CloudUsage = {
  provider: string;
  name: string;
  limits: { name: string; usage: number }[];
  models: { name: string; request_count?: number }[];
};

/** Delegação em andamento, em qualquer conversa (aba Instâncias). */
export type SubagentActive = {
  id: string;
  conversation_id: number;
  conversation?: string;
  run_id: string;
  task: string;
  status: string;
  seconds: number;
  level: string;
  model: string;
  provider: string;
  iterations: number;
  tokens: number;
  steps: number;
};

export type ServerInfo = {
  name: string;
  pid?: number;
  alive: boolean;
  exit_code?: number | null;
  command: string;
  cwd?: string;
  log?: string;
  uptime?: number;
  conv?: string; // conversa que subiu o processo, para a aba separar por conversa
  where: "local" | "host" | "container"; // no Desktop é sempre "local": tudo roda na sua máquina
  error?: string;
};


export type BrowserTab = { index: number; url: string; title: string; active: boolean };

/** Estado da sessão do navegador de uma conversa (`key` = id da conversa, "0" = rascunho). */
export type BrowserState = {
  key?: string;
  open: boolean;
  url: string;
  title: string;
  width: number;
  height: number;
  scale?: number;
  tabs?: BrowserTab[];
  file_chooser?: boolean;
  native?: boolean; // Forja Desktop: as abas são views nativas na janela; não há frames no stream
};

export type Settings = {
  provider: string;
  model: string;
  permission: "auto" | "manual" | "edits" | "plan" | "bypass";
  effort: "baixo" | "medio" | "alto" | "maximo" | "extremo";
};

export type Stats = {
  model: string;
  prompt_tokens: number;
  tokens: number;
  estimated: boolean;
  seconds: number;
  tps: number | null;
  ctx_max: number | null;
};

// ------------------------------------------------------------------ comparar modelos

/** O que a tela manda para o backend: um modelo de provedor, ou um arquivo .gguf. */
export type CompararEntrada = { provider?: string; model?: string; path?: string; nome: string };

export type CompararItem = {
  id: string;
  rotulo: string; // A, B, C… é o que aparece no modo cego
  provider: string;
  model: string;
  path: string; // vazio = modelo de provedor
  nome: string;
  status: "pendente" | "carregando" | "rodando" | "pronto" | "erro" | "cancelado";
  content: string;
  reasoning: string;
  stats: Stats | null;
  error: string;
};

export type CompararEstado = {
  message_id: number;
  status: "rodando" | "pronto" | "erro" | "cancelado";
  modo: "paralelo" | "sequencial";
  cego: boolean;
  revelado: boolean;
  voto: string; // id do item vencedor
  itens: CompararItem[];
};

export type PlacarLinha = { nome: string; rodadas: number; vitorias: number; erros: number; tps: number | null };

// ------------------------------------------------------------------ pesquisa profunda

export type PesquisaFonte = {
  id: string;
  rodada: number;
  url: string;
  titulo: string;
  dominio: string;
  status: "fila" | "lendo" | "util" | "vazia" | "erro";
  erro: string;
  resumo: string;
  trecho: string;
};

export type PesquisaEstado = {
  message_id: number;
  pergunta: string;
  profundidade: PesquisaProfundidade;
  status: "rodando" | "pronto" | "erro" | "cancelado";
  fase: "planejando" | "buscando" | "lendo" | "escrevendo" | "pronto";
  contexto: string;
  plano: { perguntas: string[]; buscas: string[] };
  rodada: number;
  rodadas: { n: number; buscas: string[] }[];
  fontes: PesquisaFonte[];
  resumo: string;   // primeiro parágrafo do relatório: é o que a aba mostra
  aviso: string;
  relatorio: string;  // markdown completo; a aba não renderiza, o HTML abre fora
  formato: PesquisaFormato;
  formato_usado: string;  // o que o "auto" decidiu; vazio enquanto não decidiu
  teto_segundos: number;  // tempo máximo desta corrida
  rodadas_total: number;  // quantas rodadas esta corrida pode fazer
  stats: {
    fontes: number; uteis: number; segundos: number; rodadas: number;
    tokens: number; tokens_entrada: number; gerando: number; chamadas: number; estimado: boolean;
    extrator: string; escritor: string; extrator_provider: string; escritor_provider: string;
  };
};

export type PesquisaFormato = "auto" | "produto" | "comparar" | "guia" | "checagem";
export type PesquisaProfundidade = "rapida" | "normal" | "funda" | "personalizado";

// ------------------------------------------------------------------ IA local (llama.cpp / sd.cpp)

/** Parâmetros de carga do llama-server. Zero/padrão = deixa o llama.cpp decidir. */
export type LlamaParams = {
  ctx: number;
  ngl: number;
  threads: number;
  batch: number;
  ubatch: number;
  parallel: number;
  flash_attn: boolean;
  cache_type_k: string;
  cache_type_v: string;
  kv_unified: boolean;
  no_kv_offload: boolean;
  mlock: boolean;
  mmap: boolean;
  seed: number;
  rope_freq_base: number;
  rope_freq_scale: number;
  ctx_checkpoints: number;
  n_cpu_moe: number;
  n_expert: number;
  mmproj: string;
  fit: boolean; // deixa o llama.cpp ajustar o que não couber
};

export type LocalModel = {
  path: string;
  name: string;
  size: number;
  mtime: number;
  shards: number; // > 1 = modelo dividido em vários arquivos
  folder: string;
  kind: "chat" | "image";
  params?: ImageParams; // só nos modelos de imagem: ajustes próprios daquele modelo
};

/** Ajustes que um modelo de imagem pode ter por conta própria. */
export type ImageParams = {
  steps: number;
  cfg: number;
  width: number;
  height: number;
  sampler: string;
  negative: string;
  vae: string;
  clip_l: string;
  t5xxl: string;
};

/** Metadados lidos do cabeçalho do .gguf. */
export type ModelInfo = {
  arch: string;
  n_layer: number;
  n_head_kv: number;
  head_dim: number;
  ctx_train: number; // contexto máximo treinado
  n_expert: number;
  n_expert_used: number;
  attn_interval: number; // 1 em cada N camadas tem atenção (modelos híbridos)
  size: number;
};

/** Estimativa de memória: o que vai para a VRAM e o total com a RAM. */
export type MemoryEstimate = {
  ok: boolean;
  gpu: number;
  total: number;
  weights_gpu: number;
  weights_cpu: number;
  kv: number;
  kv_gpu: number;
  recurrent: number;
  compute_gpu: number;
  layers_gpu: number;
  n_layer: number;
  ctx_train: number;
  attn_layers: number;
};

/** Amostragem de um modelo (tela Inferência). Vale para qualquer provedor: fica em model_settings. */
export type Inference = {
  temperature: number;
  top_k: number;
  top_p: number;
  min_p: number;
  repeat_penalty: number;
  max_tokens: number; // 0 = sem limite
  stop: string[];
  think: boolean; // enable_thinking do template
  reasoning_budget: number; // -1 = sem teto
};

/** Resposta de GET /local/inference: serve para modelo local e para modelo de qualquer provedor. */
export type InferenceView = {
  model: string;
  inference: Inference;
  inference_defaults: Inference;
  inference_overrides: (keyof Inference)[];
};

/** Resposta de POST /local/model: padrões, valores atuais, o que saiu do padrão e a estimativa. */
export type ModelView = {
  path: string;
  info: ModelInfo;
  defaults: LlamaParams;
  params: LlamaParams;
  overrides: (keyof LlamaParams)[];
  estimate: MemoryEstimate;
  model: string; // id do modelo no chat (alias do llama-server)
  inference: Inference;
  inference_defaults: Inference;
  inference_overrides: (keyof Inference)[];
};

/** Download ou geração em andamento (barra de progresso no painel). */
export type Job = {
  id: string;
  kind: "runtime" | "modelo" | "imagem" | string;
  name: string;
  done: number;
  total: number;
  status: "running" | "pronto" | "erro" | "cancelado" | string;
  error: string;
  detail: string;
  result: string | null;
};

export type ImageOpts = {
  model: string;
  out_dir: string; // vazio = %APPDATA%/Forja/imagens
  vae: string;
  clip_l: string;
  t5xxl: string;
  diffusion_model: string;
  steps: number;
  cfg: number;
  width: number;
  height: number;
  sampler: string;
  negative: string;
  seed: number; // 0 = aleatória
  descarte_dias: number; // prazo das imagens reprovadas em descartadas/ (0 = guardar para sempre)
};

/** Uma variação dentro de um lote da seção Imagens. */
export type LoteImagem = {
  path: string;
  seed: number;
  model: string;
  model_name: string;
  status: "pendente" | "gerando" | "pronta" | "erro" | "mantida" | "descartada" | "cancelada";
  error: string;
};

/** meta da mensagem do assistente num lote (a thread do backend vai preenchendo `images`). */
export type LoteMeta = {
  job: string;
  count: number;
  seed_mode: SeedMode;
  opts: Partial<ImageOpts>;
  images: LoteImagem[];
};

export type SeedMode = "incremental" | "aleatoria" | "fixa";

export type RuntimeInfo = {
  installed: boolean;
  exe: string;
  backend: string; // o que está em uso
  backends: string[]; // o que dá para baixar
  available: { backend: string; exe: string; version: string }[]; // o que já está no disco
  chosen: string; // escolhido à mão em Configurações › Runtime ("" = automático)
};

/** Memória da máquina: é o que diz se um modelo cabe na GPU, na RAM, ou em lugar nenhum. */
export type Hardware = {
  gpus: { id: string; name: string; total: number; free: number; enabled: boolean }[];
  cpu: { name: string; arch: string; flags: string[]; cores: number };
  vram: number;
  vram_free: number;
  ram: number;
  ram_free: number;
};

export type LocalState = {
  runtimes: { llama: RuntimeInfo; sd: RuntimeInfo };
  models: LocalModel[];
  server: {
    running: boolean;
    port: number;
    path?: string;
    alias?: string;
    params?: LlamaParams;
    ctx?: number | null;
    pid?: number;
    uptime?: number;
    vision?: boolean;
    /** Projetor de visão incompatível com o runtime: o encoder cai na CPU e cada print leva minutos. */
    vision_lenta?: string;
    // Carga em andamento (barra no topo da janela) e a falha da última tentativa, que fica até a próxima.
    loading?: { path: string; name: string; elapsed: number; eta: number; percent: number };
    error?: { when: number; path: string; message: string; log: string };
  };
  dirs: string[];
  download_dir: string; // para onde vão os downloads (uma das dirs)
  models_dir: string; // pasta padrão dos modelos (Configurações › Pastas)
  data_dir: string;
  image_busy: boolean; // gerando imagem: carregar modelo fica bloqueado
  hardware: Hardware;
  guardrail: "off" | "relaxado" | "rigoroso";
  autoload: boolean; // carregar o último modelo ao abrir o Forja
  hf_token: boolean; // só diz se existe; o token não volta do backend
  jobs: Job[];
  defaults: LlamaParams;
  last: string;
  image: ImageOpts;
  image_dir: string; // pasta onde as imagens do painel são salvas
  image_models: LocalModel[];
  port: number;
};

export type HfModel = {
  id: string;
  author: string;
  downloads: number;
  likes: number;
  updated: string;
  gated: boolean;
  tags: string[];
};

/** Ficha de um repositório na janela de busca. */
export type HfRepo = {
  id: string;
  author: string;
  downloads: number;
  likes: number;
  updated: string;
  gated: boolean;
  tags: string[];
  license: string;
  params: number;
  arch: string;
  ctx_train: number;
  capabilities: { vision: boolean; tools: boolean; reasoning: boolean };
  files: HfFile[];
  readme: string;
};
export type HfFile = { path: string; size: number; quant: string; shards: number };

// ------------------------------------------------------------------ Maestro
// A Maestro planeja e verifica; os Workers implementam. O estado real vive no SQLite do backend
// (taskdb) — a árvore aqui é leitura, vinda de /api/maestro/{conv}/board.

export type Contract = {
  context?: string;
  goal: string;
  relevant_files?: string[];
  requirements?: string[];
  constraints?: string[];
  do_not?: string[];
  acceptance_criteria?: string[];
  verify_command?: string;
  expected_result?: string;
};

export type TaskStatus =
  | "pending" | "queued" | "loading_model" | "implementing" | "testing" | "reviewing"
  | "completed" | "failed" | "blocked" | "needs_human" | "cancelled";

/** Protocolo Worker → Maestro. `changes` e `tests` são medição (git + comando), não autoavaliação. */
export type TaskResult = {
  type: "task_result";
  task_code: string;
  attempt: number;
  status: "completed" | "failed" | "unverified" | "error" | "cancelled";
  changes: { path: string; status: string; additions: number | null; deletions: number | null }[];
  commands: { command: string; status: string }[];
  tests: { command: string; status: string; output: string } | null;
  errors: string[];
  review: string | null;
  summary: string;
  model: string | null;
  level: string | null;
  agent?: string | null;
  seconds: number | null;
  tokens: number | null;
  iterations: number | null;
};

export type TaskAttempt = {
  n: number;
  status: string;
  worker: { level?: string; provider?: string; model?: string; agent?: string | null };
  strategy: string | null;
  error: string | null;
  seconds: number;
  tokens: number;
  result: TaskResult | null;
  started_at: string;
  finished_at: string | null;
  has_transcript?: boolean;
  transcript?: Message[];  // só no detalhe da tarefa: a conversa do Worker nesta tentativa
};

export type MaestroTask = {
  code: string;
  title: string;
  status: TaskStatus;
  feature_id: number;
  priority: number;
  depends_on: string[];
  model_slot: string | null;
  agent: string | null;
  attempt_count: number;
  max_attempts: number;
  blocked_reason: string | null;
  contract: Contract;
  result: TaskResult | null;
  updated_at: string;
  attempts?: TaskAttempt[];
};

export type MaestroFeature = {
  id: number;
  title: string;
  goal: string;
  status: "planning" | "active" | "validating" | "done" | "cancelled";
  tasks: MaestroTask[];
};

export type MaestroBoard = {
  features: MaestroFeature[];
  counts: Partial<Record<TaskStatus, number>>;
  total: number;
  done: number;
  open: number;
};

export type MaestroModels = {
  running: boolean;
  alias: string | null;
  ctx: number | null;
  vram: number | null;
  vram_free: number | null;
  ram: number | null;
  ram_free: number | null;
  lifecycle: string;
  max_workers: number;
  can_swap: boolean;
  manageable: boolean;
  min_ctx_worker: number;  // GGUF local com janela menor que isto não pode ser Worker
  slots: Record<string, { provider: string; model: string }>;
  active: SubagentActive[];
};

/** Resposta em andamento. `tool` são os argumentos de uma tool call ainda chegando (write_file de
 *  arquivo grande leva minutos e, sem isto, a tela fica parada como se o modelo tivesse travado). */
export type Draft = {
  content: string;
  thinking: string;
  tool?: { name: string; path?: string; text: string; chars?: number } | null;
};

/** Passos de um subagente/Worker, agrupados pelo id da chamada que o criou. */
export type SubState = {
  status: string;
  steps: { call: any; result?: Message }[];
  // Worker de contrato (Maestro): a conversa dele no formato do chat, e a resposta em andamento.
  mensagens?: Message[];
  draft?: Draft | null;
};

/** Troca de modelo em andamento, para o cockpit mostrar por que a execução parou por uns minutos. */
export type ModelPhase = {
  phase: "unloading" | "loading" | "ready" | "unloaded" | "error" | "clearing" | "cleared" | "restarting";
  previous?: string;
  model?: string;
  reason?: string;
};

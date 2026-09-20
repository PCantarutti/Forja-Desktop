export type ToolCall = { id: string; name: string; arguments: Record<string, unknown> };

export type Preview = { kind: "diff" | "new" | "command"; path: string; text: string };

/** Aprovação pendente: preview é null para ferramentas sem preview (ex.: MCP); sent = decisão já enviada. */
export type Approval = { preview: Preview | null; suggest?: string; tool?: string; plan?: string; sent?: boolean };

export type Attachment = { path: string; name: string; size: number; mime: string; kind: "image" | "text" | "file" };

export type Message = {
  id: number;
  role: "user" | "assistant" | "tool" | "event";
  content: string;
  thinking: string;
  tool_calls: ToolCall[] | null;
  tool_call_id: string | null;
  name: string | null;
  status: "ok" | "erro" | "rejeitada" | "cancelada" | null;
  meta: Record<string, any> | null;
};

export type Conversation = {
  id: number;
  title: string;
  updated_at: string;
  workspace?: string | null;
  workspace_label?: string;
  pinned?: boolean;
  archived?: boolean;
  snippet?: string; // trecho que casou na busca por conteúdo
};

/** Arquivo alterado pelo agente nesta conversa (checkpoints), com diff do antes para o agora. */
export type ChangeFile = { path: string; status: "created" | "modified" | "deleted" | "unchanged"; diff: string; additions: number; deletions: number };

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

/** Servidor iniciado pelo agente com serve_start, no seu sistema ("host") ou no container. */
export type ServerInfo = {
  name: string;
  pid?: number;
  alive: boolean;
  exit_code?: number | null;
  command: string;
  cwd?: string;
  log?: string;
  uptime?: number;
  where: "host" | "container";
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
};

export type Settings = {
  provider: string;
  model: string;
  permission: "auto" | "manual" | "edits" | "plan" | "bypass";
  effort: "baixo" | "medio" | "alto" | "maximo";
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

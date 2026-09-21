import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Approval, AskQuestion, Attachment, Message, Preview, Task, ToolCall } from "../types";
import { Brain, Check, Chevron, ChevronDown, Clipboard, Split, Clock, Copy, Download, Cube, FolderOpen, Gauge, Shield, Tokens, X } from "./icons";

/** Bloco de código com botão de copiar no canto (aparece ao passar o mouse). */
function CodeBlock(props: React.ComponentProps<"pre">) {
  const ref = useRef<HTMLPreElement>(null);
  // ponytail: o texto vem do DOM já renderizado, sem remontar o AST do markdown
  return (
    <div className="group relative">
      <pre ref={ref} {...props} />
      <div className="absolute top-1.5 right-1.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <CopyButton text={() => ref.current?.textContent ?? ""} bg />
      </div>
    </div>
  );
}

/** Tabela larga rola dentro do card em vez de estourar (o painel de planos e chato de estreito). */
function Table(props: React.ComponentProps<"table">) {
  return (
    <div className="md-table">
      <table {...props} />
    </div>
  );
}

const MD_COMPONENTS = { pre: CodeBlock, table: Table };

/**
 * Memoizado, e é o `memo` que mais paga no app inteiro.
 *
 * Cada token recebido re-renderiza o App, e daí toda mensagem anterior da conversa. Sem isto,
 * cada uma refazia o pipeline remark-gfm + rehype-highlight do seu texto INTEIRO, dezenas de vezes
 * por segundo, numa lista que só cresce — era o custo dominante de uma resposta longa. Com o texto
 * igual, o trabalho não se repete.
 */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="md text-[15px]">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={MD_COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
});

export function Thinking({ text, live }: { text: string; live?: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);
  if (!text) return null;
  const isOpen = open ?? !!live;
  return (
    <div className="mb-3 overflow-hidden rounded-2xl border border-line bg-surface">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-4 py-2.5 font-mono text-sm text-muted hover:text-fg"
      >
        <Brain className={`size-4 ${live ? "animate-pulse" : ""}`} />
        {live ? "Raciocinando..." : "Raciocínio"}
        <Chevron className="ml-auto size-4" />
      </button>
      {isOpen && (
        <div className="max-h-80 overflow-y-auto border-t border-line px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap text-fg/85">
          {text}
        </div>
      )}
    </div>
  );
}

// Anexos e screenshots ficam na pasta de trabalho DA CONVERSA; o App avisa qual está aberta.
let fileConv = "0";
export const setFileConv = (conv: number | null) => {
  fileConv = conv === null ? "0" : String(conv);
};
export const fileUrl = (a: Attachment) => `/api/files?path=${encodeURIComponent(a.path)}&conv=${fileConv}`;

/** Imagem em tela cheia; clique (ou Esc) fecha. */
export function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div onClick={onClose} className="fixed inset-0 z-50 grid cursor-zoom-out place-items-center bg-black/85 p-4">
      <img src={src} alt="" className="max-h-full max-w-full rounded-lg shadow-2xl" />
    </div>
  );
}

/** Screenshot devolvido por uma ferramenta: grande no chat, clique abre em tela cheia. */
export function ToolImages({ list, bare }: { list: Attachment[]; bare?: boolean }) {
  const [zoom, setZoom] = useState<string | null>(null);
  const images = list.filter((a) => a.kind === "image");
  if (!images.length) return null;
  return (
    <div className={bare ? "my-2 space-y-2" : "space-y-2 border-t border-line p-3"}>
      {images.map((a) => (
        <img
          key={a.path}
          src={fileUrl(a)}
          alt={a.name}
          title="Clique para ampliar"
          onClick={() => setZoom(fileUrl(a))}
          className="block max-h-48 w-auto max-w-sm cursor-zoom-in rounded-xl border border-line bg-black object-contain"
        />
      ))}
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}

const ICONE_POR_TIPO: { casa: RegExp; rotulo: string }[] = [
  { casa: /sheet|excel|csv/, rotulo: "Planilha" },
  { casa: /word|document$/, rotulo: "Documento" },
  { casa: /presentation/, rotulo: "Apresentação" },
  { casa: /pdf/, rotulo: "PDF" },
];

/**
 * Arquivo que o agente gerou: .docx, .xlsx, .pdf, .pptx.
 *
 * O `ToolImages` filtra `kind === "image"` e descarta o resto em silêncio, então até aqui um
 * documento gerado não aparecia em lugar nenhum — só o caminho, escondido dentro do bloco da
 * ferramenta. Abrir chama o programa padrão do sistema (Word, Excel), que é o que se quer fazer
 * com um arquivo desses.
 */
export function ToolFiles({ list, onOpen }: { list: Attachment[]; onOpen?: (path: string, mode: "editor" | "reveal") => void }) {
  const arquivos = list.filter((a) => a.kind !== "image");
  if (!arquivos.length) return null;
  return (
    <div className="my-2 space-y-2">
      {arquivos.map((a) => {
        const rotulo = ICONE_POR_TIPO.find((t) => t.casa.test(a.mime))?.rotulo ?? "Arquivo";
        return (
          <div key={a.path} className="flex max-w-md items-center gap-3 rounded-xl border border-line bg-surface px-3 py-2.5">
            <Download className="size-4 shrink-0 text-muted" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-fg" title={a.path}>
                {a.name}
              </div>
              <div className="text-xs text-faint">
                {rotulo} · {Math.max(1, Math.round(a.size / 1024))} KB
              </div>
            </div>
            {onOpen && (
              <div className="flex shrink-0 gap-1">
                <button
                  onClick={() => onOpen(a.path, "editor")}
                  title="Abrir no programa padrão"
                  className="rounded-full border border-line px-2.5 py-1 text-xs text-fg hover:bg-raised"
                >
                  Abrir
                </button>
                <button
                  onClick={() => onOpen(a.path, "reveal")}
                  title="Revelar na pasta"
                  className="rounded-full border border-line p-1.5 text-muted hover:bg-raised hover:text-fg"
                >
                  <FolderOpen className="size-3.5" />
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Anexos de uma mensagem: miniatura para imagem (clique amplia), chip para o resto. */
export function Attachments({ list, onRemove }: { list: Attachment[]; onRemove?: (a: Attachment) => void }) {
  const [zoom, setZoom] = useState<string | null>(null);
  if (!list.length) return null;
  return (
    <div className="mt-2 flex flex-wrap justify-end gap-2">
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
      {list.map((a) => (
        <div key={a.path} className="relative">
          {a.kind === "image" ? (
            <img
              src={fileUrl(a)}
              alt={a.name}
              onClick={() => setZoom(fileUrl(a))}
              className="max-h-40 cursor-zoom-in rounded-xl border border-line object-cover"
            />
          ) : (
            <span className="inline-flex max-w-60 items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-muted">
              <span className="truncate">{a.name}</span>
              <span className="text-faint">{Math.round(a.size / 1024)} KB</span>
            </span>
          )}
          {onRemove && (
            <button
              onClick={() => onRemove(a)}
              title="Remover"
              className="absolute -top-1.5 -right-1.5 grid size-5 place-items-center rounded-full bg-raised text-faint hover:text-fg"
            >
              <X className="size-3" />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

export function CopyButton({ text, bg }: { text: string | (() => string); bg?: boolean }) {
  const [done, setDone] = useState(false);
  return (
    <button
      title={done ? "Copiado" : "Copiar"}
      onClick={() => {
        navigator.clipboard?.writeText(typeof text === "function" ? text() : text);
        setDone(true);
        setTimeout(() => setDone(false), 1200);
      }}
      className={`rounded-md p-1.5 text-faint hover:bg-raised hover:text-fg ${
        bg ? "border border-line bg-surface/90 backdrop-blur" : ""
      }`}
    >
      {done ? <Check /> : <Copy />}
    </button>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-raised px-2 py-0.5 text-xs text-muted">{children}</span>
  );
}

export type TurnStats = { model: string; tokens: number; seconds: number; tps: number | null; estimated: boolean };

export function StatsRow({ s, live, instances, onInstances, phase }: { s: TurnStats; live?: boolean; instances?: number; onInstances?: () => void; phase?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted">
      <Chip>
        <Cube className="size-3.5" /> {s.model}
      </Chip>
      {live && <span className="size-1.5 animate-pulse rounded-full bg-sky-400" title="gerando: valores em tempo real" />}
      <span className="inline-flex items-center gap-1.5" title={live ? "contagem em tempo real (aproximada)" : s.estimated ? "estimado (chars/4)" : "informado pelo provider"}>
        <Tokens className="size-3.5" /> {s.estimated || live ? "~" : ""}
        {s.tokens.toLocaleString("pt-BR")} tokens
      </span>
      <span className="inline-flex items-center gap-1.5">
        <Clock className="size-3.5" /> {s.seconds < 60 ? `${s.seconds.toFixed(1)}s` : `${Math.floor(s.seconds / 60)}m${Math.round(s.seconds % 60)}s`}
      </span>
      {s.tps != null && (
        <span className="inline-flex items-center gap-1.5">
          <Gauge className="size-3.5" /> {s.tps.toFixed(2)} t/s
        </span>
      )}
      {(!!instances || !!phase) && (
        <button
          onClick={onInstances}
          title={instances ? "Subagentes e processos desta conversa — clique para abrir a aba Instâncias" : undefined}
          className="inline-flex items-center gap-1.5 text-sky-300 hover:text-sky-200"
        >
          {phase ? (
            <span className="size-3 animate-spin rounded-full border border-sky-400/30 border-t-sky-300" />
          ) : (
            <span className="size-1.5 animate-pulse rounded-full bg-sky-400" />
          )}
          {[instances ? `${instances} instância${instances > 1 ? "s" : ""} rodando` : "", phase]
            .filter(Boolean)
            .join(" · ")}
        </button>
      )}
    </div>
  );
}

export function DiffView({ preview }: { preview: Preview }) {
  if (preview.kind === "command")
    return (
      <div className="overflow-hidden rounded-xl border border-line">
        <div className="bg-raised px-3 py-1.5 text-xs text-muted">
          Comando · <span className="font-mono">{preview.path}</span>
        </div>
        <pre className="max-h-64 overflow-auto bg-[#0d0d0d] px-3 py-2.5 font-mono text-xs whitespace-pre-wrap text-fg">
          <span className="text-faint select-none">$ </span>
          {preview.text}
        </pre>
      </div>
    );
  const lines = preview.kind === "new" ? preview.text.split("\n").map((l) => "+" + l) : preview.text.split("\n");
  return (
    <div className="overflow-hidden rounded-xl border border-line">
      <div className="bg-raised px-3 py-1.5 text-xs text-muted">
        {preview.kind === "new" ? "Arquivo novo" : "Diff"} · <span className="font-mono">{preview.path}</span>
      </div>
      <pre className="max-h-96 overflow-auto bg-[#0d0d0d] py-2 font-mono text-xs leading-5">
        {lines.map((l, i) => {
          const cls = l.startsWith("@@")
            ? "text-sky-400"
            : l.startsWith("+++") || l.startsWith("---")
              ? "text-faint"
              : l.startsWith("+")
                ? "bg-emerald-950/70 text-emerald-300"
                : l.startsWith("-")
                  ? "bg-red-950/70 text-red-300"
                  : "text-muted";
          return (
            <div key={i} className={`px-3 whitespace-pre ${cls}`}>
              {l || " "}
            </div>
          );
        })}
      </pre>
    </div>
  );
}

const STATUS: Record<string, [string, string]> = {
  ok: ["ok", "text-emerald-400"],
  erro: ["erro", "text-red-400"],
  rejeitada: ["rejeitada", "text-orange-400"],
  cancelada: ["cancelada", "text-faint"],
  aguardando: ["aguardando aprovação", "text-amber-300"],
  executando: ["executando…", "text-sky-400"],
  fila: ["na fila", "text-faint"],
  pendente: ["não executada", "text-faint"],
};

/** Lista de tarefas do agente (update_tasks): o que já foi feito, o que está em andamento. */
export function TasksCard({ tasks, live }: { tasks: Task[]; live?: boolean }) {
  if (!tasks.length) return null;
  const done = tasks.filter((t) => t.status === "done").length;
  return (
    <div className={`my-3 rounded-2xl border ${live ? "border-sky-500/40" : "border-line"} bg-surface px-4 py-3 text-sm`}>
      <div className="mb-2 flex items-center gap-2 text-xs text-muted">
        <Clipboard className="size-3.5" /> Tarefas · {done}/{tasks.length} concluídas
        {live && <span className="ml-auto animate-pulse text-sky-300">em andamento</span>}
      </div>
      <ul className="space-y-1">
        {tasks.map((t, i) => (
          <li key={i} className="flex items-start gap-2">
            <span className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded border text-[10px] ${
              t.status === "done" ? "border-emerald-500 bg-emerald-600/80 text-white" : t.status === "doing" ? "border-sky-400 text-sky-300" : "border-line text-transparent"
            }`}>
              {t.status === "done" ? "✓" : t.status === "doing" ? "›" : ""}
            </span>
            <span className={t.status === "done" ? "text-muted line-through decoration-faint" : t.status === "doing" ? "text-fg" : "text-muted"}>
              {t.text}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const FILE_TOOLS = new Set(["write_file", "edit_file", "read_file"]);

export function ToolBlock(props: {
  call: ToolCall;
  result?: Message;
  approval?: Approval;
  running: boolean;
  queued?: boolean; // uma chamada anterior da mesma resposta ainda não terminou
  live?: string; // saída ao vivo (run_command) enquanto não há resultado
  onOpen?: (path: string, mode: "editor" | "reveal") => void; // abrir no editor / revelar (via runner)
  onDecide: (approved: boolean, alwaysAllow?: boolean) => void;
  hideImages?: boolean; // screenshot desenhado fora do bloco (ver ActivityGroup)
  children?: React.ReactNode; // passos de um subagente (delegate_task)
}) {
  const { call, result, approval, running, queued } = props;
  const filePath = FILE_TOOLS.has(call.name) && typeof call.arguments.path === "string" ? (call.arguments.path as string) : null;
  const waiting = approval !== undefined && !result;
  const status = result?.status ?? (waiting ? "aguardando" : running ? (queued ? "fila" : "executando") : "pendente");
  const [open, setOpen] = useState(false);
  const [label, cls] = STATUS[status];
  const preview: Preview | undefined = approval?.preview ?? result?.meta?.preview ?? undefined;
  const a = call.arguments;
  const hint = (call.name === "delegate_task"
    ? `${a.level ?? ""} · ${a.task ?? ""}`
    : [a.path, a.command, a.query, a.url, a.selector, a.script].find((v) => typeof v === "string")) as string | undefined;
  const verb =
    {
      edit_file: "editar",
      write_file: "escrever",
      run_command: "executar um comando em",
      browser_click: "clicar em",
      browser_type: "digitar em",
      browser_eval: "executar JavaScript em",
    }[call.name] ?? `usar ${call.name}`;
  const target = preview?.path ?? (call.name.startsWith("browser_") ? hint : "");

  return (
    <div className={`my-2 overflow-hidden rounded-2xl border ${waiting ? "border-amber-500/50" : "border-line"} bg-surface`}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-raised/50">
        <span className="font-mono text-fg">{call.name}</span>
        <span className="truncate font-mono text-faint">{hint}</span>
        <span className={`ml-auto shrink-0 text-xs ${cls}`}>● {label}</span>
        <Chevron className="size-4 shrink-0 text-faint" />
      </button>

      {!result && props.live && (
        <pre className="max-h-48 overflow-auto border-t border-line bg-[#0d0d0d] px-3 py-2 font-mono text-[11px] whitespace-pre-wrap text-muted">
          {props.live}
        </pre>
      )}
      {!props.hideImages && result?.meta?.attachments && <ToolImages list={result.meta.attachments} />}
      {!props.hideImages && result?.meta?.attachments && (
        <div className="border-t border-line px-3 py-1">
          <ToolFiles list={result.meta.attachments} onOpen={props.onOpen} />
        </div>
      )}
      {props.children && <div className="border-t border-line px-3 py-2">{props.children}</div>}

      {waiting && !approval?.sent && (
        <div className="space-y-3 border-t border-line p-4">
          <div className="text-sm text-fg">
            O agente quer {verb} <span className="font-mono">{target}</span>
          </div>
          {preview ? (
            <DiffView preview={preview} />
          ) : (
            <pre className="max-h-64 overflow-auto rounded-xl border border-line bg-[#0d0d0d] p-3 font-mono text-xs whitespace-pre-wrap text-muted">
              {JSON.stringify(call.arguments, null, 2)}
            </pre>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => props.onDecide(true)}
              className="rounded-full bg-fg px-4 py-1.5 text-sm font-medium text-black hover:bg-white"
            >
              Aprovar
            </button>
            {approval?.suggest && (
              <button
                onClick={() => props.onDecide(true, true)}
                title={`Cria a regra "${approval.suggest}" em Configurações › Permissões`}
                className="inline-flex items-center gap-1.5 rounded-full border border-line px-4 py-1.5 text-sm text-fg hover:bg-raised"
              >
                <Shield className="size-3.5" /> Sempre permitir{" "}
                <span className="font-mono text-xs text-muted">{approval.suggest}</span>
              </button>
            )}
            <button
              onClick={() => props.onDecide(false)}
              className="rounded-full border border-line px-4 py-1.5 text-sm text-fg hover:bg-raised"
            >
              Rejeitar
            </button>
          </div>
        </div>
      )}

      {open && (
        <div className="space-y-2 border-t border-line p-4 text-xs">
          {filePath && props.onOpen && (
            <div className="flex gap-2">
              <button onClick={() => props.onOpen!(filePath, "editor")} className="rounded-full border border-line px-3 py-1 text-fg hover:bg-raised">
                Abrir no editor
              </button>
              <button onClick={() => props.onOpen!(filePath, "reveal")} className="rounded-full border border-line px-3 py-1 text-fg hover:bg-raised">
                Revelar na pasta
              </button>
            </div>
          )}
          <div className="text-faint">Argumentos</div>
          <pre className="max-h-64 overflow-auto rounded-lg bg-[#0d0d0d] p-2.5 font-mono whitespace-pre-wrap text-muted">
            {JSON.stringify(call.arguments, null, 2)}
          </pre>
          {preview && !waiting && <DiffView preview={preview} />}
          {result?.meta?.auto_rule && (
            <div className="text-amber-200">Aprovada automaticamente pela regra: {result.meta.auto_rule}</div>
          )}
          {result && (
            <>
              <div className="text-faint">Resultado</div>
              <pre className="max-h-64 overflow-auto rounded-lg bg-[#0d0d0d] p-2.5 font-mono whitespace-pre-wrap text-muted">
                {result.content}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export type ActivityPiece =
  | { kind: "thinking"; id: string; text: string }
  | { kind: "tool"; id: string; call: ToolCall };

// Primeira ferramenta do grupo vira a frase de abertura do resumo.
const ACTION: Record<string, string> = {
  run_command: "Executou um comando",
  read_file: "Leu um arquivo",
  edit_file: "Editou um arquivo",
  write_file: "Escreveu um arquivo",
  list_dir: "Olhou a pasta",
  search: "Procurou no projeto",
  web_search: "Pesquisou na web",
  fetch_url: "Abriu uma página",
  delegate_task: "Delegou a um subagente",
  update_tasks: "Atualizou as tarefas",
  ask_user: "Perguntou ao usuário",
};

export type ActivitySeg =
  | { kind: "group"; items: ActivityPiece[] }
  | { kind: "plan"; call: ToolCall }
  | { kind: "question"; call: ToolCall }
  | { kind: "text" };

/**
 * Divide as respostas do agente em segmentos por mensagem: texto e planos aparecem sempre; raciocínio
 * e ferramentas viram um grupo condensado, que pode atravessar várias iterações sem texto no meio.
 * O grupo é desenhado na mensagem onde começou.
 */
export function groupActivity(messages: Message[]): Map<number, ActivitySeg[]> {
  const out = new Map<number, ActivitySeg[]>();
  const push = (i: number, s: ActivitySeg) => out.set(i, [...(out.get(i) ?? []), s]);
  let cur: ActivityPiece[] = [];
  let at = -1; // mensagem onde o grupo aberto será desenhado
  const flush = () => {
    if (cur.length) push(at, { kind: "group", items: cur });
    cur = [];
    at = -1;
  };
  const open = (i: number) => {
    if (at < 0) at = i;
  };
  messages.forEach((m, i) => {
    if (m.role === "tool") return; // resultado é desenhado dentro do bloco da ferramenta, não corta o grupo
    if (m.role !== "assistant") return flush();
    if (m.thinking) {
      open(i);
      cur.push({ kind: "thinking", id: `think-${m.id}`, text: m.thinking });
    }
    if (m.content) {
      flush();
      push(i, { kind: "text" });
    }
    for (const c of m.tool_calls ?? []) {
      if (c.name === "exit_plan_mode") {
        flush();
        push(i, { kind: "plan", call: c }); // plano nunca fica escondido: é decisão do usuário
        continue;
      }
      if (c.name === "ask_user") {
        flush();
        push(i, { kind: "question", call: c }); // idem: pergunta para o usuário fica sempre visível
        continue;
      }
      open(i);
      cur.push({ kind: "tool", id: c.id, call: c });
    }
  });
  flush();
  return out;
}

/**
 * Condensador: raciocínio + ferramentas de um trecho viram uma linha só, entre as falas do agente.
 * Fica aberto enquanto o turno roda ou quando há aprovação pendente; depois colapsa.
 */
export function ActivityGroup(props: {
  items: ActivityPiece[];
  results: Map<string, Message>;
  live?: boolean;
  forceOpen?: boolean;
  renderTool: (call: ToolCall, queued: boolean) => React.ReactNode;
  onOpen?: (path: string, mode: "editor" | "reveal") => void;
}) {
  const [open, setOpen] = useState(false);
  const isOpen = !!props.forceOpen || open;
  const tools = props.items.flatMap((p) => (p.kind === "tool" ? [p.call] : []));
  const fails = tools.filter((c) => {
    const st = props.results.get(c.id)?.status;
    return st === "erro" || st === "rejeitada";
  }).length;
  const head = tools.length ? (ACTION[tools[0].name] ?? `Usou ${tools[0].name}`) : "Raciocinou";
  // Screenshot e arquivo gerado são resposta, não detalhe de execução: saem do grupo e ficam
  // visíveis mesmo com ele colapsado.
  const shots = tools.flatMap((c) => props.results.get(c.id)?.meta?.attachments ?? []);
  const summary =
    (props.live && !tools.length ? "Trabalhando" : tools.length > 1 ? `${head}, usou ${tools.length} ferramentas` : head) +
    (fails ? ` (${fails} falha${fails > 1 ? "s" : ""})` : "") +
    (props.live ? "…" : "");

  return (
    <div className="my-3">
      <button
        onClick={() => setOpen(!isOpen)}
        className="flex items-center gap-1.5 text-sm text-faint transition-colors hover:text-muted"
      >
        <span className={props.live ? "animate-pulse" : ""}>{summary}</span>
        <ChevronDown className={`size-3.5 transition-transform ${isOpen ? "" : "-rotate-90"}`} />
      </button>
      {isOpen && (
        <div className="mt-1 border-l border-line pl-3">
          {props.items.map((p, k) =>
            p.kind === "thinking" ? (
              <Thinking key={p.id} text={p.text} />
            ) : (
              <div key={p.id}>
                {props.renderTool(
                  p.call,
                  props.items.slice(0, k).some((q) => q.kind === "tool" && !props.results.has(q.id)),
                )}
              </div>
            ),
          )}
        </div>
      )}
      <ToolImages list={shots} bare />
      <ToolFiles list={shots} onOpen={props.onOpen} />
    </div>
  );
}

const EVENT_STYLE: Record<string, string> = {
  warning: "border-amber-500/30 text-amber-200",
  error: "border-red-500/30 text-red-200",
  nudge: "border-sky-500/30 text-sky-200",
  info: "border-line text-muted",
};

export function EventNotice({ m }: { m: Message }) {
  const kind = m.meta?.kind ?? "info";
  if (kind === "tasks") return <TasksCard tasks={m.meta?.tasks ?? []} />;
  if (kind === "summary")
    return (
      <details className="my-3 rounded-2xl border border-line bg-surface px-4 py-2.5 text-sm text-muted">
        <summary className="cursor-pointer select-none">
          Contexto compactado: o histórico anterior foi resumido para caber na janela do modelo
        </summary>
        <div className="mt-2 border-t border-line pt-2">
          <Markdown text={m.content} />
        </div>
      </details>
    );
  const title = { warning: "Aviso", error: "Erro", nudge: "Lembrete automático ao modelo", info: "Info" }[kind as string];
  return (
    <div className={`my-3 rounded-2xl border bg-surface px-4 py-2.5 text-sm ${EVENT_STYLE[kind] ?? EVENT_STYLE.info}`}>
      <span className="font-medium">{title}:</span> {m.content}
    </div>
  );
}


type SubStep = { call: ToolCall; result?: Message };

const SUB_LEVELS: Record<string, string> = { rapido: "Rápido", capaz: "Capaz", nuvem: "Nuvem" };

/** Passos de um subagente, desenhados dentro do bloco do delegate_task (aprovações inclusas). */
export function SubagentSteps(props: {
  info?: {
    level: string;
    model: string;
    provider?: string;
    tokens?: number;
    seconds?: number;
    iterations?: number;
    fallback?: string;
    verify?: { command: string; status: string };
    review?: string;
  };
  status?: string;
  steps: SubStep[];
  approvals: Record<string, Approval>;
  running: boolean;
  onDecide: (callId: string, approved: boolean, alwaysAllow?: boolean) => void;
}) {
  const { info } = props;
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-x-3 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5">
          <Split className="size-3.5" /> subagente{info ? ` ${SUB_LEVELS[info.level] ?? info.level} · ${info.model}` : ""}
        </span>
        {info?.tokens != null && info.seconds != null && (
          <span className="text-faint">
            {info.tokens.toLocaleString("pt-BR")} tokens · {info.seconds}s · {props.steps.length} passos
            {info.iterations ? ` · ${info.iterations} iterações` : ""}
            {info.provider ? ` · ${info.provider}` : ""}
          </span>
        )}
        {props.status && <span className="animate-pulse text-sky-300">{props.status}</span>}
      </div>
      {info?.fallback && <div className="text-xs text-amber-200">{info.fallback}</div>}
      {info?.verify && (
        <div className={`text-xs ${info.verify.status === "ok" ? "text-emerald-300" : "text-rose-300"}`}>
          {info.verify.status === "ok" ? "✓" : "✗"} verificação: <span className="font-mono">{info.verify.command}</span>
        </div>
      )}
      {info?.review && (
        <div className="whitespace-pre-wrap rounded-xl border border-line bg-raised/40 px-2.5 py-1.5 text-xs text-muted">
          <div className="text-faint">revisão do diff (não bloqueante)</div>
          {info.review}
        </div>
      )}
      {props.steps.map((st, k) => (
        <ToolBlock
          key={st.call.id}
          call={st.call}
          result={st.result}
          approval={props.approvals[st.call.id]}
          running={props.running}
          queued={props.steps.slice(0, k).some((p) => !p.result)}
          onDecide={(ok, always) => props.onDecide(st.call.id, ok, always)}
        />
      ))}
    </div>
  );
}


// Modelo fraco escreve rótulo e explicação na mesma linha ("JWT (Recomendado) - escalável para APIs").
const SEPARADOR = /\s+[—–-]\s+|:\s+/;

/** Rótulo e explicação de uma opção, separando quando o modelo mandou tudo no rótulo. */
function parteOpcao(o: { label: string; description?: string }) {
  if (o.description || o.label.length <= 40) return o;
  const m = SEPARADOR.exec(o.label);
  if (!m) return o;
  return { label: o.label.slice(0, m.index), description: o.label.slice(m.index + m[0].length) };
}

/** Argumentos de um ask_user salvo no histórico, no formato novo (questions[]) ou no antigo. */
export function askQuestions(args: Record<string, unknown>): AskQuestion[] {
  const raw = Array.isArray(args.questions) ? args.questions : [{ question: args.question, options: args.options }];
  return raw
    .filter((q: any) => q && typeof q.question === "string" && q.question.trim())
    .slice(0, 4)
    .map((q: any) => ({
      header: typeof q.header === "string" ? q.header : undefined,
      question: q.question,
      multi_select: !!q.multi_select,
      options: (Array.isArray(q.options) ? q.options : [])
        .slice(0, 4)
        .map((o: any) => (typeof o === "string" ? { label: o } : { label: String(o?.label ?? ""), description: o?.description }))
        .filter((o: { label: string }) => o.label),
    }));
}

/**
 * Perguntas do agente (ask_user): até 4 numa chamada só, uma por tela (1/2, 2/2), com opções
 * descritas, múltipla escolha quando o agente pedir e sempre um campo de resposta livre.
 */
export function QuestionCard(props: { questions: AskQuestion[]; done?: Message; onAnswer: (answers: string[]) => void }) {
  // Defensivo: pergunta sem options (ou lista vazia) tem que renderizar o campo livre, nunca quebrar o card.
  const qs: AskQuestion[] = (props.questions?.length ? props.questions : [{ question: "", options: [] }]).map((x) => ({
    ...x,
    options: x.options ?? [],
  }));
  const [at, setAt] = useState(0);
  const [picked, setPicked] = useState<string[][]>(() => qs.map(() => []));
  const [texts, setTexts] = useState<string[]>(() => qs.map(() => ""));
  const livreRef = useRef<HTMLTextAreaElement>(null);
  const decided = props.done?.status;
  const meta = props.done?.meta ?? {};
  const answers = (meta.answers ?? (meta.answer ? [meta.answer] : [])) as string[];

  const q = qs[Math.min(at, qs.length - 1)];
  const sel = picked[at] ?? [];
  const livre = (texts[at] ?? "").trim();
  const last = at === qs.length - 1;
  const primary = "rounded-full bg-fg px-4 py-1.5 text-sm font-medium text-black hover:bg-white disabled:opacity-40";
  const secondary = "rounded-full border border-line px-4 py-1.5 text-sm text-fg hover:bg-raised";

  function toggle(label: string) {
    setPicked((p) =>
      p.map((v, i) =>
        i !== at ? v : q.multi_select ? (v.includes(label) ? v.filter((x) => x !== label) : [...v, label]) : [label]),
    );
  }

  /** Avança ou envia. `skip` descarta o que foi marcado nesta pergunta. */
  function advance(skip: boolean) {
    const nextPicked = skip ? picked.map((v, i) => (i === at ? [] : v)) : picked;
    const nextTexts = skip ? texts.map((v, i) => (i === at ? "" : v)) : texts;
    if (skip) {
      setPicked(nextPicked);
      setTexts(nextTexts);
    }
    if (!last) return setAt(at + 1);
    props.onAnswer(qs.map((_, i) => [...(nextPicked[i] ?? []), ...((nextTexts[i] ?? "").trim() ? [nextTexts[i].trim()] : [])].join(", ")));
  }

  return (
    <div className={`my-3 overflow-hidden rounded-2xl border ${decided ? "border-line" : "border-amber-500/50"} bg-surface`}>
      <div className="flex items-start gap-2 border-b border-line px-4 py-2.5 text-sm">
        {decided ? (
          <span className="text-fg">{qs.length > 1 ? "Perguntas do agente" : "Pergunta do agente"}</span>
        ) : (
          <>
            <span className="mt-0.5 shrink-0 font-mono text-[11px] text-amber-300">
              {at + 1}/{qs.length}
            </span>
            <span className="font-medium text-fg">{q.question}</span>
            {q.header && <span className="ml-auto shrink-0 pl-2 text-xs text-faint">{q.header}</span>}
          </>
        )}
        {decided && (
          <span className={`ml-auto text-xs ${decided === "ok" ? "text-emerald-400" : "text-orange-400"}`}>
            ● {decided === "ok" ? "respondida" : decided}
          </span>
        )}
      </div>
      {decided ? (
        <div className="divide-y divide-line">
          {qs.map((item, i) => (
            <div key={i} className="px-4 py-2.5 text-sm">
              <div className="text-fg">{item.question}</div>
              <div className="text-muted">
                Resposta: <span className="text-fg">{answers[i] || "(sem resposta)"}</span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="space-y-2 p-4">
            {q.options.map((o, i) => {
              const on = sel.includes(o.label);
              const vis = parteOpcao(o);
              return (
                <button
                  key={i}
                  onClick={() => toggle(o.label)}
                  className={`flex w-full items-start gap-3 rounded-xl border px-3 py-2.5 text-left ${on ? "border-fg/50 bg-raised" : "border-line hover:bg-raised/60"}`}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm text-fg">{vis.label}</span>
                    {vis.description && <span className="mt-0.5 block text-xs text-muted">{vis.description}</span>}
                  </span>
                  <span
                    className={`mt-0.5 grid size-4 shrink-0 place-items-center ${q.multi_select ? "rounded" : "rounded-full"} border text-[10px] ${on ? "border-fg bg-fg font-bold text-black" : "border-line text-faint"}`}
                  >
                    {on ? "✓" : i + 1}
                  </span>
                </button>
              );
            })}
            {!!q.options.length && (
              <button
                onClick={() => livreRef.current?.focus()}
                className="flex w-full items-center gap-3 rounded-xl border border-line px-3 py-2.5 text-left hover:bg-raised/60"
              >
                <span className="flex-1 text-sm text-fg">Outro</span>
                <span className="grid size-4 shrink-0 place-items-center rounded-full border border-line text-[10px] text-faint">
                  {q.options.length + 1}
                </span>
              </button>
            )}
            <textarea
              ref={livreRef}
              rows={1}
              value={texts[at] ?? ""}
              onChange={(e) => setTexts((t) => t.map((v, i) => (i === at ? e.target.value : v)))}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (sel.length || e.currentTarget.value.trim()) advance(false);
                }
              }}
              placeholder="Digite sua própria resposta aqui"
              className="w-full rounded-xl border border-line bg-raised px-3 py-2 text-sm text-fg focus:outline-none"
            />
            <div className="flex items-center gap-2 pt-1">
              {at > 0 && (
                <button onClick={() => setAt(at - 1)} className={secondary}>
                  Voltar
                </button>
              )}
              <button onClick={() => advance(true)} className={`${secondary} ml-auto`}>
                Pular
              </button>
              <button disabled={!sel.length && !livre} onClick={() => advance(false)} className={primary}>
                {last ? "Enviar" : "Próximo"}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}


/** Plano proposto no modo Plano: aprovar (escolhendo o modo de execução) ou pedir mudanças. */
export function PlanCard(props: {
  plan: string;
  done?: Message; // resultado, quando o turno já terminou
  onDecide: (approved: boolean, mode?: string, feedback?: string) => void;
}) {
  const [mode, setMode] = useState("edits");
  const [feedback, setFeedback] = useState("");
  const [asking, setAsking] = useState(false);
  const decided = props.done?.status;

  return (
    <div className={`my-3 overflow-hidden rounded-2xl border ${decided ? "border-line" : "border-sky-500/50"} bg-surface`}>
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5 text-sm">
        <Clipboard className="size-4 text-sky-300" />
        <span className="text-fg">Plano proposto</span>
        {decided && (
          <span className={`ml-auto text-xs ${decided === "ok" ? "text-emerald-400" : "text-orange-400"}`}>
            ● {decided === "ok" ? `aprovado (${props.done?.meta?.approved_mode ?? "executando"})` : "ajustes pedidos"}
          </span>
        )}
      </div>
      <div className="px-4 py-3">
        <Markdown text={props.plan} />
      </div>
      {!decided && (
        <div className="space-y-2 border-t border-line p-4">
          {asking ? (
            <>
              <textarea
                autoFocus
                rows={3}
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="O que mudar no plano?"
                className="w-full rounded-xl border border-line bg-raised px-3 py-2 text-sm text-fg focus:outline-none"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => props.onDecide(false, undefined, feedback)}
                  className="rounded-full bg-fg px-4 py-1.5 text-sm font-medium text-black hover:bg-white"
                >
                  Enviar observações
                </button>
                <button onClick={() => setAsking(false)} className="rounded-full border border-line px-4 py-1.5 text-sm text-fg hover:bg-raised">
                  Voltar
                </button>
              </div>
            </>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => props.onDecide(true, mode)}
                className="rounded-full bg-fg px-4 py-1.5 text-sm font-medium text-black hover:bg-white"
              >
                Aprovar e executar
              </button>
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value)}
                title="Modo de permissão para executar o plano"
                className="rounded-full border border-line bg-transparent px-3 py-1.5 text-xs text-muted"
              >
                <option value="edits" className="bg-surface">no modo Aceitar edições</option>
                <option value="auto" className="bg-surface">no modo Automático</option>
                <option value="manual" className="bg-surface">no modo Manual</option>
                <option value="bypass" className="bg-surface">ignorando permissões</option>
              </select>
              <button onClick={() => setAsking(true)} className="rounded-full border border-line px-4 py-1.5 text-sm text-fg hover:bg-raised">
                Pedir mudanças
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Message, Stats, ToolCall } from "../types";
import { Search, X } from "./icons";

/**
 * Visão Trajetória da conversa, no molde da aba Trajectory do DeepSeek Harness: uma linha do tempo em
 * três faixas (entrada, modelo, ferramentas) com a duração real de cada passo, a lista crua de eventos
 * e, ao clicar num evento, o detalhe dele (resumo, payload, resultado, schema da ferramenta, tempo).
 * Serve para entender por que o agente fez o que fez sem abrir bloco por bloco.
 */

type Tipo = "usuario" | "assistente" | "ferramenta" | "compactado" | "contexto" | "aviso";
type Linha = {
  id: string;
  tipo: Tipo;
  rotulo: string;         // o que aparece no selo
  texto: string;          // uma linha
  resultado?: string;     // ferramenta: "→ resultado"
  turno: number;
  passo?: number;
  segundos?: number;      // duração (modelo: stats.seconds; ferramenta: meta.segundos)
  status?: string;
  msg: Message;
  call?: ToolCall;
  res?: Message;
};
type ToolInfo = { name: string; description: string; parameters?: unknown };

const SELO: Record<Tipo, string> = {
  usuario: "text-sky-300 bg-sky-500/10",
  assistente: "text-fg/80 bg-raised",
  ferramenta: "text-amber-300 bg-amber-500/10",
  compactado: "text-violet-300 bg-violet-500/10",
  contexto: "text-emerald-300 bg-emerald-500/10",
  aviso: "text-red-300 bg-red-500/10",
};
const umaLinha = (s: string) => s.replace(/\s+/g, " ").trim();
const fmtSeg = (s?: number) => (s == null ? "" : s >= 60 ? `${Math.floor(s / 60)}m${Math.round(s % 60)}s` : `${s.toFixed(1)}s`);

function montaLinhas(messages: Message[]): Linha[] {
  const resultados = new Map(messages.filter((m) => m.role === "tool").map((m) => [m.tool_call_id, m]));
  const out: Linha[] = [];
  let turno = 0;
  let passo = 0;
  for (const m of messages) {
    const kind = String(m.meta?.kind ?? "");
    if (m.role === "user") {
      turno += 1;
      out.push({ id: `m${m.id}`, tipo: "usuario", rotulo: "USUÁRIO", texto: umaLinha(m.content), turno, msg: m });
    } else if (m.role === "event") {
      if (kind === "tasks") continue;
      const tipo: Tipo = kind === "summary" ? "compactado" : kind === "warning" || kind === "error" ? "aviso" : "contexto";
      const rotulo = kind === "summary" ? "COMPACTADO" : tipo === "aviso" ? (kind === "error" ? "ERRO" : "AVISO") : "CONTEXTO";
      out.push({ id: `m${m.id}`, tipo, rotulo, texto: umaLinha(m.content), turno, msg: m });
    } else if (m.role === "assistant") {
      passo += 1;
      const s = m.meta?.stats as Stats | undefined;
      out.push({
        id: `m${m.id}`, tipo: "assistente", rotulo: "ASSISTENTE", turno, passo, segundos: s?.seconds, msg: m,
        texto: umaLinha(m.content || m.thinking || (m.tool_calls?.length ? "(só chamadas de ferramenta)" : "")),
      });
      for (const c of m.tool_calls ?? []) {
        const res = resultados.get(c.id);
        out.push({
          id: `c${c.id}`, tipo: "ferramenta", rotulo: "FERRAMENTA", turno, passo, msg: m, call: c, res,
          texto: `${c.name} ${JSON.stringify(c.arguments)}`, resultado: res ? umaLinha(res.content) : "…",
          segundos: res?.meta?.segundos != null ? Number(res.meta.segundos) : undefined, status: res?.status ?? "rodando",
        });
      }
    }
  }
  return out;
}

export default function Trajetoria({ messages }: { messages: Message[] }) {
  const [porDuracao, setPorDuracao] = useState(true);
  const [comTurnos, setComTurnos] = useState(false);
  const [comChamadas, setComChamadas] = useState(true);
  const [busca, setBusca] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [ferramentas, setFerramentas] = useState<Record<string, ToolInfo>>({});
  useEffect(() => {
    api.get<ToolInfo[]>("/tools").then((ts) => setFerramentas(Object.fromEntries(ts.map((t) => [t.name, t])))).catch(() => {});
  }, []);

  const todas = useMemo(() => montaLinhas(messages), [messages]);
  const visiveis = todas.filter(
    (l) => (comChamadas || l.tipo !== "ferramenta") && (!busca || `${l.texto} ${l.resultado ?? ""}`.toLowerCase().includes(busca.toLowerCase())),
  );
  const escolhida = todas.find((l) => l.id === sel) ?? null;

  // Linha do tempo: passos do modelo e chamadas de ferramenta em sequência, largura pela duração.
  const { segmentos, posicoes, marcasEntrada } = useMemo(() => {
    const segs = todas.filter((l) => l.tipo === "assistente" || l.tipo === "ferramenta");
    const peso = (l: Linha) => (porDuracao ? Math.max(0.3, l.segundos ?? 0.3) : 1);
    const total = segs.reduce((n, l) => n + peso(l), 0) || 1;
    const pos = new Map<string, { esq: number; larg: number }>();
    segs.reduce((acc, l) => {
      pos.set(l.id, { esq: (acc / total) * 100, larg: (peso(l) / total) * 100 });
      return acc + peso(l);
    }, 0);
    // Entrada (mensagem do usuário, contexto) marca onde começa o próximo passo do modelo.
    const marcas = todas.flatMap((l, i) => {
      if (l.tipo !== "usuario" && l.tipo !== "contexto" && l.tipo !== "compactado") return [];
      const prox = todas.slice(i + 1).find((x) => pos.has(x.id));
      return [{ l, esq: prox ? pos.get(prox.id)!.esq : 100 }];
    });
    return { segmentos: segs, posicoes: pos, marcasEntrada: marcas };
  }, [todas, porDuracao]);

  const botao = (ativo: boolean) =>
    `rounded-md px-2 py-0.5 text-xs ${ativo ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"}`;

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        {/* barra: modos da linha do tempo e busca */}
        <div className="flex items-center gap-1 border-b border-line px-3 py-1.5">
          <button className={botao(porDuracao)} onClick={() => setPorDuracao((v) => !v)} title="Largura de cada passo pela duração real (desligado: todos iguais)">
            Duração
          </button>
          <button className={botao(comTurnos)} onClick={() => setComTurnos((v) => !v)} title="Separar a lista por turno do usuário">
            Turnos
          </button>
          <button className={botao(comChamadas)} onClick={() => setComChamadas((v) => !v)} title="Mostrar as chamadas de ferramenta na lista">
            Chamadas
          </button>
          <label className="ml-auto flex w-56 items-center gap-1.5 rounded-md border border-line px-2 py-0.5 text-xs text-muted">
            <Search className="size-3.5" />
            <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar" className="min-w-0 flex-1 bg-transparent text-fg placeholder:text-faint focus:outline-none" />
          </label>
        </div>

        {/* linha do tempo em três faixas */}
        <div className="grid grid-cols-[auto_1fr] gap-x-2 border-b border-line px-3 py-2 font-mono text-[10px] text-faint">
          {(["Entrada", "Modelo", "Ferramentas"] as const).map((faixa) => (
            <div key={faixa} className="contents">
              <span className="text-right leading-4">{faixa}</span>
              <div className="relative h-4">
                {faixa === "Entrada" &&
                  marcasEntrada.map(({ l, esq }) => (
                    <button key={l.id} onClick={() => setSel(l.id)} title={l.texto}
                      className={`absolute top-0.5 h-3 w-0.5 rounded-sm ${l.tipo === "usuario" ? "bg-sky-400" : "bg-emerald-400/70"}`}
                      style={{ left: `${esq}%` }} />
                  ))}
                {faixa !== "Entrada" &&
                  segmentos
                    .filter((l) => (faixa === "Modelo" ? l.tipo === "assistente" : l.tipo === "ferramenta"))
                    .map((l) => {
                      const p = posicoes.get(l.id)!;
                      const cor = faixa === "Modelo" ? "bg-violet-400/60" : l.status && l.status !== "ok" && l.status !== "rodando" ? "bg-red-400/80" : "bg-amber-400/80";
                      return (
                        <button key={l.id} onClick={() => setSel(l.id)} title={`${l.texto.slice(0, 120)} · ${fmtSeg(l.segundos)}`}
                          className={`absolute top-0.5 h-3 rounded-sm ${cor} ${sel === l.id ? "ring-1 ring-fg" : ""}`}
                          style={{ left: `${p.esq}%`, width: `max(2px, calc(${p.larg}% - 1px))` }} />
                      );
                    })}
              </div>
            </div>
          ))}
        </div>

        {/* lista crua de eventos */}
        <div className="min-h-0 flex-1 overflow-y-auto font-mono text-xs">
          {!visiveis.length && <div className="p-4 text-faint">Nada para mostrar.</div>}
          {visiveis.map((l, i) => (
            <div key={l.id}>
              {comTurnos && l.tipo === "usuario" && (i === 0 || visiveis[i - 1].turno !== l.turno) && (
                <div className="border-t border-line px-3 pt-2 pb-0.5 text-[10px] text-faint">Turno {l.turno}</div>
              )}
              <button
                onClick={() => setSel(l.id === sel ? null : l.id)}
                className={`flex w-full items-center gap-3 border-b border-line/50 px-3 py-1.5 text-left ${sel === l.id ? "bg-raised" : "hover:bg-raised/40"}`}
              >
                <span className={`w-24 shrink-0 rounded px-1.5 py-0.5 text-center text-[10px] font-semibold ${SELO[l.tipo]}`}>{l.rotulo}</span>
                <span className="min-w-0 flex-1 truncate text-fg/85">
                  {l.texto}
                  {l.resultado != null && <span className="text-faint">{"  →  "}{l.resultado}</span>}
                </span>
                {l.segundos != null && <span className="shrink-0 text-faint">{fmtSeg(l.segundos)}</span>}
              </button>
            </div>
          ))}
        </div>
      </div>
      {escolhida && (
        <Detalhe key={escolhida.id} linha={escolhida} ferramenta={escolhida.call ? ferramentas[escolhida.call.name] : undefined} onFechar={() => setSel(null)} />
      )}
    </div>
  );
}

const ABAS = ["Resumo", "Payload", "Resultado", "Schema", "Tempo"] as const;

function Detalhe({ linha, ferramenta, onFechar }: { linha: Linha; ferramenta?: ToolInfo; onFechar: () => void }) {
  const [aba, setAba] = useState<(typeof ABAS)[number]>("Resumo");
  const s = linha.tipo === "assistente" ? (linha.msg.meta?.stats as Stats | undefined) : undefined;
  const payload =
    linha.call ? JSON.stringify(linha.call.arguments, null, 2)
    : linha.tipo === "assistente" ? [linha.msg.thinking && `raciocínio:\n${linha.msg.thinking}`, linha.msg.content].filter(Boolean).join("\n\n")
    : linha.msg.content;
  const resultado = linha.res?.content ?? (linha.call ? "(ainda rodando)" : "");
  const inicio = (linha.res ?? linha.msg).created_at;
  const hierarquia = linha.call ? `Mensagem do assistente › ${linha.call.name}` : linha.rotulo.toLowerCase();
  const status = linha.call ? (linha.status === "ok" ? "Concluída" : linha.status) : "Concluída";
  const campo = (rotulo: string, valor: React.ReactNode) => (
    <>
      <dt className="text-faint">{rotulo}</dt>
      <dd className="min-w-0 break-words text-fg/90">{valor}</dd>
    </>
  );
  const bloco = (texto: string) => (
    <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-bg p-2 text-[11px] text-fg/85">{texto || "—"}</pre>
  );
  return (
    <aside className="flex w-[26rem] shrink-0 flex-col border-l border-line bg-surface">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2 text-xs">
        <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ${SELO[linha.tipo]}`}>{linha.rotulo}</span>
        <span className="text-muted">Turno {linha.turno}{linha.passo ? ` · Passo ${linha.passo}` : ""}</span>
        <button onClick={onFechar} className="ml-auto rounded p-1 text-faint hover:bg-raised hover:text-fg" aria-label="Fechar detalhe">
          <X className="size-3.5" />
        </button>
      </div>
      <div className="flex gap-1 border-b border-line px-2 py-1 text-xs">
        {ABAS.map((a) => (
          <button key={a} onClick={() => setAba(a)} className={`rounded-md px-2 py-1 ${aba === a ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>
            {a}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3 font-mono text-xs">
        {aba === "Resumo" && (
          <div className="space-y-3">
            <dl className="grid grid-cols-[6.5rem_1fr] gap-x-2 gap-y-1">
              {campo("Hierarquia", hierarquia)}
              {campo("Status", status)}
              {s && campo("Modelo", s.model)}
              {s && campo("Tokens", `entrada ${s.prompt_tokens}${s.cached != null ? ` (cache ${s.cached})` : ""} · saída ${s.tokens}`)}
            </dl>
            <div><div className="mb-1 text-faint">Payload</div>{bloco(payload.slice(0, 1200))}</div>
            {linha.call && <div><div className="mb-1 text-faint">Resultado</div>{bloco(resultado.slice(0, 1200))}</div>}
            {ferramenta && (
              <div>
                <div className="mb-1 text-faint">Schema</div>
                <div className="text-fg/90">{ferramenta.name}</div>
                <div className="text-muted">{ferramenta.description.slice(0, 300)}</div>
              </div>
            )}
            <dl className="grid grid-cols-[6.5rem_1fr] gap-x-2 gap-y-1">
              {inicio && campo("Início", new Date(inicio).toLocaleString("pt-BR"))}
              {linha.segundos != null && campo("Duração", fmtSeg(linha.segundos))}
            </dl>
          </div>
        )}
        {aba === "Payload" && bloco(payload)}
        {aba === "Resultado" && bloco(linha.call ? resultado : "(só chamadas de ferramenta têm resultado)")}
        {aba === "Schema" &&
          (ferramenta ? (
            <div className="space-y-2">
              <div className="text-fg">{ferramenta.name}</div>
              <div className="whitespace-pre-wrap text-muted">{ferramenta.description}</div>
              <div className="text-faint">Parâmetros</div>
              {bloco(JSON.stringify(ferramenta.parameters ?? {}, null, 2))}
            </div>
          ) : (
            <div className="text-faint">{linha.call ? "Ferramenta fora do catálogo (MCP desligado ou ferramenta de modo)." : "Só chamadas de ferramenta têm schema."}</div>
          ))}
        {aba === "Tempo" && (
          <dl className="grid grid-cols-[6.5rem_1fr] gap-x-2 gap-y-1">
            {campo("Início", inicio ? new Date(inicio).toLocaleString("pt-BR") : "—")}
            {campo("Duração", fmtSeg(linha.segundos) || "—")}
            {s?.tps != null && campo("Velocidade", `${s.tps.toFixed(1)} tokens/s`)}
          </dl>
        )}
      </div>
    </aside>
  );
}

import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Modal } from "./Modal";

/** Board de issues do projeto (E15-A): Novo → Backlog → Em andamento → Revisão → Concluído. */

type Evidencia = { arquivo?: string; linha?: number; trecho?: string; comando?: string; saida?: string; imagem?: string };
export type Issue = {
  id: number; projeto: string; titulo: string; descricao: string;
  tipo: string; area: string; severidade: number; status: string;
  evidencias: Evidencia[]; prompt: string; verify_sugerido: string; origem: string;
  motivo_rejeicao: string | null; sumiu: boolean; conversa_id: number | null; commit: string | null;
  historico: { quando: string; texto: string }[]; modo_sugerido: "agent" | "maestro"; updated_at: string;
};
type Varredura = { rodando: boolean; etapa?: string; criados: number; encontrados: number; avisos: string[] } | null;

const COLUNAS = [
  { id: "novo", nome: "Novo" }, { id: "backlog", nome: "Backlog" }, { id: "andamento", nome: "Em andamento" },
  { id: "revisao", nome: "Revisão" }, { id: "concluido", nome: "Concluído" },
];
const TIPOS = ["bugfix", "feature", "improvement", "visual", "todo", "seguranca"];
const AREAS = ["frontend", "backend", "fullstack", "testes", "infra"];
const COR_TIPO: Record<string, string> = {
  bugfix: "bg-red-500/15 text-red-300", feature: "bg-sky-500/15 text-sky-300",
  improvement: "bg-emerald-500/15 text-emerald-300", visual: "bg-fuchsia-500/15 text-fuchsia-300",
  todo: "bg-amber-500/15 text-amber-200", seguranca: "bg-orange-500/20 text-orange-300",
};
const NOME_TIPO: Record<string, string> = { bugfix: "bug", feature: "feature", improvement: "melhoria", visual: "visual",
  todo: "todo", seguranca: "segurança" };
const MOTIVOS = [{ id: "nao_e_bug", nome: "Não é bug" }, { id: "nao_quero", nome: "Não quero" }, { id: "duplicado", nome: "Duplicado" }];
const input = "w-full rounded-lg border border-line bg-raised px-3 py-1.5 text-sm text-fg focus:border-[#555] focus:outline-none";
const sel = "rounded-lg border border-line bg-raised px-2 py-1 text-xs text-fg focus:outline-none";
const btn = "rounded-full border border-line px-3 py-1 text-xs text-fg hover:bg-raised disabled:opacity-40";
const btnPrimary = "rounded-full bg-fg px-3 py-1 text-xs font-medium text-black hover:bg-white disabled:opacity-40";

export default function BoardView(props: {
  pasta: string | null;          // pasta da conversa aberta: o board abre no projeto dela
  carimbo?: string;              // activity.board: mudou, recarrega (outro aparelho, varredura, conversa terminou)
  onClose: () => void;
  onAbrirConversa: (id: number) => void;
}) {
  const [projetos, setProjetos] = useState<{ projeto: string; nome: string }[]>([]);
  const [pasta, setPasta] = useState<string | null>(props.pasta);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [varredura, setVarredura] = useState<Varredura>(null);
  const [comandos, setComandos] = useState<Record<string, string>>({});
  const [erro, setErro] = useState("");
  const [busca, setBusca] = useState("");
  const [filtro, setFiltro] = useState({ tipo: "", area: "", sev: "" });
  const [rejeitados, setRejeitados] = useState(false);
  const [aberto, setAberto] = useState<number | "novo" | null>(null);
  const [alvo, setAlvo] = useState<string | null>(null);  // coluna sob o card arrastado

  useEffect(() => {
    api.get<{ projeto: string; nome: string }[]>("/board/projetos").then((l) => {
      setProjetos(l);
      setPasta((p) => p ?? l[0]?.projeto ?? null);
    }).catch((e) => setErro(e.message));
  }, []);

  const carregar = () => {
    if (!pasta) return;
    api.get<{ issues: Issue[]; varredura: Varredura; comandos: Record<string, string>; projeto: string }>(
      `/board?pasta=${encodeURIComponent(pasta)}`)
      .then((r) => { setIssues(r.issues); setVarredura(r.varredura); setComandos(r.comandos); })
      .catch((e) => setErro(e.message));
  };
  useEffect(carregar, [pasta, props.carimbo]);
  // Varredura rodando: o carimbo só muda no fim; o progresso (etapa, contagem) vem daqui.
  useEffect(() => {
    if (!varredura?.rodando) return;
    const t = setInterval(carregar, 2000);
    return () => clearInterval(t);
  }, [varredura?.rodando, pasta]);

  const acao = async (fn: () => Promise<unknown>) => {
    setErro("");  // o recarregar de depois não limpa: senão o erro da ação sumia na hora
    try { await fn(); } catch (e: any) { setErro(e.message); }
    carregar();
  };
  const mover = (i: Issue, status: string) => {
    if (status === i.status) return;
    if (status === "andamento") return acao(() => api.post(`/board/issues/${i.id}/iniciar`, {}));
    return acao(() => api.patch(`/board/issues/${i.id}`, { status }));
  };

  /** Segurar e arrastar o card para outra coluna (mesmo gesto dos painéis do Maestro, RightPanel.alca):
   * o card levanta e segue o ponteiro, a coluna de destino acende. Soltar em "Em andamento" = Iniciar. */
  const arrasta = (i: Issue) => (e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    const el = e.currentTarget, x0 = e.clientX, y0 = e.clientY, estilo = el.style.cssText;
    let ativo = false, destino: string | null = null;
    const colunaEm = (x: number, y: number) =>
      document.elementsFromPoint(x, y).map((n) => (n as HTMLElement).closest<HTMLElement>("[data-coluna]")).find(Boolean)
        ?.dataset.coluna ?? null;
    const mexe = (ev: PointerEvent) => {
      const dx = ev.clientX - x0, dy = ev.clientY - y0;
      if (!ativo) {
        if (Math.hypot(dx, dy) < 6) return;  // clique comum abre o card; só vira arrasto passando de 6 px
        ativo = true;
        document.body.style.userSelect = "none";
        // fixed, não relative: a coluna rola (overflow) e cortaria o card assim que ele saísse dela
        const r = el.getBoundingClientRect();
        el.style.cssText = estilo + `;position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;`
          + "z-index:60;pointer-events:none;opacity:.94;scale:1.03;"
          + "transition:scale .15s ease-out;box-shadow:0 28px 60px -12px rgb(0 0 0/.65),0 0 0 1px rgb(56 189 248/.55);";
      }
      el.style.setProperty("translate", `${dx}px ${dy}px`);
      destino = colunaEm(ev.clientX, ev.clientY);
      setAlvo(destino && destino !== i.status ? destino : null);
    };
    const solta = () => {
      window.removeEventListener("pointermove", mexe);
      window.removeEventListener("pointerup", solta);
      window.removeEventListener("pointercancel", solta);
      if (!ativo) return;
      el.style.cssText = estilo;
      document.body.style.userSelect = "";
      const engole = (c: MouseEvent) => c.stopPropagation();  // o clique de depois do arrasto não abre o card
      window.addEventListener("click", engole, { capture: true, once: true });
      setTimeout(() => window.removeEventListener("click", engole, { capture: true }), 0);
      setAlvo(null);
      if (destino) mover(i, destino);
    };
    window.addEventListener("pointermove", mexe);
    window.addEventListener("pointerup", solta);
    window.addEventListener("pointercancel", solta);
  };

  const visiveis = useMemo(() => issues.filter((i) =>
    (!filtro.tipo || i.tipo === filtro.tipo) && (!filtro.area || i.area === filtro.area)
    && (!filtro.sev || String(i.severidade) === filtro.sev)
    && (!busca || `${i.titulo} ${i.descricao} ${i.evidencias.map((e) => e.arquivo).join(" ")}`.toLowerCase()
      .includes(busca.toLowerCase()))), [issues, filtro, busca]);
  const card = typeof aberto === "number" ? issues.find((i) => i.id === aberto) : undefined;
  const colunas = rejeitados ? [...COLUNAS, { id: "rejeitado", nome: "Rejeitado" }] : COLUNAS;

  return (
    <Modal onClose={props.onClose} label="Board do projeto"
      className="flex h-[92vh] w-[96vw] max-w-[1600px] flex-col overflow-hidden rounded-2xl border border-line bg-bg">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <h2 className="mr-2 text-sm font-medium text-fg">Board</h2>
        <select className={sel} value={pasta ?? ""} onChange={(e) => setPasta(e.target.value)} aria-label="Projeto">
          {pasta && !projetos.some((p) => p.projeto === pasta) && <option value={pasta}>{pasta}</option>}
          {projetos.map((p) => <option key={p.projeto} value={p.projeto} title={p.projeto}>{p.nome}</option>)}
        </select>
        <input className={`${sel} w-48`} placeholder="Buscar…" value={busca} onChange={(e) => setBusca(e.target.value)} />
        <select className={sel} value={filtro.tipo} onChange={(e) => setFiltro({ ...filtro, tipo: e.target.value })} aria-label="Tipo">
          <option value="">todos os tipos</option>
          {TIPOS.map((t) => <option key={t} value={t}>{NOME_TIPO[t]}</option>)}
        </select>
        <select className={sel} value={filtro.area} onChange={(e) => setFiltro({ ...filtro, area: e.target.value })} aria-label="Área">
          <option value="">todas as áreas</option>
          {AREAS.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <select className={sel} value={filtro.sev} onChange={(e) => setFiltro({ ...filtro, sev: e.target.value })} aria-label="Severidade">
          <option value="">toda severidade</option>
          <option value="1">alta</option><option value="2">média</option><option value="3">baixa</option>
        </select>
        <label className="flex items-center gap-1 text-xs text-muted">
          <input type="checkbox" checked={rejeitados} onChange={(e) => setRejeitados(e.target.checked)} /> rejeitados
        </label>
        <div className="ml-auto flex items-center gap-2">
          {varredura?.rodando && <span className="text-xs text-muted">Varrendo: {varredura.etapa}… {varredura.encontrados} achados</span>}
          <button className={btn} disabled={!pasta || !!varredura?.rodando}
            title={Object.keys(comandos).length ? `TODOs, ${Object.values(comandos).join(", ")} e auditoria de dependências`
              : "TODOs e auditoria de dependências (ponha test_command/typecheck_command/lint_command no FORJA.md para rodar também)"}
            onClick={() => acao(() => api.post("/board/varrer", { pasta }))}>Varrer agora</button>
          <button className={btnPrimary} disabled={!pasta} onClick={() => setAberto("novo")}>+ Novo item</button>
          <button className={btn} onClick={props.onClose}>Fechar</button>
        </div>
      </div>
      {erro && <p className="border-b border-line px-4 py-2 text-xs text-red-400">{erro}</p>}
      {!varredura?.rodando && varredura && varredura.avisos.length > 0 && (
        <p className="border-b border-line px-4 py-2 text-xs text-muted">
          Última varredura: {varredura.criados} card(s) novo(s). {varredura.avisos.join(" ")}
        </p>
      )}
      {!pasta ? (
        <p className="p-8 text-sm text-muted">Abra uma conversa de agente ou Maestro numa pasta para ter um board.</p>
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 gap-3 overflow-x-auto p-4">
            {colunas.map((col) => {
              const itens = visiveis.filter((i) => i.status === col.id);
              return (
                <div key={col.id} data-coluna={col.id}
                  className={`flex w-72 shrink-0 flex-col rounded-xl bg-surface transition-shadow ${
                    alvo === col.id ? "ring-1 ring-sky-400/60 bg-sky-400/[0.04]" : ""}`}>
                  <div className="flex items-center justify-between px-3 py-2 text-xs text-muted">
                    <span className="font-medium text-fg">{col.nome}</span><span>{itens.length}</span>
                  </div>
                  <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2 pb-2">
                    {itens.map((i) => (
                      <button key={i.id} onPointerDown={arrasta(i)} onClick={() => setAberto(i.id)}
                        title="Clique para abrir; segure e arraste para mudar de coluna"
                        className={`block w-full cursor-grab rounded-lg border bg-bg p-2 text-left hover:border-[#555] active:cursor-grabbing ${aberto === i.id ? "border-[#666]" : "border-line"}`}>
                        <div className="text-sm text-fg">{i.titulo}</div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px]">
                          <span className={`rounded px-1.5 py-0.5 ${COR_TIPO[i.tipo] ?? ""}`}>{NOME_TIPO[i.tipo] ?? i.tipo}</span>
                          <span className="rounded bg-raised px-1.5 py-0.5 text-muted">{i.area}</span>
                          <span className="text-faint" title="severidade">{"●".repeat(4 - i.severidade)}</span>
                          {i.origem !== "manual" && <span className="text-faint">varredura</span>}
                          {i.sumiu && <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-300">resolvido?</span>}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
          {aberto === "novo" && pasta && (
            <Detalhe key="novo" pasta={pasta} onFechar={() => setAberto(null)} acao={acao}
              onCriado={(id) => setAberto(id)} onAbrirConversa={props.onAbrirConversa} />
          )}
          {card && (
            <Detalhe key={card.id} card={card} pasta={pasta!} onFechar={() => setAberto(null)} acao={acao}
              onAbrirConversa={props.onAbrirConversa} />
          )}
        </div>
      )}
    </Modal>
  );
}

function Detalhe(props: {
  card?: Issue; pasta: string; onFechar: () => void; acao: (fn: () => Promise<unknown>) => Promise<void>;
  onCriado?: (id: number) => void; onAbrirConversa: (id: number) => void;
}) {
  const c = props.card;
  const [f, setF] = useState({
    titulo: c?.titulo ?? "", descricao: c?.descricao ?? "", tipo: c?.tipo ?? "bugfix", area: c?.area ?? "backend",
    severidade: c?.severidade ?? 2, prompt: c?.prompt ?? "", verify_sugerido: c?.verify_sugerido ?? "",
  });
  const [modo, setModo] = useState<"agent" | "maestro">(c?.modo_sugerido ?? "agent");
  const [comentario, setComentario] = useState("");
  const [motivo, setMotivo] = useState("");
  const mudou = !c || (Object.keys(f) as (keyof typeof f)[]).some((k) => f[k] !== (c as any)[k]);
  const campo = (k: keyof typeof f) => ({ value: f[k] as any, onChange: (e: any) => setF({ ...f, [k]: k === "severidade" ? Number(e.target.value) : e.target.value }) });

  const salvar = () => props.acao(async () => {
    if (c) return api.patch(`/board/issues/${c.id}`, f);
    const novo = await api.post<Issue>("/board/issues", { ...f, pasta: props.pasta });
    props.onCriado?.(novo.id);
  });

  return (
    <aside className="flex w-[440px] shrink-0 flex-col overflow-y-auto border-l border-line p-4 text-sm">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs text-muted">{c ? `#${c.id} · ${c.origem}` : "Novo item"}</span>
        <button className="text-xs text-faint hover:text-fg" onClick={props.onFechar}>fechar</button>
      </div>
      <div className="space-y-2">
        <input className={input} placeholder="Título" {...campo("titulo")} />
        <div className="flex gap-2">
          <select className={sel} {...campo("tipo")} aria-label="Tipo">{TIPOS.map((t) => <option key={t} value={t}>{NOME_TIPO[t]}</option>)}</select>
          <select className={sel} {...campo("area")} aria-label="Área">{AREAS.map((a) => <option key={a} value={a}>{a}</option>)}</select>
          <select className={sel} {...campo("severidade")} aria-label="Severidade">
            <option value={1}>alta</option><option value={2}>média</option><option value={3}>baixa</option>
          </select>
        </div>
        <textarea className={`${input} h-20`} placeholder="Descrição" {...campo("descricao")} />
        <label className="block text-xs text-muted">Prompt para a IA (vazio: título, descrição e evidências)
          <textarea className={`${input} mt-1 h-28 font-mono text-xs`} {...campo("prompt")} />
        </label>
        <label className="block text-xs text-muted">Verify sugerido (comando que prova que ficou pronto)
          <input className={`${input} mt-1 font-mono text-xs`} placeholder="ex.: npm test" {...campo("verify_sugerido")} />
        </label>
        {mudou && <button className={btnPrimary} disabled={!f.titulo.trim()} onClick={salvar}>{c ? "Salvar" : "Criar"}</button>}
      </div>

      {c && (
        <>
          {c.evidencias.length > 0 && (
            <div className="mt-4 space-y-2">
              <h3 className="text-xs font-medium text-muted">Evidências</h3>
              {c.evidencias.map((e, k) => e.arquivo ? (
                <button key={k} className="block w-full rounded bg-raised px-2 py-1 text-left font-mono text-xs text-fg hover:bg-surface"
                  title="Abrir no editor" onClick={() => api.post("/open", { path: `${c.projeto}/${e.arquivo}`, line: e.linha ?? null })}>
                  {e.arquivo}{e.linha ? `:${e.linha}` : ""}
                  {e.trecho && <span className="block truncate text-muted">{e.trecho}</span>}
                </button>
              ) : e.saida ? (
                <pre key={k} className="max-h-48 overflow-auto rounded bg-raised p-2 text-[11px] text-muted">{e.comando ? `$ ${e.comando}\n` : ""}{e.saida}</pre>
              ) : null)}
            </div>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            {c.status === "novo" && (
              <button className={btnPrimary} onClick={() => props.acao(() => api.patch(`/board/issues/${c.id}`, { status: "backlog" }))}>Aceitar</button>
            )}
            {["novo", "backlog", "revisao", "concluido"].includes(c.status) && (
              <>
                {c.status !== "novo" && (
                  <select className={sel} value={modo} onChange={(e) => setModo(e.target.value as any)} aria-label="Modo">
                    <option value="agent">Agente{c.modo_sugerido === "agent" ? " (sugerido)" : ""}</option>
                    <option value="maestro">Maestro{c.modo_sugerido === "maestro" ? " (sugerido)" : ""}</option>
                  </select>
                )}
                {c.status === "backlog" && (
                  <button className={btnPrimary} onClick={() => props.acao(() => api.post(`/board/issues/${c.id}/iniciar`, { modo }))}>Iniciar</button>
                )}
              </>
            )}
            {c.status === "revisao" && (
              <button className={btnPrimary} onClick={() => props.acao(() => api.patch(`/board/issues/${c.id}`, { status: "concluido" }))}>Aprovar</button>
            )}
            {c.conversa_id && <button className={btn} onClick={() => props.onAbrirConversa(c.conversa_id!)}>Abrir conversa</button>}
            {c.status !== "rejeitado" && c.status !== "andamento" && (
              <span className="flex items-center gap-1">
                <select className={sel} value={motivo} onChange={(e) => setMotivo(e.target.value)} aria-label="Motivo">
                  <option value="">motivo…</option>
                  {MOTIVOS.map((m) => <option key={m.id} value={m.id}>{m.nome}</option>)}
                </select>
                <button className={btn} onClick={() => props.acao(() => api.post(`/board/issues/${c.id}/rejeitar`, { motivo: motivo || null }))}>Rejeitar</button>
              </span>
            )}
            {c.status === "rejeitado" && (
              <button className={btn} onClick={() => props.acao(() => api.patch(`/board/issues/${c.id}`, { status: "backlog" }))}>Voltar ao backlog</button>
            )}
          </div>
          {c.commit && <p className="mt-2 font-mono text-xs text-muted">commit {c.commit.slice(0, 10)}</p>}
          {c.status === "revisao" && (
            <div className="mt-3 space-y-2">
              <textarea className={`${input} h-16`} placeholder="O que falta? Vira a próxima mensagem da mesma conversa."
                value={comentario} onChange={(e) => setComentario(e.target.value)} />
              <button className={btn} disabled={!comentario.trim()}
                onClick={() => props.acao(() => api.post(`/board/issues/${c.id}/reabrir`, { comentario }))}>Reabrir</button>
            </div>
          )}
          <div className="mt-4">
            <h3 className="mb-1 text-xs font-medium text-muted">Histórico</h3>
            <ul className="space-y-1 text-xs text-muted">
              {[...c.historico].reverse().map((h, k) => (
                <li key={k}><span className="text-faint">{h.quando.replace("T", " ").slice(0, 16)}</span> {h.texto}</li>
              ))}
            </ul>
          </div>
          <button className="mt-4 self-start text-xs text-faint hover:text-red-400"
            onClick={() => props.acao(async () => { await api.del(`/board/issues/${c.id}`); props.onFechar(); })}>Apagar card</button>
        </>
      )}
    </aside>
  );
}

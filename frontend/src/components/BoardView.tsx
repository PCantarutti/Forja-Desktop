import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Modal } from "./Modal";
import { Check, ChevronDown, Code, Folder, Play, Plus, Quadro, Raio, Refresh, Search, X } from "./icons";

/** Board de issues do projeto (E15-A): Novo → Backlog → Em andamento → Revisão → Concluído.
 *
 * Layout: cabeçalho (projeto e as três ações), barra de filtros em chips, colunas e o detalhe à direita.
 * O card mostra ONDE está o problema (arquivo:linha) e tem a ação da coluna à mão (aceitar, rejeitar,
 * iniciar, aprovar), para a triagem não exigir abrir um por um. */

type Evidencia = { arquivo?: string; linha?: number; trecho?: string; comando?: string; saida?: string;
  imagem?: string; conv?: number; rotulo?: string };
export type Issue = {
  id: number; projeto: string; titulo: string; descricao: string;
  tipo: string; area: string; severidade: number; status: string;
  evidencias: Evidencia[]; prompt: string; verify_sugerido: string; origem: string;
  motivo_rejeicao: string | null; sumiu: boolean; conversa_id: number | null; commit: string | null;
  historico: { quando: string; texto: string }[]; modo_sugerido: "agent" | "maestro"; updated_at: string;
};
type Varredura = { rodando: boolean; etapa?: string; criados: number; encontrados: number; avisos: string[] } | null;

const COLUNAS = [
  { id: "novo", nome: "Novo", ponto: "bg-amber-400", vazio: "A varredura e a IA põem os achados aqui para você triar." },
  { id: "backlog", nome: "Backlog", ponto: "bg-neutral-400", vazio: "Aceite um card de Novo, ou crie um." },
  { id: "andamento", nome: "Em andamento", ponto: "bg-sky-400", vazio: "Solte um card aqui para iniciar." },
  { id: "revisao", nome: "Revisão", ponto: "bg-violet-400", vazio: "Cards cuja conversa terminou esperam você aqui." },
  { id: "concluido", nome: "Concluído", ponto: "bg-emerald-400", vazio: "Aprovados na revisão." },
];
const REJEITADO = { id: "rejeitado", nome: "Rejeitado", ponto: "bg-red-400", vazio: "Nada rejeitado." };
const TIPOS = ["bugfix", "feature", "improvement", "visual", "todo", "seguranca"];
const AREAS = ["frontend", "backend", "fullstack", "testes", "infra"];
const COR_TIPO: Record<string, { texto: string; ponto: string }> = {
  bugfix: { texto: "text-red-300", ponto: "bg-red-400" }, feature: { texto: "text-sky-300", ponto: "bg-sky-400" },
  improvement: { texto: "text-emerald-300", ponto: "bg-emerald-400" },
  visual: { texto: "text-fuchsia-300", ponto: "bg-fuchsia-400" }, todo: { texto: "text-amber-200", ponto: "bg-amber-300" },
  seguranca: { texto: "text-orange-300", ponto: "bg-orange-400" },
};
const NOME_TIPO: Record<string, string> = { bugfix: "Bug", feature: "Feature", improvement: "Melhoria", visual: "Visual",
  todo: "TODO", seguranca: "Segurança" };
const NOME_SEV: Record<number, string> = { 1: "Alta", 2: "Média", 3: "Baixa" };
const NOME_STATUS: Record<string, string> = { novo: "Novo", backlog: "Backlog", andamento: "Em andamento",
  revisao: "Revisão", concluido: "Concluído", rejeitado: "Rejeitado" };
const NOME_ORIGEM: Record<string, string> = { manual: "manual", "varredura-deterministica": "varredura",
  "varredura-ia": "IA", visual: "visual" };
const MOTIVOS = [{ id: "nao_e_bug", nome: "Não é bug" }, { id: "nao_quero", nome: "Não quero" }, { id: "duplicado", nome: "Duplicado" }];
const FOCOS = [{ id: "tudo", nome: "Tudo" }, { id: "bugs", nome: "Bugs" }, { id: "melhorias", nome: "Melhorias" },
  { id: "features", nome: "Ideias de feature" }, { id: "visual", nome: "Visual" }];
const nomePasta = (p: string) => p.split("/").filter(Boolean).pop() ?? p;

const campo = "rounded-lg border border-line bg-raised px-3 py-1.5 text-sm text-fg placeholder:text-faint "
  + "focus:border-neutral-500 focus:outline-none";
const btn = "inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1 text-xs text-fg transition-colors "
  + "hover:bg-raised focus-visible:outline focus-visible:outline-1 focus-visible:outline-sky-400 disabled:opacity-40";
const btnPrimary = "inline-flex items-center gap-1.5 rounded-full bg-fg px-3 py-1 text-xs font-medium text-black transition-colors "
  + "hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-400 disabled:opacity-40";
const icone = "rounded-md p-1 text-faint transition-colors hover:bg-raised hover:text-fg focus-visible:outline "
  + "focus-visible:outline-1 focus-visible:outline-sky-400";

/** Prioridade como três barras crescentes (alta = as três acesas). Legível de relance, sem texto. */
function Prioridade({ sev }: { sev: number }) {
  const acesas = 4 - sev;
  const cor = sev === 1 ? "bg-red-400" : sev === 2 ? "bg-amber-300" : "bg-neutral-400";
  return (
    <span className="inline-flex items-end gap-[2px]" title={`Prioridade ${NOME_SEV[sev]?.toLowerCase() ?? sev}`}
      aria-label={`Prioridade ${NOME_SEV[sev] ?? sev}`}>
      {[0, 1, 2].map((n) => (
        <span key={n} className={`w-[3px] rounded-sm ${n < acesas ? cor : "bg-neutral-700"}`}
          style={{ height: 5 + n * 3 }} />
      ))}
    </span>
  );
}

function Tipo({ tipo }: { tipo: string }) {
  const c = COR_TIPO[tipo] ?? { texto: "text-muted", ponto: "bg-neutral-400" };
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] font-medium ${c.texto}`}>
      <span className={`size-1.5 rounded-full ${c.ponto}`} />{NOME_TIPO[tipo] ?? tipo}
    </span>
  );
}

function Interruptor({ ligado, onMuda, rotulo, dica, disabled }: {
  ligado: boolean; onMuda: (v: boolean) => void; rotulo: string; dica: string; disabled?: boolean;
}) {
  return (
    <button role="switch" aria-checked={ligado} disabled={disabled} title={dica} onClick={() => onMuda(!ligado)}
      className="inline-flex items-center gap-2 rounded-full px-1 py-0.5 text-xs text-muted hover:text-fg disabled:opacity-40">
      <span className={`relative h-4 w-7 rounded-full transition-colors ${ligado ? "bg-sky-500" : "bg-neutral-700"}`}>
        <span className={`absolute top-0.5 size-3 rounded-full bg-white shadow transition-[left] duration-150 ${
          ligado ? "left-[14px]" : "left-0.5"}`} />
      </span>
      {rotulo}
    </button>
  );
}

function Chip({ ativo, onClick, children, titulo }: { ativo: boolean; onClick: () => void; children: React.ReactNode;
  titulo?: string }) {
  return (
    <button onClick={onClick} title={titulo} aria-pressed={ativo}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
        ativo ? "border-neutral-500 bg-raised text-fg" : "border-transparent text-muted hover:bg-raised hover:text-fg"}`}>
      {children}
    </button>
  );
}

const evidenciaDe = (i: { evidencias?: Evidencia[] }) => (i.evidencias ?? []).find((e) => e.arquivo);
const ondeDe = (e?: Evidencia | null) => (e?.arquivo ? `${e.arquivo}${e.linha ? `:${e.linha}` : ""}` : "");
const abrirCodigo = (projeto: string, e: Evidencia) =>
  api.post("/open", { path: `${projeto}/${e.arquivo}`, line: e.linha ?? null });
/** Resultado do verify que o Forja roda quando a conversa termina: está no histórico. */
const verifyDe = (i: Issue) => {
  const h = [...(i.historico ?? [])].reverse().find((x) => x.texto.startsWith("verify "));
  return !h ? null : h.texto.startsWith("verify passou") ? "ok" : "falhou";
};

export default function BoardView(props: {
  pasta: string | null;          // pasta da conversa aberta: o board abre no projeto dela
  carimbo?: string;              // activity.board: mudou, recarrega (outro aparelho, varredura, conversa terminou)
  foco?: number | null;          // card para abrir já no detalhe (veio do card na resposta da IA)
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
  const [filtro, setFiltro] = useState({ tipo: "", area: "", sev: 0 });
  const [rejeitados, setRejeitados] = useState(false);
  const [aberto, setAberto] = useState<number | "novo" | null>(props.foco ?? null);
  const [alvo, setAlvo] = useState<string | null>(null);  // coluna sob o card arrastado
  const [iaCria, setIaCria] = useState(true);   // board_card ligado neste board
  const [nVinc, setNVinc] = useState(0);
  const [painel, setPainel] = useState<"pedir" | "pastas" | null>(null);
  const [foco, setFoco] = useState("tudo");
  const [subpasta, setSubpasta] = useState("");
  const [pedido, setPedido] = useState<number | null>(null);  // conversa da IA procurando
  const [avisosVistos, setAvisosVistos] = useState(false);
  const [vinc, setVinc] = useState<{ projeto: string; vinculadas: string[]; sugestoes: string[] } | null>(null);
  const [novaPasta, setNovaPasta] = useState("");
  const carregaVinc = () => pasta && api.get<typeof vinc>(`/board/vinculos?pasta=${encodeURIComponent(pasta)}`)
    .then(setVinc).catch((e) => setErro(e.message));
  useEffect(() => { if (painel === "pastas") carregaVinc(); }, [painel, pasta]);

  useEffect(() => {
    api.get<{ projeto: string; nome: string }[]>("/board/projetos").then((l) => {
      setProjetos(l);
      setPasta((p) => p ?? l[0]?.projeto ?? null);
    }).catch((e) => setErro(e.message));
  }, []);

  const carregar = () => {
    if (!pasta) return;
    api.get<{ issues: Issue[]; varredura: Varredura; comandos: Record<string, string>; projeto: string;
      board_card: boolean; vinculadas: number }>(`/board?pasta=${encodeURIComponent(pasta)}`)
      .then((r) => { setIssues(r.issues); setVarredura(r.varredura); setComandos(r.comandos);
        setIaCria(r.board_card); setNVinc(r.vinculadas); })
      .catch((e) => setErro(e.message));
  };
  useEffect(carregar, [pasta, props.carimbo]);
  // Varredura rodando: o carimbo só muda no fim; o progresso (etapa, contagem) vem daqui.
  useEffect(() => {
    if (!varredura?.rodando) return;
    setAvisosVistos(false);
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
  const arrasta = (i: Issue) => (e: React.PointerEvent<HTMLElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("[data-acao]")) return;
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
          + "z-index:60;pointer-events:none;opacity:.95;scale:1.03;"
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
    && (!filtro.sev || i.severidade === filtro.sev)
    && (!busca || `${i.titulo} ${i.descricao} ${i.evidencias.map((e) => e.arquivo).join(" ")}`.toLowerCase()
      .includes(busca.toLowerCase()))), [issues, filtro, busca]);
  const card = typeof aberto === "number" ? issues.find((i) => i.id === aberto) : undefined;
  const colunas = rejeitados ? [...COLUNAS, REJEITADO] : COLUNAS;
  const filtrando = !!(filtro.tipo || filtro.area || filtro.sev || busca);
  const nomeProjeto = pasta ? (projetos.find((p) => p.projeto === pasta)?.nome ?? nomePasta(pasta)) : "";

  return (
    <Modal onClose={props.onClose} label="Board do projeto"
      // 90% do fundo do modal, não vw/vh: com o zoom da interface o 100vw passava da tela e cortava a
      // coluna Concluído e os botões da direita (o usuário viu cortado mesmo com 100% do fundo).
      className="flex h-[90%] w-[90%] flex-col overflow-hidden rounded-2xl border border-line bg-bg shadow-[0_30px_80px_-20px_rgb(0_0_0/.7)]">
      {/* Cabeçalho: onde estou (projeto, pastas) e as três ações do board */}
      <header className="flex flex-wrap items-center gap-3 px-5 pb-3 pt-4">
        <Quadro className="size-4 text-muted" />
        <h2 className="text-[15px] font-semibold tracking-tight text-fg">Board</h2>
        <label className="relative inline-flex items-center">
          <Folder className="pointer-events-none absolute left-2.5 size-3.5 text-faint" />
          <select value={pasta ?? ""} onChange={(e) => { setPasta(e.target.value); setAberto(null); }} aria-label="Projeto"
            title={pasta ?? ""}
            className="appearance-none rounded-full border border-line bg-surface py-1 pl-8 pr-7 text-xs font-medium text-fg hover:bg-raised focus:outline-none">
            {pasta && !projetos.some((p) => p.projeto === pasta) && <option value={pasta}>{nomePasta(pasta)}</option>}
            {projetos.map((p) => <option key={p.projeto} value={p.projeto}>{p.nome}</option>)}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2.5 size-3 text-faint" />
        </label>
        <button className={`${btn} ${painel === "pastas" ? "bg-raised" : ""}`} disabled={!pasta}
          onClick={() => setPainel((v) => (v === "pastas" ? null : "pastas"))}
          title="Pastas ligadas a este board (ex.: back-end e front-end em repositórios separados)">
          {nVinc ? `+${nVinc} pasta${nVinc > 1 ? "s" : ""}` : "Vincular pastas"}
        </button>
        <div className="ml-auto flex items-center gap-2">
          {varredura?.rodando && (
            <span className="inline-flex items-center gap-1.5 text-xs text-muted" aria-live="polite">
              <span className="size-1.5 animate-pulse rounded-full bg-sky-400" />
              Varrendo {varredura.etapa}… {varredura.encontrados} achado{varredura.encontrados === 1 ? "" : "s"}
            </span>
          )}
          <button className={btn} disabled={!pasta || !!varredura?.rodando}
            title={Object.keys(comandos).length ? `TODOs, ${Object.values(comandos).join(", ")} e auditoria de dependências`
              : "TODOs e auditoria de dependências. Ponha test_command/typecheck_command/lint_command no FORJA.md para rodar também."}
            onClick={() => acao(() => api.post("/board/varrer", { pasta }))}>
            <Refresh className="size-3.5" /> Varrer
          </button>
          <button className={`${btn} ${painel === "pedir" ? "bg-raised" : ""}`} disabled={!pasta}
            onClick={() => setPainel((v) => (v === "pedir" ? null : "pedir"))}
            title="Uma IA lê o projeto e cria os cards que confirmar, com arquivo e linha (caem em Novo)">
            <Raio className="size-3.5" /> Pedir à IA
          </button>
          <button className={btnPrimary} disabled={!pasta} onClick={() => setAberto("novo")}>
            <Plus className="size-3.5" /> Novo card
          </button>
          <span className="mx-1 h-5 w-px bg-line" />
          <button className={icone} onClick={props.onClose} title="Fechar (Esc)" aria-label="Fechar"><X className="size-4" /></button>
        </div>
      </header>

      {/* Filtros: um clique, sem abrir menu */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-5 pb-3">
        <label className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
          <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar título ou arquivo"
            className="w-56 rounded-full border border-line bg-surface py-1 pl-8 pr-3 text-xs text-fg placeholder:text-faint focus:border-neutral-500 focus:outline-none" />
        </label>
        <div className="flex items-center gap-0.5" role="group" aria-label="Tipo">
          {TIPOS.map((t) => (
            <Chip key={t} ativo={filtro.tipo === t} onClick={() => setFiltro({ ...filtro, tipo: filtro.tipo === t ? "" : t })}>
              <span className={`size-1.5 rounded-full ${COR_TIPO[t].ponto}`} />{NOME_TIPO[t]}
            </Chip>
          ))}
        </div>
        <span className="h-4 w-px bg-line" />
        <div className="flex items-center gap-0.5" role="group" aria-label="Prioridade">
          {[1, 2, 3].map((s) => (
            <Chip key={s} ativo={filtro.sev === s} onClick={() => setFiltro({ ...filtro, sev: filtro.sev === s ? 0 : s })}>
              <Prioridade sev={s} />{NOME_SEV[s]}
            </Chip>
          ))}
        </div>
        <span className="h-4 w-px bg-line" />
        <label className="relative inline-flex items-center">
          <select value={filtro.area} onChange={(e) => setFiltro({ ...filtro, area: e.target.value })} aria-label="Área"
            className={`appearance-none rounded-full border py-0.5 pl-2.5 pr-6 text-xs focus:outline-none ${
              filtro.area ? "border-neutral-500 bg-raised text-fg" : "border-transparent bg-transparent text-muted hover:bg-raised"}`}>
            <option value="">Toda área</option>
            {AREAS.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 size-3 text-faint" />
        </label>
        {filtrando && (
          <button className="text-xs text-faint underline-offset-2 hover:text-fg hover:underline"
            onClick={() => { setFiltro({ tipo: "", area: "", sev: 0 }); setBusca(""); }}>limpar filtros</button>
        )}
        <div className="ml-auto flex items-center gap-3">
          <Interruptor ligado={rejeitados} onMuda={setRejeitados} rotulo="Rejeitados" dica="Mostrar a coluna dos rejeitados" />
          <Interruptor ligado={iaCria} disabled={!pasta} rotulo="IA cria cards sozinha"
            dica="Ligado: em qualquer conversa deste projeto o agente pode criar cards (board_card), que caem em Novo. Desligado: a ferramenta nem aparece para o modelo; só /board e o Pedir à IA criam cards."
            onMuda={(v) => { setIaCria(v); acao(() => api.post("/board/ia", { pasta, ligado: v })); }} />
        </div>
      </div>

      {/* Painéis que abrem no lugar (sem outro modal): Pedir à IA e pastas vinculadas */}
      {painel === "pedir" && pasta && (
        <section className="flex flex-wrap items-center gap-3 border-b border-line bg-surface/60 px-5 py-3">
          <div className="min-w-0">
            <p className="text-sm text-fg">Pedir à IA para procurar</p>
            <p className="text-xs text-muted">Ela só lê o projeto e cria os cards que confirmar, com arquivo e linha.</p>
          </div>
          <div className="flex items-center gap-0.5" role="radiogroup" aria-label="Foco">
            {FOCOS.map((f) => <Chip key={f.id} ativo={foco === f.id} onClick={() => setFoco(f.id)}>{f.nome}</Chip>)}
          </div>
          <input className={`${campo} w-52 py-1 text-xs`} placeholder="Só na subpasta (opcional)" value={subpasta}
            onChange={(e) => setSubpasta(e.target.value)} />
          <div className="ml-auto flex items-center gap-2">
            <button className={btn} onClick={() => setPainel(null)}>Cancelar</button>
            <button className={btnPrimary} onClick={() => acao(async () => {
              const r = await api.post<{ conversa_id: number }>("/board/pedir", { pasta, foco, subpasta });
              setPedido(r.conversa_id);
              setPainel(null);
            })}><Raio className="size-3.5" /> Começar</button>
          </div>
        </section>
      )}
      {painel === "pastas" && vinc && (
        <section className="space-y-3 border-b border-line bg-surface/60 px-5 py-3 text-xs text-muted">
          <p>
            Conversa aberta numa pasta ligada, ou numa subpasta dela, usa este board, e a varredura passa por todas.
            Board de <span className="font-mono text-fg">{vinc.projeto}</span>.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {vinc.vinculadas.map((v) => (
              <span key={v} title={v}
                className="inline-flex items-center gap-1.5 rounded-full border border-line bg-bg py-0.5 pl-2.5 pr-1 text-fg">
                <Folder className="size-3 text-faint" />{nomePasta(v)}
                <button className="rounded-full p-0.5 text-faint hover:bg-raised hover:text-red-300" title="Desvincular (volta a ter board próprio)"
                  aria-label={`Desvincular ${nomePasta(v)}`}
                  onClick={() => acao(async () => { await api.del(`/board/vinculos?pasta=${encodeURIComponent(v)}`); await carregaVinc(); })}>
                  <X className="size-3" />
                </button>
              </span>
            ))}
            {!vinc.vinculadas.length && <span>Nenhuma pasta ligada ainda.</span>}
          </div>
          {vinc.sugestoes.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-fg">Repositórios dentro do projeto:</span>
              {vinc.sugestoes.map((v) => (
                <button key={v} className={btn} title={v}
                  onClick={() => acao(async () => { setVinc(await api.post("/board/vinculos", { pasta_board: pasta, pasta: v })); })}>
                  <Plus className="size-3" /> {nomePasta(v)}
                </button>
              ))}
              <button className={btnPrimary} onClick={() => acao(async () => {
                let r = vinc;
                for (const v of vinc.sugestoes) r = await api.post("/board/vinculos", { pasta_board: pasta, pasta: v });
                setVinc(r);
              })}>Vincular todos</button>
            </div>
          )}
          <div className="flex items-center gap-2">
            <input className={`${campo} w-96 py-1 font-mono text-xs`} placeholder="C:/caminho/de/outra/pasta" value={novaPasta}
              onChange={(e) => setNovaPasta(e.target.value)} />
            <button className={btn} disabled={!novaPasta.trim()} onClick={() => acao(async () => {
              setVinc(await api.post("/board/vinculos", { pasta_board: pasta, pasta: novaPasta.trim() }));
              setNovaPasta("");
            })}>Vincular pasta</button>
            <button className={`${btn} ml-auto`} onClick={() => setPainel(null)}>Fechar</button>
          </div>
        </section>
      )}

      {/* Avisos: erro, IA procurando, resultado da varredura */}
      {erro && (
        <p role="alert" className="flex items-center gap-2 border-b border-red-500/20 bg-red-500/[0.06] px-5 py-2 text-xs text-red-200">
          {erro}<button className="ml-auto text-red-300/70 hover:text-red-200" onClick={() => setErro("")} aria-label="Dispensar"><X className="size-3.5" /></button>
        </p>
      )}
      {pedido !== null && (
        <p className="flex items-center gap-2 border-b border-sky-500/20 bg-sky-500/[0.06] px-5 py-2 text-xs text-sky-100">
          <span className="size-1.5 animate-pulse rounded-full bg-sky-400" />
          A IA está procurando. Os cards aparecem em Novo conforme ela confirma.
          <button className="underline underline-offset-2 hover:text-white" onClick={() => props.onAbrirConversa(pedido)}>Ver a conversa</button>
          <button className="ml-auto text-sky-200/70 hover:text-white" onClick={() => setPedido(null)} aria-label="Dispensar"><X className="size-3.5" /></button>
        </p>
      )}
      {!varredura?.rodando && varredura && varredura.avisos.length > 0 && !avisosVistos && (
        <p className="flex items-center gap-2 border-b border-line px-5 py-2 text-xs text-muted">
          <span className="text-fg">Varredura: {varredura.criados} card{varredura.criados === 1 ? "" : "s"} novo{varredura.criados === 1 ? "" : "s"}.</span>
          {varredura.avisos.join(" ")}
          <button className="ml-auto text-faint hover:text-fg" onClick={() => setAvisosVistos(true)} aria-label="Dispensar"><X className="size-3.5" /></button>
        </p>
      )}

      {!pasta ? (
        <div className="grid flex-1 place-items-center p-8 text-center">
          <div className="max-w-sm space-y-2">
            <Quadro className="mx-auto size-6 text-faint" />
            <p className="text-sm text-fg">Nenhum projeto ainda</p>
            <p className="text-xs text-muted">O board é por projeto: abra uma conversa de Agente ou Maestro numa pasta e volte aqui.</p>
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 gap-3 overflow-x-auto px-5 py-4">
            {colunas.map((col) => {
              const itens = visiveis.filter((i) => i.status === col.id);
              const acesa = alvo === col.id;
              return (
                <section key={col.id} data-coluna={col.id} aria-label={col.nome}
                  className={`flex min-w-[250px] flex-1 flex-col rounded-xl border transition-colors ${
                    acesa ? "border-sky-400/60 bg-sky-400/[0.05]" : col.id === "novo" ? "border-amber-400/15 bg-surface" : "border-transparent bg-surface/70"}`}>
                  <div className="flex items-center gap-2 px-3 pb-2 pt-3">
                    <span className={`size-2 rounded-full ${col.ponto}`} />
                    <h3 className="text-[13px] font-medium text-fg">{col.nome}</h3>
                    {col.id === "novo" && itens.length > 0 && <span className="text-[11px] text-amber-200/70">para triar</span>}
                    <span className="ml-auto rounded-full bg-raised px-2 py-0.5 text-[11px] tabular-nums text-muted">{itens.length}</span>
                  </div>
                  <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2 pb-2">
                    {itens.map((i) => (
                      <CardDoBoard key={i.id} i={i} aberto={aberto === i.id} onAbrir={() => setAberto(i.id)}
                        onPointerDown={arrasta(i)} acao={acao} />
                    ))}
                    {!itens.length && (
                      <p className={`mx-1 rounded-lg border border-dashed px-3 py-6 text-center text-xs ${
                        acesa ? "border-sky-400/50 text-sky-200" : "border-line text-faint"}`}>
                        {acesa ? "Solte aqui" : filtrando && issues.some((x) => x.status === col.id) ? "Nada com esses filtros." : col.vazio}
                      </p>
                    )}
                  </div>
                </section>
              );
            })}
          </div>
          {aberto === "novo" && pasta && (
            <Detalhe key="novo" pasta={pasta} nomeProjeto={nomeProjeto} onFechar={() => setAberto(null)} acao={acao}
              onCriado={(id) => setAberto(id)} onAbrirConversa={props.onAbrirConversa} />
          )}
          {card && (
            <Detalhe key={card.id} card={card} pasta={pasta!} nomeProjeto={nomeProjeto} onFechar={() => setAberto(null)}
              acao={acao} onAbrirConversa={props.onAbrirConversa} />
          )}
        </div>
      )}
    </Modal>
  );
}

/** Card da coluna: o que é, onde está, e a ação daquela coluna a um clique (aparece ao passar o mouse). */
function CardDoBoard({ i, aberto, onAbrir, onPointerDown, acao }: {
  i: Issue; aberto: boolean; onAbrir: () => void; onPointerDown: (e: React.PointerEvent<HTMLElement>) => void;
  acao: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const ev = evidenciaDe(i);
  const verify = verifyDe(i);
  const acoes: { rotulo: string; icone: React.ReactNode; fn: () => Promise<unknown>; perigo?: boolean }[] =
    i.status === "novo" ? [
      { rotulo: "Aceitar", icone: <Check className="size-3.5" />, fn: () => api.patch(`/board/issues/${i.id}`, { status: "backlog" }) },
      { rotulo: "Rejeitar", icone: <X className="size-3.5" />, fn: () => api.post(`/board/issues/${i.id}/rejeitar`, { motivo: null }), perigo: true },
    ] : i.status === "backlog" ? [
      { rotulo: `Iniciar (${i.modo_sugerido === "maestro" ? "Maestro" : "agente"})`, icone: <Play className="size-3.5" />,
        fn: () => api.post(`/board/issues/${i.id}/iniciar`, {}) },
    ] : i.status === "revisao" ? [
      { rotulo: "Aprovar", icone: <Check className="size-3.5" />, fn: () => api.patch(`/board/issues/${i.id}`, { status: "concluido" }) },
    ] : [];
  return (
    <article onPointerDown={onPointerDown} onClick={onAbrir} tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onAbrir(); } }}
      title="Clique para abrir; segure e arraste para mudar de coluna"
      className={`group relative cursor-grab rounded-lg border bg-bg p-3 transition-colors active:cursor-grabbing focus-visible:outline focus-visible:outline-1 focus-visible:outline-sky-400 ${
        aberto ? "border-neutral-500" : "border-line hover:border-neutral-600"}`}>
      <div className="flex items-center gap-2">
        <Tipo tipo={i.tipo} />
        <span className="text-[11px] text-faint">{i.area}</span>
        <span className="ml-auto flex items-center gap-2 text-[11px] tabular-nums text-faint">
          <Prioridade sev={i.severidade} />#{i.id}
        </span>
      </div>
      <h4 className="mt-1.5 line-clamp-2 text-[13px] leading-snug text-fg">{i.titulo}</h4>
      {ev && (
        <p className="mt-1.5 flex items-center gap-1 truncate font-mono text-[11px] text-faint" title={ev.trecho ?? ondeDe(ev)}>
          <Code className="size-3 shrink-0" /><span className="truncate">{ondeDe(ev)}</span>
        </p>
      )}
      {(i.origem !== "manual" || i.sumiu || verify || i.commit || acoes.length > 0) && (
        <div className="mt-2 flex min-h-6 flex-wrap items-center gap-1.5 pr-16 text-[11px]">
          {i.origem !== "manual" && <span className="text-faint" title="Quem criou o card">{NOME_ORIGEM[i.origem] ?? i.origem}</span>}
          {i.sumiu && <span className="rounded bg-emerald-500/15 px-1.5 py-px text-emerald-300" title="A última varredura não achou mais">resolvido?</span>}
          {verify && (
            <span className={`rounded px-1.5 py-px ${verify === "ok" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>
              verify {verify === "ok" ? "passou" : "falhou"}
            </span>
          )}
          {i.commit && <span className="font-mono text-faint" title="Commit do card">{i.commit.slice(0, 7)}</span>}
        </div>
      )}
      {acoes.length > 0 && (
        <div data-acao className="absolute bottom-2 right-2 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          {acoes.map((a) => (
            <button key={a.rotulo} title={a.rotulo} aria-label={a.rotulo}
              onClick={(e) => { e.stopPropagation(); acao(a.fn); }}
              className={`grid size-6 place-items-center rounded-md border border-line bg-surface shadow-sm transition-colors ${
                a.perigo ? "text-muted hover:border-red-400/50 hover:text-red-300" : "text-fg hover:border-neutral-500 hover:bg-raised"}`}>
              {a.icone}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}

const SELETORES: { k: "tipo" | "area" | "severidade"; rotulo: string; ops: [string | number, string][] }[] = [
  { k: "tipo", rotulo: "Tipo", ops: TIPOS.map((t) => [t, NOME_TIPO[t]]) },
  { k: "area", rotulo: "Área", ops: AREAS.map((a) => [a, a]) },
  { k: "severidade", rotulo: "Prioridade", ops: [1, 2, 3].map((s) => [s, `Prioridade ${NOME_SEV[s].toLowerCase()}`]) },
];

function Secao({ titulo, children, acao }: { titulo: string; children: React.ReactNode; acao?: React.ReactNode }) {
  return (
    <section className="space-y-2 border-t border-line px-5 py-4">
      <div className="flex items-center"><h4 className="text-xs font-medium text-muted">{titulo}</h4>{acao}</div>
      {children}
    </section>
  );
}

function Detalhe(props: {
  card?: Issue; pasta: string; nomeProjeto: string; onFechar: () => void;
  acao: (fn: () => Promise<unknown>) => Promise<void>;
  onCriado?: (id: number) => void; onAbrirConversa: (id: number) => void;
}) {
  const c = props.card;
  const inicial = {
    titulo: c?.titulo ?? "", descricao: c?.descricao ?? "", tipo: c?.tipo ?? "bugfix", area: c?.area ?? "backend",
    severidade: c?.severidade ?? 2, prompt: c?.prompt ?? "", verify_sugerido: c?.verify_sugerido ?? "",
  };
  const [f, setF] = useState(inicial);
  const [modo, setModo] = useState<"agent" | "maestro">(c?.modo_sugerido ?? "agent");
  const [comentario, setComentario] = useState("");
  const [rejeitando, setRejeitando] = useState(false);
  const [apagar, setApagar] = useState(false);
  const mudou = !c || (Object.keys(f) as (keyof typeof f)[]).some((k) => f[k] !== (c as any)[k]);
  const muda = (k: keyof typeof f) => ({
    value: f[k] as any,
    onChange: (e: any) => setF({ ...f, [k]: k === "severidade" ? Number(e.target.value) : e.target.value }),
  });
  const salvar = () => props.acao(async () => {
    if (c) return api.patch(`/board/issues/${c.id}`, f);
    const novo = await api.post<Issue>("/board/issues", { ...f, pasta: props.pasta });
    props.onCriado?.(novo.id);
  });
  const status = (s: string) => props.acao(() => api.patch(`/board/issues/${c!.id}`, { status: s }));
  const seletor = "appearance-none rounded-full border border-line bg-surface py-0.5 pl-2.5 pr-6 text-xs text-fg hover:bg-raised focus:outline-none";

  return (
    <aside className="flex w-[460px] shrink-0 flex-col border-l border-line bg-surface/40" aria-label={c ? `Card ${c.id}` : "Novo card"}>
      <div className="flex items-center gap-2 px-5 pt-4 text-xs text-faint">
        {c ? (
          <>
            <span className="tabular-nums">#{c.id}</span><span>·</span><span>{NOME_STATUS[c.status] ?? c.status}</span>
            <span>·</span><span>{NOME_ORIGEM[c.origem] ?? c.origem}</span>
          </>
        ) : <span>Novo card em {props.nomeProjeto}</span>}
        <button className={`${icone} ml-auto`} onClick={props.onFechar} aria-label="Fechar o card"><X className="size-4" /></button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="space-y-3 px-5 pb-4 pt-2">
          <textarea rows={1} style={{ fieldSizing: "content" } as React.CSSProperties} value={f.titulo} onChange={muda("titulo").onChange} placeholder="Título do card"
            aria-label="Título"
            className="w-full resize-none rounded-md border border-transparent bg-transparent px-1 py-0.5 text-[15px] font-semibold leading-snug text-fg placeholder:text-faint hover:border-line focus:border-neutral-500 focus:outline-none" />
          <div className="flex flex-wrap items-center gap-1.5">
            {SELETORES.map(({ k, rotulo, ops }) => (
              <label key={k} className="relative inline-flex items-center">
                <select className={seletor} aria-label={rotulo} {...muda(k)}>
                  {ops.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                </select>
                <ChevronDown className="pointer-events-none absolute right-2 size-3 text-faint" />
              </label>
            ))}
          </div>

          {/* A próxima ação do card fica no topo, não no fim do formulário */}
          {c && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              {c.status === "novo" && (
                <button className={btnPrimary} onClick={() => status("backlog")}><Check className="size-3.5" /> Aceitar</button>
              )}
              {c.status === "backlog" && (
                <>
                  <div className="inline-flex rounded-full border border-line p-0.5" role="radiogroup" aria-label="Modo">
                    {(["agent", "maestro"] as const).map((m) => (
                      <button key={m} role="radio" aria-checked={modo === m} onClick={() => setModo(m)}
                        title={c.modo_sugerido === m ? "Sugerido para este card" : undefined}
                        className={`rounded-full px-2.5 py-0.5 text-xs transition-colors ${modo === m ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>
                        {m === "agent" ? "Agente" : "Maestro"}{c.modo_sugerido === m ? " ·" : ""}
                      </button>
                    ))}
                  </div>
                  <button className={btnPrimary} onClick={() => props.acao(() => api.post(`/board/issues/${c.id}/iniciar`, { modo }))}>
                    <Play className="size-3.5" /> Iniciar
                  </button>
                </>
              )}
              {c.status === "revisao" && (
                <button className={btnPrimary} onClick={() => status("concluido")}><Check className="size-3.5" /> Aprovar</button>
              )}
              {c.status === "rejeitado" && <button className={btn} onClick={() => status("backlog")}>Voltar ao backlog</button>}
              {c.conversa_id && <button className={btn} onClick={() => props.onAbrirConversa(c.conversa_id!)}>Abrir conversa</button>}
              {!["rejeitado", "andamento"].includes(c.status) && !rejeitando && (
                <button className={`${btn} text-muted`} onClick={() => setRejeitando(true)}>Rejeitar</button>
              )}
            </div>
          )}
          {c && rejeitando && (
            <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-line bg-bg p-2 text-xs">
              <span className="mr-1 text-muted">Por quê?</span>
              {MOTIVOS.map((m) => (
                <button key={m.id} className={btn} onClick={() => props.acao(() => api.post(`/board/issues/${c.id}/rejeitar`, { motivo: m.id }))}>
                  {m.nome}
                </button>
              ))}
              <button className={`${btn} text-muted`} onClick={() => props.acao(() => api.post(`/board/issues/${c.id}/rejeitar`, { motivo: null }))}>Sem motivo</button>
              <button className={`${icone} ml-auto`} onClick={() => setRejeitando(false)} aria-label="Cancelar"><X className="size-3.5" /></button>
            </div>
          )}
          {c && c.motivo_rejeicao && c.status === "rejeitado" && (
            <p className="text-xs text-muted">Rejeitado: {MOTIVOS.find((m) => m.id === c.motivo_rejeicao)?.nome ?? c.motivo_rejeicao}. A varredura não recria.</p>
          )}
        </div>

        {c && c.evidencias.length > 0 && (
          <Secao titulo="Onde">
            {c.evidencias.map((e, k) => e.arquivo ? (
              <button key={k} title="Abrir no editor, na linha" onClick={() => abrirCodigo(c.projeto, e)}
                className="group block w-full overflow-hidden rounded-lg border border-line bg-bg text-left hover:border-neutral-500">
                <span className="flex items-center gap-1.5 border-b border-line px-3 py-1.5 font-mono text-[11px] text-muted">
                  <Code className="size-3" />{ondeDe(e)}
                  <span className="ml-auto font-sans text-faint group-hover:text-fg">abrir no editor</span>
                </span>
                {e.trecho && <code className="block overflow-x-auto whitespace-pre px-3 py-2 font-mono text-[12px] text-fg">{e.trecho}</code>}
              </button>
            ) : e.imagem ? (
              <a key={k} href={`/api/files?path=${encodeURIComponent(e.imagem)}&conv=${e.conv ?? 0}`} target="_blank"
                rel="noreferrer" className="block" title="Abrir o print em tamanho real">
                <span className="text-[11px] font-medium text-muted">{e.rotulo === "antes" ? "Antes" : e.rotulo === "depois" ? "Depois" : "Print"}</span>
                <img src={`/api/files?path=${encodeURIComponent(e.imagem)}&conv=${e.conv ?? 0}`} alt={`Print ${e.rotulo ?? ""}`}
                  className="mt-1 w-full rounded-lg border border-line" />
              </a>
            ) : e.saida ? (
              <pre key={k} className="max-h-48 overflow-auto rounded-lg border border-line bg-bg p-3 text-[11px] leading-relaxed text-muted">
                {e.comando ? `$ ${e.comando}\n` : ""}{e.saida}
              </pre>
            ) : null)}
          </Secao>
        )}

        <Secao titulo="Descrição">
          <textarea className={`${campo} min-h-[76px] w-full resize-y leading-relaxed`} placeholder="O problema e o impacto"
            {...muda("descricao")} />
        </Secao>
        <Secao titulo="Para a IA">
          <textarea className={`${campo} min-h-[96px] w-full resize-y text-[13px] leading-relaxed`}
            placeholder="Vazio: vai o título, a descrição e as evidências." {...muda("prompt")} />
          <label className="block space-y-1">
            <span className="text-xs text-muted">Como provar que ficou pronto</span>
            <input className={`${campo} w-full font-mono text-xs`} placeholder="Comando de terminal, ex.: npm test" {...muda("verify_sugerido")} />
          </label>
        </Secao>

        {c && c.status === "revisao" && (
          <Secao titulo="Revisão">
            {c.commit && <p className="font-mono text-xs text-muted">commit {c.commit.slice(0, 10)}</p>}
            <textarea className={`${campo} min-h-[64px] w-full resize-y`} placeholder="O que falta? Vira a próxima mensagem da mesma conversa."
              value={comentario} onChange={(e) => setComentario(e.target.value)} />
            <button className={btn} disabled={!comentario.trim()}
              onClick={() => props.acao(() => api.post(`/board/issues/${c.id}/reabrir`, { comentario }))}>Reabrir com o comentário</button>
          </Secao>
        )}

        {c && c.historico.length > 0 && (
          <Secao titulo="Histórico">
            <ol className="relative ml-1 space-y-2 border-l border-line pl-4 text-xs">
              {[...c.historico].reverse().map((h, k) => (
                <li key={k} className="relative">
                  <span className={`absolute -left-[21px] top-1 size-2 rounded-full border border-bg ${k === 0 ? "bg-neutral-300" : "bg-neutral-600"}`} />
                  <span className="text-muted">{h.texto}</span>
                  <span className="block text-[11px] tabular-nums text-faint">{h.quando.replace("T", " ").slice(5, 16)}</span>
                </li>
              ))}
            </ol>
          </Secao>
        )}

        {c && (
          <div className="px-5 pb-5 pt-1">
            {!apagar ? (
              <button className="text-xs text-faint hover:text-red-300" onClick={() => setApagar(true)}>Apagar card</button>
            ) : (
              <span className="flex items-center gap-2 text-xs">
                <span className="text-muted">Apagar de vez?</span>
                <button className="rounded-full border border-red-400/40 px-2.5 py-0.5 text-red-300 hover:bg-red-500/10"
                  onClick={() => props.acao(async () => { await api.del(`/board/issues/${c.id}`); props.onFechar(); })}>Apagar</button>
                <button className="text-faint hover:text-fg" onClick={() => setApagar(false)}>Cancelar</button>
              </span>
            )}
          </div>
        )}
      </div>

      {/* Salvar só aparece quando há o que salvar, preso no rodapé */}
      {mudou && (
        <div className="flex items-center gap-2 border-t border-line bg-surface px-5 py-3">
          <span className="text-xs text-muted">{c ? "Alterações não salvas" : "Preencha e crie"}</span>
          {c && <button className={`${btn} ml-auto`} onClick={() => setF(inicial)}>Descartar</button>}
          <button className={`${btnPrimary} ${c ? "" : "ml-auto"}`} disabled={!f.titulo.trim()} onClick={salvar}>
            {c ? "Salvar" : "Criar card"}
          </button>
        </div>
      )}
    </aside>
  );
}

/** O card que a IA criou (board_card), no fim da resposta dela: o mesmo desenho do board. Clicar abre o board
 * nele; ao lado, ir ao trecho no editor e Iniciar (aceitar é o mesmo clique: foi o usuário que pediu). */
export type CardMini = { id: number; titulo: string; tipo: string; area: string; severidade: number; status: string;
  origem: string; projeto: string; evidencia: Evidencia | null };

export function CardNoChat({ card }: { card: CardMini }) {
  const [c, setC] = useState(card);
  const [erro, setErro] = useState("");
  useEffect(() => { api.get<CardMini>(`/board/issues/${card.id}`).then((x) => setC({ ...card, ...x })).catch(() => {}); }, [card.id]);
  const ev = c.evidencia ?? evidenciaDe(c as any) ?? null;
  const iniciar = async () => {
    setErro("");
    try {
      if (c.status === "novo") await api.patch(`/board/issues/${c.id}`, { status: "backlog" });
      setC({ ...c, ...(await api.post<CardMini>(`/board/issues/${c.id}/iniciar`, {})) });
    } catch (e: any) { setErro(e.message); }
  };
  return (
    <div className="mt-2 max-w-lg">
      <div className="flex items-stretch overflow-hidden rounded-lg border border-line bg-surface">
        <button onClick={() => window.dispatchEvent(new CustomEvent("forja:board", { detail: { id: c.id, projeto: c.projeto } }))}
          title="Abrir este card no board" className="min-w-0 flex-1 p-3 text-left transition-colors hover:bg-raised">
          <div className="flex items-center gap-2">
            <Tipo tipo={c.tipo} />
            <span className="text-[11px] text-faint">{c.area}</span>
            <span className="ml-auto flex items-center gap-2 text-[11px] tabular-nums text-faint">
              <Prioridade sev={c.severidade} />#{c.id} · {NOME_STATUS[c.status] ?? c.status}
            </span>
          </div>
          <div className="mt-1 truncate text-[13px] text-fg">{c.titulo}</div>
          {ev?.arquivo && (
            <div className="mt-1 flex items-center gap-1 truncate font-mono text-[11px] text-faint"><Code className="size-3 shrink-0" />{ondeDe(ev)}</div>
          )}
        </button>
        <div className="flex flex-col justify-center gap-1.5 border-l border-line px-2.5">
          {ev?.arquivo && (
            <button className={btn} title={ondeDe(ev)} onClick={() => abrirCodigo(c.projeto, ev).catch((e) => setErro(e.message))}>
              <Code className="size-3.5" /> Ir para o código
            </button>
          )}
          {["novo", "backlog"].includes(c.status) && (
            <button className={btnPrimary} onClick={iniciar} title="Aceita o card e inicia no modo sugerido">
              <Play className="size-3.5" /> Iniciar
            </button>
          )}
        </div>
      </div>
      {erro && <p className="mt-1 text-xs text-red-400">{erro}</p>}
    </div>
  );
}

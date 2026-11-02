import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import qrcode from "qrcode-generator";
import { api, streamSSE } from "../api";
import type { LocalModel } from "../types";
import CartaoEstado, { botaoEstado, botaoEstadoPrimario } from "./CartaoEstado";
import Confirma from "./Confirma";
import { Caixa } from "./ImagensView";
import { Markdown } from "./MessageView";
import { ArrowUp, Plus, Refresh, Search, Trash, X } from "./icons";

/** Finanças: os dados do IPorte (Supabase, login do próprio usuário) com números calculados no backend
 *  (financas.agregados), os lançamentos do app (transação, conta, investimento, revisão de notificações) gravados
 *  no mesmo Supabase, e um chat com a IA do Forja que lembra a conversa. O IPorte pareado por QR usa a IA daqui
 *  para classificar notificações; cada uso muda `carimbo` (activity.iporte) e a tela recarrega. */

type Msg = { papel: "user" | "assistant"; texto: string; rotulo?: string | null; quando: number; modelo?: string };
type Estado = { conectado: boolean; email: string | null; url: string; tem_projeto: boolean; dispositivo: string | null;
  modelo: { provider: string; model: string } | null; uso: number; chat: Msg[] };
type Mes = { mes: string; receitas: number; despesas: number };
type ContaL = { id: string; nome: string; kind: string; fecha: number | null; vence: number | null; limite: number | null };
type CatL = { id: string; nome: string; tipo: string; cor: string | null };
type Lanc = { id: string; data: string; descricao: string; valor: number; tipo: string; conta: string | null; categoria: string | null;
  transferencia: boolean; origem: string };
type InvL = { id: string; nome: string; tipo: string; reserva: boolean; capital: number; rendimento: number; patrimonio: number };
type Rev = { id: string; titulo: string; texto: string; app: string; quando: string;
  ia: { descricao?: string; valor?: number; tipo?: string; conta_id?: string; categoria_id?: string; motivo?: string } | null;
  confianca: number | null; veredito: string | null };
type Dados = {
  mes_atual: string;
  kpis: { receitas: number; despesas: number; fluxo: number; caixa: number; investido: number; cartoes_a_pagar: number; poupanca: number | null;
    media_receitas_3m: number; media_despesas_3m: number };
  serie: Mes[];
  categorias: { categoria: string; valor: number; cor: string | null; media_3m: number }[];
  contas: { id: string; nome: string; tipo: string; saldo: number }[];
  maiores: { descricao: string; categoria: string; valor: number; data: string }[];
  assinaturas: { nome: string; valor: number; dia: number | null }[];
  fixos_mensais: number;
  orcamentos: { categoria: string; limite: number; gasto: number }[];
  metas: { nome: string; alvo: number; atual: number; prazo: string | null }[];
  historico: { mes: string; saldo: number }[];
  projecao: { mes: string; saldo: number }[];
  lancamentos: Lanc[];
  contas_lista: ContaL[];
  categorias_lista: CatL[];
  investimentos_lista: InvL[];
  revisao: Rev[];
};
type Par = { tipo: string; url: string | null; lan: string | null; token: string };
type Catalogo = { id: string; name: string; models: string[] }[];
type Tipo = "panorama" | "projecao" | "economia" | "pergunta";
type Aba = "geral" | "transacoes" | "contas" | "investimentos" | "revisao";
type Form =
  | { qual: "transacao"; inicial?: Partial<NovaTx>; logId?: string }
  | { qual: "conta" }
  | { qual: "investimento" }
  | { qual: "movimento"; inv: InvL; tipo: "aporte" | "resgate" };
type NovaTx = { tipo: "despesa" | "receita" | "transferencia"; valor: string; descricao: string; conta: string; destino: string;
  categoria: string; data: string; parcelas: string };

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const brlCurto = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1 });
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const nomeMes = (m: string) => MESES[Number(m.slice(5, 7)) - 1] ?? m;
const campo = "w-full min-w-0 bg-transparent text-[12.5px] text-fg outline-none placeholder:text-faint";
const hoje = () => new Date().toLocaleDateString("sv-SE");   // AAAA-MM-DD no fuso local
const dataCurta = (s: string) => `${s.slice(8, 10)}/${s.slice(5, 7)}`;
const KINDS: Record<string, string> = { corrente: "Conta corrente", poupanca: "Poupança", dinheiro: "Dinheiro", carteira: "Carteira digital",
  cartao: "Cartão de crédito", investimento: "Investimentos" };
const TIPOS_INV: Record<string, string> = { cdb: "CDB", cdi: "Pós-fixado (CDI)", lci: "LCI", lca: "LCA", tesouro_selic: "Tesouro Selic",
  tesouro_prefixado: "Tesouro Prefixado", tesouro_ipca: "Tesouro IPCA+", poupanca: "Poupança" };
const ACOES: { id: Exclude<Tipo, "pergunta">; rotulo: string; dica: string }[] = [
  { id: "panorama", rotulo: "Panorama do mês", dica: "Como está o mês contra a média" },
  { id: "projecao", rotulo: "Projeção", dica: "Saldo esperado nos próximos 3 meses" },
  { id: "economia", rotulo: "Onde economizar", dica: "Cortes concretos com o valor por mês" },
];
const ABAS: { id: Aba; rotulo: string }[] = [
  { id: "geral", rotulo: "Visão geral" }, { id: "transacoes", rotulo: "Transações" }, { id: "contas", rotulo: "Contas" },
  { id: "investimentos", rotulo: "Investimentos" }, { id: "revisao", rotulo: "Revisão" },
];

export default function FinancasView(props: { carimbo?: number; onError: (e: string) => void }) {
  const { onError } = props;
  const [e, setE] = useState<Estado | null>(null);
  const [d, setD] = useState<Dados | null>(null);
  const [erroDados, setErroDados] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [conexao, setConexao] = useState(false);
  const [aba, setAba] = useState<Aba>("geral");
  const [form, setForm] = useState<Form | null>(null);
  const [aviso, setAviso] = useState("");

  const carregaEstado = useCallback(() => api.get<Estado>("/financas").then(setE).catch((er) => onError(er.message)), [onError]);
  const carregaDados = useCallback(() => {
    setCarregando(true);
    return api.get<Dados>("/financas/dados").then((r) => { setD(r); setErroDados(""); })
      .catch((er) => { setErroDados(er.message); carregaEstado(); })
      .finally(() => setCarregando(false));
  }, [carregaEstado]);

  useEffect(() => { carregaEstado(); }, [carregaEstado]);
  useEffect(() => { if (e?.conectado) carregaDados(); }, [e?.conectado, carregaDados]);
  // ao vivo: o IPorte usou a IA daqui (lançou transação) -> dados novos no Supabase depois do sync dele
  useEffect(() => {
    if (!props.carimbo || !e?.conectado) return;
    const t = setTimeout(carregaDados, 8000);   // ponytail: espera o push do sync do celular; sem canal do Supabase
    carregaEstado();
    return () => clearTimeout(t);
  }, [props.carimbo]);
  useEffect(() => { if (!aviso) return; const t = setTimeout(() => setAviso(""), 3500); return () => clearTimeout(t); }, [aviso]);

  /** Toda escrita passa por aqui: grava no Supabase, avisa e relê os números. */
  const gravar = (caminho: string, corpo: unknown, msg: string, metodo: "post" | "delete" = "post") =>
    (metodo === "post" ? api.post(caminho, corpo) : api.del(caminho))
      .then(() => { setAviso(msg); carregaDados(); });

  if (!e) return <div className="flex-1 bg-bg" />;
  if (!e.conectado || conexao) {
    return (
      <div className="flex min-h-0 flex-1 overflow-y-auto bg-bg">
        <Conexao e={e} onError={onError} onPronto={(r) => { setE((a) => a && { ...a, ...r }); setConexao(false); }}
                 onFechar={e.conectado ? () => setConexao(false) : undefined} />
      </div>
    );
  }

  const pendentes = d?.revisao.length ?? 0;
  return (
    <div className="flex min-h-0 flex-1 bg-bg">
      <div className="relative flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center gap-2.5 border-b border-line px-4 py-2">
          <span className="rounded-[5px] border border-line px-1.5 py-px font-mono text-[11px] text-faint">
            {d ? `${nomeMes(d.mes_atual)} ${d.mes_atual.slice(0, 4)}` : "—"}
          </span>
          <span className="truncate text-[12px] text-muted">{e.email}</span>
          {e.dispositivo && <span className="truncate text-[11.5px] text-faint" title={e.uso ? `IA usada pelo celular em ${new Date(e.uso * 1000).toLocaleString("pt-BR")}` : undefined}>· {e.dispositivo} pareado</span>}
          <span className="flex-1" />
          <button className="rounded-[7px] p-1.5 text-faint hover:bg-raised hover:text-fg" title="Recarregar do IPorte" onClick={carregaDados}>
            <Refresh className={`size-3.5 ${carregando ? "animate-spin" : ""}`} />
          </button>
          <button className={botaoEstado} onClick={() => setConexao(true)}>Conexão</button>
          <button className={`${botaoEstadoPrimario} inline-flex items-center gap-1`} disabled={!d} onClick={() => setForm({ qual: "transacao" })}>
            <Plus className="size-3.5" /> Nova transação
          </button>
        </div>
        <nav className="flex shrink-0 gap-1 border-b border-line px-3">
          {ABAS.map((a) => (
            <button key={a.id} onClick={() => setAba(a.id)}
                    className={`relative -mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-[12.5px] transition-colors ${aba === a.id ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"}`}>
              {a.rotulo}
              {a.id === "revisao" && pendentes > 0 && <span className="rounded-full bg-warn/20 px-1.5 font-mono text-[10.5px] text-warn">{pendentes}</span>}
            </button>
          ))}
        </nav>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {erroDados && <div className="p-4"><CartaoEstado tom="erro" titulo="Não deu para ler o IPorte">{erroDados}</CartaoEstado></div>}
          {!d && !erroDados && <div className="p-6 text-[12.5px] text-muted">Lendo suas finanças…</div>}
          {d && aba === "geral" && <Painel d={d} />}
          {d && aba === "transacoes" && <Transacoes d={d} onApagar={(t) => gravar(`/financas/transacao/${t.id}`, null, t.transferencia ? "Transferência excluída (as duas pernas)." : "Transação excluída.", "delete").catch((er) => onError(er.message))} />}
          {d && aba === "contas" && <Contas d={d} onNova={() => setForm({ qual: "conta" })} />}
          {d && aba === "investimentos" && <Investimentos d={d} onNovo={() => setForm({ qual: "investimento" })} onMov={(inv, tipo) => setForm({ qual: "movimento", inv, tipo })} />}
          {d && aba === "revisao" && (
            <Revisao d={d}
                     onCriar={(r) => setForm({ qual: "transacao", logId: r.id, inicial: {
                       tipo: r.ia?.tipo === "receita" ? "receita" : "despesa", descricao: r.ia?.descricao || r.titulo.slice(0, 60),
                       valor: r.ia?.valor ? String(r.ia.valor).replace(".", ",") : "", conta: r.ia?.conta_id ?? "",
                       categoria: r.ia?.categoria_id ?? "", data: (r.quando || "").slice(0, 10) || hoje() } })}
                     onAcao={(r, acao) => gravar(`/financas/revisao/${r.id}`, { acao }, acao === "ignorar" ? "Marcada como não transação: a IA aprende com isso." : "Descartada da fila.").catch((er) => onError(er.message))} />
          )}
        </div>
        {aviso && (
          <div className="pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full border border-ok/40 bg-raised px-4 py-1.5 text-[12px] text-fg shadow-lg">
            <span className="text-ok">✓</span> {aviso}
          </div>
        )}
      </div>

      <Chat e={e} setE={setE} onError={onError} />

      {form && d && (
        <Modal titulo={form.qual === "transacao" ? (form.logId ? "Criar transação da notificação" : "Nova transação")
                      : form.qual === "conta" ? "Nova conta" : form.qual === "investimento" ? "Novo investimento"
                      : `${form.tipo === "aporte" ? "Aporte em" : "Resgate de"} ${form.inv.nome}`}
               onFechar={() => setForm(null)}>
          {form.qual === "transacao" && <FormTransacao d={d} inicial={form.inicial}
            onSalvar={(b) => gravar("/financas/novo/transacao", { ...b, log_id: form.logId }, "Transação lançada. O IPorte recebe no próximo sync.").then(() => setForm(null))} />}
          {form.qual === "conta" && <FormConta onSalvar={(b) => gravar("/financas/novo/conta", b, "Conta criada.").then(() => setForm(null))} />}
          {form.qual === "investimento" && <FormInvestimento d={d} onSalvar={(b) => gravar("/financas/novo/investimento", b, "Investimento criado.").then(() => setForm(null))} />}
          {form.qual === "movimento" && <FormMovimento d={d} inv={form.inv} tipo={form.tipo}
            onSalvar={(b) => gravar("/financas/novo/movimento", { ...b, investimento: form.inv.id, tipo: form.tipo }, form.tipo === "aporte" ? "Aporte registrado." : "Resgate registrado.").then(() => setForm(null))} />}
        </Modal>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ chat com a IA (histórico no backend)

function Chat(props: { e: Estado; setE: React.Dispatch<React.SetStateAction<Estado | null>>; onError: (e: string) => void }) {
  const { e, setE, onError } = props;
  const [pergunta, setPergunta] = useState("");
  const [rodando, setRodando] = useState<{ pedido: Msg; texto: string } | null>(null);
  const [catalogo, setCatalogo] = useState<Catalogo>([]);
  const [local, setLocal] = useState<LocalModel[]>([]);
  const [ajustes, setAjustes] = useState(false);
  const cancelar = useRef<AbortController | null>(null);
  const fim = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get<Catalogo>("/catalog").then(setCatalogo).catch(() => {});
    api.get<{ models: LocalModel[] }>("/local").then((r) => setLocal(r.models.filter((m) => m.kind === "chat"))).catch(() => {});
    return () => cancelar.current?.abort();
  }, []);
  useEffect(() => { fim.current?.scrollIntoView({ block: "end" }); }, [e.chat.length, rodando?.texto]);

  const enviar = (tipo: Tipo, texto = "") => {
    if (rodando) return;
    const ac = new AbortController();
    cancelar.current = ac;
    const rotulo = tipo === "pergunta" ? null : ACOES.find((a) => a.id === tipo)!.rotulo;
    setRodando({ pedido: { papel: "user", texto, rotulo, quando: Date.now() / 1000 }, texto: "" });
    streamSSE("/financas/analise", { method: "POST", body: JSON.stringify({ tipo, pergunta: texto }), signal: ac.signal }, (ev) => {
      if (ev.texto) setRodando((s) => s && { ...s, texto: s.texto + ev.texto });
      if (ev.erro) onError(ev.erro);
    }).catch((er) => { if (er.name !== "AbortError") onError(er.message); })
      .finally(() => api.get<Estado>("/financas").then(setE).catch(() => {}).finally(() => setRodando(null)));
  };

  const opcoes = [
    ...local.map((m) => ({ grupo: "IA local", provider: "local", model: m.name })),
    ...catalogo.filter((p) => p.id !== "local").flatMap((p) => p.models.map((m) => ({ grupo: p.name, provider: p.id, model: m }))),
  ];
  if (e.modelo && !opcoes.some((o) => o.provider === e.modelo!.provider && o.model === e.modelo!.model))
    opcoes.push({ grupo: "Escolhido", ...e.modelo });
  const grupos = [...new Set(opcoes.map((o) => o.grupo))];
  const msgs = rodando ? [...e.chat, rodando.pedido] : e.chat;

  return (
    <aside className="flex w-[420px] shrink-0 flex-col border-l border-line bg-panel">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <span className="grid size-6 place-items-center rounded-full bg-accent-soft text-[12px] text-accent-text">✦</span>
        <div className="min-w-0">
          <h3 className="text-[12.5px] font-semibold leading-tight text-fg">Assistente financeiro</h3>
          <p className="truncate text-[10.5px] text-faint">{e.modelo ? e.modelo.model.split(/[\\/]/).pop() : "modelo do F.O.R.J.A"}</p>
        </div>
        <span className="flex-1" />
        <button className="rounded-[7px] px-2 py-1 text-[11.5px] text-faint hover:bg-raised hover:text-fg" onClick={() => setAjustes((x) => !x)}>Modelo</button>
        {e.chat.length > 0 && !rodando && (
          <Confirma className="rounded-[7px] p-1.5 text-faint hover:bg-raised hover:text-fg" titulo="Limpar conversa" rotulo={<Trash className="size-3.5" />}
                    pergunta="Apagar a conversa?" onSim={() => api.del("/financas/chat").then(() => setE((a) => a && { ...a, chat: [] })).catch((er) => onError(er.message))} />
        )}
      </div>
      {ajustes && (
        <div className="flex flex-col gap-1.5 border-b border-line px-4 py-3">
          <Caixa rotulo="Modelo">
            <select className={`${campo} cursor-pointer`} value={e.modelo ? `${e.modelo.provider}|${e.modelo.model}` : ""}
                    onChange={(ev) => {
                      const [provider, ...m] = ev.target.value.split("|");
                      api.put<Estado>("/financas/modelo", { provider, model: m.join("|") }).then((r) => setE((a) => a && { ...a, ...r })).catch((er) => onError(er.message));
                    }}>
              <option value="">o do assistente F.O.R.J.A</option>
              {grupos.map((g) => (
                <optgroup key={g} label={g}>
                  {opcoes.filter((o) => o.grupo === g).map((o) => <option key={`${o.provider}|${o.model}`} value={`${o.provider}|${o.model}`}>{o.model.split(/[\\/]/).pop()}</option>)}
                </optgroup>
              ))}
            </select>
          </Caixa>
          <p className="text-[11px] leading-relaxed text-faint">Também é o modelo que o IPorte usa para ler as notificações quando está pareado.</p>
        </div>
      )}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
        {msgs.length === 0 && (
          <div className="m-auto flex max-w-[300px] flex-col items-center gap-2 text-center">
            <span className="grid size-10 place-items-center rounded-full bg-accent-soft text-[18px] text-accent-text">✦</span>
            <p className="text-[13px] font-medium text-fg">Pergunte sobre o seu dinheiro</p>
            <p className="text-[12px] leading-relaxed text-muted">A IA recebe os números já calculados (receitas, categorias, contas, assinaturas, orçamentos e projeção) e lembra a conversa.</p>
          </div>
        )}
        {msgs.map((m, i) => <Bolha key={i} m={m} />)}
        {rodando && (
          <div className="flex flex-col items-start gap-1">
            <div className="max-w-[92%] rounded-[14px] rounded-tl-[4px] border border-line bg-surface px-3.5 py-2.5 [&_.md]:text-[13px]">
              {rodando.texto ? <Markdown text={rodando.texto} /> : (
                <span className="flex gap-1 py-1">{[0, 1, 2].map((j) => <span key={j} className="size-1.5 animate-bounce rounded-full bg-faint" style={{ animationDelay: `${j * 120}ms` }} />)}</span>
              )}
            </div>
            <button className="pl-1 text-[11px] text-faint hover:text-fg" onClick={() => cancelar.current?.abort()}>parar</button>
          </div>
        )}
        <div ref={fim} />
      </div>
      <div className="flex flex-col gap-2 border-t border-line p-3">
        <div className="flex flex-wrap gap-1.5">
          {ACOES.map((a) => (
            <button key={a.id} title={a.dica} disabled={!!rodando} onClick={() => enviar(a.id)}
                    className="rounded-full border border-line px-2.5 py-1 text-[11.5px] text-fg-2 transition-colors hover:border-accent-line hover:bg-accent-soft hover:text-accent-text disabled:opacity-40">
              {a.rotulo}
            </button>
          ))}
        </div>
        <form className="flex items-end gap-2 rounded-[14px] border border-line bg-surface px-3 py-2 focus-within:border-focus"
              onSubmit={(ev) => { ev.preventDefault(); if (pergunta.trim() && !rodando) { enviar("pergunta", pergunta.trim()); setPergunta(""); } }}>
          <textarea rows={1} value={pergunta} onChange={(ev) => setPergunta(ev.target.value)} placeholder="Mensagem para o assistente…"
                    onKeyDown={(ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); ev.currentTarget.form?.requestSubmit(); } }}
                    className="max-h-32 min-h-[22px] flex-1 resize-none bg-transparent text-[13px] text-fg outline-none [field-sizing:content] placeholder:text-faint" />
          <button type="submit" aria-label="Enviar" disabled={!pergunta.trim() || !!rodando}
                  className="rounded-full bg-accent p-1.5 text-accent-fg disabled:opacity-40"><ArrowUp className="size-4" /></button>
        </form>
      </div>
    </aside>
  );
}

function Bolha({ m }: { m: Msg }) {
  const hora = new Date(m.quando * 1000).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  if (m.papel === "user")
    return (
      <div className="flex flex-col items-end gap-0.5">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-[14px] rounded-tr-[4px] bg-accent px-3.5 py-2 text-[13px] text-accent-fg">
          {m.rotulo ? <span className="font-medium">✦ {m.rotulo}</span> : m.texto}
        </div>
        <span className="pr-1 text-[10px] text-faint">{hora}</span>
      </div>
    );
  return (
    <div className="flex flex-col items-start gap-0.5">
      <div className="max-w-[92%] rounded-[14px] rounded-tl-[4px] border border-line bg-surface px-3.5 py-2.5 [&_.md]:text-[13px]">
        {m.texto ? <Markdown text={m.texto} /> : <span className="text-[12px] text-faint">(sem resposta)</span>}
      </div>
      <span className="pl-1 text-[10px] text-faint">{hora}{m.modelo ? ` · ${m.modelo.split(/[\\/]/).pop()}` : ""}</span>
    </div>
  );
}

// ------------------------------------------------------------------ abas de lançamentos

function Transacoes(props: { d: Dados; onApagar: (t: Lanc) => void }) {
  const [busca, setBusca] = useState("");
  const [filtro, setFiltro] = useState<"todas" | "receita" | "despesa">("todas");
  const [futuras, setFuturas] = useState(false);
  const h = hoje();
  const nFuturas = props.d.lancamentos.filter((t) => t.data > h).length;
  const lista = useMemo(() => {
    const q = busca.trim().toLowerCase();
    return props.d.lancamentos.filter((t) => (futuras || t.data <= h) && (filtro === "todas" || (t.tipo === filtro && !t.transferencia))
      && (!q || `${t.descricao} ${t.categoria ?? ""} ${t.conta ?? ""}`.toLowerCase().includes(q)));
  }, [props.d.lancamentos, busca, filtro, futuras, h]);
  const dias = useMemo(() => {
    const g = new Map<string, Lanc[]>();
    for (const t of lista) g.set(t.data, [...(g.get(t.data) ?? []), t]);
    return [...g.entries()];
  }, [lista]);
  return (
    <div className="mx-auto flex w-full max-w-[900px] flex-col gap-3 p-4">
      <div className="flex items-center gap-2">
        <label className="flex flex-1 items-center gap-2 rounded-[10px] border border-line bg-surface px-3 py-1.5 focus-within:border-focus">
          <Search className="size-3.5 text-faint" />
          <input className={campo} placeholder="Buscar por descrição, categoria ou conta" value={busca} onChange={(ev) => setBusca(ev.target.value)} />
        </label>
        <Segmentos valor={filtro} onChange={setFiltro} opcoes={[["todas", "Todas"], ["receita", "Receitas"], ["despesa", "Despesas"]]} />
      </div>
      {nFuturas > 0 && (
        <button className="self-start text-[11.5px] text-faint hover:text-fg" onClick={() => setFuturas((x) => !x)}>
          {futuras ? "Esconder" : "Mostrar"} {nFuturas} lançamento{nFuturas === 1 ? "" : "s"} futuro{nFuturas === 1 ? "" : "s"} (parcelas a vencer)
        </button>
      )}
      {!dias.length && <p className="p-4 text-[12.5px] text-muted">Nenhuma transação.</p>}
      {dias.map(([dia, ts]) => (
        <section key={dia} className="overflow-hidden rounded-[12px] border border-line bg-surface">
          <header className="flex items-center gap-2 border-b border-line bg-raised/40 px-4 py-1.5 text-[11.5px] text-muted">
            <span>{new Date(`${dia}T12:00`).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "short" })}</span>
            <span className="flex-1" />
            <span className="font-mono">{brl.format(ts.reduce((a, t) => a + (t.transferencia ? 0 : t.tipo === "receita" ? t.valor : -t.valor), 0))}</span>
          </header>
          <ul className="divide-y divide-line">
            {ts.map((t) => (
              <li key={t.id} className="group flex items-center gap-3 px-4 py-2">
                <span className={`grid size-7 shrink-0 place-items-center rounded-full text-[12px] ${t.transferencia ? "bg-raised text-muted" : t.tipo === "receita" ? "bg-ok/15 text-ok" : "bg-err/15 text-err"}`}>
                  {t.transferencia ? "⇄" : t.tipo === "receita" ? "↓" : "↑"}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] text-fg">{t.descricao || t.categoria || "Sem descrição"}</div>
                  <div className="truncate text-[11px] text-faint">
                    {[t.transferencia ? "Transferência" : t.categoria ?? "Sem categoria", t.conta, t.origem === "notification" ? "notificação" : null].filter(Boolean).join(" · ")}
                  </div>
                </div>
                <span className={`font-mono text-[12.5px] ${t.transferencia ? "text-muted" : t.tipo === "receita" ? "text-ok" : "text-fg"}`}>
                  {t.tipo === "receita" ? "+" : "−"}{brl.format(t.valor)}
                </span>
                <span className="w-0 overflow-hidden group-hover:w-auto group-focus-within:w-auto">
                  <Confirma className="rounded-[6px] p-1 text-faint hover:bg-raised hover:text-err" titulo="Excluir" rotulo={<Trash className="size-3.5" />}
                            pergunta={t.transferencia ? "Excluir as duas pernas?" : "Excluir?"} onSim={() => props.onApagar(t)} />
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Contas(props: { d: Dados; onNova: () => void }) {
  const saldo = new Map(props.d.contas.map((c) => [c.id, c.saldo]));
  const grupos = Object.keys(KINDS).map((k) => [k, props.d.contas_lista.filter((c) => c.kind === k)] as const).filter(([, cs]) => cs.length);
  return (
    <div className="mx-auto flex w-full max-w-[1000px] flex-col gap-4 p-4">
      <div className="flex items-center gap-3">
        <div className="grid flex-1 grid-cols-3 gap-3">
          <Kpi rotulo="Caixa" valor={brl.format(props.d.kpis.caixa)} nota="contas, poupança e dinheiro" />
          <Kpi rotulo="Cartões a pagar" valor={brl.format(props.d.kpis.cartoes_a_pagar)} nota="saldo devedor somado" />
          <Kpi rotulo="Investido" valor={brl.format(props.d.kpis.investido)} nota="patrimônio do livro-razão" />
        </div>
      </div>
      <div className="flex justify-end"><button className={`${botaoEstado} inline-flex items-center gap-1`} onClick={props.onNova}><Plus className="size-3.5" /> Nova conta</button></div>
      {grupos.map(([k, cs]) => (
        <section key={k} className="flex flex-col gap-2">
          <h3 className="text-[11.5px] font-semibold uppercase tracking-wide text-faint">{KINDS[k]}</h3>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {cs.map((c) => {
              const s = saldo.get(c.id) ?? 0;
              const usado = c.kind === "cartao" && c.limite ? Math.max(0, -s) / c.limite : null;
              return (
                <div key={c.id} className="flex flex-col gap-1.5 rounded-[12px] border border-line bg-surface px-4 py-3">
                  <span className="truncate text-[12.5px] text-fg">{c.nome}</span>
                  <span className={`font-mono text-[18px] font-semibold ${s < 0 ? "text-err" : "text-fg"}`}>{brl.format(s)}</span>
                  {c.kind === "cartao" && (
                    <>
                      <span className="text-[11px] text-faint">fecha dia {c.fecha ?? "—"} · vence dia {c.vence ?? "—"}{c.limite ? ` · limite ${brl.format(c.limite)}` : ""}</span>
                      {usado !== null && (
                        <div className="h-1.5 overflow-hidden rounded-full bg-raised">
                          <div className={`h-full rounded-full ${usado > 0.9 ? "bg-err" : usado > 0.6 ? "bg-warn" : "bg-accent"}`} style={{ width: `${Math.min(100, usado * 100)}%` }} />
                        </div>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

function Investimentos(props: { d: Dados; onNovo: () => void; onMov: (i: InvL, t: "aporte" | "resgate") => void }) {
  const invs = props.d.investimentos_lista;
  const total = invs.reduce((a, i) => a + i.patrimonio, 0), rend = invs.reduce((a, i) => a + i.rendimento, 0);
  return (
    <div className="mx-auto flex w-full max-w-[1000px] flex-col gap-4 p-4">
      <div className="grid grid-cols-3 gap-3">
        <Kpi rotulo="Patrimônio" valor={brl.format(total)} nota={`${invs.length} investimento${invs.length === 1 ? "" : "s"}`} />
        <Kpi rotulo="Rendimento acumulado" valor={brl.format(rend)} nota={`capital aplicado ${brl.format(total - rend)}`} />
        <Kpi rotulo="Reservas" valor={brl.format(invs.filter((i) => i.reserva).reduce((a, i) => a + i.patrimonio, 0))} nota="dinheiro guardado" />
      </div>
      <div className="flex justify-end"><button className={`${botaoEstado} inline-flex items-center gap-1`} onClick={props.onNovo}><Plus className="size-3.5" /> Novo investimento</button></div>
      {!invs.length && <p className="text-[12.5px] text-muted">Nenhum investimento ainda.</p>}
      <div className="grid gap-3 md:grid-cols-2">
        {invs.map((i) => (
          <div key={i.id} className="flex flex-col gap-2 rounded-[12px] border border-line bg-surface px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="truncate text-[13px] font-medium text-fg">{i.nome}</span>
              <span className="rounded-full border border-line px-1.5 text-[10.5px] text-faint">{TIPOS_INV[i.tipo] ?? i.tipo}</span>
              {i.reserva && <span className="rounded-full bg-accent-soft px-1.5 text-[10.5px] text-accent-text">reserva</span>}
            </div>
            <span className="font-mono text-[18px] font-semibold text-fg">{brl.format(i.patrimonio)}</span>
            <span className="text-[11px] text-faint">capital {brl.format(i.capital)} · <span className={i.rendimento >= 0 ? "text-ok" : "text-err"}>rendimento {brl.format(i.rendimento)}</span></span>
            <div className="flex gap-2">
              <button className={botaoEstado} onClick={() => props.onMov(i, "aporte")}>Aportar</button>
              <button className={botaoEstado} disabled={i.patrimonio <= 0} onClick={() => props.onMov(i, "resgate")}>Resgatar</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Revisao(props: { d: Dados; onCriar: (r: Rev) => void; onAcao: (r: Rev, a: "ignorar" | "descartar") => void }) {
  const contas = new Map(props.d.contas_lista.map((c) => [c.id, c.nome]));
  if (!props.d.revisao.length)
    return (
      <div className="mx-auto flex max-w-[420px] flex-col items-center gap-2 p-12 text-center">
        <span className="grid size-10 place-items-center rounded-full bg-ok/15 text-ok">✓</span>
        <p className="text-[13px] font-medium text-fg">Nada para revisar</p>
        <p className="text-[12px] text-muted">As notificações que o IPorte não conseguiu lançar sozinho (ou que a IA ficou em dúvida) aparecem aqui.</p>
      </div>
    );
  return (
    <div className="mx-auto flex w-full max-w-[900px] flex-col gap-3 p-4">
      <p className="text-[12px] text-muted">Notificações do banco retidas no celular. Crie a transação, ou marque "não é transação" para a IA aprender.</p>
      {props.d.revisao.map((r) => (
        <div key={r.id} className="flex flex-col gap-2 rounded-[12px] border border-line bg-surface px-4 py-3">
          <div className="flex items-center gap-2 text-[11px] text-faint">
            <span className="truncate font-mono">{r.app.split(".").slice(-2).join(".")}</span>
            <span>·</span>
            <span>{r.quando ? new Date(r.quando).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : ""}</span>
          </div>
          <div>
            <div className="text-[12.5px] font-medium text-fg">{r.titulo}</div>
            <div className="text-[12.5px] text-fg-2">{r.texto}</div>
          </div>
          {r.ia && (
            <div className="rounded-[9px] border border-accent-line/50 bg-accent-soft/40 px-3 py-2 text-[11.5px] text-fg-2">
              <span className="text-accent-text">✦ IA</span>{r.confianca != null && <span className="text-faint"> · {Math.round(r.confianca * 100)}% de confiança</span>}:{" "}
              {r.ia.tipo ?? "despesa"} de {r.ia.valor != null ? brl.format(r.ia.valor) : "?"}{r.ia.descricao ? ` — ${r.ia.descricao}` : ""}
              {r.ia.conta_id && contas.get(r.ia.conta_id) ? ` em ${contas.get(r.ia.conta_id)}` : ""}
              {r.ia.motivo && <div className="mt-0.5 text-faint">{r.ia.motivo}</div>}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <button className={botaoEstadoPrimario} onClick={() => props.onCriar(r)}>Criar transação</button>
            <button className={botaoEstado} onClick={() => props.onAcao(r, "ignorar")}>Não é transação</button>
            <button className="px-2 text-[12px] text-faint hover:text-fg" onClick={() => props.onAcao(r, "descartar")}>Descartar</button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ formulários

function Modal(props: { titulo: string; onFechar: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const esc = (ev: KeyboardEvent) => { if (ev.key === "Escape") props.onFechar(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [props.onFechar]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" onMouseDown={(ev) => { if (ev.target === ev.currentTarget) props.onFechar(); }}>
      <div className="flex w-full max-w-[480px] flex-col gap-4 rounded-[16px] border border-line bg-panel p-5 shadow-2xl">
        <div className="flex items-center gap-2">
          <h3 className="text-[14px] font-semibold text-fg">{props.titulo}</h3>
          <span className="flex-1" />
          <button aria-label="Fechar" className="rounded-[7px] p-1 text-faint hover:bg-raised hover:text-fg" onClick={props.onFechar}><X className="size-4" /></button>
        </div>
        {props.children}
      </div>
    </div>
  );
}

function Campo(props: { rotulo: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`flex min-w-0 flex-col gap-0.5 rounded-[9px] border border-line bg-surface px-3 py-1.5 focus-within:border-focus ${props.className ?? ""}`}>
      <span className="text-[10.5px] text-faint">{props.rotulo}</span>
      {props.children}
    </label>
  );
}

function Segmentos<T extends string>(props: { valor: T; onChange: (v: T) => void; opcoes: [T, string][] }) {
  return (
    <div className="flex rounded-[9px] border border-line bg-surface p-0.5">
      {props.opcoes.map(([v, r]) => (
        <button key={v} type="button" onClick={() => props.onChange(v)}
                className={`rounded-[7px] px-3 py-1 text-[12px] transition-colors ${props.valor === v ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>{r}</button>
      ))}
    </div>
  );
}

/** Rodapé de todo formulário: o erro do backend aparece aqui, sem fechar o modal. */
function useEnvio(onSalvar: (b: Record<string, unknown>) => Promise<unknown>) {
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState("");
  const enviar = (b: Record<string, unknown>) => {
    setSalvando(true);
    setErro("");
    onSalvar(b).catch((er) => setErro(er.message)).finally(() => setSalvando(false));
  };
  const rodape = (rotulo: string, pronto: boolean) => (
    <div className="flex items-center gap-2">
      {erro && <span className="text-[12px] text-err">{erro}</span>}
      <span className="flex-1" />
      <button type="submit" className={botaoEstadoPrimario} disabled={salvando || !pronto}>{salvando ? "Salvando…" : rotulo}</button>
    </div>
  );
  return { enviar, rodape };
}

const numero = (s: string) => Number(s.replace(/\./g, "").replace(",", ".")) || 0;
const inputValor = `${campo} font-mono text-[15px] font-semibold`;

function SelectConta(props: { contas: ContaL[]; valor: string; onChange: (v: string) => void; vazio?: string }) {
  return (
    <select className={`${campo} cursor-pointer`} value={props.valor} onChange={(ev) => props.onChange(ev.target.value)}>
      {props.vazio !== undefined && <option value="">{props.vazio}</option>}
      {Object.keys(KINDS).map((k) => {
        const cs = props.contas.filter((c) => c.kind === k);
        return cs.length ? <optgroup key={k} label={KINDS[k]}>{cs.map((c) => <option key={c.id} value={c.id}>{c.nome}</option>)}</optgroup> : null;
      })}
    </select>
  );
}

function FormTransacao(props: { d: Dados; inicial?: Partial<NovaTx>; onSalvar: (b: Record<string, unknown>) => Promise<unknown> }) {
  const contas = props.d.contas_lista.filter((c) => c.kind !== "investimento");
  const [f, setF] = useState<NovaTx>({ tipo: "despesa", valor: "", descricao: "", conta: contas[0]?.id ?? "", destino: "", categoria: "",
    data: hoje(), parcelas: "1", ...props.inicial });
  const { enviar, rodape } = useEnvio(props.onSalvar);
  const set = (p: Partial<NovaTx>) => setF((x) => ({ ...x, ...p }));
  const conta = props.d.contas_lista.find((c) => c.id === f.conta);
  const cats = props.d.categorias_lista.filter((c) => c.tipo === f.tipo);
  const n = Number(f.parcelas) || 1;
  return (
    <form className="flex flex-col gap-2.5" onSubmit={(ev) => { ev.preventDefault();
      enviar({ tipo: f.tipo, valor: numero(f.valor), descricao: f.descricao, conta: f.conta, destino: f.destino, categoria: f.categoria || null,
               data: f.data, parcelas: f.tipo === "despesa" ? n : 1 }); }}>
      <Segmentos valor={f.tipo} onChange={(tipo) => set({ tipo, categoria: "" })}
                 opcoes={[["despesa", "Despesa"], ["receita", "Receita"], ["transferencia", "Transferência"]]} />
      <Campo rotulo="Valor (R$)"><input autoFocus className={inputValor} inputMode="decimal" placeholder="0,00" value={f.valor} onChange={(ev) => set({ valor: ev.target.value })} /></Campo>
      <Campo rotulo="Descrição"><input className={campo} placeholder={f.tipo === "transferencia" ? "Transferência" : "Ex.: Mercado"} value={f.descricao} onChange={(ev) => set({ descricao: ev.target.value })} /></Campo>
      <div className="grid grid-cols-2 gap-2">
        <Campo rotulo={f.tipo === "transferencia" ? "De" : "Conta"}><SelectConta contas={contas} valor={f.conta} onChange={(conta) => set({ conta })} vazio="Escolha" /></Campo>
        {f.tipo === "transferencia" ? (
          <Campo rotulo="Para"><SelectConta contas={props.d.contas_lista.filter((c) => c.id !== f.conta)} valor={f.destino} onChange={(destino) => set({ destino })} vazio="Escolha" /></Campo>
        ) : (
          <Campo rotulo="Categoria">
            <select className={`${campo} cursor-pointer`} value={f.categoria} onChange={(ev) => set({ categoria: ev.target.value })}>
              <option value="">Sem categoria</option>
              {cats.map((c) => <option key={c.id} value={c.id}>{c.nome}</option>)}
            </select>
          </Campo>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Campo rotulo="Data"><input type="date" className={`${campo} [color-scheme:dark]`} value={f.data} onChange={(ev) => set({ data: ev.target.value })} /></Campo>
        {f.tipo === "despesa" && conta?.kind === "cartao" && (
          <Campo rotulo="Parcelas"><input type="number" min={1} max={48} className={`${campo} font-mono`} value={f.parcelas} onChange={(ev) => set({ parcelas: ev.target.value })} /></Campo>
        )}
      </div>
      {f.tipo === "despesa" && conta?.kind === "cartao" && (
        <p className="text-[11px] text-faint">Entra na fatura aberta do cartão (fecha dia {conta.fecha}){n > 1 ? `, uma parcela de ${brl.format(numero(f.valor) / n)} por fatura` : ""}.</p>
      )}
      {rodape("Lançar", numero(f.valor) > 0 && !!f.conta && (f.tipo !== "transferencia" || !!f.destino))}
    </form>
  );
}

function FormConta(props: { onSalvar: (b: Record<string, unknown>) => Promise<unknown> }) {
  const [f, setF] = useState({ nome: "", kind: "corrente", saldo: "", fecha: "", vence: "", limite: "" });
  const { enviar, rodape } = useEnvio(props.onSalvar);
  const set = (p: Partial<typeof f>) => setF((x) => ({ ...x, ...p }));
  const cartao = f.kind === "cartao";
  return (
    <form className="flex flex-col gap-2.5" onSubmit={(ev) => { ev.preventDefault();
      enviar({ nome: f.nome, kind: f.kind, saldo_inicial: cartao ? 0 : numero(f.saldo), closing_day: Number(f.fecha) || null,
               due_day: Number(f.vence) || null, credit_limit: f.limite ? numero(f.limite) : null }); }}>
      <Campo rotulo="Nome"><input autoFocus className={campo} placeholder="Ex.: Nubank" value={f.nome} onChange={(ev) => set({ nome: ev.target.value })} /></Campo>
      <Campo rotulo="Tipo">
        <select className={`${campo} cursor-pointer`} value={f.kind} onChange={(ev) => set({ kind: ev.target.value })}>
          {Object.entries(KINDS).filter(([k]) => k !== "investimento").map(([k, r]) => <option key={k} value={k}>{r}</option>)}
        </select>
      </Campo>
      {cartao ? (
        <div className="grid grid-cols-3 gap-2">
          <Campo rotulo="Fecha dia"><input type="number" min={1} max={31} className={`${campo} font-mono`} value={f.fecha} onChange={(ev) => set({ fecha: ev.target.value })} /></Campo>
          <Campo rotulo="Vence dia"><input type="number" min={1} max={31} className={`${campo} font-mono`} value={f.vence} onChange={(ev) => set({ vence: ev.target.value })} /></Campo>
          <Campo rotulo="Limite (R$)"><input inputMode="decimal" className={`${campo} font-mono`} value={f.limite} onChange={(ev) => set({ limite: ev.target.value })} /></Campo>
        </div>
      ) : (
        <Campo rotulo="Saldo atual (R$)"><input inputMode="decimal" className={inputValor} placeholder="0,00" value={f.saldo} onChange={(ev) => set({ saldo: ev.target.value })} /></Campo>
      )}
      {rodape("Criar conta", !!f.nome.trim() && (!cartao || (!!f.fecha && !!f.vence)))}
    </form>
  );
}

function FormInvestimento(props: { d: Dados; onSalvar: (b: Record<string, unknown>) => Promise<unknown> }) {
  const [f, setF] = useState({ nome: "", tipo: "cdb", valor: "", data: hoje(), conta: "", rendimento: "100", reserva: false });
  const { enviar, rodape } = useEnvio(props.onSalvar);
  const set = (p: Partial<typeof f>) => setF((x) => ({ ...x, ...p }));
  return (
    <form className="flex flex-col gap-2.5" onSubmit={(ev) => { ev.preventDefault();
      enviar({ nome: f.nome, tipo: f.tipo, valor: numero(f.valor), data: f.data, conta: f.conta || null, rendimento: numero(f.rendimento), reserva: f.reserva }); }}>
      <Campo rotulo="Nome"><input autoFocus className={campo} placeholder="Ex.: CDB Inter 110%" value={f.nome} onChange={(ev) => set({ nome: ev.target.value })} /></Campo>
      <div className="grid grid-cols-2 gap-2">
        <Campo rotulo="Tipo">
          <select className={`${campo} cursor-pointer`} value={f.tipo} onChange={(ev) => set({ tipo: ev.target.value })}>
            {Object.entries(TIPOS_INV).map(([k, r]) => <option key={k} value={k}>{r}</option>)}
          </select>
        </Campo>
        <Campo rotulo={f.tipo === "tesouro_prefixado" ? "Taxa (% a.a.)" : "% do CDI"}><input inputMode="decimal" className={`${campo} font-mono`} value={f.rendimento} onChange={(ev) => set({ rendimento: ev.target.value })} /></Campo>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Campo rotulo="Valor aplicado (R$)"><input inputMode="decimal" className={inputValor} placeholder="0,00" value={f.valor} onChange={(ev) => set({ valor: ev.target.value })} /></Campo>
        <Campo rotulo="Data"><input type="date" className={`${campo} [color-scheme:dark]`} value={f.data} onChange={(ev) => set({ data: ev.target.value })} /></Campo>
      </div>
      <Campo rotulo="Dinheiro saiu de">
        <SelectConta contas={props.d.contas_lista.filter((c) => c.kind !== "investimento" && c.kind !== "cartao")} valor={f.conta} onChange={(conta) => set({ conta })} vazio="Já era investido (não mexe no caixa)" />
      </Campo>
      <label className="flex items-center gap-2 text-[12.5px] text-fg-2">
        <input type="checkbox" checked={f.reserva} onChange={(ev) => set({ reserva: ev.target.checked })} /> Reserva (dinheiro guardado)
      </label>
      {rodape("Criar investimento", !!f.nome.trim() && numero(f.valor) > 0)}
    </form>
  );
}

function FormMovimento(props: { d: Dados; inv: InvL; tipo: "aporte" | "resgate"; onSalvar: (b: Record<string, unknown>) => Promise<unknown> }) {
  const [f, setF] = useState({ valor: "", data: hoje(), conta: "" });
  const { enviar, rodape } = useEnvio(props.onSalvar);
  const set = (p: Partial<typeof f>) => setF((x) => ({ ...x, ...p }));
  const v = numero(f.valor);
  const demais = props.tipo === "resgate" && v > props.inv.patrimonio + 0.005;
  return (
    <form className="flex flex-col gap-2.5" onSubmit={(ev) => { ev.preventDefault(); enviar({ valor: v, data: f.data, conta: f.conta || null }); }}>
      <div className="grid grid-cols-2 gap-2">
        <Campo rotulo="Valor (R$)"><input autoFocus inputMode="decimal" className={inputValor} placeholder="0,00" value={f.valor} onChange={(ev) => set({ valor: ev.target.value })} /></Campo>
        <Campo rotulo="Data"><input type="date" className={`${campo} [color-scheme:dark]`} value={f.data} onChange={(ev) => set({ data: ev.target.value })} /></Campo>
      </div>
      <Campo rotulo={props.tipo === "aporte" ? "Dinheiro saiu de" : "Dinheiro vai para"}>
        <SelectConta contas={props.d.contas_lista.filter((c) => c.kind !== "investimento" && c.kind !== "cartao")} valor={f.conta}
                     onChange={(conta) => set({ conta })} vazio="Só registrar (não mexe no caixa)" />
      </Campo>
      <p className={`text-[11px] ${demais ? "text-err" : "text-faint"}`}>
        Patrimônio {brl.format(props.inv.patrimonio)} (capital {brl.format(props.inv.capital)} + rendimento {brl.format(props.inv.rendimento)}).
        {props.tipo === "resgate" && " O resgate tira do capital primeiro; o excedente sai do rendimento."}
      </p>
      {rodape(props.tipo === "aporte" ? "Aportar" : "Resgatar", v > 0 && !demais)}
    </form>
  );
}

// ------------------------------------------------------------------ conexão: QR do IPorte + login

function Conexao(props: { e: Estado; onError: (e: string) => void; onPronto: (r: Partial<Estado>) => void; onFechar?: () => void }) {
  const { e, onError } = props;
  const [par, setPar] = useState<Par | null>(null);
  const [email, setEmail] = useState(e.email ?? "");
  const [senha, setSenha] = useState("");
  const [entrando, setEntrando] = useState(false);
  const [erro, setErro] = useState("");
  useEffect(() => { api.get<Par>("/iporte/pareamento").then(setPar).catch((er) => onError(er.message)); }, [onError]);
  // o IPorte manda e-mail e projeto pelo /iporte/v1/hello: com a tela aberta, o formulário preenche sozinho
  useEffect(() => {
    if (e.tem_projeto) return;
    const t = setInterval(() => api.get<Estado>("/financas").then((r) => {
      if (r.tem_projeto) { props.onPronto(r); setEmail((x) => x || r.email || ""); }
    }).catch(() => {}), 3000);
    return () => clearInterval(t);
  }, [e.tem_projeto]);

  const entrar = () => {
    setEntrando(true);
    setErro("");
    api.post<Estado>("/financas/conectar", { email, senha })
      .then((r) => { setSenha(""); props.onPronto(r); })
      .catch((er) => setErro(er.message)).finally(() => setEntrando(false));
  };
  const conteudo = par && JSON.stringify(par);
  const qr = conteudo && (par!.url || par!.lan) ? (() => { const q = qrcode(0, "M"); q.addData(conteudo); q.make(); return q.createSvgTag({ cellSize: 5, margin: 0 }); })() : "";

  return (
    <div className="mx-auto flex w-full max-w-[880px] flex-col gap-6 px-6 py-10">
      <div className="flex items-start gap-3">
        <div>
          <h2 className="text-[20px] font-semibold text-fg">Finanças</h2>
          <p className="mt-1 max-w-[560px] text-[13px] leading-relaxed text-muted">
            Conecte o IPorte para ver suas contas aqui e pedir análises, projeções e dicas de economia à IA. Pareado, o
            IPorte também usa a IA deste PC para ler as notificações do banco e lançar as transações.
          </p>
        </div>
        <span className="flex-1" />
        {props.onFechar && <button aria-label="Fechar" className="rounded-[7px] p-1.5 text-faint hover:bg-raised hover:text-fg" onClick={props.onFechar}><X className="size-4" /></button>}
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <section className="flex flex-col gap-3 rounded-[14px] border border-line bg-surface p-5">
          <div className="flex items-center gap-2">
            <Passo n={1} feito={!!e.dispositivo} />
            <h3 className="text-[13.5px] font-semibold text-fg">Parear o IPorte</h3>
          </div>
          <p className="text-[12.5px] leading-relaxed text-muted">No IPorte: Ajustes → Inteligência artificial → Parear com o Forja.</p>
          {qr ? (
            <div className="self-start rounded-xl bg-white p-3" dangerouslySetInnerHTML={{ __html: qr }} />
          ) : (
            <CartaoEstado tom="aviso" titulo="Sem endereço para o celular">
              Ligue a rede local na aba Celular das Configurações, ou o Tailscale neste PC e no celular.
            </CartaoEstado>
          )}
          <p className="font-mono text-[11px] text-faint">{[par?.lan, par?.url].filter(Boolean).join(" · ")}</p>
          <div className="flex flex-wrap gap-2">
            {conteudo && <button className={botaoEstado} onClick={() => navigator.clipboard.writeText(`iporte://parear-forja?c=${encodeURIComponent(conteudo)}`)}>Copiar link</button>}
            <Confirma className={botaoEstado} rotulo="Revogar e gerar novo QR" pergunta="O IPorte pareado perde o acesso à IA deste PC. Continuar?"
                      onSim={() => api.post<Par>("/iporte/rotate").then(setPar).catch((er) => onError(er.message))} />
          </div>
          {e.dispositivo && <p className="text-[12px] text-ok">{e.dispositivo} pareado.</p>}
        </section>

        <section className="flex flex-col gap-3 rounded-[14px] border border-line bg-surface p-5">
          <div className="flex items-center gap-2">
            <Passo n={2} feito={e.conectado} />
            <h3 className="text-[13.5px] font-semibold text-fg">Entrar com a conta do IPorte</h3>
          </div>
          <p className="text-[12.5px] leading-relaxed text-muted">
            O mesmo e-mail e senha do app. A senha não fica guardada: o Forja mantém uma sessão própria, cifrada neste PC.
          </p>
          <form className="flex flex-col gap-1.5" onSubmit={(ev) => { ev.preventDefault(); entrar(); }}>
            <Caixa rotulo="E-mail"><input className={campo} type="email" autoComplete="username" value={email} onChange={(ev) => setEmail(ev.target.value)} /></Caixa>
            <Caixa rotulo="Senha"><input className={campo} type="password" autoComplete="current-password" value={senha} onChange={(ev) => setSenha(ev.target.value)} /></Caixa>
            {erro && <p className="text-[12px] text-err">{erro}</p>}
            <div className="mt-1 flex items-center gap-2">
              <button type="submit" className={botaoEstadoPrimario} disabled={entrando || !email || !senha}>
                {entrando ? "Entrando…" : e.conectado ? "Entrar de novo" : "Entrar"}
              </button>
              <span className="flex-1" />
              {e.conectado && <Confirma className="text-[11.5px] text-faint hover:text-err" rotulo="Sair" pergunta="Sair da conta do IPorte neste PC?"
                                        onSim={() => api.post<Estado>("/financas/desconectar").then(props.onPronto).catch((er) => onError(er.message))} />}
            </div>
          </form>
        </section>
      </div>
    </div>
  );
}

function Passo({ n, feito }: { n: number; feito: boolean }) {
  return (
    <span className={`grid size-5 place-items-center rounded-full text-[11px] font-semibold ${feito ? "bg-ok text-white" : "border border-line-strong text-muted"}`}>
      {feito ? "✓" : n}
    </span>
  );
}

// ------------------------------------------------------------------ painel de números

function Painel({ d }: { d: Dados }) {
  const k = d.kpis;
  const delta = (v: number, media: number) => (media ? (v - media) / media : null);
  return (
    <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-4 p-4">
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi rotulo="Receitas do mês" valor={brl.format(k.receitas)} delta={delta(k.receitas, k.media_receitas_3m)} bom="sobe" />
        <Kpi rotulo="Despesas do mês" valor={brl.format(k.despesas)} delta={delta(k.despesas, k.media_despesas_3m)} bom="desce" />
        <Kpi rotulo="Caixa" valor={brl.format(k.caixa)}
             nota={[k.investido && `investido ${brl.format(k.investido)}`, k.cartoes_a_pagar && `cartões ${brl.format(k.cartoes_a_pagar)}`].filter(Boolean).join(" · ") || `fluxo do mês ${brl.format(k.fluxo)}`} />
        <Kpi rotulo="Taxa de poupança" valor={k.poupanca === null ? "—" : `${Math.round(k.poupanca * 100)}%`}
             nota={k.poupanca === null ? "sem receita no mês" : k.poupanca >= 0.2 ? "saudável (≥ 20%)" : "abaixo de 20%"} />
      </div>

      <Bloco titulo="Receitas × despesas" extra={<Legenda />}>
        {/* começa no primeiro mês com movimento (conta nova não mostra meses vazios), no mínimo 6 */}
        <Barras serie={d.serie.slice(Math.min(d.serie.length - 6, Math.max(0, d.serie.findIndex((s) => s.receitas || s.despesas))))} />
      </Bloco>

      <div className="grid gap-4 lg:grid-cols-2">
        <Bloco titulo="Gastos por categoria" extra={<span className="text-[11px] text-faint">▏ média de 3 meses</span>}>
          <Categorias cats={d.categorias} />
        </Bloco>
        <Bloco titulo="Caixa e projeção" extra={<span className="text-[11px] text-faint">tracejado = projeção</span>}>
          <Projecao d={d} />
        </Bloco>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Bloco titulo="Maiores gastos do mês">
          <Lista vazio="Nenhum gasto ainda." itens={d.maiores.map((m) => ({ a: m.descricao, b: `${m.categoria} · ${m.data.slice(8, 10)}/${m.data.slice(5, 7)}`, v: m.valor }))} />
        </Bloco>
        <Bloco titulo="Assinaturas" extra={<span className="font-mono text-[11px] text-faint">{brl.format(d.fixos_mensais)}/mês</span>}>
          <Lista vazio="Nenhuma assinatura ativa." itens={d.assinaturas.map((a) => ({ a: a.nome, b: a.dia ? `todo dia ${a.dia}` : "", v: a.valor }))} />
        </Bloco>
        <Bloco titulo="Contas">
          <Lista vazio="Nenhuma conta." itens={d.contas.map((c) => ({ a: c.nome, b: c.tipo, v: c.saldo }))} sinal />
        </Bloco>
      </div>

      {d.orcamentos.length > 0 && (
        <Bloco titulo="Orçamentos do mês">
          <div className="grid gap-x-6 gap-y-2.5 md:grid-cols-2">
            {d.orcamentos.map((o) => {
              const p = o.limite ? o.gasto / o.limite : 0;
              return (
                <div key={o.categoria} className="flex flex-col gap-1">
                  <div className="flex items-baseline gap-2 text-[12.5px]">
                    <span className="truncate text-fg">{o.categoria}</span>
                    <span className="flex-1" />
                    <span className="font-mono text-[11.5px] text-muted">{brl.format(o.gasto)} de {brl.format(o.limite)}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-raised">
                    <div className={`h-full rounded-full ${p > 1 ? "bg-err" : p > 0.8 ? "bg-warn" : "bg-ok"}`} style={{ width: `${Math.min(100, p * 100)}%` }} />
                  </div>
                  {p > 1 && <span className="text-[11px] text-err">⚠ estourado em {brl.format(o.gasto - o.limite)}</span>}
                </div>
              );
            })}
          </div>
        </Bloco>
      )}
    </div>
  );
}

function Kpi(props: { rotulo: string; valor: string; delta?: number | null; bom?: "sobe" | "desce"; nota?: string }) {
  const { delta } = props;
  const melhor = delta == null ? null : props.bom === "sobe" ? delta >= 0 : delta <= 0;
  return (
    <div className="flex flex-col gap-1 rounded-[12px] border border-line bg-surface px-4 py-3">
      <span className="text-[11.5px] text-muted">{props.rotulo}</span>
      <span className="font-mono text-[20px] font-semibold tracking-tight text-fg">{props.valor}</span>
      {delta != null ? (
        <span className={`text-[11.5px] ${melhor ? "text-ok" : "text-err"}`}>
          {delta >= 0 ? "▲" : "▼"} {Math.abs(Math.round(delta * 100))}% <span className="text-faint">vs média de 3 meses</span>
        </span>
      ) : <span className="text-[11.5px] text-faint">{props.nota}</span>}
    </div>
  );
}

function Bloco(props: { titulo: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-[12px] border border-line bg-surface p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-[12.5px] font-semibold text-fg">{props.titulo}</h3>
        <span className="flex-1" />
        {props.extra}
      </div>
      {props.children}
    </section>
  );
}

function Legenda() {
  return (
    <span className="flex items-center gap-3 text-[11px] text-muted">
      <span className="flex items-center gap-1"><span className="size-2 rounded-[2px] bg-ok" />Receitas</span>
      <span className="flex items-center gap-1"><span className="size-2 rounded-[2px] bg-err" />Despesas</span>
    </span>
  );
}

/** Barras agrupadas por mês. Hover: a coluna do mês destaca e mostra os dois valores. */
function Barras({ serie }: { serie: Mes[] }) {
  const [foco, setFoco] = useState<number | null>(null);
  const W = 1000, H = 230, base = H - 24, topo = 12, calha = 58;
  const max = Math.max(1, ...serie.flatMap((s) => [s.receitas, s.despesas]));
  const passo = (W - calha) / serie.length, larg = Math.min(16, passo / 3.4);
  const y = (v: number) => base - (v / max) * (base - topo);
  const f = foco !== null ? serie[foco] : null;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" onMouseLeave={() => setFoco(null)}>
        {[0.5, 1].map((p) => (
          <g key={p}>
            <line x1={calha} x2={W} y1={y(max * p)} y2={y(max * p)} className="stroke-line" strokeDasharray="2 4" />
            <text x={calha - 8} y={y(max * p) + 4} textAnchor="end" className="fill-faint text-[11px]">{brlCurto.format(max * p)}</text>
          </g>
        ))}
        <line x1={calha} x2={W} y1={base} y2={base} className="stroke-line-strong" />
        {serie.map((s, i) => {
          const cx = calha + passo * i + passo / 2;
          return (
            <g key={s.mes} onMouseEnter={() => setFoco(i)}>
              <rect x={calha + passo * i} y={0} width={passo} height={H} className={foco === i ? "fill-raised/50" : "fill-transparent"} />
              <rect x={cx - larg - 1} y={y(s.receitas)} width={larg} height={Math.max(0, base - y(s.receitas))} rx={3} className="fill-ok" />
              <rect x={cx + 1} y={y(s.despesas)} width={larg} height={Math.max(0, base - y(s.despesas))} rx={3} className="fill-err" />
              <text x={cx} y={H - 6} textAnchor="middle" className={`text-[11.5px] ${i === serie.length - 1 ? "fill-fg" : "fill-faint"}`}>{nomeMes(s.mes)}</text>
            </g>
          );
        })}
      </svg>
      {f && (
        <div className="pointer-events-none absolute top-0 rounded-[8px] border border-line bg-raised px-2.5 py-1.5 text-[11.5px] shadow-lg"
             style={{ left: `${Math.min(80, ((calha + passo * foco!) / W) * 100)}%` }}>
          <div className="font-medium text-fg">{nomeMes(f.mes)} {f.mes.slice(0, 4)}</div>
          <div className="text-muted">Receitas <span className="font-mono text-fg">{brl.format(f.receitas)}</span></div>
          <div className="text-muted">Despesas <span className="font-mono text-fg">{brl.format(f.despesas)}</span></div>
          <div className="text-muted">Sobra <span className="font-mono text-fg">{brl.format(f.receitas - f.despesas)}</span></div>
        </div>
      )}
    </div>
  );
}

/** Barras horizontais (uma cor só: magnitude), com a marca da média de 3 meses para ver o que fugiu do normal. */
function Categorias({ cats }: { cats: Dados["categorias"] }) {
  if (!cats.length) return <p className="text-[12.5px] text-muted">Nenhum gasto no mês.</p>;
  const top = cats.slice(0, 8);
  const resto = cats.slice(8).reduce((a, c) => a + c.valor, 0);
  const linhas = resto ? [...top, { categoria: "Outras", valor: resto, cor: null, media_3m: 0 }] : top;
  const max = Math.max(...linhas.flatMap((c) => [c.valor, c.media_3m]));
  return (
    <div className="flex flex-col gap-2">
      {linhas.map((c) => {
        const acima = c.media_3m > 0 && c.valor > c.media_3m * 1.15;
        return (
          <div key={c.categoria} className="group flex flex-col gap-1" title={`${c.categoria}: ${brl.format(c.valor)} · média ${brl.format(c.media_3m)}`}>
            <div className="flex items-baseline gap-2 text-[12.5px]">
              <span className="truncate text-fg">{c.categoria}</span>
              {acima && <span className="text-[10.5px] text-warn">▲ acima da média</span>}
              <span className="flex-1" />
              <span className="font-mono text-[11.5px] text-muted">{brl.format(c.valor)}</span>
            </div>
            <div className="relative h-2 rounded-full bg-raised">
              <div className="h-full rounded-full bg-accent group-hover:brightness-125" style={{ width: `${(c.valor / max) * 100}%` }} />
              {c.media_3m > 0 && <div className="absolute -top-0.5 h-3 w-0.5 rounded bg-fg-2" style={{ left: `${(c.media_3m / max) * 100}%` }} />}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Caixa no fim dos últimos 6 meses (calculado no backend) e os 3 meses projetados. */
function Projecao({ d }: { d: Dados }) {
  const [foco, setFoco] = useState<number | null>(null);
  const hist: { mes: string; saldo: number; proj?: boolean }[] = d.historico.slice(-6);
  const pts = [...hist, ...d.projecao.map((p) => ({ ...p, proj: true }))];
  const W = 520, H = 230, base = H - 24, topo = 30, esq = 4;
  const vals = pts.map((p) => p.saldo);
  const lo = Math.min(0, ...vals), hi = Math.max(1, ...vals);
  const x = (i: number) => esq + (i / (pts.length - 1 || 1)) * (W - esq * 2 - 30) + 15;
  const y = (v: number) => base - ((v - lo) / (hi - lo || 1)) * (base - topo);
  const caminho = (de: number, ate: number) => pts.slice(de, ate + 1).map((p, j) => `${j ? "L" : "M"}${x(de + j)},${y(p.saldo)}`).join(" ");
  const corte = hist.length - 1;
  const negativo = d.projecao.some((p) => p.saldo < 0);
  const f = foco !== null ? pts[foco] : null;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" onMouseLeave={() => setFoco(null)}>
        {lo < 0 && <line x1={0} x2={W} y1={y(0)} y2={y(0)} className="stroke-err/50" strokeDasharray="3 3" />}
        <line x1={0} x2={W} y1={base} y2={base} className="stroke-line-strong" />
        <path d={caminho(0, corte)} className="fill-none stroke-accent" strokeWidth={2} strokeLinejoin="round" />
        <path d={caminho(corte, pts.length - 1)} className={`fill-none ${negativo ? "stroke-err" : "stroke-accent"}`} strokeWidth={2} strokeDasharray="5 4" />
        {pts.map((p, i) => (
          <g key={p.mes} onMouseEnter={() => setFoco(i)}>
            <rect x={x(i) - 18} y={0} width={36} height={H} className="fill-transparent" />
            <circle cx={x(i)} cy={y(p.saldo)} r={foco === i ? 5 : 3.5} className={`${p.proj ? "fill-bg" : "fill-accent"} ${p.proj && p.saldo < 0 ? "stroke-err" : "stroke-accent"}`} strokeWidth={2} />
            <text x={x(i)} y={H - 6} textAnchor="middle" className={`text-[10.5px] ${p.proj ? "fill-muted" : "fill-faint"}`}>{nomeMes(p.mes)}</text>
          </g>
        ))}
        <text x={x(pts.length - 1)} y={y(pts[pts.length - 1].saldo) - 9} textAnchor="end" className="fill-fg text-[11px] font-medium">
          {brlCurto.format(pts[pts.length - 1].saldo)}
        </text>
      </svg>
      {f && (
        <div className="pointer-events-none absolute top-0 rounded-[8px] border border-line bg-raised px-2.5 py-1.5 text-[11.5px] shadow-lg"
             style={{ left: `${Math.min(62, (x(foco!) / W) * 100)}%` }}>
          <div className="font-medium text-fg">{nomeMes(f.mes)} {f.mes.slice(0, 4)}{f.proj ? " · projeção" : ""}</div>
          <div className="font-mono text-fg">{brl.format(f.saldo)}</div>
        </div>
      )}
      {negativo && <p className="mt-1 text-[11.5px] text-err">⚠ No ritmo atual o saldo fica negativo nos próximos meses.</p>}
    </div>
  );
}

function Lista(props: { itens: { a: string; b: string; v: number }[]; vazio: string; sinal?: boolean }) {
  if (!props.itens.length) return <p className="text-[12.5px] text-muted">{props.vazio}</p>;
  return (
    <ul className="flex flex-col divide-y divide-line">
      {props.itens.map((it, i) => (
        <li key={i} className="flex items-center gap-2 py-1.5">
          <div className="min-w-0 flex-1">
            <div className="truncate text-[12.5px] text-fg">{it.a}</div>
            {it.b && <div className="truncate text-[11px] text-faint">{it.b}</div>}
          </div>
          <span className={`font-mono text-[12px] ${props.sinal && it.v < 0 ? "text-err" : "text-fg-2"}`}>{brl.format(it.v)}</span>
        </li>
      ))}
    </ul>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import { Check, ChevronDown, Copy, Edit, ExternalLink, Play, Refresh, Square, Star, X } from "./icons";

const card = "rounded-xl border border-line bg-surface p-3.5";
const btnPrimary = "inline-flex items-center gap-1.5 rounded-[9px] border border-accent bg-accent px-3 py-1.5 font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";
const btnPeq = "inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[12px] text-fg hover:border-focus hover:bg-raised disabled:opacity-40";
const campo = "w-full rounded-lg border border-line bg-raised px-2.5 py-1.5 text-[13px] text-fg focus:border-focus focus:outline-none";

type Fonte = { id: string; url: string; titulo: string; dominio?: string; status: string; erro?: string };
type Cena = { id: string; texto: string };
type StatusRoteiro = "novo" | "aprovado" | "descartado" | "produzido";
type Roteiro = {
  id: string;
  status: StatusRoteiro;
  titulo: string;
  ideia: string;
  noticia: { resumo: string; data: string; fontes: { titulo: string; url: string }[] };
  cenas: Cena[];
  titulo_youtube: string;
  descricao: string;
  confianca: number;
  motivo_confianca: string;
  palavras: number;
  segundos: number;
};
type Rodada = {
  id: number;
  conv_id: number;
  criado: string | null;
  status: "rodando" | "aguardando" | "ok" | "erro" | "cancelado";
  fase: string;
  aviso: string;
  estilo: string;
  motor: { provider: string; model: string };
  fontes: Fonte[];
  roteiros: Roteiro[];
  stats: { fontes: number; uteis: number; segundos: number; tokens: number; escritor?: string };
};

const FASES: Record<string, string> = {
  planejando: "Planejando as buscas", buscando: "Buscando novidades", lendo: "Lendo as fontes",
  escrevendo: "Escrevendo os roteiros", aguardando: "Esperando o Claude", pronto: "Pronto",
};
const CORES: Record<StatusRoteiro, string> = {
  novo: "border-line", aprovado: "border-emerald-500/70", descartado: "border-line opacity-55", produzido: "border-sky-500/70",
};
const CONFIANCA = ["", "muito baixa", "baixa", "média", "boa", "alta"];

const relogio = (seg: number) => `${Math.floor(seg / 60)}:${String(Math.floor(seg % 60)).padStart(2, "0")}`;

function textoParaCopiar(r: Roteiro): string {
  return [r.titulo_youtube || r.titulo, "", ...r.cenas.map((c) => `[${c.id}] ${c.texto}`), "", r.descricao].join("\n");
}

export default function ConteudoRoteiros(props: {
  conv: number;
  carimbo?: string;
  onError: (msg: string) => void;
  onProduzindo: () => void;
}) {
  const [rodadas, setRodadas] = useState<Rodada[]>([]);
  const [disparando, setDisparando] = useState(false);
  const ouvindo = useRef<number>(0);

  const carregar = useCallback(async () => {
    try {
      setRodadas(await api.get<Rodada[]>(`/conteudo/especificacoes/${props.conv}/roteiros`));
    } catch (e: any) {
      props.onError(e.message);
    }
  }, [props.conv]);

  // Rodada viva: ouve o SSE e substitui a rodada na lista a cada retrato.
  const ouvir = useCallback(async (id: number) => {
    if (ouvindo.current === id) return;
    ouvindo.current = id;
    try {
      await streamSSE(`/conteudo/roteiros/${id}/stream`, {}, (ev) => {
        if (ev.erro) return;
        setRodadas((rs) => rs.map((r) => (r.id === id ? { ...r, ...ev } : r)));
      });
    } catch {
      /* conexão caiu: o carimbo traz o estado final */
    } finally {
      if (ouvindo.current === id) ouvindo.current = 0;
      carregar();
    }
  }, [carregar]);

  useEffect(() => { carregar(); }, [carregar, props.carimbo]);
  useEffect(() => {
    const viva = rodadas.find((r) => r.status === "rodando");
    if (viva) ouvir(viva.id);
  }, [rodadas, ouvir]);

  async function gerar() {
    setDisparando(true);
    try {
      const r = await api.post<Rodada>(`/conteudo/especificacoes/${props.conv}/roteiros`, {});
      setRodadas((rs) => [r, ...rs]);
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setDisparando(false);
    }
  }

  async function acao(fn: () => Promise<Rodada>) {
    try {
      const nova = await fn();
      setRodadas((rs) => rs.map((r) => (r.id === nova.id ? nova : r)));
      carregar();   // aprovar pode ter tirado a aprovação de um roteiro de outra rodada
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  const ocupada = rodadas.some((r) => r.status === "rodando" || r.status === "aguardando");

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div className="flex items-center gap-3">
        <button className={btnPrimary} disabled={disparando || ocupada} onClick={gerar}>
          <Refresh className="size-4" /> Pesquisar e gerar roteiros
        </button>
        {ocupada && <span className="text-[12px] text-muted">Já tem uma pesquisa em andamento.</span>}
      </div>
      {rodadas.length === 0 && (
        <div className={`${card} text-[13px] text-muted`}>
          Nenhum roteiro ainda. A pesquisa procura novidades do tema na web e escreve os roteiros no estilo escolhido,
          com a notícia, as fontes e uma nota de confiança dos fatos.
        </div>
      )}
      {rodadas.map((r) => (
        <RodadaCard key={r.id} rodada={r} onError={props.onError}
                    onCancelar={() => api.post(`/conteudo/roteiros/${r.id}/cancelar`, {}).then(carregar)}
                    onStatus={(rid, status) => acao(() => api.post<Rodada>(`/conteudo/roteiros/${r.id}/${rid}/status`, { status }))}
                    onEditar={(rid, campos) => acao(() => api.put<Rodada>(`/conteudo/roteiros/${r.id}/${rid}`, campos))}
                    onProduzir={async (rid) => {
                      try {
                        await api.post(`/conteudo/especificacoes/${props.conv}/producao`, { message_id: r.id, roteiro_id: rid });
                        props.onProduzindo();
                      } catch (e: any) {
                        props.onError(e.message);
                      }
                    }} />
      ))}
    </div>
  );
}

function RodadaCard(props: {
  rodada: Rodada;
  onError: (m: string) => void;
  onCancelar: () => void;
  onStatus: (rid: string, status: StatusRoteiro) => void;
  onEditar: (rid: string, campos: Partial<Roteiro>) => void;
  onProduzir: (rid: string) => void;
}) {
  const r = props.rodada;
  const [verFontes, setVerFontes] = useState(false);
  const quando = r.criado ? new Date(r.criado + (r.criado.endsWith("Z") ? "" : "Z")).toLocaleString().slice(0, 17) : "";
  const viva = r.status === "rodando" || r.status === "aguardando";
  const motor = r.motor?.provider === "claude-mcp" ? "Claude (MCP)" : r.stats?.escritor || r.motor?.model;

  return (
    <div className={`${card} flex flex-col gap-3`}>
      <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
        <span className="font-medium text-fg">{quando}</span>
        <span className="text-muted">· {r.estilo} · {motor}</span>
        {viva ? (
          <span className="inline-flex items-center gap-1.5 text-sky-300">
            <span className="size-2 animate-pulse rounded-full bg-sky-400" />
            {FASES[r.fase] ?? r.fase}{r.status === "rodando" && r.stats ? ` · ${relogio(r.stats.segundos)}` : ""}
          </span>
        ) : (
          <span className={r.status === "ok" ? "text-emerald-300" : r.status === "erro" ? "text-red-300" : "text-muted"}>
            {r.status === "ok" ? `${r.roteiros.length} roteiro(s)` : r.status === "erro" ? "Erro" : "Cancelada"}
          </span>
        )}
        {r.fontes.length > 0 && (
          <button className="text-muted hover:text-fg" onClick={() => setVerFontes(!verFontes)}>
            · {r.stats?.uteis ?? 0}/{r.fontes.length} fontes úteis <ChevronDown className="inline size-3" />
          </button>
        )}
        {viva && <button className={`${btnPeq} ml-auto`} onClick={props.onCancelar}><Square className="size-3" /> Cancelar</button>}
      </div>
      {r.status === "aguardando" && (
        <div className="text-[12.5px] text-muted">
          O pedido está na fila do Claude. Numa sessão do Claude Code ou do Claude Desktop conectada ao Forja, peça:
          <span className="ml-1 font-mono text-fg">"atenda os pedidos de conteudo_pedidos"</span>.
        </div>
      )}
      {r.aviso && <div className="text-[12.5px] text-amber-300">{r.aviso}</div>}
      {verFontes && (
        <ul className="flex flex-col gap-1 text-[12px]">
          {r.fontes.map((f) => (
            <li key={f.id} className="flex items-center gap-2">
              <span className={f.status === "util" ? "text-emerald-300" : f.status === "erro" ? "text-red-300" : "text-faint"}>●</span>
              <a className="truncate text-muted hover:text-fg hover:underline" href={f.url} target="_blank" rel="noreferrer">{f.titulo}</a>
            </li>
          ))}
        </ul>
      )}
      {r.roteiros.map((x) => (
        <RoteiroCard key={x.id} roteiro={x} onStatus={(s) => props.onStatus(x.id, s)} onEditar={(c) => props.onEditar(x.id, c)}
                     onProduzir={() => props.onProduzir(x.id)} />
      ))}
    </div>
  );
}

function RoteiroCard(props: {
  roteiro: Roteiro;
  onStatus: (s: StatusRoteiro) => void;
  onEditar: (c: Partial<Roteiro>) => void;
  onProduzir: () => void;
}) {
  const x = props.roteiro;
  const [aberto, setAberto] = useState(x.status === "aprovado");
  const [editando, setEditando] = useState(false);
  const [cenas, setCenas] = useState<Cena[]>(x.cenas);
  const [tituloYt, setTituloYt] = useState(x.titulo_youtube);
  const [copiado, setCopiado] = useState(false);
  useEffect(() => { if (!editando) { setCenas(x.cenas); setTituloYt(x.titulo_youtube); } }, [x, editando]);
  const travado = x.status === "produzido";

  return (
    <div className={`rounded-xl border bg-raised/40 p-3 ${CORES[x.status]}`}>
      <div className="flex flex-wrap items-start gap-2">
        <button className="min-w-0 flex-1 text-left" onClick={() => setAberto(!aberto)}>
          <div className="flex items-center gap-2">
            {x.status === "aprovado" && <span className="rounded bg-emerald-500/20 px-1.5 text-[11px] text-emerald-300">aprovado</span>}
            {x.status === "produzido" && <span className="rounded bg-sky-500/20 px-1.5 text-[11px] text-sky-300">virou vídeo</span>}
            <span className="truncate text-[14px] font-semibold text-fg">{x.titulo_youtube || x.titulo}</span>
          </div>
          <div className="mt-1 text-[12.5px] text-muted">{x.ideia}</div>
        </button>
        <div className="flex shrink-0 flex-col items-end gap-1 text-[11.5px]">
          <span title={x.motivo_confianca} className={x.confianca >= 4 ? "text-emerald-300" : x.confianca <= 2 ? "text-red-300" : "text-amber-300"}>
            <Star className="inline size-3" /> confiança {CONFIANCA[x.confianca]}
          </span>
          <span className="text-faint">{x.palavras} palavras · ~{x.segundos}s</span>
        </div>
      </div>

      {aberto && (
        <div className="mt-3 flex flex-col gap-3 text-[13px]">
          <div className="rounded-lg border border-line p-2.5">
            <div className="text-[11.5px] font-medium text-muted">A notícia{x.noticia.data && ` · ${x.noticia.data}`}</div>
            <div className="mt-1 text-fg">{x.noticia.resumo}</div>
            {x.noticia.fontes.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
                {x.noticia.fontes.map((f) => (
                  <a key={f.url} className="inline-flex items-center gap-1 text-[12px] text-accent hover:underline" href={f.url} target="_blank" rel="noreferrer">
                    <ExternalLink className="size-3" /> {f.titulo.slice(0, 60)}
                  </a>
                ))}
              </div>
            )}
            {x.motivo_confianca && <div className="mt-1.5 text-[11.5px] text-faint">Confiança: {x.motivo_confianca}</div>}
          </div>

          {editando ? (
            <div className="flex flex-col gap-2">
              <input className={campo} value={tituloYt} onChange={(e) => setTituloYt(e.target.value)} placeholder="Título do YouTube" />
              {cenas.map((c, i) => (
                <label key={c.id} className="flex flex-col gap-0.5">
                  <span className="font-mono text-[11px] text-muted">{c.id}</span>
                  <textarea className={`${campo} h-16`} value={c.texto}
                            onChange={(e) => setCenas(cenas.map((y, j) => (j === i ? { ...y, texto: e.target.value } : y)))} />
                </label>
              ))}
            </div>
          ) : (
            <ol className="flex flex-col gap-1.5">
              {x.cenas.map((c) => (
                <li key={c.id} className="flex gap-2">
                  <span className="w-20 shrink-0 font-mono text-[11px] text-muted">{c.id}</span>
                  <span className="text-fg">{c.texto}</span>
                </li>
              ))}
            </ol>
          )}
          {x.descricao && !editando && (
            <details className="text-[12px] text-muted">
              <summary className="cursor-pointer">Descrição para o YouTube</summary>
              <pre className="mt-1 whitespace-pre-wrap font-sans">{x.descricao}</pre>
            </details>
          )}

          <div className="flex flex-wrap gap-2">
            {editando ? (
              <>
                <button className={btnPeq} onClick={() => { props.onEditar({ cenas, titulo_youtube: tituloYt }); setEditando(false); }}>
                  <Check className="size-3" /> Salvar edição
                </button>
                <button className={btnPeq} onClick={() => setEditando(false)}><X className="size-3" /> Cancelar</button>
              </>
            ) : (
              <>
                {x.status !== "aprovado" ? (
                  <button className={btnPeq} disabled={travado} onClick={() => props.onStatus("aprovado")}><Check className="size-3" /> Aprovar</button>
                ) : (
                  <button className={btnPeq} onClick={() => props.onStatus("novo")}>Tirar aprovação</button>
                )}
                {x.status !== "descartado" ? (
                  <button className={btnPeq} disabled={travado} onClick={() => props.onStatus("descartado")}><X className="size-3" /> Descartar</button>
                ) : (
                  <button className={btnPeq} onClick={() => props.onStatus("novo")}>Recuperar</button>
                )}
                <button className={btnPeq} disabled={travado} onClick={() => setEditando(true)}><Edit className="size-3" /> Editar</button>
                <button className={btnPeq} disabled={travado || x.status === "descartado"} onClick={props.onProduzir}
                        title="Manda o Claude Code montar e renderizar este roteiro agora">
                  <Play className="size-3" /> Produzir agora
                </button>
                <button className={btnPeq} onClick={() => { navigator.clipboard.writeText(textoParaCopiar(x)); setCopiado(true); setTimeout(() => setCopiado(false), 1500); }}>
                  <Copy className="size-3" /> {copiado ? "Copiado" : "Copiar"}
                </button>
              </>
            )}
          </div>
          {x.status === "aprovado" && (
            <div className="text-[11.5px] text-faint">
              Aprovado: é este roteiro que a produção agendada vai pegar.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

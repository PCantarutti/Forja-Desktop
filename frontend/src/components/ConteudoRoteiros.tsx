import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import { Check, ChevronDown, Copy, Edit, ExternalLink, Play, Refresh, Square, X } from "./icons";

// Coluna dos roteiros no painel: a pesquisa ao vivo (em etapas), e os roteiros de cada rodada. Escolher um
// ("Usar no próximo vídeo") é o mesmo que aprovar: só um por especificação, e é ele que a produção pega.

const campo = "w-full rounded-lg border border-line bg-raised px-2.5 py-1.5 text-[13px] text-fg focus:border-focus focus:outline-none";
const acaoPeq = "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-muted hover:bg-raised hover:text-fg disabled:pointer-events-none disabled:opacity-40";

type Fonte = { id: string; url: string; titulo: string; status: string };
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
  criado: string | null;
  status: "rodando" | "aguardando" | "ok" | "erro" | "cancelado";
  fase: string;
  aviso: string;
  estilo: string;
  motor: { provider: string; model: string };
  fontes: Fonte[];
  roteiros: Roteiro[];
  stats: { fontes: number; uteis: number; segundos: number; escritor?: string };
};

const ETAPAS = [
  { id: "planejando", rotulo: "Planejar" },
  { id: "buscando", rotulo: "Buscar" },
  { id: "lendo", rotulo: "Ler fontes" },
  { id: "escrevendo", rotulo: "Escrever" },
];
const CONFIANCA = ["", "muito baixa", "baixa", "média", "boa", "alta"];
const corConfianca = (n: number) => (n >= 4 ? "bg-ok" : n <= 2 ? "bg-err" : "bg-warn");

const relogio = (seg: number) => `${Math.floor(seg / 60)}:${String(Math.floor(seg % 60)).padStart(2, "0")}`;
const dataCurta = (iso: string | null) => {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  const dia = d.toDateString() === new Date().toDateString() ? "Hoje" : d.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" });
  return `${dia}, ${d.toTimeString().slice(0, 5)}`;
};
const textoParaCopiar = (r: Roteiro) =>
  [r.titulo_youtube || r.titulo, "", ...r.cenas.map((c) => `[${c.id}] ${c.texto}`), "", r.descricao].join("\n");

export default function ConteudoRoteiros(props: {
  conv: number;
  carimbo?: string;
  onError: (msg: string) => void;
  onProduzindo: () => void;
}) {
  const [rodadas, setRodadas] = useState<Rodada[]>([]);
  const [carregou, setCarregou] = useState(false);
  const [disparando, setDisparando] = useState(false);
  const ouvindo = useRef<number>(0);

  const carregar = useCallback(async () => {
    try {
      setRodadas(await api.get<Rodada[]>(`/conteudo/especificacoes/${props.conv}/roteiros`));
      setCarregou(true);
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
        if (!ev.erro) setRodadas((rs) => rs.map((r) => (r.id === id ? { ...r, ...ev } : r)));
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

  async function acao(fn: () => Promise<unknown>) {
    try {
      await fn();
      await carregar();   // escolher um pode ter tirado a escolha de um roteiro de outra rodada
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function produzir(mid: number, rid: string) {
    try {
      await api.post(`/conteudo/especificacoes/${props.conv}/producao`, { message_id: mid, roteiro_id: rid });
      props.onProduzindo();
    } catch (e: any) {
      props.onError(e.message);
    }
  }


  return (
    <section className="flex min-w-0 flex-col gap-4">
      <div className="flex items-center gap-3">
        <h2 className="font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint">Roteiros</h2>
      </div>

      {carregou && rodadas.length === 0 && (
        <div className="flex flex-col items-start gap-3 rounded-[10px] border border-dashed border-line-strong px-5 py-6">
          <p className="max-w-[60ch] text-[13px] leading-relaxed text-muted">
            A pesquisa procura as novidades do tema na web e escreve roteiros prontos no estilo escolhido, cada um com a
            notícia, as fontes e uma nota de confiança dos fatos.
          </p>
          <button onClick={gerar} disabled={disparando}
                  className="inline-flex h-[30px] items-center gap-1.5 rounded-[7px] bg-accent px-3 text-xs font-medium text-accent-fg hover:brightness-110 disabled:opacity-40">
            <Refresh className="size-3.5" /> Pesquisar e escrever roteiros
          </button>
        </div>
      )}

      {rodadas.filter((r) => !(r.status === "cancelado" && r.roteiros.length === 0)).map((r) => (
        <Rodada key={r.id} r={r}
                onCancelar={() => acao(() => api.post(`/conteudo/roteiros/${r.id}/cancelar`, {}))}
                onStatus={(rid, status) => acao(() => api.post(`/conteudo/roteiros/${r.id}/${rid}/status`, { status }))}
                onEditar={(rid, campos) => acao(() => api.put(`/conteudo/roteiros/${r.id}/${rid}`, campos))}
                onProduzir={(rid) => produzir(r.id, rid)} />
      ))}
    </section>
  );
}

function Rodada(props: {
  r: Rodada;
  onCancelar: () => void;
  onStatus: (rid: string, status: StatusRoteiro) => void;
  onEditar: (rid: string, campos: Partial<Roteiro>) => void;
  onProduzir: (rid: string) => void;
}) {
  const { r } = props;
  const [verFontes, setVerFontes] = useState(false);
  const [verDescartados, setVerDescartados] = useState(false);
  const viva = r.status === "rodando" || r.status === "aguardando";
  const motor = r.motor?.provider === "claude-mcp" ? "Claude" : r.stats?.escritor || r.motor?.model;
  const ativos = r.roteiros.filter((x) => x.status !== "descartado");
  const descartados = r.roteiros.filter((x) => x.status === "descartado");

  return (
    <div className="flex flex-col gap-2">
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-faint">
        <span className="font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-muted">{dataCurta(r.criado)}</span>
        <span>· {motor}</span>
        {r.fontes.length > 0 && (
          <button className="inline-flex items-center gap-0.5 hover:text-fg" onClick={() => setVerFontes(!verFontes)}>
            · {r.stats?.uteis ?? 0} de {r.fontes.length} fontes úteis
            <ChevronDown className={`size-3 transition-transform ${verFontes ? "rotate-180" : ""}`} />
          </button>
        )}
        {r.status === "erro" && <span className="text-err">· não gerou roteiros</span>}
        {r.status === "cancelado" && <span>· cancelada</span>}
      </div>

      {viva && <Pesquisando r={r} onCancelar={props.onCancelar} />}
      {r.aviso && !viva && <p className="text-[12px] text-warn">{r.aviso}</p>}

      {verFontes && (
        <ul className="flex flex-col gap-1 rounded-[8px] border border-line bg-surface px-3 py-2 text-[12px]">
          {r.fontes.map((f) => (
            <li key={f.id} className="flex items-center gap-2">
              <span className={`size-1.5 shrink-0 rounded-full ${f.status === "util" ? "bg-ok" : f.status === "erro" ? "bg-err" : "bg-faint"}`} />
              <a className="truncate text-muted hover:text-fg hover:underline" href={f.url} target="_blank" rel="noreferrer">{f.titulo}</a>
            </li>
          ))}
        </ul>
      )}

      {ativos.map((x) => (
        <CartaoRoteiro key={x.id} x={x} onStatus={(s) => props.onStatus(x.id, s)}
                       onEditar={(c) => props.onEditar(x.id, c)} onProduzir={() => props.onProduzir(x.id)} />
      ))}
      {descartados.length > 0 && (
        <button className="self-start text-[11.5px] text-faint hover:text-fg" onClick={() => setVerDescartados(!verDescartados)}>
          {verDescartados ? "Esconder" : "Mostrar"} {descartados.length} descartado(s)
        </button>
      )}
      {verDescartados && descartados.map((x) => (
        <CartaoRoteiro key={x.id} x={x} onStatus={(s) => props.onStatus(x.id, s)}
                       onEditar={(c) => props.onEditar(x.id, c)} onProduzir={() => props.onProduzir(x.id)} />
      ))}
    </div>
  );
}

function Pesquisando(props: { r: Rodada; onCancelar: () => void }) {
  const { r } = props;
  const atual = ETAPAS.findIndex((e) => e.id === r.fase);
  return (
    <div className="flex flex-col gap-2.5 rounded-[10px] border border-info/40 bg-info/5 p-3.5">
      {r.status === "aguardando" ? (
        <p className="text-[12.5px] leading-relaxed text-fg">
          Pedido na fila do Claude. Numa sessão do Claude Code ou do Claude Desktop conectada ao Forja, peça:
          <span className="ml-1 font-mono text-info">atenda os pedidos de conteudo_pedidos</span>
        </p>
      ) : (
        <ol className="flex items-center gap-1.5 text-[12px]">
          {ETAPAS.map((e, i) => (
            <li key={e.id} className="flex items-center gap-1.5">
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 ${
                i < atual ? "text-ok" : i === atual ? "bg-info/15 text-info" : "text-faint"}`}>
                {i < atual ? <Check className="size-3" /> : i === atual ? <span className="size-1.5 animate-pulse rounded-full bg-info" /> : null}
                {e.rotulo}
              </span>
              {i < ETAPAS.length - 1 && <span className="h-px w-3 bg-line-strong" />}
            </li>
          ))}
        </ol>
      )}
      <div className="flex items-center gap-3 text-[11.5px] text-faint">
        {r.status === "rodando" && <span className="font-mono">{relogio(r.stats?.segundos ?? 0)}</span>}
        {r.fontes.length > 0 && <span>{r.fontes.length} fontes encontradas</span>}
        <button className={`${acaoPeq} ml-auto`} onClick={props.onCancelar}><Square className="size-3" /> Cancelar</button>
      </div>
    </div>
  );
}

function CartaoRoteiro(props: {
  x: Roteiro;
  onStatus: (s: StatusRoteiro) => void;
  onEditar: (c: Partial<Roteiro>) => void;
  onProduzir: () => void;
}) {
  const { x } = props;
  const [aberto, setAberto] = useState(false);
  const [editando, setEditando] = useState(false);
  const [cenas, setCenas] = useState<Cena[]>(x.cenas);
  const [tituloYt, setTituloYt] = useState(x.titulo_youtube);
  const [copiado, setCopiado] = useState(false);
  useEffect(() => { if (!editando) { setCenas(x.cenas); setTituloYt(x.titulo_youtube); } }, [x, editando]);
  const escolhido = x.status === "aprovado";
  const produzido = x.status === "produzido";
  const descartado = x.status === "descartado";

  return (
    <article className={`rounded-[10px] border transition-colors ${
      escolhido ? "border-ok/60 bg-ok/[0.04]" : "border-line bg-surface"} ${descartado ? "opacity-60" : ""}`}>
      <div className="flex gap-3 p-3.5">
        <button disabled={produzido || descartado} onClick={() => props.onStatus(escolhido ? "novo" : "aprovado")}
                title={escolhido ? "Tirar a escolha" : "Usar no próximo vídeo"} aria-pressed={escolhido}
                className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border transition-colors disabled:opacity-30 ${
                  escolhido ? "border-ok bg-ok text-bg" : "border-line-strong hover:border-ok"}`}>
          {escolhido && <Check className="size-3" />}
        </button>
        <button className="min-w-0 flex-1 text-left" onClick={() => setAberto(!aberto)} aria-expanded={aberto}>
          <h3 className="text-[13.5px] font-semibold leading-snug text-fg">{x.titulo_youtube || x.titulo}</h3>
          <p className={`mt-1 text-[12.5px] leading-relaxed text-muted ${aberto ? "" : "line-clamp-2"}`}>{x.ideia}</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-faint">
            <span className="inline-flex items-center gap-1.5" title={x.motivo_confianca}>
              <span className={`size-1.5 rounded-full ${corConfianca(x.confianca)}`} /> confiança {CONFIANCA[x.confianca]}
            </span>
            <span>~{x.segundos}s</span>
            {x.noticia.data && <span>notícia de {x.noticia.data}</span>}
            {escolhido && <span className="font-medium text-ok">escolhido para o próximo vídeo</span>}
            {produzido && <span className="font-medium text-info">virou vídeo</span>}
          </div>
        </button>
        <ChevronDown className={`mt-1 size-4 shrink-0 text-faint transition-transform ${aberto ? "rotate-180" : ""}`} />
      </div>

      {aberto && (
        <div className="flex flex-col gap-3 border-t border-line px-3.5 pb-3.5 pt-3 text-[13px]">
          <div className="flex flex-col gap-1.5">
            <span className="text-[11.5px] font-medium text-muted">A notícia</span>
            <p className="max-w-[72ch] leading-relaxed text-fg">{x.noticia.resumo}</p>
            {x.noticia.fontes.length > 0 && (
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {x.noticia.fontes.map((f) => (
                  <a key={f.url} href={f.url} target="_blank" rel="noreferrer"
                     className="inline-flex max-w-full items-center gap-1 text-[12px] text-accent-text hover:underline">
                    <ExternalLink className="size-3 shrink-0" /> <span className="truncate">{f.titulo}</span>
                  </a>
                ))}
              </div>
            )}
            {x.motivo_confianca && <p className="text-[11.5px] text-faint">Por que a nota: {x.motivo_confianca}</p>}
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-[11.5px] font-medium text-muted">Narração ({x.palavras} palavras)</span>
            {editando ? (
              <>
                <input className={campo} value={tituloYt} onChange={(e) => setTituloYt(e.target.value)} placeholder="Título do YouTube" />
                {cenas.map((c, i) => (
                  <label key={c.id} className="flex flex-col gap-0.5">
                    <span className="font-mono text-[11px] text-faint">{c.id}</span>
                    <textarea className={`${campo} h-16 leading-relaxed`} value={c.texto}
                              onChange={(e) => setCenas(cenas.map((y, j) => (j === i ? { ...y, texto: e.target.value } : y)))} />
                  </label>
                ))}
              </>
            ) : (
              <ol className="flex flex-col gap-2">
                {x.cenas.map((c) => (
                  <li key={c.id} className="grid grid-cols-[88px_1fr] gap-3">
                    <span className="truncate pt-0.5 font-mono text-[11px] text-faint">{c.id}</span>
                    <span className="max-w-[72ch] leading-relaxed text-fg">{c.texto}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>

          {x.descricao && !editando && (
            <details className="text-[12px] text-muted">
              <summary className="cursor-pointer select-none hover:text-fg">Descrição para o YouTube</summary>
              <pre className="mt-1.5 max-w-[72ch] whitespace-pre-wrap font-sans leading-relaxed">{x.descricao}</pre>
            </details>
          )}

          <div className="flex flex-wrap items-center gap-1 border-t border-line pt-2">
            {editando ? (
              <>
                <button className={acaoPeq} onClick={() => { props.onEditar({ cenas, titulo_youtube: tituloYt }); setEditando(false); }}>
                  <Check className="size-3.5" /> Salvar edição
                </button>
                <button className={acaoPeq} onClick={() => setEditando(false)}><X className="size-3.5" /> Cancelar</button>
              </>
            ) : (
              <>
                <button className={acaoPeq} disabled={produzido || descartado} onClick={props.onProduzir}
                        title="O Claude Code monta e renderiza este roteiro agora">
                  <Play className="size-3.5" /> Produzir agora
                </button>
                <button className={acaoPeq} disabled={produzido} onClick={() => setEditando(true)}><Edit className="size-3.5" /> Editar</button>
                <button className={acaoPeq}
                        onClick={() => { navigator.clipboard.writeText(textoParaCopiar(x)); setCopiado(true); setTimeout(() => setCopiado(false), 1400); }}>
                  <Copy className="size-3.5" /> {copiado ? "Copiado" : "Copiar"}
                </button>
                {descartado ? (
                  <button className={`${acaoPeq} ml-auto`} onClick={() => props.onStatus("novo")}>Recuperar</button>
                ) : (
                  <button className={`${acaoPeq} ml-auto`} disabled={produzido} onClick={() => props.onStatus("descartado")}>
                    <X className="size-3.5" /> Descartar
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </article>
  );
}

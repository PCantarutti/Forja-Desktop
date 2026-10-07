import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Check, Copy, Edit, Film, Square } from "./icons";
import ConteudoRevisao from "./ConteudoRevisao";
import { VideoPlayer, type VideoPlayerApi } from "./VideoPlayer";

// Coluna do vídeo no painel da especificação: o vídeo pronto no player da tela Vídeo, a produção ao vivo
// enquanto o Claude trabalha, e o histórico logo abaixo (clicar numa produção pronta troca o vídeo do player).

type Producao = {
  id: number;
  criado: string | null;
  status: "rodando" | "ok" | "erro" | "cancelado";
  fase: string;
  titulo: string;
  formato: "vertical" | "horizontal";
  log: string[];
  aviso: string;
  ferramentas: number;
  negados: string[];
  entregue: string;
  custo_usd: number | null;
  turnos: number | null;
  segundos: number;
  versao?: number;
  revisao_de?: number;
  voz?: string;          // edge | elevenlabs | forja | estilo
  qc?: Qc;               // conferência automática do vídeo pronto (ffmpeg)
  qc_auto?: boolean;     // esta versão é a correção automática da conferência
  youtube?: Youtube;
  voz_final?: string;    // "pendente": versão de validação com o Edge, esperando a voz final do ElevenLabs
};

const rotuloVersao = (p: Producao) => ((p.versao ?? 1) > 1 ? `v${p.versao} · ` : "");

const relogio = (seg: number) => {
  const h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60), s = Math.floor(seg % 60);
  return `${h ? `${h}:` : ""}${String(m).padStart(h ? 2 : 1, "0")}:${String(s).padStart(2, "0")}`;
};
const dataCurta = (iso: string | null) => {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  const hoje = new Date();
  const dia = d.toDateString() === hoje.toDateString() ? "hoje" : d.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" });
  return `${dia}, ${d.toTimeString().slice(0, 5)}`;
};
const STATUS: Record<Producao["status"], { rotulo: string; cor: string }> = {
  rodando: { rotulo: "produzindo", cor: "bg-info" },
  ok: { rotulo: "pronto", cor: "bg-ok" },
  erro: { rotulo: "não terminou", cor: "bg-err" },
  cancelado: { rotulo: "cancelada", cor: "bg-faint" },
};

export default function ConteudoProducao(props: { conv: number; carimbo?: string; onError: (msg: string) => void }) {
  const [lista, setLista] = useState<Producao[]>([]);
  const [escolhida, setEscolhida] = useState<number | null>(null);
  const [revisando, setRevisando] = useState<Producao | null>(null);

  const carregar = useCallback(async () => {
    try {
      setLista(await api.get<Producao[]>(`/conteudo/especificacoes/${props.conv}/producao`));
    } catch (e: any) {
      props.onError(e.message);
    }
  }, [props.conv]);

  useEffect(() => { carregar(); setEscolhida(null); }, [carregar]);
  useEffect(() => { carregar(); }, [props.carimbo]);
  const viva = lista.find((p) => p.status === "rodando");
  useEffect(() => {   // o log anda mais rápido que o carimbo: enquanto produz, pergunta a cada 3 s
    if (!viva) return;
    const t = setInterval(carregar, 3000);
    return () => clearInterval(t);
  }, [viva?.id, carregar]);

  const prontas = lista.filter((p) => p.status === "ok");
  const noPlayer = useMemo(() => prontas.find((p) => p.id === escolhida) ?? prontas[0] ?? null, [prontas, escolhida]);

  return (
    <section className="flex min-w-0 flex-col gap-4">
      <h2 className="font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint">Vídeo</h2>
      {viva && <AoVivo p={viva} onCancelar={() => api.post(`/conteudo/producao/${viva.id}/cancelar`, {}).then(carregar)} />}
      {noPlayer ? <Player p={noPlayer} onRevisar={viva ? undefined : () => setRevisando(noPlayer)}
                          onVozFinal={viva ? undefined : () => api.post(`/conteudo/producao/${noPlayer.id}/voz-final`, {}).then(carregar)
                            .catch((e: any) => props.onError(e.message))} /> : !viva && (
        <div className="mx-auto flex aspect-[9/16] w-[min(100%,300px)] flex-col items-center justify-center gap-2 rounded-[10px] border border-dashed border-line-strong px-8 text-center">
          <Film className="size-6 text-faint" />
          <p className="text-[13px] text-muted">O vídeo aparece aqui quando a primeira produção terminar.</p>
          <p className="text-[12px] text-faint">Escolha um roteiro e clique em “Produzir o escolhido”.</p>
        </div>
      )}
      {lista.length > 0 && (
        <div className="flex flex-col">
          <h3 className="mb-2 font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint">Produções <span className="ml-1 tracking-normal">{lista.length}</span></h3>
          <ul className="flex flex-col divide-y divide-line overflow-hidden rounded-[10px] border border-line bg-surface">
            {lista.map((p) => (
              <li key={p.id}>
                <button disabled={p.status !== "ok"} onClick={() => setEscolhida(p.id)}
                        className={`flex w-full items-start gap-2.5 px-3 py-2.5 text-left transition-colors enabled:hover:bg-raised ${
                          noPlayer?.id === p.id ? "bg-raised" : ""}`}>
                  <span className={`mt-1.5 size-2 shrink-0 rounded-full ${STATUS[p.status].cor} ${p.status === "rodando" ? "animate-pulse" : ""}`} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12.5px] text-fg">
                      {(p.versao ?? 1) > 1 && <span className="mr-1.5 font-mono text-[11px] text-accent-text">v{p.versao}</span>}
                      {p.titulo}
                    </span>
                    <span className="block text-[11.5px] text-faint">
                      {dataCurta(p.criado)} · {STATUS[p.status].rotulo} · {relogio(p.segundos)}
                    </span>
                    {p.aviso && <span className="mt-0.5 block text-[11.5px] text-warn">{p.aviso}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {revisando && (
        <ConteudoRevisao p={revisando} onError={props.onError} onFechar={() => setRevisando(null)}
                         onEnviado={() => { setRevisando(null); carregar(); }} />
      )}
    </section>
  );
}

function Player(props: { p: Producao; onRevisar?: () => void; onVozFinal?: () => void }) {
  const { p } = props;
  const [copiado, setCopiado] = useState(false);
  const [confirmando, setConfirmando] = useState(false);   // gasta créditos: dois cliques
  useEffect(() => setConfirmando(false), [p.id]);
  const player = useRef<VideoPlayerApi>(null);
  const vertical = p.formato !== "horizontal";
  return (
    <div className="flex flex-col gap-2.5">
      <VideoPlayer key={p.id} ref={player} src={`/api/conteudo/video/${p.id}`} fps={30} compacto audio
                   className={`rounded-[10px] border border-line bg-black ${vertical ? "mx-auto" : ""}`}
                   style={{ aspectRatio: vertical ? 9 / 16 : 16 / 9, width: vertical ? "min(100%, 300px)" : "100%" }} />
      <div className="flex flex-col gap-1">
        <span className="text-[13.5px] font-medium leading-snug text-fg">{rotuloVersao(p)}{p.titulo}</span>
        <span className="text-[11.5px] text-faint">
          {dataCurta(p.criado)} · feito em {relogio(p.segundos)}{p.turnos ? ` · ${p.turnos} turnos do Claude` : ""}
          {p.voz && p.voz !== "estilo" ? ` · voz: ${({ edge: "Edge", elevenlabs: "ElevenLabs", forja: "Forja" } as Record<string, string>)[p.voz] ?? p.voz}` : ""}
        </span>
        <button className="group flex items-center gap-1.5 self-start rounded-md py-0.5 text-[11.5px] text-muted hover:text-fg"
                title="Copiar o caminho do arquivo"
                onClick={() => { navigator.clipboard.writeText(p.entregue); setCopiado(true); setTimeout(() => setCopiado(false), 1400); }}>
          <Copy className="size-3.5" />
          <span className="max-w-[300px] truncate font-mono">{copiado ? "caminho copiado" : p.entregue}</span>
        </button>
        <Conferencia p={p} />
        <ParaYoutube id={p.id} />
        {p.voz_final !== "pendente" && <PublicarYoutube p={p} />}
        {p.voz_final === "pendente" && (
          <div className="mt-2 flex flex-col gap-1.5 rounded-[8px] border border-accent-line bg-accent-soft px-3 py-2.5">
            <span className="text-[12px] text-fg">Versão de validação com o Edge. Aprovou? Troque só a voz pela do ElevenLabs.</span>
            {props.onVozFinal && (
              <button onClick={() => { if (!confirmando) { setConfirmando(true); return; } setConfirmando(false); props.onVozFinal?.(); }}
                      className="inline-flex h-[30px] items-center justify-center gap-1.5 rounded-[7px] bg-accent px-3 text-[12.5px] font-medium text-accent-fg hover:brightness-110">
                {confirmando ? "Clique de novo: gasta créditos do ElevenLabs" : "Gerar voz final (ElevenLabs)"}
              </button>
            )}
          </div>
        )}
        {props.onRevisar && (
          <button onClick={() => { player.current?.pausar(); props.onRevisar?.(); }}   // o editor abre o mesmo vídeo: dois tocando juntos
                  className="mt-2 inline-flex h-[32px] items-center justify-center gap-1.5 rounded-[7px] border border-line-strong text-[12.5px] text-fg hover:border-focus hover:bg-raised">
            <Edit className="size-3.5" /> Pedir mudanças
          </button>
        )}
      </div>
    </div>
  );
}

type Qc = { ok: boolean; problemas: string[]; revisao?: number; aviso?: string; medidas?: { duracao: number; lufs: number | null } };

/** Resultado da conferência automática: o que a medição achou e se já tem uma versão corrigindo. */
function Conferencia(props: { p: Producao }) {
  const qc = props.p.qc;
  if (!qc) return null;
  if (qc.ok) {
    const m = qc.medidas;
    return (
      <span className="text-[11.5px] text-faint">
        ✓ conferido{m ? ` · ${relogio(m.duracao)}${m.lufs != null ? ` · ${m.lufs.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} LUFS` : ""}` : ""}
        {qc.aviso ? ` · ${qc.aviso}` : ""}
      </span>
    );
  }
  return (
    <div className="mt-1 flex flex-col gap-1 rounded-[8px] border border-warn/50 bg-warn/[0.06] px-3 py-2.5 text-[12px]">
      <span className="font-medium text-warn">A conferência automática achou {qc.problemas.length === 1 ? "um problema" : `${qc.problemas.length} problemas`}</span>
      <ul className="list-disc pl-4 text-fg-2">{qc.problemas.map((x) => <li key={x}>{x}</li>)}</ul>
      <span className="text-muted">
        {qc.revisao ? "O Claude já está fazendo a versão corrigida (aparece em Produções)."
          : props.p.qc_auto ? "Esta já era a correção automática: confira e peça mudanças se precisar."
          : qc.aviso ?? ""}
      </span>
    </div>
  );
}

type Youtube = { status: "enviando" | "ok" | "erro"; progresso?: number; url?: string; privacidade?: string; aviso?: string; erro?: string };
const PRIVACIDADE: Record<string, string> = { private: "privado", unlisted: "não listado", public: "público" };

/** Publicar no YouTube (conta conectada em Ajustes): sobe em segundo plano e mostra o andamento e o link. */
function PublicarYoutube(props: { p: Producao }) {
  const [yt, setYt] = useState<Youtube | undefined>(props.p.youtube);
  const [conectado, setConectado] = useState<boolean | null>(null);
  const [privacidade, setPrivacidade] = useState("private");
  const [erro, setErro] = useState("");
  useEffect(() => setYt(props.p.youtube), [props.p.id, props.p.youtube?.status]);
  useEffect(() => { api.get<{ conectado: boolean }>("/youtube").then((r) => setConectado(r.conectado)).catch(() => setConectado(false)); }, []);
  useEffect(() => {   // subindo: acompanha o andamento (o mesmo estado que o celular vê)
    if (yt?.status !== "enviando") return;
    const t = setInterval(() => api.get<Producao>(`/conteudo/producao/${props.p.id}`).then((r) => setYt(r.youtube)).catch(() => {}), 2000);
    return () => clearInterval(t);
  }, [yt?.status, props.p.id]);
  if (yt?.status === "ok") {
    return (
      <div className="mt-2 flex flex-col gap-1 rounded-[8px] border border-line bg-surface px-3 py-2.5 text-[12px]">
        <span className="text-fg">No YouTube ({PRIVACIDADE[yt.privacidade ?? ""] ?? yt.privacidade}):{" "}
          <a className="text-accent-text underline" href={yt.url} target="_blank" rel="noreferrer">{yt.url}</a></span>
        {yt.aviso && <span className="text-warn">{yt.aviso}</span>}
      </div>
    );
  }
  if (yt?.status === "enviando") {
    const pct = Math.round((yt.progresso ?? 0) * 100);
    return (
      <div className="mt-2 flex flex-col gap-1.5 rounded-[8px] border border-line bg-surface px-3 py-2.5 text-[12px] text-fg">
        Subindo para o YouTube… {pct}%
        <div className="h-1 overflow-hidden rounded-full bg-raised"><div className="h-full bg-accent" style={{ width: `${pct}%` }} /></div>
      </div>
    );
  }
  if (conectado === false) {
    return <span className="mt-2 text-[11.5px] text-faint">Para publicar daqui, conecte o YouTube em Conteúdo › Ajustes.</span>;
  }
  if (!conectado) return null;
  async function publicar() {
    setErro("");
    try {
      const r = await api.post<Producao>(`/conteudo/producao/${props.p.id}/youtube`, { privacidade });
      setYt(r.youtube);
    } catch (e: any) {
      setErro(e.message);
    }
  }
  return (
    <div className="mt-2 flex flex-col gap-1.5">
      {(yt?.status === "erro" || erro) && <span className="text-[11.5px] text-err">{erro || yt?.erro}</span>}
      <div className="flex items-stretch gap-1.5">
        <select className="h-[32px] cursor-pointer rounded-[7px] border border-line-strong bg-transparent px-2 text-[12.5px] text-fg"
                value={privacidade} onChange={(e) => setPrivacidade(e.target.value)} aria-label="Privacidade no YouTube">
          {Object.entries(PRIVACIDADE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <button onClick={publicar}
                className="inline-flex h-[32px] flex-1 items-center justify-center gap-1.5 rounded-[7px] bg-accent px-3 text-[12.5px] font-medium text-accent-fg hover:brightness-110">
          {yt?.status === "erro" ? "Tentar de novo no YouTube" : "Publicar no YouTube"}
        </button>
      </div>
    </div>
  );
}

/** Título e descrição prontos para colar no YouTube (com os créditos da mídia real que o Claude usou). */
function ParaYoutube(props: { id: number }) {
  const [pub, setPub] = useState<{ titulo: string; descricao: string; titulos?: string[] } | null>(null);
  const [aberto, setAberto] = useState(false);
  const [copiado, setCopiado] = useState("");
  useEffect(() => {
    setPub(null);
    api.get<{ titulo: string; descricao: string; titulos?: string[] }>(`/conteudo/producao/${props.id}/publicacao`).then(setPub).catch(() => setPub(null));
  }, [props.id]);
  if (!pub) return null;
  const copiar = (oque: "titulo" | "descricao") => {
    navigator.clipboard.writeText(pub[oque]);
    setCopiado(oque);
    setTimeout(() => setCopiado(""), 1400);
  };
  const botao = (oque: "titulo" | "descricao", rotulo: string) => (
    <button onClick={() => copiar(oque)}
            className="inline-flex h-[26px] items-center gap-1 rounded-[6px] border border-line px-2 text-[11.5px] text-muted hover:border-line-strong hover:text-fg">
      {copiado === oque ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />} {copiado === oque ? "Copiado" : rotulo}
    </button>
  );
  return (
    <div className="mt-2 flex flex-col gap-2 rounded-[10px] border border-line bg-surface p-3">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint">Para o YouTube</span>
        <span className="ml-auto flex gap-1.5">{botao("titulo", "Título")}{botao("descricao", "Descrição")}</span>
      </div>
      <p className="text-[13px] font-medium leading-snug text-fg">{pub.titulo}</p>
      {(pub.titulos?.length ?? 0) > 0 && (   // para o teste A/B de título do YouTube (ou trocar depois de uns dias)
        <div className="flex flex-col gap-0.5 text-[12px] text-muted">
          <span className="text-[11px] text-faint">Outras opções (toque para copiar):</span>
          {pub.titulos!.map((t) => (
            <button key={t} className="text-left hover:text-fg" onClick={() => navigator.clipboard.writeText(t)}>· {t}</button>
          ))}
        </div>
      )}
      <button className="text-left" onClick={() => setAberto(!aberto)} title={aberto ? "Recolher" : "Ver a descrição inteira"}>
        <p className={`whitespace-pre-line text-[12px] leading-relaxed text-muted ${aberto ? "" : "line-clamp-3"}`}>{pub.descricao}</p>
      </button>
    </div>
  );
}

function AoVivo(props: { p: Producao; onCancelar: () => void }) {
  const { p } = props;
  const fim = useRef<HTMLDivElement>(null);
  useEffect(() => { fim.current?.scrollIntoView({ block: "nearest" }); }, [p.log.length]);
  return (
    <div className="flex flex-col gap-2.5 rounded-[10px] border border-info/40 bg-info/5 p-3.5">
      <div className="flex items-center gap-2">
        <span className="size-2 animate-pulse rounded-full bg-info" />
        <span className="text-[12.5px] font-medium text-info">
          {p.fase === "preparando" ? "Preparando"
            : p.fase?.startsWith("aguardando") ? "Aguardando uma sessão do Claude (MCP)"
            : (p.versao ?? 1) > 1 ? `Claude fazendo a versão ${p.versao}` : "Claude produzindo"}
        </span>
        <span className="ml-auto font-mono text-[12px] text-muted">{relogio(p.segundos)}</span>
      </div>
      <span className="text-[13px] leading-snug text-fg">{p.titulo}</span>
      <div className="max-h-40 overflow-y-auto rounded-lg bg-code/70 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-muted">
        {p.log.slice(-40).map((l, i) => <div key={i} className="truncate">{l}</div>)}
        {p.log.length === 0 && <div>Abrindo o Claude Code…</div>}
        <div ref={fim} />
      </div>
      <div className="flex items-center gap-3 text-[11.5px] text-faint">
        <span>{p.ferramentas} ações</span>
        {p.negados.length > 0 && <span className="text-warn" title={p.negados.join("\n")}>{p.negados.length} comando(s) negado(s)</span>}
        <button className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-muted hover:bg-raised hover:text-fg"
                onClick={props.onCancelar}>
          <Square className="size-3" /> Cancelar
        </button>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Copy, Edit, Film, Square } from "./icons";
import ConteudoRevisao from "./ConteudoRevisao";
import { VideoPlayer } from "./VideoPlayer";

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
      {noPlayer ? <Player p={noPlayer} onRevisar={viva ? undefined : () => setRevisando(noPlayer)} /> : !viva && (
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
                    {p.aviso && p.status !== "ok" && <span className="mt-0.5 block text-[11.5px] text-warn">{p.aviso}</span>}
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

function Player(props: { p: Producao; onRevisar?: () => void }) {
  const { p } = props;
  const [copiado, setCopiado] = useState(false);
  const vertical = p.formato !== "horizontal";
  return (
    <div className="flex flex-col gap-2.5">
      <VideoPlayer key={p.id} src={`/api/conteudo/video/${p.id}`} fps={30} compacto audio
                   className={`rounded-[10px] border border-line bg-black ${vertical ? "mx-auto" : ""}`}
                   style={{ aspectRatio: vertical ? 9 / 16 : 16 / 9, width: vertical ? "min(100%, 300px)" : "100%" }} />
      <div className="flex flex-col gap-1">
        <span className="text-[13.5px] font-medium leading-snug text-fg">{rotuloVersao(p)}{p.titulo}</span>
        <span className="text-[11.5px] text-faint">
          {dataCurta(p.criado)} · feito em {relogio(p.segundos)}{p.turnos ? ` · ${p.turnos} turnos do Claude` : ""}
        </span>
        <button className="group flex items-center gap-1.5 self-start rounded-md py-0.5 text-[11.5px] text-muted hover:text-fg"
                title="Copiar o caminho do arquivo"
                onClick={() => { navigator.clipboard.writeText(p.entregue); setCopiado(true); setTimeout(() => setCopiado(false), 1400); }}>
          <Copy className="size-3.5" />
          <span className="max-w-[300px] truncate font-mono">{copiado ? "caminho copiado" : p.entregue}</span>
        </button>
        {props.onRevisar && (
          <button onClick={props.onRevisar}
                  className="mt-2 inline-flex h-[32px] items-center justify-center gap-1.5 rounded-[7px] border border-line-strong text-[12.5px] text-fg hover:border-focus hover:bg-raised">
            <Edit className="size-3.5" /> Pedir mudanças
          </button>
        )}
      </div>
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
          {p.fase === "preparando" ? "Preparando" : (p.versao ?? 1) > 1 ? `Claude fazendo a versão ${p.versao}` : "Claude produzindo"}
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

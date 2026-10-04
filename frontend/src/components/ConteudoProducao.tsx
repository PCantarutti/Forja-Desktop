import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Copy, Film, Play, Square } from "./icons";

const card = "rounded-xl border border-line bg-surface p-3.5";
const btnPrimary = "inline-flex items-center gap-1.5 rounded-[9px] border border-accent bg-accent px-3 py-1.5 font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";
const btnPeq = "inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[12px] text-fg hover:border-focus hover:bg-raised disabled:opacity-40";

type Producao = {
  id: number;
  criado: string | null;
  status: "rodando" | "ok" | "erro" | "cancelado";
  fase: string;
  titulo: string;
  estilo: string;
  formato: "vertical" | "horizontal";
  log: string[];
  aviso: string;
  ferramentas: number;
  negados: string[];
  entregue: string;
  custo_usd: number | null;
  turnos: number | null;
  segundos: number;
};

const relogio = (seg: number) => {
  const h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60), s = Math.floor(seg % 60);
  return `${h ? `${h}:` : ""}${String(m).padStart(h ? 2 : 1, "0")}:${String(s).padStart(2, "0")}`;
};

export default function ConteudoProducao(props: { conv: number; carimbo?: string; onError: (msg: string) => void }) {
  const [lista, setLista] = useState<Producao[]>([]);
  const [disparando, setDisparando] = useState(false);

  const carregar = useCallback(async () => {
    try {
      setLista(await api.get<Producao[]>(`/conteudo/especificacoes/${props.conv}/producao`));
    } catch (e: any) {
      props.onError(e.message);
    }
  }, [props.conv]);

  useEffect(() => { carregar(); }, [carregar, props.carimbo]);
  const rodando = lista.some((p) => p.status === "rodando");
  useEffect(() => {   // o log anda mais rápido que o carimbo: enquanto roda, pergunta a cada 3 s
    if (!rodando) return;
    const t = setInterval(carregar, 3000);
    return () => clearInterval(t);
  }, [rodando, carregar]);

  async function produzirAprovado() {
    setDisparando(true);
    try {
      await api.post(`/conteudo/especificacoes/${props.conv}/producao`, {});
      await carregar();
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setDisparando(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div className="flex items-center gap-3">
        <button className={btnPrimary} disabled={disparando || rodando} onClick={produzirAprovado}>
          <Play className="size-4" /> Produzir o roteiro aprovado
        </button>
        <span className="text-[12px] text-muted">
          O Claude Code monta e renderiza no projeto de vídeo; o .mp4 e um .txt com título e descrição vão para a pasta de entrega.
        </span>
      </div>
      {lista.length === 0 && (
        <div className={`${card} text-[13px] text-muted`}>
          Nenhum vídeo produzido ainda. Aprove um roteiro na aba Roteiros e produza por aqui, ou use "Produzir agora" no próprio roteiro.
        </div>
      )}
      {lista.map((p) => (
        <ProducaoCard key={p.id} p={p} onCancelar={() => api.post(`/conteudo/producao/${p.id}/cancelar`, {}).then(carregar)} />
      ))}
    </div>
  );
}

function ProducaoCard(props: { p: Producao; onCancelar: () => void }) {
  const p = props.p;
  const [verLog, setVerLog] = useState(p.status === "rodando");
  const fim = useRef<HTMLDivElement>(null);
  useEffect(() => { if (verLog) fim.current?.scrollIntoView({ block: "nearest" }); }, [p.log.length, verLog]);
  const quando = p.criado ? new Date(p.criado + (p.criado.endsWith("Z") ? "" : "Z")).toLocaleString().slice(0, 17) : "";
  const cor = p.status === "ok" ? "text-emerald-300" : p.status === "erro" ? "text-red-300" : p.status === "rodando" ? "text-sky-300" : "text-muted";
  const rotulo = { rodando: p.fase === "preparando" ? "Preparando" : "Claude trabalhando", ok: "Vídeo pronto", erro: "Não terminou", cancelado: "Cancelada" }[p.status];

  return (
    <div className={`${card} flex flex-col gap-2.5`}>
      <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
        <Film className="size-4 text-muted" />
        <span className="font-medium text-fg">{p.titulo}</span>
        <span className="text-muted">· {quando} · {p.estilo} · {p.formato === "horizontal" ? "16:9" : "9:16"}</span>
        <span className={`inline-flex items-center gap-1.5 ${cor}`}>
          {p.status === "rodando" && <span className="size-2 animate-pulse rounded-full bg-sky-400" />}
          {rotulo} · {relogio(p.segundos)}
        </span>
        {p.status === "rodando" && <button className={`${btnPeq} ml-auto`} onClick={props.onCancelar}><Square className="size-3" /> Cancelar</button>}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-faint">
        <span>{p.ferramentas} ações</span>
        {p.turnos != null && <span>{p.turnos} turnos</span>}
        {p.custo_usd != null && <span title="Custo informado pelo Claude Code (no plano Pro/Max conta no limite de uso)">≈ US$ {p.custo_usd.toFixed(2)}</span>}
        {p.negados.length > 0 && <span className="text-amber-300" title={p.negados.join("\n")}>{p.negados.length} comando(s) negado(s) pela lista</span>}
      </div>
      {p.aviso && <div className="text-[12.5px] text-amber-300">{p.aviso}</div>}
      {p.entregue && (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1.5 text-[12.5px]">
          <span className="text-emerald-300">Entregue em</span>
          <span className="truncate font-mono text-fg">{p.entregue}</span>
          <button className={`${btnPeq} ml-auto`} onClick={() => navigator.clipboard.writeText(p.entregue)}><Copy className="size-3" /> Copiar caminho</button>
        </div>
      )}
      {p.log.length > 0 && (
        <button className="self-start text-[12px] text-muted hover:text-fg" onClick={() => setVerLog(!verLog)}>
          {verLog ? "Esconder" : "Ver"} o que o Claude fez ({p.log.length})
        </button>
      )}
      {verLog && (
        <div className="max-h-72 overflow-y-auto rounded-lg border border-line bg-raised/50 p-2 font-mono text-[11.5px] leading-relaxed text-muted">
          {p.log.map((l, i) => <div key={i} className="whitespace-pre-wrap break-words">{l}</div>)}
          <div ref={fim} />
        </div>
      )}
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import "katex/dist/katex.min.css";
import { api, streamSSE } from "../../api";
import ModelPicker from "../ModelPicker";
import { Bubble, X } from "../icons";
import Fontes, { comEdital } from "./Fontes";
import Material from "./Material";
import Provas from "./Provas";
import Revisao from "./Revisao";
import Desempenho from "./Desempenho";
import Tutor from "./Tutor";
import { JanelaTrecho, btnLeve, relogio } from "./ui";
import type { Estudo, Run } from "./tipos";

type Aba = "fontes" | "material" | "provas" | "revisao" | "desempenho";
const KEY_MODELO = "forja.estudo.modelo";

export type Motor = { provider: string; model: string };

/** O que cada aba recebe: o estudo, quem chama o modelo e como disparar/acompanhar uma tarefa. */
export type Ctx = {
  conv: number;
  estudo: Estudo;
  motor: Motor;
  rodando: boolean;
  disparar: (path: string, body?: object) => Promise<void>;
  recarregar: () => Promise<void>;
  abrirTrecho: (id: string, destaque?: string) => void;
  onError: (e: string) => void;
};

const NOMES: Record<Run["tipo"], string> = {
  programa: "Montando o programa", pesquisa: "Pesquisando as fontes", material: "Escrevendo o material",
  prova: "Gerando a prova", correcao: "Corrigindo", flashcards: "Fazendo flashcards",
  simulado: "Lendo a prova real", busca: "Buscando provas reais",
};

export default function EstudoView(props: {
  conv: number | null;
  carimbo?: string;
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
}) {
  const [estudo, setEstudo] = useState<Estudo | null>(null);
  const [aba, setAba] = useState<Aba>("fontes");
  const [run, setRun] = useState<Run | null>(null);
  const [trecho, setTrecho] = useState<{ id: string; destaque?: string } | null>(null);
  const [tutor, setTutor] = useState(false);
  const [motor, setMotor] = useState<Motor>(() => {
    try { return JSON.parse(localStorage.getItem(KEY_MODELO) ?? "null") ?? { provider: props.provider, model: props.model }; }
    catch { return { provider: props.provider, model: props.model }; }
  });
  const seguindo = useRef<number | null>(null);
  const aoErro = useRef(props.onError);
  aoErro.current = props.onError;

  const recarregar = useCallback(async () => {
    if (props.conv == null) return setEstudo(null);
    try {
      const e = await api.get<Estudo>(`/estudo/${props.conv}`);
      setEstudo(e);
      // só adota a do servidor se estiver viva (outro aparelho disparou); a terminada fica até o X
      setRun((r) => (r?.status === "rodando" ? r : e.run?.status === "rodando" ? e.run : r));
    } catch (e: any) {
      aoErro.current(e.message);
    }
  }, [props.conv]);

  // Acompanha a tarefa viva pelo SSE (iniciada aqui ou em outro aparelho) e recarrega quando ela acaba.
  const seguir = useCallback(async (conv: number) => {
    if (seguindo.current === conv) return;
    seguindo.current = conv;
    try {
      await streamSSE(`/estudo/${conv}/stream`, { method: "GET" }, (r: Run | null) => r && setRun(r));
    } catch { /* caiu a conexão: o carimbo do /activity traz de volta */ }
    seguindo.current = null;
    await recarregar();
    props.onConversationChanged();
  }, [recarregar]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setEstudo(null); setRun(null); recarregar(); }, [props.conv]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (props.carimbo) recarregar(); }, [props.carimbo]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (estudo?.run?.status === "rodando" && props.conv != null) seguir(props.conv); }, [estudo?.run?.status]);   // eslint-disable-line react-hooks/exhaustive-deps

  function escolher(provider: string, model: string) {
    const m = { provider, model };
    setMotor(m);
    localStorage.setItem(KEY_MODELO, JSON.stringify(m));
  }

  async function disparar(path: string, body: object = {}) {
    const conv = props.conv ?? (await props.ensureConversation());
    try {
      const r = await api.post<Run>(`/estudo/${conv}${path}`, { ...body, ...motor });
      setRun(r);
      seguir(conv);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  const rodando = run?.status === "rodando";
  const ctx: Ctx | null = props.conv != null && estudo ? {
    conv: props.conv, estudo, motor, rodando, disparar, recarregar,
    abrirTrecho: (id, destaque) => setTrecho({ id, destaque }), onError: props.onError,
  } : null;
  const abas: [Aba, string, number][] = estudo ? [
    ["fontes", "Fontes", estudo.fontes.filter((f) => f.status === "util").length],
    ["material", "Material", Object.keys(estudo.material).length],
    ["provas", "Provas", estudo.provas.length],
    ["revisao", "Revisão", estudo.caderno.length + estudo.flashcards.length],
    ["desempenho", "Desempenho", 0],
  ] : [];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-6 pt-3">
        <h1 className="mr-2 truncate pb-3 text-[17px] font-semibold text-fg">{estudo?.titulo && estudo.titulo !== "Nova conversa" ? estudo.titulo : "Estudos"}</h1>
        <nav className="flex gap-5 text-[13.5px]">
          {abas.map(([id, nome, n]) => (
            <button key={id} onClick={() => setAba(id)}
                    className={`-mb-px border-b-2 pb-3 ${aba === id ? "border-accent font-medium text-fg" : "border-transparent text-muted hover:text-fg"}`}>
              {nome}{n ? <span className="ml-1.5 rounded bg-raised px-1.5 font-mono text-[10.5px] text-muted">{n}</span> : null}
            </button>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2 pb-2.5">
          <div className="w-56"><ModelPicker provider={motor.provider} model={motor.model} onChange={escolher} /></div>
          {ctx && (
            <button className={btnLeve} onClick={() => setTutor((t) => !t)} aria-pressed={tutor}>
              <Bubble className="size-3.5" /> Dúvidas
            </button>
          )}
        </div>
      </header>

      {run && (run.status === "rodando" || run.erro || run.aviso) && (
        <BarraRun run={run} onCancelar={() => props.conv != null && api.post(`/estudo/${props.conv}/cancelar`)}
                  onFechar={() => setRun(null)} />
      )}

      <div className="flex min-h-0 flex-1">
        <main className="min-w-0 flex-1 overflow-y-auto">
          {!ctx ? (
            <Fontes ctx={null} novo={async (body) => {
              const conv = props.conv ?? (await props.ensureConversation());
              try {
                setRun(await api.post<Run>(`/estudo/${conv}/programa`, { ...(await comEdital(conv, body)), ...motor }));
                seguir(conv);
              } catch (e: any) { props.onError(e.message); }
            }} carregando={props.conv != null && !estudo} />
          ) : aba === "fontes" ? <Fontes ctx={ctx} />
            : aba === "material" ? <Material ctx={ctx} />
            : aba === "provas" ? <Provas ctx={ctx} />
            : aba === "revisao" ? <Revisao ctx={ctx} />
            : <Desempenho ctx={ctx} irParaProvas={() => setAba("provas")} />}
        </main>
        {ctx && tutor && <Tutor ctx={ctx} onClose={() => setTutor(false)} />}
      </div>

      {trecho && props.conv != null && (
        <JanelaTrecho conv={props.conv} id={trecho.id} destaque={trecho.destaque} onClose={() => setTrecho(null)} />
      )}
    </div>
  );
}

function BarraRun(props: { run: Run; onCancelar: () => void; onFechar: () => void }) {
  const r = props.run;
  const vivo = r.status === "rodando";
  const tps = r.stats.gerando > 0.5 ? Math.round(r.stats.tokens / r.stats.gerando) : 0;
  return (
    <div className={`border-b border-line px-6 py-2.5 text-[13px] ${r.erro ? "bg-err/10" : "bg-surface"}`}>
      <div className="flex items-center gap-3">
        {vivo && <span className="size-2 animate-pulse rounded-full bg-accent" />}
        <span className="font-medium text-fg">{NOMES[r.tipo]}{vivo ? "" : r.status === "cancelado" ? " · parado" : r.erro ? " · erro" : " · pronto"}</span>
        {vivo && <span className="truncate text-muted">{r.fase}</span>}
        {r.total > 0 && <span className="font-mono text-[12px] text-muted">{r.feitos}/{r.total}</span>}
        <span className="ml-auto font-mono text-[11.5px] text-faint">
          {[r.stats.tokens ? `${r.stats.tokens.toLocaleString("pt-BR")} tokens` : "", tps ? `${tps} tok/s` : "",
            relogio(r.stats.segundos)].filter(Boolean).join(" · ")}
        </span>
        {vivo ? <button className={btnLeve} onClick={props.onCancelar}>Parar</button>
              : <button className={btnLeve} onClick={props.onFechar} aria-label="Fechar"><X className="size-3" /></button>}
      </div>
      {r.total > 0 && vivo && (
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-raised">
          <div className="h-full bg-accent transition-all" style={{ width: `${Math.round((100 * r.feitos) / r.total)}%` }} />
        </div>
      )}
      {(r.erro || r.aviso) && <div className={`mt-1.5 ${r.erro ? "text-err" : "text-warn"}`}>{r.erro || r.aviso}</div>}
      {vivo && r.log.length > 0 && (
        <div className="mt-1.5 max-h-24 overflow-y-auto font-mono text-[11.5px] text-faint">
          {r.log.slice(-6).map((l, i) => <div key={i} className="truncate">{l}</div>)}
        </div>
      )}
    </div>
  );
}

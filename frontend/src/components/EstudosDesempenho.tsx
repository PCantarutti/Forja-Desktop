import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { EstudosDesempenho, EstudosPlano, EstudosTarefa } from "../types";
import { ArrowRight, Check, Clock, Trash } from "./icons";
import type { ProvaPendente } from "./EstudosProva";
import { btn, btnPrimary, card, nota, rotulo } from "./estudosUi";

const W = 640, H = 150, PX = 28, PY = 14;
const diaCurto = (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
const corPct = (p: number) => (p >= 0.7 ? "bg-emerald-400/70" : p >= 0.4 ? "bg-amber-300/70" : "bg-red-400/70");

/** As notas na ordem das entregas; treino em ponto vazado. SVG à mão: uma linha não pede biblioteca. */
function Grafico({ entregas }: { entregas: EstudosDesempenho["entregas"] }) {
  const n = entregas.length;
  const x = (i: number) => (n === 1 ? W / 2 : PX + (i * (W - 2 * PX)) / (n - 1));
  const y = (v: number) => PY + (1 - v / 10) * (H - 2 * PY);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Notas das entregas">
      {[0, 5, 7, 10].map((v) => (
        <g key={v}>
          <line x1={PX} x2={W - PX} y1={y(v)} y2={y(v)} className={v === 7 ? "stroke-ok/40" : "stroke-line"} strokeDasharray={v === 7 ? "4 4" : undefined} />
          <text x={PX - 8} y={y(v) + 3} textAnchor="end" className="fill-faint font-mono text-[9px]">{v}</text>
        </g>
      ))}
      {n > 1 && <polyline fill="none" className="stroke-accent" strokeWidth={2}
                          points={entregas.map((e, i) => `${x(i)},${y(e.nota)}`).join(" ")} />}
      {entregas.map((e, i) => (
        <circle key={e.message_id} cx={x(i)} cy={y(e.nota)} r={4.5} strokeWidth={2}
                className={e.modo === "treino" ? "fill-bg stroke-accent" : "fill-accent stroke-accent"}>
          <title>{`${e.titulo}${e.modo === "treino" ? " (treino)" : ""}: ${nota(e.nota)} · ${new Date(e.criado).toLocaleDateString("pt-BR")}`}</title>
        </circle>
      ))}
    </svg>
  );
}

function Cronograma({ conv, plano, lembrete, onMudou, onProva, onIr, onError }: {
  conv: number; plano: EstudosPlano | null; lembrete: boolean; onMudou: () => void; onProva: (p: ProvaPendente) => void;
  onIr: (aba: "resumo" | "revisao") => void; onError: (e: string) => void;
}) {
  // AAAA-MM-DD no fuso daqui (o toISOString é UTC: depois das 21h já seria amanhã)
  const [[hoje, amanha]] = useState(() => [new Date(), new Date(Date.now() + 86_400_000)].map((x) => x.toLocaleDateString("sv-SE")));
  const [data, setData] = useState(plano?.data ?? "");
  const [minutos, setMinutos] = useState(plano?.minutos ?? 60);
  const [editando, setEditando] = useState(!plano);
  const [todos, setTodos] = useState(false);

  async function montar() {
    try {
      await api.post(`/estudos/${conv}/cronograma`, { data, minutos });
      setEditando(false);
      onMudou();
    } catch (e: any) {
      onError(e.message);
    }
  }

  async function marcar(t: EstudosTarefa) {
    try {
      await api.post(`/estudos/${conv}/cronograma/marcar`, { tarefa_id: t.id, feito: !t.feito });
      onMudou();
    } catch (e: any) {
      onError(e.message);
    }
  }

  async function apagar() {
    await api.del(`/estudos/${conv}/cronograma`).catch((e) => onError(e.message));
    setEditando(true);
    onMudou();
  }

  const dias = plano?.dias.filter((d) => todos || d.dia >= hoje).slice(0, todos ? undefined : 7) ?? [];
  const feitas = plano?.dias.flatMap((d) => d.tarefas).filter((t) => t.feito).length ?? 0;
  const total = plano?.dias.flatMap((d) => d.tarefas).length ?? 0;

  return (
    <div className={`${card} flex flex-col gap-3 text-xs`}>
      <div className="flex flex-wrap items-center gap-2">
        <Clock className="size-4 text-faint" />
        <span className="text-sm text-fg">Cronograma</span>
        {plano && !editando && (
          <span className="text-muted">até a prova em {diaCurto(plano.data)} · {plano.minutos} min por dia · {feitas} de {total} tarefas feitas</span>
        )}
        {plano && !editando && (
          <div className="ml-auto flex gap-2">
            <button className={btn} onClick={() => setEditando(true)}>Refazer</button>
            <button className="rounded-md p-1.5 text-faint hover:bg-raised hover:text-fg" title="Apagar o cronograma" onClick={apagar}><Trash className="size-3.5" /></button>
          </div>
        )}
      </div>

      {editando && (
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-muted">Dia da prova
            <input type="date" min={amanha} value={data} onChange={(e) => setData(e.target.value)}
                   className="rounded-md border border-line bg-raised px-2 py-1.5 text-fg focus:border-focus focus:outline-none" />
          </label>
          <label className="flex flex-col gap-1 text-muted">Minutos por dia
            <input type="number" min={15} max={600} step={15} value={minutos} onChange={(e) => setMinutos(Number(e.target.value) || 60)}
                   className="w-24 rounded-md border border-line bg-raised px-2 py-1.5 text-right font-mono text-fg focus:border-focus focus:outline-none" />
          </label>
          <button className={btnPrimary} disabled={!data} onClick={montar}>{plano ? "Refazer o cronograma" : "Montar o cronograma"}</button>
          {plano && <button className={btn} onClick={() => setEditando(false)}>Voltar</button>}
          <p className="w-full text-faint">
            Um tópico por dia (os seus pontos fracos voltam mais vezes), revisão dos cartões e erros todo dia e um simulado por
            semana e na véspera.{lembrete && " O celular avisa a tarefa do dia às 8h."}
          </p>
        </div>
      )}

      {plano && !editando && (
        <div className="flex flex-col gap-1.5">
          {dias.map((d) => (
            <div key={d.dia} className={`flex flex-wrap items-start gap-x-3 gap-y-1 rounded-lg border px-3 py-2 ${
              d.dia === hoje ? "border-accent-line bg-accent-soft/40" : d.dia < hoje ? "border-line opacity-60" : "border-line"}`}>
              <span className={`w-24 shrink-0 font-mono ${d.dia === hoje ? "text-accent-text" : "text-faint"}`}>{d.dia === hoje ? "hoje" : diaCurto(d.dia)}</span>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                {d.tarefas.map((t) => (
                  <div key={t.id} className="flex items-center gap-2">
                    <button role="checkbox" aria-checked={t.feito} onClick={() => marcar(t)}
                            className={`grid size-4 shrink-0 place-items-center rounded border ${t.feito ? "border-ok bg-ok/20 text-ok" : "border-line-strong hover:border-focus"}`}>
                      {t.feito && <Check className="size-3" />}
                    </button>
                    <span className={`min-w-0 flex-1 ${t.feito ? "text-faint line-through" : "text-fg"}`}>{t.texto}</span>
                    <span className="font-mono text-faint">{t.minutos} min</span>
                    {d.dia === hoje && !t.feito && (
                      <button className="text-accent-text hover:underline"
                              onClick={() => (t.tipo === "simulado" ? onProva({ topicos: [], instrucoes: "Simulado: todos os tópicos, no estilo da prova." })
                                : onIr(t.tipo === "revisar" ? "revisao" : "resumo"))}>
                        {t.tipo === "simulado" ? "gerar" : t.tipo === "revisar" ? "revisar" : "ler"} <ArrowRight className="inline size-3" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
          {plano.dias.length > dias.length && !todos && (
            <button className="self-start text-faint hover:text-fg" onClick={() => setTodos(true)}>ver os {plano.dias.length} dias</button>
          )}
        </div>
      )}
    </div>
  );
}

export default function Desempenho(props: {
  conv: number;
  carimbo?: string;
  abas: React.ReactNode;
  onProva: (p: ProvaPendente) => void;
  onIr: (aba: "resumo" | "revisao") => void;
  onError: (e: string) => void;
}) {
  const [d, setD] = useState<EstudosDesempenho | null>(null);
  const carregar = useCallback(() => {
    api.get<EstudosDesempenho>(`/estudos/${props.conv}/desempenho`).then(setD).catch((e) => props.onError(e.message));
  }, [props.conv]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { carregar(); }, [carregar, props.carimbo]);

  const provas = d?.entregas.filter((e) => e.modo === "prova") ?? [];
  const media = provas.length ? provas.reduce((s, e) => s + e.nota, 0) / provas.length : 0;
  const testados = d?.topicos.filter((t) => t.pct !== null) ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-3">
          {props.abas}
          {d && (
            <>
              <div className={`${card} flex flex-col gap-3`}>
                <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
                  <div>
                    <p className={rotulo}>última nota</p>
                    <p className="mt-1 font-mono text-3xl font-semibold text-fg">{d.entregas.length ? nota(d.entregas.at(-1)!.nota) : "—"}</p>
                  </div>
                  <div className="text-xs text-muted">
                    <p>{d.entregas.length} entrega(s) · média das provas {provas.length ? nota(media) : "—"}</p>
                    <p className="mt-0.5">ponto cheio = prova · ponto vazado = treino · tracejado = 7</p>
                  </div>
                </div>
                {d.entregas.length ? <Grafico entregas={d.entregas} />
                  : <p className="text-xs text-faint">Faça uma prova (ou um treino) na aba Provas para ver a evolução aqui.</p>}
              </div>

              {!!d.topicos.length && (
                <div className={`${card} flex flex-col gap-1.5 text-xs`}>
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className="text-sm text-fg">Acerto por tópico</span>
                    <span className="text-faint">somando todas as entregas; o traço é a última</span>
                    {!!d.fracos.length && (
                      <button className={`${btnPrimary} ml-auto`} onClick={() => props.onProva({ topicos: d.fracos,
                        instrucoes: `Prova dos pontos fracos: foque no que o aluno mais erra em ${d.fracos.join(", ")}.` })}>
                        Prova dos pontos fracos
                      </button>
                    )}
                  </div>
                  {d.topicos.map((t) => (
                    <div key={t.topico} className="flex items-center gap-3">
                      <span className={`w-52 shrink-0 truncate ${d.fracos.includes(t.topico) ? "text-amber-300" : "text-muted"}`} title={t.topico}>{t.topico}</span>
                      <div className="relative h-1.5 min-w-0 flex-1 rounded-full bg-raised">
                        {t.pct !== null && <div className={`h-full rounded-full ${corPct(t.pct)}`} style={{ width: `${Math.round(t.pct * 100)}%` }} />}
                        {t.ultima !== null && <div className="absolute -top-1 h-3.5 w-0.5 bg-fg/70" style={{ left: `${Math.round(t.ultima * 100)}%` }} />}
                      </div>
                      <span className="w-20 shrink-0 text-right font-mono text-faint">{t.pct === null ? "sem questão" : `${Math.round(t.pct * 100)}%`}</span>
                    </div>
                  ))}
                  {!testados.length && <p className="text-faint">Nenhum tópico com questão feita ainda.</p>}
                </div>
              )}

              <div className={`${card} flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-muted`}>
                <span className="text-sm text-fg">Revisão</span>
                <span><b className="text-fg">{d.revisao.vencem}</b> para hoje</span>
                <span>{d.revisao.erros} no caderno de erros</span>
                <span>{d.revisao.cartoes} cartões</span>
                <span>{d.revisao.dominados} dominados</span>
                <button className={`${btn} ml-auto`} onClick={() => props.onIr("revisao")}>Abrir a revisão <ArrowRight className="size-3.5" /></button>
              </div>

              <Cronograma key={d.plano?.criado ?? "novo"} conv={props.conv} plano={d.plano} lembrete={d.lembrete} onMudou={carregar}
                          onProva={props.onProva} onIr={props.onIr} onError={props.onError} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

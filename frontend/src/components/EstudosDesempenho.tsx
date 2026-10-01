import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { EstudosDesempenho, EstudosPlano, EstudosProjeto, EstudosTarefa } from "../types";
import { ArrowRight, Check, Clock, Trash } from "./icons";
import type { ProvaPendente } from "./EstudosProva";
import { btn, btnPrimary, nota, rotulo } from "./estudosUi";

const W = 640, H = 150, PX = 28, PY = 14;
const diaCurto = (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
const corPct = (p: number) => (p >= 0.7 ? "bg-ok/70" : p >= 0.4 ? "bg-warn/70" : "bg-err/70");
const cardD = "rounded-xl border border-line bg-surface px-[18px] py-4";

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
          <text x={PX - 8} y={y(v) + 4} textAnchor="end" className="fill-faint font-mono text-[12px]">{v}</text>
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

type Feitos = NonNullable<EstudosProjeto["piloto"]>["feitos"];
type Ir = (aba: "resumo" | "revisao" | "provas", topico?: string, mid?: number) => void;   // topico: o "ler" do cronograma

function Cronograma({ conv, plano, lembrete, feitos, onMudou, onProva, onIr, onError }: {
  conv: number; plano: EstudosPlano | null; lembrete: boolean; feitos: Feitos; onMudou: () => void; onProva: (p: ProvaPendente) => void;
  onIr: Ir; onError: (e: string) => void;
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
    <div className={`${cardD} flex flex-col gap-3 text-xs`}>
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

      {plano && !editando && total > 0 && (
        <div className="h-1 overflow-hidden rounded-full bg-raised" aria-hidden>
          <div className="h-full rounded-full bg-ok transition-[width] duration-[250ms]" style={{ width: `${(100 * feitas) / total}%` }} />
        </div>
      )}

      {plano && !editando && (
        <div className="flex flex-col gap-1.5">
          {dias.map((d) => (
            <div key={d.dia} className={`flex flex-wrap items-start gap-x-3 gap-y-1 rounded-[10px] border px-3 py-[9px] ${
              d.dia === hoje ? "border-accent-line bg-accent-soft/50" : d.dia < hoje ? "border-line opacity-60" : "border-line"}`}>
              <span className={`w-[92px] shrink-0 font-mono ${d.dia === hoje ? "text-accent-text" : "text-faint"}`}>{d.dia === hoje ? "hoje" : diaCurto(d.dia)}</span>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                {d.tarefas.map((t) => (
                  <div key={t.id} className="flex items-center gap-2">
                    <button role="checkbox" aria-checked={t.feito} onClick={() => marcar(t)}
                            className={`grid size-4 shrink-0 place-items-center rounded-[4px] border ${t.feito ? "border-ok bg-ok/20 text-ok" : "border-line-strong hover:border-focus"}`}>
                      {t.feito && <Check className="size-3" />}
                    </button>
                    <span className={`min-w-0 flex-1 text-[12.5px] ${t.feito ? "text-faint line-through" : "text-fg"}`}>{t.texto}</span>
                    {feitos[t.id] && (
                      <span className="flex shrink-0 gap-2 text-[11px]" title="Feito pelo piloto automático">
                        {feitos[t.id].resumo && t.tipo === "estudar" && (
                          <button className="text-ok hover:underline" onClick={() => onIr("resumo", t.topico || undefined, feitos[t.id].resumo)}>resumo ✓</button>
                        )}
                        {feitos[t.id].prova && (
                          <button className="text-ok hover:underline" onClick={() => onIr("provas", t.topico || undefined)}>prova ✓</button>
                        )}
                        {feitos[t.id].erro && <span className="text-amber-300" title={feitos[t.id].erro}>falhou</span>}
                      </span>
                    )}
                    <span className="font-mono text-faint">{t.minutos} min</span>
                    {d.dia === hoje && !t.feito && (
                      <button className="text-accent-text hover:underline"
                              onClick={() => (t.tipo === "simulado" ? onProva({ topicos: [], instrucoes: "Simulado: todos os tópicos, no estilo da prova." })
                                : onIr(t.tipo === "revisar" ? "revisao" : "resumo", t.topico || undefined))}>
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
  onProva: (p: ProvaPendente) => void;
  onIr: Ir;
  onError: (e: string) => void;
  piloto?: React.ReactNode;
  feitos?: Feitos;
}) {
  const [d, setD] = useState<EstudosDesempenho | null>(null);
  const carregar = useCallback(() => {
    api.get<EstudosDesempenho>(`/estudos/${props.conv}/desempenho`).then(setD).catch((e) => props.onError(e.message));
  }, [props.conv]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { carregar(); }, [carregar, props.carimbo]);

  const provas = d?.entregas.filter((e) => e.modo === "prova") ?? [];
  const media = provas.length ? provas.reduce((s, e) => s + e.nota, 0) / provas.length : 0;
  const testados = d?.topicos.filter((t) => t.pct !== null) ?? [];
  // com o edital, são dezenas de tópicos ainda sem questão: viram uma linha (e abrem num clique)
  const [todos, setTodos] = useState(false);
  const semQuestao = (d?.topicos.length ?? 0) - testados.length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-8 pt-6 pb-10">
        <div className="mx-auto flex max-w-[960px] flex-col gap-3">
          {d && (
            <>
              <div className="grid grid-cols-[1.5fr_1fr] gap-3">
              <div className={`${cardD} flex min-w-0 flex-col gap-3`}>
                <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
                  <div>
                    <p className={rotulo}>{d.entregas.at(-1)?.modo === "treino" ? "último treino" : "última prova"}</p>
                    <p className="mt-1 font-mono text-4xl font-semibold text-fg">
                      {d.entregas.length ? nota(d.entregas.at(-1)!.nota) : "—"}
                      {!!d.entregas.length && <span className="text-lg text-faint"> / 10</span>}
                    </p>
                  </div>
                  <div className="text-xs text-muted">
                    <p>{d.entregas.length} entrega(s) · média das provas {provas.length ? nota(media) : "—"}</p>
                    <p className="mt-0.5">ponto cheio = prova · ponto vazado = treino · tracejado = 7</p>
                  </div>
                </div>
                {d.entregas.length ? <Grafico entregas={d.entregas} />
                  : <p className="text-xs text-faint">Faça uma prova (ou um treino) na aba Provas para ver a evolução aqui.</p>}
              </div>

              <div className={`${cardD} flex flex-col gap-3 text-xs text-muted`}>
                <span className="text-sm text-fg">Revisão</span>
                <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                  <div><p className="font-mono text-2xl font-semibold text-fg">{d.revisao.vencem}</p><p>para hoje</p></div>
                  <div><p className="font-mono text-2xl font-semibold text-fg">{d.revisao.dominados}</p><p>dominados</p></div>
                  <div><p className="font-mono text-base text-fg-2">{d.revisao.erros}</p><p>no caderno de erros</p></div>
                  <div><p className="font-mono text-base text-fg-2">{d.revisao.cartoes}</p><p>cartões</p></div>
                </div>
                <button className={`${btn} mt-auto self-start`} onClick={() => props.onIr("revisao")}>Abrir a revisão <ArrowRight className="size-3.5" /></button>
              </div>
              </div>

              {!!d.topicos.length && (
                <div className={`${cardD} flex flex-col gap-1.5 text-xs`}>
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
                  {(todos ? d.topicos : testados).map((t) => (
                    <div key={t.topico} className="flex items-center gap-3">
                      <span className={`w-[220px] shrink-0 truncate ${d.fracos.includes(t.topico) ? "text-amber-300" : "text-muted"}`} title={t.topico}>{t.topico}</span>
                      <div className="relative h-1.5 min-w-0 flex-1 rounded-full bg-raised">
                        {t.pct !== null && <div className={`h-full rounded-full ${corPct(t.pct)}`} style={{ width: `${Math.round(t.pct * 100)}%` }} />}
                        {t.ultima !== null && <div className="absolute -top-1 h-3.5 w-0.5 bg-fg/70" style={{ left: `${Math.round(t.ultima * 100)}%` }} />}
                      </div>
                      <span className="w-20 shrink-0 text-right font-mono text-faint">{t.pct === null ? "sem questão" : `${Math.round(t.pct * 100)}%`}</span>
                    </div>
                  ))}
                  {!!semQuestao && (
                    <button className="self-start text-faint hover:text-fg" onClick={() => setTodos((v) => !v)}>
                      {todos ? "Esconder os tópicos sem questão" : `${testados.length ? "+ " : ""}${semQuestao} tópico${semQuestao === 1 ? "" : "s"} ainda sem questão · ver`}
                    </button>
                  )}
                </div>
              )}

              {props.piloto}
              <Cronograma key={d.plano?.criado ?? "novo"} conv={props.conv} plano={d.plano} lembrete={d.lembrete} onMudou={carregar}
                          feitos={props.feitos ?? {}}
                          onProva={props.onProva} onIr={props.onIr} onError={props.onError} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

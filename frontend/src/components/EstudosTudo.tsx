import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { EstudosVisao, EstudosVisaoMateria } from "../types";
import { ArrowRight, Check, Copy } from "./icons";
import { Markdown } from "./MessageView";
import { matematica } from "./estudosTexto";
import { btn, btnPrimary, card, corAcerto, corAcertoFundo, diasAte, pilulaFraco, rotulo, trilho } from "./estudosUi";

/** O "Tudo" lê a visão do objetivo uma vez e de novo quando o estudo muda (carimbo) ou o peso muda aqui. */
function useVisao(conv: number, carimbo: string | undefined, onError: (e: string) => void) {
  const [v, setV] = useState<EstudosVisao | null>(null);
  const ler = useCallback(() => {
    api.get<EstudosVisao>(`/estudos/${conv}/visao`).then(setV).catch((e) => onError(e.message));
  }, [conv]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(ler, [ler, carimbo]);
  return [v, ler] as const;
}

const pct = (a: number | null) => (a == null ? "—" : `${a}%`);

/** Peso da matéria, editável na própria linha (vale ao sair do campo ou no Enter). */
function Peso({ m, conv, onMudou, onError }: { m: EstudosVisaoMateria; conv: number; onMudou: () => void; onError: (e: string) => void }) {
  const [valor, setValor] = useState(String(m.peso));
  useEffect(() => setValor(String(m.peso)), [m.peso]);
  async function gravar() {
    const n = Math.max(1, Math.min(100, Math.round(Number(valor) || 1)));
    setValor(String(n));
    if (n === m.peso) return;
    try {
      await api.patch(`/estudos/${conv}/materias/${m.id}`, { peso: n });
      onMudou();
    } catch (e: any) {
      onError(e.message);
    }
  }
  return (
    <input type="number" min={1} max={100} value={valor} aria-label={`Peso de ${m.nome}`} onClick={(e) => e.stopPropagation()}
           onChange={(e) => setValor(e.target.value)} onBlur={gravar} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
           title="Peso no simulado geral e no cronograma: o número de questões da matéria no edital, por exemplo"
           className="w-14 rounded-md border border-line bg-surface px-1.5 py-0.5 text-right font-mono text-[12px] text-fg focus:border-focus focus:outline-none" />
  );
}

/** Anel do acerto geral: o arco na cor do acerto e um traço na meta de 70%. */
function Anel({ acerto }: { acerto: number | null }) {
  const r = 38, c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 92 92" className="size-[92px] shrink-0" aria-hidden>
      <circle cx="46" cy="46" r={r} fill="none" strokeWidth="8" className="stroke-raised" />
      {acerto != null && acerto > 0 && (
        <circle cx="46" cy="46" r={r} fill="none" strokeWidth="8" strokeLinecap="round" transform="rotate(-90 46 46)"
                strokeDasharray={`${(c * acerto) / 100} ${c}`} className={corAcerto(acerto).replace("bg-", "stroke-")} />
      )}
      {/* a meta: 70% do caminho, a partir do topo */}
      <line x1="46" y1="2" x2="46" y2="14" strokeWidth="1.6" className="stroke-fg" transform={`rotate(${0.7 * 360} 46 46)`} />
      <text x="46" y="46" textAnchor="middle" dominantBaseline="central" className="fill-fg font-mono text-[22px] font-semibold">
        {acerto == null ? "—" : `${acerto}%`}
      </text>
    </svg>
  );
}

/** Régua dos dias até a prova: hoje em accent, a prova em fg, um traço maior a cada 7 dias. */
function Regua({ dias }: { dias: number }) {
  const n = Math.min(dias, 120);   // ponytail: régua de até 120 dias; além disso cada barra já vira um fio
  return (
    <div className="mt-3">
      <div className="flex h-[18px] items-end gap-0.5" aria-hidden>
        {Array.from({ length: n + 1 }, (_, i) => (
          <span key={i} className={`flex-1 rounded-[1px] ${
            i === 0 ? "h-[18px] bg-accent" : i === n ? "h-[18px] bg-fg" : i % 7 === 0 ? "h-3 bg-line-strong" : "h-2 bg-line"}`} />
        ))}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10.5px] text-faint"><span>hoje</span><span>prova</span></div>
    </div>
  );
}

const cardVisao = "rounded-xl border border-line bg-surface px-[18px] py-4";
const colunas = "grid grid-cols-[minmax(0,1.7fr)_minmax(150px,1.3fr)_112px_70px_62px_88px] items-center gap-3.5";

export function VisaoGeral(props: {
  conv: number; carimbo?: string; onError: (e: string) => void; onMudou: () => void;
  onAbrir: (materia: string) => void; onSimuladoFracos: () => void; onIr: (aba: "simulado" | "revisao" | "desempenho") => void;
}) {
  const [v, ler] = useVisao(props.conv, props.carimbo, props.onError);
  if (!v) return <p className="p-6 text-center text-xs text-faint">Lendo o objetivo…</p>;
  const fraca = v.materias.find((m) => m.id === v.fraca);
  const soma = v.materias.reduce((s, m) => s + m.peso, 0) || 1;
  const dias = diasAte(v.plano?.data);
  const num = (n: number) => <span className={n ? "" : "text-faint"}>{n}</span>;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-8 pt-6 pb-10">
      <div className="mx-auto flex max-w-[1060px] flex-col gap-3">
        <div className="grid grid-cols-[1.2fr_1fr_1fr] gap-3">
          <div className={`${cardVisao} flex flex-col`} title="Pontos sobre o máximo, somando as entregas de todas as matérias">
            <p className={rotulo}>Acerto geral</p>
            <div className="mt-2.5 flex items-center gap-4">
              <Anel acerto={v.acerto} />
              <div className="min-w-0">
                <p className="text-[13px] text-fg-2"><span className="font-mono font-semibold text-fg">{v.entregas}</span> entregas</p>
                <p className="mt-1 text-[12px] text-muted">
                  Meta de 70% marcada no anel.{v.acerto != null && v.acerto < 70 ? ` Faltam ${70 - v.acerto} pontos.` : ""}
                </p>
              </div>
            </div>
            <button className={`${btn} mt-3.5 self-start text-[12.5px]`} onClick={() => props.onIr("simulado")}>Simulado geral <ArrowRight className="size-3.5" /></button>
          </div>

          <div className={`${cardVisao} flex flex-col`} title={v.plano?.data ? `Prova em ${v.plano.data}` : "Marque a data da prova no cronograma (aba Desempenho)"}>
            <p className={rotulo}>Faltam</p>
            <div className="mt-2 flex items-baseline gap-1.5">
              <span className="font-mono text-[30px] leading-none font-semibold text-fg">{dias != null && dias > 0 ? dias : "—"}</span>
              {dias != null && dias > 0 && <span className="text-[14px] text-fg-2">dias</span>}
              {v.plano?.data && <span className="ml-auto text-[12px] text-muted">prova {v.plano.data.split("-").reverse().join("/")}</span>}
            </div>
            {dias != null && dias > 0 && <Regua dias={dias} />}
            <button className={`${btn} mt-3.5 self-start text-[12.5px]`} onClick={() => props.onIr("desempenho")}>
              Desempenho e cronograma <ArrowRight className="size-3.5" />
            </button>
          </div>

          <div className={`${cardVisao} flex flex-col`} title="Erros e cartões que vencem hoje, de todas as matérias">
            <p className={rotulo}>Revisar hoje</p>
            <div className="mt-2 flex items-baseline gap-1.5">
              <span className="font-mono text-[30px] leading-none font-semibold text-fg">{v.vencem}</span>
              <span className="text-[14px] text-fg-2">itens</span>
            </div>
            <p className="mt-2 text-[12px] text-muted">Erros e cartões que vencem hoje, de todas as matérias</p>
            <button className={`${btn} mt-3.5 self-start text-[12.5px]`} onClick={() => props.onIr("revisao")}>Revisar tudo de hoje · {v.vencem}</button>
          </div>
        </div>

        {fraca && (
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-accent-line bg-accent-soft/50 px-[18px] py-4">
            <div className="min-w-0 flex-1">
              <p className={`${rotulo} text-accent-text!`}>Próximo passo</p>
              <p className="mt-1 text-[15px] font-semibold text-fg">Onde cada hora rende mais: {fraca.nome}</p>
              <p className="mt-0.5 text-[12.5px] text-muted">
                {Math.round((100 * fraca.peso) / soma)}% do peso do objetivo e {fraca.acerto == null ? "nenhuma prova feita ainda" : `${fraca.acerto}% de acerto`}
              </p>
              {!!fraca.fracos.length && (
                <p className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
                  mais fracos: {fraca.fracos.map((t) => <span key={t} className={pilulaFraco}>{t}</span>)}
                </p>
              )}
            </div>
            <button className={btn} onClick={() => props.onAbrir(fraca.id)}>Abrir a matéria <ArrowRight className="size-3.5" /></button>
            <button className={btnPrimary} onClick={props.onSimuladoFracos} title="Simulado geral com mais questões das matérias fracas">
              Simulado dos pontos fracos
            </button>
          </div>
        )}

        <div className={cardVisao}>
          <div className="mb-3 flex items-center gap-2">
            <p className={rotulo}>Por matéria</p>
            <span className="ml-auto text-[11.5px] text-faint">clique numa linha para abrir · o peso vale no simulado geral e no cronograma</span>
          </div>
          {!!v.materias.length && (
            <>
              <div className="flex h-[30px] gap-0.5 overflow-hidden rounded-lg">
                {v.materias.map((m) => (
                  <div key={m.id} title={`${m.nome}: ${Math.round((100 * m.peso) / soma)}% do peso`}
                       className={`flex min-w-0 items-center gap-1.5 px-2 transition-[width] duration-[250ms] ${corAcertoFundo(m.acerto)}`}
                       style={{ width: `${(100 * m.peso) / soma}%` }}>
                    <span className={`size-1.5 shrink-0 rounded-full ${corAcerto(m.acerto)}`} />
                    <span className="min-w-0 truncate text-[11.5px] text-fg-2">{m.nome}</span>
                    <span className="shrink-0 font-mono text-[10.5px] text-muted">{Math.round((100 * m.peso) / soma)}%</span>
                  </div>
                ))}
              </div>
              <p className="mt-1.5 text-[11px] text-faint">largura = peso no objetivo · cor = acerto (verde ≥ 70%, âmbar 50–69%, vermelho abaixo, cinza sem prova)</p>
            </>
          )}
          <div className={`${colunas} mt-4 px-2.5 pb-1.5 font-mono text-[10.5px] tracking-[.06em] text-faint uppercase`}>
            <span>Matéria</span><span>Acerto</span><span>Peso</span><span className="text-right">Resumos</span>
            <span className="text-right">Provas</span><span className="text-right">Cad. erros</span>
          </div>
          {v.materias.map((m) => (
            <div key={m.id} role="button" onClick={() => props.onAbrir(m.id)} title={`Abrir ${m.nome}`}
                 className={`${colunas} cursor-pointer rounded-lg border-t border-raised px-2.5 py-2.5 hover:bg-raised/60`}>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className={`size-2 shrink-0 rounded-full ${corAcerto(m.acerto)}`} />
                  <span className="min-w-0 truncate text-[13.5px] text-fg">{m.nome}</span>
                  {fraca?.id === m.id && <span className="shrink-0 rounded-[5px] bg-accent-soft px-1.5 py-px text-[10.5px] text-accent-text">prioridade</span>}
                </div>
                {!!m.fracos.length && <p className="mt-0.5 truncate pl-4 text-[11.5px] text-faint">fracos: {m.fracos.join(", ")}</p>}
              </div>
              <div className="flex items-center gap-2.5">
                <div className={`relative h-1.5 min-w-0 flex-1 ${trilho}`} aria-hidden>
                  <div className={`h-full rounded-full ${corAcerto(m.acerto)}`} style={{ width: `${m.acerto ?? 0}%` }} />
                  <span className="absolute top-0 left-[70%] h-full w-px bg-faint" />
                </div>
                <span className="w-9 shrink-0 text-right font-mono text-[12px] text-fg-2">{pct(m.acerto)}</span>
              </div>
              <span className="flex items-center gap-1.5">
                <Peso m={m} conv={props.conv} onMudou={() => { ler(); props.onMudou(); }} onError={props.onError} />
                <span className="font-mono text-[11.5px] text-faint">{Math.round((100 * m.peso) / soma)}%</span>
              </span>
              <span className="text-right font-mono text-[12.5px] text-fg-2">{num(m.resumos.length)}</span>
              <span className="text-right font-mono text-[12.5px] text-fg-2">{num(m.provas)}</span>
              <span className={`text-right font-mono text-[12.5px] ${m.erros >= 10 ? "text-amber-300" : "text-fg-2"}`}>{num(m.erros)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// as tabelas do quadro de revisão viram ficha (o .md global tem as bordas e o zebrado: aqui saem)
const ficha = "[&_.md-table]:border-0! [&_.md-table]:rounded-none! [&_.md_table]:border-0! [&_.md_tr]:bg-transparent! [&_.md_th]:bg-transparent! [&_.md_th]:text-left [&_.md_td]:border-0! [&_.md_th]:border-0! "
  + "[&_.md_tr]:border-b! [&_.md_tr]:border-raised! [&_.md_td:first-child]:w-[200px] [&_.md_td:first-child]:text-[13px]! [&_.md_td:first-child]:font-semibold "
  + "[&_.md_td:first-child]:text-fg-2! [&_.md_td+td]:text-muted! [&_.md_td+td]:leading-[1.5]!";

/** O resumo geral: um índice — cada matéria com os resumos dela, os pontos fracos e o quadro de revisão do
 * último resumo. Não reescreve nada (o resumo de cada matéria já fecha com o quadro). */
export function ResumoGeral(props: { conv: number; carimbo?: string; onError: (e: string) => void; onAbrir: (materia: string) => void }) {
  const [v] = useVisao(props.conv, props.carimbo, props.onError);
  const [copiado, setCopiado] = useState(false);
  if (!v) return <p className="p-6 text-center text-xs text-faint">Lendo o objetivo…</p>;
  const comQuadro = v.materias.filter((m) => m.quadro);
  function copiar() {
    const md = comQuadro.map((m) => `## ${m.nome}\n\n${m.quadro}`).join("\n\n");
    navigator.clipboard.writeText(md).then(() => { setCopiado(true); setTimeout(() => setCopiado(false), 1500); }).catch(() => {});
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-8 pt-6 pb-10">
      <div className="mx-auto flex max-w-[860px] flex-col gap-3">
        <div className="flex items-center gap-2">
          <div className="min-w-0">
            <h1 className="text-[18px] font-semibold text-fg">Resumo geral</h1>
            <p className="text-[12.5px] text-muted">o quadro de revisão de cada matéria</p>
          </div>
          {!!comQuadro.length && (
            <button className={`${btn} ml-auto text-xs`} onClick={copiar} title="Os quadros de todas as matérias, em Markdown: a folha da véspera">
              {copiado ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} {copiado ? "Copiado" : "Copiar a folha da véspera"}
            </button>
          )}
        </div>
        {v.materias.map((m) => (
          <section key={m.id} className="rounded-xl border border-line bg-surface px-[18px] py-4">
            <div className="flex items-center gap-2">
              <span className={`size-2 shrink-0 rounded-full ${corAcerto(m.acerto)}`} />
              <h2 className="min-w-0 flex-1 truncate text-[14.5px] font-semibold text-fg">{m.nome}</h2>
              <span className="font-mono text-[11px] text-faint">{pct(m.acerto)}{m.erros ? ` · ${m.erros} no caderno de erros` : ""}</span>
              <button className="text-xs text-accent-text hover:underline" onClick={() => props.onAbrir(m.id)}>abrir →</button>
            </div>
            {m.resumos.length ? (
              <p className="mt-1 text-xs text-muted">{m.resumos.map((r) => r.titulo || "Resumo").join(" · ")}</p>
            ) : (
              <p className="mt-1 text-xs text-faint">Sem resumo ainda: abra a matéria e peça o primeiro.</p>
            )}
            {!!m.fracos.length && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {m.fracos.map((t) => <span key={t} className="rounded-full border border-amber-300/40 px-2 py-0.5 text-[11px] text-amber-200">{t}</span>)}
              </div>
            )}
            {m.quadro ? <div className={`mt-3 border-t border-line pt-3 ${ficha}`}><Markdown text={matematica(m.quadro)} math /></div>
              : !!m.secoes.length && (
                <div className="mt-3 border-t border-line pt-3 text-xs">
                  <ol className="list-decimal space-y-0.5 pl-5 text-muted">{m.secoes.map((t) => <li key={t}>{t}</li>)}</ol>
                  <p className="mt-2 text-faint">Para a folha da véspera, ligue "Revisão rápida" nas preferências do próximo resumo desta matéria.</p>
                </div>
              )}
          </section>
        ))}
      </div>
    </div>
  );
}

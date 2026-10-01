import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { EstudosVisao, EstudosVisaoMateria } from "../types";
import { ArrowRight, Check, Copy } from "./icons";
import { Markdown } from "./MessageView";
import { corAcerto } from "./EstudosMaterias";
import { matematica } from "./estudosTexto";
import { btn, btnPrimary, card, rotulo } from "./estudosUi";

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

function Numero(props: { rotulo: string; valor: string; dica?: string }) {
  return (
    <div className="rounded-xl bg-raised/60 px-3.5 py-3" title={props.dica}>
      <p className="text-[11px] text-faint">{props.rotulo}</p>
      <p className="mt-0.5 font-mono text-xl font-semibold text-fg">{props.valor}</p>
    </div>
  );
}

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

export function VisaoGeral(props: {
  conv: number; carimbo?: string; onError: (e: string) => void; onMudou: () => void;
  onAbrir: (materia: string) => void; onSimuladoFracos: () => void; onIr: (aba: "simulado" | "revisao" | "desempenho") => void;
}) {
  const [v, ler] = useVisao(props.conv, props.carimbo, props.onError);
  if (!v) return <p className="p-6 text-center text-xs text-faint">Lendo o objetivo…</p>;
  const fraca = v.materias.find((m) => m.id === v.fraca);
  const soma = v.materias.reduce((s, m) => s + m.peso, 0) || 1;
  const dias = v.plano?.data ? Math.ceil((new Date(`${v.plano.data}T00:00:00`).getTime() - Date.now()) / 86_400_000) : null;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      <div className="mx-auto flex max-w-4xl flex-col gap-3">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Numero rotulo="Acerto geral" valor={pct(v.acerto)} dica="Pontos sobre o máximo, somando as entregas de todas as matérias" />
          <Numero rotulo="Entregas" valor={String(v.entregas)} />
          <Numero rotulo="Revisar hoje" valor={String(v.vencem)} dica="Erros e cartões que vencem hoje, de todas as matérias" />
          <Numero rotulo="Faltam" valor={dias != null && dias > 0 ? `${dias} dias` : "—"} dica={v.plano?.data ? `Prova em ${v.plano.data}` : "Marque a data da prova no cronograma (aba Desempenho)"} />
        </div>

        <div className={card}>
          <div className="mb-2 flex items-center gap-2">
            <p className={rotulo}>Por matéria</p>
            <span className="ml-auto text-[11px] text-faint">acerto · peso · resumos · provas · no caderno de erros</span>
          </div>
          {v.materias.map((m) => (
            <div key={m.id} role="button" onClick={() => props.onAbrir(m.id)} title={`Abrir ${m.nome}`}
                 className="-mx-2 flex cursor-pointer items-center gap-3 rounded-lg px-2 py-1.5 text-xs hover:bg-raised/60">
              <span className={`size-2 shrink-0 rounded-full ${corAcerto(m.acerto)}`} />
              <span className="w-48 shrink-0 truncate text-[13px] text-fg">{m.nome}</span>
              <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-raised" aria-hidden>
                <div className={`h-full rounded-full ${corAcerto(m.acerto)}`} style={{ width: `${m.acerto ?? 0}%` }} />
              </div>
              <span className="w-10 shrink-0 text-right font-mono text-faint">{pct(m.acerto)}</span>
              <span className="flex w-24 shrink-0 items-center justify-end gap-1.5 text-faint">
                <Peso m={m} conv={props.conv} onMudou={() => { ler(); props.onMudou(); }} onError={props.onError} />
                <span className="w-8 font-mono">{Math.round((100 * m.peso) / soma)}%</span>
              </span>
              <span className="w-28 shrink-0 text-right font-mono text-faint">{m.resumos.length} · {m.provas} · {m.erros}</span>
            </div>
          ))}
        </div>

        {fraca && (
          <div className={`${card} flex flex-wrap items-center gap-3`}>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-fg">Onde cada hora rende mais: {fraca.nome}</p>
              <p className="mt-0.5 text-xs text-muted">
                {Math.round((100 * fraca.peso) / soma)}% do peso do objetivo e {fraca.acerto == null ? "nenhuma prova feita ainda" : `${fraca.acerto}% de acerto`}
                {fraca.fracos.length ? ` · mais fracos: ${fraca.fracos.join(", ")}` : ""}
              </p>
            </div>
            <button className={btn} onClick={() => props.onAbrir(fraca.id)}>Abrir a matéria <ArrowRight className="size-3.5" /></button>
            <button className={btnPrimary} onClick={props.onSimuladoFracos} title="Simulado geral com mais questões das matérias fracas">
              Simulado dos pontos fracos
            </button>
          </div>
        )}

        <div className="flex flex-wrap gap-2 text-xs">
          <button className={btn} onClick={() => props.onIr("simulado")}>Simulado geral <ArrowRight className="size-3.5" /></button>
          <button className={btn} onClick={() => props.onIr("revisao")}>Revisar tudo de hoje · {v.vencem}</button>
          <button className={btn} onClick={() => props.onIr("desempenho")}>Desempenho e cronograma</button>
        </div>
      </div>
    </div>
  );
}

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
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        <div className="flex items-center gap-2">
          <p className={rotulo}>Resumo geral · o quadro de revisão de cada matéria</p>
          {!!comQuadro.length && (
            <button className={`${btn} ml-auto text-xs`} onClick={copiar} title="Os quadros de todas as matérias, em Markdown: a folha da véspera">
              {copiado ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} {copiado ? "Copiado" : "Copiar a folha da véspera"}
            </button>
          )}
        </div>
        {v.materias.map((m) => (
          <section key={m.id} className={card}>
            <div className="flex items-center gap-2">
              <span className={`size-2 shrink-0 rounded-full ${corAcerto(m.acerto)}`} />
              <h2 className="min-w-0 flex-1 truncate text-sm font-medium text-fg">{m.nome}</h2>
              <span className="text-[11px] text-faint">{pct(m.acerto)}{m.erros ? ` · ${m.erros} no caderno de erros` : ""}</span>
              <button className="text-xs text-accent hover:underline" onClick={() => props.onAbrir(m.id)}>abrir →</button>
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
            {m.quadro ? <div className="mt-3 border-t border-line pt-3"><Markdown text={matematica(m.quadro)} math /></div>
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

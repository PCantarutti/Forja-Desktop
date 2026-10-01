import { useState } from "react";
import { api } from "../api";
import type { EstudosProjeto } from "../types";
import { Pause, Play } from "./icons";
import { btn, btnPrimary, rotulo } from "./estudosUi";

/** O piloto automático: resumo e prova de cada tópico do cronograma, sozinho, até o dia escolhido. Mora na
 *  Visão geral e em cima do cronograma; o estado vem do projeto (o carimbo atualiza nos dois aparelhos). */
export default function Piloto(props: {
  conv: number;
  projeto: EstudosProjeto;
  corpo: Record<string, unknown>;   // preferências, web, profundidade e o motor da tela
  botaoModelos: React.ReactNode;
  onMudou: () => void;
  onError: (e: string) => void;
}) {
  const p = props.projeto.piloto;
  const plano = props.projeto.revisao?.plano;
  const [ate, setAte] = useState(p?.ate || plano?.data || "");
  const [questoes, setQuestoes] = useState(p?.questoes || 10);
  const [enviando, setEnviando] = useState(false);
  const [hoje] = useState(() => new Date().toLocaleDateString("sv-SE"));

  async function chamar(caminho: string, corpo: object) {
    setEnviando(true);
    try {
      await api.post(`/estudos/${props.conv}/${caminho}`, corpo);
      props.onMudou();
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setEnviando(false);
    }
  }

  const pct = p?.total ? Math.round((100 * p.prontos) / p.total) : 0;
  const continuar = !!p && p.prontos > 0 && p.prontos < p.total;
  return (
    <div className="rounded-xl border border-line bg-surface px-[18px] py-4 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <p className={rotulo}>Piloto automático</p>
        {p?.ativo && <span className="size-1.5 animate-pulse rounded-full bg-accent" aria-hidden />}
        {!!p?.total && <span className="ml-auto font-mono text-faint">{p.prontos} de {p.total} itens do cronograma</span>}
      </div>
      {p?.ativo ? (
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-[13.5px] text-fg first-letter:uppercase">{p.fase || "trabalhando"}…</p>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-raised" aria-hidden>
              <div className="h-full rounded-full bg-accent transition-[width] duration-[250ms]" style={{ width: `${pct}%` }} />
            </div>
            <p className="mt-1.5 text-faint">
              até {p.ate.split("-").reverse().join("/")} · pode tirar dúvidas enquanto ele trabalha; pause para pedir outra coisa ao modelo
            </p>
          </div>
          <button className={btn} disabled={enviando} onClick={() => chamar("piloto/pausar", {})}>
            <Pause className="size-3.5" /> Pausar
          </button>
        </div>
      ) : (
        <>
          <p className="mt-1.5 text-[12.5px] text-muted">
            Gera sozinho o resumo e uma prova de cada tópico do cronograma, um depois do outro, até o dia escolhido. Antes,
            procura provas anteriores de cada matéria na web para as provas imitarem o estilo. Dá para deixar rodando à noite.
          </p>
          {!plano ? (
            <p className="mt-2 text-faint">Monte o cronograma primeiro (aba Desempenho, ou pelo edital): o piloto segue ele.</p>
          ) : (
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-muted">Preparar até
                <input type="date" min={hoje} max={plano.data} value={ate} onChange={(e) => setAte(e.target.value)}
                       className="rounded-md border border-line bg-raised px-2 py-1.5 text-fg focus:border-focus focus:outline-none" />
              </label>
              <label className="flex flex-col gap-1 text-muted">Questões por prova
                <input type="number" min={1} max={40} value={questoes} onChange={(e) => setQuestoes(Math.max(1, Math.min(40, Number(e.target.value) || 10)))}
                       className="w-20 rounded-md border border-line bg-raised px-2 py-1.5 text-right font-mono text-fg focus:border-focus focus:outline-none" />
              </label>
              {props.botaoModelos}
              <button className={`${btnPrimary} ml-auto`} disabled={!ate || enviando}
                      onClick={() => chamar("piloto", { ...props.corpo, ate, questoes })}>
                <Play className="size-3.5" /> {continuar ? "Continuar" : p?.total && p.prontos >= p.total ? "Preparar até a nova data" : "Começar"}
              </button>
            </div>
          )}
        </>
      )}
      {p?.aviso && <p className="mt-2 text-amber-300">{p.aviso}</p>}
    </div>
  );
}

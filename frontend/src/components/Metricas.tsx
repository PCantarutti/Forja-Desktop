import { useEffect, useState } from "react";
import { api } from "../api";

type Resumo = {
  dias: number;
  tarefas: { total: number; concluidas: number; needs_human: number; primeira_tentativa_pct: number | null; needs_human_pct: number | null; tentativas_media: number | null };
  cache: { respostas: number; hit_pct: number | null; reprocessados: number; derrubado_por_auxiliar: number; auxiliares: [string, number][] };
  rotas: [string, number][];
  trocas: { n: number; segundos: number };
  ferramentas_falham: { nome: string; falhas: number; total: number; pct: number | null }[];
  recuperacao: Record<string, number>;
  juiz: Record<string, number>;
  filtro_abortos: number;
  exploracoes: number;
};

const pct = (v: number | null | undefined) => (v == null ? "—" : `${String(v).replace(".", ",")}%`);
const num = (v: number) => v.toLocaleString("pt-BR");

function Cartao({ titulo, valor, nota }: { titulo: string; valor: string; nota?: string }) {
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="text-xs text-muted">{titulo}</div>
      <div className="mt-1 text-xl text-fg tabular-nums">{valor}</div>
      {nota && <div className="mt-0.5 text-[11.5px] text-faint">{nota}</div>}
    </div>
  );
}

function Lista({ titulo, itens, vazio }: { titulo: string; itens: [string, string][]; vazio: string }) {
  return (
    <div>
      <div className="mb-1.5 text-xs font-medium tracking-wide text-muted uppercase">{titulo}</div>
      {itens.length ? (
        <ul className="divide-y divide-line rounded-lg border border-line text-sm">
          {itens.map(([k, v]) => (
            <li key={k} className="flex items-center justify-between gap-3 px-3 py-1.5">
              <span className="min-w-0 truncate text-fg-2">{k}</span>
              <span className="shrink-0 text-muted tabular-nums">{v}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-faint">{vazio}</p>
      )}
    </div>
  );
}

/** E10: "o Maestro melhorou?" — sucesso de tarefa, cache do principal, rotas da como_rodar, trocas e falhas. */
export default function Metricas({ onError }: { onError: (e: string) => void }) {
  const [dias, setDias] = useState(7);
  const [r, setR] = useState<Resumo | null>(null);
  useEffect(() => {
    api.get<Resumo>(`/metricas?dias=${dias}`).then(setR).catch((e) => onError(e.message));
  }, [dias]);
  if (!r) return <p className="text-sm text-muted">Carregando…</p>;
  const niveis = ["2", "3", "4", "5"].map((n) => r.recuperacao[n] ?? 0);
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-sm text-muted">
        Período:
        {[1, 7, 30].map((d) => (
          <button key={d} onClick={() => setDias(d)}
                  className={`rounded-md px-2 py-0.5 ${d === dias ? "bg-raised text-fg ring-1 ring-accent-line" : "hover:text-fg"}`}>
            {d === 1 ? "24 h" : `${d} dias`}
          </button>
        ))}
      </div>

      <section className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
        <Cartao titulo="Sucesso na 1ª tentativa" valor={pct(r.tarefas.primeira_tentativa_pct)} nota={`${r.tarefas.concluidas} de ${r.tarefas.total} tarefas concluídas`} />
        <Cartao titulo="Tentativas por tarefa" valor={r.tarefas.tentativas_media == null ? "—" : String(r.tarefas.tentativas_media).replace(".", ",")} />
        <Cartao titulo="Precisa de você" valor={pct(r.tarefas.needs_human_pct)} nota={`${r.tarefas.needs_human} em needs_human`} />
        <Cartao titulo="Tempo em troca de modelo" valor={`${Math.round(r.trocas.segundos / 60)} min`} nota={`${r.trocas.n} troca(s)`} />
      </section>

      <section className="grid grid-cols-2 gap-2.5 md:grid-cols-3">
        <Cartao titulo="Cache do principal" valor={pct(r.cache.hit_pct)} nota={`${num(r.cache.respostas)} respostas medidas (IA local)`} />
        <Cartao titulo="Tokens reprocessados" valor={num(r.cache.reprocessados)} />
        <Cartao titulo="Cache derrubado por auxiliar" valor={String(r.cache.derrubado_por_auxiliar)}
                nota={r.cache.auxiliares.length ? r.cache.auxiliares.map(([p, n]) => `${p} ×${n}`).join(", ") : "nenhuma chamada auxiliar derrubou"} />
      </section>
      {r.cache.derrubado_por_auxiliar > 0 && (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5 text-xs text-amber-100">
          Uma chamada auxiliar rodou antes de uma resposta que reprocessou mais de 2 mil tokens: o cache do principal
          caiu. Com um slot só (-np 1), aumentar os slots do servidor evita isso.
        </p>
      )}

      <div className="grid gap-5 md:grid-cols-2">
        <Lista titulo="Caminhos da como_rodar" vazio="Nenhuma decisão registrada no período."
               itens={r.rotas.map(([k, v]) => [k, String(v)])} />
        <Lista titulo="Ferramentas que mais falham" vazio="Nenhuma falha no período."
               itens={r.ferramentas_falham.map((f) => [f.nome, `${f.falhas}/${f.total} (${pct(f.pct)})`])} />
      </div>

      <Lista titulo="Recuperação de loop e juiz" vazio=""
             itens={[
               ["Intervenções · contexto limpo · recuos · estacionou", niveis.join(" · ")],
               ["Juiz do raciocínio", Object.entries(r.juiz).map(([k, v]) => `${k} ${v}`).join(", ") || "não rodou"],
               ["Raciocínios abortados pelo filtro", String(r.filtro_abortos)],
               ["Explorações", String(r.exploracoes)],
             ]} />
    </div>
  );
}

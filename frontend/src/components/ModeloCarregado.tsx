import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal } from "./Modal";
import { Cpu, X } from "./icons";

type Gpu = { nome: string; total: number; usado: number };
export type Uso = {
  modelo: { alias: string; path: string; tamanho: number; ctx: number | null; uptime: number; vision: boolean;
            ngl: number | null; cache: string } | null;
  carregando: { name: string; percent: number } | null;
  gpus: Gpu[];
  ram: { total: number; usado: number };
  gerando_imagem: boolean;
};

const gb = (b: number) => `${(b / 2 ** 30).toFixed(1).replace(".", ",")} GB`;
const tempo = (s: number) => (s < 3600 ? `${Math.max(1, Math.round(s / 60))} min` : `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`);
// Verde até 70%, âmbar até 90%, vermelho acima: é o que diz se cabe mais alguma coisa.
const cor = (f: number) => (f < 0.7 ? "bg-emerald-400/80" : f < 0.9 ? "bg-amber-400/80" : "bg-red-400/80");

/** A placa que mais importa na linha do rodapé: a de mais VRAM em uso (é onde o modelo está). */
const principal = (gpus: Gpu[]) => [...gpus].sort((a, b) => b.usado - a.usado)[0];

function Barra({ usado, total }: { usado: number; total: number }) {
  const f = total ? Math.min(1, usado / total) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised">
      <div className={`h-full rounded-full ${cor(f)} transition-[width] duration-500`} style={{ width: `${f * 100}%` }} />
    </div>
  );
}

function Memoria({ rotulo, usado, total }: { rotulo: string; usado: number; total: number }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline gap-2 text-sm">
        <span className="min-w-0 flex-1 truncate text-fg" title={rotulo}>{rotulo}</span>
        <span className="shrink-0 font-mono text-xs text-muted">{gb(usado)} de {gb(total)}</span>
      </div>
      <Barra usado={usado} total={total} />
    </div>
  );
}

/** Meio do cabeçalho: qual modelo local está carregado (ou carregando) e a VRAM; o clique abre os detalhes. */
export default function ModeloCarregado() {
  const [uso, setUso] = useState<Uso | null>(null);
  const [aberto, setAberto] = useState(false);
  const [descarregando, setDescarregando] = useState(false);

  useEffect(() => {
    const carrega = () => api.get<Uso>("/local/uso").then(setUso).catch(() => {});
    carrega();
    const t = setInterval(carrega, aberto ? 2500 : 8000); // aberto, os números andam quase ao vivo
    return () => clearInterval(t);
  }, [aberto]);

  if (!uso) return null;
  const m = uso.modelo;
  const g = principal(uso.gpus);
  const ponto = uso.carregando ? "bg-amber-400 animate-pulse" : m ? "bg-emerald-400" : "bg-faint";
  const titulo = uso.carregando ? `Carregando ${uso.carregando.name}… ${uso.carregando.percent}%` : m ? m.alias : "Nenhum modelo carregado";

  async function descarregar() {
    setDescarregando(true);
    await api.post("/local/unload", {}).catch(() => {});
    setUso(await api.get<Uso>("/local/uso").catch(() => uso));
    setDescarregando(false);
  }

  return (
    <>
      <button
        onClick={() => setAberto(true)}
        title="IA local: modelo carregado e memória"
        className="flex max-w-80 items-center gap-2 rounded-[9px] border border-line bg-surface px-3 py-1 text-xs hover:border-[#3d3d3d] hover:bg-raised"
      >
        <span className={`size-2 shrink-0 rounded-full ${ponto}`} />
        <span className={`min-w-0 truncate ${m || uso.carregando ? "text-fg" : "text-muted"}`}>{titulo}</span>
        {/* Sem modelo, a placa "mais usada" pode ser a integrada: o número confundia. Fica no painel. */}
        {m && g && (
          <>
            <span className="w-12 shrink-0"><Barra usado={g.usado} total={g.total} /></span>
            <span className="shrink-0 font-mono text-[11px] text-faint">{gb(g.usado).replace(" GB", "")}/{gb(g.total)}</span>
          </>
        )}
      </button>

      {aberto && (
        <Modal onClose={() => setAberto(false)} label="IA local" className="w-full max-w-md space-y-5 rounded-xl border border-line bg-surface p-5">
          <div className="flex items-start gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-raised text-muted"><Cpu className="size-4" /></span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-base font-medium text-fg">
                <span className={`size-2 shrink-0 rounded-full ${ponto}`} />
                <span className="truncate" title={m?.path}>{titulo}</span>
              </div>
              <div className="mt-0.5 text-xs text-muted">
                {m ? `IA local · no ar há ${tempo(m.uptime)}` : uso.carregando ? "IA local · subindo o llama-server" : "IA local · o llama-server está desligado"}
              </div>
            </div>
            <button onClick={() => setAberto(false)} title="Fechar" aria-label="Fechar"
                    className="-mr-1 -mt-1 rounded-lg p-1.5 text-faint hover:bg-raised hover:text-fg">
              <X className="size-4" />
            </button>
          </div>

          {m && (
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {([
                ["Arquivo", m.tamanho ? gb(m.tamanho) : "—"],
                ["Contexto", m.ctx ? `${m.ctx.toLocaleString("pt-BR")} tokens` : "—"],
                ["Camadas na GPU", m.ngl == null ? "—" : m.ngl >= 999 ? "todas" : String(m.ngl)],
                ["Cache KV", m.cache || "—"],
              ] as const).map(([k, v]) => (
                <div key={k} className="rounded-xl bg-raised/60 px-3 py-2">
                  <dt className="text-[11px] text-faint">{k}</dt>
                  <dd className="truncate text-fg">{v}</dd>
                </div>
              ))}
            </dl>
          )}

          <div className="space-y-3">
            <div className="text-[10.5px] tracking-[.08em] text-faint font-mono uppercase">Memória da máquina</div>
            {uso.gpus.map((x) => <Memoria key={x.nome} rotulo={x.nome} usado={x.usado} total={x.total} />)}
            <Memoria rotulo="RAM" usado={uso.ram.usado} total={uso.ram.total} />
            {uso.gerando_imagem && <div className="text-xs text-sky-300">Gerando imagem ou vídeo agora: a GPU está com o sd.cpp.</div>}
          </div>

          {m && (
            <div className="flex justify-end">
              <button onClick={descarregar} disabled={descarregando}
                      className="rounded-xl border border-line px-4 py-2 text-sm text-fg hover:bg-raised disabled:opacity-50">
                {descarregando ? "Descarregando…" : "Descarregar modelo"}
              </button>
            </div>
          )}
        </Modal>
      )}
    </>
  );
}

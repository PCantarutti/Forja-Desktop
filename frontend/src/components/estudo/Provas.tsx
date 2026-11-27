import { useEffect, useState } from "react";
import { api } from "../../api";
import type { Ctx } from "./EstudoView";
import Simulados, { JanelaPdf } from "./Simulados";
import type { Prova, Questao } from "./tipos";
import { btn, btnLeve, btnPrimary, campo, card, pct, pilula, pilulaLigada, relogio, rotulo } from "./ui";

const TIPOS = [{ id: "multipla", label: "Múltipla escolha" }, { id: "certo_errado", label: "Certo ou errado" },
               { id: "discursiva", label: "Discursiva" }];
const DIFS = [{ id: "facil", label: "Fácil" }, { id: "media", label: "Média" }, { id: "dificil", label: "Difícil" }];
const LETRAS = "ABCDE";

export default function Provas({ ctx }: { ctx: Ctx }) {
  const { estudo } = ctx;
  const [aberta, setAberta] = useState<string | null>(null);
  const prova = estudo.provas.find((p) => p.id === aberta);
  // prova recém-gerada abre sozinha
  const [vistas, setVistas] = useState(() => new Set(estudo.provas.map((p) => p.id)));
  useEffect(() => {
    const nova = estudo.provas.find((p) => !vistas.has(p.id) && !p.simulado);   // prova real não abre sozinha
    if (nova) { setAberta(nova.id); setVistas(new Set(estudo.provas.map((p) => p.id))); }
  }, [estudo.provas]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (prova) return <Fazer ctx={ctx} prova={prova} voltar={() => setAberta(null)} />;
  return (
    <div className="mx-auto grid max-w-4xl gap-5 px-6 py-6">
      <Gerar ctx={ctx} />
      {estudo.provas.length > 0 && (
        <section className={card}>
          <div className={`${rotulo} mb-2`}>Provas</div>
          {[...estudo.provas].reverse().map((p) => {
            const t = p.tentativas[p.tentativas.length - 1];
            return (
              <div key={p.id} className="flex items-center gap-3 rounded-lg px-2 py-2 text-[13.5px] hover:bg-raised">
                <button className="min-w-0 flex-1 text-left" onClick={() => setAberta(p.id)}>
                  <span className="text-fg">{p.id} · {p.questoes.length} questões · {p.simulado ? `Prova real · ${p.titulo ?? ""}` : TIPOS.find((x) => x.id === p.config.tipo)?.label}</span>
                  <span className="ml-2 text-[12px] text-muted">{new Date(p.criada * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}
                    {p.descartadas ? ` · ${p.descartadas} descartadas por falta de fonte` : ""}</span>
                </button>
                <span className="font-mono text-[12px] text-muted">{t ? `${t.acertos}/${t.total}` : "não feita"}</span>
                <button className={btnLeve} onClick={async () => { await api.del(`/estudo/${ctx.conv}/prova/${p.id}`); ctx.recarregar(); }}>Apagar</button>
              </div>
            );
          })}
        </section>
      )}
      <Simulados ctx={ctx} abrirProva={setAberta} />
    </div>
  );
}

function Gerar({ ctx }: { ctx: Ctx }) {
  const { estudo } = ctx;
  const [n, setN] = useState(10);
  const [tipo, setTipo] = useState("multipla");
  const [dif, setDif] = useState("media");
  const [topicos, setTopicos] = useState<string[]>([]);
  const fracos = estudo.desempenho.fracos;
  const corpus = estudo.fontes.some((f) => f.status === "util");
  const alterna = (id: string) => setTopicos((t) => (t.includes(id) ? t.filter((x) => x !== id) : [...t, id]));
  return (
    <section className={card}>
      <div className={`${rotulo} mb-3`}>Nova prova</div>
      {!corpus && <div className="mb-3 text-[13px] text-warn">O corpus está vazio: pesquise ou mande arquivos na aba Fontes.</div>}
      <div className="grid gap-3 text-[13px]">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-20 text-muted">Tipo</span>
          {TIPOS.map((x) => <button key={x.id} className={`${pilula} ${tipo === x.id ? pilulaLigada : ""}`} onClick={() => setTipo(x.id)}>{x.label}</button>)}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-20 text-muted">Dificuldade</span>
          {DIFS.map((x) => <button key={x.id} className={`${pilula} ${dif === x.id ? pilulaLigada : ""}`} onClick={() => setDif(x.id)}>{x.label}</button>)}
          <span className="ml-4 text-muted">Questões</span>
          <input type="number" min={1} max={60} value={n} onChange={(e) => setN(Math.max(1, Math.min(60, Number(e.target.value) || 1)))}
                 className={`${campo} w-20 py-1`} />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-20 text-muted">Tópicos</span>
          <button className={`${pilula} ${!topicos.length ? pilulaLigada : ""}`} onClick={() => setTopicos([])}>Todos (pelo peso)</button>
          {fracos.length > 0 && <button className={pilula} onClick={() => setTopicos(fracos)}>Só os fracos ({fracos.length})</button>}
          {estudo.programa.map((t) => (
            <button key={t.id} className={`${pilula} ${topicos.includes(t.id) ? pilulaLigada : ""}`} onClick={() => alterna(t.id)}>{t.titulo}</button>
          ))}
        </div>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <span className="text-[12px] text-muted">Cada questão nasce de um trecho das fontes e só entra se um conferente, lendo só aquele trecho, chegar na mesma resposta.</span>
        <button className={`${btnPrimary} ml-auto shrink-0`} disabled={ctx.rodando || !corpus}
                onClick={() => ctx.disparar("/prova", { n, tipo, dificuldade: dif, topicos })}>Gerar prova</button>
      </div>
    </section>
  );
}

function Fazer({ ctx, prova, voltar }: { ctx: Ctx; prova: Prova; voltar: () => void }) {
  const ultima = prova.tentativas[prova.tentativas.length - 1];
  const [refazendo, setRefazendo] = useState(!ultima);
  const [resp, setResp] = useState<Record<string, string>>({});
  const [inicio] = useState(() => Date.now());
  const [agora, setAgora] = useState(() => Date.now());
  useEffect(() => { const i = setInterval(() => setAgora(Date.now()), 1000); return () => clearInterval(i); }, []);
  useEffect(() => { if (ultima) setRefazendo(false); }, [prova.tentativas.length]);   // eslint-disable-line react-hooks/exhaustive-deps
  const nomes = Object.fromEntries(ctx.estudo.programa.map((t) => [t.id, t.titulo]));
  const corrigindo = ctx.rodando && ctx.estudo.run?.tipo === "correcao";
  const res = !refazendo ? ultima?.resultado : undefined;
  const [pagina, setPagina] = useState<number | null>(null);   // página do caderno aberta (prova real)

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <div className="mb-5 flex items-center gap-3">
        <button className={btnLeve} onClick={voltar}>← Provas</button>
        <div className="text-[13.5px] text-muted">{prova.id} · {prova.questoes.length} questões{prova.simulado ? ` · prova real · ${prova.titulo ?? ""}` : ""}</div>
        <div className="ml-auto flex items-center gap-2 text-[13px]">
          {res ? <span className="font-medium text-fg">Nota: {ultima.acertos}/{ultima.total} ({pct(ultima.acertos / ultima.total)})</span>
               : <span className="font-mono text-muted">{relogio((agora - inicio) / 1000)}</span>}
          {res && <button className={btn} onClick={() => { setResp({}); setRefazendo(true); }}>Refazer</button>}
        </div>
      </div>
      <div className="grid gap-4">
        {prova.questoes.map((q, i) => (
          <QuestaoCard key={q.id} n={i + 1} q={q} topico={nomes[q.topico]} valor={resp[q.id] ?? ""} res={res?.[q.id]}
                       onChange={(v) => setResp((r) => ({ ...r, [q.id]: v }))} abrirTrecho={ctx.abrirTrecho}
                       verPagina={prova.simulado ? setPagina : undefined} />
        ))}
      </div>
      {pagina !== null && prova.simulado && (
        <JanelaPdf conv={ctx.conv} sid={prova.simulado} pagina={pagina} onClose={() => setPagina(null)} />
      )}
      {!res && (
        <div className="mt-6 flex justify-end">
          <button className={btnPrimary} disabled={ctx.rodando || corrigindo}
                  onClick={() => ctx.disparar(`/prova/${prova.id}/entregar`, { respostas: resp, segundos: Math.round((Date.now() - inicio) / 1000) })}>
            {corrigindo ? "Corrigindo…" : `Entregar (${Object.values(resp).filter(Boolean).length}/${prova.questoes.length} respondidas)`}
          </button>
        </div>
      )}
    </div>
  );
}

function QuestaoCard(props: { n: number; q: Questao; topico?: string; valor: string; res?: { resposta: string; certa: boolean; nota?: number; comentario?: string };
                              onChange: (v: string) => void; abrirTrecho: (id: string, destaque?: string) => void;
                              verPagina?: (pagina: number) => void }) {
  const { q, res } = props;
  const opcoes = q.tipo === "certo_errado" ? [["C", "Certo"], ["E", "Errado"]] : q.alternativas.map((a, i) => [LETRAS[i], a]);
  return (
    <div className={`${card} ${res ? (res.certa ? "border-ok/50" : "border-err/50") : ""}`}>
      <div className="mb-2 flex items-center gap-2 text-[11.5px] text-faint">
        <span className="font-mono">{q.numero ? `Questão ${q.numero} do caderno` : `Questão ${props.n}`}</span>
        {props.topico && <span>· {props.topico}</span>}
        {q.assunto && <span>· {q.assunto}</span>}
        {props.verPagina && (
          <button className={`ml-auto ${q.figura ? "text-warn" : ""} hover:text-fg`} onClick={() => props.verPagina!(q.pagina || 1)}>
            {q.figura ? "Tem figura: ver no PDF" : "Ver no PDF"}{q.pagina ? ` (p. ${q.pagina})` : ""}
          </button>
        )}
      </div>
      <div className="mb-3 whitespace-pre-wrap text-[15px] leading-relaxed text-fg">{q.enunciado}</div>
      {q.tipo === "discursiva" ? (
        <textarea className={`${campo} min-h-28`} value={res ? res.resposta : props.valor} disabled={!!res}
                  onChange={(e) => props.onChange(e.target.value)} placeholder="Sua resposta" />
      ) : (
        <div className="grid gap-1.5">
          {opcoes.map(([l, txt]) => {
            const marcada = (res ? res.resposta : props.valor) === l;
            const certa = res && q.correta === l;
            return (
              <button key={l} disabled={!!res} onClick={() => props.onChange(l)}
                      className={`flex items-start gap-2.5 rounded-lg border px-3 py-2 text-left text-[14px] ${
                        certa ? "border-ok bg-ok/10" : marcada && res ? "border-err bg-err/10" : marcada ? "border-accent bg-accent-soft" : "border-line hover:border-focus"}`}>
                <span className="font-mono text-[12.5px] text-muted">{l})</span><span className="text-fg-2">{txt}</span>
              </button>
            );
          })}
        </div>
      )}
      {res && (
        <div className="mt-3 grid gap-2 rounded-lg bg-raised px-3 py-2.5 text-[13.5px]">
          <div className={res.certa ? "text-ok" : "text-err"}>
            {q.tipo === "discursiva" ? `Nota ${res.nota ?? 0}/10${res.comentario ? ` — ${res.comentario}` : ""}`
                                      : res.certa ? "Certa" : res.resposta ? `Errada — gabarito ${q.correta}` : `Em branco — gabarito ${q.correta}`}
          </div>
          {q.tipo === "discursiva" && q.resposta_esperada && <div className="text-fg-2"><b>Esperado:</b> {q.resposta_esperada}</div>}
          {q.explicacao && <div className="text-fg-2">{q.explicacao}</div>}
          {q.fonte && (
            <button className="text-left text-[12.5px] text-muted hover:text-fg" onClick={() => props.abrirTrecho(q.fonte!, q.citacao)}>
              <span className="font-mono text-accent-text">[{q.fonte}]</span> “{q.citacao}” — {q.fonte_titulo}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

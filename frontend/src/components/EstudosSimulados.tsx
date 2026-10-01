import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import type { EstudosBusca, EstudosPlacarSimulado, EstudosProjeto, EstudosQuestaoReal, EstudosSimulado } from "../types";
import { ArrowRight, Check, ExternalLink, Globe, Refresh, Search, Trash, X } from "./icons";
import { type Modelos, btn, btnPrimary, card, motorDe, numeros, rotulo } from "./estudosUi";

type Fonte = "pdf" | "material" | "colar";
const ETAPA: Record<string, string> = {
  lendo: "Recortando as questões", classificando: "Classificando por assunto", resolvendo: "Resolvendo às cegas",
  ranking: "Juntando o que mais cai", buscando: "Buscando na web", escolhendo: "Escolhendo os PDFs", baixando: "Baixando e conferindo",
};
const pct = (a: number, t: number) => (t ? Math.round((a / t) * 100) : 0);
const corPct = (p: number) => (p >= 70 ? "bg-ok" : p >= 50 ? "bg-amber-300" : "bg-red-400");

/** Aba Simulados: provas reais (achadas na web ou anexadas), a IA conferida com o gabarito oficial, o simulado
 *  como prova e o ranking do que mais cai entre todos os simulados do estudo. */
export default function Simulados(props: {
  conv: number;
  projeto: EstudosProjeto;
  modelos: Modelos;
  botaoModelos: React.ReactNode;
  painelModelos: React.ReactNode | null;
  onError: (e: string) => void;
  onRecarregar: () => Promise<void> | void;
  onIrProvas: () => void;
}) {
  const p = props.projeto;
  const [viva, setViva] = useState<EstudosSimulado | EstudosBusca | null>(null);
  const [pedido, setPedido] = useState("");
  const [form, setForm] = useState<{ material: number; fonte: Fonte; outro: number; texto: string } | null>(null);
  const [aberta, setAberta] = useState<number | null>(null);   // a análise mostrada em detalhe
  const [detalhe, setDetalhe] = useState<EstudosSimulado | null>(null);
  const [so, setSo] = useState<"divergencias" | "todas">("divergencias");
  const corte = useRef<AbortController | null>(null);
  const ouvindo = useRef(0);
  const aoErro = useRef(props.onError);
  aoErro.current = props.onError;
  const recarregar = useRef(props.onRecarregar);
  recarregar.current = props.onRecarregar;

  const ouvir = useCallback(async (path: string, init: RequestInit) => {
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    let ultimo: EstudosSimulado | EstudosBusca | null = null;
    try {
      await streamSSE(path, { ...init, signal: ctl.signal }, (ev) => {
        if (ctl.signal.aborted) return;
        if (ev.erro) aoErro.current(ev.erro);
        else { ultimo = ev; setViva(ev); }
      });
    } catch (e: any) {
      if (!ctl.signal.aborted) aoErro.current(e.message);
    }
    if (ctl.signal.aborted) return;
    await recarregar.current();
    const u = ultimo as EstudosSimulado | EstudosBusca | null;
    if (u?.status === "erro" && u.aviso) aoErro.current(u.aviso);
    if (u?.tipo === "simulado" && u.status === "pronto") setAberta(u.message_id);
    setViva(null);
  }, []);

  // aberta no meio de uma análise ou busca (outra aba, o celular): volta a acompanhar
  useEffect(() => {
    const rodando = [...p.simulados.filter((s) => s.status === "rodando").map((s) => s.message_id),
                     ...(p.busca?.status === "rodando" ? [p.busca.message_id] : [])][0];
    if (!rodando || ouvindo.current === rodando) return;
    ouvindo.current = rodando;
    ouvir(`/estudos/execucao/${rodando}/stream`, {}).finally(() => { ouvindo.current = 0; });
  }, [p.simulados, p.busca, ouvir]);
  useEffect(() => () => corte.current?.abort(), []);

  // a análise aberta: as questões vêm do detalhe (o estado leva só o resumo)
  useEffect(() => {
    if (aberta == null) { setDetalhe(null); return; }
    api.get<EstudosSimulado>(`/estudos/execucao/${aberta}`).then(setDetalhe).catch((e) => aoErro.current(e.message));
  }, [aberta, p.simulados]);

  const ocupado = !!viva || p.rodando != null;
  const ehGabarito = (m: { nome: string; gabarito?: boolean }) => !!m.gabarito || /^gabarito\b/i.test(m.nome);
  const provas = p.materiais.filter((m) => m.uso === "prova" && !ehGabarito(m));
  // gabarito anexado (a busca nomeia "Gabarito · ..."): já vem escolhido; senão, o do próprio PDF
  const novoForm = (material: number) => {
    // o de nome mais parecido, sem as palavras que todo nome tem ("pdf", "prova", "dia"…): "…_PV_…_CD12" casa com
    // "…_GB_…_CD12"; sem pelo menos 2 pedaços em comum, nenhum é sugerido (vale o do próprio PDF)
    const genericos = new Set(["pdf", "prova", "gabarito", "pv", "gb", "dia", "impresso", "caderno", "de", "da", "do", "fase"]);
    const pedacos = (t: string) => new Set(t.toLowerCase().split(/[^a-z0-9]+/).filter((x) => x.length > 1 && !genericos.has(x)));
    const nome = (p?.materiais ?? []).find((x) => x.id === material)?.nome ?? "";
    const meus = pedacos(nome);
    const gab = (p?.materiais ?? []).filter((x) => x.id !== material && /gabarito/i.test(x.nome))
      .map((x) => ({ x, n: [...pedacos(x.nome)].filter((t) => meus.has(t)).length }))
      .filter((g) => g.n >= 2).sort((a, b) => b.n - a.n)[0]?.x;
    return { material, fonte: (gab ? "material" : "pdf") as Fonte, outro: gab?.id ?? 0, texto: "" };
  };

  function buscar() {
    const t = pedido.trim();
    if (t.length < 3 || ocupado) return;
    ouvindo.current = -1;
    ouvir(`/estudos/${props.conv}/busca`, { method: "POST", body: JSON.stringify({ pedido: t, ...motorDe(props.modelos) }) })
      .finally(() => { if (ouvindo.current === -1) ouvindo.current = 0; });
  }

  function conferir() {
    if (!form || ocupado) return;
    ouvindo.current = -1;
    setAberta(null);
    ouvir(`/estudos/${props.conv}/simulado`, { method: "POST", body: JSON.stringify({
      material_id: form.material, gabarito: form.fonte === "colar" ? form.texto : "",
      gabarito_material: form.fonte === "material" ? form.outro : 0, ...motorDe(props.modelos) }) })
      .finally(() => { if (ouvindo.current === -1) ouvindo.current = 0; });
    setForm(null);
  }

  async function parar() {
    if (viva) await api.post(`/estudos/execucao/${viva.message_id}/cancelar`, {}).catch(() => {});
  }

  async function virarProva(id: number) {
    try {
      await api.post(`/estudos/simulado/${id}/prova`, {});
      await recarregar.current();
      props.onIrProvas();
    } catch (e: any) { aoErro.current(e.message); }
  }

  async function apagar(id: number) {
    try {
      await api.del(`/estudos/simulado/${id}`);
      if (aberta === id) setAberta(null);
      await recarregar.current();
    } catch (e: any) { aoErro.current(e.message); }
  }

  const busca = viva?.tipo === "busca" ? viva : p.busca;
  const analise = viva?.tipo === "simulado" ? viva : null;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-8 pt-6 pb-10">
      <div className="mx-auto flex max-w-[860px] flex-col gap-3">
        {props.painelModelos}

        {/* ---------------------------------------------------------------- busca na web */}
        <section className={card}>
          <div className="mb-2 flex items-center gap-2">
            <Globe className="size-4 text-muted" />
            <h2 className="text-[14px] font-semibold text-fg">Buscar provas reais na web</h2>
            <span className="ml-auto">{props.botaoModelos}</span>
          </div>
          <p className="mb-2.5 text-[12.5px] text-muted">
            O modelo procura o caderno de questões e o gabarito oficial, baixa só PDF e anexa o que confere como prova.
          </p>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); buscar(); }}>
            <input value={pedido} onChange={(e) => setPedido(e.target.value)} disabled={ocupado}
                   placeholder="Qual prova? Ex.: ENEM 2023 2º dia, FUVEST 2024 1ª fase, Banco do Brasil 2023"
                   className="min-w-0 flex-1 rounded-[9px] border border-line bg-raised px-3 py-1.5 text-sm text-fg placeholder:text-faint focus:border-focus focus:outline-none" />
            {viva?.tipo === "busca"
              ? <button type="button" className={btn} onClick={parar}><X className="size-3.5" /> Parar</button>
              : <button className={btnPrimary} disabled={pedido.trim().length < 3 || ocupado}><Search className="size-3.5" /> Buscar</button>}
          </form>
          {busca && <Busca b={busca} viva={viva?.tipo === "busca"} />}
        </section>

        {/* ---------------------------------------------------------------- gabarito oficial × IA */}
        <section className={card}>
          <h2 className="mb-1 text-[14px] font-semibold text-fg">Gabarito oficial × IA</h2>
          <p className="mb-2.5 text-[12.5px] text-muted">
            O modelo resolve as questões reais sem ver a resolução nem a resposta; depois o Forja compara com o gabarito
            oficial (o do próprio PDF, o de outro material ou o que você colar).
          </p>
          {!provas.length && <p className="text-xs text-faint">Anexe uma prova (ou busque acima) e marque o material como Prova.</p>}
          <div className="flex flex-col divide-y divide-line">
            {provas.map((m) => {
              const ultima = [...p.simulados].reverse().find((s) => s.material_id === m.id);
              const pl = ultima?.placar;
              return (
                <div key={m.id} className="flex flex-col gap-2 py-2.5 first:pt-0 last:pb-0">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="min-w-0 flex-1 truncate text-fg" title={m.nome}>{m.nome}</span>
                    {pl?.resolvidas ? (
                      <button className="font-mono text-muted hover:text-fg" onClick={() => setAberta(aberta === ultima!.message_id ? null : ultima!.message_id)}>
                        IA {pl.acertos}/{pl.resolvidas} · {pct(pl.acertos ?? 0, pl.resolvidas)}%
                      </button>
                    ) : ultima?.status === "erro" ? <span className="text-red-300">falhou</span> : null}
                    <button className={btn} disabled={ocupado} onClick={() => setForm(form?.material === m.id ? null : novoForm(m.id))}>
                      {ultima ? <><Refresh className="size-3.5" /> Conferir de novo</> : <>Conferir a IA</>}
                    </button>
                  </div>
                  {form?.material === m.id && (
                    <div className="flex flex-col gap-2 rounded-xl bg-raised/50 p-3 text-xs">
                      <span className={rotulo}>De onde vem o gabarito oficial</span>
                      <div className="flex flex-wrap gap-3" role="radiogroup">
                        {([["pdf", "Do próprio PDF (\"Resposta: C\")"], ["material", "De outro material"], ["colar", "Colar o gabarito"]] as const).map(([id, nome]) => (
                          <label key={id} className="flex items-center gap-1.5 text-fg">
                            <input type="radio" name="fonte" checked={form.fonte === id} onChange={() => setForm({ ...form, fonte: id })} /> {nome}
                          </label>
                        ))}
                      </div>
                      {form.fonte === "material" && (
                        <select value={form.outro} onChange={(e) => setForm({ ...form, outro: Number(e.target.value) })}
                                className="rounded-[9px] border border-line bg-surface px-2 py-1.5 text-fg">
                          <option value={0}>Escolha o material do gabarito</option>
                          {p.materiais.filter((x) => x.id !== m.id).map((x) => <option key={x.id} value={x.id}>{x.nome}</option>)}
                        </select>
                      )}
                      {form.fonte === "colar" && (
                        <textarea rows={3} value={form.texto} onChange={(e) => setForm({ ...form, texto: e.target.value })}
                                  placeholder="91 C  92 A  93 D … (número e letra; anulada = X)"
                                  className="rounded-[9px] border border-line bg-surface px-2.5 py-1.5 font-mono text-fg placeholder:text-faint focus:border-focus focus:outline-none" />
                      )}
                      <div className="flex justify-end gap-2">
                        <button className={btn} onClick={() => setForm(null)}>Cancelar</button>
                        <button className={btnPrimary} onClick={conferir}
                                disabled={ocupado || (form.fonte === "material" && !form.outro) || (form.fonte === "colar" && form.texto.trim().length < 3)}>
                          Conferir
                        </button>
                      </div>
                    </div>
                  )}
                  {analise?.material_id === m.id && <Andamento a={analise} onParar={parar} />}
                  {ultima && aberta === ultima.message_id && detalhe?.message_id === ultima.message_id && (
                    <Resultado d={detalhe} so={so} onSo={setSo} onProva={() => (detalhe.prova_id ? props.onIrProvas() : virarProva(detalhe.message_id))}
                               onApagar={() => apagar(detalhe.message_id)} />
                  )}
                </div>
              );
            })}
          </div>
        </section>

        {/* ---------------------------------------------------------------- o que mais cai */}
        {!!p.ranking?.itens.length && <Ranking r={p.ranking} />}
      </div>
    </div>
  );
}

function Busca({ b, viva }: { b: EstudosBusca; viva: boolean }) {
  const COR: Record<string, string> = { anexado: "text-ok", rejeitado: "text-red-300", baixando: "text-sky-300", fila: "text-faint" };
  return (
    <div className="mt-3 flex flex-col gap-1.5 border-t border-line pt-2.5 text-xs">
      <div className="flex items-center gap-2 text-muted">
        <span className="text-fg">{viva ? ETAPA[b.etapa] ?? b.etapa : `“${b.pedido}”`}</span>
        {viva && b.progresso && <span>· {b.progresso}</span>}
        {!viva && <span>· {b.anexados.length ? `${b.anexados.length} anexado${b.anexados.length > 1 ? "s" : ""}` : "nada anexado"}</span>}
        {numeros(b) && <span className="ml-auto font-mono text-faint">{numeros(b)}</span>}
      </div>
      {!!b.buscas.length && (
        <p className="text-faint">buscou: {b.buscas.map((x) => x.busca).join(" · ")}</p>
      )}
      {b.candidatos.map((c, i) => (
        <div key={`${i}:${c.url}`} className="flex items-center gap-2">
          <span className={`w-[66px] shrink-0 font-mono ${COR[c.status]}`}>{c.status}</span>
          <span className="w-16 shrink-0 text-muted">{c.tipo}</span>
          <span className="min-w-0 flex-1 truncate text-fg" title={c.url}>{c.exame || c.titulo}</span>
          {c.motivo && <span className="shrink-0 text-faint">{c.motivo}</span>}
          <a href={c.url} target="_blank" rel="noreferrer" className="shrink-0 text-faint hover:text-fg" title="Abrir o PDF original">
            <ExternalLink className="size-3.5" />
          </a>
        </div>
      ))}
      {!viva && b.aviso && <p className="text-amber-300">{b.aviso}</p>}
    </div>
  );
}

function Andamento({ a, onParar }: { a: EstudosSimulado; onParar: () => void }) {
  const pl = a.placar;
  return (
    <div className="flex items-center gap-3 rounded-xl bg-raised/50 px-3 py-2 text-xs">
      <span className="size-2 animate-pulse rounded-full bg-sky-300" />
      <span className="text-fg">{ETAPA[a.etapa] ?? a.etapa}</span>
      {a.progresso && <span className="text-muted">{a.progresso}</span>}
      {!!pl?.resolvidas && <span className="font-mono text-muted">IA {pl.acertos}/{pl.resolvidas}</span>}
      <span className="ml-auto font-mono text-faint">{numeros(a)}</span>
      <button className={btn} onClick={onParar}><X className="size-3.5" /> Parar</button>
    </div>
  );
}

function Parte({ nome, a, t, dica }: { nome: string; a: number; t: number; dica: string }) {
  if (!t) return null;
  return (
    <div className="flex items-center gap-3" title={dica}>
      <span className="w-[170px] shrink-0 truncate text-muted" title={nome}>{nome}</span>
      <div className="h-1.5 min-w-0 flex-1 rounded-full bg-raised"><div className={`h-full rounded-full ${corPct(pct(a, t))}`} style={{ width: `${pct(a, t)}%` }} /></div>
      <span className="w-28 shrink-0 whitespace-nowrap text-right font-mono text-faint">{a}/{t} · {pct(a, t)}%</span>
    </div>
  );
}

function Resultado({ d, so, onSo, onProva, onApagar }: { d: EstudosSimulado; so: "divergencias" | "todas"; onSo: (v: "divergencias" | "todas") => void;
                                                       onProva: () => void; onApagar: () => void }) {
  // conferência que parou no meio (erro, cancelada) não tem placar inteiro: tudo com valor padrão
  const pl: EstudosPlacarSimulado = { questoes: 0, com_gabarito: 0, resolvidas: 0, acertos: 0, em_branco: 0, por_area: [],
    so_texto: { resolvidas: 0, acertos: 0 }, figura_vista: { resolvidas: 0, acertos: 0 }, figura_faltou: { resolvidas: 0, acertos: 0 },
    ...(d.placar as Partial<EstudosPlacarSimulado>) };
  const pronta = d.status === "pronto";
  const qs = d.questoes.filter((q) => (so === "todas" ? true : q.certa === false));
  const brancas = d.questoes.filter((q) => !q.ia);
  return (
    <div className="flex flex-col gap-3 rounded-xl bg-raised/60 px-4 py-3.5 text-xs">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono text-[26px] font-semibold text-fg">{pct(pl.acertos, pl.resolvidas)}%</span>
        <span className="text-muted">a IA acertou <b className="text-fg">{pl.acertos}</b> de {pl.resolvidas} · {d.stats.escritor}</span>
        <span className="text-faint">gabarito: {d.gabarito || "do PDF"} · {pl.com_gabarito} de {pl.questoes} questões têm gabarito</span>
      </div>
      <div className="flex flex-col gap-1.5">
        <Parte nome="Só texto" a={pl.so_texto?.acertos ?? 0} t={pl.so_texto?.resolvidas ?? 0}
               dica="Questões em que tudo está no texto: é o que diz da capacidade do modelo nesta prova" />
        <Parte nome="Com a figura (vista)" a={pl.figura_vista?.acertos ?? 0} t={pl.figura_vista?.resolvidas ?? 0}
               dica="Dependiam de gráfico, tabela ou figura, e o modelo viu a imagem recortada do PDF" />
        <Parte nome="Faltou a figura" a={pl.figura_faltou?.acertos ?? 0} t={pl.figura_faltou?.resolvidas ?? 0}
               dica="Dependiam de figura que o modelo não viu (ele não enxerga, ou a figura não foi recortada): é quase chute" />
        {pl.por_area.map((a) => <Parte key={a.area} nome={a.area} a={a.acertos} t={a.total} dica={`Acerto da IA em ${a.area}`} />)}
        {!!pl.em_branco && <p className="text-faint">{pl.em_branco} em branco (o modelo disse que faltava dado, em vez de chutar)</p>}
      </div>
      <div className="flex items-center gap-2">
        <div className="flex rounded-full border border-line p-0.5" role="radiogroup" aria-label="Questões">
          {([["divergencias", `Divergências · ${d.questoes.filter((q) => q.certa === false).length}`], ["todas", `Todas · ${d.questoes.length}`]] as const).map(([id, nome]) => (
            <button key={id} role="radio" aria-checked={so === id} onClick={() => onSo(id)}
                    className={`rounded-full px-2.5 py-0.5 ${so === id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>{nome}</button>
          ))}
        </div>
        {!pronta && <span className="text-amber-300">conferência {d.status === "erro" ? "com erro" : "interrompida"}: confira de novo</span>}
        <button className={`${btnPrimary} ml-auto`} onClick={onProva} disabled={!pronta || !pl.com_gabarito}
                title="As questões reais com o gabarito oficial, na ordem e com as letras do caderno">
          {d.prova_id ? <>Abrir a prova <ArrowRight className="size-3.5" /></> : <>Fazer o simulado</>}
        </button>
        <button className={btn} onClick={onApagar} title="Apagar esta conferência (a prova que saiu dela fica)"><Trash className="size-3.5" /></button>
      </div>
      <div className="flex max-h-[28rem] flex-col divide-y divide-line overflow-y-auto">
        {qs.map((q) => <Linha key={q.numero} q={q} />)}
        {!qs.length && <p className="py-2 text-faint">{so === "divergencias" ? "Nenhuma divergência: a IA bateu com o gabarito em tudo que resolveu." : "—"}</p>}
      </div>
      {!!brancas.length && so === "divergencias" && (
        <p className="text-faint">Sem resposta da IA: {brancas.map((q) => q.numero).join(", ")}</p>
      )}
    </div>
  );
}

function Linha({ q }: { q: EstudosQuestaoReal }) {
  const [abre, setAbre] = useState(false);
  return (
    <button className="flex flex-col gap-1 py-2 text-left" onClick={() => setAbre((v) => !v)} aria-expanded={abre}>
      <span className="flex items-center gap-2">
        <span className="w-10 shrink-0 font-mono text-muted">{q.numero}</span>
        <span className={`w-28 shrink-0 font-mono ${q.certa === false ? "text-red-300" : q.certa ? "text-ok" : "text-faint"}`}>
          {q.ia ? `IA ${q.ia}` : "em branco"} · {q.oficial || "?"}
        </span>
        <span className="min-w-0 flex-1 truncate text-fg">{q.assunto || q.area}</span>
        {q.figura === "faltou" && <span className="shrink-0 rounded-md bg-amber-300/10 px-1.5 text-amber-300" title="Dependia de figura que o modelo não viu">sem a figura</span>}
        {q.figura === "vista" && <span className="shrink-0 rounded-md bg-raised px-1.5 text-muted">viu a figura</span>}
        {q.certa && <Check className="size-3.5 shrink-0 text-ok" />}
      </span>
      {abre && (
        <span className="ml-12 flex flex-col gap-1 text-muted">
          <span className="text-faint">{q.inicio}…</span>
          {q.conta && <span><b className="text-fg">IA:</b> {q.conta}</span>}
          {q.motivo && <span className="text-faint">{q.motivo}</span>}
        </span>
      )}
    </button>
  );
}

function Ranking({ r }: { r: NonNullable<EstudosProjeto["ranking"]> }) {
  const max = Math.max(...r.itens.map((i) => i.questoes), 1);
  return (
    <section className={card}>
      <h2 className="mb-1 text-[14px] font-semibold text-fg">O que mais cai</h2>
      <p className="mb-2.5 text-[12.5px] text-muted">
        {r.questoes} questões de {r.simulados} simulado{r.simulados > 1 ? "s" : ""} conferido{r.simulados > 1 ? "s" : ""}, por assunto
        {r.simulados > 1 ? " (primeiro o que aparece em mais simulados)" : ""}.
      </p>
      <div className="flex flex-col gap-1.5 text-xs">
        {r.itens.slice(0, 20).map((i) => (
          <div key={i.assunto} className="flex items-center gap-3">
            <span className="w-[200px] shrink-0 truncate text-fg" title={`${i.assunto} · ${i.area}`}>{i.assunto}</span>
            <div className="h-1.5 min-w-0 flex-1 rounded-full bg-raised"><div className="h-full rounded-full bg-accent" style={{ width: `${(i.questoes / max) * 100}%` }} /></div>
            <span className="w-28 shrink-0 text-right font-mono text-faint">
              {i.questoes} · {Math.round(i.fracao * 100)}%{r.simulados > 1 ? ` · ${i.simulados}/${r.simulados}` : ""}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

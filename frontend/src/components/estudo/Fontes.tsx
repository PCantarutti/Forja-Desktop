import { useEffect, useRef, useState } from "react";
import { api, enviarArquivo } from "../../api";
import { ExternalLink, Paperclip, X } from "../icons";
import type { Ctx } from "./EstudoView";
import type { Fonte, Topico } from "./tipos";
import { btn, btnLeve, btnPrimary, campo, card, pilula, pilulaLigada, rotulo } from "./ui";

export const NIVEIS = [
  { id: "concurso", label: "Concurso" }, { id: "vestibular", label: "Vestibular/ENEM" },
  { id: "faculdade", label: "Faculdade" }, { id: "iniciante", label: "Do zero" },
];
const PROFUNDIDADES = [
  { id: "rapida", label: "Rápida", hint: "2 buscas e até 3 páginas por tópico" },
  { id: "normal", label: "Normal", hint: "3 buscas e até 5 páginas por tópico" },
  { id: "funda", label: "Funda", hint: "4 buscas e até 8 páginas por tópico — demora" },
];

export type NovoBody = { pedido: string; nivel: string; cargo: string; edital_link: string; edital_texto?: string;
                         arquivo?: File | null; materiais?: File[] };

/** A entrada de um estudo novo: o que estudar, o nível e, opcional, o edital (arquivo ou link). */
function Entrada(props: { inicial?: Partial<NovoBody>; enviar: (b: NovoBody) => void; ocupado?: boolean; refazer?: boolean }) {
  const [pedido, setPedido] = useState(props.inicial?.pedido ?? "");
  const [nivel, setNivel] = useState(props.inicial?.nivel ?? "concurso");
  const [comEdital, setComEdital] = useState(false);
  const [link, setLink] = useState("");
  const [cargo, setCargo] = useState("");
  const [arquivo, setArquivo] = useState<File | null>(null);
  const [materiais, setMateriais] = useState<File[]>([]);
  const pode = !props.ocupado && (pedido.trim() || link.trim() || arquivo);
  return (
    <div className={card}>
      <div className={rotulo}>{props.refazer ? "Refazer o programa" : "O que você quer estudar?"}</div>
      <textarea className={`${campo} mt-2 min-h-24 resize-y text-[15px]`} value={pedido} onChange={(e) => setPedido(e.target.value)}
                placeholder="Ex.: Direito Constitucional — direitos e garantias fundamentais (art. 5º), para concurso de técnico do TRF." />
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[13px] text-muted">Nível</span>
        {NIVEIS.map((n) => (
          <button key={n.id} className={`${pilula} ${nivel === n.id ? pilulaLigada : ""}`} onClick={() => setNivel(n.id)}>{n.label}</button>
        ))}
        {!props.refazer && (
          <label className={`${pilula} ml-auto cursor-pointer ${materiais.length ? pilulaLigada : ""}`}
                 title="Apostila, lei, slides: o programa sai deles e eles entram nas fontes">
            {materiais.length ? `${materiais.length} arquivo(s)` : "Já tenho material"}
            <input type="file" multiple className="hidden" accept=".pdf,.docx,.pptx,.xlsx,.csv,.txt,.md,.html,.png,.jpg,.jpeg,.webp"
                   onChange={(e) => setMateriais(Array.from(e.target.files ?? []))} />
          </label>
        )}
        <button className={`${pilula} ${props.refazer ? "ml-auto" : ""} ${comEdital ? pilulaLigada : ""}`} onClick={() => setComEdital(!comEdital)}>
          Tenho o edital
        </button>
      </div>
      {comEdital && (
        <div className="mt-3 grid gap-2 rounded-lg border border-line p-3 text-[13px]">
          <div className="text-muted">Com edital, o programa é exatamente o conteúdo programático dele.</div>
          <div className="flex flex-wrap items-center gap-2">
            <label className={`${btnLeve} cursor-pointer`}>
              <Paperclip className="size-3.5" /> {arquivo ? arquivo.name : "PDF do edital"}
              <input type="file" accept=".pdf,.docx,.txt,.md,.html" className="hidden" onChange={(e) => setArquivo(e.target.files?.[0] ?? null)} />
            </label>
            <span className="text-faint">ou</span>
            <input className={`${campo} flex-1 py-1`} value={link} onChange={(e) => setLink(e.target.value)} placeholder="link da página do concurso ou do PDF" />
          </div>
          <input className={`${campo} py-1`} value={cargo} onChange={(e) => setCargo(e.target.value)} placeholder="Cargo (se o edital tem vários)" />
        </div>
      )}
      <div className="mt-3 flex justify-end">
        <button className={btnPrimary} disabled={!pode}
                onClick={() => props.enviar({ pedido, nivel, cargo, edital_link: comEdital ? link : "", arquivo: comEdital ? arquivo : null, materiais })}>
          Montar o programa
        </button>
      </div>
    </div>
  );
}

/** Antes do /programa: manda o material (o programa sai dele) e lê o edital enviado como arquivo. */
export async function comEdital(conv: number, b: NovoBody): Promise<object> {
  const { arquivo, materiais, ...resto } = b;
  for (const f of materiais ?? []) await enviarArquivo(`/estudo/${conv}/arquivo`, f);
  if (!arquivo) return resto;
  const r = await enviarArquivo<{ texto: string }>(`/estudo/${conv}/edital/arquivo`, arquivo);
  return { ...resto, edital_texto: r.texto };
}

export default function Fontes(props: { ctx: Ctx | null; novo?: (b: NovoBody) => void; carregando?: boolean }) {
  if (!props.ctx) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <p className="mb-5 text-[14px] leading-relaxed text-muted">
          A IA monta o programa, pesquisa cada tópico na web (e no que você mandar), guarda as páginas inteiras e escreve
          o material citando de onde tirou cada coisa. As provas só cobram o que está nessas fontes.
        </p>
        {props.carregando ? <div className="text-muted">Carregando…</div> : <Entrada enviar={(b) => props.novo?.(b)} />}
      </div>
    );
  }
  return <ComEstudo ctx={props.ctx} />;
}

function ComEstudo({ ctx }: { ctx: Ctx }) {
  const { estudo, conv } = ctx;
  const [programa, setPrograma] = useState<Topico[]>(estudo.programa);
  const [sujo, setSujo] = useState(false);
  const [profundidade, setProfundidade] = useState("normal");
  const [refazer, setRefazer] = useState(false);
  const [colar, setColar] = useState(false);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const arq = useRef<HTMLInputElement>(null);
  useEffect(() => { if (!sujo) setPrograma(estudo.programa); }, [estudo.programa]);   // eslint-disable-line react-hooks/exhaustive-deps

  const porTopico = (t: string) => estudo.fontes.filter((f) => f.status === "util" && (f.topicos.includes(t) || !f.topicos.length));
  const semFonte = estudo.programa.filter((t) => !porTopico(t.id).length).map((t) => t.id);
  const uteis = estudo.fontes.filter((f) => f.status === "util");

  function mudar(i: number, m: Partial<Topico>) {
    setPrograma((p) => p.map((t, k) => (k === i ? { ...t, ...m } : t)));
    setSujo(true);
  }
  async function salvar() {
    try {
      await api.patch(`/estudo/${conv}`, { programa });
      setSujo(false);
      await ctx.recarregar();
    } catch (e: any) { ctx.onError(e.message); }
  }
  async function enviar(files: FileList | null) {
    if (!files?.length) return;
    setEnviando(true);
    try {
      for (const f of Array.from(files)) await enviarArquivo(`/estudo/${conv}/arquivo`, f);
      await ctx.recarregar();
    } catch (e: any) { ctx.onError(e.message); }
    setEnviando(false);
  }
  async function colarTexto() {
    try {
      await api.post(`/estudo/${conv}/texto`, { texto });
      setTexto("");
      setColar(false);
      await ctx.recarregar();
    } catch (e: any) { ctx.onError(e.message); }
  }

  return (
    <div className="mx-auto grid max-w-5xl gap-5 px-6 py-6">
      {estudo.pedido && !refazer && (
        <div className="flex items-start gap-3 text-[13.5px]">
          <div className="min-w-0 flex-1 text-fg-2"><span className={rotulo}>Pedido</span><div className="mt-1 whitespace-pre-wrap">{estudo.pedido}</div>
            {estudo.edital && <div className="mt-1 text-[12px] text-muted">Programa tirado do edital{estudo.edital.cargo ? ` · cargo ${estudo.edital.cargo}` : ""}</div>}
          </div>
          <button className={btnLeve} disabled={ctx.rodando} onClick={() => setRefazer(true)}>Refazer programa</button>
        </div>
      )}
      {(refazer || !estudo.programa.length) && (
        <Entrada refazer={!!estudo.programa.length} inicial={{ pedido: estudo.pedido, nivel: estudo.nivel }} ocupado={ctx.rodando}
                 enviar={async (b) => {
                   try { await ctx.disparar("/programa", await comEdital(conv, b)); setRefazer(false); setSujo(false); }
                   catch (e: any) { ctx.onError(e.message); }
                 }} />
      )}

      {programa.length > 0 && (
        <section className={card}>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className={rotulo}>Programa · {programa.length} tópicos</div>
            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              {PROFUNDIDADES.map((p) => (
                <button key={p.id} title={p.hint} className={`${pilula} ${profundidade === p.id ? pilulaLigada : ""}`}
                        onClick={() => setProfundidade(p.id)}>{p.label}</button>
              ))}
              {semFonte.length > 0 && semFonte.length < estudo.programa.length && (
                <button className={btn} disabled={ctx.rodando || sujo}
                        onClick={() => ctx.disparar("/pesquisar", { topicos: semFonte, profundidade })}>
                  Pesquisar os {semFonte.length} sem fonte
                </button>
              )}
              <button className={btnPrimary} disabled={ctx.rodando || sujo}
                      onClick={() => ctx.disparar("/pesquisar", { topicos: [], profundidade })}>
                Pesquisar todos
              </button>
            </div>
          </div>
          <div className="grid gap-1.5">
            {programa.map((t, i) => (
              <div key={t.id} className="group grid grid-cols-[1fr_auto] items-start gap-2 rounded-lg border border-transparent px-2 py-1.5 hover:border-line">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <input className="min-w-0 flex-1 bg-transparent font-medium text-fg outline-none" value={t.titulo}
                           onChange={(e) => mudar(i, { titulo: e.target.value })} />
                    {t.materia && <span className="shrink-0 text-[11.5px] text-faint">{t.materia}</span>}
                  </div>
                  <input className="w-full bg-transparent text-[12.5px] text-muted outline-none placeholder:text-faint"
                         value={t.subtopicos.join("; ")} placeholder="subtópicos separados por ;"
                         onChange={(e) => mudar(i, { subtopicos: e.target.value.split(";").map((s) => s.trim()).filter(Boolean) })} />
                </div>
                <div className="flex items-center gap-2 text-[12px]">
                  <span className={porTopico(t.id).length ? "text-ok" : "text-warn"}>{porTopico(t.id).length} fonte{porTopico(t.id).length === 1 ? "" : "s"}</span>
                  <select className="rounded border border-line bg-bg px-1 py-0.5 text-fg-2" value={t.peso} title="Peso na prova"
                          onChange={(e) => mudar(i, { peso: Number(e.target.value) })}>
                    <option value={1}>peso 1</option><option value={2}>peso 2</option><option value={3}>peso 3</option>
                  </select>
                  <button className={btnLeve} disabled={ctx.rodando || sujo} title="Pesquisar só este tópico"
                          onClick={() => ctx.disparar("/pesquisar", { topicos: [t.id], profundidade })}>Pesquisar</button>
                  <button className="text-faint opacity-0 group-hover:opacity-100 hover:text-err" aria-label="Tirar tópico"
                          onClick={() => { setPrograma((p) => p.filter((_, k) => k !== i)); setSujo(true); }}><X className="size-3.5" /></button>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-2">
            <button className={btnLeve} onClick={() => { setPrograma((p) => [...p, { id: "", titulo: "Novo tópico", materia: p[p.length - 1]?.materia ?? "", subtopicos: [], peso: 1 }]); setSujo(true); }}>
              + Tópico
            </button>
            {sujo && <><button className={btnPrimary} onClick={salvar}>Salvar programa</button>
              <button className={btnLeve} onClick={() => { setPrograma(estudo.programa); setSujo(false); }}>Desfazer</button></>}
          </div>
        </section>
      )}

      <section className={card}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className={rotulo}>Corpus · {uteis.length} fontes em uso</div>
          <div className="ml-auto flex gap-1.5">
            <button className={btnLeve} disabled={enviando} onClick={() => arq.current?.click()}>
              <Paperclip className="size-3.5" /> {enviando ? "Lendo…" : "Mandar arquivo"}
            </button>
            <button className={btnLeve} onClick={() => setColar(!colar)}>Colar texto</button>
            <input ref={arq} type="file" multiple className="hidden" onChange={(e) => { enviar(e.target.files); e.target.value = ""; }}
                   accept=".pdf,.docx,.pptx,.xlsx,.csv,.txt,.md,.html,.png,.jpg,.jpeg,.webp" />
          </div>
        </div>
        {colar && (
          <div className="mb-3 grid gap-2">
            <textarea className={`${campo} min-h-32`} value={texto} onChange={(e) => setTexto(e.target.value)} placeholder="Cole aqui apostila, lei, anotações…" />
            <div className="flex justify-end"><button className={btnPrimary} disabled={texto.trim().length < 50} onClick={colarTexto}>Adicionar</button></div>
          </div>
        )}
        {!estudo.fontes.length && <div className="text-[13px] text-muted">Nenhuma fonte ainda. Pesquise os tópicos ou mande seus arquivos (apostila, lei, PDF de aula).</div>}
        <ListaFontes ctx={ctx} />
        {uteis.length > 0 && !Object.keys(estudo.material).length && (
          <div className="mt-4 flex justify-end">
            <button className={btnPrimary} disabled={ctx.rodando} onClick={() => ctx.disparar("/material", { topicos: [] })}>Escrever o material</button>
          </div>
        )}
      </section>
    </div>
  );
}

function ListaFontes({ ctx }: { ctx: Ctx }) {
  const { estudo, conv } = ctx;
  const [verDescartadas, setVerDescartadas] = useState(false);
  const [aberta, setAberta] = useState<{ f: Fonte; texto: string } | null>(null);
  const nomes = Object.fromEntries(estudo.programa.map((t) => [t.id, t.titulo]));
  const lista = estudo.fontes.filter((f) => verDescartadas || f.status === "util");
  const descartadas = estudo.fontes.length - estudo.fontes.filter((f) => f.status === "util").length;
  async function status(f: Fonte, s: Fonte["status"]) {
    try { await api.patch(`/estudo/${conv}/fontes/${f.id}`, { status: s }); await ctx.recarregar(); }
    catch (e: any) { ctx.onError(e.message); }
  }
  async function verTexto(f: Fonte) {
    try { setAberta({ f, texto: (await api.get<{ texto: string }>(`/estudo/${conv}/fontes/${f.id}/texto`)).texto }); }
    catch (e: any) { ctx.onError(e.message); }
  }
  async function apagar(f: Fonte) {
    try { await api.del(`/estudo/${conv}/fontes/${f.id}`); await ctx.recarregar(); }
    catch (e: any) { ctx.onError(e.message); }
  }
  return (
    <>
      <div className="grid gap-1">
        {lista.map((f) => (
          <div key={f.id} className={`group flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] hover:bg-raised ${f.status === "descartada" ? "opacity-60" : ""}`}>
            <span className="w-8 shrink-0 font-mono text-[11px] text-faint">{f.id}</span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="truncate text-fg">{f.titulo}</span>
                {f.url && <a href={f.url} target="_blank" rel="noreferrer" className="shrink-0 text-faint hover:text-fg"><ExternalLink className="size-3" /></a>}
              </div>
              <div className="truncate text-[11.5px] text-muted">
                {f.tipo === "web" ? new URL(f.url).hostname.replace(/^www\./, "") : f.tipo === "texto" ? "texto colado" : f.arquivo}
                {" · "}{Math.round(f.chars / 1000)} mil caracteres · {f.trechos} trechos
                {f.topicos.length ? ` · ${f.topicos.map((t) => nomes[t] ?? t).join(", ")}` : " · todos os tópicos"}
                {f.status === "descartada" && f.motivo ? ` · descartada: ${f.motivo}` : ""}
              </div>
            </div>
            <div className="flex shrink-0 gap-1 opacity-0 group-hover:opacity-100">
              <button className={btnLeve} onClick={() => verTexto(f)}>Ver texto</button>
              {f.status === "util" ? <button className={btnLeve} onClick={() => status(f, "descartada")}>Não usar</button>
                                   : <button className={btnLeve} onClick={() => status(f, "util")}>Usar</button>}
              <button className={btnLeve} onClick={() => apagar(f)} aria-label="Apagar"><X className="size-3" /></button>
            </div>
          </div>
        ))}
      </div>
      {descartadas > 0 && (
        <button className="mt-2 text-[12px] text-muted underline" onClick={() => setVerDescartadas(!verDescartadas)}>
          {verDescartadas ? "Esconder" : "Ver"} {descartadas} descartada(s) na pesquisa
        </button>
      )}
      {aberta && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" onClick={() => setAberta(null)}>
          <div className={`${card} max-h-[85vh] w-full max-w-3xl overflow-y-auto`} onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center gap-2">
              <div className="min-w-0 flex-1 truncate font-medium text-fg">{aberta.f.id} · {aberta.f.titulo}</div>
              <button className={btnLeve} onClick={() => setAberta(null)} aria-label="Fechar"><X className="size-3.5" /></button>
            </div>
            <div className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-fg-2">{aberta.texto}</div>
          </div>
        </div>
      )}
    </>
  );
}

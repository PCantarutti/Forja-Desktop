import { useEffect, useRef, useState } from "react";
import { api, enviarArquivo, streamSSE } from "../api";
import type { EstudosEdital, EstudosEditalItem, EstudosProjeto } from "../types";
import { ArrowLeft, Check, Paperclip, X } from "./icons";
import { type Modelos, btn, btnPrimary, card, motorDe, numeros, rotulo } from "./estudosUi";

/** Ler o edital: colar ou enviar o arquivo → o modelo propõe matérias, peso e tópicos → o aluno confere e aplica. */
export function LerEdital(props: {
  conv: number; projeto: EstudosProjeto; modelos: Modelos; botaoModelos: React.ReactNode; painelModelos: React.ReactNode | null;
  onFechar: () => void; onFeito: (comPlano: boolean) => void; onError: (e: string) => void;
}) {
  const [texto, setTexto] = useState("");
  const [link, setLink] = useState("");
  // o plano de estudos sai junto: até a data da prova, pelos tópicos do edital e o peso de cada matéria
  const [plano, setPlano] = useState({ ligado: true, data: "", minutos: 60 });
  const [cargo, setCargo] = useState("");
  const [enviando, setEnviando] = useState(false);
  // a última leitura do objetivo volta aberta (a proposta não se perde ao trocar de aba)
  const [ex, setEx] = useState<EstudosEdital | null>(props.projeto.edital ?? null);
  const [itens, setItens] = useState<(EstudosEditalItem & { marcada: boolean })[]>([]);
  const arquivo = useRef<HTMLInputElement>(null);
  const corte = useRef<AbortController | null>(null);
  const lendo = ex?.status === "rodando";
  useEffect(() => () => corte.current?.abort(), []);
  useEffect(() => {
    if (ex?.status === "pronto" || (ex && !lendo)) setItens((ex?.proposta ?? []).map((x) => ({ ...x, marcada: true })));
    if (ex?.data_prova) setPlano((p) => ({ ...p, data: p.data || ex.data_prova! }));
  }, [ex?.message_id, ex?.status]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function enviar(f: File) {
    setEnviando(true);
    try {
      const r = await enviarArquivo<{ texto: string; chars: number }>(`/estudos/${props.conv}/edital/arquivo`, f);
      setTexto(r.texto);
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setEnviando(false);
    }
  }

  async function ler() {
    const porLink = /^https?:\/\//i.test(link.trim());
    if (lendo || (!porLink && texto.trim().length < 200)) return props.onError("Cole o link do edital, o texto dele ou envie o arquivo.");
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    try {
      await streamSSE(`/estudos/${props.conv}/edital`, { method: "POST", signal: ctl.signal,
        body: JSON.stringify({ texto: porLink ? "" : texto, link: porLink ? link.trim() : "", cargo, ...motorDe(props.modelos) }) }, (ev) => {
        if (ctl.signal.aborted) return;
        if (ev.erro) props.onError(ev.erro);
        else setEx(ev);
      });
    } catch (e: any) {
      if (!ctl.signal.aborted) props.onError(e.message);
    }
  }

  async function aplicar() {
    const marcadas = itens.filter((x) => x.marcada && x.nome.trim());
    if (!marcadas.length) return props.onError("Marque pelo menos uma matéria.");
    try {
      const cronograma = plano.ligado && plano.data ? { data: plano.data, minutos: plano.minutos } : null;
      await api.post(`/estudos/${props.conv}/edital/aplicar`, { materias: marcadas.map(({ nome, peso, topicos }) => ({ nome, peso, topicos })), cronograma });
      props.onFeito(!!cronograma);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  const muda = (i: number, x: Partial<EstudosEditalItem & { marcada: boolean }>) => setItens((v) => v.map((y, j) => (j === i ? { ...y, ...x } : y)));
  const mostrar = !!itens.length && !lendo;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        <div className="flex items-center gap-2">
          <button className={btn} onClick={props.onFechar}><ArrowLeft className="size-3.5" /> Voltar</button>
          <p className="text-sm font-medium text-fg">Ler o edital</p>
        </div>

        {!mostrar && (
          <div className={`${card} flex flex-col gap-2 text-xs`}>
            <p className="text-muted">
              Cole o link da página do concurso (ou do PDF do edital), o texto, ou envie o arquivo. A IA procura o quadro de provas
              (quantas questões cada disciplina tem) e o conteúdo programático do seu cargo, e propõe as matérias com peso e tópicos e
              o plano de estudos até a prova. Você confere antes de criar; o edital não vira material de estudo.
            </p>
            <input value={link} onChange={(e) => setLink(e.target.value)} disabled={lendo} inputMode="url"
                   placeholder="https://… a página do concurso na banca, ou o PDF do edital"
                   className="w-full rounded-[9px] border border-line bg-surface px-2.5 py-1.5 text-fg focus:border-focus focus:outline-none" />
            <textarea value={texto} onChange={(e) => setTexto(e.target.value)} rows={link.trim() ? 2 : 8} disabled={lendo || !!link.trim()}
                      placeholder="ANEXO II — CONTEÚDO PROGRAMÁTICO&#10;LÍNGUA PORTUGUESA: 1 Compreensão de textos. 2 Crase…"
                      className="w-full resize-y rounded-[9px] border border-line bg-surface p-2.5 font-mono text-[12px] text-fg focus:border-focus focus:outline-none" />
            <div className="flex flex-wrap items-center gap-2">
              <input ref={arquivo} type="file" hidden accept=".pdf,.docx,.txt,.md,.html,.htm"
                     onChange={(e) => { const f = e.target.files?.[0]; if (f) enviar(f); e.target.value = ""; }} />
              <button className={btn} onClick={() => arquivo.current?.click()} disabled={enviando || lendo}>
                <Paperclip className="size-3.5" /> {enviando ? "Lendo o arquivo…" : "Arquivo do edital"}
              </button>
              {!!texto && <span className="text-faint">{texto.length.toLocaleString("pt-BR")} caracteres</span>}
              <input value={cargo} onChange={(e) => setCargo(e.target.value)} placeholder="Cargo (se o edital tem vários)" maxLength={120}
                     className="min-w-0 flex-1 rounded-[9px] border border-line bg-surface px-2.5 py-1.5 text-fg focus:border-focus focus:outline-none" />
            </div>
            {props.painelModelos}
            <div className="flex items-center gap-2">
              {props.botaoModelos}
              <button className={`${btnPrimary} ml-auto`} onClick={ler} disabled={lendo || (!/^https?:\/\//i.test(link.trim()) && texto.trim().length < 200)}>
                {lendo ? "Lendo…" : "Ler o edital"}
              </button>
            </div>
            {lendo && ex && (
              <p className="font-mono text-[11px] text-sky-300">
                {ex.progresso || "começando…"}{numeros(ex) ? ` · ${numeros(ex)}` : ""}{ex.proposta.length ? ` · ${ex.proposta.length} matérias até agora` : ""}
              </p>
            )}
            {!lendo && ex?.aviso && <p className="text-amber-300">{ex.aviso}</p>}
          </div>
        )}

        {mostrar && (
          <div className={`${card} flex flex-col gap-1 text-xs`}>
            <div className="mb-1 flex items-center gap-2">
              <p className={rotulo}>Matérias do edital{ex?.cargo ? ` · ${ex.cargo}` : ""}</p>
              <span className="ml-auto text-faint">peso = questões × peso no quadro de provas</span>
            </div>
            {!!ex?.anexos?.length && (
              <p className="mb-1 text-faint" title={ex.anexos.map((a) => a.nome).join("\n")}>
                Lido de {ex.anexos.filter((a) => a.chars).length} PDF{ex.anexos.filter((a) => a.chars).length === 1 ? "" : "s"} do concurso:{" "}
                {ex.anexos.filter((a) => a.chars).map((a) => a.nome.split(" · ")[0]).join(" · ")}
              </p>
            )}
            {itens.map((x, i) => (
              <details key={i} className="group rounded-lg border border-line px-2.5 py-1.5 open:bg-raised/40">
                <summary className="flex cursor-pointer list-none items-center gap-2">
                  <input type="checkbox" checked={x.marcada} onChange={(e) => muda(i, { marcada: e.target.checked })} onClick={(e) => e.stopPropagation()}
                         aria-label={`Criar ${x.nome}`} />
                  <input value={x.nome} onChange={(e) => muda(i, { nome: e.target.value })} onClick={(e) => e.preventDefault()} maxLength={60}
                         aria-label="Nome da matéria" className="min-w-0 flex-1 bg-transparent text-[13px] text-fg focus:outline-none" />
                  <span className={`rounded-full px-2 py-0.5 text-[11px] ${x.existe ? "border border-line text-faint" : "bg-accent/15 text-accent"}`}>
                    {x.existe ? "já existe: atualiza" : "nova"}
                  </span>
                  <label className="flex items-center gap-1 text-faint" onClick={(e) => e.preventDefault()}>
                    peso
                    <input type="number" min={1} max={100} value={x.peso} onChange={(e) => muda(i, { peso: Math.max(1, Math.min(100, Number(e.target.value) || 1)) })}
                           className="w-12 rounded-md border border-line bg-surface px-1 py-0.5 text-right font-mono text-fg" />
                  </label>
                  <span className="w-16 shrink-0 text-right text-faint">{x.topicos.length} tópicos</span>
                </summary>
                {!!x.topicos.length && (
                  <ol className="mt-1.5 list-decimal space-y-0.5 pl-9 text-muted">{x.topicos.map((t) => <li key={t}>{t}</li>)}</ol>
                )}
              </details>
            ))}
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-line px-2.5 py-2">
              <input type="checkbox" checked={plano.ligado} onChange={(e) => setPlano((p) => ({ ...p, ligado: e.target.checked }))} aria-label="Montar o plano de estudos" />
              <span className="text-fg">Montar o plano de estudos até a prova</span>
              <input type="date" value={plano.data} onChange={(e) => setPlano((p) => ({ ...p, data: e.target.value }))} disabled={!plano.ligado}
                     className="rounded-md border border-line bg-surface px-1.5 py-0.5 text-fg" aria-label="Data da prova" />
              <label className="flex items-center gap-1 text-faint">
                <input type="number" min={15} max={600} step={15} value={plano.minutos} disabled={!plano.ligado}
                       onChange={(e) => setPlano((p) => ({ ...p, minutos: Math.max(15, Math.min(600, Number(e.target.value) || 60)) }))}
                       className="w-14 rounded-md border border-line bg-surface px-1 py-0.5 text-right font-mono text-fg" /> min por dia
              </label>
              <span className="w-full text-faint">
                {ex?.data_prova ? "A data veio do edital. " : "O edital não disse a data: escolha. "}
                Um tópico por dia, as matérias na proporção do peso, revisão diária e um simulado por semana; vai melhorando conforme as provas.
              </span>
            </div>
            <div className="mt-2 flex items-center gap-2">
              <button className={btn} onClick={() => { setItens([]); setEx(null); }}>Ler outro edital</button>
              <button className={`${btnPrimary} ml-auto`} onClick={aplicar}>
                <Check className="size-3.5" /> Criar {itens.filter((x) => x.marcada).length} matérias{plano.ligado && plano.data ? " e o plano" : ""}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

type Conversa = { id: number; title: string; kind: string; updated_at: string; archived: boolean };

/** Trazer um estudo para dentro deste objetivo: as matérias dele viram matérias daqui, com todo o progresso. */
export function TrazerEstudo(props: { conv: number; onFechar: () => void; onFeito: () => void; onError: (e: string) => void }) {
  const [lista, setLista] = useState<Conversa[] | null>(null);
  const [escolhido, setEscolhido] = useState<Conversa | null>(null);
  const [indo, setIndo] = useState(false);
  useEffect(() => {
    api.get<Conversa[]>("/conversations").then((cs) => setLista(cs.filter((c) => c.kind === "estudos" && c.id !== props.conv)))
      .catch((e) => props.onError(e.message));
  }, [props.conv]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function trazer() {
    if (!escolhido) return;
    setIndo(true);
    try {
      await api.post(`/estudos/${props.conv}/juntar`, { de: escolhido.id });
      props.onFeito();
    } catch (e: any) {
      props.onError(e.message);
      setIndo(false);
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      <div className="mx-auto flex max-w-2xl flex-col gap-3">
        <div className="flex items-center gap-2">
          <button className={btn} onClick={props.onFechar}><ArrowLeft className="size-3.5" /> Voltar</button>
          <p className="text-sm font-medium text-fg">Trazer um estudo para este objetivo</p>
        </div>
        <p className="text-xs text-muted">
          As matérias do estudo escolhido viram matérias daqui (mesmo nome junta na mesma) com resumos, provas, notas, cartões,
          material e revisão. O estudo some da lista: tudo passa a morar neste objetivo.
        </p>
        {!lista && <p className="text-xs text-faint">Lendo os estudos…</p>}
        {lista && !lista.length && <p className={`${card} text-xs text-faint`}>Não há outro estudo para trazer.</p>}
        {!!lista?.length && (
          <div className={`${card} flex flex-col gap-0.5 p-1.5`}>
            {lista.map((c) => (
              <button key={c.id} onClick={() => setEscolhido(c)} aria-pressed={escolhido?.id === c.id}
                      className={`flex items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] ${escolhido?.id === c.id ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"}`}>
                <span className="min-w-0 flex-1 truncate">{c.title}</span>
                {c.archived && <span className="text-[11px] text-faint">arquivado</span>}
                <span className="text-[11px] text-faint">{new Date(c.updated_at).toLocaleDateString("pt-BR")}</span>
              </button>
            ))}
          </div>
        )}
        {escolhido && (
          <div className={`${card} flex flex-wrap items-center gap-2 text-xs`}>
            <span className="min-w-0 flex-1 text-amber-200">Trazer "{escolhido.title}" para cá? Ele sai da lista de estudos.</span>
            <button className={btn} onClick={() => setEscolhido(null)} disabled={indo}><X className="size-3.5" /> Cancelar</button>
            <button className={btnPrimary} onClick={trazer} disabled={indo}>{indo ? "Trazendo…" : "Trazer"}</button>
          </div>
        )}
      </div>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api, auth, streamSSE } from "../api";
import type { EstudosFlashcards, EstudosItemRevisao, EstudosProjeto, PesquisaFonte } from "../types";
import { ArrowRight, Check, Download, Lampada, Refresh, Trash, X } from "./icons";
import { Markdown } from "./MessageView";
import Sinapse from "./Sinapse";
import { AguardandoClaude, FiguraQuestao } from "./EstudosProva";
import { matematica } from "./estudosTexto";
import { type Modelos, btn, btnPrimary, card, motorDe, numeros, rotulo } from "./estudosUi";

const LETRAS = "ABCDE";
const DA_PARTE: Record<string, PesquisaFonte["status"]> = { fila: "fila", gerando: "lendo", ok: "util", erro: "erro" };
const quando = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });

function SinapseCartoes({ f }: { f: EstudosFlashcards }) {
  const fontes = f.partes.map((p): PesquisaFonte => ({ id: p.id, rodada: 1, titulo: p.topicos.join(", "),
    status: DA_PARTE[p.status] ?? "fila", url: "", dominio: "", erro: "", resumo: "", trecho: "" }));
  return (
    <Sinapse estado={{ status: "rodando", fontes, rodadas: [], pergunta: "Flashcards", fase: "pronto", rodada: 0, rodadas_total: 0, stats: f.stats }}
             ramos={["Cartões"]} fase="escrevendo os cartões"
             meta={<><span className="sin-sep">·</span><span><b>{f.cartoes.length}</b> cartões</span>
               {numeros(f) && <><span className="sin-sep">·</span><span>{numeros(f)}</span></>}</>} />
  );
}

/** Uma rodada de revisão: um item por vez; erro de prova se responde de novo, cartão se vira. */
function Sessao({ conv, itens, onFim, onError }: { conv: number; itens: EstudosItemRevisao[]; onFim: () => void;
                                                 onError: (e: string) => void }) {
  const [i, setI] = useState(0);
  const [resposta, setResposta] = useState<number | boolean | null>(null);
  const [virado, setVirado] = useState(false);
  const [placar, setPlacar] = useState({ certas: 0, feitas: 0 });
  const item = itens[i];
  const q = item?.tipo === "erro" ? item.questao : null;
  const fechada = q && q.tipo !== "disc";
  const acertouFechada = fechada && resposta !== null ? resposta === q!.correta : null;

  const proximo = useCallback(async (acertou: boolean, tirar = false) => {
    if (!item) return;
    try {
      await api.post(`/estudos/${conv}/revisao`, { chave: item.chave, acertou, tirar });
    } catch (e: any) {
      onError(e.message);
    }
    if (!tirar) setPlacar((p) => ({ certas: p.certas + (acertou ? 1 : 0), feitas: p.feitas + 1 }));
    setResposta(null);
    setVirado(false);
    setI((x) => x + 1);
  }, [item, conv, onError]);

  // Teclado: espaço vira o cartão (ou mostra a resposta); 1 = errei, 2 = acertei; A–E / V F respondem.
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("textarea, input") || !item || document.body.dataset.figuraAberta) return;
      const k = e.key.toLowerCase();
      if (fechada && resposta === null) {
        if (q!.tipo === "me" && LETRAS.slice(0, q!.alternativas?.length).toLowerCase().includes(k) && k.length === 1) setResposta(LETRAS.toLowerCase().indexOf(k));
        if (q!.tipo === "vf" && (k === "v" || k === "f")) setResposta(k === "v");
        return;
      }
      if (fechada) { if (k === "enter" || k === " ") { e.preventDefault(); proximo(!!acertouFechada); } return; }
      if (!virado && (k === " " || k === "enter")) { e.preventDefault(); setVirado(true); }
      else if (virado && k === "1") proximo(false);
      else if (virado && k === "2") proximo(true);
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  });

  if (!item) {
    return (
      <div className={`${card} flex flex-wrap items-center gap-3`}>
        <Check className="size-5 text-ok" />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-fg">Revisão feita: {placar.certas} de {placar.feitas} certas.</p>
          <p className="text-xs text-muted">O que você errou volta amanhã; o que acertou, só daqui a alguns dias.</p>
        </div>
        <button className={btnPrimary} onClick={onFim}>Fechar</button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 text-xs text-muted">
        <span className="font-mono">{i + 1} / {itens.length}</span>
        <div className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-raised">
          <div className="h-full rounded-full bg-accent" style={{ width: `${(i / itens.length) * 100}%` }} />
        </div>
        <span>{item.tipo === "erro" ? `erro da ${item.prova}` : "cartão"} · caixa {item.caixa}</span>
        <button className="text-faint hover:text-fg" title="Parar a revisão" onClick={onFim}><X className="size-4" /></button>
      </div>

      <div className={`${card} px-6 py-5`}>
        {item.topico && <p className={`${rotulo} mb-2`}>{item.topico}</p>}
        {item.tipo === "cartao" ? (
          <>
            <div className="text-[15px] [&_.md]:text-[15px]"><Markdown text={matematica(item.frente)} math /></div>
            {virado && <div className="mt-4 border-t border-line pt-4 [&_.md]:text-[14px]"><Markdown text={matematica(item.verso)} math /></div>}
          </>
        ) : (
          <>
            <Markdown text={matematica(q!.enunciado)} math />
            <FiguraQuestao conv={conv} f={q!.figura} />
            {q!.tipo === "me" && (
              <div className="mt-3 flex flex-col gap-1.5">
                {q!.alternativas?.map((a, k) => {
                  const certa = resposta !== null && k === q!.correta, minha = resposta === k;
                  return (
                    <button key={k} disabled={resposta !== null} onClick={() => setResposta(k)}
                            className={`flex items-start gap-3 rounded-xl border px-3.5 py-2 text-left ${
                              certa ? "border-ok/55 bg-ok/[.06]" : minha ? "border-red-400/50 bg-red-400/[.05]"
                                : resposta === null ? "border-line hover:border-focus hover:bg-raised" : "border-line"}`}>
                      <span className={`grid size-6 shrink-0 place-items-center rounded-full border font-mono text-xs ${
                        certa ? "border-ok text-ok" : minha ? "border-red-300 text-red-300" : "border-line-strong text-muted"}`}>{LETRAS[k]}</span>
                      <span className="min-w-0 flex-1 [&_.md]:text-[14px] [&_p]:my-0"><Markdown text={matematica(a)} math /></span>
                    </button>
                  );
                })}
              </div>
            )}
            {q!.tipo === "vf" && (
              <div className="mt-3 grid grid-cols-2 gap-2">
                {([[true, "Verdadeiro"], [false, "Falso"]] as const).map(([v, nome]) => (
                  <button key={nome} disabled={resposta !== null} onClick={() => setResposta(v)}
                          className={`rounded-xl border px-3.5 py-2.5 text-sm ${
                            resposta !== null && v === q!.correta ? "border-ok/55 bg-ok/[.06] text-ok"
                              : resposta === v ? "border-red-400/50 bg-red-400/[.05] text-red-300" : "border-line text-fg hover:border-focus"}`}>{nome}</button>
                ))}
              </div>
            )}
            {q!.tipo === "disc" && !virado && <p className="mt-3 text-xs text-faint">Responda de cabeça (ou no papel) e depois veja a resposta esperada.</p>}
            {(resposta !== null || (q!.tipo === "disc" && virado)) && (
              <div className="mt-4 rounded-xl bg-raised/50 px-3.5 py-2.5 [&_.md]:text-[14px]">
                <p className={`${rotulo} mb-1`}>{acertouFechada === true ? "Certa" : acertouFechada === false ? "Errada de novo" : "Resposta esperada"}</p>
                <Markdown text={matematica(q!.tipo === "disc" ? q!.resposta_modelo ?? "" : q!.explicacao ?? "")} math />
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {fechada ? (
          resposta !== null && <button className={btnPrimary} onClick={() => proximo(!!acertouFechada)}>Próximo <ArrowRight className="size-3.5" /></button>
        ) : !virado ? (
          <button className={btnPrimary} onClick={() => setVirado(true)}>{item.tipo === "cartao" ? "Virar o cartão" : "Mostrar a resposta"}</button>
        ) : (
          <>
            <button className={btn} onClick={() => proximo(false)}><X className="size-3.5" /> Errei <span className="font-mono text-faint">1</span></button>
            <button className={btnPrimary} onClick={() => proximo(true)}><Check className="size-3.5" /> Acertei <span className="font-mono opacity-60">2</span></button>
          </>
        )}
        <span className="ml-auto hidden text-faint md:inline">
          {fechada ? "A–E ou V/F respondem · Enter segue" : "espaço vira · 1 errei · 2 acertei"}
        </span>
        {item.tipo === "erro" && (
          <button className="text-faint hover:text-fg" onClick={() => proximo(false, true)}
                  title="Questão com defeito, ou que você já domina: não volta mais para a revisão">Tirar do caderno</button>
        )}
      </div>
    </div>
  );
}

export default function Revisao(props: {
  conv: number;
  projeto: EstudosProjeto;
  modelos: Modelos;
  botaoModelos: React.ReactNode;
  painelModelos: React.ReactNode | null;
  onImersao?: (v: boolean) => void;   // na sessão de revisão a tela de fora esconde a fila de abas
  onError: (e: string) => void;
  onRecarregar: () => Promise<void> | void;
}) {
  const painel = props.projeto.revisao;
  const [sessao, setSessao] = useState<EstudosItemRevisao[] | null>(null);
  const [quantos, setQuantos] = useState(20);
  const [viva, setViva] = useState<EstudosFlashcards | null>(null);
  // a lista já abre no que tem coisa: um toggle fechado em cima de um espaço vazio parecia quebrado
  const [lista, setLista] = useState<"" | "erros" | "cartoes">(() => {
    const it = props.projeto.revisao.itens;
    return it.some((x) => x.tipo === "erro") ? "erros" : it.some((x) => x.tipo === "cartao") ? "cartoes" : "";
  });
  const corte = useRef<AbortController | null>(null);
  const ouvindo = useRef(0);
  const aoErro = useRef(props.onError);
  aoErro.current = props.onError;
  const recarregar = useRef(props.onRecarregar);
  recarregar.current = props.onRecarregar;

  const ativos = painel.itens.filter((x) => !x.dominada);
  const vencem = ativos.filter((x) => x.vence);
  const erros = painel.itens.filter((x) => x.tipo === "erro");
  const cartoes = painel.itens.filter((x) => x.tipo === "cartao");
  const gerando = viva && (viva.status === "rodando" || viva.status === "aguardando") ? viva : null;

  const ouvir = useCallback(async (path: string, init: RequestInit) => {
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    let ultimo: EstudosFlashcards | null = null;
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
    const u = ultimo as EstudosFlashcards | null;
    if (u?.aviso && u.status !== "pronto") aoErro.current(u.aviso);
    if (u?.status !== "aguardando") setViva(null);
  }, []);

  // Aberta no meio de uma geração (outra aba, o celular): volta a acompanhar; pedido ao Claude é lido uma vez.
  useEffect(() => {
    const g = [...painel.geracoes].reverse().find((x) => x.status === "rodando" || x.status === "aguardando");
    if (!g) { if (viva && viva.status !== "rodando") setViva(null); return; }
    if (ouvindo.current === g.message_id) return;
    ouvindo.current = g.message_id;
    if (g.status === "rodando") ouvir(`/estudos/execucao/${g.message_id}/stream`, {}).finally(() => { ouvindo.current = 0; });
    else api.get<EstudosFlashcards>(`/estudos/execucao/${g.message_id}`).then(setViva).catch(() => {}).finally(() => { ouvindo.current = 0; });
  }, [painel.geracoes]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => corte.current?.abort(), []);
  useEffect(() => { props.onImersao?.(!!sessao); return () => props.onImersao?.(false); }, [sessao]);   // eslint-disable-line react-hooks/exhaustive-deps

  function gerar() {
    if (gerando) return;
    ouvindo.current = -1;
    ouvir(`/estudos/${props.conv}/flashcards`, { method: "POST", body: JSON.stringify({ quantos, ...motorDe(props.modelos) }) })
      .finally(() => { if (ouvindo.current === -1) ouvindo.current = 0; });
  }

  async function parar() {
    if (viva) await api.post(`/estudos/execucao/${viva.message_id}/cancelar`, {}).catch(() => {});
    await recarregar.current();
  }

  async function apagar(id: string) {
    try {
      await api.del(`/estudos/${props.conv}/flashcards/${id}`);
      await recarregar.current();
    } catch (e: any) {
      aoErro.current(e.message);
    }
  }

  async function exportar() {
    try {
      const r = await fetch(`/api/estudos/${props.conv}/flashcards.csv`,
                            { headers: auth() });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `${props.projeto.titulo.replace(/[\\/:*?"<>|]+/g, "").slice(0, 60) || "flashcards"} - flashcards.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      aoErro.current(e.message);
    }
  }

  function comecar(todos: boolean) {
    // vencidos primeiro (os de caixa mais baixa na frente); "mesmo assim" pega todos os não dominados
    const fila = (todos ? ativos : vencem).slice().sort((a, b) => a.caixa - b.caixa || a.proxima.localeCompare(b.proxima));
    setSessao(fila);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-3">
          {!sessao && props.painelModelos}

          {sessao ? (
            <Sessao conv={props.conv} itens={sessao} onError={props.onError}
                    onFim={() => { setSessao(null); recarregar.current(); }} />
          ) : (
            <>
              <div className={`${card} flex flex-wrap items-center gap-x-6 gap-y-3`}>
                <div>
                  <p className={rotulo}>para hoje</p>
                  <p className="mt-1 font-mono text-4xl font-semibold text-fg">{vencem.length}</p>
                </div>
                <div className="text-xs text-muted">
                  <p><b className="text-fg">{erros.length}</b> questões no caderno de erros · <b className="text-fg">{cartoes.length}</b> cartões</p>
                  <p className="mt-0.5">{painel.itens.filter((x) => x.dominada).length} dominados · revisão espaçada: errou, volta amanhã; acertou, espaça (1, 3, 7, 15, 30 dias)</p>
                </div>
                <div className="ml-auto flex gap-2 text-xs">
                  {vencem.length ? (
                    <button className={btnPrimary} onClick={() => comecar(false)}><Refresh className="size-3.5" /> Revisar agora</button>
                  ) : ativos.length ? (
                    <button className={btn} onClick={() => comecar(true)} title="Nada vence hoje: revisa tudo o que não está dominado">
                      <Refresh className="size-3.5" /> Revisar mesmo assim
                    </button>
                  ) : null}
                </div>
              </div>

              {!painel.itens.length && !gerando && (
                <div className={`${card} text-xs text-muted`}>
                  <p className="text-sm text-fg">Aqui ficam o caderno de erros e os flashcards.</p>
                  <p className="mt-1">Toda questão que você erra numa prova (ou no treino) entra no caderno e volta para revisão nos dias certos.
                    Os flashcards saem do resumo e dos seus erros, e vão para o Anki em .csv.</p>
                </div>
              )}

              {gerando && (gerando.status === "aguardando"
                ? <AguardandoClaude texto={`O Claude vai escrever ${gerando.quantos} flashcards do resumo e dos seus erros.`} onCancelar={parar} />
                : <SinapseCartoes f={gerando} />)}

              <div className={`${card} flex flex-wrap items-center gap-2 text-xs`}>
                <Lampada className="size-4 text-faint" />
                <span className="text-sm text-fg">Flashcards</span>
                <span className="text-faint">do resumo e dos erros, sem repetir os que você já tem</span>
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-1 text-muted">
                    <input type="number" min={4} max={60} value={quantos} onChange={(e) => setQuantos(Math.max(4, Math.min(60, Number(e.target.value) || 4)))}
                           className="w-14 rounded-md border border-line bg-raised px-1.5 py-1 text-right font-mono text-fg focus:border-focus focus:outline-none" />
                    cartões
                  </label>
                  {props.botaoModelos}
                  {gerando?.status === "rodando"
                    ? <button className={btn} onClick={parar}><X className="size-3.5" /> Parar</button>
                    : <button className={btnPrimary} disabled={!!gerando} onClick={gerar}>Gerar cartões</button>}
                  <button className={btn} disabled={!cartoes.length} onClick={exportar} title="Arquivo para importar no Anki (frente, verso, tópico)">
                    <Download className="size-3.5" /> Anki (.csv)
                  </button>
                </div>
              </div>

              <div className="flex gap-1 self-start rounded-full border border-line p-0.5 text-xs" role="radiogroup" aria-label="Ver">
                {([["erros", `Caderno de erros · ${erros.length}`], ["cartoes", `Cartões · ${cartoes.length}`]] as const).map(([id, nome]) => (
                  <button key={id} role="radio" aria-checked={lista === id} onClick={() => setLista((l) => (l === id ? "" : id))}
                          className={`rounded-full px-3 py-1 ${lista === id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>{nome}</button>
                ))}
              </div>

              {lista && (
                <div className={`${card} flex flex-col divide-y divide-line p-0 text-xs`}>
                  {(lista === "erros" ? erros : cartoes).map((x) => (
                    <div key={x.chave} className="flex items-start gap-3 px-3.5 py-2.5">
                      <div className="min-w-0 flex-1">
                        <div className="line-clamp-2 text-fg [&_.md]:text-[13px] [&_p]:my-0">
                          <Markdown text={matematica(x.tipo === "erro" ? x.questao.enunciado : x.frente)} math />
                        </div>
                        {x.tipo === "cartao" && (
                          <div className="mt-0.5 line-clamp-2 text-muted [&_.md]:text-xs [&_.md]:text-muted [&_p]:my-0">
                            <Markdown text={matematica(x.verso)} math />
                          </div>
                        )}
                        <p className="mt-0.5 text-faint">{x.tipo === "erro" ? `${x.prova} · ` : ""}{x.topico}</p>
                      </div>
                      <span className={`shrink-0 font-mono ${x.dominada ? "text-ok" : x.vence ? "text-amber-300" : "text-faint"}`}
                            title={`Caixa ${x.caixa} de 5 · ${x.acertos} acerto(s), ${x.erros} erro(s)`}>
                        {x.dominada ? "dominado" : x.vence ? "hoje" : quando(x.proxima)} · {x.caixa}/5
                      </span>
                      {x.tipo === "cartao" && (
                        <button className="shrink-0 rounded-md p-1 text-faint hover:bg-raised hover:text-fg" title="Apagar o cartão"
                                onClick={() => apagar(x.id)}><Trash className="size-3.5" /></button>
                      )}
                    </div>
                  ))}
                  {!(lista === "erros" ? erros : cartoes).length && <p className="px-3.5 py-2.5 text-faint">Nada aqui ainda.</p>}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import type { EstudosFigura, EstudosProjeto, EstudosProva, EstudosProvaConfig, EstudosQuestao, EstudosTentativa, PesquisaFonte }
  from "../types";
import { ArrowLeft, ArrowRight, Bubble, Check, Clock, Copy, Image, Lampada, Pin, Refresh, Trash, X } from "./icons";
import { Lightbox } from "./Lightbox";
import { ConversaDuvida } from "./EstudosDuvidas";
import { Markdown } from "./MessageView";
import { BotaoEnviar, CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt, pilula, pilulaLigada }
  from "./Composer";
import { Menu } from "./Controls";
import Sinapse from "./Sinapse";
import { matematica } from "./estudosTexto";
import { PEDIDO_CLAUDE, type Modelos, btn, btnPrimary, card, corAcerto, gravarLocal, lerLocal, motorDe, nota, numeros, relogio, rotulo }
  from "./estudosUi";

const KEY_CONFIG = "forja.estudos.prova";
const rascunhoDe = (id: number, treino = false) => `forja.estudos.${treino ? "treino" : "rascunho"}.${id}`;
const LETRAS = "ABCDE";
const NOMES = { me: "Múltipla", vf: "V/F", disc: "Discursiva" } as const;   // rótulos do grafo: curtos, ficam no meio da aresta
const TIPO_CURTO = { me: "múltipla escolha", vf: "verdadeiro ou falso", disc: "discursiva" } as const;
const DIFICULDADE = { facil: "fácil", media: "média", dificil: "difícil" } as Record<string, string>;
const DIFICULDADES: { id: EstudosProvaConfig["dificuldade"]; label: string; hint: string }[] = [
  { id: "mista", label: "Misturada", hint: "Mais média, com algumas fáceis e difíceis" },
  { id: "facil", label: "Fácil", hint: "Lembrar conceitos" },
  { id: "media", label: "Média", hint: "Aplicar a uma situação" },
  { id: "dificil", label: "Difícil", hint: "Relacionar conceitos, interpretar dados, calcular" },
];
const PADRAO: Partial<EstudosProvaConfig> = { me: 8, vf: 2, disc: 0, dificuldade: "mista", estilo: true, tempo: 0, topicos: [] };
const DA_PLANEJADA: Record<string, PesquisaFonte["status"]> = { fila: "fila", gerando: "lendo", verificando: "lendo", ok: "util", descartada: "erro" };

type Resposta = number | boolean | string;
/** O que o modo treino recebe ao conferir uma questão: o gabarito e a explicação dela. */
type Conferida = Pick<EstudosQuestao, "correta" | "explicacao" | "por_alternativa" | "resposta_modelo" | "rubrica" | "pagina">
  & { certa: boolean | null };
type Rascunho = { respostas: Record<string, Resposta>; marcadas: string[]; atual: number; inicio: number;
                  conferidas?: Record<string, Conferida> };
type Vista = { tipo: "lista" } | { tipo: "fazer"; prova: EstudosProva; treino: boolean } | { tipo: "resultado"; t: EstudosTentativa };
/** Pedido de prova que vem de fora da aba (a "prova dos pontos fracos" do Desempenho). */
export type ProvaPendente = { topicos: string[]; instrucoes: string };

function Numero(props: { valor: number; min: number; max: number; unidade: string; dica: string;
                         onChange: (v: number) => void; icone?: React.ReactNode }) {
  return (
    <label title={props.dica} className={`${pilula} rounded-full py-0.5 pl-0.5 focus-within:border-focus`}>
      <input type="number" min={props.min} max={props.max} value={props.valor}
             onChange={(e) => props.onChange(Math.min(props.max, Math.max(props.min, Number(e.target.value) || 0)))}
             className="w-10 [appearance:textfield] rounded-full bg-raised py-0.5 text-center font-mono text-fg outline-none [&::-webkit-inner-spin-button]:appearance-none" />
      {props.icone}
      {props.unidade}
    </label>
  );
}

const quando = (iso: string) => (iso ? new Date(iso).toLocaleDateString("pt-BR") : "");

/** A figura do PDF que a questão usa. Fundo branco: é recorte de página impressa, e no tema escuro o desenho
 *  preto sumia. Clique amplia (o Lightbox dá zoom no detalhe do gráfico). Material apagado: some sem quebrar. */
export function FiguraQuestao({ conv, f }: { conv: number; f?: EstudosFigura }) {
  const [aberta, setAberta] = useState(false);
  const [falhou, setFalhou] = useState("");   // a src que falhou: a próxima questão (outra figura) aparece
  // ampliada, os atalhos da prova (A–E, setas, Enter) não respondem por trás
  useEffect(() => {
    if (!aberta) return;
    document.body.dataset.figuraAberta = "1";
    return () => { delete document.body.dataset.figuraAberta; };
  }, [aberta]);
  const src = f ? `/api/estudos-figura/${conv}/${f.material}/${f.id}` : "";
  if (!f || falhou === src) return null;
  return (
    <>
      <button type="button" onClick={() => setAberta(true)} title="Ampliar a figura"
              className="my-3 block w-fit max-w-full cursor-zoom-in overflow-hidden rounded-xl border border-line bg-white p-2">
        <img src={src} alt={f.descricao || "Figura da questão"} onError={() => setFalhou(src)} loading="lazy"
             className="block max-h-[min(440px,55vh)] w-auto max-w-full object-contain" />
      </button>
      {aberta && <Lightbox src={src} titulo="Figura da questão" onClose={() => setAberta(false)} />}
    </>
  );
}

/** O grafo da Pesquisa com um ramo por tipo de questão e uma folha por questão pedida. */
function SinapseProva({ p }: { p: EstudosProva }) {
  const tipos = (["me", "vf", "disc"] as const).filter((t) => p.planejadas.some((q) => q.tipo === t));
  const fontes = p.planejadas.map((q): PesquisaFonte => ({ id: q.id, rodada: tipos.indexOf(q.tipo) + 1, titulo: q.topico,
    status: DA_PLANEJADA[q.status] ?? "fila", url: "", dominio: "", erro: "", resumo: "", trecho: "" }));
  const prontas = p.planejadas.filter((q) => q.status === "ok").length;
  const aguardando = p.status === "aguardando";
  return (
    <Sinapse estado={{ status: "rodando", fontes, rodadas: [], pergunta: p.titulo, fase: "pronto", rodada: 0, rodadas_total: 0,
                       stats: p.stats }}
             ramos={tipos.map((t) => NOMES[t])}
             fase={aguardando ? "esperando o Claude" : p.etapa === "conferindo" ? "conferindo o gabarito"
                   : p.etapa === "figuras" ? `olhando as figuras do PDF${p.figuras_olhadas ? ` (${p.figuras_olhadas})` : ""}`
                   : "escrevendo as questões"}
             meta={aguardando ? <></> : <>
               <span className="sin-sep">·</span><span><b>{prontas}</b> de {p.planejadas.length} questões</span>
               {numeros(p) && <><span className="sin-sep">·</span><span>{numeros(p)}</span></>}
             </>} />
  );
}

function SinapseCorrecao({ t }: { t: EstudosTentativa }) {
  const discs = Object.entries(t.correcao).filter(([, c]) => typeof c.resposta === "string" && c.resposta);
  const fontes = discs.map(([id, c]): PesquisaFonte => ({ id, rodada: 1, titulo: id, status: c.pendente ? "lendo" : "util",
    url: "", dominio: "", erro: "", resumo: "", trecho: "" }));
  return (
    <Sinapse estado={{ status: "rodando", fontes, rodadas: [], pergunta: t.titulo, fase: "pronto", rodada: 0, rodadas_total: 0, stats: t.stats }}
             ramos={["Discursivas"]} fase={t.status === "aguardando" ? "esperando o Claude" : "corrigindo as discursivas"}
             meta={t.status === "aguardando" ? <></> : <>{numeros(t) && <><span className="sin-sep">·</span><span>{numeros(t)}</span></>}</>} />
  );
}

export function AguardandoClaude({ texto, onCancelar }: { texto: string; onCancelar: () => void }) {
  return (
    <div className={`${card} text-xs text-muted`}>
      <p className="text-sm text-fg">Pedido enviado ao Claude</p>
      <p className="mt-1">{texto} No Claude Code conectado ao Forja, peça: <span className="text-fg">“{PEDIDO_CLAUDE}”</span></p>
      <div className="mt-2 flex gap-2">
        <button className={btn} onClick={() => navigator.clipboard.writeText(PEDIDO_CLAUDE)}><Copy className="size-3.5" /> Copiar o pedido</button>
        <button className={btn} onClick={onCancelar}><X className="size-3.5" /> Cancelar pedido</button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ fazer a prova

function FazerProva({ prova, treino, duvida, onEntregar, onSair }: {
  prova: EstudosProva; treino: boolean; duvida: Duvida;
  onEntregar: (respostas: Record<string, Resposta>, segundos: number) => void; onSair: () => void;
}) {
  const chave = rascunhoDe(prova.message_id, treino);
  const [r, setR] = useState<Rascunho>(() => lerLocal<Rascunho | null>(chave, null) ?? { respostas: {}, marcadas: [], atual: 0, inicio: Date.now() });
  const [agora, setAgora] = useState(() => Date.now());
  const [confirmar, setConfirmar] = useState(false);
  const enviado = useRef(false);
  const qs = prova.questoes;
  const q = qs[Math.min(r.atual, qs.length - 1)];
  const decorrido = Math.max(0, Math.floor((agora - r.inicio) / 1000));
  const limite = treino ? 0 : (prova.config.tempo || 0) * 60;   // treino é sem relógio
  const resta = limite ? limite - decorrido : 0;
  const respondidas = qs.filter((x) => r.respostas[x.id] !== undefined && r.respostas[x.id] !== "").length;
  const conf = r.conferidas?.[q.id];   // modo treino: esta já foi conferida (resposta travada, gabarito à vista)
  const [conferindo, setConferindo] = useState(false);

  useEffect(() => { const t = setInterval(() => setAgora(Date.now()), 1000); return () => clearInterval(t); }, []);
  useEffect(() => gravarLocal(chave, r), [chave, r]);

  const entregar = useCallback(() => {
    if (enviado.current) return;
    enviado.current = true;
    gravarLocal(chave, null);
    onEntregar(r.respostas, decorrido);
  }, [chave, onEntregar, r.respostas, decorrido]);

  useEffect(() => { if (limite && resta <= 0) entregar(); }, [limite, resta, entregar]);   // acabou o tempo: entrega

  const responder = (v: Resposta) => !conf && setR((x) => ({ ...x, respostas: { ...x.respostas, [q.id]: v } }));
  const respondida = r.respostas[q.id] !== undefined && r.respostas[q.id] !== "";

  async function conferir() {
    if (!treino || conf || !respondida || conferindo) return;
    setConferindo(true);
    try {
      const c = await api.post<Conferida>(`/estudos/prova/${prova.message_id}/conferir`, { questao_id: q.id, resposta: r.respostas[q.id] });
      setR((x) => ({ ...x, conferidas: { ...x.conferidas, [q.id]: c } }));
    } catch (e: any) {
      duvida.onError(e.message);
    } finally {
      setConferindo(false);
    }
  }
  const ir = (i: number) => setR((x) => ({ ...x, atual: Math.max(0, Math.min(qs.length - 1, i)) }));
  const marcada = r.marcadas.includes(q.id);

  // Teclado: ← → navegam; A–E marcam a alternativa; V/F no verdadeiro ou falso (fora do campo de texto).
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("textarea, input") || e.ctrlKey || e.metaKey || e.altKey || document.body.dataset.figuraAberta) return;
      if (e.key === "ArrowRight") ir(r.atual + 1);
      else if (e.key === "ArrowLeft") ir(r.atual - 1);
      else if (q.tipo === "me" && LETRAS.slice(0, q.alternativas?.length).includes(e.key.toUpperCase())) responder(LETRAS.indexOf(e.key.toUpperCase()));
      else if (q.tipo === "vf" && ["v", "f"].includes(e.key.toLowerCase())) responder(e.key.toLowerCase() === "v");
      else if (e.key === "Enter" && treino) conferir();
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  });

  const faltam = qs.length - respondidas;
  return (
    <div className="flex flex-col gap-3">
      <div className={`${card} flex flex-wrap items-center gap-3 text-xs`}>
        <button className="text-faint hover:text-fg" title="Sair (as respostas ficam guardadas)" onClick={onSair}><ArrowLeft className="size-4" /></button>
        <span className="text-sm font-medium text-fg">{prova.titulo}</span>
        <span className="text-muted">{respondidas} de {qs.length} respondidas</span>
        {treino && <span className="rounded-full bg-accent-soft px-2 py-0.5 text-accent-text">treino · correção na hora</span>}
        <span className={`ml-auto flex items-center gap-1 font-mono ${limite && resta < 300 ? "text-amber-300" : "text-muted"}`}
              title={limite ? "Tempo que falta" : "Tempo de prova"}>
          <Clock className="size-3.5" />{relogio(limite ? Math.max(0, resta) : decorrido)}
        </span>
        <button className={btnPrimary} onClick={() => (faltam || r.marcadas.length ? setConfirmar(true) : entregar())}>
          <Check className="size-3.5" /> {treino ? "Terminar o treino" : "Entregar"}
        </button>
      </div>

      {confirmar && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-warn/55 bg-warn/[.07] px-3.5 py-2.5 text-[13px] text-warn">
          <span className="min-w-0 flex-1">
            {[faltam ? `${faltam} sem resposta` : "", r.marcadas.length ? `${r.marcadas.length} marcada(s) para revisar` : ""]
              .filter(Boolean).join(" e ")}. Entregar assim mesmo?
          </span>
          <button className={btn} onClick={() => setConfirmar(false)}>Voltar</button>
          <button className={btnPrimary} onClick={entregar}>Entregar</button>
        </div>
      )}

      <div className="flex flex-wrap gap-1.5" aria-label="Questões">
        {qs.map((x, i) => {
          const feita = r.respostas[x.id] !== undefined && r.respostas[x.id] !== "";
          const c = r.conferidas?.[x.id];
          const cor = c ? (c.certa === true ? "border-ok/60 bg-ok/[.08] text-ok" : c.certa === false ? "border-red-400/60 bg-red-400/[.07] text-red-300"
            : "border-accent-line bg-accent-soft text-accent-text") : feita ? "border-accent-line bg-accent-soft text-accent-text" : "border-line text-faint hover:text-fg";
          return (
            <button key={x.id} onClick={() => ir(i)} title={`Questão ${i + 1}${r.marcadas.includes(x.id) ? " · marcada" : ""}`}
                    className={`relative grid size-8 place-items-center rounded-lg border font-mono text-xs ${
                      i === r.atual ? "border-focus text-fg ring-2 ring-accent/30" : cor}`}>
              {i + 1}
              {r.marcadas.includes(x.id) && <span className="absolute -top-1 -right-1 size-2 rounded-full bg-amber-300" />}
            </button>
          );
        })}
      </div>

      <div className={`${card} px-6 py-5`}>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-faint">
          <span className="text-muted">Questão {r.atual + 1} de {qs.length}</span>
          <span>· {TIPO_CURTO[q.tipo]}</span>
          {q.topico && <span>· {q.topico}</span>}
          <span>· {DIFICULDADE[q.dificuldade] ?? q.dificuldade}</span>
          <span>· vale {nota(q.pontos)} ponto{q.pontos === 1 ? "" : "s"}</span>
        </div>
        <Markdown text={matematica(q.enunciado)} math />
        <FiguraQuestao conv={duvida.conv} f={q.figura} />
        <div className="mt-4 flex flex-col gap-2">
          {q.tipo === "me" && q.alternativas?.map((a, i) => {
            const minha = r.respostas[q.id] === i, certa = conf && conf.correta === i;
            return (
              <button key={i} onClick={() => responder(i)} aria-pressed={minha} disabled={!!conf}
                      className={`flex flex-col rounded-xl border px-3.5 py-2.5 text-left ${
                        certa ? "border-ok/55 bg-ok/[.06]" : conf && minha ? "border-red-400/50 bg-red-400/[.05]"
                          : minha ? "border-accent-line bg-accent-soft" : conf ? "border-line" : "border-line hover:border-focus hover:bg-raised"}`}>
                <span className="flex items-start gap-3">
                  <span className={`grid size-6 shrink-0 place-items-center rounded-full border font-mono text-xs ${
                    certa ? "border-ok text-ok" : conf && minha ? "border-red-300 text-red-300"
                      : minha ? "border-accent bg-accent text-accent-fg" : "border-line-strong text-muted"}`}>{LETRAS[i]}</span>
                  <span className="min-w-0 flex-1 [&_.md]:text-[14px] [&_p]:my-0"><Markdown text={matematica(a)} math /></span>
                </span>
                {conf?.por_alternativa?.[i] && (certa || minha) && <span className="mt-1 ml-9 text-xs text-muted">{conf.por_alternativa[i]}</span>}
              </button>
            );
          })}
          {q.tipo === "vf" && (
            <div className="grid grid-cols-2 gap-2">
              {([[true, "Verdadeiro", "V"], [false, "Falso", "F"]] as const).map(([v, nome, tecla]) => (
                <button key={nome} onClick={() => responder(v)} aria-pressed={r.respostas[q.id] === v} disabled={!!conf}
                        className={`rounded-xl border px-3.5 py-3 text-sm ${
                          conf && conf.correta === v ? "border-ok/55 bg-ok/[.06] text-ok"
                            : conf && r.respostas[q.id] === v ? "border-red-400/50 bg-red-400/[.05] text-red-300"
                            : r.respostas[q.id] === v ? "border-accent-line bg-accent-soft text-accent-text" : "border-line text-fg hover:border-focus hover:bg-raised"}`}>
                  {nome} <span className="ml-1 font-mono text-xs text-faint">{tecla}</span>
                </button>
              ))}
            </div>
          )}
          {q.tipo === "disc" && (
            <textarea rows={8} value={String(r.respostas[q.id] ?? "")} onChange={(e) => responder(e.target.value)} readOnly={!!conf}
                      placeholder="Sua resposta"
                      className="rounded-xl border border-line bg-raised p-3 text-[14px] text-fg focus:border-focus focus:outline-none" />
          )}
        </div>

        {conf && (
          // a correção na hora do modo treino: o porquê (e, na discursiva, a resposta esperada; a nota sai no fim)
          <div className="mt-4 rounded-xl bg-raised/50 px-3.5 py-2.5">
            <p className={`${rotulo} mb-1 ${conf.certa === true ? "text-ok!" : conf.certa === false ? "text-red-300!" : ""}`}>
              {conf.certa === true ? "Certa" : conf.certa === false ? "Errada" : "Resposta esperada"}
            </p>
            {conf.resposta_modelo && <div className="[&_.md]:text-[14px]"><Markdown text={matematica(conf.resposta_modelo)} math /></div>}
            {!!conf.rubrica?.length && (
              <ul className="mt-1 text-xs text-muted">{conf.rubrica.map((x) => <li key={x.criterio}>· {x.criterio} ({nota(x.pontos)} pt)</li>)}</ul>
            )}
            {conf.explicacao && q.tipo !== "disc" && <div className="[&_.md]:text-[14px]"><Markdown text={matematica(conf.explicacao)} math /></div>}
            {conf.pagina && <p className="mt-1 text-xs text-faint">Material: {conf.pagina}</p>}
          </div>
        )}

        {treino && !conf && (
          <div className="mt-4 border-t border-line pt-3">
            <ConversaDuvida key={q.id} conv={duvida.conv} fio={`dica:${prova.message_id}:${q.id}`} carimbo={duvida.carimbo}
                            modelos={duvida.modelos} dica compacta onError={duvida.onError} />
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 text-xs">
        <button className={btn} disabled={r.atual === 0} onClick={() => ir(r.atual - 1)}><ArrowLeft className="size-3.5" /> Anterior</button>
        {treino && (
          <button className={btnPrimary} disabled={!respondida || !!conf || conferindo} onClick={conferir}
                  title="Ver na hora se acertou, com a explicação (Enter)">
            <Check className="size-3.5" /> {conf ? "Conferida" : conferindo ? "Conferindo…" : "Conferir"}
          </button>
        )}
        <button className={`${btn} ${marcada ? "border-amber-300/60! text-amber-300!" : ""}`} aria-pressed={marcada}
                onClick={() => setR((x) => ({ ...x, marcadas: marcada ? x.marcadas.filter((m) => m !== q.id) : [...x.marcadas, q.id] }))}>
          <Pin className="size-3.5" /> {marcada ? "Marcada para revisar" : "Marcar para revisar"}
        </button>
        <span className="ml-auto hidden text-faint md:inline">← → navegam · A–E ou V/F respondem{treino ? " · Enter confere" : ""}</span>
        <button className={btn} disabled={r.atual >= qs.length - 1} onClick={() => ir(r.atual + 1)}>Próxima <ArrowRight className="size-3.5" /></button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ resultado

/** O que a conversa de dúvidas embutida na questão precisa. */
type Duvida = { conv: number; modelos: Modelos; carimbo?: string; contagem: Record<string, number>; onError: (e: string) => void };

function Resultado({ t, onRefazer, onVoltar, duvida, nomes = {} }: { t: EstudosTentativa; onRefazer: () => void; onVoltar: () => void;
                                                                     duvida: Duvida; nomes?: Record<string, string> }) {
  const [filtro, setFiltro] = useState<"todas" | "erradas" | "certas">("todas");
  const corrigindo = t.status === "rodando" || t.status === "aguardando";
  const qs = t.questoes.filter((q) => {
    const c = t.correcao[q.id];
    return filtro === "todas" || (filtro === "certas" ? c?.certa === true : c?.certa !== true);
  });
  return (
    <div className="flex flex-col gap-3">
      <div className={`${card} flex flex-wrap items-center gap-x-6 gap-y-3`}>
        <div>
          <p className={rotulo}>{t.titulo} · nota</p>
          <p className="mt-1 font-mono text-4xl font-semibold text-fg">{nota(t.nota)}<span className="text-lg text-faint"> / 10</span></p>
        </div>
        <div className="text-xs text-muted">
          <p><b className="text-fg">{t.acertos}</b> de {t.questoes.length} certas · {nota(t.pontos)} de {nota(t.max)} pontos</p>
          {!!t.segundos && <p className="mt-0.5">em {relogio(t.segundos)}</p>}
          {corrigindo && <p className="mt-0.5 text-sky-300">as discursivas ainda estão sendo corrigidas</p>}
          {t.aviso && <p className="mt-0.5 text-amber-300">{t.aviso}</p>}
        </div>
        <div className="ml-auto flex gap-2 text-xs">
          <button className={btn} onClick={onVoltar}><ArrowLeft className="size-3.5" /> Provas</button>
          <button className={btn} onClick={onRefazer}><Refresh className="size-3.5" /> Refazer esta prova</button>
        </div>
        {!!t.por_materia?.length && (
          <div className="flex w-full flex-col gap-1.5 border-t border-line pt-3 text-xs">
            <p className={rotulo}>Por matéria · cada erro foi para o caderno da matéria</p>
            {t.por_materia.map((x) => {
              const pct = x.max ? x.pontos / x.max : 0;
              return (
                <div key={x.materia} className="flex items-center gap-3">
                  <span className="w-48 shrink-0 truncate text-fg" title={nomes[x.materia]}>{nomes[x.materia] ?? "Matéria tirada"}</span>
                  <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-raised">
                    <div className={`h-full rounded-full ${pct >= 0.7 ? "bg-emerald-400/70" : pct >= 0.4 ? "bg-amber-300/70" : "bg-red-400/70"}`}
                         style={{ width: `${Math.round(pct * 100)}%` }} />
                  </div>
                  <span className="w-16 shrink-0 text-right font-mono text-faint">{x.acertos}/{x.n}</span>
                </div>
              );
            })}
          </div>
        )}
        {t.por_topico.length > 1 && (
          <div className="flex w-full flex-col gap-1.5 border-t border-line pt-3 text-xs">
            {t.por_topico.map((x) => {
              const pct = x.max ? x.pontos / x.max : 0;
              return (
                <div key={x.topico} className="flex items-center gap-3">
                  <span className="w-48 shrink-0 truncate text-muted" title={x.topico}>{x.topico}</span>
                  <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-raised">
                    <div className={`h-full rounded-full ${pct >= 0.7 ? "bg-emerald-400/70" : pct >= 0.4 ? "bg-amber-300/70" : "bg-red-400/70"}`}
                         style={{ width: `${Math.round(pct * 100)}%` }} />
                  </div>
                  <span className="w-16 shrink-0 text-right font-mono text-faint">{nota(x.pontos)}/{nota(x.max)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex gap-1 self-start rounded-full border border-line p-0.5 text-xs" role="radiogroup" aria-label="Filtrar">
        {([["todas", "Todas"], ["erradas", "Erradas"], ["certas", "Certas"]] as const).map(([id, nome]) => (
          <button key={id} role="radio" aria-checked={filtro === id} onClick={() => setFiltro(id)}
                  className={`rounded-full px-3 py-1 ${filtro === id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>{nome}</button>
        ))}
      </div>

      {qs.map((q) => <QuestaoCorrigida key={q.id} q={q} n={t.questoes.indexOf(q) + 1} t={t} duvida={duvida} />)}
    </div>
  );
}

function QuestaoCorrigida({ q, n, t, duvida }: { q: EstudosQuestao; n: number; t: EstudosTentativa; duvida: Duvida }) {
  const c = t.correcao[q.id];
  const fio = `questao:${t.message_id}:${q.id}`;
  const feitas = duvida.contagem[fio] ?? 0;
  const [aberta, setAberta] = useState(false);
  const sugestoes = [c?.certa === true ? "Por que essa é a certa?" : "Por que a minha está errada?",
    "Explique de outro jeito", "Me dê um exemplo parecido", "Acho que o gabarito está errado"];
  const marca = c?.pendente ? ["…", "text-sky-300", "corrigindo"] : c?.certa === true ? ["✓", "text-ok", "certa"]
    : c?.certa === null ? ["½", "text-amber-300", "parcial"] : ["✕", "text-red-300", "errada"];
  return (
    <div className={`${card} px-6 py-5`}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-faint">
        <span className={`font-mono text-base ${marca[1]}`} title={marca[2]}>{marca[0]}</span>
        <span className="text-muted">Questão {n}</span>
        <span>· {TIPO_CURTO[q.tipo]}</span>
        {q.topico && <span>· {q.topico}</span>}
        <span className="ml-auto font-mono">{nota(c?.pontos ?? 0)}/{nota(q.pontos)}</span>
      </div>
      <Markdown text={matematica(q.enunciado)} math />
      <FiguraQuestao conv={duvida.conv} f={q.figura} />

      {q.tipo === "me" && (
        <div className="mt-3 flex flex-col gap-1.5">
          {q.alternativas?.map((a, i) => {
            const certa = i === q.correta, minha = i === c?.resposta;
            return (
              <div key={i} className={`rounded-xl border px-3.5 py-2 ${certa ? "border-ok/55 bg-ok/[.06]" : minha ? "border-red-400/50 bg-red-400/[.05]" : "border-line"}`}>
                <div className="flex items-start gap-3">
                  <span className={`grid size-6 shrink-0 place-items-center rounded-full border font-mono text-xs ${
                    certa ? "border-ok text-ok" : minha ? "border-red-300 text-red-300" : "border-line-strong text-faint"}`}>{LETRAS[i]}</span>
                  <span className="min-w-0 flex-1 [&_.md]:text-[14px] [&_p]:my-0"><Markdown text={matematica(a)} math /></span>
                  <span className="shrink-0 text-xs">
                    {certa && <span className="text-ok">gabarito</span>}
                    {minha && <span className={certa ? "ml-2 text-ok" : "text-red-300"}>sua resposta</span>}
                  </span>
                </div>
                {q.por_alternativa?.[i] && <p className="mt-1 ml-9 text-xs text-muted">{q.por_alternativa[i]}</p>}
              </div>
            );
          })}
          {c?.resposta === null && <p className="text-xs text-faint">Você não respondeu esta.</p>}
        </div>
      )}

      {q.tipo === "vf" && (
        <p className="mt-3 text-sm">
          <span className="text-muted">Sua resposta: </span>
          <span className={c?.certa ? "text-ok" : "text-red-300"}>{c?.resposta === null ? "em branco" : c?.resposta ? "Verdadeiro" : "Falso"}</span>
          <span className="text-muted"> · Gabarito: </span><span className="text-fg">{q.correta ? "Verdadeiro" : "Falso"}</span>
        </p>
      )}

      {q.tipo === "disc" && (
        <div className="mt-3 flex flex-col gap-2 text-sm">
          <div className="rounded-xl border border-line bg-raised/40 px-3.5 py-2.5">
            <p className={`${rotulo} mb-1`}>Sua resposta</p>
            <p className="whitespace-pre-wrap text-fg">{String(c?.resposta || "") || "Em branco."}</p>
          </div>
          {c?.feedback && <p className="text-muted">{c.feedback}</p>}
          {!!c?.criterios?.length && (
            <ul className="text-xs text-muted">
              {c.criterios.map((x) => (
                <li key={x.criterio} className="flex gap-2"><span className="font-mono text-faint">{nota(x.pontos)}/{nota(x.max)}</span>{x.criterio}</li>
              ))}
            </ul>
          )}
          {q.resposta_modelo && (
            <details className="text-xs text-muted">
              <summary className="cursor-pointer text-faint hover:text-fg">Resposta esperada</summary>
              <div className="mt-1"><Markdown text={matematica(q.resposta_modelo)} math /></div>
            </details>
          )}
        </div>
      )}

      {q.explicacao && q.tipo !== "disc" && (
        <div className="mt-3 rounded-xl bg-raised/50 px-3.5 py-2.5">
          <p className={`${rotulo} mb-1`}>Por quê</p>
          <div className="[&_.md]:text-[14px]"><Markdown text={matematica(q.explicacao)} math /></div>
          <p className="mt-1 text-xs text-faint">
            {q.pagina && `Material: ${q.pagina}`}
            {q.verificada === false && `${q.pagina ? " · " : ""}gabarito não conferido pelo verificador`}
          </p>
        </div>
      )}

      <div className="mt-3 border-t border-line pt-3">
        <button className={`${btn} text-xs`} aria-expanded={aberta} onClick={() => setAberta((v) => !v)}>
          <Bubble className="size-3.5" /> {aberta ? "Fechar a conversa" : "Perguntar sobre esta questão"}
          {!!feitas && !aberta && <span className="rounded-full bg-raised px-1.5 font-mono text-[10.5px] text-muted">{feitas}</span>}
        </button>
        {aberta && (
          <div className="mt-3">
            <ConversaDuvida conv={duvida.conv} fio={fio} questao={{ tentativa_id: t.message_id, questao_id: q.id }}
                            carimbo={duvida.carimbo} modelos={duvida.modelos} sugestoes={sugestoes} compacta onError={duvida.onError} />
          </div>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ a aba

export default function Provas(props: {
  conv: number;
  projeto: EstudosProjeto;
  carimbo?: string;
  modelos: Modelos;
  botaoModelos: React.ReactNode;
  painelModelos: React.ReactNode | null;
  onImersao?: (v: boolean) => void;   // fazendo a prova: a tela de fora esconde a fila de abas
  onError: (e: string) => void;
  onRecarregar: () => Promise<void> | void;
  pendente?: ProvaPendente | null;
  onPendenteUsado?: () => void;
  geral?: boolean;   // no "Tudo" do objetivo: o simulado geral (todas as matérias, pelo peso de cada uma)
}) {
  const [vista, setVista] = useState<Vista>({ tipo: "lista" });
  const geral = !!props.geral;
  const nomes = Object.fromEntries((props.projeto.materias ?? []).map((m) => [m.id, m.nome]));
  const [cfg, setCfg] = useState<Partial<EstudosProvaConfig>>(() => lerLocal(KEY_CONFIG, PADRAO));
  const [instrucoes, setInstrucoes] = useState("");
  const [topicosAberto, setTopicosAberto] = useState(false);
  const [viva, setViva] = useState<EstudosProva | EstudosTentativa | null>(null);   // geração ou correção em andamento
  const [pronta, setPronta] = useState<number | null>(null);   // faixa "prova pronta"
  const corte = useRef<AbortController | null>(null);
  const ouvindo = useRef(0);
  const aoErro = useRef(props.onError);
  aoErro.current = props.onError;
  const recarregar = useRef(props.onRecarregar);
  recarregar.current = props.onRecarregar;

  useEffect(() => gravarLocal(KEY_CONFIG, { ...cfg, instrucoes: undefined }), [cfg]);
  useEffect(() => () => corte.current?.abort(), []);
  useEffect(() => { props.onImersao?.(vista.tipo === "fazer"); return () => props.onImersao?.(false); }, [vista.tipo]);   // eslint-disable-line react-hooks/exhaustive-deps

  const topicosDisp = props.projeto.topicos ?? [];
  const escolhidos = (cfg.topicos ?? []).filter((t) => topicosDisp.includes(t));
  const temSimulado = props.projeto.materiais.some((m) => m.uso === "prova") || !!props.projeto.resumo?.perfil?.banca;
  const total = (cfg.me ?? 0) + (cfg.vf ?? 0) + (cfg.disc ?? 0);
  const figs = props.projeto.figuras;
  // o teto: as que servem mais as ainda não olhadas (nada olhado = as recortadas; tudo olhado = as úteis)
  const maxFiguras = Math.min(total, figs ? figs.uteis + figs.detectadas - figs.olhadas : 0);
  const comFigura = Math.min(cfg.figuras ?? 0, maxFiguras);
  const gerando = viva?.tipo === "prova" && (viva.status === "rodando" || viva.status === "aguardando") ? viva : null;
  const corrigindo = viva?.tipo === "tentativa" && (viva.status === "rodando" || viva.status === "aguardando") ? viva : null;

  /** Acompanha uma execução (geração ou correção) pelo SSE; no fim, recarrega a lista. */
  const ouvir = useCallback(async (mid: number, fim?: (e: any) => void) => {
    if (ouvindo.current === mid) return;
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    ouvindo.current = mid;
    let ultimo: any = null;
    try {
      await streamSSE(`/estudos/execucao/${mid}/stream`, { signal: ctl.signal }, (ev) => {
        if (ev.erro || ctl.signal.aborted) return;
        ultimo = ev;
        setViva(ev);
      });
    } catch (e: any) {
      if (!ctl.signal.aborted) aoErro.current(e.message);
    } finally {
      if (ouvindo.current === mid) ouvindo.current = 0;
    }
    if (!ctl.signal.aborted && ultimo) {
      await recarregar.current();
      fim?.(ultimo);
    }
  }, []);

  // Aberto no meio de uma geração/correção (outra aba, o celular): volta a acompanhar pelo SSE. Pedido
  // esperando o Claude não tem o que ouvir — é lido uma vez, e o carimbo avisa quando ele terminar
  // (ouvir ali seria um laço: o SSE fecha na hora, recarrega, e o efeito ouviria de novo).
  useEffect(() => {
    if (ouvindo.current) return;
    const todos = props.projeto.provas.flatMap((p) => [{ id: p.message_id, status: p.status },
      ...p.tentativas.map((t) => ({ id: t.message_id, status: t.status }))]);
    const rodando = todos.find((x) => x.status === "rodando");
    const esperando = todos.find((x) => x.status === "aguardando");
    if (rodando) ouvir(rodando.id);
    else if (esperando) api.get<EstudosProva | EstudosTentativa>(`/estudos/execucao/${esperando.id}`).then(setViva).catch(() => {});
    else setViva(null);
  }, [props.projeto, ouvir]);

  async function gerar(extra?: ProvaPendente) {
    if (!total || gerando) return;
    setPronta(null);
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    let ultimo: EstudosProva | null = null;
    try {
      ouvindo.current = -1;
      await streamSSE(`/estudos/${props.conv}/prova`, { method: "POST", signal: ctl.signal, body: JSON.stringify({
        config: geral
          // os pontos fracos pedidos no Desempenho do Tudo viram a distribuição "fracos" do simulado geral
          ? { ...cfg, figuras: 0, topicos: [], estilo: false, geral: true, distribuicao: extra ? "fracos" : (cfg.distribuicao ?? "peso"),
              instrucoes: extra?.instrucoes ?? instrucoes }
          : { ...cfg, figuras: comFigura, topicos: extra?.topicos ?? escolhidos, instrucoes: extra?.instrucoes ?? instrucoes },
        ...motorDe(props.modelos) }) }, (ev) => {
        if (ctl.signal.aborted) return;
        if (ev.erro) aoErro.current(ev.erro);
        else { ultimo = ev; setViva(ev); }
      });
    } catch (e: any) {
      aoErro.current(e.message);
    } finally {
      if (ouvindo.current === -1) ouvindo.current = 0;
    }
    await recarregar.current();
    const u = ultimo as EstudosProva | null;
    if (u?.status === "pronto") setPronta(u.message_id);
    if (u?.status !== "aguardando") setViva(null);
    setInstrucoes("");
  }

  // A prova dos pontos fracos pedida no Desempenho: gera uma vez, com os tópicos de lá.
  useEffect(() => {
    if (props.pendente && !gerando) {
      gerar(props.pendente);
      props.onPendenteUsado?.();
    }
  }, [props.pendente]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function fazer(provaId: number, treino = false) {
    try {
      const p = await api.get<EstudosProva>(`/estudos/execucao/${provaId}`);
      if (!p.questoes.length) return aoErro.current("Esta prova não tem questões.");
      // refazer uma prova já entregue: a tela mostra de novo sem gabarito (as respostas começam do zero)
      setPronta(null);
      setVista({ tipo: "fazer", treino, prova: { ...p, questoes: p.questoes.map(({ id, tipo, enunciado, pontos, topico, dificuldade, alternativas, figura }) =>
        ({ id, tipo, enunciado, pontos, topico, dificuldade, alternativas, figura })) } });
    } catch (e: any) {
      aoErro.current(e.message);
    }
  }

  async function verResultado(tid: number) {
    try {
      setVista({ tipo: "resultado", t: await api.get<EstudosTentativa>(`/estudos/execucao/${tid}`) });
    } catch (e: any) {
      aoErro.current(e.message);
    }
  }

  async function entregar(prova: EstudosProva, respostas: Record<string, Resposta>, segundos: number, treino: boolean) {
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    let ultimo: EstudosTentativa | null = null;
    try {
      ouvindo.current = -1;
      await streamSSE(`/estudos/prova/${prova.message_id}/entregar`, { method: "POST", signal: ctl.signal,
        body: JSON.stringify({ respostas, segundos, modo: treino ? "treino" : "prova", ...motorDe(props.modelos) }) }, (ev) => {
        if (ctl.signal.aborted) return;
        if (ev.erro) aoErro.current(ev.erro);
        else { ultimo = ev; setViva(ev); setVista({ tipo: "resultado", t: ev }); }
      });
    } catch (e: any) {
      aoErro.current(e.message);
      setVista({ tipo: "fazer", prova, treino });   // as respostas não se perdem: voltam para a prova
    } finally {
      if (ouvindo.current === -1) ouvindo.current = 0;
    }
    await recarregar.current();
    const u = ultimo as EstudosTentativa | null;
    if (u && u.status !== "aguardando") setViva(null);
  }

  async function parar() {
    if (viva) await api.post(`/estudos/execucao/${viva.message_id}/cancelar`, {}).catch(() => {});
    await recarregar.current();
  }

  async function apagar(id: number) {
    try {
      await api.del(`/estudos/prova/${id}`);
      gravarLocal(rascunhoDe(id), null);
      gravarLocal(rascunhoDe(id, true), null);
      await recarregar.current();
    } catch (e: any) {
      aoErro.current(e.message);
    }
  }

  // A correção que acaba (inclusive feita pelo Claude) atualiza o resultado aberto.
  useEffect(() => {
    if (vista.tipo === "resultado" && viva?.tipo === "tentativa" && viva.message_id === vista.t.message_id && viva !== vista.t) {
      setVista({ tipo: "resultado", t: viva });
    }
  }, [viva, vista]);
  useEffect(() => {
    if (vista.tipo === "resultado" && !corrigindo && (vista.t.status === "rodando" || vista.t.status === "aguardando")) verResultado(vista.t.message_id);
  }, [corrigindo]);   // eslint-disable-line react-hooks/exhaustive-deps

  // no Tudo, só os simulados gerais (as provas de cada matéria ficam na matéria)
  const provas = [...props.projeto.provas].filter((p) => !geral || p.config?.geral).reverse();
  // a previsão de quantas questões cada matéria leva (o backend reparte igual: pelo peso)
  const ms = props.projeto.materias ?? [];
  const pesoDe = (m: (typeof ms)[number]) => (m.peso ?? 1) * (cfg.distribuicao === "fracos" ? 1 + 2 * (1 - (m.acerto ?? 50) / 100) : 1);
  const somaPeso = ms.reduce((s, m) => s + pesoDe(m), 0) || 1;
  const resumoCfg = (c: EstudosProvaConfig) => (["me", "vf", "disc"] as const).filter((t) => c[t]).map((t) => `${c[t]} ${TIPO_CURTO[t]}`).join(" · ");
  const pedidas = (c?: EstudosProvaConfig) => (c ? (c.me ?? 0) + (c.vf ?? 0) + (c.disc ?? 0) : 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className={`min-h-0 flex-1 overflow-y-auto ${vista.tipo === "lista" ? "px-8 pt-6 pb-10" : "px-5 py-4"}`}>
        <div className={`mx-auto flex flex-col gap-3 ${vista.tipo === "lista" ? "max-w-[860px]" : "max-w-3xl"}`}>

          {vista.tipo === "fazer" && (
            <FazerProva prova={vista.prova} treino={vista.treino} onSair={() => setVista({ tipo: "lista" })}
                        duvida={{ conv: props.conv, modelos: props.modelos, carimbo: props.carimbo, contagem: {}, onError: props.onError }}
                        onEntregar={(respostas, segundos) => entregar(vista.prova, respostas, segundos, vista.treino)} />
          )}

          {vista.tipo === "resultado" && (
            <>
              {corrigindo?.message_id === vista.t.message_id && (corrigindo.status === "aguardando"
                ? <AguardandoClaude texto="As discursivas vão ser corrigidas pelo Claude." onCancelar={parar} />
                : <SinapseCorrecao t={corrigindo} />)}
              <Resultado t={vista.t} nomes={nomes} onVoltar={() => setVista({ tipo: "lista" })} onRefazer={() => fazer(vista.t.prova_id)}
                         duvida={{ conv: props.conv, modelos: props.modelos, carimbo: props.carimbo,
                                   contagem: props.projeto.duvidas ?? {}, onError: props.onError }} />
            </>
          )}

          {vista.tipo === "lista" && (
            <>
              {geral && !provas.length && !gerando && (
                <div className={`${card} text-xs text-muted`}>
                  <p className="text-sm text-fg">Um simulado com todas as matérias do objetivo.</p>
                  <p className="mt-1">
                    As questões saem de cada matéria na proporção do peso dela (o número de questões no edital, por exemplo), ou
                    do peso vezes o que falta acertar. Na entrega, a nota sai por matéria e cada erro vai para o caderno da matéria.
                    Matéria sem resumo nem material fica de fora.
                  </p>
                </div>
              )}
              {!geral && !provas.length && !gerando && (
                <div className={`${card} text-xs text-muted`}>
                  <p className="text-sm text-fg">Uma prova do seu material, com nota e o porquê de cada questão.</p>
                  <p className="mt-1">
                    Escolha quantas questões de cada tipo e toque em gerar. A IA escreve as questões a partir do resumo e do
                    material, confere o gabarito resolvendo cada uma sem ver a resposta, e deixa pronta a explicação de cada
                    alternativa para depois da entrega.
                    {!props.projeto.resumo && !props.projeto.materiais.length && " Antes, anexe material ou gere o resumo na aba Resumo."}
                  </p>
                </div>
              )}

              {gerando && (gerando.status === "aguardando"
                ? <AguardandoClaude texto="O Claude vai escrever as questões e as explicações." onCancelar={parar} />
                : <SinapseProva p={gerando} />)}
              {gerando?.aviso && <p className="text-xs text-amber-300">{gerando.aviso}</p>}
              {corrigindo && <SinapseCorrecao t={corrigindo} />}

              {pronta && (
                <div className="flex items-center gap-2 rounded-xl border border-ok/55 bg-ok/[.08] px-3.5 py-2.5 text-[13px] text-ok">
                  <Check className="size-4 shrink-0" />
                  <span className="min-w-0 flex-1">Prova pronta.</span>
                  <button className={btnPrimary} onClick={() => fazer(pronta)}>Fazer agora</button>
                  <button className="text-faint hover:text-fg" title="Dispensar" onClick={() => setPronta(null)}><X className="size-4" /></button>
                </div>
              )}

              {provas.map((p) => (
                <div key={p.message_id} className="rounded-xl border border-line bg-surface px-[18px] py-4 text-xs">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <div className="min-w-0 flex-1">
                    <p className="text-[14px] font-semibold text-fg">{p.titulo}</p>
                    <p className="mt-0.5 text-[12px] text-faint">{quando(p.criado)} · {p.status === "rodando" ? "gerando…" : p.status === "aguardando"
                      ? "esperando o Claude" : `${p.n}${pedidas(p.config) > p.n ? ` de ${pedidas(p.config)}` : ""} questões`}{p.config ? ` · ${resumoCfg(p.config)}` : ""}
                      {p.config?.tempo ? ` · ${p.config.tempo} min` : ""}{p.motor === "claude" ? " · feita pelo Claude" : ""}
                    {p.status !== "pronto" && p.status !== "rodando" && p.status !== "aguardando" && (
                      <span className={`ml-1.5 ${p.status === "erro" ? "text-red-300" : "text-amber-300"}`}>{p.status}</span>
                    )}</p>
                    </div>
                    <div className="ml-auto flex gap-2">
                      {p.n > 0 && p.status !== "rodando" && p.status !== "aguardando" && <>
                        <button className={btn} onClick={() => fazer(p.message_id, true)}
                                title="Uma questão por vez, com a correção na hora e até 3 dicas por questão">
                          <Lampada className="size-3.5" /> Treinar
                        </button>
                        <button className={p.tentativas.length ? btn : btnPrimary} onClick={() => fazer(p.message_id)}>
                          {p.tentativas.length ? <><Refresh className="size-3.5" /> Refazer</> : "Fazer a prova"}
                        </button>
                      </>}
                      <button className="rounded-md p-1.5 text-faint hover:bg-raised hover:text-fg" title="Apagar a prova e as entregas dela"
                              onClick={() => apagar(p.message_id)}><Trash className="size-3.5" /></button>
                    </div>
                  </div>
                  {!!p.tentativas.length && (
                    <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-3">
                      {p.tentativas.map((t, i) => (
                        <button key={t.message_id} onClick={() => verResultado(t.message_id)} title="Ver a correção e as explicações"
                                className="rounded-[9px] border border-line px-[11px] py-[5px] hover:border-focus hover:bg-raised">
                          <span className="text-faint">{i + 1}ª{t.modo === "treino" ? " treino" : ""} · </span>
                          <span className={`font-mono text-[13px] font-semibold ${t.nota >= 7 ? "text-ok" : t.nota >= 5 ? "text-amber-300" : "text-red-300"}`}>{nota(t.nota)}</span>
                          <span className="font-mono text-faint">/10{t.criado ? ` · ${new Date(t.criado).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })}` : ""}</span>
                          {t.status !== "pronto" && <span className="text-faint"> · {t.status}</span>}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </>
          )}
        </div>
      </div>

      {vista.tipo === "lista" && (
        <div className="shrink-0 px-5 pb-4">
          <div className="mx-auto max-w-[796px]">
            {props.painelModelos}
            {geral && !!ms.length && (
              <div className={`${card} mb-2 flex flex-col gap-1.5 text-xs`}>
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-semibold text-fg">Quantas de cada matéria</span>
                  <span className="font-mono text-faint">{total} questões</span>
                  <div className="ml-auto flex rounded-full border border-line p-0.5" role="radiogroup" aria-label="Distribuição">
                    {([["peso", "Pelo peso", "Na proporção do peso de cada matéria"],
                       ["fracos", "Mais dos pontos fracos", "O peso vezes o que falta acertar: a matéria fraca leva mais"]] as const).map(([id, nome, dica]) => (
                      <button key={id} role="radio" aria-checked={(cfg.distribuicao ?? "peso") === id} title={dica}
                              onClick={() => setCfg((c) => ({ ...c, distribuicao: id }))}
                              className={`rounded-full px-2.5 py-0.5 ${(cfg.distribuicao ?? "peso") === id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
                        {nome}
                      </button>
                    ))}
                  </div>
                </div>
                {ms.map((m) => {
                  const fatia = pesoDe(m) / somaPeso;
                  return (
                    <div key={m.id} className="flex items-center gap-3">
                      <span className={`size-2 shrink-0 rounded-full ${corAcerto(m.acerto)}`} />
                      <span className="w-[170px] shrink-0 truncate text-muted" title={m.nome}>{m.nome}</span>
                      <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-raised">
                        <div className="h-full rounded-full bg-accent/70 transition-[width] duration-[250ms]" style={{ width: `${Math.round(fatia * 100)}%` }} />
                      </div>
                      <span className="w-28 shrink-0 whitespace-nowrap text-right font-mono text-faint">≈ {Math.round(fatia * total)} questões</span>
                    </div>
                  );
                })}
              </div>
            )}
            {!geral && topicosAberto && (
              <div className={`${card} mb-2 flex flex-col gap-2 text-xs`}>
                <div className="flex items-center">
                  <span className="font-medium text-fg">Tópicos da prova</span>
                  <span className="ml-2 text-faint">nenhum marcado = todos</span>
                  <button onClick={() => setTopicosAberto(false)} title="Fechar" className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg">
                    <X className="size-3.5" />
                  </button>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {topicosDisp.map((t) => {
                    const ligado = escolhidos.includes(t);
                    return (
                      <button key={t} aria-pressed={ligado} className={`${pilula} ${ligado ? pilulaLigada : ""}`}
                              onClick={() => setCfg((c) => ({ ...c, topicos: ligado ? escolhidos.filter((x) => x !== t) : [...escolhidos, t] }))}>
                        {t}
                      </button>
                    );
                  })}
                  {!topicosDisp.length && <span className="text-faint">Gere o resumo para escolher por tópico.</span>}
                </div>
              </div>
            )}
            <CaixaPrompt>
              <textarea rows={1} value={instrucoes} onChange={(e) => setInstrucoes(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); gerar(); } }}
                        placeholder="Algo específico para esta prova? (opcional — ex.: mais questões de cálculo)" className={campoPrompt} />
              <RodapePrompt>
                <Numero valor={cfg.me ?? 0} min={0} max={40} unidade="múltipla escolha" dica="Questões de múltipla escolha"
                        onChange={(me) => setCfg((c) => ({ ...c, me }))} />
                <Numero valor={cfg.vf ?? 0} min={0} max={40} unidade="V/F" dica="Questões de verdadeiro ou falso"
                        onChange={(vf) => setCfg((c) => ({ ...c, vf }))} />
                <Numero valor={cfg.disc ?? 0} min={0} max={20} unidade="discursivas" dica="Questões abertas, corrigidas pela IA com rubrica"
                        onChange={(disc) => setCfg((c) => ({ ...c, disc }))} />
                <Menu title="Dificuldade" items={DIFICULDADES} value={cfg.dificuldade ?? "mista"}
                      onChange={(dificuldade) => setCfg((c) => ({ ...c, dificuldade }))} button={(label) => <>{label}</>} />
                {!geral && <button className={`${pilula} ${topicosAberto ? pilulaLigada : ""}`} onClick={() => setTopicosAberto((v) => !v)}
                        title="Escolher os tópicos da prova">
                  Tópicos {escolhidos.length ? `${escolhidos.length}/${topicosDisp.length}` : "· todos"}
                </button>}
                {!geral && <button className={`${pilula} ${cfg.estilo && temSimulado ? pilulaLigada : ""}`} disabled={!temSimulado}
                        aria-pressed={!!cfg.estilo && temSimulado} onClick={() => setCfg((c) => ({ ...c, estilo: !c.estilo }))}
                        title={temSimulado ? "Imitar o jeito das provas anexadas (banca, formato, enunciado)" : "Anexe uma prova ou simulado (marcado como Prova) para usar"}>
                  <Check className={`size-3.5 ${cfg.estilo && temSimulado ? "" : "opacity-30"}`} /> Estilo do simulado
                </button>}
                {!geral && maxFiguras > 0 && (
                  <Numero valor={comFigura} min={0} max={maxFiguras} unidade="com figura" icone={<Image className="size-3.5" />}
                          dica={`Questões que usam uma figura do PDF (gráfico, diagrama, tabela, tirinha) — ${figs.detectadas} recortada(s)`
                                + `${figs.olhadas ? `, ${figs.uteis} que servem` : ""}. Precisa de um modelo que enxerga (Qwen3.6, Gemma 4…).`}
                          onChange={(figuras) => setCfg((c) => ({ ...c, figuras }))} />
                )}
                <Numero valor={cfg.tempo ?? 0} min={0} max={600} unidade="min" icone={<Clock className="size-3.5" />}
                        dica="Tempo de prova (0 = sem cronômetro); acabou, entrega sozinha" onChange={(tempo) => setCfg((c) => ({ ...c, tempo }))} />
                <DireitaPrompt>
                  {props.botaoModelos}
                  <BotaoEnviar rodando={gerando?.status === "rodando"} onParar={parar} onEnviar={() => gerar()} titulo={geral ? "Gerar o simulado geral" : "Gerar prova"}
                               desabilitado={!total || total > 40 || !!gerando} />
                </DireitaPrompt>
              </RodapePrompt>
            </CaixaPrompt>
          </div>
        </div>
      )}
    </div>
  );
}

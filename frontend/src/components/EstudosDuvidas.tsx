import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import type { EstudosDuvidaMsg, EstudosProjeto } from "../types";
import { Bubble, Lampada } from "./icons";
import { Markdown } from "./MessageView";
import { BotaoEnviar, CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt, pilula } from "./Composer";
import { matematica } from "./estudosTexto";
import { PEDIDO_CLAUDE, type Modelos, btn, card, motorDe, rotulo } from "./estudosUi";

export type Pendente = { pergunta: string; trecho: string };

/** Uma conversa de dúvidas: a geral da matéria ou a de uma questão corrigida (`questao`). */
export function ConversaDuvida(props: {
  conv: number;
  fio: string;
  questao?: { tentativa_id: number; questao_id: string };
  carimbo?: string;
  modelos: Modelos;
  sugestoes?: string[];
  pendente?: Pendente | null;   // pergunta que chegou de fora (trecho marcado no resumo): vai sozinha uma vez
  onPendenteUsado?: () => void;
  compacta?: boolean;           // dentro do cartão da questão: sem a caixa de prompt grande
  extra?: React.ReactNode;      // o que fica à direita da caixa grande, antes de enviar (o seletor de modelo)
  dica?: boolean;               // modo treino: só o botão "Pedir uma dica" (até 3), sem as perguntas na tela
  onError: (e: string) => void;
}) {
  const [msgs, setMsgs] = useState<EstudosDuvidaMsg[]>([]);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const corte = useRef<AbortController | null>(null);
  const ouvindo = useRef(0);
  const fim = useRef<HTMLDivElement>(null);
  const aoErro = useRef(props.onError);
  aoErro.current = props.onError;

  const carregar = useCallback(async () => {
    try {
      setMsgs(await api.get<EstudosDuvidaMsg[]>(`/estudos/${props.conv}/duvidas?fio=${encodeURIComponent(props.fio)}`));
    } catch (e: any) {
      aoErro.current(e.message);
    }
  }, [props.conv, props.fio]);

  /** Resposta em andamento (aberta no meio, ou disparada no celular): acompanha pelo SSE. */
  const ouvir = useCallback(async (id: number) => {
    if (ouvindo.current === id) return;
    ouvindo.current = id;
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    try {
      await streamSSE(`/estudos/execucao/${id}/stream`, { signal: ctl.signal }, (ev) => {
        if (ev.erro || ctl.signal.aborted) return;
        setMsgs((ms) => ms.map((m) => (m.id === id ? { ...m, texto: ev.texto, status: ev.status, aviso: ev.aviso } : m)));
      });
    } catch {
      /* caiu a conexão: o carimbo traz o resto */
    } finally {
      if (ouvindo.current === id) ouvindo.current = 0;
    }
  }, []);

  useEffect(() => { carregar(); }, [carregar]);
  useEffect(() => () => corte.current?.abort(), []);
  // Outro aparelho perguntou, ou o Claude respondeu pelo MCP: recarrega, fora do meio de uma resposta.
  useEffect(() => {
    if (!enviando && !ouvindo.current) carregar();
  }, [props.carimbo]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const viva = msgs.find((m) => m.role === "assistant" && m.status === "rodando");
    if (viva && !enviando) ouvir(viva.id);
  }, [msgs, enviando, ouvir]);
  // "nearest" com scroll-margin num elemento de altura zero não rola no Chromium: na aba inteira vai "end"
  useEffect(() => { fim.current?.scrollIntoView({ block: props.compacta ? "nearest" : "end" }); }, [msgs.length, msgs.at(-1)?.texto]);   // eslint-disable-line react-hooks/exhaustive-deps

  const enviar = useCallback(async (pergunta: string, trecho = "") => {
    pergunta = pergunta.trim();
    if (!pergunta || enviando) return;
    setEnviando(true);
    setTexto("");
    const temp = -Date.now();
    setMsgs((ms) => [...ms,
      { id: temp, role: "user", texto: pergunta, status: "pronto", trecho, motor: "", aviso: "", modelo: "", criado: "" },
      { id: temp - 1, role: "assistant", texto: "", status: "rodando", trecho: "", motor: props.modelos.motor, aviso: "", modelo: "", criado: "" }]);
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    try {
      await streamSSE(`/estudos/${props.conv}/duvida`, { method: "POST", signal: ctl.signal, body: JSON.stringify({
        pergunta, trecho, fio: props.fio, questao: props.questao ?? null, ...motorDe(props.modelos) }) }, (ev) => {
        if (ctl.signal.aborted) return;
        if (ev.erro) return aoErro.current(ev.erro);
        setMsgs((ms) => ms.map((m) => (m.id === temp - 1 ? { ...m, texto: ev.texto, status: ev.status, aviso: ev.aviso } : m)));
      });
    } catch (e: any) {
      aoErro.current(e.message);
    } finally {
      setEnviando(false);
      if (!ctl.signal.aborted) carregar();
    }
  }, [enviando, props.conv, props.fio, props.questao, props.modelos, carregar]);

  // Pergunta que veio de fora (o "explique de outro jeito" do resumo): vai uma vez só.
  useEffect(() => {
    if (props.pendente && !enviando) {
      enviar(props.pendente.pergunta, props.pendente.trecho);
      props.onPendenteUsado?.();
    }
  }, [props.pendente]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function parar() {
    const viva = [...msgs].reverse().find((m) => m.role === "assistant" && (m.status === "rodando" || m.status === "aguardando"));
    if (viva && viva.id > 0) await api.post(`/estudos/execucao/${viva.id}/cancelar`, {}).catch(() => {});
    corte.current?.abort();
    carregar();
  }

  const respondendo = enviando || msgs.some((m) => m.role === "assistant" && (m.status === "rodando" || m.status === "aguardando"));
  const dadas = msgs.filter((m) => m.role === "assistant");

  const campo = (
    <textarea rows={props.compacta ? 1 : 2} value={texto} onChange={(e) => setTexto(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); enviar(texto); } }}
              placeholder={props.compacta ? "Pergunte sobre esta questão…" : "Qual a sua dúvida sobre a matéria?"}
              className={campoPrompt} />
  );

  return (
    <div className="flex flex-col gap-2">
      {msgs.map((m) => m.role === "user" ? (props.dica ? null :
        <div key={m.id} className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-raised px-3.5 py-2 text-[14px] text-fg">
          {m.trecho && <p className="mb-1 border-l-2 border-line-strong pl-2 text-xs text-muted italic">«{m.trecho}»</p>}
          <p className="whitespace-pre-wrap">{m.texto}</p>
        </div>
      ) : (
        <div key={m.id} className="max-w-full">
          {props.dica && <p className={`${rotulo} mb-1 flex items-center gap-1`}><Lampada className="size-3" /> Dica {dadas.indexOf(m) + 1}</p>}
          {m.status === "aguardando" ? (
            <p className="text-xs text-muted">Esperando o Claude — no Claude Code conectado ao Forja, peça: <span className="text-fg">“{PEDIDO_CLAUDE}”</span></p>
          ) : m.texto ? (
            <div className="[&_.md]:text-[14px]"><Markdown text={matematica(m.texto)} math /></div>
          ) : m.status === "rodando" ? (
            <p className="animate-pulse text-xs text-sky-300">pensando…</p>
          ) : null}
          {m.aviso && <p className="mt-1 text-xs text-amber-300">{m.aviso}</p>}
          {m.modelo && m.status === "pronto" && <p className="mt-1 font-mono text-[10.5px] text-faint">{m.modelo}</p>}
        </div>
      ))}
      <div ref={fim} className={props.compacta ? "" : "scroll-mb-40"} />   {/* a margem: a caixa presa no rodapé não cobre a última linha */}

      {!!props.sugestoes?.length && !respondendo && (
        <div className="flex flex-wrap gap-1.5">
          {props.sugestoes.map((s) => (
            <button key={s} className={pilula} onClick={() => enviar(s)}>{s}</button>
          ))}
        </div>
      )}

      {props.dica ? (
        dadas.length < 3 && (
          <button className={`${btn} self-start text-xs`} disabled={respondendo} onClick={() => enviar("Quero uma dica")}>
            <Lampada className="size-3.5" /> {dadas.length ? "Outra dica" : "Pedir uma dica"}
            <span className="font-mono text-[10.5px] text-faint">{dadas.length + 1}/3</span>
          </button>
        )
      ) : props.compacta ? (
        <div className="flex items-end gap-2 rounded-xl border border-line bg-surface px-3 py-2 focus-within:border-focus">
          {campo}
          <BotaoEnviar rodando={respondendo} onParar={parar} onEnviar={() => enviar(texto)} titulo="Perguntar" desabilitado={!texto.trim()} />
        </div>
      ) : (
        // presa no fim da tela enquanto a conversa rola, como a caixa das outras abas
        <div className="sticky bottom-0 bg-bg pt-2 pb-1">
          <CaixaPrompt>
            {campo}
            <RodapePrompt>
              <span className="text-xs text-faint">Dica: no resumo, selecione um trecho para pedir outra explicação.</span>
              <DireitaPrompt>
                {props.extra}
                <BotaoEnviar rodando={respondendo} onParar={parar} onEnviar={() => enviar(texto)} titulo="Perguntar" desabilitado={!texto.trim()} />
              </DireitaPrompt>
            </RodapePrompt>
          </CaixaPrompt>
        </div>
      )}
    </div>
  );
}

/** A aba Dúvidas: a conversa geral da matéria. */
export default function Duvidas(props: {
  conv: number;
  projeto: EstudosProjeto;
  carimbo?: string;
  modelos: Modelos;
  botaoModelos: React.ReactNode;
  painelModelos: React.ReactNode | null;
  pendente: Pendente | null;
  onPendenteUsado: () => void;
  onError: (e: string) => void;
}) {
  const vazia = !props.projeto.duvidas?.geral && !props.pendente;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-3">
          {props.painelModelos}
          {vazia && (
            <div className={`${card} text-xs text-muted`}>
              <p className="flex items-center gap-2 text-sm text-fg"><Bubble className="size-4" /> Tire dúvidas sobre a matéria.</p>
              <p className="mt-1">
                O professor responde com o resumo, o seu material e as páginas lidas na pesquisa. Para dúvida de uma questão,
                abra a correção na aba Provas e use “Perguntar” na própria questão. Para outra explicação de um pedaço do
                resumo, selecione o trecho lá.
              </p>
            </div>
          )}
          <ConversaDuvida conv={props.conv} fio="geral" carimbo={props.carimbo} modelos={props.modelos} extra={props.botaoModelos}
                          pendente={props.pendente} onPendenteUsado={props.onPendenteUsado} onError={props.onError}
                          sugestoes={vazia ? ["Quais são os pontos que mais caem?", "Me explique o tópico mais difícil", "Faça um resumo de 5 linhas"] : undefined} />
        </div>
      </div>
    </div>
  );
}

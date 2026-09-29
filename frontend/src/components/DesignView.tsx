import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import { BotaoEnviar, CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt } from "./Composer";
import { Undo } from "./icons";
import ModelPicker from "./ModelPicker";

// Tela Design: chat à esquerda, canvas à direita. Cada pedido gera uma versão nova do documento
// (HTML autocontido); desfazer/refazer só movem qual versão está à vista, no backend.

type Mensagem = { id: number; role: "user" | "assistant"; content: string; status: string | null; versao: number | null };
type Projeto = { conv_id: number; titulo: string; mensagens: Mensagem[]; total: number; atual: number; html: string; rodando: number | null };
type Geracao = { message_id: number; status: string; parcial?: string; tokens?: number; segundos?: number; texto?: string };

const KEY_MODELO = "forja.design.modelo";
const REDESENHO_MS = 1500;   // canvas durante a geração: re-renderiza o parcial no máximo nesse ritmo

// O canvas não tem allow-same-origin e o CSP corta qualquer rede: o design fica autocontido de
// verdade (fonte/imagem por URL simplesmente não carrega) e o script dele não sai do iframe.
const CSP = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:">`;

/** O que vai para o srcdoc. Injeta só na renderização — nunca na fonte salva. */
export function paraCanvas(html: string): string {
  const i = html.search(/<head[^>]*>/i);
  if (i < 0) return CSP + html;
  const fim = html.indexOf(">", i) + 1;
  return html.slice(0, fim) + CSP + html.slice(fim);
}

/** Parcial do streaming: do começo do documento em diante (o navegador fecha o que faltar). */
const parcialDoc = (t: string) => {
  const i = t.search(/<!doctype html|<html[\s>]/i);
  return i < 0 ? "" : t.slice(i);
};

export default function DesignView(props: {
  conv: number | null;
  carimbo?: string;   // activity.lista: projeto mexido em outro lugar recarrega aqui
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
}) {
  const [projeto, setProjeto] = useState<Projeto | null>(null);
  const [texto, setTexto] = useState("");
  const [geracao, setGeracao] = useState<Geracao | null>(null);
  const [canvas, setCanvas] = useState("");   // parcial já desenhado (throttle)
  const [modelo, setModelo] = useState(() => {
    try {
      const salvo = JSON.parse(localStorage.getItem(KEY_MODELO) ?? "null");
      if (salvo?.model) return salvo as { provider: string; model: string };
    } catch {
      /* storage corrompido: cai no modelo do chat */
    }
    return { provider: props.provider, model: props.model };
  });
  const corte = useRef<AbortController | null>(null);
  const ouvindo = useRef(0);
  const convDoStream = useRef<number | null>(null);
  const fimChat = useRef<HTMLDivElement>(null);
  const aoErro = useRef(props.onError);
  aoErro.current = props.onError;
  const rodando = geracao?.status === "rodando";

  const carregar = useCallback(async (id: number | null) => {
    if (id === null) return setProjeto(null);
    try {
      setProjeto(await api.get<Projeto>(`/design/${id}`));
    } catch (e: any) {
      aoErro.current(e.message);
    }
  }, []);

  /** Acompanha uma geração (a que esta tela disparou ou uma que já estava rodando ao abrir). */
  const ouvir = useCallback(async (path: string, init: RequestInit, conv: number) => {
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    convDoStream.current = conv;
    try {
      await streamSSE(path, { ...init, signal: ctl.signal }, (ev) => {
        if (ctl.signal.aborted) return;
        if (ev.erro) aoErro.current(ev.erro);
        else setGeracao(ev);
      });
    } catch (e: any) {
      if (!ctl.signal.aborted) aoErro.current(e.message);
    } finally {
      if (!ctl.signal.aborted) {
        convDoStream.current = null;
        ouvindo.current = 0;
        setGeracao(null);
        carregar(conv);
      }
    }
  }, [carregar]);

  // Trocar de projeto larga o stream do anterior. O primeiro pedido cria o projeto e muda `conv`
  // já com o stream dele aberto: esse fica.
  useEffect(() => {
    if (convDoStream.current !== props.conv) {
      corte.current?.abort();
      ouvindo.current = 0;
      setGeracao(null);
    }
    carregar(props.conv);
  }, [props.conv, carregar]);

  // Outro aparelho mexeu na lista (inclusive neste projeto): recarrega, fora do meio de uma geração.
  useEffect(() => {
    if (props.conv !== null && !ouvindo.current) carregar(props.conv);
  }, [props.carimbo]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Projeto aberto com geração em andamento (disparada em outra aba ou antes de sair): volta a ouvir.
  useEffect(() => {
    const mid = projeto?.rodando;
    if (mid && ouvindo.current !== mid && props.conv !== null) {
      ouvindo.current = mid;
      ouvir(`/design/${mid}/stream`, {}, props.conv);
    }
  }, [projeto?.rodando, props.conv, ouvir]);

  useEffect(() => () => corte.current?.abort(), []);
  useEffect(() => localStorage.setItem(KEY_MODELO, JSON.stringify(modelo)), [modelo]);
  useEffect(() => fimChat.current?.scrollIntoView({ block: "end" }), [projeto?.mensagens.length, rodando]);

  // Throttle do parcial: srcdoc novo recarrega o iframe inteiro, então não a cada token.
  const parcial = geracao?.parcial ?? "";
  const ultimo = useRef(0);
  useEffect(() => {
    if (!rodando) return;
    const doc = parcialDoc(parcial);
    const espera = REDESENHO_MS - (Date.now() - ultimo.current);
    const t = setTimeout(() => {
      ultimo.current = Date.now();
      setCanvas(doc);
    }, Math.max(0, espera));
    return () => clearTimeout(t);
  }, [parcial, rodando]);

  async function enviar() {
    const pedido = texto.trim();
    if (!pedido || rodando) return;
    try {
      const id = await props.ensureConversation();
      setTexto("");
      setCanvas("");
      // o pedido aparece já no chat, antes do primeiro retrato do SSE
      setProjeto((p) => p && { ...p, mensagens: [...p.mensagens, { id: -1, role: "user", content: pedido, status: null, versao: null }] });
      setGeracao({ message_id: 0, status: "rodando", parcial: "", tokens: 0, segundos: 0 });
      ouvindo.current = -1;
      await ouvir(`/design/${id}/gerar`, { method: "POST", body: JSON.stringify({ pedido, ...modelo }) }, id);
      props.onConversationChanged();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function cancelar() {
    if (geracao?.message_id) await api.post(`/design/${geracao.message_id}/cancelar`, {}).catch(() => {});
  }

  async function ir(versao: number) {
    if (!projeto || rodando || versao < 1 || versao > projeto.total || versao === projeto.atual) return;
    try {
      setProjeto(await api.post<Projeto>(`/design/${projeto.conv_id}/ir`, { versao }));
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function restaurar() {
    if (!projeto || rodando) return;
    try {
      setProjeto(await api.post<Projeto>(`/design/${projeto.conv_id}/restaurar`, { versao: projeto.atual }));
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  // Ctrl+Z / Ctrl+Shift+Z (ou Ctrl+Y) fora dos campos de texto.
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || !projeto) return;
      const alvo = e.target as HTMLElement;
      if (alvo.closest("input, textarea, [contenteditable]")) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) ir(projeto.atual - 1);
      else if (k === "y" || (k === "z" && e.shiftKey)) ir(projeto.atual + 1);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  });

  const atual = projeto?.atual ?? 0;
  const total = projeto?.total ?? 0;
  const html = rodando ? canvas : projeto?.html ?? "";
  const btn = "grid size-8 place-items-center rounded-lg text-muted hover:bg-raised hover:text-fg disabled:opacity-30 disabled:hover:bg-transparent";

  return (
    <div className="flex h-full min-h-0">
      {/* Chat */}
      <div className="flex w-[35%] min-w-[300px] flex-col border-r border-line">
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {!projeto?.mensagens.length && (
            <div className="rounded-xl border border-line bg-surface p-3.5 text-xs text-muted">
              <p className="text-sm text-fg">Descreva um site ou uma landing page.</p>
              <p className="mt-1">A IA gera um documento HTML único, que aparece no canvas. Cada pedido depois vira
                uma versão nova; Ctrl+Z e Ctrl+Shift+Z andam no histórico.</p>
            </div>
          )}
          <div className="flex flex-col gap-2.5">
            {projeto?.mensagens.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="ml-8 self-end rounded-2xl rounded-br-md bg-raised px-3 py-2 text-sm whitespace-pre-wrap text-fg">
                  {m.content}
                </div>
              ) : m.status === "running" ? null : m.versao ? (
                <button key={m.id} onClick={() => ir(m.versao!)} disabled={rodando}
                        title="Mostrar esta versão no canvas"
                        className={`self-start rounded-xl border px-3 py-1.5 text-left text-xs ${
                          m.versao === atual ? "border-accent-line bg-accent-soft text-accent-text" : "border-line text-muted hover:text-fg"}`}>
                  {m.content}
                </button>
              ) : (
                <div key={m.id} className={`self-start text-xs ${m.status === "erro" ? "text-red-300" : "text-faint"}`}>{m.content}</div>
              ),
            )}
            {rodando && (
              <div className="self-start text-xs text-muted">
                <span className="animate-pulse">Gerando…</span>{" "}
                {!!geracao.tokens && `${geracao.tokens.toLocaleString("pt-BR")} tokens · `}
                {Math.round(geracao.segundos ?? 0)} s
              </div>
            )}
            <div ref={fimChat} />
          </div>
        </div>
        <div className="px-3 pb-3">
          <CaixaPrompt>
            <textarea
              rows={2}
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  enviar();
                }
              }}
              placeholder={total ? "O que mudar?" : "Ex.: landing page de uma padaria artesanal"}
              className={campoPrompt}
            />
            <RodapePrompt>
              <DireitaPrompt>
                <ModelPicker provider={modelo.provider} model={modelo.model}
                             onChange={(provider, model) => setModelo({ provider, model })} />
                <BotaoEnviar rodando={rodando} onParar={cancelar} onEnviar={enviar}
                             desabilitado={!texto.trim()} titulo={total ? "Pedir mudança" : "Gerar"} />
              </DireitaPrompt>
            </RodapePrompt>
          </CaixaPrompt>
        </div>
      </div>

      {/* Canvas */}
      <div className="flex min-w-0 flex-1 flex-col bg-side">
        <div className="flex h-11 shrink-0 items-center gap-1 border-b border-line px-3 text-xs text-muted">
          <button className={btn} title="Desfazer · Ctrl+Z" disabled={rodando || atual <= 1} onClick={() => ir(atual - 1)}>
            <Undo />
          </button>
          <button className={btn} title="Refazer · Ctrl+Shift+Z" disabled={rodando || atual >= total} onClick={() => ir(atual + 1)}>
            <Undo className="size-4 -scale-x-100" />
          </button>
          <span className="ml-1.5">{total ? `v${atual} de ${total}` : "sem versões"}</span>
          {atual > 0 && atual < total && !rodando && (
            <button onClick={restaurar} title="Copia esta versão para o topo do histórico"
                    className="ml-2 rounded-lg border border-line px-2 py-1 text-fg hover:bg-raised">
              Restaurar como v{total + 1}
            </button>
          )}
          {rodando && <span className="ml-auto text-faint">o canvas mostra o parcial enquanto gera</span>}
        </div>
        <div className="min-h-0 flex-1 p-3">
          {html ? (
            <iframe title="Canvas do design" sandbox="allow-scripts" srcDoc={paraCanvas(html)}
                    className="size-full rounded-lg border border-line bg-white" />
          ) : (
            <div className="grid size-full place-items-center rounded-lg border border-dashed border-line text-sm text-faint">
              {rodando ? "Esperando o começo do documento…" : "O design aparece aqui."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

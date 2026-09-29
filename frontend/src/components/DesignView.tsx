import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import { BotaoEnviar, CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt } from "./Composer";
import { type Modo, type NoCaminho, enviar as paraIframe, lerMensagem, paraCanvas } from "./designCanvas";
import { Mira, Undo, X } from "./icons";
import ModelPicker from "./ModelPicker";

// Tela Design: chat à esquerda, canvas à direita. Cada pedido gera uma versão nova do documento
// (HTML autocontido); desfazer/refazer só movem qual versão está à vista, no backend. Com elementos
// selecionados no canvas, o pedido vira edição de fragmento e o canvas troca só esses nós.

type Mensagem = {
  id: number; role: "user" | "assistant"; content: string; status: string | null; versao: number | null;
  fids?: string[]; entrada?: number | null;
};
type Projeto = { conv_id: number; titulo: string; mensagens: Mensagem[]; total: number; atual: number; html: string; rodando: number | null };
type Patch = { fid: string; html: string };
type Geracao = {
  message_id: number; status: string; modo?: "documento" | "fragmento"; parcial?: string; tokens?: number;
  segundos?: number; texto?: string; versao?: number | null; base?: number | null; patches?: Patch[];
};
type Selecao = { fid: string; tag: string; path: NoCaminho[] };

const KEY_MODELO = "forja.design.modelo";
const REDESENHO_MS = 1500;   // canvas durante a geração: re-renderiza o parcial no máximo nesse ritmo

/** Parcial do streaming: do começo do documento em diante (o navegador fecha o que faltar). */
const parcialDoc = (t: string) => {
  const i = t.search(/<!doctype html|<html[\s>]/i);
  return i < 0 ? "" : t.slice(i);
};

const rotulo = (n: { tag: string; cls: string }) => n.tag + (n.cls ? "." + n.cls.split(/\s+/)[0] : "");
const milhar = (n: number) => (n >= 1000 ? `${(n / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mil` : String(n));

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
  const [parcialDesenhado, setParcialDesenhado] = useState("");   // throttle do parcial
  // O que está carregado no iframe. Muda só quando precisa recarregar: patch de fragmento vai por
  // postMessage e deixa isto na versão antiga (o DOM do iframe já é o da nova).
  const [srcBase, setSrcBase] = useState("");
  const [modo, setModo] = useState<Modo>("view");
  const [selecao, setSelecao] = useState<Selecao | null>(null);
  const [modelo, setModelo] = useState(() => {
    try {
      const salvo = JSON.parse(localStorage.getItem(KEY_MODELO) ?? "null");
      if (salvo?.model) return salvo as { provider: string; model: string };
    } catch {
      /* storage corrompido: cai no modelo do chat */
    }
    return { provider: props.provider, model: props.model };
  });
  const iframe = useRef<HTMLIFrameElement>(null);
  const versaoNoCanvas = useRef(0);
  const temCanvas = useRef(false);
  const corte = useRef<AbortController | null>(null);
  const ouvindo = useRef(0);
  const convDoStream = useRef<number | null>(null);
  const ultimoEvento = useRef<Geracao | null>(null);
  const fimChat = useRef<HTMLDivElement>(null);
  const aoErro = useRef(props.onError);
  aoErro.current = props.onError;
  const rodando = geracao?.status === "rodando";
  const janela = () => iframe.current?.contentWindow;

  /** Todo projeto novo passa por aqui: decide entre trocar nós por patch ou recarregar o canvas. */
  const mostrar = useCallback((p: Projeto | null, fim?: Geracao | null) => {
    const patches = fim?.patches ?? [];
    if (p && patches.length && fim?.base === versaoNoCanvas.current && temCanvas.current) {
      patches.forEach((x) => paraIframe(janela(), { type: "patch", fid: x.fid, html: x.html }));
    } else if (!(p && p.atual === versaoNoCanvas.current && temCanvas.current)) {
      // mesma versão já no canvas (recarga pelo carimbo, patch já aplicado): não recarrega o iframe
      setSrcBase(p?.html ?? "");
      temCanvas.current = !!p?.html;
    }
    versaoNoCanvas.current = p?.atual ?? 0;
    setProjeto(p);
  }, []);

  const carregar = useCallback(async (id: number | null, fim?: Geracao | null) => {
    if (id === null) return mostrar(null);
    try {
      mostrar(await api.get<Projeto>(`/design/${id}`), fim);
    } catch (e: any) {
      aoErro.current(e.message);
    }
  }, [mostrar]);

  /** Acompanha uma geração (a que esta tela disparou ou uma que já estava rodando ao abrir). */
  const ouvir = useCallback(async (path: string, init: RequestInit, conv: number) => {
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    convDoStream.current = conv;
    ultimoEvento.current = null;
    try {
      await streamSSE(path, { ...init, signal: ctl.signal }, (ev) => {
        if (ctl.signal.aborted) return;
        if (ev.erro) aoErro.current(ev.erro);
        else {
          ultimoEvento.current = ev;
          setGeracao(ev);
        }
      });
    } catch (e: any) {
      if (!ctl.signal.aborted) aoErro.current(e.message);
    } finally {
      if (!ctl.signal.aborted) {
        convDoStream.current = null;
        ouvindo.current = 0;
        setGeracao(null);
        const fim = ultimoEvento.current as Geracao | null;   // escrito no callback do SSE
        carregar(conv, fim?.status === "ok" ? fim : null);
      }
    }
  }, [carregar]);

  // Trocar de projeto larga o stream e a seleção do anterior. O primeiro pedido cria o projeto e
  // muda `conv` já com o stream dele aberto: esse fica.
  useEffect(() => {
    if (convDoStream.current !== props.conv) {
      corte.current?.abort();
      ouvindo.current = 0;
      setGeracao(null);
      setSelecao(null);
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
  useEffect(() => paraIframe(janela(), { type: "setMode", mode: modo }), [modo]);

  // Throttle do parcial: srcdoc novo recarrega o iframe inteiro, então não a cada token.
  const parcial = geracao?.parcial ?? "";
  const ultimo = useRef(0);
  useEffect(() => {
    if (!rodando || geracao?.modo === "fragmento") return;
    const doc = parcialDoc(parcial);
    const t = setTimeout(() => {
      ultimo.current = Date.now();
      setParcialDesenhado(doc);
    }, Math.max(0, REDESENHO_MS - (Date.now() - ultimo.current)));
    return () => clearTimeout(t);
  }, [parcial, rodando, geracao?.modo]);

  async function pedir() {
    const pedido = texto.trim();
    if (!pedido || rodando) return;
    const fids = selecao ? [selecao.fid] : [];
    try {
      const id = await props.ensureConversation();
      setTexto("");
      setParcialDesenhado("");
      // o pedido aparece já no chat, antes do primeiro retrato do SSE
      setProjeto((p) => p && { ...p, mensagens: [...p.mensagens, { id: -1, role: "user", content: pedido, status: null, versao: null, fids }] });
      setGeracao({ message_id: 0, status: "rodando", modo: fids.length ? "fragmento" : "documento", parcial: "", tokens: 0, segundos: 0 });
      ouvindo.current = -1;
      await ouvir(`/design/${id}/gerar`, { method: "POST", body: JSON.stringify({ pedido, fids, ...modelo }) }, id);
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
      mostrar(await api.post<Projeto>(`/design/${projeto.conv_id}/ir`, { versao }));
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function restaurar() {
    if (!projeto || rodando) return;
    try {
      mostrar(await api.post<Projeto>(`/design/${projeto.conv_id}/restaurar`, { versao: projeto.atual }));
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  const selecionar = (fids: string[]) => paraIframe(janela(), { type: "highlight", fids });
  const alternarInspecao = () => setModo((m) => (m === "inspect" ? "view" : "inspect"));

  // Mensagens do canvas. Os handlers mudam a cada render; o ouvinte (fixo) chama o mais recente.
  const doCanvas = useRef<(e: MessageEvent) => void>(() => {});
  doCanvas.current = (e) => {
    const m = lerMensagem(e, janela());
    if (!m) return;
    if (m.type === "ready") {   // iframe (re)carregou: devolve modo e seleção (os fids são estáveis)
      paraIframe(janela(), { type: "setMode", mode: modo });
      if (selecao) selecionar([selecao.fid]);
    } else if (m.type === "select") setSelecao(m.fid ? { fid: m.fid, tag: m.tag, path: m.path } : null);
    else if (m.type === "atalho") {
      if (m.acao === "inspect") alternarInspecao();
      else if (projeto) ir(projeto.atual + (m.acao === "undo" ? -1 : 1));
    }
  };
  useEffect(() => {
    const f = (e: MessageEvent) => doCanvas.current(e);
    window.addEventListener("message", f);
    return () => window.removeEventListener("message", f);
  }, []);

  // Atalhos fora do canvas (dentro dele, o inspetor repassa por mensagem): Ctrl+Z/Ctrl+Shift+Z e
  // Ctrl+Shift+C (inspecionar, como no navegador). Campos de texto ficam com o Ctrl+Z deles.
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || !projeto) return;
      const k = e.key.toLowerCase();
      if (e.shiftKey && k === "c") alternarInspecao();
      else if ((e.target as HTMLElement).closest("input, textarea, [contenteditable]")) return;
      else if (k === "z" && !e.shiftKey) ir(projeto.atual - 1);
      else if (k === "y" || (k === "z" && e.shiftKey)) ir(projeto.atual + 1);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  });

  const atual = projeto?.atual ?? 0;
  const total = projeto?.total ?? 0;
  const html = rodando && geracao?.modo !== "fragmento" && parcialDesenhado ? parcialDesenhado : srcBase;
  const btn = "grid size-8 place-items-center rounded-lg text-muted hover:bg-raised hover:text-fg disabled:opacity-30 disabled:hover:bg-transparent";

  return (
    <div className="flex h-full min-h-0">
      {/* Chat */}
      <div className="flex w-[35%] min-w-[300px] flex-col border-r border-line">
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {!projeto?.mensagens.length && (
            <div className="rounded-xl border border-line bg-surface p-3.5 text-xs text-muted">
              <p className="text-sm text-fg">Descreva um site ou uma landing page.</p>
              <p className="mt-1">A IA gera um documento HTML único, que aparece no canvas. Depois, ligue a inspeção
                (Ctrl+Shift+C), clique num elemento e peça a mudança: só ele vai ao modelo. Ctrl+Z e Ctrl+Shift+Z
                andam no histórico.</p>
            </div>
          )}
          <div className="flex flex-col gap-2.5">
            {projeto?.mensagens.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="ml-8 self-end rounded-2xl rounded-br-md bg-raised px-3 py-2 text-sm whitespace-pre-wrap text-fg">
                  {!!m.fids?.length && (
                    <span className="mr-1.5 rounded-md bg-accent-soft px-1.5 py-px font-mono text-[11px] text-accent-text">
                      {m.fids.length === 1 ? "1 elemento" : `${m.fids.length} elementos`}
                    </span>
                  )}
                  {m.content}
                </div>
              ) : m.status === "running" ? null : m.versao ? (
                <button key={m.id} onClick={() => ir(m.versao!)} disabled={rodando}
                        title={`Mostrar esta versão no canvas${m.entrada ? `\nEnviado ao modelo: ${m.entrada.toLocaleString("pt-BR")} caracteres` : ""}`}
                        className={`self-start rounded-xl border px-3 py-1.5 text-left text-xs ${
                          m.versao === atual ? "border-accent-line bg-accent-soft text-accent-text" : "border-line text-muted hover:text-fg"}`}>
                  {m.content}
                  {!!m.entrada && <span className="ml-1.5 text-faint">· {milhar(m.entrada)} caracteres enviados</span>}
                </button>
              ) : (
                <div key={m.id} className={`self-start text-xs ${m.status === "erro" ? "text-red-300" : "text-faint"}`}>{m.content}</div>
              ),
            )}
            {rodando && (
              <div className="self-start text-xs text-muted">
                <span className="animate-pulse">{geracao.modo === "fragmento" ? "Editando o fragmento…" : "Gerando…"}</span>{" "}
                {!!geracao.tokens && `${geracao.tokens.toLocaleString("pt-BR")} tokens · `}
                {Math.round(geracao.segundos ?? 0)} s
              </div>
            )}
            <div ref={fimChat} />
          </div>
        </div>
        <div className="px-3 pb-3">
          <CaixaPrompt>
            {selecao && (
              <div className="mb-1.5 flex flex-wrap gap-1.5">
                <span className="inline-flex items-center gap-1 rounded-lg border border-accent-line bg-accent-soft py-0.5 pr-1 pl-2 font-mono text-[11.5px] text-accent-text"
                      title={`data-fid=${selecao.fid}`}>
                  {rotulo(selecao.path[selecao.path.length - 1] ?? { tag: selecao.tag, cls: "" })}
                  <button onClick={() => selecionar([])} title="Tirar da seleção" className="rounded p-0.5 hover:bg-raised">
                    <X className="size-3" />
                  </button>
                </span>
              </div>
            )}
            <textarea
              rows={2}
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  pedir();
                }
              }}
              placeholder={selecao ? "O que mudar neste elemento?" : total ? "O que mudar?" : "Ex.: landing page de uma padaria artesanal"}
              className={campoPrompt}
            />
            <RodapePrompt>
              <DireitaPrompt>
                <ModelPicker provider={modelo.provider} model={modelo.model}
                             onChange={(provider, model) => setModelo({ provider, model })} />
                <BotaoEnviar rodando={rodando} onParar={cancelar} onEnviar={pedir}
                             desabilitado={!texto.trim()} titulo={selecao ? "Editar o elemento" : total ? "Pedir mudança" : "Gerar"} />
              </DireitaPrompt>
            </RodapePrompt>
          </CaixaPrompt>
        </div>
      </div>

      {/* Canvas */}
      <div className="flex min-w-0 flex-1 flex-col bg-side">
        <div className="flex h-11 shrink-0 items-center gap-1 border-b border-line px-3 text-xs text-muted">
          <button className={`${btn} ${modo === "inspect" ? "bg-accent-soft! text-accent-text!" : ""}`} aria-pressed={modo === "inspect"}
                  title="Inspecionar elementos · Ctrl+Shift+C" disabled={!srcBase} onClick={alternarInspecao}>
            <Mira />
          </button>
          <span className="mx-1 h-5 w-px bg-line" />
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
          {rodando && geracao?.modo !== "fragmento" && <span className="ml-auto text-faint">o canvas mostra o parcial enquanto gera</span>}
        </div>
        {/* Breadcrumb da seleção: clicar sobe direto para aquele ancestral */}
        <nav aria-label="Caminho do elemento" className="flex h-8 shrink-0 items-center gap-1 overflow-x-auto border-b border-line px-3 font-mono text-[11.5px] whitespace-nowrap text-faint">
          {selecao ? selecao.path.map((n, i) => (
            <span key={n.fid} className="flex items-center gap-1">
              {i > 0 && <span>›</span>}
              <button onClick={() => selecionar([n.fid])} title={`data-fid=${n.fid}`}
                      className={`rounded px-1 hover:bg-raised hover:text-fg ${n.fid === selecao.fid ? "text-accent-text" : ""}`}>
                {rotulo(n)}
              </button>
            </span>
          )) : (
            <span>{modo === "inspect" ? "Clique num elemento · Alt+clique ou ↑ sobe para o pai · ↓ volta · Esc limpa" : "Nenhum elemento selecionado"}</span>
          )}
        </nav>
        <div className="min-h-0 flex-1 p-3">
          {html ? (
            <iframe ref={iframe} title="Canvas do design" sandbox="allow-scripts" srcDoc={paraCanvas(html)}
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

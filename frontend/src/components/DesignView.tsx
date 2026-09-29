import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import type { Stats } from "../types";
import { BotaoEnviar, CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt, pilula } from "./Composer";
import { type Effort, Menu, ModeEffortMenu } from "./Controls";
import DesignAjustes, { type Sistema } from "./DesignAjustes";
import DesignPlano, { type Plano } from "./DesignPlano";
import { type Item, type Modo, type NoCaminho, enviar as paraIframe, lerMensagem, paraCanvas, ponte } from "./designCanvas";
import { ArrowLeft, ArrowRight, Bubble, Check, Code, Cube, Download, Image, Mira, Split, Undo, X } from "./icons";
import { PromptRow, StatsRow, Thinking, aggregate } from "./MessageView";
import ModelPicker from "./ModelPicker";

// Tela Design: chat à esquerda (no feitio do chat do agente: bolhas, raciocínio colapsável e a linha
// de métricas), canvas à direita. Cada pedido vai pela rota mais barata (o backend decide, você pode
// forçar) e vira uma versão nova; desfazer/refazer só movem qual versão está à vista.

type Mensagem = {
  id: number; role: "user" | "assistant"; content: string; status: string | null; thinking: string;
  versao: number | null; fids: string[]; entrada?: number | null; rota?: string | null; secao?: string | null;
  stats: Stats[]; comentarios: number[]; plano: Plano | null;
};
type Comentario = {
  id: number; texto: string; fids: string[]; status: "pendente" | "aplicado" | "descartado"; orfao: boolean;
  versao_criada: number; versao_aplicada: number | null;
};
type Projeto = {
  conv_id: number; titulo: string; mensagens: Mensagem[]; total: number; atual: number; html: string;
  secoes: string[]; comentarios: Comentario[]; rodando: number | null;
  imagens: { total: number; pendentes: number; nomes: string[]; conversa: number | null; disponivel: boolean };
  sistema: string | null;
};
type Patch = { fid: string; html: string };
type Geracao = {
  message_id: number; status: string; modo?: string; parcial?: string; raciocinio?: string; tokens?: number;
  segundos?: number; texto?: string; versao?: number | null; base?: number | null; patches?: Patch[];
  vivo?: Stats; secoes?: { nome: string; status: string }[]; n?: number; doc?: string;
};
type Selecao = { fid: string; tag: string; path: NoCaminho[]; itens: Item[] };
type Par = { provider: string; model: string };
type Modelos = { plano: Par; geracao: Par; edicao: Par };
type Rota = "auto" | "tokens" | "secao" | "documento";
type Viewport = "desktop" | "tablet" | "mobile";

const KEY = "forja.design.preferencias";
const REDESENHO_MS = 1500;   // canvas durante a geração do documento: re-renderiza o parcial nesse ritmo
const CAMPO_MAX = 320;        // o campo cresce até aqui e depois rola, como no agente

const ROTAS: { id: Rota; label: string; hint: string }[] = [
  { id: "auto", label: "Rota automática", hint: "O Forja escolhe o caminho mais barato pelo pedido" },
  { id: "tokens", label: "Só tokens", hint: "Muda só o :root (cores, fontes, espaços): a página inteira acompanha" },
  { id: "secao", label: "Uma seção", hint: "Refaz ou cria só a seção citada" },
  { id: "documento", label: "Documento inteiro", hint: "Reescreve tudo — o mais caro num modelo local" },
];
const ROTULO_ROTA: Record<string, string> = {
  plano: "plano", etapas: "em etapas", fragmento: "fragmento", tokens: "só tokens", secao: "seção",
  documento: "documento inteiro", texto: "texto · sem IA", restaurar: "restauração",
};
const VIEWPORTS: { id: Viewport; label: string; largura: number | null }[] = [
  { id: "desktop", label: "Desktop", largura: null },
  { id: "tablet", label: "Tablet", largura: 768 },
  { id: "mobile", label: "Celular", largura: 375 },
];
const EXPORTS: { formato: "html" | "pdf" | "png"; fids?: boolean; label: string; hint: string }[] = [
  { formato: "html", label: "HTML limpo", hint: "Um arquivo só, sem o script do canvas e sem data-fid" },
  { formato: "html", fids: true, label: "HTML com data-fid", hint: "Mantém os ids estáveis (para voltar a editar em outro lugar)" },
  { formato: "pdf", label: "PDF", hint: "Deck: um slide por página em 1920×1080. Site: A4" },
  { formato: "png", label: "PNG", hint: "Deck: o slide atual. Site: a página inteira na largura do viewport" },
];
const MINI = 160;   // largura da miniatura de slide (px)
const ETAPAS: { id: keyof Modelos; label: string; hint: string }[] = [
  { id: "plano", label: "Plano", hint: "Propõe tokens e seções (uma chamada curta)" },
  { id: "geracao", label: "Geração", hint: "Escreve as seções e o documento inteiro — onde um modelo forte rende mais" },
  { id: "edicao", label: "Edição", hint: "Fragmentos, tokens e comentários: contexto pequeno, cabe num modelo local" },
];

/** Parcial do streaming: do começo do documento em diante (o navegador fecha o que faltar). */
const parcialDoc = (t: string) => {
  const i = t.search(/<!doctype html|<html[\s>]/i);
  return i < 0 ? "" : t.slice(i);
};
/** Quantos slides o documento tem (seções de topo com data-slide); 0 = site. */
const contaSlides = (html: string) => (html.match(/<section\b[^>]*\sdata-slide(?=[\s=>])/gi) ?? []).length;
const rotulo = (n: { tag: string; cls: string }) => n.tag + (n.cls ? "." + n.cls.split(/\s+/)[0] : "");
const milhar = (n: number) => (n >= 1000 ? `${(n / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mil` : String(n));

type Preferencias = { modelos: Modelos; esforco: Effort; sistema?: string };

function lerPreferencias(provider: string, model: string): Preferencias {
  const par = { provider, model };
  try {
    const p = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (p?.modelos?.geracao?.model) return { modelos: { plano: par, edicao: par, ...p.modelos }, esforco: p.esforco ?? "baixo", sistema: p.sistema ?? "" };
    const antigo = JSON.parse(localStorage.getItem("forja.design.modelo") ?? "null");   // fase 1: um modelo só
    if (antigo?.model) return { modelos: { plano: antigo, geracao: antigo, edicao: antigo }, esforco: "baixo" };
  } catch {
    /* storage corrompido: cai no modelo do chat */
  }
  return { modelos: { plano: par, geracao: par, edicao: par }, esforco: "baixo" };
}

export default function DesignView(props: {
  conv: number | null;
  carimbo?: string;   // activity.lista: projeto mexido em outro lugar recarrega aqui
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
  onAbrirConversa: (id: number, kind: "imagem") => void;          // fila das imagens na tela Imagens
  onMandarParaAgente: (pasta: string, texto: string) => void;     // handoff: Agente com o pedido no campo
  pastaPadrao: string;
}) {
  const [projeto, setProjeto] = useState<Projeto | null>(null);
  const [texto, setTexto] = useState("");
  const [geracao, setGeracao] = useState<Geracao | null>(null);
  const [parcialDesenhado, setParcialDesenhado] = useState("");
  const [docVivo, setDocVivo] = useState("");   // geração em etapas: o documento a cada seção pronta
  // O que está carregado no iframe. Muda só quando precisa recarregar: patch vai por postMessage e
  // deixa isto na versão antiga (o DOM do iframe já é o da nova).
  const [srcBase, setSrcBase] = useState("");
  const [chave, setChave] = useState(0);   // força recarregar o iframe (edição de texto recusada)
  const [modo, setModo] = useState<Modo>("view");
  const [selecao, setSelecao] = useState<Selecao | null>(null);
  const [aba, setAba] = useState<"chat" | "comentarios" | "ajustes" | "versoes">("chat");
  const [sistemas, setSistemas] = useState<Sistema[]>([]);
  const [pinAtivo, setPinAtivo] = useState<number | null>(null);
  const [rota, setRota] = useState<Rota>("auto");
  const [prefs, setPrefs] = useState(() => lerPreferencias(props.provider, props.model));
  const [abrirModelos, setAbrirModelos] = useState(false);
  const [viewport, setViewport] = useState<Viewport>("desktop");
  const [slides, setSlides] = useState({ atual: 1, total: 0 });
  const [abrirExport, setAbrirExport] = useState(false);
  const [exportando, setExportando] = useState("");
  const iframe = useRef<HTMLIFrameElement>(null);
  const campo = useRef<HTMLTextAreaElement>(null);
  const versaoNoCanvas = useRef(0);
  const temCanvas = useRef(false);
  const nVivo = useRef(-1);
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
    nVivo.current = -1;
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
        setDocVivo("");
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
  useEffect(() => {
    api.get<Sistema[]>("/design-sistemas").then(setSistemas).catch(() => {});
  }, []);
  useEffect(() => localStorage.setItem(KEY, JSON.stringify(prefs)), [prefs]);
  useEffect(() => fimChat.current?.scrollIntoView({ block: "end" }), [projeto?.mensagens.length, rodando, aba]);
  useEffect(() => paraIframe(janela(), { type: "setMode", mode: modo }), [modo]);

  // Campo que cresce com o texto até CAMPO_MAX, como o do agente.
  useEffect(() => {
    const el = campo.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, CAMPO_MAX)}px`;
    el.style.overflowY = el.scrollHeight > CAMPO_MAX ? "auto" : "hidden";
  }, [texto]);

  // Documento inteiro: parcial com throttle (srcdoc novo recarrega o iframe inteiro).
  const parcial = geracao?.parcial ?? "";
  const ultimo = useRef(0);
  useEffect(() => {
    if (!rodando || geracao?.modo !== "documento") return;
    const doc = parcialDoc(parcial);
    const t = setTimeout(() => {
      ultimo.current = Date.now();
      setParcialDesenhado(doc);
    }, Math.max(0, REDESENHO_MS - (Date.now() - ultimo.current)));
    return () => clearTimeout(t);
  }, [parcial, rodando, geracao?.modo]);

  // Em etapas: o canvas mostra o esqueleto e troca a cada seção que fica pronta (não a cada tick).
  useEffect(() => {
    if (geracao?.modo !== "etapas" || !geracao.doc || geracao.n === nVivo.current) return;
    nVivo.current = geracao.n ?? 0;
    setDocVivo(geracao.doc);
  }, [geracao?.modo, geracao?.n, geracao?.doc]);

  const pendentes = (projeto?.comentarios ?? []).filter((c) => c.status === "pendente" && !c.orfao);
  const numero = (id: number) => pendentes.findIndex((c) => c.id === id) + 1;
  const pinsAtuais = () => pendentes.map((c, i) => ({ fid: c.fids[0], n: i + 1 }));
  useEffect(() => paraIframe(janela(), { type: "showPins", pins: pinsAtuais() }), [projeto?.comentarios]);   // eslint-disable-line react-hooks/exhaustive-deps

  const modelosDe = () => ({ ...prefs.modelos });

  async function pedir(extra: { rota?: string; secao?: string; comentarios?: number[] } = {}) {
    const pedido = texto.trim();
    if (rodando || (!pedido && !extra.comentarios?.length && extra.rota !== "secao")) return;
    const fids = extra.comentarios || extra.rota === "secao" ? [] : selecao?.itens.map((i) => i.fid) ?? [];
    try {
      const id = await props.ensureConversation();
      setTexto("");
      setParcialDesenhado("");
      setAba("chat");
      // o pedido aparece já no chat, antes do primeiro retrato do SSE
      setProjeto((p) => p && { ...p, mensagens: [...p.mensagens, {
        id: -1, role: "user", content: pedido || (extra.comentarios ? `${extra.comentarios.length} comentário(s)` : `refazer a seção ${extra.secao}`),
        status: null, thinking: "", versao: null, fids, stats: [], comentarios: extra.comentarios ?? [], plano: null }] });
      setGeracao({ message_id: 0, status: "rodando", modo: fids.length || extra.comentarios ? "fragmento" : "", tokens: 0, segundos: 0 });
      ouvindo.current = -1;
      await ouvir(`/design/${id}/gerar`, { method: "POST", body: JSON.stringify({
        pedido, fids, rota: extra.rota ?? rota, secao: extra.secao ?? "", comentarios: extra.comentarios ?? [],
        esforco: prefs.esforco, modelos: modelosDe(), sistema: prefs.sistema ?? "", ...prefs.modelos.geracao }) }, id);
      props.onConversationChanged();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function aprovar(mid: number, plano: Plano) {
    if (rodando || !projeto) return;
    setGeracao({ message_id: mid, status: "rodando", modo: "etapas", tokens: 0, segundos: 0 });
    ouvindo.current = mid;
    await ouvir(`/design/${mid}/aprovar`, { method: "POST", body: JSON.stringify({
      plano, esforco: prefs.esforco, modelos: modelosDe(), ...prefs.modelos.geracao }) }, projeto.conv_id);
  }

  async function comentar() {
    const t = texto.trim();
    if (!t || !selecao || !projeto) return;
    try {
      mostrar(await api.post<Projeto>(`/design/${projeto.conv_id}/comentarios`, { fids: selecao.itens.map((i) => i.fid), texto: t }));
      setTexto("");
      selecionar([]);
      setAba("comentarios");
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function descartar(id: number) {
    if (!projeto) return;
    try {
      mostrar(await api.post<Projeto>(`/design/${projeto.conv_id}/comentarios/${id}/descartar`, {}));
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

  async function restaurar(versao: number) {
    if (!projeto || rodando) return;
    try {
      mostrar(await api.post<Projeto>(`/design/${projeto.conv_id}/restaurar`, { versao }));
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  /** Duplo clique no canvas: o texto vai direto para a fonte (versão nova), sem chamar o modelo. */
  async function salvarTexto(fid: string, html: string) {
    if (!projeto) return;
    try {
      const r = await api.post<{ projeto: Projeto; fim: Geracao }>(`/design/${projeto.conv_id}/texto`, { fid, html });
      mostrar(r.projeto, r.fim);
    } catch (e: any) {
      props.onError(e.message);
      setChave((k) => k + 1);   // o DOM ficou com o texto recusado: volta para a fonte
    }
  }

  /** Mudança direta, sem modelo (sliders, design system): versão nova e patch no canvas. */
  async function semIA(caminho: string, corpo: unknown) {
    if (!projeto) return;
    try {
      const r = await api.post<{ projeto: Projeto; fim: Geracao | null }>(`/design/${projeto.conv_id}/${caminho}`, corpo);
      mostrar(r.projeto, r.fim);
    } catch (e: any) {
      props.onError(e.message);
      setChave((k) => k + 1);
    } finally {
      paraIframe(janela(), { type: "setTokens", tokens: {} });   // a prévia sai: o <style> novo já tem os valores
    }
  }

  async function extrairSistema(pasta: string, nome: string) {
    try {
      const s = await api.post<Sistema>("/design-sistemas/extrair", { pasta, nome, esforco: prefs.esforco, ...prefs.modelos.edicao });
      setSistemas((l) => [...l, s]);
      setPrefs((p) => ({ ...p, sistema: p.sistema || s.id }));
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function apagarSistema(id: string) {
    try {
      setSistemas(await api.del<Sistema[]>(`/design-sistemas/${id}`));
      setPrefs((p) => ({ ...p, sistema: p.sistema === id ? "" : p.sistema }));
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  /** Slots de imagem -> fila na tela Imagens (a ferramenta da skill gerar-imagens registra). */
  async function gerarImagens() {
    if (!projeto) return;
    try {
      if (!projeto.imagens.pendentes && projeto.imagens.conversa) return props.onAbrirConversa(projeto.imagens.conversa, "imagem");
      const r = await api.post<{ id: number }>(`/design/${projeto.conv_id}/imagens`, {});
      props.onAbrirConversa(r.id, "imagem");
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  /** Handoff: pacote na pasta do projeto e o Agente aberto com o pedido pronto (você revisa e envia). */
  async function mandarParaAgente() {
    if (!projeto) return;
    setAbrirExport(false);
    const escolher = ponte()?.pickFolder;
    const pasta = escolher ? await escolher(props.pastaPadrao) : window.prompt("Pasta do projeto onde implementar:", props.pastaPadrao);
    if (!pasta) return;
    try {
      const r = await api.post<{ pasta: string; prompt: string }>(`/design/${projeto.conv_id}/handoff`, { pasta });
      props.onMandarParaAgente(r.pasta, r.prompt);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  /** Baixa a versão atual (o backend gera PDF/PNG num Chromium headless, sem rede). */
  async function exportar(formato: "html" | "pdf" | "png", fids = false) {
    if (!projeto) return;
    setAbrirExport(false);
    setExportando(formato);
    try {
      const q = new URLSearchParams({ formato, fids: String(fids), slide: String(slides.atual), viewport });
      const r = await fetch(`/api/design/${projeto.conv_id}/exportar?${q}`,
                            { headers: ponte()?.token ? { "X-Forja-Token": ponte()!.token! } : {} });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
      const disp = r.headers.get("content-disposition") ?? "";
      const nome = decodeURIComponent(disp.split("''")[1] ?? `design.${formato}`);
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = nome;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setExportando("");
    }
  }

  const irSlide = (n: number) => paraIframe(janela(), { type: "setSlide", n });
  const selecionar = (fids: string[]) => paraIframe(janela(), { type: "highlight", fids });
  const mostrarComentario = (c: Comentario) => {
    selecionar(c.fids);
    paraIframe(janela(), { type: "scrollTo", fid: c.fids[0] });
  };
  const alternarInspecao = () => setModo((m) => (m === "inspect" ? "view" : "inspect"));

  // Mensagens do canvas. Os handlers mudam a cada render; o ouvinte (fixo) chama o mais recente.
  const doCanvas = useRef<(e: MessageEvent) => void>(() => {});
  doCanvas.current = (e) => {
    const m = lerMensagem(e, janela());
    if (!m) return;
    if (m.type === "ready") {   // iframe (re)carregou: devolve modo, slide, seleção e pins (os fids são estáveis)
      paraIframe(janela(), { type: "setMode", mode: modo });
      paraIframe(janela(), { type: "setSlide", n: slides.atual });
      paraIframe(janela(), { type: "showPins", pins: pinsAtuais() });
      if (selecao) selecionar(selecao.itens.map((i) => i.fid));
    } else if (m.type === "select") setSelecao(m.fid ? { fid: m.fid, tag: m.tag, path: m.path, itens: m.itens } : null);
    else if (m.type === "textEdited") salvarTexto(m.fid, m.html);
    else if (m.type === "slides") setSlides({ atual: m.atual, total: m.total });
    else if (m.type === "pin") {
      setAba("comentarios");
      setPinAtivo(m.n);
      const c = pendentes[m.n - 1];
      if (c) selecionar(c.fids);
    } else if (m.type === "atalho") {
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
  const html = rodando && geracao?.modo === "etapas" && docVivo ? docVivo
    : rodando && geracao?.modo === "documento" && parcialDesenhado ? parcialDesenhado : srcBase;
  const secaoSel = selecao?.path.find((n) => n.sec)?.sec;
  const nSlides = contaSlides(html);
  const largura = nSlides ? null : VIEWPORTS.find((v) => v.id === viewport)!.largura;
  // miniaturas pela fonte mais nova (o iframe principal pode estar na versão antiga + patches)
  const fonteMini = rodando && docVivo ? docVivo : projeto?.html ?? "";
  const miniaturas = useMemo(() => {
    const n = contaSlides(fonteMini);
    return Array.from({ length: n }, (_, i) => paraCanvas(fonteMini, false, { slide: i + 1, largura: MINI }));
  }, [fonteMini]);
  const mensagens = projeto?.mensagens ?? [];
  const versoes = mensagens.filter((m) => m.versao).reverse();
  const btn = "grid size-8 place-items-center rounded-lg text-muted hover:bg-raised hover:text-fg disabled:opacity-30 disabled:hover:bg-transparent";
  const abaBtn = (on: boolean) => `rounded-lg px-2.5 py-1 text-xs ${on ? "bg-raised text-fg" : "text-muted hover:text-fg"}`;

  /** Bloco da resposta em andamento: raciocínio ao vivo, progresso e a linha de métricas ao vivo. */
  const aoVivo = rodando && geracao && (
    <div className="my-4">
      <Thinking text={geracao.raciocinio ?? ""} live={!geracao.parcial && !geracao.n} />
      {geracao.modo === "etapas" && geracao.secoes ? (
        <ol className="mb-2 space-y-0.5 text-[13px]">
          {geracao.secoes.map((s) => (
            <li key={s.nome} className={s.status === "ok" ? "text-muted" : s.status === "gerando" ? "text-accent-text" : s.status === "erro" ? "text-red-300" : "text-faint"}>
              <span className="mr-1.5 inline-block w-3">{s.status === "ok" ? "✓" : s.status === "gerando" ? "›" : s.status === "erro" ? "×" : "·"}</span>
              {s.nome}{s.status === "gerando" && <span className="animate-pulse"> — escrevendo…</span>}
            </li>
          ))}
        </ol>
      ) : (
        <div className="mb-2 animate-pulse text-sm text-muted">
          {geracao.modo === "fragmento" ? "Editando o fragmento…" : geracao.modo === "tokens" ? "Ajustando os tokens…"
            : geracao.modo === "secao" ? "Escrevendo a seção…" : geracao.modo === "plano" ? "Planejando…" : "Gerando…"}
        </div>
      )}
      {geracao.vivo && <StatsRow s={aggregate([geracao.vivo])} live />}
    </div>
  );

  return (
    <div className="flex h-full min-h-0">
      {/* Esquerda: chat / comentários / versões + composer */}
      <div className="flex w-[35%] min-w-[320px] flex-col border-r border-line">
        <div className="flex h-11 shrink-0 items-center gap-1 border-b border-line px-3">
          <button className={abaBtn(aba === "chat")} onClick={() => setAba("chat")}>Chat</button>
          <button className={abaBtn(aba === "comentarios")} onClick={() => setAba("comentarios")}>
            Comentários{!!pendentes.length && <span className="ml-1 rounded-full bg-amber-500/20 px-1.5 text-[10.5px] text-amber-300">{pendentes.length}</span>}
          </button>
          <button className={abaBtn(aba === "ajustes")} onClick={() => setAba("ajustes")}>Ajustes</button>
          <button className={abaBtn(aba === "versoes")} onClick={() => setAba("versoes")}>Versões{!!total && <span className="ml-1 text-faint">{total}</span>}</button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
          {aba === "chat" && (
            <>
              {!mensagens.length && (
                <div className="mt-2 rounded-xl border border-line bg-surface p-3.5 text-xs text-muted">
                  <p className="text-sm text-fg">Descreva um site ou uma landing page.</p>
                  <p className="mt-1">Primeiro vem um plano (cores, fontes e seções) para você ajustar; depois as seções
                    são escritas uma a uma. Para mudar algo, ligue a inspeção (Ctrl+Shift+C), clique no elemento
                    (Shift+clique junta vários) e peça: só ele vai ao modelo. Duplo clique num texto edita direto,
                    sem IA. Mudança de estilo geral mexe só nos tokens.</p>
                </div>
              )}
              {mensagens.map((m, i) => {
                if (m.role === "user") {
                  const pp = mensagens[i + 1]?.stats?.[0]?.prompt_proc;
                  return (
                    <div key={m.id} className="my-6 flex flex-col items-end">
                      <div className="max-w-[85%] rounded-[22px] bg-raised px-[18px] py-2.5 text-[14.5px] leading-[1.65] whitespace-pre-wrap">
                        {!!m.fids?.length && !m.comentarios?.length && (
                          <span className="mr-1.5 rounded-md bg-accent-soft px-1.5 py-px font-mono text-[11px] text-accent-text">
                            {m.fids.length === 1 ? "1 elemento" : `${m.fids.length} elementos`}
                          </span>
                        )}
                        {m.content}
                      </div>
                      {pp && pp.tokens > 0 && <PromptRow p={pp} />}
                    </div>
                  );
                }
                // a que está rodando aparece no bloco ao vivo (o plano aprovado some daqui na hora)
                if (m.status === "running" || (rodando && m.id === geracao?.message_id)) return null;
                return (
                  <div key={m.id} className="my-4">
                    <Thinking text={m.thinking} />
                    {m.plano ? (
                      <DesignPlano plano={m.plano} desabilitado={rodando} onGerar={(p) => aprovar(m.id, p)} />
                    ) : m.versao ? (
                      <button onClick={() => ir(m.versao!)} disabled={rodando}
                              title={`Mostrar esta versão no canvas${m.entrada ? `\nEnviado ao modelo: ${m.entrada.toLocaleString("pt-BR")} caracteres` : ""}`}
                              className={`rounded-xl border px-3 py-1.5 text-left text-[13px] ${
                                m.versao === atual ? "border-accent-line bg-accent-soft text-accent-text" : "border-line text-fg-2 hover:text-fg"}`}>
                        {m.content}
                      </button>
                    ) : (
                      <div className={`text-[13px] ${m.status === "erro" ? "text-red-300" : "text-faint"}`}>{m.content}</div>
                    )}
                    {(m.rota || m.entrada) && (
                      <div className="mt-1.5 font-mono text-[11px] text-faint">
                        {m.rota && (ROTULO_ROTA[m.rota] ?? m.rota) + (m.rota === "secao" && m.secao ? ` ${m.secao}` : "")}
                        {!!m.entrada && ` · ${milhar(m.entrada)} caracteres enviados`}
                      </div>
                    )}
                    {!!m.stats?.length && <div className="mt-3"><StatsRow s={aggregate(m.stats)} /></div>}
                  </div>
                );
              })}
              {aoVivo}
            </>
          )}

          {aba === "comentarios" && (
            <div className="flex flex-col gap-2 py-2">
              {pendentes.length > 1 && (
                <button disabled={rodando} onClick={() => pedir({ comentarios: pendentes.map((c) => c.id) })}
                        className="self-start rounded-[9px] bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:brightness-110 disabled:opacity-40">
                  Aplicar {pendentes.length} pendentes numa chamada
                </button>
              )}
              {!projeto?.comentarios.length && (
                <p className="text-xs text-muted">Selecione elementos no canvas, escreva e use “Comentar”: os comentários
                  ficam aqui, com pins numerados no canvas, até você aplicar um agora ou todos de uma vez.</p>
              )}
              {[...(projeto?.comentarios ?? [])].reverse().map((c) => {
                const n = numero(c.id);
                return (
                  <div key={c.id} onClick={() => mostrarComentario(c)}
                       className={`cursor-pointer rounded-xl border p-2.5 text-[13px] ${n && n === pinAtivo ? "border-amber-400/60 bg-amber-500/5" : "border-line hover:bg-raised/50"} ${c.status !== "pendente" ? "opacity-60" : ""}`}>
                    <div className="flex items-start gap-2">
                      {n > 0 ? (
                        <span className="grid size-5 shrink-0 place-items-center rounded-full bg-amber-500 font-mono text-[10.5px] font-bold text-black">{n}</span>
                      ) : c.status === "aplicado" ? <Check className="mt-0.5 size-4 shrink-0 text-emerald-300" /> : <span className="size-5 shrink-0" />}
                      <div className="min-w-0 flex-1">
                        <p className="whitespace-pre-wrap text-fg">{c.texto}</p>
                        <p className="mt-0.5 font-mono text-[11px] text-faint">
                          {c.fids.length === 1 ? "1 elemento" : `${c.fids.length} elementos`} · v{c.versao_criada}
                          {c.status === "aplicado" && ` · aplicado na v${c.versao_aplicada}`}
                          {c.status === "descartado" && " · descartado"}
                        </p>
                        {c.orfao && <p className="mt-1 text-[12px] text-amber-300">Órfão: o elemento não existe mais nesta versão.</p>}
                      </div>
                    </div>
                    {c.status === "pendente" && (
                      <div className="mt-2 flex gap-1.5 pl-7" onClick={(e) => e.stopPropagation()}>
                        {!c.orfao && (
                          <button disabled={rodando} onClick={() => pedir({ comentarios: [c.id] })}
                                  className="rounded-lg border border-line px-2 py-0.5 text-xs text-fg hover:bg-raised disabled:opacity-40">Aplicar agora</button>
                        )}
                        <button onClick={() => descartar(c.id)} className="rounded-lg px-2 py-0.5 text-xs text-muted hover:text-fg">Descartar</button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {aba === "ajustes" && (
            <DesignAjustes
              html={projeto?.html ?? ""}
              desabilitado={rodando || !projeto?.html}
              onPrevia={(tokens) => paraIframe(janela(), { type: "setTokens", tokens })}
              onSalvar={(tokens) => semIA("tokens", { tokens })}
              sistemas={sistemas}
              sistemaDoDoc={projeto?.sistema ?? null}
              sistemaNovos={prefs.sistema ?? ""}
              onSistemaNovos={(id) => setPrefs((p) => ({ ...p, sistema: id }))}
              onAplicarSistema={(id) => semIA(`sistema/${id}`, {})}
              onExtrair={extrairSistema}
              onApagar={apagarSistema}
              pastaPadrao={props.pastaPadrao}
            />
          )}

          {aba === "versoes" && (
            <div className="flex flex-col gap-1 py-2">
              {versoes.map((m) => (
                <div key={m.id} className={`group flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] ${m.versao === atual ? "bg-accent-soft text-accent-text" : "text-fg-2 hover:bg-raised"}`}>
                  <button onClick={() => ir(m.versao!)} disabled={rodando} className="min-w-0 flex-1 truncate text-left">{m.content}</button>
                  <span className="shrink-0 font-mono text-[10.5px] text-faint">{m.rota ? ROTULO_ROTA[m.rota] ?? m.rota : ""}</span>
                  {m.versao !== atual && m.versao !== total && (
                    <button onClick={() => restaurar(m.versao!)} disabled={rodando} title="Copia esta versão para o topo do histórico"
                            className="shrink-0 rounded border border-line px-1.5 text-[11px] opacity-0 group-hover:opacity-100 hover:bg-raised">Restaurar</button>
                  )}
                </div>
              ))}
              {!versoes.length && <p className="text-xs text-muted">Nenhuma versão ainda.</p>}
            </div>
          )}
          <div ref={fimChat} />
        </div>

        <div className="px-3 pb-3">
          {abrirModelos && (
            <div className="mb-2 rounded-xl border border-line bg-surface p-3 text-xs">
              <div className="mb-2 font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">Modelo por etapa</div>
              {ETAPAS.map((e) => (
                <div key={e.id} className="mb-2 flex items-center gap-2 last:mb-0 [&>div]:ml-0" title={e.hint}>
                  <span className="w-16 shrink-0 text-muted">{e.label}</span>
                  <ModelPicker provider={prefs.modelos[e.id].provider} model={prefs.modelos[e.id].model}
                               onChange={(provider, model) => setPrefs((p) => ({ ...p, modelos: { ...p.modelos, [e.id]: { provider, model } } }))} />
                </div>
              ))}
            </div>
          )}
          <CaixaPrompt>
            {!!selecao?.itens.length && (
              <div className="mb-1.5 flex flex-wrap gap-1.5">
                {selecao.itens.map((it) => (
                  <span key={it.fid} title={`data-fid=${it.fid}`}
                        className="inline-flex items-center gap-1 rounded-lg border border-accent-line bg-accent-soft py-0.5 pr-1 pl-2 font-mono text-[11.5px] text-accent-text">
                    {rotulo(it)}
                    <button onClick={() => selecionar(selecao.itens.filter((x) => x.fid !== it.fid).map((x) => x.fid))}
                            title="Tirar da seleção" className="rounded p-0.5 hover:bg-raised">
                      <X className="size-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <textarea
              ref={campo}
              rows={1}
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  pedir();
                }
              }}
              placeholder={selecao ? (selecao.itens.length > 1 ? "O que mudar nestes elementos? (ou comente)" : "O que mudar neste elemento? (ou comente)")
                : total ? "O que mudar?" : "Ex.: landing page de uma padaria artesanal"}
              className={campoPrompt}
            />
            <RodapePrompt>
              <ModeEffortMenu effort={prefs.esforco} onEffort={(esforco) => setPrefs((p) => ({ ...p, esforco }))} semExtremo />
              {!selecao && !!total && (
                <Menu title="Rota do pedido" items={ROTAS} value={rota} onChange={setRota}
                      button={(label) => (<><Split className="size-3.5" />{label}</>)} />
              )}
              {!!selecao && (
                <button className={pilula} disabled={!texto.trim()} onClick={comentar}
                        title="Guarda como comentário pendente (com pin no canvas) em vez de editar agora">
                  <Bubble className="size-3.5" /> Comentar
                </button>
              )}
              <DireitaPrompt>
                <button onClick={() => setAbrirModelos((v) => !v)}
                        title={ETAPAS.map((e) => `${e.label}: ${prefs.modelos[e.id].model || "—"}`).join("\n")}
                        className={`flex min-w-0 max-w-[min(16rem,100%)] items-center gap-1.5 overflow-hidden rounded-lg px-2.5 py-1 text-xs whitespace-nowrap ${
                          abrirModelos ? "bg-line-strong text-fg" : "bg-raised text-muted hover:text-fg"}`}>
                  <Cube className="size-3.5 shrink-0" />
                  <span className="min-w-0 truncate">{prefs.modelos.geracao.model || "escolher modelo"}</span>
                  {(prefs.modelos.edicao.model !== prefs.modelos.geracao.model || prefs.modelos.plano.model !== prefs.modelos.geracao.model) &&
                    <span className="shrink-0 text-faint">+ etapas</span>}
                </button>
                <BotaoEnviar rodando={rodando} onParar={cancelar} onEnviar={() => pedir()}
                             desabilitado={!texto.trim()} titulo={selecao ? "Editar agora" : total ? "Pedir mudança" : "Gerar"} />
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
            <button onClick={() => restaurar(atual)} title="Copia esta versão para o topo do histórico"
                    className="ml-2 rounded-lg border border-line px-2 py-1 text-fg hover:bg-raised">
              Restaurar como v{total + 1}
            </button>
          )}
          <span className="mx-1 h-5 w-px bg-line" />
          {nSlides ? (
            // deck: navegação (← → também funcionam dentro do canvas)
            <>
              <button className={btn} title="Slide anterior · ←" disabled={slides.atual <= 1} onClick={() => irSlide(slides.atual - 1)}>
                <ArrowLeft className="size-4" />
              </button>
              <span className="font-mono">{slides.atual} / {slides.total || nSlides}</span>
              <button className={btn} title="Próximo slide · →" disabled={slides.atual >= (slides.total || nSlides)} onClick={() => irSlide(slides.atual + 1)}>
                <ArrowRight className="size-4" />
              </button>
            </>
          ) : (
            <div className="flex rounded-lg border border-line p-0.5" role="radiogroup" aria-label="Viewport">
              {VIEWPORTS.map((v) => (
                <button key={v.id} role="radio" aria-checked={viewport === v.id} onClick={() => setViewport(v.id)}
                        title={v.largura ? `${v.largura} px de largura` : "Largura do canvas"}
                        className={`rounded-md px-2 py-0.5 ${viewport === v.id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
                  {v.label}
                </button>
              ))}
            </div>
          )}
          <span className="flex-1" />
          {!!projeto?.imagens.total && projeto.imagens.disponivel && (
            <button onClick={gerarImagens} disabled={rodando}
                    title={projeto.imagens.pendentes ? `Slots sem imagem: ${projeto.imagens.nomes.join(", ")}` : "Abre a conversa de Imagens deste design"}
                    className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 disabled:opacity-40 ${projeto.imagens.pendentes
                      ? "bg-accent font-medium text-accent-fg hover:brightness-110" : "border border-line text-fg hover:bg-raised"}`}>
              <Image className="size-3.5" />
              {projeto.imagens.pendentes ? `Gerar ${projeto.imagens.pendentes} ${projeto.imagens.pendentes === 1 ? "imagem" : "imagens"}` : "Ver imagens"}
            </button>
          )}
          <div className="relative">
            <button onClick={() => setAbrirExport((v) => !v)} disabled={!srcBase || !!exportando}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2 py-1 text-fg hover:bg-raised disabled:opacity-40">
              <Download className="size-3.5" /> {exportando ? `Exportando ${exportando.toUpperCase()}…` : "Exportar"}
            </button>
            {abrirExport && (
              <div className="absolute top-full right-0 z-30 mt-1 w-64 rounded-xl border border-line bg-surface p-1 shadow-xl">
                {EXPORTS.map((x) => (
                  <button key={x.label} onClick={() => exportar(x.formato, x.fids)}
                          className="block w-full rounded-lg px-2.5 py-1.5 text-left hover:bg-raised">
                    <span className="block text-[13px] text-fg">{x.label}{x.formato === "png" && nSlides ? ` · slide ${slides.atual}` : ""}</span>
                    <span className="block text-[11.5px] text-faint">{x.hint}</span>
                  </button>
                ))}
                <button onClick={mandarParaAgente} className="mt-1 block w-full rounded-lg border-t border-line px-2.5 pt-2 pb-1.5 text-left hover:bg-raised">
                  <span className="flex items-center gap-1.5 text-[13px] text-fg"><Code className="size-3.5" /> Mandar para o Agente…</span>
                  <span className="block text-[11.5px] text-faint">Grava o pacote (HTML, tokens, imagens, README) na pasta do projeto e abre o Agente com o pedido pronto</span>
                </button>
              </div>
            )}
          </div>
          {secaoSel && !rodando && (
            <button onClick={() => pedir({ rota: "secao", secao: secaoSel })}
                    title={`Gera de novo só a seção ${secaoSel} (o texto do campo, se houver, vai como pedido)`}
                    className="rounded-lg border border-line px-2 py-1 text-fg hover:bg-raised">
              Refazer seção “{secaoSel}”
            </button>
          )}
          {rodando && geracao?.modo === "documento" && <span className="text-faint">o canvas mostra o parcial enquanto gera</span>}
          {rodando && geracao?.modo === "etapas" && <span className="text-faint">{geracao.n ?? 0} de {geracao.secoes?.length ?? "?"} seções</span>}
        </div>
        {/* Breadcrumb da seleção: clicar sobe direto para aquele ancestral */}
        <nav aria-label="Caminho do elemento" className="flex h-8 shrink-0 items-center gap-1 overflow-x-auto border-b border-line px-3 font-mono text-[11.5px] whitespace-nowrap text-faint">
          {selecao ? (
            <>
              {selecao.path.map((n, i) => (
                <span key={n.fid} className="flex items-center gap-1">
                  {i > 0 && <span>›</span>}
                  <button onClick={() => selecionar([n.fid])} title={`data-fid=${n.fid}`}
                          className={`rounded px-1 hover:bg-raised hover:text-fg ${n.fid === selecao.fid ? "text-accent-text" : ""}`}>
                    {rotulo(n)}
                  </button>
                </span>
              ))}
              {selecao.itens.length > 1 && <span className="ml-2 text-accent-text">+{selecao.itens.length - 1} selecionados</span>}
            </>
          ) : (
            <span>{modo === "inspect" ? "Clique num elemento · Shift+clique junta · Alt+clique ou ↑ sobe · ↓ volta · Esc limpa · duplo clique edita o texto" : "Nenhum elemento selecionado · duplo clique num texto edita direto"}</span>
          )}
        </nav>
        <div className="flex min-h-0 flex-1 justify-center overflow-hidden p-3">
          {html ? (
            <iframe key={chave} ref={iframe} title="Canvas do design" sandbox="allow-scripts" srcDoc={paraCanvas(html)}
                    style={largura ? { width: largura } : undefined}
                    className={`h-full max-w-full rounded-lg border border-line ${largura ? "" : "w-full"} ${nSlides ? "bg-[#3a3a3a]" : "bg-white"}`} />
          ) : (
            <div className="grid size-full place-items-center rounded-lg border border-dashed border-line text-sm text-faint">
              {rodando ? "Esperando o começo do documento…" : "O design aparece aqui."}
            </div>
          )}
        </div>
        {!!miniaturas.length && (
          <div className="flex shrink-0 gap-2 overflow-x-auto border-t border-line px-3 py-2" aria-label="Slides">
            {miniaturas.map((doc, i) => (
              <button key={i} onClick={() => irSlide(i + 1)} title={`Slide ${i + 1}`}
                      style={{ width: MINI + 4, height: (MINI * 9) / 16 + 4 }}
                      className={`relative shrink-0 overflow-hidden rounded-md border-2 bg-white ${
                        slides.atual === i + 1 ? "border-accent" : "border-transparent hover:border-line-strong"}`}>
                <iframe title={`Miniatura do slide ${i + 1}`} sandbox="" srcDoc={doc} tabIndex={-1}
                        style={{ width: MINI, height: (MINI * 9) / 16 }} className="pointer-events-none block" />
                <span className="absolute bottom-0.5 left-1 rounded bg-black/60 px-1 font-mono text-[10px] text-white">{i + 1}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

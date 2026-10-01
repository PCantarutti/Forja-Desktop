import { useCallback, useEffect, useRef, useState } from "react";
import "katex/dist/katex.min.css";
import { auth, api, enviarArquivo, setMateriaEstudos, streamSSE } from "../api";
import type { EstudosEstado, EstudosMaterial, EstudosPreferencias, EstudosProjeto, PesquisaFonte } from "../types";
import { Bubble, Check, Clipboard, Copy, Cube, Download, ExternalLink, Globe, Livro, Paperclip, Search, Sliders, X } from "./icons";
import { Markdown } from "./MessageView";
import ModelPicker from "./ModelPicker";
import { BotaoEnviar, CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt, pilula, pilulaLigada, redondo } from "./Composer";
import { Menu } from "./Controls";
import { matematica, sumario } from "./estudosTexto";
import Sinapse from "./Sinapse";
import Provas, { type ProvaPendente } from "./EstudosProva";
import Duvidas, { type Pendente } from "./EstudosDuvidas";
import Revisao from "./EstudosRevisao";
import Desempenho from "./EstudosDesempenho";
import Simulados from "./EstudosSimulados";
import EstudosMapaMental from "./EstudosMapaMental";
import EstudosMaterias from "./EstudosMaterias";
import { ResumoGeral, VisaoGeral } from "./EstudosTudo";
import { LerEdital, TrazerEstudo } from "./EstudosObjetivo";
import { acharTitulo } from "./estudosMapa";
import { PEDIDO_CLAUDE, type Modelos, btn, btnPrimary, card, gravarLocal, lerLocal as ler, motorDe, numeros, relogio, rotulo } from "./estudosUi";

const KEY_PREFS = "forja.estudos.preferencias";
const KEY_MODELOS = "forja.estudos.modelos";
const KEY_MATERIA = "forja.estudos.materia.";   // + conv: a matéria aberta em cada objetivo

type Aba = "resumo" | "provas" | "simulados" | "duvidas" | "revisao" | "desempenho" | "visao" | "simulado" | "geral";
// No "Tudo" de um objetivo com matérias: a visão de cada matéria, o simulado geral e o resumo geral; a revisão e o
// desempenho valem para tudo. Resumo, provas, simulados reais e dúvidas são de uma matéria.
const ABAS_TUDO: Aba[] = ["visao", "simulado", "geral", "revisao", "desempenho"];
const SO_TUDO: Aba[] = ["visao", "simulado", "geral"];
const TEXTO_LONGO = 1500;            // colar mais que isto no campo do tema vira material

type Opcao<T extends string> = { id: T; label: string; hint: string };

const NIVEIS: Opcao<EstudosPreferencias["nivel"]>[] = [
  { id: "iniciante", label: "Iniciante", hint: "Parte do zero e explica cada termo" },
  { id: "intermediario", label: "Intermediário", hint: "Assume o básico; foco em entender e aplicar" },
  { id: "avancado", label: "Avançado", hint: "Direto ao ponto, com rigor e nuances" },
];
const OBJETIVOS: Opcao<EstudosPreferencias["objetivo"]>[] = [
  { id: "vestibular", label: "Vestibular/ENEM", hint: "O que costuma cair e de que jeito cai" },
  { id: "concurso", label: "Concurso", hint: "Definições exatas e pegadinhas de banca" },
  { id: "faculdade", label: "Faculdade", hint: "Profundidade conceitual e exercícios" },
  { id: "entender", label: "Só entender", hint: "Sem prova em vista" },
];
const TONS: Opcao<EstudosPreferencias["tom"]>[] = [
  { id: "direto", label: "Direto", hint: "Enxuto, frases curtas" },
  { id: "didatico", label: "Didático", hint: "Conversado, com analogias do dia a dia" },
  { id: "formal", label: "Formal", hint: "De livro-texto" },
];
const TAMANHOS: Opcao<EstudosPreferencias["tamanho"]>[] = [
  { id: "curto", label: "Curto", hint: "3 a 4 tópicos, 150–300 palavras cada" },
  { id: "medio", label: "Médio", hint: "5 a 7 tópicos, 300–600 palavras cada" },
  { id: "completo", label: "Completo", hint: "8 a 12 tópicos, 600–1000 palavras cada" },
];
const EXTRAS: Opcao<EstudosPreferencias["extras"][number]>[] = [
  { id: "exemplos", label: "Exemplos resolvidos", hint: "Ao menos um exemplo passo a passo por tópico" },
  { id: "mnemonicos", label: "Mnemônicos", hint: "Truques de memorização quando ajudam" },
  { id: "pegadinhas", label: "Pegadinhas", hint: "Um erro comum de prova no fim de cada tópico" },
  { id: "quadro", label: "Revisão rápida", hint: "Tabela com o que lembrar e perguntas de autoteste no fim" },
];
const PADRAO: EstudosPreferencias = { nivel: "intermediario", objetivo: "entender", tom: "didatico", tamanho: "medio",
                                      extras: ["exemplos"], observacoes: "" };

const PROFUNDIDADES: Opcao<EstudosEstado["profundidade"]>[] = [
  { id: "rapida", label: "Rápida", hint: "1 rodada, 3 páginas" },
  { id: "normal", label: "Normal", hint: "2 rodadas, 5 páginas por rodada" },
  { id: "funda", label: "Funda", hint: "4 rodadas, 8 páginas por rodada — demora" },
];

const ETAPAS: { id: EstudosEstado["etapa"]; label: string }[] = [
  { id: "material", label: "Ler material" },
  { id: "web", label: "Pesquisar" },
  { id: "plano", label: "Roteiro" },
  { id: "escrita", label: "Escrever" },
];

const MARCA_TOPICO: Record<string, string> = { fila: "·", escrevendo: "›", pronto: "✓", erro: "✕" };

// disponivel=false: este Forja não expõe o /mcp (no web o nginx não repassa e não há o interruptor) — sem a opção Claude
type McpServidor = { ligado: boolean; comando: string; disponivel?: boolean };

const FASE_ETAPA: Record<EstudosEstado["etapa"], string> = {
  material: "lendo o material", web: "pesquisando na web", plano: "montando o roteiro",
  escrita: "escrevendo os tópicos", pronto: "concluído",
};
const DO_TOPICO: Record<string, PesquisaFonte["status"]> = { fila: "fila", escrevendo: "lendo", pronto: "util", erro: "erro" };

/** O grafo da Pesquisa com os ramos do estudo: material (uma folha por arquivo), web (uma por página)
 *  e tópicos (uma por seção). Cada folha acende conforme a etapa anda. */
function sinapseDe(e: EstudosEstado) {
  const ramos = e.web ? ["Material", "Web", "Tópicos"] : ["Material", "Tópicos"];
  const folha = (id: string, ramo: number, titulo: string, status: PesquisaFonte["status"]): PesquisaFonte =>
    ({ id, rodada: ramo, titulo, status, url: "", dominio: "", erro: "", resumo: "", trecho: "" });
  const fontes = [
    ...e.materiais.map((m) => folha(`m${m.id}`, 1, m.nome,
      m.uso === "prova" || (m.pedacos && m.feitos >= m.pedacos) || e.etapa !== "material" ? "util" : "lendo")),
    ...(e.web ? e.fontes.map((f) => ({ ...f, id: `w${f.id}`, rodada: 2 })) : []),
    ...e.topicos.map((t, i) => folha(`t${i}`, ramos.length, t.titulo, DO_TOPICO[t.status] ?? "fila")),
  ];
  const estado = { status: "rodando" as const, fontes, rodadas: [], pergunta: e.titulo || e.tema,
                   fase: "pronto" as const, rodada: 0, rodadas_total: 0, stats: e.stats };
  return { estado, ramos, fase: e.status === "aguardando" ? "esperando o Claude" : FASE_ETAPA[e.etapa] };
}

function Opcoes<T extends string>(props: { titulo: string; itens: Opcao<T>[]; ligado: (id: T) => boolean;
                                             onClick: (id: T) => void }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-muted">{props.titulo}</span>
      <div className="flex flex-wrap gap-1.5">
        {props.itens.map((o) => (
          <button key={o.id} title={o.hint} aria-pressed={props.ligado(o.id)} onClick={() => props.onClick(o.id)}
                  className={`${pilula} ${props.ligado(o.id) ? pilulaLigada : ""}`}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

const tamanhoTexto = (n: number) => (n >= 1000 ? `${Math.round(n / 1000).toLocaleString("pt-BR")} mil` : String(n));

const semAspas = (s: string) => s.replace(/["'\s]/g, "").toLowerCase();

/** O CSS da página com as fontes que ela de fato carregou embutidas em data:. O Chromium que monta o PDF não
 *  alcança os arquivos da interface (no Docker, quem os serve é o nginx), então tudo vai no corpo do pedido. */
async function cssDaTela(): Promise<string> {
  const usadas = new Set<string>();
  document.fonts.forEach((f) => {
    if (f.status === "loaded") usadas.add(semAspas(`${f.family}|${f.weight}|${f.style}|${f.unicodeRange}`));
  });
  const partes: string[] = [];
  for (const folha of Array.from(document.styleSheets)) {
    let regras: CSSRuleList;
    try {
      regras = folha.cssRules;
    } catch {
      continue;   // folha de outra origem: o navegador não deixa ler
    }
    for (const r of Array.from(regras)) {
      if (!(r instanceof CSSFontFaceRule)) {
        partes.push(r.cssText);
        continue;
      }
      const s = r.style, v = (p: string, padrao: string) => s.getPropertyValue(p) || padrao;
      const chave = semAspas(`${v("font-family", "")}|${v("font-weight", "normal")}|${v("font-style", "normal")}|${v("unicode-range", "U+0-10FFFF")}`);
      const url = /url\("?([^")]+\.woff2)"?\)/.exec(v("src", ""))?.[1];
      if (!usadas.has(chave) || !url) continue;   // fonte que a tela não usou fica de fora (o PDF não precisa)
      const blob = await fetch(new URL(url, folha.href ?? location.href)).then((x) => x.blob());
      const dado = await new Promise<string>((ok) => {
        const fr = new FileReader();
        fr.onload = () => ok(String(fr.result));
        fr.readAsDataURL(blob);
      });
      partes.push(r.cssText.replace(/src:[^;]+;/, `src: url("${dado}") format("woff2");`));
    }
  }
  return partes.join("\n");
}

export default function EstudosView(props: {
  conv: number | null;
  carimbo?: string;   // activity.lista: o estudo mexido em outro lugar (celular, Claude) recarrega aqui
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
}) {
  const [projeto, setProjeto] = useState<EstudosProjeto | null>(null);
  // a matéria aberta (null = "Tudo"): vai em toda chamada pelo cabeçalho (setMateriaEstudos), e o backend filtra
  const [materia, setMateria] = useState<string | null>(null);
  const materiaAtual = useRef<string | null>(null);
  const [mapa, setMapa] = useState(() => ler(KEY_PREFS + ".mapa", { v: false }).v);   // o resumo como mapa mental
  // a seção pedida com o mapa na tela (clique num nó, ou o Sumário), aberta quando o texto voltar
  const irDepois = useRef<{ i: number; titulos: string; texto?: string } | null>(null);
  const [estado, setEstado] = useState<EstudosEstado | null>(null);
  const [tema, setTema] = useState("");
  const [prefs, setPrefs] = useState<EstudosPreferencias>(() => ler(KEY_PREFS, PADRAO));
  const [web, setWeb] = useState(() => ler(KEY_PREFS + ".web", { v: true }).v);
  const [profundidade, setProfundidade] = useState<EstudosEstado["profundidade"]>(
    () => ler(KEY_PREFS + ".profundidade", { v: "rapida" as EstudosEstado["profundidade"] }).v);
  // Sem escolha salva, segue o modelo do Chat — que chega depois do 1º render (as configurações carregam
  // assíncronas): guardar o valor inicial deixava a tela presa no modelo padrão de antes do carregamento.
  const [escolha, setEscolha] = useState<Modelos | null>(() => ler<Modelos | null>(KEY_MODELOS, null));
  const [mcp, setMcp] = useState<McpServidor | null>(null);
  const semClaude = mcp?.disponivel === false;
  const modelos: Modelos = escolha && !(semClaude && escolha.motor === "claude") ? escolha
    : { motor: "forja", escritor: escolha?.escritor ?? { provider: props.provider, model: props.model }, extrator: escolha?.extrator ?? null };
  const setModelos = (f: (m: Modelos) => Modelos) => setEscolha((m) => f(m ?? modelos));
  const [painel, setPainel] = useState<"" | "prefs" | "modelos" | "colar">("");
  const [colado, setColado] = useState("");
  const [lendo, setLendo] = useState(0);   // arquivos sendo enviados/extraídos (OCR demora)
  const [arrastando, setArrastando] = useState(false);
  const [terminou, setTerminou] = useState<EstudosEstado | null>(null);
  const [copiado, setCopiado] = useState(false);
  const [aba, setAba] = useState<Aba>("resumo");
  const [imersao, setImersao] = useState(false);   // fazendo prova ou revisando: a fila de abas sai da frente
  const [pendente, setPendente] = useState<Pendente | null>(null);   // trecho do resumo a explicar de outro jeito
  const [provaPendente, setProvaPendente] = useState<ProvaPendente | null>(null);   // a prova dos pontos fracos
  const [gerandoPdf, setGerandoPdf] = useState(false);
  const [selecao, setSelecao] = useState<{ texto: string; x: number; y: number } | null>(null);
  const statusAnterior = useRef("");
  const acompanhando = useRef(0);   // message_id ouvido por SSE; -1 = o POST de estudar está no ar
  const escolhida = useRef(0);      // versão antiga aberta pelo seletor (0 = a mais recente)
  const convAtual = useRef(props.conv);
  const corte = useRef<AbortController | null>(null);
  const resumoRef = useRef<HTMLDivElement>(null);
  // a faixa que marca a seção aberta pelo mapa ou pelo Sumário (some no próximo clique no texto)
  const [destaque, setDestaque] = useState<{ top: number; height: number } | null>(null);
  useEffect(() => {
    if (!destaque) return;
    const some = () => setDestaque(null);   // a janela mudou de largura: a faixa sairia do lugar
    window.addEventListener("resize", some);
    return () => window.removeEventListener("resize", some);
  }, [destaque]);
  const arquivo = useRef<HTMLInputElement>(null);
  const aoErro = useRef(props.onError);
  aoErro.current = props.onError;
  convAtual.current = props.conv;

  const rodando = estado?.status === "rodando";
  const aguardando = estado?.status === "aguardando";

  useEffect(() => { localStorage.setItem(KEY_PREFS, JSON.stringify(prefs)); }, [prefs]);
  useEffect(() => { localStorage.setItem(KEY_PREFS + ".web", JSON.stringify({ v: web })); }, [web]);
  useEffect(() => { localStorage.setItem(KEY_PREFS + ".profundidade", JSON.stringify({ v: profundidade })); }, [profundidade]);
  useEffect(() => { if (escolha) localStorage.setItem(KEY_MODELOS, JSON.stringify(escolha)); }, [escolha]);

  // O motor Claude depende do interruptor do MCP (a tela diz na hora se está desligado) e de o /mcp existir.
  useEffect(() => {
    api.get<McpServidor>("/mcp/servidor").then(setMcp).catch(() => setMcp(null));
  }, [modelos.motor, aguardando]);

  /** Todo retrato passa aqui: é onde o estudo que acaba (inclusive o feito pelo Claude) vira faixa. */
  const receber = useCallback((novo: EstudosEstado) => {
    setEstado(novo);
    const antes = statusAnterior.current;
    statusAnterior.current = novo.status;
    if ((antes === "rodando" || antes === "aguardando") && novo.status !== antes && novo.status !== "rodando") {
      setTerminou(novo);
    }
  }, []);

  const ouvir = useCallback(async (messageId: number) => {
    if (acompanhando.current === messageId) return;
    corte.current?.abort();
    const ctl = new AbortController();
    corte.current = ctl;
    acompanhando.current = messageId;
    try {
      await streamSSE(`/estudos/execucao/${messageId}/stream`, { signal: ctl.signal },
        (ev) => !ev.erro && !ctl.signal.aborted && receber(ev));
    } catch (e: any) {
      if (!ctl.signal.aborted) aoErro.current(e.message);
    } finally {
      if (acompanhando.current === messageId) acompanhando.current = 0;
    }
  }, [receber]);

  const carregar = useCallback(async (id: number | null) => {
    if (id === null) return;
    try {
      const p = await api.get<EstudosProjeto>(`/estudos/${id}`);
      if (convAtual.current !== id || (p.materia ?? null) !== materiaAtual.current) return;   // trocou no meio
      setProjeto(p);
      if (acompanhando.current || escolhida.current) return;   // o SSE (ou a versão escolhida) manda no resumo
      if (p.resumo) receber(p.resumo);
      else setEstado(null);
      if (p.resumo?.status === "rodando") ouvir(p.resumo.message_id);
    } catch (e: any) {
      aoErro.current(e.message);
    }
  }, [receber, ouvir]);

  /** Esquece o resumo aberto (o SSE dele, a versão escolhida): troca de estudo ou de matéria. */
  function largar() {
    corte.current?.abort();
    acompanhando.current = 0;
    escolhida.current = 0;
    statusAnterior.current = "";
    setTerminou(null);
    setEstado(null);
    setTema("");
  }

  function usarMateria(m: string | null) {
    materiaAtual.current = m;
    setMateriaEstudos(m);
    setMateria(m);
  }

  function escolherMateria(m: string | null) {
    if (m === materiaAtual.current || props.conv === null) return;
    gravarLocal(KEY_MATERIA + props.conv, { v: m });
    largar();
    usarMateria(m);
    carregar(props.conv);
  }

  useEffect(() => {
    largar();
    setProjeto(null);
    usarMateria(props.conv === null ? null : ler<{ v: string | null } | null>(KEY_MATERIA + props.conv, null)?.v ?? null);
    carregar(props.conv);
  }, [props.conv, carregar]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Matéria que sumiu (tirada em outro aparelho) volta para o Tudo; objetivo com uma matéria só (o estudo de
  // antes das matérias) abre nela, para o que for criado já cair lá.
  useEffect(() => {
    if (!projeto || props.conv === null) return;
    const ids = projeto.materias.map((m) => m.id);
    const salva = ler<{ v: string | null } | null>(KEY_MATERIA + props.conv, null);
    if (materia !== null && !ids.includes(materia)) escolherMateria(null);
    else if (materia === null && !salva && ids.length === 1) escolherMateria(ids[0]);
  }, [projeto?.materias]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => setMateriaEstudos(null), []);   // fora da tela Estudos o cabeçalho não vai

  const tudo = materia === null && !!projeto?.materias.length;
  useEffect(() => {
    if (tudo && !ABAS_TUDO.includes(aba)) setAba("visao");
    else if (!tudo && SO_TUDO.includes(aba)) setAba("resumo");
  }, [tudo]);   // eslint-disable-line react-hooks/exhaustive-deps
  const abrirMateria = (m: string) => { escolherMateria(m); setAba("resumo"); };
  // painel do objetivo por cima das abas: ler o edital, trazer um estudo antigo
  const [objetivo, setObjetivo] = useState<"" | "edital" | "trazer">("");
  useEffect(() => setObjetivo(""), [props.conv]);
  const doEdital = projeto?.materias.find((m) => m.id === materia)?.topicos ?? [];
  const simuladoFracos = () => { setProvaPendente({ topicos: [], instrucoes: "" }); setAba("simulado"); };

  // O tema do último resumo volta para o campo ao abrir o estudo: refazer é um Enter.
  useEffect(() => {
    if (estado?.tema) setTema((t) => t || estado.tema);
  }, [estado?.message_id]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Outro aparelho (ou o Claude pelo MCP) mexeu no estudo: recarrega, fora do meio de um stream.
  useEffect(() => {
    if (props.conv !== null && !acompanhando.current) carregar(props.conv);
  }, [props.carimbo]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => corte.current?.abort(), []);

  async function anexar(files: File[]) {
    if (!files.length) return;
    try {
      const id = await props.ensureConversation();
      setLendo((n) => n + files.length);
      for (const f of files) {
        try {
          await enviarArquivo<EstudosMaterial>(`/estudos/${id}/material`, f);
        } catch (e: any) {
          props.onError(`${f.name}: ${e.message}`);
        } finally {
          setLendo((n) => n - 1);
        }
      }
      await carregar(id);
      props.onConversationChanged();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function colar(texto: string) {
    if (!texto.trim()) return;
    try {
      const id = await props.ensureConversation();
      await api.post(`/estudos/${id}/material/texto`, { texto });
      setColado("");
      setPainel("");
      await carregar(id);
      props.onConversationChanged();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function materiaDe(m: EstudosMaterial, nova: string) {
    try {
      await api.patch(`/estudos/material/${m.id}`, { materia: nova });
      await carregar(props.conv);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function usoDe(m: EstudosMaterial, uso: EstudosMaterial["uso"]) {
    try {
      await api.patch(`/estudos/material/${m.id}`, { uso });
      await carregar(props.conv);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function remover(m: EstudosMaterial) {
    try {
      await api.del(`/estudos/material/${m.id}`);
      await carregar(props.conv);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function estudar() {
    const t = tema.trim();
    if (!t || rodando) return;
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission().catch(() => {});
    try {
      const id = await props.ensureConversation();
      corte.current?.abort();
      const ctl = new AbortController();
      corte.current = ctl;
      acompanhando.current = -1;
      escolhida.current = 0;
      setTerminou(null);
      setPainel("");
      try {
        await streamSSE(`/estudos/${id}/estudar`, { method: "POST", signal: ctl.signal, body: JSON.stringify({
          tema: t, preferencias: prefs, web, profundidade, ...motorDe(modelos) }) },
          (ev) => {
            if (ctl.signal.aborted) return;
            if (ev.erro) props.onError(ev.erro);
            else receber(ev);
          });
      } finally {
        if (acompanhando.current === -1) acompanhando.current = 0;
      }
      props.onConversationChanged();
      if (!ctl.signal.aborted) carregar(id);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function parar() {
    if (estado) await api.post(`/estudos/execucao/${estado.message_id}/cancelar`, {}).catch(() => {});
    if (aguardando) carregar(props.conv);
  }

  function abrirVersao(mid: number) {
    const ultima = projeto?.resumos.at(-1)?.message_id;
    escolhida.current = mid === ultima ? 0 : mid;
    statusAnterior.current = "";
    setTerminou(null);
    ouvir(mid);
  }

  function irPara(i: number, titulos = "h2", texto?: string) {
    const todos = [...(resumoRef.current?.querySelectorAll<HTMLElement>(titulos) ?? [])];
    // do mapa: confere pelo texto (a âncora é o palpite; o renderizador pode contar um título que a árvore não viu)
    const el = todos[texto ? acharTitulo(todos.map((h) => h.textContent ?? ""), texto, i) : i];
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    // a seção inteira fica destacada: o título e tudo até o próximo título do mesmo nível ou de cima
    const nivel = Number(el.tagName[1]);
    let fim: Element = el;
    for (let x = el.nextElementSibling; x && !(/^H[1-6]$/.test(x.tagName) && Number(x.tagName[1]) <= nivel); x = x.nextElementSibling) fim = x;
    const caixa = resumoRef.current!.getBoundingClientRect();
    const a = el.getBoundingClientRect(), b = fim.getBoundingClientRect();
    setDestaque({ top: a.top - caixa.top - 8, height: b.bottom - a.top + 16 });
  }
  function verMapa(v: boolean) {
    setMapa(v);
    gravarLocal(KEY_PREFS + ".mapa", { v });
  }
  // do mapa para o texto: a seção abre depois que o texto voltou à tela
  useEffect(() => {
    if (mapa || irDepois.current == null) return;
    const { i, titulos, texto } = irDepois.current;
    irDepois.current = null;
    requestAnimationFrame(() => irPara(i, titulos, texto));
  }, [mapa]);

  function copiar() {
    if (!estado?.texto) return;
    navigator.clipboard.writeText(estado.texto);
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1500);
  }

  /** O resumo como está na tela (fórmulas já desenhadas) vira PDF no backend, com o CSS e as fontes daqui. */
  async function baixarPdf() {
    if (!resumoRef.current || !estado?.texto || gerandoPdf) return;
    setGerandoPdf(true);
    const titulo = (estado.titulo || estado.tema).replace(/[\\/:*?"<>|]+/g, "").slice(0, 60) || "resumo";
    try {
      const css = await cssDaTela();
      const r = await fetch("/api/estudos/pdf", {
        method: "POST", body: JSON.stringify({ titulo, html: resumoRef.current.innerHTML, css }),
        headers: { "Content-Type": "application/json", ...auth() } });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `${titulo}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      aoErro.current(e.message);
    } finally {
      setGerandoPdf(false);
    }
  }

  function baixar() {
    if (!estado?.texto) return;
    const url = URL.createObjectURL(new Blob([estado.texto], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(estado.titulo || estado.tema).replace(/[\\/:*?"<>|]+/g, "").slice(0, 60) || "resumo"}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const materiais = projeto?.materiais ?? [];
  const etapas = ETAPAS.filter((x) => x.id !== "web" || estado?.web);
  const atual = estado?.etapa === "pronto" ? etapas.length : etapas.findIndex((x) => x.id === estado?.etapa);
  const secoes = estado?.texto ? sumario(estado.texto) : [];
  const fontesWeb = estado?.fontes ?? [];
  const perfil = estado?.perfil;
  const resumoPrefs = [NIVEIS.find((o) => o.id === prefs.nivel), TONS.find((o) => o.id === prefs.tom),
                       TAMANHOS.find((o) => o.id === prefs.tamanho)].map((o) => o?.label).filter(Boolean).join(" · ");


  const painelModelos = (
    <div className={`${card} @container mb-2 text-xs`}>
      <div className="mb-2.5 flex items-center gap-2">
        <span className="font-medium text-fg">Quem faz o estudo</span>
        <div className="flex rounded-full border border-line p-0.5" role="radiogroup" aria-label="Motor">
          {([["forja", "Modelo do Forja"], ["claude", "Claude via MCP"]] as const).filter(([id]) => id !== "claude" || !semClaude).map(([id, nome]) => (
            <button key={id} role="radio" aria-checked={modelos.motor === id} onClick={() => setModelos((m) => ({ ...m, motor: id }))}
                    className={`rounded-[9px] px-2.5 py-0.5 ${modelos.motor === id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
              {nome}
            </button>
          ))}
        </div>
        <button onClick={() => setPainel("")} title="Fechar" className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg">
          <X className="size-3.5" />
        </button>
      </div>
      {modelos.motor === "forja" ? (
        <div className="grid gap-3 @xl:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <span className="text-muted">Resumo e prova</span>
            <div className="flex [&>div]:ml-0">
              <ModelPicker provider={modelos.escritor.provider} model={modelos.escritor.model}
                           onChange={(provider, model) => setModelos((m) => ({ ...m, escritor: { provider, model } }))} />
            </div>
            <span className="text-faint">Escreve o resumo, as questões e corrige as discursivas. Vale o melhor modelo que você tiver.</span>
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-muted">Leitura e conferência</span>
            <div className="flex flex-wrap items-center gap-2 [&>div]:ml-0">
              <div className="flex rounded-full border border-line p-0.5" role="radiogroup" aria-label="Leitura">
                {([["auto", "Automática"], ["propria", "Escolher"]] as const).map(([id, nome]) => {
                  const ligado = (id === "propria") === !!modelos.extrator;
                  return (
                    <button key={id} role="radio" aria-checked={ligado}
                            onClick={() => setModelos((m) => ({ ...m, extrator: id === "auto" ? null : m.extrator ?? { ...m.escritor } }))}
                            className={`rounded-[9px] px-2.5 py-0.5 ${ligado ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
                      {nome}
                    </button>
                  );
                })}
              </div>
              {modelos.extrator && (
                <ModelPicker provider={modelos.extrator.provider} model={modelos.extrator.model}
                             onChange={(provider, model) => setModelos((m) => ({ ...m, extrator: { provider, model } }))} />
              )}
            </div>
            <span className="text-faint">
              Tira notas do material grande, lê as páginas da web e confere o gabarito da prova resolvendo cada questão.
              Um modelo diferente do de cima pega mais erro. Automática: o subagente Rápido na leitura; na prova, o mesmo de cima.
            </span>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2 text-muted">
          <p>
            O Claude (no Claude Code ou no Claude Desktop) faz tudo com o próprio raciocínio e a própria busca:
            lê o seu material pelo MCP do Forja e grava aqui o resumo, a prova e a correção. O pedido fica esperando; no
            Claude, peça <span className="text-fg">“{PEDIDO_CLAUDE}”</span>.
          </p>
          {mcp && !mcp.ligado && <p className="text-amber-300">Desligado: ligue “Permitir que o Claude controle o Forja” em Configurações › MCP.</p>}
          {mcp?.ligado && (
            <p className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-faint" title={mcp.comando}>{mcp.comando}</span>
              <button className={btn} onClick={() => navigator.clipboard.writeText(mcp.comando)}><Copy className="size-3.5" /> Conectar no Claude Code</button>
            </p>
          )}
        </div>
      )}
    </div>
  );
  const botaoModelos = (
    <button onClick={() => setPainel(painel === "modelos" ? "" : "modelos")}
            title={modelos.motor === "claude" ? "O Claude faz o estudo pelo MCP" : `Resumo: ${modelos.escritor.model || "—"}\nLeitura: ${modelos.extrator?.model ?? "automática"}`}
            className={`flex min-w-0 max-w-[min(18rem,100%)] items-center gap-1.5 overflow-hidden rounded-lg px-2.5 py-1 text-xs whitespace-nowrap ${
              painel === "modelos" ? "bg-line-strong text-fg" : "bg-raised text-muted hover:text-fg"}`}>
      {modelos.motor === "claude" ? <Livro className="size-3.5 shrink-0" /> : <Cube className="size-3.5 shrink-0" />}
      <span className="min-w-0 truncate">{modelos.motor === "claude" ? "Claude via MCP" : modelos.escritor.model || "escolher modelo"}</span>
    </button>
  );
  const abas = (
    <div className="flex gap-1 rounded-full border border-line p-0.5 text-xs" role="tablist" aria-label="Estudos">
      {(tudo ? ([["visao", "Visão geral"], ["simulado", "Simulado geral"], ["geral", "Resumo geral"],
                 ["revisao", `Revisão${projeto?.revisao?.vencem ? ` · ${projeto.revisao.vencem}` : ""}`], ["desempenho", "Desempenho"]] as const)
        : ([["resumo", "Resumo"], ["provas", `Provas${projeto?.provas.length ? ` · ${projeto.provas.length}` : ""}`],
         ["simulados", `Simulados${projeto?.simulados?.length ? ` · ${new Set(projeto.simulados.map((x) => x.material_id)).size}` : ""}`],
         ["duvidas", `Dúvidas${projeto?.duvidas?.geral ? ` · ${projeto.duvidas.geral}` : ""}`],
         ["revisao", `Revisão${projeto?.revisao?.vencem ? ` · ${projeto.revisao.vencem}` : ""}`], ["desempenho", "Desempenho"]] as const)).map(([id, nome]) => (
        <button key={id} role="tab" aria-selected={aba === id} onClick={() => setAba(id)}
                className={`rounded-full px-3 py-1 ${aba === id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
          {nome}
        </button>
      ))}
    </div>
  );

  // A fila de abas fica num topo fixo, no mesmo lugar em toda aba (cada aba tem a sua largura de conteúdo).
  // À esquerda, as matérias do objetivo (somem fazendo prova/revisando, como as abas). A chave da matéria
  // remonta as abas: cada uma busca de novo o que é da matéria nova.
  const quadro = (conteudo: React.ReactNode) => (
    <div className="flex h-full min-h-0">
      {projeto && !imersao && props.conv !== null && (
        <EstudosMaterias conv={props.conv} projeto={projeto} materia={materia} onEscolher={(m) => { setObjetivo(""); escolherMateria(m); }}
                         onMudou={() => carregar(props.conv)} onError={props.onError}
                         onEdital={() => setObjetivo("edital")} onTrazer={() => setObjetivo("trazer")} />
      )}
      <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
        {projeto && !imersao && !objetivo && (
          <div className="shrink-0 px-5 pt-3">
            <div className="mx-auto flex max-w-6xl justify-center">{abas}</div>
          </div>
        )}
        <div key={materia ?? ""} className="flex min-h-0 flex-1 flex-col">{conteudo}</div>
      </div>
    </div>
  );

  if (objetivo === "edital" && props.conv !== null && projeto) {
    return quadro(
      <LerEdital conv={props.conv} projeto={projeto} modelos={modelos} botaoModelos={botaoModelos}
                 painelModelos={painel === "modelos" ? painelModelos : null} onError={props.onError}
                 onFechar={() => setObjetivo("")} onFeito={() => { setObjetivo(""); escolherMateria(null); carregar(props.conv); }} />,
    );
  }

  if (objetivo === "trazer" && props.conv !== null && projeto) {
    return quadro(
      <TrazerEstudo conv={props.conv} onError={props.onError} onFechar={() => setObjetivo("")}
                    onFeito={() => { setObjetivo(""); carregar(props.conv); props.onConversationChanged(); }} />,
    );
  }

  if (tudo && aba === "visao" && props.conv !== null && projeto) {
    return quadro(
      <VisaoGeral conv={props.conv} carimbo={props.carimbo} onError={props.onError} onMudou={() => carregar(props.conv)}
                  onAbrir={abrirMateria} onSimuladoFracos={simuladoFracos} onIr={setAba} />,
    );
  }

  if (tudo && aba === "geral" && props.conv !== null && projeto) {
    return quadro(<ResumoGeral conv={props.conv} carimbo={props.carimbo} onError={props.onError} onAbrir={abrirMateria} />);
  }

  if (tudo && aba === "simulado" && props.conv !== null && projeto) {
    return quadro(
      <Provas geral conv={props.conv} projeto={projeto} carimbo={props.carimbo} modelos={modelos}
              botaoModelos={botaoModelos} painelModelos={painel === "modelos" ? painelModelos : null}
              onError={props.onError} onRecarregar={() => carregar(props.conv)}
              pendente={provaPendente} onPendenteUsado={() => setProvaPendente(null)} onImersao={setImersao} />,
    );
  }

  if (aba === "revisao" && props.conv !== null && projeto) {
    return quadro(
      <Revisao conv={props.conv} projeto={projeto} modelos={modelos} botaoModelos={botaoModelos}
               painelModelos={painel === "modelos" ? painelModelos : null} onError={props.onError}
               onRecarregar={() => carregar(props.conv)} onImersao={setImersao} />,
    );
  }

  if (aba === "simulados" && props.conv !== null && projeto) {
    return quadro(
      <Simulados conv={props.conv} projeto={projeto} modelos={modelos} botaoModelos={botaoModelos}
                 painelModelos={painel === "modelos" ? painelModelos : null} onError={props.onError}
                 onRecarregar={() => carregar(props.conv)} onIrProvas={() => setAba("provas")} />,
    );
  }

  if (aba === "desempenho" && props.conv !== null && projeto) {
    return quadro(
      <Desempenho conv={props.conv} carimbo={props.carimbo} onError={props.onError} onIr={setAba}
                  onProva={(p) => { setProvaPendente(p); setAba(tudo ? "simulado" : "provas"); }} />,
    );
  }

  if (aba === "duvidas" && props.conv !== null && projeto) {
    return quadro(
      <Duvidas conv={props.conv} projeto={projeto} carimbo={props.carimbo} modelos={modelos}
               botaoModelos={botaoModelos} painelModelos={painel === "modelos" ? painelModelos : null}
               pendente={pendente} onPendenteUsado={() => setPendente(null)} onError={props.onError} />,
    );
  }

  /** Trecho selecionado no resumo: aparece o "Explicar de outro jeito" em cima dele. */
  function marcar() {
    const sel = window.getSelection();
    const texto = sel?.toString().trim() ?? "";
    if (!sel || texto.length < 12 || !resumoRef.current?.contains(sel.anchorNode)) return setSelecao(null);
    const r = sel.getRangeAt(0).getBoundingClientRect();
    setSelecao({ texto: texto.slice(0, 1500), x: r.left + r.width / 2, y: r.top });
  }

  function explicarTrecho() {
    if (!selecao) return;
    setPendente({ pergunta: "Não entendi este trecho. Explique de outro jeito, mais simples, com um exemplo.", trecho: selecao.texto });
    setSelecao(null);
    window.getSelection()?.removeAllRanges();
    setAba("duvidas");
  }

  if (aba === "provas" && props.conv !== null && projeto) {
    return quadro(
      <Provas conv={props.conv} projeto={projeto} carimbo={props.carimbo} modelos={modelos}
              botaoModelos={botaoModelos} painelModelos={painel === "modelos" ? painelModelos : null}
              onError={props.onError} onRecarregar={() => carregar(props.conv)}
              pendente={provaPendente} onPendenteUsado={() => setProvaPendente(null)} onImersao={setImersao} />,
    );
  }

  return quadro(
    <div className={`flex h-full min-h-0 flex-col ${arrastando ? "ring-2 ring-accent/50 ring-inset" : ""}`}
         onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setArrastando(true); } }}
         onDragLeave={(e) => { if (e.currentTarget === e.target) setArrastando(false); }}
         onDrop={(e) => { e.preventDefault(); setArrastando(false); anexar(Array.from(e.dataTransfer.files)); }}>
      <input ref={arquivo} type="file" multiple hidden
             accept=".pdf,.docx,.pptx,.xlsx,.csv,.txt,.md,.html,.htm,.json,.png,.jpg,.jpeg,.webp,.bmp"
             onChange={(e) => { anexar(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
      {selecao && (
        // fixo na tela, em cima do trecho; some ao rolar ou ao clicar em outro lugar
        <button onMouseDown={(e) => e.preventDefault()} onClick={explicarTrecho}
                style={{ left: selecao.x, top: Math.max(8, selecao.y - 40) }}
                className={`${btnPrimary} fixed z-40 -translate-x-1/2 text-xs shadow-lg`}>
          <Bubble className="size-3.5" /> Explicar de outro jeito
        </button>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4" onScroll={() => selecao && setSelecao(null)}>
        <div className="mx-auto flex max-w-6xl flex-col items-start gap-3 xl:flex-row">
          <div className="flex w-full min-w-0 flex-1 flex-col gap-3">
            {!estado && (
              <div className={`${card} text-xs text-muted`}>
                <p className="text-sm text-fg">Seu material, um resumo feito para você estudar.</p>
                <p className="mt-1">
                  Anexe apostilas, slides, anotações ou provas antigas (PDF, Word, PowerPoint, texto ou foto) e diga o
                  tema. A IA lê tudo, completa com pesquisa na web se você quiser e escreve um resumo didático no nível e
                  no tom que você escolher, citando a página de onde tirou cada coisa. Prova anexada vira o perfil do
                  que cai: o resumo dá prioridade a isso.
                </p>
                {!!doEdital.length && (
                  <div className="mt-3 border-t border-line pt-2.5">
                    <p className="mb-1.5 text-faint">Do edital · toque num tópico para pedir o resumo dele</p>
                    <div className="flex flex-wrap gap-1.5">
                      {doEdital.map((t) => (
                        <button key={t} className={pilula} onClick={() => setTema(t)} title="Usar como tema do resumo">{t}</button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {estado && (rodando || aguardando) && (() => {
              const s = sinapseDe(estado);
              const feitos = estado.topicos.filter((t) => t.status === "pronto").length;
              return (
                <Sinapse estado={s.estado} ramos={s.ramos} fase={s.fase}
                         meta={aguardando ? <></> : <>
                           {!!estado.topicos.length && <><span className="sin-sep">·</span>
                             <span><b>{feitos}</b> de {estado.topicos.length} tópicos</span></>}
                           {numeros(estado) && <><span className="sin-sep">·</span><span>{numeros(estado)}</span></>}
                         </>} />
              );
            })()}

            {estado && (rodando || aguardando || estado.aviso || estado.status !== "pronto") && (
              <div className={card}>
                {aguardando ? (
                  <div className="text-xs text-muted">
                    <p className="text-sm text-fg">Pedido enviado ao Claude</p>
                    <p className="mt-1">
                      No Claude Code (ou Claude Desktop) conectado ao Forja pelo MCP, peça:{" "}
                      <span className="text-fg">“{PEDIDO_CLAUDE}”</span>. Ele lê o material, pesquisa e grava o resumo
                      aqui — a tela atualiza sozinha.
                    </p>
                    {mcp && !mcp.ligado && (
                      <p className="mt-2 text-amber-300">O controle pelo Claude está desligado: ligue em Configurações › MCP.</p>
                    )}
                    <div className="mt-2 flex gap-2">
                      <button className={btn} onClick={() => navigator.clipboard.writeText(PEDIDO_CLAUDE)}>
                        <Copy className="size-3.5" /> Copiar o pedido
                      </button>
                      <button className={btn} onClick={parar}><X className="size-3.5" /> Cancelar pedido</button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                      {etapas.map((x, i) => (
                        <span key={x.id} className={i < atual ? "text-muted" : i === atual ? "text-sky-300" : "text-faint"}>
                          <span className="mr-1">{i < atual ? "✓" : i === atual ? "›" : "·"}</span>
                          {x.label}
                        </span>
                      ))}
                      {!rodando && <span className="ml-auto text-faint">{numeros(estado)}</span>}
                    </div>
                    {rodando && estado.etapa === "material" && estado.materiais.some((m) => m.pedacos > 1) && (
                      <p className="mt-2 text-xs text-faint">
                        {estado.materiais.filter((m) => m.pedacos).map((m) => `${m.nome}: ${m.feitos}/${m.pedacos} partes`).join(" · ")}
                      </p>
                    )}
                    {rodando && estado.etapa === "web" && (
                      <p className="mt-2 text-xs text-faint">
                        rodada {estado.rodada || 1} · {estado.stats.uteis} fontes úteis de {estado.fontes.length}
                      </p>
                    )}
                    {!!estado.topicos.length && (
                      <ol className="mt-2 flex flex-col gap-0.5 text-xs">
                        {estado.topicos.map((t, i) => (
                          <li key={i} className={t.status === "escrevendo" ? "text-sky-300" : t.status === "erro" ? "text-red-300"
                            : t.status === "pronto" ? "text-muted" : "text-faint"}>
                            <span className="mr-1.5 inline-block w-3 text-center">{MARCA_TOPICO[t.status]}</span>
                            {i + 1}. {t.titulo}
                          </li>
                        ))}
                      </ol>
                    )}
                  </>
                )}
                {estado.aviso && <p className="mt-2 text-xs text-amber-300">{estado.aviso}</p>}
              </div>
            )}

            {terminou && (
              <div className={`flex items-center gap-2 rounded-xl border px-3.5 py-2.5 text-[13px] ${
                terminou.status === "pronto" ? "border-ok/55 bg-ok/[.08] text-ok" : "border-warn/55 bg-warn/[.07] text-warn"}`}>
                <Check className="size-4 shrink-0" />
                <span className="min-w-0 flex-1">
                  {terminou.status === "pronto"
                    ? `Resumo pronto · ${terminou.topicos.length} tópicos${terminou.stats.segundos ? ` · ${relogio(terminou.stats.segundos)}` : ""}`
                    : `Estudo ${terminou.status}${terminou.aviso ? ` · ${terminou.aviso}` : ""}`}
                </span>
                <button className="text-faint hover:text-fg" title="Dispensar" onClick={() => setTerminou(null)}>
                  <X className="size-4" />
                </button>
              </div>
            )}

            {estado?.texto && secoes.length > 1 && (
              <div className="flex rounded-full border border-line p-0.5 text-xs self-start" role="radiogroup" aria-label="Ver o resumo como">
                {([[false, "Texto"], [true, "Mapa mental"]] as const).map(([v, nome]) => (
                  <button key={nome} role="radio" aria-checked={mapa === v} onClick={() => verMapa(v)}
                          className={`rounded-full px-3 py-1 ${mapa === v ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
                    {nome}
                  </button>
                ))}
              </div>
            )}
            {estado?.texto && mapa && secoes.length > 1 && (
              <EstudosMapaMental key={estado.message_id} md={estado.texto} tema={estado.tema}
                                 onAbrir={(i, texto) => { irDepois.current = { i, titulos: "h2, h3, h4", texto }; verMapa(false); }} />
            )}
            {estado?.texto && (
              <div ref={resumoRef} onMouseUp={marcar} onKeyUp={marcar} onMouseDown={() => setDestaque(null)}
                   className={`${card} relative px-6 py-5 ${mapa && secoes.length > 1 ? "hidden" : ""}`}>
                {destaque && (
                  <div aria-hidden className="pointer-events-none absolute inset-x-2.5 animate-[surgir_.35s_ease-out] rounded-xl bg-accent-soft ring-1 ring-accent/35"
                       style={{ top: destaque.top, height: destaque.height }} />
                )}
                <div className="relative"><Markdown text={matematica(estado.texto)} math /></div>
              </div>
            )}

            {estado && !rodando && (estado.texto || (projeto?.resumos.length ?? 0) > 1) && (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {estado.texto && <>
                  <button className={btn} onClick={copiar}>
                    {copiado ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} Copiar .md
                  </button>
                  <button className={btn} onClick={baixar}><Download className="size-3.5" /> Baixar .md</button>
                  <button className={btn} onClick={baixarPdf} disabled={gerandoPdf} title="O resumo com as fórmulas, pronto para imprimir">
                    <Download className="size-3.5" /> {gerandoPdf ? "Gerando o PDF…" : "Baixar PDF"}
                  </button>
                </>}
                {(projeto?.resumos.length ?? 0) > 1 && (
                  <select value={estado.message_id} onChange={(e) => abrirVersao(Number(e.target.value))}
                          title="Resumos anteriores deste estudo"
                          className="rounded-[9px] border border-line bg-raised px-2 py-1.5 text-xs text-fg focus:border-focus focus:outline-none">
                    {projeto!.resumos.map((r, i) => (
                      <option key={r.message_id} value={r.message_id}>
                        Versão {i + 1}{r.criado ? ` · ${new Date(r.criado).toLocaleDateString("pt-BR")}` : ""} · {r.status}
                      </option>
                    ))}
                  </select>
                )}
                <span className="text-faint">
                  {estado.motor === "claude" ? `feito por ${estado.stats.escritor}`
                    : `resumo: ${estado.stats.escritor} · leitura: ${estado.stats.extrator}`}
                  {numeros(estado) && ` · ${numeros(estado)}`}
                </span>
              </div>
            )}
          </div>

          <aside className="flex w-full shrink-0 flex-col gap-3 xl:sticky xl:top-0 xl:w-[300px]">
            <div className={card}>
              <div className="mb-2 flex items-center gap-2">
                <p className={rotulo}>Material · {materiais.length}</p>
                {!!lendo && <span className="animate-pulse text-xs text-sky-300">lendo {lendo}…</span>}
              </div>
              {materiais.map((m) => (
                <div key={m.id} className="border-t border-line py-1.5 text-xs first:border-0 first:pt-0">
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-fg" title={m.nome}>{m.nome}</span>
                    {!!projeto?.materias.length && (
                      <select aria-label={`Matéria de ${m.nome}`} value={m.materia ?? ""} onChange={(e) => materiaDe(m, e.target.value)}
                              title="Geral: o material serve para todas as matérias"
                              className="max-w-[7.5rem] shrink-0 truncate rounded-md border border-line bg-surface px-1 py-0.5 text-[11px] text-muted">
                        <option value="">Geral</option>
                        {projeto.materias.map((x) => <option key={x.id} value={x.id}>{x.nome}</option>)}
                      </select>
                    )}
                    <button title="Tirar do estudo" onClick={() => remover(m)} className="shrink-0 text-faint hover:text-fg">
                      <X className="size-3.5" />
                    </button>
                  </div>
                  <div className="mt-1 flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-faint">
                      {m.paginas ? `${m.paginas} págs · ` : ""}{tamanhoTexto(m.chars)} caracteres{m.ocr ? " · OCR" : ""}{m.figuras ? ` · ${m.figuras} figura${m.figuras === 1 ? "" : "s"}` : ""}
                    </span>
                    <div className="flex shrink-0 rounded-full border border-line p-0.5" role="radiogroup" aria-label="Uso do material">
                      {([["conteudo", "Conteúdo", "Vira base do resumo"], ["prova", "Prova", "Simulado ou prova antiga: mostra o que cai"]] as const).map(([id, nome, dica]) => (
                        <button key={id} role="radio" aria-checked={m.uso === id} title={dica} onClick={() => m.uso !== id && usoDe(m, id)}
                                className={`rounded-[9px] px-2 py-0.5 ${m.uso === id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
                          {nome}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
              <div className={`flex flex-wrap gap-2 text-xs ${materiais.length ? "mt-2 border-t border-line pt-2" : ""}`}>
                <button className={btn} onClick={() => arquivo.current?.click()}><Paperclip className="size-3.5" /> Arquivo</button>
                <button className={btn} onClick={() => setPainel(painel === "colar" ? "" : "colar")}><Clipboard className="size-3.5" /> Colar texto</button>
              </div>
              {!materiais.length && <p className="mt-2 text-xs text-faint">ou arraste arquivos para esta tela</p>}
            </div>

            {perfil && (perfil.banca || !!perfil.topicos?.length) && (
              <div className={`${card} text-xs`}>
                <p className={`${rotulo} mb-1.5`}>O que cai · pelas provas anexadas</p>
                {(perfil.banca || perfil.formato) && (
                  <p className="text-fg">{[perfil.banca, perfil.formato].filter(Boolean).join(" · ")}</p>
                )}
                {perfil.estilo && <p className="mt-1 text-muted">{perfil.estilo}</p>}
                {!!perfil.topicos?.length && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {perfil.topicos.map((t) => <span key={t} className="rounded-md bg-raised px-1.5 py-0.5 text-muted">{t}</span>)}
                  </div>
                )}
              </div>
            )}

            {secoes.length > 1 && (
              <div className={`${card} text-xs`}>
                <p className={`${rotulo} mb-1.5`}>Sumário</p>
                {secoes.map((s, i) => (
                  <button key={i} onClick={() => { if (mapa) { irDepois.current = { i, titulos: "h2" }; verMapa(false); } else irPara(i); }}
                          className="block w-full truncate py-0.5 text-left text-muted hover:text-fg" title={s}>
                    {s}
                  </button>
                ))}
              </div>
            )}

            {!!fontesWeb.length && (
              <div className={`${card} text-xs`}>
                <p className={`${rotulo} mb-1.5`}>Web · {fontesWeb.filter((f) => f.status === "util").length} úteis</p>
                {fontesWeb.map((f) => (
                  <div key={f.id} className="flex items-center gap-2 py-0.5">
                    <span className={`min-w-0 flex-1 truncate ${f.status === "util" ? "text-fg" : "text-faint"}`} title={f.titulo}>{f.titulo}</span>
                    <a href={f.url} target="_blank" rel="noreferrer" className="shrink-0 text-faint hover:text-fg" title="Abrir a página">
                      <ExternalLink className="size-3.5" />
                    </a>
                  </div>
                ))}
              </div>
            )}
          </aside>
        </div>
      </div>

      <div className="shrink-0 px-5 pb-4">
        <div className="mx-auto max-w-3xl">
          {painel === "colar" && (
            <div className={`${card} mb-2 flex flex-col gap-2 text-xs`}>
              <div className="flex items-center">
                <span className="font-medium text-fg">Colar texto como material</span>
                <button onClick={() => setPainel("")} title="Fechar" className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg">
                  <X className="size-3.5" />
                </button>
              </div>
              <textarea rows={6} value={colado} onChange={(e) => setColado(e.target.value)}
                        placeholder="Anotações, um capítulo, questões de uma prova…"
                        className="rounded-lg border border-line bg-raised p-2 text-[13px] text-fg focus:border-focus focus:outline-none" />
              <div><button className={btnPrimary} disabled={!colado.trim()} onClick={() => colar(colado)}>Adicionar</button></div>
            </div>
          )}

          {painel === "prefs" && (
            <div className={`${card} mb-2 flex flex-col gap-3 text-xs`}>
              <div className="flex items-center">
                <span className="font-medium text-fg">Como você quer o resumo</span>
                <button onClick={() => setPainel("")} title="Fechar" className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg">
                  <X className="size-3.5" />
                </button>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <Opcoes titulo="Nível" itens={NIVEIS} ligado={(id) => prefs.nivel === id} onClick={(nivel) => setPrefs((p) => ({ ...p, nivel }))} />
                <Opcoes titulo="Objetivo" itens={OBJETIVOS} ligado={(id) => prefs.objetivo === id} onClick={(objetivo) => setPrefs((p) => ({ ...p, objetivo }))} />
                <Opcoes titulo="Tom" itens={TONS} ligado={(id) => prefs.tom === id} onClick={(tom) => setPrefs((p) => ({ ...p, tom }))} />
                <Opcoes titulo="Tamanho" itens={TAMANHOS} ligado={(id) => prefs.tamanho === id} onClick={(tamanho) => setPrefs((p) => ({ ...p, tamanho }))} />
              </div>
              <Opcoes titulo="Extras" itens={EXTRAS} ligado={(id) => prefs.extras.includes(id)}
                      onClick={(id) => setPrefs((p) => ({ ...p, extras: p.extras.includes(id) ? p.extras.filter((x) => x !== id) : [...p.extras, id] }))} />
              <label className="flex flex-col gap-1.5">
                <span className="text-muted">Algo mais? (opcional)</span>
                <input value={prefs.observacoes} onChange={(e) => setPrefs((p) => ({ ...p, observacoes: e.target.value }))}
                       placeholder="Ex.: minha prova é dia 12, foque em cálculo; explique como para quem trabalha com vendas"
                       className="rounded-lg border border-line bg-raised px-2 py-1.5 text-[13px] text-fg focus:border-focus focus:outline-none" />
              </label>
            </div>
          )}

          {painel === "modelos" && painelModelos}

          <CaixaPrompt>
            <textarea
              rows={2}
              value={tema}
              onChange={(e) => setTema(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  estudar();
                }
              }}
              onPaste={(e) => {
                const files = Array.from(e.clipboardData.files);
                if (files.length) {
                  e.preventDefault();
                  anexar(files);
                  return;
                }
                const texto = e.clipboardData.getData("text");
                if (texto.length > TEXTO_LONGO) {   // capítulo colado no campo do tema: é material, não tema
                  e.preventDefault();
                  colar(texto);
                }
              }}
              placeholder="Qual matéria ou tema você quer estudar?"
              className={campoPrompt}
            />
            <RodapePrompt>
              <button className={redondo} title="Anexar material" onClick={() => arquivo.current?.click()}>
                <Paperclip className="size-4" />
              </button>
              <button className={`${pilula} ${painel === "prefs" ? pilulaLigada : ""}`} title="Nível, objetivo, tom, tamanho e extras"
                      onClick={() => setPainel(painel === "prefs" ? "" : "prefs")}>
                <Sliders className="size-3.5" />
                {resumoPrefs}
              </button>
              <button className={`${pilula} ${web ? pilulaLigada : ""}`} aria-pressed={web}
                      title="Completar o material com pesquisa na web" onClick={() => setWeb((v) => !v)}>
                <Globe className="size-3.5" /> Web
              </button>
              {web && (
                <Menu title="Profundidade da pesquisa" items={PROFUNDIDADES} value={profundidade} onChange={setProfundidade}
                      button={(label) => (<><Search className="size-3.5" />{label}</>)} />
              )}
              <DireitaPrompt>
                {botaoModelos}
                <BotaoEnviar rodando={rodando} onParar={parar} onEnviar={estudar} titulo="Estudar" desabilitado={!tema.trim() || !!lendo} />
              </DireitaPrompt>
            </RodapePrompt>
          </CaixaPrompt>
        </div>
      </div>
    </div>
  );
}

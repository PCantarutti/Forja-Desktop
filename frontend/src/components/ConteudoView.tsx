import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import Confirma from "./Confirma";
import { Check, Edit, Eye, FolderOpen, Gear, Play, Plus, Refresh, Trash } from "./icons";
import { Markdown } from "./MessageView";
import ModelPicker from "./ModelPicker";
import ConteudoRoteiros from "./ConteudoRoteiros";
import ConteudoProducao from "./ConteudoProducao";

// Repetidas por view para a aba viajar inteira num cherry-pick (mesmo motivo do PesquisaView).
const card = "rounded-xl border border-line bg-surface p-3.5";
const btn = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-[9px] border border-line-strong px-3 py-1.5 text-[13px] text-fg hover:border-focus hover:bg-raised disabled:opacity-40";
const btnPrimary = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-[9px] border border-accent bg-accent px-3 py-1.5 text-[13px] font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";
const campo = "w-full rounded-lg border border-line bg-raised px-2.5 py-1.5 text-[13px] text-fg focus:border-focus focus:outline-none";
const rotulo = "text-[12px] font-medium text-muted";

const MOTOR_CLAUDE = "claude-mcp";   // o mesmo literal de estudos.MOTOR_CLAUDE no backend

type Pastas = { pasta_estilos: string; pasta_projeto: string; pasta_saida: string; comandos: string[]; claude_cli?: string; claude_conta?: string };
type Estilo = { nome: string; resumo: string; atualizado: string };
type Modo = "desligada" | "aprovacao" | "automatico";
type Spec = {
  id?: number;
  nome: string;
  tema: string;
  palavras_chave: string[];
  fontes: string[];
  dias: number;
  estilo: string;
  roteiros: number;
  formato: "vertical" | "horizontal";
  motor: { provider: string; model: string };
  observacoes: string;
  automacao: { modo: Modo; hora_roteiros: string; hora_producao: string };
};

const SPEC_VAZIA: Spec = {
  nome: "", tema: "", palavras_chave: [], fontes: [], dias: 3, estilo: "", roteiros: 3, formato: "vertical",
  motor: { provider: "", model: "" }, observacoes: "",
  automacao: { modo: "desligada", hora_roteiros: "19:00", hora_producao: "03:00" },
};

const MODOS: { id: Modo; label: string; hint: string }[] = [
  { id: "desligada", label: "Manual", hint: "Você dispara a pesquisa e a produção quando quiser." },
  { id: "aprovacao", label: "Aprovação", hint: "Gera os roteiros no 1º horário; você aprova um e ele é produzido no 2º." },
  { id: "automatico", label: "Automático", hint: "No 2º horário pesquisa, escolhe o melhor roteiro sozinho e produz o vídeo." },
];


type Trilha = { dia?: string; etapa?: string; aviso?: string; escolhido?: string };
type Agenda = { modo: Modo; r: Trilha; p: Trilha; proximas: { r?: string; p?: string } };

const ETAPAS: Record<string, string> = {
  roteiros: "pesquisando e escrevendo roteiros", fila: "esperando outra produção terminar",
  produzindo: "produzindo o vídeo", feito: "concluído", falhou: "não terminou",
};

function quandoFica(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso), hoje = new Date();
  const amanha = new Date(hoje.getFullYear(), hoje.getMonth(), hoje.getDate() + 1);
  const dia = d.toDateString() === hoje.toDateString() ? "hoje" : d.toDateString() === amanha.toDateString() ? "amanhã"
    : d.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" });
  return `${dia} às ${d.toTimeString().slice(0, 5)}`;
}

function LinhaTrilha(props: { rotulo: string; t: Trilha; proxima?: string }) {
  const { t } = props;
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 text-[12.5px]">
      <span className="w-32 shrink-0 text-muted">{props.rotulo}</span>
      {props.proxima && <span className="text-fg">próxima {quandoFica(props.proxima)}</span>}
      {t.etapa && (
        <span className={t.etapa === "falhou" ? "text-red-300" : t.etapa === "feito" ? "text-emerald-300" : "text-sky-300"}>
          · última ({t.dia}): {ETAPAS[t.etapa] ?? t.etapa}{t.escolhido ? ` — "${t.escolhido}"` : ""}
        </span>
      )}
      {t.aviso && <span className="basis-full pl-34 text-[11.5px] text-amber-300">{t.aviso}</span>}
    </div>
  );
}
const KEY_ABA = "forja.conteudo.aba";

// Painel = a especificação em uso (vídeo + roteiros); Especificação = o formulário dela. Estilos e Ajustes valem
// para todas as especificações, por isso ficam à parte, no canto.
type Vista = "painel" | "spec" | "estilos" | "ajustes";

export default function ConteudoView(props: {
  conv: number | null;
  carimbo?: string;
  provider: string;
  model: string;
  onError: (msg: string) => void;
  onConversationChanged: () => void;
  onAbrir: (id: number) => void;
}) {
  const [vista, setVistaState] = useState<Vista>(() => {
    const salva = localStorage.getItem(KEY_ABA);
    return salva === "spec" || salva === "estilos" || salva === "ajustes" ? salva : "painel";
  });
  const [pastas, setPastas] = useState<Pastas | null>(null);
  const [estilos, setEstilos] = useState<Estilo[]>([]);
  const [cab, setCab] = useState<Spec | null>(null);
  const [agenda, setAgenda] = useState<Agenda | null>(null);
  const [pulso, setPulso] = useState(0);   // ação do cabeçalho: painel recarrega na hora, sem esperar o carimbo
  const [disparando, setDisparando] = useState("");
  const setVista = (a: Vista) => { setVistaState(a); localStorage.setItem(KEY_ABA, a); };

  const carregarEstilos = useCallback(async () => {
    try {
      setEstilos(await api.get<Estilo[]>("/conteudo/estilos"));
    } catch {
      setEstilos([]);   // sem pasta escolhida: a tela pede a pasta
    }
  }, []);

  useEffect(() => {
    api.get<Pastas>("/conteudo/pastas").then(setPastas).catch((e) => props.onError(e.message));
  }, []);
  useEffect(() => {
    if (pastas?.pasta_estilos) carregarEstilos();
  }, [pastas?.pasta_estilos, carregarEstilos]);
  useEffect(() => {
    if (props.conv === null) { setCab(null); setAgenda(null); return; }
    Promise.all([api.get<Spec>(`/conteudo/especificacoes/${props.conv}`), api.get<Agenda>(`/conteudo/especificacoes/${props.conv}/agenda`)])
      .then(([s, a]) => { setCab(s); setAgenda(a); })
      .catch(() => {});
  }, [props.conv, props.carimbo, pulso]);

  async function disparar(qual: "roteiros" | "producao") {
    setDisparando(qual);
    try {
      await api.post(`/conteudo/especificacoes/${props.conv}/${qual}`, {});
      setPulso((n) => n + 1);
      if (vista !== "painel") setVista("painel");
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setDisparando("");
    }
  }

  if (!pastas) return <div className="flex-1" />;
  const semPasta = !pastas.pasta_estilos;
  const global = vista === "estilos" || vista === "ajustes";
  const atual: Vista = semPasta ? "ajustes" : props.conv === null && !global ? "spec" : vista;
  const carimbo = `${props.carimbo ?? ""}-${pulso}`;
  const formato = cab?.formato === "horizontal" ? "Horizontal 16:9" : "Vertical 9:16";
  const modo = MODOS.find((m) => m.id === cab?.automacao.modo)?.label ?? "Manual";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="border-b border-line px-6 pt-5">
        <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[19px] font-semibold tracking-[-0.01em] text-fg">
              {atual === "estilos" ? "Estilos" : atual === "ajustes" ? "Ajustes do Conteúdo"
                : props.conv === null ? "Nova especificação" : cab?.nome ?? "…"}
            </h1>
            <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12.5px] text-muted">
              {atual === "estilos" ? <span>Guias de roteiro, voz e visual — valem para todas as especificações.</span>
                : atual === "ajustes" ? <span>Pastas, Claude Code e o que ele pode rodar sozinho.</span>
                : props.conv === null ? <span>Descreva um tema do canal; o Forja pesquisa e escreve os roteiros dele.</span>
                : cab && (
                  <>
                    <span>{cab.estilo || "sem estilo"}</span><Ponto />
                    <span>{formato}</span><Ponto />
                    <span>automação {modo.toLowerCase()}</span>
                    {agenda?.proximas.r && <><Ponto /><span>roteiros {quandoFica(agenda.proximas.r)}</span></>}
                    {agenda?.proximas.p && <><Ponto /><span>vídeo {quandoFica(agenda.proximas.p)}</span></>}
                  </>
                )}
            </p>
          </div>
          {props.conv !== null && !global && (
            <div className="flex shrink-0 items-center gap-2">
              <button className={btn} disabled={!!disparando} onClick={() => disparar("roteiros")}>
                <Refresh className="size-4" /> Pesquisar roteiros
              </button>
              <button className={btnPrimary} disabled={!!disparando} onClick={() => disparar("producao")}
                      title="O Claude Code monta e renderiza o roteiro escolhido">
                <Play className="size-4" /> Produzir o escolhido
              </button>
            </div>
          )}
        </div>
        <nav className="mt-4 flex items-end gap-1 text-[13px]">
          {props.conv !== null && ([["painel", "Painel"], ["spec", "Especificação"]] as const).map(([id, label]) => (
            <Aba key={id} ativa={atual === id} desabilitada={semPasta} onClick={() => setVista(id)}>{label}</Aba>
          ))}
          {props.conv === null && <Aba ativa={atual === "spec"} desabilitada={semPasta} onClick={() => setVista("spec")}>Especificação</Aba>}
          <div className="ml-auto flex items-end gap-1">
            <Aba ativa={atual === "estilos"} desabilitada={semPasta} onClick={() => setVista("estilos")}>
              <Edit className="size-3.5" /> Estilos
            </Aba>
            <Aba ativa={atual === "ajustes"} onClick={() => setVista("ajustes")}>
              <Gear className="size-3.5" /> Ajustes
            </Aba>
          </div>
        </nav>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {atual === "ajustes" ? (
          <div className="px-6 py-5">
            <PastasPainel pastas={pastas} primeira={semPasta} onError={props.onError}
                          onSalvo={(p) => { setPastas(p); if (semPasta) setVista("estilos"); }} />
          </div>
        ) : atual === "estilos" ? (
          <div className="px-6 py-5">
            <EstilosPainel estilos={estilos} recarregar={carregarEstilos} provider={props.provider} model={props.model}
                           onError={props.onError} />
          </div>
        ) : atual === "painel" && props.conv !== null ? (
          <div className="mx-auto grid max-w-[1400px] items-start gap-x-8 gap-y-6 px-6 py-5 lg:grid-cols-[minmax(280px,340px)_1fr]">
            <ConteudoProducao conv={props.conv} carimbo={carimbo} onError={props.onError} />
            <ConteudoRoteiros conv={props.conv} carimbo={carimbo} onError={props.onError} onProduzindo={() => setPulso((n) => n + 1)} />
          </div>
        ) : (
          <div className="px-6 py-5">
            <SpecPainel {...props} estilos={estilos} irParaEstilos={() => setVista("estilos")} />
          </div>
        )}
      </div>
    </div>
  );
}

const Ponto = () => <span className="size-[3px] rounded-full bg-faint" aria-hidden />;

function Aba(props: { ativa: boolean; desabilitada?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button disabled={props.desabilitada} onClick={props.onClick} aria-current={props.ativa ? "page" : undefined}
            className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 pb-2.5 pt-1 transition-colors disabled:opacity-40 ${
              props.ativa ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"}`}>
      {props.children}
    </button>
  );
}

/* ------------------------------------------------------------------ pastas */

function CampoPasta(props: { rotulo: string; ajuda: string; valor: string; onChange: (v: string) => void }) {
  async function escolher() {
    const p = await window.forja?.pickFolder(props.valor || undefined);
    if (p) props.onChange(p);
  }
  return (
    <label className="flex flex-col gap-1">
      <span className={rotulo}>{props.rotulo}</span>
      <div className="flex gap-2">
        <input className={campo} value={props.valor} onChange={(e) => props.onChange(e.target.value)} placeholder="C:\..." />
        {window.forja && <button className={btn} onClick={escolher}><FolderOpen className="size-4" /> Escolher</button>}
      </div>
      <span className="text-[11.5px] text-faint">{props.ajuda}</span>
    </label>
  );
}

function PastasPainel(props: { pastas: Pastas; primeira: boolean; onError: (m: string) => void; onSalvo: (p: Pastas) => void }) {
  const [p, setP] = useState(props.pastas);
  const [comandos, setComandos] = useState(props.pastas.comandos.join("\n"));
  const [salvando, setSalvando] = useState(false);

  async function salvar() {
    setSalvando(true);
    try {
      props.onSalvo(await api.put<Pastas>("/conteudo/pastas", {
        ...p, comandos: comandos.split("\n").map((c) => c.trim()).filter(Boolean),
      }));
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      {props.primeira && (
        <div className={`${card} text-[13px] text-muted`}>
          Para começar, escolha a pasta onde ficam os <b className="text-fg">estilos</b> dos vídeos (arquivos .md).
          O Forja lê e grava direto nela, e o Claude que monta o vídeo lê os mesmos arquivos.
        </div>
      )}
      <div className={`${card} flex flex-col gap-4`}>
        <CampoPasta rotulo="Pasta de estilos" valor={p.pasta_estilos} onChange={(v) => setP({ ...p, pasta_estilos: v })}
                    ajuda="Um .md por estilo. Se não tiver _modelo.md, o Forja cria um com o padrão." />
        <CampoPasta rotulo="Projeto de vídeo (Remotion)" valor={p.pasta_projeto} onChange={(v) => setP({ ...p, pasta_projeto: v })}
                    ajuda="Onde o Claude monta e renderiza o vídeo. Usado a partir da etapa de produção." />
        <CampoPasta rotulo="Entrega dos vídeos prontos" valor={p.pasta_saida} onChange={(v) => setP({ ...p, pasta_saida: v })}
                    ajuda="Para onde o .mp4 final é copiado. Padrão: Área de Trabalho." />
        <ClaudeCli valor={p.claude_cli ?? ""} onChange={(v) => setP({ ...p, claude_cli: v })} />
        <CampoPasta rotulo="Pasta da conta do Claude (opcional)" valor={p.claude_conta ?? ""}
                    onChange={(v) => setP({ ...p, claude_conta: v })}
                    ajuda="Para produzir com outra conta do Claude Code (CLAUDE_CONFIG_DIR). Vazio = a conta do terminal. Depois de salvar, faça o login nela uma vez (veja abaixo) e use Testar Claude." />
        {p.claude_conta && (
          <div className="rounded-lg border border-line bg-raised/40 p-2.5 text-[12px] text-muted">
            Login nesta conta, uma vez, num PowerShell:
            <pre className="mt-1 select-all whitespace-pre-wrap font-mono text-[11.5px] text-fg">{`$env:CLAUDE_CONFIG_DIR = "${p.claude_conta}"; claude auth login`}</pre>
          </div>
        )}
        <label className="flex flex-col gap-1">
          <span className={rotulo}>Comandos que o Claude pode rodar sozinho</span>
          <textarea className={`${campo} h-28 font-mono text-[12px]`} value={comandos} onChange={(e) => setComandos(e.target.value)} />
          <span className="text-[11.5px] text-faint">Um por linha; * vale qualquer coisa. Fora desta lista o comando é negado na hora.</span>
        </label>
        <div><button className={btnPrimary} disabled={salvando || !p.pasta_estilos} onClick={salvar}><Check className="size-4" /> Salvar</button></div>
      </div>
    </div>
  );
}

function ClaudeCli(props: { valor: string; onChange: (v: string) => void }) {
  const [achado, setAchado] = useState<string | null>(null);
  const [teste, setTeste] = useState<{ ok: boolean; mensagem: string } | "testando" | null>(null);
  useEffect(() => {
    api.get<{ caminho: string }>("/conteudo/claude").then((r) => setAchado(r.caminho)).catch(() => setAchado(""));
  }, []);
  return (
    <label className="flex flex-col gap-1">
      <span className={rotulo}>Claude Code (quem produz o vídeo)</span>
      <div className="flex gap-2">
        <input className={campo} value={props.valor} onChange={(e) => props.onChange(e.target.value)}
               placeholder={achado ? `Automático: ${achado}` : "Caminho do claude.exe"} />
        <button className={btn} disabled={teste === "testando"} title="Salve antes se mudou o caminho"
                onClick={async () => {
                  setTeste("testando");
                  setTeste(await api.post<{ ok: boolean; mensagem: string }>("/conteudo/claude/testar", {})
                    .catch((e) => ({ ok: false, mensagem: e.message })));
                }}>
          {teste === "testando" ? "Testando…" : "Testar Claude"}
        </button>
      </div>
      {teste && teste !== "testando" && (
        <span className={`text-[12px] ${teste.ok ? "text-emerald-300" : "text-red-300"}`}>{teste.mensagem}</span>
      )}
      <span className={`text-[11.5px] ${achado === "" && !props.valor ? "text-amber-300" : "text-faint"}`}>
        {achado === null ? "Procurando…" : achado
          ? "Encontrado no PC. Deixe vazio para usar este; ele precisa estar logado (rode claude uma vez no terminal)."
          : "Não encontrado. Instale com npm i -g @anthropic-ai/claude-code ou informe o caminho do claude.exe."}
      </span>
    </label>
  );
}

/* ------------------------------------------------------------------ especificação */

function SpecPainel(props: {
  conv: number | null;
  carimbo?: string;
  provider: string;
  model: string;
  estilos: Estilo[];
  onError: (msg: string) => void;
  onConversationChanged: () => void;
  onAbrir: (id: number) => void;
  irParaEstilos: () => void;
}) {
  const [spec, setSpec] = useState<Spec>(SPEC_VAZIA);
  const [sujo, setSujo] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [salvoEm, setSalvoEm] = useState("");

  const aberta = useRef<number | null | undefined>(undefined);   // conv que está no formulário
  const [agenda, setAgenda] = useState<Agenda | null>(null);
  useEffect(() => {
    if (props.conv === null) { setAgenda(null); return; }
    api.get<Agenda>(`/conteudo/especificacoes/${props.conv}/agenda`).then(setAgenda).catch(() => setAgenda(null));
  }, [props.conv, props.carimbo, salvoEm]);

  useEffect(() => {
    const trocou = aberta.current !== props.conv;
    if (!trocou && sujo) return;   // só o carimbo mudou, com edição na tela: não joga fora o que foi digitado
    aberta.current = props.conv;
    setSalvoEm("");
    setSujo(false);
    if (props.conv === null) {
      setSpec({ ...SPEC_VAZIA, motor: { provider: props.provider, model: props.model } });
      return;
    }
    api.get<Spec>(`/conteudo/especificacoes/${props.conv}`).then(setSpec).catch((e) => props.onError(e.message));
  }, [props.conv, props.carimbo]);

  const muda = (patch: Partial<Spec>) => { setSpec((s) => ({ ...s, ...patch })); setSujo(true); };
  const claude = spec.motor.provider === MOTOR_CLAUDE;

  async function salvar() {
    setSalvando(true);
    try {
      if (props.conv === null) {
        const r = await api.post<Spec>("/conteudo/especificacoes", spec);
        props.onConversationChanged();
        props.onAbrir(r.id!);
      } else {
        setSpec(await api.put<Spec>(`/conteudo/especificacoes/${props.conv}`, spec));
        props.onConversationChanged();
      }
      setSujo(false);
      setSalvoEm(new Date().toLocaleTimeString().slice(0, 5));
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <div className={`${card} flex flex-col gap-3.5`}>
        <label className="flex flex-col gap-1">
          <span className={rotulo}>Nome</span>
          <input className={campo} value={spec.nome} placeholder="Notícias de IA" onChange={(e) => muda({ nome: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1">
          <span className={rotulo}>Tema</span>
          <textarea className={`${campo} h-20`} value={spec.tema}
                    placeholder="Lançamentos, riscos e polêmicas de inteligência artificial que afetam o público geral"
                    onChange={(e) => muda({ tema: e.target.value })} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className={rotulo}>Palavras-chave</span>
            <input className={campo} value={spec.palavras_chave.join(", ")} placeholder="OpenAI, Meta, Anthropic"
                   onChange={(e) => muda({ palavras_chave: e.target.value.split(",").map((x) => x.trimStart()) })} />
          </label>
          <label className="flex flex-col gap-1">
            <span className={rotulo}>Fontes preferidas</span>
            <input className={campo} value={spec.fontes.join(", ")} placeholder="techcrunch.com, theverge.com"
                   onChange={(e) => muda({ fontes: e.target.value.split(",").map((x) => x.trimStart()) })} />
          </label>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <label className="flex flex-col gap-1">
            <span className={rotulo}>Estilo</span>
            <select className={campo} value={spec.estilo} onChange={(e) => muda({ estilo: e.target.value })}>
              <option value="">Escolha…</option>
              {props.estilos.map((e) => <option key={e.nome} value={e.nome}>{e.nome}</option>)}
            </select>
            <button className="self-start text-[11.5px] text-accent hover:underline" onClick={props.irParaEstilos}>
              Ver / criar estilos
            </button>
          </label>
          <label className="flex flex-col gap-1">
            <span className={rotulo}>Novidades dos últimos</span>
            <div className="flex items-center gap-2">
              <input type="number" min={1} max={30} className={`${campo} w-20`} value={spec.dias}
                     onChange={(e) => muda({ dias: Number(e.target.value) })} />
              <span className="text-[12px] text-muted">dias</span>
            </div>
          </label>
          <label className="flex flex-col gap-1">
            <span className={rotulo}>Roteiros por pesquisa</span>
            <input type="number" min={1} max={10} className={`${campo} w-20`} value={spec.roteiros}
                   onChange={(e) => muda({ roteiros: Number(e.target.value) })} />
          </label>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className={rotulo}>Formato do vídeo</span>
          <div className="flex flex-wrap gap-2">
            {([["vertical", "Vertical 9:16", "Shorts, Reels, TikTok · 1080×1920"],
               ["horizontal", "Horizontal 16:9", "YouTube, vídeo longo · 1920×1080"]] as const).map(([id, label, hint]) => (
              <button key={id}
                      className={`flex flex-col items-start rounded-lg border px-3 py-1.5 text-left ${spec.formato === id ? "border-accent bg-accent/15" : "border-line hover:border-line-strong"}`}
                      onClick={() => muda({ formato: id })}>
                <span className="text-[13px] text-fg">{label}</span>
                <span className="text-[11px] text-muted">{hint}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className={rotulo}>Quem pesquisa e escreve os roteiros</span>
          <div className="flex flex-wrap items-center gap-3 text-[13px]">
            <label className="flex items-center gap-1.5">
              <input type="radio" checked={!claude}
                     onChange={() => muda({ motor: { provider: props.provider, model: props.model } })} />
              Modelo do Forja
            </label>
            {!claude && (
              <div className="[&>div]:ml-0">
                <ModelPicker provider={spec.motor.provider} model={spec.motor.model} loadLocal={false} autoFallback={false}
                             onChange={(provider, model) => muda({ motor: { provider, model } })} />
              </div>
            )}
            <label className="flex items-center gap-1.5">
              <input type="radio" checked={claude}
                     onChange={() => muda({ motor: { provider: MOTOR_CLAUDE, model: "Claude (MCP)" } })} />
              Claude (MCP)
            </label>
          </div>
        </div>
        <label className="flex flex-col gap-1">
          <span className={rotulo}>Observações para o roteirista</span>
          <textarea className={`${campo} h-16`} value={spec.observacoes}
                    placeholder="Evitar boatos sem fonte; preferir notícias com impacto no Brasil…"
                    onChange={(e) => muda({ observacoes: e.target.value })} />
        </label>
      </div>

      <div className={`${card} flex flex-col gap-3`}>
        <div className="text-[13px] font-semibold text-fg">Automação</div>
        <div className="flex flex-wrap gap-2">
          {MODOS.map((m) => (
            <button key={m.id} title={m.hint}
                    className={`rounded-lg border px-3 py-1.5 text-[13px] ${spec.automacao.modo === m.id ? "border-accent bg-accent/15 text-fg" : "border-line text-muted hover:text-fg"}`}
                    onClick={() => muda({ automacao: { ...spec.automacao, modo: m.id } })}>
              {m.label}
            </button>
          ))}
        </div>
        <span className="text-[12px] text-muted">{MODOS.find((m) => m.id === spec.automacao.modo)?.hint}</span>
        {spec.automacao.modo !== "desligada" && (
          <div className="flex flex-wrap gap-4 text-[13px]">
            {spec.automacao.modo === "aprovacao" && (
              <label className="flex items-center gap-2">
                <span className="whitespace-nowrap text-muted">Gerar roteiros às</span>
                <input type="time" className={`${campo} w-28`} value={spec.automacao.hora_roteiros}
                       onChange={(e) => muda({ automacao: { ...spec.automacao, hora_roteiros: e.target.value } })} />
              </label>
            )}
            <label className="flex items-center gap-2">
              <span className="whitespace-nowrap text-muted">Produzir o vídeo às</span>
              <input type="time" className={`${campo} w-28`} value={spec.automacao.hora_producao}
                     onChange={(e) => muda({ automacao: { ...spec.automacao, hora_producao: e.target.value } })} />
            </label>
          </div>
        )}
        {agenda && agenda.modo !== "desligada" && !sujo && (
          <div className="flex flex-col gap-1 rounded-lg border border-line bg-raised/40 p-2.5">
            {agenda.modo === "aprovacao" && <LinhaTrilha rotulo="Roteiros" t={agenda.r} proxima={agenda.proximas.r} />}
            <LinhaTrilha rotulo={agenda.modo === "automatico" ? "Roteiro + vídeo" : "Vídeo do aprovado"} t={agenda.p}
                         proxima={agenda.proximas.p} />
          </div>
        )}
        {spec.automacao.modo !== "desligada" && (
          <span className="text-[11.5px] text-faint">
            Precisa do Forja aberto (pode ficar na bandeja). Com automação ligada o PC não entra em suspensão — a tela
            pode apagar. Se o Forja abrir mais de 3 h depois do horário, aquele dia é pulado.
          </span>
        )}
      </div>

      <div className="flex items-center gap-3">
        <button className={btnPrimary} disabled={salvando || !spec.nome.trim() || !spec.tema.trim()} onClick={salvar}>
          <Check className="size-4" /> {props.conv === null ? "Criar especificação" : "Salvar"}
        </button>
        {salvoEm && !sujo && <span className="text-[12px] text-muted">Salvo às {salvoEm}</span>}
        {sujo && <span className="text-[12px] text-amber-300">Alterações não salvas</span>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ estilos */

type Novo = { nome: string; para_que: string; tom: string; duracao: string; descricao: string; ia: { provider: string; model: string } };

function EstilosPainel(props: {
  estilos: Estilo[];
  recarregar: () => Promise<void>;
  provider: string;
  model: string;
  onError: (msg: string) => void;
}) {
  const [aberto, setAberto] = useState<string | null>(null);   // nome do estilo; "" = criando um novo
  const [texto, setTexto] = useState("");
  const [original, setOriginal] = useState("");
  const [ver, setVer] = useState(false);
  const [novo, setNovo] = useState<Novo | null>(null);
  const [ocupado, setOcupado] = useState("");

  async function abrir(nome: string) {
    try {
      const r = await api.get<{ texto: string }>(`/conteudo/estilos/${nome}`);
      setAberto(nome);
      setNovo(null);
      setTexto(r.texto);
      setOriginal(r.texto);
      setVer(true);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  function comecarNovo() {
    setAberto("");
    setTexto("");
    setOriginal("");
    setVer(false);
    setNovo({ nome: "", para_que: "", tom: "", duracao: "", descricao: "", ia: { provider: props.provider, model: props.model } });
  }

  async function doModelo() {
    if (!novo) return;
    const r = await api.get<{ texto: string }>("/conteudo/estilos-modelo");
    setTexto(r.texto.replace("<nome-do-estilo>", novo.nome || "<nome-do-estilo>"));
  }

  async function comIa() {
    if (!novo) return;
    setOcupado("gerando");
    try {
      const r = await api.post<{ texto: string }>("/conteudo/estilos-gerar", {
        descricao: `Nome do estilo: ${novo.nome}\nPara quê: ${novo.para_que}\nTom: ${novo.tom}\nDuração: ${novo.duracao}\n\n${novo.descricao}`,
        provider: novo.ia.provider, model: novo.ia.model,
      });
      setTexto(r.texto);
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setOcupado("");
    }
  }

  async function salvar() {
    setOcupado("salvando");
    try {
      if (novo) {
        const r = await api.put<{ nome: string; texto: string }>(`/conteudo/estilos/${novo.nome.trim().toLowerCase()}`, {
          texto, novo: true, indice: { para_que: novo.para_que, tom: novo.tom, duracao: novo.duracao },
        });
        await props.recarregar();
        setNovo(null);
        setAberto(r.nome);
        setOriginal(r.texto);
        setVer(true);
      } else if (aberto) {
        const r = await api.put<{ texto: string }>(`/conteudo/estilos/${aberto}`, { texto });
        setOriginal(r.texto);
        await props.recarregar();
      }
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setOcupado("");
    }
  }

  async function apagar() {
    if (!aberto) return;
    try {
      await api.del(`/conteudo/estilos/${aberto}`);
      setAberto(null);
      await props.recarregar();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl gap-4">
      <div className="flex w-64 shrink-0 flex-col gap-2">
        <button className={btn} onClick={comecarNovo}><Plus className="size-4" /> Novo estilo</button>
        {props.estilos.map((e) => (
          <button key={e.nome} onClick={() => abrir(e.nome)}
                  className={`rounded-xl border p-2.5 text-left ${aberto === e.nome ? "border-accent bg-raised" : "border-line bg-surface hover:border-line-strong"}`}>
            <div className="font-mono text-[12.5px] text-fg">{e.nome}</div>
            <div className="mt-0.5 line-clamp-2 text-[11.5px] text-muted">{e.resumo}</div>
          </button>
        ))}
        {props.estilos.length === 0 && <div className="text-[12px] text-faint">Nenhum estilo na pasta ainda.</div>}
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-3">
        {aberto === null ? (
          <div className={`${card} text-[13px] text-muted`}>
            Escolha um estilo à esquerda ou crie um novo. Todo estilo segue o mesmo modelo (Roteiro, Voz e legenda,
            Visual, Áudio, Engajamento), para o Claude saber onde procurar cada coisa.
          </div>
        ) : (
          <>
            {novo && (
              <div className={`${card} flex flex-col gap-3`}>
                <div className="text-[13px] font-semibold text-fg">Novo estilo</div>
                <div className="grid grid-cols-2 gap-3">
                  <label className="flex flex-col gap-1">
                    <span className={rotulo}>Nome (minúsculas e hífen)</span>
                    <input className={`${campo} font-mono`} value={novo.nome} placeholder="humor-games"
                           onChange={(e) => setNovo({ ...novo, nome: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className={rotulo}>Para quê</span>
                    <input className={campo} value={novo.para_que} placeholder="Notícias de games com humor"
                           onChange={(e) => setNovo({ ...novo, para_que: e.target.value })} />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className={rotulo}>Tom</span>
                    <input className={campo} value={novo.tom} placeholder="Leve, zoeira, rápido"
                           onChange={(e) => setNovo({ ...novo, tom: e.target.value })} />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className={rotulo}>Duração</span>
                    <input className={campo} value={novo.duracao} placeholder="30–45s"
                           onChange={(e) => setNovo({ ...novo, duracao: e.target.value })} />
                  </label>
                </div>
                <label className="flex flex-col gap-1">
                  <span className={rotulo}>Descreva o estilo (para gerar com IA)</span>
                  <textarea className={`${campo} h-20`} value={novo.descricao}
                            placeholder="Cores neon, memes de jogos, narrador animado, efeitos sonoros de videogame…"
                            onChange={(e) => setNovo({ ...novo, descricao: e.target.value })} />
                </label>
                <div className="flex flex-wrap items-center gap-2">
                  <button className={btn} disabled={!novo.nome} onClick={doModelo}><Edit className="size-4" /> Começar do modelo</button>
                  <div className="[&>div]:ml-0">
                    <ModelPicker provider={novo.ia.provider} model={novo.ia.model} loadLocal={false} autoFallback={false}
                                 onChange={(provider, model) => setNovo({ ...novo, ia: { provider, model } })} />
                  </div>
                  <button className={btn} disabled={!novo.nome || !novo.descricao.trim() || !!ocupado} onClick={comIa}>
                    {ocupado === "gerando" ? "Gerando…" : "Gerar com IA"}
                  </button>
                </div>
              </div>
            )}
            <div className="flex items-center gap-2">
              <span className="font-mono text-[13px] text-fg">{novo ? (novo.nome || "novo-estilo") : aberto}.md</span>
              <button className={`${btn} ml-auto`} onClick={() => setVer(!ver)} disabled={!texto}>
                {ver ? <><Edit className="size-4" /> Editar</> : <><Eye className="size-4" /> Ver</>}
              </button>
              {!novo && (
                <Confirma className={btn} rotulo={<><Trash className="size-4" /> Apagar</>}
                          pergunta={`Apagar ${aberto}.md da pasta?`} onSim={apagar} />
              )}
              <button className={btnPrimary} onClick={salvar}
                      disabled={!!ocupado || !texto.trim() || (novo ? !novo.nome : texto === original)}>
                <Check className="size-4" /> {novo ? "Criar estilo" : "Salvar"}
              </button>
            </div>
            {ver ? (
              <div className={`${card} prose-sm`}><Markdown text={texto} /></div>
            ) : (
              <textarea className={`${campo} min-h-[480px] flex-1 font-mono text-[12.5px] leading-relaxed`} value={texto}
                        placeholder={novo ? "Comece do modelo ou gere com IA, depois ajuste aqui." : ""}
                        onChange={(e) => setTexto(e.target.value)} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

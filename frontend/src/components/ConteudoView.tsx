import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import Confirma from "./Confirma";
import { Check, Edit, Eye, Film, FolderOpen, PanelRight, Play, Plus, Refresh, Trash, X } from "./icons";
import { Markdown } from "./MessageView";
import ModelPicker from "./ModelPicker";
import ConteudoRoteiros from "./ConteudoRoteiros";
import ConteudoProducao from "./ConteudoProducao";

// Mesma linguagem das abas Imagem e Vídeo: faixa no topo, o trabalho no meio e o painel da especificação à
// direita (o "Parâmetros" do Conteúdo). Classes e peças repetidas aqui de propósito, para a aba viajar inteira
// num cherry-pick (mesmo motivo do PesquisaView).
const btn = "inline-flex h-[30px] items-center gap-1.5 whitespace-nowrap rounded-[7px] border border-line-strong px-2.5 text-xs text-fg hover:border-focus hover:bg-raised disabled:pointer-events-none disabled:opacity-40";
const btnPrimary = "inline-flex h-[30px] items-center gap-1.5 whitespace-nowrap rounded-[7px] bg-accent px-3 text-xs font-medium text-accent-fg hover:brightness-110 disabled:pointer-events-none disabled:opacity-40";
const campoCaixa = "w-full min-w-0 bg-transparent text-[13px] text-fg outline-none placeholder:text-faint";
const caixaMono = `${campoCaixa} font-mono text-[12.5px]`;
const ajuda = "text-[11px] leading-relaxed text-faint";

const MOTOR_CLAUDE = "claude-mcp";   // o mesmo literal de estudos.MOTOR_CLAUDE no backend

type Pastas = { pasta_estilos: string; pasta_projeto: string; pasta_saida: string; comandos: string[]; claude_cli?: string; claude_conta?: string; claude_modelo?: string; claude_esforco?: string };
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
  automacao: { modo: Modo; dias: number[]; horarios: string[]; hora_roteiros: string; hora_producao?: string; ativado_em?: string };
};

const SPEC_VAZIA: Spec = {
  nome: "", tema: "", palavras_chave: [], fontes: [], dias: 3, estilo: "", roteiros: 3, formato: "vertical",
  motor: { provider: "", model: "" }, observacoes: "",
  automacao: { modo: "desligada", dias: [0, 1, 2, 3, 4, 5, 6], horarios: ["07:00"], hora_roteiros: "19:00" },
};

const MODOS: { id: Modo; label: string; hint: string }[] = [
  { id: "desligada", label: "Manual", hint: "Você dispara a pesquisa e a produção quando quiser." },
  { id: "aprovacao", label: "Aprovação", hint: "Gera os roteiros no 1º horário; você escolhe um e ele é produzido no 2º." },
  { id: "automatico", label: "Automático", hint: "Em cada horário pesquisa, escolhe o melhor roteiro e o Claude entrega o vídeo. Sem nenhuma pergunta." },
];

type Trilha = { dia?: string; etapa?: string; aviso?: string; escolhido?: string };
type Perdido = { trilha: "r" | "p"; slot: string; desde: string };
type Agenda = { modo: Modo; r: Trilha; p: Trilha; proximas: { r?: string; p?: string }; perdido?: Perdido | null };
type AgendaItem = Agenda & { id: number; nome: string; estilo: string; automacao: Spec["automacao"]; motor: Spec["motor"] };

const DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];
const DIAS_CURTOS = ["S", "T", "Q", "Q", "S", "S", "D"];
const resumoDias = (d: number[]) => d.length === 7 ? "todo dia" : d.join() === "0,1,2,3,4" ? "seg a sex"
  : d.join() === "5,6" ? "fim de semana" : d.map((i) => DIAS[i]).join(", ");

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

/* ------------------------------------------------------------------ peças do design (as do ImagensView) */

/** Rótulo mono em caixa-alta e o conteúdo embaixo, como as seções do painel Parâmetros. */
function Secao(props: { titulo: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint">{props.titulo}</span>
        {props.extra && <span className="ml-auto">{props.extra}</span>}
      </div>
      {props.children}
    </div>
  );
}

/** Campo em caixa: rótulo pequeno em cima, valor embaixo. */
function Caixa(props: { rotulo: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`flex min-w-0 flex-col gap-0.5 rounded-[8px] border border-line bg-surface px-2.5 py-1.5 focus-within:border-focus ${props.className ?? ""}`}>
      <span className="text-[10.5px] text-faint">{props.rotulo}</span>
      {props.children}
    </label>
  );
}

/** Escolha entre poucas opções (o Todas · Mantidas · Sem decisão da galeria). */
function Segmentado<T extends string>(props: { valor: T; opcoes: [T, React.ReactNode][]; onValor: (v: T) => void; rotulo: string; cheio?: boolean; desabilitado?: boolean }) {
  return (
    <div role="radiogroup" aria-label={props.rotulo}
         className={`gap-0.5 rounded-[8px] border border-line bg-surface p-0.5 text-xs ${props.cheio ? "grid auto-cols-fr grid-flow-col" : "flex"}`}>
      {props.opcoes.map(([id, rot]) => (
        <button key={id} role="radio" aria-checked={props.valor === id} disabled={props.desabilitado} onClick={() => props.onValor(id)}
                className={`inline-flex items-center justify-center gap-1.5 rounded-[6px] px-2.5 py-[3px] disabled:opacity-40 ${
                  props.valor === id ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>
          {rot}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ a tela */

const KEY_ABA = "forja.conteudo.aba";
const KEY_PAINEL = "forja.conteudo.painel";

// Painel = a especificação em uso (vídeo + roteiros). Estilos e Ajustes valem para todas as especificações.
// A especificação em si mora no painel da direita, como os Parâmetros da Imagem e do Vídeo.
type Vista = "painel" | "agenda" | "estilos" | "ajustes";

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
    return salva === "agenda" || salva === "estilos" || salva === "ajustes" ? salva : "painel";
  });
  const [painel, setPainelState] = useState(() => localStorage.getItem(KEY_PAINEL) !== "0");
  const [pastas, setPastas] = useState<Pastas | null>(null);
  const [estilos, setEstilos] = useState<Estilo[]>([]);
  const [agenda, setAgenda] = useState<Agenda | null>(null);
  const [pulso, setPulso] = useState(0);   // ação da faixa ou spec salva: o painel recarrega na hora, sem esperar o carimbo
  const [disparando, setDisparando] = useState("");
  const setVista = (a: Vista) => { setVistaState(a); localStorage.setItem(KEY_ABA, a); };
  const setPainel = (v: boolean) => { setPainelState(v); localStorage.setItem(KEY_PAINEL, v ? "1" : "0"); };

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
    if (props.conv === null) { setAgenda(null); return; }
    api.get<Agenda>(`/conteudo/especificacoes/${props.conv}/agenda`).then(setAgenda).catch(() => setAgenda(null));
  }, [props.conv, props.carimbo, pulso]);

  async function disparar(qual: "roteiros" | "producao") {
    setDisparando(qual);
    try {
      await api.post(`/conteudo/especificacoes/${props.conv}/${qual}`, {});
      setPulso((n) => n + 1);
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setDisparando("");
    }
  }

  if (!pastas) return <div className="flex-1" />;
  const semPasta = !pastas.pasta_estilos;
  const atual: Vista = semPasta ? "ajustes" : vista;
  const nova = props.conv === null;
  const carimbo = `${props.carimbo ?? ""}-${pulso}`;
  // Spec nova: o painel é o próprio formulário de criar, então não fecha.
  const comPainel = atual === "painel" && (painel || nova);
  const proxima = agenda && agenda.modo !== "desligada" ? agenda.proximas.p : undefined;

  return (
    <div className="flex min-h-0 flex-1">
      <div className="@container/main flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center gap-2.5 border-b border-line px-4 py-2">
          <Segmentado rotulo="Seção do Conteúdo" valor={atual} onValor={setVista} desabilitado={semPasta}
                      opcoes={[["painel", nova ? "Nova" : "Painel"], ["agenda", "Agenda"], ["estilos", "Estilos"], ["ajustes", "Ajustes"]]} />
          <div className="flex min-w-0 flex-1 justify-center overflow-hidden">
            <UsoClaude carimbo={props.carimbo} />
          </div>
          {atual === "painel" && !nova && (
            <>
              {proxima && (
                <span className="hidden truncate text-[11.5px] text-faint @6xl/main:inline" title="Próximo vídeo agendado">
                  próximo vídeo {quandoFica(proxima)}
                </span>
              )}
              <button className={btn} disabled={!!disparando} onClick={() => disparar("roteiros")}
                      title="Pesquisa as novidades do tema e escreve roteiros novos">
                <Refresh className="size-3.5" /> <span className="hidden @5xl/main:inline">Pesquisar roteiros</span>
              </button>
              <button className={btnPrimary} disabled={!!disparando} onClick={() => disparar("producao")}
                      title="O Claude Code monta e renderiza o roteiro escolhido">
                <Play className="size-3.5" /> Produzir<span className="hidden @5xl/main:inline">o escolhido</span>
              </button>
            </>
          )}
          {atual === "painel" && !nova && (
            <button onClick={() => setPainel(!painel)} aria-pressed={painel}
                    title={painel ? "Esconder a especificação" : "Mostrar a especificação"}
                    className={`grid size-[30px] place-items-center rounded-[7px] ${painel ? "bg-raised text-accent-text" : "text-muted hover:bg-raised hover:text-fg"}`}>
              <PanelRight />
            </button>
          )}
        </div>

        <div className="@container min-h-0 flex-1 overflow-y-auto">
          {atual === "ajustes" ? (
            <PastasPainel pastas={pastas} primeira={semPasta} onError={props.onError}
                          onSalvo={(p) => { setPastas(p); if (semPasta) setVista("estilos"); }} />
          ) : atual === "agenda" ? (
            <AgendaPainel carimbo={carimbo} onError={props.onError} onAbrir={(id) => { props.onAbrir(id); setVista("painel"); }} />
          ) : atual === "estilos" ? (
            <EstilosPainel estilos={estilos} pasta={pastas.pasta_estilos} recarregar={carregarEstilos}
                           provider={props.provider} model={props.model} onError={props.onError} />
          ) : nova ? (
            <Boasvindas />
          ) : (
            <div className="mx-auto grid max-w-[1280px] items-start gap-x-8 gap-y-6 px-5 py-4 @3xl:grid-cols-[minmax(260px,320px)_1fr]">
              {agenda?.perdido && (
                <div className="@3xl:col-span-2">
                  <AvisoPerdido conv={props.conv!} perdido={agenda.perdido} onError={props.onError} onFeito={() => setPulso((n) => n + 1)} />
                </div>
              )}
              <ConteudoProducao conv={props.conv!} carimbo={carimbo} onError={props.onError} />
              <ConteudoRoteiros conv={props.conv!} carimbo={carimbo} onError={props.onError} onProduzindo={() => setPulso((n) => n + 1)} />
            </div>
          )}
        </div>
      </div>

      {comPainel && (
        <SpecPainel {...props} estilos={estilos} agenda={agenda} irParaEstilos={() => setVista("estilos")}
                    onFechar={nova ? undefined : () => setPainel(false)} onSalvo={() => setPulso((n) => n + 1)} />
      )}
    </div>
  );
}

/** O meio da tela enquanto a especificação ainda não existe (o formulário está no painel da direita). */
function Boasvindas() {
  const passos: [string, string][] = [
    ["Tema", "Diga do que é o canal, as palavras-chave e as fontes que valem."],
    ["Roteiros", "O Forja pesquisa as novidades e escreve roteiros prontos, com fontes e nota de confiança."],
    ["Vídeo", "Você escolhe um e o Claude Code monta e renderiza o vídeo no projeto Remotion."],
  ];
  return (
    <div className="mx-auto flex max-w-[640px] flex-col gap-5 px-5 py-14">
      <div className="flex flex-col items-start gap-2">
        <span className="grid size-10 place-items-center rounded-[10px] border border-line bg-surface text-muted"><Film className="size-5" /></span>
        <h2 className="mt-2 text-[20px] font-semibold tracking-[-0.01em] text-fg">Um tema do canal, vídeos prontos.</h2>
        <p className="max-w-[56ch] text-[13.5px] leading-relaxed text-muted">
          Preencha a especificação ao lado e clique em <span className="text-fg">Criar especificação</span>. Depois disso
          ela pode rodar sozinha, no horário que você escolher.
        </p>
      </div>
      <ol className="flex flex-col divide-y divide-line rounded-[10px] border border-line bg-surface">
        {passos.map(([t, d], i) => (
          <li key={t} className="flex gap-3 px-4 py-3">
            <span className="mt-px font-mono text-[11px] text-faint">{i + 1}</span>
            <span className="flex flex-col gap-0.5">
              <span className="text-[13px] font-medium text-fg">{t}</span>
              <span className="text-[12.5px] leading-relaxed text-muted">{d}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/* ------------------------------------------------------------------ uso do plano */

type Uso = { janelas?: Record<string, { uso: number; renova: number | null }>; atualizado?: string };
const JANELAS: [string, string][] = [["five_hour", "5 h"], ["seven_day", "semana"]];

const renovaEm = (s: number | null) => {
  if (!s) return "";
  const d = new Date(s * 1000), hoje = new Date();
  return d.toDateString() === hoje.toDateString() ? d.toTimeString().slice(0, 5)
    : `${d.toLocaleDateString(undefined, { weekday: "short" })} ${d.toTimeString().slice(0, 5)}`;
};
const corUso = (pct: number) => (pct >= 85 ? "bg-err" : pct >= 60 ? "bg-warn" : "bg-ok");

/** Uso do plano do Claude, na pílula do meio da faixa (onde Imagem e Vídeo mostram a GPU): o último que o Claude
 *  Code informou, em cada produção e no "Testar Claude". */
function UsoClaude(props: { carimbo?: string }) {
  const [uso, setUso] = useState<Uso | null>(null);
  const [atualizando, setAtualizando] = useState(false);
  const carregar = useCallback(() => api.get<Uso>("/conteudo/uso").then(setUso).catch(() => {}), []);
  useEffect(() => { carregar(); }, [carregar, props.carimbo]);
  async function atualizar() {
    setAtualizando(true);
    try {
      await api.post("/conteudo/claude/testar", {});
      await carregar();
    } finally {
      setAtualizando(false);
    }
  }
  const janelas = JANELAS.filter(([k]) => uso?.janelas?.[k]);
  const pior = Math.max(0, ...janelas.map(([k]) => Math.round(uso!.janelas![k].uso * 100)));
  return (
    <span className="flex min-w-0 items-center gap-2.5 rounded-full border border-line bg-surface py-1 pl-3 pr-1.5 text-xs text-muted"
          title={uso?.atualizado ? `Uso do plano do Claude, informado às ${uso.atualizado.slice(11, 16)}` : "Uso do plano do Claude"}>
      <span className={`size-1.5 shrink-0 rounded-full ${janelas.length ? corUso(pior) : "bg-faint"}`} />
      <span className="hidden shrink-0 @5xl/main:inline">Plano do Claude</span>
      {janelas.length === 0 && <span className="truncate text-faint">uso ainda desconhecido</span>}
      {janelas.map(([k, rotulo]) => {
        const j = uso!.janelas![k];
        const pct = Math.round(j.uso * 100);
        return (
          <span key={k} className="flex shrink-0 items-center gap-1.5" title={`${rotulo}: ${pct}% usado · renova ${renovaEm(j.renova)}`}>
            <span className="text-faint">{rotulo}</span>
            <span className="hidden h-1 w-10 overflow-hidden rounded-full bg-line @5xl/main:block">
              <span className={`block h-full rounded-full ${corUso(pct)}`} style={{ width: `${Math.max(4, pct)}%` }} />
            </span>
            <span className="font-mono tabular-nums text-fg-2">{pct}%</span>
          </span>
        );
      })}
      <button className="grid size-5 shrink-0 place-items-center rounded-full text-faint hover:bg-raised hover:text-fg disabled:animate-spin"
              disabled={atualizando} onClick={atualizar} aria-label="Atualizar o uso do plano"
              title="Atualizar agora (faz uma pergunta mínima ao Claude)">
        <Refresh className="size-3" />
      </button>
    </span>
  );
}

/* ------------------------------------------------------------------ ajustes */

function CampoPasta(props: { rotulo: string; dica: string; valor: string; onChange: (v: string) => void; placeholder?: string }) {
  async function escolher() {
    const p = await window.forja?.pickFolder(props.valor || undefined);
    if (p) props.onChange(p);
  }
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-stretch gap-1.5">
        <Caixa rotulo={props.rotulo} className="flex-1">
          <input className={caixaMono} value={props.valor} spellCheck={false} title={props.valor}
                 onChange={(e) => props.onChange(e.target.value)} placeholder={props.placeholder ?? "C:\\..."} />
        </Caixa>
        {window.forja && (
          <button className="grid w-10 shrink-0 place-items-center rounded-[8px] border border-line bg-surface text-muted hover:border-line-strong hover:text-fg"
                  onClick={escolher} title="Escolher pasta" aria-label={`Escolher: ${props.rotulo}`}>
            <FolderOpen className="size-4" />
          </button>
        )}
      </div>
      <span className={ajuda}>{props.dica}</span>
    </div>
  );
}

function PastasPainel(props: { pastas: Pastas; primeira: boolean; onError: (m: string) => void; onSalvo: (p: Pastas) => void }) {
  const [p, setP] = useState(props.pastas);
  const [comandos, setComandos] = useState(props.pastas.comandos.join("\n"));
  const [salvando, setSalvando] = useState(false);
  const [salvoEm, setSalvoEm] = useState("");
  const sujo = JSON.stringify({ ...p, comandos: comandos.split("\n").map((c) => c.trim()).filter(Boolean) }) !== JSON.stringify(props.pastas);

  async function salvar() {
    setSalvando(true);
    try {
      props.onSalvo(await api.put<Pastas>("/conteudo/pastas", {
        ...p, comandos: comandos.split("\n").map((c) => c.trim()).filter(Boolean),
      }));
      setSalvoEm(new Date().toTimeString().slice(0, 5));
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-[680px] flex-col gap-6 px-5 py-6">
      <div className="flex flex-col gap-1">
        <h2 className="text-[16px] font-semibold text-fg">Ajustes do Conteúdo</h2>
        <p className="text-[12.5px] text-muted">Valem para todas as especificações.</p>
      </div>
      {props.primeira && (
        <div className="rounded-[10px] border border-accent-line bg-accent-soft px-3.5 py-3 text-[13px] leading-relaxed text-fg-2">
          Para começar, escolha a pasta onde ficam os <b className="text-fg">estilos</b> dos vídeos (arquivos .md). O Forja
          lê e grava direto nela, e o Claude que monta o vídeo lê os mesmos arquivos.
        </div>
      )}

      <Secao titulo="Pastas">
        <CampoPasta rotulo="Estilos" valor={p.pasta_estilos} onChange={(v) => setP({ ...p, pasta_estilos: v })}
                    dica="Um .md por estilo. Se não tiver _modelo.md, o Forja cria um com o padrão." />
        <CampoPasta rotulo="Projeto de vídeo (Remotion)" valor={p.pasta_projeto} onChange={(v) => setP({ ...p, pasta_projeto: v })}
                    dica="Onde o Claude monta e renderiza o vídeo." />
        <CampoPasta rotulo="Entrega dos vídeos prontos" valor={p.pasta_saida} onChange={(v) => setP({ ...p, pasta_saida: v })}
                    dica="Para onde o .mp4 final é copiado. Padrão: Área de Trabalho." />
      </Secao>

      <Secao titulo="Claude Code">
        <ClaudeCli valor={p.claude_cli ?? ""} onChange={(v) => setP({ ...p, claude_cli: v })} />
        <div className="grid grid-cols-2 gap-1.5">
          <Caixa rotulo="Modelo que edita">
            <input className={caixaMono} list="modelos-claude" value={p.claude_modelo ?? ""} placeholder="padrão do Claude Code"
                   onChange={(e) => setP({ ...p, claude_modelo: e.target.value })} />
            <datalist id="modelos-claude">
              <option value="claude-opus-5-5">Opus 5.5</option>
              <option value="claude-sonnet-5-5">Sonnet 5.5</option>
              <option value="claude-fable-5-1">Fable 5.1</option>
            </datalist>
          </Caixa>
          <Caixa rotulo="Esforço">
            <select className={`${campoCaixa} -ml-1 cursor-pointer`} value={p.claude_esforco ?? ""}
                    onChange={(e) => setP({ ...p, claude_esforco: e.target.value })}>
              <option value="">padrão do Claude Code</option>
              <option value="low">baixo</option>
              <option value="medium">médio</option>
              <option value="high">alto</option>
              <option value="xhigh">muito alto</option>
              <option value="max">máximo</option>
            </select>
          </Caixa>
        </div>
        <CampoPasta rotulo="Conta do Claude (opcional)" valor={p.claude_conta ?? ""} placeholder="a conta do terminal"
                    onChange={(v) => setP({ ...p, claude_conta: v })}
                    dica="Para produzir com outra conta (CLAUDE_CONFIG_DIR). Depois de salvar, faça o login nela uma vez e use Testar." />
        {p.claude_conta && (
          <div className="rounded-[8px] border border-line bg-code px-2.5 py-2 text-[11.5px] text-muted">
            Login nesta conta, uma vez, num PowerShell:
            <pre className="mt-1 select-all whitespace-pre-wrap font-mono text-fg-2">{`$env:CLAUDE_CONFIG_DIR = "${p.claude_conta}"; claude auth login`}</pre>
          </div>
        )}
      </Secao>

      <ServicoAjuste onError={props.onError} />

      <Secao titulo="Comandos liberados">
        <Caixa rotulo="Um por linha; * vale qualquer coisa">
          <textarea className={`${caixaMono} h-32 resize-y leading-relaxed`} value={comandos} spellCheck={false}
                    onChange={(e) => setComandos(e.target.value)} />
        </Caixa>
        <span className={ajuda}>Fora desta lista o comando é negado na hora, e o Claude segue sem ele.</span>
      </Secao>

      <div className="sticky bottom-0 -mx-5 flex items-center justify-end gap-3 border-t border-line bg-bg px-5 py-3">
        {sujo ? <span className="text-[12px] text-warn">Alterações não salvas</span>
          : salvoEm && <span className="text-[12px] text-faint">Salvo às {salvoEm}</span>}
        <button className={btnPrimary} disabled={salvando || !p.pasta_estilos || !sujo} onClick={salvar}>
          <Check className="size-3.5" /> Salvar
        </button>
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
    <div className="flex flex-col gap-1">
      <div className="flex items-stretch gap-1.5">
        <Caixa rotulo="Executável" className="flex-1">
          <input className={caixaMono} value={props.valor} spellCheck={false} onChange={(e) => props.onChange(e.target.value)}
                 placeholder={achado ? `automático: ${achado}` : "caminho do claude.exe"} title={props.valor || achado || ""} />
        </Caixa>
        <button className={`${btn} h-auto`} disabled={teste === "testando"} title="Salve antes se mudou o caminho"
                onClick={async () => {
                  setTeste("testando");
                  setTeste(await api.post<{ ok: boolean; mensagem: string }>("/conteudo/claude/testar", {})
                    .catch((e) => ({ ok: false, mensagem: e.message })));
                }}>
          {teste === "testando" ? "Testando…" : "Testar"}
        </button>
      </div>
      {teste && teste !== "testando" && (
        <span className={`flex items-center gap-1.5 text-[12px] ${teste.ok ? "text-ok" : "text-err"}`}>
          <span className={`size-1.5 rounded-full ${teste.ok ? "bg-ok" : "bg-err"}`} />{teste.mensagem}
        </span>
      )}
      <span className={achado === "" && !props.valor ? "text-[11px] text-warn" : ajuda}>
        {achado === null ? "Procurando…" : achado
          ? "Vazio = sempre o mais novo do PC (npm ou o que vem com o app Claude Desktop). Precisa estar logado."
          : "Não encontrado. Instale com npm i -g @anthropic-ai/claude-code ou informe o caminho do claude.exe."}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ agenda */

function DiasSemana(props: { dias: number[]; onDias: (d: number[]) => void }) {
  return (
    <div className="grid grid-cols-7 gap-1" role="group" aria-label="Dias da semana">
      {DIAS_CURTOS.map((d, i) => {
        const on = props.dias.includes(i);
        return (
          <button key={i} aria-pressed={on} title={DIAS[i]} aria-label={DIAS[i]}
                  onClick={() => props.onDias(on ? props.dias.filter((x) => x !== i) : [...props.dias, i].sort())}
                  className={`h-[30px] rounded-[7px] border font-mono text-[11.5px] ${
                    on ? "border-accent-line bg-accent-soft text-accent-text" : "border-line bg-surface text-faint hover:text-fg"}`}>
            {d}
          </button>
        );
      })}
    </div>
  );
}

function Horarios(props: { rotulo: string; horarios: string[]; onHorarios: (h: string[]) => void }) {
  const h = props.horarios;
  return (
    <div className="flex flex-wrap gap-1.5">
      {h.map((hora, i) => (
        <span key={i} className="flex items-center rounded-[8px] border border-line bg-surface pl-2.5 focus-within:border-focus">
          <span className="mr-2 text-[10.5px] text-faint">{i === 0 ? props.rotulo : "e às"}</span>
          <input type="time" value={hora} aria-label={`Horário ${i + 1}`}
                 onChange={(e) => props.onHorarios(h.map((x, j) => (j === i ? e.target.value : x)))}
                 className="w-[64px] bg-transparent py-1.5 font-mono text-[12.5px] text-fg outline-none" />
          {h.length > 1 ? (
            <button onClick={() => props.onHorarios(h.filter((_, j) => j !== i))} aria-label={`Tirar ${hora}`}
                    className="grid size-7 place-items-center text-faint hover:text-fg"><X className="size-3" /></button>
          ) : <span className="w-2" />}
        </span>
      ))}
      {h.length < 8 && (
        <button onClick={() => props.onHorarios([...h, "19:00"])}
                className="inline-flex items-center gap-1 rounded-[8px] border border-dashed border-line-strong px-2.5 text-[11.5px] text-muted hover:text-fg">
          <Plus className="size-3" /> horário
        </button>
      )}
    </div>
  );
}

/** Horário perdido: o Forja estava desligado e pergunta se ainda roda (o celular recebeu o mesmo aviso). */
function AvisoPerdido(props: { conv: number; nome?: string; perdido: Perdido; onFeito: () => void; onError: (m: string) => void }) {
  const [ocupado, setOcupado] = useState(false);
  async function responder(acao: "rodar" | "pular") {
    setOcupado(true);
    try {
      await api.post(`/conteudo/especificacoes/${props.conv}/agenda/perdido`, { acao });
      props.onFeito();
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setOcupado(false);
    }
  }
  const hora = props.perdido.slot.slice(11, 16);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[10px] border border-warn/40 bg-warn/10 px-3.5 py-2.5">
      <span className="size-1.5 shrink-0 rounded-full bg-warn" />
      <span className="min-w-0 flex-1 text-[12.5px] text-fg-2">
        {props.nome && <b className="font-medium text-fg">{props.nome}: </b>}
        o Forja estava desligado às <span className="font-mono">{hora}</span> e {props.perdido.trilha === "r" ? "a pesquisa" : "o vídeo"} de
        hoje não rodou.
      </span>
      <span className="flex gap-1.5">
        <button className={btn} disabled={ocupado} onClick={() => responder("pular")}>Pular</button>
        <button className={btnPrimary} disabled={ocupado} onClick={() => responder("rodar")}><Play className="size-3.5" /> Rodar agora</button>
      </span>
    </div>
  );
}

function AgendaPainel(props: { carimbo?: string; onAbrir: (id: number) => void; onError: (m: string) => void }) {
  const [itens, setItens] = useState<AgendaItem[] | null>(null);
  const [svc, setSvc] = useState<Servico | null>(null);
  const [pulso, setPulso] = useState(0);
  useEffect(() => {
    api.get<AgendaItem[]>("/conteudo/agenda").then(setItens).catch((e) => props.onError(e.message));
    api.get<Servico>("/conteudo/servico").then(setSvc).catch(() => setSvc(null));
  }, [props.carimbo, pulso]);
  useEffect(() => {   // o andamento (pesquisa, fila, produção) muda sem carimbo: pergunta a cada 5 s
    const t = setInterval(() => setPulso((n) => n + 1), 5000);
    return () => clearInterval(t);
  }, []);

  async function ligar(it: AgendaItem, ligado: boolean) {
    try {
      await api.put(`/conteudo/especificacoes/${it.id}`, { automacao: { ...it.automacao, modo: ligado ? "automatico" : "desligada" } });
      setPulso((n) => n + 1);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  if (!itens) return null;
  const ativas = itens.filter((i) => i.modo !== "desligada");
  return (
    <div className="mx-auto flex w-full max-w-[860px] flex-col gap-5 px-5 py-6">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
        <div className="flex flex-1 flex-col gap-1">
          <h2 className="text-[16px] font-semibold text-fg">Agenda</h2>
          <p className="max-w-[62ch] text-[12.5px] leading-relaxed text-muted">
            Cada especificação ativa roda sozinha nos dias e horários dela: pesquisa as novidades, escolhe o roteiro de
            maior confiança e o Claude Code entrega o vídeo pronto. Uma pesquisa e um vídeo por vez; o resto espera na fila.
          </p>
        </div>
        <span className="font-mono text-[11px] text-faint">{ativas.length} de {itens.length} ativas</span>
      </div>

      {itens.filter((i) => i.perdido).map((i) => (
        <AvisoPerdido key={i.id} conv={i.id} nome={i.nome} perdido={i.perdido!} onError={props.onError} onFeito={() => setPulso((n) => n + 1)} />
      ))}

      <ul className="flex flex-col divide-y divide-line overflow-hidden rounded-[10px] border border-line bg-surface">
        {itens.map((i) => {
          const ligado = i.modo !== "desligada";
          const t = i.p;
          const rodando = ["roteiros", "espera", "fila", "produzindo"].includes(t.etapa ?? "");
          return (
            <li key={i.id} className="flex items-center gap-3 px-4 py-3">
              <button role="switch" aria-checked={ligado} aria-label={`${ligado ? "Desligar" : "Ligar"} a automação de ${i.nome}`}
                      onClick={() => ligar(i, !ligado)}
                      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${ligado ? "bg-accent" : "bg-line-strong"}`}>
                <span className={`absolute top-0.5 size-4 rounded-full bg-white shadow-[0_1px_2px_rgba(0,0,0,.4)] transition-[left] ${ligado ? "left-[18px]" : "left-0.5"}`} />
              </button>
              <button className="flex min-w-0 flex-1 flex-col items-start gap-0.5 text-left" onClick={() => props.onAbrir(i.id)}>
                <span className="truncate text-[13.5px] font-medium text-fg hover:underline">{i.nome}</span>
                <span className="truncate text-[11.5px] text-faint">
                  {ligado ? `${resumoDias(i.automacao.dias)} · ${i.automacao.horarios.join(", ")}${i.modo === "aprovacao" ? " · com aprovação" : ""}` : "desligada"}
                  {" · "}{i.estilo || "sem estilo"} · {i.motor.provider === "claude-mcp" ? "Claude (MCP)" : i.motor.model || "sem modelo"}
                </span>
              </button>
              <span className="hidden shrink-0 flex-col items-end gap-0.5 text-right text-[11.5px] sm:flex">
                {rodando ? (
                  <span className="flex items-center gap-1.5 text-info"><span className="size-1.5 animate-pulse rounded-full bg-info" />{ETAPAS[t.etapa!] ?? t.etapa}</span>
                ) : ligado && i.proximas.p ? (
                  <span className="text-fg-2">próxima {quandoFica(i.proximas.p)}</span>
                ) : null}
                {!rodando && t.etapa && (
                  <span className={t.etapa === "falhou" ? "text-err" : "text-faint"} title={t.aviso || t.escolhido}>
                    última ({t.dia?.slice(8, 10)}/{t.dia?.slice(5, 7)}): {ETAPAS[t.etapa] ?? t.etapa}
                  </span>
                )}
              </span>
            </li>
          );
        })}
        {itens.length === 0 && <li className="px-4 py-6 text-center text-[12.5px] text-muted">Nenhuma especificação ainda. Crie uma em “Nova”.</li>}
      </ul>

      {svc?.suportado && (
        <div className="flex items-center gap-3 rounded-[10px] border border-line px-4 py-3">
          <span className={`size-1.5 shrink-0 rounded-full ${svc.desta_pasta ? "bg-ok" : "bg-faint"}`} />
          <span className="flex-1 text-[12.5px] text-muted">
            {svc.desta_pasta ? "Roda ao ligar o PC, mesmo antes de entrar na conta."
              : "Hoje só roda com o Forja aberto ou na bandeja. Para rodar ao ligar o PC, sem login, instale o serviço em Ajustes."}
          </span>
        </div>
      )}
    </div>
  );
}

type Servico = { suportado: boolean; instalado: boolean; desta_pasta?: boolean; pasta?: string };

/** Ajustes › Ao ligar o PC: a tarefa do Windows que sobe o Forja sem janela antes do login. */
function ServicoAjuste(props: { onError: (m: string) => void }) {
  const [svc, setSvc] = useState<Servico | null>(null);
  const [aberto, setAberto] = useState(false);
  const carregar = useCallback(() => api.get<Servico>("/conteudo/servico").then(setSvc).catch(() => setSvc(null)), []);
  useEffect(() => { carregar(); }, [carregar]);
  useEffect(() => {   // o instalador roda numa janela à parte: confere de tempos em tempos enquanto ela pode estar aberta
    if (!aberto) return;
    const t = setInterval(carregar, 3000);
    const fim = setTimeout(() => setAberto(false), 180_000);
    return () => { clearInterval(t); clearTimeout(fim); };
  }, [aberto, carregar]);
  if (!svc?.suportado) return null;
  async function abrir(acao: "instalar" | "remover") {
    try {
      await api.post(`/conteudo/servico/${acao}`, {});
      setAberto(true);
    } catch (e: any) {
      props.onError(e.message);
    }
  }
  return (
    <Secao titulo="Ao ligar o PC">
      <div className="flex flex-col gap-2.5 rounded-[10px] border border-line bg-surface px-3.5 py-3">
        <div className="flex items-center gap-2.5">
          <span className={`size-1.5 shrink-0 rounded-full ${svc.desta_pasta ? "bg-ok" : svc.instalado ? "bg-warn" : "bg-faint"}`} />
          <span className="flex-1 text-[13px] text-fg">
            {svc.desta_pasta ? "Instalado: as automações rodam ao ligar o PC, sem login"
              : svc.instalado ? "Instalado para outra pasta de dados" : "Não instalado"}
          </span>
          {svc.instalado && <button className={btn} onClick={() => abrir("remover")}>Remover</button>}
          {!svc.desta_pasta && <button className={btnPrimary} onClick={() => abrir("instalar")}>Instalar</button>}
        </div>
        <span className={ajuda}>
          Uma tarefa do Windows sobe o Forja sem janela 30 s depois de ligar, ainda na tela de bloqueio, e ele cuida da
          agenda. Ao instalar, o Windows pede permissão de administrador e a senha da sua conta (guardada pelo
          Agendador de Tarefas; o Forja não a vê). Quando você entrar e abrir o Forja, a janela usa esse mesmo backend.
        </span>
        {aberto && <span className="text-[11.5px] text-info">Termine na janela do Windows que abriu; esta tela se atualiza sozinha.</span>}
      </div>
    </Secao>
  );
}

/* ------------------------------------------------------------------ especificação (painel da direita) */

function LinhaTrilha(props: { rotulo: string; t: Trilha; proxima?: string }) {
  const { t } = props;
  const cor = t.etapa === "falhou" ? "text-err" : t.etapa === "feito" ? "text-ok" : "text-info";
  return (
    <div className="flex flex-col gap-0.5 text-[11.5px]">
      <span className="flex items-baseline justify-between gap-2">
        <span className="text-muted">{props.rotulo}</span>
        {props.proxima && <span className="text-fg-2">{quandoFica(props.proxima)}</span>}
      </span>
      {t.etapa && (
        <span className={cor}>última ({t.dia}): {ETAPAS[t.etapa] ?? t.etapa}{t.escolhido ? ` — "${t.escolhido}"` : ""}</span>
      )}
      {t.aviso && <span className="text-warn">{t.aviso}</span>}
    </div>
  );
}

function SpecPainel(props: {
  conv: number | null;
  carimbo?: string;
  provider: string;
  model: string;
  estilos: Estilo[];
  agenda: Agenda | null;
  onError: (msg: string) => void;
  onConversationChanged: () => void;
  onAbrir: (id: number) => void;
  irParaEstilos: () => void;
  onFechar?: () => void;
  onSalvo: () => void;
}) {
  const [spec, setSpec] = useState<Spec>(SPEC_VAZIA);
  const [sujo, setSujo] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [salvoEm, setSalvoEm] = useState("");
  const aberta = useRef<number | null | undefined>(undefined);   // conv que está no formulário

  useEffect(() => {
    const trocou = aberta.current !== props.conv;
    if (!trocou && sujo) return;   // só o carimbo mudou, com edição na tela: não joga fora o que foi digitado
    aberta.current = props.conv;
    if (trocou) setSalvoEm("");
    setSujo(false);
    if (props.conv === null) {
      setSpec({ ...SPEC_VAZIA, motor: { provider: props.provider, model: props.model } });
      return;
    }
    api.get<Spec>(`/conteudo/especificacoes/${props.conv}`).then(setSpec).catch((e) => props.onError(e.message));
  }, [props.conv, props.carimbo]);

  const muda = (patch: Partial<Spec>) => { setSpec((s) => ({ ...s, ...patch })); setSujo(true); };
  const mudaAuto = (patch: Partial<Spec["automacao"]>) => muda({ automacao: { ...spec.automacao, ...patch } });
  const claude = spec.motor.provider === MOTOR_CLAUDE;
  const nova = props.conv === null;

  async function salvar() {
    setSalvando(true);
    try {
      if (nova) {
        const r = await api.post<Spec>("/conteudo/especificacoes", spec);
        props.onConversationChanged();
        props.onAbrir(r.id!);
      } else {
        setSpec(await api.put<Spec>(`/conteudo/especificacoes/${props.conv}`, spec));
        props.onConversationChanged();
      }
      setSujo(false);
      setSalvoEm(new Date().toTimeString().slice(0, 5));
      props.onSalvo();
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setSalvando(false);
    }
  }

  const agenda = props.agenda;
  return (
    <aside className="flex w-[320px] shrink-0 flex-col overflow-hidden border-l border-line bg-side text-xs" aria-label="Especificação">
      <div className="flex items-center gap-2 px-4 pb-1.5 pt-4">
        <span className="text-[13px] font-semibold text-fg">{nova ? "Nova especificação" : "Especificação"}</span>
        {props.onFechar && (
          <button onClick={props.onFechar} className="ml-auto rounded-[7px] p-1 text-faint hover:bg-raised hover:text-fg" aria-label="Esconder a especificação">
            <X className="size-3.5" />
          </button>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-[18px] overflow-y-auto px-4 pb-4 pt-1.5">
        <Secao titulo="Tema">
          <Caixa rotulo="Nome">
            <input className={campoCaixa} value={spec.nome} placeholder="Notícias de IA" onChange={(e) => muda({ nome: e.target.value })} />
          </Caixa>
          <Caixa rotulo="Do que é o canal">
            <textarea className={`${campoCaixa} h-[74px] resize-none leading-relaxed`} value={spec.tema}
                      placeholder="Lançamentos, riscos e polêmicas de IA que afetam o público geral"
                      onChange={(e) => muda({ tema: e.target.value })} />
          </Caixa>
        </Secao>

        <Secao titulo="Pesquisa">
          <Caixa rotulo="Palavras-chave (separe por vírgula)">
            <input className={campoCaixa} value={spec.palavras_chave.join(", ")} placeholder="OpenAI, Meta, Anthropic"
                   onChange={(e) => muda({ palavras_chave: e.target.value.split(",").map((x) => x.trimStart()) })} />
          </Caixa>
          <Caixa rotulo="Fontes preferidas">
            <input className={campoCaixa} value={spec.fontes.join(", ")} placeholder="techcrunch.com, theverge.com"
                   onChange={(e) => muda({ fontes: e.target.value.split(",").map((x) => x.trimStart()) })} />
          </Caixa>
          <div className="grid grid-cols-2 gap-1.5">
            <Caixa rotulo="Novidades dos últimos">
              <span className="flex items-baseline gap-1">
                <input type="number" min={1} max={30} value={spec.dias} onChange={(e) => muda({ dias: Number(e.target.value) })}
                       className={`${caixaMono} w-10 font-medium [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none`} />
                <span className="text-[11px] text-faint">dias</span>
              </span>
            </Caixa>
            <Caixa rotulo="Roteiros por vez">
              <input type="number" min={1} max={10} value={spec.roteiros} onChange={(e) => muda({ roteiros: Number(e.target.value) })}
                     className={`${caixaMono} font-medium [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none`} />
            </Caixa>
          </div>
          <Segmentado rotulo="Quem pesquisa e escreve" cheio valor={claude ? "claude" : "forja"}
                      onValor={(v) => muda({ motor: v === "claude" ? { provider: MOTOR_CLAUDE, model: "Claude (MCP)" } : { provider: props.provider, model: props.model } })}
                      opcoes={[["forja", "Modelo do Forja"], ["claude", "Claude (MCP)"]]} />
          {claude ? (
            <span className={ajuda}>O pedido fica na fila; uma sessão do Claude conectada ao Forja escreve os roteiros.</span>
          ) : (
            <div className="[&>div]:ml-0">
              <ModelPicker provider={spec.motor.provider} model={spec.motor.model} loadLocal={false} autoFallback={false}
                           onChange={(provider, model) => muda({ motor: { provider, model } })} />
            </div>
          )}
        </Secao>

        <Secao titulo="Vídeo" extra={
          <button className="text-[11px] text-faint hover:text-fg" onClick={props.irParaEstilos}>editar estilos</button>
        }>
          <div className="grid grid-cols-2 gap-1.5">
            {([["vertical", "9:16", "Shorts · Reels"], ["horizontal", "16:9", "YouTube"]] as const).map(([id, prop, para]) => {
              const on = spec.formato === id;
              return (
                <button key={id} onClick={() => muda({ formato: id })} aria-pressed={on}
                        className={`flex items-center gap-2.5 rounded-[8px] border px-2.5 py-2 text-left ${
                          on ? "border-accent-line bg-accent-soft" : "border-line bg-surface hover:border-line-strong"}`}>
                  <span className={`shrink-0 rounded-[3px] border-[1.5px] ${on ? "border-accent-text" : "border-faint"} ${
                    id === "vertical" ? "h-[18px] w-[11px]" : "h-[11px] w-[19px]"}`} />
                  <span className="flex min-w-0 flex-col">
                    <span className={`font-mono text-[12px] font-medium ${on ? "text-accent-text" : "text-fg"}`}>{prop}</span>
                    <span className="truncate text-[10.5px] text-faint">{para}</span>
                  </span>
                </button>
              );
            })}
          </div>
          <Caixa rotulo="Estilo">
            <select className={`${campoCaixa} -ml-1 cursor-pointer font-mono text-[12.5px]`} value={spec.estilo}
                    onChange={(e) => muda({ estilo: e.target.value })}>
              <option value="">escolha…</option>
              {props.estilos.map((e) => <option key={e.nome} value={e.nome}>{e.nome}</option>)}
            </select>
          </Caixa>
          <Caixa rotulo="Observações para o roteirista">
            <textarea className={`${campoCaixa} h-[58px] resize-none leading-relaxed`} value={spec.observacoes}
                      placeholder="Evitar boatos sem fonte; preferir o que afeta o Brasil…"
                      onChange={(e) => muda({ observacoes: e.target.value })} />
          </Caixa>
        </Secao>

        <Secao titulo="Automação">
          <Segmentado rotulo="Automação" cheio valor={spec.automacao.modo} onValor={(m) => mudaAuto({ modo: m })}
                      opcoes={MODOS.map((m) => [m.id, m.label])} />
          <span className={ajuda}>{MODOS.find((m) => m.id === spec.automacao.modo)?.hint}</span>
          {spec.automacao.modo !== "desligada" && (
            <>
              <DiasSemana dias={spec.automacao.dias} onDias={(dias) => mudaAuto({ dias })} />
              {spec.automacao.modo === "aprovacao" && (
                <Caixa rotulo="Roteiros às">
                  <input type="time" className={caixaMono} value={spec.automacao.hora_roteiros}
                         onChange={(e) => mudaAuto({ hora_roteiros: e.target.value })} />
                </Caixa>
              )}
              <Horarios rotulo={spec.automacao.modo === "aprovacao" ? "Vídeo às" : "Roda às"} horarios={spec.automacao.horarios}
                        onHorarios={(horarios) => mudaAuto({ horarios })} />
            </>
          )}
          {agenda && agenda.modo !== "desligada" && !sujo && (
            <div className="flex flex-col gap-2 rounded-[8px] border border-line bg-surface px-2.5 py-2">
              {agenda.modo === "aprovacao" && <LinhaTrilha rotulo="Roteiros" t={agenda.r} proxima={agenda.proximas.r} />}
              <LinhaTrilha rotulo={agenda.modo === "automatico" ? "Roteiro + vídeo" : "Vídeo do escolhido"} t={agenda.p}
                           proxima={agenda.proximas.p} />
            </div>
          )}
          {spec.automacao.modo !== "desligada" && (
            <span className={ajuda}>
              Precisa do Forja no ar: aberto, na bandeja ou no serviço sem login (Ajustes). O PC não suspende. Se ele
              estava desligado no horário, o Forja avisa no celular e pergunta se ainda roda.
            </span>
          )}
        </Secao>
      </div>

      <div className="flex shrink-0 items-center gap-3 border-t border-line px-4 py-3">
        <span className="min-w-0 flex-1 truncate text-[11.5px]">
          {sujo ? <span className="text-warn">Alterações não salvas</span>
            : salvoEm ? <span className="text-faint">Salvo às {salvoEm}</span>
            : nova ? <span className="text-faint">Nome e tema bastam para começar</span> : null}
        </span>
        <button className={btnPrimary} disabled={salvando || !spec.nome.trim() || !spec.tema.trim() || (!nova && !sujo)} onClick={salvar}>
          <Check className="size-3.5" /> {nova ? "Criar especificação" : "Salvar"}
        </button>
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------ estilos */

type Novo = { nome: string; para_que: string; tom: string; duracao: string; descricao: string; ia: { provider: string; model: string } };

function EstilosPainel(props: {
  estilos: Estilo[];
  pasta: string;
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
    <div className="flex h-full min-h-0">
      <nav className="flex w-[248px] shrink-0 flex-col gap-3 overflow-y-auto border-r border-line px-3 py-4" aria-label="Estilos">
        <div className="flex items-center gap-2 px-1">
          <span className="font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint">Estilos</span>
          <span className="font-mono text-[10.5px] text-faint">{props.estilos.length}</span>
          <button className="ml-auto inline-flex items-center gap-1 rounded-[6px] px-1.5 py-0.5 text-[11.5px] text-muted hover:bg-raised hover:text-fg"
                  onClick={comecarNovo}>
            <Plus className="size-3.5" /> Novo
          </button>
        </div>
        <div className="flex flex-col gap-0.5">
          {novo && (
            <div className="rounded-[8px] bg-raised px-2.5 py-2">
              <div className="font-mono text-[12.5px] text-fg">{novo.nome || "novo-estilo"}</div>
              <div className="mt-0.5 text-[11.5px] text-faint">ainda não salvo</div>
            </div>
          )}
          {props.estilos.map((e) => (
            <button key={e.nome} onClick={() => abrir(e.nome)} aria-current={aberto === e.nome ? "true" : undefined}
                    className={`rounded-[8px] px-2.5 py-2 text-left transition-colors ${aberto === e.nome ? "bg-raised" : "hover:bg-surface"}`}>
              <div className="font-mono text-[12.5px] text-fg">{e.nome}</div>
              <div className="mt-0.5 line-clamp-2 text-[11.5px] leading-snug text-muted">{e.resumo}</div>
            </button>
          ))}
          {props.estilos.length === 0 && !novo && <div className="px-1 text-[12px] text-faint">Nenhum estilo na pasta ainda.</div>}
        </div>
        <span className="mt-auto truncate px-1 font-mono text-[10.5px] text-faint" title={props.pasta}>{props.pasta}</span>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        {aberto === null ? (
          <div className="mx-auto flex max-w-[480px] flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
            <span className="grid size-10 place-items-center rounded-[10px] border border-line bg-surface text-muted"><Edit className="size-5" /></span>
            <p className="text-[13px] leading-relaxed text-muted">
              Escolha um estilo ou crie um novo. Todo estilo segue o mesmo modelo (Roteiro, Voz e legenda, Visual, Áudio,
              Engajamento), para o Claude saber onde procurar cada coisa.
            </p>
            <button className={btn} onClick={comecarNovo}><Plus className="size-3.5" /> Novo estilo</button>
          </div>
        ) : (
          <>
            <div className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-2">
              <span className="truncate font-mono text-[12.5px] text-fg">{novo ? (novo.nome || "novo-estilo") : aberto}.md</span>
              {!novo && texto !== original && <span className="size-1.5 shrink-0 rounded-full bg-warn" title="Alterações não salvas" />}
              <div className="ml-auto flex items-center gap-2">
                <Segmentado rotulo="Modo do estilo" valor={ver ? "ver" : "editar"} onValor={(v) => setVer(v === "ver")} desabilitado={!texto}
                            opcoes={[["ver", <><Eye className="size-3.5" /> Ver</>], ["editar", <><Edit className="size-3.5" /> Editar</>]]} />
                {!novo && (
                  <Confirma className={btn} rotulo={<><Trash className="size-3.5" /> Apagar</>}
                            pergunta={`Apagar ${aberto}.md da pasta?`} onSim={apagar} />
                )}
                <button className={btnPrimary} onClick={salvar}
                        disabled={!!ocupado || !texto.trim() || (novo ? !novo.nome : texto === original)}>
                  <Check className="size-3.5" /> {novo ? "Criar estilo" : "Salvar"}
                </button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <div className="mx-auto flex max-w-[860px] flex-col gap-4 px-6 py-5">
                {novo && (
                  <div className="flex flex-col gap-2.5 rounded-[10px] border border-line bg-surface p-3.5">
                    <div className="grid grid-cols-2 gap-1.5">
                      <Caixa rotulo="Nome (minúsculas e hífen)">
                        <input className={caixaMono} value={novo.nome} placeholder="humor-games"
                               onChange={(e) => setNovo({ ...novo, nome: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} />
                      </Caixa>
                      <Caixa rotulo="Para quê">
                        <input className={campoCaixa} value={novo.para_que} placeholder="Notícias de games com humor"
                               onChange={(e) => setNovo({ ...novo, para_que: e.target.value })} />
                      </Caixa>
                      <Caixa rotulo="Tom">
                        <input className={campoCaixa} value={novo.tom} placeholder="Leve, zoeira, rápido"
                               onChange={(e) => setNovo({ ...novo, tom: e.target.value })} />
                      </Caixa>
                      <Caixa rotulo="Duração">
                        <input className={campoCaixa} value={novo.duracao} placeholder="30–45s"
                               onChange={(e) => setNovo({ ...novo, duracao: e.target.value })} />
                      </Caixa>
                    </div>
                    <Caixa rotulo="Descreva o estilo (para gerar com IA)">
                      <textarea className={`${campoCaixa} h-16 resize-none leading-relaxed`} value={novo.descricao}
                                placeholder="Cores neon, memes de jogos, narrador animado, efeitos sonoros de videogame…"
                                onChange={(e) => setNovo({ ...novo, descricao: e.target.value })} />
                    </Caixa>
                    <div className="flex flex-wrap items-center gap-2">
                      <button className={btn} disabled={!novo.nome || !novo.descricao.trim() || !!ocupado} onClick={comIa}>
                        {ocupado === "gerando" ? "Gerando…" : "Gerar com IA"}
                      </button>
                      <div className="[&>div]:ml-0">
                        <ModelPicker provider={novo.ia.provider} model={novo.ia.model} loadLocal={false} autoFallback={false}
                                     onChange={(provider, model) => setNovo({ ...novo, ia: { provider, model } })} />
                      </div>
                      <span className="text-[11.5px] text-faint">ou</span>
                      <button className="text-[12px] text-muted underline-offset-2 hover:text-fg hover:underline disabled:opacity-40"
                              disabled={!novo.nome} onClick={doModelo}>comece do modelo em branco</button>
                    </div>
                  </div>
                )}
                {ver ? (
                  <div className="prose-sm"><Markdown text={texto} /></div>
                ) : (
                  <textarea className="min-h-[520px] w-full rounded-[10px] border border-line bg-surface px-3.5 py-3 font-mono text-[12.5px] leading-relaxed text-fg outline-none placeholder:text-faint focus:border-focus"
                            value={texto} spellCheck={false}
                            placeholder={novo ? "Gere com IA ou comece do modelo, depois ajuste aqui." : ""}
                            onChange={(e) => setTexto(e.target.value)} />
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

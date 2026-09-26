import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CartaoEstado, { botaoEstado, botaoEstadoPrimario } from "./CartaoEstado";
import { api, uploadReferencia } from "../api";
import type { ImageOpts, LocalState, LoteImagem, LoteMeta, Message, PedidoMeta, SeedMode, SlotImagem } from "../types";
import type { Section } from "./Controls";
import { AmpliarArquivo, PainelAmpliar } from "./AmpliarVideo";
import { ArrowRight, ArrowUp, Check, Copy, Edit, FolderOpen, PanelRight, Plus, Refresh, Robo, Sliders, Square, TelaCheia, Trash, Undo, X } from "./icons";
import { campoPrompt, larguraNumero, numeroPilula, pilula, pilulaLigada } from "./Composer";
import { btn, btnPrimary, SAMPLERS } from "./LocalPanel";
import { Lightbox } from "./MessageView";
import SeletorFormato, { type Forma } from "./Formato";
import { colunasPara, distribuir } from "./mosaico";
import MascaraEditor, { type ModoPintura } from "./MascaraEditor";
import { Modal } from "./Modal";
import ModelPicker from "./ModelPicker";

const POLL_MS = 1500; // só enquanto um lote roda; fora disso a tela fica parada
// O modelo que reescreve o prompt é separado do modelo do Chat: quem gera imagem costuma querer
// um modelo pequeno e rápido aqui, não o mesmo que responde no chat.
const KEY_LLM = "forja.imagem.llm";
const KEY_PARAMETROS = "forja.imagem.parametros";

export const SEEDS: { id: SeedMode; label: string; hint: string }[] = [
  { id: "incremental", label: "Incremental", hint: "base, base+1, base+2… variações próximas e repetíveis" },
  { id: "aleatoria", label: "Aleatória", hint: "uma semente sorteada por imagem (mas anotada, dá para repetir)" },
  { id: "fixa", label: "Fixa", hint: "a mesma em todas: compara modelos com a variável travada" },
];

export const CORES: Record<LoteImagem["status"], string> = {
  pendente: "text-faint",
  gerando: "text-sky-300",
  pronta: "text-muted",
  mantida: "text-emerald-300",
  descartada: "text-faint",
  cancelada: "text-faint",
  erro: "text-red-300",
  interrompida: "text-amber-300",
};
// O que "Continuar" gera de novo (mesma lista do backend, lotes.A_REFAZER).
export const A_REFAZER: LoteImagem["status"][] = ["interrompida", "pendente", "cancelada", "erro"];

const MAX_REFS = 10;  // Qwen-Image 2.1; o backend barra também
export const urlDa = (p: string) => `/api/local/image/file?path=${encodeURIComponent(p)}`;
// O arquivo do slot troca de conteúdo sem trocar de caminho ("Usar no site"): a semente na URL fura o cache.
const srcDe = (img: LoteImagem) => urlDa(img.path) + (img.destino ? `&v=${img.seed}` : "");
/** Largura/altura de verdade da imagem (o slot tem tamanho próprio; senão, o do lote), presa entre 1:2 e 2.4:1
 *  para um banner não virar fita nem um retrato comprido empurrar a grade. */
const proporcaoDe = (img: LoteImagem, opts?: { width?: number; height?: number }) => {
  const w = img.width || opts?.width, h = img.height || opts?.height;
  return w && h ? Math.min(2.4, Math.max(0.5, w / h)) : 1;
};

/** Fotos empilhadas por trás do card: o slot tem mais versões para escolher. */
/** Galeria em mosaico: colunas independentes, cada cartão na coluna mais baixa (ver mosaico.ts). */
function Mosaico({ proporcoes, children }: { proporcoes: number[]; children: React.ReactNode[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [n, setN] = useState(4);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setN(colunasPara(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const chave = proporcoes.join();
  const cols = useMemo(() => distribuir(proporcoes, n), [chave, n]);
  return (
    <div ref={ref} className="flex items-start gap-3">
      {cols.map((c, i) => (
        <div key={i} className="flex min-w-0 flex-1 flex-col gap-3">{c.map((k) => children[k])}</div>
      ))}
    </div>
  );
}

function Pilha(props: { n: number; largo?: boolean; children: React.ReactNode }) {
  return (
    <div className={`relative ${props.largo ? "col-span-2" : ""}`}>
      {props.n > 2 && <div aria-hidden className="absolute inset-0 translate-x-2 -translate-y-2 rotate-[3deg] rounded-xl border border-line bg-surface" />}
      {props.n > 1 && <div aria-hidden className="absolute inset-0 translate-x-1 -translate-y-1 rotate-[1.5deg] rounded-xl border border-line bg-raised" />}
      <div className="relative">{props.children}</div>
    </div>
  );
}
const rodando = (m: Message) => m.role === "assistant" && m.status === "running";

export default function ImagensView(props: {
  conv: number | null;
  onAbrirConversa: (id: number, kind: Section) => void; // "Ir para o chat" de uma conversa aberta pela IA
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
  carimbo?: string; // muda quando qualquer conversa muda (/api/activity): lote criado pelo celular aparece sem recarregar
}) {
  const [st, setSt] = useState<LocalState | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [o, setO] = useState<ImageOpts | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  // Imagens a editar (-r do sd.cpp), na ordem. Vazio = gerar do zero.
  const [refs, setRefs] = useState<string[]>([]);
  const arquivo = useRef<HTMLInputElement>(null);
  // Referências cujo arquivo não está mais lá (a miniatura não carregou): pedem para reanexar.
  const [sumidas, setSumidas] = useState<Set<string>>(new Set());
  const trocar = useRef<string | null>(null);
  const [pintando, setPintando] = useState<string | null>(null);  // referência aberta no editor de máscara  // "Reanexar": o próximo arquivo escolhido entra no lugar desta
  const [count, setCount] = useState(4);
  const [seedMode, setSeedMode] = useState<SeedMode>("incremental");
  // Painel Parâmetros: fixo à direita (o design), liga/desliga no botão do topo; lembra a escolha.
  const [abrirAjustes, setAbrirAjustesBruto] = useState(() => {
    try {
      return localStorage.getItem(KEY_PARAMETROS) !== "0";
    } catch {
      return true;
    }
  });
  const setAbrirAjustes = (v: boolean | ((a: boolean) => boolean)) =>
    setAbrirAjustesBruto((a) => {
      const n = typeof v === "function" ? v(a) : v;
      try {
        localStorage.setItem(KEY_PARAMETROS, n ? "1" : "0");
      } catch { /* sem storage: vale só agora */ }
      return n;
    });
  const [negAberto, setNegAberto] = useState(false);
  // Tem LLM na VRAM: guarda o pedido para repetir com confirm=true se a pessoa aceitar descarregar.
  const [perguntando, setPerguntando] = useState<(() => void) | null>(null);
  // 409: ou é o LLM deste Forja (o texto fixo abaixo) ou outro programa na GPU (a mensagem do backend)
  const [motivo, setMotivo] = useState("");
  const [aviso, setAviso] = useState("");  // resultado de ações do site (otimizar), sem cara de erro
  const [trocandoEstilo, setTrocandoEstilo] = useState(false);
  const [slotAberto, setSlotAberto] = useState<string | null>(null);  // modal de variações de um slot
  // Conversa aberta pela IA (skill gerar-imagens): de qual chat e projeto vieram os slots. null = comum.
  const [origem, setOrigem] = useState<Origem | null>(null);
  const [melhorando, setMelhorando] = useState(false);
  const [zoom, setZoom] = useState<string | null>(null);
  const [ampliarPc, setAmpliarPc] = useState(false);
  // Na primeira vez herda o par do Chat; a partir daí é escolha própria desta aba.
  const [llm, setLlm] = useState(() => {
    try {
      const salvo = JSON.parse(localStorage.getItem(KEY_LLM) ?? "null");
      if (salvo?.model) return salvo as { provider: string; model: string };
    } catch {
      /* localStorage corrompido: cai no do Chat */
    }
    return { provider: props.provider, model: props.model };
  });
  const fim = useRef<HTMLDivElement>(null);
  // O erro do App só aparece no composer do chat, que esta aba não mostra: sem isto, falha era silêncio.
  const [erro, setErro] = useState("");
  const mostrarErro = useCallback((e: string) => setErro(e), []);

  useEffect(() => {
    localStorage.setItem(KEY_LLM, JSON.stringify(llm));
  }, [llm]);

  const ocupado = messages.some(rodando);

  const carregarLocal = useCallback(async () => {
    try {
      const novo = await api.get<LocalState>("/local");
      setSt(novo);
      setO((atual) => atual ?? novo.image);
      // o padrão global pode apontar para um arquivo que não é modelo de imagem (sobra da aba antiga)
      setModels((atual) =>
        atual.length ? atual : novo.image_models.some((m) => m.path === novo.image.model) ? [novo.image.model] : [],
      );
    } catch (e: any) {
      mostrarErro(e.message);
    }
  }, [mostrarErro]);

  useEffect(() => {
    carregarLocal();
  }, [carregarLocal]);

  // Recebe o id: logo depois de criar a conversa, `props.conv` ainda é o null do render anterior.
  const carregarConversa = useCallback(
    async (id: number | null = props.conv) => {
      if (id === null) return setMessages([]);
      try {
        const c = await api.get<{ messages: Message[] }>(`/conversations/${id}`);
        setMessages(c.messages);
      } catch (e: any) {
        mostrarErro(e.message);
      }
    },
    [props.conv, mostrarErro],
  );
  // Lote criado ou ampliado em outro aparelho (celular): o carimbo da atividade muda e a conversa aberta
  // recarrega. Sozinha, a tela só consulta enquanto sabe de um lote rodando.
  useEffect(() => {
    if (props.carimbo) carregarConversa();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.carimbo]);

  useEffect(() => {
    carregarConversa();
  }, [carregarConversa]);

  const carregarOrigem = useCallback(() => {
    if (props.conv === null) return setOrigem(null);
    const conv = props.conv;
    api
      .get<{ origem: Origem | null }>(`/imagens/${conv}/origem`)
      .then((r) => conv === props.conv && setOrigem(r.origem))
      .catch((e) => mostrarErro(e.message));
  }, [props.conv, mostrarErro]);

  useEffect(() => {
    setOrigem(null);
    carregarOrigem();
  }, [carregarOrigem]);

  // Um lote que acabou muda o que falta gerar (e o código pode ter mudado): a faixa e a fila acompanham.
  const lotesRodando = messages.filter(rodando).length;
  useEffect(() => {
    if (!lotesRodando) carregarOrigem();
  }, [lotesRodando, carregarOrigem]);

  // Poll só enquanto há lote em andamento: a thread do backend preenche o meta imagem a imagem.
  useEffect(() => {
    if (!ocupado) return;
    const t = setInterval(() => carregarConversa(), POLL_MS);
    return () => clearInterval(t);
  }, [ocupado, carregarConversa]);

  useEffect(() => {
    fim.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const lotes = useMemo(() => {
    const out: { pedido: Message; resposta: Message }[] = [];
    for (let i = 0; i < messages.length; i++) {
      const m = messages[i];
      if (m.role === "assistant" && (m.meta as LoteMeta | null)?.images) {
        out.push({ pedido: messages[i - 1] ?? m, resposta: m });
      }
    }
    return out;
  }, [messages]);

  // Todas as versões de cada slot, de todos os lotes: a que está no site (destino) e as variações (slot).
  const variacoes = useMemo(() => {
    const m = new Map<string, { img: LoteImagem; lote: Message }[]>();
    for (const { resposta } of lotes)
      for (const img of (resposta.meta as LoteMeta).images) {
        const chave = img.destino ?? img.slot;
        // Cancelada, com erro ou interrompida não é versão: também tem `destino`, e o card do slot
        // pegava ela (cinza, "cancelada") no lugar da que foi gerada depois com outro modelo.
        if (chave && !["cancelada", "erro", "interrompida"].includes(img.status))
          m.set(chave, [...(m.get(chave) ?? []), { img, lote: resposta }]);
      }
    return m;
  }, [lotes]);

  // Variações de um slot moram no modal dele: não aparecem como lote na tela.
  // Lote de slots que falhou inteiro (cancelado, erro) e cujos slots já saíram em outro lote é sobra: mostrava
  // as imagens de novo, com um "Continuar" que refaria tudo no modelo do lote que falhou.
  const superado = (l: Message) => (l.meta as LoteMeta).images.every((i) =>
    ["cancelada", "erro", "interrompida"].includes(i.status) && (i.destino ?? i.slot) && variacoes.has((i.destino ?? i.slot)!));
  const visiveis = lotes.filter((l) => !(l.resposta.meta as LoteMeta).variacao_de && !superado(l.resposta));
  // A fila dos slots aparece até sair o primeiro lote dela.
  const slotsPendentes = origem?.pendentes.length ? { message_id: origem.message_id, slots: origem.pendentes } : null;
  const foraDoCodigo = new Set(origem?.fora_do_codigo ?? []);
  // O card do slot mostra a versão que o site usa agora, venha de qual lote vier.
  const noSite = (img: LoteImagem) => {
    const chave = img.destino ?? img.slot;
    return (chave && variacoes.get(chave)?.find((v) => v.img.destino)?.img) || img;
  };

  const set = <K extends keyof ImageOpts>(k: K, v: ImageOpts[K]) => setO((c) => c && { ...c, [k]: v });

  const divisao = useMemo(() => {
    // Mesma conta do backend (_distribuir): blocos contíguos, resto nos primeiros.
    if (!models.length) return new Map<string, number>();
    const por = Math.floor(count / models.length);
    const resto = count % models.length;
    return new Map(models.map((m, i) => [m, por + (i < resto ? 1 : 0)]));
  }, [models, count]);

  async function gerar(confirm = false) {
    const slots = slotsPendentes;
    if (!o || (!slots && !prompt.trim()) || !models.length) return;
    if (slots) return gerarDoBackend({ slots_de: slots.message_id }, confirm);
    if (refs.some((r) => sumidas.has(r))) {
      mostrarErro("Uma imagem de referência não foi encontrada (movida ou apagada): reanexe ou tire da edição.");
      return;
    }
    if (refs.length > MAX_REFS) {
      mostrarErro(`No máximo ${MAX_REFS} imagens de referência (limite do Qwen-Image 2.1), máscaras incluídas.`);
      return;
    }
    try {
      // O que está na tela também vira o padrão da ferramenta image_generate do agente.
      await api.put("/local/image/defaults", { ...o, model: models[0] });
      const conv = await props.ensureConversation();
      await api.post(`/imagens/${conv}/gerar`, {
        prompt,
        // offload/flash attention são do modelo (IA local › Modelos): o global não passa por cima
        opts: { ...o, model: undefined, seed: undefined, offload: undefined, flash_attn: undefined, vae_tiling: undefined, te_cpu: undefined, preview: undefined, taesd: undefined },
        models,
        count,
        seed: o.seed,
        seed_mode: seedMode,
        confirm,
        refs,
      });
      setPerguntando(null);
      setErro("");
      setPrompt(""); // "Reaproveitar" no lote traz o texto de volta
      props.onConversationChanged();
      carregarConversa(conv);
    } catch (e: any) {
      if (e.status === 409) { setMotivo(e.message); setPerguntando(() => () => gerar(true)); } // VRAM: a conta é do usuário
      else mostrarErro(e.message);
    }
  }

  // Fila dos slots e variações de um slot: prompt, tamanho e arquivo vêm do backend; daqui só modelo e ajustes.
  async function gerarDoBackend(
    extra:
      | { slots_de: number }
      | { variar: { message_id: number; path: string; prompt?: string }; count: number }
      | { estilo: string; count: number },
    confirm = false,
  ) {
    if (!o) return;
    if (!models.length) {
      setAbrirAjustes(true);
      return mostrarErro("Escolha nos ajustes o modelo que gera as imagens.");
    }
    try {
      await api.put("/local/image/defaults", { ...o, model: models[0] });
      const conv = await props.ensureConversation();
      await api.post(`/imagens/${conv}/gerar`, {
        opts: { ...o, model: undefined, seed: undefined, width: undefined, height: undefined, offload: undefined, flash_attn: undefined, vae_tiling: undefined, te_cpu: undefined, preview: undefined, taesd: undefined },
        models,
        seed: o.seed,
        seed_mode: seedMode,
        confirm,
        ...extra,
      });
      setPerguntando(null);
      setErro("");
      setTrocandoEstilo(false);
      props.onConversationChanged();
      carregarConversa(conv);
    } catch (e: any) {
      if (e.status === 409) { setMotivo(e.message); setPerguntando(() => () => gerarDoBackend(extra, true)); }
      else mostrarErro(e.message);
    }
  }

  // prompt: o texto editado no modal de variações; sem ele, o mesmo da imagem
  const regerar = (message_id: number, path: string, prompt?: string) =>
    gerarDoBackend({ variar: { message_id, path, ...(prompt ? { prompt } : {}) }, count });

  async function otimizar() {
    try {
      const r = await api.post<{ imagens: { nome: string; png: number; webp: number }[]; arquivos: string[] }>(
        `/imagens/${props.conv}/otimizar`, {});
      const kb = (n: number) => Math.round(n / 1024);
      const antes = r.imagens.reduce((t, i) => t + i.png, 0), depois = r.imagens.reduce((t, i) => t + i.webp, 0);
      setAviso(`${r.imagens.length} imagens em .webp: ${kb(antes)} KB → ${kb(depois)} KB. `
        + (r.arquivos.length ? `O código agora aponta para o .webp em ${r.arquivos.join(", ")}.` : "O código já apontava para .webp."));
      carregarOrigem();
    } catch (e: any) {
      mostrarErro(e.message);
    }
  }

  async function escolher(slot: string, path: string) {
    try {
      await api.post(`/imagens/${props.conv}/escolher`, { slot, path });
      carregarConversa();
    } catch (e: any) {
      mostrarErro(e.message);
    }
  }

  async function melhorar() {
    if (!prompt.trim() || !llm.model) return;
    setMelhorando(true);
    try {
      const r = await api.post<{ prompt: string }>("/imagens/prompt", { prompt, ...llm });
      setPrompt(r.prompt);
    } catch (e: any) {
      mostrarErro(e.message);
    } finally {
      setMelhorando(false);
    }
  }

  function editar(path: string) {
    setRefs((r) => (r.includes(path) ? r : [...r, path]));
  }

  async function anexar(files: FileList | null) {
    const velha = trocar.current;
    trocar.current = null;
    try {
      for (const f of Array.from(files ?? [])) {
        // No app, o caminho do próprio arquivo: nada é copiado. Sem caminho (colada, navegador), cópia.
        const noDisco = window.forja?.caminhoDe?.(f);
        const path = noDisco
          ? (await api.post<{ path: string }>("/imagens/referencia/caminho", { path: noDisco })).path
          : await uploadReferencia(f);
        if (velha) {
          setRefs((r) => r.map((x) => (x === velha ? path : x)).filter((x, i, a) => a.indexOf(x) === i));
          setSumidas((s) => new Set([...s].filter((x) => x !== velha && x !== path)));
          break;  // reanexar troca uma só
        }
        editar(path);
      }
    } catch (e: any) {
      mostrarErro(e.message);
    }
  }

  // Qwen-Image 2.1. Máscara (branco = muda): vai logo depois da original. O modelo a reconhece sozinho —
  // dizer "black-and-white mask" no prompt deixou a imagem inteira em preto e branco no teste.
  // Anotação: a imagem com os traços entra no lugar da original.
  async function usarPintura(original: string, png: Blob, modo: ModoPintura) {
    setPintando(null);
    try {
      const nova = await uploadReferencia(new File([png], `${modo}.png`, { type: "image/png" }));
      const sem = refs.filter((x) => x !== nova);
      const n = sem.indexOf(original) + 1;
      setRefs(modo === "mascara" ? [...sem.slice(0, n), nova, ...sem.slice(n)] : sem.map((x) => (x === original ? nova : x)));
    } catch (e: any) {
      mostrarErro(e.message);
    }
  }

  function reaproveitar(meta: LoteMeta, pedido: Message) {
    const usados: string[] = (pedido.meta as PedidoMeta | null)?.models ?? [];
    setRefs((pedido.meta as PedidoMeta | null)?.refs ?? []);
    setO((c) => c && { ...c, ...meta.opts });
    if (usados.length) setModels(usados);
    setCount(meta.count);
    setSeedMode(meta.seed_mode);
    setPrompt(pedido.content);
    setAbrirAjustes(true);
  }

  if (!st || !o) return <div className="grid h-full place-items-center text-sm text-faint">Carregando…</div>;

  const semRuntime = !st.runtimes.sd.installed;
  const semModelo = !st.image_models.length;
  // O backend barra também; aqui é só para avisar antes de apertar o botão.
  const naoEditam = refs.length
    ? st.image_models.filter((m) => models.includes(m.path) && !m.req?.edita).map((m) => m.name)
    : [];

  return (
    <>
      {slotAberto && (
        <Variacoes
          slot={slotAberto}
          itens={variacoes.get(slotAberto) ?? []}
          count={count}
          ocupado={ocupado || st.image_busy}
          onZoom={setZoom}
          onEscolher={(path) => escolher(slotAberto, path)}
          onRegerar={(prompt) => {
            const todas = variacoes.get(slotAberto) ?? [];
            const base = todas.find((v) => v.img.destino) ?? todas[0];
            if (base) regerar(base.lote.id, base.img.path, prompt);
          }}
          onClose={() => setSlotAberto(null)}
        />
      )}
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
      {ampliarPc && (
        <Modal label="Ampliar imagem do PC" onClose={() => setAmpliarPc(false)} className="w-full max-w-2xl rounded-xl border border-line bg-surface p-4">
          <p className="mb-3 text-sm text-fg">Ampliar imagem do PC</p>
          <AmpliarArquivo
            imagem
            ensureConversation={props.ensureConversation}
            onError={mostrarErro}
            onPronto={(conv) => {
              setAmpliarPc(false);
              props.onConversationChanged();
              carregarConversa(conv);
            }}
          />
        </Modal>
      )}
      {pintando && (
        <MascaraEditor src={urlDa(pintando)} onClose={() => setPintando(null)} onPronta={(png, modo) => usarPintura(pintando, png, modo)} />
      )}
      <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
      <BarraTopo
        contagem={visiveis.reduce((n, l) => n + (l.resposta.meta as LoteMeta).images.length, 0)}
        unidade={["imagem", "imagens"]}
        status={(() => {
          const viva = visiveis.find((l) => rodando(l.resposta));
          if (viva) {
            const imgs = (viva.resposta.meta as LoteMeta).images;
            const feitas = imgs.filter((i) => ["pronta", "mantida", "descartada"].includes(i.status)).length;
            const resta = imgs.reduce((t, i) => t + (i.status === "gerando" ? i.restante ?? 0 : 0), 0);
            return { cor: "bg-accent animate-pulse", texto: `Gerando ${Math.min(feitas + 1, imgs.length)} de ${imgs.length}`, meta: resta ? duracao(resta) : "" };
          }
          if (st.image_busy) return { cor: "bg-warn", texto: "GPU ocupada com outra geração", meta: "" };
          return st.gpu_video?.nome ? { cor: "bg-ok", texto: "GPU livre para o sd.cpp", meta: `${st.gpu_video.nome}${st.gpu_video.gb ? ` · ${Math.round(st.gpu_video.gb)} GB` : ""}` } : null;
        })()}
        parametros={abrirAjustes}
        onParametros={() => setAbrirAjustes((v) => !v)}
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1400px] px-5 py-4">
          {origem && (
            <Origem
              origem={origem}
              temImagens={visiveis.length > 0}
              ocupado={ocupado || st.image_busy}
              onAbrir={props.onAbrirConversa}
              onOtimizar={otimizar}
              onEstilo={() => setTrocandoEstilo(true)}
            />
          )}
          {trocandoEstilo && origem && (
            <EstiloTodas
              estilo={origem.estilo}
              count={count}
              imagens={new Set([...variacoes.values()].flat().filter((v) => v.img.destino).map((v) => v.img.destino)).size}
              onGerar={(estilo) => gerarDoBackend({ estilo, count })}
              onClose={() => setTrocandoEstilo(false)}
            />
          )}
          {!visiveis.length && !origem && (
            <div className="mt-[18vh] text-center">
              <div className="text-3xl font-semibold">Imagens</div>
              <div className="text-3xl text-faint">Descreva, gere várias, fique com as boas.</div>
              <p className="mx-auto mt-4 max-w-lg text-sm text-muted">
                {semRuntime
                  ? "O stable-diffusion.cpp ainda não está instalado — baixe o runtime em IA local › Imagem."
                  : semModelo
                    ? "Nenhum modelo de imagem nas pastas — baixe um .safetensors em IA local › Baixar."
                    : "As reprovadas vão para a subpasta descartadas/ e somem sozinhas depois do prazo — nada é apagado na hora."}
              </p>
            </div>
          )}

          {visiveis.map(({ pedido, resposta }, i) => (
            <Lote
              key={resposta.id}
              numero={i + 1}
              ultimo={i === visiveis.length - 1}
              pedido={pedido}
              resposta={resposta}
              noSite={noSite}
              foraDoCodigo={foraDoCodigo}
              versoes={(chave) => variacoes.get(chave) ?? []}
              onZoom={setZoom}
              onAbrirSlot={(chave) => ((variacoes.get(chave)?.length ?? 0) > 1 ? (setSlotAberto(chave), true) : false)}
              onRegerar={regerar}
              onError={mostrarErro}
              onMudou={carregarConversa}
              onReaproveitar={() => reaproveitar(resposta.meta as LoteMeta, pedido)}
              onEditar={editar}
              onSemente={(s) => {
                setO((c) => c && { ...c, seed: s });
                setSeedMode("fixa");
                setAbrirAjustes(true);
              }}
            />
          ))}
          <div ref={fim} />
        </div>
      </div>

      <div className="shrink-0 px-5 pb-4">
        <div className="mx-auto max-w-[1400px]">
          {erro && (
            <div className="mb-2 flex items-start gap-2 rounded-xl border border-red-900/70 bg-red-950/30 p-2.5 text-xs text-red-200">
              <p className="min-w-0 flex-1 whitespace-pre-wrap break-words">{erro}</p>
              <button onClick={() => setErro("")} title="Fechar" className="text-red-300 hover:text-red-100">
                <X className="size-3.5" />
              </button>
            </div>
          )}
          {aviso && (
            <div className="mb-2 flex items-start gap-2 rounded-xl border border-line bg-surface p-2.5 text-xs text-muted">
              <p className="min-w-0 flex-1">{aviso}</p>
              <button onClick={() => setAviso("")} title="Fechar" className="text-faint hover:text-fg">
                <X className="size-3.5" />
              </button>
            </div>
          )}
          {perguntando && (
            <CartaoEstado tom="aviso" className="mb-2"
              titulo={motivo.startsWith("Outro programa") ? motivo : `O modelo ${st.server.alias} está carregado na VRAM.`}
              acoes={<>
                <button className={botaoEstadoPrimario} onClick={() => perguntando?.()}>
                  {motivo.startsWith("Outro programa") ? "Gerar mesmo assim" : "Descarregar e gerar"}
                </button>
                <button className={botaoEstado} onClick={() => setPerguntando(null)}>Cancelar</button>
              </>}>
              {!motivo.startsWith("Outro programa") &&
                "O sd.cpp precisa dessa memória. Descarregar derruba o cache de contexto do chat: a próxima mensagem de lá reprocessa o histórico inteiro. A conversa em si não se perde."}
            </CartaoEstado>
          )}

          {slotsPendentes && (
            <div className="mb-2 rounded-xl border border-line bg-surface p-3.5 text-xs">
              <div className="mb-2 flex items-center gap-2">
                <span className="font-medium text-fg">
                  {slotsPendentes.slots.length} {visiveis.length ? "imagens novas" : "imagens"} pedidas pelo chat
                </span>
                <span className="text-faint">cada uma vai direto para o arquivo que o código aponta</span>
              </div>
              <ul className="flex max-h-48 flex-col gap-1 overflow-y-auto">
                {slotsPendentes.slots.map((s) => (
                  <li key={s.nome} className="flex items-baseline gap-2" title={s.caminho}>
                    <span className="shrink-0 font-mono text-fg">{s.nome}</span>
                    <span className="shrink-0 text-faint">{s.largura && s.altura ? `${s.largura}×${s.altura}` : `${o.width}×${o.height}`}</span>
                    <span className="min-w-0 flex-1 truncate text-muted">{s.prompt}</span>
                    <span className="shrink-0 font-mono text-faint">{s.rel}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-2.5 flex items-center gap-2">
                <button
                  className={btnPrimary}
                  onClick={() => gerar()}
                  disabled={!models.length || semRuntime || st.image_busy || ocupado}
                >
                  Gerar {slotsPendentes.slots.length} imagens
                </button>
                <button className={btn} onClick={() => setAbrirAjustes(true)}>
                  {models.length ? `${models.length} modelo${models.length > 1 ? "s" : ""} · ajustes` : "Escolher modelo"}
                </button>
                {st.image_busy || ocupado ? <span className="text-faint">Já tem imagem sendo gerada.</span> : null}
              </div>
            </div>
          )}

          {origem ? (
            !slotsPendentes && (
              <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface px-3.5 py-2.5 text-xs text-muted">
                <Robo className="size-4 shrink-0 text-sky-300/80" />
                <span className="min-w-0 flex-1">
                  Esta conversa gera só as imagens que o chat pediu para o site. Para refazer uma, use <Refresh className="inline size-3" /> no
                  card ou clique na foto. Imagem avulsa: abra uma conversa nova em Imagens.
                </span>
                <label className={`${pilula} focus-within:border-focus`} title="Quantas versões cada Regerar gera">
                  <Copy className="size-3.5" />
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={count}
                    onChange={(e) => setCount(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
                    className={numeroPilula}
                    style={larguraNumero(count)}
                  />
                  {count === 1 ? "versão por vez" : "versões por vez"}
                </label>
                <button className={`${pilula} ${abrirAjustes ? pilulaLigada : ""}`} onClick={() => setAbrirAjustes((v) => !v)}>
                  <Sliders className="size-3.5" />
                  {models.length ? `${models.length} modelo${models.length > 1 ? "s" : ""}` : "Escolher modelo"}
                </button>
              </div>
            )
          ) : (
          <>
          <div className="rounded-2xl border border-line bg-surface px-3.5 py-3 shadow-[0_-10px_30px_rgba(0,0,0,.35)] transition-colors duration-150 focus-within:border-focus">
            <div className="flex items-start gap-2.5">
              <div className="flex max-w-[45%] shrink-0 flex-wrap gap-1.5">
                {refs.map((r, i) => (
                  <div key={r} className="relative" title={sumidas.has(r) ? `Não encontrada: ${r}` : r}>
                    {sumidas.has(r) ? (
                      <button
                        onClick={() => {
                          trocar.current = r;
                          arquivo.current?.click();
                        }}
                        className="grid size-[52px] place-items-center rounded-[9px] border border-dashed border-warn/70 bg-warn/5 px-1 text-center text-[10px] leading-tight text-warn hover:bg-warn/10"
                      >
                        não achada
                        <span className="underline">Reanexar</span>
                      </button>
                    ) : (
                      <img
                        src={urlDa(r)}
                        alt={`referência ${i + 1}`}
                        onError={() => setSumidas((s) => new Set(s).add(r))}
                        className="size-[52px] rounded-[9px] border border-line-strong object-cover"
                      />
                    )}
                    {!sumidas.has(r) && (
                      <button
                        onClick={() => setPintando(r)}
                        title="Marcar onde editar: máscara, círculos ou pintura (Qwen-Image 2.1)"
                        className="absolute -bottom-1.5 -left-1.5 grid size-4 place-items-center rounded-full border border-line-strong bg-raised text-muted hover:text-fg"
                      >
                        <Edit className="size-2.5" />
                      </button>
                    )}
                    <button
                      onClick={() => setRefs((atual) => atual.filter((x) => x !== r))}
                      title="Tirar da edição"
                      className="absolute -right-1.5 -top-1.5 grid size-4 place-items-center rounded-full border border-line-strong bg-raised text-muted hover:text-fg"
                    >
                      <X className="size-2.5" />
                    </button>
                  </div>
                ))}
                <button
                  onClick={() => arquivo.current?.click()}
                  title="Anexar uma imagem para editar (modelos que editam, como o Qwen-Image 2.1)"
                  className="grid size-[52px] place-items-center rounded-[9px] border border-dashed border-line-strong text-faint hover:border-focus hover:text-fg"
                >
                  <Plus className="size-4" />
                </button>
              </div>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                {refs.length > 0 && (
                  <span className={`text-[11.5px] ${naoEditam.length || refs.length > MAX_REFS || refs.some((r) => sumidas.has(r)) ? "text-warn" : "text-accent-text"}`}>
                    {refs.some((r) => sumidas.has(r))
                      ? "Imagem de referência não encontrada no lugar de antes (movida ou apagada): reanexe ou tire da edição."
                      : naoEditam.length
                      ? `${naoEditam.join(", ")} não edita imagem — escolha um modelo que edita (ex.: Qwen-Image 2.1).`
                      : refs.length > MAX_REFS
                      ? `${refs.length} imagens: o máximo são ${MAX_REFS}, máscaras incluídas.`
                      : `Editando ${refs.length} ${refs.length === 1 ? "imagem" : "imagens"} · o lápis marca onde mudar`}
                  </span>
                )}
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      gerar();
                    }
                  }}
                  rows={2}
                  placeholder={
                    refs.length
                      ? "change the sky to a sunset, keep everything else the same"
                      : "a red fox in the snow, cinematic lighting — em inglês funciona melhor"
                  }
                  className={campoPrompt}
                />
                {(negAberto || !!o.negative) && (
                  <input
                    autoFocus={negAberto && !o.negative}
                    value={o.negative}
                    onChange={(e) => set("negative", e.target.value)}
                    placeholder="Negativo: o que evitar na imagem"
                    className="w-full border-t border-line bg-transparent pt-1.5 text-[13px] text-fg-2 placeholder:text-faint focus:outline-none"
                  />
                )}
              </div>
            </div>
            <input
              ref={arquivo}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              hidden
              onChange={(e) => {
                anexar(e.target.files);
                e.target.value = "";
              }}
            />
            <div className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-line pt-2.5 text-xs">
              <button
                onClick={melhorar}
                disabled={!prompt.trim() || !llm.model || melhorando}
                title={llm.model ? `Reescrever o prompt com ${llm.model} (troque em Parâmetros)` : "Escolha em Parâmetros o modelo que reescreve"}
                className="inline-flex items-center gap-1.5 rounded-[8px] bg-raised px-2.5 py-1 text-muted hover:text-fg disabled:opacity-40"
              >
                <Refresh className={`size-3.5 ${melhorando ? "animate-spin" : ""}`} />
                Melhorar prompt
              </button>
              <button
                onClick={() => {
                  if (negAberto && o.negative) set("negative", "");
                  setNegAberto((v) => !v);
                }}
                title={o.negative ? "Tirar o negativo" : "O que evitar na imagem"}
                className={`rounded-[8px] px-1.5 py-1 ${negAberto || o.negative ? "text-accent-text" : "text-faint hover:text-fg"}`}
              >
                {negAberto || o.negative ? "− Negativo" : "+ Negativo"}
              </button>
              <button onClick={() => setAmpliarPc(true)} title="Ampliar a resolução de uma imagem do PC (ESRGAN ou Lanczos)"
                      className="rounded-[8px] px-1.5 py-1 text-faint hover:text-fg">
                Ampliar do PC
              </button>
              <div className="ml-auto flex min-w-0 items-center gap-3">
                <span className="truncate font-mono text-[11.5px] text-muted" title="Como as variações se dividem entre os modelos">
                  {models.length > 1
                    ? `${count} = ${[...divisao].map(([m, n]) => `${n} ${st.image_models.find((x) => x.path === m)?.name ?? m.split(/[\\/]/).pop()}`).join(" + ")}`
                    : `${count} × ${o.width}×${o.height}`}
                </span>
                <button
                  onClick={() => gerar()}
                  disabled={!prompt.trim() || !models.length || semRuntime}
                  title={!models.length ? "Escolha um modelo em Parâmetros" : st.image_busy || ocupado ? "Entra na fila: gera quando o lote atual terminar" : "Gerar (Enter)"}
                  className="inline-flex shrink-0 items-center gap-2 rounded-[10px] bg-accent px-3.5 py-2 text-[13px] font-semibold text-accent-fg hover:brightness-110 disabled:bg-raised disabled:text-faint disabled:hover:brightness-100"
                >
                  Gerar {count}
                  <span className="font-mono text-[10.5px] font-medium opacity-65">Enter</span>
                </button>
              </div>
            </div>
          </div>
          <p className="mt-1.5 text-center text-[11px] text-faint">
            O sd.cpp gera uma imagem por vez e libera a memória no fim — um lote é uma fila.
          </p>
          </>
          )}
        </div>
      </div>
      </div>
      {abrirAjustes && (
            <Ajustes
              st={st}
              o={o}
              set={set}
              models={models}
              onModels={setModels}
              divisao={divisao}
              seedMode={seedMode}
              onSeedMode={setSeedMode}
              count={count}
              onCount={setCount}
              llm={llm}
              onLlm={setLlm}
              onError={mostrarErro}
              onFechar={() => setAbrirAjustes(false)}
            />
          )}

      </div>
    </>
  );
}

// ---------------------------------------------------------------- ajustes

function Ajustes(props: {
  st: LocalState;
  o: ImageOpts;
  set: <K extends keyof ImageOpts>(k: K, v: ImageOpts[K]) => void;
  models: string[];
  onModels: (m: string[]) => void;
  divisao: Map<string, number>;
  seedMode: SeedMode;
  onSeedMode: (s: SeedMode) => void;
  count: number;
  onCount: (n: number) => void;
  llm: { provider: string; model: string };
  onLlm: (l: { provider: string; model: string }) => void;
  onError: (e: string) => void;
  onFechar: () => void;
}) {
  const { o, set, st } = props;
  const [limpando, setLimpando] = useState("");

  function alternar(path: string) {
    const entra = !props.models.includes(path);
    props.onModels(entra ? [...props.models, path] : props.models.filter((p) => p !== path));
    // Os ajustes do modelo (IA local › Modelos) viram os do composer: senão os globais — 512², CFG 7 —
    // iam por cima e o Qwen-Image saía com o CFG do SD 1.5.
    const p = entra && st.image_models.find((m) => m.path === path)?.params;
    if (p) for (const k of ["steps", "cfg", "width", "height", "sampler"] as const) set(k, p[k] as never);
  }

  async function esvaziar() {
    try {
      const r = await api.post<{ apagados: number }>("/imagens/descartadas/limpar");
      setLimpando(`${r.apagados} arquivo(s) apagado(s).`);
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function salvarPrazo(dias: number) {
    set("descarte_dias", dias);
    try {
      await api.put("/local/image/defaults", { ...o, descarte_dias: dias });
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  const [salvo, setSalvo] = useState(false);
  async function salvarPadrao() {
    try {
      await api.put("/local/image/defaults", { ...o, model: props.models[0] ?? o.model });
      setSalvo(true);
      setTimeout(() => setSalvo(false), 1800);
    } catch (e: any) {
      props.onError(e.message);
    }
  }
  // Formato: lado menor 512 · 768 · 1024 · 1536, no múltiplo de 64 que o sd.cpp pede.
  const snap = (v: number) => Math.max(64, Math.round(v / 64) * 64);
  const tamanhoPara = (f: string, q: string): [number, number] => {
    const [ra, rb] = f.split(":").map(Number), lado = Number(q), r = ra / rb;
    return r >= 1 ? [snap(lado * r), lado] : [lado, snap(lado / r)];
  };
  const quals = ["512", "768", "1024", "1536"];
  let prop: string | null = null, qual: string | null = null;
  for (const f of FORMAS_IMAGEM) for (const q of quals) {
    const [w, h] = tamanhoPara(f.id, q);
    if (w === o.width && h === o.height) { prop = f.id; qual = q; }
  }
  if (!prop && o.width && o.height) {
    const r = o.width / o.height;
    const perto = FORMAS_IMAGEM.find((f) => { const [a, b] = f.id.split(":").map(Number); return Math.abs(a / b - r) / (a / b) <= 0.03; });
    prop = perto?.id ?? null;
  }
  // o tamanho nativo dos modelos marcados diz até onde a resolução vai bem
  const nativo = Math.max(0, ...st.image_models.filter((m) => props.models.includes(m.path)).map((m) => Math.min(m.params?.width ?? 0, m.params?.height ?? 0)));
  return (
    <aside className="flex w-[300px] shrink-0 flex-col overflow-hidden border-l border-line bg-side text-xs">
      <div className="flex items-center gap-2 px-4 pt-4 pb-1.5">
        <span className="text-[13px] font-semibold text-fg">Parâmetros</span>
        <span className="ml-auto" />
        <button onClick={salvarPadrao} title="Estes ajustes viram o padrão da aba e da ferramenta de imagem do agente"
                className="text-[11.5px] text-faint hover:text-fg">
          {salvo ? "Salvo" : "Salvar como padrão"}
        </button>
        <button onClick={props.onFechar} className="rounded-[7px] p-1 text-faint hover:bg-raised hover:text-fg" aria-label="Esconder os parâmetros">
          <X className="size-3.5" />
        </button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-[18px] overflow-y-auto px-4 pt-1.5 pb-4">
        <Secao titulo="Modelos" dica="divide as variações">
          <div className="flex flex-col gap-0.5 rounded-[10px] border border-line bg-surface p-1.5">
            {!st.image_models.length && <span className="px-1.5 py-1 text-faint">Nenhum modelo de imagem nas pastas.</span>}
            {st.image_models.map((m) => {
              const ativo = props.models.includes(m.path);
              const pr = m.params;
              return (
                <label key={m.path} title={m.path}
                       className={`flex cursor-pointer items-center gap-2.5 rounded-[7px] px-1.5 py-[5px] ${ativo ? "bg-raised" : "hover:bg-raised/60"}`}>
                  <input type="checkbox" checked={ativo} onChange={() => alternar(m.path)} className="sr-only" />
                  <span className={`grid size-3.5 shrink-0 place-items-center rounded-[4px] border-[1.5px] ${ativo ? "border-accent bg-accent text-accent-fg" : "border-line-strong"}`}>
                    {ativo && <Check className="size-2.5" />}
                  </span>
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className={`truncate text-[12.5px] ${ativo ? "text-fg" : "text-muted"}`}>{m.name}</span>
                    {pr && (
                      <span className="truncate text-[10.5px] text-faint">
                        {m.req?.edita ? "edita · " : ""}{pr.steps} passos · CFG {String(pr.cfg).replace(".", ",")}
                      </span>
                    )}
                  </span>
                  {ativo && <span className="shrink-0 font-mono text-xs font-medium text-accent-text">{props.divisao.get(m.path) ?? 0}×</span>}
                </label>
              );
            })}
          </div>
        </Secao>

        <Secao titulo="Formato">
          <SeletorFormato
            w={o.width}
            h={o.height}
            mult={64}
            formas={FORMAS_IMAGEM}
            quals={quals.map((q) => {
              const alem = !!nativo && Number(q) > nativo * 1.5;
              const t = tamanhoPara(prop ?? "1:1", q).join(" × ");
              return { id: q, apagada: alem, titulo: alem ? `${t} — bem acima do tamanho nativo dos modelos marcados (${nativo}): pede muito mais memória e pode repetir elementos` : t };
            })}
            tamanhoPara={tamanhoPara}
            prop={prop}
            qual={qual}
            dicaQuals="Lado menor da imagem, no múltiplo de 64 que o sd.cpp pede."
            onTamanho={(w, h) => { set("width", w); set("height", h); }}
          />
        </Secao>

        <Secao titulo="Variações">
          <Stepper valor={props.count} min={1} max={50} onValor={props.onCount} />
        </Secao>

        <div className="flex flex-col gap-2.5 border-t border-line pt-3.5">
          <span className="font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">Avançado</span>
          <div className="grid grid-cols-2 gap-2">
            <Caixa rotulo="Passos"><input type="number" value={o.steps} onChange={(e) => set("steps", Number(e.target.value))} className={numeroCaixa} /></Caixa>
            <Caixa rotulo="CFG"><input type="number" step={0.5} value={o.cfg} onChange={(e) => set("cfg", Number(e.target.value))} className={numeroCaixa} /></Caixa>
            <Caixa rotulo="Amostrador">
              <select value={o.sampler} onChange={(e) => set("sampler", e.target.value)} className={`${numeroCaixa} -ml-1 cursor-pointer`}>
                {SAMPLERS.map((sm) => <option key={sm}>{sm}</option>)}
              </select>
            </Caixa>
            <Caixa rotulo={props.seedMode === "aleatoria" ? "Semente (sorteada)" : "Semente base"}>
              <input type="number" value={o.seed} disabled={props.seedMode === "aleatoria"} title="0 = sorteia uma e anota"
                     onChange={(e) => set("seed", Number(e.target.value))} className={`${numeroCaixa} disabled:text-faint`} />
            </Caixa>
          </div>
          <div className="flex rounded-[8px] border border-line bg-surface p-0.5 text-xs">
            {SEEDS.map((sd) => (
              <button key={sd.id} onClick={() => props.onSeedMode(sd.id)} title={sd.hint}
                      className={`flex-1 rounded-[6px] py-1 ${props.seedMode === sd.id ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>
                {sd.label}
              </button>
            ))}
          </div>

          <Secao titulo="Melhorar prompt">
            <div className="flex justify-center [&>div]:ml-0" title="O que reescreve o prompt: um LLM rápido basta. Não é o que gera a imagem.">
              <ModelPicker provider={props.llm.provider} model={props.llm.model} onChange={(provider, model) => props.onLlm({ provider, model })} />
            </div>
          </Secao>
        </div>

      </div>
      <div className="flex shrink-0 flex-col gap-2 border-t border-line px-4 py-3 text-[11.5px] text-muted">
          <div className="flex items-center gap-1.5">
            <span className="shrink-0">Salvar em</span>
            <input value={o.out_dir || st.image_dir} onChange={(e) => set("out_dir", e.target.value)} spellCheck={false}
                   title={o.out_dir || st.image_dir}
                   className="min-w-0 flex-1 truncate bg-transparent font-mono text-fg-2 outline-none focus:text-fg" />
          <button title="Escolher pasta" className="shrink-0 rounded-[6px] p-1 text-faint hover:bg-raised hover:text-fg"
                  onClick={async () => {
                    const escolhida = window.forja ? await window.forja.pickFolder(o.out_dir || st.image_dir) : "";
                    if (escolhida) set("out_dir", escolhida);
                  }}>
            <FolderOpen className="size-3.5" />
          </button>
          </div>
          <label className="flex items-center gap-1.5" title="0 = guardar para sempre. Vale para imagens e vídeos descartados.">
            Descartadas somem em
<label data-arrasta data-passo={1} className="rounded-[8px] border border-line bg-surface px-2 py-0.5 focus-within:border-focus">
              <input type="number" min={0} max={365} step={1} value={o.descarte_dias} aria-label="Dias até apagar as descartadas" onChange={(e) => salvarPrazo(Number(e.target.value))}
                     className={`${numeroCaixa} w-8 text-center`} />
            </label>
            {o.descarte_dias === 1 ? "dia" : "dias"}
          </label>
          <div className="flex items-center gap-2">
            <button onClick={esvaziar} className="inline-flex items-center gap-1 text-faint hover:text-err">
              <Trash className="size-3" /> Esvaziar descartadas agora
            </button>
            {limpando && <span className="text-faint">{limpando}</span>}
          </div>
      </div>
    </aside>
  );
}

// Desenhos do formato (mesmo traço do Vídeo), com as proporções que a Imagem sempre teve.
const FORMAS_IMAGEM: Forma[] = [
  { id: "1:1", w: 17, h: 17 }, { id: "3:2", w: 24, h: 16 }, { id: "2:3", w: 14, h: 20 }, { id: "16:9", w: 26, h: 15 },
];

/** Uma seção do painel Parâmetros: rótulo mono em caixa-alta e o conteúdo embaixo. */
export function Secao(props: { titulo: string; dica?: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">{props.titulo}</span>
        {props.dica && <span className="ml-auto text-[11px] text-faint">{props.dica}</span>}
        {props.extra && <span className="ml-auto">{props.extra}</span>}
      </div>
      {props.children}
    </div>
  );
}

/** Campo numérico em caixa (rótulo pequeno em cima, valor mono embaixo), a grade do design. */
export function Caixa(props: { rotulo: string; passo?: number; children: React.ReactNode }) {
  // data-arrasta: arrastar para os lados na caixa muda o número (arrastaNumero.ts)
  return (
    <label data-arrasta data-passo={props.passo} className="flex min-w-0 flex-col gap-0.5 rounded-[8px] border border-line bg-surface px-2.5 py-1.5 focus-within:border-focus">
      <span className="text-[10.5px] text-faint">{props.rotulo}</span>
      {props.children}
    </label>
  );
}
export const numeroCaixa =
  "w-full min-w-0 bg-transparent font-mono text-[12.5px] font-medium text-fg outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none";

/** − valor + (Variações). */
export function Stepper(props: { valor: number; min: number; max: number; onValor: (n: number) => void }) {
  const muda = (n: number) => props.onValor(Math.max(props.min, Math.min(props.max, n || props.min)));
  return (
    <div className="flex items-center rounded-[8px] border border-line bg-surface">
      <button onClick={() => muda(props.valor - 1)} disabled={props.valor <= props.min} aria-label="Menos"
              className="w-8 py-1 text-muted hover:text-fg disabled:opacity-30">−</button>
      <input type="number" value={props.valor} onChange={(e) => muda(Number(e.target.value))}
             className={`${numeroCaixa} flex-1 py-1 text-center text-[13px]`} />
      <button onClick={() => muda(props.valor + 1)} disabled={props.valor >= props.max} aria-label="Mais"
              className="w-8 py-1 text-muted hover:text-fg disabled:opacity-30">+</button>
    </div>
  );
}

/** Faixa do topo da galeria (Imagem e Vídeo): contagem, estado da GPU no meio e o botão do painel Parâmetros. */
export function BarraTopo(props: {
  contagem: number;
  unidade: [string, string];
  status: { cor: string; texto: string; meta: string } | null;
  parametros: boolean;
  onParametros: () => void;
  esquerda?: React.ReactNode;
}) {
  return (
    <div className="flex shrink-0 items-center gap-2.5 border-b border-line px-4 py-2">
      {props.esquerda ?? (
        <span className="rounded-[5px] border border-line px-1.5 py-px font-mono text-[11px] text-faint">
          {props.contagem} {props.contagem === 1 ? props.unidade[0] : props.unidade[1]}
        </span>
      )}
      <div className="flex min-w-0 flex-1 justify-center">
        {props.status && (
          <span className="flex min-w-0 items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs text-muted">
            <span className={`size-1.5 shrink-0 rounded-full ${props.status.cor}`} />
            <span className="truncate">{props.status.texto}</span>
            {props.status.meta && <span className="shrink-0 font-mono text-faint">{props.status.meta}</span>}
          </span>
        )}
      </div>
      <button onClick={props.onParametros} title={props.parametros ? "Esconder os parâmetros" : "Mostrar os parâmetros"} aria-pressed={props.parametros}
              className={`grid size-[30px] place-items-center rounded-[7px] ${props.parametros ? "bg-raised text-accent-text" : "text-muted hover:bg-raised hover:text-fg"}`}>
        <PanelRight />
      </button>
    </div>
  );
}

/** O prompt que descreve a imagem de um lote (o mesmo que lotes.prompt_da_imagem): o da geração; numa ampliação, o do
 *  redesenho dela; numa ampliação de arquivo do PC o pedido é só o nome do arquivo, e aí fica vazio. */
function promptDaImagem(pedido: Message): string {
  const amp = (pedido.meta as { ampliacao?: { prompt?: string } } | null)?.ampliacao;
  if (!amp) return pedido.content;
  return amp.prompt || (/\.(png|jpe?g|webp)$/i.test(pedido.content) ? "" : pedido.content);
}

// ---------------------------------------------------------------- um lote

function Lote(props: {
  pedido: Message;
  resposta: Message;
  onZoom: (src: string) => void;
  noSite: (img: LoteImagem) => LoteImagem;
  foraDoCodigo: Set<string>;
  versoes: (slot: string) => { img: LoteImagem; lote: Message }[];
  onAbrirSlot: (slot: string) => boolean; // true = abriu o modal de variações (não amplia)
  onRegerar: (message_id: number, path: string) => void;
  onEditar: (path: string) => void;
  onError: (e: string) => void;
  onMudou: () => void;
  onReaproveitar: () => void;
  onSemente: (s: number) => void;
  numero: number;   // "Lote N" no cabeçalho
  ultimo: boolean;  // o mais recente leva o destaque
}) {
  const meta = props.resposta.meta as LoteMeta;
  const imagens = meta.images;
  const viva = props.resposta.status === "running";
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [salvando, setSalvando] = useState(false);
  const [vram, setVram] = useState("");  // Continuar esbarrou na VRAM ocupada: a mensagem do 409
  const [ampliando, setAmpliando] = useState<{ img: LoteImagem; lote: number } | null>(null);
  const faltam = imagens.filter((i) => A_REFAZER.includes(i.status)).length;
  // Lote dos slots do site: o card mostra a versão em uso e as versões se escolhem no modal, então
  // manter/descartar e reaproveitar (que joga no campo de prompt, que aqui não existe) ficam de fora.
  const deSlots = imagens.every((i) => i.destino || i.slot);

  // Enquanto o lote roda os caminhos mudam de status; a seleção acompanha o que já ficou pronto.
  // Slot do site já vem marcado: descartar tira a imagem do caminho que o código aponta.
  const marcadaDePadrao = (i: LoteImagem) => i.status === "mantida" || (!!i.destino && i.status === "pronta");
  useEffect(() => {
    setSel(new Set(imagens.filter(marcadaDePadrao).map((i) => i.path)));
  }, [props.resposta.id, imagens.filter(marcadaDePadrao).length]);

  const decididas = imagens.filter((i) => i.status === "mantida" || i.status === "descartada").length;
  const aprovaveis = imagens.filter((i) => ["pronta", "mantida", "descartada"].includes(i.status));
  const prontas = imagens.filter((i) => i.status === "pronta" || i.status === "mantida").length;

  async function decidir() {
    setSalvando(true);
    try {
      await api.post(`/imagens/${props.resposta.id}/decidir`, { keep: [...sel] });
      props.onMudou();
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setSalvando(false);
    }
  }

  async function cancelar() {
    try {
      await api.post(`/imagens/${props.resposta.id}/cancelar`, {});
      props.onMudou();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function continuar(confirm = false) {
    try {
      await api.post(`/imagens/${props.resposta.id}/continuar`, { confirm });
      setVram("");
      props.onMudou();
    } catch (e: any) {
      if (e.status === 409) setVram(e.message || "x");
      else props.onError(e.message);
    }
  }

  async function mostrarNaPasta(caminho: string) {
    try {
      await api.post("/open", { path: caminho, mode: "reveal" });
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  return (
    <section className="mb-8">
      {ampliando && (
        <Modal label="Ampliar imagem" onClose={() => setAmpliando(null)} className="w-full max-w-sm rounded-xl border border-line bg-surface p-4">
          <p className="mb-3 text-sm text-fg">Ampliar imagem</p>
          <PainelAmpliar
            imagem
            prompt={promptDaImagem(props.pedido)}
            w={ampliando.img.width ?? meta.opts.width ?? 0}
            h={ampliando.img.height ?? meta.opts.height ?? 0}
            onError={props.onError}
            enviar={async (c) => {
              await api.post(`/imagens/${ampliando.lote}/ampliar`, { path: ampliando.img.path, ...c });
              setAmpliando(null);
              props.onMudou();
            }}
          />
        </Modal>
      )}
      <div className="mb-2.5 flex items-center gap-2.5">
        <span className={`shrink-0 rounded-[5px] px-[7px] py-0.5 font-mono text-[11px] font-medium ${props.ultimo ? "bg-accent text-accent-fg" : "bg-raised text-muted"}`}>
          Lote {props.numero}
        </span>
        <p className="min-w-0 flex-1 truncate text-[13px] text-fg-2" title={props.pedido.content}>{props.pedido.content}</p>
        <span className="shrink-0 font-mono text-[11px] text-faint">
          {viva ? `gerando ${prontas + 1} de ${imagens.length} · `
            : props.resposta.status === "interrompido" ? `interrompido: ${imagens.length - faltam} de ${imagens.length} · `
            : deSlots ? `${imagens.length} do site · ` : ""}
          {[meta.opts.width && `${meta.opts.width}×${meta.opts.height}`, meta.opts.steps !== undefined && `${meta.opts.steps} passos`,
            meta.opts.cfg !== undefined && `CFG ${String(meta.opts.cfg).replace(".", ",")}`, meta.opts.sampler].filter(Boolean).join(" · ")}
          {" · "}
          <button
            onClick={() => props.onSemente(imagens[0].seed)}
            title={`${rotuloSementes(imagens.map((i) => i.seed), meta.seed_mode)}\nClique para usar ${imagens[0].seed} no próximo lote`}
            className="hover:text-fg"
          >
            {imagens[0].seed}{imagens.length > 1 ? "+" : ""}
          </button>
        </span>
        {viva ? (
          <button onClick={cancelar} className="shrink-0 rounded-[7px] border border-line px-2 py-0.5 text-xs text-muted hover:bg-raised hover:text-fg">
            Cancelar
          </button>
        ) : !deSlots && (
          <button onClick={props.onReaproveitar} title="Traz prompt e ajustes deste lote para o campo"
                  className="shrink-0 rounded-[7px] border border-line px-2 py-0.5 text-xs text-muted hover:bg-raised hover:text-fg">
            Reaproveitar
          </button>
        )}
      </div>
      {(meta.opts.ampliacao || !!(props.pedido.meta as PedidoMeta | null)?.refs?.length) && (
        <div className="-mt-1 mb-2.5 flex flex-wrap items-center gap-1.5 text-[11px] text-faint">
          {meta.opts.ampliacao && <Chip>{`ampliada ${meta.opts.ampliacao.fator}×`}</Chip>}
          {meta.opts.ampliacao?.forca != null && ( // redesenho: a força usada; o prompt no hover (pode ser longo)
            <span className="rounded-[5px] bg-raised px-2 py-0.5 font-mono" title={`Prompt do redesenho: ${meta.opts.ampliacao.prompt || "(vazio)"}`}>
              {`força ${meta.opts.ampliacao.forca.toFixed(2).replace(".", ",")}`}
            </span>
          )}
          {!!(props.pedido.meta as PedidoMeta | null)?.refs?.length && (
            <Chip>edição de {(props.pedido.meta as PedidoMeta).refs!.length} imagem(ns)</Chip>
          )}
        </div>
      )}

      <Mosaico proporcoes={imagens.map((img) => proporcaoDe(deSlots ? props.noSite(img) : img, meta.opts) ?? 1)}>
        {imagens.map((img) => {
          const chave = img.destino ?? img.slot;
          const versoes = chave ? props.versoes(chave) : [];
          const mostrada = deSlots ? props.noSite(img) : img;
          const loteDaMostrada = versoes.find((v) => v.img === mostrada)?.lote.id ?? props.resposta.id;
          const gerandoVersao = versoes.some((v) => v.lote.status === "running" && v.lote.id !== props.resposta.id
            && ["pendente", "gerando"].includes(v.img.status));
          return (
          <Cartao
            key={img.path}
            img={mostrada}
            proporcao={proporcaoDe(mostrada, meta.opts)}
            pilha={deSlots ? versoes.length : 1}
            semMarcar={deSlots}
            extra={gerandoVersao ? "gerando versão…"
              : chave && props.foraDoCodigo.has(chave) ? "fora do código"
              : versoes.length > 1 ? `${versoes.length} versões` : undefined}
            marcada={sel.has(img.path)}
            onMarcar={() =>
              setSel((s) => {
                const novo = new Set(s);
                if (novo.has(img.path)) novo.delete(img.path);
                else novo.add(img.path);
                return novo;
              })
            }
            onZoom={() => {
              if (!chave || !props.onAbrirSlot(chave)) props.onZoom(srcDe(mostrada));
            }}
            onRegerar={chave ? () => props.onRegerar(loteDaMostrada, mostrada.path) : undefined}
            onSemente={() => props.onSemente(mostrada.seed)}
            onPasta={() => mostrarNaPasta(mostrada.path)}
            onEditar={() => props.onEditar(mostrada.path)}
            onAmpliar={() => setAmpliando({ img: mostrada, lote: loteDaMostrada })}
            // edição: a imagem editada por trás; ampliação: a original, enquanto amplia
            origem={(props.pedido.meta as PedidoMeta | null)?.refs?.[0] ?? meta.opts.ampliacao?.origem}
          />
          );
        })}
      </Mosaico>

      <div className="mt-2.5 flex flex-wrap items-center gap-2 text-xs">
        {!viva && (
          aprovaveis.length > 0 && !deSlots && (
            <div className="sticky bottom-3 z-10 flex w-full flex-wrap items-center gap-2 rounded-[14px] border border-line-strong bg-surface px-3.5 py-2.5 shadow-float">
              <span className="text-[13px] font-medium text-fg">{sel.size} marcada{sel.size === 1 ? "" : "s"}</span>
              <span className="text-faint">as outras vão para <span className="font-mono">descartadas/</span></span>
              <span className="flex-1" />
              {sel.size > 0 && (
                <button className={btn} onClick={() => setSel(new Set())}>
                  Limpar seleção
                </button>
              )}
              <button className={btn} onClick={() => setSel(new Set(aprovaveis.map((i) => i.path)))}>
                Marcar todas
              </button>
              <button
                className={btnPrimary}
                disabled={salvando}
                onClick={decidir}
                title="As não marcadas vão para a subpasta descartadas/"
              >
                <Check className="mr-1 inline size-3.5" />
                {sel.size
                  ? `Manter ${sel.size} · descartar ${aprovaveis.length - sel.size}`
                  : `Descartar todas (${aprovaveis.length})`}
              </button>
            </div>
          )
        )}
        {!viva && faltam > 0 && (
          <button className={btn} onClick={() => continuar()}
                  title="Gera só as que faltaram, com a mesma semente e os mesmos ajustes (a imagem que parou no meio recomeça do zero)">
            <ArrowUp className="mr-1 inline size-3.5 rotate-90" />
            Continuar ({faltam})
          </button>
        )}
        {decididas > 0 && (
          <span className="text-faint">
            {imagens.filter((i) => i.status === "mantida").length} mantida(s) ·{" "}
            {imagens.filter((i) => i.status === "descartada").length} em descartadas/
          </span>
        )}
      </div>
      {vram && (
        <CartaoEstado tom="aviso" className="mt-2"
          titulo={vram.startsWith("Outro programa") ? vram : "Tem um modelo carregado na VRAM, e o sd.cpp precisa dessa memória."}
          acoes={<>
            <button className={botaoEstadoPrimario} onClick={() => continuar(true)}>
              {vram.startsWith("Outro programa") ? "Continuar mesmo assim" : "Descarregar e continuar"}
            </button>
            <button className={botaoEstado} onClick={() => setVram("")}>Cancelar</button>
          </>} />
      )}
    </section>
  );
}

/** Nível sobe com o passo da amostragem; antes do 1º passo (carregando pesos) fica uma lâmina no fundo. */
/** "32 s/passo" quando lento, "2,5 passos/s" quando rápido — como o sd.cpp decide a unidade. */
export const velocidade = (s: number, unidade = "passo") =>
  s >= 1 ? `${Math.round(s)} s/${unidade}` : `${(1 / s).toFixed(1).replace(".", ",")} ${unidade}s/s`;

export const duracao = (s: number) =>
  s < 60 ? `~${Math.max(1, Math.round(s))} s` : `~${Math.floor(s / 60)} min${s % 60 >= 30 && s < 600 ? " 30 s" : ""}`;

export function Liquido({ fracao, sPasso, restante, fase }: { fracao: number; sPasso?: number; restante?: number; fase?: string }) {
  const pct = Math.round(Math.min(1, Math.max(0, fracao)) * 100);
  return (
    <>
      <div
        className="absolute inset-x-0 bottom-0 border-t-[1.5px] border-accent/75 bg-gradient-to-t from-accent/25 to-accent/5 transition-[height] duration-1000 ease-out"
        style={{ height: `${Math.max(pct, 3)}%` }}
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      />
      <div className="relative flex flex-col items-center gap-0.5 text-center tabular-nums">
        <span className="font-mono text-lg font-medium text-accent-text">{pct}%</span>
        {(!!sPasso || !!restante || !!fase) && (
          <span className="font-mono text-[10.5px] text-fg-2">
            {[fase, sPasso ? velocidade(sPasso) : "", restante ? `${duracao(restante)} restantes` : ""].filter(Boolean).join(" · ")}
          </span>
        )}
      </div>
    </>
  );
}

/** Fundo listrado (o placeholder do design): imagem que ainda não começou ou sem a 1ª amostra. */
export const listras = "bg-[repeating-linear-gradient(135deg,var(--color-raised)_0_7px,var(--color-surface)_7px_14px)]";

/** Imagem de fundo do card que troca só quando a próxima já carregou: o sd-cli regrava a prévia a cada
 *  passo, e pegar o arquivo no meio da gravação mostraria uma imagem quebrada. */
export function Fundo(props: { src?: string; inicial?: string }) {
  const [visivel, setVisivel] = useState(props.inicial);
  useEffect(() => {
    if (!props.src) return;
    const i = new window.Image();
    i.onload = () => setVisivel(props.src);
    i.src = props.src;
    return () => {
      i.onload = null;
    };
  }, [props.src]);
  if (!visivel) return null;
  return <img src={visivel} alt="" onError={() => setVisivel(undefined)} className={`absolute inset-0 size-full object-cover ${visivel === props.inicial ? "opacity-40" : "opacity-80"}`} />;
}

/** "semente 4 · fixa", "sementes 4–7 · incremental", "sementes 12, 98, 551 · aleatória": o número é o
 *  que permite repetir a imagem, então ele aparece, e não só o modo. */
export function rotuloSementes(sementes: number[], modo: SeedMode): string {
  const unicas = [...new Set(sementes)];
  const ordenadas = [...unicas].sort((a, b) => a - b);
  const seguidas = ordenadas.every((s, i) => !i || s === ordenadas[i - 1] + 1);
  const numeros = unicas.length === 1 ? `semente ${unicas[0]}`
    : seguidas ? `sementes ${ordenadas[0]}–${ordenadas[ordenadas.length - 1]}`
    : `sementes ${unicas.slice(0, 3).join(", ")}${unicas.length > 3 ? ` +${unicas.length - 3}` : ""}`;
  return `${numeros} · ${modo === "aleatoria" ? "aleatória" : modo}`;
}

export function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-[5px] bg-raised px-2 py-0.5 font-mono text-[11px] text-fg-2">{children}</span>;
}

/** A bolinha (24 px, onde fica a de marcar) com a % dentro; o anel contorna por fora. */
export function AnelProgresso({ pct, lado = "dir" }: { pct: number; lado?: "esq" | "dir" }) {
  return (
    <svg
      viewBox="0 0 30 30"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      className={`absolute top-[5px] size-[30px] ${lado === "esq" ? "left-[5px]" : "right-[5px]"}`}
    >
      <circle cx="15" cy="15" r="12" className="fill-black/60" />
      <circle cx="15" cy="15" r="13.75" fill="none" strokeWidth="2.5" className="stroke-white/20" />
      <circle
        cx="15" cy="15" r="13.75" fill="none" strokeWidth="2.5" strokeLinecap="round" pathLength={100}
        strokeDasharray={`${pct} 100`} transform="rotate(-90 15 15)"
        className="stroke-sky-400 transition-[stroke-dasharray] duration-1000 ease-out"
      />
      <text x="15" y="15" textAnchor="middle" dominantBaseline="central" fontSize="10" fontWeight="600"
            className="fill-white tabular-nums">
        {pct}
      </text>
    </svg>
  );
}

function Cartao(props: {
  img: LoteImagem;
  marcada: boolean;
  onMarcar: () => void;
  onZoom: () => void;
  onSemente: () => void;
  onPasta: () => void;
  onEditar: () => void;
  onAmpliar: () => void;
  onRegerar?: () => void; // imagem de slot: variações com o mesmo prompt
  semMarcar?: boolean; // slot do site: não se marca para manter/descartar
  extra?: string; // "3 versões", "gerando versão…"
  origem?: string; // edição: a imagem que está sendo editada aparece por trás enquanto gera
  proporcao?: number; // largura/altura da imagem (1 = quadrada)
  pilha?: number; // versões do slot: mais de uma vira pilha de fotos por trás
}) {
  const { img } = props;
  const ar = { aspectRatio: String(props.proporcao ?? 1) };
  const temArquivo = ["pronta", "mantida", "descartada"].includes(img.status);
  // Com prévia, a imagem fica inteira à vista: o andamento vai num anel no lugar da bolinha de marcar
  // (que aparece ali quando ela fica pronta) e o número no rodapé. Sem prévia, o líquido por cima.
  const comPrevia = img.status === "gerando" && (!!img.preview || !!img.com_previa);
  const pct = Math.round(Math.min(1, Math.max(0, img.progress ?? 0)) * 100);

  return (
    <Pilha n={props.pilha ?? 1} largo={(props.proporcao ?? 1) > 1.3 && !!props.semMarcar}>
    <figure
      className={`group relative overflow-hidden rounded-[10px] border ${
        props.marcada ? "border-accent ring-[3px] ring-accent/15" : "border-line"
      } ${temArquivo ? "bg-raised" : listras}`}
    >
      {temArquivo ? (
        <img
          src={srcDe(img)}
          alt={`semente ${img.seed}`}
          onClick={props.onZoom}
          style={ar}
          className={`block w-full cursor-zoom-in object-cover ${
            img.status === "descartada" ? "opacity-40 grayscale" : ""
          }`}
        />
      ) : (
        <div style={ar} className="relative grid w-full place-items-center overflow-hidden">
          {/* A prévia do passo atual e, até ela chegar, a imagem em edição. */}
          {img.status !== "erro" && (
            <Fundo
              src={img.status === "gerando" && img.preview ? `${urlDa(img.preview)}&v=${img.progress ?? 0}` : undefined}
              inicial={props.origem && urlDa(props.origem)}
            />
          )}
          {img.status === "erro" ? (
            <span className="relative px-3 text-center text-[11px] text-err" title={img.error}>
              {img.error.split("\n")[0].slice(0, 90)}
            </span>
          ) : img.status === "gerando" && !comPrevia ? (
            <Liquido fracao={img.progress ?? 0} sPasso={img.s_passo} restante={img.restante} fase={img.fase} />
          ) : img.status === "gerando" ? null : (
            <span className={`relative text-[11.5px] ${CORES[img.status]}`}>
              {img.status === "pendente" ? "na fila" : img.fase ?? img.status}
            </span>
          )}
        </div>
      )}

      {comPrevia && <AnelProgresso pct={pct} />}
      {temArquivo && !props.semMarcar && (
        <button
          onClick={props.onMarcar}
          title={props.marcada ? "Desmarcar" : "Marcar para manter"}
          className={`absolute right-2 top-2 grid size-5 place-items-center rounded-full border-[1.5px] ${
            props.marcada ? "border-accent bg-accent text-accent-fg" : "border-white/35 bg-black/25 text-transparent hover:text-white"
          }`}
        >
          <Check className="size-3" />
        </button>
      )}
      {props.extra && (
        <span
          title={props.extra === "fora do código" ? "Nenhum arquivo do projeto aponta mais para esta imagem" : undefined}
          className={`absolute left-2 top-2 rounded-[5px] bg-black/60 px-1.5 py-px font-mono text-[10.5px] ${
            props.extra.startsWith("gerando") ? "animate-pulse text-accent-text"
            : props.extra === "fora do código" ? "text-warn" : "text-fg-2"}`}
        >
          {props.extra}
        </span>
      )}

      {/* Rodapé sobre a imagem: modelo · semente; no hover, as ações */}
      <figcaption className={`absolute inset-x-0 bottom-0 flex items-end gap-1.5 px-2 pt-6 pb-1.5 font-mono text-[10.5px] ${
        temArquivo ? "bg-gradient-to-t from-black/70 to-transparent text-white/75" : "text-faint"}`}>
        <button
          onClick={temArquivo ? props.onSemente : undefined}
          title={temArquivo ? `${img.model_name} · ${img.path}\nClique para usar a semente ${img.seed} no próximo lote` : `${img.model_name} · ${img.path}`}
          className={`flex min-w-0 flex-1 text-left ${temArquivo ? "hover:text-white" : "cursor-default"}`}
        >
          <span className="truncate">{img.nome ?? (img.model_name || "—")}</span>
          <span className="shrink-0">&nbsp;· {img.seed}</span>
        </button>
        {comPrevia && (
          // a % está na bolinha; aqui a velocidade e quanto falta (antes do 1º passo, carregando: "gerando")
          <span className="shrink-0 tabular-nums text-accent-text">
            {img.s_passo ? `${velocidade(img.s_passo)} · ${duracao(img.restante ?? 0)}` : "gerando"}
          </span>
        )}
        {temArquivo && (
          <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 [&>button]:rounded-[5px] [&>button]:p-1 [&>button:hover]:bg-white/15 [&>button:hover]:text-white">
            {props.onRegerar && (
              <button onClick={props.onRegerar} title="Regerar: novas variações com o mesmo prompt, para escolher qual fica no site">
                <Refresh className="size-3" />
              </button>
            )}
            <button onClick={props.onEditar} title="Editar esta imagem no próximo lote">
              <Edit className="size-3" />
            </button>
            <button onClick={props.onAmpliar} title="Ampliar a resolução (ESRGAN ou Lanczos)">
              <TelaCheia className="size-3" />
            </button>
            <button onClick={props.onPasta} title="Mostrar na pasta">
              <FolderOpen className="size-3" />
            </button>
          </span>
        )}
      </figcaption>
    </figure>
    </Pilha>
  );
}

/** Todas as versões de um slot: a que o site mostra e as regeradas. Escolher troca o arquivo do site. */
function Variacoes(props: {
  slot: string;
  itens: { img: LoteImagem; lote: Message }[];
  count: number;
  ocupado: boolean;
  onZoom: (src: string) => void;
  onEscolher: (path: string) => void;
  onRegerar: (prompt?: string) => void; // sem prompt = o mesmo da imagem do site
  onClose: () => void;
}) {
  const nome = props.itens[0]?.img.nome ?? props.slot.split(/[\\/]/).pop();
  // A do site primeiro; depois as mais novas (do último lote) no começo.
  const ordem = [...props.itens].reverse().sort((a, b) => Number(!!b.img.destino) - Number(!!a.img.destino));
  const original = ordem[0]?.img.prompt ?? "";  // o prompt da versão que o site mostra
  const daIa = props.itens[0]?.img.prompt ?? "";  // o 1º lote do slot: o prompt que a IA escreveu
  const [prompt, setPrompt] = useState(original);
  const editado = prompt.trim() !== original.trim();
  const rodando = [...new Set(props.itens.filter((v) => v.lote.status === "running").map((v) => v.lote.id))];
  return (
    <Modal onClose={props.onClose} label={`Variações de ${nome}`} className="flex max-h-[90vh] w-full max-w-5xl flex-col rounded-xl border border-line bg-surface p-4 text-xs">
      <div className="mb-3 flex items-center gap-2">
        <span className="font-mono text-sm text-fg">{nome}</span>
        <span className="text-faint">{props.itens.length} versões · a de borda verde é a que o site mostra</span>
        <button onClick={props.onClose} title="Fechar" className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg">
          <X className="size-3.5" />
        </button>
      </div>
      <label className="mb-3 block">
        <span className="mb-1 flex items-center gap-2 text-muted">
          Prompt
          {editado ? (
            <span className="text-amber-300">editado: as próximas variações usam este texto</span>
          ) : (
            <span className="text-faint">o da imagem do site; edite para regerar diferente</span>
          )}
          <span className="ml-auto flex gap-1.5">
            {/* iguais (o site ainda usa o texto da IA): um botão só basta */}
            {editado && original.trim() !== daIa.trim() && (
              <button onClick={() => setPrompt(original)} className={`${btn} py-0.5`} title={original}>
                <Undo className="mr-1 inline size-3" />
                O da imagem do site
              </button>
            )}
            {daIa && prompt.trim() !== daIa.trim() && (
              <button onClick={() => setPrompt(daIa)} className={`${btn} py-0.5`} title={daIa}>
                <Robo className="mr-1 inline size-3" />
                Prompt original da IA
              </button>
            )}
          </span>
        </span>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          spellCheck={false}
          className="w-full resize-y rounded-lg border border-line bg-bg px-2.5 py-2 text-[13px] leading-relaxed text-fg focus:border-focus focus:outline-none"
        />
      </label>
      <div className="grid min-h-0 grid-cols-2 items-start gap-3 overflow-y-auto p-2 md:grid-cols-3 xl:grid-cols-4">
        {ordem.map(({ img, lote }) => {
          const pronta = ["pronta", "mantida"].includes(img.status);
          const noSite = !!img.destino;
          return (
            <figure key={img.path} className={`overflow-hidden rounded-xl border ${noSite ? "border-emerald-500" : "border-line"} bg-raised`}>
              {pronta || img.status === "descartada" ? (
                <img
                  src={srcDe(img)}
                  alt={`${nome}, semente ${img.seed}`}
                  onClick={() => props.onZoom(srcDe(img))}
                  style={{ aspectRatio: String(proporcaoDe(img, lote.meta?.opts)) }}
                  className={`w-full cursor-zoom-in object-cover ${img.status === "descartada" ? "opacity-40 grayscale" : ""}`}
                />
              ) : (
                <div style={{ aspectRatio: String(proporcaoDe(img, lote.meta?.opts)) }}
                     className="relative grid w-full place-items-center overflow-hidden px-3 text-center text-faint" title={img.error || undefined}>
                  {/* A prévia do passo atual, como no card do lote (antes só o número aparecia aqui). */}
                  {img.status === "gerando" && img.preview && <Fundo src={`${urlDa(img.preview)}&v=${img.progress ?? 0}`} />}
                  <span className={`relative ${img.status === "gerando" && img.preview ? "rounded-full bg-black/60 px-2 py-0.5 text-fg" : ""}`}>
                    {img.status === "gerando" ? `gerando ${Math.round((img.progress ?? 0) * 100)}%`
                      : img.status === "erro" ? <span className="text-red-300">{img.error.split("\n")[0].slice(0, 90) || "erro"}</span>
                      : img.status}
                  </span>
                </div>
              )}
              <figcaption className="flex items-center gap-1.5 px-2 py-1.5">
                <span className="min-w-0 flex-1 truncate text-faint" title={`${img.model_name} · ${img.path}\n\n${img.prompt ?? ""}`}>
                  semente {img.seed}
                </span>
                {img.prompt && img.prompt.trim() !== original.trim() && (
                  <button
                    onClick={() => setPrompt(img.prompt!)}
                    title={`Feita com outro prompt:\n${img.prompt}\n\nClique para trazer este texto para o campo`}
                    className="shrink-0 text-amber-300/80 hover:text-amber-200"
                  >
                    outro prompt
                  </button>
                )}
                {noSite ? (
                  <span className="text-emerald-300">no site</span>
                ) : (
                  pronta && (
                    <button
                      onClick={() => props.onEscolher(img.path)}
                      disabled={lote.status === "running"}
                      title={lote.status === "running" ? "Espere o lote terminar" : "Trocar a imagem do site por esta"}
                      className="rounded-full border border-line px-2 py-0.5 text-fg hover:bg-raised disabled:opacity-40"
                    >
                      Usar no site
                    </button>
                  )
                )}
              </figcaption>
            </figure>
          );
        })}
      </div>
      <div className="mt-3 flex items-center gap-2">
        {rodando.length > 0 && (
          <button className={btn} onClick={() => rodando.forEach((id) => api.post(`/imagens/${id}/cancelar`, {}).catch(() => {}))}>
            <Square className="mr-1 inline size-3" />
            Parar
          </button>
        )}
        <button
          className={btn}
          onClick={() => props.onRegerar(editado ? prompt.trim() : undefined)}
          disabled={props.ocupado || !prompt.trim()}
        >
          <Refresh className="mr-1 inline size-3.5" />
          Regerar mais {props.count}{editado ? " com o prompt editado" : ""}
        </button>
        <span className="text-faint">
          {props.ocupado ? "Gerando…" : "Mesmo tamanho, sementes novas. A quantidade é a de “versões por vez”, embaixo; o site só muda quando você usar uma."}
        </span>
      </div>
    </Modal>
  );
}

type Origem = {
  message_id: number; // o pedido mais novo da IA (é por ele que a fila dos pendentes sai)
  workspace: string;
  projeto: string;
  chat: { id: number; title: string; kind: Section } | null;
  slots: SlotImagem[];
  pendentes: SlotImagem[]; // pedidos pela IA e ainda não gerados
  fora_do_codigo: string[]; // caminhos de slot que nenhum arquivo do projeto cita mais
  estilo: string;
  web: boolean; // já otimizado: o código aponta para .webp e eles se regravam a cada troca
};

/** Topo da conversa aberta pela IA: qual projeto e qual chat pediram estas imagens. */
function Origem(props: {
  origem: Origem;
  temImagens: boolean;
  ocupado: boolean;
  onAbrir: (id: number, kind: Section) => void;
  onOtimizar: () => void;
  onEstilo: () => void;
}) {
  const { origem } = props;
  const acao = "flex items-center gap-1 rounded-[9px] border border-line px-2.5 py-1 text-fg hover:bg-raised disabled:opacity-40";
  // Duas linhas: quem pediu (identidade + volta ao chat) e o que dá para fazer com todas as imagens.
  return (
    <div className="mb-3 overflow-hidden rounded-2xl border border-sky-900/50 bg-sky-950/15 text-xs">
      <div className="flex items-center gap-3 px-3.5 py-2.5">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-sky-500/10 text-sky-300">
          <Robo className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] text-muted">
            Aberta pela IA · projeto <span className="font-medium text-fg">{origem.projeto}</span>
          </p>
          <p className="truncate font-mono text-[11px] text-faint" title={origem.workspace}>{origem.workspace}</p>
        </div>
        {origem.chat ? (
          <button
            onClick={() => props.onAbrir(origem.chat!.id, origem.chat!.kind)}
            title={`Abrir o chat que pediu estas imagens: “${origem.chat.title}”`}
            className="flex min-w-0 max-w-[45%] shrink-0 items-center gap-1.5 rounded-[9px] border border-line px-3 py-1 text-fg hover:bg-raised"
          >
            <span className="shrink-0 text-faint">Chat</span>
            <span className="truncate">{origem.chat.title}</span>
            <ArrowRight className="size-3 shrink-0" />
          </button>
        ) : (
          <span className="shrink-0 text-faint">o chat que pediu foi apagado</span>
        )}
      </div>
      {props.temImagens && (
        <div className="flex flex-wrap items-center gap-2 border-t border-sky-900/40 px-3.5 py-2">
          <span className="mr-auto text-faint">Todas as imagens do site</span>
          <button
            onClick={props.onEstilo}
            disabled={props.ocupado}
            title="Novas versões de todas as imagens do site com outro estilo (o site só muda quando você escolher)"
            className={acao}
          >
            <Refresh className="size-3" />
            Outro estilo
          </button>
          <button
            onClick={props.onOtimizar}
            disabled={props.ocupado}
            title={origem.web
              ? "Já otimizado: os .webp se regravam a cada troca. Clique para regravar todos agora"
              : "Grava um .webp leve ao lado de cada PNG e troca .png por .webp no código do site"}
            className={acao}
          >
            {origem.web ? (
              <>
                <Check className="size-3 text-emerald-300" />
                Versão web (.webp)
              </>
            ) : (
              "Otimizar para web"
            )}
          </button>
        </div>
      )}
    </div>
  );
}

/** "Outro estilo": o texto que vai no fim de cada prompt, trocado em todas as imagens do site de uma vez. */
function EstiloTodas(props: { estilo: string; count: number; imagens: number; onGerar: (estilo: string) => void; onClose: () => void }) {
  const [estilo, setEstilo] = useState(props.estilo);
  return (
    <Modal onClose={props.onClose} label="Regerar todas com outro estilo" className="w-full max-w-xl rounded-xl border border-line bg-surface p-4 text-xs">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium text-fg">Outro estilo para as {props.imagens} imagens do site</span>
        <button onClick={props.onClose} title="Fechar" className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg">
          <X className="size-3.5" />
        </button>
      </div>
      <p className="mb-2 text-muted">
        Vai no fim do prompt de cada imagem, no lugar do estilo atual. Sai {props.count} versão(ões) de cada; o site só
        muda quando você escolher, no card de cada uma.
      </p>
      <textarea
        value={estilo}
        onChange={(e) => setEstilo(e.target.value)}
        rows={3}
        spellCheck={false}
        placeholder="cold blue night light, film grain, minimal"
        className="w-full resize-y rounded-lg border border-line bg-bg px-2.5 py-2 text-[13px] text-fg focus:border-focus focus:outline-none"
      />
      <div className="mt-3 flex items-center gap-2">
        <button className={btnPrimary} onClick={() => props.onGerar(estilo.trim())}>
          <Refresh className="mr-1 inline size-3.5" />
          Gerar {props.imagens * props.count} versões
        </button>
        {estilo.trim() !== props.estilo.trim() && props.estilo && (
          <button className={btn} onClick={() => setEstilo(props.estilo)}>
            <Undo className="mr-1 inline size-3" />
            Estilo original
          </button>
        )}
      </div>
    </Modal>
  );
}

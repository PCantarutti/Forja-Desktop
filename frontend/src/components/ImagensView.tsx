import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, uploadReferencia } from "../api";
import type { ImageOpts, LocalState, LoteImagem, LoteMeta, Message, PedidoMeta, SeedMode, SlotImagem } from "../types";
import type { Section } from "./Controls";
import { AmpliarArquivo, PainelAmpliar } from "./AmpliarVideo";
import { ArrowRight, ArrowUp, Check, Copy, Edit, FolderOpen, Image, Paperclip, Refresh, Robo, Search, Sliders, Square, TelaCheia, Trash, Undo, X } from "./icons";
import { BotaoEnviar, CaixaPrompt, DireitaPrompt, RodapePrompt, campoPrompt, larguraNumero, numeroPilula, pilula, pilulaLigada, redondo } from "./Composer";
import { btn, btnPrimary, campo, Field, input, Num, SAMPLERS } from "./LocalPanel";
import { Lightbox } from "./MessageView";
import MascaraEditor, { type ModoPintura } from "./MascaraEditor";
import { Modal } from "./Modal";
import ModelPicker from "./ModelPicker";

const POLL_MS = 1500; // só enquanto um lote roda; fora disso a tela fica parada
// O modelo que reescreve o prompt é separado do modelo do Chat: quem gera imagem costuma querer
// um modelo pequeno e rápido aqui, não o mesmo que responde no chat.
const KEY_LLM = "forja.imagem.llm";

/** Proporções comuns em múltiplos de 64 (o que o sd.cpp pede). */
const PROPORCOES: { label: string; width: number; height: number }[] = [
  { label: "1:1", width: 512, height: 512 },
  { label: "3:2", width: 768, height: 512 },
  { label: "2:3", width: 512, height: 768 },
  { label: "16:9", width: 896, height: 512 },
];

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
function Pilha(props: { n: number; largo?: boolean; children: React.ReactNode }) {
  return (
    <div className={`relative ${props.largo ? "col-span-2" : ""}`}>
      {props.n > 2 && <div aria-hidden className="absolute inset-0 translate-x-2 -translate-y-2 rotate-[3deg] rounded-xl border border-line bg-[#232323]" />}
      {props.n > 1 && <div aria-hidden className="absolute inset-0 translate-x-1 -translate-y-1 rotate-[1.5deg] rounded-xl border border-line bg-[#2a2a2a]" />}
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
  const [abrirAjustes, setAbrirAjustes] = useState(false);
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
        <Modal label="Ampliar imagem do PC" onClose={() => setAmpliarPc(false)} className="w-full max-w-2xl rounded-2xl border border-line bg-surface p-4">
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
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-5 py-6">
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

          {visiveis.map(({ pedido, resposta }) => (
            <Lote
              key={resposta.id}
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
        <div className="mx-auto max-w-3xl">
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
            <div className="mb-2 rounded-xl border border-amber-800/70 bg-amber-950/30 p-2.5 text-xs text-amber-200">
              {motivo.startsWith("Outro programa") ? (
                <p className="font-medium">{motivo}</p>
              ) : (
                <>
                  <p className="font-medium">O modelo {st.server.alias} está carregado na VRAM.</p>
                  <p className="mt-1 text-amber-200/80">
                    O sd.cpp precisa dessa memória. Descarregar derruba o cache de contexto do chat: a próxima
                    mensagem de lá reprocessa o histórico inteiro. A conversa em si não se perde.
                  </p>
                </>
              )}
              <div className="mt-2 flex gap-2">
                <button className={btnPrimary} onClick={() => perguntando?.()}>
                  {motivo.startsWith("Outro programa") ? "Gerar mesmo assim" : "Descarregar e gerar"}
                </button>
                <button className={btn} onClick={() => setPerguntando(null)}>
                  Cancelar
                </button>
              </div>
            </div>
          )}

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
              onError={mostrarErro}
              onFechar={() => setAbrirAjustes(false)}
            />
          )}

          {slotsPendentes && (
            <div className="mb-2 rounded-2xl border border-line bg-surface p-3.5 text-xs">
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
              <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-line bg-surface px-3.5 py-2.5 text-xs text-muted">
                <Robo className="size-4 shrink-0 text-sky-300/80" />
                <span className="min-w-0 flex-1">
                  Esta conversa gera só as imagens que o chat pediu para o site. Para refazer uma, use <Refresh className="inline size-3" /> no
                  card ou clique na foto. Imagem avulsa: abra uma conversa nova em Imagens.
                </span>
                <label className={`${pilula} focus-within:border-[#555]`} title="Quantas versões cada Regerar gera">
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
          <CaixaPrompt>
            {refs.length > 0 && (
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                {refs.map((r, i) => (
                  <div key={r} className="relative" title={sumidas.has(r) ? `Não encontrada: ${r}` : r}>
                    {sumidas.has(r) ? (
                      <button
                        onClick={() => {
                          trocar.current = r;
                          arquivo.current?.click();
                        }}
                        className="grid size-14 place-items-center rounded-lg border border-dashed border-amber-500/70 bg-amber-500/5 px-1 text-center text-[10px] leading-tight text-amber-300 hover:bg-amber-500/10"
                      >
                        não achada
                        <span className="underline">Reanexar</span>
                      </button>
                    ) : (
                      <img
                        src={urlDa(r)}
                        alt={`referência ${i + 1}`}
                        onError={() => setSumidas((s) => new Set(s).add(r))}
                        className="size-14 rounded-lg border border-line object-cover"
                      />
                    )}
                    {!sumidas.has(r) && (
                      <button
                        onClick={() => setPintando(r)}
                        title="Marcar onde editar: máscara, círculos ou pintura (Qwen-Image 2.1)"
                        className="absolute -bottom-1.5 -left-1.5 grid size-5 place-items-center rounded-full border border-line bg-surface text-faint hover:text-fg"
                      >
                        <Edit className="size-3" />
                      </button>
                    )}
                    <button
                      onClick={() => setRefs((atual) => atual.filter((x) => x !== r))}
                      title="Tirar da edição"
                      className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full border border-line bg-surface text-faint hover:text-fg"
                    >
                      <X className="size-3" />
                    </button>
                  </div>
                ))}
                <span className={naoEditam.length || refs.length > MAX_REFS || refs.some((r) => sumidas.has(r)) ? "text-amber-400" : "text-muted"}>
                  {refs.some((r) => sumidas.has(r))
                    ? "Imagem de referência não encontrada no lugar de antes (movida ou apagada): reanexe ou tire da edição."
                    : naoEditam.length
                    ? `${naoEditam.join(", ")} não edita imagem — escolha um modelo que edita (ex.: Qwen-Image 2.1).`
                    : refs.length > MAX_REFS
                    ? `${refs.length} imagens: o máximo são ${MAX_REFS}, máscaras incluídas.`
                    : `Editando ${refs.length} imagem(ns): descreva a mudança abaixo (o lápis marca onde mudar).`}
                </span>
              </div>
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
            <RodapePrompt>
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
              <button
                onClick={() => arquivo.current?.click()}
                title="Anexar uma imagem para editar (modelos que editam, como o Qwen-Image 2.1)"
                className={redondo}
              >
                <Paperclip className="size-4" />
              </button>
              <button onClick={() => setAmpliarPc(true)} title="Ampliar a resolução de uma imagem do PC (ESRGAN ou Lanczos)" className={redondo}>
                <TelaCheia className="size-4" />
              </button>
              <button
                onClick={() => setAbrirAjustes((v) => !v)}
                title={`Modelos, tamanho, passos, sementes\n${
                  models.length > 1
                    ? [...divisao].map(([m, n]) => `${n}× ${m.split(/[\\/]/).pop()}`).join(" · ")
                    : `${o.width}×${o.height} · ${o.steps} passos · CFG ${o.cfg}`}`}
                className={`${pilula} ${abrirAjustes ? pilulaLigada : ""}`}
              >
                <Sliders className="size-3.5" />
                {models.length ? `${models.length} modelo${models.length > 1 ? "s" : ""}` : "Escolher modelo"}
              </button>
              <label className={`${pilula} focus-within:border-[#555]`} title="Quantas variações gerar">
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
                {count === 1 ? "variação" : "variações"}
              </label>
              <button
                onClick={melhorar}
                disabled={!prompt.trim() || !llm.model || melhorando}
                title={llm.model ? `Reescrever o prompt com ${llm.model} (o modelo à direita)` : "Escolha à direita o modelo que reescreve"}
                className={pilula}
              >
                <Refresh className={`size-3.5 ${melhorando ? "animate-spin" : ""}`} />
                Melhorar prompt
              </button>
              <DireitaPrompt>
              <div className="min-w-0" title="Modelo que reescreve o prompt (não é o que gera a imagem)">
                <ModelPicker
                  provider={llm.provider}
                  model={llm.model}
                  onChange={(provider, model) => setLlm({ provider, model })}
                />
              </div>
              <BotaoEnviar
                onEnviar={() => gerar()}
                desabilitado={!prompt.trim() || !models.length || semRuntime}
                titulo={st.image_busy || ocupado ? "Entra na fila: gera quando o lote atual terminar" : "Gerar"}
              />
              </DireitaPrompt>
            </RodapePrompt>
          </CaixaPrompt>
          <p className="mt-1.5 text-center text-[11px] text-faint">
            O sd.cpp gera uma imagem por vez e libera a memória no fim — um lote é uma fila.
          </p>
          </>
          )}
        </div>
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

  return (
    <div className="mb-2 rounded-2xl border border-line bg-surface p-3.5 text-xs">
      <div className="mb-2.5 flex items-center gap-2">
        <span className="font-medium text-fg">Ajustes da geração</span>
        <button onClick={props.onFechar} className="ml-auto rounded-md p-1 text-muted hover:bg-raised hover:text-fg">
          <X className="size-3.5" />
        </button>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="flex flex-col gap-2.5">
          <Field label="Modelos" hint="Marque mais de um para dividir as variações entre eles.">
            <div className="flex max-h-40 flex-col gap-1 overflow-y-auto rounded-lg border border-line p-1.5">
              {!st.image_models.length && <span className="text-faint">Nenhum modelo de imagem nas pastas.</span>}
              {st.image_models.map((m) => {
                const ativo = props.models.includes(m.path);
                return (
                  <label key={m.path} className="flex cursor-pointer items-center gap-2 rounded-md px-1 py-0.5 hover:bg-raised">
                    <input type="checkbox" checked={ativo} onChange={() => alternar(m.path)} className="accent-white" />
                    <span className={`min-w-0 flex-1 truncate ${ativo ? "text-fg" : "text-muted"}`} title={m.path}>
                      {m.name}
                    </span>
                    {ativo && <span className="shrink-0 text-faint">{props.divisao.get(m.path) ?? 0}×</span>}
                  </label>
                );
              })}
            </div>
          </Field>

          <Field label="Negativo" hint="O que evitar na imagem.">
            <input className={input} value={o.negative} onChange={(e) => set("negative", e.target.value)} />
          </Field>

          <Field label="Proporção">
            <div className="flex flex-wrap gap-1">
              {PROPORCOES.map((p) => {
                const ativo = o.width === p.width && o.height === p.height;
                return (
                  <button
                    key={p.label}
                    onClick={() => {
                      set("width", p.width);
                      set("height", p.height);
                    }}
                    className={`rounded-full px-2.5 py-0.5 ${ativo ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Num label="Largura" value={o.width} onChange={(v) => set("width", v)} step={64} />
            <Num label="Altura" value={o.height} onChange={(v) => set("height", v)} step={64} />
          </div>
        </div>

        <div className="flex flex-col gap-2.5">
          <div className="grid grid-cols-2 gap-2">
            <Num label="Passos" value={o.steps} onChange={(v) => set("steps", v)} />
            <Num label="CFG" value={o.cfg} onChange={(v) => set("cfg", v)} step={0.5} />
          </div>
          <Field label="Amostrador">
            <select className={input} value={o.sampler} onChange={(e) => set("sampler", e.target.value)}>
              {SAMPLERS.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Sementes" hint={SEEDS.find((s) => s.id === props.seedMode)?.hint}>
            <div className="flex gap-1">
              {SEEDS.map((s) => (
                <button
                  key={s.id}
                  onClick={() => props.onSeedMode(s.id)}
                  className={`rounded-full px-2.5 py-0.5 ${props.seedMode === s.id ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </Field>
          {props.seedMode !== "aleatoria" && (
            <Num label="Semente base" value={o.seed} onChange={(v) => set("seed", v)} hint="0 = sorteia uma e anota" />
          )}
          <Field label="Salvar imagens em">
            <div className="flex items-center gap-2">
              <input
                className={input}
                value={o.out_dir || st.image_dir}
                onChange={(e) => set("out_dir", e.target.value)}
                spellCheck={false}
              />
              <button
                className={btn}
                title="Escolher pasta"
                onClick={async () => {
                  const escolhida = window.forja ? await window.forja.pickFolder(o.out_dir || st.image_dir) : "";
                  if (escolhida) set("out_dir", escolhida);
                }}
              >
                <FolderOpen className="size-3.5" />
              </button>
            </div>
          </Field>
          <Num
            label="Apagar descartadas depois de (dias)"
            value={o.descarte_dias}
            onChange={salvarPrazo}
            hint="0 = guardar para sempre."
          />
          <div className="flex items-center gap-2">
            <button className={btn} onClick={esvaziar}>
              <Trash className="mr-1 inline size-3.5" />
              Esvaziar descartadas agora
            </button>
            {limpando && <span className="text-faint">{limpando}</span>}
          </div>
        </div>
      </div>
    </div>
  );
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
    <section className="my-8">
      {ampliando && (
        <Modal label="Ampliar imagem" onClose={() => setAmpliando(null)} className="w-full max-w-sm rounded-2xl border border-line bg-surface p-4">
          <p className="mb-3 text-sm text-fg">Ampliar imagem</p>
          <PainelAmpliar
            imagem
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
      <div className="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <p className="min-w-0 flex-1 text-[15px] text-fg">{props.pedido.content}</p>
        <span className="text-xs text-faint">
          {viva ? `gerando ${prontas + 1} de ${imagens.length}…`
            : props.resposta.status === "interrompido" ? `interrompido: ${imagens.length - faltam} de ${imagens.length} prontas`
            : deSlots ? `${imagens.length} imagens do site` : `${imagens.length} variações`}
        </span>
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] text-faint">
        {meta.opts.width && <Chip>{`${meta.opts.width}×${meta.opts.height}`}</Chip>}
        {meta.opts.steps !== undefined && <Chip>{`${meta.opts.steps} passos`}</Chip>}
        {meta.opts.cfg !== undefined && <Chip>{`CFG ${meta.opts.cfg}`}</Chip>}
        {meta.opts.sampler && <Chip>{meta.opts.sampler}</Chip>}
        <button
          onClick={() => props.onSemente(imagens[0].seed)}
          title={`Sementes usadas: ${imagens.map((i) => i.seed).join(", ")}\nClique para usar ${imagens[0].seed} no próximo lote`}
          className="rounded-full bg-raised px-2 py-0.5 hover:text-fg"
        >
          {rotuloSementes(imagens.map((i) => i.seed), meta.seed_mode)}
        </button>
        {[...new Set(imagens.map((i) => i.model_name))].map((n) => (
          <Chip key={n}>{n}</Chip>
        ))}
        {meta.opts.ampliacao && <Chip>{`ampliada ${meta.opts.ampliacao.fator}×`}</Chip>}
        {!!(props.pedido.meta as PedidoMeta | null)?.refs?.length && (
          <Chip>edição de {(props.pedido.meta as PedidoMeta).refs!.length} imagem(ns)</Chip>
        )}
      </div>

      <div className="grid grid-cols-2 items-start gap-3 md:grid-cols-3 xl:grid-cols-4">
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
            origem={(props.pedido.meta as PedidoMeta | null)?.refs?.[0]}
          />
          );
        })}
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-2 text-xs">
        {viva ? (
          <button className={btn} onClick={cancelar}>
            <Square className="mr-1 inline size-3" />
            Cancelar lote
          </button>
        ) : (
          aprovaveis.length > 0 && !deSlots && (
            <>
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
              <button className={btn} onClick={() => setSel(new Set(aprovaveis.map((i) => i.path)))}>
                Marcar todas
              </button>
              {sel.size > 0 && (
                <button className={btn} onClick={() => setSel(new Set())}>
                  Limpar seleção
                </button>
              )}
            </>
          )
        )}
        {!viva && faltam > 0 && (
          <button className={btn} onClick={() => continuar()}
                  title="Gera só as que faltaram, com a mesma semente e os mesmos ajustes (a imagem que parou no meio recomeça do zero)">
            <ArrowUp className="mr-1 inline size-3.5 rotate-90" />
            Continuar ({faltam})
          </button>
        )}
        {!deSlots && (
          <button className={btn} onClick={props.onReaproveitar} title="Traz prompt e ajustes deste lote para o campo">
            <Refresh className="mr-1 inline size-3.5" />
            Reaproveitar
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
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-amber-800/70 bg-amber-950/30 p-2.5 text-xs text-amber-200">
          <span className="flex-1">
            {vram.startsWith("Outro programa") ? vram : "Tem um modelo carregado na VRAM, e o sd.cpp precisa dessa memória."}
          </span>
          <button className={btnPrimary} onClick={() => continuar(true)}>
            {vram.startsWith("Outro programa") ? "Continuar mesmo assim" : "Descarregar e continuar"}
          </button>
          <button className={btn} onClick={() => setVram("")}>Cancelar</button>
        </div>
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

export function Liquido({ fracao, sPasso, restante }: { fracao: number; sPasso?: number; restante?: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, fracao)) * 100);
  return (
    <>
      <div
        // Cor sólida por dentro e a transparência no grupo: crista e corpo se sobrepõem 1 px, e com
        // cada um semitransparente a sobreposição aparecia como uma linha mais escura.
        className="absolute inset-x-0 bottom-0 bg-sky-400 opacity-25 transition-[height] duration-1000 ease-out"
        style={{ height: `${Math.max(pct, 4)}%` }}
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <svg viewBox="0 0 200 10" preserveAspectRatio="none" className="onda absolute -top-[9px] left-0 h-2.5 w-[200%]" aria-hidden>
          <path d="M0 5 Q 25 0 50 5 T 100 5 T 150 5 T 200 5 V 10 H 0 Z" className="fill-sky-400" />
        </svg>
      </div>
      <div className="relative text-center tabular-nums">
        <span className="block text-sm font-medium text-fg">{pct}%</span>
        {!!sPasso && (
          <span className="block text-[11px] text-muted">
            {velocidade(sPasso)} · {duracao(restante ?? 0)} restantes
          </span>
        )}
      </div>
    </>
  );
}

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
  return <span className="rounded-full bg-raised px-2 py-0.5">{children}</span>;
}

/** A bolinha (24 px, onde fica a de marcar) com a % dentro; o anel contorna por fora. */
export function AnelProgresso({ pct }: { pct: number }) {
  return (
    <svg
      viewBox="0 0 30 30"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      className="absolute left-[5px] top-[5px] size-[30px]"
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
      className={`group relative overflow-hidden rounded-xl border ${
        props.marcada ? "border-emerald-500" : "border-line"
      } bg-raised`}
    >
      {temArquivo ? (
        <img
          src={srcDe(img)}
          alt={`semente ${img.seed}`}
          onClick={props.onZoom}
          style={ar}
          className={`w-full cursor-zoom-in object-cover ${
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
            <span className="px-3 text-center text-[11px] text-red-300" title={img.error}>
              {img.error.split("\n")[0].slice(0, 90)}
            </span>
          ) : img.status === "gerando" && !comPrevia ? (
            <Liquido fracao={img.progress ?? 0} sPasso={img.s_passo} restante={img.restante} />
          ) : null}
        </div>
      )}

      {comPrevia && <AnelProgresso pct={pct} />}
      {temArquivo && !props.semMarcar && (
        <button
          onClick={props.onMarcar}
          title={props.marcada ? "Desmarcar" : "Marcar para manter"}
          className={`absolute left-2 top-2 grid size-6 place-items-center rounded-full border ${
            props.marcada ? "border-emerald-400 bg-emerald-500 text-black" : "border-line bg-black/60 text-transparent hover:text-white"
          }`}
        >
          <Check className="size-3.5" />
        </button>
      )}

      <figcaption className="flex items-center gap-1.5 px-2 py-1.5 text-[11px]">
        <span className={`min-w-0 flex-1 truncate ${CORES[img.status]}`} title={`${img.model_name} · ${img.path}`}>
          {img.nome ?? (img.model_name || "—")}
        </span>
        {props.extra && (
          <span
            title={props.extra === "fora do código" ? "Nenhum arquivo do projeto aponta mais para esta imagem" : undefined}
            className={`shrink-0 ${props.extra.startsWith("gerando") ? "animate-pulse text-sky-300"
              : props.extra === "fora do código" ? "text-amber-300" : "text-faint"}`}
          >
            {props.extra}
          </span>
        )}
        {temArquivo && (
          <>
            <button onClick={props.onSemente} title="Usar esta semente no próximo lote" className="text-faint hover:text-fg">
              <Search className="mr-0.5 inline size-3" />
              {img.seed}
            </button>
            {props.onRegerar && (
              <button onClick={props.onRegerar} title="Regerar: novas variações com o mesmo prompt, para escolher qual fica no site" className="text-faint hover:text-fg">
                <Refresh className="size-3" />
              </button>
            )}
            <button onClick={props.onEditar} title="Editar esta imagem no próximo lote" className="text-faint hover:text-fg">
              <Edit className="size-3" />
            </button>
            <button onClick={props.onAmpliar} title="Ampliar a resolução (ESRGAN ou Lanczos)" className="text-faint hover:text-fg">
              <TelaCheia className="size-3" />
            </button>
            <button onClick={props.onPasta} title="Mostrar na pasta" className="text-faint hover:text-fg">
              <FolderOpen className="size-3" />
            </button>
          </>
        )}
        {comPrevia ? (
          // a % está na bolinha; aqui a velocidade e quanto falta (antes do 1º passo, carregando: "gerando")
          <span className="shrink-0 tabular-nums text-sky-300">
            {img.s_passo ? `${velocidade(img.s_passo)} · ${duracao(img.restante ?? 0)}` : "gerando"}
          </span>
        ) : (
          !temArquivo && <span className={CORES[img.status]}>{img.fase ?? img.status}</span>
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
    <Modal onClose={props.onClose} label={`Variações de ${nome}`} className="flex max-h-[90vh] w-full max-w-5xl flex-col rounded-2xl border border-line bg-surface p-4 text-xs">
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
          className="w-full resize-y rounded-lg border border-line bg-bg px-2.5 py-2 text-[13px] leading-relaxed text-fg focus:border-[#555] focus:outline-none"
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
  const acao = "flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-fg hover:bg-raised disabled:opacity-40";
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
            className="flex min-w-0 max-w-[45%] shrink-0 items-center gap-1.5 rounded-full border border-line px-3 py-1 text-fg hover:bg-raised"
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
    <Modal onClose={props.onClose} label="Regerar todas com outro estilo" className="w-full max-w-xl rounded-2xl border border-line bg-surface p-4 text-xs">
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
        className="w-full resize-y rounded-lg border border-line bg-bg px-2.5 py-2 text-[13px] text-fg focus:border-[#555] focus:outline-none"
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

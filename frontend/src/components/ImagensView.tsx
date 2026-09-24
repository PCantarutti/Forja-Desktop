import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, uploadReferencia } from "../api";
import type { ImageOpts, LocalState, LoteImagem, LoteMeta, Message, PedidoMeta, SeedMode } from "../types";
import { ArrowUp, Check, Edit, FolderOpen, Image, Refresh, Search, Sliders, Square, Trash, X } from "./icons";
import { btn, btnPrimary, campo, Field, input, Num, SAMPLERS } from "./LocalPanel";
import { Lightbox } from "./MessageView";
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

const SEEDS: { id: SeedMode; label: string; hint: string }[] = [
  { id: "incremental", label: "Incremental", hint: "base, base+1, base+2… variações próximas e repetíveis" },
  { id: "aleatoria", label: "Aleatória", hint: "uma semente sorteada por imagem (mas anotada, dá para repetir)" },
  { id: "fixa", label: "Fixa", hint: "a mesma em todas: compara modelos com a variável travada" },
];

const CORES: Record<LoteImagem["status"], string> = {
  pendente: "text-faint",
  gerando: "text-sky-300",
  pronta: "text-muted",
  mantida: "text-emerald-300",
  descartada: "text-faint",
  cancelada: "text-faint",
  erro: "text-red-300",
};

const urlDa = (p: string) => `/api/local/image/file?path=${encodeURIComponent(p)}`;
const rodando = (m: Message) => m.role === "assistant" && m.status === "running";

export default function ImagensView(props: {
  conv: number | null;
  ensureConversation: () => Promise<number>;
  provider: string;
  model: string;
  onError: (e: string) => void;
  onConversationChanged: () => void;
}) {
  const [st, setSt] = useState<LocalState | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [o, setO] = useState<ImageOpts | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  // Imagens a editar (-r do sd.cpp), na ordem. Vazio = gerar do zero.
  const [refs, setRefs] = useState<string[]>([]);
  const arquivo = useRef<HTMLInputElement>(null);
  const [count, setCount] = useState(4);
  const [seedMode, setSeedMode] = useState<SeedMode>("incremental");
  const [abrirAjustes, setAbrirAjustes] = useState(false);
  const [perguntando, setPerguntando] = useState(false);
  const [melhorando, setMelhorando] = useState(false);
  const [zoom, setZoom] = useState<string | null>(null);
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

  useEffect(() => {
    carregarConversa();
  }, [carregarConversa]);

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

  const set = <K extends keyof ImageOpts>(k: K, v: ImageOpts[K]) => setO((c) => c && { ...c, [k]: v });

  const divisao = useMemo(() => {
    // Mesma conta do backend (_distribuir): blocos contíguos, resto nos primeiros.
    if (!models.length) return new Map<string, number>();
    const por = Math.floor(count / models.length);
    const resto = count % models.length;
    return new Map(models.map((m, i) => [m, por + (i < resto ? 1 : 0)]));
  }, [models, count]);

  async function gerar(confirm = false) {
    if (!o || !prompt.trim() || !models.length) return;
    try {
      // O que está na tela também vira o padrão da ferramenta image_generate do agente.
      await api.put("/local/image/defaults", { ...o, model: models[0] });
      const conv = await props.ensureConversation();
      await api.post(`/imagens/${conv}/gerar`, {
        prompt,
        // offload/flash attention são do modelo (IA local › Modelos): o global não passa por cima
        opts: { ...o, model: undefined, seed: undefined, offload: undefined, flash_attn: undefined, vae_tiling: undefined, te_cpu: undefined },
        models,
        count,
        seed: o.seed,
        seed_mode: seedMode,
        confirm,
        refs,
      });
      setPerguntando(false);
      setErro("");
      setPrompt(""); // "Reaproveitar" no lote traz o texto de volta
      props.onConversationChanged();
      carregarConversa(conv);
    } catch (e: any) {
      if (e.status === 409) setPerguntando(true); // tem LLM na VRAM: a conta é do usuário
      else mostrarErro(e.message);
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
    try {
      for (const f of Array.from(files ?? [])) editar(await uploadReferencia(f));
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
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-5 py-6">
          {!lotes.length && (
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

          {lotes.map(({ pedido, resposta }) => (
            <Lote
              key={resposta.id}
              pedido={pedido}
              resposta={resposta}
              onZoom={setZoom}
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
        <div className="mx-auto max-w-5xl">
          {erro && (
            <div className="mb-2 flex items-start gap-2 rounded-xl border border-red-900/70 bg-red-950/30 p-2.5 text-xs text-red-200">
              <p className="min-w-0 flex-1 whitespace-pre-wrap break-words">{erro}</p>
              <button onClick={() => setErro("")} title="Fechar" className="text-red-300 hover:text-red-100">
                <X className="size-3.5" />
              </button>
            </div>
          )}
          {perguntando && (
            <div className="mb-2 rounded-xl border border-amber-800/70 bg-amber-950/30 p-2.5 text-xs text-amber-200">
              <p className="font-medium">O modelo {st.server.alias} está carregado na VRAM.</p>
              <p className="mt-1 text-amber-200/80">
                O sd.cpp precisa dessa memória. Descarregar derruba o cache de contexto do chat: a próxima
                mensagem de lá reprocessa o histórico inteiro. A conversa em si não se perde.
              </p>
              <div className="mt-2 flex gap-2">
                <button className={btnPrimary} onClick={() => gerar(true)}>
                  Descarregar e gerar
                </button>
                <button className={btn} onClick={() => setPerguntando(false)}>
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

          <div className="rounded-3xl border border-line bg-surface p-3">
            {refs.length > 0 && (
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                {refs.map((r, i) => (
                  <div key={r} className="relative" title={r}>
                    <img src={urlDa(r)} alt={`referência ${i + 1}`} className="size-14 rounded-lg border border-line object-cover" />
                    <button
                      onClick={() => setRefs((atual) => atual.filter((x) => x !== r))}
                      title="Tirar da edição"
                      className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full border border-line bg-surface text-faint hover:text-fg"
                    >
                      <X className="size-3" />
                    </button>
                  </div>
                ))}
                <span className={naoEditam.length ? "text-amber-400" : "text-muted"}>
                  {naoEditam.length
                    ? `${naoEditam.join(", ")} não edita imagem — escolha um modelo que edita (ex.: Qwen-Image 2.1).`
                    : "Editando: descreva a mudança no campo abaixo."}
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
              className="w-full resize-none bg-transparent text-[15px] text-fg placeholder:text-faint focus:outline-none"
            />
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
              <button
                onClick={() => setAbrirAjustes((v) => !v)}
                title="Modelos, tamanho, passos, sementes"
                className={`inline-flex items-center gap-1 ${btn} ${abrirAjustes ? "bg-raised" : ""}`}
              >
                <Sliders className="size-3.5" />
                {models.length ? `${models.length} modelo${models.length > 1 ? "s" : ""}` : "Escolher modelo"}
              </button>
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
                title="Trazer uma imagem para editar (modelos que editam, como o Qwen-Image 2.1)"
                className={`inline-flex items-center gap-1 ${btn}`}
              >
                <Image className="size-3.5" />
                Editar imagem
              </button>
              <button
                onClick={melhorar}
                disabled={!prompt.trim() || !llm.model || melhorando}
                title={llm.model ? `Reescrever o prompt com ${llm.model}` : "Escolha ao lado o modelo que reescreve"}
                className={`inline-flex items-center gap-1 ${btn}`}
              >
                <Refresh className={`size-3.5 ${melhorando ? "animate-spin" : ""}`} />
                Melhorar prompt
              </button>
              {/* Div à parte: o ModelPicker traz ml-auto, que na linha do composer jogaria tudo para a direita. */}
              <div title="Modelo que reescreve o prompt (não é o que gera a imagem)">
                <ModelPicker
                  provider={llm.provider}
                  model={llm.model}
                  onChange={(provider, model) => setLlm({ provider, model })}
                />
              </div>
              <label className="inline-flex items-center gap-1.5 text-muted">
                Variações
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={count}
                  onChange={(e) => setCount(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
                  className={`${campo} w-16 text-right`}
                />
              </label>
              <span className="min-w-0 flex-1 truncate text-faint">
                {models.length > 1
                  ? [...divisao].map(([m, n]) => `${n}× ${m.split(/[\\/]/).pop()}`).join(" · ")
                  : `${o.width}×${o.height} · ${o.steps} passos · CFG ${o.cfg}`}
              </span>
              <button
                onClick={() => gerar()}
                disabled={!prompt.trim() || !models.length || semRuntime || st.image_busy || ocupado}
                title={st.image_busy || ocupado ? "Já tem imagem sendo gerada" : "Gerar"}
                className="grid size-9 place-items-center rounded-full bg-fg text-black hover:bg-white disabled:bg-raised disabled:text-faint"
              >
                <ArrowUp />
              </button>
            </div>
          </div>
          <p className="mt-1.5 text-center text-[11px] text-faint">
            O sd.cpp gera uma imagem por vez e libera a memória no fim — um lote é uma fila.
          </p>
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

  // Enquanto o lote roda os caminhos mudam de status; a seleção acompanha o que já ficou pronto.
  useEffect(() => {
    setSel(new Set(imagens.filter((i) => i.status === "mantida").map((i) => i.path)));
  }, [props.resposta.id, imagens.filter((i) => i.status === "mantida").length]);

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

  async function mostrarNaPasta(caminho: string) {
    try {
      await api.post("/open", { path: caminho, mode: "reveal" });
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  return (
    <section className="my-8">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <p className="min-w-0 flex-1 text-[15px] text-fg">{props.pedido.content}</p>
        <span className="text-xs text-faint">
          {viva ? `gerando ${prontas + 1} de ${imagens.length}…` : `${imagens.length} variações`}
        </span>
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] text-faint">
        {meta.opts.width && <Chip>{`${meta.opts.width}×${meta.opts.height}`}</Chip>}
        {meta.opts.steps !== undefined && <Chip>{`${meta.opts.steps} passos`}</Chip>}
        {meta.opts.cfg !== undefined && <Chip>{`CFG ${meta.opts.cfg}`}</Chip>}
        {meta.opts.sampler && <Chip>{meta.opts.sampler}</Chip>}
        <Chip>{`sementes: ${meta.seed_mode}`}</Chip>
        {[...new Set(imagens.map((i) => i.model_name))].map((n) => (
          <Chip key={n}>{n}</Chip>
        ))}
        {!!(props.pedido.meta as PedidoMeta | null)?.refs?.length && (
          <Chip>edição de {(props.pedido.meta as PedidoMeta).refs!.length} imagem(ns)</Chip>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
        {imagens.map((img) => (
          <Cartao
            key={img.path}
            img={img}
            marcada={sel.has(img.path)}
            onMarcar={() =>
              setSel((s) => {
                const novo = new Set(s);
                if (novo.has(img.path)) novo.delete(img.path);
                else novo.add(img.path);
                return novo;
              })
            }
            onZoom={() => props.onZoom(urlDa(img.path))}
            onSemente={() => props.onSemente(img.seed)}
            onPasta={() => mostrarNaPasta(img.path)}
            onEditar={() => props.onEditar(img.path)}
            origem={(props.pedido.meta as PedidoMeta | null)?.refs?.[0]}
          />
        ))}
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-2 text-xs">
        {viva ? (
          <button className={btn} onClick={cancelar}>
            <Square className="mr-1 inline size-3" />
            Cancelar lote
          </button>
        ) : (
          aprovaveis.length > 0 && (
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
        <button className={btn} onClick={props.onReaproveitar} title="Traz prompt e ajustes deste lote para o campo">
          <Refresh className="mr-1 inline size-3.5" />
          Reaproveitar
        </button>
        {decididas > 0 && (
          <span className="text-faint">
            {imagens.filter((i) => i.status === "mantida").length} mantida(s) ·{" "}
            {imagens.filter((i) => i.status === "descartada").length} em descartadas/
          </span>
        )}
      </div>
    </section>
  );
}

/** Nível sobe com o passo da amostragem; antes do 1º passo (carregando pesos) fica uma lâmina no fundo. */
/** "32 s/passo" quando lento, "2,5 passos/s" quando rápido — como o sd.cpp decide a unidade. */
const velocidade = (s: number) =>
  s >= 1 ? `${Math.round(s)} s/passo` : `${(1 / s).toFixed(1).replace(".", ",")} passos/s`;

const duracao = (s: number) =>
  s < 60 ? `~${Math.max(1, Math.round(s))} s` : `~${Math.floor(s / 60)} min${s % 60 >= 30 && s < 600 ? " 30 s" : ""}`;

function Liquido({ fracao, sPasso, restante }: { fracao: number; sPasso?: number; restante?: number }) {
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

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-full bg-raised px-2 py-0.5">{children}</span>;
}

function Cartao(props: {
  img: LoteImagem;
  marcada: boolean;
  onMarcar: () => void;
  onZoom: () => void;
  onSemente: () => void;
  onPasta: () => void;
  onEditar: () => void;
  origem?: string; // edição: a imagem que está sendo editada aparece por trás enquanto gera
}) {
  const { img } = props;
  const temArquivo = ["pronta", "mantida", "descartada"].includes(img.status);

  return (
    <figure
      className={`group relative overflow-hidden rounded-xl border ${
        props.marcada ? "border-emerald-500" : "border-line"
      } bg-raised`}
    >
      {temArquivo ? (
        <img
          src={urlDa(img.path)}
          alt={`semente ${img.seed}`}
          onClick={props.onZoom}
          className={`aspect-square w-full cursor-zoom-in object-cover ${
            img.status === "descartada" ? "opacity-40 grayscale" : ""
          }`}
        />
      ) : (
        <div className="relative grid aspect-square w-full place-items-center overflow-hidden">
          {props.origem && img.status !== "erro" && (
            <img src={urlDa(props.origem)} alt="imagem em edição" className="absolute inset-0 size-full object-cover opacity-40" />
          )}
          {img.status === "erro" ? (
            <span className="px-3 text-center text-[11px] text-red-300" title={img.error}>
              {img.error.split("\n")[0].slice(0, 90)}
            </span>
          ) : img.status === "gerando" ? (
            <Liquido fracao={img.progress ?? 0} sPasso={img.s_passo} restante={img.restante} />
          ) : null}
        </div>
      )}

      {temArquivo && (
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
          {img.model_name || "—"}
        </span>
        {temArquivo && (
          <>
            <button onClick={props.onSemente} title="Usar esta semente no próximo lote" className="text-faint hover:text-fg">
              <Search className="mr-0.5 inline size-3" />
              {img.seed}
            </button>
            <button onClick={props.onEditar} title="Editar esta imagem no próximo lote" className="text-faint hover:text-fg">
              <Edit className="size-3" />
            </button>
            <button onClick={props.onPasta} title="Mostrar na pasta" className="text-faint hover:text-fg">
              <FolderOpen className="size-3" />
            </button>
          </>
        )}
        {!temArquivo && <span className={CORES[img.status]}>{img.status}</span>}
      </figcaption>
    </figure>
  );
}

import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Modal } from "./Modal";
import type { Hardware, HfFile, HfModel, HfRepo } from "../types";
import { Markdown } from "./MessageView";
import { Check, Copy, Download, Search, X } from "./icons";
import SelosModo from "./SelosModo";

const chip = "rounded-[5px] bg-raised px-1.5 py-0.5 font-mono text-[11px] text-muted";

const ORDENS = [
  ["relevancia", "Relevância"],
  ["downloads", "Mais downloads"],
  ["curtidas", "Mais curtidas"],
  ["recentes", "Atualizados recentemente"],
] as const;

const FOLGA = 1.2 * 2 ** 30; // contexto e buffers de cálculo que sobem junto com os pesos

/** Se o arquivo cabe na VRAM, se cabe só com parte na RAM, ou se não cabe de jeito nenhum. */
function cabe(bytes: number, hw?: Hardware) {
  if (!bytes || !hw?.ram) return { cor: "text-faint", dica: "", tom: "", rotulo: "" };
  const vram = hw.vram;
  const gb = (n: number) => `${(n / 2 ** 30).toFixed(1)} GB`;
  if (vram && bytes + FOLGA <= vram)
    return {
      cor: "text-ok",
      tom: "border-ok/40 bg-ok/[.06]",
      rotulo: "cabe na GPU",
      dica: `Cabe inteiro na GPU: ${gb(bytes)} de ${gb(vram)} de VRAM, com folga para o contexto. É o caso mais rápido.`,
    };
  if (bytes + FOLGA <= hw.ram + vram)
    return {
      cor: "text-warn",
      tom: "border-warn/40 bg-warn/[.06]",
      rotulo: vram ? "parte na RAM" : "só na CPU",
      dica: vram
        ? `Não cabe todo na VRAM (${gb(vram)}): parte das camadas fica na RAM (${gb(hw.ram)}). Carrega, mas gera mais devagar — ajuste "Camadas na GPU".`
        : `Sem GPU detectada: roda na CPU com ${gb(hw.ram)} de RAM. Devagar.`,
    };
  return {
    cor: "text-err",
    tom: "border-err/40 bg-err/[.07]",
    rotulo: "não carrega",
    dica: `Maior que a memória total da máquina (${gb(vram)} de VRAM + ${gb(hw.ram)} de RAM). Não vai carregar.`,
  };
}

function milhares(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(".", ",")}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

function bilhoes(n: number): string {
  if (!n) return "";
  return n >= 1e9 ? `${+(n / 1e9).toFixed(1)}B` : `${Math.round(n / 1e6)}M`;
}

function tamanho(n: number): string {
  if (!n) return "";
  return n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(2)} GB` : `${Math.round(n / 2 ** 20)} MB`;
}

function quando(iso: string): string {
  if (!iso) return "";
  const dias = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (dias <= 0) return "hoje";
  if (dias === 1) return "ontem";
  if (dias < 30) return `há ${dias} dias`;
  const meses = Math.floor(dias / 30);
  return meses === 1 ? "há 1 mês" : `há ${meses} meses`;
}

/** Busca de modelos no Hugging Face: lista à esquerda, ficha do modelo à direita. */
export default function ModelSearch(props: {
  kind: "text" | "image" | "video" | "ampliar";
  destino: string;
  hardware?: Hardware;
  onKind: (k: "text" | "image" | "video" | "ampliar") => void;
  onDownload: (repo: string, file: string, subpasta?: string) => void;
  onClose: () => void;
  onError: (e: string) => void;
}) {
  const [q, setQ] = useState("");
  const [ordem, setOrdem] = useState<string>("relevancia");
  const [lista, setLista] = useState<HfModel[] | null>(null);
  const [buscando, setBuscando] = useState(false);
  const [sel, setSel] = useState("");
  const [repo, setRepo] = useState<HfRepo | null>(null);
  const [baixados, setBaixados] = useState<string[]>([]);
  const pedido = useRef(0);

  // vídeo: vazio = "wan"; ampliação: vazio = os de referência. A lista já abre cheia.
  const semTermo = props.kind === "video" || props.kind === "ampliar";
  async function buscar() {
    if (q.trim().length < 2 && !semTermo) return;
    const meu = ++pedido.current;
    setBuscando(true);
    setSel("");
    setRepo(null);
    try {
      const r = await api.get<{ models: HfModel[] }>(
        `/local/search?kind=${props.kind}&sort=${ordem}&q=${encodeURIComponent(q)}`,
      );
      if (meu === pedido.current) setLista(r.models);
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      if (meu === pedido.current) setBuscando(false);
    }
  }

  // Busca sozinha quando a digitação para — sem Enter. 450 ms é o tempo de uma pausa entre palavras.
  useEffect(() => {
    if (q.trim().length < 2 && !(semTermo && !q.trim())) return;
    const t = setTimeout(buscar, q.trim() ? 450 : 0);
    return () => clearTimeout(t);
  }, [q, ordem, props.kind]);

  useEffect(() => {
    if (!sel) return;
    setRepo(null);
    api
      .get<HfRepo>(`/local/repo?kind=${props.kind}&repo=${encodeURIComponent(sel)}`)
      .catch((e) => (props.onError(e.message), null))
      .then((r) => r && setRepo(r));
  }, [sel, props.kind]);

  function baixar(f: HfFile) {
    props.onDownload(sel, f.path, f.subpasta);
    setBaixados((b) => [...b, f.path]);
  }

  return (
    <Modal
      onClose={props.onClose}
      label="Procurar modelos no Hugging Face"
      className="flex h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-[18px] border border-line-strong bg-bg text-xs"
    >
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
          <Search className="size-4 shrink-0 text-muted" />
          <input
            autoFocus
            className="min-w-0 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-faint"
            placeholder={props.kind === "text" ? "Buscar modelos no Hugging Face…" : props.kind === "image" ? "Buscar modelos de imagem…"
              : props.kind === "ampliar" ? "Buscar ampliadores (esrgan, 4x, seedvr2…)" : "Buscar modelos de vídeo (Wan)…"}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && buscar()}
            spellCheck={false}
          />
          <div className="flex shrink-0 gap-0.5 rounded-[9px] border border-line bg-surface p-0.5">
            {(["text", "image", "video", "ampliar"] as const).map((k) => (
              <button
                key={k}
                onClick={() => {
                  props.onKind(k);
                  setLista(null);
                  setSel("");
                }}
                className={`rounded-[7px] px-2.5 py-1 ${props.kind === k ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}
              >
                {k === "text" ? "chat" : k === "image" ? "imagem" : k === "video" ? "vídeo" : "ampliação"}
              </button>
            ))}
          </div>
          <select
            className="shrink-0 rounded-lg border border-line bg-raised px-2 py-1 text-xs text-muted"
            value={ordem}
            onChange={(e) => setOrdem(e.target.value)}
            title="Ordenar por"
          >
            {ORDENS.map(([v, rotulo]) => (
              <option key={v} value={v}>
                {rotulo}
              </option>
            ))}
          </select>
          <button onClick={props.onClose} title="Fechar (Esc)" className="shrink-0 text-muted hover:text-fg">
            <X className="size-4" />
          </button>
        </div>
        {props.hardware?.ram ? (
          <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-1 text-faint">
            <span>
              {props.hardware.gpus[0]?.name ?? "sem GPU"} · VRAM {(props.hardware.vram / 2 ** 30).toFixed(1)} GB · RAM{" "}
              {(props.hardware.ram / 2 ** 30).toFixed(1)} GB
            </span>
            <span className="ml-auto flex items-center gap-3">
              <span className="inline-flex items-center gap-1"><span className="size-1.5 rounded-full bg-ok" />cabe na GPU</span>
              <span className="inline-flex items-center gap-1"><span className="size-1.5 rounded-full bg-warn" />parte na RAM</span>
              <span className="inline-flex items-center gap-1"><span className="size-1.5 rounded-full bg-err" />não carrega</span>
            </span>
          </div>
        ) : null}

        <div className="flex min-h-0 flex-1">
          <aside className="w-80 shrink-0 overflow-y-auto border-r border-line">
            {buscando && <p className="p-3 text-faint">buscando…</p>}
            {!buscando && lista === null && (
              <p className="p-3 text-faint">
                {props.kind === "ampliar"
                  ? "Só aparecem os que o Forja roda, conferidos pelo conteúdo do arquivo: ESRGAN (pelo sd-cli), DAT/HAT/SwinIR/SPAN/PLKSR e compactos (pelo ComfyUI) e SeedVR2 (difusão, pelo ComfyUI)."
                  : semTermo ? "Só aparecem os Wan, que é o que o stable-diffusion.cpp gera em vídeo." : "Digite o que procura e aperte Enter. Ex.: qwen3, gemma, sdxl."}
              </p>
            )}
            {!buscando && lista?.length === 0 && <p className="p-3 text-muted">Nada encontrado.</p>}
            {lista?.map((m) => (
              <button
                key={m.id}
                onClick={() => setSel(m.id)}
                className={`flex w-full flex-col gap-0.5 border-b border-line/60 px-3 py-2 text-left hover:bg-raised ${
                  sel === m.id ? "bg-raised" : ""
                }`}
              >
                <span className="flex items-center gap-1.5">
                  <span className="min-w-0 flex-1 truncate text-fg">{m.id.split("/").pop()}</span>
                  {m.modos && <SelosModo modos={m.modos} />}
                </span>
                <span className="truncate text-faint">{m.variante_nome ? `${m.variante_nome} · ${m.author}` : m.author}</span>
                <span className="flex items-center gap-2 font-mono text-[11px] text-faint">
                  <span>{milhares(m.downloads)} ↓</span>
                  <span>{milhares(m.likes)} ★</span>
                  <span className="ml-auto">{quando(m.updated)}</span>
                </span>
              </button>
            ))}
          </aside>

          <section className="min-w-0 flex-1 overflow-y-auto p-4">
            {!sel && <p className="text-faint">Escolha um modelo na lista para ver a ficha dele.</p>}
            {sel && !repo && <p className="text-faint">carregando ficha…</p>}
            {repo && (
              <>
                <div className="flex items-center gap-2">
                  <h2 className="min-w-0 flex-1 truncate text-base text-fg">{repo.id}</h2>
                  <button
                    title="Copiar o nome do repositório"
                    className="shrink-0 text-muted hover:text-fg"
                    onClick={() => navigator.clipboard.writeText(repo.id)}
                  >
                    <Copy className="size-3.5" />
                  </button>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-3 text-faint">
                  <span>{milhares(repo.downloads)} downloads</span>
                  <span>{milhares(repo.likes)} curtidas</span>
                  <span>atualizado {quando(repo.updated)}</span>
                  {repo.gated && <span className="text-amber-400">acesso restrito</span>}
                </div>

                <div className="mt-3 flex flex-wrap gap-1.5">
                  {repo.params > 0 && <span className={chip}>PARAMS {bilhoes(repo.params)}</span>}
                  {repo.arch && <span className={chip}>ARCH {repo.arch}</span>}
                  {repo.ctx_train > 0 && props.kind === "text" && <span className={chip}>CTX {milhares(repo.ctx_train)}</span>}
                  {repo.license && <span className={chip}>{repo.license}</span>}
                  <span className="rounded-[5px] bg-accent-soft px-1.5 py-0.5 font-mono text-[11px] text-accent-text">
                    {props.kind === "text" ? "GGUF" : props.kind === "image" ? "imagem" : props.kind === "video" ? "vídeo" : "ampliação"}
                  </span>
                </div>

                {/* Capacidades são de modelo de chat; num modelo de difusão a seção só faria ruído. */}
                {props.kind === "text" && (
                <div className="mt-3">
                  <p className="text-muted">Capacidades</p>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {[
                      ["vision", "Visão"],
                      ["tools", "Ferramentas"],
                      ["reasoning", "Raciocínio"],
                    ].map(([k, rotulo]) => {
                      const tem = repo.capabilities[k as keyof typeof repo.capabilities];
                      return (
                        <span
                          key={k}
                          className={`flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] ${
                            tem ? "bg-ok/15 text-ok" : "bg-raised text-faint line-through"
                          }`}
                        >
                          {tem && <Check className="size-3" />}
                          {rotulo}
                        </span>
                      );
                    })}
                  </div>
                  <p className="mt-1 text-faint">Tiradas do template de chat e da arquitetura do modelo.</p>
                </div>
                )}

                <div className="mt-4">
                  <div className="flex items-center gap-2">
                    <p className="flex-1 text-muted">Opções de download ({repo.files.length}) · do menor para o maior</p>
                    <span className="truncate text-faint" title={props.destino}>
                      para {props.destino}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-col gap-1">
                    {!repo.files.length && <p className="text-faint">Nenhum arquivo compatível neste repositório.</p>}
                    {repo.files.map((f) => (
                      <div key={f.path} className={`flex items-center gap-2 rounded-[9px] border px-2.5 py-1.5 ${cabe(f.size, props.hardware).tom || "border-line"}`}>
                        {f.quant && <span className={`${chip} shrink-0`}>{f.quant}</span>}
                        {f.tipo && (
                          <span className={`${chip} shrink-0`} title={f.tipo === "seedvr2" ? "Difusão: mais detalhe, minutos por imagem (precisa do ComfyUI)"
                            : f.tipo === "spandrel" ? "DAT, HAT, SwinIR e afins: segundos por imagem, mais fiel que o ESRGAN (precisa do ComfyUI)"
                            : f.tipo === "vae" ? "Peça do SeedVR2: vai junto do modelo" : "Rápido: segundos por imagem"}>
                            {f.tipo === "seedvr2" ? "SeedVR2 · ComfyUI" : f.tipo === "spandrel" ? "DAT/HAT · ComfyUI" : f.tipo === "vae" ? "VAE do SeedVR2" : "ESRGAN · sd-cli"}
                          </span>
                        )}
                        {f.papel && f.papel !== "modelo" && (
                          <span className="shrink-0 rounded-md bg-raised px-1.5 py-0.5 text-[11px] text-muted" title="Peça que o modelo pede à parte">
                            {({ vae: "VAE", t5xxl: "codificador", clip_vision: "CLIP Vision", high_noise_model: "HighNoise" } as Record<string, string>)[f.papel] ?? f.papel}
                          </span>
                        )}
                        <span className="min-w-0 flex-1 truncate text-fg" title={f.path}>
                          {f.path.split("/").pop()}
                          {f.shards > 1 ? ` · ${f.shards} partes` : ""}
                        </span>
                        <span className={`flex shrink-0 items-center gap-1.5 ${cabe(f.size, props.hardware).cor}`} title={cabe(f.size, props.hardware).dica}>
                          {cabe(f.size, props.hardware).rotulo && <span className="text-[11px]">{cabe(f.size, props.hardware).rotulo}</span>}
                          <span className="font-mono">{tamanho(f.size)}</span>
                        </span>
                        <button
                          className="shrink-0 rounded-[9px] bg-accent px-2.5 py-0.5 font-medium text-accent-fg hover:brightness-110 disabled:opacity-40"
                          disabled={baixados.includes(f.path)}
                          onClick={() => baixar(f)}
                        >
                          <Download className="mr-1 inline size-3" />
                          {baixados.includes(f.path) ? "baixando" : "Baixar"}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>

                {repo.readme && (
                  <div className="mt-4 border-t border-line pt-3">
                    <p className="text-muted">README</p>
                    <div className="mt-1 max-w-none text-fg">
                      <Markdown text={repo.readme} />
                    </div>
                  </div>
                )}
              </>
            )}
          </section>
        </div>
    </Modal>
  );
}

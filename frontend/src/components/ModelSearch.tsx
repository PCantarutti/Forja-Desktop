import { useEffect, useState } from "react";
import { api } from "../api";
import type { HfFile, HfModel, HfRepo } from "../types";
import { Markdown } from "./MessageView";
import { Check, Copy, Download, Search, X } from "./icons";

const chip = "rounded-md bg-raised px-1.5 py-0.5 text-[11px] text-muted";

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
  kind: "text" | "image";
  destino: string;
  onKind: (k: "text" | "image") => void;
  onDownload: (repo: string, file: string) => void;
  onClose: () => void;
  onError: (e: string) => void;
}) {
  const [q, setQ] = useState("");
  const [lista, setLista] = useState<HfModel[] | null>(null);
  const [buscando, setBuscando] = useState(false);
  const [sel, setSel] = useState("");
  const [repo, setRepo] = useState<HfRepo | null>(null);
  const [baixados, setBaixados] = useState<string[]>([]);

  async function buscar() {
    if (!q.trim()) return;
    setBuscando(true);
    setSel("");
    setRepo(null);
    try {
      const r = await api.get<{ models: HfModel[] }>(`/local/search?kind=${props.kind}&q=${encodeURIComponent(q)}`);
      setLista(r.models);
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setBuscando(false);
    }
  }

  useEffect(() => {
    if (!sel) return;
    setRepo(null);
    api
      .get<HfRepo>(`/local/repo?kind=${props.kind}&repo=${encodeURIComponent(sel)}`)
      .catch((e) => (props.onError(e.message), null))
      .then((r) => r && setRepo(r));
  }, [sel, props.kind]);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && props.onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, []);

  function baixar(f: HfFile) {
    props.onDownload(sel, f.path);
    setBaixados((b) => [...b, f.path]);
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-6" onClick={props.onClose}>
      <div
        className="flex h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-line bg-bg text-xs"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
          <Search className="size-4 shrink-0 text-muted" />
          <input
            autoFocus
            className="min-w-0 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-faint"
            placeholder={props.kind === "text" ? "Buscar modelos no Hugging Face…" : "Buscar modelos de imagem…"}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && buscar()}
          />
          <div className="flex shrink-0 gap-0.5 rounded-full border border-line p-0.5">
            {(["text", "image"] as const).map((k) => (
              <button
                key={k}
                onClick={() => {
                  props.onKind(k);
                  setLista(null);
                  setSel("");
                }}
                className={`rounded-full px-2 py-0.5 ${props.kind === k ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}
              >
                {k === "text" ? "chat" : "imagem"}
              </button>
            ))}
          </div>
          <button onClick={props.onClose} title="Fechar (Esc)" className="shrink-0 text-muted hover:text-fg">
            <X className="size-4" />
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          <aside className="w-80 shrink-0 overflow-y-auto border-r border-line">
            {buscando && <p className="p-3 text-faint">buscando…</p>}
            {!buscando && lista === null && (
              <p className="p-3 text-faint">Digite o que procura e aperte Enter. Ex.: qwen3, gemma, sdxl.</p>
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
                <span className="truncate text-fg">{m.id.split("/").pop()}</span>
                <span className="truncate text-faint">{m.author}</span>
                <span className="flex items-center gap-2 text-faint">
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
                  {repo.ctx_train > 0 && <span className={chip}>CTX {milhares(repo.ctx_train)}</span>}
                  {repo.license && <span className={chip}>{repo.license}</span>}
                  <span className="rounded-md bg-sky-900/50 px-1.5 py-0.5 text-[11px] text-sky-300">
                    {props.kind === "text" ? "GGUF" : "imagem"}
                  </span>
                </div>

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
                            tem ? "bg-emerald-900/40 text-emerald-300" : "bg-raised text-faint line-through"
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

                <div className="mt-4">
                  <div className="flex items-center gap-2">
                    <p className="flex-1 text-muted">Opções de download ({repo.files.length})</p>
                    <span className="truncate text-faint" title={props.destino}>
                      para {props.destino}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-col gap-1">
                    {!repo.files.length && <p className="text-faint">Nenhum arquivo compatível neste repositório.</p>}
                    {repo.files.map((f) => (
                      <div key={f.path} className="flex items-center gap-2 rounded-lg border border-line px-2 py-1.5">
                        {f.quant && <span className={`${chip} shrink-0`}>{f.quant}</span>}
                        <span className="min-w-0 flex-1 truncate text-fg" title={f.path}>
                          {f.path.split("/").pop()}
                          {f.shards > 1 ? ` · ${f.shards} partes` : ""}
                        </span>
                        <span className="shrink-0 text-faint">{tamanho(f.size)}</span>
                        <button
                          className="shrink-0 rounded-full bg-fg px-2.5 py-0.5 font-medium text-black hover:bg-white disabled:opacity-40"
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
      </div>
    </div>
  );
}

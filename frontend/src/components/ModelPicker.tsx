import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Check, Cube, Eye, Search } from "./icons";

export type CatalogEntry = { id: string; name: string; type: string; models: string[]; error: string };

/** Recorte do /api/local. Só o que este seletor usa; a versão web não tem essa rota e fica sem. */
type IaLocal = {
  models: { path: string; name: string; kind: string; ctx?: number; vision?: boolean }[];  // ctx: janela por requisição; vision: tem projetor (mmproj)
  server: { running: boolean; path?: string; alias?: string };
  image_busy: boolean;
};

const POP_W = 544;   // w-[34rem]
const POP_H = 320;   // h-80
const MARGEM = 8;

/** Onde o popover cabe. Posição FIXA calculada do botão, e não `absolute bottom-full`: o seletor mora
 *  no composer (embaixo), no cabeçalho (em cima) e dentro da doca do Maestro, que tem overflow — e em
 *  cada um desses lugares o popover relativo saía da tela ou era cortado pelo contêiner. */
function posicao(botao: HTMLElement | null) {
  if (!botao) return null;
  const r = botao.getBoundingClientRect();
  const w = Math.min(POP_W, window.innerWidth * 0.9);
  const cabeEmCima = r.top - MARGEM >= POP_H;
  const top = cabeEmCima ? r.top - MARGEM - POP_H : Math.min(r.bottom + MARGEM, window.innerHeight - POP_H - MARGEM);
  const left = Math.min(Math.max(r.right - w, MARGEM), window.innerWidth - w - MARGEM);
  return { top: Math.max(MARGEM, top), left, width: w };
}

const fmtK = (n: number) => (n >= 1024 ? `${Math.round(n / 1024)}k` : String(n));

/** Seletor de provedor + modelo (campo de mensagem, cabeçalho do Maestro, slots de Worker). */
export default function ModelPicker(props: {
  provider: string;
  model: string;
  onChange: (provider: string, model: string) => void;
  refreshKey?: number; // muda quando as configurações de provedores mudam
  // Cair sozinho no primeiro modelo quando o salvo sumiu. Certo no chat (sempre precisa de um
  // modelo); errado num slot opcional, onde "vazio" é uma escolha e trocar por conta própria grava
  // configuração que ninguém pediu.
  autoFallback?: boolean;
  // Carregar o GGUF ao escolher. Certo onde o modelo é usado já (chat, Maestro); errado num slot de
  // Worker, que só diz QUAL modelo usar quando houver tarefa — carregar ali trocaria o modelo da
  // própria Maestro, se ela roda local.
  loadLocal?: boolean;
  // Janela mínima (tokens por requisição) para um GGUF local ser escolhível. Abaixo disso o servidor
  // recusa o prompt no meio do trabalho, então o modelo aparece desabilitado com o motivo.
  minCtx?: number;
}) {
  const carrega = props.loadLocal ?? true;
  const botao = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(null);
  const [local, setLocal] = useState<IaLocal | null>(null);
  const [carregando, setCarregando] = useState("");   // caminho do .gguf subindo agora
  const [erro, setErro] = useState("");
  const [active, setActive] = useState(props.provider);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);

  const load = () => {
    // Os .gguf baixados não passam pelo /catalog: sem modelo carregado o llama-server nem está no ar.
    api.get<IaLocal>("/local").then(setLocal).catch(() => setLocal(null));
    return api
      .get<CatalogEntry[]>("/catalog")
      .then((c) => {
        setCatalog(c);
        // Modelo salvo sumiu (provedor removido ou modelo desmarcado): cai no primeiro disponível.
        const current = c.find((p) => p.id === props.provider);
        if ((props.autoFallback ?? true) && !current?.models.includes(props.model)) {
          const first = c.find((p) => p.models.length);
          if (first) props.onChange(first.id, first.models[0]);
        }
      })
      .catch(() => setCatalog([]));
  };

  useEffect(() => {
    load();
  }, [props.refreshKey]);

  useEffect(() => {
    if (!open) return;
    setActive(props.provider);
    setQ("");
    setErro("");
    load();
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const reposiciona = () => setPos(posicao(botao.current));
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    window.addEventListener("resize", reposiciona);
    window.addEventListener("scroll", reposiciona, true);  // true: rolagem de qualquer contêiner
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("resize", reposiciona);
      window.removeEventListener("scroll", reposiciona, true);
    };
  }, [open]);

  const prov = catalog?.find((p) => p.id === active);
  const providerName = catalog?.find((p) => p.id === props.provider)?.name ?? props.provider;
  const models = (prov?.models ?? []).filter((m) => m.toLowerCase().includes(q.toLowerCase()));

  // No provedor da IA local a lista sai dos arquivos baixados, não do /v1/models: assim ela existe
  // mesmo com nada carregado, que é justamente quando a pessoa precisa carregar alguma coisa.
  const ggufs = (local?.models ?? []).filter((m) => m.kind === "chat");
  const localAtivo = prov?.type === "llamacpp" && !!local;
  const daPasta = ggufs.filter((m) => m.name.toLowerCase().includes(q.toLowerCase()));

  const contar = (p: CatalogEntry) =>
    p.type === "llamacpp" && local ? ggufs.length : p.error ? "off" : p.models.length;

  async function carregar(caminho: string) {
    setCarregando(caminho);
    setErro("");
    try {
      const s = await api.post<{ alias?: string }>("/local/load", { path: caminho, params: {} });
      await load();
      if (s.alias) props.onChange(active, s.alias);
      setOpen(false);
    } catch (e: any) {
      setErro(e.message);
      load();   // a falha fica registrada no painel IA local; aqui só atualizamos o estado
    } finally {
      setCarregando("");
    }
  }

  async function descarregar() {
    setErro("");
    try {
      await api.post("/local/unload", {});
    } catch (e: any) {
      setErro(e.message);
    }
    load();
  }

  return (
    <div ref={box} className="relative ml-auto min-w-0">
      <button
        ref={botao}
        onClick={() => {
          if (!open) setPos(posicao(botao.current));
          setOpen(!open);
        }}
        title={`Trocar provedor e modelo — ${providerName} · ${props.model}`}
        className="flex max-w-[min(14rem,100%)] items-center gap-1.5 overflow-hidden rounded-lg bg-raised px-2.5 py-1 text-xs whitespace-nowrap text-muted hover:text-fg"
      >
        <Cube className="size-3.5 shrink-0" />
        <span className="hidden max-w-24 shrink truncate text-faint sm:inline-block">{providerName} ·</span>
        <span className="min-w-0 flex-1 truncate text-left">{props.model || "escolher modelo"}</span>
      </button>

      {open && (
        <div
          style={pos ?? undefined}
          className={`${pos ? "fixed" : "absolute right-0 bottom-full mb-2"} z-50 flex h-80 w-[34rem] max-w-[90vw] overflow-hidden rounded-xl border border-line bg-surface shadow-popover`}
        >
          <ul className="w-40 shrink-0 space-y-0.5 overflow-y-auto border-r border-line bg-side p-2 text-sm">
            {catalog === null && <li className="px-2 py-1 text-muted">carregando…</li>}
            {catalog?.map((p) => (
              <li key={p.id}>
                <button
                  onClick={() => setActive(p.id)}
                  className={`flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left ${
                    p.id === active ? "bg-raised text-fg" : "text-muted hover:bg-surface hover:text-fg"
                  }`}
                >
                  <span className="truncate">{p.name}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    {p.type === "llamacpp" && local?.server.running && (
                      <span className="size-1.5 rounded-full bg-emerald-400" title="tem modelo carregado" />
                    )}
                    <span className={`text-[10px] ${p.error && !(p.type === "llamacpp" && local) ? "text-red-300" : "text-faint"}`}>
                      {contar(p)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="flex min-w-0 flex-1 flex-col">
            <label className="flex items-center gap-2 border-b border-line px-3 py-2 text-sm text-muted">
              <Search className="size-3.5" />
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Buscar modelo"
                className="w-full bg-transparent text-fg placeholder:text-faint focus:outline-none"
              />
            </label>
            <ul className="flex-1 overflow-y-auto p-1.5 text-sm">
              {erro && <li className="px-2 py-2 text-xs text-red-300">{erro}</li>}
              {prov?.error && !localAtivo && <li className="px-2 py-2 text-xs text-red-300">{prov.error}</li>}

              {carrega && localAtivo && local.server.running && (
                <li className="flex items-center gap-2 px-2.5 py-1.5 text-xs text-muted">
                  <span className="size-1.5 shrink-0 rounded-full bg-emerald-400" />
                  <span className="min-w-0 flex-1 truncate">{local.server.alias} está carregado</span>
                  <button onClick={descarregar} className="shrink-0 text-faint hover:text-fg">
                    descarregar
                  </button>
                </li>
              )}
              {localAtivo && local.image_busy && (
                <li className="px-2 py-2 text-xs text-amber-300">
                  Uma imagem está sendo gerada; os dois disputam a mesma VRAM.
                </li>
              )}

              {localAtivo && daPasta.map((m) => {
                const carregado = local.server.path === m.path;
                const subindo = carregando === m.path;
                const escolhido = active === props.provider && m.name === props.model;
                const curta = !!props.minCtx && !!m.ctx && m.ctx < props.minCtx;
                const marcado = carrega ? carregado : escolhido;
                return (
                  <li key={m.path}>
                    <button
                      disabled={curta || !!carregando || (carrega && local.image_busy && !carregado)}
                      title={
                        curta
                          ? `Janela de ${m.ctx} tokens por requisição; o mínimo aqui é ${props.minCtx}. Aumente o contexto dele no painel IA local.`
                          : carrega && !carregado ? `Carregar ${m.name}` : m.path
                      }
                      onClick={() => {
                        if (!carrega) {
                          props.onChange(active, m.name);  // só registra a escolha; carrega quando houver tarefa
                          return setOpen(false);
                        }
                        if (!carregado) return void carregar(m.path);
                        props.onChange(active, local.server.alias || m.name);
                        setOpen(false);
                      }}
                      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left font-mono text-[13px] disabled:opacity-40 ${
                        marcado ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"
                      }`}
                    >
                      {carrega && carregado && <span className="size-1.5 shrink-0 rounded-full bg-emerald-400" />}
                      <span className="flex-1 truncate">{m.name}</span>
                      {m.vision && (
                        <span title="Visão: o projetor (mmproj) da pasta dele sobe junto" className="shrink-0 rounded border border-amber-500/50 p-0.5 text-amber-400">
                          <Eye className="size-3" />
                        </span>
                      )}
                      {m.ctx ? (
                        <span className={`shrink-0 font-sans text-[11px] ${curta ? "text-amber-300" : "text-faint"}`}>
                          {fmtK(m.ctx)}{curta ? ` · mín ${fmtK(props.minCtx!)}` : ""}
                        </span>
                      ) : null}
                      {carrega && (
                        <span className="shrink-0 font-sans text-[11px] text-faint">
                          {subindo ? "carregando…" : carregado ? "carregado" : "carregar"}
                        </span>
                      )}
                      {marcado && <Check className="size-3.5 shrink-0" />}
                    </button>
                  </li>
                );
              })}
              {localAtivo && !daPasta.length && (
                <li className="px-2 py-2 text-xs text-muted">
                  Nenhum .gguf{q ? " com esse nome" : ""} nas pastas de modelos. Baixe um no painel IA local.
                </li>
              )}

              {!localAtivo && models.map((m) => {
                const selected = active === props.provider && m === props.model;
                return (
                  <li key={m}>
                    <button
                      onClick={() => {
                        props.onChange(active, m);
                        setOpen(false);
                      }}
                      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left font-mono text-[13px] ${
                        selected ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"
                      }`}
                    >
                      <span className="flex-1 truncate">{m}</span>
                      {selected && <Check className="size-3.5 shrink-0" />}
                    </button>
                  </li>
                );
              })}
              {prov && !localAtivo && !prov.error && !models.length && (
                <li className="px-2 py-2 text-xs text-muted">
                  Nenhum modelo{q ? " com esse nome" : ""}. Escolha quais aparecem em Configurações › Provedores.
                </li>
              )}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

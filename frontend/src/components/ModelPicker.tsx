import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Check, Cube, Search } from "./icons";

export type CatalogEntry = { id: string; name: string; type: string; models: string[]; error: string };

/** Recorte do /api/local. Só o que este seletor usa; a versão web não tem essa rota e fica sem. */
type IaLocal = {
  models: { path: string; name: string; kind: string }[];
  server: { running: boolean; path?: string; alias?: string };
  image_busy: boolean;
};

/** Seletor de provedor + modelo no campo de mensagem (popover abrindo para cima). */
export default function ModelPicker(props: {
  provider: string;
  model: string;
  onChange: (provider: string, model: string) => void;
  refreshKey?: number; // muda quando as configurações de provedores mudam
}) {
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
        if (!current?.models.includes(props.model)) {
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
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
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
        onClick={() => setOpen(!open)}
        title={`Trocar provedor e modelo — ${providerName} · ${props.model}`}
        className="flex max-w-56 items-center gap-1.5 overflow-hidden rounded-lg bg-raised px-2.5 py-1 text-xs whitespace-nowrap text-muted hover:text-fg"
      >
        <Cube className="size-3.5 shrink-0" />
        <span className="hidden max-w-24 shrink truncate text-faint sm:inline-block">{providerName} ·</span>
        <span className="min-w-0 flex-1 truncate text-left">{props.model || "escolher modelo"}</span>
      </button>

      {open && (
        <div className="absolute right-0 bottom-full z-40 mb-2 flex h-80 w-[34rem] max-w-[90vw] overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl shadow-black/50">
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

              {localAtivo && local.server.running && (
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
                return (
                  <li key={m.path}>
                    <button
                      disabled={!!carregando || (local.image_busy && !carregado)}
                      title={carregado ? m.path : `Carregar ${m.name}`}
                      onClick={() => {
                        if (!carregado) return void carregar(m.path);
                        props.onChange(active, local.server.alias || m.name);
                        setOpen(false);
                      }}
                      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left font-mono text-[13px] disabled:opacity-40 ${
                        carregado ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"
                      }`}
                    >
                      {carregado && <span className="size-1.5 shrink-0 rounded-full bg-emerald-400" />}
                      <span className="flex-1 truncate">{m.name}</span>
                      <span className="shrink-0 font-sans text-[11px] text-faint">
                        {subindo ? "carregando…" : carregado ? "carregado" : "carregar"}
                      </span>
                      {carregado && <Check className="size-3.5 shrink-0" />}
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

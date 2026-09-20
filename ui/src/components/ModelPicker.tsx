import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Check, Cube, Search } from "./icons";

export type CatalogEntry = { id: string; name: string; type: string; models: string[]; error: string };

/** Seletor de provedor + modelo no campo de mensagem (popover abrindo para cima). */
export default function ModelPicker(props: {
  provider: string;
  model: string;
  onChange: (provider: string, model: string) => void;
  refreshKey?: number; // muda quando as configurações de provedores mudam
}) {
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(null);
  const [active, setActive] = useState(props.provider);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);

  const load = () =>
    api
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

  useEffect(() => {
    load();
  }, [props.refreshKey]);

  useEffect(() => {
    if (!open) return;
    setActive(props.provider);
    setQ("");
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

  return (
    <div ref={box} className="relative ml-auto">
      <button
        onClick={() => setOpen(!open)}
        title="Trocar provedor e modelo"
        className="flex max-w-72 items-center gap-1.5 rounded-lg bg-raised px-2.5 py-1 text-xs text-muted hover:text-fg"
      >
        <Cube className="size-3.5 shrink-0" />
        <span className="hidden text-faint sm:inline">{providerName} ·</span>
        <span className="truncate">{props.model || "escolher modelo"}</span>
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
                  <span className={`text-[10px] ${p.error ? "text-red-300" : "text-faint"}`}>{p.error ? "off" : p.models.length}</span>
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
              {prov?.error && <li className="px-2 py-2 text-xs text-red-300">{prov.error}</li>}
              {models.map((m) => {
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
              {prov && !prov.error && !models.length && (
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

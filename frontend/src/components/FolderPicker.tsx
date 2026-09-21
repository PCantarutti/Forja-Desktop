import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal } from "./Modal";
import { ChevronDown, Folder, X } from "./icons";

type Listing = { path: string; parent: string | null; dirs: { name: string; path: string }[] };
type Roots = { drives: { name: string; path: string }[]; recent: string[]; default: string };

/** Nome curto da pasta (último segmento), como o chip do Claude Desktop. */
export function folderName(path: string | null | undefined, fallback = "workspace"): string {
  if (!path) return fallback;
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** Seletor de pasta de trabalho: discos montados, pastas recentes e navegação por subpastas. */
export default function FolderPicker(props: {
  current: string | null;
  onPick: (path: string | null) => void;
  onClose: () => void;
  nativeError?: string; // por que o seletor nativo não abriu (ajudante desligado etc.)
  onNative?: () => void; // tentar o seletor nativo de novo
}) {
  const [roots, setRoots] = useState<Roots | null>(null);
  const [list, setList] = useState<Listing | null>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState("");

  const open = (path: string) => {
    setError("");
    api
      .get<Listing>(`/fs/list?path=${encodeURIComponent(path)}`)
      .then((l) => {
        setList(l);
        setTyped(l.path);
      })
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    api.get<Roots>("/fs/roots").then((r) => {
      setRoots(r);
      const start = props.current ?? r.default ?? r.recent[0] ?? r.drives[0]?.path;
      if (start) open(start);
    });
  }, []);

  const crumbs = list ? list.path.split("/").filter(Boolean) : [];

  return (
    <Modal
      onClose={props.onClose}
      label="Escolher pasta de trabalho"
      className="flex h-[70vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-line bg-bg"
    >
        <header className="flex items-center gap-3 border-b border-line px-5 py-3">
          <Folder className="size-4 text-muted" />
          <h2 className="flex-1 text-sm font-medium">Pasta de trabalho</h2>
          {props.onNative && (
            <button onClick={props.onNative} className="rounded-full border border-line px-3 py-1 text-xs text-fg hover:bg-raised">
              Abrir seletor do sistema
            </button>
          )}
          <button onClick={props.onClose} className="rounded-md p-1 text-faint hover:bg-raised hover:text-fg" title="Fechar">
            <X />
          </button>
        </header>

        {props.nativeError && (
          <div className="border-b border-line bg-surface px-5 py-2.5 text-xs text-muted">
            <span className="text-amber-200">Seletor do sistema indisponível:</span> {props.nativeError} Escolha
            a pasta por aqui.
          </div>
        )}
        <div className="flex min-h-0 flex-1">
          <nav className="w-52 shrink-0 space-y-4 overflow-y-auto border-r border-line bg-side p-3 text-sm">
            <section>
              <h3 className="mb-1 px-2 text-[11px] tracking-wider text-faint uppercase">Discos</h3>
              {roots?.drives.map((d) => (
                <button key={d.path} onClick={() => open(d.path)} className="w-full rounded-lg px-2 py-1.5 text-left text-muted hover:bg-surface hover:text-fg">
                  {d.name}
                </button>
              ))}
              {roots && !roots.drives.length && <p className="px-2 text-xs text-muted">Nenhum disco montado (veja HOST_MOUNTS no README).</p>}
            </section>
            {!!roots?.recent.length && (
              <section>
                <h3 className="mb-1 px-2 text-[11px] tracking-wider text-faint uppercase">Recentes</h3>
                {roots.recent.map((r) => (
                  <button
                    key={r}
                    onClick={() => open(r)}
                    title={r}
                    className="w-full truncate rounded-lg px-2 py-1.5 text-left text-muted hover:bg-surface hover:text-fg"
                  >
                    {folderName(r)}
                  </button>
                ))}
              </section>
            )}
            <section>
              <h3 className="mb-1 px-2 text-[11px] tracking-wider text-faint uppercase">Padrão</h3>
              <button
                onClick={() => props.onPick(null)}
                title={roots?.default}
                className="w-full truncate rounded-lg px-2 py-1.5 text-left text-muted hover:bg-surface hover:text-fg"
              >
                {folderName(roots?.default)}
              </button>
            </section>
          </nav>

          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex flex-wrap items-center gap-1 border-b border-line px-4 py-2 text-sm">
              {list &&
                crumbs.map((c, i) => {
                  const path = crumbs.slice(0, i + 1).join("/") + (i === 0 ? "/" : "");
                  return (
                    <span key={path} className="flex items-center gap-1">
                      {i > 0 && <span className="text-faint">/</span>}
                      <button onClick={() => open(path)} className="rounded px-1 text-muted hover:bg-raised hover:text-fg">
                        {c}
                      </button>
                    </span>
                  );
                })}
            </div>
            <div className="flex-1 overflow-y-auto p-2">
              {list?.parent && (
                <button onClick={() => open(list.parent!)} className="flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left text-sm text-muted hover:bg-surface">
                  <ChevronDown className="size-3.5 rotate-90" /> ..
                </button>
              )}
              {list?.dirs.map((d) => (
                <button
                  key={d.path}
                  onClick={() => open(d.path)}
                  onDoubleClick={() => props.onPick(d.path)}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left text-sm text-fg hover:bg-surface"
                >
                  <Folder className="size-4 shrink-0 text-faint" /> <span className="truncate">{d.name}</span>
                </button>
              ))}
              {list && !list.dirs.length && <p className="px-3 py-2 text-sm text-muted">Sem subpastas.</p>}
            </div>
            <footer className="flex items-center gap-2 border-t border-line p-3">
              <input
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && open(typed)}
                placeholder="C:/Users/voce/Projetos/app"
                className="min-w-0 flex-1 rounded-lg border border-line bg-raised px-3 py-1.5 font-mono text-sm text-fg focus:outline-none"
              />
              <button
                disabled={!list}
                onClick={() => list && props.onPick(list.path)}
                className="rounded-full bg-fg px-4 py-1.5 text-sm font-medium text-black hover:bg-white disabled:opacity-40"
              >
                Usar esta pasta
              </button>
            </footer>
            {error && <p className="px-4 pb-3 text-sm text-red-300">{error}</p>}
          </div>
        </div>
    </Modal>
  );
}

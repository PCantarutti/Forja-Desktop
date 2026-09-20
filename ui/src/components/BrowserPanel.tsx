import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { BrowserState } from "../types";
import { ArrowLeft, ArrowRight, Refresh, X } from "./icons";

const EMPTY: BrowserState = { open: false, url: "", title: "", width: 1280, height: 800, scale: 1, tabs: [] };
const KEYS: Record<string, string> = { " ": "Space" };

/**
 * Aba Navegador: espelho ao vivo da sessão da conversa atual (`conv`), com abas e interação do usuário.
 * Clique, teclado, roda, barra de URL, abas e upload vão para o backend, que repassa ao Chromium.
 * A sessão continua viva no backend quando esta aba não está montada; só o stream de frames fecha.
 */
export default function BrowserPanel(props: { conv: string; onState: (s: BrowserState) => void }) {
  const { conv } = props;
  const q = `?conv=${encodeURIComponent(conv)}`;
  const [state, setState] = useState<BrowserState>(EMPTY);
  const [frame, setFrame] = useState<string | null>(null);
  const [urlInput, setUrlInput] = useState("");
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const editing = useRef(false);
  const img = useRef<HTMLImageElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const onState = useRef(props.onState);
  onState.current = props.onState;
  const wanted = useRef<{ width: number; height: number } | null>(null); // tamanho do painel = viewport remoto
  // Movimento do mouse: no máximo um POST em voo; o último movimento pendente vence (coalescido).
  const moveInFlight = useRef(false);
  const movePending = useRef<{ x: number; y: number } | null>(null);

  /** A página do Chromium tem exatamente o tamanho da área visível (como a janela do Claude Desktop). */
  function syncViewport() {
    const el = box.current;
    if (!el) return;
    const width = Math.floor(el.clientWidth);
    const height = Math.floor(el.clientHeight);
    if (width < 320 || height < 240) return;
    if (wanted.current?.width === width && wanted.current?.height === height) return;
    wanted.current = { width, height };
    api.post(`/browser/viewport${q}`, { width, height }).catch(() => {});
  }

  // Troca de conversa: zera o espelho e assina a sessão dela.
  useEffect(() => {
    setState(EMPTY);
    setFrame(null);
    setUrlInput("");
    setError("");
    wanted.current = null;
    const es = new EventSource(`/api/browser/stream${q}`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      if (ev.type === "frame") {
        setFrame(`data:${ev.mime ?? "image/jpeg"};base64,${ev.data}`);
        if (!editing.current) setUrlInput(ev.url);
      } else if (ev.type === "state") {
        setState(ev);
        onState.current(ev);
        if (!editing.current) setUrlInput(ev.url);
        if (!ev.open) setFrame(null);
        // Sessão (re)aberta com outro tamanho: manda o tamanho do painel.
        if (ev.open && (!wanted.current || ev.width !== wanted.current.width || ev.height !== wanted.current.height)) {
          wanted.current = null;
          syncViewport();
        }
      }
    };
    return () => es.close();
  }, [conv]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    let t: ReturnType<typeof setTimeout>;
    const ro = new ResizeObserver(() => {
      clearTimeout(t);
      t = setTimeout(syncViewport, 200); // espera o arraste da borda terminar
    });
    ro.observe(el);
    syncViewport();
    return () => {
      ro.disconnect();
      clearTimeout(t);
    };
  }, [conv]);

  // React registra "wheel" como passive: preventDefault só funciona com listener nativo.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const c = coords(e.clientX, e.clientY);
      if (c) send({ type: "wheel", ...c, delta_x: e.deltaX, delta_y: e.deltaY });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  async function send(body: Record<string, unknown>) {
    try {
      await api.post(`/browser/input${q}`, body);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function sendMove(c: { x: number; y: number }) {
    movePending.current = c;
    if (moveInFlight.current) return;
    moveInFlight.current = true;
    try {
      while (movePending.current) {
        const next = movePending.current;
        movePending.current = null;
        await api.post(`/browser/input${q}`, { type: "move", ...next }).catch(() => {});
      }
    } finally {
      moveInFlight.current = false;
    }
  }

  async function call(path: string, body?: unknown) {
    try {
      const s = await api.post<BrowserState>(`/browser/${path}${q}`, body);
      setState(s);
      onState.current(s);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }

  /** Posição do mouse sobre a imagem → coordenadas de CSS do viewport remoto (object-contain, canto superior esquerdo). */
  function coords(clientX: number, clientY: number) {
    const r = img.current?.getBoundingClientRect();
    if (!r || !r.width || !r.height) return null;
    const scale = Math.min(r.width / state.width, r.height / state.height); // área realmente ocupada pela imagem
    const x = (clientX - r.left) / scale;
    const y = (clientY - r.top) / scale;
    if (x < 0 || y < 0 || x > state.width || y > state.height) return null;
    return { x: Math.round(x), y: Math.round(y) };
  }

  function onKey(e: React.KeyboardEvent) {
    e.preventDefault();
    const key = KEYS[e.key] ?? e.key;
    // Caractere simples já vem com o shift aplicado ("A", "!"); só modificadores de comando entram no nome.
    const mods = [e.ctrlKey && "Control", e.altKey && "Alt", e.metaKey && "Meta", key.length > 1 && e.shiftKey && "Shift"];
    send({ type: "key", key: [...mods.filter(Boolean), key].join("+") });
  }

  function submitUrl() {
    const raw = urlInput.trim();
    if (!raw) return;
    editing.current = false;
    call("navigate", { url: /^https?:\/\//i.test(raw) ? raw : `http://${raw}` });
  }

  async function uploadChosen(file: File | null) {
    const form = new FormData();
    if (file) form.append("file", file);
    try {
      const r = await fetch(`/api/browser/upload${q}`, { method: "POST", body: file ? form : undefined });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }

  const btn = "grid size-7 place-items-center rounded-md text-muted hover:bg-raised hover:text-fg disabled:opacity-30";
  const tabs = state.tabs ?? [];

  return (
    <div className="flex h-full flex-col text-xs">
      {/* Abas da sessão */}
      <div className="flex items-center gap-0.5 overflow-x-auto border-b border-line px-1 pt-1">
        {tabs.map((t) => (
          <div
            key={t.index}
            title={`${t.title || "(sem título)"}\n${t.url}`}
            onClick={() => !t.active && call("tabs", { action: "switch", index: t.index })}
            className={`group flex max-w-44 shrink-0 cursor-pointer items-center gap-1 rounded-t-md border border-b-0 px-2 py-1 ${
              t.active ? "border-line bg-surface text-fg" : "border-transparent text-muted hover:bg-raised/60 hover:text-fg"
            }`}
          >
            <span className="truncate">{t.title || t.url.replace(/^https?:\/\//, "") || "nova aba"}</span>
            <button
              title="Fechar aba"
              onClick={(e) => {
                e.stopPropagation();
                call("tabs", { action: "close", index: t.index });
              }}
              className="grid size-4 shrink-0 place-items-center rounded text-faint opacity-0 group-hover:opacity-100 hover:bg-raised hover:text-fg"
            >
              <X className="size-3" />
            </button>
          </div>
        ))}
        <button
          title="Nova aba"
          onClick={() => call("tabs", { action: "new" })}
          className="grid size-6 shrink-0 place-items-center rounded-md text-muted hover:bg-raised hover:text-fg"
        >
          +
        </button>
      </div>

      <div className="flex items-center gap-1 border-b border-line px-2 py-1.5">
        <button className={btn} title="Voltar" disabled={!state.open} onClick={() => call("navigate", { action: "back" })}>
          <ArrowLeft className="size-4" />
        </button>
        <button className={btn} title="Avançar" disabled={!state.open} onClick={() => call("navigate", { action: "forward" })}>
          <ArrowRight className="size-4" />
        </button>
        <button className={btn} title="Recarregar" disabled={!state.open} onClick={() => call("navigate", { action: "reload" })}>
          <Refresh className="size-4" />
        </button>
        <input
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          onFocus={() => (editing.current = true)}
          onBlur={() => (editing.current = false)}
          onKeyDown={(e) => e.key === "Enter" && submitUrl()}
          placeholder="http://localhost:5173"
          spellCheck={false}
          className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1 font-mono text-fg placeholder:text-faint focus:border-[#454545] focus:outline-none"
        />
        <button className={btn} title="Fechar sessão do navegador desta conversa" disabled={!state.open} onClick={() => call("close")}>
          <X className="size-4" />
        </button>
      </div>
      {(state.title || error) && (
        <div className={`truncate border-b border-line px-3 py-1 ${error ? "text-red-300" : "text-muted"}`} title={error || state.title}>
          {error || state.title}
        </div>
      )}
      {state.file_chooser && (
        <div className="flex items-center gap-2 border-b border-amber-500/40 bg-amber-950/30 px-3 py-1.5 text-amber-100">
          <span className="flex-1">A página pediu um arquivo.</span>
          <input ref={fileInput} type="file" hidden onChange={(e) => uploadChosen(e.target.files?.[0] ?? null)} />
          <button onClick={() => fileInput.current?.click()} className="rounded-full bg-fg px-3 py-0.5 font-medium text-black hover:bg-white">
            Escolher arquivo
          </button>
          <button onClick={() => uploadChosen(null)} className="rounded-full border border-line px-3 py-0.5 text-fg hover:bg-raised">
            Cancelar
          </button>
        </div>
      )}

      <div
        ref={box}
        tabIndex={0}
        onKeyDown={onKey}
        onPaste={(e) => {
          e.preventDefault();
          const text = e.clipboardData.getData("text");
          if (text) send({ type: "text", text });
        }}
        className="min-h-0 flex-1 overflow-hidden bg-black outline-none focus:ring-1 focus:ring-sky-500/60 focus:ring-inset"
      >
        {frame ? (
          <img
            ref={img}
            src={frame}
            alt="Tela do navegador"
            draggable={false}
            className="block h-full w-full cursor-default select-none object-contain object-left-top"
            onClick={(e) => {
              box.current?.focus();
              const c = coords(e.clientX, e.clientY);
              if (c) send({ type: "click", ...c, button: "left" });
            }}
            onContextMenu={(e) => {
              e.preventDefault();
              const c = coords(e.clientX, e.clientY);
              if (c) send({ type: "click", ...c, button: "right" });
            }}
            onDoubleClick={(e) => {
              const c = coords(e.clientX, e.clientY);
              if (c) send({ type: "dblclick", ...c });
            }}
            onMouseMove={(e) => {
              const c = coords(e.clientX, e.clientY);
              if (c) sendMove(c);
            }}
          />
        ) : (
          <div className="p-4 text-muted">
            {state.open
              ? "Aguardando a primeira tela…"
              : connected
                ? conv === "0"
                  ? "Nenhuma sessão neste rascunho. Digite uma URL acima; cada conversa tem o próprio navegador."
                  : "Esta conversa não tem navegador aberto. Digite uma URL acima ou peça ao agente para abrir uma página."
                : "Conectando ao navegador…"}
          </div>
        )}
      </div>
      <div className="border-t border-line px-3 py-1 text-[11px] text-faint">
        {state.open
          ? `viewport ${state.width}×${state.height} (segue o painel) · render ${state.scale ?? 1}x · ${tabs.length} aba${tabs.length === 1 ? "" : "s"} · clique na tela para focar e digitar`
          : "sessão fechada"}
      </div>
    </div>
  );
}

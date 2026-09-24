import { useEffect, useRef, useState } from "react";
import { api, streamSSE } from "../api";
import type { BrowserState, ServerInfo } from "../types";
import { ArrowLeft, ArrowRight, Refresh, X } from "./icons";

const EMPTY: BrowserState = { open: false, url: "", title: "", width: 1280, height: 800, scale: 1, tabs: [] };
const KEYS: Record<string, string> = { " ": "Space" };

/**
 * Aba Navegador da sessão da conversa atual (`conv`), com abas e barra de URL.
 *
 * - Forja Desktop (`state.native`): a página é uma WebContentsView do Electron desenhada por cima da área
 *   escura deste painel; a gente só informa ao main.js onde a área está. Interação é direta, sem espelho.
 * - Web/Docker: espelho ao vivo (frames em SSE); clique, teclado e roda vão para o backend, que repassa ao Chromium.
 *
 * A sessão continua viva no backend quando esta aba não está montada; só o stream fecha.
 */
/** Aba nova (a URL-marcador da view nativa, ou about:blank): mostra a tela inicial do painel no lugar. */
const emBranco = (url?: string) => !url || url.startsWith("about:blank");

export default function BrowserPanel(props: { conv: string; onState: (s: BrowserState) => void }) {
  const { conv } = props;
  const q = `?conv=${encodeURIComponent(conv)}`;
  const [state, setState] = useState<BrowserState>(EMPTY);
  const [frame, setFrame] = useState<string | null>(null);
  const [urlInput, setUrlInput] = useState("");
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const native = !!state.native && !!window.forja?.browser;
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
  // Roda: idem, somando os deltas que chegaram enquanto o POST anterior estava em voo.
  const wheelInFlight = useRef(false);
  const wheelPending = useRef<{ x: number; y: number; delta_x: number; delta_y: number } | null>(null);

  /** A página do Chromium tem exatamente o tamanho da área visível (como a janela do Claude Desktop). */
  function syncViewport() {
    const el = box.current;
    if (!el) return;
    const width = Math.floor(el.clientWidth);
    const height = Math.floor(el.clientHeight);
    if (width < 320 || height < 240) return;
    if (wanted.current?.width === width && wanted.current?.height === height) return;
    wanted.current = { width, height };
    api.post(`/browser/viewport${q}`, { width, height, dpr: window.devicePixelRatio || 1 }).catch(() => {});
  }

  // Troca de conversa: zera o espelho e assina a sessão dela.
  useEffect(() => {
    setState(EMPTY);
    setFrame(null);
    setUrlInput("");
    setError("");
    wanted.current = null;
    // streamSSE, e não EventSource: o EventSource não manda header nenhum, e o backend do Desktop
    // exige X-Forja-Token em /api. O stream de frames levava 403 e o painel ficava eternamente em
    // "Aguardando a primeira tela…" — o estado ainda chegava, porque vem dos POST, que passam pelo
    // api.ts. Reconectar também passa a ser nosso: o fetch não tenta de novo sozinho, e backend
    // reiniciando é rotina.
    let parar: AbortController | null = null;
    let retentar: ReturnType<typeof setTimeout> | undefined;
    let vivo = true;

    const aplicar = (ev: any) => {
      setConnected(true);
      if (ev.type === "frame") {
        setFrame(`data:${ev.mime ?? "image/jpeg"};base64,${ev.data}`);
        if (!editing.current) setUrlInput(emBranco(ev.url) ? "" : ev.url);
      } else if (ev.type === "state") {
        setState(ev);
        onState.current(ev);
        if (!editing.current) setUrlInput(emBranco(ev.url) ? "" : ev.url);
        if (!ev.open) setFrame(null);
        // Sessão (re)aberta com outro tamanho: manda o tamanho do painel.
        if (ev.open && (!wanted.current || ev.width !== wanted.current.width || ev.height !== wanted.current.height)) {
          wanted.current = null;
          syncViewport();
        }
      }
    };

    const conectar = async () => {
      if (!vivo) return;
      parar = new AbortController();
      try {
        await streamSSE(`/browser/stream${q}`, { method: "GET", signal: parar.signal }, aplicar);
      } catch {
        /* caiu, ou foi recusado: a reconexão abaixo cuida */
      }
      if (!vivo) return;
      setConnected(false);
      clearTimeout(retentar);
      retentar = setTimeout(conectar, 1500);
    };

    conectar();
    return () => {
      vivo = false;
      clearTimeout(retentar);
      parar?.abort();
    };
  }, [conv, q]);

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

  const abaBranca = state.open && emBranco(state.url);
  const branca = useRef(abaBranca);
  branca.current = abaBranca;

  // Nativo: diz ao Electron onde a view deve ficar. Some quando o painel encolhe ou algo cobre a área.
  useEffect(() => {
    const el = box.current;
    const bridge = window.forja?.browser;
    if (!native || !el || !bridge) return;
    const report = () => {
      const r = el.getBoundingClientRect();
      if (branca.current) return bridge.view({ key: conv, bounds: null }); // aba nova: a lista de servidores aparece
      // ponytail: elementFromPoint no centro detecta modal/lightbox por cima do painel; a view nativa não
      // está no DOM, então uma cobertura só pode ser da própria interface.
      const mid = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      const covered = !!mid && mid !== el && !el.contains(mid);
      const hidden = covered || r.width < 50 || r.height < 50;
      bridge.view({ key: conv, bounds: hidden ? null : { x: r.left, y: r.top, width: r.width, height: r.height } });
    };
    const ro = new ResizeObserver(report);
    ro.observe(el);
    const iv = setInterval(report, 250);
    report();
    return () => {
      ro.disconnect();
      clearInterval(iv);
      bridge.view({ key: conv, bounds: null });
    };
  }, [native, conv]);

  // React registra "wheel" como passive: preventDefault só funciona com listener nativo.
  // Com as dependências certas: sem elas o listener era removido e registrado de novo a cada
  // quadro do espelho, porque cada frame re-renderiza este componente.
  useEffect(() => {
    const el = box.current;
    if (!el || native) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const c = coords(e.clientX, e.clientY);
      if (c) sendWheel({ ...c, delta_x: e.deltaX, delta_y: e.deltaY });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [native, q]);

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

  async function sendWheel(w: { x: number; y: number; delta_x: number; delta_y: number }) {
    const p = wheelPending.current;
    wheelPending.current = p ? { ...w, delta_x: p.delta_x + w.delta_x, delta_y: p.delta_y + w.delta_y } : w;
    if (wheelInFlight.current) return;
    wheelInFlight.current = true;
    try {
      while (wheelPending.current) {
        const next = wheelPending.current;
        wheelPending.current = null;
        await api.post(`/browser/input${q}`, { type: "wheel", ...next }).catch(() => {});
      }
    } finally {
      wheelInFlight.current = false;
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
            <span className="truncate">{emBranco(t.url) ? "Nova aba" : t.title || t.url.replace(/^https?:\/\//, "")}</span>
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
        tabIndex={native ? -1 : 0}
        onKeyDown={native ? undefined : onKey}
        onPaste={
          native
            ? undefined
            : (e) => {
                e.preventDefault();
                const text = e.clipboardData.getData("text");
                if (text) send({ type: "text", text });
              }
        }
        className="min-h-0 flex-1 overflow-hidden bg-black outline-none focus:ring-1 focus:ring-sky-500/60 focus:ring-inset"
      >
        {native && state.open && !abaBranca ? null /* a view nativa cobre esta área */ : frame && !abaBranca ? (
          <img
            ref={img}
            src={frame}
            alt="Tela do navegador"
            decoding="async"
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
            {abaBranca
              ? "Aba nova. Digite uma URL acima ou abra um servidor abaixo."
              : state.open
              ? "Aguardando a primeira tela…"
              : connected
                ? conv === "0"
                  ? "Nenhuma sessão neste rascunho. Digite uma URL acima; cada conversa tem o próprio navegador."
                  : "Esta conversa não tem navegador aberto. Digite uma URL acima ou peça ao agente para abrir uma página."
                : "Conectando ao navegador…"}
            <ServidoresRodando onAbrir={(url) => call("navigate", { url })} />
          </div>
        )}
      </div>
      <div className="border-t border-line px-3 py-1 text-[11px] text-faint">
        {!state.open
          ? "sessão fechada"
          : native
            ? `navegador nativo · ${state.width}×${state.height} · ${tabs.length} aba${tabs.length === 1 ? "" : "s"}`
            : `viewport ${state.width}×${state.height} (segue o painel) · render ${state.scale ?? 1}x · ${tabs.length} aba${tabs.length === 1 ? "" : "s"} · clique na tela para focar e digitar`}
      </div>
    </div>
  );
}


/** `& "C:\...\python.exe" -m http.server` → `python -m http.server`: o caminho inteiro fica no title. */
const comandoCurto = (cmd: string) =>
  cmd.replace(/^&\s*/, "").replace(/"[^"]*[\\/]([^"\\/]+?)(?:\.exe)?"/gi, "$1");

/** Servidores de desenvolvimento no ar (os do serve_start, de qualquer conversa) para abrir com um
 *  clique, sem decorar a porta. Só os que já anunciaram o endereço no log. */
function ServidoresRodando(props: { onAbrir: (url: string) => void }) {
  const [servidores, setServidores] = useState<ServerInfo[]>([]);
  useEffect(() => {
    let vivo = true;
    const ler = () =>
      api.get<{ servers: ServerInfo[] }>("/servers")
        .then((r) => vivo && setServidores(r.servers.filter((x) => x.alive && x.url)))
        .catch(() => {});
    ler();
    const t = setInterval(ler, 4000);
    return () => {
      vivo = false;
      clearInterval(t);
    };
  }, []);
  if (!servidores.length) return null;
  return (
    <div className="mx-auto mt-6 flex max-w-lg flex-col gap-2">
      <p className="text-center text-xs text-faint">Servidores rodando: clique para abrir aqui.</p>
      {servidores.map((x) => (
        <div key={x.name} className="flex items-center gap-3 rounded-xl border border-line bg-panel px-3 py-2">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] text-faint">
              {x.url!.replace(/^https?:\/\//, "")}
              {x.cwd ? ` · ${x.cwd.split(/[\\/]/).filter(Boolean).pop()}` : ""}
            </div>
            <div className="truncate font-mono text-xs text-fg" title={x.command}>{comandoCurto(x.command)}</div>
          </div>
          <button
            className="shrink-0 rounded-lg bg-raised px-3 py-1 text-xs text-fg hover:bg-line"
            onClick={() => props.onAbrir(x.url!)}
          >
            Abrir
          </button>
        </div>
      ))}
    </div>
  );
}

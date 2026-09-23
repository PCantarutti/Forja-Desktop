import { useEffect, useRef, useState } from "react";
import { api } from "../api";

type Session = { id: string; where: string; shell: string; cwd: string; buf: string; cursor: number; history: string[]; alive: boolean };
// Uma sessão por conversa, viva enquanto o app estiver aberto (trocar de aba não fecha o shell).
const SESSIONS: Record<string, Session> = {};
// Comando pedido de fora (o "Testar" de um bloco de código) antes de o shell desta conversa abrir.
const PENDENTES: Record<string, string> = {};

/** Roda um comando no terminal da conversa, como se a pessoa tivesse digitado. Sem shell aberto
 * ainda, o comando espera e roda assim que a aba Terminal abrir um. */
export async function executarNoTerminal(conv: string, text: string) {
  const s = SESSIONS[conv];
  if (!s) {
    PENDENTES[conv] = text;
    return;
  }
  s.history = [text, ...s.history.filter((h) => h !== text)].slice(0, 100);
  s.buf = (s.buf + `\n$ ${text}\n`).slice(-200_000);
  await api.post(`/term/${s.id}/input`, { text });
}

/**
 * Aba Terminal: seu shell na pasta da conversa (PowerShell no Windows, bash no Linux/macOS).
 * Sem PTY: comandos comuns funcionam; programas de tela cheia, não.
 */
export default function TerminalPanel(props: { conv: string }) {
  const { conv } = props;
  const [sess, setSess] = useState<Session | null>(SESSIONS[conv] ?? null);
  const [out, setOut] = useState(SESSIONS[conv]?.buf ?? "");
  const [cmd, setCmd] = useState("");
  const [error, setError] = useState("");
  const [hist, setHist] = useState(-1);
  const pre = useRef<HTMLPreElement>(null);
  const alive = useRef(true);

  async function start(fresh = false) {
    if (fresh && SESSIONS[conv]) {
      api.del(`/term/${SESSIONS[conv].id}`).catch(() => {});
      delete SESSIONS[conv];
    }
    if (SESSIONS[conv]) {
      setSess(SESSIONS[conv]);
      setOut(SESSIONS[conv].buf);
      return;
    }
    try {
      const r = await api.post<{ id: string; where: string; shell: string; cwd: string }>("/term/start", { conv });
      SESSIONS[conv] = { ...r, buf: "", cursor: 0, history: [], alive: true };
      setSess(SESSIONS[conv]);
      setOut("");
      setError("");
      if (PENDENTES[conv]) {
        const t = PENDENTES[conv];
        delete PENDENTES[conv];
        executarNoTerminal(conv, t).then(() => setOut(SESSIONS[conv]?.buf ?? "")).catch((e) => setError(e.message));
      }
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => {
    alive.current = true;
    setSess(SESSIONS[conv] ?? null);
    setOut(SESSIONS[conv]?.buf ?? "");
    start();
    return () => {
      alive.current = false;
    };
  }, [conv]);

  // Polling longo: o backend segura até 20 s esperando saída nova.
  useEffect(() => {
    if (!sess) return;
    let stop = false;
    (async () => {
      while (!stop && alive.current) {
        try {
          const r = await api.get<{ text: string; cursor: number; alive: boolean }>(`/term/${sess.id}/poll?cursor=${sess.cursor}`);
          if (stop) break;
          const s = SESSIONS[conv];
          if (!s || s.id !== sess.id) break;
          if (r.text) {
            s.buf = (s.buf + r.text).slice(-200_000);
            setOut(s.buf);
          }
          s.cursor = r.cursor;
          s.alive = r.alive;
          if (!r.alive) {
            setError("O shell encerrou. Clique em Novo shell.");
            break;
          }
        } catch (e: any) {
          if (stop) break;
          setError(e.message);
          await new Promise((res) => setTimeout(res, 2000));
        }
      }
    })();
    return () => {
      stop = true;
    };
  }, [sess?.id]);

  useEffect(() => {
    pre.current?.scrollTo({ top: pre.current.scrollHeight });
  }, [out]);

  async function send() {
    const s = SESSIONS[conv];
    const text = cmd;
    if (!s || !text.trim()) return;
    setCmd("");
    setHist(-1);
    s.history = [text, ...s.history.filter((h) => h !== text)].slice(0, 100);
    s.buf = (s.buf + `\n$ ${text}\n`).slice(-200_000);
    setOut(s.buf);
    try {
      await api.post(`/term/${s.id}/input`, { text });
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }

  const s = SESSIONS[conv];
  return (
    <div className="flex h-full flex-col text-xs">
      <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-faint">
        {sess ? (
          <span className="truncate" title={sess.cwd}>
            {sess.shell} · {sess.where === "container" ? "container" : "seu sistema"} · {sess.cwd}
          </span>
        ) : (
          <span>abrindo shell…</span>
        )}
        <button onClick={() => start(true)} className="ml-auto rounded-md px-2 py-0.5 text-muted hover:bg-raised hover:text-fg">
          Novo shell
        </button>
        <button
          onClick={() => {
            if (s) s.buf = "";
            setOut("");
          }}
          className="rounded-md px-2 py-0.5 text-muted hover:bg-raised hover:text-fg"
        >
          Limpar
        </button>
      </div>
      {error && <div className="border-b border-line px-3 py-1 text-red-300">{error}</div>}
      <pre
        ref={pre}
        onClick={() => document.getElementById("forja-term-input")?.focus()}
        className="min-h-0 flex-1 overflow-auto bg-[#0d0d0d] p-3 font-mono text-[12px] leading-5 whitespace-pre-wrap text-fg/90"
      >
        {out || "(sem saída ainda: digite um comando abaixo)"}
      </pre>
      <div className="flex items-center gap-2 border-t border-line bg-surface px-3 py-2">
        <span className="font-mono text-faint">$</span>
        <input
          id="forja-term-input"
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              send();
            } else if (e.key === "ArrowUp" && s?.history.length) {
              e.preventDefault();
              const i = Math.min(hist + 1, s.history.length - 1);
              setHist(i);
              setCmd(s.history[i]);
            } else if (e.key === "ArrowDown") {
              e.preventDefault();
              const i = Math.max(hist - 1, -1);
              setHist(i);
              setCmd(i === -1 ? "" : s!.history[i]);
            }
          }}
          placeholder="comando (Enter envia, ↑↓ histórico)"
          spellCheck={false}
          autoComplete="off"
          className="min-w-0 flex-1 bg-transparent font-mono text-fg placeholder:text-faint focus:outline-none"
        />
      </div>
    </div>
  );
}

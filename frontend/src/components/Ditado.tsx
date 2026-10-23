import { useCallback, useEffect, useRef, useState } from "react";
import { api, auth } from "../api";
import { Mic } from "./icons";

type Campo = HTMLTextAreaElement;
type Fase = "parado" | "gravando" | "transcrevendo";

/** Ditado em qualquer campo de texto do Forja (todos os chats, prompts, revisões): um microfone aparece acima do canto
 *  direito do campo em foco; um clique grava, outro para, e o texto entra onde estava o cursor. Transcreve com o
 *  mesmo Whisper do assistente (/api/transcrever), pelo mesmo microfone escolhido na página F.O.R.J.A. Global de
 *  propósito: são dezenas de campos, e um botão em cada um esqueceria os próximos. */
export default function Ditado() {
  const [campo, setCampo] = useState<Campo | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const [fase, setFase] = useState<Fase>("parado");
  const [erro, setErro] = useState("");
  const gravador = useRef<MediaRecorder | null>(null);
  const ultimo = useRef<Campo | null>(null);  // o último campo focado: "Forja, dita" escreve nele
  const alvo = useRef<{ el: Campo; ini: number; fim: number } | null>(null);
  const faseRef = useRef<Fase>("parado");
  faseRef.current = fase;

  const posiciona = useCallback((el: Campo | null) => {
    if (!el || !el.isConnected) return setPos(null);
    const r = el.getBoundingClientRect();
    if (r.width < 120 || r.height < 18) return setPos(null);  // campo minúsculo (busca de uma linha estreita): sem botão
    setPos({ top: Math.max(4, r.top - 30), left: Math.min(window.innerWidth - 34, r.right - 30) });
  }, []);

  useEffect(() => {
    const entra = (e: FocusEvent) => {
      const el = e.target;
      if (el instanceof HTMLTextAreaElement && !el.readOnly && !el.disabled && faseRef.current === "parado") {
        ultimo.current = el;
        setCampo(el);
        posiciona(el);
      }
    };
    const sai = () => setTimeout(() => {
      if (faseRef.current === "parado" && !(document.activeElement instanceof HTMLTextAreaElement)) setCampo(null);
    }, 150);
    document.addEventListener("focusin", entra);
    document.addEventListener("focusout", sai);
    return () => { document.removeEventListener("focusin", entra); document.removeEventListener("focusout", sai); };
  }, [posiciona]);

  useEffect(() => {  // o campo cresce, a tela rola: o botão acompanha
    if (!campo) return;
    const t = setInterval(() => posiciona(campo), 300);
    return () => clearInterval(t);
  }, [campo, posiciona]);

  async function microfone(): Promise<MediaStream> {
    // o mesmo microfone do assistente, pelo nome ("Microfone (fifine Microphone) MME" -> "fifine"); sem ele, o padrão
    let deviceId: string | undefined;
    try {
      const cfg = await api.get<{ mic: string }>("/assistente");
      const nome = cfg.mic.replace(/\s+(MME|Windows DirectSound|Windows WASAPI|Windows WDM-KS)$/, "").toLowerCase();
      if (nome) {
        const ds = await navigator.mediaDevices.enumerateDevices();
        deviceId = ds.find((d) => d.kind === "audioinput" && d.label.toLowerCase().includes(nome.slice(0, 24)))?.deviceId;
      }
    } catch { /* assistente sem configuração: o microfone padrão serve */ }
    return navigator.mediaDevices.getUserMedia({ audio: deviceId ? { deviceId: { exact: deviceId } } : true });
  }

  async function comeca(el: Campo | null = campo, sozinho = false) {
    if (!el) return;
    setErro("");
    alvo.current = { el, ini: el.selectionStart ?? el.value.length, fim: el.selectionEnd ?? el.value.length };
    try {
      const stream = await microfone();
      const rec = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      const partes: Blob[] = [];
      rec.ondataavailable = (e) => e.data.size && partes.push(e.data);
      rec.onstop = () => { stream.getTracks().forEach((t) => t.stop()); transcreve(new Blob(partes, { type: "audio/webm" })); };
      rec.start(250);
      gravador.current = rec;
      setFase("gravando");
      if (sozinho) paraNoSilencio(stream, rec);
      setTimeout(() => rec.state === "recording" && rec.stop(), 180_000);  // teto: 3 minutos de ditado
    } catch (e: any) {
      setErro(e?.name === "NotAllowedError" ? "O Windows não liberou o microfone para o Forja." : e?.message ?? String(e));
    }
  }

  /** Pela voz ("Forja, dita") não há botão para parar: para depois de 2 s de silêncio (ou 8 s sem começar a falar). */
  function paraNoSilencio(stream: MediaStream, rec: MediaRecorder) {
    const ctx = new AudioContext();
    const an = ctx.createAnalyser();
    ctx.createMediaStreamSource(stream).connect(an);
    const buf = new Float32Array(an.fftSize);
    const inicio = Date.now();
    let falou = false, calado = Date.now();
    const t = setInterval(() => {
      if (rec.state !== "recording") { clearInterval(t); ctx.close(); return; }
      an.getFloatTimeDomainData(buf);
      const rms = Math.sqrt(buf.reduce((s, x) => s + x * x, 0) / buf.length);
      if (rms > 0.012) { falou = true; calado = Date.now(); }
      if ((falou && Date.now() - calado > 2000) || (!falou && Date.now() - inicio > 8000)) rec.stop();
    }, 100);
  }

  useEffect(() => {  // "Forja, dita": espera ele terminar o "Pode falar" e grava no campo aberto
    const dita = (e: Event) => {
      const texto = (e as CustomEvent<string>).detail;
      const visivel = (el: Element) => (el as HTMLElement).offsetParent !== null;
      const el = (ultimo.current?.isConnected && visivel(ultimo.current) ? ultimo.current : null)
        ?? ([...document.querySelectorAll("textarea")].filter((x) => visivel(x) && !x.readOnly && !x.disabled).pop() as Campo | undefined);
      if (!el || faseRef.current !== "parado") return;
      el.focus();
      setCampo(el);
      posiciona(el);
      if (texto) {  // "digita aí: ..." já trouxe o texto: entra direto, sem gravar
        alvo.current = { el, ini: el.selectionStart ?? el.value.length, fim: el.selectionEnd ?? el.value.length };
        return insere(texto);
      }
      setTimeout(() => comeca(el, true), 1500);
    };
    window.addEventListener("forja:ditar", dita);
    return () => window.removeEventListener("forja:ditar", dita);
  });

  async function transcreve(audio: Blob) {
    setFase("transcrevendo");
    try {
      const form = new FormData();
      form.append("file", audio, "ditado.webm");
      const r = await fetch("/api/transcrever", { method: "POST", body: form, headers: auth() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail ?? `HTTP ${r.status}`);
      insere(String(d.texto ?? "").trim());
    } catch (e: any) {
      setErro(e.message ?? String(e));
    } finally {
      setFase("parado");
    }
  }

  function insere(texto: string) {
    const a = alvo.current;
    if (!a || !texto || !a.el.isConnected) return;
    const { el, ini, fim } = a;
    const antes = el.value.slice(0, ini), depois = el.value.slice(fim);
    const ins = (antes && !/\s$/.test(antes) ? " " : "") + texto + (depois && !/^\s/.test(depois) ? " " : "");
    // o setter nativo + evento "input": é o que o React de cada tela enxerga como digitação
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(el, antes + ins + depois);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.focus();
    el.setSelectionRange(antes.length + ins.length, antes.length + ins.length);
  }

  useEffect(() => { if (erro) { const t = setTimeout(() => setErro(""), 5000); return () => clearTimeout(t); } }, [erro]);

  // o microfone ao lado do enviar (Composer.tsx) mostra o estado e pede para gravar/parar por evento
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("forja:ditado-estado", { detail: { fase, el: alvo.current?.el ?? null, erro } }));
  }, [fase, erro]);
  useEffect(() => {
    const aqui = (e: Event) => {
      const el = (e as CustomEvent<Campo>).detail;
      if (faseRef.current === "gravando") gravador.current?.stop();
      else if (faseRef.current === "parado") { ultimo.current = el; comeca(el); }
    };
    window.addEventListener("forja:ditar-aqui", aqui);
    return () => window.removeEventListener("forja:ditar-aqui", aqui);
  });

  if (!campo || !pos || campo.closest("[data-caixa-prompt]")) return null;  // caixa de prompt: o botão já está ao lado do enviar
  const gravando = fase === "gravando", ocupado = fase === "transcrevendo";
  return (
    <div className="fixed z-[60] flex items-center gap-1.5" style={{ top: pos.top, left: pos.left - (erro ? 260 : 0) }}>
      {erro && <span className="max-w-[250px] rounded-[8px] border border-line bg-surface px-2 py-1 text-[11px] text-diff-del-fg shadow">{erro}</span>}
      <button
        type="button"
        aria-label={gravando ? "Parar e transcrever" : ocupado ? "Transcrevendo" : "Ditar por voz"}
        title={gravando ? "Parar e transcrever" : ocupado ? "Transcrevendo…" : "Ditar por voz"}
        disabled={ocupado}
        onMouseDown={(e) => e.preventDefault()}  // não tira o foco do campo
        onClick={() => (gravando ? gravador.current?.stop() : comeca(campo))}
        className={`grid size-[26px] place-items-center rounded-full border shadow-sm transition-colors ${
          gravando ? "animate-pulse border-red-500/60 bg-red-500/90 text-white"
            : ocupado ? "border-line bg-surface text-faint" : "border-line bg-surface text-muted hover:text-fg"}`}
      >
        <Mic className="size-[14px]" />
      </button>
    </div>
  );
}

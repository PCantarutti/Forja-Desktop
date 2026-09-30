import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Pause, Pip, Play, QuadroAntes, QuadroDepois, Repetir, TelaCheia, Teclado, X } from "./icons";

/** Player dos vídeos do Wan: clipes de 2 a 5 s, sem áudio, que a pessoa examina quadro a quadro.
 *
 *  Por isso os controles são outros que os do `<video controls>`: linha do tempo com miniaturas,
 *  contador de quadro exato, passo de um quadro, loop ligado e velocidade — e nada de volume. */

/** O outro vídeo, tocando junto com este (Comparar): `deslizar` = cortina, com ele à esquerda da barra;
 *  `lado` = lado a lado, ele à esquerda. `antes`: é o original (a cortina diz Antes/Depois). */
export type ComparaVideo = { src: string; nome: string; modo: "deslizar" | "lado"; antes?: boolean; nomeAtual?: string };

export type VideoPlayerApi = {
  /** O quadro na tela, em PNG no tamanho real do vídeo ("usar como início", "salvar quadro"). */
  capturar: () => Promise<Blob | null>;
  pausar: () => void;
};

const VELOCIDADES = [0.25, 0.5, 1, 1.5, 2];
const SOME_EM = 2000; // ms parado com o vídeo tocando até os controles saírem da frente
const EPS = 1e-3;

export const ATALHOS: [string, string][] = [
  ["Espaço · K", "tocar / pausar"],
  ["← · →", "um quadro"],
  ["Shift + ← · →", "um segundo"],
  ["J · L", "−1 s · +1 s"],
  ["Home · End", "início · fim"],
  ["0 – 9", "ir a 0 – 90 %"],
  ["F", "tela cheia"],
  ["P", "picture-in-picture"],
  ["R", "loop"],
  ["< · >", "mais lento · mais rápido"],
  ["?", "estes atalhos"],
];

const segundos = (t: number) => `${t.toFixed(2).replace(".", ",")} s`;

// ---------------------------------------------------------------- miniaturas da linha do tempo

const filmstrips = new Map<string, Promise<string[]>>();

/** Miniaturas tiradas do próprio vídeo num `<video>` fora da tela: nada de ffmpeg, e só uma vez por
 *  arquivo (o cache vive enquanto o app estiver aberto). */
function tirarMiniaturas(src: string, n: number): Promise<string[]> {
  const chave = `${src}#${n}`;
  const pronto = filmstrips.get(chave);
  if (pronto) return pronto;
  const p = new Promise<string[]>((resolve) => {
    const v = document.createElement("video");
    v.muted = true;
    v.preload = "auto";
    v.src = src;
    const fotos: string[] = [];
    const canvas = document.createElement("canvas");
    let i = 0;
    const proximo = () => {
      if (i >= n || !v.duration) return resolve(fotos);
      v.currentTime = Math.min(v.duration - EPS, ((i + 0.5) / n) * v.duration);
    };
    v.onloadeddata = () => {
      canvas.height = 72;
      canvas.width = Math.round((72 * v.videoWidth) / Math.max(1, v.videoHeight));
      proximo();
    };
    v.onseeked = () => {
      canvas.getContext("2d")?.drawImage(v, 0, 0, canvas.width, canvas.height);
      fotos.push(canvas.toDataURL("image/jpeg", 0.72));
      i++;
      proximo();
    };
    v.onerror = () => resolve(fotos);
  });
  filmstrips.set(chave, p);
  return p;
}

// ---------------------------------------------------------------- player

type Props = {
  src: string;
  fps: number;
  /** Quantos quadros o vídeo tem, se já se sabe (o lote anota); senão sai da duração. */
  quadros?: number;
  /** Chat e cartão: controles mínimos. Foco e tela cheia: tudo. */
  compacto?: boolean;
  autoPlay?: boolean;
  /** Atalhos valendo na janela inteira (modo foco), e não só com o player focado. */
  tecladoGlobal?: boolean;
  /** Início→Fim: marca na linha do tempo onde estão os quadros dados. */
  marcas?: boolean;
  /** Atalhos de quem usa o player (o foco: ↑↓, M/X, Esc), listados junto no painel "?". */
  atalhosExtras?: [string, string][];
  className?: string;
  style?: React.CSSProperties;
  onTelaCheia?: () => void;
  comparar?: ComparaVideo | null;
};

export const VideoPlayer = forwardRef<VideoPlayerApi, Props>(function VideoPlayer(props, ref) {
  const caixa = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const trilho = useRef<HTMLDivElement>(null);
  const [tocando, setTocando] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);
  const [loop, setLoop] = useState(true);
  const [vel, setVel] = useState(1);
  const [fotos, setFotos] = useState<string[]>([]);
  const [sobre, setSobre] = useState<number | null>(null); // fração da linha do tempo sob o mouse
  const [arrastando, setArrastando] = useState(false);
  const [visiveis, setVisiveis] = useState(true);
  const [ajuda, setAjuda] = useState(false);
  const [dims, setDims] = useState<[number, number]>([0, 0]);
  const esconder = useRef<number | undefined>(undefined);
  // Comparar: o outro vídeo só segue este (tocar, pausar, posição, velocidade); quem manda é o principal.
  const outro = useRef<HTMLVideoElement>(null);
  const [corte, setCorte] = useState(0.5);
  const [outroFalhou, setOutroFalhou] = useState(false);
  const cmp = props.comparar ?? null;
  const lado = cmp?.modo === "lado";
  useEffect(() => {
    setCorte(0.5);
    setOutroFalhou(false);
  }, [cmp?.src]);

  const fps = props.fps || 16;
  /** Leva o outro vídeo ao estado do principal. Pausado ou num salto (seek, volta do loop), acerta a posição.
   *  Tocando, não salta: cada salto é um seek, que engasga o decodificador — ele acelera ou freia 3% até
   *  alcançar, e só salta se ficou mais de 4 quadros para trás ou para a frente. */
  const segue = useCallback((forcar = false) => {
    const o = outro.current, v = video.current;
    if (!o || !v || !o.duration) return;
    const quadro = 1 / (props.fps || 16);
    const alvo = Math.min(o.duration, v.currentTime);
    const d = o.currentTime - alvo;
    if (forcar || v.paused || Math.abs(d) > 4 * quadro) {
      if (Math.abs(d) > 1e-3) o.currentTime = alvo;
      o.playbackRate = v.playbackRate;
    } else {
      const r = Math.abs(d) > quadro / 2 ? v.playbackRate * (d > 0 ? 0.97 : 1.03) : v.playbackRate;
      if (o.playbackRate !== r) o.playbackRate = r;
    }
    if (v.paused && !o.paused) o.pause();
    else if (!v.paused && o.paused && alvo < o.duration) o.play().catch(() => {});
  }, [props.fps]);
  const total = props.quadros || Math.max(1, Math.round(dur * fps));
  const quadro = Math.min(total - 1, Math.max(0, Math.floor(t * fps + EPS)));

  // Tempo exato do quadro na tela: o `timeupdate` chega 4 vezes por segundo, pouco para um clipe de 2 s.
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    let vivo = true;
    let id = 0;
    const rvfc = (v as any).requestVideoFrameCallback?.bind(v);
    if (rvfc) {
      const cada = (_: number, meta: { mediaTime: number }) => {
        if (!vivo) return;
        // Só tocando: pausado, o quadro de um seek anterior chegava depois do seguinte e voltava o
        // contador (Home e três → paravam no quadro 3, não no 4). Pausado quem manda é o currentTime.
        if (!v.paused) setT(meta.mediaTime);
        segue();
        id = rvfc(cada);
      };
      id = rvfc(cada);
      return () => {
        vivo = false;
        (v as any).cancelVideoFrameCallback?.(id);
      };
    }
    const quadroAQuadro = () => {
      if (!vivo) return;
      setT(v.currentTime);
      segue();
      id = requestAnimationFrame(quadroAQuadro);
    };
    id = requestAnimationFrame(quadroAQuadro);
    return () => {
      vivo = false;
      cancelAnimationFrame(id);
    };
  }, [props.src, segue]);

  useEffect(() => {
    setFotos([]);
    if (!dur) return;
    let vivo = true;
    // uma miniatura a cada ~4 quadros, entre 6 e 16: dá para achar o momento sem virar mosaico
    tirarMiniaturas(props.src, Math.min(16, Math.max(6, Math.round(total / 4)))).then((f) => vivo && setFotos(f));
    return () => {
      vivo = false;
    };
  }, [props.src, dur, total]);

  useEffect(() => {
    if (video.current) video.current.playbackRate = vel;
    if (outro.current) outro.current.playbackRate = vel;
  }, [vel]);

  const mexeu = useCallback(() => {
    setVisiveis(true);
    window.clearTimeout(esconder.current);
    esconder.current = window.setTimeout(() => setVisiveis(false), SOME_EM);
  }, []);

  const irPara = useCallback((s: number) => {
    const v = video.current;
    if (!v || !v.duration) return;
    // Até a duração exata: o webm dá como duração o início do último quadro, e com "− 1 ms" o End e o
    // passo a passo nunca chegavam nele (paravam no 16 de 17).
    v.currentTime = Math.min(v.duration, Math.max(0, s));
    setT(v.currentTime);
    if (outro.current?.duration) outro.current.currentTime = Math.min(outro.current.duration, v.currentTime);
  }, []);

  const tocarPausar = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (v.paused) {
      if (v.ended || v.currentTime >= v.duration - EPS) v.currentTime = 0;
      v.play().catch(() => {});
    } else v.pause();
  }, []);

  const passo = useCallback(
    (n: number) => {
      const v = video.current;
      if (!v) return;
      v.pause();
      const atual = Math.min(total - 1, Math.max(0, Math.floor(v.currentTime * fps + EPS)));
      const alvo = Math.min(total - 1, Math.max(0, atual + n));
      irPara((alvo + 0.5) / fps); // o meio do quadro: na borda o decodificador pode mostrar o vizinho
    },
    [total, fps, irPara],
  );

  const telaCheia = useCallback(() => {
    if (props.onTelaCheia) return props.onTelaCheia();
    if (document.fullscreenElement) document.exitFullscreen();
    else caixa.current?.requestFullscreen().catch(() => {});
  }, [props.onTelaCheia]);

  const pip = useCallback(() => {
    const v = video.current as any;
    if (!v) return;
    if (document.pictureInPictureElement) (document as any).exitPictureInPicture();
    else v.requestPictureInPicture?.().catch(() => {});
  }, []);

  useImperativeHandle(ref, () => ({
    pausar: () => {
      video.current?.pause();
      outro.current?.pause();
    },
    capturar: async () => {
      const v = video.current;
      if (!v || !v.videoWidth) return null;
      const c = document.createElement("canvas");
      c.width = v.videoWidth;
      c.height = v.videoHeight;
      c.getContext("2d")?.drawImage(v, 0, 0);
      return await new Promise((r) => c.toBlob((b) => r(b), "image/png"));
    },
  }));

  const teclas = useCallback(
    (e: KeyboardEvent | React.KeyboardEvent) => {
      const alvo = e.target as HTMLElement;
      if (alvo.closest("input, textarea, select, [contenteditable=true]")) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return; // Ctrl+0 (zoom), Ctrl+R etc. são do app, não do player
      // Espaço/Enter num botão é do botão ("Manter", "Loop"...): senão tocava o vídeo e o botão não agia.
      if ((e.key === " " || e.key === "Enter") && alvo.closest("button, a, [role=button]")) return;
      const v = video.current;
      if (!v) return;
      const k = e.key;
      const feito = () => {
        e.preventDefault();
        e.stopPropagation();
        mexeu();
      };
      if (k === " " || k === "k" || k === "K") return feito(), tocarPausar();
      if (k === "ArrowLeft") return feito(), e.shiftKey ? irPara(v.currentTime - 1) : passo(-1);
      if (k === "ArrowRight") return feito(), e.shiftKey ? irPara(v.currentTime + 1) : passo(1);
      if (k === "j" || k === "J") return feito(), irPara(v.currentTime - 1);
      if (k === "l" || k === "L") return feito(), irPara(v.currentTime + 1);
      if (k === "Home") return feito(), irPara(0);
      if (k === "End") return feito(), irPara(v.duration);
      if (/^[0-9]$/.test(k)) return feito(), irPara((Number(k) / 10) * v.duration);
      if (k === "f" || k === "F") return feito(), telaCheia();
      if (k === "p" || k === "P") return feito(), pip();
      if (k === "r" || k === "R") return feito(), setLoop((x) => !x);
      if (k === "?") return feito(), setAjuda((x) => !x);
      if (k === ">" || k === ".") return feito(), setVel((x) => VELOCIDADES[Math.min(VELOCIDADES.length - 1, VELOCIDADES.indexOf(x) + 1)]);
      if (k === "<" || k === ",") return feito(), setVel((x) => VELOCIDADES[Math.max(0, VELOCIDADES.indexOf(x) - 1)]);
    },
    [tocarPausar, irPara, passo, telaCheia, pip, mexeu],
  );

  useEffect(() => {
    if (!props.tecladoGlobal) return;
    const h = (e: KeyboardEvent) => teclas(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [props.tecladoGlobal, teclas]);

  function fracaoDo(e: React.PointerEvent) {
    const r = trilho.current!.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  }

  const frac = dur ? t / dur : 0;
  const mostrar = visiveis || !tocando || arrastando || ajuda;
  const miniatura = sobre !== null && fotos.length ? fotos[Math.min(fotos.length - 1, Math.floor(sobre * fotos.length))] : null;
  // Como os botões dos painéis do Forja: cantos de 8px, texto cheio, fundo só no hover, anel no foco.
  const botao =
    "grid size-8 shrink-0 place-items-center rounded-lg text-fg outline-none transition-colors hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-sky-400/60";
  const ligado = "bg-sky-400/15 text-sky-300 hover:bg-sky-400/20";

  return (
    <div
      ref={caixa}
      tabIndex={0}
      onKeyDown={props.tecladoGlobal ? undefined : teclas}
      onPointerMove={mexeu}
      onPointerLeave={() => tocando && setVisiveis(false)}
      style={props.style}
      className={`group/player relative isolate overflow-hidden bg-black outline-none focus-visible:ring-2 focus-visible:ring-sky-400/60 ${
        mostrar ? "" : "cursor-none"
      } ${props.className ?? ""}`}
    >
      <div className={`size-full ${lado ? "flex" : "relative"}`}>
        {cmp && lado && (
          <video ref={outro} src={cmp.src} loop={loop} muted playsInline preload="auto"
                 onLoadedMetadata={() => segue(true)} onError={() => setOutroFalhou(true)}
                 onClick={tocarPausar}
                 className="block h-full w-1/2 min-w-0 cursor-pointer border-r border-white/10 object-contain" />
        )}
        <video
          ref={video}
          src={props.src}
          loop={loop}
          muted
          playsInline
          autoPlay={props.autoPlay}
          preload="auto"
          onClick={tocarPausar}
          onDoubleClick={telaCheia}
          onPlay={() => {
            setTocando(true);
            mexeu();
            segue(true);
          }}
          onPause={(e) => {
            setTocando(false);
            setT(e.currentTarget.currentTime);
            segue(true);
          }}
          onSeeked={(e) => {
            if (e.currentTarget.paused) setT(e.currentTarget.currentTime);
            segue(true);
          }}
          onLoadedMetadata={(e) => {
            setDur(e.currentTarget.duration);
            setDims([e.currentTarget.videoWidth, e.currentTarget.videoHeight]);
          }}
          className={`block cursor-pointer object-contain ${lado ? "h-full w-1/2 min-w-0" : "size-full"}`}
        />
        {cmp && !lado && (
          // A cortina: o outro por cima, recortado até a barra (o recorte é da caixa, então os dois ficam alinhados
          // mesmo com resoluções diferentes: a ampliação 2× sai do mesmo tamanho na tela e a diferença é detalhe).
          <video ref={outro} src={cmp.src} loop={loop} muted playsInline preload="auto"
                 onLoadedMetadata={() => segue(true)} onError={() => setOutroFalhou(true)}
                 className="pointer-events-none absolute inset-0 block size-full object-contain"
                 style={{ clipPath: `inset(0 ${(1 - corte) * 100}% 0 0)` }} />
        )}
      </div>

      {cmp && !lado && (
        <div
          className="absolute inset-y-0 z-[1] w-8 -translate-x-1/2 cursor-ew-resize touch-none"
          style={{ left: `${corte * 100}%` }}
          role="slider"
          aria-label="Divisão entre os dois vídeos"
          aria-valuenow={Math.round(corte * 100)}
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
              e.preventDefault();
              e.stopPropagation();  // senão o player passava um quadro
              setCorte((c) => Math.min(1, Math.max(0, c + (e.key === "ArrowLeft" ? -0.02 : 0.02))));
            }
          }}
          onPointerDown={(e) => {
            e.stopPropagation();
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
            const r = caixa.current!.getBoundingClientRect();
            setCorte(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)));
          }}
        >
          <div className="mx-auto h-full w-0.5 bg-white/90 shadow-[0_0_8px_rgba(0,0,0,.6)]" />
          <div className="absolute top-1/2 left-1/2 grid size-9 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/40 bg-black/75 text-white shadow-popover">
            <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="m9 7-5 5 5 5M15 7l5 5-5 5" />
            </svg>
          </div>
        </div>
      )}
      {cmp && (
        <>
          <span className="pointer-events-none absolute left-3 top-3 z-[1] max-w-[40%] truncate rounded-md bg-black/70 px-2 py-0.5 text-[11px] text-white/85" title={cmp.nome}>
            {outroFalhou ? "Não deu para tocar este vídeo aqui" : lado ? cmp.nome : cmp.antes ? "Antes" : cmp.nome}
          </span>
          <span className={`pointer-events-none absolute top-3 z-[1] max-w-[40%] truncate rounded-md bg-black/70 px-2 py-0.5 text-[11px] text-white/85 ${lado ? "left-[calc(50%+0.75rem)]" : "right-3"}`}
                title={cmp.nomeAtual}>
            {!lado && cmp.antes ? "Depois" : cmp.nomeAtual ?? "Este"}
          </span>
        </>
      )}

      {/* Grande no meio só com o vídeo parado: o convite para tocar, sem cobrir a imagem tocando. */}
      {!tocando && !arrastando && (
        <button
          onClick={tocarPausar}
          aria-label="Tocar"
          className="absolute left-1/2 top-1/2 grid size-14 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/15 bg-[#161616]/85 text-fg shadow-popover backdrop-blur-md transition hover:scale-105 hover:bg-surface"
        >
          <Play className="size-6" />
        </button>
      )}

      {ajuda && (
        <div className="absolute inset-0 z-10 grid place-items-center bg-black/70 backdrop-blur-sm" onClick={() => setAjuda(false)}>
          <div className="rounded-2xl border border-white/10 bg-black/60 p-4 text-xs text-white/85" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex items-center gap-2 text-sm font-medium text-white">
              <Teclado className="size-4" /> Atalhos do player
              <button onClick={() => setAjuda(false)} className="ml-auto rounded p-0.5 hover:bg-white/10" aria-label="Fechar">
                <X className="size-3.5" />
              </button>
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
              {[...ATALHOS, ...(props.atalhosExtras ?? [])].map(([k, o]) => (
                <div key={k} className="contents">
                  <dt className="font-mono text-white">{k}</dt>
                  <dd className="text-white/70">{o}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      )}

      <div
        // Painel sólido flutuando sobre o vídeo (o degradê sumia em cena clara: neve, céu), com a borda e o
        // fundo dos painéis do Forja. Sem backdrop-blur: por cima de vídeo tocando ele refaz o desfoque a cada
        // quadro (medido: 8 fps na página e metade dos quadros perdidos; sem ele, 70 fps).
        className={`absolute z-[2] rounded-xl border border-white/10 bg-[#161616]/95 shadow-lg shadow-black/40 transition-opacity duration-300 ${
          props.compacto ? "inset-x-1.5 bottom-1.5 px-2 pb-1 pt-2" : "inset-x-3 bottom-3 px-3 pb-2 pt-2.5"
        } ${mostrar ? "opacity-100" : "pointer-events-none opacity-0"}`}
      >
        {/* Linha do tempo: as miniaturas são o trilho; o que já passou fica aceso. */}
        <div
          ref={trilho}
          role="slider"
          aria-label="Posição no vídeo"
          aria-valuemin={1}
          aria-valuemax={total}
          aria-valuenow={quadro + 1}
          aria-valuetext={`quadro ${quadro + 1} de ${total}`}
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            setArrastando(true);
            video.current?.pause();
            irPara(fracaoDo(e) * dur);
          }}
          onPointerMove={(e) => {
            const f = fracaoDo(e);
            setSobre(f);
            if (arrastando) irPara(f * dur);
          }}
          onPointerUp={() => setArrastando(false)}
          onPointerLeave={() => setSobre(null)}
          className={`relative cursor-pointer select-none overflow-visible rounded-md ${props.compacto ? "h-1.5" : "h-8"}`}
        >
          <div className="absolute inset-0 flex overflow-hidden rounded-md bg-white/15">
            {!props.compacto &&
              fotos.map((f, i) => <img key={i} src={f} alt="" draggable={false} className="h-full min-w-0 flex-1 object-cover opacity-35" />)}
          </div>
          {/* o trecho já visto, sem as miniaturas apagadas */}
          <div className="absolute inset-y-0 left-0 overflow-hidden rounded-l-md" style={{ width: `${frac * 100}%` }}>
            {props.compacto ? (
              <div className="size-full bg-sky-400" />
            ) : (
              <div className="flex h-full" style={{ width: trilho.current?.clientWidth ?? 0 }}>
                {fotos.map((f, i) => <img key={i} src={f} alt="" draggable={false} className="h-full min-w-0 flex-1 object-cover" />)}
              </div>
            )}
          </div>
          {props.marcas && !props.compacto && (
            <>
              <span className="absolute -top-1 left-0 size-2 -translate-x-1/2 rotate-45 rounded-[2px] bg-emerald-400" title="Quadro inicial dado" />
              <span className="absolute -top-1 right-0 size-2 translate-x-1/2 rotate-45 rounded-[2px] bg-emerald-400" title="Quadro final dado" />
            </>
          )}
          <div
            className={`pointer-events-none absolute -inset-y-1 w-0.5 -translate-x-1/2 rounded-full bg-white shadow-[0_0_0_1px_rgba(0,0,0,.4)] ${props.compacto ? "hidden" : ""}`}
            style={{ left: `${frac * 100}%` }}
          />
          {sobre !== null && !props.compacto && (
            <div
              className="pointer-events-none absolute bottom-full mb-2 -translate-x-1/2 overflow-hidden rounded-lg border border-white/15 bg-black/80 shadow-xl"
              style={{ left: `clamp(64px, ${sobre * 100}%, calc(100% - 64px))` }}
            >
              {miniatura && <img src={miniatura} alt="" className="block h-[72px] w-auto" />}
              <div className="px-2 py-1 text-center text-[10px] tabular-nums text-white/80">
                {segundos(sobre * dur)} · quadro {Math.min(total, Math.floor(sobre * total) + 1)}/{total}
              </div>
            </div>
          )}
        </div>

        <div className="mt-2 flex items-center gap-0.5 text-fg">
          <button className={botao} onClick={tocarPausar} title={tocando ? "Pausar (Espaço)" : "Tocar (Espaço)"}>
            {tocando ? <Pause className="size-4" /> : <Play className="size-4" />}
          </button>
          {!props.compacto && (
            <>
              <button className={botao} onClick={() => passo(-1)} title="Quadro anterior (←)">
                <QuadroAntes className="size-4" />
              </button>
              <button className={botao} onClick={() => passo(1)} title="Próximo quadro (→)">
                <QuadroDepois className="size-4" />
              </button>
            </>
          )}
          <span className="mx-1.5 h-4 w-px bg-white/10" aria-hidden />
          <span className="font-mono text-[11px] tabular-nums text-fg" title={`${segundos(t)} de ${segundos(dur)}`}>
            {String(quadro + 1).padStart(String(total).length, "0")}
            <span className="text-muted"> / {total}</span>
          </span>
          {!props.compacto && (
            <span className="ml-2.5 hidden text-[11px] tabular-nums text-muted sm:inline">
              {segundos(t)} · {fps} fps{dims[0] ? ` · ${dims[0]}×${dims[1]}` : ""}
            </span>
          )}
          <div className="ml-auto flex items-center gap-1">
            {!props.compacto && (
              <button
                className={`h-7 min-w-11 rounded-lg border px-2 font-mono text-[11px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-sky-400/60 ${
                  vel === 1 ? "border-white/10 text-fg hover:bg-white/10" : "border-sky-400/40 bg-sky-400/10 text-sky-300"
                }`}
                onClick={() => setVel((v) => VELOCIDADES[(VELOCIDADES.indexOf(v) + 1) % VELOCIDADES.length])}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setVel((v) => VELOCIDADES[(VELOCIDADES.indexOf(v) - 1 + VELOCIDADES.length) % VELOCIDADES.length]);
                }}
                title="Velocidade (clique ou >: mais rápido · botão direito ou <: mais lento)"
              >
                {String(vel).replace(".", ",")}×
              </button>
            )}
            <button
              className={`${botao} ${loop ? ligado : "text-muted"}`}
              onClick={() => setLoop((x) => !x)}
              title={loop ? "Loop ligado (R)" : "Loop desligado (R)"}
              aria-pressed={loop}
            >
              <Repetir className="size-4" />
            </button>
            {!props.compacto && (
              <>
                {"pictureInPictureEnabled" in document && (
                  <button className={botao} onClick={pip} title="Picture-in-picture (P)">
                    <Pip className="size-4" />
                  </button>
                )}
                <button className={botao} onClick={() => setAjuda((x) => !x)} title="Atalhos (?)">
                  <Teclado className="size-4" />
                </button>
              </>
            )}
            <button className={botao} onClick={telaCheia} title="Tela cheia (F)">
              <TelaCheia className="size-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
});

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Pause, Pip, Play, QuadroAntes, QuadroDepois, Repetir, TelaCheia, Teclado, X } from "./icons";

/** Player dos vídeos do Wan: clipes de 2 a 5 s, sem áudio, que a pessoa examina quadro a quadro.
 *
 *  Por isso os controles são outros que os do `<video controls>`: linha do tempo com miniaturas,
 *  contador de quadro exato, passo de um quadro, loop ligado e velocidade — e nada de volume. */

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
  className?: string;
  style?: React.CSSProperties;
  onTelaCheia?: () => void;
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

  const fps = props.fps || 16;
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
      id = requestAnimationFrame(quadroAQuadro);
    };
    id = requestAnimationFrame(quadroAQuadro);
    return () => {
      vivo = false;
      cancelAnimationFrame(id);
    };
  }, [props.src]);

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
    pausar: () => video.current?.pause(),
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
  const botao = "grid size-8 shrink-0 place-items-center rounded-full text-white/85 hover:bg-white/15 hover:text-white";

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
        }}
        onPause={(e) => {
          setTocando(false);
          setT(e.currentTarget.currentTime);
        }}
        onSeeked={(e) => e.currentTarget.paused && setT(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => {
          setDur(e.currentTarget.duration);
          setDims([e.currentTarget.videoWidth, e.currentTarget.videoHeight]);
        }}
        className="block size-full object-contain"
      />

      {/* Grande no meio só com o vídeo parado: o convite para tocar, sem cobrir a imagem tocando. */}
      {!tocando && !arrastando && (
        <button
          onClick={tocarPausar}
          aria-label="Tocar"
          className="absolute left-1/2 top-1/2 grid size-14 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/55 text-white shadow-lg backdrop-blur-sm transition hover:scale-105 hover:bg-black/70"
        >
          <Play className="size-6 translate-x-0.5" />
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
              {ATALHOS.map(([k, o]) => (
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
        className={`absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/45 to-transparent px-3 pb-2 pt-10 transition-opacity duration-300 ${
          mostrar ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
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
          className={`relative cursor-pointer select-none overflow-visible rounded-md ${props.compacto ? "h-1.5" : "h-9"}`}
        >
          <div className="absolute inset-0 flex overflow-hidden rounded-md bg-white/10">
            {!props.compacto &&
              fotos.map((f, i) => <img key={i} src={f} alt="" draggable={false} className="h-full min-w-0 flex-1 object-cover opacity-45" />)}
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

        <div className="mt-1.5 flex items-center gap-1 text-white">
          <button className={botao} onClick={tocarPausar} title={tocando ? "Pausar (Espaço)" : "Tocar (Espaço)"}>
            {tocando ? <Pause className="size-4" /> : <Play className="size-4 translate-x-px" />}
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
          <span className="ml-1 font-mono text-[11px] tabular-nums text-white/85" title={`${segundos(t)} de ${segundos(dur)}`}>
            {String(quadro + 1).padStart(String(total).length, "0")}
            <span className="text-white/45"> / {total}</span>
          </span>
          {!props.compacto && (
            <span className="ml-2 hidden text-[11px] tabular-nums text-white/50 sm:inline">
              {segundos(t)} · {fps} fps{dims[0] ? ` · ${dims[0]}×${dims[1]}` : ""}
            </span>
          )}
          <div className="ml-auto flex items-center gap-0.5">
            {!props.compacto && (
              <button
                className="h-7 min-w-10 rounded-full px-2 font-mono text-[11px] text-white/85 hover:bg-white/15 hover:text-white"
                onClick={() => setVel((v) => VELOCIDADES[(VELOCIDADES.indexOf(v) + 1) % VELOCIDADES.length])}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setVel((v) => VELOCIDADES[(VELOCIDADES.indexOf(v) - 1 + VELOCIDADES.length) % VELOCIDADES.length]);
                }}
                title="Velocidade (clique: mais rápido · botão direito: mais lento)"
              >
                {String(vel).replace(".", ",")}×
              </button>
            )}
            <button
              className={`${botao} ${loop ? "text-sky-300 hover:text-sky-200" : "text-white/50"}`}
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

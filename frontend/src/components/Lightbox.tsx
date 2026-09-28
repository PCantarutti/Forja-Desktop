import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Download, Minus, Plus, Split, TelaCheia, X } from "./icons";

/** Outra imagem que dá para pôr ao lado desta (as da mesma conversa). */
export type Comparavel = { src: string; nome: string };
type Modo = "lado" | "deslizar";

const nomeDe = (src: string) => {
  if (src.startsWith("data:")) return "";
  // O nome vem do `path=` quando é /api/files (o fim da URL é o `conv=97`, que virava o título).
  const caminho = new URL(src, location.href).searchParams.get("path") ?? decodeURIComponent(src.split("?")[0]);
  return caminho.split(/[\\/]/).filter(Boolean).pop() ?? "";
};

/**
 * Visualizador de imagem em tela cheia. Roda do mouse dá zoom no ponto do cursor, arrastar move a
 * imagem quando ela passa da tela, duplo clique alterna entre "ajustar" e ampliado. Teclado: Esc fecha,
 * + e − dão zoom, 0 ajusta, 1 vai ao tamanho real. Clique no fundo (sem arrastar) fecha.
 *
 * A imagem fica no tamanho natural e quem muda é o `transform`: o "Tamanho real" antigo trocava só o
 * `max-width`, e imagem menor que a tela (a maioria das geradas) ficava exatamente igual.
 *
 * Comparar: `antes` (a original de uma ampliação ou edição) abre na cortina — a barra do meio mostra a
 * antiga à esquerda e a nova à direita; qualquer outra da conversa (`outras`) abre lado a lado. Nos dois
 * modos o zoom e o arrasto valem para as duas imagens juntas, então o mesmo pedaço fica sob o olho. A
 * outra imagem ocupa a caixa desta: ampliada 2× ela sai do mesmo tamanho na tela, e a diferença é detalhe.
 */
export function Lightbox({ src, onClose, titulo, antes, outras = [] }: {
  src: string;
  onClose: () => void;
  titulo?: string;
  antes?: string;
  outras?: Comparavel[];
}) {
  const palco = useRef<HTMLDivElement>(null);
  const [medida, setMedida] = useState<[number, number] | null>(null);
  const [v, setV] = useState({ s: 1, x: 0, y: 0 });
  const [ajustado, setAjustado] = useState(true); // segue o tamanho da janela até a pessoa mexer no zoom
  const [arrastando, setArrastando] = useState(false);
  const arrasto = useRef<{ px: number; py: number; x: number; y: number; moveu: boolean } | null>(null);
  const [outra, setOutra] = useState<Comparavel | null>(null);
  const [modo, setModo] = useState<Modo>("deslizar");
  const [escolhendo, setEscolhendo] = useState(false);
  const [corte, setCorte] = useState(0.5); // onde está a barra da cortina, em fração da largura
  const lado = !!outra && modo === "lado";
  const opcoes: Comparavel[] = [
    ...(antes ? [{ src: antes, nome: "Original (antes)" }] : []),
    ...outras.filter((o) => o.src !== src && o.src !== antes),
  ];

  // Lado a lado, cada imagem tem meia tela: o ajuste, o limite do arrasto e o ponto do zoom são da metade.
  const area = () => {
    const el = palco.current;
    return el ? [lado ? el.clientWidth / 2 : el.clientWidth, el.clientHeight] as const : [0, 0] as const;
  };
  // Escala que cabe na tela, descontando as barras de cima e de baixo; imagem pequena também cresce.
  const escalaAjuste = () => {
    const [w, h] = area();
    return w && medida ? Math.min((w - 48) / medida[0], (h - 144) / medida[1]) : 1;
  };
  // Enquanto a imagem cabe num eixo, fica centrada nele; passando da tela, a borda não entra além do limite.
  const limitar = (s: number, x: number, y: number) => {
    const [w, h] = area();
    if (!w || !medida) return { s, x, y };
    const lx = Math.max(0, (medida[0] * s - w) / 2);
    const ly = Math.max(0, (medida[1] * s - h) / 2);
    return { s, x: Math.min(lx, Math.max(-lx, x)), y: Math.min(ly, Math.max(-ly, y)) };
  };
  /** Zoom mantendo parado o ponto (cx, cy), medido a partir do centro da área da imagem. */
  const zoom = (alvo: (s: number) => number, cx = 0, cy = 0) => {
    setAjustado(false);
    setV(({ s, x, y }) => {
      const s2 = Math.min(Math.max(alvo(s), Math.min(escalaAjuste(), 1) / 2), 32);
      const k = s2 / s;
      return limitar(s2, cx - (cx - x) * k, cy - (cy - y) * k);
    });
  };
  const ajustar = () => setAjustado(true);
  const real = () => zoom(() => 1);
  const pontoNoPalco = (e: { clientX: number; clientY: number }) => {
    const r = palco.current!.getBoundingClientRect();
    const [w] = area();
    const x = e.clientX - r.left;
    return [x - (lado && x > w ? w : 0) - w / 2, e.clientY - r.top - r.height / 2] as const;
  };
  const comparar = (c: Comparavel | null) => {
    setOutra(c);
    setEscolhendo(false);
    if (c) setModo(c.src === antes ? "deslizar" : "lado");
    setCorte(0.5);
    setAjustado(true);
  };

  // Os listeners de janela leem sempre a versão atual das funções (o modo muda a conta do ponto).
  const vivo = useRef({ zoom, pontoNoPalco, ajustar, real, onClose, escolhendo });
  vivo.current = { zoom, pontoNoPalco, ajustar, real, onClose, escolhendo };

  useEffect(() => {
    if (!ajustado || !medida) return;
    const f = () => setV({ s: escalaAjuste(), x: 0, y: 0 });
    f();
    window.addEventListener("resize", f);
    return () => window.removeEventListener("resize", f);
  }, [ajustado, medida, lado]); // eslint-disable-line react-hooks/exhaustive-deps

  // Roda do mouse: listener nativo porque o do React é passivo e não impede a página de rolar atrás.
  useEffect(() => {
    const el = palco.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const [cx, cy] = vivo.current.pontoNoPalco(e);
      vivo.current.zoom((s) => s * Math.exp(-e.deltaY * 0.0015), cx, cy); // mouse (±100) e touchpad (passos pequenos)
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const f = vivo.current;
      if (e.key === "Escape") f.escolhendo ? setEscolhendo(false) : f.onClose();
      else if (e.key === "+" || e.key === "=") f.zoom((s) => s * 1.25);
      else if (e.key === "-") f.zoom((s) => s / 1.25);
      else if (e.key === "0") f.ajustar();
      else if (e.key === "1") f.real();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const [aw, ah] = area();
  const passaDaTela = !!medida && !!aw && (medida[0] * v.s > aw || medida[1] * v.s > ah);
  const nome = titulo || nomeDe(src) || "Imagem";
  const botao = "flex h-8 min-w-8 items-center justify-center gap-1.5 rounded-lg px-2 text-xs text-white/75 transition-colors hover:bg-white/10 hover:text-white";
  const ligado = "bg-white/10 text-white";
  const barra = "pointer-events-auto flex items-center gap-0.5 rounded-xl border border-white/10 bg-black/60 p-1 shadow-popover backdrop-blur-md";
  const rotulo = "pointer-events-none absolute z-20 top-16 rounded-md bg-black/60 px-2 py-0.5 text-[11px] text-white/80 backdrop-blur";

  /** A imagem na caixa da principal, com o zoom e o deslocamento de agora (as duas andam juntas). */
  const imagem = (s: string, principal: boolean) => (
    <img
      src={s}
      alt={principal ? nome : outra?.nome}
      draggable={false}
      onLoad={principal ? (e) => setMedida([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight]) : undefined}
      className="absolute top-1/2 left-1/2 max-w-none rounded-sm object-contain shadow-2xl"
      style={medida ? {
        width: medida[0],
        height: medida[1],
        marginLeft: -medida[0] / 2,
        marginTop: -medida[1] / 2,
        transform: `translate(${v.x}px, ${v.y}px) scale(${v.s})`,
        transition: arrastando ? "none" : "transform 120ms ease-out",
        imageRendering: v.s >= 3 ? "pixelated" : "auto", // de perto, o pixel de verdade, não o borrão
      } : { opacity: 0 }}
    />
  );

  // Portal no body: desenhado dentro da resposta, herdava o CSS dela (miniatura de 280px numa célula de
  // tabela) e o "ampliar" mostrava a imagem do mesmo tamanho, só que com o fundo escuro.
  return createPortal(
    <div role="dialog" aria-label={nome} className="fixed inset-0 z-50 select-none bg-black/85 backdrop-blur-sm">
      <div
        ref={palco}
        className={`absolute inset-0 overflow-hidden ${arrastando ? "cursor-grabbing" : passaDaTela ? "cursor-grab" : ""}`}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          arrasto.current = { px: e.clientX, py: e.clientY, x: v.x, y: v.y, moveu: false };
        }}
        onPointerMove={(e) => {
          const a = arrasto.current;
          if (!a) return;
          const dx = e.clientX - a.px, dy = e.clientY - a.py;
          if (!a.moveu && Math.abs(dx) + Math.abs(dy) < 4) return;
          a.moveu = true;
          setArrastando(true);
          setV((o) => limitar(o.s, a.x + dx, a.y + dy));
        }}
        onPointerUp={(e) => {
          const a = arrasto.current;
          arrasto.current = null;
          setArrastando(false);
          // Clique solto no fundo fecha; na imagem, não (lá o duplo clique é o zoom).
          if (a && !a.moveu && !(e.target instanceof HTMLImageElement)) {
            if (escolhendo) setEscolhendo(false);
            else onClose();
          }
        }}
        onDoubleClick={(e) => {
          if (!(e.target instanceof HTMLImageElement)) return;
          const [cx, cy] = pontoNoPalco(e);
          if (ajustado) zoom(() => Math.max(1, escalaAjuste() * 2), cx, cy);
          else ajustar();
        }}
      >
        {lado && outra ? (
          <>
            <div className="absolute inset-y-0 left-0 w-1/2 overflow-hidden">{imagem(outra.src, false)}</div>
            <div className="absolute inset-y-0 right-0 w-1/2 overflow-hidden border-l border-white/10">{imagem(src, true)}</div>
          </>
        ) : (
          <>
            {imagem(src, true)}
            {outra && (
              // A cortina: a outra por cima, recortada até a barra. O recorte é da tela, não da imagem,
              // então a barra fica parada enquanto o zoom e o arrasto mexem nas duas.
              <div className="pointer-events-none absolute inset-0" style={{ clipPath: `inset(0 ${(1 - corte) * 100}% 0 0)` }}>
                {imagem(outra.src, false)}
              </div>
            )}
          </>
        )}
      </div>

      {outra && !lado && (
        <div
          className="absolute inset-y-0 z-10 w-8 -translate-x-1/2 cursor-ew-resize touch-none"
          style={{ left: `${corte * 100}%` }}
          role="slider"
          aria-label="Divisão entre antes e depois"
          aria-valuenow={Math.round(corte * 100)}
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
              e.stopPropagation();
              setCorte((c) => Math.min(1, Math.max(0, c + (e.key === "ArrowLeft" ? -0.02 : 0.02))));
            }
          }}
          onPointerDown={(e) => {
            e.stopPropagation();
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
            const r = palco.current!.getBoundingClientRect();
            setCorte(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)));
          }}
        >
          <div className="mx-auto h-full w-0.5 bg-white/90 shadow-[0_0_8px_rgba(0,0,0,.6)]" />
          <div className="absolute top-1/2 left-1/2 grid size-9 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/40 bg-black/60 text-white shadow-popover backdrop-blur">
            <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="m9 7-5 5 5 5M15 7l5 5-5 5" />
            </svg>
          </div>
        </div>
      )}
      {outra && (
        <>
          <span className={`${rotulo} left-4 max-w-[40%] truncate`} title={outra.nome}>{lado ? outra.nome : "Antes"}</span>
          <span className={`${rotulo} max-w-[40%] truncate ${lado ? "left-[calc(50%+1rem)]" : "right-4"}`} title={nome}>{lado ? nome : "Depois"}</span>
        </>
      )}

      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-start gap-3 livre-controles bg-gradient-to-b from-black/70 to-transparent pt-2 pb-10 pl-4">
        <div className="min-w-0 pt-1">
          <div className="truncate text-sm text-white/90" title={nome}>{nome}</div>
          {medida && <div className="text-[11px] text-white/50 tabular-nums">{medida[0]} × {medida[1]} px</div>}
        </div>
        <div className={`${barra} ml-auto`}>
          <a className={botao} href={src} download title="Baixar a imagem"><Download className="size-4" /></a>
          <button className={botao} onClick={onClose} title="Fechar (Esc)"><X className="size-4" /></button>
        </div>
      </div>

      <div className="pointer-events-none absolute inset-x-0 bottom-5 z-20 flex flex-col items-center gap-2">
        {escolhendo && (
          <div className={`${barra} max-w-[min(900px,calc(100%-2rem))] flex-col items-stretch gap-2 p-2`}>
            <span className="px-1 text-[11px] text-white/60">Comparar com</span>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {opcoes.map((c) => (
                <button key={c.src} onClick={() => comparar(c)} title={c.nome}
                        className={`shrink-0 overflow-hidden rounded-lg border ${outra?.src === c.src ? "border-accent" : "border-white/10 hover:border-white/40"}`}>
                  <img src={c.src} alt={c.nome} className="h-20 w-auto max-w-40 object-cover" draggable={false} />
                  <span className="block max-w-40 truncate px-1.5 py-0.5 text-left text-[10.5px] text-white/70">{c.nome}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        <div className={barra}>
          <button className={botao} onClick={() => zoom((s) => s / 1.25)} title="Diminuir (−)"><Minus className="size-4" /></button>
          <span className="w-12 text-center text-xs text-white/75 tabular-nums">{Math.round(v.s * 100)}%</span>
          <button className={botao} onClick={() => zoom((s) => s * 1.25)} title="Aumentar (+)"><Plus className="size-4" /></button>
          <span className="mx-1 h-5 w-px bg-white/15" />
          <button className={`${botao} ${ajustado ? ligado : ""}`} onClick={ajustar} title="Ajustar à tela (0)">
            <TelaCheia className="size-4" /> Ajustar
          </button>
          <button className={`${botao} ${!ajustado && Math.abs(v.s - 1) < 1e-3 ? ligado : ""}`} onClick={real}
                  title="Tamanho real, 100% (1)">
            1:1
          </button>
          {opcoes.length > 0 && (
            <>
              <span className="mx-1 h-5 w-px bg-white/15" />
              <button className={`${botao} ${outra || escolhendo ? ligado : ""}`}
                      onClick={() => (opcoes.length === 1 && !outra ? comparar(opcoes[0]) : setEscolhendo((e) => !e))}
                      title={antes ? "Comparar com a original ou com outra imagem da conversa" : "Comparar com outra imagem da conversa"}>
                <Split className="size-4" /> Comparar
              </button>
              {outra && (
                <>
                  <div className="ml-0.5 flex rounded-lg bg-white/5 p-0.5">
                    {([["deslizar", "Cortina"], ["lado", "Lado a lado"]] as const).map(([id, rot]) => (
                      <button key={id} onClick={() => { setModo(id); setAjustado(true); }}
                              className={`rounded-md px-2 py-1 text-xs ${modo === id ? "bg-white/15 text-white" : "text-white/60 hover:text-white"}`}>
                        {rot}
                      </button>
                    ))}
                  </div>
                  <button className={botao} onClick={() => comparar(null)} title="Parar de comparar"><X className="size-3.5" /></button>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

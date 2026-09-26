import { useEffect, useRef, useState, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { btnPrimary } from "./LocalPanel";

export type ModoPintura = "mascara" | "anotacao";

// Cores para marcar regiões diferentes e citar no prompt ("remove the watch in the blue circle").
const CORES: { nome: string; css: string }[] = [
  { nome: "vermelho (red)", css: "#ef4444" },
  { nome: "azul (blue)", css: "#3b82f6" },
  { nome: "verde (green)", css: "#22c55e" },
  { nome: "amarelo (yellow)", css: "#eab308" },
  { nome: "branco (white)", css: "#ffffff" },
];

/** Marca onde editar, nos dois jeitos que o Qwen-Image 2.1 entende:
 * - máscara: PNG do tamanho da original, branco onde muda e preto onde fica. Vai como a imagem
 *   seguinte à original (-r original -r máscara), e a original chega inteira;
 * - anotação: círculos ou pintura coloridos por cima da própria imagem, que passa a ser a referência.
 *   Cobre parte da original, mas deixa marcar várias regiões com cores e citá-las no prompt. */
export default function MascaraEditor(props: { src: string; onPronta: (png: Blob, modo: ModoPintura) => void; onClose: () => void }) {
  const tela = useRef<HTMLCanvasElement>(null);
  const foto = useRef<HTMLImageElement>(null);
  const ultimo = useRef<[number, number] | null>(null);
  const [modo, setModo] = useState<ModoPintura>("mascara");
  const [cor, setCor] = useState(CORES[0].css);
  const [pincel, setPincel] = useState(48);  // px da imagem original, não da tela
  const [borracha, setBorracha] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && props.onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.onClose]);

  function ponto(e: PointerEvent<HTMLCanvasElement>): [number, number] {
    const c = e.currentTarget;
    const r = c.getBoundingClientRect();
    return [((e.clientX - r.left) * c.width) / r.width, ((e.clientY - r.top) * c.height) / r.height];
  }

  function risca(e: PointerEvent<HTMLCanvasElement>) {
    const ctx = e.currentTarget.getContext("2d")!;
    const p = ponto(e);
    ctx.globalCompositeOperation = borracha ? "destination-out" : "source-over";
    ctx.strokeStyle = modo === "mascara" ? "#fff" : cor;
    ctx.lineWidth = pincel;
    ctx.lineCap = ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(...(ultimo.current ?? p));
    ctx.lineTo(...p);
    ctx.stroke();
    ultimo.current = p;
  }

  function usar() {
    const t = tela.current!;
    const m = document.createElement("canvas");
    m.width = t.width;
    m.height = t.height;
    const ctx = m.getContext("2d")!;
    if (modo === "anotacao") {
      ctx.drawImage(foto.current!, 0, 0, m.width, m.height);
      ctx.drawImage(t, 0, 0);
    } else {
      // traço de qualquer cor (feito antes no modo anotação) conta como branco
      ctx.drawImage(t, 0, 0);
      ctx.globalCompositeOperation = "source-in";
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, m.width, m.height);
      ctx.globalCompositeOperation = "destination-over";
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, m.width, m.height);
    }
    m.toBlob((b) => b && props.onPronta(b, modo), "image/png");
  }

  const botao = "rounded-md px-2 py-1 text-xs text-muted hover:bg-raised hover:text-fg";
  const ligado = "bg-raised text-fg";
  return createPortal(
    <div onClick={props.onClose} role="dialog" aria-label="Marcar a área a editar"
         className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm">
      <div onClick={(e) => e.stopPropagation()}
           className="flex max-h-full max-w-[min(1400px,100%)] flex-col overflow-hidden rounded-2xl border border-line bg-panel shadow-popover">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line px-3 py-2 text-xs">
          <button className={`${botao} ${modo === "mascara" ? ligado : ""}`} onClick={() => setModo("mascara")}
                  title="Pinte o que deve mudar: vai uma máscara separada e a imagem segue intacta">
            Máscara
          </button>
          <button className={`${botao} ${modo === "anotacao" ? ligado : ""}`} onClick={() => setModo("anotacao")}
                  title="Circule ou pinte com cores por cima da imagem e cite as cores no prompt (ex.: remove the watch in the blue circle)">
            Anotar
          </button>
          {modo === "anotacao" && (
            <div className="flex items-center gap-1">
              {CORES.map((c) => (
                <button key={c.css} title={c.nome} aria-label={c.nome} onClick={() => setCor(c.css)} style={{ background: c.css }}
                        className={`size-4 rounded-full border ${cor === c.css ? "border-fg ring-1 ring-fg" : "border-line"}`} />
              ))}
            </div>
          )}
          <span className="text-faint">
            {modo === "mascara" ? "pinte o que deve mudar; o resto fica igual" : "circule ou pinte; cite a cor no prompt"}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <label className="flex items-center gap-1 text-muted" title="Tamanho do pincel">
              Pincel
              <input type="range" min={4} max={256} value={pincel} onChange={(e) => setPincel(+e.target.value)} />
            </label>
            <button className={`${botao} ${borracha ? ligado : ""}`} onClick={() => setBorracha((v) => !v)}>
              Borracha
            </button>
            <button className={botao} onClick={() => {
              const t = tela.current!;
              t.getContext("2d")!.clearRect(0, 0, t.width, t.height);
            }}>
              Limpar
            </button>
            <button className={botao} onClick={props.onClose}>Cancelar</button>
            <button className={btnPrimary} onClick={usar}>
              {modo === "mascara" ? "Usar máscara" : "Usar anotação"}
            </button>
          </div>
        </div>
        <div className="min-h-0 overflow-auto">
          <div className="relative mx-auto w-fit">
            <img
              ref={foto}
              src={props.src}
              alt="imagem a editar"
              draggable={false}
              onLoad={(e) => {
                // a tela tem a resolução da original: a máscara sai do mesmo tamanho dela
                tela.current!.width = e.currentTarget.naturalWidth;
                tela.current!.height = e.currentTarget.naturalHeight;
              }}
              className="block max-h-[78vh] max-w-full select-none"
            />
            <canvas
              ref={tela}
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId);
                ultimo.current = null;
                risca(e);
              }}
              onPointerMove={(e) => e.buttons === 1 && risca(e)}
              onPointerUp={() => (ultimo.current = null)}
              className={`absolute inset-0 h-full w-full cursor-crosshair touch-none ${modo === "mascara" ? "opacity-60" : ""}`}
            />
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Minus, Plus, TelaCheia, Undo, X } from "./icons";

export type ModoPintura = "mascara" | "anotacao";

// Cores para marcar regiões diferentes e citar no prompt ("remove the watch in the blue circle").
const CORES: { nome: string; css: string }[] = [
  { nome: "vermelho (red)", css: "#ef4444" },
  { nome: "azul (blue)", css: "#3b82f6" },
  { nome: "verde (green)", css: "#22c55e" },
  { nome: "amarelo (yellow)", css: "#eab308" },
  { nome: "branco (white)", css: "#ffffff" },
];
// Na tela a máscara é desenhada nesta cor para aparecer em cima de qualquer imagem; na saída todo traço vira branco.
const COR_MASCARA = "#ff3d7f";
const DESFAZER_MAX = 30; // cópias da tela guardadas para o Ctrl+Z

/** Marca onde editar, nos dois jeitos que o Qwen-Image 2.1 entende:
 * - máscara: PNG do tamanho da original, branco onde muda e preto onde fica. Vai como a imagem
 *   seguinte à original (-r original -r máscara), e a original chega inteira;
 * - anotação: círculos ou pintura coloridos por cima da própria imagem, que passa a ser a referência.
 *   Cobre parte da original, mas deixa marcar várias regiões com cores e citá-las no prompt.
 *
 * Tela cheia como o visualizador: roda do mouse dá zoom no cursor, espaço + arrastar (ou o botão do meio)
 * move a imagem, e o botão esquerdo pinta. O pincel aparece como um círculo do tamanho do traço, só em
 * cima da foto; clique fora dela fecha (um traço que começa fora e entra na foto pinta normalmente).
 * Teclado: [ e ] mudam o pincel, E alterna a borracha, Ctrl+Z desfaz, 0 ajusta, Esc cancela. */
export default function MascaraEditor(props: { src: string; onPronta: (png: Blob, modo: ModoPintura) => void; onClose: () => void }) {
  const palco = useRef<HTMLDivElement>(null);
  const tela = useRef<HTMLCanvasElement>(null);
  const foto = useRef<HTMLImageElement>(null);
  const ultimo = useRef<[number, number] | null>(null);
  // o toque do botão esquerdo: começou fora da foto? já virou traço? (clique solto fora fecha o editor)
  const toque = useRef<{ x: number; y: number; fora: boolean; pintando: boolean } | null>(null);
  const historico = useRef<ImageData[]>([]);
  const [modo, setModo] = useState<ModoPintura>("mascara");
  const [cor, setCor] = useState(CORES[0].css);
  // px da imagem original, não da tela; cada modo com o seu: a máscara cobre área, a anotação é traço fino
  const [pinceis, setPinceis] = useState<Record<ModoPintura, number>>({ mascara: 48, anotacao: 4 });
  const pincel = pinceis[modo];
  const setPincel = (n: number | ((p: number) => number)) =>
    setPinceis((t) => ({ ...t, [modo]: typeof n === "function" ? n(t[modo]) : n }));
  const [borracha, setBorracha] = useState(false);
  const [medida, setMedida] = useState<[number, number] | null>(null);
  const [v, setV] = useState({ s: 1, x: 0, y: 0 });
  const [espaco, setEspaco] = useState(false);
  const [movendo, setMovendo] = useState<{ px: number; py: number; x: number; y: number } | null>(null);
  const [mira, setMira] = useState<[number, number] | null>(null); // onde está o círculo do pincel, na tela
  const [desfazeres, setDesfazeres] = useState(0);

  const ajuste = () => {
    const el = palco.current;
    return el && medida ? Math.min((el.clientWidth - 48) / medida[0], (el.clientHeight - 176) / medida[1]) : 1;
  };
  const ajustar = () => setV({ s: ajuste(), x: 0, y: -16 }); // um pouco acima: a barra de baixo é mais alta
  useLayoutEffect(() => {
    if (!medida) return;
    ajustar();
    window.addEventListener("resize", ajustar);
    return () => window.removeEventListener("resize", ajustar);
  }, [medida]); // eslint-disable-line react-hooks/exhaustive-deps

  const zoom = (fator: number, cx = 0, cy = 0) =>
    setV(({ s, x, y }) => {
      const s2 = Math.min(Math.max(s * fator, ajuste() / 2), 16);
      const k = s2 / s;
      return { s: s2, x: cx - (cx - x) * k, y: cy - (cy - y) * k };
    });
  const doCentro = (e: { clientX: number; clientY: number }) => {
    const r = palco.current!.getBoundingClientRect();
    return [e.clientX - r.left - r.width / 2, e.clientY - r.top - r.height / 2] as const;
  };

  function guardar() {
    const t = tela.current!;
    historico.current.push(t.getContext("2d")!.getImageData(0, 0, t.width, t.height));
    if (historico.current.length > DESFAZER_MAX) historico.current.shift();
    setDesfazeres(historico.current.length);
  }
  function desfazer() {
    const anterior = historico.current.pop();
    if (anterior) tela.current!.getContext("2d")!.putImageData(anterior, 0, 0);
    setDesfazeres(historico.current.length);
  }
  function limpar() {
    const t = tela.current!;
    guardar();
    t.getContext("2d")!.clearRect(0, 0, t.width, t.height);
  }

  // Os atalhos leem sempre o estado atual (o pincel e a borracha mudam enquanto a janela está aberta).
  const vivo = useRef({ desfazer, zoom, doCentro, ajustar, setPincel, onClose: props.onClose });
  vivo.current = { desfazer, zoom, doCentro, ajustar, setPincel, onClose: props.onClose };
  useEffect(() => {
    const baixo = (e: KeyboardEvent) => {
      const f = vivo.current;
      if (e.key === " ") { e.preventDefault(); setEspaco(true); }
      else if (e.key === "Escape") f.onClose();
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); f.desfazer(); }
      else if (e.key === "[") f.setPincel((p) => Math.max(4, Math.round(p / 1.2)));
      else if (e.key === "]") f.setPincel((p) => Math.min(512, Math.round(p * 1.2)));
      else if (e.key.toLowerCase() === "e") setBorracha((b) => !b);
      else if (e.key === "0") f.ajustar();
      else if (e.key === "+" || e.key === "=") f.zoom(1.25);
      else if (e.key === "-") f.zoom(1 / 1.25);
    };
    const cima = (e: KeyboardEvent) => e.key === " " && setEspaco(false);
    window.addEventListener("keydown", baixo);
    window.addEventListener("keyup", cima);
    return () => {
      window.removeEventListener("keydown", baixo);
      window.removeEventListener("keyup", cima);
    };
  }, []);
  // Roda do mouse: nativo e não passivo, para a página de trás não rolar junto.
  useEffect(() => {
    const el = palco.current!;
    const roda = (e: WheelEvent) => {
      e.preventDefault();
      const [cx, cy] = vivo.current.doCentro(e);
      vivo.current.zoom(Math.exp(-e.deltaY * 0.0015), cx, cy);
    };
    el.addEventListener("wheel", roda, { passive: false });
    return () => el.removeEventListener("wheel", roda);
  }, []);

  const naFoto = (e: { clientX: number; clientY: number }) => {
    const r = tela.current?.getBoundingClientRect();
    return !!r && e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
  };
  const comecarTraco = (e: { clientX: number; clientY: number }) => {
    guardar();
    ultimo.current = null;
    risca(e);
  };

  /** Ponto na resolução da original: o retângulo do canvas já vem com o zoom aplicado. */
  function ponto(e: { clientX: number; clientY: number }): [number, number] {
    const c = tela.current!;
    const r = c.getBoundingClientRect();
    return [((e.clientX - r.left) * c.width) / r.width, ((e.clientY - r.top) * c.height) / r.height];
  }

  function risca(e: { clientX: number; clientY: number }) {
    const ctx = tela.current!.getContext("2d")!;
    const p = ponto(e);
    ctx.globalCompositeOperation = borracha ? "destination-out" : "source-over";
    ctx.strokeStyle = ctx.fillStyle = modo === "mascara" ? COR_MASCARA : cor;
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
      // traço de qualquer cor (o rosa da tela, ou feito antes no modo anotação) conta como branco
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

  const botao = "flex h-8 min-w-8 items-center justify-center gap-1.5 rounded-lg px-2 text-xs text-white/75 transition-colors hover:bg-white/10 hover:text-white disabled:opacity-35 disabled:hover:bg-transparent";
  const ligado = "bg-white/15 text-white";
  const barra = "pointer-events-auto flex items-center gap-0.5 rounded-xl border border-white/10 bg-black/60 p-1 shadow-popover backdrop-blur-md";
  const divisor = <span className="mx-1 h-5 w-px bg-white/15" />;
  const mover = espaco || !!movendo;
  const diametro = pincel * v.s;

  return createPortal(
    <div role="dialog" aria-label="Marcar a área a editar" className="fixed inset-0 z-50 select-none">
      <div className="absolute inset-0 bg-black/85 backdrop-blur-sm" />
      <div
        ref={palco}
        className={`absolute inset-0 overflow-hidden ${movendo ? "cursor-grabbing" : mover ? "cursor-grab" : mira ? "cursor-none" : ""}`}
        onContextMenu={(e) => e.preventDefault()}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          if (espaco || e.button === 1) {
            setMovendo({ px: e.clientX, py: e.clientY, x: v.x, y: v.y });
            return;
          }
          if (e.button !== 0) return;
          const fora = !naFoto(e);
          toque.current = { x: e.clientX, y: e.clientY, fora, pintando: !fora };
          if (!fora) comecarTraco(e);
        }}
        onPointerMove={(e) => {
          setMira(naFoto(e) ? [e.clientX, e.clientY] : null);
          const t = toque.current;
          if (movendo) setV((o) => ({ ...o, x: movendo.x + e.clientX - movendo.px, y: movendo.y + e.clientY - movendo.py }));
          else if (t && e.buttons === 1) {
            // começou fora: só vira traço quando o mouse anda (um clique parado fora é fechar)
            if (!t.pintando && Math.abs(e.clientX - t.x) + Math.abs(e.clientY - t.y) > 4) {
              t.pintando = true;
              comecarTraco(e);
            } else if (t.pintando) risca(e);
          }
        }}
        onPointerUp={() => {
          const t = toque.current;
          toque.current = null;
          ultimo.current = null;
          setMovendo(null);
          if (t?.fora && !t.pintando) props.onClose();
        }}
        onPointerLeave={() => setMira(null)}
      >
        <div
          className="absolute top-1/2 left-1/2 shadow-2xl"
          style={medida ? {
            width: medida[0], height: medida[1], marginLeft: -medida[0] / 2, marginTop: -medida[1] / 2,
            transform: `translate(${v.x}px, ${v.y}px) scale(${v.s})`,
          } : { opacity: 0 }}
        >
          <img
            ref={foto}
            src={props.src}
            alt="imagem a editar"
            draggable={false}
            onLoad={(e) => {
              // a tela tem a resolução da original: a máscara sai do mesmo tamanho dela
              const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
              tela.current!.width = w;
              tela.current!.height = h;
              setMedida([w, h]);
            }}
            className="block h-full w-full max-w-none rounded-sm"
            style={{ imageRendering: v.s >= 3 ? "pixelated" : "auto" }}
          />
          <canvas ref={tela} className={`absolute inset-0 h-full w-full ${modo === "mascara" ? "opacity-55" : ""}`} />
        </div>
      </div>

      {mira && !mover && (
        // o pincel de verdade: o círculo tem o tamanho do traço no zoom de agora
        <div className="pointer-events-none absolute z-10 rounded-full border border-white/90 shadow-[0_0_0_1px_rgba(0,0,0,.6)]"
             style={{ left: mira[0] - diametro / 2, top: mira[1] - diametro / 2, width: diametro, height: diametro,
                      background: borracha ? "transparent" : `${modo === "mascara" ? COR_MASCARA : cor}33`,
                      borderStyle: borracha ? "dashed" : "solid" }} />
      )}

      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-start gap-3 livre-controles bg-gradient-to-b from-black/70 to-transparent pt-2 pb-10 pl-4">
        <div className="min-w-0 pt-1">
          <div className="text-sm text-white/90">{modo === "mascara" ? "Pinte o que deve mudar" : "Circule ou pinte e cite a cor no prompt"}</div>
          <div className="text-[11px] text-white/50">
            {modo === "mascara" ? "o resto da imagem fica igual" : "ex.: remove the watch in the blue circle"}
            {" · espaço + arrastar move · roda dá zoom · [ ] pincel · Ctrl+Z desfaz"}
          </div>
        </div>
        <div className={`${barra} ml-auto`}>
          <button className={botao} onClick={props.onClose} title="Cancelar (Esc)"><X className="size-4" /></button>
        </div>
      </div>

      <div className="pointer-events-none absolute inset-x-0 bottom-5 z-20 flex justify-center px-4">
        <div className={`${barra} flex-wrap justify-center`}>
          <div className="flex rounded-lg bg-white/5 p-0.5">
            <button onClick={() => setModo("mascara")} title="Pinte o que deve mudar: vai uma máscara separada e a imagem segue intacta"
                    className={`rounded-md px-2.5 py-1 text-xs ${modo === "mascara" ? "bg-white/15 text-white" : "text-white/60 hover:text-white"}`}>
              Máscara
            </button>
            <button onClick={() => setModo("anotacao")} title="Circule ou pinte com cores por cima da imagem e cite as cores no prompt"
                    className={`rounded-md px-2.5 py-1 text-xs ${modo === "anotacao" ? "bg-white/15 text-white" : "text-white/60 hover:text-white"}`}>
              Anotar
            </button>
          </div>
          {modo === "anotacao" && (
            <div className="ml-1 flex items-center gap-1.5 px-1">
              {CORES.map((c) => (
                <button key={c.css} title={c.nome} aria-label={c.nome} onClick={() => { setCor(c.css); setBorracha(false); }}
                        style={{ background: c.css }}
                        className={`size-5 rounded-full border-2 ${cor === c.css && !borracha ? "border-white" : "border-transparent opacity-70 hover:opacity-100"}`} />
              ))}
            </div>
          )}
          {divisor}
          <button className={`${botao} ${!borracha ? ligado : ""}`} onClick={() => setBorracha(false)} title="Pincel">Pincel</button>
          <button className={`${botao} ${borracha ? ligado : ""}`} onClick={() => setBorracha(true)} title="Borracha (E)">Borracha</button>
          <label className="flex items-center gap-2 px-2 text-xs text-white/60" title="Tamanho do pincel ([ e ])">
            <input type="range" min={4} max={512} value={pincel} onChange={(e) => setPincel(+e.target.value)} className="w-28 accent-white" />
            <span className="w-9 text-right tabular-nums text-white/75">{pincel}px</span>
          </label>
          {divisor}
          <button className={botao} onClick={desfazer} disabled={!desfazeres} title="Desfazer (Ctrl+Z)"><Undo className="size-4" /></button>
          <button className={botao} onClick={limpar} title="Apagar tudo o que foi pintado">Limpar</button>
          {divisor}
          <button className={botao} onClick={() => zoom(1 / 1.25)} title="Diminuir (−)"><Minus className="size-4" /></button>
          <span className="w-11 text-center text-xs text-white/75 tabular-nums">{Math.round(v.s * 100)}%</span>
          <button className={botao} onClick={() => zoom(1.25)} title="Aumentar (+)"><Plus className="size-4" /></button>
          <button className={botao} onClick={ajustar} title="Ajustar à tela (0)"><TelaCheia className="size-4" /></button>
          {divisor}
          <button onClick={usar}
                  className="ml-0.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg hover:brightness-110">
            {modo === "mascara" ? "Usar máscara" : "Usar anotação"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

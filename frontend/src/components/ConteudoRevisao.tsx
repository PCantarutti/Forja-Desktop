import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Edit, Play, Trash, Undo, X } from "./icons";
import { VideoPlayer, type VideoPlayerApi } from "./VideoPlayer";

// Pedir mudanças num vídeo pronto, como num editor: pausar num quadro e desenhar por cima o que mudar, marcar um
// trecho (início e fim) com um pedido, e uma mudança geral. O Claude retoma a sessão que fez o vídeo, enxerga as
// imagens marcadas e renderiza a próxima versão (a anterior fica).

const btn = "inline-flex h-[30px] items-center justify-center gap-1.5 whitespace-nowrap rounded-[7px] border border-line-strong px-2.5 text-xs text-fg hover:border-focus hover:bg-raised disabled:pointer-events-none disabled:opacity-40";
const btnPrimary = "inline-flex h-[32px] items-center justify-center gap-1.5 whitespace-nowrap rounded-[7px] bg-accent px-3 text-[12.5px] font-medium text-accent-fg hover:brightness-110 disabled:pointer-events-none disabled:opacity-40";
const campo = "w-full rounded-[8px] border border-line bg-surface px-2.5 py-1.5 text-[13px] text-fg placeholder:text-faint focus:border-focus focus:outline-none";

type Producao = { id: number; titulo: string; formato: "vertical" | "horizontal"; versao?: number };
type Pedido =
  | { tipo: "quadro"; tempo: number; comentario: string; imagem: string }
  | { tipo: "trecho"; inicio: number; fim: number; comentario: string };
type Ponto = [number, number];   // 0..1 na largura e na altura do quadro
type Traco = { cor: string; pontos: Ponto[] };

const CORES = ["#ff3b30", "#ffd60a", "#22d3ee"];
const relogio = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0").replace(".", ",")}`;

export default function ConteudoRevisao(props: {
  p: Producao;
  onFechar: () => void;
  onEnviado: () => void;
  onError: (msg: string) => void;
}) {
  const { p } = props;
  const player = useRef<VideoPlayerApi>(null);
  const [pedidos, setPedidos] = useState<Pedido[]>([]);
  const [geral, setGeral] = useState("");
  const [quadro, setQuadro] = useState<{ tempo: number; url: string } | null>(null);
  const [trecho, setTrecho] = useState<{ inicio: number | null; fim: number | null; comentario: string }>({ inicio: null, fim: null, comentario: "" });
  const [enviando, setEnviando] = useState(false);
  const vertical = p.formato !== "horizontal";
  const proxima = (p.versao ?? 1) + 1;

  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !quadro) props.onFechar(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [quadro, props.onFechar]);

  async function desenharAqui() {
    const api_ = player.current;
    if (!api_) return;
    api_.pausar();
    const tempo = api_.tempo();
    const blob = await api_.capturar();
    if (!blob) return props.onError("Não consegui capturar este quadro.");
    setQuadro({ tempo, url: URL.createObjectURL(blob) });
  }

  const marcar = (ponta: "inicio" | "fim") => setTrecho((t) => ({ ...t, [ponta]: player.current?.tempo() ?? 0 }));

  async function enviar() {
    setEnviando(true);
    try {
      await api.post(`/conteudo/producao/${p.id}/revisar`, { pedidos, geral });
      props.onEnviado();
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setEnviando(false);
    }
  }

  const trechoPronto = trecho.inicio !== null && trecho.fim !== null && trecho.comentario.trim();

  return (
    <div className="fixed inset-0 z-50 flex bg-scrim backdrop-blur-[2px]" role="dialog" aria-label="Pedir mudanças no vídeo">
      <div className="flex min-w-0 flex-1 items-center justify-center p-6">
        <VideoPlayer ref={player} src={`/api/conteudo/video/${p.id}`} fps={30} audio
                     className="rounded-xl border border-line bg-black shadow-popover"
                     style={{ aspectRatio: vertical ? 9 / 16 : 16 / 9,
                              width: vertical ? "min(100%, calc((100vh - 64px) * 9 / 16))" : "min(100%, calc((100vh - 64px) * 16 / 9))" }} />
      </div>

      <aside className="flex w-[380px] shrink-0 flex-col border-l border-line bg-panel">
        <header className="flex items-start gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-[15px] font-semibold text-fg">Pedir mudanças</h2>
            <p className="mt-0.5 truncate text-[12px] text-muted">{p.titulo}</p>
          </div>
          <button className="rounded-md p-1 text-muted hover:bg-raised hover:text-fg" onClick={props.onFechar} title="Fechar (Esc)">
            <X className="size-4" />
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-5 py-4">
          <section className="flex flex-col gap-2">
            <h3 className="font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint">Num quadro</h3>
            <p className="text-[12px] leading-relaxed text-faint">Pause onde está o que quer mudar, circule ou risque por cima e diga o que fazer.</p>
            <button className={btn} onClick={desenharAqui}><Edit className="size-4" /> Desenhar neste quadro</button>
          </section>

          <section className="flex flex-col gap-2">
            <h3 className="font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint">Num trecho</h3>
            <div className="grid grid-cols-2 gap-2">
              <button className={btn} onClick={() => marcar("inicio")}>Início{trecho.inicio !== null ? `: ${relogio(trecho.inicio)}` : ""}</button>
              <button className={btn} onClick={() => marcar("fim")}>Fim{trecho.fim !== null ? `: ${relogio(trecho.fim)}` : ""}</button>
            </div>
            <textarea className={`${campo} h-16`} value={trecho.comentario} placeholder="Ex.: deixe essa parte mais rápida, troque a animação…"
                      onChange={(e) => setTrecho((t) => ({ ...t, comentario: e.target.value }))} />
            <button className={btn} disabled={!trechoPronto}
                    onClick={() => {
                      setPedidos((ps) => [...ps, { tipo: "trecho", inicio: Math.min(trecho.inicio!, trecho.fim!),
                                                   fim: Math.max(trecho.inicio!, trecho.fim!), comentario: trecho.comentario.trim() }]);
                      setTrecho({ inicio: null, fim: null, comentario: "" });
                    }}>
              Adicionar trecho
            </button>
          </section>

          {pedidos.length > 0 && (
            <section className="flex flex-col gap-2">
              <h3 className="font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint">Pedidos ({pedidos.length})</h3>
              <ul className="flex flex-col gap-2">
                {pedidos.map((x, i) => (
                  <li key={i} className="flex gap-2.5 rounded-lg border border-line bg-surface p-2">
                    {x.tipo === "quadro" ? (
                      <img src={x.imagem} alt="" className="h-24 w-auto shrink-0 rounded-md border border-line object-contain" />
                    ) : (
                      <div className="flex h-16 w-12 shrink-0 flex-col items-center justify-center rounded-md border border-line font-mono text-[10px] text-muted">
                        <span>{relogio(x.inicio)}</span><span className="text-faint">até</span><span>{relogio(x.fim)}</span>
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="font-mono text-[11px] text-faint">{x.tipo === "quadro" ? `quadro em ${relogio(x.tempo)}` : "trecho"}</div>
                      <p className="text-[12.5px] leading-snug text-fg">{x.comentario}</p>
                    </div>
                    <button className="self-start rounded-md p-1 text-faint hover:bg-raised hover:text-fg" title="Tirar este pedido"
                            onClick={() => setPedidos((ps) => ps.filter((_, j) => j !== i))}>
                      <Trash className="size-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="flex flex-col gap-2">
            <h3 className="font-mono text-[10.5px] font-medium uppercase tracking-[.08em] text-faint">No vídeo todo</h3>
            <textarea className={`${campo} h-20`} value={geral} placeholder="Ex.: música mais baixa, legenda maior, cores mais quentes…"
                      onChange={(e) => setGeral(e.target.value)} />
          </section>
        </div>

        <footer className="flex flex-col gap-2 border-t border-line px-5 py-4">
          <button className={btnPrimary} disabled={enviando || (!pedidos.length && !geral.trim())} onClick={enviar}>
            <Play className="size-4" /> Fazer a versão {proxima}
          </button>
          <p className="text-[11.5px] leading-relaxed text-faint">
            O Claude retoma a sessão que fez o vídeo, muda só o que foi pedido e entrega a versão {proxima}; a atual continua guardada.
          </p>
        </footer>
      </aside>

      {quadro && (
        <Desenho url={quadro.url} tempo={quadro.tempo}
                 onCancelar={() => { URL.revokeObjectURL(quadro.url); setQuadro(null); }}
                 onPronto={(imagem, comentario) => {
                   setPedidos((ps) => [...ps, { tipo: "quadro", tempo: quadro.tempo, comentario, imagem }]);
                   URL.revokeObjectURL(quadro.url);
                   setQuadro(null);
                 }} />
      )}
    </div>
  );
}

function Desenho(props: { url: string; tempo: number; onCancelar: () => void; onPronto: (imagem: string, comentario: string) => void }) {
  const img = useRef<HTMLImageElement>(null);
  const tela = useRef<HTMLCanvasElement>(null);
  const [tracos, setTracos] = useState<Traco[]>([]);
  const [cor, setCor] = useState(CORES[0]);
  const [comentario, setComentario] = useState("");
  const atual = useRef<Traco | null>(null);

  const redesenhar = useCallback(() => {
    const c = tela.current, i = img.current;
    if (!c || !i) return;
    c.width = i.clientWidth;
    c.height = i.clientHeight;
    pintar(c.getContext("2d")!, c.width, c.height, atual.current ? [...tracos, atual.current] : tracos);
  }, [tracos]);
  useEffect(() => { redesenhar(); }, [redesenhar]);
  useEffect(() => {
    window.addEventListener("resize", redesenhar);
    return () => window.removeEventListener("resize", redesenhar);
  }, [redesenhar]);

  const ponto = (e: React.PointerEvent): Ponto => {
    const r = tela.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  };

  function pronto() {
    const i = img.current!;
    const c = document.createElement("canvas");
    c.width = i.naturalWidth;
    c.height = i.naturalHeight;
    const ctx = c.getContext("2d")!;
    ctx.drawImage(i, 0, 0);
    pintar(ctx, c.width, c.height, tracos);
    props.onPronto(c.toDataURL("image/png"), comentario.trim());
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center gap-6 bg-black/80 p-6" role="dialog" aria-label="Desenhar no quadro">
      <div className="relative">
        <img ref={img} src={props.url} alt="" onLoad={redesenhar} draggable={false}
             className="block max-h-[calc(100vh-48px)] max-w-[60vw] select-none rounded-lg" />
        <canvas ref={tela} className="absolute inset-0 cursor-crosshair touch-none rounded-lg"
                onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); atual.current = { cor, pontos: [ponto(e)] }; redesenhar(); }}
                onPointerMove={(e) => { if (atual.current) { atual.current.pontos.push(ponto(e)); redesenhar(); } }}
                onPointerUp={() => { if (atual.current) { const t = atual.current; atual.current = null; setTracos((ts) => [...ts, t]); } }} />
      </div>
      <div className="flex w-[320px] flex-col gap-3 rounded-xl border border-line bg-panel p-4">
        <div>
          <h3 className="text-[14px] font-semibold text-fg">Quadro em {relogio(props.tempo)}</h3>
          <p className="mt-0.5 text-[12px] text-muted">Circule ou risque o que deve mudar.</p>
        </div>
        <div className="flex items-center gap-2">
          {CORES.map((c) => (
            <button key={c} onClick={() => setCor(c)} title="Cor da caneta" aria-pressed={cor === c}
                    className={`size-7 rounded-full border-2 ${cor === c ? "border-fg" : "border-transparent"}`} style={{ background: c }} />
          ))}
          <button className={`${btn} ml-auto px-2`} disabled={!tracos.length} onClick={() => setTracos((t) => t.slice(0, -1))} title="Desfazer o último traço">
            <Undo className="size-4" />
          </button>
        </div>
        <textarea className={`${campo} h-24`} autoFocus value={comentario} placeholder="O que mudar aqui? Ex.: troque este ícone por um carro de polícia"
                  onChange={(e) => setComentario(e.target.value)} />
        <div className="flex gap-2">
          <button className={`${btn} flex-1`} onClick={props.onCancelar}>Cancelar</button>
          <button className={`${btnPrimary} flex-1`} disabled={!comentario.trim()} onClick={pronto}>Adicionar</button>
        </div>
      </div>
    </div>
  );
}

function pintar(ctx: CanvasRenderingContext2D, w: number, h: number, tracos: Traco[]) {
  ctx.clearRect(0, 0, w, h);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = Math.max(3, Math.round(w / 110));   // a mesma espessura relativa na tela e no PNG do tamanho real
  for (const t of tracos) {
    ctx.strokeStyle = t.cor;
    ctx.beginPath();
    t.pontos.forEach(([x, y], i) => (i ? ctx.lineTo(x * w, y * h) : ctx.moveTo(x * w, y * h)));
    if (t.pontos.length === 1) ctx.lineTo(t.pontos[0][0] * w + 0.1, t.pontos[0][1] * h);
    ctx.stroke();
  }
}

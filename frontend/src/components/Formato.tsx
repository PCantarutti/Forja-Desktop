import { Fragment, useEffect, useState } from "react";
import { Caixa, numeroCaixa } from "./ImagensView";
import { Trocar } from "./icons";
import { outroLado, razaoSimples, tamanhoNaRazao } from "./videoConta";
import { FIGURAS, caminho, retangulo } from "./formasLivre";

/** Um botão de formato: o id é a proporção ("16:9") e w/h o tamanho do desenho (as medidas do protótipo). */
export type Forma = { id: string; w: number; h: number };
/** Uma resolução do seletor ("720p", "1024"). `apagada`: fora do que o modelo aguenta bem (o título explica). */
export type Qualidade = { id: string; titulo?: string; apagada?: boolean };

// As distrações do tracejado do Livre parado. `figura`: vira outra forma geométrica (morph do path);
// `classe`: um keyframe de index.css (sozinho ou por cima da figura: a estrela gira, a bola quica).
type Truque = { figura?: string; classe?: string; dura: number };
const TRUQUES: Truque[] = [
  { figura: "estrela", classe: "livre-gira", dura: 2600 },
  { figura: "bola", classe: "livre-pulo", dura: 2600 },
  { figura: "octogono", classe: "livre-onda", dura: 2600 },
  { figura: "cubo", dura: 2400 },
  { figura: "paralelepipedo", dura: 2400 },
  { figura: "triangulo", classe: "livre-achata", dura: 2500 },
  { figura: "losango", classe: "livre-pisca", dura: 2500 },
  { classe: "livre-onda", dura: 1300 },
  { classe: "livre-achata", dura: 1000 },
  { classe: "livre-pisca", dura: 1100 },
  { classe: "livre-pulo", dura: 1000 },
];
const CLASSE_DURA: Record<string, number> = { "livre-gira": 1400, "livre-pulo": 1000, "livre-onda": 1300, "livre-achata": 1000, "livre-pisca": 1100 };

/** Sorteia um truque a cada 4–9 s, nunca o mesmo duas vezes seguidas; `quieto` para tudo na hora. */
function useTruque(quieto: boolean): { figura: string; classe: string } {
  const [estado, setEstado] = useState({ figura: "", classe: "" });
  useEffect(() => {
    const limpa = { figura: "", classe: "" };
    if (quieto || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return setEstado(limpa);
    let vivo = true, ultimo = -1;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const depois = (ms: number, f: () => void) => timers.push(setTimeout(() => vivo && f(), ms));
    const proximo = () =>
      depois(4000 + Math.random() * 5000, () => {
        let i = Math.floor(Math.random() * TRUQUES.length);
        if (i === ultimo) i = (i + 1) % TRUQUES.length;
        ultimo = i;
        const t = TRUQUES[i];
        if (t.figura) {
          // vira a figura, faz a graça dela no meio, e volta a ser retângulo
          setEstado({ figura: t.figura, classe: "" });
          if (t.classe) {
            depois(650, () => setEstado({ figura: t.figura!, classe: t.classe! }));
            depois(650 + CLASSE_DURA[t.classe], () => setEstado({ figura: t.figura!, classe: "" }));
          }
          depois(t.dura, () => setEstado(limpa));
          depois(t.dura + 700, proximo);
        } else {
          setEstado({ figura: "", classe: t.classe! });
          depois(t.dura, () => setEstado(limpa));
          depois(t.dura + 100, proximo);
        }
      });
    proximo();
    return () => {
      vivo = false;
      timers.forEach(clearTimeout);
      setEstado(limpa);
    };
  }, [quieto]);
  return estado;
}

/** O desenho do Livre: um path tracejado de N pontos. Ativo, é o retângulo da proporção escolhida (e
 *  acompanha a proporção); parado, de vez em quando vira outra figura. */
function IconeLivre(props: { ativo: boolean; razao: [number, number]; quieto: boolean }) {
  const { figura, classe } = useTruque(props.quieto);
  const escala = Math.min(26 / props.razao[0], 20 / props.razao[1]);
  const base = props.ativo ? retangulo(props.razao[0] * escala - 1.5, props.razao[1] * escala - 1.5) : retangulo(22, 15);
  const f = figura ? FIGURAS[figura] : null;
  const d = caminho(f ? f.pontos : base);
  // o traço de dentro (cubo, paralelepípedo) some junto com a figura
  const [dentro, setDentro] = useState("");
  useEffect(() => {
    if (f?.dentro) setDentro(f.dentro);
  }, [f?.dentro]);
  const mola = figura || !props.ativo ? "d .6s cubic-bezier(.34, 1.56, .64, 1)" : "d .2s cubic-bezier(.2, 0, 0, 1)";
  const traco = { fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeDasharray: "3 2", strokeLinejoin: "round" as const };
  return (
    <svg viewBox="0 0 26 20" width={26} height={20} className={`overflow-visible ${classe}`} aria-hidden>
      <path {...traco} style={{ d: `path("${d}")`, transition: mola } as React.CSSProperties} />
      {dentro && (
        <path {...traco} d={dentro} style={{ opacity: f?.dentro ? 1 : 0, transition: "opacity .35s" }} />
      )}
    </svg>
  );
}

const razaoDe = (id: string) => {
  const [a, b] = id.split(":").map(Number);
  return a / b;
};
const par = (id: string) => id.split(":").map(Number) as [number, number];

/**
 * Formato do painel Parâmetros (Imagem e Vídeo): os formatos com desenho, o Livre (proporção a:b com
 * o desenho mudando de forma), as resoluções e o tamanho personalizado travado na proporção.
 * Quem usa diz o tamanho de cada formato × resolução (`tamanhoPara`) e qual combinação está valendo.
 */
export default function SeletorFormato(props: {
  w: number;
  h: number;
  mult: number;
  formas: Forma[];
  quals: Qualidade[];
  tamanhoPara: (forma: string, qual: string) => [number, number];
  prop: string | null; // o formato que bate com o tamanho (ou quase)
  qual: string | null; // a resolução que bate com o tamanho
  dicaQuals?: string;
  onTamanho: (w: number, h: number) => void;
}) {
  const [livre, setLivre] = useState(false);
  const livreAtivo = livre || !props.prop;
  const [sobreLivre, setSobreLivre] = useState(false);
  const [razaoLivre, setRazaoLivre] = useState<[number, number]>(() => (props.prop ? par(props.prop) : razaoSimples(props.w, props.h)));
  const mudaRazao = (a: number, b: number) => {
    const ra = Math.max(1, Math.min(64, Math.round(a) || 1)), rb = Math.max(1, Math.min(64, Math.round(b) || 1));
    setRazaoLivre([ra, rb]);
    props.onTamanho(...tamanhoNaRazao(props.w, props.h, ra, rb, props.mult));
  };
  const base = props.formas[0].id;
  /** Resolução no Livre: o lado menor vai para o da resolução e a proporção atual fica. */
  const qualidadeLivre = (q: string) => {
    const menor = Math.min(...props.tamanhoPara(base, q));
    const r = props.w / props.h;
    const snap = (v: number) => Math.max(props.mult, Math.round(v / props.mult) * props.mult);
    props.onTamanho(...(r >= 1 ? [snap(menor * r), snap(menor)] : [snap(menor), snap(menor / r)]) as [number, number]);
  };
  const aceso = "border-accent-line bg-accent-soft text-accent-text";
  const apagado = "border-line text-muted hover:border-focus hover:text-fg";

  return (
    <>
      <div className="grid gap-1.5" style={{ gridTemplateColumns: `repeat(${props.formas.length + 1}, minmax(0, 1fr))` }}>
        {props.formas.map((f) => (
          <button key={f.id} onClick={() => { setLivre(false); props.onTamanho(...props.tamanhoPara(f.id, props.qual ?? props.quals[0].id)); }}
                  className={`flex flex-col items-center gap-[5px] rounded-[9px] border pt-2 pb-1.5 ${!livreAtivo && props.prop === f.id ? aceso : apagado}`}>
            <span className="flex h-5 items-center justify-center">
              <span className="block rounded-[3px] border-[1.5px] border-current" style={{ width: f.w, height: f.h }} />
            </span>
            <span className="font-mono text-[10.5px]">{f.id}</span>
          </button>
        ))}
        <button onClick={() => { if (!livreAtivo) setRazaoLivre(props.prop ? par(props.prop) : razaoSimples(props.w, props.h)); setLivre(true); }}
                title="Personalizado: escolha uma proporção qualquer (5:7, 3:2…)"
                onPointerEnter={() => setSobreLivre(true)} onPointerLeave={() => setSobreLivre(false)}
                className={`flex flex-col items-center gap-[5px] rounded-[9px] border pt-2 pb-1.5 ${livreAtivo ? aceso : apagado}`}>
          <span className="flex h-5 items-center justify-center">
            <IconeLivre ativo={livreAtivo} razao={razaoLivre} quieto={livreAtivo || sobreLivre} />
          </span>
          <span className={livreAtivo ? "font-mono text-[10.5px]" : "text-[10.5px]"}>{livreAtivo ? `${razaoLivre[0]}:${razaoLivre[1]}` : "Livre"}</span>
        </button>
      </div>
      {livreAtivo && (
        <div className="flex items-center justify-center">
          <div className="flex items-center gap-1.5" title="Proporção personalizada (largura : altura)">
            {[0, 1].map((i) => (
              <Fragment key={i}>
                {i === 1 && <span className="font-mono text-faint">:</span>}
                <label data-arrasta data-passo={1} className="rounded-[8px] border border-line bg-surface px-2 py-1 focus-within:border-focus">
                  <input type="number" min={1} max={64} step={1} value={razaoLivre[i]} aria-label={i === 0 ? "Proporção: largura" : "Proporção: altura"}
                         onChange={(e) => mudaRazao(i === 0 ? Number(e.target.value) : razaoLivre[0], i === 1 ? Number(e.target.value) : razaoLivre[1])}
                         className={`${numeroCaixa} w-8 text-center`} />
                </label>
              </Fragment>
            ))}
            <button onClick={() => mudaRazao(razaoLivre[1], razaoLivre[0])} title="Inverter (retrato ↔ paisagem)" aria-label="Inverter a proporção"
                    className="grid size-7 place-items-center rounded-[7px] text-faint hover:bg-raised hover:text-fg">
              <Trocar className="size-3.5" />
            </button>
          </div>
        </div>
      )}
      <div className="flex rounded-[8px] border border-line bg-surface p-0.5 text-xs" title={props.dicaQuals}>
        {props.quals.map((q) => (
          <button key={q.id} onClick={() => (livreAtivo ? qualidadeLivre(q.id) : props.onTamanho(...props.tamanhoPara(props.prop ?? base, q.id)))}
                  title={q.titulo ?? props.tamanhoPara(props.prop ?? base, q.id).join(" × ")}
                  className={`flex-1 rounded-[6px] py-1 ${props.qual === q.id && !livreAtivo ? "bg-raised text-fg" : q.apagada ? "text-faint hover:text-muted" : "text-muted hover:text-fg"}`}>
            {q.id}
          </button>
        ))}
      </div>
      <TamanhoPersonalizado w={props.w} h={props.h} passo={props.mult}
                            razao={livreAtivo ? razaoLivre[0] / razaoLivre[1] : props.prop ? razaoDe(props.prop) : null}
                            rotulo={livreAtivo ? `${razaoLivre[0]}:${razaoLivre[1]}` : props.prop ?? ""}
                            onAplicar={props.onTamanho} />
    </>
  );
}

/** Tamanho livre. Com uma proporção valendo (e travada), mexer num lado calcula o outro na hora; os dois
 *  vão para o múltiplo do modelo ao confirmar (Enter ou sair do campo), para não brigar com quem digita. */
function TamanhoPersonalizado(props: { w: number; h: number; passo: number; razao: number | null; rotulo: string; onAplicar: (w: number, h: number) => void }) {
  const [w, setW] = useState(String(props.w));
  const [h, setH] = useState(String(props.h));
  const [travada, setTravada] = useState(true);
  useEffect(() => {
    setW(String(props.w));
    setH(String(props.h));
  }, [props.w, props.h]);
  const razao = travada ? props.razao : null;
  const encaixa = (v: number) => Math.min(3840, Math.max(props.passo * 8, Math.round((v || 0) / props.passo) * props.passo));
  const muda = (eixo: "w" | "h", v: string) => {
    const n = v.replace(/\D/g, "");
    if (eixo === "w") {
      setW(n);
      if (razao && Number(n)) setH(String(outroLado(Number(n), razao, "w", props.passo)));
    } else {
      setH(n);
      if (razao && Number(n)) setW(String(outroLado(Number(n), razao, "h", props.passo)));
    }
  };
  const aplicar = () => props.onAplicar(encaixa(Number(w)), encaixa(Number(h)));
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-muted">Personalizada</span>
        {props.razao && (
          <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-[11px] text-faint" title="Mexer num lado calcula o outro pela proporção">
            <input type="checkbox" checked={travada} onChange={(e) => setTravada(e.target.checked)} className="accent-[var(--accent)]" />
            travar em {props.rotulo}
          </label>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <Caixa rotulo="Largura" passo={props.passo}>
          <input aria-label="Largura" inputMode="numeric" value={w} onChange={(e) => muda("w", e.target.value)}
                 onBlur={aplicar} onKeyDown={(e) => e.key === "Enter" && aplicar()} className={numeroCaixa} />
        </Caixa>
        <span className="flex shrink-0 flex-col items-center leading-none" title={`Os dois lados vão para múltiplos de ${props.passo} (o que o modelo pede)`}>
          <span className="font-mono text-[9.5px] text-faint">{props.passo}</span>
          <span className="text-faint">×</span>
        </span>
        <Caixa rotulo="Altura" passo={props.passo}>
          <input aria-label="Altura" inputMode="numeric" value={h} onChange={(e) => muda("h", e.target.value)}
                 onBlur={aplicar} onKeyDown={(e) => e.key === "Enter" && aplicar()} className={numeroCaixa} />
        </Caixa>
      </div>
    </div>
  );
}

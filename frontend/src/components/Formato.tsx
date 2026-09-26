import { Fragment, useEffect, useState } from "react";
import { Caixa, numeroCaixa } from "./ImagensView";
import { Trocar } from "./icons";
import { outroLado, razaoSimples, tamanhoNaRazao } from "./videoConta";

/** Um botão de formato: o id é a proporção ("16:9") e w/h o tamanho do desenho (as medidas do protótipo). */
export type Forma = { id: string; w: number; h: number };
/** Uma resolução do seletor ("720p", "1024"). `apagada`: fora do que o modelo aguenta bem (o título explica). */
export type Qualidade = { id: string; titulo?: string; apagada?: boolean };

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
  const [razaoLivre, setRazaoLivre] = useState<[number, number]>(() => (props.prop ? par(props.prop) : razaoSimples(props.w, props.h)));
  const mudaRazao = (a: number, b: number) => {
    const ra = Math.max(1, Math.min(64, Math.round(a) || 1)), rb = Math.max(1, Math.min(64, Math.round(b) || 1));
    setRazaoLivre([ra, rb]);
    props.onTamanho(...tamanhoNaRazao(props.w, props.h, ra, rb, props.mult));
  };
  // desenho tracejado do Livre: a proporção cabe numa caixa de 26×20 e muda de forma com transição
  const escala = Math.min(26 / razaoLivre[0], 20 / razaoLivre[1]);
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
                className={`flex flex-col items-center gap-[5px] rounded-[9px] border pt-2 pb-1.5 ${livreAtivo ? aceso : apagado}`}>
          <span className="flex h-5 items-center justify-center">
            <span className="block rounded-[3px] border-[1.5px] border-dashed border-current transition-[width,height] duration-200 ease-[cubic-bezier(.2,0,0,1)]"
                  style={livreAtivo ? { width: razaoLivre[0] * escala, height: razaoLivre[1] * escala } : { width: 22, height: 15 }} />
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

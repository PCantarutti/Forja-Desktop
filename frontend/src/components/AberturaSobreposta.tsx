import { useEffect, useRef, useState } from "react";
import Abertura from "./Abertura";

type Voo = { clone: HTMLElement; caixa: DOMRect; logo: DOMRect; area: DOMRect };

/** Abertura no primeiro envio de uma tela vazia. `saudacao` vai na raiz da Saudacao; `disparar()` no envio.
 *  A saudação vira uma cópia por cima (o texto some em fade) e a logo cresce um pouco e desce ao centro da
 *  área enquanto toca a abertura. É uma camada fixa, sem fundo e sem ponteiro: a conversa (ou o lote) já
 *  aparece por baixo e o processamento não espera nada. */
export function useAbertura() {
  const saudacao = useRef<HTMLDivElement>(null);
  const [voo, setVoo] = useState<Voo | null>(null);
  const disparar = () => {
    const el = saudacao.current;
    const logo = el?.querySelector("[data-logo]");
    if (!el || !logo) return;
    const area = (el.closest(".overflow-y-auto") ?? el.parentElement ?? el).getBoundingClientRect();
    setVoo({ clone: el.cloneNode(true) as HTMLElement, caixa: el.getBoundingClientRect(), logo: logo.getBoundingClientRect(), area });
  };
  return { saudacao, disparar, voo, fim: () => setVoo(null) };
}

export default function AberturaSobreposta({ voo, onFim }: { voo: Voo; onFim: () => void }) {
  const texto = useRef<HTMLDivElement>(null);
  const logo = useRef<HTMLDivElement>(null);
  const [tocando, setTocando] = useState(true);

  useEffect(() => {
    // a cópia da saudação, sem a logo (a que voa é outra), some em fade
    const t = texto.current;
    if (t) {
      voo.clone.querySelector<HTMLElement>("[data-logo]")?.style.setProperty("visibility", "hidden");
      voo.clone.style.margin = "0";
      t.replaceChildren(voo.clone);
      t.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, easing: "ease-out", fill: "forwards" });
    }
    // a logo desce ao centro da área e cresce um pouco
    const { logo: l, area: a } = voo;
    const dx = a.left + a.width / 2 - (l.left + l.width / 2), dy = a.top + a.height / 2 - (l.top + l.height / 2);
    logo.current?.animate([{ transform: "none" }, { transform: `translate(${dx}px, ${dy}px) scale(1.3)` }],
      { duration: 520, easing: "cubic-bezier(.2, 0, 0, 1)", fill: "forwards" });
  }, [voo]);

  const acabou = () => {
    setTocando(false);
    const el = logo.current;
    if (!el) return onFim();
    el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 300, easing: "ease-in", fill: "forwards" }).finished.then(onFim, onFim);
  };

  return (
    <div className="pointer-events-none fixed inset-0 z-30" aria-hidden>
      <div ref={texto} className="absolute" style={{ left: voo.caixa.left, top: voo.caixa.top, width: voo.caixa.width }} />
      <div ref={logo} className="absolute text-fg"
           style={{ left: voo.logo.left, top: voo.logo.top, width: voo.logo.width, height: voo.logo.height }}>
        <Abertura className="size-full" onFim={tocando ? acabou : () => {}} />
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { PECAS } from "./aberturaPecas";

// Animação de abertura do Forja Mobile (src/Abertura.tsx), só com a marca (sem o texto FORJA): o martelo
// sobe devagar, segura e desce de uma vez; no impacto a cena treme, a bigorna afunda, sai clarão + onda
// de choque e as faíscas explodem; o círculo fecha no sentido horário. Mesmo roteiro, dirigido pelo relógio.
// Fica no viewBox do logo (o mesmo do LogoMark) com overflow visível: o martelo erguido passa do topo.
const VEL = 1.25;  // 25% mais rápida que a do app mobile
const DUR = 2.6, T0 = 1.44, PIV = [318, 213] as const, IMP = [652, 496] as const;

const clamp = (x: number) => Math.max(0, Math.min(1, x));
const seg = (t: number, a: number, b: number) => clamp((t - a) / (b - a));
const easeOut = (x: number) => 1 - Math.pow(1 - x, 3);
const easeIn = (x: number) => x * x * x * x;
const easeInOut = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);

/** Ângulo do martelo: sobe devagar até -72°, segura com tremor, desce em 100 ms e repica. */
function angulo(t: number) {
  if (t < 0.15) return -8;
  if (t < 1.2) return -8 - 64 * easeInOut(seg(t, 0.15, 1.2));
  if (t < 1.34) return -72 - 1.5 * Math.sin(seg(t, 1.2, 1.34) * Math.PI);
  if (t < T0) return -72 * (1 - easeIn(seg(t, 1.34, T0)));
  const r = seg(t, T0, 1.75);
  return -11 * Math.sin(r * Math.PI) * Math.pow(1 - r, 0.6);
}
const gira = (a: number, dy = 0) => `rotate(${a} ${PIV[0]} ${PIV[1]}) translate(0 ${dy})`;

export default function Abertura({ className, onFim }: { className?: string; onFim: () => void }) {
  const [t, setT] = useState(0);
  const fim = useRef(onFim);
  fim.current = onFim;
  useEffect(() => {
    // "reduzir movimento": pula direto para o fim
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return void fim.current();
    let raf = 0;
    const t0 = performance.now();
    const passo = () => {
      const s = ((performance.now() - t0) / 1000) * VEL;
      setT(Math.min(s, DUR));
      if (s < DUR) raf = requestAnimationFrame(passo);
      else fim.current();
    };
    raf = requestAnimationFrame(passo);
    return () => cancelAnimationFrame(raf);
  }, []);

  const a = easeOut(seg(t, 0, 0.35));
  const imp = t >= T0 ? seg(t, T0, 1.72) : 0, dec = Math.pow(1 - imp, 2);
  const vivo = imp > 0 && imp < 1;
  const sx = vivo ? Math.sin(t * 95) * 14 * dec : 0, sy = vivo ? Math.cos(t * 120) * 10 * dec : 0;
  const afunda = vivo ? Math.sin(Math.min(1, imp * 2.2) * Math.PI) * 16 : 0;
  const ang = angulo(t);
  const golpe = t > 1.34 && t < T0 + 0.05;
  const fo = seg(t, T0, T0 + 0.28), oo = seg(t, T0, T0 + 0.5);
  const f = seg(t, T0, T0 + 0.55);
  const kf = f <= 0 ? 0 : f < 0.35 ? easeOut(f / 0.35) * 1.6 : 1.6 - 0.6 * easeInOut((f - 0.35) / 0.65);
  const circ = easeInOut(seg(t, 1.55, 2.45));

  return (
    <svg viewBox="228.5 137.5 802 718" className={className} overflow="visible" role="img" aria-label="Forja">
      <defs>
        <mask id="abertura-m" maskUnits="userSpaceOnUse" x="0" y="-400" width="2000" height="2000">
          <circle cx={622} cy={514} r={357} fill="none" stroke="#fff" strokeWidth={80} transform="rotate(128 622 514)"
                  strokeDasharray="2243 2243" strokeDashoffset={2243 * (1 - circ)} />
        </mask>
        <radialGradient id="abertura-brilho">
          <stop offset="0" stopColor="currentColor" stopOpacity={0.9} />
          <stop offset="1" stopColor="currentColor" stopOpacity={0} />
        </radialGradient>
      </defs>
      <g transform={`translate(${sx} ${sy})`} fill="currentColor">
        <g mask="url(#abertura-m)"><path d={PECAS.arcos} /></g>
        {t >= T0 && <circle cx={IMP[0]} cy={IMP[1]} r={60 + 320 * easeOut(fo)} fill="url(#abertura-brilho)" opacity={0.55 * (1 - fo)} />}
        {t >= T0 && (
          <circle cx={IMP[0]} cy={IMP[1]} r={30 + 460 * easeOut(oo)} fill="none" stroke="currentColor"
                  strokeWidth={14 * (1 - oo) + 1} opacity={0.7 * (1 - oo)} />
        )}
        <path d={PECAS.bigorna} opacity={a} transform={`translate(0 ${(1 - a) * 40 + afunda})`} />
        {kf > 0 && PECAS.faiscas.map((d, i) => (
          <path key={i} d={d} opacity={clamp(f * 6)}
                transform={`translate(${IMP[0]} ${IMP[1]}) scale(${kf}) translate(${-IMP[0]} ${-IMP[1]})`} />
        ))}
        {golpe && [1, 2, 3, 4].map((k) => (  // rastro do golpe: o martelo nos instantes de antes, cada vez mais apagado
          <path key={k} fillRule="evenodd" d={PECAS.martelo} opacity={0.28 / k} transform={gira(angulo(t - k * 0.012))} />
        ))}
        <path fillRule="evenodd" d={PECAS.martelo} opacity={easeOut(seg(t, 0.05, 0.3))} transform={gira(ang, afunda)} />
      </g>
    </svg>
  );
}

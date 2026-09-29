import { useMemo } from "react";

// Mapa do fluxo do protótipo: cada tela é um nó, cada botão/link com data-ir é uma seta até a tela que
// ele abre. Lido do próprio HTML (DOMParser não roda script), então vale para qualquer versão.

type Aresta = { de: string; para: string; rotulos: string[] };

export function lerFluxo(html: string): { telas: string[]; arestas: Aresta[] } {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const els = [...doc.querySelectorAll("body > [data-tela]")];
  const telas = els.map((e) => e.getAttribute("data-section") || "").filter(Boolean);
  const mapa = new Map<string, Aresta>();
  for (const el of els) {
    const de = el.getAttribute("data-section") || "";
    for (const b of el.querySelectorAll("[data-ir]")) {
      const para = b.getAttribute("data-ir") || "";
      if (!telas.includes(para) || para === de) continue;
      const k = `${de}>${para}`;
      const a = mapa.get(k) ?? { de, para, rotulos: [] };
      // rótulo só com letras: botão de ícone ("←", "+") cai no aria-label/title ou fica sem rótulo
      const r = [b.textContent, b.getAttribute("aria-label"), b.getAttribute("title")]
        .map((t) => (t || "").replace(/\s+/g, " ").trim()).find((t) => /\p{L}{2}/u.test(t))?.slice(0, 24);
      if (r && !a.rotulos.includes(r)) a.rotulos.push(r);
      mapa.set(k, a);
    }
  }
  return { telas, arestas: [...mapa.values()] };
}

const W = 150, H = 54, GX = 90, GY = 80;

export default function DesignFluxo(props: { html: string; atual: string; onIr: (tela: string) => void; onFechar: () => void }) {
  const { telas, arestas } = useMemo(() => lerFluxo(props.html), [props.html]);
  const cols = Math.max(1, Math.ceil(Math.sqrt(telas.length)));
  const pos = (i: number) => ({ x: 40 + (i % cols) * (W + GX), y: 40 + Math.floor(i / cols) * (H + GY) });
  const largura = 80 + cols * W + (cols - 1) * GX;
  const altura = 80 + Math.ceil(telas.length / cols) * H + (Math.ceil(telas.length / cols) - 1) * GY;
  const semSaida = telas.filter((t) => !arestas.some((a) => a.de === t));
  const semEntrada = telas.slice(1).filter((t) => !arestas.some((a) => a.para === t));

  return (
    <div className="flex size-full flex-col overflow-auto rounded-lg border border-line bg-surface p-3">
      <div className="mb-2 flex items-center gap-2 text-xs text-muted">
        <span className="font-mono text-[10.5px] tracking-[.08em] text-faint uppercase">Fluxo do protótipo</span>
        <span>{telas.length} telas · {arestas.length} ligações · clique numa tela para abri-la</span>
        <span className="flex-1" />
        <button onClick={props.onFechar} className="rounded-lg border border-line px-2 py-0.5 text-fg hover:bg-raised">Ver a tela</button>
      </div>
      <svg width={largura} height={altura} className="shrink-0" role="img" aria-label="Mapa das telas e ligações">
        <defs>
          <marker id="seta-fluxo" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0 10 5 0 10z" fill="var(--color-accent)" />
          </marker>
        </defs>
        {arestas.map((a) => {
          const p1 = pos(telas.indexOf(a.de)), p2 = pos(telas.indexOf(a.para));
          const [x1, y1, x2, y2] = [p1.x + W / 2, p1.y + H / 2, p2.x + W / 2, p2.y + H / 2];
          // sai e chega pela borda do nó, não pelo centro
          const ang = Math.atan2(y2 - y1, x2 - x1), rx = W / 2 + 4, ry = H / 2 + 4;
          const t = Math.min(rx / Math.abs(Math.cos(ang) || 1e-6), ry / Math.abs(Math.sin(ang) || 1e-6));
          const [ax, ay, bx, by] = [x1 + Math.cos(ang) * t, y1 + Math.sin(ang) * t, x2 - Math.cos(ang) * t, y2 - Math.sin(ang) * t];
          const volta = arestas.some((o) => o.de === a.para && o.para === a.de);
          const curva = volta ? 26 : 0;   // ida e volta: duas curvas, uma de cada lado
          const mx = (ax + bx) / 2 - Math.sin(ang) * curva, my = (ay + by) / 2 + Math.cos(ang) * curva;
          return (
            <g key={`${a.de}>${a.para}`}>
              <path d={`M${ax},${ay} Q${mx},${my} ${bx},${by}`} fill="none" stroke="var(--color-accent)" strokeOpacity="0.7" strokeWidth="1.6" markerEnd="url(#seta-fluxo)" />
              {a.rotulos[0] && (
                <text x={mx} y={my - 4} textAnchor="middle" className="fill-[var(--color-fg-2)] text-[10.5px]">
                  {a.rotulos[0]}{a.rotulos.length > 1 ? ` +${a.rotulos.length - 1}` : ""}
                </text>
              )}
            </g>
          );
        })}
        {telas.map((t, i) => {
          const { x, y } = pos(i);
          const on = t === props.atual;
          return (
            <g key={t} onClick={() => props.onIr(t)} className="cursor-pointer" role="button" aria-label={`Abrir a tela ${t}`}>
              <rect x={x} y={y} width={W} height={H} rx="10" fill={on ? "var(--color-accent-soft)" : "var(--color-raised)"}
                    stroke={on ? "var(--color-accent)" : "var(--color-line-strong)"} strokeWidth={on ? 2 : 1} />
              <text x={x + W / 2} y={y + H / 2 - 3} textAnchor="middle" className="fill-[var(--color-fg)] font-mono text-[12.5px]">{t}</text>
              <text x={x + W / 2} y={y + H / 2 + 13} textAnchor="middle" className="fill-[var(--color-faint)] text-[10px]">
                {i === 0 ? "início · " : ""}{arestas.filter((a) => a.de === t).length} saída(s)
              </text>
            </g>
          );
        })}
      </svg>
      {(!!semSaida.length || !!semEntrada.length) && (
        <div className="mt-3 space-y-1 text-[12px] text-amber-300">
          {!!semEntrada.length && <p>Nenhum botão leva a: {semEntrada.join(", ")} — só dá para chegar pela barra de telas.</p>}
          {!!semSaida.length && <p>Sem saída: {semSaida.join(", ")} — a pessoa fica presa nessa tela.</p>}
        </div>
      )}
    </div>
  );
}

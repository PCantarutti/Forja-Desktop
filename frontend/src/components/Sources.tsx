import { useState } from "react";
import type { Source } from "../types";

/**
 * Fontes da web: favicon + título + domínio, como o chat do Claude mostra.
 *
 * O favicon vem do próprio site (`/favicon.ico`) — nada de serviço de terceiro, que veria todo
 * domínio pesquisado. Site sem favicon nesse caminho cai no quadradinho com a inicial.
 */

const CORES = ["bg-sky-500/25", "bg-emerald-500/25", "bg-amber-500/25", "bg-violet-500/25", "bg-rose-500/25"];

export function Favicon({ dominio, className = "size-4" }: { dominio: string; className?: string }) {
  const [falhou, setFalhou] = useState(false);
  if (!dominio) return null;
  if (falhou) {
    const cor = CORES[[...dominio].reduce((a, c) => a + c.charCodeAt(0), 0) % CORES.length];
    return (
      <span className={`${className} ${cor} grid shrink-0 place-items-center rounded-[4px] text-[9px] font-semibold text-fg uppercase`}>
        {dominio[0]}
      </span>
    );
  }
  return (
    <img
      src={`https://${dominio}/favicon.ico`}
      alt=""
      loading="lazy"
      onError={() => setFalhou(true)}
      className={`${className} shrink-0 rounded-[4px] object-contain`}
    />
  );
}

/** Lista de sites percorridos, dentro do bloco da ferramenta. */
export function SourceList({ items }: { items: Source[] }) {
  if (!items?.length) return null;
  return (
    <div className="border-t border-line">
      {items.map((f) => (
        <a
          key={f.url}
          href={f.url}
          target="_blank"
          rel="noreferrer"
          title={f.trecho || f.url}
          className="flex items-center gap-2 px-4 py-2 text-sm hover:bg-raised/50"
        >
          <Favicon dominio={f.dominio} />
          <span className="truncate text-fg">{f.titulo}</span>
          <span className="ml-auto shrink-0 text-xs text-faint">{f.dominio}</span>
        </a>
      ))}
    </div>
  );
}

/** Citação dentro da resposta: pílula clicável, no lugar do link cru do markdown. */
export function SourceChip({ href, children }: { href: string; children: React.ReactNode }) {
  let dominio: string;
  try {
    dominio = new URL(href).hostname.replace(/^www\./, "");
  } catch {
    dominio = "";
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      title={href}
      className="fonte mx-0.5 inline-flex max-w-[18rem] items-center gap-1 rounded-md bg-raised px-1.5 py-0.5 align-baseline text-xs"
    >
      <Favicon dominio={dominio} className="size-3" />
      <span className="truncate">{children}</span>
    </a>
  );
}

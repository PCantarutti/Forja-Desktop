import { useState, type ReactNode } from "react";

/** Botão com confirmação na própria tela. `confirm()` não serve no app: o backend está ligado ao
 * Electron por CDP (é assim que ele controla o navegador), e o Playwright recusa sozinho os diálogos
 * das páginas que enxerga — inclusive a da interface —, então o confirm voltava "não" e o botão não
 * fazia nada. */
export default function Confirma(props: {
  rotulo: ReactNode;
  pergunta: ReactNode;
  titulo?: string;
  className: string;
  desabilitado?: boolean;
  onSim: () => void;
}) {
  const [perguntando, setPerguntando] = useState(false);
  if (!perguntando)
    return (
      <button className={props.className} disabled={props.desabilitado} title={props.titulo} onClick={() => setPerguntando(true)}>
        {props.rotulo}
      </button>
    );
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px]" title={props.titulo}>
      <span className="text-amber-300">{props.pergunta}</span>
      <button className="rounded-md border border-line px-2 py-0.5 text-fg hover:bg-raised"
              onClick={() => { setPerguntando(false); props.onSim(); }}>
        Sim
      </button>
      <button className="rounded-md px-2 py-0.5 text-muted hover:text-fg" onClick={() => setPerguntando(false)}>
        Não
      </button>
    </span>
  );
}

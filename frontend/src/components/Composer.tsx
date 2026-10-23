import { useEffect, useRef, useState } from "react";
import { ArrowUp, Mic, Square } from "./icons";

/**
 * A caixa de prompt de todas as telas (chat, agente, Maestro, Imagem, Comparar, Pesquisa): a mesma
 * borda, os mesmos cantos, o campo em cima e uma linha de pílulas embaixo (RodapePrompt). Cada tela
 * põe as pílulas dela; o que é igual mora aqui, para as telas não voltarem a divergir.
 */
export function CaixaPrompt(props: { children: React.ReactNode }) {
  return (
    <div data-caixa-prompt className="rounded-[18px] border border-line bg-surface px-3.5 pt-3 pb-2.5 transition-colors duration-150 focus-within:border-focus">
      {props.children}
    </div>
  );
}

/** Linha de baixo da caixa: pílulas à esquerda; quem leva ml-auto (o ModelPicker) vai para a direita. */
export function RodapePrompt(props: { children: React.ReactNode }) {
  return <div className="mt-1 flex flex-wrap items-center gap-2">{props.children}</div>;
}

/** O fim do rodapé: seletor de modelo, enviar e o microfone do ditado, sempre juntos à direita. Ocupa o que sobra da
 *  linha (basis 0) e o seletor corta o nome do modelo com "…" antes de o enviar cair para a linha de baixo. */
export function DireitaPrompt(props: { children: React.ReactNode }) {
  return (
    <div className="flex min-w-36 flex-1 basis-0 items-center justify-end gap-2">
      {props.children}
      <BotaoDitado />
    </div>
  );
}

type EstadoDitado = { fase: "parado" | "gravando" | "transcrevendo"; el: HTMLTextAreaElement | null; erro: string };

/** O microfone ao lado do enviar: dita no campo desta caixa (a gravação e a transcrição são do Ditado.tsx, que
 *  avisa o estado por evento). Mesmo tamanho do enviar, sem fundo. */
export function BotaoDitado() {
  const eu = useRef<HTMLButtonElement>(null);
  const [st, setSt] = useState<EstadoDitado>({ fase: "parado", el: null, erro: "" });
  useEffect(() => {
    const ouve = (e: Event) => setSt((e as CustomEvent<EstadoDitado>).detail);
    window.addEventListener("forja:ditado-estado", ouve);
    return () => window.removeEventListener("forja:ditado-estado", ouve);
  }, []);
  const campo = () => eu.current?.closest("[data-caixa-prompt]")?.querySelector("textarea") ?? null;
  const meu = st.el !== null && st.el === campo();
  const gravando = meu && st.fase === "gravando", ocupado = meu && st.fase === "transcrevendo";
  const titulo = meu && st.erro ? st.erro : gravando ? "Parar e transcrever" : ocupado ? "Transcrevendo…" : "Ditar por voz";
  return (
    <button
      ref={eu}
      type="button"
      title={titulo}
      aria-label={gravando ? "Parar e transcrever" : "Ditar por voz"}
      disabled={ocupado}
      onMouseDown={(e) => e.preventDefault()}  // não tira o foco do campo
      onClick={() => { const el = campo(); if (el) window.dispatchEvent(new CustomEvent("forja:ditar-aqui", { detail: el })); }}
      className={`grid size-9 shrink-0 place-items-center rounded-[11px] transition-colors ${
        gravando ? "animate-pulse bg-red-500/90 text-white" : meu && st.erro ? "text-diff-del-fg hover:bg-raised"
          : ocupado ? "text-faint" : "text-muted hover:bg-raised hover:text-fg"}`}
    >
      <Mic className="size-[18px]" />
    </button>
  );
}

export const campoPrompt =
  "w-full resize-none overflow-y-auto bg-transparent text-[15px] leading-normal text-fg placeholder:text-faint focus:outline-none";
/** A pílula do rodapé (Automático, Médio...): botão, menu, número ou select no mesmo feitio. */
export const pilula =
  "inline-flex shrink-0 items-center gap-1.5 rounded-[9px] border border-line px-2.5 py-[5px] text-[12.5px] text-fg-2 hover:bg-raised hover:text-fg disabled:opacity-40 disabled:hover:bg-transparent";
export const pilulaLigada = "border-accent-line! bg-accent-soft! text-accent-text!";
/** Número dentro de uma pílula ("4 variações", "10 min"): da largura do valor, sem as setinhas. */
export const numeroPilula =
  "bg-transparent text-right text-fg outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none";
export const larguraNumero = (v: number) => ({ width: `${String(v).length + 0.4}ch` });
/** Botão redondo só de ícone (o clipe de anexar). */
export const redondo =
  "grid size-[30px] shrink-0 cursor-pointer place-items-center rounded-[9px] border border-line text-muted hover:bg-raised hover:text-fg";
export const enviarClasse =
  "grid size-9 shrink-0 place-items-center rounded-[11px] bg-accent text-accent-fg hover:brightness-110 disabled:bg-raised disabled:text-faint disabled:hover:brightness-100";
export const pararClasse = "grid size-9 shrink-0 place-items-center rounded-[11px] bg-raised text-fg hover:bg-line-strong";

/** Enviar (seta) ou, rodando, Parar (quadrado). */
export function BotaoEnviar(props: {
  rodando?: boolean;
  onEnviar: () => void;
  onParar?: () => void;
  desabilitado?: boolean;
  titulo?: string;
  icone?: React.ReactNode;
}) {
  return props.rodando && props.onParar ? (
    <button onClick={props.onParar} title="Parar" className={pararClasse}>
      <Square />
    </button>
  ) : (
    <button onClick={props.onEnviar} disabled={props.desabilitado} title={props.titulo ?? "Enviar"} className={enviarClasse}>
      {props.icone ?? <ArrowUp />}
    </button>
  );
}

import { ArrowUp, Square } from "./icons";

/**
 * A caixa de prompt de todas as telas (chat, agente, Maestro, Imagem, Comparar, Pesquisa): a mesma
 * borda, os mesmos cantos, o campo em cima e uma linha de pílulas embaixo (RodapePrompt). Cada tela
 * põe as pílulas dela; o que é igual mora aqui, para as telas não voltarem a divergir.
 */
export function CaixaPrompt(props: { children: React.ReactNode }) {
  return (
    <div className="rounded-[18px] border border-line bg-surface px-3.5 pt-3 pb-2.5 transition-colors duration-150 focus-within:border-focus">
      {props.children}
    </div>
  );
}

/** Linha de baixo da caixa: pílulas à esquerda; quem leva ml-auto (o ModelPicker) vai para a direita. */
export function RodapePrompt(props: { children: React.ReactNode }) {
  return <div className="mt-1 flex flex-wrap items-center gap-2">{props.children}</div>;
}

/** O fim do rodapé: seletor de modelo e enviar, sempre juntos à direita. Ocupa o que sobra da linha
 *  (basis 0) e o seletor corta o nome do modelo com "…" antes de o enviar cair para a linha de baixo. */
export function DireitaPrompt(props: { children: React.ReactNode }) {
  return <div className="flex min-w-36 flex-1 basis-0 items-center justify-end gap-2">{props.children}</div>;
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

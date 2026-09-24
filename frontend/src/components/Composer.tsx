import { ArrowUp, Square } from "./icons";

/**
 * A caixa de prompt de todas as telas (chat, agente, Maestro, Imagem, Comparar, Pesquisa): a mesma
 * borda, os mesmos cantos, o campo em cima e uma linha de pílulas embaixo (RodapePrompt). Cada tela
 * põe as pílulas dela; o que é igual mora aqui, para as telas não voltarem a divergir.
 */
export function CaixaPrompt(props: { children: React.ReactNode }) {
  return (
    <div className="rounded-3xl border border-line bg-surface px-4 pt-3 pb-2.5 focus-within:border-[#454545]">
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
  "w-full resize-none overflow-y-auto bg-transparent text-[15px] text-fg placeholder:text-faint focus:outline-none";
/** A pílula do rodapé (Automático, Médio...): botão, menu, número ou select no mesmo feitio. */
export const pilula =
  "inline-flex shrink-0 items-center gap-1.5 rounded-full border border-line px-3 py-1 text-xs text-muted hover:bg-raised hover:text-fg disabled:opacity-40 disabled:hover:bg-transparent";
export const pilulaLigada = "bg-raised text-fg";
/** Número dentro de uma pílula ("4 variações", "10 min"): da largura do valor, sem as setinhas. */
export const numeroPilula =
  "bg-transparent text-right text-fg outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none";
export const larguraNumero = (v: number) => ({ width: `${String(v).length + 0.4}ch` });
/** Botão redondo só de ícone (o clipe de anexar). */
export const redondo =
  "grid size-8 shrink-0 cursor-pointer place-items-center rounded-full border border-line text-muted hover:bg-raised hover:text-fg";
export const enviarClasse =
  "grid size-9 shrink-0 place-items-center rounded-full bg-fg text-black hover:bg-white disabled:bg-raised disabled:text-faint";
export const pararClasse = "grid size-9 shrink-0 place-items-center rounded-full bg-raised text-fg hover:bg-[#3a3a3a]";

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

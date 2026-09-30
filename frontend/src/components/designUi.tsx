// Peças visuais comuns aos painéis da tela Design (Comentários, Ajustes, Revisão, Versões): o mesmo
// título de seção, os mesmos botões e o mesmo estado vazio, no feitio do resto do programa.

const foco = "focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-focus";

/** Título de seção: mono, caixa alta, discreto (o padrão já usado em "Modelo por etapa"). */
export const tituloSecao = "font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase";

/** Botão secundário do painel. */
export const botao = `inline-flex h-7 shrink-0 items-center gap-1.5 rounded-lg border border-line px-2.5 text-[12px] text-fg transition-colors hover:bg-raised disabled:opacity-40 disabled:hover:bg-transparent ${foco}`;

/** Botão principal do painel (uma ação por seção). */
export const botaoPrincipal = `inline-flex h-7 shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 text-[12px] font-medium text-accent-fg transition-[filter] hover:brightness-110 disabled:opacity-40 disabled:hover:brightness-100 ${foco}`;

/** Botão pequeno, dentro de um item da lista. */
export const botaoItem = `inline-flex h-6 shrink-0 items-center gap-1 rounded-md border border-line px-2 text-[11.5px] text-fg-2 transition-colors hover:bg-raised hover:text-fg disabled:opacity-40 disabled:hover:bg-transparent ${foco}`;

/** Botão só de texto (descartar, limpar). */
export const botaoTexto = `inline-flex h-6 shrink-0 items-center rounded-md px-1.5 text-[11.5px] text-muted transition-colors hover:text-fg disabled:opacity-40 ${foco}`;

/** Item de lista do painel. */
export const item = "rounded-xl border border-line bg-surface transition-colors";

/** Estado vazio: ícone, uma frase e (opcional) o que fazer. */
export function Vazio(props: { icone: React.ReactNode; titulo: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-line px-5 py-7 text-center">
      <span className="grid size-9 place-items-center rounded-full bg-raised text-muted">{props.icone}</span>
      <p className="text-[13px] font-medium text-fg-2">{props.titulo}</p>
      {props.children && <div className="max-w-[34ch] text-[12px] leading-relaxed text-faint">{props.children}</div>}
    </div>
  );
}

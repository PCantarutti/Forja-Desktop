import { X } from "./icons";

/**
 * Cartão de estado do design (referência 9f): aparece sobre o composer ou na conversa. `aviso` é âmbar
 * (VRAM ocupada, algo que custa), `erro` é vermelho (modo perigoso, falha), `info` é azul com o giro
 * (reconectando), `neutro` é o resto. Compacto = uma linha só, com o rótulo mono à esquerda.
 */
export type Tom = "aviso" | "erro" | "info" | "neutro";

const CAIXA: Record<Tom, string> = {
  aviso: "border-warn/50 bg-warn/[.06]",
  erro: "border-err/50 bg-err/[.07]",
  info: "border-info/40",
  neutro: "border-line bg-surface",
};
const TITULO: Record<Tom, string> = { aviso: "text-warn", erro: "text-diff-del-fg", info: "text-info", neutro: "text-fg" };

export const botaoEstado = "rounded-[9px] border border-line-strong px-3 py-[5px] text-[12.5px] text-fg hover:bg-raised disabled:opacity-40";
export const botaoEstadoPrimario = "rounded-[9px] bg-accent px-3 py-[5px] text-[12.5px] font-semibold text-accent-fg hover:brightness-110 disabled:opacity-40";
export const botaoEstadoPerigo = "rounded-[9px] bg-err px-3 py-[5px] text-[12.5px] font-semibold text-white hover:brightness-110 disabled:opacity-40";

export default function CartaoEstado(props: {
  tom?: Tom;
  titulo?: React.ReactNode;
  rotulo?: string; // compacto: "OBJETIVO"
  compacto?: boolean;
  girando?: boolean; // info: o spinner no começo
  acoes?: React.ReactNode;
  onFechar?: () => void;
  className?: string;
  children?: React.ReactNode;
}) {
  const tom = props.tom ?? "neutro";
  const fechar = props.onFechar && (
    <button onClick={props.onFechar} aria-label="Fechar" title="Fechar" className="ml-auto shrink-0 rounded-[6px] p-0.5 opacity-70 hover:bg-raised hover:opacity-100">
      <X className="size-3.5" />
    </button>
  );
  if (props.compacto)
    return (
      <div className={`flex items-center gap-2 rounded-xl border px-3 py-[9px] text-[12.5px] ${CAIXA[tom]} ${tom === "neutro" ? "text-fg-2" : TITULO[tom]} ${props.className ?? ""}`}>
        {props.girando && <span className="size-3 shrink-0 animate-spin rounded-full border-2 border-info/30 border-t-info" />}
        {props.rotulo && <span className="shrink-0 font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">{props.rotulo}</span>}
        <div className="flex min-w-0 flex-1 items-center gap-2">{props.children}</div>
        {props.acoes}
        {fechar}
      </div>
    );
  return (
    <div className={`flex flex-col gap-2 rounded-[14px] border px-3.5 py-3 text-[13px] ${CAIXA[tom]} ${props.className ?? ""}`}>
      {(props.titulo || fechar) && (
        <div className="flex items-start gap-2">
          {props.titulo && <b className={`font-semibold ${TITULO[tom]}`}>{props.titulo}</b>}
          {fechar}
        </div>
      )}
      {props.children && <div className="leading-relaxed text-fg-2">{props.children}</div>}
      {props.acoes && <div className="flex flex-wrap gap-1.5">{props.acoes}</div>}
    </div>
  );
}

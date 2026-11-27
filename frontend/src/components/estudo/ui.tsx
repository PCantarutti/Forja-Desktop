import { useEffect, useState } from "react";
import { api } from "../../api";
import { Markdown } from "../MessageView";
import { X } from "../icons";
import type { Fonte } from "./tipos";

export const card = "rounded-xl border border-line bg-surface p-4";
export const btn = "inline-flex items-center gap-1.5 rounded-[9px] border border-line-strong px-3 py-1.5 text-fg hover:border-focus hover:bg-raised disabled:opacity-40";
export const btnPrimary = "inline-flex items-center gap-1.5 rounded-[9px] border border-accent bg-accent px-3 py-1.5 font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";
export const btnLeve = "inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-[12px] text-fg-2 hover:border-focus hover:bg-raised disabled:opacity-40";
export const rotulo = "font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase";
export const campo = "w-full rounded-[9px] border border-line bg-bg px-3 py-2 text-fg outline-none focus:border-focus";
export const pilula = "rounded-full border border-line px-2.5 py-0.5 text-[12px] text-fg-2 hover:border-focus";
export const pilulaLigada = "border-accent bg-accent-soft text-accent-text";

export const relogio = (seg: number) => `${Math.floor(seg / 60)}:${String(Math.floor(seg % 60)).padStart(2, "0")}`;
export const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);
export const corAcerto = (a: number | null) => (a == null ? "bg-faint/50" : a >= 0.7 ? "bg-ok" : a >= 0.5 ? "bg-warn" : "bg-err");

/** Texto do modelo → o Markdown da tela: fórmula LaTeX nos delimitadores que o KaTeX entende, bloco de complemento
 *  como citação marcada e cada [F3·12] como link que abre o trecho original. */
export function paraTela(md: string): string {
  return md
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, f) => `$$${f}$$`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, f) => `$${f}$`)
    .replace(/R\$(?=\s?\d)/g, "R\\$")
    .replace(/^:::complemento[ \t]*\n([\s\S]*?)^:::[ \t]*$/gm,
             (_, corpo: string) => "> **Complemento · sem fonte**\n>\n" + corpo.trim().split("\n").map((l) => `> ${l}`).join("\n"))
    .replace(/\[(F\d+·\d+)\]/g, (_, id) => ` [${id}](#t-${id})`);
}

/** Markdown com citações clicáveis: o clique em [F3·12] chama onTrecho. */
export function Texto(props: { md: string; onTrecho: (id: string) => void; className?: string }) {
  return (
    <div className={`estudo-texto ${props.className ?? ""}`}
         onClick={(e) => {
           const a = (e.target as HTMLElement).closest("a");
           const href = a?.getAttribute("href") ?? "";
           if (href.startsWith("#t-")) {
             e.preventDefault();
             props.onTrecho(decodeURIComponent(href.slice(3)));
           }
         }}>
      <Markdown text={paraTela(props.md)} math />
    </div>
  );
}

/** O trecho original de uma citação, com a fonte de onde veio. */
export function JanelaTrecho(props: { conv: number; id: string; destaque?: string; onClose: () => void }) {
  const [t, setT] = useState<{ texto: string; fonte: Partial<Fonte> } | null>(null);
  const [erro, setErro] = useState("");
  useEffect(() => {
    setT(null);
    api.get<{ texto: string; fonte: Fonte }>(`/estudo/${props.conv}/trecho/${encodeURIComponent(props.id)}`)
      .then(setT, (e) => setErro(e.message));
  }, [props.conv, props.id]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" onClick={props.onClose}>
      <div className={`${card} max-h-[80vh] w-full max-w-2xl overflow-y-auto`} onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className={rotulo}>Trecho {props.id}</div>
            <div className="mt-1 truncate text-[13px] text-muted">
              {t?.fonte?.url ? <a href={t.fonte.url} target="_blank" rel="noreferrer" className="underline">{t.fonte.titulo}</a>
                             : t?.fonte?.titulo}
            </div>
          </div>
          <button className={btnLeve} onClick={props.onClose} aria-label="Fechar"><X className="size-3.5" /></button>
        </div>
        {erro && <div className="text-err">{erro}</div>}
        {!t && !erro && <div className="text-muted">Carregando…</div>}
        {t && <div className="whitespace-pre-wrap text-[14px] leading-relaxed text-fg-2">{destacar(t.texto, props.destaque)}</div>}
      </div>
    </div>
  );
}

/** Marca a citação dentro do trecho (busca simples, sem acento/caixa não contam). */
function destacar(texto: string, citacao?: string) {
  if (!citacao) return texto;
  const n = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const i = n(texto).indexOf(n(citacao).slice(0, 60));
  if (i < 0) return texto;
  const fim = Math.min(texto.length, i + citacao.length);
  return <>{texto.slice(0, i)}<mark className="rounded bg-accent-soft px-0.5 text-fg">{texto.slice(i, fim)}</mark>{texto.slice(fim)}</>;
}

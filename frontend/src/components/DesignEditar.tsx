import { useEffect, useRef, useState } from "react";
import { X } from "./icons";

// Modo Editar da tela Design: o elemento clicado (ou todos os selecionados) ganha um painel de
// propriedades. Cada mudança aparece na hora no canvas (prévia no style do nó) e, 700 ms depois do
// último movimento, entra no rascunho pelo backend — sem modelo nenhum. Versão só quando você salvar.

const SALVAR_MS = 700;
const FONTES = ["system-ui, sans-serif", "'Segoe UI', system-ui, sans-serif", "Georgia, serif", "'Times New Roman', serif",
  "'Trebuchet MS', sans-serif", "Verdana, sans-serif", "ui-monospace, monospace"];
const PESOS = ["300", "400", "500", "600", "700", "800", "900"];
const ALINHA = [["left", "Esq."], ["center", "Centro"], ["right", "Dir."], ["justify", "Just."]];
const campo = "min-w-0 flex-1 rounded-md border border-line bg-raised px-1.5 py-1 font-mono text-[11.5px] text-fg focus:border-focus focus:outline-none";

/** rgb(a) do getComputedStyle → #rrggbb para o <input type=color>; transparente → "". */
export function hex(cor: string): string {
  const m = cor.match(/rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s/]+([\d.]+))?/);
  if (!m) return /^#[0-9a-f]{6}$/i.test(cor) ? cor : "";
  if (m[4] !== undefined && Number(m[4]) === 0) return "";
  return "#" + [m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, "0")).join("");
}

export default function DesignEditar(props: {
  rotulo: string;
  n: number;                                   // quantos elementos a mudança atinge
  estilo: Record<string, string>;              // valores atuais (computados) do principal
  onPrevia: (estilos: Record<string, string>) => void;
  onSalvar: (estilos: Record<string, string>) => void;
  onFechar: () => void;
}) {
  const [vals, setVals] = useState(props.estilo);
  const pendente = useRef<Record<string, string>>({});
  const timer = useRef<number | undefined>(undefined);
  const salvar = useRef(props.onSalvar);
  salvar.current = props.onSalvar;

  const descarrega = () => {
    window.clearTimeout(timer.current);
    if (Object.keys(pendente.current).length) salvar.current(pendente.current);
    pendente.current = {};
  };
  useEffect(() => descarrega, []);   // trocou de elemento ou saiu do modo: o que faltava salvar vai

  const muda = (k: string, v: string) => {
    v = v.replace(/"/g, "'");        // o style="" é entre aspas duplas
    setVals((s) => ({ ...s, [k]: v }));
    pendente.current = { ...pendente.current, [k]: v };
    props.onPrevia({ [k]: v });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(descarrega, SALVAR_MS);
  };

  const texto = (k: string, rotulo: string, lista?: string) => (
    <label className="flex items-center gap-2">
      <span className="w-24 shrink-0 text-muted">{rotulo}</span>
      <input className={campo} value={vals[k] ?? ""} list={lista} aria-label={rotulo} spellCheck={false}
             onChange={(e) => muda(k, e.target.value)} />
    </label>
  );
  const cor = (k: string, rotulo: string) => (
    <label className="flex items-center gap-2">
      <span className="w-24 shrink-0 text-muted">{rotulo}</span>
      <input type="color" value={hex(vals[k] ?? "") || "#000000"} aria-label={`${rotulo} (seletor)`}
             onChange={(e) => muda(k, e.target.value)} className="h-6 w-7 shrink-0 cursor-pointer rounded border border-line bg-transparent" />
      <input className={campo} value={vals[k] ?? ""} aria-label={rotulo} spellCheck={false} onChange={(e) => muda(k, e.target.value)} />
    </label>
  );
  const grupo = "space-y-1.5 border-t border-line pt-2.5";
  const titulo = "font-mono text-[10.5px] tracking-[.08em] text-faint uppercase";

  return (
    <aside aria-label="Editar elemento" className="flex w-72 shrink-0 flex-col border-l border-line bg-surface text-[12px]">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-fg">{props.n > 1 ? `${props.n} elementos` : props.rotulo}</span>
        <button onClick={props.onFechar} title="Sair do modo Editar" className="grid size-6 place-items-center rounded text-muted hover:bg-raised hover:text-fg">
          <X className="size-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-2.5">
        <p className="text-[11.5px] leading-snug text-faint">
          Muda na hora, sem IA, e vai para o rascunho (Ctrl+Z desfaz; Ctrl+S salva a versão). Duplo clique no texto edita o conteúdo.
          {props.n > 1 && " Vale para todos os selecionados."}
        </p>
        <div className="space-y-1.5">
          <div className={titulo}>Texto</div>
          {texto("font-family", "Fonte", "design-fontes")}
          <datalist id="design-fontes">{FONTES.map((f) => <option key={f} value={f} />)}</datalist>
          {texto("font-size", "Tamanho")}
          <label className="flex items-center gap-2">
            <span className="w-24 shrink-0 text-muted">Peso</span>
            <select className={campo} value={vals["font-weight"] ?? ""} aria-label="Peso" onChange={(e) => muda("font-weight", e.target.value)}>
              {!PESOS.includes(vals["font-weight"] ?? "") && <option value={vals["font-weight"] ?? ""}>{vals["font-weight"] || "—"}</option>}
              {PESOS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          {texto("line-height", "Entrelinha")}
          {texto("letter-spacing", "Espaço letras")}
          <div className="flex items-center gap-2">
            <span className="w-24 shrink-0 text-muted">Alinhamento</span>
            <div className="flex flex-1 rounded-md border border-line p-0.5" role="radiogroup" aria-label="Alinhamento">
              {ALINHA.map(([v, r]) => (
                <button key={v} role="radio" aria-checked={vals["text-align"] === v} onClick={() => muda("text-align", v)}
                        className={`flex-1 rounded px-1 py-0.5 text-[11px] ${vals["text-align"] === v ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
                  {r}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className={grupo}>
          <div className={titulo}>Cores</div>
          {cor("color", "Texto")}
          {cor("background-color", "Fundo")}
          <label className="flex items-center gap-2">
            <span className="w-24 shrink-0 text-muted">Opacidade</span>
            <input type="range" min={0} max={1} step={0.05} value={Number(vals.opacity ?? 1)} aria-label="Opacidade"
                   onChange={(e) => muda("opacity", e.target.value)} className="flex-1 accent-[var(--color-accent)]" />
            <span className="w-8 text-right font-mono text-faint">{Number(vals.opacity ?? 1).toFixed(2)}</span>
          </label>
        </div>
        <div className={grupo}>
          <div className={titulo}>Caixa</div>
          {texto("padding", "Preenchimento")}
          {texto("margin", "Margem")}
          {texto("border-radius", "Cantos")}
          {texto("border", "Borda")}
          {texto("width", "Largura")}
          {texto("height", "Altura")}
          {texto("gap", "Espaço filhos")}
        </div>
      </div>
    </aside>
  );
}

import { useState } from "react";
import { ArrowUp, Plus, Trash } from "./icons";

// Card do plano da geração em etapas: o modelo propõe, você ajusta aqui e só então as seções são
// geradas (uma por vez). Mexer no plano é barato; regenerar a página inteira não é.

export type Secao = { nome: string; objetivo: string; conteudo: string };
export type Plano = { tipo: "site" | "slides"; titulo: string; tokens: Record<string, string>; secoes: Secao[] };

const campo = "w-full rounded-lg border border-line bg-raised px-2 py-1 text-[13px] text-fg focus:border-focus focus:outline-none";
const eCor = (v: string) => /^#[0-9a-f]{6}$/i.test(v.trim());
const slug = (t: string) => t.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

export default function DesignPlano(props: { plano: Plano; onGerar: (p: Plano) => void; desabilitado?: boolean }) {
  const [p, setP] = useState<Plano>(props.plano);
  const mexe = (i: number, parte: Partial<Secao>) =>
    setP((x) => ({ ...x, secoes: x.secoes.map((s, j) => (j === i ? { ...s, ...parte } : s)) }));
  const move = (i: number, d: number) =>
    setP((x) => {
      const s = [...x.secoes];
      [s[i], s[i + d]] = [s[i + d], s[i]];
      return { ...x, secoes: s };
    });
  const cores = Object.entries(p.tokens).filter(([, v]) => eCor(v));
  const outros = Object.entries(p.tokens).filter(([, v]) => !eCor(v));
  const valido = p.secoes.length > 0 && p.secoes.every((s) => slug(s.nome));

  return (
    <div className="w-full rounded-2xl border border-line bg-surface p-3.5">
      <div className="mb-2 font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">Plano · aprove ou ajuste</div>
      <div className="mb-3 flex items-center gap-2">
        <input value={p.titulo} onChange={(e) => setP({ ...p, titulo: e.target.value })} aria-label="Título"
               className={`${campo} text-sm font-medium`} />
        <div className="flex shrink-0 rounded-lg border border-line p-0.5 text-xs" role="radiogroup" aria-label="Tipo">
          {(["site", "slides"] as const).map((t) => (
            <button key={t} role="radio" aria-checked={p.tipo === t} onClick={() => setP({ ...p, tipo: t })}
                    className={`rounded-md px-2 py-0.5 ${p.tipo === t ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}>
              {t === "site" ? "Site" : "Slides"}
            </button>
          ))}
        </div>
      </div>

      <div className="mb-1 text-xs text-muted">Cores</div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {cores.map(([k, v]) => (
          <label key={k} title={k} className="flex items-center gap-1.5 rounded-lg border border-line px-1.5 py-0.5 font-mono text-[11px] text-fg-2">
            <input type="color" value={v} onChange={(e) => setP({ ...p, tokens: { ...p.tokens, [k]: e.target.value } })}
                   className="size-4 cursor-pointer rounded border-0 bg-transparent p-0" />
            {k.replace(/^--cor-/, "")}
          </label>
        ))}
      </div>
      {!!outros.length && (
        <details className="mb-3 text-xs text-muted">
          <summary className="cursor-pointer hover:text-fg">Tipografia, espaços, raios e sombras ({outros.length})</summary>
          <div className="mt-1.5 grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-1">
            {outros.map(([k, v]) => (
              <label key={k} className="contents">
                <span className="font-mono text-[11px] text-faint">{k}</span>
                <input value={v} onChange={(e) => setP({ ...p, tokens: { ...p.tokens, [k]: e.target.value } })} className={campo} />
              </label>
            ))}
          </div>
        </details>
      )}

      <div className="mb-1 text-xs text-muted">{p.tipo === "slides" ? "Slides, na ordem (uma ideia por slide)" : "Seções, na ordem"}</div>
      <ol className="flex flex-col gap-2">
        {p.secoes.map((s, i) => (
          <li key={i} className="rounded-xl border border-line p-2">
            <div className="flex items-center gap-1.5">
              <span className="w-4 text-right font-mono text-[11px] text-faint">{i + 1}</span>
              <input value={s.nome} onChange={(e) => mexe(i, { nome: e.target.value })} aria-label="Nome da seção"
                     className={`${campo} font-mono`} />
              <button disabled={i === 0} onClick={() => move(i, -1)} title="Subir" className="rounded p-1 text-faint hover:text-fg disabled:opacity-30"><ArrowUp className="size-3.5" /></button>
              <button disabled={i === p.secoes.length - 1} onClick={() => move(i, 1)} title="Descer" className="rounded p-1 text-faint hover:text-fg disabled:opacity-30"><ArrowUp className="size-3.5 rotate-180" /></button>
              <button onClick={() => setP({ ...p, secoes: p.secoes.filter((_, j) => j !== i) })} title="Tirar esta seção" className="rounded p-1 text-faint hover:text-red-300"><Trash className="size-3.5" /></button>
            </div>
            <input value={s.objetivo} onChange={(e) => mexe(i, { objetivo: e.target.value })} placeholder="Objetivo"
                   className={`${campo} mt-1.5`} />
            <textarea value={s.conteudo} onChange={(e) => mexe(i, { conteudo: e.target.value })} placeholder="Conteúdo" rows={2}
                      className={`${campo} mt-1 resize-y`} />
          </li>
        ))}
      </ol>
      <div className="mt-2.5 flex items-center gap-2">
        <button onClick={() => setP({ ...p, secoes: [...p.secoes, { nome: "nova", objetivo: "", conteudo: "" }] })}
                className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-xs text-fg hover:bg-raised">
          <Plus className="size-3.5" /> Seção
        </button>
        <span className="flex-1" />
        <button disabled={props.desabilitado || !valido}
                onClick={() => props.onGerar({ ...p, secoes: p.secoes.map((s) => ({ ...s, nome: slug(s.nome) })) })}
                className="rounded-[9px] bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:brightness-110 disabled:opacity-40">
          Gerar {p.secoes.length} {p.tipo === "slides" ? "slides" : "seções"}
        </button>
      </div>
    </div>
  );
}

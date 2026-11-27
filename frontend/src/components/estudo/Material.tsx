import { useRef } from "react";
import { Download } from "../icons";
import type { Ctx } from "./EstudoView";
import { Texto, btnLeve, btnPrimary, rotulo } from "./ui";

export default function Material({ ctx }: { ctx: Ctx }) {
  const { estudo } = ctx;
  const rolagem = useRef<HTMLDivElement>(null);
  const feitos = estudo.programa.filter((t) => estudo.material[t.id]);
  const faltam = estudo.programa.filter((t) => !estudo.material[t.id]).map((t) => t.id);
  const tot = feitos.reduce((s, t) => {
    const x = estudo.material[t.id].stats;
    return { b: s.b + x.blocos, c: s.c + x.citados };
  }, { b: 0, c: 0 });

  function baixar() {
    const md = [`# ${estudo.titulo}`, ...feitos.map((t) => `## ${t.titulo}\n\n${estudo.material[t.id].md}`)].join("\n\n");
    const url = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${estudo.titulo.replace(/[\\/:*?"<>|]+/g, "").slice(0, 60) || "material"}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!estudo.programa.length) return <div className="p-10 text-center text-muted">Monte o programa na aba Fontes primeiro.</div>;

  return (
    <div className="flex h-full min-h-0">
      <aside className="w-64 shrink-0 overflow-y-auto border-r border-line px-3 py-5 text-[13px]">
        <div className={`${rotulo} mb-2 px-2`}>Sumário</div>
        {estudo.programa.map((t, i) => {
          const cab = t.materia && t.materia !== estudo.programa[i - 1]?.materia ? t.materia : "";
          return (
            <div key={t.id}>
              {cab && <div className="mt-3 mb-1 px-2 text-[11.5px] font-medium text-faint">{cab}</div>}
              <button className={`block w-full truncate rounded-md px-2 py-1 text-left hover:bg-raised ${estudo.material[t.id] ? "text-fg-2" : "text-faint"}`}
                      onClick={() => document.getElementById(`mat-${t.id}`)?.scrollIntoView({ behavior: "smooth" })}>
                {t.titulo}
              </button>
            </div>
          );
        })}
      </aside>
      <div ref={rolagem} className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-8 py-8">
          <div className="mb-6 flex flex-wrap items-center gap-2 text-[13px] text-muted">
            {feitos.length > 0 && <span>{feitos.length}/{estudo.programa.length} tópicos · {tot.b ? Math.round((100 * tot.c) / tot.b) : 0}% dos parágrafos com fonte</span>}
            <div className="ml-auto flex gap-1.5">
              {faltam.length > 0 && (
                <button className={btnPrimary} disabled={ctx.rodando} onClick={() => ctx.disparar("/material", { topicos: faltam })}>
                  Escrever {faltam.length === estudo.programa.length ? "o material" : `os ${faltam.length} que faltam`}
                </button>
              )}
              {feitos.length > 0 && <button className={btnLeve} onClick={baixar}><Download className="size-3.5" /> Markdown</button>}
            </div>
          </div>
          {feitos.length > 0 && (
            <p className="mb-8 rounded-lg border border-line px-3 py-2 text-[12.5px] text-muted">
              Cada <span className="font-mono text-accent-text">[F3·12]</span> abre o trecho da fonte. O que vem marcado
              como <b>Complemento · sem fonte</b> é do conhecimento do modelo: nunca cai na prova.
            </p>
          )}
          {estudo.programa.map((t) => {
            const s = estudo.material[t.id];
            return (
              <section key={t.id} id={`mat-${t.id}`} className="mb-12 scroll-mt-6">
                <div className="mb-3 flex items-baseline gap-3 border-b border-line pb-2">
                  <h2 className="flex-1 text-[22px] font-bold text-fg">{t.titulo}</h2>
                  {s && <span className="text-[11.5px] text-faint">{s.stats.citados}/{s.stats.blocos} com fonte</span>}
                  {s && <button className={btnLeve} disabled={ctx.rodando} onClick={() => ctx.disparar("/material", { topicos: [t.id] })}>Reescrever</button>}
                </div>
                {s ? <Texto md={s.md} onTrecho={(id) => ctx.abrirTrecho(id)}
                            className="[&_.md]:text-[15.5px]! [&_.md]:leading-[1.75]! [&_.md_blockquote]:border-warn/60! [&_.md_blockquote]:text-muted!" />
                   : <div className="text-[13px] text-faint">Ainda não escrito.</div>}
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}

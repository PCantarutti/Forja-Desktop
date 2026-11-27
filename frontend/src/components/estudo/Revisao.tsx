import { useState } from "react";
import { api, auth } from "../../api";
import { Download } from "../icons";
import type { Ctx } from "./EstudoView";
import type { Cartao } from "./tipos";
import { btn, btnLeve, btnPrimary, card, rotulo } from "./ui";

const hoje = () => new Date().toISOString().slice(0, 10);

export default function Revisao({ ctx }: { ctx: Ctx }) {
  const { estudo } = ctx;
  const [modo, setModo] = useState<"cartoes" | "erros">(estudo.caderno.length ? "erros" : "cartoes");
  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <div className="mb-5 flex gap-2">
        <button className={`${btnLeve} ${modo === "erros" ? "border-accent text-fg" : ""}`} onClick={() => setModo("erros")}>
          Caderno de erros · {estudo.caderno.length}
        </button>
        <button className={`${btnLeve} ${modo === "cartoes" ? "border-accent text-fg" : ""}`} onClick={() => setModo("cartoes")}>
          Flashcards · {estudo.flashcards.length}
        </button>
      </div>
      {modo === "erros" ? <Erros ctx={ctx} /> : <Cartoes ctx={ctx} />}
    </div>
  );
}

function Erros({ ctx }: { ctx: Ctx }) {
  const nomes = Object.fromEntries(ctx.estudo.programa.map((t) => [t.id, t.titulo]));
  if (!ctx.estudo.caderno.length) return <div className="text-[13px] text-muted">Nenhum erro pendente. Questão errada numa prova entra aqui até você acertá-la.</div>;
  return (
    <div className="grid gap-3">
      {ctx.estudo.caderno.map((e) => (
        <div key={`${e.prova}-${e.questao}`} className={card}>
          <div className="mb-1 text-[11.5px] text-faint">{e.prova} · {nomes[e.topico] ?? e.topico}</div>
          <div className="mb-2 text-[14.5px] text-fg">{e.enunciado}</div>
          <div className="text-[13px] text-err">Você: {e.resposta || "em branco"} · Gabarito: {e.correta}</div>
          {e.alternativas.length > 2 && "ABCDE".indexOf(e.correta) >= 0 && (
            <div className="text-[13px] text-ok">{e.correta}) {e.alternativas["ABCDE".indexOf(e.correta)]}</div>
          )}
          <div className="mt-2 text-[13px] text-fg-2">{e.explicacao}</div>
          <button className="mt-2 text-left text-[12.5px] text-muted hover:text-fg" onClick={() => ctx.abrirTrecho(e.fonte, e.citacao)}>
            <span className="font-mono text-accent-text">[{e.fonte}]</span> “{e.citacao}”
          </button>
        </div>
      ))}
    </div>
  );
}

function Cartoes({ ctx }: { ctx: Ctx }) {
  const { estudo, conv } = ctx;
  const vencidos = estudo.flashcards.filter((c) => c.proxima <= hoje());
  const [virado, setVirado] = useState(false);
  const atual: Cartao | undefined = vencidos[0];
  async function rever(acertou: boolean) {
    if (!atual) return;
    try { await api.post(`/estudo/${conv}/flashcards/${atual.id}`, { acertou }); setVirado(false); await ctx.recarregar(); }
    catch (e: any) { ctx.onError(e.message); }
  }
  async function anki() {
    const r = await fetch(`/api/estudo/${conv}/anki.csv`, { headers: auth() });
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "flashcards.csv";
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
        <span>{vencidos.length} para hoje · {estudo.flashcards.length} no total</span>
        <div className="ml-auto flex gap-1.5">
          <button className={btn} disabled={ctx.rodando || !estudo.programa.length} onClick={() => ctx.disparar("/flashcards", { topicos: [] })}>
            Gerar flashcards das fontes
          </button>
          {estudo.flashcards.length > 0 && <button className={btnLeve} onClick={anki}><Download className="size-3.5" /> Anki (CSV)</button>}
        </div>
      </div>
      {atual ? (
        <div className={`${card} min-h-48 cursor-pointer`} onClick={() => setVirado(true)}>
          <div className={rotulo}>Caixa {atual.caixa}</div>
          <div className="mt-3 text-[17px] leading-relaxed text-fg">{atual.frente}</div>
          {virado ? (
            <>
              <div className="mt-4 border-t border-line pt-4 text-[15px] text-fg-2">{atual.verso}</div>
              <button className="mt-2 text-left text-[12px] text-muted hover:text-fg"
                      onClick={(e) => { e.stopPropagation(); ctx.abrirTrecho(atual.fonte, atual.citacao); }}>
                <span className="font-mono text-accent-text">[{atual.fonte}]</span> ver na fonte
              </button>
              <div className="mt-4 flex justify-end gap-2" onClick={(e) => e.stopPropagation()}>
                <button className={btn} onClick={() => rever(false)}>Errei</button>
                <button className={btnPrimary} onClick={() => rever(true)}>Acertei</button>
              </div>
            </>
          ) : <div className="mt-6 text-[12.5px] text-faint">Clique para ver a resposta</div>}
        </div>
      ) : estudo.flashcards.length ? <div className="text-[13px] text-muted">Tudo revisado por hoje.</div> : null}
    </div>
  );
}

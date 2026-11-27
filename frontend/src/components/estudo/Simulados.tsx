import { useEffect, useState } from "react";
import { api, auth } from "../../api";
import { Paperclip, X } from "../icons";
import type { Ctx } from "./EstudoView";
import type { Simulado } from "./tipos";
import { btn, btnLeve, btnPrimary, campo, card, rotulo } from "./ui";

/** O caderno da prova real aberto na página da questão (o desenho da figura só existe no PDF). */
export function JanelaPdf(props: { conv: number; sid: string; pagina?: number; onClose: () => void }) {
  const [url, setUrl] = useState("");
  const [erro, setErro] = useState("");
  useEffect(() => {
    let feito = "";
    fetch(`/api/estudo/${props.conv}/simulado/${props.sid}/pdf`, { headers: auth() })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((b) => { feito = URL.createObjectURL(b); setUrl(feito); }, (e) => setErro(e.message));
    return () => { if (feito) URL.revokeObjectURL(feito); };
  }, [props.conv, props.sid]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" onClick={props.onClose}>
      <div className={`${card} flex h-[88vh] w-full max-w-5xl flex-col`} onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 flex items-center gap-2">
          <div className={rotulo}>Caderno da prova{props.pagina ? ` · página ${props.pagina}` : ""}</div>
          <button className={`${btnLeve} ml-auto`} onClick={props.onClose} aria-label="Fechar"><X className="size-3.5" /></button>
        </div>
        {erro && <div className="text-err">{erro}</div>}
        {url && <iframe title="Caderno da prova" src={`${url}#page=${props.pagina || 1}`} className="min-h-0 flex-1 rounded-lg border border-line bg-white" />}
      </div>
    </div>
  );
}

export default function Simulados({ ctx, abrirProva }: { ctx: Ctx; abrirProva: (pid: string) => void }) {
  const { estudo, conv } = ctx;
  const [pedido, setPedido] = useState("");
  const [arquivo, setArquivo] = useState<File | null>(null);
  const [gabArquivo, setGabArquivo] = useState<File | null>(null);
  const [gabTexto, setGabTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [pdf, setPdf] = useState<string | null>(null);
  const nomes = Object.fromEntries(estudo.programa.map((t) => [t.id, t.titulo]));

  async function enviar() {
    if (!arquivo) return;
    setEnviando(true);
    const form = new FormData();
    form.append("file", arquivo);
    if (gabArquivo) form.append("gabarito_arquivo", gabArquivo);
    form.append("gabarito", gabTexto);
    form.append("provider", ctx.motor.provider);
    form.append("model", ctx.motor.model);
    try {
      const r = await fetch(`/api/estudo/${conv}/simulado/arquivo`, { method: "POST", body: form, headers: auth() });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
      setArquivo(null); setGabArquivo(null); setGabTexto("");
      await ctx.recarregar();
    } catch (e: any) { ctx.onError(e.message); }
    setEnviando(false);
  }

  return (
    <section className={card}>
      <div className={`${rotulo} mb-1`}>Simulados reais</div>
      <p className="mb-3 text-[12.5px] text-muted">
        Prova de verdade com o gabarito oficial da banca. Cada questão ganha o assunto e entra no desempenho do tópico.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="grid content-start gap-2 rounded-lg border border-line p-3 text-[13px]">
          <div className="font-medium text-fg">Buscar na web</div>
          <input className={`${campo} py-1.5`} value={pedido} onChange={(e) => setPedido(e.target.value)}
                 placeholder="Ex.: ENEM 2023 2º dia, TRF 2024 técnico judiciário" />
          <button className={`${btnPrimary} justify-self-end`} disabled={ctx.rodando || pedido.trim().length < 3}
                  onClick={() => ctx.disparar("/simulado/buscar", { pedido })}>Buscar prova e gabarito</button>
        </div>
        <div className="grid content-start gap-2 rounded-lg border border-line p-3 text-[13px]">
          <div className="font-medium text-fg">Mandar o PDF</div>
          <div className="flex flex-wrap gap-1.5">
            <label className={`${btnLeve} cursor-pointer`}><Paperclip className="size-3.5" /> {arquivo ? arquivo.name : "Caderno (PDF)"}
              <input type="file" accept=".pdf" className="hidden" onChange={(e) => setArquivo(e.target.files?.[0] ?? null)} /></label>
            <label className={`${btnLeve} cursor-pointer`}><Paperclip className="size-3.5" /> {gabArquivo ? gabArquivo.name : "Gabarito (opcional)"}
              <input type="file" accept=".pdf,.txt,.docx" className="hidden" onChange={(e) => setGabArquivo(e.target.files?.[0] ?? null)} /></label>
          </div>
          <input className={`${campo} py-1.5`} value={gabTexto} onChange={(e) => setGabTexto(e.target.value)} placeholder="ou cole o gabarito: 1 C 2 A 3 E…" />
          <button className={`${btnPrimary} justify-self-end`} disabled={!arquivo || enviando || ctx.rodando} onClick={enviar}>
            {enviando ? "Enviando…" : "Ler a prova"}
          </button>
        </div>
      </div>

      {estudo.simulados.length > 0 && (
        <div className="mt-4 grid gap-2">
          {[...estudo.simulados].reverse().map((s) => <Item key={s.id} ctx={ctx} s={s} abrirProva={abrirProva} verPdf={() => setPdf(s.id)} />)}
        </div>
      )}

      {estudo.ranking.length > 0 && (
        <div className="mt-5">
          <div className={`${rotulo} mb-2`}>O que mais cai</div>
          <div className="grid gap-1 text-[13px]">
            {estudo.ranking.slice(0, 15).map((a) => (
              <div key={a.assunto} className="grid grid-cols-[1fr_8rem_4rem] items-center gap-3">
                <span className="truncate text-fg-2">{a.assunto}<span className="ml-2 text-[11.5px] text-faint">{a.area}{a.topico && nomes[a.topico] ? ` · ${nomes[a.topico]}` : ""}</span></span>
                <div className="h-1.5 overflow-hidden rounded-full bg-raised"><div className="h-full bg-accent" style={{ width: `${Math.round(a.fracao * 100 / estudo.ranking[0].fracao)}%` }} /></div>
                <span className="text-right font-mono text-[12px] text-muted">{a.questoes}q · {a.simulados}s</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {pdf && <JanelaPdf conv={conv} sid={pdf} onClose={() => setPdf(null)} />}
    </section>
  );
}

function Item({ ctx, s, abrirProva, verPdf }: { ctx: Ctx; s: Simulado; abrirProva: (pid: string) => void; verPdf: () => void }) {
  const [gab, setGab] = useState("");
  const [colar, setColar] = useState(false);
  async function regabaritar(body: object) {
    try { await api.post(`/estudo/${ctx.conv}/simulado/${s.id}/gabarito`, body); setColar(false); setGab(""); await ctx.recarregar(); }
    catch (e: any) { ctx.onError(e.message); }
  }
  async function apagar() {
    try { await api.del(`/estudo/${ctx.conv}/simulado/${s.id}`); await ctx.recarregar(); }
    catch (e: any) { ctx.onError(e.message); }
  }
  return (
    <div className="rounded-lg border border-line px-3 py-2 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-fg">{s.titulo}</span>
        <span className="text-[12px] text-muted">
          {s.status === "lendo" ? "lendo…" : `${s.questoes} questões · ${s.com_gabarito} com gabarito${s.gabarito ? ` (${s.gabarito})` : ""}`}
        </span>
        {s.prova && <button className={btn} onClick={() => abrirProva(s.prova)}>Fazer a prova</button>}
        <button className={btnLeve} onClick={verPdf}>PDF</button>
        <button className={btnLeve} onClick={() => setColar(!colar)}>Gabarito</button>
        <button className={btnLeve} onClick={apagar} aria-label="Apagar simulado"><X className="size-3" /></button>
      </div>
      {s.aviso && <div className="mt-1 text-[12px] text-warn">{s.aviso}</div>}
      {s.bloco && (
        <div className="mt-1.5 flex items-center gap-2 text-[12px] text-muted">
          Versão do gabarito:
          <select className="rounded border border-line bg-bg px-1 py-0.5 text-fg-2" value={s.bloco.id}
                  onChange={(e) => regabaritar({ bloco: e.target.value })}>
            {s.bloco.opcoes.map((o) => <option key={o.id} value={o.id}>{o.rotulo}</option>)}
          </select>
          {s.bloco.motivo && <span className="text-faint">{s.bloco.motivo}</span>}
        </div>
      )}
      {colar && (
        <div className="mt-2 flex gap-2">
          <input className={`${campo} flex-1 py-1`} value={gab} onChange={(e) => setGab(e.target.value)} placeholder="1 C 2 A 3 E …" />
          <button className={btnPrimary} disabled={!gab.trim()} onClick={() => regabaritar({ texto: gab })}>Aplicar</button>
        </div>
      )}
    </div>
  );
}

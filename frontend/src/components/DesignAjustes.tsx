import { useEffect, useMemo, useRef, useState } from "react";
import { ponte } from "./designCanvas";
import { FolderOpen, Trash } from "./icons";

// Aba Ajustes da tela Design: os tokens do :root viram controles (cor, slider, fonte) que mexem no
// canvas ao vivo e vão para o rascunho, sem chamar modelo nenhum; e os design systems (tirados do código
// de um projeto) para aplicar aqui ou usar nos próximos planos.

export type Sistema = { id: string; nome: string; pasta: string; tokens: Record<string, string>; css: string; notas: string; criado: string };

const SALVAR_MS = 700;
const CHAVE_PASTA = "forja.design.pasta-sistema";   // depois do último movimento: um passo no rascunho, não um por pixel
const FONTES = [
  "system-ui, sans-serif", '"Segoe UI", system-ui, sans-serif', "Georgia, serif", '"Palatino Linotype", Palatino, serif',
  '"Times New Roman", serif', '"Trebuchet MS", sans-serif', "Verdana, sans-serif", "ui-monospace, monospace",
];
const GRUPOS: { titulo: string; casa: RegExp }[] = [
  { titulo: "Cores", casa: /^--cor/ },
  { titulo: "Tipografia", casa: /^--(fonte|texto)/ },
  { titulo: "Espaçamentos", casa: /^--esp/ },
  { titulo: "Raios", casa: /^--raio/ },
  { titulo: "Sombras", casa: /^--sombra/ },
  { titulo: "Outros", casa: /./ },
];
const campo = "min-w-0 rounded-md border border-line bg-raised px-1.5 py-0.5 font-mono text-[11.5px] text-fg focus:border-focus focus:outline-none";

/** Ajuste criado pela IA: o rótulo e a faixa moram num comentário na mesma linha do token. */
export type Tweak = { rotulo: string; min: number; max: number; unidade: string; passo: number };

/** Tokens do primeiro :root do documento, na ordem em que aparecem. */
export function lerTokens(html: string): [string, string][] {
  const bloco = /:root\s*\{([^}]*)\}/.exec(html)?.[1] ?? "";
  return [...bloco.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/(--[\w-]+)\s*:\s*([^;]+);?/g)].map((m) => [m[1], m[2].trim()]);
}

export function lerTweaks(html: string): Record<string, Tweak> {
  const bloco = /:root\s*\{([^}]*)\}/.exec(html)?.[1] ?? "";
  const out: Record<string, Tweak> = {};
  for (const m of bloco.matchAll(/(--[\w-]+)\s*:[^;]*;\s*\/\*\s*ajuste:\s*([^|*]+)\|\s*(-?[\d.]+)\.\.(-?[\d.]+)\s*([a-z%]*)\s*\|\s*([\d.]+)\s*\*\//g))
    out[m[1]] = { rotulo: m[2].trim(), min: +m[3], max: +m[4], unidade: m[5], passo: +m[6] || 1 };
  return out;
}

const hex6 = (v: string) => {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(v.trim());
  if (!m) return null;
  return m[1].length === 3 ? "#" + [...m[1]].map((c) => c + c).join("") : v.trim();
};
const numero = (v: string) => {
  const m = /^(-?\d*\.?\d+)(px|rem|em|%|vh|vw)?$/.exec(v.trim());
  return m ? { n: Number(m[1]), u: m[2] ?? "" } : null;
};

function Controle(props: { nome: string; valor: string; onChange: (v: string) => void; tweak?: Tweak }) {
  const { nome, valor, tweak } = props;
  if (tweak) {
    const n = parseFloat(valor);
    return (
      <label className="flex items-center gap-2" title={nome}>
        <span className="w-24 shrink-0 truncate text-[12px] text-fg-2">{tweak.rotulo}</span>
        <input type="range" min={tweak.min} max={tweak.max} step={tweak.passo} value={Number.isFinite(n) ? n : tweak.min}
               onChange={(e) => props.onChange(`${+Number(e.target.value).toFixed(3)}${tweak.unidade}`)} className="min-w-0 flex-1 accent-[var(--accent)]" />
        <span className="w-14 shrink-0 text-right font-mono text-[11px] text-fg-2">{valor}</span>
      </label>
    );
  }
  const cor = hex6(valor);
  const num = numero(valor);
  const rotulo = <span className="w-24 shrink-0 truncate font-mono text-[11px] text-muted" title={nome}>{nome.replace(/^--/, "")}</span>;
  if (cor)
    return (
      <label className="flex items-center gap-2">
        {rotulo}
        <input type="color" value={cor} onChange={(e) => props.onChange(e.target.value)} className="size-6 shrink-0 cursor-pointer rounded border-0 bg-transparent p-0" />
        <input value={valor} onChange={(e) => props.onChange(e.target.value)} className={`${campo} w-20`} />
      </label>
    );
  if (nome.startsWith("--fonte"))
    return (
      <label className="flex items-center gap-2">
        {rotulo}
        <select value={valor} onChange={(e) => props.onChange(e.target.value)} className={`${campo} flex-1`}>
          {[valor, ...FONTES.filter((f) => f !== valor)].map((f) => <option key={f} value={f}>{f.split(",")[0].replace(/"/g, "")}</option>)}
        </select>
      </label>
    );
  if (num) {
    const px = num.u === "px" || num.u === "";
    const max = px ? Math.max(64, Math.ceil(num.n * 3)) : Math.max(4, Math.ceil(num.n * 3));
    const passo = px ? 1 : 0.05;
    return (
      <label className="flex items-center gap-2">
        {rotulo}
        <input type="range" min={0} max={max} step={passo} value={num.n}
               onChange={(e) => props.onChange(`${+Number(e.target.value).toFixed(3)}${num.u}`)} className="min-w-0 flex-1 accent-[var(--accent)]" />
        <span className="w-14 shrink-0 text-right font-mono text-[11px] text-fg-2">{valor}</span>
      </label>
    );
  }
  return (
    <label className="flex items-center gap-2">
      {rotulo}
      <input value={valor} onChange={(e) => props.onChange(e.target.value)} className={`${campo} flex-1`} />
    </label>
  );
}

export default function DesignAjustes(props: {
  html: string;
  desabilitado: boolean;
  onPrevia: (tokens: Record<string, string>) => void;       // canvas ao vivo
  onSalvar: (tokens: Record<string, string>) => Promise<void>;
  sistemas: Sistema[];
  sistemaDoDoc: string | null;
  sistemaNovos: string;                                     // o que os próximos planos usam
  onSistemaNovos: (id: string) => void;
  onAplicarSistema: (id: string) => void;
  onExtrair: (pasta: string, nome: string) => Promise<void>;
  onApagar: (id: string) => void;
  pastaPadrao: string;
  selecionados: number;          // elementos selecionados no canvas: os ajustes da IA focam neles
  onCriarComIA: () => void;
  onVariacoes: () => void;
}) {
  const originais = useMemo(() => lerTokens(props.html), [props.html]);
  const tweaks = useMemo(() => lerTweaks(props.html), [props.html]);
  const [valores, setValores] = useState<Record<string, string>>({});
  // a última pasta escolhida fica guardada (sem isso, sair da aba voltava o campo para a padrão)
  const [pasta, setPastaEstado] = useState(() => { try { return localStorage.getItem(CHAVE_PASTA) || props.pastaPadrao; } catch { return props.pastaPadrao; } });
  const setPasta = (p: string) => { setPastaEstado(p); try { localStorage.setItem(CHAVE_PASTA, p); } catch { /* sem storage: só nesta aba */ } };
  const [nome, setNome] = useState("");
  const [extraindo, setExtraindo] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const salvar = useRef(props.onSalvar);
  salvar.current = props.onSalvar;

  // documento novo (salvo, desfeito, outra versão): os controles voltam a espelhar a fonte
  useEffect(() => setValores({}), [props.html]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  function mexe(nomeToken: string, v: string) {
    const novos = { ...valores, [nomeToken]: v };
    setValores(novos);
    props.onPrevia(novos);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const mudou = Object.fromEntries(Object.entries(novos).filter(([k, x]) => originais.find(([n]) => n === k)?.[1] !== x));
      if (Object.keys(mudou).length) salvar.current(mudou);
    }, SALVAR_MS);
  }

  async function escolherPasta() {
    const p = await ponte()?.pickFolder?.(pasta || props.pastaPadrao);
    if (p) setPasta(p);
  }

  const vistos = new Set<string>(Object.keys(tweaks));
  const daIA = originais.filter(([n]) => tweaks[n]);
  return (
    <div className="flex flex-col gap-4 py-2 text-[13px]">
      {!originais.length && <p className="text-xs text-muted">Este documento não tem tokens no :root ainda.</p>}
      <section>
        <div className="mb-1.5 flex items-center gap-2">
          <h3 className="font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">Criados pela IA</h3>
          <span className="flex-1" />
          <button disabled={props.desabilitado} onClick={props.onVariacoes}
                  title="Três direções visuais (só tokens) para ver lado a lado no chat e escolher"
                  className="rounded-md border border-line px-2 py-0.5 text-[11.5px] text-fg hover:bg-raised disabled:opacity-40">
            Ver 3 variações
          </button>
          <button disabled={props.desabilitado} onClick={props.onCriarComIA}
                  title="A IA cria sliders para esta página (ou para os elementos selecionados). O texto do campo, se houver, diz o foco."
                  className="rounded-md border border-line px-2 py-0.5 text-[11.5px] text-fg hover:bg-raised disabled:opacity-40">
            {props.selecionados ? `Criar ajustes para ${props.selecionados === 1 ? "o elemento" : `${props.selecionados} elementos`}` : "Criar ajustes com a IA"}
          </button>
        </div>
        {daIA.length ? (
          <div className={`flex flex-col gap-1.5 ${props.desabilitado ? "pointer-events-none opacity-50" : ""}`}>
            {daIA.map(([n, v]) => <Controle key={n} nome={n} valor={valores[n] ?? v} tweak={tweaks[n]} onChange={(x) => mexe(n, x)} />)}
          </div>
        ) : (
          <p className="text-[11.5px] text-faint">Controles sob medida para este design (altura do hero, colunas, respiro...), criados numa chamada e depois ajustados sem IA.</p>
        )}
      </section>
      {GRUPOS.map((g) => {
        const itens = originais.filter(([n]) => !vistos.has(n) && g.casa.test(n));
        itens.forEach(([n]) => vistos.add(n));
        return itens.length ? (
          <section key={g.titulo}>
            <h3 className="mb-1.5 font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">{g.titulo}</h3>
            <div className={`flex flex-col gap-1.5 ${props.desabilitado ? "pointer-events-none opacity-50" : ""}`}>
              {itens.map(([n, v]) => <Controle key={n} nome={n} valor={valores[n] ?? v} onChange={(x) => mexe(n, x)} />)}
            </div>
          </section>
        ) : null;
      })}
      <p className="text-[11.5px] text-faint">Mexe no canvas na hora e vai para o rascunho (Salvar versão quando quiser) — sem chamar o modelo.</p>

      <section className="border-t border-line pt-3">
        <h3 className="mb-1.5 font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">Design systems</h3>
        <p className="mb-2 text-[11.5px] text-faint">Tirados do código de um projeto (CSS, Tailwind, componentes). O escolhido em
          “Próximos planos” manda nos tokens e no CSS dos designs novos.</p>
        <label className="mb-2 flex items-center gap-2 text-xs text-muted">
          Próximos planos
          <select value={props.sistemaNovos} onChange={(e) => props.onSistemaNovos(e.target.value)} className={`${campo} flex-1 font-sans`}>
            <option value="">nenhum (o modelo escolhe)</option>
            {props.sistemas.map((s) => <option key={s.id} value={s.id}>{s.nome}</option>)}
          </select>
        </label>
        <div className="flex flex-col gap-1.5">
          {props.sistemas.map((s) => (
            <div key={s.id} className={`rounded-lg border p-2 ${props.sistemaDoDoc === s.id ? "border-accent-line bg-accent-soft/40" : "border-line"}`}>
              <div className="flex items-center gap-2">
                <div className="flex shrink-0 -space-x-1">
                  {Object.entries(s.tokens).filter(([, v]) => hex6(v)).slice(0, 5).map(([k, v]) => (
                    <span key={k} title={`${k}: ${v}`} className="size-3.5 rounded-full border border-surface" style={{ background: v }} />
                  ))}
                </div>
                <span className="min-w-0 flex-1 truncate text-fg" title={s.pasta}>{s.nome}</span>
                {props.sistemaDoDoc === s.id ? (
                  <span className="text-[11px] text-accent-text">neste design</span>
                ) : (
                  <button disabled={props.desabilitado || !props.html} onClick={() => props.onAplicarSistema(s.id)}
                          className="rounded border border-line px-1.5 text-[11px] text-fg hover:bg-raised disabled:opacity-40">Aplicar aqui</button>
                )}
                <button onClick={() => props.onApagar(s.id)} title="Apagar este design system" className="p-0.5 text-faint hover:text-red-300">
                  <Trash className="size-3.5" />
                </button>
              </div>
              {s.notas && <p className="mt-1 text-[11.5px] text-faint">{s.notas}</p>}
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-col gap-1.5 rounded-lg border border-dashed border-line p-2">
          <div className="flex items-center gap-1.5">
            <input value={pasta} onChange={(e) => setPasta(e.target.value)} placeholder="Pasta do projeto" className={`${campo} flex-1`} />
            {ponte()?.pickFolder && (
              <button onClick={escolherPasta} title="Escolher pasta" className="rounded p-1 text-muted hover:bg-raised hover:text-fg">
                <FolderOpen className="size-4" />
              </button>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <input value={nome} onChange={(e) => setNome(e.target.value)} placeholder="Nome (opcional)" className={`${campo} flex-1 font-sans`} />
            <button disabled={!pasta.trim() || extraindo}
                    onClick={async () => {
                      setExtraindo(true);
                      try {
                        await props.onExtrair(pasta.trim(), nome.trim());
                        setNome("");
                      } finally {
                        setExtraindo(false);
                      }
                    }}
                    className="rounded-md bg-accent px-2 py-0.5 text-xs font-medium text-accent-fg hover:brightness-110 disabled:opacity-40">
              {extraindo ? "Lendo o código…" : "Criar do código"}
            </button>
          </div>
          <p className="text-[11px] text-faint">Lê a pasta de um projeto (CSS, Tailwind, componentes) ou de um design system pronto (DESIGN.md, tokens em JSON, fontes) e resume; o modelo de edição só vê esse resumo.</p>
        </div>
      </section>
    </div>
  );
}

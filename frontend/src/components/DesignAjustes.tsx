import { useEffect, useMemo, useRef, useState } from "react";
import { ponte } from "./designCanvas";
import { Vazio, botaoItem, botaoPrincipal, item, tituloSecao } from "./designUi";
import { FolderOpen, Sliders, Trash } from "./icons";

// Aba Ajustes da tela Design: os tokens do :root viram controles (cor, slider, fonte) que mexem no
// canvas ao vivo e vão para o rascunho, sem chamar modelo nenhum; e os design systems (tirados do código
// de um projeto) para aplicar aqui ou usar nos próximos planos.

/** Design system sendo criado agora (mora no DesignView: sobrevive à troca de aba). */
export type CriandoSistema = { pasta: string; nome: string; modelo: string; desde: number };

function Criando({ c }: { c: CriandoSistema }) {
  const [agora, setAgora] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setAgora(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const s = Math.max(0, Math.round((agora - c.desde) / 1000));
  const pasta = c.pasta.split(/[\\/]/).filter(Boolean).slice(-2).join("/");
  return (
    <div role="status" aria-live="polite" className="overflow-hidden rounded-lg border border-accent-line bg-accent-soft/40">
      <div className="flex items-center gap-2.5 p-2.5">
        <span className="size-4 shrink-0 animate-spin rounded-full border-2 border-accent/25 border-t-accent" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] text-fg">Criando “{c.nome || pasta}”…</div>
          <div className="truncate text-[11.5px] text-muted">
            Lendo <span className="font-mono text-[11px]">{pasta}</span> e resumindo com {c.modelo}
          </div>
        </div>
        <span className="shrink-0 font-mono text-[11px] text-faint tabular-nums">{Math.floor(s / 60)}:{String(s % 60).padStart(2, "0")}</span>
      </div>
      <div className="h-0.5 overflow-hidden bg-accent/10">
        <div className="corre h-full w-2/5 rounded-full bg-gradient-to-r from-transparent via-accent to-transparent" />
      </div>
    </div>
  );
}

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
  criando?: CriandoSistema | null;   // design system sendo criado (spinner na lista)
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
  const extraindo = !!props.criando;
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
    <div className="flex flex-col gap-5 px-1 py-2 text-[13px]">
      {originais.length ? (
        <p className="text-[11.5px] leading-snug text-faint">Tudo aqui muda o canvas na hora e vai para o rascunho, sem chamar o modelo.</p>
      ) : (
        <Vazio icone={<Sliders className="size-4" />} titulo="Sem tokens ainda">Os controles aparecem quando o design tiver tokens no :root (a IA cria no primeiro plano).</Vazio>
      )}
      <section>
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <h3 className={`${tituloSecao} mr-auto`}>Criados pela IA</h3>
          <button disabled={props.desabilitado} onClick={props.onVariacoes}
                  title="Três direções visuais (só tokens) para ver lado a lado no chat e escolher" className={botaoItem}>
            3 variações
          </button>
          <button disabled={props.desabilitado} onClick={props.onCriarComIA}
                  title="A IA cria sliders para esta página (ou para os elementos selecionados). O texto do campo, se houver, diz o foco."
                  className={botaoItem}>
            {props.selecionados ? `Criar ajustes para ${props.selecionados === 1 ? "o elemento" : `${props.selecionados} elementos`}` : "Criar ajustes"}
          </button>
        </div>
        {daIA.length ? (
          <div className={`flex flex-col gap-1.5 ${props.desabilitado ? "pointer-events-none opacity-50" : ""}`}>
            {daIA.map(([n, v]) => <Controle key={n} nome={n} valor={valores[n] ?? v} tweak={tweaks[n]} onChange={(x) => mexe(n, x)} />)}
          </div>
        ) : (
          <p className="text-[11.5px] leading-snug text-faint">Controles sob medida para este design (altura do hero, colunas, respiro…): a IA cria numa chamada, depois você ajusta sem IA.</p>
        )}
      </section>
      {GRUPOS.map((g) => {
        const itens = originais.filter(([n]) => !vistos.has(n) && g.casa.test(n));
        itens.forEach(([n]) => vistos.add(n));
        return itens.length ? (
          <section key={g.titulo}>
            <h3 className={`${tituloSecao} mb-2`}>{g.titulo}</h3>
            <div className={`flex flex-col gap-1.5 ${props.desabilitado ? "pointer-events-none opacity-50" : ""}`}>
              {itens.map(([n, v]) => <Controle key={n} nome={n} valor={valores[n] ?? v} onChange={(x) => mexe(n, x)} />)}
            </div>
          </section>
        ) : null;
      })}

      <section className="border-t border-line pt-4">
        <h3 className={`${tituloSecao} mb-1.5`}>Design systems</h3>
        <p className="mb-2.5 text-[11.5px] leading-snug text-faint">O escolhido em “Próximos planos” manda nos tokens, no CSS e na logo dos designs novos.</p>
        <label className="mb-2.5 flex items-center gap-2 text-[12px] text-muted">
          Próximos planos
          <select value={props.sistemaNovos} onChange={(e) => props.onSistemaNovos(e.target.value)} className={`${campo} flex-1 font-sans`}>
            <option value="">nenhum (o modelo escolhe)</option>
            {props.sistemas.map((s) => <option key={s.id} value={s.id}>{s.nome}</option>)}
          </select>
        </label>
        <div className="flex flex-col gap-1.5">
          {props.criando && <Criando c={props.criando} />}
          {props.sistemas.map((s) => (
            <div key={s.id} className={`${item} p-2.5 ${props.sistemaDoDoc === s.id ? "border-accent-line bg-accent-soft/40" : ""}`}>
              <div className="flex items-center gap-2">
                <div className="flex shrink-0 -space-x-1">
                  {Object.entries(s.tokens).filter(([, v]) => hex6(v)).slice(0, 5).map(([k, v]) => (
                    <span key={k} title={`${k}: ${v}`} className="size-3.5 rounded-full border border-surface" style={{ background: v }} />
                  ))}
                </div>
                <span className="min-w-0 flex-1 truncate text-fg" title={s.pasta}>{s.nome}</span>
                {props.sistemaDoDoc === s.id ? (
                  <span className="rounded-full bg-accent-soft px-1.5 text-[10.5px] leading-4 text-accent-text">neste design</span>
                ) : (
                  <button disabled={props.desabilitado || !props.html} onClick={() => props.onAplicarSistema(s.id)} className={botaoItem}>Aplicar aqui</button>
                )}
                <button onClick={() => props.onApagar(s.id)} title="Apagar este design system" aria-label={`Apagar ${s.nome}`}
                        className="grid size-6 place-items-center rounded-md text-faint transition-colors hover:bg-raised hover:text-err">
                  <Trash className="size-3.5" />
                </button>
              </div>
              {s.notas && <p className="mt-1.5 line-clamp-3 text-[11.5px] leading-snug text-faint" title={s.notas}>{s.notas}</p>}
            </div>
          ))}
        </div>
        <div className="mt-2.5 flex flex-col gap-1.5 rounded-xl border border-dashed border-line p-2.5">
          <span className="text-[12px] font-medium text-fg-2">Novo, a partir de uma pasta</span>
          <div className="flex items-center gap-1.5">
            <input value={pasta} onChange={(e) => setPasta(e.target.value)} placeholder="Pasta do projeto" className={`${campo} flex-1`} />
            {ponte()?.pickFolder && (
              <button onClick={escolherPasta} title="Escolher pasta" aria-label="Escolher pasta"
                      className="grid size-7 place-items-center rounded-md text-muted transition-colors hover:bg-raised hover:text-fg">
                <FolderOpen className="size-4" />
              </button>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <input value={nome} onChange={(e) => setNome(e.target.value)} placeholder="Nome (opcional)" className={`${campo} flex-1 font-sans`} />
            <button disabled={!pasta.trim() || extraindo}
                    onClick={async () => {
                      await props.onExtrair(pasta.trim(), nome.trim());
                      setNome("");
                    }}
                    className={botaoPrincipal}>
              {extraindo ? "Criando…" : "Criar"}
            </button>
          </div>
          <p className="text-[11px] leading-snug text-faint">Projeto (CSS, Tailwind, componentes) ou design system pronto (DESIGN.md, tokens JSON, fontes, logo SVG).</p>
        </div>
      </section>
    </div>
  );
}

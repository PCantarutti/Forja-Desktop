import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Check, Download } from "./icons";
import { btn, btnPrimary } from "./LocalPanel";

/** O que o backend diz da ampliação: ffmpeg instalado, ESRGAN do catálogo e todos os que estão no disco. */
export type CatalogoAmpliacao = {
  modelos: { nome: string; resumo: string; mb: number; presente: string }[];
  no_disco: { path: string; name: string }[];
  ffmpeg: string;
  erro: string;
};

/** Catálogo com recarga enquanto algo baixa (o download corre no backend; aqui só se espera o arquivo). */
function useCatalogo(onError: (e: string) => void) {
  const [cat, setCat] = useState<CatalogoAmpliacao | null>(null);
  const [baixando, setBaixando] = useState<Set<string>>(new Set());
  const carregar = useCallback(() => {
    api.get<CatalogoAmpliacao>("/local/video/ampliadores").then((c) => {
      setCat(c);
      setBaixando((b) => new Set([...b].filter((n) => (n === "ffmpeg" ? !c.ffmpeg : !c.modelos.find((m) => m.nome === n)?.presente))));
    }).catch((e) => onError(e.message));
  }, []);
  useEffect(carregar, [carregar]);
  useEffect(() => {
    if (!baixando.size) return;
    const t = setInterval(carregar, 3000); // ponytail: sondagem simples; a falha do download aparece na lista de downloads
    return () => clearInterval(t);
  }, [baixando.size, carregar]);

  async function baixar(nome: string) {
    try {
      if (nome === "ffmpeg") await api.post("/local/runtime", { kind: "ffmpeg", backend: "cpu" });
      else await api.post("/local/video/ampliador", { nome });
      setBaixando((b) => new Set(b).add(nome));
    } catch (e: any) {
      onError(e.message);
    }
  }
  return { cat, baixando, baixar, carregar };
}

/** Os downloads da ampliação: o ffmpeg (obrigatório) e os ESRGAN (opcionais: sem eles, é Lanczos). */
export function BaixarAmpliacao(props: { onError: (e: string) => void; soFaltando?: boolean; estado?: ReturnType<typeof useCatalogo> }) {
  const proprio = useCatalogo(props.onError);
  const { cat, baixando, baixar } = props.estado ?? proprio;
  if (!cat) return <p className="text-xs text-muted">Carregando…</p>;
  const linhas = [
    { nome: "ffmpeg", titulo: "ffmpeg", resumo: "Lê e grava o vídeo (obrigatório) · ~80 MB", presente: !!cat.ffmpeg },
    ...cat.modelos.map((m) => ({
      nome: m.nome,
      titulo: m.nome.replace(/\.pth$/, ""),
      resumo: `${m.resumo}${m.mb ? ` · ${Math.round(m.mb)} MB` : ""}`,
      presente: !!m.presente,
    })),
  ].filter((l) => !props.soFaltando || !l.presente);
  return (
    <div className="flex flex-col gap-1.5 text-xs">
      {cat.erro && <p className="text-amber-400">{cat.erro}</p>}
      {linhas.map((l) => (
        <div key={l.nome} className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-1.5">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-fg">{l.titulo}</span>
            <span className="block text-faint">{l.resumo}</span>
          </span>
          {l.presente ? (
            <span className="shrink-0 text-emerald-400"><Check className="mr-0.5 inline size-3" />pronto</span>
          ) : baixando.has(l.nome) ? (
            <span className="shrink-0 text-sky-300">baixando…</span>
          ) : (
            <button className={`${btn} shrink-0`} onClick={() => baixar(l.nome)} disabled={l.nome !== "ffmpeg" && !cat.modelos.find((m) => m.nome === l.nome)?.mb}>
              <Download className="mr-1 inline size-3" />
              Baixar
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/** Painel do player: método (ESRGAN no disco ou Lanczos), fator e suavizar. O resultado é uma tomada nova no feed. */
export function PainelAmpliar(props: {
  messageId: number;
  path: string;
  w: number;
  h: number;
  fps: number;
  onPronto: () => void;
  onError: (e: string) => void;
}) {
  const estado = useCatalogo(props.onError);
  const { cat } = estado;
  const [modelo, setModelo] = useState<string | null>(null); // null = ainda não escolheu: o 1º ESRGAN do disco
  const [fator, setFator] = useState<2 | 4>(2);
  const [suavizar, setSuavizar] = useState(false);
  const [enviando, setEnviando] = useState(false);
  // sem escolha: o ESRGAN do mesmo fator (um 4× para 2× faz o dobro do trabalho e o Lanczos joga fora)
  const escolhido = modelo ?? (cat?.no_disco.find((m) => new RegExp(`x${fator}(?!\\d)`, "i").test(m.name)) ?? cat?.no_disco[0])?.path ?? "";

  async function ampliar() {
    setEnviando(true);
    try {
      await api.post(`/imagens/${props.messageId}/ampliar`, { path: props.path, fator, modelo: escolhido, suavizar });
      props.onPronto();
    } catch (e: any) {
      props.onError(e.message);
    } finally {
      setEnviando(false);
    }
  }

  if (!cat) return <p className="px-2.5 text-xs text-muted">Carregando…</p>;
  const falta = !cat.ffmpeg || cat.modelos.some((m) => !m.presente);
  const opcao = (ligada: boolean) =>
    `rounded-md border px-2 py-1 text-xs ${ligada ? "border-sky-500/60 bg-sky-500/10 text-sky-200" : "border-line text-muted hover:bg-raised hover:text-fg"}`;
  return (
    <div className="flex flex-col gap-2.5 px-1 text-xs">
      <label className="flex flex-col gap-1">
        <span className="text-faint">Método</span>
        <select
          className="rounded-md border border-line bg-raised px-2 py-1 text-fg"
          value={escolhido}
          onChange={(e) => setModelo(e.target.value)}
        >
          {cat.no_disco.map((m) => <option key={m.path} value={m.path}>{m.name} (IA, quadro a quadro)</option>)}
          <option value="">Rápido, sem IA (Lanczos)</option>
        </select>
      </label>
      <div className="flex items-center gap-1.5">
        <span className="mr-1 text-faint">Fator</span>
        {([2, 4] as const).map((f) => (
          <button key={f} className={opcao(fator === f)} aria-pressed={fator === f} onClick={() => setFator(f)}>
            {f}× <span className="text-faint">{props.w * f}×{props.h * f}</span>
          </button>
        ))}
      </div>
      <label className="flex items-center gap-2 text-muted" title="Interpolação de movimento do ffmpeg: o dobro de quadros, sem gerar de novo">
        <input type="checkbox" checked={suavizar} onChange={(e) => setSuavizar(e.target.checked)} />
        Suavizar movimento ({props.fps} → {props.fps * 2} fps)
      </label>
      <button className={btnPrimary} disabled={!cat.ffmpeg || enviando} onClick={ampliar}>
        {enviando ? "Começando…" : `Ampliar ${fator}×`}
      </button>
      {!cat.ffmpeg && <p className="text-amber-400">Falta o ffmpeg para ler e gravar o vídeo:</p>}
      {falta && (
        <details open={!cat.ffmpeg || !cat.no_disco.length}>
          <summary className="cursor-pointer text-faint hover:text-fg">Baixar o que falta</summary>
          <div className="mt-1.5"><BaixarAmpliacao onError={props.onError} soFaltando estado={estado} /></div>
        </details>
      )}
    </div>
  );
}

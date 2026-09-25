import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Check, Download, Film, Image, X } from "./icons";
import { btn, btnPrimary } from "./LocalPanel";

/** O que o backend diz da ampliação: ffmpeg (vídeo) e ComfyUI (SeedVR2) instalados, o catálogo e todos os
 *  modelos no disco. `tipo`: esrgan (sd-cli, rápido, imagem e vídeo) ou seedvr2 (difusão, pesado, só imagem). */
type Tipo = "esrgan" | "seedvr2";
export type CatalogoAmpliacao = {
  modelos: { nome: string; resumo: string; mb: number; presente: string; tipo: Tipo }[];
  no_disco: { path: string; name: string; tipo: Tipo }[];
  ffmpeg: string;
  comfy: { instalado: string; gpu: string; mb: number; versao: string };
  erro: string;
};
const GPU: Record<string, string> = { intel: "Intel", amd: "AMD", nvidia: "NVIDIA" };

/** Catálogo com recarga enquanto algo baixa (o download corre no backend; aqui só se espera o arquivo). */
function useCatalogo(onError: (e: string) => void) {
  const [cat, setCat] = useState<CatalogoAmpliacao | null>(null);
  const [baixando, setBaixando] = useState<Set<string>>(new Set());
  const carregar = useCallback(() => {
    api.get<CatalogoAmpliacao>("/local/video/ampliadores").then((c) => {
      setCat(c);
      setBaixando((b) => new Set([...b].filter((n) => (n === "ffmpeg" ? !c.ffmpeg : n === "comfyui" ? !c.comfy.instalado
        : !c.modelos.find((m) => m.nome === n)?.presente))));
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

/** Os downloads da ampliação: o ffmpeg (vídeo), o ComfyUI (motor do SeedVR2) e os modelos (opcionais: sem
 *  eles, é Lanczos). `video`: só o que amplia vídeo (sem SeedVR2 e sem ComfyUI); `imagem`: sem o ffmpeg. */
export function BaixarAmpliacao(props: {
  onError: (e: string) => void;
  soFaltando?: boolean;
  soModelos?: boolean; // o ffmpeg já aparece como motor (card de runtime) acima
  video?: boolean;
  imagem?: boolean;
  estado?: ReturnType<typeof useCatalogo>;
}) {
  const proprio = useCatalogo(props.onError);
  const { cat, baixando, baixar } = props.estado ?? proprio;
  if (!cat) return <p className="text-xs text-muted">Carregando…</p>;
  const linhas = [
    ...(props.imagem ? [] : [{ nome: "ffmpeg", titulo: "ffmpeg", resumo: "Lê e grava o vídeo (obrigatório) · ~80 MB", presente: !!cat.ffmpeg }]),
    ...(props.video ? [] : [{ nome: "comfyui", titulo: `ComfyUI portátil (${GPU[cat.comfy.gpu] ?? cat.comfy.gpu})`,
      resumo: `Motor do SeedVR2, fica com os runtimes · ~${(cat.comfy.mb / 1000).toFixed(1).replace(".", ",")} GB`, presente: !!cat.comfy.instalado }]),
    ...cat.modelos.filter((m) => !props.video || m.tipo === "esrgan").map((m) => ({
      nome: m.nome,
      titulo: m.nome.replace(/\.(pth|safetensors)$/, ""),
      resumo: `${m.resumo}${m.mb ? ` · ${m.mb >= 1000 ? `${(m.mb / 1000).toFixed(1).replace(".", ",")} GB` : `${Math.round(m.mb)} MB`}` : ""}`,
      presente: !!m.presente,
    })),
  ].filter((l) => (!props.soFaltando || !l.presente) && (!props.soModelos || l.nome !== "ffmpeg"));
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
            <button className={`${btn} shrink-0`} onClick={() => baixar(l.nome)}
              disabled={l.nome !== "ffmpeg" && l.nome !== "comfyui" && !cat.modelos.find((m) => m.nome === l.nome)?.mb}>
              <Download className="mr-1 inline size-3" />
              Baixar
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

const fmtFps = (f: number) => (Number.isInteger(f) ? String(f) : f.toFixed(2).replace(".", ","));

/** Método, fator e suavizar. `enviar` cria a tomada (de uma tomada do feed ou de um arquivo do PC).
 *  `imagem`: sem suavizar e sem ffmpeg, e com o SeedVR2 (difusão, pelo ComfyUI) entre os métodos. Com um modelo
 *  de texto na VRAM, o SeedVR2 volta 409 e o painel pergunta antes de descarregar (enviar de novo com confirm). */
export function PainelAmpliar(props: {
  w: number;
  h: number;
  fps?: number;
  quadros?: number;
  imagem?: boolean;
  enviar: (corpo: { fator: number; modelo: string; suavizar: boolean; confirm?: boolean }) => Promise<void>;
  onError: (e: string) => void;
}) {
  const estado = useCatalogo(props.onError);
  const { cat } = estado;
  const [modelo, setModelo] = useState<string | null>(null); // null = ainda não escolheu
  const [fator, setFator] = useState<2 | 4>(2);
  const [suavizar, setSuavizar] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [vram, setVram] = useState(""); // a mensagem do 409: tem modelo de texto carregado
  const metodos = (cat?.no_disco ?? []).filter((m) => props.imagem || m.tipo === "esrgan");
  const esrgans = metodos.filter((m) => m.tipo === "esrgan");
  // sem escolha: o ESRGAN do mesmo fator (um 4× para 2× faz o dobro do trabalho e o Lanczos joga fora); o
  // SeedVR2 nunca é o padrão, leva minutos
  const escolhido = modelo ?? (esrgans.find((m) => new RegExp(`x${fator}(?!\d)`, "i").test(m.name)) ?? esrgans[0])?.path ?? "";
  const pesado = metodos.find((m) => m.path === escolhido)?.tipo === "seedvr2";
  const semComfy = pesado && !cat?.comfy.instalado;

  async function ampliar(confirm = false) {
    setEnviando(true);
    try {
      await props.enviar({ fator, modelo: escolhido, suavizar: suavizar && !props.imagem, ...(confirm ? { confirm } : {}) });
      setVram("");
    } catch (e: any) {
      if (e.status === 409) setVram(e.message || "Tem um modelo carregado na VRAM.");
      else props.onError(e.message);
    } finally {
      setEnviando(false);
    }
  }

  if (!cat) return <p className="px-2.5 text-xs text-muted">Carregando…</p>;
  const semFfmpeg = !cat.ffmpeg && !props.imagem;
  const falta = semFfmpeg || (props.imagem && !cat.comfy.instalado)
    || cat.modelos.some((m) => !m.presente && (props.imagem || m.tipo === "esrgan"));
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
          {metodos.map((m) => (
            <option key={m.path} value={m.path}>
              {m.name} ({m.tipo === "seedvr2" ? "IA pesada, leva minutos" : props.imagem ? "IA" : "IA, quadro a quadro"})
            </option>
          ))}
          <option value="">Rápido, sem IA (Lanczos)</option>
        </select>
      </label>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-faint">Fator</span>
        {([2, 4] as const).map((f) => (
          <button key={f} className={opcao(fator === f)} aria-pressed={fator === f} onClick={() => setFator(f)}>
            {f}× {!!props.w && <span className="text-faint">{props.w * f}×{props.h * f}</span>}
          </button>
        ))}
      </div>
      {!props.imagem && (
        <label className="flex items-center gap-2 text-muted" title="Interpolação de movimento do ffmpeg: o dobro de quadros, sem gerar de novo">
          <input type="checkbox" checked={suavizar} onChange={(e) => setSuavizar(e.target.checked)} />
          Suavizar movimento ({fmtFps(props.fps ?? 0)} → {fmtFps((props.fps ?? 0) * 2)} fps)
        </label>
      )}
      {escolhido && !!props.quadros && (
        <p className="text-faint">{props.quadros} quadros, cada um passa pelo ESRGAN na GPU: vídeo longo leva tempo (o cartão mostra quanto falta).</p>
      )}
      {pesado && !semComfy && (
        <p className="text-faint">Difusão: reconstrói textura e detalhe, mas usa ~7 GB de VRAM e leva de 1 a alguns minutos (a 1ª vez, mais).</p>
      )}
      {semComfy && <p className="text-amber-400">O SeedVR2 roda no ComfyUI portátil: baixe em "Baixar o que falta".</p>}
      <button className={btnPrimary} disabled={semFfmpeg || semComfy || enviando} onClick={() => ampliar()}>
        {enviando ? "Começando…" : `Ampliar ${fator}×`}
      </button>
      {vram && (
        <div className="flex flex-col gap-1.5 rounded-lg border border-amber-800/70 bg-amber-950/30 p-2 text-amber-200">
          <span>{vram.startsWith("Outro programa") ? vram : `${vram} O SeedVR2 precisa dessa memória.`}</span>
          <div className="flex gap-1.5">
            <button className={btnPrimary} disabled={enviando} onClick={() => ampliar(true)}>
              {vram.startsWith("Outro programa") ? "Ampliar mesmo assim" : "Descarregar e ampliar"}
            </button>
            <button className={btn} onClick={() => setVram("")}>Cancelar</button>
          </div>
        </div>
      )}
      {semFfmpeg && <p className="text-amber-400">Falta o ffmpeg para ler e gravar o vídeo:</p>}
      {falta && (
        <details open={semFfmpeg || semComfy || !metodos.length}>
          <summary className="cursor-pointer text-faint hover:text-fg">Baixar o que falta</summary>
          <div className="mt-1.5">
            <BaixarAmpliacao onError={props.onError} soFaltando imagem={props.imagem} video={!props.imagem} estado={estado} />
          </div>
        </details>
      )}
    </div>
  );
}

type Sondagem = { w: number; h: number; fps: number; quadros: number; audio: boolean };

/** Um vídeo (ou, com `imagem`, uma imagem) qualquer do PC: escolher ou soltar, ver o que ele é e ampliar.
 *  O original não é tocado; o resultado entra no feed como tomada. */
export function AmpliarArquivo(props: {
  imagem?: boolean;
  ensureConversation: () => Promise<number>;
  onPronto: (conv: number) => void;
  onError: (e: string) => void;
}) {
  const [arq, setArq] = useState<{ path: string; nome: string; url: string } | null>(null);
  const [info, setInfo] = useState<Sondagem | null>(null);
  const [lendo, setLendo] = useState(false);
  const [sobre, setSobre] = useState(false);
  const [semPrevia, setSemPrevia] = useState(false); // formato que o Chromium não toca (MPEG-4 part 2, HEVC…)
  const entrada = useRef<HTMLInputElement>(null);

  useEffect(() => () => {
    if (arq) URL.revokeObjectURL(arq.url);
  }, [arq]);

  function remover() {
    setArq(null);
    setInfo(null);
    setLendo(false);
  }

  async function escolher(f: File | undefined) {
    if (!f) return;
    const path = window.forja?.caminhoDe?.(f);
    if (!path) return props.onError(`Ess${props.imagem ? "a imagem" : "e vídeo"} não veio de um arquivo do disco. Escolha o arquivo.`);
    setArq({ path, nome: f.name, url: URL.createObjectURL(f) });
    setSemPrevia(false);
    setInfo(null);
    if (props.imagem) return; // o tamanho sai do <img> da prévia (onLoad)
    setLendo(true);
    try {
      setInfo(await api.get<Sondagem>(`/local/video/sondar?path=${encodeURIComponent(path)}`));
    } catch (e: any) {
      setArq(null);
      props.onError(e.message);
    } finally {
      setLendo(false);
    }
  }

  return (
    <div
      className="mb-2 flex flex-wrap items-start gap-3"
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setSobre(true);
      }}
      onDragLeave={() => setSobre(false)}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        setSobre(false);
        escolher(e.dataTransfer.files[0]);
      }}
    >
      <input
        ref={entrada}
        type="file"
        accept={props.imagem ? "image/png,image/jpeg,image/webp" : "video/*,.mkv,.mov,.avi,.webm"}
        hidden
        onChange={(e) => {
          escolher(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <div className="relative w-56 shrink-0">
      <button
        onClick={() => entrada.current?.click()}
        className={`relative grid w-full place-items-center overflow-hidden rounded-xl border border-dashed text-xs transition-colors ${
          sobre ? "border-sky-400 bg-sky-400/10 text-sky-200" : "border-line text-muted hover:border-[#454545] hover:text-fg"
        }`}
        style={{ aspectRatio: info ? info.w / info.h : 16 / 9 }}
        title={props.imagem ? (arq ? "Escolher outra imagem" : "Escolher uma imagem do PC") : arq ? "Escolher outro vídeo" : "Escolher um vídeo do PC"}
      >
        {arq && props.imagem ? (
          <img src={arq.url} alt={arq.nome} className="size-full bg-black object-contain"
            onLoad={(e) => setInfo({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight, fps: 0, quadros: 0, audio: false })}
            onError={() => { setArq(null); props.onError("Não consegui abrir essa imagem (use PNG, JPG ou WebP)."); }}
          />
        ) : arq ? (
          semPrevia ? (
            <span className="px-3 text-center text-faint">Sem prévia neste formato: a ampliação funciona igual (o ffmpeg lê).</span>
          ) : (
            <video src={arq.url} muted loop autoPlay playsInline onError={() => setSemPrevia(true)}
              // codec que o Chromium não decodifica (MPEG-4 part 2, HEVC): abre, toca o áudio e não dá erro, só não tem imagem
              onLoadedMetadata={(e) => !e.currentTarget.videoWidth && setSemPrevia(true)}
              className="size-full bg-black object-contain"
            />
          )
        ) : (
          <span className="flex flex-col items-center gap-1.5 px-3 text-center">
            {props.imagem ? <Image className="size-5" /> : <Film className="size-5" />}
            Escolha ou solte {props.imagem ? "uma imagem" : "um vídeo"} do PC
            <span className="text-faint">{props.imagem ? "png, jpg, webp" : "mp4, mov, mkv, webm…"}</span>
          </span>
        )}
      </button>
      {arq && (
        // fora do botão da prévia (botão dentro de botão não vale), sempre visível: é o jeito de desistir
        <button
          onClick={remover}
          title={props.imagem ? "Remover esta imagem" : "Remover este vídeo"}
          aria-label={props.imagem ? "Remover a imagem anexada" : "Remover o vídeo anexado"}
          className="absolute right-1.5 top-1.5 grid size-6 place-items-center rounded-full border border-white/15 bg-[#161616]/85 text-fg shadow backdrop-blur-sm hover:bg-[#2a2a2a] focus-visible:ring-2 focus-visible:ring-sky-400/60"
        >
          <X className="size-3.5" />
        </button>
      )}
      </div>
      <div className="min-w-56 flex-1">
        {!arq && props.imagem && (
          <p className="text-xs leading-relaxed text-muted">
            Amplia a resolução de qualquer imagem: ESRGAN (segundos), SeedVR2 (difusão, minutos, mais detalhe) ou Lanczos, sem IA. O original fica como está e o resultado entra
            aqui no feed.
          </p>
        )}
        {!arq && !props.imagem && (
          <p className="text-xs leading-relaxed text-muted">
            Amplia a resolução de qualquer vídeo (ESRGAN quadro a quadro, ou Lanczos) e, se quiser, dobra os quadros. O áudio é
            mantido; o original fica como está e o resultado entra aqui no feed.
          </p>
        )}
        {arq && (
          <p className="mb-2 truncate text-xs text-fg" title={arq.path}>
            {arq.nome}
            {lendo && <span className="ml-2 text-faint">lendo o vídeo…</span>}
            {info && props.imagem && <span className="ml-2 text-faint">{info.w}×{info.h}</span>}
            {info && !props.imagem && (
              <span className="ml-2 text-faint">
                {info.w}×{info.h} · {fmtFps(info.fps)} fps · {info.quadros} quadros ·{" "}
                {(info.quadros / info.fps).toFixed(1).replace(".", ",")} s{info.audio ? " · com áudio" : ""}
              </span>
            )}
          </p>
        )}
        {arq && info && (
          <PainelAmpliar
            key={arq.path}
            w={info.w}
            h={info.h}
            fps={info.fps}
            quadros={info.quadros}
            imagem={props.imagem}
            onError={props.onError}
            enviar={async (c) => {
              const conv = await props.ensureConversation();
              await api.post(`/imagens/${conv}/ampliar-arquivo`, { path: arq.path, ...c });
              setArq(null);
              setInfo(null);
              props.onPronto(conv);
            }}
          />
        )}
      </div>
    </div>
  );
}

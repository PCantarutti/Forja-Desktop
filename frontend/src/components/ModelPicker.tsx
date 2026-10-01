import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Carregar, Check, Cube, Ejetar, Eye, Search } from "./icons";

export type CatalogEntry = { id: string; name: string; type: string; models: string[]; error: string };

/** Recorte do /api/local. Só o que este seletor usa; a versão web não tem essa rota e fica sem. */
type IaLocal = {
  models: { path: string; name: string; kind: string; ctx?: number; vision?: boolean }[];  // ctx: janela por requisição; vision: tem projetor (mmproj)
  server: { running: boolean; path?: string; alias?: string };
  image_busy: boolean;
};

const POP_W = 640;   // w-[40rem]
const POP_H = 352;   // h-[22rem]
const MARGEM = 8;

/** Onde o popover cabe. Posição FIXA calculada do botão, e não `absolute bottom-full`: o seletor mora
 *  no composer (embaixo), no cabeçalho (em cima) e dentro da doca do Maestro, que tem overflow — e em
 *  cada um desses lugares o popover relativo saía da tela ou era cortado pelo contêiner. */
function posicao(botao: HTMLElement | null) {
  if (!botao) return null;
  const r = botao.getBoundingClientRect();
  const w = Math.min(POP_W, window.innerWidth * 0.9);
  const cabeEmCima = r.top - MARGEM >= POP_H;
  const top = cabeEmCima ? r.top - MARGEM - POP_H : Math.min(r.bottom + MARGEM, window.innerHeight - POP_H - MARGEM);
  const left = Math.min(Math.max(r.right - w, MARGEM), window.innerWidth - w - MARGEM);
  return { top: Math.max(MARGEM, top), left, width: w };
}

const fmtK = (n: number) => (n >= 1024 ? `${Math.round(n / 1024)}k` : String(n));

/** Seletor de provedor + modelo (campo de mensagem, cabeçalho do Maestro, slots de Worker). */
export default function ModelPicker(props: {
  provider: string;
  model: string;
  onChange: (provider: string, model: string) => void;
  refreshKey?: number; // muda quando as configurações de provedores mudam
  // Cair sozinho no primeiro modelo quando o salvo sumiu. Certo no chat (sempre precisa de um
  // modelo); errado num slot opcional, onde "vazio" é uma escolha e trocar por conta própria grava
  // configuração que ninguém pediu.
  autoFallback?: boolean;
  // Carregar o GGUF ao escolher. Certo onde o modelo é usado já (chat, Maestro); errado num slot de
  // Worker, que só diz QUAL modelo usar quando houver tarefa — carregar ali trocaria o modelo da
  // própria Maestro, se ela roda local.
  loadLocal?: boolean;
  // Clicar no GGUF só escolhe; quem põe na VRAM é o envio (agent._garante_modelo) ou o botão Carregar.
  // Só onde o backend carrega sozinho na mensagem: nas outras telas escolher continua carregando.
  cargaNoEnvio?: boolean;
  // Janela mínima (tokens por requisição) para um GGUF local ser escolhível. Abaixo disso o servidor
  // recusa o prompt no meio do trabalho, então o modelo aparece desabilitado com o motivo.
  minCtx?: number;
}) {
  const carrega = props.loadLocal ?? true;
  const botao = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(null);
  const [local, setLocal] = useState<IaLocal | null>(null);
  const [carregando, setCarregando] = useState("");   // caminho do .gguf subindo agora
  const [erro, setErro] = useState("");
  const [active, setActive] = useState(props.provider);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);

  const load = () => {
    // Os .gguf baixados não passam pelo /catalog: sem modelo carregado o llama-server nem está no ar.
    const loc = api.get<IaLocal>("/local").then((l) => (setLocal(l), l)).catch(() => (setLocal(null), null));
    return api
      .get<CatalogEntry[]>("/catalog")
      .then(async (c) => {
        setCatalog(c);
        // Modelo salvo sumiu (provedor removido ou modelo desmarcado): cai no primeiro disponível.
        // GGUF escolhido e ainda não carregado não está no /catalog (só o do ar está), mas existe na pasta.
        const current = c.find((p) => p.id === props.provider);
        const naPasta = current?.type === "llamacpp" && !!(await loc)?.models.some((m) => m.name === props.model);
        if ((props.autoFallback ?? true) && !current?.models.includes(props.model) && !naPasta) {
          const first = c.find((p) => p.models.length);
          if (first) props.onChange(first.id, first.models[0]);
        }
      })
      .catch(() => setCatalog([]));
  };

  useEffect(() => {
    load();
  }, [props.refreshKey]);

  useEffect(() => {
    if (!open) return;
    setActive(props.provider);
    setQ("");
    setErro("");
    load();
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const reposiciona = () => setPos(posicao(botao.current));
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    window.addEventListener("resize", reposiciona);
    window.addEventListener("scroll", reposiciona, true);  // true: rolagem de qualquer contêiner
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("resize", reposiciona);
      window.removeEventListener("scroll", reposiciona, true);
    };
  }, [open]);

  const prov = catalog?.find((p) => p.id === active);
  const providerName = catalog?.find((p) => p.id === props.provider)?.name ?? props.provider;
  const models = (prov?.models ?? []).filter((m) => m.toLowerCase().includes(q.toLowerCase()));

  // No provedor da IA local a lista sai dos arquivos baixados, não do /v1/models: assim ela existe
  // mesmo com nada carregado, que é justamente quando a pessoa precisa carregar alguma coisa.
  const ggufs = (local?.models ?? []).filter((m) => m.kind === "chat");
  const localAtivo = prov?.type === "llamacpp" && !!local;
  // O do ar e o escolhido vão para o topo: são os que a pessoa procura. O escolhido junto para a lista não
  // se reorganizar debaixo do cursor ao descarregar (o Carregar de outro modelo cairia sob o mouse).
  const peso = (m: { path: string; name: string }) =>
    2 * Number(m.path === local?.server.path) + Number(active === props.provider && m.name === props.model);
  const daPasta = ggufs.filter((m) => m.name.toLowerCase().includes(q.toLowerCase())).sort((a, b) => peso(b) - peso(a));

  const contar = (p: CatalogEntry) =>
    p.type === "llamacpp" && local ? ggufs.length : p.error ? "off" : p.models.length;

  async function carregar(caminho: string, fechar = false) {
    setCarregando(caminho);
    setErro("");
    try {
      const s = await api.post<{ alias?: string }>("/local/load", { path: caminho, params: {} });
      await load();
      // Carregou, então é este que vai responder: escolher outro faria a próxima mensagem trocar de volta.
      if (s.alias) props.onChange(active, s.alias);
      if (fechar) setOpen(false);
    } catch (e: any) {
      setErro(e.message);
      load();   // a falha fica registrada no painel IA local; aqui só atualizamos o estado
    } finally {
      setCarregando("");
    }
  }

  async function descarregar() {
    setErro("");
    try {
      await api.post("/local/unload", {});
    } catch (e: any) {
      setErro(e.message);
    }
    load();
  }

  const linha = (marcado: boolean, extra = "") =>
    `group flex items-center gap-1 rounded-[9px] pr-1 transition-colors duration-150 ${
      marcado ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"
    } ${extra}`;

  return (
    <div ref={box} className="relative ml-auto min-w-0">
      <button
        ref={botao}
        onClick={() => {
          if (!open) setPos(posicao(botao.current));
          setOpen(!open);
        }}
        title={`Trocar provedor e modelo — ${providerName} · ${props.model}`}
        aria-expanded={open}
        className="flex max-w-[min(14rem,100%)] items-center gap-1.5 overflow-hidden rounded-lg bg-raised px-2.5 py-1 text-xs whitespace-nowrap text-muted hover:text-fg"
      >
        <Cube className="size-3.5 shrink-0" />
        <span className="hidden max-w-24 shrink truncate text-faint sm:inline-block">{providerName} ·</span>
        <span className="min-w-0 flex-1 truncate text-left">{props.model || "escolher modelo"}</span>
      </button>

      {open && (
        <div
          style={pos ?? undefined}
          className={`${pos ? "fixed" : "absolute right-0 bottom-full mb-2"} z-50 flex h-[22rem] w-[40rem] max-w-[90vw] overflow-hidden rounded-[14px] border border-line-strong bg-surface shadow-popover`}
        >
          <ul className="w-44 shrink-0 space-y-0.5 overflow-y-auto border-r border-line p-1.5">
            {catalog === null && <li className="px-2.5 py-2 text-[12.5px] text-faint">carregando…</li>}
            {catalog?.map((p) => {
              const off = !!p.error && !(p.type === "llamacpp" && local);
              return (
                <li key={p.id}>
                  <button
                    onClick={() => setActive(p.id)}
                    className={`flex w-full items-center gap-2 rounded-[9px] px-2.5 py-2 text-left text-[13px] transition-colors duration-150 ${
                      p.id === active ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"
                    }`}
                  >
                    <span className="min-w-0 flex-1 truncate">{p.name}</span>
                    {p.type === "llamacpp" && local?.server.running && (
                      <span className="size-1.5 shrink-0 rounded-full bg-ok" title="Tem modelo carregado" />
                    )}
                    <span className={`shrink-0 font-mono text-[10.5px] tabular-nums ${off ? "text-err" : "text-faint"}`}>
                      {contar(p)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="flex min-w-0 flex-1 flex-col">
            <label className="flex items-center gap-2 border-b border-line px-3.5 py-2.5 text-faint focus-within:text-muted">
              <Search className="size-3.5 shrink-0" />
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Buscar modelo"
                className="w-full bg-transparent text-[13px] text-fg placeholder:text-faint focus:outline-none"
              />
            </label>
            <ul className="flex-1 space-y-0.5 overflow-y-auto p-1.5">
              {erro && <li className="px-2.5 py-2 text-[11.5px] leading-snug text-err">{erro}</li>}
              {prov?.error && !localAtivo && <li className="px-2.5 py-2 text-[11.5px] leading-snug text-err">{prov.error}</li>}

              {localAtivo && daPasta.map((m) => {
                const carregado = local.server.path === m.path;
                const subindo = carregando === m.path;
                const escolhido = active === props.provider && m.name === props.model;
                const curta = !!props.minCtx && !!m.ctx && m.ctx < props.minCtx;
                // Sem carga no envio, escolher É carregar: o marcado é o do ar. Com ela, o marcado é a escolha.
                const marcado = carrega && !props.cargaNoEnvio ? carregado : escolhido;
                return (
                  <li key={m.path} className={linha(marcado, carregado ? "ring-1 ring-ok/30 ring-inset" : "")}>
                    <button
                      disabled={curta || (!!carregando && !props.cargaNoEnvio)}
                      title={
                        curta
                          ? `Janela de ${m.ctx} tokens por requisição; o mínimo aqui é ${props.minCtx}. Aumente o contexto dele no painel IA local.`
                          : m.path
                      }
                      onClick={() => {
                        if (carrega && !props.cargaNoEnvio && !carregado) return void carregar(m.path, true);
                        props.onChange(active, carregado ? local.server.alias || m.name : m.name);
                        setOpen(false);
                      }}
                      className="flex min-w-0 flex-1 items-center gap-2.5 py-2 pl-2.5 text-left disabled:opacity-40"
                    >
                      <span
                        className={`size-1.5 shrink-0 rounded-full ${
                          carregado ? "bg-ok" : subindo ? "animate-pulse bg-info" : "bg-transparent"
                        }`}
                        aria-hidden
                      />
                      <span className={`min-w-0 flex-1 truncate font-mono text-[13px] ${carregado ? "text-fg" : ""}`}>{m.name}</span>
                      {carregado && (
                        <span className="shrink-0 font-mono text-[10.5px] tracking-[.06em] text-ok uppercase">na VRAM</span>
                      )}
                      {subindo && <span className="shrink-0 text-[11.5px] text-info">carregando…</span>}
                      {m.vision && (
                        <span title="Visão: o projetor (mmproj) da pasta dele sobe junto" className="shrink-0 text-faint group-hover:text-muted">
                          <Eye className="size-3.5" />
                        </span>
                      )}
                      <span
                        title={curta ? `Mínimo aqui: ${fmtK(props.minCtx!)}` : "Janela por requisição"}
                        className={`w-9 shrink-0 text-right font-mono text-[11px] tabular-nums ${curta ? "text-warn" : "text-faint"}`}
                      >
                        {m.ctx ? fmtK(m.ctx) : ""}
                      </span>
                    </button>
                    {carrega && (
                      <button
                        onClick={() => (carregado ? void descarregar() : void carregar(m.path))}
                        disabled={subindo || (!carregado && (curta || !!carregando || local.image_busy))}
                        title={carregado ? "Descarregar: libera a VRAM" : subindo ? "Carregando…" : "Carregar na VRAM agora"}
                        aria-label={carregado ? `Descarregar ${m.name}` : `Carregar ${m.name}`}
                        className={`grid size-7 shrink-0 place-items-center rounded-[7px] transition-colors duration-150 hover:bg-surface hover:text-fg focus-visible:outline-1 focus-visible:outline-focus disabled:pointer-events-none disabled:opacity-40 ${
                          carregado ? "text-ok" : "text-faint"
                        }`}
                      >
                        {subindo ? (
                          <span className="size-3 animate-spin rounded-full border-2 border-info/30 border-t-info" aria-hidden />
                        ) : carregado ? (
                          <Ejetar className="size-[15px]" />
                        ) : (
                          <Carregar className="size-[15px]" />
                        )}
                      </button>
                    )}
                    <span className="grid w-5 shrink-0 place-items-center">
                      {marcado && <Check className="size-3.5 text-accent" />}
                    </span>
                  </li>
                );
              })}
              {localAtivo && !daPasta.length && (
                <li className="px-2.5 py-2 text-[11.5px] leading-snug text-faint">
                  Nenhum .gguf{q ? " com esse nome" : ""} nas pastas de modelos. Baixe um no painel IA local.
                </li>
              )}

              {!localAtivo && models.map((m) => {
                const selected = active === props.provider && m === props.model;
                return (
                  <li key={m} className={linha(selected)}>
                    <button
                      onClick={() => {
                        props.onChange(active, m);
                        setOpen(false);
                      }}
                      className="flex min-w-0 flex-1 items-center py-2 pl-2.5 text-left"
                    >
                      <span className="min-w-0 flex-1 truncate font-mono text-[13px]">{m}</span>
                    </button>
                    <span className="grid w-5 shrink-0 place-items-center">
                      {selected && <Check className="size-3.5 text-accent" />}
                    </span>
                  </li>
                );
              })}
              {prov && !localAtivo && !prov.error && !models.length && (
                <li className="px-2.5 py-2 text-[11.5px] leading-snug text-faint">
                  Nenhum modelo{q ? " com esse nome" : ""}. Escolha quais aparecem em Configurações › Provedores.
                </li>
              )}
            </ul>
            {localAtivo && (local.image_busy || (carrega && props.cargaNoEnvio)) && (
              <p className={`border-t border-line px-3.5 py-2 text-[11.5px] leading-snug ${local.image_busy ? "text-warn" : "text-faint"}`}>
                {local.image_busy
                  ? "Uma imagem está sendo gerada; os dois disputam a mesma VRAM."
                  : "Escolher não carrega: o modelo sobe na VRAM no próximo envio."}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

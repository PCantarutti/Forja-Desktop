import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Modal } from "./Modal";
import { Download } from "./icons";

/** O que a ponte do Electron devolve. Tipado aqui de propósito: o `forja.d.ts` só existe no
 *  desktop, e assim este arquivo continua valendo nos dois repositórios. */
type Estado = { state: string; version: string; percent: number; error: string; notes: string };

type Ponte = {
  get: () => Promise<Estado>;
  check: () => Promise<Estado>;
  download: () => Promise<Estado>;
  install: () => Promise<Estado>;
};

const ponte = () => (window as { forja?: { update?: Ponte } }).forja?.update;

const AVISOS: Record<string, string> = {
  available: "Atualização disponível",
  downloading: "Baixando atualização",
  ready: "Atualização pronta para instalar",
};

/** Espelho do estado que vive no processo principal. Só existe poll porque é ele que avança. */
function useAtualizacao(ligado: boolean) {
  const [u, setU] = useState<Estado | null>(null);
  useEffect(() => {
    const p = ponte();
    if (!p || !ligado) return;
    const ler = () => p.get().then(setU).catch(() => {});
    ler();
    // ponytail: poll bobo. O handler do main só devolve um objeto que já está na memória dele.
    const t = setInterval(ler, 1000);
    return () => clearInterval(t);
  }, [ligado]);
  return [u, setU] as const;
}

/**
 * As notas vêm do latest.yml em Markdown (ver scripts/release.mjs). Se um dia vierem do feed do
 * GitHub, chegam em HTML: aí vale o texto, não as tags cruas na tela.
 */
function textoDasNotas(notas: string) {
  if (!/^\s*</.test(notas)) return notas;
  return new DOMParser().parseFromString(notas, "text/html").body.textContent ?? "";
}

const btn = "rounded-full border border-line px-4 py-1.5 text-sm text-fg hover:bg-raised";
const btnPrimary = "rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";

/**
 * Pergunta, e só. Levar para Configurações punia quem nunca esteve lá: a pessoa clica num aviso e
 * cai numa tela cheia de abas, tendo que descobrir qual delas responde o que ela acabou de ler.
 */
function ModalAtualizacao(props: { onClose: () => void }) {
  const [u, setU] = useAtualizacao(true);
  const p = ponte();
  if (!u || !p) return null;

  const notas = u.notes.trim();
  const acao = {
    available: { rotulo: "Baixar e instalar", faz: () => p.download().then(setU) },
    ready: { rotulo: "Reiniciar e instalar agora", faz: () => p.install() },
    error: { rotulo: "Tentar de novo", faz: () => p.check().then(setU) },
  }[u.state];

  return (
    <Modal
      onClose={props.onClose}
      label="Atualização do Forja"
      className="w-[min(34rem,92vw)] rounded-2xl border border-line bg-surface p-5 shadow-2xl"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 text-amber-300">
          <Download />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-base font-medium text-fg">
            {AVISOS[u.state] ?? "Atualização"}
            {u.version ? ` — versão ${u.version}` : ""}
          </div>

          {notas && (
            <div className="notas md mt-3 max-h-72 overflow-auto rounded-lg border border-line bg-bg px-4 py-3 text-sm">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{textoDasNotas(notas)}</ReactMarkdown>
            </div>
          )}

          {u.state === "downloading" && (
            <div className="mt-3">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised">
                <div className="h-full bg-amber-400 transition-all" style={{ width: `${u.percent}%` }} />
              </div>
              <div className="mt-1.5 text-xs text-muted">{u.percent}% — pode continuar usando o Forja.</div>
            </div>
          )}

          {u.state === "available" && (
            <div className="mt-3 text-xs text-muted">
              O download roda em segundo plano. A instalação só acontece quando você mandar, e reinicia o app.
            </div>
          )}
          {u.state === "ready" && (
            <div className="mt-3 text-xs text-muted">O Forja fecha, instala e abre de novo. O que estiver em andamento para.</div>
          )}
          {u.error && <div className="mt-3 text-xs text-red-300">{u.error}</div>}

          <div className="mt-4 flex flex-wrap justify-end gap-2">
            <button className={btn} onClick={props.onClose}>
              {u.state === "ready" ? "Instalar depois" : "Agora não"}
            </button>
            {acao && (
              <button className={btnPrimary} onClick={() => void acao.faz()}>
                {acao.rotulo}
              </button>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}

/**
 * Fica no rodapé enquanto houver versão nova — e só some quando o app reabrir já atualizado, que é
 * quando o estado vira "current". Não há como dispensar: é o ponto do aviso.
 */
export function AvisoAtualizacao() {
  const [aberto, setAberto] = useState(false);
  const [u] = useAtualizacao(!aberto); // com o modal aberto quem faz o poll é ele
  const aviso = u && AVISOS[u.state];
  if (!aviso) return null;

  return (
    <>
      <button
        onClick={() => setAberto(true)}
        className="mx-2 mt-2 flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-left text-sm text-amber-200 hover:bg-amber-500/20"
      >
        <Download />
        <span className="truncate">
          {aviso}
          {u.state === "downloading" ? ` ${u.percent}%` : u.version ? ` ${u.version}` : ""}
        </span>
      </button>
      {aberto && <ModalAtualizacao onClose={() => setAberto(false)} />}
    </>
  );
}

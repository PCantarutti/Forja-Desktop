import { useState } from "react";
import { api } from "../api";
import { ChevronDown, Code } from "./icons";
import { DiffView, Thinking, ToolDraft } from "./MessageView";

// A atividade de uma resposta do Design no feitio do grupo do Agente: o raciocínio, cada passo como
// uma ferramenta usada (escrever_secao · hero ● ok, que abre com o código) e, ao vivo, o código da
// chamada em curso saindo linha a linha. O diff do documento inteiro é buscado só quando você abre.

type Passo = { nome: string; alvo: string; erro: boolean; detalhe: string };

/** Os passos vêm como frases (o que o chat mostrava antes); aqui viram "ferramentas". */
function comoFerramenta(p: string): Passo {
  const aspas = p.match(/“([^”]+)”/)?.[1] ?? "";
  const erro = /^Não conseguiu|não conseguiu/.test(p);
  const nome = /^Montou o esqueleto/.test(p) ? "montar_esqueleto"
    : /^Revis(ão|ou) visual|^Revisou o visual|página pronta/.test(p) ? "revisar_visual"
      : /seção|slide|tela/.test(p) && aspas ? "escrever_secao"
        : /^--[\w-]+:/.test(p) ? "ajustar_token"
          : /imagem|slot|foto/i.test(p) ? "imagens"
            : /elemento|fragmento/i.test(p) ? "editar_elementos"
              : /documento/i.test(p) ? "escrever_documento" : "passo";
  return { nome, alvo: aspas || (nome === "passo" || nome === "ajustar_token" ? p : ""), erro, detalhe: p };
}

/** A seção pronta no documento ao vivo (o canvas já tem): é o "resultado" da ferramenta. */
function codigoDaSecao(doc: string | undefined, nome: string): string {
  if (!doc || !nome) return "";
  const el = new DOMParser().parseFromString(doc, "text/html").querySelector(`[data-section="${CSS.escape(nome)}"]`);
  return el ? el.outerHTML.replace(/ data-fid="\w+"/g, "") : "";
}

function Ferramenta({ p, codigo }: { p: Passo; codigo: string }) {
  const [aberto, setAberto] = useState(false);
  return (
    <div className="my-1.5 overflow-hidden rounded-xl border border-line bg-surface">
      <button onClick={() => setAberto((v) => !v)} disabled={!codigo && p.detalhe === p.alvo}
              className="flex w-full items-center gap-2 px-3.5 py-2 text-left text-[13px] hover:bg-raised/50 disabled:cursor-default">
        <span className="font-mono text-[12.5px] text-fg">{p.nome}</span>
        <span className="min-w-0 truncate text-faint">{p.alvo}</span>
        <span className={`ml-auto shrink-0 text-[11.5px] ${p.erro ? "text-err" : "text-ok"}`}>● {p.erro ? "erro" : "ok"}</span>
        {(codigo || p.detalhe !== p.alvo) && <ChevronDown className={`size-3.5 shrink-0 text-faint transition-transform ${aberto ? "" : "-rotate-90"}`} />}
      </button>
      {aberto && (
        <div className="border-t border-line">
          {p.detalhe !== p.alvo && <p className="px-3.5 py-2 text-[12.5px] text-muted">{p.detalhe}</p>}
          {codigo && (
            <pre className="max-h-72 overflow-auto bg-code px-3.5 py-2 font-mono text-[11px] whitespace-pre-wrap text-muted">{codigo}</pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function DesignAtividade(props: {
  messageId?: number;          // sem id: resposta ao vivo (ainda sem diff)
  raciocinio: string;
  passos: string[];
  mais?: number;
  menos?: number;
  ao_vivo?: boolean;
  rotulo?: string;
  parcial?: string;            // ao vivo: o que o modelo está escrevendo agora
  atual?: { nome: string; alvo: string } | null;   // ao vivo: a ferramenta em curso
  doc?: string;                // ao vivo: o documento (as seções prontas viram o resultado de cada passo)
}) {
  const [aberto, setAberto] = useState<boolean | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [vendoCodigo, setVendoCodigo] = useState(false);
  const temCodigo = !!(props.mais || props.menos);
  const passos = props.passos.map(comoFerramenta);
  const n = passos.length + (props.atual ? 1 : 0);
  if (!props.raciocinio && !n && !temCodigo && !props.ao_vivo) return null;
  const isOpen = aberto ?? !!props.ao_vivo;
  const falhas = passos.filter((p) => p.erro).length;
  const cabeca = props.ao_vivo
    ? (props.atual ? `${props.rotulo ?? "Trabalhando"} — ${props.atual.nome}${props.atual.alvo ? ` · ${props.atual.alvo}` : ""}` : props.rotulo ?? "Trabalhando")
    : [props.raciocinio ? "Raciocinou" : "", n ? `usou ${n} ${n === 1 ? "ferramenta" : "ferramentas"}` : "",
       temCodigo ? `+${props.mais} −${props.menos} linhas` : ""].filter(Boolean).join(", ");

  async function verCodigo() {
    setVendoCodigo((v) => !v);
    if (diff !== null || !props.messageId) return;
    try {
      setDiff((await api.get<{ diff: { texto: string } }>(`/design/atividade/${props.messageId}`)).diff.texto);
    } catch {
      setDiff("");
    }
  }

  return (
    <div className="my-2">
      <button onClick={() => setAberto(!isOpen)} className="flex items-center gap-1.5 text-[13.5px] text-faint transition-colors hover:text-muted">
        <span className={props.ao_vivo ? "animate-pulse" : ""}>{cabeca}{props.ao_vivo ? "…" : ""}</span>
        {!!falhas && <span className="text-err">({falhas} falha{falhas > 1 ? "s" : ""})</span>}
        <ChevronDown className={`size-3.5 transition-transform duration-150 ${isOpen ? "" : "-rotate-90"}`} />
      </button>
      {isOpen && (
        <div className="mt-2 border-l border-line pl-3.5">
          {props.raciocinio && <Thinking text={props.raciocinio} live={props.ao_vivo && !props.parcial} />}
          {passos.map((p, i) => (
            <Ferramenta key={i} p={p} codigo={p.nome === "escrever_secao" ? codigoDaSecao(props.doc, p.alvo) : ""} />
          ))}
          {props.ao_vivo && props.atual && props.parcial && (
            <ToolDraft tool={{ name: props.atual.nome, path: props.atual.alvo, text: props.parcial }} />
          )}
          {temCodigo && props.messageId && (
            <div className="mt-1.5">
              <button onClick={verCodigo} className="inline-flex items-center gap-1.5 text-[12.5px] text-muted hover:text-fg">
                <Code className="size-3.5" /> {vendoCodigo ? "Esconder o código alterado" : "Ver o código alterado"}
                <span className="font-mono text-[11px]"><span className="text-emerald-300/80">+{props.mais}</span> <span className="text-red-300/80">−{props.menos}</span></span>
              </button>
              {vendoCodigo && (
                <div className="mt-2">
                  {diff === null ? <p className="text-xs text-faint">Carregando…</p>
                    : diff ? <DiffView preview={{ kind: "diff", path: "index.html", text: diff } as any} />
                      : <p className="text-xs text-faint">Sem diff guardado para esta versão.</p>}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

import { useState } from "react";
import { api } from "../api";
import { Check, ChevronDown, Code, X } from "./icons";
import { DiffView, Thinking } from "./MessageView";

// A atividade de uma resposta do Design, no feitio do grupo colapsável do Agente: o raciocínio, os
// passos que ela deu (plano, cada seção escrita, cada elemento alterado, tokens, CSS) e o código que
// mudou — o diff do documento, buscado só quando você abre (são dezenas de KB por versão).

export default function DesignAtividade(props: {
  messageId?: number;          // sem id: resposta ao vivo (ainda sem diff)
  raciocinio: string;
  passos: string[];
  mais?: number;
  menos?: number;
  ao_vivo?: boolean;
  passosVivos?: { nome: string; status: string }[];   // etapas: seção por seção enquanto gera
  rotulo?: string;
}) {
  const [aberto, setAberto] = useState<boolean | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [vendoCodigo, setVendoCodigo] = useState(false);
  const temCodigo = !!(props.mais || props.menos);
  const n = props.passos.length + (props.passosVivos?.length ?? 0);
  if (!props.raciocinio && !n && !temCodigo) return null;
  const isOpen = aberto ?? !!props.ao_vivo;
  const resumo = props.ao_vivo
    ? props.rotulo ?? "Trabalhando…"
    : [props.raciocinio ? "Pensou" : "", n ? `${n} ${n === 1 ? "passo" : "passos"}` : "",
       temCodigo ? `+${props.mais} −${props.menos} linhas` : ""].filter(Boolean).join(" · ");

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
        <span className={props.ao_vivo ? "animate-pulse" : ""}>{resumo}</span>
        <ChevronDown className={`size-3.5 transition-transform duration-150 ${isOpen ? "" : "-rotate-90"}`} />
      </button>
      {isOpen && (
        <div className="mt-2 border-l border-line pl-3.5">
          {props.raciocinio && <Thinking text={props.raciocinio} live={props.ao_vivo && !props.passosVivos?.some((p) => p.status === "ok")} />}
          {(!!props.passos.length || !!props.passosVivos?.length) && (
            <ol className="my-1.5 space-y-1 text-[13px]">
              {props.passos.map((p, i) => (
                <li key={i} className={`flex gap-2 ${/^Não conseguiu/.test(p) ? "text-red-300" : "text-muted"}`}>
                  {/^Não conseguiu/.test(p) ? <X className="mt-0.5 size-3.5 shrink-0" /> : <Check className="mt-0.5 size-3.5 shrink-0 text-emerald-300/80" />}
                  <span>{p}</span>
                </li>
              ))}
              {props.passosVivos?.map((s) => (
                <li key={s.nome} className={`flex gap-2 ${s.status === "gerando" ? "text-accent-text" : s.status === "erro" ? "text-red-300" : s.status === "ok" ? "text-muted" : "text-faint"}`}>
                  <span className="inline-block w-3.5 shrink-0 text-center">{s.status === "ok" ? "✓" : s.status === "gerando" ? "›" : s.status === "erro" ? "×" : "·"}</span>
                  <span>{s.nome}{s.status === "gerando" && <span className="animate-pulse"> — escrevendo…</span>}</span>
                </li>
              ))}
            </ol>
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

import { useEffect, useState } from "react";
import { api } from "../api";
import type { CloudUsage } from "../types";

const TTL = 60_000;
let cache: { at: number; dados: CloudUsage[] } | null = null;
let voando: Promise<CloudUsage[]> | null = null;

const JANELAS: Record<string, string> = {
  session: "Sessão (5 h)",
  daily: "Diário",
  weekly: "Semanal",
  monthly: "Mensal",
};

/**
 * Cota do Ollama Cloud, compartilhada por quem mostra (Provedores, delegação, anel de contexto):
 * uma consulta por minuto para todos, não uma por tela. `ativo=false` não busca nada — é o que
 * mantém o anel de contexto calado enquanto o popover está fechado.
 */
export function useCloudUsage(ativo = true): CloudUsage[] {
  const [dados, setDados] = useState<CloudUsage[]>(cache?.dados ?? []);
  useEffect(() => {
    if (!ativo) return;
    let vivo = true;
    const buscar = () => {
      if (cache && Date.now() - cache.at < TTL) return Promise.resolve(cache.dados);
      voando ??= api
        .get<{ providers: CloudUsage[] }>("/cloud-usage")
        .then((r) => {
          cache = { at: Date.now(), dados: r.providers };
          return r.providers;
        })
        .catch(() => [] as CloudUsage[])
        .finally(() => {
          voando = null;
        });
      return voando;
    };
    buscar().then((d) => vivo && setDados(d));
    return () => {
      vivo = false;
    };
  }, [ativo]);
  return dados;
}

/** Barras de cota de um provedor. `models` acrescenta as requisições por modelo do período. */
export function UsageBars(props: { data: CloudUsage; models?: boolean }) {
  return (
    <div className="space-y-1.5">
      {props.data.limits.map((l) => {
        const pct = Math.min(100, Math.round(l.usage * 100));
        const cor = pct >= 90 ? "bg-rose-400" : pct >= 70 ? "bg-amber-400" : "bg-sky-400";
        return (
          <div key={l.name}>
            <div className="flex items-baseline justify-between gap-2 text-[11px]">
              <span className="truncate text-muted">{JANELAS[l.name] ?? l.name}</span>
              <span className="shrink-0 text-fg">{pct}%</span>
            </div>
            <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-raised">
              <div className={`h-full rounded-full ${cor}`} style={{ width: `${pct}%` }} />
            </div>
          </div>
        );
      })}
      {props.models && !!props.data.models.length && (
        <div className="pt-0.5 text-[11px] text-faint">
          {props.data.models
            .slice(0, 5)
            .map((m) => `${m.name} ${m.request_count}`)
            .join(" · ")}
        </div>
      )}
    </div>
  );
}

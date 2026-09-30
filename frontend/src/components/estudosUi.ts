// Peças da tela Estudos usadas pelo resumo (EstudosView) e pela prova (EstudosProva).
// Classes repetidas da Pesquisa de propósito: a aba viaja inteira num cherry-pick para o forja-web.

export const card = "rounded-xl border border-line bg-surface p-3.5";
export const btn = "inline-flex items-center gap-1.5 rounded-[9px] border border-line-strong px-3 py-1.5 text-fg hover:border-focus hover:bg-raised disabled:opacity-40";
export const btnPrimary = "inline-flex items-center gap-1.5 rounded-[9px] border border-accent bg-accent px-3 py-1.5 font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";
export const rotulo = "font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase";

export const MOTOR_CLAUDE = "claude-mcp";   // o provider que o backend entende como "o Claude faz via MCP"
export const PEDIDO_CLAUDE = "Atenda os pedidos da tela Estudos do Forja.";

export type Par = { provider: string; model: string };
export type Modelos = { motor: "forja" | "claude"; escritor: Par; extrator: Par | null };   // extrator null = automático

/** O que vai no corpo de quem pede trabalho ao backend (resumo, prova, correção). */
export function motorDe(m: Modelos) {
  const claude = m.motor === "claude";
  return {
    provider: claude ? MOTOR_CLAUDE : m.escritor.provider, model: claude ? "" : m.escritor.model,
    ex_provider: claude ? "" : m.extrator?.provider ?? "", ex_model: claude ? "" : m.extrator?.model ?? "",
  };
}

export const relogio = (seg: number) => `${Math.floor(seg / 60)}:${String(Math.floor(seg % 60)).padStart(2, "0")}`;

type ComStats = { stats: { tokens: number; gerando: number; segundos: number; estimado: boolean } };

/** "1.234 tokens · 38 tok/s · 3:12". */
export function numeros(e: ComStats): string {
  const s = e.stats;
  const tps = s.gerando > 0.5 ? Math.round(s.tokens / s.gerando) : 0;
  return [s.tokens ? `${s.tokens.toLocaleString("pt-BR")} tokens${s.estimado ? " (estim.)" : ""}` : "",
          tps ? `${tps} tok/s` : "", s.segundos ? relogio(s.segundos) : ""].filter(Boolean).join(" · ");
}

export function lerLocal<T>(chave: string, padrao: T): T {
  try {
    const v = JSON.parse(localStorage.getItem(chave) ?? "null");
    return v ? (padrao ? { ...padrao, ...v } : v) : padrao;
  } catch {
    return padrao;
  }
}

export function gravarLocal(chave: string, valor: unknown) {
  try {
    if (valor === null) localStorage.removeItem(chave);
    else localStorage.setItem(chave, JSON.stringify(valor));
  } catch {
    /* sem storage (janela privada): segue sem lembrar */
  }
}

/** Nota com vírgula, como se escreve no Brasil: 7,5. */
export const nota = (n: number) => n.toLocaleString("pt-BR", { maximumFractionDigits: 1 });

// Peças da tela Estudos usadas pelo resumo (EstudosView) e pela prova (EstudosProva).
// Classes repetidas da Pesquisa de propósito: a aba viaja inteira num cherry-pick para o forja-web.

export const card = "rounded-xl border border-line bg-surface p-3.5";
export const btn = "inline-flex items-center gap-1.5 rounded-[9px] border border-line-strong px-3 py-1.5 text-fg hover:border-focus hover:bg-raised disabled:opacity-40";
export const btnPrimary = "inline-flex items-center gap-1.5 rounded-[9px] border border-accent bg-accent px-3 py-1.5 font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";
export const rotulo = "font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase";

/** A cor de cada matéria pelo acerto: ok ≥ 70%, warn 50–69%, err abaixo; faint sem entrega. */
export const corAcerto = (a: number | null) =>
  a == null ? "bg-faint/50" : a >= 70 ? "bg-ok" : a >= 50 ? "bg-warn" : "bg-err";
/** A mesma cor a 20%, fundo dos segmentos da faixa de peso. */
export const corAcertoFundo = (a: number | null) =>
  a == null ? "bg-faint/20" : a >= 70 ? "bg-ok/20" : a >= 50 ? "bg-warn/20" : "bg-err/20";

/** Acerto do objetivo inteiro: as entregas de todas as matérias, pesadas pelo número de entregas. */
export function acertoGeral(ms: { acerto: number | null; entregas: number }[]): number | null {
  const com = ms.filter((m) => m.acerto != null && m.entregas);
  const n = com.reduce((s, m) => s + m.entregas, 0);
  return n ? Math.round(com.reduce((s, m) => s + (m.acerto ?? 0) * m.entregas, 0) / n) : null;
}

/** Dias até a data da prova (AAAA-MM-DD), arredondando para cima; null sem data. */
export const diasAte = (data?: string | null) =>
  data ? Math.ceil((new Date(`${data}T00:00:00`).getTime() - Date.now()) / 86_400_000) : null;

// Barra de abas sublinhadas do topo
export const aba = "flex shrink-0 items-center gap-[7px] whitespace-nowrap border-b-2 pt-3.5 pb-3 -mb-px";
export const abaLigada = `${aba} border-accent font-medium text-fg`;
export const abaDesligada = `${aba} border-transparent text-muted hover:text-fg`;
/** Selo de contagem depois do nome da aba (só quando > 0). */
export const selo = "rounded-[5px] bg-raised px-1.5 py-px font-mono text-[10.5px] text-muted";
export const seloDestaque = "rounded-[5px] bg-accent-soft px-1.5 py-px font-mono text-[10.5px] text-accent-text";
/** Trilho de barra de acerto/progresso (3px na coluna, 6px na tabela). */
export const trilho = "overflow-hidden rounded-full bg-raised";
/** Pílula de tópico fraco. */
export const pilulaFraco = "rounded-full border border-amber-300/40 px-[9px] py-0.5 text-[11.5px] text-amber-300";
/** Botão leve das barras de ferramentas. */
export const btnLeve = "inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-[12px] text-fg-2 hover:border-focus hover:bg-raised disabled:opacity-40";

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

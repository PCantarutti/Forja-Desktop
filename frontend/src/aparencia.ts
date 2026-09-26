// Tema, cor de destaque e fonte da interface. Aplicam na hora (atributos e variáveis no <html>).
// ponytail: guardado no localStorage; mover para o /settings do backend quando ele aceitar esses campos.

export type Tema = "forja" | "brasa" | "aco" | "musgo" | "violeta" | "carmim";
export type Fonte = "atkinson" | "plex" | "sistema";
export type Aparencia = { tema: Tema; destaque: string | null; fonte: Fonte; iniciais: string };

export const TEMAS: { id: Tema; label: string; bg: string; accent: string }[] = [
  { id: "forja", label: "Forja atual", bg: "#171717", accent: "#4f8ff7" },
  { id: "brasa", label: "Brasa", bg: "#151311", accent: "#f2a14a" },
  { id: "aco", label: "Aço", bg: "#13161b", accent: "#4aa3e8" },
  { id: "musgo", label: "Musgo", bg: "#121513", accent: "#3fc3a3" },
  { id: "violeta", label: "Violeta", bg: "#15131a", accent: "#a98bf5" },
  { id: "carmim", label: "Carmim", bg: "#161314", accent: "#e8546a" },
];

export const FONTES: { id: Fonte; label: string; hint: string }[] = [
  { id: "atkinson", label: "Atkinson + JetBrains", hint: "Padrão: legível em tamanho pequeno." },
  { id: "plex", label: "IBM Plex", hint: "Sans e Mono da mesma família." },
  { id: "sistema", label: "Sistema", hint: "Segoe UI e Cascadia, as do Windows." },
];

const CHAVE = "forja.aparencia";
const PADRAO: Aparencia = { tema: "forja", destaque: null, fonte: "atkinson", iniciais: "EU" };

export function lerAparencia(): Aparencia {
  try {
    return { ...PADRAO, ...JSON.parse(localStorage.getItem(CHAVE) ?? "{}") };
  } catch {
    return PADRAO;
  }
}

/** Texto sobre o destaque: preto quando a cor é clara (luminância > .55), branco senão. */
export function textoSobre(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255].map((c) => c / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 ? "#0e0e0e" : "#ffffff";
}

export function aplicarAparencia(a: Aparencia) {
  const html = document.documentElement;
  if (a.tema === "forja") delete html.dataset.theme;
  else html.dataset.theme = a.tema;
  if (a.fonte === "atkinson") delete html.dataset.font;
  else html.dataset.font = a.fonte;
  if (a.destaque) {
    html.style.setProperty("--accent", a.destaque);
    html.style.setProperty("--accent-fg", textoSobre(a.destaque));
  } else {
    html.style.removeProperty("--accent");
    html.style.removeProperty("--accent-fg");
  }
}

export function salvarAparencia(a: Aparencia) {
  try {
    localStorage.setItem(CHAVE, JSON.stringify(a));
  } catch { /* sem storage: vale só nesta sessão */ }
  aplicarAparencia(a);
  window.dispatchEvent(new Event("forja-aparencia")); // o trilho redesenha as iniciais
}

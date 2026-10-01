// Texto do resumo da tela Estudos: o que o modelo escreve → o que o Markdown com KaTeX entende.

/** Modelo escreve fórmula de três jeitos; o remark-math só entende $...$ e $$...$$. E "R$ 10" não é fórmula. */
export function matematica(texto: string): string {
  return texto
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, f) => `$$${f}$$`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, f) => `$${f}$`)
    .replace(/R\$(?=\s?\d)/g, "R\\$");
}

/** As seções ("## ...") na ordem, para o sumário ao lado do resumo. */
export function sumario(texto: string): string[] {
  const out: string[] = [];
  let cerca = false;
  for (const linha of texto.split("\n")) {
    if (linha.startsWith("```")) cerca = !cerca;
    else if (!cerca && linha.startsWith("## ")) out.push(linha.slice(3).trim());
  }
  return out;
}

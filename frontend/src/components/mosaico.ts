/**
 * Mosaico: cada cartão vai para a coluna mais baixa até ali, pela proporção real (largura/altura).
 * `rodape` é a altura do rodapé do cartão em frações da largura da coluna (a legenda embaixo da imagem).
 * Devolve, por coluna, os índices dos itens na ordem original.
 */
export function distribuir(proporcoes: number[], colunas: number, rodape = 0.12): number[][] {
  const cols: number[][] = Array.from({ length: Math.max(1, colunas) }, () => []);
  const alturas = cols.map(() => 0);
  proporcoes.forEach((p, i) => {
    const c = alturas.indexOf(Math.min(...alturas));
    cols[c].push(i);
    alturas[c] += 1 / (p > 0 ? p : 1) + rodape;
  });
  return cols;
}

/** Quantas colunas cabem: cartão com pelo menos ~200 px, no máximo 4 (o design). */
export const colunasPara = (largura: number) => Math.max(2, Math.min(4, Math.floor(largura / 200)));

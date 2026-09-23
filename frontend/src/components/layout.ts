/**
 * Layout do cockpit da Maestro: onde fica cada bloco, larguras, faixa, recolhidos. Lógica pura, sem
 * React nem storage, para ser testada sozinha (layout.test.ts, `npm test`).
 */

/** Blocos do cockpit que o usuário reposiciona arrastando pelo cabeçalho. */
export type Bloco = "arvore" | "maestro" | "worker" | "doca";
export const BLOCOS: Bloco[] = ["arvore", "maestro", "worker", "doca"];

/** Colunas da esquerda para a direita (com a largura de cada uma em partes proporcionais) e, opcional,
 * uma faixa de largura inteira em cima ou embaixo delas, com a altura em % da área. Qualquer bloco
 * vai para qualquer lugar: a doca pode virar coluna e a Maestro pode virar faixa. */
export type Layout = {
  colunas: Bloco[]; larguras: number[]; faixa: Bloco | null; faixaEmCima: boolean; dock: number;
  recolhidos: Bloco[];  // viram uma barra fina (coluna: vertical; faixa: horizontal)
};
// Tarefas estreita de propósito: é uma lista de códigos e títulos curtos, e o espaço rende mais na
// coluna da Maestro, onde está o texto e o composer.
export const LAYOUT_PADRAO: Layout = {
  colunas: ["arvore", "maestro", "worker"], larguras: [14, 56, 30], faixa: "doca", faixaEmCima: false, dock: 36,
  recolhidos: [],
};

/** Onde o bloco arrastado cai: trocar de lugar com outro, entrar como coluna numa posição, ou virar a
 * faixa de cima/baixo. */
export type Alvo = { tipo: "trocar"; com: Bloco } | { tipo: "coluna"; pos: number } | { tipo: "faixa"; emCima: boolean };

/** Aplica o solto. Função pura: o mesmo layout entra, um novo sai (ou o mesmo, se não mudou nada). */
export function mover(l: Layout, id: Bloco, alvo: Alvo): Layout {
  const colunas = [...l.colunas], larguras = [...l.larguras];
  const i = colunas.indexOf(id);
  if (alvo.tipo === "trocar") {
    const j = colunas.indexOf(alvo.com);
    if (alvo.com === id) return l;
    if (i >= 0 && j >= 0) {
      [colunas[i], colunas[j]] = [colunas[j], colunas[i]];
      [larguras[i], larguras[j]] = [larguras[j], larguras[i]];
      return { ...l, colunas, larguras };
    }
    // um dos dois é a faixa: trocam de papel, a coluna fica com a largura de antes
    if (i >= 0) colunas[i] = alvo.com;
    else colunas[j] = id;
    return { ...l, colunas, faixa: i >= 0 ? id : alvo.com };
  }
  if (alvo.tipo === "faixa") {
    if (l.faixa === id) return l.faixaEmCima === alvo.emCima ? l : { ...l, faixaEmCima: alvo.emCima };
    if (l.faixa) {  // a faixa atual desce para a coluna que o bloco deixou
      colunas[i] = l.faixa;
      return { ...l, colunas, faixa: id, faixaEmCima: alvo.emCima };
    }
    if (colunas.length < 2) return l;  // sempre sobra ao menos uma coluna
    colunas.splice(i, 1);
    larguras.splice(i, 1);
    return { ...l, colunas, larguras, faixa: id, faixaEmCima: alvo.emCima };
  }
  let pos = alvo.pos;
  if (i >= 0) {
    if (pos === i || pos === i + 1) return l;  // soltou no próprio lugar
    const [larg] = larguras.splice(i, 1);
    colunas.splice(i, 1);
    if (pos > i) pos--;
    colunas.splice(pos, 0, id);
    larguras.splice(pos, 0, larg);
    return { ...l, colunas, larguras };
  }
  // a faixa virou coluna: entra com a largura média das outras
  colunas.splice(pos, 0, id);
  larguras.splice(pos, 0, larguras.reduce((a, b) => a + b, 0) / larguras.length);
  return { ...l, colunas, larguras, faixa: null };
}
/** Layout por conversa. Conversa sem layout salvo começa no padrão que o usuário escolheu
 * ("Salvar como padrão", guardado em `_padrao`), ou no original. */
export function layoutDe(chave: string, todos: Record<string, Partial<Layout>>): Layout {
  const salvo = ((chave !== "_nova" && todos[chave]) || todos._padrao || {}) as Partial<Layout> & {
    cols?: number[]; ordem?: Bloco[]; docaEmCima?: boolean;  // formato anterior: 3 colunas + doca
  };
  const l: Layout = Array.isArray(salvo.colunas) ? { ...LAYOUT_PADRAO, ...salvo } : {
    ...LAYOUT_PADRAO,
    dock: salvo.dock ?? LAYOUT_PADRAO.dock,
    colunas: salvo.ordem ?? LAYOUT_PADRAO.colunas,
    larguras: salvo.cols ?? LAYOUT_PADRAO.larguras,
    faixaEmCima: !!salvo.docaEmCima,
  };
  // Conferência: cada bloco uma vez só, nenhum faltando; senão, padrão (layout de versão diferente).
  const todosBlocos = [...l.colunas, ...(l.faixa ? [l.faixa] : [])];
  const valido = l.colunas.length > 0 && l.larguras.length === l.colunas.length
    && [...todosBlocos].sort().join() === [...BLOCOS].sort().join();
  if (!valido) return LAYOUT_PADRAO;
  return { ...l, recolhidos: (Array.isArray(l.recolhidos) ? l.recolhidos : []).filter((b) => BLOCOS.includes(b)) };
}

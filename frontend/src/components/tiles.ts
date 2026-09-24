/**
 * Grade dos tiles à direita do conteúdo principal: colunas, cada uma com uma pilha de tiles. Lógica
 * pura, sem React nem storage, para ser testada sozinha (tiles.test.ts, `npm test`).
 */

/** Uma coluna: os tiles de cima para baixo, a largura em px e a altura de cada um em partes. */
export type Coluna<T extends string = string> = { tabs: T[]; largura: number; alturas: number[] };
/** `fixos`: itens que não fecham nem contam no limite (os blocos do cockpit da Maestro). Movem e
 * recolhem como qualquer outro. */
export type Grade<T extends string = string> = { colunas: Coluna<T>[]; recolhidos: T[]; fixos?: T[] };

export const MAX_TILES = 4;  // sem contar o conteúdo principal nem os fixos
export const GRADE_VAZIA: Grade<never> = { colunas: [], recolhidos: [] };

export const abertos = <T extends string>(g: Grade<T>): T[] => g.colunas.flatMap((c) => c.tabs);
/** Os abertos que contam no limite e aparecem ligados na barra do topo. */
export const soltos = <T extends string>(g: Grade<T>): T[] => abertos(g).filter((t) => !g.fixos?.includes(t));

/** Abre sem mexer nos outros, alternando lado e baixo: a coluna da ponta com um tile só ganha o novo
 * embaixo; senão ele abre numa coluna nova à direita. Já aberto ou no limite: nada muda. */
export function abrir<T extends string>(g: Grade<T>, t: T, largura: number): Grade<T> {
  if (abertos(g).includes(t) || soltos(g).length >= MAX_TILES) return g;
  const ult = g.colunas[g.colunas.length - 1];
  if (ult?.tabs.length === 1)
    return { ...g, colunas: [...g.colunas.slice(0, -1), { ...ult, tabs: [...ult.tabs, t], alturas: [...ult.alturas, ult.alturas[0]] }] };
  return { ...g, colunas: [...g.colunas, { tabs: [t], largura, alturas: [1] }] };
}

export function fechar<T extends string>(g: Grade<T>, t: T): Grade<T> {
  return g.fixos?.includes(t) ? g : tirar(g, t);
}

function tirar<T extends string>(g: Grade<T>, t: T): Grade<T> {
  const colunas = g.colunas
    .map((c) => {
      const i = c.tabs.indexOf(t);
      return i < 0 ? c : { ...c, tabs: c.tabs.filter((_, j) => j !== i), alturas: c.alturas.filter((_, j) => j !== i) };
    })
    .filter((c) => c.tabs.length);
  return { ...g, colunas, recolhidos: g.recolhidos.filter((x) => x !== t) };
}

/** Onde o tile arrastado cai, em relação ao tile sob o ponteiro: coluna nova à esquerda/direita da
 * dele, na pilha dele em cima/embaixo, ou no meio (trocam de lugar). */
export type Lado = "esq" | "dir" | "cima" | "baixo" | "centro";

export function mover<T extends string>(g: Grade<T>, t: T, com: T, lado: Lado): Grade<T> {
  if (t === com) return g;
  const onde = (x: T, cs: Coluna<T>[]) => {
    const c = cs.findIndex((c) => c.tabs.includes(x));
    return [c, c < 0 ? -1 : cs[c].tabs.indexOf(x)];
  };
  if (lado === "centro") {
    const [ct, it] = onde(t, g.colunas), [cc, ic] = onde(com, g.colunas);
    const colunas = g.colunas.map((c) => ({ ...c, tabs: [...c.tabs] }));
    colunas[ct].tabs[it] = com;
    colunas[cc].tabs[ic] = t;
    return { ...g, colunas };
  }
  const largura = g.colunas[onde(t, g.colunas)[0]].largura;
  const sem = tirar(g, t).colunas;  // sem o tile; se estava recolhido, continua
  const [c, i] = onde(com, sem);
  const colunas = [...sem];
  if (lado === "esq" || lado === "dir") {
    colunas.splice(c + (lado === "dir" ? 1 : 0), 0, { tabs: [t], largura, alturas: [1] });
  } else {
    const col = colunas[c], pos = i + (lado === "baixo" ? 1 : 0);
    const media = col.alturas.reduce((a, b) => a + b, 0) / col.alturas.length;
    colunas[c] = { ...col, tabs: [...col.tabs.slice(0, pos), t, ...col.tabs.slice(pos)],
      alturas: [...col.alturas.slice(0, pos), media, ...col.alturas.slice(pos)] };
  }
  return { ...g, colunas };
}

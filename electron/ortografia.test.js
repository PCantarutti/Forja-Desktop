// node --test electron/ortografia.test.js — o menu do botão direito sem abrir o Electron.
const test = require("node:test");
const assert = require("node:assert");
const { itensDoMenu } = require("./ortografia");

const trocadas = [];
const wc = { session: { addWordToSpellCheckerDictionary() {} }, replaceMisspelling: (s) => trocadas.push(s) };
const rotulos = (itens) => itens.map((i) => i.label || i.type);

test("palavra errada num campo: sugestões, dicionário e edição", () => {
  const itens = itensDoMenu(wc, { isEditable: true, misspelledWord: "conteudo", dictionarySuggestions: ["conteúdo"],
                                  editFlags: { canUndo: true, canCopy: true, canPaste: true, canSelectAll: true } });
  assert.deepStrictEqual(rotulos(itens), ["conteúdo", "separator", "Adicionar ao dicionário", "separator", "Desfazer", "Refazer",
                                          "separator", "Recortar", "Copiar", "Colar", "separator", "Selecionar tudo"]);
  itens[0].click();
  assert.deepStrictEqual(trocadas, ["conteúdo"]);
  assert.strictEqual(itens.find((i) => i.label === "Refazer").enabled, false);
});

test("sem sugestão: avisa em vez de menu vazio", () => {
  assert.strictEqual(itensDoMenu(wc, { isEditable: true, misspelledWord: "xptoq", dictionarySuggestions: [], editFlags: {} })[0].label, "Sem sugestões");
});

test("fora de campo: só copiar a seleção; nada selecionado, sem menu", () => {
  assert.deepStrictEqual(rotulos(itensDoMenu(wc, { isEditable: false, selectionText: "olá" })), ["Copiar"]);
  assert.deepStrictEqual(itensDoMenu(wc, { isEditable: false, selectionText: "" }), []);
});

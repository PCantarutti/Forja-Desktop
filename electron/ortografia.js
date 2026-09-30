// Correção ortográfica em todo campo de texto do app: o corretor do Chromium em português e o menu
// do botão direito que o Electron não traz pronto — sugestões para a palavra sublinhada, "Adicionar
// ao dicionário" e desfazer/recortar/copiar/colar, como no navegador.
const { Menu, clipboard } = require("electron");

// Só português: com o inglês junto, o dicionário inglês toma as sugestões ("conteudo" → "content" em vez de
// "conteúdo"). Termo técnico em inglês sublinhado vai para "Adicionar ao dicionário".
const IDIOMAS = ["pt-BR"];

/** Monta os itens do menu a partir do que o clique acertou (params do evento context-menu). */
function itensDoMenu(wc, p) {
  const itens = [];
  const ses = wc.session;
  if (p.misspelledWord) {
    const sugestoes = (p.dictionarySuggestions || []).slice(0, 6);
    for (const s of sugestoes) itens.push({ label: s, click: () => wc.replaceMisspelling(s) });
    if (!sugestoes.length) itens.push({ label: "Sem sugestões", enabled: false });
    itens.push({ type: "separator" },
      { label: "Adicionar ao dicionário", click: () => ses.addWordToSpellCheckerDictionary(p.misspelledWord) },
      { type: "separator" });
  }
  if (p.isEditable) {
    const f = p.editFlags || {};
    itens.push(
      { label: "Desfazer", role: "undo", accelerator: "CmdOrCtrl+Z", enabled: !!f.canUndo },
      { label: "Refazer", role: "redo", accelerator: "CmdOrCtrl+Y", enabled: !!f.canRedo },
      { type: "separator" },
      { label: "Recortar", role: "cut", accelerator: "CmdOrCtrl+X", enabled: !!f.canCut },
      { label: "Copiar", role: "copy", accelerator: "CmdOrCtrl+C", enabled: !!f.canCopy },
      { label: "Colar", role: "paste", accelerator: "CmdOrCtrl+V", enabled: !!f.canPaste },
      { type: "separator" },
      { label: "Selecionar tudo", role: "selectAll", accelerator: "CmdOrCtrl+A", enabled: !!f.canSelectAll });
  } else if ((p.selectionText || "").trim()) {
    itens.push({ label: "Copiar", role: "copy", accelerator: "CmdOrCtrl+C" });
  }
  if (p.linkURL && !p.isEditable && /^https?:/i.test(p.linkURL)) {
    if (itens.length) itens.push({ type: "separator" });
    itens.push({ label: "Copiar endereço do link", click: () => clipboard.writeText(p.linkURL) });
  }
  return itens;
}

/** Liga o corretor na sessão da janela e o menu do botão direito. `aoMontar` (testes) recebe os itens em vez de abrir o menu. */
function ligarOrtografia(win, aoMontar) {
  const wc = win.webContents;
  const ses = wc.session;
  ses.setSpellCheckerEnabled(true);
  const disponiveis = ses.availableSpellCheckerLanguages || [];
  // No Windows o corretor é o do sistema: só entram os idiomas que ele tem (o português do Windows do usuário).
  const idiomas = disponiveis.length ? IDIOMAS.filter((l) => disponiveis.includes(l)) : IDIOMAS;
  try {
    if (idiomas.length) ses.setSpellCheckerLanguages(idiomas);
  } catch (e) {
    console.error(`Forja: corretor ortográfico sem ${idiomas.join(", ")}: ${e.message}`);
  }
  wc.on("context-menu", (_e, p) => {
    const itens = itensDoMenu(wc, p);
    if (aoMontar) return aoMontar(itens, p);
    if (itens.length) Menu.buildFromTemplate(itens).popup({ window: win, frame: p.frame || undefined });
  });
  return idiomas;
}

module.exports = { ligarOrtografia, itensDoMenu };

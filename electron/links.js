/**
 * Para onde um link pode levar: dentro da janela (só a origem do backend) e para fora (só página).
 *
 * Self-check: node electron/links.js
 */

/**
 * A janela carrega a interface do backend e nada mais.
 *
 * Comparar por `startsWith` deixava passar `http://127.0.0.1:PORTA@evil.com/`: o que vem antes do
 * `@` é userinfo, o host de verdade é o evil.com — e ele herdaria a janela com o preload
 * (`window.forja`) e a mesma origem da API local.
 */
function sameOrigin(url, origin) {
  try {
    return new URL(url).origin === origin;
  } catch {
    return false; // o que não parseia não é a nossa origem
  }
}

/**
 * O que pode ir para o navegador do usuário.
 *
 * `shell.openExternal` entrega ao Windows qualquer esquema registrado: `file:` executa o arquivo
 * (é o que acontece ao arrastar um .exe para a janela), `smb://` vaza o hash NTLM para o host
 * remoto, `ms-msdt:` e afins têm histórico próprio. O chat mostra texto do modelo e de páginas
 * que o agente leu, então o link nem sempre é de quem está olhando.
 */
const EXTERNOS = ["http:", "https:", "mailto:"];

function isSafeExternal(url) {
  try {
    return EXTERNOS.includes(new URL(url).protocol);
  } catch {
    return false;
  }
}

module.exports = { sameOrigin, isSafeExternal };

if (require.main === module) {
  const assert = require("node:assert/strict");
  const nossa = "http://127.0.0.1:54321";
  assert.equal(sameOrigin("http://127.0.0.1:54321/conversas/7", nossa), true);
  assert.equal(sameOrigin("http://127.0.0.1:54321@evil.com/", nossa), false); // userinfo, não host
  assert.equal(sameOrigin("http://127.0.0.1:54322/", nossa), false); // outra porta
  assert.equal(sameOrigin("https://127.0.0.1:54321/", nossa), false); // outro esquema
  assert.equal(sameOrigin("nao e url", nossa), false);
  assert.equal(isSafeExternal("https://exemplo.com/a"), true);
  assert.equal(isSafeExternal("mailto:a@b.c"), true);
  assert.equal(isSafeExternal("file:///C:/Users/x/virus.exe"), false);
  assert.equal(isSafeExternal("smb://attacker/share"), false); // hash NTLM
  assert.equal(isSafeExternal("ms-msdt:/id"), false);
  assert.equal(isSafeExternal("javascript:alert(1)"), false);
  console.log("links ok");
}

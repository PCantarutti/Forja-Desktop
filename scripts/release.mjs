/**
 * Publica uma versão, do começo ao fim, num comando só:  npm run release
 *
 * Pergunta a versão e o texto da release, roda a bateria de testes, sobe a versão, commita,
 * empurra e publica no GitHub. Para antes de qualquer coisa irreversível se algo não estiver no
 * lugar — token sem permissão, árvore suja ou teste falhando — porque descobrir isso depois de dez
 * minutos de empacotamento e 269 MB de upload custa caro.
 *
 * Atalhos:  --rapido  pula os testes      --seco  faz tudo menos publicar
 *
 * Notas da versão: Markdown, uma seção por área e um item por mudança. É o texto do aviso de
 * atualização dentro do app (vai no latest.yml) e também a descrição da release no GitHub.
 *
 *   ### Imagens
 *   - **Edição de imagem**: clique no lápis de uma imagem gerada e descreva a mudança.
 *   - **Cancelar** encerra a geração na hora.
 *
 *   ### Correções
 *   - Erros da aba Imagens agora aparecem na própria aba.
 *
 * No modo interativo: linha começando com # abre uma seção, as outras viram itens.
 */
import { execFileSync, execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline/promises";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const NL = String.fromCharCode(10);
const PULAR_TESTES = process.argv.includes("--rapido");
const SECO = process.argv.includes("--seco");

const cor = (c, s) => `${String.fromCharCode(27)}[${c}m${s}${String.fromCharCode(27)}[0m`;
const passo = (s) => console.log(NL + cor(36, "=== " + s));
const aviso = (s) => console.log(cor(33, "  ! " + s));
const ok = (s) => console.log(cor(32, "  ok ") + s);

function parar(motivo, comoResolver) {
  console.error(NL + cor(31, "Parei aqui: ") + motivo);
  if (comoResolver) console.error("  " + comoResolver);
  process.exit(1);
}

const git = (args) => execFileSync("git", args, { cwd: ROOT, encoding: "utf8" }).trim();
const rodar = (cmd, args, cwd = ROOT) =>
  execFileSync(cmd, args, { cwd, stdio: "inherit", shell: process.platform === "win32" });

// ------------------------------------------------------------------ conferências

async function conferirGit() {
  passo("conferindo o repositório");
  const branch = git(["rev-parse", "--abbrev-ref", "HEAD"]);
  if (branch !== "main") parar(`você está na branch ${branch}, não na main.`, "git switch main");
  if (git(["status", "--porcelain"])) {
    parar("há mudanças não commitadas.", "Commite ou guarde antes: git status");
  }
  git(["fetch", "origin", "--quiet"]);
  const pendentes = git(["rev-list", "--count", "origin/main..main"]);
  if (pendentes !== "0") {
    parar(`há ${pendentes} commit(s) não enviados.`,
      "A release apontaria para código que não está no GitHub. Rode: git push origin main");
  }
  ok(`main limpa e sincronizada (${git(["rev-parse", "--short", "HEAD"])})`);
}

async function conferirToken() {
  passo("conferindo o token do GitHub");
  const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
  if (!token) {
    parar("GH_TOKEN não está no ambiente.",
      'Gere um token clássico com escopo "repo" e rode, num terminal NOVO:  setx GH_TOKEN "ghp_..."');
  }
  const { owner, repo } = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8")).build.publish[0];
  const r = await fetch(`https://api.github.com/repos/${owner}/${repo}`, {
    headers: { Authorization: `token ${token}`, "User-Agent": "forja-release" },
  });
  if (r.status === 401) parar("o token foi recusado (401).", "Ele expirou, foi revogado, ou a variável tem um valor velho.");
  if (!r.ok) parar(`o GitHub respondeu ${r.status} ao consultar ${owner}/${repo}.`);
  // Ler o repositório não prova que dá para criar release; a permissão de escrita é outra.
  const permissoes = (await r.json()).permissions || {};
  if (!permissoes.push) {
    parar("o token lê o repositório mas não escreve nele.",
      'Token clássico precisa do escopo "repo". Fine-grained precisa de Contents: Read and write ' +
      'E do repositório escolhido em "Only select repositories".');
  }
  ok(`token válido, com escrita em ${owner}/${repo}`);
}

/**
 * `publisherName` liga a checagem de assinatura do electron-updater: ele só instala se o .exe
 * baixado estiver assinado por esse nome. Com o instalador sem assinatura, a atualização baixa os
 * 245 MB e é recusada na hora de instalar — foi o que aconteceu na 0.3.2. Só faz sentido declarar
 * o nome quando existe certificado para assinar de verdade.
 */
function conferirAssinatura() {
  passo("conferindo a assinatura");
  const { build } = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  const win = build.win || {};
  const nome = win.publisherName ?? win.signtoolOptions?.publisherName;
  const assina =
    process.env.CSC_LINK || process.env.WIN_CSC_LINK || win.certificateFile || win.signtoolOptions?.certificateFile;
  if (nome && !assina) {
    parar(`o build declara publisherName "${nome}" mas não assina nada.`,
      "O updater vai recusar a instalação. Tire o publisherName do package.json, ou configure o certificado.");
  }
  ok(nome ? "assinatura configurada" : "sem assinatura, e sem publisherName declarado (o updater não vai barrar)");
}

function rodarTestes() {
  if (PULAR_TESTES) return aviso("testes pulados (--rapido)");
  passo("rodando a bateria");
  const py = path.join(ROOT, "backend", ".venv", "Scripts", "python.exe");
  rodar(fs.existsSync(py) ? py : "python", ["-m", "pytest", "-q"], path.join(ROOT, "backend"));
  rodar("npm", ["run", "typecheck"], path.join(ROOT, "frontend"));
  rodar("npm", ["run", "lint"], path.join(ROOT, "frontend"));
  for (const modulo of ["zoom.js", "links.js", "browserHost.js"]) {
    rodar("node", [path.join("electron", modulo)]);
  }
  ok("tudo passou");
}

// ------------------------------------------------------------------ perguntas

function proxima(versao, tipo) {
  const [maior, menor, correcao] = versao.split(".").map(Number);
  if (tipo === "maior") return `${maior + 1}.0.0`;
  if (tipo === "menor") return `${maior}.${menor + 1}.0`;
  return `${maior}.${menor}.${correcao + 1}`;
}

const VERSAO_OK = /^[0-9]+[.][0-9]+[.][0-9]+$/;

/** Valor de `--nome valor` na linha de comando, ou null. */
function opcao(nome) {
  const i = process.argv.indexOf(nome);
  return i >= 0 ? process.argv[i + 1] ?? null : null;
}

/**
 * Sem perguntas: `--versao 0.3.3 --notas notas.md`. Existe porque o prompt não sobrevive a um
 * stdin que não é terminal — o readline fecha no fim do arquivo e a pergunta seguinte estoura.
 * Devolve null quando não foi pedido, e aí segue o caminho interativo.
 */
function semPerguntar(atual) {
  const versao = opcao("--versao");
  const arq = opcao("--notas");
  if (!versao && !arq) return null;
  if (!versao || !arq) parar("--versao e --notas andam juntos.", "Ex.: npm run release -- --versao 0.3.3 --notas notas.md");
  if (!VERSAO_OK.test(versao)) parar(`"${versao}" não é uma versão válida.`);
  if (versao === atual) parar("a versão precisa ser diferente da atual: o updater compara número.");
  if (!fs.existsSync(arq)) parar(`não achei o arquivo de notas: ${arq}`);
  const notas = fs.readFileSync(arq, "utf8").trim();
  conferirNotas(notas);
  passo(`versão ${versao} (sem perguntar)`);
  return { versao, notas };
}

/** O formato do cabeçalho: sem nenhum item "- " o aviso vira um parágrafo corrido difícil de ler. */
function conferirNotas(notas) {
  if (!notas) parar("sem texto de release.", "Quem for atualizar precisa saber o que muda.");
  if (!notas.split(NL).some((l) => /^\s*[-*] /.test(l))) {
    parar("as notas não têm nenhum item de lista (linha começando com \"- \").",
      "Use o formato do cabeçalho de scripts/release.mjs: ### Área e um - item por mudança.");
  }
}

async function perguntar(rl, atual) {
  passo("versão");
  const opcoes = {
    1: ["correção", proxima(atual, "correcao"), "conserto de bug, sem novidade"],
    2: ["novidade", proxima(atual, "menor"), "feature nova, compatível"],
    3: ["quebra", proxima(atual, "maior"), "mudou algo que estava funcionando"],
  };
  console.log(`  a versão de agora é ${cor(36, atual)}`);
  for (const [n, [rotulo, v, quando]] of Object.entries(opcoes)) {
    console.log(`  ${n}) ${v.padEnd(10)} ${rotulo.padEnd(9)} ${cor(90, quando)}`);
  }
  console.log("  4) outra      (você digita)");

  const escolha = (await rl.question(NL + "  escolha [1]: ")).trim() || "1";
  let versao = opcoes[escolha]?.[1];
  if (escolha === "4") versao = (await rl.question("  versão (ex.: 1.2.0): ")).trim();
  if (!VERSAO_OK.test(versao || "")) parar(`"${versao}" não é uma versão válida.`);
  if (versao === atual) parar("a versão precisa ser diferente da atual: o updater compara número.");

  passo("o que mudou nesta versão");
  console.log(cor(90, "  Uma linha por item; \"# Área\" abre uma seção (ex.: # Imagens). Linha vazia encerra."));
  console.log(cor(90, "  É o que o app mostra ao avisar da atualização. **negrito** e `código` valem."));
  const linhas = [];
  for (;;) {
    const linha = (await rl.question("  > ")).trim();
    if (!linha) break;
    if (linha.startsWith("#")) linhas.push((linhas.length ? NL : "") + "### " + linha.replace(/^#+\s*/, ""), "");
    else linhas.push("- " + linha.replace(/^[-*]\s*/, ""));
  }
  const notas = linhas.join(NL).trim();
  conferirNotas(notas);
  return { versao, notas };
}

// ------------------------------------------------------------------ publicação

function subirVersao(versao, notas) {
  passo(`preparando a ${versao}`);
  const tocados = [];
  for (const dir of [ROOT, path.join(ROOT, "frontend")]) {
    // O lock guarda a versão em dois lugares. Sem atualizar, o próximo `npm install` reescreve o
    // arquivo e a árvore amanhece suja — o que faz a release SEGUINTE parar na conferência do git.
    for (const nome of ["package.json", "package-lock.json"]) {
      const arq = path.join(dir, nome);
      if (!fs.existsSync(arq)) continue;
      const json = JSON.parse(fs.readFileSync(arq, "utf8"));
      json.version = versao;
      if (json.packages?.[""]) json.packages[""].version = versao;
      fs.writeFileSync(arq, JSON.stringify(json, null, 2) + NL);
      tocados.push(path.relative(ROOT, arq).split(path.sep).join("/"));
    }
  }
  // O electron-builder lê este arquivo sozinho: getResource() procura "release-notes.md" em
  // buildResources (= build/), e o conteúdo vai para o latest.yml — é o texto que o app mostra
  // ao avisar da atualização. Não entra no git: build/ é gerado e está no .gitignore.
  fs.mkdirSync(path.join(ROOT, "build"), { recursive: true });
  fs.writeFileSync(path.join(ROOT, "build", "release-notes.md"), notas + NL);
  ok("versão e notas gravadas");

  if (SECO) return aviso("--seco: não vou commitar nem publicar");
  git(["add", ...tocados]);
  execSync(`git commit -q -m "chore: versão ${versao}"`, { cwd: ROOT });
  git(["push", "origin", "main"]);
  ok(`commit da versão enviado`);
}

/**
 * O electron-builder cria a release com a descrição vazia (as notas só vão para o latest.yml, que é
 * de onde o app lê). Quem abre a página no GitHub merece o mesmo texto. Falhar aqui não desfaz nada:
 * o instalador já subiu, então é aviso, não parada.
 */
async function descreverRelease(versao, notas) {
  if (SECO) return;
  const { owner, repo } = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8")).build.publish[0];
  const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
  const h = { Authorization: `token ${token}`, "User-Agent": "forja-release", "Content-Type": "application/json" };
  try {
    // Rascunho não aparece em /releases/tags/...: só na lista, e só para quem tem escrita.
    const lista = await (await fetch(`https://api.github.com/repos/${owner}/${repo}/releases?per_page=20`, { headers: h })).json();
    const rel = Array.isArray(lista) && lista.find((r) => r.tag_name === `v${versao}`);
    if (!rel) return aviso(`não achei a release v${versao} para preencher a descrição.`);
    // tag_name vai junto SEMPRE: editar um rascunho sem ele troca a tag por "untagged-…", e a
    // release publicada sai sem tag (a 0.6.1 sumiu da lista assim, 23/09/2026).
    const r = await fetch(rel.url, { method: "PATCH", headers: h, body: JSON.stringify({ tag_name: rel.tag_name, body: notas }) });
    if (!r.ok) return aviso(`o GitHub respondeu ${r.status} ao gravar a descrição.`);
    ok("descrição da release preenchida com as notas");
  } catch (e) {
    aviso(`não deu para preencher a descrição: ${e.message}`);
  }
}

function publicar() {
  if (SECO) return aviso("--seco: parando antes de empacotar");
  passo("empacotando e publicando (demora; o Python e o Chromium vêm do cache)");
  rodar("node", [path.join("scripts", "prepare.mjs")]);
  rodar("npx", ["electron-builder", "--win", "nsis", "--publish", "always"]);
}

// ------------------------------------------------------------------

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
try {
  const atual = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8")).version;
  await conferirGit();
  await conferirToken();
  conferirAssinatura();
  const { versao, notas } = semPerguntar(atual) ?? (await perguntar(rl, atual));
  rl.close();
  rodarTestes();
  subirVersao(versao, notas);
  publicar();
  await descreverRelease(versao, notas);

  const { owner, repo } = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8")).build.publish[0];
  console.log(NL + cor(32, `Pronto: ${versao} empacotada e enviada.`));
  console.log(cor(33, "Falta um passo manual:") + " a release nasce como RASCUNHO e o updater não");
  console.log("enxerga rascunho. Abra e clique em Publish release (a descrição já leva as notas):");
  console.log("  " + cor(36, `https://github.com/${owner}/${repo}/releases`));
} finally {
  rl.close();
}

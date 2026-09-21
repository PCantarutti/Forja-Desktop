/**
 * Publica uma versão, do começo ao fim, num comando só:  npm run release
 *
 * Pergunta a versão e o texto da release, roda a bateria de testes, sobe a versão, commita,
 * empurra e publica no GitHub. Para antes de qualquer coisa irreversível se algo não estiver no
 * lugar — token sem permissão, árvore suja ou teste falhando — porque descobrir isso depois de dez
 * minutos de empacotamento e 269 MB de upload custa caro.
 *
 * Atalhos:  --rapido  pula os testes      --seco  faz tudo menos publicar
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
  if (!/^[0-9]+[.][0-9]+[.][0-9]+$/.test(versao || "")) parar(`"${versao}" não é uma versão válida.`);
  if (versao === atual) parar("a versão precisa ser diferente da atual: o updater compara número.");

  passo("o que mudou nesta versão");
  console.log(cor(90, "  Uma linha por item. Linha vazia encerra. Isto vira o texto da release no GitHub."));
  const linhas = [];
  for (;;) {
    const linha = await rl.question("  - ");
    if (!linha.trim()) break;
    linhas.push("- " + linha.trim());
  }
  if (!linhas.length) parar("sem texto de release.", "Quem for atualizar precisa saber o que muda.");
  return { versao, notas: linhas.join(NL) };
}

// ------------------------------------------------------------------ publicação

function subirVersao(versao, notas) {
  passo(`preparando a ${versao}`);
  for (const dir of [ROOT, path.join(ROOT, "frontend")]) {
    const arq = path.join(dir, "package.json");
    const pkg = JSON.parse(fs.readFileSync(arq, "utf8"));
    pkg.version = versao;
    fs.writeFileSync(arq, JSON.stringify(pkg, null, 2) + NL);
  }
  // O electron-builder lê este arquivo sozinho (releaseInfo.releaseNotesFile tem esse padrão).
  fs.mkdirSync(path.join(ROOT, "build"), { recursive: true });
  fs.writeFileSync(path.join(ROOT, "build", "release-notes.md"), notas + NL);
  ok("versão e notas gravadas");

  if (SECO) return aviso("--seco: não vou commitar nem publicar");
  git(["add", "package.json", "frontend/package.json", "build/release-notes.md"]);
  execSync(`git commit -q -m "chore: versão ${versao}"`, { cwd: ROOT });
  git(["push", "origin", "main"]);
  ok(`commit da versão enviado`);
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
  const { versao, notas } = await perguntar(rl, atual);
  rl.close();
  rodarTestes();
  subirVersao(versao, notas);
  publicar();

  const { owner, repo } = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8")).build.publish[0];
  console.log(NL + cor(32, `Pronto: ${versao} empacotada e enviada.`));
  console.log(cor(33, "Falta um passo manual:") + " a release nasce como RASCUNHO e o updater não");
  console.log("enxerga rascunho. Abra e clique em Publish release:");
  console.log("  " + cor(36, `https://github.com/${owner}/${repo}/releases`));
} finally {
  rl.close();
}

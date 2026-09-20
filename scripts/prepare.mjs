/**
 * Monta resources/ para o instalador: Python portátil + dependências, Chromium do Playwright,
 * build da interface e o código do backend. Roda antes do electron-builder (npm run dist).
 *
 * Tudo em resources/ é gerado: apagar a pasta e rodar de novo é sempre seguro.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RES = path.join(ROOT, "resources");
const PY_DIR = path.join(RES, "python");
const PY = path.join(PY_DIR, "python.exe");
const PY_VERSION = "3.12"; // a mesma linha usada no desenvolvimento do backend

const run = (cmd, args, opts = {}) =>
  execFileSync(cmd, args, { stdio: "inherit", cwd: ROOT, ...opts, env: { ...process.env, ...(opts.env ?? {}) } });

const step = (msg) => console.log(`\n=== ${msg}`);

/** CPython relocável do python-build-standalone (o mesmo que o uv usa). */
async function fetchPython() {
  if (fs.existsSync(PY)) return console.log("python/ já está pronto");
  step("baixando o Python portátil");
  const r = await fetch("https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest", {
    headers: { "User-Agent": "forja-desktop-build" },
  });
  if (!r.ok) throw new Error(`GitHub respondeu ${r.status} ao listar releases do python-build-standalone`);
  const rx = new RegExp(`^cpython-${PY_VERSION}\\.\\d+\\+\\d+-x86_64-pc-windows-msvc-install_only\\.tar\\.gz$`);
  const asset = (await r.json()).assets.find((a) => rx.test(a.name));
  if (!asset) throw new Error(`nenhum build ${PY_VERSION} x86_64-pc-windows-msvc (install_only) nesse release`);
  const tgz = path.join(RES, asset.name);
  fs.mkdirSync(RES, { recursive: true });
  if (!fs.existsSync(tgz)) {
    const dl = await fetch(asset.browser_download_url);
    if (!dl.ok) throw new Error(`falha ao baixar ${asset.name}: HTTP ${dl.status}`);
    fs.writeFileSync(tgz, Buffer.from(await dl.arrayBuffer()));
  }
  // O tar do Windows (bsdtar) entende C:\...; o tar do Git Bash acha que é um host remoto.
  const winTar = path.join(process.env.SystemRoot ?? "C:\\Windows", "System32", "tar.exe");
  run(fs.existsSync(winTar) ? winTar : "tar", ["-xzf", asset.name], { cwd: RES }); // cria python/
  fs.rmSync(tgz);
  if (!fs.existsSync(PY)) throw new Error("o arquivo extraiu, mas não achei resources/python/python.exe");
}

function pipInstall() {
  step("instalando as dependências do backend");
  run(PY, ["-m", "pip", "install", "--upgrade", "pip", "--no-warn-script-location"]);
  run(PY, ["-m", "pip", "install", "--no-warn-script-location", "-r", path.join(ROOT, "backend", "requirements.txt")]);
}

/**
 * Chromium do navegador integrado.
 *
 * O download é feito aqui, com fetch, em vez de `playwright install`: o downloader embutido do
 * Playwright trava nesta rede (estoura o timeout sem receber um byte, mesmo com 300 s). O `--dry-run`
 * dele diz o que baixar e para onde; o resto é descompactar e deixar o marcador que ele procura.
 */
async function playwrightChromium() {
  step("baixando o Chromium do navegador integrado");
  const browsers = path.join(RES, "ms-playwright");
  const dry = execFileSync(PY, ["-m", "playwright", "install", "--only-shell", "--dry-run", "chromium"], {
    cwd: ROOT,
    env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: browsers },
  }).toString();

  const pacotes = [];
  for (const bloco of dry.split(/\n(?=\S)/)) {
    const dir = bloco.match(/Install location:\s+(.+)/)?.[1]?.trim();
    const url = bloco.match(/Download url:\s+(\S+)/)?.[1];
    if (dir && url) pacotes.push({ dir, url });
  }
  if (!pacotes.length) throw new Error("não consegui ler o --dry-run do playwright");

  const winTar = path.join(process.env.SystemRoot ?? "C:\\Windows", "System32", "tar.exe");
  for (const { dir, url } of pacotes) {
    const marker = path.join(dir, "INSTALLATION_COMPLETE"); // é o que o Playwright checa
    if (fs.existsSync(marker)) {
      console.log(`já instalado: ${path.basename(dir)}`);
      continue;
    }
    console.log(`${path.basename(dir)} <- ${url}`);
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status} ao baixar ${url}`);
    const zip = path.join(RES, path.basename(new URL(url).pathname));
    fs.writeFileSync(zip, Buffer.from(await r.arrayBuffer()));
    fs.mkdirSync(dir, { recursive: true });
    run(fs.existsSync(winTar) ? winTar : "tar", ["-xf", zip], { cwd: dir });
    fs.rmSync(zip);
    fs.writeFileSync(marker, "");
  }
}

function buildUi() {
  step("build da interface");
  const ui = path.join(ROOT, "frontend");
  run("npm", [fs.existsSync(path.join(ui, "node_modules")) ? "install" : "ci"], { cwd: ui, shell: true });
  run("npm", ["run", "build"], { cwd: ui, shell: true });
  fs.rmSync(path.join(RES, "web"), { recursive: true, force: true });
  fs.cpSync(path.join(ui, "dist"), path.join(RES, "web"), { recursive: true });
}

function copyBackend() {
  step("copiando o backend");
  const dest = path.join(RES, "backend", "app");
  fs.rmSync(path.join(RES, "backend"), { recursive: true, force: true });
  fs.cpSync(path.join(ROOT, "backend", "app"), dest, {
    recursive: true,
    filter: (src) => !src.includes("__pycache__"),
  });
}

function makeIcon() {
  const png = path.join(ROOT, "build", "icon.png"); // o electron-builder gera o .ico a partir dele
  if (fs.existsSync(png)) return console.log("build/icon.png já existe");
  step("gerando o ícone a partir do favicon");
  fs.mkdirSync(path.dirname(png), { recursive: true });
  run(PY, [path.join(ROOT, "scripts", "make_icon.py")], {
    env: { PLAYWRIGHT_BROWSERS_PATH: path.join(RES, "ms-playwright") },
  });
}

await fetchPython();
pipInstall();
await playwrightChromium();
makeIcon();
buildUi();
copyBackend();
console.log("\nresources/ pronto. Agora: npx electron-builder --win nsis");

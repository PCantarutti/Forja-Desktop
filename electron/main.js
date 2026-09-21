/**
 * Casca do Forja Desktop: sobe o backend (FastAPI) numa porta livre de 127.0.0.1 e mostra a
 * interface numa janela. Sem Docker, sem terminal, sem porta publicada na rede.
 *
 * Empacotado, o Python e o build da UI vêm de process.resourcesPath. Em dev (npm run dev), usa o
 * venv de backend/.venv e o FORJA_WEB que o script passar.
 */
const { app, BrowserWindow, Menu, Tray, dialog, ipcMain, nativeImage, screen, shell } = require("electron");
const { spawn, execFileSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const net = require("net");
const path = require("path");
const { ZOOM_STEPS, nextZoom } = require("./zoom");
const { BrowserHost } = require("./browserHost");

const DEV = !app.isPackaged;
const ROOT = DEV ? path.join(__dirname, "..") : process.resourcesPath;
const USER_DATA = app.getPath("userData");
const LOG_FILE = path.join(USER_DATA, "logs", "backend.log");
const DB_FILE = path.join(USER_DATA, "forja.db"); // conversas, provedores e configurações
const MD_DIR = path.join(USER_DATA, "conversas"); // espelho em Markdown (forja-code / forja-chat)
const PREFS_FILE = path.join(USER_DATA, "desktop.json");

// Navegador integrado nativo: o backend (Playwright) liga por CDP neste Chromium e controla as abas, que são
// WebContentsViews dentro da janela. Porta 0 = o sistema escolhe; o Chromium grava em DevToolsActivePort.
// Só 127.0.0.1 e sem --remote-allow-origins: página web nenhuma consegue conectar, só processos locais.
app.commandLine.appendSwitch("remote-debugging-port", "0");

let backend = null;
let port = 0;
// Token desta execução: o backend só atende /api com ele no header. Fecha a porta para outro
// processo (ou outro usuário) da mesma máquina, que alcança 127.0.0.1 tão bem quanto o app.
const token = crypto.randomBytes(32).toString("hex");
let mainWindow = null;
let tray = null;
let host = null; // views do navegador integrado (BrowserHost)

// ------------------------------------------------------------------ preferências da casca

/** Preferências do app instalado (não do agente): zoom, o que o X faz, início com o Windows, janela. */
const DEFAULT_PREFS = {
  zoom: 1,
  closeToTray: false, // false = o X fecha o app de verdade
  startWithWindows: false,
  startMinimized: false, // só vale junto com startWithWindows
  bounds: null, // { x, y, width, height, maximized }
};

let prefs = { ...DEFAULT_PREFS };

function loadPrefs() {
  try {
    // O replace tira o BOM: arquivo editado à mão no Bloco de Notas vem com ele e o JSON.parse quebra.
    prefs = { ...DEFAULT_PREFS, ...JSON.parse(fs.readFileSync(PREFS_FILE, "utf8").replace(/^﻿/, "")) };
  } catch {
    prefs = { ...DEFAULT_PREFS }; // primeira execução ou arquivo corrompido
  }
}

function savePrefs() {
  try {
    fs.mkdirSync(USER_DATA, { recursive: true });
    fs.writeFileSync(PREFS_FILE, JSON.stringify(prefs, null, 2));
  } catch {
    /* sem permissão ou disco cheio: não vale derrubar o app por isso */
  }
}

function iconPath() {
  return [path.join(ROOT, "icon.png"), path.join(ROOT, "build", "icon.png")].find((p) => fs.existsSync(p)) ?? null;
}

// ------------------------------------------------------------------ backend

function pythonExe() {
  const packaged = path.join(ROOT, "python", "python.exe");
  if (fs.existsSync(packaged)) return packaged;
  const venv = path.join(ROOT, "backend", ".venv", "Scripts", "python.exe"); // dev no Windows
  return fs.existsSync(venv) ? venv : path.join(ROOT, "backend", ".venv", "bin", "python");
}

function backendDir() {
  const packaged = path.join(ROOT, "backend");
  return fs.existsSync(path.join(packaged, "app")) ? packaged : path.join(ROOT, "backend");
}

/** Porta livre: pede 0 ao sistema e devolve a que ele deu. */
function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const p = srv.address().port;
      srv.close(() => resolve(p));
    });
  });
}

/** Endpoint CDP deste Electron (http://127.0.0.1:PORTA). Vazio se o Chromium não escreveu o arquivo a tempo. */
async function cdpEndpoint() {
  const file = path.join(USER_DATA, "DevToolsActivePort");
  for (let i = 0; i < 30; i++) {
    try {
      const p = Number(fs.readFileSync(file, "utf8").split("\n")[0]);
      if (p > 0) return `http://127.0.0.1:${p}`;
    } catch {
      /* ainda não escreveu */
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  return "";
}

function startBackend(cdp) {
  fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
  const log = fs.openSync(LOG_FILE, "a");
  const env = {
    ...process.env,
    FORJA_DATA: USER_DATA,
    FORJA_TOKEN: token,
    ...(cdp ? { FORJA_CDP: cdp } : {}), // sem CDP o backend cai no Chromium headless com espelho
    FORJA_WEB: process.env.FORJA_WEB ?? path.join(ROOT, "web"),
    PYTHONUNBUFFERED: "1",
    PYTHONUTF8: "1",
  };
  // Empacotado, o Chromium vem em resources/ms-playwright. Em dev, usa o de resources/ se já foi
  // baixado; senão deixa o Playwright procurar no cache dele.
  const browsers = [path.join(ROOT, "ms-playwright"), path.join(ROOT, "resources", "ms-playwright")].find((p) =>
    fs.existsSync(p),
  );
  if (browsers) env.PLAYWRIGHT_BROWSERS_PATH = browsers;
  backend = spawn(pythonExe(), ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: backendDir(),
    env,
    stdio: ["ignore", log, log],
    windowsHide: true,
  });
  backend.on("exit", (code) => {
    backend = null;
    if (code !== 0 && !app.isQuitting) {
      dialog.showErrorBox("Forja", `O backend encerrou (código ${code}).\n\nLog: ${LOG_FILE}`);
      app.quit();
    }
  });
}

/** Mata a árvore inteira: uvicorn, o Chromium do navegador integrado e os servidores do agente. */
function stopBackend() {
  if (!backend) return;
  const pid = backend.pid;
  backend = null;
  try {
    if (process.platform === "win32") execFileSync("taskkill", ["/T", "/F", "/PID", String(pid)]);
    else process.kill(-pid, "SIGKILL");
  } catch {
    /* já morreu */
  }
}

async function waitForBackend(timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!backend) throw new Error(`o backend não subiu. Log: ${LOG_FILE}`);
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/config`, { headers: { "x-forja-token": token } });
      if (r.ok) return;
    } catch {
      /* ainda subindo */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`o backend não respondeu em ${timeoutMs / 1000}s. Log: ${LOG_FILE}`);
}

// ------------------------------------------------------------------ zoom

function applyZoom(factor) {
  const z = Math.min(ZOOM_STEPS[ZOOM_STEPS.length - 1], Math.max(ZOOM_STEPS[0], factor));
  prefs.zoom = z;
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.setZoomFactor(z);
  host?.layout(); // o painel muda de tamanho em px reais junto com o zoom
  savePrefs();
  return z;
}

/** Anda um degrau a partir do zoom atual (dir = +1 / -1); dir 0 volta para 100%. */
const stepZoom = (dir) => applyZoom(nextZoom(prefs.zoom, dir));

/**
 * Ctrl + "+" / "-" / "0" tratados no próprio webContents: o menu nativo está escondido
 * (autoHideMenuBar) e os roles de zoom dele não pegam o "=" sem Shift nem as teclas do numérico.
 */
function wireZoomShortcuts(win) {
  win.webContents.setZoomFactor(prefs.zoom);
  win.webContents.on("did-finish-load", () => win.webContents.setZoomFactor(prefs.zoom));
  win.webContents.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown" || !input.control || input.alt || input.meta) return;
    const k = input.key;
    if (k === "+" || k === "=" || k === "Add") {
      e.preventDefault();
      stepZoom(+1);
    } else if (k === "-" || k === "_" || k === "Subtract") {
      e.preventDefault();
      stepZoom(-1);
    } else if (k === "0") {
      e.preventDefault();
      stepZoom(0);
    }
  });
  // Ctrl + roda do mouse e pinça no trackpad.
  win.webContents.on("zoom-changed", (e, direction) => {
    e.preventDefault();
    stepZoom(direction === "in" ? +1 : -1);
  });
}

// ------------------------------------------------------------------ bandeja do sistema

function showWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (!mainWindow.isVisible()) mainWindow.show();
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
}

function ensureTray() {
  if (tray) return;
  const icon = iconPath();
  const img = icon ? nativeImage.createFromPath(icon).resize({ width: 16, height: 16 }) : nativeImage.createEmpty();
  tray = new Tray(img);
  tray.setToolTip("Forja");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Abrir o Forja", click: showWindow },
      { type: "separator" },
      {
        label: "Sair",
        click: () => {
          app.isQuitting = true;
          app.quit();
        },
      },
    ]),
  );
  tray.on("click", showWindow);
}

function dropTray() {
  if (!tray) return;
  tray.destroy();
  tray = null;
}

/** O ícone da bandeja só existe quando serve para alguma coisa: fechar-para-bandeja ligado. */
function syncTray() {
  if (prefs.closeToTray) ensureTray();
  else dropTray();
}

// ------------------------------------------------------------------ janela

function saveBounds() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const maximized = mainWindow.isMaximized();
  // Maximizada, guarda o tamanho normal de antes: é para ele que a janela volta ao restaurar.
  prefs.bounds = { ...(maximized ? prefs.bounds ?? {} : mainWindow.getNormalBounds()), maximized };
  savePrefs();
}

/** Monitor que sumiu desde a última vez: descarta a posição salva e deixa o Windows centralizar. */
function onScreen(b) {
  if (!b || b.x == null) return b ?? {};
  const cabe = screen.getAllDisplays().some(({ workArea: a }) => {
    return b.x < a.x + a.width && b.x + b.width > a.x && b.y < a.y + a.height && b.y + b.height > a.y;
  });
  return cabe ? b : { width: b.width, height: b.height, maximized: b.maximized };
}

function createWindow({ hidden = false } = {}) {
  const b = onScreen(prefs.bounds);
  const icon = iconPath();
  const win = new BrowserWindow({
    width: b.width ?? 1280,
    height: b.height ?? 860,
    x: b.x,
    y: b.y,
    minWidth: 900,
    minHeight: 600,
    show: false,
    backgroundColor: "#171717", // a interface é escura: evita o flash branco
    title: "Forja",
    icon: icon ?? undefined,
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false },
  });
  mainWindow = win;
  if (b.maximized) win.maximize();
  if (!hidden) win.show();

  win.loadURL(`http://127.0.0.1:${port}`);
  wireZoomShortcuts(win);
  host = new BrowserHost(win, `http://127.0.0.1:${port}`, token);
  host.start();

  // Links para fora do app abrem no navegador do usuário, não dentro da janela.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith(`http://127.0.0.1:${port}`)) {
      e.preventDefault();
      shell.openExternal(url);
    }
  });

  win.on("resize", saveBounds);
  win.on("move", saveBounds);
  // O X: some para a bandeja ou fecha o app de verdade, conforme a configuração.
  win.on("close", (e) => {
    saveBounds();
    if (app.isQuitting || !prefs.closeToTray) return;
    e.preventDefault();
    win.hide();
  });
  win.on("closed", () => {
    mainWindow = null;
    host?.stop();
    host = null;
  });
  return win;
}

// ------------------------------------------------------------------ ponte com a interface

ipcMain.handle("forja:pickFolder", async (_e, start) => {
  const win = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0];
  const r = await dialog.showOpenDialog(win, {
    title: "Escolher pasta de trabalho",
    defaultPath: start || undefined,
    properties: ["openDirectory", "createDirectory"],
  });
  return r.canceled || !r.filePaths.length ? null : r.filePaths[0];
});

const desktopState = () => ({
  zoom: prefs.zoom,
  zoomSteps: ZOOM_STEPS,
  closeToTray: prefs.closeToTray,
  startWithWindows: prefs.startWithWindows,
  startMinimized: prefs.startMinimized,
  version: app.getVersion(),
  packaged: app.isPackaged,
  paths: { data: USER_DATA, log: LOG_FILE, db: DB_FILE, md: MD_DIR, exe: app.getPath("exe") },
});

// Síncrono de propósito: o api.ts precisa do token antes da primeira chamada, sem await.
ipcMain.on("forja:token", (e) => {
  e.returnValue = token;
});

ipcMain.handle("forja:desktop:get", () => desktopState());

ipcMain.handle("forja:desktop:set", (_e, patch = {}) => {
  if (typeof patch.zoom === "number") applyZoom(patch.zoom);
  if (typeof patch.closeToTray === "boolean") prefs.closeToTray = patch.closeToTray;
  if (typeof patch.startWithWindows === "boolean") prefs.startWithWindows = patch.startWithWindows;
  if (typeof patch.startMinimized === "boolean") prefs.startMinimized = patch.startMinimized;
  savePrefs();
  syncTray();
  syncAutoStart();
  return desktopState();
});

ipcMain.handle("forja:desktop:zoom", (_e, dir) => stepZoom(dir === "in" ? +1 : dir === "out" ? -1 : 0));

// Painel Navegador: qual conversa está à mostra e onde (px de CSS da interface); null = escondido/coberto.
ipcMain.on("forja:browser:view", (_e, shown) => host?.setShown(shown));

ipcMain.handle("forja:desktop:open", (_e, what) => {
  if (what === "log") return shell.showItemInFolder(LOG_FILE);
  if (what === "db") return shell.showItemInFolder(DB_FILE); // abre a pasta com o banco já selecionado
  if (what === "md") return shell.openPath(MD_DIR);
  if (what === "data") return shell.openPath(USER_DATA);
  return null;
});

/** Início com o Windows: quem mexe no registro é o próprio Electron. Em dev não faz nada. */
function syncAutoStart() {
  if (!app.isPackaged) return;
  app.setLoginItemSettings({
    openAtLogin: prefs.startWithWindows,
    args: prefs.startMinimized ? ["--hidden"] : [],
  });
}

// ------------------------------------------------------------------ ciclo de vida

// Uma instância só: a segunda apenas traz a janela existente para a frente.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", showWindow);

  app.whenReady().then(async () => {
    loadPrefs();
    app.setAppUserModelId("dev.forja.desktop"); // agrupamento na barra de tarefas e notificações
    syncTray();
    syncAutoStart();
    try {
      port = Number(process.env.FORJA_PORT) || (await freePort());
      startBackend(await cdpEndpoint());
      await waitForBackend();
      // --hidden: subiu junto com o Windows e fica só na bandeja até o usuário chamar.
      const hidden = process.argv.includes("--hidden") && prefs.closeToTray;
      if (hidden) ensureTray();
      createWindow({ hidden });
    } catch (e) {
      dialog.showErrorBox("Forja", `Não foi possível iniciar: ${e.message}`);
      app.quit();
    }
  });
}

// Com a bandeja ligada a janela some em vez de fechar, então isto só dispara no modo "fechar mesmo".
app.on("window-all-closed", () => {
  if (!prefs.closeToTray) app.quit();
});
app.on("activate", showWindow);
app.on("before-quit", () => {
  app.isQuitting = true;
});
app.on("will-quit", stopBackend);
process.on("exit", stopBackend);

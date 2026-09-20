/**
 * Casca do Forja Desktop: sobe o backend (FastAPI) numa porta livre de 127.0.0.1 e mostra a
 * interface numa janela. Sem Docker, sem terminal, sem porta publicada na rede.
 *
 * Empacotado, o Python e o build da UI vêm de process.resourcesPath. Em dev (npm run dev), usa o
 * venv de backend/.venv e o FORJA_WEB que o script passar.
 */
const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");

const DEV = !app.isPackaged;
const ROOT = DEV ? path.join(__dirname, "..") : process.resourcesPath;
const USER_DATA = app.getPath("userData");
const LOG_FILE = path.join(USER_DATA, "logs", "backend.log");

let backend = null;
let port = 0;

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

function startBackend() {
  fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
  const log = fs.openSync(LOG_FILE, "a");
  const env = {
    ...process.env,
    FORJA_DATA: USER_DATA,
    FORJA_WEB: process.env.FORJA_WEB ?? path.join(ROOT, "web"),
    PLAYWRIGHT_BROWSERS_PATH: path.join(ROOT, "ms-playwright"),
    PYTHONUNBUFFERED: "1",
    PYTHONUTF8: "1",
  };
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
      const r = await fetch(`http://127.0.0.1:${port}/api/config`);
      if (r.ok) return;
    } catch {
      /* ainda subindo */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`o backend não respondeu em ${timeoutMs / 1000}s. Log: ${LOG_FILE}`);
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#171717", // a interface é escura: evita o flash branco
    title: "Forja",
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false },
  });
  win.loadURL(`http://127.0.0.1:${port}`);
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
  return win;
}

ipcMain.handle("forja:pickFolder", async (_e, start) => {
  const win = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0];
  const r = await dialog.showOpenDialog(win, {
    title: "Escolher pasta de trabalho",
    defaultPath: start || undefined,
    properties: ["openDirectory", "createDirectory"],
  });
  return r.canceled || !r.filePaths.length ? null : r.filePaths[0];
});

// Uma instância só: a segunda apenas traz a janela existente para a frente.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const win = BrowserWindow.getAllWindows()[0];
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    try {
      port = Number(process.env.FORJA_PORT) || (await freePort());
      startBackend();
      await waitForBackend();
      createWindow();
    } catch (e) {
      dialog.showErrorBox("Forja", `Não foi possível iniciar: ${e.message}`);
      app.quit();
    }
  });
}

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  app.isQuitting = true;
});
app.on("will-quit", stopBackend);
process.on("exit", stopBackend);

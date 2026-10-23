/**
 * Casca do Forja Desktop: sobe o backend (FastAPI) numa porta livre de 127.0.0.1 e mostra a
 * interface numa janela. Sem Docker, sem terminal, sem porta publicada na rede.
 *
 * Empacotado, o Python e o build da UI vêm de process.resourcesPath. Em dev (npm run dev), usa o
 * venv de backend/.venv e o FORJA_WEB que o script passar.
 */
const { app, BrowserWindow, Menu, Notification, Tray, dialog, globalShortcut, ipcMain, nativeImage, powerSaveBlocker, screen, shell } = require("electron");
const { spawn, execFileSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const net = require("net");
const path = require("path");
const { ZOOM_STEPS, nextZoom } = require("./zoom");
const { BrowserHost } = require("./browserHost");
const { autoUpdater } = require("electron-updater");
const { isSafeExternal, sameOrigin } = require("./links");
const { ligarOrtografia } = require("./ortografia");

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
// PC bloqueado ou tela apagada: o Chromium do Windows dá a janela por coberta e as abas do agente param de
// desenhar (o browser_screenshot ficava esperando até o teto da ferramenta). Sem o rastreio, seguem vivas.
app.commandLine.appendSwitch("disable-features", "CalculateNativeWinOcclusion");

let backend = null;
let port = 0;
// Token desta execução: o backend só atende /api com ele no header. Fecha a porta para outro
// processo (ou outro usuário) da mesma máquina, que alcança 127.0.0.1 tão bem quanto o app.
let token = crypto.randomBytes(32).toString("hex");   // vira o do serviço sem janela, se a janela adotar um
let adotado = false;   // usando o backend do serviço sem janela (servico.py) em vez de um próprio
let subindo = true;    // até o primeiro backend responder: queda do backend aqui é tentar de novo, não diálogo
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
  manterAcordado: false, // true = o PC não dorme com o Forja aberto (o celular alcança a qualquer hora)
  overlayAssistente: true, // false = o círculo do assistente no canto da tela não aparece (jogo em tela cheia)
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

/**
 * Perfis do navegador integrado que versões anteriores gravaram em disco.
 *
 * Até a 0.3.0 cada conversa usava uma partição `persist:`, então cookie e storage de tudo o que o
 * agente abriu ficaram em userData/Partitions — e desinstalar não apaga %APPDATA%. Hoje a partição
 * é em memória; isto varre o que ficou para trás, uma vez.
 */
function dropOldBrowserProfiles() {
  const dir = path.join(USER_DATA, "Partitions");
  try {
    for (const nome of fs.readdirSync(dir)) {
      if (nome.startsWith("forja-browser-")) fs.rmSync(path.join(dir, nome), { recursive: true, force: true });
    }
  } catch {
    /* pasta não existe (instalação nova) ou arquivo em uso: não vale segurar a abertura por isso */
  }
}

function iconPath() {
  return [path.join(ROOT, "icon.png"), path.join(ROOT, "build", "icon.png")].find((p) => fs.existsSync(p)) ?? null;
}

// ------------------------------------------------------------------ backend

function pythonExe() {
  const packaged = path.join(ROOT, "python", "python.exe");
  if (fs.existsSync(packaged)) return packaged;
  // Dev: o Python portátil de resources/ (npm run prepare-resources) vem antes do venv. Um venv sobre o
  // Python da Microsoft Store tem as escritas em %APPDATA% virtualizadas para LocalCache: o backend
  // passa a ver um forja.db de lá misturado com o -wal da pasta real, e o SQLite acusa
  // "database disk image is malformed" no startup.
  const portable = path.join(ROOT, "resources", "python", "python.exe");
  if (fs.existsSync(portable)) return portable;
  const venv = path.join(ROOT, "backend", ".venv", "Scripts", "python.exe"); // dev no Windows
  return fs.existsSync(venv) ? venv : path.join(ROOT, "backend", ".venv", "bin", "python");
}

function backendDir() {
  const packaged = path.join(ROOT, "backend");
  return fs.existsSync(path.join(packaged, "app")) ? packaged : path.join(ROOT, "backend");
}

const PORTA_PADRAO = 47810;

/** Porta livre: tenta `pref` e, ocupada, pede 0 ao sistema e devolve a que ele deu. */
function freePort(pref = 0) {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once("error", (e) => (pref ? freePort().then(resolve, reject) : reject(e)));
    srv.listen(pref, "127.0.0.1", () => {
      const p = srv.address().port;
      srv.close(() => resolve(p));
    });
  });
}

/**
 * Endpoint CDP deste Electron (http://127.0.0.1:PORTA). Vazio se o Chromium não escreveu a tempo.
 *
 * O arquivo fica para trás quando o app morre sujo, e a primeira leitura devolvia a porta da
 * execução ANTERIOR — o backend ligava num endpoint morto (ou, pior, no Chromium de outra coisa).
 * Por isso ele é apagado antes de esperar: o que vier depois é desta execução.
 */
async function cdpEndpoint() {
  // O Chromium escreve este arquivo UMA vez, antes do `ready`; apagá-lo aqui deixaria o app sem CDP
  // (e o backend cairia no espelho headless sem avisar). Contra arquivo velho de uma execução que
  // morreu suja, a defesa é outra: a 2ª linha traz o GUID do browser, e /json/version da porta tem
  // de devolver o mesmo GUID — um endpoint morto não responde, e outro Chromium responde outro GUID.
  const file = path.join(USER_DATA, "DevToolsActivePort");
  for (let i = 0; i < 30; i++) {
    try {
      const [porta, guid] = fs.readFileSync(file, "utf8").split(/\r?\n/);
      const p = Number(porta);
      if (p > 0) {
        const r = await fetch(`http://127.0.0.1:${p}/json/version`, { signal: AbortSignal.timeout(1000) });
        const ws = (await r.json()).webSocketDebuggerUrl ?? "";
        if (guid && ws.endsWith(guid)) return `http://127.0.0.1:${p}`;
      }
    } catch {
      /* ainda não escreveu, ou porta de outra execução: espera o arquivo desta */
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  return "";
}

function startBackend(cdp) {
  fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
  // O log só crescia: passou de 10 MB, vira backend.log.1 (o anterior .1 some) e este recomeça.
  try {
    if (fs.statSync(LOG_FILE).size > 10 * 1024 * 1024) fs.renameSync(LOG_FILE, LOG_FILE + ".1");
  } catch {}
  const log = fs.openSync(LOG_FILE, "a");
  const env = {
    ...process.env,
    FORJA_DATA: USER_DATA,
    FORJA_TOKEN: token,
    FORJA_PORT: String(port), // o servidor MCP grava este endereço em mcp_endpoint.json (sem ele, 8765)
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
  // Sem log de acesso: a URL de <img> do celular leva o token em ?t=, e cada caminho de arquivo aberto
  // ficava gravado no backend.log.
  backend = spawn(pythonExe(), ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port), "--no-access-log"], {
    cwd: backendDir(),
    env,
    stdio: ["ignore", log, log],
    windowsHide: true,
  });
  // Sem este listener, um Python que não existe vira exceção não tratada e derruba o processo
  // principal sem diálogo nenhum — o usuário só vê a janela não abrir.
  backend.on("error", (err) => {
    backend = null;
    if (app.isQuitting) return;
    dialog.showErrorBox("Forja", `Não consegui iniciar o backend (${err.message}).\n\nLog: ${LOG_FILE}`);
    app.quit();
  });
  backend.on("exit", (code) => {
    backend = null;
    // Subindo, quem decide é o subirBackend (tenta de novo: no boot o serviço pode estar com a trava do banco).
    if (code !== 0 && !app.isQuitting && !subindo) {
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

// PC recém-ligado (disco frio, antivírus varrendo o Python) passava dos 60 s.
async function waitForBackend(timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!backend && !adotado) throw new Error(`o backend não subiu. Log: ${LOG_FILE}`);
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

/** O serviço sem janela (tarefa "Forja Automatico", sobe com o Windows antes do login) pode estar no ar com os
 *  mesmos dados. No meio de uma automação, a janela usa ele (devolve {port, token}); ocioso, pede para ele sair e
 *  a janela sobe o seu backend, com tudo (o navegador nativo precisa do CDP desta janela). Nunca os dois juntos. */
async function servicoSemJanela() {
  let info;
  try {
    info = JSON.parse(fs.readFileSync(path.join(USER_DATA, "servico.json"), "utf8"));
  } catch {
    return null;
  }
  const base = `http://127.0.0.1:${info.port}`;
  const headers = { "x-forja-token": info.token };
  try {
    const st = await (await fetch(`${base}/api/servico`, { headers, signal: AbortSignal.timeout(3000) })).json();
    if (!st.servico) return null;
    if (st.ocupado) return { port: info.port, token: info.token };
    const r = await fetch(`${base}/api/servico/sair`, { method: "POST", headers });
    if (r.status === 409) return { port: info.port, token: info.token };   // começou algo agora há pouco
  } catch {
    return null;   // arquivo velho de um serviço que já morreu
  }
  for (let i = 0; i < 150; i++) {   // até 30 s para descarregar o modelo e soltar a trava do banco
    await new Promise((r) => setTimeout(r, 200));
    try {
      await fetch(`${base}/api/servico`, { headers, signal: AbortSignal.timeout(500) });
    } catch {
      await new Promise((r) => setTimeout(r, 800));
      return null;
    }
  }
  return { port: info.port, token: info.token };   // não saiu: melhor usar ele do que brigar pelo banco
}

/** Adota o serviço sem janela ou sobe o backend da janela. Com o PC recém-ligado o serviço (tarefa "Forja
 *  Automatico", sobe antes do login) pode já ter a trava do banco sem ainda responder: o backend da janela morria
 *  com código 3 ("Outro backend do Forja já está usando..."). Então tenta de novo, e na próxima o serviço atende. */
async function subirBackend() {
  for (let tentativa = 1; ; tentativa++) {
    const servico = await servicoSemJanela();
    if (servico) {
      port = servico.port;
      token = servico.token;
      adotado = true;
    } else {
      // Porta fixa quando dá: o `tailscale serve` do celular (aba Celular) aponta para ela.
      port = Number(process.env.FORJA_PORT) || (await freePort(PORTA_PADRAO));
      startBackend(await cdpEndpoint());
    }
    try {
      await waitForBackend();
      subindo = false;
      return;
    } catch (e) {
      if (tentativa >= 10 || backend || adotado) throw e;   // só a queda rápida (trava) vale outra tentativa
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
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

const gb = (b) => (b / 2 ** 30).toLocaleString("pt-BR", { maximumFractionDigits: 1, minimumFractionDigits: 1 });

/** O pedaço "modelo e memória" do menu: o que está na VRAM, quanto cada placa e a RAM têm em uso, e descarregar. */
function itensUso(u) {
  if (!u) return [{ label: "IA local: o Forja ainda não respondeu", enabled: false }];
  const m = u.modelo;
  const itens = [
    { label: m ? `● ${m.alias} carregado` : u.carregando ? `◌ Carregando ${u.carregando.name ?? "modelo"}…` : "○ Nenhum modelo na VRAM", enabled: false },
    ...(u.gpus ?? []).map((g) => ({ label: `    ${g.nome.replace(/\(R\)|\(TM\)|Graphics/g, "").replace(/\s+/g, " ").trim()}: ${gb(g.usado)} / ${gb(g.total)} GB`, enabled: false })),
    ...(u.ram?.total ? [{ label: `    RAM: ${gb(u.ram.usado)} / ${gb(u.ram.total)} GB`, enabled: false }] : []),
  ];
  if (u.gerando_imagem) itens.push({ label: "    Gerando imagem ou vídeo agora", enabled: false });
  itens.push({ label: "Descarregar o modelo da VRAM", enabled: Boolean(m), click: descarregarModelo });
  return itens;
}

function menuBandeja(u = usoAtual) {
  return Menu.buildFromTemplate([
    { label: "Abrir o Forja", click: showWindow },
    ...(voz.instalado ? [
      { label: "Ouvir “Forja, …” (assistente de voz)", type: "checkbox", checked: voz.ligado, click: alternarVoz },
      { label: "Mostrar o círculo do assistente na tela", type: "checkbox", checked: prefs.overlayAssistente,
        click: () => { prefs.overlayAssistente = !prefs.overlayAssistente; savePrefs(); if (!prefs.overlayAssistente) mostraBolha(""); } },
      { label: "Chamar o assistente agora", accelerator: atalho.ok ? atalho.atual : undefined, click: chamarAssistente },
    ] : []),
    { type: "separator" },
    ...itensUso(u),
    { type: "separator" },
    {
      label: "Sair",
      click: () => {
        app.isQuitting = true;
        app.quit();
      },
    },
  ]);
}

let iconeBase = null; // 16x16 do Forja; com modelo na VRAM ganha um ponto verde no canto
let usoAtual = null;   // o último /api/local/uso: o menu abre na hora, sem esperar o backend

function iconeBandeja(carregado) {
  if (!iconeBase) {
    const icon = iconPath();
    iconeBase = icon ? nativeImage.createFromPath(icon).resize({ width: 16, height: 16 }) : nativeImage.createEmpty();
  }
  if (!carregado || iconeBase.isEmpty()) return iconeBase;
  const { width: w, height: h } = iconeBase.getSize();
  const px = Buffer.from(iconeBase.toBitmap()); // BGRA
  for (let y = h - 7; y < h; y++) {
    for (let x = w - 7; x < w; x++) {
      const d = Math.hypot(x - (w - 3.5), y - (h - 3.5));
      if (d > 3.5) continue;
      const i = (y * w + x) * 4;
      const borda = d > 2.6; // anel escuro: o ponto aparece em barra de tarefas clara e escura
      px[i] = borda ? 20 : 80; px[i + 1] = borda ? 20 : 200; px[i + 2] = borda ? 20 : 60; px[i + 3] = 255;
    }
  }
  return nativeImage.createFromBitmap(px, { width: w, height: h });
}

function ensureTray() {
  if (tray) return;
  tray = new Tray(iconeBandeja(false));
  tray.on("click", showWindow);
  // o menu é montado na hora do clique: modelo, VRAM e RAM de agora (setContextMenu congelaria o de quando montou)
  tray.on("right-click", async () => {
    await vigiaUso();
    tray?.popUpContextMenu(menuBandeja());
  });
}

async function vigiaUso() {
  if (!port || !tray) return;
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/local/uso`, { headers: { "x-forja-token": token }, signal: AbortSignal.timeout(2500) });
    usoAtual = await r.json();
  } catch {
    return; // backend reiniciando: fica o último
  }
  const m = usoAtual?.modelo;
  const gpu = (usoAtual?.gpus ?? []).reduce((a, g) => (!a || g.usado > a.usado ? g : a), null);
  tray.setImage(iconeBandeja(Boolean(m)));
  tray.setToolTip([voz.ligado ? "Forja · ouvindo “Forja, …”" : "Forja",
    m ? `${m.alias} na VRAM` : "Nenhum modelo na VRAM",
    gpu ? `VRAM ${gb(gpu.usado)}/${gb(gpu.total)} GB · RAM ${gb(usoAtual.ram.usado)}/${gb(usoAtual.ram.total)} GB` : ""].filter(Boolean).join("\n"));
}

async function descarregarModelo() {
  try {
    await fetch(`http://127.0.0.1:${port}/api/local/unload`, { method: "POST", headers: { "x-forja-token": token } });
  } catch (err) {
    dialog.showErrorBox("Descarregar o modelo", String(err));
  }
  await vigiaUso();
}

function dropTray() {
  if (!tray) return;
  tray.destroy();
  tray = null;
}

/** O ícone da bandeja só existe quando serve para alguma coisa: fechar-para-bandeja ligado, ou o assistente de voz
 *  instalado (o liga/desliga dele mora no menu). */
function syncTray() {
  if (prefs.closeToTray || voz.instalado) ensureTray();
  else dropTray();
  if (!tray) return;
  vigiaUso();
}

// ------------------------------------------------------------------ assistente de voz (bandeja)

let voz = { instalado: false, ligado: false };
// Atalho global (configurado na página F.O.R.J.A): funciona com o Forja minimizado, na bandeja ou atrás de outra
// janela. `erro`: outro programa já usa a combinação, ou ela não é válida; a página mostra.
let atalho = { atual: "", ok: false, erro: "" };

function registraAtalho(accel) {
  if (accel === atalho.atual) return;
  if (atalho.ok) globalShortcut.unregister(atalho.atual);
  atalho = { atual: accel, ok: false, erro: "" };
  if (accel) {
    try {
      atalho.ok = globalShortcut.register(accel, chamarAssistente);
      if (!atalho.ok) atalho.erro = "Outro programa já usa esta combinação. Escolha outra.";
    } catch (err) {
      atalho.erro = `Combinação inválida: ${err.message}`;
    }
  }
  syncTray();
}

async function chamarAssistente() {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/assistente/chamar`, { method: "POST", headers: { "x-forja-token": token } });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      new Notification({ title: "Assistente F.O.R.J.A", body: e.detail || `HTTP ${r.status}` }).show();
    }
    vigiaVoz();
  } catch (err) {
    new Notification({ title: "Assistente F.O.R.J.A", body: `O Forja não respondeu: ${err.message}` }).show();
  }
}

ipcMain.handle("forja:atalho:status", () => atalho);

// A página do assistente aberta mostra o círculo grande: com a janela à vista, o do canto da tela sobraria.
let paginaAssistente = false;
ipcMain.on("forja:assistente:visivel", (_e, visivel) => { paginaAssistente = Boolean(visivel); });

/** Ligado no trilho da janela ou pelo backend: o checkbox da bandeja acompanha, e o atalho segue a configuração. */
async function vigiaVoz() {
  if (!port) return;
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/assistente`, { headers: { "x-forja-token": token } });
    const e = await r.json();
    registraAtalho(e.instalado ? String(e.atalho || "") : "");
    if (Boolean(e.instalado) !== voz.instalado || Boolean(e.ligado) !== voz.ligado) {
      voz = { instalado: Boolean(e.instalado), ligado: Boolean(e.ligado) };
      syncTray();
    }
  } catch { /* backend reiniciando: tenta no próximo tique */ }
}

// Círculo fluido no canto inferior direito da TELA (não da janela): pulsa enquanto o assistente fala, mais devagar
// enquanto pensa. Janela transparente, sempre por cima, que não pega foco nem clique.
const BOLHA_HTML = `<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:transparent;overflow:hidden}
#b{position:absolute;inset:22px;opacity:0;transform:scale(.55);transition:opacity .35s ease,transform .45s cubic-bezier(.22,1,.36,1)}
#b.on{opacity:1;transform:scale(1)}
.g{position:absolute;inset:0;mix-blend-mode:screen;animation:forma 6s linear infinite}
.g1{background:radial-gradient(circle at 35% 35%,#bfe3ff,#3b82f6 52%,#1e3a8a00 72%)}
.g2{background:radial-gradient(circle at 65% 62%,#d9c7ff,#7c3aed 52%,#4c1d9500 72%);animation-duration:8s;animation-direction:reverse}
.g3{background:radial-gradient(circle at 50% 45%,#a5fff0,#06b6d4 48%,#0e749000 70%);animation-duration:4.6s;opacity:.85}
.halo{position:absolute;inset:-14px;border-radius:50%;background:radial-gradient(circle,#60a5fa66,#60a5fa00 68%);animation:halo 1.1s ease-in-out infinite}
@keyframes forma{0%{border-radius:42% 58% 63% 37%/45% 41% 59% 55%;rotate:0deg}33%{border-radius:58% 42% 38% 62%/60% 52% 48% 40%}
66%{border-radius:38% 62% 52% 48%/42% 61% 39% 58%}100%{border-radius:42% 58% 63% 37%/45% 41% 59% 55%;rotate:360deg}}
@keyframes halo{0%,100%{scale:.9;opacity:.55}50%{scale:1.14;opacity:1}}
@keyframes fala{0%,100%{scale:1}30%{scale:1.09}60%{scale:.97}}
#b.falando{animation:fala .85s ease-in-out infinite}
#b.pensando{filter:saturate(.55) brightness(.8)}
.anel{position:absolute;inset:0;border-radius:50%;border:2px solid #5eead4;opacity:0}
#b.escutando{filter:hue-rotate(-38deg) saturate(1.25)}
#b.escutando .anel{animation:anel 1.5s cubic-bezier(.22,1,.36,1) infinite}
#b.escutando .a2{animation-delay:.5s}#b.escutando .a3{animation-delay:1s}
@keyframes anel{0%{scale:.62;opacity:.9}100%{scale:1.32;opacity:0}}

@media (prefers-reduced-motion:reduce){.g,.halo,.anel,#b.falando{animation:none}}
</style><div id="b"><div class="anel"></div><div class="anel a2"></div><div class="anel a3"></div><div class="halo"></div><div class="g g1"></div><div class="g g2"></div><div class="g g3"></div></div>
<script>/* velocidade pelo playbackRate, que guarda a fase: trocar animation-duration fazia o círculo pular */
function modo(m){const b=document.getElementById("b");b.className=m?"on "+m:"";const v={escutando:2,pensando:.43}[m]||1;
b.querySelectorAll(".g,.halo").forEach(el=>el.getAnimations().forEach(a=>a.updatePlaybackRate(el.classList.contains("halo")?(m==="pensando"?1.15:1):v)))}</script>`;
let bolha = null;
let bolhaModo = "";

function criaBolha() {
  const { workArea } = screen.getPrimaryDisplay();
  const lado = 132;
  bolha = new BrowserWindow({
    width: lado, height: lado, x: workArea.x + workArea.width - lado - 16, y: workArea.y + workArea.height - lado - 16,
    frame: false, transparent: true, resizable: false, movable: false, focusable: false, skipTaskbar: true,
    alwaysOnTop: true, show: false, hasShadow: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  bolha.setAlwaysOnTop(true, "screen-saver");
  bolha.setIgnoreMouseEvents(true);
  bolha.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(BOLHA_HTML));
  bolha.on("closed", () => { bolha = null; });
}

function aplicaBolha() {
  const js = `modo(${JSON.stringify(bolhaModo)})`;
  if (bolha.webContents.isLoading()) bolha.webContents.once("did-finish-load", () => bolha?.webContents.executeJavaScript(js));
  else bolha.webContents.executeJavaScript(js).catch(() => {});
}

function mostraBolha(modo) {
  if (modo === bolhaModo) return;
  bolhaModo = modo;
  if (modo) {
    if (!bolha) criaBolha();
    bolha.showInactive();
    aplicaBolha();
  } else if (bolha) {
    // entre uma frase e outra o ouvido passa um instante por "ouvindo": só some se continuar parado
    setTimeout(() => {
      if (bolhaModo || !bolha) return;
      aplicaBolha();
      setTimeout(() => { if (!bolhaModo && bolha) bolha.hide(); }, 450);
    }, 700);
  }
}

async function vigiaBolha() {
  if (!voz.ligado || !port) return mostraBolha("");
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/assistente/estado`, { headers: { "x-forja-token": token } });
    const e = await r.json();
    if (Boolean(e.ligado) !== voz.ligado) vigiaVoz();
        // escutando: ouviu "Forja" (ou o atalho) e grava o pedido; entendendo/pensando: Whisper e modelo; falando: a voz
    const modo = { escutando: "escutando", entendendo: "pensando", pensando: "pensando", falando: "falando" }[e.estado] || "";
    const naTela = paginaAssistente && mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible() && !mainWindow.isMinimized();
    mostraBolha(naTela || !prefs.overlayAssistente ? "" : modo);
  } catch { /* backend reiniciando */ }
}

async function alternarVoz() {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/assistente/ligar`, {
      method: "POST", headers: { "x-forja-token": token, "content-type": "application/json" },
      body: JSON.stringify({ ligado: !voz.ligado }),
    });
    const e = await r.json();
    if (r.ok) voz = { instalado: Boolean(e.instalado), ligado: Boolean(e.ligado) };
    else dialog.showErrorBox("Assistente de voz", e.detail || `HTTP ${r.status}`);
  } catch (err) {
    dialog.showErrorBox("Assistente de voz", String(err));
  }
  syncTray();
}

// ------------------------------------------------------------------ janela

/**
 * Guarda a geometria da janela, no máximo uma vez a cada 300 ms.
 *
 * `resize` e `move` disparam a cada quadro: arrastar a janela virava centenas de writeFileSync
 * síncronos na thread da interface.
 */
let boundsTimer = null;
function saveBoundsLater() {
  if (boundsTimer) return;
  boundsTimer = setTimeout(() => {
    boundsTimer = null;
    saveBounds();
  }, 300);
}

function saveBounds() {
  if (boundsTimer) {
    clearTimeout(boundsTimer);
    boundsTimer = null;
  }
  // Minimizada ou escondida (início com o Windows) a geometria não é a da pessoa: gravaria maximized: false.
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.isMinimized() || !mainWindow.isVisible()) return;
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
    // Sem a barra de título do sistema, como no Claude Desktop: o cabeçalho do próprio app arrasta a
    // janela (-webkit-app-region no CSS) e os botões nativos de minimizar/maximizar/fechar ficam
    // desenhados no canto dele, na mesma cor. A altura é a do cabeçalho (h-12 = 48px).
    ...(process.platform === "darwin"
      ? { titleBarStyle: "hiddenInset" }
      : { titleBarStyle: "hidden", titleBarOverlay: { color: "#171717", symbolColor: "#a3a3a3", height: 48 } }),
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false,
                      backgroundThrottling: false }, // escondida/bloqueada, a tela segue acompanhando o turno do celular
  });
  mainWindow = win;
  if (!hidden) {
    if (b.maximized) win.maximize();
    win.show();
  } else {
    // No Windows maximize() MOSTRA a janela: era por isso que ela abria mesmo com "iniciar na bandeja".
    // Maximiza só quando a pessoa abrir. O minimize() vem antes: ele mesmo dispara "show".
    if (hidden === "min") win.minimize();
    if (b.maximized) {
      const max = () => {
        win.off("show", max);
        win.off("restore", max);
        setTimeout(() => win.isDestroyed() || win.maximize(), 0);   // dentro do próprio restore o Windows ignora
      };
      win.on("show", max);
      win.on("restore", max);
    }
  }

  win.loadURL(`http://127.0.0.1:${port}`);
  wireZoomShortcuts(win);
  ligarOrtografia(win);   // corretor em pt-BR e o menu do botão direito (sugestões, dicionário, editar)
  host = new BrowserHost(win, `http://127.0.0.1:${port}`, token);
  host.start();

  // Links para fora do app abrem no navegador do usuário, não dentro da janela.
  const nossa = `http://127.0.0.1:${port}`;
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternal(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (sameOrigin(url, nossa)) return;
    e.preventDefault();
    if (isSafeExternal(url)) shell.openExternal(url);
  });

  win.on("resize", saveBoundsLater);
  win.on("move", saveBoundsLater);
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

// ------------------------------------------------------------------ ícone do tipo de arquivo

/**
 * Ícone que o Windows usa para aquele tipo de arquivo — o do programa padrão, o mesmo que aparece
 * no Explorer. É assim que um .docx no chat fica com a cara do Word.
 *
 * `app.getFileIcon` exige um arquivo que exista, e nós queremos o ícone da EXTENSÃO, não de um
 * arquivo específico: um vazio no temp resolve, e o resultado fica em cache — a associação não
 * muda no meio da sessão, e a lista do chat pediria o mesmo ícone dezenas de vezes.
 */
const icones = new Map();

async function iconeDe(ext) {
  const limpa = String(ext || "").toLowerCase().replace(/[^a-z0-9.]/g, "");
  if (!limpa.startsWith(".") || limpa.length > 8) return "";
  if (icones.has(limpa)) return icones.get(limpa);
  let url = "";
  try {
    const pasta = path.join(app.getPath("temp"), "forja-icones");
    fs.mkdirSync(pasta, { recursive: true });
    const molde = path.join(pasta, "molde" + limpa);
    if (!fs.existsSync(molde)) fs.writeFileSync(molde, "");
    const img = await app.getFileIcon(molde, { size: "normal" });
    url = img.isEmpty() ? "" : img.toDataURL();
  } catch {
    /* sem associação para essa extensão, ou o Windows recusou: a interface usa o desenho dela */
  }
  icones.set(limpa, url);
  return url;
}

ipcMain.handle("forja:icon", (_e, ext) => iconeDe(ext));

// ------------------------------------------------------------------ atualização

/**
 * Atualização pelo GitHub Releases, sem nada acontecendo às escondidas.
 *
 * O instalador tem ~245 MB por causa do Python e do Chromium embutidos, e é justamente por isso
 * que o `.blockmap` importa: com ele o electron-updater baixa só os blocos que mudaram, então uma
 * versão que mexeu no backend e na interface custa dezenas de MB, não 245.
 *
 * `autoDownload` desligado de propósito: quem decide baixar é quem está pagando a internet. E o
 * sha512 do latest.yml é conferido antes de instalar — o que ele não cobre é o SmartScreen, que
 * segue avisando "editor desconhecido" enquanto o instalador não for assinado.
 */
let update = { state: "idle", version: "", percent: 0, error: "", notes: "" };

function setUpdate(patch) {
  update = { ...update, ...patch };
}

function wireUpdater() {
  if (!app.isPackaged) {
    // Em dev não há release para comparar. FORJA_FAKE_UPDATE=<notas.md> finge uma pronta, para ver
    // o aviso com notas de verdade sem publicar nada.
    const notas = process.env.FORJA_FAKE_UPDATE;
    if (notas) setUpdate({ state: "ready", version: "0.0.0-dev", notes: fs.readFileSync(notas, "utf8") });
    return;
  }
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.on("checking-for-update", () => setUpdate({ state: "checking", error: "" }));
  autoUpdater.on("update-available", (info) => setUpdate({ state: "available", version: info.version, notes: String(info.releaseNotes ?? "") }));
  autoUpdater.on("update-not-available", () => setUpdate({ state: "current", version: "", percent: 0 }));
  autoUpdater.on("download-progress", (p) => setUpdate({ state: "downloading", percent: Math.round(p.percent) }));
  autoUpdater.on("update-downloaded", (info) => setUpdate({ state: "ready", version: info.version, percent: 100 }));
  autoUpdater.on("error", (e) => setUpdate({ state: "error", error: String(e?.message ?? e) }));
  autoUpdater.checkForUpdates().catch(() => {
    /* sem rede na abertura: o botão em Configurações tenta de novo */
  });
}

ipcMain.handle("forja:update:get", () => update);

ipcMain.handle("forja:update:check", async () => {
  if (!app.isPackaged) return setUpdate({ state: "dev" }), update;
  try {
    await autoUpdater.checkForUpdates();
  } catch (e) {
    setUpdate({ state: "error", error: String(e?.message ?? e) });
  }
  return update;
});

ipcMain.handle("forja:update:download", async () => {
  try {
    setUpdate({ state: "downloading", percent: 0 });
    await autoUpdater.downloadUpdate();
  } catch (e) {
    setUpdate({ state: "error", error: String(e?.message ?? e) });
  }
  return update;
});

ipcMain.handle("forja:update:install", () => {
  if (update.state !== "ready") return update;
  app.isQuitting = true;
  stopBackend(); // o instalador não pode encontrar o Python segurando arquivo
  autoUpdater.quitAndInstall(false, true);
  return update;
});

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
  manterAcordado: prefs.manterAcordado,
  overlayAssistente: prefs.overlayAssistente,
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
  if (typeof patch.manterAcordado === "boolean") prefs.manterAcordado = patch.manterAcordado;
  if (typeof patch.overlayAssistente === "boolean") prefs.overlayAssistente = patch.overlayAssistente;
  savePrefs();
  vigiaSono();
  syncTray();
  syncAutoStart();
  return desktopState();
});

ipcMain.handle("forja:desktop:zoom", (_e, dir) => stepZoom(dir === "in" ? +1 : dir === "out" ? -1 : 0));

// Painel Navegador: qual conversa está à mostra e onde (px de CSS da interface); null = escondido/coberto.
ipcMain.on("forja:browser:view", (_e, shown) => host?.setShown(shown));
ipcMain.on("forja:focus", () => showWindow());

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
    dropOldBrowserProfiles();
    app.setAppUserModelId("dev.forja.desktop"); // agrupamento na barra de tarefas e notificações
    syncTray();
    syncAutoStart();
    try {
      await subirBackend();
      // --hidden: subiu junto com o Windows. Com a bandeja, fica só nela; sem, minimizada na barra de tarefas
      // (escondida sem bandeja não haveria como abrir).
      const hidden = process.argv.includes("--hidden") ? (prefs.closeToTray ? "tray" : "min") : false;
      if (hidden === "tray") ensureTray();
      createWindow({ hidden });
      wireUpdater();
      vigiaSono();
      setInterval(vigiaSono, 30_000);
      vigiaVoz();
      setInterval(vigiaVoz, 5_000);
      setInterval(vigiaBolha, 250);
setInterval(vigiaUso, 10_000); // ícone e dica da bandeja: modelo na VRAM, VRAM e RAM
    } catch (e) {
      dialog.showErrorBox("Forja", `Não foi possível iniciar: ${e.message}`);
      app.quit();
    }
  });
}

// ------------------------------------------------------------------ sono do Windows

// O Windows conta ociosidade pelo teclado e mouse, não pela rede: com o PC bloqueado ele dormia no meio de um
// turno pedido pelo celular, e dormindo nada responde. Segura o sono (a tela pode apagar) enquanto houver
// turno rodando, ou sempre, se a pessoa ligou "manter acordado".
let bloqueioSono = null;
async function vigiaSono() {
  let precisa = prefs.manterAcordado;
  if (!precisa && port) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/activity`, { headers: { "x-forja-token": token } });
      const atividade = await r.json();
      // `acordado`: automação da tela Conteúdo marcada para mais tarde (o vídeo das 3h não sai com o PC dormindo)
      precisa = atividade.conversations.some((c) => c.running) || Boolean(atividade.acordado);
    } catch {
      precisa = bloqueioSono !== null; // backend sem responder agora: mantém como estava
    }
  }
  if (precisa && bloqueioSono === null) bloqueioSono = powerSaveBlocker.start("prevent-app-suspension");
  else if (!precisa && bloqueioSono !== null) {
    powerSaveBlocker.stop(bloqueioSono);
    bloqueioSono = null;
  }
}

// Com a bandeja ligada a janela some em vez de fechar, então isto só dispara no modo "fechar mesmo".
app.on("window-all-closed", () => {
  if (!prefs.closeToTray) app.quit();
});
app.on("activate", showWindow);
app.on("before-quit", () => {
  app.isQuitting = true;
});
app.on("will-quit", () => { globalShortcut.unregisterAll(); stopBackend(); });
process.on("exit", stopBackend);
// `exit` não roda em SIGINT/SIGTERM: sem estes, o Ctrl+C do `npm run dev` deixava uvicorn, o
// Chromium do navegador integrado, o llama-server e todo serve_start rodando.
for (const sinal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sinal, () => {
    app.isQuitting = true;
    stopBackend();
    process.exit(0);
  });
}

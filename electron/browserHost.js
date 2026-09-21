/**
 * Navegador integrado nativo: uma WebContentsView por aba, dentro da janela, no lugar do espelho.
 *
 * Quem decide abas, navegação e fechamento é o backend (Playwright ligado por CDP ao Chromium deste
 * Electron). Aqui as views só nascem, aparecem e somem:
 *
 * - `create {key, marker}`: cria a view carregando a URL-marcador `about:blank?forja=CHAVE/ID`; o backend
 *   acha a page por essa URL e assume dali em diante (goto, close...). Fechar a page destrói a view.
 * - `active {key, marker}`: qual view da conversa `key` está ativa (a única que pode ficar visível).
 * - A interface diz por IPC qual conversa o painel mostra e onde ele está (`setShown`).
 *
 * Self-check: node electron/browserHost.js
 */
const { WebContentsView, session } = require("electron");

/** Retângulo da view em DIP da janela a partir do retângulo do painel em px de CSS e do zoom da UI. */
function viewBounds(b, zoom) {
  const r = (v) => Math.round(v * zoom);
  return { x: r(b.x), y: r(b.y), width: Math.max(0, r(b.width)), height: Math.max(0, r(b.height)) };
}

class BrowserHost {
  constructor(win, api, token) {
    this.win = win;
    this.api = api; // http://127.0.0.1:PORTA
    this.headers = { "x-forja-token": token }; // a API local só atende com ele
    this.views = new Map(); // marker -> { view, key }
    this.active = new Map(); // key -> marker
    this.shown = null; // { key, bounds } do painel, ou null (painel fechado / coberto)
    this.partitions = new Set();
    this.stopped = false;
    this.ctl = null;
  }

  start() {
    this.loop();
  }

  stop() {
    this.stopped = true;
    this.ctl?.abort();
    this.reset();
  }

  /** Assina /api/browser/host e reconecta se cair. A cada (re)conexão zera as views: o backend recria. */
  async loop() {
    while (!this.stopped) {
      this.ctl = new AbortController();
      try {
        const r = await fetch(`${this.api}/api/browser/host`, { signal: this.ctl.signal, headers: this.headers });
        if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
        this.reset();
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const line = buf.slice(0, i);
            buf = buf.slice(i + 2);
            if (line.startsWith("data: ")) this.handle(JSON.parse(line.slice(6)));
          }
        }
      } catch {
        /* backend ainda subindo ou caiu: tenta de novo */
      }
      if (!this.stopped) await new Promise((r) => setTimeout(r, 1000));
    }
  }

  handle(ev) {
    if (ev.type === "create") this.create(ev.key, ev.marker);
    else if (ev.type === "active") {
      if (ev.marker) this.active.set(ev.key, ev.marker);
      else this.active.delete(ev.key);
      this.layout();
    }
  }

  create(key, marker) {
    if (this.views.has(marker) || this.win.isDestroyed()) return;
    const partition = `persist:forja-browser-${key}`; // cookies e storage por conversa, como no espelho
    if (!this.partitions.has(partition)) {
      this.partitions.add(partition);
      session.fromPartition(partition).on("will-download", (e) => e.preventDefault()); // sem downloads
    }
    const view = new WebContentsView({
      webPreferences: { partition, sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false },
    });
    const wc = view.webContents;
    // window.open / target=_blank: vira aba da mesma sessão, criada pelo backend.
    wc.setWindowOpenHandler(({ url }) => {
      this.popup(key, url);
      return { action: "deny" };
    });
    // Link clicado para file:, chrome: etc.: o backend já bloqueia no goto; aqui é o clique do usuário.
    wc.on("will-navigate", (e, url) => {
      if (!/^https?:/i.test(url)) e.preventDefault();
    });
    wc.on("destroyed", () => {
      this.views.delete(marker);
      try {
        if (!this.win.isDestroyed()) this.win.contentView.removeChildView(view);
      } catch {
        /* já removida */
      }
    });
    this.views.set(marker, { view, key });
    view.setVisible(false);
    this.win.contentView.addChildView(view);
    wc.loadURL(marker);
    this.layout();
  }

  popup(key, url) {
    fetch(`${this.api}/api/browser/host/popup?conv=${encodeURIComponent(key)}`, {
      method: "POST",
      headers: { "content-type": "application/json", ...this.headers },
      body: JSON.stringify({ url }),
    }).catch(() => {});
  }

  /** A interface: painel Navegador da conversa `key` ocupa `bounds` (px de CSS), ou null se não está visível. */
  setShown(shown) {
    this.shown = shown && shown.bounds ? { key: String(shown.key), bounds: shown.bounds } : null;
    this.layout();
  }

  layout() {
    if (this.win.isDestroyed()) return;
    const zoom = this.win.webContents.getZoomFactor();
    for (const [marker, { view, key }] of this.views) {
      const on = !!this.shown && this.shown.key === key && this.active.get(key) === marker;
      if (on) view.setBounds(viewBounds(this.shown.bounds, zoom));
      view.setVisible(on);
    }
  }

  reset() {
    for (const { view } of this.views.values()) {
      try {
        view.webContents.close();
      } catch {
        /* já fechada */
      }
    }
    this.views.clear();
    this.active.clear();
  }
}

module.exports = { BrowserHost, viewBounds };

if (require.main === module) {
  const assert = require("node:assert/strict");
  assert.deepEqual(viewBounds({ x: 10, y: 20.4, width: 300, height: 200 }, 1), { x: 10, y: 20, width: 300, height: 200 });
  assert.deepEqual(viewBounds({ x: 10, y: 20, width: 300, height: 200 }, 1.25), { x: 13, y: 25, width: 375, height: 250 });
  assert.deepEqual(viewBounds({ x: 0, y: 0, width: -5, height: 0 }, 1), { x: 0, y: 0, width: 0, height: 0 });
  console.log("browserHost ok");
}

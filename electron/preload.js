/** Ponte entre a interface e o sistema: diálogo de pasta e as preferências da casca. O resto vai pela API do backend. */
const { contextBridge, ipcRenderer } = require("electron");

// O token vem síncrono: o api.ts precisa dele na primeira chamada, antes de qualquer await.
const token = ipcRenderer.sendSync("forja:token");

contextBridge.exposeInMainWorld("forja", {
  token,
  pickFolder: (start) => ipcRenderer.invoke("forja:pickFolder", start),
  desktop: {
    get: () => ipcRenderer.invoke("forja:desktop:get"),
    set: (patch) => ipcRenderer.invoke("forja:desktop:set", patch),
    zoom: (dir) => ipcRenderer.invoke("forja:desktop:zoom", dir),
    open: (what) => ipcRenderer.invoke("forja:desktop:open", what),
  },
  // Atualização: a tela consulta o estado enquanto estiver aberta (ver Settings › Aplicativo).
  update: {
    get: () => ipcRenderer.invoke("forja:update:get"),
    check: () => ipcRenderer.invoke("forja:update:check"),
    download: () => ipcRenderer.invoke("forja:update:download"),
    install: () => ipcRenderer.invoke("forja:update:install"),
  },
  /** Ícone do programa padrão do sistema para uma extensão (".docx"), como data URL. */
  icon: (ext) => ipcRenderer.invoke("forja:icon", ext),
  browser: {
    // Onde o painel Navegador está (px de CSS) e de qual conversa; bounds null = não mostrar view nenhuma.
    view: (shown) => ipcRenderer.send("forja:browser:view", shown),
  },
});

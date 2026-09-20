/** Ponte entre a interface e o sistema: diálogo de pasta e as preferências da casca. O resto vai pela API do backend. */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("forja", {
  pickFolder: (start) => ipcRenderer.invoke("forja:pickFolder", start),
  desktop: {
    get: () => ipcRenderer.invoke("forja:desktop:get"),
    set: (patch) => ipcRenderer.invoke("forja:desktop:set", patch),
    zoom: (dir) => ipcRenderer.invoke("forja:desktop:zoom", dir),
    open: (what) => ipcRenderer.invoke("forja:desktop:open", what),
  },
  browser: {
    // Onde o painel Navegador está (px de CSS) e de qual conversa; bounds null = não mostrar view nenhuma.
    view: (shown) => ipcRenderer.send("forja:browser:view", shown),
  },
});

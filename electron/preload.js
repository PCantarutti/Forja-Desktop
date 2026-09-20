/** Única ponte entre a interface e o sistema: o diálogo de pasta. O resto vai pela API do backend. */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("forja", {
  pickFolder: (start) => ipcRenderer.invoke("forja:pickFolder", start),
});

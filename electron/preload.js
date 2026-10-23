/** Ponte entre a interface e o sistema: diálogo de pasta e as preferências da casca. O resto vai pela API do backend. */
const { contextBridge, ipcRenderer, webUtils } = require("electron");

// O token vem síncrono: o api.ts precisa dele na primeira chamada, antes de qualquer await.
const token = ipcRenderer.sendSync("forja:token");

contextBridge.exposeInMainWorld("forja", {
  token,
  pickFolder: (start) => ipcRenderer.invoke("forja:pickFolder", start),
  /** Caminho no disco de um arquivo escolhido ou arrastado ("" se ele não veio do disco, ex.: colado). */
  caminhoDe: (file) => webUtils.getPathForFile(file),
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
  /** Traz a janela para a frente (clique numa notificação do sistema), mesmo minimizada na bandeja. */
  focus: () => ipcRenderer.send("forja:focus"),
  /** Atalho global do assistente de voz: {atual, ok, erro} (registrado pela casca a partir da configuração). */
  atalho: () => ipcRenderer.invoke("forja:atalho:status"),
  /** A página do assistente está aberta: o círculo do canto da tela se esconde (a página mostra o grande). */
  assistenteVisivel: (visivel) => ipcRenderer.send("forja:assistente:visivel", visivel),
  browser: {
    // Onde o painel Navegador está (px de CSS) e de qual conversa; bounds null = não mostrar view nenhuma.
    view: (shown) => ipcRenderer.send("forja:browser:view", shown),
  },
});

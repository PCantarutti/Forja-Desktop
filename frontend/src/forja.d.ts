/** Ponte com o Electron (preload.js). Ausente quando a UI abre numa aba comum do navegador (dev). */
interface ForjaBridge {
  /** Diálogo de pasta do sistema; devolve o caminho escolhido ou null se cancelou. */
  pickFolder(start?: string): Promise<string | null>;
}

interface Window {
  forja?: ForjaBridge;
}

/** Ponte com o Electron (preload.js). Ausente quando a UI abre numa aba comum do navegador (dev). */
interface DesktopState {
  zoom: number;
  zoomSteps: number[];
  closeToTray: boolean;
  startWithWindows: boolean;
  startMinimized: boolean;
  version: string;
  packaged: boolean;
  paths: { data: string; log: string; db: string; md: string; exe: string };
}

/** Estado da atualização. `dev` = rodando fora do app instalado, onde não há release para comparar. */
interface UpdateState {
  state: "idle" | "dev" | "checking" | "available" | "downloading" | "ready" | "current" | "error";
  version: string;
  percent: number;
  error: string;
  notes: string;
}

interface ForjaBridge {
  /** Token desta execução do app; vai no header de toda chamada de /api (ver api.ts). */
  token: string;
  /** Diálogo de pasta do sistema; devolve o caminho escolhido ou null se cancelou. */
  pickFolder(start?: string): Promise<string | null>;
  /** Preferências da janela: zoom, bandeja, início com o Windows. */
  desktop: {
    get(): Promise<DesktopState>;
    set(patch: Partial<Pick<DesktopState, "zoom" | "closeToTray" | "startWithWindows" | "startMinimized">>): Promise<DesktopState>;
    zoom(dir: "in" | "out" | "reset"): Promise<number>;
    open(what: "log" | "data" | "db" | "md"): Promise<unknown>;
  };
  /** Atualização pelo GitHub Releases; nada baixa ou instala sem clique. */
  update: {
    get(): Promise<UpdateState>;
    check(): Promise<UpdateState>;
    download(): Promise<UpdateState>;
    install(): Promise<UpdateState>;
  };
  /** Navegador nativo: onde o painel da conversa `key` está (px de CSS), ou bounds null quando não está visível. */
  browser: {
    view(shown: { key: string; bounds: { x: number; y: number; width: number; height: number } | null }): void;
  };
}

interface Window {
  forja?: ForjaBridge;
}

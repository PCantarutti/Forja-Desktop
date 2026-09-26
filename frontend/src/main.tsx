import { Component, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "@fontsource-variable/atkinson-hyperlegible-next";
import "@fontsource-variable/jetbrains-mono";
import "@fontsource-variable/ibm-plex-sans";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./index.css";
import { aplicarAparencia, lerAparencia } from "./aparencia";
import { instalarArrasto } from "./arrastaNumero";

aplicarAparencia(lerAparencia());
instalarArrasto();

/**
 * Sem isto, um erro de render derruba a árvore inteira e sobra a página vazia (tela preta que não
 * responde a nada). Aqui o erro vira uma tela legível com a mensagem e um botão de recarregar.
 */
class ErrorBoundary extends Component<{ children: ReactNode }, { erro: Error | null }> {
  state: { erro: Error | null } = { erro: null };

  static getDerivedStateFromError(erro: Error) {
    return { erro };
  }

  componentDidCatch(erro: Error, info: { componentStack?: string | null }) {
    console.error("Forja: erro na interface", erro, info.componentStack);
  }

  render() {
    const { erro } = this.state;
    if (!erro) return this.props.children;
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-xl space-y-3 rounded-2xl border border-line bg-surface p-5 text-sm">
          <div className="font-medium text-fg">A interface quebrou</div>
          <div className="text-muted">
            A conversa está salva no servidor: recarregar volta tudo de onde parou. Se repetir, mande esta mensagem:
          </div>
          <pre className="max-h-60 overflow-auto rounded-xl border border-line bg-bg p-3 text-xs text-fg/85">
            {erro.stack || String(erro)}
          </pre>
          <div className="flex gap-2">
            <button
              onClick={() => location.reload()}
              className="rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-accent-fg hover:brightness-110"
            >
              Recarregar
            </button>
            <button
              onClick={() => navigator.clipboard?.writeText(erro.stack || String(erro))}
              className="rounded-full border border-line px-4 py-1.5 text-sm text-fg hover:bg-raised"
            >
              Copiar erro
            </button>
          </div>
        </div>
      </div>
    );
  }
}

createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>,
);

import { useEffect, useRef } from "react";

/**
 * Casca dos modais: fundo escuro, painel centralizado, Escape, foco e `role="dialog"`.
 *
 * Os três modais do app (Configurações, seletor de pasta, busca de modelos) tinham a mesma
 * estrutura copiada e nenhum tinha a parte de teclado completa: só a busca fechava no Escape,
 * nenhum devolvia o foco para o botão que o abriu, e o Tab passeava pela tela de trás.
 */
export function Modal(props: {
  onClose: () => void;
  /** Chamado antes de fechar pelo fundo ou pelo Escape. False segura o modal aberto. */
  canClose?: () => boolean;
  /** Nome do diálogo para quem usa leitor de tela. */
  label: string;
  /** Classes do painel (tamanho e layout são de cada tela). */
  className: string;
  children: React.ReactNode;
}) {
  const painel = useRef<HTMLDivElement>(null);
  const { onClose, canClose } = props;

  useEffect(() => {
    const anterior = document.activeElement as HTMLElement | null;
    painel.current?.focus();

    const naTecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (canClose && !canClose()) return;
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !painel.current) return;
      // Prende o Tab no painel: sem isto ele sai para a tela de trás, que está coberta e não
      // deveria receber foco nenhum enquanto o modal está aberto.
      const focaveis = painel.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focaveis.length) return;
      const primeiro = focaveis[0];
      const ultimo = focaveis[focaveis.length - 1];
      const foco = document.activeElement;
      if (!e.shiftKey && foco === ultimo) {
        e.preventDefault();
        primeiro.focus();
      } else if (e.shiftKey && (foco === primeiro || foco === painel.current)) {
        e.preventDefault();
        ultimo.focus();
      }
    };

    window.addEventListener("keydown", naTecla);
    return () => {
      window.removeEventListener("keydown", naTecla);
      anterior?.focus?.();
    };
  }, [onClose, canClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4"
      onClick={() => (!canClose || canClose()) && onClose()}
    >
      <div
        ref={painel}
        role="dialog"
        aria-modal="true"
        aria-label={props.label}
        tabIndex={-1}
        className={props.className + " shadow-dialog focus:outline-none"}
        onClick={(e) => e.stopPropagation()}
      >
        {props.children}
      </div>
    </div>
  );
}

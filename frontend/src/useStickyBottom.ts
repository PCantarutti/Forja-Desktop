import { useCallback, useEffect, useRef } from "react";

/**
 * Cola a rolagem no fim enquanto chega conteúdo novo, sem prender o usuário.
 *
 * Duas regras assimétricas, e é a assimetria que faz funcionar:
 *
 * - **Solta** quando o usuário rola para cima, nem que seja 1px. Quem decide isso é o `onScroll`, e
 *   ele só olha a direção — a nossa própria rolagem sempre desce, então nunca se confunde com ele.
 * - **Cola** quando a sentinela do fim aparece. Quem decide isso é o IntersectionObserver, nunca o
 *   `onScroll`.
 *
 * Medir "chegou ao fim" em px no `onScroll` não funciona aqui: entre o usuário rolar e o evento
 * chegar, o React já pintou mais texto (a 160 t/s são ~100px), e a distância que o handler lê nunca
 * é a que ele viu. Qualquer limiar vira loteria — com 24px o usuário não conseguia voltar a
 * acompanhar, com 120px ainda falhava por 1px. O observer não tem essa corrida: quem responde se a
 * sentinela está visível é o browser, no layout, com o conteúdo já no lugar.
 *
 * O `requestAnimationFrame` NÃO é cancelado na limpeza do efeito. Cancelando (era o que o código
 * antigo fazia), com token a cada ~6ms e quadro a cada ~16ms, cada token matava o quadro do anterior
 * e a rolagem simplesmente nunca acontecia.
 */
export function useStickyBottom<T extends HTMLElement>(deps: unknown[]) {
  const ref = useRef<T>(null);
  const fim = useRef<HTMLDivElement>(null);
  const colado = useRef(true);
  const ultimo = useRef(0);
  const quadro = useRef(0);
  const olho = useRef<IntersectionObserver | null>(null);
  const observado = useRef<Element | null>(null);

  // Sem lista de dependências de propósito: a sentinela nem sempre existe na montagem — a caixa de
  // raciocínio só desenha o corpo depois que chega o primeiro texto. Com `[]`, o observer nunca era
  // criado e a caixa acompanhava até a primeira rolagem e nunca mais colava. Aqui a cada render se
  // pergunta se já dá para observar, e só se cria quando o nó é outro.
  useEffect(() => {
    const raiz = ref.current;
    const alvo = fim.current;
    if (!raiz || !alvo || observado.current === alvo) return;
    olho.current?.disconnect();
    observado.current = alvo;
    olho.current = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) colado.current = true; // só cola; soltar é decisão do onScroll
      },
      { root: raiz },
    );
    olho.current.observe(alvo);
  });

  useEffect(() => () => olho.current?.disconnect(), []);

  useEffect(() => {
    if (!ref.current || !colado.current || quadro.current) return;
    quadro.current = requestAnimationFrame(() => {
      quadro.current = 0;
      const el = ref.current;
      if (!el || !colado.current) return;
      el.scrollTop = el.scrollHeight;
      ultimo.current = el.scrollTop;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => () => cancelAnimationFrame(quadro.current), []);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    if (el.scrollTop < ultimo.current - 1) colado.current = false;
    ultimo.current = el.scrollTop;
  }, []);

  /** Volta a acompanhar (trocar de conversa, mandar mensagem). */
  const colar = useCallback(() => {
    colado.current = true;
  }, []);

  return { ref, fim, onScroll, colar };
}

// Arrastar para os lados muda o valor de qualquer campo numérico do app (como no Blender/Figma).
// Vale para todo <input type="number"> e para os campos dentro de um [data-arrasta] (as caixas dos
// Parâmetros). Clique sem arrastar continua sendo clique: foca o campo para digitar.
// Passo: `step` do campo (ou data-passo no [data-arrasta]); Shift = passo × 10, Alt = passo ÷ 10.

const PX_POR_PASSO = 4;

function campoDe(alvo: EventTarget | null): HTMLInputElement | null {
  const el = alvo as HTMLElement | null;
  if (!el?.closest) return null;
  if (el instanceof HTMLInputElement && el.type === "number") return el;
  const caixa = el.closest<HTMLElement>("[data-arrasta]");
  return caixa?.querySelector<HTMLInputElement>("input") ?? null;
}

/** Escreve o valor do jeito que o React enxerga (setter nativo + evento input). */
function escrever(input: HTMLInputElement, v: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, v);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

export function instalarArrasto() {
  document.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const input = campoDe(e.target);
    // já focado (digitando) ou desabilitado: comportamento normal do campo
    if (!input || input.disabled || input.readOnly || document.activeElement === input) return;
    const caixa = input.closest<HTMLElement>("[data-arrasta]");
    const passoBase = Number(caixa?.dataset.passo || input.step) || 1;
    const casas = (String(passoBase).split(".")[1] ?? "").length;
    const min = input.min !== "" ? Number(input.min) : -Infinity;
    const max = input.max !== "" ? Number(input.max) : Infinity;
    const inicio = Number(input.value.replace(",", ".")) || 0;
    const x0 = e.clientX;
    let arrastando = false;

    const mexe = (ev: PointerEvent) => {
      const dx = ev.clientX - x0;
      if (!arrastando) {
        if (Math.abs(dx) < 4) return;
        arrastando = true;
        document.body.style.cursor = "ew-resize";
        document.body.style.userSelect = "none";
      }
      const passo = passoBase * (ev.shiftKey ? 10 : ev.altKey ? 0.1 : 1);
      const bruto = inicio + Math.round(dx / PX_POR_PASSO) * passo;
      const v = Math.min(max, Math.max(min, Math.round(bruto / passo) * passo));
      escrever(input, v.toFixed(ev.altKey ? casas + 1 : casas));
    };
    const solta = () => {
      window.removeEventListener("pointermove", mexe);
      window.removeEventListener("pointerup", solta);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      if (arrastando) {
        // quem só aplica ao sair do campo (tamanho livre, prazo) fica sabendo que acabou
        input.focus();
        input.blur();
        const engole = (c: MouseEvent) => c.preventDefault();
        window.addEventListener("click", engole, { capture: true, once: true });
        setTimeout(() => window.removeEventListener("click", engole, { capture: true }), 0);
      }
    };
    // segura o foco até saber se é clique ou arrasto
    e.preventDefault();
    window.addEventListener("pointermove", mexe);
    window.addEventListener("pointerup", () => {
      if (!arrastando) input.focus();
    }, { once: true });
    window.addEventListener("pointerup", solta);
  });
}

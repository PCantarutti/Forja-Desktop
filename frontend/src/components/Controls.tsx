import { useEffect, useRef, useState } from "react";
import CartaoEstado from "./CartaoEstado";
import { Balanca, Bubble, Check, ChevronDown, Code, Film, Image, PanelLeft, Search, Split } from "./icons";

export type Permission = "auto" | "manual" | "edits" | "plan" | "bypass";
export type Effort = "baixo" | "medio" | "alto" | "maximo" | "extremo";
// "imagem" é o mesmo literal do `kind` da conversa no backend: a barra lateral interpola
// a seção direto na query de /conversations.
export type Section = "chat" | "agent" | "maestro" | "imagem" | "video" | "comparar" | "pesquisa";

export const PERMISSIONS: { id: Permission; label: string; hint: string }[] = [
  { id: "auto", label: "Automático", hint: "O Forja decide: edições passam, o resto pergunta" },
  { id: "manual", label: "Manual", hint: "Sempre perguntar antes de qualquer alteração" },
  { id: "edits", label: "Aceitar edições", hint: "Aceita edições de arquivo; shell e navegador perguntam" },
  { id: "plan", label: "Plano", hint: "Só leitura: monta um plano antes de alterar" },
  { id: "bypass", label: "Ignorar permissões", hint: "Aceita tudo, inclusive shell. Cuidado." },
];

export const EFFORTS: { id: Effort; label: string; hint: string }[] = [
  { id: "baixo", label: "Baixo", hint: "Direto ao ponto, menos passos e menos raciocínio" },
  { id: "medio", label: "Médio", hint: "Equilíbrio entre rapidez e cuidado" },
  { id: "alto", label: "Alto", hint: "Confere o que fez e roda testes quando faz sentido" },
  { id: "maximo", label: "Máximo", hint: "Investiga a fundo, testa e revisa antes de concluir" },
  { id: "extremo", label: "Extremo", hint: "Delega o difícil a um modelo mais forte, verifica com um comando e revisa o diff" },
];

/** Shift+Tab: durante uma resposta o modo Plano fica de fora (entrar nele no meio não faz sentido). */
export const nextPermission = (p: Permission, running = false): Permission => {
  const list = running ? PERMISSIONS.filter((x) => x.id !== "plan") : PERMISSIONS;
  const i = list.findIndex((x) => x.id === p);
  return list[(i + 1) % list.length].id;
};

/** Menu que abre para cima, no rodapé do campo de mensagem (como no Claude). */
export function Menu<T extends string>(props: {
  title: string;
  items: { id: T; label: string; hint: string }[];
  value: T;
  onChange: (v: T) => void;
  button: (label: string, open: boolean) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const current = props.items.find((i) => i.id === props.value) ?? props.items[0];

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
      const n = Number(e.key);
      if (n >= 1 && n <= props.items.length) {
        props.onChange(props.items[n - 1].id);
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", key);
    };
  }, [open, props.items, props.onChange]);

  return (
    <div ref={box} className="relative">
      <button onClick={() => setOpen(!open)} className={`flex items-center gap-1.5 rounded-[9px] border px-2.5 py-[5px] text-[12.5px] ${open ? "border-accent-line bg-raised text-fg" : "border-line text-fg-2 hover:bg-raised hover:text-fg"}`}>
        {props.button(current.label, open)}
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-40 mb-2 w-80 overflow-hidden rounded-[14px] border border-line-strong bg-surface p-1.5 shadow-popover">
          <div className="px-2.5 pt-1 pb-1.5 font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase">{props.title}</div>
          {props.items.map((item, i) => (
            <button
              key={item.id}
              onClick={() => {
                props.onChange(item.id);
                setOpen(false);
              }}
              className={`flex w-full items-start gap-2 rounded-[9px] px-2.5 py-2 text-left ${
                item.id === props.value ? "bg-raised" : "hover:bg-raised/60"
              }`}
            >
              <div className="min-w-0 flex-1">
                <div className="text-[13px] text-fg">{item.label}</div>
                <div className="text-[11.5px] leading-snug text-faint">{item.hint}</div>
              </div>
              {item.id === props.value && <Check className="mt-1 size-3.5 shrink-0 text-fg" />}
              <span className="mt-1 w-3 shrink-0 text-right text-[11px] text-faint">{i + 1}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const COR_MODO: Record<Permission, string> = { auto: "bg-ok", manual: "bg-muted", edits: "bg-info", plan: "bg-agent", bypass: "bg-err" };
// Multiplicadores de custo que o design mostra ao lado de cada esforço. Fora da tela até o backend dizer
// o que são de verdade (tokens/tempo relativos ao Médio); ver REDESIGN-CHECKLIST.md › Backend.
// const MULT_ESFORCO: Record<Effort, string> = { baixo: "0,4×", medio: "1×", alto: "1,6×", maximo: "3×", extremo: "4× · multi" };
const NIVEL_ESFORCO: Record<Effort, number> = { baixo: 1, medio: 2, alto: 3, maximo: 4, extremo: 4 };

/** Pílula única de Modo · Esforço no composer (o Chat só tem esforço). Abre o menu de duas colunas:
 *  MODO (1–5) à esquerda e ESFORÇO com barras de intensidade à direita. */
export function ModeEffortMenu(props: {
  permission?: Permission; // sem ferramentas (Chat): só o esforço
  onPermission?: (v: Permission) => void;
  effort: Effort;
  onEffort: (v: Effort) => void;
  running?: boolean;
  semExtremo?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const modos = props.running ? PERMISSIONS.filter((x) => x.id !== "plan") : PERMISSIONS;
  const esforcos = props.semExtremo ? EFFORTS.filter((e) => e.id !== "extremo") : EFFORTS;
  const effort = props.semExtremo && props.effort === "extremo" ? "maximo" : props.effort;
  const modo = PERMISSIONS.find((p) => p.id === props.permission);
  const esf = EFFORTS.find((e) => e.id === effort) ?? EFFORTS[1];
  const temModo = !!(props.permission && props.onPermission);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
      const n = Number(e.key);
      // Com as duas colunas o menu fica aberto: dá para acertar modo e esforço de uma vez.
      if (temModo && n >= 1 && n <= modos.length) props.onPermission!(modos[n - 1].id);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", key);
    };
  }, [open, modos, temModo, props.onPermission]);

  const item = (on: boolean) => `flex w-full items-start gap-2.5 rounded-[9px] px-2.5 py-2 text-left ${on ? "bg-raised text-fg" : "text-fg-2 hover:bg-raised/60"}`;
  const rotulo = "px-2.5 pt-1 pb-1.5 font-mono text-[10.5px] font-medium tracking-[.08em] text-faint uppercase";

  return (
    <div ref={box} className="relative min-w-0">
      <button
        onClick={() => setOpen(!open)}
        title={temModo ? (props.running ? "Modo e esforço (vale já nesta resposta)" : "Modo e esforço · Shift+Tab alterna o modo") : "Esforço"}
        className={`inline-flex max-w-full items-center gap-2 rounded-[9px] px-2.5 py-[5px] text-[12.5px] whitespace-nowrap text-fg-2 hover:text-fg ${
          open ? "bg-line ring-1 ring-accent-line" : "bg-raised"}`}
      >
        {modo && <span className={`size-1.5 shrink-0 rounded-full ${COR_MODO[modo.id]}`} />}
        <span className={`truncate ${modo?.id === "bypass" ? "text-err" : ""}`}>
          {modo ? `${modo.label} · ${esf.label}` : `Esforço ${esf.label.toLowerCase()}`}
        </span>
        <ChevronDown className="size-3 shrink-0" />
      </button>
      {open && (
        <div className={`absolute bottom-full left-0 z-40 mb-2 overflow-hidden rounded-[14px] border border-line-strong bg-surface shadow-popover ${temModo ? "w-[520px]" : "w-[240px]"}`}>
          <div className="flex gap-1 p-1.5">
            {temModo && (
              <div className="min-w-0 flex-1">
                <div className={rotulo}>Modo</div>
                {modos.map((m, i) => (
                  <button key={m.id} onClick={() => props.onPermission!(m.id)} className={item(m.id === props.permission)}>
                    <span className={`mt-[5px] size-1.5 shrink-0 rounded-full ${COR_MODO[m.id]}`} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px]">{m.label}</span>
                      <span className="block text-[11.5px] leading-snug text-faint">{m.hint}</span>
                    </span>
                    <span className="mt-0.5 font-mono text-[10.5px] text-faint">{i + 1}</span>
                  </button>
                ))}
              </div>
            )}
            <div className={temModo ? "w-[200px] shrink-0 border-l border-line pl-1" : "flex-1"}>
              <div className={rotulo}>Esforço</div>
              {esforcos.map((e) => {
                const on = e.id === effort;
                return (
                  <button key={e.id} onClick={() => { props.onEffort(e.id); if (!temModo) setOpen(false); }} className={`${item(on)} items-center`}>
                    <span className="flex h-[15px] shrink-0 items-end gap-[2px]" aria-hidden>
                      {[6, 9, 12, 15].map((h, k) => (
                        <span key={h} style={{ height: h }}
                              className={`w-[3px] rounded-[1px] ${k < NIVEL_ESFORCO[e.id] ? (e.id === "extremo" && k === 3 ? "bg-warn" : on ? "bg-accent" : "bg-muted") : "bg-line"}`} />
                      ))}
                    </span>
                    <span className="flex-1 text-[13px]">{e.label}</span>
                    {/* <span className="font-mono text-[10.5px] text-faint">{MULT_ESFORCO[e.id]}</span> */}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="border-t border-line px-4 py-2 text-[11.5px] text-muted">{esf.label}: {esf.hint.charAt(0).toLowerCase() + esf.hint.slice(1)}.</div>
        </div>
      )}
    </div>
  );
}

export const SECOES: { id: Section; label: string; title: string; icon: React.ReactNode }[] = [
  { id: "chat", label: "Chat", title: "Chat (busca na web)", icon: <Bubble className="size-[18px]" /> },
  { id: "agent", label: "Agente", title: "Agente (ferramentas)", icon: <Code className="size-[18px]" /> },
  { id: "maestro", label: "Maestro", title: "Maestro (planeja e delega a Workers)", icon: <Split className="size-[18px]" /> },
  { id: "imagem", label: "Imagem", title: "Imagens (Stable Diffusion)", icon: <Image className="size-[18px]" /> },
  { id: "video", label: "Vídeo", title: "Vídeo (Wan)", icon: <Film className="size-[18px]" /> },
  { id: "comparar", label: "Comparar", title: "Comparar modelos", icon: <Balanca className="size-[18px]" /> },
  { id: "pesquisa", label: "Pesquisa", title: "Pesquisa profunda", icon: <Search className="size-[18px]" /> },
];

/** Trilho de seções à esquerda (60 px), sempre visível. Com a lista de conversas escondida, mostra o
 *  botão de reabrir logo abaixo do logo. */
export function SectionRail(props: {
  value: Section;
  onChange: (v: Section) => void;
  listHidden: boolean;
  onShowList: () => void;
  logo: React.ReactNode;
  pe?: React.ReactNode; // rodapé do trilho (as iniciais)
}) {
  return (
    <nav className="arrasta flex w-[60px] shrink-0 flex-col items-center gap-1 border-r border-line bg-side pt-3 pb-2.5" aria-label="Seção">
      <div className="mb-3.5 size-[30px]">{props.logo}</div>
      {props.listHidden && (
        <button onClick={props.onShowList} title="Mostrar conversas"
                className="mb-1.5 grid h-7 w-8 place-items-center rounded-[7px] text-muted hover:bg-raised hover:text-fg">
          <PanelLeft />
        </button>
      )}
      <div role="radiogroup" className="flex flex-col items-center gap-1">
        {SECOES.map((t, i) => {
          const on = props.value === t.id;
          return (
            <button
              key={t.id}
              role="radio"
              aria-checked={on}
              title={`${t.title} · Ctrl ${i + 1}`}
              onClick={() => props.onChange(t.id)}
              className={`relative flex w-12 flex-col items-center gap-[3px] rounded-[10px] pt-[7px] pb-1.5 transition-colors ${
                on ? "bg-accent-soft text-accent-text" : "text-muted hover:text-fg"}`}
            >
              <span className={`absolute top-2.5 bottom-2.5 -left-1.5 w-[3px] rounded-r-[3px] ${on ? "bg-accent" : "bg-transparent"}`} />
              {t.icon}
              <span className="text-[9.5px] leading-none tracking-[.02em]">{t.label}</span>
            </button>
          );
        })}
      </div>
      <div className="flex-1" />
      {props.pe}
    </nav>
  );
}

const AVISO_BYPASS = "forja.aviso.bypass";

const dispensado = () => {
  try {
    return localStorage.getItem(AVISO_BYPASS) === "1";
  } catch {
    return false; // janela anônima ou storage bloqueado: mostra, que é o lado seguro
  }
};

/**
 * Aviso no rodapé quando o modo aceita tudo. Dá para fechar — mas volta se você sair do modo e
 * entrar de novo: dispensar valeu para aquela vez, não para sempre. O chip do compositor continua
 * dizendo em que modo você está, então fechar esconde o lembrete, não a informação.
 */
export function ModeWarning({ permission }: { permission: Permission }) {
  const [oculto, setOculto] = useState(dispensado);
  const anterior = useRef(permission);

  useEffect(() => {
    if (permission === "bypass" && anterior.current !== "bypass") {
      setOculto(false);
      try {
        localStorage.removeItem(AVISO_BYPASS);
      } catch { /* sem storage: só não lembra */ }
    }
    anterior.current = permission;
  }, [permission]);

  if (permission !== "bypass" || oculto) return null;

  const fechar = () => {
    setOculto(true);
    try {
      localStorage.setItem(AVISO_BYPASS, "1");
    } catch { /* sem storage: fecha só desta vez */ }
  };

  return (
    <CartaoEstado tom="erro" compacto onFechar={fechar} className="mb-2">
      <span className="min-w-0">
        <strong className="font-semibold">Ignorar permissões</strong>: tudo passa sem perguntar, inclusive shell. Só comando
        destrutivo (apagar, formatar, desligar, sudo, force push) ainda pede confirmação.
      </span>
    </CartaoEstado>
  );
}

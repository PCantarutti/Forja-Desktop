import { useEffect, useRef, useState } from "react";
import { Balanca, Bubble, Check, Clipboard, Code, Film, Gauge, Image, PanelLeft, Search, Shield, Sliders, Split, X } from "./icons";

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
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 rounded-full border border-line px-3 py-1 text-xs text-muted hover:bg-raised hover:text-fg">
        {props.button(current.label, open)}
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-40 mb-2 w-80 overflow-hidden rounded-2xl border border-line bg-surface p-1.5 shadow-2xl shadow-black/50">
          <div className="px-2.5 py-1 text-[11px] tracking-wider text-faint uppercase">{props.title}</div>
          {props.items.map((item, i) => (
            <button
              key={item.id}
              onClick={() => {
                props.onChange(item.id);
                setOpen(false);
              }}
              className={`flex w-full items-start gap-2 rounded-xl px-2.5 py-1.5 text-left ${
                item.id === props.value ? "bg-raised" : "hover:bg-raised/60"
              }`}
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm text-fg">{item.label}</div>
                <div className="text-xs leading-snug text-muted">{item.hint}</div>
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

export function PermissionMenu({
  value,
  onChange,
  running = false,
}: {
  value: Permission;
  onChange: (v: Permission) => void;
  running?: boolean;
}) {
  return (
    <Menu
      title={running ? "Modo (vale já nesta resposta)" : "Modo (Shift+Tab alterna)"}
      items={running ? PERMISSIONS.filter((x) => x.id !== "plan") : PERMISSIONS}
      value={value}
      onChange={onChange}
      button={(label) => (
        <>
          {value === "plan" ? <Clipboard className="size-3.5" /> : <Shield className={`size-3.5 ${value === "bypass" ? "text-amber-300" : ""}`} />}
          <span className={value === "bypass" ? "text-amber-200" : ""}>{label}</span>
        </>
      )}
    />
  );
}

export function EffortMenu({ value, onChange, semExtremo }: {
  value: Effort;
  onChange: (v: Effort) => void;
  // No Maestro o "Extremo" não existe: ele é o modo em que o principal só delega, e a tela do
  // Maestro já é isso — com contrato, tentativa e verificação.
  semExtremo?: boolean;
}) {
  return (
    <Menu
      title="Esforço"
      items={semExtremo ? EFFORTS.filter((e) => e.id !== "extremo") : EFFORTS}
      value={semExtremo && value === "extremo" ? "maximo" : value}
      onChange={onChange}
      button={(label) => (
        <>
          <Gauge className="size-3.5" />
          {label}
        </>
      )}
    />
  );
}

/** Chat | Agente | Maestro | Imagens | Comparar | Pesquisa, no canto superior esquerdo (com o botão de esconder a barra lateral). */
export function SectionTabs(props: {
  value: Section;
  onChange: (v: Section) => void;
  sidebarHidden: boolean;
  onToggleSidebar: () => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <button
        onClick={props.onToggleSidebar}
        title={props.sidebarHidden ? "Mostrar conversas" : "Esconder conversas"}
        className="rounded-lg p-1.5 text-muted hover:bg-raised hover:text-fg"
      >
        <PanelLeft />
      </button>
      <div className="flex rounded-lg border border-line p-0.5" role="radiogroup" aria-label="Seção">
        {([
          { id: "chat" as const, icon: <Bubble className="size-4" />, title: "Chat (busca na web)" },
          { id: "agent" as const, icon: <Code className="size-4" />, title: "Agente (ferramentas)" },
          { id: "maestro" as const, icon: <Split className="size-4" />, title: "Maestro (planeja e delega a Workers)" },
          { id: "imagem" as const, icon: <Image className="size-4" />, title: "Imagens (Stable Diffusion)" },
          { id: "video" as const, icon: <Film className="size-4" />, title: "Vídeo (Wan)" },
          { id: "comparar" as const, icon: <Balanca className="size-4" />, title: "Comparar modelos" },
          { id: "pesquisa" as const, icon: <Search className="size-4" />, title: "Pesquisa profunda" },
        ]).map((t) => (
          <button
            key={t.id}
            role="radio"
            aria-checked={props.value === t.id}
            title={t.title}
            onClick={() => props.onChange(t.id)}
            // px-1.5 e não px-2: com a aba Vídeo são 7, e a 7ª saía cortada na coluna de 240 px
            className={`rounded-md px-1.5 py-1 ${props.value === t.id ? "bg-raised text-fg" : "text-faint hover:text-fg"}`}
          >
            {t.icon}
          </button>
        ))}
      </div>
    </div>
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
    <div className="mb-2 flex items-center gap-2 rounded-xl border border-amber-500/40 bg-surface px-3 py-1.5 text-xs text-amber-200">
      <Sliders className="size-3.5 shrink-0" />
      <span className="min-w-0 flex-1">
        Modo <strong>Ignorar permissões</strong>: comandos e alterações rodam sem perguntar. Só comando
        destrutivo (apagar, formatar, desligar, sudo, force push) ainda pede confirmação.
      </span>
      <button
        onClick={fechar}
        title="Fechar o aviso (volta se você trocar de modo e voltar)"
        aria-label="Fechar o aviso"
        className="shrink-0 rounded-md p-1 text-amber-200/70 hover:bg-amber-500/20 hover:text-amber-100"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}

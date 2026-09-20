import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { Conversation } from "../types";
import { folderName } from "./FolderPicker";
import { LogoMark, LogoText } from "./Logo";
import { SectionTabs, type Section } from "./Controls";
import { Archive, Chevron, Download, Edit, Gear, More, Pin, Search, Trash } from "./icons";

export type BulkAction = "archive" | "unarchive" | "pin" | "unpin" | "delete";

const GROUPS_KEY = "forja.groups.collapsed";

function loadCollapsed(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(GROUPS_KEY) ?? "[]"));
  } catch {
    return new Set();
  }
}

/** Menu "⋯" de uma conversa: renomear, fixar, arquivar, exportar, apagar (confirmação inline, sem confirm()). */
function ItemMenu(props: {
  c: Conversation;
  onClose: () => void;
  onRename: () => void;
  onPin: (pinned: boolean) => void;
  onArchive: (archived: boolean) => void;
  onDelete: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) props.onClose();
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);
  const item = "flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-fg hover:bg-raised";
  return (
    <div ref={ref} onClick={(e) => e.stopPropagation()} className="absolute top-full right-1 z-30 mt-1 w-48 overflow-hidden rounded-xl border border-line bg-surface py-1 shadow-xl">
      <button className={item} onClick={() => { props.onRename(); props.onClose(); }}>
        <Edit className="size-3.5" /> Renomear
      </button>
      <button className={item} onClick={() => { props.onPin(!props.c.pinned); props.onClose(); }}>
        <Pin className="size-3.5" /> {props.c.pinned ? "Desafixar" : "Fixar no topo"}
      </button>
      <button className={item} onClick={() => { props.onArchive(!props.c.archived); props.onClose(); }}>
        <Archive className="size-3.5" /> {props.c.archived ? "Desarquivar" : "Arquivar"}
      </button>
      <a className={item} href={`/api/conversations/${props.c.id}/export`} download onClick={props.onClose}>
        <Download className="size-3.5" /> Exportar (.md)
      </a>
      {confirming ? (
        <div className="flex items-center gap-2 px-3 py-1.5 text-sm">
          <span className="text-red-300">Apagar?</span>
          <button onClick={() => { props.onDelete(); props.onClose(); }} className="rounded-full bg-red-600 px-2.5 py-0.5 text-xs font-medium text-white hover:bg-red-500">
            Sim
          </button>
          <button onClick={() => setConfirming(false)} className="rounded-full border border-line px-2.5 py-0.5 text-xs text-fg hover:bg-raised">
            Não
          </button>
        </div>
      ) : (
        <button className={`${item} text-red-300 hover:text-red-200`} onClick={() => setConfirming(true)}>
          <Trash className="size-3.5" /> Apagar
        </button>
      )}
    </div>
  );
}

export default function Sidebar(props: {
  conversations: Conversation[];
  current: number | null;
  unread: Set<number>;
  onSelect: (id: number) => void;
  onNew: () => void;
  onNewIn: (workspace: string | null) => void; // nova conversa numa pasta específica (grupo)
  onDelete: (id: number) => void;
  onRename: (id: number, title: string) => void;
  onPin: (id: number, pinned: boolean) => void;
  onArchive: (id: number, archived: boolean) => void;
  onBulk: (ids: number[], action: BulkAction) => Promise<void>;
  onSettings: () => void;
  section: Section;
  onSection: (s: Section) => void;
  onHide: () => void;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Conversation[] | null>(null); // busca por conteúdo no servidor
  const [menu, setMenu] = useState<number | null>(null);
  const [renaming, setRenaming] = useState<{ id: number; title: string } | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [archived, setArchived] = useState<Conversation[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(loadCollapsed);
  // Seleção múltipla: ações em lote (arquivar, fixar, apagar…)
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    localStorage.setItem(GROUPS_KEY, JSON.stringify([...collapsed]));
  }, [collapsed]);

  // Título casa na hora; conteúdo é consultado ao servidor depois de uma pausa na digitação.
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) return setHits(null);
    const t = setTimeout(() => {
      api.get<Conversation[]>(`/conversations/search?q=${encodeURIComponent(term)}&kind=${props.section}`).then(setHits).catch(() => setHits(null));
    }, 300);
    return () => clearTimeout(t);
  }, [q, props.section]);

  useEffect(() => {
    if (!showArchived) return;
    api.get<Conversation[]>(`/conversations?kind=${props.section}&archived=true`).then(setArchived).catch(() => setArchived([]));
  }, [showArchived, props.section, props.conversations]);

  useEffect(() => {
    // Sair da seção ou fechar a seleção limpa o que estava marcado.
    setSelected(new Set());
    setConfirmDelete(false);
  }, [selecting, props.section]);

  const byTitle = props.conversations.filter((c) => c.title.toLowerCase().includes(q.toLowerCase()));
  const extra = (hits ?? []).filter((h) => !byTitle.some((c) => c.id === h.id));
  const list = q.trim() ? [...byTitle, ...extra] : props.conversations;

  // Seção Agente: agrupado por pasta de trabalho (como no Claude); grupo mais recente primeiro.
  const groups = useMemo(() => {
    if (props.section !== "agent" || q.trim()) return null;
    const map = new Map<string, Conversation[]>();
    for (const c of list) {
      const key = c.workspace ?? "";
      map.set(key, [...(map.get(key) ?? []), c]);
    }
    return [...map.entries()]
      .map(([key, items]) => ({
        key,
        full: key || (items[0]?.workspace_label ?? "Pasta padrão"),
        label: key ? folderName(key) : `${folderName(items[0]?.workspace_label ?? "")} (padrão)`,
        items,
        latest: Math.max(...items.map((c) => +new Date(c.updated_at))),
        pinned: items.some((c) => c.pinned),
      }))
      .sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.latest - a.latest);
  }, [list, props.section, q]);

  function toggleSelect(id: number) {
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }

  function toggleGroup(key: string) {
    setCollapsed((s) => {
      const n = new Set(s);
      n.has(key) ? n.delete(key) : n.add(key);
      return n;
    });
  }

  async function bulk(action: BulkAction) {
    if (!selected.size) return;
    setBusy(true);
    await props.onBulk([...selected], action);
    setBusy(false);
    setSelecting(false);
  }

  const selectedList = [...selected];
  const visible = [...list, ...(showArchived ? archived : [])];
  const allArchived = selectedList.length > 0 && selectedList.every((id) => visible.find((c) => c.id === id)?.archived);
  const allPinned = selectedList.length > 0 && selectedList.every((id) => visible.find((c) => c.id === id)?.pinned);

  function row(c: Conversation, dim = false) {
    const active = c.id === props.current;
    const checked = selected.has(c.id);
    return (
      <div
        key={c.id}
        onClick={() => (selecting ? toggleSelect(c.id) : props.onSelect(c.id))}
        className={`group relative flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-2 text-sm ${
          checked ? "bg-sky-950/40 text-fg" : active ? "bg-raised text-fg" : dim ? "text-faint hover:bg-surface hover:text-muted" : "text-muted hover:bg-surface hover:text-fg"
        }`}
      >
        {selecting && (
          <input type="checkbox" checked={checked} onChange={() => toggleSelect(c.id)} onClick={(e) => e.stopPropagation()} className="size-3.5 shrink-0 accent-sky-500" />
        )}
        {props.unread.has(c.id) && <span className="size-1.5 shrink-0 rounded-full bg-sky-400" title="terminou em segundo plano" />}
        {c.pinned && !dim && <Pin className="size-3 shrink-0 text-faint" />}
        {renaming?.id === c.id ? (
          <input
            autoFocus
            value={renaming.title}
            onChange={(e) => setRenaming({ id: c.id, title: e.target.value })}
            onClick={(e) => e.stopPropagation()}
            onBlur={() => {
              if (renaming.title.trim() && renaming.title !== c.title) props.onRename(c.id, renaming.title.trim());
              setRenaming(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") setRenaming(null);
            }}
            className="w-full rounded bg-bg px-1 text-fg focus:outline-none"
          />
        ) : (
          <span className="min-w-0 flex-1 truncate" title={c.snippet ? `…${c.snippet}…` : c.title}>
            {c.title}
            {c.snippet && <span className="block truncate text-[11px] text-faint">…{c.snippet}…</span>}
          </span>
        )}
        {!selecting && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setMenu(menu === c.id ? null : c.id);
            }}
            className={`shrink-0 rounded p-0.5 text-faint hover:bg-bg hover:text-fg ${menu === c.id ? "" : "opacity-0 group-hover:opacity-100"}`}
            title="Opções"
          >
            <More className="size-3.5" />
          </button>
        )}
        {menu === c.id && (
          <ItemMenu
            c={c}
            onClose={() => setMenu(null)}
            onRename={() => setRenaming({ id: c.id, title: c.title })}
            onPin={(p) => props.onPin(c.id, p)}
            onArchive={(a) => props.onArchive(c.id, a)}
            onDelete={() => props.onDelete(c.id)}
          />
        )}
      </div>
    );
  }

  function groupHeader(g: { key: string; full: string; label: string; items: Conversation[] }) {
    const isCollapsed = collapsed.has(g.key);
    const ids = g.items.map((c) => c.id);
    const allSel = ids.every((id) => selected.has(id));
    return (
      <div key={`h-${g.key}`} className="group/h mt-2 flex items-center gap-1 px-2 py-1 text-[11px] text-faint">
        {selecting && (
          <input
            type="checkbox"
            checked={allSel}
            onChange={() =>
              setSelected((s) => {
                const n = new Set(s);
                ids.forEach((id) => (allSel ? n.delete(id) : n.add(id)));
                return n;
              })
            }
            title="Selecionar todas da pasta"
            className="size-3.5 accent-sky-500"
          />
        )}
        <button onClick={() => toggleGroup(g.key)} className="flex min-w-0 flex-1 items-center gap-1 text-left uppercase tracking-wider hover:text-muted" title={g.full}>
          <Chevron className={`size-3 shrink-0 ${isCollapsed ? "-rotate-90" : ""}`} />
          <span className="truncate">{g.label}</span>
          <span className="shrink-0 normal-case tracking-normal">{g.items.length}</span>
        </button>
        {!selecting && (
          <button
            onClick={() => props.onNewIn(g.key || null)}
            title={`Nova conversa em ${g.full}`}
            className="rounded p-0.5 opacity-0 hover:bg-raised hover:text-fg group-hover/h:opacity-100"
          >
            <Edit className="size-3" />
          </button>
        )}
      </div>
    );
  }

  const bulkBtn = "rounded-full border border-line px-2.5 py-1 text-xs text-fg hover:bg-raised disabled:opacity-40";

  return (
    <aside className="flex w-64 shrink-0 flex-col bg-side">
      <div className="flex items-center gap-2 px-3 pt-3 pb-1">
        <SectionTabs value={props.section} onChange={props.onSection} sidebarHidden={false} onToggleSidebar={props.onHide} />
      </div>
      <div className="flex items-center gap-2.5 px-4 pt-2 pb-2">
        <LogoMark className="size-7 shrink-0 text-fg" />
        <LogoText className="h-3.5 text-fg" />
        <button
          onClick={() => setSelecting((v) => !v)}
          title={selecting ? "Sair da seleção" : "Selecionar várias conversas"}
          className={`ml-auto rounded-lg px-2 py-1 text-xs ${selecting ? "bg-raised text-fg" : "text-muted hover:bg-raised hover:text-fg"}`}
        >
          {selecting ? "Cancelar" : "Selecionar"}
        </button>
        <button onClick={props.onNew} title={props.section === "chat" ? "Nova conversa de chat" : "Nova conversa do agente"} className="rounded-lg p-1.5 text-muted hover:bg-raised hover:text-fg">
          <Edit />
        </button>
      </div>
      <label className="mx-3 mt-2 mb-3 flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-muted focus-within:bg-surface">
        <Search />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Buscar (título e conteúdo)"
          className="w-full bg-transparent text-fg placeholder:text-muted focus:outline-none"
        />
      </label>
      <nav className="flex-1 overflow-y-auto px-2 pb-1">
        {groups
          ? groups.map((g) => (
              <div key={g.key}>
                {groupHeader(g)}
                {!collapsed.has(g.key) && g.items.map((c) => row(c))}
              </div>
            ))
          : list.map((c) => row(c))}
        {q.trim() && !list.length && <div className="px-3 py-2 text-xs text-faint">Nada encontrado.</div>}
        <button
          onClick={() => setShowArchived((v) => !v)}
          className="mt-2 flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-xs text-faint hover:bg-surface hover:text-muted"
        >
          <Archive className="size-3.5" /> {showArchived ? "Ocultar arquivadas" : "Arquivadas"}
        </button>
        {showArchived && (archived.length ? archived.map((c) => row(c, true)) : <div className="px-3 py-1 text-xs text-faint">Nenhuma arquivada.</div>)}
      </nav>
      {selecting && (
        <div className="border-t border-line px-3 py-2 text-xs">
          <div className="mb-1.5 text-muted">{selected.size} selecionada{selected.size === 1 ? "" : "s"}</div>
          {confirmDelete ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-red-300">Apagar {selected.size}?</span>
              <button disabled={busy} onClick={() => bulk("delete")} className="rounded-full bg-red-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-red-500 disabled:opacity-40">
                Sim, apagar
              </button>
              <button onClick={() => setConfirmDelete(false)} className={bulkBtn}>Não</button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              <button disabled={busy || !selected.size} onClick={() => bulk(allArchived ? "unarchive" : "archive")} className={bulkBtn}>
                {allArchived ? "Desarquivar" : "Arquivar"}
              </button>
              <button disabled={busy || !selected.size} onClick={() => bulk(allPinned ? "unpin" : "pin")} className={bulkBtn}>
                {allPinned ? "Desafixar" : "Fixar"}
              </button>
              <button disabled={busy || !selected.size} onClick={() => setConfirmDelete(true)} className={`${bulkBtn} text-red-300`}>
                Apagar
              </button>
            </div>
          )}
        </div>
      )}
      <button
        onClick={props.onSettings}
        className="m-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-muted hover:bg-surface hover:text-fg"
      >
        <Gear /> Configurações
      </button>
    </aside>
  );
}

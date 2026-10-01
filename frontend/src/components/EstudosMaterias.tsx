import { useState } from "react";
import { api } from "../api";
import type { EstudosMateria, EstudosProjeto } from "../types";
import { Edit, Plus, Trash } from "./icons";
import { rotulo } from "./estudosUi";

/** O ponto de cada matéria: verde ≥ 70% de acerto, âmbar 50–69%, vermelho abaixo; cinza sem entrega. */
export const corAcerto = (a: number | null) =>
  a == null ? "bg-faint/50" : a >= 70 ? "bg-emerald-400" : a >= 50 ? "bg-amber-400" : "bg-rose-400";

/** Acerto do objetivo inteiro: as entregas de todas as matérias, pesadas pelo número de entregas. */
export function acertoGeral(ms: EstudosMateria[]): number | null {
  const com = ms.filter((m) => m.acerto != null && m.entregas);
  const n = com.reduce((s, m) => s + m.entregas, 0);
  return n ? Math.round(com.reduce((s, m) => s + (m.acerto ?? 0) * m.entregas, 0) / n) : null;
}

/** Coluna "Tudo + matérias" do objetivo. Trocar de matéria troca o filtro de todas as abas. */
export default function EstudosMaterias(props: {
  conv: number;
  projeto: EstudosProjeto;
  materia: string | null;
  onEscolher: (m: string | null) => void;
  onMudou: () => void;
  onError: (e: string) => void;
  onEdital: () => void;    // abre "Ler o edital"
  onTrazer: () => void;    // abre "Trazer um estudo para cá"
}) {
  const [nova, setNova] = useState<string | null>(null);        // campo "+ Matéria" aberto
  const [editando, setEditando] = useState<{ id: string; nome: string } | null>(null);
  // confirmação na própria linha: confirm() não serve no app (ver Confirma.tsx)
  const [apagando, setApagando] = useState<string | null>(null);
  const ms = props.projeto.materias ?? [];
  const geral = acertoGeral(ms);

  async function criar() {
    const nome = (nova ?? "").trim();
    if (!nome) return setNova(null);
    try {
      const m = await api.post<EstudosMateria>(`/estudos/${props.conv}/materias`, { nome });
      setNova(null);
      props.onEscolher(m.id);
      props.onMudou();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function renomear() {
    if (!editando) return;
    const { id, nome } = editando;
    setEditando(null);
    if (!nome.trim() || nome.trim() === ms.find((m) => m.id === id)?.nome) return;
    try {
      await api.patch(`/estudos/${props.conv}/materias/${id}`, { nome });
      props.onMudou();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  async function apagar(m: EstudosMateria) {
    setApagando(null);
    try {
      await api.del(`/estudos/${props.conv}/materias/${m.id}`);
      if (props.materia === m.id) props.onEscolher(null);
      props.onMudou();
    } catch (e: any) {
      props.onError(e.message);
    }
  }

  const item = "group flex w-full items-center gap-2 rounded-[9px] px-2.5 py-1.5 text-left text-[13px]";
  return (
    <nav aria-label="Matérias" className="flex w-52 shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-line px-2 py-3">
      <p className={`${rotulo} mb-1 truncate px-2.5`} title={props.projeto.titulo}>{props.projeto.titulo}</p>
      <button onClick={() => props.onEscolher(null)} aria-current={props.materia === null}
              className={`${item} ${props.materia === null ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"}`}>
        <span className="size-2 shrink-0 rounded-full bg-accent" />
        <span className="min-w-0 flex-1 truncate font-medium">Tudo</span>
        {geral != null && <span className="font-mono text-[11px] text-faint">{geral}%</span>}
      </button>
      {ms.map((m) =>
        apagando === m.id ? (
          <div key={m.id} className="rounded-[9px] border border-line px-2.5 py-1.5 text-[11px]">
            <p className="text-amber-300">Tirar "{m.nome}"? O que é dela vai para o Geral; nada se apaga.</p>
            <div className="mt-1 flex gap-1.5">
              <button className="rounded-md border border-line px-2 py-0.5 text-fg hover:bg-raised" onClick={() => apagar(m)}>Tirar</button>
              <button className="rounded-md px-2 py-0.5 text-muted hover:text-fg" onClick={() => setApagando(null)}>Cancelar</button>
            </div>
          </div>
        ) : editando?.id === m.id ? (
          <input key={m.id} autoFocus value={editando.nome} aria-label="Nome da matéria" maxLength={60}
                 onChange={(e) => setEditando({ id: m.id, nome: e.target.value })} onBlur={renomear}
                 onKeyDown={(e) => { if (e.key === "Enter") renomear(); if (e.key === "Escape") setEditando(null); }}
                 className="rounded-[9px] border border-focus bg-surface px-2.5 py-1.5 text-[13px] text-fg outline-none" />
        ) : (
          <div key={m.id} className={`${item} cursor-pointer ${props.materia === m.id ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg"}`}
               onClick={() => props.onEscolher(m.id)} role="button" aria-current={props.materia === m.id}
               title={m.entregas ? `${m.acerto}% de acerto em ${m.entregas} entrega${m.entregas === 1 ? "" : "s"}` : "Nenhuma prova entregue ainda"}>
            <span className={`size-2 shrink-0 rounded-full ${corAcerto(m.acerto)}`} />
            <span className="min-w-0 flex-1 truncate">{m.nome}</span>
            <span className="font-mono text-[11px] text-faint group-hover:hidden">{m.acerto == null ? "—" : `${m.acerto}%`}</span>
            <span className="hidden shrink-0 gap-1 group-hover:flex">
              <button aria-label={`Renomear ${m.nome}`} title="Renomear" className="text-faint hover:text-fg"
                      onClick={(e) => { e.stopPropagation(); setEditando({ id: m.id, nome: m.nome }); }}>
                <Edit className="size-3.5" />
              </button>
              <button aria-label={`Tirar ${m.nome}`} title="Tirar a matéria" className="text-faint hover:text-rose-300"
                      onClick={(e) => { e.stopPropagation(); setApagando(m.id); }}>
                <Trash className="size-3.5" />
              </button>
            </span>
          </div>
        ))}
      {nova === null ? (
        <button onClick={() => setNova("")} className={`${item} mt-1 text-faint hover:bg-raised/60 hover:text-fg`}>
          <Plus className="size-3.5" /> Matéria
        </button>
      ) : (
        <input autoFocus value={nova} placeholder="Direito Administrativo" aria-label="Nova matéria" maxLength={60}
               onChange={(e) => setNova(e.target.value)} onBlur={criar}
               onKeyDown={(e) => { if (e.key === "Enter") criar(); if (e.key === "Escape") setNova(null); }}
               className="mt-1 rounded-[9px] border border-focus bg-surface px-2.5 py-1.5 text-[13px] text-fg outline-none" />
      )}
      <button onClick={props.onEdital} className={`${item} text-faint hover:bg-raised/60 hover:text-fg`}
              title="As matérias, o peso de cada uma e os tópicos, tirados do edital">
        Ler o edital
      </button>
      <button onClick={props.onTrazer} className={`${item} text-faint hover:bg-raised/60 hover:text-fg`}
              title="Juntar um estudo antigo neste objetivo, com todo o progresso dele">
        Trazer um estudo
      </button>
      <p className="mt-auto px-2.5 pt-4 text-[11px] leading-relaxed text-faint">
        {ms.length ? "Cada matéria guarda os seus resumos, provas e cartões. Em Tudo aparece o objetivo inteiro."
          : "Separe o objetivo em matérias: cada uma guarda os seus resumos, provas e cartões."}
      </p>
    </nav>
  );
}

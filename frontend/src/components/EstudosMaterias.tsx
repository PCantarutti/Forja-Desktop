import { useState } from "react";
import { api } from "../api";
import type { EstudosMateria, EstudosProjeto } from "../types";
import { Clipboard, Download, Edit, Plus, Trash } from "./icons";
import { acertoGeral, corAcerto, diasAte, rotulo } from "./estudosUi";

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
  const linha = "group block w-full rounded-[9px] px-2.5 pt-2 pb-[9px] text-left text-[13px]";
  const barra = (a: number | null, cor: string) => (
    <div className="mt-1.5 ml-4 h-[3px] overflow-hidden rounded-full bg-side" aria-hidden>
      <div className={`h-full rounded-full ${cor}`} style={{ width: `${a ?? 0}%` }} />
    </div>
  );
  const plano = props.projeto.revisao?.plano;
  const dias = diasAte(plano?.data);
  return (
    <nav aria-label="Matérias" className="flex w-64 shrink-0 flex-col overflow-y-auto border-r border-line bg-side">
      <div className="flex flex-col gap-2 border-b border-line px-4 pt-[18px] pb-3.5">
        <p className={rotulo}>Objetivo</p>
        <p className="line-clamp-2 text-[14.5px] leading-snug font-semibold text-pretty text-fg" title={props.projeto.titulo}>{props.projeto.titulo}</p>
        {plano?.data && dias != null && dias > 0 && (
          <span className="self-start rounded-full bg-raised px-2 py-[3px] font-mono text-[11px] text-muted">
            {dias} dias · prova {plano.data.slice(8, 10)}/{plano.data.slice(5, 7)}
          </span>
        )}
      </div>
      <div className="flex flex-col gap-0.5 px-2 pt-2.5">
      <button onClick={() => props.onEscolher(null)} aria-current={props.materia === null}
              className={`${linha} mb-2 ${props.materia === null ? "bg-raised text-fg" : "text-muted hover:bg-raised hover:text-fg"}`}>
        <span className="flex items-center gap-2">
          <span className="size-2 shrink-0 rounded-full bg-accent" />
          <span className="min-w-0 flex-1 truncate font-semibold">Tudo</span>
          {geral != null && <span className="font-mono text-[11px] text-muted">{geral}%</span>}
        </span>
        {barra(geral, "bg-accent")}
      </button>
      {!!ms.length && (
        <div className="flex items-center justify-between px-2.5 pb-1">
          <p className={rotulo}>Matérias · {ms.length}</p>
          <span className="font-mono text-[10.5px] text-faint">acerto</span>
        </div>
      )}
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
          <div key={m.id} className={`${linha} cursor-pointer ${props.materia === m.id ? "bg-raised text-fg" : "text-muted hover:bg-raised hover:text-fg"}`}
               onClick={() => props.onEscolher(m.id)} role="button" aria-current={props.materia === m.id}
               title={m.entregas ? `${m.acerto}% de acerto em ${m.entregas} entrega${m.entregas === 1 ? "" : "s"}` : "Nenhuma prova entregue ainda"}>
            <span className="flex items-center gap-2">
            <span className={`size-2 shrink-0 rounded-full ${corAcerto(m.acerto)}`} />
            <span className="min-w-0 flex-1 truncate">{m.nome}</span>
            <span className="font-mono text-[11px] text-faint group-hover:hidden">{m.acerto == null ? "—" : `${m.acerto}%`}</span>
            <span className="hidden shrink-0 gap-1 group-hover:flex">
              <button aria-label={`Renomear ${m.nome}`} title="Renomear" className="text-faint hover:text-fg"
                      onClick={(e) => { e.stopPropagation(); setEditando({ id: m.id, nome: m.nome }); }}>
                <Edit className="size-3.5" />
              </button>
              <button aria-label={`Tirar ${m.nome}`} title="Tirar a matéria" className="text-faint hover:text-err"
                      onClick={(e) => { e.stopPropagation(); setApagando(m.id); }}>
                <Trash className="size-3.5" />
              </button>
            </span>
            </span>
            {barra(m.acerto, corAcerto(m.acerto))}
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
      </div>
      <div className="mx-4 my-2 border-t border-line" />
      <div className="flex flex-col gap-0.5 px-2">
      <button onClick={props.onEdital} className={`${item} text-faint hover:bg-raised/60 hover:text-fg`}
              title="As matérias, o peso de cada uma e os tópicos, tirados do edital">
        <Clipboard className="size-3.5" /> Ler o edital
      </button>
      <button onClick={props.onTrazer} className={`${item} text-faint hover:bg-raised/60 hover:text-fg`}
              title="Juntar um estudo antigo neste objetivo, com todo o progresso dele">
        <Download className="size-3.5" /> Trazer um estudo
      </button>
      </div>
      <p className="mt-auto px-[18px] py-4 text-[11px] leading-relaxed text-faint">
        {ms.length ? "Cada matéria guarda os seus resumos, provas e cartões. Em Tudo aparece o objetivo inteiro."
          : "Separe o objetivo em matérias: cada uma guarda os seus resumos, provas e cartões."}
      </p>
    </nav>
  );
}

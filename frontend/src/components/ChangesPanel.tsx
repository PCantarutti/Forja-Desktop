import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChangeFile, GitStatus } from "../types";
import { DiffView } from "./MessageView";
import { Chevron, ExternalLink, FolderOpen } from "./icons";

const STATUS: Record<ChangeFile["status"], [string, string]> = {
  created: ["novo", "text-emerald-400"],
  modified: ["alterado", "text-amber-300"],
  deleted: ["apagado", "text-red-400"],
  unchanged: ["igual ao original", "text-faint"],
};

const GIT_LABEL: Record<string, string> = { modified: "M", untracked: "?", added: "A", deleted: "D", renamed: "R" };

export type ChangesAction = "commit" | "pr" | null;

/**
 * Aba Alterações: arquivos que o agente mudou nesta conversa (com diff) e o git da pasta:
 * commit com mensagem gerada pelo modelo, PR via gh e worktree por conversa.
 */
export default function ChangesPanel(props: {
  conv: number | null;
  provider: string;
  model: string;
  refreshKey: number; // muda quando um turno termina
  action: ChangesAction; // pedido vindo de /commit ou /pr
  onActionDone: () => void;
  onCount: (n: number) => void;
  onOpen: (path: string, mode: "editor" | "reveal") => void;
  onConversationChanged: () => void; // worktree trocou a pasta da conversa
}) {
  const { conv } = props;
  const [files, setFiles] = useState<ChangeFile[]>([]);
  const [git, setGit] = useState<GitStatus | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [gitDiff, setGitDiff] = useState<{ path: string; text: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [commitMsg, setCommitMsg] = useState<string | null>(null);
  const [pr, setPr] = useState<{ title: string; body: string } | null>(null);
  const [prUrl, setPrUrl] = useState("");
  const [branch, setBranch] = useState<string | null>(null);
  const [lastCommit, setLastCommit] = useState("");
  const pedido = useRef(0); // carga mais nova vence: resposta atrasada não pinta a tela

  async function load() {
    // Duas cargas em voo (troca de conversa, ou um refreshKey chegando no meio) e a mais lenta
    // sobrescrevia a mais nova — inclusive o onCount, que pinta o contador no cabeçalho do chat.
    const meu = ++pedido.current;
    if (conv === null) {
      setFiles([]);
      setGit(null);
      props.onCount(0);
      return;
    }
    try {
      const [c, g] = await Promise.all([
        api.get<{ files: ChangeFile[] }>(`/conversations/${conv}/changes`),
        api.get<GitStatus>(`/conversations/${conv}/git`),
      ]);
      if (meu !== pedido.current) return;
      const changed = c.files.filter((f) => f.status !== "unchanged");
      setFiles(changed);
      props.onCount(changed.length);
      setGit(g);
      setError("");
    } catch (e: any) {
      if (meu === pedido.current) setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, [conv, props.refreshKey]);

  useEffect(() => {
    if (props.action === "commit") startCommit();
    if (props.action === "pr") setPr({ title: "", body: "" });
    if (props.action) props.onActionDone();
  }, [props.action]);

  async function startCommit() {
    if (conv === null) return;
    setBusy("Gerando mensagem de commit com o modelo…");
    setError("");
    try {
      const r = await api.post<{ message: string }>(`/conversations/${conv}/git/commit`, {
        provider: props.provider,
        model: props.model,
        dry: true,
      });
      setCommitMsg(r.message);
    } catch (e: any) {
      setError(e.message);
    }
    setBusy("");
  }

  async function doCommit() {
    if (conv === null || !commitMsg?.trim()) return;
    setBusy("Fazendo commit…");
    try {
      const r = await api.post<{ sha: string; output: string }>(`/conversations/${conv}/git/commit`, { message: commitMsg });
      setLastCommit(`Commit ${r.sha} criado.`);
      setCommitMsg(null);
      await load();
    } catch (e: any) {
      setError(e.message);
    }
    setBusy("");
  }

  async function doPr() {
    if (conv === null || !pr) return;
    setBusy("Fazendo push e abrindo o PR…");
    try {
      const r = await api.post<{ url: string; output: string }>(`/conversations/${conv}/git/pr`, pr);
      setPrUrl(r.url || r.output);
      setPr(null);
      await load();
    } catch (e: any) {
      setError(e.message);
    }
    setBusy("");
  }

  async function doWorktree() {
    if (conv === null || !branch?.trim()) return;
    setBusy("Criando worktree…");
    try {
      const r = await api.post<{ path: string; branch: string }>(`/conversations/${conv}/git/worktree`, { branch: branch.trim() });
      setLastCommit(`Worktree criado em ${r.path} (branch ${r.branch}). A conversa passa a trabalhar lá a partir da próxima mensagem.`);
      setBranch(null);
      props.onConversationChanged();
      await load();
    } catch (e: any) {
      setError(e.message);
    }
    setBusy("");
  }

  async function showGitDiff(path: string) {
    if (conv === null) return;
    if (gitDiff?.path === path) return setGitDiff(null);
    try {
      const r = await api.get<{ diff: string }>(`/conversations/${conv}/git/diff?path=${encodeURIComponent(path)}`);
      setGitDiff({ path, text: r.diff || "(sem diff: arquivo binário ou vazio)" });
    } catch (e: any) {
      setError(e.message);
    }
  }

  const btn = "rounded-[9px] border border-line px-3 py-1 text-fg hover:bg-raised disabled:opacity-40";
  const primary = "rounded-[9px] bg-accent px-3 py-1 font-medium text-accent-fg hover:brightness-110 disabled:opacity-40";
  const iconBtn = "grid size-6 place-items-center rounded text-faint hover:bg-raised hover:text-fg";

  if (conv === null)
    return (
      <div className="p-3 text-xs">
        <div className="rounded-xl border border-line bg-surface p-3.5 text-muted">Abra uma conversa para ver as alterações e o git da pasta dela.</div>
      </div>
    );

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3 text-xs">
      {error && <div className="rounded-xl border border-red-500/30 px-3 py-2 text-red-300">{error}</div>}
      {busy && <div className="animate-pulse text-muted">{busy}</div>}
      {lastCommit && <div className="rounded-xl border border-emerald-500/30 px-3 py-2 text-emerald-200">{lastCommit}</div>}
      {prUrl && (
        <div className="rounded-xl border border-emerald-500/30 px-3 py-2 text-emerald-200">
          PR: {prUrl.startsWith("http") ? <a href={prUrl} target="_blank" rel="noreferrer" className="underline">{prUrl}</a> : prUrl}
        </div>
      )}

      <section className="rounded-xl border border-line bg-surface">
        <div className="flex items-center gap-2 px-3.5 py-2.5">
          <h3 className="text-[10.5px] font-medium tracking-[.08em] text-faint font-mono uppercase">Alterações do agente</h3>
          <span className="text-faint">{files.length} arquivo{files.length === 1 ? "" : "s"}</span>
          <button onClick={load} className="ml-auto text-muted hover:text-fg">atualizar</button>
        </div>
        {!files.length ? (
          <div className="border-t border-line px-3.5 py-3 text-muted">
            Nenhum arquivo alterado por write_file/edit_file nesta conversa. Mudanças por comandos aparecem só no git, abaixo.
          </div>
        ) : (
          files.map((f) => {
            const [label, cls] = STATUS[f.status];
            const isOpen = open === f.path;
            return (
              <div key={f.path} className="border-t border-line">
                <div className="flex items-center gap-2 px-3.5 py-2">
                  <button onClick={() => setOpen(isOpen ? null : f.path)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                    <Chevron className={`size-3.5 shrink-0 text-faint ${isOpen ? "" : "-rotate-90"}`} />
                    <span className="truncate font-mono text-fg" title={f.path}>
                      {f.path.split("/").pop()}
                    </span>
                    <span className={`shrink-0 ${cls}`}>{label}</span>
                    {f.binary ? (
                      <span className="shrink-0 text-faint">binário</span>
                    ) : (
                      <>
                        <span className="shrink-0 font-mono text-emerald-400">+{f.additions}</span>
                        <span className="shrink-0 font-mono text-red-400">−{f.deletions}</span>
                      </>
                    )}
                  </button>
                  <button className={iconBtn} title="Abrir no editor" onClick={() => props.onOpen(f.path, "editor")}>
                    <ExternalLink className="size-3.5" />
                  </button>
                  <button className={iconBtn} title="Revelar na pasta" onClick={() => props.onOpen(f.path, "reveal")}>
                    <FolderOpen className="size-3.5" />
                  </button>
                </div>
                {isOpen && (
                  <div className="px-3.5 pb-3">
                    <div className="mb-1 truncate font-mono text-faint">{f.path}</div>
                    {f.diff ? (
                      <DiffView preview={{ kind: "diff", path: f.path, text: f.diff }} />
                    ) : (
                      <div className="text-muted">
                        {f.binary
                          ? "Sem comparação: não há texto a extrair deste arquivo. Abra para ver."
                          : "(sem diff)"}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </section>

      <section className="rounded-xl border border-line bg-surface">
        <div className="flex items-center gap-2 px-3.5 py-2.5">
          <h3 className="text-[10.5px] font-medium tracking-[.08em] text-faint font-mono uppercase">Git</h3>
          {git?.repo && (
            <span className="truncate font-mono text-fg" title={git.remote}>
              {git.branch || "(sem branch)"}
              {git.ahead || git.behind ? <span className="text-faint"> · ↑{git.ahead ?? 0} ↓{git.behind ?? 0}</span> : null}
            </span>
          )}
        </div>
        {!git ? (
          <div className="border-t border-line px-3.5 py-3 text-muted">Consultando…</div>
        ) : !git.repo ? (
          <div className="border-t border-line px-3.5 py-3 text-muted">A pasta da conversa não é um repositório git.</div>
        ) : (
          <>
            <div className="border-t border-line px-3.5 py-2 text-faint">
              último commit: <span className="font-mono text-muted">{git.last_commit || "—"}</span>
            </div>
            <div className="border-t border-line">
              {!git.files?.length ? (
                <div className="px-3.5 py-2 text-muted">Árvore limpa.</div>
              ) : (
                git.files.map((f) => (
                  <div key={f.path}>
                    <button
                      onClick={() => showGitDiff(f.path)}
                      className="flex w-full items-center gap-2 px-3.5 py-1.5 text-left hover:bg-raised/50"
                    >
                      <span className={`w-3 font-mono ${f.status === "deleted" ? "text-red-400" : f.status === "untracked" ? "text-emerald-400" : "text-amber-300"}`}>
                        {GIT_LABEL[f.status] ?? f.status}
                      </span>
                      <span className="truncate font-mono text-fg">{f.path}</span>
                    </button>
                    {gitDiff?.path === f.path && (
                      <div className="px-3.5 pb-2">
                        <DiffView preview={{ kind: "diff", path: f.path, text: gitDiff.text }} />
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
            <div className="flex flex-wrap gap-2 border-t border-line px-3.5 py-2.5">
              <button className={primary} disabled={!!busy || !git.files?.length} onClick={startCommit} title="git add -A + mensagem gerada pelo modelo">
                Commit
              </button>
              <button
                className={btn}
                disabled={!!busy || !git.has_gh}
                onClick={() => setPr({ title: "", body: "" })}
                title={git.has_gh ? "git push + gh pr create" : "Precisa do GitHub CLI (gh) onde os comandos rodam"}
              >
                Criar PR
              </button>
              <button className={btn} disabled={!!busy} onClick={() => setBranch(`forja/${conv}`)} title="Nova branch num worktree irmão; a conversa passa a trabalhar lá">
                Worktree
              </button>
            </div>
            {commitMsg !== null && (
              <div className="space-y-2 border-t border-line px-3.5 py-3">
                <div className="text-muted">Mensagem (edite se quiser):</div>
                <textarea
                  value={commitMsg}
                  onChange={(e) => setCommitMsg(e.target.value)}
                  rows={Math.min(10, commitMsg.split("\n").length + 1)}
                  className="w-full rounded-lg border border-line bg-bg p-2 font-mono text-fg focus:outline-none"
                />
                <div className="flex gap-2">
                  <button className={primary} disabled={!!busy} onClick={doCommit}>Confirmar commit</button>
                  <button className={btn} onClick={() => setCommitMsg(null)}>Cancelar</button>
                </div>
              </div>
            )}
            {pr && (
              <div className="space-y-2 border-t border-line px-3.5 py-3">
                <input
                  value={pr.title}
                  onChange={(e) => setPr({ ...pr, title: e.target.value })}
                  placeholder="Título (vazio = usa o último commit)"
                  className="w-full rounded-lg border border-line bg-bg px-2 py-1 text-fg focus:outline-none"
                />
                <textarea
                  value={pr.body}
                  onChange={(e) => setPr({ ...pr, body: e.target.value })}
                  rows={4}
                  placeholder="Descrição (markdown)"
                  className="w-full rounded-lg border border-line bg-bg p-2 text-fg focus:outline-none"
                />
                <div className="flex gap-2">
                  <button className={primary} disabled={!!busy} onClick={doPr}>Push + criar PR</button>
                  <button className={btn} onClick={() => setPr(null)}>Cancelar</button>
                </div>
              </div>
            )}
            {branch !== null && (
              <div className="space-y-2 border-t border-line px-3.5 py-3">
                <input
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  placeholder="nome da branch"
                  className="w-full rounded-lg border border-line bg-bg px-2 py-1 font-mono text-fg focus:outline-none"
                />
                <div className="flex gap-2">
                  <button className={primary} disabled={!!busy} onClick={doWorktree}>Criar worktree</button>
                  <button className={btn} onClick={() => setBranch(null)}>Cancelar</button>
                </div>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

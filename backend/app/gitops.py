"""Git da pasta da conversa: status, diff, commit com mensagem gerada pelo modelo, PR (gh) e worktree.

Os comandos rodam como os do run_command: no sistema do usuário, na pasta da conversa. A mensagem de commit e o corpo do PR vão por arquivo (`.forja/`) para não brigar
com as aspas do PowerShell; o que sobra interpolado passa por `native.quote`.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import llm, native, shell, workspace
from .parsing import split_think
from .tools import ToolError

MAX_DIFF_CHARS = 12_000
COMMIT_PROMPT = (
    "Você escreve mensagens de commit. Dado o diff abaixo, responda SOMENTE com a mensagem, no formato "
    "Conventional Commits: primeira linha `tipo(escopo): resumo` com até 72 caracteres, em português, "
    "depois uma linha em branco e, se ajudar, até 5 tópicos curtos do que mudou. Sem markdown, sem aspas.")


def _run(root: Path, command: str, timeout: int = 60) -> tuple[int, str]:
    return shell.exec_in(root, command, timeout)


def _ok(root: Path, command: str, timeout: int = 60) -> str:
    code, out = _run(root, command, timeout)
    if code != 0:
        raise ToolError(out.strip() or f"`{command}` falhou (exit {code})")
    return out


def is_repo(root: Path) -> bool:
    code, out = _run(root, "git rev-parse --is-inside-work-tree", 20)
    return code == 0 and "true" in out


def status(root: Path) -> dict:
    """Estado resumido para a aba Alterações."""
    if not is_repo(root):
        return {"repo": False}
    branch = _run(root, "git branch --show-current", 20)[1].strip()
    code, porcelain = _run(root, "git status --porcelain=v1", 30)
    files = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        xy, path = line[:2], line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ")[-1]
        files.append({"status": "untracked" if xy == "??" else "modified" if "M" in xy else "added" if "A" in xy
                      else "deleted" if "D" in xy else "renamed" if "R" in xy else xy.strip(),
                      "path": path.strip('"'), "staged": xy[0] not in (" ", "?")})
    ahead = behind = None
    code, counts = _run(root, "git rev-list --left-right --count @{u}...HEAD", 20)
    if code == 0:
        m = re.match(r"\s*(\d+)\s+(\d+)", counts)
        if m:
            behind, ahead = int(m.group(1)), int(m.group(2))
    remote = _run(root, "git remote get-url origin", 20)[1].strip() if code is not None else ""
    has_gh = _run(root, "gh --version", 20)[0] == 0
    last = _run(root, "git log -1 --pretty=%h%x09%s", 20)[1].strip()
    return {"repo": True, "branch": branch, "files": files, "ahead": ahead, "behind": behind,
            "remote": remote if "fatal" not in remote else "", "has_gh": has_gh, "last_commit": last}


def diff(root: Path, path: str | None = None) -> str:
    """Diff do working tree (staged + unstaged) contra HEAD; untracked vira 'arquivo novo'."""
    target = f" -- {native.quote(path)}" if path else ""
    out = _run(root, f"git diff HEAD{target}", 60)[1]
    if path and not out.strip():
        p = root / path
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            out = f"--- /dev/null\n+++ b/{path}\n" + "".join(f"+{l}\n" for l in text.splitlines())
    return out[:200_000]


async def generate_message(root: Path, provider: str, model: str) -> str:
    _ok(root, "git add -A", 60)
    stat = _run(root, "git diff --cached --stat", 60)[1]
    patch = _run(root, "git diff --cached", 60)[1]
    if not patch.strip():
        raise ToolError("Nada para commitar: a árvore está limpa.")
    text = f"{stat}\n\n{patch[:MAX_DIFF_CHARS]}" + ("\n\n(diff truncado)" if len(patch) > MAX_DIFF_CHARS else "")
    out = ""
    async for kind, val in llm.chat_stream(provider, model, [{"role": "system", "content": COMMIT_PROMPT},
                                                             {"role": "user", "content": text}], None, 16_384):
        if kind == "content":
            out += val
    msg = split_think(out)[1].strip().strip("`").strip()
    if not msg:
        raise ToolError("O modelo não devolveu uma mensagem de commit.")
    return msg


def _write_forja_file(root: Path, name: str, text: str) -> str:
    folder = root / ".forja"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")
    return f".forja/{name}"


def commit(root: Path, message: str) -> dict:
    if not message.strip():
        raise ToolError("Mensagem de commit vazia.")
    _ok(root, "git add -A", 60)
    if not _run(root, "git diff --cached --quiet", 30)[0]:
        raise ToolError("Nada para commitar: a árvore está limpa.")
    rel = _write_forja_file(root, "commit-msg.txt", message)
    try:
        # .forja/ pode não estar no .gitignore: não deixa o arquivo da mensagem entrar no commit.
        _run(root, f"git reset -q -- {native.quote(rel)}", 20)
        out = _ok(root, f"git commit -F {native.quote(rel)}", 120)
    finally:
        try:
            (root / rel).unlink()
        except OSError:
            pass
    sha = _run(root, "git rev-parse --short HEAD", 20)[1].strip()
    return {"sha": sha, "output": out.strip(), "message": message}


def create_pr(root: Path, title: str, body: str) -> dict:
    st = status(root)
    if not st.get("repo"):
        raise ToolError("A pasta da conversa não é um repositório git.")
    if not st.get("has_gh"):
        raise ToolError("O GitHub CLI (gh) não está instalado ou não está no PATH onde os comandos rodam. "
                        "Instale com `winget install GitHub.cli` e faça `gh auth login`.")
    if st.get("files"):
        raise ToolError("Há alterações sem commit. Faça o commit antes de abrir o PR.")
    push = _ok(root, "git push -u origin HEAD", 180)
    rel = _write_forja_file(root, "pr-body.md", body or "")
    try:
        cmd = (f"gh pr create --title {native.quote(title)} --body-file {native.quote(rel)}"
               if title else "gh pr create --fill")
        out = _ok(root, cmd, 120)
    finally:
        try:
            (root / rel).unlink()
        except OSError:
            pass
    url = next((w for w in out.split() if w.startswith("http")), "")
    return {"url": url, "output": (push + "\n" + out).strip()}


def worktree(root: Path, branch: str) -> dict:
    """Cria um worktree irmão da raiz do repositório numa branch nova e devolve o caminho no sistema do usuário."""
    if not is_repo(root):
        raise ToolError("A pasta da conversa não é um repositório git.")
    top = Path(_ok(root, "git rev-parse --show-toplevel", 20).strip().replace("\\", "/"))
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-") or "forja"
    dest = f"{top.parent.as_posix()}/{top.name}-{slug}"
    _ok(root, f"git worktree add {native.quote(dest)} -b {native.quote(branch)}", 120)
    return {"path": workspace.normalize(dest), "branch": branch}

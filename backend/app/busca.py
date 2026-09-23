"""glob e grep: achar arquivo e achar texto sem passar pelo shell.

Sem isto a busca de código só existia via run_command (findstr/Select-String/grep), que muda de
sintaxe por sistema, pede aprovação e devolve saída sem limite. Portado do DeepSeek Harness
(fs/tool-fs-search): glob por padrão de caminho, grep por regex com teto de ocorrências.
"""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from .tools import IGNORED_DIRS, Tool, ToolError, _obj, _rel, register, resolve_leitura, resolve_path

MAX_GLOB = 100
MAX_GREP = 250
MAX_LINHA = 300          # trecho de cada ocorrência
MAX_ARQUIVO = 2_000_000  # arquivo maior que isto fica de fora do grep


def _arquivos(base: Path):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for n in filenames:
            yield Path(dirpath) / n


def _casa(padrao: str, rel: str) -> bool:
    """Padrão sem '/' casa o nome do arquivo em qualquer profundidade; com '/', o caminho relativo."""
    if "/" not in padrao:
        return fnmatch.fnmatch(rel.rsplit("/", 1)[-1], padrao)
    return fnmatch.fnmatch(rel, padrao) or (padrao.startswith("**/") and fnmatch.fnmatch(rel, padrao[3:]))


def glob(root: Path, args: dict) -> str:
    padrao = str(args.get("pattern") or "").strip().replace("\\", "/")
    if not padrao:
        raise ToolError("Informe 'pattern', ex.: '*.py' ou 'src/**/*.ts'.")
    base = resolve_path(root, args.get("path"))
    achados = [p for p in _arquivos(base) if _casa(padrao, p.relative_to(base).as_posix())]
    achados.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if not achados:
        return f"(nenhum arquivo casa com '{padrao}')"
    linhas = [_rel(root, p) for p in achados[:MAX_GLOB]]
    if len(achados) > MAX_GLOB:
        linhas.append(f"({len(achados)} arquivos; mostrando os {MAX_GLOB} mais recentes — refine o padrão)")
    return "\n".join(linhas)


def grep(root: Path, args: dict) -> str:
    try:
        rx = re.compile(str(args.get("pattern") or ""), 0 if args.get("case_sensitive") else re.I)
    except re.error as e:
        raise ToolError(f"Regex inválida: {e}. Escape caracteres especiais (\\( \\. \\[).") from e
    if not rx.pattern:
        raise ToolError("Informe 'pattern' (regex).")
    base = resolve_leitura(root, args.get("path"))
    include = str(args.get("include") or "").strip().replace("\\", "/")
    alvos = [base] if base.is_file() else _arquivos(base)
    out: list[str] = []
    total = 0
    # ponytail: varredura em Python puro; troca por ripgrep se virar gargalo em repositório gigante
    for p in alvos:
        if include and not _casa(include, p.name if "/" not in include else _rel(root, p)):
            continue
        try:
            if p.stat().st_size > MAX_ARQUIVO:
                continue
            dados = p.read_bytes()
        except OSError:
            continue
        if b"\x00" in dados[:8192]:
            continue
        for i, linha in enumerate(dados.decode("utf-8", errors="replace").splitlines(), 1):
            if rx.search(linha):
                total += 1
                if len(out) < MAX_GREP:
                    out.append(f"{_rel(root, p)}:{i}: {linha.strip()[:MAX_LINHA]}")
    if not out:
        return f"(nenhuma ocorrência de /{rx.pattern}/)"
    if total > len(out):
        out.append(f"({total} ocorrências; mostrando as {MAX_GREP} primeiras — refine o padrão ou use 'include')")
    return "\n".join(out)


register(Tool(
    "glob",
    "Acha arquivos por padrão de caminho. Padrão sem '/' casa o nome em qualquer profundidade ('*.py' "
    "pega todos os .py da árvore); com '/', o caminho relativo ('src/**/*.ts'). Devolve só arquivos, "
    f"os {MAX_GLOB} mais recentes primeiro. Ignora .git, node_modules e afins.",
    _obj({"pattern": {"type": "string", "description": "Padrão glob, ex.: '*.tsx' ou 'app/**/test_*.py'"},
          "path": {"type": "string", "description": "Pasta onde procurar. Padrão: '.'"}}, ["pattern"]),
    glob))
register(Tool(
    "grep",
    "Procura texto (regex) no conteúdo dos arquivos e devolve 'arquivo:linha: trecho'. Sem diferenciar "
    f"maiúsculas por padrão; até {MAX_GREP} ocorrências. Leia o arquivo achado com read_file quando "
    "precisar do contexto em volta.",
    _obj({"pattern": {"type": "string", "description": "Expressão regular (sintaxe Python)"},
          "path": {"type": "string", "description": "Pasta ou arquivo. Padrão: '.'"},
          "include": {"type": "string", "description": "Filtro de arquivos, ex.: '*.py'"},
          "case_sensitive": {"type": "boolean", "description": "Diferenciar maiúsculas. Padrão: false"}},
         ["pattern"]),
    grep))

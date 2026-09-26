"""Relatórios do explorador guardados no projeto (.forja/exploracoes/EXP-NNN.md).

Explorar é caro (um subagente lendo dezenas de arquivos) e o resultado é conhecimento, não código:
se ficasse só na conversa, a primeira compactação o apagaria e a Maestro exploraria tudo de novo. No
Project State ele sobrevive à compactação e ao reinício do app, entra resumido no prompt (índice) e
inteiro no contrato do Worker que precisar dele.

Cada relatório guarda os arquivos que cita com o mtime da hora: arquivo citado que mudou depois deixa
a exploração marcada como desatualizada, em vez de a Maestro confiar em mapa velho.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

PASTA = ".forja/exploracoes"
MAX_INDICE = 8          # explorações listadas no prompt da Maestro
MAX_NO_CONTRATO = 3000  # do relatório que vai para o Worker
_CITACAO = re.compile(r"([\w./\\-]+\.[A-Za-z0-9]{1,6})(?::\d+)?")


def _pasta(root: Path) -> Path:
    return root / PASTA


def _citados(root: Path, texto: str) -> dict[str, int]:
    """Arquivos do projeto citados no relatório (caminho:linha), com o mtime de agora."""
    out: dict[str, int] = {}
    for m in _CITACAO.finditer(texto):
        rel = m.group(1).replace("\\", "/").removeprefix("./")
        p = root / rel
        try:
            if p.is_file() and p.resolve().is_relative_to(root.resolve()):
                out[rel] = p.stat().st_mtime_ns
        except OSError:
            continue
    return out


def grava(root: Path, pergunta: str, paths: list[str], relatorio: str) -> str:
    """Grava o relatório e devolve o id (EXP-001)."""
    pasta = _pasta(root)
    pasta.mkdir(parents=True, exist_ok=True)
    numeros = [int(m.group(1)) for p in pasta.glob("EXP-*.md") if (m := re.match(r"EXP-(\d+)", p.stem))]
    eid = f"EXP-{max(numeros, default=0) + 1:03}"
    meta = {"pergunta": pergunta, "paths": paths, "data": datetime.now().isoformat(timespec="seconds"),
            "citados": _citados(root, relatorio)}
    (pasta / f"{eid}.md").write_text(f"<!-- {json.dumps(meta, ensure_ascii=False)} -->\n# {eid}: {pergunta}\n\n"
                                     f"{relatorio.strip()}\n", encoding="utf-8", newline="\n")
    return eid


def _le(p: Path) -> tuple[dict, str]:
    texto = p.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"<!-- (.*?) -->\n", texto)
    try:
        meta = json.loads(m.group(1)) if m else {}
    except ValueError:
        meta = {}
    return meta, texto[m.end():] if m else texto


def desatualizada(root: Path, meta: dict) -> list[str]:
    """Arquivos citados que mudaram (ou sumiram) depois da exploração."""
    mudaram = []
    for rel, mtime in (meta.get("citados") or {}).items():
        p = root / rel
        try:
            if p.stat().st_mtime_ns != mtime:
                mudaram.append(rel)
        except OSError:
            mudaram.append(rel)
    return mudaram


def indice(root: Path) -> str:
    """Bloco curto para o prompt: as explorações já feitas, a mais recente primeiro."""
    pasta = _pasta(root)
    if not pasta.is_dir():
        return ""
    linhas = []
    for p in sorted(pasta.glob("EXP-*.md"), reverse=True)[:MAX_INDICE]:
        meta, _ = _le(p)
        mudou = desatualizada(root, meta)
        linhas.append(f"- {p.stem}: {meta.get('pergunta', '')[:120]}"
                      + (f" (DESATUALIZADA: {', '.join(mudou[:3])} mudou)" if mudou else ""))
    if not linhas:
        return ""
    return ("Explorações já feitas (leia com read_file em " + PASTA + "/EXP-NNN.md antes de explorar de novo; "
            "passe no contrato com explorations=[...]):\n" + "\n".join(linhas))


def para_contrato(root: Path, ids: list[str]) -> str:
    """Os relatórios pedidos no contrato, com teto, para o briefing do Worker."""
    partes = []
    for eid in ids:
        p = _pasta(root) / f"{str(eid).strip().upper()}.md"
        if not p.is_file():
            continue
        meta, corpo = _le(p)
        aviso = " (ATENÇÃO: arquivos citados mudaram desde então)" if desatualizada(root, meta) else ""
        partes.append(f"{p.stem}{aviso}\n{corpo.strip()[:MAX_NO_CONTRATO]}")
    return "\n\n".join(partes)

"""Modelos da tela Design: um design pronto guardado para começar outros projetos dele, na hora e sem
IA (o projeto novo nasce com ele como v1).

Um arquivo por modelo em DATA_DIR/design_modelos/<id>.json ({id, nome, tipo, html, criado}): o HTML
pode ter fotos embutidas (MBs), então nada de um arquivo único reescrito a cada mudança.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone

from . import config, design_html
from .tools import ToolError

PASTA = config.DATA_DIR / "design_modelos"
_ID = re.compile(r"^[a-z0-9-]{3,60}$")


def _arquivo(mid: str):
    if not _ID.match(mid or ""):
        raise ToolError("Modelo inválido.")
    return PASTA / f"{mid}.json"


def _tipo(html: str) -> str:
    return "slides" if design_html.e_slides(html) else "prototipo" if design_html.e_prototipo(html) else \
        "paginas" if design_html.paginas(html) else "site"


def listar() -> list[dict]:
    """Sem o HTML (a tela pede cada um para a miniatura)."""
    out = []
    for f in sorted(PASTA.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True) if PASTA.is_dir() else []:
        try:
            d = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: d[k] for k in ("id", "nome", "tipo", "criado")} | {"tamanho": len(d.get("html", ""))})
    return out


def pegar(mid: str) -> dict:
    f = _arquivo(mid)
    if not f.is_file():
        raise ToolError("Modelo não encontrado.")
    return json.loads(f.read_text("utf-8"))


def salvar(nome: str, html: str) -> dict:
    nome = " ".join((nome or "").split())[:80]
    if not nome:
        raise ToolError("Dê um nome ao modelo.")
    if not html:
        raise ToolError("Não há design para guardar.")
    PASTA.mkdir(parents=True, exist_ok=True)
    mid = f"{design_html.slug(nome) or 'modelo'}-{secrets.token_hex(2)}"
    d = {"id": mid, "nome": nome, "tipo": _tipo(html), "html": html,
         "criado": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    tmp = _arquivo(mid).with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
    tmp.replace(_arquivo(mid))
    return {k: d[k] for k in ("id", "nome", "tipo", "criado")}


def apagar(mid: str) -> list[dict]:
    f = _arquivo(mid)
    if f.is_file():
        f.unlink()
    return listar()

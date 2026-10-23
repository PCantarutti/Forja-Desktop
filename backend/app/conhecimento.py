"""O que o assistente de voz sabe da pessoa além da conversa: os documentos dela e as notas rápidas.

- Documentos: as pastas escolhidas na página do assistente (contratos, notas, PDFs, um cofre do Obsidian) viram um
  índice FTS5 com bm25, como o code_search do agente (codebusca.py), em DATA_DIR/indices/documentos.db. PDF, Word,
  planilha e apresentação passam pelo documentos.extrair; .md e .txt são lidos direto. Incremental por mtime: só
  relê o que mudou. Tudo local; o que sai daqui é DADO para o modelo responder citando o arquivo.
- Notas: "anota: comprar café" em DATA_DIR/notas.json, com a data, para "o que eu anotei ontem?".
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from . import config
from .sessoes import consulta_fts

EXTS_TEXTO = {".md", ".txt", ".markdown", ".rst", ".org"}
EXTS_DOC = {".pdf", ".docx", ".xlsx", ".pptx", ".csv"}
MAX_ARQUIVO = 40_000_000
MAX_ARQUIVOS = 5000
TRECHO = 1200        # caracteres por trecho (com sobreposição de 200)
IGNORAR = {".git", ".obsidian", ".trash", "node_modules", "__pycache__", ".venv", "venv"}
_trava = threading.Lock()
_estado = {"arquivos": 0, "indexando": False, "erro": ""}


def _banco() -> sqlite3.Connection:
    pasta = config.DATA_DIR / "indices"
    pasta.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(pasta / "documentos.db", timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS arquivos (path TEXT PRIMARY KEY, mtime INTEGER, tam INTEGER)")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS trechos USING fts5(path, titulo, texto, "
              "tokenize='unicode61 remove_diacritics 2')")
    return c


def _texto(p: Path) -> str:
    if p.suffix.lower() in EXTS_TEXTO:
        return p.read_text("utf-8", errors="replace")
    from . import documentos
    return documentos.extrair(p) or ""


def _pedacos(texto: str) -> list[str]:
    texto = re.sub(r"[ \t]+", " ", texto)
    return [texto[i:i + TRECHO] for i in range(0, max(1, len(texto)), TRECHO - 200)] if texto.strip() else []


def _arquivos(pastas: list[str]):
    for raiz in pastas:
        for pasta, dirs, nomes in os.walk(raiz):
            dirs[:] = [d for d in dirs if d not in IGNORAR and not d.startswith("$")]
            for n in nomes:
                p = Path(pasta, n)
                if p.suffix.lower() in EXTS_TEXTO | EXTS_DOC:
                    yield p


def atualiza(pastas: list[str]) -> int:
    """Índice em dia com as pastas: devolve quantos arquivos foram (re)lidos agora."""
    with _trava:
        _estado["indexando"] = True
        try:
            c = _banco()
            vistos = {p: (m, t) for p, m, t in c.execute("SELECT path, mtime, tam FROM arquivos")}
            no_disco, mudou = set(), 0
            for p in _arquivos(pastas):
                if len(no_disco) >= MAX_ARQUIVOS:
                    break
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size > MAX_ARQUIVO:
                    continue
                chave = str(p)
                no_disco.add(chave)
                if vistos.get(chave) == (st.st_mtime_ns, st.st_size):
                    continue
                try:
                    pedacos = _pedacos(_texto(p))
                except Exception as e:  # PDF protegido, arquivo aberto no Word: fica de fora, o resto segue
                    print(f"Forja: documentos: {p.suffix} ilegível ({e.__class__.__name__})", flush=True)
                    pedacos = []
                c.execute("DELETE FROM trechos WHERE path = ?", (chave,))
                c.executemany("INSERT INTO trechos (path, titulo, texto) VALUES (?,?,?)",
                              [(chave, p.stem.replace("_", " ").replace("-", " "), t) for t in pedacos])
                c.execute("INSERT OR REPLACE INTO arquivos VALUES (?,?,?)", (chave, st.st_mtime_ns, st.st_size))
                mudou += 1
                if mudou % 50 == 0:
                    c.commit()
            for chave in set(vistos) - no_disco:
                c.execute("DELETE FROM trechos WHERE path = ?", (chave,))
                c.execute("DELETE FROM arquivos WHERE path = ?", (chave,))
            c.commit()
            _estado.update(arquivos=len(no_disco), erro="")
            c.close()
            return mudou
        except (OSError, sqlite3.Error) as e:
            _estado["erro"] = str(e)[:200]
            raise
        finally:
            _estado["indexando"] = False


def estado() -> dict:
    return dict(_estado)


VAZIAS = {"quando", "qual", "quais", "onde", "como", "que", "meu", "minha", "meus", "minhas", "esta", "está", "tem",
          "tenho", "sobre", "para", "pra", "com", "uma", "um", "dos", "das", "nos", "nas", "foi", "ser", "sao", "são"}


def busca(pergunta: str, pastas: list[str], n: int = 6) -> list[dict]:
    """Os trechos que mais casam com a pergunta (nome do arquivo pesa mais), no máximo 2 por arquivo."""
    if not pastas:
        return []
    atualiza(pastas)
    palavras = " ".join(w for w in re.findall(r"\w+", pergunta.lower()) if len(w) >= 3 and w not in VAZIAS)
    consulta = consulta_fts(palavras)
    if not consulta:
        return []
    c = _banco()
    try:
        linhas = c.execute("SELECT path, texto, bm25(trechos, 2.0, 3.0, 1.0) AS r FROM trechos WHERE trechos MATCH ? "
                           "ORDER BY r LIMIT 60", (consulta,)).fetchall()
    finally:
        c.close()
    dentro = [str(Path(p)) for p in pastas]
    saida, por_arquivo = [], {}
    for path, texto, _ in linhas:
        if not any(path.startswith(d) for d in dentro) or por_arquivo.get(path, 0) >= 2:
            continue  # pasta tirada da lista: o índice ainda tem, mas não vale mais
        por_arquivo[path] = por_arquivo.get(path, 0) + 1
        saida.append({"arquivo": Path(path).name, "caminho": path, "trecho": texto})
        if len(saida) >= n:
            break
    return saida


# ------------------------------------------------------------------ notas rápidas

NOTAS = config.DATA_DIR / "notas.json"
_trava_notas = threading.Lock()


def notas() -> list[dict]:
    try:
        return json.loads(NOTAS.read_text("utf-8"))
    except (OSError, ValueError):
        return []


def anotar(texto: str) -> dict:
    item = {"id": uuid.uuid4().hex[:10], "texto": texto.strip()[:500], "quando": datetime.now().isoformat(timespec="seconds")}
    with _trava_notas:
        NOTAS.parent.mkdir(parents=True, exist_ok=True)
        NOTAS.write_text(json.dumps(notas() + [item], ensure_ascii=False, indent=1), "utf-8")
    return item


def apagar_notas(ids: set[str]) -> list[dict]:
    with _trava_notas:
        resto = [x for x in notas() if x["id"] not in ids]
        NOTAS.write_text(json.dumps(resto, ensure_ascii=False, indent=1), "utf-8")
    return resto


def notas_do_dia(dia) -> list[dict]:
    return [x for x in notas() if datetime.fromisoformat(x["quando"]).date() == dia]

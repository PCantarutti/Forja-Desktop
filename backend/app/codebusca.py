"""code_search: "onde o projeto trata X?" quando o nome exato não é conhecido.

O grep acha texto exato; aqui é um índice FTS5 por projeto, com ranking bm25, sobre trechos de
arquivo (caminho, símbolos do `ast`, identificadores quebrados em palavras e o texto). A indexação é
incremental por mtime e acontece na própria chamada: o primeiro uso num repositório grande leva
alguns segundos, os seguintes só releem o que mudou.

O índice é cache, não memória do projeto: mora em DATA_DIR/indices, não no `.forja/`.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from pathlib import Path

from . import config
from .codigo import LINGUAS, _analisaveis, simbolos
from .sessoes import consulta_fts
from .tools import Tool, ToolError, _obj, _rel, register, resolve_path

EXTS = set(LINGUAS) | {
    ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte", ".astro",
    ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".cs", ".fs", ".php", ".rb", ".swift", ".dart", ".lua",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".m", ".sh", ".bash", ".ps1", ".bat", ".sql", ".graphql", ".proto",
    ".gradle", ".tf", ".r", ".jl", ".ex", ".exs", ".erl", ".clj", ".zig", ".nim", ".prisma"}
MAX_ARQUIVO = 400_000       # maior que isto é gerado, minificado ou dado
MAX_ARQUIVOS = 20_000
LINHAS_TRECHO = 40
MAX_RESULTADOS = 10
MAX_LINHAS_MOSTRADAS = 3
PESOS = (4.0, 6.0, 1.5, 1.0)  # bm25 por coluna: path, simbolos, partes, texto
# Documentação e dado citam o assunto muito mais vezes que o código que o implementa; sem desconto,
# "login" no mesaflow trazia 10 .md e .json antes do LoginScreen.tsx.
TEXTO_CORRIDO = {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".htm"}
PESO_TEXTO_CORRIDO = 0.35
PESO_TESTE = 0.6  # teste repete o assunto em cada caso; quem pergunta "onde se trata X" quer a implementação
TESTE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|\.(test|spec)\.[^/]+$")
_TRAVAS: dict[str, threading.Lock] = {}
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _banco(root: Path) -> sqlite3.Connection:
    pasta = config.DATA_DIR / "indices"
    pasta.mkdir(parents=True, exist_ok=True)
    nome = hashlib.sha1(str(root.resolve()).lower().encode()).hexdigest()[:16]
    c = sqlite3.connect(pasta / f"{nome}.db", timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS arquivos (path TEXT PRIMARY KEY, mtime INTEGER, tam INTEGER)")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS trechos USING fts5(path, simbolos, partes, texto, "
              "linha UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
    return c


def partes(texto: str) -> str:
    """Identificadores camelCase quebrados em palavras: `handleLoginForm` vira `handle Login Form`, para
    `login` achar. snake_case o tokenizer já separa sozinho."""
    ids = {i for i in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", texto) if _CAMEL.search(i)}
    return " ".join(_CAMEL.sub(" ", i) for i in sorted(ids))


def _trechos(root: Path, p: Path) -> list[tuple]:
    try:
        dados = p.read_bytes()
    except OSError:
        return []
    if b"\0" in dados[:4096]:
        return []
    linhas = dados.decode("utf-8", "replace").splitlines()
    try:
        simbs = simbolos(p)[0] if p.suffix.lower() in LINGUAS else []
    except (ToolError, SyntaxError, ValueError, RecursionError):
        simbs = []
    rel = _rel(root, p)
    saida = []
    for ini in range(0, max(1, len(linhas)), LINHAS_TRECHO):
        corpo = "\n".join(linhas[ini:ini + LINHAS_TRECHO])
        nomes = " ".join(s["nome"] for s in simbs if ini < s["ini"] <= ini + LINHAS_TRECHO)
        saida.append((rel, nomes, partes(nomes + "\n" + corpo), corpo, ini + 1))
    return saida


def atualiza(root: Path) -> tuple[sqlite3.Connection, int]:
    """Índice em dia com o disco: (conexão, arquivos reindexados agora)."""
    root = root.resolve()
    trava = _TRAVAS.setdefault(str(root).lower(), threading.Lock())
    with trava:
        c = _banco(root)
        vistos = {p: (m, t) for p, m, t in c.execute("SELECT path, mtime, tam FROM arquivos")}
        no_disco: dict[str, Path] = {}
        mudou = 0
        for p in _analisaveis(root, root, EXTS, ocultas=False):
            if len(no_disco) >= MAX_ARQUIVOS:
                break
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > MAX_ARQUIVO:
                continue
            rel = _rel(root, p)
            no_disco[rel] = p
            if vistos.get(rel) == (st.st_mtime_ns, st.st_size):
                continue
            # ponytail: DELETE por path varre a tabela FTS; guardar os rowids por arquivo se ficar lento
            c.execute("DELETE FROM trechos WHERE path = ?", (rel,))
            c.executemany("INSERT INTO trechos (path, simbolos, partes, texto, linha) VALUES (?,?,?,?,?)",
                          _trechos(root, p))
            c.execute("INSERT OR REPLACE INTO arquivos VALUES (?,?,?)", (rel, st.st_mtime_ns, st.st_size))
            mudou += 1
        for rel in set(vistos) - set(no_disco):
            c.execute("DELETE FROM trechos WHERE path = ?", (rel,))
            c.execute("DELETE FROM arquivos WHERE path = ?", (rel,))
        c.commit()
        return c, mudou


def _linhas_relevantes(texto: str, inicio: int, termos: list[str]) -> list[str]:
    pontua = []
    for i, linha in enumerate(texto.splitlines()):
        baixa = linha.lower()
        n = sum(t in baixa for t in termos)
        if n and linha.strip():
            pontua.append((-n, i, f"  L{inicio + i}: {linha.strip()[:160]}"))
    return [x[2] for x in sorted(pontua)[:MAX_LINHAS_MOSTRADAS]]


def code_search(root: Path, args: dict) -> str:
    pergunta = str(args.get("query") or "").strip()
    consulta = consulta_fts(pergunta)
    if not consulta:
        raise ToolError("Informe 'query' com as palavras do que procura (ex.: 'login senha autenticação').")
    base = resolve_path(root, args.get("path") or ".")
    if not base.is_dir():  # sem isto, pasta inventada virava "nada casa" e o modelo concluía que não existe
        raise ToolError(f"Não é uma pasta do projeto: '{args.get('path')}'. Veja as pastas com tree.")
    prefixo = "" if base == root.resolve() else _rel(root, base).rstrip("/") + "/"
    c, novos = atualiza(root)
    try:
        linhas = c.execute(
            f"SELECT path, simbolos, texto, linha, bm25(trechos, {', '.join(map(str, PESOS))}) AS r FROM trechos "
            "WHERE trechos MATCH ? ORDER BY r LIMIT 500", (consulta,)).fetchall()
    finally:
        c.close()
    termos = re.findall(r"\w+", pergunta.lower())
    termos = [t for t in termos if len(t) >= 2]
    por_arquivo: dict[str, list] = {}
    peso = (lambda path: (PESO_TEXTO_CORRIDO if Path(path).suffix.lower() in TEXTO_CORRIDO else 1.0)
            * (PESO_TESTE if TESTE.search(path) else 1.0))
    linhas.sort(key=lambda x: x[4] * peso(x[0]))  # bm25 é negativo: menor = melhor
    for path, simbs, texto, linha, _r in linhas:
        if path.startswith(prefixo) and (path in por_arquivo or len(por_arquivo) < MAX_RESULTADOS):
            por_arquivo.setdefault(path, []).append((simbs, texto, linha))
    if not por_arquivo:
        return f"Nada no projeto casa com '{pergunta}'. Tente sinônimos, o termo em inglês ou o grep."
    saida = []
    for path, trechos in por_arquivo.items():
        nomes = list(dict.fromkeys(" ".join(s for s, _, _ in trechos[:2]).split()))
        cabeca = f"{path}" + (f"  — {', '.join(nomes[:4])}" if nomes else "")
        saida.append(cabeca)
        for _, texto, linha in trechos[:2]:
            saida += _linhas_relevantes(texto, linha, termos)
    nota = f"\n(índice atualizado: {novos} arquivo(s) relidos)" if novos else ""
    return ("\n".join(saida) + "\n(ordem: o mais relevante primeiro. Abra com read_file nas linhas "
            "indicadas ou veja a estrutura com ast.)" + nota)


register(Tool(
    "code_search",
    "Busca por assunto no código do projeto, com ranking: responde 'onde se trata X?' quando você NÃO sabe "
    "o nome exato do arquivo ou da função (para texto exato, use grep). Olha caminho, nomes de funções e "
    "classes e o conteúdo. Passe várias palavras, de preferência também em inglês, como aparecem no código "
    "(ex.: 'login auth senha password session').",
    _obj({"query": {"type": "string", "description": "Palavras do assunto procurado"},
          "path": {"type": "string", "description": "Só dentro desta subpasta. Padrão: o projeto inteiro"}},
         ["query"]),
    code_search))

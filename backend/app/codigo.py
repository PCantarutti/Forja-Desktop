"""tree, ast e imports: entender a forma do código sem language server.

O `lsp` só aparece com um servidor instalado no PATH (na máquina típica, nenhum); grep acha texto, não
estrutura. Aqui:
- tree: a árvore de pastas indentada, para a primeira olhada num repositório.
- ast: esqueleto de um arquivo (classes, funções, linhas), o fonte de um símbolo pelo nome, o nó numa
  posição e consultas estruturais (S-expression do tree-sitter).
- imports: o que um arquivo importa, quem importa ele, o grafo interno e os ciclos.

Python usa o `ast` da stdlib; TS/TSX/JS usam o tree-sitter (pacotes por linguagem, ~0,5 MB). Sem o
tree-sitter instalado, tudo continua funcionando para Python.
"""
from __future__ import annotations

import ast as pyast
import fnmatch
import json
import os
import re
import warnings
from collections import OrderedDict
from pathlib import Path

from .tools import IGNORED_DIRS, Tool, ToolError, _obj, _rel, register, resolve_leitura, resolve_path

LINGUAS = {".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
           ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript", ".mts": "typescript",
           ".cts": "typescript", ".tsx": "tsx"}
JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
MAX_ARQUIVO = 1_000_000     # arquivo maior não é analisado (minificado, gerado)
MAX_CACHE = 64              # árvores de sintaxe guardadas (por caminho, mtime e tamanho)
MAX_OUTLINE_PASTA = 30      # arquivos no outline de uma pasta
MAX_QUERY = 250             # ocorrências de uma consulta, como o teto do grep
MAX_INDICE = 5000           # arquivos no índice de imports
MAX_CICLOS = 20
MAX_TREE_LINHAS = 500       # como o MAX_LIST_ENTRIES do list_dir
MAX_FILHOS = 25             # filhos de uma pasta antes do "… +N"
TREE_IGNORADOS = IGNORED_DIRS | {"dist", "build", ".next", "out", "coverage", ".pytest_cache", ".mypy_cache"}


# ------------------------------------------------------------------ parsers

_TS: dict[str, object] = {}


def _ts_parser(lingua: str):
    """Parser do tree-sitter para a linguagem, ou None se o pacote não estiver instalado."""
    if lingua in _TS:
        return _TS[lingua]
    try:
        import tree_sitter as ts
        if lingua == "python":
            import tree_sitter_python as mod
            lang = ts.Language(mod.language())
        elif lingua == "javascript":
            import tree_sitter_javascript as mod
            lang = ts.Language(mod.language())
        else:
            import tree_sitter_typescript as mod
            lang = ts.Language(mod.language_tsx() if lingua == "tsx" else mod.language_typescript())
        _TS[lingua] = (ts.Parser(lang), lang)
    except ImportError:
        _TS[lingua] = None
    return _TS[lingua]


def tem_tree_sitter() -> bool:
    return _ts_parser("typescript") is not None


_CACHE: OrderedDict = OrderedDict()


def _fonte(p: Path) -> bytes:
    if not p.is_file():
        raise ToolError(f"Arquivo não encontrado: {p.name}")
    if p.stat().st_size > MAX_ARQUIVO:
        raise ToolError(f"{p.name} tem mais de {MAX_ARQUIVO // 1_000_000} MB: grande demais para analisar.")
    return p.read_bytes()


def _lingua(p: Path) -> str:
    lingua = LINGUAS.get(p.suffix.lower())
    if not lingua:
        raise ToolError(f"Sem parser para '{p.suffix or p.name}'. Suportado: Python, JS/JSX, TS/TSX.")
    return lingua


def _arvore(p: Path, lingua: str):
    """(árvore, fonte) em cache por (caminho, mtime, tamanho). Python: ast da stdlib, ou tree-sitter se
    `lingua` pedir a versão dele (consulta estrutural, arquivo com erro de sintaxe)."""
    st = p.stat()
    chave = (str(p), st.st_mtime_ns, st.st_size, lingua)
    if chave in _CACHE:
        _CACHE.move_to_end(chave)
        return _CACHE[chave]
    fonte = _fonte(p)
    if lingua == "python-ast":
        with warnings.catch_warnings():  # escape inválido no arquivo analisado não é problema nosso
            warnings.simplefilter("ignore")
            arvore = pyast.parse(fonte, filename=p.name)  # SyntaxError sobe para quem chamou
    else:
        par = _ts_parser(lingua)
        if par is None:
            raise ToolError("O parser de TS/JS (tree-sitter) não está instalado neste Forja: "
                            "só arquivos Python são analisados.")
        arvore = par[0].parse(fonte)
    _CACHE[chave] = (arvore, fonte)
    while len(_CACHE) > MAX_CACHE:
        _CACHE.popitem(last=False)
    return arvore, fonte


# ------------------------------------------------------------------ símbolos

def _simbolos_py(arvore) -> list[dict]:
    """[{nome, tipo, assinatura, ini, fim, doc, nivel}] em ordem, métodos com nome Classe.metodo."""
    out: list[dict] = []

    def visita(nos, prefixo: str, nivel: int):
        for n in nos:
            if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef, pyast.ClassDef)):
                eh_classe = isinstance(n, pyast.ClassDef)
                if eh_classe:
                    bases = ", ".join(pyast.unparse(b) for b in n.bases)
                    assin = f"class {n.name}" + (f"({bases})" if bases else "")
                else:
                    ret = f" -> {pyast.unparse(n.returns)}" if n.returns else ""
                    assin = (f"{'async ' if isinstance(n, pyast.AsyncFunctionDef) else ''}def "
                             f"{n.name}({pyast.unparse(n.args)}){ret}")
                doc = (pyast.get_docstring(n) or "").strip().splitlines()
                out.append({"nome": prefixo + n.name, "tipo": "class" if eh_classe else "def",
                            "assinatura": assin, "ini": n.lineno, "fim": n.end_lineno or n.lineno,
                            "doc": doc[0][:100] if doc else "", "nivel": nivel})
                visita(n.body, f"{prefixo}{n.name}.", nivel + 1)
    visita(arvore.body, "", 0)
    return out


_TS_SIMBOLOS = {"function_declaration": "function", "generator_function_declaration": "function",
                "class_declaration": "class", "abstract_class_declaration": "class",
                "method_definition": "method", "interface_declaration": "interface",
                "type_alias_declaration": "type", "enum_declaration": "enum",
                "function_definition": "def", "class_definition": "class"}


def _simbolos_ts(arvore, fonte: bytes) -> list[dict]:
    out: list[dict] = []

    def nome_de(n) -> str:
        c = n.child_by_field_name("name")
        return fonte[c.start_byte:c.end_byte].decode("utf-8", "replace") if c else ""

    def visita(n, prefixo: str, nivel: int):
        for c in n.children:
            tipo = _TS_SIMBOLOS.get(c.type)
            if c.type in ("lexical_declaration", "variable_declaration"):
                # const f = () => …  /  const f = function …: função com nome de variável
                for d in c.children:
                    v = d.child_by_field_name("value") if d.type == "variable_declarator" else None
                    if v is not None and v.type in ("arrow_function", "function_expression", "function"):
                        nome = nome_de(d)
                        out.append({"nome": prefixo + nome, "tipo": "function",
                                    "assinatura": _primeira_linha(fonte, c), "ini": c.start_point[0] + 1,
                                    "fim": c.end_point[0] + 1, "doc": "", "nivel": nivel})
                continue
            if tipo:
                nome = nome_de(c)
                out.append({"nome": prefixo + nome, "tipo": tipo, "assinatura": _primeira_linha(fonte, c),
                            "ini": c.start_point[0] + 1, "fim": c.end_point[0] + 1, "doc": "", "nivel": nivel})
                corpo = c.child_by_field_name("body")
                if corpo is not None and tipo in ("class", "interface"):
                    visita(corpo, f"{prefixo}{nome}.", nivel + 1)
            elif c.type in ("export_statement", "program", "module", "decorated_definition", "block"):
                visita(c, prefixo, nivel)
    visita(arvore.root_node, "", 0)
    return out


def _primeira_linha(fonte: bytes, n) -> str:
    texto = fonte[n.start_byte:n.end_byte].decode("utf-8", "replace").splitlines()
    linha = (texto[0] if texto else "").strip().rstrip("{").strip()
    return linha[:140]


def _erro_sintaxe_ts(arvore) -> int | None:
    """Linha (1-based) do primeiro nó ERROR/ausente, ou None."""
    if not arvore.root_node.has_error:
        return None
    pilha = [arvore.root_node]
    while pilha:
        n = pilha.pop(0)
        if n.type == "ERROR" or n.is_missing:
            return n.start_point[0] + 1
        pilha.extend(c for c in n.children if c.has_error or c.type == "ERROR" or c.is_missing)
    return arvore.root_node.start_point[0] + 1


def simbolos(p: Path) -> tuple[list[dict], str]:
    """(símbolos, aviso) de um arquivo. Erro de sintaxe não é falha: vem o que der, com aviso."""
    lingua = _lingua(p)
    if lingua == "python":
        try:
            arvore, _ = _arvore(p, "python-ast")
            return _simbolos_py(arvore), ""
        except SyntaxError as e:
            aviso = f"erro de sintaxe perto da linha {e.lineno}"
            if _ts_parser("python") is None:
                return [], aviso
            arvore, fonte = _arvore(p, "python")
            return _simbolos_ts(arvore, fonte), aviso
    arvore, fonte = _arvore(p, lingua)
    linha = _erro_sintaxe_ts(arvore)
    return _simbolos_ts(arvore, fonte), (f"erro de sintaxe perto da linha {linha}" if linha else "")


# ------------------------------------------------------------------ ast

def _outline_arquivo(root: Path, p: Path, so_topo: bool = False) -> str:
    itens, aviso = simbolos(p)
    linhas = [f"{_rel(root, p)}" + (f"  (ATENÇÃO: {aviso}; estrutura parcial)" if aviso else "")]
    for s in itens:
        if so_topo and s["nivel"]:
            continue
        faixa = f"L{s['ini']}" + (f"–{s['fim']}" if s["fim"] != s["ini"] else "")
        doc = f" — {s['doc']}" if s["doc"] else ""
        linhas.append(f"{'  ' * (s['nivel'] + 1)}{s['assinatura']}  {faixa}{doc}")
    if len(linhas) == 1:
        linhas.append("  (nenhuma função ou classe)")
    return "\n".join(linhas)


def _analisaveis(base: Path, root: Path | None = None):
    """Arquivos Python/JS/TS da pasta, pulando o que o `tree` pula (dependências, build, .gitignore da
    raiz). Sem isto o índice varria resources/python inteiro e batia no teto antes do código do projeto."""
    root = root or base
    padroes = _gitignore(root)
    for dirpath, dirnames, filenames in os.walk(base):
        d = Path(dirpath)
        rel_d = d.relative_to(root).as_posix() if d.is_relative_to(root) else ""
        rel = (lambda n: f"{rel_d}/{n}" if rel_d not in ("", ".") else n)
        dirnames[:] = sorted(n for n in dirnames if not _ignorado(rel(n), n, True, padroes)
                             and not _repo_aninhado(d / n))
        for n in sorted(filenames):
            if Path(n).suffix.lower() in LINGUAS and not _ignorado(rel(n), n, False, padroes):
                yield d / n


def _op_outline(root: Path, p: Path) -> str:
    if p.is_dir():
        blocos, n = [], 0
        for f in _analisaveis(p, root):
            if n >= MAX_OUTLINE_PASTA:
                blocos.append(f"(… mais arquivos: use path de um arquivo, ou uma subpasta)")
                break
            try:
                blocos.append(_outline_arquivo(root, f, so_topo=True))
            except ToolError as e:
                blocos.append(f"{_rel(root, f)}  ({e})")
            n += 1
        return "\n\n".join(blocos) or "(nenhum arquivo Python/JS/TS nesta pasta)"
    return _outline_arquivo(root, p)


def _op_symbol(root: Path, p: Path, nome: str) -> str:
    if not nome:
        raise ToolError("Informe 'name' (ex.: soma, Classe.metodo).")
    itens, _ = simbolos(p)
    achados = [s for s in itens if s["nome"] == nome] or [s for s in itens if s["nome"].split(".")[-1] == nome]
    if not achados:
        perto = ", ".join(s["nome"] for s in itens[:30])
        raise ToolError(f"'{nome}' não encontrado em {_rel(root, p)}. Símbolos: {perto or 'nenhum'}.")
    linhas = _fonte(p).decode("utf-8", "replace").splitlines()
    partes = []
    for s in achados[:3]:
        corpo = "\n".join(f"{i:>5}\t{linhas[i - 1]}" for i in range(s["ini"], min(s["fim"], len(linhas)) + 1))
        partes.append(f"{s['nome']} ({_rel(root, p)} L{s['ini']}–{s['fim']})\n{corpo}")
    return "\n\n".join(partes)


def _op_node_at(root: Path, p: Path, linha: int, coluna: int) -> str:
    if not linha:
        raise ToolError("node_at precisa de line (1-based) e, se quiser, character.")
    lingua = _lingua(p)
    coluna = max(1, coluna or 1)
    if lingua == "python":
        try:
            arvore, fonte = _arvore(p, "python-ast")
        except SyntaxError as e:
            raise ToolError(f"erro de sintaxe perto da linha {e.lineno}")
        cadeia, alvo = [], None

        def contem(n) -> bool:
            if not hasattr(n, "lineno"):
                return False
            ini, fim = (n.lineno, n.col_offset + 1), (n.end_lineno or n.lineno, (n.end_col_offset or 0) + 1)
            return ini <= (linha, coluna) <= fim

        n = arvore
        while True:
            filho = next((c for c in pyast.iter_child_nodes(n) if contem(c)), None)
            if filho is None:
                break
            nome = getattr(filho, "name", None) or getattr(filho, "id", None) or getattr(filho, "attr", None)
            cadeia.append(type(filho).__name__ + (f" {nome}" if nome else ""))
            n = alvo = filho
        texto = pyast.get_source_segment(fonte.decode("utf-8", "replace"), alvo) if alvo else ""
    else:
        arvore, fonte = _arvore(p, lingua)
        no = arvore.root_node.descendant_for_point_range((linha - 1, coluna - 1), (linha - 1, coluna - 1))
        cadeia, n = [], no
        while n is not None and n.type not in ("program", "module"):
            nome = n.child_by_field_name("name")
            cadeia.insert(0, n.type + (f" {fonte[nome.start_byte:nome.end_byte].decode()}" if nome else ""))
            n = n.parent
        texto = fonte[no.start_byte:no.end_byte].decode("utf-8", "replace") if no else ""
    if not cadeia:
        return f"(nada na linha {linha}, coluna {coluna})"
    return " > ".join(cadeia) + "\n\n" + (texto or "")[:1500]


def _op_query(root: Path, p: Path, consulta: str) -> str:
    if not consulta.strip():
        raise ToolError("Informe 'query': uma S-expression do tree-sitter, ex.: (call_expression "
                        "function: (identifier) @f (#eq? @f \"fetch\")).")
    import tree_sitter as ts
    alvos = list(_analisaveis(p, root)) if p.is_dir() else [p]
    out: list[str] = []
    compiladas: dict[str, object] = {}
    for f in alvos:
        lingua = LINGUAS.get(f.suffix.lower())
        par = _ts_parser(lingua) if lingua else None
        if par is None:
            continue
        if lingua not in compiladas:
            try:
                compiladas[lingua] = ts.Query(par[1], consulta)
            except Exception as e:  # consulta que não vale para esta gramática
                compiladas[lingua] = None
                if not p.is_dir():
                    raise ToolError(f"Consulta inválida para {lingua}: {e}")
        if compiladas[lingua] is None:
            continue
        try:
            arvore, fonte = _arvore(f, lingua)
        except ToolError:
            continue
        for nome, nos in ts.QueryCursor(compiladas[lingua]).captures(arvore.root_node).items():
            for n in nos:
                trecho = fonte[n.start_byte:n.end_byte].decode("utf-8", "replace").splitlines()
                out.append(f"{_rel(root, f)}:{n.start_point[0] + 1}: @{nome} {(trecho[0] if trecho else '')[:200]}")
                if len(out) >= MAX_QUERY:
                    return "\n".join(out) + f"\n(parado em {MAX_QUERY} ocorrências)"
    return "\n".join(sorted(out, key=_ordem_ocorrencia)) or "(nenhuma ocorrência)"


def _ordem_ocorrencia(linha: str):
    caminho, num, _ = linha.split(":", 2)
    return caminho, int(num)


def ast_tool(root: Path, args: dict) -> str:
    op = str(args.get("operation") or "").strip()
    alvo = args.get("path") or "."
    p = resolve_leitura(root, alvo) if op != "query" else resolve_path(root, alvo)
    if op == "outline":
        return _op_outline(root, p)
    if p.is_dir() and op != "query":
        raise ToolError(f"{op} precisa de um arquivo, não de uma pasta.")
    if op == "symbol":
        return _op_symbol(root, p, str(args.get("name") or "").strip())
    if op == "node_at":
        return _op_node_at(root, p, int(args.get("line") or 0), int(args.get("character") or 0))
    if op == "query":
        if not tem_tree_sitter():
            raise ToolError("query usa o tree-sitter, que não está instalado neste Forja.")
        return _op_query(root, p, str(args.get("query") or ""))
    raise ToolError("operation deve ser outline, symbol, node_at ou query.")


register(Tool(
    "ast",
    "Estrutura do código sem language server. outline: esqueleto do arquivo (ou pasta) com classes, "
    "funções, assinaturas e linhas — leia isto antes de abrir um arquivo grande inteiro. symbol: só o "
    "fonte de uma função/classe pelo nome (aceita Classe.metodo). node_at: o nó sintático numa linha. "
    "query: busca estrutural por S-expression do tree-sitter. Python, JS/JSX, TS/TSX. Para referências e "
    "tipos entre arquivos use lsp; para texto, grep.",
    _obj({"operation": {"type": "string", "enum": ["outline", "symbol", "node_at", "query"]},
          "path": {"type": "string", "description": "Arquivo (ou pasta, no outline e no query)"},
          "name": {"type": "string", "description": "symbol: nome da função/classe (ex.: Run.step)"},
          "line": {"type": "integer", "description": "node_at: linha (1-based)"},
          "character": {"type": "integer", "description": "node_at: coluna (1-based)"},
          "query": {"type": "string", "description": "query: S-expression do tree-sitter"}},
         ["operation", "path"]),
    ast_tool))


# ------------------------------------------------------------------ imports

def _imports_py(p: Path) -> list[dict]:
    """[{mod, nomes, nivel, linha, local}] — `local` = import dentro de função."""
    try:
        arvore, _ = _arvore(p, "python-ast")
    except SyntaxError:
        return []
    out: list[dict] = []

    def visita(n, dentro: bool):
        for c in pyast.iter_child_nodes(n):
            if isinstance(c, pyast.Import):
                out.extend({"mod": a.name, "nomes": [], "nivel": 0, "linha": c.lineno, "local": dentro}
                           for a in c.names)
            elif isinstance(c, pyast.ImportFrom):
                out.append({"mod": c.module or "", "nomes": [a.name for a in c.names], "nivel": c.level,
                            "linha": c.lineno, "local": dentro})
            visita(c, dentro or isinstance(c, (pyast.FunctionDef, pyast.AsyncFunctionDef)))
    visita(arvore, False)
    return out


def _imports_js(p: Path) -> list[dict]:
    try:
        arvore, fonte = _arvore(p, LINGUAS[p.suffix.lower()])
    except ToolError:
        return []
    out: list[dict] = []

    def texto(n) -> str:
        return fonte[n.start_byte:n.end_byte].decode("utf-8", "replace").strip("'\"`")

    pilha = [arvore.root_node]
    while pilha:
        n = pilha.pop()
        if n.type in ("import_statement", "export_statement"):
            src = n.child_by_field_name("source")
            if src is not None:
                out.append({"mod": texto(src), "linha": n.start_point[0] + 1, "local": False})
        elif n.type == "call_expression":
            fn = n.child_by_field_name("function")
            args = n.child_by_field_name("arguments")
            if fn is not None and args is not None and fonte[fn.start_byte:fn.end_byte] in (b"require", b"import"):
                primeiro = next((a for a in args.children if a.type == "string"), None)
                if primeiro is not None:
                    out.append({"mod": texto(primeiro), "linha": n.start_point[0] + 1, "local": False})
        pilha.extend(n.children)
    return sorted(out, key=lambda i: i["linha"])


def _raizes_py(root: Path) -> list[Path]:
    return [r for r in (root, root / "src") if r.is_dir()]


def _resolve_py(root: Path, arquivo: Path, imp: dict) -> list[Path]:
    """Arquivos do projeto que o import alcança; [] = externo ou não resolvido."""
    def modulo(base: Path, partes: list[str]) -> Path | None:
        alvo = base.joinpath(*partes) if partes else base
        for c in (alvo.with_suffix(".py"), alvo / "__init__.py"):
            if c.is_file():
                return c
        return None

    partes = [x for x in imp["mod"].split(".") if x]
    if imp["nivel"]:
        base = arquivo.parent
        for _ in range(imp["nivel"] - 1):
            base = base.parent
        bases = [base]
    else:
        bases = _raizes_py(root)
    achados: list[Path] = []
    for base in bases:
        if (m := modulo(base, partes)) and m not in achados:
            achados.append(m)
        for nome in imp["nomes"]:  # from pacote import submodulo
            if nome != "*" and (s := modulo(base, partes + [nome])) and s not in achados:
                achados.append(s)
        if achados:
            break
    return achados


def _tsconfig(root: Path) -> tuple[Path, dict]:
    """(baseUrl, paths) do tsconfig.json da raiz; só o 1º nível, sem `extends`."""
    f = root / "tsconfig.json"
    try:
        bruto = f.read_text(encoding="utf-8")
        bruto = re.sub(r"/\*.*?\*/", "", bruto, flags=re.S)
        bruto = re.sub(r'(?m)(^|[^:"\\])//.*$', r"\1", bruto)  # // comentário (não o de "http://")
        bruto = re.sub(r",(\s*[}\]])", r"\1", bruto)  # vírgula sobrando, comum em tsconfig
        opts = json.loads(bruto).get("compilerOptions") or {}
    except (OSError, ValueError, AttributeError):
        return root, {}
    return root / (opts.get("baseUrl") or "."), opts.get("paths") or {}


def _resolve_js(root: Path, arquivo: Path, imp: dict, cfg) -> list[Path]:
    spec = imp["mod"]

    def arquivo_de(base: Path) -> Path | None:
        if base.is_file():
            return base
        for ext in JS_EXTS:
            if (c := base.with_name(base.name + ext)).is_file():
                return c
        for ext in JS_EXTS:
            if (c := base / f"index{ext}").is_file():
                return c
        return None

    if spec.startswith((".", "/")):
        alvo = arquivo_de((arquivo.parent / spec) if spec.startswith(".") else root / spec.lstrip("/"))
        return [alvo] if alvo else []
    base_url, paths = cfg
    for padrao, destinos in paths.items():
        prefixo = padrao.rstrip("*")
        if spec == padrao or (padrao.endswith("*") and spec.startswith(prefixo)):
            resto = spec[len(prefixo):] if padrao.endswith("*") else ""
            for d in destinos:
                if alvo := arquivo_de(base_url / d.replace("*", resto)):
                    return [alvo]
    return []


def _pacote(spec: str, lingua: str) -> str:
    if lingua == "python":
        return spec.split(".")[0]
    partes = spec.split("/")
    return "/".join(partes[:2]) if spec.startswith("@") else partes[0]


def _imports_de(root: Path, p: Path, cfg=None) -> list[dict]:
    """Imports de um arquivo, cada um com `alvos` (arquivos do projeto) ou `externo`."""
    lingua = _lingua(p)
    if lingua == "python":
        itens = _imports_py(p)
        for i in itens:
            i["alvos"] = _resolve_py(root, p, i)
            i["rotulo"] = ("." * i["nivel"]) + i["mod"] + (f" import {', '.join(i['nomes'])}" if i["nomes"] else "")
    else:
        cfg = cfg or _tsconfig(root)
        itens = _imports_js(p)
        for i in itens:
            i["alvos"] = _resolve_js(root, p, i, cfg)
            i["rotulo"] = i["mod"]
    for i in itens:
        interno = i["mod"].startswith(".") or i.get("nivel")
        i["externo"] = "" if i["alvos"] or interno else _pacote(i["mod"], lingua)
    return itens


_INDICE: dict[str, tuple] = {}


def _indice(root: Path, base: Path) -> tuple[dict[Path, list[dict]], bool]:
    """{arquivo: imports} de uma pasta, em cache por mtime. (índice, truncado)."""
    cfg = _tsconfig(root)
    indice: dict[Path, list[dict]] = {}
    for n, f in enumerate(_analisaveis(base, root)):
        if n >= MAX_INDICE:
            return indice, True
        try:
            st = f.stat()
            chave = str(f)
            if (c := _INDICE.get(chave)) and c[0] == (st.st_mtime_ns, st.st_size):
                indice[f] = c[1]
                continue
            itens = _imports_de(root, f, cfg)
        except (ToolError, OSError):
            continue
        _INDICE[chave] = ((st.st_mtime_ns, st.st_size), itens)
        indice[f] = itens
    return indice, False


def _op_of(root: Path, p: Path) -> str:
    itens = _imports_de(root, p)
    if not itens:
        return f"{_rel(root, p)} não importa nada."
    linhas = []
    for i in itens:
        destino = (", ".join(_rel(root, a) for a in i["alvos"]) if i["alvos"]
                   else f"(pacote: {i['externo']})" if i["externo"] else "(?) não resolvido")
        linhas.append(f"L{i['linha']} {i['rotulo']} → {destino}" + ("  (local)" if i.get("local") else ""))
    return "\n".join(linhas)


def _op_importers(root: Path, p: Path) -> str:
    indice, truncado = _indice(root, root)
    alvo = p.resolve()
    linhas = [f"{_rel(root, f)}:{i['linha']}  {i['rotulo']}" for f, itens in indice.items()
              for i in itens if any(a.resolve() == alvo for a in i["alvos"])]
    aviso = f"\n(índice parou em {MAX_INDICE} arquivos: pode faltar alguém)" if truncado else ""
    return ("\n".join(sorted(linhas)) or f"Ninguém no projeto importa {_rel(root, p)}.") + aviso


def _arestas(root: Path, base: Path, com_locais: bool = True) -> tuple[dict[str, set[str]], bool]:
    indice, truncado = _indice(root, base)
    grafo: dict[str, set[str]] = {}
    for f, itens in indice.items():
        origem = _rel(root, f)
        grafo.setdefault(origem, set())
        for i in itens:
            if i.get("local") and not com_locais:
                continue
            for a in i["alvos"]:
                if a.resolve() != f.resolve():
                    grafo[origem].add(_rel(root, a.resolve()))
    return grafo, truncado


def _op_graph(root: Path, base: Path) -> str:
    grafo, truncado = _arestas(root, base)
    por_pasta: dict[str, list[str]] = {}
    for origem in sorted(grafo):
        for destino in sorted(grafo[origem]):
            por_pasta.setdefault(os.path.dirname(origem) or ".", []).append(f"  {origem} → {destino}")
    if not por_pasta:
        return "(nenhum import entre arquivos do projeto nesta pasta)"
    texto = "\n".join(f"{pasta}/\n" + "\n".join(linhas) for pasta, linhas in por_pasta.items())
    return texto + (f"\n(índice parou em {MAX_INDICE} arquivos)" if truncado else "")


def ciclos(grafo: dict[str, set[str]]) -> list[list[str]]:
    """Ciclos de import (cada um uma vez, começando pelo menor nome), por DFS iterativo."""
    achados: list[list[str]] = []
    vistos: set[tuple] = set()
    for inicio in sorted(grafo):
        pilha = [(inicio, [inicio])]
        while pilha and len(achados) < MAX_CICLOS:
            no, caminho = pilha.pop()
            for prox in sorted(grafo.get(no, ())):
                if prox == inicio:
                    chave = tuple(sorted(caminho))
                    if chave not in vistos and min(caminho) == inicio:
                        vistos.add(chave)
                        achados.append(caminho + [inicio])
                elif prox not in caminho and prox > inicio and len(caminho) < 12:
                    pilha.append((prox, caminho + [prox]))
    return achados


def _op_cycles(root: Path, base: Path) -> str:
    # Import dentro de função não roda no import do módulo: é o jeito comum de quebrar um ciclo, e
    # contá-lo aqui acusaria justamente o ciclo que ele resolve.
    grafo, _ = _arestas(root, base, com_locais=False)
    achados = ciclos(grafo)
    if not achados:
        return "Nenhum ciclo de import."
    return "\n".join(" → ".join(c) for c in achados) + (
        f"\n(parado em {MAX_CICLOS} ciclos)" if len(achados) >= MAX_CICLOS else "")


def imports_tool(root: Path, args: dict) -> str:
    op = str(args.get("operation") or "").strip()
    p = resolve_path(root, args.get("path") or ".")
    if op in ("of", "importers"):
        if not p.is_file():
            raise ToolError(f"{op} precisa de um arquivo.")
        if LINGUAS.get(p.suffix.lower()) not in ("python", None) and not tem_tree_sitter():
            raise ToolError("imports de JS/TS usa o tree-sitter, que não está instalado neste Forja.")
        return _op_of(root, p) if op == "of" else _op_importers(root, p)
    if op in ("graph", "cycles"):
        if not p.is_dir():
            raise ToolError(f"{op} precisa de uma pasta.")
        return _op_graph(root, p) if op == "graph" else _op_cycles(root, p)
    raise ToolError("operation deve ser of, importers, graph ou cycles.")


register(Tool(
    "imports",
    "Dependências entre arquivos do projeto. of: o que um arquivo importa, com o destino resolvido "
    "(arquivo do projeto, pacote externo ou não resolvido). importers: quem importa um arquivo — use antes "
    "de mudar a interface dele. graph: as arestas internas de uma pasta. cycles: os ciclos de import. "
    "Python (relativos e pacotes) e JS/TS (relativos, index.*, paths do tsconfig, require).",
    _obj({"operation": {"type": "string", "enum": ["of", "importers", "graph", "cycles"]},
          "path": {"type": "string", "description": "Arquivo (of, importers) ou pasta (graph, cycles)"}},
         ["operation", "path"]),
    imports_tool))


# ------------------------------------------------------------------ tree

def _gitignore(root: Path) -> list[str]:
    """Padrões simples do .gitignore da raiz (sem negação `!`): o que o fnmatch consegue."""
    try:
        linhas = (root / ".gitignore").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [l.strip().lstrip("/") for l in linhas if l.strip() and not l.lstrip().startswith(("#", "!"))]


def _repo_aninhado(p: Path) -> bool:
    """Pasta com `.git` próprio: outro repositório ou worktree (ex.: .claude/worktrees/*). É cópia do
    código, e mostrá-la duplica tudo no tree e no índice de imports."""
    return (p / ".git").exists()


def _ignorado(rel: str, nome: str, eh_pasta: bool, padroes: list[str]) -> bool:
    if eh_pasta and nome in TREE_IGNORADOS:
        return True
    for pad in padroes:
        so_pasta = pad.endswith("/")
        pad = pad.rstrip("/")
        if so_pasta and not eh_pasta:
            continue
        if fnmatch.fnmatch(nome, pad) or fnmatch.fnmatch(rel, pad) or fnmatch.fnmatch(rel, pad + "/*"):
            return True
    return False


def _linhas_do_arquivo(p: Path) -> str:
    try:
        tam = p.stat().st_size
        if tam > MAX_ARQUIVO:
            return f"{tam // 1024} KB"
        dados = p.read_bytes()
    except OSError:
        return ""
    if b"\0" in dados[:4096]:
        return f"{tam // 1024 or 1} KB"
    n = dados.count(b"\n") + (1 if dados and not dados.endswith(b"\n") else 0)
    return f"{n} linhas"


def tree(root: Path, args: dict) -> str:
    base = resolve_path(root, args.get("path") or ".")
    if not base.is_dir():
        raise ToolError(f"Não é um diretório: '{args.get('path')}'.")
    profundidade = max(1, min(10, int(args.get("depth") or 3)))
    so_pastas = bool(args.get("dirs_only"))
    padrao = str(args.get("pattern") or "").strip()
    padroes = _gitignore(root)
    contagem: dict[Path, int] = {}

    def filhos(d: Path) -> tuple[list[Path], list[Path]]:
        try:
            itens = sorted(d.iterdir(), key=lambda x: x.name.lower())
        except OSError:
            return [], []
        pastas, arquivos = [], []
        for x in itens:
            rel = x.relative_to(root).as_posix() if x.is_relative_to(root) else x.name
            eh = x.is_dir()
            if _ignorado(rel, x.name, eh, padroes) or (eh and _repo_aninhado(x)):
                continue
            if eh:
                pastas.append(x)
            elif not padrao or fnmatch.fnmatch(x.name, padrao):
                arquivos.append(x)
        return pastas, arquivos

    def conta(d: Path) -> int:
        """Arquivos (que casam com o pattern) dentro da pasta, recursivo, com teto."""
        if d in contagem:
            return contagem[d]
        total = 0
        pilha = [d]
        while pilha and total < 10_000:
            pastas, arquivos = filhos(pilha.pop())
            total += len(arquivos)
            pilha.extend(pastas)
        contagem[d] = total
        return total

    saida = [f"{_rel(root, base)}/"]
    totais = {"pastas": 0, "arquivos": 0}

    def desenha(d: Path, prefixo: str, nivel: int):
        pastas, arquivos = filhos(d)
        # Pasta sem nenhum arquivo aparente (vazia, ou só com o que foi ignorado: .claude/ com as
        # worktrees) é ruído na primeira olhada.
        pastas = [x for x in pastas if conta(x)]
        itens = pastas + ([] if so_pastas else arquivos)
        mostrar, resto = itens[:MAX_FILHOS], itens[MAX_FILHOS:]
        for i, x in enumerate(mostrar):
            if len(saida) >= MAX_TREE_LINHAS:
                return
            ultimo = i == len(mostrar) - 1 and not resto
            ramo = "└── " if ultimo else "├── "
            if x.is_dir():
                totais["pastas"] += 1
                saida.append(f"{prefixo}{ramo}{x.name}/  ({conta(x)} arquivos)")
                if nivel + 1 < profundidade:
                    desenha(x, prefixo + ("    " if ultimo else "│   "), nivel + 1)
            else:
                totais["arquivos"] += 1
                saida.append(f"{prefixo}{ramo}{x.name}  {_linhas_do_arquivo(x)}")
        if resto:
            n_pastas = sum(1 for x in resto if x.is_dir())
            descr = ", ".join(t for t in (f"{n_pastas} pastas" if n_pastas else "",
                                          f"{len(resto) - n_pastas} arquivos" if len(resto) - n_pastas else "") if t)
            saida.append(f"{prefixo}└── … +{descr}")

    desenha(base, "", 0)
    nota = f"\n(árvore cortada em {MAX_TREE_LINHAS} linhas: use path de uma subpasta)" \
        if len(saida) >= MAX_TREE_LINHAS else ""
    return ("\n".join(saida) + f"\n({totais['pastas']} pastas e {totais['arquivos']} arquivos mostrados; "
            f"profundidade {profundidade}){nota}")


register(Tool(
    "tree",
    "Árvore de pastas indentada (como o `tree` do Unix), com a contagem de arquivos de cada pasta e as "
    "linhas de cada arquivo. É a primeira olhada num repositório desconhecido: mostra a forma do projeto "
    "com poucos tokens. Ignora node_modules, .git, dist, build e o que o .gitignore da raiz lista.",
    _obj({"path": {"type": "string", "description": "Pasta de partida. Padrão: a raiz do projeto"},
          "depth": {"type": "integer", "description": "Profundidade máxima (1–10). Padrão: 3"},
          "dirs_only": {"type": "boolean", "description": "Só as pastas"},
          "pattern": {"type": "string", "description": "Só arquivos que casam com o glob (ex.: *.py)"}}, []),
    tree))

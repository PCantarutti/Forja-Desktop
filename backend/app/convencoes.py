"""Convenções do projeto detectadas pelo código, sem LLM (E14, parte 1).

O Worker começa cada tarefa só com o contrato: sem saber que o projeto é TypeScript strict, usa Tailwind
e testa com Vitest, um modelo pequeno inventa um estilo por tarefa. Aqui o Forja lê o que o projeto já
declara (manifesto, lockfile, configs de lint e tipos, estrutura de pastas) e escreve em
`.forja/knowledge/convencoes.md`, seção "Detectado", cada item com a origem (`tsconfig.json:5`).

Recalculado ao abrir o projeto e antes do plan_feature; o arquivo só é regravado quando muda. As
outras seções do arquivo (o que o usuário ou o Maestro escreverem) são preservadas.

Também sai daqui os comandos de teste, tipos e lint: o board varre com eles e o Maestro os usa de
verify, sem ninguém precisar escrever `test_command:` no FORJA.md.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ARQUIVO = ".forja/knowledge/convencoes.md"
INICIO, FIM = "<!-- forja:detectado:inicio -->", "<!-- forja:detectado:fim -->"
MAX_PROMPT = 1500           # caracteres que entram no prompt/contrato
MAX_SUBPROJETOS = 8
PASTAS_DE_CAMADA = ("components", "pages", "app", "routes", "hooks", "stores", "services", "repositories",
                    "controllers", "models", "schemas", "api", "lib", "utils", "features", "domain",
                    "adapters", "middlewares", "migrations", "tests", "__tests__")
IGNORAR = {"node_modules", ".git", "dist", "build", ".next", "out", "coverage", ".venv", "venv", "__pycache__",
           ".forja", ".claude", "target", ".pytest_cache", ".mypy_cache"}


def _texto(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _linha(texto: str, padrao: str) -> int | None:
    """Linha (1-based) onde o padrão aparece: é a origem que o usuário confere."""
    rx = re.compile(padrao)
    return next((n for n, l in enumerate(texto.splitlines(), 1) if rx.search(l)), None)


def _origem(rel: str, texto: str = "", padrao: str = "") -> str:
    n = _linha(texto, padrao) if padrao else None
    return f"{rel}:{n}" if n else rel


def _json(texto: str) -> dict:
    """JSON tolerante a comentário e vírgula sobrando (tsconfig costuma ter os dois)."""
    sem = re.sub(r"//[^\n]*|/\*.*?\*/", "", texto, flags=re.S)
    sem = re.sub(r",(\s*[}\]])", r"\1", sem)
    try:
        v = json.loads(sem)
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


# ------------------------------------------------------------------ JavaScript / TypeScript

def _pm_js(base: Path) -> str:
    for arq, pm in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun"),
                    ("package-lock.json", "npm")):
        if (base / arq).is_file():
            return pm
    return "npm"


LOCK_JS = {"pnpm": "pnpm-lock.yaml", "yarn": "yarn.lock", "bun": "bun.lock", "npm": "package-lock.json"}


def _roda(pm: str, script: str) -> str:
    if pm == "npm":
        return "npm test" if script == "test" else f"npm run {script}"
    return f"yarn {script}" if pm == "yarn" else f"{pm} run {script}"


LIBS_JS = {  # dependência → (convenção, categoria)
    "next": ("Next.js", "stack"), "react": ("React", "stack"), "vue": ("Vue", "stack"), "svelte": ("Svelte", "stack"),
    "@angular/core": ("Angular", "stack"), "vite": ("Vite", "build"), "express": ("Express", "stack"),
    "fastify": ("Fastify", "stack"), "@nestjs/core": ("NestJS", "stack"), "tailwindcss": ("Tailwind CSS", "estilo"),
    "vitest": ("Testes com Vitest", "testes"), "jest": ("Testes com Jest", "testes"),
    "@playwright/test": ("Testes de ponta a ponta com Playwright", "testes"), "cypress": ("Testes E2E com Cypress", "testes"),
    "eslint": ("Lint com ESLint", "lint"), "prettier": ("Formatação com Prettier", "lint"), "biome": ("Lint e formatação com Biome", "lint"),
    "@biomejs/biome": ("Lint e formatação com Biome", "lint"), "zod": ("Validação com Zod", "stack"),
    "prisma": ("Banco com Prisma", "stack"), "drizzle-orm": ("Banco com Drizzle", "stack"),
    "@tanstack/react-query": ("Dados com TanStack Query", "stack"), "zustand": ("Estado com Zustand", "stack"),
    "redux": ("Estado com Redux", "stack"), "@reduxjs/toolkit": ("Estado com Redux Toolkit", "stack"),
    "expo": ("App mobile com Expo", "stack"), "react-native": ("React Native", "stack"),
}


def _js(base: Path, rel: str, achados: list, comandos: dict) -> None:
    texto = _texto(base / "package.json")
    pkg = _json(texto)
    if not pkg:
        return
    pr = f"{rel}package.json"
    pm = _pm_js(base)
    lock = next((f for f in (LOCK_JS[pm], "bun.lockb") if (base / f).is_file()), None)
    achados.append(("Gerenciador de pacotes: " + pm, "build", f"{rel}{lock}" if lock else pr))
    if pkg.get("type") == "module":
        achados.append(("Módulos ES (import/export, não require)", "estilo", _origem(pr, texto, r'"type"\s*:\s*"module"')))
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    vistos = set()
    for lib, (nome, cat) in LIBS_JS.items():
        if lib in deps and nome not in vistos:
            vistos.add(nome)
            achados.append((nome, cat, _origem(pr, texto, rf'"{re.escape(lib)}"\s*:')))
    scripts = pkg.get("scripts") or {}
    for chave, nomes in (("test", ("test",)), ("typecheck", ("typecheck", "type-check", "tsc", "check-types")),
                         ("lint", ("lint",)), ("build", ("build",))):
        script = next((n for n in nomes if n in scripts), None)
        if script and not (chave == "test" and "no test specified" in str(scripts[script])):
            comandos.setdefault(chave, (_roda(pm, script), _origem(pr, texto, rf'"{re.escape(script)}"\s*:')))
    ts = _texto(base / "tsconfig.json")
    if ts:
        cfg = (_json(ts).get("compilerOptions") or {})
        tr = f"{rel}tsconfig.json"
        achados.append(("TypeScript" + (" strict" if cfg.get("strict") else ""), "tipos",
                        _origem(tr, ts, r'"strict"\s*:\s*true') if cfg.get("strict") else tr))
        if cfg.get("noImplicitAny") is True and not cfg.get("strict"):
            achados.append(("TypeScript sem any implícito", "tipos", _origem(tr, ts, r'"noImplicitAny"\s*:\s*true')))
        if isinstance(cfg.get("paths"), dict) and cfg["paths"]:
            apelido = next(iter(cfg["paths"]))
            achados.append((f"Imports com apelido de caminho ({apelido})", "estilo", _origem(tr, ts, r'"paths"\s*:')))
        comandos.setdefault("typecheck", (("npx tsc --noEmit" if pm == "npm" else f"{pm} exec tsc --noEmit"), tr))
    for arq in sorted(base.glob("tailwind.config.*")):
        if "Tailwind CSS" not in vistos:
            achados.append(("Tailwind CSS", "estilo", f"{rel}{arq.name}"))
            vistos.add("Tailwind CSS")
    if "Tailwind CSS" not in vistos:
        for css in list(base.glob("src/**/*.css"))[:40]:
            t = _texto(css)
            if re.search(r'@tailwind|@import\s+["\']tailwindcss', t):
                achados.append(("Tailwind CSS", "estilo", _origem(f"{rel}{css.relative_to(base).as_posix()}", t,
                                                                   r'@tailwind|@import\s+["\']tailwindcss')))
                break
    for padrao, nome in (("eslint.config.*", "Lint com ESLint"), (".eslintrc*", "Lint com ESLint"),
                         (".prettierrc*", "Formatação com Prettier"), ("prettier.config.*", "Formatação com Prettier"),
                         ("biome.json*", "Lint e formatação com Biome")):
        if nome not in vistos and (arq := next(iter(sorted(base.glob(padrao))), None)):
            vistos.add(nome)
            achados.append((nome, "lint", f"{rel}{arq.name}"))
    prettier = _texto(next(iter(sorted(base.glob(".prettierrc*"))), base / "-"))
    if prettier:
        p = _json(prettier)
        detalhes = [d for d, ok in (("sem ponto e vírgula", p.get("semi") is False), ("aspas simples", p.get("singleQuote") is True))
                    if ok]
        if detalhes:
            achados.append(("Estilo: " + ", ".join(detalhes), "estilo", f"{rel}{next(iter(sorted(base.glob('.prettierrc*')))).name}"))


# ------------------------------------------------------------------ Python

LIBS_PY = {"fastapi": ("FastAPI", "stack"), "django": ("Django", "stack"), "flask": ("Flask", "stack"),
           "sqlalchemy": ("Banco com SQLAlchemy", "stack"), "pydantic": ("Modelos com Pydantic", "stack"),
           "pytest": ("Testes com pytest", "testes"), "ruff": ("Lint com Ruff", "lint"), "black": ("Formatação com Black", "lint"),
           "mypy": ("Tipos checados com mypy", "tipos"), "alembic": ("Migrações com Alembic", "stack"),
           "celery": ("Tarefas com Celery", "stack"), "httpx": ("HTTP com httpx", "stack")}


def _py(base: Path, rel: str, achados: list, comandos: dict) -> None:
    py = _texto(base / "pyproject.toml")
    reqs = "\n".join(_texto(p) for p in sorted(base.glob("requirements*.txt")))
    if not py and not reqs and not (base / "setup.py").is_file():
        return
    if (base / "uv.lock").is_file():
        pm, lock, prefixo = "uv", "uv.lock", "uv run "
    elif (base / "poetry.lock").is_file():
        pm, lock, prefixo = "poetry", "poetry.lock", "poetry run "
    elif (base / "Pipfile.lock").is_file():
        pm, lock, prefixo = "pipenv", "Pipfile.lock", "pipenv run "
    else:
        pm, lock, prefixo = "pip", ("requirements.txt" if (base / "requirements.txt").is_file() else "pyproject.toml"), ""
    achados.append((f"Python, pacotes com {pm}", "build", f"{rel}{lock}"))
    vistos = set()
    for lib, (nome, cat) in LIBS_PY.items():
        # começo de linha, ou entre aspas numa lista de uma linha só (dependencies = ["django", ...])
        padrao = rf'(?i)^\s*"?{re.escape(lib)}\b|["\']{re.escape(lib)}\b|\[tool\.{re.escape(lib)}'
        origem = _origem(f"{rel}pyproject.toml", py, padrao) if re.search(padrao, py, re.M) else None
        if not origem:
            for req in sorted(base.glob("requirements*.txt")):
                if _linha(texto_req := _texto(req), rf"(?i)^\s*{re.escape(lib)}\b"):
                    origem = _origem(f"{rel}{req.name}", texto_req, rf"(?i)^\s*{re.escape(lib)}\b")
                    break
        if origem:
            vistos.add(lib)
            achados.append((nome, cat, origem))
    ruff_arq = next((f for f in ("ruff.toml", ".ruff.toml") if (base / f).is_file()), None)
    if ruff_arq and "ruff" not in vistos:
        achados.append(("Lint com Ruff", "lint", f"{rel}{ruff_arq}"))
        vistos.add("ruff")
    if m := re.search(r"(?m)^\s*line-length\s*=\s*(\d+)", py + "\n" + _texto(base / (ruff_arq or "-"))):
        achados.append((f"Linhas de até {m.group(1)} caracteres", "estilo",
                        _origem(f"{rel}pyproject.toml", py, r"^\s*line-length\s*=") if m.group(0) in py else f"{rel}{ruff_arq}"))
    if (base / "pytest.ini").is_file() and "pytest" not in vistos:
        achados.append(("Testes com pytest", "testes", f"{rel}pytest.ini"))
        vistos.add("pytest")
    if "pytest" in vistos or (base / "tests").is_dir():
        origem_teste = next((o for n, _c, o in achados if n == "Testes com pytest"), f"{rel}tests/")
        comandos.setdefault("test", (prefixo + "pytest -q", origem_teste))
    if "ruff" in vistos:
        comandos.setdefault("lint", (prefixo + "ruff check .", f"{rel}{ruff_arq or 'pyproject.toml'}"))
    if "mypy" in vistos:
        comandos.setdefault("typecheck", (prefixo + "mypy .", f"{rel}pyproject.toml"))


def _outros(base: Path, rel: str, achados: list, comandos: dict) -> None:
    if (base / "go.mod").is_file():
        achados.append(("Go", "stack", f"{rel}go.mod"))
        comandos.setdefault("test", ("go test ./...", f"{rel}go.mod"))
    if (base / "Cargo.toml").is_file():
        achados.append(("Rust", "stack", f"{rel}Cargo.toml"))
        comandos.setdefault("test", ("cargo test", f"{rel}Cargo.toml"))
    ec = _texto(base / ".editorconfig")
    if m := re.search(r"(?im)^\s*indent_style\s*=\s*(\w+)", ec):
        tam = re.search(r"(?im)^\s*indent_size\s*=\s*(\d+)", ec)
        achados.append((f"Indentação com {'tabs' if m.group(1).lower() == 'tab' else 'espaços'}"
                        + (f" ({tam.group(1)})" if tam and m.group(1).lower() != "tab" else ""), "estilo",
                        _origem(f"{rel}.editorconfig", ec, r"(?i)^\s*indent_style")))


def _camadas(base: Path, rel: str, achados: list) -> None:
    """Como o código é separado: as pastas de camada que existem (até 3 níveis), com um exemplo de caminho."""
    achadas: dict[str, str] = {}
    fila = [(base, 0)]
    while fila:
        d, nivel = fila.pop(0)
        try:
            filhos = sorted(x for x in d.iterdir() if x.is_dir() and x.name not in IGNORAR and not x.name.startswith("."))
        except OSError:
            continue
        for x in filhos:
            if x.name in PASTAS_DE_CAMADA and x.name not in achadas:
                achadas[x.name] = f"{rel}{x.relative_to(base).as_posix()}/"
            if nivel < 2 and not (x / ".git").exists():
                fila.append((x, nivel + 1))
    if achadas:
        itens = [f"{n}/" for n in PASTAS_DE_CAMADA if n in achadas]
        achados.append(("Código separado em " + ", ".join(itens), "estrutura",
                        ", ".join(achadas[n] for n in PASTAS_DE_CAMADA if n in achadas)[:300]))


def _subprojetos(root: Path) -> list[tuple[Path, str]]:
    """A raiz e as subpastas diretas com manifesto próprio (back/ e front/ num mesmo projeto)."""
    manifesto = ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "setup.py")
    out = [(root, "")]
    try:
        for d in sorted(root.iterdir()):
            if (d.is_dir() and d.name not in IGNORAR and not d.name.startswith(".")
                    and any((d / m).is_file() for m in manifesto)):
                out.append((d, f"{d.name}/"))
    except OSError:
        pass
    return out[:MAX_SUBPROJETOS + 1]


def detectar(root: Path) -> dict:
    """{"itens": [{texto, categoria, origem}], "comandos": {chave: {comando, origem}}}."""
    root = Path(root)
    itens: list[tuple] = []
    comandos: dict[str, tuple] = {}
    subprojetos = _subprojetos(root)
    for base, rel in subprojetos:
        por_pasta: dict[str, tuple] = {}
        antes = len(itens)
        _js(base, rel, itens, por_pasta)
        _py(base, rel, itens, por_pasta)
        _outros(base, rel, itens, por_pasta)
        if rel or len(subprojetos) == 1:  # com back/ e front/, a raiz só repetiria as pastas deles
            _camadas(base, rel, itens)
        if rel:  # num projeto com back/ e front/, cada item diz de qual é
            itens[antes:] = [(f"{rel.rstrip('/')}: {t}", c, o) for t, c, o in itens[antes:]]
        for chave, v in por_pasta.items():
            comandos.setdefault(chave if not rel else f"{chave}:{rel.rstrip('/')}", (*v, rel.rstrip("/")))
    vistos, unicos = set(), []
    for t, c, o in itens:
        if t not in vistos:
            vistos.add(t)
            unicos.append({"texto": t, "categoria": c, "origem": o})
    return {"itens": unicos, "comandos": {k: {"comando": v[0], "origem": v[1], "pasta": v[2]} for k, v in comandos.items()}}


# ------------------------------------------------------------------ o arquivo e o prompt

ORDEM = ("stack", "tipos", "estilo", "lint", "testes", "build", "estrutura")
ROTULO_COMANDO = {"test": "testes", "typecheck": "tipos", "lint": "lint", "build": "build"}


def _secao(det: dict) -> str:
    linhas = [INICIO, "## Detectado", "_Gerado pelo Forja a partir dos arquivos do projeto; refeito quando eles mudam. "
              "Escreva as suas regras fora deste bloco._", ""]
    for cat in ORDEM:
        for i in det["itens"]:
            if i["categoria"] == cat:
                linhas.append(f"- {i['texto']} — `{i['origem']}`")
    if det["comandos"]:
        linhas += ["", "### Comandos"]
        for chave, v in det["comandos"].items():
            base, _, pasta = chave.partition(":")
            linhas.append(f"- {ROTULO_COMANDO.get(base, base)}{f' (na pasta {pasta}/)' if pasta else ''}: `{v['comando']}` — `{v['origem']}`")
    linhas.append(FIM)
    return "\n".join(linhas)


def atualiza(root: Path) -> dict:
    """Detecta e grava a seção "Detectado" de convencoes.md, preservando o resto. Só escreve se mudou."""
    root = Path(root)
    det = detectar(root)
    if not det["itens"] and not det["comandos"]:
        return det
    p = root / ARQUIVO
    atual = _texto(p)
    secao = _secao(det)
    if INICIO in atual and FIM in atual:
        novo = atual[:atual.index(INICIO)] + secao + atual[atual.index(FIM) + len(FIM):]
    else:
        cabeca = atual.rstrip() if atual.strip() else "# Convenções do projeto"
        novo = f"{cabeca}\n\n{secao}\n"
    if novo != atual:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(novo, encoding="utf-8")
        except OSError:
            pass
    return det


def texto_para_prompt(root: Path) -> str:
    """O arquivo inteiro (detectado + o que o usuário escreveu), enxuto, para o prompt e o contrato."""
    t = _texto(Path(root) / ARQUIVO)
    t = t.replace(INICIO, "").replace(FIM, "")
    t = re.sub(r"(?m)^_Gerado pelo Forja.*$\n?", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t[:MAX_PROMPT] + ("\n(… mais em .forja/knowledge/convencoes.md)" if len(t) > MAX_PROMPT else "") if t else ""


def comandos(root: Path) -> dict[str, str]:
    """Comandos detectados da raiz (test, typecheck, lint): o fallback do board quando o FORJA.md não diz."""
    return {k: v["comando"] for k, v in detectar(Path(root))["comandos"].items() if ":" not in k and k != "build"}

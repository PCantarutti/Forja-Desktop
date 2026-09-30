"""Design systems do Design: tirados do código de um projeto e reusados em todo design novo.

Extrair tem duas partes. A primeira é determinística e barata: varre CSS/SCSS/Tailwind/componentes da
pasta e conta o que se repete (variáveis CSS, cores, fontes, tamanhos, raios, sombras, nomes de
componente). A segunda é UMA chamada curta ao modelo, que recebe só esse resumo (nunca os arquivos) e
devolve os tokens no padrão do Design (--cor-*, --fonte-*, --texto-*, --esp-*, --raio-*, --sombra-*),
CSS base de componentes e notas de estilo.

Guardados em DATA_DIR/design_sistemas.json. ponytail: arquivo JSON inteiro reescrito a cada mudança;
são poucos sistemas por pessoa. Tabela se um dia forem centenas ou compartilhados.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from contextlib import aclosing
from pathlib import Path

from . import config, design_html, llm, workspace
from .parsing import split_think
from .tools import ToolError

ARQUIVO = config.DATA_DIR / "design_sistemas.json"
PROMPT = Path(__file__).parent / "design_prompts" / "sistema.md"
EXTENSOES = {".css", ".scss", ".sass", ".less", ".tsx", ".jsx", ".vue", ".svelte", ".html", ".astro", ".ts", ".js"}
IGNORAR = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", "__pycache__", ".venv", "vendor", "coverage", ".forja"}
MAX_ARQUIVOS, MAX_BYTES = 600, 300_000
FONTES_ARQ = {".woff", ".woff2", ".ttf", ".otf"}
# texto de design system (DESIGN.md do Claude, README, guias): vira notas de estilo que a IA segue
_TEXTO_PRIORIDADE = ("design.md", "design-system.md", "readme.md", "guidelines.md", "tokens.md", "brand.md", "style.md")
MAX_TEXTO = 7000
_COR = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\([^)]*\)|hsla?\([^)]*\)|oklch\([^)]*\)")


def listar() -> list[dict]:
    try:
        return json.loads(ARQUIVO.read_text("utf-8"))
    except (OSError, ValueError):
        return []


def _gravar(lista: list[dict]) -> None:
    tmp = ARQUIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(lista, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, ARQUIVO)


def pegar(sid: str) -> dict:
    s = next((x for x in listar() if x["id"] == sid), None)
    if not s:
        raise ToolError("Design system não encontrado.")
    return s


def apagar(sid: str) -> list[dict]:
    lista = [x for x in listar() if x["id"] != sid]
    _gravar(lista)
    return lista


def _tokens_json(dado, caminho: list[str], out: dict) -> None:
    """Tokens em JSON (W3C/DTCG `$value`, Style Dictionary `value`, ou valor solto): o caminho vira o nome."""
    if len(out) >= 200:
        return
    if isinstance(dado, dict):
        v = dado.get("$value", dado.get("value"))
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            out["--" + "-".join(caminho)] = str(v)
            return
        for k, x in dado.items():
            if not str(k).startswith("$"):
                _tokens_json(x, [*caminho, re.sub(r"[^\w-]+", "-", str(k)).strip("-").lower()], out)
    elif isinstance(dado, (str, int, float)) and not isinstance(dado, bool) and caminho:
        t = str(dado)
        if _COR.fullmatch(t) or re.fullmatch(r"-?[\d.]+(px|rem|em|%)?", t) or "," in t or " " in t:
            out["--" + "-".join(caminho)] = t


def resumo(pasta: Path) -> str:
    """O que se repete no código da pasta, em poucas linhas (é isso que vai ao modelo). Lê também
    design system em forma de documento (DESIGN.md, README), tokens em JSON e as fontes da pasta."""
    vars_, cores, fontes, tamanhos, raios, sombras, comps = Counter(), Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    tailwind, lidos = "", 0
    tokens_json: dict[str, str] = {}
    textos: list[tuple[int, str, str]] = []   # (prioridade, arquivo, texto)
    arquivos_fonte: set[str] = set()
    for atual, pastas, arquivos in os.walk(pasta):
        pastas[:] = [p for p in pastas if p not in IGNORAR and not p.startswith(".")]
        for nome in arquivos:
            f = Path(atual) / nome
            if lidos >= MAX_ARQUIVOS:
                break
            if nome.startswith("tailwind.config"):
                tailwind = f.read_text("utf-8", "ignore")[:4000]
                continue
            ext = f.suffix.lower()
            if ext in FONTES_ARQ:
                arquivos_fonte.add(re.sub(r"[-_ ]?(regular|bold|italic|medium|semibold|light|black|thin|variable|vf|\d{3})+$", "", f.stem, flags=re.I))
                continue
            if ext in (".md", ".mdx", ".json"):
                try:
                    if f.stat().st_size > MAX_BYTES or nome in ("package.json", "package-lock.json", "tsconfig.json"):
                        continue
                    t = f.read_text("utf-8", "ignore")
                except OSError:
                    continue
                lidos += 1
                cores.update(c.lower() for c in _COR.findall(t))
                if ext == ".json":
                    try:
                        _tokens_json(json.loads(t), [], tokens_json)
                    except ValueError:
                        pass
                else:
                    prio = next((i for i, n in enumerate(_TEXTO_PRIORIDADE) if nome.lower() == n), len(_TEXTO_PRIORIDADE))
                    textos.append((prio, str(f.relative_to(pasta)), t))
                continue
            if ext not in EXTENSOES:
                continue
            try:
                if f.stat().st_size > MAX_BYTES:
                    continue
                t = f.read_text("utf-8", "ignore")
            except OSError:
                continue
            lidos += 1
            vars_.update(f"{a}: {b.strip()}" for a, b in re.findall(r"(--[\w-]+)\s*:\s*([^;}{\n]+)", t))
            cores.update(c.lower() for c in _COR.findall(t))
            fontes.update(v.strip() for v in re.findall(r"font-family\s*:\s*([^;}{\n]+)", t))
            tamanhos.update(v.strip() for v in re.findall(r"font-size\s*:\s*([^;}{\n]+)", t))
            raios.update(v.strip() for v in re.findall(r"border-radius\s*:\s*([^;}{\n]+)", t))
            sombras.update(v.strip() for v in re.findall(r"box-shadow\s*:\s*([^;}{\n]+)", t))
            comps.update(m.lower() for m in re.findall(
                r"\.((?:btn|button|card|input|field|badge|tag|chip|nav|navbar|header|footer|modal|alert|tab|hero)[\w-]*)", t))
    if not lidos and not tailwind:
        raise ToolError(f"Não achei CSS, componentes, tokens nem documento de design system em {pasta}.")

    def top(c: Counter, n: int) -> str:
        return "\n".join(f"  {k}  (×{v})" for k, v in c.most_common(n)) or "  (nada)"
    partes = [f"Arquivos lidos: {lidos}",
              f"Variáveis CSS mais usadas:\n{top(vars_, 60)}",
              f"Cores mais usadas:\n{top(cores, 24)}",
              f"font-family:\n{top(fontes, 8)}",
              f"font-size:\n{top(tamanhos, 12)}",
              f"border-radius:\n{top(raios, 8)}",
              f"box-shadow:\n{top(sombras, 6)}",
              f"Classes de componente:\n{top(comps, 30)}"]
    if tailwind:
        partes.append(f"tailwind.config (início):\n{tailwind}")
    if tokens_json:
        partes.append("Tokens em JSON:\n" + "\n".join(f"  {k}: {v}" for k, v in list(tokens_json.items())[:150]))
    if arquivos_fonte:
        partes.append("Arquivos de fonte na pasta: " + ", ".join(sorted(arquivos_fonte)[:12]))
    if textos:   # o guia do sistema, na íntegra até o limite: é daí que saem as regras de uso
        resto, blocos = MAX_TEXTO, []
        for _, arq, t in sorted(textos)[:6]:
            if resto <= 200:
                break
            blocos.append(f"--- {arq} ---\n{t[:resto]}")
            resto -= min(len(t), resto)
        partes.append("Documentação do design system:\n" + "\n\n".join(blocos))
    return "\n\n".join(partes)


def _validar(d: dict, nome: str, pasta: str) -> dict:
    tokens = {}
    for k, v in (d.get("tokens") or {}).items() if isinstance(d.get("tokens"), dict) else []:
        k, v = str(k).strip(), str(v).strip().rstrip(";")
        k = k if k.startswith("--") else f"--{k}"
        if design_html.token_valido(k, v):
            tokens[k] = v
    if len(tokens) < 4:
        raise ToolError("O modelo não devolveu tokens suficientes para um design system.")
    css = str(d.get("css") or "")
    if "</" in css or "<" in css:
        css = ""
    return {"id": f"ds{int(time.time() * 1000):x}", "nome": (str(d.get("nome") or "") or nome)[:80],
            "pasta": pasta, "tokens": tokens, "css": css[:8000], "notas": str(d.get("notas") or "")[:4000],
            "criado": time.strftime("%Y-%m-%d %H:%M")}


async def extrair(pasta: str, nome: str, spec: dict, esforco: str = "baixo") -> dict:
    """Pasta do projeto → design system novo, gravado. Uma chamada ao modelo, sobre o resumo."""
    raiz = workspace.resolve(pasta)
    user = f"Projeto: {nome or raiz.name}\n\n{resumo(raiz)}"
    mensagens = [{"role": "system", "content": PROMPT.read_text("utf-8")}, {"role": "user", "content": user}]
    texto = ""
    async with aclosing(llm.chat_stream(spec["provider"], spec["model"], mensagens, None, config.NUM_CTX, esforco)) as fluxo:
        async for kind, val in fluxo:
            if kind == "content":
                texto += val
    try:
        d = design_html.ler_json(split_think(texto)[1])
    except ValueError as e:
        raise ToolError(f"O modelo não devolveu o design system em JSON ({e}).") from e
    sistema = _validar(d, nome or raiz.name, workspace.to_host(raiz) or pasta)
    _gravar([*listar(), sistema])
    return sistema


# ------------------------------------------------------------------ usar num design

_NOME_LOGO = re.compile(r"logo|wordmark|marca|brand|lockup|s[ií]mbolo|emblema", re.I)
MAX_SVG = 80_000


def logos(s: dict) -> dict[str, str]:
    """SVGs de marca da pasta do sistema (logo, wordmark, lockup...), lidos na hora: {nome: svg limpo}.
    Sem script, sem on*=, sem link externo — vai inline no documento, que roda num iframe sem rede."""
    pasta, out = Path(s.get("pasta") or ""), {}
    if not pasta.is_dir():
        return out
    for atual, pastas, arquivos in os.walk(pasta):
        pastas[:] = [p for p in pastas if p not in IGNORAR and not p.startswith(".")]
        for nome in sorted(arquivos):
            f = Path(atual) / nome
            if f.suffix.lower() != ".svg" or not _NOME_LOGO.search(f.stem) or len(out) >= 6:
                continue
            try:
                if f.stat().st_size > MAX_SVG:
                    continue
                svg = f.read_text("utf-8", "ignore")
            except OSError:
                continue
            i = svg.lower().find("<svg")
            if i < 0:
                continue
            svg = svg[i:svg.lower().rfind("</svg>") + 6]
            svg = re.sub(r"<script\b.*?</script\s*>", "", svg, flags=re.S | re.I)
            svg = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", svg, flags=re.I)
            svg = re.sub(r"\s(?:xlink:)?href\s*=\s*(\"(?!#)[^\"]*\"|'(?!#)[^']*')", "", svg, flags=re.I)
            out[design_html.slug(f.stem)] = svg
    return out


def aplicar_logos(html: str, s: dict) -> str:
    """Onde o modelo marcou `<span data-logo="nome">`, entra o SVG oficial (a cor segue o `color`)."""
    marcas = logos(s) if 'data-logo="' in html else {}
    if not marcas:
        return html
    def troca(m: re.Match) -> str:
        svg = marcas.get(m.group(3)) or next(iter(marcas.values()))
        return f"<{m.group(1)}{m.group(2)}>{svg}</{m.group(1)}>"
    return re.sub(r'<(span|div|a|i)(\s[^>]*\bdata-logo="([\w-]+)"[^>]*)>(.*?)</\1>', troca, html, flags=re.S)


def para_prompt(s: dict) -> str:
    tokens = "\n".join(f"  {k}: {v};" for k, v in s["tokens"].items())
    classes = sorted(set(re.findall(r"\.([a-zA-Z][\w-]*)", s.get("css") or "")))
    marcas = logos(s)
    return (f"Design system obrigatório: “{s['nome']}”. Use estes tokens (os nomes e valores exatos):\n{tokens}\n"
            + (f"Classes de componente já prontas (use em vez de recriar): {', '.join('.' + c for c in classes[:40])}\n" if classes else "")
            + (f"Logo oficial da marca (SVG): {', '.join(marcas)}. Onde a marca aparece (topo, rodapé), escreva "
               f'`<span class="logo" data-logo="{next(iter(marcas))}" role="img" aria-label="{s["nome"]}"></span>` — o sistema põe o SVG '
               "oficial dentro (a cor segue o `color` do elemento e a altura é 1.25em do font-size). Nunca desenhe nem "
               "escreva a logo em texto.\n" if marcas else "")
            + (f"Notas de estilo: {s['notas']}\n" if s.get("notas") else "")
            + "Se as notas limitam o peso da fonte, declare font-weight em todo título (h1–h4 saem em 700 por padrão).")


def css_bloco(s: dict) -> str:
    return f"/* design system: {s['nome']} */\n{s['css'].strip()}\n" if s.get("css") else ""


def do_documento(html: str) -> dict | None:
    m = re.search(r'<meta\s+name="forja-sistema"\s+content="([^"]*)"', html)
    if not m:
        return None
    try:
        return pegar(m.group(1))
    except ToolError:
        return None   # apagado depois: o design segue com o que já tem


def aplicar(html: str, s: dict) -> str:
    """Num design que já existe, sem IA: tokens do sistema no :root, CSS dos componentes no fim do
    <style> (uma vez) e a marca no <head> para as próximas gerações seguirem o sistema."""
    html, _ = design_html.aplicar(html, {"tokens": s["tokens"]})
    if s.get("css") and f"/* design system: {s['nome']} */" not in html:
        html, _ = design_html.aplicar(html, {"css": css_bloco(s)})
    html = re.sub(r'<meta\s+name="forja-sistema"[^>]*>\s*', "", html)
    return re.sub(r"</head>", f'<meta name="forja-sistema" content="{s["id"]}">\n</head>', html, count=1, flags=re.I)

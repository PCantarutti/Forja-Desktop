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


def resumo(pasta: Path) -> str:
    """O que se repete no código da pasta, em poucas linhas (é isso que vai ao modelo)."""
    vars_, cores, fontes, tamanhos, raios, sombras, comps = Counter(), Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    tailwind, lidos = "", 0
    for atual, pastas, arquivos in os.walk(pasta):
        pastas[:] = [p for p in pastas if p not in IGNORAR and not p.startswith(".")]
        for nome in arquivos:
            f = Path(atual) / nome
            if lidos >= MAX_ARQUIVOS:
                break
            if nome.startswith("tailwind.config"):
                tailwind = f.read_text("utf-8", "ignore")[:4000]
                continue
            if f.suffix.lower() not in EXTENSOES:
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
        raise ToolError(f"Não achei CSS nem componentes em {pasta}.")

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
            "pasta": pasta, "tokens": tokens, "css": css[:8000], "notas": str(d.get("notas") or "")[:1500],
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

def para_prompt(s: dict) -> str:
    tokens = "\n".join(f"  {k}: {v};" for k, v in s["tokens"].items())
    classes = sorted(set(re.findall(r"\.([a-zA-Z][\w-]*)", s.get("css") or "")))
    return (f"Design system obrigatório: “{s['nome']}”. Use estes tokens (os nomes e valores exatos):\n{tokens}\n"
            + (f"Classes de componente já prontas (use em vez de recriar): {', '.join('.' + c for c in classes[:40])}\n" if classes else "")
            + (f"Notas de estilo: {s['notas']}" if s.get("notas") else ""))


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

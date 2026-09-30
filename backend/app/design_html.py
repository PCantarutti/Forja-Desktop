"""HTML do Design sem dependência nova: índice de elementos por posição no texto (html.parser da
stdlib), carimbo de data-fid, contexto de um fragmento e aplicação de patch por fid.

Tudo trabalha no texto original — nada de parsear e reserializar o documento, que reescreveria
aspas, espaços e entidades de quem não foi tocado.

ponytail: fechamento implícito (<p> sem </p>, <li> seguido de <li>) só é entendido quando a tag
de fim do pai chega; o modelo quase sempre fecha tudo. Se um dia virar problema, parse5/html5lib.
"""
from __future__ import annotations

import json
import re
import secrets
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
        "track", "wbr"}
# Não recebem data-fid: não aparecem no canvas (ou o patch delas não faria sentido).
SEM_FID = {"html", "head", "meta", "title", "link", "script", "base", "br", "wbr", "noscript"}
_ATTR_FID = re.compile(r"""\sdata-fid\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.I)


class _Indice(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=False)
        self.html = html
        self.linhas = [0] + [m.end() for m in re.finditer("\n", html)]
        self.els: list[dict] = []
        self.pilha: list[int] = []

    def _off(self) -> int:
        linha, col = self.getpos()
        return self.linhas[linha - 1] + col

    def _abre(self, tag: str, attrs, fecha: bool) -> None:
        ini = self._off()
        txt = self.get_starttag_text() or ""
        el = {"tag": tag, "ini": ini, "fim_tag": ini + len(txt), "fim": None, "attrs": dict(attrs),
              "pai": self.pilha[-1] if self.pilha else None, "txt": txt}
        self.els.append(el)
        if fecha or tag in VOID:
            el["fim"] = el["fim_tag"]
        else:
            self.pilha.append(len(self.els) - 1)

    def handle_starttag(self, tag, attrs):
        self._abre(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._abre(tag, attrs, True)

    def handle_endtag(self, tag):
        ini = self._off()
        fim = self.html.find(">", ini) + 1 or len(self.html)
        for j in range(len(self.pilha) - 1, -1, -1):
            if self.els[self.pilha[j]]["tag"] == tag:
                for k in self.pilha[j + 1:]:   # filhos sem tag de fim fecham onde o pai fecha
                    self.els[k]["fim"] = ini
                self.els[self.pilha[j]]["fim"] = fim
                del self.pilha[j:]
                return
        # tag de fim solta: ignora


def indexar(html: str) -> list[dict]:
    p = _Indice(html)
    p.feed(html)
    p.close()
    for k in p.pilha:
        p.els[k]["fim"] = len(html)
    return p.els


def _fid(el: dict) -> str | None:
    return el["attrs"].get("data-fid")


def _com_fid(txt: str, fid: str) -> str:
    """Tag de abertura com data-fid=fid (tira o que houver e põe antes do > ou />)."""
    txt = _ATTR_FID.sub("", txt)
    corte = len(txt) - (2 if txt.endswith("/>") else 1)
    return f'{txt[:corte].rstrip()} data-fid="{fid}"{txt[corte:]}'


def _novo_id(usados: set[str]) -> str:
    while (f := secrets.token_hex(3)) in usados:
        pass
    usados.add(f)
    return f


def carimbar(html: str) -> str:
    """Põe data-fid em todo elemento que não tem. Existentes nunca mudam; repetido (o modelo copiou
    um bloco inteiro) ganha id novo a partir da segunda ocorrência. Idempotente."""
    els = indexar(html)
    usados = {f for e in els if (f := _fid(e))}
    vistos: set[str] = set()
    trocas = []
    for e in els:
        if e["tag"] in SEM_FID:
            continue
        f = _fid(e)
        if f and f not in vistos:
            vistos.add(f)
            continue
        trocas.append((e, _novo_id(usados)))
    for e, f in reversed(trocas):
        html = html[:e["ini"]] + _com_fid(e["txt"], f) + html[e["fim_tag"]:]
    return html


def por_fid(els: list[dict], fid: str) -> dict | None:
    return next((e for e in els if _fid(e) == fid), None)


def outer(html: str, fid: str) -> str | None:
    e = por_fid(indexar(html), fid)
    return html[e["ini"]:e["fim"]] if e else None


def _rotulo(e: dict) -> str:
    cls = "".join(f".{c}" for c in (e["attrs"].get("class") or "").split())
    return f'{e["tag"]}{cls}[data-fid={_fid(e)}]' if _fid(e) else f'{e["tag"]}{cls}'


def ancestrais(els: list[dict], e: dict) -> str:
    """body > section.hero[data-fid=a1] > div.card[data-fid=b2] — sem conteúdo."""
    cadeia, i = [], e["pai"]
    while i is not None and els[i]["tag"] != "html":
        cadeia.append(_rotulo(els[i]))
        i = els[i]["pai"]
    return " > ".join(reversed(cadeia)) or "(topo)"


# ------------------------------------------------------------------ CSS

def _estilos(html: str, els: list[dict]) -> list[dict]:
    return [e for e in els if e["tag"] == "style"]


def _regras(css: str) -> list[tuple[str, str]]:
    """(prelúdio, regra inteira) no nível de cima, contando chaves."""
    out, i, n = [], 0, len(css)
    while i < n:
        a = css.find("{", i)
        if a < 0:
            break
        prel = css[i:a].strip()
        prof, j = 1, a + 1
        while j < n and prof:
            prof += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        out.append((re.sub(r"/\*.*?\*/", "", prel, flags=re.S).strip(), css[i:j].strip()))
        i = j
    return out


def root_css(html: str) -> str:
    m = re.search(r":root\s*\{[^}]*\}", html)
    return m.group(0) if m else ""


def css_de(html: str, els: list[dict], alvos: list[dict]) -> str:
    """Regras de <style> que citam classe, id ou tag dos alvos (ou de quem está dentro deles).
    :root vai à parte (tokens); @media entra só com as regras de dentro que batem."""
    classes, ids, tags = set(), set(), {e["tag"] for e in alvos}
    for a in alvos:
        for e in els:
            if a["ini"] <= e["ini"] < a["fim"]:
                classes.update((e["attrs"].get("class") or "").split())
                if e["attrs"].get("id"):
                    ids.add(e["attrs"]["id"])

    def bate(sel: str) -> bool:
        if sel.startswith(":root"):
            return False
        return (any(re.search(rf"\.{re.escape(c)}(?![\w-])", sel) for c in classes)
                or any(re.search(rf"#{re.escape(i)}(?![\w-])", sel) for i in ids)
                or any(re.search(rf"(^|[\s,>+~(]){re.escape(t)}(?![\w-])", sel) for t in tags))

    achadas = []
    for st in _estilos(html, els):
        css = html[st["fim_tag"]:html.rfind("<", st["fim_tag"], st["fim"])]
        for prel, regra in _regras(css):
            if prel.startswith("@media") or prel.startswith("@supports"):
                corpo = regra[regra.find("{") + 1:regra.rfind("}")]
                dentro = [r for p, r in _regras(corpo) if bate(p)]
                if dentro:
                    achadas.append(prel + " {\n  " + "\n  ".join(dentro) + "\n}")
            elif not prel.startswith("@") and bate(prel):
                achadas.append(regra)
    return "\n".join(achadas)


def contexto(html: str, fids: list[str]) -> str:
    """Só o que a edição precisa: tokens, cada alvo com a cadeia de ancestrais e as regras CSS."""
    els = indexar(html)
    alvos = []
    for f in fids:
        e = por_fid(els, f)
        if not e:
            raise ValueError(f"Elemento {f} não existe mais no documento.")
        alvos.append(e)
    partes = [f"Tokens (bloco :root):\n{root_css(html) or '(nenhum)'}"]
    for e in alvos:
        partes.append(f"Elemento alvo data-fid={_fid(e)}\nAncestrais: {ancestrais(els, e)}\n"
                      f"{html[e['ini']:e['fim']]}")
    if css := css_de(html, els, alvos):
        partes.append(f"Regras CSS do <style> que afetam esses elementos:\n{css}")
    return "\n\n".join(partes)


# ------------------------------------------------------------------ resposta do modelo

def ler_json(texto: str) -> dict:
    """Resposta de edição → dict. Uma tentativa de reparo: cerca de código, texto em volta,
    quebra de linha crua dentro de string e vírgula sobrando. Falhou de novo: ValueError."""
    t = re.sub(r"^```\w*\s*|\s*```$", "", texto.strip())
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("A resposta não trouxe um objeto JSON.")
    t = t[a:b + 1]
    for tentativa in (t, re.sub(r",\s*([}\]])", r"\1", t)):
        try:
            d = json.loads(tentativa, strict=False)
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            continue
    raise ValueError("A resposta não é um JSON válido.")


def _tokens(html: str, tokens: dict) -> str:
    m = re.search(r":root\s*\{([^}]*)\}", html)
    if not m:
        raise ValueError("O documento não tem bloco :root para receber os tokens.")
    corpo = m.group(1)
    for nome, valor in tokens.items():
        nome, valor = str(nome).strip(), str(valor).strip().rstrip(";")
        nome = nome if nome.startswith("--") else f"--{nome}"
        if not token_valido(nome, valor):
            raise ValueError(f"Token inválido: {nome}")
        linha = re.compile(rf"({re.escape(nome)}\s*:)[^;}}]*")
        corpo = linha.sub(lambda x: f"{x.group(1)} {valor}", corpo, count=1) if linha.search(corpo) \
            else corpo.rstrip() + f"\n  {nome}: {valor};\n"
    return html[:m.start(1)] + corpo + html[m.end(1):]


_CICLO = re.compile(r"(--[\w-]+)\s*:\s*var\(\s*\1\s*[,)][^;}]*;?")


_SO_MONO = re.compile(r"((?:font-family|--[\w-]*(?:font|fonte|mono)[\w-]*)\s*:[^;{}]*?),\s*monospace\b")   # também em token


def sem_ciclos(css: str) -> str:
    """Tira `--x: var(--x)`: token que aponta para si mesmo é inválido e, na seção onde aparece, apaga
    TODOS os tamanhos, cores e espaços que dependem dele (o hero inteiro virava texto cru). E fonte
    mono da web (Space Mono, JetBrains) sem rede cai no genérico `monospace` = Courier: entra Consolas antes."""
    css = _CICLO.sub("", css)
    def mono(m: re.Match) -> str:   # ui-monospace não existe no Chromium do Windows: o que vale é o Consolas
        if "consolas" in m.group(1).lower():
            return m.group(0)
        return re.sub(r",\s*ui-monospace\s*$", "", m.group(1)) + ", ui-monospace, Consolas, monospace"
    return _SO_MONO.sub(mono, css)


def aplicar(html: str, resp: dict) -> tuple[str, list[str]]:
    """Aplica {patches:[{fid, html}], css?, tokens?}. Tudo ou nada: qualquer coisa errada levanta
    ValueError e o documento original fica como estava. Devolve (html novo, fids que mudaram)."""
    patches = resp.get("patches") or []
    if not isinstance(patches, list) or not all(
            isinstance(p, dict) and isinstance(p.get("fid"), str) and isinstance(p.get("html"), str) for p in patches):
        raise ValueError("'patches' deve ser uma lista de {fid, html}.")
    css, tokens = resp.get("css") or "", resp.get("tokens") or {}
    if not isinstance(css, str) or not isinstance(tokens, dict):
        raise ValueError("'css' deve ser texto e 'tokens' um objeto.")
    css = sem_ciclos(css)
    if not (patches or css.strip() or tokens):
        raise ValueError("A resposta não trouxe nenhuma mudança.")

    els = indexar(html)
    alvos = []
    for p in patches:
        e = por_fid(els, p["fid"])
        if not e:
            raise ValueError(f"Elemento {p['fid']} não existe no documento.")
        novo = p["html"].strip()
        sub = indexar(novo)
        raizes = [s for s in sub if s["pai"] is None]
        inteiro = len(raizes) == 1 and raizes[0]["ini"] == 0 and raizes[0]["fim"] == len(novo)
        # trocar a tag é permitido (a → button), menos em body e style, que o canvas trata à parte
        if not inteiro or (e["tag"] in ("body", "style") and raizes[0]["tag"] != e["tag"]):
            raise ValueError(f"O patch de {p['fid']} não é um único elemento HTML completo.")
        # a raiz do patch é o mesmo elemento: fica com o fid do alvo, venha o que vier
        novo = _com_fid(raizes[0]["txt"], p["fid"]) + novo[raizes[0]["fim_tag"]:]
        alvos.append((e, novo))
    alvos.sort(key=lambda x: x[0]["ini"])
    for (a, _), (b, _) in zip(alvos, alvos[1:]):
        if b["ini"] < a["fim"]:
            raise ValueError("Dois patches no mesmo trecho (um elemento dentro do outro).")
    for e, novo in reversed(alvos):
        html = html[:e["ini"]] + novo + html[e["fim"]:]
    mudou = [p["fid"] for p in patches]

    if tokens:
        html = _tokens(html, tokens)
    if css.strip():
        if "</" in css:
            raise ValueError("O CSS do patch tem tag HTML.")
        st = [e for e in indexar(html) if e["tag"] == "style"]
        if st:   # no fim do último <style>: vence pela ordem da cascata
            fecha = html.rfind("<", st[-1]["fim_tag"], st[-1]["fim"])
            html = html[:fecha].rstrip() + "\n" + css.strip() + "\n" + html[fecha:]
        else:
            i = html.lower().find("</head>")
            if i < 0:
                raise ValueError("Documento sem <head> para receber o CSS.")
            html = html[:i] + f"<style>\n{css.strip()}\n</style>" + html[i:]
    html = carimbar(html)
    if tokens or css.strip():   # o <style> mexido vai inteiro para o canvas (patch pelo fid dele)
        els = indexar(html)
        alvo = [e for e in els if e["tag"] == "style"]
        if tokens and not css.strip():
            m = re.search(r":root\s*\{", html)
            alvo = [e for e in alvo if e["fim_tag"] <= m.start() < e["fim"]] or alvo
        if alvo and (f := _fid(alvo[-1])) and f not in mudou:
            mudou.append(f)
    return html, mudou


# ------------------------------------------------------------------ seções e esqueleto (fase 3)

_TOKEN_NOME = re.compile(r"--[\w-]+")


def token_valido(nome: str, valor: str) -> bool:
    return bool(_TOKEN_NOME.fullmatch(nome)) and not re.search(r"[;{}<>]", valor) and 0 < len(valor) <= 200


def slug(texto: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40]


def _attr(txt: str, nome: str, valor: str | None) -> str:
    """Tag de abertura com o atributo `nome` trocado (None = removido)."""
    txt = re.sub(rf"""\s{re.escape(nome)}(\s*=\s*("[^"]*"|'[^']*'|[^\s>]+))?(?=[\s/>])""", "", txt, flags=re.I)
    if valor is None:
        return txt
    corte = len(txt) - (2 if txt.endswith("/>") else 1)
    return f'{txt[:corte].rstrip()} {nome}="{valor}"{txt[corte:]}'


def secoes(html: str) -> list[dict]:
    """Seções de topo: filhas diretas do <body> com data-section."""
    els = indexar(html)
    return [e for e in els if e["pai"] is not None and els[e["pai"]]["tag"] == "body"
            and e["attrs"].get("data-section")]


PLACEHOLDER_CSS = ("[data-placeholder]{padding:4rem 1.5rem;text-align:center;font:500 1rem system-ui,sans-serif;"
                   "color:#8a8a8a;border:2px dashed #d4d4d4;margin:1rem;border-radius:12px}"
                   "body>[data-slide][data-placeholder]{display:grid;place-items:center;font-size:3rem;margin:0 auto 48px}")
_BASE_CSS = """*,*::before,*::after{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;font-family:var(--fonte-texto, system-ui, sans-serif);color:var(--cor-texto, #222);background:var(--cor-fundo, #fff);line-height:1.6}
h1,h2,h3,h4{font-family:var(--fonte-titulo, inherit);line-height:1.2;margin:0 0 .5em}
img,svg{max-width:100%;display:block}
.container{width:min(1160px,100% - 2*var(--esp-4, 1.5rem));margin-inline:auto}
.selo{display:inline-flex;align-items:center;gap:.4em;padding:.35em .9em;border-radius:999px;font-size:var(--texto-sm, .875rem);font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--cor-primaria);background:color-mix(in srgb, var(--cor-primaria) 10%, transparent)}
.secao-cabeca{text-align:center;max-width:720px;margin:0 auto var(--esp-6, 3rem)}
.secao-cabeca h2{font-size:var(--texto-3xl, 3rem);letter-spacing:-.02em;margin:var(--esp-3, .75rem) 0}
.secao-cabeca p{font-size:var(--texto-lg, 1.25rem);color:color-mix(in srgb, currentColor 70%, transparent);margin:0}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:.5em;padding:.9em 1.6em;border-radius:var(--raio-md, 10px);font-weight:600;text-decoration:none;border:1px solid transparent;cursor:pointer;transition:transform .2s, box-shadow .2s, background .2s;white-space:nowrap}
.btn-primario{background:var(--cor-primaria);color:#fff;box-shadow:var(--sombra-sm, 0 1px 2px rgba(0,0,0,.2))}
.btn-primario:hover{transform:translateY(-2px);box-shadow:var(--sombra-md, 0 8px 20px rgba(0,0,0,.25))}
.btn-secundario{background:var(--cor-superficie, #fff);color:var(--cor-texto);border-color:var(--cor-borda, #ddd)}
.btn-secundario:hover{border-color:var(--cor-primaria);color:var(--cor-primaria)}
.cartao{background:var(--cor-superficie, #fff);border:1px solid var(--cor-borda, #e5e5e5);border-radius:var(--raio-lg, 20px);padding:var(--esp-4, 1.5rem);box-shadow:var(--sombra-sm, none);transition:transform .25s, box-shadow .25s}
.cartao:hover{transform:translateY(-4px);box-shadow:var(--sombra-md, 0 12px 32px rgba(0,0,0,.08))}
.icone{display:grid;place-items:center;width:52px;height:52px;border-radius:var(--raio-md, 12px);color:var(--cor-primaria);background:color-mix(in srgb, var(--cor-primaria) 12%, transparent)}
.icone svg{width:26px;height:26px}
.preco{white-space:nowrap;font-size:var(--texto-3xl, 3rem);font-weight:800;letter-spacing:-.02em;line-height:1}
.faq-lista{max-width:760px;margin:0 auto}
.faq-lista details{border-bottom:1px solid var(--cor-borda, #e5e5e5);padding:var(--esp-4, 1.5rem) 0}
.faq-lista summary{display:flex;justify-content:space-between;gap:1rem;font-weight:600;font-size:var(--texto-lg, 1.125rem);cursor:pointer;list-style:none}
.faq-lista summary::-webkit-details-marker{display:none}
.faq-lista summary::after{content:"+";color:var(--cor-primaria);font-size:1.5em;line-height:1}
.faq-lista details[open] summary::after{content:"−"}
.faq-lista details p{margin:var(--esp-3, .75rem) 0 0;color:color-mix(in srgb, currentColor 72%, transparent)}
.estrelas{display:inline-flex;gap:2px;color:#f59e0b}
.estrelas svg{width:18px;height:18px;fill:currentColor}
[data-logo]{display:inline-flex;align-items:center;line-height:0}
[data-logo] svg{height:1.25em;width:auto;fill:currentColor}
.grade{display:grid;gap:var(--esp-4, 1.5rem);grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.fundo-alt{background:var(--cor-fundo-alt, color-mix(in srgb, var(--cor-texto) 3%, var(--cor-fundo)))}
.fundo-escuro{background:var(--cor-escura, #0f172a);color:var(--cor-sobre-escura, #f1f5f9)}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}"""


def _placeholder(nome: str, slide: bool = False, tela: bool = False, pagina: str = "") -> str:
    extra = " data-slide" if slide else " data-tela" if tela else ""
    extra += f' data-pagina="{pagina}"' if pagina else ""
    o_que = "o slide" if slide else "a tela" if tela else "a seção"
    return f'<section data-section="{nome}"{extra} data-placeholder="1">Gerando {o_que} “{nome}”…</section>'


def esqueleto(plano: dict) -> str:
    """HTML base do plano aprovado: tokens no :root, CSS base e um placeholder por seção."""
    tokens = "\n".join(f"  {k}: {v};" for k, v in plano["tokens"].items())
    slides = plano.get("tipo") == "slides"   # deck: cada seção é um slide 1920x1080
    tela = plano.get("tipo") == "prototipo"   # protótipo: cada seção é uma tela, uma por vez
    corpo = "\n".join(_placeholder(s["nome"], slides, tela) for s in plano["secoes"])
    titulo = re.sub(r"[<>&]", "", plano["titulo"])
    base = _BASE_CSS + ("\n" + SLIDES_CSS if slides else "") + ("\n" + PROTOTIPO_CSS if tela else "")
    # estilo comum das fotos (skill gerar-imagens): vai junto com o documento para o registro dos slots
    estilo = re.sub(r'["<>&]', "", plano.get("estilo_imagens") or "")
    meta_estilo = f'<meta name="forja-estilo-imagens" content="{estilo}">\n' if estilo else ""
    return (f'<!doctype html>\n<html lang="pt-BR">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{titulo}</title>\n{meta_estilo}'
            f"<style>\n:root {{\n{tokens}\n}}\n{base}\n{PLACEHOLDER_CSS}\n</style>\n</head>\n<body>\n{corpo}\n{PROTOTIPO_JS if tela else ""}\n</body>\n</html>\n")


def placeholder(html: str, nome: str) -> str | None:
    e = next((e for e in secoes(html) if e["attrs"].get("data-section") == nome and "data-placeholder" in e["attrs"]), None)
    return _fid(e) if e else None


def remover(html: str, fid: str) -> str:
    e = por_fid(indexar(html), fid)
    if not e:
        return html
    fim = e["fim"] + (html[e["fim"]:e["fim"] + 1] == "\n")   # leva a quebra de linha junto
    return html[:e["ini"]] + html[fim:]


def limpar_placeholders(html: str) -> str:
    """Fim da geração em etapas (inclusive cancelada): some o que não foi gerado e a regra deles."""
    for e in reversed([e for e in secoes(html) if "data-placeholder" in e["attrs"]]):
        html = html[:e["ini"]] + html[e["fim"]:]
    return html.replace(PLACEHOLDER_CSS + "\n", "").replace(PLACEHOLDER_CSS, "")


def inserir_secao(html: str, nome: str, pagina: str = "") -> str:
    """Placeholder novo antes do rodapé (se a última seção for rodapé) ou no fim do <body>."""
    secs = secoes(html)
    ultima = secs[-1] if secs else None
    slide = e_slides(html)   # num deck o slide novo vai para o fim
    tela = e_prototipo(html)
    if ultima and not slide and not tela and (ultima["tag"] == "footer" or slug(ultima["attrs"].get("data-section")) in ("rodape", "footer")
                                             or ultima["attrs"].get("data-pagina") == "*"):
        pos = ultima["ini"]
    else:
        pos = html.lower().rfind("</body>")
        if pos < 0:
            raise ValueError("Documento sem </body>.")
        for marca in ("<script data-forja-prototipo", "<script data-forja-paginas"):
            if (js := html.find(marca)) >= 0:
                pos = min(pos, js)   # antes do runtime (protótipo ou páginas)
    return carimbar(html[:pos] + _placeholder(nome, slide, tela, pagina) + "\n" + html[pos:])


def ler_secao(texto: str, nome: str, slide: bool = False, tela: bool = False, pagina: str = "") -> tuple[str, str]:
    """Resposta da geração de uma seção → (<section> com data-section=nome, css). O <style> pode vir
    depois da seção ou (modelo distraído) dentro dela: sai de lá e vai para o <head>."""
    from .parsing import split_think
    texto = split_think(texto)[1]
    texto = re.sub(r"```\w*", "", texto)
    css = "\n".join(m.group(1).strip() for m in re.finditer(r"<style[^>]*>(.*?)</style\s*>", texto, re.S | re.I))
    sem_style = re.sub(r"<style[^>]*>.*?</style\s*>", "", texto, flags=re.S | re.I)
    i = re.search(r"<section[\s>]", sem_style, re.I)
    if not i:
        raise ValueError("a resposta não trouxe um <section>")
    trecho = sem_style[i.start():]
    raiz = indexar(trecho)[0]
    if raiz["fim"] >= len(trecho) and not trecho.rstrip().lower().endswith("</section>"):
        raise ValueError("a seção veio incompleta (sem </section>)")
    sec = trecho[:raiz["fim"]]
    tag = _attr(_attr(raiz["txt"], "data-placeholder", None), "data-section", nome)
    if slide:   # num deck, toda seção de topo é um slide, diga o modelo o que disser
        tag = _attr(tag, "data-slide", "")
    if tela:    # no protótipo, toda seção de topo é uma tela
        tag = _attr(tag, "data-tela", "")
    if pagina:  # site com páginas: a seção fica na página em que foi pedida
        tag = _attr(tag, "data-pagina", pagina)
    return tag + sec[len(raiz["txt"]):], css


def cobertura(html: str, fids: list[str]) -> list[str]:
    """Tira da lista quem está dentro de outro da lista: o patch do pai já cobre o filho."""
    els = indexar(html)
    alvos = [(f, por_fid(els, f)) for f in fids]
    # o fid que sumiu fica na lista: quem chama (contexto) acusa o erro
    return [f for f, e in alvos if not e or not any(o is not e and o["ini"] <= e["ini"] and e["fim"] <= o["fim"]
                                                     for _, o in alvos if o)]


# ------------------------------------------------------------------ slides e export (fase 4)

SLIDES_CSS = """body{background:#e5e5e5}
body>[data-slide]{width:1920px;height:1080px;overflow:hidden;position:relative;margin:0 auto 48px;background:var(--cor-fundo, #fff);box-shadow:0 8px 32px #0003}
@media print{@page{size:1920px 1080px;margin:0}body{background:none}body>[data-slide]{margin:0;box-shadow:none}body>[data-slide]:not(:last-child){break-after:page}}"""
# no PDF o documento pode ter mexido nisso: o export impõe de novo, por último
IMPRESSAO_SLIDES = ("<style>@page{size:1920px 1080px;margin:0}html,body{margin:0!important;padding:0!important;background:none!important}"
                    "body>[data-slide]{margin:0!important;box-shadow:none!important}"
                    "body>[data-slide]:not(:last-child){break-after:page!important}</style>")


def e_slides(html: str) -> bool:
    return any("data-slide" in e["attrs"] for e in secoes(html))


def limpar_export(html: str, com_fids: bool = False) -> str:
    """HTML para levar embora: sem placeholder e (por padrão) sem data-fid. O inspetor e o CSP nunca
    estão na fonte — são injetados só no canvas —, então não há o que tirar deles."""
    html = limpar_placeholders(html)
    if com_fids:
        return html
    # os atributos de trabalho saem junto (o data-slot fica: diz de onde veio cada imagem)
    html = re.sub(r"""\s(data-prompt|data-slot-status)\s*=\s*("[^"]*"|'[^']*')""", "", html)
    return _ATTR_FID.sub("", html)


# ------------------------------------------------------------------ conversa e atividade

def diff(antes: str, depois: str, limite: int = 1500) -> dict:
    """Diff unificado do documento (sem os data URIs das imagens), para a atividade no chat."""
    import difflib
    from .design_imagens import enxugar

    a, b = enxugar(antes).splitlines(), enxugar(depois).splitlines()
    linhas = list(difflib.unified_diff(a, b, "antes", "depois", n=2, lineterm=""))
    mais = sum(1 for l in linhas if l.startswith("+") and not l.startswith("+++"))
    menos = sum(1 for l in linhas if l.startswith("-") and not l.startswith("---"))
    texto = "\n".join(l if len(l) <= 400 else l[:400] + " …" for l in linhas[:limite])
    if len(linhas) > limite:
        texto += f"\n… (+{len(linhas) - limite} linhas)"
    return {"texto": texto, "mais": mais, "menos": menos}


_SUG = re.compile(r"^\s*(?:\*\*)?SUGEST(?:OES|ÕES)(?:\*\*)?\s*:\s*(.+)$", re.I | re.M)
_MSG = re.compile(r"^\s*(?:\*\*)?MENSAGEM(?:\*\*)?\s*:\s*(.+)$", re.I | re.M)


def sugestoes(bruto) -> list[str]:
    itens = bruto if isinstance(bruto, list) else re.split(r"\s*[|;]\s*", str(bruto or ""))
    return [str(x).strip(" -•")[:140] for x in itens if str(x).strip(" -•")][:4]


def rodape(texto: str) -> tuple[str, list[str]]:
    """Resposta em HTML: o modelo fecha com `MENSAGEM: ...` e `SUGESTOES: a | b | c`."""
    m, s = _MSG.search(texto or ""), _SUG.search(texto or "")
    return (m.group(1).strip()[:600] if m else ""), (sugestoes(s.group(1)) if s else [])


def tweaks_validos(bruto) -> list[dict]:
    """Controles que a IA cria (Claude Design: "custom sliders"): variável nova + faixa + rótulo."""
    out = []
    for t in bruto if isinstance(bruto, list) else []:
        if not isinstance(t, dict):
            continue
        nome = str(t.get("nome") or "").strip()
        nome = nome if nome.startswith("--") else f"--{nome}"
        un = re.sub(r"[^a-z%]", "", str(t.get("unidade") or ""))[:4]
        try:
            mn, mx, passo = float(t.get("min", 0)), float(t.get("max", 100)), float(t.get("passo") or 1)
            valor = float(re.sub(r"[^\d.-]", "", str(t.get("valor"))) or mn)
        except (TypeError, ValueError):
            continue
        rotulo = re.sub(r"[|*/;{}<>]", "", str(t.get("rotulo") or nome))[:40].strip()
        if not _TOKEN_NOME.fullmatch(nome) or mx <= mn or not mn <= valor <= mx:
            continue
        out.append({"nome": nome, "rotulo": rotulo, "min": mn, "max": mx, "passo": passo, "unidade": un,
                    "valor": f"{valor:g}{un}"})
    return out[:8]


def por_tweaks(html: str, tweaks: list[dict]) -> str:
    """Cada tweak vira um token no :root com o rótulo e a faixa num comentário da mesma linha — é de lá
    que o painel de Ajustes tira o slider, e a informação viaja com o documento."""
    m = re.search(r":root\s*\{([^}]*)\}", html)
    if not m:
        raise ValueError("O documento não tem bloco :root para receber os ajustes.")
    corpo = m.group(1)
    for t in tweaks:
        linha = f"{t['nome']}: {t['valor']}; /* ajuste: {t['rotulo']} | {t['min']:g}..{t['max']:g} {t['unidade']} | {t['passo']:g} */"
        velha = re.compile(rf"[ \t]*{re.escape(t['nome'])}\s*:[^;}}]*;?(?:[ \t]*/\*[^*]*\*/)?")
        corpo = velha.sub(lambda _: "  " + linha, corpo, count=1) if velha.search(corpo) else corpo.rstrip() + f"\n  {linha}\n"
    return html[:m.start(1)] + corpo + html[m.end(1):]


# ------------------------------------------------------------------ protótipo interativo

PROTOTIPO_CSS = """body>[data-tela]{display:none;min-height:100vh}
body>[data-tela][data-tela-atual]{display:block}
@media print{body>[data-tela]{display:block!important}body>[data-tela]:not(:last-child){break-after:page}}"""
# Vai no documento (e no export): troca de tela por data-ir, abre/fecha por data-alterna, #hash direto.
PROTOTIPO_JS = """<script data-forja-prototipo>
(function () {
  var telas = function () { return [].slice.call(document.querySelectorAll("body > [data-tela]")); };
  function ir(nome) {
    var ts = telas(), alvo = ts.filter(function (t) { return t.getAttribute("data-section") === nome; })[0] || ts[0];
    if (!alvo) return;
    ts.forEach(function (t) { if (t === alvo) t.setAttribute("data-tela-atual", ""); else t.removeAttribute("data-tela-atual"); });
    window.scrollTo(0, 0);
    try { parent.postMessage({ "forja-design": 1, type: "tela", nome: alvo.getAttribute("data-section") }, "*"); } catch (e) {}
  }
  document.addEventListener("click", function (e) {
    var x = e.target.closest && e.target.closest("[data-ir], [data-alterna]");
    if (!x) return;
    e.preventDefault();
    if (x.hasAttribute("data-ir")) return ir(x.getAttribute("data-ir"));
    var alvo = document.querySelector(x.getAttribute("data-alterna"));
    if (alvo) alvo.hidden = !alvo.hidden;
  });
  window.forjaIrTela = ir;
  window.addEventListener("hashchange", function () { ir(location.hash.slice(1)); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { ir(location.hash.slice(1)); });
  else ir(location.hash.slice(1));
})();
</script>"""


# ------------------------------------------------------------------ site com várias páginas
# Um documento só: cada seção de topo diz a que página pertence (data-pagina="sobre"; "*" = em todas,
# o cabeçalho e o rodapé). As seções continuam no topo, então tudo que é de seção vale igual. Links
# "#/sobre" trocam de página; o runtime vai no documento (e no export) e esconde as outras por CSS
# gerado na hora (não mexe nos nós: sobrevive ao patch do canvas).
PAGINA_COMUM = {"topo", "header", "cabecalho", "menu", "nav", "navegacao", "rodape", "footer"}
PAGINAS_JS = """<script data-forja-paginas>
(function () {
  var css = document.createElement("style");
  function paginas() {
    var vistos = [];
    [].forEach.call(document.querySelectorAll("body > [data-pagina]"), function (s) {
      var p = s.getAttribute("data-pagina");
      if (p !== "*" && vistos.indexOf(p) < 0) vistos.push(p);
    });
    return vistos;
  }
  function ir(nome) {
    var ps = paginas(), p = ps.indexOf(nome) >= 0 ? nome : ps[0];
    if (!p) return;
    css.textContent = 'body > [data-pagina]:not([data-pagina="*"]):not([data-pagina="' + p.replace(/"/g, "") + '"]){display:none!important}';
    if (!css.parentNode) document.head.appendChild(css);
    document.documentElement.setAttribute("data-pagina-atual", p);
    window.scrollTo(0, 0);
    try { parent.postMessage({ "forja-design": 1, type: "pagina", nome: p }, "*"); } catch (e) {}
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#/"]');
    if (!a) return;
    e.preventDefault();
    var p = decodeURIComponent(a.getAttribute("href").slice(2));
    try { history.replaceState(null, "", "#/" + p); } catch (x) {}
    ir(p);
  });
  window.forjaIrPagina = ir;
  var hash = function () { return decodeURIComponent(location.hash.replace(/^#[/]?/, "")); };
  window.addEventListener("hashchange", function () { ir(hash()); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { ir(hash()); });
  else ir(hash());
})();
</script>"""


def paginas(html: str) -> list[str]:
    """Páginas do site, na ordem em que aparecem ([] = página única)."""
    out = []
    for e in secoes(html):
        p = e["attrs"].get("data-pagina")
        if p and p != "*" and p not in out:
            out.append(p)
    return out


def paginar(html: str) -> str:
    """Página única → site com páginas: o que é cabeçalho/rodapé vai para todas ("*"), o resto é a
    página "inicio"; o runtime entra antes do </body>. Idempotente."""
    for e in reversed(secoes(html)):
        if "data-pagina" in e["attrs"]:
            continue
        comum = e["tag"] in ("header", "footer", "nav") or slug(e["attrs"].get("data-section") or "") in PAGINA_COMUM
        html = html[:e["ini"]] + _attr(e["txt"], "data-pagina", "*" if comum else "inicio") + html[e["fim_tag"]:]
    if "data-forja-paginas" not in html:
        corte = html.lower().rfind("</body>")
        html = html[:corte] + PAGINAS_JS + "\n" + html[corte:] if corte >= 0 else html + PAGINAS_JS
    return html


def link_no_menu(html: str, pagina: str, rotulo: str) -> tuple[str, bool]:
    """Link "#/pagina" no fim do <nav> do cabeçalho comum, copiando a classe do último link dele."""
    if f'href="#/{pagina}"' in html:
        return html, True
    els = indexar(html)
    comuns = [e for e in secoes(html) if e["attrs"].get("data-pagina") == "*"]
    for c in comuns:
        nav = next((e for e in els if e["tag"] == "nav" and c["ini"] <= e["ini"] < c["fim"]), None)
        if not nav:
            continue
        links = [e for e in els if e["tag"] == "a" and nav["ini"] <= e["ini"] < nav["fim"]]
        cls = links[-1]["attrs"].get("class") if links else None
        a = f'<a{f" class={chr(34)}{cls}{chr(34)}" if cls else ""} href="#/{pagina}">{re.sub(r"[<>&]", "", rotulo)}</a>'
        pos = links[-1]["fim"] if links else html.rfind("<", nav["fim_tag"], nav["fim"])
        # o link pode estar dentro de <li>: entra um <li> novo depois do último
        li = next((e for e in els if e["tag"] == "li" and links and e["ini"] <= links[-1]["ini"] < e["fim"]), None)
        if li:
            li_cls = li["attrs"].get("class")
            a, pos = f'<li{f" class={chr(34)}{li_cls}{chr(34)}" if li_cls else ""}>{a}</li>', li["fim"]
        return carimbar(html[:pos] + a + html[pos:]), True
    # sem menu nenhum: um cabeçalho simples, comum a todas, com um link por página (tokens do design)
    corpo = re.search(r"<body[^>]*>", html, re.I)
    if not corpo:
        return html, False
    rot = lambda p: re.sub(r"[<>&]", "", p.replace("-", " ").capitalize())   # noqa: E731
    links = "".join(f'<a href="#/{p}" style="color:inherit;text-decoration:none;font-weight:600">{rot(p) if p != pagina else re.sub(r"[<>&]", "", rotulo)}</a>'
                    for p in [*paginas(html), *([pagina] if pagina not in paginas(html) else [])])
    menu = (f'\n<header data-section="menu" data-pagina="*" style="background:var(--cor-fundo, #fff);'
            f'border-bottom:1px solid color-mix(in srgb, currentColor 15%, transparent)">'
            f'<nav class="container" style="display:flex;gap:1.5rem;flex-wrap:wrap;padding:1rem 0">{links}</nav></header>')
    return carimbar(html[:corpo.end()] + menu + html[corpo.end():]), True


def e_prototipo(html: str) -> bool:
    return any("data-tela" in e["attrs"] for e in secoes(html))

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
        if not re.fullmatch(r"--[\w-]+", nome) or re.search(r"[;{}<>]", valor):
            raise ValueError(f"Token inválido: {nome}")
        linha = re.compile(rf"({re.escape(nome)}\s*:)[^;}}]*")
        corpo = linha.sub(lambda x: f"{x.group(1)} {valor}", corpo, count=1) if linha.search(corpo) \
            else corpo.rstrip() + f"\n  {nome}: {valor};\n"
    return html[:m.start(1)] + corpo + html[m.end(1):]


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

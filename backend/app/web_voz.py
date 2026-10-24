"""Web pelo assistente de voz: achar o site oficial, comparar preço em lojas e acompanhar preço e versão nova.

- Nada aqui loga nem compra: abrir o site é com a pessoa (login e compra são sempre dela). O domínio achado é
  falado, para ela conferir antes de digitar qualquer coisa.
- Páginas e resultados de busca são DADO: quem decide o que dizer é o assistente, numa chamada sem ferramentas.
- Acompanhamentos ("me avisa quando a placa baixar de R$ 2.000", "quando sair versão nova do llama.cpp") ficam em
  DATA_DIR/acompanhar.json; a rotina do assistente confere a cada 3 horas e avisa uma vez (depois sai da lista).
"""
from __future__ import annotations

import json
import re
import threading
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse

import httpx

from . import config, web
from .tools import ToolError

# sites que aparecem na busca mas não são "o site" de ninguém, nem loja
NAO_OFICIAL = {"wikipedia.org", "youtube.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
               "reclameaqui.com.br", "tiktok.com", "reddit.com", "glassdoor.com.br", "glassdoor.com", "google.com",
               "play.google.com", "apps.apple.com", "jusbrasil.com.br", "pt.wikipedia.org"}
NAO_LOJA = NAO_OFICIAL | {"zoom.com.br", "buscape.com.br", "tecmundo.com.br", "tudocelular.com", "adrenaline.com.br",
                          "hardware.com.br", "techtudo.com.br", "gamevicio.com", "clubedohardware.com.br"}
PRECO = re.compile(r"R\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?")
DOMINIO = re.compile(r"^(?:https?://)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(/.*)?$", re.I)


def _dominio(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.").lower()


def _de(dom: str, lista: set[str]) -> bool:
    return any(dom == d or dom.endswith("." + d) for d in lista)


def _nome(texto: str) -> str:
    """"Itaú Unibanco" -> "itauunibanco": para comparar o pedido com o nome do domínio."""
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode())


def site_oficial(alvo: str) -> str | None:
    """"itau.com.br" vai direto; "o site do Itaú" passa pela busca. Vence o resultado cujo domínio tem o nome pedido
    (itau.com.br para "Itaú"), mesmo que esteja em NAO_OFICIAL: pedir "o YouTube" é querer o youtube.com, e sem isso
    ele caía no about.youtube. Sem nenhum assim, fica o primeiro que não é rede social, enciclopédia nem reclamação.
    Devolve a URL da raiz do site."""
    alvo = alvo.strip().strip(".")
    if m := DOMINIO.match(alvo.replace(" ", "")):
        return f"https://{m[1].lower()}{m[2] or '/'}"
    nome = _nome(alvo)
    for q in (f"{alvo} site oficial", alvo):  # a busca às vezes volta vazia: uma segunda, com outras palavras
        reserva = None
        for r in web.buscar(q, 8):
            dom = _dominio(r.get("url", ""))
            if not dom:
                continue
            raiz = f"https://{urlparse(r['url']).hostname}/"
            if nome and _nome(dom.split(".")[0]) == nome:
                return raiz
            if not reserva and not _de(dom, NAO_OFICIAL):
                reserva = raiz
        if reserva:
            return reserva
    return None


def _linhas_com_preco(texto: str, n: int = 8) -> list[str]:
    linhas = [l.strip() for l in texto.splitlines() if PRECO.search(l)]
    return [l[:200] for l in linhas[:n]]


def candidatos_preco(produto: str, n_paginas: int = 6) -> list[dict]:
    """O que a busca e as páginas das lojas dizem do preço: [{loja, url, titulo, linhas}] para o modelo escolher.
    Loja que bloqueia robô fica só com o trecho da busca (que muitas vezes já traz o preço)."""
    vistos, resultados = set(), []
    for q in (f"{produto} preço", f"comprar {produto}"):
        for r in web.buscar(q, 8):
            dom = _dominio(r.get("url", ""))
            if dom and dom not in vistos and not _de(dom, NAO_LOJA):
                vistos.add(dom)
                resultados.append(r)
    resultados = resultados[:n_paginas]

    def le(r: dict) -> dict:
        linhas = _linhas_com_preco(r.get("content", ""), 3)
        try:
            pagina = web.ler(r["url"], 60_000)
            linhas += _linhas_com_preco(pagina["text"])
        except ToolError:
            pass
        return {"loja": _dominio(r["url"]), "url": r["url"], "titulo": r.get("title", "")[:150], "linhas": linhas}

    with ThreadPoolExecutor(4) as ex:
        return [c for c in ex.map(le, resultados) if c["linhas"]]


def para_numero(preco: str | float | int) -> float | None:
    """'R$ 3.499,90' -> 3499.9; número passa direto."""
    if isinstance(preco, (int, float)):
        return float(preco)
    if m := re.search(r"(\d+(?:[.,]\d+)?)\s*mil\b", str(preco)):  # "4 mil", "2,5 mil"
        return float(m[1].replace(",", ".")) * 1000
    m = re.search(r"\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:\.\d+)?", str(preco))
    if not m:
        return None
    t = m.group(0)
    return float(t.replace(".", "").replace(",", ".")) if "," in t or re.search(r"\.\d{3}\b", t) else float(t)


# ------------------------------------------------------------------ versão nova (GitHub)

def repo_github(projeto: str) -> str | None:
    """"ggml-org/llama.cpp" direto, ou o primeiro github.com/<dono>/<repo> da busca."""
    if re.fullmatch(r"[\w.-]+/[\w.-]+", projeto.strip()):
        return projeto.strip()
    achados = []
    for r in web.buscar(f"{projeto} github", 10):
        m = re.match(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)", r.get("url", ""))
        if m and m[1].lower() not in ("topics", "orgs", "features", "marketplace", "sponsors"):
            achados.append(f"{m[1]}/{m[2]}")
    # o repositório com o nome exato ganha: "llama.cpp" é ggml-org/llama.cpp, não um binding que aparece antes
    nome = re.sub(r"[^a-z0-9]", "", projeto.lower())
    return next((r for r in achados if re.sub(r"[^a-z0-9]", "", r.split("/")[1].lower()) == nome), achados[0] if achados else None)


def ultima_versao(repo: str) -> str | None:
    """A última release publicada (ou a última tag, para quem não publica release)."""
    h = {"Accept": "application/vnd.github+json", "User-Agent": "Forja"}
    try:
        r = httpx.get(f"https://api.github.com/repos/{repo}/releases/latest", headers=h, timeout=15)
        if r.status_code == 200:
            return r.json().get("tag_name")
        r = httpx.get(f"https://api.github.com/repos/{repo}/tags", headers=h, params={"per_page": 1}, timeout=15)
        if r.status_code == 200 and r.json():
            return r.json()[0]["name"]
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return None


# ------------------------------------------------------------------ acompanhamentos

ARQUIVO = config.DATA_DIR / "acompanhar.json"
_trava = threading.Lock()


def lista() -> list[dict]:
    try:
        return json.loads(ARQUIVO.read_text("utf-8"))
    except (OSError, ValueError):
        return []


def _grava(itens: list[dict]) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO.write_text(json.dumps(itens, ensure_ascii=False, indent=1), "utf-8")


def acompanhar(tipo: str, alvo: str, limite: float | None = None, base: str | None = None, repo: str = "") -> dict:
    item = {"id": uuid.uuid4().hex[:10], "tipo": tipo, "alvo": alvo.strip()[:120], "limite": limite, "base": base,
            "repo": repo, "criado": datetime.now().isoformat(timespec="seconds"), "conferido": None, "ultimo": None}
    with _trava:
        _grava(lista() + [item])
    return item


def atualiza(item_id: str, **campos) -> None:
    with _trava:
        _grava([{**x, **campos} if x["id"] == item_id else x for x in lista()])


def apagar(ids: set[str]) -> list[dict]:
    with _trava:
        resto = [x for x in lista() if x["id"] not in ids]
        _grava(resto)
    return resto

"""Imagens do Design pela skill gerar-imagens (a mesma do modo agente).

O modelo não gera imagem nenhuma: cada foto/ilustração do design vira um SLOT no HTML,
`<img data-slot="hero-paes-4821" data-prompt="..." width height>`. Até sair a imagem, o canvas mostra
um provisório (SVG com o nome do slot). O botão "Gerar N imagens" registra os slots com a própria
ferramenta `imagens_pendentes` (mesma mensagem de ferramenta, mesmos PNGs provisórios, mesma conferência
do código) numa pasta do projeto de design, e abre a fila na tela Imagens. Quando o lote termina, o
aviso "Imagens do site geradas" (lotes._avisar) chama `embutir`: cada PNG/WebP entra no HTML como data
URI WebP e vira uma versão nova — o documento continua autocontido.

`data-slot-status` diz o estado: "pendente" (provisório) ou "pronta" (imagem de verdade embutida).
"""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from urllib.parse import quote

from . import config, db, design_html, imagegen
from .tools import ToolError

# A fila de slots da skill (ferramenta imagens_pendentes + slots.py + lotes com slots_de) não existe em
# toda versão do Forja: sem ela, os slots ficam como provisório e a tela esconde o botão.
DISPONIVEL = hasattr(imagegen, "imagens_pendentes")

_IMG = re.compile(r"<img\b[^>]*>", re.I)
NOME = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")   # o mesmo NOME_SLOT do imagegen
LADO_MAX_WEB = 1600    # a imagem embutida não precisa ser maior que isso numa página
QUALIDADE = 80


def _attr(tag: str, nome: str) -> str | None:
    m = re.search(rf"""\s{re.escape(nome)}\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""", tag, re.I)
    return None if not m else next(g for g in m.groups()[1:] if g is not None)


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v else None
    except ValueError:
        return None


def pasta(conv_id: int) -> Path:
    """Onde os slots moram de verdade (PNG/WebP), com um index.html que aponta para eles: a pasta
    geradas/ dentro da pasta do projeto (design_repo), fora do git."""
    from . import design_repo
    return design_repo.geradas(conv_id)


def provisorio(nome: str, w: int | None, h: int | None) -> str:
    w, h = w or 1024, h or 768
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
           f'<rect width="100%" height="100%" fill="#d9d4cc"/>'
           f'<text x="50%" y="48%" text-anchor="middle" font-family="system-ui,sans-serif" '
           f'font-size="{max(14, min(w, h) // 14)}" fill="#6b645a">{nome}</text>'
           f'<text x="50%" y="58%" text-anchor="middle" font-family="system-ui,sans-serif" '
           f'font-size="{max(11, min(w, h) // 22)}" fill="#8f877b">imagem ainda não gerada</text></svg>')
    return "data:image/svg+xml;charset=utf-8," + quote(svg)


def slots(html: str) -> list[dict]:
    out, vistos = [], set()
    for m in _IMG.finditer(html):
        tag = m.group(0)
        nome = (_attr(tag, "data-slot") or "").strip()
        if not NOME.match(nome) or nome in vistos:
            continue
        vistos.add(nome)
        out.append({"nome": nome, "prompt": (_attr(tag, "data-prompt") or "").strip(),
                    "largura": _int(_attr(tag, "width")), "altura": _int(_attr(tag, "height")),
                    "status": _attr(tag, "data-slot-status") or "pendente", "src": _attr(tag, "src") or ""})
    return out


def _troca(tag: str, src: str, status: str) -> str:
    tag = design_html._attr(tag, "src", src)
    return design_html._attr(tag, "data-slot-status", status)


def preencher(html: str, anterior: str = "") -> str:
    """Toda versão passa por aqui antes de salvar: slot sem imagem ganha o provisório, e o que o modelo
    devolveu sem `src` (o contexto vai enxuto, sem os data URIs) recupera a imagem que já estava pronta."""
    prontas = {s["nome"]: s["src"] for s in slots(anterior) if s["status"] == "pronta" and s["src"].startswith("data:")}
    vistos: set[str] = set()

    def um(m: re.Match) -> str:
        tag = m.group(0)
        nome = (_attr(tag, "data-slot") or "").strip()
        if not NOME.match(nome):
            return tag
        if nome in vistos:   # o modelo repetiu o nome: a segunda imagem ganha outro (senão dividiriam o arquivo)
            k = 2
            while f"{nome}-{k}" in vistos:
                k += 1
            nome = f"{nome}-{k}"
            tag = design_html._attr(tag, "data-slot", nome)
        vistos.add(nome)
        src = _attr(tag, "src") or ""
        if src.startswith("data:") and _attr(tag, "data-slot-status"):
            return tag
        if nome in prontas:
            return _troca(tag, prontas[nome], "pronta")
        return _troca(tag, provisorio(nome, _int(_attr(tag, "width")), _int(_attr(tag, "height"))), "pendente")
    return _IMG.sub(um, html)


def enxugar(texto: str) -> str:
    """O que vai ao modelo, sem os data URIs (uma foto embutida são centenas de KB de base64)."""
    texto = re.sub(r"""(\ssrc\s*=\s*)("data:[^"]*"|'data:[^']*')""", r'\1""', texto)
    return re.sub(r"url\(\s*(['\"]?)data:[^)]*\1\s*\)", "url()", texto)


def _tamanho(w: int | None, h: int | None) -> tuple[int | None, int | None]:
    """A proporção de onde a imagem aparece, no tamanho que o modelo de imagem gera bem."""
    if not (w and h):
        return 1024, 768
    r = w / h
    lw = 1344 if r >= 1.5 else 1024 if r >= 0.75 else 768
    return lw, max(256, min(2048, round(lw / r)))


def _site(root: Path, html: str) -> None:
    """index.html com os slots apontando para img/<slot>.png: é o "código" que a conferência lê e o que
    a tela Imagens mostra como projeto."""
    def um(m: re.Match) -> str:
        tag = m.group(0)
        nome = _attr(tag, "data-slot") or ""
        return design_html._attr(tag, "src", f"img/{nome}.png") if NOME.match(nome) else tag
    (root / "index.html").write_text(_IMG.sub(um, html), "utf-8")


def _estilo(html: str) -> str:
    m = re.search(r'<meta\s+name="forja-estilo-imagens"\s+content="([^"]*)"', html)
    return m.group(1) if m else ""


def registrar(conv_id: int) -> dict:
    """"Gerar N imagens": registra os slots pendentes pela ferramenta da skill e devolve a conversa de
    Imagens do projeto (uma por projeto de design; cada registro novo entra nela)."""
    from . import design
    from .agent import _save

    if not DISPONIVEL:
        raise ToolError("Esta versão do Forja ainda não tem a fila de imagens da skill gerar-imagens.")
    p = design.projeto(conv_id)
    # o mesmo nome em dois <img> (duplicado no Editar, a mesma foto em duas páginas) é o mesmo arquivo:
    # gera uma vez só, e o embutir põe a imagem nos dois
    pend = list({s["nome"]: s for s in slots(p["html"]) if s["status"] != "pronta"}.values())
    if not pend:
        raise ToolError("Não há imagem pendente neste design.")
    sem_prompt = [s["nome"] for s in pend if not s["prompt"]]
    if sem_prompt:
        raise ToolError(f"Slot sem descrição (data-prompt): {', '.join(sem_prompt)}. Peça ao modelo para descrevê-lo.")
    root = pasta(conv_id)
    _site(root, p["html"])
    args = {"estilo": _estilo(p["html"]), "slots": [
        {"nome": s["nome"], "caminho": f"img/{s['nome']}.png", "prompt": s["prompt"],
         **dict(zip(("largura", "altura"), _tamanho(s["largura"], s["altura"])))} for s in pend]}
    res = imagegen.imagens_pendentes(root, args)
    msg = _save(conv_id, role="tool", name="imagens_pendentes", content=res["text"],
                meta={"imagens_pendentes": res["imagens_pendentes"]})
    with db.session() as s:
        imagem = next((c for c in s.query(db.Conversation).filter(db.Conversation.kind == "imagem",
                                                                   db.Conversation.origem.isnot(None))
                       if (c.origem or {}).get("conv_id") == conv_id and not c.archived), None)
        if imagem:
            ids = list(imagem.origem.get("message_ids") or [imagem.origem.get("message_id")])
            imagem.origem = {**imagem.origem, "message_ids": [*ids, msg.id]}
        else:
            imagem = db.Conversation(kind="imagem", title=f"Imagens · {p['titulo']}"[:200], workspace=str(root),
                                     origem={"conv_id": conv_id, "message_id": msg.id, "message_ids": [msg.id]})
            s.add(imagem)
        s.commit()
        return {"id": imagem.id, "kind": "imagem"}


def conversa(conv_id: int) -> int | None:
    with db.session() as s:
        return next((c.id for c in s.query(db.Conversation).filter(db.Conversation.kind == "imagem",
                                                                   db.Conversation.origem.isnot(None))
                     if (c.origem or {}).get("conv_id") == conv_id and not c.archived), None)


def _data_uri(arquivo: Path) -> str:
    from PIL import Image

    with Image.open(arquivo) as img:
        img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
        img.thumbnail((LADO_MAX_WEB, LADO_MAX_WEB))
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=QUALIDADE, method=5)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def embutir(conv_id: int) -> int | None:
    """Fim do lote: o que já saiu (PNG ou o WebP da versão web) entra no HTML. Devolve a versão nova ou
    None se nada mudou. Provisório (PNG marcado pela skill) não conta."""
    from . import design, slots as projeto

    p = design.projeto(conv_id)
    if not p["html"] or p["rodando"]:
        return None
    root = pasta(conv_id)
    novas = {}
    for sl in slots(p["html"]):
        for ext in (".webp", ".png"):
            f = root / "img" / f"{sl['nome']}{ext}"
            if f.is_file() and not projeto.eh_placeholder(f):
                uri = _data_uri(f)
                if uri != sl["src"]:
                    novas[sl["nome"]] = uri
                break
    if not novas:
        return None

    def um(m: re.Match) -> str:
        tag = m.group(0)
        nome = _attr(tag, "data-slot") or ""
        return _troca(tag, novas[nome], "pronta") if nome in novas else tag
    html = _IMG.sub(um, p["html"])
    n = len(novas)
    return design._nova_versao(conv_id, None, html, f"{n} {'imagem gerada' if n == 1 else 'imagens geradas'}",
                               base=p["atual"], rota="imagens",
                               passos=[f"Embutiu {nome} (gerada na tela Imagens)" for nome in novas])


# ------------------------------------------------------------------ imagens da internet
# Opção da tela: em vez de gerar pela skill, o modelo põe o link de uma foto real no src (e mantém
# data-slot/data-prompt). O Forja baixa ao salvar a versão e embute como as geradas: o canvas segue sem
# rede, o export segue autocontido e a foto vai para o git. Link que falhar vira slot (gera depois).

INSTRUCAO_INTERNET = """

IMAGENS DA INTERNET (escolha do usuário, vale acima da regra de slot): em toda foto/ilustração ponha no
`src` o link DIRETO de uma imagem real e de uso livre (Unsplash `https://images.unsplash.com/...`,
Pexels, Wikimedia Commons), terminando no arquivo (jpg/png/webp), sem página HTML no meio. Mantenha
também `data-slot` e `data-prompt` como antes: se o link não abrir, o Forja troca por uma imagem gerada.
Só links que você conhece de verdade; na dúvida, deixe sem `src` (vira slot)."""
MAX_BYTES = 8_000_000
MAX_FOTOS = 12
_SRC_REDE = re.compile(r"""\ssrc\s*=\s*("https?://[^"]+"|'https?://[^']+')""", re.I)


def _host_publico(url: str) -> bool:
    """Só IP público: link malicioso não pode fazer o Forja bater na rede local (roteador, localhost…).
    ponytail: confere o DNS antes da conexão (rebinding entre a checagem e o connect não é coberto)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    try:
        ips = {i[4][0] for i in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))}
    except OSError:
        return False
    return bool(ips) and all(ipaddress.ip_address(ip.split("%")[0]).is_global for ip in ips)


async def _baixar(cliente, url: str) -> str:
    """URL → data URI WebP (≤ LADO_MAX_WEB). Levanta ValueError com o motivo."""
    from PIL import Image
    for _ in range(4):   # redirecionamento na mão: cada destino passa pela mesma conferência
        if not await _em_thread(_host_publico, url):
            raise ValueError("endereço fora da internet pública")
        async with cliente.stream("GET", url) as r:
            if r.is_redirect:
                url = str(r.url.join(r.headers.get("location", "")))
                continue
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}")
            if not r.headers.get("content-type", "").startswith("image/"):
                raise ValueError(f"não é imagem ({r.headers.get('content-type', '?')})")
            dados = bytearray()
            async for pedaco in r.aiter_bytes():
                dados += pedaco
                if len(dados) > MAX_BYTES:
                    raise ValueError("maior que 8 MB")
        try:
            with Image.open(io.BytesIO(bytes(dados))) as img:
                img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
                img.thumbnail((LADO_MAX_WEB, LADO_MAX_WEB))
                buf = io.BytesIO()
                img.save(buf, "WEBP", quality=QUALIDADE, method=5)
        except Exception as e:
            raise ValueError("o arquivo não abre como imagem") from e
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
    raise ValueError("redirecionamentos demais")


async def _em_thread(f, *a):
    import asyncio
    return await asyncio.to_thread(f, *a)


def _vira_slot(tag: str, fonte: str = "") -> str:
    """<img> sem src de rede: vira slot (nome pelo alt se o modelo não deu), e o preencher põe o provisório."""
    tag = design_html._attr(tag, "src", None)
    if not _attr(tag, "data-slot"):
        base = design_html.slug(_attr(tag, "alt") or "imagem")[:60] or "imagem"
        import secrets
        tag = design_html._attr(tag, "data-slot", f"{base}-{secrets.randbelow(9000) + 1000}")
    if not _attr(tag, "data-prompt"):
        tag = design_html._attr(tag, "data-prompt", (_attr(tag, "alt") or "photo").replace('"', "'")[:300])
    return design_html._attr(tag, "data-slot-status", None)


def sem_links(html: str) -> str:
    """Modo skill: foto por URL não entra (o canvas não abre rede); o lugar vira slot."""
    return _IMG.sub(lambda m: _vira_slot(m.group(0)) if _SRC_REDE.search(m.group(0)) else m.group(0), html)


async def fotos_da_internet(html: str) -> tuple[str, list[str]]:
    """Modo internet: baixa as fotos dos links do modelo e embute. (html, passos para a atividade)."""
    import asyncio
    import httpx
    alvos = [m for m in _IMG.finditer(html) if _SRC_REDE.search(m.group(0))][:MAX_FOTOS]
    if not alvos:
        return html, []
    passos: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0), follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0 (Forja Design)"}) as cliente:
        async def uma(m: re.Match):
            tag = m.group(0)
            url = _SRC_REDE.search(tag).group(1)[1:-1]
            try:
                uri = await _baixar(cliente, url)
                nova = design_html._attr(design_html._attr(tag, "src", uri), "data-fonte", url.replace('"', "%22"))
                if not _attr(nova, "data-slot"):
                    nova = _vira_slot(nova)
                    nova = design_html._attr(nova, "src", uri)
                passos.append(f"Baixou a foto de {url[:80]}")
                return design_html._attr(nova, "data-slot-status", "pronta")
            except (ValueError, httpx.HTTPError) as e:
                passos.append(f"Link sem imagem ({e}): virou espaço para gerar — {url[:80]}")
                return _vira_slot(tag)
        novas = await asyncio.gather(*(uma(m) for m in alvos))
    for m, nova in sorted(zip(alvos, novas), key=lambda x: x[0].start(), reverse=True):
        html = html[:m.start()] + nova + html[m.end():]
    return html, passos

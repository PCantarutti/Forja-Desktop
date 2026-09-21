"""Relatório da pesquisa em HTML: template fixo do repositório preenchido pelo backend.

O modelo nunca escreve HTML — ele devolve Markdown e aqui vira página. Tudo é escapado antes de
qualquer conversão: o texto vem de páginas da web e de um LLM, e termina num navegador de verdade.

ponytail: subconjunto de Markdown escrito à mão (é o que o próprio prompt do relatório pede) em vez
de uma dependência nova. Teto: se um dia precisar de tabela ou nota de rodapé, aí entra o `markdown`
no requirements.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path

TEMPLATE = Path(__file__).with_name("relatorio.html")

LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
NEGRITO = re.compile(r"\*\*([^*\n]+)\*\*")
ITALICO = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
CODIGO = re.compile(r"`([^`\n]+)`")
ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
TITULO = re.compile(r"^\s*(#{1,6})\s+(.*)$")


def _inline(texto: str) -> str:
    """Escapa e aplica só as marcas de linha. O href já veio filtrado pela regex (http/https)."""
    s = html.escape(texto, quote=True)
    s = LINK.sub(lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noreferrer">{m.group(1)}</a>', s)
    s = NEGRITO.sub(r"<strong>\1</strong>", s)
    s = ITALICO.sub(r"<em>\1</em>", s)
    return CODIGO.sub(r"<code>\1</code>", s)


def _md(texto: str) -> tuple[str, list[tuple[str, str, int]]]:
    """Markdown -> (HTML, [(id, título, nível) do sumário]). O resto vira parágrafo."""
    out: list[str] = []
    secoes: list[tuple[str, str, int]] = []
    lista: str | None = None

    def fecha() -> None:
        nonlocal lista
        if lista:
            out.append(f"</{lista}>")
            lista = None

    for linha in (texto or "").splitlines():
        if not linha.strip():
            fecha()
            continue
        if m := TITULO.match(linha):
            fecha()
            nivel = min(max(len(m.group(1)), 2), 4)  # # e ## viram h2; o resto desce
            corpo = _inline(m.group(2).strip())
            if nivel <= 3:  # h2 e h3 entram no sumário lateral e ganham âncora
                alvo = f"s{len(secoes) + 1}"
                secoes.append((alvo, m.group(2).strip(), nivel))
                out.append(f'<h{nivel} id="{alvo}">{corpo}</h{nivel}>')
            else:
                out.append(f"<h{nivel}>{corpo}</h{nivel}>")
            continue
        if m := ITEM.match(linha):
            tipo = "ol" if linha.lstrip()[0].isdigit() else "ul"
            if lista != tipo:
                fecha()
                out.append(f"<{tipo}>")
                lista = tipo
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        fecha()
        out.append(f"<p>{_inline(linha.strip())}</p>")
    fecha()
    return "\n".join(out), secoes


def _fontes(pesquisa: dict) -> str:
    """Painel recolhível com a lista numerada, no mesmo desenho do odysseus."""
    uteis = [f for f in pesquisa.get("fontes", [])
             if f.get("status") == "util" and (f.get("url") or "").startswith("http")]
    if not uteis:
        return ""
    itens = "".join(
        f'<a href="{html.escape(f["url"], quote=True)}" target="_blank" rel="noopener noreferrer" '
        f'title="{html.escape(f.get("resumo", "")[:300], quote=True)}">'
        f'<span class="snum">{i}.</span>'
        f'<span>{html.escape(f.get("titulo") or f["url"])}</span>'
        f'<span class="sdomain">{html.escape(f.get("dominio", ""))}</span></a>'
        for i, f in enumerate(uteis, 1))
    return ('<div class="sources-panel"><details><summary>'
            f'Fontes ({len(uteis)})</summary><div class="sources-list">{itens}</div></details></div>')


def _stats(pesquisa: dict) -> str:
    s = pesquisa.get("stats", {})
    segundos = int(s.get("segundos") or 0)
    itens = [(f"{segundos // 60}m{segundos % 60:02d}s", "de pesquisa"),
             (str(s.get("rodadas") or 0), "rodadas"),
             (str(s.get("fontes") or 0), "páginas lidas"),
             (str(s.get("uteis") or 0), "fontes úteis"),
             (s.get("extrator") or "—", "extração"),
             (s.get("escritor") or "—", "relatório")]
    return "\n  ".join(f'<div class="stat"><span class="stat-value">{html.escape(str(v))}</span> '
                       f'{html.escape(r)}</div>' for v, r in itens)


def html_do(pesquisa: dict, markdown: str) -> str:
    """A página inteira, pronta para abrir no navegador (CSS embutido, sem rede)."""
    corpo, secoes = _md(markdown or pesquisa.get("resumo") or pesquisa.get("aviso") or "")
    sumario = "\n      ".join(f'<a href="#{i}" class="depth-{n}">{html.escape(t)}</a>'
                              for i, t, n in secoes)
    aviso = pesquisa.get("aviso") or ""
    return (TEMPLATE.read_text("utf-8")
            .replace("{{TITULO}}", html.escape(pesquisa.get("pergunta") or "Pesquisa"))
            .replace("{{DATA}}", datetime.now().strftime("%d/%m/%Y %H:%M"))
            .replace("{{AVISO}}", f'<div class="aviso">{html.escape(aviso)}</div>' if aviso else "")
            .replace("{{SUMARIO}}", sumario)
            .replace("{{CORPO}}", corpo)
            .replace("{{FONTES}}", _fontes(pesquisa))
            .replace("{{STATS}}", _stats(pesquisa)))

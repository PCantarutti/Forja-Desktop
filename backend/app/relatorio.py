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


def _md(texto: str) -> tuple[str, list[tuple[str, str]]]:
    """Markdown -> (HTML, [(id, título) das seções ##]). O que não for reconhecido vira parágrafo."""
    out: list[str] = []
    secoes: list[tuple[str, str]] = []
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
            nivel = min(len(m.group(1)), 4)
            corpo = _inline(m.group(2).strip())
            if nivel <= 2:
                alvo = f"s{len(secoes) + 1}"
                secoes.append((alvo, m.group(2).strip()))
                out.append(f'<h2 id="{alvo}">{corpo}</h2>')
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
    uteis = [f for f in pesquisa.get("fontes", []) if f.get("status") == "util"]
    if not uteis:
        return "<p>Nenhuma fonte aproveitada.</p>"
    linhas = [
        f'<li><a href="{html.escape(f["url"], quote=True)}" target="_blank" rel="noreferrer">'
        f'{html.escape(f.get("titulo") or f["url"])}</a>'
        f'<span class="dominio">{html.escape(f.get("dominio", ""))}</span>'
        f'<p>{html.escape(f.get("resumo", ""))}</p></li>'
        for f in uteis if (f.get("url") or "").startswith("http")
    ]
    return f'<ol class="fontes">{"".join(linhas)}</ol>'


def _stats(pesquisa: dict) -> str:
    s = pesquisa.get("stats", {})
    segundos = int(s.get("segundos") or 0)
    itens = [("Tempo", f"{segundos // 60}m{segundos % 60:02d}s"),
             ("Rodadas", str(s.get("rodadas") or 0)),
             ("Páginas lidas", str(s.get("fontes") or 0)),
             ("Fontes úteis", str(s.get("uteis") or 0)),
             ("Extração", s.get("extrator") or "—"),
             ("Relatório", s.get("escritor") or "—")]
    return "".join(f"<div><span>{r}</span><strong>{html.escape(str(v))}</strong></div>" for r, v in itens)


def html_do(pesquisa: dict, markdown: str) -> str:
    """A página inteira, pronta para abrir no navegador (CSS embutido, sem rede)."""
    corpo, secoes = _md(markdown or pesquisa.get("resumo") or pesquisa.get("aviso") or "")
    sumario = "".join(f'<li><a href="#{i}">{html.escape(t)}</a></li>' for i, t in secoes)
    aviso = pesquisa.get("aviso") or ""
    return (TEMPLATE.read_text("utf-8")
            .replace("{{TITULO}}", html.escape(pesquisa.get("pergunta") or "Pesquisa"))
            .replace("{{DATA}}", datetime.now().strftime("%d/%m/%Y %H:%M"))
            .replace("{{AVISO}}", f'<div class="aviso">{html.escape(aviso)}</div>' if aviso else "")
            .replace("{{SUMARIO}}", f"<ol>{sumario}</ol>" if sumario else "")
            .replace("{{CORPO}}", corpo)
            .replace("{{FONTES}}", _fontes(pesquisa))
            .replace("{{STATS}}", _stats(pesquisa)))

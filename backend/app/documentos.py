"""Documentos de escritório: ler e gerar .pdf, .docx, .xlsx, .pptx e .csv.

O agente era cego e mudo para esses formatos. `read_file` parava em "Arquivo binário" — e .docx,
.xlsx e .pptx são ZIP, então batiam nessa parede já nos primeiros bytes — e não havia nenhuma forma
de produzir um deles: `write_file` escreve texto.

Duas metades:

- **Ler** (`extrair`): cada formato vira Markdown. É a representação que o modelo já entende sem ser
  ensinado, e que o `read_file` numera por linha como faria com qualquer arquivo de texto.
- **Gerar** (`escrever_documento`, `escrever_planilha`): Markdown entra, arquivo sai. O Markdown é
  quebrado em blocos uma vez só (`blocos()`), e cada formato tem um renderizador que consome essa
  lista — é o que permite testar o parser separado dos formatos.

ponytail: `relatorio.py` tem um parser de Markdown próprio, sem tabela, acoplado ao template da
pesquisa (sumário com âncora, imagem por seção). O `blocos()` daqui nasceu neutro e com tabela, e é
o candidato a substituir o de lá — mas migrar a pesquisa profunda não paga o risco agora.

ponytail: dos três pacotes novos, o `python-pptx` é dois terços do peso (arrasta Pillow e
XlsxWriter, ~8 dos ~12 MB). Se o PowerPoint um dia não estiver valendo, tirar a linha dele do
requirements devolve a maior parte.
"""
from __future__ import annotations

import asyncio
import csv
import io
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from . import config
from .tools import Tool, ToolError, register, resolve_path

NL = chr(10)
BARRA_PIPE = chr(92) + "|"  # pipe escapado, para o conteúdo não quebrar a tabela Markdown

# Extensões que este módulo entende. `read_file` consulta este dicionário antes de tentar ler texto.
LEITURA = {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".csv"}
DOCUMENTO = {".docx", ".pdf", ".pptx", ".md", ".html"}   # escrever_documento
PLANILHA = {".xlsx", ".csv"}                              # escrever_planilha

MAX_PAGINAS = 300      # PDF: teto de páginas extraídas
MAX_LINHAS_ABA = 2000  # planilha: teto de linhas por aba
MAX_COLUNAS = 60


def _tabela_md(linhas: list[list[str]]) -> str:
    """Lista de listas -> tabela Markdown. Primeira linha é o cabeçalho."""
    if not linhas:
        return ""
    largura = max(len(l) for l in linhas)
    corpo = [list(l) + [""] * (largura - len(l)) for l in linhas]
    cabecalho = corpo[0]
    limpa = lambda c: str(c).replace("|", BARRA_PIPE).replace(NL, "<br>")  # noqa: E731
    saida = ["| " + " | ".join(limpa(c) for c in cabecalho) + " |",
             "|" + "|".join([" --- "] * largura) + "|"]
    for linha in corpo[1:]:
        saida.append("| " + " | ".join(limpa(c) for c in linha) + " |")
    return NL.join(saida)


# ------------------------------------------------------------------ leitura

def _extrair_pdf(origem: Path) -> str | None:
    from pypdf import PdfReader

    leitor = PdfReader(str(origem))
    if leitor.is_encrypted:
        leitor.decrypt("")  # PDF só com senha de dono abre com senha vazia
    total = len(leitor.pages)
    paginas = [(p.extract_text() or "").strip() for p in leitor.pages[:MAX_PAGINAS]]
    corpo = (NL * 2).join(f"--- página {i} ---{NL}{t}" for i, t in enumerate(paginas, 1) if t)
    if not corpo.strip():
        return None  # escaneado: é imagem, não texto, e aqui não há OCR
    sobrou = " (as demais não foram extraídas)" if total > MAX_PAGINAS else ""
    return f"{total} página(s){sobrou}.{NL * 2}{corpo}"


def _cabecalhos_docx(doc):
    """Texto de cabeçalho e rodapé, que não estão no corpo e o python-docx não devolve junto.

    Sem isto o agente lê um formulário institucional e enxerga um documento quase vazio — foi o que
    aconteceu com um modelo de escola: nome da prefeitura, professor e turma estavam todos no
    cabeçalho, e a leitura devolvia só a tabela de uma célula do corpo.
    """
    vistos: list[str] = []
    fora: list[str] = []
    for secao in doc.sections:
        for rotulo, parte in (("cabeçalho", secao.header), ("rodapé", secao.footer)):
            linhas = [p.text.strip() for p in parte.paragraphs if p.text.strip()]
            linhas += [_tabela_md([[c.text.strip() for c in l.cells] for l in t.rows])
                       for t in parte.tables]
            texto = NL.join(linhas).strip()
            if texto and texto not in vistos:
                vistos.append(texto)
                fora.append(f"[{rotulo} do documento]{NL}{texto}")
    return fora


def _extrair_docx(origem: Path) -> str | None:
    import docx

    doc = docx.Document(str(origem))
    partes: list[str] = list(_cabecalhos_docx(doc))
    for bloco in _corpo_docx(doc):
        if bloco[0] == "p":
            texto = bloco[1].text.strip()
            if not texto:
                continue
            estilo = (bloco[1].style.name or "").lower()
            if estilo.startswith(("heading", "título", "titulo")):
                nivel = "".join(c for c in estilo if c.isdigit()) or "1"
                partes.append("#" * min(int(nivel), 6) + " " + texto)
            elif "list" in estilo or "lista" in estilo:
                partes.append("- " + texto)
            else:
                partes.append(texto)
        else:
            linhas = [[celula.text.strip() for celula in linha.cells] for linha in bloco[1].rows]
            partes.append(_tabela_md(linhas))
    return (NL * 2).join(partes) or None


def _corpo_docx(doc):
    """Parágrafos e tabelas na ordem em que aparecem no documento (o python-docx separa os dois)."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for filho in doc.element.body.iterchildren():
        if filho.tag.endswith("}p"):
            yield "p", Paragraph(filho, doc)
        elif filho.tag.endswith("}tbl"):
            yield "t", Table(filho, doc)


def _extrair_xlsx(origem: Path) -> str | None:
    """Cada aba vira uma seção com uma tabela Markdown.

    Lê duas vezes de propósito. `data_only=True` traz o valor que o Excel calculou e guardou — que
    é o que se quer ler —, mas uma planilha recém-escrita por programa ainda não tem esse cache, e
    a célula com fórmula sairia vazia. Então, quando não há valor, mostra a fórmula: o modelo
    precisa saber que ali existe um cálculo, e não um branco.
    """
    import openpyxl

    valores = openpyxl.load_workbook(str(origem), read_only=True, data_only=True)
    formulas = openpyxl.load_workbook(str(origem), read_only=True, data_only=False)
    try:
        partes: list[str] = []
        for aba in valores.worksheets:
            crua = formulas[aba.title]
            linhas: list[list[str]] = []
            pares = zip(aba.iter_rows(max_row=MAX_LINHAS_ABA, max_col=MAX_COLUNAS, values_only=True),
                        crua.iter_rows(max_row=MAX_LINHAS_ABA, max_col=MAX_COLUNAS, values_only=True))
            for linha, original in pares:
                celulas = [v if v is not None else f for v, f in zip(linha, original)]
                while celulas and celulas[-1] is None:
                    celulas.pop()  # sem isto a tabela sai com dezenas de colunas vazias
                if not celulas:
                    continue
                linhas.append(["" if c is None else str(c) for c in celulas])
            corte = f"{NL}(cortado em {MAX_LINHAS_ABA} linhas)" if aba.max_row and aba.max_row > MAX_LINHAS_ABA else ""
            tabela = _tabela_md(linhas) if linhas else "(aba vazia)"
            partes.append(f"## {aba.title}{NL}{NL}{tabela}{corte}")
        return (NL * 2).join(partes) or None
    finally:
        valores.close()
        formulas.close()


def _extrair_pptx(origem: Path) -> str | None:
    from pptx import Presentation

    prs = Presentation(str(origem))
    partes: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        linhas = [f"## Slide {i}"]
        for forma in slide.shapes:
            if forma.has_text_frame and forma.text_frame.text.strip():
                linhas.append(forma.text_frame.text.strip())
            elif getattr(forma, "has_table", False):
                linhas.append(_tabela_md([[c.text.strip() for c in l.cells] for l in forma.table.rows]))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            linhas.append("> Notas: " + slide.notes_slide.notes_text_frame.text.strip())
        partes.append(NL.join(linhas))
    return (NL * 2).join(partes) or None


def _extrair_csv(origem: Path) -> str | None:
    bruto = origem.read_text(encoding="utf-8", errors="replace")
    if not bruto.strip():
        return None
    try:  # vírgula e ponto-e-vírgula convivem: quem exporta do Excel em pt-BR usa o segundo
        dialeto = csv.Sniffer().sniff(bruto[:4096], delimiters=",;\t")
    except csv.Error:
        dialeto = csv.excel
    linhas = list(csv.reader(io.StringIO(bruto), dialeto))[:MAX_LINHAS_ABA]
    return _tabela_md([[str(c) for c in l] for l in linhas]) or None


EXTRATORES = {".pdf": _extrair_pdf, ".docx": _extrair_docx, ".xlsx": _extrair_xlsx,
              ".xlsm": _extrair_xlsx, ".pptx": _extrair_pptx, ".csv": _extrair_csv}


def extrair(origem: Path) -> str | None:
    """Conteúdo do documento como Markdown, ou None quando não há texto a extrair.

    Nunca levanta por causa do arquivo: documento corrompido, protegido por senha ou de uma versão
    que a biblioteca não conhece vira None, e quem chamou decide o que dizer ao usuário.
    """
    extrator = EXTRATORES.get(origem.suffix.lower())
    if not extrator:
        return None
    try:
        return extrator(origem)
    except Exception:  # as bibliotecas levantam de tudo em arquivo malformado
        return None


# ------------------------------------------------------------------ Markdown -> blocos

TITULO = re.compile(r"^(#{1,6})\s+(.*)$")
ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
IMAGEM = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
CERCA = re.compile(r"^```(\w*)\s*$")
QUEBRA = re.compile(r"^\s*(?:---|\*\*\*|___)\s*$")
LINHA_TABELA = re.compile(r"^\s*\|.*\|\s*$")
SEPARADOR_TABELA = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


# Markdown não tem quebra de linha dentro de célula, então quem escreve tabela usa <br> — foi o
# que o modelo fez, e os "<br><br>" foram parar no Word como texto. Aqui vira quebra de verdade.
QUEBRA_NA_CELULA = re.compile(r"<br\s*/?>", re.I)


def _celulas(linha: str) -> list[str]:
    bruto = linha.strip().strip("|")
    partes = re.split(r"(?<!" + re.escape(chr(92)) + r")\|", bruto)
    return [QUEBRA_NA_CELULA.sub(NL, c.strip()).replace(BARRA_PIPE, "|") for c in partes]


def blocos(markdown: str) -> list[dict]:
    """Markdown -> lista de blocos, na ordem.

    Tipos: `titulo` (nivel, texto), `paragrafo` (texto), `lista` (itens, ordenada),
    `tabela` (linhas), `codigo` (texto, lingua), `imagem` (caminho, alt), `quebra`.

    É o coração da geração: cada formato de saída consome esta lista e não sabe nada de Markdown.
    """
    saida: list[dict] = []
    linhas = markdown.replace(chr(13) + NL, NL).split(NL)
    i = 0
    paragrafo: list[str] = []

    def fecha_paragrafo() -> None:
        if paragrafo:
            saida.append({"tipo": "paragrafo", "texto": " ".join(paragrafo).strip()})
            paragrafo.clear()

    while i < len(linhas):
        linha = linhas[i]

        if cerca := CERCA.match(linha):
            fecha_paragrafo()
            lingua, corpo, i = cerca.group(1), [], i + 1
            while i < len(linhas) and not CERCA.match(linhas[i]):
                corpo.append(linhas[i])
                i += 1
            saida.append({"tipo": "codigo", "texto": NL.join(corpo), "lingua": lingua})
            i += 1
            continue

        if LINHA_TABELA.match(linha) and i + 1 < len(linhas) and SEPARADOR_TABELA.match(linhas[i + 1]):
            fecha_paragrafo()
            tabela = [_celulas(linha)]
            i += 2
            while i < len(linhas) and LINHA_TABELA.match(linhas[i]):
                tabela.append(_celulas(linhas[i]))
                i += 1
            saida.append({"tipo": "tabela", "linhas": tabela})
            continue

        if not linha.strip():
            fecha_paragrafo()
        elif QUEBRA.match(linha):
            fecha_paragrafo()
            saida.append({"tipo": "quebra"})
        elif img := IMAGEM.match(linha):
            fecha_paragrafo()
            saida.append({"tipo": "imagem", "alt": img.group(1), "caminho": img.group(2)})
        elif t := TITULO.match(linha):
            fecha_paragrafo()
            saida.append({"tipo": "titulo", "nivel": len(t.group(1)), "texto": t.group(2).strip()})
        elif item := ITEM.match(linha):
            ordenada = not linha.lstrip()[0] in "-*+"
            if saida and saida[-1]["tipo"] == "lista" and saida[-1]["ordenada"] == ordenada and not paragrafo:
                saida[-1]["itens"].append(item.group(1).strip())
            else:
                fecha_paragrafo()
                saida.append({"tipo": "lista", "itens": [item.group(1).strip()], "ordenada": ordenada})
        else:
            paragrafo.append(linha.strip())
        i += 1

    fecha_paragrafo()
    return saida


# ------------------------------------------------------------------ blocos -> arquivo

NEGRITO = re.compile(r"\*\*([^*]+)\*\*")
ITALICO = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
CODIGO_INLINE = re.compile(r"`([^`]+)`")


def _bordas_docx(tabela) -> None:
    """Grade preta fina, escrita direto no XML.

    O `Table Grid` é um estilo, e estilo só existe no documento que o declara. Um .docx feito no
    Word normalmente não traz os que nunca foram usados — daí o KeyError ao editar arquivo de fora.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    bordas = OxmlElement("w:tblBorders")
    for lado in ("top", "left", "bottom", "right", "insideH", "insideV"):
        linha = OxmlElement(f"w:{lado}")
        linha.set(qn("w:val"), "single")
        linha.set(qn("w:sz"), "4")
        linha.set(qn("w:color"), "000000")
        bordas.append(linha)
    tabela._tbl.tblPr.append(bordas)


def _tem_estilo(doc, nome: str) -> bool:
    """Perguntar antes, em vez de tentar e cair no except.

    `add_paragraph(texto, style=...)` cria o parágrafo e SÓ DEPOIS aplica o estilo. Quando o estilo
    não existe, o KeyError chega com o parágrafo já no documento — e o caminho alternativo escrevia
    um segundo, com o título aparecendo duas vezes. Achado pelo preview_document.
    """
    try:
        doc.styles[nome]
        return True
    except KeyError:
        return False


def _titulo_docx(doc, texto: str, nivel: int):
    """Título do jeito certo; e, se o documento não tiver o estilo, negrito e corpo maior."""
    from docx.shared import Pt

    nivel = min(nivel, 9)
    if _tem_estilo(doc, f"Heading {nivel}"):
        return doc.add_heading(texto, level=nivel)
    p = doc.add_paragraph()
    run = p.add_run(texto)
    run.bold = True
    run.font.size = Pt(max(18 - 2 * min(nivel, 5), 11))
    return p


def _item_docx(doc, ordenada: bool, indice: int):
    """Item de lista; sem o estilo, o marcador entra como texto — feio, mas legível."""
    estilo = "List Number" if ordenada else "List Bullet"
    if _tem_estilo(doc, estilo):
        return doc.add_paragraph(style=estilo)
    p = doc.add_paragraph()
    p.add_run(f"{indice}. " if ordenada else "• ")
    return p


def _tabela_docx(doc, linhas: list[list[str]]):
    """Tabela de Word a partir das linhas do parser. Quebra dentro da célula vira parágrafo.

    Um run com quebra crua dentro não mostra quebra nenhuma no Word: tem que ser parágrafo.
    """
    tabela = doc.add_table(rows=len(linhas), cols=max(len(l) for l in linhas))
    try:
        tabela.style = "Table Grid"
    except KeyError:
        _bordas_docx(tabela)  # o documento não tem esse estilo; desenha as linhas na mão
    for i, linha in enumerate(linhas):
        for j, celula in enumerate(linha):
            partes = _sem_marcas(celula).split(NL)
            alvo = tabela.cell(i, j)
            alvo.text = partes[0]
            for extra in partes[1:]:
                alvo.add_paragraph(extra)
    return tabela


def _sem_marcas(texto: str) -> str:
    """Tira as marcas de linha do Markdown. Para onde não dá para estilizar palavra a palavra."""
    texto = NEGRITO.sub(r"\1", texto)
    texto = ITALICO.sub(r"\1", texto)
    return CODIGO_INLINE.sub(r"\1", texto)


def para_html(bs: list[dict], titulo: str = "") -> str:
    """Blocos -> página HTML autocontida. É também o caminho do PDF."""
    import html as _html

    def inline(texto: str) -> str:
        s = _html.escape(texto)
        s = NEGRITO.sub(r"<strong>\1</strong>", s)
        s = ITALICO.sub(r"<em>\1</em>", s)
        return CODIGO_INLINE.sub(r"<code>\1</code>", s)

    corpo: list[str] = []
    for b in bs:
        if b["tipo"] == "titulo":
            corpo.append(f"<h{b['nivel']}>{inline(b['texto'])}</h{b['nivel']}>")
        elif b["tipo"] == "paragrafo":
            corpo.append(f"<p>{inline(b['texto'])}</p>")
        elif b["tipo"] == "lista":
            tag = "ol" if b["ordenada"] else "ul"
            itens = "".join(f"<li>{inline(i)}</li>" for i in b["itens"])
            corpo.append(f"<{tag}>{itens}</{tag}>")
        elif b["tipo"] == "codigo":
            corpo.append(f"<pre><code>{_html.escape(b['texto'])}</code></pre>")
        elif b["tipo"] == "quebra":
            corpo.append('<div class="quebra"></div>')
        elif b["tipo"] == "imagem":
            corpo.append(f'<img src="{_html.escape(b["caminho"], quote=True)}" alt="{_html.escape(b["alt"])}">')
        elif b["tipo"] == "tabela":
            linhas = b["linhas"]
            celula = lambda c: inline(c).replace(NL, "<br>")  # noqa: E731
            cab = "".join(f"<th>{celula(c)}</th>" for c in linhas[0])
            resto = "".join("<tr>" + "".join(f"<td>{celula(c)}</td>" for c in l) + "</tr>" for l in linhas[1:])
            corpo.append(f"<table><thead><tr>{cab}</tr></thead><tbody>{resto}</tbody></table>")
    return (f"<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
            f"<title>{_html.escape(titulo)}</title><style>{CSS}</style></head>"
            f"<body>{''.join(corpo)}</body></html>")


CSS = """
body { font-family: "Segoe UI", system-ui, sans-serif; font-size: 11pt; line-height: 1.55; color: #1a1a1a; }
h1, h2, h3, h4 { line-height: 1.25; margin: 1.4em 0 .5em; }
h1 { font-size: 20pt; } h2 { font-size: 15pt; } h3 { font-size: 12.5pt; }
p, li { orphans: 2; widows: 2; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 10pt; }
th, td { border: 1px solid #c8c8c8; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f2f2f2; font-weight: 600; }
tr { break-inside: avoid; }
pre { background: #f6f6f6; border: 1px solid #e2e2e2; border-radius: 4px; padding: 10px;
      font-size: 9.5pt; white-space: pre-wrap; break-inside: avoid; }
code { font-family: Consolas, "Cascadia Mono", monospace; }
img { max-width: 100%; }
.quebra { break-after: page; }
"""


def para_docx(bs: list[dict], destino: Path) -> None:
    import docx
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt

    doc = docx.Document()
    for b in bs:
        if b["tipo"] == "titulo":
            _titulo_docx(doc, _sem_marcas(b["texto"]), b["nivel"])
        elif b["tipo"] == "paragrafo":
            _runs_docx(doc.add_paragraph(), b["texto"])
        elif b["tipo"] == "lista":
            for n, item in enumerate(b["itens"], 1):
                _runs_docx(_item_docx(doc, b["ordenada"], n), item)
        elif b["tipo"] == "codigo":
            p = doc.add_paragraph()
            run = p.add_run(b["texto"])
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif b["tipo"] == "quebra":
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        elif b["tipo"] == "imagem":
            caminho = destino.parent / b["caminho"]
            if caminho.is_file():
                doc.add_picture(str(caminho))
        elif b["tipo"] == "tabela":
            linhas = b["linhas"]
            _tabela_docx(doc, linhas)
    doc.save(str(destino))


def _runs_docx(paragrafo, texto: str) -> None:
    """Escreve o texto no parágrafo quebrando em trechos para aplicar negrito e itálico."""
    for parte in re.split(r"(\*\*[^*]+\*\*|(?<!\*)\*[^*]+\*(?!\*)|`[^`]+`)", texto):
        if not parte:
            continue
        if parte.startswith("**") and parte.endswith("**"):
            paragrafo.add_run(parte[2:-2]).bold = True
        elif parte.startswith("`") and parte.endswith("`"):
            run = paragrafo.add_run(parte[1:-1])
            run.font.name = "Consolas"
        elif parte.startswith("*") and parte.endswith("*"):
            paragrafo.add_run(parte[1:-1]).italic = True
        else:
            paragrafo.add_run(parte)


def para_pptx(bs: list[dict], destino: Path) -> None:
    """Cada `#`/`##` ou `---` abre um slide; o resto entra como corpo do slide aberto."""
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    slide = None
    corpo = None

    def novo_slide(titulo: str):
        nonlocal slide, corpo
        slide = prs.slides.add_slide(prs.slide_layouts[1])  # título + conteúdo
        slide.shapes.title.text = _sem_marcas(titulo)
        corpo = slide.placeholders[1].text_frame
        corpo.clear()
        corpo.paragraphs[0].text = ""
        return corpo

    def linha(texto: str, nivel: int = 0) -> None:
        nonlocal corpo
        if corpo is None:
            novo_slide("")
        p = corpo.paragraphs[0] if (len(corpo.paragraphs) == 1 and not corpo.paragraphs[0].text) else corpo.add_paragraph()
        p.text = _sem_marcas(texto)
        p.level = min(nivel, 4)
        p.font.size = Pt(18)

    for b in bs:
        if b["tipo"] == "titulo":
            novo_slide(b["texto"])
        elif b["tipo"] == "quebra":
            corpo = None
        elif b["tipo"] == "paragrafo":
            linha(b["texto"])
        elif b["tipo"] == "lista":
            for item in b["itens"]:
                linha(item, 1)
        elif b["tipo"] == "codigo":
            for l in b["texto"].split(NL):
                linha(l, 1)
        elif b["tipo"] == "tabela":
            for l in b["linhas"]:
                linha(" · ".join(l), 1)
    if not prs.slides:
        novo_slide("")
    prs.save(str(destino))


@asynccontextmanager
async def _pagina_chromium(html: str, base: Path, para_que: str):
    """Uma página headless com esse HTML carregado, e o Chromium fechado no fim.

    Instância própria de propósito: no Desktop o `browser.MANAGER` está ligado por CDP ao Electron,
    que é headed, e `page.pdf()` só existe em Chromium headless. A página é montada aqui e entra por
    `set_content`, então nunca vai à rede.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        try:
            navegador = await pw.chromium.launch(headless=True)
        except Exception as e:
            raise ToolError(
                f"Não consegui abrir o Chromium para {para_que}. Ele vem com o Forja instalado; em "
                "desenvolvimento, rode `python -m playwright install --only-shell chromium`. "
                f"({e.__class__.__name__}) Gerar .docx ou .html não depende dele.") from e
        try:
            pagina = await navegador.new_page()
            # `file://` da pasta: é o que faz ![](imagem.png) relativo funcionar.
            await pagina.goto(base.resolve().as_uri() + "/")
            await pagina.set_content(html, wait_until="load")
            yield pagina
        finally:
            await navegador.close()


async def para_pdf(bs: list[dict], destino: Path, titulo: str = "") -> None:
    """HTML -> PDF pelo Chromium que o app já empacota. Nenhuma biblioteca de PDF no meio.

    Sobe uma instância headless própria de propósito: no Desktop o `browser.MANAGER` está ligado por
    CDP ao Electron, que é headed, e `page.pdf()` só existe em Chromium headless. Como a página é
    montada aqui e carregada por `set_content`, ela nunca vai à rede.
    """
    html = para_html(bs, titulo)
    async with _pagina_chromium(html, destino.parent, "gerar o PDF") as pagina:
        dados = await pagina.pdf(format="A4", print_background=True,
                                     margin={"top": "2cm", "bottom": "2cm", "left": "2cm", "right": "2cm"},
                                     display_header_footer=True, header_template="<div></div>",
                                     footer_template='<div style="width:100%;font-size:8pt;color:#777;'
                                                     'text-align:center"><span class="pageNumber"></span>'
                                                     '/<span class="totalPages"></span></div>')
    destino.write_bytes(dados)


# ------------------------------------------------------------------ planilhas

def _valor(bruto) -> object:
    """Texto da célula -> o que vai para a planilha. `=` vira fórmula; número vira número."""
    if not isinstance(bruto, str):
        return bruto
    texto = bruto.strip()
    if texto.startswith("="):
        return texto  # o openpyxl grava como fórmula
    try:
        return int(texto)
    except ValueError:
        pass
    try:  # 1.234,56 (pt-BR) e 1234.56 (en) — o ponto só some quando há vírgula decimal
        return float(texto.replace(".", "").replace(",", ".") if "," in texto else texto)
    except ValueError:
        return bruto


def para_xlsx(abas: list[dict], destino: Path) -> None:
    import openpyxl
    from openpyxl.styles import Font

    livro = openpyxl.Workbook()
    livro.remove(livro.active)
    for aba in abas:
        folha = livro.create_sheet(str(aba.get("nome") or "Planilha")[:31])
        linhas = aba.get("linhas") or []
        for i, linha in enumerate(linhas, 1):
            for j, celula in enumerate(linha, 1):
                folha.cell(row=i, column=j, value=_valor(celula))
        if linhas and aba.get("cabecalho", True):
            for celula in folha[1]:
                celula.font = Font(bold=True)
            folha.freeze_panes = "A2"
        _larguras(folha, linhas)
    if not livro.worksheets:
        livro.create_sheet("Planilha")
    livro.save(str(destino))


def _larguras(folha, linhas: list[list]) -> None:
    """Coluna larga o bastante para ler sem arrastar a borda. Teto para não virar uma faixa."""
    from openpyxl.utils import get_column_letter

    for j in range(max((len(l) for l in linhas), default=0)):
        maior = max((len(str(l[j])) for l in linhas if j < len(l)), default=0)
        folha.column_dimensions[get_column_letter(j + 1)].width = min(max(maior + 2, 10), 60)


def para_csv(abas: list[dict], destino: Path) -> None:
    """CSV é uma aba só. Ponto-e-vírgula: é o que o Excel em pt-BR abre sem perguntar nada."""
    linhas = abas[0].get("linhas") or [] if abas else []
    with destino.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh, delimiter=";").writerows(linhas)


# ------------------------------------------------------------------ ferramentas

PASTA = "documentos"  # onde cai um caminho sem pasta; caminho com pasta é respeitado como veio
MIMES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf", ".csv": "text/csv", ".md": "text/markdown", ".html": "text/html",
}


def _destino(root: Path, caminho: str, aceitas: set[str], sobrescrever: bool = False) -> Path:
    """Resolve o caminho de saída, confinado à pasta da conversa, e valida a extensão.

    Com `sobrescrever` falso, recusa por cima de arquivo que já existe. Aconteceu duas vezes em
    uso: pedido para acrescentar algo a um documento anexado, o modelo chamou write_document no
    caminho dele — que gera do zero, e teria levado o documento do usuário junto. Errar aqui não
    tem desfazer depois do turno, então a porta fica fechada e a mensagem diz por onde é a saída."""
    bruto = (caminho or "").strip().replace(chr(92), "/")
    if not bruto:
        raise ToolError("Informe o caminho do arquivo, com a extensão.")
    ext = Path(bruto).suffix.lower()
    if ext not in aceitas:
        raise ToolError(f"Extensão '{ext or '(nenhuma)'}' não serve aqui. Use: {', '.join(sorted(aceitas))}.")
    if "/" not in bruto:
        bruto = f"{PASTA}/{bruto}"
    destino = resolve_path(root, bruto)
    # A pasta de anexos é do usuário: é o arquivo que ELE mandou. Nem com sobrescrever — o modelo
    # levou o não duas vezes, leu o "sobrescrever: true" no esquema e passou por cima assim mesmo.
    from . import uploads  # tardio: uploads importa documentos, e o contrário fecharia o ciclo

    if destino.relative_to(root.resolve()).as_posix().startswith(uploads.UPLOAD_DIR + "/"):
        raise ToolError(
            f"'{bruto}' está na pasta de anexos, que guarda o arquivo original do usuário: gerar "
            "por cima dele o destruiria. Para alterar esse arquivo preservando o conteúdo, use "
            "edit_document (ou edit_spreadsheet) nesse mesmo caminho. Para um arquivo novo a "
            "partir dele, escreva em documentos/.")
    if destino.exists() and not sobrescrever:
        raise ToolError(
            f"'{bruto}' já existe, e esta ferramenta gera o arquivo do zero: o conteúdo atual se "
            "perderia. Para ACRESCENTAR ou alterar preservando o resto, use edit_document (ou "
            "edit_spreadsheet, se for planilha) neste mesmo caminho. Para um arquivo à parte, "
            "escolha outro nome. Para refazer este do zero mesmo assim, mande sobrescrever: true.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    return destino


def _resultado(root: Path, destino: Path, o_que: str) -> dict:
    """Texto para o modelo + anexo para o chat."""
    anexo = _anexo(root, destino)
    return {"text": f"{o_que} em {anexo['path']} ({anexo['size'] // 1024} KB).", "attachments": [anexo]}


async def write_document(root: Path, args: dict) -> dict:
    destino = _destino(root, str(args.get("path") or ""), DOCUMENTO, bool(args.get("sobrescrever")))
    conteudo = str(args.get("content") or "")
    if not conteudo.strip():
        raise ToolError("content vazio: mande o conteúdo do documento em Markdown.")
    bs = blocos(conteudo)
    titulo = next((b["texto"] for b in bs if b["tipo"] == "titulo"), destino.stem)
    ext = destino.suffix.lower()
    if ext == ".pdf":
        await para_pdf(bs, destino, titulo)
    elif ext == ".docx":
        await asyncio.to_thread(para_docx, bs, destino)
    elif ext == ".pptx":
        await asyncio.to_thread(para_pptx, bs, destino)
    elif ext == ".html":
        destino.write_text(para_html(bs, titulo), encoding="utf-8")
    else:  # .md
        destino.write_text(conteudo, encoding="utf-8")
    return _resultado(root, destino, "Documento gerado")


MAX_PREVIA_CHARS = 20_000  # o suficiente para umas 15 páginas; acima disso a imagem só engorda
LARGURA_PREVIA = 820          # ~A4 a 96dpi, que é a largura do CSS do para_html
LARGURA_NAVEGADOR = 1280      # .html é página: renderiza em largura de desktop, não de folha A4
ALTURA_PREVIA = 1160
ALTURA_MAX_PREVIA = 3000      # teto da imagem; sem ele uma landing page virava uma tira de 15000px
PAGINAS_PREVIA = 3            # quantas páginas viram imagem quando dá para paginar de verdade
MAX_PAGINAS_OCR = 30          # OCR é ~1s por página: acima disso a leitura deixa de ser interativa
ESCALA_OCR = 2.6              # ~200 DPI; abaixo disso o reconhecimento cai bastante
ESCALA_PREVIA = 1.4           # legível sem virar um JPEG gigante
VIRTUAL_PDF = "Microsoft Print to PDF"  # impressora que não existe fisicamente; ver _office_para_pdf


def _pdf_para_imagens(pdf: Path, paginas: int = PAGINAS_PREVIA, inicio: int = 1,
                      escala: float = ESCALA_PREVIA) -> tuple[list[bytes], int]:
    """Páginas do PDF como JPEG, e quantas o arquivo tem ao todo, pelo PDFium — o mesmo motor do
    visualizador do Chrome.

    O Chromium que empacotamos é o `headless-shell`, que não traz o visualizador de PDF: apontar o
    navegador para o arquivo só dispara um download. Daí a biblioteca à parte.

    `inicio` (1-based) existe porque num PDF escaneado a prévia é a ÚNICA leitura possível: com uma
    janela fixa nas três primeiras páginas o modelo transcrevia só o começo e achava que tinha
    terminado. Agora ele avança a janela até o fim.
    """
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(pdf))
    except Exception as e:
        raise ToolError(f"Não consegui abrir '{pdf.name}' para montar a prévia: o arquivo está "
                        f"corrompido ou protegido por senha. ({type(e).__name__})") from e
    try:
        total = len(doc)
        if inicio > total:
            raise ToolError(f"'{pdf.name}' tem {total} página(s); não existe a página {inicio}.")
        saida = []
        for i in range(inicio - 1, min(total, inicio - 1 + paginas)):
            buf = io.BytesIO()
            doc[i].render(scale=escala).to_pil().convert("RGB").save(buf, "JPEG", quality=75)
            saida.append(buf.getvalue())
        return saida, total
    finally:
        doc.close()


def extrair_ocr(pdf: Path) -> str | None:
    """Texto de um PDF escaneado, pelo OCR do sistema. None quando não há OCR aqui ou não saiu nada.

    Não entra no `extrair()`: aquele é a leitura barata, chamada inclusive no upload só para saber
    se há texto. Esta custa segundos por página e só vale a pena quando a barata já voltou vazia.
    """
    from . import ocr

    if not ocr.disponivel():
        return None
    imagens, total = _pdf_para_imagens(pdf, MAX_PAGINAS_OCR, escala=ESCALA_OCR)
    textos = ocr.de_imagens(imagens)
    corpo = (NL * 2).join(f"--- página {i} ---{NL}{t}" for i, t in enumerate(textos, 1) if t)
    if not corpo.strip():
        return None
    sobrou = f" (o OCR passou nas {MAX_PAGINAS_OCR} primeiras)" if total > MAX_PAGINAS_OCR else ""
    # O aviso vai no texto porque quem lê é o modelo: OCR erra caractere e embaralha coluna, e ele
    # precisa saber que está lendo um palpite de máquina, não o arquivo.
    return (f"{total} página(s){sobrou}. Texto obtido por OCR, não extraído do arquivo: é leitura de "
            f"imagem, então caractere trocado e coluna fora de ordem acontecem. Confira número e "
            f"código antes de afirmar qualquer coisa, e diga ao usuário que veio de OCR.{NL * 2}{corpo}")


def _office_para_pdf(origem: Path, destino: Path) -> bool:
    """Pede ao Word que exporte o .docx em PDF. Só no Windows, e só se o Word estiver instalado.

    É a única forma de ver o documento como o usuário vai vê-lo: fonte, margem, paginação e os
    estilos do arquivo. Sem Word a prévia continua saindo do nosso HTML, que mostra conteúdo e
    estrutura mas não a diagramação.

    ponytail: só .docx. .pptx e .xlsx caem no HTML; se algum dia incomodar, o caminho é o mesmo
    (PowerPoint.Application / Excel.Application, com o mesmo ExportAsFixedFormat).
    """
    try:
        import pythoncom
        import win32com.client
        import win32print
    except ImportError:
        return False

    # O Word repagina consultando a impressora ativa, e com uma impressora de rede isso custa caro:
    # exportar DUAS páginas levava 48 segundos aqui. Apontando para a virtual do Windows, 0,8s.
    #
    # `ActivePrinter` do Word escreve no padrão do SISTEMA, então tem que ser devolvido. Devolver
    # pelo Word custaria os mesmos 48s (ele vai à impressora de novo); pelo win32print é instantâneo.
    padrao = None
    try:
        if VIRTUAL_PDF in {p[2] for p in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)}:
            padrao = win32print.GetDefaultPrinter()
    except Exception:
        padrao = None

    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.DispatchEx("Word.Application")
        app.Visible = False
        app.DisplayAlerts = 0
        if padrao:
            app.ActivePrinter = VIRTUAL_PDF
        doc = app.Documents.Open(str(origem.resolve()), ReadOnly=True, AddToRecentFiles=False)
        try:
            doc.ExportAsFixedFormat(OutputFileName=str(destino), ExportFormat=17,  # wdExportFormatPDF
                                    OptimizeFor=1, DocStructureTags=False, BitmapMissingFonts=False,
                                    IncludeDocProps=False, CreateBookmarks=0)
        finally:
            doc.Close(False)
        return destino.is_file()
    except Exception:
        return False  # sem Word, licença expirada, arquivo travado: a prévia cai no HTML
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
        if padrao:
            try:
                win32print.SetDefaultPrinter(padrao)
            except Exception:
                pass


async def preview_document(root: Path, args: dict) -> dict:
    """Uma imagem do arquivo, para quem tem olhos — o modelo, se tiver visão, e sempre o usuário.

    A prévia sai do arquivo SALVO: extrai o conteúdo dele e monta a página com o mesmo HTML do PDF.
    É isso que faz dela uma verificação de verdade — se o renderizador escreveu `|` como texto em
    vez de montar a tabela, é `|` como texto que aparece aqui.

    O que ela NÃO é: uma foto do que o Word mostra. Não empacotamos Word nem LibreOffice, então
    paginação e estilo do Office ficam de fora. Para o PDF, que sai deste mesmo HTML, ela é fiel.
    """
    from . import uploads  # tardio: uploads importa documentos, e o contrário fecharia o ciclo

    alvo = _existente(root, str(args.get("path") or ""), LEITURA | {".md", ".html"})
    ext = alvo.suffix.lower()

    # Caminho fiel: o PDF já é o que o usuário vê; o .docx vira PDF pelo próprio Word, quando ele
    # existe na máquina. O PDF intermediário mora numa pasta temporária e some ao fim da chamada —
    # ele serve só para virar imagem.
    if ext == ".pdf":
        inicio = max(int(args.get("pagina") or 1), 1)
        imagens, total = await asyncio.to_thread(_pdf_para_imagens, alvo, PAGINAS_PREVIA, inicio)
        fim = inicio + len(imagens) - 1
        # Dizer o que ficou de fora, senão o modelo transcreve três páginas e dá o trabalho por feito.
        resto = ("" if fim >= total else
                 f". São {total} páginas; chame de novo com pagina={fim + 1} para as demais")
        return _resposta_previa(root, alvo, imagens, f"páginas {inicio}-{fim}, como ele será aberto{resto}")
    if ext == ".docx":
        with tempfile.TemporaryDirectory(prefix="forja-previa-") as tmp:
            pdf = Path(tmp) / "previa.pdf"
            if await asyncio.to_thread(_office_para_pdf, alvo, pdf):
                imagens, _ = await asyncio.to_thread(_pdf_para_imagens, pdf)
                return _resposta_previa(root, alvo, imagens, "renderizado pelo Word desta máquina")

    if ext in (".md", ".html"):
        texto = alvo.read_text(encoding="utf-8", errors="replace")
    else:
        texto = await asyncio.to_thread(extrair, alvo)
    if not texto or not texto.strip():
        raise ToolError(f"Não consegui extrair conteúdo de '{alvo.name}' para montar a prévia. "
                        "PDF escaneado e arquivo protegido caem aqui.")
    pagina_web = ext == ".html"
    # .html é página, não documento: renderiza o arquivo como ele é, numa janela de navegador.
    # Nos outros, o que vale é o texto extraído, e aí o corte em MAX_PREVIA_CHARS é real.
    cortado = not pagina_web and len(texto) > MAX_PREVIA_CHARS
    html = (alvo.read_text(encoding="utf-8", errors="replace") if pagina_web
            else para_html(blocos(texto[:MAX_PREVIA_CHARS]), alvo.stem))
    largura = LARGURA_NAVEGADOR if pagina_web else LARGURA_PREVIA

    async with _pagina_chromium(html, alvo.parent, "montar a prévia") as pagina:
        await pagina.set_viewport_size({"width": largura, "height": ALTURA_PREVIA})
        # `full_page` sem teto era o problema: uma landing page vira uma tira de 13 mil pixels de
        # altura, ilegível como imagem e cara como visão — um modelo local engasgou nela por minutos.
        # Acima do teto, o recorte sai esticando a JANELA até o teto, e não pelo `clip`: o clip é
        # limitado pela viewport, então ele devolvia 1160px enquanto a resposta prometia 3000.
        altura = await pagina.evaluate("document.documentElement.scrollHeight")
        alta = altura > ALTURA_MAX_PREVIA
        if alta:
            await pagina.set_viewport_size({"width": largura, "height": ALTURA_MAX_PREVIA})
        dados = await pagina.screenshot(type="jpeg", quality=75, scale="css", full_page=not alta)

    if pagina_web:
        o_que = "a página renderizada num navegador de " + str(largura) + "px"
        if alta:
            o_que += (f", só os primeiros {ALTURA_MAX_PREVIA}px de {altura}px — para ver o resto, "
                      "ou conferir rolagem, responsividade e erros de console, abra no navegador integrado")
    else:
        o_que = "como o Forja lê o arquivo salvo, sem a diagramação do Word"
        if cortado:
            o_que += f". Só o começo: o arquivo passa de {MAX_PREVIA_CHARS} caracteres"
        elif alta:
            o_que += f". Só os primeiros {ALTURA_MAX_PREVIA}px de {altura}px"
    return _resposta_previa(root, alvo, [dados], o_que)


def _resposta_previa(root: Path, alvo: Path, imagens: list[bytes], o_que_mostra: str) -> dict:
    from . import uploads

    if not imagens:
        raise ToolError(f"'{alvo.name}' não rendeu nenhuma página para a prévia.")
    anexos = [uploads.save(f"previa-{alvo.stem}-{i}.jpg" if len(imagens) > 1 else f"previa-{alvo.stem}.jpg",
                           img, "image/jpeg", root)
              for i, img in enumerate(imagens, 1)]
    pags = f"{len(anexos)} página(s)" if len(anexos) > 1 else "1 página"
    return {"text": f"Prévia de {alvo.name} anexada ({pags}): {o_que_mostra}.", "attachments": anexos}


def normalizar_abas(bruto) -> list[dict]:
    """O que o modelo mandou -> [{"nome", "linhas"}]. Levanta ToolError se não der para entender.

    Aceita mais de uma forma porque o modelo escreve mais de uma: chave em português ou em inglês,
    e a aba como lista de linhas direto, sem o envelope com nome. Recusar isso seria fazer o modelo
    adivinhar o dialeto certo — e foi exatamente aí que a primeira versão quebrou, com um
    AttributeError feio em vez de uma planilha.
    """
    if isinstance(bruto, dict):  # uma aba só, sem lista em volta
        bruto = [bruto]
    if not isinstance(bruto, list) or not bruto:
        raise ToolError('sheets vazio. Ex.: [{"nome": "Dados", "linhas": [["A", "B"], [1, 2]]}]')
    abas: list[dict] = []
    for i, aba in enumerate(bruto, 1):
        if isinstance(aba, list):  # a aba veio como as linhas direto
            linhas, nome = aba, f"Planilha{i}" if len(bruto) > 1 else "Planilha"
        elif isinstance(aba, dict):
            linhas = aba.get("linhas") or aba.get("rows") or aba.get("data") or []
            nome = str(aba.get("nome") or aba.get("name") or aba.get("title") or f"Planilha{i}")
        else:
            raise ToolError(f'Aba {i} não é objeto nem lista de linhas. Ex.: '
                            '{"nome": "Dados", "linhas": [["A", "B"], [1, 2]]}')
        if not isinstance(linhas, list):
            raise ToolError(f"As linhas da aba '{nome}' precisam ser uma lista de listas.")
        # Linha solta (não-lista) vira linha de uma célula: é erro comum e não vale recusar por isso.
        abas.append({"nome": nome, "linhas": [l if isinstance(l, list) else [l] for l in linhas],
                     "cabecalho": aba.get("cabecalho", True) if isinstance(aba, dict) else True})
    return abas


def write_spreadsheet(root: Path, args: dict) -> dict:
    destino = _destino(root, str(args.get("path") or ""), PLANILHA, bool(args.get("sobrescrever")))
    abas = normalizar_abas(args.get("sheets"))
    if destino.suffix.lower() == ".csv":
        para_csv(abas, destino)
        if len(abas) > 1:
            return _resultado(root, destino, f"CSV gerado com a primeira das {len(abas)} abas (CSV não tem abas);")
    else:
        para_xlsx(abas, destino)
    return _resultado(root, destino, "Planilha gerada")


# ------------------------------------------------------------------ editar o que já existe

class _Ocupado(ToolError):
    """Arquivo aberto no Word/Excel. Mensagem à parte porque o que resolve é do usuário, não do modelo."""


def _aberto_em_outro_programa(alvo: Path) -> _Ocupado:
    return _Ocupado(f"'{alvo.name}' está aberto em outro programa (Word, Excel, o visualizador do "
                    "Windows). Peça ao usuário para fechar o arquivo e tente de novo — não adianta "
                    "gerar uma cópia com outro nome, ele quer este arquivo.")


def _existente(root: Path, caminho: str, aceitas: set[str]) -> Path:
    alvo = resolve_path(root, (caminho or "").strip())
    if not alvo.is_file():
        raise ToolError(f"Arquivo não encontrado: '{caminho}'. Use list_dir para ver o que existe.")
    if alvo.suffix.lower() not in aceitas:
        raise ToolError(f"Não sei editar '{alvo.suffix}'. Aqui dá para: {', '.join(sorted(aceitas))}.")
    return alvo


def _anexo(root: Path, alvo: Path) -> dict:
    rel = alvo.relative_to(root.resolve()).as_posix()
    return {"path": rel, "name": alvo.name, "size": alvo.stat().st_size,
            "mime": MIMES.get(alvo.suffix.lower(), "application/octet-stream"), "kind": "file"}


def edit_spreadsheet(root: Path, args: dict) -> dict:
    """Mexe numa planilha que já existe, preservando o resto: fórmulas, formatação e outras abas."""
    import openpyxl

    alvo = _existente(root, str(args.get("path") or ""), {".xlsx", ".xlsm"})
    mudancas = args.get("changes") or []
    if not isinstance(mudancas, list) or not mudancas:
        raise ToolError('changes vazio. Ex.: [{"tipo": "celula", "aba": "Dados", "celula": "B2", "valor": 10}]')
    livro = openpyxl.load_workbook(str(alvo))
    feitas: list[str] = []
    try:
        for m in mudancas:
            if not isinstance(m, dict):
                raise ToolError("Cada mudança é um objeto com 'tipo'.")
            tipo = str(m.get("tipo") or m.get("type") or "").lower()
            nome = str(m.get("aba") or m.get("sheet") or "").strip()
            linhas = m.get("linhas") or m.get("rows") or []
            if tipo in ("aba", "sheet", "nova_aba"):
                folha = livro.create_sheet(nome[:31] or "Planilha")
                for linha in linhas:
                    folha.append([_valor(c) for c in linha])
                feitas.append(f"aba '{folha.title}' criada")
                continue
            if nome and nome not in livro.sheetnames:
                raise ToolError(f"A aba '{nome}' não existe. Abas: {', '.join(livro.sheetnames)}.")
            folha = livro[nome] if nome else livro.worksheets[0]
            if tipo in ("celula", "cell"):
                ref = str(m.get("celula") or m.get("cell") or "").strip().upper()
                if not ref:
                    raise ToolError("Falta 'celula' (ex.: B2).")
                folha[ref] = _valor(m.get("valor", m.get("value")))
                feitas.append(f"{folha.title}!{ref}")
            elif tipo in ("linhas", "rows", "append"):
                for linha in linhas:
                    folha.append([_valor(c) for c in linha])
                feitas.append(f"{len(linhas)} linha(s) em '{folha.title}'")
            elif tipo in ("renomear", "rename"):
                antigo = folha.title
                folha.title = str(m.get("novo") or m.get("new") or "")[:31]
                feitas.append(f"'{antigo}' virou '{folha.title}'")
            else:
                raise ToolError(f"Tipo de mudança desconhecido: '{tipo}'. Use celula, linhas, aba ou renomear.")
        livro.save(str(alvo))
    finally:
        livro.close()
    return {"text": f"Planilha {_anexo(root, alvo)['path']} atualizada: {'; '.join(feitas)}.",
            "attachments": [_anexo(root, alvo)]}


async def edit_document(root: Path, args: dict) -> dict:
    alvo = _existente(root, str(args.get("path") or ""), {".docx", ".pptx", ".pdf"})
    ops = args.get("operations") or []
    if not isinstance(ops, list) or not ops:
        raise ToolError('operations vazio. Ex.: [{"tipo": "acrescentar", "conteudo": "## Nova seção"}]')
    ext = alvo.suffix.lower()
    try:
        if ext == ".pdf":
            feitas = await asyncio.to_thread(_editar_pdf, alvo, ops)
        else:
            feitas = await asyncio.to_thread(_editar_office, alvo, ops, ext)
    except PermissionError:
        raise _aberto_em_outro_programa(alvo) from None
    return {"text": f"{alvo.name} atualizado: {'; '.join(feitas)}.", "attachments": [_anexo(root, alvo)]}


def _editar_office(alvo: Path, ops: list, ext: str) -> list[str]:
    feitas: list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            raise ToolError("Cada operação é um objeto com 'tipo'.")
        tipo = str(op.get("tipo") or op.get("type") or "").lower()
        if tipo in ("acrescentar", "append"):
            bs = blocos(str(op.get("conteudo") or op.get("content") or ""))
            if not bs:
                raise ToolError("Falta 'conteudo': o Markdown a acrescentar.")
            if ext == ".docx":
                _acrescentar_docx(alvo, bs)
            else:
                _acrescentar_pptx(alvo, bs)
            feitas.append(f"{len(bs)} bloco(s) acrescentado(s)")
        elif tipo in ("inserir", "insert"):
            bs = blocos(str(op.get("conteudo") or op.get("content") or ""))
            if not bs:
                raise ToolError("Falta 'conteudo': o Markdown a inserir.")
            depois = str(op.get("depois") or op.get("after") or "")
            antes = str(op.get("antes") or op.get("before") or "")
            if not depois and not antes:
                raise ToolError("Falta 'depois' (ou 'antes'): um trecho do documento que diga onde "
                                "entra. Sem âncora, use {'tipo': 'acrescentar'}.")
            if ext != ".docx":
                raise ToolError("'inserir' só vale para .docx. Num .pptx use "
                                "{'tipo': 'acrescentar'}, que cria slides no fim.")
            _inserir_docx(alvo, bs, antes or depois, bool(antes))
            feitas.append(f"{len(bs)} bloco(s) inserido(s) {'antes de' if antes else 'depois de'} "
                          f"'{(antes or depois)[:30]}'")
        elif tipo in ("substituir", "replace"):
            de = str(op.get("de") or op.get("old") or "")
            para = str(op.get("para") or op.get("new") or "")
            if not de:
                raise ToolError("Falta 'de': o texto a ser substituído.")
            if any(b["tipo"] in ("tabela", "titulo", "lista") for b in blocos(para)):
                raise ToolError(
                    "'substituir' troca texto por texto, literalmente: uma tabela em Markdown ia "
                    "parar no documento com os `|` à mostra. Para conteúdo formatado use "
                    "{'tipo': 'acrescentar', 'conteudo': '<markdown>'}, que monta tabela e título "
                    "de verdade no fim, ou {'tipo': 'inserir', 'depois': '<trecho do documento>', "
                    "'conteudo': '<markdown>'} para montar no meio, no lugar certo.")
            n = _substituir(alvo, de, para, ext)
            if not n:
                raise ToolError(f"Não achei '{de[:40]}' no documento. Leia com read_file antes, e "
                                "lembre que só casa texto contínuo, com a mesma formatação.")
            feitas.append(f"{n} ocorrência(s) trocada(s)")
        else:
            raise ToolError(f"Tipo de operação desconhecido: '{tipo}'. Use acrescentar, inserir ou substituir.")
    return feitas


def _acrescentar_docx(alvo: Path, bs: list[dict]) -> None:
    import docx

    doc = docx.Document(str(alvo))
    _montar_docx(doc, bs)
    doc.save(str(alvo))


def _montar_docx(doc, bs: list[dict]) -> None:
    """Escreve os blocos no fim do corpo. Quem quer no meio move os elementos depois."""
    for b in bs:
        if b["tipo"] == "titulo":
            _titulo_docx(doc, _sem_marcas(b["texto"]), b["nivel"])
        elif b["tipo"] == "lista":
            for n, item in enumerate(b["itens"], 1):
                _runs_docx(_item_docx(doc, b["ordenada"], n), item)
        elif b["tipo"] == "tabela":
            linhas = b["linhas"]
            _tabela_docx(doc, linhas)
        elif b["tipo"] in ("paragrafo", "codigo"):
            _runs_docx(doc.add_paragraph(), b.get("texto", ""))


def _inserir_docx(alvo: Path, bs: list[dict], ancora: str, antes: bool) -> None:
    """Põe os blocos ao lado do parágrafo que contém `ancora`, em vez de no fim do documento.

    O python-docx só sabe escrever no fim, então monta-se lá e move-se o XML para o lugar. É o que
    permite atender "adicione um calendário depois da seção tal" sem reescrever o arquivo inteiro.
    """
    import docx

    doc = docx.Document(str(alvo))
    alvo_par = next((p for p in doc.paragraphs if ancora.lower() in p.text.lower()), None)
    if alvo_par is None:
        raise ToolError(f"Não achei '{ancora[:40]}' no documento. Leia com read_file antes e use um "
                        "trecho que exista, ou use {'tipo': 'acrescentar'} para pôr no fim.")
    # ponytail: comparação por identidade numa lista curta; é o corpo de um .docx, não um índice.
    antigos = list(doc.element.body)
    _montar_docx(doc, bs)
    novos = [el for el in doc.element.body if el not in antigos]
    ref = alvo_par._p
    for el in novos if antes else reversed(novos):
        (ref.addprevious if antes else ref.addnext)(el)
    doc.save(str(alvo))


def _acrescentar_pptx(alvo: Path, bs: list[dict]) -> None:
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation(str(alvo))
    corpo = None
    for b in bs:
        if b["tipo"] == "titulo" or corpo is None:
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = _sem_marcas(b.get("texto", ""))
            corpo = slide.placeholders[1].text_frame
            corpo.clear()
            corpo.paragraphs[0].text = ""
            if b["tipo"] == "titulo":
                continue
        for texto in (b["itens"] if b["tipo"] == "lista" else [b.get("texto", "")]):
            p = corpo.paragraphs[0] if not corpo.paragraphs[0].text else corpo.add_paragraph()
            p.text = _sem_marcas(texto)
            p.font.size = Pt(18)
    prs.save(str(alvo))


def _substituir(alvo: Path, de: str, para: str, ext: str) -> int:
    """Troca texto preservando a formatação do trecho.

    Só casa texto contínuo dentro de um run: uma frase partida ao meio por um negrito vira dois
    runs no OOXML e não é encontrada. É limitação do formato, e a mensagem de erro diz isso.
    """
    trocas = 0
    if ext == ".docx":
        import docx

        doc = docx.Document(str(alvo))
        alvos = list(doc.paragraphs)
        for tabela in doc.tables:
            for linha in tabela.rows:
                for celula in linha.cells:
                    alvos.extend(celula.paragraphs)
        for p in alvos:
            for run in p.runs:
                if de in run.text:
                    run.text = run.text.replace(de, para)
                    trocas += 1
        if trocas:
            doc.save(str(alvo))
        return trocas

    from pptx import Presentation

    prs = Presentation(str(alvo))
    for slide in prs.slides:
        for forma in slide.shapes:
            if not forma.has_text_frame:
                continue
            for p in forma.text_frame.paragraphs:
                for run in p.runs:
                    if de in run.text:
                        run.text = run.text.replace(de, para)
                        trocas += 1
    if trocas:
        prs.save(str(alvo))
    return trocas


def _editar_pdf(alvo: Path, ops: list) -> list[str]:
    """PDF: a página é a unidade.

    Reescrever o texto não está aqui e não vai estar: PDF não guarda parágrafo, guarda glifo
    posicionado, e mexer nisso preservando o layout é outro problema. Para mudar o conteúdo, o
    caminho é gerar um PDF novo com write_document.
    """
    from pypdf import PdfReader, PdfWriter

    feitas: list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            raise ToolError("Cada operação é um objeto com 'tipo'.")
        tipo = str(op.get("tipo") or op.get("type") or "").lower()
        leitor = PdfReader(str(alvo))
        escritor = PdfWriter()
        if tipo in ("juntar", "merge"):
            outros = op.get("arquivos") or op.get("files") or []
            escritor.append(str(alvo))
            for outro in outros:
                caminho = alvo.parent / str(outro)
                if not caminho.is_file():
                    raise ToolError(f"Não achei '{outro}' para juntar, ao lado de {alvo.name}.")
                escritor.append(str(caminho))
            feitas.append(f"{len(outros)} PDF(s) anexado(s)")
        elif tipo in ("paginas", "pages", "extrair"):
            escolhidas = _paginas(op.get("paginas") or op.get("pages") or "", len(leitor.pages))
            for i in escolhidas:
                escritor.add_page(leitor.pages[i])
            feitas.append(f"{len(escolhidas)} de {len(leitor.pages)} página(s) mantida(s)")
        elif tipo in ("girar", "rotate"):
            graus = int(op.get("graus") or op.get("degrees") or 90)
            for pagina in leitor.pages:
                escritor.add_page(pagina)
            for pagina in escritor.pages:
                pagina.rotate(graus)
            feitas.append(f"páginas giradas em {graus} graus")
        else:
            raise ToolError(f"Tipo de operação desconhecido para PDF: '{tipo}'. Use juntar, paginas "
                            "ou girar. Para mudar o texto, gere um PDF novo com write_document.")
        with alvo.open("wb") as fh:
            escritor.write(fh)
    return feitas


def _paginas(spec, total: int) -> list[int]:
    """'1-3,7' ou [1,2,3] vira índice 0-based, na ordem pedida, sem sair do documento."""
    if isinstance(spec, list):
        numeros = [int(n) for n in spec]
    else:
        numeros = []
        for trecho in str(spec).split(","):
            trecho = trecho.strip()
            if "-" in trecho:
                a, _, b = trecho.partition("-")
                numeros.extend(range(int(a), int(b) + 1))
            elif trecho:
                numeros.append(int(trecho))
    fora = [n for n in numeros if not 1 <= n <= total]
    if fora or not numeros:
        raise ToolError(f"Páginas inválidas: {fora or 'nenhuma informada'}. O documento tem {total}.")
    return [n - 1 for n in numeros]


# ------------------------------------------------------------------ registro

def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


def _preview_documento(root: Path, args: dict) -> dict:
    """O card mostra o Markdown de origem, não os bytes: é o que dá para ler e conferir."""
    destino = _destino(root, str(args.get("path") or ""), DOCUMENTO, sobrescrever=True)
    rel = destino.relative_to(root.resolve()).as_posix()
    conteudo = str(args.get("content") or "")
    return {"kind": "diff" if destino.exists() else "new", "path": rel, "text": conteudo}


def _preview_planilha(root: Path, args: dict) -> dict:
    destino = _destino(root, str(args.get("path") or ""), PLANILHA, sobrescrever=True)
    rel = destino.relative_to(root.resolve()).as_posix()
    partes = []
    # Pela mesma normalização do handler: se o card aceitar o que a ferramenta recusa (ou o
    # contrário), o usuário aprova uma coisa e acontece outra.
    for aba in normalizar_abas(args.get("sheets")):
        linhas = aba["linhas"]
        partes.append(f"## {aba['nome']}" + NL
                      + _tabela_md([[str(c) for c in l] for l in linhas[:20]])
                      + (f"{NL}(+{len(linhas) - 20} linha(s))" if len(linhas) > 20 else ""))
    return {"kind": "diff" if destino.exists() else "new", "path": rel, "text": (NL * 2).join(partes)}


def _preview_edicao(root: Path, args: dict) -> dict:
    """Edição não tem diff barato (o arquivo é binário): o card lista o que vai ser feito."""
    import json as _json

    alvo = resolve_path(root, str(args.get("path") or "").strip())
    rel = alvo.relative_to(root.resolve()).as_posix() if alvo.is_relative_to(root.resolve()) else alvo.name
    ops = args.get("operations") or args.get("changes") or []
    return {"kind": "command", "path": rel,
            "text": (NL * 2).join(_json.dumps(o, ensure_ascii=False, indent=2) for o in ops if o)}


register(Tool(
    "write_document",
    "Cria um documento NOVO, do zero, a partir de Markdown: .docx (Word), .pdf, .pptx (PowerPoint), "
    ".html ou .md. O Markdown vira o documento de verdade — título vira título, tabela vira tabela, "
    "`---` vira quebra de página (e slide novo no .pptx). Caminho sem pasta cai em documentos/. "
    "NÃO use para mexer num documento que já existe (inclusive um que o usuário anexou): o "
    "resultado tem só o que você escrever, e o conteúdo original se perde. Para isso é o "
    "edit_document, no caminho do próprio arquivo — e caminho ocupado é recusado aqui. "
    "Dentro da célula de uma tabela, <br> vira quebra de linha. Para planilha use write_spreadsheet.",
    _obj({"path": {"type": "string", "description": "Ex.: relatorio.docx, propostas/resumo.pdf"},
          "content": {"type": "string", "description": "O documento inteiro, em Markdown"},
          "sobrescrever": {"type": "boolean", "description":
                           "Só para refazer do zero um arquivo que já existe, perdendo o conteúdo "
                           "atual. Para alterar preservando, é o edit_document."}},
         ["path", "content"]),
    write_document, mutating=True, preview=_preview_documento))

register(Tool(
    "write_spreadsheet",
    "Gera uma planilha .xlsx (uma ou mais abas) ou .csv (uma aba só). Célula começando com '=' vira "
    "fórmula de verdade, e número vira número. Caminho sem pasta cai em documentos/.",
    _obj({"path": {"type": "string", "description": "Ex.: vendas.xlsx, dados/export.csv"},
          "sheets": {"type": "array", "description": "Abas da planilha, na ordem",
                     "items": _obj({"nome": {"type": "string"},
                                    "linhas": {"type": "array", "description": "Linhas; a primeira é o cabeçalho",
                                               "items": {"type": "array", "items": {"type": "string"}}}},
                                   ["nome", "linhas"])},
          "sobrescrever": {"type": "boolean", "description":
                           "Só para refazer do zero uma planilha que já existe. Para alterar "
                           "preservando fórmulas e as outras abas, é o edit_spreadsheet."}},
         ["path", "sheets"]),
    write_spreadsheet, mutating=True, preview=_preview_planilha))

register(Tool(
    "edit_spreadsheet",
    "Altera uma planilha .xlsx que já existe, preservando o resto (fórmulas, formatação e as outras "
    "abas). Mudanças: {'tipo':'celula','aba':'Dados','celula':'B2','valor':10} | "
    "{'tipo':'linhas','aba':'Dados','linhas':[[...]]} | {'tipo':'aba','aba':'Nova','linhas':[[...]]} | "
    "{'tipo':'renomear','aba':'Dados','novo':'Vendas'}.",
    _obj({"path": {"type": "string"},
          "changes": {"type": "array", "items": {"type": "object"}}}, ["path", "changes"]),
    edit_spreadsheet, mutating=True, preview=_preview_edicao))

register(Tool(
    "preview_document",
    "Gera uma imagem do arquivo para conferir o resultado. Ela aparece no chat para o usuário; se "
    "você tiver visão, também chega a você. Sai sempre do arquivo salvo, então pega tabela que "
    "virou texto, conteúdo que sumiu e caixa alta que não pegou. Num .pdf mostra as páginas de "
    "verdade; num .docx, o Word da máquina renderiza quando está instalado (o texto da resposta diz "
    "qual caminho foi usado). Planilha e apresentação saem pela leitura, sem a diagramação do Office. "
    "É TAMBÉM a saída para PDF escaneado, em que read_file não devolve texto: as páginas chegam a "
    "você como imagem e você transcreve o que vê, em vez de dizer ao usuário que o arquivo é ilegível. "
    "NÃO é o jeito de conferir um site que você escreveu: um .html sai como foto única, sem rolagem, "
    "sem interação e sem console — para isso é o navegador (browser_navigate).",
    _obj({"path": {"type": "string", "description": "O arquivo a pré-visualizar"},
          "pagina": {"type": "integer",
                     "description": f"Só .pdf: primeira página da janela de {PAGINAS_PREVIA} "
                                    "(padrão 1). A resposta diz quando há mais para pedir."}},
         ["path"]),
    preview_document))

register(Tool(
    "edit_document",
    "Altera um .docx, .pptx ou .pdf que já existe, preservando o resto — é esta que atende "
    "\"adicione X neste documento\". Leia com read_file antes, para escolher onde entra. "
    "Word e PowerPoint: {'tipo':'inserir','depois':'<trecho do documento>','conteudo':'<markdown>'} "
    "põe conteúdo formatado no meio, logo depois desse trecho ('antes' em vez de 'depois' inverte; "
    "só .docx); {'tipo':'acrescentar','conteudo':'<markdown>'} põe no fim; "
    "{'tipo':'substituir','de':'x','para':'y'} troca texto por texto — este não "
    "interpreta Markdown, então não mande tabela por ele. "
    "PDF, onde a unidade é a página: {'tipo':'juntar','arquivos':['outro.pdf']} | "
    "{'tipo':'paginas','paginas':'1-3,7'} | {'tipo':'girar','graus':90}. Mudar o TEXTO de um PDF não "
    "dá — para isso, gere um PDF novo com write_document.",
    _obj({"path": {"type": "string"},
          "operations": {"type": "array", "items": {"type": "object"}}}, ["path", "operations"]),
    edit_document, mutating=True, preview=_preview_edicao))

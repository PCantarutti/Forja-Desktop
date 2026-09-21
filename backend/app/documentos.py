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
    saida = ["| " + " | ".join(c.replace("|", BARRA_PIPE) for c in cabecalho) + " |",
             "|" + "|".join([" --- "] * largura) + "|"]
    for linha in corpo[1:]:
        saida.append("| " + " | ".join(str(c).replace("|", BARRA_PIPE) for c in linha) + " |")
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


def _extrair_docx(origem: Path) -> str | None:
    import docx

    doc = docx.Document(str(origem))
    partes: list[str] = []
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
    import openpyxl

    livro = openpyxl.load_workbook(str(origem), read_only=True, data_only=True)
    try:
        partes: list[str] = []
        for aba in livro.worksheets:
            linhas: list[list[str]] = []
            for linha in aba.iter_rows(max_row=MAX_LINHAS_ABA, max_col=MAX_COLUNAS, values_only=True):
                if all(c is None for c in linha):
                    continue
                linhas.append(["" if c is None else str(c) for c in linha])
            corte = f"{NL}(cortado em {MAX_LINHAS_ABA} linhas)" if aba.max_row and aba.max_row > MAX_LINHAS_ABA else ""
            tabela = _tabela_md(linhas) if linhas else "(aba vazia)"
            partes.append(f"## {aba.title}{NL}{NL}{tabela}{corte}")
        return (NL * 2).join(partes) or None
    finally:
        livro.close()


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


def _celulas(linha: str) -> list[str]:
    bruto = linha.strip().strip("|")
    return [c.strip().replace(BARRA_PIPE, "|") for c in re.split(r"(?<!" + re.escape(chr(92)) + r")\|", bruto)]


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
            cab = "".join(f"<th>{inline(c)}</th>" for c in linhas[0])
            resto = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in l) + "</tr>" for l in linhas[1:])
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
            doc.add_heading(_sem_marcas(b["texto"]), level=min(b["nivel"], 9))
        elif b["tipo"] == "paragrafo":
            _runs_docx(doc.add_paragraph(), b["texto"])
        elif b["tipo"] == "lista":
            for item in b["itens"]:
                estilo = "List Number" if b["ordenada"] else "List Bullet"
                _runs_docx(doc.add_paragraph(style=estilo), item)
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
            tabela = doc.add_table(rows=len(linhas), cols=max(len(l) for l in linhas))
            tabela.style = "Table Grid"
            for i, linha in enumerate(linhas):
                for j, celula in enumerate(linha):
                    tabela.cell(i, j).text = _sem_marcas(celula)
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


async def para_pdf(bs: list[dict], destino: Path, titulo: str = "") -> None:
    """HTML -> PDF pelo Chromium que o app já empacota. Nenhuma biblioteca de PDF no meio.

    Sobe uma instância headless própria de propósito: no Desktop o `browser.MANAGER` está ligado por
    CDP ao Electron, que é headed, e `page.pdf()` só existe em Chromium headless. Como a página é
    montada aqui e carregada por `set_content`, ela nunca vai à rede.
    """
    from playwright.async_api import async_playwright

    html = para_html(bs, titulo)
    async with async_playwright() as pw:
        try:
            navegador = await pw.chromium.launch(headless=True)
        except Exception as e:
            raise ToolError(
                "Não consegui abrir o Chromium para gerar o PDF. Ele vem com o Forja instalado; em "
                "desenvolvimento, rode `python -m playwright install --only-shell chromium`. "
                f"({e.__class__.__name__}) Gerar .docx ou .html não depende dele.") from e
        try:
            pagina = await navegador.new_page()
            # `file://` da pasta de destino: é o que faz ![](imagem.png) relativo funcionar.
            await pagina.goto((destino.parent.resolve().as_uri() + "/"))
            await pagina.set_content(html, wait_until="load")
            dados = await pagina.pdf(format="A4", print_background=True,
                                     margin={"top": "2cm", "bottom": "2cm", "left": "2cm", "right": "2cm"},
                                     display_header_footer=True, header_template="<div></div>",
                                     footer_template='<div style="width:100%;font-size:8pt;color:#777;'
                                                     'text-align:center"><span class="pageNumber"></span>'
                                                     '/<span class="totalPages"></span></div>')
        finally:
            await navegador.close()
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


def _destino(root: Path, caminho: str, aceitas: set[str]) -> Path:
    """Resolve o caminho de saída, confinado à pasta da conversa, e valida a extensão."""
    bruto = (caminho or "").strip().replace(chr(92), "/")
    if not bruto:
        raise ToolError("Informe o caminho do arquivo, com a extensão.")
    ext = Path(bruto).suffix.lower()
    if ext not in aceitas:
        raise ToolError(f"Extensão '{ext or '(nenhuma)'}' não serve aqui. Use: {', '.join(sorted(aceitas))}.")
    if "/" not in bruto:
        bruto = f"{PASTA}/{bruto}"
    destino = resolve_path(root, bruto)
    destino.parent.mkdir(parents=True, exist_ok=True)
    return destino


def _resultado(root: Path, destino: Path, o_que: str) -> dict:
    """Texto para o modelo + anexo para o chat."""
    anexo = _anexo(root, destino)
    return {"text": f"{o_que} em {anexo['path']} ({anexo['size'] // 1024} KB).", "attachments": [anexo]}


async def write_document(root: Path, args: dict) -> dict:
    destino = _destino(root, str(args.get("path") or ""), DOCUMENTO)
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


def write_spreadsheet(root: Path, args: dict) -> dict:
    destino = _destino(root, str(args.get("path") or ""), PLANILHA)
    abas = args.get("sheets") or []
    if not isinstance(abas, list) or not abas:
        raise ToolError("sheets vazio: mande ao menos uma aba, com nome e linhas.")
    for aba in abas:
        if not isinstance(aba, dict) or not isinstance(aba.get("linhas") or aba.get("rows"), list):
            raise ToolError('Cada aba é {"nome": "Dados", "linhas": [["A", "B"], [1, 2]]}.')
        aba.setdefault("linhas", aba.get("rows"))
    if destino.suffix.lower() == ".csv":
        para_csv(abas, destino)
        if len(abas) > 1:
            return _resultado(root, destino, f"CSV gerado com a primeira das {len(abas)} abas (CSV não tem abas);")
    else:
        para_xlsx(abas, destino)
    return _resultado(root, destino, "Planilha gerada")


# ------------------------------------------------------------------ editar o que já existe

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
    if ext == ".pdf":
        feitas = await asyncio.to_thread(_editar_pdf, alvo, ops)
    else:
        feitas = await asyncio.to_thread(_editar_office, alvo, ops, ext)
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
        elif tipo in ("substituir", "replace"):
            de = str(op.get("de") or op.get("old") or "")
            para = str(op.get("para") or op.get("new") or "")
            if not de:
                raise ToolError("Falta 'de': o texto a ser substituído.")
            n = _substituir(alvo, de, para, ext)
            if not n:
                raise ToolError(f"Não achei '{de[:40]}' no documento. Leia com read_file antes, e "
                                "lembre que só casa texto contínuo, com a mesma formatação.")
            feitas.append(f"{n} ocorrência(s) trocada(s)")
        else:
            raise ToolError(f"Tipo de operação desconhecido: '{tipo}'. Use acrescentar ou substituir.")
    return feitas


def _acrescentar_docx(alvo: Path, bs: list[dict]) -> None:
    import docx

    doc = docx.Document(str(alvo))
    for b in bs:
        if b["tipo"] == "titulo":
            doc.add_heading(_sem_marcas(b["texto"]), level=min(b["nivel"], 9))
        elif b["tipo"] == "lista":
            for item in b["itens"]:
                _runs_docx(doc.add_paragraph(style="List Number" if b["ordenada"] else "List Bullet"), item)
        elif b["tipo"] == "tabela":
            linhas = b["linhas"]
            tabela = doc.add_table(rows=len(linhas), cols=max(len(l) for l in linhas))
            tabela.style = "Table Grid"
            for i, linha in enumerate(linhas):
                for j, celula in enumerate(linha):
                    tabela.cell(i, j).text = _sem_marcas(celula)
        elif b["tipo"] in ("paragrafo", "codigo"):
            _runs_docx(doc.add_paragraph(), b.get("texto", ""))
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
    destino = _destino(root, str(args.get("path") or ""), DOCUMENTO)
    rel = destino.relative_to(root.resolve()).as_posix()
    conteudo = str(args.get("content") or "")
    return {"kind": "diff" if destino.exists() else "new", "path": rel, "text": conteudo}


def _preview_planilha(root: Path, args: dict) -> dict:
    destino = _destino(root, str(args.get("path") or ""), PLANILHA)
    rel = destino.relative_to(root.resolve()).as_posix()
    partes = []
    for aba in args.get("sheets") or []:
        linhas = (aba or {}).get("linhas") or (aba or {}).get("rows") or []
        partes.append(f"## {(aba or {}).get('nome') or (aba or {}).get('name') or 'Planilha'}"
                      + NL + _tabela_md([[str(c) for c in l] for l in linhas[:20]])
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
    "Gera um documento a partir de Markdown: .docx (Word), .pdf, .pptx (PowerPoint), .html ou .md. "
    "O Markdown vira o documento de verdade — título vira título, tabela vira tabela, `---` vira "
    "quebra de página (e slide novo no .pptx). Caminho sem pasta cai em documentos/. "
    "Para planilha use write_spreadsheet.",
    _obj({"path": {"type": "string", "description": "Ex.: relatorio.docx, propostas/resumo.pdf"},
          "content": {"type": "string", "description": "O documento inteiro, em Markdown"}},
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
                                   ["nome", "linhas"])}},
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
    "edit_document",
    "Altera um .docx, .pptx ou .pdf que já existe. Word e PowerPoint: "
    "{'tipo':'acrescentar','conteudo':'<markdown>'} ou {'tipo':'substituir','de':'x','para':'y'}. "
    "PDF, onde a unidade é a página: {'tipo':'juntar','arquivos':['outro.pdf']} | "
    "{'tipo':'paginas','paginas':'1-3,7'} | {'tipo':'girar','graus':90}. Mudar o TEXTO de um PDF não "
    "dá — para isso, gere um PDF novo com write_document.",
    _obj({"path": {"type": "string"},
          "operations": {"type": "array", "items": {"type": "object"}}}, ["path", "operations"]),
    edit_document, mutating=True, preview=_preview_edicao))

"""Anexar um documento tem que funcionar.

Antes, o PDF anexado virava um `.txt` ao lado, porque o `read_file` recusava binário. Agora o
`read_file` lê PDF, Word, Excel e PowerPoint direto (ver `documentos.py`), então o upload não
precisa mais preparar nada — só conferir se há texto a extrair, para avisar quando não há.
"""
import asyncio
import io

import pytest

from app import documentos, ocr, uploads
from app.tools import ToolError, run_tool


def pdf_com_texto(texto: str) -> bytes:
    """Um PDF mínimo, montado à mão, com uma página e esse texto.

    Montado byte a byte de propósito: nenhum binário fica versionado no repositório.
    """
    fluxo = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(fluxo)).encode() + b" >>stream" + bytes([10]) + fluxo + bytes([10]) + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4" + bytes([10]))
    offsets = []
    for i, corpo in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj".encode() + bytes([10]) + corpo + bytes([10]) + b"endobj" + bytes([10])
    xref = len(out)
    out += f"xref{chr(10)}0 {len(objs) + 1}{chr(10)}".encode() + b"0000000000 65535 f " + bytes([10])
    for off in offsets:
        out += f"{off:010d} 00000 n ".encode() + bytes([10])
    out += (f"trailer{chr(10)}<< /Size {len(objs) + 1} /Root 1 0 R >>{chr(10)}"
            f"startxref{chr(10)}{xref}{chr(10)}%%EOF{chr(10)}").encode()
    return bytes(out)


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads.workspace, "root", lambda: tmp_path)
    return tmp_path


def test_pdf_anexado_e_lido_pelo_read_file(raiz):
    """O caminho inteiro: anexar, e o agente abrir com a ferramenta que ele já usa."""
    anexo = uploads.save("relatorio.pdf", pdf_com_texto("linha do relatorio"), "application/pdf", raiz)
    assert "sem_texto" not in anexo
    assert "linha do relatorio" in run_tool("read_file", {"path": anexo["path"]}, raiz)


def test_nao_sobra_arquivo_ao_lado_do_anexo(raiz):
    """O `.txt` paralelo que existia aqui saiu junto com o motivo dele."""
    uploads.save("relatorio.pdf", pdf_com_texto("conteudo"), "application/pdf", raiz)
    nomes = sorted(p.name for p in (raiz / uploads.UPLOAD_DIR).iterdir())
    assert len(nomes) == 1 and nomes[0].endswith(".pdf")


def test_a_mensagem_manda_usar_o_read_file_no_proprio_arquivo(raiz):
    anexo = uploads.save("relatorio.pdf", pdf_com_texto("conteudo"), "application/pdf", raiz)
    msg = uploads.user_message("resuma isto", [anexo])
    assert anexo["path"] in msg["content"] and "read_file" in msg["content"]


def test_documento_sem_texto_avisa_em_vez_de_mentir(raiz):
    """PDF escaneado é imagem: prometer que o read_file abre empurraria o modelo para o erro."""
    anexo = uploads.save("scan.pdf", b"%PDF-1.4 lixo que nao e pdf", "application/pdf", raiz)
    assert anexo.get("sem_texto")
    assert "não sai texto direto" in uploads.user_message("leia", [anexo])["content"]


def test_planilha_anexada_tambem_e_lida(raiz):
    """O upload não tem nada de específico de PDF: vale para todo formato do documentos.LEITURA."""
    planilha = raiz / "origem.xlsx"
    documentos.para_xlsx([{"nome": "Dados", "linhas": [["Produto", "Valor"], ["Cafe", "12"]]}], planilha)
    anexo = uploads.save("origem.xlsx", planilha.read_bytes(),
                         documentos.MIMES[".xlsx"], raiz)
    assert "sem_texto" not in anexo
    lido = run_tool("read_file", {"path": anexo["path"]}, raiz)
    assert "Produto" in lido and "Cafe" in lido


def test_arquivo_de_texto_segue_igual(raiz):
    anexo = uploads.save("notas.txt", b"oi", "text/plain", raiz)
    assert anexo["kind"] == "text" and "sem_texto" not in anexo
    assert anexo["path"] in uploads.user_message("veja", [anexo])["content"]


def test_documento_de_verdade_cabe_no_anexo(raiz, monkeypatch):
    """Aconteceu em uso: nenhum documento anexava. O teto do anexo era o do texto puro (1 MB), e
    PDF ou planilha de verdade passa disso sem esforço."""
    from app import config

    grande = pdf_com_texto("conteudo") + b"%" + b"x" * 1_500_000  # 1,5 MB
    anexo = uploads.save("relatorio.pdf", grande, "application/pdf", raiz)
    assert anexo["size"] > config.MAX_FILE_BYTES

    monkeypatch.setattr(config, "MAX_DOC_BYTES", 1000)
    with pytest.raises(ValueError, match="maior que o limite"):
        uploads.save("outro.pdf", grande, "application/pdf", raiz)


def test_arquivo_de_texto_mantem_o_teto_menor(raiz, monkeypatch):
    """O teto folgado é só para documento: texto vai inteiro ao contexto e continua com 1 MB."""
    from app import config

    monkeypatch.setattr(config, "MAX_FILE_BYTES", 100)
    with pytest.raises(ValueError, match="maior que o limite"):
        uploads.save("notas.txt", b"x" * 200, "text/plain", raiz)


def pdf_escaneado(paginas: int = 1, linhas: list[str] | None = None) -> bytes:
    """PDF em que a página é imagem, como sai de um scanner: não há texto a extrair, só pixels."""
    from PIL import Image, ImageDraw, ImageFont

    try:  # fonte de verdade: a embutida do PIL é pequena demais para o OCR acertar
        fonte = ImageFont.truetype("arial.ttf", 30)
    except OSError:
        fonte = ImageFont.load_default()
    folhas = []
    for i in range(1, paginas + 1):
        img = Image.new("RGB", (1000, 800), "white")
        desenho = ImageDraw.Draw(img)
        for n, linha in enumerate([f"PAGINA {i}"] + (linhas or [])):
            desenho.text((40, 40 + n * 60), linha, fill="black", font=fonte)
        folhas.append(img)
    buf = io.BytesIO()
    folhas[0].save(buf, "PDF", save_all=True, append_images=folhas[1:])
    return buf.getvalue()


def test_previa_do_pdf_escaneado_devolve_as_paginas_como_imagem(raiz):
    anexo = uploads.save("scan.pdf", pdf_escaneado(), "application/pdf", raiz)
    r = asyncio.run(documentos.preview_document(raiz, {"path": anexo["path"]}))
    assert len(r["attachments"]) == 1 and r["attachments"][0]["mime"] == "image/jpeg"


def test_previa_avanca_a_janela_ate_a_ultima_pagina(raiz):
    """Sem isto o modelo transcrevia as três primeiras páginas e dava o trabalho por terminado."""
    anexo = uploads.save("scan.pdf", pdf_escaneado(5), "application/pdf", raiz)

    inicio = asyncio.run(documentos.preview_document(raiz, {"path": anexo["path"]}))
    assert len(inicio["attachments"]) == documentos.PAGINAS_PREVIA
    assert "5 páginas" in inicio["text"] and "pagina=4" in inicio["text"]

    resto = asyncio.run(documentos.preview_document(raiz, {"path": anexo["path"], "pagina": 4}))
    assert len(resto["attachments"]) == 2 and "chame de novo" not in resto["text"]

    with pytest.raises(ToolError, match="não existe a página"):
        asyncio.run(documentos.preview_document(raiz, {"path": anexo["path"], "pagina": 9}))


def test_ocr_nao_roda_sozinho_e_a_visao_vem_primeiro(raiz):
    """Aconteceu em uso: modelo COM visão recebeu o palpite do OCR e nem olhou o documento.

    Enxergar a página é mais fiel que OCR, então o read_file não pode decidir isso sozinho: ele
    volta vazio apontando as duas saídas, na ordem, e o OCR só roda se pedirem.
    """
    anexo = uploads.save("scan.pdf", pdf_escaneado(), "application/pdf", raiz)
    assert anexo.get("sem_texto")

    aviso = uploads.user_message("transcreva", [anexo])["content"]
    assert aviso.index("preview_document") < aviso.index("ocr=true")  # a ordem é a mensagem

    with pytest.raises(ToolError) as erro:
        run_tool("read_file", {"path": anexo["path"]}, raiz)
    assert "preview_document" in str(erro.value) and "ocr=true" in str(erro.value)


def test_pdf_escaneado_e_lido_pelo_ocr_quando_pedido(raiz):
    """O pedido que gerou o OCR: modelo sem visão não tinha como ler PDF de imagem nenhum."""
    if not ocr.disponivel():
        pytest.skip("sem OCR nesta máquina")

    anexo = uploads.save("scan.pdf", pdf_escaneado(linhas=["Inspecao concluida", "Total 2026"]),
                         "application/pdf", raiz)
    lido = run_tool("read_file", {"path": anexo["path"], "ocr": True}, raiz)
    assert "2026" in lido
    assert "OCR" in lido  # o modelo tem que saber que está lendo palpite de máquina, não o arquivo


def test_sem_ocr_o_caminho_continua_sendo_a_visao(raiz, monkeypatch):
    """Máquina sem motor de OCR: mesmo pedindo ocr=true, o que sobra é olhar a página."""
    monkeypatch.setattr(ocr, "disponivel", lambda: False)

    anexo = uploads.save("scan.pdf", pdf_escaneado(), "application/pdf", raiz)
    with pytest.raises(ToolError, match="preview_document"):
        run_tool("read_file", {"path": anexo["path"], "ocr": True}, raiz)


def test_ocr_remonta_a_fileira_da_tabela(raiz):
    """O OCR do Windows varre coluna a coluna: sem remontar, a tabela chega certa e trocada.

    Que é o pior caso — reconhecimento perfeito e linhas pareadas erradas parecem corretos para
    quem lê. Aqui cada fileira tem que voltar junta, na ordem da esquerda para a direita.
    """
    if not ocr.disponivel():
        pytest.skip("sem OCR nesta máquina")

    from PIL import Image, ImageDraw, ImageFont

    try:
        fonte = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        pytest.skip("sem fonte TrueType para desenhar a tabela")

    img = Image.new("RGB", (1100, 400), "white")
    desenho = ImageDraw.Draw(img)
    for i, (codigo, situacao) in enumerate([("A-102", "Concluido"), ("B-317", "Pendencia")]):
        desenho.text((60, 60 + i * 90), codigo, fill="black", font=fonte)
        desenho.text((700, 60 + i * 90), situacao, fill="black", font=fonte)  # coluna bem afastada
    buf = io.BytesIO()
    img.save(buf, "PDF")

    anexo = uploads.save("tabela.pdf", buf.getvalue(), "application/pdf", raiz)
    lido = run_tool("read_file", {"path": anexo["path"], "ocr": True}, raiz)
    assert "A-102 | Concluido" in lido, lido
    assert "B-317 | Pendencia" in lido, lido

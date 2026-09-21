"""Anexar um PDF tem que funcionar.

Antes, o PDF virava `kind: "file"` e a mensagem mandava o modelo abrir com `read_file` — que
recusa binário (`tools._read_text`). O anexo simplesmente não fazia nada, sem dizer por quê.
"""
import zlib

import pytest

from app import uploads


def pdf_com_texto(texto: str) -> bytes:
    """Um PDF mínimo, montado à mão, com uma página e esse texto."""
    fluxo = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(fluxo)).encode() + b" >>stream\n" + fluxo + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, corpo in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + corpo + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads.workspace, "root", lambda: tmp_path)
    return tmp_path


def test_pdf_ganha_um_txt_ao_lado(raiz):
    anexo = uploads.save("relatorio.pdf", pdf_com_texto("linha do relatorio"), "application/pdf", raiz)
    assert anexo["text_path"].endswith(".txt")
    extraido = (raiz / anexo["text_path"]).read_text(encoding="utf-8")
    assert "linha do relatorio" in extraido and "página 1" in extraido


def test_a_mensagem_aponta_para_o_texto_e_nao_para_o_binario(raiz):
    anexo = uploads.save("relatorio.pdf", pdf_com_texto("conteudo"), "application/pdf", raiz)
    msg = uploads.user_message("resuma isto", [anexo])
    assert anexo["text_path"] in msg["content"]
    assert "é um PDF" in msg["content"]


def test_pdf_sem_texto_avisa_em_vez_de_mentir(raiz):
    """PDF escaneado é imagem: prometer que o read_file abre seria empurrar o modelo para o erro."""
    anexo = uploads.save("scan.pdf", b"%PDF-1.4 lixo que nao e pdf", "application/pdf", raiz)
    assert anexo.get("sem_texto") and "text_path" not in anexo
    msg = uploads.user_message("leia", [anexo])
    assert "sem texto extraível" in msg["content"]


def test_arquivo_comum_segue_igual(raiz):
    anexo = uploads.save("notas.txt", b"oi", "text/plain", raiz)
    assert anexo["kind"] == "text" and "text_path" not in anexo
    assert anexo["path"] in uploads.user_message("veja", [anexo])["content"]

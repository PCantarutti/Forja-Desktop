"""Documentos de escritório: ler e gerar .pdf, .docx, .xlsx, .pptx e .csv.

O teste que mais paga é a ida e volta: gerar com a ferramenta e ler de volta com `read_file`. Ele
pega o parser de Markdown, o renderizador daquele formato e o extrator de uma vez só — e falha se
qualquer um dos três quebrar.

Nenhum binário fica versionado: todo arquivo de entrada é produzido pelas próprias ferramentas.
"""
import asyncio
import pathlib

import pytest

from app import config, documentos
from app.tools import ToolError, run_tool


def _tem_chromium() -> bool:
    """PDF precisa do Chromium do Playwright. No app ele vem junto; numa máquina crua, não.

    Tenta o que a feature faz, em vez de olhar `executable_path`: o instalador traz só o
    headless-shell, e aquele atributo aponta para o Chromium completo, que não está lá.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            pw.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


precisa_chromium = pytest.mark.skipif(
    not _tem_chromium(),
    reason="PDF precisa do Chromium: python -m playwright install --only-shell chromium")


@pytest.fixture
def ws(tmp_path):
    return tmp_path


def roda(nome, args, ws):
    """Chama a ferramenta pelo nome, sem agente e sem servidor. Async vai pelo asyncio."""
    from app.tools import REGISTRY

    tool = REGISTRY[nome]
    if asyncio.iscoroutinefunction(tool.handler):
        return asyncio.run(tool.handler(ws, args))
    return run_tool(nome, args, ws)


# ------------------------------------------------ parser de Markdown

def test_parser_reconhece_os_blocos():
    bs = documentos.blocos(
        "# Titulo" + chr(10) * 2 + "Um paragrafo." + chr(10) * 2
        + "- a" + chr(10) + "- b" + chr(10) * 2
        + "| A | B |" + chr(10) + "| --- | --- |" + chr(10) + "| 1 | 2 |" + chr(10) * 2
        + "---" + chr(10) * 2 + "Fim.")
    tipos = [b["tipo"] for b in bs]
    assert tipos == ["titulo", "paragrafo", "lista", "tabela", "quebra", "paragrafo"]
    assert bs[0]["nivel"] == 1 and bs[2]["itens"] == ["a", "b"]
    assert bs[3]["linhas"] == [["A", "B"], ["1", "2"]]


def test_parser_junta_linhas_do_mesmo_paragrafo():
    bs = documentos.blocos("uma linha" + chr(10) + "e a continuacao dela")
    assert len(bs) == 1 and bs[0]["texto"] == "uma linha e a continuacao dela"


def test_parser_separa_lista_ordenada_da_com_marcador():
    bs = documentos.blocos("- a" + chr(10) * 2 + "1. um")
    assert [b["ordenada"] for b in bs] == [False, True]


def test_parser_nao_confunde_codigo_com_o_resto():
    bs = documentos.blocos("```python" + chr(10) + "# isto nao e titulo" + chr(10) + "```")
    assert len(bs) == 1 and bs[0]["tipo"] == "codigo" and bs[0]["lingua"] == "python"


# ------------------------------------------------ ida e volta, um por formato

MARKDOWN = ("# Relatorio do trimestre" + chr(10) * 2
            + "Vendas cresceram no periodo." + chr(10) * 2
            + "| Produto | Valor |" + chr(10) + "| --- | --- |" + chr(10)
            + "| Cafe | 1200 |" + chr(10) + "| Pao | 350 |" + chr(10) * 2
            + "- primeiro ponto" + chr(10) + "- segundo ponto")


@pytest.mark.parametrize("arquivo", ["saida.docx", pytest.param("saida.pdf", marks=precisa_chromium),
                                     "saida.md", "saida.html"])
def test_gera_documento_e_le_de_volta(ws, arquivo):
    out = roda("write_document", {"path": arquivo, "content": MARKDOWN}, ws)
    gerado = ws / "documentos" / arquivo
    assert gerado.is_file() and gerado.stat().st_size > 0
    assert out["attachments"][0]["path"] == f"documentos/{arquivo}"
    if arquivo.endswith((".md", ".html")):
        return  # texto puro: o read_file já lia antes desta feature
    lido = run_tool("read_file", {"path": f"documentos/{arquivo}"}, ws)
    assert "Relatorio do trimestre" in lido
    assert "Cafe" in lido and "1200" in lido


def test_gera_pptx_com_um_slide_por_titulo(ws):
    md = "# Primeiro" + chr(10) * 2 + "corpo um" + chr(10) * 2 + "# Segundo" + chr(10) * 2 + "corpo dois"
    roda("write_document", {"path": "apre.pptx", "content": md}, ws)
    lido = run_tool("read_file", {"path": "documentos/apre.pptx"}, ws)
    assert "Slide 1" in lido and "Slide 2" in lido
    assert "Primeiro" in lido and "corpo dois" in lido


@pytest.mark.parametrize("arquivo", ["dados.xlsx", "dados.csv"])
def test_gera_planilha_e_le_de_volta(ws, arquivo):
    abas = [{"nome": "Vendas", "linhas": [["Produto", "Valor"], ["Cafe", "1200"], ["Pao", "350"]]}]
    roda("write_spreadsheet", {"path": arquivo, "sheets": abas}, ws)
    lido = run_tool("read_file", {"path": f"documentos/{arquivo}"}, ws)
    assert "Produto" in lido and "Cafe" in lido and "1200" in lido


def test_planilha_guarda_numero_como_numero_e_formula_como_formula(ws):
    import openpyxl

    abas = [{"nome": "Contas", "linhas": [["Item", "Valor"], ["a", "10"], ["b", "32,5"],
                                          ["total", "=SUM(B2:B3)"]]}]
    roda("write_spreadsheet", {"path": "contas.xlsx", "sheets": abas}, ws)
    folha = openpyxl.load_workbook(ws / "documentos" / "contas.xlsx").active
    assert folha["B2"].value == 10
    assert folha["B3"].value == 32.5          # vírgula decimal do pt-BR
    assert folha["B4"].value == "=SUM(B2:B3)"  # fórmula, não texto


# ------------------------------------------------ editar sem estragar o resto

def test_edita_celula_sem_tocar_na_outra_aba(ws):
    abas = [{"nome": "Um", "linhas": [["a", "1"]]}, {"nome": "Dois", "linhas": [["b", "2"]]}]
    roda("write_spreadsheet", {"path": "p.xlsx", "sheets": abas}, ws)
    roda("edit_spreadsheet", {"path": "documentos/p.xlsx",
                              "changes": [{"tipo": "celula", "aba": "Um", "celula": "B1", "valor": 99}]}, ws)
    import openpyxl

    livro = openpyxl.load_workbook(ws / "documentos" / "p.xlsx")
    assert livro["Um"]["B1"].value == 99
    assert livro["Dois"]["A1"].value == "b" and livro["Dois"]["B1"].value == 2  # intacta


def test_acrescenta_aba_e_linhas(ws):
    roda("write_spreadsheet", {"path": "p.xlsx", "sheets": [{"nome": "Um", "linhas": [["a", "1"]]}]}, ws)
    roda("edit_spreadsheet", {"path": "documentos/p.xlsx", "changes": [
        {"tipo": "linhas", "aba": "Um", "linhas": [["b", "2"]]},
        {"tipo": "aba", "aba": "Nova", "linhas": [["x", "9"]]}]}, ws)
    lido = run_tool("read_file", {"path": "documentos/p.xlsx"}, ws)
    assert "## Um" in lido and "## Nova" in lido and "b" in lido and "x" in lido


def test_aba_que_nao_existe_diz_quais_existem(ws):
    roda("write_spreadsheet", {"path": "p.xlsx", "sheets": [{"nome": "Um", "linhas": [["a"]]}]}, ws)
    with pytest.raises(ToolError, match="não existe"):
        roda("edit_spreadsheet", {"path": "documentos/p.xlsx",
                                  "changes": [{"tipo": "celula", "aba": "Fantasma", "celula": "A1", "valor": 1}]}, ws)


def test_acrescenta_secao_no_docx_preservando_o_que_havia(ws):
    roda("write_document", {"path": "d.docx", "content": "# Original" + chr(10) * 2 + "texto de antes"}, ws)
    roda("edit_document", {"path": "documentos/d.docx",
                           "operations": [{"tipo": "acrescentar", "conteudo": "## Anexo" + chr(10) * 2 + "texto novo"}]}, ws)
    lido = run_tool("read_file", {"path": "documentos/d.docx"}, ws)
    assert "texto de antes" in lido and "texto novo" in lido and "Anexo" in lido


def test_substituir_texto_no_docx(ws):
    roda("write_document", {"path": "d.docx", "content": "O prazo e de 30 dias."}, ws)
    roda("edit_document", {"path": "documentos/d.docx",
                           "operations": [{"tipo": "substituir", "de": "30 dias", "para": "45 dias"}]}, ws)
    lido = run_tool("read_file", {"path": "documentos/d.docx"}, ws)
    assert "45 dias" in lido and "30 dias" not in lido


def test_substituir_o_que_nao_existe_avisa_em_vez_de_calar(ws):
    roda("write_document", {"path": "d.docx", "content": "qualquer coisa"}, ws)
    with pytest.raises(ToolError, match="Não achei"):
        roda("edit_document", {"path": "documentos/d.docx",
                               "operations": [{"tipo": "substituir", "de": "inexistente", "para": "x"}]}, ws)


@precisa_chromium
def test_pdf_extrai_paginas(ws):
    md = "# Um" + chr(10) * 2 + "---" + chr(10) * 2 + "# Dois" + chr(10) * 2 + "---" + chr(10) * 2 + "# Tres"
    roda("write_document", {"path": "p.pdf", "content": md}, ws)
    roda("edit_document", {"path": "documentos/p.pdf", "operations": [{"tipo": "paginas", "paginas": "1,3"}]}, ws)
    from pypdf import PdfReader

    assert len(PdfReader(str(ws / "documentos" / "p.pdf")).pages) == 2


@precisa_chromium
def test_pdf_recusa_editar_texto_e_explica_o_caminho(ws):
    roda("write_document", {"path": "p.pdf", "content": "# oi"}, ws)
    with pytest.raises(ToolError, match="write_document"):
        roda("edit_document", {"path": "documentos/p.pdf",
                               "operations": [{"tipo": "substituir", "de": "oi", "para": "tchau"}]}, ws)


# ------------------------------------------------ confinamento, limites e recusas

def test_nao_escreve_fora_da_pasta_da_conversa(ws):
    with pytest.raises(ToolError, match="Acesso negado"):
        roda("write_document", {"path": "../fora.docx", "content": "# x"}, ws)


def test_extensao_que_nao_serve_e_recusada(ws):
    with pytest.raises(ToolError, match="não serve aqui"):
        roda("write_document", {"path": "arquivo.xlsx", "content": "# x"}, ws)
    with pytest.raises(ToolError, match="não serve aqui"):
        roda("write_spreadsheet", {"path": "arquivo.docx", "sheets": [{"nome": "a", "linhas": [["x"]]}]}, ws)


def test_caminho_com_pasta_e_respeitado(ws):
    roda("write_document", {"path": "propostas/2026/plano.docx", "content": "# Plano"}, ws)
    assert (ws / "propostas" / "2026" / "plano.docx").is_file()


def test_documento_grande_demais_recusa_com_o_limite(ws, monkeypatch):
    roda("write_document", {"path": "d.docx", "content": "# oi"}, ws)
    monkeypatch.setattr(config, "MAX_DOC_BYTES", 10)
    with pytest.raises(ToolError, match="grande demais"):
        run_tool("read_file", {"path": "documentos/d.docx"}, ws)


def test_arquivo_ilegivel_avisa_em_vez_de_devolver_vazio(ws):
    (ws / "quebrado.docx").write_bytes(b"isto nao e um docx")
    with pytest.raises(ToolError, match="Não consegui extrair"):
        run_tool("read_file", {"path": "quebrado.docx"}, ws)


def test_read_file_continua_lendo_texto_normal(ws):
    (ws / "a.py").write_text("linha1" + chr(10) + "linha2" + chr(10), encoding="utf-8")
    assert "linha2" in run_tool("read_file", {"path": "a.py"}, ws)


def test_read_file_numera_e_corta_por_linha_no_documento(ws):
    linhas = [f"item {i}" for i in range(1, 30)]
    roda("write_spreadsheet", {"path": "d.csv", "sheets": [{"nome": "a", "linhas": [[l] for l in linhas]}]}, ws)
    saida = run_tool("read_file", {"path": "documentos/d.csv", "start_line": 3, "end_line": 4}, ws)
    assert "item 2" in saida and "item 3" in saida   # 3ª e 4ª: cabeçalho e separador vêm antes
    assert "item 1" not in saida and "item 5" not in saida
    assert "mostrando linhas 3-4" in saida


# ------------------------------------------------ o card de aprovação

def test_preview_mostra_o_markdown_e_nao_os_bytes(ws):
    pv = documentos._preview_documento(ws, {"path": "novo.docx", "content": MARKDOWN})
    assert pv["kind"] == "new" and pv["path"] == "documentos/novo.docx"
    assert "Relatorio do trimestre" in pv["text"]


def test_preview_de_arquivo_existente_vira_diff(ws):
    roda("write_document", {"path": "d.docx", "content": "# antes"}, ws)
    pv = documentos._preview_documento(ws, {"path": "d.docx", "content": "# depois"})
    assert pv["kind"] == "diff"


def test_preview_de_planilha_mostra_as_abas(ws):
    pv = documentos._preview_planilha(ws, {"path": "p.xlsx", "sheets": [
        {"nome": "Vendas", "linhas": [["a", "b"], ["1", "2"]]}]})
    assert "## Vendas" in pv["text"] and "| a | b |" in pv["text"]

"""Documentos de escritório: ler e gerar .pdf, .docx, .xlsx, .pptx e .csv.

O teste que mais paga é a ida e volta: gerar com a ferramenta e ler de volta com `read_file`. Ele
pega o parser de Markdown, o renderizador daquele formato e o extrator de uma vez só — e falha se
qualquer um dos três quebrar.

Nenhum binário fica versionado: todo arquivo de entrada é produzido pelas próprias ferramentas.
"""
import asyncio
import pathlib
import zipfile

import pytest

from app import config, documentos
from app.tools import ToolError, execute, run_tool


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


def test_planilha_lida_nao_traz_coluna_vazia(ws):
    """max_col enchia a tabela de colunas vazias — dezenas de `|` por linha no contexto do modelo."""
    roda("write_spreadsheet", {"path": "p.xlsx", "sheets": [{"nome": "A", "linhas": [["um", "dois"]]}]}, ws)
    linha = [l for l in run_tool("read_file", {"path": "documentos/p.xlsx"}, ws).splitlines() if "um" in l][0]
    assert linha.count("|") == 3   # borda, separador, borda


def test_formula_aparece_na_leitura_em_vez_de_celula_vazia(ws):
    """Planilha recém-escrita não tem o valor calculado em cache: sem isto a célula saía em branco,
    e o modelo concluiria que a fórmula não foi gravada."""
    abas = [{"nome": "C", "linhas": [["a", "1"], ["b", "2"], ["total", "=SUM(B1:B2)"]]}]
    roda("write_spreadsheet", {"path": "p.xlsx", "sheets": abas}, ws)
    assert "=SUM(B1:B2)" in run_tool("read_file", {"path": "documentos/p.xlsx"}, ws)


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


# ------------------------------------------------ o que quebrou em uso de verdade

@pytest.mark.parametrize("sheets", [
    [[["Produto", "Receita"], ["Cafe", "100"]]],          # aba como lista de linhas, sem envelope
    [{"name": "Vendas", "rows": [["a", "1"]]}],           # chaves em inglês
    [{"nome": "V", "data": [["a", "1"]]}],                # "data" no lugar de "linhas"
    {"nome": "Uma", "linhas": [["a", "1"]]},              # uma aba só, sem lista em volta
    [{"nome": "Y", "linhas": ["texto solto"]}],           # linha que não é lista
])
def test_aceita_as_formas_que_o_modelo_realmente_manda(ws, sheets):
    """O modelo escreve mais de um dialeto, e o primeiro que ele escolheu derrubou a ferramenta
    com AttributeError no preview, antes de qualquer validação."""
    documentos._preview_planilha(ws, {"path": "v.xlsx", "sheets": sheets})   # não pode estourar
    roda("write_spreadsheet", {"path": "v.xlsx", "sheets": sheets}, ws)
    assert (ws / "documentos" / "v.xlsx").is_file()


def test_sheets_que_nao_da_para_entender_vira_erro_legivel(ws):
    with pytest.raises(ToolError, match="sheets vazio"):
        roda("write_spreadsheet", {"path": "v.xlsx", "sheets": []}, ws)
    with pytest.raises(ToolError, match="não é objeto nem lista"):
        roda("write_spreadsheet", {"path": "v.xlsx", "sheets": ["só uma string"]}, ws)


def test_substituir_recusa_markdown_em_vez_de_escrever_os_pipes(ws):
    """Aconteceu em uso: o modelo mandou uma tabela por 'substituir' e ela foi para o documento
    com os `|` à mostra, porque substituir é troca literal."""
    roda("write_document", {"path": "d.docx", "content": "NOME:"}, ws)
    tabela = ("| SEG | TER |" + chr(10) + "| --- | --- |" + chr(10) + "| 01 | 02 |")
    with pytest.raises(ToolError, match="acrescentar"):
        roda("edit_document", {"path": "documentos/d.docx",
                               "operations": [{"tipo": "substituir", "de": "NOME:", "para": tabela}]}, ws)


def test_substituir_texto_simples_continua_passando(ws):
    roda("write_document", {"path": "d.docx", "content": "prazo de 30 dias"}, ws)
    roda("edit_document", {"path": "documentos/d.docx",
                           "operations": [{"tipo": "substituir", "de": "30 dias", "para": "45 dias"}]}, ws)
    assert "45 dias" in run_tool("read_file", {"path": "documentos/d.docx"}, ws)


def test_acrescentar_monta_tabela_de_verdade(ws):
    """O caminho certo para o caso acima: `acrescentar` passa pelo parser."""
    import docx

    roda("write_document", {"path": "d.docx", "content": "# Plano"}, ws)
    tabela = ("| SEG | TER |" + chr(10) + "| --- | --- |" + chr(10) + "| 01 | 02 |")
    roda("edit_document", {"path": "documentos/d.docx",
                           "operations": [{"tipo": "acrescentar", "conteudo": tabela}]}, ws)
    doc = docx.Document(str(ws / "documentos" / "d.docx"))
    assert len(doc.tables) == 1 and doc.tables[0].cell(0, 0).text == "SEG"
    assert "|" not in (doc.paragraphs[-1].text if doc.paragraphs else "")


# ------------------------------------------------ inserir no meio (o pedido que virou write_document)

def _docx_base(ws):
    """Um documento com começo, meio e fim, para dar para provar que o resto sobreviveu."""
    corpo = (chr(10) * 2).join(["# MODELO", "NOME: [INSIRA O NOME AQUI]", "## OBSERVAÇÕES",
                                "Preencher antes de enviar.", "## RODAPÉ", "Documento interno."])
    roda("write_document", {"path": "modelo.docx", "content": corpo}, ws)
    return "documentos/modelo.docx"


def test_inserir_poe_no_lugar_pedido_e_preserva_o_resto(ws):
    """Aconteceu em uso: pedir 'adicione um calendário neste documento' virou write_document, e o
    documento saiu só com o calendário. Faltava poder pôr conteúdo no meio."""
    import docx

    alvo = _docx_base(ws)
    tabela = ("| DOM | SEG |" + chr(10) + "| --- | --- |" + chr(10) + "| 01 | 02 |")
    roda("edit_document", {"path": alvo, "operations": [
        {"tipo": "inserir", "depois": "OBSERVAÇÕES", "conteudo": tabela}]}, ws)

    doc = docx.Document(str(ws / "documentos" / "modelo.docx"))
    assert len(doc.tables) == 1 and doc.tables[0].cell(0, 0).text == "DOM"
    textos = [p.text for p in doc.paragraphs]
    assert "MODELO" in textos[0] and "NOME: [INSIRA O NOME AQUI]" in textos  # o original ficou
    assert "Documento interno." in textos
    # a tabela entrou entre a âncora e o que vinha depois dela, não no fim
    corpo = list(doc.element.body)
    pos_tabela = next(i for i, el in enumerate(corpo) if el.tag.endswith("}tbl"))
    pos_rodape = next(i for i, el in enumerate(corpo) if "RODAPÉ" in (el.xpath("string(.)") or ""))
    assert pos_tabela < pos_rodape


def test_inserir_antes_da_ancora(ws):
    import docx

    alvo = _docx_base(ws)
    roda("edit_document", {"path": alvo, "operations": [
        {"tipo": "inserir", "antes": "RODAPÉ", "conteudo": "Assinatura: ____"}]}, ws)
    textos = [p.text for p in docx.Document(str(ws / "documentos" / "modelo.docx")).paragraphs]
    assert textos.index("Assinatura: ____") < textos.index("RODAPÉ")


def test_inserir_sem_ancora_que_exista_avisa(ws):
    alvo = _docx_base(ws)
    with pytest.raises(ToolError, match="Não achei"):
        roda("edit_document", {"path": alvo, "operations": [
            {"tipo": "inserir", "depois": "SEÇÃO QUE NÃO EXISTE", "conteudo": "x"}]}, ws)


def test_inserir_sem_dizer_onde_manda_usar_acrescentar(ws):
    alvo = _docx_base(ws)
    with pytest.raises(ToolError, match="acrescentar"):
        roda("edit_document", {"path": alvo, "operations": [
            {"tipo": "inserir", "conteudo": "x"}]}, ws)


def test_substituir_com_tabela_agora_aponta_o_inserir(ws):
    """A saída sugerida tem que ser a que resolve: tabela no meio é 'inserir', não 'acrescentar'."""
    alvo = _docx_base(ws)
    tabela = ("| A | B |" + chr(10) + "| --- | --- |" + chr(10) + "| 1 | 2 |")
    with pytest.raises(ToolError, match="inserir"):
        roda("edit_document", {"path": alvo, "operations": [
            {"tipo": "substituir", "de": "OBSERVAÇÕES", "para": tabela}]}, ws)


# ------------------------------------------------ não passar por cima do arquivo do usuário

def test_write_document_recusa_caminho_ja_ocupado(ws):
    """Aconteceu em uso: pedido para acrescentar ao documento anexado, o modelo chamou
    write_document no caminho dele. Só não destruiu porque o Word estava com o arquivo aberto."""
    roda("write_document", {"path": "modelo.docx", "content": "# MODELO" + chr(10) * 2 + "NOME: ____"}, ws)
    with pytest.raises(ToolError, match="edit_document"):
        roda("write_document", {"path": "documentos/modelo.docx", "content": "# so a tabela"}, ws)
    assert "NOME: ____" in run_tool("read_file", {"path": "documentos/modelo.docx"}, ws)


def test_write_spreadsheet_tambem_recusa(ws):
    roda("write_spreadsheet", {"path": "v.xlsx", "sheets": [{"nome": "A", "linhas": [["x"]]}]}, ws)
    with pytest.raises(ToolError, match="edit_spreadsheet"):
        roda("write_spreadsheet", {"path": "documentos/v.xlsx", "sheets": [{"nome": "B", "linhas": [["y"]]}]}, ws)


def test_sobrescrever_explicito_continua_podendo(ws):
    """Refazer do zero é legítimo — desde que seja escolha, não acidente."""
    roda("write_document", {"path": "d.docx", "content": "# antes"}, ws)
    roda("write_document", {"path": "documentos/d.docx", "content": "# depois", "sobrescrever": True}, ws)
    lido = run_tool("read_file", {"path": "documentos/d.docx"}, ws)
    assert "depois" in lido and "antes" not in lido


def test_o_card_de_aprovacao_nao_quebra_com_arquivo_existente(ws):
    """O preview só resolve caminho: se ele levantasse o mesmo erro, sumiria o card em vez de
    aparecer o aviso da ferramenta."""
    roda("write_document", {"path": "d.docx", "content": "# antes"}, ws)
    p = documentos._preview_documento(ws, {"path": "documentos/d.docx", "content": "# depois"})
    assert p["kind"] == "diff" and p["path"] == "documentos/d.docx"


def test_arquivo_aberto_no_word_diz_o_que_fazer(ws, monkeypatch):
    """O Word segura o arquivo e o save estoura PermissionError. O modelo tem que saber que a saída
    é pedir para fechar, e não gerar uma cópia com outro nome — que foi o que ele fez."""
    roda("write_document", {"path": "d.docx", "content": "# oi"}, ws)

    def ocupado(*a, **kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(documentos, "_editar_office", ocupado)
    with pytest.raises(ToolError, match="aberto em outro programa"):
        roda("edit_document", {"path": "documentos/d.docx",
                               "operations": [{"tipo": "acrescentar", "conteudo": "x"}]}, ws)


# ------------------------------------------------ <br> na célula

def test_br_na_celula_vira_quebra_de_linha_no_word(ws):
    """Aconteceu em uso: o modelo pôs <br><br> nas células para dar altura, e os literais foram
    parar no Word como texto."""
    import docx

    tabela = ("| DIA | ATIVIDADE |" + chr(10) + "| --- | --- |" + chr(10) + "| SEG | manha<br>tarde |")
    roda("write_document", {"path": "t.docx", "content": tabela}, ws)
    celula = docx.Document(str(ws / "documentos" / "t.docx")).tables[0].cell(1, 1)
    assert "<br>" not in celula.text
    assert [p.text for p in celula.paragraphs] == ["manha", "tarde"]


def test_celula_com_quebra_volta_como_br_na_leitura(ws):
    """Ida e volta: uma quebra crua dentro da célula arrebentaria a tabela Markdown relida."""
    tabela = ("| A | B |" + chr(10) + "| --- | --- |" + chr(10) + "| um<br>dois | tres |")
    roda("write_document", {"path": "t.docx", "content": tabela}, ws)
    lido = run_tool("read_file", {"path": "documentos/t.docx"}, ws)
    assert "um<br>dois" in lido
    assert len([l for l in lido.splitlines() if "|" in l]) == 3  # cabeçalho, separador, uma linha


def test_br_na_celula_tambem_no_html(ws):
    tabela = ("| A |" + chr(10) + "| --- |" + chr(10) + "| um<br/>dois |")
    roda("write_document", {"path": "t.html", "content": tabela}, ws)
    html = (ws / "documentos" / "t.html").read_text(encoding="utf-8")
    assert "<td>um<br>dois</td>" in html


# ------------------------------------------------ documento de fora, sem os estilos do python-docx

def _sem_estilos(caminho, texto_cabecalho=""):
    """Um .docx como os que o Word produz: sem os estilos que nunca foram usados.

    `Heading 3` e `Table Grid` só existem no documento que os declara. O modelo padrão do
    python-docx traz todos, então gerar e reeditar o que a gente mesmo gerou nunca pega o problema.
    """
    import docx

    doc = docx.Document()
    for estilo in list(doc.styles):
        if estilo.name in ("Heading 3", "Table Grid", "List Bullet", "List Number"):
            estilo.element.getparent().remove(estilo.element)
    doc.add_paragraph("NOME: ____")
    if texto_cabecalho:
        doc.sections[0].header.paragraphs[0].text = texto_cabecalho
    doc.save(str(caminho))


def test_acrescentar_em_documento_sem_os_estilos(ws):
    """Aconteceu em uso, com um formulário de escola: `no style with name 'Heading 3'`, depois
    `'Table Grid'`. O modelo tentou duas vezes, desistiu e gerou por cima do arquivo do usuário."""
    import docx

    _sem_estilos(ws / "externo.docx")
    md = ("### CALENDARIO" + chr(10) * 2 + "| DIA | FEITO |" + chr(10) + "| --- | --- |"
          + chr(10) + "| SEGUNDA | [ ] |")
    roda("edit_document", {"path": "externo.docx", "operations": [
        {"tipo": "acrescentar", "conteudo": md}]}, ws)

    doc = docx.Document(str(ws / "externo.docx"))
    assert "Table Grid" not in [e.name for e in doc.styles]  # o cenário é este; sem isso não prova nada
    assert "NOME: ____" in [p.text for p in doc.paragraphs]     # o original ficou
    assert len(doc.tables) == 1 and doc.tables[0].cell(0, 0).text == "DIA"
    assert any("CALENDARIO" in p.text for p in doc.paragraphs)  # o título entrou, sem o estilo
    # sem o Table Grid, a grade vem das bordas escritas à mão, senão a tabela sai invisível
    with zipfile.ZipFile(ws / "externo.docx") as z:
        assert b"tblBorders" in z.read("word/document.xml")


def test_lista_em_documento_sem_os_estilos(ws):
    import docx

    _sem_estilos(ws / "externo.docx")
    roda("edit_document", {"path": "externo.docx", "operations": [
        {"tipo": "acrescentar", "conteudo": "- um" + chr(10) + "- dois"}]}, ws)
    textos = [p.text for p in docx.Document(str(ws / "externo.docx")).paragraphs]
    assert any("um" in t for t in textos) and any("dois" in t for t in textos)


def test_read_file_mostra_o_cabecalho_do_documento(ws):
    """O formulário tinha prefeitura, escola, professor e turma no cabeçalho. A leitura devolvia só
    a tabela de uma célula do corpo — o agente via um documento quase vazio e se sentiu à vontade
    para substituir tudo."""
    _sem_estilos(ws / "externo.docx", texto_cabecalho="PREFEITURA MUNICIPAL DE EXEMPLO")
    lido = run_tool("read_file", {"path": "externo.docx"}, ws)
    assert "PREFEITURA MUNICIPAL DE EXEMPLO" in lido and "cabeçalho do documento" in lido
    assert "NOME: ____" in lido


def test_anexo_do_usuario_nao_e_sobrescrito_nem_com_o_flag(ws):
    """Ele levou o não duas vezes, leu o 'sobrescrever' no esquema e passou por cima assim mesmo."""
    from app import uploads

    origem = ws / uploads.UPLOAD_DIR
    origem.mkdir(parents=True)
    roda("write_document", {"path": "documentos/base.docx", "content": "# ORIGINAL"}, ws)
    (origem / "anexo.docx").write_bytes((ws / "documentos" / "base.docx").read_bytes())
    antes = (origem / "anexo.docx").read_bytes()

    for args in ({"sobrescrever": True}, {}):
        with pytest.raises(ToolError, match="pasta de anexos"):
            roda("write_document", {"path": f"{uploads.UPLOAD_DIR}/anexo.docx",
                                    "content": "# so a tabela", **args}, ws)
    assert (origem / "anexo.docx").read_bytes() == antes


def test_editar_o_anexo_continua_sendo_o_caminho(ws):
    from app import uploads

    (ws / uploads.UPLOAD_DIR).mkdir(parents=True)
    roda("write_document", {"path": "documentos/base.docx", "content": "# ORIGINAL"}, ws)
    alvo = ws / uploads.UPLOAD_DIR / "anexo.docx"
    alvo.write_bytes((ws / "documentos" / "base.docx").read_bytes())
    roda("edit_document", {"path": f"{uploads.UPLOAD_DIR}/anexo.docx",
                           "operations": [{"tipo": "acrescentar", "conteudo": "## NOVA"}]}, ws)
    lido = run_tool("read_file", {"path": f"{uploads.UPLOAD_DIR}/anexo.docx"}, ws)
    assert "ORIGINAL" in lido and "NOVA" in lido


def test_erro_de_dentro_da_ferramenta_nao_vira_erro_de_argumento(ws, monkeypatch):
    """O KeyError do python-docx chegava como 'Argumentos inválidos', e o modelo passou a
    reescrever o Markdown em vez de entender que o problema não era dele."""
    roda("write_document", {"path": "d.docx", "content": "# oi"}, ws)

    def estilo_faltando(*a, **kw):
        raise KeyError("no style with name 'Heading 3'")

    monkeypatch.setattr(documentos, "_editar_office", estilo_faltando)
    # pelo `execute`, que é por onde o agente chama de verdade: o `roda` daqui pula o embrulho
    with pytest.raises(ToolError) as erro:
        asyncio.run(execute("edit_document", {"path": "documentos/d.docx",
                                              "operations": [{"tipo": "acrescentar", "conteudo": "x"}]}, ws))
    assert "Argumentos inválidos" not in str(erro.value)
    assert "edit_document falhou" in str(erro.value)


def test_o_agente_e_mandado_conferir_o_que_gerou(ws):
    """A validação mais barata não precisa de visão: o arquivo salvo, lido de volta pelo extrator."""
    from app import agent

    prompt = agent.system_prompt("native")
    assert "read_file e confira" in prompt
    assert "write_document gera do zero e não escreve por cima" in prompt

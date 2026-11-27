"""Leitor de edital do Estudos v2 (portado do v1): recortes, junção e quadro de provas, sem modelo."""
import re

from app.estudo import edital as ED

QUADRO_CONTAGEM = """=== 03 - ANEXO III - QUADRO DE PROVAS.pdf ===
ANEXO III - QUADRO DE PROVAS
ENSINO MÉDIO COMPLETO
CÓDIGO/CARGO
LÍNGUA
PORTUGUESA
(Peso 2)
CONHEC.
ESPECIFÍCOS
(Peso 3)
TOTAL DE
QUESTÕES
301 - ASSISTENTE ADMINISTRATIVO
10 20 30 100,0
ENSINO SUPERIOR COMPLETO
CÓDIGO/CARGO
PROVA
DE
TÍTULOS
LÍNGUA
PORTUGUESA
(Peso 2)
RACIOCÍNIO
LÓGICO
(Peso 2)
CONHEC.
GERAIS DO
MUNICÍPIO
(Peso 1)
CONHEC.
ESPECIFÍCOS
(Peso 3)
TOTAL DE
QUESTÕES
TOTAL DE
PONTOS (*)
501 - ANALISTA DE TI
10 05 10
20
45 100,0 30,0 6,5
502 - ARQUITETO 20
503 - ARQUIVISTA 20
"""


def test_trechos_do_edital_longo_pegam_o_quadro_e_o_conteudo_programatico():
    regra = "Das inscrições. O candidato deverá pagar a taxa. " * 3000   # ~150 mil caracteres de regra
    texto = (regra + "\nQUADRO DE PROVAS\nConhecimentos Gerais: Língua Portuguesa 20 questões\nDireito Administrativo 30 questões\n" + regra
             + "\nANEXO II — CONTEÚDO PROGRAMÁTICO\nLÍNGUA PORTUGUESA: 1 Crase. 2 Concordância.\n" + "x " * 100)
    partes = ED.trechos(texto)
    assert any("Direito Administrativo 30" in p for p in partes) and any("1 Crase" in p for p in partes)
    assert len(partes) <= ED.MAX_PEDACOS


def test_juntar_os_pedacos_soma_topicos_e_fica_com_o_numero_de_questoes():
    atual = {}
    ED.juntar(atual, [{"nome": "Língua Portuguesa", "questoes": 20, "topicos": []}])
    ED.juntar(atual, [{"nome": "lingua  portuguesa", "questoes": None, "topicos": ["Crase", "Concordância", "crase"]},
                      {"nome": "Noções de Informática", "questoes": "15", "topicos": ["Windows"]}, {"nome": ""}, "lixo"])
    assert atual["lingua portuguesa"] == {"nome": "Língua Portuguesa", "questoes": 20, "topicos": ["Crase", "Concordância"]}
    assert atual["informatica"]["questoes"] == 15


def test_escolhe_os_anexos_que_dizem_o_que_cai():
    base = "https://banca.example.com/rest/concurso/download/edital/{}/?file=site/anexos/739/{}"
    links = [{"href": base.format(i, n), "texto": "", "linha": f"14/08/2026 {n}"} for i, n in enumerate([
        "01 - ANEXO I - REQUISITOS.pdf", "00 - EDITAL 01-2026.pdf", "03 - ANEXO III - QUADRO DE PROVAS.pdf",
        "04 - ANEXO IV - CONTEUDOS PROGRAMATICOS.pdf", "09 - ANEXO IX - MODELO DE RECURSO.pdf", "RESULTADO PRELIMINAR.pdf",
        "CRONOGRAMA PRELIMINAR.pdf"])] + [{"href": "javascript:void(0)", "texto": "EDITAL", "linha": ""},
                                          {"href": "https://banca.example.com/img/pdf.png", "texto": "", "linha": "Edital"}]
    nomes = [a["nome"] for a in ED.escolher_anexos(links)]
    assert "00 - EDITAL" in nomes[0] and len(nomes) == 3
    assert any("QUADRO" in n for n in nomes) and any("CONTEUDOS" in n for n in nomes)
    assert not any(re.search("RECURSO|RESULTADO|CRONOGRAMA|REQUISITOS", n) for n in nomes)


def test_recorte_pelo_cargo_num_edital_de_muitos_cargos():
    outros = "".join(f"\n{500 + i} - CARGO NUMERO {i}\nAssunto exclusivo do cargo {i}. " * 1 + "x " * 3000 for i in range(2, 9))
    texto = ("=== Página do concurso ===\nData da Prova: 24/01/27\n\n=== 00 - EDITAL.pdf ===\n"
             + "O quadro de provas está no Anexo III. Analista de TI é cargo de nível superior. " + "regra " * 20000
             + "\n\n=== 03 - ANEXO III - QUADRO DE PROVAS.pdf ===\nANEXO III - QUADRO DE PROVAS\nLÍNGUA PORTUGUESA (Peso 2)\n"
             + "501 - ANALISTA DE TI 10 05 10 20\n502 - ARQUITETO 10 05 10 20\n"
             + "\n\n=== 04 - ANEXO IV - CONTEUDOS PROGRAMATICOS.pdf ===\nANEXO IV - CONTEÚDOS PROGRAMÁTICOS\n"
             + "ENSINO MÉDIO\nLÍNGUA PORTUGUESA\nCrase do médio.\n" + "y " * 3000
             + "\nENSINO SUPERIOR\nLÍNGUA PORTUGUESA\nVariação linguística do superior.\n"
             + "CONHECIMENTOS ESPECÍFICOS\n501 - ANALISTA DE TI\nRedes de computadores. Banco de dados. LGPD.\n"
             + "CONHECIMENTOS ESPECÍFICOS" + outros)
    partes = ED.trechos(texto, "analista de ti")
    junto = "\n".join(partes)
    assert partes[0].startswith("=== Página do concurso") and "24/01/27" in partes[0]
    assert "501 - ANALISTA DE TI 10 05 10 20" in junto            # o quadro de verdade, não a menção no edital
    assert "Variação linguística do superior" in junto and "Crase do médio" not in junto   # a parte comum do nível
    assert "Banco de dados" in junto and "exclusivo do cargo 3" not in junto              # o específico, até o próximo cargo


def test_quadro_de_provas_lido_sem_modelo_e_corrige_o_peso():
    ti = ED.quadro_do_cargo(QUADRO_CONTAGEM, "Analista de TI")
    assert [(c["questoes"], c["peso"]) for c in ti] == [(10, 2), (5, 2), (10, 1), (20, 3)]
    # célula mesclada: o Arquiteto só tem a coluna dele; as comuns vêm da 1ª linha do bloco
    assert [c["questoes"] for c in ED.quadro_do_cargo(QUADRO_CONTAGEM, "arquiteto")] == [10, 5, 10, 20]
    assert [c["questoes"] for c in ED.quadro_do_cargo(QUADRO_CONTAGEM, "Assistente Administrativo")] == [10, 20]
    assert ED.quadro_do_cargo(QUADRO_CONTAGEM, "Cargo que não existe") == []
    achadas = {k: {"nome": n, "questoes": q, "topicos": []} for k, n, q in [
        ("a", "Língua Portuguesa", None), ("b", "Raciocínio Lógico", 3), ("c", "Conhecimentos Gerais do Município", None),
        ("d", "Conhecimentos Específicos", 1)]}
    ED.corrigir_pelo_quadro(achadas, ti)
    assert {v["nome"]: v["questoes"] for v in achadas.values()} == {
        "Língua Portuguesa": 20, "Raciocínio Lógico": 10, "Conhecimentos Gerais do Município": 10, "Conhecimentos Específicos": 60}

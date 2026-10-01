import asyncio
import json
import re

from fastapi.testclient import TestClient

from app import db, estudos, estudos_edital as ED, estudos_revisao, pesquisa
from tests.test_estudos import _estudo, pastas  # noqa: F401  (pastas: fixture autouse)


def _meta(mid):
    with db.session() as s:
        m = s.get(db.Message, mid)
        return m.conversation_id, m.meta["estudos"]


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


def test_ler_edital_propoe_e_aplicar_cria_e_atualiza(monkeypatch):
    conv = _estudo()
    estudos.materias(conv)
    port = estudos.nova_materia(conv, "Língua Portuguesa")["id"]
    respostas = iter([{"materias": [{"nome": "Língua Portuguesa", "questoes": 20, "topicos": ["Crase"]},
                                    {"nome": "Direito Administrativo", "questoes": 30, "topicos": ["Atos", "Licitações"]}]}])

    async def perguntar(spec, system, user, run=None, effort="baixo"):
        assert system.startswith("Você lê um trecho de EDITAL") and "Cargo pedido: Analista" in user
        return json.dumps(next(respostas, {"materias": []}))
    monkeypatch.setattr(pesquisa, "_perguntar", perguntar)

    async def main():
        msg = ED.start(conv, "edital " * 100, "Analista", "fake", "m")
        while estudos._TAREFAS:
            await asyncio.gather(*list(estudos._TAREFAS))
        return msg["id"]
    mid = asyncio.run(main())
    _, e = _meta(mid)
    assert e["status"] == "pronto" and "materia" not in e   # do objetivo inteiro
    prop = {x["nome"]: x for x in e["proposta"]}
    assert prop["Língua Portuguesa"]["existe"] == port and prop["Direito Administrativo"]["existe"] is None
    assert prop["Direito Administrativo"]["peso"] == 30
    lista = ED.aplicar(conv, e["proposta"])
    por = {x["nome"]: x for x in lista}
    assert por["Língua Portuguesa"]["id"] == port and por["Língua Portuguesa"]["peso"] == 20
    assert por["Direito Administrativo"]["topicos"] == ["Atos", "Licitações"]
    assert estudos.projeto(conv)["edital"]["message_id"] == mid


def test_juntar_traz_o_estudo_antigo_com_material_revisao_e_apaga_a_conversa(monkeypatch):
    from app.main import app
    with TestClient(app) as c:
        aqui = c.post("/api/conversations", json={"kind": "estudos"}).json()["id"]
        la = c.post("/api/conversations", json={"kind": "estudos"}).json()["id"]
        port = c.post(f"/api/estudos/{aqui}/materias", json={"nome": "Português"}).json()["id"]
        c.post(f"/api/estudos/{aqui}/material/texto", json={"nome": "crase.md", "texto": "crase " * 50},
               headers={"x-forja-materia": port})
        # o de lá é um estudo de antes das matérias: material, resumo, entrega e o estado da revisão
        with db.session() as s:
            s.get(db.Conversation, la).title = "Biologia"
            s.commit()
        mat = estudos._salvar(la, role="event", content="aula.md", meta={"estudos": {
            "tipo": "material", "nome": "aula.md", "uso": "conteudo", "n": 1, "arquivo": "01-aula.md", "ocr": False, "figuras": []}})
        pasta = estudos.pasta(la) / "material"
        pasta.mkdir(parents=True)
        (pasta / "01-aula.md").write_text("mitocôndria", encoding="utf-8")
        (pasta / "01.txt").write_text("mitocôndria", encoding="utf-8")
        (pasta / "01-figuras").mkdir()
        (pasta / "01-figuras" / "p01-0.png").write_bytes(b"png")
        resumo = estudos._salvar(la, role="assistant", content="# Bio", status="pronto",
                                 meta={"estudos": {"tipo": "resumo", "tema": "Bio"}})
        estudos._salvar(la, role="user", content="Revisão", status="pronto",
                        meta={"estudos": {"tipo": "revisao", "itens": {"f:c1": {"caixa": 3}}, "plano": None}})
        r = c.post(f"/api/estudos/{aqui}/juntar", json={"de": la}).json()
        assert [x["nome"] for x in r["materias"]] == ["Português", "Biologia"]
        bio = r["materias"][1]["id"]
        conv_, e = _meta(mat.id)
        assert conv_ == aqui and e["materia"] == bio and e["n"] == 2 and e["arquivo"] == "02-aula.md"
        dest = estudos.pasta(aqui) / "material"
        assert (dest / "02.txt").read_text(encoding="utf-8") == "mitocôndria" and (dest / "02-figuras" / "p01-0.png").exists()
        assert _meta(resumo.id) == (aqui, {**_meta(resumo.id)[1], "materia": bio})
        h = {"x-forja-materia": bio}
        p = c.get(f"/api/estudos/{aqui}", headers=h).json()
        assert [m["nome"] for m in p["materiais"]] == ["aula.md"] and len(p["resumos"]) == 1
        assert c.get(f"/api/conversations/{la}").status_code == 404
        assert c.post(f"/api/estudos/{aqui}/juntar", json={"de": aqui}).status_code == 400
        with db.session() as s:
            revs = [m for m in s.query(db.Message).filter_by(conversation_id=aqui) if estudos._tipo(m) == "revisao"]
        assert len(revs) == 1 and revs[0].meta["estudos"]["itens"]["f:c1"]["caixa"] == 3


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


def test_plano_de_estudos_sai_dos_topicos_do_edital_sem_resumo():
    conv = _estudo()
    estudos.materias(conv)
    lista = ED.aplicar(conv, [{"nome": "Língua Portuguesa", "peso": 20, "topicos": ["Crase", "Concordância"]},
                              {"nome": "Conhecimentos Específicos", "peso": 60, "topicos": ["Redes", "Banco de dados", "LGPD"]}],
                       cronograma={"data": "2099-01-31", "minutos": 90})
    assert [m["nome"] for m in lista] == ["Língua Portuguesa", "Conhecimentos Específicos"]
    plano = estudos_revisao._ler(conv)["plano"]
    estudar = [t["topico"] for d in plano["dias"] for t in d["tarefas"] if t["tipo"] == "estudar"]
    assert estudar and all(" · " in t for t in estudar)
    por = {m: sum(t.startswith(m) for t in estudar) for m in ("Língua Portuguesa", "Conhecimentos Específicos")}
    assert por["Conhecimentos Específicos"] > 2 * por["Língua Portuguesa"] > 0
    # intercalado: nenhuma sequência de 5 dias seguidos da mesma matéria
    materias = [t.split(" · ")[0] for t in estudar]
    assert not any(len(set(materias[i:i + 5])) == 1 for i in range(len(materias) - 5))

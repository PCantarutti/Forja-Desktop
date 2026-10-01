import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import config, db, estudos, estudos_figuras as F, estudos_prova as P, mirror, pesquisa


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(estudos, "RAIZ", tmp_path / "estudos")
    monkeypatch.setattr(config, "SUBAGENTS", {})
    estudos._RUNS.clear()
    yield
    estudos._RUNS.clear()


def _pdf(paginas: list[str], mediabox="0 0 595 842", form: str = "") -> bytes:
    """PDF mínimo, uma página por content stream (operadores de desenho e texto em Helvetica). `form`: o
    conteúdo de uma Form XObject /X1 (BBox 0 0 300 300), que a página desenha com "/X1 Do"."""
    fdados = form.encode("latin-1")
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Type /XObject /Subtype /Form /BBox [0 0 300 300] /Length {len(fdados)} >>\nstream\n{form}\nendstream"]
    kids = []
    for conteudo in paginas:
        dados = conteudo.encode("latin-1")
        objs.append(f"<< /Length {len(dados)} >>\nstream\n{conteudo}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [{mediabox}] /Resources << /Font << /F1 3 0 R >> "
                    f"/XObject << /X1 4 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def _texto(y0: int, linhas: int, x=60) -> str:
    return "".join(f"BT /F1 11 Tf {x} {y0 - 16 * i} Td (Linha de texto da questao sobre glicolise numero {i}) Tj ET\n"
                   for i in range(linhas))


GRAFICO = ("1 w 300 400 m 300 600 l 520 600 l S\n"            # eixos (um traço em L)
           "300 400 m 360 560 l 420 470 l 500 590 l S\n"       # a curva
           "BT /F1 9 Tf 470 380 Td (tempo) Tj ET\n")
BOLINHAS = "".join(f"{50 + 5} {700 - 22 * i} m {50 + 10} {700 - 22 * i} 5 5 re f\n" for i in range(5))  # A–E
REGUA = "0.5 w 40 300 m 560 300 l S\n"


def _estudo_com_pdf(paginas=None) -> tuple[int, dict]:
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="Biologia")
        s.add(c)
        s.commit()
        conv = c.id
    paginas = paginas or [_texto(780, 30) + BOLINHAS + REGUA + GRAFICO, _texto(780, 20)]
    m = estudos.adicionar_material(conv, "apostila.pdf", _pdf(paginas))
    return conv, m


def test_detecta_o_desenho_vetorial_e_ignora_bolinhas_e_regua():
    conv, m = _estudo_com_pdf()
    figs = m["figuras"]
    assert [f["id"] for f in figs] == ["p01-0"]                     # só o gráfico; a página 2 é texto puro
    f = figs[0]
    assert f["pagina"] == 1 and 400 < f["w"] < 560 and 380 < f["h"] < 520   # ~220 x 200 pt a 2x, com o rótulo
    assert (F.pasta(conv, m["n"]) / "p01-0.png").is_file()
    assert estudos.projeto(conv)["materiais"][0]["figuras"] == 1   # a tela recebe a contagem, não a lista


def _detecta(tmp_path, paginas, **kw):
    arq = tmp_path / "t.pdf"
    arq.write_bytes(_pdf(paginas, **kw))
    return F.detectar(arq, tmp_path / "figs")


QUADRO = "1 w 0 0 m 0 150 l 180 150 l S 0 0 m 120 110 l 180 20 l S\n"   # 180 x 150 pt, desenhado na origem


def test_desenho_fora_da_pagina_nao_quebra_nada(tmp_path):
    """Sangria, marca de corte, objeto escondido fora da área: não vira figura e não derruba o upload."""
    assert _detecta(tmp_path, [f"q 1 0 0 1 700 300 cm {QUADRO} Q", f"q 1 0 0 1 300 -400 cm {QUADRO} Q"]) == []
    figs = _detecta(tmp_path, [f"q 1 0 0 1 480 300 cm {QUADRO} Q"])   # metade para fora: só a parte visível
    assert len(figs) == 1 and figs[0]["w"] < 250 * F.ESCALA


def test_figura_dentro_de_form_xobject_sai_no_lugar_certo(tmp_path):
    """LaTeX \\includegraphics, PDF reimpresso: o desenho mora numa form; o recorte tem de pegar o desenho."""
    from PIL import Image
    figs = _detecta(tmp_path, ["q 2 0 0 2 100 300 cm /X1 Do Q"], form=QUADRO)
    assert len(figs) == 1
    assert abs(figs[0]["w"] - 360 * F.ESCALA) < 40 and abs(figs[0]["h"] - 300 * F.ESCALA) < 40   # escala 2 da form
    escuro = sum(1 for px in Image.open(tmp_path / "figs" / "p01-0.png").convert("L").getdata() if px < 128)
    assert escuro > 200   # recortou o traço, não um pedaço em branco da página


def test_mediabox_que_nao_comeca_em_zero(tmp_path):
    from PIL import Image
    figs = _detecta(tmp_path, [f"q 1 0 0 1 550 600 cm {QUADRO} Q"], mediabox="500 500 900 900")
    assert len(figs) == 1 and abs(figs[0]["w"] - 180 * F.ESCALA) < 30
    escuro = sum(1 for px in Image.open(tmp_path / "figs" / "p01-0.png").convert("L").getdata() if px < 128)
    assert escuro > 200


def test_material_de_ocr_nao_recorta(monkeypatch):
    assert F.deve_recortar("x.pdf", False) and not F.deve_recortar("x.pdf", True) and not F.deve_recortar("x.docx", False)


def test_rota_da_figura_confere_o_estudo_e_o_id():
    TOKEN = getattr(config, "API_TOKEN", "")   # o web não tem token
    from app.main import app
    conv, m = _estudo_com_pdf()
    with TestClient(app) as c:
        h = {"x-forja-token": TOKEN} if TOKEN else {}
        r = c.get(f"/api/estudos-figura/{conv}/{m['id']}/p01-0", headers=h)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
        assert c.get(f"/api/estudos-figura/{conv}/{m['id']}/..%2F01.txt", headers=h).status_code == 404
        assert c.get(f"/api/estudos-figura/{conv + 99}/{m['id']}/p01-0", headers=h).status_code == 404
        if TOKEN:   # é um <img>: o token vale pelo ?t=
            assert c.get(f"/api/estudos-figura/{conv}/{m['id']}/p01-0?t={TOKEN}").status_code == 200


def test_remover_material_leva_as_figuras():
    conv, m = _estudo_com_pdf()
    pasta = F.pasta(conv, m["n"])
    assert pasta.is_dir()
    estudos.remover_material(m["id"])
    assert not pasta.exists()


def test_material_antigo_ganha_figuras_quando_preciso():
    conv, m = _estudo_com_pdf()
    with db.session() as s:   # como estava antes desta função: sem a chave "figuras"
        msg = s.get(db.Message, m["id"])
        e = dict(msg.meta["estudos"])
        e.pop("figuras")
        msg.meta = {**msg.meta, "estudos": e}
        s.commit()
    assert "figuras" not in estudos.materiais(conv)[0]
    assert [f["id"] for f in F.garantir(conv)[0]["figuras"]] == ["p01-0"]
    assert estudos.materiais(conv)[0]["figuras"][0]["id"] == "p01-0"


# ------------------------------------------------------------------ prova com figura (modelo falso)


CHAMADAS: list[dict] = []


def _fake(monkeypatch, enxerga=True, util=True):
    CHAMADAS.clear()

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        imagens = [p for p in user if p.get("type") == "image_url"] if isinstance(user, list) else []
        texto_user = "\n".join(p["text"] for p in user if p.get("type") == "text") if isinstance(user, list) else user
        CHAMADAS.append({"system": system, "user": texto_user, "imagens": len(imagens), "model": model})
        if system.startswith("Você olha figuras"):
            n = len(imagens)
            out = {"figuras": [{"n": i + 1, "tipo": "grafico", "util": util, "assunto": "biologia: glicólise",
                                "descricao": "curva da glicólise no tempo"} for i in range(n)]}
        elif system.startswith("Você é um professor que elabora"):
            pedidas = re.findall(r"^\d+\. (me|vf|disc) ", texto_user, re.M)
            out = {"questoes": [{"tipo": "me", "enunciado": f"Observe a figura {len(CHAMADAS)}-{i}: em que ponto "
                                 f"a curva {'abcdefgh'[len(CHAMADAS) % 8]}{i}xyz{len(CHAMADAS)} sobe mais?",
                                 "alternativas": [f"ponto {len(CHAMADAS)}-{i}-{k}" for k in range(4)], "correta": 1,
                                 "explicacao": "Pela figura.", "por_alternativa": ["a", "b", "c", "d"]}
                                for i, _ in enumerate(pedidas)]}
        elif system.startswith("Você resolve questões"):
            ids = re.findall(r"^\[(q\d+)\]", texto_user, re.M)
            certas = {}
            for qid, bloco in zip(ids, re.split(r"^\[q\d+\]", texto_user, flags=re.M)[1:]):
                achou = re.search(r"^([A-E])\) ponto \d+-\d+-1$", bloco, re.M)
                certas[qid] = achou.group(1) if achou else "A"
            out = {"respostas": [{"id": i, "resposta": r} for i, r in certas.items()]}
        else:
            out = {}
        yield ("content", json.dumps(out, ensure_ascii=False))
        yield ("done", {"prompt_tokens": 10, "completion_tokens": 20})

    async def ve(spec):
        return enxerga
    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)
    monkeypatch.setattr(F, "enxerga", ve)


def _resumo(conv):
    topicos = [{"titulo": "Glicólise", "objetivo": "", "pontos": [], "status": "pronto"}]
    estudos._save(conv, role="assistant", content="# Bio\n\n## 1. Glicólise\n\nSaldo de 2 ATP.\n", status="pronto",
                  meta={"estudos": {"tipo": "resumo", "tema": "Biologia", "topicos": topicos, "perfil": {"alternativas": 4}}})


def _gerar(conv, cfg):
    async def main():
        msg = P.start(conv, cfg, "fake", "m")
        while estudos._TAREFAS:
            await asyncio.gather(*list(estudos._TAREFAS))
        return msg["id"]
    pid = asyncio.run(main())
    with db.session() as s:
        return s.get(db.Message, pid).meta["estudos"]


def test_prova_com_figura_manda_a_imagem_e_guarda_na_questao(monkeypatch):
    _fake(monkeypatch)
    conv, m = _estudo_com_pdf()
    _resumo(conv)
    e = _gerar(conv, {"me": 3, "figuras": 1})
    assert e["status"] == "pronto", e.get("aviso")
    com = [q for q in e["questoes"] if q.get("figura")]
    assert len(com) == 1 and com[0]["figura"]["id"] == "p01-0" and com[0]["figura"]["material"] == m["id"]
    assert com[0]["figura"]["descricao"] == "curva da glicólise no tempo"
    olhar = [c for c in CHAMADAS if c["system"].startswith("Você olha figuras")]
    gerar_fig = [c for c in CHAMADAS if c["system"].startswith("Você é um professor") and c["imagens"]]
    conferir_fig = [c for c in CHAMADAS if c["system"].startswith("Você resolve") and c["imagens"]]
    assert len(olhar) == 1 and olhar[0]["imagens"] == 1
    assert len(gerar_fig) == 1 and "FIGURA anexada" in gerar_fig[0]["system"] and "Linha de texto" in gerar_fig[0]["user"]
    assert len(conferir_fig) == 1 and "(usa a figura anexada)" in conferir_fig[0]["user"]
    # as outras duas não levaram imagem nem a regra da figura
    assert all("FIGURA anexada" not in c["system"] for c in CHAMADAS if c["system"].startswith("Você é um professor") and not c["imagens"])
    # a descrição fica guardada: a próxima prova não olha de novo
    CHAMADAS.clear()
    e2 = _gerar(conv, {"me": 1, "figuras": 1})
    assert not [c for c in CHAMADAS if c["system"].startswith("Você olha figuras")]
    assert e2["questoes"][0]["figura"]["id"] == "p01-0"   # a única figura útil volta, já usada, na falta de outra


def test_sem_visao_ou_sem_figura_util_a_questao_sai_comum(monkeypatch):
    _fake(monkeypatch, enxerga=False)
    conv, _ = _estudo_com_pdf()
    _resumo(conv)
    e = _gerar(conv, {"me": 2, "figuras": 2})
    assert e["status"] == "pronto" and not any(q.get("figura") for q in e["questoes"])
    assert "não enxerga" in e["aviso"]
    assert not any(c["imagens"] for c in CHAMADAS)

    _fake(monkeypatch, util=False)
    e = _gerar(conv, {"me": 2, "figuras": 1})
    assert e["status"] == "pronto" and not any(q.get("figura") for q in e["questoes"])
    assert "saíram sem figura" in e["aviso"]


def test_claude_grava_questao_com_figura(monkeypatch):
    conv, m = _estudo_com_pdf()
    _resumo(conv)
    q = {"tipo": "me", "enunciado": "Observe a figura: em que trecho a curva sobe mais?",
         "alternativas": ["no início", "no meio", "no fim", "em nenhum"], "correta": 0, "explicacao": "Pela figura.",
         "por_alternativa": ["a", "b", "c", "d"], "topico": "Glicólise", "figura": f"{m['id']}:p01-0"}
    assert "ERRO" in P.mcp_salvar_prova([{**q, "figura": f"{m['id']}:p09-9"}], conv_id=conv)
    assert "ERRO" in P.mcp_salvar_prova([{**q, "figura": "lixo"}], conv_id=conv)
    assert "gravada" in P.mcp_salvar_prova([q], conv_id=conv)
    prova = P.lista(conv)[-1]
    with db.session() as s:
        guardada = s.get(db.Message, prova["message_id"]).meta["estudos"]["questoes"][0]
    assert guardada["figura"]["id"] == "p01-0" and guardada["figura"]["pagina"] == 1
    # o Claude acha a figura no texto do material e consegue olhar
    assert f"{m['id']}:p01-0" in estudos.mcp_ler_material(m["id"])
    legenda, png = F.mcp_ver(conv, f"{m['id']}:p01-0")
    assert "página 1" in legenda and png[:4] == b"\x89PNG"


def test_escolher_prefere_paginas_diferentes_e_figuras_nao_usadas():
    def fig(fid, pagina, assunto="glicólise"):
        return {"id": fid, "pagina": pagina, "util": True, "assunto": assunto, "descricao": ""}
    mats = [{"id": 7, "figuras": [fig("p10-0", 10), fig("p10-1", 10), fig("p12-0", 12), fig("p03-0", 3, "física"),
                                  {**fig("p05-0", 5), "util": False}]}]
    ids = [f["id"] for f in F.escolher(mats, ["Glicólise"], 3, set())]
    assert ids[:2] == ["p10-0", "p12-0"] and "p05-0" not in ids and len(ids) == 3   # a 2ª da p. 10 só depois
    assert F.escolher(mats, ["Glicólise"], 1, {(7, "p10-0")})[0]["id"] == "p10-1"   # a já usada fica para o fim


def test_tutor_le_a_descricao_da_figura():
    from app import estudos_duvidas as D
    linhas = D._linha_figura({"figura": {"material": 1, "id": "p01-0", "pagina": 3, "descricao": "gráfico v x t"}})
    assert linhas == ["A questão tem uma figura (página 3 do material): gráfico v x t."]
    assert D._linha_figura({"enunciado": "sem figura"}) == []

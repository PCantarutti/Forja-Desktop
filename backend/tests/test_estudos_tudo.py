from collections import Counter

from app import db, estudos, estudos_prova as P, estudos_revisao as R
from tests.test_estudos_prova import _correto_do_prompt, _fake, _gerar, pastas  # noqa: F401  (pastas: fixture autouse)


def _na(materia, f, *a, **kw):
    marca = estudos.MATERIA.set(materia)
    try:
        return f(*a, **kw)
    finally:
        estudos.MATERIA.reset(marca)


def _resumo(conv, materia, tema, topicos, quadro=""):
    md = f"# {tema}\n\n" + "".join(f"## {i}. {t}\n\nTexto de {t} [p. 1].\n\n" for i, t in enumerate(topicos, 1))
    md += f"## Revisão rápida\n\n{quadro}\n" if quadro else ""
    _na(materia, estudos._save, conv, role="assistant", content=md, status="pronto",
        meta={"estudos": {"tipo": "resumo", "tema": tema, "perfil": {"alternativas": 4},
                          "topicos": [{"titulo": t, "objetivo": "", "pontos": [], "status": "pronto"} for t in topicos]}})


def _objetivo():
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="Concurso TRT")
        s.add(c)
        s.commit()
        conv = c.id
    estudos.materias(conv)
    bio = estudos.nova_materia(conv, "Biologia", 1)["id"]
    qui = estudos.nova_materia(conv, "Química", 3)["id"]
    _resumo(conv, bio, "Biologia", ["Glicólise", "Fermentação"], "| Conceito | Lembrar |\n|---|---|\n| ATP | energia |")
    _resumo(conv, qui, "Química", ["Mol", "Estequiometria"])
    return conv, bio, qui


def test_simulado_geral_reparte_pelo_peso_e_cada_questao_sabe_a_materia(monkeypatch):
    conv, bio, qui = _objetivo()
    _fake(monkeypatch, verificar=_correto_do_prompt)
    pid = _gerar(conv, {"me": 8, "geral": True})
    with db.session() as s:
        prova = s.get(db.Message, pid).meta["estudos"]
    assert prova["titulo"] == "Simulado geral 1" and prova["materia"] == "" and prova["config"]["geral"]
    assert Counter(q["materia"] for q in prova["questoes"]) == {bio: 2, qui: 6}   # pesos 1 e 3
    assert all(q["topico"].startswith(("Biologia · ", "Química · ")) for q in prova["questoes"])


def test_entrega_do_geral_sai_por_materia_no_acerto_no_caderno_e_no_desempenho(monkeypatch):
    conv, bio, qui = _objetivo()
    _fake(monkeypatch, verificar=_correto_do_prompt)
    pid = _gerar(conv, {"me": 8, "geral": True})
    with db.session() as s:
        qs = s.get(db.Message, pid).meta["estudos"]["questoes"]
    # acerta as de Biologia, erra as de Química
    resp = {q["id"]: "ABCD"[q["correta"] if q["materia"] == bio else (q["correta"] + 1) % 4] for q in qs}
    t = P.entregar(pid, resp, 60)
    with db.session() as s:
        e = s.get(db.Message, t["id"]).meta["estudos"]
    pm = {x["materia"]: x for x in e["por_materia"]}
    assert pm[bio]["acertos"] == 2 and pm[qui]["acertos"] == 0
    acertos = estudos._acerto_por_materia(conv)
    assert acertos[bio]["acerto"] == 100 and acertos[qui]["acerto"] == 0
    assert len(_na(qui, R.erros, conv)) == 6 and _na(bio, R.erros, conv) == []
    assert len(_na(None, R.erros, conv)) == 6
    tops = {x["topico"]: x for x in _na(qui, R.desempenho, conv)["topicos"]}
    assert tops["Mol"]["max"] > 0 and tops["Mol"]["pct"] == 0   # o tópico de volta ao nome da matéria
    tudo = {x["topico"] for x in _na(None, R.desempenho, conv)["topicos"]}
    assert {"Biologia · Glicólise", "Química · Mol"} <= tudo


def test_visao_geral_mostra_cada_materia_o_quadro_e_a_mais_fraca(monkeypatch):
    conv, bio, qui = _objetivo()
    _fake(monkeypatch, verificar=_correto_do_prompt)
    pid = _gerar(conv, {"me": 8, "geral": True})
    with db.session() as s:
        qs = s.get(db.Message, pid).meta["estudos"]["questoes"]
    P.entregar(pid, {q["id"]: "ABCD"[q["correta"] if q["materia"] == bio else (q["correta"] + 1) % 4] for q in qs}, 60)
    v = _na(None, estudos.visao, conv)
    por = {x["id"]: x for x in v["materias"]}
    assert por[bio]["peso"] == 1 and por[qui]["peso"] == 3 and por[qui]["erros"] == 6
    assert "| ATP | energia |" in por[bio]["quadro"] and por[qui]["quadro"] == ""
    assert v["fraca"] == qui and v["acerto"] is not None
    assert [r["titulo"] for r in por[bio]["resumos"]] == ["Biologia"]
    # cronograma do objetivo: os tópicos das duas matérias, Química (peso 3, acerto 0) aparece mais
    plano = _na(None, R.planejar, conv, "2099-01-01", 60)
    estudar = Counter(x["topico"].split(" · ")[0] for d in plano["dias"] for x in d["tarefas"] if x["tipo"] == "estudar")
    assert estudar["Química"] > 2 * estudar["Biologia"] > 0


def test_peso_da_materia_e_simulado_geral_sem_materia_pronta():
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="Vazio")
        s.add(c)
        s.commit()
        conv = c.id
    estudos.materias(conv)
    m = estudos.nova_materia(conv, "Português")["id"]
    assert estudos.alterar_materia(conv, m, peso=250)["peso"] == 100
    try:
        P._contexto_geral(conv)
        raise AssertionError("devia recusar")
    except Exception as e:
        assert "resumo" in str(e)

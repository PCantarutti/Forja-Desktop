"""Autorrevisão: depois das etapas, a página pronta é aberta num Chromium, o modelo aponta o que está
visivelmente errado e as seções apontadas são reescritas uma vez (como o Agente faz com screenshots)."""
import json
import re

from app import design
from tests.test_design_fase3 import M, PLANO, _esperar, _fake, _projeto, pastas  # noqa: F401


def _secao(nome: str, conserto: bool = False) -> str:
    miolo = "<h1>Logo numa linha</h1>" if conserto else "<h1>Logo quebrada</h1>"
    return f'<section data-section="{nome}">{miolo}</section><style>[data-section="{nome}"]{{padding:2rem}}</style>'


def test_pagina_pronta_e_revisada_e_a_secao_apontada_reescrita(monkeypatch):
    conv, chamadas = _projeto(None), []

    def responder(tipo, user):
        if tipo == "plano":
            return json.dumps(PLANO)
        if tipo == "autorrevisao":
            assert "Geometria medida no navegador" in (user if isinstance(user, str) else user[0]["text"])
            return json.dumps({"corrigir": [{"secao": "hero", "problema": "A logo quebra em 2 linhas.",
                                             "correcao": "white-space:nowrap na logo."},
                                            {"secao": "inexistente", "correcao": "x"}]})
        texto = user if isinstance(user, str) else user[0]["text"]
        nome = re.search(r'data-section="(\w+)"', texto).group(1)
        return _secao(nome, conserto="Revisão visual da página pronta" in texto)

    _fake(monkeypatch, responder, chamadas)
    msg = _esperar(lambda: design.start(conv, "landing de padaria", M))
    plano = design.estado(msg["id"])["plano"]
    _esperar(lambda: design.aprovar(msg["id"], plano, M))
    tipos = [c[0] for c in chamadas]
    assert tipos == ["plano", "secao", "secao", "secao", "autorrevisao", "secao"]   # só o hero de novo (o inexistente cai fora)
    p = design.projeto(conv)
    assert "Logo numa linha" in p["html"] and p["html"].count("Logo quebrada") == 2   # sobre e rodapé ficaram
    passos = p["mensagens"][-1]["passos"]
    assert any("corrigiu “hero”" in x for x in passos)


def test_critica_ilegivel_nao_estraga_a_pagina(monkeypatch):
    conv, chamadas = _projeto(None), []

    def responder(tipo, user):
        if tipo == "plano":
            return json.dumps(PLANO)
        if tipo == "autorrevisao":
            return "não sei"
        texto = user if isinstance(user, str) else user[0]["text"]
        return _secao(re.search(r'data-section="(\w+)"', texto).group(1))

    _fake(monkeypatch, responder, chamadas)
    msg = _esperar(lambda: design.start(conv, "landing de padaria", M))
    _esperar(lambda: design.aprovar(msg["id"], design.estado(msg["id"])["plano"], M))
    p = design.projeto(conv)
    assert p["total"] == 1 and p["html"].count("Logo quebrada") == 3
    assert any("crítica não veio legível" in x for x in p["mensagens"][-1]["passos"])

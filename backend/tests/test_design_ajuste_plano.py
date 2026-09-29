"""Mensagem nova antes de existir documento ajusta o plano pendente em vez de começar do zero
(o bug: a sugestão "adicionar carrossel de logos" virou um plano novo só com ela, sem o pedido original)."""
import json

from app import design
from tests.test_design_conversa import M, _esperar, _fake, _projeto, _ultima, pastas  # noqa: F401

PLANO = {"tipo": "site", "titulo": "Nova", "tokens": {"--cor-primaria": "#2255dd"},
         "secoes": [{"nome": n, "objetivo": "", "conteudo": ""} for n in ("hero", "beneficios", "faq", "rodape")]}


def test_mensagem_com_plano_pendente_ajusta_o_plano(monkeypatch):
    conv, vistos = _projeto(html=None), []
    _fake(monkeypatch, lambda tipo, user: json.dumps(PLANO), vistos)
    _esperar(lambda: design.start(conv, "landing com hero, benefícios, FAQ e rodapé", M, perguntar=False))
    assert _ultima(conv)["status"] == "plano"
    # o usuário manda outra frase em vez de aprovar; com "perguntar antes" ligado não pode perguntar de novo
    _esperar(lambda: design.start(conv, "adicionar um carrossel de logos", M, perguntar=True))
    tipo, user = vistos[-1]
    assert tipo == "plano"
    assert "Pedido original: landing com hero, benefícios, FAQ e rodapé" in user
    assert '"beneficios"' in user and "Plano atual" in user
    assert "Ajuste pedido agora: adicionar um carrossel de logos" in user


def test_resposta_as_perguntas_nao_vira_ajuste(monkeypatch):
    conv, vistos = _projeto(html=None), []
    _fake(monkeypatch, lambda tipo, user: json.dumps(PLANO), vistos)
    respostas = [{"pergunta": "Tom?", "resposta": "sóbrio"}]
    _esperar(lambda: design.start(conv, "landing de padaria", M))
    _esperar(lambda: design.start(conv, "landing de padaria", M, perguntar=True, respostas=respostas))
    tipo, user = vistos[-1]
    assert user.startswith("Pedido: landing de padaria") and "sóbrio" in user

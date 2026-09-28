"""A API local recusa origem de fora e, com token configurado, exige o header.

Sem isso, uma página que o usuário visite dispara POST contra 127.0.0.1 (o navegador manda; só não
deixa ela ler a resposta) e alcança /api/term, que é execução de shell sem aprovação.
"""
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def cliente():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("origin", [
    "https://evil.example",
    "http://localhost:5173",      # outro servidor de desenvolvimento na mesma máquina não é o Forja
    "http://127.0.0.1:9999",      # nem outra porta do loopback
    "null",                       # iframe sandbox / file://
])
def test_origem_de_fora_e_recusada(cliente, origin):
    r = cliente.get("/api/config", headers={"Origin": origin, "Host": "127.0.0.1:53211"})
    assert r.status_code == 403


def test_a_propria_interface_passa(cliente):
    r = cliente.get("/api/config", headers={"Origin": "http://127.0.0.1:53211", "Host": "127.0.0.1:53211"})
    assert r.status_code == 200


def test_sem_origin_passa(cliente):
    """Processo local e navegação do próprio app não mandam Origin; quem cuida deles é o token."""
    assert cliente.get("/api/config").status_code == 200


def test_token_exigido_quando_configurado(cliente, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "segredo")
    assert cliente.get("/api/config").status_code == 403
    assert cliente.get("/api/config", headers={"X-Forja-Token": "errado"}).status_code == 403
    assert cliente.get("/api/config", headers={"X-Forja-Token": "segredo"}).status_code == 200


@pytest.mark.parametrize("rota", ["/api/files?path=nao-existe.png", "/api/local/image/file?path=x.png",
                                  "/api/conversations/999/export"])
def test_subrecurso_leva_token_no_cookie_ou_na_query(cliente, monkeypatch, rota):
    """<img src> e link de download não mandam header: sem token nenhum, qualquer processo da máquina lia
    os arquivos da conversa. O token vem no cookie (a janela do app) ou em ?t= (o celular)."""
    monkeypatch.setattr(config, "API_TOKEN", "segredo")
    assert cliente.get(rota).status_code == 403
    cliente.cookies.set("forja_token", "errado")
    assert cliente.get(rota).status_code == 403
    cliente.cookies.set("forja_token", "segredo")
    assert cliente.get(rota).status_code != 403
    cliente.cookies.clear()
    assert cliente.get(rota + ("&" if "?" in rota else "?") + "t=segredo").status_code != 403


def test_cookie_nao_vale_para_o_resto_da_api(cliente, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "segredo")
    cliente.cookies.set("forja_token", "segredo")
    assert cliente.get("/api/config").status_code == 403


def test_relatorio_dispensa_token(cliente, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "segredo")
    # O relatório da pesquisa abre no navegador do usuário (window.open), fora do fetch da interface.
    assert cliente.get("/api/pesquisa/999/relatorio").status_code != 403

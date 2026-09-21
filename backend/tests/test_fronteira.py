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


def test_subrecurso_dispensa_token(cliente, monkeypatch):
    """<img src> e link de download não têm como mandar header; são leitura confinada."""
    monkeypatch.setattr(config, "API_TOKEN", "segredo")
    assert cliente.get("/api/files?path=nao-existe.png").status_code != 403

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


def test_origem_de_fora_e_recusada(cliente):
    r = cliente.get("/api/config", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


@pytest.mark.parametrize("origin", ["http://127.0.0.1:53211", "http://localhost:5173"])
def test_origem_loopback_passa(cliente, origin):
    assert cliente.get("/api/config", headers={"Origin": origin}).status_code == 200


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

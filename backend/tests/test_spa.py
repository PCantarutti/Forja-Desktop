"""A rota curinga que serve a interface não pode sair da pasta do build.

`GET /..%2f..%2fforja.db` chega em `spa()` com o caminho já decodificado: sem confinamento o
FileResponse entregaria o banco (com as chaves de API) na porta local.
"""
import pytest

from app import config
from app.main import _web_file


@pytest.fixture
def web(tmp_path, monkeypatch):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<html>", encoding="utf-8")
    (tmp_path / "web" / "assets").mkdir()
    (tmp_path / "web" / "assets" / "app.js").write_text("//", encoding="utf-8")
    (tmp_path / "forja.db").write_bytes(b"SQLite format 3\x00")
    monkeypatch.setattr(config, "WEB_DIR", tmp_path / "web")
    return tmp_path


def test_serve_arquivo_do_build(web):
    assert _web_file("assets/app.js") == (web / "web" / "assets" / "app.js").resolve()


@pytest.mark.parametrize("path", [
    "../forja.db",
    "../../forja.db",
    "assets/../../forja.db",
    "..\forja.db",
])
def test_fora_da_pasta_do_build(web, path):
    assert _web_file(path) is None  # o chamador cai no index.html


def test_rota_da_spa_nao_e_arquivo(web):
    assert _web_file("conversas/7") is None  # rota do React, não arquivo: index.html
    assert _web_file("") is None

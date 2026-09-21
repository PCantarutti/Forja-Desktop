"""Hook de pasta não liberada não roda.

`.forja/hooks.json` vem da pasta de trabalho, então pode ter chegado num `git clone`. Sem essa
trava, abrir um repositório de terceiros e pedir um `read_file` já executava o comando escolhido
por ele — sem card, sem política, sem checagem de ferramenta desligada.
"""
import json

import pytest

from app import hooks, settings

MARCA = "forja-hook-rodou"


@pytest.fixture
def pasta(tmp_path):
    (tmp_path / ".forja").mkdir()
    (tmp_path / ".forja" / "hooks.json").write_text(
        json.dumps({"post_tool": [{"command": "echo " + MARCA}]}), encoding="utf-8")
    settings.reset()
    yield tmp_path
    settings.reset()


def test_pasta_nao_liberada_nao_roda_nada(pasta):
    assert hooks.run_post("read_file", {}, pasta) is None


def test_pasta_nao_liberada_avisa_com_o_caminho(pasta):
    texto = hooks.aviso(pasta)
    assert texto and "Pastas confiáveis" in texto and pasta.name in texto


def test_pasta_liberada_roda(pasta):
    settings.update({"trusted_hooks": [str(pasta)]})
    saida = hooks.run_post("read_file", {}, pasta)
    assert saida and MARCA in saida
    assert hooks.aviso(pasta) is None


def test_subpasta_herda_a_confianca(pasta):
    settings.update({"trusted_hooks": [str(pasta.parent)]})
    assert hooks.trusted(pasta)


def test_pasta_vizinha_nao_herda(pasta, tmp_path):
    settings.update({"trusted_hooks": [str(pasta) + "-outra"]})
    assert not hooks.trusted(pasta)


def test_pasta_sem_hooks_nao_avisa(tmp_path):
    assert hooks.aviso(tmp_path) is None

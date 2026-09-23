from pathlib import Path

import pytest

from app import downloads


def test_troca_do_runtime_e_tudo_ou_nada(tmp_path, monkeypatch):
    """Um .dll em uso não pode deixar metade das DLLs de cada versão."""
    dest, novo = tmp_path / "vulkan", tmp_path / "vulkan.novo"
    for pasta, versao in ((dest, "velho"), (novo, "novo")):
        pasta.mkdir()
        for nome in ("ggml-vulkan.dll", "llama-common.dll"):
            (pasta / nome).write_text(versao)
    monkeypatch.setattr(downloads, "TRAVADO_TENTATIVAS", 2)
    monkeypatch.setattr(downloads.time, "sleep", lambda s: None)
    monkeypatch.setattr(downloads, "_travado", lambda f: f.name == "llama-common.dll")

    with pytest.raises(PermissionError, match="não foi alterado"):
        downloads._trocar(novo, dest)
    assert {f.read_text() for f in dest.iterdir()} == {"velho"}  # nenhum arquivo trocado
    assert not novo.exists()

    novo.mkdir()
    for nome in ("ggml-vulkan.dll", "llama-common.dll"):
        (novo / nome).write_text("novo")
    monkeypatch.setattr(downloads, "_travado", lambda f: False)
    downloads._trocar(novo, dest)
    assert {f.read_text() for f in dest.iterdir()} == {"novo"}
    assert not novo.exists()

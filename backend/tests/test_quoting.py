"""Valor interpolado num comando de shell não pode virar outro comando.

O `gitops` monta strings de PowerShell com título de PR, nome de branch e caminho de arquivo
dentro. No PowerShell a aspa dupla avalia `$(...)` e crase — então `x$(rm -rf ~)` num título
executava. `native.quote` fecha isso com aspa simples (PowerShell) ou `shlex.quote` (bash).
"""
import subprocess

import pytest

from app import gitops, native

PERIGOSOS = [
    "x$(rm -rf tudo)",
    "titulo com 'aspa simples'",
    'titulo com "aspa dupla"',
    "a; echo invadiu",
    "a && echo invadiu",
    "a`necho invadiu",
    "C:/pasta com espaco/arq.txt",
]


@pytest.mark.parametrize("valor", PERIGOSOS)
def test_quote_devolve_o_valor_intacto(valor):
    """O shell tem que receber exatamente o texto original, seja ele qual for."""
    argv = native.shell_argv("echo " + native.quote(valor))
    r = subprocess.run(argv, capture_output=True, timeout=60, **native.popen_kwargs())
    assert native.decode(r.stdout).strip() == valor


@pytest.mark.parametrize("valor", PERIGOSOS)
def test_gitops_cita_o_caminho_do_diff(valor, monkeypatch, tmp_path):
    """`git diff HEAD -- <caminho>` é o ponto do gitops onde a UI escolhe o valor."""
    vistos = []

    def falso_run(root, cmd, timeout=60):
        vistos.append(cmd)
        return 0, ""

    monkeypatch.setattr(gitops, "_run", falso_run)
    gitops.diff(tmp_path, valor)
    assert vistos and native.quote(valor) in vistos[0]

"""Suíte de aceite do bench (E0). O agente NÃO vê este arquivo: o bench copia para o projeto só no fim,
como tests/test_aceite_bench.py, e conta quantos passam. Não começa com test_ para o pytest do backend não
coletar."""
import csv
import datetime as dt
import json
import os
import subprocess
import sys

import pytest

from tarefas.armazem import Armazem
from tarefas.modelo import PRIORIDADES, Tarefa

HOJE = dt.date(2026, 9, 25)


def test_modelo_valida_e_serializa():
    assert PRIORIDADES == ("baixa", "media", "alta")
    t = Tarefa(id=1, titulo="Comprar pão", prazo=dt.date(2026, 10, 1), tags=["casa"])
    assert t.feita is False and t.prioridade == "media"
    d = t.para_dict()
    json.dumps(d)
    assert d["prazo"] == "2026-10-01"
    assert Tarefa.de_dict(d) == t
    with pytest.raises(ValueError):
        Tarefa(id=2, titulo="   ")
    with pytest.raises(ValueError):
        Tarefa(id=3, titulo="x", prioridade="urgente")
    assert Tarefa(id=4, titulo="y").tags == [] and Tarefa(id=5, titulo="z").tags is not Tarefa(id=6, titulo="w").tags


def test_armazem_crud_e_persistencia(tmp_path):
    arq = tmp_path / "t.json"
    a = Armazem(arq)
    assert a.listar() == []
    t1 = a.adicionar("A")
    t2 = a.adicionar("B", prioridade="alta", prazo=dt.date(2026, 9, 20), tags=["x"])
    assert (t1.id, t2.id) == (1, 2)
    assert Armazem(arq).obter(2).titulo == "B"  # outra instância lê o arquivo
    assert a.concluir(1).feita is True
    a.remover(2)
    with pytest.raises(KeyError):
        a.obter(2)
    with pytest.raises(KeyError):
        a.concluir(99)
    with pytest.raises(KeyError):
        a.remover(99)
    assert a.adicionar("C").id == 2  # maior id existente (1) + 1


def test_armazem_filtros_ordem_e_atrasadas(tmp_path):
    a = Armazem(tmp_path / "t.json")
    a.adicionar("baixa sem prazo", prioridade="baixa")
    a.adicionar("alta tarde", prioridade="alta", prazo=dt.date(2026, 12, 1), tags=["t"])
    a.adicionar("alta cedo", prioridade="alta", prazo=dt.date(2026, 9, 1))
    a.adicionar("media", tags=["t"])
    a.concluir(4)
    assert [t.titulo for t in a.listar()] == ["alta cedo", "alta tarde", "media", "baixa sem prazo"]
    assert [t.id for t in a.listar(feita=True)] == [4]
    assert [t.id for t in a.listar(prioridade="alta")] == [3, 2]
    assert [t.id for t in a.listar(tag="t")] == [2, 4]
    assert [t.id for t in a.atrasadas(HOJE)] == [3]


def _cli(tmp_path, *args):
    env = {**os.environ, "TAREFAS_ARQUIVO": str(tmp_path / "cli.json"), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "tarefas", *args], capture_output=True, text=True, env=env,
                       encoding="utf-8", timeout=60, cwd=os.getcwd())
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def test_cli_fluxo_completo(tmp_path):
    assert _cli(tmp_path, "add", "Ler livro") == (0, "Criada #1: Ler livro", "")
    assert _cli(tmp_path, "add", "Pagar conta", "--prioridade", "alta", "--prazo", "2020-01-01", "--tag", "casa")[:2] == \
        (0, "Criada #2: Pagar conta")
    assert _cli(tmp_path, "list")[1].splitlines() == ["#2 [ ] Pagar conta", "#1 [ ] Ler livro"]
    assert _cli(tmp_path, "done", "1")[:2] == (0, "Concluída #1")
    assert _cli(tmp_path, "list", "--feitas")[1] == "#1 [x] Ler livro"
    assert _cli(tmp_path, "list", "--pendentes")[1] == "#2 [ ] Pagar conta"
    assert _cli(tmp_path, "list", "--tag", "casa")[1] == "#2 [ ] Pagar conta"
    assert _cli(tmp_path, "stats")[1].splitlines() == ["total: 2", "feitas: 1", "pendentes: 1", "atrasadas: 1"]
    assert _cli(tmp_path, "rm", "2")[:2] == (0, "Removida #2")


def test_cli_erros(tmp_path):
    codigo, _, erro = _cli(tmp_path, "done", "42")
    assert codigo == 1 and erro
    codigo, _, erro = _cli(tmp_path, "add", "x", "--prioridade", "urgente")
    assert codigo != 0 and erro
    codigo, _, erro = _cli(tmp_path, "add", "x", "--prazo", "amanhã")
    assert codigo != 0 and erro


def test_cli_export_csv(tmp_path):
    _cli(tmp_path, "add", "A", "--tag", "a", "--tag", "b")
    _cli(tmp_path, "add", "B", "--prazo", "2026-10-05")
    _cli(tmp_path, "done", "2")
    saida = tmp_path / "out.csv"
    assert _cli(tmp_path, "export", "--csv", str(saida))[:2] == (0, "Exportadas 2 tarefas")
    with open(saida, encoding="utf-8", newline="") as f:
        linhas = list(csv.DictReader(f))
    assert list(linhas[0]) == ["id", "titulo", "feita", "prioridade", "prazo", "tags"]
    por_id = {l["id"]: l for l in linhas}
    assert por_id["1"]["tags"] == "a;b" and por_id["1"]["feita"] == "nao"
    assert por_id["2"]["feita"] == "sim" and por_id["2"]["prazo"] == "2026-10-05"

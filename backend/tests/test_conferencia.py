"""Conferência automática dos testes prontos: a solução certa leva nota máxima, os erros típicos perdem."""
import shutil

import pytest

from app import baterias, conferencia

FIFO_CERTO = '''```python
def custo_fifo(movimentos):
    lotes, total = [], 0
    for mov in movimentos:
        tipo, qtd = mov[0], mov[1]
        if qtd <= 0:
            raise ValueError("quantidade")
        if tipo == "entrada":
            lotes.append([qtd, mov[2]])
        elif tipo == "saida":
            if qtd > sum(l[0] for l in lotes):
                raise ValueError("estoque")
            while qtd:
                usa = min(qtd, lotes[0][0])
                total += usa * lotes[0][1]
                lotes[0][0] -= usa
                qtd -= usa
                if not lotes[0][0]:
                    lotes.pop(0)
        else:
            raise ValueError("tipo")
    return total, [tuple(l) for l in lotes]
```
Sem executar: (9700, [(2, 700)])'''

TESTES_BONS = '''```python
import pytest
def test_ok():
    assert minutos("45m") == 45 and minutos("2h") == 120 and minutos("1h30m") == 90 and minutos("90m") == 90
@pytest.mark.parametrize("t", ["", "1h60m", "1h 30m", "30", "30m1h"])
def test_invalidos(t):
    with pytest.raises(ValueError):
        minutos(t)
```'''

TOPN_CERTO = '''```js
function topN(lista, n) {
  if (n <= 0) return [];
  return [...new Set(lista)].sort((a, b) => b - a).slice(0, n);
}
```
Devolve [40, 12, 7] e [100, 20, 9].'''


def test_fifo_certo_nota_maxima_e_lifo_perde():
    assert conferencia.logica(FIFO_CERTO)["nota"] == 10
    lifo = FIFO_CERTO.replace("lotes[0]", "lotes[-1]").replace("lotes.pop(0)", "lotes.pop()").replace("(9700, [(2, 700)])", "")
    r = conferencia.logica(lifo)
    assert r["nota"] <= 7 and "exemplo do enunciado" in r["resumo"] and "lote mais antigo" in r["resumo"]


def test_testes_contam_bugs_pegos_e_testes_errados_zeram():
    assert conferencia.testes(TESTES_BONS)["nota"] == 10
    so_um = TESTES_BONS.replace('["", "1h60m", "1h 30m", "30", "30m1h"]', '["30"]').replace('[""', '["')
    assert conferencia.testes(so_um)["nota"] == 1          # válidos, mas não pegam nenhum dos 3 bugs
    errado = TESTES_BONS.replace('minutos("2h") == 120', 'minutos("2h") == 2')
    assert conferencia.testes(errado)["nota"] == 0          # falha na versão certa: testes errados


@pytest.mark.skipif(not shutil.which("node"), reason="precisa do node")
def test_topn_certo_e_o_original_bugado():
    assert conferencia.geral(TOPN_CERTO)["nota"] == 10
    original = "```js\n" + baterias.CODIGO_GERAL + "\n```"
    assert conferencia.geral(original)["nota"] <= 3


def test_bug_do_enunciado_de_testes_e_o_que_os_mutantes_simulam():
    """O código mostrado ao modelo tem exatamente os 3 bugs que a conferência planta um a um."""
    ns: dict = {}
    exec(baterias.CODIGO_TESTES, ns)
    minutos = ns["minutos"]
    assert minutos("") == 0 and minutos("1h60m") == 120 and minutos("1h 30m") == 90   # os 3 bugs
    assert minutos("1h30m") == 90 and minutos("90m") == 90 and minutos("0h") == 0
    with pytest.raises(ValueError):
        minutos("30m1h")


def test_testar_de_resposta_de_teste_pronto_roda_os_casos(tmp_path, monkeypatch):
    """Função sozinha não mostra nada no terminal: o Testar põe os casos do gabarito junto."""
    import re
    monkeypatch.setattr(baterias, "TESTES_DIR", tmp_path)
    codigo = re.search(r"```\w*\n(.*?)```", FIFO_CERTO, re.S).group(1)
    r = baterias.testar_codigo(codigo, "python", "9-A", bateria="logica")
    conteudo = (tmp_path / "9-A" / "main.py").read_text("utf-8")
    assert "Casos do gabarito" in conteudo and r["comando"].startswith("python ")
    testes = re.search(r"```\w*\n(.*?)```", TESTES_BONS, re.S).group(1)
    r = baterias.testar_codigo(testes, "python", "9-B", bateria="testes")
    assert "pytest -v" in r["comando"] and "def minutos" in (tmp_path / "9-B" / "main.py").read_text("utf-8")
    assert "Casos" not in (tmp_path / "9-C" / "main.py").read_text("utf-8") if baterias.testar_codigo(
        "print(1)", "python", "9-C")["tipo"] == "terminal" else True   # fora de teste pronto: código puro

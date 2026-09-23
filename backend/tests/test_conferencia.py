"""Conferência automática dos testes prontos: a solução certa leva nota máxima, os erros típicos perdem."""
import shutil

import pytest

from app import baterias, conferencia

FIFO_CERTO = '''```python
def custo_fifo(movimentos):
    lotes, total, ultima = [], 0, []
    for mov in movimentos:
        tipo, qtd = mov[0], mov[1]
        if qtd <= 0:
            raise ValueError("quantidade")
        if tipo == "entrada":
            lotes.append([qtd, mov[2]])
        elif tipo == "saida":
            if qtd > sum(l[0] for l in lotes):
                raise ValueError("estoque")
            ultima = []
            while qtd:
                usa = min(qtd, lotes[0][0])
                total += usa * lotes[0][1]
                ultima.append([usa, lotes[0][1]])
                lotes[0][0] -= usa
                qtd -= usa
                if not lotes[0][0]:
                    lotes.pop(0)
        elif tipo == "devolucao":
            if qtd > sum(u[0] for u in ultima):
                raise ValueError("devolucao")
            while qtd:
                usa = min(qtd, ultima[-1][0])
                custo = ultima[-1][1]
                total -= usa * custo
                ultima[-1][0] -= usa
                qtd -= usa
                if not ultima[-1][0]:
                    ultima.pop()
                if lotes and lotes[0][1] == custo:
                    lotes[0][0] += usa
                else:
                    lotes.insert(0, [usa, custo])
        else:
            raise ValueError("tipo")
    return total, [tuple(l) for l in lotes]
```
Sem executar: (8200, [(1, 800), (3, 700)])'''

TESTES_BONS = '''```python
import pytest
def test_ok():
    assert minutos("45m") == 45 and minutos("2h") == 120 and minutos("1h30m") == 90 and minutos("90m") == 90
@pytest.mark.parametrize("t", ["", "1h60m", "1h 30m", "30", "30m1h"])
def test_invalidos(t):
    with pytest.raises(ValueError):
        minutos(t)
```'''

OCUPADOS_CERTO = '''```js
function ocupados(reservas) {
  const min = h => { const [a, b] = h.split(":"); return +a * 60 + +b; };
  const ordenadas = reservas.map(r => ({ ...r })).sort((a, b) => min(a.inicio) - min(b.inicio));
  const res = [];
  for (const r of ordenadas) {
    const ultimo = res[res.length - 1];
    if (ultimo && min(r.inicio) <= min(ultimo.fim)) {
      if (min(r.fim) > min(ultimo.fim)) ultimo.fim = r.fim;
    } else {
      res.push(r);
    }
  }
  return res;
}
```
Devolve [{inicio: "8:15", fim: "11:00"}, {inicio: "13:00", fim: "15:00"}].'''


def test_fifo_certo_nota_maxima_e_lifo_perde():
    assert conferencia.logica(FIFO_CERTO)["nota"] == 10
    lifo = FIFO_CERTO.replace("lotes[0]", "lotes[-1]").replace("lotes.pop(0)", "lotes.pop()").replace("(8200, [(1, 800), (3, 700)])", "")
    r = conferencia.logica(lifo)
    assert r["nota"] <= 7 and "exemplo do enunciado" in r["resumo"] and "lote mais antigo" in r["resumo"]


def test_devolucao_no_fim_da_fila_ou_das_primeiras_unidades_perde():
    """O desempate da lógica: quem acerta o FIFO mas erra a devolução não leva 10."""
    no_fim = FIFO_CERTO.replace("lotes.insert(0, [usa, custo])", "lotes.append([usa, custo])")
    assert "devolvido é o primeiro a sair" in conferencia.logica(no_fim)["resumo"]
    primeiras = FIFO_CERTO.replace("ultima[-1]", "ultima[0]").replace("ultima.pop()", "ultima.pop(0)")
    assert conferencia.logica(primeiras)["nota"] < 10
    sem_juntar = FIFO_CERTO.replace("if lotes and lotes[0][1] == custo:", "if False:")
    assert "junta com lote" in conferencia.logica(sem_juntar)["resumo"]


def test_testes_contam_bugs_pegos_e_testes_errados_zeram():
    assert conferencia.testes(TESTES_BONS)["nota"] == 10
    so_um = TESTES_BONS.replace('["", "1h60m", "1h 30m", "30", "30m1h"]', '["30"]').replace('[""', '["')
    assert conferencia.testes(so_um)["nota"] == 1          # válidos, mas não pegam nenhum dos 3 bugs
    errado = TESTES_BONS.replace('minutos("2h") == 120', 'minutos("2h") == 2')
    assert conferencia.testes(errado)["nota"] == 0          # falha na versão certa: testes errados


@pytest.mark.skipif(not shutil.which("node"), reason="precisa do node")
def test_ocupados_certo_e_o_original_bugado():
    assert conferencia.geral(OCUPADOS_CERTO)["nota"] == 10
    original = "```js\n" + baterias.CODIGO_GERAL + "\n```"
    assert conferencia.geral(original)["nota"] <= 3
    # o erro sutil: copiar só a lista (slice) e continuar mudando o objeto do chamador
    raso = OCUPADOS_CERTO.replace("reservas.map(r => ({ ...r }))", "reservas.slice()")
    r = conferencia.geral(raso)
    assert r["nota"] < 10 and "não altera os objetos" in r["resumo"]
    texto = OCUPADOS_CERTO.replace("min(a.inicio) - min(b.inicio)", "a.inicio < b.inicio ? -1 : 1")
    assert "um dígito" in conferencia.geral(texto)["resumo"]


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

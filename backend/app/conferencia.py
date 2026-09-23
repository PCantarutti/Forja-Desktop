"""Conferência automática dos testes prontos do Comparar: EXECUTA o código que cada modelo entregou.

O revisor lê respostas e erra — numa validação real ele "viu" um código truncado que estava inteiro e
deu nota por impressão. O que dá para medir, mede-se: a função roda contra casos com resposta certa,
os testes do modelo rodam contra a versão certa e contra versões com um bug plantado cada (quantos
bugs eles pegam). O resultado vai para o revisor como fato e fica numa tabela na análise.

O código é de um modelo respondendo a um teste nosso, e roda como o "Testar" já roda: processo
separado, pasta temporária, tempo limite. Não recebe arquivo nem rede do usuário além disso.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

TEMPO = 30  # s por execução


def _blocos(texto: str, *linguagens: str) -> list[str]:
    achados = re.findall(r"```(\w*)[^\n]*\n(.*?)```", texto or "", re.S)
    return [c for lang, c in achados if lang.lower() in linguagens or (not lang and "" in linguagens)]


def _fora_do_codigo(texto: str) -> str:
    return re.sub(r"```.*?```", " ", texto or "", flags=re.S)


def _previsao(texto: str, funcao: str) -> str:
    """Onde o modelo diz o retorno previsto: o texto livre e os blocos que não definem a função (muitos
    põem o resultado num bloco de código próprio)."""
    outros = [c for _, c in re.findall(r"```(\w*)[^\n]*\n(.*?)```", texto or "", re.S) if not re.search(
        rf"(def|function)\s+{funcao}\b|{funcao}\s*=", c)]
    return _fora_do_codigo(texto) + "\n" + "\n".join(outros)


def _roda(cmd: list[str], arquivos: dict[str, str]) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d:
        for nome, conteudo in arquivos.items():
            with open(os.path.join(d, nome), "w", encoding="utf-8") as f:
                f.write(conteudo)
        try:
            r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=TEMPO,
                               encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return -1, "tempo esgotado (laço infinito?)"
        except OSError as e:  # sem node, por exemplo
            return -1, f"não deu para executar: {e}"
    return r.returncode, r.stdout + r.stderr


def _casos(saida: str) -> dict | None:
    for linha in reversed(saida.strip().splitlines()):
        try:
            return json.loads(linha)
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ lógica: custo FIFO

CASOS_FIFO = r'''
import json
res = {}
def norm(r):
    total, lotes = r
    return int(total), [tuple(int(x) for x in l) for l in lotes]
def t(nome, f):
    try: res[nome] = bool(f())
    except Exception as e: res[nome] = False
def erro(movs):
    try: custo_fifo(movs); return False
    except ValueError: return True
    except Exception: return False
t("exemplo do enunciado", lambda: norm(custo_fifo([("entrada",10,500),("entrada",5,800),("saida",12),("entrada",3,700),("saida",4),("devolucao",2)])) == (8200, [(1,800),(3,700)]))
t("exemplo sem a devolução", lambda: norm(custo_fifo([("entrada",10,500),("entrada",5,800),("saida",12),("entrada",3,700),("saida",4)])) == (9700, [(2,700)]))
t("só entradas", lambda: norm(custo_fifo([("entrada",4,100)])) == (0, [(4,100)]))
t("saída zera o estoque", lambda: norm(custo_fifo([("entrada",3,100),("saida",3)])) == (300, []))
t("saída atravessa 3 lotes", lambda: norm(custo_fifo([("entrada",1,100),("entrada",1,200),("entrada",1,300),("saida",3)])) == (600, []))
t("sai do lote mais antigo e mantém a ordem", lambda: norm(custo_fifo([("entrada",2,100),("entrada",2,200),("saida",1)])) == (100, [(1,100),(2,200)]))
t("saída parcial mantém o resto do lote", lambda: norm(custo_fifo([("entrada",5,100),("saida",2)])) == (200, [(3,100)]))
t("saída maior que o estoque: ValueError", lambda: erro([("entrada",2,100),("saida",3)]))
t("quantidade zero: ValueError", lambda: erro([("entrada",0,100)]))
t("saída negativa: ValueError", lambda: erro([("entrada",2,100),("saida",-1)]))
t("tipo desconhecido: ValueError", lambda: erro([("ajuste",1)]))
t("devolução desfaz a saída de trás para frente", lambda: norm(custo_fifo([("entrada",1,100),("entrada",1,200),("saida",2),("devolucao",1)])) == (100, [(1,200)]))
t("devolvido é o primeiro a sair de novo", lambda: norm(custo_fifo([("entrada",1,100),("entrada",1,200),("entrada",1,300),("saida",2),("devolucao",1),("saida",1)])) == (300, [(1,300)]))
t("devolução junta com lote de mesmo custo", lambda: norm(custo_fifo([("entrada",3,100),("saida",1),("devolucao",1)])) == (0, [(3,100)]))
t("devoluções seguidas desfazem a mesma saída", lambda: norm(custo_fifo([("entrada",2,100),("entrada",2,200),("saida",3),("devolucao",1),("devolucao",1)])) == (100, [(1,100),(2,200)]))
t("devolução só da saída mais recente", lambda: norm(custo_fifo([("entrada",1,100),("entrada",1,200),("saida",1),("saida",1),("devolucao",1)])) == (100, [(1,200)]))
t("devolução maior que a saída: ValueError", lambda: erro([("entrada",5,100),("saida",2),("devolucao",3)]))
t("devolver de novo o que já voltou: ValueError", lambda: erro([("entrada",5,100),("saida",1),("devolucao",1),("devolucao",1)]))
t("devolução sem saída: ValueError", lambda: erro([("entrada",5,100),("devolucao",1)]))
print(json.dumps(res))
'''


def logica(texto: str) -> dict:
    codigo = "\n\n".join(c for c in _blocos(texto, "python", "py", "") if "def custo_fifo" in c)
    if not codigo:
        return {"nota": 0, "resumo": "não entregou a função custo_fifo"}
    _, saida = _roda([sys.executable, "m.py"], {"m.py": codigo + "\n" + CASOS_FIFO})
    res = _casos(saida)
    if res is None:
        return {"nota": 0, "resumo": "o código não roda: " + saida.strip()[-160:]}
    ok = sum(res.values())
    texto_livre = re.sub(r"\s", "", _previsao(texto, "custo_fifo"))
    disse = ("8200" in texto_livre or "8.200" in texto_livre or "82,00" in texto_livre) + ("[(1,800),(3,700)]" in texto_livre)
    falhas = [k for k, v in res.items() if not v]
    return {"nota": round(10 * (ok + disse) / (len(res) + 2), 1),
            "resumo": f"{ok}/{len(res)} casos; cálculo sem executar {disse}/2"
                      + (f"; falhou: {', '.join(falhas)}" if falhas else "")}


# ------------------------------------------------------------------ testes: quantos bugs plantados os testes pegam

MINUTOS_CERTA = r'''
import re
def minutos(texto: str) -> int:
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", texto)
    if not texto or not m:
        raise ValueError(texto)
    h, mi = m.groups()
    if h is not None and mi is not None and int(mi) > 59:
        raise ValueError(texto)
    return int(h or 0) * 60 + int(mi or 0)
'''
MUTANTES = {
    "texto vazio vira 0": MINUTOS_CERTA.replace("if not texto or not m:", "if not m:"),
    "aceita 1h60m": MINUTOS_CERTA.replace("int(mi) > 59", "int(mi) > 60"),
    "aceita espaço (1h 30m)": MINUTOS_CERTA.replace(r'(?:(\d+)h)?(?:(\d+)m)?', r'(?:(\d+)h)?\s*(?:(\d+)m)?'),
}


def _suite(codigo_testes: str, impl: str) -> tuple[int, int]:
    _, saida = _roda([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_m.py"],
                     {"test_m.py": "import pytest\n" + impl + "\n" + codigo_testes})
    falhas = re.search(r"(\d+) failed", saida)
    passes = re.search(r"(\d+) passed", saida)
    erros = re.search(r"(\d+) error", saida)
    return (int(falhas.group(1)) if falhas else 0) + (int(erros.group(1)) if erros else 0), \
        int(passes.group(1)) if passes else 0


def so_testes(codigo: str) -> str:
    """Os testes sem o import da função nem uma cópia dela colada junto: a função vem da nossa versão."""
    codigo = re.sub(r"^\s*from\s+\S+\s+import\s+minutos.*$", "", codigo, flags=re.M)
    return re.sub(r"^def minutos\(.*?(?=^\S)", "", codigo + "\n#fim\n", flags=re.M | re.S)


def testes(texto: str) -> dict:
    codigo = "\n\n".join(c for c in _blocos(texto, "python", "py", "") if "def test" in c)
    if not codigo:
        return {"nota": 0, "resumo": "não entregou testes pytest"}
    codigo = so_testes(codigo)
    falhas_ok, passes_ok = _suite(codigo, MINUTOS_CERTA)
    if not passes_ok and not falhas_ok:
        return {"nota": 0, "resumo": "os testes não rodam"}
    pegos = [nome for nome, impl in MUTANTES.items() if _suite(codigo, impl)[0] > 0]
    validos = falhas_ok == 0
    nota = (1 if validos else 0) + (3 * len(pegos) if validos else 0)
    return {"nota": nota,
            "resumo": (f"{passes_ok + falhas_ok} testes; " + ("passam na versão certa" if validos
                       else f"{falhas_ok} FALHAM na versão certa (testes errados)")
                       + f"; bugs pegos {len(pegos)}/{len(MUTANTES)}" + (f" ({', '.join(pegos)})" if pegos else ""))}


# ------------------------------------------------------------------ geral: períodos ocupados em JavaScript

CASOS_OCUPADOS = r'''
const r = {};
const t = (n, f) => { try { r[n] = !!f(); } catch (e) { r[n] = false; } };
const min = h => typeof h === "number" ? h : (([a, b]) => +a * 60 + +b)(String(h).split(":"));
const igual = (saida, esperado) => Array.isArray(saida) && saida.length === esperado.length &&
  saida.every((p, i) => p && min(p.inicio) === min(esperado[i][0]) && min(p.fim) === min(esperado[i][1]));
const R = (...ps) => ps.map(([inicio, fim]) => ({ inicio, fim }));
t("exemplo do enunciado", () => igual(ocupados(R(["9:00", "10:30"], ["13:30", "14:00"], ["10:30", "11:00"], ["13:00", "15:00"], ["8:15", "9:00"])), [["8:15", "11:00"], ["13:00", "15:00"]]));
t("hora de um dígito ordena como hora (9:00 antes de 10:00)", () => igual(ocupados(R(["10:00", "11:00"], ["9:00", "9:30"])), [["9:00", "9:30"], ["10:00", "11:00"]]));
t("sobreposição que cruza 9h → 10h", () => igual(ocupados(R(["9:30", "10:15"], ["10:00", "10:45"])), [["9:30", "10:45"]]));
t("reservas que encostam viram uma", () => igual(ocupados(R(["9:00", "10:00"], ["10:00", "11:00"])), [["9:00", "11:00"]]));
t("reserva contida não encurta o período", () => igual(ocupados(R(["9:00", "10:00"], ["9:30", "9:45"])), [["9:00", "10:00"]]));
t("encadeadas viram um período só", () => igual(ocupados(R(["9:15", "10:00"], ["9:00", "9:30"], ["9:50", "11:00"])), [["9:00", "11:00"]]));
t("separadas continuam separadas", () => igual(ocupados(R(["13:00", "14:00"], ["9:00", "10:00"])), [["9:00", "10:00"], ["13:00", "14:00"]]));
t("não altera os objetos recebidos", () => { const l = R(["9:00", "10:00"], ["9:30", "11:00"]); const antes = JSON.stringify(l); ocupados(l); return JSON.stringify(l) === antes; });
t("não altera a ordem da lista", () => { const l = R(["13:00", "14:00"], ["9:00", "10:00"]); ocupados(l); return l[0].inicio === "13:00"; });
t("lista vazia devolve []", () => { const s = ocupados([]); return Array.isArray(s) && s.length === 0; });
console.log(JSON.stringify(r));
'''


def geral(texto: str) -> dict:
    codigo = "\n\n".join(c for c in _blocos(texto, "js", "javascript", "") if "ocupados" in c)
    if not codigo:
        return {"nota": 0, "resumo": "não entregou a função ocupados corrigida"}
    codigo = re.sub(r"^export\s+(default\s+)?", "", codigo, flags=re.M)   # node roda como CommonJS
    _, saida = _roda(["node", "m.js"], {"m.js": codigo + "\n" + CASOS_OCUPADOS})
    res = _casos(saida)
    if res is None:
        return {"nota": 0, "resumo": "o código não roda: " + saida.strip()[-160:]}
    ok = sum(res.values())
    livre = re.sub(r"[\s\"'`]", "", _previsao(texto, "ocupados"))
    # a previsão: os dois períodos em sequência; "13:00 → 15:00" sozinho está na própria entrada
    disse = (bool(re.search(r"0?8:15\D{1,12}11:00", livre))
             + bool(re.search(r"0?8:15\D{1,12}11:00\D{1,30}13:00\D{1,12}15:00", livre)))
    falhas = [k for k, v in res.items() if not v]
    return {"nota": round(10 * (ok + disse) / (len(res) + 2), 1),
            "resumo": f"{ok}/{len(res)} casos; saídas ditas {disse}/2" + (f"; falhou: {', '.join(falhas)}" if falhas else "")}


CONFERE = {"logica": logica, "testes": testes, "geral": geral}


def conferir(bateria: str, resposta: str) -> dict | None:
    """{"nota", "resumo"} medidos executando o código, ou None (bateria sem conferência)."""
    f = CONFERE.get(bateria)
    if not f:
        return None
    try:
        return f(resposta or "")
    except Exception as e:  # conferência que quebra não pode derrubar a análise
        return {"nota": None, "resumo": f"conferência falhou: {e}"}


# ------------------------------------------------------------------ "▶ Testar" de uma resposta de teste pronto

def _legivel_py(casos: str) -> str:
    return casos.replace("print(json.dumps(res))",
                         'print("\\nCasos do gabarito:")\nfor k, v in res.items():\n    print(("OK      " if v else "FALHOU  ") + k)')


def _legivel_js(casos: str) -> str:
    return casos.replace("console.log(JSON.stringify(r));",
                         "console.log('\\nCasos do gabarito:');\n"
                         "for (const [k, v] of Object.entries(r)) console.log((v ? 'OK      ' : 'FALHOU  ') + k);")


def para_testar(bateria: str, linguagem: str, codigo: str) -> tuple[str, str] | None:
    """Função sozinha não mostra nada no terminal: numa resposta de teste pronto, o "Testar" roda junto os
    casos do gabarito (OK / FALHOU por caso). Devolve (conteúdo do arquivo, modo) ou None."""
    if bateria == "logica" and linguagem == "python" and "def custo_fifo" in codigo:
        return codigo + "\n" + _legivel_py(CASOS_FIFO), "python"
    if bateria == "geral" and linguagem == "javascript" and "ocupados" in codigo:
        return codigo + "\n" + _legivel_js(CASOS_OCUPADOS), "node"
    if bateria == "testes" and linguagem == "python" and "def test" in codigo:
        from .baterias import CODIGO_TESTES
        # os testes do modelo contra a função do enunciado, que tem 3 bugs: os que falham acharam um
        return "import pytest\n" + CODIGO_TESTES + "\n\n" + so_testes(codigo), "pytest"
    return None

"""Placar de progresso e recuperação de loop sem LLM (E16 parte A).

O `LoopDetector` (parsing.py) só vê a mesma chamada com os mesmos argumentos em sequência. Aqui entra
o resto: ciclos curtos (A,B,A,B), o mesmo erro voltando por caminhos diferentes, passos que não
produzem nada novo, sinais de alucinação e raciocínio degenerado. A resposta é em níveis:

- nível 1: lembrete (4 passos sem progresso);
- nível 2: intervenção estruturada, a chamada que girava fica bloqueada por `BLOQUEIO` passos e o
  próximo turno pensa com teto menor.
"""
from __future__ import annotations

import hashlib
import json
import re
import zlib
from collections import Counter
from pathlib import Path

LEMBRA_SEM_PROGRESSO = 4
INTERVEM_SEM_PROGRESSO = 8
MESMO_ERRO = 3
LIMITE_ALUCINACAO = 3
BLOQUEIO = 5          # passos em que a chamada da intervenção fica recusada
PERIODOS = (2, 3, 4)  # tamanhos de ciclo procurados na sequência de chamadas


def chave(nome: str, args: dict) -> str:
    return nome + json.dumps(args, sort_keys=True, ensure_ascii=False)


def _h(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8", "replace")).hexdigest()


_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_CAMINHO = re.compile(r"(?:[A-Za-z]:)?[\\/][^\s'\"`:,)]+|\b[\w.-]+\.[a-z]{1,4}\b")
_ASPAS = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`")


def normaliza(texto: str) -> str:
    """Sem números (tempos, linhas, pids): dois resultados iguais a menos do relógio contam como iguais."""
    return _NUM.sub("#", texto or "").strip()


def assinatura_erro(texto: str) -> str:
    """Primeira linha do erro, sem números, caminhos nem o que está entre aspas."""
    linha = next((ln for ln in (texto or "").splitlines() if ln.strip()), "")
    return _NUM.sub("#", _CAMINHO.sub("<p>", _ASPAS.sub("<s>", linha))).strip().lower()[:200]


AFIRMA = re.compile(r"\b(testei|verifiquei|conferi|validei|os testes passa\w*|passou|passaram|tudo funciona\w*"
                    r"|i tested|i verified|tests? pass(?:ed|es)?|all tests pass)\b", re.I)


class Placar:
    def __init__(self) -> None:
        self.seq: list[str] = []                    # chaves das chamadas, em ordem
        self.nomes: dict[str, str] = {}              # chave -> nome da ferramenta (para a mensagem)
        self.resultados: set[str] = set()
        self.arquivos: dict[str, list[str]] = {}     # caminho -> hashes do conteúdo depois de cada escrita
        self.erros: Counter[str] = Counter()
        self.cmd_falhou: set[str] = set()
        self.ausentes: Counter[str] = Counter()
        self.old_str: Counter[str] = Counter()
        self.alucina = 0
        self.sem_progresso = 0
        self.lembrou = False
        self.bloqueios: dict[str, int] = {}
        self.escreveu = False
        self.testou = False                          # run_command ok desde a última escrita
        self.teto_menor = False                      # próximo turno pensa com teto menor
        self.ultimo = ""                              # último resultado (para a mensagem do nível 2)
        self.mudados: list[str] = []

    # ------------------------------------------------------------ entrada

    def usuario(self) -> None:
        """Mensagem do usuário conta como progresso e zera o que era sinal de giro."""
        hist = self.arquivos
        self.__init__()
        self.arquivos = hist

    def bloqueada(self, nome: str, args: dict) -> str | None:
        if self.bloqueios.get(chave(nome, args), 0) > 0:
            return ("Chamada bloqueada pela recuperação de loop: ela já foi feita e não levou a nada. "
                    "Escolha outra ação.")
        return None

    def alucinou(self) -> None:
        self.alucina += 1

    def resultado(self, nome: str, args: dict, status: str, texto: str, root: Path | None,
                  mutante: bool) -> bool:
        """Registra uma chamada executada. Devolve True se ela produziu progresso."""
        k = chave(nome, args)
        self.seq = (self.seq + [k])[-24:]
        self.nomes[k] = nome
        self.ultimo = (texto or "")[:300]
        ok = status == "ok"
        caminho = str(args.get("path") or "")
        if not ok:
            if (s := assinatura_erro(texto)):
                self.erros[s] += 1
            baixo = (texto or "").lower()
            if caminho and ("não encontrad" in baixo or "not found" in baixo or "não existe" in baixo):
                if "old_str" in baixo:
                    self.old_str[caminho] += 1
                    self.alucina += self.old_str[caminho] >= 2
                else:
                    self.ausentes[caminho] += 1
                    self.alucina += self.ausentes[caminho] >= 2
            if nome == "run_command":
                self.cmd_falhou.add(str(args.get("command")))
            return False
        if mutante and caminho and root is not None:
            try:
                atual = _h((root / caminho).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                atual = "<sem arquivo>"
            antes = self.arquivos.setdefault(caminho, [])
            novo = atual not in antes  # voltar a um conteúdo anterior é desfazer, não avançar
            antes.append(atual)
            self.escreveu, self.testou = True, False
            if caminho not in self.mudados:
                self.mudados.append(caminho)
            return novo
        if nome == "run_command":
            self.testou = True
            if str(args.get("command")) in self.cmd_falhou:  # falhava e passou
                self.cmd_falhou.discard(str(args.get("command")))
                return True
        h = _h(nome + normaliza(texto))
        novo = h not in self.resultados
        self.resultados.add(h)
        return novo

    def passo(self, progrediu: bool) -> None:
        """Fim de um passo (uma resposta do modelo com as chamadas dela)."""
        self.bloqueios = {k: n - 1 for k, n in self.bloqueios.items() if n > 1}
        if progrediu:
            self.sem_progresso, self.lembrou = 0, False
        else:
            self.sem_progresso += 1

    # ------------------------------------------------------------ diagnóstico

    def ciclo(self) -> tuple[int, str] | None:
        """(período, chave a bloquear) quando o fim da sequência é um ciclo curto repetido."""
        for p in PERIODOS:
            if len(self.seq) >= 2 * p:
                a, b = self.seq[-2 * p:-p], self.seq[-p:]
                if a == b and len(set(b)) > 1:
                    return p, b[0]  # b[0] é a próxima chamada do ciclo
        return None

    def afirmacao_sem_teste(self, texto: str) -> bool:
        return self.escreveu and not self.testou and bool(AFIRMA.search(texto or ""))

    def avalia(self) -> tuple[int, str, str | None]:
        """(nível, motivo, chave a bloquear). Nível 0 = nada a fazer."""
        ultima = self.seq[-1] if self.seq else None
        if (c := self.ciclo()):
            p, k = c
            nomes = " → ".join(self.nomes.get(x, "?") for x in self.seq[-p:])
            self.seq = []
            return 2, f"ciclo de {p} chamadas repetido ({nomes})", k
        s, n = self.erros.most_common(1)[0] if self.erros else ("", 0)
        if n >= MESMO_ERRO:
            del self.erros[s]
            return 2, f"o mesmo erro voltou {n} vezes ({s[:120]})", ultima
        if self.alucina >= LIMITE_ALUCINACAO:
            self.alucina = 0
            return 2, "sinais de alucinação (ferramenta que não existe, arquivo inexistente ou old_str que não está no arquivo, repetidos)", ultima
        if self.sem_progresso >= INTERVEM_SEM_PROGRESSO:
            n, self.sem_progresso = self.sem_progresso, 0
            return 2, f"{n} passos seguidos sem nada novo", ultima
        if self.sem_progresso >= LEMBRA_SEM_PROGRESSO and not self.lembrou:
            self.lembrou = True
            return 1, f"{self.sem_progresso} passos seguidos sem nada novo", None
        return 0, "", None

    def intervencao(self, motivo: str, alvo: str | None) -> str:
        """Mensagem do nível 2. Bloqueia `alvo` e marca o próximo turno para pensar menos."""
        if alvo:
            self.bloqueios[alvo] = BLOQUEIO
        self.teto_menor = True
        self.sem_progresso, self.lembrou = 0, True  # a contagem recomeça depois da intervenção, sem lembrete no meio
        estado = f"arquivos mudados: {', '.join(self.mudados[-6:]) or 'nenhum'}; " + (
            "último comando passou" if self.testou else "nada testado desde a última escrita" if self.escreveu
            else "nenhum teste rodado")
        bloq = (f" A chamada {self.nomes.get(alvo, alvo.split('{')[0])} com esses argumentos fica bloqueada pelos "
                f"próximos {BLOQUEIO} passos." if alvo else "")
        return (f"Você está em loop: {motivo}. Último resultado: {self.ultimo.strip()[:300] or '(vazio)'}. "
                f"Estado: {estado}.{bloq} Escreva em 3 linhas o que está errado e escolha uma abordagem "
                "DIFERENTE da que vinha tentando.")


LEMBRETE = ("Os últimos passos não produziram nada novo (mesmos resultados, nenhum arquivo mudou de verdade). "
            "Pare e reveja: o que falta, e qual ação diferente leva até lá?")


# ------------------------------------------------------------ raciocínio

HESITA = re.compile(r"\b(wait|actually|hmm+|espera|na verdade)\b", re.I)
JANELA = 4000         # caracteres analisados (~1k tokens)
MIN_ANALISE = 2000
COMPRESSAO_MIN = 0.25
NGRAMA, NGRAMA_VEZES = 12, 3
HESITA_POR_MIL = 20   # marcadores por mil tokens (~4 caracteres por token)
CHECA_A_CADA = 2000   # caracteres de raciocínio entre duas análises (~500 tokens)


def degenerado(raciocinio: str) -> str:
    """Motivo, se o fim do raciocínio degenerou; "" se está normal."""
    j = (raciocinio or "")[-JANELA:]
    if len(j) < MIN_ANALISE:
        return ""
    b = j.encode("utf-8", "replace")
    if (r := len(zlib.compress(b)) / len(b)) < COMPRESSAO_MIN:
        return f"texto repetitivo (compressão {r:.2f})"
    p = j.lower().split()
    if len(p) > NGRAMA:
        n, vezes = Counter(tuple(p[i:i + NGRAMA]) for i in range(len(p) - NGRAMA + 1)).most_common(1)[0]
        if vezes >= NGRAMA_VEZES:
            return f"a mesma frase {vezes} vezes (\"{' '.join(n)[:80]}\")"
    if (h := len(HESITA.findall(j))) / (len(j) / 4) * 1000 > HESITA_POR_MIL:
        return f"hesitação em série ({h} \"wait/espera/na verdade\" em ~{len(j) // 4} tokens)"
    return ""


# ------------------------------------------------------------ mediana por modelo

CHAVE_MEDIANA = "raciocinio_por_modelo"
AMOSTRAS = 40


def registra_raciocinio(modelo: str, tokens: int) -> None:
    if not modelo or tokens <= 0:
        return
    from . import db
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE_MEDIANA)
        dados = dict(linha.value) if linha and isinstance(linha.value, dict) else {}
        dados[modelo] = (list(dados.get(modelo) or []) + [int(tokens)])[-AMOSTRAS:]
        if linha:
            linha.value = dados
        else:
            s.add(db.AppSetting(key=CHAVE_MEDIANA, value=dados))
        s.commit()


def mediana(modelo: str) -> int | None:
    from . import db
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE_MEDIANA)
    v = sorted(((linha.value or {}).get(modelo) or []) if linha and isinstance(linha.value, dict) else [])
    return v[len(v) // 2] if len(v) >= 5 else None


def pensa_demais(modelo: str, tokens: int) -> bool:
    """"Pensar muito" relativo ao modelo: mais de 3× a mediana dele (a parte B usa para chamar o juiz)."""
    m = mediana(modelo)
    return bool(m) and tokens > 3 * m

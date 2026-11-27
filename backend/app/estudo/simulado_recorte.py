"""Recorte de prova real e leitura de gabarito, sem modelo (portado do Estudos v1, calibrado com cadernos reais:
INEP, Objetivo, bancas de concurso).

- questoes_reais(texto): "QUESTÃO N" → enunciado → alternativas A–E → (resolução) → "Resposta: X". A resolução e
  a resposta do PDF nunca vão para o enunciado.
- ler_gabarito(texto): pares número-letra ("91 C", "91-C", tabela em linhas); anulada = "X".
- blocos(texto) / escolher(...): gabarito definitivo com vários cargos e versões num arquivo só.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .corpus import MARCA_PAGINA

LETRAS = "ABCDE"


FIGURA_NO_TEXTO = re.compile(r"(?i)\b(figura|gr[áa]fico|tabela|imagem|esquema|tirinha|charge|mapa|diagrama|ilustra|quadro|infogr[áa]fico)")

QUESTAO = re.compile(r"(?mi)^[ \t]*quest[ãa]o[ \t]*(\d{1,3})\b[^\n]*$")
ALT = re.compile(r"(?m)^[ \t]*\(?([A-E])\)[ \t]*(.*)$")
# caderno do INEP: o ícone da letra sai como a própria letra ("A A estimulação de leucócitos..."): vira "A) ..."
ALT_INEP = re.compile(r"(?m)^([ \t]*)([A-E])[ \t]+\2[ \t]+(?=\S)")
ALT_INEP_FIGURA = re.compile(r"(?m)^([ \t]*)([A-E])[ \t]+\2[ \t]*$")   # "A A" sozinho: a alternativa é imagem
# a linha é só o cabeçalho ("Resolução", "Comentário:"): um enunciado que começa com "Comentário de um leitor" não corta
RESOLUCAO = re.compile(r"(?mi)^[ \t]*(resolu[çc][ãa]o|coment[áa]rio|gabarito comentado)[ \t]*[:\-–]?[ \t]*$")
# "pptResposta: C" (sem espaço antes, no PDF do Objetivo): sem \b na frente, mas com ":" ou "-" depois; a letra é
# MAIÚSCULA ("resposta: a escolha certa..." não é gabarito)
RESPOSTA = re.compile(r"(?i:resposta|gabarito)(?:[ \t]+(?i:correta))?[ \t]*[:\-–][ \t]*(?:(?i:letra)[ \t]*)?\(?([A-E])\)?(?![a-zà-úA-Z])")
# "91 C", "91-C", "91) C", "91 | C", "169 Anulado": letra maiúscula (o "a" de "Questões de 91 a 135" não conta)
PAR_GABARITO = re.compile(r"(?<![\d,.])(\d{1,3})[ \t]*[-–.:)|]?[ \t]*\(?([A-E]|X|\*|(?i:anulad[ao]))\)?(?![\wà-ú])")
VARIAS_COLUNAS = re.compile(r"(?m)^[ \t]*\d{1,3}(?:[ \t]*\|?[ \t]*[A-E]){2,}[ \t]*$")   # "91 C B D A": uma cor por coluna

# ------------------------------------------------------------------ recorte (sem modelo)


def _repetidas(texto: str) -> set[str]:
    """Cabeçalho e rodapé que se repetem em toda página ("CN – 2.o dia – RESOLUÇÕES – Página 20"). "Resolução",
    "Resposta: C" e "A) 2" também se repetem, mas são da questão: ficam fora da conta."""
    # só as pontas de cada página (é onde mora cabeçalho e rodapé): frase repetida no meio é da questão
    pontas: Counter = Counter()
    paginas = re.split(r"(?m)^--- página \d+ ---\s*$", texto)
    for p in paginas:
        linhas = [l.strip() for l in p.splitlines()
                  if len(l.strip()) >= 12 and not (RESOLUCAO.match(l) or RESPOSTA.search(l) or ALT.match(l) or QUESTAO.match(l))]
        pontas.update({re.sub(r"\d+", "#", l) for l in linhas[:2] + linhas[-2:]})
    return {l for l, n in pontas.items() if n >= max(3, len(paginas) // 3)}


def _alternativas(trecho: str) -> tuple[int, list[str]]:
    """(onde começam, textos) da última sequência A, B, C, D(, E) do trecho; (-1, []) se não houver."""
    achadas = list(ALT.finditer(trecho))
    melhor: list[re.Match] = []
    atual: list[re.Match] = []
    for m in achadas:
        letra = m.group(1)
        if letra == "A":
            atual = [m]
        elif atual and LETRAS.index(letra) == len(atual):
            atual.append(m)
        else:
            continue
        if len(atual) >= 4:
            melhor = list(atual)
    if not melhor:
        return -1, []
    textos = []
    for i, m in enumerate(melhor):
        fim = melhor[i + 1].start() if i + 1 < len(melhor) else len(trecho)
        textos.append(re.sub(r"\s+", " ", (m.group(2) + "\n" + trecho[m.end():fim]).strip()))
    return melhor[0].start(), textos


def _ultima(alternativas: list[str], resto: str, cru: str = "") -> tuple[list[str], str]:
    """A última alternativa vai até o fim do trecho — e ali pode estar a resolução sem cabeçalho, ou o gabarito
    impresso no fim do caderno. Depois da 1ª linha dela só entra linha de CONTINUAÇÃO (a quebra do PDF começa em
    minúscula, número ou sinal), até ~2,5x a maior das outras; frase nova vira resolução, nunca alternativa."""
    if not alternativas or not cru:
        return alternativas, resto
    linhas = [l.strip() for l in cru.splitlines() if l.strip()]
    if not linhas:
        return alternativas, resto
    teto = max(120, int(2.5 * max((len(a) for a in alternativas[:-1]), default=60)))
    fica = [linhas[0]]
    for i, l in enumerate(linhas[1:], 1):
        continua = bool(re.match(r"[a-zà-ú0-9(\[{,.;:–\-+=%$]", l)) or fica[-1].endswith(("-", ","))
        if not continua or len(" ".join(fica + [l])) > teto or re.match(r"(?i)(gabarito|resolu[çc][ãa]o|coment[áa]rio)\b", l):
            return ([*alternativas[:-1], re.sub(r"\s+", " ", " ".join(fica)).strip()],
                    ("\n".join(linhas[i:]) + "\n" + resto).strip())
        fica.append(l)
    return [*alternativas[:-1], re.sub(r"\s+", " ", " ".join(fica)).strip()], resto


def questoes_reais(texto: str) -> list[dict]:
    """[{numero, pagina, enunciado, alternativas, oficial, resolucao}] pelo recorte "QUESTÃO N"."""
    texto = ALT_INEP_FIGURA.sub(r"\1\2) (alternativa na figura)", ALT_INEP.sub(r"\1\2) ", texto))
    texto = re.sub(r"(?m)^(--- página (\d+) ---)[ \t]*\n[ \t]*\2[ \t]*$", r"\1", texto)   # o número impresso da página
    repetidas = _repetidas(texto)
    paginas = [(m.start(), int(m.group(1))) for m in MARCA_PAGINA.finditer(texto)]
    cabecas = list(QUESTAO.finditer(texto))
    out: list[dict] = []
    vistos: set[int] = set()
    for i, c in enumerate(cabecas):
        numero = int(c.group(1))
        fim = cabecas[i + 1].start() if i + 1 < len(cabecas) else len(texto)
        bloco = "\n".join(l for l in texto[c.end():fim].splitlines()
                          if not MARCA_PAGINA.match(l.strip()) and re.sub(r"\d+", "#", l.strip()) not in repetidas)
        bloco = re.split(r"(?mi)^[ \t]*gabarito(?: oficial)?[ \t]*$", bloco)[0]   # o gabarito impresso no fim do caderno
        r = RESOLUCAO.search(bloco)
        pergunta, resolucao = (bloco[:r.start()], bloco[r.end():]) if r else (bloco, "")
        # "Resposta: C" dentro do que vai para quem resolve — antes da "Resolução" ou sem ela: corta ali
        dentro = RESPOSTA.search(pergunta)
        oficial = RESPOSTA.search(resolucao) or dentro
        if dentro:
            resolucao = pergunta[dentro.start():] + "\n" + resolucao
            pergunta = pergunta[:dentro.start()]
        inicio, alternativas = _alternativas(pergunta)
        cru = pergunta[list(ALT.finditer(pergunta))[-1].start():] if alternativas else ""
        cru = cru.split(")", 1)[1] if ")" in cru else cru   # sem o "E)" da frente
        alternativas, resolucao = _ultima(alternativas, resolucao, cru)
        enunciado = re.sub(r"[ \t]+", " ", pergunta[:inicio] if inicio >= 0 else pergunta).strip()
        if numero in vistos or len(enunciado) < 15:
            continue   # sumário, capa ("QUESTÃO 1 a 45") ou a mesma questão em outra cor de caderno
        vistos.add(numero)
        pagina = next((p for pos, p in reversed(paginas) if pos <= c.start()), 0)
        out.append({"numero": numero, "pagina": pagina, "enunciado": enunciado[:6000], "alternativas": alternativas,
                    "oficial": oficial.group(1).upper() if oficial else "",
                    "resolucao": re.sub(r"\n{3,}", "\n\n", RESPOSTA.sub("", resolucao)).strip()[:3000]})
    return out


def ler_gabarito(texto: str) -> dict[int, str]:
    """{número: letra} de um gabarito em texto ("91 C", "91-C", "91) C", "91 | C", "169 Anulado", tabela em linhas
    "91 92 93" + "C A B"). Anulada vira "X". Tabela com uma coluna por cor de caderno: a 1ª coluna (veja
    `aviso_gabarito`). Número repetido com letras diferentes: vale o que está numa sequência (91, 92, 93...)."""
    texto = texto or ""
    pares: list[tuple[int, str, int]] = []   # (número, letra, posição)
    for m in PAR_GABARITO.finditer(texto):
        letra = m.group(2).upper()
        pares.append((int(m.group(1)), letra if letra in LETRAS else "X", m.start()))
    linhas = [l.split() for l in texto.splitlines()]
    for a, b in zip(linhas, linhas[1:]):   # "91 92 93 94 95" em cima de "C A B D E"
        if len(a) >= 5 and len(a) == len(b) and all(x.isdigit() for x in a) and all(y in LETRAS + "X" for y in b):
            pares += [(int(x), y, -1) for x, y in zip(a, b)]
    if not pares:
        return {}
    em_sequencia = {i for i, (n, _, _) in enumerate(pares)
                    if any(abs(n - pares[j][0]) == 1 for j in (i - 1, i + 1) if 0 <= j < len(pares))}
    out: dict[int, str] = {}
    for i, (n, letra, _) in enumerate(pares):
        if n not in out or (i in em_sequencia and out.get(f"_{n}") is None):
            out[n] = letra
            if i in em_sequencia:
                out[f"_{n}"] = True   # type: ignore[index]
    return {k: v for k, v in out.items() if isinstance(k, int)}


def aviso_gabarito(texto: str) -> str:
    """Gabarito com várias cores de caderno por questão: avisa que usou a 1ª coluna."""
    if len(VARIAS_COLUNAS.findall(texto or "")) >= 5:
        return ("O gabarito tem uma coluna por cor de caderno; usei a primeira. Se a sua prova é de outra cor, "
                "cole só a coluna dela.")
    return ""


def parece_gabarito(texto: str) -> bool:
    """Material que é só o gabarito: muitos pares número-letra e pouco texto em volta."""
    pares = len(ler_gabarito(texto))
    return pares >= 20 and len(texto) < pares * 120


MIN_PARES = 5        # bloco com menos que isso é ruído (um "01: A" solto num cabeçalho)
FOLGA = 3            # concordância: a 2ª versão a até tanto da 1ª = escolha duvidosa (avisa)
MAX_IMAGENS = 8
LADO_IMAGEM = 1600   # gabarito é texto miúdo: o lado maior fica maior que o das figuras da prova

VERSAO = re.compile(r"(?i)^\s*(prova|tipo|caderno|vers[ãa]o|modelo)\s*[:\-–nº°.]*\s*(\d{1,2}|[A-H]\b|[a-zà-ú]+)\s*$")
CODIGO = re.compile(r"^\s*[A-Z]{0,3}\d{1,3}\s*[-–—:]\s*")   # "M20 - " antes do nome do cargo
PAR = re.compile(r"(?<![\d,.])\d{1,3}\s*[-–.:)|]?\s*\(?(?:[A-E]|X|\*|(?i:anulad[ao]))\)?(?![\wà-ú])")

def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _caixa_alta(l: str) -> bool:
    letras = [c for c in l if c.isalpha()]
    return len(letras) >= 4 and sum(c.isupper() for c in letras) >= 0.8 * len(letras)


def blocos(texto: str) -> list[dict]:
    """O gabarito em blocos por cargo e versão. Cabeçalho de página repetido (o nome do concurso em toda folha) não
    vira cargo: linha em caixa alta que aparece 3 vezes ou mais é cabeçalho."""
    linhas = [l.strip() for l in (texto or "").splitlines()]
    cheias = [l for l in linhas if l]
    # cabeçalho = repete 3+ vezes e nunca vem logo antes de "PROVA n" ou de respostas (o nome do cargo vem)
    antes_de_bloco = {a for a, b in zip(cheias, cheias[1:]) if VERSAO.match(b) or len(PAR.findall(b)) >= 2}
    repetidas = {l for l, n in Counter(cheias).items() if n >= 3 and l not in antes_de_bloco}
    out: list[dict] = []
    cargo = prova = ultimo = ""
    buf: list[str] = []

    def fecha():
        if buf:
            pares = ler_gabarito("\n".join(buf))
            if len(pares) >= MIN_PARES:
                out.append({"id": f"{len(out) + 1}", "cargo": cargo, "prova": prova, "pares": pares})
            buf.clear()

    for l in linhas:
        if not l:
            continue
        if v := VERSAO.match(l):   # antes do filtro de repetidas: "PROVA 1" se repete em todo cargo
            fecha()
            if ultimo:
                cargo, ultimo = ultimo, ""
            prova = v.group(2).upper() if len(v.group(2)) <= 2 else v.group(2).capitalize()
            continue
        if len(PAR.findall(l)) >= 2:
            if ultimo and not buf:   # pares direto depois de um nome (gabarito sem "PROVA n"): o nome é o cargo
                cargo, ultimo = ultimo, ""
            buf.append(l)
            continue
        if l in repetidas:
            continue
        if _caixa_alta(l) and not re.search(r"(?i)gabarito|p[aá]gina\s+\d", l):
            if buf:   # nome novo depois de pares: o bloco anterior acabou, e é outro cargo
                fecha()
                prova = ""
            ultimo = CODIGO.sub("", l).strip(" -–")[:120]
    fecha()
    if len(out) <= 1:   # sem cabeçalhos: o arquivo inteiro, como sempre foi
        pares = ler_gabarito(texto)
        return [{"id": "1", "cargo": out[0]["cargo"] if out else "", "prova": out[0]["prova"] if out else "", "pares": pares}] if pares else []
    return out


def rotulo(b: dict) -> str:
    return " · ".join(x for x in (b.get("cargo"), f"Prova {b['prova']}" if b.get("prova") else "") if x) or "gabarito"


def _tipo_da_prova(texto_prova: str) -> str:
    """O "TIPO 1" / "PROVA 2" da capa, quando ele é texto (na Access é desenho: aí vale a concordância)."""
    capa = (texto_prova or "")[:4000]
    m = re.search(r"(?im)^\s*(?:tipo|caderno|prova|vers[ãa]o)\s*[:\-–nº°.]*\s*(\d{1,2}|[A-H])\s*$", capa)
    return m.group(1).upper() if m else ""


def cargos_da_prova(bs: list[dict], texto_prova: str, cargo_edital: str = "") -> set[str]:
    """Os cargos do gabarito cujo nome está na capa da prova (ou é o do edital). O nome mais comprido vence: "Analista
    de Sistemas" não pode casar com um "Analista" genérico."""
    capa = _norm((texto_prova or "")[:6000]) + " " + _norm(cargo_edital)
    achados = [(len(_norm(b["cargo"])), b["cargo"]) for b in bs if b["cargo"] and _norm(b["cargo"]) and f" {_norm(b['cargo'])} " in f" {capa} "]
    if not achados:
        return set()
    maior = max(n for n, _ in achados)
    return {c for n, c in achados if n == maior}


def concordancia(b: dict, ia: dict[int, str]) -> tuple[int, int]:
    """(iguais, comparadas): só as questões que a IA respondeu e o bloco tem com letra (anulada fica de fora)."""
    comuns = [n for n, l in ia.items() if l and b["pares"].get(n) in tuple("ABCDE")]
    return sum(1 for n in comuns if b["pares"][n] == ia[n]), len(comuns)


def escolher(bs: list[dict], texto_prova: str, cargo_edital: str = "", ia: dict[int, str] | None = None,
             forcado: str = "") -> dict:
    """{bloco, opcoes, motivo, aviso}. Sem resposta da IA ainda (ia=None) e sem como decidir pelo texto, bloco=None:
    quem chama resolve primeiro e chama de novo com `ia`."""
    if not bs:
        return {"bloco": None, "opcoes": [], "motivo": "", "aviso": ""}
    ia = ia or {}
    opcoes = []
    for b in bs:
        iguais, de = concordancia(b, ia)
        opcoes.append({"id": b["id"], "rotulo": rotulo(b), "cargo": b["cargo"], "prova": b["prova"], "iguais": iguais, "de": de})
    if forcado and (b := next((x for x in bs if x["id"] == forcado), None)):
        return {"bloco": b, "opcoes": opcoes, "motivo": "escolhido por você", "aviso": ""}
    if len(bs) == 1:
        return {"bloco": bs[0], "opcoes": opcoes, "motivo": "", "aviso": ""}
    cargos = cargos_da_prova(bs, texto_prova, cargo_edital)
    candidatos = [b for b in bs if b["cargo"] in cargos] or bs
    motivo = f"cargo pelo nome na prova ({', '.join(sorted(cargos))})" if cargos else ""
    aviso = "" if cargos else "Não achei o nome do cargo da prova no gabarito: escolhi pela concordância com a IA. Confira o bloco."
    tipo = _tipo_da_prova(texto_prova)
    if tipo and (b := next((x for x in candidatos if x["prova"] == tipo), None)):
        return {"bloco": b, "opcoes": opcoes, "motivo": " · ".join(x for x in (motivo, f"Prova {tipo} na capa") if x), "aviso": aviso}
    if len(candidatos) == 1:
        return {"bloco": candidatos[0], "opcoes": opcoes, "motivo": motivo, "aviso": aviso}
    if not ia:
        return {"bloco": None, "opcoes": opcoes, "motivo": motivo, "aviso": aviso}
    por = sorted(candidatos, key=lambda b: concordancia(b, ia)[0], reverse=True)
    (a, de), (b2, _) = concordancia(por[0], ia), concordancia(por[1], ia)
    motivo = " · ".join(x for x in (motivo, f"versão pela concordância com a IA ({a} de {de})") if x)
    if a - b2 < FOLGA:
        aviso = (aviso + " " if aviso else "") + (f"Escolha duvidosa: {rotulo(por[0])} bate {a} e {rotulo(por[1])} bate {b2}. "
                                                    "Confira qual é a sua prova e troque o bloco se preciso.")
    return {"bloco": por[0], "opcoes": opcoes, "motivo": motivo, "aviso": aviso}




def depende_figura(q: dict) -> bool:
    """A questão cita gráfico, tabela ou figura (o dado está no desenho) ou tem as alternativas desenhadas."""
    alts = q.get("alternativas") or []
    return not alts or "(alternativa na figura)" in alts or bool(FIGURA_NO_TEXTO.search(q.get("enunciado") or ""))

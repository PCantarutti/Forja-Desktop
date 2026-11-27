"""O corpus: fontes em texto integral, picadas em trechos numerados, com busca BM25 e conferência de citação."""
from __future__ import annotations

import math
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path

from .. import config, documentos
from ..tools import ToolError
from . import base

TRECHO = 1_200        # alvo de caracteres por trecho
TRECHO_MAX = 1_800    # parágrafo maior que isto é cortado por frase
MIN_CITACAO = 6       # palavras: citação mais curta que isto não ancora nada ("é a lei")
SEP = "·"
ID_TRECHO = re.compile(r"\bF(\d+)\s*[·.:\-–]\s*(\d+)\b")

EXT_TEXTO = (".txt", ".md", ".html", ".htm", ".json")
EXT_IMAGEM = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

STOP = set("""a o e é de do da dos das em no na nos nas um uma uns umas por para com sem que se ao aos à às ou
como mais mas foi ser são ele ela eles elas seu sua seus suas isso este esta esse essa pelo pela pelos pelas
entre sobre também quando onde qual quais the and for não sim já há até ter tem""".split())


# ------------------------------------------------------------------ texto

def norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def palavras(s: str) -> list[str]:
    return [w for w in norm(s).split() if len(w) > 2 and w not in STOP]


FRASE = re.compile(r"(?<=[.!?;:])\s+")


def picar(texto: str) -> list[str]:
    """Trechos de ~TRECHO caracteres que respeitam parágrafo (e frase, no parágrafo gigante). Nada se perde:
    a junção dos trechos tem todas as palavras do texto."""
    pars: list[str] = []
    for p in (texto or "").splitlines():   # cada linha é um parágrafo (o web.ler e o pypdf já quebram assim)
        p = re.sub(r"[ \t]+", " ", p).strip()
        if not p:
            continue
        if len(p) <= TRECHO_MAX:
            pars.append(p)
            continue
        atual = ""
        for f in FRASE.split(p):
            if atual and len(atual) + len(f) > TRECHO:
                pars.append(atual)
                atual = ""
            while len(f) > TRECHO_MAX:   # frase sem pontuação (tabela, lista colada): corta seco
                pars.append(f[:TRECHO])
                f = f[TRECHO:]
            atual = f"{atual} {f}".strip()
        if atual:
            pars.append(atual)
    out, atual = [], ""
    for p in pars:
        if atual and len(atual) + len(p) > TRECHO:
            out.append(atual)
            atual = ""
        atual = f"{atual}\n{p}".strip()
    if atual:
        out.append(atual)
    return out


def achar_citacao(citacao: str, trecho: str) -> bool:
    """A citação está no trecho? Aceita diferença de acento, caixa, pontuação e espaço, e até 10% das palavras
    trocadas (o modelo erra um artigo ao copiar). Paráfrase não passa."""
    c, t = norm(citacao).split(), norm(trecho).split()
    if len(c) < MIN_CITACAO:
        return False
    if f" {' '.join(c)} " in f" {' '.join(t)} ":
        return True
    alvo, n = Counter(c), len(c)
    for i in range(0, max(1, len(t) - n + 1)):
        janela = Counter(t[i:i + n])
        if sum((alvo & janela).values()) >= 0.9 * n:
            return True
    return False


# ------------------------------------------------------------------ fontes no disco

def _arq(conv_id: int, fid: str) -> Path:
    return base.pasta(conv_id) / "fontes" / f"{fid}.txt"


def texto_da_fonte(conv_id: int, fid: str) -> str:
    a = _arq(conv_id, fid)
    return a.read_text("utf-8") if a.exists() else ""


_CACHE: dict[tuple[int, str], tuple[float, list[str]]] = {}


def trechos_da_fonte(conv_id: int, fid: str) -> list[str]:
    a = _arq(conv_id, fid)
    if not a.exists():
        return []
    m = a.stat().st_mtime
    chave = (conv_id, fid)
    if (c := _CACHE.get(chave)) and c[0] == m:
        return c[1]
    t = picar(a.read_text("utf-8"))
    _CACHE[chave] = (m, t)
    return t


def trecho(conv_id: int, tid: str) -> str:
    """"F3·12" -> o texto do trecho ("" se não existe)."""
    m = ID_TRECHO.search(tid or "")
    if not m:
        return ""
    t = trechos_da_fonte(conv_id, f"F{m.group(1)}")
    i = int(m.group(2)) - 1
    return t[i] if 0 <= i < len(t) else ""


def id_trecho(fid: str, i: int) -> str:
    return f"{fid}{SEP}{i + 1}"


def gravar_fonte(conv_id: int, texto: str, tipo: str, titulo: str, url: str = "", arquivo: str = "",
                 topicos: list[str] | None = None, status: str = "util", motivo: str = "") -> dict:
    texto = (texto or "").strip()
    with base.mudar(conv_id) as est:
        fid = f"F{base.proximo(est, 'fonte')}"
        a = _arq(conv_id, fid)
        a.parent.mkdir(parents=True, exist_ok=True)
        a.write_text(texto, "utf-8")
        f = {"id": fid, "tipo": tipo, "titulo": (titulo or url or arquivo or fid)[:200], "url": url, "arquivo": arquivo,
             "topicos": topicos or [], "chars": len(texto), "trechos": len(picar(texto)), "status": status,
             "motivo": motivo, "criada": time.time()}
        est["fontes"].append(f)
    return f


def uteis(est: dict, topico: str | None = None) -> list[dict]:
    """Fontes em uso. Com tópico: as dele e as gerais (arquivo sem tópico vale para todos)."""
    return [f for f in est["fontes"] if f["status"] == "util"
            and (topico is None or not f["topicos"] or topico in f["topicos"])]


# ------------------------------------------------------------------ busca

def _bm25(consulta: list[str], docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    if not docs:
        return []
    n, media = len(docs), sum(map(len, docs)) / len(docs) or 1
    df = Counter(w for d in docs for w in set(d))
    out = []
    for d in docs:
        tf, s = Counter(d), 0.0
        for w in set(consulta):
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(d) / media))
        out.append(s)
    return out


def todos_trechos(conv_id: int, fontes: list[dict]) -> list[dict]:
    return [{"id": id_trecho(f["id"], i), "fonte": f["id"], "titulo": f["titulo"], "texto": t}
            for f in fontes for i, t in enumerate(trechos_da_fonte(conv_id, f["id"]))]


def buscar(conv_id: int, est: dict, consulta: str, topico: str | None = None, k: int = 8,
           teto_chars: int | None = None, excluir: set | None = None) -> list[dict]:
    """Os k trechos mais parecidos com a consulta (BM25), entre as fontes úteis do tópico. Com teto_chars,
    corta quando a soma passa do teto. Sem nada no tópico, cai para o corpus inteiro."""
    cands = todos_trechos(conv_id, uteis(est, topico)) or (todos_trechos(conv_id, uteis(est)) if topico else [])
    if excluir:
        cands = [c for c in cands if c["id"] not in excluir]
    notas = _bm25(palavras(consulta), [palavras(c["texto"]) for c in cands])
    ordem = sorted(range(len(cands)), key=lambda i: -notas[i])
    out, soma = [], 0
    for i in ordem[:k * 3]:
        if notas[i] <= 0 or len(out) >= k:
            break
        if teto_chars and soma + len(cands[i]["texto"]) > teto_chars and out:
            break
        out.append({**cands[i], "nota": round(notas[i], 2)})
        soma += len(cands[i]["texto"])
    return out


def bloco(trechos: list[dict]) -> str:
    """Os trechos como o modelo os lê: cada um com o id que ele deve citar."""
    return "\n\n".join(f"[{t['id']}] (fonte: {t['titulo'][:80]})\n{t['texto']}" for t in trechos)


# ------------------------------------------------------------------ arquivos enviados

def _html_para_texto(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", html))


GLIFO_ALTERNATIVA = re.compile(r"/L5784([0-4])")   # fonte de "bolinhas" A–E de alguns simulados impressos
MARCA_PAGINA = re.compile(r"^--- página (\d+) ---", re.M)


def glifos(texto: str) -> str:
    return GLIFO_ALTERNATIVA.sub(lambda m: "\n" + "ABCDE"[int(m.group(1))] + ") ", texto)


def _pouco_texto(texto: str) -> bool:
    """PDF escaneado que solta só marca d'água e número de página: precisa de OCR."""
    paginas = max(1, len(MARCA_PAGINA.findall(texto)))
    linhas = [l.strip() for l in texto.splitlines() if l.strip() and not MARCA_PAGINA.match(l.strip())]
    repetidas = {l for l in set(linhas) if linhas.count(l) >= max(2, paginas // 2)}
    return sum(len(l) for l in linhas if l not in repetidas) / paginas < 300


def extrair(nome: str, dados: bytes) -> str:
    """Texto de um arquivo enviado (PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, HTML ou foto)."""
    import tempfile
    ext = Path(nome).suffix.lower()
    if ext in EXT_TEXTO:
        t = dados.decode("utf-8", "ignore")
        return _html_para_texto(t) if ext in (".html", ".htm") else t
    if ext in EXT_IMAGEM:
        from .. import ocr
        if not ocr.disponivel():
            raise ToolError("Não há OCR nesta máquina para ler imagem.")
        return "\n\n".join(t for t in ocr.de_imagens([dados]) if t)
    if ext not in documentos.EXTRATORES:
        raise ToolError(f"Formato {ext or '(sem extensão)'} não é lido. Use PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, HTML ou imagem.")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"arquivo{ext}"
        p.write_bytes(dados)
        texto = documentos.extrair(p) or ""
        if ext == ".pdf" and _pouco_texto(texto):
            lido = documentos.extrair_ocr(p) or ""
            if len(lido) > 2 * len(texto):
                texto = lido
    return glifos(texto)


def adicionar_arquivo(conv_id: int, nome: str, dados: bytes | None = None, texto: str | None = None,
                      tipo: str = "arquivo") -> dict:
    base.conferir_conv(conv_id)
    nome = Path(nome or "").name[:120] or "texto colado.txt"
    if texto is None:
        if not dados:
            raise ToolError("Arquivo vazio.")
        if len(dados) > config.MAX_DOC_BYTES:
            raise ToolError(f"Arquivo maior que {config.MAX_DOC_BYTES // 1_000_000} MB.")
        texto = extrair(nome, dados)
    texto = (texto or "").strip()
    if len(texto) < 50:
        raise ToolError("Não saiu texto desse arquivo (escaneado sem OCR?).")
    return gravar_fonte(conv_id, texto, tipo, Path(nome).stem, arquivo=nome)

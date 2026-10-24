"""Lembretes falados: "Forja, me lembre de lavar a louça às 14h". Ficam em DATA_DIR/lembretes.json (sobrevivem a
reiniciar o Forja). Na hora: notificação do Windows (a tela do PC vê o `disparado` pelo /api/activity), push no
celular (mobile.avisa) e a voz do assistente, se ele estiver ligado. Perdido com o Forja fechado sai assim que abrir.

entende(texto) decide (tests/test_lembretes.py); quando(texto, agora) lê o horário falado em português."""
from __future__ import annotations

import asyncio
import json
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timedelta

from . import config, mobile

ARQUIVO = config.DATA_DIR / "lembretes.json"
_lock = threading.Lock()
disparado = {"n": 0, "titulo": "", "texto": "", "id": ""}  # o último aviso: a tela do PC mostra a notificação quando n muda


def _norm(t: str) -> str:
    # NFKD + tirar acento não muda o tamanho do texto em português: a posição no normalizado vale no original
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# ------------------------------------------------------------------ o horário falado

NUM = {"uma": 1, "um": 1, "duas": 2, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
       "nove": 9, "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20, "trinta": 30, "quarenta": 40,
       "quarenta e cinco": 45, "cinquenta": 50, "meia": 30}
_N = r"\d{1,3}|" + "|".join(sorted(NUM, key=len, reverse=True))
RELATIVO = re.compile(r"\b(?:daqui\s+a|daqui|em|dentro\s+de|depois\s+de)\s+(?:uns\s+|umas\s+|cerca\s+de\s+|uns\s+|tipo\s+)?"
                      r"(?:(?P<meia>meia)\s+hora|(?P<n>" + _N + r")\s*"
                      r"(?P<u>minutos?|mins?|horas?|hrs?|h)(?P<emeia>\s+e\s+meia)?)\b")
HORA = re.compile(r"(?P<as>\b(?:as|a|ao|para\s+as|para\s+o|pras|pelas|la\s+pelas)\s+)?\b(?P<h>\d{1,2}|" + "|".join(k for k in NUM if NUM[k] <= 12) +
                  r"|meio[\s-]?dia|meia[\s-]?noite)(?:(?P<u>\s*(?::|h(?=\d|\b)|hs\b|hrs\b|horas?\b))\s*(?:e\s+)?"
                  r"(?P<m>\d{2}|meia|quinze|trinta|quarenta\s+e\s+cinco)?(?:\s*min(?:utos)?\b)?)?"
                  r"(?:\s+(?:da|de|a)\s+(?P<p>manha|tarde|noite|madrugada))?(?![\w:])")
DIA = re.compile(r"\b(?P<dia>depois\s+de\s+amanha|amanha|hoje|anteontem|ontem)\b|\b(?:na\s+|no\s+|nesta\s+|nessa\s+|proxim[ao]\s+|n[ao]\s+proxim[ao]\s+)?"
                 r"(?P<sem>segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:[\s-]feira)?\b")
PERIODO = re.compile(r"\b(?:de|a|pela|na|hoje\s+a)\s+(?P<p>manha|tarde|noite)\b")
SEMANA = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
HORA_DO_PERIODO = {"manha": 9, "tarde": 15, "noite": 20}


def _n(t: str) -> int:
    return int(t) if t.isdigit() else NUM[re.sub(r"\s+", " ", t)]


def quando(texto: str, agora: datetime | None = None) -> tuple[datetime | None, list[tuple[int, int]]]:
    """(data e hora, trechos do texto que diziam o horário). None quando não há horário."""
    exato = (agora or datetime.now()).replace(microsecond=0)
    agora = exato.replace(second=0)
    c = _norm(texto)
    if m := RELATIVO.search(c):
        if m["meia"]:
            delta = timedelta(minutes=30)
        else:
            n = _n(m["n"])
            delta = timedelta(hours=n) if m["u"].startswith("h") else timedelta(minutes=n)
            delta += timedelta(minutes=30) if m["emeia"] else timedelta()
        return exato + delta, [m.span()]  # "daqui a 1 minuto" é daqui a 60 s, não na virada do minuto
    trechos = []
    dia = None
    if d := DIA.search(c):
        trechos.append(d.span())
        if d["dia"]:
            dia = agora.date() + timedelta(days={"hoje": 0, "amanha": 1, "ontem": -1, "anteontem": -2}.get(d["dia"], 2))
        else:
            falta = (SEMANA.index(d["sem"]) - agora.weekday()) % 7
            dia = agora.date() + timedelta(days=falta or 7)
    hora = None
    for m in HORA.finditer(c):
        if not (m["as"] or m["u"] or m["p"]) or (m["h"] in NUM and not (m["as"] or m["p"])):
            continue  # "tomar 2 comprimidos": número solto não é hora
        h = m["h"]
        h = 12 if h.startswith("meio") else 0 if h.startswith("meia") else _n(h)
        if h > 23:
            continue
        minuto = _n(m["m"]) if m["m"] else 0
        p = m["p"]
        if p in ("tarde", "noite") and h < 12:
            h += 12
        hora = (h, minuto, bool(p) or h > 12 or m["h"].startswith(("meio", "meia")))
        trechos.append(m.span())
        break
    if hora is None and (p := PERIODO.search(c)) and dia:
        hora = (HORA_DO_PERIODO[p["p"]], 0, True)
        trechos.append(p.span())
    if hora is None:
        if not dia:
            return None, []
        hora = (9, 0, True)  # "amanhã" sem hora: de manhã
    h, minuto, certo = hora
    alvo = datetime.combine(dia or agora.date(), datetime.min.time()).replace(hour=h, minute=minuto)
    if dia is None and alvo <= agora:
        if not certo and h < 12 and alvo + timedelta(hours=12) > agora:
            alvo += timedelta(hours=12)  # "às 2" depois do meio-dia: as 14h
        else:
            alvo += timedelta(days=1)
    return alvo, trechos


def quando_da_ferramenta(texto: str, agora: datetime | None = None) -> datetime | None:
    """O `quando` que o modelo passou: como a pessoa disse ("amanhã às 8") ou já em ISO ("2026-10-24T08:00")."""
    texto = (texto or "").strip()
    try:
        alvo = datetime.fromisoformat(texto.replace(" ", "T", 1) if re.match(r"\d{4}-\d\d-\d\d \d", texto) else texto)
        if alvo.tzinfo:
            alvo = alvo.astimezone().replace(tzinfo=None)
        return alvo if alvo > (agora or datetime.now()) else None
    except ValueError:
        return quando(texto, agora)[0] if texto else None


def tem_hora(texto: str) -> bool:
    """O texto disse um horário (e não só o dia)? "sexta às 10" sim; "sexta" não (evento do dia inteiro)."""
    c = _norm(texto or "")
    return bool(RELATIVO.search(c) or any(m["as"] or m["u"] or m["p"] for m in HORA.finditer(c)))


def dia_dito(d, agora: datetime | None = None) -> str:
    """"hoje", "amanhã", "na sexta", "no dia 30/10"."""
    hoje = (agora or datetime.now()).date()
    dias = (d - hoje).days
    if dias == 0:
        return "hoje"
    if dias == 1:
        return "amanhã"
    if dias in (-1, -2):
        return "ontem" if dias == -1 else "anteontem"
    if 1 < dias <= 7:
        dia = ["na segunda", "na terça", "na quarta", "na quinta", "na sexta", "no sábado", "no domingo"][d.weekday()]
        return dia.replace("na ", "na próxima ").replace("no ", "no próximo ") if dias == 7 else dia
    return f"no dia {d.day:02d}/{d.month:02d}"


def dito(alvo: datetime, agora: datetime | None = None) -> str:
    """Como falar a hora: "às 14h", "às 14h30, amanhã", "na sexta às 10h"."""
    agora = agora or datetime.now()
    hora = f"às {alvo.hour}h{alvo.minute:02d}" if alvo.minute else f"às {alvo.hour}h"
    dias = (alvo.date() - agora.date()).days
    if dias == 0:
        return hora
    if dias == 1:
        return f"amanhã {hora}"
    if dias < 7:
        return f"{['na segunda', 'na terça', 'na quarta', 'na quinta', 'na sexta', 'no sábado', 'no domingo'][alvo.weekday()]} {hora}"
    return f"no dia {alvo.day:02d}/{alvo.month:02d} {hora}"


# ------------------------------------------------------------------ o pedido

GATILHO = re.compile(r"\b(?:me\s+lembr\w*|lembr\w*[\s-]+me|(?:cri\w*|coloc\w*|bot\w*|fa[zc]\w*|marc\w*|agend\w*|poe|anot\w*)\s+"
                     r"(?:um\s+)?lembrete|lembrete)\b(?P<con>\s*(?:de|que|para|pra|do|da|dos|das)\b|\s*[:,])?")
LISTAR = re.compile(r"\b(?:quais|que)\s+(?:sao\s+)?(?:os\s+)?(?:meus\s+)?(?:proximos\s+)?lembretes\b|\btenho\s+(?:algum\s+|alguns\s+|de\s+)?lembretes?\b|"
                    r"\b(?:lista|fala|diz)\w*\s+(?:os\s+)?(?:meus\s+)?lembretes\b")
CANCELAR = re.compile(r"\b(?:cancel\w*|esquec\w*|apag\w*|tir\w*|remov\w*|desmarc\w*|exclu\w*|delet\w*)\s+(?P<todos>todos\s+)?(?:(?:o|os|do|dos|meus|aquele|esse|este)\s+)?"
                      r"lembretes?\b(?:\s+(?:de|da|do|das|dos|para|pra|que)\s+(?P<alvo>.+))?")
CONECTIVO = re.compile(r"^(?:de|que|para|pra|do|da|e|:|,|-)\s+|\s+(?:de|que|para|pra|e|,|as|a)$")


def entende(comando: str, agora: datetime | None = None) -> tuple[str, object] | None:
    """("criar", (quando, texto)) | ("pergunta", texto) sem horário | ("listar", "") | ("cancelar", alvo) | None."""
    c = _norm(comando)
    if LISTAR.search(c):
        return "listar", ""
    if m := CANCELAR.search(c):
        return "cancelar", "*" if m["todos"] else (comando[m.start("alvo"):m.end("alvo")] if m["alvo"] else "").strip(" .?!")
    g = GATILHO.search(c)
    if not g:
        return None
    alvo, trechos = quando(comando, agora)
    # o que lembrar: o pedido sem o gatilho e sem o horário
    cortes = sorted(trechos + [g.span()])
    texto, fim = "", 0
    for a, b in cortes:
        texto += comando[fim:a] + " "
        fim = max(fim, b)
    texto += comando[fim:]
    texto = re.sub(r"\s{2,}", " ", texto).strip(" .,!?:;")
    for _ in range(3):
        texto = CONECTIVO.sub("", texto).strip(" .,!?:;")
    texto = re.sub(r"^(?:por\s+favor|forja)[\s,]+", "", texto, flags=re.I).strip(" .,!?:;")
    if alvo is None:
        # "me lembra quem foi Einstein" é pergunta, não lembrete: sem horário, só vale com "lembrete" ou "me lembre de"
        return ("pergunta", texto) if "lembrete" in g[0] or g["con"] else None
    return "criar", (alvo, texto)


# ------------------------------------------------------------------ guardar e tocar

def lista() -> list[dict]:
    try:
        itens = json.loads(ARQUIVO.read_text("utf-8"))
    except (OSError, ValueError):
        itens = []
    return sorted(itens, key=lambda x: x["quando"])


def _grava(itens: list[dict]) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQUIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(itens, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(ARQUIVO)


def criar(alvo: datetime, texto: str) -> dict:
    item = {"id": uuid.uuid4().hex[:10], "quando": alvo.isoformat(timespec="seconds"), "texto": texto[:200],
            "criado": datetime.now().isoformat(timespec="seconds")}
    with _lock:
        _grava(lista() + [item])
    return item


def apagar(ids: set[str]) -> list[dict]:
    with _lock:
        itens = [x for x in lista() if x["id"] not in ids]
        _grava(itens)
    return itens


def parecidos(alvo: str, itens: list[dict]) -> list[dict]:
    """Os lembretes que o "cancela o lembrete da louça" quis dizer: alguma palavra (4+ letras) no texto."""
    if alvo == "*":
        return itens
    palavras = [w for w in re.findall(r"[a-z0-9]+", _norm(alvo)) if len(w) >= 4]
    if not palavras:
        return itens[:1] if len(itens) == 1 else []
    return [x for x in itens if any(w in _norm(x["texto"]) for w in palavras)]


def avisa(titulo: str, texto: str, falado: str, fala, id_: str = "") -> None:
    """Notificação do Windows (a tela do PC vê `disparado` pelo /api/activity), push no celular e a voz. Também o
    resumo do dia e os avisos de e-mail saem por aqui."""
    disparado.update(n=disparado["n"] + 1, titulo=titulo, texto=texto, id=id_)
    threading.Thread(target=mobile.avisa, args=(titulo, texto), daemon=True).start()
    try:
        if falado:
            fala(falado)
    except Exception as e:  # voz desligada ou caindo: a notificação já saiu
        print(f"Forja: aviso sem voz: {e}", flush=True)


def _toca(item: dict, fala, atraso_min: int) -> None:
    texto = item["texto"] or "Você pediu para eu lembrar agora."
    titulo = "Lembrete" if atraso_min < 5 else f"Lembrete (era para {datetime.fromisoformat(item['quando']):%H:%M})"
    avisa(titulo, texto, f"Lembrete: {texto}.", fala, item["id"])
    print("Forja: lembrete tocou", flush=True)  # sem o texto: o log não guarda o que a pessoa disse


def vence(agora: datetime | None = None) -> list[dict]:
    """Tira da lista e devolve os que já deram a hora."""
    agora = agora or datetime.now()
    with _lock:
        itens = lista()
        hora = [x for x in itens if datetime.fromisoformat(x["quando"]) <= agora]
        if hora:
            _grava([x for x in itens if x not in hora])
    return hora


async def vigia(fala) -> None:
    """Laço do backend (assistente.ao_subir): a cada 10 s toca os que venceram."""
    while True:
        try:
            agora = datetime.now()
            for item in vence(agora):
                _toca(item, fala, int((agora - datetime.fromisoformat(item["quando"])).total_seconds() // 60))
        except Exception as e:  # a vigia nunca morre por uma volta ruim
            print(f"Forja: lembretes: {e.__class__.__name__}: {e}", flush=True)
        await asyncio.sleep(10)

"""Métricas: uma linha por evento (E0 criou o JSONL do bench; a E10 guarda sempre, numa tabela).

Cada evento vai para:
- a tabela `metricas` do SQLite (sempre; é o que a tela de métricas agrega);
- `FORJA_DATA/logs/agente.jsonl`, JSON por linha com rotação (log estruturado para ler fora do app);
- `FORJA_METRICAS=<arquivo>`, se definido (o bench da E0 lê este).

Nada aqui derruba o agente: qualquer falha ao gravar é engolida.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

_trava = threading.Lock()
_log: logging.Logger | None = None
_AUX: dict = {}          # última chamada auxiliar desde o último turno do principal (cache perdido, E10)
PRINCIPAIS = ("agente", "maestro")
RETENCAO_DIAS = 60


def _logger() -> logging.Logger | None:
    global _log
    if _log is None:
        try:
            from . import config
            pasta = config.DATA_DIR / "logs"
            pasta.mkdir(parents=True, exist_ok=True)
            lg = logging.getLogger("forja.agente")
            lg.propagate = False
            if not lg.handlers:
                h = RotatingFileHandler(pasta / "agente.jsonl", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
                h.setFormatter(logging.Formatter("%(message)s"))
                lg.addHandler(h)
            lg.setLevel(logging.INFO)
            _log = lg
        except Exception:
            return None
    return _log


def registra(tipo: str, **dados) -> None:
    if tipo == "rota" and dados.get("papel") not in ("principal",):
        _AUX.update(papel=dados.get("papel"), caminho=dados.get("caminho"))
    if tipo == "llm" and dados.get("papel") in PRINCIPAIS:
        tm = dados.get("timings") or {}
        if isinstance(tm, dict) and tm.get("prompt_n") is not None:
            # prompt_n = processados agora; cache_n = reaproveitados. Muito processado depois de uma auxiliar
            # é o cache do principal que ela derrubou.
            dados["reprocessados"], dados["cache_n"] = int(tm.get("prompt_n") or 0), int(tm.get("cache_n") or 0)
        if _AUX:
            dados["aux_antes"] = dict(_AUX)
            _AUX.clear()
    linha = json.dumps({"t": round(time.time(), 3), "tipo": tipo, **dados}, ensure_ascii=False, default=str)
    if arq := os.getenv("FORJA_METRICAS"):
        try:
            with _trava, open(arq, "a", encoding="utf-8") as f:
                f.write(linha + "\n")
        except OSError:
            pass
    try:
        if lg := _logger():
            lg.info(linha)
    except Exception:
        pass
    try:
        from . import db
        conv = dados.get("conv")
        with db.session() as s:
            s.add(db.Metrica(tipo=tipo[:30], conv_id=conv if isinstance(conv, int) else None,
                             dados=json.loads(linha)))
            s.commit()
    except Exception:
        pass


def poda() -> int:
    """Linhas mais velhas que RETENCAO_DIAS saem (chamada na abertura do app)."""
    try:
        from . import db
        limite = datetime.now(timezone.utc) - timedelta(days=RETENCAO_DIAS)
        with db.session() as s:
            n = s.query(db.Metrica).filter(db.Metrica.t < limite).delete(synchronize_session=False)
            s.commit()
        return n
    except Exception:
        return 0


def _pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 1) if b else None


def resumo(dias: int = 7) -> dict:
    """O que a tela de métricas mostra, dos últimos `dias`."""
    from . import db
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    with db.session() as s:
        linhas = [(m.tipo, m.dados or {}) for m in s.query(db.Metrica).filter(db.Metrica.t >= desde)]
        tarefas = s.query(db.Task).filter(db.Task.updated_at >= desde).all()
        tentativas = {t.id: t.attempt_count for t in tarefas}
        status = Counter(t.status for t in tarefas)
        primeira = sum(1 for t in tarefas if t.status == "completed" and t.attempt_count <= 1)
        cards_ia = [(i.tipo, i.status) for i in s.query(db.Issue).filter(db.Issue.origem == "varredura-ia",
                                                                         db.Issue.updated_at >= desde)]
    por = defaultdict(list)
    for tipo, d in linhas:
        por[tipo].append(d)

    fechadas = status["completed"] + status["needs_human"] + status["failed"]
    principais = [d for d in por["llm"] if d.get("papel") in PRINCIPAIS and d.get("reprocessados") is not None]
    cache = sum(d.get("cache_n", 0) for d in principais)
    reproc = sum(d.get("reprocessados", 0) for d in principais)
    derrubadas = [d for d in principais if d.get("aux_antes") and d.get("reprocessados", 0) > 2000]
    ferramentas = Counter()
    falhas = Counter()
    for d in por["ferramenta"]:
        ferramentas[d.get("nome")] += 1
        if d.get("status") not in ("ok",):
            falhas[d.get("nome")] += 1
    return {
        "dias": dias,
        "tarefas": {"total": len(tarefas), "concluidas": status["completed"], "needs_human": status["needs_human"],
                    "primeira_tentativa_pct": _pct(primeira, status["completed"]),
                    "needs_human_pct": _pct(status["needs_human"], fechadas),
                    "tentativas_media": round(sum(tentativas.values()) / len(tentativas), 2) if tentativas else None},
        "cache": {"respostas": len(principais), "hit_pct": _pct(cache, cache + reproc),
                  "reprocessados": reproc, "derrubado_por_auxiliar": len(derrubadas),
                  "auxiliares": Counter(d["aux_antes"].get("papel") for d in derrubadas).most_common(5)},
        "rotas": Counter(f"{d.get('papel')} → {d.get('caminho')}" for d in por["rota"]).most_common(12),
        "trocas": {"n": len(por["troca"]), "segundos": round(sum(float(d.get("segundos") or 0) for d in por["troca"]), 1)},
        "ferramentas_falham": [{"nome": n, "falhas": f, "total": ferramentas[n], "pct": _pct(f, ferramentas[n])}
                               for n, f in falhas.most_common(8)],
        "recuperacao": Counter(str(d.get("nivel")) for d in por["recuperacao"]),
        "juiz": Counter(d.get("veredito") for d in por["juiz"]),
        "filtro_abortos": len(por["filtro_raciocinio"]),
        "exploracoes": len(por["exploracao"]),
        "varredura_ia": {tipo: {"aceitos": sum(1 for t, st in cards_ia if t == tipo and st not in ("novo", "rejeitado")),
                                "rejeitados": sum(1 for t, st in cards_ia if t == tipo and st == "rejeitado")}
                         for tipo in sorted({t for t, _ in cards_ia})},
    }

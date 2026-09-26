"""Registro de métricas em JSONL, uma linha por evento (E0: base do bench; a E10 vira tabela).

Desligado por padrão. `FORJA_METRICAS=<arquivo>` liga: cada chamada ao LLM do agente/Maestro/Worker (com os
timings do llama-server: prompt_n, cache_n, tempos) e cada troca de modelo viram uma linha.
"""
from __future__ import annotations

import json
import os
import threading
import time

_trava = threading.Lock()


def registra(tipo: str, **dados) -> None:
    arq = os.getenv("FORJA_METRICAS")
    if not arq:
        return
    linha = json.dumps({"t": round(time.time(), 3), "tipo": tipo, **dados}, ensure_ascii=False, default=str)
    try:
        with _trava, open(arq, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass  # métrica nunca derruba o agente

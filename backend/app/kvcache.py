"""Cache do prompt em disco (E4): salvar o KV de um slot do llama-server e restaurar quando a conversa volta.

Sem isto, trocar de conversa com `-np 1`, descarregar o modelo por ociosidade ou trocar de modelo joga fora
o cache, e a próxima resposta reprocessa o prompt inteiro (a E0 mediu 4 s para 10k tokens num modelo comum e
82 s para 20k no Qwen3.6). Restaurar custa 3–12% disso — **onde o llama.cpp consegue**:
- modelo comum (só atenção completa): sim;
- janela deslizante (SWA): só com `--swa-full`, que guarda o KV de todas as camadas (mais VRAM);
- híbrido (recorrente + atenção, família Qwen3.5/3.6/3.8): não; o restore responde ok e a requisição seguinte
  reprocessa tudo, nem com `--ctx-checkpoints`. Nesses o recurso fica desligado, com o motivo.

Um arquivo por (modelo, conversa): `<hash do modelo>-<conversa>.bin` + `.json` com a chave de validade. O
llama-server só aceita nome simples (sem pasta) no `filename`.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import httpx

from . import config, metricas

PASTA = config.DATA_DIR / "kvcache"
_DONO: dict[int, int] = {}   # slot -> conversa cujo cache está nele agora (vale para o processo atual)
_PID: dict[str, int] = {"pid": 0}


def pasta() -> Path:
    PASTA.mkdir(parents=True, exist_ok=True)
    return PASTA


# ------------------------------------------------------------------ o que o modelo aceita

def tipo_de_cache(path: str) -> str:
    """comum | swa | hibrido, pelas camadas do GGUF."""
    from . import localai
    try:
        tipos = {c.get("kind") for c in localai.gguf_info(path).get("layers") or []}
    except Exception:
        return "comum"
    return "hibrido" if "recurrent" in tipos else "swa" if "swa" in tipos else "comum"


def suportado(path: str, params: dict) -> tuple[bool, str]:
    """(dá para salvar e restaurar, por que não)."""
    if not ligado():
        return False, "cache em disco desligado nas configurações"
    tipo = tipo_de_cache(path)
    if tipo == "hibrido":
        return False, ("modelo híbrido (camadas recorrentes): o llama.cpp não restaura o estado recorrente de um "
                       "slot salvo, então restaurar custaria o mesmo que reprocessar (medido na E0)")
    if tipo == "swa" and not params.get("swa_full"):
        return False, ("modelo com janela deslizante: só restaura com 'Guardar a janela inteira' (--swa-full) "
                       "ligado, que usa mais VRAM para o cache")
    return True, ""


def ligado() -> bool:
    return bool(getattr(config, "CACHE_DISCO", True)) and limite_bytes() > 0


def limite_bytes() -> int:
    return int(float(getattr(config, "CACHE_DISCO_GB", 4.0) or 0) * 2**30)


# ------------------------------------------------------------------ chave e arquivos

def _estado() -> dict:
    from . import localai
    return localai.status()


def chave(st: dict | None = None) -> dict | None:
    """O que o KV depende: o GGUF (caminho + tamanho + mtime) e os parâmetros que mudam o cache."""
    st = st or _estado()
    if not st.get("running") or not st.get("path"):
        return None
    p = Path(st["path"])
    try:
        info = p.stat()
    except OSError:
        return None
    prm = st.get("params") or {}
    return {"gguf": str(p), "bytes": info.st_size, "mtime": int(info.st_mtime), "ctx": int(prm.get("ctx") or 0),
            "k": prm.get("cache_type_k") or "f16", "v": prm.get("cache_type_v") or "f16",
            "unificado": bool(prm.get("kv_unified")), "swa_full": bool(prm.get("swa_full"))}


def _nome(k: dict, conv: int) -> str:
    h = hashlib.sha1(json.dumps(k, sort_keys=True).encode()).hexdigest()[:16]
    return f"{h}-{int(conv)}.bin"


def _post(caminho: str, corpo: dict, timeout: float = 120) -> dict:
    r = httpx.post(f"http://127.0.0.1:{config.LOCAL_PORT}{caminho}", json=corpo, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _vigente() -> tuple[dict, dict] | None:
    """(estado do servidor, chave) quando dá para usar o cache em disco agora; None se não dá."""
    st = _estado()
    k = chave(st)
    if not k or not suportado(st["path"], st.get("params") or {})[0]:
        return None
    if _PID["pid"] != st.get("pid"):  # processo novo: nenhum slot tem o cache de ninguém
        _PID["pid"] = int(st.get("pid") or 0)
        _DONO.clear()
    return st, k


def salvar(conv: int, slot: int) -> dict | None:
    """Grava o KV do slot como o cache de `conv`. Falha vira log, nunca exceção."""
    v = _vigente()
    if not v:
        return None
    _, k = v
    nome = _nome(k, conv)
    t0 = time.monotonic()
    try:
        r = _post(f"/slots/{slot}?action=save", {"filename": nome})
    except Exception as e:
        metricas.registra("kvcache", acao="salvar", conv=conv, ok=False, erro=str(e)[:200])
        return None
    (pasta() / nome).with_suffix(".json").write_text(json.dumps({**k, "conv": conv, "salvo": time.time()}),
                                                    encoding="utf-8")
    out = {"n": r.get("n_saved"), "ms": round((time.monotonic() - t0) * 1000)}
    metricas.registra("kvcache", acao="salvar", conv=conv, ok=True, **out)
    poda()
    return out


def restaurar(conv: int, slot: int) -> dict | None:
    """Põe no slot o cache salvo de `conv`, se existir e a chave bater. Chave diferente: apaga sem tentar."""
    v = _vigente()
    if not v:
        return None
    _, k = v
    nome = _nome(k, conv)
    arq = pasta() / nome
    if not arq.exists():
        _apaga_de_outra_chave(conv, nome)
        return None
    t0 = time.monotonic()
    try:
        r = _post(f"/slots/{slot}?action=restore", {"filename": nome})
    except Exception as e:  # arquivo de outro build/parâmetro: segue sem cache e não tenta de novo
        arq.unlink(missing_ok=True)
        arq.with_suffix(".json").unlink(missing_ok=True)
        metricas.registra("kvcache", acao="restaurar", conv=conv, ok=False, erro=str(e)[:200])
        return None
    arq.touch()  # LRU: usado agora
    out = {"n": r.get("n_restored"), "ms": round((time.monotonic() - t0) * 1000)}
    metricas.registra("kvcache", acao="restaurar", conv=conv, ok=True, **out)
    return out


def _apaga_de_outra_chave(conv: int, atual: str) -> None:
    """Cache desta conversa feito com outro modelo/parâmetros não serve mais: sai do disco."""
    for a in pasta().glob(f"*-{int(conv)}.bin"):
        if a.name != atual:
            a.unlink(missing_ok=True)
            a.with_suffix(".json").unlink(missing_ok=True)


def assume(conv: int, slot: int) -> str:
    """Antes da requisição do principal de `conv` no `slot`: guarda o cache de quem estava lá e traz o desta
    conversa. Devolve o que fez ("" = nada), para o log e a métrica."""
    if not _vigente():
        return ""
    dono = _DONO.get(slot)
    if dono == conv:
        return ""
    feito = []
    if dono is not None and salvar(dono, slot):
        feito.append(f"salvou a conversa {dono}")
    if restaurar(conv, slot):
        feito.append(f"restaurou a conversa {conv}")
    _DONO[slot] = conv
    return "; ".join(feito)


def salvar_todos() -> int:
    """Antes de descarregar ou trocar de modelo: cada slot com dono vai para o disco."""
    if not _vigente():
        return 0
    n = sum(1 for slot, conv in list(_DONO.items()) if salvar(conv, slot))
    _DONO.clear()
    return n


# ------------------------------------------------------------------ espaço em disco

def uso() -> dict:
    arqs = list(pasta().glob("*.bin"))
    return {"bytes": sum(a.stat().st_size for a in arqs), "conversas": len({a.stem.split("-", 1)[-1] for a in arqs}),
            "limite": limite_bytes(), "ligado": ligado()}


CAMPOS = ("ctx", "cache_type_k", "cache_type_v", "kv_unified", "swa_full")  # mudar um deles invalida o cache


def _do_modelo(path: str) -> list[Path]:
    alvo, out = os.path.normcase(os.path.normpath(str(path))), []
    for j in pasta().glob("*.json"):
        try:
            if os.path.normcase(os.path.normpath(json.loads(j.read_text("utf-8")).get("gguf") or "")) == alvo:
                out += [j, j.with_suffix(".bin")]
        except (OSError, ValueError):
            continue
    return out


def bytes_do_modelo(path: str) -> int:
    return sum(a.stat().st_size for a in _do_modelo(path) if a.suffix == ".bin" and a.exists())


def apagar_modelo(path: str) -> int:
    """Parâmetro que muda o KV (CAMPOS) ou GGUF trocado: o cache salvo daquele modelo não restaura mais."""
    n = bytes_do_modelo(path)
    for a in _do_modelo(path):
        a.unlink(missing_ok=True)
    return n


def poda() -> None:
    """Passou do limite: apaga os menos usados primeiro (mtime = último save/restore)."""
    lim = limite_bytes()
    arqs = sorted(pasta().glob("*.bin"), key=lambda a: a.stat().st_mtime)
    total = sum(a.stat().st_size for a in arqs)
    while arqs and total > lim:
        a = arqs.pop(0)
        total -= a.stat().st_size
        a.unlink(missing_ok=True)
        a.with_suffix(".json").unlink(missing_ok=True)


def apagar_conversa(conv: int) -> None:
    for a in pasta().glob(f"*-{int(conv)}.*"):
        a.unlink(missing_ok=True)
    for slot, dono in list(_DONO.items()):
        if dono == conv:
            _DONO.pop(slot, None)


def limpar() -> int:
    n = 0
    for a in pasta().glob("*"):
        a.unlink(missing_ok=True)
        n += 1
    _DONO.clear()
    return n


def cede(slot: int) -> None:
    """Uma chamada auxiliar vai usar o slot do principal (-np 1): guarda o cache do dono antes, para a
    próxima volta dele restaurar em vez de reprocessar."""
    if not _vigente():
        return
    if (dono := _DONO.pop(slot, None)) is not None:
        salvar(dono, slot)

"""Downloads e trabalhos com progresso (runtimes, modelos, geração de imagem).

Um único registro em memória para tudo que demora e precisa aparecer com barra no painel.
O painel faz poll de GET /api/local; não há SSE aqui de propósito — são poucos jobs e o
painel já faz poll por outros motivos.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
import zipfile
from pathlib import Path

import httpx

_JOBS: dict[str, dict] = {}
_lock = threading.Lock()
KEEP_DONE = 300     # segundos que um job concluído continua na lista
KEEP_FAILED = 7200  # erro fica muito mais tempo: some antes de a pessoa ler é pior que poluir a tela


def _new(kind: str, name: str) -> dict:
    job = {"id": uuid.uuid4().hex[:8], "kind": kind, "name": name, "done": 0, "total": 0,
           "status": "running", "error": "", "detail": "", "result": None, "started": time.time(),
           "finished": 0.0}
    with _lock:
        _JOBS[job["id"]] = job
    return job


def create(kind: str, name: str) -> dict:
    """Job controlado por outro módulo (ex.: imagegen), que chama update/finish."""
    return _new(kind, name)


def update(job_id: str, done: int | None = None, total: int | None = None, detail: str | None = None) -> None:
    with _lock:
        job = _JOBS.get(job_id)
        if not job:
            return
        if done is not None:
            job["done"] = done
        if total is not None:
            job["total"] = total
        if detail is not None:
            job["detail"] = detail


def finish(job_id: str, error: str = "", result=None) -> None:
    with _lock:
        job = _JOBS.get(job_id)
        if not job:
            return
        if job["status"] == "running":
            job["status"] = "erro" if error else "pronto"
        job["error"] = error
        job["result"] = result
        job["finished"] = time.time()


def cancelled(job_id: str) -> bool:
    with _lock:
        job = _JOBS.get(job_id)
        return bool(job and job["status"] == "cancelado")


def cancel(job_id: str) -> None:
    with _lock:
        job = _JOBS.get(job_id)
        if job and job["status"] == "running":
            job["status"] = "cancelado"
            job["finished"] = time.time()


def dismiss(job_id: str) -> None:
    """Tira o job da lista. Serve para o erro que já foi lido: ele fica 2h, mas some quando se quer."""
    with _lock:
        job = _JOBS.get(job_id)
        if job and job["status"] != "running":
            del _JOBS[job_id]


def list_jobs() -> list[dict]:
    now = time.time()
    with _lock:
        for jid, job in list(_JOBS.items()):
            limite = KEEP_FAILED if job["status"] == "erro" else KEEP_DONE
            if job["finished"] and now - job["finished"] > limite:
                del _JOBS[jid]
        return sorted((dict(j) for j in _JOBS.values()), key=lambda j: j["started"])


# ------------------------------------------------------------------ download

def _fetch(url: str, dest: Path, job: dict, base: int, total_all: int) -> None:
    """Baixa uma URL para `dest` (via .part), somando `base` no progresso do job."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code} em {url}")
            size = int(r.headers.get("content-length") or 0)
            update(job["id"], total=total_all or (base + size))
            got = 0
            with open(part, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    if cancelled(job["id"]):
                        raise _Cancelled()
                    f.write(chunk)
                    got += len(chunk)
                    update(job["id"], done=base + got)
        if cancelled(job["id"]):  # cancelado entre o último chunk e o fim: nada de arquivo final
            raise _Cancelled()
    except BaseException:
        part.unlink(missing_ok=True)  # cancelado, rede caiu, disco cheio: não deixa .part no meio dos modelos
        raise
    os.replace(part, dest)


class _Cancelled(Exception):
    pass


def _run(job: dict, urls: list[str], dest: Path, extract: bool) -> None:
    try:
        total = 0
        for url in urls:  # HEAD para a barra fazer sentido com vários arquivos (cuda = zip + cudart)
            try:
                h = httpx.head(url, follow_redirects=True, timeout=20)
                total += int(h.headers.get("content-length") or 0)
            except httpx.HTTPError:
                total = 0
                break
        update(job["id"], total=total)
        base = 0
        for url in urls:
            name = url.rsplit("/", 1)[-1].split("?")[0]
            target = (dest / name) if extract else dest
            update(job["id"], detail=name)
            _fetch(url, target, job, base, total)
            base = job["done"]
            if extract:
                update(job["id"], detail=f"extraindo {name}")
                _unzip(target, dest)
                target.unlink(missing_ok=True)
        finish(job["id"], result=str(dest))
    except _Cancelled:
        pass
    except Exception as e:  # rede, disco, zip corrompido — tudo vira erro visível no painel
        finish(job["id"], error=f"{e.__class__.__name__}: {e}")


def _unzip(zip_path: Path, dest: Path) -> None:
    """Extrai achatando o diretório raiz do zip (os do llama.cpp trazem build/bin/...)."""
    with zipfile.ZipFile(zip_path) as z:
        members = [m for m in z.namelist() if not m.endswith("/")]
        for m in members:
            out = dest / Path(m).name if _flat(members) else dest / m
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(out, "wb") as f:
                f.write(src.read())


def _flat(members: list[str]) -> bool:
    """True quando o zip tem subpastas só de empacotamento (nenhum .exe/.dll na raiz)."""
    return not any("/" not in m for m in members)


def start(kind: str, name: str, urls: list[str], dest: Path, extract: bool = False) -> dict:
    """Baixa em thread e devolve o job já registrado. `extract`: dest é pasta, zips são abertos."""
    job = _new(kind, name)
    threading.Thread(target=_run, args=(job, urls, dest, extract), daemon=True).start()
    return job

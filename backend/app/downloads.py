"""Downloads e trabalhos com progresso (runtimes, modelos, geração de imagem).

Um único registro em memória para tudo que demora e precisa aparecer com barra no painel.
O painel faz poll de GET /api/local; não há SSE aqui de propósito — são poucos jobs e o
painel já faz poll por outros motivos.
"""
from __future__ import annotations

import os
import shutil
import subprocess
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

def _fetch(url: str, dest: Path, job: dict, base: int, total_all: int, headers: dict | None = None) -> None:
    """Baixa uma URL para `dest` (via .part), somando `base` no progresso do job.

    Se já existe um .part, continua de onde parou (Range). Modelo tem dezenas de GB: recomeçar do
    zero porque a rede piscou é o tipo de coisa que faz a pessoa desistir.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    ja_tem = part.stat().st_size if part.exists() else 0
    cabecalho = dict(headers or {})
    if ja_tem:
        cabecalho["Range"] = f"bytes={ja_tem}-"
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=120),
                          headers=cabecalho) as r:
            if r.status_code == 416:  # já estava inteiro
                ja_tem = 0 if not part.exists() else ja_tem
                raise _Pronto()
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code} em {url}")
            retomou = r.status_code == 206
            if ja_tem and not retomou:
                ja_tem = 0  # servidor ignorou o Range: recomeça, mas só depois de saber disso
            size = int(r.headers.get("content-length") or 0) + (ja_tem if retomou else 0)
            update(job["id"], total=total_all or (base + size), detail=dest.name + (" (retomando)" if retomou else ""))
            _espaco(dest.parent, size - ja_tem)
            got = ja_tem
            update(job["id"], done=base + got)
            with open(part, "ab" if retomou else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    if cancelled(job["id"]):
                        raise _Cancelled()
                    f.write(chunk)
                    got += len(chunk)
                    update(job["id"], done=base + got)
        if cancelled(job["id"]):  # cancelado entre o último chunk e o fim: nada de arquivo final
            raise _Cancelled()
    except _Pronto:
        pass
    except _Cancelled:
        part.unlink(missing_ok=True)  # cancelou de propósito: limpa o rastro
        raise
    except BaseException:
        raise  # rede caiu/disco cheio: o .part FICA, e a próxima tentativa continua dele
    os.replace(part, dest)


class _Pronto(Exception):
    """O servidor disse que o arquivo já está inteiro no .part."""


def _espaco(pasta: Path, precisa: int) -> None:
    """Sem espaço, o download só falha lá na frente, depois de 20 GB baixados."""
    if precisa <= 0:
        return
    try:
        livre = shutil.disk_usage(pasta).free
    except OSError:
        return
    if livre < precisa + (500 << 20):
        raise RuntimeError(f"Espaço insuficiente em {pasta}: faltam "
                           f"{(precisa + (500 << 20) - livre) / 2 ** 30:.1f} GB")


class _Cancelled(Exception):
    pass


def _run(job: dict, urls: list[str], dest: Path, extract: bool, headers: dict | None = None) -> None:
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
        # Extrai ao lado e só troca no fim: extrair por cima parava no primeiro .dll em uso e deixava
        # o runtime com metade das DLLs de cada versão (o llama.cpp b11146 sobre o 11135, 23/09/2026).
        destino = dest.with_name(dest.name + ".novo") if extract else dest
        if extract:
            shutil.rmtree(destino, ignore_errors=True)
            destino.mkdir(parents=True, exist_ok=True)
        for url in urls:
            name = url.rsplit("/", 1)[-1].split("?")[0]
            target = (destino / name) if extract else dest
            update(job["id"], detail=name)
            _fetch(url, target, job, base, total, headers)
            base = job["done"]
            if extract:
                update(job["id"], detail=f"extraindo {name}")
                _unzip(target, destino)
                target.unlink(missing_ok=True)
        if extract:
            update(job["id"], detail="instalando")
            _trocar(destino, dest)
        finish(job["id"], result=str(dest))
    except _Cancelled:
        pass
    except Exception as e:  # rede, disco, zip corrompido — tudo vira erro visível no painel
        finish(job["id"], error=f"{e.__class__.__name__}: {e}")


TRAVADO_TENTATIVAS = 10  # x 0,5 s: o `--list-devices` do painel prende as DLLs por menos de 1 s


def _travado(f: Path) -> bool:
    """Windows não deixa escrever num .dll/.exe carregado por algum processo."""
    try:
        with open(f, "r+b"):
            return False
    except PermissionError:
        return True
    except FileNotFoundError:
        return False


def _trocar(novo: Path, dest: Path) -> None:
    """Põe os arquivos de `novo` em `dest` só se nenhum dos que serão substituídos estiver em uso.

    Tudo ou nada: se algum continuar travado, nada é tocado e o runtime antigo segue inteiro.
    """
    arquivos = [f for f in novo.rglob("*") if f.is_file()]
    for _ in range(TRAVADO_TENTATIVAS):
        presos = [f.relative_to(novo) for f in arquivos if _travado(dest / f.relative_to(novo))]
        if not presos:
            break
        time.sleep(0.5)
    else:
        shutil.rmtree(novo, ignore_errors=True)
        raise PermissionError(f"{presos[0]} está em uso por outro programa (um modelo carregado, ou o Forja "
                              "instalado usando o mesmo runtime). Descarregue o modelo e tente de novo; "
                              "o runtime atual não foi alterado.")
    for f in arquivos:
        alvo = dest / f.relative_to(novo)
        alvo.parent.mkdir(parents=True, exist_ok=True)
        os.replace(f, alvo)
    shutil.rmtree(novo, ignore_errors=True)


def _unzip(zip_path: Path, dest: Path) -> None:
    """Extrai achatando o diretório raiz do zip (os do llama.cpp trazem build/bin/...). O .7z (ComfyUI portátil)
    mantém a árvore e só perde a pasta raiz; quem abre é o `tar` do Windows (libarchive lê 7z)."""
    if zip_path.suffix.lower() == ".7z":
        tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"  # o do Git (GNU) não lê 7z
        r = subprocess.run([str(tar), "-xf", str(zip_path), "-C", str(dest), "--strip-components", "1"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise OSError(f"tar não abriu {zip_path.name}: {(r.stderr or r.stdout).strip()[:300]}")
        return
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


def start(kind: str, name: str, urls: list[str], dest: Path, extract: bool = False,
          headers: dict | None = None) -> dict:
    """Baixa em thread e devolve o job já registrado. `extract`: dest é pasta, zips são abertos."""
    job = _new(kind, name)
    threading.Thread(target=_run, args=(job, urls, dest, extract, headers), daemon=True).start()
    return job

"""Validações V1–V6 da E0 (cache em disco, KV e paralelo), direto no llama-server que o Forja usa.

    resources\\python\\python.exe scripts\\bench_kvcache.py [--so V1,V2]

Resultado em docs/bench/2026-09-kvcache.json (chave "validacoes"). Sobe o llama-server numa porta própria
(8079), com os parâmetros de carga do local.json do usuário, e nunca encosta no Forja aberto.

Modelos desta máquina (Arc B580 12 GB + 30 GB RAM):
- comum (atenção completa): qwen2.5-coder-1.5b — controle das V1/V2;
- janela deslizante (SWA 1024): gemma-4-12b — V3;
- híbrido (recorrente + atenção a cada 4 camadas): Qwen3.6-35B-A3B, o principal do usuário — V3, V5, V6.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
DADOS_USUARIO = Path(os.environ.get("APPDATA", "")) / "Forja"
EXE = DADOS_USUARIO / "runtimes" / "llama" / "vulkan" / "llama-server.exe"
PORTA = 8079
URL = f"http://127.0.0.1:{PORTA}"
PASTA = RAIZ.parent / ".devbench" / "kv"
SLOTS = PASTA / "slots"
SAIDA = RAIZ / "docs" / "bench" / "2026-09-kvcache.json"
M = {
    "comum": Path(r"D:\Modelos-IA\lmstudio\Qwen\Qwen2.5-Coder-1.5B-Instruct-GGUF\qwen2.5-coder-1.5b-instruct-q8_0.gguf"),
    "swa": Path(r"D:\Modelos-IA\lmstudio\unsloth\gemma-4-12b-it-GGUF\gemma-4-12b-it-Q4_K_M.gguf"),
    "hibrido": Path(r"D:\Modelos-IA\lmstudio\lmstudio-community\Qwen3.6-35B-A3B-GGUF\Qwen3.6-35B-A3B-Q4_K_M.gguf"),
}
USUARIO = json.loads((DADOS_USUARIO / "local.json").read_text(encoding="utf-8")).get("models", {})


def log(*a):
    print(f"[{datetime.now():%H:%M:%S}]", *a, flush=True)


# ------------------------------------------------------------------ servidor

class Servidor:
    def __init__(self, modelo: str, ctx: int, extra: list[str] | None = None, usar_kv_usuario: bool = True):
        p = USUARIO.get(str(M[modelo]), {})
        self.argv = [str(EXE), "-m", str(M[modelo]), "--host", "127.0.0.1", "--port", str(PORTA), "-c", str(ctx),
                     "-ngl", "999", "-fa", "on", "--slot-save-path", str(SLOTS), "--no-webui"]
        for chave, flag in (("threads", "-t"), ("n_cpu_moe", "--n-cpu-moe")):
            if int(p.get(chave) or 0):
                self.argv += [flag, str(p[chave])]
        if usar_kv_usuario:
            for chave, flag in (("cache_type_k", "--cache-type-k"), ("cache_type_v", "--cache-type-v")):
                if p.get(chave) and p[chave] != "f16":
                    self.argv += [flag, p[chave]]
        self.argv += extra or []
        self.modelo, self.ctx = modelo, ctx

    def __enter__(self):
        SLOTS.mkdir(parents=True, exist_ok=True)
        self.log = open(PASTA / f"llama-{self.modelo}.log", "ab")
        self.log.write(("\n" + " ".join(self.argv) + "\n").encode())
        t0 = time.monotonic()
        self.proc = subprocess.Popen(self.argv, cwd=EXE.parent, stdout=self.log, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        while time.monotonic() - t0 < 900:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server saiu (código {self.proc.returncode}); veja {self.log.name}")
            try:
                if httpx.get(f"{URL}/health", timeout=2).status_code == 200:
                    self.carga_s = round(time.monotonic() - t0, 1)
                    log(f"{self.modelo} no ar em {self.carga_s}s (ctx {self.ctx}) {' '.join(self.argv[13:])}")
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("llama-server não subiu em 15 min")

    def __exit__(self, *exc):
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
        self.proc.wait()
        self.log.close()
        time.sleep(2)


def _post(caminho: str, corpo: dict, timeout: float = 1800) -> tuple[dict, float]:
    t0 = time.monotonic()
    r = httpx.post(f"{URL}{caminho}", json=corpo, timeout=timeout)
    s = time.monotonic() - t0
    if r.status_code >= 400:
        raise RuntimeError(f"{caminho}: HTTP {r.status_code} {r.text[:300]}")
    return r.json(), s


_TEXTO: str | None = None


def tokens(n: int, semente: int = 0) -> list[int]:
    """n tokens de texto real (o código do próprio Forja), a partir de um deslocamento: prompts distintos."""
    global _TEXTO
    if _TEXTO is None:
        _TEXTO = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                           for p in sorted((RAIZ / "backend" / "app").glob("*.py")))
    ini = (semente * 37_000) % max(1, len(_TEXTO) - 400_000)
    toks = _post("/tokenize", {"content": _TEXTO[ini:ini + max(200_000, n * 6)]})[0]["tokens"]
    if len(toks) < n:
        raise RuntimeError(f"texto curto: {len(toks)} tokens")
    return toks[:n]


FRASE = "\n\n# Pergunta: resuma em uma linha o que este código faz.\n"


def completa(prompt, n_predict: int = 4, slot: int = 0) -> dict:
    r, s = _post("/completion", {"prompt": prompt, "n_predict": n_predict, "cache_prompt": True, "id_slot": slot,
                                 "temperature": 0, "seed": 1})
    t = r.get("timings") or {}
    return {"prompt_n": t.get("prompt_n"), "cache_n": t.get("cache_n", r.get("tokens_cached")),
            "prompt_ms": round(t.get("prompt_ms") or 0), "predito_n": t.get("predicted_n"),
            "tok_s": round(t.get("predicted_per_second") or 0, 1), "parede_s": round(s, 2)}


def salva(nome: str, slot: int = 0) -> dict:
    r, s = _post(f"/slots/{slot}?action=save", {"filename": nome})
    return {"ok": True, "ms": round((r.get("timings") or {}).get("save_ms") or s * 1000),
            "mb": round((SLOTS / nome).stat().st_size / 2**20, 1), "n": r.get("n_saved")}


def restaura(nome: str, slot: int = 0) -> dict:
    try:
        r, s = _post(f"/slots/{slot}?action=restore", {"filename": nome})
    except RuntimeError as e:
        return {"ok": False, "erro": str(e)}
    return {"ok": True, "ms": round((r.get("timings") or {}).get("restore_ms") or s * 1000), "parede_ms": round(s * 1000),
            "n": r.get("n_restored")}


def apaga(slot: int = 0) -> None:
    try:
        _post(f"/slots/{slot}?action=erase", {})
    except RuntimeError:
        pass


def frase() -> list[int]:
    return _post("/tokenize", {"content": FRASE})[0]["tokens"]


# ------------------------------------------------------------------ validações

def v1(modelo: str) -> dict:
    """Salvar, reiniciar o servidor, restaurar e mandar o mesmo prompt + uma frase: o cache vale?"""
    out = {}
    for rotulo, extra in (("kv_unificado", ["--kv-unified"]), ("controle_np1", ["-np", "1"])):
        try:
            with Servidor(modelo, 32768, extra) as s:
                tk = tokens(10_000)
                primeira = completa(tk)
                gravado = salva(f"v1-{modelo}-{rotulo}.bin")
            with Servidor(modelo, 32768, extra):
                volta = restaura(f"v1-{modelo}-{rotulo}.bin")
                segunda = completa(tk + frase())
            cache = segunda["cache_n"] or 0
            out[rotulo] = {"primeira": primeira, "salvo": gravado, "restaurado": volta, "segunda": segunda,
                           "cache_da_segunda_pct": round(100 * cache / len(tk), 1),
                           "passa": bool(volta.get("ok")) and cache >= 0.95 * len(tk)}
        except Exception as e:
            out[rotulo] = {"erro": str(e), "passa": False}
        log("V1", modelo, rotulo, {k: v for k, v in out[rotulo].items() if k in ("passa", "cache_da_segunda_pct", "erro")})
    return out


def v2(modelo: str) -> dict:
    """Processar do zero x restaurar do disco, com 5k, 10k e 20k tokens."""
    out = {}
    with Servidor(modelo, 32768, ["--kv-unified", "-np", "1"]) as s:
        out["carga_s"] = s.carga_s
        extra = frase()
        for n in (5_000, 10_000, 20_000):
            apaga()
            tk = tokens(n, semente=n)
            zero = completa(tk)
            gravado = salva(f"v2-{modelo}-{n}.bin")
            apaga()
            volta = restaura(f"v2-{modelo}-{n}.bin")
            depois = completa(tk + extra)
            custo = (volta.get("parede_ms") or 0) + depois["prompt_ms"]
            out[str(n)] = {"do_zero_ms": zero["prompt_ms"], "restaurar_ms": volta.get("parede_ms"),
                           "prompt_depois_ms": depois["prompt_ms"], "cache_depois": depois["cache_n"],
                           "restaurar_total_ms": custo, "bin_mb": gravado["mb"],
                           "razao": round(custo / zero["prompt_ms"], 3) if zero["prompt_ms"] else None,
                           "restaurou": volta.get("ok")}
            log("V2", modelo, n, out[str(n)])
    r10 = out["10000"]["razao"]
    out["passa"] = r10 is not None and r10 <= 0.30 and bool(out["10000"]["restaurou"])
    return out


def v5() -> dict:
    """Maestro (híbrido) -> Worker (SWA) -> Maestro: tempo até a 1ª resposta do Maestro com e sem o cache em disco."""
    tk = None
    with Servidor("hibrido", 65536, ["--kv-unified", "-np", "1"]) as s:
        carga_maestro = s.carga_s
        tk = tokens(20_000, semente=5)
        inicial = completa(tk)
        gravado = salva("v5-maestro.bin")
    with Servidor("swa", 32768, ["--kv-unified", "-np", "1"]) as s:
        carga_worker = s.carga_s
        worker = completa(tokens(5_000, semente=9), n_predict=64)
    with Servidor("hibrido", 65536, ["--kv-unified", "-np", "1"]) as s:
        recarga_sem = s.carga_s
        sem = completa(tk + frase())
    with Servidor("hibrido", 65536, ["--kv-unified", "-np", "1"]) as s:
        recarga_com = s.carga_s
        volta = restaura("v5-maestro.bin")
        com = completa(tk + frase())
    ttft_sem = sem["prompt_ms"]
    ttft_com = (volta.get("parede_ms") or 0) + com["prompt_ms"]
    return {"carga_s": {"maestro": carga_maestro, "worker": carga_worker, "maestro_de_novo": recarga_sem,
                        "maestro_de_novo_2": recarga_com},
            "prompt_inicial_ms": inicial["prompt_ms"], "salvo": gravado, "worker": worker,
            "sem_cache": {"primeira_resposta_ms": ttft_sem, "cache_n": sem["cache_n"]},
            "com_cache_em_disco": {"restaurar": volta, "prompt_ms": com["prompt_ms"], "cache_n": com["cache_n"],
                                   "primeira_resposta_ms": ttft_com},
            "ciclo_troca_s": {"sem_cache": round(carga_worker + recarga_sem + ttft_sem / 1000, 1),
                              "com_cache": round(carga_worker + recarga_com + ttft_com / 1000, 1)}}


def _vram() -> dict:
    """VRAM livre por dispositivo, pelo próprio llama-server (--list-devices), em MiB."""
    r = subprocess.run([str(EXE), "--list-devices"], capture_output=True, text=True, errors="replace",
                       cwd=EXE.parent, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {m.group(1): {"total": int(m.group(3)), "livre": int(m.group(4) or 0)}
            for m in re.finditer(r"^\s*(\S+):\s*(.+?)\s*\((\d+) MiB(?:,\s*(\d+) MiB free)?\)", r.stdout + r.stderr, re.M)}


def _paralelo(n: int, prompt: int, n_predict: int) -> dict:
    res: list[dict | None] = [None] * n
    erros: list[str] = []

    def uma(i):
        try:
            res[i] = completa(tokens(prompt, semente=100 + i), n_predict=n_predict, slot=-1)
        except Exception as e:
            erros.append(str(e)[:200])
    t0 = time.monotonic()
    th = [threading.Thread(target=uma, args=(i,)) for i in range(n)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    parede = time.monotonic() - t0
    ok = [r for r in res if r]
    return {"sessoes": n, "tok_s_cada": [r["tok_s"] for r in ok],
            "tok_s_total": round(sum(r["predito_n"] or 0 for r in ok) / parede, 1) if ok else 0,
            "parede_s": round(parede, 1), "erros": erros}


def v6() -> dict:
    out = {"vram_antes": _vram()}
    for ctx in (8192, 32768):
        chave = f"ctx_{ctx}"
        out[chave] = {}
        try:
            with Servidor("hibrido", ctx, ["--kv-unified", "-np", "4"]) as s:
                out[chave]["carga_s"] = s.carga_s
                out[chave]["vram_carregado"] = _vram()
                for n in (1, 2, 4):
                    out[chave][f"{n}_sessoes"] = _paralelo(n, 1000, 128)
                    log("V6", ctx, n, out[chave][f"{n}_sessoes"])
                # Disputa: prompt curto de uma sessão enquanto outra processa um prompt grande.
                sozinho = completa(tokens(500, semente=300), slot=-1)
                grande = min(20_000, ctx - 1500)
                t = threading.Thread(target=lambda: out[chave].__setitem__("grande", completa(tokens(grande, semente=301),
                                                                                                slot=-1)))
                t.start()
                time.sleep(1.0)
                disputa = completa(tokens(500, semente=302), slot=-1)
                t.join()
                out[chave]["prompt_curto_sozinho_ms"] = sozinho["prompt_ms"]
                out[chave]["prompt_curto_na_disputa"] = disputa
                out[chave]["prompt_grande_tokens"] = grande
                # Ponto de descarte: 4 sessões que juntas passam da janela unificada.
                por = int(ctx * 0.4)
                estouro = _paralelo(4, por, 16)
                out[chave]["quatro_sessoes_de"] = por
                out[chave]["estouro"] = estouro
                refeito = completa(tokens(por, semente=100), slot=-1)  # a 1ª das quatro de novo: ainda no cache?
                out[chave]["cache_da_primeira_depois"] = refeito["cache_n"]
                out[chave]["vram_durante"] = _vram()
        except Exception as e:
            out[chave]["erro"] = str(e)
        log("V6", ctx, {k: v for k, v in out[chave].items() if k in ("erro", "estouro", "cache_da_primeira_depois")})
    base = out.get("ctx_8192", {})
    um, dois = base.get("1_sessoes", {}).get("tok_s_total"), base.get("2_sessoes", {}).get("tok_s_total")
    out["ganho_2_sessoes"] = round(dois / um, 2) if um and dois else None
    out["paralelo_vale_a_pena"] = bool(out["ganho_2_sessoes"] and out["ganho_2_sessoes"] > 1.3)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--so", default="V1,V2,V3,V5,V6")
    etapas = set(ap.parse_args().so.upper().split(","))
    PASTA.mkdir(parents=True, exist_ok=True)
    todos = json.loads(SAIDA.read_text(encoding="utf-8")) if SAIDA.exists() else {}
    val = todos.setdefault("validacoes", {})
    val["maquina"] = {"gpu": "Intel Arc B580 12 GB (Vulkan)", "ram_gb": 30, "llama_server": str(EXE),
                      "quando": datetime.now().isoformat(timespec="seconds")}

    def grava():
        SAIDA.parent.mkdir(parents=True, exist_ok=True)
        SAIDA.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")

    def roda(chave, fn):
        log("começando", chave)
        try:
            val[chave] = fn()
        except Exception as e:
            val[chave] = {"erro": str(e), "passa": False}
            log(chave, "ERRO", e)
        grava()

    if "V1" in etapas:
        roda("V1_comum", lambda: v1("comum"))
    if "V2" in etapas:
        roda("V2_comum", lambda: v2("comum"))
    if "V3" in etapas:
        for mod in ("swa", "hibrido"):
            roda(f"V3_{mod}_v1", lambda m=mod: v1(m))
            roda(f"V3_{mod}_v2", lambda m=mod: v2(m))
        g_comum = 1 - (val.get("V2_comum", {}).get("10000", {}).get("razao") or 1)
        for mod in ("swa", "hibrido"):
            razao = val.get(f"V3_{mod}_v2", {}).get("10000", {}).get("razao")
            ganho = 1 - razao if razao is not None else None
            val[f"V3_{mod}"] = {"ganho": round(ganho, 3) if ganho is not None else None,
                                "ganho_do_comum": round(g_comum, 3),
                                "passa": bool(ganho is not None and g_comum > 0 and ganho >= 0.5 * g_comum)}
        grava()
    if "V5" in etapas:
        roda("V5", v5)
    if "V6" in etapas:
        roda("V6", v6)
    log("fim")


if __name__ == "__main__":
    main()

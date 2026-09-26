"""Bench do Maestro (E0): roda o pedido fixo de ponta a ponta, sem intervenção, e salva um JSON com os números.

    resources\\python\\python.exe scripts\\bench_maestro.py --maestro Qwen3.6-35B-A3B-Q4_K_M ^
        --worker Ornith-1.5-9B-Q4_K_M --rotulo diferentes --saida docs\\bench\\2026-09-baseline.json

- Sobe um backend próprio (FORJA_DATA novo em .devbench\\<rotulo>-<hora>, portas próprias): não encosta no
  Forja do usuário nem na instância de validação.
- Os parâmetros de carga de cada modelo vêm do local.json do usuário (%APPDATA%\\Forja), com a janela que
  o papel exige (Maestro >= 32k). O runtime do llama.cpp é copiado de lá.
- Mede pelo FORJA_METRICAS (app/metricas.py): cada volta do Maestro e do Worker com os timings do llama-server
  e cada troca de modelo. O resto vem do banco (tarefas, tentativas) e da suíte de aceite escondida.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
BENCH = RAIZ / "backend" / "tests" / "bench"
PYTHON = RAIZ / "resources" / "python" / "python.exe"
DADOS_USUARIO = Path(os.environ.get("APPDATA", "")) / "Forja"
PORTA_API, PORTA_LLAMA = 8801, 8078
PASTA_MODELOS = r"D:\Modelos-IA\lmstudio"
CTX = {"maestro": 65536, "worker": 32768}
TRAVADO_MIN = 15


def acha_gguf(nome: str) -> Path:
    achados = [p for p in Path(PASTA_MODELOS).rglob("*.gguf") if p.stem == nome or p.name == nome]
    if not achados:
        raise SystemExit(f"Modelo {nome} não encontrado em {PASTA_MODELOS}")
    return achados[0]


def prepara_dados(dados: Path, modelos: dict[str, Path], extra: dict) -> None:
    dados.mkdir(parents=True)
    shutil.copytree(DADOS_USUARIO / "runtimes" / "llama", dados / "runtimes" / "llama")
    usuario = json.loads((DADOS_USUARIO / "local.json").read_text(encoding="utf-8"))
    params = {}
    for papel, caminho in modelos.items():
        p = dict(usuario.get("models", {}).get(str(caminho)) or {})
        p["ctx"] = max(int(p.get("ctx") or 0), CTX[papel], int(params.get(str(caminho), {}).get("ctx") or 0))
        p.update(extra)
        params[str(caminho)] = p
    (dados / "local.json").write_text(json.dumps({
        "dirs": [PASTA_MODELOS], "models": params, "runtime": usuario.get("runtime") or {},
        "guardrail": "relaxado", "autoload": False}, indent=2), encoding="utf-8")


def prepara_projeto(destino: Path) -> None:
    shutil.copytree(BENCH / "projeto", destino)
    for cmd in (["git", "init", "-q"], ["git", "config", "user.name", "Forja Bench"],
                ["git", "config", "user.email", "bench@forja.local"], ["git", "add", "-A"],
                ["git", "commit", "-qm", "inicio do bench"]):
        subprocess.run(cmd, cwd=destino, check=True, capture_output=True)


def sobe_backend(dados: Path, token: str, metricas: Path) -> subprocess.Popen:
    env = {**os.environ, "FORJA_DATA": str(dados), "DB_PATH": str(dados / "forja.db"), "FORJA_TOKEN": token,
           "FORJA_LOCAL_PORT": str(PORTA_LLAMA), "FORJA_METRICAS": str(metricas),
           "WORKSPACE_ROOT": str(dados / "ws"), "FORJA_LAN_PORTA": "47899", "PYTHONUTF8": "1"}
    log = open(dados / "backend.log", "wb")
    proc = subprocess.Popen([str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port",
                             str(PORTA_API)], cwd=RAIZ / "backend", env=env, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    for _ in range(120):
        try:
            if httpx.get(f"http://127.0.0.1:{PORTA_API}/api/settings", headers={"x-forja-token": token},
                         timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"backend não subiu; veja {dados / 'backend.log'}")


def derruba(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)


def roda_aceite(projeto: Path) -> dict:
    """Suíte do próprio projeto e a de aceite (escondida do agente, copiada só agora)."""
    def pytest(*alvo: str) -> dict:
        base = projeto / ".pytest-bench"
        # o python do sistema, o mesmo que o verify do Worker usa (não o portátil do Forja)
        r = subprocess.run(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--basetemp={base}", *alvo],
                           cwd=projeto, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        fim = [ln for ln in r.stdout.splitlines() if re.search(r"\d+ (passed|failed|errors?)\b", ln)]
        contagem = {k: 0 for k in ("passed", "failed", "error")}
        for n, rotulo in re.findall(r"(\d+) (passed|failed|error)", fim[-1] if fim else ""):
            contagem[rotulo] = int(n)
        return {"exit": r.returncode, **contagem, "resumo": fim[-1] if fim else r.stdout[-400:]}

    proprio = pytest("tests") if (projeto / "tests").is_dir() else {"exit": None, "resumo": "sem tests/"}
    (projeto / "tests").mkdir(exist_ok=True)
    shutil.copy(BENCH / "aceite_tarefas.py", projeto / "tests" / "test_aceite_bench.py")
    aceite = pytest("tests/test_aceite_bench.py")
    return {"suite_do_projeto": proprio, "aceite": aceite}


def resumo_metricas(arq: Path) -> dict:
    eventos = [json.loads(ln) for ln in arq.read_text(encoding="utf-8").splitlines()] if arq.exists() else []
    llm = [e for e in eventos if e["tipo"] == "llm"]
    trocas = [e for e in eventos if e["tipo"] == "troca"]

    def soma(lista, campo, sub=None):
        return sum(((e.get(campo) or {}).get(sub) if sub else e.get(campo)) or 0 for e in lista)

    por_papel = {}
    for papel in sorted({e["papel"] for e in llm}):
        ls = [e for e in llm if e["papel"] == papel]
        processados, reaproveitados = soma(ls, "timings", "prompt_n"), soma(ls, "timings", "cache_n")
        por_papel[papel] = {
            "chamadas": len(ls), "segundos": round(soma(ls, "segundos"), 1),
            "prompt_tokens": soma(ls, "prompt_tokens"), "completion_tokens": soma(ls, "completion_tokens"),
            "prompt_processado": processados, "prompt_do_cache": reaproveitados,
            "taxa_cache": round(reaproveitados / (processados + reaproveitados), 3) if processados + reaproveitados else None,
            "prompt_ms": round(soma(ls, "timings", "prompt_ms")), "geracao_ms": round(soma(ls, "timings", "predicted_ms")),
        }
    # Cache perdido do principal: a volta do Maestro logo depois de o Worker rodar (run_task). Pela sequência
    # e não pelas chamadas: modelo que chama ferramenta em texto chega com tool_calls vazio.
    depois = [b for a, b in zip(llm, llm[1:]) if a["papel"] == "worker" and b["papel"] == "maestro"]
    perdido = [{"prompt_processado": (e.get("timings") or {}).get("prompt_n"),
                "prompt_do_cache": (e.get("timings") or {}).get("cache_n"),
                "prompt_ms": round((e.get("timings") or {}).get("prompt_ms") or 0)} for e in depois]
    return {"por_papel": por_papel, "trocas_de_modelo": len(trocas),
            "segundos_em_troca": round(sum(e["segundos"] for e in trocas), 1),
            "trocas": [{"de": e["de"], "para": e["para"], "segundos": e["segundos"]} for e in trocas],
            "cache_perdido_do_maestro": {
                "voltas_depois_de_run_task": len(perdido),
                "prompt_reprocessado_total": sum(p["prompt_processado"] or 0 for p in perdido),
                "prompt_reprocessado_medio": round(sum(p["prompt_processado"] or 0 for p in perdido) / len(perdido))
                if perdido else None,
                "ms_reprocessando_total": sum(p["prompt_ms"] for p in perdido), "voltas": perdido}}


def resumo_banco(db: Path) -> dict:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tarefas = [dict(zip(("code", "title", "status", "attempt_count"), r))
               for r in c.execute("select code, title, status, attempt_count from tasks order by id")]
    tentativas = c.execute("select count(*), coalesce(sum(seconds),0) from attempts").fetchone()
    return {"tarefas": len(tarefas), "por_status": {s: sum(t["status"] == s for t in tarefas)
                                                     for s in sorted({t["status"] for t in tarefas})},
            "needs_human": sum(t["status"] == "needs_human" for t in tarefas),
            "tentativas_total": tentativas[0], "segundos_nos_workers": round(tentativas[1], 1),
            "tentativas_por_tarefa": {t["code"]: t["attempt_count"] for t in tarefas}, "lista": tarefas,
            "features": [dict(zip(("title", "status"), r)) for r in c.execute("select title, status from features")]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--maestro", required=True, help="nome do .gguf (sem extensão) do Maestro")
    ap.add_argument("--worker", required=True, help="nome do .gguf do Worker (o mesmo = configuração 'mesmo')")
    ap.add_argument("--rotulo", required=True)
    ap.add_argument("--saida", required=True, help="JSON onde este resultado entra (chave = rótulo)")
    ap.add_argument("--timeout", type=int, default=150, help="minutos")
    ap.add_argument("--kv", default="", help="cache_type_k/v para os dois modelos (ex.: f16, q8_0); vazio = o do usuário")
    ap.add_argument("--effort", default="medio")
    a = ap.parse_args()

    modelos = {"maestro": acha_gguf(a.maestro), "worker": acha_gguf(a.worker)}
    extra = {"cache_type_k": a.kv, "cache_type_v": a.kv} if a.kv else {}
    dados = RAIZ.parent / ".devbench" / f"{a.rotulo}-{datetime.now():%Y%m%d-%H%M%S}"
    prepara_dados(dados, modelos, extra)
    projeto = dados / "ws" / "bench-tarefas"
    prepara_projeto(projeto)
    token, metricas = secrets.token_hex(16), dados / "metricas.jsonl"
    proc = sobe_backend(dados, token, metricas)
    api = httpx.Client(base_url=f"http://127.0.0.1:{PORTA_API}/api", headers={"x-forja-token": token}, timeout=120)
    alias = {p: m.stem for p, m in modelos.items()}
    inicio = time.time()
    resultado: dict = {"rotulo": a.rotulo, "quando": datetime.now().isoformat(timespec="seconds"),
                       "maestro": alias["maestro"], "worker": alias["worker"], "kv": a.kv or "do usuário",
                       "effort": a.effort, "dados": str(dados)}
    try:
        worker = {"provider": "local", "model": alias["worker"]}
        r = api.put("/settings", json={"maestro_model": {"provider": "local", "model": alias["maestro"]},
                                       "subagents": {"rapido": worker, "capaz": worker, "nuvem": worker},
                                       "max_workers": 1, "maestro_browser": False, "model_lifecycle": "persistent",
                                       "sandbox_isolado": "desligado"})
        r.raise_for_status()
        conv = api.post("/conversations", json={"kind": "maestro", "workspace": str(projeto)}).json()["id"]
        pedido = (BENCH / "pedido.md").read_text(encoding="utf-8")
        with api.stream("POST", f"/conversations/{conv}/run", json={
                "content": pedido, "provider": "local", "model": alias["maestro"], "permission": "bypass",
                "effort": a.effort}) as st:
            next(st.iter_lines(), None)  # o turno roda desacoplado da conexão: só precisa ter começado
        prazo = inicio + a.timeout * 60
        estado = "terminou"
        aprovacoes: list[dict] = []
        while True:
            time.sleep(15)
            vivo = api.get(f"/conversations/{conv}/live").json().get("run")
            if not vivo:
                break
            # Comando destrutivo pede aprovação até em "Ignorar permissões": o bench faz o papel de quem aprova
            # tudo, e conta. Na 1ª rodada (b) o Maestro ficou parado num Remove-Item esperando alguém.
            for ap in vivo.get("approvals") or []:
                call = ap.get("call") or {}
                r = api.post(f"/runs/{vivo['run_id']}/approve", json={"call_id": call.get("id"), "approved": True})
                aprovacoes.append({"ferramenta": call.get("name"), "argumentos": str(call.get("arguments"))[:200],
                                   "respondida": r.status_code == 200})
            # Cão de guarda: nenhuma chamada ao LLM terminou em TRAVADO_MIN minutos (resposta sem fim, ferramenta
            # presa). Sem ele a etapa gastava o timeout inteiro numa tarefa e o bench não seguia sozinho.
            ultima = max(metricas.stat().st_mtime if metricas.exists() else inicio, inicio)
            travou = time.time() - ultima > TRAVADO_MIN * 60
            if time.time() > prazo or travou:
                api.post(f"/runs/{vivo['run_id']}/stop")
                estado = f"travou: {TRAVADO_MIN} min sem nenhuma chamada terminar" if travou else "tempo esgotado"
                time.sleep(20)
                break
        resultado.update(estado=estado, relogio_s=round(time.time() - inicio, 1), aprovacoes_pedidas=aprovacoes)
    finally:
        derruba(proc)
        time.sleep(3)
    resultado.update(banco=resumo_banco(dados / "forja.db"), metricas=resumo_metricas(metricas),
                     testes=roda_aceite(projeto))
    geracao = sum(p["segundos"] for p in resultado["metricas"]["por_papel"].values())
    resultado["resumo"] = {
        "relogio_min": round(resultado["relogio_s"] / 60, 1),
        "troca_de_modelo_min": round(resultado["metricas"]["segundos_em_troca"] / 60, 1),
        "chamadas_ao_llm_min": round(geracao / 60, 1),
        "tarefas": resultado["banco"]["tarefas"], "needs_human": resultado["banco"]["needs_human"],
        "tentativas": resultado["banco"]["tentativas_total"],
        "aceite": f"{resultado['testes']['aceite'].get('passed', 0)}/6",
        "suite_do_projeto_passa": resultado["testes"]["suite_do_projeto"].get("exit") == 0,
    }
    saida = Path(a.saida) if Path(a.saida).is_absolute() else RAIZ / a.saida
    saida.parent.mkdir(parents=True, exist_ok=True)
    todos = json.loads(saida.read_text(encoding="utf-8")) if saida.exists() else {}
    todos[a.rotulo] = resultado
    saida.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resultado["resumo"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Orchestrator do Maestro: despacha uma tarefa a um Worker e devolve resultado medido.

`run_task` é tratado aqui e não como handler comum de ferramenta pelo mesmo motivo do
`delegate_task`: precisa emitir eventos para a interface e chamar de volta o `run_call` do agente
(para que a verificação passe pela policy e pelo card de aprovação, como qualquer outro comando).

O que volta ao Maestro é MEDIÇÃO, não autoavaliação do Worker: os arquivos vêm do `git diff`, os
testes vêm do comando que o `subagents` roda depois que o Worker para, e o relatório dele entra
como `summary` — uma linha entre outras, não o veredito. Quem fecha a tarefa é o Maestro, com
`update_task`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import AsyncIterator, Callable

from . import gitops, subagents, taskdb, workspace
from .tools import ToolError

MAX_ERROS = 5          # erros de passo que entram no resultado
MAX_ERRO_TEXTO = 1500
MAX_SAIDA_TESTE = 4000
WRITE_TOOLS = subagents.WRITE_TOOLS


def _mudancas(root: Path, steps: list[dict]) -> list[dict]:
    """Arquivos que esta tentativa tocou, com as contagens vindas do `git diff`.

    Do git e não do relatório do Worker: ele pode dizer que mudou três arquivos e ter mudado cinco,
    ou nenhum. O diff é o que aconteceu de fato.
    """
    alvos = sorted({str(s["arguments"].get("path"))
                    for s in steps
                    if s["name"] in WRITE_TOOLS and s["status"] == "ok" and s["arguments"].get("path")})
    if not alvos or not gitops.is_repo(root):
        # Sem repositório não há diff; ainda assim vale dizer quais caminhos foram escritos.
        return [{"path": p, "status": "written", "additions": None, "deletions": None} for p in alvos]
    out = []
    for path in alvos:
        corpo = [l for l in gitops.diff(root, path).splitlines() if not l.startswith(("+++", "---"))]
        existe = (root / path).is_file()
        out.append({"path": path,
                    "status": "deleted" if not existe else ("modified" if corpo else "unchanged"),
                    "additions": sum(1 for l in corpo if l.startswith("+")),
                    "deletions": sum(1 for l in corpo if l.startswith("-"))})
    return out


def collect_result(task, attempt_n: int, sub_out: dict, root: Path) -> dict:
    """O protocolo Worker → Maestro, montado a partir do que foi observado."""
    meta = sub_out.get("meta") or {}
    info = meta.get("sub") or {}
    steps = info.get("steps") or []
    verify = info.get("verify") or {}
    saida_teste = ""
    if verify:
        # A saída do comando está no passo correspondente, não no dicionário de verificação.
        saida_teste = next((s.get("result") or "" for s in reversed(steps)
                            if s["name"] == "run_command"
                            and s["arguments"].get("command") == verify.get("command")), "")

    if sub_out.get("status") == "erro":
        status = "error"
    elif verify:
        status = "completed" if verify.get("status") == "ok" else "failed"
    else:
        # Sem comando de verificação nada PROVA que ficou pronto. 'unverified' obriga o Maestro a
        # julgar (ler o diff, rodar algo, abrir o navegador) em vez de confiar no relatório.
        status = "unverified"

    return {
        "type": "task_result",
        "task_code": task.code,
        "attempt": attempt_n,
        "status": status,
        "changes": _mudancas(root, steps),
        "commands": [{"command": s["arguments"].get("command"), "status": s["status"]}
                     for s in steps if s["name"] == "run_command"],
        "tests": ({"command": verify.get("command"), "status": verify.get("status"),
                   "output": saida_teste[:MAX_SAIDA_TESTE]} if verify else None),
        "errors": [f"{s['name']}: {(s.get('result') or '')[:MAX_ERRO_TEXTO]}"
                   for s in steps if s["status"] == "erro"][:MAX_ERROS],
        "review": info.get("review"),
        "summary": sub_out.get("text") or "",
        "model": info.get("model"),
        "level": info.get("level"),
        "agent": info.get("agent"),
        "seconds": info.get("seconds"),
        "tokens": info.get("tokens"),
        "iterations": info.get("iterations"),
    }


async def run_task(conv_id: int, call: dict, req, run_obj, out: dict,
                   run_call: Callable) -> AsyncIterator[dict]:
    args = call["arguments"]
    code = str(args.get("code") or "").strip().upper()
    strategy = str(args.get("strategy") or "").strip()
    meta: dict = {"arguments": args}
    root = workspace.root()

    def erro(texto: str) -> None:
        out.update(status="erro", text=texto, meta=meta)

    try:
        task = taskdb.get(code, conv_id)
    except ToolError as e:
        erro(str(e))
        return

    if pendentes := taskdb.unmet_deps(task, conv_id):
        erro(f"{task.code} depende de {', '.join(pendentes)}, que ainda não concluíram. "
             "Execute essas primeiro (list_tasks mostra o estado de cada uma).")
        return
    if task.status == "completed":
        erro(f"{task.code} já está concluída. Se precisa mexer nela de novo, reabra com "
             "update_task(status='queued') dizendo o motivo.")
        return
    if task.attempt_count >= task.max_attempts:
        taskdb.set_status(task.code, "needs_human", conv_id,
                          f"Limite de {task.max_attempts} tentativas atingido.")
        erro(f"{task.code} atingiu o limite de {task.max_attempts} tentativas e foi marcada como "
             "needs_human. Não tente de novo: diagnostique o que está travando, e ou reescreva o "
             "contrato (update_task) e aumente max_attempts, ou pergunte ao usuário com ask_user.")
        return
    if task.attempt_count and not strategy:
        # §10: mudar de estratégia entre tentativas, não repetir o mesmo prompt.
        erro(f"{task.code} já falhou {task.attempt_count} vez(es). Informe 'strategy' dizendo o que "
             "muda nesta tentativa — o erro anterior vai junto no briefing do Worker. "
             f"Último erro:\n{taskdb.last_error(task.code, conv_id)[:1500]}")
        return

    cadeia = subagents.chain(task.model_slot or "capaz")
    if not cadeia:
        porque = subagents._why_not()
        erro("Nenhum Worker disponível agora"
             + (f" ({porque})" if porque else " — configure os slots em Configurações › Subagentes")
             + f". A tarefa {task.code} continua em '{task.status}'.")
        return
    nivel, spec = cadeia[0]

    # Lido ANTES de abrir a tentativa nova: last_error() olha a tentativa mais recente, e a que
    # estamos prestes a criar ainda não falhou — pegá-la deixaria o Worker sem saber o que deu errado
    # da vez passada, que é metade do motivo de existir uma segunda tentativa.
    erro_anterior = taskdb.last_error(task.code, conv_id)

    # Tentativa persistida ANTES de qualquer efeito colateral: se o app cair agora, taskdb.reap()
    # encontra o rastro e devolve a tarefa para 'queued' na próxima abertura.
    attempt_id = taskdb.new_attempt(task.code, {"level": nivel, "provider": spec["provider"],
                                                "model": spec["model"], "agent": task.agent},
                                    strategy, conv_id)
    attempt_n = task.attempt_count + 1
    if task.status in ("pending", "failed", "blocked", "needs_human", "cancelled", "completed"):
        taskdb.set_status(task.code, "queued", conv_id)

    brief = taskdb.render_contract(task, erro_anterior, strategy)
    contrato = task.contract or {}
    # O Worker é o mesmo subagente de sempre: mesmas ferramentas, mesmas aprovações, mesma pasta.
    # Muda só de onde vem o briefing — um contrato estruturado em vez de texto que o modelo escreveu.
    sub_call = {"id": call["id"], "name": "delegate_task", "arguments": {
        "task": brief,
        "level": task.model_slot or "capaz",
        "agent": task.agent,
        "files": contrato.get("relevant_files") or [],
        "done_when": contrato.get("verify_command") or ""}}

    t0 = time.monotonic()
    sub_out: dict = {}
    taskdb.set_status(task.code, "implementing", conv_id)
    yield {"type": "task_update", "code": task.code, "status": "implementing", "attempt": attempt_n}
    try:
        async for ev in subagents.run(conv_id, sub_call, req, run_obj, sub_out, run_call,
                                      structured=True):
            yield ev
    except Exception as e:  # nunca deixar a tentativa aberta no banco
        taskdb.finish_attempt(attempt_id, "error", error=f"{e.__class__.__name__}: {e}",
                              seconds=time.monotonic() - t0)
        taskdb.set_status(task.code, "failed", conv_id, str(e)[:500])
        erro(f"O Worker falhou: {e.__class__.__name__}: {e}")
        return

    if run_obj.cancel.is_set():
        taskdb.finish_attempt(attempt_id, "cancelled", error="Interrompido pelo usuário.",
                              seconds=time.monotonic() - t0)
        taskdb.set_status(task.code, "cancelled", conv_id, "Interrompido pelo usuário.")
        out.update(status="cancelada", text=f"{task.code}: interrompida pelo usuário.", meta=meta)
        return

    taskdb.set_status(task.code, "testing", conv_id)
    task = taskdb.get(task.code, conv_id)  # recarrega: o status mudou desde o get inicial
    resultado = collect_result(task, attempt_n, sub_out, root)
    taskdb.finish_attempt(attempt_id, "completed" if resultado["status"] == "completed" else "failed",
                          resultado, seconds=time.monotonic() - t0,
                          tokens=resultado.get("tokens") or 0)
    # 'reviewing' e não 'completed': a tarefa fica esperando o julgamento do Maestro. É a etapa
    # explícita de revisão — o Worker não assina o próprio atestado.
    taskdb.set_status(task.code, "reviewing" if resultado["status"] != "failed" else "failed",
                      conv_id, "" if resultado["status"] != "failed" else "A verificação falhou.")
    yield {"type": "task_update", "code": task.code, "status": resultado["status"],
           "attempt": attempt_n}

    meta["task"] = task.code
    meta["task_result"] = resultado
    meta["sub"] = (sub_out.get("meta") or {}).get("sub")
    out.update(status="ok", text=_para_o_maestro(resultado), meta=meta)


def _para_o_maestro(r: dict) -> str:
    """JSON do resultado mais a instrução do que fazer com ele.

    JSON porque é o protocolo; a linha final porque sem ela o modelo pequeno lê 'status: completed'
    e segue em frente sem nunca chamar update_task — a tarefa ficaria viva em 'reviewing' para sempre.
    """
    cauda = {
        "completed": "A verificação PASSOU. Confira o diff e os critérios de aceitação; se estiver bom, "
                     "feche com update_task(status='completed').",
        "failed": "A verificação FALHOU. Diagnostique pela saída em 'tests' e por 'errors', e então "
                  "run_task de novo com 'strategy' dizendo o que muda — ou reescreva o contrato.",
        "unverified": "Não houve comando de verificação, então NADA prova que funcionou. Julgue você: "
                      "leia os arquivos de 'changes', rode um comando ou abra o navegador antes de "
                      "fechar a tarefa.",
        "error": "O Worker não concluiu. Veja 'errors' e decida: nova tentativa com outra estratégia, "
                 "outro modelo (update_task model_slot), ou needs_human.",
    }.get(r["status"], "Analise o resultado e decida o próximo passo.")
    return json.dumps(r, ensure_ascii=False, default=str) + "\n\n" + cauda

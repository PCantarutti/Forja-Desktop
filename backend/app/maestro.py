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

import asyncio
import contextlib
import hashlib
import json
import time
from pathlib import Path
from typing import AsyncIterator, Callable

from . import checkpoints, config, db, gitops, modelctl, subagents, taskdb, workspace
from .tools import ToolError

MAX_ERROS = 5          # erros de passo que entram no resultado
MAX_ERRO_TEXTO = 1500
MAX_SAIDA_TESTE = 4000
WRITE_TOOLS = subagents.WRITE_TOOLS

# Um lock por arquivo, por conversa. Duas tarefas em paralelo que declaram o mesmo arquivo em
# `relevant_files` serializam; as que não se tocam correm juntas.
#
# ponytail: o lock vale para o que o contrato DECLARA, não para o que o Worker de fato escreve —
# uma tarefa que mexe num arquivo que não listou passa sem esperar. O freio real seria travar no
# write_file/edit_file, dentro do run_call; fica para quando aparecer conflito de verdade.
_LOCKS: dict[str, asyncio.Lock] = {}


def _chave(conv_id: int, caminho: str) -> str:
    return f"{conv_id}:{caminho.strip().replace(chr(92), '/').lstrip('./').lower()}"


@contextlib.asynccontextmanager
async def _travas(conv_id: int, caminhos: list[str]):
    """Adquire os locks em ordem alfabética: duas tarefas pedindo {a, b} e {b, a} pegariam cada uma
    metade e esperariam a outra para sempre se a ordem fosse a do contrato."""
    chaves = sorted({_chave(conv_id, c) for c in caminhos if c.strip()})
    async with contextlib.AsyncExitStack() as pilha:
        for k in chaves:
            await pilha.enter_async_context(_LOCKS.setdefault(k, asyncio.Lock()))
        yield chaves


def para_travar(arquivos: list[str]) -> list[str]:
    """Os arquivos do contrato que entram no lock. .forja/ fica de fora: o guia visual vai nos
    relevant_files de TODA tarefa com tela e o Worker só lê — travar nele serializava as tarefas
    "paralelas" do TaskBoard uma atrás da outra."""
    return [a for a in arquivos if not a.replace(chr(92), "/").removeprefix("./").startswith(".forja/")]


def em_conflito(conv_id: int, caminhos: list[str]) -> list[str]:
    """Arquivos deste contrato que outra tarefa está segurando agora. Serve para avisar na interface
    por que a tarefa não começou, em vez de ela parecer travada."""
    return sorted(c for c in caminhos if (l := _LOCKS.get(_chave(conv_id, c))) and l.locked())


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

    mudancas = _mudancas(root, steps)
    return {
        "type": "task_result",
        "task_code": task.code,
        "attempt": attempt_n,
        "status": status,
        "changes": mudancas,
        "outside_contract": fora_do_contrato(mudancas, (task.contract or {}).get("relevant_files") or []),
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


def _norm(caminho: str) -> str:
    return str(caminho).replace(chr(92), "/").removeprefix("./").lower()


def fora_do_contrato(mudancas: list[dict], declarados: list[str]) -> list[str]:
    """Arquivos escritos que o contrato não listou. No TaskBoard o Worker da camada de dados reescreveu
    o style.css que a tarefa anterior tinha entregue, e ninguém viu. Contrato sem arquivos: nada a dizer."""
    declarados = para_travar(declarados)  # o guia visual entra sozinho: não conta como declaração
    if not declarados:
        return []
    ok = {_norm(d) for d in declarados}
    return [m["path"] for m in mudancas if _norm(m["path"]) not in ok]


def mesmo_modelo(req) -> dict | None:
    """Modelo da Maestro para os Workers, com o interruptor ligado; senão None."""
    if not getattr(config, "WORKERS_DO_MAESTRO", False):
        return None
    provider, model = getattr(req, "provider", ""), getattr(req, "model", "")
    return {"provider": provider, "model": model} if provider and model else None


SUCESSO = ("completed", "unverified")
MIN_HISTORICO = 3       # tentativas de um nível no projeto antes de o histórico pesar
TAXA_MINIMA = 1 / 3     # abaixo disto o nível sai do roteamento automático no projeto


def evitar_niveis(task, root) -> dict[str, str]:
    """Níveis que o roteador automático deve pular nesta tarefa (nível -> motivo): os que já
    falharam nela, e os que vão mal neste projeto (pasta), contando todas as conversas dela."""
    evitar: dict[str, str] = {}
    with db.session() as s:
        for a in s.query(db.Attempt).filter(db.Attempt.task_id == task.id):
            nivel = (a.worker or {}).get("level")
            if nivel and a.status in ("failed", "error"):
                evitar[nivel] = f"falhou na tentativa {a.n} desta tarefa"
        # mesma pasta, escrita de qualquer jeito (barra, maiúscula): compara o caminho resolvido
        alvo = Path(root).resolve()
        convs = [c for c, ws in s.query(db.Conversation.id, db.Conversation.workspace)
                 if ws and Path(ws).resolve() == alvo]
        placar: dict[str, list[int]] = {}
        if convs:
            q = (s.query(db.Attempt.worker, db.Attempt.status).join(db.Task, db.Task.id == db.Attempt.task_id)
                 .filter(db.Task.conversation_id.in_(convs), db.Attempt.status != "running"))
            for worker, status in q:
                if nivel := (worker or {}).get("level"):
                    p = placar.setdefault(nivel, [0, 0])
                    p[0] += status in SUCESSO
                    p[1] += 1
    for nivel, (ok, total) in placar.items():
        if total >= MIN_HISTORICO and ok / total < TAXA_MINIMA and nivel not in evitar:
            evitar[nivel] = f"{ok}/{total} tentativas com sucesso neste projeto"
    return evitar


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
    with db.session() as s:
        feat = s.get(db.Feature, task.feature_id)
        copiada = feat.copiada_para if feat else None
    if copiada:
        erro(f"{task.code} foi copiada para a conversa {copiada}, onde o trabalho continua. Aqui a lista é só consulta.")
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

    # Em modo sequencial o orquestrador pode trocar o modelo local sozinho, então um slot que só
    # precisa ser carregado continua elegível (ver subagents._fits e modelctl.ensure).
    trocar = modelctl.pode_trocar()
    # Quem faz: a escolha da Maestro, ou o roteador (tipo, tamanho, falhas desta tarefa e o
    # histórico do projeto).
    nivel, motivo_rota = subagents.rota(task.model_slot, task.contract, evitar_niveis(task, root))
    cadeia = subagents.chain(nivel, swap=trocar)
    mesmo = mesmo_modelo(req)
    if mesmo:
        # "Workers usam o modelo da Maestro": nada de trocar de modelo. Com IA local, a Maestro e os
        # Workers dividem o mesmo llama-server (as vagas dele rodam os Workers em paralelo) e nada é
        # descarregado por engano para subir o modelo de um especialista.
        cadeia = [(nivel, mesmo)]
        motivo_rota = "modelo da Maestro (Workers usam o mesmo modelo)"
    if not cadeia:
        porque = subagents._why_not(trocar)
        erro("Nenhum Worker disponível agora"
             + (f" ({porque})" if porque else " — configure os slots em Configurações › Subagentes")
             + f". A tarefa {task.code} continua em '{task.status}'.")
        return
    nivel, spec = cadeia[0]
    if curta := modelctl.janela_curta(spec, config.WORKER_MIN_CTX, "um Worker"):
        erro(curta)  # antes da tentativa: recusar aqui não gasta uma das max_attempts
        return

    # Lido ANTES de abrir a tentativa nova: last_error() olha a tentativa mais recente, e a que
    # estamos prestes a criar ainda não falhou — pegá-la deixaria o Worker sem saber o que deu errado
    # da vez passada, que é metade do motivo de existir uma segunda tentativa.
    erro_anterior = taskdb.last_error(task.code, conv_id)

    # Tentativa persistida ANTES de qualquer efeito colateral: se o app cair agora, taskdb.reap()
    # encontra o rastro e devolve a tarefa para 'queued' na próxima abertura.
    attempt_id = taskdb.new_attempt(task.code, {"level": nivel, "provider": spec["provider"],
                                                "model": spec["model"], "agent": task.agent,
                                                "rota": motivo_rota},
                                    strategy, conv_id)
    attempt_n = task.attempt_count + 1
    if hasattr(run_obj, "tentativas"):  # escrita do Worker desta chamada = checkpoint desta tentativa
        run_obj.tentativas[call["id"]] = attempt_id
    # Arquivo que um Worker deixou num estado e que mudou desde então: foi mexido por fora. A Maestro
    # precisa saber antes de confiar no que a tarefa anterior entregou.
    externas = alteracoes_externas(conv_id, root)
    if externas:
        yield {"type": "event", "message": {"id": None, "role": "event", "content":
               "Arquivos alterados fora do Forja desde a última tarefa: " + ", ".join(externas[:10]),
               "meta": {"kind": "warning"}}}
    # Toda tentativa recomeça o ciclo em 'queued'. Sem isto, despachar de novo uma tarefa parada em
    # 'reviewing' (o Maestro não gostou do resultado) batia numa transição ilegal e derrubava o turno.
    if task.status != "queued":
        taskdb.set_status(task.code, "queued", conv_id)

    # Ciclo de vida do modelo: carrega o do slot, trocando o que estiver na VRAM se for outro.
    # O estado da tarefa já está no banco, então descarregar aqui não perde nada.
    if modelctl.gerenciavel(spec) and not modelctl.carregado(spec):
        taskdb.set_status(task.code, "loading_model", conv_id)
        yield {"type": "task_update", "code": task.code, "status": "loading_model",
               "attempt": attempt_n}
        troca: dict = {}
        try:
            async for ev in modelctl.ensure(spec, troca, run_obj.cancel):
                yield ev
        except ToolError as e:
            taskdb.finish_attempt(attempt_id, "error", error=str(e))
            taskdb.set_status(task.code, "failed", conv_id, str(e)[:500])
            erro(f"Não consegui preparar o modelo do Worker: {e}")
            return
        if run_obj.cancel.is_set():
            taskdb.finish_attempt(attempt_id, "cancelled", error="Interrompido durante a carga do modelo.")
            taskdb.set_status(task.code, "cancelled", conv_id, "Interrompido pelo usuário.")
            out.update(status="cancelada", text=f"{task.code}: interrompida ao trocar de modelo.", meta=meta)
            return
        if troca.get("swapped"):
            meta["model_swap"] = {"from": troca.get("previous"), "to": troca.get("model")}

    brief = taskdb.render_contract(task, erro_anterior, strategy)
    contrato = task.contract or {}
    # O Worker é o mesmo subagente de sempre: mesmas ferramentas, mesmas aprovações, mesma pasta.
    # Muda só de onde vem o briefing — um contrato estruturado em vez de texto que o modelo escreveu.
    sub_call = {"id": call["id"], "name": "delegate_task", "arguments": {
        "task": brief,
        "level": nivel,
        "agent": task.agent,
        "files": contrato.get("relevant_files") or [],
        "done_when": contrato.get("verify_command") or ""}}
    if mesmo:
        sub_call["arguments"]["_spec"] = mesmo  # o _run usa este modelo em vez da cadeia do nível

    t0 = time.monotonic()
    sub_out: dict = {}
    arquivos = para_travar(contrato.get("relevant_files") or [])
    if ocupados := em_conflito(conv_id, arquivos):
        # Outra tarefa em paralelo está mexendo nos mesmos arquivos: esta espera a vez em vez de
        # escrever por cima. O evento diz o motivo, senão a tarefa parece travada no cockpit.
        yield {"type": "task_update", "code": task.code, "status": "queued", "attempt": attempt_n,
               "waiting_for": ocupados}
    try:
        async with _travas(conv_id, arquivos):
            taskdb.set_status(task.code, "implementing", conv_id)
            yield {"type": "task_update", "code": task.code, "status": "implementing",
                   "attempt": attempt_n}
            async for ev in subagents.run(conv_id, sub_call, req, run_obj, sub_out, run_call,
                                          structured=True,
                                          ao_registrar=lambda t: taskdb.save_transcript(attempt_id, t)):
                yield ev
    except Exception as e:  # nunca deixar a tentativa aberta no banco
        taskdb.finish_attempt(attempt_id, "error", error=f"{e.__class__.__name__}: {e}",
                              seconds=time.monotonic() - t0)
        taskdb.set_status(task.code, "failed", conv_id, str(e)[:500])
        erro(f"O Worker falhou: {e.__class__.__name__}: {e}")
        return

    if run_obj.cancel.is_set():
        # O que o Worker fez até a interrupção fica registrado. Sem isto, parar uma tarefa lenta
        # apagava justamente o rastro que explicaria a lentidão: modelo, passos, tokens e tempo.
        parcial = None
        if sub_out.get("meta"):
            parcial = collect_result(taskdb.get(task.code, conv_id), attempt_n, sub_out, root)
            parcial["status"] = "cancelled"
        taskdb.finish_attempt(attempt_id, "cancelled", parcial, error="Interrompido pelo usuário.",
                              seconds=time.monotonic() - t0, tokens=(parcial or {}).get("tokens") or 0)
        taskdb.set_status(task.code, "cancelled", conv_id, "Interrompido pelo usuário.")
        meta["sub"] = (sub_out.get("meta") or {}).get("sub")
        if parcial:
            meta["task_result"] = parcial
        out.update(status="cancelada", text=f"{task.code}: interrompida pelo usuário.", meta=meta)
        return

    taskdb.set_status(task.code, "testing", conv_id)
    task = taskdb.get(task.code, conv_id)  # recarrega: o status mudou desde o get inicial
    resultado = collect_result(task, attempt_n, sub_out, root)
    resultado["route"] = f"{subagents.nome_do_nivel(nivel)} — {motivo_rota}"
    if externas:
        resultado["external_changes"] = externas
    registra_estado(attempt_id, root)

    # Etapa de revisão explícita: só quando NADA provou o resultado. Com o comando de verificação
    # passando, o parecer de um modelo menor que o autor rende falso-positivo, não bug — a mesma
    # razão pela qual subagents._review fica calado quando há medição.
    if resultado["status"] == "unverified" and not run_obj.cancel.is_set():
        taskdb.set_status(task.code, "reviewing", conv_id)
        yield {"type": "task_update", "code": task.code, "status": "reviewing", "attempt": attempt_n}
        alvos = {c["path"] for c in resultado["changes"] if c.get("path")}
        revisor, parecer = await subagents._review(root, brief, alvos)
        if parecer:
            resultado["review"] = f"({revisor}) {parecer}"
    # 'unverified' não é falha: nada provou nem desprovou, e quem decide é o Maestro na revisão.
    taskdb.finish_attempt(attempt_id, resultado["status"] if resultado["status"] in ("completed", "unverified")
                          else "failed",
                          resultado, seconds=time.monotonic() - t0,
                          tokens=resultado.get("tokens") or 0)
    # 'reviewing' e não 'completed': a tarefa fica esperando o julgamento do Maestro. É a etapa
    # explícita de revisão — o Worker não assina o próprio atestado.
    taskdb.set_status(task.code, "reviewing" if resultado["status"] != "failed" else "failed",
                      conv_id, "" if resultado["status"] != "failed" else "A verificação falhou.")
    yield {"type": "task_update", "code": task.code, "status": resultado["status"],
           "attempt": attempt_n}

    # O slot da Maestro vai junto: se ela roda no mesmo modelo local, descarregar a deixaria sem
    # servidor bem na hora de ler o resultado.
    async for ev in modelctl.after_task(spec, {"provider": req.provider, "model": req.model}):
        yield ev

    meta["task"] = task.code
    meta["task_result"] = resultado
    meta["sub"] = (sub_out.get("meta") or {}).get("sub")
    out.update(status="ok", text=_para_o_maestro(resultado), meta=meta)


def _sha(p: Path) -> str | None:
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()
    except OSError:
        return None  # apagado (ou nunca existiu)


def registra_estado(attempt_id: int, root: Path) -> None:
    """Como a tentativa deixou os arquivos que ela escreveu (base da detecção de mudança externa)."""
    estado = {}
    for p in checkpoints.arquivos_da_tentativa(attempt_id):
        try:
            estado[p.relative_to(root).as_posix()] = _sha(p)
        except ValueError:
            continue
    if estado:
        with db.session() as s:
            if att := s.get(db.Attempt, attempt_id):
                att.estado = estado
                s.commit()


def alteracoes_externas(conv_id: int, root: Path) -> list[str]:
    """Arquivos escritos por Workers desta conversa que mudaram desde então, fora de uma tarefa.

    Aceita a mudança depois de reportar (grava o estado atual), para avisar uma vez só."""
    with db.session() as s:
        atts = (s.query(db.Attempt).join(db.Task, db.Task.id == db.Attempt.task_id)
                .filter(db.Task.conversation_id == conv_id, db.Attempt.estado.isnot(None))
                .order_by(db.Attempt.id).all())
        conhecido: dict[str, tuple[str | None, db.Attempt]] = {}
        for att in atts:
            for rel, h in (att.estado or {}).items():
                conhecido[rel] = (h, att)  # a tentativa mais nova vence
        mudou = [rel for rel, (h, _) in conhecido.items() if _sha(root / rel) != h]
        for rel in mudou:
            att = conhecido[rel][1]
            att.estado = {**att.estado, rel: _sha(root / rel)}
        if mudou:
            s.commit()
    return mudou


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
    if fora := r.get("outside_contract"):
        cauda += (f"\nATENÇÃO: o Worker escreveu fora do contrato ({', '.join(fora)}). Confira se não desfez "
                  "o trabalho de outra tarefa antes de fechar esta.")
    return json.dumps(r, ensure_ascii=False, default=str) + "\n\n" + cauda

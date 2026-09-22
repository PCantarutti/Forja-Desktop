"""Task Manager do Maestro: funcionalidades, tarefas, tentativas e a máquina de estados delas.

A decisão que sustenta tudo: o estado do projeto mora AQUI, no SQLite, não no contexto do modelo.
Por isso um Worker pode ser morto no meio, o modelo local descarregado da VRAM e outro carregado no
lugar sem que o Maestro perca o fio — `list_tasks` reconstrói a situação inteira. É também por isso
que a compactação automática da conversa (agent._compact) pode apagar o histórico sem prejuízo.

Três ferramentas do Maestro moram aqui (plan_feature, list_tasks, update_task). A quarta, run_task,
é declarada aqui mas executada pelo loop do agente (maestro.run_task), do mesmo jeito que o
delegate_task: ela precisa emitir eventos e chamar de volta o `run_call`, o que um handler comum de
ferramenta não consegue fazer.
"""
from __future__ import annotations

import contextvars
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import config, db
from .tools import Tool, ToolError, register_extra

# Conversa do Maestro em execução. O handler de ferramenta não recebe conv_id, e exigir o id em toda
# chamada só daria ao modelo mais uma chance de errar. run_agent seta isto.
CONV: contextvars.ContextVar[int | None] = contextvars.ContextVar("forja_maestro_conv", default=None)
# Publica mudanças de tarefa na SSE, para a árvore do cockpit reagir na hora (a UI também faz
# polling do /board, que é o caminho que sobrevive a reconexão).
SINK: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar(
    "forja_task_sink", default=None)

STATUSES = ("pending", "queued", "loading_model", "implementing", "testing", "reviewing",
            "completed", "failed", "blocked", "needs_human", "cancelled")
TERMINAL = ("completed", "cancelled")
OPEN = tuple(s for s in STATUSES if s not in TERMINAL)

# Máquina de estados. Qualquer estado pode ir para cancelled/needs_human/blocked — isso é tratado
# em set_status (SEMPRE), não repetido em cada linha.
TRANSITIONS: dict[str, set[str]] = {
    "pending": {"queued"},
    "queued": {"loading_model", "implementing", "pending"},
    "loading_model": {"implementing", "failed"},
    "implementing": {"testing", "reviewing", "failed"},
    "testing": {"reviewing", "failed"},
    "reviewing": {"completed", "failed"},
    "failed": {"queued", "pending"},
    "blocked": {"pending", "queued"},
    "needs_human": {"pending", "queued"},
    "completed": {"pending", "queued"},   # reabrir: o Maestro achou um bug depois
    "cancelled": {"pending", "queued"},
}
SEMPRE = {"cancelled", "needs_human", "blocked"}

CONTRACT_FIELDS = ("context", "goal", "relevant_files", "requirements", "constraints", "do_not",
                   "acceptance_criteria", "verify_command", "expected_result")
LISTAS = ("relevant_files", "requirements", "constraints", "do_not", "acceptance_criteria")
MAX_TASKS_POR_FEATURE = 40
MAX_ITENS = 20          # itens por lista do contrato
MAX_TEXTO = 4000        # caracteres por campo de texto do contrato
SLOTS = ("rapido", "capaz", "nuvem")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _conv() -> int:
    conv = CONV.get()
    if conv is None:
        raise ToolError("Ferramenta do Maestro chamada fora de uma conversa do Maestro.")
    return conv


def _lista(raw) -> list[str]:
    """Modelo pequeno manda 'a.py, b.py' em vez de lista. Aceita os dois, como subagents._files."""
    itens = raw.split(",") if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    return [str(x).strip()[:MAX_TEXTO] for x in itens[:MAX_ITENS] if str(x).strip()]


def normalize_contract(raw) -> dict:
    """Implementation Contract validado. Só `goal` é obrigatório — exigir os nove campos faria o
    modelo pequeno travar numa chamada impossível em vez de começar a trabalhar."""
    if not isinstance(raw, dict):
        raise ToolError("contract deve ser um objeto com os campos do Implementation Contract.")
    if not str(raw.get("goal") or "").strip():
        raise ToolError("O contrato precisa de 'goal': o que esta tarefa deve alcançar, em uma frase.")
    out: dict = {}
    for campo in CONTRACT_FIELDS:
        valor = raw.get(campo)
        if campo in LISTAS:
            if itens := _lista(valor):
                out[campo] = itens
        elif texto := str(valor or "").strip()[:MAX_TEXTO]:
            out[campo] = texto
    return out


def render_contract(task: db.Task, erro_anterior: str = "", strategy: str = "") -> str:
    """O contrato virando o briefing que o Worker lê. Texto porque é o que o modelo consome, mas
    gerado a partir de dados estruturados: o Maestro não escreve o brief à mão."""
    c = task.contract or {}
    partes = [f"TAREFA {task.code}: {task.title}"]
    if c.get("context"):
        partes.append("CONTEXTO\n" + c["context"])
    partes.append("OBJETIVO\n" + c.get("goal", task.title))
    for campo, titulo in (("requirements", "REQUISITOS"), ("constraints", "RESTRIÇÕES"),
                          ("do_not", "NÃO FAÇA"), ("acceptance_criteria", "CRITÉRIOS DE ACEITAÇÃO")):
        if itens := c.get(campo):
            if campo == "do_not":
                corpo = "\n".join(f"- {x}" for x in itens)
            else:
                corpo = "\n".join(f"{i}. {x}" for i, x in enumerate(itens, 1))
            partes.append(f"{titulo}\n{corpo}")
    if c.get("expected_result"):
        partes.append("RESULTADO ESPERADO\n" + c["expected_result"])
    if erro_anterior:
        # A tentativa N carrega o que falhou na N-1. Sem isto o Worker repete o mesmo erro com o
        # mesmo prompt, que é exatamente o laço que max_attempts existe para cortar.
        partes.append(f"A TENTATIVA ANTERIOR FALHOU\n{erro_anterior}")
    if strategy:
        partes.append(f"MUDE A ABORDAGEM NESTA TENTATIVA\n{strategy}")
    return "\n\n".join(partes)


# ------------------------------------------------------------------ CRUD

def _proximo_code(s, conv_id: int) -> str:
    n = s.query(db.Task).filter(db.Task.conversation_id == conv_id).count()
    return f"TASK-{n + 1:03d}"


def _get(s, code: str, conv_id: int) -> db.Task:
    task = s.query(db.Task).filter(db.Task.conversation_id == conv_id,
                                   db.Task.code == str(code).strip().upper()).first()
    if not task:
        raise ToolError(f"Tarefa '{code}' não existe nesta conversa. Use list_tasks para ver os códigos.")
    return task


def get(code: str, conv_id: int | None = None) -> db.Task:
    with db.session() as s:
        return _get(s, code, conv_id if conv_id is not None else _conv())


def _codes_existentes(s, conv_id: int) -> set[str]:
    return {c for (c,) in s.query(db.Task.code).filter(db.Task.conversation_id == conv_id)}


def _max_attempts() -> int:
    return getattr(config, "MAESTRO_MAX_ATTEMPTS", 5)


def create_feature(conv_id: int, title: str, goal: str, tasks: list) -> dict:
    """Cria a funcionalidade e as tarefas dela numa transação só. Devolve o resumo com os códigos."""
    title = str(title or "").strip()[:200]
    if not title:
        raise ToolError("Informe 'title': o nome da funcionalidade.")
    if not isinstance(tasks, list) or not tasks:
        raise ToolError("Informe 'tasks': a lista de tarefas em que a funcionalidade foi decomposta.")
    if len(tasks) > MAX_TASKS_POR_FEATURE:
        raise ToolError(f"No máximo {MAX_TASKS_POR_FEATURE} tarefas por funcionalidade. "
                        "Decomponha em mais de uma funcionalidade.")
    with db.session() as s:
        feat = db.Feature(conversation_id=conv_id, title=title,
                          goal=str(goal or "").strip()[:MAX_TEXTO], status="active")
        s.add(feat)
        s.flush()
        criadas: list[tuple[db.Task, object]] = []
        # Duas passadas: a primeira cria tudo, a segunda resolve depends_on — assim uma tarefa pode
        # depender de outra declarada depois dela na mesma chamada.
        for i, bruto in enumerate(tasks):
            if not isinstance(bruto, dict):
                raise ToolError(f"Tarefa {i + 1} deve ser um objeto com title e contract.")
            titulo = str(bruto.get("title") or "").strip()[:200]
            contrato = normalize_contract(bruto.get("contract") or ({"goal": titulo} if titulo else None))
            titulo = titulo or contrato["goal"][:200]
            slot = str(bruto.get("model_slot") or "").strip().lower() or None
            if slot and slot not in SLOTS:
                raise ToolError(f"model_slot inválido '{slot}' na tarefa {i + 1}: use {', '.join(SLOTS)}.")
            task = db.Task(
                feature_id=feat.id, conversation_id=conv_id, code=_proximo_code(s, conv_id),
                title=titulo, contract=contrato, depends_on=[],
                priority=int(bruto.get("priority") or 0), model_slot=slot,
                agent=(str(bruto.get("agent") or "").strip() or None),
                max_attempts=max(1, min(10, int(bruto.get("max_attempts") or 0) or _max_attempts())))
            s.add(task)
            s.flush()
            criadas.append((task, bruto.get("depends_on")))
        por_indice = {str(i + 1): t.code for i, (t, _) in enumerate(criadas)}
        codigos = _codes_existentes(s, conv_id)
        for task, deps in criadas:
            resolvidas = []
            for d in _lista(deps):
                # Aceita "TASK-002" e também "2" (posição na lista desta chamada): o modelo ainda não
                # viu os códigos quando escreveu o plano.
                alvo = d.strip().upper()
                if alvo not in codigos:
                    alvo = por_indice.get(d.strip(), alvo)
                if alvo not in codigos:
                    raise ToolError(f"{task.code} depende de '{d}', que não existe.")
                if alvo != task.code and alvo not in resolvidas:
                    resolvidas.append(alvo)
            task.depends_on = resolvidas
        s.commit()
        out = {"feature_id": feat.id, "title": feat.title,
               "tasks": [{"code": t.code, "title": t.title, "depends_on": t.depends_on,
                          "model_slot": t.model_slot} for t, _ in criadas]}
    _publish(conv_id)
    return out


def set_status(code: str, novo: str, conv_id: int | None = None, reason: str = "") -> dict:
    conv_id = conv_id if conv_id is not None else _conv()
    novo = str(novo or "").strip().lower()
    if novo not in STATUSES:
        raise ToolError(f"Status inválido '{novo}'. Use: {', '.join(STATUSES)}.")
    with db.session() as s:
        task = _get(s, code, conv_id)
        atual = task.status
        if novo != atual and novo not in SEMPRE and novo not in TRANSITIONS.get(atual, set()):
            permitidos = ", ".join(sorted(TRANSITIONS.get(atual, set()) | SEMPRE))
            raise ToolError(f"{task.code} está em '{atual}' e não pode ir para '{novo}'. "
                            f"De '{atual}' dá para: {permitidos}.")
        task.status = novo
        task.blocked_reason = (reason or None) if novo in ("blocked", "needs_human", "failed") else None
        task.updated_at = _now()
        if novo == "completed":
            _fecha_feature(s, task.feature_id, task.id)
        s.commit()
        out = _task_dict(task)
    _publish(conv_id)
    return out


def _fecha_feature(s, feature_id: int, fechando: int) -> None:
    """Funcionalidade vira 'done' quando nenhuma tarefa dela continua aberta. `fechando` é a tarefa
    que está virando completed nesta mesma transação — ela ainda aparece como aberta na consulta."""
    abertas = s.query(db.Task).filter(db.Task.feature_id == feature_id, db.Task.id != fechando,
                                      db.Task.status.in_(OPEN)).count()
    feat = s.get(db.Feature, feature_id)
    if feat and not abertas:
        feat.status = "done"
        feat.updated_at = _now()


def unmet_deps(task: db.Task, conv_id: int | None = None) -> list[str]:
    """Códigos de dependências que ainda não concluíram. Lista vazia = pode rodar."""
    if not task.depends_on:
        return []
    conv_id = conv_id if conv_id is not None else task.conversation_id
    with db.session() as s:
        feitas = {c for (c,) in s.query(db.Task.code).filter(
            db.Task.conversation_id == conv_id, db.Task.code.in_(list(task.depends_on)),
            db.Task.status == "completed")}
    return [c for c in task.depends_on if c not in feitas]


# ------------------------------------------------------------------ tentativas

def new_attempt(code: str, worker: dict, strategy: str = "", conv_id: int | None = None) -> int:
    """Grava a tentativa ANTES do Worker rodar e devolve o id.

    Antes e não depois: se o app cair no meio, `reap()` encontra a tentativa aberta e sabe o que
    estava em voo. Uma tentativa que só existe na memória do processo não sobrevive à queda que ela
    deveria documentar.
    """
    conv_id = conv_id if conv_id is not None else _conv()
    with db.session() as s:
        task = _get(s, code, conv_id)
        task.attempt_count += 1
        task.updated_at = _now()
        att = db.Attempt(task_id=task.id, n=task.attempt_count, status="running",
                         worker=worker or {}, strategy=(strategy or None))
        s.add(att)
        s.commit()
        return att.id


def finish_attempt(attempt_id: int, status: str, result: dict | None = None,
                   error: str = "", seconds: float = 0.0, tokens: int = 0) -> None:
    with db.session() as s:
        att = s.get(db.Attempt, attempt_id)
        if not att:
            return
        att.status = status
        att.result = result
        att.error = error or None
        att.seconds = round(float(seconds), 1)
        att.tokens = int(tokens)
        att.finished_at = _now()
        if result is not None and (task := s.get(db.Task, att.task_id)):
            task.result = result
            task.updated_at = _now()
        s.commit()


def last_error(code: str, conv_id: int | None = None) -> str:
    """O que deu errado na última tentativa, para entrar no brief da próxima."""
    conv_id = conv_id if conv_id is not None else _conv()
    with db.session() as s:
        task = _get(s, code, conv_id)
        att = s.query(db.Attempt).filter(db.Attempt.task_id == task.id).order_by(
            db.Attempt.n.desc()).first()
        if not att or att.status == "completed":
            return ""
        if att.error:
            return att.error[:MAX_TEXTO]
        r = att.result or {}
        partes: list[str] = []
        testes = r.get("tests") or {}
        if testes.get("status") and testes["status"] != "ok":
            partes.append(f"O comando de verificação `{testes.get('command')}` FALHOU:\n"
                          f"{(testes.get('output') or '')[:2000]}")
        partes += [str(p) for p in (r.get("summary"), *(r.get("errors") or [])) if p]
        return "\n".join(partes)[:MAX_TEXTO]


def reap() -> int:
    """Tentativas que ficaram 'running' de uma execução anterior do app: o processo morreu no meio.

    Chamada no lifespan, ao lado de localai.reap_orphan(). A tarefa volta para 'queued' com o motivo
    registrado — não para 'failed', porque nada prova que a implementação estava errada; o que houve
    foi uma queda.
    """
    with db.session() as s:
        orfas = s.query(db.Attempt).filter(db.Attempt.status == "running").all()
        for att in orfas:
            att.status = "error"
            att.error = "O Forja foi encerrado enquanto esta tentativa rodava."
            att.finished_at = _now()
            task = s.get(db.Task, att.task_id)
            if task and task.status in ("queued", "loading_model", "implementing", "testing", "reviewing"):
                task.status = "queued"
                task.blocked_reason = "Retomada após o Forja ser encerrado no meio da tentativa."
                task.updated_at = _now()
        s.commit()
        return len(orfas)


# ------------------------------------------------------------------ leitura

def _task_dict(task: db.Task, attempts: list | None = None) -> dict:
    out = {"code": task.code, "title": task.title, "status": task.status,
           "feature_id": task.feature_id, "priority": task.priority,
           "depends_on": task.depends_on or [], "model_slot": task.model_slot, "agent": task.agent,
           "attempt_count": task.attempt_count, "max_attempts": task.max_attempts,
           "blocked_reason": task.blocked_reason, "contract": task.contract or {},
           "result": task.result, "updated_at": task.updated_at}
    if attempts is not None:
        out["attempts"] = attempts
    return out


def _attempt_dict(att: db.Attempt) -> dict:
    return {"n": att.n, "status": att.status, "worker": att.worker or {}, "strategy": att.strategy,
            "error": att.error, "seconds": att.seconds, "tokens": att.tokens,
            "result": att.result, "started_at": att.started_at, "finished_at": att.finished_at}


def board(conv_id: int) -> dict:
    """Árvore completa para o cockpit: funcionalidades, tarefas e as tentativas de cada uma."""
    with db.session() as s:
        feats = s.query(db.Feature).filter(db.Feature.conversation_id == conv_id).order_by(
            db.Feature.id).all()
        tasks = s.query(db.Task).filter(db.Task.conversation_id == conv_id).order_by(
            db.Task.priority.desc(), db.Task.id).all()
        ids = [t.id for t in tasks]
        tent: dict[int, list] = {i: [] for i in ids}
        if ids:
            for att in s.query(db.Attempt).filter(db.Attempt.task_id.in_(ids)).order_by(db.Attempt.n):
                tent[att.task_id].append(_attempt_dict(att))
        por_feature: dict[int, list] = {f.id: [] for f in feats}
        for t in tasks:
            por_feature.setdefault(t.feature_id, []).append(_task_dict(t, tent.get(t.id, [])))
        contagem = {st: sum(1 for t in tasks if t.status == st) for st in STATUSES}
        return {"features": [{"id": f.id, "title": f.title, "goal": f.goal, "status": f.status,
                              "tasks": por_feature.get(f.id, [])} for f in feats],
                "counts": {k: v for k, v in contagem.items() if v},
                "total": len(tasks), "done": contagem["completed"],
                "open": sum(1 for t in tasks if t.status in OPEN)}


def detail(conv_id: int, code: str) -> dict:
    with db.session() as s:
        task = _get(s, code, conv_id)
        atts = s.query(db.Attempt).filter(db.Attempt.task_id == task.id).order_by(db.Attempt.n).all()
        return _task_dict(task, [_attempt_dict(a) for a in atts])


def _publish(conv_id: int) -> None:
    """Avisa a UI que a árvore mudou. Falha em silêncio de propósito: publicar é o caminho rápido,
    o /board por polling é o que sempre funciona."""
    sink = SINK.get()
    if sink:
        try:
            sink(board(conv_id))
        except Exception:
            pass


# ------------------------------------------------------------------ ferramentas do Maestro
# Fora do REGISTRY, como exit_plan_mode e ask_user: só entram quando o modo é "maestro"
# (agent.available_tools). Assim não aparecem nas Configurações nem nos outros modos.

_CONTRACT_SCHEMA = {
    "type": "object",
    "description": "Implementation Contract: tudo o que o Worker precisa saber. Ele não vê esta conversa.",
    "properties": {
        "context": {"type": "string", "description": "Como o código está hoje, na parte que importa"},
        "goal": {"type": "string", "description": "O que esta tarefa deve alcançar, em uma frase"},
        "relevant_files": {"type": "array", "items": {"type": "string"},
                           "description": "Arquivos que ele precisa ler; o conteúdo vai junto no pedido"},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "do_not": {"type": "array", "items": {"type": "string"},
                   "description": "O que NÃO pode ser tocado (API, banco, layout existente...)"},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "verify_command": {"type": "string",
                           "description": "Comando que PROVA que ficou pronto (ex.: pytest -q tests/test_x.py). "
                                          "Roda depois que o Worker para e o resultado entra no task_result."},
        "expected_result": {"type": "string"}},
    "required": ["goal"]}


def _plan_feature(_root: Path, args: dict) -> str:
    out = create_feature(_conv(), args.get("title"), args.get("goal"), args.get("tasks") or [])
    linhas = [f"Funcionalidade '{out['title']}' criada com {len(out['tasks'])} tarefas:"]
    for t in out["tasks"]:
        dep = f" (depende de {', '.join(t['depends_on'])})" if t["depends_on"] else ""
        linhas.append(f"  {t['code']} {t['title']}{dep}")
    linhas.append("Agora execute uma por vez com run_task, respeitando as dependências.")
    return "\n".join(linhas)


PLAN_FEATURE = Tool(
    "plan_feature",
    "Registra uma funcionalidade e as tarefas em que você a decompôs. Cada tarefa leva um "
    "Implementation Contract completo: o Worker que vai executá-la NÃO vê esta conversa, só o "
    "contrato. Decomponha em tarefas pequenas, cada uma verificável por um comando.",
    {"type": "object", "properties": {
        "title": {"type": "string", "description": "Nome da funcionalidade"},
        "goal": {"type": "string", "description": "O objetivo geral dela"},
        "tasks": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"},
            "contract": _CONTRACT_SCHEMA,
            "depends_on": {"type": "array", "items": {"type": "string"},
                           "description": "Códigos (TASK-002) ou a posição na lista ('1') das tarefas "
                                          "que precisam concluir antes desta"},
            "model_slot": {"type": "string", "enum": list(SLOTS),
                           "description": "rapido para tarefa simples, capaz para tarefa difícil"},
            "agent": {"type": "string", "description": "Persona do projeto (.forja/agents/*.md), se houver"},
            "priority": {"type": "integer"}},
            "required": ["contract"]}}},
     "required": ["title", "tasks"]},
    _plan_feature)


def _list_tasks(_root: Path, args: dict) -> str:
    conv = _conv()
    filtro = str(args.get("status") or "").strip().lower()
    dados = board(conv)
    if not dados["total"]:
        return "Nenhuma tarefa ainda. Use plan_feature depois de analisar o projeto."
    marcas = {"completed": "[x]", "failed": "[!]", "needs_human": "[?]", "blocked": "[-]",
              "cancelled": "[/]"}
    linhas = []
    for feat in dados["features"]:
        tarefas = [t for t in feat["tasks"] if not filtro or t["status"] == filtro]
        if filtro and not tarefas:
            continue
        linhas.append(f"{feat['title']} [{feat['status']}]")
        for t in tarefas:
            extra = []
            if t["depends_on"]:
                extra.append("depende de " + ", ".join(t["depends_on"]))
            if t["attempt_count"]:
                extra.append(f"tentativa {t['attempt_count']}/{t['max_attempts']}")
            if t["model_slot"]:
                extra.append(t["model_slot"])
            if t["blocked_reason"]:
                extra.append(t["blocked_reason"][:120])
            linhas.append(f"  {marcas.get(t['status'], '[ ]')} {t['code']} {t['title']} [{t['status']}]"
                          + (f" — {'; '.join(extra)}" if extra else ""))
    linhas.append(f"Total: {dados['done']}/{dados['total']} concluídas, {dados['open']} abertas.")
    return "\n".join(linhas)


LIST_TASKS = Tool(
    "list_tasks",
    "Estado atual de todas as tarefas. Chame sempre que precisar decidir o próximo passo e SEMPRE "
    "depois de uma compactação de contexto: as tarefas vivem no banco, não nesta conversa.",
    {"type": "object", "properties": {
        "status": {"type": "string", "description": "Filtrar por um status (pending, failed, ...)"}}},
    _list_tasks, poll=True)


def _update_task(_root: Path, args: dict) -> str:
    conv = _conv()
    code = str(args.get("code") or "").strip().upper()
    mudou = []
    if novo := str(args.get("status") or "").strip().lower():
        set_status(code, novo, conv, str(args.get("reason") or ""))
        mudou.append(f"status={novo}")
    with db.session() as s:
        task = _get(s, code, conv)
        if (c := args.get("contract")) is not None:
            task.contract = normalize_contract(c)
            mudou.append("contrato")
        if (slot := args.get("model_slot")) is not None:
            slot = str(slot).strip().lower()
            if slot and slot not in SLOTS:
                raise ToolError(f"model_slot inválido '{slot}': use {', '.join(SLOTS)}.")
            task.model_slot = slot or None
            mudou.append(f"modelo={slot or 'automático'}")
        if (p := args.get("priority")) is not None:
            task.priority = int(p)
            mudou.append(f"prioridade={p}")
        if (m := args.get("max_attempts")) is not None:
            task.max_attempts = max(1, min(10, int(m)))
            mudou.append(f"max_attempts={task.max_attempts}")
        task.updated_at = _now()
        s.commit()
        estado = task.status
    if not mudou:
        raise ToolError("Nada para mudar: informe status, contract, model_slot, priority ou max_attempts.")
    _publish(conv)
    return f"{code} atualizada ({', '.join(mudou)}). Status atual: {estado}."


UPDATE_TASK = Tool(
    "update_task",
    "Muda uma tarefa. Use para FECHAR uma tarefa (status='completed') depois de conferir o resultado, "
    "para marcar 'blocked'/'needs_human' quando não der para seguir, ou para corrigir o contrato antes "
    "de uma nova tentativa. Quem decide que uma tarefa acabou é você, nunca o Worker.",
    {"type": "object", "properties": {
        "code": {"type": "string", "description": "Código da tarefa (TASK-003)"},
        "status": {"type": "string", "enum": list(STATUSES)},
        "reason": {"type": "string", "description": "Por quê, quando for blocked/needs_human/failed"},
        "contract": _CONTRACT_SCHEMA,
        "model_slot": {"type": "string", "enum": list(SLOTS)},
        "priority": {"type": "integer"},
        "max_attempts": {"type": "integer"}},
     "required": ["code"]},
    # Não é `mutating`: mexe na escrituração do próprio Forja, como o update_tasks — não escreve
    # arquivo do usuário nem roda comando. Marcada como mutante, ela pedia um card de aprovação a
    # cada tarefa fechada, e uma execução autônoma parava em toda conclusão.
    _update_task)


def _nao_usado(*_a, **_k):
    raise RuntimeError("run_task é tratado pelo loop do agente (maestro.run_task)")


RUN_TASK = Tool(
    "run_task",
    "Entrega a tarefa a um Worker e devolve o resultado medido (arquivos alterados, testes, erros) "
    "em JSON. O Worker recebe só o Implementation Contract, roda num modelo próprio e não vê esta "
    "conversa. Ele NÃO fecha a tarefa: leia o resultado e decida com update_task. "
    "Numa nova tentativa, diga em 'strategy' o que deve ser feito diferente.",
    {"type": "object", "properties": {
        "code": {"type": "string", "description": "Código da tarefa (TASK-003)"},
        "strategy": {"type": "string",
                     "description": "Obrigatório a partir da 2ª tentativa: o que mudar em relação à "
                                    "tentativa anterior. Repetir a mesma abordagem só gasta tempo."}},
     "required": ["code"]},
    _nao_usado,
    # Não é `mutating` pelo mesmo motivo do delegate_task: quem altera o projeto são as chamadas do
    # Worker lá dentro, e cada uma passa por policy.decide e pelo card de aprovação. Pedir aprovação
    # aqui também só perguntaria duas vezes pela mesma coisa.
    #
    # `poll` isenta do LoopDetector: chamar run_task três vezes na mesma tarefa é o ciclo de
    # tentativas funcionando, não um laço. O freio de verdade é max_attempts, no banco.
    poll=True)

TOOLS = [PLAN_FEATURE, LIST_TASKS, UPDATE_TASK, RUN_TASK]
# No modo Plano a Maestro só lê. As outras três não são `mutating` (ver os comentários acima), então
# o filtro genérico do modo Plano não as pegaria — e planejar não é hora de criar, mudar nem
# despachar tarefa. Despachar seria inócuo (o Worker herda a permissão e só recebe leitura), mas
# gastaria um modelo inteiro para não poder escrever nada.
PLAN_SAFE = {LIST_TASKS.name}
# Fora do REGISTRY (não aparecem nas Configurações nem nos outros modos), mas encontráveis por
# get_tool/execute — sem isto o Maestro chama plan_feature e recebe "Ferramenta desconhecida".
for _t in TOOLS:
    register_extra(_t)

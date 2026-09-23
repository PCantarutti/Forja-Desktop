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
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
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
    # Os estados de trabalho voltam para 'queued' porque toda tentativa nova recomeça o ciclo: o
    # Maestro que não gostou do resultado redespacha a mesma tarefa, e sem esta aresta o run_task
    # batia numa transição ilegal e derrubava o turno.
    "pending": {"queued"},
    "queued": {"loading_model", "implementing", "pending"},
    "loading_model": {"implementing", "failed", "queued"},
    "implementing": {"testing", "reviewing", "failed", "queued", "completed"},
    "testing": {"reviewing", "failed", "queued", "completed"},
    "reviewing": {"completed", "failed", "queued"},
    "failed": {"queued", "pending", "completed"},  # a Maestro conferiu e aceita: fecha direto
    "blocked": {"pending", "queued"},
    "needs_human": {"pending", "queued"},
    "completed": {"pending", "queued"},   # reabrir: o Maestro achou um bug depois
    "cancelled": {"pending", "queued"},
}
SEMPRE = {"cancelled", "needs_human", "blocked"}

CONTRACT_FIELDS = ("type", "context", "goal", "relevant_files", "requirements", "constraints", "do_not",
                   "acceptance_criteria", "verify_command", "expected_result")
# Tipo da tarefa: diz ao Worker que tipo de mudança é (correção não é hora de refatorar) e ao
# roteador que especialista chamar (subagents.ROTA_POR_TIPO). Fora da lista, é ignorado.
TIPOS = ("feature", "bugfix", "refactor", "test", "ui", "docs", "chore")
LISTAS = ("relevant_files", "requirements", "constraints", "do_not", "acceptance_criteria")
MAX_TASKS_POR_FEATURE = 40
MAX_ITENS = 20          # itens por lista do contrato
MAX_TEXTO = 4000        # caracteres por campo de texto do contrato
SLOTS = ("rapido", "capaz", "nuvem")


def _slot_valido(slot: str | None, onde: str = "") -> str | None:
    """model_slot aceito: nível ou especialidade cadastrada (mesmo sem modelo: cai no capaz)."""
    slot = str(slot or "").strip().lower() or None
    ids = [e["id"] for e in getattr(config, "WORKER_ESPECIALIDADES", [])]
    if slot and slot not in SLOTS and slot not in ids:
        raise ToolError(f"model_slot inválido '{slot}'{onde}: use {', '.join([*SLOTS, *ids])}.")
    return slot


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


def _verify_invalido(cmd: str) -> str:
    """Motivo para recusar um comando de verificação, ou ''. Ele roda por run_command depois do
    Worker e tem de TERMINAR — no StockFlow a Maestro escreveu "npm run dev; browser_validate(...)":
    servidor que nunca acaba (timeout de 180 s, tentativa marcada como falha) e uma ferramenta dela
    escrita como se fosse comando de terminal."""
    from . import shell
    if re.search(r"\b(browser_[a-z]+|serve_(start|status|stop)|run_(task|command))\b", cmd):
        return ("verify_command é comando de terminal: ferramenta (browser_validate, serve_start...) não "
                "roda ali. Conferir no navegador é trabalho seu, na validação da entrega.")
    if shell.parece_servidor(cmd):
        return ("verify_command precisa TERMINAR (build, testes, lint, tsc). Servidor de desenvolvimento "
                "nunca termina: a verificação estouraria o tempo e a tarefa viraria falha.")
    return ""


def normalize_contract(raw) -> dict:
    """Implementation Contract validado. Só `goal` é obrigatório — exigir os nove campos faria o
    modelo pequeno travar numa chamada impossível em vez de começar a trabalhar."""
    if not isinstance(raw, dict):
        raise ToolError("contract deve ser um objeto com os campos do Implementation Contract.")
    if not str(raw.get("goal") or "").strip():
        raise ToolError("O contrato precisa de 'goal': o que esta tarefa deve alcançar, em uma frase.")
    if motivo := _verify_invalido(str(raw.get("verify_command") or "")):
        raise ToolError(motivo)
    out: dict = {}
    for campo in CONTRACT_FIELDS:
        valor = raw.get(campo)
        if campo == "type":
            if (tipo := str(valor or "").strip().lower()) in TIPOS:
                out[campo] = tipo
        elif campo in LISTAS:
            if itens := _lista(valor):
                out[campo] = itens
        elif texto := str(valor or "").strip()[:MAX_TEXTO]:
            out[campo] = texto
    return out


def render_contract(task: db.Task, erro_anterior: str = "", strategy: str = "") -> str:
    """O contrato virando o briefing que o Worker lê. Texto porque é o que o modelo consome, mas
    gerado a partir de dados estruturados: o Maestro não escreve o brief à mão."""
    c = task.contract or {}
    tipo = f" ({c['type']})" if c.get("type") else ""
    partes = [f"TAREFA {task.code}{tipo}: {task.title}"]
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
        # sem falha antes, o strategy é recado da Maestro ("corrija também os IDs do HTML"), não troca
        # de abordagem
        titulo = "MUDE A ABORDAGEM NESTA TENTATIVA" if erro_anterior else "ORIENTAÇÃO DA MAESTRO PARA ESTA TAREFA"
        partes.append(f"{titulo}\n{strategy}")
    if escopo := [a for a in c.get("relevant_files") or [] if not a.replace(chr(92), "/").removeprefix("./").startswith(".forja/")]:
        # No TaskBoard o Worker da camada de dados reescreveu o CSS de outra tarefa.
        partes.append("ESCOPO\nMexa só em: " + ", ".join(escopo) + ". Precisa mudar outro arquivo? Mude o "
                      "mínimo e diga no relatório — outra tarefa pode ser dona dele; não o reescreva inteiro.")
    return "\n\n".join(partes)


# ------------------------------------------------------------------ CRUD

def _numero(code: str) -> int:
    try:
        return int(str(code).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _proximo_code(s, conv_id: int) -> str:
    """Maior número + 1, não contagem + 1: tarefa trazida de outra conversa chega com o número dela."""
    return f"TASK-{max((_numero(c) for c in _codes_existentes(s, conv_id)), default=0) + 1:03d}"


TRABALHANDO = ("queued", "loading_model", "implementing", "testing")


def assume(de: list[int], para: int) -> list[str]:
    """COPIA para a conversa `para` as funcionalidades abertas das conversas `de`, com as tarefas.

    Cópia e não mudança: a lista continua na conversa antiga para consulta (marcada `copiada_para`),
    e o trabalho segue na nova — o run_task só enxerga tarefas da própria conversa. As tentativas
    ficam com as tarefas originais (histórico); a cópia leva o contador, para o limite de tentativas
    continuar valendo. Tarefa que estava no meio (queued/implementing...) volta a 'pending': quem
    estava trabalhando nela era a outra sessão. Código repetido no destino é renumerado, e o
    depends_on acompanha. Devolve os títulos copiados."""
    if not de:
        return []
    with db.session() as s:
        feats = s.query(db.Feature).filter(db.Feature.conversation_id.in_(de), db.Feature.copiada_para.is_(None),
                                           db.Feature.status.notin_(("done", "cancelled"))).all()
        if not feats:
            return []
        usados = _codes_existentes(s, para)
        prox = max((_numero(c) for c in usados), default=0)
        for f in feats:
            nova = db.Feature(conversation_id=para, title=f.title, goal=f.goal, status=f.status)
            s.add(nova)
            s.flush()
            troca: dict[str, str] = {}
            copias = []
            for t in s.query(db.Task).filter(db.Task.feature_id == f.id).order_by(db.Task.id).all():
                code = t.code
                if code in usados:
                    prox += 1
                    code = troca[t.code] = f"TASK-{prox:03d}"
                usados.add(code)
                prox = max(prox, _numero(code))
                copias.append(db.Task(
                    feature_id=nova.id, conversation_id=para, code=code, title=t.title,
                    contract=t.contract, depends_on=list(t.depends_on or []), priority=t.priority,
                    status="pending" if t.status in TRABALHANDO else t.status, model_slot=t.model_slot,
                    agent=t.agent, max_attempts=t.max_attempts, attempt_count=t.attempt_count,
                    result=t.result, blocked_reason=t.blocked_reason))
            for c in copias:
                if troca and c.depends_on:
                    c.depends_on = [troca.get(d, d) for d in c.depends_on]
                s.add(c)
            f.copiada_para = para
            f.updated_at = _now()
        s.commit()
        titulos = [f.title for f in feats]
    for c in de:
        _publish(c)
    _publish(para)
    return titulos


def _normaliza(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(texto or "").lower()).strip()


def sem_copias(tasks: list) -> tuple[list, int]:
    """Tira do plano a tarefa repetida IGUAL (título e contrato), remapeando as dependências por
    posição. No TaskBoard a Maestro mandou a TASK-004 duas vezes (virou TASK-009); recusar custaria
    gerar o plano inteiro de novo. Só cópia exata: título parecido pode ser tarefa diferente."""
    vistos: dict[str, int] = {}
    mapa: dict[str, str] = {}
    out: list = []
    for i, t in enumerate(tasks, 1):
        chave = json.dumps(t, sort_keys=True, ensure_ascii=False) if isinstance(t, dict) else ""
        if chave and chave in vistos:
            mapa[str(i)] = str(vistos[chave])
            continue
        out.append(t)
        mapa[str(i)] = str(len(out))
        if chave:
            vistos[chave] = len(out)
    if len(out) < len(tasks):
        for t in out:
            if isinstance(t, dict) and isinstance(t.get("depends_on"), list):
                t["depends_on"] = [mapa.get(str(d).strip(), d) for d in t["depends_on"]]
    return out, len(tasks) - len(out)


def duplicadas(conv_id: int, tasks: list) -> list[str]:
    """Tarefas do plano que repetem uma tarefa ABERTA desta conversa (mesmo título ou quase).

    No StockFlow, a sessão nova replanejou TASK-008/009, que já estavam pendentes, como 010/011 — e
    depois de novo como 012. Aqui o plano volta com os códigos que já existem."""
    with db.session() as s:
        abertas = [(t.code, t.title) for t in s.query(db.Task).join(db.Feature, db.Feature.id == db.Task.feature_id)
                   .filter(db.Task.conversation_id == conv_id, db.Task.status.in_(OPEN),
                           db.Feature.copiada_para.is_(None))]
    achadas = []
    for bruto in tasks:
        if not isinstance(bruto, dict):
            continue
        nome = _normaliza(bruto.get("title") or (bruto.get("contract") or {}).get("goal"))
        if not nome:
            continue
        for code, titulo in abertas:
            if SequenceMatcher(None, nome, _normaliza(titulo)).ratio() >= 0.85:
                achadas.append(f"'{bruto.get('title') or nome}' já existe como {code} ('{titulo}')")
                break
    return achadas


def pendencias(conv_id: int) -> list[str]:
    """O que a Maestro deixou para trás: tarefa esperando a revisão dela, e tarefa pendente ou que
    falhou (com tentativas sobrando) que já pode rodar. No StockFlow a TASK-005 ficou em 'reviewing'
    e a 006 pendente enquanto ela abria outra funcionalidade."""
    with db.session() as s:
        tarefas = (s.query(db.Task).join(db.Feature, db.Feature.id == db.Task.feature_id)
                   .filter(db.Task.conversation_id == conv_id, db.Feature.copiada_para.is_(None),
                           db.Task.status.in_(("reviewing", "pending", "failed")))
                   .order_by(db.Task.id).all())
        feitas = {c for (c,) in s.query(db.Task.code).filter(
            db.Task.conversation_id == conv_id, db.Task.status.in_(TERMINAL))}
        out = []
        for t in tarefas:
            if t.status == "reviewing":
                out.append(f"{t.code} ({t.title}) espera sua revisão: feche com update_task ou redespache")
            elif t.status == "failed" and t.attempt_count < t.max_attempts:
                out.append(f"{t.code} ({t.title}) falhou e ainda tem tentativas")
            elif t.status == "pending" and all(d in feitas for d in (t.depends_on or [])):
                out.append(f"{t.code} ({t.title}) está pendente e pronta para rodar")
        return out


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


def create_feature(conv_id: int, title: str, goal: str, tasks: list, feature_id: int | None = None) -> dict:
    """Cria a funcionalidade e as tarefas dela numa transação só. Devolve o resumo com os códigos.

    Com `feature_id`, as tarefas entram numa funcionalidade que já existe e ela volta a 'active':
    é o caminho das correções que a validação da entrega encontrou."""
    if not isinstance(tasks, list) or not tasks:
        raise ToolError("Informe 'tasks': a lista de tarefas em que a funcionalidade foi decomposta.")
    # Título que falta sai do objetivo, ou da primeira tarefa. Os modelos esquecem o 'title' com
    # frequência na primeira chamada, e recusar custava uma rodada inteira só para repetir o pedido
    # com um nome — o mesmo motivo pelo qual o título de cada tarefa já sai do goal dela.
    title = str(title or "").strip()[:200] or _titulo_de(goal, tasks)
    if len(tasks) > MAX_TASKS_POR_FEATURE:
        raise ToolError(f"No máximo {MAX_TASKS_POR_FEATURE} tarefas por funcionalidade. "
                        "Decomponha em mais de uma funcionalidade.")
    with db.session() as s:
        if feature_id:
            feat = s.get(db.Feature, int(feature_id))
            if not feat or feat.conversation_id != conv_id:
                raise ToolError(f"Funcionalidade {feature_id} não existe nesta conversa.")
            feat.status = "active"
            feat.updated_at = _now()
        else:
            feat = db.Feature(conversation_id=conv_id, title=title,
                              goal=str(goal or "").strip()[:MAX_TEXTO], status="active")
            s.add(feat)
            s.flush()
        criadas: list[tuple[db.Task, object]] = []
        # Duas passadas: a primeira cria tudo, a segunda resolve depends_on — assim uma tarefa pode
        # depender de outra declarada depois dela na mesma chamada.
        guia = _guia_no_contrato()
        for i, bruto in enumerate(tasks):
            if not isinstance(bruto, dict):
                raise ToolError(f"Tarefa {i + 1} deve ser um objeto com title e contract.")
            titulo = str(bruto.get("title") or "").strip()[:200]
            contrato = normalize_contract(bruto.get("contract") or ({"goal": titulo} if titulo else None))
            if guia and guia not in contrato.get("relevant_files", []):
                contrato["relevant_files"] = [guia, *contrato.get("relevant_files", [])][:MAX_ITENS]
            titulo = titulo or contrato["goal"][:200]
            slot = _slot_valido(bruto.get("model_slot"), f" na tarefa {i + 1}")
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


def _guia_no_contrato() -> str:
    """Caminho do guia visual, se o projeto tem tela e o guia existe: vai nos relevant_files de toda
    tarefa (o Worker recebe o conteúdo junto do briefing). '' fora de execução ou sem guia."""
    try:
        from . import qualidade, workspace
        root = workspace.root()
        return qualidade.GUIA if qualidade.tem_tela(root) and qualidade.guia_visual(root) else ""
    except Exception:
        return ""


def _objetivo_da_conversa() -> str | None:
    """Primeira linha do pedido do usuário: nome melhor para a funcionalidade sem título que o
    objetivo da primeira tarefa ("Criar package.json…" virou o nome do TaskBoard inteiro)."""
    with db.session() as s:
        m = (s.query(db.Message).filter(db.Message.conversation_id == _conv(), db.Message.role == "user")
             .order_by(db.Message.id).first())
    linha = next((l for l in (m.content if m else "").splitlines() if l.strip()), "")
    linha = re.sub(r"[*_`#>]+", "", linha).strip()
    return (linha[:80].rstrip(" .,;:") + ("…" if len(linha) > 80 else "")) or None


def _titulo_de(goal, tasks: list) -> str:
    texto = str(goal or "").strip()
    if not texto:
        primeira = tasks[0] if isinstance(tasks[0], dict) else {}
        texto = str(primeira.get("title") or (primeira.get("contract") or {}).get("goal") or "").strip()
    linha = texto.splitlines()[0] if texto else ""
    return (linha[:80].rstrip(" .,;:") + ("…" if len(linha) > 80 else "")) or "Funcionalidade"


def set_status(code: str, novo: str, conv_id: int | None = None, reason: str = "") -> dict:
    conv_id = conv_id if conv_id is not None else _conv()
    novo = str(novo or "").strip().lower()
    if novo not in STATUSES:
        raise ToolError(f"Status inválido '{novo}'. Use: {', '.join(STATUSES)}.")
    with db.session() as s:
        task = _get(s, code, conv_id)
        atual = task.status
        # A Maestro conferiu sozinha (rodou os testes) o trabalho que um Worker já fez e quer fechar a
        # tarefa que ela mesma tinha devolvido para a fila: sem esta saída, numa rodada real ela tentou
        # oito vezes e desistiu com needs_human em tarefas com os testes passando.
        conferida = novo == "completed" and atual in ("pending", "queued", "needs_human", "blocked")             and task.attempt_count > 0
        if novo != atual and novo not in SEMPRE and not conferida and novo not in TRANSITIONS.get(atual, set()):
            permitidos = ", ".join(sorted(TRANSITIONS.get(atual, set()) | SEMPRE))
            raise ToolError(f"{task.code} está em '{atual}' e não pode ir para '{novo}'. "
                            f"De '{atual}' dá para: {permitidos}.")
        task.status = novo
        task.blocked_reason = (reason or None) if novo in ("blocked", "needs_human", "failed") else None
        task.updated_at = _now()
        if novo == "completed":
            _fecha_feature(s, task.feature_id, task.id)
        elif novo in OPEN and (feat := s.get(db.Feature, task.feature_id)) and feat.status in ("validating", "done"):
            # Tarefa reaberta (reenviada, redespachada): a entrega mudou e precisa ser validada de novo.
            feat.status = "active"
            feat.updated_at = _now()
        s.commit()
        out = _task_dict(task)
    _publish(conv_id)
    return out


def _fecha_feature(s, feature_id: int, fechando: int) -> None:
    """Sem tarefa aberta, a funcionalidade vai para 'validating', não para 'done': tarefa concluída
    uma a uma não prova que o conjunto funciona. Quem encerra é a Maestro, depois de validar a
    entrega inteira (close_feature, pela session_note). `fechando` é a tarefa que está virando
    completed nesta mesma transação — ela ainda aparece como aberta na consulta."""
    abertas = s.query(db.Task).filter(db.Task.feature_id == feature_id, db.Task.id != fechando,
                                      db.Task.status.in_(OPEN)).count()
    feat = s.get(db.Feature, feature_id)
    if feat and not abertas and feat.status != "done":
        feat.status = "validating"
        feat.updated_at = _now()


def validando(conv_id: int) -> list[dict]:
    """Funcionalidades desta conversa esperando a validação da entrega."""
    with db.session() as s:
        feats = s.query(db.Feature).filter(db.Feature.conversation_id == conv_id,
                                           db.Feature.status == "validating").order_by(db.Feature.id).all()
        return [{"id": f.id, "title": f.title, "goal": f.goal, "verify": _verifies(s, f.id), "desde": f.updated_at}
                for f in feats]


def pedido_de_validacao(f: dict) -> str:
    """O que a Maestro precisa fazer antes de encerrar uma funcionalidade."""
    cmds = "; ".join(f"`{c}`" for c in f["verify"]) or "a suíte de testes do projeto"
    return (f"Todas as tarefas de '{f['title']}' (feature_id={f['id']}) concluíram. VALIDE A ENTREGA "
            f"inteira antes de encerrar: rode {cmds} de novo, juntos, mais build/lint do projeto se houver; "
            + ("browser_validate se tem tela; " if config.MAESTRO_BROWSER else "") + "confira o objetivo"
            + (f" ({f['goal'][:300]})" if f["goal"] else "") + ". Passou: session_note encerra. "
            f"Falhou: plan_feature(feature_id={f['id']}, tasks=[correções]).")


# O que conta como validação feita pela própria Maestro. Os comandos do Worker não entram: eles
# ficam na tentativa, não nesta conversa, e são justamente o que a validação confere.
VALIDACOES = ("run_command", "browser_validate", "browser_console", "browser_read", "browser_screenshot")


def encerra_validadas(conv_id: int) -> list[str]:
    """Encerra as funcionalidades em 'validating' — só depois de a Maestro ter validado de fato:
    rodado um comando ou conferido no navegador DEPOIS que a funcionalidade entrou em validação.
    Recusa (ToolError) se alguma não foi validada. Devolve os títulos encerrados."""
    with db.session() as s:
        feats = s.query(db.Feature).filter(db.Feature.conversation_id == conv_id,
                                           db.Feature.status == "validating").all()
        if not feats:
            return []
        feitas = [m.created_at for m in s.query(db.Message.created_at).filter(
            db.Message.conversation_id == conv_id, db.Message.role == "tool",
            db.Message.name.in_(VALIDACOES))]
        from . import qualidade, workspace  # import tardio: qualidade importa este módulo
        for f in feats:
            if not any(t >= f.updated_at for t in feitas):
                raise ToolError("A entrega ainda não foi validada. " + pedido_de_validacao(
                    {"id": f.id, "title": f.title, "goal": f.goal, "verify": _verifies(s, f.id)}))
            if faltas := qualidade.faltas_para_entregar(conv_id, f.updated_at, workspace.root()):
                raise ToolError(f"Projeto com tela: '{f.title}' ainda não pode ser encerrada. Falta: "
                                + "; ".join(faltas) + ".")
        for f in feats:
            f.status = "done"
            f.updated_at = _now()
        s.commit()
        titulos = [f.title for f in feats]
    _publish(conv_id)
    return titulos


def _verifies(s, feature_id: int) -> list[str]:
    cmds: list[str] = []
    for (contrato,) in s.query(db.Task.contract).filter(db.Task.feature_id == feature_id):
        c = (contrato or {}).get("verify_command")
        if c and c not in cmds:
            cmds.append(c)
    return cmds


def unmet_deps(task: db.Task, conv_id: int | None = None) -> list[str]:
    """Códigos de dependências que ainda não concluíram. Lista vazia = pode rodar."""
    if not task.depends_on:
        return []
    conv_id = conv_id if conv_id is not None else task.conversation_id
    with db.session() as s:
        # Cancelada conta como resolvida: a dependência foi descartada de propósito. Contar como
        # aberta travava a tarefa para sempre (no StockFlow, a Maestro cancelou a TASK-008 e a 011
        # continuou bloqueada por ela, e o jeito que achou foi recriar tudo numa feature nova).
        feitas = {c for (c,) in s.query(db.Task.code).filter(
            db.Task.conversation_id == conv_id, db.Task.code.in_(list(task.depends_on)),
            db.Task.status.in_(TERMINAL))}
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


def save_transcript(attempt_id: int, transcript: list) -> None:
    """Grava a conversa do Worker até agora. Chamada a cada rodada dele."""
    with db.session() as s:
        att = s.get(db.Attempt, attempt_id)
        if att:
            att.transcript = list(transcript)
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


def _attempt_dict(att: db.Attempt, transcript: bool = False) -> dict:
    """`transcript` só no detalhe de uma tarefa: o /board é consultado a cada 2 s e levaria a conversa
    de todos os Workers de todas as tarefas junto."""
    out = {"n": att.n, "status": att.status, "worker": att.worker or {}, "strategy": att.strategy,
           "error": att.error, "seconds": att.seconds, "tokens": att.tokens,
           "result": att.result, "started_at": att.started_at, "finished_at": att.finished_at,
           "has_transcript": bool(att.transcript)}
    if transcript:
        out["transcript"] = att.transcript or []
    return out


def _utc(t: datetime | None) -> str | None:
    """ISO com Z: o SQLite devolve sem fuso, e o navegador leria como hora local."""
    if t is None:
        return None
    return (t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t).isoformat() + "Z"


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
        # Relógio da conversa para o cabeçalho: do primeiro pedido até a última coisa que aconteceu
        # (mensagem, tarefa ou tentativa). A estatística da Maestro soma só o tempo DELA no modelo —
        # no TaskBoard mostrava 35 min de uma conversa de 1h18.
        from sqlalchemy import func
        inicio, ultima_msg = s.query(func.min(db.Message.created_at), func.max(db.Message.created_at)).filter(
            db.Message.conversation_id == conv_id).one()
        marcas = [ultima_msg, *(t.updated_at for t in tasks),
                  *(a.finished_at or a.started_at for a in (s.query(db.Attempt).filter(db.Attempt.task_id.in_(ids))
                                                             if ids else []))]
        sem_fuso = [m.astimezone(timezone.utc).replace(tzinfo=None) if m.tzinfo else m for m in marcas if m]
        ultima = max(sem_fuso, default=None)
        return {"inicio": _utc(inicio), "ultima": _utc(ultima), "features": [{"id": f.id, "title": f.title, "goal": f.goal, "status": f.status,
                              "copiada_para": f.copiada_para,
                              "tasks": por_feature.get(f.id, [])} for f in feats],
                "counts": {k: v for k, v in contagem.items() if v},
                # cancelada não conta: "10/11" com uma cancelada parecia trabalho faltando
                "total": len(tasks) - contagem["cancelled"], "done": contagem["completed"],
                "open": sum(1 for t in tasks if t.status in OPEN)}


def detail(conv_id: int, code: str) -> dict:
    with db.session() as s:
        task = _get(s, code, conv_id)
        atts = s.query(db.Attempt).filter(db.Attempt.task_id == task.id).order_by(db.Attempt.n).all()
        return _task_dict(task, [_attempt_dict(a, transcript=True) for a in atts])


def _publish(conv_id: int) -> None:
    """Avisa a UI que a árvore mudou e atualiza o espelho em .forja/ (progress.md, tasks.json). Falha
    em silêncio de propósito: publicar é o caminho rápido, o /board por polling é o que sempre
    funciona, e o espelho é reescrito inteiro na próxima mudança."""
    try:
        from . import projstate  # import tardio: projstate importa este módulo
        projstate.sync(conv_id)
    except Exception:
        pass
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
        "type": {"type": "string", "enum": list(TIPOS),
                 "description": "feature (nova), bugfix (correção), refactor, test, ui (tela/visual), docs, chore"},
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
                                          "Executor de testes, não python -c: esse pede aprovação. "
                                          "Roda depois que o Worker para e o resultado entra no task_result."},
        "expected_result": {"type": "string"}},
    "required": ["goal"]}


def _plan_feature(_root: Path, args: dict) -> str:
    from . import projstate, workspace  # import tardio: projstate importa este módulo
    root = workspace.root()
    # Portão do Project State: sem ele, a conversa seguinte começa do zero. Instrução no prompt não
    # bastou (um modelo de 9B leu os arquivos vazios e planejou assim mesmo); aqui não tem como pular.
    from . import qualidade
    com_tela = qualidade.tem_tela(root) or qualidade.plano_com_tela(args.get("tasks") or [])
    if (root / projstate.PASTA).is_dir() and com_tela and not qualidade.guia_visual(root):
        raise ToolError(f"Projeto com tela: antes de planejar, escreva {qualidade.GUIA} com o guia visual "
                        "(paleta, tipografia, espaçamentos, componentes e o tom da interface). Ele vai no "
                        "contrato de cada Worker, e é o que mantém as telas com a mesma cara.")
    if (root / projstate.PASTA).is_dir() and projstate.vazio(projstate.forja_md(root)):
        raise ToolError(f"Antes de planejar, escreva {config.PROJECT_MEMORY_FILE} (o que é o projeto, stack, "
                        f"como rodar e testar, convenções) e, se couber, {projstate.PASTA}/architecture.md e "
                        "knowledge/. Fatos curtos: é o que a próxima conversa lê primeiro.")
    if repetidas := duplicadas(_conv(), args.get("tasks") or []):
        raise ToolError("Estas tarefas já existem e estão abertas: " + "; ".join(repetidas)
                        + ". Execute a existente com run_task (ou ajuste com update_task) em vez de criar outra.")
    if args.get("feature_id"):
        with db.session() as s:
            alvo = s.get(db.Feature, int(args["feature_id"]))
            if alvo and alvo.copiada_para:
                raise ToolError(f"A funcionalidade {alvo.id} continua na conversa {alvo.copiada_para}; "
                                "não acrescente tarefas aqui.")
    anexada = None
    if not args.get("feature_id"):
        # Plano no meio da validação é correção da entrega: vai para a funcionalidade em validação. No
        # TaskBoard cada bug achado no navegador virou funcionalidade nova (12, 13), cada uma pedindo a
        # própria validação, mesmo com o aviso dizendo feature_id=11.
        with db.session() as s:
            validando = s.query(db.Feature).filter(db.Feature.conversation_id == _conv(),
                                                   db.Feature.status == "validating",
                                                   db.Feature.copiada_para.is_(None)).all()
        if len(validando) == 1:
            anexada = validando[0]
            args = {**args, "feature_id": anexada.id}
    antes = pendencias(_conv()) if not args.get("feature_id") else []
    tarefas, copias = sem_copias(args.get("tasks") or [])
    out = create_feature(_conv(), args.get("title") or (None if args.get("goal") else _objetivo_da_conversa()), args.get("goal"), tarefas,
                         args.get("feature_id") or None)
    linhas = [f"Funcionalidade '{out['title']}' (feature_id={out['feature_id']}): "
              f"{len(out['tasks'])} tarefas {'novas' if args.get('feature_id') else 'criadas'}"
              + (f" ({copias} cópia(s) repetida(s) no plano ignorada(s))" if copias else "") + ":"]
    if anexada:
        linhas.insert(0, f"(Entraram na funcionalidade {anexada.id}, que está em validação: são correções da entrega.)")
    for t in out["tasks"]:
        dep = f" (depende de {', '.join(t['depends_on'])})" if t["depends_on"] else ""
        linhas.append(f"  {t['code']} {t['title']}{dep}")
    linhas.append("Agora execute uma por vez com run_task, respeitando as dependências.")
    if antes:
        linhas.append("ATENÇÃO, trabalho aberto de antes (resolva antes de seguir): " + "; ".join(antes) + ".")
    return "\n".join(linhas)


PLAN_FEATURE = Tool(
    "plan_feature",
    "Registra uma funcionalidade e as tarefas em que você a decompôs. Cada tarefa leva um "
    "Implementation Contract completo: o Worker que vai executá-la NÃO vê esta conversa, só o "
    "contrato. Decomponha em tarefas pequenas, cada uma verificável por um comando.",
    {"type": "object", "properties": {
        "feature_id": {"type": "integer", "description": "Acrescenta as tarefas a esta funcionalidade "
                                                         "(correções da validação) em vez de criar outra"},
        "title": {"type": "string", "description": "Nome da funcionalidade"},
        "goal": {"type": "string", "description": "O objetivo geral dela"},
        "tasks": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"},
            "contract": _CONTRACT_SCHEMA,
            "depends_on": {"type": "array", "items": {"type": "string"},
                           "description": "Códigos (TASK-002) ou a posição na lista ('1') das tarefas "
                                          "que precisam concluir antes desta"},
            "model_slot": {"type": "string",
                           "description": "rapido (simples), capaz (difícil) ou o id de um Worker especialista "
                                          "da lista do prompt. Vazio: escolhido pelo tipo e pelos arquivos"},
            "agent": {"type": "string", "description": "Persona do projeto (.forja/agents/*.md), se houver"},
            "priority": {"type": "integer",
                         "description": "Urgência: MAIOR sai primeiro (padrão 0). Não é a ordem de execução — "
                                        "ordem é depends_on"}},
            "required": ["contract"]}}},
     "required": ["tasks"]},
    _plan_feature)


def _list_tasks(_root: Path, args: dict) -> str:
    conv = _conv()
    filtro = str(args.get("status") or "").strip().lower()
    dados = board(conv)
    if not dados["features"]:
        return "Nenhuma tarefa ainda. Use plan_feature depois de analisar o projeto."
    marcas = {"completed": "[x]", "failed": "[!]", "needs_human": "[?]", "blocked": "[-]",
              "cancelled": "[/]"}
    linhas = []
    copiadas = [f for f in dados["features"] if f.get("copiada_para")]
    for feat in dados["features"]:
        if feat.get("copiada_para"):
            continue  # continua em outra conversa: aqui é só consulta
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
    if copiadas:
        linhas.append(f"({len(copiadas)} funcionalidade(s) continuaram em outra conversa e não rodam aqui.)")
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
            slot = _slot_valido(slot)
            task.model_slot = slot
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
    saida = f"{code} atualizada ({', '.join(mudou)}). Status atual: {estado}."
    if estado == "completed":
        saida += "".join("\n" + pedido_de_validacao(f) for f in validando(conv))
    return saida


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
        "model_slot": {"type": "string", "description": "rapido, capaz ou id de especialista; '' = automático"},
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
    "Numa nova tentativa, diga em 'strategy' o que deve ser feito diferente. Várias tarefas "
    "independentes de uma vez: 'codes' (rodam juntas no modo paralelo).",
    {"type": "object", "properties": {
        "code": {"type": "string", "description": "Código da tarefa (TASK-003)"},
        "codes": {"type": "array", "items": {"type": "string"},
                  "description": "Várias tarefas independentes numa chamada só (TASK-001, TASK-002)"},
        "strategy": {"type": "string",
                     "description": "Obrigatório a partir da 2ª tentativa: o que mudar em relação à "
                                    "tentativa anterior. Repetir a mesma abordagem só gasta tempo."}}},
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

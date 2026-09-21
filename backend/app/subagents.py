"""Subagentes: o agente principal delega uma subtarefa para outro modelo.

Três slots nas Configurações: `rapido` (modelo menor/rápido), `capaz` (maior/mais lento) e `nuvem`
(rede de segurança — entra quando o slot escolhido não roda nesta máquina ou falha; o modelo nunca a
escolhe sozinho). O agente principal chama `delegate_task(task, level, files, done_when)` e escolhe o
nível pela dificuldade. O subagente roda um loop próprio, com as mesmas ferramentas (menos
delegate_task), as mesmas aprovações e a mesma pasta de trabalho, e devolve só o relatório final. Os
passos aparecem na UI dentro do bloco da delegação, mas não entram no histórico do agente principal
(só o relatório entra).

O relatório dele é palavra dele. Por isso o `done_when` é executado DEPOIS que ele para, pelo caminho
normal de aprovação do agente: o que volta para o principal é medição, não autoavaliação. A revisão do
diff só entra quando essa prova não existe (sem `done_when`, ou com ele reprovando) — com exit code 0 na
mão, o parecer de um modelo menor que o autor rende falso-positivo, não bug.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable

from . import config, db, gitops, llm, workspace
from .parsing import parse_text_tool_calls, split_think
from .tools import Tool, ToolError, register, resolve_path, vision_caps

try:  # localai só existe no Forja desktop; no Docker não há modelo local embutido
    from . import localai
except ImportError:  # pragma: no cover
    localai = None

LEVELS = {"rapido": "Rápido", "capaz": "Capaz", "nuvem": "Nuvem"}
DELEGABLE = ("rapido", "capaz")  # 'nuvem' é rede de segurança, não escolha do modelo
SUB_PROMPT = """
Você é um SUBAGENTE do Forja: outro agente te passou a tarefa abaixo. O usuário não vê suas mensagens
intermediárias, só o seu relatório final. Faça apenas a tarefa pedida, usando as ferramentas. Ao terminar,
responda com um relatório curto e objetivo: o que fez, arquivos alterados, resultados e o que ficou pendente.
"""
MAX_RESULT_IN_STEP = 2000
MAX_FILES = 12            # arquivos anexados ao brief
MAX_DIFF = 30_000         # diff mandado para a revisão
VERIFY_TIMEOUT = 180      # teto do done_when (o run_command ainda corta em SHELL_TIMEOUT_MAX)
WRITE_TOOLS = {"write_file", "edit_file"}
NUDGE_LINES = 12          # escrita maior que isto, no extremo, é trabalho de subagente
REVIEW_PROMPT = (
    "Você revisa o diff abaixo, escrito por outro modelo para a tarefa dada. Responda em no máximo 8 linhas: "
    "a primeira é 'VEREDITO: ok' ou 'VEREDITO: ajustar'; depois, um problema real por linha, com o arquivo "
    "(bug, caso não tratado, algo que contradiz a tarefa). Sem elogio, sem estilo, sem reescrever código.")


ATIVAS: dict[str, dict] = {}   # delegações rodando agora, para a aba Instâncias


def slot(level: str) -> dict | None:
    spec = config.SUBAGENTS.get(level) or {}
    return spec if spec.get("provider") and spec.get("model") else None


def configured() -> dict[str, dict]:
    return {lvl: spec for lvl in LEVELS if (spec := slot(lvl))}


def ativas() -> list[dict]:
    """Delegações em andamento, em qualquer conversa. Lê o mesmo `info` que a UI da conversa mostra,
    então não existe estado duplicado para desencontrar."""
    agora = time.time()
    return [{"id": a["id"], "conversation_id": a["conversation_id"], "run_id": a["run_id"],
             "task": a["task"], "status": a["status"], "seconds": round(agora - a["started"]),
             "level": a["info"]["level"], "model": a["info"]["model"], "provider": a["info"]["provider"],
             "iterations": a["info"]["iterations"], "tokens": a["info"]["tokens"],
             "steps": len(a["info"]["steps"])} for a in list(ATIVAS.values())]


def _fits(spec: dict) -> tuple[bool, str]:
    """(dá para usar agora?, por que não). Um slot do provedor local só vale se o modelo dele for
    justamente o que está carregado: o Forja sobe um llama-server por vez e o llama.cpp ignora o campo
    `model` do pedido, então pedir outro alias rodaria o modelo errado em silêncio."""
    if localai is None or config.PROVIDERS.get(spec["provider"], {}).get("type") != "llamacpp":
        return True, ""
    estado = localai.status()
    if not estado.get("running"):
        return False, "nenhum modelo local está carregado"
    alias = estado.get("alias") or ""
    if alias and alias != spec["model"]:
        return False, f"o modelo local carregado é '{alias}', não '{spec['model']}'"
    return True, ""


def chain(level: str) -> list[tuple[str, dict]]:
    """Ordem de tentativa: o nível pedido, a reserva na nuvem e o outro nível. Slot sem modelo, ou que
    não roda agora nesta máquina, fica de fora."""
    ordem = dict.fromkeys([level, "nuvem", "capaz" if level == "rapido" else "rapido"])
    return [(lvl, spec) for lvl in ordem if (spec := slot(lvl)) and _fits(spec)[0]]


def _why_not() -> str:
    """Motivo de cada slot configurado que não pode rodar agora. Vira o texto do erro: o principal
    precisa saber que não adianta insistir, é para fazer sozinho."""
    motivos = []
    for lvl in LEVELS:
        spec = slot(lvl)
        if spec and not (fit := _fits(spec))[0]:
            motivos.append(f"{LEVELS[lvl]}: {fit[1]}")
    return "; ".join(motivos)


def nudge_write(name: str, args: dict, avisados: set[str]) -> str:
    """Freio do esforço extremo: o principal integra, não implementa. Escrita grande de arquivo volta
    uma vez com a instrução de delegar — a regra no system prompt sozinha não segura modelo pequeno,
    que lê "delegue o difícil" e escreve assim mesmo. Na segunda tentativa passa: se o subagente não
    puder entregar, travar o turno seria pior que deixar o principal fazer."""
    if name not in WRITE_TOOLS or not chain("capaz"):
        return ""
    alvo = str(args.get("path") or "")
    corpo = str(args.get("content") or args.get("new_str") or "")
    if alvo in avisados or corpo.count("\n") < NUDGE_LINES:
        return ""
    avisados.add(alvo)
    # A primeira frase diz que NADA foi gravado: sem isso o modelo segue achando que escreveu e vai
    # rodar o teste contra um arquivo vazio, queimando duas iteracoes para descobrir.
    return (f"NADA FOI ESCRITO em {alvo or 'o arquivo'}: esta chamada foi recusada e o arquivo continua como "
            "estava. Esforço Extremo: você integra, não implementa. Delegue com delegate_task(level='capaz', "
            "files=[...], done_when='...'), dizendo por completo o que o arquivo precisa fazer e como provar que "
            f"funcionou; depois integre o que voltar. Se for mesmo trivial, repita ESTA MESMA chamada de {name} "
            "que a segunda passa. Não siga em frente sem fazer uma das duas.")


def _unused(_root: Path, _args: dict) -> str:  # a execução real é o run() abaixo, chamado pelo agente
    raise RuntimeError("delegate_task é tratado pelo loop do agente")


register(Tool(
    "delegate_task",
    "Delega uma subtarefa autocontida a um subagente e devolve o relatório dele. level='rapido' para tarefas "
    "simples (modelo mais rápido), level='capaz' para tarefas difíceis (modelo mais forte e lento). "
    "Descreva tudo o que ele precisa saber: ele não vê esta conversa.",
    {"type": "object", "properties": {
        "task": {"type": "string", "description": "Tarefa completa, com contexto, arquivos e critério de pronto"},
        "level": {"type": "string", "enum": list(DELEGABLE), "description": "rapido ou capaz"},
        "files": {"type": "array", "items": {"type": "string"},
                  "description": "Arquivos que ele precisa ler (caminhos da pasta de trabalho). O conteúdo vai "
                                 "junto com a tarefa, ele não precisa procurar."},
        "done_when": {"type": "string",
                      "description": "Comando que prova que ficou pronto (ex.: pytest -q tests/test_x.py). É "
                                     "executado depois que ele termina e o resultado entra no relatório."}},
     "required": ["task", "level"]},
    _unused, available=lambda: bool(configured())))


def _est(messages: list[dict]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 4


def _files(raw) -> list[str]:
    """Modelo pequeno às vezes manda 'a.py, b.py' em vez de lista. Aceita os dois."""
    itens = raw.split(",") if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    return [str(p).strip() for p in itens if str(p).strip()]


def _context(root: Path, files: list[str]) -> str:
    """Conteúdo dos arquivos já na primeira mensagem: cada read_file economizado é uma iteração a menos
    no modelo caro. O teto acompanha a janela para não estourar o contexto do subagente."""
    saldo = min(24_000, config.NUM_CTX * 2)
    partes = []
    for caminho in files[:MAX_FILES]:
        try:
            texto = resolve_path(root, caminho).read_text("utf-8", errors="replace")
        except (OSError, ToolError) as e:
            partes.append(f"--- {caminho}\n(não consegui ler: {e})")
            continue
        corte = texto[:saldo]
        saldo -= len(corte)
        partes.append(f"--- {caminho}\n{corte}"
                      + ("\n… (truncado; use read_file para o resto)" if len(corte) < len(texto) else ""))
        if saldo <= 0:
            break
    return "\n".join(partes)


async def _setup(spec: dict, run_obj, sub_effort: str) -> tuple:
    """(via, auto, caps, tools, schemas, mensagem de sistema) de um slot. Serve à primeira tentativa e
    ao fallback: trocar de modelo troca ferramentas, capacidades e formato de tool call junto."""
    from .agent import available_tools, system_prompt  # import tardio: agent importa este módulo

    provider, model = spec["provider"], spec["model"]
    setting = db.get_model_setting(model)
    via = "prompt" if setting["tool_mode"] == "text" else "native"
    caps = vision_caps(await llm.capabilities(provider, model), setting["vision"])
    tools = available_tools(caps, run_obj.permission, exclude={"delegate_task"})
    schemas = [t.openai_schema() for t in tools] if via == "native" else None
    system = {"role": "system", "content": system_prompt(via, caps, exclude={"delegate_task"},
                                                         permission=run_obj.permission,
                                                         effort=sub_effort) + SUB_PROMPT}
    return via, setting["tool_mode"] == "auto", caps, tools, schemas, system


async def _review(root: Path, task: str, paths: set[str]) -> tuple[str, str]:
    """(modelo, parecer) sobre o que ESTA delegação mudou, quando nenhum comando provou o resultado.
    O diff sai por arquivo tocado, não do repo inteiro: o usuário quase sempre tem trabalho não
    commitado do lado. É uma pergunta só, sem ferramentas e sem a conversa — conselho para o
    principal, nunca portão."""
    escolha = next(iter(chain("rapido")), None)
    if not escolha or not paths or not gitops.is_repo(root):
        return "", ""
    spec = escolha[1]
    diff = "\n".join(gitops.diff(root, p) for p in sorted(paths))[:MAX_DIFF]
    if not diff.strip():
        return "", ""
    messages = [{"role": "system", "content": REVIEW_PROMPT},
                {"role": "user", "content": f"Tarefa:\n{task}\n\nDiff:\n{diff}"}]
    texto = ""
    try:
        async for kind, val in llm.chat_stream(spec["provider"], spec["model"], messages, None,
                                               config.NUM_CTX, "baixo"):
            if kind == "content":
                texto += val
    except llm.LLMError as e:
        return spec["model"], f"(revisão indisponível: {e})"
    return spec["model"], split_think(texto)[1].strip()[:1500]


async def _run(conv_id: int, call: dict, req, run_obj, out: dict,
               run_call: Callable) -> AsyncIterator[dict]:
    args = call["arguments"]
    pid = call["id"]
    level = str(args.get("level") or "rapido").lower()
    task = str(args.get("task") or "").strip()
    files = _files(args.get("files"))
    done_when = str(args.get("done_when") or "").strip()
    effort = getattr(req, "effort", "medio")
    sub_effort = "maximo" if effort == "extremo" else effort  # o sub não delega: herdar 'extremo' seria letra morta
    root = workspace.root()
    meta: dict = {"arguments": args}
    if not task:
        out.update(status="erro", text="Informe 'task' com a tarefa completa.", meta=meta)
        return
    if effort == "extremo" and (len(task) < 120 or not files):
        out.update(status="erro", meta=meta, text=(
            "Delegação sem contexto suficiente. Refaça a chamada com 'task' explicando o que fazer e por quê, "
            "'files' com os arquivos que ele precisa ler e 'done_when' com o comando que prova que ficou pronto. "
            'Exemplo: {"task":"Corrija o cálculo de X em ... porque ...","level":"capaz",'
            '"files":["backend/app/x.py","backend/tests/test_x.py"],"done_when":"pytest -q backend/tests/test_x.py"}'))
        return
    cadeia = chain(level)
    if not cadeia:
        porque = _why_not()
        out.update(status="erro", meta=meta, text=(
            "Nenhum subagente disponível agora"
            + (f" ({porque})" if porque else " (Configurações › Subagentes)") + ". Faça a tarefa você mesmo."))
        return

    used_level, spec = cadeia[0]
    tentados = {used_level}
    provider, model = spec["provider"], spec["model"]
    via, auto, caps, tools, schemas, system = await _setup(spec, run_obj, sub_effort)
    brief = [task]
    if ctx := _context(root, files):
        brief.append("Arquivos relevantes (já lidos para você):\n" + ctx)
    if done_when:
        brief.append(f"Critério de pronto: ao final será executado `{done_when}`. Faça o necessário para passar.")
    messages: list[dict] = [system, {"role": "user", "content": "\n\n".join(brief)}]

    info = {"level": used_level, "provider": provider, "model": model, "steps": [], "tokens": 0,
            "iterations": 0, "chain": [lvl for lvl, _ in cadeia]}
    if used_level != level:
        info["fallback"] = f"Nível '{level}' indisponível; usei '{used_level}'."
    meta["sub"] = info
    ATIVAS[pid] = ativa = {"id": pid, "conversation_id": conv_id, "run_id": run_obj.id,
                           "task": task[:200], "started": time.time(), "status": "", "info": info}

    def estado(texto: str) -> dict:
        """Um lugar só atualiza o que a conversa mostra e o que a aba Instâncias lê."""
        ativa["status"] = texto
        return {"type": "sub_status", "parent": pid, "text": texto}

    t0 = time.monotonic()
    final = ""
    yield estado(f"{LEVELS[used_level]} · {model}: começando…")

    for i in range(config.SUBAGENT_MAX_ITERATIONS):
        if run_obj.cancel.is_set():
            final = final or "(interrompido pelo usuário)"
            break
        info["iterations"] = i + 1
        yield estado(f"{LEVELS[used_level]} · {model}: pensando (passo {i + 1})")
        content = ""
        done: dict = {"tool_calls": []}
        try:
            async for kind, val in llm.chat_stream(provider, model, messages, schemas, config.NUM_CTX,
                                                   sub_effort):
                if run_obj.cancel.is_set():
                    break
                if kind == "content":
                    content += val
                elif kind == "done":
                    done = val
        except llm.LLMError as e:
            proximo = next(((lvl, s) for lvl, s in cadeia if lvl not in tentados), None)
            # trocar de modelo depois que o sub já mexeu em arquivo repetiria efeito colateral
            if proximo and not info["steps"]:
                used_level, spec = proximo
                tentados.add(used_level)
                provider, model = spec["provider"], spec["model"]
                via, auto, caps, tools, schemas, messages[0] = await _setup(spec, run_obj, sub_effort)
                info.update(level=used_level, provider=provider, model=model)
                info["fallback"] = f"O slot anterior falhou ({e}); segui com {LEVELS[used_level]} · {model}."
                yield estado(info["fallback"])
                continue
            out.update(status="erro", text=f"Subagente falhou ({model}): {e}", meta=meta)
            return
        info["tokens"] += done.get("completion_tokens") or len(content) // 4

        _, visible = split_think(content)
        calls = done.get("tool_calls") or []
        if not calls and (via == "prompt" or auto):
            parsed, visible = parse_text_tool_calls(content, [t.name for t in tools])
            calls = [{"id": "call_" + uuid.uuid4().hex[:12], **c} for c in parsed]
        if not calls:
            final = visible
            break

        if via == "native":
            messages.append({"role": "assistant", "content": visible, "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                for c in calls]})
        else:
            messages.append({"role": "assistant", "content": visible + "".join(
                "\n<tool_call>\n" + json.dumps({"name": c["name"], "arguments": c["arguments"]},
                                               ensure_ascii=False) + "\n</tool_call>" for c in calls)})

        for c in calls:
            sub_out: dict = {}
            async for ev in run_call(conv_id, c, req, run_obj, caps, sub_out, parent=pid):
                yield ev
            step = {"id": c["id"], "name": c["name"], "arguments": c["arguments"], "status": sub_out["status"],
                    "result": sub_out["text"][:MAX_RESULT_IN_STEP], "meta": {
                        k: v for k, v in sub_out["meta"].items() if k in ("preview", "auto_rule", "approved")}}
            info["steps"].append(step)
            # resultado do passo para a UI (não é gravado como mensagem da conversa)
            yield {"type": "tool_result", "parent": pid, "message": {
                "id": None, "role": "tool", "content": sub_out["text"], "thinking": "", "tool_calls": None,
                "tool_call_id": c["id"], "name": c["name"], "status": sub_out["status"], "meta": sub_out["meta"]}}
            if via == "native":
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": sub_out["text"]})
            else:
                messages.append({"role": "user", "content":
                                 f"<tool_response>\n[{c['name']}: {sub_out['status']}]\n{sub_out['text']}\n</tool_response>"})
    else:
        final = f"(o subagente parou no limite de {config.SUBAGENT_MAX_ITERATIONS} passos sem concluir)"

    final = final or "(sem relatório)"

    # Verificação: roda DEPOIS que ele parou, pelo run_call do agente — assim vale a aprovação normal
    # (card na UI, policy, globs de auto-aprovação) e ele não escolhe se rodou nem o que reportar.
    if done_when and not run_obj.cancel.is_set() and run_obj.permission != "plan" \
            and any(t.name == "run_command" for t in tools):
        yield estado(f"verificando: {done_when}")
        ver: dict = {}
        vcall = {"id": "ver_" + uuid.uuid4().hex[:12], "name": "run_command",
                 "arguments": {"command": done_when, "timeout": VERIFY_TIMEOUT}}
        async for ev in run_call(conv_id, vcall, req, run_obj, caps, ver, parent=pid):
            yield ev
        info["steps"].append({"id": vcall["id"], "name": "run_command", "arguments": vcall["arguments"],
                              "status": ver["status"], "result": ver["text"][:MAX_RESULT_IN_STEP], "meta": {}})
        yield {"type": "tool_result", "parent": pid, "message": {
            "id": None, "role": "tool", "content": ver["text"], "thinking": "", "tool_calls": None,
            "tool_call_id": vcall["id"], "name": "run_command", "status": ver["status"], "meta": ver["meta"]}}
        info["verify"] = {"command": done_when, "status": ver["status"]}
        final += (f"\n\nVerificação `{done_when}`: {'PASSOU' if ver['status'] == 'ok' else 'FALHOU'}\n"
                  f"{ver['text'][:MAX_RESULT_IN_STEP]}")

    # Opinião só vale onde não há medição: verificação passou, revisão calada.
    provado = (info.get("verify") or {}).get("status") == "ok"
    if effort == "extremo" and not run_obj.cancel.is_set() and not provado:
        alvos = {s["arguments"].get("path") for s in info["steps"]
                 if s["name"] in WRITE_TOOLS and s["status"] == "ok" and s["arguments"].get("path")}
        yield estado("revisando o diff…" if alvos else "")
        revisor, parecer = await _review(root, task, alvos)
        if parecer:
            info["review"] = parecer
            final += f"\n\nRevisão do diff ({revisor}, não bloqueante — julgue você mesmo):\n{parecer}"

    info["seconds"] = round(time.monotonic() - t0, 1)
    yield estado("")
    out.update(status="ok", meta=meta,
               text=f"[Relatório do subagente {LEVELS[used_level]} ({model})]\n{final}")

async def run(conv_id: int, call: dict, req, run_obj, out: dict,
              run_call: Callable) -> AsyncIterator[dict]:
    """Roda a delegação e garante que ela saia da lista de ativas. O finally vale também quando o
    usuário cancela o turno: o consumidor fecha o gerador e o finally corre."""
    try:
        async for ev in _run(conv_id, call, req, run_obj, out, run_call):
            yield ev
    finally:
        ATIVAS.pop(call["id"], None)

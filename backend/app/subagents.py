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

import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable

from . import config, db, gitops, llm, modelctl, skills, workspace
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
MAX_RECARGAS = 2   # quantas vezes o Worker recarrega o próprio modelo local que caiu
MAX_NA_TRANSCRICAO = 20_000  # por mensagem da conversa gravada do Worker (read_file de arquivo grande)
MAX_FILES = 12            # arquivos anexados ao brief
MAX_DIFF = 30_000         # diff mandado para a revisão
VERIFY_TIMEOUT = 180      # teto do done_when (o run_command ainda corta em SHELL_TIMEOUT_MAX)
SUB_BUDGET_MULT = 1.5     # quem resolve a tarefa é ele: pensa mais folgado que o maestro (ver llm._budget)
WRITE_TOOLS = {"write_file", "edit_file"}
# Ferramentas de um Worker que recebe Implementation Contract (maestro.run_task). O resto sai.
#
# Não é preferência de estilo, é orçamento de contexto: os schemas de TODAS as ferramentas custam
# ~4900 tokens, contra ~1600 do system prompt. Num Ornith 9B com 8k de janela isso deixava ~1,6k
# para o contrato, os arquivos e os resultados — e a janela batia em 7914/8192 no segundo passo,
# antes de o Worker chegar a rodar o teste. Com esta lista o orçamento cabe. Quem precisar de mais
# (gerar documento, navegar) declara uma persona em .forja/agents/*.md com o `tools:` que quiser.
WORKER_TOOLS = frozenset({
    "read_file", "write_file", "edit_file", "list_dir", "glob", "grep",
    "run_command", "serve_start", "serve_status", "serve_stop",
})
NUDGE_LINES = 12          # escrita maior que isto, no extremo, é trabalho de subagente
REVIEW_PROMPT = (
    "Você revisa o diff abaixo, escrito por outro modelo para a tarefa dada. Responda em no máximo 8 linhas: "
    "a primeira é 'VEREDITO: ok' ou 'VEREDITO: ajustar'; depois, um problema real por linha, com o arquivo "
    "(bug, caso não tratado, algo que contradiz a tarefa). Sem elogio, sem estilo, sem reescrever código.")


AGENTS_DIR = ".forja/agents"   # personas do projeto, no formato das skills
MAX_AGENTS = 20

ATIVAS: dict[str, dict] = {}   # delegações rodando agora, para a aba Instâncias


def agents_for(root: Path) -> dict[str, dict]:
    """Personas de `.forja/agents/*.md`: cabeçalho diz o nível e as ferramentas, o corpo são as instruções.

    Sem persona o delegate_task só escolhe o tamanho do modelo; com ela o projeto versiona especialistas
    ("revisor que não edita nada") junto com o código, do mesmo jeito que já faz com as skills.
    """
    out: dict[str, dict] = {}
    folder = root / AGENTS_DIR
    if not folder.is_dir():
        return out
    for p in sorted(folder.glob("*.md"))[:MAX_AGENTS]:
        try:
            campos, corpo = skills.frontmatter(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not (prompt := corpo.strip()):
            continue
        level = campos.get("level", "").lower()
        ferramentas = [t for t in campos.get("tools", "").replace(",", " ").split() if t]
        out[p.stem] = {"name": p.stem, "description": campos.get("description", "") or prompt.splitlines()[0][:80],
                       "level": level if level in DELEGABLE else "rapido", "tools": ferramentas,
                       "prompt": prompt, "source": f"{AGENTS_DIR}/{p.name}"}
    return out


def slot(level: str) -> dict | None:
    """Modelo de um nível (rapido/capaz/nuvem) ou de uma especialidade (logica, frontend...)."""
    spec = config.SUBAGENTS.get(level) or especialidade(level) or {}
    return spec if spec.get("provider") and spec.get("model") else None


def especialidade(eid: str) -> dict | None:
    return next((e for e in getattr(config, "WORKER_ESPECIALIDADES", []) if e.get("id") == eid), None)


def especialidades() -> list[dict]:
    """Especialidades com modelo: só estas a Maestro enxerga."""
    return [e for e in getattr(config, "WORKER_ESPECIALIDADES", []) if slot(e["id"])]


def nome_do_nivel(level: str) -> str:
    return LEVELS.get(level) or (especialidade(level) or {}).get("nome") or level


# tipo da tarefa -> especialidade que o roteador tenta quando a Maestro não escolheu
ROTA_POR_TIPO = {"ui": "frontend", "test": "testes", "docs": "docs",
                 "feature": "logica", "bugfix": "logica", "refactor": "logica", "chore": "logica"}


FERRAMENTAS_PY = ("pytest", "mypy", "ruff", "black", "flake8")


def sem_path(cmd: str) -> str:
    """`pytest x` -> `python -m pytest x` quando o executável não está no PATH. No Windows o pip
    instala o pytest sem pôr a pasta Scripts no PATH: o Worker rodava `python -m pytest` e passava,
    a verificação `pytest` falhava com "termo não reconhecido" e a tarefa virava falha."""
    primeira = cmd.split(maxsplit=1)[0] if cmd.strip() else ""
    if primeira in FERRAMENTAS_PY and not shutil.which(primeira):
        return f"python -m {cmd.strip()}"
    return cmd


def pequena(c: dict) -> bool:
    """Tarefa que o Worker rápido dá conta: texto e manutenção. Código fica com o capaz mesmo quando
    é um arquivo só — o rápido costuma ser um modelo bem menor (1,5B), e errar custa uma tentativa."""
    return c.get("type") in ("docs", "chore")


def rota(model_slot: str | None, contrato: dict | None, evitar: dict[str, str] | None = None) -> tuple[str, str]:
    """(quem faz a tarefa, por quê). A escolha da Maestro manda. Sem ela, em ordem: o especialista
    do tipo/arquivos, o rápido se a tarefa é pequena, o capaz, a nuvem — pulando quem tem modelo
    vazio e quem está em `evitar` (nível -> motivo: já falhou nesta tarefa, ou vai mal no projeto)."""
    if model_slot:
        return model_slot, "escolha da Maestro"
    evitar = evitar or {}
    c = contrato or {}
    from .qualidade import EXTENSOES_DE_TELA  # import tardio: qualidade importa taskdb
    arquivos = [str(a).lower() for a in c.get("relevant_files") or []]
    tipo = c.get("type") or ""
    # tarefa sem tipo que só mexe em tela é de frontend (o guia visual entra em toda tarefa: não conta)
    de_tela = [a for a in arquivos if not a.endswith(".forja/knowledge/frontend.md")]
    if not tipo and de_tela and all(a.endswith(EXTENSOES_DE_TELA) for a in de_tela):
        tipo = "ui"
    candidatos = []
    if alvo := ROTA_POR_TIPO.get(tipo):
        candidatos.append((alvo, f"especialista pelo tipo '{tipo}'"))
    if pequena(c):
        candidatos.append(("rapido", "tarefa pequena"))
    candidatos += [("capaz", "generalista"), ("nuvem", "reserva")]
    pulados = []
    for nivel, motivo in candidatos:
        if not slot(nivel):
            continue
        if nivel in evitar:
            pulados.append(f"{nome_do_nivel(nivel)}: {evitar[nivel]}")
            continue
        return nivel, motivo + (f" (pulei {'; '.join(pulados)})" if pulados else "")
    return "capaz", "padrão" + (f" (pulei {'; '.join(pulados)})" if pulados else "")


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


def _fits(spec: dict, swap: bool = False) -> tuple[bool, str]:
    """(dá para usar agora?, por que não). Um slot do provedor local só vale se o modelo dele for
    justamente o que está carregado: o Forja sobe um llama-server por vez e o llama.cpp ignora o campo
    `model` do pedido, então pedir outro alias rodaria o modelo errado em silêncio.

    `swap=True` (Maestro em modo sequencial) muda a pergunta: não é "já está carregado?" e sim "dá
    para carregar?". O orquestrador chama `modelctl.ensure` antes de despachar, então basta o arquivo
    existir em alguma pasta configurada. É o que permite o ciclo carrega A → tarefa → carrega B.
    """
    if localai is None or config.PROVIDERS.get(spec["provider"], {}).get("type") != "llamacpp":
        return True, ""
    estado = localai.status()
    alias = estado.get("alias") or ""
    if alias and alias == spec["model"]:
        return True, ""
    if swap:
        from . import modelctl  # import tardio: modelctl importa localai, que importa config
        if modelctl.path_for(spec["model"]):
            return True, ""
        return False, f"o modelo local '{spec['model']}' não está em nenhuma pasta configurada"
    if not estado.get("running"):
        return False, "nenhum modelo local está carregado"
    if alias:
        return False, f"o modelo local carregado é '{alias}', não '{spec['model']}'"
    return True, ""


def chain(level: str, swap: bool = False) -> list[tuple[str, dict]]:
    """Ordem de tentativa: o nível pedido, a reserva na nuvem e o outro nível. Slot sem modelo, ou que
    não roda agora nesta máquina, fica de fora. Com `swap`, um slot local que só precisa ser carregado
    continua valendo (ver _fits)."""
    # especialidade: ela, depois o capaz (generalista), a nuvem e o rápido
    ordem = dict.fromkeys([level, "nuvem", "capaz" if level == "rapido" else "rapido"] if level in LEVELS
                          else [level, "capaz", "nuvem", "rapido"])
    return [(lvl, spec) for lvl in ordem if (spec := slot(lvl)) and _fits(spec, swap)[0]]


def _why_not(swap: bool = False) -> str:
    """Motivo de cada slot configurado que não pode rodar agora. Vira o texto do erro: o principal
    precisa saber que não adianta insistir, é para fazer sozinho."""
    motivos = []
    for lvl in LEVELS:
        spec = slot(lvl)
        if spec and not (fit := _fits(spec, swap))[0]:
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
    "simples (modelo mais rápido), level='capaz' para tarefas difíceis (modelo mais forte e lento); com "
    "'agent' o subagente vem pronto do projeto (nível e ferramentas saem do arquivo dele). "
    "Descreva tudo o que ele precisa saber: ele não vê esta conversa.",
    {"type": "object", "properties": {
        "task": {"type": "string", "description": "Tarefa completa, com contexto, arquivos e critério de pronto"},
        "level": {"type": "string", "enum": list(DELEGABLE), "description": "rapido ou capaz"},
        "agent": {"type": "string",
                  "description": "Nome de um subagente do projeto (.forja/agents/*.md), quando houver. "
                                 "Substitui o level."},
        "files": {"type": "array", "items": {"type": "string"},
                  "description": "Arquivos que ele precisa ler (caminhos da pasta de trabalho). O conteúdo vai "
                                 "junto com a tarefa, ele não precisa procurar."},
        "done_when": {"type": "string",
                      "description": "Comando que prova que ficou pronto (ex.: pytest -q tests/test_x.py). É "
                                     "executado depois que ele termina e o resultado entra no relatório."},
        "run_in_background": {"type": "boolean",
                              "description": "Padrão true: devolve na hora e o relatório chega como aviso quando "
                                             "ele terminar. false só quando o seu próximo passo depende do "
                                             "resultado."}},
     "required": ["task"]},
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


def _tem_arquivos(root: Path) -> bool:
    """Pasta de trabalho com algo visível para listar em 'files'."""
    try:
        return any(not p.name.startswith(".") for p in root.iterdir())
    except OSError:
        return False


def _falta(root: Path, task: str, files: list[str]) -> str:
    """O que impede esta delegação de ser útil no esforço extremo; '' quando está boa. Projeto do zero
    não tem arquivo para listar: exigir 'files' ali é pedido impossível e o modelo repete a mesma
    chamada até o laço ser interrompido."""
    if len(task) < 120:
        return ("a 'task' está curta demais — o subagente não vê esta conversa, então repasse o enunciado "
                "inteiro (requisitos, nomes de arquivo, o que conta como pronto)")
    if not files and _tem_arquivos(root):
        return ("faltou 'files' — liste os arquivos que ele precisa ler, que o conteúdo vai junto no pedido; "
                "se nenhum serve (arquivo novo), mande a mesma chamada de novo sem 'files'")
    return ""


async def _setup(spec: dict, run_obj, sub_effort: str, persona: dict | None = None,
                 focado: bool = False) -> tuple:
    """(via, auto, caps, tools, schemas, mensagem de sistema) de um slot. Serve à primeira tentativa e
    ao fallback: trocar de modelo troca ferramentas, capacidades e formato de tool call junto."""
    from .agent import available_tools, system_prompt  # import tardio: agent importa este módulo

    provider, model = spec["provider"], spec["model"]
    setting = db.get_model_setting(model)
    via = "prompt" if setting["tool_mode"] == "text" else "native"
    caps = vision_caps(await llm.capabilities(provider, model), setting["vision"])
    # Coisa da conversa principal: plano, goal e orquestração não são papel de quem recebe a tarefa.
    excluir = {"delegate_task", "exit_plan_mode", "create_goal", "get_goal", "update_goal"}
    permitidas = set(persona["tools"]) if persona and persona["tools"] else (
        set(WORKER_TOOLS) if focado else None)
    if permitidas is not None:  # tudo o que não está na lista nem aparece para ele
        excluir |= {t.name for t in available_tools(caps, run_obj.permission) if t.name not in permitidas}
    # Persona que lista ferramentas continua ganhando o ask_user de brinde (comportamento de sempre);
    # o Worker de contrato não, porque _run_call recusa ask_user de subagente e o schema só ocuparia
    # janela para devolver erro.
    if not focado:
        excluir.discard("ask_user")
    tools = available_tools(caps, run_obj.permission, exclude=excluir)
    conteudo = system_prompt(via, caps, exclude=excluir, permission=run_obj.permission,
                             effort=sub_effort) + SUB_PROMPT
    if persona:
        conteudo += f"\nVocê é o subagente '{persona['name']}' deste projeto. Instruções dele:\n{persona['prompt']}"
    return via, setting["tool_mode"] == "auto", caps, tools, schemas_de(tools, via), {"role": "system",
                                                                                      "content": conteudo}


def schemas_de(tools: list, via: str) -> list | None:
    return [t.openai_schema() for t in tools] if via == "native" else None


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
                                               config.NUM_CTX, "baixo", budget_mult=SUB_BUDGET_MULT):
            if kind == "content":
                texto += val
    except llm.LLMError as e:
        return spec["model"], f"(revisão indisponível: {e})"
    return spec["model"], split_think(texto)[1].strip()[:1500]


async def _run(conv_id: int, call: dict, req, run_obj, out: dict,
               run_call: Callable, structured: bool = False,
               ao_registrar: Callable[[list], None] | None = None) -> AsyncIterator[dict]:
    """`structured=True`: o brief veio de um Implementation Contract do Maestro (taskdb), nao de um
    delegate_task escrito a maquina pelo modelo. Os freios de delegacao rasa (_falta) nao se aplicam:
    o contrato ja foi validado na criacao da tarefa, e recusar aqui travaria o ciclo autonomo."""
    args = call["arguments"]
    pid = call["id"]
    root_persona = workspace.root()
    persona = agents_for(root_persona).get(str(args.get("agent") or "").strip()) if args.get("agent") else None
    level = persona["level"] if persona else str(args.get("level") or "rapido").lower()
    task = str(args.get("task") or "").strip()
    files = _files(args.get("files"))
    done_when = sem_path(str(args.get("done_when") or "").strip())
    effort = getattr(req, "effort", "medio")
    sub_effort = "maximo" if effort == "extremo" else effort  # o sub não delega: herdar 'extremo' seria letra morta
    root = workspace.root()
    meta: dict = {"arguments": args}
    if not task:
        out.update(status="erro", text="Informe 'task' com a tarefa completa.", meta=meta)
        return
    if effort == "extremo" and not structured and (motivo := _falta(root, task, files))             and "delegate_task" not in run_obj.nudged:
        run_obj.nudged.add("delegate_task")  # uma vez por turno: insistir é sinal de que não há mais contexto
        out.update(status="erro", meta=meta, text=(
            f"Delegação recusada (nada foi feito): {motivo}. Modelo de chamada boa: "
            '{"task":"Corrija o cálculo de X em ... porque ...","level":"capaz",'
            '"files":["backend/app/x.py","backend/tests/test_x.py"],"done_when":"pytest -q backend/tests/test_x.py"}'
            ". Refaça a chamada agora — mesmo que só dê para melhorar a 'task', a próxima passa."))
        return
    # `_spec`: a Maestro mandou o Worker usar um modelo específico (o dela), sem cadeia de reserva
    cadeia = [(level, dict(args["_spec"]))] if isinstance(args.get("_spec"), dict) else chain(level)
    if not cadeia:
        porque = _why_not()
        out.update(status="erro", meta=meta, text=(
            "Nenhum subagente disponível agora"
            + (f" ({porque})" if porque else " (Configurações › Subagentes)") + ". Faça a tarefa você mesmo."))
        return

    used_level, spec = cadeia[0]
    tentados = {used_level}
    provider, model = spec["provider"], spec["model"]
    via, auto, caps, tools, schemas, system = await _setup(spec, run_obj, sub_effort, persona, structured)
    brief = [task]
    if ctx := _context(root, files):
        brief.append("Arquivos relevantes (já lidos para você):\n" + ctx)
    if done_when:
        brief.append(f"Critério de pronto: ao final será executado `{done_when}`. Faça o necessário para passar.")
    if structured:  # execução autônoma: cada `python -c` para esperando o usuário clicar
        brief.append("Para conferir, rode os testes (pytest, npm test...) em vez de `python -c`/`node -e`: "
                     "no modo Automático testes passam direto, código solto na linha de comando pede aprovação.")
    messages: list[dict] = [system, {"role": "user", "content": "\n\n".join(brief)}]

    info = {"level": used_level, "provider": provider, "model": model, "steps": [], "tokens": 0,
            "iterations": 0, "chain": [lvl for lvl, _ in cadeia]}
    if persona:
        info["agent"] = persona["name"]
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
    yield estado(f"{nome_do_nivel(used_level)} · {model}: começando…")

    # Worker de contrato vira uma conversa como a do chat: mensagens no mesmo formato, transmitidas
    # ao vivo (a coluna Worker do cockpit desenha com os componentes do chat) e gravadas a cada
    # rodada na tentativa. O delegate_task comum continua como sempre — só passos e relatório.
    from .agent import _stats  # import tardio: agent importa este módulo
    transcricao: list[dict] = []
    ctx_max = await llm.context_limit(provider, model, config.NUM_CTX) if structured else None

    def registra(msg: dict) -> dict:
        msg = {"id": len(transcricao) + 1, "thinking": "", "tool_calls": None, "tool_call_id": None,
               "name": None, "status": None, "meta": None, **msg}
        for campo in ("content", "thinking"):
            if isinstance(msg.get(campo), str) and len(msg[campo]) > MAX_NA_TRANSCRICAO:
                msg[campo] = msg[campo][:MAX_NA_TRANSCRICAO] + "\n… (cortado na gravação)"
        transcricao.append(msg)
        if ao_registrar:
            ao_registrar(transcricao)
        return msg

    if structured:
        yield {"type": "sub_message", "parent": pid,
               "message": registra({"role": "user", "content": messages[1]["content"]})}

    recargas = 0
    for i in range(config.SUBAGENT_MAX_ITERATIONS):
        if getattr(run_obj, "paused", False):  # Pausar vale também no meio de uma tarefa do Worker
            yield estado(f"{nome_do_nivel(used_level)} · {model}: pausado")
            await run_obj.espera_retomar()
        if run_obj.cancel.is_set():
            final = final or "(interrompido pelo usuário)"
            break
        info["iterations"] = i + 1
        yield estado(f"{nome_do_nivel(used_level)} · {model}: pensando (passo {i + 1})")
        content = reasoning = ""
        done: dict = {"tool_calls": []}
        t_passo, t_primeiro = time.monotonic(), None
        if structured:
            yield {"type": "sub_assistant_start", "parent": pid}
        try:
            async for kind, val in llm.chat_stream(provider, model, messages, schemas, config.NUM_CTX,
                                                   sub_effort, budget_mult=SUB_BUDGET_MULT):
                if run_obj.cancel.is_set():
                    break
                if kind != "done" and t_primeiro is None:
                    t_primeiro = time.monotonic()
                if kind == "content":
                    content += val
                    if structured:
                        yield {"type": "sub_token", "parent": pid, "text": val}
                elif kind == "reasoning":
                    reasoning += val
                    if structured:
                        yield {"type": "sub_thinking", "parent": pid, "text": val}
                elif kind == "tool_args" and structured:
                    yield {"type": "sub_tool_token", "parent": pid, "name": val["name"], "text": val["text"]}
                elif kind == "done":
                    done = val
        except llm.LLMError as e:
            # O llama-server do Worker morreu no meio (falta de memória, crash): recarrega o mesmo
            # modelo e repete o passo. As mensagens estão aqui, e os arquivos já escritos continuam
            # no disco — nada da tentativa se perde. Duas vezes no máximo: morrer de novo é sinal de
            # que o modelo não cabe, e aí o fallback de slot (abaixo) decide.
            if (recargas < MAX_RECARGAS and not run_obj.cancel.is_set() and e.status is None
                    and await modelctl.caiu(spec)):
                recargas += 1
                yield estado(f"{nome_do_nivel(used_level)} · {model}: o modelo caiu ({e}); recarregando e "
                             f"repetindo o passo {i + 1}")
                try:
                    async for ev in modelctl.recupera(spec, run_obj.cancel):
                        yield ev
                except ToolError as e2:
                    out.update(status="erro", text=f"O modelo do Worker caiu e não voltou: {e2}", meta=meta)
                    return
                info["recovered"] = recargas
                continue
            proximo = next(((lvl, s) for lvl, s in cadeia if lvl not in tentados), None)
            # trocar de modelo depois que o sub já mexeu em arquivo repetiria efeito colateral
            if proximo and not info["steps"]:
                used_level, spec = proximo
                tentados.add(used_level)
                provider, model = spec["provider"], spec["model"]
                via, auto, caps, tools, schemas, messages[0] = await _setup(spec, run_obj, sub_effort,
                                                                              persona, structured)
                info.update(level=used_level, provider=provider, model=model)
                if structured:
                    ctx_max = await llm.context_limit(provider, model, config.NUM_CTX)
                info["fallback"] = f"O slot anterior falhou ({e}); segui com {nome_do_nivel(used_level)} · {model}."
                yield estado(info["fallback"])
                continue
            out.update(status="erro", text=f"Subagente falhou ({model}): {e}", meta=meta)
            return
        info["tokens"] += done.get("completion_tokens") or len(content) // 4

        pensou, visible = split_think(content)
        calls = done.get("tool_calls") or []
        if not calls and (via == "prompt" or auto):
            parsed, visible = parse_text_tool_calls(content, [t.name for t in tools])
            calls = [{"id": "call_" + uuid.uuid4().hex[:12], **c} for c in parsed]
        if structured:
            stats = _stats(messages, schemas, content, reasoning, done, t_passo, t_primeiro, ctx_max, model)
            yield {"type": "sub_message", "parent": pid, "message": registra({
                "role": "assistant", "content": visible, "thinking": (reasoning + "\n" + pensou).strip(),
                "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]}
                               for c in calls] or None,
                "meta": {"stats": stats}})}
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
            # resultado do passo para a UI (não entra na conversa da Maestro; entra na do Worker)
            resultado = {"role": "tool", "content": sub_out["text"], "tool_call_id": c["id"],
                         "name": c["name"], "status": sub_out["status"], "meta": sub_out["meta"]}
            if structured:
                resultado = registra(resultado)
            yield {"type": "tool_result", "parent": pid,
                   "message": {"id": None, "thinking": "", "tool_calls": None, **resultado}}
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
        if structured:  # na conversa do Worker a verificação aparece como uma chamada, com a saída
            yield {"type": "sub_message", "parent": pid, "message": registra({
                "role": "assistant", "content": "", "tool_calls": [
                    {"id": vcall["id"], "name": "run_command", "arguments": vcall["arguments"]}],
                "meta": {"verificacao": True}})}
        async for ev in run_call(conv_id, vcall, req, run_obj, caps, ver, parent=pid):
            yield ev
        info["steps"].append({"id": vcall["id"], "name": "run_command", "arguments": vcall["arguments"],
                              "status": ver["status"], "result": ver["text"][:MAX_RESULT_IN_STEP], "meta": {}})
        resultado = {"role": "tool", "content": ver["text"], "tool_call_id": vcall["id"],
                     "name": "run_command", "status": ver["status"], "meta": ver["meta"]}
        if structured:
            resultado = registra(resultado)
        yield {"type": "tool_result", "parent": pid,
               "message": {"id": None, "thinking": "", "tool_calls": None, **resultado}}
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
               text=f"[Relatório do subagente {nome_do_nivel(used_level)} ({model})]\n{final}")

async def run(conv_id: int, call: dict, req, run_obj, out: dict,
              run_call: Callable, structured: bool = False,
              ao_registrar: Callable[[list], None] | None = None) -> AsyncIterator[dict]:
    """Roda a delegação e garante que ela saia da lista de ativas. O finally vale também quando o
    usuário cancela o turno: o consumidor fecha o gerador e o finally corre."""
    from . import hooks  # tardio: hooks importa shell, que importa tools; evita ciclo no import do pacote

    tarefa = {"task": str(call["arguments"].get("task") or "")[:500]}
    await hooks.rodar_async("subagent_start", workspace.root(), "", tarefa)
    try:
        async for ev in _run(conv_id, call, req, run_obj, out, run_call, structured, ao_registrar):
            yield ev
    finally:
        ATIVAS.pop(call["id"], None)
    if saida := hooks.texto(await hooks.rodar_async("subagent_stop", workspace.root(), "", tarefa)):
        out["text"] = f"{out.get('text') or ''}\n\n{saida}"

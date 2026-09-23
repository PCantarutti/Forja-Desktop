"""Project State da Maestro: a pasta `.forja/` do projeto, a memória que passa de uma conversa para a outra.

    FORJA.md              o que é, stack, como rodar e testar, convenções     Maestro (memória do projeto)
    .forja/
    ├── architecture.md   módulos, fluxo, onde fica cada coisa                 Maestro
    ├── requirements.md   o que o usuário pediu e o que já está atendido       Maestro
    ├── decisions.md      decisões e o porquê                                  Maestro + session_note
    ├── progress.md       funcionalidades e tarefas                            gerado do banco
    ├── tasks.json        as mesmas tarefas, para máquina                      gerado do banco
    ├── knowledge/        frontend.md, backend.md, database.md                 Maestro
    └── sessions/         SESSION-NNN.md, uma por session_note                 session_note

As tarefas continuam morando no SQLite (taskdb): é lá que a máquina de estados e as tentativas
funcionam. progress.md e tasks.json são o espelho delas no projeto, cobrindo TODAS as conversas
Maestro da pasta — uma conversa nova não vê as tarefas da anterior pelo list_tasks, mas lê o
progresso aqui. Fica ao lado do código, versionado junto. Só existe no modo Maestro: é criado no
início de uma execução dela (`congelar`), nunca pelo modo agente.

FORJA.md, na raiz, é o arquivo de memória do projeto que o Forja já tinha (config.PROJECT_MEMORY_FILE)
e já entra no prompt de todo modo: aqui ele passa a ser obrigatório antes do primeiro plano. Um project.md dentro de .forja/ seria a mesma coisa duas vezes no contexto.

Custo de contexto, o requisito que manda no formato: o prompt leva FORJA.md, o fim de
decisions.md, o que está em andamento no progress.md e a última sessão, cada um com teto. O resto
entra como índice (nome e tamanho) para a Maestro ler com read_file quando precisar. E o bloco é
tirado UMA vez por execução: progress.md muda a cada tarefa, e um system prompt que muda no meio da
execução invalida o cache de prompt do llama.cpp — a conversa inteira seria reprocessada a cada
rodada. O estado vivo, durante a execução, é o list_tasks.
"""
from __future__ import annotations

import contextvars
import json
import re
from datetime import datetime
from pathlib import Path

from . import config, db, memory, taskdb, workspace
from .tools import Tool, ToolError, register_extra

PASTA = ".forja"
SESSOES = f"{PASTA}/sessions"
PADRAO = re.compile(r"^SESSION-(\d{3,})\.md$")
MAX_ITENS = 15
MAX_TEXTO = 1500
# Tetos do que entra em TODA requisição da Maestro (caracteres; ~4 por token).
TETO = {"forja.md": 2500, "decisions.md": 1200, "progress.md": 1500, "sessao": 1800}

# Os arquivos nascem quando têm conteúdo, nunca como esqueleto vazio: um modelo pequeno lia cada
# esqueleto (seis read_file por nada) mesmo com o aviso para não ler. O que vai em cada um fica
# no pedido de bootstrap, que só entra no prompt enquanto o projeto não tem estado.
ARQUIVOS = ("FORJA.md NA RAIZ do projeto, não dentro de .forja/: o que é, stack, como rodar e testar, convenções; "
            f"{PASTA}/architecture.md: módulos, fluxo de dados, onde fica cada coisa; "
            f"{PASTA}/requirements.md: o que o usuário pediu e o que já está atendido; "
            f"{PASTA}/knowledge/frontend.md, backend.md, database.md: um por camada que o projeto tiver")
AVISO_GERADO = "_Gerado pelo Forja a partir das tarefas. Não edite: é reescrito a cada mudança._\n"
ROTULO = {"planning": "planejando", "active": "em andamento", "validating": "VALIDANDO A ENTREGA",
          "done": "concluída", "cancelled": "cancelada"}
MARCA = {"completed": "x", "cancelled": "/", "failed": "!", "needs_human": "?", "blocked": "-"}

# Bloco do prompt congelado no início da execução (ver docstring). None = fora de execução: calcula.
BLOCO: contextvars.ContextVar[str | None] = contextvars.ContextVar("forja_projstate", default=None)


def _ler(root: Path, nome: str) -> str:
    try:
        return (root / PASTA / nome).read_text("utf-8")
    except OSError:
        return ""


def vazio(texto: str) -> bool:
    """Só título e comentário do esqueleto: nada que valha tokens."""
    sem = re.sub(r"<!--.*?-->", "", texto, flags=re.S)
    return not any(l.strip() and not l.lstrip().startswith("#") for l in sem.splitlines())


def _grava_se_mudou(p: Path, texto: str) -> None:
    """Sem reescrever o que não mudou: o arquivo é versionado, e mtime/diff à toa é ruído."""
    try:
        if p.read_text("utf-8") == texto:
            return
    except OSError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto, "utf-8")


def _forja_md(root: Path) -> Path:
    return root / config.PROJECT_MEMORY_FILE


def forja_md(root: Path) -> str:
    try:
        return _forja_md(root).read_text("utf-8")
    except OSError:
        return ""


def ensure(root: Path) -> None:
    """A pasta .forja/ (o que ela tem, a Maestro e o sync escrevem)."""
    (root / PASTA).mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ progress.md / tasks.json

def _conversas_da_pasta(s, conv_id: int) -> tuple[Path, list[int]] | None:
    conv = s.get(db.Conversation, conv_id)
    if not conv or conv.kind != "maestro":
        return None
    try:
        root = workspace.resolve(conv.workspace)
    except workspace.WorkspaceError:
        return None
    alvo = workspace.normalize(str(root))
    ids = []
    for cid, ws in s.query(db.Conversation.id, db.Conversation.workspace).filter(
            db.Conversation.kind == "maestro"):
        try:
            if workspace.normalize(str(workspace.resolve(ws))) == alvo:
                ids.append(cid)
        except workspace.WorkspaceError:
            continue  # pasta que sumiu: as conversas dela não entram
    return root, ids


def sync(conv_id: int) -> None:
    """Reescreve progress.md e tasks.json da pasta desta conversa a partir do banco."""
    with db.session() as s:
        achado = _conversas_da_pasta(s, conv_id)
        if not achado:
            return
        root, ids = achado
        if not (root / PASTA).is_dir():
            return  # a pasta nasce no congelar(), no início de uma execução Maestro
        feats = s.query(db.Feature).filter(db.Feature.conversation_id.in_(ids),
                                           db.Feature.copiada_para.is_(None)).order_by(db.Feature.id).all()
        if not feats and not (root / PASTA / "progress.md").exists():
            return  # sem trabalho, sem arquivo: um progresso vazio só convida o modelo a lê-lo
        tarefas: dict[int, list[db.Task]] = {}
        for t in s.query(db.Task).filter(db.Task.conversation_id.in_(ids)).order_by(db.Task.id):
            tarefas.setdefault(t.feature_id, []).append(t)
        abertas = [f for f in feats if f.status not in ("done", "cancelled")]
        fechadas = [f for f in reversed(feats) if f.status in ("done", "cancelled")]

        linhas = ["# Progresso", AVISO_GERADO, "## Em andamento"]
        for f in abertas:
            linhas.append(f"### {f.title} — {ROTULO.get(f.status, f.status)} (feature_id={f.id})")
            for t in tarefas.get(f.id, []):
                extra = [t.status] if t.status not in ("completed", "pending") else []
                if t.attempt_count:
                    extra.append(f"tentativa {t.attempt_count}/{t.max_attempts}")
                if t.blocked_reason:
                    extra.append(t.blocked_reason[:120])
                linhas.append(f"- [{MARCA.get(t.status, ' ')}] {t.code} {t.title}"
                              + (f" — {'; '.join(extra)}" if extra else ""))
        if not abertas:
            linhas.append("(nada)")
        linhas += ["", "## Concluídas"]
        linhas += [f"- {f.title} — {ROTULO.get(f.status, f.status)}, {len(tarefas.get(f.id, []))} tarefas"
                   f" (feature_id={f.id})" for f in fechadas] or ["(nada)"]

        dados = {"features": [{
            "id": f.id, "title": f.title, "goal": f.goal, "status": f.status,
            "conversation_id": f.conversation_id,
            "tasks": [{"code": t.code, "title": t.title, "status": t.status, "depends_on": t.depends_on or [],
                       "attempts": t.attempt_count, "model_slot": t.model_slot,
                       "verify_command": (t.contract or {}).get("verify_command"),
                       "blocked_reason": t.blocked_reason} for t in tarefas.get(f.id, [])]}
            for f in feats]}
    _grava_se_mudou(root / PASTA / "progress.md", "\n".join(linhas) + "\n")
    _grava_se_mudou(root / PASTA / "tasks.json", json.dumps(dados, ensure_ascii=False, indent=1) + "\n")


# ------------------------------------------------------------------ sessões

def _numeros(root: Path) -> list[int]:
    pasta = root / SESSOES
    if not pasta.is_dir():
        return []
    return sorted(int(m.group(1)) for p in pasta.iterdir() if (m := PADRAO.match(p.name)))


def latest(root: Path | None = None) -> tuple[str, str] | None:
    """(nome, conteúdo) da nota de sessão mais recente, ou None."""
    root = root or workspace.root()
    nums = _numeros(root)
    if not nums:
        return None
    nome = f"SESSION-{nums[-1]:03d}.md"
    try:
        return nome, (root / SESSOES / nome).read_text("utf-8")
    except OSError:
        return None


def _lista(raw) -> list[str]:
    itens = raw if isinstance(raw, list) else ([raw] if raw else [])
    return [str(x).strip()[:MAX_TEXTO] for x in itens[:MAX_ITENS] if str(x).strip()]


def _abertas(conv_id: int | None) -> list[str]:
    if conv_id is None:
        return []
    linhas = []
    for f in taskdb.board(conv_id)["features"]:
        for t in f["tasks"]:
            if t["status"] in taskdb.OPEN:
                motivo = f" — {t['blocked_reason']}" if t.get("blocked_reason") else ""
                linhas.append(f"{t['code']} [{t['status']}] {t['title']}{motivo}")
    return linhas


def write(root: Path, conv_id: int | None, objetivo: str, resultado: str,
          decisoes: list[str], problemas: list[str], proximo: str) -> str:
    """Grava a próxima nota e devolve o nome. Nunca sobrescreve: cada sessão ganha um arquivo. As
    decisões vão também para decisions.md, que é onde a próxima conversa as procura."""
    objetivo = str(objetivo or "").strip()[:MAX_TEXTO]
    if not objetivo:
        raise ToolError("Informe 'objective': o que esta sessão estava tentando alcançar.")
    pasta = root / SESSOES
    pasta.mkdir(parents=True, exist_ok=True)
    n = (_numeros(root) or [0])[-1] + 1
    nome = f"SESSION-{n:03d}.md"
    decisoes = _lista(decisoes)

    def secao(titulo: str, corpo) -> str:
        if isinstance(corpo, list):
            corpo = "\n".join(f"- {x}" for x in corpo) if corpo else "- (nenhum)"
        return f"## {titulo}\n{corpo or '(não informado)'}\n"

    texto = "\n".join([
        f"# {nome.removesuffix('.md')}",
        f"_{datetime.now():%d/%m/%Y %H:%M}_\n",
        secao("Objetivo", objetivo),
        secao("Resultado", str(resultado or "").strip()[:MAX_TEXTO]),
        secao("Decisões", decisoes),
        secao("Problemas", _lista(problemas)),
        secao("Próximo passo", str(proximo or "").strip()[:MAX_TEXTO]),
        secao("Tarefas em aberto", _abertas(conv_id)),
    ])
    # `x` falha se o arquivo existir: duas Maestros gravando na mesma pasta não se sobrescrevem.
    with open(pasta / nome, "x", encoding="utf-8") as fh:
        fh.write(texto)
    if decisoes:
        dec = root / PASTA / "decisions.md"
        atual = _ler(root, "decisions.md") or "# Decisões\n"
        dia = f"{datetime.now():%d/%m/%Y}"
        _grava_se_mudou(dec, atual.rstrip("\n") + "\n" + "".join(
            f"- {dia}: {d} ({nome.removesuffix('.md')})\n" for d in decisoes))
    return nome


# ------------------------------------------------------------------ prompt

def _cap(texto: str, n: int, caminho: str, fim: bool = False) -> str:
    texto = texto.strip()
    if len(texto) <= n:
        return texto
    corte = texto[-n:] if fim else texto[:n]
    return (f"… (início cortado; leia {caminho} inteiro)\n{corte}" if fim
            else f"{corte}\n… (cortado; leia {caminho} inteiro)")


def _enxuto(texto: str) -> str:
    """Só o que vale token: sem comentário de esqueleto e sem linha em branco repetida."""
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"<!--.*?-->\n?", "", texto, flags=re.S)).strip()


def _sessao_enxuta(texto: str) -> str:
    """A nota sem o que já está em outro lugar do bloco (Decisões → decisions.md; título e data → o
    cabeçalho) e sem seção vazia."""
    corpo = texto.split("\n## ", 1)[1] if "\n## " in texto else texto
    secoes = [s for s in ("## " + corpo).split("\n## ")]
    fica = []
    for s in secoes:
        titulo, _, resto = s.removeprefix("## ").partition("\n")
        if titulo == "Decisões" or resto.strip() in ("", "- (nenhum)", "(não informado)"):
            continue
        fica.append(f"## {titulo}\n{resto.strip()}")
    return "\n".join(fica)


def _em_andamento(progresso: str) -> str:
    """Só a seção 'Em andamento': o histórico de concluídas fica no arquivo, para quem quiser ler."""
    m = re.search(r"## Em andamento\n(.*?)(?:\n## |\Z)", progresso, re.S)
    corpo = (m.group(1) if m else "").strip()
    return "" if corpo in ("", "(nada)") else corpo


def prompt_block(root: Path | None = None) -> str:
    """O Project State que entra no system prompt da Maestro. Vazio se a pasta não existe."""
    root = root or workspace.root()
    if not (root / PASTA).is_dir():
        return ""
    partes: list[str] = []
    arquivo = config.PROJECT_MEMORY_FILE
    projeto = forja_md(root)
    if vazio(projeto):
        partes.append(f"PROJETO AINDA SEM ESTADO ({arquivo} e {PASTA}/ vazios; não há o que ler lá). Investigue "
                      f"o código e, ANTES do plan_feature (que recusa sem o {arquivo}), escreva com write_file: "
                      + ARQUIVOS.replace("FORJA.md", arquivo)
                      + ". Fatos curtos, sem prosa: é lido no início de toda conversa.")
    elif not memory.project_text():
        # memória do projeto desligada nas Configurações: o _extra não o põe no prompt, então vai aqui
        partes.append(f"--- {arquivo} ---\n{_cap(_enxuto(projeto), TETO['forja.md'], arquivo)}")
    if not vazio(dec := _ler(root, "decisions.md")):
        partes.append(f"--- decisions.md ---\n{_cap(_enxuto(dec).removeprefix('# Decisões').strip(), TETO['decisions.md'], PASTA + '/decisions.md', fim=True)}")
    if andamento := _em_andamento(_ler(root, "progress.md")):
        partes.append(f"--- progress.md (em andamento) ---\n{_cap(andamento, TETO['progress.md'], PASTA + '/progress.md')}")
    if achada := latest(root):
        nome, texto = achada
        partes.append(f"--- sessions/{nome} (onde a última sessão parou) ---\n"
                      f"{_cap(_sessao_enxuta(texto), TETO['sessao'], SESSOES + '/' + nome)}")
    indice = []
    for nome in ("architecture.md", "requirements.md", *sorted(
            f"knowledge/{p.name}" for p in (root / PASTA / "knowledge").glob("*.md"))):
        if not vazio(texto := _ler(root, nome)):
            indice.append(f"{nome} ({len(texto)} c)")
    if (root / PASTA / "progress.md").is_file():
        indice.append("progress.md (histórico)")
    return (f"\n\nProject State ({PASTA}/, a memória do projeto entre conversas; não reabra decisão "
            "tomada sem motivo novo). Sob demanda com read_file: "
            + (", ".join(indice) or "nada ainda") + ".\n" + "\n".join(partes))


def congelar(root: Path, conv_id: int, ocupada=lambda _c: False) -> list[str]:
    """Início de uma execução Maestro: garante a pasta, assume o trabalho aberto das outras
    conversas Maestro da pasta (as que não estão rodando: `ocupada`), atualiza o progresso e fixa o
    bloco do prompt para a execução inteira (cache de prompt do llama.cpp; ver docstring do módulo).
    Devolve os títulos das funcionalidades assumidas."""
    ensure(root)
    with db.session() as s:
        achado = _conversas_da_pasta(s, conv_id)
    outras = [c for c in (achado[1] if achado else []) if c != conv_id and not ocupada(c)]
    assumidas = taskdb.assume(outras, conv_id)
    sync(conv_id)
    BLOCO.set(prompt_block(root))
    return assumidas


def nova_sessao(conv_id: int) -> tuple[int, list[str]]:
    """Botão "Nova sessão": conversa Maestro nova na mesma pasta, com CÓPIA do trabalho aberto desta
    (a lista fica aqui para consulta). A conversa nova começa só com o Project State. Devolve o id e
    os títulos copiados."""
    with db.session() as s:
        velha = s.get(db.Conversation, conv_id)
        nova = db.Conversation(title=f"{velha.title or 'Maestro'} · nova sessão"[:200], kind="maestro",
                               workspace=velha.workspace)
        s.add(nova)
        s.commit()
        novo_id = nova.id
    return novo_id, taskdb.assume([conv_id], novo_id)


def fora_do_papel(root: Path, call: dict) -> str | None:
    """Escrita da própria Maestro fora do Project State: o motivo da recusa, ou None se pode.

    Instrução no prompt não segurou: um modelo de 9B, com o plan_feature recusado, foi implementar
    sozinho, brigou com CRLF e queimou a janela. O Worker não passa por aqui — só as chamadas da
    Maestro, no loop dela."""
    if call.get("name") not in ("write_file", "edit_file"):
        return None
    from .tools import _rel, resolve_path
    try:
        rel = _rel(root, resolve_path(root, (call.get("arguments") or {}).get("path")))
    except ToolError:
        return None  # fora da pasta: a própria ferramenta recusa, com a mensagem dela
    arquivo = config.PROJECT_MEMORY_FILE
    if rel.lower() in (f"{PASTA}/{arquivo}".lower(), f"{PASTA}/project.md"):
        return f"O arquivo do projeto é {arquivo} na RAIZ, não em {PASTA}/. Escreva em '{arquivo}'."
    if rel.lower() == arquivo.lower() or rel.startswith(f"{PASTA}/"):
        return None
    return (f"A Maestro não implementa: '{rel}' é trabalho de Worker. Crie a tarefa com plan_feature "
            f"e despache com run_task. Você só escreve em {arquivo} e {PASTA}/.")


def bloco() -> str:
    b = BLOCO.get()
    return prompt_block() if b is None else b


# ------------------------------------------------------------------ ferramenta

def _session_note(_root: Path, args: dict) -> str:
    conv = taskdb.CONV.get()
    # A nota é o ato de encerrar: funcionalidade em validação fecha junto — se a Maestro validou
    # mesmo (senão recusa, antes de gravar qualquer coisa). Sem parâmetro para isso: um modelo
    # pequeno esquecia o feature_id e dizia que tinha passado.
    fechadas = taskdb.encerra_validadas(conv) if conv is not None else []
    nome = write(workspace.root(), conv, args.get("objective"), args.get("result"),
                 args.get("decisions") or [], args.get("problems") or [], args.get("next_step"))
    if conv is not None:
        sync(conv)
    return (f"Nota gravada em {SESSOES}/{nome}"
            + "".join(f"; funcionalidade '{t}' encerrada" for t in fechadas)
            + f". Se algo estrutural mudou, atualize {config.PROJECT_MEMORY_FILE}, {PASTA}/architecture.md "
            "ou knowledge/ com edit_file.")


SESSION_NOTE = register_extra(Tool(
    "session_note",
    "Registra onde esta sessão parou, para a próxima continuar sem reler a conversa: objetivo, "
    "resultado, DECISÕES (vão também para decisions.md) e o porquê, PROBLEMAS em aberto e o próximo "
    "passo. Encerra a funcionalidade em validação, se você já validou a entrega.",
    {"type": "object", "properties": {
        "objective": {"type": "string"},
        "result": {"type": "string", "description": "O que ficou pronto e como foi validado"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "problems": {"type": "array", "items": {"type": "string"}},
        "next_step": {"type": "string"}},
     "required": ["objective"]},
    _session_note))
# Escreve só dentro de .forja/, com caminho montado aqui e nunca vindo do modelo: é escrituração da
# Maestro, como o update_task. Marcada como mutante, pediria aprovação a cada nota e travaria a
# execução autônoma.

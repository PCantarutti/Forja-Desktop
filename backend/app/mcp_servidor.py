"""O Forja como servidor MCP (E17): o Claude (Claude Code, Claude Desktop) planeja, revisa e escreve os cards;
os Workers locais fazem o trabalho pesado.

Rota `/mcp` (streamable HTTP) no próprio FastAPI, só em 127.0.0.1, com um token fixo deste Forja
(`mcp_token`, diferente do da interface, que muda a cada abertura) e o interruptor "Permitir que o Claude
controle o Forja", desligado por padrão.

Nenhuma lógica duplicada: cada ferramenta MCP vira uma chamada de ferramenta do Forja executada pela mesma
máquina do agente (`agent._run_call`), dentro de um Run da **conversa-espelho** do projeto. Por isso valem as
mesmas regras e portões (E1), a mesma aprovação no PC e no celular, e tudo fica registrado e ao vivo na
conversa, sem depender do modelo lembrar de contar. `run_task` roda em segundo plano e devolve na hora; o
`task_status` acompanha.

O caminho de volta (do usuário para o Claude): o que o usuário escreve na conversa-espelho fica numa caixa de
entrada e sai no resultado da próxima ferramenta que o Claude chamar, e no `forja_inbox`.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from . import config, db, workspace

MAX_ESPERA = 100          # task_status: abaixo do timeout comum de uma chamada MCP
# s sem chamada: o Run da conversa-espelho encerra (a conversa fica). Com os hooks, o Stop do Claude já
# encerra na hora; isto é para quem não instalou. Era 900 s, e a tela dizia "trabalhando…" esse tempo todo.
OCIOSO = 90
JANELA_ESPELHO = timedelta(hours=6)  # conversa-espelho reaproveitada dentro desta janela
MAX_TEXTO_HOOK = 20_000
ORIGEM = "claude"


# ------------------------------------------------------------------ token e endereço

def _arquivo_token() -> Path:
    return config.DATA_DIR / "mcp_token"


def token() -> str:
    f = _arquivo_token()
    try:
        if t := f.read_text(encoding="utf-8").strip():
            return t
    except OSError:
        pass
    f.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_hex(24)
    f.write_text(t, encoding="utf-8")
    return t


def rotaciona() -> str:
    _arquivo_token().unlink(missing_ok=True)
    return token()


def url_base() -> str:
    return f"http://127.0.0.1:{os.getenv('FORJA_PORT') or '8765'}"


def grava_endereco() -> None:
    """Endereço e token para o hook do Claude Code (forja_hook.py) achar este Forja sem guardar segredo no
    projeto. Um arquivo por pasta de dados: a instância de validação não atende o hook da de uso."""
    try:
        (config.DATA_DIR / "mcp_endpoint.json").write_text(
            json.dumps({"url": url_base(), "token": token()}), encoding="utf-8")
    except OSError:
        pass


def configuracao() -> dict:
    """O que a tela de Configurações mostra para colar no Claude Code / Claude Desktop."""
    url, tok = f"{url_base()}/mcp", token()
    return {
        "ligado": bool(config.MCP_SERVIDOR), "permissao": config.MCP_PERMISSAO, "url": url, "token": tok,
        "comando": f'claude mcp add --transport http forja {url} --header "x-forja-token: {tok}"',
        "json": {"mcpServers": {"forja": {"type": "http", "url": url, "headers": {"x-forja-token": tok}}}},
    }


# ------------------------------------------------------------------ conversa-espelho

def eh_espelho(c: db.Conversation | None) -> bool:
    return bool(c and isinstance(c.origem, dict) and c.origem.get("externo") == ORIGEM)


def espelho(pasta: str) -> tuple[int, Path]:
    """A conversa do Claude neste projeto: a mais recente dentro da janela, senão uma nova. Tipo `maestro`
    para o MaestroView mostrar a árvore de tarefas, que são as mesmas Feature/Task."""
    from . import board
    projeto = board.projeto_de(pasta)
    limite = datetime.now() - JANELA_ESPELHO
    with db.session() as s:
        for c in s.scalars(select(db.Conversation).where(db.Conversation.workspace == projeto,
                                                         db.Conversation.updated_at >= limite)
                           .order_by(db.Conversation.updated_at.desc())):
            if eh_espelho(c):
                return c.id, Path(projeto)
        c = db.Conversation(kind="maestro", workspace=projeto, title=f"Claude · {Path(projeto).name}",
                            origem={"externo": ORIGEM})
        s.add(c)
        s.commit()
        return c.id, Path(projeto)


class Sessao:
    def __init__(self, conv: int, root: Path):
        self.conv, self.root = conv, root
        self.fila: asyncio.Queue = asyncio.Queue()
        self.run = None
        self.fundo: set[asyncio.Task] = set()   # run_task em andamento
        self.saidas: dict[int, dict] = {}        # attempt_id -> resultado do run_task (para o task_status)
        self.fechando = False                     # o laço saiu: chamada nova abre outra sessão


_SESSOES: dict[int, Sessao] = {}


def _req(conv: int):
    from . import mobile
    from .agent import RunRequest
    escolha = {**(mobile.defaults() or {}), **{k: v for k, v in (config.MAESTRO_MODEL or {}).items() if v}}
    return RunRequest(content=None, provider=escolha.get("provider") or "local", model=escolha.get("model") or "",
                      mode="maestro", permission=config.MCP_PERMISSAO, effort=escolha.get("effort") or "medio")


async def _laco(sess: Sessao, req):
    """O 'turno' da conversa-espelho: executa o que o Claude pede, na ordem, pela máquina do agente."""
    from . import sandbox, sessoes, taskdb
    from .agent import _run_call, _save, _save_result
    workspace.CURRENT.set(sess.root)
    sessoes.CONV.set(sess.conv)
    taskdb.CONV.set(sess.conv)  # plan_feature/list_tasks/update_task leem a conversa daqui
    sandbox.AUTONOMO.set(lambda: sess.run.permission in ("auto", "bypass"))
    yield {"type": "run_started", "run_id": sess.run.id}
    while not sess.run.cancel.is_set():
        try:
            job = await asyncio.wait_for(sess.fila.get(), OCIOSO)
        except TimeoutError:
            if sess.fundo:
                continue
            break
        if job["tipo"] == "evento":
            yield job["ev"]
            if job.get("fim") and not sess.fundo:  # o Claude terminou o turno (hook Stop): o Run fecha
                break
            continue
        call, fut = job["call"], job["fut"]
        msg = _save(sess.conv, role="assistant", content="", tool_calls=[call], meta={"via": "mcp"})
        yield {"type": "assistant_end", "message": msg.to_dict()}
        out: dict = {}
        if job.get("fundo"):
            async def fundo(call=call, out=out):
                async for ev in _run_call(sess.conv, call, req, sess.run, None, out):
                    await sess.run.publish(ev)
                await sess.run.publish(_save_result(sess.conv, call, out))
                if aid := sess.run.tentativas.get(call["id"]):
                    sess.saidas[aid] = out
            t = asyncio.create_task(fundo())
            sess.fundo.add(t)
            t.add_done_callback(sess.fundo.discard)
            fut.set_result({"fundo": t, "call": call, "out": out})
            continue
        try:
            async for ev in _run_call(sess.conv, call, req, sess.run, None, out):
                yield ev
            yield _save_result(sess.conv, call, out)
        except Exception as e:  # a chamada do Claude não pode derrubar o turno inteiro
            out.update(status="erro", text=f"Erro interno no Forja: {type(e).__name__}: {e}")
        if not fut.done():
            fut.set_result(out)
    # O que chegou enquanto fechava não se perde: vai para uma sessão nova (o Claude esperaria para sempre).
    sess.fechando = True
    sobra = []
    while not sess.fila.empty():
        sobra.append(sess.fila.get_nowait())
    if sobra:
        nova = _sessao(sess.conv, sess.root)
        for job in sobra:
            nova.fila.put_nowait(job)
    yield {"type": "done"}


def _sessao(conv: int, root: Path) -> Sessao:
    from .agent import RUNS, Run
    sess = _SESSOES.get(conv)
    if sess and sess.run and not sess.run.finished and not sess.fechando:
        sess.run.permission = config.MCP_PERMISSAO
        return sess
    sess = Sessao(conv, root)
    run = Run(conv)
    run.permission = config.MCP_PERMISSAO
    sess.run = run
    RUNS[run.id] = run
    _SESSOES[conv] = sess
    run.start(_req(conv), gerador=_laco(sess, _req(conv)))
    return sess


async def publica(pasta: str, ev_fn, fim: bool = False) -> int:
    """Grava algo na conversa-espelho (fala do Claude, pedido do usuário no Claude Code) e mostra ao vivo.
    `fim`: é o fim do turno do Claude; sem Worker rodando, o Run da conversa fecha (a tela para de dizer
    "trabalhando")."""
    conv, root = espelho(pasta)
    ev = ev_fn(conv)
    await _sessao(conv, root).fila.put({"tipo": "evento", "ev": ev, "fim": fim})
    return conv


# ------------------------------------------------------------------ caixa de entrada (usuário → Claude)

def caixa(conv: int, marcar: bool = True) -> list[str]:
    with db.session() as s:
        pendentes = [m for m in s.scalars(select(db.Message).where(db.Message.conversation_id == conv,
                                                                   db.Message.role == "user"))
                     if (m.meta or {}).get("inbox") and not (m.meta or {}).get("entregue")]
        textos = [m.content for m in pendentes]
        if marcar:
            for m in pendentes:
                m.meta = {**(m.meta or {}), "entregue": True}
            s.commit()
    return textos


def _com_caixa(conv: int, texto: str) -> str:
    if msgs := caixa(conv):
        texto += "\n\n[Mensagem do usuário, escrita no Forja para você:]\n" + "\n---\n".join(msgs)
    return texto


def guarda_para_o_claude(conv: int, texto: str) -> tuple[dict, dict]:
    """(mensagem, aviso) já gravados: a fala do usuário na caixa de entrada e a confirmação."""
    from .agent import _event, _save
    msg = _save(conv, role="user", content=texto, meta={"inbox": True})
    ativo = conv in _SESSOES and _SESSOES[conv].run and not _SESSOES[conv].run.finished
    aviso = _event(conv, "info", "Guardado para o Claude: chega no resultado da próxima ferramenta que ele chamar"
                   + ("." if ativo else ". Ele não está trabalhando com o Forja agora, então espera o próximo turno dele."))
    return {"type": "message", "message": msg.to_dict()}, aviso


def recebe_do_usuario(conv: int, texto: str):
    """O usuário escreveu na conversa-espelho (PC ou celular): vai para a caixa de entrada do Claude. Devolve um
    Run curto, para a tela receber a confirmação pelo mesmo SSE de sempre."""
    from .agent import RUNS, Run
    msg, aviso = guarda_para_o_claude(conv, texto)

    async def gerador():
        yield msg
        yield aviso
        yield {"type": "done"}

    run = Run(conv)
    RUNS[run.id] = run
    run.start(_req(conv), gerador=gerador())
    return run


# ------------------------------------------------------------------ execução das ferramentas

class ForjaDesligado(RuntimeError):
    pass


async def chamar(pasta: str, nome: str, args: dict, fundo: bool = False) -> tuple[dict, int, Sessao]:
    if not config.MCP_SERVIDOR:
        raise ForjaDesligado("O controle pelo Claude está desligado no Forja (Configurações › MCP).")
    conv, root = espelho(pasta)
    sess = _sessao(conv, root)
    call = {"id": "mcp_" + uuid.uuid4().hex[:12], "name": nome, "arguments": args}
    fut = asyncio.get_running_loop().create_future()
    await sess.fila.put({"tipo": "call", "call": call, "fut": fut, "fundo": fundo})
    return await fut, conv, sess


async def texto_de(pasta: str, nome: str, args: dict) -> str:
    try:
        out, conv, _ = await chamar(pasta, nome, args)
    except (ForjaDesligado, workspace.WorkspaceError) as e:
        return f"ERRO: {e}"
    texto = out.get("text") or ""
    if out.get("status") not in ("ok", None):
        texto = f"ERRO ({out.get('status')}): {texto}"
    return _com_caixa(conv, texto)


async def despacha(pasta: str, code: str, strategy: str) -> str:
    """run_task sem bloquear: abre a tentativa num Worker local e devolve o attempt_id."""
    try:
        r, conv, sess = await chamar(pasta, "run_task", {k: v for k, v in (("code", code), ("strategy", strategy)) if v},
                                     fundo=True)
    except (ForjaDesligado, workspace.WorkspaceError) as e:
        return f"ERRO: {e}"
    t0 = time.monotonic()
    while time.monotonic() - t0 < 20:  # a tentativa é gravada antes do Worker começar: sai em segundos
        if aid := sess.run.tentativas.get(r["call"]["id"]):
            return _com_caixa(conv, json.dumps({"attempt_id": aid, "status": "running",
                                                "dica": "acompanhe com task_status(attempt_id, wait_s)"}))
        if r["fundo"].done():  # recusou antes de abrir tentativa (dependência, limite, sem Worker)
            return _com_caixa(conv, "ERRO: " + (r["out"].get("text") or "run_task não começou."))
        await asyncio.sleep(0.2)
    return _com_caixa(conv, "ERRO: o Worker não abriu a tentativa em 20 s. Veja o painel do Forja.")


async def status_da_tentativa(pasta: str, attempt_id: int, wait_s: int) -> str:
    if not config.MCP_SERVIDOR:
        return "ERRO: o controle pelo Claude está desligado no Forja (Configurações › MCP)."
    conv, root = espelho(pasta)
    sess = _SESSOES.get(conv)
    fim = time.monotonic() + max(0, min(MAX_ESPERA, int(wait_s or 0)))
    while True:
        with db.session() as s:
            a = s.get(db.Attempt, int(attempt_id))
            if not a:
                return f"ERRO: tentativa {attempt_id} não existe."
            estado, erro_ = a.status, a.error
            code = a.task.code if a.task else "?"
        pronto = sess and attempt_id in sess.saidas
        if estado != "running" and (pronto or not sess or time.monotonic() >= fim):
            break
        if time.monotonic() >= fim:
            return _com_caixa(conv, json.dumps({"attempt_id": attempt_id, "task": code, "status": "running"}))
        await asyncio.sleep(1)
    corpo = {"attempt_id": attempt_id, "task": code, "status": estado}
    if sess and attempt_id in sess.saidas:
        corpo["resultado"] = sess.saidas[attempt_id].get("text")  # o mesmo resultado enxuto que a Maestro recebe
    elif erro_:
        corpo["erro"] = erro_[:2000]
    corpo["proximo"] = "Leia o resultado e feche a tarefa com update_task (quem decide é você, não o Worker)."
    return _com_caixa(conv, json.dumps(corpo, ensure_ascii=False))


async def nota(pasta: str, texto: str) -> str:
    if not config.MCP_SERVIDOR:
        return "ERRO: o controle pelo Claude está desligado no Forja (Configurações › MCP)."
    from .agent import _save

    def ev(conv):
        m = _save(conv, role="assistant", content=str(texto)[:MAX_TEXTO_HOOK], meta={"via": "mcp", "nota": True})
        return {"type": "assistant_end", "message": m.to_dict()}
    conv = await publica(pasta, ev)
    return _com_caixa(conv, "Anotado no painel do Forja.")


# ------------------------------------------------------------------ ferramentas extras (só existem para o MCP)

def _project_state(root: Path, _args: dict) -> str:
    from . import board, projstate
    projeto = board.projeto_de(str(root))
    partes = [f"# Projeto {Path(projeto).name} ({projeto})"]
    if forja := projstate.forja_md(Path(projeto)).strip():
        partes.append("## FORJA.md\n" + forja[:6000])
    cards = board.listar(projeto)
    abertos = [c for c in cards if c["status"] not in ("concluido", "rejeitado")]
    contagem = {s: sum(c["status"] == s for c in cards) for s in board.STATUS}
    partes.append("## Board\n" + ", ".join(f"{s}: {n}" for s, n in contagem.items() if n) + "\n" + "\n".join(
        f"- #{c['id']} [{c['status']}] {c['tipo']}: {c['titulo']}" for c in abertos[:25]))
    return "\n\n".join(partes)


def _issue_list(root: Path, args: dict) -> str:
    from . import board
    status = str(args.get("status") or "").strip()
    cards = [c for c in board.listar(board.projeto_de(str(root))) if not status or c["status"] == status]
    return json.dumps([{k: c[k] for k in ("id", "titulo", "tipo", "area", "severidade", "status", "origem")}
                       | {"onde": next((f"{e['arquivo']}:{e.get('linha') or ''}" for e in c["evidencias"]
                                        if e.get("arquivo")), None)} for c in cards[:100]], ensure_ascii=False)


def _issue_update(root: Path, args: dict) -> str:
    from . import board
    from .tools import ToolError
    try:
        issue_id = int(args.get("id"))
        card = board.pega(issue_id)
        if card["projeto"] != board.projeto_de(str(root)):
            raise ToolError(f"O card #{issue_id} é de outro projeto.")
        mudar = {k: args[k] for k in ("titulo", "descricao", "tipo", "area", "severidade", "status", "prompt",
                                      "verify_sugerido") if k in args}
        if mudar.get("status") == "andamento":
            raise ToolError("Para iniciar um card use o Iniciar do board (ou planeje tarefas e rode com run_task).")
        novo = board.atualizar(issue_id, mudar)
    except board.BoardError as e:
        raise ToolError(str(e)) from None
    return f"Card #{novo['id']} atualizado: {novo['status']} · {novo['titulo']}"


def _registra():
    from .tools import Tool, _obj, register_extra
    register_extra(Tool("project_state", "FORJA.md, o board e as tarefas abertas do projeto.", _obj({}, []),
                        _project_state))
    register_extra(Tool("issue_list", "Cards do board do projeto.",
                        _obj({"status": {"type": "string"}}, []), _issue_list))
    # mutante: mudar a coluna de um card passa pelo modo de permissão (no Manual, aprovação no PC/celular)
    register_extra(Tool("issue_update", "Muda um card do board (coluna, título, prioridade...).",
                        _obj({"id": {"type": "integer"}, "status": {"type": "string"}}, ["id"]),
                        _issue_update, mutating=True))


_registra()


# ------------------------------------------------------------------ o servidor MCP

INSTRUCOES = (
    "Este é o Forja, o harness de IA local do usuário. Você planeja e revisa; os Workers locais do Forja fazem "
    "o trabalho. Sempre passe `path` = a pasta do projeto em que você está (caminho absoluto). Fluxo: "
    "project_state → (sem FORJA.md: escreva um curto com forja_md) → plan_feature (tarefas com goal, relevant_files e verify_command) → run_task (devolve um "
    "attempt_id na hora) → task_status até terminar → leia o resultado e feche com update_task → terminadas todas, "
    "validate_feature encerra a funcionalidade. Achou um bug ou "
    "melhoria? issue_create com arquivo, linha e trecho real. Use forja_note para contar o progresso ao usuário "
    "no painel do Forja em cada etapa. Se um resultado trouxer '[Mensagem do usuário…]', ela vale como pedido "
    "dele: leve em conta antes de seguir.")


def _servidor():
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("forja", instructions=INSTRUCOES)

    @mcp.tool()
    async def project_state(path: str) -> str:
        """FORJA.md do projeto, o resumo do board e as tarefas abertas. Chame primeiro."""
        estado = await texto_de(path, "project_state", {})
        tarefas = await texto_de(path, "list_tasks", {})
        return f"{estado}\n\n## Tarefas\n{tarefas}"

    @mcp.tool()
    async def plan_feature(path: str, tasks: list[dict], title: str = "", goal: str = "",
                           feature_id: int | None = None, explorations: list[str] | None = None) -> str:
        """Cria (ou amplia, com feature_id) uma funcionalidade com tarefas. Cada tarefa: title, goal,
        relevant_files, acceptance, verify_command (ou verify_reason), depends_on. Os mesmos portões do Maestro."""
        args = {"tasks": tasks, "title": title, "goal": goal}
        if feature_id:
            args["feature_id"] = feature_id
        if explorations:
            args["explorations"] = explorations
        return await texto_de(path, "plan_feature", args)

    @mcp.tool()
    async def list_tasks(path: str, status: str = "", code: str = "") -> str:
        """Tarefas do projeto (e o detalhe de uma, com code)."""
        return await texto_de(path, "list_tasks", {k: v for k, v in (("status", status), ("code", code)) if v})

    @mcp.tool()
    async def update_task(path: str, code: str, status: str = "", reason: str = "", contract: dict | None = None,
                          priority: int | None = None, max_attempts: int | None = None, model_slot: str | None = None) -> str:
        """Fecha (status='completed', exige a prova do verify), bloqueia ou corrige o contrato de uma tarefa."""
        args: dict = {"code": code}
        for k, v in (("status", status), ("reason", reason), ("contract", contract), ("priority", priority),
                     ("max_attempts", max_attempts), ("model_slot", model_slot)):
            if v not in (None, ""):
                args[k] = v
        return await texto_de(path, "update_task", args)

    @mcp.tool()
    async def run_task(path: str, code: str = "", strategy: str = "") -> str:
        """Despacha a tarefa para um Worker LOCAL do Forja e devolve na hora um attempt_id (não bloqueia).
        Sem code, o Forja escolhe a próxima pronta. A partir da 2ª tentativa, diga em strategy o que muda."""
        return await despacha(path, code.strip().upper(), strategy)

    @mcp.tool()
    async def task_status(path: str, attempt_id: int, wait_s: int = 60) -> str:
        """Estado de uma tentativa. Espera até wait_s segundos (máx. 100) pelo fim e devolve o resultado enxuto."""
        return await status_da_tentativa(path, attempt_id, wait_s)

    @mcp.tool()
    async def issue_create(path: str, titulo: str, tipo: str, arquivo: str, linha: int | None = None,
                           trecho: str = "", descricao: str = "", severidade: int = 2, area: str = "",
                           prompt: str = "", verify_sugerido: str = "") -> str:
        """Cria um card no board (coluna Novo). Recusa sem evidência real: arquivo, linha e o trecho copiado
        exatamente. tipo: bugfix, feature, improvement, visual, todo, seguranca."""
        args = {"titulo": titulo, "tipo": tipo, "arquivo": arquivo, "trecho": trecho, "descricao": descricao,
                "severidade": severidade, "prompt": prompt, "verify_sugerido": verify_sugerido}
        if linha:
            args["linha"] = linha
        if area:
            args["area"] = area
        return await texto_de(path, "board_card", args)

    @mcp.tool()
    async def issue_list(path: str, status: str = "") -> str:
        """Cards do board (status: novo, backlog, andamento, revisao, concluido, rejeitado)."""
        return await texto_de(path, "issue_list", {"status": status} if status else {})

    @mcp.tool()
    async def issue_update(path: str, id: int, status: str = "", titulo: str = "", descricao: str = "",
                           severidade: int | None = None) -> str:
        """Muda um card. Mudar de coluna segue o modo de permissão do Forja (pode pedir aprovação ao usuário)."""
        args: dict = {"id": id}
        for k, v in (("status", status), ("titulo", titulo), ("descricao", descricao), ("severidade", severidade)):
            if v not in (None, ""):
                args[k] = v
        return await texto_de(path, "issue_update", args)

    @mcp.tool()
    async def forja_note(path: str, texto: str) -> str:
        """Conta o progresso ao usuário no painel do Forja (e no celular dele). Use a cada etapa."""
        return await nota(path, texto)

    @mcp.tool()
    async def validate_feature(path: str, feature_id: int, resumo: str = "") -> str:
        """Depois da última tarefa: roda de novo os verifies da funcionalidade (só eles, pelo run_command do
        Forja, com sandbox e aprovação) e, passando, encerra a funcionalidade. Falhou: plan_feature com correções."""
        from . import taskdb
        if not config.MCP_SERVIDOR:
            return "ERRO: o controle pelo Claude está desligado no Forja (Configurações › MCP)."
        conv, _ = espelho(path)
        f = next((x for x in taskdb.validando(conv) if x["id"] == int(feature_id)), None)
        if not f:
            return _com_caixa(conv, f"ERRO: a funcionalidade {feature_id} não está esperando validação "
                                    "(todas as tarefas dela precisam estar concluídas).")
        saidas = [f"$ {cmd}\n{await texto_de(path, 'run_command', {'command': cmd})}" for cmd in f["verify"]]
        fecha = await texto_de(path, "session_note", {"objective": f["title"],
                                                      "result": resumo or "Entrega validada: verifies passaram de novo."})
        return "\n\n".join(saidas + [fecha])

    @mcp.tool()
    async def forja_md(path: str, conteudo: str) -> str:
        """Grava o FORJA.md do projeto (o que é, stack, como rodar e testar, convenções): o plan_feature exige.
        Passa pelo write_file do Forja, com checkpoint e a aprovação do modo de permissão."""
        from . import config as _cfg
        return await texto_de(path, "write_file", {"path": _cfg.PROJECT_MEMORY_FILE, "content": conteudo})

    @mcp.tool()
    async def forja_inbox(path: str) -> str:
        """Mensagens que o usuário escreveu para você no Forja ou no celular e que você ainda não recebeu."""
        if not config.MCP_SERVIDOR:
            return "ERRO: o controle pelo Claude está desligado no Forja (Configurações › MCP)."
        conv, _ = espelho(path)
        return "\n---\n".join(caixa(conv)) or "Nenhuma mensagem nova do usuário."

    return mcp


SERVIDOR = _servidor()
_GERENTE = None


@asynccontextmanager
async def gerente():
    """Um gerente de sessões por vida do app (o `run()` dele só pode ser chamado uma vez)."""
    global _GERENTE
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    anterior = _GERENTE  # outro ciclo de vida no mesmo processo (testes): ao sair, ele volta a valer
    _GERENTE = StreamableHTTPSessionManager(app=SERVIDOR._mcp_server)
    grava_endereco()
    async with _GERENTE.run():
        try:
            yield
        finally:
            _GERENTE = anterior
            if anterior is None:
                for sess in list(_SESSOES.values()):
                    if sess.run:
                        sess.run.stop()


async def _responde(send, codigo: int, texto: str) -> None:
    corpo = json.dumps({"error": texto}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": codigo,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(corpo)).encode())]})
    await send({"type": "http.response.body", "body": corpo})


async def porteiro(scope, receive, send) -> None:
    """/mcp: token deste Forja no cabeçalho x-forja-token (ou Authorization: Bearer) e o interruptor ligado."""
    if scope["type"] != "http":
        return
    cab = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
    dado = cab.get("x-forja-token") or cab.get("authorization", "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(dado or "-", token()):
        return await _responde(send, 401, "Token do Forja ausente ou errado (Configurações › MCP).")
    if not config.MCP_SERVIDOR:
        return await _responde(send, 403, "O controle pelo Claude está desligado no Forja (Configurações › MCP).")
    if _GERENTE is None:
        return await _responde(send, 503, "O servidor MCP do Forja ainda não subiu.")
    await _GERENTE.handle_request(scope, receive, send)


class _App:
    """A Route do Starlette só trata como app ASGI o que NÃO é função: função vira request/response."""
    async def __call__(self, scope, receive, send):
        await porteiro(scope, receive, send)


PORTEIRO = _App()


# ------------------------------------------------------------------ hooks do Claude Code (o diálogo inteiro)

def nome_do_modelo(mid: str) -> str:
    """`claude-opus-5-5` → `Claude Opus 5.5`; `claude-haiku-4-5-20251001` → `Claude Haiku 4.5`."""
    partes = [p for p in str(mid or "").split("-") if p and not (p.isdigit() and len(p) == 8)]
    if len(partes) < 2 or partes[0].lower() != "claude":
        return str(mid or "Claude")
    versao = ".".join(p for p in partes[2:] if p.isdigit())
    return " ".join(["Claude", partes[1].capitalize()] + ([versao] if versao else []))


def _ts(e: dict) -> float | None:
    try:
        return datetime.fromisoformat(str(e.get("timestamp")).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def turno_do_transcript(transcript: str) -> dict:
    """O último turno do transcript .jsonl do Claude Code: texto final, modelo, tokens gerados e duração.
    É o que dá à linha de estatísticas da conversa-espelho os números do Claude, não os do Forja."""
    try:
        linhas = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    entradas = []
    for linha in linhas:
        try:
            entradas.append(json.loads(linha))
        except ValueError:
            continue

    def eh_pedido(e):  # fala do usuário (não o resultado de ferramenta, que também vem como "user")
        msg = e.get("message") or {}
        c = msg.get("content")
        return e.get("type") == "user" and (isinstance(c, str) or any(
            isinstance(b, dict) and b.get("type") == "text" for b in c or []))

    inicio = max((i for i, e in enumerate(entradas) if eh_pedido(e)), default=-1)
    turno = [e for e in entradas[inicio + 1:] if e.get("type") == "assistant" or (e.get("message") or {}).get("role") == "assistant"]
    texto, modelo, tokens = "", "", 0
    for e in turno:
        msg = e.get("message") or {}
        modelo = msg.get("model") or modelo
        tokens += int((msg.get("usage") or {}).get("output_tokens") or 0)
        conteudo = msg.get("content")
        blocos = [conteudo] if isinstance(conteudo, str) else [
            b.get("text", "") for b in conteudo or [] if isinstance(b, dict) and b.get("type") == "text"]
        if any(t.strip() for t in blocos):
            texto = "\n".join(blocos)
    t0 = _ts(entradas[inicio]) if inicio >= 0 else None
    t1 = _ts(turno[-1]) if turno else None
    segundos = round(t1 - t0, 1) if t0 and t1 and t1 >= t0 else 0.0
    return {"texto": texto, "modelo": modelo, "tokens": tokens, "segundos": segundos}


def _ultimo_texto(transcript: str) -> str:
    return turno_do_transcript(transcript).get("texto", "")


def _guarda_modelo(conv: int, modelo: str) -> None:
    """O modelo do Claude nesta conversa: a linha ao vivo e o cabeçalho do Maestro mostram ele."""
    with db.session() as s:
        c = s.get(db.Conversation, conv)
        if c and eh_espelho(c) and (c.origem or {}).get("modelo") != modelo:
            c.origem = {**c.origem, "modelo": modelo}
            s.commit()


async def hook(dados: dict) -> dict:
    """Um evento de hook do Claude Code: UserPromptSubmit, Stop ou PostToolUse."""
    from .agent import _event, _save
    if not config.MCP_SERVIDOR:
        return {"ok": False, "motivo": "desligado"}
    pasta = str(dados.get("cwd") or "")
    evento = dados.get("hook_event_name") or dados.get("evento")
    if evento == "UserPromptSubmit" and (texto := str(dados.get("prompt") or "").lstrip("\ufeff").strip()):
        def ev(conv):
            m = _save(conv, role="user", content=texto[:MAX_TEXTO_HOOK], meta={"via": "claude_code"})
            return {"type": "message", "message": m.to_dict()}
    elif evento == "Stop":
        turno = turno_do_transcript(str(dados.get("transcript_path") or ""))
        texto = str(dados.get("last_assistant_message") or "").strip() or turno.get("texto", "")
        if not texto:
            return {"ok": True, "vazio": True}
        modelo = nome_do_modelo(turno.get("modelo")) if turno.get("modelo") else "Claude"
        stats = {"model": modelo, "tokens": turno.get("tokens", 0), "seconds": turno.get("segundos", 0.0),
                 "tps": None, "estimated": not turno.get("tokens")}

        def ev(conv):
            _guarda_modelo(conv, modelo)
            m = _save(conv, role="assistant", content=texto[:MAX_TEXTO_HOOK], meta={"via": "claude_code", "stats": stats})
            return {"type": "assistant_end", "message": m.to_dict()}
    elif evento == "PostToolUse":
        nome = str(dados.get("tool_name") or "")
        if nome.startswith("mcp__forja"):
            return {"ok": True, "ignorado": "já registrado pelo MCP"}
        if nome in HOOK_IGNORADAS:
            return {"ok": True, "ignorado": nome}
        entrada = dados.get("tool_input") or {}
        alvo = next((str(entrada[k]) for k in ("file_path", "path", "command", "pattern", "url") if entrada.get(k)), "")
        resumo = f"Claude usou {nome}" + (f": {alvo[:200]}" if alvo else "")

        def ev(conv):
            return _event(conv, "info", resumo)
    else:
        return {"ok": True, "ignorado": evento}
    try:
        conv = await publica(pasta, ev, fim=evento == "Stop")
    except workspace.WorkspaceError as e:
        return {"ok": False, "motivo": str(e)}
    return {"ok": True, "conversa": conv}


HOOK_EVENTOS = ("UserPromptSubmit", "Stop", "PostToolUse")
HOOK_IGNORADAS = {"ToolSearch", "TodoWrite"}  # engrenagem interna do Claude Code: ruído no painel


def instala_hooks(pasta: str) -> dict:
    """Grava os hooks no `.claude/settings.local.json` do projeto (pessoal: fora do git). Não guarda segredo:
    o script lê o endereço e o token do arquivo na pasta de dados deste Forja."""
    import sys
    from . import board
    projeto = Path(board.projeto_de(pasta))
    script = Path(__file__).with_name("forja_hook.py")
    comando = f'"{sys.executable}" "{script}" "{config.DATA_DIR}"'
    arq = projeto / ".claude" / "settings.local.json"
    try:
        atual = json.loads(arq.read_text(encoding="utf-8")) if arq.is_file() else {}
    except ValueError:
        raise ValueError(f"{arq} não é um JSON válido: corrija antes (não sobrescrevo).") from None
    hooks = atual.setdefault("hooks", {})
    for ev in HOOK_EVENTOS:
        grupos = hooks.setdefault(ev, [])
        grupos[:] = [g for g in grupos if not any("forja_hook.py" in (h.get("command") or "") for h in g.get("hooks") or [])]
        grupo = {"hooks": [{"type": "command", "command": comando, "timeout": 10}]}
        if ev == "PostToolUse":
            grupo["matcher"] = "*"
        grupos.append(grupo)
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(atual, indent=2, ensure_ascii=False), encoding="utf-8")
    grava_endereco()
    return {"arquivo": str(arq), "eventos": list(HOOK_EVENTOS)}

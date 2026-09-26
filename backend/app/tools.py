"""Registry de ferramentas do agente.

Cada Tool declara schema (formato OpenAI), handler, flag `mutating` e opcionalmente
`preview` (usado no card de aprovação). Novas ferramentas (web, shell, MCP...) entram
só com um `register(Tool(...))`, sem mexer no loop do agente.
"""
from __future__ import annotations

import asyncio
import contextvars
import difflib
import inspect
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config, workspace


class ToolError(Exception):
    """Erro devolvido ao modelo como resultado da ferramenta (não derruba o loop)."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[Path, dict], str]  # pode ser async (ex.: MCP); pode devolver {"text", "attachments"}
    mutating: bool = False
    preview: Callable[[Path, dict], dict] | None = None
    always_ask: bool = False  # pede aprovação mesmo com escrita "automática" (ex.: shell)
    source: str = "builtin"   # builtin | mcp:<servidor>
    requires: frozenset[str] = frozenset()  # capacidades do modelo exigidas, ex.: {"vision"}
    available: Callable[[], bool] | None = None  # some da lista quando False (ex.: delegate_task sem subagente)
    poll: bool = False        # acompanhar um processo é repetir a mesma chamada: fica fora do freio de loop
    # Teto da execução (DeepSeek Harness: timeout-policy). 0 = o padrão (config.TOOL_TIMEOUT); None =
    # sem teto aqui, porque a ferramenta controla o próprio tempo (shell, terminal, espera de processo).
    timeout: float | None = 0

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


REGISTRY: dict[str, Tool] = {}
# Ferramentas que só existem num modo específico (hoje: as do Maestro). Ficam fora do REGISTRY de
# propósito — não devem aparecer nas Configurações, nem no modo Agente, nem em /api/tools — mas
# precisam ser encontráveis por get_tool/execute na hora de rodar. Quem decide quando elas entram
# no prompt é agent.available_tools().
EXTRA: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def register_extra(tool: Tool) -> Tool:
    EXTRA[tool.name] = tool
    return tool


def active(caps: set[str] | None = None) -> list[Tool]:
    """Ferramentas ligadas nas Configurações e, se `caps` for dado, que o modelo consegue usar.

    `caps=None` ignora capacidades (listagens da UI). As desligadas/bloqueadas não vão para o modelo.
    """
    return [t for t in REGISTRY.values()
            if t.name not in config.DISABLED_TOOLS and (caps is None or t.requires <= caps)
            and (t.available is None or t.available())]


def blocked(caps: set[str]) -> list[dict]:
    """Ferramentas ligadas mas bloqueadas pelo modelo atual (aparecem no painel como bloqueadas)."""
    return [{"name": t.name, "missing": sorted(t.requires - caps)}
            for t in REGISTRY.values() if t.name not in config.DISABLED_TOOLS and not t.requires <= caps]


def vision_caps(detected: set[str] | None, override: str) -> set[str]:
    """Capacidades efetivas: o que o provider informou (ou nada) ajustado pelo override do usuário."""
    caps = set(detected or ())
    if override == "yes":
        caps.add("vision")
    elif override == "no":
        caps.discard("vision")
    return caps


def get_tool(name: str, caps: set[str] | None = None) -> Tool:
    if name in config.DISABLED_TOOLS:
        raise ToolError(f"A ferramenta '{name}' está desativada nas configurações do Forja.")
    tool = REGISTRY.get(name) or EXTRA.get(name)
    if not tool:
        raise ToolError(f"Ferramenta desconhecida: '{name}'. Disponíveis: {', '.join(REGISTRY)}")
    if caps is not None and not tool.requires <= caps:
        faltam = ", ".join(sorted(tool.requires - caps))
        raise ToolError(f"'{name}' exige {faltam} e o modelo atual não tem (ou marque 'Visão: sim' no painel). "
                        "Valide pela estrutura da página: browser_read e browser_console.")
    return tool


def coerce_args(tool: Tool, args: dict) -> dict:
    """Converte strings vindas do fallback XML ("true", "10") para o tipo do schema."""
    props = tool.parameters.get("properties", {})
    out = dict(args)
    for k, v in args.items():
        t = props.get(k, {}).get("type")
        if isinstance(v, str) and t == "boolean":
            out[k] = v.strip().lower() in ("true", "1", "yes", "sim")
        elif isinstance(v, str) and t == "integer" and v.strip().lstrip("-").isdigit():
            out[k] = int(v)
    return out


def validar(tool: Tool, args: dict) -> None:
    """Confere os argumentos contra o schema antes de rodar (DeepSeek Harness: `invalid arguments`).

    Sem isto o modelo recebia um KeyError de dentro do handler, ou a ferramenta rodava com um campo
    errado ignorado. Só o nível de cima, e só obrigatório + tipo primitivo: o interior (sheets,
    edits…) alguns handlers aceitam de propósito em várias formas, porque é como o modelo manda.
    """
    props = tool.parameters.get("properties") or {}
    tipos = {"string": str, "boolean": bool, "integer": int, "number": (int, float)}
    erros = [f"falta '{k}'" for k in tool.parameters.get("required") or () if k not in args]
    for k, v in args.items():
        t = (props.get(k) or {}).get("type")
        if t in tipos and v is not None and (not isinstance(v, tipos[t]) or (t != "boolean" and isinstance(v, bool))):
            erros.append(f"'{k}' deveria ser {t}, veio {type(v).__name__}")
    if erros:
        raise ToolError(f"Argumentos inválidos para {tool.name}: {'; '.join(erros)}. Confira o schema da ferramenta.")


def _falha(tool: "Tool", e: Exception) -> ToolError:
    """Erro de dentro da ferramenta não é erro de argumento.

    Isto dizia "Argumentos inválidos" para qualquer KeyError, TypeError ou ValueError — inclusive
    os vindos lá de dentro de uma biblioteca. Um `KeyError: no style with name 'Heading 3'` do
    python-docx chegava ao modelo como culpa dele; ele reescreveu o Markdown duas vezes, não
    adiantou, e acabou gerando o documento do zero por cima do arquivo do usuário.

    O que separa os dois é a chave: se ela é um parâmetro desta ferramenta, o handler tropeçou no
    que faltava nos argumentos — aí o aviso antigo está certo.
    """
    chave = e.args[0] if isinstance(e, KeyError) and e.args else None
    if chave in (tool.parameters.get("properties") or {}):
        return ToolError(f"Argumentos inválidos para {tool.name}: faltando ou incorreto {e}")
    return ToolError(f"{tool.name} falhou: {type(e).__name__}: {e}")


def _call(name: str, fn_attr: str, args: dict, root: Path | None):
    tool = get_tool(name)
    try:
        argumentos = coerce_args(tool, args)
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError(f"Argumentos inválidos para {name}: faltando ou incorreto {e}") from e
    validar(tool, argumentos)
    try:
        return getattr(tool, fn_attr)(root or workspace.root(), argumentos)
    except (KeyError, TypeError, ValueError) as e:
        raise _falha(tool, e) from e


def run_tool(name: str, args: dict, root: Path | None = None) -> str:
    return _call(name, "handler", args, root)


async def execute(name: str, args: dict, root: Path | None = None) -> str:
    """Executa handler sync (em thread) ou async (direto)."""
    tool = get_tool(name)
    if not inspect.iscoroutinefunction(tool.handler):
        return await asyncio.to_thread(run_tool, name, args, root)
    try:
        argumentos = coerce_args(tool, args)
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError(f"Argumentos inválidos para {name}: faltando ou incorreto {e}") from e
    validar(tool, argumentos)
    try:
        return await tool.handler(root or workspace.root(), argumentos)
    except (KeyError, TypeError, ValueError) as e:
        raise _falha(tool, e) from e


def unregister_source(source: str) -> None:
    for name in [n for n, t in REGISTRY.items() if t.source == source]:
        del REGISTRY[name]


def preview_tool(name: str, args: dict, root: Path | None = None) -> dict | None:
    return _call(name, "preview", args, root) if get_tool(name).preview else None


# ---------------------------------------------------------------- confinamento

def resolve_path(root: Path, path: str | None) -> Path:
    """Resolve `path` dentro de `root`. Bloqueia `..`, absolutos fora da raiz e symlinks que escapem."""
    root_r = root.resolve()
    raw = (path or ".").strip() or "."
    # Modelos às vezes mandam "/workspace/x"; trate como relativo à raiz. Caminho absoluto de verdade
    # (C:/... ou /home/...) passa direto: o join abaixo o mantém e o confinamento decide.
    if raw == "/workspace" or raw.startswith("/workspace/"):
        raw = raw[len("/workspace"):].lstrip("/") or "."
    target = (root_r / raw).resolve()  # resolve() segue symlinks
    if not target.is_relative_to(root_r):
        raise ToolError(
            f"Acesso negado: '{path}' está fora da pasta de trabalho. "
            "Use caminhos relativos à pasta de trabalho.")
    return target


# Espelhos que o Forja gera a partir do banco (projstate.sync): escrever neles é trabalho perdido —
# a próxima mudança de tarefa reescreve tudo — e o modelo acharia que mudou o estado das tarefas.
GERADOS = (".forja/progress.md", ".forja/tasks.json")


def _nao_gerado(root: Path, p: Path) -> None:
    if _rel(root, p) in GERADOS:
        raise ToolError(f"{_rel(root, p)} é gerado pelo Forja a partir das tarefas; não edite. "
                        "Mude o estado pelas ferramentas de tarefa (update_task, plan_feature).")


def _rel(root: Path, p: Path) -> str:
    if SPILL_DIR in p.parents:  # saída guardada de outra ferramenta: fora da pasta, vai o caminho inteiro
        return str(p)
    return p.relative_to(root.resolve()).as_posix() or "."


# ---------------------------------------------------------------- spill de saída grande
# Resultado enorme (log de build, página inteira, grep amplo) enchia a janela do modelo local de uma
# vez. Como no DeepSeek Harness: cabeça e cauda vão ao modelo, o texto inteiro fica num arquivo que
# ele lê por partes com read_file/grep. Só esses dois leem fora da pasta de trabalho, e só aqui.
SPILL_DIR = (config.DATA_DIR / "spill").resolve()
SPILL_CHARS = 24_000
SPILL_HEAD = 16_000
SPILL_TAIL = 6_000


def spill(texto: str, nome: str) -> str:
    if len(texto) <= SPILL_CHARS:
        return texto
    destino = SPILL_DIR / f"{nome}.txt"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    omitidos = len(texto) - SPILL_HEAD - SPILL_TAIL
    return (f"{texto[:SPILL_HEAD]}\n\n[...]\n\n{texto[-SPILL_TAIL:]}\n\n(Omitidos {omitidos} caracteres. "
            f"Resultado completo em: {destino}. Leia por partes com read_file (start_line/end_line) ou "
            "procure nele com grep.)")


SKILLS_DIR = (config.DATA_DIR / "skills").resolve()  # skills do usuário: recursos que a skill manda ler


def resolve_leitura(root: Path, path: str | None) -> Path:
    """resolve_path, mais a pasta de spill e a de skills do usuário: os únicos lugares fora da raiz
    que dá para ler."""
    raw = (path or "").strip()
    if raw:
        alvo = Path(raw).expanduser()
        if alvo.is_absolute() and ((alvo := alvo.resolve()).is_relative_to(SPILL_DIR)
                                   or alvo.is_relative_to(SKILLS_DIR)):
            return alvo
    return resolve_path(root, path)


# ---------------------------------------------------------------- ler antes de escrever
# Portado da fs-observation-policy do DeepSeek Harness. Editar de memória — o modelo "lembra" como
# o arquivo era, ou o arquivo mudou por um comando depois da leitura — é a origem de old_str que
# não bate e de write_file que apaga o que ele nunca viu. O dicionário é por execução (contextvar
# que o agente liga); fora de uma execução (testes, rotas da API) não há checagem.
LIDOS: contextvars.ContextVar[dict | None] = contextvars.ContextVar("forja_lidos", default=None)


def _versao(p: Path) -> tuple[int, int]:
    st = p.stat()
    return st.st_mtime_ns, st.st_size


def marcar_lido(p: Path) -> None:
    lidos = LIDOS.get()
    if lidos is not None and p.is_file():
        lidos[str(p)] = _versao(p)


def _observado(root: Path, p: Path) -> None:
    """Recusa escrever em arquivo existente que não foi lido nesta execução, ou que mudou depois."""
    lidos = LIDOS.get()
    if lidos is None or not p.is_file():
        return
    visto = lidos.get(str(p))
    if visto is None:
        raise ToolError(f"Não dá para alterar '{_rel(root, p)}': o arquivo ainda não foi lido nesta conversa. "
                        "Leia com read_file e tente de novo.")
    if visto != _versao(p):
        raise ToolError(f"'{_rel(root, p)}' mudou depois da sua última leitura (outro comando ou o usuário "
                        "mexeu). Leia de novo com read_file e tente de novo.")


def _read_text(p: Path) -> str:
    if not p.is_file():
        raise ToolError(f"Arquivo não encontrado: '{p.name}'. Use list_dir para ver o que existe.")
    size = p.stat().st_size
    if size > config.MAX_FILE_BYTES:
        raise ToolError(f"Arquivo grande demais ({size} bytes, limite {config.MAX_FILE_BYTES}).")
    data = p.read_bytes()
    if b"\x00" in data[:8192]:
        raise ToolError("Arquivo binário; só arquivos de texto são suportados.")
    return data.decode("utf-8", errors="replace")


def _diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{name}", tofile=f"b/{name}"))


# ---------------------------------------------------------------- list_dir

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
MAX_LIST_ENTRIES = 500


def list_dir(root: Path, args: dict) -> str:
    base = resolve_path(root, args.get("path"))
    if not base.is_dir():
        raise ToolError(f"Não é um diretório: '{args.get('path')}'.")
    out: list[str] = []
    if args.get("recursive"):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            d = Path(dirpath)
            for name in dirnames:
                out.append(_rel(root, d / name) + "/")
            for name in sorted(filenames):
                out.append(_rel(root, d / name))
            if len(out) >= MAX_LIST_ENTRIES:
                break
    else:
        for p in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            out.append(_rel(root, p) + ("/" if p.is_dir() else f"  ({p.stat().st_size} B)"))
    if not out:
        return f"(diretório vazio: {_rel(root, base)})"
    note = f"\n(listagem truncada em {MAX_LIST_ENTRIES} entradas)" if len(out) >= MAX_LIST_ENTRIES else ""
    return "\n".join(out[:MAX_LIST_ENTRIES]) + note


# ---------------------------------------------------------------- read_file

MAX_READ_LINES = 2000


def _conteudo(p: Path, usar_ocr: bool = False) -> str:
    """Texto do arquivo. Documento de escritório vira Markdown; o resto é lido como texto.

    Sem este desvio, .pdf/.docx/.xlsx/.pptx batiam em "Arquivo binário" no `_read_text` — os três
    últimos são ZIP, então o teste de byte nulo os pegava logo nos primeiros bytes.
    """
    from . import documentos  # import tardio: o documentos.py é quem depende daqui, não o contrário

    if p.suffix.lower() not in documentos.LEITURA:
        return _read_text(p)
    if not p.is_file():
        raise ToolError(f"Arquivo não encontrado: '{p.name}'. Use list_dir para ver o que existe.")
    tamanho = p.stat().st_size
    if tamanho > config.MAX_DOC_BYTES:
        raise ToolError(f"Documento grande demais ({tamanho // 1024} KB, limite "
                        f"{config.MAX_DOC_BYTES // 1024} KB). Ajuste MAX_DOC_BYTES se precisar.")
    texto = documentos.extrair(p)
    if texto is None and usar_ocr and p.suffix.lower() == ".pdf":
        texto = documentos.extrair_ocr(p)
    if texto is None:
        if p.suffix.lower() == ".pdf":
            # Duas saídas, e a ordem importa: quem enxerga lê a página de verdade pelo
            # preview_document e transcreve melhor que qualquer OCR — layout, tabela, carimbo,
            # coluna torta. O OCR é o que sobra para quem não enxerga, e por isso é opt-in: quando
            # ele rodava sozinho aqui, o modelo com visão recebia o palpite do OCR e nem chegava a
            # olhar o documento.
            raise ToolError(
                f"'{p.name}' não tem texto extraível: ou é um PDF escaneado (páginas são imagem), "
                "ou está protegido por senha, ou corrompido. NÃO desista nem avise o usuário ainda. "
                + ("Se você recebe imagens, chame preview_document neste mesmo caminho e transcreva "
                   "o que vê — é a leitura mais fiel. Se não recebe, ou se a prévia não resolveu, "
                   "chame read_file de novo com ocr=true."
                   if not usar_ocr else
                   "Nem o OCR achou texto aqui: chame preview_document neste mesmo caminho e olhe a "
                   "página. Só se a prévia também falhar é que o arquivo é mesmo ilegível."))
        raise ToolError(
            f"Não consegui extrair texto de '{p.name}'. Pode ser um arquivo protegido por senha ou "
            "corrompido. Diga isso ao usuário em vez de tentar de novo.")
    return texto


MAX_LINE_CHARS = 2000


def _linha(texto: str) -> str:
    if len(texto) <= MAX_LINE_CHARS:
        return texto
    return texto[:MAX_LINE_CHARS] + f"... (linha cortada em {MAX_LINE_CHARS} caracteres)"


def read_file(root: Path, args: dict) -> str:
    p = resolve_leitura(root, args.get("path"))
    lines = _conteudo(p, bool(args.get("ocr"))).splitlines()
    marcar_lido(p)
    start = max(int(args.get("start_line") or 1), 1)
    end = int(args.get("end_line") or len(lines))
    end = min(end, len(lines), start + MAX_READ_LINES - 1)
    if not lines:
        return f"(arquivo vazio: {_rel(root, p)})"
    if start > len(lines):
        raise ToolError(f"start_line {start} passa do fim: o arquivo tem {len(lines)} linhas.")
    body = "\n".join(f"{i:>5}\t{_linha(lines[i - 1])}" for i in range(start, end + 1))
    if end < len(lines):
        body += f"\n(Mostrando linhas {start}-{end} de {len(lines)}. Use start_line={end + 1} para continuar.)"
    else:
        body += f"\n(Fim do arquivo - {len(lines)} linhas)"
    return body


# ---------------------------------------------------------------- write_file

def _check_size(content: str) -> None:
    n = len(content.encode("utf-8"))
    if n > config.MAX_FILE_BYTES:
        raise ToolError(f"Conteúdo grande demais ({n} bytes, limite {config.MAX_FILE_BYTES}).")


def write_file(root: Path, args: dict) -> str:
    p = resolve_path(root, args.get("path"))
    _nao_gerado(root, p)
    content = args["content"]
    _check_size(content)
    if p.is_dir():
        raise ToolError(f"'{args.get('path')}' é um diretório.")
    _observado(root, p)
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="")
    marcar_lido(p)  # quem escreveu sabe o que está lá
    lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    return f"Arquivo {'sobrescrito' if existed else 'criado'}: {_rel(root, p)} ({lines} linhas)"


def write_preview(root: Path, args: dict) -> dict:
    p = resolve_path(root, args.get("path"))
    content = args.get("content", "")
    _check_size(content)
    _observado(root, p)
    if p.is_file():
        return {"kind": "diff", "path": _rel(root, p), "text": _diff(_read_text(p), content, _rel(root, p))}
    return {"kind": "new", "path": _rel(root, p), "text": content}


# ---------------------------------------------------------------- edit_file

def _edits(args: dict) -> list[dict]:
    """As edições desta chamada: a lista `edits`, ou o par old_str/new_str do topo (uma edição)."""
    raw = args.get("edits")
    if isinstance(raw, list) and raw:
        return [e for e in raw if isinstance(e, dict)]
    return [{"old_str": args.get("old_str"), "new_str": args.get("new_str"),
             "replace_all": args.get("replace_all")}]


def _one_edit(text: str, edit: dict, onde: str) -> tuple[str, int]:
    old_str, new_str = edit.get("old_str"), edit.get("new_str")
    if not old_str:
        raise ToolError(f"{onde}old_str vazio. Para criar ou reescrever o arquivo inteiro use write_file.")
    count = text.count(old_str)
    if count == 0:
        raise ToolError(
            f"{onde}old_str não encontrado no arquivo. Leia o arquivo com read_file e copie o trecho "
            "exatamente (espaços, indentação e quebras de linha), sem os números de linha.")
    if count > 1 and not edit.get("replace_all"):
        raise ToolError(
            f"{onde}old_str aparece {count} vezes. Inclua mais linhas de contexto para que o trecho seja único, "
            "ou mande replace_all=true para trocar todas.")
    trocas = count if edit.get("replace_all") else 1
    return text.replace(old_str, new_str or "", trocas), trocas


def _apply_edit(root: Path, args: dict) -> tuple[Path, str, str, int]:
    """Aplica as edições em sequência, tudo ou nada: se uma não bater, nada é escrito."""
    p = resolve_path(root, args.get("path"))
    _observado(root, p)
    edits = _edits(args)
    old = novo = _read_text(p)
    trocas = 0
    for i, edit in enumerate(edits, 1):
        onde = f"Edição {i}: " if len(edits) > 1 else ""
        novo, n = _one_edit(novo, edit, onde)
        trocas += n
    return p, old, novo, trocas


def edit_file(root: Path, args: dict) -> str:
    _nao_gerado(root, resolve_path(root, args.get("path")))
    p, _, new, trocas = _apply_edit(root, args)
    _check_size(new)
    p.write_text(new, encoding="utf-8", newline="")
    marcar_lido(p)
    detalhe = f" ({trocas} trechos)" if trocas > 1 else ""
    return f"Arquivo editado: {_rel(root, p)}{detalhe}"


def edit_preview(root: Path, args: dict) -> dict:
    p, old, new, _ = _apply_edit(root, args)
    return {"kind": "diff", "path": _rel(root, p), "text": _diff(old, new, _rel(root, p))}


# ---------------------------------------------------------------- registro

def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


register(Tool(
    "list_dir", "Lista arquivos e pastas de um diretório da pasta de trabalho. Para a visão geral de um "
    "projeto, use tree.",
    _obj({"path": {"type": "string", "description": "Diretório relativo à pasta de trabalho. Padrão: '.'"},
          "recursive": {"type": "boolean", "description": "Listar subpastas também. Padrão: false"}}, []),
    list_dir))
register(Tool(
    "read_file",
    "Lê um arquivo e devolve o conteúdo com números de linha. Entende texto e também documentos: "
    ".pdf, .docx, .xlsx, .pptx e .csv saem convertidos em Markdown (tabela vira tabela).",
    _obj({"path": {"type": "string"},
          "start_line": {"type": "integer", "description": "Primeira linha (1-based), opcional"},
          "end_line": {"type": "integer", "description": "Última linha (inclusiva), opcional"},
          "ocr": {"type": "boolean",
                  "description": "Só para PDF escaneado, e só depois de a leitura normal voltar "
                                 "vazia: passa o OCR do sistema nas páginas. Se você recebe "
                                 "imagens, prefira preview_document — enxergar a página é mais "
                                 "fiel que o OCR."}}, ["path"]),
    read_file))
register(Tool(
    "write_file", "Cria ou sobrescreve um arquivo com o conteúdo completo. Cria diretórios intermediários. "
    "Arquivo que já existe precisa ter sido lido antes com read_file; para mudança pontual prefira edit_file.",
    _obj({"path": {"type": "string"}, "content": {"type": "string", "description": "Conteúdo completo do arquivo"}},
         ["path", "content"]),
    write_file, mutating=True, preview=write_preview))
register(Tool(
    "edit_file",
    "Substitui trechos exatos de um arquivo. Por padrão old_str precisa aparecer uma vez só; use "
    "replace_all para trocar todas as ocorrências, e `edits` para várias trocas no mesmo arquivo numa "
    "chamada só (aplicadas em ordem; se uma não bater, nada é escrito). Leia o arquivo com read_file "
    "antes, a não ser que você mesmo o tenha criado ou editado agora há pouco.",
    _obj({"path": {"type": "string"},
          "old_str": {"type": "string", "description": "Trecho exato existente (sem números de linha)"},
          "new_str": {"type": "string", "description": "Texto que substitui old_str"},
          "replace_all": {"type": "boolean",
                          "description": "Trocar todas as ocorrências em vez de exigir trecho único"},
          "edits": {"type": "array",
                    "description": "Várias edições no mesmo arquivo, em vez de old_str/new_str soltos",
                    "items": _obj({"old_str": {"type": "string"}, "new_str": {"type": "string"},
                                   "replace_all": {"type": "boolean"}}, ["old_str", "new_str"])}},
         ["path"]),
    edit_file, mutating=True, preview=edit_preview))

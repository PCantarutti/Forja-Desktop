"""Ferramenta lsp: navegação precisa de código por language server (porte do lsp/tool-lsp do DeepSeek Harness).

grep acha texto; o language server acha SÍMBOLO — a definição de verdade, todas as referências, o tipo
no hover. Vale quando o texto é ambíguo (nome repetido, método sobrecarregado) ou antes de uma mudança
que precisa de todas as referências. Só aparece com um servidor instalado no PATH:

    Python      pyright-langserver --stdio  (npm i -g pyright)  ou  pylsp
    TS/JS       typescript-language-server --stdio  (npm i -g typescript-language-server typescript)

Um processo por (pasta, linguagem), reaproveitado entre chamadas; JSON-RPC por stdin/stdout.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import native
from .tools import Tool, ToolError, _obj, _rel, register, resolve_path

SERVIDORES = {  # linguagem -> [(executável, argumentos)], o primeiro que existir
    "python": [("pyright-langserver", ["--stdio"]), ("basedpyright-langserver", ["--stdio"]), ("pylsp", [])],
    "typescript": [("typescript-language-server", ["--stdio"])],
}
EXTENSOES = {".py": "python", ".pyi": "python", ".ts": "typescript", ".tsx": "typescript",
             ".js": "typescript", ".jsx": "typescript", ".mjs": "typescript", ".cjs": "typescript"}
LANGUAGE_ID = {".py": "python", ".pyi": "python", ".ts": "typescript", ".tsx": "typescriptreact",
               ".js": "javascript", ".jsx": "javascriptreact", ".mjs": "javascript", ".cjs": "javascript"}
ESPERA = 45.0   # primeira consulta pode indexar o projeto inteiro
MAX_ITENS = 60


_ACHADOS: dict[str, tuple[float, list[str] | None]] = {}
CACHE_PATH = 60.0  # shutil.which varre o PATH inteiro (~40 ms no Windows) e a lista de ferramentas é montada várias vezes por passo


def _comando(lingua: str) -> list[str] | None:
    agora = time.monotonic()
    if (achado := _ACHADOS.get(lingua)) and agora - achado[0] < CACHE_PATH:
        return achado[1]
    cmd = next(([c, *args] for exe, args in SERVIDORES.get(lingua, []) if (c := shutil.which(exe))), None)
    _ACHADOS[lingua] = (agora, cmd)
    return cmd


def _uri(p: Path) -> str:
    return p.resolve().as_uri()


def _caminho(uri: str) -> Path:
    from urllib.parse import unquote, urlparse

    bruto = unquote(urlparse(uri).path)
    return Path(bruto[1:] if len(bruto) > 2 and bruto[2] == ":" else bruto)  # /C:/x -> C:/x


class Cliente:
    def __init__(self, cmd: list[str], raiz: Path):
        self.proc = subprocess.Popen(cmd, cwd=raiz, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, **native.popen_kwargs())
        self.lock = threading.Lock()
        self.respostas: dict[int, dict] = {}
        self.chegou = threading.Condition()
        self.proximo = 0
        self.abertos: dict[str, int] = {}  # uri -> versão enviada
        threading.Thread(target=self._leitor, daemon=True).start()
        self.pedir("initialize", {"processId": None, "rootUri": _uri(raiz),
                                  "capabilities": {"textDocument": {"hover": {"contentFormat": ["plaintext", "markdown"]}}},
                                  "workspaceFolders": [{"uri": _uri(raiz), "name": raiz.name}]})
        self.avisar("initialized", {})

    def _envia(self, msg: dict) -> None:
        corpo = json.dumps(msg).encode("utf-8")
        with self.lock:
            assert self.proc.stdin
            self.proc.stdin.write(f"Content-Length: {len(corpo)}\r\n\r\n".encode() + corpo)
            self.proc.stdin.flush()

    def _leitor(self) -> None:
        out = self.proc.stdout
        assert out
        while True:
            tamanho = 0
            while (linha := out.readline()) not in (b"\r\n", b"\n"):
                if not linha:
                    with self.chegou:
                        self.chegou.notify_all()
                    return
                if linha.lower().startswith(b"content-length:"):
                    tamanho = int(linha.split(b":")[1])
            msg = json.loads(out.read(tamanho))
            if "id" in msg and "method" in msg:  # pedido do servidor (ex.: workspace/configuration): responde vazio
                self._envia({"jsonrpc": "2.0", "id": msg["id"], "result": None})
            elif "id" in msg:
                with self.chegou:
                    self.respostas[msg["id"]] = msg
                    self.chegou.notify_all()

    def pedir(self, metodo: str, params: dict):
        with self.lock:
            self.proximo += 1
            n = self.proximo
        self._envia({"jsonrpc": "2.0", "id": n, "method": metodo, "params": params})
        with self.chegou:
            if not self.chegou.wait_for(lambda: n in self.respostas or self.proc.poll() is not None, ESPERA):
                raise ToolError(f"O language server não respondeu a {metodo} em {ESPERA:.0f}s (pode estar indexando; "
                                "tente de novo ou use grep).")
            if n not in self.respostas:
                raise ToolError("O language server encerrou. Tente de novo.")
            msg = self.respostas.pop(n)
        if "error" in msg:
            raise ToolError(f"Language server: {msg['error'].get('message')}")
        return msg.get("result")

    def avisar(self, metodo: str, params: dict) -> None:
        self._envia({"jsonrpc": "2.0", "method": metodo, "params": params})

    def abrir(self, p: Path) -> str:
        """Manda o conteúdo atual do arquivo (didOpen/didChange): o servidor responde sobre o que está no disco."""
        uri = _uri(p)
        texto = p.read_text(encoding="utf-8", errors="replace")
        if uri not in self.abertos:
            self.abertos[uri] = 1
            self.avisar("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": LANGUAGE_ID.get(p.suffix.lower(), "plaintext"), "version": 1, "text": texto}})
        else:
            self.abertos[uri] += 1
            self.avisar("textDocument/didChange", {"textDocument": {"uri": uri, "version": self.abertos[uri]},
                                                   "contentChanges": [{"text": texto}]})
        return uri

    def fechar(self) -> None:
        native.kill_tree(self.proc)


CLIENTES: dict[tuple[str, str], Cliente] = {}
_clientes_lock = threading.Lock()


def _cliente(raiz: Path, lingua: str) -> Cliente:
    chave = (str(raiz.resolve()), lingua)
    with _clientes_lock:
        c = CLIENTES.get(chave)
        if c and c.proc.poll() is None:
            return c
        cmd = _comando(lingua)
        if not cmd:
            raise ToolError(f"Nenhum language server de {lingua} instalado. " + (
                "Python: npm i -g pyright (ou pip install python-lsp-server)." if lingua == "python" else
                "TS/JS: npm i -g typescript-language-server typescript."))
        CLIENTES[chave] = c = Cliente(cmd, raiz)
        return c


def fechar_todos() -> None:
    with _clientes_lock:
        for c in CLIENTES.values():
            c.fechar()
        CLIENTES.clear()


def _local(root: Path, loc: dict) -> str:
    alvo = _caminho(loc.get("uri") or loc.get("targetUri") or "")
    faixa = loc.get("range") or loc.get("targetSelectionRange") or {}
    linha = faixa.get("start", {}).get("line", 0)
    try:
        nome = _rel(root, alvo.resolve()) if alvo.resolve().is_relative_to(root.resolve()) else str(alvo)
        trecho = alvo.read_text(encoding="utf-8", errors="replace").splitlines()[linha].strip()[:160]
    except (OSError, IndexError, ValueError):
        nome, trecho = str(alvo), ""
    return f"{nome}:{linha + 1}:{faixa.get('start', {}).get('character', 0) + 1}  {trecho}"


def _simbolos(itens: list, nivel: int = 0) -> list[str]:
    out = []
    for s in itens or []:
        linha = (s.get("selectionRange") or s.get("range") or (s.get("location") or {}).get("range") or {}).get(
            "start", {}).get("line", 0)
        out.append(f"{'  ' * nivel}{s.get('name')} (linha {linha + 1})")
        out += _simbolos(s.get("children") or [], nivel + 1)
    return out


def lsp(root: Path, args: dict) -> str:
    p = resolve_path(root, args.get("path"))
    if not p.is_file():
        raise ToolError(f"Arquivo não encontrado: {args.get('path')}")
    lingua = EXTENSOES.get(p.suffix.lower())
    if not lingua:
        raise ToolError(f"Sem language server para '{p.suffix}'. Suportados: Python, TypeScript/JavaScript.")
    op = str(args.get("operation") or "")
    c = _cliente(root, lingua)
    uri = c.abrir(p)
    if op == "documentSymbol":
        return "\n".join(_simbolos(c.pedir("textDocument/documentSymbol", {"textDocument": {"uri": uri}}))
                         [:MAX_ITENS * 2]) or "(nenhum símbolo)"
    if not args.get("line") or not args.get("character"):
        raise ToolError(f"{op} precisa de line e character (1-based, no símbolo).")
    pos = {"line": int(args["line"]) - 1, "character": int(args["character"]) - 1}
    base = {"textDocument": {"uri": uri}, "position": pos}
    if op == "hover":
        r = c.pedir("textDocument/hover", base) or {}
        cont = r.get("contents")
        texto = cont.get("value") if isinstance(cont, dict) else "\n".join(
            x.get("value", "") if isinstance(x, dict) else str(x) for x in cont) if isinstance(cont, list) else str(cont or "")
        return texto.strip()[:4000] or "(nada no hover: a posição pode não estar sobre um símbolo)"
    metodo = {"definition": "textDocument/definition", "references": "textDocument/references",
              "implementation": "textDocument/implementation"}.get(op)
    if not metodo:
        raise ToolError("operation deve ser definition, references, implementation, hover ou documentSymbol.")
    if op == "references":
        base["context"] = {"includeDeclaration": True}
    r = c.pedir(metodo, base) or []
    locais = [r] if isinstance(r, dict) else r
    linhas = [_local(root, loc) for loc in locais[:MAX_ITENS]]
    if len(locais) > MAX_ITENS:
        linhas.append(f"(… {len(locais) - MAX_ITENS} a mais)")
    return "\n".join(linhas) or "(nada encontrado: a posição pode não estar sobre um símbolo)"


def _disponivel() -> bool:
    return any(_comando(lang) for lang in SERVIDORES)


register(Tool(
    "lsp",
    "Navegação precisa de código pelo language server: definition (onde o símbolo é definido), references "
    "(todos os usos, com a declaração), implementation, hover (tipo/documentação) e documentSymbol (estrutura "
    "do arquivo; sem servidor, o ast outline faz isso). Use grep/read_file para navegar no dia a dia; use lsp quando o texto é ambíguo ou antes de "
    "uma mudança que precisa achar todas as referências. line e character são 1-based, sobre o símbolo.",
    _obj({"operation": {"type": "string",
                        "enum": ["definition", "references", "implementation", "hover", "documentSymbol"]},
          "path": {"type": "string", "description": "Arquivo onde está o símbolo"},
          "line": {"type": "integer", "description": "Linha (1-based)"},
          "character": {"type": "integer", "description": "Coluna (1-based) sobre o símbolo"}},
         ["operation", "path"]),
    lsp, available=_disponivel, timeout=None))

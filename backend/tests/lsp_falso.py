"""Language server de mentira para os testes do app/lsp.py: responde initialize, definition, references,
hover e documentSymbol com posições fixas, e pede workspace/configuration uma vez (como o pyright)."""
import json
import sys

ent, sai = sys.stdin.buffer, sys.stdout.buffer


def manda(msg):
    corpo = json.dumps(msg).encode()
    sai.write(f"Content-Length: {len(corpo)}\r\n\r\n".encode() + corpo)
    sai.flush()


uri_aberto = None
while True:
    tamanho = 0
    while (linha := ent.readline()) not in (b"\r\n", b"\n"):
        if not linha:
            sys.exit(0)
        if linha.lower().startswith(b"content-length:"):
            tamanho = int(linha.split(b":")[1])
    msg = json.loads(ent.read(tamanho))
    metodo, n = msg.get("method"), msg.get("id")
    if metodo == "initialize":
        manda({"jsonrpc": "2.0", "id": n, "result": {"capabilities": {}}})
        manda({"jsonrpc": "2.0", "id": 999, "method": "workspace/configuration", "params": {"items": []}})
    elif metodo == "textDocument/didOpen":
        uri_aberto = msg["params"]["textDocument"]["uri"]
    elif metodo == "textDocument/definition":
        manda({"jsonrpc": "2.0", "id": n, "result": [{"uri": uri_aberto, "range": {
            "start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 8}}}]})
    elif metodo == "textDocument/references":
        manda({"jsonrpc": "2.0", "id": n, "result": [
            {"uri": uri_aberto, "range": {"start": {"line": ln, "character": 0}, "end": {"line": ln, "character": 1}}}
            for ln in (0, 3)]})
    elif metodo == "textDocument/hover":
        manda({"jsonrpc": "2.0", "id": n, "result": {"contents": {"kind": "markdown", "value": "def soma(a, b) -> int"}}})
    elif metodo == "textDocument/documentSymbol":
        manda({"jsonrpc": "2.0", "id": n, "result": [{"name": "soma", "range": {"start": {"line": 0, "character": 0}},
                                                      "children": [{"name": "interno", "range": {"start": {"line": 1}}}]}]})
    elif n is not None and metodo:
        manda({"jsonrpc": "2.0", "id": n, "result": None})

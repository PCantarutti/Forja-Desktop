"""Hook do Claude Code → Forja (E17): repassa o evento (UserPromptSubmit, Stop, PostToolUse) para a
conversa-espelho do projeto no Forja.

Uso (gravado pelo Forja em .claude/settings.local.json): python forja_hook.py <pasta de dados do Forja>
Lê o JSON do evento na entrada padrão e o endereço/token de <pasta>/mcp_endpoint.json. Nunca falha nem
segura o Claude: Forja fechado, token velho ou rede lenta viram silêncio (exit 0).
"""
import json
import sys
import urllib.request
from pathlib import Path


def main() -> None:
    try:
        dados = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        fim = json.loads((Path(sys.argv[1]) / "mcp_endpoint.json").read_text(encoding="utf-8"))
        req = urllib.request.Request(fim["url"] + "/mcp/hook", data=json.dumps(dados).encode("utf-8"),
                                     headers={"content-type": "application/json", "x-forja-token": fim["token"]})
        urllib.request.urlopen(req, timeout=4).read()
    except Exception:
        pass


if __name__ == "__main__":
    main()

"""Comandos `/` do campo de mensagem: ações do Forja e skills do projeto (`.forja/skills/*.md`).

Uma skill é um arquivo markdown; o cabeçalho opcional `---\\ndescription: ...\\n---` vira a descrição
no menu e o resto é o prompt enviado ao agente, com `$ARGUMENTS` substituído pelo que vier depois
do comando. Ações (`kind: action`) são executadas pela própria interface (compactar, commit, PR).
"""
from __future__ import annotations

import re
from pathlib import Path

DIR = ".forja/skills"
MAX_SKILLS = 50

BUILTIN = [
    {"name": "compactar", "kind": "action", "action": "compact",
     "description": "Resume o histórico antigo desta conversa agora (libera contexto)"},
    {"name": "commit", "kind": "action", "action": "commit",
     "description": "Gera a mensagem com o modelo e faz commit das alterações da pasta"},
    {"name": "pr", "kind": "action", "action": "pr",
     "description": "Faz push e abre um pull request com o GitHub CLI (gh)"},
    {"name": "alteracoes", "kind": "action", "action": "changes",
     "description": "Abre a aba Alterações (arquivos que o agente mudou e git)"},
    {"name": "revisar", "kind": "prompt",
     "description": "Revisa as alterações desta conversa em busca de bugs e melhorias",
     "prompt": "Revise as alterações desta conversa. Comece por `git status` e `git diff` para saber o que mudou. "
               "Havendo subagente disponível, divida o diff por arquivo ou área e delegue os pedaços NA MESMA "
               "resposta — eles rodam em paralelo: delegate_task(agent='revisor') se existir essa persona no "
               "projeto, senão level='capaz'. Peça a cada um os problemas reais com arquivo e linha, sem estilo. "
               "Junte tudo num relatório único, em ordem de gravidade e sem repetir. Não altere nada. $ARGUMENTS"},
    {"name": "testar", "kind": "prompt",
     "description": "Descobre e roda os testes do projeto, corrigindo falhas",
     "prompt": "Descubra como rodar os testes deste projeto (package.json, pytest, etc.), rode-os e corrija as falhas "
               "que forem causadas por alterações desta conversa. Relate o resultado final. $ARGUMENTS"},
    {"name": "explicar", "kind": "prompt",
     "description": "Explica a estrutura do projeto da pasta da conversa",
     "prompt": "Explore a pasta da conversa (list_dir, read_file) e explique em tópicos: o que o projeto faz, "
               "estrutura de pastas, como rodar e onde ficam as partes principais. $ARGUMENTS"},
]

FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def frontmatter(text: str) -> tuple[dict, str]:
    """(campos do cabeçalho `---`, resto do arquivo). Usado pelas skills e pelas personas de subagente."""
    campos: dict[str, str] = {}
    m = FRONT.match(text)
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip():
                campos[k.strip().lower()] = v.strip().strip('"').strip("'")
        text = text[m.end():]
    return campos, text


def _parse(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    campos, text = frontmatter(text)
    description = campos.get("description", "")
    prompt = text.strip()
    if not prompt:
        return None
    return {"name": path.stem, "kind": "prompt", "description": description or prompt.splitlines()[0][:80],
            "prompt": prompt, "source": f"{DIR}/{path.name}"}


def list_for(root: Path) -> list[dict]:
    """Ações do Forja + skills da pasta da conversa (nome do arquivo = comando)."""
    out = list(BUILTIN)
    folder = root / DIR
    if folder.is_dir():
        for p in sorted(folder.glob("*.md"))[:MAX_SKILLS]:
            s = _parse(p)
            if s:
                out = [x for x in out if x["name"] != s["name"]] + [s]  # skill do projeto sobrepõe a padrão
    return out


def expand(prompt: str, arguments: str) -> str:
    return prompt.replace("$ARGUMENTS", arguments.strip()).strip()

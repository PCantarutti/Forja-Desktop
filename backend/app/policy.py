"""Permissões: modos de aprovação (como no Claude) + regras de auto-aprovação.

Modos (escolhidos no campo de mensagem, `Shift+Tab` alterna):

- `manual`   — pergunta antes de qualquer alteração.
- `edits`    — aceita edições de arquivo; o resto (shell, navegador, MCP) pergunta.
- `auto`     — o Forja decide: edições e comandos reconhecidamente seguros passam, o resto pergunta.
- `plan`     — só leitura. O agente investiga e propõe um plano; nada é alterado.
- `bypass`   — aceita tudo, inclusive shell e JavaScript na página. Só comando destrutivo
               (apagar, formatar, desligar, sudo, force push...) ainda pede confirmação.

As regras de `Configurações › Permissões` (globs) continuam valendo em todos os modos menos `plan`,
e a razão pela qual algo foi liberado sempre aparece no bloco da ferramenta: nada é aprovado em
silêncio.
"""
from __future__ import annotations

import re
import shlex
from fnmatch import fnmatch

from . import config

MODES = ("auto", "manual", "edits", "plan", "bypass")
FILE_EDITS = {"write_file", "edit_file"}
# Ações de baixo risco no modo Automático: mexem na pasta de trabalho ou na página, não no sistema.
AUTO_TOOLS = FILE_EDITS | {"browser_click", "browser_type", "browser_upload"}

# Comandos de leitura/teste que o modo Automático libera (primeira palavra, ou duas para subcomandos).
SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "pwd", "echo", "grep", "rg", "find", "tree", "file", "stat",
    "which", "whoami", "date", "env", "printenv", "du", "df", "diff", "sort", "uniq", "sed", "awk",
    "pytest", "ruff", "eslint", "tsc", "mypy", "black", "jq",
}
SAFE_SUBCOMMANDS = {
    "git": {"status", "diff", "log", "show", "branch", "remote", "blame", "ls-files", "rev-parse"},
    "npm": {"test", "run", "ls", "list", "view", "outdated"},
    "pnpm": {"test", "run", "list", "outdated"},
    "yarn": {"test", "run", "list"},
    "pip": {"list", "show", "freeze", "check"},
    "uv": {"run", "pip", "tree"},
    "docker": {"ps", "images", "logs", "compose"},
    "python": set(), "python3": set(), "node": set(),  # tratados abaixo (só -m pytest / --version etc.)
}
SAFE_PYTHON_ARGS = {"-m", "--version", "-V", "-c"}
# Cmdlets e utilitários do Windows que só leem (o run_command roda no PowerShell). Minúsculos: o
# PowerShell não diferencia caixa. Escrita (Set-Content, Out-File, Remove-Item...) fica de fora.
SAFE_WINDOWS = {
    "get-date", "get-content", "gc", "type", "test-path", "select-string", "sls", "findstr",
    "get-childitem", "gci", "dir", "get-location", "gl", "get-item", "gi", "get-itemproperty",
    "select-object", "select", "where-object", "where", "measure-object", "measure", "sort-object",
    "format-table", "ft", "format-list", "fl", "get-command", "gcm", "resolve-path", "split-path",
    "join-path", "get-filehash", "write-output", "write-host",
}
DANGEROUS = re.compile(r"(^|\s)(rm|rmdir|mv|dd|mkfs|chmod|chown|sudo|su|kill|pkill|shutdown|reboot|"
                       r"curl|wget|nc|ssh|scp|apt|apt-get|yum|brew|systemctl)(\s|$)")
REDIRECT = re.compile(r"[>]|(^|\s)tee(\s|$)")
# Redirecionamento que não grava arquivo: juntar um fluxo no outro (2>&1) ou jogar no nulo.
SEM_ARQUIVO = re.compile(r"\d?>&\d|\d?>\s*(/dev/null|nul|\$null)(?=$|[\s;&|)])", re.I)
GIT_CONFIG_LEITURA = {"--get", "--get-all", "--list", "-l", "--show-origin"}
# Comandos que destroem dados ou mexem no sistema: nem o modo Ignorar permissões deixa passar calado.
# Casa no início de qualquer trecho (depois de ; && || | ( ` $( ), então `$(rm -rf x)` também é pego.
DESTRUCTIVE = re.compile(
    r"(?:^|[\s;&|(`]|\$\()\s*"
    r"(rm|rmdir|del|erase|rd|dd|shred|srm|wipe|mkfs\S*|fdisk|diskpart|format|"
    r"shutdown|reboot|halt|poweroff|stop-computer|restart-computer|"
    r"sudo|su|doas|runas|"
    r"kill|pkill|killall|taskkill|"
    r"chmod|chown|chattr|icacls|takeown|attrib|"
    r"truncate|mkswap|swapoff|mount|umount|"
    r"useradd|userdel|usermod|passwd|"
    r"systemctl|launchctl|bcdedit|regedit|reg|vssadmin|cipher|"
    r"remove-item|clear-content|set-executionpolicy|uninstall-\S+)"
    r"(?=$|[\s;&|)])", re.I)
DESTRUCTIVE_EXTRA = re.compile(
    r"\bgit\s+(clean|filter-branch)\b|\bgit\s+reset\s+--hard\b|\bgit\s+push\b[^;&|]*?\s-{1,2}f(orce)?\b|"
    r"\bdocker\s+(rm|rmi|kill)\b|\bdocker\s+(\S+\s+)?prune\b|"
    r"\b(npm|pnpm|yarn)\s+publish\b|\bkubectl\s+delete\b|\bterraform\s+destroy\b|"
    r"\bdrop\s+(table|database|schema)\b", re.I)
SPLIT = re.compile(r"&&|\|\||;|\|")


def partes(command: str) -> list[str]:
    """Os comandos de uma linha, separados por ; && || | FORA de aspas. Dividir pelo regex puro
    cortava `python -c "import a; a.b()"` no meio das aspas, e o trecho sem fechar aspas virava
    "comando desconhecido" — pedia aprovação no modo Automático para algo que é um comando só."""
    out, atual, aspas, i = [], "", "", 0
    while i < len(command):
        ch = command[i]
        if aspas:
            aspas = "" if ch == aspas else aspas
        elif ch in "\"'":
            aspas = ch
        elif m := SPLIT.match(command, i):
            out.append(atual)
            atual, i = "", m.end()
            continue
        atual += ch
        i += 1
    return [*out, atual]


def chained(command: str) -> bool:
    """Mais de um comando na mesma string: separador, subshell, crase ou quebra de linha.

    Uma regra de auto-aprovação vale para UM comando, não para o que vier grudado nele:
    o glob casa prefixo, então `pytest*` sozinho liberaria `pytest -q; Remove-Item -Recurse C:/`.
    """
    return (len(command.splitlines()) > 1 or len(partes(command)) > 1
            or "$(" in command or "`" in command)


def destructive_command(command: str) -> bool:
    """True se o comando apaga dados, mexe no sistema ou publica algo: pergunta mesmo no bypass."""
    c = command or ""
    return bool(DESTRUCTIVE.search(c) or DESTRUCTIVE_EXTRA.search(c))


def destructive_args(args: dict) -> bool:
    """Só ferramentas de shell têm `command`; sem ele, nada é considerado destrutivo."""
    return destructive_command(str((args or {}).get("command") or ""))


def safe_command(command: str) -> bool:
    """True se TODOS os trechos do comando forem leitura/teste conhecidos."""
    command = (command or "").strip()
    if not command or REDIRECT.search(SEM_ARQUIVO.sub(" ", command)) or DANGEROUS.search(command) or "$(" in command or "`" in command:
        return False
    for part in partes(command):
        try:
            # posix=False: no Windows a barra invertida é separador de pasta, não escape
            words = [w.strip("\"'") for w in shlex.split(part, posix=False)]
        except ValueError:
            return False
        if not words:
            return False
        base = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
        if base in SAFE_COMMANDS or base in SAFE_WINDOWS:
            continue
        if len(words) == 2 and words[1] in ("--version", "-V"):  # só imprime a versão
            continue
        if base in ("python", "python3", "node"):
            if len(words) > 1 and words[1] in SAFE_PYTHON_ARGS and (len(words) < 3 or words[2] in
                                                                    ("pytest", "unittest", "pip", "json.tool")):
                continue
            return False
        # `git config chave` e `git config --get/--list` leem; `git config chave valor` grava.
        if base == "git" and words[1:2] == ["config"] and (
                len(words) == 3 and not words[2].startswith("-") or
                len(words) >= 3 and words[2] in GIT_CONFIG_LEITURA and len(words) <= 4):
            continue
        subs = SAFE_SUBCOMMANDS.get(base)
        if subs and len(words) > 1 and words[1] in subs:
            continue
        return False
    return True


WILDCARD = re.compile("[*?[]")
# Ferramentas cujo `command` é um comando de shell: regras, leitura segura e destrutivo valem igual.
SHELLS = ("run_command", "terminal_send")


def _rule(name: str, args: dict) -> tuple[str, bool] | None:
    """(motivo, cobre destrutivo?) da regra de Permissões que libera esta chamada.

    Só uma regra de comando **exata** cobre um comando destrutivo: `rm -rf build` escrito à mão é
    escolha consciente, `git push*` casando com `git push --force` é acidente. Regra de ferramenta
    (`run_command`, `browser_*`) nunca cobre — ela vale para a ferramenta toda, não para o comando.
    """
    for pattern in config.AUTO_APPROVE_TOOLS:
        if fnmatch(name, pattern):
            return f"ferramenta {pattern}", False
    if name in SHELLS:
        command = str(args.get("command") or "").strip()
        if chained(command):  # regra libera um comando, não o que vier grudado nele
            return None
        for pattern in config.AUTO_APPROVE_COMMANDS:
            if fnmatch(command, pattern):
                return f"comando {pattern}", not WILDCARD.search(pattern)
    return None


def auto_rule(name: str, args: dict) -> str | None:
    """Regra de Configurações › Permissões que libera esta chamada (ou None)."""
    achada = _rule(name, args)
    return achada[0] if achada else None


def decide(tool, args: dict, mode: str) -> tuple[bool, str | None]:
    """(precisa de aprovação?, motivo da liberação) para uma ferramenta que altera algo."""
    if not tool.mutating:
        return False, None
    if mode == "plan":  # nem deveria chegar aqui: no modo Plano essas ferramentas não são enviadas
        return True, None
    achada = _rule(tool.name, args)
    # Apagar/formatar/desligar/sudo pergunta em qualquer modo — só uma regra exata dispensa o card.
    if destructive_args(args):
        return (False, achada[0]) if achada and achada[1] else (True, None)
    if achada:
        return False, achada[0]
    if mode == "bypass":
        return False, "modo Ignorar permissões"
    # Comando só de leitura não para o agente no modo Automático. `ls`, `git status`, `grep`,
    # `pytest` não alteram nada, e parar em cada um deles é o que fazia uma execução longa ficar
    # esperando clique — uma sessão de teste passou 40 minutos travada num `python -m http.server`.
    # `safe_command` exige que TODOS os trechos encadeados sejam leitura conhecida, e recusa
    # redirecionamento e substituição de comando; escrita, rede e instalação seguem perguntando.
    if mode == "auto" and tool.name in SHELLS and safe_command(str(args.get("command") or "")):
        return False, "modo Automático: comando só de leitura"
    if tool.always_ask:  # shell e browser_eval só passam por regra explícita ou bypass
        return True, None
    if mode == "manual":
        return True, None
    if mode == "edits":
        return tool.name not in FILE_EDITS, ("modo Aceitar edições" if tool.name in FILE_EDITS else None)
    if mode == "auto":
        if tool.name in AUTO_TOOLS:
            return False, "modo Automático: alteração na pasta de trabalho"
        return True, None
    return True, None


def suggest(name: str, args: dict) -> str:
    """Sugestão de regra para o botão 'sempre permitir' do card de aprovação."""
    if name not in SHELLS:
        return name
    first = str(args.get("command") or "").strip().split()
    if not first:
        return name
    base = first[0]
    # "git status" é mais útil como regra do que "git *"
    if base in ("git", "npm", "pnpm", "yarn", "docker", "uv", "pip", "python", "node") and len(first) > 1:
        return f"{base} {first[1]}*"
    return f"{base}*"

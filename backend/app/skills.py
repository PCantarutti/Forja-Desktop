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
    {"name": "gerar-imagens", "kind": "prompt",
     "description": "Deixa slots de imagem no código e um botão para gerar todas na tela Imagens",
     "prompt": "Enquanto cria ou edita o que foi pedido, cada imagem que o resultado precisar (foto, ilustração, "
               "banner, ícone grande) vira um SLOT em vez de imagem de banco ou placeholder externo:\n"
               "1. Dê a cada slot um nome em minúsculas com hífens e um código de 4 dígitos, único no projeto: "
               "`vela-3141`, `hero-velas-8027`.\n"
               "2. No código, aponte direto para o arquivo final PNG numa pasta de imagens do projeto, ex.: "
               "`<img src=\"img/vela-3141.png\" alt=\"...\">` ou `url(img/hero-velas-8027.png)`. O arquivo ainda não "
               "existe; vai existir depois, com esse nome e nesse lugar.\n"
               "3. No fim, chame `imagens_pendentes` UMA vez com todos os slots: `caminho` relativo à pasta da "
               "conversa (o mesmo arquivo do código, visto da raiz), `prompt` em inglês descrevendo só a imagem "
               "(assunto, composição, luz, material), `largura`/`altura` na proporção de onde ela aparece "
               "(banner 16:9 ≈ 1344×768, card quadrado 1024×1024, retrato 768×1024) e um `estilo` comum a todas, "
               "para o conjunto parecer do mesmo site.\n"
               "4. A ferramenta confere o código contra os slots: se ela apontar um problema, corrija no mesmo "
               "turno. Até a imagem sair, cada caminho tem um PNG provisório com o nome do slot.\n"
               "5. Quando chegar o aviso \"Imagens do site geradas\" (ou de troca/otimização), confira a página no "
               "navegador, se tiver a ferramenta, e ajuste o layout (recorte, proporção, contraste do texto). Se o "
               "aviso disser que as imagens viraram .webp, aponte para o .webp daí em diante.\n"
               "Não gere as imagens você mesmo e não chame image_generate: o usuário gera a fila pela tela "
               "Imagens a partir do botão que a ferramenta mostra no chat. $ARGUMENTS"},
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


def _sim(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("true", "yes", "sim", "1")


def _parse_dir(skill_md: Path) -> dict | None:
    """Formato do DeepSeek Harness / Agent Skills: `<nome>/SKILL.md`, com a pasta como recurso."""
    try:
        campos, corpo = frontmatter(skill_md.read_text(encoding="utf-8"))
    except OSError:
        return None
    if not corpo.strip():
        return None
    nome = campos.get("name") or skill_md.parent.name
    return {"name": nome, "kind": "prompt", "description": campos.get("description", "")[:500],
            "prompt": corpo.strip(), "source": str(skill_md), "base": str(skill_md.parent),
            "model": not _sim(campos.get("disable-model-invocation")),
            "user": campos.get("user-invocable", "true").lower() not in ("false", "no", "nao", "não", "0")}


def pastas(root: Path) -> list[Path]:
    """De onde vêm skills, da mais geral para a mais específica (a última sobrepõe)."""
    from . import config  # tardio: config importa pouco, mas skills é importado cedo

    return [config.DATA_DIR / "skills", root / ".agents" / "skills", root / DIR]


def descobrir(root: Path) -> list[dict]:
    """Skills do usuário e do projeto: `.forja/skills/*.md` (formato antigo) e `*/SKILL.md`."""
    achadas: dict[str, dict] = {}
    for pasta in pastas(root):
        if not pasta.is_dir():
            continue
        for p in sorted(pasta.glob("*/SKILL.md")):
            if s := _parse_dir(p):
                achadas[s["name"]] = s
        if pasta == root / DIR:
            for p in sorted(pasta.glob("*.md")):
                if s := _parse(p):
                    achadas[s["name"]] = {**s, "model": True, "user": True}
    return list(achadas.values())[:MAX_SKILLS]


def list_for(root: Path) -> list[dict]:
    """Ações do Forja + skills que o usuário pode chamar com `/` (nome = comando)."""
    out = list(BUILTIN)
    for s in descobrir(root):
        if s.get("user", True):
            out = [x for x in out if x["name"] != s["name"]] + [s]  # skill do projeto sobrepõe a padrão
    return out


def do_modelo(root: Path) -> list[dict]:
    return [s for s in descobrir(root) if s.get("model")]


def catalogo(root: Path) -> str:
    """Bloco do contexto de execução com as skills que o modelo pode carregar (texto do harness)."""
    lista = do_modelo(root)
    if not lista:
        return ""
    itens = "\n".join(f"- `{s['name']}`: {s['description'] or s['prompt'].splitlines()[0][:200]}" for s in lista)
    return ("\n\nSkill é um conjunto de instruções reutilizáveis para um tipo de tarefa. Skills disponíveis:\n"
            f"{itens}\nSe o usuário citar uma skill, ou se a tarefa casar claramente com a descrição de uma, "
            "chame a ferramenta `skill` com o nome exato ANTES de agir e siga as instruções completas. Este "
            "catálogo tem só resumos: não siga nem deduza as instruções de uma skill antes de carregá-la. Se a "
            "skill já veio na conversa (o usuário a chamou com /), siga-a e não a carregue de novo.")


def _bloco(s: dict, root: Path, argumentos: str = "") -> str:
    corpo = expand(s["prompt"], argumentos) if "$ARGUMENTS" in s["prompt"] else s["prompt"]
    base = s.get("base") or (str(root / DIR) if s.get("source") else "")  # as do Forja não têm pasta
    recursos = f"<skill_resources>Pasta base desta skill: {base}</skill_resources>\n\n" if base else ""
    return (f'<skill_content name="{s["name"]}">\n{recursos}<skill_instructions>\n{corpo}\n'
            "</skill_instructions>\n</skill_content>")


def conteudo(root: Path, nome: str) -> str:
    s = next((x for x in do_modelo(root) if x["name"] == nome), None)
    if not s:
        nomes = ", ".join(x["name"] for x in do_modelo(root)) or "nenhuma"
        from .tools import ToolError

        raise ToolError(f"Skill '{nome}' não existe. Disponíveis: {nomes}.")
    return _bloco(s, root)


INLINE = re.compile(r"(?<!\S)/skill:([\w.-]+)")


def invocada(root: Path, mensagem: str | None) -> str | None:
    """`/nome argumentos` no começo, ou `/skill:nome` em qualquer ponto (várias) → os blocos das skills.

    Como no DeepSeek Harness, a mensagem fica como o usuário escreveu e a skill entra inteira logo
    depois — antes o front trocava o `/nome` pelo texto da skill, e a pasta dos recursos se perdia.
    """
    texto = (mensagem or "").strip()
    prompts = {x["name"]: x for x in list_for(root) if x.get("kind") == "prompt"}
    blocos: list[str] = []
    if texto.startswith("/") and not texto.startswith("/skill:") and "\n" not in texto.split(" ", 1)[0]:
        nome, _, argumentos = texto[1:].partition(" ")
        if s := prompts.get(nome):
            blocos.append(f"O usuário chamou a skill /{nome}"
                          + (f" com: {argumentos.strip()}" if argumentos.strip() else "")
                          + ". Siga as instruções dela; não a carregue de novo com a ferramenta skill.\n\n"
                          + _bloco(s, root, argumentos))
    inline = [n for n in dict.fromkeys(INLINE.findall(texto)) if n in prompts]
    if inline:
        # $ARGUMENTS fica vazio: no meio do texto não há "o que vem depois"; o pedido inteiro é o contexto
        blocos.append(f"O usuário citou as skills {', '.join('/skill:' + n for n in inline)} no pedido. "
                      "Siga as instruções de todas ao mesmo tempo; não as carregue de novo com a ferramenta skill.\n\n"
                      + "\n\n".join(_bloco(prompts[n], root) for n in inline))
    return "\n\n".join(blocos) or None


def expand(prompt: str, arguments: str) -> str:
    return prompt.replace("$ARGUMENTS", arguments.strip()).strip()


def _skill(root: Path, args: dict) -> str:
    return conteudo(root, str(args.get("name") or "").strip())


def _tem_skill() -> bool:
    from . import workspace

    try:
        return bool(do_modelo(workspace.root()))
    except Exception:  # pasta da conversa inacessível: a ferramenta só some
        return False


def _registra() -> None:
    from .tools import Tool, _obj, register

    register(Tool(
        "skill",
        "Carrega as instruções completas de uma skill do catálogo (veja 'Skills disponíveis' no contexto). "
        "Chame antes de começar a tarefa que casa com a skill e siga o que ela disser.",
        _obj({"name": {"type": "string", "description": "Nome exato da skill"}}, ["name"]),
        _skill, available=_tem_skill))


_registra()


# ------------------------------------------------------------------ Configurações › Skills

NOME_SKILL = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _do_usuario() -> Path:
    return pastas(Path("."))[0]  # config.DATA_DIR / "skills": a mesma pasta que descobrir() já lê


def para_configuracoes(root: Path) -> list[dict]:
    """Todas as skills com a origem: as do Forja (embutidas), as do usuário (editáveis na tela) e as do
    projeto da pasta padrão. Uma skill de projeto com o mesmo nome sobrepõe as outras na conversa."""
    usuario = _do_usuario().resolve()
    out = [{**b, "origem": "forja", "editavel": False} for b in BUILTIN]
    for s in descobrir(root):
        base = Path(s.get("base") or Path(s.get("source") or "").parent)
        do_usuario = usuario in base.resolve().parents or base.resolve() == usuario
        out.append({**s, "origem": "usuario" if do_usuario else "projeto", "editavel": do_usuario})
    return out


def salvar_do_usuario(nome: str, descricao: str, instrucoes: str, antigo: str = "") -> dict:
    """Cria ou atualiza `<dados>/skills/<nome>/SKILL.md` (o formato Agent Skills que descobrir() lê)."""
    nome, instrucoes = nome.strip(), instrucoes.strip()
    if not NOME_SKILL.match(nome):
        raise ValueError("Nome: minúsculas, números e hífens (ex.: revisar-textos).")
    if not instrucoes:
        raise ValueError("Escreva as instruções da skill.")
    if nome in {b["name"] for b in BUILTIN}:
        raise ValueError(f"/{nome} já é um comando do Forja: escolha outro nome.")
    pasta = _do_usuario() / nome
    if antigo and antigo != nome:
        if pasta.exists():
            raise ValueError(f"Já existe uma skill {nome}.")
        velha = _do_usuario() / antigo
        if velha.is_dir():
            velha.rename(pasta)  # renomear leva os arquivos de recurso junto
    pasta.mkdir(parents=True, exist_ok=True)
    desc = " ".join(descricao.split())  # o frontmatter é uma linha por campo
    (pasta / "SKILL.md").write_text(f"---\nname: {nome}\ndescription: {desc}\n---\n{instrucoes}\n", encoding="utf-8")
    return {"name": nome}


def apagar_do_usuario(nome: str) -> None:
    """Só as do usuário: as do Forja são código e as do projeto moram no repositório dele."""
    import shutil

    if not NOME_SKILL.match(nome or ""):
        raise ValueError("Skill inválida.")
    pasta = _do_usuario() / nome
    if not (pasta / "SKILL.md").is_file():
        raise ValueError(f"A skill {nome} não é sua (ou não existe).")
    shutil.rmtree(pasta)

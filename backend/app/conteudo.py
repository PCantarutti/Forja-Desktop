"""E18: Conteúdo — especificações de vídeo, estilos e (próximas etapas) roteiros e produção com o Claude.

Etapa 1: pastas, estilos e especificações.

- Estilos são arquivos .md numa pasta que o usuário escolhe (no PC do dono, `youtube/estilos`). O Forja
  lê e escreve direto ali, sem cópia no banco: o Claude que monta o vídeo lê o mesmo arquivo, então nada
  diverge. Estilo novo nasce do `_modelo.md` da pasta (que o Forja cria se faltar) e ganha uma linha na
  tabela "Estilos disponíveis" do README.md da pasta, quando ela existe.
- Especificação = conversa `kind="conteudo"` (a barra lateral lista, o /api/activity sincroniza o
  celular). Os campos ficam no `meta["especificacao"]` de uma mensagem `event` da conversa: sem tabela nova.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from . import db
from .tools import ToolError

KIND = "conteudo"
CHAVE = "conteudo"   # AppSetting: {pasta_estilos, pasta_projeto, pasta_saida, comandos}
NOME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
RESERVADOS = {"readme", "_modelo"}
MAX_ESTILO = 200_000   # caracteres; um estilo é um documento curto
MODOS = ("desligada", "aprovacao", "automatico")
# Formato do vídeo: vale para o roteiro (duração) e para a produção (tamanho da composição).
FORMATOS = {
    "vertical": {"largura": 1080, "altura": 1920, "rotulo": "vertical 9:16 (Shorts, Reels, TikTok)"},
    "horizontal": {"largura": 1920, "altura": 1080, "rotulo": "horizontal 16:9 (YouTube, vídeo longo)"},
}
HORA_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Comandos que o Claude pode rodar sozinho na produção (etapa 3). Editável na tela.
COMANDOS_PADRAO = ["npm run *", "npx remotion *", "npx tsc*", "python scripts/*", "node scripts/*", "ffmpeg *", "ffprobe *"]

MODELO_PADRAO = """# Estilo: `<nome-do-estilo>`

Uma frase: para que tipo de vídeo é este estilo e o que o diferencia.

## Roteiro
- **Tom:**
- **Tamanho:** palavras e duração
- **Estrutura:** cenas, função de cada uma e exemplo
- **Regras de escrita:**
- **Checagem:**

## Voz e legenda
| Parâmetro | Valor |
|---|---|
| TTS / voz | |
| Velocidade | |
| Legenda (cor, posição, palavras por página) | |

## Visual
- **Paleta:**
- **Tipografia** (precisa ter acentos do português):
- **Layout** (zonas da tela):
- **Componentes recorrentes:**
- **Transições:**

## Áudio
- **Trilha:**
- **Efeitos e volumes:**

## Engajamento e publicação
- **Título:**
- **Descrição (modelo):**
- **Capa:**
- **Comentário fixado:**
"""


# ------------------------------------------------------------------ pastas

def _area_de_trabalho() -> str:
    return str(Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop")


def pastas() -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        salvo = dict(linha.value) if linha and isinstance(linha.value, dict) else {}
    return {"pasta_estilos": "", "pasta_projeto": "", "pasta_saida": _area_de_trabalho(),
            "comandos": list(COMANDOS_PADRAO), **salvo}


def _pasta(valor: str, rotulo: str, obrigatoria: bool = False) -> str:
    valor = (valor or "").strip().strip('"')
    if not valor:
        if obrigatoria:
            raise ToolError(f"Escolha a {rotulo}.")
        return ""
    p = Path(valor)
    if not p.is_absolute() or not p.is_dir():
        raise ToolError(f"A {rotulo} não existe: {valor}")
    return str(p.resolve())


def salvar_pastas(dados: dict) -> dict:
    atual = pastas()
    novo = dict(atual)
    if "pasta_estilos" in dados:
        novo["pasta_estilos"] = _pasta(dados["pasta_estilos"], "pasta de estilos")
    if "pasta_projeto" in dados:
        novo["pasta_projeto"] = _pasta(dados["pasta_projeto"], "pasta do projeto de vídeo")
    if "pasta_saida" in dados:
        novo["pasta_saida"] = _pasta(dados["pasta_saida"], "pasta onde os vídeos prontos são entregues")
    if "claude_cli" in dados:   # vazio = achar sozinho (PATH)
        cli = str(dados["claude_cli"] or "").strip().strip('"')
        if cli and not Path(cli).is_file():
            raise ToolError(f"O Claude Code não está em: {cli}")
        novo["claude_cli"] = cli
    if "claude_conta" in dados:   # CLAUDE_CONFIG_DIR: outra conta do Claude Code; vazio = a conta padrão (~/.claude)
        conta = str(dados["claude_conta"] or "").strip().strip('"')
        if conta and not Path(conta).is_absolute():
            raise ToolError("A pasta da conta do Claude precisa ser um caminho completo (ex.: C:\\Users\\voce\\.claude-gabi).")
        if conta:
            Path(conta).mkdir(parents=True, exist_ok=True)
        novo["claude_conta"] = conta
    if "comandos" in dados:
        cmds = [str(c).strip()[:200] for c in (dados["comandos"] or []) if str(c).strip()]
        if len(cmds) > 50:
            raise ToolError("No máximo 50 comandos permitidos.")
        novo["comandos"] = cmds
    with db.session() as s:
        s.merge(db.AppSetting(key=CHAVE, value=novo))
        s.commit()
    return novo


# ------------------------------------------------------------------ estilos

def _dir_estilos() -> Path:
    d = pastas()["pasta_estilos"]
    if not d or not Path(d).is_dir():
        raise ToolError("Escolha a pasta de estilos primeiro.")
    return Path(d)


def _arquivo(nome: str) -> Path:
    nome = (nome or "").strip().lower()
    if not NOME_RE.match(nome) or nome in RESERVADOS:
        raise ToolError("Nome de estilo inválido: use letras minúsculas, números e hífen (ex.: alerta-tech).")
    return _dir_estilos() / f"{nome}.md"


def _resumo(texto: str) -> str:
    """Primeiro parágrafo depois do título: a "uma frase" do modelo."""
    for bloco in re.split(r"\n\s*\n", texto):
        b = bloco.strip()
        if b and not b.startswith("#"):
            return re.sub(r"\s+", " ", b)[:300]
    return ""


def estilos() -> list[dict]:
    out = []
    for p in sorted(_dir_estilos().glob("*.md")):
        if p.stem.lower() in RESERVADOS:
            continue
        try:
            texto = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out.append({"nome": p.stem, "resumo": _resumo(texto),
                    "atualizado": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")})
    return out


def ler_estilo(nome: str) -> dict:
    p = _arquivo(nome)
    if not p.is_file():
        raise ToolError(f"Estilo '{nome}' não encontrado.")
    return {"nome": p.stem, "texto": p.read_text(encoding="utf-8")}


def modelo() -> str:
    """O `_modelo.md` da pasta; criado com o padrão do Forja quando falta (o mesmo padrão para todos)."""
    p = _dir_estilos() / "_modelo.md"
    if not p.is_file():
        p.write_text(MODELO_PADRAO, encoding="utf-8")
    return p.read_text(encoding="utf-8")


def salvar_estilo(nome: str, texto: str, novo: bool = False, indice: dict | None = None) -> dict:
    p = _arquivo(nome)
    texto = (texto or "").replace("\r\n", "\n")
    if not texto.strip():
        raise ToolError("O estilo está vazio.")
    if len(texto) > MAX_ESTILO:
        raise ToolError("Estilo grande demais.")
    if novo and p.exists():
        raise ToolError(f"Já existe um estilo '{p.stem}'.")
    if not novo and not p.exists():
        raise ToolError(f"Estilo '{p.stem}' não encontrado.")
    p.write_text(texto if texto.endswith("\n") else texto + "\n", encoding="utf-8")
    if novo:
        _indexar(p.stem, indice or {}, texto)
    return {"nome": p.stem, "texto": p.read_text(encoding="utf-8")}


def _celula(v: str) -> str:
    return re.sub(r"\s+", " ", str(v or "")).replace("|", "/").strip()[:120]


def _indexar(nome: str, indice: dict, texto: str) -> None:
    """Linha nova na tabela "Estilos disponíveis" do README da pasta (se ele e a tabela existirem)."""
    readme = _dir_estilos() / "README.md"
    if not readme.is_file():
        return
    linhas = readme.read_text(encoding="utf-8").split("\n")
    try:
        i = next(n for n, l in enumerate(linhas) if l.strip().lower().startswith("## estilos disponíveis"))
    except StopIteration:
        return
    fim, viu_tabela = i + 1, False
    for n in range(i + 1, len(linhas)):
        if linhas[n].lstrip().startswith("|"):
            viu_tabela, fim = True, n + 1
        elif viu_tabela or linhas[n].startswith("#"):
            break
    if not viu_tabela:
        return
    linha = (f"| [`{nome}`]({nome}.md) | {_celula(indice.get('para_que') or _resumo(texto))} | "
             f"{_celula(indice.get('tom'))} | {_celula(indice.get('duracao'))} |")
    linhas.insert(fim, linha)
    readme.write_text("\n".join(linhas), encoding="utf-8")


def apagar_estilo(nome: str) -> None:
    p = _arquivo(nome)
    if not p.is_file():
        raise ToolError(f"Estilo '{nome}' não encontrado.")
    p.unlink()


def prompt_gerar_estilo(descricao: str, base: str) -> tuple[str, str]:
    """(system, user) para o modelo preencher o modelo de estilo a partir de uma descrição."""
    system = ("Você escreve guias de estilo para vídeos curtos (Shorts/Reels) em português do Brasil. "
              "Preencha o MODELO abaixo seguindo exatamente os mesmos títulos e a mesma ordem, sem acrescentar "
              "seções novas. Seja concreto: números (duração, palavras, cores em hex, volumes), exemplos de "
              "frases e regras verificáveis. Responda só com o Markdown do estilo, sem comentários antes ou depois.")
    user = f"MODELO:\n\n{base}\n\nDESCRIÇÃO DO ESTILO QUE O USUÁRIO QUER:\n\n{descricao.strip()}"
    return system, user


def limpar_markdown(texto: str) -> str:
    """Tira cerca ```markdown que alguns modelos põem em volta da resposta."""
    t = texto.strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```$", t, re.S)
    return (m.group(1) if m else t).strip() + "\n"


# ------------------------------------------------------------------ especificações

def _spec_padrao() -> dict:
    return {"tema": "", "palavras_chave": [], "fontes": [], "dias": 3, "estilo": "", "roteiros": 3, "formato": "vertical",
            "motor": {"provider": "", "model": ""}, "observacoes": "",
            "automacao": {"modo": "desligada", "hora_roteiros": "19:00", "hora_producao": "03:00"}}


def _lista(v, limite: int, tam: int = 120) -> list[str]:
    itens = v if isinstance(v, list) else re.split(r"[,\n]", str(v or ""))
    return [str(i).strip()[:tam] for i in itens if str(i).strip()][:limite]


def _inteiro(v, padrao: int) -> int:
    """Campo numérico da tela: vazio vira o padrão; 0 continua 0 (quem limita é o chamador)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return padrao


def validar_spec(dados: dict, base: dict | None = None) -> dict:
    spec = {**_spec_padrao(), **(base or {})}
    d = dados or {}
    if "tema" in d:
        spec["tema"] = str(d["tema"] or "").strip()[:2000]
    if "palavras_chave" in d:
        spec["palavras_chave"] = _lista(d["palavras_chave"], 30)
    if "fontes" in d:
        spec["fontes"] = _lista(d["fontes"], 30, 300)
    if "observacoes" in d:
        spec["observacoes"] = str(d["observacoes"] or "").strip()[:4000]
    if "dias" in d:
        spec["dias"] = max(1, min(30, _inteiro(d["dias"], 3)))
    if "roteiros" in d:
        spec["roteiros"] = max(1, min(10, _inteiro(d["roteiros"], 3)))
    if "estilo" in d:
        estilo = str(d["estilo"] or "").strip().lower()
        if estilo and (not NOME_RE.match(estilo) or estilo in RESERVADOS):
            raise ToolError("Estilo inválido.")
        spec["estilo"] = estilo
    if "formato" in d:
        if d["formato"] not in FORMATOS:
            raise ToolError("Formato inválido: vertical ou horizontal.")
        spec["formato"] = d["formato"]
    if "motor" in d:
        m = d["motor"] if isinstance(d["motor"], dict) else {}
        spec["motor"] = {"provider": str(m.get("provider") or "")[:60], "model": str(m.get("model") or "")[:300]}
    if "automacao" in d:
        a = {**spec["automacao"], **(d["automacao"] if isinstance(d["automacao"], dict) else {})}
        if a.get("modo") not in MODOS:
            raise ToolError("Modo de automação inválido.")
        for k in ("hora_roteiros", "hora_producao"):
            if not HORA_RE.match(str(a.get(k) or "")):
                raise ToolError("Horário inválido: use HH:MM (ex.: 03:00).")
        spec["automacao"] = {k: a[k] for k in ("modo", "hora_roteiros", "hora_producao")}
    if not spec["tema"]:
        raise ToolError("Descreva o tema da especificação.")
    return spec


def _msg_spec(s, conv_id: int) -> db.Message | None:
    return s.scalars(select(db.Message).where(db.Message.conversation_id == conv_id, db.Message.role == "event",
                                              db.Message.name == "especificacao").limit(1)).first()


def _conv(s, conv_id: int) -> db.Conversation:
    c = s.get(db.Conversation, conv_id)
    if not c or c.kind != KIND:
        raise ToolError("Especificação não encontrada.")
    return c


def _dict(c: db.Conversation, m: db.Message | None) -> dict:
    spec = {**_spec_padrao(), **(((m.meta or {}).get("especificacao")) if m else {})}
    return {"id": c.id, "nome": c.title, **spec, "atualizado": c.updated_at.isoformat() if c.updated_at else None}


def especificacoes() -> list[dict]:
    with db.session() as s:
        convs = s.scalars(select(db.Conversation).where(db.Conversation.kind == KIND,
                                                        db.Conversation.archived.is_(False))
                          .order_by(db.Conversation.updated_at.desc())).all()
        return [_dict(c, _msg_spec(s, c.id)) for c in convs]


def especificacao(conv_id: int) -> dict:
    with db.session() as s:
        c = _conv(s, conv_id)
        return _dict(c, _msg_spec(s, c.id))


def salvar_especificacao(dados: dict, conv_id: int | None = None) -> dict:
    nome = str((dados or {}).get("nome") or "").strip()[:200]
    with db.session() as s:
        if conv_id is None:
            if not nome:
                raise ToolError("Dê um nome à especificação.")
            spec = validar_spec(dados)
            c = db.Conversation(kind=KIND, title=nome)
            s.add(c)
            s.flush()
            s.add(db.Message(conversation_id=c.id, role="event", name="especificacao", content="",
                             meta={"especificacao": spec}))
        else:
            c = _conv(s, conv_id)
            m = _msg_spec(s, c.id)
            spec = validar_spec(dados, (m.meta or {}).get("especificacao") if m else None)
            if m is None:
                s.add(db.Message(conversation_id=c.id, role="event", name="especificacao", content="",
                                 meta={"especificacao": spec}))
            else:
                m.meta = {**(m.meta or {}), "especificacao": spec}   # JSON sem MutableDict: reatribuir
            if nome:
                c.title = nome
        c.updated_at = db._now()   # carimbo `lista` do /api/activity: o outro aparelho se atualiza
        s.commit()
        return _dict(c, _msg_spec(s, c.id))

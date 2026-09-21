"""Espelho das conversas em Markdown, para copiar entre máquinas.

O banco continua sendo a fonte da verdade. Estes arquivos são escritos no fim de cada execução
(e quando a conversa é renomeada) e apagados junto com a conversa. Editar o .md não volta para o
app: é uma cópia de leitura.

    %APPDATA%\\Forja\\conversas\\forja-code\\0007 - titulo.md   (modo agente)
    %APPDATA%\\Forja\\conversas\\forja-chat\\0008 - titulo.md   (chat)
    %APPDATA%\\Forja\\conversas\\forja-imagens\\0009 - titulo.md   (lotes de imagem)
    %APPDATA%\\Forja\\conversas\\forja-comparacoes\\0010 - titulo.md   (comparação de modelos)
"""
from __future__ import annotations

import json
import os
import re

from . import config, db, workspace

ROOT = config.DATA_DIR / "conversas"
DIRS = {"agent": "forja-code", "chat": "forja-chat", "imagem": "forja-imagens",
        "comparar": "forja-comparacoes", "pesquisa": "forja-pesquisas"}

# Proibidos em nome de arquivo no Windows, mais os de controle.
_PROIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def folder(kind: str | None):
    return ROOT / DIRS.get(kind or "agent", DIRS["agent"])


def _slug(title: str) -> str:
    nome = _PROIBIDOS.sub("", title or "").strip(" .")
    nome = re.sub(r"\s+", " ", nome)[:60].strip(" .")
    return nome or "conversa"


def _name(conv) -> str:
    return f"{conv.id:04d} - {_slug(conv.title)}.md"


def markdown(c) -> str:
    """A conversa inteira em Markdown. Mesmo texto do botão Exportar."""
    linhas = [f"# {c.title}", "", f"Pasta: {workspace.label(c.workspace)}  ·  exportado do Forja", ""]
    for m in c.messages:
        if m.role == "user":
            linhas += ["## Usuário", "", m.content or "", ""]
        elif m.role == "assistant" and (m.meta or {}).get("images"):  # lote de imagem
            linhas += [f"## Imagens ({m.status or 'pendente'})", ""]
            for img in m.meta["images"]:
                etiqueta = f"semente {img['seed']} · {img.get('model_name') or '?'} · {img['status']}"
                linhas += [f"- {etiqueta}", f"  ![{etiqueta}]({img['path']})"]
            linhas.append("")
        elif m.role == "assistant" and (m.meta or {}).get("pesquisa"):  # pesquisa profunda
            p = m.meta["pesquisa"]
            linhas += ["## Relatório", "", m.content or p.get("aviso") or "", "", "### Fontes lidas", ""]
            linhas += [f"- [{f['titulo'] or f['url']}]({f['url']}) — {f['status']}" for f in p["fontes"]]
            linhas.append("")
        elif m.role == "assistant" and (m.meta or {}).get("itens"):  # comparação de modelos
            for item in m.meta["itens"]:
                st = item.get("stats") or {}
                medida = f"{st.get('tokens', '?')} tokens · {st.get('tps', '?')} tok/s · {st.get('seconds', '?')}s"
                venceu = " 🏆" if item["id"] == (m.meta.get("voto") or None) else ""
                linhas += [f"## {item['nome']}{venceu} ({item['status']})", "", f"*{medida}*", "",
                           item["content"] or item["error"] or "", ""]
        elif m.role == "assistant":
            linhas += ["## Forja", "", m.content or ""]
            for tc in m.tool_calls or []:
                if tc["name"] in ("exit_plan_mode", "ask_user"):
                    continue  # o plano e a pergunta aparecem inteiros no resultado, logo abaixo
                linhas.append(f"- `{tc['name']}` {json.dumps(tc.get('arguments', {}), ensure_ascii=False)[:300]}")
            linhas.append("")
        elif m.role == "tool" and m.name == "exit_plan_mode":  # plano inteiro, legível, como no Claude
            meta = m.meta or {}
            estado = (f"aprovado, modo {meta['approved_mode']}" if meta.get("approved_mode") else
                      "aprovado" if meta.get("approved") else m.status or "pendente")
            linhas += [f"### Plano ({estado})", "", meta.get("plan") or "", ""]
        elif m.role == "tool" and m.name == "ask_user":
            meta = m.meta or {}
            perguntas = meta.get("questions") or [{"question": (meta.get("arguments") or {}).get("question", "")}]
            respostas = meta.get("answers") or [meta.get("answer")]
            for i, q in enumerate(perguntas):
                resposta = (respostas[i] if i < len(respostas) else "") or m.status
                linhas += [f"> **Pergunta:** {q.get('question', '')}", f"> **Resposta:** {resposta}", ""]
        elif m.role == "tool":
            linhas += [f"<details><summary>{m.name} [{m.status}]</summary>", "", "```",
                       (m.content or "")[:4000], "```", "", "</details>", ""]
        elif m.role == "event":
            linhas += [f"> {(m.content or '').replace(chr(10), chr(10) + '> ')}", ""]
    return "\n".join(linhas)


def _limpar(conv_id: int, manter=None) -> None:
    """Apaga os .md desta conversa nas duas pastas, menos o que acabou de ser escrito.

    É o que cuida do rename: o arquivo com o título antigo some em vez de virar duplicata.
    """
    for kind in DIRS:
        for antigo in folder(kind).glob(f"{conv_id:04d} - *.md"):
            if antigo != manter:
                antigo.unlink(missing_ok=True)


def write(conv_id: int) -> None:
    """Regrava o .md da conversa. Falha de disco não pode derrubar a execução do agente."""
    try:
        with db.session() as s:
            c = s.get(db.Conversation, conv_id)
            if c is None or not c.messages:
                return  # conversa apagada ou ainda vazia: não cria arquivo à toa
            destino = folder(c.kind) / _name(c)
            texto = markdown(c)
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_suffix(".md.tmp")  # grava e troca: um crash no meio não corrompe o .md
        tmp.write_text(texto, encoding="utf-8")
        os.replace(tmp, destino)
        _limpar(conv_id, manter=destino)
    except OSError as e:
        print(f"Forja: não consegui escrever o espelho da conversa {conv_id}: {e}", flush=True)


def remove(conv_id: int) -> None:
    """Conversa apagada no app: o .md vai junto."""
    try:
        _limpar(conv_id)
    except OSError as e:
        print(f"Forja: não consegui apagar o espelho da conversa {conv_id}: {e}", flush=True)


def sync() -> int:
    """Na subida: gera o que falta (banco de antes do espelho) e varre .md órfão."""
    escritos = 0
    try:
        with db.session() as s:
            convs = [(c.id, c.kind, _name(c), bool(c.messages)) for c in s.query(db.Conversation).all()]
        existentes = {cid for cid, _, _, _ in convs}
        for cid, kind, nome, tem_msg in convs:
            if tem_msg and not (folder(kind) / nome).exists():
                write(cid)
                escritos += 1
        for kind in DIRS:  # .md de conversa que não existe mais (apagada com o app fechado)
            for f in folder(kind).glob("[0-9][0-9][0-9][0-9] - *.md"):
                if int(f.name[:4]) not in existentes:
                    f.unlink(missing_ok=True)
    except OSError as e:
        print(f"Forja: não consegui sincronizar a pasta de conversas: {e}", flush=True)
    return escritos

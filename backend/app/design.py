"""Design: chat à esquerda, canvas à direita. A IA gera um documento HTML autocontido e cada pedido
seguinte vira uma versão nova.

Nada de tabela nova, como na pesquisa: um projeto é uma Conversation(kind="design"). Cada pedido
são duas mensagens — a do usuário e a do assistente, cujo meta["design"] guarda a versão:
{versao, html, descricao, atual}. `atual` marca a versão que o canvas mostra (desfazer/refazer só
mudam essa marca; restaurar uma versão antiga cria uma nova, o histórico nunca é apagado).

Falha nunca altera o documento: resposta sem HTML válido, erro do modelo ou cancelamento fecham a
mensagem sem versão, e a marca `atual` fica onde estava.

ponytail: fase 1 regenera o documento inteiro a cada pedido. Edição por fragmento (data-fid),
tokens e geração por seção entram nas próximas fases, que são justamente o que corta esse custo.
"""
from __future__ import annotations

import asyncio
import re
import time
from contextlib import aclosing
from html.parser import HTMLParser
from pathlib import Path

from . import config, db, llm, mirror
from .agent import _save
from .parsing import split_think
from .tools import ToolError

PROMPTS = Path(__file__).parent / "design_prompts"
TICK = 0.4   # segundos entre retratos do SSE (o parcial vai inteiro em cada um)

_RUNS: dict[int, dict] = {}   # message_id -> geração viva
_TAREFAS: set = set()         # referência forte das tasks (o loop só guarda fraca)


def prompt(nome: str) -> str:
    return (PROMPTS / f"{nome}.md").read_text("utf-8")


# ------------------------------------------------------------------ HTML

class _Checa(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: set[str] = set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)


def extrair_html(texto: str) -> str | None:
    """O documento dentro da resposta: tira raciocínio e cerca de código, corta do <!doctype>/<html>
    ao </html>. Sem </html> a resposta veio truncada e não serve."""
    texto = split_think(texto)[1]
    ini = re.search(r"<!doctype html|<html[\s>]", texto, re.I)
    fim = None
    for fim in re.finditer(r"</html\s*>", texto, re.I):
        pass
    if not ini or not fim or fim.end() <= ini.start():
        return None
    html = texto[ini.start():fim.end()]
    p = _Checa()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return None
    return html if {"html", "body"} <= p.tags else None


# ------------------------------------------------------------------ banco

def _versoes(s, conv_id: int) -> list[db.Message]:
    msgs = s.query(db.Message).filter(db.Message.conversation_id == conv_id,
                                      db.Message.role == "assistant").order_by(db.Message.id).all()
    return [m for m in msgs if (m.meta or {}).get("design", {}).get("versao")]


def _marca_atual(s, conv_id: int, versao: int) -> None:
    """meta é JSON puro: só persiste reatribuindo o dict inteiro."""
    for m in _versoes(s, conv_id):
        d = m.meta["design"]
        if d.get("atual") != (d["versao"] == versao):
            m.meta = {**m.meta, "design": {**d, "atual": d["versao"] == versao}}


def _atual(s, conv_id: int) -> db.Message | None:
    vs = _versoes(s, conv_id)
    return next((m for m in vs if m.meta["design"].get("atual")), vs[-1] if vs else None)


def _conv(s, conv_id: int) -> db.Conversation:
    c = s.get(db.Conversation, conv_id)
    if not c or c.kind != "design":
        raise ToolError("Projeto de design não encontrado.")
    return c


def projeto(conv_id: int) -> dict:
    """Tudo que a tela precisa: chat (sem o HTML de cada versão), versão atual e o HTML dela."""
    with db.session() as s:
        c = _conv(s, conv_id)
        atual = _atual(s, conv_id)
        mensagens = []
        for m in c.messages:
            if m.role not in ("user", "assistant"):
                continue
            d = (m.meta or {}).get("design", {})
            mensagens.append({"id": m.id, "role": m.role, "content": m.content, "status": m.status,
                              "versao": d.get("versao"), "created_at": m.created_at.isoformat()})
        return {"conv_id": conv_id, "titulo": c.title, "mensagens": mensagens,
                "total": len(_versoes(s, conv_id)),
                "atual": atual.meta["design"]["versao"] if atual else 0,
                "html": atual.meta["design"]["html"] if atual else "",
                "rodando": next((mid for mid, r in _RUNS.items() if r["conv_id"] == conv_id), None)}


def ir_para(conv_id: int, versao: int) -> dict:
    """Desfazer/refazer/abrir do histórico: só move a marca."""
    with db.session() as s:
        _conv(s, conv_id)
        if not any(m.meta["design"]["versao"] == versao for m in _versoes(s, conv_id)):
            raise ToolError(f"Versão {versao} não existe.")
        _marca_atual(s, conv_id, versao)
        s.commit()
    return projeto(conv_id)


def restaurar(conv_id: int, versao: int) -> dict:
    """Versão antiga volta como uma versão nova, no topo do histórico."""
    with db.session() as s:
        _conv(s, conv_id)
        velha = next((m for m in _versoes(s, conv_id) if m.meta["design"]["versao"] == versao), None)
        if not velha:
            raise ToolError(f"Versão {versao} não existe.")
        html = velha.meta["design"]["html"]
    _nova_versao(conv_id, None, html, f"restaurada da v{versao}")
    return projeto(conv_id)


def _nova_versao(conv_id: int, message_id: int | None, html: str, descricao: str) -> int:
    """Grava a versão (na mensagem da geração, ou numa nova ao restaurar) e a marca como atual."""
    with db.session() as s:
        n = max((m.meta["design"]["versao"] for m in _versoes(s, conv_id)), default=0) + 1
        texto = f"v{n}: {descricao}"
        meta = {"design": {"versao": n, "html": html, "descricao": descricao}}
        if message_id is None:
            s.add(db.Message(conversation_id=conv_id, role="assistant", content=texto, status="ok", meta=meta))
        else:
            m = s.get(db.Message, message_id)
            m.content, m.status, m.meta = texto, "ok", meta
        s.get(db.Conversation, conv_id).updated_at = db._now()
        s.flush()
        _marca_atual(s, conv_id, n)
        s.commit()
    return n


def _fecha(message_id: int, status: str, texto: str) -> None:
    with db.session() as s:
        m = s.get(db.Message, message_id)
        m.status, m.content = status, texto
        s.commit()


# ------------------------------------------------------------------ geração

def _descricao(pedido: str) -> str:
    linha = " ".join(pedido.split())
    return linha if len(linha) <= 60 else linha[:57].rstrip() + "…"


def start(conv_id: int, pedido: str, provider: str, model: str) -> dict:
    pedido = (pedido or "").strip()
    if not pedido:
        raise ToolError("Descreva o design.")
    if not (provider and model):
        raise ToolError("Escolha um modelo antes de gerar.")
    with db.session() as s:
        c = _conv(s, conv_id)
        if any(r["conv_id"] == conv_id for r in _RUNS.values()):
            raise ToolError("Já tem uma geração rodando neste projeto.")
        if c.title == "Nova conversa":
            c.title = _descricao(pedido)
        atual = _atual(s, conv_id)
        base = (atual.meta["design"]["versao"], atual.meta["design"]["html"]) if atual else (0, "")
        s.commit()

    if base[1]:
        user = (f"Documento atual (v{base[0]}):\n\n{base[1]}\n\nPedido de mudança: {pedido}\n\n"
                "Devolva o documento inteiro já com a mudança, mexendo só no que o pedido pede.")
    else:
        user = f"Pedido: {pedido}"
    mensagens = [{"role": "system", "content": prompt("documento")}, {"role": "user", "content": user}]

    _save(conv_id, role="user", content=pedido)
    msg = _save(conv_id, role="assistant", content="", status="running", meta={"design": {"base": base[0]}})
    run = _RUNS[msg.id] = {"conv_id": conv_id, "message_id": msg.id, "parcial": "", "cancelar": False,
                           "t0": time.monotonic(), "tokens": 0, "descricao": _descricao(pedido)}
    t = asyncio.create_task(_rodar(run, provider, model, mensagens))
    _TAREFAS.add(t)
    t.add_done_callback(_TAREFAS.discard)
    return msg.to_dict()


async def _rodar(run: dict, provider: str, model: str, mensagens: list[dict]) -> None:
    mid = run["message_id"]
    try:
        async with aclosing(llm.chat_stream(provider, model, mensagens, None, config.NUM_CTX, "baixo")) as fluxo:
            async for kind, val in fluxo:
                if run["cancelar"]:
                    break
                if kind == "content":
                    run["parcial"] += val
                    run["tokens"] += 1
                elif kind == "done":
                    run["tokens"] = (val or {}).get("completion_tokens") or run["tokens"]
        if run["cancelar"]:
            _fecha(mid, "cancelado", "Cancelado — o documento não mudou.")
        elif html := extrair_html(run["parcial"]):
            _nova_versao(run["conv_id"], mid, html, run["descricao"])
        else:
            _fecha(mid, "erro", "A resposta não trouxe um documento HTML completo — o documento não mudou.")
    except Exception as e:   # erro do modelo não pode deixar a mensagem em "running"
        _fecha(mid, "erro", f"Erro do modelo: {e} — o documento não mudou.")
    finally:
        _RUNS.pop(mid, None)
        mirror.write(run["conv_id"])


def estado(message_id: int) -> dict:
    if run := _RUNS.get(message_id):
        return {"message_id": message_id, "status": "rodando", "parcial": run["parcial"],
                "tokens": run["tokens"], "segundos": round(time.monotonic() - run["t0"], 1)}
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or "design" not in (m.meta or {}):
            raise ToolError("Geração não encontrada.")
        return {"message_id": message_id, "status": m.status or "ok", "texto": m.content,
                "versao": m.meta["design"].get("versao")}


def cancelar(message_id: int) -> dict:
    if run := _RUNS.get(message_id):
        run["cancelar"] = True
    return {"ok": True}

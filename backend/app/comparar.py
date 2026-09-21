"""Comparar modelos: o mesmo prompt em vários modelos, lado a lado.

Nada de tabela nova: uma comparação são duas mensagens de uma Conversation(kind="comparar") —
a do usuário com o prompt, e a do assistente cujo meta["itens"] a execução vai preenchendo.

Sem ferramentas, sem memória, sem pasta de trabalho: é uma chamada nua ao modelo, senão a
comparação mede o andaime e não o modelo.

Modo paralelo: todos ao mesmo tempo (só provedores). Modo sequencial: um de cada vez — é o único
que aceita arquivos .gguf, porque o Forja sobe um llama-server por vez: carrega, responde,
descarrega, carrega o próximo.

ponytail: o estado ao vivo vive num dict em memória (`_RUNS`) e o SSE manda o retrato inteiro a
cada tick. Buffer de eventos com cursor (como o agent.Run) seria correto e cinco vezes maior.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from pathlib import Path

from sqlalchemy import select

from . import config, db, llm, mirror
from .agent import _save, _stats
from .parsing import split_think
from .tools import ToolError

try:  # desktop: .gguf sobe e desce entre os modelos da comparação
    from . import localai
except ImportError:  # forja-web não tem IA local: comparação só entre provedores
    localai = None

MAX_MODELOS = 6
PROVIDER_LOCAL = "local"  # id do provedor do llama-server que o Forja sobe (config.LOCAL_PROVIDER)
MODOS = ("paralelo", "sequencial")
TICK = 0.2  # segundos entre retratos do SSE; abaixo disso ninguém percebe a diferença

_RUNS: dict[int, dict] = {}  # message_id -> corrida viva (sai daqui quando termina)


class ModeloCarregado(ToolError):
    """Tem um LLM na VRAM e a comparação vai trocá-lo. Quem chamou decide: confirmar ou desistir."""


# ------------------------------------------------------------------ banco

def _patch(message_id: int, **fields) -> dict:
    """meta é JSON puro (sem MutableDict): só persiste se o dict for reatribuído inteiro."""
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m:
            raise ToolError("Comparação não encontrada.")
        meta = dict(m.meta or {})
        if "meta" in fields:
            meta.update(fields.pop("meta"))
            m.meta = meta
        for k, v in fields.items():
            setattr(m, k, v)
        s.commit()
        return {**m.to_dict(), "conversation_id": m.conversation_id}


def _mensagem(message_id: int) -> dict:
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or not (m.meta or {}).get("itens"):
            raise ToolError("Comparação não encontrada.")
        return {**m.to_dict(), "conversation_id": m.conversation_id}


def _persistir(run: dict) -> None:
    """Só nas transições de status — gravar por token seria animar a tela escrevendo no disco."""
    _patch(run["message_id"], meta={"itens": run["itens"]})


# ------------------------------------------------------------------ preparo

def _preparar(itens: list[dict] | None, modo: str) -> list[dict]:
    """Valida a lista crua da UI e devolve os itens normalizados."""
    if modo not in MODOS:
        raise ToolError(f"modo deve ser {' ou '.join(MODOS)}.")
    crus = list(itens or [])
    if not 2 <= len(crus) <= MAX_MODELOS:
        raise ToolError(f"Escolha de 2 a {MAX_MODELOS} modelos para comparar.")
    out: list[dict] = []
    vistos: set[tuple[str, str, str]] = set()
    for i, cru in enumerate(crus):
        path = str(cru.get("path") or "").strip()
        provider = str(cru.get("provider") or "").strip()
        model = str(cru.get("model") or "").strip()
        if path:
            if localai is None:
                raise ToolError("Esta versão do Forja não roda arquivos .gguf; escolha modelos de um provedor.")
            if modo != "sequencial":
                raise ToolError("Arquivos .gguf só entram no modo sequencial: o Forja sobe um "
                                "llama-server por vez.")
            if not Path(path).is_file():
                raise ToolError(f"Modelo não encontrado: {path}")
            provider, model = PROVIDER_LOCAL, localai.alias_of(path)
        elif not (provider and model):
            raise ToolError("Escolha um provedor e um modelo, ou um arquivo .gguf.")
        chave = (provider, model, path)
        if chave in vistos:
            raise ToolError(f"Modelo repetido na comparação: {model}")
        vistos.add(chave)
        out.append({"id": str(i), "rotulo": chr(ord("A") + i), "provider": provider, "model": model,
                    "path": path, "nome": model, "status": "pendente", "content": "", "reasoning": "",
                    "stats": None, "error": ""})
    if any(x["path"] for x in out) and any(x["provider"] == PROVIDER_LOCAL and not x["path"] for x in out):
        raise ToolError("Não dá para comparar o modelo local já carregado com um .gguf: a carga o substitui.")
    return out


def _mensagens(prompt: str, system: str) -> list[dict]:
    base = [{"role": "system", "content": system}] if (system or "").strip() else []
    return base + [{"role": "user", "content": prompt}]


# ------------------------------------------------------------------ execução

def start(conv_id: int, prompt: str, itens: list[dict] | None, modo: str = "paralelo", system: str = "",
          effort: str = "medio", cego: bool = False, confirm: bool = False) -> dict:
    """Cria as duas mensagens, registra a corrida e dispara a task. Devolve a msg do assistente."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ToolError("Escreva o prompt da comparação.")
    itens = _preparar(itens, modo)

    descarregado = ""
    if any(i["path"] for i in itens) and localai and localai.status()["running"]:
        alias = localai.status().get("alias") or "um modelo"
        if not confirm:
            raise ModeloCarregado(alias)
        descarregado = alias  # a UI oferece recarregar no fim; a carga do 1º .gguf já substitui

    with db.session() as s:
        conv = s.get(db.Conversation, conv_id)
        if not conv:
            raise ToolError("Conversa não encontrada.")
        if conv.title == "Nova conversa":
            conv.title = prompt.splitlines()[0][:60] or "Nova conversa"
        s.commit()

    _save(conv_id, role="user", content=prompt,
          meta={"modo": modo, "cego": cego, "modelos": [i["nome"] for i in itens]})
    msg = _save(conv_id, role="assistant", content="", status="running",
                meta={"modo": modo, "cego": cego, "revelado": False, "voto": "", "system": system,
                      "effort": effort, "descarregado": descarregado, "itens": itens})
    run = _RUNS[msg.id] = {"message_id": msg.id, "conv_id": conv_id, "status": "rodando", "modo": modo,
                           "cego": cego, "cancelar": False, "itens": itens}
    asyncio.create_task(_rodar(run, _mensagens(prompt, system), effort))
    return msg.to_dict()


async def _rodar(run: dict, mensagens: list[dict], effort: str) -> None:
    try:
        if run["modo"] == "sequencial":
            await _sequencial(run, mensagens, effort)
        else:
            # gather: uma falha não derruba as outras porque _um nunca levanta
            await asyncio.gather(*(_um(run, item, mensagens, effort) for item in run["itens"]))
    finally:
        pronto = any(i["status"] == "pronto" for i in run["itens"])
        run["status"] = "pronto" if pronto else ("cancelado" if run["cancelar"] else "erro")
        _patch(run["message_id"], status=run["status"], meta={"itens": run["itens"]})
        mirror.write(run["conv_id"])
        # sai do ar por último: daqui em diante o `estado()` vem do banco, já final
        _RUNS.pop(run["message_id"], None)


async def _um(run: dict, item: dict, mensagens: list[dict], effort: str) -> None:
    """Uma chamada de modelo. Nunca levanta: a falha é o resultado deste modelo, não da comparação."""
    if run["cancelar"]:
        item["status"] = "cancelado"
        return
    item["status"] = "rodando"
    _persistir(run)
    t0, t_first, done = time.monotonic(), 0.0, {}
    try:
        ctx = await llm.context_limit(item["provider"], item["model"], config.NUM_CTX)
        async with aclosing(llm.chat_stream(item["provider"], item["model"], mensagens, None,
                                            config.NUM_CTX, effort)) as fluxo:
            async for kind, val in fluxo:
                if run["cancelar"]:
                    item["status"] = "cancelado"
                    return  # fechar o gerador corta o HTTP e o servidor para de gerar
                if kind == "content":
                    t_first = t_first or time.monotonic()
                    item["content"] += val
                elif kind == "reasoning":
                    item["reasoning"] += val
                elif kind == "done":
                    done = val or {}
        pensou, visivel = split_think(item["content"])
        item["content"] = visivel or item["content"]
        item["reasoning"] = item["reasoning"] or pensou
        item["stats"] = _stats(mensagens, None, item["content"], item["reasoning"], done, t0, t_first,
                               ctx, item["model"])
        item["status"] = "pronto"
    except Exception as e:  # rede, servidor caindo, modelo inexistente
        item.update(status="erro", error=str(e) or e.__class__.__name__)
    finally:
        _persistir(run)


async def _sequencial(run: dict, mensagens: list[dict], effort: str) -> None:
    subiu = False
    try:
        for item in run["itens"]:
            if run["cancelar"]:
                item["status"] = "cancelado"
                _persistir(run)
                continue
            if item["path"]:
                item["status"] = "carregando"
                _persistir(run)
                try:
                    await asyncio.to_thread(localai.load, item["path"])
                    subiu = True
                except Exception as e:  # sem runtime, sem memória, imagem ocupando a VRAM
                    item.update(status="erro", error=str(e))
                    _persistir(run)
                    continue
                item["model"] = localai.status().get("alias") or item["nome"]
            await _um(run, item, mensagens, effort)
    finally:
        if subiu:
            await asyncio.to_thread(localai.unload)  # a VRAM não fica presa depois da comparação


# ------------------------------------------------------------------ leitura e controle

def estado(message_id: int) -> dict:
    """Retrato da corrida viva ou, se já acabou, o que está no banco. É o payload do SSE."""
    run = _RUNS.get(message_id)
    if run:
        return {"message_id": message_id, "status": run["status"], "modo": run["modo"],
                "cego": run["cego"], "revelado": False, "voto": "",
                "itens": [dict(i) for i in run["itens"]]}
    m = _mensagem(message_id)
    meta = m["meta"] or {}
    return {"message_id": message_id, "status": m["status"] or "pronto",
            "modo": meta.get("modo") or "paralelo", "cego": bool(meta.get("cego")),
            "revelado": bool(meta.get("revelado")), "voto": meta.get("voto") or "",
            "itens": meta.get("itens") or []}


def cancelar(message_id: int) -> dict:
    run = _RUNS.get(message_id)
    if not run:
        return {"ok": True}  # já acabou: nada a cancelar
    run["cancelar"] = True
    if localai and any(i["status"] == "carregando" for i in run["itens"]):
        localai.cancel_load()
    return {"ok": True}


def votar(message_id: int, voto: str) -> dict:
    """Marca o vencedor (o mesmo id de novo desfaz) e revela os nomes do modo cego."""
    m = _mensagem(message_id)
    meta = m["meta"] or {}
    if voto and voto not in {i["id"] for i in meta["itens"]}:
        raise ToolError("Voto inválido.")
    atual = meta.get("voto") or ""
    out = _patch(message_id, meta={"voto": "" if voto == atual else voto, "revelado": True})
    mirror.write(out["conversation_id"])
    return out


def placar(limite: int = 200) -> dict:
    """Vitórias por modelo. ponytail: varredura — comparações são dezenas, não milhões."""
    with db.session() as s:
        msgs = s.scalars(select(db.Message).join(db.Conversation)
                         .where(db.Conversation.kind == "comparar", db.Message.role == "assistant")
                         .order_by(db.Message.id.desc()).limit(limite)).all()
        metas = [m.meta or {} for m in msgs]
    linhas: dict[str, dict] = {}
    for meta in metas:
        for item in meta.get("itens") or []:
            linha = linhas.setdefault(item["nome"], {"nome": item["nome"], "rodadas": 0, "vitorias": 0,
                                                     "erros": 0, "tps": []})
            linha["rodadas"] += 1
            linha["erros"] += item["status"] == "erro"
            linha["vitorias"] += item["id"] == meta.get("voto")
            if (item.get("stats") or {}).get("tps"):
                linha["tps"].append(item["stats"]["tps"])
    saida = [{**l, "tps": round(sum(l["tps"]) / len(l["tps"]), 1) if l["tps"] else None}
             for l in linhas.values()]
    return {"linhas": sorted(saida, key=lambda l: (-l["vitorias"], -l["rodadas"], l["nome"])),
            "comparacoes": len(metas)}

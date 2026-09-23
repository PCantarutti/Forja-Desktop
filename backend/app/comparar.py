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
_TAREFAS: set = set()  # referência forte das execuções em voo (ver start)


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
    for i, cru in enumerate(crus):
        out.append(_novo_item(cru, modo, i, out))
    if any(x["path"] for x in out) and any(x["provider"] == PROVIDER_LOCAL and not x["path"] for x in out):
        raise ToolError("Não dá para comparar o modelo local já carregado com um .gguf: a carga o substitui.")
    return out


def _novo_item(cru: dict, modo: str, n: int, existentes: list[dict]) -> dict:
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
    if any((i["provider"], i["model"], i["path"]) == (provider, model, path) for i in existentes):
        raise ToolError(f"Modelo repetido na comparação: {model}")
    return {"id": str(n), "rotulo": chr(ord("A") + n), "provider": provider, "model": model,
            "path": path, "nome": model, "status": "pendente", "content": "", "reasoning": "",
            "stats": None, "error": ""}


def _mensagens(prompt: str, system: str) -> list[dict]:
    base = [{"role": "system", "content": system}] if (system or "").strip() else []
    return base + [{"role": "user", "content": prompt}]


# ------------------------------------------------------------------ execução

def start(conv_id: int, prompt: str, itens: list[dict] | None, modo: str = "paralelo", system: str = "",
          effort: str = "medio", cego: bool = False, confirm: bool = False, bateria: str = "") -> dict:
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

    from . import baterias  # import tardio: baterias usa este módulo
    if bateria and bateria not in baterias.BATERIAS:
        raise ToolError(f"Teste desconhecido: {bateria}")
    if bateria and not system.strip():
        system = baterias.SYSTEM
    _save(conv_id, role="user", content=prompt,
          meta={"modo": modo, "cego": cego, "modelos": [i["nome"] for i in itens], "bateria": bateria})
    msg = _save(conv_id, role="assistant", content="", status="running",
                meta={"modo": modo, "cego": cego, "revelado": False, "voto": "", "system": system,
                      "effort": effort, "descarregado": descarregado, "itens": itens})
    run = _RUNS[msg.id] = {"message_id": msg.id, "conv_id": conv_id, "status": "rodando", "modo": modo,
                           "cego": cego, "cancelar": False, "itens": itens, "fila": []}
    # O loop só guarda referência fraca para a task: sem manter a nossa, o coletor de lixo pode
    # levar a execução no meio e a mensagem fica em "running" para sempre, sem erro nenhum.
    # O arquivo da bateria (documento para ler) vai para os modelos, não para a mensagem visível.
    enviado = baterias.com_anexo(prompt, bateria) if bateria else prompt
    _disparar(_rodar(run, _mensagens(enviado, system), effort))
    return msg.to_dict()


def _disparar(coro) -> None:
    t = asyncio.create_task(coro)
    _TAREFAS.add(t)
    t.add_done_callback(_TAREFAS.discard)


def refazer(message_id: int, item_id: str) -> dict:
    """Gera de novo a resposta de UM modelo (alucinou, entrou em laço, deu erro). Gerando: recomeça na
    hora. Já terminou com a comparação ainda rodando: entra na fila do fim. Comparação encerrada: abre
    uma corrida só com ele — mesmo prompt, mesmo system, mesmo anexo."""
    run = _RUNS.get(message_id)
    if run:
        item = next((i for i in run["itens"] if i["id"] == item_id), None)
        if not item:
            raise ToolError("Modelo não encontrado nesta comparação.")
        if item["status"] == "rodando":
            item["refazer"] = True
        elif item["status"] not in ("pendente", "carregando") and item not in run["fila"]:
            _limpar(item, "pendente")
            run["fila"].append(item)
            _persistir(run)
        return {"ok": True}

    m = _mensagem(message_id)
    meta = m["meta"] or {}
    item = next((i for i in meta["itens"] if i["id"] == item_id), None)
    if not item:
        raise ToolError("Modelo não encontrado nesta comparação.")
    if item["path"] and not Path(item["path"]).is_file():
        raise ToolError(f"Modelo não encontrado: {item['path']}")
    itens = meta["itens"]
    item = next(i for i in itens if i["id"] == item_id)
    _limpar(item, "pendente")
    _so_a_fila(m, itens, item)
    return {"ok": True}


def _so_a_fila(m: dict, itens: list[dict], item: dict) -> None:
    """Comparação encerrada: uma corrida nova só com este item — mesmo prompt, system e anexo."""
    message_id, meta = m["id"], m["meta"] or {}
    with db.session() as s:
        pedido = s.scalars(select(db.Message).where(db.Message.conversation_id == m["conversation_id"],
                                                    db.Message.role == "user", db.Message.id < message_id)
                           .order_by(db.Message.id.desc()).limit(1)).first()
        prompt, bateria = (pedido.content, (pedido.meta or {}).get("bateria") or "") if pedido else ("", "")
    if not prompt:
        raise ToolError("Não achei o prompt desta comparação.")
    from . import baterias
    enviado = baterias.com_anexo(prompt, bateria) if bateria else prompt
    run = _RUNS[message_id] = {"message_id": message_id, "conv_id": m["conversation_id"], "status": "rodando",
                               "modo": meta.get("modo") or "paralelo", "cego": bool(meta.get("cego")),
                               "cancelar": False, "itens": itens, "fila": [item]}
    _patch(message_id, status="running", meta={"itens": itens})
    _disparar(_rodar(run, _mensagens(enviado, meta.get("system") or ""), meta.get("effort") or "medio",
                     so_fila=True))


def adicionar(message_id: int, cru: dict) -> dict:
    """Mais um modelo numa comparação que já rodou: só ele gera; os outros ficam como estão."""
    run = _RUNS.get(message_id)
    m = None if run else _mensagem(message_id)
    itens = run["itens"] if run else m["meta"]["itens"]
    modo = run["modo"] if run else (m["meta"].get("modo") or "paralelo")
    if len(itens) >= MAX_MODELOS:
        raise ToolError(f"A comparação já tem {MAX_MODELOS} modelos.")
    if cru.get("path") and modo != "sequencial":
        raise ToolError("Esta comparação rodou em paralelo: .gguf só entra numa comparação sequencial.")
    # letra e id novos, nunca reaproveitados: a análise antiga e o voto citam os de antes
    n = max((ord(i["rotulo"]) - ord("A") for i in itens), default=-1) + 1
    item = _novo_item(cru, modo, n, itens)
    item["id"] = str(max((int(i["id"]) for i in itens), default=-1) + 1)
    itens.append(item)
    if run:
        run["fila"].append(item)
        _persistir(run)
    else:
        _so_a_fila(m, itens, item)
    return {"ok": True, "item": item["id"]}


def remover(message_id: int, item_id: str) -> dict:
    """Tira um modelo (e a resposta dele) da comparação — para analisar de novo sem ele."""
    run = _RUNS.get(message_id)
    itens = run["itens"] if run else _mensagem(message_id)["meta"]["itens"]
    item = next((i for i in itens if i["id"] == item_id), None)
    if not item:
        raise ToolError("Modelo não encontrado nesta comparação.")
    if item["status"] in ("rodando", "carregando"):
        raise ToolError("Este modelo está gerando: pare a comparação antes de tirá-lo.")
    if len(itens) <= 2:
        raise ToolError("Uma comparação precisa de pelo menos 2 modelos.")
    itens.remove(item)
    if run:
        if item in run["fila"]:
            run["fila"].remove(item)
        _persistir(run)
        return {"ok": True}
    voto = _mensagem(message_id)["meta"].get("voto")
    out = _patch(message_id, meta={"itens": itens, **({"voto": ""} if voto == item_id else {})})
    mirror.write(out["conversation_id"])
    return {"ok": True}


async def _rodar(run: dict, mensagens: list[dict], effort: str, so_fila: bool = False) -> None:
    try:
        if so_fila:
            pass
        elif run["modo"] == "sequencial":
            await _sequencial(run, mensagens, effort)
        else:
            # gather: uma falha não derruba as outras porque _um nunca levanta
            await asyncio.gather(*(_um(run, item, mensagens, effort) for item in run["itens"]))
        await _fila(run, mensagens, effort)
    finally:
        if run.get("subiu"):
            await asyncio.to_thread(localai.unload)  # a VRAM não fica presa depois da comparação
        pronto = any(i["status"] == "pronto" for i in run["itens"])
        run["status"] = "pronto" if pronto else ("cancelado" if run["cancelar"] else "erro")
        _patch(run["message_id"], status=run["status"], meta={"itens": run["itens"]})
        mirror.write(run["conv_id"])
        # sai do ar por último: daqui em diante o `estado()` vem do banco, já final
        _RUNS.pop(run["message_id"], None)


LACO = "**[Parado pelo Forja: o modelo entrou em laço, repetindo o mesmo trecho]**"
REPETICOES = 12


def em_laco(texto: str) -> bool:
    """O fim do texto é um ciclo de 1 a 6 linhas repetido REPETICOES vezes seguidas."""
    linhas = [l.strip() for l in texto[-6000:].splitlines()]
    for p in range(1, 7):
        cauda = linhas[-p * REPETICOES - 1:-1]  # a última linha ainda pode estar pela metade
        if len(cauda) == p * REPETICOES and any(cauda[:p]) and all(l == cauda[i % p] for i, l in enumerate(cauda)):
            return True
    return False


async def _um(run: dict, item: dict, mensagens: list[dict], effort: str) -> None:
    """Uma chamada de modelo. Nunca levanta: a falha é o resultado deste modelo, não da comparação."""
    if run["cancelar"]:
        item["status"] = "cancelado"
        return
    item["status"] = "rodando"
    _persistir(run)
    t0, t_first, done, de_novo = time.monotonic(), 0.0, {}, False
    try:
        ctx = await llm.context_limit(item["provider"], item["model"], config.NUM_CTX)
        async with aclosing(llm.chat_stream(item["provider"], item["model"], mensagens, None,
                                            config.NUM_CTX, effort)) as fluxo:
            async for kind, val in fluxo:
                if run["cancelar"]:
                    item["status"] = "cancelado"
                    return  # fechar o gerador corta o HTTP e o servidor para de gerar
                if item.pop("refazer", False):
                    de_novo = True
                    break
                if kind == "content":
                    t_first = t_first or time.monotonic()
                    item["content"] += val
                elif kind == "reasoning":
                    # o relógio da geração começa no primeiro token, pensado ou não: os tokens de
                    # raciocínio entram na contagem, e sem isto o tok/s saía 4x maior que o real
                    t_first = t_first or time.monotonic()
                    item["reasoning"] += val
                elif kind == "done":
                    done = val or {}
                if t_first:
                    item["stats"] = _ao_vivo(item, t0, t_first)
                texto = item["content"] if kind == "content" else item["reasoning"]
                if kind in ("content", "reasoning") and len(texto) // 400 != (len(texto) - len(val or "")) // 400 and em_laco(texto):
                    # modelo local degenerado repete a mesma linha até o teto de tokens (minutos de GPU à
                    # toa): corta, e a resposta fica como está — é o resultado deste modelo
                    item["content"] += f"\n\n{LACO}"
                    break
        if de_novo:
            _limpar(item, "rodando")
            return
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
        if de_novo:
            await _um(run, item, mensagens, effort)


def _limpar(item: dict, status: str) -> None:
    item.update(status=status, content="", reasoning="", stats=None, error="")
    item.pop("refazer", None)


CHARS_POR_TOKEN = 3.5  # estimativa durante a geração; o número real chega no fim, do servidor


def _ao_vivo(item: dict, t0: float, t_first: float) -> dict:
    """Estatística enquanto o modelo gera (o servidor só conta os tokens no fim): tokens estimados pelos
    caracteres, tempo desde o pedido e tok/s desde o primeiro token. Marcada como estimada."""
    agora = time.monotonic()
    tokens = round((len(item["content"]) + len(item["reasoning"])) / CHARS_POR_TOKEN)
    gerando = max(agora - t_first, 0.001)
    return {"tokens": tokens, "seconds": round(agora - t0, 1), "tps": round(tokens / gerando, 1),
            "estimated": True, "ao_vivo": True}


async def _carregar_e_rodar(run: dict, item: dict, mensagens: list[dict], effort: str) -> bool:
    """Devolve se subiu um llama-server (quem chamou descarrega no fim)."""
    if run["cancelar"]:
        item["status"] = "cancelado"
        _persistir(run)
        return False
    if item["path"]:
        if localai.status().get("alias") != localai.alias_of(item["path"]):
            item["status"] = "carregando"
            _persistir(run)
            try:
                await asyncio.to_thread(localai.load, item["path"])
            except Exception as e:  # sem runtime, sem memória, imagem ocupando a VRAM
                item.update(status="erro", error=str(e))
                _persistir(run)
                return False
        item["model"] = localai.status().get("alias") or item["nome"]
    await _um(run, item, mensagens, effort)
    return bool(item["path"])


async def _sequencial(run: dict, mensagens: list[dict], effort: str) -> None:
    for item in run["itens"]:
        run["subiu"] = await _carregar_e_rodar(run, item, mensagens, effort) or run.get("subiu", False)


async def _fila(run: dict, mensagens: list[dict], effort: str) -> None:
    """Modelos que o usuário mandou refazer depois de terminarem, um de cada vez."""
    while run["fila"] and not run["cancelar"]:
        item = run["fila"].pop(0)
        if run["modo"] == "sequencial":
            run["subiu"] = await _carregar_e_rodar(run, item, mensagens, effort) or run.get("subiu", False)
        else:
            await _um(run, item, mensagens, effort)


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

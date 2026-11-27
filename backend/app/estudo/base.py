"""Estado em disco, execuções vivas e a chamada ao modelo."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .. import config, db, pesquisa
from ..parsing import split_think
from ..tools import ToolError

KIND = "estudo"
RAIZ = config.DATA_DIR / "estudo"
TICK = 0.4

_TRAVA = threading.Lock()   # ponytail: uma trava para todos os estados; por matéria se um dia disputar
_RUNS: dict[int, dict] = {}  # conv_id -> execução viva (uma por matéria; o tutor não entra aqui)
_TAREFAS: set = set()


def pasta(conv_id: int) -> Path:
    return RAIZ / str(conv_id)


def conferir_conv(conv_id: int) -> str:
    """Levanta se não for matéria de estudo. Devolve o título."""
    with db.session() as s:
        c = s.get(db.Conversation, conv_id)
        if not c or c.kind != KIND:
            raise ToolError("Matéria não encontrada.")
        return c.title


def tocar(conv_id: int, titulo: str | None = None) -> None:
    """O carimbo `lista` do /api/activity sai do updated_at: é o que faz a outra tela se atualizar."""
    with db.session() as s:
        if c := s.get(db.Conversation, conv_id):
            c.updated_at = db._now()
            if titulo and c.title == "Nova conversa":
                c.title = titulo[:60]
            s.commit()


def novo_estado() -> dict:
    return {"pedido": "", "nivel": "concurso", "programa": [], "fontes": [], "material": {}, "provas": [],
            "flashcards": [], "duvidas": [], "seq": {"fonte": 0, "prova": 0, "cartao": 0, "topico": 0}}


def ler(conv_id: int) -> dict:
    arq = pasta(conv_id) / "estado.json"
    with _TRAVA:
        if not arq.exists():
            return novo_estado()
        return {**novo_estado(), **json.loads(arq.read_text("utf-8"))}


def _gravar(conv_id: int, est: dict) -> None:
    d = pasta(conv_id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "estado.json.tmp"
    tmp.write_text(json.dumps(est, ensure_ascii=False), "utf-8")
    os.replace(tmp, d / "estado.json")   # atômico: queda no meio não deixa JSON pela metade


@contextmanager
def mudar(conv_id: int):
    """with mudar(c) as est: ... — lê, deixa mexer e grava sob a trava. Toca o carimbo no fim."""
    arq = pasta(conv_id) / "estado.json"
    with _TRAVA:
        est = {**novo_estado(), **json.loads(arq.read_text("utf-8"))} if arq.exists() else novo_estado()
        yield est
        _gravar(conv_id, est)
    tocar(conv_id)


def proximo(est: dict, chave: str) -> int:
    est["seq"][chave] = est["seq"].get(chave, 0) + 1
    return est["seq"][chave]


def apagar(conv_id: int) -> None:
    shutil.rmtree(pasta(conv_id), ignore_errors=True)


# ------------------------------------------------------------------ execuções

def novo_run(conv_id: int, tipo: str, spec: dict, teto: float = 3600) -> dict:
    if conv_id in _RUNS:
        raise ToolError("Esta matéria já tem uma tarefa rodando. Espere ou pare antes.")
    return {"conv_id": conv_id, "tipo": tipo, "spec": spec, "fase": "", "feitos": 0, "total": 0, "log": [],
            "aviso": "", "erro": "", "status": "rodando", "cancelar": False, "t0": time.monotonic(), "teto": teto,
            "stats": {"tokens": 0, "tokens_entrada": 0, "gerando": 0.0, "chamadas": 0, "estimado": False,
                      "segundos": 0.0}}


def disparar(run: dict, coro) -> None:
    """Referência forte da task: o loop só guarda fraca e o coletor levaria a execução no meio."""
    _RUNS[run["conv_id"]] = run

    async def envolto():
        try:
            await coro
            run["status"] = "cancelado" if run["cancelar"] else "pronto"
        except ToolError as e:
            run.update(status="erro", erro=str(e))
        except Exception as e:  # nada pode deixar a execução presa em "rodando"
            run.update(status="erro", erro=f"{e.__class__.__name__}: {e}"[:400])
        finally:
            run["fim"] = time.monotonic()
            _ULTIMO[run["conv_id"]] = publico(run)
            _RUNS.pop(run["conv_id"], None)
            tocar(run["conv_id"])

    t = asyncio.create_task(envolto())
    _TAREFAS.add(t)
    t.add_done_callback(_TAREFAS.discard)


_ULTIMO: dict[int, dict] = {}   # retrato final da última execução (a tela mostra o aviso/erro dela)


def publico(run: dict) -> dict:
    run["stats"]["segundos"] = round(time.monotonic() - run["t0"], 1)
    return {k: v for k, v in run.items() if k in ("tipo", "fase", "feitos", "total", "log", "aviso", "erro", "status",
                                                    "stats", "alvo")}


def estado_run(conv_id: int) -> dict | None:
    if run := _RUNS.get(conv_id):
        return publico(run)
    return _ULTIMO.get(conv_id)


def cancelar(conv_id: int) -> None:
    if run := _RUNS.get(conv_id):
        run["cancelar"] = True


def registrar(run: dict, texto: str) -> None:
    run["log"] = (run["log"] + [texto])[-60:]


def parar(run: dict) -> bool:
    return run["cancelar"]


# ------------------------------------------------------------------ modelo

def spec_de(provider: str, model: str) -> dict:
    if not (provider and model):
        raise ToolError("Escolha um modelo antes.")
    if provider == "claude-mcp":
        raise ToolError("O motor Claude via MCP saiu da tela de Estudos; escolha um modelo do Forja.")
    return {"provider": provider, "model": model}


async def perguntar(run: dict, system: str, user: str, effort: str = "baixo") -> str:
    """Uma chamada sem ferramentas; devolve só o texto visível (sem o <think>)."""
    run["teto"] = time.monotonic() - run["t0"] + 900   # cada chamada tem seus 15 min, não a execução inteira
    texto = await pesquisa._perguntar(run["spec"], system, user, run, effort)
    return split_think(texto or "")[1].strip()


async def preparar_modelo(run: dict) -> None:
    """IA local vazia: sobe o modelo escolhido (o mesmo gancho do Design)."""
    from .. import design
    run["fase"] = "carregando o modelo"
    await design._garantir_local(run)


async def orcamento(run: dict) -> int:
    """Caracteres de fonte por chamada: ~45% da janela; o resto é prompt e resposta."""
    from .. import design
    janela = await design._janela(run["spec"]["provider"], run["spec"]["model"]) or config.NUM_CTX
    return max(6_000, min(60_000, int(janela * 0.45 * config.CHARS_POR_TOKEN)))


def json_de(texto: str, tipo: type = dict):
    return pesquisa._json(texto, tipo)

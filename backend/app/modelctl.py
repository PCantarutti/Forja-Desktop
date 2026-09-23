"""Ciclo de vida dos modelos: carregar, descarregar e trocar entre tarefas.

Existe para que uma máquina apertada consiga rodar o Maestro: o Worker de uma tarefa usa um modelo,
a tarefa seguinte usa outro, e entre as duas a VRAM é liberada. O estado do projeto não participa
disso — ele mora no SQLite (`taskdb`), então descarregar um modelo no meio do caminho não perde
nada. É o requisito que sustenta "IA local em máquina com recursos limitados".

Nem todo backend sabe fazer o mesmo: o llama.cpp que o Forja sobe é um processo nosso e dá para
matar; Ollama e LM Studio gerenciam os próprios modelos; um provedor de nuvem não tem nada para
descarregar. `CAPS` diz o que cada um aceita, e quem chama pergunta antes em vez de supor.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable

from . import config
from .tools import ToolError

try:  # localai só existe no Forja desktop; no Docker não há modelo local embutido
    from . import localai
except ImportError:  # pragma: no cover
    localai = None

# O que cada tipo de backend sabe fazer. Faltar aqui não é erro: é motivo para não tentar.
CAPS: dict[str, frozenset[str]] = {
    "llamacpp": frozenset({"status", "load", "unload"}),  # processo nosso: sobe e mata
    "ollama": frozenset({"status"}),                      # ele gerencia os próprios modelos
    "lmstudio": frozenset({"status"}),
    "openai": frozenset(),                                # nuvem: não há o que descarregar
}

# O que fazer com o modelo depois que a tarefa termina.
LIFECYCLES = ("persistent", "unload_after_task", "unload_clear", "restart_after_task")
LIBERA_TIMEOUT = 30   # s esperando a VRAM voltar depois de matar o processo
LOAD_TIMEOUT = 900  # o mesmo teto do localai._wait_ready


def _tipo(provider: str) -> str:
    if provider == config.LOCAL_PROVIDER["id"]:
        return config.LOCAL_PROVIDER["type"]
    return str(config.PROVIDERS.get(provider, {}).get("type") or "openai")


def caps_of(provider: str) -> frozenset[str]:
    return CAPS.get(_tipo(provider), frozenset())


def gerenciavel(spec: dict | None) -> bool:
    """Este slot tem ciclo de vida que o Forja controla?"""
    return bool(spec) and localai is not None and "load" in caps_of(spec.get("provider", ""))


def lifecycle() -> str:
    lc = str(getattr(config, "MODEL_LIFECYCLE", "persistent"))
    return lc if lc in LIFECYCLES else "persistent"


def pode_trocar() -> bool:
    """Trocar de modelo entre tarefas só faz sentido com um Worker por vez: com dois rodando, matar
    o llama-server derrubaria a geração do outro no meio."""
    return int(getattr(config, "MAX_WORKERS", 1)) <= 1


def status() -> dict:
    """Estado para a interface. Não levanta: o painel tem que desenhar mesmo sem IA local."""
    if localai is None:
        return {"running": False, "alias": "", "ctx": None, "manageable": False}
    try:
        st = localai.status()
        hw = localai.hardware()
    except Exception:  # pragma: no cover - hardware é consulta de sistema, não vale derrubar a UI
        return {"running": False, "alias": "", "ctx": None, "manageable": True}
    return {"running": bool(st.get("running")), "alias": st.get("alias") or "",
            "ctx": st.get("ctx"), "loading": st.get("loading") or {},
            "vram": hw.get("vram"), "vram_free": hw.get("vram_free"),
            "ram": hw.get("ram"), "ram_free": hw.get("ram_free"),
            "lifecycle": lifecycle(), "manageable": True}


def path_for(alias: str) -> str:
    """Arquivo .gguf de um alias. '' quando não está em nenhuma pasta configurada.

    O alias é o que o llama-server publica e o que o usuário escolhe no slot do subagente; o caminho
    é o que o `load` precisa. Sem esta tradução, um slot local só funcionaria com o modelo que já
    estivesse carregado — que é justamente a limitação que esta etapa remove.
    """
    if localai is None or not alias:
        return ""
    for m in localai.scan():
        if m.get("kind") == "image":
            continue
        if localai.alias_of(m["path"]) == alias or m.get("name") == alias:
            return m["path"]
    return ""


def janela(spec: dict | None) -> int | None:
    """Janela por requisição de um slot local (carregado ou não). None fora do llama.cpp."""
    if not gerenciavel(spec):
        return None
    caminho = path_for(spec.get("model", ""))
    return localai.ctx_de(caminho) if caminho else None


def janela_curta(spec: dict | None, minimo: int, papel: str) -> str:
    """Mensagem de recusa quando o modelo local tem janela menor que o mínimo do papel; '' se serve."""
    n = janela(spec)
    if n is None or n >= minimo:
        return ""
    return (f"O modelo local '{spec['model']}' está com janela de {n:,} tokens por requisição, e "
            f"{papel} precisa de pelo menos {minimo:,}. Aumente o contexto dele no painel IA local "
            "(ou reduza o 'parallel', que divide a janela entre os slots).").replace(",", ".")


def carregado(spec: dict) -> bool:
    return bool(localai and localai.status().get("alias") == spec.get("model"))


async def caiu(spec: dict | None, espera: float = 3.0) -> bool:
    """O servidor do modelo deste slot saiu do ar (morreu ou travou)?

    Pergunta ao próprio servidor, não só ao processo: no Windows, logo depois de o processo morrer o
    handle ainda responde "vivo" por um instante — e é exatamente nesse instante que o erro de conexão
    chega. Espera até `espera` segundos pela resposta antes de concluir."""
    if not gerenciavel(spec):
        return False
    fim = asyncio.get_running_loop().time() + espera
    while True:
        if not carregado(spec):
            return True
        if await asyncio.to_thread(localai._health):
            return False
        if asyncio.get_running_loop().time() >= fim:
            return True  # processo de pé mas sem responder: travado conta como caído
        await asyncio.sleep(0.3)


async def recupera(spec: dict, cancel: asyncio.Event | None = None) -> AsyncIterator[dict]:
    """Põe o modelo do slot de volta no ar depois de uma queda: mata o que sobrou (travado) e recarrega."""
    if carregado(spec):
        async for ev in unload("recuperação"):
            yield ev
    async for ev in ensure(spec, {}, cancel):
        yield ev


def _evento(fase: str, **extra) -> dict:
    return {"type": "model", "phase": fase, **extra}


async def ensure(spec: dict, out: dict | None = None,
                 cancel: asyncio.Event | None = None, temporario: dict | None = None) -> AsyncIterator[dict]:
    """Garante que o modelo do slot esteja no ar, trocando o que estiver carregado se preciso.

    Gerador assíncrono como `subagents.run`: os eventos saem enquanto acontecem, para a interface
    mostrar "Descarregando X… Carregando Y…" em vez de congelar por minutos.
    """
    out = out if out is not None else {}
    out.setdefault("swapped", False)
    if not gerenciavel(spec):
        return
    if carregado(spec):
        # Com janela pedida (`temporario["ctx"]`): recarrega se a atual não cabe o pedido, ou se é tão
        # maior que só ocupa VRAM que faltaria ao encoder de visão.
        ctx_agora, ctx_pedido = int(localai.status().get("ctx") or 0), int((temporario or {}).get("ctx") or 0)
        if not ctx_pedido or ctx_pedido <= ctx_agora <= ctx_pedido * 2:
            return

    alvo = spec["model"]
    caminho = path_for(alvo)
    if not caminho:
        raise ToolError(
            f"O modelo local '{alvo}' não está em nenhuma pasta configurada, então não dá para "
            "carregá-lo. Ajuste o slot em Configurações › Subagentes ou adicione a pasta em IA local.")
    if localai.image_busy():
        raise ToolError("Uma imagem está sendo gerada agora e ocupa a mesma VRAM. "
                        "Espere terminar para trocar de modelo.")

    anterior = localai.status().get("alias") or ""
    out.update(swapped=True, previous=anterior, model=alvo)
    if anterior:
        yield _evento("unloading", previous=anterior, model=alvo)
    yield _evento("loading", previous=anterior, model=alvo)

    # `load` é síncrono e segura o _proc_lock por até LOAD_TIMEOUT; numa thread o event loop segue
    # publicando eventos e atendendo o botão Parar. Ele já descarrega o anterior sozinho.
    carga = (localai.load, caminho, None, temporario) if temporario else (localai.load, caminho)
    tarefa = asyncio.create_task(asyncio.to_thread(*carga))
    if cancel is not None:
        espera = asyncio.ensure_future(cancel.wait())
        feitos, _ = await asyncio.wait({tarefa, espera}, return_when=asyncio.FIRST_COMPLETED)
        espera.cancel()
        if tarefa not in feitos:
            localai.cancel_load()  # derruba o processo; o await abaixo colhe o resultado/erro
    try:
        await tarefa
    except ToolError:
        yield _evento("error", model=alvo)
        raise
    except Exception as e:
        yield _evento("error", model=alvo)
        raise ToolError(f"Falha ao carregar '{alvo}': {e}") from e
    yield _evento("ready", previous=anterior, model=alvo)


async def unload(motivo: str = "") -> AsyncIterator[dict]:
    """Libera a VRAM. Só faz sentido onde o Forja é dono do processo (CAPS)."""
    if localai is None or not localai.status().get("running"):
        return
    anterior = localai.status().get("alias") or ""
    yield _evento("unloading", previous=anterior, model="", reason=motivo)
    await asyncio.to_thread(localai.unload)
    yield _evento("unloaded", previous=anterior, model="", reason=motivo)


def _vram_livre() -> int | None:
    """Leitura nova, sem o cache curto do `devices()`: é a espera pela memória que depende dela."""
    limpa = getattr(getattr(localai, "_devices", None), "cache_clear", None)
    if limpa:
        limpa()
    try:
        return localai.hardware().get("vram_free")
    except Exception:  # pragma: no cover - consulta de sistema
        return None


async def libera(motivo: str = "unload_clear") -> AsyncIterator[dict]:
    """Descarrega e ESPERA a memória voltar. Matar o processo não devolve a VRAM na mesma hora: o
    driver libera depois, e o próximo modelo carregado nesse meio-tempo acharia a placa cheia e
    cairia para a CPU. Aqui só segue quando a leitura da VRAM livre para de subir."""
    antes = _vram_livre()
    async for ev in unload(motivo):
        yield ev
    yield _evento("clearing", reason=motivo)
    ultima, estavel = _vram_livre(), 0
    for _ in range(LIBERA_TIMEOUT * 2):
        await asyncio.sleep(0.5)
        agora = _vram_livre()
        if agora is None:
            break
        # tolerância: a VRAM livre oscila uns MB com o resto do sistema (navegador, desktop)
        estavel = estavel + 1 if abs(agora - (ultima or 0)) < (64 << 20) else 0
        ultima = agora
        if estavel >= 3:  # 1,5 s sem mudar: o driver terminou de devolver
            break
    liberado = (ultima - antes) if (ultima is not None and antes is not None) else None
    yield _evento("cleared", reason=motivo, freed=liberado)


async def after_task(spec: dict | None, maestro: dict | None = None) -> AsyncIterator[dict]:
    """Aplica a estratégia escolhida depois que uma tarefa termina.

    `persistent` mantém o modelo no ar — é o certo quando a próxima tarefa usa o mesmo, porque
    recarregar custa minutos. `unload_after_task` libera a VRAM na hora, que é o que faz sentido em
    máquina apertada ou quando a próxima tarefa pede outro modelo.

    `maestro` é o slot de quem está orquestrando. Numa máquina inteiramente local ele roda no MESMO
    llama-server do Worker, e descarregar ali deixaria a Maestro sem servidor no passo seguinte —
    ela perderia a conexão justamente ao ler o resultado da tarefa que acabou de terminar.
    """
    estrategia = lifecycle()
    if estrategia == "persistent" or not gerenciavel(spec):
        return
    # Maestro no MESMO llama-server (mesmo GGUF do Worker): nenhuma estratégia mexe nele. Descarregar
    # a deixaria sem servidor; reiniciar recarregaria o modelo inteiro a cada tarefa só para zerar um
    # cache que ela volta a encher na rodada seguinte.
    if maestro and gerenciavel(maestro) and carregado(maestro):
        return
    if estrategia == "restart_after_task":
        # Processo novo com o mesmo modelo: cache e fragmentação zerados, e o modelo de volta no ar
        # para a próxima tarefa.
        if not carregado(spec):
            return
        yield _evento("restarting", model=spec["model"])
        async for ev in unload("restart_after_task"):
            yield ev
        async for ev in ensure(spec):
            yield ev
        return
    if estrategia == "unload_clear":
        async for ev in libera("unload_clear"):
            yield ev
        return
    async for ev in unload("unload_after_task"):
        yield ev

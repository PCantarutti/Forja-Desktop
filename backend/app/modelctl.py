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
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from . import config, metricas
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
    if anterior:  # E4: o cache das conversas no slot vai para o disco antes de o modelo sair
        from . import kvcache
        await asyncio.to_thread(kvcache.salvar_todos)
    if anterior:
        yield _evento("unloading", previous=anterior, model=alvo)
    yield _evento("loading", previous=anterior, model=alvo)

    # `load` é síncrono e segura o _proc_lock por até LOAD_TIMEOUT; numa thread o event loop segue
    # publicando eventos e atendendo o botão Parar. Ele já descarrega o anterior sozinho.
    carga = (localai.load, caminho, None, temporario) if temporario else (localai.load, caminho)
    t0 = time.monotonic()
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
    metricas.registra("troca", de=anterior, para=alvo, segundos=round(time.monotonic() - t0, 2))  # E0
    yield _evento("ready", previous=anterior, model=alvo)


async def unload(motivo: str = "") -> AsyncIterator[dict]:
    """Libera a VRAM. Só faz sentido onde o Forja é dono do processo (CAPS)."""
    if localai is None or not localai.status().get("running"):
        return
    anterior = localai.status().get("alias") or ""
    yield _evento("unloading", previous=anterior, model="", reason=motivo)
    from . import kvcache
    await asyncio.to_thread(kvcache.salvar_todos)  # E4: salvar antes de perder
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


# ------------------------------------------------------------------ política de execução (E4)
# Uma função decide onde cada chamada ao LLM roda. Regra do plano inteiro: chamada auxiliar não troca o
# modelo carregado nem toma o slot do principal por conta própria. O Worker é a única exceção (pode trocar),
# e a E0 mediu o custo: numa máquina de 12 GB a troca Maestro <-> Worker gastou 39% do relógio (carga mais o
# Maestro reprocessando o contexto inteiro a cada volta, porque os híbridos não restauram do disco).

PAPEIS = ("principal", "worker", "explorador", "revisor", "visual", "lateral", "compactar", "embeddings")
SLOT_PRINCIPAL = 0      # o principal (agente/Maestro) fica sempre no slot 0, com cache_prompt
SLOT_AUXILIAR = 1       # auxiliar vai para o 1 quando o servidor tem mais de um slot
_SLOTS: dict[int, int] = {}  # pid do llama-server -> nº de slots (pergunta ao servidor uma vez)


@dataclass
class Rota:
    caminho: str            # mesmo-slot | outro-slot | mesmo-slot-sequencial | modelo-do-principal |
                            # trocar-modelo | nuvem | externo | pular
    spec: dict | None       # o modelo que de fato atende (None em "pular")
    slot: int | None        # id_slot do llama-server (None: não fixa)
    motivo: str

    def texto(self, papel: str) -> str:
        return f"{papel} → {self.caminho} ({self.motivo})"


def slots_do_servidor() -> int:
    """Quantos slots o llama-server carregado tem: o -np da configuração, ou o servidor diz (0 = automático)."""
    if localai is None:
        return 1
    st = localai.status()
    if not st.get("running"):
        return 1
    n = int((st.get("params") or {}).get("parallel") or 0)
    if n >= 1:
        return n
    pid = int(st.get("pid") or 0)
    if pid not in _SLOTS:
        try:
            import httpx
            r = httpx.get(f"http://127.0.0.1:{config.LOCAL_PORT}/slots", timeout=3)
            _SLOTS[pid] = max(1, len(r.json())) if r.status_code == 200 else 1
        except Exception:
            _SLOTS[pid] = 1
    return _SLOTS[pid]


def _local_carregado() -> dict | None:
    if localai is None:
        return None
    st = localai.status()
    return {"provider": config.LOCAL_PROVIDER["id"], "model": st["alias"]} if st.get("running") and st.get("alias") else None


def _nuvem(papel: str) -> dict | None:
    """O slot "nuvem" dos subagentes, se o usuário liberou este papel para a nuvem."""
    if not (getattr(config, "NUVEM_POR_PAPEL", {}) or {}).get(papel):
        return None
    spec = (getattr(config, "SUBAGENTS", {}) or {}).get("nuvem") or {}
    return spec if spec.get("provider") and spec.get("model") else None


def como_rodar(papel: str, pedido: dict | None) -> Rota:
    """Onde a chamada de `papel` roda, para quem pediu `pedido` ({provider, model}). Nunca carrega nada:
    quem recebe "trocar-modelo" chama `ensure`. `metricas` registra cada decisão (E10)."""
    rota = _decide(papel, pedido)
    from . import perfis  # E4: o motivo cita o perfil ativo (a E10 mostra por execução)
    rota.motivo = f"{rota.motivo} [perfil {perfis.rotulo()}]"
    metricas.registra("rota", papel=papel, caminho=rota.caminho, modelo=(rota.spec or {}).get("model"),
                      slot=rota.slot, motivo=rota.motivo)
    return rota


def _decide(papel: str, pedido: dict | None) -> Rota:
    carregado = _local_carregado()
    pedido = pedido if pedido and pedido.get("model") else None
    if papel == "principal":
        if pedido and gerenciavel(pedido):
            return Rota("mesmo-slot", pedido, SLOT_PRINCIPAL, "principal fica no slot fixo, com cache_prompt")
        return Rota("externo", pedido, None, "o provedor cuida do cache")
    if pedido and not gerenciavel(pedido):
        return Rota("externo", pedido, None, "provedor fora do Forja (nuvem, Ollama, LM Studio)")
    if pedido and carregado and pedido["model"] == carregado["model"]:
        n = slots_do_servidor()
        if n > 1:
            return Rota("outro-slot", pedido, SLOT_AUXILIAR, f"mesmo modelo, slot {SLOT_AUXILIAR} de {n}")
        return Rota("mesmo-slot-sequencial", pedido, SLOT_PRINCIPAL,
                    "mesmo modelo com um slot só (-np 1): espera o principal e divide o cache com ele")
    # Daqui em diante o pedido é outro modelo local (ou nenhum): a regra é não trocar.
    if papel == "worker" and pedido:
        return Rota("trocar-modelo", pedido, None, "o Worker pode trocar de modelo (a E0 mediu: troca "
                                                    "+ reprocessar o Maestro custam minutos por tarefa)")
    if nuvem := _nuvem(papel):
        return Rota("nuvem", nuvem, None, "este papel está liberado para a nuvem nas configurações")
    if pedido and not carregado:
        return Rota("trocar-modelo", pedido, None, "nenhum modelo carregado: carregar não derruba ninguém")
    if papel in ("visual", "embeddings"):
        return Rota("pular", None, None, "sem VRAM para carregar o modelo junto: o Forja roda um modelo por "
                                         "vez, e trocar no meio do trabalho derrubaria o principal")
    if carregado:
        n = slots_do_servidor()
        return Rota("modelo-do-principal", carregado, SLOT_AUXILIAR if n > 1 else SLOT_PRINCIPAL,
                    f"o pedido ({pedido['model']}) não está carregado e chamada auxiliar não troca de modelo"
                    if pedido else "usa o modelo carregado")
    return Rota("pular", None, None, "nenhum modelo disponível")

# ------------------------------------------------------------------ descarga por ociosidade (E4)
OCIOSO: dict = {}   # último descarregado por ociosidade: {"alias", "path", "quando", "carga_s"}


def em_uso() -> bool:
    """Algo usando o modelo agora: execução de agente/Maestro/Worker, subagente em segundo plano, carga ou
    geração de imagem. Execução parada numa aprovação conta como ociosa só depois do dobro do tempo."""
    from . import agent, llm
    limite = int(getattr(config, "DESCARREGAR_OCIOSO_MIN", 15)) * 60
    for run in list(agent.RUNS.values()):
        if run.finished:
            continue
        if run.approvals and time.monotonic() - llm.ULTIMO_USO["t"] < 2 * limite:
            return True
        if not run.approvals:
            return True
    return bool(localai and (localai.image_busy() or localai._loading))


def precisa_descarregar() -> bool:
    minutos = int(getattr(config, "DESCARREGAR_OCIOSO_MIN", 15))
    if minutos <= 0 or localai is None or not localai.status().get("running"):
        return False
    from . import llm
    return not em_uso() and time.monotonic() - llm.ULTIMO_USO["t"] > minutos * 60


async def vigia_ociosidade(intervalo: float = 60) -> None:
    """A cada minuto, como a varredura do navegador: modelo local sem uso há N minutos sai da VRAM (o cache das
    conversas vai para o disco antes). A próxima mensagem carrega de novo (agent._garante_modelo)."""
    while True:
        await asyncio.sleep(intervalo)
        try:
            if precisa_descarregar():
                st = localai.status()
                from pathlib import Path
                gb = Path(st["path"]).stat().st_size / 2**30 if st.get("path") else 0
                OCIOSO.update(alias=st.get("alias"), path=st.get("path"), quando=time.time(),
                              carga_s=round(float(localai.read_config().get("speed") or 0) * gb))  # s/GB medido
                async for _ in unload("ociosidade"):
                    pass
                metricas.registra("ociosidade", alias=st.get("alias"))
        except Exception as e:  # a vigia nunca morre por um erro de uma volta
            print(f"Forja: descarga por ociosidade falhou: {e}", flush=True)


def recarregar_sob_demanda(spec: dict | None) -> bool:
    """A conversa quer um modelo local e não há nenhum carregado (descarregado por ociosidade, ou o app
    acabou de abrir): carregar de novo não derruba ninguém."""
    return bool(spec) and gerenciavel(spec) and localai is not None and not localai.status().get("running")

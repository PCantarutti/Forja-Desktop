"""Perfis de hardware (E4): o Forja percebe o que cabe na máquina e escolhe os valores sozinho.

Um perfil é só um conjunto de valores, sem lógica nova: a `como_rodar` e o resto da E4 leem daqui. O que o
usuário mudou à mão continua valendo por cima (as configurações só guardam o que foi mexido).

Números da E0 (Arc B580 12 GB, 30 GB RAM): Maestro e Worker no mesmo modelo terminaram o bench em 55,6 min;
em modelos diferentes, 112,7 min (a troca e o reprocessamento do contexto do Maestro dominaram). Paralelo no
mesmo modelo rendeu 1,45× com janela de 8k e 1,03× com 32k.
"""
from __future__ import annotations

import re

from . import config

PERFIS = ("auto", "performance", "balanced", "low_vram")
NOMES = {"auto": "Automático", "performance": "Performance", "balanced": "Balanced", "low_vram": "Low VRAM"}
VALORES = {
    # max_workers e sessões em paralelo ficam registrados para a E7 (paralelismo seguro, com worktree por Worker):
    # ligar Workers paralelos antes dela escreveria na mesma árvore ao mesmo tempo.
    "performance": {"kv": "f16", "cache_disco_gb": 4.0, "descarregar_ocioso_min": 30, "fator_tetos": 1.5,
                    "um_modelo_so": False, "max_workers": 3, "sessoes_paralelo": 4},
    "balanced": {"kv": "q8_0", "cache_disco_gb": 4.0, "descarregar_ocioso_min": 15, "fator_tetos": 1.0,
                 "um_modelo_so": False, "max_workers": 2, "sessoes_paralelo": 2},
    "low_vram": {"kv": "q8_0", "cache_disco_gb": 8.0, "descarregar_ocioso_min": 5, "fator_tetos": 0.7,
                 "um_modelo_so": True, "max_workers": 1, "sessoes_paralelo": 1},
}
GOVERNADOS = ("cache_disco_gb", "descarregar_ocioso_min")  # configurações que o perfil preenche quando não mexidas
BENCH_MESMO_MODELO = round(112.7 / 55.6, 1)  # E0: ~2× mais rápido com um modelo só para Maestro e Worker

_INTEGRADA = re.compile(r"(Radeon\(TM\) Graphics|Radeon Graphics|UHD Graphics|Iris|Intel\(R\) Graphics)\s*$", re.I)
_VIGENTE: dict = {}   # {"perfil", "motivo"}: reavaliado ao carregar/trocar o modelo ou mudar uma GPU


def vram_dedicada(hw: dict) -> int:
    """Maior VRAM entre as GPUs ligadas que não são integradas (a integrada divide a RAM, e o Vulkan a mostra como
    se fosse VRAM: nesta máquina, "16,8 GB" de uma Radeon integrada ao lado da Arc de 12 GB)."""
    ligadas = [g for g in hw.get("gpus") or [] if g.get("enabled", True)]
    dedicadas = [g for g in ligadas if not _INTEGRADA.search(g.get("name") or "")]
    return max((g.get("total") or 0 for g in (dedicadas or ligadas)), default=0)


def automatico(hw: dict, modelo_gb: float | None) -> tuple[str, str]:
    """(perfil, motivo) pela VRAM dedicada, a RAM e o tamanho do modelo principal com o contexto."""
    vram = vram_dedicada(hw) / 2**30
    if not vram:
        return "low_vram", "sem GPU dedicada"
    if modelo_gb is None:
        perfil = "performance" if vram >= 20 else "balanced" if vram >= 10 else "low_vram"
        return perfil, f"{vram:.0f} GB de VRAM, nenhum modelo carregado ainda"
    folga = vram - modelo_gb
    if modelo_gb > vram * 0.9:
        return "low_vram", f"{vram:.0f} GB de VRAM, modelo de {modelo_gb:.1f} GB (parte roda na CPU)"
    if vram < 10:  # 8 GB: um modelo só com cache é quase sempre mais rápido que dois se revezando
        return "low_vram", f"{vram:.0f} GB de VRAM, modelo de {modelo_gb:.1f} GB (sem folga para um segundo)"
    if vram >= 20 and folga >= vram * 0.4:
        return "performance", f"{vram:.0f} GB de VRAM, modelo de {modelo_gb:.1f} GB (sobra para mais)"
    return "balanced", f"{vram:.0f} GB de VRAM, modelo de {modelo_gb:.1f} GB (cabe inteiro)"


def _modelo_gb() -> float | None:
    """Tamanho estimado do modelo principal na GPU com o contexto dele (o carregado, ou o último usado)."""
    try:
        from . import localai
        st = localai.status()
        path = st.get("path") if st.get("running") else localai.read_config().get("last")
        if not path:
            return None
        p = localai.params(path)
        e = localai.estimate(path, {**p, "ngl": 999, "n_cpu_moe": 0})  # "caberia inteiro?"
        return e["gpu"] / 2**30 if e.get("ok") else None
    except Exception:
        return None


def reavaliar() -> dict:
    """Recalcula o Automático. Chamado ao carregar/descarregar modelo e ao ligar/desligar GPU — não no meio de
    uma execução (a `como_rodar` lê o valor guardado)."""
    escolhido = str(getattr(config, "PERFIL_HARDWARE", "auto") or "auto")
    if escolhido != "auto" and escolhido in VALORES:
        _VIGENTE.update(perfil=escolhido, motivo="escolhido nas configurações", auto=False)
        return dict(_VIGENTE)
    try:
        from . import localai
        hw = localai.hardware()
    except Exception:
        hw = {"gpus": []}
    perfil, motivo = automatico(hw, _modelo_gb())
    _VIGENTE.update(perfil=perfil, motivo=motivo, auto=True)
    return dict(_VIGENTE)


def vigente() -> dict:
    if not _VIGENTE:
        reavaliar()
    return dict(_VIGENTE)


def valores() -> dict:
    return VALORES[vigente()["perfil"]]


def rotulo() -> str:
    v = vigente()
    return f"{NOMES[v['perfil']]}{' (automático)' if v.get('auto') else ''}"


def recomendados(limite: int = 5) -> list[dict]:
    """GGUFs de chat já baixados que cabem inteiros na GPU dedicada (sem nada na CPU), maiores primeiro. O Forja
    nunca troca o modelo sozinho: a tela lista e o usuário escolhe."""
    from . import localai
    vram = vram_dedicada(localai.hardware())
    if not vram:
        return []
    out = []
    for m in localai.scan():
        if m.get("kind") != "chat" or "mmproj" in m["name"].lower():
            continue
        try:
            e = localai.estimate(m["path"], {**localai.params(m["path"]), "ngl": 999, "n_cpu_moe": 0})
        except Exception:
            continue
        if e.get("ok") and e["gpu"] <= vram * 0.9:
            out.append({"nome": m["name"], "path": m["path"], "gb": round(e["gpu"] / 2**30, 1)})
    return sorted(out, key=lambda x: -x["gb"])[:limite]
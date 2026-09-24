"""LoRAs de vídeo (Wan) no sd.cpp: o que um arquivo é, para qual modelo ele serve e como entra no prompt.

Nada vem de lista de nomes: um .safetensors é LoRA se tem tensores `lora_down`/`lora_up` (ou `lora_A`/`lora_B`);
é de Wan se tem `blocks.N.cross_attn`; e serve para um modelo quando a dimensão do `self_attn.q` dele é a do
modelo (5120 no 14B, 3072 no 5B, 1536 no 1.3B — lido dos dois arquivos, não escrito aqui).

O sd.cpp lê a LoRA do prompt: `<lora:caminho:peso>`, e `<lora:|high_noise|caminho:peso>` para o modelo
HighNoise do Wan2.2 A14B. O caminho é relativo a `--lora-model-dir` e não pode ter ":" — a regex dele é
`<lora:([^:>]+):([^>]+)>`, então "C:\\..." não passa. Por isso as LoRAs de uma geração vão como caminho
relativo à pasta que todas elas têm em comum.
"""
from __future__ import annotations

import fnmatch
import functools
import json
import os
import re
import struct
from pathlib import Path

from .tools import ToolError

MAX_CABECALHO = 64 << 20  # cabeçalho maior que isso não é de safetensors de verdade


def _normal(nome: str) -> str:
    return re.sub(r"[^a-z0-9]", "", Path(nome).name.lower())


def cabecalho(path: str) -> dict:
    """O JSON do começo de um .safetensors (nomes, tipos e formas dos tensores), sem ler os pesos."""
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if not 0 < n < MAX_CABECALHO:
            raise ValueError("cabeçalho inválido")
        return json.loads(f.read(n))


@functools.lru_cache(maxsize=512)
def _info(path: str, _stamp: tuple) -> dict | None:
    try:
        h = cabecalho(path)
    except (OSError, ValueError, struct.error):
        return None
    h.pop("__metadata__", None)
    nomes = list(h)
    if not any(re.search(r"\.lora_(down|up|A|B)\b", k) for k in nomes):
        return None

    def forma(sufixos: tuple[str, ...]) -> list[int]:
        k = next((k for k in nomes if k.endswith(sufixos)), None)
        return list(h[k].get("shape") or []) if k else []

    up = forma(("self_attn.q.lora_up.weight", "self_attn.q.lora_B.weight"))
    down = forma(("self_attn.q.lora_down.weight", "self_attn.q.lora_A.weight"))
    n = _normal(path)
    passos = re.search(r"(\d+)step", n)
    return {
        "wan": any("cross_attn" in k and "blocks." in k for k in nomes),
        "dim": up[0] if up else 0,  # [saída, rank] no formato do torch: a saída do q é a dimensão do modelo
        "rank": down[0] if down else 0,
        # As LoRAs do A14B vêm em par, e quem diz qual é qual é o nome (é assim que o lightx2v publica).
        "ruido": "high" if "highnoise" in n else "low" if "lownoise" in n else "",
        # Destilada em N passos: o nome diz quantos; as de passos também destilam o CFG (roda com 1).
        "passos": int(passos.group(1)) if passos else 0,
    }


def info_lora(path: str) -> dict | None:
    """None se não for LoRA (ou não der para ler)."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return _info(str(path), (st.st_size, int(st.st_mtime)))


def dim_do_modelo(path: str) -> int:
    """A dimensão do modelo de difusão, pela forma do `self_attn.q` do primeiro bloco."""
    if str(path).lower().endswith(".gguf"):
        from .localai import gguf_info
        formas = gguf_info(str(path)).get("formas") or {}
        q = next((f for k, f in formas.items() if k.endswith("self_attn.q.weight")), None)
        return int(q[0]) if q else 0  # no GGUF a ordem é a do ggml: [entrada, saída]
    try:
        h = cabecalho(str(path))
    except (OSError, ValueError, struct.error):
        return 0
    k = next((k for k in h if k.endswith("blocks.0.self_attn.q.weight")), None)
    return int(h[k]["shape"][1]) if k else 0  # torch: [saída, entrada]


def compativel(info: dict | None, dim: int) -> bool:
    return bool(info and info["wan"] and dim and info["dim"] == dim)


def tags(loras: list[dict], com_alto_ruido: bool) -> tuple[str, str]:
    """(pasta para --lora-model-dir, texto que vai no fim do prompt). As que o nome marca como HighNoise
    vão para o modelo HighNoise quando ele existe; num modelo sem par, a marca não quer dizer nada."""
    caminhos = [os.path.abspath(str(l["path"])) for l in loras]
    for c in caminhos:
        if not Path(c).is_file():
            raise ToolError(f"LoRA não encontrada: {c}")
    try:
        pasta = os.path.commonpath([os.path.dirname(c) for c in caminhos])
    except ValueError:
        raise ToolError("As LoRAs precisam estar no mesmo disco: o sd.cpp as lê a partir de uma pasta só.")
    partes = []
    for l, c in zip(loras, caminhos):
        rel = os.path.relpath(c, pasta)
        if ":" in rel:
            raise ToolError(f"Caminho de LoRA que o sd.cpp não aceita: {rel}")
        alto = com_alto_ruido and (info_lora(c) or {}).get("ruido") == "high"
        partes.append(f"<lora:{'|high_noise|' if alto else ''}{rel}:{float(l.get('peso') or 1.0):g}>")
    return pasta, "".join(partes)


# Aceleradores: LoRAs de destilação (poucos passos, CFG 1) publicadas pelo lightx2v para cada Wan. Aqui só a
# curadoria (repositório e família do arquivo); versões e tamanhos vêm do Hugging Face. CausVid fica de fora:
# ele deixa o modelo causal (quadro a quadro), e o sd.cpp gera o vídeo inteiro de uma vez (issue #973).
ACELERADORES = {
    "wan21_t2v": [("lightx2v/Wan2.1-Distill-Loras", "wan2.1_t2v_14b_lora_*step*.safetensors")],
    "wan21_i2v": [("lightx2v/Wan2.1-Distill-Loras", "wan2.1_i2v_lora_*step*.safetensors")],
    # FLF2V não tem destilado próprio; o do T2V 14B (mesma base) funciona: validado em 24/09/2026, 4 passos,
    # CFG 1, início e fim respeitados.
    "wan21_flf2v": [("lightx2v/Wan2.1-Distill-Loras", "wan2.1_t2v_14b_lora_*step*.safetensors")],
    "wan22_a14b_t2v": [("lightx2v/Wan2.2-Distill-Loras", "wan2.2_t2v_A14b_high_noise_lora_*step*.safetensors"),
                       ("lightx2v/Wan2.2-Distill-Loras", "wan2.2_t2v_A14b_low_noise_lora_*step*.safetensors")],
    "wan22_a14b_i2v": [("lightx2v/Wan2.2-Distill-Loras", "wan2.2_i2v_A14b_high_noise_lora_*step*.safetensors"),
                       ("lightx2v/Wan2.2-Distill-Loras", "wan2.2_i2v_A14b_low_noise_lora_*step*.safetensors")],
}


def mais_recente(arquivos: list[dict], glob: str) -> dict | None:
    """Do mesmo glob, a versão mais nova (o lightx2v põe a data no fim do nome: _1022, _1217)."""
    casam = [f for f in arquivos if fnmatch.fnmatch(Path(f["path"]).name.lower(), glob.lower())]
    return max(casam, key=lambda f: f["path"]) if casam else None

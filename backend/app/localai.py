"""IA local: llama.cpp (chat) e stable-diffusion.cpp (imagem) rodando dentro do Forja.

Três responsabilidades:

1. Runtimes — baixa e descompacta os binários oficiais do GitHub em %APPDATA%\\Forja\\runtimes.
   Nada vem no instalador: o usuário escolhe CPU, Vulkan (padrão, roda em qualquer GPU) ou CUDA.
2. Modelos — varre as pastas configuradas por .gguf e guarda os parâmetros de carga de cada um.
3. Servidor — sobe o `llama-server` com esses parâmetros. Ele fala OpenAI, então o resto do Forja
   (llm.py, agent.py) o usa como qualquer outro provedor, o `local`.

Como o llama-server é filho deste processo e o Electron mata a árvore do backend ao sair, o modelo
descarrega junto com o app.
"""
from __future__ import annotations

import functools
import json
import os
import re
import struct
import subprocess
import threading
import time
from pathlib import Path

import httpx

from . import config, downloads, native
from .tools import ToolError

RUNTIMES = config.DATA_DIR / "runtimes"
IMAGENS = config.DATA_DIR / "imagens"  # padrão das imagens do painel
LOG_DIR = config.DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "llama-server.log"

# Binários procurados dentro da pasta do runtime (o primeiro que existir) e repositório de origem.
# O sd.cpp renomeou sd.exe para sd-cli.exe; aceitamos os dois para não quebrar com builds antigos.
EXE = {"llama": ["llama-server"], "sd": ["sd-cli", "sd"]}
REPO = {"llama": "ggml-org/llama.cpp", "sd": "leejet/stable-diffusion.cpp"}
BACKENDS = ("vulkan", "cpu", "cuda")

# Regex do asset por runtime/backend. `extra` é baixado junto (runtime do CUDA).
ASSETS = {
    "llama": {
        "cpu":    (r"^llama-b\d+-bin-win-cpu-x64\.zip$", None),
        "vulkan": (r"^llama-b\d+-bin-win-vulkan-x64\.zip$", None),
        "cuda":   (r"^llama-b\d+-bin-win-cuda-\d+\.\d+-x64\.zip$", r"^cudart-llama-bin-win-cuda-\d+\.\d+-x64\.zip$"),
    },
    "sd": {
        "cpu":    (r"^sd-.*-bin-win-cpu-x64\.zip$", None),
        "vulkan": (r"^sd-.*-bin-win-vulkan-x64\.zip$", None),
        "cuda":   (r"^sd-.*-bin-win-cuda\d+-x64\.zip$", r"^cudart-sd-bin-win-cu\d+-x64\.zip$"),
    },
}

# ponytail: só Windows por enquanto — é o alvo do Forja Desktop. Linux/macOS: outro mapa de assets.
SUPPORTED = native.WINDOWS


# ------------------------------------------------------------------ local.json

DEFAULT_PARAMS = {
    "ctx": 8192,          # -c   tamanho do contexto
    "ngl": 999,           # -ngl camadas na GPU (999 = tudo que couber)
    "threads": 0,         # -t   0 = automático
    "batch": 0,           # -b   lote de avaliação
    "ubatch": 0,          # -ub  lote físico
    "parallel": 0,        # -np  previsões simultâneas (0 = o llama.cpp decide)
    "flash_attn": True,   # -fa
    "cache_type_k": "f16",
    "cache_type_v": "f16",
    "kv_unified": False,      # --kv-unified
    "no_kv_offload": False,   # --no-kv-offload (cache KV fora da GPU)
    "mlock": False,           # manter o modelo na memória (não vai para o swap)
    "mmap": True,             # mapear o arquivo em vez de copiar tudo para a RAM (padrão do llama.cpp)
    "seed": 0,                # 0 = aleatório
    "rope_freq_base": 0.0,
    "rope_freq_scale": 0.0,
    "ctx_checkpoints": 0,     # --ctx-checkpoints
    "n_cpu_moe": 0,           # --n-cpu-moe (camadas MoE na CPU)
    "n_expert": 0,            # nº de especialistas ativos (override do gguf)
    "mmproj": "",             # projetor multimodal: dá visão ao modelo
}

# Amostragem: padrão do llama.cpp, sobrescrito pelo que o próprio gguf recomenda (general.sampling.*).
INFERENCE_DEFAULTS = {
    "temperature": 0.8,
    "top_k": 40,
    "top_p": 0.95,
    "min_p": 0.05,
    "repeat_penalty": 1.0,
    "max_tokens": 0,        # 0 = sem limite
    "stop": [],
    "think": True,          # enable_thinking do template (modelos com raciocínio)
    "reasoning_budget": -1,  # -1 = sem teto de tokens de raciocínio
}
GGUF_SAMPLING = {"temp": "temperature", "top_k": "top_k", "top_p": "top_p", "min_p": "min_p"}


def inference_defaults(path: str = "") -> dict:
    d = dict(INFERENCE_DEFAULTS)
    for origem, destino in GGUF_SAMPLING.items():
        valor = (gguf_info(path)["sampling"] if path else {}).get(origem)
        if isinstance(valor, (int, float)):
            v = type(d[destino])(valor)
            d[destino] = round(v, 4) if isinstance(v, float) else v  # f32 do gguf vira 0.949999988...
    return d


DEFAULT_IMAGE = {
    "model": "", "vae": "", "clip_l": "", "t5xxl": "", "diffusion_model": "",
    "steps": 20, "cfg": 7.0, "width": 512, "height": 512, "sampler": "euler_a", "negative": "",
    "out_dir": "",  # vazio = %APPDATA%/Forja/imagens
}

_cfg_lock = threading.Lock()


def _blank() -> dict:
    return {"dirs": [], "models": {}, "image": dict(DEFAULT_IMAGE), "last": "", "speed": SEGUNDOS_POR_GB,
            "download_dir": "", "models_dir": ""}


def read_config() -> dict:
    try:
        data = json.loads(config.LOCAL_CONFIG.read_text("utf-8"))
    except (OSError, ValueError):
        return _blank()
    out = _blank()
    out.update({k: v for k, v in data.items() if k in out})
    out["image"] = {**DEFAULT_IMAGE, **(out.get("image") or {})}
    return out


def write_config(data: dict) -> dict:
    with _cfg_lock:
        config.LOCAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        tmp = config.LOCAL_CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(config.LOCAL_CONFIG)
    return data


def _patch(key: str, value) -> dict:
    data = read_config()
    data[key] = value
    return write_config(data)


def models_dir() -> str:
    """Pasta padrão dos modelos: a de Configurações, senão ~/Forja/modelos."""
    return read_config().get("models_dir") or str(config.MODELS_DIR)


def dirs() -> list[str]:
    """Pastas varridas: a padrão mais as que o usuário adicionou."""
    extra = [d for d in read_config()["dirs"] if d]
    return [models_dir(), *[d for d in extra if not _mesma_pasta(d, models_dir())]]


def set_paths(models: str = "", imagens: str = "") -> dict:
    """Pastas padrão da tela de Configurações. Cria o que não existe (dar erro por isso seria chato)."""
    data = read_config()
    for valor, chave in ((models, "models_dir"), (imagens, "image")):
        if not valor:
            continue
        try:
            Path(valor).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ToolError(f"Não deu para usar a pasta '{valor}': {e}")
        if chave == "image":
            data["image"] = {**data["image"], "out_dir": valor}
        else:
            data[chave] = valor
    write_config(data)
    return {"models_dir": models_dir(), "image_dir": read_config()["image"].get("out_dir") or str(IMAGENS),
            "dirs": dirs()}


def set_dirs(paths: list[str]) -> list[str]:
    clean: list[str] = []
    for p in paths:
        p = str(p).strip()
        if not p or p == str(config.MODELS_DIR):
            continue
        if not Path(p).is_dir():
            raise ToolError(f"Pasta não encontrada: {p}")
        if p not in clean:
            clean.append(p)
    _patch("dirs", clean)
    return dirs()


def defaults_for(path: str = "") -> dict:
    """Padrões que a interface mostra: os do llama.cpp, ajustados ao modelo quando dá para ler o gguf.

    É o que o LM Studio faz — o campo já vem com o valor de verdade, e só fica destacado o que o
    usuário mudou.
    """
    d = dict(DEFAULT_PARAMS)
    exe = find_exe("llama")
    if exe:
        do_help = help_defaults(str(exe))
        for key, flag in HELP_FLAG.items():
            if isinstance(do_help.get(flag), int) and do_help[flag] >= 0:
                d[key] = do_help[flag]
    if path:
        d["mmproj"] = projector_for(path)   # visão já vem ligada quando o mmproj está do lado
    info = gguf_info(path) if path else None
    if info and info["n_layer"]:
        d["ngl"] = info["n_layer"]                                   # tudo na GPU, como o LM Studio
        d["ctx"] = min(info["ctx_train"] or d["ctx"], 32768)         # a janela cheia costuma não caber
        d["n_expert"] = info["n_expert_used"] or 0
    return d


def overrides(path: str) -> dict:
    """Só o que o usuário mudou em relação ao padrão (o resto acompanha o padrão se ele mudar)."""
    d = defaults_for(path)
    salvo = read_config()["models"].get(str(path)) or {}
    return {k: v for k, v in salvo.items() if k in d and v != d[k]}


def params(path: str) -> dict:
    return {**defaults_for(path), **overrides(path)}


def save_params(path: str, patch: dict) -> dict:
    d = defaults_for(path)
    novo = {**overrides(path), **_clean_params(patch)}
    data = read_config()
    data["models"][str(path)] = {k: v for k, v in novo.items() if v != d[k]}  # padrão não vira override
    write_config(data)
    return {**d, **data["models"][str(path)]}


def _clean_params(patch: dict) -> dict:
    """Mantém só chaves conhecidas, no tipo do padrão (a UI manda string em campo numérico)."""
    out = {}
    for k, default in DEFAULT_PARAMS.items():
        if k not in patch:
            continue
        v = patch[k]
        try:
            out[k] = bool(v) if isinstance(default, bool) else type(default)(v)
        except (TypeError, ValueError):
            raise ToolError(f"Valor inválido para '{k}': {v!r}")
    return out


# ------------------------------------------------------------------ runtimes

def runtime_dir(kind: str, backend: str) -> Path:
    return RUNTIMES / kind / backend


def find_exe(kind: str) -> Path | None:
    """Binário instalado, preferindo o backend mais rápido disponível."""
    sufixo = ".exe" if native.WINDOWS else ""
    for backend in ("cuda", "vulkan", "cpu"):
        for name in EXE[kind]:
            exe = runtime_dir(kind, backend) / (name + sufixo)
            if exe.exists():
                return exe
    return None


def runtimes() -> dict:
    out = {}
    for kind in EXE:
        exe = find_exe(kind)
        out[kind] = {"installed": bool(exe), "exe": str(exe) if exe else "",
                     "backend": exe.parent.name if exe else "", "backends": list(BACKENDS)}
    return out


def _releases(kind: str, per_page: int = 12) -> list[dict]:
    """Últimos releases. Não dá para usar /releases/latest: o `latest` do llama.cpp é uma marcação
    sem binário — os builds ficam nas tags bXXXXX, que não são marcadas como latest."""
    url = f"https://api.github.com/repos/{REPO[kind]}/releases"
    r = httpx.get(url, timeout=20, follow_redirects=True, params={"per_page": per_page},
                  headers={"Accept": "application/vnd.github+json"})
    if r.status_code >= 400:
        raise ToolError(f"GitHub respondeu {r.status_code} ao procurar os releases do {kind}.")
    return r.json()


def _find_assets(kind: str, backend: str) -> tuple[str, list[str]]:
    """Release mais recente que tenha o build pedido, com as URLs (o CUDA leva o cudart junto)."""
    main_re, extra_re = ASSETS[kind][backend]
    for rel in _releases(kind):
        assets = rel.get("assets") or []
        main = next((a for a in assets if re.search(main_re, a["name"])), None)
        if not main:
            continue
        urls = [main["browser_download_url"]]
        if extra_re:
            extra = next((a for a in assets if re.search(extra_re, a["name"])), None)
            if not extra:
                continue
            urls.append(extra["browser_download_url"])
        return rel.get("tag_name", ""), urls
    raise ToolError(f"Nenhum release recente do {kind} tem build de {backend} para Windows x64.")


def install_runtime(kind: str, backend: str) -> dict:
    """Acha o asset do último release e baixa em background. Devolve o job."""
    if kind not in EXE:
        raise ToolError(f"Runtime desconhecido: {kind}")
    if backend not in BACKENDS:
        raise ToolError(f"Backend desconhecido: {backend}")
    if not SUPPORTED:
        raise ToolError("Download automático de runtime só está pronto para Windows. "
                        "Compile o llama.cpp/sd.cpp e aponte a pasta manualmente.")
    tag, urls = _find_assets(kind, backend)
    dest = runtime_dir(kind, backend)
    return downloads.start("runtime", f"{kind} {backend} {tag}", urls, dest, extract=True)


# ------------------------------------------------------------------ modelos no disco

SHARD = re.compile(r"^(?P<base>.+)-(?P<idx>\d{5})-of-(?P<total>\d{5})\.gguf$", re.I)
WEIGHTS = (".gguf", ".safetensors", ".ckpt")


def scan(exts: tuple[str, ...] = (".gguf",)) -> list[dict]:
    """Modelos nas pastas configuradas. Shards (00001-of-00003) viram um item só, o primeiro."""
    seen: dict[str, dict] = {}
    for folder in dirs():
        root = Path(folder)
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in exts:
                continue
            if f.name.lower().startswith("mmproj"):
                continue  # projetor de visão, não é modelo: entra no campo mmproj do modelo dele
            m = SHARD.match(f.name)
            if m and m.group("idx") != "00001":
                continue  # shard do meio: o primeiro já representa o conjunto
            try:
                size = f.stat().st_size
                mtime = f.stat().st_mtime
            except OSError:
                continue
            shards = int(m.group("total")) if m else 1
            if shards > 1:
                size = sum((f.with_name(f"{m.group('base')}-{i:05d}-of-{shards:05d}.gguf").stat().st_size
                            for i in range(1, shards + 1)
                            if f.with_name(f"{m.group('base')}-{i:05d}-of-{shards:05d}.gguf").exists()), 0)
            key = str(f)
            seen[key] = {"path": key, "name": m.group("base") if m else f.stem, "size": size,
                         "mtime": mtime, "shards": shards, "folder": str(root)}
    return sorted(seen.values(), key=lambda m: m["name"].lower())


# ---------------------------------------------------------------- metadados do gguf
# Só o que a interface precisa: arquitetura, camadas, cabeças e o tamanho de cada tensor (para a
# estimativa de memória). É leitura de cabeçalho — não abre o modelo nem toca no llama.cpp.

_FIXED = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}  # tipos de valor do GGUF
_UNPACK = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
# tipo do tensor -> (elementos por bloco, bytes por bloco)
_GGML = {0: (1, 4), 1: (1, 2), 2: (32, 18), 3: (32, 20), 6: (32, 22), 7: (32, 24), 8: (32, 34), 9: (32, 36),
         10: (256, 84), 11: (256, 110), 12: (256, 144), 13: (256, 176), 14: (256, 210), 15: (256, 292),
         16: (256, 66), 17: (256, 74), 18: (256, 98), 19: (256, 50), 20: (32, 18), 21: (256, 110),
         22: (256, 82), 23: (256, 136), 24: (1, 1), 25: (1, 2), 26: (1, 4), 27: (1, 8), 28: (1, 8),
         29: (256, 56), 30: (1, 2), 34: (256, 54), 35: (256, 66), 38: (32, 17)}
# bytes por elemento do cache KV, por tipo aceito no --cache-type-k/-v
KV_BYTES = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32, "q5_0": 22 / 32,
            "q4_1": 20 / 32, "q4_0": 18 / 32, "iq4_nl": 18 / 32}


def _string(f) -> str:
    return f.read(struct.unpack("<Q", f.read(8))[0]).decode("utf-8", "replace")


def _value(f, vtype: int):
    if vtype == 8:
        return _string(f)
    if vtype == 9:
        itype, count = struct.unpack("<IQ", f.read(12))
        return [_value(f, itype) for _ in range(count)]
    fmt = _UNPACK.get(vtype)
    if not fmt:
        raise ValueError(f"tipo GGUF desconhecido: {vtype}")
    return struct.unpack(fmt, f.read(_FIXED[vtype]))[0]


@functools.lru_cache(maxsize=32)
def _gguf(path: str, _stamp: tuple) -> dict:
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError("não é um arquivo GGUF")
        f.read(4)
        n_tensors, n_kv = struct.unpack("<QQ", f.read(16))
        kv = {}
        for _ in range(n_kv):
            key = _string(f)
            value = _value(f, struct.unpack("<I", f.read(4))[0])
            if not (isinstance(value, list) and len(value) > 64):  # listas de tokens não interessam
                kv[key] = value
        tensors = []
        for _ in range(n_tensors):
            name = _string(f)
            dims = struct.unpack("<I", f.read(4))[0]
            shape = struct.unpack(f"<{dims}Q", f.read(8 * dims))
            ttype = struct.unpack("<I", f.read(4))[0]
            f.read(8)  # offset
            bloco, bytes_bloco = _GGML.get(ttype, (1, 2))
            elementos = 1
            for d in shape:
                elementos *= d
            tensors.append((name, elementos // bloco * bytes_bloco))
    return {"kv": kv, "tensors": tensors}


def gguf_info(path: str) -> dict:
    """Metadados úteis do modelo. Tudo zero/vazio quando o arquivo não dá para ler."""
    vazio = {"sampling": {}, "arch": "", "n_layer": 0, "n_head_kv": 0, "head_dim": 0, "ctx_train": 0, "n_expert": 0,
             "n_expert_used": 0, "n_head": 0, "embedding": 0, "attn_interval": 1, "ssm_inner": 0,
             "ssm_state": 0, "ssm_conv": 0, "ssm_groups": 0, "size": 0, "tensors": []}
    try:
        st = Path(path).stat()
        data = _gguf(path, (st.st_size, int(st.st_mtime)))
    except (OSError, ValueError, struct.error):
        return vazio
    kv, arch = data["kv"], str(data["kv"].get("general.architecture") or "")

    def get(sufixo, padrao=0):
        return int(kv.get(f"{arch}.{sufixo}", padrao) or padrao)

    n_head = get("attention.head_count")
    embed = get("embedding_length")
    head_dim = get("attention.key_length") or (embed // n_head if n_head else 0)
    sampling = {k.rsplit(".", 1)[-1]: v for k, v in kv.items() if k.startswith("general.sampling.")}
    return {"sampling": sampling, "arch": arch, "n_layer": get("block_count"), "n_head_kv": get("attention.head_count_kv", n_head),
            "head_dim": head_dim, "ctx_train": get("context_length"), "n_expert": get("expert_count"),
            "n_expert_used": get("expert_used_count"), "n_head": n_head, "embedding": embed,
            # Modelos híbridos (Qwen3.6, Granite): só 1 em cada N camadas tem atenção; as outras são
            # recorrentes e não gastam cache KV. Sem a chave, toda camada tem atenção.
            "attn_interval": get("full_attention_interval", 1),
            "ssm_inner": get("ssm.inner_size"), "ssm_state": get("ssm.state_size"),
            "ssm_conv": get("ssm.conv_kernel"), "ssm_groups": get("ssm.group_count"),
            "size": st.st_size, "tensors": data["tensors"]}


def gguf_arch(path: str) -> str:
    return gguf_info(path)["arch"]


INFERENCE_LIMITS = {  # chave: (tipo, mínimo, máximo)
    "temperature": (float, 0.0, 5.0), "top_k": (int, 0, 500), "top_p": (float, 0.0, 1.0),
    "min_p": (float, 0.0, 1.0), "repeat_penalty": (float, 0.5, 3.0), "max_tokens": (int, 0, 1_000_000),
    "reasoning_budget": (int, -1, 1_000_000),
}


def clean_inference(patch: dict) -> dict:
    """Valida o que veio da tela Inferência. Só chaves conhecidas, dentro de faixa que o servidor aceita."""
    out: dict = {}
    for chave, valor in (patch or {}).items():
        if chave not in INFERENCE_DEFAULTS:
            raise ToolError(f"Ajuste de inferência desconhecido: '{chave}'.")
        if chave == "think":
            out[chave] = bool(valor)
        elif chave == "stop":
            if not isinstance(valor, list):
                raise ToolError("'stop' precisa ser uma lista de textos.")
            out[chave] = [str(x)[:100] for x in valor if str(x).strip()][:8]
        else:
            tipo, lo, hi = INFERENCE_LIMITS[chave]
            try:
                v = tipo(valor)
            except (TypeError, ValueError):
                raise ToolError(f"'{chave}' precisa ser um número.") from None
            if not lo <= v <= hi:
                raise ToolError(f"'{chave}' deve ficar entre {lo} e {hi}.")
            out[chave] = v
    return out


# ---------------------------------------------------------------- estimativa de memória
# Conferida contra os buffers que o próprio llama.cpp imprime (-lv 6) num Qwen3.6-35B-A3B:
# pesos 5588/14763 MiB GPU/CPU, KV 680/1360, estado recorrente 109/251, compute 519 MiB.

SLOTS_AUTO = 4          # -np -1: o llama-server abre 4 slots
COMPUTE_BASE = 519 << 20  # buffer de cálculo na GPU com ubatch 512 (cresce junto com o ubatch)
HOST_BASE = 160 << 20     # buffers auxiliares na RAM


def estimate(path: str, p: dict) -> dict:
    """Quanto o modelo deve ocupar: VRAM e total (VRAM + RAM). É estimativa, não medição."""
    info = gguf_info(path)
    if not info["n_layer"] or not info["tensors"]:
        return {"ok": False}
    n_layer = info["n_layer"]
    ngl = min(int(p.get("ngl") or 0), n_layer)
    ncmoe = int(p.get("n_cpu_moe") or 0)
    # O llama.cpp descarrega as ÚLTIMAS camadas, e a primeira coisa que sobe é a camada de saída:
    # com -ngl 19 ele registra "18 repeating layers" + a saída = 19/41.
    repetidas = max(0, ngl - 1) if ngl else 0
    na_gpu = set(range(n_layer - repetidas, n_layer))
    saida_na_gpu = ngl > 0

    pesos_gpu = pesos_cpu = 0
    for nome, tam in info["tensors"]:
        m = re.match(r"blk\.(\d+)\.", nome)
        camada = int(m.group(1)) if m else -1
        # -1 = token_embd/output: só vão para a GPU quando todas as camadas couberam (ngl > n_layer).
        gpu = (camada in na_gpu) if m else saida_na_gpu
        if gpu and m and "_exps" in nome and camada < ncmoe:
            gpu = False  # --n-cpu-moe: os especialistas destas camadas ficam na RAM
        if gpu:
            pesos_gpu += tam
        else:
            pesos_cpu += tam

    ctx = int(p.get("ctx") or 0)
    intervalo = max(1, info["attn_interval"])
    atencao = [i for i in range(n_layer) if (i + 1) % intervalo == 0]
    por_token = info["n_head_kv"] * info["head_dim"] * (
        KV_BYTES.get(str(p.get("cache_type_k") or "f16"), 2.0) + KV_BYTES.get(str(p.get("cache_type_v") or "f16"), 2.0))
    kv = int(ctx * len(atencao) * por_token)
    kv_gpu = int(ctx * len([i for i in atencao if i in na_gpu]) * por_token) if not p.get("no_kv_offload") else 0

    slots = int(p.get("parallel") or 0) or SLOTS_AUTO
    recorrentes = [i for i in range(n_layer) if (i + 1) % intervalo != 0]
    por_camada = 0
    if info["ssm_inner"]:  # estado das camadas recorrentes: não cresce com o contexto
        conv = max(0, info["ssm_conv"] - 1) * (info["ssm_inner"] + 2 * info["ssm_groups"] * info["ssm_state"])
        por_camada = (conv + info["ssm_state"] * info["ssm_inner"]) * 4
    rs = por_camada * len(recorrentes) * slots
    rs_gpu = por_camada * len([i for i in recorrentes if i in na_gpu]) * slots

    ubatch = int(p.get("ubatch") or 512) or 512
    compute_gpu = int(COMPUTE_BASE * max(1.0, ubatch / 512)) if ngl else 0
    # ponytail: buffer de cálculo por medição, não por fórmula — ele depende de grafo, backend e ubatch
    total = pesos_gpu + pesos_cpu + kv + rs + compute_gpu + HOST_BASE
    return {"ok": True, "gpu": pesos_gpu + kv_gpu + rs_gpu + compute_gpu, "total": total,
            "weights_gpu": pesos_gpu, "weights_cpu": pesos_cpu, "kv": kv, "kv_gpu": kv_gpu,
            "recurrent": rs, "compute_gpu": compute_gpu, "layers_gpu": ngl, "n_layer": n_layer,
            "ctx_train": info["ctx_train"], "attn_layers": len(atencao)}


def projector_for(path: str) -> str:
    """mmproj-*.gguf na mesma pasta do modelo. É o que dá visão a ele, e vem junto no repositório."""
    pasta = Path(path).parent
    try:
        achados = sorted(f for f in pasta.glob("*.gguf") if f.name.lower().startswith("mmproj"))
    except OSError:
        return ""
    return str(achados[0]) if achados else ""


def remove_model(path: str) -> list[str]:
    """Apaga o modelo do disco, com os shards e o .part de um download interrompido.

    Só dentro das pastas configuradas, e nunca o que está carregado — senão o llama-server segura o
    arquivo e o usuário fica achando que o Forja não apagou.
    """
    alvo = Path(path)
    if not alvo.is_file():
        raise ToolError(f"Modelo não encontrado: {path}")
    if not dentro_das_pastas(alvo.parent):
        raise ToolError("Esse arquivo não está em nenhuma pasta de modelos do Forja.")
    if status()["running"] and _mesma_pasta(status().get("path"), alvo):
        raise ToolError("Esse modelo está carregado. Descarregue antes de apagar.")
    m = SHARD.match(alvo.name)
    if m:
        total = int(m.group("total"))
        arquivos = [alvo.with_name(f"{m.group('base')}-{i:05d}-of-{total:05d}.gguf") for i in range(1, total + 1)]
    else:
        arquivos = [alvo]
    apagados = []
    for f in arquivos + [f.with_suffix(f.suffix + ".part") for f in list(arquivos)]:
        try:
            if f.exists():
                f.unlink()
                apagados.append(str(f))
        except OSError as e:
            raise ToolError(f"Não deu para apagar {f.name}: {e}")
    data = read_config()
    data["models"].pop(str(alvo), None)  # os ajustes de carga daquele modelo vão junto
    write_config(data)
    return apagados


def alias_of(path: str) -> str:
    m = SHARD.match(Path(path).name)
    return m.group("base") if m else Path(path).stem


# ------------------------------------------------------------------ llama-server

_proc: subprocess.Popen | None = None
_state: dict = {}
_loading: dict = {}       # carga em andamento: o painel desenha a barra a partir daqui
_last_error: dict = {}    # fica até a próxima carga: erro some da tela antes de a pessoa ler
_proc_lock = threading.Lock()
_imagem = threading.Event()  # geração de imagem em andamento: sd.cpp e llama-server brigam pela VRAM
SEGUNDOS_POR_GB = 6.0     # chute inicial do tempo de carga; vira a média da máquina depois da 1ª vez


# Parâmetro da interface -> opção cujo "(default: N)" do --help vale como padrão.
HELP_FLAG = {"batch": "--batch-size", "ubatch": "--ubatch-size", "ctx_checkpoints": "--ctx-checkpoints"}


@functools.lru_cache(maxsize=4)
def _help(exe: str) -> str:
    try:
        r = subprocess.run([exe, "--help"], cwd=str(Path(exe).parent), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60, **native.popen_kwargs())
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout + r.stderr


def flags(exe: str) -> frozenset[str]:
    """Opções que ESTE binário aceita, lidas do --help.

    O llama.cpp renomeia opção de vez em quando (o --mlock/--no-mmap virou --load-mode) e, com um nome
    que ele não conhece, o llama-server sai com código 1 e uma linha só de erro. Lendo o --help antes,
    o que não existe nesta build simplesmente não é enviado. Vazio = não deu para ler: manda tudo.
    """
    return frozenset(re.findall(r"-{1,2}[A-Za-z][A-Za-z0-9_+-]*", _help(exe)))


@functools.lru_cache(maxsize=4)
def help_defaults(exe: str) -> dict:
    """{opção: valor padrão} lido dos "(default: N)" do --help — o padrão de verdade daquela build."""
    out: dict[str, object] = {}
    bloco: list[str] = []
    for linha in _help(exe).splitlines() + [""]:
        if re.match(r"\s{0,7}-{1,2}[A-Za-z]", linha) or not linha.strip():
            _absorve(bloco, out)
            bloco = [linha]
        else:
            bloco.append(linha)
    return out


def _absorve(bloco: list[str], out: dict) -> None:
    if not bloco:
        return
    texto = " ".join(bloco)
    m = re.search(r"\(default: ([^)]*)\)", texto)
    if not m:
        return
    bruto = m.group(1).strip().strip("'\"")
    valor: object = bruto
    if re.fullmatch(r"-?\d+", bruto):
        valor = int(bruto)
    elif re.fullmatch(r"-?\d*\.\d+", bruto):
        valor = float(bruto)
    for nome in re.findall(r"-{1,2}[A-Za-z][A-Za-z0-9_-]*", bloco[0][:42]):  # só a coluna das opções
        out.setdefault(nome, valor)


def argv(exe: Path, path: str, p: dict, known: frozenset[str] = frozenset()) -> list[str]:
    """Parâmetros de carga -> linha de comando do llama-server. Zero/vazio = deixa o padrão dele."""
    ok = (lambda flag: not known or flag in known)  # sem lista de opções conhecidas, não filtra nada
    a = [str(exe), "-m", str(path), "--host", "127.0.0.1", "--port", str(config.LOCAL_PORT),
         "--alias", alias_of(path), "--jinja"]  # --jinja: templates do gguf, necessário p/ tool calling
    a += ["-c", str(int(p["ctx"])), "-ngl", str(int(p["ngl"]))]
    for key, flag in (("threads", "-t"), ("batch", "-b"), ("ubatch", "-ub"), ("ctx_checkpoints", "--ctx-checkpoints"),
                      ("n_cpu_moe", "--n-cpu-moe")):
        if int(p.get(key) or 0) > 0 and ok(flag):
            a += [flag, str(int(p[key]))]
    if int(p.get("parallel") or 1) > 1:
        a += ["-np", str(int(p["parallel"]))]
    a += ["-fa", "on" if p.get("flash_attn") else "off"]
    for key, flag in (("cache_type_k", "--cache-type-k"), ("cache_type_v", "--cache-type-v")):
        if p.get(key) and p[key] != "f16" and ok(flag):
            a += [flag, str(p[key])]
    for key, flag in (("kv_unified", "--kv-unified"), ("no_kv_offload", "--no-kv-offload")):
        if p.get(key) and ok(flag):
            a.append(flag)
    a += _load_mode(bool(p.get("mlock")), bool(p.get("mmap", True)), known)
    for key, flag in (("rope_freq_base", "--rope-freq-base"), ("rope_freq_scale", "--rope-freq-scale")):
        if float(p.get(key) or 0) and ok(flag):
            a += [flag, str(float(p[key]))]
    if int(p.get("seed") or 0):
        a += ["--seed", str(int(p["seed"]))]
    arch = gguf_arch(path) if int(p.get("n_expert") or 0) else ""
    if arch and ok("--override-kv"):  # sem a arquitetura certa no prefixo, o llama.cpp ignora a chave
        a += ["--override-kv", f"{arch}.expert_used_count=int:{int(p['n_expert'])}"]
    if p.get("mmproj") and ok("--mmproj"):
        a += ["--mmproj", str(p["mmproj"])]
    return a


def _load_mode(mlock: bool, mmap: bool, known: frozenset[str]) -> list[str]:
    """mlock/mmap: builds novas têm um --load-mode só; as antigas, --mlock e --no-mmap separados.

    mlock sem mmap tenta travar o modelo inteiro na RAM de uma vez e o llama.cpp aborta
    (GGML_ASSERT(addr)) quando não consegue — é a combinação que o LM Studio nem oferece.
    """
    if "--load-mode" in known:
        modo = {(False, True): "", (True, True): "mmap+mlock", (False, False): "none", (True, False): "mlock"}
        escolha = modo[(mlock, mmap)]
        return ["--load-mode", escolha] if escolha else []
    out = []
    if mlock and (not known or "--mlock" in known):
        out.append("--mlock")
    if not mmap and (not known or "--no-mmap" in known):
        out.append("--no-mmap")
    return out


def status() -> dict:
    with _proc_lock:
        alive = bool(_proc and _proc.poll() is None)
        base = {"port": config.LOCAL_PORT, "loading": _progress(), "error": dict(_last_error)}
        if not alive:
            return {"running": False, "path": "", "params": {}, "ctx": None, "alias": "", "vision": False, **base}
        return {"running": True, "pid": _proc.pid, "uptime": int(time.time() - _state["started"]),
                **_state["info"], **base}


def _progress() -> dict:
    """Quanto falta para o modelo subir. O llama.cpp não publica % nenhuma enquanto carrega, então é
    tempo decorrido sobre o tempo estimado (segundos por GB medidos na carga anterior desta máquina)."""
    if not _loading:
        return {}
    passado = time.time() - _loading["started"]
    previsto = max(1.0, _loading["eta"])
    return {"path": _loading["path"], "name": _loading["name"], "elapsed": int(passado),
            "eta": int(previsto), "percent": min(99, int(passado / previsto * 100))}


def log(tail: int = 80) -> str:
    try:
        lines = LOG_FILE.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max(1, min(int(tail or 40), 500)):])


PID_FILE = config.DATA_DIR / "llama-server.pid"


def reap_orphan() -> None:
    """Mata um llama-server que tenha sobrado de uma execução anterior do backend.

    O normal é ele morrer junto: o Electron mata a árvore ao sair e o lifespan chama unload(). Se o
    backend levar um kill seco, sobra um processo segurando dezenas de GB — é esse que some aqui.
    O filtro por nome do taskkill garante que um PID reciclado por outro programa não seja atingido.
    """
    try:
        pid_txt, _, porta = PID_FILE.read_text("utf-8").strip().partition(":")
        pid = int(pid_txt)
    except (OSError, ValueError):
        return
    if porta and porta != str(config.LOCAL_PORT):
        return  # o registro é de outra configuração (outra porta): não é nosso para matar
    PID_FILE.unlink(missing_ok=True)
    if native.WINDOWS:
        r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid), "/FI", "IMAGENAME eq llama-server.exe"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print(f"Forja: llama-server órfão (pid {pid}) encerrado.", flush=True)
    else:
        try:
            os.kill(pid, 9)
        except (ProcessLookupError, PermissionError):
            pass


def image_busy() -> bool:
    return _imagem.is_set()


def set_image_busy(ligado: bool) -> None:
    (_imagem.set if ligado else _imagem.clear)()


def clear_error() -> None:
    """Esquece a falha da última carga e zera o log do llama-server."""
    _last_error.clear()
    try:
        LOG_FILE.write_text("", "utf-8")
    except OSError:
        pass


def unload() -> None:
    global _proc
    with _proc_lock:
        proc, _proc = _proc, None
        _state.clear()
    PID_FILE.unlink(missing_ok=True)
    if proc:
        native.kill_tree(proc)


def load(path: str, patch: dict | None = None) -> dict:
    """Sobe o llama-server com o modelo. Substitui o que estiver carregado (um por vez).

    ponytail: um modelo por vez; multi-modelo simultâneo é caso de llama-swap, não deste projeto.
    """
    if image_busy():
        raise ToolError("Uma imagem está sendo gerada agora. Espere terminar para carregar um modelo — "
                        "os dois disputam a mesma VRAM.")
    exe = find_exe("llama")
    if not exe:
        raise ToolError("llama.cpp não instalado. Baixe o runtime no painel IA local.")
    if not Path(path).is_file():
        raise ToolError(f"Modelo não encontrado: {path}")
    p = save_params(path, patch or {})
    unload()
    for _ in range(10):  # a porta leva um instante para liberar depois do kill do modelo anterior
        if not _health():
            break
        time.sleep(0.3)
    else:  # porta ocupada por outro processo: o nosso llama-server morreria calado e o /health mentiria
        raise ToolError(f"A porta {config.LOCAL_PORT} já está ocupada. Feche o programa que está usando "
                        "essa porta (outro llama-server?) e tente de novo.")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    gb = max(0.1, Path(path).stat().st_size / 2 ** 30)
    _last_error.clear()
    _loading.update({"path": path, "name": alias_of(path), "started": time.time(),
                     "eta": gb * float(read_config().get("speed") or SEGUNDOS_POR_GB)})
    fh = open(LOG_FILE, "wb")  # sobrescreve: o log é sempre do modelo carregado agora
    cmd = argv(exe, path, p, flags(str(exe)))
    fh.write((" ".join(cmd) + "\n").encode("utf-8"))
    fh.flush()
    proc = subprocess.Popen(cmd, cwd=str(exe.parent), stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **native.popen_kwargs())
    PID_FILE.write_text(f"{proc.pid}:{config.LOCAL_PORT}", "utf-8")
    global _proc
    with _proc_lock:
        _proc = proc
        _state.update({"started": time.time(),
                       "info": {"path": path, "alias": alias_of(path), "params": p,
                                "ctx": int(p["ctx"]), "vision": bool(p.get("mmproj"))}})
    try:
        _wait_ready(proc)
    except ToolError as e:
        _last_error.update({"when": time.time(), "path": path, "message": str(e), "log": log(60)})
        _loading.clear()
        raise
    gasto = time.time() - _loading["started"]
    _loading.clear()
    data = read_config()
    anterior = float(data.get("speed") or SEGUNDOS_POR_GB)
    data["speed"] = round((anterior + gasto / gb) / 2, 2)  # média simples: a próxima barra já acerta mais
    data["last"] = path
    write_config(data)
    return status()


def _health() -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{config.LOCAL_PORT}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def _wait_ready(proc: subprocess.Popen, timeout: int = 900) -> None:
    """Espera /health responder 200. Carregar 30GB do disco demora (mais ainda sem mmap); o erro mostra o log."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            unload()
            raise ToolError(f"llama-server saiu com código {proc.returncode}.\n{log(15)}")
        # Só vale se quem respondeu foi o processo que acabamos de subir.
        if _health() and proc.poll() is None:
            return
        time.sleep(0.5)
    unload()
    raise ToolError(f"llama-server não respondeu em {timeout}s.\n{log(15)}")


# ------------------------------------------------------------------ Hugging Face

HF = "https://huggingface.co"
NOVA_LINHA = chr(10)
TAB = chr(9)
QUANT = re.compile(r"(IQ\d[A-Z_]*|Q\d_[A-Z0-9_]+|F16|BF16|F32)", re.I)


def search(q: str, kind: str = "text", limit: int = 20) -> list[dict]:
    """kind=text: repos com .gguf (chat). kind=image: modelos de difusão (.safetensors também)."""
    tipo = {"pipeline_tag": "text-to-image"} if kind == "image" else {"filter": "gguf"}
    r = httpx.get(f"{HF}/api/models", timeout=20, follow_redirects=True,
                  params={"search": q, "sort": "downloads", "direction": -1, "limit": limit, "full": "true", **tipo})
    if r.status_code >= 400:
        raise ToolError(f"Hugging Face respondeu {r.status_code}.")
    return [{"id": m["id"], "author": m.get("author", ""), "downloads": m.get("downloads", 0),
             "likes": m.get("likes", 0), "updated": m.get("lastModified", ""),
             "gated": bool(m.get("gated")), "tags": _tags(m.get("tags") or [])}
            for m in r.json()]


# Tags do HF vêm com muito ruído de catálogo (region:, endpoints_compatible, base_model:...).
TAG_RUIDO = ("region:", "endpoints_compatible", "base_model:", "autotrain", "arxiv:", "doi:", "dataset:",
             "co2_eq_emissions", "model-index", "has_space", "custom_code", "text-generation-inference")


def _tags(tags: list[str], limite: int = 8) -> list[str]:
    return [t for t in tags if not t.startswith(TAG_RUIDO)][:limite]


def _licenca(tags: list[str], card: dict) -> str:
    for t in tags:
        if t.startswith("license:"):
            return t.split(":", 1)[1]
    return str(card.get("license") or "")


def _capacidades(j: dict, arquivos: list[dict], readme: str) -> dict:
    """O que o modelo sabe fazer. Sai do template de chat e da arquitetura, não de adivinhação no nome."""
    template = str((j.get("gguf") or {}).get("chat_template") or "")
    arqs = " ".join((j.get("config") or {}).get("architectures") or [])
    tags = " ".join(j.get("tags") or [])
    nomes = " ".join(f["path"] for f in arquivos).lower()
    visao = ("mmproj" in nomes or "ForConditionalGeneration" in arqs or "VL" in arqs
             or "image-text-to-text" in tags or "multimodal" in tags or "vision" in tags)
    ferramentas = "tool_call" in template or "tools" in template or "function-calling" in tags
    raciocinio = ("think" in template or "reasoning" in template or "reasoning" in tags
                  or "reasoning" in readme[:2000].lower())
    return {"vision": bool(visao), "tools": bool(ferramentas), "reasoning": bool(raciocinio)}


def _readme(repo: str) -> str:
    """Card do modelo, sem o cabeçalho YAML e cortado: é para ler, não para guardar."""
    try:
        r = httpx.get(f"{HF}/{repo}/raw/main/README.md", timeout=20, follow_redirects=True)
    except httpx.HTTPError:
        return ""
    if r.status_code >= 400:
        return ""
    texto = r.text
    if texto.startswith("---"):
        fim = texto.find(chr(10) + "---", 3)
        if fim > 0:
            texto = texto[fim + 4:]
    return _sem_html(texto)[:30_000]


def _sem_html(texto: str) -> str:
    """Card do HF sem as tags HTML (quase todo card começa com um bloco de <div> e <img>).

    A interface mostra o README como markdown; HTML cru apareceria escapado na tela, e renderizá-lo
    significaria confiar em texto de terceiros dentro do app.
    """
    texto = re.sub(r"<(script|style)[^>]*>.*?</>", "", texto, flags=re.S | re.I)
    texto = re.sub(r"<br\s*/?>", chr(10), texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", "", texto)
    texto = texto.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return _desindenta(re.sub(NOVA_LINHA + r"[ " + TAB + r"]*(?:" + NOVA_LINHA + r"[ " + TAB + r"]*){2,}",
                              NOVA_LINHA * 2, texto)).strip()


def _desindenta(texto: str) -> str:
    """Tira o recuo que sobrou do HTML: 4 espaços em markdown viram bloco de código."""
    saida, dentro_de_codigo = [], False
    for linha in texto.splitlines():
        if linha.lstrip().startswith("```"):
            dentro_de_codigo = not dentro_de_codigo
        saida.append(linha if dentro_de_codigo else linha.lstrip(" " + TAB))
    return NOVA_LINHA.join(saida)


def repo_info(repo: str, kind: str = "text") -> dict:
    """Tudo que a janela de busca mostra de um modelo: números, capacidades, arquivos e README."""
    r = httpx.get(f"{HF}/api/models/{repo}", timeout=20, follow_redirects=True)
    if r.status_code == 401:
        raise ToolError("Repositório restrito (gated). Baixe manualmente e aponte a pasta.")
    if r.status_code >= 400:
        raise ToolError(f"Hugging Face respondeu {r.status_code} para {repo}.")
    j = r.json()
    arquivos = files(repo, kind)
    readme = _readme(repo)
    gguf = j.get("gguf") or {}
    return {"id": j.get("id", repo), "author": j.get("author", ""), "downloads": j.get("downloads", 0),
            "likes": j.get("likes", 0), "updated": j.get("lastModified", ""), "gated": bool(j.get("gated")),
            "tags": _tags(j.get("tags") or [], 12), "license": _licenca(j.get("tags") or [], j.get("cardData") or {}),
            "params": int(gguf.get("total") or 0), "arch": str(gguf.get("architecture") or ""),
            "ctx_train": int(gguf.get("context_length") or 0),
            "capabilities": _capacidades(j, arquivos, readme), "files": arquivos, "readme": readme}


def files(repo: str, kind: str = "text") -> list[dict]:
    exts = WEIGHTS if kind == "image" else (".gguf",)
    r = httpx.get(f"{HF}/api/models/{repo}/tree/main", timeout=20, follow_redirects=True,
                  params={"recursive": "true"})
    if r.status_code == 401:
        raise ToolError("Repositório restrito (gated). Baixe manualmente e aponte a pasta.")
    if r.status_code >= 400:
        raise ToolError(f"Hugging Face respondeu {r.status_code} para {repo}.")
    out = []
    for f in r.json():
        if f.get("type") != "file" or not f["path"].lower().endswith(exts):
            continue
        m = SHARD.match(Path(f["path"]).name)
        if m and m.group("idx") != "00001":
            continue  # shard do meio: baixar o primeiro já traz o conjunto inteiro
        quant = QUANT.search(Path(f["path"]).stem)
        out.append({"path": f["path"], "size": f.get("size") or (f.get("lfs") or {}).get("size") or 0,
                    "quant": quant.group(0).upper() if quant else "", "shards": int(m.group("total")) if m else 1})
    return sorted(out, key=lambda f: f["path"])


def _mesma_pasta(a, b) -> bool:
    """Mesma pasta, com barra normal, barra invertida ou maiúscula diferente: comparar a string crua
    dava "pasta não está na lista" para quem digitou o caminho de um jeito e escolheu de outro."""
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def dentro_das_pastas(path) -> bool:
    """A pasta é uma das configuradas (ou está dentro de uma delas)."""
    alvo = Path(os.path.normcase(os.path.normpath(str(path))))
    return any(alvo == raiz or raiz in alvo.parents
               for raiz in (Path(os.path.normcase(os.path.normpath(d))) for d in dirs()))


def download(repo: str, path: str, folder: str = "") -> dict:
    """Baixa o arquivo (e todos os shards do conjunto) para a pasta escolhida."""
    dest_dir = Path(folder) if folder else Path(read_config().get("download_dir") or models_dir())
    if not any(_mesma_pasta(dest_dir, d) for d in dirs()):
        raise ToolError(f"'{dest_dir}' não está na lista de pastas de modelos. Adicione-a primeiro.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    _patch("download_dir", str(dest_dir))  # a próxima vez já vem com a mesma pasta
    names = [path]
    m = SHARD.match(Path(path).name)
    if m:
        parent = str(Path(path).parent).replace("\\", "/")
        prefix = "" if parent in (".", "") else parent + "/"
        total = int(m.group("total"))
        names = [f"{prefix}{m.group('base')}-{i:05d}-of-{total:05d}.gguf" for i in range(1, total + 1)]
    urls = [f"{HF}/{repo}/resolve/main/{n}?download=true" for n in names]
    job = downloads.create("modelo", f"{repo}/{Path(path).name}")
    threading.Thread(target=_download_files, args=(job, urls, names, dest_dir), daemon=True).start()
    return job


def _download_files(job: dict, urls: list[str], names: list[str], dest_dir: Path) -> None:
    """Cada arquivo vai com o seu nome (o helper genérico de downloads baixa pra um destino só)."""
    from .downloads import _Cancelled, _fetch, finish, update
    try:
        total = 0
        for u in urls:
            try:
                total += int(httpx.head(u, follow_redirects=True, timeout=20).headers.get("content-length") or 0)
            except httpx.HTTPError:
                total = 0
                break
        update(job["id"], total=total)
        base = 0
        for url, name in zip(urls, names):
            update(job["id"], detail=Path(name).name)
            _fetch(url, dest_dir / Path(name).name, job, base, total)
            base = job["done"]
        finish(job["id"], result=str(dest_dir))
    except _Cancelled:
        pass
    except Exception as e:
        finish(job["id"], error=f"{e.__class__.__name__}: {e}")


def set_image(patch: dict) -> dict:
    """Padrões da aba Imagem (modelo, passos, tamanho...)."""
    data = read_config()
    img = {**DEFAULT_IMAGE, **data["image"]}
    for k, default in DEFAULT_IMAGE.items():
        if k not in patch:
            continue
        try:
            img[k] = type(default)(patch[k])
        except (TypeError, ValueError):
            raise ToolError(f"Valor inválido para '{k}': {patch[k]!r}")
    if img["out_dir"]:
        try:
            Path(img["out_dir"]).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ToolError(f"Não deu para usar a pasta '{img['out_dir']}': {e}")
    data["image"] = img
    write_config(data)
    return img


# ------------------------------------------------------------------ painel

def model_view(path: str, patch: dict | None = None) -> dict:
    """Tudo que o painel precisa de um modelo: metadados, padrões, o que foi mudado e a estimativa."""
    d = defaults_for(path)
    atual = {**d, **overrides(path), **_clean_params(patch or {})}
    info = {k: v for k, v in gguf_info(path).items() if k != "tensors"}
    from . import db  # import local: db não é necessário para nada mais deste módulo
    inf_d = inference_defaults(path)
    inf = {**inf_d, **(db.get_model_setting(alias_of(path)).get("inference") or {})}
    return {"path": path, "info": info, "defaults": d, "params": atual,
            "overrides": sorted(k for k, v in atual.items() if v != d[k]),
            "estimate": estimate(path, atual), "model": alias_of(path),
            "inference": inf, "inference_defaults": inf_d,
            "inference_overrides": sorted(k for k, v in inf.items() if v != inf_d[k])}


def state() -> dict:
    cfg = read_config()
    models = scan()
    baixar = cfg.get("download_dir") or models_dir()
    return {"runtimes": runtimes(), "models": models, "server": status(), "dirs": dirs(), "download_dir": baixar,
            "jobs": downloads.list_jobs(), "defaults": DEFAULT_PARAMS, "last": cfg["last"],
            "image": cfg["image"], "image_models": scan(WEIGHTS), "port": config.LOCAL_PORT,
            "image_dir": cfg["image"].get("out_dir") or str(IMAGENS), "models_dir": models_dir(),
            "image_busy": image_busy(), "data_dir": str(config.DATA_DIR)}

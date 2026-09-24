"""Geração de imagem com stable-diffusion.cpp.

Sem servidor: cada imagem é uma chamada do `sd-cli.exe` que termina e libera a VRAM. Dois caminhos
para a mesma função — a ferramenta `image_generate` (o agente gera e a imagem aparece no chat) e
POST /api/local/image (o painel, com prompt e parâmetros na mão).
"""
from __future__ import annotations

import asyncio
import functools
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import config, downloads, localai, native, uploads
from .tools import Tool, ToolError, register

OUT_DIR = config.DATA_DIR / "imagens"   # padrão; a tela Imagem pode apontar outra pasta


class ModeloCarregado(ToolError):
    """Tem um LLM na VRAM. Quem chamou decide: descarregar (perde o cache do chat) ou desistir."""


def out_dir() -> Path:
    escolhida = localai.read_config()["image"].get("out_dir")
    return Path(escolhida) if escolhida else OUT_DIR
# Barra de amostragem do sd.cpp: "  |=====>   | 3/8 - 11.5it/s". As barras de carregamento do modelo
# usam MB/s e ficam de fora — senão a barra da UI andaria para trás.
PROGRESS = re.compile(r"\|\s*(\d+)/(\d+) - ([\d.]+)\s*(it/s|s/it)")
TIMEOUT = 1800  # 30 min: CPU puro com modelo grande é lento mesmo


def _opts(patch: dict | None = None) -> dict:
    """Padrão da aba Imagem + ajustes daquele modelo + o que veio na chamada."""
    base = localai.read_config()["image"]
    # Os ajustes são do modelo que vai gerar — num lote multi-modelo o patch troca o modelo a cada
    # imagem, e usar os ajustes do padrão vazaria o VAE/clip do modelo errado.
    alvo = (patch or {}).get("model") or base.get("model", "")
    do_modelo = localai.image_params(alvo) if alvo else {}
    return {**base, **do_modelo, **{k: v for k, v in (patch or {}).items() if v not in (None, "")}}


def _flag_modelo(path: str) -> str:
    """GGUF com `general.architecture` (flux, qwen_image...) é só o unet: vai em --diffusion-model.

    Com -m o sd.cpp procura os pesos com o prefixo de checkpoint completo e não acha nada.
    """
    # ponytail: heurística pelo metadado; o `convert` do sd.cpp (checkpoint inteiro) não grava arquitetura
    if path.lower().endswith(".gguf") and localai.gguf_info(path)["arch"]:
        return "--diffusion-model"
    return "-m"


MAX_REFS = 10  # limite do Qwen-Image 2.1


def _confere_arquivos(model: str, o: dict, refs: list[str]) -> None:
    """Barra antes de rodar: sem VAE/codificador o sd-cli falha com erro que ninguém entende."""
    req = localai.requisitos(model) or {}
    if refs and not req.get("edita"):
        editam = ", ".join(r["nome"] for r in localai.REQUISITOS.values() if r.get("edita"))
        raise ToolError(f"{Path(model).stem} não edita imagem (só gera). Edição funciona com: {editam}.")
    if len(refs) > MAX_REFS:
        raise ToolError(f"No máximo {MAX_REFS} imagens de referência (máscaras incluídas); vieram {len(refs)}.")
    for r in refs:
        if not Path(r).is_file():
            raise ToolError(f"Imagem de referência não encontrada: {r}\nEla foi movida, renomeada ou apagada: "
                            "anexe de novo (Reanexar, na miniatura).")
    falta = localai.faltando(model, o, editar=bool(refs))
    if falta:
        arquivos = {**req.get("precisa", {}), **req.get("edita", {})}
        itens = "\n".join(f"- {localai.ROTULO_ARQUIVO[k]}: {arquivos[k][0]} — {arquivos[k][1]}" for k in falta)
        raise ToolError(f"{req.get('nome')} precisa de arquivos que não estão configurados (ou não existem):\n"
                        f"{itens}\nBaixe e informe os caminhos em IA local › Imagem › ajustes deste modelo. "
                        f"Guia: {req.get('doc')}")


SEM_PROJECAO = "No latent to RGB projection known"  # aviso do sd-cli, a cada passo


def previa_automatica(model: str, o: dict) -> str:
    """O modo que a prévia "Automática" usa neste modelo: TAESD se houver o arquivo; senão a projeção
    (de graça), a não ser que o modelo não tenha — aí o VAE."""
    if o.get("taesd"):
        return "tae"
    return "vae" if localai.sem_proj(model) else "proj"


def modo_previa(o: dict) -> str | None:
    """O --preview que vai para o sd-cli, ou None. "tae" sem o arquivo do TAESD não tem com o que
    decodificar: gera sem prévia, que ela é só enfeite."""
    modo = o.get("preview") or previa_automatica(str(o.get("model") or o.get("diffusion_model") or ""), o)
    if modo == "none" or (modo == "tae" and not o.get("taesd")):
        return None
    return modo


def argv(exe: Path, prompt: str, out: Path, o: dict, refs: list[str] | tuple = ()) -> list[str]:
    # Sem -M: o modo padrão do sd.cpp é a geração de imagem (img_gen nas builds novas, txt2img nas antigas).
    a = [str(exe), "-p", prompt, "-o", str(out),
         "--steps", str(int(o["steps"])), "--cfg-scale", str(float(o["cfg"])),
         "-W", str(int(o["width"])), "-H", str(int(o["height"])), "--sampling-method", str(o["sampler"])]
    if o.get("diffusion_model"):      # Flux/SD3: o unet vem separado do resto
        a += ["--diffusion-model", str(o["diffusion_model"])]
    elif o.get("model"):
        _confere_arquivos(str(o["model"]), o, list(refs))
        a += [_flag_modelo(str(o["model"])), str(o["model"])]
    else:
        raise ToolError("Escolha um modelo de imagem no painel IA local › Imagem.")
    for key, flag in (("vae", "--vae"), ("clip_l", "--clip_l"), ("t5xxl", "--t5xxl"), ("llm", "--llm")):
        if o.get(key):
            a += [flag, str(o[key])]
    if refs:  # edição: cada -r é uma imagem de referência, na ordem
        for r in refs:
            a += ["-r", str(r)]
        if o.get("llm_vision"):
            a += ["--llm_vision", str(o["llm_vision"])]
    if o.get("offload"):
        a += ["--offload-to-cpu"]
    if o.get("flash_attn"):
        a += ["--diffusion-fa"]
    if o.get("vae_tiling"):
        a += ["--vae-tiling"]
    if o.get("te_cpu") in ("sempre", "editar" if refs else "gerar"):
        # Só "te=cpu" jogava o resto no dispositivo 0 — num Ryzen, a GPU integrada, e a Arc ficava parada.
        a += ["--backend", f"{_gpu(str(exe))},te=cpu"]
    # Prévia por passo num arquivo (quem chama passa `_preview`: o lote, um por imagem).
    modo = modo_previa(o) if o.get("_preview") else None
    if modo:
        a += ["--preview", modo, "--preview-path", str(o["_preview"])]
        if modo == "tae":  # só a prévia: a imagem final continua saindo do VAE de verdade
            a += ["--taesd", str(o["taesd"]), "--taesd-preview-only"]
    if o.get("negative"):
        a += ["-n", str(o["negative"])]
    # -s 0 é uma semente válida para o sd.cpp (o padrão dele é 42, sempre a mesma imagem): 0 aqui = aleatória.
    a += ["-s", str(int(o["seed"])) if int(o.get("seed") or 0) else "-1"]
    return a


def escolhe_gpu(listagem: str) -> str:
    """Nome da GPU dedicada na saída do `sd-cli --list-devices` (a que não é memória unificada)."""
    nomes = [m.group(1).lower() for m in re.finditer(r"^((?:vulkan|cuda)\d+)\t", listagem, re.M | re.I)]
    integradas = {f"vulkan{n}" for n in re.findall(r"^ggml_vulkan: (\d+) = .*\| uma: 1", listagem, re.M)}
    dedicadas = [n for n in nomes if n not in integradas]
    return (dedicadas or nomes or ["cpu"])[0]


@functools.lru_cache(maxsize=4)  # ponytail: GPU trocada com o Forja aberto só vale depois de reiniciar
def _gpu(exe: str) -> str:
    r = subprocess.run([exe, "--list-devices"], cwd=str(Path(exe).parent), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60, **native.popen_kwargs())
    return escolhe_gpu(r.stdout + r.stderr)


def _exe() -> Path:
    exe = localai.find_exe("sd")
    if not exe:
        raise ToolError("stable-diffusion.cpp não instalado. Baixe o runtime no painel IA local.")
    return exe


def generate(prompt: str, out: Path, opts: dict | None = None, job_id: str = "",
             refs: list[str] | tuple = (), progresso=None, previa: Path | None = None) -> Path:
    """Roda o sd-cli até o fim. Bloqueante: quem chama usa thread.

    `progresso(passo, total, s_passo)` a cada passo da amostragem (o card do lote enche com isso).
    `previa`: onde o sd-cli grava a prévia de cada passo, se o modelo tiver o modo de prévia ligado."""
    exe = _exe()
    if not prompt.strip():
        raise ToolError("Descreva a imagem (prompt vazio).")
    o = {**_opts(opts), "_preview": previa} if previa else _opts(opts)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(argv(exe, prompt, out, o, refs), cwd=str(exe.parent), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace", **native.popen_kwargs())
    tail: list[str] = []
    timer = threading.Timer(TIMEOUT, lambda: native.kill_tree(proc))
    timer.start()
    # Vigia à parte: carregando pesos o sd-cli passa minutos sem imprimir nada, e conferir o
    # cancelamento só a cada linha deixava o processo vivo (e a GPU ocupada) depois do "Cancelar".
    parar = threading.Event()

    def vigia():
        while not parar.wait(0.5):
            if downloads.cancelled(job_id):
                native.kill_tree(proc)
                return

    if job_id:
        threading.Thread(target=vigia, daemon=True).start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            tail.append(line.rstrip())
            del tail[:-40]
            if SEM_PROJECAO in line and modo_previa(o) == "proj":
                localai.marcar_sem_proj(str(o.get("model") or o.get("diffusion_model")))  # próxima: VAE
            m = PROGRESS.search(line)
            # Só a barra da amostragem (total = passos): o VAE em blocos também imprime barra em s/it, e
            # na edição ele codifica a referência antes de amostrar — o card ia a 100% e voltava a 0.
            if m and int(m.group(2)) != int(o["steps"]):
                m = None
            if job_id and m:
                downloads.update(job_id, done=int(m.group(1)), total=int(m.group(2)))
            if progresso and m:
                v = float(m.group(3))
                # o sd.cpp troca a unidade conforme a velocidade: abaixo de 1 it/s ele passa a s/it
                progresso(int(m.group(1)), int(m.group(2)), v if m.group(4) == "s/it" else (1 / v if v else 0.0))
        proc.wait()
    finally:
        parar.set()
        timer.cancel()
    if job_id and downloads.cancelled(job_id):
        raise ToolError("Geração cancelada.")
    if proc.returncode != 0 or not out.exists():
        log = "\n".join(tail[-12:])
        dica = ""
        # "available 0.00 MB device ... workspace capacity check": outro programa (um LLM carregado) tomou a VRAM
        if ("DeviceLost" in log or "OutOfDeviceMemory" in log or "out of memory" in log.lower()
                or "workspace capacity check" in log):
            dica = ("A GPU ficou sem memória (outro programa, como um LLM carregado noutra janela do Forja, "
                    "pode estar usando a VRAM). Em IA local › Modelos › ajustes deste modelo, ligue "
                    "\"Pesos na RAM\", \"Flash attention\" e \"VAE em blocos\", ou diminua a resolução.\n\n")
        raise ToolError(f"{dica}sd falhou (código {proc.returncode}):\n{log}")
    return out


def start_job(prompt: str, opts: dict | None = None, confirm: bool = False) -> dict:
    """Versão do painel: job com progresso, na pasta escolhida na aba Imagem.

    Com um LLM carregado, os dois disputam a VRAM — então descarregamos antes, mas só depois de a
    pessoa confirmar, porque isso derruba o cache de contexto do chat que estiver aberto.
    """
    argv(_exe(), prompt or " ", OUT_DIR / "x.png", _opts(opts))  # valida runtime, modelo e prompt ANTES
    if localai.status()["running"]:                                # de descarregar o LLM por nada
        if not confirm:
            raise ModeloCarregado(localai.status().get("alias") or "um modelo")
        localai.unload()
    localai.set_image_busy(True)
    job = downloads.create("imagem", prompt[:60])
    out = out_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}.png"

    def work():
        try:
            generate(prompt, out, opts, job["id"])
            downloads.finish(job["id"], result=str(out))
        except Exception as e:
            downloads.finish(job["id"], error=str(e))
        finally:
            localai.set_image_busy(False)

    threading.Thread(target=work, daemon=True).start()
    return job


# ---------------------------------------------------------------- ferramenta

def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


async def image_generate(root: Path, args: dict) -> dict:
    # Aqui não se descarrega nada: a conversa pode estar rodando justamente no modelo local.
    localai.set_image_busy(True)
    prompt = str(args.get("prompt") or "")
    opts = {k: args.get(k) for k in ("negative", "steps", "width", "height", "seed")}
    refs = [str((root / r).resolve()) for r in (args.get("refs") or [])]  # absoluto passa intacto
    # A imagem do agente mora na pasta de trabalho da conversa; o arquivo aqui é só passagem.
    out = Path(tempfile.gettempdir()) / "forja-sd" / f"{time.strftime('%Y%m%d-%H%M%S')}.png"
    try:
        await asyncio.to_thread(generate, prompt, out, opts, "", refs)
    finally:
        localai.set_image_busy(False)
    dados = out.read_bytes()
    out.unlink(missing_ok=True)
    att = uploads.save("imagem.png", dados, "image/png", root)
    return {"text": f"Imagem gerada em {att['path']} ({len(dados) // 1024} KB).", "attachments": [att]}


def _preview(_root: Path, args: dict) -> dict:
    """`kind` tem que ser um dos três que o front conhece (diff | new | command).

    Isto devolvia `kind: "text"` com `title`/`body`, campos que o `Preview` do frontend não tem:
    o card caía no ramo do diff e fazia split num `text` inexistente.
    """
    return {"kind": "new", "path": "imagem.png", "text": str(args.get("prompt") or "")}


register(Tool(
    "image_generate",
    "Gera uma imagem a partir de uma descrição, com o stable-diffusion.cpp local. A imagem é salva "
    "na pasta de trabalho e aparece no chat. Use quando o usuário pedir uma imagem, ilustração ou arte.",
    _obj({"prompt": {"type": "string", "description": "Descrição da imagem, em inglês funciona melhor"},
          "negative": {"type": "string", "description": "O que evitar na imagem"},
          "steps": {"type": "integer", "description": "Passos de amostragem (padrão: o do painel)"},
          "width": {"type": "integer"}, "height": {"type": "integer"},
          "seed": {"type": "integer", "description": "Semente para repetir a mesma imagem"},
          "refs": {"type": "array", "items": {"type": "string"},
                   "description": "Imagens a editar (caminhos na pasta de trabalho). Com isso o prompt "
                                  "descreve a edição. Só em modelos que editam, como o Qwen-Image 2.1. "
                                  "Até 10. Edição local no Qwen-Image 2.1: a original e, logo depois, uma "
                                  "máscara do mesmo tamanho (branco = muda, preto = fica), com o prompt só "
                                  "descrevendo o que entra ali; ou círculos coloridos pintados na imagem, "
                                  "citados no prompt (\"remove the watch in the blue circle\")"}},
         ["prompt"]),
    image_generate, mutating=True, preview=_preview, timeout=None,  # geração longa, com progresso próprio
    available=lambda: bool(localai.find_exe("sd"))))


# ---------------------------------------------------------------- slots (skill gerar-imagens)

NOME_SLOT = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
MAX_SLOTS = 50  # o mesmo teto de um lote


def _lado(v, nome: str) -> int | None:
    if v in (None, "", 0):
        return None  # fica o tamanho do painel
    n = int(v)
    if not 256 <= n <= 2048:
        raise ToolError(f"{nome} {n} fora de 256–2048.")
    return round(n / 64) * 64  # o sd.cpp pede múltiplos de 64


def imagens_pendentes(root: Path, args: dict) -> dict:
    """Só registra: os slots vão no meta do resultado e a UI desenha o botão que leva a fila para a tela
    Imagens (lotes.start com slots_de). Nada é gerado aqui."""
    from .tools import resolve_path

    estilo = str(args.get("estilo") or "").strip()
    slots_in = args.get("slots") or []
    if not isinstance(slots_in, list) or not slots_in:
        raise ToolError("Passe pelo menos um slot em `slots`.")
    if len(slots_in) > MAX_SLOTS:
        raise ToolError(f"No máximo {MAX_SLOTS} slots por chamada.")
    slots, vistos = [], set()
    for s in slots_in:
        nome = str(s.get("nome") or "").strip()
        if not NOME_SLOT.match(nome):
            raise ToolError(f"Nome de slot inválido: '{nome}'. Use minúsculas, números e hífens (ex.: vela-3141).")
        alvo = resolve_path(root, str(s.get("caminho") or ""))  # confina na pasta da conversa
        if alvo.suffix.lower() != ".png":
            raise ToolError(f"{s.get('caminho')}: o arquivo do slot tem que ser .png (é o que o sd.cpp grava).")
        prompt = str(s.get("prompt") or "").strip()
        if not prompt:
            raise ToolError(f"Slot {nome} sem prompt.")
        if nome in vistos or str(alvo) in vistos:
            raise ToolError(f"Slot repetido: {nome} ({s.get('caminho')}).")
        vistos |= {nome, str(alvo)}
        slots.append({"nome": nome, "caminho": str(alvo), "rel": alvo.relative_to(root.resolve()).as_posix(),
                      "prompt": f"{prompt}, {estilo}" if estilo else prompt,
                      "prompt_base": prompt, "estilo": estilo,  # "regerar todas com outro estilo" troca só o fim
                      "largura": _lado(s.get("largura"), "largura"), "altura": _lado(s.get("altura"), "altura")})
    from . import slots as projeto

    # Até gerar, o site mostra um PNG neutro com o nome do slot em vez de imagem quebrada.
    for s in slots:
        projeto.placeholder(s["caminho"], s["largura"], s["altura"], s["nome"])
    lista = "\n".join(f"- {s['nome']} → {s['rel']}" for s in slots)
    avisos = projeto.conferir(root, slots)
    conferencia = ("\n\nConferi o código contra os slots e achei problemas; corrija agora (o caminho no código "
                   "tem que ser o `caminho` do slot) e chame imagens_pendentes de novo se mudar algum slot:\n"
                   + "\n".join(f"- {a}" for a in avisos)) if avisos else "\n\nConferi o código: todo slot é usado."
    return {"text": f"{len(slots)} slot(s) de imagem registrados. O chat mostra ao usuário um botão que abre a "
                    f"tela Imagens com a fila. Até ele gerar, cada caminho tem um PNG provisório com o nome do "
                    f"slot:\n{lista}{conferencia}\n\nQuando as imagens ficarem prontas, chega um aviso nesta conversa.",
            "imagens_pendentes": {"estilo": estilo, "slots": slots}}


register(Tool(
    "imagens_pendentes",
    "Registra os slots de imagem que o código criado aponta (arquivos PNG que ainda não existem). O usuário "
    "recebe um botão no chat que abre a tela Imagens com uma fila para gerar cada slot no caminho certo. "
    "Chame uma vez, no fim, com todos os slots (skill gerar-imagens).",
    _obj({"estilo": {"type": "string", "description": "Estilo comum a todas, somado a cada prompt (em inglês)"},
          "slots": {"type": "array", "items": _obj({
              "nome": {"type": "string", "description": "nome-codigo, ex.: vela-3141"},
              "caminho": {"type": "string", "description": "PNG relativo à pasta da conversa, ex.: img/vela-3141.png"},
              "prompt": {"type": "string", "description": "Descrição da imagem, em inglês"},
              "largura": {"type": "integer"}, "altura": {"type": "integer"}},
              ["nome", "caminho", "prompt"])}},
         ["slots"]),
    imagens_pendentes))

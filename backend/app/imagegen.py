"""Geração de imagem e de vídeo com stable-diffusion.cpp.

Sem servidor: cada imagem é uma chamada do `sd-cli.exe` que termina e libera a VRAM. Dois caminhos
para a mesma função — a ferramenta `image_generate` (o agente gera e a imagem aparece no chat) e os
lotes das abas Imagens e Vídeo (`lotes.py`).

Vídeo é o mesmo binário em `-M vid_gen` com um modelo Wan, gravando .webm direto (o Electron toca
sem ffmpeg). Nos lotes e no argv, `refs` do vídeo são os quadros: nenhum = texto → vídeo, um = a
imagem inicial (-i), dois = início e fim (--end-img).
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
TIMEOUT_VIDEO = 4 * 3600  # vídeo 720p num 14B com pesos na RAM passa de uma hora fácil
MODOS_VIDEO = ("t2v", "i2v", "flf2v")  # pelo número de quadros dados: 0, 1 ou 2
ROTULO_MODO = {"t2v": "texto → vídeo", "i2v": "imagem → vídeo", "flf2v": "primeiro e último quadro"}


def _opts(patch: dict | None = None) -> dict:
    """Padrão da aba Imagem + ajustes daquele modelo + o que veio na chamada."""
    base = localai.read_config()["image"]
    # Os ajustes são do modelo que vai gerar — num lote multi-modelo o patch troca o modelo a cada
    # imagem, e usar os ajustes do padrão vazaria o VAE/clip do modelo errado.
    alvo = (patch or {}).get("model") or base.get("model", "")
    if localai.eh_video(alvo):  # o padrão do vídeo é o da aba Vídeo: o VAE da imagem não serve ao Wan
        base = {**localai.DEFAULT_IMAGE, **(localai.read_config().get("video") or {})}
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
    if req.get("video"):
        if len(refs) > 2:
            raise ToolError("Vídeo aceita no máximo dois quadros: o inicial e o final.")
        modo = MODOS_VIDEO[len(refs)]
        if modo not in req["modos"]:
            fazem = ", ".join(r["nome"] for r in localai.REQUISITOS.values() if modo in (r.get("modos") or []))
            raise ToolError(f"{req['nome']} não faz {ROTULO_MODO[modo]}. Esse modo funciona com: {fazem}.")
    elif refs and not req.get("edita"):
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
                        f"{itens}\nBaixe e informe os caminhos em IA local › Modelos › ajustes deste modelo. "
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
    video = localai.eh_video(str(o.get("model") or ""))
    if video:
        a[1:1] = ["-M", "vid_gen"]
        a += ["--video-frames", str(int(o["frames"])), "--fps", str(int(o["fps"]))]
        if float(o.get("flow_shift") or 0):
            a += ["--flow-shift", str(float(o["flow_shift"]))]
        if o.get("high_noise_model"):  # Wan2.2 A14B: o HighNoise abre a amostragem, o LowNoise fecha
            a += ["--high-noise-diffusion-model", str(o["high_noise_model"]),
                  "--high-noise-sampling-method", str(o["sampler"])]
            if int(o.get("high_noise_steps") or -1) > 0:
                a += ["--high-noise-steps", str(int(o["high_noise_steps"]))]
            if float(o.get("high_noise_cfg") or 0):
                a += ["--high-noise-cfg-scale", str(float(o["high_noise_cfg"]))]
    for key, flag in (("vae", "--vae"), ("clip_l", "--clip_l"), ("t5xxl", "--t5xxl"), ("llm", "--llm"),
                      ("clip_vision", "--clip_vision")):
        if o.get(key):
            a += [flag, str(o[key])]
    if video:
        if refs:
            a += ["-i", str(refs[0])]
        if len(refs) > 1:
            a += ["--end-img", str(refs[1])]
    elif refs:  # edição: cada -r é uma imagem de referência, na ordem
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
        if video:
            # O bloco padrão (32×32 no latente) do VAE do Wan2.2 pediu 15 GB num só na B580, e o 5B morria
            # no fim da amostragem. 16×16 decodificou 17 quadros em 23 s; o corte no tempo segura os clipes
            # mais longos, em que cada bloco carrega todos os quadros.
            a += ["--vae-tile-size", "16x16", "--temporal-tiling"]
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
    video = localai.eh_video(str(o.get("model") or ""))
    timer = threading.Timer(TIMEOUT_VIDEO if video else TIMEOUT, lambda: native.kill_tree(proc))
    # O Wan2.2 A14B amostra em dois passes (HighNoise e LowNoise), cada um com a sua barra.
    passos = {int(o.get("steps") or 0), int(o.get("high_noise_steps") or -1) if video else -1}
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
            # barra de carregamento ("|####   | 201/242 - 651MB/s") não explica erro nenhum e enchia o resumo
            if not line.lstrip().startswith("|"):
                tail.append(line.rstrip())
                del tail[:-40]
            if SEM_PROJECAO in line and modo_previa(o) == "proj":
                localai.marcar_sem_proj(str(o.get("model") or o.get("diffusion_model")))  # próxima: VAE
            m = PROGRESS.search(line)
            # Só a barra da amostragem (total = passos): o VAE em blocos também imprime barra em s/it, e
            # na edição ele codifica a referência antes de amostrar — o card ia a 100% e voltava a 0.
            if m and int(m.group(2)) not in passos:
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
        if "DeviceLost" in log or "OutOfDeviceMemory" in log or "out of memory" in log.lower():
            dica = ("A GPU ficou sem memória. Em IA local › Modelos › ajustes deste modelo, ligue "
                    "\"Pesos na RAM\", \"Flash attention\" e \"VAE em blocos\", ou diminua a resolução"
                    + (" e a duração" if video else "") + ".\n\n")
        raise ToolError(f"{dica}sd falhou (código {proc.returncode}):\n{log}")
    return out


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


def _modelo_de_video() -> str:
    """O da aba Vídeo; sem ele, o primeiro modelo de vídeo das pastas."""
    escolhido = (localai.read_config().get("video") or {}).get("model") or ""
    if escolhido and Path(escolhido).is_file():
        return escolhido
    return next((m["path"] for m in localai.scan(localai.WEIGHTS)
                 if m["kind"] == "video" and not localai.alto_ruido(m["path"])), "")


def quadros(segundos: float, fps: int) -> int:
    """Duração em quadros que o Wan aceita: 4k+1 (o VAE junta 4 quadros em 1 no tempo)."""
    return max(1, round(segundos * fps / 4)) * 4 + 1


async def video_generate(root: Path, args: dict) -> dict:
    model = _modelo_de_video()
    if not model:
        raise ToolError("Nenhum modelo de vídeo nas pastas. Baixe um kit em IA local › Baixar › Vídeo.")
    o = _opts({"model": model})
    opts = {"model": model, **{k: args.get(k) for k in ("negative", "seed")}}
    if args.get("segundos"):
        opts["frames"] = quadros(float(args["segundos"]), int(o["fps"]))
    refs = [str((root / r).resolve()) for r in (args.get("imagem_inicial"), args.get("imagem_final")) if r]
    localai.set_image_busy(True)
    out = Path(tempfile.gettempdir()) / "forja-sd" / f"{time.strftime('%Y%m%d-%H%M%S')}.webm"
    try:
        await asyncio.to_thread(generate, str(args.get("prompt") or ""), out, opts, "", refs)
    finally:
        localai.set_image_busy(False)
    dados = out.read_bytes()
    out.unlink(missing_ok=True)
    att = uploads.save("video.webm", dados, "video/webm", root)
    return {"text": f"Vídeo gerado em {att['path']} ({len(dados) // 1024} KB).", "attachments": [att]}


def _preview_video(_root: Path, args: dict) -> dict:
    return {"kind": "new", "path": "video.webm", "text": str(args.get("prompt") or "")}


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

register(Tool(
    "video_generate",
    "Gera um vídeo curto (2 a 5 s, sem áudio) com o Wan local no stable-diffusion.cpp. Três modos: só o "
    "prompt (texto → vídeo), com imagem_inicial (anima a imagem) ou com imagem_inicial e imagem_final "
    "(liga os dois quadros). O vídeo é salvo na pasta de trabalho e aparece no chat. Leva minutos.",
    _obj({"prompt": {"type": "string", "description": "A cena: sujeito, ação, câmera, luz. Em inglês funciona melhor"},
          "imagem_inicial": {"type": "string", "description": "Imagem a animar (caminho na pasta de trabalho)"},
          "imagem_final": {"type": "string", "description": "Último quadro; exige imagem_inicial e um modelo FLF2V"},
          "segundos": {"type": "number", "description": "Duração (padrão: a da aba Vídeo)"},
          "negative": {"type": "string", "description": "O que evitar"},
          "seed": {"type": "integer", "description": "Semente para repetir o mesmo vídeo"}},
         ["prompt"]),
    video_generate, mutating=True, preview=_preview_video, timeout=None,
    available=lambda: bool(localai.find_exe("sd")) and bool(_modelo_de_video())))

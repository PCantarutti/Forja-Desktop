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

from . import config, downloads, localai, loras, native, uploads
from .tools import Tool, ToolError, register

OUT_DIR = config.DATA_DIR / "imagens"   # padrão; a tela Imagem pode apontar outra pasta


class ModeloCarregado(ToolError):
    """Tem um LLM na VRAM. Quem chamou decide: descarregar (perde o cache do chat) ou desistir."""


def out_dir() -> Path:
    escolhida = localai.read_config()["image"].get("out_dir")
    return Path(escolhida) if escolhida else OUT_DIR


def video_dir() -> Path:
    """Vídeos da aba Vídeo (Configurações › Pastas). Prévias e descartados seguem na pasta de imagens."""
    return Path(localai.read_config()["video_dir"] or localai.VIDEOS)


def pastas_saida() -> set[Path]:
    """Raízes de onde saem arquivos gerados (servir, apagar com a conversa)."""
    return {OUT_DIR.resolve(), out_dir().resolve(), localai.VIDEOS.resolve(), video_dir().resolve()}
# Barra de amostragem do sd.cpp: "  |=====>   | 3/8 - 11.5it/s". As barras de carregamento do modelo
# usam MB/s e ficam de fora — senão a barra da UI andaria para trás.
PROGRESS = re.compile(r"\|\s*(\d+)/(\d+) - ([\d.]+)\s*(it/s|s/it)")
# Sem teto de tempo total: um 14B em 720p com pesos na RAM leva horas, e CPU puro mais ainda. O que mata o
# processo é ele parar de dar sinal: nenhuma linha por SEM_SINAL (a carga de pesos passa minutos calada),
# ou por dez passos seguidos no ritmo medido, o que for maior.
SEM_SINAL = 900
PASSOS_SEM_SINAL = 10
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
    if localai.eh_video(path):  # Wan em .safetensors (Comfy-Org) também é só o unet
        return "--diffusion-model"
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
    pasta_lora = ""
    if o.get("loras"):  # vão no prompt; o sd.cpp as tira de lá e aplica (ver loras.py)
        pasta_lora, sufixo = loras.tags(list(o["loras"]), bool(o.get("high_noise_model")))
        prompt = prompt + sufixo
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
            # O corte no tempo segura os clipes longos, em que cada bloco carrega todos os quadros; o tamanho do
            # bloco vai pela VRAM livre na hora (bloco maior = menos emendas, mais rápido).
            t = o.get("_bloco") or bloco_vae(o.get("_vram_livre_gb"))
            a += ["--vae-tile-size", f"{t}x{t}", "--temporal-tiling"]
    if o.get("te_cpu") in ("sempre", "editar" if refs else "gerar"):
        # Só "te=cpu" jogava o resto no dispositivo 0 — num Ryzen, a GPU integrada, e a Arc ficava parada.
        a += ["--backend", f"{_gpu(str(exe))},te=cpu"]
    # Prévia por passo num arquivo (quem chama passa `_preview`: o lote, um por imagem).
    modo = modo_previa(o) if o.get("_preview") else None
    if modo:
        a += ["--preview", modo, "--preview-path", str(o["_preview"])]
        if modo == "tae":  # só a prévia: a imagem final continua saindo do VAE de verdade
            a += ["--taesd", str(o["taesd"]), "--taesd-preview-only"]
    if pasta_lora:
        a += ["--lora-model-dir", pasta_lora]
    if o.get("negative"):
        a += ["-n", str(o["negative"])]
    # -s 0 é uma semente válida para o sd.cpp (o padrão dele é 42, sempre a mesma imagem): 0 aqui = aleatória.
    a += ["-s", str(int(o["seed"])) if int(o.get("seed") or 0) else "-1"]
    return a


# Quanto o VAE do Wan pede para decodificar um bloco, lido do log do sd.cpp ("need 15432.83 MB device"). Não
# cresce só com a área: tem um custo fixo grande. Parte das duas medições da B580 com o VAE do Wan2.2 (bloco 32:
# 15.432 MB; 24: 11.446 MB → ~6,3 GB fixos + ~8,9 MB por unidade de área) e passa a usar as da máquina, por
# arquivo de VAE, a cada vez que um bloco estoura (localai.anotar_vae).
VAE_MEDIDO_MB = {32: 15432.0, 24: 11446.0}
MARGEM_SD_MB = 512  # o sd.cpp deixa 512 MB fora do orçamento ("budget" = livre − 512, visto no log)
BLOCOS_VAE = (32, 24, 16)
PEDIU_VAE = re.compile(r"need ([\d.]+) MB device")


def _ajuste_vae(medidas: dict[int, float]) -> tuple[float, float]:
    """(custo fixo, MB por unidade de área). Com duas medições da máquina, só elas; com uma, ela ancora o fixo
    e a inclinação vem das de referência; sem nenhuma, as de referência."""
    ref = sorted(VAE_MEDIDO_MB)
    k_ref = (VAE_MEDIDO_MB[ref[-1]] - VAE_MEDIDO_MB[ref[0]]) / (ref[-1] ** 2 - ref[0] ** 2)
    if len(medidas) >= 2:
        ts = sorted(medidas)
        k = (medidas[ts[-1]] - medidas[ts[0]]) / (ts[-1] ** 2 - ts[0] ** 2)
        return medidas[ts[-1]] - k * ts[-1] ** 2, k
    base = medidas or VAE_MEDIDO_MB
    t = max(base)
    return base[t] - k_ref * t * t, k_ref


def necessidade_vae(t: int, medidas: dict[int, float] | None = None) -> float:
    fixo, k = _ajuste_vae(medidas or {})
    return fixo + k * t * t


def bloco_vae(livre_gb: float | None, medidas: dict[int, float] | None = None, teto: int | None = None) -> int:
    """O maior bloco (abaixo de `teto`, se houver) cuja conta cabe na VRAM livre; sem saber quanto há
    livre, o menor (o que sempre passou)."""
    if not livre_gb:
        return BLOCOS_VAE[-1]
    orcamento = livre_gb * 1024 - MARGEM_SD_MB
    for t in BLOCOS_VAE:
        if (teto is None or t < teto) and necessidade_vae(t, medidas) <= orcamento:
            return t
    return BLOCOS_VAE[-1]


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
             refs: list[str] | tuple = (), progresso=None, previa: Path | None = None, medir: dict | None = None) -> Path:
    """Roda o sd-cli até o fim. Bloqueante: quem chama usa thread.

    `progresso(passo, total, s_passo)` a cada passo da amostragem (o card do lote enche com isso).
    `previa`: onde o sd-cli grava a prévia de cada passo, se o modelo tiver o modo de prévia ligado.
    `medir`: recebe {"segundos": ...} da execução que deu certo (sem a tentativa que estourou o VAE)."""
    comeco = time.monotonic()
    exe = _exe()
    if not prompt.strip():
        raise ToolError("Descreva a imagem (prompt vazio).")
    o = {**_opts(opts), "_preview": previa} if previa else _opts(opts)
    if localai.eh_video(str(o.get("model") or "")) and o.get("vae_tiling"):
        livre = localai.vram_livre_para_vae(str(o["model"]), bool(o.get("offload")))
        o["_bloco"] = bloco_vae(livre, localai.vae_medidas(str(o.get("vae") or "")), o.get("_teto_bloco"))
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(argv(exe, prompt, out, o, refs), cwd=str(exe.parent), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace", **native.popen_kwargs())
    tail: list[str] = []
    video = localai.eh_video(str(o.get("model") or ""))
    visto = [time.monotonic(), 0.0]  # última linha do sd-cli, e o s/passo medido
    # O Wan2.2 A14B amostra em dois passes (HighNoise e LowNoise), cada um com a sua barra: o card soma
    # os dois numa barra só, senão ia a 100% no meio e voltava a 0.
    alto = int(o.get("high_noise_steps") or -1) if video and o.get("high_noise_model") else -1
    passos = {int(o.get("steps") or 0), alto}
    total_geral = int(o.get("steps") or 0) + max(0, alto)
    feito_antes, ultimo, passe = 0, 0, 0
    travou = threading.Event()
    # Vigia à parte: carregando pesos o sd-cli passa minutos sem imprimir nada, e conferir o
    # cancelamento só a cada linha deixava o processo vivo (e a GPU ocupada) depois do "Cancelar".
    parar = threading.Event()

    def vigia():
        while not parar.wait(0.5):
            if job_id and downloads.cancelled(job_id):
                native.kill_tree(proc)
                return
            if time.monotonic() - visto[0] > max(SEM_SINAL, PASSOS_SEM_SINAL * visto[1]):
                travou.set()
                native.kill_tree(proc)
                return

    threading.Thread(target=vigia, daemon=True).start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            visto[0] = time.monotonic()
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
            if not m:
                continue
            n, tot = int(m.group(1)), int(m.group(2))
            if n < ultimo:  # a barra recomeçou: é o segundo passe do A14B
                feito_antes += passe
            ultimo, passe = n, tot
            if alto > 0:
                n, tot = feito_antes + n, total_geral
            if job_id:
                downloads.update(job_id, done=n, total=tot)
            v = float(m.group(3))
            # o sd.cpp troca a unidade conforme a velocidade: abaixo de 1 it/s ele passa a s/it
            visto[1] = v if m.group(4) == "s/it" else (1 / v if v else 0.0)
            if progresso:
                progresso(n, tot, visto[1])
        proc.wait()
    finally:
        parar.set()
    if job_id and downloads.cancelled(job_id):
        raise ToolError("Geração cancelada.")
    if travou.is_set():
        raise ToolError(f"O sd-cli parou de responder (nada por {max(SEM_SINAL, PASSOS_SEM_SINAL * visto[1]) / 60:.0f} "
                        "min) e foi encerrado. A GPU pode ter travado; se repetir, reinicie o Forja.")
    if proc.returncode != 0 or not out.exists():
        texto = "\n".join(tail)
        bloco = o.get("_bloco")
        if bloco and "vae decode compute failed" in texto:
            # O VAE não coube no bloco escolhido: o que ele pediu fica anotado (a próxima conta já sai certa) e a
            # geração vai de novo com o bloco menor — refaz a amostragem, mas entrega o vídeo em vez do erro.
            pedidos = PEDIU_VAE.findall(texto)
            if pedidos and o.get("vae"):
                localai.anotar_vae(str(o["vae"]), int(bloco), float(pedidos[-1]))
            livres = MEMORIA_LIVRE.findall(texto)
            if livres:
                localai.anotar_livre_sd(max(float(l) for l, _ in livres))
            if bloco > BLOCOS_VAE[-1] and not (job_id and downloads.cancelled(job_id)):
                return generate(prompt, out, {**(opts or {}), "_teto_bloco": bloco}, job_id, refs, progresso, previa, medir)
        log = "\n".join(tail[-12:])
        raise ToolError(f"{dica_de_falha(tail, video)}sd falhou (código {rotulo_codigo(proc.returncode)}):\n{log}")
    if medir is not None:
        medir["segundos"] = time.monotonic() - comeco
    return out


# "model manager memory on Vulkan1: reported free 65.43 MB / total 12118.00 MB"
MEMORIA_LIVRE = re.compile(r"reported free ([\d.]+) MB / total ([\d.]+) MB")


def dica_de_falha(tail: list[str], video: bool = False) -> str:
    """A primeira linha do erro, em português: é ela que o card mostra, e o log cru não explica nada."""
    texto = "\n".join(tail)
    baixo = texto.lower()
    # "workspace capacity check": o sd.cpp viu 0 MB livres antes de amostrar (outro programa na VRAM)
    if not any(x in baixo for x in ("devicelost", "outofdevicememory", "out of memory",
                                    "cannot make enough memory available", "workspace capacity check")):
        return ""
    livres = MEMORIA_LIVRE.findall(texto)
    if livres and float(livres[-1][0]) < 0.25 * float(livres[-1][1]):
        # Quase nada livre antes de começar: não é o modelo que é grande, é outro programa segurando a VRAM.
        livre, total = float(livres[-1][0]), float(livres[-1][1])
        return (f"A GPU estava com só {livre:.0f} MB livres de {total / 1024:.0f} GB: outro programa está "
                "ocupando a VRAM (um modelo carregado no chat, outra instância do Forja, um jogo). Libere e use "
                "Continuar.\n\n")
    return ("A GPU ficou sem memória. Em IA local › Modelos › ajustes deste modelo, ligue "
            "\"Pesos na RAM\", \"Flash attention\" e \"VAE em blocos\", ou diminua a resolução"
            + (" e a duração" if video else "") + ".\n\n")


def rotulo_codigo(rc: int | None) -> str:
    """3221226505 não diz nada; 0xC0000409 (o processo se derrubou) dá para procurar."""
    if rc is None or 0 <= rc < 256:
        return str(rc)
    return f"{rc}, 0x{rc & 0xFFFFFFFF:08X}"


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
    if args.get("imagem_final") and not args.get("imagem_inicial"):
        raise ToolError("imagem_final precisa de imagem_inicial: são o primeiro e o último quadro.")
    o = _opts({"model": model})
    # semente 0 = sorteada: sem isso vinha a da tela (a última "Refazer com esta semente")
    opts = {"model": model, "negative": args.get("negative"), "seed": int(args.get("seed") or 0)}
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
    o = _opts(opts)
    att.update(fps=int(o["fps"]), quadros=int(o["frames"]))  # o player do chat conta quadros com isso
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

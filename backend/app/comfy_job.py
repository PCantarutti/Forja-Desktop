"""Uma ampliação no ComfyUI portátil, do começo ao fim: sobe o servidor escondido (127.0.0.1, porta livre), manda o
fluxo, grava o PNG e derruba o servidor (a VRAM volta toda). Roda com o Python do próprio ComfyUI (python_embeded),
chamado pelo Forja Desktop ou, no Docker, pelo runner. Além da biblioteca padrão, só o Pillow, que vem no portátil.

    python comfy_job.py --modo seedvr2|spandrel --comfy <pasta do portátil> --modelo <arquivo> [--vae <vae>]
                        --entrada <img> --saida <png> --fator 2|4
    python comfy_job.py ... --quadros --entrada <pasta de PNGs> --saida <pasta>     (vídeo: os quadros do ffmpeg)

seedvr2: difusão (o DiT + o VAE dele). spandrel: DAT, HAT, SwinIR, SPAN, os compactos e afins, pelo carregador de
modelos de ampliação do ComfyUI; a escala sai dos pesos e é medida na saída (um 2× pedido como 4× roda duas vezes;
o que passar do alvo volta por Lanczos). redesenhar: um checkpoint de imagem (SD 1.5/SDXL) redesenha a imagem em
alta resolução por blocos, com prompt e força (--prompt, --negativo, --forca): a técnica do "Ultimate SD Upscale"
feita aqui com os nós básicos do ComfyUI; Lanczos até o tamanho final, cada bloco por imagem-para-imagem, e a
costura com transição suave na sobreposição (sem emenda).

Vídeo (--quadros): o Forja separa os quadros com o ffmpeg e junta de volta; aqui cada quadro passa pelo fluxo de
imagem, com o servidor e o modelo de pé (spandrel e seedvr2). O SeedVR2 por trechos (o blueprint "Upscale Video",
vários quadros num lote) seria mais estável no tempo, mas na Arc B580 (ComfyUI v0.37.0, PyTorch XPU) o VAE de vídeo
dele trava: o sampler de 9 quadros de 640x360 leva 1 s e a decodificação, 3 min (33 quadros: mais de 10). Quadro a
quadro sai a ~3 s cada, com a semente fixa e sem tremida visível. Vale tentar de novo numa versão nova do ComfyUI.

Fala com quem chamou por linhas no stdout: "FASE <texto>", "PROGRESSO <0..1>" (do WebSocket do ComfyUI, que é
onde ele conta os blocos; o log não tem) e, no fim, "OK <w>x<h>" ou "ERRO <mensagem>".
O mesmo arquivo existe em forja-desktop e forja-web (backend/app/comfy_job.py): mudou um, copie no outro.
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

PRONTO_S = 300  # 1ª subida do portátil compila kernels da GPU (na Arc: ~1 min)
TRABALHO_S = 3600


class Falha(Exception):
    pass


def diz(tipo: str, texto: str = "") -> None:
    print(f"{tipo} {texto}".strip(), flush=True)


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fluxo_seedvr2(img: str, fator: int, modelo: str, vae: str, semente: int) -> dict:
    """O blueprint "Upscale Video (SeedVR2)" do ComfyUI, na versão de uma imagem (sem os blocos de vídeo)."""
    tiles = {"tile_size": 512, "overlap": 128, "temporal_size": 64, "temporal_overlap": 8}
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": img}},
        "2": {"class_type": "ResizeImageMaskNode", "inputs": {"input": ["1", 0], "resize_type": "scale by multiplier",
                                                              "resize_type.multiplier": float(fator), "scale_method": "lanczos"}},
        "3": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["2", 0]}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "5": {"class_type": "UNETLoader", "inputs": {"unet_name": modelo, "weight_dtype": "default"}},
        "6": {"class_type": "VAEEncodeTiled", "inputs": {"pixels": ["3", 0], "vae": ["4", 0], **tiles}},
        "7": {"class_type": "SeedVR2Conditioning", "inputs": {"model": ["5", 0], "vae_conditioning": ["6", 0]}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["5", 0], "positive": ["7", 0], "negative": ["7", 1],
                                                   "latent_image": ["6", 0], "seed": semente, "steps": 1, "cfg": 1.0,
                                                   "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["8", 0], "vae": ["4", 0], **tiles}},
        "10": {"class_type": "SeedVR2PostProcessing", "inputs": {"images": ["9", 0], "original_resized_images": ["2", 0],
                                                                 "color_correction_method": "lab"}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "forja"}},
    }


def fluxo_seedvr2_video(nomes: list[str], fator: int, modelo: str, vae: str, semente: int, por_bloco: int,
                        sobra: int) -> dict:
    """O blueprint "Upscale Video (SeedVR2)" do ComfyUI: os quadros entram como UM vídeo (o modelo olha os vizinhos e
    a textura inventada fica a mesma de um quadro para o outro). O Split corta o latente em blocos de `por_bloco`
    quadros (4n+1) que cabem na VRAM, com `sobra` quadros latentes em comum, e o Merge junta com crossfade.
    Quadro a quadro, cada um ganhava uma textura própria: tocando, virava chiado."""
    # o VAE também vai em pedaços no tempo: 64 quadros de uma vez enchiam os 12 GB da B580
    tiles = {"tile_size": 512, "overlap": 128, "temporal_size": 16, "temporal_overlap": 4}
    g: dict = {}
    for i, nome in enumerate(nomes):
        g[f"q{i}"] = {"class_type": "LoadImage", "inputs": {"image": nome}}
    lote = "q0"
    for i in range(1, len(nomes)):  # ImageBatch em cadeia: o lote vira a sequência (frames, H, W, C) que o SeedVR2 lê como vídeo
        g[f"b{i}"] = {"class_type": "ImageBatch", "inputs": {"image1": [lote, 0], "image2": [f"q{i}", 0]}}
        lote = f"b{i}"
    g.update({
        "2": {"class_type": "ResizeImageMaskNode", "inputs": {"input": [lote, 0], "resize_type": "scale by multiplier",
                                                              "resize_type.multiplier": float(fator), "scale_method": "lanczos"}},
        "3": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["2", 0]}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "5": {"class_type": "UNETLoader", "inputs": {"unet_name": modelo, "weight_dtype": "default"}},
        "6": {"class_type": "VAEEncodeTiled", "inputs": {"pixels": ["3", 0], "vae": ["4", 0], **tiles}},
        "12": {"class_type": "SeedVR2TemporalChunk", "inputs": {"latent": ["6", 0], "temporal_overlap": sobra,
                                                                "chunking_mode": "manual", "chunking_mode.frames_per_chunk": por_bloco}},
        "7": {"class_type": "SeedVR2Conditioning", "inputs": {"model": ["5", 0], "vae_conditioning": ["12", 0]}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["5", 0], "positive": ["7", 0], "negative": ["7", 1],
                                                   "latent_image": ["12", 0], "seed": semente, "steps": 1, "cfg": 1.0,
                                                   "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "13": {"class_type": "SeedVR2TemporalMerge", "inputs": {"latents": ["8", 0], "temporal_overlap": ["12", 1]}},
        # decodificar é a parte pesada (o VAE 3D do SeedVR2): pedaços menores que os da codificação, senão os 12 GB
        # da B580 enchiam e o ComfyUI ficava minutos trocando memória (o modelo em si leva ~1 s por bloco)
        "9": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["13", 0], "vae": ["4", 0], "tile_size": 256, "overlap": 64,
                                                        "temporal_size": 8, "temporal_overlap": 4}},
        # sem correção de cor, como no blueprint de vídeo (a "lab" roda na CPU e leva segundos por quadro)
        "10": {"class_type": "SeedVR2PostProcessing", "inputs": {"images": ["9", 0], "original_resized_images": ["2", 0],
                                                                 "color_correction_method": "none"}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "forja"}},
    })
    return g


def fluxo_redesenhar(img: str, ckpt: str, prompt: str, negativo: str, forca: float, semente: int, passos: int) -> dict:
    """Imagem-para-imagem de um bloco: o checkpoint redesenha com o prompt; `forca` (denoise) é quanto pode mudar."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": img}},
        "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 1], "text": prompt}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 1], "text": negativo}},
        "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["2", 2]}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0],
                                                   "latent_image": ["5", 0], "seed": semente, "steps": passos, "cfg": 5.0,
                                                   "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": forca}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["2", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "forja"}},
    }


def blocos(total: int, bloco: int, sobra: int) -> list[int]:
    """Onde começa cada bloco numa dimensão: o menor número de blocos de `bloco` px que cobre `total` com pelo menos
    `sobra` px de sobreposição, espaçados por igual (múltiplos de 8, que o VAE pede)."""
    if total <= bloco:
        return [0]
    n = -(-(total - sobra) // (bloco - sobra))
    return [round((total - bloco) * i / (n - 1) / 8) * 8 for i in range(n)]


def pesos(w: int, h: int, sobra: int, esq: bool, cima: bool, dir_: bool, baixo: bool):
    """Máscara de mistura de um bloco: 1 no miolo, rampa até 0 nas bordas que encostam em outro bloco (as da imagem
    ficam em 1). É o que tira a emenda da costura."""
    import numpy as np
    x = np.ones(w, dtype=np.float32)
    y = np.ones(h, dtype=np.float32)
    rampa = np.linspace(0, 1, sobra + 2, dtype=np.float32)[1:-1]
    if esq:
        x[:sobra] = rampa
    if dir_:
        x[-sobra:] = rampa[::-1]
    if cima:
        y[:sobra] = rampa
    if baixo:
        y[-sobra:] = rampa[::-1]
    return (y[:, None] * x[None, :])[..., None]


def fluxo_spandrel(img: str, modelo: str) -> dict:
    """Carregar o modelo de ampliação, ampliar (o ComfyUI divide em blocos sozinho se faltar VRAM), salvar."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": img}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": modelo}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "forja"}},
    }


# Faixa de cada nó no total: medida na Arc B580 (SeedVR2 3B, 1024 -> 2048, servidor de pé: codificar 4 s, carregar o
# modelo 6 s, o passo 6 s, decodificar 6 s). Dentro do nó que conta blocos, o avanço é contínuo.
FAIXAS = {"seedvr2": {"6": (0.03, 0.20), "5": (0.20, 0.45), "8": (0.45, 0.72), "9": (0.72, 0.97), "10": (0.97, 1.0)},
          "spandrel": {"2": (0.0, 0.02), "3": (0.02, 0.98), "4": (0.98, 1.0)},
          # por bloco (a escala do ouvinte leva ao total): carregar/codificar, os passos, decodificar
          "redesenhar": {"5": (0.0, 0.08), "6": (0.08, 0.92), "7": (0.92, 1.0)}}


def ouvir(url: str, cid: str, faixas: dict, parar: threading.Event, escala: list | None = None) -> None:
    """Os eventos do ComfyUI para este cliente viram "PROGRESSO <fração>" (só quando a fração sobe 1% ou mais).
    `escala` [início, largura]: a fração de um fluxo dentro do total (no redesenhar, cada bloco é um fluxo e quem
    roda os blocos muda a escala). Sem aiohttp (vem no portátil) ou sem conexão, fica calado."""
    escala = escala if escala is not None else [0.0, 1.0]
    try:
        import asyncio
        import aiohttp
    except ImportError:
        return
    visto = [-1.0]

    def manda(f: float) -> None:
        if f >= visto[0] + 0.01:
            visto[0] = f
            diz("PROGRESSO", f"{min(f, 1.0):.3f}")

    async def laco() -> None:
        async with aiohttp.ClientSession() as s, s.ws_connect(f"{url}/ws?clientId={cid}") as ws:
            async for m in ws:
                if parar.is_set():
                    return
                if m.type != aiohttp.WSMsgType.TEXT:
                    continue
                e = json.loads(m.data)
                d = e.get("data") or {}
                faixa = faixas.get(str(d.get("node")))
                if not faixa:
                    continue
                if e.get("type") == "executing":
                    manda(escala[0] + escala[1] * faixa[0])
                elif e.get("type") == "progress" and d.get("max"):
                    manda(escala[0] + escala[1] * (faixa[0] + (faixa[1] - faixa[0]) * d["value"] / d["max"]))
    try:
        asyncio.run(laco())
    except Exception:  # noqa: BLE001 — progresso é enfeite: qualquer falha aqui não derruba a ampliação
        pass


def main() -> int:
    a = argparse.ArgumentParser()
    for nome in ("comfy", "modelo", "entrada", "saida"):
        a.add_argument(f"--{nome}", required=True)
    a.add_argument("--modo", choices=("seedvr2", "spandrel", "redesenhar"), default="seedvr2")
    a.add_argument("--prompt", default="")
    a.add_argument("--negativo", default="blurry, lowres, jpeg artifacts, oversmoothed, watermark, text")
    a.add_argument("--forca", type=float, default=0.35)
    a.add_argument("--passos", type=int, default=20)
    a.add_argument("--bloco", type=int, default=1024)
    a.add_argument("--vae", default="")
    a.add_argument("--fator", type=int, default=2)
    a.add_argument("--semente", type=int, default=42)
    a.add_argument("--quadros", action="store_true", help="vídeo: --entrada e --saida são pastas de quadros PNG")
    # SeedVR2 em vídeo: quadros por trecho mandado ao ComfyUI, quadros de contexto de cada lado, quadros por bloco
    # temporal dentro dele (4n+1) e latentes em comum entre blocos
    a.add_argument("--trecho", type=int, default=49)
    a.add_argument("--contexto", type=int, default=4)
    a.add_argument("--por-bloco", type=int, default=5)  # 4n+1; medido na B580 12 GB: 5 cabe até 1440x960 no 7B fp8 (pico 11,4 GB)
    a.add_argument("--sobra", type=int, default=1)
    o = a.parse_args()
    raiz, modelo = Path(o.comfy), Path(o.modelo)
    trabalho = Path(tempfile.mkdtemp(prefix="forja-comfy-"))
    (trabalho / "in").mkdir()
    (trabalho / "out").mkdir()
    # As pastas dos pesos entram como pastas extras de modelo: nada é copiado para dentro do portátil.
    pastas = ({"diffusion_models": modelo.parent, "vae": Path(o.vae).parent} if o.modo == "seedvr2"
              else {"checkpoints": modelo.parent} if o.modo == "redesenhar" else {"upscale_models": modelo.parent})
    (trabalho / "pastas.yaml").write_text("forja:\n" + "".join(f"  {k}: {json.dumps(str(v))}\n" for k, v in pastas.items()),
                                          encoding="utf-8")
    entrada = "entrada" + ("" if o.quadros else Path(o.entrada).suffix.lower())
    if not o.quadros:
        shutil.copyfile(o.entrada, trabalho / "in" / entrada)
    porta = porta_livre()
    url = f"http://127.0.0.1:{porta}"
    log = open(trabalho / "comfy.log", "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [str(raiz / "python_embeded" / "python.exe"), "-s", str(raiz / "ComfyUI" / "main.py"), "--listen", "127.0.0.1",
         "--port", str(porta), "--disable-auto-launch", "--disable-all-custom-nodes",
         "--extra-model-paths-config", str(trabalho / "pastas.yaml"),
         "--input-directory", str(trabalho / "in"), "--output-directory", str(trabalho / "out")],
        cwd=str(raiz), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def pede(caminho: str, corpo: dict | None = None):
        r = urllib.request.Request(url + caminho, data=json.dumps(corpo).encode() if corpo is not None else None,
                                   headers={"Content-Type": "application/json"})
        dados = urllib.request.urlopen(r, timeout=30).read()
        return json.loads(dados) if dados.strip() else {}

    def cauda() -> str:
        log.flush()
        linhas = (trabalho / "comfy.log").read_text(encoding="utf-8", errors="replace").splitlines()
        return " | ".join(l.strip() for l in linhas[-6:] if l.strip())[:600]

    cid = uuid.uuid4().hex
    parar = threading.Event()

    def rodar(g: dict, todas: bool = False):
        """Um fluxo até o fim; devolve a imagem que ele gravou em out/ (com `todas`, a lista, na ordem)."""
        try:
            pid = pede("/prompt", {"prompt": g, "client_id": cid})["prompt_id"]
        except urllib.error.HTTPError as e:
            raise Falha(f"O ComfyUI recusou o fluxo: {e.read().decode('utf-8', 'replace')[:500]}") from None
        limite = time.monotonic() + TRABALHO_S
        while True:
            try:
                h = pede(f"/history/{pid}")
            except (TimeoutError, OSError) as e:  # imagem enorme: o servidor ocupado demora a responder
                if isinstance(e, urllib.error.HTTPError):
                    raise
                h = {}
            if pid in h:
                break
            if proc.poll() is not None:
                raise Falha(f"O ComfyUI caiu no meio: {cauda()}")
            if time.monotonic() > limite:
                raise Falha("A ampliação passou de 1 hora.")
            time.sleep(1)
        st = h[pid].get("status") or {}
        if st.get("status_str") != "success":
            erro = next((m[1] for m in st.get("messages", []) if m[0] == "execution_error"), {})
            msg = erro.get("exception_message", "") or cauda()
            if "OUT_OF_RESOURCES" in msg or "DEVICE_LOST" in msg or "out of memory" in msg.lower():
                msg = "A GPU ficou sem memória (tem outro modelo carregado?). " + msg
            elif "UnsupportedModel" in msg or "Unsupported model" in msg:
                msg = "O ComfyUI não reconhece esta arquitetura de ampliação. " + msg
            raise Falha(msg.strip()[:600])
        arquivos = [i["filename"] for s in h[pid]["outputs"].values() for i in s.get("images", [])]
        if not arquivos:
            raise Falha("O ComfyUI terminou sem gravar a imagem.")
        return [trabalho / "out" / a for a in arquivos] if todas else trabalho / "out" / arquivos[0]

    def video(escala: list) -> tuple[int, int]:
        """Os quadros de --entrada, ampliados em --saida com os mesmos nomes e o tamanho pedido."""
        from PIL import Image
        nomes = sorted(p.name for p in Path(o.entrada).glob("*.png"))
        if not nomes:
            raise Falha("Nenhum quadro para ampliar.")
        destino = Path(o.saida)
        destino.mkdir(parents=True, exist_ok=True)
        with Image.open(Path(o.entrada) / nomes[0]) as im:
            alvo = (im.width * o.fator, im.height * o.fator)

        def no_alvo(im: Image.Image) -> Image.Image:
            im = im.convert("RGB")
            return im if im.size == alvo else im.resize(alvo, Image.LANCZOS)

        n, passadas = len(nomes), 0  # passadas do spandrel, medidas no 1º quadro: um 2× pedido como 4× roda duas vezes
        if o.modo == "seedvr2":
            seedvr2_em_trechos(nomes, destino, alvo, no_alvo, escala)
            return alvo
        for i, nome in enumerate(nomes):
            diz("FASE", f"ampliando o quadro {i + 1} de {n}")
            escala[0], escala[1] = i / n, 1 / n
            # nome único por quadro: com o mesmo nome, dois quadros iguais seguidos (comum em vídeo) davam o mesmo fluxo,
            # o ComfyUI devolvia o resultado do cache — o arquivo do quadro anterior, que já tinha sido apagado
            q = f"q{i:05d}.png"
            shutil.copyfile(Path(o.entrada) / nome, trabalho / "in" / q)
            if o.modo == "seedvr2":
                g = fluxo_seedvr2(q, o.fator, modelo.name, Path(o.vae).name, o.semente)
                # sem correção de cor, como no blueprint de vídeo: a "lab" roda na CPU, ~19 s por quadro de 640x360
                g["10"]["inputs"]["color_correction_method"] = "none"
                saiu = rodar(g)
            else:
                saiu = rodar(fluxo_spandrel(q, modelo.name))
                if not passadas:
                    with Image.open(saiu) as im:
                        passadas = 2 if im.width < alvo[0] else 1
                if passadas == 2:
                    shutil.move(str(saiu), trabalho / "in" / f"q{i:05d}b.png")
                    saiu = rodar(fluxo_spandrel(f"q{i:05d}b.png", modelo.name))
            with Image.open(saiu) as im:
                no_alvo(im).save(destino / nome)
            saiu.unlink(missing_ok=True)  # out/ não acumula milhares de quadros
            for f in (trabalho / "in").glob(f"q{i:05d}*.png"):
                f.unlink()
        return alvo

    def seedvr2_em_trechos(nomes: list[str], destino: Path, alvo, no_alvo, escala: list) -> None:
        """O vídeo em trechos de --trecho quadros (a RAM não comporta milhares de quadros num lote). Cada trecho leva
        --contexto quadros a mais de cada lado, para o começo e o fim dele não saírem sem vizinhos; os de contexto que
        caem dentro do trecho seguinte se misturam com ele (crossfade), e a emenda não pula."""
        import numpy as np
        from PIL import Image
        n, T, C = len(nomes), max(1, o.trecho), max(0, o.contexto)
        inicios = list(range(0, n, T))
        cauda: list = []  # os quadros de contexto do fim do trecho anterior (já no alvo), para o crossfade
        for k, ini in enumerate(inicios):
            fim = min(n, ini + T)
            a, b = max(0, ini - C), min(n, fim + C)
            diz("FASE", f"ampliando os quadros {ini + 1} a {fim} de {n}")
            escala[0], escala[1] = k / len(inicios), 1 / len(inicios)
            for f in (trabalho / "in").glob("t*.png"):
                f.unlink()
            locais = []
            for j, nome in enumerate(nomes[a:b]):
                local = f"t{k:04d}_{j:05d}.png"  # único por trecho: trecho igual ao anterior não sai do cache
                shutil.copyfile(Path(o.entrada) / nome, trabalho / "in" / local)
                locais.append(local)
            saidas = rodar(fluxo_seedvr2_video(locais, o.fator, modelo.name, Path(o.vae).name, o.semente,
                                               o.por_bloco, o.sobra), todas=True)
            if len(saidas) < b - a:
                raise Falha(f"O ComfyUI devolveu {len(saidas)} de {b - a} quadros do trecho.")
            imgs = []
            for s in saidas[: b - a]:
                with Image.open(s) as im:
                    imgs.append(no_alvo(im))
                s.unlink(missing_ok=True)
            miolo = imgs[ini - a: ini - a + (fim - ini)]
            # crossfade: os primeiros quadros do miolo também saíram como contexto do fim do trecho anterior
            for j, anterior in enumerate(cauda[: len(miolo)]):
                w = (j + 1) / (len(cauda) + 1)
                miolo[j] = Image.fromarray((np.asarray(anterior, np.float32) * (1 - w)
                                            + np.asarray(miolo[j], np.float32) * w).round().astype(np.uint8))
            for j, im in enumerate(miolo):
                im.save(destino / nomes[ini + j])
            cauda = imgs[ini - a + (fim - ini):]

    def redesenhar(alvo: tuple[int, int], escala: list) -> Path:
        """Lanczos até o tamanho final (arredondado a 8 px), um fluxo por bloco e a costura com as máscaras."""
        import numpy as np
        from PIL import Image
        W, H = (alvo[0] + 7) // 8 * 8, (alvo[1] + 7) // 8 * 8
        with Image.open(o.entrada) as im:
            base = np.asarray(im.convert("RGB").resize(alvo, Image.LANCZOS))
        # a borda que falta para o múltiplo de 8 repete a última linha/coluna; no fim, corta de volta
        grande = Image.fromarray(np.pad(base, ((0, H - alvo[1]), (0, W - alvo[0]), (0, 0)), mode="edge"))
        bw, bh = min(o.bloco, W), min(o.bloco, H)
        sobra = min(128, bw // 4, bh // 4) // 8 * 8 or 8
        xs, ys = blocos(W, bw, sobra), blocos(H, bh, sobra)
        soma = np.zeros((H, W, 3), dtype=np.float32)
        peso = np.zeros((H, W, 1), dtype=np.float32)
        total, feito = len(xs) * len(ys), 0
        for j, y in enumerate(ys):
            for i, x in enumerate(xs):
                diz("FASE", f"redesenhando o bloco {feito + 1} de {total}")
                escala[0], escala[1] = feito / total, 1 / total
                nome = f"bloco{feito}.png"
                grande.crop((x, y, x + bw, y + bh)).save(trabalho / "in" / nome)
                saiu = rodar(fluxo_redesenhar(nome, modelo.name, o.prompt or "high quality, detailed, sharp",
                                              o.negativo, o.forca, o.semente + feito, o.passos))
                with Image.open(saiu) as b:
                    arr = np.asarray(b.convert("RGB").resize((bw, bh), Image.LANCZOS), dtype=np.float32)
                m = pesos(bw, bh, sobra, i > 0, j > 0, i < len(xs) - 1, j < len(ys) - 1)
                soma[y:y + bh, x:x + bw] += arr * m
                peso[y:y + bh, x:x + bw] += m
                feito += 1
        pronto = Image.fromarray(np.clip(soma / np.maximum(peso, 1e-6), 0, 255).astype(np.uint8)).crop((0, 0, *alvo))
        caminho = trabalho / "out" / "redesenhada.png"
        pronto.save(caminho)
        return caminho

    try:
        diz("FASE", "iniciando o ComfyUI")
        limite = time.monotonic() + PRONTO_S
        while True:
            if proc.poll() is not None:
                diz("ERRO", f"O ComfyUI fechou ao iniciar: {cauda()}")
                return 1
            try:
                pede("/system_stats")
                break
            except OSError:
                if time.monotonic() > limite:
                    diz("ERRO", "O ComfyUI não respondeu em 5 min.")
                    return 1
                time.sleep(1)
        diz("FASE", "ampliando")
        escala = [0.0, 1.0]
        threading.Thread(target=ouvir, args=(url, cid, FAIXAS[o.modo], parar, escala), daemon=True).start()
        time.sleep(0.3)  # o WebSocket conectado antes do fluxo, senão os primeiros eventos se perdem
        from PIL import Image
        if o.quadros:
            if o.modo not in ("seedvr2", "spandrel"):
                raise Falha("Em vídeo, só SeedVR2 e os DAT/HAT/SwinIR: o redesenho mudaria cada quadro de um jeito.")
            alvo = video(escala)
            diz("OK", f"{alvo[0]}x{alvo[1]}")
            return 0
        with Image.open(o.entrada) as im:
            alvo = (im.width * o.fator, im.height * o.fator)
        if o.modo == "redesenhar":
            final = redesenhar(alvo, escala)
        elif o.modo == "seedvr2":
            final = rodar(fluxo_seedvr2(entrada, o.fator, modelo.name, Path(o.vae).name, o.semente))
        else:
            final = rodar(fluxo_spandrel(entrada, modelo.name))
            with Image.open(final) as im:
                largura = im.width
            if largura < alvo[0]:  # modelo 2× e pediu 4×: mais uma passada, em cima da primeira
                diz("FASE", "ampliando (2ª passada)")
                shutil.copyfile(final, trabalho / "in" / "passo2.png")
                final = rodar(fluxo_spandrel("passo2.png", modelo.name))
        with Image.open(final) as im:
            if im.size != alvo:  # um 4× pedido como 2× (ou o 2× que passou do 4×): o tamanho pedido por Lanczos
                im = im.resize(alvo, Image.LANCZOS)
            im.save(o.saida)
        diz("OK", f"{alvo[0]}x{alvo[1]}")
        return 0
    except Falha as e:
        diz("ERRO", str(e))
        return 1
    except Exception as e:  # noqa: BLE001 — o Forja precisa da mensagem; sem ela, só "saiu com código 1"
        diz("ERRO", f"Falha inesperada no driver ({type(e).__name__}): {e} | {cauda()}"[:600])
        return 1
    finally:
        parar.set()
        proc.kill()  # ponytail: o ComfyUI não tem filhos que segurem a GPU; kill no processo basta
        proc.wait()
        log.close()
        shutil.rmtree(trabalho, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

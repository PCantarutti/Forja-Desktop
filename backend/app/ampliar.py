"""Ampliação de vídeo: os quadros saem pelo ffmpeg, cada um passa pelo ESRGAN do sd-cli (`-M upscale`) na GPU
dedicada, e voltam num webm. Sem IA (Lanczos), é tudo no ffmpeg. "Suavizar" dobra os quadros por
interpolação de movimento (minterpolate do ffmpeg).

O sd.cpp só lê imagem e só grava vídeo que ele mesmo gerou, por isso o ffmpeg — baixado como os outros
runtimes (IA local), não empacotado. O ESRGAN que o sd.cpp roda é o RRDBNet (RealESRGAN x4plus,
anime_6B); a escala sai dos pesos, e aqui ela é medida no 1º quadro (tamanho de saída ÷ de entrada).
"""
from __future__ import annotations

import functools
import json
import re
import shutil
import struct
import subprocess
import time
import zipfile
from pathlib import Path

import httpx

from . import downloads, imagegen, localai, native
from .tools import ToolError

# Curadoria: os ESRGAN oficiais que o sd.cpp carrega (RRDBNet). Os "compactos" (realesr-general-v3,
# animevideov3) são outra arquitetura e ficam de fora; o x2plus também (entra com pixel-unshuffle, 12 canais,
# e o sd.cpp recusa o conv_first). 2× sai do 4× reduzido por Lanczos. Tamanho e URL vêm da API do GitHub.
CATALOGO = [
    ("RealESRGAN_x4plus.pth", "Fotográfico, 4×: o mais fiel para cenas reais"),
    ("RealESRGAN_x4plus_anime_6B.pth", "Animação e ilustração, 4× (leve)"),
]
REPO_ESRGAN = "xinntao/Real-ESRGAN"
# ponytail: medido no Arc B580 (832×480 → 4×): 128 (padrão do sd.cpp) 8,7 s, 256 6,1 s, 512 26,6 s (estoura o
# buffer do Vulkan). Se outra GPU pedir outro valor, vira medida por máquina como o bloco do VAE.
TILE_ESRGAN = 256
GH_TTL = 3600
PASTA = "Ampliação (ESRGAN)"  # subpasta da pasta de modelos onde os ESRGAN baixados caem


def eh_ampliador(path: str) -> bool:
    """ESRGAN (RRDBNet) pelo conteúdo: `conv_first` e os blocos `rdb` no pickle do .pth (ou no cabeçalho do
    .safetensors). Pelo nome não dá: o mesmo arquivo circula com nomes diferentes."""
    p = Path(path)
    try:
        if p.suffix.lower() == ".pth":
            with zipfile.ZipFile(p) as z:
                pkl = next((n for n in z.namelist() if n.endswith("data.pkl")), None)
                dados = z.read(pkl) if pkl else b""
            return b"conv_first" in dados and b"rdb1" in dados
        if p.suffix.lower() == ".safetensors":
            from .loras import cabecalho
            nomes = list(cabecalho(str(p)))
            return any(n.startswith("conv_first") for n in nomes) and any(".rdb1." in n for n in nomes)
    except (OSError, ValueError, zipfile.BadZipFile, KeyError, struct.error):
        return False
    return False


@functools.lru_cache(maxsize=4)
def _assets_esrgan(_janela: int) -> dict[str, dict]:
    r = httpx.get(f"https://api.github.com/repos/{REPO_ESRGAN}/releases", params={"per_page": 30}, timeout=20,
                  follow_redirects=True, headers={"Accept": "application/vnd.github+json"})
    if r.status_code >= 400:
        raise ToolError(f"GitHub respondeu {r.status_code} ao listar os modelos de ampliação.")
    return {a["name"]: {"url": a["browser_download_url"], "mb": round(a["size"] / 1e6, 1)}
            for rel in r.json() for a in rel.get("assets") or []}


def catalogo() -> dict:
    """Os modelos de ampliação do catálogo, com tamanho e o que já está no disco; e o estado do ffmpeg."""
    # o mesmo arquivo em duas pastas de modelos (nome e tamanho iguais) aparece uma vez só
    achados, vistos = [], set()
    for m in localai.scan(localai.WEIGHTS_TODOS):
        chave = (m["name"].lower(), m.get("size"))
        if m["kind"] == "ampliador" and chave not in vistos:
            vistos.add(chave)
            achados.append(m)
    no_disco = {Path(m["path"]).name.lower(): m["path"] for m in achados}
    try:
        assets = _assets_esrgan(int(time.time() // GH_TTL))
        erro = ""
    except (ToolError, httpx.HTTPError) as e:
        assets, erro = {}, f"Não deu para consultar o GitHub: {e}"
    modelos = [{"nome": n, "resumo": r, "mb": (assets.get(n) or {}).get("mb", 0), "presente": no_disco.get(n.lower(), "")}
               for n, r in CATALOGO]
    ff = localai.find_exe("ffmpeg")
    return {"modelos": modelos, "erro": erro, "ffmpeg": str(ff) if ff else "",
            "no_disco": [{"path": m["path"], "name": m["name"]} for m in achados]}  # inclui os de fora do catálogo


def baixar_modelo(nome: str, folder: str = "") -> dict:
    if nome not in dict(CATALOGO):
        raise ToolError("Modelo de ampliação fora do catálogo.")
    asset = _assets_esrgan(int(time.time() // GH_TTL)).get(nome)
    if not asset:
        raise ToolError(f"{nome} não está mais nos releases do Real-ESRGAN.")
    pasta = Path(folder or localai.models_dir()) / PASTA
    pasta.mkdir(parents=True, exist_ok=True)
    return downloads.start("modelo", nome, [asset["url"]], pasta / nome)


# ---------------------------------------------------------------- ffmpeg

def _ffmpeg() -> Path:
    exe = localai.find_exe("ffmpeg")
    if not exe:
        raise ToolError("Falta o ffmpeg: baixe em IA local › Vídeo, ou em Configurações › Runtime (uns 80 MB).")
    return exe


def _rodar(argv: list[str], job_id: str = "", linha=None) -> str:
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", **native.popen_kwargs())
    saida = []
    for l in proc.stdout:  # type: ignore[union-attr]
        saida.append(l)  # inteira: -encoders e o JSON do ffprobe são lidos daqui (e com -v error é pouca coisa)
        if linha:
            linha(l)
        if job_id and downloads.cancelled(job_id):
            native.kill_tree(proc)
            break
    proc.wait()
    if job_id and downloads.cancelled(job_id):
        raise ToolError("Ampliação cancelada.")
    if proc.returncode != 0:
        raise ToolError(f"{Path(argv[0]).stem} falhou (código {proc.returncode}):\n{''.join(saida[-12:])}")
    return "".join(saida)


def sondar(video: str) -> dict:
    """Largura, altura, fps (e a fração exata, `taxa`: 30000/1001), quadros e se tem áudio, pelo ffprobe."""
    probe = _ffmpeg().with_name("ffprobe" + _ffmpeg().suffix)
    try:
        out = _rodar([str(probe), "-v", "error", "-count_frames", "-show_entries",
                      "stream=codec_type,width,height,r_frame_rate,nb_read_frames", "-of", "json", str(video)])
        streams = json.loads(out)["streams"]
        s = next(x for x in streams if x.get("codec_type") == "video" and x.get("width"))
        num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    except (ToolError, StopIteration, KeyError, ValueError, json.JSONDecodeError):
        raise ToolError(f"Não consegui ler {Path(video).name} como vídeo.") from None
    return {"w": int(s["width"]), "h": int(s["height"]), "fps": num / den if den else float(num),
            "taxa": s["r_frame_rate"], "quadros": int(s.get("nb_read_frames") or 0),
            "audio": any(x.get("codec_type") == "audio" for x in streams)}


@functools.lru_cache(maxsize=4)
def codificador(ffmpeg: str) -> str:
    """O primeiro encoder de webm que este ffmpeg tem (VP9 de preferência; AV1 também toca no Electron)."""
    enc = _rodar([ffmpeg, "-hide_banner", "-encoders"])
    for c in ("libvpx-vp9", "libsvtav1", "libaom-av1", "libvpx"):
        if re.search(rf"\s{re.escape(c)}\s", enc):
            return c
    raise ToolError("Este ffmpeg não tem encoder de WebM (VP9/AV1).")


def filtros(w: int, h: int, fator: int, suavizar: bool, fps: float) -> str:
    """O -vf do final: Lanczos até o tamanho pedido (múltiplo de 2, que o yuv420p exige) e, se pedido, o
    dobro de quadros por interpolação de movimento."""
    alvo_w, alvo_h = (w * fator) // 2 * 2, (h * fator) // 2 * 2
    vf = [f"scale={alvo_w}:{alvo_h}:flags=lanczos"]
    if suavizar:
        vf.append(f"minterpolate=fps={fps * 2:g}:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
    return ",".join(vf)


def _esrgan(exe: Path, gpu: str, entrada: Path, destino: Path, modelo: str, fator: int, job_id: str = "",
            repeticoes: int = 0, oque: str = "a imagem") -> int:
    """Uma imagem pelo ESRGAN do sd-cli. `repeticoes` 0 = ainda não medida: a escala é a do modelo (2× ou 4×),
    medida nesta passada, e um 2× pedido como 4× roda de novo com duas repetições. Devolve as repetições."""
    def rodar(n: int) -> None:
        subprocess.run([str(exe), "-M", "upscale", "-i", str(entrada), "-o", str(destino), "--upscale-model", modelo,
                        "--upscale-tile-size", str(TILE_ESRGAN), "--upscale-repeats", str(n), "--backend", gpu],
                       cwd=str(exe.parent), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       **native.popen_kwargs())
        if job_id and downloads.cancelled(job_id):
            raise ToolError("Ampliação cancelada.")
        if not destino.is_file():
            raise ToolError(f"O ESRGAN não gerou {oque}. O modelo é um RRDBNet (RealESRGAN)?")
    rodar(repeticoes or 1)
    if not repeticoes:
        from PIL import Image
        with Image.open(entrada) as a0, Image.open(destino) as b0:
            escala = round(b0.width / a0.width)
        if escala < 2:  # o sd-cli que não carrega o modelo grava a própria entrada e diz "success"
            destino.unlink(missing_ok=True)
            raise ToolError(f"O sd.cpp não conseguiu rodar {Path(modelo).name} (saiu do mesmo tamanho). "
                            "Use um RRDBNet 4× como RealESRGAN_x4plus ou x4plus_anime_6B.")
        repeticoes = 1
        if escala < fator:
            repeticoes = 2
            destino.unlink()
            rodar(2)
    return repeticoes


EXT_IMAGEM = {".png", ".jpg", ".jpeg", ".webp"}


def eh_imagem(path: str) -> bool:
    return Path(path).suffix.lower() in EXT_IMAGEM


def ampliar_imagem(entrada: str, saida: Path, fator: int, modelo: str = "", job_id: str = "") -> dict:
    """Amplia uma imagem em `fator` e grava `saida` (.png). `modelo` vazio = Lanczos (Pillow), sem IA e sem ffmpeg.
    ESRGAN que passou do alvo (um 4× pedido como 2×) volta ao tamanho pedido por Lanczos."""
    from PIL import Image
    with Image.open(entrada) as im:
        alvo = (im.width * int(fator), im.height * int(fator))
        if not modelo:
            im.convert("RGBA" if "A" in im.getbands() else "RGB").resize(alvo, Image.LANCZOS).save(saida)
            return {"w": alvo[0], "h": alvo[1]}
    exe = imagegen._exe()
    _esrgan(exe, imagegen._gpu(str(exe)), Path(entrada), saida, modelo, fator, job_id)
    with Image.open(saida) as out:
        certo = out.size == alvo
        if not certo:
            out = out.resize(alvo, Image.LANCZOS)  # já carregada: o arquivo fecha antes de ser regravado (Windows)
    if not certo:
        out.save(saida)
    return {"w": alvo[0], "h": alvo[1]}


def ampliar(entrada: str, saida: Path, fator: int, modelo: str = "", suavizar: bool = False, job_id: str = "",
            progresso=None, previa: Path | None = None) -> dict:
    """Amplia `entrada` em `fator` (2 ou 4) e grava `saida` (.webm). `modelo` vazio = Lanczos, sem IA.
    `progresso(feitos, total, s_por_quadro)`; `previa` recebe o último quadro ampliado. Devolve a sondagem
    do resultado (tamanho, fps, quadros)."""
    ff = str(_ffmpeg())
    enc = codificador(ff)  # antes dos quadros: sem encoder, falha já, não depois de minutos de ESRGAN
    info = sondar(entrada)
    trabalho = saida.parent / ".ampliando" / saida.stem
    shutil.rmtree(trabalho, ignore_errors=True)
    (trabalho / "in").mkdir(parents=True)
    (trabalho / "out").mkdir()
    try:
        fonte = entrada
        if modelo:
            # fps constante: vídeo de celular vem com fps variável, e contar quadros "como vieram" tirava o
            # vídeo do tempo do áudio
            _rodar([ff, "-v", "error", "-i", entrada, "-fps_mode", "cfr", "-r", info["taxa"], str(trabalho / "in" / "%05d.png")], job_id)
            quadros = sorted((trabalho / "in").glob("*.png"))
            exe = imagegen._exe()
            gpu = imagegen._gpu(str(exe))
            repeticoes, comeco = 0, time.monotonic()
            for i, q in enumerate(quadros):
                destino = trabalho / "out" / q.name
                repeticoes = _esrgan(exe, gpu, q, destino, modelo, fator, job_id, repeticoes, f"o quadro {i + 1}")
                if previa:
                    shutil.copyfile(destino, previa)
                if progresso:
                    progresso(i + 1, len(quadros), (time.monotonic() - comeco) / (i + 1))
            fonte = str(trabalho / "out" / "%05d.png")
        # o áudio vem do original (vídeo do PC costuma ter; os gerados não têm, e o "?" deixa passar)
        entrada_final = ["-framerate", info["taxa"], "-i", fonte, "-i", entrada, "-map", "0:v", "-map", "1:a?"] if modelo             else ["-i", entrada, "-map", "0:v:0", "-map", "0:a?"]
        total = max(1, info["quadros"] * (2 if suavizar else 1))
        comeco = time.monotonic()

        def andou(l: str) -> None:  # -progress: "frame=N" a cada meio segundo; só o Lanczos usa (é a fase inteira)
            if progresso and not modelo and l.startswith("frame="):
                feitos = int(l[6:] or 0)
                progresso(min(feitos, total), total, (time.monotonic() - comeco) / max(1, feitos))
        _rodar([ff, "-v", "error", "-nostats", "-progress", "pipe:1", "-y", *entrada_final,
                "-vf", filtros(info["w"], info["h"], fator, suavizar, info["fps"]),
                "-c:v", enc, "-b:v", "0", "-crf", "24", "-pix_fmt", "yuv420p", *(["-row-mt", "1"] if "vpx" in enc else []),
                "-c:a", "libopus", "-b:a", "128k", str(saida)], job_id, andou)
        if progresso and not modelo:
            progresso(total, total, (time.monotonic() - comeco) / total)
        return sondar(str(saida))
    finally:
        shutil.rmtree(trabalho, ignore_errors=True)

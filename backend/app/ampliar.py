"""Ampliação de vídeo: os quadros saem pelo ffmpeg, cada um passa pelo ESRGAN do sd-cli (`-M upscale`) na GPU
dedicada, e voltam num webm. Sem IA (Lanczos), é tudo no ffmpeg. "Suavizar" dobra os quadros por
interpolação de movimento (minterpolate do ffmpeg).

O sd.cpp só lê imagem e só grava vídeo que ele mesmo gerou, por isso o ffmpeg — baixado como os outros
runtimes (IA local), não empacotado. O ESRGAN que o sd.cpp roda é o RRDBNet (RealESRGAN x4plus, x2plus,
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
# animevideov3) são outra arquitetura e ficam de fora. Tamanho e URL vêm da API do GitHub.
CATALOGO = [
    ("RealESRGAN_x4plus.pth", "Fotográfico, 4×: o mais fiel para cenas reais"),
    ("RealESRGAN_x2plus.pth", "Fotográfico, 2×: mais rápido, para dobrar"),
    ("RealESRGAN_x4plus_anime_6B.pth", "Animação e ilustração, 4× (leve)"),
]
REPO_ESRGAN = "xinntao/Real-ESRGAN"
# ponytail: medido no Arc B580 (832×480 → 4×): 128 (padrão do sd.cpp) 8,7 s, 256 6,1 s, 512 26,6 s (estoura o
# buffer do Vulkan). Se outra GPU pedir outro valor, vira medida por máquina como o bloco do VAE.
TILE_ESRGAN = 256
GH_TTL = 3600


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
    achados = [m for m in localai.scan(localai.WEIGHTS_TODOS) if m["kind"] == "ampliador"]
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
    pasta = Path(folder or localai.models_dir())
    pasta.mkdir(parents=True, exist_ok=True)
    return downloads.start("modelo", nome, [asset["url"]], pasta / nome)


# ---------------------------------------------------------------- ffmpeg

def _ffmpeg() -> Path:
    exe = localai.find_exe("ffmpeg")
    if not exe:
        raise ToolError("Falta o ffmpeg: baixe em Ampliar, no player, ou em IA local › Baixar › Ampliação de vídeo (uns 80 MB).")
    return exe


def _rodar(argv: list[str], job_id: str = "") -> str:
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", **native.popen_kwargs())
    saida = []
    for linha in proc.stdout:  # type: ignore[union-attr]
        saida.append(linha)  # inteira: -encoders e o JSON do ffprobe são lidos daqui (e com -v error é pouca coisa)
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
    """Largura, altura, fps e quadros do vídeo (pelo ffprobe que vem junto do ffmpeg)."""
    probe = _ffmpeg().with_name("ffprobe" + _ffmpeg().suffix)
    out = _rodar([str(probe), "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                  "stream=width,height,r_frame_rate,nb_read_frames", "-of", "json", str(video)])
    s = json.loads(out)["streams"][0]
    num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    return {"w": int(s["width"]), "h": int(s["height"]), "fps": num / den if den else float(num),
            "quadros": int(s.get("nb_read_frames") or 0)}


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
            _rodar([ff, "-v", "error", "-i", entrada, "-fps_mode", "passthrough", str(trabalho / "in" / "%05d.png")], job_id)
            quadros = sorted((trabalho / "in").glob("*.png"))
            exe = imagegen._exe()
            gpu = imagegen._gpu(str(exe))
            repeticoes, comeco = 1, time.monotonic()
            for i, q in enumerate(quadros):
                destino = trabalho / "out" / q.name
                a = [str(exe), "-M", "upscale", "-i", str(q), "-o", str(destino), "--upscale-model", modelo,
                     "--upscale-tile-size", str(TILE_ESRGAN), "--upscale-repeats", str(repeticoes), "--backend", gpu]
                subprocess.run(a, cwd=str(exe.parent), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", **native.popen_kwargs())
                if job_id and downloads.cancelled(job_id):
                    raise ToolError("Ampliação cancelada.")
                if not destino.is_file():
                    raise ToolError(f"O ESRGAN não gerou o quadro {i + 1}. O modelo é um RRDBNet (RealESRGAN)?")
                if i == 0:
                    # A escala é a do modelo (2× ou 4×): medida aqui. Um 2× pedido como 4× roda duas vezes.
                    from PIL import Image
                    with Image.open(q) as a0, Image.open(destino) as b0:
                        escala = round(b0.width / a0.width)
                    if escala < fator and escala > 1:
                        repeticoes = 2
                        destino.unlink()
                        subprocess.run([*a[:a.index("--upscale-repeats") + 1], "2", "--backend", gpu], cwd=str(exe.parent),
                                       capture_output=True, **native.popen_kwargs())
                if previa:
                    shutil.copyfile(destino, previa)
                if progresso:
                    progresso(i + 1, len(quadros), (time.monotonic() - comeco) / (i + 1))
            fonte = str(trabalho / "out" / "%05d.png")
        entrada_final = ["-framerate", f"{info['fps']:g}", "-i", fonte] if modelo else ["-i", entrada]
        _rodar([ff, "-v", "error", "-y", *entrada_final, "-vf", filtros(info["w"], info["h"], fator, suavizar, info["fps"]),
                "-c:v", enc, "-b:v", "0", "-crf", "24", "-pix_fmt", "yuv420p", *(["-row-mt", "1"] if "vpx" in enc else []),
                str(saida)], job_id)
        if progresso and not modelo:
            progresso(1, 1, 0.0)
        return sondar(str(saida))
    finally:
        shutil.rmtree(trabalho, ignore_errors=True)

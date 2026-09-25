"""Uma ampliação SeedVR2 no ComfyUI portátil, do começo ao fim: sobe o servidor escondido (127.0.0.1, porta
livre), manda o fluxo, grava o PNG e derruba o servidor (a VRAM volta toda). Só biblioteca padrão: roda
com o Python do próprio ComfyUI (python_embeded), chamado pelo Forja Desktop ou, no Docker, pelo runner.

    python comfy_job.py --comfy <pasta do portátil> --modelo <seedvr2_*.safetensors> --vae <vae>
                        --entrada <img> --saida <png> --fator 2|4

Fala com quem chamou por linhas no stdout: "FASE <texto>" e, no fim, "OK <w>x<h>" ou "ERRO <mensagem>".
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
import time
import urllib.error
import urllib.request
from pathlib import Path

PRONTO_S = 300  # 1ª subida do portátil compila kernels da GPU (na Arc: ~1 min)
TRABALHO_S = 3600


def diz(tipo: str, texto: str = "") -> None:
    print(f"{tipo} {texto}".strip(), flush=True)


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fluxo(img: str, fator: int, modelo: str, vae: str, semente: int) -> dict:
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


def main() -> int:
    a = argparse.ArgumentParser()
    for nome in ("comfy", "modelo", "vae", "entrada", "saida"):
        a.add_argument(f"--{nome}", required=True)
    a.add_argument("--fator", type=int, default=2)
    a.add_argument("--semente", type=int, default=42)
    o = a.parse_args()
    raiz, modelo, vae = Path(o.comfy), Path(o.modelo), Path(o.vae)
    trabalho = Path(tempfile.mkdtemp(prefix="forja-comfy-"))
    (trabalho / "in").mkdir()
    (trabalho / "out").mkdir()
    # As pastas dos dois pesos entram como pastas extras de modelo: nada é copiado para dentro do portátil.
    (trabalho / "pastas.yaml").write_text(
        f"forja:\n  diffusion_models: {json.dumps(str(modelo.parent))}\n  vae: {json.dumps(str(vae.parent))}\n",
        encoding="utf-8")
    shutil.copyfile(o.entrada, trabalho / "in" / ("entrada" + Path(o.entrada).suffix.lower()))
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
        try:
            pid = pede("/prompt", {"prompt": fluxo("entrada" + Path(o.entrada).suffix.lower(), o.fator, modelo.name,
                                                   vae.name, o.semente)})["prompt_id"]
        except urllib.error.HTTPError as e:
            diz("ERRO", f"O ComfyUI recusou o fluxo: {e.read().decode('utf-8', 'replace')[:500]}")
            return 1
        limite = time.monotonic() + TRABALHO_S
        while True:
            h = pede(f"/history/{pid}")
            if pid in h:
                break
            if proc.poll() is not None:
                diz("ERRO", f"O ComfyUI caiu no meio: {cauda()}")
                return 1
            if time.monotonic() > limite:
                diz("ERRO", "A ampliação passou de 1 hora.")
                return 1
            time.sleep(1)
        st = h[pid].get("status") or {}
        if st.get("status_str") != "success":
            erro = next((m[1] for m in st.get("messages", []) if m[0] == "execution_error"), {})
            msg = erro.get("exception_message", "") or cauda()
            if "OUT_OF_RESOURCES" in msg or "DEVICE_LOST" in msg or "out of memory" in msg.lower():
                msg = "A GPU ficou sem memória (tem outro modelo carregado?). " + msg
            diz("ERRO", msg.strip()[:600])
            return 1
        arquivos = [i["filename"] for s in h[pid]["outputs"].values() for i in s.get("images", [])]
        if not arquivos:
            diz("ERRO", "O ComfyUI terminou sem gravar a imagem.")
            return 1
        shutil.copyfile(trabalho / "out" / arquivos[0], o.saida)
        with open(o.saida, "rb") as f:  # largura e altura do PNG (bytes 16-24 do IHDR), sem Pillow
            cab = f.read(24)
        diz("OK", f"{int.from_bytes(cab[16:20], 'big')}x{int.from_bytes(cab[20:24], 'big')}")
        return 0
    finally:
        proc.kill()  # ponytail: o ComfyUI não tem filhos que segurem a GPU; kill no processo basta
        proc.wait()
        log.close()
        shutil.rmtree(trabalho, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

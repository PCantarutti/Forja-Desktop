"""Motor F5-TTS/E2-TTS da tela Voz: um processo com o modelo carregado, atendendo pedidos até ser derrubado.
Roda no venv do motor (RUNTIMES/tts/f5/venv, com torch e f5-tts), chamado por tts.py. Mesmo protocolo do tts_fish.py.

    python tts_f5.py --arquitetura F5TTS_v1_Base [--ckpt <arquivo|hf://repo/arq>] [--vocab <arquivo|hf://...>] [--dispositivo cpu]

Sem --ckpt, o checkpoint oficial da arquitetura. Pedidos no stdin, um JSON por linha: {ref, ref_texto, texto, saida,
velocidade, passos, semente, sem_silencio, minusculas, numeros}. Respostas no stdout: "PRONTO <dispositivo>", e por
pedido "FASE", "PROGRESSO <0..1>", "OK <segundos de áudio> <semente> <segundos gastos>" ou "ERRO <mensagem>".
Referência sem texto: o Whisper do próprio F5 transcreve.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path


def diz(tipo: str, texto: str = "") -> None:
    print(f"{tipo} {texto}".strip(), flush=True)


def local(caminho: str) -> str:
    """hf://dono/repo/arquivo vira o arquivo no cache do Hugging Face (baixa na 1ª vez)."""
    if not caminho.startswith("hf://"):
        return caminho
    from cached_path import cached_path
    diz("FASE", f"obtendo {caminho[5:]} (baixa só na 1ª vez)")
    return str(cached_path(caminho))


def normalizar(texto: str, minusculas: bool, idioma: str) -> str:
    """Fine-tunes treinados com texto "falado" (ex.: o pt-br do firstpixel) erram número e maiúscula."""
    if idioma:
        from num2words import num2words

        def extenso(m: re.Match) -> str:
            n = m.group(0)
            try:
                return num2words(float(n.replace(",", ".")) if "," in n else int(n), lang=idioma)
            except (NotImplementedError, ValueError, OverflowError):
                return n
        texto = re.sub(r"\d+(?:,\d+)?", extenso, texto)
    return texto.lower() if minusculas else texto


class Progresso:
    """O F5 passa os lotes de texto por progress.tqdm(...): vira PROGRESSO no stdout."""

    @staticmethod
    def tqdm(it, *a, **k):
        itens = list(it)
        for i, x in enumerate(itens):
            diz("PROGRESSO", f"{i / max(len(itens), 1):.3f}")
            yield x
        diz("PROGRESSO", "1")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--arquitetura", default="F5TTS_v1_Base")
    p.add_argument("--ckpt", default="")
    p.add_argument("--vocab", default="")
    p.add_argument("--dispositivo", default=None)  # cpu/cuda/xpu; sem: o F5 escolhe
    a = p.parse_args()
    try:
        import soundfile as sf
        import torch
        import torchaudio

        def carregar(caminho, *_, **__):
            # o torchaudio novo lê pelo torchcodec, que não existe em todo índice do PyTorch (o XPU não tem)
            dados, sr = sf.read(caminho, dtype="float32", always_2d=True)
            return torch.from_numpy(dados.T.copy()), sr
        torchaudio.load = carregar
        ckpt, vocab = local(a.ckpt), local(a.vocab)
        diz("FASE", "carregando o modelo")
        from f5_tts.api import F5TTS
        tts = F5TTS(model=a.arquitetura, ckpt_file=ckpt, vocab_file=vocab, device=a.dispositivo)
        diz("PRONTO", tts.device)
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        diz("ERRO", f"{e.__class__.__name__}: {e}"[:500].replace("\n", " "))
        return 1

    for linha in sys.stdin:
        if not linha.strip():
            continue
        try:
            q = json.loads(linha)
            t0 = time.time()
            minus, nums = bool(q.get("minusculas")), str(q.get("numeros") or "")
            texto = normalizar(q["texto"].strip(), minus, nums)
            ref = q["ref"]
            if Path(ref).suffix.lower() != ".wav":
                # o pydub do F5 só lê WAV sem ffmpeg; o soundfile lê mp3/flac/ogg
                dados, sr = sf.read(ref)
                ref = str(Path(tempfile.mkdtemp()) / "ref.wav")
                sf.write(ref, dados, sr)
            ref_texto = q.get("ref_texto", "").strip()
            diz("FASE", "gerando" + ("" if ref_texto else " · transcrevendo a referência"))
            semente = int(q.get("semente", -1))
            wav, sr, _ = tts.infer(ref_file=ref, ref_text=normalizar(ref_texto, minus, nums) if ref_texto else "",
                                   gen_text=texto, show_info=lambda *x: None, progress=Progresso,
                                   speed=float(q.get("velocidade", 1.0)), nfe_step=int(q.get("passos", 32)),
                                   seed=None if semente < 0 else semente, remove_silence=bool(q.get("sem_silencio")),
                                   file_wave=q["saida"])
            diz("OK", f"{len(wav) / sr:.2f} {tts.seed} {time.time() - t0:.1f}")
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            diz("ERRO", f"{e.__class__.__name__}: {e}"[:500].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Uma fala do começo ao fim com um modelo da família F5-TTS/E2-TTS: carrega, gera o WAV e sai (a VRAM volta toda).
Roda no Python do runtime de voz (RUNTIMES/tts/venv, com torch e f5-tts), chamado por tts.py.

    python tts_job.py --arquitetura F5TTS_v1_Base [--ckpt <arquivo|hf://repo/arq>] [--vocab <arquivo|hf://...>]
                      --ref <áudio> [--ref-texto <texto>] --texto <arquivo .txt> --saida <wav>
                      [--velocidade 1.0] [--passos 32] [--semente N] [--sem-silencio] [--minusculas] [--numeros pt_BR]

Sem --ckpt, o checkpoint oficial da arquitetura (baixado do Hugging Face na 1ª vez). Sem --ref-texto, o Whisper
transcreve a referência. Fala com quem chamou por linhas no stdout: "FASE <texto>", "PROGRESSO <0..1>" e, no fim,
"OK <segundos> <semente>" ou "ERRO <mensagem>".
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
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
    p.add_argument("--ref", required=True)
    p.add_argument("--ref-texto", default="")
    p.add_argument("--texto", required=True)
    p.add_argument("--saida", required=True)
    p.add_argument("--velocidade", type=float, default=1.0)
    p.add_argument("--passos", type=int, default=32)
    p.add_argument("--semente", type=int, default=-1)
    p.add_argument("--sem-silencio", action="store_true")
    p.add_argument("--minusculas", action="store_true")
    p.add_argument("--numeros", default="")
    p.add_argument("--dispositivo", default=None)  # cpu/cuda/xpu; sem: o F5 escolhe
    a = p.parse_args()
    try:
        texto = normalizar(Path(a.texto).read_text("utf-8").strip(), a.minusculas, a.numeros)
        if not texto:
            raise ValueError("Texto vazio.")
        ref = a.ref
        if Path(ref).suffix.lower() != ".wav":
            # o pydub do F5 só lê WAV sem ffmpeg; o soundfile lê mp3/flac/ogg
            import soundfile as sf
            dados, sr = sf.read(ref)
            ref = str(Path(tempfile.mkdtemp()) / "ref.wav")
            sf.write(ref, dados, sr)
        ckpt, vocab = local(a.ckpt), local(a.vocab)
        diz("FASE", "carregando o modelo")
        import soundfile as sf
        import torch
        import torchaudio

        def carregar(caminho, *_, **__):
            # o torchaudio novo lê pelo torchcodec, que não existe em todo índice do PyTorch (o XPU não tem)
            dados, sr = sf.read(caminho, dtype="float32", always_2d=True)
            return torch.from_numpy(dados.T.copy()), sr
        torchaudio.load = carregar
        from f5_tts.api import F5TTS
        tts = F5TTS(model=a.arquitetura, ckpt_file=ckpt, vocab_file=vocab, device=a.dispositivo)
        diz("FASE", f"gerando ({tts.device})" + ("" if a.ref_texto else " · transcrevendo a referência"))
        wav, sr, _ = tts.infer(ref_file=ref, ref_text=normalizar(a.ref_texto, a.minusculas, a.numeros) if a.ref_texto else "",
                               gen_text=texto, show_info=lambda *x: None,
                               progress=Progresso, speed=a.velocidade, nfe_step=a.passos,
                               seed=None if a.semente < 0 else a.semente, remove_silence=a.sem_silencio,
                               file_wave=a.saida)
        diz("OK", f"{len(wav) / sr:.2f} {tts.seed}")
        return 0
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        diz("ERRO", f"{e.__class__.__name__}: {e}"[:500].replace("\n", " "))
        return 1


if __name__ == "__main__":
    sys.exit(main())

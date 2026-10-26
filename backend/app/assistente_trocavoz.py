"""Trocador de voz do F.O.R.J.A: o que você fala no microfone sai com a voz do assistente num cabo de áudio virtual
(VB-CABLE: toca no "CABLE Input", o Discord ouve o microfone "CABLE Output"). Whisper transcreve cada frase e a Boca
do ouvido (Kokoro ou a voz treinada, com o filtro GLaDOS se for o caso) fala no cabo. Roda no venv do assistente,
chamado por assistente.py.

    python assistente_trocavoz.py --modelos <pasta do Kokoro> --saida "CABLE Input (VB-Audio Virtual Cable) MME"
                                  [--mic ...] [--whisper small] [--voz pf_dora] [--efeito glados] [--traduz]

Saída no stdout: "PRONTO <saída>", "ERRO <mensagem>", "LOG <texto>". Fecha quando o stdin fecha (o backend saiu).
ponytail: frase a frase (fala → pausa escolhida, 0,6 s por padrão → voz nova), ~1 s de atraso com o Whisper base. Conversão de voz em tempo real (RVC) só se
o atraso incomodar.
"""
from __future__ import annotations

import argparse
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

from assistente_ouvido import ALUCINACAO, BLOCO, INICIO, PRE_ROLL, SR, Boca, diz, dispositivo, frases, normaliza

MAX_TRECHO = SR * 12


def trechos(mic, fim_silencio: int):
    """Frases do microfone, cortadas por energia com o limiar acompanhando o ruído da sala (o mesmo do ouvido)."""
    import numpy as np
    import sounddevice as sd
    blocos: queue.Queue = queue.Queue()
    with sd.InputStream(device=mic, samplerate=SR, channels=1, dtype="float32", blocksize=BLOCO,
                        callback=lambda d, *_: blocos.put(d[:, 0].copy())):
        piso, antes, atual, acima, silencio = 0.003, [], None, 0, 0
        while True:
            b = blocos.get()
            rms = float(np.sqrt(np.mean(b * b)))
            limiar = max(0.008, piso * 3.5)
            if atual is None:
                piso = piso * 0.995 + rms * 0.005 if rms < limiar else piso
                antes = (antes + [b])[-PRE_ROLL:]
                acima = acima + 1 if rms > limiar else 0
                if acima >= INICIO:
                    atual, silencio = list(antes), 0
                    diz("ESTADO", "captando")
                continue
            atual.append(b)
            silencio = silencio + 1 if rms < limiar else 0
            if silencio >= fim_silencio or len(atual) * BLOCO >= MAX_TRECHO:
                diz("ESTADO", "convertendo")
                yield np.concatenate(atual), silencio < fim_silencio
                atual, antes, acima = None, [], 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--modelos", required=True)
    p.add_argument("--saida", required=True)
    p.add_argument("--mic", default="")
    p.add_argument("--whisper", default="small")
    p.add_argument("--voz", default="pf_dora")
    p.add_argument("--efeito", default="")
    p.add_argument("--pausa", type=float, default=0.6)  # segundos de silêncio que fecham a frase
    p.add_argument("--retorno", default="")  # também toca aqui ("padrao" = o alto-falante padrão): você se ouve
    p.add_argument("--traduz", action="store_true")  # voz que só fala inglês: o Whisper já traduz para o inglês
    a = p.parse_args()
    try:
        import sounddevice as sd
        from faster_whisper import WhisperModel
        mic = dispositivo(a.mic, "entrada")
        saida = dispositivo(a.saida, "saida")
        eco = None if not a.retorno else sd.query_devices(kind="output")["index"] if a.retorno == "padrao"             else dispositivo(a.retorno, "saida")
        sd.default.device = (None, saida)
        stt = WhisperModel(a.whisper, device="cpu", compute_type="int8")
        boca = Boca(Path(a.modelos), a.voz, a.efeito, eco)
    except Exception as e:
        diz("ERRO", f"{e.__class__.__name__}: {e}")
        return 1

    def fim() -> None:
        for _ in sys.stdin:
            pass
        os._exit(0)  # o backend fechou o stdin: desligou o trocador ou saiu (sys.exit numa thread não sai)
    threading.Thread(target=fim, daemon=True).start()

    diz("PRONTO", sd.query_devices(saida)["name"])
    fim_da_fala: dict = {}  # texto -> quando a pessoa parou de falar: o log mede o atraso até a voz sair

    def tocou(texto: str) -> None:
        if (t0 := fim_da_fala.pop(texto, None)) is not None:
            diz("LOG", f"voz saiu {time.time() - t0:.1f} s depois do fim da fala ({boca.textos.qsize()} na fila)")
    boca.ao_tocar = tocou

    for audio, no_limite in trechos(mic, max(3, round(a.pausa * SR / BLOCO))):
        t0 = time.time()
        segs, _ = stt.transcribe(audio, language=None if a.traduz else "pt", task="translate" if a.traduz else "transcribe",
                                 beam_size=1, vad_filter=False, condition_on_previous_text=False, without_timestamps=True)
        texto = " ".join(s.text for s in segs).strip()
        # sem o que foi dito: só tamanhos e tempos
        diz("LOG", f"frase de {len(audio) / SR:.1f} s{' (cortada no limite: o silêncio não chegou)' if no_limite else ''}, "
                   f"Whisper {time.time() - t0:.1f} s")
        if not texto or ALUCINACAO.search(normaliza(texto)):
            diz("ESTADO", "falando" if boca.falando.is_set() else "ouvindo")  # tosse, barulho: nada a falar
        else:
            for parte in re.split(r"(?<=,)\s+", texto):  # a primeira parte já toca enquanto a próxima é sintetizada
                fim_da_fala.update(dict.fromkeys(frases(parte), t0))  # as chaves que a Boca vai tocar
                boca.fala(parte)
    return 0


if __name__ == "__main__":
    import faulthandler
    faulthandler.enable()
    sys.exit(main())

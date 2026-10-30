"""Conversor RVC da boca do F.O.R.J.A: a frase que o Piper falou sai com o timbre do modelo RVC (Applio). Roda no venv
do Applio, não no do assistente (torch e o Applio não cabem lá); a Boca do ouvido abre um destes e manda as frases.

    python assistente_rvc.py --applio <pasta do Applio> --pth voz.rvc.pth [--index voz.rvc.index] [--index-rate 0.3]

Sobe e diz "PRONTO" (ou "ERRO <mensagem>" e sai). Depois, um pedido por linha no stdin, "<entrada.wav>\\t<saída.wav>",
e uma resposta por linha: "OK" ou "ERRO <mensagem>". Fecha quando o stdin fecha.
ponytail: troca por arquivos WAV temporários (~1 ms por frase); pipeline direto em memória se isso aparecer no atraso.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--applio", required=True)
    p.add_argument("--pth", required=True)
    p.add_argument("--index", default="")
    p.add_argument("--index-rate", type=float, default=0.3)
    p.add_argument("--protect", type=float, default=0.33)
    p.add_argument("--config", default="")  # o config.json do Applio (precisão, aparelho), se não for o da pasta
    a = p.parse_args()
    saida = sys.stdout
    if a.config:
        os.environ["APPLIO_CONFIG_FILE"] = a.config
    try:
        os.chdir(a.applio)  # o Applio acha os modelos dele (rmvpe, contentvec) pelo caminho relativo
        sys.path.insert(0, a.applio)
        with contextlib.redirect_stdout(io.StringIO()):  # o Applio imprime progresso: o stdout é do protocolo
            from rvc.infer.infer import VoiceConverter
            vc = VoiceConverter()
    except Exception as e:
        print(f"ERRO {e.__class__.__name__}: {e}", file=saida, flush=True)
        return
    print("PRONTO", file=saida, flush=True)
    for linha in sys.stdin:
        entrada, _, destino = linha.rstrip("\n").partition("\t")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                vc.convert_audio(entrada, destino, a.pth, a.index if os.path.isfile(a.index) else "",
                                 index_rate=a.index_rate if os.path.isfile(a.index) else 0, protect=a.protect,
                                 f0_method="rmvpe")
            print("OK" if os.path.isfile(destino) else "ERRO sem saída", file=saida, flush=True)
        except Exception as e:
            print(f"ERRO {e.__class__.__name__}: {e}", file=saida, flush=True)


if __name__ == "__main__":
    main()

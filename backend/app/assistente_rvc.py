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


class _FaissFixo:
    """O faiss do pipeline do Applio, com o índice lido uma vez: ele relia o .index (centenas de MB) e remontava a
    matriz inteira a cada frase, ~0,5 s de cada conversão. Mesma conta, mesmo resultado."""

    def __init__(self, faiss):
        self._faiss, self._lidos = faiss, {}

    def __getattr__(self, nome):
        return getattr(self._faiss, nome)

    def read_index(self, arquivo):
        if arquivo not in self._lidos:
            self._lidos[arquivo] = _IndiceFixo(self._faiss.read_index(arquivo))
        return self._lidos[arquivo]


class _IndiceFixo:
    def __init__(self, indice):
        self._indice, self.ntotal, self._todos = indice, indice.ntotal, None

    def search(self, *a, **k):
        return self._indice.search(*a, **k)

    def reconstruct_n(self, *_):  # o pipeline só pede a matriz inteira (0, ntotal)
        if self._todos is None:
            self._todos = self._indice.reconstruct_n(0, self.ntotal)
        return self._todos


BORDA_S = 0.3  # o Applio converte 1 s de reflexo antes e depois de cada trecho; numa frase falada, 0,3 s soa igual


def borda(p, tgt_sr: int) -> None:
    """Encurta o reflexo das bordas do pipeline (~20% a menos por frase, mais nas curtas). Inteiros: vão para o np.pad."""
    p.t_pad = int(p.sample_rate * BORDA_S)
    p.t_pad_tgt = int(tgt_sr * BORDA_S)
    p.t_pad2 = p.t_pad * 2


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
            from rvc.infer import pipeline
            from rvc.infer.infer import VoiceConverter
            vc = VoiceConverter()
        pipeline.faiss = _FaissFixo(pipeline.faiss)
    except Exception as e:
        print(f"ERRO {e.__class__.__name__}: {e}", file=saida, flush=True)
        return
    print("PRONTO", file=saida, flush=True)
    for linha in sys.stdin:
        entrada, _, destino = linha.rstrip("\n").partition("\t")
        try:
            if vc.vc:  # o pipeline nasce na 1ª conversão (o aquecimento da Boca)
                borda(vc.vc, vc.tgt_sr)
            with contextlib.redirect_stdout(io.StringIO()):
                vc.convert_audio(entrada, destino, a.pth, a.index if os.path.isfile(a.index) else "",
                                 index_rate=a.index_rate if os.path.isfile(a.index) else 0, protect=a.protect,
                                 f0_method="rmvpe")
            print("OK" if os.path.isfile(destino) else "ERRO sem saída", file=saida, flush=True)
        except Exception as e:
            print(f"ERRO {e.__class__.__name__}: {e}", file=saida, flush=True)


if __name__ == "__main__":
    main()

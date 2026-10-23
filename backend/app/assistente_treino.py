"""Treina o detector da palavra "Forja" (roda no venv do assistente, chamado por assistente.py).

    python assistente_treino.py --kokoro <pasta do Kokoro> --oww <pasta dos .onnx do openWakeWord> --saida forja.npz
                                [--ambiente <wav do microfone>] [--minha-voz <pasta com wavs>]

Não precisa de ninguém falando: os exemplos positivos são o Kokoro dizendo "Forja" com todas as vozes dele (de vários
idomas, lendo a fonética do português) em velocidades diferentes, com ruído e volume variados. Os negativos são palavras
parecidas ("força", "fora", "forma"...), frases comuns, ruído e o som ambiente do microfone de quem usa. Gravações da
própria voz (--minha-voz) entram como positivos extras, com peso.

As características são as do openWakeWord (melspectrograma + embedding pré-treinados, 96 números a cada 80 ms); o
classificador é uma MLP pequena sobre as últimas 16 janelas (~1,3 s). Saída: um .npz com os pesos, a normalização e o
limiar, que o ouvido roda com numpy puro (Detector em assistente_ouvido.py).

Progresso no stdout: "FASE <texto>", "PROGRESSO <0..1>"; no fim "OK <json com as métricas>" ou "ERRO <mensagem>".
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import wave
from pathlib import Path

import numpy as np

SR = 16000
JANELA = 2.0  # segundos por exemplo: a palavra termina perto do fim, como no fluxo ao vivo
QUADROS = 16  # janelas de 80 ms que o classificador vê
POSITIVOS = ["Forja.", "Forja!", "Forja,", "Forja?", "Ei, Forja."]
# o que soa parecido (o detector precisa aprender a recusar) e fala comum de casa
PARECIDAS = ["força", "fora", "forma", "forno", "forro", "foi já", "loja", "jóia", "corja", "Borja", "forte", "porta",
             "fórmula", "forjar", "Jorge", "orla", "ferro", "folha", "fofa", "fogo", "fora já", "for", "já",
             "por favor", "olha", "ponha", "força já", "o forno", "a loja", "a porta", "Forte", "Formiga", "fortuna"]
FRASES = ["que horas são", "bom dia", "boa noite", "tudo bem com você", "vamos jantar", "liga a televisão",
          "onde está o controle", "o jogo começou", "amanhã vai chover", "preciso trabalhar", "abre a janela",
          "o café está pronto", "me passa o sal", "que legal", "não sei", "pode ser", "vou dormir",
          "a reunião é às três", "olha só isso", "está muito calor hoje", "o cachorro latiu", "compra pão",
          "quanto custa", "obrigado", "tchau", "até logo", "que filme bom", "a internet caiu", "liga para ele",
          "o que você acha", "vamos sair", "fecha a porta", "estou com fome", "que dia é hoje", "manda mensagem"]


def diz(tipo: str, texto: str = "") -> None:
    print(f"{tipo} {texto}".strip(), flush=True)


def reamostra(a: np.ndarray, de: int) -> np.ndarray:
    if de == SR:
        return a.astype(np.float32)
    return np.interp(np.arange(0, len(a), de / SR), np.arange(len(a)), a).astype(np.float32)


def apara(a: np.ndarray) -> np.ndarray:
    """Tira o silêncio das pontas (o Kokoro deixa um pouco)."""
    e = np.convolve(np.abs(a), np.ones(160) / 160, "same")
    voz = np.where(e > max(0.01, e.max() * 0.05))[0]
    return a[voz[0]:voz[-1] + 1] if len(voz) else a


def ler_wav(p: Path) -> np.ndarray:
    with wave.open(str(p)) as w:
        sr, n, larg = w.getframerate(), w.getnchannels(), w.getsampwidth()
        bruto = np.frombuffer(w.readframes(w.getnframes()), {2: np.int16, 4: np.int32}[larg])
    a = bruto.reshape(-1, n).mean(axis=1) / np.iinfo({2: np.int16, 4: np.int32}[larg]).max
    return reamostra(a, sr)


class Ruidos:
    """Ruído de fundo para misturar: o ambiente gravado do microfone (o mais valioso) e ruído rosa/branco."""

    def __init__(self, ambiente: np.ndarray | None, rng: random.Random):
        self.amb, self.rng = ambiente, rng

    def trecho(self, n: int) -> np.ndarray:
        r = self.rng.random()
        if self.amb is not None and len(self.amb) > n and r < 0.6:
            i = self.rng.randrange(0, len(self.amb) - n)
            return self.amb[i:i + n]
        branco = np.random.default_rng(self.rng.randrange(1 << 30)).standard_normal(n).astype(np.float32)
        if r < 0.8:  # rosa: mais grave, parece ventilador/ar-condicionado
            branco = np.cumsum(branco)
            branco -= np.convolve(branco, np.ones(400) / 400, "same")
            branco /= max(1e-6, np.abs(branco).max())
        return branco * 0.02


def monta(palavra: np.ndarray, ruido: Ruidos, rng: random.Random, fim: float | None = None,
          depois: np.ndarray | None = None) -> np.ndarray:
    """Um exemplo de JANELA s: a fala colocada para terminar em `fim` (fração da janela), ruído por baixo e volume
    variado. `depois`: fala que continua logo em seguida ("Forja, que horas são")."""
    n = int(JANELA * SR)
    x = ruido.trecho(n) * rng.uniform(0.2, 1.5)
    p = palavra * np.exp(rng.uniform(np.log(0.04), np.log(0.9))) / max(1e-6, np.abs(palavra).max())  # de longe a perto
    fim_i = int(n * (fim if fim is not None else rng.uniform(0.82, 0.97)))
    ini = max(0, fim_i - len(p))
    p = p[-(fim_i - ini):]
    x[ini:fim_i] += p
    if depois is not None and fim_i < n:
        d = depois[: n - fim_i] * np.abs(p).max() * rng.uniform(0.7, 1.2) / max(1e-6, np.abs(depois).max())
        x[fim_i:fim_i + len(d)] += d
    return np.clip(x, -1, 1)


def mlp_treina(X: np.ndarray, y: np.ndarray, peso: np.ndarray, rng: int):
    from sklearn.neural_network import MLPClassifier
    clf = MLPClassifier(hidden_layer_sizes=(96,), alpha=1e-3, max_iter=300, early_stopping=True,
                        validation_fraction=0.15, random_state=rng)
    # o sklearn não aceita peso por exemplo na MLP: repete os positivos com peso > 1
    rep = np.repeat(np.arange(len(X)), np.maximum(1, np.round(peso).astype(int)))
    clf.fit(X[rep], y[rep])
    return clf


def prob(clf, X: np.ndarray) -> np.ndarray:
    return clf.predict_proba(X)[:, 1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kokoro", required=True)
    ap.add_argument("--oww", required=True)
    ap.add_argument("--saida", required=True)
    ap.add_argument("--ambiente", default="")
    ap.add_argument("--minha-voz", default="")
    ap.add_argument("--semente", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.semente)
    try:
        from kokoro_onnx import Kokoro
        from openwakeword.utils import AudioFeatures
        diz("FASE", "carregando a voz sintética")
        k = Kokoro(str(Path(a.kokoro) / "kokoro-v1.0.int8.onnx"), str(Path(a.kokoro) / "voices-v1.0.bin"))
        vozes = sorted(k.get_voices())
        ambiente = ler_wav(Path(a.ambiente)) if a.ambiente and Path(a.ambiente).is_file() else None
        ruido = Ruidos(ambiente, rng)

        def fala(texto: str, voz: str, vel: float) -> np.ndarray:
            amostras, sr = k.create(texto, voice=voz, speed=vel, lang="pt-br")
            return apara(reamostra(np.asarray(amostras, np.float32), sr))

        # separa vozes para o teste: o detector tem de funcionar com voz que nunca ouviu
        rng.shuffle(vozes)
        teste_vozes = set(vozes[: max(4, len(vozes) // 6)])
        pos, neg, pos_v, neg_v = [], [], [], []
        frases_fala, frases_teste = [], []  # fala corrida das vozes de treino (negativos difíceis) e das de teste
        total = len(vozes)
        for i, voz in enumerate(vozes):
            diz("FASE", f"gerando exemplos com a voz {voz} ({i + 1} de {total})")
            diz("PROGRESSO", f"{0.7 * i / total:.3f}")
            alvo_p, alvo_n = (pos_v, neg_v) if voz in teste_vozes else (pos, neg)
            for vel in (0.85, 1.0, 1.2):
                w = fala(rng.choice(POSITIVOS), voz, vel)
                for _ in range(3):
                    alvo_p.append(monta(w, ruido, rng))
                # "Forja, que horas são": a palavra termina e a fala segue. Um só por velocidade: com mais, o
                # positivo fica parecido com conversa comum e o limiar sobe (testado em 2026-10-23: acerto 74%)
                cont = fala(rng.choice(FRASES), voz, vel)
                (frases_teste if voz in teste_vozes else frases_fala).append(cont)
                alvo_p.append(monta(w, ruido, rng, fim=rng.uniform(0.7, 0.85), depois=cont))
            for texto in rng.sample(PARECIDAS, 8) + rng.sample(FRASES, 4):
                w = fala(texto, voz, rng.choice((0.85, 1.0, 1.2)))
                alvo_n.append(monta(w, ruido, rng))
                alvo_n.append(monta(w, ruido, rng, fim=rng.uniform(0.3, 0.7)))  # fala no meio da janela
                (frases_teste if voz in teste_vozes else frases_fala).append(w)
            for _ in range(3):  # só ruído, sem fala
                alvo_n.append(np.clip(ruido.trecho(int(JANELA * SR)) * rng.uniform(0.3, 3), -1, 1))
        minha = []
        if a.minha_voz and Path(a.minha_voz).is_dir():
            for p in sorted(Path(a.minha_voz).glob("*.wav")):
                w = apara(ler_wav(p))
                minha += [monta(w, ruido, rng) for _ in range(6)]
        diz("FASE", "extraindo características (openWakeWord)")
        diz("PROGRESSO", "0.72")
        oww = Path(a.oww)
        af = AudioFeatures(melspec_model_path=str(oww / "melspectrogram.onnx"),
                           embedding_model_path=str(oww / "embedding_model.onnx"), inference_framework="onnx")

        def feats(clips: list[np.ndarray]) -> np.ndarray:
            if not clips:
                return np.zeros((0, QUADROS * 96), np.float32)
            x = (np.stack(clips) * 32767).astype(np.int16)
            e = af.embed_clips(x, batch_size=64)
            return e[:, -QUADROS:, :].reshape(len(clips), -1)

        n = int(JANELA * SR)

        def corrida(frases: list[np.ndarray]) -> np.ndarray:
            """Frases emendadas com pausas curtas, com ruído: a conversa da casa, onde o falso alarme aparece."""
            rng.shuffle(frases)
            c = np.concatenate([np.concatenate([f, np.zeros(int(rng.uniform(0.1, 0.5) * SR), np.float32)]) for f in frases])
            return np.clip(c * 0.6 + ruido.trecho(len(c)) * 0.6, -1, 1)

        def varre(c: np.ndarray, passo: int) -> list[np.ndarray]:
            return [c[i:i + n] for i in range(0, len(c) - n, passo)]

        diz("FASE", "extraindo características da conversa corrida")
        # negativos difíceis: janelas de conversa corrida das vozes de treino (cortes que o ao vivo vai ver)
        Xd = feats(varre(corrida(frases_fala), 5120))
        teste_corrida = corrida(frases_teste)
        Xc = feats(varre(teste_corrida, 1280))  # passo de 80 ms, como o ao vivo
        minutos = len(teste_corrida) / SR / 60
        Xp, Xn, Xm = feats(pos), np.concatenate([feats(neg), Xd]), feats(minha)
        Xpv, Xnv = feats(pos_v), feats(neg_v)
        diz("FASE", "treinando o detector")
        diz("PROGRESSO", "0.85")
        X = np.concatenate([Xp, Xm, Xn])
        y = np.concatenate([np.ones(len(Xp) + len(Xm)), np.zeros(len(Xn))])
        peso = np.concatenate([np.ones(len(Xp)), np.full(len(Xm), 3.0), np.ones(len(Xn))])
        media, desvio = X.mean(0), X.std(0) + 1e-6
        clf = mlp_treina((X - media) / desvio, y, peso, a.semente)

        # limiar calibrado nas vozes de teste: o menor que deixa no máximo 1 alarme a cada 3 min de conversa corrida
        # e ~1% das palavras parecidas acendendo (2 janelas seguidas acima, como o ao vivo exige)
        pp, pn = prob(clf, (Xpv - media) / desvio), prob(clf, (Xnv - media) / desvio)
        pc = prob(clf, (Xc - media) / desvio) if len(Xc) else np.zeros(0)

        def alarmes_com(lim: float) -> int:
            # a regra do ao vivo (Detector.alimenta): média de 2 janelas seguidas >= limiar; conta cada subida
            acima = np.r_[False, (pc[1:] + pc[:-1]) / 2 >= lim]
            return int(np.sum(acima[1:] & ~acima[:-1]))
        limiar = 0.5
        for lim in np.arange(0.5, 0.96, 0.01):
            limiar = float(lim)
            if alarmes_com(lim) <= max(1, minutos / 3) and (pn >= lim).mean() <= 0.01:
                break
        acerto = float((pp >= limiar).mean()) if len(pp) else 0.0
        falsos = float((pn >= limiar).mean()) if len(pn) else 0.0
        alarmes = alarmes_com(limiar)

        W1, W2 = clf.coefs_
        b1, b2 = clf.intercepts_
        Path(a.saida).parent.mkdir(parents=True, exist_ok=True)
        np.savez(a.saida, W1=W1, b1=b1, W2=W2, b2=b2, media=media, desvio=desvio, limiar=limiar, quadros=QUADROS)
        diz("PROGRESSO", "1")
        diz("OK", json.dumps({"acerto": round(acerto, 3), "falsos": round(falsos, 4), "limiar": round(limiar, 3),
                              "alarmes_fala_corrida": alarmes, "minutos_fala_corrida": round(minutos, 1),
                              "positivos": len(Xp) + len(Xm), "negativos": len(Xn), "vozes": total,
                              "minha_voz": len(Xm) // 6}))
        return 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        diz("ERRO", f"{e.__class__.__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

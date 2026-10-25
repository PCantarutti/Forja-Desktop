"""Filtro "GLaDOS" sobre a voz do assistente, frase a frase, na CPU (~0,15 s por segundo de fala).

O filtro é o do Pedro (C:\\Projetos\\Mini Projetos\\voz-sintetica\\glados.py), com os parâmetros que ele escolheu ouvindo:
WORLD separa afinação, envelope e aperiodicidade; a afinação é cravada na escala de Sol# maior, com a mediana de 100 ms
achatando cada nota e 25% das sílabas deslocadas de 2 a 5 semitons (a entonação de máquina); a voz desce 2 semitons,
fala a 0,91x, formantes 3% acima, um pouco menos de ar nas vogais; depois passa-alta, realce de presença e flanger.
Roda dentro do ouvido (venv do assistente): precisa de pyworld e scipy; com o pedalboard os efeitos soam melhor.
"""
from __future__ import annotations

import numpy as np

MAJOR = [0, 2, 4, 5, 7, 9, 11]
KEYS = {k: i for i, k in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}
PRESET = dict(scale="major", key="G#", weird_prob=0.25, median_ms=100, flatten=0.3, formant_shift=1.03, transpose=-2,
              speed=0.91, breath=0.9, presence_db=3, mud_db=1, flanger_mix=0.2, reverb_mix=0, seed=558)


def _segmentos(f0):
    v = np.concatenate([[0], (f0 > 0).astype(int), [0]])
    d = np.diff(v)
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _crava(midi, scale, key):
    if scale == "chromatic":
        return np.round(midi)
    allowed = np.array([(key + s) % 12 for s in MAJOR])
    base = np.floor(midi)
    cands = base[:, None] + np.arange(-1, 3)[None, :]
    ok = np.isin(cands % 12, allowed)
    dist = np.where(ok, np.abs(cands - midi[:, None]), np.inf)
    return cands[np.arange(len(midi)), dist.argmin(axis=1)]


def _afina(f0, scale, key, weird_prob, rng, median_frames, flatten, transpose):
    from scipy.signal import medfilt
    out = np.zeros_like(f0)
    centro = np.median(69 + 12 * np.log2(f0[f0 > 0] / 440.0)) if (f0 > 0).any() else 60
    for a, b in _segmentos(f0):
        midi = 69 + 12 * np.log2(f0[a:b] / 440.0)
        midi = centro + (midi - centro) * (1 - flatten) + transpose
        q = _crava(midi, scale, key)
        k = min(median_frames, (b - a) // 2 * 2 + 1)
        if k >= 3:
            q = medfilt(q, k)
        if weird_prob > 0 and rng.random() < weird_prob:  # a sílaba inteira fora do lugar: entonação de máquina
            q = _crava(q + rng.choice([-1, 1]) * rng.integers(2, 6), scale, key)
        out[a:b] = 440.0 * 2 ** ((q - 69) / 12)
    return out


def _formantes(sp, fator):
    bins = np.arange(sp.shape[1])
    return np.ascontiguousarray(np.array([np.interp(bins / fator, bins, q) for q in sp]))


def _velocidade(f0, sp, ap, speed):
    if speed == 1.0:
        return f0, sp, ap
    src = np.arange(int(len(f0) / speed)) * speed
    i0 = np.minimum(src.astype(int), len(f0) - 2)
    w = (src - i0)[:, None]
    lerp = lambda m: np.ascontiguousarray(m[i0] * (1 - w) + m[i0 + 1] * w)
    return np.ascontiguousarray(f0[np.minimum(np.round(src).astype(int), len(f0) - 1)]), lerp(sp), lerp(ap)


def _rbj(kind, f, gain_db, q, sr):
    A, w0 = 10 ** (gain_db / 40), 2 * np.pi * f / sr
    al, c = np.sin(w0) / (2 * q), np.cos(w0)
    if kind == "peak":
        return [1 + al * A, -2 * c, 1 - al * A], [1 + al / A, -2 * c, 1 - al / A]
    s = 2 * np.sqrt(A) * al
    return ([A * ((A + 1) + (A - 1) * c + s), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - s)],
            [(A + 1) - (A - 1) * c + s, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - s])


def _efeitos(y, sr, flanger_mix, reverb_mix, presence_db, mud_db):
    try:
        from pedalboard import Chorus, HighpassFilter, HighShelfFilter, Pedalboard, PeakFilter, Reverb
        board = Pedalboard([
            HighpassFilter(cutoff_frequency_hz=150),
            PeakFilter(cutoff_frequency_hz=500, gain_db=mud_db, q=0.8),
            HighShelfFilter(cutoff_frequency_hz=4000, gain_db=presence_db, q=0.7),
            Chorus(rate_hz=0.3, depth=0.15, centre_delay_ms=8, feedback=0.25, mix=flanger_mix),
            Chorus(rate_hz=0.8, depth=0.08, centre_delay_ms=18, feedback=0.0, mix=0.08),
            Reverb(room_size=0.12, damping=0.2, wet_level=reverb_mix, dry_level=1 - reverb_mix, width=0.6),
        ])
        return board(y.astype(np.float32), sr).astype(np.float64)
    except ImportError:  # sem o pedalboard: o mesmo caminho em scipy, mais simples
        from scipy.signal import butter, fftconvolve, lfilter, sosfilt
        y = sosfilt(butter(2, 150, "highpass", fs=sr, output="sos"), y)
        y = lfilter(*_rbj("peak", 500, mud_db, 0.8, sr), y)
        y = lfilter(*_rbj("shelf", 4000, presence_db, 0.7, sr), y)
        d = int(0.008 * sr)
        comb = lfilter([1], np.r_[1, np.zeros(d - 1), -0.25], y)
        y = (1 - flanger_mix) * y + flanger_mix * comb
        if reverb_mix:
            t = np.arange(int(0.25 * sr)) / sr
            ir = np.random.default_rng(0).standard_normal(len(t)) * np.exp(-t / 0.05)
            y = (1 - reverb_mix) * y + reverb_mix * fftconvolve(y, ir / np.sqrt((ir ** 2).sum()))[: len(y)]
        return y


def aplica(y: np.ndarray, sr: int, p: dict = PRESET) -> np.ndarray:
    """A frase sintetizada (float, mono) com o filtro, de volta em float32 no mesmo sr, com pico em -1 dBFS."""
    import pyworld as pw
    x = np.ascontiguousarray(y, dtype=np.float64)
    f0, t = pw.harvest(x, sr, f0_floor=60, f0_ceil=900, frame_period=5.0)
    sp, ap = pw.cheaptrick(x, f0, t, sr), pw.d4c(x, f0, t, sr)
    rng = np.random.default_rng(p["seed"])
    f0_q = _afina(f0, p["scale"], KEYS[p["key"]], p["weird_prob"], rng, int(p["median_ms"] / 5) // 2 * 2 + 1,
                  p["flatten"], p["transpose"])
    sp_s = _formantes(sp, p["formant_shift"]) if p["formant_shift"] != 1.0 else sp
    ap_s = ap.copy()
    ap_s[f0 > 0] *= p["breath"]  # só nas vogais: consoantes (s, f, x) continuam com o ruído delas
    z = pw.synthesize(*_velocidade(f0_q, sp_s, np.clip(ap_s, 0, 1), p["speed"]), sr, 5.0)
    z = _efeitos(z, sr, p["flanger_mix"], p["reverb_mix"], p["presence_db"], p["mud_db"])
    return (z * (10 ** (-1 / 20) / max(np.abs(z).max(), 1e-9))).astype(np.float32)

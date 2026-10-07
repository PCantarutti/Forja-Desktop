"""Conferência automática do vídeo pronto (tela Conteúdo), antes de entregar como bom ou publicar.

Uma passada do ffmpeg mede tudo de uma vez (ebur128: loudness e pico; silencedetect; blackdetect; freezedetect) e o
ffprobe dá duração, formato e se tem áudio. O que sai daqui é uma lista de problemas em português, que vira o pedido da
revisão automática ao Claude. Texto cortado na tela não dá para medir assim: fica com a conferência de stills do
próprio Claude (regra do pedido).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import localai, native

CAPA = 2.0          # s do começo ignorados: a capa fica parada e sem narração de propósito
FIM = 1.5           # s do fim ignorados: fade de saída
LUFS = (-20.0, -9.0)
PICO = -0.5         # dBTP
SILENCIO = 2.5      # s
PRETO = 0.8         # s
CONGELADO = 10.0    # s


def _exe(nome: str) -> str:
    exe = localai.find_exe(nome) or shutil.which(nome)
    if not exe:
        raise FileNotFoundError(f"Falta o {nome}")
    return str(exe)


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def medir(video: Path) -> dict:
    """Números crus do vídeo: duração, largura, altura, áudio, lufs, pico e trechos de silêncio, preto e congelado."""
    probe = json.loads(subprocess.run(
        [_exe("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json",
         str(video)], capture_output=True, text=True, encoding="utf-8", errors="replace", **native.popen_kwargs()).stdout or "{}")
    streams = probe.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    tem_audio = any(s.get("codec_type") == "audio" for s in streams)
    filtros = ["-vf", f"blackdetect=d={PRETO}:pix_th=0.10,freezedetect=n=0.003:d={CONGELADO}"]
    if tem_audio:
        filtros += ["-af", f"ebur128=peak=true,silencedetect=n=-45dB:d={SILENCIO}"]
    log = subprocess.run([_exe("ffmpeg"), "-hide_banner", "-nostats", "-i", str(video), *filtros, "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", **native.popen_kwargs()).stderr
    resumo = log[log.rfind("Summary:"):] if "Summary:" in log else ""
    lufs = re.search(r"I:\s*(-?[\d.]+) LUFS", resumo)
    pico = re.search(r"Peak:\s*(-?[\d.]+|-inf) dBFS", resumo)

    def trechos(ini: str, fim: str) -> list[tuple[float, float]]:
        a = [float(x) for x in re.findall(ini + r":\s*(-?[\d.]+)", log)]
        b = [float(x) for x in re.findall(fim + r":\s*(-?[\d.]+)", log)]
        return list(zip(a, b + [float(probe.get("format", {}).get("duration") or 0)] * (len(a) - len(b))))
    return {"duracao": float((probe.get("format") or {}).get("duration") or 0), "largura": v.get("width") or 0,
            "altura": v.get("height") or 0, "audio": tem_audio,
            "lufs": float(lufs.group(1)) if lufs else None,
            "pico": None if not pico or pico.group(1) == "-inf" else float(pico.group(1)),
            "silencio": trechos("silence_start", "silence_end"), "preto": trechos("black_start", "black_end"),
            "congelado": trechos(r"lavfi\.freezedetect\.freeze_start", r"lavfi\.freezedetect\.freeze_end")}


def problemas(m: dict, formato: str = "vertical", duracao_min: int = 0, prevista: float = 0) -> list[str]:
    """O que está errado, em frases curtas (vão para a tela e para o pedido de revisão)."""
    out, d = [], m["duracao"]
    if not m["largura"]:
        return ["O arquivo não tem vídeo legível."]
    if (formato == "horizontal") != (m["largura"] > m["altura"]):
        out.append(f"Formato errado: {m['largura']}x{m['altura']} num vídeo {'horizontal' if formato == 'horizontal' else 'vertical'}.")
    if not m["audio"]:
        out.append("O vídeo saiu sem áudio.")
    if duracao_min and d < duracao_min - 1:
        out.append(f"Duração {d:.0f} s, abaixo do mínimo de {duracao_min} s.")
    if prevista and d < prevista * 0.6:
        out.append(f"Duração {d:.0f} s, bem menor que a narração prevista (~{prevista:.0f} s): pode ter cortado fala.")
    if m["audio"] and m["lufs"] is not None:
        if m["lufs"] < LUFS[0]:
            out.append(f"Volume baixo: {m['lufs']:.1f} LUFS (o alvo é -14). Normalize o áudio.")
        elif m["lufs"] > LUFS[1]:
            out.append(f"Volume alto demais: {m['lufs']:.1f} LUFS (o alvo é -14).")
    if m["audio"] and m["pico"] is not None and m["pico"] > PICO:
        out.append(f"Áudio estourando: pico de {m['pico']:.1f} dB.")
    dentro = lambda a, b: b > CAPA and a < d - FIM
    for a, b in m["silencio"]:
        if dentro(a, b) and b - a >= SILENCIO:
            out.append(f"Silêncio de {b - a:.1f} s em {_mmss(a)}.")
    for a, b in m["preto"]:
        if dentro(a, b) and b - a >= PRETO:
            out.append(f"Tela preta de {b - a:.1f} s em {_mmss(a)}.")
    for a, b in m["congelado"]:
        if dentro(a, b) and b - a >= CONGELADO:
            out.append(f"Imagem parada por {b - a:.0f} s em {_mmss(a)}.")
    return out[:12]


def conferir(video: Path, formato: str = "vertical", duracao_min: int = 0, prevista: float = 0) -> dict:
    """{ok, problemas, medidas}. Sem ffmpeg ou com erro na medição: ok com aviso (a conferência não trava a entrega)."""
    try:
        m = medir(video)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"ok": True, "problemas": [], "aviso": f"Conferência automática não rodou: {e}"[:200]}
    p = problemas(m, formato, duracao_min, prevista)
    return {"ok": not p, "problemas": p, "medidas": {k: m[k] for k in ("duracao", "largura", "altura", "lufs", "pico")}}

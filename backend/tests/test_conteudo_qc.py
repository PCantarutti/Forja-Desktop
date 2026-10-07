"""Conferência automática do vídeo pronto: regras (sem ffmpeg) e uma medição de verdade num vídeo sintético."""
import shutil
import subprocess

import pytest

from app import conteudo_qc as Q

BOM = {"duracao": 60.0, "largura": 1080, "altura": 1920, "audio": True, "lufs": -14.2, "pico": -1.5,
       "silencio": [(0.0, 1.9)], "preto": [(59.5, 60.0)], "congelado": [(0.0, 1.5)]}


def test_video_bom_passa_e_capa_e_fim_nao_contam():
    assert Q.problemas(BOM, "vertical", 50, 55) == []


def test_cada_problema_vira_uma_frase():
    ruim = {**BOM, "largura": 1920, "altura": 1080, "lufs": -24.0, "pico": 0.2, "duracao": 30.0,
            "silencio": [(10.0, 14.0)], "preto": [(20.0, 21.5)], "congelado": [(5.0, 18.0)]}
    p = Q.problemas(ruim, "vertical", 50, 60)
    assert any("Formato errado" in x for x in p) and any("Volume baixo" in x for x in p)
    assert any("estourando" in x for x in p) and any("abaixo do mínimo de 50" in x for x in p)
    assert any("narração prevista" in x for x in p) and any("Silêncio de 4.0 s em 0:10" in x for x in p)
    assert any("Tela preta de 1.5 s em 0:20" in x for x in p) and any("Imagem parada por 13 s em 0:05" in x for x in p)
    assert Q.problemas({**BOM, "audio": False}) == ["O vídeo saiu sem áudio."]
    assert Q.problemas({**BOM, "largura": 0}) == ["O arquivo não tem vídeo legível."]


def test_sem_ffmpeg_nao_trava(monkeypatch, tmp_path):
    monkeypatch.setattr(Q, "_exe", lambda nome: (_ for _ in ()).throw(FileNotFoundError("Falta o ffprobe")))
    r = Q.conferir(tmp_path / "x.mp4")
    assert r["ok"] and "não rodou" in r["aviso"]


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="sem ffmpeg no PATH")
def test_mede_um_video_sintetico_de_verdade(tmp_path, monkeypatch):
    monkeypatch.setattr(Q.localai, "find_exe", lambda nome: shutil.which(nome))
    video = tmp_path / "v.mp4"
    # 9 s em 180x320: imagem mexendo, preta de 4 a 5,5 s; som baixinho com 3 s de silêncio no meio (4 a 7 s)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=15:duration=9",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=9",
                    "-filter_complex", "[0:v]drawbox=enable='between(t,4,5.5)':color=black:t=fill[v];"
                                       "[1:a]volume=enable='between(t,4,7)':volume=0,volume=-20dB[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
                   check=True)
    m = Q.medir(video)
    assert m["largura"] == 180 and m["audio"] and 8.5 < m["duracao"] < 9.5
    assert m["lufs"] < -20 and any(3.5 < a < 4.5 for a, _ in m["preto"]) and any(3.5 < a < 4.5 for a, _ in m["silencio"])
    p = Q.conferir(video, "vertical")["problemas"]
    assert any("Volume baixo" in x for x in p) and any("Tela preta" in x for x in p) and any("Silêncio" in x for x in p)

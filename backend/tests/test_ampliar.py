"""Ampliação de vídeo: ESRGAN reconhecido pelo conteúdo, filtros do ffmpeg, catálogo e a tomada nova no lote."""
import time
import zipfile
from pathlib import Path

import pytest

from app import ampliar, config, db, downloads, imagegen, localai, lotes, mirror


@pytest.fixture(autouse=True)
def isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.json")
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "modelos")
    monkeypatch.setattr(imagegen, "OUT_DIR", tmp_path / "imagens")
    monkeypatch.setattr(localai, "VIDEOS", tmp_path / "videos")
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    localai._KINDS.clear()
    (tmp_path / "modelos").mkdir()
    (tmp_path / "imagens").mkdir()
    localai.write_config({**localai._blank(), "image": {**localai.DEFAULT_IMAGE, "out_dir": str(tmp_path / "imagens")}})
    return tmp_path


def pth(pasta: Path, nome: str, pickle: bytes) -> str:
    """Um .pth do PyTorch é um zip com o pickle dentro; o Forja só olha os nomes das camadas nele."""
    f = pasta / nome
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("archive/data.pkl", pickle)
    return str(f)


def test_esrgan_pelo_conteudo_e_pth_qualquer_nao_vira_modelo(isolado):
    m = isolado / "modelos"
    esrgan = pth(m, "qualquer.pth", b"...params_ema conv_first.weight body.0.rdb1.conv1.weight conv_up1...")
    outro = pth(m, "clip.pth", b"...visual.transformer.resblocks...")
    assert ampliar.eh_ampliador(esrgan) and not ampliar.eh_ampliador(outro)
    assert localai.kind_of(Path(esrgan)) == "ampliador"
    assert localai.kind_of(Path(outro)) == "outro"
    assert [a["name"] for a in localai.state()["ampliadores"]] == ["qualquer"]
    assert localai.state()["image_models"] == []


def test_filtros_do_ffmpeg():
    assert ampliar.filtros(832, 480, 2, False, 16) == "scale=1664:960:flags=lanczos"
    assert ampliar.filtros(831, 479, 4, False, 16).startswith("scale=3324:1916")  # par, que o yuv420p exige
    assert ampliar.filtros(832, 480, 2, True, 16).endswith("minterpolate=fps=32:mi_mode=mci:mc_mode=aobmc:vsbmc=1")


def test_catalogo_com_o_que_ja_esta_no_disco(isolado, monkeypatch):
    monkeypatch.setattr(ampliar, "_assets_esrgan", lambda janela: {
        "RealESRGAN_x4plus.pth": {"url": "https://x/RealESRGAN_x4plus.pth", "mb": 67.0},
        "RealESRGAN_x2plus.pth": {"url": "https://x/RealESRGAN_x2plus.pth", "mb": 67.1}})
    monkeypatch.setattr(localai, "find_exe", lambda kind: None)
    pth(isolado / "modelos", "RealESRGAN_x2plus.pth", b"conv_first rdb1")
    c = ampliar.catalogo()
    assert [(x["nome"], x["mb"], bool(x["presente"])) for x in c["modelos"]] == [
        ("RealESRGAN_x4plus.pth", 67.0, False), ("RealESRGAN_x2plus.pth", 67.1, True), ("RealESRGAN_x4plus_anime_6B.pth", 0, False)]
    assert c["ffmpeg"] == ""
    with pytest.raises(ampliar.ToolError, match="ffmpeg"):
        ampliar._ffmpeg()


def _esperar(message_id, timeout=5.0):
    fim = time.time() + timeout
    while time.time() < fim:
        m = lotes._mensagem(message_id)
        if m["status"] != "running":
            return m
        time.sleep(0.02)
    raise AssertionError("não terminou")


def test_ampliar_vira_tomada_nova_e_continuar_refaz_a_ampliacao(isolado, monkeypatch):
    feitas = []

    def falso(entrada, saida, fator, modelo="", suavizar=False, job_id="", progresso=None, previa=None):
        feitas.append((entrada, Path(saida).name, fator, suavizar))
        if len(feitas) == 1:
            raise ampliar.ToolError("ffmpeg falhou")
        progresso and progresso(1, 1, 0.5)
        Path(saida).write_bytes(b"webm")
        return {"w": 1664, "h": 960, "fps": 32.0, "quadros": 63}  # o ffprobe do resultado
    monkeypatch.setattr(ampliar, "ampliar", falso)
    monkeypatch.setattr(ampliar, "_ffmpeg", lambda: Path("ffmpeg.exe"))
    monkeypatch.setattr(localai, "status", lambda: {"running": False})
    with db.session() as s:
        c = db.Conversation(kind="video")
        s.add(c)
        s.commit()
        conv = c.id
    origem = isolado / "imagens" / "a-00-s7.webm"
    origem.write_bytes(b"webm")
    lotes._save(conv, role="user", content="a fox", meta={})
    msg = lotes._save(conv, role="assistant", content="", status="pronto", meta={
        "job": "", "count": 1, "seed_mode": "fixa", "opts": {"width": 832, "height": 480, "fps": 16, "frames": 33},
        "images": [{"path": str(origem), "seed": 7, "model": "m.gguf", "model_name": "m", "status": "pronta", "error": ""}]})
    nova = lotes.ampliar(msg.id, str(origem), 2, suavizar=True)
    m = _esperar(nova["id"])
    assert m["status"] == "erro" and m["meta"]["images"][0]["error"] == "ffmpeg falhou"
    o = m["meta"]["opts"]
    assert (o["width"], o["height"], o["fps"], o["frames"]) == (1664, 960, 32, 65)  # 2× e o dobro de quadros
    lotes.continuar(nova["id"])  # "Gerar as que faltaram" refaz a ampliação, não uma geração
    m = _esperar(nova["id"])
    assert m["status"] == "pronto" and Path(m["meta"]["images"][0]["path"]).name == "a-00-s7-2x-suave.webm"
    assert feitas[-1] == (str(origem), "a-00-s7-2x-suave.webm", 2, True)
    assert m["meta"]["opts"]["frames"] == 63  # o que saiu, não a conta de antes
    with pytest.raises(lotes.ToolError, match="2× ou 4×"):
        lotes.ampliar(msg.id, str(origem), 3)

    # um vídeo qualquer do disco: tamanho e fps vêm do ffprobe, o resultado vai para a pasta de vídeos
    fora = isolado / "de-fora" / "ferias.mp4"
    fora.parent.mkdir()
    fora.write_bytes(b"mp4")
    monkeypatch.setattr(ampliar, "sondar", lambda v: {"w": 1280, "h": 720, "fps": 29.97, "taxa": "30000/1001", "quadros": 90, "audio": True})
    nova = lotes.ampliar_arquivo(conv, str(fora), 2)
    m = _esperar(nova["id"])
    assert m["status"] == "pronto" and feitas[-1][0] == str(fora)
    saida = Path(m["meta"]["images"][0]["path"])
    assert saida.parent == isolado / "videos" and saida.name.endswith("-ferias-2x.webm")
    with db.session() as s:
        assert s.get(db.Message, nova["id"] - 1).content == "ferias.mp4"  # o pedido é o nome do arquivo
    with pytest.raises(lotes.ToolError, match="não existe"):
        lotes.ampliar_arquivo(conv, str(isolado / "sumiu.mp4"), 2)


def test_ampliacao_que_caiu_no_meio_nao_vira_pronta(isolado):
    """O ffmpeg grava o webm aos poucos: na subida, o arquivo pela metade não pode contar como tomada pronta."""
    with db.session() as s:
        c = db.Conversation(kind="video")
        s.add(c)
        s.commit()
        conv = c.id
    parcial = isolado / "imagens" / "x-2x.webm"
    parcial.write_bytes(b"pela metade")
    m = lotes._save(conv, role="assistant", content="", status="running", meta={"job": "", "count": 1, "seed_mode": "fixa",
        "opts": {}, "images": [{"path": str(parcial), "seed": 0, "model": "", "model_name": "", "status": "gerando",
                                "error": "", "unidade": "quadro"}]})
    lotes.reap()
    r = lotes._mensagem(m.id)
    assert r["status"] == "interrompido" and r["meta"]["images"][0]["status"] == "interrompida"
    assert not parcial.exists()

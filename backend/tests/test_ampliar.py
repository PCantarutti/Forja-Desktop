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
    monkeypatch.setattr(lotes.projeto, "gpu_alheia", lambda pid: [])  # a GPU de verdade desta máquina não entra
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
        "RealESRGAN_x4plus_anime_6B.pth": {"url": "https://x/RealESRGAN_x4plus_anime_6B.pth", "mb": 17.9}})
    monkeypatch.setattr(localai, "find_exe", lambda kind: None)
    pth(isolado / "modelos", "RealESRGAN_x4plus_anime_6B.pth", b"conv_first rdb1")
    (isolado / "modelos" / "copia").mkdir()
    pth(isolado / "modelos" / "copia", "RealESRGAN_x4plus_anime_6B.pth", b"conv_first rdb1")  # a mesma, em outra pasta
    c = ampliar.catalogo()
    assert len(c["no_disco"]) == 1
    assert [(x["nome"], x["mb"], bool(x["presente"])) for x in c["modelos"]] == [
        ("RealESRGAN_x4plus.pth", 67.0, False), ("RealESRGAN_x4plus_anime_6B.pth", 17.9, True),
        ("4x-UltraSharp.safetensors", 66.9, False), ("seedvr2_3b_fp16.safetensors", 6780, False),
        ("seedvr2_7b_fp8_e4m3fn.safetensors", 8240, False)]
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


def test_ampliar_imagem_sem_ffmpeg_por_lanczos_gerada_e_do_disco(isolado, monkeypatch):
    """Imagem não passa pelo ffmpeg: Lanczos é o Pillow; a tomada é um PNG do tamanho pedido."""
    from PIL import Image

    def sem_ffmpeg():
        raise ampliar.ToolError("Falta o ffmpeg")
    monkeypatch.setattr(ampliar, "_ffmpeg", sem_ffmpeg)
    monkeypatch.setattr(localai, "status", lambda: {"running": False})
    with db.session() as s:
        c = db.Conversation(kind="imagem")
        s.add(c)
        s.commit()
        conv = c.id
    origem = isolado / "imagens" / "gato-s3.png"
    Image.new("RGB", (8, 6), "red").save(origem)
    lotes._save(conv, role="user", content="um gato", meta={})
    msg = lotes._save(conv, role="assistant", content="", status="pronto", meta={
        "job": "", "count": 1, "seed_mode": "fixa", "opts": {"width": 8, "height": 6},
        "images": [{"path": str(origem), "seed": 3, "model": "m.gguf", "model_name": "m", "status": "pronta", "error": ""}]})
    m = _esperar(lotes.ampliar(msg.id, str(origem), 2, suavizar=True)["id"])
    item = m["meta"]["images"][0]
    assert m["status"] == "pronto" and "unidade" not in item and not m["meta"]["opts"]["ampliacao"]["suavizar"]
    assert Path(item["path"]).name == "gato-s3-2x.png" and Image.open(item["path"]).size == (16, 12)

    fora = isolado / "de-fora" / "foto.jpg"
    fora.parent.mkdir()
    Image.new("RGB", (5, 4)).save(fora)
    m = _esperar(lotes.ampliar_arquivo(conv, str(fora), 4)["id"])
    saida = Path(m["meta"]["images"][0]["path"])
    assert saida.parent == isolado / "imagens" and saida.name.endswith("-foto-4x.png")
    assert Image.open(saida).size == (20, 16) and (m["meta"]["opts"]["width"], m["meta"]["opts"]["height"]) == (20, 16)


def safetensors(pasta: Path, nome: str, camadas: list[str]) -> str:
    """Só o cabeçalho importa para o Forja: 8 bytes com o tamanho do JSON, o JSON e os dados."""
    import json, struct
    cab = json.dumps({n: {"dtype": "F16", "shape": [1], "data_offsets": [2 * i, 2 * i + 2]} for i, n in enumerate(camadas)}).encode()
    f = pasta / nome
    f.write_bytes(struct.pack("<Q", len(cab)) + cab + b"\0\0" * len(camadas))
    return str(f)


def test_esrgan_formato_antigo_e_seedvr2_pelo_conteudo(isolado):
    """UltraSharp e os da comunidade nomeiam as camadas do jeito antigo (model.0, RDB1); o SeedVR2 tem os blocos
    com modulação por texto. O VAE dele não vira modelo de imagem."""
    m = isolado / "modelos"
    velho = safetensors(m, "4x-UltraSharp.safetensors", ["model.0.weight", "model.1.sub.0.RDB1.conv1.0.weight"])
    seed = safetensors(m, "qualquer-nome.safetensors", ["blocks.0.ada.txt.attn_gate", "blocks.0.attn.proj.weight"])
    vae = safetensors(m, "seedvr2_ema_vae_fp16.safetensors", ["decoder.conv_in.weight"])
    assert ampliar.eh_ampliador(velho) and not ampliar.eh_seedvr2(velho)
    assert ampliar.eh_seedvr2(seed) and not ampliar.eh_ampliador(seed)
    assert [localai.kind_of(Path(x)) for x in (velho, seed, vae)] == ["ampliador", "ampliador", "outro"]
    no_disco = {x["name"]: x["tipo"] for x in ampliar.catalogo()["no_disco"]}
    assert no_disco == {"4x-UltraSharp": "esrgan", "qualquer-nome": "seedvr2"}
    assert ampliar.vae_seedvr2(seed) == vae  # ao lado do modelo


def test_seedvr2_so_imagem_pede_comfyui_e_vram(isolado, monkeypatch):
    from app import comfy
    seed = safetensors(isolado / "modelos", "seedvr2_3b_fp16.safetensors", ["blocks.0.ada.txt.attn_gate"])
    with pytest.raises(lotes.ToolError, match="só imagem"):
        lotes._validar_ampliacao(2, seed, video=True)
    monkeypatch.setattr(comfy, "python", lambda: None)
    with pytest.raises(lotes.ToolError, match="ComfyUI"):
        lotes._validar_ampliacao(2, seed, video=False)
    monkeypatch.setattr(comfy, "python", lambda: Path("python.exe"))
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "alias": "qwen"})
    monkeypatch.setattr(lotes.projeto, "gpu_alheia", lambda pid: [])
    with pytest.raises(imagegen.ModeloCarregado):  # a tela pergunta (409) antes de descarregar o LLM
        lotes._validar_ampliacao(2, seed, video=False)


def test_comfy_le_as_fases_e_o_resultado_do_driver(isolado, monkeypatch):
    """comfy.ampliar roda o driver e lê o stdout: FASE vira progresso, OK vira tamanho, ERRO vira a mensagem."""
    import subprocess, sys
    from app import comfy
    seed = safetensors(isolado / "modelos", "seedvr2_3b_fp16.safetensors", ["blocks.0.ada.txt.attn_gate"])
    safetensors(isolado / "modelos", "seedvr2_ema_vae_fp16.safetensors", ["decoder.conv_in.weight"])
    falso = isolado / "driver.py"
    monkeypatch.setattr(comfy, "python", lambda: Path(sys.executable))
    monkeypatch.setattr(comfy, "JOB", falso)
    fases = []
    falso.write_text("print('FASE iniciando o ComfyUI'); print('FASE ampliando'); print('OK 1024x768')", encoding="utf-8")
    assert comfy.ampliar("in.png", isolado / "out.png", 4, seed, progresso=fases.append) == {"w": 1024, "h": 768}
    assert fases == ["iniciando o ComfyUI", "ampliando"]
    falso.write_text("import sys; print('ERRO A GPU ficou sem memória'); sys.exit(1)", encoding="utf-8")
    with pytest.raises(lotes.ToolError, match="sem memória"):
        comfy.ampliar("in.png", isolado / "out.png", 4, seed)


def test_7z_mantem_a_arvore_sem_a_pasta_raiz(tmp_path):
    """O ComfyUI portátil vem em .7z com uma pasta raiz; o runtime precisa da árvore (python_embeded/, ComfyUI/)."""
    import os, subprocess
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if not tar.is_file():
        pytest.skip("sem o tar do Windows")
    raiz = tmp_path / "src" / "ComfyUI_windows_portable"
    (raiz / "python_embeded").mkdir(parents=True)
    (raiz / "python_embeded" / "python.exe").write_bytes(b"x")
    (raiz / "ComfyUI").mkdir()
    (raiz / "ComfyUI" / "main.py").write_text("")
    pacote = tmp_path / "p.7z"
    subprocess.run([str(tar), "--format", "7zip", "-cf", str(pacote), "-C", str(tmp_path / "src"), "ComfyUI_windows_portable"], check=True)
    dest = tmp_path / "dest"
    dest.mkdir()
    downloads._unzip(pacote, dest)
    assert (dest / "python_embeded" / "python.exe").is_file() and (dest / "ComfyUI" / "main.py").is_file()


def test_pacote_do_comfyui_pela_placa_de_mais_vram(monkeypatch):
    """A Arc com a integrada AMD ao lado: o pacote é o da Intel, não o da AMD, e sem placa conhecida é o da NVIDIA."""
    from app import comfy, native
    monkeypatch.setattr(native, "placas", lambda: [{"nome": "Intel(R) Arc(TM) B580", "vendor": 0x8086, "vram": 12 << 30},
                                                   {"nome": "AMD Radeon(TM) Graphics", "vendor": 0x1002, "vram": 2 << 30}])
    assert comfy.gpu() == "intel" and comfy.URL.format(versao=comfy.VERSAO, gpu="intel").endswith("_intel.7z")
    monkeypatch.setattr(native, "placas", lambda: [{"nome": "Microsoft Basic Render Driver", "vendor": 0x1414, "vram": 0}])
    assert comfy.gpu() == "nvidia"


def test_caminho_so_com_ascii_para_o_sd_cli(tmp_path):
    """O sd-cli não abre caminho com acento ("Ampliação", "Área de Trabalho"): vai o curto 8.3 ou uma cópia ASCII,
    sempre com o mesmo conteúdo."""
    from app import native
    f = tmp_path / "Ampliação" / "café.png"
    f.parent.mkdir()
    f.write_bytes(b"png de teste")
    a = native.caminho_ascii(f)
    assert a.isascii() and Path(a).read_bytes() == b"png de teste"
    assert native.pasta_ascii().is_dir() and str(native.pasta_ascii()).isascii()


def test_cancelar_mata_o_driver_mesmo_calado(isolado, monkeypatch):
    """Na fase "ampliando" o driver fica minutos sem escrever: cancelar tem de derrubar o processo assim mesmo."""
    import sys, threading
    from app import comfy
    seed = safetensors(isolado / "modelos", "seedvr2_3b_fp16.safetensors", ["blocks.0.ada.txt.attn_gate"])
    safetensors(isolado / "modelos", "seedvr2_ema_vae_fp16.safetensors", ["decoder.conv_in.weight"])
    falso = isolado / "driver.py"
    falso.write_text("import time; print('FASE ampliando', flush=True); time.sleep(60); print('OK 1x1')", encoding="utf-8")
    monkeypatch.setattr(comfy, "python", lambda: Path(sys.executable))
    monkeypatch.setattr(comfy, "JOB", falso)
    job = downloads.create("lote", "teste")
    threading.Timer(1.0, lambda: downloads.cancel(job["id"])).start()
    comeco = time.monotonic()
    with pytest.raises(lotes.ToolError, match="cancelada"):
        comfy.ampliar("in.png", isolado / "out.png", 2, seed, job["id"])
    assert time.monotonic() - comeco < 10

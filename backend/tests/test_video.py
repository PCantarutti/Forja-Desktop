"""Vídeo com o Wan: variante pelo nome, argv do sd-cli, busca e kits de download, lote em .webm."""
import time
from pathlib import Path

import pytest

from app import config, db, imagegen, localai, lotes, mirror


@pytest.fixture(autouse=True)
def isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.json")
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "modelos")
    monkeypatch.setattr(imagegen, "OUT_DIR", tmp_path / "imagens")
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(localai, "find_exe", lambda kind: None)
    # GGUF de mentira: arquitetura pelo nome (wan* = vídeo), sem abrir o arquivo
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": "wan" if "wan" in Path(p).name.lower() else "",
                                                          "n_layer": 0, "n_head": 0})
    localai._KINDS.clear()
    (tmp_path / "modelos").mkdir()
    (tmp_path / "imagens").mkdir()
    localai.write_config({**localai._blank(), "image": {**localai.DEFAULT_IMAGE, "out_dir": str(tmp_path / "imagens")}})
    return tmp_path


def arquivo(pasta: Path, nome: str) -> str:
    f = pasta / nome
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\0" * 64)
    return str(f)


# ------------------------------------------------------------------ variante e tipo

@pytest.mark.parametrize("nome, variante", [
    ("Wan2.1-T2V-1.3B-Q8_0.gguf", "wan21_t2v"),
    ("wan2.1-t2v-14b-Q4_K_M.gguf", "wan21_t2v"),
    ("wan2.1-i2v-14b-480p-Q4_K_M.gguf", "wan21_i2v"),
    ("wan2.1-flf2v-14b-720p-Q8_0.gguf", "wan21_flf2v"),
    ("wan2.1-vace-14b-q8_0.gguf", "wan21_vace"),
    ("Wan2.2-TI2V-5B-Q8_0.gguf", "wan22_ti2v"),
    ("Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf", "wan22_a14b_t2v"),
    ("Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf", "wan22_a14b_i2v"),
    ("wan2.2_i2v_low_noise_14B_fp16.safetensors", "wan22_a14b_i2v"),
])
def test_variante_pelo_nome(nome, variante):
    assert localai.variante_video(nome) == variante


def test_wan_e_video_e_o_vae_dele_nao(isolado):
    m = isolado / "modelos"
    assert localai.kind_of(Path(arquivo(m, "Wan2.2-TI2V-5B-Q8_0.gguf"))) == "video"
    assert localai.kind_of(Path(arquivo(m, "wan2.1_t2v_1.3B_fp16.safetensors"))) == "video"
    assert localai.kind_of(Path(arquivo(m, "wan_2.1_vae.safetensors"))) == "image"
    assert localai.kind_of(Path(arquivo(m, "flux1-dev-Q8_0.gguf"))) == "image"
    assert localai.alto_ruido("Wan2.2-T2V-A14B-HighNoise-Q4_K_M.gguf")
    assert not localai.alto_ruido("Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf")


# ------------------------------------------------------------------ argv

def _o(isolado, modelo: str, **extra) -> dict:
    m = isolado / "modelos"
    return {**localai.DEFAULT_IMAGE, "model": arquivo(m, modelo), "vae": arquivo(m, "wan_2.1_vae.safetensors"),
            "t5xxl": arquivo(m, "umt5-xxl-encoder-Q8_0.gguf"), "frames": 33, "fps": 16, "flow_shift": 3.0, **extra}


def test_argv_texto_para_video(isolado):
    a = imagegen.argv(Path("sd.exe"), "a cat", isolado / "o.webm", _o(isolado, "wan2.1-t2v-14b-Q4_K_M.gguf"))
    assert a[1:3] == ["-M", "vid_gen"]
    assert a[a.index("--video-frames") + 1] == "33" and a[a.index("--fps") + 1] == "16"
    assert a[a.index("--flow-shift") + 1] == "3.0"
    assert "--diffusion-model" in a and "--t5xxl" in a and "-i" not in a and "-r" not in a


def test_argv_imagem_e_quadros(isolado):
    ini, fim = arquivo(isolado, "ini.png"), arquivo(isolado, "fim.png")
    clipv = arquivo(isolado / "modelos", "clip_vision_h.safetensors")
    a = imagegen.argv(Path("sd.exe"), "x", isolado / "o.webm",
                      _o(isolado, "wan2.1-i2v-14b-480p-Q4_K_M.gguf", clip_vision=clipv), [ini])
    assert a[a.index("-i") + 1] == ini and a[a.index("--clip_vision") + 1] == clipv
    a = imagegen.argv(Path("sd.exe"), "x", isolado / "o.webm",
                      _o(isolado, "wan2.1-flf2v-14b-720p-Q4_K_M.gguf", clip_vision=clipv), [ini, fim])
    assert a[a.index("-i") + 1] == ini and a[a.index("--end-img") + 1] == fim


def test_argv_a14b_leva_o_par_highnoise(isolado):
    alto = arquivo(isolado / "modelos", "Wan2.2-T2V-A14B-HighNoise-Q4_K_M.gguf")
    o = _o(isolado, "Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf", high_noise_model=alto, high_noise_steps=8,
           high_noise_cfg=3.5)
    a = imagegen.argv(Path("sd.exe"), "x", isolado / "o.webm", o)
    assert a[a.index("--high-noise-diffusion-model") + 1] == alto
    assert a[a.index("--high-noise-steps") + 1] == "8" and a[a.index("--high-noise-cfg-scale") + 1] == "3.5"


def test_modo_que_o_modelo_nao_faz_e_barrado(isolado):
    ini = arquivo(isolado, "ini.png")
    with pytest.raises(imagegen.ToolError, match="não faz imagem → vídeo.*TI2V"):
        imagegen.argv(Path("sd.exe"), "x", isolado / "o.webm", _o(isolado, "Wan2.1-T2V-1.3B-Q8_0.gguf"), [ini])
    with pytest.raises(imagegen.ToolError, match="não faz texto → vídeo"):
        imagegen.argv(Path("sd.exe"), "x", isolado / "o.webm",
                      _o(isolado, "wan2.1-i2v-14b-480p-Q4_K_M.gguf",
                         clip_vision=arquivo(isolado / "modelos", "clip_vision_h.safetensors")))
    with pytest.raises(imagegen.ToolError, match="VAE"):  # TI2V pede o VAE do 2.2, que não está configurado
        imagegen.argv(Path("sd.exe"), "x", isolado / "o.webm", _o(isolado, "Wan2.2-TI2V-5B-Q8_0.gguf", vae=""))


def test_quadros_sao_4k_mais_1():
    assert imagegen.quadros(2, 16) == 33
    assert imagegen.quadros(3, 24) == 73
    assert all((imagegen.quadros(s, f) - 1) % 4 == 0 for s in (1, 2.5, 5) for f in (16, 24))


# ------------------------------------------------------------------ componentes, busca e kits

def test_kit_baixado_configura_as_pecas_sozinho(isolado):
    m = isolado / "modelos"
    modelo = arquivo(m, "Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf")
    arquivo(m, "Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf")  # par errado (I2V), não pode ganhar
    alto = arquivo(m, "Wan2.2-T2V-A14B-HighNoise-Q4_K_M.gguf")
    vae = arquivo(m / "vae", "wan_2.1_vae.safetensors")
    umt5 = arquivo(m, "umt5-xxl-encoder-Q8_0.gguf")
    p = localai.completar_componentes(modelo)
    assert (p["vae"], p["t5xxl"], p["high_noise_model"]) == (vae, umt5, alto)
    estado = localai.state()
    assert [v["name"] for v in estado["video_models"]] == ["Wan2.2-T2V-A14B-LowNoise-Q4_K_M"]  # sem o HighNoise
    assert estado["video_models"][0]["falta"] == [] and estado["image_models"] == []  # peças não viram modelo


def test_busca_de_video_so_passa_wan(monkeypatch):
    class R:
        status_code = 200

        def json(self):
            return [{"id": "QuantStack/Wan2.2-TI2V-5B-GGUF", "tags": ["gguf", "text-to-video"]},
                    {"id": "someone/wan2.1-anime-lora", "tags": ["lora"]},
                    {"id": "city96/HunyuanVideo-gguf", "tags": ["gguf"]},
                    {"id": "Lightricks/LTX-Video", "tags": []}]
    pedidos = []
    monkeypatch.setattr(localai.httpx, "get", lambda url, **k: pedidos.append(k["params"]) or R())
    achados = localai.search("", "video")
    assert [a["id"] for a in achados] == ["QuantStack/Wan2.2-TI2V-5B-GGUF"]
    assert achados[0]["variante"] == "wan22_ti2v" and achados[0]["modos"] == ["t2v", "i2v"]
    assert pedidos[0]["search"] == "wan" and "pipeline_tag" not in pedidos[0]


def test_kit_lista_so_o_que_falta(isolado, monkeypatch):
    arquivo(isolado / "modelos", "umt5-xxl-encoder-Q8_0.gguf")
    kit = next(k for k in localai.kits_video() if k["id"] == "wan22_ti2v_5b")
    faltam = [Path(a["path"]).name for a in kit["arquivos"] if not a["presente"]]
    assert faltam == ["Wan2.2-TI2V-5B-Q8_0.gguf", "wan2.2_vae.safetensors"]
    assert kit["gb_falta"] == round(5.40 + 1.41, 2) and kit["gb_modelo"] == 5.40
    baixados = []
    monkeypatch.setattr(localai, "download", lambda repo, path, folder="": baixados.append(path) or {"id": path})
    localai.baixar_kit("wan22_ti2v_5b")
    assert [Path(p).name for p in baixados] == faltam


# ------------------------------------------------------------------ lote

def test_lote_em_conversa_de_video_grava_webm(isolado, monkeypatch):
    saidas = []

    def generate(prompt, out, opts=None, job_id="", refs=(), progresso=None, previa=None):
        saidas.append((Path(out), previa))
        Path(out).write_bytes(b"webm")
        return Path(out)
    monkeypatch.setattr(imagegen, "generate", generate)
    monkeypatch.setattr(imagegen, "_exe", lambda: Path("sd-cli.exe"))
    monkeypatch.setattr(imagegen, "argv", lambda *a, **k: ["sd-cli"])
    monkeypatch.setattr(localai, "status", lambda: {"running": False})
    with db.session() as s:
        c = db.Conversation(kind="video")
        s.add(c)
        s.commit()
        conv = c.id
    msg = lotes.start(conv, "a cat", {}, ["C:/m/Wan2.2-TI2V-5B-Q8_0.gguf"], count=2)
    fim = time.time() + 5
    while lotes._mensagem(msg["id"])["status"] == "running" and time.time() < fim:
        time.sleep(0.02)
    assert [o.suffix for o, _ in saidas] == [".webm", ".webm"]
    assert all(p.suffix == ".png" for _, p in saidas)  # a prévia é um quadro, não um vídeo
    lotes.decidir(msg["id"], keep=[])
    assert lotes.limpar_descartadas(dias=0) == 2  # o expurgo leva webm também

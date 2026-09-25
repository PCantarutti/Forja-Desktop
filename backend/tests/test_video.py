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
    monkeypatch.setattr(localai, "VIDEOS", tmp_path / "videos")
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
    a = imagegen.argv(Path("sd.exe"), "a cat", isolado / "o.webm", _o(isolado, "wan2.1-t2v-14b-Q4_K_M.gguf", vae_tiling=True))
    assert a[a.index("--vae-tile-size") + 1] == "16x16" and "--temporal-tiling" in a  # sem isso o VAE do 2.2 estoura


def test_argv_imagem_e_quadros(isolado):
    ini, fim = arquivo(isolado, "ini.png"), arquivo(isolado, "fim.png")
    clipv = arquivo(isolado / "modelos", "clip_vision_h.safetensors")
    a = imagegen.argv(Path("sd.exe"), "x", isolado / "o.webm",
                      _o(isolado, "wan2.1-i2v-14b-480p-Q4_K_M.gguf", clip_vision=clipv), [ini])
    assert a[a.index("-i") + 1] == ini and a[a.index("--clip_vision") + 1] == clipv
    a = imagegen.argv(Path("sd.exe"), "x", isolado / "o.webm",
                      _o(isolado, "wan2.1-flf2v-14b-720p-Q4_K_M.gguf", clip_vision=clipv), [ini, fim])
    assert a[a.index("-i") + 1] == ini and a[a.index("--end-img") + 1] == fim


def test_wan_safetensors_vai_como_diffusion_model(isolado):
    a = imagegen.argv(Path("sd.exe"), "x", isolado / "o.webm", _o(isolado, "wan2.1_t2v_1.3B_fp16.safetensors"))
    assert "--diffusion-model" in a and "-m" not in a


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


def test_erro_de_vram_ocupada_explica_em_portugues():
    ocupada = ["[WARN ] model manager memory on Vulkan1: reported free 65.43 MB / total 12118.00 MB, tracked",
               "[ERROR] conditioner.hpp:1573: GGML_ASSERT(!chunk_hidden_states.empty()) failed",
               "[WARN ] model manager cannot make enough memory available on Vulkan1: need 809.00 MB"]
    assert imagegen.dica_de_falha(ocupada).startswith("A GPU estava com só 65 MB livres de 12 GB")
    grande = ["reported free 11313.05 MB / total 12118.00 MB", "cannot make enough memory available"]
    assert "Pesos na RAM" in imagegen.dica_de_falha(grande, video=True)  # livre e mesmo assim não coube
    assert imagegen.dica_de_falha(["qualquer outro erro"]) == ""
    assert imagegen.rotulo_codigo(3221226505) == "3221226505, 0xC0000409"
    assert imagegen.rotulo_codigo(1) == "1"


def test_gpu_do_video_e_a_do_sd_cpp(isolado, monkeypatch):
    monkeypatch.setattr(localai, "find_exe", lambda kind: Path(f"{kind}.exe"))
    monkeypatch.setattr(imagegen, "_gpu", lambda exe: "vulkan1")
    monkeypatch.setattr(localai, "devices", lambda exe: [
        {"id": "Vulkan0", "name": "AMD Radeon(TM) Graphics", "total": 16 << 30, "free": 16 << 30},
        {"id": "Vulkan1", "name": "Intel(R) Arc(TM) B580 Graphics", "total": 12118 << 20, "free": 11 << 30}])
    assert localai.gpu_video() == {"nome": "Intel(R) Arc(TM) B580 Graphics", "gb": 11.8,  # não a integrada
                                   "folga": localai.FOLGA_VRAM}


def test_nomes_que_nao_sao_video(isolado):
    m = isolado / "modelos"
    for nome in ("swan_lake_xl.safetensors", "wan21_causvid_lora.safetensors", "Taiwan-landscape.safetensors"):
        assert localai.kind_of(Path(arquivo(m, nome))) == "image", nome


def test_codificador_gguf_nao_vira_modelo_de_chat(isolado, monkeypatch):
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": "t5encoder", "n_layer": 24, "n_head": 64})
    assert localai.kind_of(Path(arquivo(isolado / "modelos", "umt5-xxl-encoder-Q8_0.gguf"))) == "codificador"


def test_trocar_variante_refaz_pecas_e_sugeridos(isolado):
    m = isolado / "modelos"
    modelo = arquivo(m, "wan-generico-Q8_0.gguf")  # sem marcador: cai em wan21_t2v
    arquivo(m, "wan_2.1_vae.safetensors")
    vae22 = arquivo(m, "wan2.2_vae.safetensors")
    arquivo(m, "umt5-xxl-encoder-Q8_0.gguf")
    assert localai.completar_componentes(modelo)["vae"].endswith("wan_2.1_vae.safetensors")
    localai.save_image_params(modelo, {**localai.image_params(modelo), "variante": "wan22_ti2v"})
    p = localai.completar_componentes(modelo)
    assert p["vae"] == vae22 and p["fps"] == 24 and p["frames"] == 49


def test_padrao_da_aba_video_nao_leva_ajustes_de_modelo(isolado):
    localai.set_video({"model": "C:/m/x.gguf", "fps": 24, "frames": 49, "seed": 7, "negative": "blur"})
    import os
    assert localai.read_config()["video"] == {"model": os.path.normpath("C:/m/x.gguf"), "negative": "blur"}


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

    arquivo(m, "Wan2.2-TI2V-5B-Q8_0.gguf")
    vae22 = arquivo(m, "wan2.2_vae.safetensors")
    estado = localai.state()  # na mesma resposta em que o 5B pega o VAE do 2.2, o VAE sai da lista de imagem
    assert vae22 not in [i["path"] for i in estado["image_models"]]


def test_busca_de_video_so_passa_wan(monkeypatch):
    class R:
        status_code = 200

        def json(self):
            return [{"id": "QuantStack/Wan2.2-TI2V-5B-GGUF", "tags": ["gguf", "text-to-video"]},
                    {"id": "someone/wan2.1-anime-lora", "tags": ["lora"]},
                    {"id": "city96/HunyuanVideo-gguf", "tags": ["gguf"]},
                    {"id": "Lightricks/LTX-Video", "tags": []},
                    {"id": "x/DeBERTa-v3-large-mnli-fever-anli-ling-wanli", "tags": []},
                    {"id": "y/gemma-4-31B-it-abliterated", "tags": ["wan"]},
                    {"id": "Kijai/WanVideo_comfy", "tags": []}]
    pedidos = []
    monkeypatch.setattr(localai.httpx, "get", lambda url, **k: pedidos.append(k["params"]) or R())
    achados = localai.search("", "video")
    assert [a["id"] for a in achados] == ["QuantStack/Wan2.2-TI2V-5B-GGUF", "Kijai/WanVideo_comfy"]
    assert achados[0]["variante"] == "wan22_ti2v" and achados[0]["modos"] == ["t2v", "i2v"]
    assert pedidos[0]["search"] == "wan" and "pipeline_tag" not in pedidos[0]
    assert localai.variante_clara("Kijai/WanVideo_comfy") is None  # genérico: sem chute de T2V
    assert localai.variante_clara("Wan-AI/Wan2.2-TI2V-5B") == "wan22_ti2v"


# O que o Hugging Face responde para cada repositório (tamanhos reais, conferidos em 2026-09-24).
HF = {
    "QuantStack/Wan2.2-TI2V-5B-GGUF": [("Wan2.2-TI2V-5B-Q4_K_M.gguf", 3.43), ("Wan2.2-TI2V-5B-Q5_K_M.gguf", 3.81),
                                       ("Wan2.2-TI2V-5B-Q8_0.gguf", 5.40)],
    "city96/umt5-xxl-encoder-gguf": [("umt5-xxl-encoder-Q4_K_M.gguf", 3.66), ("umt5-xxl-encoder-Q8_0.gguf", 6.04)],
    "Comfy-Org/Wan_2.2_ComfyUI_Repackaged": [("split_files/vae/wan2.2_vae.safetensors", 1.41),
                                             ("split_files/vae/wan_2.1_vae.safetensors", 0.25)],
    "QuantStack/Wan2.2-T2V-A14B-GGUF": [("LowNoise/Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf", 9.65),
                                        ("HighNoise/Wan2.2-T2V-A14B-HighNoise-Q4_K_M.gguf", 9.65),
                                        ("LowNoise/Wan2.2-T2V-A14B-LowNoise-Q8_0.gguf", 15.4),
                                        ("HighNoise/Wan2.2-T2V-A14B-HighNoise-Q8_0.gguf", 15.4)],
    "Comfy-Org/Wan_2.1_ComfyUI_repackaged": [("split_files/vae/wan_2.1_vae.safetensors", 0.25)],
}


def _hf(monkeypatch, vram_gb):
    monkeypatch.setattr(localai, "arquivos_do_repo", lambda repo: [
        {"path": p, "size": int(gb * 1e9)} for p, gb in HF.get(repo, [])])
    monkeypatch.setattr(localai, "vram_video_gb", lambda: vram_gb)


def test_kit_escolhe_a_maior_quantizacao_que_cabe_e_lista_so_o_que_falta(isolado, monkeypatch):
    _hf(monkeypatch, 5.0)  # 5 GB: o Q8 (5,4) não cabe, o Q5 (3,8) cabe
    arquivo(isolado / "modelos", "umt5-xxl-encoder-Q8_0.gguf")  # já no disco: vale esse, mesmo sem caber
    kit = next(k for k in localai.kits_video() if k["id"] == "wan22_ti2v_5b")
    assert kit["quant"] == "Q5_K_M" and [o["cabe"] for o in kit["opcoes"]] == [True, True, False]
    faltam = [Path(a["path"]).name for a in kit["arquivos"] if not a["presente"]]
    assert faltam == ["Wan2.2-TI2V-5B-Q5_K_M.gguf", "wan2.2_vae.safetensors"]
    assert kit["gb_falta"] == round(3.81 + 1.41, 2)
    baixados = []
    monkeypatch.setattr(localai, "download",
                        lambda repo, path, folder="", subpasta="": baixados.append((path, subpasta)) or {"id": path})
    localai.baixar_kit("wan22_ti2v_5b", quant="Q8_0")  # trocada no cartão
    # o modelo e a peça só dele (o VAE do 2.2) na subpasta com o nome do kit
    assert [(Path(p).name, sp) for p, sp in baixados] == [("Wan2.2-TI2V-5B-Q8_0.gguf", "Wan2.2 TI2V 5B"),
                                                           ("wan2.2_vae.safetensors", "Wan2.2 TI2V 5B")]
    # a peça que vários kits usam vai para a pasta comum
    kit = next(k for k in localai.kits_video() if k["id"] == "wan22_a14b_t2v")
    assert {Path(a["path"]).name: a["subpasta"] for a in kit["arquivos"]}["wan_2.1_vae.safetensors"] == localai.PASTA_COMUM


def test_kit_a14b_leva_o_par_na_mesma_quantizacao(isolado, monkeypatch):
    _hf(monkeypatch, 24.0)  # cabe o Q8
    kit = next(k for k in localai.kits_video() if k["id"] == "wan22_a14b_t2v")
    nomes = [Path(a["path"]).name for a in kit["arquivos"]]
    assert nomes[:2] == ["Wan2.2-T2V-A14B-LowNoise-Q8_0.gguf", "Wan2.2-T2V-A14B-HighNoise-Q8_0.gguf"]
    assert kit["gb_modelo"] == 15.4  # um de cada vez na VRAM, não a soma


def test_kit_sem_hugging_face_avisa_em_vez_de_quebrar(isolado, monkeypatch):
    def falha(repo):
        raise localai.httpx.ConnectError("sem rede")
    monkeypatch.setattr(localai, "arquivos_do_repo", falha)
    monkeypatch.setattr(localai, "vram_video_gb", lambda: 12.0)
    kits = localai.kits_video()
    assert kits and all("Hugging Face" in k["erro"] for k in kits)
    with pytest.raises(localai.ToolError, match="Hugging Face"):
        localai.baixar_kit("wan22_ti2v_5b")


def test_bloco_do_vae_pela_vram_livre():
    assert imagegen.bloco_vae(None) == 16  # sem saber, o que sempre passou
    # 11,05 GB livres (a B580 no fim da amostragem, com pesos na RAM): o 24 pediu 11.446 MB e estourou
    assert imagegen.bloco_vae(11312.8 / 1024) == 16
    assert imagegen.bloco_vae(13.0) == 24
    assert imagegen.bloco_vae(20.0) == 32
    assert imagegen.bloco_vae(20.0, teto=32) == 24  # a nova tentativa depois de um estouro vai abaixo do que falhou
    # as referências voltam exatas, e o 16 fica bem abaixo do que a B580 tinha livre (e passou)
    assert round(imagegen.necessidade_vae(24)) == 11446 and imagegen.necessidade_vae(16) < 11312.8 - 512


def test_vae_estourado_anota_e_tenta_de_novo_com_bloco_menor(isolado, monkeypatch):
    chamadas = []

    class Proc:
        def __init__(self, a, **k):
            chamadas.append(a)
            self.out = Path(a[a.index("-o") + 1])
            self.falha = len(chamadas) == 1
            self.stdout = iter(["[WARN] model manager memory on Vulkan1: reported free 11312.80 MB / total 12118.00 MB\n",
                                "[WARN] model manager cannot make enough memory available on Vulkan1: need 11445.82 MB device\n",
                                "[ERROR] vae.hpp:312  - vae decode compute failed\n"] if self.falha else ["ok\n"])
            self.returncode = 1 if self.falha else 0

        def wait(self):
            if not self.falha:
                self.out.write_bytes(b"webm")
    monkeypatch.setattr(imagegen.subprocess, "Popen", Proc)
    monkeypatch.setattr(imagegen, "_exe", lambda: Path("sd-cli.exe"))
    monkeypatch.setattr(localai, "vram_livre_para_vae", lambda model, offload: 13.0)  # a conta de referência dá 24
    o = _o(isolado, "Wan2.2-TI2V-5B-Q8_0.gguf", vae=arquivo(isolado / "modelos", "wan2.2_vae.safetensors"),
           vae_tiling=True, offload=True)
    medido = {}
    out = imagegen.generate("a boat", isolado / "o.webm", o, medir=medido)
    assert out.exists() and medido["segundos"] >= 0
    blocos = [a[a.index("--vae-tile-size") + 1] for a in chamadas]
    assert blocos == ["24x24", "16x16"]
    assert localai.vae_medidas(o["vae"]) == {24: 11445.8}  # a próxima conta já sai com o número desta máquina
    assert localai.read_config()["livre_sd_mb"] == 11312.8  # e com a memória que o sd.cpp enxerga de fato


def test_bloco_aprende_com_a_maquina(isolado):
    vae = arquivo(isolado / "modelos", "wan_2.1_vae.safetensors")
    localai.anotar_vae(vae, 32, 9000.0)  # um VAE menor, numa máquina qualquer
    localai.anotar_vae(vae, 24, 7000.0)
    medidas = localai.vae_medidas(vae)
    assert medidas == {32: 9000.0, 24: 7000.0}
    assert round(imagegen.necessidade_vae(32, medidas)) == 9000  # a conta passa a ser a da máquina
    assert imagegen.bloco_vae(11.0, medidas) == 32  # e o bloco grande volta a caber


def test_resolucao_do_i2v_sai_do_nome(isolado):
    m = isolado / "modelos"
    assert list(localai.requisitos(arquivo(m, "wan2.1-i2v-14b-480p-Q4_K_M.gguf"))["resolucoes"]) == ["480p"]
    assert list(localai.requisitos(arquivo(m, "wan2.1-i2v-14b-720p-Q4_K_M.gguf"))["resolucoes"]) == ["720p"]
    assert localai.requisitos(arquivo(m, "Wan2.2-TI2V-5B-Q8_0.gguf"))["multiplo"] == 32


def test_tempo_medido_vai_para_o_estado(isolado):
    modelo = arquivo(isolado / "modelos", "Wan2.2-TI2V-5B-Q8_0.gguf")
    o = {**localai.DEFAULT_IMAGE, "width": 832, "height": 480, "frames": 17, "steps": 12}
    localai.anotar_tempo(modelo, o, 3.35, 92.0)
    localai.anotar_tempo(modelo, {**o, "frames": 49, "steps": 20}, 25.0, 560.0)
    localai.anotar_tempo(modelo, o, 3.2, 90.0)  # mesmo tamanho: substitui, não acumula
    tempos = localai.state()["tempos_video"]
    assert sorted((t["frames"], t["s_passo"]) for t in tempos) == [(17, 3.2), (49, 25.0)]
    assert all(t["model"] == localai._chave(modelo) for t in tempos)


# ------------------------------------------------------------------ lote

def test_lote_em_conversa_de_video_grava_webm(isolado, monkeypatch):
    saidas = []

    def generate(prompt, out, opts=None, job_id="", refs=(), progresso=None, previa=None, medir=None):
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
    assert all(p.suffix == ".webp" for _, p in saidas)  # prévia animada: .png viraria .avi, que não toca
    # o foco decide uma tomada: a outra fica pronta, sem virar "mantida" de carona
    primeira, segunda = [i["path"] for i in lotes._mensagem(msg["id"])["meta"]["images"]]
    lotes.decidir(msg["id"], keep=[primeira], apenas=[primeira])
    estados = {i["path"]: i["status"] for i in lotes._mensagem(msg["id"])["meta"]["images"]}
    assert estados == {primeira: "mantida", segunda: "pronta"}
    lotes.decidir(msg["id"], keep=[])
    assert lotes.limpar_descartadas(dias=0) == 2  # o expurgo leva webm também



def test_kits_automaticos_da_busca_do_hf(isolado, monkeypatch):
    """Cada família de GGUF de um repositório de Wan vira um kit; o duvidoso e o que o curado cobre ficam de fora."""
    repos = [{"id": "x/Wan2.1_14B_VACE-GGUF", "downloads": 30, "variante": "wan21_vace"},
             {"id": "y/Wan2.2-I2V-A14B-GGUF", "downloads": 20, "variante": "wan22_a14b_i2v"},
             {"id": "z/Wan2.2-TI2V-5B-GGUF", "downloads": 10, "variante": "wan22_ti2v"}]
    arquivos = {
        "x/Wan2.1_14B_VACE-GGUF": ["Wan2.1_14B_VACE-Q4_K_M.gguf", "Wan2.1_14B_VACE-Q8_0.gguf",
                                   "Wan2_1-VACE_module_14B-Q4_K_M.gguf",  # só o módulo: fora
                                   "wan2.1-vace-14b-00001-of-00002-Q8_0.gguf"],  # shard: fora
        "y/Wan2.2-I2V-A14B-GGUF": ["low_noise/wan2.2_i2v_low_noise_14B_Q4_K_M.gguf",
                                   "high_noise/wan2.2_i2v_high_noise_14B_Q4_K_M.gguf",
                                   "wan2.2_i2v_animate_14B_Q4_K_M.gguf"],  # Animate: o sd.cpp não roda
        "z/Wan2.2-TI2V-5B-GGUF": ["Wan2.2-TI2V-5B-Q8_0.gguf"],  # o mesmo arquivo do kit curado: fora
    }
    monkeypatch.setattr(localai, "_repos_wan_gguf", lambda: repos)
    monkeypatch.setattr(localai, "arquivos_do_repo", lambda repo, kind="video": [
        {"path": f, "size": 1} for f in arquivos.get(repo, [])])
    localai._descobrir.cache_clear()
    kits = {k["nome"]: k for k in localai.kits_descobertos()}
    assert sorted(kits) == ["Wan2.1_14B_VACE", "wan2.2_i2v_14B"]
    assert kits["Wan2.1_14B_VACE"]["modelo"] == ("x/Wan2.1_14B_VACE-GGUF", "Wan2.1_14B_VACE-*.gguf")
    assert kits["Wan2.1_14B_VACE"]["pecas"] == ["vae21", "umt5"]
    a14b = kits["wan2.2_i2v_14B"]  # o par HighNoise achado pelo nome, pasta inclusive
    assert a14b["par"] == ("y/Wan2.2-I2V-A14B-GGUF", "high_noise/wan2.2_i2v_high_noise_14B_*.gguf")
    assert all(k["id"].startswith("auto:") for k in kits.values())

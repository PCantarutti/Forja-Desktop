"""LoRAs de vídeo: o que o arquivo é pelos tensores, para qual modelo serve, e como vai no prompt do sd.cpp."""
import json
import struct
from pathlib import Path

import pytest

from app import config, imagegen, localai, loras


def safetensors(pasta: Path, nome: str, tensores: dict[str, list[int]]) -> str:
    """Só o cabeçalho (é o que o Forja lê); os pesos não importam aqui."""
    f = pasta / nome
    f.parent.mkdir(parents=True, exist_ok=True)
    cab = json.dumps({k: {"dtype": "BF16", "shape": s, "data_offsets": [0, 0]} for k, s in tensores.items()}).encode()
    f.write_bytes(struct.pack("<Q", len(cab)) + cab)
    return str(f)


def lora_wan(pasta: Path, nome: str, dim: int, rank: int = 64) -> str:
    b = "diffusion_model.blocks.0"
    return safetensors(pasta, nome, {f"{b}.self_attn.q.lora_down.weight": [rank, dim],
                                     f"{b}.self_attn.q.lora_up.weight": [dim, rank],
                                     f"{b}.cross_attn.k.lora_down.weight": [rank, dim]})


@pytest.fixture(autouse=True)
def isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.json")
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "modelos")
    monkeypatch.setattr(localai, "find_exe", lambda kind: None)
    localai._KINDS.clear()
    (tmp_path / "modelos").mkdir()
    localai.write_config(localai._blank())
    return tmp_path


def test_lora_pelos_tensores_e_nao_pelo_nome(isolado):
    m = isolado / "modelos"
    x = lora_wan(m, "qualquer_nome.safetensors", 5120)
    info = loras.info_lora(x)
    assert info == {"wan": True, "dim": 5120, "rank": 64, "ruido": "", "passos": 0}
    assert localai.kind_of(Path(x)) == "lora"
    modelo = safetensors(m, "wan2.1_t2v_14B_bf16.safetensors", {"model.diffusion_model.blocks.0.self_attn.q.weight": [5120, 5120]})
    assert localai.kind_of(Path(modelo)) == "video" and loras.info_lora(modelo) is None


def test_passos_e_ruido_do_acelerador(isolado):
    alto = lora_wan(isolado, "wan2.2_t2v_A14b_high_noise_lora_rank64_lightx2v_4step_1217.safetensors", 5120)
    info = loras.info_lora(alto)
    assert (info["ruido"], info["passos"]) == ("high", 4)


def test_compatibilidade_pela_dimensao_do_modelo(isolado):
    m = isolado / "modelos"
    de14b = loras.info_lora(lora_wan(m, "a.safetensors", 5120))
    modelo_13b = safetensors(m, "wan2.1_t2v_1.3B.safetensors", {"blocks.0.self_attn.q.weight": [1536, 1536]})
    assert loras.dim_do_modelo(modelo_13b) == 1536
    assert not loras.compativel(de14b, 1536) and loras.compativel(de14b, 5120)


def test_tags_relativas_e_high_noise_so_com_par(isolado):
    base = isolado / "loras"
    alto = lora_wan(base / "a14b", "x_high_noise_4step.safetensors", 5120)
    baixo = lora_wan(base / "a14b", "x_low_noise_4step.safetensors", 5120)
    estilo = lora_wan(base / "estilos", "aquarela.safetensors", 5120)
    pasta, texto = loras.tags([{"path": alto, "peso": 1}, {"path": baixo, "peso": 1}, {"path": estilo, "peso": 0.6}], True)
    assert Path(pasta) == base
    assert texto == ("<lora:|high_noise|a14b\\x_high_noise_4step.safetensors:1><lora:a14b\\x_low_noise_4step.safetensors:1>"
                     "<lora:estilos\\aquarela.safetensors:0.6>")
    assert ":" not in texto.replace("<lora:", "").replace(":1>", "").replace(":0.6>", "")  # sem "C:" dentro
    _, sem_par = loras.tags([{"path": alto, "peso": 1}], False)
    assert "|high_noise|" not in sem_par  # modelo sem HighNoise: a marca não quer dizer nada
    with pytest.raises(loras.ToolError, match="não encontrada"):
        loras.tags([{"path": str(base / "sumiu.safetensors")}], False)


def test_argv_leva_as_loras(isolado, monkeypatch):
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": "wan", "n_layer": 0, "n_head": 0, "formas": {}})
    m = isolado / "modelos"
    for n in ("wan_2.1_vae.safetensors", "umt5-xxl-encoder-Q8_0.gguf", "wan2.1-t2v-14b-Q4_K_M.gguf"):
        (m / n).write_bytes(b"\0" * 64)
    acel = lora_wan(m, "wan2.1_t2v_14b_lora_rank64_lightx2v_4step.safetensors", 5120)
    o = {**localai.DEFAULT_IMAGE, "model": str(m / "wan2.1-t2v-14b-Q4_K_M.gguf"), "vae": str(m / "wan_2.1_vae.safetensors"),
         "t5xxl": str(m / "umt5-xxl-encoder-Q8_0.gguf"), "loras": [{"path": acel, "peso": 1.0}]}
    a = imagegen.argv(Path("sd.exe"), "a cat", isolado / "o.webm", o)
    assert a[a.index("-p") + 1] == "a cat<lora:wan2.1_t2v_14b_lora_rank64_lightx2v_4step.safetensors:1>"
    assert Path(a[a.index("--lora-model-dir") + 1]) == m


def test_listagem_do_hf_para_lora_nao_joga_as_loras_fora(monkeypatch):
    class R:
        status_code = 200

        def json(self):
            return [{"type": "file", "path": "wan2.1_t2v_14b_lora_rank64_lightx2v_4step.safetensors", "size": 631_000_000},
                    {"type": "file", "path": "README.md", "size": 100}]
    monkeypatch.setattr(localai.httpx, "get", lambda *a, **k: R())
    assert [f["path"] for f in localai.files("x/y", "lora")] == ["wan2.1_t2v_14b_lora_rank64_lightx2v_4step.safetensors"]
    assert localai.files("x/y", "video") == []  # na de modelos ela sai, como antes


def test_acelerador_da_variante_e_o_que_ja_esta_no_disco(isolado, monkeypatch):
    m = isolado / "modelos"
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": "wan", "n_layer": 0, "n_head": 0, "formas": {}})
    modelo = m / "Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf"
    modelo.write_bytes(b"\0" * 64)
    monkeypatch.setattr(localai, "arquivos_do_repo", lambda repo, kind="video": [
        {"path": "wan2.2_t2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors", "size": 600_000_000},
        {"path": "wan2.2_t2v_A14b_high_noise_lora_rank64_lightx2v_4step_1217.safetensors", "size": 614_000_000},
        {"path": "wan2.2_t2v_A14b_low_noise_lora_rank64_lightx2v_4step_1217.safetensors", "size": 614_000_000}])
    lora_wan(m, "wan2.2_t2v_A14b_low_noise_lora_rank64_lightx2v_4step_1217.safetensors", 5120)
    ac = localai.aceleradores(str(modelo))
    nomes = [(Path(a["path"]).name, bool(a["presente"])) for a in ac["arquivos"]]
    assert nomes == [("wan2.2_t2v_A14b_high_noise_lora_rank64_lightx2v_4step_1217.safetensors", False),  # a mais nova
                     ("wan2.2_t2v_A14b_low_noise_lora_rank64_lightx2v_4step_1217.safetensors", True)]
    baixados = []
    monkeypatch.setattr(localai, "download",
                        lambda repo, path, folder="", subpasta="": baixados.append((path, folder, subpasta)) or {"id": path})
    localai.baixar_acelerador(str(modelo))
    assert [Path(p).name for p, _, _ in baixados] == ["wan2.2_t2v_A14b_high_noise_lora_rank64_lightx2v_4step_1217.safetensors"]
    assert Path(baixados[0][1]) == m and baixados[0][2] == ""  # na pasta de modelos do próprio modelo
    # modelo numa subpasta (kit): o acelerador vai para a mesma subpasta
    (m / "Wan2.2 T2V A14B").mkdir()
    modelo.rename(m / "Wan2.2 T2V A14B" / modelo.name)
    baixados.clear()
    localai.baixar_acelerador(str(m / "Wan2.2 T2V A14B" / modelo.name))
    assert (Path(baixados[0][1]), baixados[0][2]) == (m, "Wan2.2 T2V A14B")
    assert localai.aceleradores(str(m / "Wan2.2-TI2V-5B-Q8_0.gguf"))["motivo"].startswith("Não há acelerador")

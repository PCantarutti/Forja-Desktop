"""IA local: linha de comando do llama-server, varredura de modelos, Hugging Face e sd.exe."""
from pathlib import Path

import pytest

from app import config, db, downloads, imagegen, llm, localai

FIND_EXE_REAL = localai.find_exe  # a fixture troca por None; alguns testes querem o de verdade


@pytest.fixture(autouse=True)
def isolado(tmp_path, monkeypatch):
    """Nada de tocar no local.json, na pasta de modelos nem no runtime instalado de verdade."""
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.json")
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "modelos")
    monkeypatch.setattr(localai, "find_exe", lambda kind: None)  # padrões vêm só do DEFAULT_PARAMS
    (tmp_path / "modelos").mkdir()
    return tmp_path


def gguf(pasta: Path, nome: str, tamanho: int = 1024) -> Path:
    f = pasta / nome
    f.write_bytes(b"\0" * tamanho)
    return f


# ---------------------------------------------------------------- argv do llama-server

def test_argv_manda_so_o_que_o_usuario_mexeu():
    a = localai.argv(Path("bin/llama-server.exe"), "C:/m/modelo.gguf", localai.DEFAULT_PARAMS)

    assert a[0] == str(Path("bin/llama-server.exe")) and a[1:3] == ["-m", "C:/m/modelo.gguf"]
    assert "--jinja" in a  # sem isso o template do gguf não gera tool calls
    assert ["--alias", "modelo"] == a[a.index("--alias"):a.index("--alias") + 2]
    assert ["-c", "8192"] == a[a.index("-c"):a.index("-c") + 2]
    assert ["-fa", "on"] == a[a.index("-fa"):a.index("-fa") + 2]
    # zeros/padrões não viram flag: quem decide é o llama.cpp
    for ausente in ("-t", "-b", "-ub", "-np", "--cache-type-k", "--mlock", "--seed", "--mmproj"):
        assert ausente not in a


def test_argv_traduz_os_controles_avancados():
    p = {**localai.DEFAULT_PARAMS, "ctx": 131072, "ngl": 19, "threads": 6, "batch": 2048, "ubatch": 512,
         "parallel": 2, "flash_attn": False, "cache_type_k": "q8_0", "cache_type_v": "q8_0",
         "kv_unified": True, "mlock": True, "mmap": False, "seed": 42, "rope_freq_base": 10000.0,
         "n_cpu_moe": 30, "n_expert": 8, "mmproj": "C:/m/mmproj.gguf", "ctx_checkpoints": 32}
    a = localai.argv(Path("llama-server.exe"), "C:/m/qwen3-a3b.gguf", p)

    esperado = {"-c": "131072", "-ngl": "19", "-t": "6", "-b": "2048", "-ub": "512", "-np": "2",
                "-fa": "off", "--cache-type-k": "q8_0", "--cache-type-v": "q8_0", "--seed": "42",
                "--rope-freq-base": "10000.0", "--n-cpu-moe": "30", "--ctx-checkpoints": "32",
                "--mmproj": "C:/m/mmproj.gguf"}  # --override-kv depende da arquitetura: teste separado
    for flag, valor in esperado.items():
        assert a[a.index(flag) + 1] == valor, flag
    assert "--kv-unified" in a and "--mlock" in a and "--no-mmap" in a
    assert "--no-kv-offload" not in a


NOVO = frozenset(["-c", "-ngl", "-t", "-fa", "--cache-type-k", "--cache-type-v", "--kv-unified",
                  "--ctx-checkpoints", "--n-cpu-moe", "--load-mode", "--mmproj", "--override-kv"])
ANTIGO = frozenset(["-c", "-ngl", "-t", "-fa", "--cache-type-k", "--cache-type-v", "--mlock", "--no-mmap"])


def test_launch_limita_o_pensamento_do_modelo():
    """Sem --reasoning-budget o llama.cpp usa INT_MAX e a fase de pensamento fica ilimitada."""
    a = localai.argv(Path("llama-server"), "m.gguf", localai.DEFAULT_PARAMS)
    teto = a[a.index("--reasoning-budget") + 1]
    assert int(teto) == max(config.REASONING_BUDGET.values())

    # build que não conhece a opção não pode receber ela: o llama-server sai com código 1
    antigo = localai.argv(Path("llama-server"), "m.gguf", localai.DEFAULT_PARAMS, ANTIGO)
    assert "--reasoning-budget" not in antigo


def test_nao_manda_opcao_que_a_build_nao_conhece():
    """O llama.cpp trocou --mlock/--no-mmap por --load-mode; com o nome errado ele sai com código 1."""
    p = {**localai.DEFAULT_PARAMS, "mlock": True, "mmap": False, "ctx_checkpoints": 32, "n_cpu_moe": 30}

    novo = localai.argv(Path("llama-server"), "m.gguf", p, NOVO)
    assert novo[novo.index("--load-mode") + 1] == "mlock"  # mlock sem mmap
    assert "--mlock" not in novo and "--no-mmap" not in novo
    assert "--ctx-checkpoints" in novo and "--n-cpu-moe" in novo

    antigo = localai.argv(Path("llama-server"), "m.gguf", p, ANTIGO)
    assert "--mlock" in antigo and "--no-mmap" in antigo and "--load-mode" not in antigo
    # a build antiga não conhece estas: ficam de fora em vez de derrubar o servidor
    assert "--ctx-checkpoints" not in antigo and "--n-cpu-moe" not in antigo


def test_load_mode_cobre_as_quatro_combinacoes():
    def modo(mlock, mmap):
        a = localai.argv(Path("x"), "m.gguf", {**localai.DEFAULT_PARAMS, "mlock": mlock, "mmap": mmap}, NOVO)
        return a[a.index("--load-mode") + 1] if "--load-mode" in a else ""

    assert (modo(False, True), modo(True, True), modo(False, False), modo(True, False)) == (
        "", "mmap+mlock", "none", "mlock")


def test_params_salvos_por_modelo(isolado):
    localai.save_params("C:/m/a.gguf", {"ctx": "16384", "flash_attn": False})

    assert localai.params("C:/m/a.gguf")["ctx"] == 16384        # string da UI vira int
    assert localai.params("C:/m/a.gguf")["flash_attn"] is False
    assert localai.params("C:/m/b.gguf") == localai.DEFAULT_PARAMS  # outro modelo segue no padrão

    with pytest.raises(Exception):
        localai.save_params("C:/m/a.gguf", {"ctx": "muito"})


def test_so_guarda_o_que_saiu_do_padrao(isolado):
    """Igual ao LM Studio: o que está no padrão não vira ajuste salvo — e some do destaque."""
    localai.save_params("C:/m/a.gguf", {"ctx": 16384, "ngl": localai.DEFAULT_PARAMS["ngl"]})

    assert localai.overrides("C:/m/a.gguf") == {"ctx": 16384}
    assert localai.read_config()["models"]["C:/m/a.gguf"] == {"ctx": 16384}

    localai.save_params("C:/m/a.gguf", {"ctx": localai.DEFAULT_PARAMS["ctx"]})  # voltou ao padrão
    assert localai.overrides("C:/m/a.gguf") == {}


def test_padroes_saem_do_help_do_binario_e_do_gguf(isolado, monkeypatch):
    """O campo mostra o padrão de verdade (2048, 512...), não 0."""
    ajuda = """
  -b,    --batch-size N                   logical maximum batch size (default: 2048)
  -ub,   --ubatch-size N                  physical maximum batch size (default: 512)
  -ctxcp, --ctx-checkpoints N             number of checkpoints (default: 8)
  -t,    --threads N                      number of CPU threads (default: -1)
"""
    monkeypatch.setattr(localai, "find_exe", lambda kind: Path("llama-server"))
    monkeypatch.setattr(localai, "_help", lambda exe: ajuda)
    localai.help_defaults.cache_clear()
    modelo = _gguf(isolado / "moe.gguf", "qwen35moe", [("qwen35moe.block_count", 4, __import__("struct").pack("<I", 40)),
                                                       ("qwen35moe.context_length", 4, __import__("struct").pack("<I", 262144)),
                                                       ("qwen35moe.expert_used_count", 4, __import__("struct").pack("<I", 8))])

    d = localai.defaults_for(str(modelo))

    assert (d["batch"], d["ubatch"], d["ctx_checkpoints"]) == (2048, 512, 8)
    assert d["threads"] == 0                       # -1 = automático: continua 0 na interface
    assert d["ngl"] == 40 and d["n_expert"] == 8   # do gguf: todas as camadas e os especialistas do modelo
    assert d["ctx"] == 32768                       # 262144 treinados, mas o padrão não estoura a memória
    localai.help_defaults.cache_clear()


# ---------------------------------------------------------------- modelos no disco

def test_scan_junta_shards_e_soma_o_tamanho(isolado):
    pasta = isolado / "modelos"
    gguf(pasta, "solo.gguf", 10)
    for i in (1, 2, 3):
        gguf(pasta, f"grande-{i:05d}-of-00003.gguf", 100)

    achados = localai.scan()

    assert [m["name"] for m in achados] == ["grande", "solo"]
    grande = achados[0]
    assert grande["shards"] == 3 and grande["size"] == 300
    assert grande["path"].endswith("grande-00001-of-00003.gguf")  # o primeiro representa o conjunto


def test_scan_le_as_pastas_adicionadas(isolado):
    extra = isolado / "outra"
    extra.mkdir()
    gguf(extra, "baixado.gguf")

    localai.set_dirs([str(extra)])

    assert str(config.MODELS_DIR) in localai.dirs() and str(extra) in localai.dirs()
    assert [m["name"] for m in localai.scan()] == ["baixado"]
    with pytest.raises(Exception):
        localai.set_dirs([str(isolado / "nao-existe")])


# ---------------------------------------------------------------- Hugging Face

def test_download_pega_todos_os_shards(isolado, monkeypatch):
    capturado = {}
    monkeypatch.setattr(localai.threading, "Thread", lambda target, args, daemon: type(
        "T", (), {"start": lambda _self: capturado.update(urls=args[1], names=args[2], dest=args[3])})())

    localai.download("unsloth/Qwen3-GGUF", "Q4_K_M/qwen3-00001-of-00002.gguf")

    assert capturado["urls"] == [
        "https://huggingface.co/unsloth/Qwen3-GGUF/resolve/main/Q4_K_M/qwen3-00001-of-00002.gguf?download=true",
        "https://huggingface.co/unsloth/Qwen3-GGUF/resolve/main/Q4_K_M/qwen3-00002-of-00002.gguf?download=true"]
    assert capturado["dest"] == config.MODELS_DIR


def test_asset_do_runtime_bate_com_os_nomes_do_github():
    nomes = ["llama-b11064-bin-win-cpu-x64.zip", "llama-b11064-bin-win-vulkan-x64.zip",
             "llama-b11064-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip",
             "llama-b11064-bin-win-cpu-arm64.zip", "sd-master-17860c0-bin-win-vulkan-x64.zip",
             "sd-master-17860c0-bin-win-cuda12-x64.zip", "cudart-sd-bin-win-cu12-x64.zip"]
    import re

    def casa(kind, backend, idx):
        padrao = localai.ASSETS[kind][backend][idx]
        return [n for n in nomes if padrao and re.search(padrao, n)]

    assert casa("llama", "vulkan", 0) == ["llama-b11064-bin-win-vulkan-x64.zip"]
    assert casa("llama", "cpu", 0) == ["llama-b11064-bin-win-cpu-x64.zip"]  # não pega o arm64
    assert casa("llama", "cuda", 0) == ["llama-b11064-bin-win-cuda-12.4-x64.zip"]
    assert casa("llama", "cuda", 1) == ["cudart-llama-bin-win-cuda-12.4-x64.zip"]
    assert casa("sd", "vulkan", 0) == ["sd-master-17860c0-bin-win-vulkan-x64.zip"]
    assert casa("sd", "cuda", 1) == ["cudart-sd-bin-win-cu12-x64.zip"]


# ---------------------------------------------------------------- imagem

def test_caminho_da_imagem_normaliza_a_barra(isolado):
    """Barra normal e invertida são o mesmo caminho; sem normalizar, o seletor da tela some."""
    img = localai.set_image({"model": "D:/Modelos/sd.gguf"})
    assert img["model"] == str(Path("D:/Modelos/sd.gguf"))


def test_argv_do_sd(isolado):
    localai.set_image({"model": "C:/m/sd15.safetensors", "steps": 25, "width": 768})
    o = imagegen._opts({"seed": 7, "negative": "blurry"})
    a = imagegen.argv(Path("sd.exe"), "um gato", isolado / "out.png", o)

    assert a[:3] == ["sd.exe", "-p", "um gato"]  # sem -M: o padrão do sd.cpp já é gerar imagem
    assert a[a.index("-m") + 1] == str(Path("C:/m/sd15.safetensors"))  # caminho normalizado ao salvar
    assert a[a.index("--steps") + 1] == "25" and a[a.index("-W") + 1] == "768"
    assert a[a.index("-n") + 1] == "blurry" and a[a.index("-s") + 1] == "7"
    # semente 0 na UI = aleatória (o padrão do sd.cpp é 42, que repetiria a mesma imagem)
    assert imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts())[-1] == "-1"


def test_gguf_so_do_unet_vai_em_diffusion_model(isolado, monkeypatch):
    """Qwen-Image/Flux em GGUF são só o unet; com -m o sd.cpp não acha os pesos."""
    arch = {"C:/m/qwen-image.gguf": "arch_sem_requisitos", "C:/m/sd15.gguf": ""}
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": arch[str(Path(p).as_posix())]})
    localai.set_image({"model": "C:/m/qwen-image.gguf", "llm": "C:/m/qwen3vl.gguf", "vae": "C:/m/vae.safetensors"})
    a = imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts())
    assert a[a.index("--diffusion-model") + 1] == str(Path("C:/m/qwen-image.gguf")) and "-m" not in a
    assert a[a.index("--llm") + 1] == str(Path("C:/m/qwen3vl.gguf"))
    localai.set_image({"model": "C:/m/sd15.gguf"})
    assert "-m" in imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts())


def test_qwen_image_sem_encoder_explica_o_que_falta(isolado, monkeypatch):
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": "qwen_image21"})
    vae = isolado / "vae.safetensors"
    vae.write_bytes(b"x")
    localai.set_image({"model": "C:/m/qwen.gguf", "vae": str(vae), "llm": "C:/nao/existe.gguf"})
    with pytest.raises(Exception) as e:
        imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts())
    assert "Codificador LLM" in str(e.value) and "VAE:" not in str(e.value)


def test_edicao_passa_referencias_e_mmproj(isolado, monkeypatch):
    """Qwen-Image 2.1 edita: -r por imagem, na ordem, e o mmproj quando o codificador é GGUF."""
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": "qwen_image21"})
    arq = {n: isolado / n for n in ("vae.st", "enc.gguf", "mmproj.gguf", "a.png", "b.png")}
    for f in arq.values():
        f.write_bytes(b"x")
    localai.set_image({"model": "C:/m/qwen.gguf", "vae": str(arq["vae.st"]), "llm": str(arq["enc.gguf"])})
    refs = [str(arq["a.png"]), str(arq["b.png"])]

    with pytest.raises(Exception, match="mmproj"):  # GGUF sem visão: edição barrada, geração não
        imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts(), refs)
    assert "-r" not in imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts())

    monkeypatch.setattr(imagegen, "_gpu", lambda exe: "vulkan1")
    localai.set_image({"llm_vision": str(arq["mmproj.gguf"]), "offload": True, "flash_attn": True,
                       "vae_tiling": True, "te_cpu": "editar"})
    a = imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts(), refs)
    assert [a[i + 1] for i, v in enumerate(a) if v == "-r"] == refs
    assert a[a.index("--llm_vision") + 1] == str(arq["mmproj.gguf"])
    assert "--offload-to-cpu" in a and "--diffusion-fa" in a  # sem isso a edição em 1024² vai à CPU
    assert "--vae-tiling" in a  # sem isso o VAE pede 4,7 GB de uma vez e a Arc perde o dispositivo
    assert a[a.index("--backend") + 1] == "vulkan1,te=cpu"
    assert "--backend" not in imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts())  # só na edição


def test_modelo_que_so_gera_recusa_edicao(isolado, monkeypatch):
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": ""})
    ref = isolado / "a.png"
    ref.write_bytes(b"x")
    localai.set_image({"model": "C:/m/sd15.safetensors"})
    with pytest.raises(Exception, match="não edita"):
        imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts(), [str(ref)])


def test_cancelar_mata_o_sd_mesmo_calado(isolado, monkeypatch):
    """Carregando pesos o sd-cli não imprime nada; o cancelamento não pode esperar a próxima linha."""
    import sys
    import threading
    import time

    from app import downloads
    monkeypatch.setattr(imagegen, "_exe", lambda: Path(sys.executable))
    monkeypatch.setattr(imagegen, "argv", lambda *a, **k: [sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(imagegen, "_opts", lambda o=None: {})
    job = downloads.create("imagem", "teste")
    threading.Timer(0.3, lambda: downloads.cancel(job["id"])).start()
    inicio = time.monotonic()
    with pytest.raises(Exception, match="cancelada"):
        imagegen.generate("x", isolado / "o.png", {}, job["id"])
    assert time.monotonic() - inicio < 5


def test_progresso_le_a_velocidade_nas_duas_unidades():
    rapido = imagegen.PROGRESS.search("  |====>     | 3/8 - 2.50it/s")
    lento = imagegen.PROGRESS.search("  |====>     | 11/20 - 31.78s/it")
    assert rapido.groups() == ("3", "8", "2.50", "it/s")
    assert lento.groups() == ("11", "20", "31.78", "s/it")


def test_barra_do_vae_nao_conta_como_passo(isolado, monkeypatch):
    """Com VAE em blocos o sd.cpp imprime outra barra em s/it; o card não pode ir a 100% com ela."""
    import sys
    saida = "  |####| 16/16 - 1.62s/it\n  |#   | 1/20 - 31.00s/it\n  |####| 16/16 - 1.10s/it\n"
    monkeypatch.setattr(imagegen, "_exe", lambda: Path(sys.executable))
    monkeypatch.setattr(imagegen, "_opts", lambda o=None: {"steps": 20})
    out = isolado / "o.png"
    monkeypatch.setattr(imagegen, "argv", lambda *a, **k: [
        sys.executable, "-c", f"import sys, pathlib; sys.stdout.write({saida!r}); pathlib.Path({str(out)!r}).write_bytes(b'x')"])
    vistos = []
    imagegen.generate("x", out, {}, progresso=lambda p, t, s: vistos.append((p, t, s)))
    assert vistos == [(1, 20, 31.0)]


def test_vae_configurado_some_da_lista_de_imagem(isolado, monkeypatch):
    """VAE e codificador na mesma pasta do modelo: configurados, não aparecem como modelo de imagem."""
    pasta = isolado / "modelos"
    modelo = gguf(pasta, "qwen.safetensors")
    vae = gguf(pasta, "qwen_vae.safetensors")
    monkeypatch.setattr(localai, "hardware", lambda: {"gpus": []})
    nomes = lambda: {m["name"] for m in localai.state()["image_models"]}
    assert nomes() == {"qwen", "qwen_vae"}  # sem configurar, o VAE parece modelo
    localai.save_image_params(str(modelo), {"vae": str(vae).replace("\\", "/")})  # barra da API
    assert nomes() == {"qwen"}


def test_acha_vae_e_codificador_perto_do_modelo(isolado, monkeypatch):
    """Layout real do Pedro: modelo dentro do lmstudio, VAE e codificador numa pasta irmã lá em cima."""
    raiz = isolado / "Modelos-IA"
    repo = raiz / "lmstudio" / "autor" / "Qwen-Image-2.1-GGUF"
    irma = raiz / "Qwen-Image-2.1"
    for p in (repo, irma):
        p.mkdir(parents=True)
    modelo = gguf(repo, "qwen-image-2.1-Q8_0.gguf")
    mmproj = gguf(repo, "mmproj-Qwen3VL-8B-Instruct-F16.gguf")
    vae = gguf(irma, "qwen_image_2.1_vae_bf16.safetensors")
    llm = gguf(irma, "Qwen3VL-8B-Instruct-Q4_K_M.gguf")
    gguf(irma, "leia-me.txt")
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": "qwen_image21"})

    achados = localai.achar_arquivos(str(modelo))

    assert achados == {"vae": [str(vae)], "llm": [str(llm)], "llm_vision": [str(mmproj)]}


def test_modelo_sem_requisitos_nao_procura_nada(isolado, monkeypatch):
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": ""})
    assert localai.achar_arquivos(str(gguf(isolado, "sd15.gguf"))) == {}


def test_sd_sem_modelo_reclama(isolado):
    with pytest.raises(Exception):
        imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts())


def test_progresso_do_sd_so_conta_a_amostragem():
    passo = imagegen.PROGRESS.search("  |======>          | 3/8 - 11.50it/s")
    assert passo and passo.group(1, 2) == ("3", "8")
    # carregar o modelo também imprime barra; ela não pode virar progresso da imagem
    assert not imagegen.PROGRESS.search("  |###############| 686/686 - 2.97GB/s")
    assert not imagegen.PROGRESS.search("[INFO   ] image.cpp:853  - generating image: 1/1 - seed 2")


def test_files_do_hf_mostra_so_o_primeiro_shard(monkeypatch):
    arquivos = [{"type": "file", "path": "Q4/m-00001-of-00002.gguf", "size": 10},
                {"type": "file", "path": "Q4/m-00002-of-00002.gguf", "size": 10},
                {"type": "file", "path": "README.md", "size": 1},
                {"type": "file", "path": "Q8/m-Q8_0.gguf", "size": 20}]
    monkeypatch.setattr(localai.httpx, "get", lambda *a, **k: type(
        "R", (), {"status_code": 200, "json": lambda _self: arquivos})())

    fs = localai.files("repo/x")

    assert [f["path"] for f in fs] == ["Q4/m-00001-of-00002.gguf", "Q8/m-Q8_0.gguf"]
    assert fs[0]["shards"] == 2 and fs[1]["quant"] == "Q8_0"


def _gguf(path, arch: str, antes: list[tuple[str, int, bytes]] = (), tensores: list[tuple[str, int]] = ()):
    """Escreve um gguf mínimo: cabeçalho, KVs e (opcional) tensores f16 com N elementos."""
    import struct

    def s(txt: bytes) -> bytes:
        return struct.pack("<Q", len(txt)) + txt

    kvs = b""
    for chave, vtype, valor in antes:
        kvs += s(chave.encode()) + struct.pack("<I", vtype) + valor
    kvs += s(b"general.architecture") + struct.pack("<I", 8) + s(arch.encode())
    corpo = b""
    for nome, elementos in tensores:
        corpo += s(nome.encode()) + struct.pack("<IQIQ", 1, elementos, 1, 0)  # 1 dim, tipo 1 = f16
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, len(tensores), len(antes) + 1) + kvs + corpo)
    return path


def _u32(n: int) -> bytes:
    import struct
    return struct.pack("<I", n)


def test_le_a_arquitetura_do_gguf(isolado):
    import struct

    simples = _gguf(isolado / "a.gguf", "qwen35moe")
    assert localai.gguf_arch(str(simples)) == "qwen35moe"

    # pula valores de outros tipos antes de achar a chave: u32, array de strings e array numérico
    antes = [("x.count", 4, struct.pack("<I", 7)),
             ("x.names", 9, struct.pack("<IQ", 8, 2) + struct.pack("<Q", 2) + b"ab" + struct.pack("<Q", 1) + b"c"),
             ("x.ids", 9, struct.pack("<IQ", 5, 3) + struct.pack("<iii", 1, 2, 3))]
    assert localai.gguf_arch(str(_gguf(isolado / "b.gguf", "gemma3", antes))) == "gemma3"

    (isolado / "c.gguf").write_bytes(b"nao e gguf")
    assert localai.gguf_arch(str(isolado / "c.gguf")) == ""
    assert localai.gguf_arch(str(isolado / "sumiu.gguf")) == ""


def test_override_de_especialistas_usa_o_prefixo_da_arquitetura(isolado):
    modelo = _gguf(isolado / "moe.gguf", "qwen35moe")
    a = localai.argv(Path("llama-server"), str(modelo), {**localai.DEFAULT_PARAMS, "n_expert": 8}, NOVO)

    assert a[a.index("--override-kv") + 1] == "qwen35moe.expert_used_count=int:8"
    # arquitetura desconhecida: não manda nada em vez de mandar uma chave que o llama.cpp ignora calado
    (isolado / "vazio.gguf").write_bytes(b"xxxx")
    b = localai.argv(Path("llama-server"), str(isolado / "vazio.gguf"), {**localai.DEFAULT_PARAMS, "n_expert": 8}, NOVO)
    assert "--override-kv" not in b


def test_estimativa_separa_gpu_de_ram(isolado):
    """Regras medidas no llama.cpp: sobem as ÚLTIMAS camadas (a de saída primeiro), --n-cpu-moe deixa os
    especialistas na RAM, e só camada de atenção gasta cache KV."""
    kvs = [("h.block_count", 4, _u32(4)), ("h.attention.head_count", 4, _u32(8)),
           ("h.attention.head_count_kv", 4, _u32(2)), ("h.attention.key_length", 4, _u32(64)),
           ("h.context_length", 4, _u32(8192)), ("h.full_attention_interval", 4, _u32(2)),
           ("h.ssm.inner_size", 4, _u32(256))]  # híbrido: as camadas sem atenção são recorrentes
    tensores = [("output.weight", 500)]
    for i in range(4):
        tensores += [(f"blk.{i}.attn_q.weight", 100), (f"blk.{i}.ffn_down_exps.weight", 1000)]
    modelo = _gguf(isolado / "m.gguf", "h", kvs, tensores)

    p = {**localai.DEFAULT_PARAMS, "ctx": 1024, "ngl": 3, "n_cpu_moe": 3, "cache_type_k": "f16",
         "cache_type_v": "f16", "parallel": 1, "ubatch": 512}
    e = localai.estimate(str(modelo), p)

    # ngl 3 = saída + camadas 2 e 3; a 2 tem os experts na CPU (2 < 3), a 3 não
    assert e["weights_gpu"] == (500 + 100 + 100 + 1000) * 2
    assert e["weights_cpu"] == ((100 + 1000) * 2 + 1000) * 2  # camadas 0 e 1 inteiras + os experts da 2
    # atenção nas camadas 1 e 3 (intervalo 2); só a 3 está na GPU
    por_camada = 1024 * 2 * 64 * (2 + 2)
    assert e["kv"] == por_camada * 2 and e["kv_gpu"] == por_camada
    assert e["total"] > e["gpu"] and e["attn_layers"] == 2


def test_estimativa_sem_metadados_nao_inventa(isolado):
    (isolado / "x.gguf").write_bytes(b"nao e gguf")
    assert localai.estimate(str(isolado / "x.gguf"), localai.DEFAULT_PARAMS) == {"ok": False}


def test_download_vai_para_a_pasta_escolhida(isolado, monkeypatch):
    """O bug: o painel não mandava destino e tudo caía na pasta padrão."""
    outra = isolado / "outra"
    outra.mkdir()
    localai.set_dirs([str(outra)])
    capturado = {}
    monkeypatch.setattr(localai.threading, "Thread", lambda target, args, daemon: type(
        "T", (), {"start": lambda _self: capturado.update(dest=args[3])})())

    localai.download("repo/x", "m.gguf", str(outra))
    assert capturado["dest"] == outra
    assert localai.read_config()["download_dir"] == str(outra)  # a próxima já vem nessa pasta

    localai.download("repo/x", "m2.gguf")          # sem destino: repete a última escolha
    assert capturado["dest"] == outra

    # barra trocada é a mesma pasta; antes isso dava "pasta não está na lista"
    localai.download("repo/x", "m3.gguf", str(outra).replace("\\", "/"))
    assert localai.dentro_das_pastas(outra / "sub")

    with pytest.raises(Exception):
        localai.download("repo/x", "m4.gguf", str(isolado / "fora"))


def test_apagar_modelo_leva_shards_e_ajustes(isolado):
    pasta = isolado / "modelos"
    for i in (1, 2):
        gguf(pasta, f"grande-{i:05d}-of-00002.gguf", 10)
    (pasta / "grande-00002-of-00002.gguf.part").write_bytes(b"x")  # download interrompido
    primeiro = str(pasta / "grande-00001-of-00002.gguf")
    localai.save_params(primeiro, {"ctx": 4096})

    apagados = localai.remove_model(primeiro)

    assert len(apagados) == 3 and not list(pasta.glob("*"))
    assert primeiro not in localai.read_config()["models"]


def test_apagar_so_dentro_das_pastas_e_nunca_o_carregado(isolado, monkeypatch):
    fora = isolado / "fora"
    fora.mkdir()
    gguf(fora, "x.gguf")
    with pytest.raises(Exception):
        localai.remove_model(str(fora / "x.gguf"))
    assert (fora / "x.gguf").exists()

    dentro = gguf(isolado / "modelos", "y.gguf")
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "path": str(dentro)})
    with pytest.raises(Exception):
        localai.remove_model(str(dentro))
    assert dentro.exists()


# ---------------------------------------------------------------- inferência (amostragem por modelo)

def test_padroes_de_amostragem_vem_do_gguf(isolado):
    """O gguf traz a amostragem recomendada pelo autor do modelo; é ela que o campo mostra."""
    import struct
    kvs = [("general.sampling.temp", 6, struct.pack("<f", 0.6)),
           ("general.sampling.top_k", 4, _u32(20))]
    modelo = _gguf(isolado / "m.gguf", "qwen3", kvs)

    d = localai.inference_defaults(str(modelo))

    assert d["temperature"] == 0.6 and d["top_k"] == 20
    assert d["top_p"] == 0.95 and d["min_p"] == 0.05      # o que o gguf não diz fica no padrão do llama.cpp
    assert localai.inference_defaults()["temperature"] == 0.8


def test_validacao_da_inferencia():
    assert localai.clean_inference({"temperature": "0.6", "stop": ["</s>", "  ", "x"]}) == {
        "temperature": 0.6, "stop": ["</s>", "x"]}
    for ruim in ({"temperature": 9}, {"top_p": 2}, {"foo": 1}, {"stop": "texto"}, {"top_k": "abc"}):
        with pytest.raises(Exception):
            localai.clean_inference(ruim)


def _corpo(kind: str, cfg: dict) -> dict:
    """Monta o `extra` que o llm.py mandaria para um provedor daquele tipo."""
    config.PROVIDERS["p"] = {"id": "p", "name": "p", "type": kind, "url": "http://x/v1", "api_key": ""}
    original = db.get_model_setting
    db.get_model_setting = lambda m: {"tool_mode": "auto", "vision": "auto", "inference": cfg}
    try:
        extra: dict = {}
        llm._inference("p", "m", extra)
        return extra
    finally:
        db.get_model_setting = original
        config.PROVIDERS.pop("p", None)


def test_amostragem_por_tipo_de_provedor():
    cfg = {"temperature": 0.6, "top_k": 20, "min_p": 0.02, "repeat_penalty": 1.1, "max_tokens": 500,
           "stop": ["</s>"], "think": False, "reasoning_budget": 128}

    llama = _corpo("llamacpp", cfg)
    assert llama["temperature"] == 0.6 and llama["top_k"] == 20 and llama["min_p"] == 0.02
    assert llama["max_tokens"] == 500 and llama["stop"] == ["</s>"]
    assert llama["chat_template_kwargs"] == {"enable_thinking": False} and llama["reasoning_budget"] == 128

    # OpenAI genérico (OpenRouter, API da OpenAI) devolve 400 para top_k/min_p: só vai o que é padrão
    aberto = _corpo("openai", cfg)
    assert aberto == {"temperature": 0.6, "max_tokens": 500, "stop": ["</s>"]}

    ollama = _corpo("ollama", cfg)
    assert ollama["options"] == {"temperature": 0.6, "top_k": 20, "min_p": 0.02, "repeat_penalty": 1.1,
                                 "num_predict": 500, "stop": ["</s>"]}
    assert ollama["think"] is False


def test_amostragem_vazia_nao_mexe_no_corpo():
    assert _corpo("llamacpp", {}) == {}
    # 0 = sem limite e lista vazia não viram parâmetro
    assert _corpo("llamacpp", {"max_tokens": 0, "stop": []}) == {}


def test_faxina_do_orfao_respeita_a_porta(isolado, monkeypatch):
    """O arquivo de pid guarda a porta: registro de outra configuração não pode ser morto por engano."""
    monkeypatch.setattr(localai, "PID_FILE", isolado / "llama.pid")
    chamadas = []
    monkeypatch.setattr(localai.subprocess, "run", lambda *a, **k: chamadas.append(a[0]) or type("R", (), {"returncode": 1})())

    (isolado / "llama.pid").write_text("4321:9999", "utf-8")
    localai.reap_orphan()
    assert not chamadas and (isolado / "llama.pid").exists()  # porta diferente: nem toca

    (isolado / "llama.pid").write_text(f"4321:{config.LOCAL_PORT}", "utf-8")
    localai.reap_orphan()
    assert chamadas and "4321" in chamadas[0] and "IMAGENAME eq llama-server.exe" in chamadas[0]
    assert not (isolado / "llama.pid").exists()


def test_readme_do_hf_vira_markdown_limpo(monkeypatch):
    """Card do HF começa com um bloco de HTML; renderizar HTML de terceiros no app não é opção."""
    bruto = """---
license: apache-2.0
---

<div align="center">
  <img src="https://x/logo.png" width="200">
  <p>Modelo <b>rápido</b></p>
</div>

# Titulo

    Linha que veio de dentro da div

```py
    codigo = "indentado de proposito"
```
"""
    monkeypatch.setattr(localai.httpx, "get",
                        lambda *a, **k: type("R", (), {"status_code": 200, "text": bruto})())

    md = localai._readme("repo/x")

    assert "<div" not in md and "<img" not in md and "license: apache-2.0" not in md
    assert md.startswith("Modelo rápido") and "# Titulo" in md
    assert chr(10) + "Linha que veio de dentro da div" in md   # sem o recuo que viraria bloco de código
    assert '    codigo = "indentado de proposito"' in md       # dentro da cerca o recuo fica


# ---------------------------------------------------------------- imagem x modelo carregado

def test_imagem_pede_confirmacao_com_modelo_na_vram(isolado, monkeypatch):
    """sd.cpp e llama-server brigam pela VRAM: descarregar é preciso, mas derruba o cache do chat."""
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "alias": "qwen3"})
    monkeypatch.setattr(localai, "find_exe", lambda kind: Path("sd-cli"))
    localai.set_image({"model": str(gguf(isolado / "modelos", "sd.gguf"))})
    descarregou = []
    monkeypatch.setattr(localai, "unload", lambda: descarregou.append(True))
    monkeypatch.setattr(imagegen.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda _s: None})())

    with pytest.raises(imagegen.ModeloCarregado):
        imagegen.start_job("gato")
    assert not descarregou

    imagegen.start_job("gato", confirm=True)
    assert descarregou and localai.image_busy()
    localai.set_image_busy(False)


def test_nao_carrega_modelo_durante_a_geracao(isolado, monkeypatch):
    localai.set_image_busy(True)
    try:
        with pytest.raises(Exception, match="imagem"):
            localai.load("qualquer.gguf")
    finally:
        localai.set_image_busy(False)


def test_pastas_padrao(isolado):
    novo = isolado / "modelos-novos"
    r = localai.set_paths(models=str(novo), imagens=str(isolado / "fotos"))

    assert r["models_dir"] == str(novo) and novo.is_dir()          # cria em vez de reclamar
    assert r["image_dir"] == str(isolado / "fotos")
    assert localai.dirs()[0] == str(novo)                          # a padrão é sempre a primeira
    localai.set_dirs([str(novo)])
    assert localai.dirs().count(str(novo)) == 1                    # não duplica com a padrão


def test_gemma4_declara_valores_por_camada(isolado):
    """Isto derrubava o painel com HTTP 500: o Gemma 4 manda head_count_kv como lista, uma por camada.

    E cada tipo de camada gasta cache diferente — a de janela deslizante guarda só os últimos N tokens.
    """
    import struct

    def lista_u32(valores):
        return struct.pack("<IQ", 4, len(valores)) + b"".join(_u32(v) for v in valores)

    def lista_bool(valores):
        return struct.pack("<IQ", 7, len(valores)) + bytes(1 if v else 0 for v in valores)

    kvs = [("gemma4.block_count", 4, _u32(4)),
           ("gemma4.attention.head_count", 4, _u32(16)),
           ("gemma4.attention.head_count_kv", 9, lista_u32([8, 8, 8, 1])),
           ("gemma4.attention.key_length", 4, _u32(512)),
           ("gemma4.attention.key_length_swa", 4, _u32(256)),
           ("gemma4.attention.sliding_window", 4, _u32(1024)),
           ("gemma4.attention.sliding_window_pattern", 9, lista_bool([True, True, True, False])),
           ("gemma4.context_length", 4, _u32(262144))]
    tensores = [("output.weight", 10)] + [(f"blk.{i}.attn_q.weight", 10) for i in range(4)]
    modelo = _gguf(isolado / "gemma.gguf", "gemma4", kvs, tensores)

    info = localai.gguf_info(str(modelo))
    assert [c["kind"] for c in info["layers"]] == ["swa", "swa", "swa", "full"]
    assert info["layers"][0] == {"kind": "swa", "kv_heads": 8, "head_dim": 256, "window": 1024}
    assert info["layers"][3] == {"kind": "full", "kv_heads": 1, "head_dim": 512, "window": 0}

    e = localai.estimate(str(modelo), {**localai.DEFAULT_PARAMS, "ctx": 8192, "ngl": 4,
                                       "cache_type_k": "f16", "cache_type_v": "f16"})
    # as 3 de janela guardam 1024 tokens; só a última guarda o contexto inteiro
    assert e["kv"] == 3 * 1024 * 8 * 256 * 4 + 8192 * 1 * 512 * 4
    assert e["ok"] and e["attn_layers"] == 4


def test_chave_por_camada_nao_derruba_o_resto(isolado):
    """Qualquer outra chave que venha como lista vira o maior valor, em vez de estourar."""
    import struct

    kvs = [("x.block_count", 9, struct.pack("<IQ", 4, 2) + _u32(30) + _u32(48))]
    info = localai.gguf_info(str(_gguf(isolado / "x.gguf", "x", kvs)))

    assert info["n_layer"] == 48


def test_separa_modelo_de_chat_de_modelo_de_imagem(isolado):
    """Um .gguf de chat aparecia na lista de modelos de imagem: os dois usam a mesma extensão."""
    import struct

    pasta = isolado / "modelos"
    chat = _gguf(pasta / "chat.gguf", "qwen3",
                 [("qwen3.block_count", 4, _u32(28)), ("qwen3.attention.head_count", 4, _u32(16))])
    difusao = _gguf(pasta / "sd.gguf", "sd1")          # sem camadas nem cabeças: não é modelo de linguagem
    (pasta / "checkpoint.safetensors").write_bytes(b"x")

    achados = {m["name"]: m["kind"] for m in localai.scan(localai.WEIGHTS)}

    assert achados == {"chat": "chat", "sd": "image", "checkpoint": "image"}
    assert [m["name"] for m in localai.scan()] == ["chat", "sd"]  # scan() padrão continua só .gguf


def test_ajustes_por_modelo_de_imagem(isolado):
    """Flux e SD 1.5 querem CFG e passos diferentes; o que está no padrão geral não vira ajuste salvo."""
    localai.set_image({"steps": 20, "cfg": 7.0})

    localai.save_image_params("C:/m/flux.gguf", {"steps": 4, "cfg": 1.0})

    assert localai.image_params("C:/m/flux.gguf")["steps"] == 4
    assert localai.read_config()["image_models"][str(Path("C:/m/flux.gguf"))] == {"steps": 4, "cfg": 1.0}
    assert localai.image_params("C:/m/outro.gguf")["steps"] == 20      # outro modelo segue o padrão

    localai.save_image_params("C:/m/flux.gguf", {"steps": 20})          # voltou ao padrão: some do arquivo
    assert localai.read_config()["image_models"][str(Path("C:/m/flux.gguf"))] == {"cfg": 1.0}


# ---------------------------------------------------------------- busca: ordem e filtro de imagem

def _resposta(itens):
    return type("R", (), {"status_code": 200, "json": lambda _s: itens})()


def test_ordem_da_busca(monkeypatch):
    """Relevância é o ranking do próprio HF: manda a busca SEM sort, senão vira lista por downloads."""
    vistos = {}
    monkeypatch.setattr(localai.httpx, "get", lambda *a, **k: vistos.update(k["params"]) or _resposta([]))

    localai.search("qwen", "text", 5, "relevancia")
    assert "sort" not in vistos

    localai.search("qwen", "text", 5, "curtidas")
    assert vistos["sort"] == "likes" and vistos["direction"] == -1

    localai.search("qwen", "text", 5, "recentes")
    assert vistos["sort"] == "lastModified"


def test_busca_de_imagem_ignora_peca_solta(monkeypatch):
    """LoRA, ControlNet e afins não carregam no sd.cpp: são complemento, não modelo."""
    itens = [{"id": "org/sdxl-base", "tags": ["text-to-image"]},
             {"id": "org/detail-lora-sdxl", "tags": ["lora"]},
             {"id": "org/controlnet-canny", "tags": ["controlnet"]},
             {"id": "org/flux-gguf", "tags": ["gguf"]}]
    monkeypatch.setattr(localai.httpx, "get", lambda *a, **k: _resposta(itens))

    achados = [m["id"] for m in localai.search("sdxl", "image")]

    assert achados == ["org/sdxl-base", "org/flux-gguf"]


def test_arquivos_de_imagem_so_o_que_o_sd_abre(monkeypatch):
    arquivos = [{"type": "file", "path": "flux1-dev-Q4_0.gguf", "size": 6 << 30},
                {"type": "file", "path": "unet/diffusion_pytorch_model.safetensors", "size": 6 << 30},
                {"type": "file", "path": "loras/detalhe.safetensors", "size": 200 << 20},
                {"type": "file", "path": "ae.safetensors", "size": 300 << 20},
                {"type": "file", "path": "embeddings/x.safetensors", "size": 1 << 20},
                {"type": "file", "path": "flux1-dev-Q2_K.gguf", "size": 3 << 30}]
    monkeypatch.setattr(localai.httpx, "get", lambda *a, **k: _resposta(arquivos))

    achados = [f["path"] for f in localai.files("org/flux", "image")]

    # fora: pasta do diffusers, LoRA e embedding. dentro: os gguf e o VAE, do menor para o maior
    assert achados == ["ae.safetensors", "flux1-dev-Q2_K.gguf", "flux1-dev-Q4_0.gguf"]


# ---------------------------------------------------------------- retomada de download

class _Resposta:
    """Resposta falsa do httpx.stream, com os pedaços que a gente quiser."""

    def __init__(self, status, pedacos, headers=None):
        self.status_code, self._pedacos, self.headers = status, pedacos, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_bytes(self, _n=0):
        yield from self._pedacos


def test_download_retoma_do_que_ja_baixou(isolado, monkeypatch):
    """20 GB pela metade não podem recomeçar do zero porque a rede piscou."""
    destino = isolado / "m.gguf"
    parcial = isolado / "m.gguf.part"
    parcial.write_bytes(b"a" * 100)
    vistos = {}

    def stream(_metodo, _url, **k):
        vistos.update(k.get("headers") or {})
        return _Resposta(206, [b"b" * 50], {"content-length": "50"})

    monkeypatch.setattr(downloads.httpx, "stream", stream)
    job = downloads.create("modelo", "m")

    downloads._fetch("http://x/m.gguf", destino, job, 0, 0)

    assert vistos["Range"] == "bytes=100-"          # pediu só o que falta
    assert destino.read_bytes() == b"a" * 100 + b"b" * 50   # colou no que já tinha
    assert not parcial.exists()


def test_download_que_falha_guarda_o_pedaco(isolado, monkeypatch):
    def stream(*_a, **_k):
        raise OSError("rede caiu")

    monkeypatch.setattr(downloads.httpx, "stream", stream)
    (isolado / "m.gguf.part").write_bytes(b"x" * 10)
    job = downloads.create("modelo", "m")

    with pytest.raises(OSError):
        downloads._fetch("http://x/m.gguf", isolado / "m.gguf", job, 0, 0)

    assert (isolado / "m.gguf.part").read_bytes() == b"x" * 10  # o .part FICA para a próxima tentativa


def test_download_checa_espaco(isolado, monkeypatch):
    monkeypatch.setattr(downloads.shutil, "disk_usage", lambda _p: type("U", (), {"free": 1 << 20})())
    monkeypatch.setattr(downloads.httpx, "stream",
                        lambda *a, **k: _Resposta(200, [], {"content-length": str(50 << 30)}))
    job = downloads.create("modelo", "m")

    with pytest.raises(RuntimeError, match="Espaço insuficiente"):
        downloads._fetch("http://x/m.gguf", isolado / "m.gguf", job, 0, 0)


# ---------------------------------------------------------------- motor, GPU e proteções

def test_troca_de_motor(isolado, monkeypatch):
    """CPU, Vulkan e CUDA convivem no disco; escolher é só apontar qual usar."""
    monkeypatch.setattr(localai, "RUNTIMES", isolado / "runtimes")  # nunca a pasta real do app
    monkeypatch.setattr(localai, "find_exe", FIND_EXE_REAL)
    monkeypatch.setattr(localai, "runtime_version", lambda exe: "b1")
    for backend in ("vulkan", "cpu"):
        pasta = localai.runtime_dir("llama", backend)
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / ("llama-server.exe" if localai.native.WINDOWS else "llama-server")).write_bytes(b"x")

    localai.set_runtime("llama", "cpu")
    assert localai.find_exe("llama").parent.name == "cpu"
    assert localai.runtimes()["llama"]["chosen"] == "cpu"

    localai.set_runtime("llama", "vulkan")
    assert localai.find_exe("llama").parent.name == "vulkan"

    with pytest.raises(Exception):
        localai.set_runtime("llama", "cuda")  # não está instalado


def test_gpu_desligada_entra_no_comando(isolado, monkeypatch):
    monkeypatch.setattr(localai, "hardware", lambda: {
        "gpus": [{"id": "Vulkan0", "enabled": True}, {"id": "Vulkan1", "enabled": False}],
        "vram": 0, "vram_free": 0, "ram": 0, "ram_free": 0})
    conhecidas = frozenset(["-c", "-ngl", "--device", "--fit"])

    a = localai.argv(Path("llama-server"), "m.gguf", localai.DEFAULT_PARAMS, conhecidas)

    assert a[a.index("--device") + 1] == "Vulkan0"
    assert ["-fit", "on"] == a[a.index("-fit"):a.index("-fit") + 2]


def test_protecoes_de_carregamento(isolado, monkeypatch):
    GB = 2 ** 30
    monkeypatch.setattr(localai, "hardware", lambda: {"vram": 12 * GB, "vram_free": 11 * GB,
                                                      "ram": 32 * GB, "ram_free": 16 * GB, "gpus": []})
    monkeypatch.setattr(localai, "estimate", lambda *_a: {"ok": True, "gpu": 14 * GB, "total": 20 * GB})

    localai.set_guardrail("rigoroso")
    with pytest.raises(Exception, match="rigorosa"):
        localai._checa_memoria("m.gguf", localai.DEFAULT_PARAMS)

    localai.set_guardrail("relaxado")
    localai._checa_memoria("m.gguf", localai.DEFAULT_PARAMS)  # cabe somando RAM: passa

    monkeypatch.setattr(localai, "estimate", lambda *_a: {"ok": True, "gpu": 14 * GB, "total": 90 * GB})
    with pytest.raises(Exception, match="no total"):
        localai._checa_memoria("m.gguf", localai.DEFAULT_PARAMS)

    localai.set_guardrail("off")
    localai._checa_memoria("m.gguf", localai.DEFAULT_PARAMS)  # desligado não olha nada


def test_ajustes_de_amostragem_por_nome(isolado):
    """A tela Inferência precisa funcionar para modelo do Ollama, que não tem arquivo local."""
    v = localai.inference_view("gemma4:31b")

    assert v["model"] == "gemma4:31b"
    assert v["inference"]["temperature"] == localai.INFERENCE_DEFAULTS["temperature"]
    assert v["inference_overrides"] == []


def test_seletor_lista_gguf_mesmo_sem_modelo_carregado(isolado, monkeypatch):
    """Contrato que o ModelPicker usa: /catalog reclama do llama-server fora do ar, mas /local
    continua listando os .gguf baixados — é de lá que sai a lista para carregar pelo seletor."""
    from fastapi.testclient import TestClient

    from app.main import app
    pasta = isolado / "modelos"
    gguf(pasta, "modelo-a.gguf")
    monkeypatch.setattr(localai, "kind_of", lambda f: "chat")

    async def sem_servidor(provider):   # o llama-server da máquina de quem roda o teste fica fora disto
        raise llm.LLMError("Não foi possível conectar em http://127.0.0.1:8077/v1: ConnectError. "
                           "Nenhum modelo carregado. Abra o painel IA local e carregue um .gguf.")

    monkeypatch.setattr(llm, "list_models", sem_servidor)

    with TestClient(app) as c:
        local = c.get("/api/local").json()
        assert [m["name"] for m in local["models"]] == ["modelo-a"]
        assert local["server"]["running"] is False and local["server"]["path"] == ""

        catalogo = {p["id"]: p for p in c.get("/api/catalog").json()}
        assert catalogo["local"]["type"] == "llamacpp"
        assert catalogo["local"]["models"] == [] and "Nenhum modelo carregado" in catalogo["local"]["error"]


def test_mmproj_vazio_nao_desliga_a_visao(tmp_path, monkeypatch):
    """Aconteceu em uso: um Qwen3.6 com o mmproj-*.gguf na mesma pasta subia com vision=False.

    O auto-detect do projetor chegou depois que a configuração já estava salva, e nela o campo
    estava gravado como "" — override vazio que vence o padrão e desliga a visão calado. Todo print
    que o agente tirasse ia para o lixo sem ninguém perceber.
    """
    modelo = tmp_path / "modelo.gguf"
    modelo.write_bytes(b"gguf")
    projetor = tmp_path / "mmproj-modelo-BF16.gguf"
    projetor.write_bytes(b"gguf")

    monkeypatch.setattr(localai, "defaults_for", lambda p: {"ctx": 4096, "mmproj": str(projetor)})
    monkeypatch.setattr(localai, "read_config",
                        lambda: {"models": {str(modelo): {"ctx": 8192, "mmproj": ""}}})

    ov = localai.overrides(str(modelo))
    assert ov == {"ctx": 8192}, "o mmproj vazio tem que ser descartado, o ctx não"
    assert localai.params(str(modelo))["mmproj"] == str(projetor)


def test_projetor_escolhe_o_formato_que_roda_melhor(tmp_path):
    """`sorted()` puro punha `mmproj-BF16.gguf` na frente de `mmproj-F16.gguf` — B vem antes de F —
    e o BF16 é exatamente o que o Vulkan não sabe rodar: cai na CPU e uma imagem de ~1700 tokens
    passava de 6 minutos, com o texto indo a 265 tokens/s no mesmo servidor.
    """
    (tmp_path / "modelo.gguf").write_bytes(b"gguf")
    for nome in ("mmproj-BF16.gguf", "mmproj-F32.gguf", "mmproj-F16.gguf"):
        (tmp_path / nome).write_bytes(b"gguf")

    assert Path(localai.projector_for(str(tmp_path / "modelo.gguf"))).name == "mmproj-F16.gguf"

    # Só o BF16 disponível: usa ele mesmo, que é melhor do que ficar sem visão.
    (tmp_path / "mmproj-F16.gguf").unlink()
    (tmp_path / "mmproj-F32.gguf").unlink()
    assert Path(localai.projector_for(str(tmp_path / "modelo.gguf"))).name == "mmproj-BF16.gguf"

    # Sem projetor nenhum, sem visão — e sem inventar caminho.
    (tmp_path / "mmproj-BF16.gguf").unlink()
    assert localai.projector_for(str(tmp_path / "modelo.gguf")) == ""


@pytest.mark.parametrize("mmproj,exe,avisa", [
    ("mmproj-BF16.gguf", r"C:\runtimes\llama\vulkan\llama-server.exe", True),
    ("mmproj-F16.gguf", r"C:\runtimes\llama\vulkan\llama-server.exe", False),   # F16 é o caminho nativo
    ("mmproj-BF16.gguf", r"C:\runtimes\llama\cuda\llama-server.exe", False),    # CUDA roda BF16
    ("", r"C:\runtimes\llama\vulkan\llama-server.exe", False),                  # sem visão, sem aviso
])
def test_avisa_projetor_incompativel_com_o_runtime(mmproj, exe, avisa):
    """Vulkan não tem caminho nativo para BF16 e o encoder de visão cai na CPU: medido em uso, o
    texto ia a 265 tokens/s no mesmo servidor e uma imagem de ~1700 tokens passava de 6 minutos.

    O arquivo certo pesa o mesmo e está no mesmo repositório, então o aviso vale a pena.
    """
    msg = localai.visao_lenta(mmproj, exe)
    assert bool(msg) is avisa
    if avisa:
        assert "mmproj-F16" in msg and "CUDA" in msg


def test_visao_por_modelo_pelo_mmproj_da_pasta(isolado, monkeypatch):
    """Visão é do modelo, não do servidor: o que tem mmproj na pasta enxerga mesmo descarregado."""
    monkeypatch.setattr(localai, "kind_of", lambda f: "chat")
    for nome, com_projetor in (("Qwen3.6", True), ("Coder", False)):
        pasta = isolado / "modelos" / nome
        pasta.mkdir()
        gguf(pasta, f"{nome}-Q4_K_M.gguf")
        if com_projetor:
            gguf(pasta, "mmproj-F16.gguf")
    visao = {m["name"]: m["vision"] for m in localai.state()["models"]}
    assert visao == {"Qwen3.6-Q4_K_M": True, "Coder-Q4_K_M": False}
    assert localai.visao_do_alias("Qwen3.6-Q4_K_M") is True
    assert localai.visao_do_alias("Coder-Q4_K_M") is False
    assert localai.visao_do_alias("nao-existe") is None


def test_vram_livre_vem_do_sistema_e_a_troca_conta_o_modelo_atual(isolado, monkeypatch):
    """O Vulkan não enxerga a VRAM de outros processos: a livre vem do sistema. E a proteção rigorosa
    não pode recusar uma troca por causa do modelo que a própria troca vai descarregar."""
    from app import native
    monkeypatch.setattr(localai, "_devices", lambda exe, janela: [{"id": "Vulkan0", "name": "GPU", "total": 12 << 30, "free": 12 << 30}])
    monkeypatch.setattr(native, "vram_em_uso", lambda: {"GPU": 9 << 30})
    assert localai.devices("llama")[0]["free"] == 3 << 30

    monkeypatch.setattr(localai, "guardrail", lambda: "rigoroso")
    monkeypatch.setattr(localai, "hardware", lambda: {"vram": 12 << 30, "vram_free": 3 << 30, "ram": 32 << 30})
    monkeypatch.setattr(localai, "estimate", lambda path, p: {"ok": True, "gpu": 8 << 30, "total": 9 << 30})
    monkeypatch.setattr(localai, "status", lambda: {"path": "atual.gguf"})
    localai._checa_memoria("novo.gguf", {})  # 3 livres + 8 do atual >= 8: passa
    monkeypatch.setattr(localai, "status", lambda: {"path": ""})
    with pytest.raises(Exception, match="rigorosa"):
        localai._checa_memoria("novo.gguf", {})


def test_argv_uma_previsao_simultanea_vai_explicita():
    """Omitido, o llama-server abria 4 slots e o "1" das Configurações não valia."""
    a = localai.argv(Path("llama-server.exe"), "C:/m/m.gguf", {**localai.DEFAULT_PARAMS, "parallel": 1})
    assert a[a.index("-np") + 1] == "1"


def test_kv_unificado_cada_requisicao_ve_a_janela_toda():
    assert localai.ctx_por_requisicao(131072, {"parallel": 4, "kv_unified": True}) == 131072
    assert localai.ctx_por_requisicao(131072, {"parallel": 4}) == 32768


def test_te_na_cpu_escolhe_a_gpu_dedicada():
    # Saída real do sd-cli num Ryzen 7600X + Arc B580: o dispositivo 0 é a integrada.
    listagem = ("ggml_vulkan: Found 2 Vulkan devices:\n"
                "ggml_vulkan: 0 = AMD Radeon(TM) Graphics (AMD proprietary driver) | uma: 1 | fp16: 1\n"
                "ggml_vulkan: 1 = Intel(R) Arc(TM) B580 Graphics (Intel Corporation) | uma: 0 | fp16: 1\n"
                "Vulkan0\tAMD Radeon(TM) Graphics\nVulkan1\tIntel(R) Arc(TM) B580 Graphics\nCPU\tAMD Ryzen 5 7600X\n")
    assert imagegen.escolhe_gpu(listagem) == "vulkan1"
    assert imagegen.escolhe_gpu("CUDA0\tNVIDIA GeForce RTX 4070\nCPU\tx\n") == "cuda0"
    assert imagegen.escolhe_gpu("CPU\tx\n") == "cpu"

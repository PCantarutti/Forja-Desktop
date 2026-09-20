"""IA local: linha de comando do llama-server, varredura de modelos, Hugging Face e sd.exe."""
from pathlib import Path

import pytest

from app import config, db, imagegen, llm, localai


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

def test_argv_do_sd(isolado):
    localai.set_image({"model": "C:/m/sd15.safetensors", "steps": 25, "width": 768})
    o = imagegen._opts({"seed": 7, "negative": "blurry"})
    a = imagegen.argv(Path("sd.exe"), "um gato", isolado / "out.png", o)

    assert a[:3] == ["sd.exe", "-p", "um gato"]  # sem -M: o padrão do sd.cpp já é gerar imagem
    assert a[a.index("-m") + 1] == "C:/m/sd15.safetensors"
    assert a[a.index("--steps") + 1] == "25" and a[a.index("-W") + 1] == "768"
    assert a[a.index("-n") + 1] == "blurry" and a[a.index("-s") + 1] == "7"
    # semente 0 na UI = aleatória (o padrão do sd.cpp é 42, que repetiria a mesma imagem)
    assert imagegen.argv(Path("sd.exe"), "x", isolado / "o.png", imagegen._opts())[-1] == "-1"


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
           ("h.context_length", 4, _u32(8192)), ("h.full_attention_interval", 4, _u32(2))]
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

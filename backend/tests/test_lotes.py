import os
import time
from pathlib import Path

import pytest

from app import config, db, downloads, imagegen, localai, lotes, mirror, slots


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    """Tudo em tmp: o config do localai, a pasta das imagens e o espelho em Markdown."""
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.json")
    monkeypatch.setattr(imagegen, "OUT_DIR", tmp_path / "imagens")
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(lotes.projeto, "gpu_alheia", lambda pid: [])  # a GPU de verdade desta máquina não entra
    localai.write_config({**localai._blank(), "image": {**localai.DEFAULT_IMAGE,
                                                        "out_dir": str(tmp_path / "imagens")}})
    (tmp_path / "imagens").mkdir()
    yield


def _conversa(kind="imagem") -> int:
    with db.session() as s:
        c = db.Conversation(kind=kind)
        s.add(c)
        s.commit()
        return c.id


def _fake_sd(monkeypatch, falhar=()):
    """Troca o sd-cli por um PNG de mentira; guarda o que cada chamada recebeu."""
    chamadas: list[dict] = []

    def generate(prompt, out, opts=None, job_id="", refs=(), progresso=None, previa=None):
        o = dict(opts or {})
        chamadas.append({"prompt": prompt, "out": Path(out), "refs": list(refs), **o})
        if o.get("model") in falhar:
            raise imagegen.ToolError("sd falhou (código 1)")
        if progresso:
            progresso(10, 20, 3.5)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"\x89PNG")
        return Path(out)

    monkeypatch.setattr(imagegen, "generate", generate)
    monkeypatch.setattr(imagegen, "_exe", lambda: Path("sd-cli.exe"))
    monkeypatch.setattr(imagegen, "argv", lambda *a, **k: ["sd-cli"])
    monkeypatch.setattr(localai, "status", lambda: {"running": False})
    return chamadas


def _esperar(message_id, timeout=5.0):
    fim = time.time() + timeout
    while time.time() < fim:
        m = lotes._mensagem(message_id)
        if m["status"] != "running":
            return m
        time.sleep(0.02)
    raise AssertionError("o lote não terminou")


# ------------------------------------------------------------------ puros

def test_sementes_incremental():
    assert lotes._sementes(4, 1000, "incremental") == [1000, 1001, 1002, 1003]


def test_sementes_fixa_e_aleatoria():
    assert lotes._sementes(3, 7, "fixa") == [7, 7, 7]
    s = lotes._sementes(5, 0, "aleatoria")
    assert len(s) == 5 and all(1 <= x <= lotes.SEED_MAX for x in s)


def test_semente_zero_sorteia_a_base():
    s = lotes._sementes(3, 0, "incremental")
    assert s[0] >= 1 and s == [s[0], s[0] + 1, s[0] + 2]


@pytest.mark.parametrize("models,count,esperado", [
    (["a"], 3, ["a", "a", "a"]),
    (["a", "b"], 10, ["a"] * 5 + ["b"] * 5),
    (["a", "b", "c"], 10, ["a"] * 4 + ["b"] * 3 + ["c"] * 3),
])
def test_distribuir(models, count, esperado):
    assert lotes._distribuir(models, count) == esperado


def test_distribuir_sem_modelo():
    with pytest.raises(lotes.ToolError, match="pelo menos um modelo"):
        lotes._distribuir([], 4)


# ------------------------------------------------------------------ lote

def test_lote_divide_entre_modelos(monkeypatch):
    chamadas = _fake_sd(monkeypatch)
    conv = _conversa()
    msg = lotes.start(conv, "a fox", models=["m1.safetensors", "m2.safetensors"], count=6,
                      seed=1000, seed_mode="incremental")
    pronto = _esperar(msg["id"])

    assert pronto["status"] == "pronto"
    imagens = pronto["meta"]["images"]
    assert [i["status"] for i in imagens] == ["pronta"] * 6
    assert all(i["progress"] == 0.5 for i in imagens)  # o card enche com o passo do sd-cli
    assert all(i["s_passo"] == 3.5 and i["restante"] == 35 for i in imagens)  # 10 passos x 3,5 s
    assert [i["model"] for i in imagens] == ["m1.safetensors"] * 3 + ["m2.safetensors"] * 3
    assert [i["seed"] for i in imagens] == [1000, 1001, 1002, 1003, 1004, 1005]
    assert all(Path(i["path"]).exists() for i in imagens)
    # cada chamada recebeu o seu próprio modelo e a sua própria semente
    assert [c["model"] for c in chamadas] == [i["model"] for i in imagens]
    assert [c["seed"] for c in chamadas] == [i["seed"] for i in imagens]


def test_lote_de_edicao_leva_as_referencias_em_cada_imagem(monkeypatch):
    chamadas = _fake_sd(monkeypatch)
    msg = lotes.start(_conversa(), "make it night", models=["q.gguf"], count=2, refs=["C:/r/a.png"])
    _esperar(msg["id"])
    assert [c["refs"] for c in chamadas] == [["C:/r/a.png"]] * 2
    with db.session() as s:
        pedido = s.get(db.Message, msg["id"] - 1)
        assert pedido.meta["refs"] == ["C:/r/a.png"]  # "Reaproveitar" traz a edição de volta


def test_lote_grava_as_duas_mensagens_e_titula(monkeypatch):
    _fake_sd(monkeypatch)
    conv = _conversa()
    msg = lotes.start(conv, "a fox in the snow", models=["m1.safetensors"], count=2)
    _esperar(msg["id"])
    with db.session() as s:
        c = s.get(db.Conversation, conv)
        assert c.title == "a fox in the snow"
        papeis = [m.role for m in c.messages]
    assert papeis == ["user", "assistant"]


def test_lote_com_modelo_que_falha(monkeypatch):
    _fake_sd(monkeypatch, falhar={"ruim.safetensors"})
    conv = _conversa()
    msg = lotes.start(conv, "a fox", models=["m1.safetensors", "ruim.safetensors"], count=2)
    pronto = _esperar(msg["id"])
    estados = {i["model"]: i["status"] for i in pronto["meta"]["images"]}
    assert estados == {"m1.safetensors": "pronta", "ruim.safetensors": "erro"}
    assert pronto["status"] == "pronto"  # uma deu certo: o lote não é um fracasso


def test_prompt_vazio(monkeypatch):
    _fake_sd(monkeypatch)
    with pytest.raises(lotes.ToolError, match="prompt vazio"):
        lotes.start(_conversa(), "   ", models=["m1.safetensors"], count=2)


def test_lote_com_llm_carregado_pede_confirmacao(monkeypatch):
    _fake_sd(monkeypatch)
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "alias": "qwen"})
    with pytest.raises(imagegen.ModeloCarregado):
        lotes.start(_conversa(), "a fox", models=["m1.safetensors"], count=1)


def test_cancelar_marca_as_restantes(monkeypatch):
    conv = _conversa()

    def generate(prompt, out, opts=None, job_id="", refs=(), progresso=None, previa=None):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"\x89PNG")
        downloads.cancel(job_id)  # cancela logo na primeira, como o botão faria
        return Path(out)

    monkeypatch.setattr(imagegen, "generate", generate)
    monkeypatch.setattr(imagegen, "_exe", lambda: Path("sd-cli.exe"))
    monkeypatch.setattr(imagegen, "argv", lambda *a, **k: ["sd-cli"])
    monkeypatch.setattr(localai, "status", lambda: {"running": False})

    msg = lotes.start(conv, "a fox", models=["m1.safetensors"], count=4)
    pronto = _esperar(msg["id"])
    estados = [i["status"] for i in pronto["meta"]["images"]]
    assert estados == ["pronta", "cancelada", "cancelada", "cancelada"]
    assert not localai.image_busy()


# ------------------------------------------------------------------ aprovação

def test_decidir_move_as_reprovadas(monkeypatch):
    _fake_sd(monkeypatch)
    conv = _conversa()
    msg = lotes.start(conv, "a fox", models=["m1.safetensors"], count=3)
    pronto = _esperar(msg["id"])
    imagens = pronto["meta"]["images"]
    manter = [imagens[0]["path"]]

    out = lotes.decidir(msg["id"], manter)
    depois = out["meta"]["images"]

    assert depois[0]["status"] == "mantida" and Path(depois[0]["path"]).exists()
    assert depois[0]["path"] == manter[0]  # a aprovada não se mexe
    for item in depois[1:]:
        assert item["status"] == "descartada"
        assert Path(item["path"]).parent == lotes.descartadas_dir()
        assert Path(item["path"]).exists()  # movida, não apagada
    assert not Path(imagens[1]["path"]).exists()


def test_decidir_desfaz_o_descarte(monkeypatch):
    _fake_sd(monkeypatch)
    conv = _conversa()
    msg = lotes.start(conv, "a fox", models=["m1.safetensors"], count=2)
    pronto = _esperar(msg["id"])
    originais = [i["path"] for i in pronto["meta"]["images"]]

    agora = lotes.decidir(msg["id"], [originais[0]])["meta"]["images"]
    # desfazer é aprovar pelo caminho atual: depois do descarte, ele aponta para descartadas/
    voltou = lotes.decidir(msg["id"], [i["path"] for i in agora])["meta"]["images"]

    assert [i["status"] for i in voltou] == ["mantida", "mantida"]
    assert [i["path"] for i in voltou] == originais
    assert all(Path(p).exists() for p in originais)


def test_expurgo_por_idade(monkeypatch):
    _fake_sd(monkeypatch)
    conv = _conversa()
    msg = lotes.start(conv, "a fox", models=["m1.safetensors"], count=2)
    pronto = _esperar(msg["id"])
    lotes.decidir(msg["id"], [])  # descarta as duas
    velha, nova = sorted(lotes.descartadas_dir().glob("*.png"))
    os.utime(velha, (time.time() - 9 * 86400,) * 2)

    assert lotes.limpar_descartadas(7) == 1
    assert not velha.exists() and nova.exists()


def test_expurgo_zero_dias_no_config_guarda_para_sempre(monkeypatch):
    _fake_sd(monkeypatch)
    localai.set_image({"descarte_dias": 0})
    conv = _conversa()
    msg = lotes.start(conv, "a fox", models=["m1.safetensors"], count=1)
    _esperar(msg["id"])
    lotes.decidir(msg["id"], [])
    velha = next(lotes.descartadas_dir().glob("*.png"))
    os.utime(velha, (time.time() - 999 * 86400,) * 2)

    assert lotes.limpar_descartadas() == 0 and velha.exists()
    assert lotes.limpar_descartadas(0) == 1 and not velha.exists()  # "Esvaziar agora" leva tudo


# ------------------------------------------------------------------ API

def test_api_aceita_conversa_de_imagem():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        r = c.post("/api/conversations", json={"kind": "imagem"})
        assert r.status_code == 200 and r.json()["kind"] == "imagem"
        assert c.post("/api/conversations", json={"kind": "outro"}).status_code == 400


def test_queda_no_meio_vira_interrompido_e_continuar_gera_o_que_faltou(monkeypatch):
    """App fechou com o lote rodando: na subida ele não pode ficar "gerando" para sempre."""
    chamadas = _fake_sd(monkeypatch)
    conv = _conversa()
    msg = lotes.start(conv, "a fox", models=["m1.safetensors"], count=3, seed=7)
    _esperar(msg["id"])
    # simula a queda: a 1ª saiu, a 2ª estava no meio (sem PNG), a 3ª nem começou
    imagens = lotes._mensagem(msg["id"])["meta"]["images"]
    Path(imagens[1]["path"]).unlink()
    Path(imagens[2]["path"]).unlink()
    imagens[1].update(status="gerando", progress=0.18)
    imagens[2]["status"] = "pendente"
    lotes._patch(msg["id"], status="running", meta={"images": imagens})

    assert lotes.reap() == 1
    m = lotes._mensagem(msg["id"])
    assert m["status"] == "interrompido"
    assert [i["status"] for i in m["meta"]["images"]] == ["pronta", "interrompida", "interrompida"]

    chamadas.clear()
    lotes.continuar(msg["id"])
    m = _esperar(msg["id"])
    assert m["status"] == "pronto"
    assert [i["status"] for i in m["meta"]["images"]] == ["pronta"] * 3
    # só as que faltaram, com as mesmas sementes: sai a mesma imagem que teria saído
    assert [c["seed"] for c in chamadas] == [imagens[1]["seed"], imagens[2]["seed"]]
    assert [c["prompt"] for c in chamadas] == ["a fox", "a fox"]
    with pytest.raises(lotes.ToolError):
        lotes.continuar(msg["id"])  # nada mais a continuar


def test_previa_entra_no_card_e_some_no_fim(monkeypatch):
    """A prévia só aparece depois que o sd-cli grava a primeira, e o arquivo some quando a imagem sai."""
    _fake_sd(monkeypatch)
    vistas: list = []

    def generate(prompt, out, opts=None, job_id="", refs=(), progresso=None, previa=None):
        progresso(1, 4, 1.0)  # antes da primeira prévia: nada no card, mas ele já sabe que vem prévia
        vistas.append(lotes._mensagem(msg_id[0])["meta"]["images"][0].get("preview"))
        assert lotes._mensagem(msg_id[0])["meta"]["images"][0]["com_previa"] is True
        Path(previa).write_bytes(b"\x89PNG")
        progresso(2, 4, 1.0)
        vistas.append(lotes._mensagem(msg_id[0])["meta"]["images"][0].get("preview"))
        Path(out).write_bytes(b"\x89PNG")
        return Path(out)

    monkeypatch.setattr(imagegen, "generate", generate)
    msg_id: list = []
    orig = lotes.threading.Thread
    # segura a thread até o id da mensagem estar à mão (o generate de mentira lê a mensagem)
    monkeypatch.setattr(lotes.threading, "Thread", lambda target, args, daemon: orig(
        target=lambda *a: (msg_id.append(a[1]), target(*a)), args=args, daemon=daemon))
    m = _esperar(lotes.start(_conversa(), "a fox", models=["m1.safetensors"])["id"])
    assert vistas[0] is None and vistas[1].endswith(".png")
    assert "preview" not in m["meta"]["images"][0] and "com_previa" not in m["meta"]["images"][0]
    assert not Path(vistas[1]).exists()


def test_referencia_do_disco_fica_no_lugar_e_so_ela_e_servida(tmp_path):
    """Anexar do disco guarda o caminho (nada copiado); a rota serve essa imagem, e não outra qualquer."""
    from fastapi.testclient import TestClient

    from app.main import app
    fora = tmp_path / "fotos"
    fora.mkdir()
    foto, outra = fora / "gato.png", fora / "segredo.png"
    foto.write_bytes(b"png1")
    outra.write_bytes(b"png2")
    with TestClient(app) as c:
        assert c.get("/api/local/image/file", params={"path": str(foto)}).status_code == 404
        r = c.post("/api/imagens/referencia/caminho", json={"path": str(foto)})
        assert r.status_code == 200 and r.json()["path"] == str(foto)
        assert not (imagegen.out_dir() / "referencias").exists()  # nenhuma cópia
        assert c.get("/api/local/image/file", params={"path": str(foto)}).content == b"png1"
        assert c.get("/api/local/image/file", params={"path": str(outra)}).status_code == 404
        assert c.post("/api/imagens/referencia/caminho", json={"path": str(fora / "sumiu.png")}).status_code == 400
        assert c.post("/api/imagens/referencia/caminho", json={"path": str(fora / "nota.txt")}).status_code == 400


def test_mesma_imagem_colada_duas_vezes_nao_duplica():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        a = c.post("/api/imagens/referencia", files={"file": ("ref.png", b"mesma", "image/png")}).json()["path"]
        b = c.post("/api/imagens/referencia", files={"file": ("ref.png", b"mesma", "image/png")}).json()["path"]
    assert a == b and len(list((imagegen.out_dir() / "referencias").iterdir())) == 1


def test_referencia_sumida_pede_para_reanexar(monkeypatch, tmp_path):
    monkeypatch.setattr(localai, "gguf_info", lambda p: {"arch": "qwen_image21"})
    with pytest.raises(imagegen.ToolError, match="anexe de novo"):
        imagegen._confere_arquivos("C:/m/qwen.gguf", {}, [str(tmp_path / "movida.png")])


def test_apagar_conversa_leva_as_imagens_dela_e_so_elas(monkeypatch, tmp_path):
    """As geradas (inclusive a descartada) saem; a referência do disco e as de outra conversa ficam."""
    from fastapi.testclient import TestClient

    from app.main import app
    _fake_sd(monkeypatch)
    ref = tmp_path / "minha-foto.png"
    ref.write_bytes(b"png")
    conv, outra = _conversa(), _conversa()
    m = _esperar(lotes.start(conv, "a fox", models=["m1.safetensors"], count=2)["id"])
    lotes._save(conv, role="user", content="edita", meta={"refs": [str(ref)]})
    fica = _esperar(lotes.start(outra, "a cat", models=["m1.safetensors"], count=1)["id"])
    lotes.decidir(m["id"], keep=[m["meta"]["images"][0]["path"]])  # a 2ª vai para descartadas/
    geradas = lotes.imagens_da_conversa(conv)
    assert len(geradas) == 2 and any(lotes.DESCARTADAS in str(f) for f in geradas)
    with TestClient(app) as c:
        assert c.get(f"/api/imagens/{conv}/arquivos").json()["count"] == 2
        assert c.delete(f"/api/conversations/{conv}").status_code == 200
    assert not any(f.exists() for f in geradas)
    assert ref.exists() and Path(fica["meta"]["images"][0]["path"]).exists()


# ------------------------------------------------------------------ slots (skill gerar-imagens)

def test_slots_viram_fila_no_caminho_do_projeto(tmp_path, monkeypatch):
    chamadas = _fake_sd(monkeypatch)
    projeto = tmp_path / "site"
    projeto.mkdir()
    r = imagegen.imagens_pendentes(projeto, {"estilo": "warm light", "slots": [
        {"nome": "vela-3141", "caminho": "img/vela-3141.png", "prompt": "soy candle", "largura": 1000, "altura": 1000},
        {"nome": "hero-8027", "caminho": "img/hero-8027.png", "prompt": "candles on a table"}]})
    with pytest.raises(imagegen.ToolError, match="fora da pasta"):
        imagegen.imagens_pendentes(projeto, {"slots": [{"nome": "x-1", "caminho": "../x.png", "prompt": "p"}]})
    with pytest.raises(imagegen.ToolError, match="repetido"):
        imagegen.imagens_pendentes(projeto, {"slots": [{"nome": "x-1", "caminho": "a.png", "prompt": "p"}] * 2})

    conv = _conversa("agent")
    with db.session() as s:
        m = db.Message(conversation_id=conv, role="tool", content=r["text"],
                       meta={"imagens_pendentes": r["imagens_pendentes"]})
        s.add(m)
        s.commit()
        tool_id = m.id
    # até gerar, o caminho tem um PNG provisório com o nome do slot (e ele não vai para descartadas/)
    assert slots.eh_placeholder(projeto / "img" / "hero-8027.png")
    (projeto / "img" / "vela-3141.png").write_bytes(b"velha")  # versão anterior: vai para descartadas/

    with pytest.raises(lotes.ToolError, match="botão"):  # slots só saem na conversa aberta pelo botão
        lotes.start(_conversa(), "", {}, ["m.gguf"], slots_de=tool_id)
    img_conv = lotes.conversa_dos_slots(tool_id)["id"]
    assert lotes.conversa_dos_slots(tool_id)["id"] == img_conv  # clicar de novo volta para a mesma
    info = lotes.origem(img_conv)
    assert info["chat"]["id"] == conv and info["projeto"] == "pasta padrão" and len(info["slots"]) == 2
    with pytest.raises(lotes.ToolError, match="só para as imagens"):  # nada de imagem avulsa aqui
        lotes.start(img_conv, "a fox", {}, ["m.gguf"])
    msg = lotes.start(img_conv, "", {}, ["m.gguf"], slots_de=tool_id)
    feito = _esperar(msg["id"])
    with db.session() as s:  # o botão do chat vira "Ver as N imagens"
        assert s.get(db.Message, tool_id).meta["imagens_pendentes"]["geradas"] is True
    # gera ao lado (.nome.gerando.png) e só então troca o arquivo do site
    assert [c["out"] for c in chamadas] == [projeto / "img" / ".vela-3141.gerando.png", projeto / "img" / ".hero-8027.gerando.png"]
    assert not list((projeto / "img").glob(".*.gerando.png"))
    assert chamadas[0]["prompt"] == "soy candle, warm light" and chamadas[0]["width"] == 1024
    assert "width" not in chamadas[1]  # sem tamanho: o do painel
    assert (projeto / "img" / "vela-3141.png").read_bytes() == b"\x89PNG"
    assert list(lotes.descartadas_dir().glob("vela-3141-*.png"))
    assert lotes.eh_slot(str(projeto / "img" / "hero-8027.png"))  # a rota de arquivo serve o slot

    # descartar e desfazer: o slot volta para o caminho que o código aponta
    lotes.decidir(feito["id"], [])
    assert not (projeto / "img" / "hero-8027.png").exists()
    descartado = next(i["path"] for i in lotes._mensagem(feito["id"])["meta"]["images"] if i["nome"] == "hero-8027")
    lotes.decidir(feito["id"], [descartado])
    assert (projeto / "img" / "hero-8027.png").exists()


def test_regerar_slot_e_escolher_variacao(tmp_path, monkeypatch):
    chamadas = _fake_sd(monkeypatch)
    projeto = tmp_path / "site"
    projeto.mkdir()
    r = imagegen.imagens_pendentes(projeto, {"slots": [
        {"nome": "vela-3141", "caminho": "img/vela-3141.png", "prompt": "soy candle", "largura": 768, "altura": 512}]})
    conv_agente = _conversa("agent")
    with db.session() as s:
        m = db.Message(conversation_id=conv_agente, role="tool", content="", meta={"imagens_pendentes": r["imagens_pendentes"]})
        s.add(m)
        s.commit()
        tool_id = m.id
    conv = lotes.conversa_dos_slots(tool_id)["id"]
    slot = str(projeto / "img" / "vela-3141.png")
    original = _esperar(lotes.start(conv, "", {}, ["m.gguf"], slots_de=tool_id)["id"])
    Path(slot).write_bytes(b"original")

    avulsa = _conversa()
    comum = _esperar(lotes.start(avulsa, "gato", {}, ["m.gguf"])["id"])
    with pytest.raises(lotes.ToolError, match="slot"):  # variação comum não tem slot para refazer
        lotes.start(avulsa, "", {}, ["m.gguf"], count=2, variar={"message_id": comum["id"], "path": comum["meta"]["images"][0]["path"]})
    with pytest.raises(lotes.ToolError, match="outra conversa"):
        lotes.start(avulsa, "", {}, ["m.gguf"], variar={"message_id": original["id"], "path": slot})

    var = _esperar(lotes.start(conv, "", {}, ["m.gguf"], count=3, variar={"message_id": original["id"], "path": slot})["id"])
    imgs = var["meta"]["images"]
    assert var["meta"]["variacao_de"] == slot  # a tela mostra no modal do slot, não como lote novo
    assert len(imgs) == 3 and all(i["slot"] == slot and "destino" not in i for i in imgs)
    assert all(c["prompt"] == "soy candle" and c["width"] == 768 for c in chamadas[-3:])
    assert Path(slot).read_bytes() == b"original"  # gerar variações não mexe no site

    escolhida = imgs[1]["path"]
    Path(escolhida).write_bytes(b"nova")
    lotes.escolher(conv, slot, escolhida)
    assert Path(slot).read_bytes() == b"nova"
    velha = lotes._mensagem(original["id"])["meta"]["images"][0]
    assert velha["slot"] == slot and "destino" not in velha and Path(velha["path"]).read_bytes() == b"original"
    nova = lotes._mensagem(var["id"])["meta"]["images"][1]
    assert nova["destino"] == slot and nova["path"] == slot and nova["status"] == "mantida"

    lotes.escolher(conv, slot, velha["path"])  # e dá para voltar atrás
    assert Path(slot).read_bytes() == b"original"

    # prompt editado no modal: as variações novas usam o texto novo; o site continua igual
    editado = _esperar(lotes.start(conv, "", {}, ["m.gguf"], count=1, variar={
        "message_id": original["id"], "path": slot, "prompt": "  beeswax candle  "})["id"])
    assert chamadas[-1]["prompt"] == "beeswax candle" and editado["meta"]["images"][0]["prompt"] == "beeswax candle"
    assert editado["meta"]["seed_mode"] == "aleatoria"  # regerar com a semente de antes repetiria a imagem
    assert Path(slot).read_bytes() == b"original"
    with pytest.raises(lotes.ToolError, match="não encontrada"):
        lotes.escolher(conv, slot, str(tmp_path / "qualquer.png"))


def test_slot_que_falha_nao_tira_a_imagem_do_site(tmp_path, monkeypatch):
    _fake_sd(monkeypatch, falhar={"sem-vram.gguf"})
    projeto = tmp_path / "site"
    (projeto / "img").mkdir(parents=True)
    no_site = projeto / "img" / "vela-3141.png"
    no_site.write_bytes(b"a que o site mostra")
    r = imagegen.imagens_pendentes(projeto, {"slots": [{"nome": "vela-3141", "caminho": "img/vela-3141.png", "prompt": "p"}]})
    with db.session() as s:
        m = db.Message(conversation_id=_conversa("agent"), role="tool", content="", meta={"imagens_pendentes": r["imagens_pendentes"]})
        s.add(m)
        s.commit()
        tool_id = m.id
    conv = lotes.conversa_dos_slots(tool_id)["id"]
    feito = _esperar(lotes.start(conv, "", {}, ["sem-vram.gguf"], slots_de=tool_id)["id"])
    assert feito["meta"]["images"][0]["status"] == "erro"
    assert no_site.read_bytes() == b"a que o site mostra"  # o sd falhou: o site fica como estava
    assert not list(lotes.descartadas_dir().glob("vela-3141-*.png"))

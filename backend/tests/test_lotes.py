import os
import time
from pathlib import Path

import pytest

from app import config, db, downloads, imagegen, localai, lotes, mirror


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    """Tudo em tmp: o config do localai, a pasta das imagens e o espelho em Markdown."""
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.json")
    monkeypatch.setattr(imagegen, "OUT_DIR", tmp_path / "imagens")
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
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

    def generate(prompt, out, opts=None, job_id="", refs=(), progresso=None):
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

    def generate(prompt, out, opts=None, job_id="", refs=(), progresso=None):
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

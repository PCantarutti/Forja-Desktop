"""Slots de imagem do site (skill gerar-imagens): conferência do código, placeholder, conversa por projeto,
aviso ao chat, regerar com outro estilo, versão web, tamanho nativo e GPU ocupada por outro programa."""
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from app import config, db, imagegen, localai, lotes, mirror, slots

GPU_ALHEIA = slots.gpu_alheia  # a real: a fixture troca a do módulo em todo teste


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.json")
    monkeypatch.setattr(imagegen, "OUT_DIR", tmp_path / "imagens")
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(lotes.projeto, "gpu_alheia", lambda pid: [])
    localai.write_config({**localai._blank(), "image": {**localai.DEFAULT_IMAGE, "out_dir": str(tmp_path / "imagens")}})
    (tmp_path / "imagens").mkdir()
    chamadas: list[dict] = []

    def generate(prompt, out, opts=None, job_id="", refs=(), progresso=None, previa=None, medir=None):
        chamadas.append({"prompt": prompt, "out": Path(out), **(opts or {})})
        Image.new("RGB", (64, 64), (200, 100, 50)).save(out)  # PNG de verdade: o .webp sai dele
        return Path(out)

    monkeypatch.setattr(imagegen, "generate", generate)
    monkeypatch.setattr(imagegen, "_exe", lambda: Path("sd-cli.exe"))
    monkeypatch.setattr(imagegen, "argv", lambda *a, **k: ["sd-cli"])
    monkeypatch.setattr(localai, "status", lambda: {"running": False})
    yield chamadas


def _esperar(message_id, timeout=5.0):
    fim = time.time() + timeout
    while time.time() < fim:
        m = lotes._mensagem(message_id)
        if m["status"] != "running":
            return m
        time.sleep(0.02)
    raise AssertionError("o lote não terminou")


def _chat(projeto: Path) -> int:
    with db.session() as s:
        c = db.Conversation(kind="agent", workspace=str(projeto))
        s.add(c)
        s.commit()
        return c.id


def _pedido(chat: int, projeto: Path, slots_: list[dict], estilo: str = "") -> tuple[int, str]:
    """Chama a ferramenta como o agente chamaria e grava o resultado como a mensagem da ferramenta."""
    r = imagegen.imagens_pendentes(projeto, {"estilo": estilo, "slots": slots_})
    with db.session() as s:
        m = db.Message(conversation_id=chat, role="tool", content=r["text"], meta={"imagens_pendentes": r["imagens_pendentes"]})
        s.add(m)
        s.commit()
        return m.id, r["text"]


def _site(tmp_path: Path, html: str) -> Path:
    projeto = tmp_path / "site"
    projeto.mkdir()
    (projeto / "index.html").write_text(html, encoding="utf-8")
    return projeto


def _eventos(chat: int) -> list[str]:
    with db.session() as s:
        return [m.content for m in s.query(db.Message).filter(db.Message.conversation_id == chat,
                                                                 db.Message.role == "event").all()
                if (m.meta or {}).get("kind") == "imagens" and m.meta.get("to_model")]


def test_ferramenta_confere_o_codigo_e_deixa_placeholder(tmp_path):
    projeto = _site(tmp_path, '<img src="img/vela-3141.png"><img src="img/errada.png"><img src="https://x.com/a.png">')
    _, texto = _pedido(_chat(projeto), projeto, [
        {"nome": "vela-3141", "caminho": "img/vela-3141.png", "prompt": "candle", "largura": 512, "altura": 384},
        {"nome": "hero-8027", "caminho": "img/hero-8027.png", "prompt": "hero"}])
    assert "hero-8027 (img/hero-8027.png) não aparece em nenhum arquivo" in texto
    assert "index.html usa img/errada.png, que não existe e não é slot" in texto
    assert "x.com" not in texto  # imagem externa não é problema
    ph = projeto / "img" / "vela-3141.png"
    assert slots.eh_placeholder(ph) and Image.open(ph).size == (512, 384)
    ph.write_bytes(b"a da pessoa")  # placeholder nunca vai por cima de arquivo que existe
    assert not slots.placeholder(str(ph), 512, 384, "vela-3141") and ph.read_bytes() == b"a da pessoa"


def test_projeto_tem_uma_conversa_e_so_os_slots_novos_entram_na_fila(tmp_path, pastas):
    projeto = _site(tmp_path, '<img src="img/a-1111.png"><img src="img/b-2222.png">')
    chat = _chat(projeto)
    p1, _ = _pedido(chat, projeto, [{"nome": "a-1111", "caminho": "img/a-1111.png", "prompt": "a"}])
    conv = lotes.conversa_dos_slots(p1)["id"]
    _esperar(lotes.start(conv, "", {}, ["m.gguf"], slots_de=p1)["id"])
    assert not list(lotes.descartadas_dir().glob("a-1111-*"))  # o placeholder só some, não é "versão"
    assert "a-1111 → a-1111.png: pronta" in _eventos(chat)[-1]  # a IA fica sabendo no próximo turno

    # outro pedido no mesmo projeto (outra seção do site): mesma conversa, só o slot novo na fila
    p2, _ = _pedido(chat, projeto, [{"nome": "b-2222", "caminho": "img/b-2222.png", "prompt": "b"}])
    assert lotes.conversa_dos_slots(p2)["id"] == conv
    info = lotes.origem(conv)
    assert [s["nome"] for s in info["pendentes"]] == ["b-2222"] and info["message_id"] == p2
    n = len(pastas)
    _esperar(lotes.start(conv, "", {}, ["m.gguf"], slots_de=p2)["id"])
    assert [c["out"].name for c in pastas[n:]] == [".b-2222.gerando.png"]
    with pytest.raises(lotes.ToolError, match="já foram gerados"):
        lotes.start(conv, "", {}, ["m.gguf"], slots_de=p2)

    # o código parou de usar um slot: a tela marca
    (projeto / "index.html").write_text('<img src="img/b-2222.png">', encoding="utf-8")
    assert lotes.origem(conv)["fora_do_codigo"] == [str((projeto / "img" / "a-1111.png").resolve())]


def test_regerar_todas_com_outro_estilo(tmp_path, pastas):
    projeto = _site(tmp_path, '<img src="img/a-1111.png"><img src="img/b-2222.png">')
    chat = _chat(projeto)
    p, _ = _pedido(chat, projeto, [{"nome": "a-1111", "caminho": "img/a-1111.png", "prompt": "a candle"},
                                   {"nome": "b-2222", "caminho": "img/b-2222.png", "prompt": "a jar"}], estilo="warm light")
    conv = lotes.conversa_dos_slots(p)["id"]
    _esperar(lotes.start(conv, "", {}, ["m.gguf"], slots_de=p)["id"])
    assert pastas[0]["prompt"] == "a candle, warm light"
    n = len(pastas)
    var = _esperar(lotes.start(conv, "", {}, ["m.gguf"], count=2, estilo="cold blue night")["id"])
    assert var["meta"]["variacao_de"] == "*" and len(var["meta"]["images"]) == 4
    assert sorted({c["prompt"] for c in pastas[n:]}) == ["a candle, cold blue night", "a jar, cold blue night"]
    assert all(i["slot"] and "destino" not in i for i in var["meta"]["images"])  # o site não muda sozinho
    with pytest.raises(lotes.ToolError, match="só para as imagens do site"):
        with db.session() as s:
            avulsa = db.Conversation(kind="imagem")
            s.add(avulsa)
            s.commit()
            aid = avulsa.id
        lotes.start(aid, "", {}, ["m.gguf"], estilo="x")


def test_otimizar_troca_o_codigo_para_webp_e_mantem_em_dia(tmp_path):
    projeto = _site(tmp_path, '<img src="img/a-1111.png"> <div style="background:url(img/a-1111.png)"></div> img/xa-1111.png')
    chat = _chat(projeto)
    p, _ = _pedido(chat, projeto, [{"nome": "a-1111", "caminho": "img/a-1111.png", "prompt": "a"}])
    conv = lotes.conversa_dos_slots(p)["id"]
    original = _esperar(lotes.start(conv, "", {}, ["m.gguf"], slots_de=p)["id"])
    r = lotes.otimizar(conv)
    html = (projeto / "index.html").read_text(encoding="utf-8")
    assert html.count("img/a-1111.webp") == 2 and "img/xa-1111.png" in html  # só o nome exato
    assert r["arquivos"] == ["index.html"] and (projeto / "img" / "a-1111.webp").is_file()
    assert lotes.origem(conv)["web"] and lotes.origem(conv)["fora_do_codigo"] == []  # .webp conta como uso
    assert "webp" in _eventos(chat)[-1]

    # regerar e usar outra no site: o .webp acompanha a troca
    slot = str((projeto / "img" / "a-1111.png").resolve())
    var = _esperar(lotes.start(conv, "", {}, ["m.gguf"], count=1, variar={"message_id": original["id"], "path": slot})["id"])
    Image.new("RGB", (64, 64), (0, 0, 255)).save(var["meta"]["images"][0]["path"])
    lotes.escolher(conv, slot, var["meta"]["images"][0]["path"])
    assert Image.open(projeto / "img" / "a-1111.webp").convert("RGB").getpixel((5, 5))[2] > 200
    assert "trocada no site" in _eventos(chat)[-1]


def test_tamanho_nativo_do_modelo():
    assert slots.area_nativa("D:/m/qwen.gguf", {"width": 1024, "height": 1024}) == 1024 * 1024
    assert slots.area_nativa("D:/m/Juggernaut-XL_v9.safetensors", None) == 1024 * 1024
    assert slots.area_nativa("D:/m/v1-5-pruned.safetensors", None) == 512 * 512
    assert slots.area_nativa("D:/m/qualquer.safetensors", None) is None
    assert slots.ajustar(1344, 768, 1024 * 1024) == (1344, 768)       # já está na área: fica
    assert slots.ajustar(1344, 768, 512 * 512) == (704, 384)          # mesma proporção, área do SD 1.5
    assert slots.ajustar(None, None, 1024 * 1024) == (None, None)      # sem tamanho no slot: o do painel


def test_gpu_de_outro_programa(monkeypatch):
    lista = {"llama-server.exe": '"llama-server.exe","111","Console","1","1.000 K"\r\n"llama-server.exe","222","Console","1","9 K"\r\n',
             "sd-cli.exe": '"sd-cli.exe","333","Console","1","5 K"\r\n'}
    monkeypatch.setattr(slots.sys, "platform", "win32")
    monkeypatch.setattr(slots.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(
        cmd, 0, stdout=lista[cmd[2].split()[-1]]))
    # o llama-server deste Forja não conta; sd-cli de outra instância conta
    assert GPU_ALHEIA(111) == ["llama-server (PID 222)", "sd-cli gerando imagem (PID 333)"]
    monkeypatch.setattr(lotes.projeto, "gpu_alheia", lambda pid: ["llama-server (PID 222)"])
    with pytest.raises(lotes.GpuOcupada, match="Outro programa está usando a GPU"):
        lotes._liberar_vram(False)
    lotes._liberar_vram(True)  # a pessoa aceitou: segue

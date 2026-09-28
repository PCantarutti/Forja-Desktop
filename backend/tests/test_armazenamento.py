"""Onde os arquivos do usuário ficam e o que sai com a conversa.

Anexo do disco é usado no lugar (sem cópia) e nunca é apagado; cópia que o Forja fez (colado, print,
prévia, referência de imagem) sai com a conversa que a usou; imagem e vídeo têm o mesmo desenho de
pastas; e os segredos em repouso vão cifrados.
"""
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, imagegen, localai, lotes, mirror, segredo, settings, shell, tools, uploads
from app.main import app


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.json")
    monkeypatch.setattr(imagegen, "OUT_DIR", tmp_path / "imagens")
    monkeypatch.setattr(localai, "VIDEOS", tmp_path / "videos")
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(tools, "SPILL_DIR", (tmp_path / "spill").resolve())
    monkeypatch.setattr("app.main.SPILL_DIR", (tmp_path / "spill").resolve())
    monkeypatch.setattr(uploads, "CAPTURAS_DIR", tmp_path / "capturas")
    localai.write_config(localai._blank())
    yield


def _conversa(pasta: Path, kind="chat") -> int:
    pasta.mkdir(parents=True, exist_ok=True)
    with db.session() as s:
        c = db.Conversation(kind=kind, workspace=str(pasta))
        s.add(c)
        s.commit()
        return c.id


def _mensagem(conv: int, meta: dict, role="user") -> None:
    with db.session() as s:
        s.add(db.Message(conversation_id=conv, role=role, content="x", meta=meta))
        s.commit()


def test_anexo_do_disco_nao_copia_e_sobrevive_a_conversa(tmp_path):
    proj, fora = tmp_path / "proj", tmp_path / "Documentos"
    conv = _conversa(proj)
    fora.mkdir()
    doc = fora / "planilha.csv"
    doc.write_bytes(b"a,b\n1,2\n")
    with TestClient(app) as c:
        a = c.post("/api/uploads/referencia", json={"path": str(doc), "conv": conv}).json()
        assert a["externo"] and Path(a["path"]) == doc.resolve()
        assert not (proj / uploads.UPLOAD_DIR).exists()  # nenhuma cópia
        _mensagem(conv, {"attachments": [a]})
        assert c.get("/api/files", params={"path": a["path"], "conv": conv}).content == b"a,b\n1,2\n"
        # outra conversa não enxerga o anexo desta (nem arquivo solto do disco)
        outra = _conversa(tmp_path / "outro")
        uploads._REFERENCIADOS.clear()
        assert c.get("/api/files", params={"path": a["path"], "conv": outra}).status_code == 400
        assert c.delete(f"/api/conversations/{conv}").status_code == 200
    assert doc.read_bytes() == b"a,b\n1,2\n"  # o arquivo do usuário nunca é apagado


def test_anexo_dentro_da_pasta_vira_relativo(tmp_path):
    proj = tmp_path / "proj"
    conv = _conversa(proj)
    (proj / "notas.md").write_text("oi")
    a = uploads.referenciar(str(proj / "notas.md"), proj)
    assert a["path"] == "notas.md" and "externo" not in a


def test_apagar_conversa_leva_as_copias_e_o_spill(tmp_path):
    proj = tmp_path / "proj"
    conv = _conversa(proj)
    novo = uploads.save("colado.png", b"png", "image/png", proj, conv)  # pasta da conversa
    assert novo["path"].startswith(f"{uploads.UPLOAD_DIR}/{conv}/")
    antigo = uploads.save("velho.txt", b"txt", "text/plain", proj, 0)  # versão anterior: raiz dos uploads
    vizinho = uploads.save("de-outra.txt", b"txt", "text/plain", proj, 0)  # de outra conversa, fica
    _mensagem(conv, {"attachments": [novo, antigo]})
    spill = tools.SPILL_DIR / str(conv)
    spill.mkdir(parents=True)
    (spill / "c1.txt").write_text("saída grande")
    with TestClient(app) as c:
        assert c.delete(f"/api/conversations/{conv}").status_code == 200
    assert not (proj / novo["path"]).exists() and not (proj / uploads.UPLOAD_DIR / str(conv)).exists()
    assert not (proj / antigo["path"]).exists()
    assert (proj / vizinho["path"]).exists()
    assert not spill.exists()


def test_print_do_navegador_fica_fora_do_repositorio_ate_a_conversa_sair(tmp_path):
    proj = tmp_path / "proj"
    conv = _conversa(proj)
    tok = shell.CONV.set(str(conv))
    try:
        a = uploads.salvar_captura("browser.jpg", b"jpg")
    finally:
        shell.CONV.reset(tok)
    f = Path(a["path"])
    assert f.parent == uploads.CAPTURAS_DIR / str(conv) and not (proj / ".forja").exists()
    assert uploads.data_url(a).startswith("data:image/jpeg;base64,")  # o histórico relê do disco a cada turno
    _mensagem(conv, {"attachments": [a]}, role="tool")
    with TestClient(app) as c:
        assert c.get("/api/files", params={"path": a["path"], "conv": conv}).content == b"jpg"
        outra = _conversa(tmp_path / "outra")
        assert c.get("/api/files", params={"path": a["path"], "conv": outra}).status_code == 400
        assert c.delete(f"/api/conversations/{conv}").status_code == 200
    assert not f.parent.exists()


def test_apagar_em_lote_limpa_igual(tmp_path):
    proj = tmp_path / "proj"
    conv = _conversa(proj)
    a = uploads.save("colado.png", b"png", "image/png", proj, conv)
    _mensagem(conv, {"attachments": [a]})
    with TestClient(app) as c:
        assert c.post("/api/conversations/bulk", json={"ids": [conv], "action": "delete"}).json()["done"] == 1
    assert not (proj / a["path"]).exists()


def test_upload_fica_fora_do_git(tmp_path):
    proj = tmp_path / "repo"
    (proj / ".git" / "info").mkdir(parents=True)
    uploads.save("a.txt", b"x", "text/plain", proj, 7)
    assert ".forja/uploads/" in (proj / ".git" / "info" / "exclude").read_text().splitlines()


def test_spill_de_outra_conversa_nao_se_le(tmp_path):
    (tools.SPILL_DIR / "5").mkdir(parents=True)
    (tools.SPILL_DIR / "6").mkdir(parents=True)
    (tools.SPILL_DIR / "5" / "a.txt").write_text("minha")
    (tools.SPILL_DIR / "6" / "b.txt").write_text("alheia")
    tok = shell.CONV.set("5")
    try:
        assert tools.resolve_leitura(tmp_path / "ws", str(tools.SPILL_DIR / "5" / "a.txt")).read_text() == "minha"
        with pytest.raises(tools.ToolError):
            tools.resolve_leitura(tmp_path / "ws", str(tools.SPILL_DIR / "6" / "b.txt"))
    finally:
        shell.CONV.reset(tok)


def test_referencia_colada_sai_com_a_conversa_menos_se_outra_usa(tmp_path):
    with TestClient(app) as c:
        so_minha = c.post("/api/imagens/referencia", files={"file": ("a.png", b"so-minha", "image/png")}).json()["path"]
        dividida = c.post("/api/imagens/referencia", files={"file": ("b.png", b"dividida", "image/png")}).json()["path"]
        video = c.post("/api/imagens/referencia?video=true", files={"file": ("q.png", b"quadro", "image/png")}).json()["path"]
        assert Path(video).parent == lotes.referencias_dir(True)  # a do vídeo mora na pasta de vídeos
        conv, outra = _conversa(tmp_path / "a", "imagem"), _conversa(tmp_path / "b", "imagem")
        _mensagem(conv, {"refs": [so_minha, dividida], "opts": {"mask": video}})
        _mensagem(outra, {"refs": [dividida]})
        assert c.delete(f"/api/conversations/{conv}").status_code == 200
    assert not Path(so_minha).exists() and not Path(video).exists()
    assert Path(dividida).exists()


def test_expurgo_de_referencia_que_ninguem_cita(tmp_path):
    pasta = lotes.referencias_dir()
    pasta.mkdir(parents=True)
    velha, nova, usada = pasta / "velha.png", pasta / "nova.png", pasta / "usada.png"
    for f in (velha, nova, usada):
        f.write_bytes(b"x")
    antigo = time.time() - 3 * 86400
    os.utime(velha, (antigo, antigo))
    os.utime(usada, (antigo, antigo))
    _mensagem(_conversa(tmp_path / "c", "imagem"), {"refs": [str(usada)]})
    assert lotes.limpar_referencias() == 1
    assert not velha.exists() and nova.exists() and usada.exists()  # a nova ainda pode estar no campo


def test_video_tem_descartadas_e_previas_na_pasta_dele(tmp_path):
    assert lotes.descartadas_dir(True) == imagegen.video_dir() / "descartadas"
    assert lotes.previas_dir(True) == imagegen.video_dir() / ".previas"
    assert lotes.descartadas_dir() == imagegen.out_dir() / "descartadas"
    for video in (False, True):
        lotes.descartadas_dir(video).mkdir(parents=True)
        (lotes.descartadas_dir(video) / ("a.webm" if video else "a.png")).write_bytes(b"x")
    assert lotes.limpar_descartadas(0) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI é do Windows")
def test_segredos_vao_cifrados():
    valores = settings.load()
    provs = [{**p, "api_key": ""} for p in valores["providers"]]
    provs[0]["api_key"] = "sk-segredo-123"
    settings.update({"providers": provs})
    with db.session() as s:
        guardado = s.get(db.AppSetting, "providers").value
    assert guardado[0]["api_key"].startswith(segredo.PREFIXO) and "sk-segredo" not in str(guardado)
    assert settings.load()["providers"][0]["api_key"] == "sk-segredo-123"
    localai.set_hf_token("hf_abc")
    assert "hf_abc" not in config.LOCAL_CONFIG.read_text() and localai.hf_token() == "hf_abc"
    assert segredo.decifrar("texto-antigo") == "texto-antigo"  # valor de antes da cifra continua valendo

"""Narração de vídeo pela voz do Forja (tts.falar / POST /api/tts/falar), sem motor de verdade."""
import io
import json

import pytest

from app import config, tts
from app.tools import ToolError


class ProcFalso:
    """Motor de voz falso: responde ao pedido com PALAVRAS e OK, como o tts_fish.py."""
    def __init__(self, palavras=True):
        self.stdin = io.StringIO()
        linhas = (["FASE gerando", "PALAVRAS " + json.dumps([["Olá", 0, 300], ["mundo", 320, 700]])] if palavras else []) + \
                 ["OK 0.80 7 1.5"]
        self.stdout = iter(l + "\n" for l in linhas)

    def poll(self):
        return None


@pytest.fixture
def motor(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "SAIDA", tmp_path / "saida")
    monkeypatch.setattr(tts, "modelos", lambda: [{"nome": "Fish", "motor": "fish", "modelo": "x"}])
    monkeypatch.setattr(tts, "vozes", lambda: [{"id": "narrador", "nome": "Narrador", "caminho": "ref.wav", "texto": "oi"}])
    monkeypatch.setattr(tts, "instalado", lambda motor: True)
    monkeypatch.setattr(tts, "_local", lambda m, mid, job: m)
    import app.lotes
    monkeypatch.setattr(app.lotes, "_liberar_vram", lambda confirm=False: None)
    procs = []

    def subir(m, mid):
        procs.append(ProcFalso(getattr(subir, "palavras", True)))
        return procs[-1]
    monkeypatch.setattr(tts, "_subir", subir)
    return subir, procs


def test_falar_devolve_arquivo_e_palavras(motor):
    subir, procs = motor
    r = tts.falar("Olá mundo", voz="Narrador")
    assert r["palavras"] == [["Olá", 0, 300], ["mundo", 320, 700]] and r["duracao"] == 0.8 and r["voz"] == "Narrador"
    pedido = json.loads(procs[0].stdin.getvalue())
    assert pedido["palavras"] is True and pedido["texto"] == "Olá mundo" and pedido["ref"] == "ref.wav"
    assert pedido["saida"].endswith(".wav") and "conteudo" in pedido["saida"]


def test_sem_tempos_do_motor_estima_pelo_tamanho(motor):
    subir, _ = motor
    subir.palavras = False
    r = tts.falar("Olá mundo grande", voz="narrador")
    assert [w for w, _, _ in r["palavras"]] == ["Olá", "mundo", "grande"]
    assert r["palavras"][0][1] == 0 and r["palavras"][-1][2] <= 800 and r["palavras"][1][1] > r["palavras"][0][1]


def test_falar_valida(motor, monkeypatch):
    with pytest.raises(ToolError, match="Texto vazio"):
        tts.falar("  ")
    with pytest.raises(ToolError, match="Voz de referência não encontrada"):
        tts.falar("oi", voz="outra")
    with pytest.raises(ToolError, match="Modelo de voz não encontrado"):
        tts.falar("oi", modelo="Outro", voz="narrador")


def test_rota_aceita_so_o_token_proprio_ou_o_da_api(motor, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(config, "API_TOKEN", "token-de-teste")
    c = TestClient(app)
    corpo = {"texto": "Olá mundo", "voz": "narrador"}
    assert c.post("/api/tts/falar", json=corpo).status_code == 403
    assert c.post("/api/tts/falar", json=corpo, headers={"x-forja-token": "errado"}).status_code == 403
    r = c.post("/api/tts/falar", json=corpo, headers={"x-forja-token": tts.TOKEN_FALAR})
    assert r.status_code == 200 and r.json()["palavras"][1][0] == "mundo"
    # o token da voz não abre o resto da API
    assert c.get("/api/tts", headers={"x-forja-token": tts.TOKEN_FALAR}).status_code == 403
    assert c.post("/api/tts/falar", json={"texto": ""}, headers={"x-forja-token": tts.TOKEN_FALAR}).status_code == 400

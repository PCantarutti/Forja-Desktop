"""E4: cache do prompt em disco (salvar/restaurar slot) e descarga do modelo ocioso."""
import asyncio
import time
from pathlib import Path

import pytest

from app import agent, config, kvcache, llm, localai, modelctl


@pytest.fixture
def servidor(tmp_path, monkeypatch):
    """llama-server falso: save grava o arquivo, restore falha se ele não existe."""
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"x" * 1000)
    estado = {"running": True, "pid": 7, "path": str(gguf), "alias": "m",
              "params": {"ctx": 8192, "cache_type_k": "q8_0", "cache_type_v": "q8_0", "kv_unified": True}}
    chamadas = []
    monkeypatch.setattr(kvcache, "PASTA", tmp_path / "kv")
    monkeypatch.setattr(kvcache, "_estado", lambda: estado)
    monkeypatch.setattr(kvcache, "tipo_de_cache", lambda path: estado.get("tipo", "comum"))
    monkeypatch.setattr(config, "CACHE_DISCO", True)
    monkeypatch.setattr(config, "CACHE_DISCO_GB", 4.0)
    kvcache._DONO.clear()
    kvcache._PID["pid"] = 0

    def post(caminho, corpo, timeout=120):
        acao = caminho.split("action=")[1]
        chamadas.append((acao, corpo["filename"]))
        arq = kvcache.pasta() / corpo["filename"]
        if acao == "save":
            arq.write_bytes(b"k" * estado.get("tam", 100))
            return {"n_saved": 5000}
        if not arq.exists() or estado.get("restore_falha"):
            raise RuntimeError("HTTP 400")
        return {"n_restored": 5000}

    monkeypatch.setattr(kvcache, "_post", post)
    return estado, chamadas


def test_trocar_de_conversa_salva_uma_e_restaura_a_outra(servidor):
    _, chamadas = servidor
    assert kvcache.assume(1, 0) == ""            # nada salvo ainda
    assert "salvou a conversa 1" in kvcache.assume(2, 0)
    feito = kvcache.assume(1, 0)                 # volta para a 1: guarda a 2 e traz a 1
    assert "salvou a conversa 2" in feito and "restaurou a conversa 1" in feito
    assert [a for a, _ in chamadas] == ["save", "save", "restore"]
    assert kvcache.assume(1, 0) == ""            # já é o dono: nada a fazer


def test_chave_diferente_descarta_sem_restaurar(servidor):
    estado, chamadas = servidor
    kvcache.assume(1, 0)
    kvcache.assume(2, 0)                          # salvou a 1
    estado["params"] = {**estado["params"], "ctx": 16384}  # recarregou com outra janela
    estado["pid"] = 8
    kvcache.assume(1, 0)
    assert ("restore", chamadas[0][1]) not in chamadas   # o arquivo velho nem foi tentado
    assert not list(kvcache.pasta().glob("*-1.bin"))     # e saiu do disco


def test_restore_que_falha_nao_trava(servidor):
    estado, _ = servidor
    kvcache.assume(1, 0)
    kvcache.assume(2, 0)
    estado["restore_falha"] = True
    assert "restaurou" not in kvcache.assume(1, 0)
    assert not list(kvcache.pasta().glob("*-1.bin"))


def test_limite_apaga_o_menos_usado(servidor, monkeypatch):
    estado, _ = servidor
    estado["tam"] = 400 * 2**20                   # 400 MB por conversa
    monkeypatch.setattr(config, "CACHE_DISCO_GB", 1.0)
    for conv in (1, 2, 3):
        kvcache.salvar(conv, 0)
        time.sleep(0.02)
    restantes = sorted(a.stem.split("-")[1] for a in kvcache.pasta().glob("*.bin"))
    assert restantes == ["2", "3"]


def test_apagar_conversa_apaga_o_cache(servidor):
    kvcache.salvar(5, 0)
    kvcache.apagar_conversa(5)
    assert not list(kvcache.pasta().glob("*-5.*"))


def test_hibrido_e_swa_sem_janela_inteira_ficam_de_fora(servidor):
    estado, chamadas = servidor
    estado["tipo"] = "hibrido"
    ok, motivo = kvcache.suportado(estado["path"], estado["params"])
    assert not ok and "híbrido" in motivo
    assert kvcache.assume(1, 0) == "" and kvcache.assume(2, 0) == "" and chamadas == []
    estado["tipo"] = "swa"
    assert not kvcache.suportado(estado["path"], estado["params"])[0]
    assert kvcache.suportado(estado["path"], {**estado["params"], "swa_full": True})[0]


def test_argv_so_liga_o_slot_save_path_onde_restaura(tmp_path, monkeypatch):
    monkeypatch.setattr(localai, "hardware", lambda: {"gpus": []})
    monkeypatch.setattr(localai, "alias_of", lambda p: "m")
    p = {**localai.DEFAULT_PARAMS}
    monkeypatch.setattr(kvcache, "tipo_de_cache", lambda path: "comum")
    assert "--slot-save-path" in localai.argv(Path("llama-server.exe"), str(tmp_path / "m.gguf"), p)
    monkeypatch.setattr(kvcache, "tipo_de_cache", lambda path: "hibrido")
    assert "--slot-save-path" not in localai.argv(Path("llama-server.exe"), str(tmp_path / "m.gguf"), p)
    monkeypatch.setattr(kvcache, "tipo_de_cache", lambda path: "swa")
    a = localai.argv(Path("llama-server.exe"), str(tmp_path / "m.gguf"), {**p, "swa_full": True})
    assert "--swa-full" in a and "--slot-save-path" in a


def test_descarregar_salva_o_cache_antes(monkeypatch):
    ordem = []
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "alias": "m"})
    monkeypatch.setattr(kvcache, "salvar_todos", lambda: ordem.append("salvar") or 1)
    monkeypatch.setattr(localai, "unload", lambda: ordem.append("unload"))

    async def roda():
        async for _ in modelctl.unload("teste"):
            pass
    asyncio.run(roda())
    assert ordem == ["salvar", "unload"]


def test_ociosidade(monkeypatch):
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "alias": "m"})
    monkeypatch.setattr(localai, "image_busy", lambda: False)
    monkeypatch.setattr(localai, "_loading", {})
    monkeypatch.setattr(config, "DESCARREGAR_OCIOSO_MIN", 15)
    monkeypatch.setitem(llm.ULTIMO_USO, "t", time.monotonic() - 16 * 60)
    monkeypatch.setattr(agent, "RUNS", {})
    assert modelctl.precisa_descarregar()                    # 16 min sem uso

    class Ativa:
        finished, approvals = False, {}
    monkeypatch.setattr(agent, "RUNS", {"r": Ativa()})
    assert not modelctl.precisa_descarregar()                # execução ativa segura o modelo
    monkeypatch.setattr(agent, "RUNS", {})
    monkeypatch.setitem(llm.ULTIMO_USO, "t", time.monotonic() - 5 * 60)
    assert not modelctl.precisa_descarregar()                # usado há 5 min
    monkeypatch.setattr(config, "DESCARREGAR_OCIOSO_MIN", 0)
    monkeypatch.setitem(llm.ULTIMO_USO, "t", time.monotonic() - 999 * 60)
    assert not modelctl.precisa_descarregar()                # 0 = nunca


def test_recarrega_sob_demanda_troca_so_sem_outra_execucao(monkeypatch):
    from app import agent
    spec = {"provider": config.LOCAL_PROVIDER["id"], "model": "m"}
    monkeypatch.setattr(localai, "image_busy", lambda: False)
    monkeypatch.setattr(localai, "_loading", {})
    monkeypatch.setattr(agent, "RUNS", {})
    monkeypatch.setattr(localai, "status", lambda: {"running": False})
    assert modelctl.recarregar_sob_demanda(spec)
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "alias": "m"})
    assert not modelctl.recarregar_sob_demanda(spec)       # já é o do ar
    monkeypatch.setattr(localai, "status", lambda: {"running": True, "alias": "outro"})
    assert modelctl.recarregar_sob_demanda(spec)           # seletor só escolheu: a mensagem troca
    propria = type("R", (), {"finished": False, "approvals": {}})()
    outra = type("R", (), {"finished": False, "approvals": {}})()
    monkeypatch.setattr(agent, "RUNS", {"a": propria})
    assert modelctl.recarregar_sob_demanda(spec, propria)  # a própria execução não segura a troca
    monkeypatch.setattr(agent, "RUNS", {"a": propria, "b": outra})
    assert not modelctl.recarregar_sob_demanda(spec, propria)  # outra gerando: não derruba


def test_mudar_parametro_do_cache_descarta_o_salvo_daquele_modelo(servidor, monkeypatch, tmp_path):
    estado, _ = servidor
    kvcache.pasta().mkdir(parents=True, exist_ok=True)
    kvcache.salvar(1, 0)
    assert kvcache.bytes_do_modelo(estado["path"]) == 100
    assert kvcache.bytes_do_modelo(str(tmp_path / "outro.gguf")) == 0
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(localai, "params", lambda p: {"ctx": 8192, "mlock": False})
    monkeypatch.setattr(localai, "save_params", lambda p, prm: {"ctx": 8192, **prm})
    c = TestClient(main.app)
    c.put("/api/local/params", json={"path": estado["path"], "params": {"mlock": True}})
    assert kvcache.bytes_do_modelo(estado["path"]) == 100          # mlock não mexe no KV
    c.put("/api/local/params", json={"path": estado["path"], "params": {"ctx": 16384}})
    assert kvcache.bytes_do_modelo(estado["path"]) == 0            # ctx muda o KV: não restauraria mais


def test_aviso_dos_padroes_novos_aparece_uma_vez_e_desfazer_volta_o_f16(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.json")
    assert not localai.aviso_padroes_e4()                           # instalação nova: nada a avisar
    localai.save_params(str(tmp_path / "m.gguf"), {"mlock": True})
    assert localai.aviso_padroes_e4()
    localai.visto_padroes_e4(desfazer=True)
    assert not localai.aviso_padroes_e4()
    assert localai.read_config()["defaults"] == {"cache_type_k": "f16", "cache_type_v": "f16", "kv_unified": False}

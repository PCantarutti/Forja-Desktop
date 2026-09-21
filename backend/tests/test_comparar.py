import asyncio
import json
import types

import pytest

from app import comparar, db, llm, mirror
from app.tools import ToolError


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    comparar._RUNS.clear()
    yield
    comparar._RUNS.clear()


def _conversa(kind="comparar") -> int:
    with db.session() as s:
        c = db.Conversation(kind=kind)
        s.add(c)
        s.commit()
        return c.id


def _fake_llm(monkeypatch, textos=None, falhar=(), pausa=0.0):
    """Troca o LLM por um gerador de mentira; anota a ordem em que os modelos foram chamados."""
    textos, chamados = textos or {}, []

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None):
        chamados.append(model)
        if model in falhar:
            raise llm.LLMError("modelo caiu")
        for pedaco in textos.get(model, [f"resposta de {model}"]):
            if pausa:
                await asyncio.sleep(pausa)
            yield ("content", pedaco)
        yield ("done", {"prompt_tokens": 10, "completion_tokens": 5})

    async def context_limit(*a, **k):
        return 8192

    monkeypatch.setattr(comparar.llm, "chat_stream", chat_stream)
    monkeypatch.setattr(comparar.llm, "context_limit", context_limit)
    return chamados


def _fake_local(monkeypatch, tmp_path, falhar=()):
    """llama.cpp de mentira: registra load/unload e finge um modelo carregado por vez."""
    eventos: list[str] = []
    atual = {"alias": ""}

    def load(path, patch=None):
        nome = str(path).rsplit("\\", 1)[-1].rsplit("/", 1)[-1].removesuffix(".gguf")
        eventos.append(f"load:{nome}")
        if nome in falhar:
            raise ToolError("sem memória")
        atual["alias"] = nome
        return {}

    def unload():
        eventos.append("unload")
        atual["alias"] = ""

    fake = types.SimpleNamespace(
        load=load, unload=unload, cancel_load=lambda: True,
        status=lambda: {"running": bool(atual["alias"]), "alias": atual["alias"]},
        alias_of=lambda p: str(p).rsplit("\\", 1)[-1].rsplit("/", 1)[-1].removesuffix(".gguf"))
    monkeypatch.setattr(comparar, "localai", fake)
    return eventos


def _gguf(tmp_path, nome) -> str:
    f = tmp_path / f"{nome}.gguf"
    f.write_bytes(b"GGUF")
    return str(f)


def _rodar(**kw) -> dict:
    """start() + espera a task acabar, num loop só — é o que o endpoint faz."""
    async def main():
        msg = comparar.start(**kw)
        pendentes = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pendentes)
        return comparar.estado(msg["id"])
    return asyncio.run(main())


def _item(estado, nome) -> dict:
    return next(i for i in estado["itens"] if i["nome"] == nome)


# ------------------------------------------------------------------ preparo

def test_preparar_exige_dois_modelos():
    with pytest.raises(ToolError):
        comparar._preparar([{"provider": "ollama", "model": "a"}], "paralelo")
    demais = [{"provider": "ollama", "model": f"m{i}"} for i in range(comparar.MAX_MODELOS + 1)]
    with pytest.raises(ToolError):
        comparar._preparar(demais, "paralelo")


def test_preparar_recusa_gguf_em_paralelo(tmp_path, monkeypatch):
    _fake_local(monkeypatch, tmp_path)
    itens = [{"path": _gguf(tmp_path, "qwen")}, {"provider": "ollama", "model": "llama3"}]
    with pytest.raises(ToolError, match="sequencial"):
        comparar._preparar(itens, "paralelo")
    pronto = comparar._preparar(itens, "sequencial")
    assert [i["rotulo"] for i in pronto] == ["A", "B"]
    assert pronto[0]["provider"] == "local" and pronto[0]["nome"] == "qwen"


def test_preparar_recusa_repetido():
    with pytest.raises(ToolError, match="repetido"):
        comparar._preparar([{"provider": "ollama", "model": "a"}, {"provider": "ollama", "model": "a"}],
                           "paralelo")


# ------------------------------------------------------------------ execução

def test_paralelo_responde_todos(monkeypatch):
    _fake_llm(monkeypatch, {"a": ["oi ", "mundo"]})
    conv = _conversa()
    itens = [{"provider": "ollama", "model": m} for m in ("a", "b", "c")]
    est = _rodar(conv_id=conv, prompt="teste", itens=itens)
    assert est["status"] == "pronto"
    assert [i["status"] for i in est["itens"]] == ["pronto"] * 3
    assert _item(est, "a")["content"] == "oi mundo"
    assert _item(est, "a")["stats"]["tokens"] == 5
    # e ficou no banco, não só na memória
    assert comparar._mensagem(est["message_id"])["meta"]["itens"][0]["content"] == "oi mundo"


def test_falha_de_um_modelo_nao_derruba_os_outros(monkeypatch):
    _fake_llm(monkeypatch, falhar=("b",))
    itens = [{"provider": "ollama", "model": m} for m in ("a", "b")]
    est = _rodar(conv_id=_conversa(), prompt="teste", itens=itens)
    assert est["status"] == "pronto"
    assert _item(est, "a")["status"] == "pronto"
    assert _item(est, "b")["status"] == "erro" and "caiu" in _item(est, "b")["error"]


def test_sequencial_carrega_e_descarrega_cada_gguf(tmp_path, monkeypatch):
    eventos = _fake_local(monkeypatch, tmp_path)
    chamados = _fake_llm(monkeypatch)
    itens = [{"path": _gguf(tmp_path, "um")}, {"path": _gguf(tmp_path, "dois")}]
    est = _rodar(conv_id=_conversa(), prompt="teste", itens=itens, modo="sequencial")
    assert est["status"] == "pronto"
    assert eventos == ["load:um", "load:dois", "unload"]
    assert chamados == ["um", "dois"]  # um de cada vez, na ordem escolhida


def test_gguf_que_nao_carrega_vira_erro_do_item(tmp_path, monkeypatch):
    eventos = _fake_local(monkeypatch, tmp_path, falhar=("um",))
    _fake_llm(monkeypatch)
    itens = [{"path": _gguf(tmp_path, "um")}, {"path": _gguf(tmp_path, "dois")}]
    est = _rodar(conv_id=_conversa(), prompt="teste", itens=itens, modo="sequencial")
    assert _item(est, "um")["status"] == "erro" and "memória" in _item(est, "um")["error"]
    assert _item(est, "dois")["status"] == "pronto"
    assert eventos == ["load:um", "load:dois", "unload"]


def test_modelo_carregado_pede_confirmacao(tmp_path, monkeypatch):
    _fake_local(monkeypatch, tmp_path).append("")  # eventos: só para criar o fake
    comparar.localai.load(_gguf(tmp_path, "ocupando"))  # já tem um modelo na VRAM
    _fake_llm(monkeypatch)
    itens = [{"path": _gguf(tmp_path, "um")}, {"path": _gguf(tmp_path, "dois")}]
    with pytest.raises(comparar.ModeloCarregado):
        comparar.start(_conversa(), "teste", itens, modo="sequencial")
    est = _rodar(conv_id=_conversa(), prompt="teste", itens=itens, modo="sequencial", confirm=True)
    assert est["status"] == "pronto"


def test_cancelar_no_meio_poupa_os_pendentes(tmp_path, monkeypatch):
    eventos = _fake_local(monkeypatch, tmp_path)
    _fake_llm(monkeypatch, {"um": ["a", "b", "c", "d"]}, pausa=0.05)
    itens = [{"path": _gguf(tmp_path, "um")}, {"path": _gguf(tmp_path, "dois")}]

    async def main():
        msg = comparar.start(_conversa(), "teste", itens, modo="sequencial")
        await asyncio.sleep(0.08)
        comparar.cancelar(msg["id"])
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return comparar.estado(msg["id"])

    est = asyncio.run(main())
    assert est["status"] == "cancelado"
    assert _item(est, "um")["status"] == "cancelado"
    assert _item(est, "um")["content"]  # o texto parcial não se perde
    assert _item(est, "dois")["status"] == "cancelado"
    assert "load:dois" not in eventos  # o ganho real: o segundo nem chega a carregar


def test_estado_depois_do_fim_vem_do_banco(monkeypatch):
    _fake_llm(monkeypatch)
    itens = [{"provider": "ollama", "model": m} for m in ("a", "b")]
    est = _rodar(conv_id=_conversa(), prompt="teste", itens=itens)
    assert est["message_id"] not in comparar._RUNS
    de_novo = comparar.estado(est["message_id"])
    assert de_novo["status"] == "pronto" and len(de_novo["itens"]) == 2


# ------------------------------------------------------------------ voto e placar

def test_voto_e_placar(monkeypatch):
    _fake_llm(monkeypatch)
    itens = [{"provider": "ollama", "model": m} for m in ("a", "b")]
    est = _rodar(conv_id=_conversa(), prompt="teste", itens=itens, cego=True)
    mid = est["message_id"]

    with pytest.raises(ToolError):
        comparar.votar(mid, "99")
    comparar.votar(mid, "0")
    assert comparar.estado(mid)["voto"] == "0" and comparar.estado(mid)["revelado"]
    comparar.votar(mid, "0")  # o mesmo de novo desfaz
    assert comparar.estado(mid)["voto"] == ""

    comparar.votar(mid, "1")
    placar = {l["nome"]: l for l in comparar.placar()["linhas"]}
    assert placar["b"]["vitorias"] == 1 and placar["a"]["vitorias"] == 0
    assert placar["a"]["rodadas"] >= 1  # o banco dos testes é o mesmo para todos: contagem relativa
    assert comparar.placar()["linhas"][0]["nome"] == "b"  # o vencedor vem primeiro


# ------------------------------------------------------------------ API

def test_api_roda_comparacao_e_devolve_sse(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    _fake_llm(monkeypatch, {"a": ["oi"]})
    with TestClient(app) as c:
        conv = c.post("/api/conversations", json={"kind": "comparar"})
        assert conv.status_code == 200 and conv.json()["kind"] == "comparar"
        r = c.post(f"/api/comparar/{conv.json()['id']}/rodar",
                   json={"prompt": "teste", "itens": [{"provider": "ollama", "model": "a"},
                                                      {"provider": "ollama", "model": "b"}]})
        assert r.status_code == 200
        eventos = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
        assert eventos[-1]["status"] == "pronto"
        assert [i["content"] for i in eventos[-1]["itens"]] == ["oi", "resposta de b"]
        # reconectar depois do fim devolve o resultado e fecha
        de_novo = c.get(f"/api/comparar/{eventos[-1]['message_id']}/stream")
        assert json.loads(de_novo.text.splitlines()[0][6:])["status"] == "pronto"


def test_api_recusa_gguf_em_paralelo(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    _fake_local(monkeypatch, tmp_path)
    _fake_llm(monkeypatch)
    with TestClient(app) as c:
        conv = c.post("/api/conversations", json={"kind": "comparar"}).json()["id"]
        r = c.post(f"/api/comparar/{conv}/rodar",
                   json={"prompt": "teste", "itens": [{"path": _gguf(tmp_path, "um")},
                                                      {"provider": "ollama", "model": "b"}]})
        assert r.status_code == 400 and "sequencial" in r.json()["detail"]

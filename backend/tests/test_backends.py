"""E13-A: capacidades por backend e janela real em cada tipo de servidor (servidores falsos)."""
import asyncio

import httpx
import pytest

from app import agent, config, db, llm, settings

_REAL = httpx.AsyncClient


@pytest.fixture
def servidor(monkeypatch):
    """Instala respostas falsas: {(método, caminho): json}. Devolve a lista de caminhos pedidos."""
    pedidos: list[str] = []
    rotas: dict = {}

    def trata(req: httpx.Request):
        pedidos.append(req.url.path)
        corpo = rotas.get((req.method, req.url.path))
        return httpx.Response(404, json={}) if corpo is None else httpx.Response(200, json=corpo)

    monkeypatch.setattr(llm.httpx, "AsyncClient",
                        lambda **kw: _REAL(transport=httpx.MockTransport(trata), **kw))
    llm._JANELAS.clear()
    return rotas, pedidos


def _provedor(monkeypatch, tipo, url="http://srv:8000/v1", **extra):
    monkeypatch.setitem(config.PROVIDERS, "p", {"id": "p", "name": "P", "type": tipo, "url": url, **extra})


def _janela(num_ctx=32768):
    return asyncio.run(llm.context_limit("p", "m", num_ctx))


def test_vllm_informa_max_model_len(monkeypatch, servidor):
    rotas, _ = servidor
    _provedor(monkeypatch, "openai")
    rotas[("GET", "/v1/models")] = {"data": [{"id": "outro", "max_model_len": 99}, {"id": "m", "max_model_len": 8192}]}
    assert _janela() == 8192  # e não os 32k do num_ctx


def test_openrouter_e_llama_server(monkeypatch, servidor):
    rotas, _ = servidor
    _provedor(monkeypatch, "openai")
    rotas[("GET", "/v1/models")] = {"data": [{"id": "m", "top_provider": {"context_length": 131072}}]}
    assert _janela() == 131072
    llm._JANELAS.clear()
    rotas[("GET", "/v1/models")] = {"data": [{"id": "m"}]}  # llama-server: janela só no /props
    rotas[("GET", "/props")] = {"default_generation_settings": {"n_ctx": 16384}}
    assert _janela() == 16384


def test_campo_manual_vence_e_nao_pergunta(monkeypatch, servidor):
    _, pedidos = servidor
    _provedor(monkeypatch, "openai", context_window=12000)
    assert _janela() == 12000 and not pedidos


def test_openai_sem_janela_recusa_com_mensagem_clara(monkeypatch, servidor):
    _provedor(monkeypatch, "openai")
    assert _janela() is None
    msg = llm.janela_obrigatoria("p", None)
    assert "Janela de contexto" in msg and "--max-model-len" in msg
    assert llm.janela_obrigatoria("p", 8192) is None
    monkeypatch.setitem(config.PROVIDERS, "q", {"id": "q", "type": "lmstudio", "url": "http://x/v1"})
    assert llm.janela_obrigatoria("q", None) is None  # só o genérico recusa

    async def falha(*a, **k):
        raise AssertionError("não devia chamar o modelo")
        yield

    monkeypatch.setattr(llm, "chat_stream", falha)
    with db.session() as s:
        c = db.Conversation(kind="agent")
        s.add(c)
        s.commit()
        conv = c.id

    async def cenario():
        req = agent.RunRequest(content="oi", provider="p", model="m", mode="agent", permission="manual")
        return [ev async for ev in agent.run_agent(conv, req, agent.Run(conv))]

    erros = [e for e in asyncio.run(cenario()) if e.get("type") == "event"
             and (e["message"].get("meta") or {}).get("kind") == "error"]
    assert erros and "Janela de contexto" in erros[0]["message"]["content"]


def test_ollama_local_le_a_janela_carregada(monkeypatch, servidor):
    rotas, _ = servidor
    _provedor(monkeypatch, "ollama", url="http://127.0.0.1:11434/v1")
    assert _janela() == 32768  # nada carregado ainda: o que vamos pedir
    rotas[("GET", "/api/ps")] = {"models": [{"name": "m", "model": "m", "context_length": 8192}]}
    assert _janela() == 8192  # o Ollama cortou por memória


def test_ollama_nuvem_usa_a_janela_do_modelo(monkeypatch, servidor):
    rotas, pedidos = servidor
    _provedor(monkeypatch, "ollama", url="https://ollama.com/v1", api_key="k")
    rotas[("POST", "/api/show")] = {"model_info": {"gptoss.context_length": 131072}}
    assert _janela() == 131072 and _janela() == 131072
    assert pedidos.count("/api/show") == 1  # cacheado


def test_tabela_de_capacidades(monkeypatch):
    _provedor(monkeypatch, "openai")
    assert llm.capacidade("p", "slots") == "nao" and llm.capacidade("p", "janela") == "parcial"
    _provedor(monkeypatch, "ollama", url="https://ollama.com/v1")
    assert llm.tipo_capacidade("p") == "ollama_nuvem" and llm.capacidade("p", "carregar") == "nao"
    assert llm.capacidade("p", "inexistente") == "nao"
    assert all(set(c) == set(llm.ROTULOS_CAPACIDADE) for c in llm.CAPACIDADES.values())
    assert llm.indisponiveis("llamacpp") == {"nao": [], "parcial": []}
    assert "cache em disco" in llm.indisponiveis("lmstudio")["nao"]


def test_cadastro_aceita_e_valida_a_janela():
    base = [{"id": "v", "name": "vLLM", "type": "openai", "url": "http://v:8000/v1"}]
    assert settings._providers([{**base[0], "context_window": "8192"}], [])[0]["context_window"] == 8192
    assert "context_window" not in settings._providers([{**base[0], "context_window": ""}], [])[0]
    with pytest.raises(settings.SettingsError, match="fora do intervalo"):
        settings._providers([{**base[0], "context_window": 100}], [])
    assert "openai" in settings.public()["capacidades"]

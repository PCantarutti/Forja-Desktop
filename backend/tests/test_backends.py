"""E13-B: paridade entre backends — a mesma conversa curta (duas ferramentas e um erro de janela cheia) contra
um servidor falso de cada tipo; e o controle parcial de Ollama/LM Studio (rota segura, descarga, cache)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import config, llm, metricas, modelctl
from tests.test_progresso import _roda


class _Falso(BaseHTTPRequestHandler):
    """Roteiro: 1ª requisição recusa por janela cheia; depois list_dir; depois read_file; depois responde."""
    estado: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        corpo = {"object": "list", "data": [{"id": "m", "context_length": 32768, "max_model_len": 32768}]}
        if self.path.startswith("/api/ps"):
            corpo = {"models": [{"name": "m", "context_length": 32768}]}
        self._json(corpo)

    def _json(self, obj, status=200):
        dado = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dado)))
        self.end_headers()
        self.wfile.write(dado)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        n = self.estado["n"] = self.estado.get("n", 0) + 1
        self.estado.setdefault("corpos", []).append(req)
        if self.path.startswith("/api/generate"):  # descarga do Ollama (keep_alive 0)
            return self._json({"done": True})
        if n == 1 and not self.estado.get("sem_estouro"):
            return self._json({"error": {"message": "the request exceeds the available context size"}}, 400)
        passo = [("list_dir", {"path": "."}), ("read_file", {"path": "a.txt"}), None][min(len(
            [m for m in req["messages"] if m.get("role") == "tool"]), 2)]
        ollama = self.path.startswith("/api/chat")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson" if ollama else "text/event-stream")
        self.end_headers()
        if ollama:
            msg = ({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": passo[0], "arguments": passo[1]}}]}
                   if passo else {"role": "assistant", "content": "Pronto: a.txt diz oi."})
            self.wfile.write((json.dumps({"message": msg, "done": False}) + "\n").encode())
            self.wfile.write((json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                                          "prompt_eval_count": 120, "eval_count": 7}) + "\n").encode())
            return
        delta = ({"tool_calls": [{"index": 0, "id": f"c{n}", "type": "function",
                                  "function": {"name": passo[0], "arguments": json.dumps(passo[1])}}]}
                 if passo else {"content": "Pronto: a.txt diz oi."})
        for ch in ({"choices": [{"index": 0, "delta": delta}]},
                   {"choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 7,
                                             "prompt_tokens_details": {"cached_tokens": 900}}}):
            self.wfile.write(b"data: " + json.dumps(ch).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture
def servidor():
    _Falso.estado = {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Falso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.mark.parametrize("tipo", ["llamacpp", "lmstudio", "openai", "ollama"])
def test_mesma_conversa_em_cada_backend(tipo, servidor, monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("oi", encoding="utf-8")
    url = f"http://127.0.0.1:{servidor}" + ("" if tipo == "ollama" else "/v1")
    monkeypatch.setitem(config.PROVIDERS, "falso", {"id": "falso", "type": tipo, "url": url, "api_key": "",
                                                    "janela": 32768})
    registros = []
    monkeypatch.setattr(metricas, "registra", lambda tipo_, **d: registros.append((tipo_, d)))

    async def janela(*a):
        return 32768

    monkeypatch.setattr(llm, "context_limit", janela)
    real = llm.chat_stream

    async def via_falso(provider, model, messages, tools, num_ctx, effort=None, **kw):
        async for x in real("falso", "m", messages, tools, num_ctx, effort, **kw):
            yield x

    eventos = _roda(monkeypatch, tmp_path, via_falso)
    res = [e["message"] for e in eventos if e.get("type") == "tool_result"]
    assert [m["name"] for m in res] == ["list_dir", "read_file"] and all(m["status"] == "ok" for m in res)
    assert "oi" in res[1]["content"]
    textos = [e["message"]["content"] for e in eventos if e.get("type") == "event"]
    assert any("contexto cheio" in t for t in textos)                       # recusou, compactou e seguiu
    final = [e for e in eventos if e.get("type") == "token"]
    assert "Pronto" in "".join(e["text"] for e in final)
    llms = [d for t, d in registros if t == "llm"]
    if tipo != "ollama":  # compatível com OpenAI: o cache vem em prompt_tokens_details
        assert llms[-1]["cached_tokens"] == 900


def test_auxiliar_no_ollama_nao_troca_o_modelo_carregado(monkeypatch):
    monkeypatch.setitem(config.PROVIDERS, "olla", {"id": "olla", "type": "ollama", "url": "http://127.0.0.1:11434"})
    monkeypatch.setattr(llm, "carregado_externo", lambda p: "qwen-grande")
    r = modelctl.como_rodar("revisor", {"provider": "olla", "model": "outro"})
    assert r.caminho == "modelo-do-principal" and r.spec["model"] == "qwen-grande"
    assert modelctl.como_rodar("worker", {"provider": "olla", "model": "outro"}).caminho == "externo"  # Worker pode
    assert modelctl.como_rodar("revisor", {"provider": "olla", "model": "qwen-grande"}).caminho == "externo"


def test_ollama_local_ocioso_descarrega_com_keep_alive_zero(servidor, monkeypatch):
    monkeypatch.setitem(config.PROVIDERS, "olla", {"id": "olla", "type": "ollama", "url": f"http://127.0.0.1:{servidor}"})
    _Falso.estado["sem_estouro"] = True
    assert llm.descarrega_externo("olla", "m")
    assert _Falso.estado["corpos"][-1] == {"model": "m", "keep_alive": 0}
    monkeypatch.setattr(config, "DESCARREGAR_OCIOSO_MIN", 1)
    monkeypatch.setattr(modelctl, "em_uso", lambda: False)
    llm.ULTIMO_EXTERNO.update(provider="olla", model="m", t=0)
    assert modelctl.externo_ocioso()["model"] == "m"
    llm.ULTIMO_EXTERNO.clear()


def test_cache_medido_fora_do_llamacpp(monkeypatch):
    vistos = []
    from app import db

    class S:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def add(self, m):
            vistos.append(m.dados)

        def commit(self):
            pass

    monkeypatch.setattr(db, "session", lambda: S())
    metricas.registra("llm", papel="agente", prompt_tokens=1000, cached_tokens=900, timings=None)
    assert vistos[-1]["reprocessados"] == 100 and vistos[-1]["cache_n"] == 900

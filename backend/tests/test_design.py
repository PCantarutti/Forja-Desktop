import asyncio

import pytest

from app import db, design, mirror
from app.tools import ToolError

DOC = "<!doctype html><html><head><style>:root{--cor:#000}</style></head><body><section data-section='hero'>{}</section></body></html>"


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    design._RUNS.clear()
    yield
    design._RUNS.clear()


def _projeto() -> int:
    with db.session() as s:
        c = db.Conversation(kind="design")
        s.add(c)
        s.commit()
        return c.id


def _fake_llm(monkeypatch, resposta: str | Exception, vistos: list | None = None):
    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        if vistos is not None:
            vistos.append(messages)
        if isinstance(resposta, Exception):
            raise resposta
        for i in range(0, len(resposta), 20):
            yield "content", resposta[i:i + 20]
        yield "done", {"completion_tokens": 42}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)


def _gerar(conv: int, pedido: str) -> dict:
    async def main():
        msg = design.start(conv, pedido, "ollama", "qwen3:8b")
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return design.estado(msg["id"])
    return asyncio.run(main())


def test_extrai_documento_de_resposta_tagarela():
    resp = "<think>hmm</think>Claro! ```html\n" + DOC.replace("{}", "oi") + "\n``` Pronto."
    assert design.extrair_html(resp) == DOC.replace("{}", "oi")
    assert design.extrair_html(DOC.replace("{}", "x")[:-7]) is None      # truncado: sem </html>
    assert design.extrair_html("<html><head></head></html>") is None      # sem <body>
    assert design.extrair_html("nada aqui") is None


def test_versoes_desfazer_refazer_restaurar(monkeypatch):
    conv = _projeto()
    vistos: list = []
    _fake_llm(monkeypatch, DOC.replace("{}", "Padaria"), vistos)
    assert _gerar(conv, "landing page de uma padaria")["versao"] == 1
    _fake_llm(monkeypatch, DOC.replace("{}", "Padaria azul"), vistos)
    assert _gerar(conv, "deixa azul")["versao"] == 2
    assert "Padaria</section>" in vistos[1][1]["content"]   # a edição parte da versão atual

    p = design.projeto(conv)
    assert (p["atual"], p["total"], "azul" in p["html"]) == (2, 2, True)
    assert p["titulo"] == "landing page de uma padaria"
    assert [m["role"] for m in p["mensagens"]] == ["user", "assistant"] * 2

    p = design.ir_para(conv, 1)           # desfazer
    assert (p["atual"], "azul" in p["html"]) == (1, False)
    assert design.ir_para(conv, 2)["atual"] == 2   # refazer
    p = design.restaurar(conv, 1)         # restaurar cria versão nova
    assert (p["atual"], p["total"], "azul" in p["html"]) == (3, 3, False)
    with pytest.raises(ToolError):
        design.ir_para(conv, 9)


@pytest.mark.parametrize("resposta", ["desculpe, não consigo", RuntimeError("caiu")])
def test_falha_nao_altera_versao_atual(monkeypatch, resposta):
    conv = _projeto()
    _fake_llm(monkeypatch, DOC.replace("{}", "ok"))
    _gerar(conv, "primeira")
    _fake_llm(monkeypatch, resposta)
    est = _gerar(conv, "segunda")
    assert est["status"] == "erro" and est["versao"] is None
    p = design.projeto(conv)
    assert (p["atual"], p["total"], p["rodando"]) == (1, 1, None)
    assert "ok" in p["html"]


def test_cancelar_nao_cria_versao(monkeypatch):
    conv = _projeto()

    async def lento(*a, **k):
        for _ in range(100):
            await asyncio.sleep(0.01)
            yield "content", "<html>"

    monkeypatch.setattr(design.llm, "chat_stream", lento)

    async def main():
        msg = design.start(conv, "algo", "ollama", "m")
        await asyncio.sleep(0.05)
        design.cancelar(msg["id"])
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return design.estado(msg["id"])

    assert asyncio.run(main())["status"] == "cancelado"
    assert design.projeto(conv)["total"] == 0


def test_projeto_de_outro_tipo_recusa():
    with db.session() as s:
        c = db.Conversation(kind="chat")
        s.add(c)
        s.commit()
        cid = c.id
    with pytest.raises(ToolError):
        design.projeto(cid)

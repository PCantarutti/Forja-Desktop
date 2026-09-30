"""'Remova essa div' vira patch vazio que apaga o elemento; e a IA local sem modelo no ar sobe o escolhido."""
import json
import re

from app import design, design_html
from tests.test_design_conversa import DOC, M, _esperar, _fake, _projeto, _ultima, pastas  # noqa: F401


def test_patch_vazio_remove_o_elemento():
    html = design_html.carimbar(DOC)
    fid = re.search(r'<a class="cta" data-fid="(\w+)"', html).group(1)
    novo, mudou = design_html.aplicar(html, {"patches": [{"fid": fid, "html": ""}]})
    assert "Comprar" not in novo and "<h1" in novo and mudou == []   # sem fid para mandar: o canvas recarrega


def test_comentario_remova_aplica_a_remocao(monkeypatch):
    conv, vistos = _projeto(), []
    html = design.projeto(conv)["html"]
    fid = re.search(r'<a class="cta" data-fid="(\w+)"', html).group(1)
    _fake(monkeypatch, lambda tipo, user: json.dumps({"patches": [{"fid": fid, "html": ""}], "mensagem": "Removi o botão."}), vistos)
    _esperar(lambda: design.start(conv, "Remova esse botão", M, fids=[fid]))
    assert _ultima(conv)["status"] == "ok" and "Comprar" not in design.projeto(conv)["html"]


def test_ia_local_sem_modelo_carrega_o_escolhido(monkeypatch):
    from app import localai
    carregados = []
    monkeypatch.setattr(design.llm, "spec", lambda p: {"type": "llamacpp"})
    monkeypatch.setattr(localai, "status", lambda: {"running": False, "loading": {}})
    monkeypatch.setattr(localai, "scan", lambda *a: [{"kind": "chat", "path": r"D:\m\Qwen-X.gguf", "name": "Qwen-X"}])
    monkeypatch.setattr(localai, "load", lambda path, *a, **k: carregados.append(path))
    run = {"spec": {"provider": "local", "model": "Qwen-X"}}
    import asyncio
    asyncio.run(design._garantir_local(run))
    assert carregados == [r"D:\m\Qwen-X.gguf"] and "Carregou o modelo local Qwen-X" in run["passos"][0]
    # com um modelo já no ar, não troca
    monkeypatch.setattr(localai, "status", lambda: {"running": True})
    asyncio.run(design._garantir_local(run))
    assert len(carregados) == 1


def test_nome_comum_da_parte_vai_para_a_secao_certa():
    secoes = ["topo", "hero", "produtos", "precos", "faq", "rodape"]
    assert design.rotear("e quanto ao menu hamburguer?", True, secoes) == ("secao", "topo")
    assert design.rotear("o footer está muito alto", True, secoes) == ("secao", "rodape")
    assert design.rotear("acrescente uma pergunta sobre prazo", True, secoes) == ("secao", "faq")
    assert design.rotear("refaça o site inteiro do zero", True, secoes)[0] == "documento"

"""Modo multi-modelo (esforço extremo): brief com arquivos, verificação objetiva e rede de segurança.

Os testes chamam `subagents.run` direto, passando um `run_call` de mentira no lugar do laço do agente
— é nele que a delegação executa as ferramentas do subagente e, agora, o `done_when`.
"""
import asyncio

import pytest

from app import agent, config, llm, settings, subagents, workspace
from app.tools import REGISTRY


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    pasta = tmp_path / "projeto"
    pasta.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_ROOT", pasta)
    workspace.CURRENT.set(pasta)

    async def nada(*a):
        return None

    monkeypatch.setattr(llm, "capabilities", nada)
    settings.reset()
    yield pasta
    settings.reset()
    workspace.CURRENT.set(None)


def _slots(**pares):
    config.SUBAGENTS = {lvl: {"provider": p, "model": m} for lvl, (p, m) in pares.items()}


async def _ok_run_call(_conv, call, _req, _run, _caps, out, parent=None):
    out.update(status="ok", text=f"[{call['name']}] ok", meta={})
    return
    yield  # pragma: no cover  (só para a função ser geradora assíncrona)


def _delegate(args, *, effort="medio", run_call=_ok_run_call, permission="manual"):
    """Roda uma delegação inteira e devolve (out, eventos)."""
    run_obj = agent.Run(1)
    run_obj.permission = permission
    req = agent.RunRequest(content="x", provider="lmstudio", model="main", mode="agent",
                           permission=permission, effort=effort)
    out: dict = {}

    async def scenario():
        eventos = []
        async for ev in subagents.run(1, {"id": "d1", "arguments": args}, req, run_obj, out, run_call):
            eventos.append(ev)
        return eventos

    return out, asyncio.run(scenario())


def _fala(texto="pronto", **por_modelo):
    """chat_stream de mentira: cada modelo responde o que foi combinado (str ou Exception)."""
    vistos = []

    async def fake(provider, model, messages, tools, num_ctx, effort=None):
        vistos.append({"model": model, "messages": messages, "effort": effort})
        resposta = por_modelo.get(model, texto)
        if isinstance(resposta, Exception):
            raise resposta
        yield "content", resposta
        yield "done", {"tool_calls": []}

    return fake, vistos


# ------------------------------------------------ rede de segurança: modelo local

class _LocalAi:
    def __init__(self, **estado):
        self._estado = estado

    def status(self):
        return self._estado


def test_slot_local_com_outro_modelo_carregado_sai_da_cadeia(monkeypatch):
    """O Forja sobe um llama-server por vez e o llama.cpp ignora o campo `model`: pedir outro alias
    rodaria o modelo errado calado, então esse slot não existe agora."""
    monkeypatch.setitem(config.PROVIDERS, "local", {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    monkeypatch.setattr(subagents, "localai", _LocalAi(running=True, alias="qwen-8b"))
    _slots(capaz=("local", "qwen-30b"), nuvem=("lmstudio", "grande"))
    assert [lvl for lvl, _ in subagents.chain("capaz")] == ["nuvem"]
    assert "qwen-8b" in subagents._why_not()


def test_slot_local_com_o_modelo_certo_fica(monkeypatch):
    monkeypatch.setitem(config.PROVIDERS, "local", {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    monkeypatch.setattr(subagents, "localai", _LocalAi(running=True, alias="qwen-8b"))
    _slots(capaz=("local", "qwen-8b"))
    assert [lvl for lvl, _ in subagents.chain("capaz")] == ["capaz"]


def test_sem_localai_nada_e_barrado(monkeypatch):
    """No forja-web não existe modelo local embutido: a checagem simplesmente não se aplica."""
    monkeypatch.setitem(config.PROVIDERS, "local", {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    monkeypatch.setattr(subagents, "localai", None)
    _slots(capaz=("local", "qualquer"))
    assert [lvl for lvl, _ in subagents.chain("capaz")] == ["capaz"]


def test_cadeia_vazia_manda_o_principal_fazer_sozinho(monkeypatch):
    monkeypatch.setitem(config.PROVIDERS, "local", {"id": "local", "type": "llamacpp", "url": "", "api_key": ""})
    monkeypatch.setattr(subagents, "localai", _LocalAi(running=False))
    _slots(capaz=("local", "qwen-30b"))
    out, _ = _delegate({"task": "faz algo", "level": "capaz"})
    assert out["status"] == "erro"
    assert "nenhum modelo local" in out["text"] and "você mesmo" in out["text"]


# ------------------------------------------------ fallback por falha do provedor

def test_erro_de_llm_cai_para_o_proximo_slot(monkeypatch):
    _slots(capaz=("lmstudio", "grande"), nuvem=("lmstudio", "reserva"))
    fake, vistos = _fala(grande=llm.LLMError("sem memória"), reserva="fiz")
    monkeypatch.setattr(llm, "chat_stream", fake)
    out, _ = _delegate({"task": "faz algo", "level": "capaz"})
    assert out["status"] == "ok" and "fiz" in out["text"]
    assert [v["model"] for v in vistos] == ["grande", "reserva"]
    assert "reserva" in out["meta"]["sub"]["fallback"]


def test_erro_depois_de_escrever_arquivo_nao_troca_de_modelo(monkeypatch):
    """Trocar de modelo depois que o sub já mexeu no disco repetiria o efeito colateral."""
    _slots(capaz=("lmstudio", "grande"), nuvem=("lmstudio", "reserva"))
    passos = {"n": 0}

    async def fake(provider, model, messages, tools, num_ctx, effort=None):
        passos["n"] += 1
        if passos["n"] == 1:
            yield "done", {"tool_calls": [{"id": "s1", "name": "write_file",
                                           "arguments": {"path": "a.txt", "content": "oi"}}]}
        else:
            raise llm.LLMError("caiu")

    monkeypatch.setattr(llm, "chat_stream", fake)
    out, _ = _delegate({"task": "faz algo", "level": "capaz"})
    assert out["status"] == "erro" and "caiu" in out["text"]


# ------------------------------------------------ brief

def test_extremo_recusa_delegacao_sem_contexto(monkeypatch):
    _slots(capaz=("lmstudio", "grande"))
    fake, _ = _fala()
    monkeypatch.setattr(llm, "chat_stream", fake)
    out, _ = _delegate({"task": "conserta o bug", "level": "capaz"}, effort="extremo")
    assert out["status"] == "erro"
    assert "files" in out["text"] and "done_when" in out["text"]


def test_conteudo_dos_arquivos_vai_junto_com_a_tarefa(monkeypatch, clean):
    (clean / "alvo.py").write_text("valor = 42\n", encoding="utf-8")
    _slots(capaz=("lmstudio", "grande"))
    fake, vistos = _fala()
    monkeypatch.setattr(llm, "chat_stream", fake)
    out, _ = _delegate({"task": "usa o arquivo", "level": "capaz", "files": "alvo.py, some.py"})
    brief = vistos[0]["messages"][1]["content"]
    assert "valor = 42" in brief and "alvo.py" in brief
    assert "não consegui ler" in brief  # o que não existe vira nota, não aborta
    assert out["status"] == "ok"


def test_subagente_nao_herda_o_esforco_extremo(monkeypatch):
    """Ele não tem delegate_task; mandar 'delegue o difícil' seria instrução morta."""
    _slots(capaz=("lmstudio", "grande"))
    fake, vistos = _fala()
    monkeypatch.setattr(llm, "chat_stream", fake)
    _delegate({"task": "t" * 200, "level": "capaz", "files": ["x.py"]}, effort="extremo")
    assert vistos[0]["effort"] == "maximo"
    assert "ESFORÇO EXTREMO" not in vistos[0]["messages"][0]["content"]


# ------------------------------------------------ verificação

def test_done_when_roda_pelo_caminho_de_aprovacao(monkeypatch):
    _slots(capaz=("lmstudio", "grande"))
    fake, _ = _fala("terminei")
    monkeypatch.setattr(llm, "chat_stream", fake)
    feitos = []

    async def run_call(_conv, call, _req, _run, _caps, out, parent=None):
        feitos.append((call["name"], call["arguments"], parent))
        out.update(status="ok", text="exit code: 0\n2 passed", meta={})
        return
        yield  # pragma: no cover

    out, _ = _delegate({"task": "t" * 200, "level": "capaz", "files": ["x.py"], "done_when": "pytest -q"},
                       effort="extremo", run_call=run_call)
    assert feitos == [("run_command", {"command": "pytest -q", "timeout": subagents.VERIFY_TIMEOUT}, "d1")]
    assert "Verificação `pytest -q`: PASSOU" in out["text"]
    assert out["meta"]["sub"]["verify"] == {"command": "pytest -q", "status": "ok"}


def test_verificacao_reprovada_aparece_no_relatorio(monkeypatch):
    _slots(capaz=("lmstudio", "grande"))
    fake, _ = _fala("terminei")
    monkeypatch.setattr(llm, "chat_stream", fake)

    async def run_call(_conv, call, _req, _run, _caps, out, parent=None):
        out.update(status="erro", text="exit code: 1\nFAILED test_x", meta={})
        return
        yield  # pragma: no cover

    out, _ = _delegate({"task": "t" * 200, "level": "capaz", "files": ["x.py"], "done_when": "pytest -q"},
                       effort="extremo", run_call=run_call)
    assert "FALHOU" in out["text"] and "FAILED test_x" in out["text"]


def test_modo_plano_nao_roda_verificacao(monkeypatch):
    _slots(capaz=("lmstudio", "grande"))
    fake, _ = _fala("terminei")
    monkeypatch.setattr(llm, "chat_stream", fake)
    feitos = []

    async def run_call(_conv, call, _req, _run, _caps, out, parent=None):
        feitos.append(call["name"])
        out.update(status="ok", text="ok", meta={})
        return
        yield  # pragma: no cover

    out, _ = _delegate({"task": "t" * 200, "level": "capaz", "files": ["x.py"], "done_when": "pytest -q"},
                       effort="extremo", run_call=run_call, permission="plan")
    assert feitos == [] and "Verificação" not in out["text"]


# ------------------------------------------------ configuração e esforço

def test_slot_nuvem_e_configuravel():
    settings.update({"subagents": {"nuvem": {"provider": "lmstudio", "model": "grande"}}})
    assert config.SUBAGENTS["nuvem"] == {"provider": "lmstudio", "model": "grande"}
    with pytest.raises(settings.SettingsError, match="não existe"):
        settings.update({"subagents": {"nuvem": {"provider": "nada", "model": "x"}}})


def test_nuvem_nao_e_escolha_do_modelo():
    """Se entrasse no enum, modelo pequeno escolheria a nuvem no chute — e isso custa dinheiro."""
    enum = REGISTRY["delegate_task"].parameters["properties"]["level"]["enum"]
    assert enum == ["rapido", "capaz"] and "nuvem" in subagents.LEVELS


def test_extremo_e_o_esforco_mais_longo():
    assert agent.effort_iterations("extremo") > agent.effort_iterations("maximo")
    assert llm.EFFORT_LEVEL["extremo"] == "high"

"""Modo multi-modelo (esforço extremo): brief com arquivos, verificação objetiva e rede de segurança.

Os testes chamam `subagents.run` direto, passando um `run_call` de mentira no lugar do laço do agente
— é nele que a delegação executa as ferramentas do subagente e, agora, o `done_when`.
"""
import asyncio

import httpx
import pytest

from app import agent, config, db, llm, settings, subagents, workspace
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

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
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

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
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


def test_no_extremo_o_maestro_pensa_pouco_e_o_subagente_muito(monkeypatch):
    """O maestro raciocina o mínimo para montar o pedido; quem resolve é o subagente, que herda
    'maximo'. Quem realmente segura o tratado é o REASONING_CAP: o modelo local ignora as duas
    chaves abaixo."""
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    corpo, msgs = {}, [{"role": "system", "content": "regras"}]
    asyncio.run(llm._reasoning("lm", "gpt-oss:120b", "extremo", corpo, msgs))
    assert corpo["reasoning_effort"] == "low"

    corpo, msgs = {}, [{"role": "system", "content": "regras"}]
    asyncio.run(llm._reasoning("lm", "gpt-oss:120b", "maximo", corpo, msgs))
    assert corpo["reasoning_effort"] == "high"


def test_banco_antigo_sem_o_slot_nuvem_nao_quebra():
    """A linha salva substitui a chave inteira; sem preencher o que falta, a tela de Subagentes
    lia um slot inexistente e apagava a interface."""
    with db.session() as s:
        s.merge(db.AppSetting(key="subagents", value={"rapido": {"provider": "lmstudio", "model": "mini"},
                                                      "capaz": {"provider": "", "model": ""}}))
        s.commit()
    valores = settings.apply()
    assert valores["subagents"]["nuvem"] == {"provider": "", "model": ""}
    assert settings.public()["subagents"]["nuvem"] == {"provider": "", "model": ""}
    assert config.SUBAGENTS["rapido"]["model"] == "mini"


# ------------------------------------------------ freio: o principal integra, não implementa

def test_freio_devolve_escrita_grande_uma_vez():
    _slots(capaz=("lmstudio", "grande"))
    avisados: set[str] = set()
    args = {"path": "x.py", "content": "linha\n" * 20}
    aviso = subagents.nudge_write("write_file", args, avisados)
    # a primeira frase precisa dizer que nada foi gravado, senao ele vai testar um arquivo vazio
    assert aviso.startswith("NADA FOI ESCRITO em x.py") and "delegate_task" in aviso
    # na segunda tentativa passa: travar o turno seria pior que deixar o principal fazer
    assert subagents.nudge_write("write_file", args, avisados) == ""


def test_freio_pega_edit_file_pelo_new_str():
    _slots(capaz=("lmstudio", "grande"))
    args = {"path": "x.py", "old_str": "a", "new_str": "nova\n" * 20}
    assert subagents.nudge_write("edit_file", args, set())


def test_freio_deixa_passar_o_trivial_e_a_leitura():
    _slots(capaz=("lmstudio", "grande"))
    assert subagents.nudge_write("write_file", {"path": "x.py", "content": "a\nb\n"}, set()) == ""
    assert subagents.nudge_write("read_file", {"path": "x.py"}, set()) == ""


def test_sem_subagente_disponivel_o_freio_nao_existe():
    """Sem para quem delegar, segurar a escrita só travaria o turno."""
    config.SUBAGENTS = {}
    assert subagents.nudge_write("write_file", {"path": "x.py", "content": "l\n" * 30}, set()) == ""


# ------------------------------------------------ aba Instâncias

def test_delegacao_aparece_entre_as_ativas_e_some_no_fim(monkeypatch):
    _slots(capaz=("lmstudio", "grande"))
    durante = []

    async def fake(provider, model, messages, tools, num_ctx, effort=None, **kw):
        durante.append(subagents.ativas())
        yield "content", "pronto"
        yield "done", {"tool_calls": []}

    monkeypatch.setattr(llm, "chat_stream", fake)
    _delegate({"task": "faz algo", "level": "capaz"})
    assert len(durante[0]) == 1
    ativa = durante[0][0]
    assert ativa["model"] == "grande" and ativa["conversation_id"] == 1 and ativa["status"]
    assert subagents.ativas() == []


def test_ativa_some_quando_o_turno_e_abandonado(monkeypatch):
    """Cancelar fecha o gerador no meio; sem o finally a delegação ficaria eterna na aba."""
    _slots(capaz=("lmstudio", "grande"))
    fake, _ = _fala()
    monkeypatch.setattr(llm, "chat_stream", fake)

    async def scenario():
        run_obj = agent.Run(1)
        req = agent.RunRequest(content="x", provider="lmstudio", model="main")
        gen = subagents.run(1, {"id": "d1", "arguments": {"task": "t", "level": "capaz"}},
                            req, run_obj, {}, _ok_run_call)
        await gen.__anext__()  # primeiro sub_status: já entrou na lista
        dentro = len(subagents.ativas())
        await gen.aclose()
        return dentro, len(subagents.ativas())

    dentro, depois = asyncio.run(scenario())
    assert (dentro, depois) == (1, 0)


def test_endpoint_das_ativas():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        r = c.get("/api/subagents/active")
        assert r.status_code == 200 and r.json() == {"subagents": []}


# ------------------------------------------------ revisão: só onde não há medição

def _com_revisao_falsa(monkeypatch):
    chamadas = []

    async def fake_review(_root, task, _paths):
        chamadas.append(task)
        return "nano", "VEREDITO: ajustar"

    monkeypatch.setattr(subagents, "_review", fake_review)
    return chamadas


def _verificacao(status: str, texto: str):
    async def run_call(_conv, _call, _req, _run, _caps, out, parent=None):
        out.update(status=status, text=texto, meta={})
        return
        yield  # pragma: no cover

    return run_call


def test_verificacao_passando_dispensa_a_revisao(monkeypatch):
    """Com exit code 0 na mão, parecer de modelo menor que o autor só gera falso-positivo."""
    _slots(capaz=("lmstudio", "grande"))
    fake, _ = _fala("terminei")
    monkeypatch.setattr(llm, "chat_stream", fake)
    chamadas = _com_revisao_falsa(monkeypatch)
    out, _ = _delegate({"task": "t" * 200, "level": "capaz", "files": ["x.py"], "done_when": "pytest -q"},
                       effort="extremo", run_call=_verificacao("ok", "exit code: 0"))
    assert chamadas == [] and "Revisão do diff" not in out["text"]
    assert "PASSOU" in out["text"]


def test_verificacao_reprovada_pede_revisao(monkeypatch):
    _slots(capaz=("lmstudio", "grande"))
    fake, _ = _fala("terminei")
    monkeypatch.setattr(llm, "chat_stream", fake)
    chamadas = _com_revisao_falsa(monkeypatch)
    out, _ = _delegate({"task": "t" * 200, "level": "capaz", "files": ["x.py"], "done_when": "pytest -q"},
                       effort="extremo", run_call=_verificacao("erro", "exit code: 1"))
    assert len(chamadas) == 1 and "VEREDITO: ajustar" in out["text"]


def test_sem_done_when_a_revisao_e_a_unica_opiniao(monkeypatch):
    _slots(capaz=("lmstudio", "grande"))
    fake, _ = _fala("terminei")
    monkeypatch.setattr(llm, "chat_stream", fake)
    chamadas = _com_revisao_falsa(monkeypatch)
    out, _ = _delegate({"task": "t" * 200, "level": "capaz", "files": ["x.py"]}, effort="extremo")
    assert len(chamadas) == 1 and "VEREDITO: ajustar" in out["text"]


# ------------------------------------------------ cota do Ollama Cloud

def test_is_cloud_exige_nuvem_da_ollama_com_chave(monkeypatch):
    monkeypatch.setitem(config.PROVIDERS, "nuvem1",
                        {"id": "nuvem1", "name": "N", "type": "ollama", "url": "https://ollama.com/v1", "api_key": "k"})
    monkeypatch.setitem(config.PROVIDERS, "semchave",
                        {"id": "semchave", "name": "N", "type": "ollama", "url": "https://ollama.com/v1", "api_key": ""})
    monkeypatch.setitem(config.PROVIDERS, "local2",
                        {"id": "local2", "name": "L", "type": "ollama", "url": "http://127.0.0.1:11434/v1", "api_key": ""})
    assert llm.is_cloud("nuvem1")
    assert not llm.is_cloud("semchave") and not llm.is_cloud("local2") and not llm.is_cloud("nada")


def test_limites_aceita_o_formato_gratis_e_o_pago():
    """Plano grátis devolve monthly + lista; o pago, session/weekly e (em algumas versões) objeto."""
    gratis = {"monthly": {"usage": 0.135, "models": [{"name": "gpt-oss:120b", "request_count": 220},
                                                     {"name": "gemma4:31b", "request_count": 50}]}}
    limites, modelos = llm._limites(gratis)
    assert limites == [{"name": "monthly", "usage": 0.135}]
    assert [m["name"] for m in modelos] == ["gpt-oss:120b", "gemma4:31b"]

    pago = {"weekly": {"usage": 0.58, "models": {"a": {"request_count": 3}, "b": {"request_count": 9}}},
            "session": {"usage": 0.19, "models": {}}}
    limites, modelos = llm._limites(pago)
    assert [l["name"] for l in limites] == ["session", "weekly"]          # sessão primeiro, como na tela
    assert [m["name"] for m in modelos] == ["b", "a"]                     # mais requisições primeiro


def test_usage_nao_levanta_quando_o_endpoint_some(monkeypatch):
    """O /api/usage não é documentado: se sumir, a interface só não mostra a cota."""
    monkeypatch.setitem(config.PROVIDERS, "nuvem2",
                        {"id": "nuvem2", "name": "N", "type": "ollama", "url": "https://ollama.com/v1", "api_key": "k"})
    llm._USAGE.clear()

    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeClient)
    assert asyncio.run(llm.usage("nuvem2")) is None


# ------------------------------------------------ teto de raciocínio

CAP = llm.REASONING_CAP["medio"][0]   # 4000 caracteres; o teto de relógio fica frouxo nestes testes
TETO = (CAP, 999)

def _impl_falso(registro: list, raciocinio: int):
    """impl de mentira: gera `raciocinio` caracteres de pensamento e depois responde."""
    async def impl(provider, model, messages, tools, num_ctx, extra):
        registro.append(extra)
        pensa = (extra.get("chat_template_kwargs") or {}).get("enable_thinking", True)
        for _ in range(raciocinio // 100 if pensa else 0):
            yield "reasoning", "p" * 100
        yield "content", f"resposta {len(registro)}"
        yield "done", {"tool_calls": []}
    return impl


async def _sem_capacidades(provider, model):
    return set()


def _consome(gen):
    async def tudo():
        return [ev async for ev in gen]
    return asyncio.run(tudo())


def test_maestro_pensando_demais_e_cortado_e_refeito_sem_pensar(monkeypatch):
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    chamadas: list = []
    impl = _impl_falso(chamadas, CAP * 2)
    eventos = _consome(llm._capped(impl, "lm", "qwen", [], None, 8192, {}, TETO))
    assert len(chamadas) == 2
    assert chamadas[1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert ("content", "resposta 2") in eventos          # a resposta boa é a da segunda chamada
    assert sum(len(v) for k, v in eventos if k == "reasoning") < CAP * 1.5


def test_raciocinio_curto_passa_inteiro(monkeypatch):
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    chamadas: list = []
    impl = _impl_falso(chamadas, 500)
    eventos = _consome(llm._capped(impl, "lm", "qwen", [], None, 8192, {}, TETO))
    assert len(chamadas) == 1 and ("content", "resposta 1") in eventos


def test_o_corte_manda_todos_os_interruptores(monkeypatch):
    """Não existe um interruptor só: o que a família do modelo não entende é ignorado sem erro."""
    monkeypatch.setitem(config.PROVIDERS, "oll",
                        {"id": "oll", "name": "O", "type": "ollama", "url": "http://x/v1", "api_key": ""})
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    sistema = [{"role": "system", "content": "regras"}]
    assert llm._sem_pensar("oll", {}, sistema) == ({"think": False}, sistema)

    corpo, msgs = llm._sem_pensar("lm", {}, sistema)
    assert corpo == {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_budget": 0}
    assert msgs[0]["content"].endswith("/no_think") and sistema[0]["content"] == "regras"  # sem mutar o original


def test_teto_pega_pensamento_que_vem_no_proprio_texto(monkeypatch):
    """Servidor que não separa o canal manda <think> junto do conteúdo; o teto tem que valer igual."""
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    chamadas: list = []

    async def impl(provider, model, messages, tools, num_ctx, extra):
        chamadas.append(extra)
        if (extra.get("chat_template_kwargs") or {}).get("enable_thinking", True):
            yield "content", "<think>"
            for _ in range(CAP // 100 + 2):
                yield "content", "p" * 100
        yield "content", "resposta final"
        yield "done", {"tool_calls": []}

    eventos = _consome(llm._capped(impl, "lm", "qwen", [{"role": "system", "content": "s"}], None, 8192, {}, TETO))
    assert len(chamadas) == 2 and ("content", "resposta final") in eventos


def test_teto_nativo_vai_no_corpo_da_requisicao(monkeypatch):
    """O caminho bom: o servidor corta sozinho, na mesma geração. O `_capped` é só a rede."""
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    corpo: dict = {}
    asyncio.run(llm._reasoning("lm", "qwen", "medio", corpo, []))
    assert corpo["reasoning_budget"] == CAP // 4          # o mesmo teto, em tokens

    folgado: dict = {}
    asyncio.run(llm._reasoning("lm", "qwen", "medio", folgado, [], subagents.SUB_CAP_MULT))
    assert folgado["reasoning_budget"] > corpo["reasoning_budget"]


def test_teto_nativo_nao_vai_para_quem_nao_entende(monkeypatch):
    """`openai` genérico devolveria 400; no Ollama o interruptor é `think`, não um orçamento."""
    monkeypatch.setitem(config.PROVIDERS, "gen",
                        {"id": "gen", "name": "G", "type": "openai", "url": "http://x/v1", "api_key": ""})
    monkeypatch.setitem(config.PROVIDERS, "oll",
                        {"id": "oll", "name": "O", "type": "ollama", "url": "http://x/v1", "api_key": ""})
    monkeypatch.setattr(llm, "capabilities", _sem_capacidades)
    for provider in ("gen", "oll"):
        corpo: dict = {}
        asyncio.run(llm._reasoning(provider, "qwen", "medio", corpo, []))
        assert "reasoning_budget" not in corpo


@pytest.mark.skipif(not hasattr(llm, "_inference"), reason="a tela Inferência existe só no desktop")
def test_ajuste_salvo_do_modelo_manda_mais_que_o_teto_nativo(monkeypatch):
    """Quem mexeu na tela Inferência decidiu; o padrão do esforço não pode passar por cima."""
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    monkeypatch.setattr(llm.db, "get_model_setting",
                        lambda m: {"inference": {"reasoning_budget": 99}, "tool_mode": "auto", "vision": "auto"})
    corpo: dict = {}
    asyncio.run(llm._reasoning("lm", "qwen", "medio", corpo, []))
    llm._inference("lm", "qwen", corpo)
    assert corpo["reasoning_budget"] == 99


def test_raciocinio_cortado_nao_volta_como_entrada(monkeypatch):
    """Re-prefillar o tratado que acabou de ser rejeitado gasta contexto e convida a continuar o loop."""
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    vistos: list = []

    async def impl(provider, model, messages, tools, num_ctx, extra):
        vistos.append(messages)
        if (extra.get("chat_template_kwargs") or {}).get("enable_thinking", True):
            for _ in range(CAP // 100 + 2):
                yield "reasoning", "blá " * 25
        yield "content", "resposta final"
        yield "done", {"tool_calls": []}

    original = [{"role": "system", "content": "s"}, {"role": "user", "content": "pergunta"}]
    _consome(llm._capped(impl, "lm", "qwen", original, None, 8192, {}, TETO))
    assert len(vistos) == 2
    assert [m["role"] for m in vistos[1]] == ["system", "user"]   # nada foi acrescentado
    assert not any("blá" in m["content"] for m in vistos[1])
    assert len(original) == 2 and original[0]["content"] == "s"   # sem mutar a lista de quem chamou


def test_teto_tambem_e_de_relogio(monkeypatch):
    """Modelo local lento gasta minutos gerando poucos caracteres: quem segura é o cronômetro."""
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    chamadas: list = []

    async def impl(provider, model, messages, tools, num_ctx, extra):
        chamadas.append(extra)
        if (extra.get("chat_template_kwargs") or {}).get("enable_thinking", True):
            for _ in range(50):
                await asyncio.sleep(0.01)
                yield "reasoning", "p"      # devagar e curtinho: nunca chegaria no teto de caracteres
        yield "content", "resposta final"
        yield "done", {"tool_calls": []}

    eventos = _consome(llm._capped(impl, "lm", "qwen", [], None, 8192, {}, (CAP, 0.05)))
    assert len(chamadas) == 2 and ("content", "resposta final") in eventos
    assert sum(len(v) for k, v in eventos if k == "reasoning") < CAP


def test_teto_vale_em_todo_esforco_nao_so_no_extremo(monkeypatch):
    """A regressão que motivou isto: no Médio o raciocínio não tinha limite nenhum."""
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    chamadas: list = []
    monkeypatch.setattr(llm, "_openai_stream", _impl_falso(chamadas, CAP * 2))
    eventos = _consome(llm.chat_stream("lm", "qwen", [], None, 8192, "medio"))
    assert len(chamadas) == 2 and ("content", "resposta 2") in eventos


def test_chamada_mecanica_nao_pensa(monkeypatch):
    """Compactar, titular e escrever mensagem de commit não precisam de raciocínio nenhum."""
    monkeypatch.setitem(config.PROVIDERS, "lm",
                        {"id": "lm", "name": "LM", "type": "lmstudio", "url": "http://x/v1", "api_key": ""})
    chamadas: list = []
    monkeypatch.setattr(llm, "_openai_stream", _impl_falso(chamadas, CAP * 2))
    eventos = _consome(llm.chat_stream("lm", "qwen", [{"role": "system", "content": "s"}], None, 8192,
                                       "baixo", think=False))
    assert len(chamadas) == 1                            # uma chamada só: não pensou, não teve o que cortar
    assert chamadas[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert ("content", "resposta 1") in eventos


def test_subagente_pensa_mais_folgado_que_o_maestro():
    """Quem resolve a tarefa é ele; cortar o sub no mesmo ponto do maestro devolveria relatório vazio."""
    assert llm._cap("medio", subagents.SUB_CAP_MULT) > llm._cap("medio", 1.0)
    assert llm._cap("maximo", 1.0) > llm._cap("medio", 1.0)     # o teto cresce com o esforço


def test_knob_de_configuracao_afrouxa_o_teto(monkeypatch):
    monkeypatch.setattr(config, "REASONING_CAP_MULT", 2.0)
    assert llm._cap("medio", 1.0) == (CAP * 2, llm.REASONING_CAP["medio"][1] * 2)

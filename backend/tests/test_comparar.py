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

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
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


# ------------------------------------------------------------------ baterias por especialidade e juiz

def test_bateria_de_documentos_manda_o_arquivo_aos_modelos_e_nao_na_mensagem(monkeypatch):
    from app import baterias
    vistos = []

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(messages)
        yield ("content", "resumo")
        yield ("done", {"prompt_tokens": 10, "completion_tokens": 5})

    async def context_limit(*a, **k):
        return 8192

    monkeypatch.setattr(comparar.llm, "chat_stream", chat_stream)
    monkeypatch.setattr(comparar.llm, "context_limit", context_limit)
    conv = _conversa()
    prompt = baterias.BATERIAS["docs"]["prompt"]
    _rodar(conv_id=conv, prompt=prompt, bateria="docs",
           itens=[{"provider": "p", "model": "a"}, {"provider": "p", "model": "b"}])
    enviado = vistos[0][-1]["content"]
    assert "politica-de-reembolso.md" in enviado and "Aurora Viagens" in enviado
    assert vistos[0][0]["role"] == "system"  # a bateria põe o system de "não invente"
    with db.session() as s:
        user = s.query(db.Message).filter_by(conversation_id=conv, role="user").one()
        assert "Aurora Viagens" not in user.content and user.meta["bateria"] == "docs"
    with pytest.raises(ToolError, match="desconhecido"):
        _rodar(conv_id=conv, prompt="x", bateria="nada", itens=[{"provider": "p", "model": "a"}, {"provider": "p", "model": "b"}])


def test_juiz_recebe_gabarito_e_estatisticas_e_responde_como_chat(monkeypatch):
    from app import baterias, modelctl
    _fake_llm(monkeypatch)
    conv = _conversa()
    estado = _rodar(conv_id=conv, prompt=baterias.BATERIAS["logica"]["prompt"], bateria="logica",
                    itens=[{"provider": "p", "model": "modelo-a"}, {"provider": "p", "model": "modelo-b"}])
    pedidos = []

    async def juiz(provider, model, messages, tools, num_ctx, effort=None, **kw):
        pedidos.append(messages)
        yield ("reasoning", "comparando A e B")
        yield ("content", "| Modelo | Acertou |\n|---|---|\n| A | sim |")
        yield ("done", {})

    async def ensure(spec, *a, **k):
        yield {"type": "model", "phase": "loading"}

    monkeypatch.setattr(baterias.llm, "chat_stream", juiz)
    monkeypatch.setattr(modelctl, "ensure", ensure)

    async def main():
        return [ev async for ev in baterias.julgar(estado["message_id"], "p", "juiz-grande")]

    eventos = asyncio.run(main())
    pedido = pedidos[0][-1]["content"]
    assert "(8200, [(1, 800), (3, 700)])" in pedido                      # o gabarito vai junto
    cabecalhos = [l for l in pedido.splitlines() if l.startswith("=== MODELO")]
    assert len(cabecalhos) == 2 and all("modelo-" not in l for l in cabecalhos)  # só letras: o juiz não vê nomes
    assert "tok/s" in pedido
    fim = eventos[-1]["fim"]
    assert fim["meta"]["julgamento"]["de"] == estado["message_id"]
    assert "| modelo-a | sim |" in fim["content"]      # fora do modo cego: o nome no lugar da letra
    assert fim["thinking"] == "comparando A e B" and {"pensando": "comparando A e B"} in eventos
    passos = [e["etapa"] for e in eventos if "etapa" in e]
    assert any("Carregando juiz-grande" in p for p in passos) and any("lendo 2 respostas" in p for p in passos)


def test_velocidade_conta_o_tempo_do_raciocinio(monkeypatch):
    """Tokens pensados entram no total; o relógio tem de começar neles, não no primeiro texto visível."""
    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        for _ in range(3):
            await asyncio.sleep(0.1)
            yield ("reasoning", "pensando ")
        yield ("content", "ok")
        yield ("done", {"prompt_tokens": 10, "completion_tokens": 40})

    async def context_limit(*a, **k):
        return 8192

    monkeypatch.setattr(comparar.llm, "chat_stream", chat_stream)
    monkeypatch.setattr(comparar.llm, "context_limit", context_limit)
    estado = _rodar(conv_id=_conversa(), prompt="x", itens=[{"provider": "p", "model": "a"}, {"provider": "p", "model": "b"}])
    s = estado["itens"][0]["stats"]
    assert s["tps"] < 200  # 40 tokens em ~0,3 s ≈ 130; com o relógio no texto daria milhares


def test_testar_codigo_html_sobe_servidor_e_python_vira_comando(tmp_path, monkeypatch):
    from app import baterias, shell
    monkeypatch.setattr(baterias, "TESTES_DIR", tmp_path)
    subidos = []
    monkeypatch.setattr(shell, "_start", lambda nome, cmd, cwd: subidos.append((nome, cmd, cwd, shell.CONV.get())))
    monkeypatch.setattr(shell, "list_servers", lambda: [{"name": n, "alive": True} for n, _, _, _ in subidos])
    r = baterias.testar_codigo("<!DOCTYPE html><h1>oi</h1>", "", "7-A", conv=42)
    assert subidos[0][3] == "42" and shell.CONV.get() == ""   # servidor é da conversa; o contexto volta
    assert r["tipo"] == "web" and r["url"].endswith("/index.html")
    assert (tmp_path / "7-A" / "index.html").read_text("utf-8").startswith("<!DOCTYPE")
    assert baterias.testar_codigo("<!DOCTYPE html><h1>de novo</h1>", "html", "7-A")["url"] == r["url"]
    assert len(subidos) == 1                       # mesma resposta: reaproveita o servidor
    from app import native
    assert subidos[0][1].startswith('& "') == native.WINDOWS  # PowerShell: caminho entre aspas só roda com &
    py = baterias.testar_codigo("print('oi')", "python", "7-B")
    assert py["tipo"] == "terminal" and py["comando"].startswith("python ") and "main.py" in py["comando"]
    with pytest.raises(ToolError, match="Não reconheci"):
        baterias.testar_codigo("apenas texto", "", "7-C")


def test_juiz_com_visao_no_teste_de_frontend_recebe_os_prints(monkeypatch, tmp_path):
    from app import baterias, browser, modelctl
    _fake_llm(monkeypatch, textos={"a": ["```html\n<!DOCTYPE html><p>A</p>\n```"], "b": ["sem html"]})
    conv = _conversa()
    estado = _rodar(conv_id=conv, prompt="card", bateria="frontend",
                    itens=[{"provider": "p", "model": "a"}, {"provider": "p", "model": "b"}])
    monkeypatch.setattr(baterias, "testar_codigo", lambda codigo, dica, chave, conv=None: {"url": f"http://x/{chave}"})
    fotos = []

    async def navega(_root, args):
        return "ok"

    async def foto(_root, args):
        fotos.append(args["largura"])
        return {"attachments": [{"kind": "image", "path": str(tmp_path / f"f{len(fotos)}.jpg")}]}

    async def tem_visao(p, m):
        return True

    async def ensure(spec, *a, **k):
        return
        yield

    pedidos = []

    async def juiz(provider, model, messages, tools, num_ctx, effort=None, **kw):
        pedidos.append(messages)
        yield ("content", "tabela")

    monkeypatch.setattr(browser, "navigate", navega)
    monkeypatch.setattr(browser, "screenshot", foto)
    monkeypatch.setattr(baterias, "_tem_visao", tem_visao)
    monkeypatch.setattr(modelctl, "ensure", ensure)
    monkeypatch.setattr(baterias.llm, "chat_stream", juiz)
    from app import uploads
    monkeypatch.setattr(uploads, "user_message", lambda texto, anexos: {"role": "user", "content": texto, "n": len(anexos)})

    async def main():
        return [ev async for ev in baterias.julgar(estado["message_id"], "p", "juiz")]

    asyncio.run(main())
    assert fotos == [1280, 390]                      # só o modelo A entregou HTML: desktop e celular
    ultimo = pedidos[0][-1]
    assert ultimo["n"] == 2 and "MODELO A — desktop" in ultimo["content"] and "MODELO B: não entregou HTML" in ultimo["content"]
    with db.session() as s:
        analise = s.query(db.Message).filter(db.Message.conversation_id == conv).order_by(db.Message.id.desc()).first()
    assert "Prints que o revisor analisou" in analise.content and "/api/files?path=" in analise.content


def test_estatistica_aparece_durante_a_geracao(monkeypatch):
    """O servidor só conta os tokens no fim; enquanto gera, a coluna mostra uma estimativa."""
    _fake_llm(monkeypatch, textos={"a": ["x" * 35] * 6, "b": ["y" * 35] * 6}, pausa=0.05)

    async def main():
        msg = comparar.start(conv_id=_conversa(), prompt="p", itens=[{"provider": "p", "model": "a"},
                                                                      {"provider": "p", "model": "b"}])
        await asyncio.sleep(0.18)
        meio = comparar.estado(msg["id"])["itens"][0]["stats"]
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return meio, comparar.estado(msg["id"])["itens"][0]["stats"]

    meio, fim = asyncio.run(main())
    assert meio["estimated"] and meio["ao_vivo"] and meio["tokens"] > 0
    assert not fim.get("ao_vivo")


def test_dois_prints_no_mesmo_segundo_nao_se_sobrescrevem(tmp_path):
    from app import uploads
    a = uploads.save("browser.jpg", b"desktop", "image/jpeg", root=tmp_path)
    b = uploads.save("browser.jpg", b"mobile", "image/jpeg", root=tmp_path)
    assert a["path"] != b["path"] and (tmp_path / a["path"]).read_bytes() == b"desktop"



def test_resposta_longa_vai_inteira_ao_juiz_e_corte_e_avisado():
    from app import baterias
    itens = [{"rotulo": "A", "nome": "a", "content": "x" * 20000, "stats": None},
             {"rotulo": "B", "nome": "b", "content": "y" * 100, "stats": None}]
    pedido = baterias.pedido_ao_juiz("p", "frontend", itens, baterias.limite_por_resposta(65536, 2))[-1]["content"]
    assert "x" * 20000 in pedido and "cortou" not in pedido          # página inteira cabe
    curto = baterias.pedido_ao_juiz("p", "frontend", itens, 6000)[-1]["content"]
    assert "NÃO conte" in curto                                       # se cortar, o juiz é avisado


def test_com_nomes_troca_letras_so_onde_e_rotulo():
    from app import baterias
    itens = [{"rotulo": "A", "nome": "gemma"}, {"rotulo": "B", "nome": "ornith"}]
    t = baterias.com_nomes("| **A** | sim |\n| B | não |\nVencedor: Modelo B. A nota A+ fica.", itens)
    assert t == "| **gemma** | sim |\n| ornith | não |\nVencedor: ornith. A nota A+ fica."


def test_analise_roda_no_servidor_sem_tela_e_para_quando_pedido(monkeypatch):
    """Trocar de página não pode matar a análise: ela vive numa tarefa do servidor."""
    from app import baterias, modelctl
    _fake_llm(monkeypatch)
    estado = _rodar(conv_id=_conversa(), prompt="p", itens=[{"provider": "p", "model": "a"}, {"provider": "p", "model": "b"}])

    async def ensure(spec, *a, **k):
        return
        yield

    async def lento(provider, model, messages, tools, num_ctx, effort=None, **kw):
        for _ in range(50):
            await asyncio.sleep(0.02)
            yield ("content", "x")

    async def rapido(provider, model, messages, tools, num_ctx, effort=None, **kw):
        yield ("content", "| A | ok |")

    monkeypatch.setattr(modelctl, "ensure", ensure)
    mid = estado["message_id"]

    async def main():
        monkeypatch.setattr(baterias.llm, "chat_stream", rapido)
        baterias.iniciar_analise(mid, "p", "juiz")          # ninguém acompanhando
        await asyncio.sleep(0.2)
        pronto = baterias.estado_analise(mid)
        monkeypatch.setattr(baterias.llm, "chat_stream", lento)
        baterias.iniciar_analise(mid, "p", "juiz")
        await asyncio.sleep(0.15)
        vivo = baterias.estado_analise(mid)
        baterias.parar_analise(mid)
        await asyncio.sleep(0.05)
        return pronto, vivo, baterias.estado_analise(mid)

    pronto, vivo, parado = asyncio.run(main())
    assert pronto["status"] == "pronto" and "| a | ok |" in pronto["texto"] and pronto["fim"]
    assert vivo["status"] == "rodando" and vivo["stats"]["estimated"]
    assert parado["status"] == "parado"


def test_revisor_local_sobe_com_janela_propria_sem_gravar_na_configuracao(monkeypatch):
    """A janela cresce com o que o revisor vai ler; vale só para a carga dele e ele é descarregado no fim."""
    from app import baterias, modelctl
    _fake_llm(monkeypatch, textos={"a": ["x" * 30000], "b": ["y" * 30000]})
    estado = _rodar(conv_id=_conversa(), prompt="p", itens=[{"provider": "p", "model": "a"}, {"provider": "p", "model": "b"}])
    pedidos, descargas = [], []

    async def ensure(spec, out=None, cancel=None, temporario=None):
        pedidos.append(temporario)
        if out is not None:
            out["swapped"] = True
        return
        yield

    async def unload(motivo=""):
        descargas.append(motivo)
        return
        yield

    async def juiz(provider, model, messages, tools, num_ctx, effort=None, **kw):
        yield ("content", "ok")

    monkeypatch.setattr(modelctl, "gerenciavel", lambda spec: True)
    monkeypatch.setattr(modelctl, "ensure", ensure)
    monkeypatch.setattr(modelctl, "unload", unload)
    monkeypatch.setattr(baterias.llm, "chat_stream", juiz)

    async def main():
        return [ev async for ev in baterias.julgar(estado["message_id"], "local", "qwen")]

    eventos = asyncio.run(main())
    assert pedidos == [{"ctx": 32768, "parallel": 1}]      # 60 mil caracteres de respostas → 32k
    assert descargas and any("Janela do revisor: 32.768" in e.get("etapa", "") for e in eventos)
    assert baterias.janela_do_revisor("p", [{"content": "a"}], 0) == baterias.JANELA_MIN


def test_parametro_temporario_nao_e_gravado(tmp_path, monkeypatch):
    from app import localai
    monkeypatch.setattr(localai.config, "LOCAL_CONFIG", tmp_path / "local.json")
    salvos = localai.save_params("m.gguf", {"ctx": 131072})
    assert {**salvos, **{"ctx": 32768}}["ctx"] == 32768 and localai.params("m.gguf")["ctx"] == 131072


def test_laco_de_repeticao_e_detectado_sem_falso_positivo():
    """Modelo local degenerado repetindo a mesma linha (visto com Qwen3.6 na bateria de lógica)."""
    from app.comparar import em_laco
    codigo = "def f():\n" + "".join(f"    x{i} = {i}\n" for i in range(40))
    assert not em_laco(codigo)
    assert em_laco(codigo + "            custo_total -= qtd * custo\n" * 30 + "            custo_to")
    assert em_laco(codigo + "a = 1\nb = 2\n\n" * 20)
    assert not em_laco("| a | b |\n|---|---|\n" + "".join(f"| {i} | x |\n" for i in range(30)))


def test_comparacao_corta_modelo_em_laco(monkeypatch):
    from app import comparar

    async def fluxo(*a, **k):
        yield "content", "começo\n"
        for _ in range(500):
            yield "content", "    custo_total -= qtd * custo\n"
        yield "content", "NUNCA CHEGA"

    async def ctx(*a, **k):
        return 8192

    monkeypatch.setattr(comparar.llm, "chat_stream", fluxo)
    monkeypatch.setattr(comparar.llm, "context_limit", ctx)
    monkeypatch.setattr(comparar, "_persistir", lambda run: None)
    item = {"provider": "p", "model": "m", "content": "", "reasoning": "", "status": "pendente"}
    asyncio.run(comparar._um({"cancelar": False}, item, [{"role": "user", "content": "oi"}], "medio"))
    assert item["status"] == "pronto" and comparar.LACO in item["content"]
    assert "NUNCA CHEGA" not in item["content"] and item["content"].count("custo_total") < 60


def test_com_nomes_troca_letra_solta_do_revisor():
    itens = [{"rotulo": "A", "nome": "qwen-1.5b"}, {"rotulo": "D", "nome": "Qwen3.6"}]
    from app import baterias
    texto = baterias.com_nomes("Mais correto: **D**. O A não gerou código; pelo D. Vitamina A e plano B.", itens)
    assert texto == "Mais correto: **Qwen3.6**. O qwen-1.5b não gerou código; pelo Qwen3.6. Vitamina A e plano B."


def test_refazer_depois_de_encerrada_roda_so_aquele_modelo(tmp_path, monkeypatch):
    """O modelo alucinou: gera de novo só a resposta dele, com o mesmo prompt, e descarrega no fim."""
    eventos = _fake_local(monkeypatch, tmp_path)
    chamados = _fake_llm(monkeypatch)
    itens = [{"path": _gguf(tmp_path, "um")}, {"path": _gguf(tmp_path, "dois")}]
    est = _rodar(conv_id=_conversa(), prompt="teste", itens=itens, modo="sequencial")
    mid = est["message_id"]

    async def main():
        comparar.refazer(mid, "0")
        assert comparar.estado(mid)["status"] == "rodando"
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return comparar.estado(mid)

    depois = asyncio.run(main())
    assert chamados == ["um", "dois", "um"]
    assert eventos[-2:] == ["load:um", "unload"]
    assert depois["status"] == "pronto" and _item(depois, "um")["content"] == "resposta de um"
    assert _item(depois, "dois")["content"] == "resposta de dois"   # o outro não mexe
    with pytest.raises(ToolError):
        comparar.refazer(mid, "9")


def test_refazer_no_meio_da_geracao_recomeca_do_zero(monkeypatch):
    chamados = _fake_llm(monkeypatch, {"a": ["x"] * 6}, pausa=0.02)
    itens = [{"provider": "ollama", "model": m} for m in ("a", "b")]

    async def main():
        msg = comparar.start(_conversa(), "teste", itens)
        while comparar.estado(msg["id"])["itens"][0]["content"] != "xx":
            await asyncio.sleep(0.005)
        comparar.refazer(msg["id"], "0")
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return comparar.estado(msg["id"])

    est = asyncio.run(main())
    assert chamados.count("a") == 2 and _item(est, "a")["content"] == "xxxxxx"   # não emenda com a 1ª


def test_adicionar_e_remover_modelo_sem_regerar_os_outros(tmp_path, monkeypatch):
    eventos = _fake_local(monkeypatch, tmp_path)
    chamados = _fake_llm(monkeypatch)
    itens = [{"path": _gguf(tmp_path, "um")}, {"path": _gguf(tmp_path, "dois")}]
    est = _rodar(conv_id=_conversa(), prompt="teste", itens=itens, modo="sequencial")
    mid = est["message_id"]

    async def main():
        r = comparar.adicionar(mid, {"path": _gguf(tmp_path, "tres")})
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return r, comparar.estado(mid)

    r, depois = asyncio.run(main())
    assert chamados == ["um", "dois", "tres"] and eventos[-2:] == ["load:tres", "unload"]
    novo = _item(depois, "tres")
    assert r["item"] == novo["id"] == "2" and novo["rotulo"] == "C" and novo["content"] == "resposta de tres"
    with pytest.raises(ToolError, match="repetido"):
        comparar.adicionar(mid, {"path": _gguf(tmp_path, "um")})

    comparar.votar(mid, "0")
    comparar.remover(mid, "0")
    final = comparar.estado(mid)
    assert [i["nome"] for i in final["itens"]] == ["dois", "tres"] and final["voto"] == ""
    assert _item(final, "dois")["content"] == "resposta de dois"
    with pytest.raises(ToolError, match="pelo menos 2"):
        comparar.remover(mid, "1")
    # a letra nova não reaproveita a do removido
    assert asyncio.run(_adiciona(mid, {"path": _gguf(tmp_path, "quatro")}))["rotulo"] == "D"


async def _adiciona(mid, cru):
    comparar.adicionar(mid, cru)
    await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
    return comparar.estado(mid)["itens"][-1]

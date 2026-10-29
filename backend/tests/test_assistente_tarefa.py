import asyncio

from app import agent, assistente, desktop, policy


class RunFalso:
    def __init__(self, pedidos=(), eventos=()):
        self.id, self.conv_id, self.finished = "r1", 7, False
        self.approvals = {f"c{i}": {"call": {"name": "run_command", "arguments": {"command": c}}} for i, c in enumerate(pedidos)}
        self.events = list(eventos)


def _prepara(monkeypatch, run):
    falas = []
    monkeypatch.setattr(assistente, "fala", falas.append)
    monkeypatch.setattr(assistente, "_manda", lambda *a, **k: None)
    monkeypatch.setattr(agent, "RUNS", {run.id: run})
    assistente._pendente.clear()
    return falas


def test_tarefa_fala_a_aprovacao_e_o_resultado(monkeypatch):
    run = RunFalso(["mkdir pasta-x"])
    falas = _prepara(monkeypatch, run)
    monkeypatch.setattr(assistente, "_ultima_resposta", lambda conv, desde=0: "Criei a pasta **x** com o arquivo oi.txt.")
    monkeypatch.setattr(assistente, "modelo_assistente", lambda: {"provider": "p", "model": "m"})

    async def gera(*a, **k):
        return "Pronto, criei a pasta x com o arquivo."
    monkeypatch.setattr(assistente, "_gera", gera)

    async def roda():
        t = asyncio.create_task(assistente._acompanha_tarefa(run, passo=0.01))
        await asyncio.sleep(0.05)
        assert falas == ["Para continuar, preciso rodar o comando mkdir pasta-x. Pode?"]
        assert assistente._pendente["tipo"] == "aprovar"
        await asyncio.sleep(0.05)
        assert len(falas) == 1  # o mesmo pedido não é lido de novo
        run.approvals.clear()
        run.finished = True
        await t
    asyncio.run(roda())
    assert falas[-1] == "Pronto, criei a pasta x com o arquivo."


def test_tarefa_fala_o_erro(monkeypatch):
    run = RunFalso(eventos=[{"type": "event", "message": {"content": "Modelo fora do ar", "meta": {"kind": "error"}}},
                            {"type": "done"}])
    run.finished = True
    falas = _prepara(monkeypatch, run)
    asyncio.run(assistente._acompanha_tarefa(run, passo=0.01))
    assert falas == ["A tarefa deu erro: Modelo fora do ar"]


def test_classificador_aceita_tarefa(monkeypatch):
    async def stream(*a, **k):
        yield "content", '{"tipo":"tarefa","pedido":"organiza meus downloads"}'
    from app import llm
    monkeypatch.setattr(llm, "chat_stream", stream)
    r = asyncio.run(assistente._classifica_acao({"provider": "p", "model": "m"}, None, "organiza meus downloads"))
    assert r == ("tarefa", {"tipo": "tarefa", "pedido": "organiza meus downloads"})
    assert assistente.PISTA.search(assistente._sem_acento("organiza meus downloads por tipo"))
    assert assistente.PISTA.search(assistente._sem_acento("abre o bloco de notas e escreve uma lista"))


def test_teclas_e_coordenadas():
    assert desktop.teclas("ctrl+shift+s") == [0x11, 0x10, ord("S")]
    assert desktop.teclas("Alt + F4") == [0x12, 0x73]
    desktop._tela.update(x=100, y=50, escala=1.5)
    assert desktop.converte(10, 20) == (115, 80)


def test_tela_no_automatico_e_rm_digitado_pergunta():
    from app.tools import REGISTRY
    assert policy.decide(REGISTRY["desktop_click"], {"x": 1, "y": 2}, "auto") == (False, "modo Automático: alteração na pasta de trabalho")
    assert policy.decide(REGISTRY["desktop_type"], {"texto": "rm -rf pasta"}, "auto")[0] is True
    assert policy.decide(REGISTRY["desktop_type"], {"texto": "olá"}, "auto")[0] is False


def test_abrir_e_fazer_mais_vira_tarefa():
    from app import pc
    assert pc.entende("abre o bloco de notas e escreve uma lista de compras") is None
    assert pc.entende("abre a pasta de downloads e organiza por tipo") is None
    assert pc.entende("abre o bloco de notas") == ("abrir", "bloco de notas")


def test_pergunta_do_agente_e_falada_e_respondida_por_voz(monkeypatch):
    run = RunFalso()
    run.approvals = {"q1": {"call": {"name": "ask_user", "arguments": {"questions": [
        {"question": "Qual canal você prefere?", "options": ["Kurzgesagt", "Veritasium"]}]}}}}
    resolvidos = []
    run.resolve = lambda cid, d: resolvidos.append((cid, d)) or run.approvals.pop(cid, None) or True
    falas = _prepara(monkeypatch, run)

    async def roda():
        t = asyncio.create_task(assistente._acompanha_tarefa(run, passo=0.01))
        await asyncio.sleep(0.05)
        assert falas == ["Qual canal você prefere? Kurzgesagt, Veritasium?"]
        assert assistente._pendente["tipo"] == "pergunta_agente"
        await assistente._atende({"comando": "o Veritasium", "texto": "o Veritasium", "resposta": True})
        run.finished = True
        await t
    monkeypatch.setattr(assistente, "_ultima_resposta", lambda conv, desde=0: "")
    asyncio.run(roda())
    assert resolvidos[0][0] == "q1" and resolvidos[0][1]["answers"] == ["o Veritasium"]


def test_progresso_falado_quando_demora(monkeypatch):
    run = RunFalso()
    falas = _prepara(monkeypatch, run)
    monkeypatch.setattr(assistente, "_ultima_resposta", lambda conv, desde=0: "")

    async def roda():
        t = asyncio.create_task(assistente._acompanha_tarefa(run, passo=0.01, progresso_s=0.03))
        await asyncio.sleep(0.01)  # a task começa e marca o que já tinha
        run.events.append({"type": "tool_result", "message": {"name": "web_search"}})
        await asyncio.sleep(0.1)
        run.finished = True
        await t
    asyncio.run(roda())
    assert falas[0] == "Ainda nisso: pesquisando na web."
    assert falas.count("Ainda nisso: pesquisando na web.") == 1  # sem ação nova, não repete


def test_classificador_de_pedido_ao_app_manda_youtube_para_tarefa(monkeypatch):
    feitos = []
    _prepara(monkeypatch, RunFalso())

    async def classifica(texto):
        return "tarefa"

    async def tarefa(pedido):
        feitos.append(pedido)
    monkeypatch.setattr(assistente, "classifica_rota", classifica)
    monkeypatch.setattr(assistente, "_tarefa_voz", tarefa)
    asyncio.run(assistente._atende({"comando": "reproduza um vídeo aleatório das minhas inscrições do youtube",
                                    "texto": "x", "resposta": False}))
    assert feitos == ["reproduza um vídeo aleatório das minhas inscrições do youtube"]
    assert "tarefa" in assistente.ROTAS_LLM


def test_open_in_browser_so_aceita_http(monkeypatch):
    import pytest
    from pathlib import Path
    from app.tools import ToolError
    abertos = []
    monkeypatch.setattr(desktop.os, "startfile", abertos.append, raising=False)
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    assert desktop.navegador(Path("."), {"urls": ["https://a.com", "https://b.com"]}) == "Abri 2 links no navegador da pessoa."
    assert abertos == ["https://a.com", "https://b.com"]
    with pytest.raises(ToolError):
        desktop.navegador(Path("."), {"url": "file:///C:/Windows/system32/cmd.exe"})


def test_videos_dos_canais_pela_playlist_de_envios(monkeypatch):
    from pathlib import Path
    from app import youtube
    pedidos = []

    class R:
        status_code = 200

        def json(self):
            return {"items": [{"snippet": {"title": "Buracos negros", "channelTitle": "Kurz"},
                               "contentDetails": {"videoId": "abc", "videoPublishedAt": "2026-10-20T10:00:00Z"}}]}
    monkeypatch.setattr(youtube, "_h", lambda: {})
    monkeypatch.setattr(youtube.httpx, "get", lambda url, **k: pedidos.append(k["params"]["playlistId"]) or R())
    saida = youtube.videos_canais(Path("."), {"channel_ids": ["UCsXVk37bltHxD1rDPwtNM8Q", "lixo"]})
    assert pedidos == ["UUsXVk37bltHxD1rDPwtNM8Q"]
    assert saida == "- [Kurz] Buracos negros (2026-10-20) https://www.youtube.com/watch?v=abc"


def test_midia_escolhe_a_sessao_pelo_titulo():
    sessoes = [{"i": 0, "titulo": "Instagram", "estado": "Paused"}, {"i": 1, "titulo": "Alanzoka joga", "estado": "Playing"}]
    assert desktop.escolhe_sessao(sessoes, "alanzoka")["i"] == 1
    assert desktop.escolhe_sessao(sessoes, "")["i"] == 1  # sem título: a que está tocando
    assert desktop.escolhe_sessao(sessoes, "spotify") is None


def test_teclado_recusa_quando_a_janela_da_frente_mudou(monkeypatch):
    import pytest
    from pathlib import Path
    from app.tools import ToolError

    class U:
        def GetForegroundWindow(self):
            return 222

        def GetWindowThreadProcessId(self, h, p):
            return 0

        def keybd_event(self, *a):
            raise AssertionError("não pode apertar nada")
    monkeypatch.setattr(desktop, "_u32", lambda: U())
    monkeypatch.setattr(desktop, "_traz", lambda h: False)
    monkeypatch.setattr(desktop, "_frente", lambda: "Claude")
    desktop._alvo["hwnd"] = 111
    with pytest.raises(ToolError, match="Claude"):
        desktop.aperta(Path("."), {"teclas": "enter"})
    desktop._alvo["hwnd"] = 0


def test_pausa_pela_imagem_quando_o_navegador_nao_publica_a_midia(monkeypatch):
    from pathlib import Path

    class U:
        teclas = []

        def keybd_event(self, vk, *a):
            if a[1] == 0:
                U.teclas.append(vk)
    movendo = iter([True, True, False])  # tocava; o k não pausou (vídeo carregando); o espaço pausou
    monkeypatch.setattr(desktop, "_sessoes", lambda *a: [])
    monkeypatch.setattr(desktop, "_janela", lambda t: {"hwnd": 5, "titulo": "Vídeo - YouTube"})
    monkeypatch.setattr(desktop, "_traz", lambda h: True)
    monkeypatch.setattr(desktop, "_u32", lambda: U())
    monkeypatch.setattr(desktop, "mexendo", lambda h, espera=1.2: next(movendo))
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    assert desktop.midia(Path("."), {"acao": "pausar", "titulo": "YouTube"}) == \
        "'Vídeo - YouTube': pausado (conferido pela imagem)."
    assert U.teclas == [0x4B, 0x20]


def test_tarefa_temporaria_some_depois_do_prazo_e_manter_salva(monkeypatch):
    from datetime import datetime, timedelta
    from app import db, main  # noqa: F401  (apaga_temporarias usa a limpeza do main)
    velha = assistente._conversa_tarefa("abre um vídeo de lofi")
    guardada = assistente._conversa_tarefa("organiza os downloads")
    nova = assistente._conversa_tarefa("toca uma música")
    with db.session() as s:
        for cid in (velha, guardada):
            s.get(db.Conversation, cid).updated_at = datetime.now() - timedelta(hours=2)
        s.commit()
    assert assistente.ultima_temporaria() == nova
    assert assistente.manter(guardada) is True
    monkeypatch.setattr(agent, "active_run", lambda cid: None)
    assert asyncio.run(assistente.apaga_temporarias()) == 1
    with db.session() as s:
        assert s.get(db.Conversation, velha) is None  # vencida: apagada
        assert s.get(db.Conversation, guardada).origem is None  # mantida: virou conversa comum
        assert s.get(db.Conversation, nova).origem["temporaria"]  # ainda no prazo


def test_mantem_essa_tarefa_por_voz(monkeypatch):
    falas = _prepara(monkeypatch, RunFalso())
    guardadas = []
    monkeypatch.setattr(assistente, "ultima_temporaria", lambda: 42)
    monkeypatch.setattr(assistente, "manter", guardadas.append)
    asyncio.run(assistente._atende({"comando": "mantém essa tarefa", "texto": "x", "resposta": False}))
    assert guardadas == [42] and falas == ["Guardei. Essa tarefa não some mais."]

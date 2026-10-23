import asyncio
import base64
from datetime import date, datetime

from app import assistente, google_conta, lembretes

SPEC = {"provider": "local", "model": "m"}


def _falas(monkeypatch):
    falas = []
    monkeypatch.setattr(assistente, "fala", falas.append)
    monkeypatch.setattr(assistente, "_manda", lambda *a, **k: None)
    return falas


def _google(monkeypatch, **fns):
    monkeypatch.setattr(google_conta, "conectado", lambda: True)
    for nome, fn in fns.items():
        monkeypatch.setattr(google_conta, nome, fn)


EMAIL = {"id": "m1", "thread": "t1", "de": "João Silva <joao@x.com>", "assunto": "Reunião", "data": "hoje",
         "message_id": "<abc@x>", "trecho": "Topa quinta?", "nao_lido": True}


def test_responder_le_o_rascunho_e_so_envia_com_sim(monkeypatch):
    falas = _falas(monkeypatch)
    rascunhos, enviados = [], []
    _google(monkeypatch, emails=lambda q, n=8: [EMAIL], corpo=lambda i, limite=6000: "Topa a reunião na quinta?",
            rascunho=lambda para, assunto, texto, responde=None, substitui="": rascunhos.append((para, assunto, texto, substitui)) or {"id": "d1"},
            enviar_rascunho=lambda d: enviados.append(d))
    respostas = iter(["João, leu o e-mail sobre a reunião.", "ASSUNTO: x\n\nTopo sim, até quinta.",
                      "ASSUNTO: x\n\nTopo sim, mas na sexta."])

    async def gera(spec, slot, sistema, usuario, max_tokens=400):
        assert "<<<DADOS>>>" in usuario or "Escreva" in usuario  # e-mail entra sempre como dado
        return next(respostas)
    monkeypatch.setattr(assistente, "_gera", gera)

    async def prepara():
        return SPEC, None, None
    monkeypatch.setattr(assistente, "_prepara_modelo", prepara)
    assistente._pendente.clear()
    asyncio.run(assistente._email_voz({"acao": "ler_ultimo", "de": "joão"}, SPEC, None))
    asyncio.run(assistente._email_voz({"acao": "responder", "texto": "que eu topo"}, SPEC, None))
    assert rascunhos[-1][:2] == ("joao@x.com", "Re: Reunião") and falas[-1].endswith("Mando?")
    assert assistente._pendente["tipo"] == "enviar_email"
    asyncio.run(assistente._enviar_respondido("muda para sexta"))   # ajuste: reescreve o mesmo rascunho
    assert rascunhos[-1][3] == "d1" and "sexta" in falas[-1] and not enviados
    asyncio.run(assistente._enviar_respondido("Pode mandar."))
    assert enviados == ["d1"] and falas[-1] == "Enviado para João Silva." and not assistente._pendente


def test_nao_envia_sem_um_sim_claro():
    for frase in ["manda dizendo que eu atraso", "sim mas muda o horário", "manda pro chefe também"]:
        assert not assistente.SIM.match(frase), frase
    for frase in ["manda", "pode mandar", "sim", "sim, pode enviar", "envia"]:
        assert assistente.SIM.match(frase), frase
    assert assistente.NAO.match("nao") and assistente.NAO.match("deixa pra la")


def test_agenda_lista_e_marca(monkeypatch):
    falas = _falas(monkeypatch)
    criados = []
    evs = [{"id": "1", "titulo": "Dentista", "inicio": datetime(2026, 10, 24, 9, 0), "dia_todo": False, "local": ""},
           {"id": "2", "titulo": "Aniversário", "inicio": date(2026, 10, 24), "dia_todo": True, "local": ""}]
    _google(monkeypatch, eventos=lambda i, f: evs, criar_evento=lambda t, q, minutos=60: criados.append((t, q)) or {})
    monkeypatch.setattr(lembretes, "ARQUIVO", lembretes.config.DATA_DIR / "lembretes-teste.json")
    lembretes.ARQUIVO.unlink(missing_ok=True)
    amanha = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + __import__("datetime").timedelta(days=1)
    lembretes.criar(amanha.replace(hour=14), "pagar a luz")
    asyncio.run(assistente._agenda_voz({"acao": "listar", "quando": "amanhã"}, SPEC, None))
    assert falas[-1] == "Amanhã você tem 3 compromissos: o dia todo, Aniversário; às 9h, Dentista; às 14h, lembrete: pagar a luz."
    asyncio.run(assistente._agenda_voz({"acao": "criar", "titulo": "dentista", "quando": "sexta às 10",
                                        "aviso_min": 15}, SPEC, None))
    assert criados[-1][0] == "dentista" and criados[-1][1].hour == 10
    assert "na sua agenda" in falas[-1] and "15 minutos antes" in falas[-1]
    assert any(x["texto"] == "dentista em 15 minutos" for x in lembretes.lista())
    asyncio.run(assistente._agenda_voz({"acao": "criar", "titulo": "academia", "quando": ""}, SPEC, None))
    assert falas[-1] == "Pra quando?" and assistente._pendente["tipo"] == "evento"
    asyncio.run(assistente._evento_quando("amanhã"))
    assert criados[-1] == ("academia", criados[-1][1]) and isinstance(criados[-1][1], date) \
        and not isinstance(criados[-1][1], datetime)   # só o dia: evento do dia inteiro
    lembretes.ARQUIVO.unlink(missing_ok=True)


def test_avisos_de_email_so_os_novos(monkeypatch, tmp_path):
    caixa = [dict(EMAIL, id="a")]
    _google(monkeypatch, emails=lambda q, n=10: list(caixa))
    monkeypatch.setattr(assistente, "AVISOS_VISTOS", tmp_path / "vistos.json")
    monkeypatch.setattr(assistente, "ler", lambda: {**assistente.PADRAO, "avisos_email": ["joao"]})
    avisos = []
    monkeypatch.setattr(lembretes, "avisa", lambda titulo, texto, falado, fala, id_="": avisos.append((titulo, texto)))
    assert assistente._confere_avisos_email() == [] and avisos == []      # primeira volta: só anota
    caixa.insert(0, dict(EMAIL, id="b", assunto="Build quebrou"))
    assert [e["id"] for e in assistente._confere_avisos_email()] == ["b"]
    assert avisos == [("E-mail de João Silva", "Build quebrou")]
    assert assistente._confere_avisos_email() == []                       # já visto


def test_resumo_agendado_uma_vez_por_dia(monkeypatch, tmp_path):
    monkeypatch.setattr(assistente, "RESUMO_FEITO", tmp_path / "r.txt")
    monkeypatch.setattr(assistente, "ler", lambda: {**assistente.PADRAO, "resumo_hora": "08:00"})

    async def prepara():
        return SPEC, None, None

    async def resumo(spec, slot):
        return "Bom dia."
    monkeypatch.setattr(assistente, "_prepara_modelo", prepara)
    monkeypatch.setattr(assistente, "_resumo_dia", resumo)
    avisos = []
    monkeypatch.setattr(lembretes, "avisa", lambda *a, **k: avisos.append(a[:2]))
    r = lambda h, m: asyncio.run(assistente._resumo_agendado(datetime(2026, 10, 23, h, m)))
    assert not r(7, 59)
    assert r(8, 1) and avisos == [("Resumo do dia", "Bom dia.")]
    assert not r(8, 30)                                   # já saiu hoje
    (tmp_path / "r.txt").unlink()
    assert not r(11, 30)                                  # passou da janela de 3 h


def test_corpo_do_email(monkeypatch):
    b64 = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
    msg = {"payload": {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/html", "body": {"data": b64("<p>Oi <b>João</b></p><style>x{}</style>")}}]}}
    monkeypatch.setattr(google_conta, "_pede", lambda *a, **k: msg)
    assert google_conta.corpo("x") == "Oi João"
    msg["payload"]["parts"].append({"mimeType": "text/plain", "body": {"data": b64("Oi João, tudo certo?")}})
    assert google_conta.corpo("x") == "Oi João, tudo certo?"
    raw = google_conta._raw("a@b.com", "Re: Reunião", "Topo.", EMAIL)
    texto = base64.urlsafe_b64decode(raw).decode()
    assert "In-Reply-To: <abc@x>" in texto and "To: a@b.com" in texto


def test_configuracao_nova():
    assert assistente.PADRAO["avisos_email"] == [] and assistente.PADRAO["resumo_hora"] == ""

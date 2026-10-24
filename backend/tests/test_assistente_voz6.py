import asyncio
from datetime import datetime, timedelta

from app import assistente, conhecimento, lembretes, pc

SPEC = {"provider": "local", "model": "m"}


def _prepara(monkeypatch, tmp_path):
    falas, mandados = [], []
    monkeypatch.setattr(assistente, "_anota", lambda quem, texto: falas.append(texto))
    monkeypatch.setattr(assistente, "_manda", lambda cmd, **d: mandados.append((cmd, d)))
    monkeypatch.setattr(assistente, "ACOES", tmp_path / "acoes.jsonl")
    monkeypatch.setattr(conhecimento, "NOTAS", tmp_path / "notas.json")
    monkeypatch.setattr(lembretes, "ARQUIVO", tmp_path / "lembretes.json")
    return falas, mandados


def test_registra_e_desfaz_nota_e_lembrete(monkeypatch, tmp_path):
    falas, _ = _prepara(monkeypatch, tmp_path)
    asyncio.run(assistente._saber_voz("nota", {"acao": "anotar", "texto": "comprar café"}, SPEC, None))
    asyncio.run(assistente._lembrete("criar", (datetime.now() + timedelta(hours=1), "ligar pro João")))
    assert [a["tipo"] for a in assistente.acoes()] == ["lembrete", "nota"] and conhecimento.notas()
    asyncio.run(assistente._desfaz())                          # a última: o lembrete
    assert lembretes.lista() == [] and falas[-1].startswith("Desfeito: Lembrete")
    asyncio.run(assistente._desfaz())                          # depois a nota
    assert conhecimento.notas() == [] and not any(a["pode_desfazer"] for a in assistente.acoes())
    asyncio.run(assistente._desfaz())
    assert falas[-1] == "Não tem nada recente que eu consiga desfazer."


def test_volume_volta_como_estava(monkeypatch, tmp_path):
    falas, _ = _prepara(monkeypatch, tmp_path)
    chamadas = []

    def audio(*a):
        chamadas.append(a)
        return "70;1" if a[0] == "estado" else "30"
    monkeypatch.setattr(pc, "audio", audio)
    monkeypatch.setattr(pc, "faz", lambda acao, arg: "Volume em 30.")
    assert asyncio.run(assistente._pc("volume", "30")) == "Volume em 30."
    asyncio.run(assistente._desfaz())
    assert ("volume", "70") in chamadas and ("mudo", "1") in chamadas   # volume e mudo como estavam
    assert falas[-1] == "Desfeito: Volume em 30."


def test_traduz_com_voz_nativa_e_conversa_em_ingles(monkeypatch, tmp_path):
    falas, mandados = _prepara(monkeypatch, tmp_path)

    async def gera(spec, slot, sistema, usuario, max_tokens=400):
        assert "espanhol" in sistema and "<<<DADOS>>>" in usuario
        return "Buenos días"
    monkeypatch.setattr(assistente, "_gera", gera)
    asyncio.run(assistente._idioma_voz("traduzir", {"texto": "bom dia", "idioma": "espanhol"}, SPEC, None))
    assert mandados[-1] == ("fala", {"texto": "Buenos días", "voz": "ef_dora", "lang": "es"})
    asyncio.run(assistente._idioma_voz("idioma", {"idioma": "inglês"}, SPEC, None))
    assert ("idioma", {"stt": ""}) in mandados and mandados[-1][1]["voz"] == "af_heart"
    assert assistente._idioma["cod"] == "en"
    asyncio.run(assistente._idioma_voz("idioma", {"idioma": "português"}, SPEC, None))
    assert assistente._idioma == {} and ("idioma", {"stt": "pt"}) in mandados


def test_regra_do_desfaz():
    for frase in ["desfaz", "desfaz isso", "Forja desfaça", "volta o que você fez", "Não, volta atrás.", "Volta pro que estava antes, por favor.",
                  "Não era isso. Dá um passo atrás.", "Mano, para tudo. Volta pra como tava antes."]:
        assert assistente.DESFAZ.search(assistente._sem_acento(frase.replace("Forja ", ""))), frase
    assert not assistente.DESFAZ.search(assistente._sem_acento("volta o som"))

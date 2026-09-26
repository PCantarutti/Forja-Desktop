"""E4: contexto em janela pequena — tetos proporcionais, compactação dentro do turno, contexto único e
catálogo enxuto."""
from app import agent, catalogo, compact, config, db, main, sessoes  # noqa: F401  (main registra as ferramentas)
from app.tools import spill, vision_caps


def _janela(n):
    return config.JANELA.set(n)


def test_tetos_mudam_com_a_janela(monkeypatch):
    monkeypatch.setattr(config, "FATOR_TETOS", 1.0)  # o perfil de hardware multiplica; aqui só a janela
    tok = _janela(8192)
    try:
        assert config.teto(20_000, 0.2) == int(8192 * 0.2 * 3)
        assert config.teto_linhas(2000, 0.3) < 200
        texto = "x" * 30_000
        assert len(spill(texto, "t-janela")) < 9_000        # 8k: ~6k de cabeça e cauda + o aviso
    finally:
        config.JANELA.reset(tok)
    tok = _janela(262_144)
    try:
        assert config.teto(20_000, 0.2) == 20_000             # janela grande: o padrão de sempre
    finally:
        config.JANELA.reset(tok)
    assert config.teto(20_000, 0.2) == 20_000                 # sem janela conhecida: o padrão


def _msg(i, role, **kw):
    return db.Message(id=i, role=role, content=kw.pop("content", ""), **kw)


def test_compacta_dentro_de_um_turno_so():
    msgs = [_msg(1, "user", content="faça tudo")]
    i = 2
    for n in range(10):
        msgs.append(_msg(i, "assistant", tool_calls=[{"id": f"c{n}", "name": "read_file", "arguments": {}}]))
        msgs.append(_msg(i + 1, "tool", tool_call_id=f"c{n}", name="read_file", status="ok", content="r"))
        i += 2
    assert compact.split_point(msgs) is None                  # um turno só: o corte antigo não resumia nada
    corte = compact.split_point_turno(msgs)
    restantes = [m for m in msgs if m.id > corte]
    assert restantes[0].role == "assistant"                   # o que fica começa numa chamada, nunca num resultado
    assert sum(m.role == "tool" for m in restantes) == compact.PODA_MANTEM


def test_so_o_ultimo_contexto_vai_ao_modelo():
    msgs = [_msg(1, "event", content="Contexto atual de execução. v1", meta={"kind": "contexto", "to_model": True}),
            _msg(2, "user", content="oi"),
            _msg(3, "event", content="Contexto atual de execução. v2", meta={"kind": "contexto", "to_model": True}),
            _msg(4, "user", content="de novo")]
    hist = agent.build_history(msgs, "native", contexto=True)
    textos = "\n".join(str(m.get("content")) for m in hist)
    assert "v2" in textos and "v1" not in textos


def test_anel_conta_o_contexto_como_sistema():
    messages = [{"role": "system", "content": "base"},
                {"role": "user", "content": agent.CONTEXTO_PREFIXO + " " + "x" * 4000},
                {"role": "user", "content": "pergunta curta"}]
    partes = agent.partes_do_contexto(messages, None)
    assert partes["sistema"] > partes["mensagens"]


def test_catalogo_enxuto_na_janela_pequena_e_mais_ferramentas_liga_o_grupo():
    caps = vision_caps({"vision"}, "auto")
    todas = agent.available_tools(caps, "manual")
    assert not catalogo.enxuto(todas, 262_144)
    assert catalogo.filtra(todas, 1, 262_144) == todas         # janela grande: nada muda
    pequena = catalogo.filtra(todas, 99, 16_384)
    nomes = {t.name for t in pequena}
    assert "mais_ferramentas" in nomes and "write_document" not in nomes and "read_file" in nomes
    assert catalogo.tokens(pequena) < catalogo.tokens(todas)
    curto = [catalogo.schema(t, True) for t in pequena]
    assert sum(len(str(s)) for s in curto) < sum(len(str(t.openai_schema())) for t in pequena)
    tok = sessoes.CONV.set(99)
    try:
        assert "write_document" in catalogo._mais_ferramentas(None, {"grupo": "documentos"})
    finally:
        sessoes.CONV.reset(tok)
    assert "write_document" in {t.name for t in catalogo.filtra(todas, 99, 16_384)}

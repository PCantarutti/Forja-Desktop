import asyncio
import copy
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, db, estudos, estudos_prova as P, mirror, pesquisa
from app.tools import ToolError


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(estudos, "RAIZ", tmp_path / "estudos")
    monkeypatch.setattr(config, "SUBAGENTS", {})
    estudos._RUNS.clear()
    yield
    estudos._RUNS.clear()


RESUMO = ("# Biologia\n\nVisão.\n\n## 1. Glicólise\n\nNo citosol, saldo de 2 ATP [p. 2].\n\n"
          "## 2. Fermentação\n\nRegenera NAD+ [p. 5].\n\n## Fontes\n\n- apostila")


def _estudo(resumo=True) -> int:
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="Biologia")
        s.add(c)
        s.commit()
        conv = c.id
    if resumo:
        topicos = [{"titulo": t, "objetivo": "", "pontos": [], "status": "pronto"} for t in ("Glicólise", "Fermentação")]
        estudos._save(conv, role="assistant", content=RESUMO, status="pronto",
                      meta={"estudos": {"tipo": "resumo", "tema": "Biologia", "topicos": topicos,
                                        "perfil": {"alternativas": 4, "banca": "ENEM"}}})
    return conv


def _unicas(i: int, prefixo: str) -> str:
    """Palavras só desta questão: sem isto as falsas seriam a mesma questão e a checagem de semelhança as barraria."""
    return " ".join(f"{prefixo}{i}{c}" for c in "abcdef")


def _me(i, correta=1, alts=4):
    return {"tipo": "me", "enunciado": f"Questão {i}: onde ocorre a glicólise, {_unicas(i, 'termo')}?",
            "alternativas": [f"lugar {i}-{k}" for k in range(alts)], "correta": correta,
            "explicacao": "Ocorre no citosol.", "por_alternativa": [f"porque {k}" for k in range(alts)], "pagina": "p. 2"}


def _vf(i, correta=True):
    return {"tipo": "vf", "enunciado": f"Afirmação {i}: a glicólise dá 2 ATP, {_unicas(i, 'item')}.", "correta": correta,
            "explicacao": "Saldo 2."}


def _disc(i):
    return {"tipo": "disc", "enunciado": f"Explique {i} por que a fermentação regenera NAD+, {_unicas(i, 'ponto')}.",
            "resposta_modelo": "Para a glicólise continuar.", "rubrica": [{"criterio": "cita NAD+", "pontos": 1},
                                                                         {"criterio": "cita glicólise", "pontos": 1}]}


USUARIO: list[str] = []   # o que cada chamada ao modelo falso recebeu como mensagem do usuário
SISTEMA: list[str] = []   # e como prompt de sistema
MODELOS: list[str] = []   # e qual modelo foi chamado


def _fake(monkeypatch, gerar=None, verificar=None, corrigir=None, pausa=0.0):
    """gerar(pedidas: list[str]) -> lista de questões; verificar(questoes do prompt) -> {id: resposta}."""
    chamados: list[str] = []
    ultimas: dict[str, list] = {}
    USUARIO.clear()
    SISTEMA.clear()
    MODELOS.clear()

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        USUARIO.append(user)
        SISTEMA.append(system)
        MODELOS.append(model)
        if pausa:
            await asyncio.sleep(pausa)
        if system.startswith("Você é um professor que elabora"):
            chamados.append("gerar")
            pedidas = re.findall(r"^\d+\. (me|vf|disc) ", user, re.M)
            qs = gerar(pedidas) if gerar else [{"me": _me, "vf": _vf, "disc": _disc}[t](len(chamados) * 10 + i)
                                                for i, t in enumerate(pedidas)]
            ultimas["gerar"] = qs
            texto = json.dumps({"questoes": qs}, ensure_ascii=False)
        elif system.startswith("Você resolve questões"):
            chamados.append("verificar")
            ids = re.findall(r"^\[(q\d+)\]", user, re.M)
            texto = json.dumps({"respostas": [{"id": i, "resposta": r} for i, r in (verificar(user, ids) if verificar else {}).items()]})
        elif system.startswith("Você corrige uma questão discursiva"):
            chamados.append("corrigir")
            texto = json.dumps(corrigir(user) if corrigir else {"criterios": [{"pontos": 1}, {"pontos": 0.5}], "feedback": "Faltou glicólise."})
        else:
            chamados.append("?")
            texto = ""
        yield ("content", texto)
        yield ("done", {"prompt_tokens": 10, "completion_tokens": 20})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)
    return chamados


def _correto_do_prompt(user: str, ids: list[str]) -> dict:
    """Verificador que acerta: acha, no prompt, a alternativa cujo texto é o "lugar X-1" (a correta do _me)."""
    out = {}
    for qid, bloco in zip(ids, re.split(r"^\[q\d+\]", user, flags=re.M)[1:]):
        m = re.search(r"^([A-E])\) lugar \d+-1$", bloco, re.M)
        out[qid] = m.group(1) if m else "V"
    return out


async def _acabar():
    while estudos._TAREFAS:
        await asyncio.gather(*list(estudos._TAREFAS))


def _gerar(conv, config_, **kw):
    async def main():
        msg = P.start(conv, config_, "fake", "m", **kw)
        await _acabar()
        return msg["id"]
    return asyncio.run(main())


def _cheia(pid):
    with db.session() as s:
        return s.get(db.Message, pid).meta["estudos"]


# ------------------------------------------------------------------ validação


def test_validar_cada_tipo_e_os_erros():
    q, _ = P.validar(_me(1, correta="B"))
    assert q["correta"] == 1 and len(q["por_alternativa"]) == 4
    assert P.validar({**_me(1), "correta": 9})[1] == "sem a alternativa correta"
    assert P.validar({**_me(1), "alternativas": ["a", "a", "b", "c"]})[1] == "alternativas repetidas"
    assert P.validar({**_me(1), "alternativas": ["A) um", "B) dois", "C) três"]})[0]["alternativas"] == ["um", "dois", "três"]
    assert P.validar(_vf(1, "Falso"))[0]["correta"] is False
    assert P.validar({**_vf(1), "correta": "talvez"})[1] == "sem o gabarito verdadeiro/falso"
    d, _ = P.validar({**_disc(1), "rubrica": [{"criterio": "a", "pontos": "3"}, {"criterio": "b", "pontos": 1}]})
    assert sum(r["pontos"] for r in d["rubrica"]) == pytest.approx(P.PONTOS["disc"])   # rubrica reescalada
    assert P.validar(_me(1), "vf")[1] == "veio me no lugar de vf"


def test_alternativas_com_o_mesmo_valor_sao_recusadas():
    base = _me(1)
    assert P.validar({**base, "alternativas": ["30/55", "6/11", "2/3", "5/8"]})[1].startswith("duas alternativas com o mesmo valor")
    assert P.validar({**base, "alternativas": ["0,5", "50%", "1/3", "2"]})[1].startswith("duas alternativas")
    assert P.validar({**base, "alternativas": ["5 m", "5 s", "10 m", "20 m"]})[0]   # unidades diferentes: valores diferentes
    assert P.validar({**base, "alternativas": ["$\\frac{1}{2}$ de tudo", "metade", "um terço", "nada"]})[0]   # texto: não compara


def _verificador_que_revê(convencido: set[str], chamadas: list):
    """chat_stream falso: gera 2 ME; na 1ª olhada o verificador erra as duas; revendo com o argumento do autor,
    é convencido nas de `convencido` e mantém o erro nas outras."""
    async def chat_stream(provider, model, messages, *a, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        chamadas.append((system[:22], model))
        if system.startswith("Você é um professor que elabora"):
            yield ("content", json.dumps({"questoes": [_me(1), _me(2)]}))
        else:
            revendo = system.startswith("Você conferiu questões")
            certo = _correto_do_prompt(user, re.findall(r"^\[(q\d+)\]", user, re.M))
            errado = {i: "ABCD"[("ABCD".index(c) + 1) % 4] for i, c in certo.items()}
            resp = {i: (certo[i] if revendo and i in convencido else errado[i]) for i in certo}
            yield ("content", json.dumps({"respostas": [{"id": i, "conta": "revi" if revendo else "1ª conta", "resposta": r}
                                                        for i, r in resp.items()]}))
        yield ("done", {})
    return chat_stream


def test_verificador_revê_com_o_argumento_do_autor(monkeypatch):
    chamadas: list = []
    monkeypatch.setattr(pesquisa.llm, "chat_stream", _verificador_que_revê({"q1"}, chamadas))
    pid = _gerar(_estudo(), {"me": 2}, ex_provider="fake", ex_model="outro")
    e = _cheia(pid)
    q1 = next(q for q in e["questoes"] if q["id"] == "q1")
    assert q1["verificada"] and q1["desempate"]   # foi convencido: a questão fica
    assert "desempate" not in estudos.estado(pid)["questoes"][0]   # e a tela não vê a 1ª resposta dele
    assert [p["status"] for p in e["planejadas"]] == ["ok", "descartada"]
    d = next(x for x in e["descartadas"] if x["id"] == "q2")
    assert d["conta"] == "1ª conta" and d["conta_desempate"] == "revi" and d["gabarito"] != d["verificador"]
    revisoes = [m for sist, m in chamadas if sist.startswith("Você conferiu")]
    assert revisoes and set(revisoes) == {"outro"}   # quem revê é o verificador, não o autor


def test_rever_mostra_o_argumento_do_autor_e_a_conta_do_verificador(monkeypatch):
    vistos: list[str] = []

    async def chat_stream(provider, model, messages, *a, **kw):
        vistos.append(messages[1]["content"])
        yield ("content", "{}")
        yield ("done", {})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)
    q, _ = P.validar({**_me(1), "explicacao": "Ocorre no citosol, diz o autor."})
    q["id"] = "q1"
    run = {"cancelar": False, "t0": 0, "teto": 9e9, "stats": estudos.stats_novos({"model": "x", "provider": "x"},
                                                                                {"model": "x", "provider": "x"})}
    asyncio.run(P._rever(run, {"provider": "fake", "model": "outro"}, [q], {"q1": (0, "minha conta deu A", "")},
                         {"itens": [], "web": []}, 10_000))
    assert "O autor marcou B: Ocorre no citosol, diz o autor." in vistos[0] and "Você tinha marcado A: minha conta deu A" in vistos[0]


def test_duas_certas_na_lista_do_verificador_e_defeito():
    q, _ = P.validar(_me(1))
    q["id"] = "q1"
    obj = {"respostas": [{"id": "q1", "resposta": "B", "certas": ["B", "C"], "conta": "x"}]}
    assert P._respostas(obj, [q])["q1"][2] == "mais de uma alternativa certa: B, C"
    obj["respostas"][0]["certas"] = ["B"]
    assert P._respostas(obj, [q])["q1"][2] == ""


def test_mesma_resposta_em_duas_questoes_e_repeticao():
    def me(enunciado, certa, outras=("Diagrama de classes", "Diagrama de atividades", "Diagrama de componentes")):
        return P.validar({"tipo": "me", "enunciado": enunciado, "alternativas": [certa, *outras], "correta": 0,
                          "explicacao": "porque sim."})[0]
    a = me("Qual diagrama mostra a troca de mensagens no tempo?", "Diagrama de sequência")
    b = me("Para a ordem cronológica das mensagens entre objetos, use o:", "Diagrama de sequência")
    c = me("Qual característica da ISO 9126 trata de mudar de ambiente?", "Portabilidade", ("Usabilidade", "Eficiência", "Confiabilidade"))
    d = me("Sobre a característica Portabilidade, é correto afirmar:", "Ser transferido para outro ambiente",
           ("Ser fácil de aprender", "Usar pouca memória", "Não falhar"))
    n = me("Quanto vale 2 + 1?", "3", ("4", "5", "6"))
    assert P._mesma_resposta(b, [a]) and P._mesma_resposta(c, [d])
    assert not P._mesma_resposta(a, [c]) and not P._mesma_resposta(n, [me("Quanto vale 6/2?", "3", ("1", "2", "4"))])


def test_json_torto_ganha_uma_chance_de_conserto(monkeypatch):
    pedidos: list[str] = []

    async def chat_stream(provider, model, messages, *a, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        if system.startswith("Você é um professor que elabora"):
            pedidos.append(user)
            ok = json.dumps({"questoes": [_me(1)]})
            yield ("content", ok if "não é um JSON válido" in user else ok.replace('"alternativas"', 'alternativas'))
        else:
            yield ("content", "{}")
        yield ("done", {})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)
    pid = _gerar(_estudo(), {"me": 1})
    assert len(pedidos) == 2 and "perto de" in pedidos[1] and "Resposta anterior" in pedidos[1]
    assert len(_cheia(pid)["questoes"]) == 1


def test_alternativa_nenhuma_das_anteriores_e_letra_na_explicacao():
    assert "anteriores" in P.validar({**_me(1), "alternativas": ["x", "y", "z", "Nenhuma das anteriores"]})[1]
    assert "anteriores" in P.validar({**_me(1), "alternativas": ["x", "y", "z", "Todas as alternativas"]})[1]
    q, _ = P.validar({**_me(1), "explicacao": "A alternativa 1 reflete a regra; a letra C também (alternativa B).",
                      "por_alternativa": ["Errada: veja a alternativa D.", "", "", ""]})
    assert q["explicacao"] == "A alternativa correta reflete a regra; a letra correta também (alternativa correta)."
    assert q["por_alternativa"][0] == "Errada: veja a alternativa correta."
    assert P._sem_letra("Alternativa correta: vitamina C.") == "Alternativa correta: vitamina C."   # C de vitamina fica


def test_questao_parecida_com_outra_ou_com_a_do_pdf_e_refeita(monkeypatch):
    jpa = {"tipo": "me", "enunciado": "Qual especificação Java EE é responsável exclusivamente pela persistência de dados?",
           "alternativas": ["JPA", "JTA", "JSF", "EJB"], "correta": 0, "explicacao": "A JPA cuida da persistência."}
    jpa2 = {**jpa, "enunciado": "Em uma aplicação Java EE, qual especificação é responsável exclusivamente pela persistência?"}
    q1, _ = P.validar(jpa)
    q2, _ = P.validar(jpa2)
    assert P._parecida(q2, [P._palavras(f"{q1['enunciado']} JPA")])
    assert not P._parecida(P.validar(_me(1))[0], [P._palavras(f"{q1['enunciado']} JPA")])
    blocos = P._blocos_prova("Questão 1\nQual especificação Java EE é responsável exclusivamente pela persistência de "
                             "dados?\nA) JPA\nB) JTA\nResolução\nA JPA.\nQuestão 2\nOutra coisa sobre redes e switches.")
    assert len(blocos) == 2 and "resolucao" not in blocos[0]

    lotes = {"n": 0}

    async def chat_stream(provider, model, messages, *a, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        if system.startswith("Você é um professor que elabora"):
            lotes["n"] += 1
            yield ("content", json.dumps({"questoes": [jpa, jpa2]} if lotes["n"] == 1 else {"questoes": [_me(5)]}))
        else:   # acerta: a letra da linha "JPA" ou "lugar N-1" de cada questão
            ids = re.findall(r"^\[(q\d+)\]", user, re.M)
            blocos = re.split(r"^\[q\d+\]", user, flags=re.M)[1:]
            certo = {i: re.search(r"^([A-E])\) (JPA|lugar \d+-1)$", b, re.M).group(1) for i, b in zip(ids, blocos)}
            yield ("content", json.dumps({"respostas": [{"id": i, "resposta": c} for i, c in certo.items()]}))
        yield ("done", {})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)
    pid = _gerar(_estudo(), {"me": 2})
    e = _cheia(pid)
    assert lotes["n"] == 2 and [q["enunciado"][:10] for q in e["questoes"]] == [jpa["enunciado"][:10], "Questão 5:"]


def test_area_casa_pelo_nome_inteiro_antes_da_primeira_palavra():
    areas = [{"area": "Conhecimentos Gerais", "peso": 0.25}, {"area": "Conhecimentos Específicos", "peso": 0.75}]
    casou = P._casar(["Governança de TI", "Atualidades"], {"Governança de TI": "Conhecimentos Específicos",
                                                          "Atualidades": "Conhecimentos Gerais"}, areas)
    assert casou == {"Governança de TI": "Conhecimentos Específicos", "Atualidades": "Conhecimentos Gerais"}
    # sem a área no roteiro, a palavra que é de duas áreas não decide sozinha
    assert P._casar(["Conhecimentos de redes"], {}, areas) == {"Conhecimentos de redes": None}


def test_json_com_latex_de_barra_simples():
    bruto = '{"a": "$\\mathrm{CO_2}$, $\\frac{1}{2} \\times 3$, $\\sqrt{2}$, linha\\nquebra, \\"x\\", \\u00e9, \\\\alpha"}'
    assert P._json(bruto)["a"] == '$\\mathrm{CO_2}$, $\\frac{1}{2} \\times 3$, $\\sqrt{2}$, linha\nquebra, "x", é, \\alpha'


def test_lote_com_latex_de_barra_simples_nao_se_perde(monkeypatch):
    async def latex(provider, model, messages, *a, **kw):
        if messages[0]["content"].startswith("Você é um professor que elabora"):
            q = json.dumps({**_me(1), "explicacao": "XX"}, ensure_ascii=False).replace("XX", "Saldo de $\\\\frac{4}{2}$ ATP")
            yield ("content", '{"questoes": [' + q.replace("\\\\frac", "\\frac") + "]}")   # barra simples, como o modelo faz
        else:
            yield ("content", "{}")
        yield ("done", {})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", latex)
    pid = _gerar(_estudo(), {"me": 1})
    assert _cheia(pid)["questoes"][0]["explicacao"] == "Saldo de $\\frac{4}{2}$ ATP"


def test_embaralhar_mantem_a_correta_certa():
    import random
    q, _ = P.validar(_me(1, correta=2))
    certa = q["alternativas"][2]
    por = q["por_alternativa"][2]
    for seed in range(20):
        x = {**q, "alternativas": list(q["alternativas"]), "por_alternativa": list(q["por_alternativa"])}
        P._embaralhar(x, random.Random(seed))
        assert x["alternativas"][x["correta"]] == certa and x["por_alternativa"][x["correta"]] == por


def test_planejar_distribui_tipos_topicos_e_dificuldade():
    cfg = {"me": 3, "vf": 2, "disc": 1, "topicos": ["A", "B"], "dificuldade": "mista"}
    plano = P._planejar(cfg)
    assert [p["tipo"] for p in plano] == ["me", "me", "me", "vf", "vf", "disc"]
    assert [p["topico"] for p in plano] == ["A", "B", "A", "B", "A", "B"]
    assert [p["dificuldade"] for p in plano][:4] == ["facil", "media", "media", "dificil"]
    assert [len(l) for l in P._lotes(plano)] == [3, 2, 1]


def test_config_valida():
    ctx = P._contexto(_estudo())
    assert ctx["topicos"] == ["Glicólise", "Fermentação"]
    with pytest.raises(ToolError, match="pelo menos uma"):
        P._config({"me": 0}, ctx)
    with pytest.raises(ToolError, match="No máximo"):
        P._config({"me": 41}, ctx)
    cfg = P._config({"me": 2, "topicos": ["Fermentação", "Inventado"], "estilo": True}, ctx)
    assert cfg["topicos"] == ["Fermentação"] and cfg["alternativas"] == 4 and cfg["estilo"]


def test_resumo_cancelado_vazio_nao_apaga_os_topicos():
    conv = _estudo()
    estudos._save(conv, role="assistant", content="", status="cancelado",
                  meta={"estudos": {"tipo": "resumo", "tema": "x", "topicos": [{"titulo": "Outro", "status": "fila"}]}})
    assert P.topicos(conv) == ["Glicólise", "Fermentação"] == estudos.projeto(conv)["topicos"]


SIMULADO = ("--- página 1 ---\nLEIA ATENTAMENTE AS INSTRUÇÕES. Este caderno contém 90 questões.\n\n"
            + "\n\n".join(f"QUESTÃO {n}\nEnunciado da questão {n} sobre fermentação e respiração celular, com texto-base "
                          f"longo o bastante para valer como exemplo de estilo de enunciado do ENEM.\n"
                          "/L57840um\n/L57841dois\n/L57842três\n/L57843quatro\n/L57844cinco\n"
                          f"Resolução\nA resposta é a C porque a fermentação regenera NAD+.\nResposta: C"
                          for n in range(91, 99)))


def test_simulado_sozinho_vira_fonte_e_exemplo_sem_a_resolucao(monkeypatch):
    conv = _estudo(resumo=False)
    m = estudos.adicionar_material(conv, "simulado.txt", texto=SIMULADO)
    assert m["uso"] == "prova"
    ctx = P._contexto(conv)   # sem resumo e sem material de conteúdo: o simulado basta
    assert any("prova anexada" in i["cabeca"] for i in ctx["itens"])
    ex = ctx["simulado"]
    assert "QUESTÃO 91" in ex and "QUESTÃO 98" in ex and "Resolução" not in ex and "LEIA ATENTAMENTE" not in ex
    assert "C) três" in ex   # as letras das alternativas voltaram (glifos /L5784x)
    _fake(monkeypatch, verificar=_correto_do_prompt)
    pid = _gerar(conv, {"me": 1, "estilo": True})
    assert estudos.estado(pid)["status"] == "pronto"
    gerar = USUARIO[0]
    assert "prova anexada: use o conteúdo, não copie" in gerar and "imite o estilo" in gerar


def test_paginas_da_web_do_resumo_vao_para_a_prova(monkeypatch):
    conv = _estudo()
    with db.session() as s:
        m = s.scalars(select(db.Message).where(db.Message.conversation_id == conv)).first()
        fonte = {"id": "0", "url": "https://bio.org/glicolise", "titulo": "Glicólise passo a passo", "status": "util",
                 "resumo": "A fosfofrutoquinase é a enzima-chave da glicólise.", "trecho": "enzima-chave"}
        m.meta = {"estudos": {**m.meta["estudos"], "fontes": [fonte, {**fonte, "id": "1", "status": "vazia"}]}}
        s.commit()
    assert len(P._contexto(conv)["web"]) == 1   # só as úteis
    _fake(monkeypatch, verificar=_correto_do_prompt)
    _gerar(conv, {"me": 1})
    gerar, conferir = USUARIO[0], USUARIO[1]
    assert "Páginas da web lidas na pesquisa do resumo" in gerar and "fosfofrutoquinase" in gerar
    assert "[Glicólise passo a passo](https://bio.org/glicolise)" in gerar and "fosfofrutoquinase" in conferir


def test_pesos_seguem_as_areas_da_prova_anexada():
    areas = [{"area": "Matemática", "peso": 0.5}, {"area": "Física", "peso": 0.3}, {"area": "Química", "peso": 0.2}]
    topicos = ["Funções", "Probabilidade", "Cinemática", "Cinética química"]
    area_de = {"Funções": "Matemática", "Probabilidade": "matematica", "Cinemática": "Física"}   # Cinética: pelo título
    pesos = P._pesos(topicos, area_de, areas)
    assert pesos == pytest.approx({"Funções": 0.25, "Probabilidade": 0.25, "Cinemática": 0.3, "Cinética química": 0.2})
    seq = P._sequencia(topicos, pesos, 20)
    from collections import Counter
    assert Counter(seq) == {"Funções": 5, "Probabilidade": 5, "Cinemática": 6, "Cinética química": 4}
    assert seq[:4] != ["Cinemática"] * 4   # intercalado, não em bloco
    assert P._pesos(topicos, {}, []) == {t: 1.0 for t in topicos}   # sem áreas: todos iguais
    assert P._pesos(["Outro"], {}, areas) == {"Outro": 1.0}          # nenhum casou: todos iguais


def test_divisao_por_area_antes_do_topico():
    # o caso do simulado: 4 tópicos de Ciências, 2 de Matemática, metade/metade -> 5 e 5 (por tópico dava 6 e 4)
    topicos = ["Cinemática", "Química", "Óptica", "Funções", "Probabilidade", "Malária"]
    area_de = {"Funções": "Matemática", "Probabilidade": "Matemática"} | {t: "Ciências da Natureza" for t in
                                                                         ("Cinemática", "Química", "Óptica", "Malária")}
    areas = [{"area": "Ciências da Natureza", "peso": 0.5}, {"area": "Matemática", "peso": 0.5}]
    pesos = P._pesos(topicos, area_de, areas)
    grupos = {t: a or t for t, a in P._casar(topicos, area_de, areas).items()}
    seq = P._sequencia(topicos, pesos, 10, grupos)
    from collections import Counter
    n = Counter(grupos[t] for t in seq)
    assert n == {"Ciências da Natureza": 5, "Matemática": 5}
    assert Counter(seq)["Funções"] in (2, 3) and Counter(seq)["Cinemática"] >= 1   # dentro da área, revezam
    assert P._sequencia(["A", "B"], {"A": 1, "B": 1}, 4) == ["A", "B", "A", "B"]   # sem grupos: como antes


def test_verificador_que_ve_defeito_manda_reescrever(monkeypatch):
    vez = {"n": 0}

    async def chat_stream(provider, model, messages, *a, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        if system.startswith("Você é um professor que elabora"):
            vez["n"] += 1
            yield ("content", json.dumps({"questoes": [_me(vez["n"])]}))
        else:
            ids = re.findall(r"^\[(q\d+)\]", user, re.M)
            certo = _correto_do_prompt(user, ids)
            problema = "pede o mínimo de uma parábola voltada para baixo" if vez["n"] == 1 else ""
            yield ("content", json.dumps({"respostas": [{"id": i, "conta": "x", "resposta": c, "problema": problema}
                                                        for i, c in certo.items()]}))
        yield ("done", {})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", chat_stream)
    pid = _gerar(_estudo(), {"me": 1})
    e = _cheia(pid)
    assert vez["n"] == 2 and e["questoes"][0]["enunciado"].startswith("Questão 2")   # a 1ª foi reescrita
    assert e["descartadas"][0]["problema"] == "pede o mínimo de uma parábola voltada para baixo"


def test_lote_seguinte_sabe_o_que_a_prova_ja_tem(monkeypatch):
    _fake(monkeypatch, verificar=_correto_do_prompt)
    _gerar(_estudo(), {"me": 6})   # dois lotes: 5 + 1
    gerar = [u for u, s in zip(USUARIO, SISTEMA) if s.startswith("Você é um professor que elabora")]
    assert "Questões que a prova já tem" not in gerar[0]
    assert "Questões que a prova já tem" in gerar[1] and "Questão 10: onde ocorre" in gerar[1]
    assert "vírgula decimal" in SISTEMA[0] and "grau maior que 2" in SISTEMA[0]   # o _estudo é ENEM


def test_areas_do_perfil_viram_pesos_da_prova(monkeypatch):
    conv = _estudo()
    with db.session() as s:
        m = s.scalars(select(db.Message).where(db.Message.conversation_id == conv)).first()
        e = copy.deepcopy(m.meta["estudos"])   # mexer no dict carregado não grava: o JSON não é MutableDict
        e["perfil"] = {**e["perfil"], "areas": [{"area": "Biologia", "peso": 0.75}, {"area": "Química", "peso": 0.25}]}
        e["topicos"] = [{**t, "area": "Biologia" if t["titulo"] == "Glicólise" else "Química"} for t in e["topicos"]]
        m.meta = {"estudos": e}
        s.commit()
    cfg = P._config({"me": 8}, P._contexto(conv))
    assert [q["topico"] for q in P._planejar(cfg)].count("Glicólise") == 6


def test_regras_enem_pela_banca_ou_pelo_objetivo(monkeypatch):
    _fake(monkeypatch, verificar=_correto_do_prompt)
    _gerar(_estudo(), {"me": 1})   # o perfil do _estudo é ENEM
    assert "Padrão ENEM" in SISTEMA[0] and "calculadora" in SISTEMA[0]
    conv = _estudo()
    with db.session() as s:
        m = s.scalars(select(db.Message).where(db.Message.conversation_id == conv)).first()
        m.meta = {"estudos": {**m.meta["estudos"], "perfil": {"banca": "FUVEST"}, "preferencias": {"objetivo": "faculdade"}}}
        s.commit()
    _fake(monkeypatch, verificar=_correto_do_prompt)
    _gerar(conv, {"me": 1})
    assert "Padrão ENEM" not in SISTEMA[0]


def test_verificador_usa_o_modelo_de_conferencia_quando_escolhido(monkeypatch):
    _fake(monkeypatch, verificar=_correto_do_prompt)
    pid = _gerar(_estudo(), {"me": 2}, ex_provider="fake", ex_model="outro")
    assert MODELOS == ["m", "outro"]   # escreve com um, confere com o outro
    assert _cheia(pid)["stats"]["extrator"] == "outro"
    _fake(monkeypatch, verificar=_correto_do_prompt)
    _gerar(_estudo(), {"me": 2})
    assert MODELOS == ["m", "m"]   # sem escolha: o mesmo (o automático da leitura é pequeno demais para isso)
    assert "conta" in SISTEMA[1] and "menor, maior, exceto" in SISTEMA[1]


def test_motivo_do_descarte_traz_a_conta_do_verificador(monkeypatch):
    async def fala(provider, model, messages, *a, **kw):
        if messages[0]["content"].startswith("Você resolve"):
            certo = _correto_do_prompt(messages[1]["content"], re.findall(r"^\[(q\d+)\]", messages[1]["content"], re.M))
            yield ("content", json.dumps({"respostas": [{"id": i, "conta": "56,2° não basta", "resposta": "ABCD"[("ABCD".index(c) + 1) % 4]}
                                                        for i, c in certo.items()]}))
        else:
            yield ("content", json.dumps({"questoes": [{**_me(1), "correta": 1}]}))
        yield ("done", {})

    monkeypatch.setattr(pesquisa.llm, "chat_stream", fala)
    pid = _gerar(_estudo(), {"me": 1})
    motivo = _cheia(pid)["planejadas"][0]["motivo"]
    assert motivo.startswith("o verificador chegou a outra resposta") and "56,2° não basta" in motivo


def test_sem_resumo_nem_material_nao_da_prova():
    with pytest.raises(ToolError, match="Anexe material"):
        P._contexto(_estudo(resumo=False))


# ------------------------------------------------------------------ gerar


def test_gera_confere_e_esconde_o_gabarito(monkeypatch):
    chamados = _fake(monkeypatch, verificar=_correto_do_prompt)
    conv = _estudo()
    pid = _gerar(conv, {"me": 6, "vf": 2, "disc": 1})
    e = estudos.estado(pid)
    assert e["status"] == "pronto", e["aviso"]
    assert len(e["questoes"]) == 9 and [q["id"] for q in e["questoes"]] == [f"q{i}" for i in range(1, 10)]
    assert chamados.count("gerar") == 4 and chamados.count("verificar") == 3   # lotes: 5 me, 1 me, 2 vf, 1 disc (sem conferência)
    assert not e["revelada"] and all("correta" not in q and "explicacao" not in q for q in e["questoes"])
    cheia = _cheia(pid)["questoes"]
    assert all(q["verificada"] for q in cheia if q["tipo"] != "disc")
    assert all(q["alternativas"][q["correta"]].endswith("-1") for q in cheia if q["tipo"] == "me")
    assert {q["correta"] for q in cheia if q["tipo"] == "me"} != {1}   # embaralhou: a correta não fica onde o modelo pôs (B)
    assert [p["titulo"] for p in P.lista(conv)] == ["Prova 1"]


def test_verificador_que_discorda_regera_e_depois_descarta(monkeypatch):
    # verificador que sempre marca a letra seguinte à certa: discorda de tudo, nas duas rodadas
    def errado(user, ids):
        return {i: "ABCDE"[("ABCDE".index(c) + 1) % 4] for i, c in _correto_do_prompt(user, ids).items()}

    chamados = _fake(monkeypatch, verificar=errado)
    pid = _gerar(_estudo(), {"me": 4})
    e, plano = estudos.estado(pid), _cheia(pid)["planejadas"]
    assert chamados.count("gerar") == 2   # a 1ª rodada e a refeita
    assert [p["status"] for p in plano] == ["descartada"] * 4 and not e["questoes"]
    assert all(p["motivo"].startswith("o verificador chegou a outra resposta (") for p in plano)
    assert e["status"] == "erro" and "verificador chegou a outra resposta" in e["aviso"]


def test_verificador_que_discorda_so_de_uma_fica_com_as_outras(monkeypatch):
    def quase(user, ids):
        certo = _correto_do_prompt(user, ids)
        return {i: ("ABCDE"[("ABCDE".index(c) + 1) % 4] if i == "q1" else c) for i, c in certo.items()}

    _fake(monkeypatch, verificar=quase)
    pid = _gerar(_estudo(), {"me": 3})
    e = estudos.estado(pid)
    assert [q["id"] for q in e["questoes"]] == ["q2", "q3"] and "ficaram de fora" in e["aviso"]


def test_formato_errado_na_primeira_vez_e_refeito(monkeypatch):
    vez = {"n": 0}

    def gerar(pedidas):
        vez["n"] += 1
        return [{"tipo": "me", "enunciado": "curto"}] * len(pedidas) if vez["n"] == 1 else [_me(i) for i in range(len(pedidas))]

    chamados = _fake(monkeypatch, gerar=gerar)
    pid = _gerar(_estudo(), {"me": 2})
    e = estudos.estado(pid)
    assert e["status"] == "pronto" and len(e["questoes"]) == 2 and chamados.count("gerar") == 2


def test_nenhuma_questao_valida_e_erro(monkeypatch):
    _fake(monkeypatch, gerar=lambda pedidas: [])
    e = estudos.estado(_gerar(_estudo(), {"vf": 2}))
    assert e["status"] == "erro" and not e["questoes"]


def test_repetida_nao_entra_duas_vezes(monkeypatch):
    _fake(monkeypatch, gerar=lambda pedidas: [_vf(1)] * len(pedidas))
    e = estudos.estado(_gerar(_estudo(), {"vf": 3}))
    assert len(e["questoes"]) == 1


# ------------------------------------------------------------------ entregar


def _prova_pronta(monkeypatch, config_=None) -> tuple[int, list[dict]]:
    _fake(monkeypatch, verificar=_correto_do_prompt)
    pid = _gerar(_estudo(), config_ or {"me": 2, "vf": 1, "disc": 1})
    return pid, _cheia(pid)["questoes"]


def test_entrega_corrige_fechadas_e_discursiva(monkeypatch):
    pid, qs = _prova_pronta(monkeypatch)
    me1, me2, vf, disc = qs
    respostas = {me1["id"]: me1["correta"], me2["id"]: (me2["correta"] + 1) % 4, vf["id"]: "V", disc["id"]: "Regenera NAD+."}

    async def main():
        msg = P.entregar(pid, respostas, 125, "fake", "m")
        assert estudos.estado(msg["id"])["status"] == "rodando"
        await _acabar()
        return estudos.estado(msg["id"])

    t = asyncio.run(main())
    c = t["correcao"]
    assert c[me1["id"]]["certa"] and not c[me2["id"]]["certa"] and c[vf["id"]]["certa"] is (vf["correta"] is True)
    assert c[disc["id"]]["pontos"] == 1.5 and c[disc["id"]]["certa"] is None and "Faltou" in c[disc["id"]]["feedback"]
    assert t["max"] == 5 and t["pontos"] == pytest.approx(1 + (1 if vf["correta"] else 0) + 1.5)
    assert t["nota"] == round(10 * t["pontos"] / 5, 1) and t["segundos"] == 125
    assert len(t["questoes"]) == 4 and "correta" in t["questoes"][0]   # a entrega vem com o gabarito
    assert estudos.estado(pid)["revelada"] and "explicacao" in estudos.estado(pid)["questoes"][0]
    assert {x["topico"] for x in t["por_topico"]} == {"Glicólise", "Fermentação"}


def test_entrega_so_de_fechadas_nao_chama_modelo(monkeypatch):
    pid, qs = _prova_pronta(monkeypatch, {"me": 2})
    msg = P.entregar(pid, {qs[0]["id"]: "A"})   # sem modelo: não precisa
    t = estudos.estado(msg["id"])
    assert t["status"] == "pronto" and not estudos._RUNS and t["max"] == 2
    assert t["correcao"][qs[1]["id"]]["resposta"] is None and t["correcao"][qs[1]["id"]]["pontos"] == 0


def test_discursiva_em_branco_nao_vai_para_o_modelo(monkeypatch):
    pid, qs = _prova_pronta(monkeypatch, {"disc": 1})
    t = estudos.estado(P.entregar(pid, {qs[0]["id"]: "   "})["id"])
    assert t["status"] == "pronto" and t["correcao"][qs[0]["id"]]["feedback"] == "Sem resposta."


def test_entregar_prova_que_nao_esta_pronta():
    conv = _estudo()
    msg = estudos._save(conv, role="assistant", content="", status="running",
                        meta={"estudos": {"tipo": "prova", "questoes": []}})
    with pytest.raises(ToolError, match="não está pronta"):
        P.entregar(msg.id, {})


def test_apagar_prova_leva_as_tentativas(monkeypatch):
    pid, qs = _prova_pronta(monkeypatch, {"me": 1})
    tid = P.entregar(pid, {})["id"]
    P.apagar_prova(pid)
    with db.session() as s:
        assert s.get(db.Message, pid) is None and s.get(db.Message, tid) is None


# ------------------------------------------------------------------ Claude via MCP


def test_claude_gera_a_prova_pedida(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    conv = _estudo()
    pid = P.start(conv, {"me": 2, "vf": 1, "instrucoes": "só cálculo"}, estudos.MOTOR_CLAUDE)["id"]
    assert estudos.estado(pid)["status"] == "aguardando" and not estudos._RUNS
    texto = asyncio.run(estudos.mcp_pedidos(0))
    assert f"PEDIDO {pid} — prova" in texto and "2 múltipla escolha, 1 verdadeiro ou falso" in texto and "só cálculo" in texto
    erro = P.mcp_salvar_prova([_me(1), {"tipo": "vf", "enunciado": "sem gabarito aqui"}], pedido_id=pid)
    assert erro.startswith("ERRO") and "questão 2" in erro and estudos.estado(pid)["status"] == "aguardando"
    ok = P.mcp_salvar_prova([_me(1), _me(2), _vf(3)], pedido_id=pid, modelo="Claude Opus 5.5")
    assert "3 questões" in ok
    e = estudos.estado(pid)
    assert e["status"] == "pronto" and len(e["questoes"]) == 3 and e["stats"]["escritor"] == "Claude Opus 5.5"
    assert "já atendido" in P.mcp_salvar_prova([_me(1)], pedido_id=pid)
    assert "Resumo" in estudos.mcp_ler_resumo(conv) and "## 1. Glicólise" in estudos.mcp_ler_resumo(conv)


def test_claude_corrige_a_discursiva(monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVIDOR", True)
    conv = _estudo()
    P.mcp_salvar_prova([_disc(1), _vf(2)], conv_id=conv)
    prova = P.lista(conv)[0]
    qs = _cheia(prova["message_id"])["questoes"]
    tid = P.entregar(prova["message_id"], {"q1": "Regenera NAD+ para a glicólise", "q2": "F"}, 0, estudos.MOTOR_CLAUDE)["id"]
    assert estudos.estado(tid)["status"] == "aguardando"
    texto = asyncio.run(estudos.mcp_pedidos(0))
    assert f"PEDIDO {tid} — corrigir" in texto and "Regenera NAD+ para a glicólise" in texto and "[q2]" not in texto
    assert "faltou corrigir q1" in P.mcp_corrigir(tid, [])
    assert "nota" in P.mcp_corrigir(tid, [{"questao_id": "q1", "pontos": "2", "feedback": "Completa."}])
    t = estudos.estado(tid)
    assert t["status"] == "pronto" and t["correcao"]["q1"]["pontos"] == 2 and t["correcao"]["q1"]["certa"]
    assert t["pontos"] == 2 + (0 if qs[1]["correta"] else 1)
    visto = P.mcp_ver_prova(prova["message_id"])
    assert "Resposta-modelo" in visto and f"Entrega {tid}" in visto
    assert "prova " in estudos.mcp_abrir(conv) and "tentativa" in estudos.mcp_abrir(conv)


# ------------------------------------------------------------------ API


def test_api_da_prova(monkeypatch):
    from app.main import app
    _fake(monkeypatch, verificar=_correto_do_prompt)
    conv = _estudo()

    def sse(r):
        return [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]

    with TestClient(app) as c:
        with c.stream("POST", f"/api/estudos/{conv}/prova", json={"config": {"me": 2, "vf": 1}, "provider": "fake", "model": "m"}) as r:
            eventos = sse(r)
        assert eventos[-1]["status"] == "pronto" and "correta" not in eventos[-1]["questoes"][0]
        pid = eventos[-1]["message_id"]
        assert c.get(f"/api/estudos/execucao/{pid}").json()["revelada"] is False
        assert c.post(f"/api/estudos/{conv}/prova", json={"config": {}, "provider": "fake", "model": "m"}).status_code == 400
        with c.stream("POST", f"/api/estudos/prova/{pid}/entregar", json={"respostas": {"q1": 0}, "segundos": 30}) as r:
            t = sse(r)[-1]
        assert t["status"] == "pronto" and t["max"] == 3 and len(t["questoes"]) == 3
        p = c.get(f"/api/estudos/{conv}").json()
        assert p["provas"][0]["tentativas"][0]["message_id"] == t["message_id"]
        assert c.delete(f"/api/estudos/prova/{pid}").json()["ok"]
        assert c.get(f"/api/estudos/{conv}").json()["provas"] == []


def test_ferramentas_mcp_da_prova_existem():
    from app import mcp_servidor
    nomes = {t.name for t in mcp_servidor.SERVIDOR._tool_manager.list_tools()}
    assert {"estudos_ler_resumo", "estudos_salvar_prova", "estudos_corrigir", "estudos_ver_prova"} <= nomes

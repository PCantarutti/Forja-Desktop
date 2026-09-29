import asyncio
import json
import re

import pytest

from app import db, design, design_html, mirror
from app.tools import ToolError

M = {k: {"provider": "ollama", "model": "m"} for k in ("plano", "geracao", "edicao")}
DOC = """<!doctype html><html><head><style>
:root { --cor-primaria: #c75b12; --cor-fundo: #fff; }
.hero-cta { background: var(--cor-primaria); }
</style></head><body>
<section data-section="hero"><h1>Padaria</h1><p class="hero-apoio">Pão quente</p><a class="hero-cta">Comprar</a></section>
<section data-section="sobre"><h2>Sobre</h2><p>Desde 1998</p></section>
<footer data-section="rodape"><p>© 2026</p></footer>
</body></html>"""
PLANO = {"tipo": "site", "titulo": "Padaria Sol", "tokens": {"--cor-primaria": "#c75b12", "cor-fundo": "#fff", "--ruim": "x;}"},
         "secoes": [{"nome": "Hero", "objetivo": "chamar", "conteudo": "pão"}, {"nome": "sobre"}, {"nome": "rodapé"}]}


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    design._RUNS.clear()
    yield
    design._RUNS.clear()


def _projeto(html: str | None = DOC) -> int:
    with db.session() as s:
        c = db.Conversation(kind="design")
        s.add(c)
        s.commit()
        cid = c.id
    if html:
        design._nova_versao(cid, None, html, "inicial")
    return cid


def _fake(monkeypatch, responder, chamadas: list):
    """O modelo falso responde conforme o prompt de sistema (plano/secao/tokens/fragmento)."""
    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        tipo = re.match(r"<!-- (\w+)", messages[0]["content"]).group(1)
        chamadas.append((tipo, messages[1]["content"]))
        yield "reasoning", f"pensando em {tipo}"
        texto = responder(tipo, messages[1]["content"])
        for i in range(0, len(texto), 40):
            await asyncio.sleep(0)
            yield "content", texto[i:i + 40]
        yield "done", {"completion_tokens": 50, "prompt_tokens": 400}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)


def _esperar(coro_fn):
    async def main():
        r = coro_fn()
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return r
    return asyncio.run(main())


def _secao(nome: str) -> str:
    return f'<section data-section="{nome}"><div class="container"><h2 class="{nome}-t">Seção {nome}</h2></div></section>\n<style>.{nome}-t{{color:var(--cor-primaria)}}</style>'


# ------------------------------------------------------------------ roteamento

@pytest.mark.parametrize("pedido, esperado", [
    ("muda a cor primária para azul", ("tokens", None)),
    ("troca a fonte por uma serifada", ("tokens", None)),
    ("deixa tudo mais escuro", ("tokens", None)),
    ("refaz o hero com mais impacto", ("secao", "hero")),
    ("deixa o rodapé mais escuro", ("secao", "rodape")),
    ("adiciona uma seção de depoimentos", ("secao", "depoimentos")),
    ("inclui nova seção com perguntas frequentes", ("secao", "perguntas")),
    ("reescreve o documento inteiro em inglês", ("documento", None)),
    ("começa de novo do zero", ("documento", None)),
])
def test_rotear(pedido, esperado):
    assert design.rotear(pedido, True, ["hero", "sobre", "rodape"]) == esperado


def test_rotear_casos_fixos():
    assert design.rotear("qualquer coisa", False, []) == ("plano", None)
    assert design.rotear("muda a cor", True, ["hero"], ["a1"]) == ("fragmento", None)


# ------------------------------------------------------------------ tokens

def test_cor_primaria_muda_so_o_root(monkeypatch):
    conv = _projeto()
    chamadas = []
    _fake(monkeypatch, lambda t, u: '{"tokens": {"--cor-primaria": "#1d4ed8", "--inventado": "red"}}', chamadas)
    antes = design.projeto(conv)["html"]
    msg = _esperar(lambda: design.start(conv, "muda a cor primária para azul", M))
    depois = design.projeto(conv)["html"]
    assert [c[0] for c in chamadas] == ["tokens"]
    assert "Padaria" not in chamadas[0][1]                      # só o :root foi ao modelo
    assert "--cor-primaria: #1d4ed8" in depois and "--inventado" not in depois
    raiz = re.compile(r":root\s*\{[^}]*\}")
    assert raiz.sub("", antes) == raiz.sub("", depois)          # fora do :root, nada mudou
    est = design.estado(msg["id"])
    assert est["rota"] == "tokens" and len(est["patches"]) == 1 and est["patches"][0]["html"].startswith("<style")


# ------------------------------------------------------------------ comentários

def test_tres_comentarios_numa_chamada_so(monkeypatch):
    conv = _projeto()
    html = design.projeto(conv)["html"]
    fid = lambda tag: re.search(rf'<{tag}[^>]*data-fid="(\w+)"', html).group(1)   # noqa: E731
    h1, p, h2 = fid("h1"), fid("p"), fid("h2")
    for f, t in ((h1, "título maior"), (p, "texto em itálico"), (h2, "sublinhar")):
        design.comentar(conv, [f], t)
    orfao = design.comentar(conv, [fid("footer")], "sumir")["comentarios"][-1]["id"]
    # o rodapé some numa versão: o comentário dele vira órfão em vez de quebrar
    design._nova_versao(conv, None, re.sub(r"<footer.*?</footer>", "", design.projeto(conv)["html"], flags=re.S), "sem rodapé")
    cs = design.projeto(conv)["comentarios"]
    assert [c["orfao"] for c in cs] == [False, False, False, True]

    chamadas = []
    def responder(tipo, user):
        fids = re.findall(r"Elemento alvo data-fid=(\w+)", user)
        return json.dumps({"patches": [{"fid": f, "html": design_html.outer(design.projeto(conv)["html"], f).replace(">", ' class="ok">', 1)} for f in fids]})
    _fake(monkeypatch, responder, chamadas)
    ids = [c["id"] for c in cs]
    _esperar(lambda: design.start(conv, "", M, comentarios=ids))
    assert len(chamadas) == 1 and chamadas[0][0] == "fragmento"   # uma chamada só
    assert all(t in chamadas[0][1] for t in ("título maior", "texto em itálico", "sublinhar"))
    assert "sumir" not in chamadas[0][1]
    p_ = design.projeto(conv)
    assert [c["status"] for c in p_["comentarios"]] == ["aplicado"] * 3 + ["pendente"]
    assert {c["versao_aplicada"] for c in p_["comentarios"][:3]} == {p_["atual"]}
    assert p_["html"].count('class="ok"') == 3
    design.descartar(conv, orfao)
    assert design.projeto(conv)["comentarios"][-1]["status"] == "descartado"
    with pytest.raises(ToolError):
        design.comentar(conv, ["naoexiste"], "x")


# ------------------------------------------------------------------ texto inline

def test_texto_por_duplo_clique_nao_chama_modelo(monkeypatch):
    conv = _projeto()
    chamadas = []
    _fake(monkeypatch, lambda t, u: "", chamadas)
    html = design.projeto(conv)["html"]
    h1 = re.search(r'<h1 data-fid="(\w+)"', html).group(1)
    r = design.editar_texto(conv, h1, 'Padaria <b contenteditable="true">Sol</b>')
    assert chamadas == []
    assert r["projeto"]["atual"] == 2 and "<h1 data-fid" in r["projeto"]["html"]
    novo = design_html.outer(r["projeto"]["html"], h1)
    assert "Sol</b>" in novo and "contenteditable" not in novo and r["fim"]["patches"][0]["html"] == novo
    assert r["projeto"]["html"].replace(novo, "") == html.replace(design_html.outer(html, h1), "")
    for ruim in ("<script>x</script>", "abre <b>sem fechar", "</h1><h1>dois"):
        with pytest.raises(ToolError):
            design.editar_texto(conv, h1, ruim)
    assert design.projeto(conv)["total"] == 2


# ------------------------------------------------------------------ plano → esqueleto → seções

def test_plano_aprovado_gera_secao_por_secao(monkeypatch):
    conv = _projeto(None)
    chamadas = []
    _fake(monkeypatch, lambda t, u: json.dumps(PLANO) if t == "plano" else _secao(re.search(r'data-section="(\w+)"', u).group(1)), chamadas)
    msg = _esperar(lambda: design.start(conv, "landing de padaria", M))
    est = design.estado(msg["id"])
    assert est["status"] == "plano" and [s["nome"] for s in est["plano"]["secoes"]] == ["hero", "sobre", "rodape"]
    assert "--ruim" not in est["plano"]["tokens"] and est["plano"]["tokens"]["--cor-fundo"] == "#fff"
    assert design.projeto(conv)["total"] == 0                   # plano não é versão

    plano = {**est["plano"], "secoes": est["plano"]["secoes"][:2]}   # o usuário tirou o rodapé no card
    _esperar(lambda: design.aprovar(msg["id"], plano, M))
    assert [c[0] for c in chamadas] == ["plano", "secao", "secao"]
    assert "Seção a escrever: data-section=\"sobre\"" in chamadas[2][1]
    p = design.projeto(conv)
    assert p["atual"] == 1 and p["secoes"] == ["hero", "sobre"]
    assert "data-placeholder" not in p["html"] and ".hero-t{color" in p["html"]
    ult = p["mensagens"][-1]
    assert ult["rota"] == "etapas" and len(ult["stats"]) == 3 and "pensando em secao" in ult["thinking"]
    assert ult["stats"][0]["tokens"] == 50 and ult["stats"][0]["model"] == "m"
    with pytest.raises(ToolError):
        design.aprovar(msg["id"], plano, M)                    # já aprovado


def test_cancelar_etapas_mantem_o_que_ja_saiu(monkeypatch):
    conv = _projeto(None)
    chamadas = []
    _fake(monkeypatch, lambda t, u: json.dumps(PLANO) if t == "plano" else _secao(re.search(r'data-section="(\w+)"', u).group(1)), chamadas)
    msg = _esperar(lambda: design.start(conv, "landing", M))
    plano = design.estado(msg["id"])["plano"]

    async def main():
        design.aprovar(msg["id"], plano, M)
        while design._RUNS[msg["id"]]["n"] < 1:
            await asyncio.sleep(0)
        design.cancelar(msg["id"])
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
    asyncio.run(main())
    p = design.projeto(conv)
    assert p["total"] == 1 and p["secoes"] == ["hero"] and "cancelado" in p["mensagens"][-1]["content"]
    assert "data-placeholder" not in p["html"]


def test_secao_nova_entra_antes_do_rodape_e_refazer_troca_so_ela(monkeypatch):
    conv = _projeto()
    chamadas = []
    _fake(monkeypatch, lambda t, u: _secao(re.search(r'data-section="(\w+)"', u).group(1)), chamadas)
    msg = _esperar(lambda: design.start(conv, "adiciona uma seção de depoimentos", M))
    p = design.projeto(conv)
    assert p["secoes"] == ["hero", "sobre", "depoimentos", "rodape"]
    assert design.estado(msg["id"])["patches"] == []            # nova: a tela recarrega
    antes = p["html"]
    msg = _esperar(lambda: design.start(conv, "refaz o hero", M))
    assert "HTML atual da seção" in chamadas[-1][1] and "Padaria" in chamadas[-1][1]
    depois = design.projeto(conv)["html"]
    hero = lambda h: design_html.outer(h, re.search(r'<section data-section="hero" data-fid="(\w+)"', h).group(1))  # noqa: E731
    assert depois.split("</style>")[1].replace(hero(depois), "") == antes.split("</style>")[1].replace(hero(antes), "")
    assert design.estado(msg["id"])["patches"][0]["html"] == hero(depois)


def test_modelo_por_etapa(monkeypatch):
    conv = _projeto()
    vistos = []

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append((model, effort))
        yield "content", '{"tokens": {"--cor-primaria": "#000"}}'
        yield "done", {}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)
    modelos = {"plano": {"provider": "p", "model": "grande"}, "geracao": {"provider": "p", "model": "grande"},
               "edicao": {"provider": "p", "model": "local"}}
    _esperar(lambda: design.start(conv, "muda a cor primária", modelos, esforco="alto"))
    assert vistos == [("local", "alto")]
    with pytest.raises(ToolError):
        design.start(conv, "muda a cor", {"edicao": {"provider": "", "model": ""}})

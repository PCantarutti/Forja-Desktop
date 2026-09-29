import asyncio
import json
import re

import pytest

from app import db, design, design_export, design_html, design_imagens, design_sistema, lotes, mirror
from app.tools import ToolError

SKILL = pytest.mark.skipif(not design_imagens.DISPONIVEL, reason="sem a fila de slots da skill gerar-imagens")

M = {k: {"provider": "ollama", "model": "m"} for k in ("plano", "geracao", "edicao")}
DOC = """<!doctype html><html><head><title>x</title><style>
:root { --cor-primaria: #c75b12; --esp-4: 1.5rem; --fonte-texto: Georgia, serif; }
.hero-cta { background: var(--cor-primaria); }
</style></head><body>
<section data-section="hero"><h1>Padaria</h1>
<img data-slot="hero-paes-8027" data-prompt="fresh sourdough loaves on a wooden table" width="1344" height="768" alt="Pães">
<a class="hero-cta">Comprar</a></section>
<section data-section="sobre"><img data-slot="forno-1234" data-prompt="baker at a wood oven" width="768" height="1024" alt="Forno"></section>
</body></html>"""


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(design_sistema, "ARQUIVO", tmp_path / "sistemas.json")
    monkeypatch.setattr(design_imagens.config, "DATA_DIR", tmp_path / "dados")
    design._RUNS.clear()
    yield
    design._RUNS.clear()


def _projeto(html: str | None = DOC) -> int:
    with db.session() as s:
        c = db.Conversation(kind="design", title="Padaria Sol")
        s.add(c)
        s.commit()
        cid = c.id
    if html:
        design._nova_versao(cid, None, html, "inicial")
    return cid


def _png(caminho, cor=(200, 120, 40)):
    from PIL import Image
    caminho.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), cor).save(caminho)


def _esperar(fn):
    async def main():
        r = fn()
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return r
    return asyncio.run(main())


# ------------------------------------------------------------------ 4. imagens pela skill gerar-imagens

def test_slots_ganham_provisorio_e_o_modelo_nao_ve_base64():
    conv = _projeto()
    html = design.projeto(conv)["html"]
    sl = design_imagens.slots(html)
    assert [(s["nome"], s["status"]) for s in sl] == [("hero-paes-8027", "pendente"), ("forno-1234", "pendente")]
    assert all(s["src"].startswith("data:image/svg+xml") for s in sl)
    assert 'src=""' in design_imagens.enxugar(html) and "data:image" not in design_imagens.enxugar(html)
    p = design.projeto(conv)
    assert p["imagens"] == {"total": 2, "pendentes": 2, "nomes": ["hero-paes-8027", "forno-1234"],
                            "disponivel": design_imagens.DISPONIVEL, "conversa": None}


@SKILL
def test_gerar_imagens_registra_pela_ferramenta_da_skill_e_embute_no_fim():
    conv = _projeto()
    r = design_imagens.registrar(conv)
    assert r["kind"] == "imagem"
    with db.session() as s:
        tool = s.query(db.Message).filter(db.Message.conversation_id == conv, db.Message.role == "tool").one()
        img_conv = s.get(db.Conversation, r["id"])
        pedido = tool.meta["imagens_pendentes"]
        assert tool.name == "imagens_pendentes" and img_conv.origem["conv_id"] == conv
    raiz = design_imagens.pasta(conv)
    assert [s["rel"] for s in pedido["slots"]] == ["img/hero-paes-8027.png", "img/forno-1234.png"]
    assert (pedido["slots"][0]["largura"], pedido["slots"][0]["altura"]) == (1344, 768)
    from app import slots as projeto
    assert projeto.eh_placeholder(raiz / "img" / "hero-paes-8027.png")        # provisório da skill
    assert 'src="img/hero-paes-8027.png"' in (raiz / "index.html").read_text("utf-8")
    assert design_imagens.registrar(conv)["id"] == r["id"]                   # a mesma conversa de Imagens

    assert design_imagens.embutir(conv) is None                              # só provisório: nada muda
    _png(raiz / "img" / "hero-paes-8027.png")
    lotes._avisar(r["id"], "Imagens do site geradas")                        # o aviso do fim do lote
    p = design.projeto(conv)
    assert p["atual"] == 2 and p["mensagens"][-1]["rota"] == "imagens"
    sl = {s["nome"]: s for s in design_imagens.slots(p["html"])}
    assert sl["hero-paes-8027"]["status"] == "pronta" and sl["hero-paes-8027"]["src"].startswith("data:image/webp;base64,")
    assert sl["forno-1234"]["status"] == "pendente" and p["imagens"]["pendentes"] == 1
    assert design_imagens.embutir(conv) is None                              # de novo: nada mudou


def _com_foto(tmp_path) -> int:
    """Projeto com a foto do hero já pronta (o que o fim do lote faria), sem depender da fila."""
    _png(tmp_path / "foto.png")
    uri = design_imagens._data_uri(tmp_path / "foto.png")
    return _projeto(DOC.replace('data-slot="hero-paes-8027"', f'data-slot="hero-paes-8027" src="{uri}" data-slot-status="pronta"'))


def test_edicao_depois_da_imagem_nao_perde_a_foto(monkeypatch, tmp_path):
    conv = _com_foto(tmp_path)
    html = design.projeto(conv)["html"]
    sec = re.search(r'<section data-section="hero" data-fid="(\w+)"', html).group(1)
    vistos = []

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(messages[1]["content"])
        # o modelo devolve o <img> sem src, como o prompt manda
        yield "content", json.dumps({"patches": [{"fid": sec, "html": '<section data-section="hero"><h1>Nova</h1>'
                                                  '<img data-slot="hero-paes-8027" data-prompt="x" width="1344" height="768"></section>'}]})
        yield "done", {}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)
    _esperar(lambda: design.start(conv, "título novo", M, [sec]))
    assert "base64" not in vistos[0]
    novo = {s["nome"]: s for s in design_imagens.slots(design.projeto(conv)["html"])}
    assert novo["hero-paes-8027"]["status"] == "pronta" and "webp;base64" in novo["hero-paes-8027"]["src"]


# ------------------------------------------------------------------ 1. painel de ajustes

def test_ajuste_de_tokens_sem_modelo(monkeypatch):
    conv = _projeto()
    chamou = []
    monkeypatch.setattr(design.llm, "chat_stream", lambda *a, **k: chamou.append(1))
    antes = design.projeto(conv)["html"]
    r = design.ajustar_tokens(conv, {"--cor-primaria": "#1d4ed8", "--esp-4": "2rem"})
    depois = r["projeto"]["html"]
    assert not chamou and r["projeto"]["rascunho"]["mudancas"] == 2 and r["projeto"]["total"] == 1
    assert "--cor-primaria: #1d4ed8" in depois and "--esp-4: 2rem" in depois
    raiz = re.compile(r":root\s*\{[^}]*\}")
    assert raiz.sub("", antes) == raiz.sub("", depois)
    assert r["fim"]["patches"][0]["html"].startswith("<style")
    with pytest.raises(ToolError):
        design.ajustar_tokens(conv, {"--inventado": "red"})
    with pytest.raises(ToolError):
        design.ajustar_tokens(conv, {"--cor-primaria": "red; } body {display:none"})


# ------------------------------------------------------------------ 6. design system do código

def _projeto_codigo(tmp_path):
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "tema.css").write_text(
        ":root { --brand: #0f766e; --radius: 12px; }\n.btn-primary { background: #0f766e; border-radius: 12px; }\n"
        ".card { box-shadow: 0 2px 8px #0002; font-family: Inter, sans-serif; }\n", "utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lixo.css").write_text(".btn-lixo{color:#ff00ff}", "utf-8")
    return tmp_path


def test_design_system_extraido_do_codigo_e_aplicado(monkeypatch, tmp_path):
    raiz = _projeto_codigo(tmp_path / "proj")
    r = design_sistema.resumo(raiz)
    assert "--brand: #0f766e" in r and "btn-primary" in r and "#0f766e" in r and "lixo" not in r
    vistos = []
    resposta = {"nome": "Verde", "tokens": {"--cor-primaria": "#0f766e", "--cor-fundo": "#fff", "--cor-texto": "#111",
                                            "--raio-md": "12px", "--ruim": "x;}"},
                "css": ".btn{background:var(--cor-primaria);border-radius:var(--raio-md)}", "notas": "Cantos de 12px."}

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        vistos.append(messages)
        yield "content", json.dumps(resposta)
        yield "done", {}
    monkeypatch.setattr(design_sistema.llm, "chat_stream", chat_stream)
    ds = asyncio.run(design_sistema.extrair(str(raiz), "", M["edicao"]))
    assert "tema.css" not in vistos[0][1]["content"] and "--brand" in vistos[0][1]["content"]   # só o resumo vai
    assert ds["nome"] == "Verde" and "--ruim" not in ds["tokens"] and design_sistema.listar()[0]["id"] == ds["id"]

    conv = _projeto()
    r = design.aplicar_sistema(conv, ds["id"])
    h = r["projeto"]["html"]
    assert "--cor-primaria: #0f766e" in h and "--cor-fundo: #fff" in h and "/* design system: Verde */" in h
    assert f'<meta name="forja-sistema" content="{ds["id"]}">' in h and r["projeto"]["sistema"] == ds["id"]
    assert design.aplicar_sistema(conv, ds["id"])["projeto"]["html"].count("/* design system: Verde */") == 1
    design_sistema.apagar(ds["id"])
    assert design_sistema.listar() == []


def test_plano_com_design_system_usa_os_tokens_dele(monkeypatch):
    ds = design_sistema._validar({"tokens": {"--cor-primaria": "#0f766e", "--cor-fundo": "#fff", "--cor-texto": "#111",
                                             "--raio-md": "12px"}, "css": ".btn{color:red}", "notas": "Sóbrio."}, "Verde", "C:/x")
    design_sistema._gravar([ds])
    conv = _projeto(None)
    vistos = []

    async def chat_stream(provider, model, messages, tools, num_ctx, effort=None, **kw):
        tipo = re.match(r"<!-- (\w+)", messages[0]["content"]).group(1)
        vistos.append((tipo, messages[1]["content"]))
        if tipo == "plano":
            yield "content", json.dumps({"titulo": "P", "tokens": {"--cor-primaria": "#ff0000"}, "estilo_imagens": "warm light",
                                         "secoes": [{"nome": "hero"}]})
        else:
            yield "content", '<section data-section="hero"><h1>Oi</h1></section>'
        yield "done", {}
    monkeypatch.setattr(design.llm, "chat_stream", chat_stream)
    msg = _esperar(lambda: design.start(conv, "landing", M, ds_id=ds["id"]))
    plano = design.estado(msg["id"])["plano"]
    assert "Design system obrigatório" in vistos[0][1]
    assert plano["tokens"]["--cor-primaria"] == "#0f766e" and plano["sistema"] == ds["id"]
    _esperar(lambda: design.aprovar(msg["id"], plano, M))
    h = design.projeto(conv)["html"]
    assert ".btn{color:red}" in h and 'name="forja-sistema"' in h and 'content="warm light"' in h
    assert "Notas de estilo: Sóbrio." in vistos[1][1]


# ------------------------------------------------------------------ 5. handoff para o Agente

def test_handoff_grava_o_pacote_na_pasta_do_projeto(tmp_path):
    conv = _com_foto(tmp_path)
    p = design.projeto(conv)
    proj = tmp_path / "meu-site"
    proj.mkdir()
    r = asyncio.run(design_export.handoff(p["html"], p["titulo"], str(proj)))
    dest = proj / r["rel"]
    assert r["rel"] == "design-handoff/padaria-sol" and r["rel"] in r["prompt"] and "README.md" in r["prompt"]
    index = (dest / "index.html").read_text("utf-8")
    assert "data-fid" not in index and "data-prompt" not in index and "webp;base64" in index
    assert (dest / "img" / "hero-paes-8027.webp").stat().st_size > 0
    assert 'src="img/hero-paes-8027.webp"' in (dest / "index.arquivos.html").read_text("utf-8")
    assert "--cor-primaria: #c75b12" in (dest / "tokens.css").read_text("utf-8")
    readme = (dest / "README.md").read_text("utf-8")
    assert "1. `hero`" in readme and "2. `sobre`" in readme and "stack DESTE projeto" in readme
    with pytest.raises(Exception):
        asyncio.run(design_export.handoff(p["html"], p["titulo"], str(tmp_path / "nao-existe")))

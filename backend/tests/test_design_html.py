import re

import pytest

from app import design_html as dh

DOC = """<!doctype html>
<html><head><title>x</title><style>
:root { --cor-primaria: #c75b12; --esp-2: 8px; }
.btn { background: var(--cor-primaria); padding: var(--esp-2); }
.card h2 { font-size: 20px; }
footer { color: gray; }
@media (max-width: 600px) { .btn { width: 100%; } footer { font-size: 12px; } }
</style></head>
<body>
<section data-section="hero" class="hero"><h1>Padaria</h1><p>Pão<br>quente</p>
<a class="btn" href="#c">Encomende</a><img src="data:," alt=""></section>
<footer data-section="rodape"><p>© 2026</p></footer>
</body></html>"""


def _fids(html: str) -> list[str]:
    return re.findall(r'data-fid="([^"]+)"', html)


def test_carimbo_marca_todos_e_e_idempotente():
    h = dh.carimbar(DOC)
    tags = {e["tag"] for e in dh.indexar(h) if "data-fid" in e["attrs"]}
    assert {"body", "section", "h1", "p", "a", "img", "footer", "style"} <= tags
    assert not tags & {"html", "head", "title", "br"}
    assert len(set(_fids(h))) == len(_fids(h))
    assert dh.carimbar(h) == h
    # só insere atributo: tirando os data-fid volta ao original
    assert re.sub(r' data-fid="[^"]+"', "", h) == DOC


def test_carimbo_preserva_existentes_e_resolve_repetidos():
    h = dh.carimbar(DOC)
    fid_a = dh.indexar(h)[[e["tag"] for e in dh.indexar(h)].index("a")]["attrs"]["data-fid"]
    copia = h.replace("</footer>", f'<a data-fid="{fid_a}" class="btn">cópia</a></footer>')
    h2 = dh.carimbar(copia)
    assert _fids(h2).count(fid_a) == 1
    assert set(_fids(h)) <= set(_fids(h2))
    novo = dh.carimbar(h.replace("Padaria</h1>", "Padaria<span>nova</span></h1>"))
    assert set(_fids(h)) <= set(_fids(novo)) and len(_fids(novo)) == len(_fids(h)) + 1


def _fid_de(h: str, tag: str) -> str:
    return next(e for e in dh.indexar(h) if e["tag"] == tag)["attrs"]["data-fid"]


def test_contexto_manda_so_o_fragmento():
    h = dh.carimbar(DOC)
    a = _fid_de(h, "a")
    ctx = dh.contexto(h, [a])
    assert dh.outer(h, a) in ctx
    assert "--cor-primaria: #c75b12" in ctx                  # tokens
    assert ".btn { background" in ctx and "width: 100%" in ctx  # regra e @media que afetam
    assert "Padaria" not in ctx and "© 2026" not in ctx      # conteúdo de fora não vai
    assert "footer { color" not in ctx
    assert re.search(r"Ancestrais: body\[data-fid=\w+\] > section\.hero\[data-fid=\w+\]", ctx)
    with pytest.raises(ValueError):
        dh.contexto(h, ["naoexiste"])


def test_patch_troca_so_o_alvo_e_preserva_os_outros_fids():
    h = dh.carimbar(DOC)
    a = _fid_de(h, "a")
    antes = {f: dh.outer(h, f) for f in _fids(h)}
    novo, mudou = dh.aplicar(h, {"patches": [{"fid": a, "html": '<a class="btn btn-verde" href="#c">Encomende <b>já</b></a>'}],
                                 "css": ".btn-verde { background: green; font-size: 1.3em; }"})
    assert mudou[0] == a and len(mudou) == 2                 # o alvo e o <style> mexido
    assert f'data-fid="{a}"' in dh.outer(novo, a) and "btn-verde" in dh.outer(novo, a)
    assert "<b data-fid=" in dh.outer(novo, a)               # elemento novo foi carimbado
    ancestrais = {_fid_de(h, "section"), _fid_de(h, "body")}   # contêm o alvo, mudam junto
    for f, html in antes.items():
        if f not in mudou and f not in ancestrais:
            assert dh.outer(novo, f) == html
    assert set(_fids(h)) <= set(_fids(novo))
    assert novo.index(".btn-verde") > novo.index("@media")   # css no fim do <style>


def test_patch_invalido_nao_muda_nada():
    h = dh.carimbar(DOC)
    a, h1 = _fid_de(h, "a"), _fid_de(h, "h1")
    for resp in ({"patches": [{"fid": "zzz", "html": "<a>x</a>"}]},
                 {"patches": [{"fid": a, "html": "<a>x</a><b>y</b>"}]},
                 {"patches": [{"fid": a, "html": "texto solto"}]},
                 {"patches": [{"fid": a, "html": "<a>ok</a>"}, {"fid": h1, "html": "<h1>ok"}], "css": "x{}</style>"},
                 {"patches": "nada"}, {}):
        with pytest.raises(ValueError):
            dh.aplicar(h, resp)
    sec = _fid_de(h, "section")
    with pytest.raises(ValueError):   # pai e filho no mesmo lote
        dh.aplicar(h, {"patches": [{"fid": sec, "html": "<section>a</section>"}, {"fid": a, "html": "<a>b</a>"}]})


def test_tokens_mexem_so_no_root():
    h = dh.carimbar(DOC)
    novo, mudou = dh.aplicar(h, {"tokens": {"--cor-primaria": "#1e40af", "cor-nova": "red"}})
    assert "--cor-primaria: #1e40af" in novo and "--cor-nova: red;" in novo
    assert novo.split("</style>")[1] == h.split("</style>")[1]   # corpo intacto
    assert mudou == [_fid_de(h, "style")]
    with pytest.raises(ValueError):
        dh.aplicar(h, {"tokens": {"--x": "red; } body { display:none"}})


def test_ler_json_repara_uma_vez():
    ok = {"patches": [{"fid": "a1", "html": "<a>x</a>"}]}
    assert dh.ler_json('{"patches": [{"fid": "a1", "html": "<a>x</a>"}]}') == ok
    assert dh.ler_json('Claro!\n```json\n{"patches": [{"fid": "a1", "html": "<a>x</a>"},],}\n```') == ok
    assert dh.ler_json('{"patches": [{"fid": "a1", "html": "<a>\nx</a>"}]}')["patches"][0]["html"] == "<a>\nx</a>"
    for ruim in ("sem json", '{"patches": [', "[1, 2]", '{"a": "sem fim}'):
        with pytest.raises(ValueError):
            dh.ler_json(ruim)

"""A logo SVG do design system entra inline onde o modelo marcou data-logo (limpa de script e links)."""
from app import design_sistema


def test_logo_da_pasta_entra_no_lugar_marcado(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "acme-logo.svg").write_text(
        '<?xml version="1.0"?><svg viewBox="0 0 10 10" onload="alert(1)"><script>x()</script>'
        '<a href="https://mal.example"><path d="M0 0h10v10z"/></a><use href="#p"/></svg>', encoding="utf-8")
    (tmp_path / "assets" / "foto.svg").write_text("<svg></svg>", encoding="utf-8")   # não é marca: fica de fora
    s = {"pasta": str(tmp_path), "nome": "ACME", "tokens": {}, "css": "", "notas": ""}
    marcas = design_sistema.logos(s)
    assert list(marcas) == ["acme-logo"]
    svg = marcas["acme-logo"]
    assert svg.startswith("<svg") and "script" not in svg and "onload" not in svg and "mal.example" not in svg and 'href="#p"' in svg
    html = '<header><span class="logo" data-logo="acme-logo" role="img" aria-label="ACME"></span></header>'
    assert '<span class="logo" data-logo="acme-logo" role="img" aria-label="ACME"><svg' in design_sistema.aplicar_logos(html, s)
    assert 'data-logo="acme-logo"' in design_sistema.para_prompt(s)


def test_nome_da_marca_em_texto_vira_a_logo(tmp_path):
    (tmp_path / "druve-wordmark.svg").write_text('<svg viewBox="0 0 10 2"><path d="M0 0h10v2z"/></svg>', encoding="utf-8")
    s = {"pasta": str(tmp_path), "nome": "DRUVE", "tokens": {}, "css": "", "notas": ""}
    topo = '<a href="#" class="topo-logo" aria-label="DRUVE"><span class="topo-logo-nome">DRUVE</span></a>'
    out = design_sistema.aplicar_logos(topo, s)
    assert out.startswith('<a href="#" class="topo-logo" aria-label="DRUVE"><span class="logo" data-logo="druve-wordmark"')
    assert "<svg" in out and "topo-logo-nome" not in out
    # texto que não é só o nome da marca fica como está
    frase = '<p class="rodape-marca">DRUVE — software sob medida</p>'
    assert design_sistema.aplicar_logos(frase, s) == frase

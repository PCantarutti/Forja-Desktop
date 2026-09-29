import json

from app import design_sistema


def test_le_design_system_em_documento_e_json(tmp_path):
    (tmp_path / "DESIGN.md").write_text("# Acme\nUse **#0F766E** como cor primária; cantos de 8px; títulos em Söhne.\nNunca use gradiente.", "utf-8")
    (tmp_path / "tokens").mkdir()
    (tmp_path / "tokens" / "cores.json").write_text(json.dumps({
        "color": {"primary": {"$value": "#0F766E", "$type": "color"}, "surface": {"value": "#FAFAF9"}},
        "radius": {"md": "8px"}}), "utf-8")
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "Sohne-Bold.woff2").write_bytes(b"x")
    (tmp_path / "package.json").write_text('{"name": "x", "version": "1.0.0"}', "utf-8")   # não é token
    r = design_sistema.resumo(tmp_path)
    assert "--color-primary: #0F766E" in r and "--color-surface: #FAFAF9" in r and "--radius-md: 8px" in r
    assert "Documentação do design system" in r and "Nunca use gradiente" in r and "DESIGN.md" in r
    assert "Arquivos de fonte na pasta: Sohne" in r and "--version" not in r

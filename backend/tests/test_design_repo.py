import base64
import re
import subprocess

import pytest

from app import db, design, design_repo, mirror
from app.tools import ToolError

FOTO = "data:image/png;base64," + base64.b64encode(b"\x89PNG" + bytes(range(256)) * 20).decode()
DOC = f"""<!doctype html><html><head><style>:root {{ --cor: #c75b12; }}</style></head><body>
<section data-section="hero"><h1>Padaria</h1><img data-slot="foto-hero" data-slot-status="pronta" src="{FOTO}" alt="pão"></section>
</body></html>"""


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(design_repo, "RAIZ", tmp_path / "designs")
    monkeypatch.setattr(design_repo, "ANTIGA", tmp_path / "design")
    design._RUNS.clear()


def _conv(titulo="Padaria do Bairro") -> int:
    with db.session() as s:
        c = db.Conversation(kind="design", title=titulo)
        s.add(c)
        s.commit()
        return c.id


def _git(r, *a):
    return subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True, check=True).stdout.strip()


def test_pasta_por_projeto_e_versao_vira_commit_com_ref():
    conv = _conv()
    design._nova_versao(conv, None, DOC, "inicial")
    r = design_repo.raiz(conv)
    assert r.name == f"{conv}-padaria-do-bairro" and (r / ".git").is_dir()
    html_disco = (r / "index.html").read_text("utf-8")
    fotos = list((r / "img").iterdir())
    assert len(fotos) == 1 and f'src="img/{fotos[0].name}"' in html_disco and "base64" not in html_disco   # foto em arquivo
    p = design.projeto(conv)
    assert FOTO in p["html"] and p["pasta"] == str(r)   # o canvas recebe a foto de volta no HTML
    assert _git(r, "log", "-1", "--format=%s", "refs/versoes/v1") == "v1: inicial"
    assert design.projeto(conv)["mensagens"][-1]["versao"] == 1
    with db.session() as s:
        d = design._versoes(s, conv)[0].meta["design"]
    assert "html" not in d and len(d["commit"]) == 40   # o HTML saiu do banco


def test_ramos_sao_pais_de_commit_e_rascunho_e_o_index_modificado():
    conv = _conv()
    design._nova_versao(conv, None, DOC, "inicial")
    h1 = re.search(r'<h1 data-fid="(\w+)"', design.projeto(conv)["html"]).group(1)
    design.editar_estilo(conv, [h1], {"color": "#111111"})
    r = design_repo.raiz(conv)
    assert "index.html" in _git(r, "status", "--porcelain")   # rascunho = não commitado
    design.salvar_versao(conv, "cor")
    design.ir_para(conv, 1)
    assert "#111111" not in (r / "index.html").read_text("utf-8")   # checkout da v1
    design.editar_estilo(conv, [h1], {"color": "#222222"})
    design.salvar_versao(conv, "outra cor")   # v3 nasce da v1: ramo
    v1, v2, v3 = (_git(r, "rev-parse", f"refs/versoes/v{n}") for n in (1, 2, 3))
    assert _git(r, "rev-parse", "refs/versoes/v2^") == v1 and _git(r, "rev-parse", "refs/versoes/v3^") == v1
    assert design.versao_html(conv, 2)["html"].count("#111111") == 1 and "#222222" in design.projeto(conv)["html"]
    # a foto não mudou: um blob só no repositório inteiro
    assert len({_git(r, "rev-parse", f"refs/versoes/v{n}:img") for n in (1, 2, 3)}) == 1
    # descartar rascunho volta o index ao da versão
    design.editar_estilo(conv, [h1], {"color": "#333333"})
    design.descartar_rascunho(conv)
    assert not _git(r, "status", "--porcelain")


def test_apagar_conversa_apaga_a_pasta():
    from fastapi.testclient import TestClient
    from app import main
    conv = _conv()
    design._nova_versao(conv, None, DOC, "inicial")
    r = design_repo.raiz(conv)
    assert r.is_dir()
    assert TestClient(main.app).delete(f"/api/conversations/{conv}").status_code == 200
    assert not r.exists()


def test_projeto_antigo_migra_com_os_mesmos_pais_e_o_rascunho():
    conv = _conv("Antigo")
    v2 = DOC.replace("Padaria", "Padaria Dois")
    v3 = DOC.replace("Padaria", "Padaria Três")
    with db.session() as s:   # do jeito que era antes da pasta: HTML inteiro no meta
        for n, h, base in ((1, DOC, 0), (2, v2, 1), (3, v3, 1)):
            s.add(db.Message(conversation_id=conv, role="assistant", content=f"v{n}", status="ok",
                             meta={"design": {"versao": n, "html": h, "descricao": f"v{n}", "base": base, "atual": n == 3}}))
        s.add(db.Message(conversation_id=conv, role="event", content="rascunho",
                         meta={"design_rascunho": {"base": 3, "rev": 1, "passos": ["x"], "html": v3.replace("Três", "Três!")}}))
        s.commit()
    p = design.projeto(conv)   # primeira abertura: migra
    r = design_repo.raiz(conv)
    assert _git(r, "rev-parse", "refs/versoes/v3^") == _git(r, "rev-parse", "refs/versoes/v1")
    assert "Três!" in p["html"] and p["rascunho"]["mudancas"] == 1 and p["atual"] == 3
    assert "Padaria Dois" in design.versao_html(conv, 2)["html"]
    with db.session() as s:
        assert all("html" not in m.meta["design"] for m in design._versoes(s, conv))
        assert "html" not in design._rascunho(s, conv).meta["design_rascunho"]


def test_sem_git_avisa_como_instalar(monkeypatch):
    monkeypatch.setattr(design_repo, "_GIT", [])
    monkeypatch.setattr(design_repo, "_candidatos", lambda: [])
    st = design_repo.status_git()
    assert not st["ok"] and "winget install --id Git.Git" in st["aviso"] and st["baixar"].startswith("https://git-scm.com")
    conv = _conv()
    with pytest.raises(ToolError, match="Git"):
        design._nova_versao(conv, None, DOC, "inicial")


def test_separar_e_juntar_nao_mudam_o_documento():
    pequeno = '<img src="data:image/png;base64,iVBORw0KGgo=">'
    texto, fotos = design_repo.separar(DOC + pequeno)
    assert len(fotos) == 1 and pequeno in texto   # o que é pequeno fica no HTML
    assert design_repo.juntar(texto, fotos.get) == DOC + pequeno

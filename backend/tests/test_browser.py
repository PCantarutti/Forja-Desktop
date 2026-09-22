"""Navegador integrado: bloqueio por capacidade (visão), imagens de ferramenta no histórico, estimativa."""
import asyncio
import pathlib
import os
import sys

import pytest

from app import browser, config
from app.agent import _estimate, build_history
from app.db import Message
from app.tools import ToolError, active, blocked, get_tool, vision_caps


def msg(id, role, content="", **kw):
    return Message(id=id, role=role, content=content, thinking="", **kw)


# ------------------------------------------------ gating por capacidade

def test_vision_caps_override():
    assert vision_caps(None, "auto") == set()
    assert vision_caps(None, "yes") == {"vision"}
    assert vision_caps({"vision", "tools"}, "no") == {"tools"}
    assert vision_caps({"vision"}, "auto") == {"vision"}


@pytest.fixture
def vision_tool():
    """Ferramenta sintética que exige visão (browser_screenshot não exige mais: o print é para o usuário)."""
    from app.tools import REGISTRY, Tool, register
    t = register(Tool("needs_eyes", "teste", {"type": "object", "properties": {}}, lambda r, a: "ok",
                      requires=frozenset({"vision"})))
    yield t
    REGISTRY.pop("needs_eyes", None)


def test_active_filters_by_caps(vision_tool):
    names = lambda caps: {t.name for t in active(caps)}  # noqa: E731
    assert "needs_eyes" in names({"vision"})
    assert "needs_eyes" not in names(set())
    assert "browser_screenshot" in names(set())      # sempre disponível: o print aparece para o usuário
    assert "browser_read" in names(set())
    assert "needs_eyes" in names(None)               # None = listagem da UI, ignora capacidades
    assert [b["name"] for b in blocked(set())] == ["needs_eyes"]
    assert blocked({"vision"}) == []


def test_get_tool_blocked_without_vision(vision_tool):
    with pytest.raises(ToolError, match="exige vision"):
        get_tool("needs_eyes", set())
    assert get_tool("needs_eyes", {"vision"}).name == "needs_eyes"
    assert get_tool("needs_eyes").name == "needs_eyes"  # sem caps: comportamento antigo


@pytest.mark.parametrize("ref", ["e12", "ref=e12", "f1e49", "ref=f2e3"])
def test_ref_regex_accepts_main_and_frame_refs(ref):
    assert browser.REF_RE.match(ref)


@pytest.mark.parametrize("sel", ["text=Salvar", "#id", "role=button[name='x']", "e12x", "fe1"])
def test_ref_regex_rejects_selectors(sel):
    assert not browser.REF_RE.match(sel)


def test_check_url_accepts_local_http():
    assert browser.check_url(" http://localhost:5173/x ") == "http://localhost:5173/x"
    assert browser.check_url("http://host.docker.internal:7001") == "http://host.docker.internal:7001"


def test_set_viewport_clamps_and_updates_state_without_browser():
    s = browser.Session("t", browser.Manager())
    asyncio.run(s.set_viewport(100, 100))       # abaixo do mínimo → sobe para o mínimo
    assert (s.state()["width"], s.state()["height"]) == browser.MIN_VIEWPORT
    asyncio.run(s.set_viewport(10_000, 10_000))  # acima do máximo → teto
    assert (s.viewport["width"], s.viewport["height"]) == browser.MAX_VIEWPORT
    asyncio.run(s.set_viewport(1045, 1100))
    assert s.viewport == {"width": 1045, "height": 1100} and s.last_event["type"] == "state"


def test_sessions_are_per_conversation_key():
    m = browser.Manager()
    a, b = m.session("1"), m.session("2")
    assert a is not b and m.session("1") is a and m.session("") is m.session(browser.SCRATCH)
    a.viewport = {"width": 500, "height": 400}
    assert b.viewport == browser.VIEWPORT and a.state()["key"] == "1" and a.state()["open"] is False


def test_current_session_follows_contextvar():
    token = browser.CURRENT_KEY.set("abc")
    try:
        assert browser.current().key == "abc"
    finally:
        browser.CURRENT_KEY.reset(token)
    assert browser.current().key == browser.SCRATCH


def test_tabs_tool_errors_on_bad_index():
    m = browser.Manager()
    with pytest.raises(ToolError, match="não existe"):
        m.session("x")._page_at(3)


# ------------------------------------------------ imagens de ferramenta no histórico

def _shot(root, n):
    folder = root / ".forja/uploads"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"s{n}.jpg").write_bytes(b"\xff\xd8jpeg" + bytes([n]))
    return {"path": f".forja/uploads/s{n}.jpg", "name": "browser.jpg", "size": 5, "mime": "image/jpeg", "kind": "image"}


def _with_shots(root, n):
    msgs = [msg(1, "user", "olha a tela")]
    i = 2
    for k in range(n):
        msgs.append(msg(i, "assistant", "", tool_calls=[{"id": f"c{k}", "name": "browser_screenshot", "arguments": {}}]))
        msgs.append(msg(i + 1, "tool", "Screenshot anexado", tool_call_id=f"c{k}", name="browser_screenshot",
                        status="ok", meta={"attachments": [_shot(root, k)]}))
        i += 2
    msgs.append(msg(i, "assistant", "vi a tela"))
    return msgs


def test_tool_image_becomes_user_message_after_tool_block(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path)
    hist = build_history(_with_shots(tmp_path, 1), "native")
    roles = [m["role"] for m in hist]
    i = roles.index("tool")
    assert roles[i + 1] == "user" and roles[i + 2] == "assistant"
    parts = hist[i + 1]["content"]
    assert parts[0]["type"] == "text" and "ferramentas" in parts[0]["text"]
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_image_hidden_from_model_when_model_sees_false(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path)
    msgs = _with_shots(tmp_path, 1)
    tool_msg = next(m for m in msgs if m.role == "tool")
    tool_msg.meta = {**tool_msg.meta, "model_sees": False}
    hist = build_history(msgs, "native")
    assert not any(isinstance(m["content"], list) for m in hist)  # nenhuma image_url
    assert [m["role"] for m in hist].count("user") == 1


def test_only_last_two_images_are_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path)
    hist = build_history(_with_shots(tmp_path, 3), "native")
    imgs = [p for m in hist if isinstance(m["content"], list) for p in m["content"] if p["type"] == "image_url"]
    assert len(imgs) == 2
    texts = [m["content"] for m in hist if m["role"] == "user" and isinstance(m["content"], str)]
    assert any("omitida" in t for t in texts)


def test_text_mode_merges_tool_response_with_image_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path)
    hist = build_history(_with_shots(tmp_path, 1), "prompt")  # sem tool role: tudo vira "user" e é fundido
    merged = [m for m in hist if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(merged) == 1
    parts = merged[0]["content"]
    assert any(p.get("type") == "text" and "<tool_response>" in p["text"] for p in parts)
    assert any(p.get("type") == "image_url" for p in parts)
    roles = [m["role"] for m in hist]
    assert all(a != "user" or b != "user" for a, b in zip(roles, roles[1:]))  # alternância preservada


def test_estimate_counts_image_flat():
    big = "A" * 100_000
    msgs = [{"role": "user", "content": [{"type": "text", "text": "x"},
                                         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + big}}]}]
    assert _estimate(msgs, None) < 1_500
    assert _estimate([{"role": "user", "content": "abcd" * 100}], None) == pytest.approx(100 + 8, abs=10)


# ------------------------------------------------ smoke com Chromium
# Precisa do Chromium baixado: rode `npm run prepare-resources` (ou `playwright install --only-shell
# chromium`) e chame o pytest com FORJA_BROWSER_TESTS=1 e PLAYWRIGHT_BROWSERS_PATH apontado para ele.

@pytest.mark.skipif(not os.getenv("FORJA_BROWSER_TESTS"), reason="smoke do Chromium: defina FORJA_BROWSER_TESTS=1")
def test_snapshot_refs_click_and_tabs_work():
    pytest.importorskip("playwright")

    async def go():
        browser.CURRENT_KEY.set("smoke")
        s = browser.MANAGER.session("smoke")
        page = await s.ensure()
        await page.set_content("<button onclick=\"document.title='ok'\">Salvar</button><input name=q>")
        snap = await browser.read(None, {})
        ref = next(l for l in snap.splitlines() if "button" in l).split("[ref=")[1].split("]")[0]
        out = await browser.click(None, {"selector": ref})
        typed = await browser.type_text(None, {"selector": "input[name=q]", "text": "abc"})
        value = await page.locator("input[name=q]").input_value()
        # abas: nova aba vira ativa; fechar volta para a anterior; outra conversa não enxerga
        listed = await browser.tabs(None, {"action": "new"})
        assert len(s.pages) == 2 and s.active is s.pages[1] and "* 2." in listed
        await browser.tabs(None, {"action": "close", "index": 2})
        assert len(s.pages) == 1 and s.active is page
        other = browser.MANAGER.session("other")
        assert other.pages == [] and not other.open
        await browser.MANAGER.shutdown()
        return snap, out, typed, value

    snap, out, typed, value = asyncio.run(go())
    assert "[ref=" in snap and "button" in snap
    assert "Título: ok" in out and value == "abc" and "URL:" in typed


def test_print_sai_em_desktop_e_nao_no_tamanho_do_painel():
    """Aconteceu em uso: o print que ia ao modelo saía com o site em largura de celular.

    O viewport do navegador segue o painel da UI, que é uma coluna estreita — então validar o
    layout de um site desktop era impossível. O print agora fixa o viewport por CDP só enquanto
    tira a foto, sem mexer no painel.
    """
    assert browser.PRINT_VIEWPORT == {"width": 1920, "height": 1080}
    from app.tools import REGISTRY
    esquema = REGISTRY["browser_screenshot"].parameters["properties"]
    # `full_page` saiu: a página inteira vira uma imagem de milhares de pixels, cara para o modelo
    # com visão e pequena demais para julgar. Quem quer o resto usa browser_scroll.
    assert set(esquema) == {"selector", "largura", "altura"}
    assert "browser_scroll" in REGISTRY
    assert not REGISTRY["browser_screenshot"].parameters["required"]


def test_scroll_nao_pula_mais_do_que_o_print_mostra():
    """O passo da rolagem tem que caber no que a foto seguinte mostra.

    A rolagem acontece na janela real (tamanho do painel) e o print sai em 1080px. Com um painel
    mais alto que isso, rolar "uma tela" passava além do que a foto cobre e abria um buraco: uma
    faixa da página que não aparecia em print nenhum.
    """
    from app.tools import REGISTRY

    assert "browser_scroll" in REGISTRY
    fonte = (pathlib.Path(__file__).resolve().parents[1] / "app" / "browser.py").read_text(encoding="utf-8")
    passo = fonte[fonte.index("tela = min(int(await page.evaluate"):].splitlines()[0]
    assert "ALTURA_MAX_PRINT" in passo and 'PRINT_VIEWPORT["height"]' in passo

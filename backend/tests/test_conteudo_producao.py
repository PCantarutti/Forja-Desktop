import asyncio
import json
import sys
from pathlib import Path

import pytest

from app import config, conteudo, conteudo_producao as P, conteudo_roteiros as R, db, mirror
from app.agent import _save
from app.tools import ToolError

# Claude falso: lê o pedido do stdin, conta como o stream-json do claude -p e escreve o .mp4 pedido.
FALSO = r'''
import json, os, re, sys, time
pedido = sys.stdin.buffer.read().decode("utf-8")
modo = os.environ.get("CLAUDE_FALSO", "ok")
open(".forja/pedido-recebido.md", "w", encoding="utf-8").write(pedido)
open(".forja/argv.json", "w").write(json.dumps(sys.argv[1:]))
def ev(e): print(json.dumps(e), flush=True)
ev({"type": "system", "subtype": "init"})
ev({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "isUsingOverage": False,
    "unifiedWindows": {"five_hour": {"utilization": 0.17, "resetsAt": 1791168000}, "seven_day": {"utilization": 0.02, "resetsAt": 1791619200}}}})
if modo == "sem-sessao" and "--resume" in sys.argv:
    ev({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "No conversation found with session ID: sess-1"})
    sys.exit(1)
ev({"type": "assistant", "message": {"content": [{"type": "text", "text": "Lendo o roteiro."},
    {"type": "tool_use", "name": "Bash", "input": {"command": "npx remotion render X"}}]}})
ev({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
    "content": "This command requires approval"}]}})
if modo == "lento":
    time.sleep(30)
slug = re.search(r"VIDEO: out/(\S+)\.mp4", pedido).group(1)
if modo != "sem-video":
    os.makedirs("out", exist_ok=True)
    open("out/f_hook.png", "wb").write(b"png")
    open(f"out/{slug}.mp4", "wb").write(b"mp4falso")
ev({"type": "result", "subtype": "error_during_execution" if modo == "erro" else "success",
    "is_error": modo == "erro", "result": ("Pronto.\nMÍDIA: yt-dlp não está instalado; mande o arquivo do trailer.\n" if modo == "midia" else "Pronto.\n") + f"VIDEO: out/{slug}.mp4", "total_cost_usd": 1.23, "num_turns": 7, "session_id": "sess-1",
    "permission_denials": [{"tool_name": "PowerShell", "tool_input": {"command": "python -c 1"}}]})
'''


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    estilos, projeto, saida = tmp_path / "estilos", tmp_path / "youtube", tmp_path / "Desktop"
    for d in (estilos, projeto, saida):
        d.mkdir()
    (estilos / "alerta-tech.md").write_text("# Estilo\n\nUrgente.\n", encoding="utf-8")
    (estilos / "README.md").write_text("# Estilos\n", encoding="utf-8")
    falso = tmp_path / "claude_falso.py"
    falso.write_text(FALSO, encoding="utf-8")
    with db.session() as s:
        s.query(db.AppSetting).filter(db.AppSetting.key == conteudo.CHAVE).delete()
        s.query(db.Conversation).filter(db.Conversation.kind == conteudo.KIND).delete()
        s.commit()
    conteudo.salvar_pastas({"pasta_estilos": str(estilos), "pasta_projeto": str(projeto), "pasta_saida": str(saida)})
    monkeypatch.setattr(P, "achar_claude", lambda: "claude-falso")
    real = P.argv
    monkeypatch.setattr(P, "argv", lambda claude, pastas: [sys.executable, str(falso), *real(claude, pastas)[1:]])
    P._RUNS.clear()
    yield {"projeto": projeto, "saida": saida, "estilos": estilos}
    P._RUNS.clear()


def _aprovado(formato="vertical") -> tuple[int, int, dict]:
    cid = conteudo.salvar_especificacao({"nome": "IA", "tema": "riscos", "estilo": "alerta-tech", "formato": formato})["id"]
    r = R.normalizar([{"titulo": "OpenAI", "titulo_youtube": "A OpenAI parou tudo 🚨", "descricao": "Desc.",
                       "noticia": {"resumo": "fato", "fontes": [{"titulo": "Fortune", "url": "https://fortune.com/x"}]},
                       "cenas": [{"id": "hook", "texto": "A OpenAI parou."}]}], [])[0]
    mid = _save(cid, role="assistant", name=R.NOME, status="ok",
                meta={R.CHAVE: {"roteiros": [r], "estilo": "alerta-tech", "formato": formato}}).id
    R.marcar(mid, r["id"], "aprovado")
    return cid, mid, r


async def _ate_o_fim(cid: int, **kw) -> dict:
    est = P.iniciar(cid, **kw)
    for _ in range(600):
        if est["id"] not in P._RUNS:
            break
        await asyncio.sleep(0.05)
    return P.estado(est["id"])


def test_producao_completa(ambiente, monkeypatch):
    monkeypatch.setenv("CLAUDE_FALSO", "ok")
    cid, mid, r = _aprovado()
    est = asyncio.run(_ate_o_fim(cid))
    assert est["status"] == "ok", est["aviso"]
    entregue = Path(est["entregue"])
    assert entregue.parent == ambiente["saida"] and entregue.read_bytes() == b"mp4falso"
    assert entregue.name.endswith("-a-openai-parou-tudo.mp4")
    txt = entregue.with_suffix(".txt").read_text(encoding="utf-8")
    assert "A OpenAI parou tudo" in txt and "https://fortune.com/x" in txt
    assert est["custo_usd"] == 1.23 and est["turnos"] == 7 and est["ferramentas"] == 1
    assert est["negados"][0] == "This command requires approval" and est["negados"][1] == "PowerShell: python -c 1"
    assert any(l.startswith("🔧 Bash") for l in est["log"])
    assert R.estado(mid)["roteiros"][0]["status"] == "produzido"          # saiu da fila de aprovados
    assert R.aprovado(cid) is None

    pedido = (ambiente["projeto"] / ".forja" / "pedido-recebido.md").read_text(encoding="utf-8")
    assert "1080x1920" in pedido and "vertical 9:16" in pedido and "alerta-tech.md" in pedido and "README.md" in pedido
    assert f"producao/{est['id']}/roteiro.json" in pedido
    args = json.loads((ambiente["projeto"] / ".forja" / "argv.json").read_text())
    assert args[:6] == ["-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits"]
    assert args[6:10] == ["--model", "claude-opus-5-5", "--effort", "medium"]   # o padrão: Opus 5.5 no esforço médio
    assert "Bash(npx remotion *)" in args and "PowerShell(python scripts/*)" in args and "--add-dir" in args
    assert "`python scripts/*`" in pedido   # o Claude sabe o que pode rodar e não gasta turno testando
    assert "Bash" not in args   # nunca o Bash inteiro, só os prefixos da lista


def test_horizontal_no_pedido(ambiente, monkeypatch):
    monkeypatch.setenv("CLAUDE_FALSO", "ok")
    cid, _, _ = _aprovado("horizontal")
    asyncio.run(_ate_o_fim(cid))
    pedido = (ambiente["projeto"] / ".forja" / "pedido-recebido.md").read_text(encoding="utf-8")
    assert "1920x1080" in pedido and "horizontal 16:9" in pedido


def test_erro_do_claude(monkeypatch):
    monkeypatch.setenv("CLAUDE_FALSO", "erro")
    cid, mid, r = _aprovado()
    est = asyncio.run(_ate_o_fim(cid))
    assert est["status"] == "erro" and "erro" in est["aviso"]
    assert R.estado(mid)["roteiros"][0]["status"] == "aprovado"           # continua na fila


def test_sem_video(monkeypatch):
    monkeypatch.setenv("CLAUDE_FALSO", "sem-video")
    cid, _, _ = _aprovado()
    est = asyncio.run(_ate_o_fim(cid))
    assert est["status"] == "erro" and ".mp4" in est["aviso"]


def test_cancelar(monkeypatch):
    monkeypatch.setenv("CLAUDE_FALSO", "lento")

    async def rodar():
        est = P.iniciar(_aprovado()[0])
        await asyncio.sleep(1.5)
        P.cancelar(est["id"])
        for _ in range(200):
            if est["id"] not in P._RUNS:
                break
            await asyncio.sleep(0.05)
        return P.estado(est["id"])

    est = asyncio.run(rodar())
    assert est["status"] == "cancelado"


def test_validacoes(ambiente):
    cid = conteudo.salvar_especificacao({"nome": "x", "tema": "y", "estilo": "alerta-tech"})["id"]
    with pytest.raises(ToolError, match="aprovado"):
        P.iniciar(cid)
    conteudo.salvar_pastas({"pasta_projeto": ""})
    with pytest.raises(ToolError, match="projeto"):
        P.iniciar(cid)


def test_uma_producao_por_vez(monkeypatch):
    monkeypatch.setenv("CLAUDE_FALSO", "lento")

    async def rodar():
        cid = _aprovado()[0]
        est = P.iniciar(cid)
        with pytest.raises(ToolError, match="Já tem"):
            P.iniciar(cid)
        P.cancelar(est["id"])
        while est["id"] in P._RUNS:
            await asyncio.sleep(0.05)

    asyncio.run(rodar())


def test_regras_bash_e_achar_claude(tmp_path, monkeypatch):
    assert P.regras_bash(["npm run *", "ffmpeg -version", " "]) == \
        ["Bash(npm run *)", "PowerShell(npm run *)", "Bash(ffmpeg -version)", "PowerShell(ffmpeg -version)"]
    shim = tmp_path / "npm" / "claude.cmd"
    exe = tmp_path / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    shim.write_text("@echo off")
    exe.write_text("")
    monkeypatch.undo()   # o achar_claude de verdade
    monkeypatch.setattr(P.shutil, "which", lambda nome: str(shim))
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": b"2.1.233 (Claude Code)"})())
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    with db.session() as s:
        s.query(db.AppSetting).filter(db.AppSetting.key == conteudo.CHAVE).delete()
        s.commit()
    assert P.achar_claude() == str(exe)                      # só o do npm
    novo = tmp_path / "appdata" / "Claude-Gabi" / "claude-code" / "2.1.286" / "635c1867224a" / "claude.exe"
    velho = tmp_path / "appdata" / "Claude" / "claude-code" / "2.1.84" / "aa" / "claude.exe"
    for f in (novo, velho):
        f.parent.mkdir(parents=True)
        f.write_text("")
    assert P.achar_claude() == str(novo)                     # o mais novo ganha (2.1.286 > 2.1.233 > 2.1.84)
    conteudo.salvar_pastas({"claude_cli": str(exe)})
    assert P.achar_claude() == str(exe)                      # o caminho dos Ajustes manda


def test_reap():
    cid, _, _ = _aprovado()
    mid = _save(cid, role="assistant", name=P.NOME, status="running", meta={P.CHAVE: {"fase": "trabalhando"}}).id
    assert P.reap() >= 1
    assert P.estado(mid)["status"] == "erro"


def test_login_expirado_vira_aviso_claro(monkeypatch):
    monkeypatch.setenv("CLAUDE_FALSO", "erro")
    falso_result = "Failed to authenticate: OAuth session expired and could not be refreshed"
    assert P.LOGIN_RE.search(falso_result)
    assert not P.LOGIN_RE.search("Render falhou: composição não existe")


def test_testar_claude(monkeypatch, tmp_path):
    uso = b'{"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {"five_hour": {"utilization": 0.4, "resetsAt": 1}}}}'
    respostas = {"ok": uso + b'\n{"type": "result", "is_error": false, "result": "OK"}',
                 "login": b'{"type": "result", "is_error": true, "result": "Failed to authenticate: OAuth session expired"}'}
    for chave, saida in respostas.items():
        monkeypatch.setattr(P.subprocess, "run", lambda *a, saida=saida, **k: type("R", (), {"stdout": saida, "stderr": b""})())
        r = P.testar_claude()
        assert r["ok"] is (chave == "ok")
        if chave == "login":
            assert "/login" in r["mensagem"]


def test_conta_do_claude_vai_no_ambiente(ambiente, monkeypatch, tmp_path):
    monkeypatch.setenv("FORJA_TOKEN", "segredo")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "herdado-nao-vale")
    assert "CLAUDE_CONFIG_DIR" not in P.ambiente_claude() and "FORJA_TOKEN" not in P.ambiente_claude()
    conta = tmp_path / ".claude-gabi"
    conteudo.salvar_pastas({"claude_conta": str(conta)})
    assert conta.is_dir() and P.ambiente_claude()["CLAUDE_CONFIG_DIR"] == str(conta)
    with pytest.raises(ToolError, match="caminho completo"):
        conteudo.salvar_pastas({"claude_conta": "relativa"})


def test_frames_soltos_e_rota_do_video(ambiente, monkeypatch):
    monkeypatch.setenv("CLAUDE_FALSO", "ok")
    cid, _, _ = _aprovado()
    est = asyncio.run(_ate_o_fim(cid))
    projeto = ambiente["projeto"]
    assert not list((projeto / "out").glob("*.png"))                         # out/ só com o vídeo
    assert not (projeto / ".forja" / "producao" / str(est["id"]) / "frames").exists()   # saem de out/ e somem no fim
    pedido = (projeto / ".forja" / "pedido-recebido.md").read_text(encoding="utf-8")
    assert f".forja/producao/{est['id']}/frames/" in pedido and "nunca em `out/`" in pedido

    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(config, "API_TOKEN", "token-de-teste")   # nos testes ele vem vazio (sem fronteira)
    c = TestClient(app)
    r = c.get(f"/api/conteudo/video/{est['id']}", cookies={"forja_token": config.API_TOKEN})
    assert r.status_code == 200 and r.content == b"mp4falso" and r.headers["content-type"] == "video/mp4"
    assert TestClient(app).get(f"/api/conteudo/video/{est['id']}").status_code == 403    # cliente sem cookie: nada
    Path(est["entregue"]).unlink()
    assert c.get(f"/api/conteudo/video/{est['id']}", cookies={"forja_token": config.API_TOKEN}).status_code == 404


def test_modelo_e_esforco_validos():
    salvo = conteudo.salvar_pastas({"claude_modelo": "opus", "claude_esforco": "high"})
    assert P.modelo_e_esforco(salvo) == ["--model", "opus", "--effort", "high"]
    assert P.modelo_e_esforco(conteudo.salvar_pastas({"claude_modelo": "", "claude_esforco": ""})) == []
    with pytest.raises(ToolError, match="Esforço"):
        conteudo.salvar_pastas({"claude_esforco": "turbo"})
    with pytest.raises(ToolError, match="Modelo"):
        conteudo.salvar_pastas({"claude_modelo": "opus; rm -rf"})


PNG = "data:image/png;base64," + __import__("base64").b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()


def _v1(monkeypatch) -> dict:
    monkeypatch.setenv("CLAUDE_FALSO", "ok")
    cid, _, _ = _aprovado()
    return asyncio.run(_ate_o_fim(cid))


async def _revisao_ate_o_fim(mid: int, pedidos, geral="") -> dict:
    est = P.revisar(mid, pedidos, geral)
    for _ in range(600):
        if est["id"] not in P._RUNS:
            break
        await asyncio.sleep(0.05)
    return P.estado(est["id"])


def test_uso_do_plano_salvo(monkeypatch):
    v1 = _v1(monkeypatch)
    u = P.uso()
    assert v1["status"] == "ok" and u["janelas"]["five_hour"]["uso"] == 0.17 and u["janelas"]["seven_day"]["renova"] == 1791619200


def test_revisao_faz_v2_retomando_a_sessao(ambiente, monkeypatch):
    v1 = _v1(monkeypatch)
    assert v1["sessao"] == "sess-1"
    v2 = asyncio.run(_revisao_ate_o_fim(v1["id"], [
        {"tipo": "quadro", "tempo": 12.4, "comentario": "Troque a cor do título", "imagem": PNG},
        {"tipo": "trecho", "inicio": 20, "fim": 18.5, "comentario": "Corte mais rápido aqui"},
    ], geral="Música um pouco mais baixa"))
    assert v2["status"] == "ok", v2["aviso"]
    assert v2["versao"] == 2 and v2["revisao_de"] == v1["id"] and v2["slug"].endswith("-v2")
    assert Path(v2["entregue"]).name.endswith("-v2.mp4") and Path(v1["entregue"]).is_file()   # a v1 continua lá
    projeto = ambiente["projeto"]
    pedido = (projeto / ".forja" / "pedido-recebido.md").read_text(encoding="utf-8")
    assert "Troque a cor do título" in pedido and "0:12,40" in pedido and "trecho de 0:18,50 a 0:20,00" in pedido
    assert f".forja/producao/{v2['id']}/anotacoes/q1.png" in pedido and "Música um pouco mais baixa" in pedido
    assert (projeto / ".forja" / "producao" / str(v2["id"]) / "anotacoes" / "q1.png").read_bytes().startswith(b"\x89PNG")
    args = json.loads((projeto / ".forja" / "argv.json").read_text())
    assert args[:2] == ["--resume", "sess-1"]
    assert [x["tipo"] for x in v2["pedidos"]] == ["quadro", "trecho"] and "_png" not in v2["pedidos"][0]
    # e a v3 sai da v2
    v3 = asyncio.run(_revisao_ate_o_fim(v2["id"], [], geral="Título maior"))
    assert v3["versao"] == 3 and v3["slug"].endswith("-v3") and "-v2-v3" not in v3["slug"]


def test_revisao_sem_sessao_refaz_sem_retomar(monkeypatch):
    v1 = _v1(monkeypatch)
    monkeypatch.setenv("CLAUDE_FALSO", "sem-sessao")
    v2 = asyncio.run(_revisao_ate_o_fim(v1["id"], [], geral="Título maior"))
    assert v2["status"] == "ok", v2["aviso"]
    assert any(l.startswith("↻") for l in v2["log"])


def test_revisao_valida(monkeypatch):
    v1 = _v1(monkeypatch)
    with pytest.raises(ToolError, match="Diga o que mudar"):
        P.revisar(v1["id"], [], "")
    with pytest.raises(ToolError, match="comentário"):
        P.revisar(v1["id"], [{"tipo": "trecho", "inicio": 1, "fim": 2, "comentario": " "}])
    with pytest.raises(ToolError, match="PNG"):
        P.revisar(v1["id"], [{"tipo": "quadro", "tempo": 1, "comentario": "x", "imagem": "data:image/png;base64,QUJD"}])
    monkeypatch.setenv("CLAUDE_FALSO", "erro")
    cid, _, _ = _aprovado()
    falhou = asyncio.run(_ate_o_fim(cid))
    with pytest.raises(ToolError, match="ficou pronto"):
        P.revisar(falhou["id"], [], "x")


def test_entrega_na_propria_pasta_do_render(ambiente, monkeypatch):
    """Pasta de entrega = out/ do projeto (onde o Remotion grava): não copia sobre si mesmo (WinError 32)."""
    monkeypatch.setenv("CLAUDE_FALSO", "ok")
    (ambiente["projeto"] / "out").mkdir()
    conteudo.salvar_pastas({**conteudo.pastas(), "pasta_saida": str(ambiente["projeto"] / "out")})
    cid, _, _ = _aprovado()
    est = asyncio.run(_ate_o_fim(cid))
    assert est["status"] == "ok", est["aviso"]
    entregue = Path(est["entregue"])
    assert entregue.parent == ambiente["projeto"] / "out" and entregue.read_bytes() == b"mp4falso"
    assert entregue.with_suffix(".txt").is_file()


def test_pedido_ensina_midia_real_quando_o_projeto_tem_o_script(ambiente, monkeypatch):
    monkeypatch.setenv("CLAUDE_FALSO", "ok")
    cid, _, _ = _aprovado()
    asyncio.run(_ate_o_fim(cid))
    pedido = (ambiente["projeto"] / ".forja" / "pedido-recebido.md").read_text(encoding="utf-8")
    assert "Não baixe nada da internet" in pedido and "midia.py" not in pedido   # projeto sem o script: regra antiga
    (ambiente["projeto"] / "scripts").mkdir()
    (ambiente["projeto"] / "scripts" / "midia.py").write_text("", encoding="utf-8")
    cid2 = conteudo.salvar_especificacao({"nome": "Games", "tema": "jogos", "estilo": "alerta-tech"})["id"]
    r = R.normalizar([{"titulo": "x", "cenas": [{"id": "a", "texto": "b"}]}], [])
    mid = _save(cid2, role="assistant", name=R.NOME, status="ok", meta={R.CHAVE: {"roteiros": r}}).id
    R.marcar(mid, r[0]["id"], "aprovado")
    asyncio.run(_ate_o_fim(cid2))
    pedido = (ambiente["projeto"] / ".forja" / "pedido-recebido.md").read_text(encoding="utf-8")
    assert "python scripts/midia.py steam" in pedido and "nunca o\n  áudio original" in pedido and "Não baixe nada" not in pedido



def test_revisao_com_midia_real_no_pedido_e_aviso_do_que_faltou(ambiente, monkeypatch):
    monkeypatch.setenv("CLAUDE_FALSO", "ok")
    (ambiente["projeto"] / "scripts").mkdir()
    (ambiente["projeto"] / "scripts" / "midia.py").write_text("", encoding="utf-8")
    v1 = _v1(monkeypatch)
    monkeypatch.setenv("CLAUDE_FALSO", "midia")
    v2 = asyncio.run(_revisao_ate_o_fim(v1["id"], [], geral="Coloque um trecho do trailer https://youtu.be/abc de 0:10 a 0:15"))
    pedido = (ambiente["projeto"] / ".forja" / "pedido-recebido.md").read_text(encoding="utf-8")
    assert "python scripts/midia.py" in pedido and "Link no pedido: use ESSE link" in pedido and '"MÍDIA:"' in pedido
    assert v2["status"] == "ok" and v2["aviso"] == "Mídia: yt-dlp não está instalado; mande o arquivo do trailer."



def test_limpar_midia_apaga_o_que_o_video_nao_usa(tmp_path):
    proj = tmp_path / "youtube"
    (proj / "src" / "Gta").mkdir(parents=True)
    m = proj / "public" / "midia"
    for f in ("gta-6/trailer-1.mp4", "gta-6/yt-20-25.mp4", "gta-6/ref-360p-0-90.mp4", "gta-6/creditos.json",
              "outro/captura-1.jpg", "outro/creditos.json"):
        (m / f).parent.mkdir(parents=True, exist_ok=True)
        (m / f).write_bytes(b"x" * 1000)
    (proj / "src" / "Gta" / "scenes.tsx").write_text('<OffthreadVideo src={staticFile("midia/gta-6/yt-20-25.mp4")} muted />', encoding="utf-8")
    n, tam = P.limpar_midia(proj)
    assert n == 3 and tam == 3000
    assert sorted(f.relative_to(m).as_posix() for f in m.rglob("*") if f.is_file()) == ["gta-6/creditos.json", "gta-6/yt-20-25.mp4"]
    assert not (m / "outro").exists()   # pasta só com o creditos.json some junto
    assert P.limpar_midia(tmp_path / "nada") == (0, 0)

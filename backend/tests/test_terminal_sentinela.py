"""E9: o terminal do agente sabe quando o comando terminou (sentinela com exit code), sem inferir por silêncio."""
import pytest

from app import native, shell, terminal
from app.tools import run_tool

WIN = native.WINDOWS


@pytest.fixture
def conv_ctx():
    tok = shell.CONV.set("conv-sentinela")
    yield
    for tid in list(terminal.AGENTE):
        terminal.close(tid)
        terminal.AGENTE.pop(tid, None)
    shell.CONV.reset(tok)


def _abre(tmp_path):
    return run_tool("terminal_open", {}, tmp_path).split("'")[1]


def test_comando_que_passa_termina_com_exit_0_e_sem_a_linha_do_sentinela(tmp_path, conv_ctx):
    tid = _abre(tmp_path)
    saida = run_tool("terminal_send", {"id": tid, "command": "echo ola", "wait": 20}, tmp_path)
    assert "ola" in saida and "[comando terminou: exit 0]" in saida
    assert terminal.MARCA not in saida and "quieto" not in saida


def test_comando_que_falha_traz_o_exit_code(tmp_path, conv_ctx):
    tid = _abre(tmp_path)
    falha = "cmd /c exit 3" if WIN else "bash -c 'exit 3'"
    saida = run_tool("terminal_send", {"id": tid, "command": falha, "wait": 20}, tmp_path)
    assert "[comando terminou: exit 3]" in saida


def test_comando_calado_e_lento_nao_vira_quieto(tmp_path, conv_ctx):
    """Antes: 2 s sem saída = 'terminou'. Um sleep de 3 s calado era dado como pronto no meio."""
    tid = _abre(tmp_path)
    dorme = "Start-Sleep -Seconds 3" if WIN else "sleep 3"
    saida = run_tool("terminal_send", {"id": tid, "command": dorme, "wait": 20}, tmp_path)
    assert "[comando terminou: exit 0]" in saida


def test_comando_que_passa_do_tempo_fica_pendente_e_o_read_avisa_quando_termina(tmp_path, conv_ctx):
    tid = _abre(tmp_path)
    dorme = "Start-Sleep -Seconds 4; echo fim" if WIN else "sleep 4; echo fim"
    saida = run_tool("terminal_send", {"id": tid, "command": dorme, "wait": 1}, tmp_path)
    assert "comando terminou" not in saida
    lido = run_tool("terminal_read", {"id": tid, "wait": 20}, tmp_path)
    assert "fim" in lido and "[o comando anterior terminou: exit 0]" in lido
    assert terminal.MARCA not in lido
    depois = run_tool("terminal_send", {"id": tid, "command": "echo de novo", "wait": 20}, tmp_path)
    assert "[comando terminou: exit 0]" in depois  # voltou a usar o sentinela


def test_limpa_logs_antigos_e_mantem_os_recentes(tmp_path, monkeypatch):
    import os
    import time
    monkeypatch.setattr(shell, "LOG_DIR", tmp_path)
    velho, novo = tmp_path / "velho.log", tmp_path / "novo.log"
    velho.write_text("x", encoding="utf-8")
    novo.write_text("y", encoding="utf-8")
    dez_dias = time.time() - 10 * 86_400
    os.utime(velho, (dez_dias, dez_dias))
    assert shell.limpa_logs() >= 1
    assert not velho.exists() and novo.exists()


def test_saida_grande_do_run_command_guarda_o_log_completo(tmp_path):
    """Antes a saída inteira ficava na memória e o log era apagado: o meio de um log de build sumia."""
    from pathlib import Path
    gera = ("1..6000 | ForEach-Object { \"linha $_ do build\" }" if WIN
            else "for i in $(seq 1 6000); do echo \"linha $i do build\"; done")
    saida = run_tool("run_command", {"command": gera, "timeout": 60}, tmp_path)
    assert "linha 1 do build" in saida and "linha 6000 do build" in saida
    assert "caracteres omitidos; a saída completa está em" in saida
    caminho = saida.split("a saída completa está em ")[1].split(" — ")[0]
    completo = Path(caminho).read_text(encoding="utf-8", errors="replace")
    assert "linha 3000 do build" in completo                    # o meio que foi cortado está lá
    assert len(saida) < shell.MAX_OUTPUT + 1000

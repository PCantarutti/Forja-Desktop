

def test_url_do_log_sem_parentese_do_http_server():
    """O http.server anuncia "(http://127.0.0.1:63309/) ..." e o ')' ia junto para o botão Abrir."""
    from app import shell
    assert shell.URL_NO_LOG.findall("Serving HTTP on 127.0.0.1 port 63309 (http://127.0.0.1:63309/) ...") \
        == ["http://127.0.0.1:63309/"]
    assert shell.URL_NO_LOG.findall("  Local:   http://localhost:5173/") == ["http://localhost:5173/"]


def test_escutando_pega_localhost_ipv4_e_ipv6():
    """O Vite escuta só em [::1]; porta de rede (192.168...) e conexão estabelecida ficam de fora."""
    from app import shell
    saida = """
  Proto  Endereço local          Endereço externo       Estado          PID
  TCP    0.0.0.0:3000           0.0.0.0:0              LISTENING       100
  TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       200
  TCP    192.168.0.5:9000       0.0.0.0:0              LISTENING       300
  TCP    127.0.0.1:8000         127.0.0.1:51000        ESTABLISHED     200
  TCP    [::1]:5173             [::]:0                 LISTENING       400
  TCP    [::]:3000              [::]:0                 LISTENING       100
"""
    assert shell._escutando(saida) == {3000: 100, 8000: 200, 5173: 400}


def test_url_do_log_com_ipv6_do_http_server_no_windows(monkeypatch):
    """`python -m http.server` no Windows anuncia http://[::]:8080: a porta sai e o endereço vira localhost."""
    from app import shell
    monkeypatch.setattr(shell, "_log", lambda name, n: "Serving HTTP on :: port 8080 (http://[::]:8080/) ...")
    assert shell.url_do_log("druve") == "http://localhost:8080"
    monkeypatch.setattr(shell, "_log", lambda name, n: "Serving HTTP on 0.0.0.0 port 8000 (http://0.0.0.0:8000/) ...")
    assert shell.url_do_log("x") == "http://localhost:8000"

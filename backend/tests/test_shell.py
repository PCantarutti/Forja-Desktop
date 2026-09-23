

def test_url_do_log_sem_parentese_do_http_server():
    """O http.server anuncia "(http://127.0.0.1:63309/) ..." e o ')' ia junto para o botão Abrir."""
    from app import shell
    assert shell.URL_NO_LOG.findall("Serving HTTP on 127.0.0.1 port 63309 (http://127.0.0.1:63309/) ...") \
        == ["http://127.0.0.1:63309/"]
    assert shell.URL_NO_LOG.findall("  Local:   http://localhost:5173/") == ["http://localhost:5173/"]

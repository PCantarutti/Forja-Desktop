"""Publicação no YouTube sem rede: o Google e o YouTube são falsos (httpx trocado por respostas prontas)."""
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import db, segredo, youtube
from app.tools import ToolError

CID = "123-abc.apps.googleusercontent.com"


class Resp:
    def __init__(self, status=200, json=None, headers=None, text=""):
        self.status_code, self._json, self.headers, self.text = status, json or {}, headers or {}, text

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def limpo():
    with db.session() as s:
        if (linha := s.get(db.AppSetting, youtube.CHAVE)):
            s.delete(linha)
            s.commit()
    youtube._pendente.clear()
    youtube._acesso.clear()


def _google(monkeypatch, chamadas):
    def post(url, data=None, **kw):
        chamadas.append(("POST", url, data))
        if url == youtube.TOKEN and data["grant_type"] == "authorization_code":
            return Resp(json={"access_token": "acc-1", "refresh_token": "ref-1", "expires_in": 3600})
        if url == youtube.TOKEN:
            return Resp(json={"access_token": "acc-2", "expires_in": 3600})
        return Resp()

    def get(url, **kw):
        chamadas.append(("GET", url, kw.get("params")))
        return Resp(json={"items": [{"id": "UC1", "snippet": {"title": "Meu Canal"}}]})
    monkeypatch.setattr(youtube.httpx, "post", post)
    monkeypatch.setattr(youtube.httpx, "get", get)


def test_login_com_pkce_guarda_o_refresh_cifrado(monkeypatch):
    chamadas = []
    _google(monkeypatch, chamadas)
    with pytest.raises(ToolError, match="Cole o ID"):
        youtube.url_login("http://127.0.0.1:8799")
    with pytest.raises(ToolError, match="ID do cliente inválido"):
        youtube.salvar_cliente("qualquer", "x")
    assert youtube.salvar_cliente(CID, "segredo-1")["tem_chave"]
    q = parse_qs(urlparse(youtube.url_login("http://127.0.0.1:8799")).query)
    assert q["redirect_uri"] == ["http://127.0.0.1:8799/api/youtube/retorno"] and q["access_type"] == ["offline"]
    assert q["code_challenge_method"] == ["S256"] and "youtube.upload" in q["scope"][0]
    with pytest.raises(ToolError, match="expirado"):
        youtube.retorno("outro", "c")
    msg = youtube.retorno(q["state"][0], "codigo")
    assert "Meu Canal" in msg
    troca = next(d for m, u, d in chamadas if m == "POST" and d and d.get("grant_type") == "authorization_code")
    desafio = base64.urlsafe_b64encode(hashlib.sha256(troca["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert desafio == q["code_challenge"][0] and troca["client_secret"] == "segredo-1"
    e = youtube.estado()
    assert e["conectado"] and e["canal"] == {"id": "UC1", "titulo": "Meu Canal"} and "client_secret" not in e
    guardado = youtube._ler()["refresh_token"]
    assert segredo.decifrar(guardado) == "ref-1"
    with pytest.raises(ToolError):
        youtube.retorno(q["state"][0], "codigo")   # o mesmo state não serve duas vezes
    # outro projeto do Google: o login antigo cai
    assert not youtube.salvar_cliente("999-x.apps.googleusercontent.com")["conectado"]


def _conectado(monkeypatch):
    _google(monkeypatch, [])
    youtube.salvar_cliente(CID, "s")
    youtube._gravar(refresh_token=segredo.cifrar("ref-1"))


class Upload:
    """YouTube falso: sessão resumível que guarda os bytes; a 2ª parte cai uma vez na rede."""
    def __init__(self, cair=True):
        self.recebido, self.cair, self.thumb, self.corpo = b"", cair, None, None

    def post(self, url, params=None, json=None, content=None, headers=None, **kw):
        if url.endswith("/videos"):
            self.corpo = json
            assert headers["X-Upload-Content-Type"] == "video/mp4"
            return Resp(headers={"Location": "https://upload/sessao"})
        if url.endswith("/thumbnails/set"):
            self.thumb = (params["videoId"], content)
            return Resp()
        return Resp(json={"access_token": "acc", "expires_in": 3600})

    def put(self, url, content=None, headers=None):
        faixa = headers["Content-Range"]
        if faixa.startswith("bytes */"):
            return Resp(308, headers={"Range": f"bytes=0-{len(self.recebido) - 1}"} if self.recebido else {})
        if self.cair and self.recebido:
            self.cair = False
            raise httpx.ConnectError("caiu")
        ini = int(faixa.split()[1].split("-")[0])
        assert ini == len(self.recebido)
        self.recebido += content
        total = int(faixa.split("/")[1])
        if len(self.recebido) < total:
            return Resp(308, headers={"Range": f"bytes=0-{len(self.recebido) - 1}"})
        return Resp(200, json={"id": "VID1", "status": {"privacyStatus": self.corpo["status"]["privacyStatus"]}})


def _ligar(monkeypatch, up):
    monkeypatch.setattr(youtube.httpx, "post", up.post)

    class Cli:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        put = staticmethod(up.put)
    monkeypatch.setattr(youtube.httpx, "Client", Cli)
    monkeypatch.setattr(youtube, "PEDACO", 10)
    monkeypatch.setattr(youtube.time, "sleep", lambda s: None)


def test_envio_resumivel_retoma_depois_de_cair_e_manda_a_thumb(monkeypatch, tmp_path):
    _conectado(monkeypatch)
    up = Upload()
    _ligar(monkeypatch, up)
    video = tmp_path / "v.mp4"
    video.write_bytes(bytes(range(25)))
    capa = tmp_path / "v-capa.jpg"
    capa.write_bytes(b"jpg")
    passos = []
    r = youtube.enviar(video, "Título <com> sinais", "Desc", "unlisted", capa, progresso=passos.append)
    assert up.recebido == video.read_bytes() and r["video_id"] == "VID1" and r["url"] == "https://youtu.be/VID1"
    assert up.thumb == ("VID1", b"jpg") and r["aviso"] == ""
    assert up.corpo["snippet"]["title"] == "Título ‹com› sinais" and up.corpo["status"]["privacyStatus"] == "unlisted"
    assert passos and passos[-1] < 1
    # agendado: sobe privado com publishAt
    up2 = Upload(cair=False)
    _ligar(monkeypatch, up2)
    youtube.enviar(video, "T", "D", "public", None, publicar_em="2026-10-10T12:00:00Z")
    assert up2.corpo["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "publishAt": "2026-10-10T12:00:00Z"}


def test_publico_que_volta_privado_avisa_e_cota_esgotada(monkeypatch, tmp_path):
    _conectado(monkeypatch)
    up = Upload(cair=False)
    _ligar(monkeypatch, up)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 5)
    up.put = lambda url, content=None, headers=None: Resp(200, json={"id": "V", "status": {"privacyStatus": "private"}})
    _ligar(monkeypatch, up)
    assert "sem auditoria" in youtube.enviar(video, "T", "D", "public")["aviso"]
    up.post = lambda url, **kw: Resp(403, json={"error": {"message": "x", "errors": [{"reason": "quotaExceeded"}]}})
    _ligar(monkeypatch, up)
    with pytest.raises(ToolError, match="Cota diária"):
        youtube.enviar(video, "T", "D", "public")


def test_acesso_revogado_pede_para_conectar_de_novo(monkeypatch):
    _conectado(monkeypatch)
    monkeypatch.setattr(youtube.httpx, "post", lambda url, **kw: Resp(400, text='{"error": "invalid_grant"}'))
    with pytest.raises(ToolError, match="conecte de novo"):
        youtube._token()
    assert not youtube.estado()["conectado"]


def test_rota_de_retorno_sem_token_e_o_resto_com(monkeypatch):
    from fastapi.testclient import TestClient
    from app import config
    from app.main import app
    monkeypatch.setattr(config, "API_TOKEN", "token-de-teste")
    c = TestClient(app)
    assert c.get("/api/youtube").status_code == 403
    r = c.get("/api/youtube/retorno", params={"state": "inventado", "code": "x"})
    assert r.status_code == 400 and "expirado" in r.text
    assert c.get("/api/youtube", headers={"x-forja-token": "token-de-teste"}).json()["conectado"] is False

"""Publicação no YouTube (tela Conteúdo): login Google uma vez e upload do vídeo pronto, com a thumb.

- OAuth de app instalado com PKCE: a pessoa cria o projeto no Google Cloud (cliente "App para computador") e cola o
  ID e a chave em Ajustes. O Google devolve para o próprio backend (/api/youtube/retorno, no navegador do sistema);
  o `state` aleatório é a autorização dessa rota, que não leva o token da API.
- O refresh token fica cifrado (segredo.py, DPAPI). Sem dependência nova: a API REST pelo httpx.
- Upload resumível em pedaços de 8 MB; o andamento fica no meta da produção ("youtube"), que o PC e o celular já
  consultam. Cota padrão da API: 10.000 unidades/dia, um upload custa 1.600 (~6 vídeos por dia).
- Projeto do Google sem auditoria da API: o YouTube deixa o vídeo privado, mesmo pedindo público.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from . import db, segredo
from .tools import ToolError

CHAVE = "youtube"   # AppSetting: {client_id, client_secret, refresh_token, canal}
ESCOPOS = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
PRIVACIDADES = ("private", "unlisted", "public")
PEDACO = 8 * 1024 * 1024

_pendente: dict = {}   # state -> {verificador, redirect, criado}: o login em andamento
_acesso: dict = {}     # {token, expira}


def _ler() -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        return dict(linha.value) if linha and isinstance(linha.value, dict) else {}


def _gravar(**campos) -> None:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        valor = {**(dict(linha.value) if linha and isinstance(linha.value, dict) else {}), **campos}
        if linha:
            linha.value = valor
        else:
            s.add(db.AppSetting(key=CHAVE, value=valor))
        s.commit()


def estado() -> dict:
    c = _ler()
    return {"client_id": c.get("client_id", ""), "tem_chave": bool(c.get("client_secret")),
            "conectado": bool(c.get("refresh_token")), "canal": c.get("canal") or None}


def salvar_cliente(client_id: str, client_secret: str = "") -> dict:
    client_id = str(client_id or "").strip()
    if client_id and not client_id.endswith(".apps.googleusercontent.com"):
        raise ToolError("ID do cliente inválido: termina em .apps.googleusercontent.com.")
    campos = {"client_id": client_id}
    if client_secret.strip():   # vazio = mantém a chave guardada
        campos["client_secret"] = segredo.cifrar(client_secret.strip())
    if client_id != _ler().get("client_id"):   # outro projeto do Google: o login antigo não vale
        campos.update(refresh_token="", canal=None)
        _acesso.clear()
    _gravar(**campos)
    return estado()


def desconectar() -> dict:
    c = _ler()
    if c.get("refresh_token"):
        try:   # revoga no Google também; sem rede, ao menos esquece aqui
            httpx.post("https://oauth2.googleapis.com/revoke", data={"token": segredo.decifrar(c["refresh_token"])}, timeout=10)
        except httpx.HTTPError:
            pass
    _gravar(refresh_token="", canal=None)
    _acesso.clear()
    return estado()


def url_login(base: str) -> str:
    """URL de consentimento do Google; `base` = http://127.0.0.1:<porta> do backend (redirect de loopback)."""
    c = _ler()
    if not c.get("client_id") or not c.get("client_secret"):
        raise ToolError("Cole o ID do cliente e a chave secreta do Google em Ajustes primeiro.")
    agora = time.time()
    for k in [k for k, v in _pendente.items() if agora - v["criado"] > 900]:
        _pendente.pop(k)
    state, verificador = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    redirect = f"{base.rstrip('/')}/api/youtube/retorno"
    _pendente[state] = {"verificador": verificador, "redirect": redirect, "criado": agora}
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    return AUTH + "?" + urlencode({"client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code",
                                   "scope": ESCOPOS, "access_type": "offline", "prompt": "consent", "state": state,
                                   "code_challenge": desafio, "code_challenge_method": "S256"})


def retorno(state: str, code: str = "", erro: str = "") -> str:
    """O Google voltou com o código: troca pelo refresh token e lê o nome do canal. Devolve a mensagem da página."""
    p = _pendente.pop(state or "", None)
    if not p:
        raise ToolError("Login expirado ou de outra janela. Clique em Conectar de novo no Forja.")
    if erro or not code:
        raise ToolError(f"O Google não autorizou: {erro or 'sem código'}.")
    c = _ler()
    r = httpx.post(TOKEN, timeout=30, data={"client_id": c["client_id"], "client_secret": segredo.decifrar(c["client_secret"]),
                                            "code": code, "code_verifier": p["verificador"], "grant_type": "authorization_code",
                                            "redirect_uri": p["redirect"]})
    if r.status_code != 200:
        raise ToolError(f"O Google recusou o código ({r.status_code}): {r.text[:200]}")
    d = r.json()
    if not d.get("refresh_token"):
        raise ToolError("O Google não mandou o acesso offline. Remova o Forja em myaccount.google.com/permissions e conecte de novo.")
    _acesso.update(token=d["access_token"], expira=time.time() + int(d.get("expires_in", 3600)) - 60)
    _gravar(refresh_token=segredo.cifrar(d["refresh_token"]))
    canal = _canal()
    _gravar(canal=canal)
    return f"Conta do YouTube conectada: {canal['titulo'] if canal else 'canal sem nome'}. Pode fechar esta aba."


def _token() -> str:
    if _acesso.get("token") and _acesso["expira"] > time.time():
        return _acesso["token"]
    c = _ler()
    if not c.get("refresh_token"):
        raise ToolError("Conecte a conta do YouTube em Conteúdo › Ajustes.")
    r = httpx.post(TOKEN, timeout=30, data={"client_id": c["client_id"], "client_secret": segredo.decifrar(c["client_secret"]),
                                            "refresh_token": segredo.decifrar(c["refresh_token"]), "grant_type": "refresh_token"})
    if r.status_code != 200:
        if "invalid_grant" in r.text:   # revogado, ou app do Google em "Teste" (o acesso vence em 7 dias)
            _gravar(refresh_token="", canal=None)
            raise ToolError("O acesso ao YouTube venceu ou foi revogado: conecte de novo em Conteúdo › Ajustes.")
        raise ToolError(f"Não deu para renovar o acesso ao YouTube ({r.status_code}).")
    d = r.json()
    _acesso.update(token=d["access_token"], expira=time.time() + int(d.get("expires_in", 3600)) - 60)
    return _acesso["token"]


def _h() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


def _canal() -> dict | None:
    r = httpx.get(f"{API}/channels", params={"part": "snippet", "mine": "true"}, headers=_h(), timeout=30)
    itens = r.json().get("items") or [] if r.status_code == 200 else []
    return {"id": itens[0]["id"], "titulo": itens[0]["snippet"]["title"]} if itens else None


def _erro(r: httpx.Response) -> str:
    try:
        e = r.json()["error"]
        motivo = (e.get("errors") or [{}])[0].get("reason", "")
        if motivo in ("quotaExceeded", "uploadLimitExceeded"):
            return "Cota diária do YouTube esgotada (≈6 envios por dia na cota padrão). Tente amanhã."
        return f"YouTube {r.status_code}: {e.get('message', '')[:200]}"
    except (ValueError, KeyError, TypeError):
        return f"YouTube {r.status_code}: {r.text[:200]}"


def _limpo(texto: str, limite: int) -> str:
    return texto.replace("<", "‹").replace(">", "›").strip()[:limite]   # a API recusa < e >


def enviar(video: Path, titulo: str, descricao: str, privacidade: str = "private", capa: Path | None = None,
           publicar_em: str = "", categoria: str = "28", progresso=lambda f: None) -> dict:
    """Sobe o vídeo (resumível) e a thumb. publicar_em (ISO 8601) agenda: fica privado até a hora."""
    if privacidade not in PRIVACIDADES:
        raise ToolError("Privacidade inválida: private, unlisted ou public.")
    if not titulo.strip():
        raise ToolError("Vídeo sem título.")
    status = {"privacyStatus": "private" if publicar_em else privacidade, "selfDeclaredMadeForKids": False}
    if publicar_em:
        status["publishAt"] = publicar_em
    corpo = {"snippet": {"title": _limpo(titulo, 100), "description": _limpo(descricao, 5000), "categoryId": categoria,
                         "defaultLanguage": "pt-BR", "defaultAudioLanguage": "pt-BR"}, "status": status}
    tam = video.stat().st_size
    r = httpx.post(f"{UPLOAD}/videos", params={"uploadType": "resumable", "part": "snippet,status"}, json=corpo, timeout=60,
                   headers={**_h(), "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(tam)})
    if r.status_code != 200:
        raise ToolError(_erro(r))
    sessao, enviado, falhas, d = r.headers["Location"], 0, 0, None
    with open(video, "rb") as f, httpx.Client(timeout=300) as cli:
        while enviado < tam:
            f.seek(enviado)
            pedaco = f.read(PEDACO)
            try:
                r = cli.put(sessao, content=pedaco,
                            headers={**_h(), "Content-Range": f"bytes {enviado}-{enviado + len(pedaco) - 1}/{tam}"})
                if r.status_code >= 500:
                    raise httpx.HTTPError(f"YouTube {r.status_code}")
            except httpx.HTTPError:
                # rede caiu ou o Google engasgou: espera, pergunta onde parou (308 + Range) e segue de lá
                falhas += 1
                if falhas > 5:
                    raise ToolError("A conexão com o YouTube caiu e não voltou.")
                time.sleep(2 ** falhas)
                try:
                    r = cli.put(sessao, headers={**_h(), "Content-Range": f"bytes */{tam}"})
                except httpx.HTTPError:
                    continue
            if r.status_code in (200, 201):
                d = r.json()
                break
            if r.status_code != 308:
                raise ToolError(_erro(r))
            faixa = r.headers.get("Range", "")
            enviado = int(faixa.split("-")[1]) + 1 if faixa else 0
            progresso(enviado / tam)
    if not d:
        raise ToolError("O YouTube não confirmou o envio.")
    vid = d["id"]
    aviso = ""
    if capa and capa.is_file():
        r = httpx.post(f"{UPLOAD}/thumbnails/set", params={"videoId": vid}, content=capa.read_bytes(), timeout=120,
                       headers={**_h(), "Content-Type": "image/jpeg" if capa.suffix.lower() in (".jpg", ".jpeg") else "image/png"})
        if r.status_code != 200:   # canal sem verificação por telefone não aceita thumb própria
            aviso = "Thumb não enviada: " + _erro(r)
    final = d.get("status", {}).get("privacyStatus", privacidade)
    if final == "private" and privacidade != "private" and not publicar_em:
        aviso = (aviso + " · " if aviso else "") + "O YouTube deixou privado (projeto do Google sem auditoria da API)."
    return {"video_id": vid, "url": f"https://youtu.be/{vid}", "privacidade": final, "aviso": aviso}

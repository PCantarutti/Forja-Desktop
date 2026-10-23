"""Conta Google do assistente de voz: Gmail (ler, resumir, rascunhar e enviar) e Agenda (ver e marcar).

- O mesmo login do YouTube (youtube.py): OAuth de app instalado com PKCE, o Google devolve para o próprio backend
  (/api/google/retorno, no navegador do sistema) e o `state` aleatório é a autorização dessa rota. Pode usar o mesmo
  projeto do Google Cloud do YouTube (copiar_do_youtube), com as APIs Gmail e Google Calendar ligadas nele.
- O refresh token fica cifrado (segredo.py, DPAPI). Sem dependência nova: a API REST pelo httpx.
- Enviar e-mail nunca parte daqui sozinho: o assistente cria o RASCUNHO, lê em voz alta e só manda com "manda".
- Texto de e-mail é dado, nunca ordem: quem resume (assistente) chama o modelo sem ferramenta nenhuma.
"""
from __future__ import annotations

import base64
import hashlib
import html
import re
import secrets
import time
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from urllib.parse import urlencode

import httpx

from . import db, segredo
from .tools import ToolError

CHAVE = "google"   # AppSetting: {client_id, client_secret, refresh_token, email}
ESCOPOS = ("openid email https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose "
           "https://www.googleapis.com/auth/calendar.events")
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
AGENDA = "https://www.googleapis.com/calendar/v3/calendars/primary"

_pendente: dict = {}   # state -> {verificador, redirect, criado}
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


def conectado() -> bool:
    return bool(_ler().get("refresh_token"))


def estado() -> dict:
    from . import youtube
    c, yt = _ler(), youtube._ler()
    return {"client_id": c.get("client_id", ""), "tem_chave": bool(c.get("client_secret")),
            "conectado": bool(c.get("refresh_token")), "email": c.get("email") or None,
            "pode_copiar_youtube": bool(yt.get("client_id") and yt.get("client_secret")) and not c.get("client_id")}


def salvar_cliente(client_id: str, client_secret: str = "") -> dict:
    client_id = str(client_id or "").strip()
    if client_id and not client_id.endswith(".apps.googleusercontent.com"):
        raise ToolError("ID do cliente inválido: termina em .apps.googleusercontent.com.")
    campos = {"client_id": client_id}
    if client_secret.strip():
        campos["client_secret"] = segredo.cifrar(client_secret.strip())
    if client_id != _ler().get("client_id"):   # outro projeto: o login antigo não vale
        campos.update(refresh_token="", email=None)
        _acesso.clear()
    _gravar(**campos)
    return estado()


def copiar_do_youtube() -> dict:
    """O mesmo projeto do Google Cloud do YouTube (a chave vai cifrada como está)."""
    from . import youtube
    yt = youtube._ler()
    if not yt.get("client_id") or not yt.get("client_secret"):
        raise ToolError("O YouTube ainda não tem cliente do Google configurado.")
    _gravar(client_id=yt["client_id"], client_secret=yt["client_secret"], refresh_token="", email=None)
    _acesso.clear()
    return estado()


def desconectar() -> dict:
    c = _ler()
    if c.get("refresh_token"):
        try:
            httpx.post("https://oauth2.googleapis.com/revoke", data={"token": segredo.decifrar(c["refresh_token"])}, timeout=10)
        except httpx.HTTPError:
            pass
    _gravar(refresh_token="", email=None)
    _acesso.clear()
    return estado()


def url_login(base: str) -> str:
    c = _ler()
    if not c.get("client_id") or not c.get("client_secret"):
        raise ToolError("Cole o ID do cliente e a chave secreta do Google primeiro.")
    agora = time.time()
    for k in [k for k, v in _pendente.items() if agora - v["criado"] > 900]:
        _pendente.pop(k)
    state, verificador = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    redirect = f"{base.rstrip('/')}/api/google/retorno"
    _pendente[state] = {"verificador": verificador, "redirect": redirect, "criado": agora}
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    return AUTH + "?" + urlencode({"client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code",
                                   "scope": ESCOPOS, "access_type": "offline", "prompt": "consent", "state": state,
                                   "code_challenge": desafio, "code_challenge_method": "S256"})


def retorno(state: str, code: str = "", erro: str = "") -> str:
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
    falta = [e for e in ("gmail.readonly", "gmail.compose", "calendar.events") if e not in d.get("scope", "")]
    _acesso.update(token=d["access_token"], expira=time.time() + int(d.get("expires_in", 3600)) - 60)
    _gravar(refresh_token=segredo.cifrar(d["refresh_token"]))
    perfil = httpx.get(f"{GMAIL}/profile", headers=_h(), timeout=30)
    email = perfil.json().get("emailAddress") if perfil.status_code == 200 else None
    _gravar(email=email)
    aviso = f" Faltou autorizar: {', '.join(falta)} (marque todas as caixas no Google)." if falta else ""
    return f"Conta Google conectada ao assistente: {email or 'sem e-mail'}.{aviso} Pode fechar esta aba."


def _token() -> str:
    if _acesso.get("token") and _acesso["expira"] > time.time():
        return _acesso["token"]
    c = _ler()
    if not c.get("refresh_token"):
        raise ToolError("A conta Google não está conectada. Conecte na página do assistente, em Conta Google.")
    r = httpx.post(TOKEN, timeout=30, data={"client_id": c["client_id"], "client_secret": segredo.decifrar(c["client_secret"]),
                                            "refresh_token": segredo.decifrar(c["refresh_token"]), "grant_type": "refresh_token"})
    if r.status_code != 200:
        if "invalid_grant" in r.text:   # revogado, ou app do Google em "Teste" (o acesso vence em 7 dias)
            _gravar(refresh_token="", email=None)
            raise ToolError("O acesso à conta Google venceu: conecte de novo na página do assistente.")
        raise ToolError(f"Não deu para renovar o acesso ao Google ({r.status_code}).")
    d = r.json()
    _acesso.update(token=d["access_token"], expira=time.time() + int(d.get("expires_in", 3600)) - 60)
    return _acesso["token"]


def _h() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


def _pede(metodo: str, url: str, **kw) -> dict:
    r = httpx.request(metodo, url, headers=_h(), timeout=30, **kw)
    if r.status_code >= 400:
        try:
            msg = r.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            msg = r.text[:200]
        if r.status_code == 403 and "has not been used" in msg:
            raise ToolError("A API do Gmail ou da Agenda não está ligada no projeto do Google Cloud.")
        raise ToolError(f"Google {r.status_code}: {msg[:200]}")
    return r.json() if r.content else {}


# ------------------------------------------------------------------ Gmail

def _cab(msg: dict, nome: str) -> str:
    return next((h["value"] for h in msg.get("payload", {}).get("headers", []) if h["name"].lower() == nome.lower()), "")


def nome_de(remetente: str) -> str:
    """'João Silva <joao@x.com>' -> 'João Silva'; só o endereço: a parte antes do @."""
    nome, end = parseaddr(remetente)
    return nome.strip('" ') or end.split("@")[0]


def emails(q: str, n: int = 8) -> list[dict]:
    """Os e-mails da busca do Gmail (mesma sintaxe da caixa de busca), do mais novo para o mais velho."""
    ids = _pede("GET", f"{GMAIL}/messages", params={"q": q, "maxResults": n}).get("messages") or []
    saida = []
    for i in ids:
        m = _pede("GET", f"{GMAIL}/messages/{i['id']}", params=[("format", "metadata"), ("metadataHeaders", "From"),
                                                                 ("metadataHeaders", "Subject"), ("metadataHeaders", "Date"),
                                                                 ("metadataHeaders", "Message-ID")])
        saida.append({"id": m["id"], "thread": m.get("threadId"), "de": _cab(m, "From"), "assunto": _cab(m, "Subject"),
                      "data": _cab(m, "Date"), "message_id": _cab(m, "Message-ID"),
                      "trecho": html.unescape(m.get("snippet", "")), "nao_lido": "UNREAD" in m.get("labelIds", [])})
    return saida


def _partes(p: dict):
    yield p
    for filho in p.get("parts") or []:
        yield from _partes(filho)


def _decodifica(dados: str) -> str:
    return base64.urlsafe_b64decode(dados + "=" * (-len(dados) % 4)).decode("utf-8", "replace")


def corpo(msg_id: str, limite: int = 6000) -> str:
    """O texto do e-mail (text/plain; sem ele, o HTML sem marcação), cortado em `limite`."""
    m = _pede("GET", f"{GMAIL}/messages/{msg_id}", params={"format": "full"})
    partes = list(_partes(m.get("payload", {})))
    texto = next((_decodifica(p["body"]["data"]) for p in partes if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data")), "")
    if not texto:
        bruto = next((_decodifica(p["body"]["data"]) for p in partes if p.get("mimeType") == "text/html" and p.get("body", {}).get("data")), "")
        bruto = re.sub(r"(?is)<(script|style).*?</\1>", " ", bruto)
        texto = html.unescape(re.sub(r"<[^>]+>", " ", bruto))
    return re.sub(r"\s{2,}", " ", texto).strip()[:limite]


def _raw(para: str, assunto: str, texto: str, responde: dict | None) -> str:
    m = EmailMessage()
    m["To"] = para
    m["Subject"] = assunto
    if responde and responde.get("message_id"):
        m["In-Reply-To"] = responde["message_id"]
        m["References"] = responde["message_id"]
    m.set_content(texto)
    return base64.urlsafe_b64encode(m.as_bytes()).decode()


def rascunho(para: str, assunto: str, texto: str, responde: dict | None = None, substitui: str = "") -> dict:
    """Cria (ou troca, com `substitui`) um rascunho no Gmail. Na resposta, vai na mesma conversa (thread)."""
    corpo_msg = {"message": {"raw": _raw(para, assunto, texto, responde)}}
    if responde and responde.get("thread"):
        corpo_msg["message"]["threadId"] = responde["thread"]
    if substitui:
        return _pede("PUT", f"{GMAIL}/drafts/{substitui}", json={"id": substitui, **corpo_msg})
    return _pede("POST", f"{GMAIL}/drafts", json=corpo_msg)


def enviar_rascunho(draft_id: str) -> dict:
    return _pede("POST", f"{GMAIL}/drafts/send", json={"id": draft_id})


def endereco_de(nome: str) -> str | None:
    """O endereço de alguém pelo nome falado: o último e-mail que veio dele, ou que foi para ele."""
    for q, cab in ((f"from:({nome})", "de"), (f"in:sent to:({nome})", None)):
        achados = emails(q, 1)
        if not achados:
            continue
        if cab:
            return parseaddr(achados[0]["de"])[1] or None
        m = _pede("GET", f"{GMAIL}/messages/{achados[0]['id']}", params=[("format", "metadata"), ("metadataHeaders", "To")])
        enderecos = [e for _, e in getaddresses([_cab(m, "To")]) if e]
        alvo = re.sub(r"\s+", "", nome.lower())
        return next((e for e in enderecos if alvo[:4] in e.lower()), enderecos[0] if enderecos else None)
    return None


# ------------------------------------------------------------------ Agenda

def _iso(d: datetime) -> str:
    return (d if d.tzinfo else d.astimezone()).isoformat()


def eventos(inicio: datetime, fim: datetime) -> list[dict]:
    r = _pede("GET", f"{AGENDA}/events", params={"timeMin": _iso(inicio), "timeMax": _iso(fim), "singleEvents": "true",
                                                 "orderBy": "startTime", "maxResults": 30})
    saida = []
    for e in r.get("items") or []:
        if e.get("status") == "cancelled":
            continue
        ini = e.get("start", {})
        dia_todo = "date" in ini and "dateTime" not in ini
        quando = date.fromisoformat(ini["date"]) if dia_todo else datetime.fromisoformat(ini["dateTime"]).astimezone().replace(tzinfo=None)
        saida.append({"id": e["id"], "titulo": e.get("summary") or "(sem título)", "inicio": quando, "dia_todo": dia_todo,
                      "local": e.get("location") or ""})
    return saida


def criar_evento(titulo: str, inicio: datetime | date, minutos: int = 60) -> dict:
    """Evento na agenda principal. `date` (sem hora) vira evento do dia inteiro."""
    if isinstance(inicio, datetime):
        corpo_ev = {"start": {"dateTime": _iso(inicio)}, "end": {"dateTime": _iso(inicio + timedelta(minutes=minutos))}}
    else:
        corpo_ev = {"start": {"date": inicio.isoformat()}, "end": {"date": (inicio + timedelta(days=1)).isoformat()}}
    return _pede("POST", f"{AGENDA}/events", json={"summary": titulo[:200], **corpo_ev})

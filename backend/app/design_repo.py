"""Pasta e histórico de cada projeto do Design: DATA_DIR/designs/<id>-<nome>/ é um repositório git.

    index.html   o que está no canvas: a versão atual, mais o rascunho se houver (não commitado)
    img/         fotos que vinham embutidas no HTML (data URI base64), uma por conteúdo (sha1)
    geradas/     área da skill gerar-imagens (PNG/WebP antes de entrarem no HTML); fora do git

Cada versão é um commit com a ref refs/versoes/vN (a ref mantém vivos os ramos: sem ela, commit que
não está em branch nenhuma some no gc). O pai do commit é a versão de onde ela nasceu (a "base").
No disco, o HTML guarda `src="img/<sha1>.webp"`; para o canvas e para o export o Forja põe a foto de
volta no HTML (o canvas só aceita data:). O banco continua com chat, passos e metadados; o HTML sai
de lá (projetos antigos migram sozinhos na primeira abertura).

Usa o git do sistema: sem ele, a tela Design mostra como instalar (não há modo sem git).
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import stat
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from . import config, db, design_html
from .tools import ToolError

RAIZ = config.DATA_DIR / "designs"
ANTIGA = config.DATA_DIR / "design"   # onde ficavam as imagens da skill antes da pasta por projeto
MIN_FOTO = 2048                        # data URI menor que isso fica no HTML (ícone, provisório)
_SRC = re.compile(r"""(\ssrc\s*=\s*")data:(image/(?:png|jpeg|webp|gif|svg\+xml|avif));base64,([A-Za-z0-9+/=]+)(")""", re.I)
_REF = re.compile(r"""(\ssrc\s*=\s*")img/([0-9a-f]{40})\.(png|jpg|webp|gif|svg|avif)(")""")
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/svg+xml": "svg", "image/avif": "avif"}
MIME = {v: k for k, v in EXT.items()}
_TRAVAS: dict[int, threading.RLock] = {}
_TRAVA_GERAL = threading.Lock()
AVISO_GIT = ("O Design guarda o histórico de versões com o Git, e ele não foi encontrado neste computador. "
             "Instale o Git (git-scm.com ou `winget install --id Git.Git -e`) e tente de novo.")


# ------------------------------------------------------------------ git

def _candidatos() -> list[str]:
    achado = shutil.which("git")
    fixos = [r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe",
             os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe")] if os.name == "nt" else []
    return [c for c in [achado, *fixos] if c]


_GIT: list[str] = []   # achado uma vez; enquanto não achar, procura de novo a cada pedido (instalou agora)


def git_exe() -> str | None:
    """O git do sistema. Procura também nas pastas padrão do instalador: quem instala com o Forja
    aberto não tem o PATH novo neste processo."""
    if _GIT:
        return _GIT[0]
    for c in _candidatos():
        if Path(c).is_file():
            _GIT.append(c)
            return c
    return None


def status_git() -> dict:
    exe = git_exe()
    if not exe:
        return {"ok": False, "aviso": AVISO_GIT, "baixar": "https://git-scm.com/download/win"}
    try:
        versao = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10, **_sem_janela()).stdout.strip()
    except OSError:
        _GIT.clear()
        return {"ok": False, "aviso": AVISO_GIT, "baixar": "https://git-scm.com/download/win"}
    return {"ok": True, "versao": versao, "caminho": exe}


def _sem_janela() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _git(raiz: Path, *args: str, binario: bool = False) -> str | bytes:
    exe = git_exe()
    if not exe:
        raise ToolError(AVISO_GIT)
    # config do usuário não entra: assinatura GPG, hooks, autocrlf e pager mudariam o que fica gravado
    base = [exe, "-C", str(raiz), "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false", "-c", f"core.hooksPath={raiz / '.git' / 'sem-hooks'}", "-c", "core.quotepath=false",
            "-c", "user.name=Forja", "-c", "user.email=forja@local", "-c", "gc.auto=0", "-c", "core.longpaths=true"]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    r = subprocess.run([*base, *args], capture_output=True, timeout=120, env=env, **_sem_janela())
    if r.returncode != 0:
        raise ToolError(f"git {args[0]} falhou: {r.stderr.decode('utf-8', 'replace').strip()[:400]}")
    return r.stdout if binario else r.stdout.decode("utf-8", "replace")


def _trava(conv_id: int) -> threading.RLock:
    with _TRAVA_GERAL:
        return _TRAVAS.setdefault(conv_id, threading.RLock())


# ------------------------------------------------------------------ pasta

def raiz(conv_id: int) -> Path | None:
    achadas = sorted(RAIZ.glob(f"{conv_id}-*")) + ([RAIZ / str(conv_id)] if (RAIZ / str(conv_id)).is_dir() else [])
    return achadas[0] if achadas else None


def _nova_raiz(conv_id: int) -> Path:
    with db.session() as s:
        c = s.get(db.Conversation, conv_id)
        nome = design_html.slug(c.title if c else "") or "projeto"
    p = RAIZ / f"{conv_id}-{nome[:40]}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def garantir(conv_id: int) -> Path:
    """A pasta do projeto, com o repositório (e a migração do banco, na primeira vez)."""
    with _trava(conv_id):
        r = raiz(conv_id)
        if r and (r / ".git").is_dir():
            return r
        r = r or _nova_raiz(conv_id)
        _git(r, "init", "-q")
        (r / ".gitignore").write_text("geradas/\n", "utf-8")
        _mover_geradas(conv_id, r)
        from . import design
        design._migrar_para_git(conv_id, r)
        return r


def geradas(conv_id: int) -> Path:
    """A área da skill gerar-imagens dentro da pasta do projeto."""
    p = garantir(conv_id) / "geradas"
    (p / "img").mkdir(parents=True, exist_ok=True)
    return p


def _mover_geradas(conv_id: int, r: Path) -> None:
    """Imagens da skill do tempo da pasta antiga (DATA_DIR/design/<id>) vêm para dentro do projeto,
    e a conversa de Imagens ligada a ele passa a apontar para o lugar novo."""
    velha = ANTIGA / str(conv_id)
    if not velha.is_dir():
        return
    destino = r / "geradas"
    if destino.exists():
        shutil.rmtree(destino, onerror=_apaga_readonly)
    shutil.move(str(velha), str(destino))
    with db.session() as s:
        for c in s.query(db.Conversation).filter(db.Conversation.kind == "imagem", db.Conversation.origem.isnot(None)):
            if (c.origem or {}).get("conv_id") == conv_id and c.workspace and Path(c.workspace) == velha:
                c.workspace = str(destino)
        s.commit()


def _apaga_readonly(func, caminho, _exc):
    os.chmod(caminho, stat.S_IWRITE)   # objetos do .git são somente leitura no Windows
    func(caminho)


def apagar(conv_id: int) -> None:
    """A conversa saiu: a pasta inteira vai junto (e a antiga, se ainda existir)."""
    with _trava(conv_id):
        r = raiz(conv_id)
        if r:
            shutil.rmtree(r, onerror=_apaga_readonly)
        if (ANTIGA / str(conv_id)).is_dir():
            shutil.rmtree(ANTIGA / str(conv_id), onerror=_apaga_readonly)
    _TRAVAS.pop(conv_id, None)


# ------------------------------------------------------------------ HTML ↔ disco

def separar(html: str) -> tuple[str, dict[str, bytes]]:
    """Fotos embutidas viram arquivos img/<sha1>.<ext>. Só as que voltam idênticas (base64 canônico)
    e grandes: o que não, fica no HTML — nunca muda o documento."""
    fotos: dict[str, bytes] = {}

    def troca(m: re.Match) -> str:
        b64 = m.group(3)
        if len(b64) < MIN_FOTO:
            return m.group(0)
        try:
            dados = base64.b64decode(b64, validate=True)
        except ValueError:
            return m.group(0)
        if base64.b64encode(dados).decode() != b64:
            return m.group(0)
        nome = f"{hashlib.sha1(dados).hexdigest()}.{EXT[m.group(2).lower()]}"
        fotos[nome] = dados
        return f"{m.group(1)}img/{nome}{m.group(4)}"
    return _SRC.sub(troca, html), fotos


def juntar(html: str, ler) -> str:
    """O inverso: img/<sha1>.<ext> volta a ser data URI. `ler(nome)` devolve os bytes (ou None)."""
    def troca(m: re.Match) -> str:
        nome = f"{m.group(2)}.{m.group(3)}"
        dados = ler(nome)
        if dados is None:
            return m.group(0)
        return f"{m.group(1)}data:{MIME[m.group(3)]};base64,{base64.b64encode(dados).decode()}{m.group(4)}"
    return _REF.sub(troca, html)


def gravar(conv_id: int, html: str) -> None:
    """O canvas mudou (versão nova ou rascunho): index.html e as fotos novas no disco."""
    r = garantir(conv_id)
    with _trava(conv_id):
        texto, fotos = separar(html)
        (r / "img").mkdir(exist_ok=True)
        for nome, dados in fotos.items():
            f = r / "img" / nome
            if not f.exists():
                f.write_bytes(dados)
        for f in (r / "img").iterdir():   # foto que o documento não usa mais sai da pasta (o git guarda)
            if f.name not in fotos and f.is_file():
                f.unlink()
        (r / "index.html").write_text(texto, "utf-8", newline="")
        _cache_trabalho.clear()


_cache_trabalho: dict[int, tuple[tuple, str]] = {}


def ler(conv_id: int) -> str:
    """O que está no canvas (index.html com as fotos de volta). "" = projeto sem documento."""
    r = raiz(conv_id)
    if not r or not (r / "index.html").is_file():
        if r is None and _tem_versoes(conv_id):
            r = garantir(conv_id)   # projeto antigo: migra agora
            if not (r / "index.html").is_file():
                return ""
        else:
            return ""
    idx = r / "index.html"
    chave = (idx.stat().st_mtime_ns, idx.stat().st_size)
    if (c := _cache_trabalho.get(conv_id)) and c[0] == chave:
        return c[1]
    ler_foto = lambda nome: (r / "img" / nome).read_bytes() if (r / "img" / nome).is_file() else None   # noqa: E731
    html = juntar(idx.read_text("utf-8"), ler_foto)
    _cache_trabalho[conv_id] = (chave, html)
    return html


def _tem_versoes(conv_id: int) -> bool:
    with db.session() as s:
        return s.query(db.Message).filter(db.Message.conversation_id == conv_id, db.Message.role == "assistant").count() > 0


# ------------------------------------------------------------------ versões

def _ref(n: int) -> str:
    return f"refs/versoes/v{n}"


def commitar(conv_id: int, html: str, n: int, descricao: str, base: int) -> str:
    """Versão n: parte da versão `base` (0 = raiz nova), grava o HTML e faz o commit. Devolve o sha."""
    r = garantir(conv_id)
    with _trava(conv_id):
        tem_base = base > 0 and _existe(r, _ref(base))
        if tem_base:
            _git(r, "checkout", "-q", "-f", "--detach", _ref(base))
        elif _existe(r, "HEAD"):   # raiz nova num repositório que já tem história: começa do zero
            _git(r, "checkout", "-q", "-f", "--orphan", f"raiz-v{n}")
            _git(r, "rm", "-q", "-r", "-f", "--cached", "--ignore-unmatch", ".")
        gravar(conv_id, html)
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "--allow-empty", "-m", f"v{n}: {descricao}"[:500])
        sha = str(_git(r, "rev-parse", "HEAD")).strip()
        _git(r, "update-ref", _ref(n), sha)
        if not tem_base and not base:
            _git(r, "checkout", "-q", "--detach", sha)   # sai do branch órfão: HEAD sempre solto na versão
        return sha


def _existe(r: Path, ref: str) -> bool:
    try:
        _git(r, "rev-parse", "-q", "--verify", ref + "^{commit}")
        return True
    except ToolError:
        return False


def ir(conv_id: int, n: int) -> None:
    """O canvas passa a mostrar a versão n (checkout)."""
    r = garantir(conv_id)
    with _trava(conv_id):
        _git(r, "checkout", "-q", "-f", "--detach", _ref(n))
        _git(r, "clean", "-q", "-f", "-d")   # foto nova do rascunho descartado (geradas/ é ignorada: fica)
        _cache_trabalho.pop(conv_id, None)


def versao(conv_id: int, n: int) -> str:
    """HTML de uma versão qualquer (com as fotos daquele commit)."""
    r = garantir(conv_id)
    sha = str(_git(r, "rev-parse", _ref(n) + "^{commit}")).strip()
    return _versao_sha(str(r), sha)


@lru_cache(maxsize=64)
def _versao_sha(r: str, sha: str) -> str:   # commit não muda: pode guardar
    raiz_ = Path(r)
    texto = str(_git(raiz_, "show", f"{sha}:index.html"))

    def ler_foto(nome: str):
        try:
            return _git(raiz_, "show", f"{sha}:img/{nome}", binario=True)
        except ToolError:
            return None
    return juntar(texto, ler_foto)


def limpo(conv_id: int) -> bool:
    """O index.html é o da versão (sem rascunho)."""
    r = raiz(conv_id)
    return not r or not str(_git(r, "status", "--porcelain", "--", "index.html", "img")).strip()

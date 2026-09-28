"""Anexos do chat.

Arquivos enviados vão para `.forja/uploads/` DENTRO da pasta de trabalho, então o agente pode
lê-los com read_file/run_command como qualquer outro arquivo. Imagens são enviadas ao modelo
como visão (content parts `image_url` no formato OpenAI; a conversão para Ollama fica em llm.py).

Documento de escritório (PDF, Word, Excel, PowerPoint) não precisa de tratamento aqui: o
`read_file` lê esses formatos direto, convertendo para Markdown — ver `documentos.py`. O que este
módulo faz é conferir, na hora do upload, se há mesmo texto a extrair, e avisar o agente quando não
há — com as duas saídas, na ordem: `preview_document`, que rende as páginas em JPEG e as entrega à
visão do modelo, e o `read_file` com `ocr=true`, que passa o OCR do sistema (ver `ocr.py`).
"""
from __future__ import annotations

import base64
import mimetypes
import re
import shutil
import time
import uuid
from pathlib import Path

from . import config, documentos, workspace

NOVA_LINHA = chr(10)
UPLOAD_DIR = ".forja/uploads"
MAX_IMAGE_BYTES = 8_000_000  # imagem maior que isso não vira data URL (estoura o contexto)
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_VIDEO_BYTES = 500_000_000  # vídeo nunca vai ao contexto: o teto é só de sanidade


def kind_of(mime: str) -> str:
    if mime in IMAGE_TYPES:
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("text/") or mime in ("application/json", "application/xml", "application/javascript"):
        return "text"
    return "file"


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "arquivo"
    return name[:80]


def _pasta(root: Path | None, conv: int | str | None) -> tuple[Path, str]:
    """`.forja/uploads/<conversa>/`: apagar a conversa leva a pasta dela inteira, prints e prévias
    incluídos. Sem conversa (anexo antes da primeira mensagem) cai na raiz dos uploads."""
    if conv is None:
        from . import shell  # tardio: shell puxa o resto das ferramentas
        conv = shell.CONV.get() or None
    rel = f"{UPLOAD_DIR}/{conv}" if conv and str(conv) != "0" else UPLOAD_DIR
    root = root or workspace.root()
    from . import gitops  # tardio: gitops puxa o llm
    gitops.ignora(root, UPLOAD_DIR)  # arquivo do usuário não vai parar no commit de ninguém
    return root / rel, rel


def save(name: str, data: bytes, mime: str | None = None, root: Path | None = None,
         conv: int | str | None = None) -> dict:
    # Documento tem o teto do documento, não o do texto: `MAX_FILE_BYTES` é 1 MB porque vale para
    # arquivo que vai INTEIRO ao contexto, e um PDF ou .xlsx de verdade passa disso sem esforço —
    # anexar qualquer documento real esbarrava nesse limite e parecia que o anexo não funcionava.
    teto = config.MAX_DOC_BYTES if Path(name).suffix.lower() in documentos.LEITURA else config.MAX_FILE_BYTES
    if (mime or mimetypes.guess_type(name)[0] or "").startswith("video/"):
        teto = MAX_VIDEO_BYTES
    if len(data) > teto:
        raise ValueError(f"Arquivo maior que o limite ({teto // 1024} KB). "
                         "Aumente em Configurações › Geral se precisar.")
    mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
    folder, rel = _pasta(root, conv)
    folder.mkdir(parents=True, exist_ok=True)
    # Único mesmo no mesmo segundo: dois prints seguidos (desktop e celular da revisão visual) saíam com
    # o mesmo nome, e o segundo sobrescrevia o primeiro — o modelo "via" o desktop e recebia o celular.
    filename = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{safe_name(name)}"
    (folder / filename).write_bytes(data)
    anexo = {"path": f"{rel}/{filename}", "name": name, "size": len(data), "mime": mime,
             "kind": kind_of(mime)}
    # Documento sem texto extraível (escaneado, protegido, corrompido): o agente precisa saber
    # agora, senão ele chama read_file, leva um erro e fica tentando de novo.
    if Path(filename).suffix.lower() in documentos.LEITURA:
        if documentos.extrair(folder / filename) is None:
            anexo["sem_texto"] = True
    return anexo


# Prints das ferramentas de navegador: fora do repositório (nada de lixo na pasta nem no git), um
# subdiretório por conversa, apagado com ela. Não é temporário de turno: o histórico relê o arquivo a
# cada turno para mandar a imagem ao modelo, e sumir com ele no meio da conversa reescreveria o
# histórico (e derrubaria o cache de prompt do modelo local — ver agent.build_history).
CAPTURAS_DIR = config.DATA_DIR / "capturas"
# Cópia do "antes/depois" de um card do board: o card sobrevive à conversa que fez os prints.
EVIDENCIAS_DIR = config.DATA_DIR / "board"
CAPTURA_AVULSA_DIAS = 7  # print sem conversa (servidor MCP, rota avulsa): expurgo por idade


def salvar_captura(name: str, data: bytes, mime: str = "image/jpeg") -> dict:
    from . import shell  # tardio: shell puxa o resto das ferramentas
    pasta = CAPTURAS_DIR / (shell.CONV.get() or "0")
    pasta.mkdir(parents=True, exist_ok=True)
    f = pasta / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{safe_name(name)}"
    f.write_bytes(data)
    return {"path": str(f), "name": name, "size": len(data), "mime": mime, "kind": kind_of(mime), "captura": True}


def limpar_capturas_avulsas() -> int:
    pasta, limite, n = CAPTURAS_DIR / "0", time.time() - CAPTURA_AVULSA_DIAS * 86400, 0
    for f in pasta.glob("*") if pasta.is_dir() else ():
        try:
            if f.stat().st_mtime < limite:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n


# Caminhos anexados por referência nesta execução do backend e ainda sem mensagem (a miniatura do
# campo de mensagem aparece antes do envio). Depois do envio vale o que está gravado na conversa.
_REFERENCIADOS: set[str] = set()


def referenciar(caminho: str, root: Path) -> dict:
    """Anexo do disco do usuário usado onde está, sem cópia (o app sabe o caminho do arquivo escolhido
    ou arrastado). Dentro da pasta da conversa vira caminho relativo, como qualquer arquivo dela; fora,
    fica absoluto e só para leitura: as ferramentas de escrita continuam presas à pasta de trabalho."""
    f = Path(caminho)
    if not f.is_absolute() or not f.is_file():
        raise ValueError(f"Arquivo não encontrado: {caminho}")
    f = f.resolve()
    mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    try:
        path = f.relative_to(root.resolve()).as_posix()
    except ValueError:
        path = str(f)
        _REFERENCIADOS.add(path)
    anexo = {"path": path, "name": f.name, "size": f.stat().st_size, "mime": mime, "kind": kind_of(mime),
             **({"externo": True} if Path(path).is_absolute() else {})}
    if f.suffix.lower() in documentos.LEITURA and documentos.extrair(f) is None:
        anexo["sem_texto"] = True
    return anexo


def _anexos(conv_id: int) -> list[dict]:
    from . import db  # tardio: db é pesado e este módulo é importado cedo
    with db.session() as s:
        metas = [meta for (meta,) in s.query(db.Message.meta)
                 .filter(db.Message.conversation_id == int(conv_id)).all() if meta]
    return [a for meta in metas for a in (meta.get("attachments") or []) if isinstance(a, dict) and a.get("path")]


def externo_liberado(conv_id: int | str | None, caminho: str) -> bool:
    """Arquivo fora da pasta de trabalho que esta conversa pode ler e mostrar: um anexo por referência,
    um print dela, ou a cópia de print que um card do board guarda."""
    try:
        f = Path(caminho).resolve()
    except OSError:
        return False
    alvo = str(f)
    if alvo in _REFERENCIADOS or f.is_relative_to(EVIDENCIAS_DIR.resolve()):
        return True
    if conv_id and f.is_relative_to((CAPTURAS_DIR / str(conv_id)).resolve()):
        return True
    return bool(conv_id) and str(conv_id) != "0" and any(
        a.get("externo") and str(Path(a["path"]).resolve()) == alvo for a in _anexos(int(conv_id)))


def apagar_da_conversa(conv_id: int, root: Path | None) -> int:
    """Apagar a conversa leva as cópias que o Forja fez para ela: os prints (`capturas/<id>/`), a pasta
    `.forja/uploads/<id>/` e, das versões antigas (uploads soltos na raiz), os anexos citados nas
    mensagens. Anexo por referência é o arquivo do usuário, no lugar dele: nunca é tocado. Chamar ANTES
    de apagar as mensagens."""
    n = 0
    capturas = CAPTURAS_DIR / str(conv_id)
    if capturas.is_dir():
        n += sum(1 for f in capturas.iterdir() if f.is_file())
        shutil.rmtree(capturas, ignore_errors=True)
    if root is None:
        return n
    base = (root / UPLOAD_DIR).resolve()
    for a in _anexos(conv_id):
        if a.get("externo") or Path(a["path"]).is_absolute():
            continue
        f = (root / a["path"]).resolve()
        if f.parent == base and f.is_file():  # só a raiz antiga; a pasta da conversa sai inteira abaixo
            try:
                f.unlink()
                n += 1
            except OSError:
                pass  # aberto em outro programa: fica
    pasta = base / str(conv_id)
    if pasta.is_dir():
        n += sum(1 for f in pasta.rglob("*") if f.is_file())
        shutil.rmtree(pasta, ignore_errors=True)
    return n


def caminho(attachment: dict, root: Path | None = None) -> Path:
    return (root or workspace.root()) / attachment["path"]  # absoluto (por referência) ignora a raiz


def data_url(attachment: dict) -> str | None:
    p = caminho(attachment)
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_IMAGE_BYTES:
        return None
    return f"data:{attachment['mime']};base64,{base64.b64encode(raw).decode()}"


def user_message(content: str, attachments: list | None) -> dict:
    """Mensagem do usuário no formato OpenAI, com imagens como content parts."""
    attachments = attachments or []
    images = [a for a in attachments if a.get("kind") == "image"]
    others = [a for a in attachments if a.get("kind") != "image"]
    text = content
    dentro = [a for a in others if not a.get("externo")]
    fora = [a for a in others if a.get("externo")]
    if dentro:
        lista = ", ".join(a["path"] for a in dentro)
        text += NOVA_LINHA * 2 + (
            f"[Arquivos anexados na pasta de trabalho: {lista} — use read_file para ler. Para "
            "alterar um deles, use edit_document (ou edit_spreadsheet) NESSE MESMO caminho: "
            "write_document criaria outro arquivo, só com o que você escrevesse, e o conteúdo "
            "atual se perderia.]")
    if fora:
        lista = ", ".join(a["path"] for a in fora)
        text += NOVA_LINHA * 2 + (
            f"[Arquivos anexados do computador do usuário, fora da pasta de trabalho: {lista} — use "
            "read_file com esse caminho absoluto para ler. Eles são só leitura: para entregar uma "
            "versão alterada, grave um arquivo novo na pasta de trabalho.]")
    for a in others:
        if a.get("sem_texto"):
            text += NOVA_LINHA + (
                f"[De {a['name']} não sai texto direto (PDF escaneado, arquivo protegido ou "
                "corrompido). Não é motivo para desistir, e a ordem importa: se você recebe "
                "imagens, preview_document mostra as páginas e você transcreve o que vê, que é "
                "a leitura mais fiel; se não recebe, ou se a prévia não resolveu, read_file com "
                "ocr=true passa o OCR do sistema. Ilegível só depois das duas.]")
    if not images:
        return {"role": "user", "content": text}
    parts: list[dict] = [{"type": "text", "text": text}]
    for a in images:
        url = data_url(a)
        if url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
        else:
            parts[0]["text"] += NOVA_LINHA + f"[Imagem {a['path']} não pôde ser enviada (muito grande ou ausente).]"
    return {"role": "user", "content": parts}

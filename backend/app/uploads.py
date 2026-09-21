"""Anexos do chat.

Arquivos enviados vão para `.forja/uploads/` DENTRO da pasta de trabalho, então o agente pode
lê-los com read_file/run_command como qualquer outro arquivo. Imagens são enviadas ao modelo
como visão (content parts `image_url` no formato OpenAI; a conversão para Ollama fica em llm.py).

PDF ganha um `.txt` ao lado, extraído na hora do upload: o `read_file` recusa binário, então
anexar um PDF só mandava o modelo abrir um arquivo que ele nunca conseguiria ler.
"""
from __future__ import annotations

import base64
import mimetypes
import re
import time
from pathlib import Path

from . import config, workspace

NOVA_LINHA = chr(10)
UPLOAD_DIR = ".forja/uploads"
MAX_IMAGE_BYTES = 8_000_000  # imagem maior que isso não vira data URL (estoura o contexto)
MAX_PDF_PAGES = 300          # teto da extração: PDF gigante não vale travar o upload
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def kind_of(mime: str) -> str:
    if mime in IMAGE_TYPES:
        return "image"
    if mime.startswith("text/") or mime in ("application/json", "application/xml", "application/javascript"):
        return "text"
    return "file"


def extrair_pdf(origem: Path) -> str | None:
    """Texto do PDF, ou None quando não dá. Sem OCR: PDF escaneado é imagem, não texto."""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover — vem no requirements.txt
        return None
    try:
        leitor = PdfReader(str(origem))
        if leitor.is_encrypted:
            leitor.decrypt("")  # PDF só com senha de dono abre com senha vazia
        total = len(leitor.pages)
        paginas = [(pagina.extract_text() or "").strip() for pagina in leitor.pages[:MAX_PDF_PAGES]]
    except Exception:  # pypdf levanta de tudo em arquivo malformado, e um anexo não derruba o upload
        return None
    corpo = (NOVA_LINHA * 2).join(
        f"--- página {i} ---" + NOVA_LINHA + texto for i, texto in enumerate(paginas, 1) if texto)
    if not corpo.strip():
        return None
    sobrou = " (as demais não foram extraídas)" if total > MAX_PDF_PAGES else ""
    return f"{total} página(s){sobrou}." + NOVA_LINHA * 2 + corpo


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "arquivo"
    return name[:80]


def save(name: str, data: bytes, mime: str | None = None, root: Path | None = None) -> dict:
    if len(data) > config.MAX_FILE_BYTES:
        raise ValueError(f"Arquivo maior que o limite ({config.MAX_FILE_BYTES} bytes). "
                         "Aumente em Configurações › Geral se precisar.")
    mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
    folder = (root or workspace.root()) / UPLOAD_DIR
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_name(name)}"
    (folder / filename).write_bytes(data)
    anexo = {"path": f"{UPLOAD_DIR}/{filename}", "name": name, "size": len(data), "mime": mime,
             "kind": kind_of(mime)}
    if mime == "application/pdf":
        texto = extrair_pdf(folder / filename)
        if texto:
            (folder / f"{filename}.txt").write_text(texto, encoding="utf-8")
            anexo["text_path"] = f"{UPLOAD_DIR}/{filename}.txt"
        else:
            anexo["sem_texto"] = True  # escaneado ou protegido: o agente precisa saber disso
    return anexo


def data_url(attachment: dict) -> str | None:
    p = workspace.root() / attachment["path"]
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
    if others:
        lista = ", ".join(a.get("text_path") or a["path"] for a in others)
        text += NOVA_LINHA * 2 + f"[Arquivos anexados na pasta de trabalho: {lista} — use read_file para ler.]"
        for a in others:
            if a.get("text_path"):
                nome, txt, orig = a["name"], a["text_path"], a["path"]
                text += NOVA_LINHA + f"[{nome} é um PDF: o texto extraído está em {txt}, o original em {orig}.]"
            elif a.get("sem_texto"):
                nome = a["name"]
                text += NOVA_LINHA + f"[{nome} é um PDF sem texto extraível (provavelmente escaneado): "
                text += "read_file não vai abri-lo. Diga isso ao usuário.]"
    if not images:
        return {"role": "user", "content": text}
    parts: list[dict] = [{"type": "text", "text": text}]
    for a in images:
        url = data_url(a)
        if url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
        else:
            parts[0]["text"] += f"\n[Imagem {a['path']} não pôde ser enviada (muito grande ou ausente).]"
    return {"role": "user", "content": parts}

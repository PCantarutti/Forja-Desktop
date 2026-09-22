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
import time
from pathlib import Path

from . import config, documentos, workspace

NOVA_LINHA = chr(10)
UPLOAD_DIR = ".forja/uploads"
MAX_IMAGE_BYTES = 8_000_000  # imagem maior que isso não vira data URL (estoura o contexto)
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def kind_of(mime: str) -> str:
    if mime in IMAGE_TYPES:
        return "image"
    if mime.startswith("text/") or mime in ("application/json", "application/xml", "application/javascript"):
        return "text"
    return "file"


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "arquivo"
    return name[:80]


def save(name: str, data: bytes, mime: str | None = None, root: Path | None = None) -> dict:
    # Documento tem o teto do documento, não o do texto: `MAX_FILE_BYTES` é 1 MB porque vale para
    # arquivo que vai INTEIRO ao contexto, e um PDF ou .xlsx de verdade passa disso sem esforço —
    # anexar qualquer documento real esbarrava nesse limite e parecia que o anexo não funcionava.
    teto = config.MAX_DOC_BYTES if Path(name).suffix.lower() in documentos.LEITURA else config.MAX_FILE_BYTES
    if len(data) > teto:
        raise ValueError(f"Arquivo maior que o limite ({teto // 1024} KB). "
                         "Aumente em Configurações › Geral se precisar.")
    mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
    folder = (root or workspace.root()) / UPLOAD_DIR
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_name(name)}"
    (folder / filename).write_bytes(data)
    anexo = {"path": f"{UPLOAD_DIR}/{filename}", "name": name, "size": len(data), "mime": mime,
             "kind": kind_of(mime)}
    # Documento sem texto extraível (escaneado, protegido, corrompido): o agente precisa saber
    # agora, senão ele chama read_file, leva um erro e fica tentando de novo.
    if Path(filename).suffix.lower() in documentos.LEITURA:
        if documentos.extrair(folder / filename) is None:
            anexo["sem_texto"] = True
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
        lista = ", ".join(a["path"] for a in others)
        text += NOVA_LINHA * 2 + (
            f"[Arquivos anexados na pasta de trabalho: {lista} — use read_file para ler. Para "
            "alterar um deles, use edit_document (ou edit_spreadsheet) NESSE MESMO caminho: "
            "write_document criaria outro arquivo, só com o que você escrevesse, e o conteúdo "
            "atual se perderia.]")
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

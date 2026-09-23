"""Lotes de imagem: várias variações de um prompt, divididas entre modelos, com aprovação depois.

O `sd-cli` gera uma imagem por processo, então um lote é uma fila numa thread só — e o mutex de VRAM
(`localai.set_image_busy`) vale para o lote inteiro, não por imagem.

Nada de tabela nova: um lote são duas mensagens de uma Conversation(kind="imagem") —
a do usuário com o pedido, e a do assistente com a lista de imagens, que a thread vai preenchendo.

As reprovadas não são apagadas na hora: vão para `<pasta>/descartadas/` e o expurgo leva as que
passarem de `image.descarte_dias` (padrão 7).
"""
from __future__ import annotations

import random
import shutil
import threading
import time
from pathlib import Path

from . import db, downloads, imagegen, localai, mirror
from .tools import ToolError

DESCARTADAS = "descartadas"
MAX_VARIACOES = 50  # o sd-cli é sequencial; acima disso é espera, não geração
SEED_MAX = 2**31 - 1


def descartadas_dir() -> Path:
    return imagegen.out_dir() / DESCARTADAS


def _sementes(count: int, seed: int, modo: str) -> list[int]:
    """A semente é sempre decidida aqui, nunca pelo sd.cpp: sem isso não dá para repetir a imagem."""
    if modo == "aleatoria":
        return [random.randint(1, SEED_MAX) for _ in range(count)]
    base = int(seed) or random.randint(1, SEED_MAX)
    if modo == "fixa":  # mesma semente em todas: compara modelos com a variável travada
        return [base] * count
    return [(base + i) % SEED_MAX or 1 for i in range(count)]  # incremental (padrão)


def _distribuir(models: list[str], count: int) -> list[str]:
    """Blocos contíguos, resto nos primeiros: 10 em 2 modelos = 5+5; 10 em 3 = 4+3+3."""
    if not models:
        raise ToolError("Escolha pelo menos um modelo de imagem.")
    por, resto = divmod(count, len(models))
    out: list[str] = []
    for i, m in enumerate(models):
        out += [m] * (por + (1 if i < resto else 0))
    return out


def _nome(m: str) -> str:
    return Path(m).stem if m else ""


def _save(conv_id: int, **fields) -> db.Message:
    with db.session() as s:
        m = db.Message(conversation_id=conv_id, **fields)
        s.add(m)
        conv = s.get(db.Conversation, conv_id)
        if conv:
            conv.updated_at = db._now()
        s.commit()
        return m


def _patch(message_id: int, **fields) -> dict:
    """meta é JSON puro (sem MutableDict): só persiste se o dict for reatribuído inteiro."""
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m:
            raise ToolError("Lote não encontrado.")
        meta = dict(m.meta or {})
        if "meta" in fields:
            meta.update(fields.pop("meta"))
            m.meta = meta
        for k, v in fields.items():
            setattr(m, k, v)
        s.commit()
        return {**m.to_dict(), "conversation_id": m.conversation_id}


def _mensagem(message_id: int) -> dict:
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or not (m.meta or {}).get("images"):
            raise ToolError("Lote não encontrado.")
        return {**m.to_dict(), "conversation_id": m.conversation_id}


# ------------------------------------------------------------------ geração

def start(conv_id: int, prompt: str, opts: dict | None = None, models: list[str] | None = None,
          count: int = 1, seed: int = 0, seed_mode: str = "incremental", confirm: bool = False,
          refs: list[str] | None = None) -> dict:
    """Enfileira o lote e devolve a mensagem do assistente já criada (a thread preenche o resto)."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ToolError("Descreva a imagem (prompt vazio).")
    count = max(1, min(int(count or 1), MAX_VARIACOES))
    opts = {k: v for k, v in (opts or {}).items() if v not in (None, "")}
    escolhidos = _distribuir(list(models or []), count)
    refs = [str(r) for r in (refs or [])]
    exe = imagegen._exe()
    for m in dict.fromkeys(escolhidos):  # valida runtime e modelo ANTES de descarregar o LLM por nada
        imagegen.argv(exe, prompt, imagegen.OUT_DIR / "x.png", imagegen._opts({**opts, "model": m}), refs)

    if localai.status()["running"]:
        if not confirm:
            raise imagegen.ModeloCarregado(localai.status().get("alias") or "um modelo")
        localai.unload()

    sementes = _sementes(count, seed, seed_mode)
    pasta = imagegen.out_dir()
    marca = time.strftime("%Y%m%d-%H%M%S")
    imagens = [{"path": str(pasta / f"{marca}-{i:02d}-s{s}.png"), "seed": s, "model": m,
                "model_name": _nome(m), "status": "pendente", "error": ""}
               for i, (m, s) in enumerate(zip(escolhidos, sementes))]

    with db.session() as s:
        conv = s.get(db.Conversation, conv_id)
        if not conv:
            raise ToolError("Conversa não encontrada.")
        if conv.title == "Nova conversa":
            conv.title = prompt.splitlines()[0][:60] or "Nova conversa"
        s.commit()

    _save(conv_id, role="user", content=prompt,
          meta={"opts": opts, "models": list(dict.fromkeys(escolhidos)), "count": count,
                "seed": seed, "seed_mode": seed_mode, "refs": refs})
    job = downloads.create("lote", prompt[:60])
    downloads.update(job["id"], done=0, total=count)
    msg = _save(conv_id, role="assistant", content="", status="running",
                meta={"job": job["id"], "count": count, "seed_mode": seed_mode,
                      "opts": opts, "images": imagens})

    threading.Thread(target=_trabalhar, args=(conv_id, msg.id, prompt, opts, job["id"], refs), daemon=True).start()
    return msg.to_dict()


def _trabalhar(conv_id: int, message_id: int, prompt: str, opts: dict, job_id: str,
               refs: list[str] | None = None) -> None:
    imagens = list(_mensagem(message_id)["meta"]["images"])
    localai.set_image_busy(True)
    erro = ""
    try:
        for i, item in enumerate(imagens):
            if downloads.cancelled(job_id):
                for resto in imagens[i:]:
                    resto["status"] = "cancelada"
                _patch(message_id, meta={"images": imagens})
                break
            item["status"] = "gerando"
            item["progress"] = 0.0
            _patch(message_id, meta={"images": imagens})

            def progresso(passo: int, total: int, s_passo: float = 0.0, item=item) -> None:
                # Vai no meta porque a tela já consulta a conversa enquanto o lote roda: nada de rota nova.
                item["progress"] = round(passo / total, 3) if total else 0.0
                item["s_passo"] = round(s_passo, 2)
                item["restante"] = round(max(0, total - passo) * s_passo)  # só a amostragem; o VAE vem depois
                _patch(message_id, meta={"images": imagens})

            try:
                imagegen.generate(prompt, Path(item["path"]), {**opts, "model": item["model"],
                                                               "seed": item["seed"]}, job_id, refs or [],
                                  progresso)
                item["status"] = "pronta"
            except Exception as e:
                # o próprio generate mata o sd-cli quando o job é cancelado no meio de uma imagem
                cancelada = downloads.cancelled(job_id)
                item["status"] = "cancelada" if cancelada else "erro"
                item["error"] = "" if cancelada else str(e)
                if not cancelada:
                    erro = erro or str(e)
            # o generate move a barra por passo; aqui ela volta a contar imagens do lote
            downloads.update(job_id, done=i + 1, total=len(imagens))
            _patch(message_id, meta={"images": imagens})
    finally:
        localai.set_image_busy(False)

    pronta = any(i["status"] == "pronta" for i in imagens)
    cancelado = any(i["status"] == "cancelada" for i in imagens)
    status = "pronto" if pronta else ("cancelado" if cancelado else "erro")
    downloads.finish(job_id, error="" if pronta else erro)
    mirror.write(conv_id)
    limpar_descartadas()
    # o status sai por último de propósito: é o sinal de "acabou" para quem espera o lote, e nada
    # pode acontecer depois dele (nos testes, o monkeypatch das pastas já teria sido desfeito).
    _patch(message_id, status=status, meta={"images": imagens})


def cancelar(message_id: int) -> dict:
    meta = _mensagem(message_id)["meta"]
    downloads.cancel(meta.get("job") or "")
    return {"ok": True}


# ------------------------------------------------------------------ aprovação

def decidir(message_id: int, keep: list[str]) -> dict:
    """As aprovadas ficam onde estão; o resto vai para descartadas/ (some sozinho no expurgo)."""
    m = _mensagem(message_id)
    imagens = [dict(i) for i in m["meta"]["images"]]
    manter = {str(p) for p in (keep or [])}
    destino = descartadas_dir()
    for item in imagens:
        if item["status"] not in ("pronta", "mantida", "descartada"):
            continue
        if item["path"] in manter:
            if item["status"] == "descartada":  # desfazer: volta para a pasta de saída
                alvo = imagegen.out_dir() / Path(item["path"]).name
                if Path(item["path"]).exists():
                    alvo.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(item["path"], alvo)
                item["path"] = str(alvo)
            item["status"] = "mantida"
        elif item["status"] != "descartada":
            origem = Path(item["path"])
            if origem.exists():
                destino.mkdir(parents=True, exist_ok=True)
                alvo = destino / origem.name
                shutil.move(str(origem), str(alvo))
                item["path"] = str(alvo)
            item["status"] = "descartada"
    out = _patch(message_id, meta={"images": imagens})
    mirror.write(out["conversation_id"])
    return out


def limpar_descartadas(dias: int | None = None) -> int:
    """Expurgo por idade. Sem agendador: roda na subida do app e no fim de cada lote.

    `dias=None` usa o prazo do config, onde 0 significa guardar para sempre. Passar `dias<=0` na
    chamada é o "Esvaziar agora" do botão: leva tudo.
    """
    pasta = descartadas_dir()
    if not pasta.is_dir():
        return 0
    if dias is None:
        dias = int(localai.read_config()["image"].get("descarte_dias") or 0)
        if dias <= 0:
            return 0
    limite = time.time() - dias * 86400 if dias > 0 else time.time() + 1
    apagados = 0
    for f in pasta.glob("*.png"):
        try:
            if f.stat().st_mtime < limite:
                f.unlink()
                apagados += 1
        except OSError:
            pass
    return apagados

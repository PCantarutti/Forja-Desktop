"""Lotes de imagem (e de vídeo): várias variações de um prompt, divididas entre modelos, com aprovação
depois. Numa Conversation(kind="video") cada item é um .webm do Wan; o resto do caminho é o mesmo.

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


def previas_dir() -> Path:
    """Prévia de cada imagem enquanto ela gera. Dentro da pasta de saída porque é de lá que a rota de
    arquivo aceita servir; o arquivo some quando a imagem termina."""
    return imagegen.out_dir() / ".previas"


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


# ------------------------------------------------------------------ referências

EXT_REFERENCIA = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MAX_REFERENCIAS = 500  # ponytail: lista no local.json; as mais antigas saem (a miniatura delas some)


def registrar_referencia(path: str) -> str:
    """Imagem do disco da pessoa para editar: usada onde está, sem cópia. Fica registrada para a rota
    de arquivo poder mostrar a miniatura (ela não serve arquivo qualquer do disco)."""
    f = Path(path)
    if not f.is_absolute() or f.suffix.lower() not in EXT_REFERENCIA:
        raise ToolError("Anexe uma imagem (PNG, JPG ou WebP).")
    if not f.is_file():
        raise ToolError(f"Imagem de referência não encontrada: {path}")
    if f.stat().st_size > 50_000_000:
        raise ToolError("Imagem maior que 50 MB.")
    chave = localai._chave(str(f))
    data = localai.read_config()
    lista = [p for p in data.get("referencias") or [] if p != chave] + [chave]
    data["referencias"] = lista[-MAX_REFERENCIAS:]
    localai.write_config(data)
    return str(f)


def eh_referencia(path: str) -> bool:
    return localai._chave(path) in (localai.read_config().get("referencias") or [])


# ------------------------------------------------------------------ geração

def _liberar_vram(confirm: bool) -> None:
    """LLM na VRAM: sem confirmação a tela pergunta (409); com ela, descarrega."""
    if localai.status()["running"]:
        if not confirm:
            raise imagegen.ModeloCarregado(localai.status().get("alias") or "um modelo")
        localai.unload()


def start(conv_id: int, prompt: str, opts: dict | None = None, models: list[str] | None = None,
          count: int = 1, seed: int = 0, seed_mode: str = "incremental", confirm: bool = False,
          refs: list[str] | None = None) -> dict:
    """Enfileira o lote e devolve a mensagem do assistente já criada (a thread preenche o resto)."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ToolError("Descreva a imagem (prompt vazio).")
    count = max(1, min(int(count or 1), MAX_VARIACOES))
    if localai.image_busy():
        raise ToolError("Já tem uma geração em andamento (imagem ou vídeo): espere terminar ou cancele.")
    opts = {k: v for k, v in (opts or {}).items() if v not in (None, "")}
    escolhidos = _distribuir(list(models or []), count)
    refs = [str(r) for r in (refs or [])]
    with db.session() as s:
        conv = s.get(db.Conversation, conv_id)
        ext = ".webm" if conv and conv.kind == "video" else ".png"
    exe = imagegen._exe()
    for m in dict.fromkeys(escolhidos):  # valida runtime e modelo ANTES de descarregar o LLM por nada
        imagegen.argv(exe, prompt, imagegen.OUT_DIR / f"x{ext}", imagegen._opts({**opts, "model": m}), refs)

    _liberar_vram(confirm)

    sementes = _sementes(count, seed, seed_mode)
    pasta = imagegen.out_dir()
    marca = time.strftime("%Y%m%d-%H%M%S")
    imagens = [{"path": str(pasta / f"{marca}-{i:02d}-s{s}{ext}"), "seed": s, "model": m,
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
    feitas, alvo = 0, sum(i["status"] == "pendente" for i in imagens)
    try:
        for i, item in enumerate(imagens):
            if item["status"] != "pendente":  # "Continuar": o que já saiu fica como está
                continue
            if downloads.cancelled(job_id):
                for resto in imagens[i:]:
                    if resto["status"] == "pendente":
                        resto["status"] = "cancelada"
                _patch(message_id, meta={"images": imagens})
                break
            item["status"] = "gerando"
            item["progress"] = 0.0
            # Já no começo (carregando o modelo, antes da 1ª prévia): o card sabe que não vai de líquido.
            item["com_previa"] = imagegen.modo_previa(imagegen._opts({**opts, "model": item["model"]})) is not None
            _patch(message_id, meta={"images": imagens})
            # Prévia de vídeo tem vários quadros: com .png o sd-cli grava .avi, que o Chromium não toca;
            # WebP animado ele grava e o <img> do card anima sozinho.
            previa = previas_dir() / (Path(item["path"]).stem + (".webp" if item["path"].endswith(".webm") else ".png"))
            previa.parent.mkdir(parents=True, exist_ok=True)

            def progresso(passo: int, total: int, s_passo: float = 0.0, item=item, previa=previa) -> None:
                # Vai no meta porque a tela já consulta a conversa enquanto o lote roda: nada de rota nova.
                # A prévia só entra quando o sd-cli já gravou a primeira: até lá o card mostra o que tinha
                # (na edição, a imagem original).
                if previa.is_file():
                    item["preview"] = str(previa)
                item["progress"] = round(passo / total, 3) if total else 0.0
                item["s_passo"] = round(s_passo, 2)
                item["restante"] = round(max(0, total - passo) * s_passo)  # só a amostragem; o VAE vem depois
                _patch(message_id, meta={"images": imagens})

            try:
                medido: dict = {}
                imagegen.generate(prompt, Path(item["path"]), {**opts, "model": item["model"],
                                                               "seed": item["seed"]}, job_id, refs or [],
                                  progresso, previa, medido)
                item["status"] = "pronta"
                if item.get("s_passo") and medido.get("segundos"):  # base do "≈ N min", em qualquer conversa
                    localai.anotar_tempo(item["model"], imagegen._opts({**opts, "model": item["model"]}),
                                         float(item["s_passo"]), float(medido["segundos"]))
            except Exception as e:
                # o próprio generate mata o sd-cli quando o job é cancelado no meio de uma imagem
                cancelada = downloads.cancelled(job_id)
                item["status"] = "cancelada" if cancelada else "erro"
                item["error"] = "" if cancelada else str(e)
                if not cancelada:
                    erro = erro or str(e)
            item.pop("preview", None)
            item.pop("com_previa", None)
            previa.unlink(missing_ok=True)
            # o generate move a barra por passo; aqui ela volta a contar imagens do lote
            feitas += 1
            downloads.update(job_id, done=feitas, total=alvo)
            _patch(message_id, meta={"images": imagens})
    finally:
        localai.set_image_busy(False)

    pronta = any(i["status"] in ("pronta", "mantida", "descartada") for i in imagens)
    cancelado = any(i["status"] == "cancelada" for i in imagens)
    status = "pronto" if pronta else ("cancelado" if cancelado else "erro")
    downloads.finish(job_id, error="" if pronta else erro)
    mirror.write(conv_id)
    limpar_descartadas()
    # o status sai por último de propósito: é o sinal de "acabou" para quem espera o lote, e nada
    # pode acontecer depois dele (nos testes, o monkeypatch das pastas já teria sido desfeito).
    _patch(message_id, status=status, meta={"images": imagens})


# O que o lote ainda não entregou e "Continuar" gera de novo.
A_REFAZER = ("interrompida", "pendente", "cancelada", "erro")


def reap() -> int:
    """Na subida do backend nenhum lote está rodando: os que ficaram "running" são de uma queda (o app
    fechou no meio). Viram "interrompido", para a tela parar de esperar e oferecer "Continuar". A
    imagem que estava no meio perde os passos (o sd-cli não salva estado parcial); se o PNG chegou a
    ser gravado antes da queda, ela conta como pronta."""
    shutil.rmtree(previas_dir(), ignore_errors=True)  # prévias de imagens que não terminaram
    with db.session() as s:
        presos = s.query(db.Message).filter(db.Message.role == "assistant", db.Message.status == "running").all()
        n = 0
        for m in presos:
            imagens = [dict(i) for i in (m.meta or {}).get("images") or []]
            if not imagens:
                continue  # não é lote de imagem
            for i in imagens:
                if i["status"] in ("gerando", "pendente"):
                    i["status"] = "pronta" if Path(i["path"]).is_file() else "interrompida"
                    i.pop("progress", None)
                    i.pop("preview", None)
                    i.pop("com_previa", None)
            m.meta = {**m.meta, "images": imagens}
            m.status = "interrompido" if any(i["status"] == "interrompida" for i in imagens) else "pronto"
            n += 1
        s.commit()
        return n


def continuar(message_id: int, confirm: bool = False) -> dict:
    """Gera o que faltou do lote (interrompidas, canceladas, com erro), com as mesmas sementes e
    ajustes: sai a mesma imagem que teria saído. As prontas ficam como estão."""
    msg = _mensagem(message_id)
    if msg["status"] == "running":
        raise ToolError("O lote ainda está rodando.")
    imagens = list(msg["meta"]["images"])
    if not any(i["status"] in A_REFAZER for i in imagens):
        raise ToolError("Nada a continuar: todas as imagens deste lote já saíram.")
    with db.session() as s:
        pedido = (s.query(db.Message)
                  .filter(db.Message.conversation_id == msg["conversation_id"], db.Message.role == "user",
                          db.Message.id < message_id)
                  .order_by(db.Message.id.desc()).first())
        if not pedido:
            raise ToolError("Pedido do lote não encontrado.")
        prompt, refs = pedido.content, list((pedido.meta or {}).get("refs") or [])
    _liberar_vram(confirm)
    for i in imagens:
        if i["status"] in A_REFAZER:
            i.update(status="pendente", error="")
    faltam = sum(i["status"] == "pendente" for i in imagens)
    job = downloads.create("lote", prompt[:60])
    downloads.update(job["id"], done=0, total=faltam)
    _patch(message_id, status="running", meta={"job": job["id"], "images": imagens})
    opts = msg["meta"].get("opts") or {}
    threading.Thread(target=_trabalhar, args=(msg["conversation_id"], message_id, prompt, opts, job["id"], refs),
                     daemon=True).start()
    return {"ok": True}


def cancelar(message_id: int) -> dict:
    meta = _mensagem(message_id)["meta"]
    downloads.cancel(meta.get("job") or "")
    return {"ok": True}


# ------------------------------------------------------------------ aprovação

def decidir(message_id: int, keep: list[str], apenas: list[str] | None = None) -> dict:
    """As aprovadas ficam onde estão; o resto vai para descartadas/ (some sozinho no expurgo).

    `apenas`: só estas mudam, as outras ficam como estão. O foco do vídeo decide uma tomada por vez, e
    mandar as demais como "keep" as marcava todas como mantidas."""
    m = _mensagem(message_id)
    imagens = [dict(i) for i in m["meta"]["images"]]
    manter = {str(p) for p in (keep or [])}
    so = {str(p) for p in apenas} if apenas else None
    destino = descartadas_dir()
    for item in imagens:
        if item["status"] not in ("pronta", "mantida", "descartada"):
            continue
        if so is not None and item["path"] not in so:
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


def imagens_da_conversa(conv_id: int) -> list[Path]:
    """Os arquivos que os lotes desta conversa geraram e ainda existem (inclusive em descartadas/).
    Só o que está dentro da pasta de imagens: referência anexada do disco da pessoa nunca entra."""
    pastas = {imagegen.OUT_DIR.resolve(), imagegen.out_dir().resolve()}
    with db.session() as s:
        msgs = s.query(db.Message).filter(db.Message.conversation_id == conv_id, db.Message.role == "assistant").all()
        caminhos = [i["path"] for m in msgs for i in (m.meta or {}).get("images") or [] if i.get("path")]
    achados: list[Path] = []
    for c in caminhos:
        for f in (Path(c), descartadas_dir() / Path(c).name):  # o caminho do meta, ou já no descarte
            f = f.resolve()
            if f.is_file() and pastas & set(f.parents) and f not in achados:
                achados.append(f)
                break
    return achados


def apagar_imagens(conv_id: int) -> int:
    """Apagar a conversa leva as imagens dela junto (a tela avisa antes, com a contagem)."""
    n = 0
    for f in imagens_da_conversa(conv_id):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass  # aberta em outro programa: fica, e a conversa sai assim mesmo
    return n


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
    for f in [*pasta.glob("*.png"), *pasta.glob("*.webm")]:
        try:
            if f.stat().st_mtime < limite:
                f.unlink()
                apagados += 1
        except OSError:
            pass
    return apagados

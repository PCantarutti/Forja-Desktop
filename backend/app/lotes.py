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
from . import slots as projeto
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
MAX_SLOTS_LIBERADOS = 2000  # caminhos de slot que a rota de arquivo serve (lista própria, não a das referências)


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

class GpuOcupada(imagegen.ModeloCarregado):
    """Outro programa (o llama-server de outra janela ou instância do Forja) segura a VRAM: o sd.cpp
    falharia imagem por imagem, por falta de memória. A tela avisa antes e a pessoa decide."""


def _liberar_vram(confirm: bool) -> None:
    """LLM na VRAM: sem confirmação a tela pergunta (409); com ela, descarrega."""
    st = localai.status()
    if not confirm and (outros := projeto.gpu_alheia(st.get("pid"))):
        raise GpuOcupada(f"Outro programa está usando a GPU: {', '.join(outros)}, provavelmente outra janela "
                         "ou instância do Forja. O sd.cpp pode ficar sem memória e falhar em cada imagem."
                         + (f" O modelo {st.get('alias')} desta janela também será descarregado." if st["running"] else ""))
    if st["running"]:
        if not confirm:
            raise imagegen.ModeloCarregado(st.get("alias") or "um modelo")
        localai.unload()


def _sem_estilo(prompt: str, estilo: str) -> str:
    return prompt[: -len(estilo) - 2] if estilo and prompt.endswith(f", {estilo}") else prompt


def _slots(message_id: int) -> list[dict]:
    """Slots que a ferramenta imagens_pendentes registrou (caminhos já confinados na pasta da conversa).
    Cada um sai com o prompt sem o estilo (prompt_base): "regerar todas com outro estilo" troca só o fim."""
    with db.session() as s:
        m = s.get(db.Message, message_id)
        pedido = ((m.meta or {}).get("imagens_pendentes") or {}) if m else {}
    if not pedido.get("slots"):
        raise ToolError("Slots de imagem não encontrados.")
    out = []
    for sl in pedido["slots"]:
        estilo = sl.get("estilo", pedido.get("estilo") or "")
        out.append({**sl, "estilo": estilo, "prompt_base": sl.get("prompt_base") or _sem_estilo(sl["prompt"], estilo)})
    return out


def _ids(origem_: dict) -> list[int]:
    """Os pedidos da IA (chamadas imagens_pendentes) que esta conversa reúne, do mais velho ao mais novo."""
    return list(origem_.get("message_ids") or [origem_["message_id"]])


def _todos_slots(origem_: dict) -> list[dict]:
    """Os slots de todos os pedidos; o mesmo caminho pedido de novo fica com o pedido mais novo."""
    por: dict[str, dict] = {}
    for mid in _ids(origem_):
        try:
            for sl in _slots(mid):
                por[sl["caminho"]] = {**sl, "message_id": mid}
        except ToolError:
            continue  # o chat que pediu foi apagado: os slots dele saem junto
    return list(por.values())


def _lotes_da_conversa(conv_id: int) -> list[tuple[int, str | None, list[dict]]]:
    """(id, status, imagens) de cada lote da conversa, com as imagens copiadas (pode editar)."""
    # ponytail: lê todos os lotes da conversa (dezenas); índice por slot se virarem milhares
    with db.session() as s:
        msgs = (s.query(db.Message)
                .filter(db.Message.conversation_id == conv_id, db.Message.role == "assistant").all())
        return [(m.id, m.status, [dict(i) for i in (m.meta or {}).get("images") or []]) for m in msgs]


FALHOU = ("cancelada", "erro", "interrompida")  # "pendente" fica de fora: é a fila de um lote rodando


def _gerados(conv_id: int) -> set[str]:
    # Cancelado, com erro ou interrompido não conta: o slot volta a ser pendente e sai de novo com o modelo
    # escolhido agora (antes ficava preso ao "Continuar", que repete o modelo do lote que falhou).
    return {i.get("destino") or i.get("slot") for _, _, imgs in _lotes_da_conversa(conv_id) for i in imgs
            if (i.get("destino") or i.get("slot")) and i.get("status") not in FALHOU}


def conversa_dos_slots(tool_message_id: int) -> dict:
    """A conversa de Imagens do projeto daquele pedido: uma por pasta, e cada pedido novo da IA (outra
    seção do site, outro chat) entra nela. A mesma a cada clique no botão; criada no primeiro."""
    _slots(tool_message_id)  # confere que é mesmo uma chamada imagens_pendentes
    with db.session() as s:
        tool = s.get(db.Message, tool_message_id)
        chat = s.get(db.Conversation, tool.conversation_id)
        # ponytail: varre as conversas de imagem com origem; coluna própria se virarem milhares
        candidatas = [c for c in s.query(db.Conversation).filter(db.Conversation.kind == "imagem",
                                                                  db.Conversation.origem.isnot(None))]
        achada = (next((c for c in candidatas if tool_message_id in _ids(c.origem)), None)
                  or next((c for c in candidatas if c.workspace == chat.workspace and not c.archived), None))
        if not achada:
            projeto_ = Path(chat.workspace).name if chat.workspace else "pasta padrão"
            achada = db.Conversation(kind="imagem", title=f"Imagens · {projeto_}"[:200], workspace=chat.workspace,
                                     origem={"conv_id": chat.id, "message_id": tool_message_id,
                                             "message_ids": [tool_message_id]})
            s.add(achada)
        elif tool_message_id not in _ids(achada.origem):
            # o "Ir para o chat" leva ao chat do pedido mais novo
            achada.origem = {**achada.origem, "conv_id": chat.id,
                             "message_ids": [*_ids(achada.origem), tool_message_id]}
        s.commit()
        return {"id": achada.id, "kind": "imagem"}


def _raiz(conv_id: int) -> Path | None:
    from . import workspace

    with db.session() as s:
        c = s.get(db.Conversation, conv_id)
        pasta = c.workspace if c else None
    try:
        return workspace.resolve(pasta)
    except Exception:  # pasta do projeto sumiu: a tela segue, só sem conferir o código
        return None


def origem(conv_id: int) -> dict | None:
    """Para a tela: de qual chat e projeto vieram os slots, quais faltam gerar e quais o código não usa
    mais (None = conversa comum)."""
    from . import workspace

    with db.session() as s:
        c = s.get(db.Conversation, conv_id)
        o = c.origem if c else None
        if not o:
            return None
        chat = s.get(db.Conversation, o.get("conv_id") or 0)
        info = {"message_id": _ids(o)[-1], "workspace": workspace.label(c.workspace), "web": bool(o.get("web")),
                "projeto": Path(c.workspace).name if c.workspace else "pasta padrão",
                "chat": {"id": chat.id, "title": chat.title, "kind": chat.kind} if chat else None}
    todos = _todos_slots(o)
    feitos = _gerados(conv_id)
    raiz = _raiz(conv_id)
    usados = projeto.referenciados(raiz, [sl["caminho"] for sl in todos]) if raiz else {sl["caminho"] for sl in todos}
    info.update(slots=todos, pendentes=[sl for sl in todos if sl["caminho"] not in feitos],
                fora_do_codigo=[sl["caminho"] for sl in todos if sl["caminho"] not in usados],
                estilo=next((sl["estilo"] for sl in reversed(todos) if sl.get("estilo")), ""))
    return info


def _avisar(conv_id: int, texto: str) -> None:
    """Nota para o chat que pediu as imagens: a IA lê no próximo turno (to_model) e pode conferir o site."""
    with db.session() as s:
        c = s.get(db.Conversation, conv_id)
        chat_id = (c.origem or {}).get("conv_id") if c else None
        if not chat_id or not s.get(db.Conversation, chat_id):
            return
    _save(chat_id, role="event", content=texto, meta={"kind": "imagens", "to_model": True})


def _web(conv_id: int) -> bool:
    with db.session() as s:
        c = s.get(db.Conversation, conv_id)
        return bool(c and (c.origem or {}).get("web"))


def _liberar(paths: list[str]) -> None:
    """A rota de arquivo só serve a pasta de imagens e o que foi registrado: o slot mora na pasta do
    projeto, então entra numa lista própria (as referências anexadas têm a delas)."""
    data = localai.read_config()
    chaves = [localai._chave(p) for p in paths]
    lista = [p for p in data.get("slots_liberados") or [] if p not in chaves] + chaves
    data["slots_liberados"] = lista[-MAX_SLOTS_LIBERADOS:]
    localai.write_config(data)


def eh_slot(path: str) -> bool:
    return localai._chave(path) in (localai.read_config().get("slots_liberados") or [])


def _base_variacao(conv_id: int, variar: dict) -> dict:
    """O slot de onde saem as variações: prompt e tamanho da imagem que a pessoa quer refazer."""
    lote = _mensagem(int(variar.get("message_id") or 0))
    if lote["conversation_id"] != conv_id:
        raise ToolError("Essa imagem é de outra conversa.")
    item = next((i for i in lote["meta"]["images"] if i["path"] == variar.get("path")), None)
    slot = item and (item.get("destino") or item.get("slot"))
    if not slot:
        raise ToolError("Só imagem de slot (skill gerar-imagens) tem variações para escolher.")
    editado = str(variar.get("prompt") or "").strip()  # a pessoa editou no modal antes de regerar
    prompt = editado or item.get("prompt") or ""
    return {"slot": slot, "nome": item.get("nome") or Path(slot).stem, "prompt": prompt,
            "prompt_base": editado or item.get("prompt_base") or prompt, "estilo": "" if editado else item.get("estilo", ""),
            "width": item.get("width"), "height": item.get("height")}


def _bases_estilo(conv_id: int, estilo: str) -> list[dict]:
    """Uma base por slot, da versão que o site mostra, com o estilo novo no lugar do antigo."""
    estilo = estilo.strip()
    bases = []
    for _, _, imgs in _lotes_da_conversa(conv_id):
        for i in imgs:
            if i.get("destino"):
                base = i.get("prompt_base") or _sem_estilo(i.get("prompt") or "", i.get("estilo", ""))
                bases.append({"slot": i["destino"], "nome": i.get("nome") or Path(i["destino"]).stem,
                              "prompt": f"{base}, {estilo}" if estilo else base, "prompt_base": base,
                              "estilo": estilo, "width": i.get("width"), "height": i.get("height")})
    if not bases:
        raise ToolError("Nenhuma imagem do site gerada ainda para regerar.")
    return bases


def start(conv_id: int, prompt: str, opts: dict | None = None, models: list[str] | None = None,
          count: int = 1, seed: int = 0, seed_mode: str = "incremental", confirm: bool = False,
          refs: list[str] | None = None, slots_de: int = 0, variar: dict | None = None,
          estilo: str | None = None) -> dict:
    """Enfileira o lote e devolve a mensagem do assistente já criada (a thread preenche o resto).

    `slots_de`: um pedido da IA (chamada imagens_pendentes) desta conversa. Gera os slots do projeto que
    ainda não saíram, cada um com prompt, tamanho e arquivo próprios (o caminho que o código aponta).
    `variar`: {message_id, path, prompt?} de uma imagem de slot: `count` variações dela, na pasta de
    saída; o site só muda quando a pessoa escolhe uma (escolher).
    `estilo`: variações de TODOS os slots com este estilo no lugar do antigo (`count` por slot)."""
    with db.session() as s:
        c = s.get(db.Conversation, conv_id)
        o = c.origem if c else None
    ids = _ids(o) if o else []
    # A conversa aberta pela IA só gera os slots dela (e variações); os slots só saem numa conversa assim.
    if o and not variar and estilo is None and slots_de not in ids:
        raise ToolError("Esta conversa é só para as imagens que o chat pediu para o site: use Regerar num "
                        "card. Para uma imagem avulsa, abra uma conversa nova em Imagens.")
    if slots_de and slots_de not in ids:
        raise ToolError("Abra estas imagens pelo botão \"Gerar imagens\" do chat que as pediu.")
    if estilo is not None and not o:
        raise ToolError("Regerar com outro estilo é só para as imagens do site.")
    slots: list[dict] = []
    if slots_de:
        feitos = _gerados(conv_id)
        slots = [sl for sl in _todos_slots(o) if sl["caminho"] not in feitos]
        if not slots:
            raise ToolError("Todos os slots já foram gerados: use Regerar nos cards.")
    bases = ([_base_variacao(conv_id, variar)] if variar
             else _bases_estilo(conv_id, estilo) if estilo is not None else [])
    prompt = (prompt or "").strip() or (f"{len(slots)} imagens para o projeto" if slots else "")
    if slots:
        count, refs = len(slots), []
    por_base = 1
    if bases:
        refs = []
        prompt = prompt or (f"Variações de {bases[0]['nome']}" if len(bases) == 1
                            else f"Variações das {len(bases)} imagens do site")
        por_base = max(1, min(int(count or 1), MAX_VARIACOES // len(bases)))
        count = por_base * len(bases)
        # incremental com a mesma base repetiria as imagens de antes: regerar é sempre semente nova
        seed_mode = "aleatoria"
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
    for item, slot in zip(imagens, slots):
        item.update(path=slot["caminho"], destino=slot["caminho"], nome=slot["nome"], prompt=slot["prompt"],
                    prompt_base=slot["prompt_base"], estilo=slot["estilo"],
                    width=slot.get("largura"), height=slot.get("altura"))
    if slots:
        _liberar([s["caminho"] for s in slots])
    for i, item in enumerate(imagens if bases else []):
        b = bases[i // por_base]
        item.update(path=str(pasta / f"{b['nome']}-{marca}-{i:02d}-s{item['seed']}.png"), slot=b["slot"],
                    nome=b["nome"], prompt=b["prompt"], prompt_base=b["prompt_base"], estilo=b["estilo"],
                    width=b["width"], height=b["height"])

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
                      "opts": opts, "images": imagens,
                      # variações de um slot moram no modal do slot, não viram um lote novo na tela
                      **({"variacao_de": bases[0]["slot"] if len(bases) == 1 else "*"} if bases else {})})
    if slots:  # o botão do chat passa de "Gerar N imagens" para "Ver as N imagens"
        with db.session() as s:
            for mid in ids:
                pedido_ia = s.get(db.Message, mid)
                if pedido_ia and pedido_ia.meta and pedido_ia.meta.get("imagens_pendentes"):
                    pedido_ia.meta = {**pedido_ia.meta,
                                      "imagens_pendentes": {**pedido_ia.meta["imagens_pendentes"], "geradas": True}}
            s.commit()

    threading.Thread(target=_trabalhar, args=(conv_id, msg.id, prompt, opts, job["id"], refs), daemon=True).start()
    return msg.to_dict()


def _temporario(arquivo: Path) -> Path:
    return arquivo.with_name(f".{arquivo.stem}.gerando.png")


def _trabalhar(conv_id: int, message_id: int, prompt: str, opts: dict, job_id: str,
               refs: list[str] | None = None) -> None:
    imagens = list(_mensagem(message_id)["meta"]["images"])
    localai.set_image_busy(True)
    erro = ""
    web = _web(conv_id)
    feitas, total = 0, sum(i["status"] == "pendente" for i in imagens)
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

            def progresso(passo: int, total_passos: int, s_passo: float = 0.0, item=item, previa=previa) -> None:
                # Vai no meta porque a tela já consulta a conversa enquanto o lote roda: nada de rota nova.
                # A prévia só entra quando o sd-cli já gravou a primeira: até lá o card mostra o que tinha
                # (na edição, a imagem original).
                if previa.is_file():
                    item["preview"] = str(previa)
                item["progress"] = round(passo / total_passos, 3) if total_passos else 0.0
                item["s_passo"] = round(s_passo, 2)
                item["restante"] = round(max(0, total_passos - passo) * s_passo)  # só a amostragem; o VAE vem depois
                _patch(message_id, meta={"images": imagens})

            arquivo = Path(item["path"])
            # Slot: gera ao lado e só troca o arquivo do site se a imagem nova sair — gerar direto no
            # caminho (ou tirar a antiga antes) deixava o site sem imagem quando o sd falhava.
            saida = _temporario(arquivo) if item.get("destino") else arquivo
            try:
                tamanho = {k: item[k] for k in ("width", "height") if item.get(k)}  # slot com tamanho próprio
                if item.get("destino") or item.get("slot"):
                    # a proporção é do slot; a área, a que o modelo sabe gerar (fora dela ele repete objetos)
                    area = projeto.area_nativa(item["model"], (localai.requisitos(item["model"]) or {}).get("sugere"))
                    w, h = projeto.ajustar(item.get("width"), item.get("height"), area)
                    tamanho = {k: v for k, v in (("width", w), ("height", h)) if v}
                medido: dict = {}
                imagegen.generate(item.get("prompt") or prompt, saida,
                                  {**opts, **tamanho, "model": item["model"], "seed": item["seed"]},
                                  job_id, refs or [], progresso, previa, medido)
                if saida != arquivo:
                    # a versão anterior do site vai para descartadas/; o PNG provisório só some
                    if arquivo.is_file() and not projeto.eh_placeholder(arquivo):
                        descartadas_dir().mkdir(parents=True, exist_ok=True)
                        shutil.move(str(arquivo), str(descartadas_dir() / f"{arquivo.stem}-{time.strftime('%Y%m%d-%H%M%S')}.png"))
                    shutil.move(str(saida), str(arquivo))
                    if web:
                        projeto.webp(arquivo)
                item["status"] = "pronta"
                if item.get("s_passo") and medido.get("segundos"):  # base do "≈ N min", em qualquer conversa
                    localai.anotar_tempo(item["model"], imagegen._opts({**opts, "model": item["model"]}),
                                         float(item["s_passo"]), float(medido["segundos"]))
            except Exception as e:
                if saida != arquivo:
                    saida.unlink(missing_ok=True)
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
            downloads.update(job_id, done=feitas, total=total)
            _patch(message_id, meta={"images": imagens})
    finally:
        localai.set_image_busy(False)

    pronta = any(i["status"] in ("pronta", "mantida", "descartada") for i in imagens)
    cancelado = any(i["status"] == "cancelada" for i in imagens)
    status = "pronto" if pronta else ("cancelado" if cancelado else "erro")
    downloads.finish(job_id, error="" if pronta else erro)
    mirror.write(conv_id)
    limpar_descartadas()
    do_site = [i for i in imagens if i.get("destino")]
    if do_site:  # só o lote que mexe no site avisa; variações ficam no modal até alguém escolher
        linhas = "\n".join(f"- {i['nome']} → {Path(i['destino']).name}: "
                           + ("pronta" if i["status"] == "pronta" else f"{i['status']} ({i['error'].splitlines()[0][:120]})"
                              if i.get("error") else i["status"]) for i in do_site)
        _avisar(conv_id, "Imagens do site geradas pela tela Imagens (os arquivos já estão nos caminhos dos slots):\n"
                         f"{linhas}\n\nSe puder, abra a página no navegador e confira se as imagens encaixam no "
                         "layout: proporção, recorte e contraste com o texto por cima.")
    # o status sai por último de propósito: é o sinal de "acabou" para quem espera o lote, e nada
    # pode acontecer depois dele (nos testes, o monkeypatch das pastas já teria sido desfeito).
    _patch(message_id, status=status, meta={"images": imagens})


# ------------------------------------------------------------------ ampliação

def _validar_ampliacao(fator: int, modelo: str) -> None:
    from . import ampliar as amp
    if int(fator) not in (2, 4):
        raise ToolError("Amplie em 2× ou 4×.")
    if modelo and not amp.eh_ampliador(modelo):
        raise ToolError("Esse arquivo não é um modelo de ampliação (ESRGAN).")
    amp._ffmpeg()  # sem ffmpeg, avisa antes de criar a tomada
    if localai.image_busy():
        raise ToolError("Já tem uma geração em andamento (imagem ou vídeo): espere terminar ou cancele.")


def _nova_ampliacao(conv_id: int, origem: str, saida: Path, prompt: str, opts: dict, seed: int,
                    fator: int, modelo: str, suavizar: bool) -> dict:
    """A tomada nova (pedido + resposta) e a thread que amplia. `opts`: largura, altura, fps e quadros da origem."""
    nome = Path(modelo).stem if modelo else "Lanczos"
    amp_meta = {"origem": origem, "fator": int(fator), "modelo": modelo, "suavizar": bool(suavizar)}
    opts = {**opts, "width": int(opts.get("width") or 0) * int(fator), "height": int(opts.get("height") or 0) * int(fator),
            "ampliacao": amp_meta}
    if suavizar and opts.get("fps"):
        opts.update(fps=int(opts["fps"]) * 2, frames=int(opts.get("frames") or 0) * 2 - 1)
    imagens = [{"path": str(saida), "seed": seed, "model": modelo, "model_name": f"{nome} · {fator}×",
                "status": "pendente", "error": "", "unidade": "quadro"}]
    _save(conv_id, role="user", content=prompt, meta={"refs": [], "models": [modelo], "ampliacao": amp_meta})
    job = downloads.create("lote", f"ampliar {Path(origem).name}")
    nova = _save(conv_id, role="assistant", content="", status="running",
                 meta={"job": job["id"], "count": 1, "seed_mode": "fixa", "opts": opts, "images": imagens})
    threading.Thread(target=_ampliar_trabalho, args=(conv_id, nova.id, job["id"]), daemon=True).start()
    return nova.to_dict()


def ampliar(message_id: int, path: str, fator: int, modelo: str = "", suavizar: bool = False) -> dict:
    """Amplia uma tomada pronta num vídeo novo, que entra na mesma conversa como uma tomada à parte (com
    progresso por quadro, prévia, cancelar e manter/descartar como qualquer outra)."""
    msg = _mensagem(message_id)
    item = next((i for i in msg["meta"]["images"] if i["path"] == path), None)
    if not item or not Path(path).is_file():
        raise ToolError("Essa tomada não está pronta (ou o arquivo sumiu).")
    _validar_ampliacao(fator, modelo)
    saida = Path(path).with_name(f"{Path(path).stem}-{fator}x{'-suave' if suavizar else ''}.webm")
    with db.session() as s:
        pedido = (s.query(db.Message).filter(db.Message.conversation_id == msg["conversation_id"], db.Message.role == "user",
                                             db.Message.id < message_id).order_by(db.Message.id.desc()).first())
        prompt = pedido.content if pedido else ""
    return _nova_ampliacao(msg["conversation_id"], path, saida, prompt, dict(msg["meta"].get("opts") or {}), item["seed"],
                           fator, modelo, suavizar)


def ampliar_arquivo(conv_id: int, path: str, fator: int, modelo: str = "", suavizar: bool = False) -> dict:
    """Amplia um vídeo qualquer do disco (mp4, mov, mkv, webm…): vira uma tomada na conversa, e o resultado vai
    para a pasta de imagens; o original não é tocado."""
    from . import ampliar as amp
    if not Path(path).is_file():
        raise ToolError("Esse arquivo não existe (ou não está acessível).")
    _validar_ampliacao(fator, modelo)
    info = amp.sondar(path)
    pasta = imagegen.out_dir()
    pasta.mkdir(parents=True, exist_ok=True)
    saida = pasta / f"{time.strftime('%Y%m%d-%H%M%S')}-{Path(path).stem}-{fator}x{'-suave' if suavizar else ''}.webm"
    opts = {"width": info["w"], "height": info["h"], "fps": round(info["fps"]), "frames": info["quadros"]}
    return _nova_ampliacao(conv_id, path, saida, Path(path).name, opts, 0, fator, modelo, suavizar)


def _ampliar_trabalho(conv_id: int, message_id: int, job_id: str) -> None:
    from . import ampliar as amp
    meta = _mensagem(message_id)["meta"]
    imagens = list(meta["images"])
    a = meta["opts"]["ampliacao"]
    item = imagens[0]
    localai.set_image_busy(True)
    previa = previas_dir() / (Path(item["path"]).stem + ".png")
    previa.parent.mkdir(parents=True, exist_ok=True)
    try:
        item.update(status="gerando", progress=0.0, com_previa=bool(a["modelo"]))
        _patch(message_id, meta={"images": imagens})

        def progresso(feitos: int, total: int, s_quadro: float) -> None:
            if previa.is_file():
                item["preview"] = str(previa)
            item.update(progress=round(feitos / total, 3) if total else 0.0, s_passo=round(s_quadro, 2),
                        restante=round(max(0, total - feitos) * s_quadro))
            _patch(message_id, meta={"images": imagens})

        r = amp.ampliar(a["origem"], Path(item["path"]), a["fator"], a["modelo"], a["suavizar"], job_id, progresso, previa)
        # o que saiu de fato (o minterpolate não inventa quadro depois do último): o player conta com isso
        if r:
            _patch(message_id, meta={"opts": {**meta["opts"], "width": r["w"], "height": r["h"], "fps": round(r["fps"]),
                                              "frames": r["quadros"] or meta["opts"].get("frames")}})
        item["status"] = "pronta"
    except Exception as e:
        cancelada = downloads.cancelled(job_id)
        item["status"] = "cancelada" if cancelada else "erro"
        item["error"] = "" if cancelada else str(e)
    finally:
        localai.set_image_busy(False)
        for k in ("preview", "com_previa"):
            item.pop(k, None)
        previa.unlink(missing_ok=True)
    pronta = item["status"] == "pronta"
    downloads.finish(job_id, error="" if pronta else item["error"])
    mirror.write(conv_id)
    _patch(message_id, status="pronto" if pronta else ("cancelado" if item["status"] == "cancelada" else "erro"),
           meta={"images": imagens})


# O que o lote ainda não entregou e "Continuar" gera de novo.
A_REFAZER = ("interrompida", "pendente", "cancelada", "erro")


def reap() -> int:
    """Na subida do backend nenhum lote está rodando: os que ficaram "running" são de uma queda (o app
    fechou no meio). Viram "interrompido", para a tela parar de esperar e oferecer "Continuar". A
    imagem que estava no meio perde os passos (o sd-cli não salva estado parcial); se o PNG chegou a
    ser gravado antes da queda, ela conta como pronta."""
    shutil.rmtree(previas_dir(), ignore_errors=True)  # prévias de imagens que não terminaram
    shutil.rmtree(imagegen.out_dir() / ".ampliando", ignore_errors=True)  # quadros de ampliações que caíram
    with db.session() as s:
        presos = s.query(db.Message).filter(db.Message.role == "assistant", db.Message.status == "running").all()
        n = 0
        for m in presos:
            imagens = [dict(i) for i in (m.meta or {}).get("images") or []]
            if not imagens:
                continue  # não é lote de imagem
            for i in imagens:
                if i.get("destino"):
                    _temporario(Path(i["destino"])).unlink(missing_ok=True)
                if i["status"] in ("gerando", "pendente"):
                    f = Path(i["path"])
                    # a ampliação grava o webm aos poucos (ffmpeg): arquivo lá não quer dizer que terminou
                    if i.get("unidade") == "quadro":
                        f.unlink(missing_ok=True)
                    # o PNG provisório de um slot também não é imagem pronta
                    feita = f.is_file() and f.stat().st_size > 0 and not projeto.eh_placeholder(f)
                    i["status"] = "pronta" if feita else "interrompida"
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
    if (msg["meta"].get("opts") or {}).get("ampliacao"):  # é uma ampliação: refaz a ampliação
        _liberar_vram(confirm)
        for i in imagens:
            i.update(status="pendente", error="")
        job = downloads.create("lote", f"ampliar {Path(imagens[0]['path']).name}")
        _patch(message_id, status="running", meta={"job": job["id"], "images": imagens})
        threading.Thread(target=_ampliar_trabalho, args=(msg["conversation_id"], message_id, job["id"]), daemon=True).start()
        return {"ok": True}
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
                # slot volta para o caminho que o código aponta; variação, para a pasta de saída
                alvo = Path(item["destino"]) if item.get("destino") else imagegen.out_dir() / Path(item["path"]).name
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


def escolher(conv_id: int, slot: str, path: str) -> dict:
    """Põe a variação `path` no site: ela vai para o caminho do slot e a que estava lá vai para a pasta de
    saída, virando mais uma variação. Troca e não cópia: a imagem com `destino` é sempre a que o site mostra."""
    lotes_ = _lotes_da_conversa(conv_id)
    atual = nova = None
    for mid, status, imgs in lotes_:
        for i in imgs:
            if i.get("destino") == slot:
                atual = (mid, status, imgs, i)
            if i["path"] == path and slot in (i.get("slot"), i.get("destino")):
                nova = (mid, status, imgs, i)
    if not atual or not nova:
        raise ToolError("Variação não encontrada nesta conversa.")
    if nova[3] is atual[3]:
        return {"ok": True}  # já é a do site
    if "running" in (atual[1], nova[1]):  # a thread do lote regrava o meta inteiro: a troca se perderia
        raise ToolError("Espere o lote terminar para escolher.")
    v, o = nova[3], atual[3]
    if v["status"] not in ("pronta", "mantida") or not Path(v["path"]).is_file():
        raise ToolError("Essa variação não está pronta.")
    no_site = Path(slot)
    if o["path"] == slot and no_site.is_file():  # a anterior sai do site, mas continua escolhível
        guardada = imagegen.out_dir() / f"{no_site.stem}-s{o['seed']}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        guardada.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(no_site), str(guardada))
        o["path"] = str(guardada)
    o.pop("destino", None)
    o["slot"] = slot
    no_site.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(v["path"], str(no_site))
    v.pop("slot", None)
    v.update(path=slot, destino=slot, status="mantida")
    for mid, _st, imgs, _i in {atual[0]: atual, nova[0]: nova}.values():
        _patch(mid, meta={"images": imgs})
    if _web(conv_id):
        projeto.webp(no_site)
    mirror.write(conv_id)
    _avisar(conv_id, f"A imagem do slot {v.get('nome') or no_site.stem} ({no_site.name}) foi trocada no site por "
                     f"outra versão, feita com o prompt: {v.get('prompt') or '(o mesmo)'}. O caminho é o mesmo; "
                     "se puder, confira no navegador.")
    return {"ok": True}


def otimizar(conv_id: int) -> dict:
    """Versão leve para a web: um .webp ao lado de cada imagem do site e o código apontando para ele. O PNG
    fica como matriz (é ele que Regerar e Usar no site trocam) e o .webp é regravado a cada troca."""
    raiz = _raiz(conv_id)
    with db.session() as s:
        c = s.get(db.Conversation, conv_id)
        if not c or not c.origem:
            raise ToolError("Otimizar é só para as imagens do site.")
    if not raiz:
        raise ToolError("A pasta do projeto não foi encontrada.")
    atuais = [i for _, _, imgs in _lotes_da_conversa(conv_id) for i in imgs
              if i.get("destino") and Path(i["destino"]).is_file() and not projeto.eh_placeholder(i["destino"])]
    if not atuais:
        raise ToolError("Nenhuma imagem do site gerada ainda.")
    feitas = []
    for i in atuais:
        png, leve = projeto.webp(i["destino"])
        feitas.append({"nome": i.get("nome") or Path(i["destino"]).stem, "png": png, "webp": leve})
    alterados = projeto.trocar_referencias(raiz, [Path(i["destino"]).stem for i in atuais])
    with db.session() as s:
        c = s.get(db.Conversation, conv_id)
        c.origem = {**c.origem, "web": True}
        s.commit()
    antes, depois = sum(f["png"] for f in feitas), sum(f["webp"] for f in feitas)
    _avisar(conv_id, f"Otimizei as {len(feitas)} imagens do site para a web ({antes // 1024} KB → {depois // 1024} KB): "
                     f"cada uma ganhou um .webp ao lado do PNG e troquei .png por .webp em "
                     f"{', '.join(alterados) or 'nenhum arquivo (o código já apontava para .webp)'}. "
                     "Daqui em diante, aponte para o .webp; o .png continua como matriz.")
    return {"imagens": feitas, "arquivos": alterados}


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

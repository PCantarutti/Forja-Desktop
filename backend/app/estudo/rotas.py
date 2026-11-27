"""/api/estudo/... — a tela Estudos (versão 2)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from .. import config
from ..tools import ToolError
from . import base, corpus, extras, fontes, material, prova

router = APIRouter(prefix="/api/estudo")


def _ok(f, *a, **k):
    try:
        return f(*a, **k)
    except ToolError as e:
        raise HTTPException(400, str(e))


class Motor(BaseModel):
    provider: str = ""
    model: str = ""


class ProgramaBody(Motor):
    pedido: str = ""
    nivel: str = ""
    edital_texto: str = ""
    edital_link: str = ""
    cargo: str = ""


class AlvoBody(Motor):
    topicos: list[str] = []
    profundidade: str = "normal"


class ProvaBody(Motor):
    n: int = 10
    tipo: str = "multipla"
    dificuldade: str = "media"
    topicos: list[str] = []


class EntregaBody(Motor):
    respostas: dict = {}
    segundos: int = 0


class EstudoPatch(BaseModel):
    pedido: str | None = None
    nivel: str | None = None
    programa: list[dict] | None = None


class FontePatch(BaseModel):
    status: str | None = None        # util | descartada
    topicos: list[str] | None = None


class TextoBody(BaseModel):
    nome: str = ""
    texto: str = ""


class DuvidaBody(Motor):
    pergunta: str = ""
    topico: str = ""


class CartaoBody(BaseModel):
    acertou: bool = False


def _tela(conv_id: int) -> dict:
    titulo = base.conferir_conv(conv_id)
    est = base.ler(conv_id)
    return {"id": conv_id, "titulo": est.get("titulo") or titulo, "pedido": est["pedido"], "nivel": est["nivel"],
            "edital": est.get("edital"), "programa": est["programa"], "fontes": est["fontes"],
            "material": est["material"], "provas": [prova.para_tela(p) for p in est["provas"]],
            "flashcards": est["flashcards"], "duvidas": est["duvidas"][-50:],
            "desempenho": extras.desempenho(est), "caderno": extras.caderno(est), "run": base.estado_run(conv_id)}


def _disparar(conv_id: int, tipo: str, body: Motor, alvo: dict, coro_de) -> dict:
    base.conferir_conv(conv_id)
    run = base.novo_run(conv_id, tipo, base.spec_de(body.provider, body.model))
    run["alvo"] = alvo
    base.disparar(run, coro_de(run))
    return base.publico(run)


@router.get("/{conv_id}")
def tela(conv_id: int):
    try:
        return _tela(conv_id)
    except ToolError as e:
        raise HTTPException(404, str(e))


@router.patch("/{conv_id}")
def alterar(conv_id: int, body: EstudoPatch):
    base.conferir_conv(conv_id)
    with base.mudar(conv_id) as est:
        if body.pedido is not None:
            est["pedido"] = body.pedido.strip()[:4000]
        if body.nivel in material.NIVEIS:
            est["nivel"] = body.nivel
        if body.programa is not None:   # a tela manda a lista inteira editada; tópico sem id ganha um
            novos = []
            for x in body.programa[:fontes.MAX_TOPICOS]:
                for t in fontes._topicos_de({"topicos": [x]}):
                    novos.append({**t, "id": str(x.get("id") or f"t{base.proximo(est, 'topico')}")})
            est["programa"] = novos
    return _tela(conv_id)


@router.get("/{conv_id}/stream")
async def stream(conv_id: int):
    """Retrato da execução a cada tick, até ela acabar (o último retrato já é o final)."""
    async def gerar():
        while True:
            r = base.estado_run(conv_id)
            yield f"data: {json.dumps(r, ensure_ascii=False, default=str)}\n\n"
            if not r or r["status"] != "rodando":
                return
            await asyncio.sleep(base.TICK)
    return StreamingResponse(gerar(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{conv_id}/cancelar")
def cancelar(conv_id: int):
    base.cancelar(conv_id)
    return {"ok": True}


# ------------------------------------------------------------------ programa e fontes

@router.post("/{conv_id}/programa")
async def programa(conv_id: int, body: ProgramaBody):
    if not (body.pedido.strip() or body.edital_texto.strip() or body.edital_link.strip()):
        raise HTTPException(400, "Escreva o que quer estudar ou mande o edital.")
    base.conferir_conv(conv_id)
    with base.mudar(conv_id) as est:
        est["pedido"] = body.pedido.strip()[:4000]
        if body.nivel in material.NIVEIS:
            est["nivel"] = body.nivel

    async def coro(run):
        if body.edital_link.strip():
            from . import edital as ED
            run["fase"] = "baixando o edital"
            r = await asyncio.to_thread(ED.texto_de_link, body.edital_link.strip(), lambda: run["cancelar"])
            run["alvo"]["edital_texto"] = r["texto"]
        if run["alvo"].get("edital_texto"):
            with base.mudar(conv_id) as est:
                est["edital"] = {"chars": len(run["alvo"]["edital_texto"]), "cargo": body.cargo.strip()[:120],
                                 "link": body.edital_link.strip()[:2000]}
        await fontes.rodar_programa(run)
    return _ok(_disparar, conv_id, "programa", body,
               {"pedido": body.pedido.strip(), "edital_texto": body.edital_texto, "cargo": body.cargo.strip()}, coro)


@router.post("/{conv_id}/edital/arquivo")
async def edital_arquivo(conv_id: int, file: UploadFile = File(...)):
    """Só devolve o texto (edital não é fonte de estudo); a tela manda de volta no /programa."""
    from . import edital as ED
    base.conferir_conv(conv_id)
    dados = await file.read()
    if len(dados) > config.MAX_DOC_BYTES:
        raise HTTPException(413, f"Arquivo maior que {config.MAX_DOC_BYTES // 1_000_000} MB.")
    return _ok(lambda: ED.texto_de_arquivo(file.filename or "edital.pdf", dados))


@router.post("/{conv_id}/pesquisar")
async def pesquisar(conv_id: int, body: AlvoBody):
    return _ok(_disparar, conv_id, "pesquisa", body, {"topicos": body.topicos, "profundidade": body.profundidade},
               fontes.rodar_pesquisa)


@router.post("/{conv_id}/arquivo")
async def arquivo(conv_id: int, file: UploadFile = File(...)):
    dados = await file.read()
    try:
        return await asyncio.to_thread(corpus.adicionar_arquivo, conv_id, file.filename or "arquivo", dados)
    except ToolError as e:
        raise HTTPException(400, str(e))


@router.post("/{conv_id}/texto")
def texto(conv_id: int, body: TextoBody):
    return _ok(corpus.adicionar_arquivo, conv_id, body.nome or "texto colado.txt", texto=body.texto, tipo="texto")


@router.patch("/{conv_id}/fontes/{fid}")
def fonte_alterar(conv_id: int, fid: str, body: FontePatch):
    base.conferir_conv(conv_id)
    with base.mudar(conv_id) as est:
        f = next((x for x in est["fontes"] if x["id"] == fid), None)
        if not f:
            raise HTTPException(404, "Fonte não encontrada.")
        if body.status in ("util", "descartada"):
            f["status"] = body.status
        if body.topicos is not None:
            f["topicos"] = [t for t in body.topicos if any(p["id"] == t for p in est["programa"])]
    return f


@router.delete("/{conv_id}/fontes/{fid}")
def fonte_apagar(conv_id: int, fid: str):
    base.conferir_conv(conv_id)
    with base.mudar(conv_id) as est:
        est["fontes"] = [x for x in est["fontes"] if x["id"] != fid]
    (base.pasta(conv_id) / "fontes" / f"{fid}.txt").unlink(missing_ok=True)
    return {"ok": True}


@router.get("/{conv_id}/trecho/{tid}")
def trecho(conv_id: int, tid: str):
    base.conferir_conv(conv_id)
    t = corpus.trecho(conv_id, tid)
    if not t:
        raise HTTPException(404, "Trecho não encontrado.")
    m = corpus.ID_TRECHO.search(tid)
    f = next((x for x in base.ler(conv_id)["fontes"] if x["id"] == f"F{m.group(1)}"), {})
    return {"id": tid, "texto": t, "fonte": f}


@router.get("/{conv_id}/fontes/{fid}/texto")
def fonte_texto(conv_id: int, fid: str):
    base.conferir_conv(conv_id)
    return {"texto": corpus.texto_da_fonte(conv_id, fid)}


# ------------------------------------------------------------------ material, prova, revisão

@router.post("/{conv_id}/material")
async def escrever(conv_id: int, body: AlvoBody):
    return _ok(_disparar, conv_id, "material", body, {"topicos": body.topicos}, material.rodar)


@router.post("/{conv_id}/prova")
async def gerar_prova(conv_id: int, body: ProvaBody):
    return _ok(_disparar, conv_id, "prova", body, body.model_dump(exclude={"provider", "model"}), prova.rodar)


@router.post("/{conv_id}/prova/{pid}/entregar")
async def entregar(conv_id: int, pid: str, body: EntregaBody):
    return _ok(_disparar, conv_id, "correcao", body, {"prova": pid, "respostas": body.respostas,
                                                       "segundos": body.segundos}, prova.corrigir)


@router.delete("/{conv_id}/prova/{pid}")
def apagar_prova(conv_id: int, pid: str):
    base.conferir_conv(conv_id)
    prova.apagar(conv_id, pid)
    return {"ok": True}


@router.post("/{conv_id}/flashcards")
async def flashcards(conv_id: int, body: AlvoBody):
    return _ok(_disparar, conv_id, "flashcards", body, {"topicos": body.topicos}, extras.rodar_flashcards)


@router.post("/{conv_id}/flashcards/{cid}")
def rever(conv_id: int, cid: str, body: CartaoBody):
    return _ok(extras.rever_cartao, conv_id, cid, body.acertou)


@router.delete("/{conv_id}/flashcards/{cid}")
def apagar_cartao(conv_id: int, cid: str):
    base.conferir_conv(conv_id)
    extras.apagar_cartao(conv_id, cid)
    return {"ok": True}


@router.get("/{conv_id}/anki.csv", response_class=PlainTextResponse)
def anki(conv_id: int):
    base.conferir_conv(conv_id)
    return PlainTextResponse(extras.csv_anki(base.ler(conv_id)), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="flashcards.csv"'})


@router.post("/{conv_id}/duvida")
async def duvida(conv_id: int, body: DuvidaBody):
    try:
        base.conferir_conv(conv_id)
        return await extras.tutor(conv_id, base.spec_de(body.provider, body.model), body.pergunta, body.topico)
    except ToolError as e:
        raise HTTPException(400, str(e))

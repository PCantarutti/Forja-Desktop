"""E18 etapa 4: agendador da tela Conteúdo (as automações das especificações).

Dois modos, por especificação:
- aprovacao: no `hora_roteiros` pesquisa e escreve os roteiros e avisa o celular; no `hora_producao` produz o
  roteiro que o usuário aprovou (sem aprovado, avisa e não faz nada);
- automatico: no `hora_producao` pesquisa, aprova sozinho o roteiro de maior confiança e produz.

Cada trilha (r = roteiros, p = produção) roda no máximo uma vez por dia e só dentro de uma janela depois do
horário: o Forja aberto às 10h não dispara a produção das 3h (o usuário pode estar usando o PC). Estado no
AppSetting `conteudo_agenda`: {conv_id: {"r": {...}, "p": {...}}}, então o app reiniciado no meio continua.

Enquanto houver automação ligada o /api/activity diz `acordado` e o Electron segura o sono do PC: dormindo
às 3h, nada roda.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from . import conteudo, conteudo_producao, conteudo_roteiros, db
from .tools import ToolError

log = logging.getLogger(__name__)

CHAVE = "conteudo_agenda"
JANELA = timedelta(hours=3)          # quanto depois do horário ainda vale disparar
ESPERA_ROTEIROS = timedelta(hours=3)  # roteiros que não ficam prontos nisso viram falha
ESPERA_FILA = timedelta(hours=6)      # produção esperando outra terminar
TIQUE = 30                            # segundos


def agora() -> datetime:   # os testes trocam o relógio por aqui
    return datetime.now()


# ------------------------------------------------------------------ estado

def _todos() -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        return dict(linha.value) if linha and isinstance(linha.value, dict) else {}


def _grava(conv_id: int, trilha: str, **campos) -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        dados = dict(linha.value) if linha and isinstance(linha.value, dict) else {}
        conv = dict(dados.get(str(conv_id)) or {})
        conv[trilha] = {**(conv.get(trilha) or {}), **campos}
        dados[str(conv_id)] = conv
        s.merge(db.AppSetting(key=CHAVE, value=dados))
        s.commit()
        return conv[trilha]


def estado(conv_id: int) -> dict:
    """O que a tela mostra: o que já rodou hoje e quando é a próxima vez."""
    spec = conteudo.especificacao(conv_id)
    a = spec["automacao"]
    e = _todos().get(str(conv_id)) or {}
    proximas = {}
    if a["modo"] != "desligada":
        horarios = {"p": a["hora_producao"]} if a["modo"] == "automatico" else \
            {"r": a["hora_roteiros"], "p": a["hora_producao"]}
        for trilha, hora in horarios.items():
            proximas[trilha] = _proxima(hora, (e.get(trilha) or {}).get("dia")).isoformat(timespec="minutes")
    return {"modo": a["modo"], "r": e.get("r") or {}, "p": e.get("p") or {}, "proximas": proximas}


def _horario(hora: str, dia: datetime) -> datetime:
    h, m = (int(x) for x in hora.split(":"))
    return dia.replace(hour=h, minute=m, second=0, microsecond=0)


def _proxima(hora: str, feito_em: str | None) -> datetime:
    agora_ = agora()
    alvo = _horario(hora, agora_)
    if alvo < agora_ - JANELA or feito_em == alvo.date().isoformat():
        alvo += timedelta(days=1)
    return alvo


def _vence(hora: str, trilha: dict) -> str | None:
    """Dia (ISO) que dispara agora, ou None. Vale o horário de hoje dentro da janela."""
    agora_ = agora()
    alvo = _horario(hora, agora_)
    if alvo <= agora_ <= alvo + JANELA and trilha.get("dia") != alvo.date().isoformat():
        return alvo.date().isoformat()
    return None


def ligadas() -> list[dict]:
    return [s for s in conteudo.especificacoes() if s["automacao"]["modo"] != "desligada"]


def acordado() -> bool:
    """Alguma automação ligada: o PC não pode dormir (vai no /api/activity, o Electron segura o sono)."""
    try:
        return bool(ligadas())
    except Exception:
        return False


def _avisa(titulo: str, texto: str, conv_id: int) -> None:
    """Push no celular. É HTTP síncrono: dentro do loop do app vai para uma thread, para não travar o resto."""
    def manda() -> None:
        try:
            from . import mobile
            mobile.avisa(titulo, texto, conv_id)
        except Exception:
            log.exception("conteudo: aviso no celular falhou")
    try:
        asyncio.get_running_loop().run_in_executor(None, manda)
    except RuntimeError:   # fora do loop (testes, thread): manda direto
        manda()


# ------------------------------------------------------------------ o tique

def tique() -> list[str]:
    """Um passo de cada automação. Devolve o que fez (para log e testes). Roda no loop do app: os disparos
    (roteiros, produção) criam tasks nele, e o resto é banco e decisão — rápido."""
    feito = []
    for spec in ligadas():
        try:
            feito += _passo(spec)
        except Exception as e:   # uma especificação com problema não para as outras
            log.exception("conteudo: automação %s falhou", spec["id"])
            feito.append(f"{spec['id']}: erro {e}")
    return feito


def _passo(spec: dict) -> list[str]:
    cid, a = spec["id"], spec["automacao"]
    e = _todos().get(str(cid)) or {}
    r, p = e.get("r") or {}, e.get("p") or {}
    feito = []

    if a["modo"] == "aprovacao":
        if dia := _vence(a["hora_roteiros"], r):
            feito.append(_iniciar_roteiros(cid, "r", dia))
        elif r.get("etapa") == "roteiros":
            feito += _acompanhar_roteiros(cid, "r", r, aprovar=False)
        if dia := _vence(a["hora_producao"], p):
            achado = conteudo_roteiros.aprovado(cid)
            if not achado:
                _grava(cid, "p", dia=dia, etapa="feito", aviso="Nenhum roteiro aprovado para hoje.")
                _avisa(f"Conteúdo: {spec['nome']}", "Nenhum roteiro aprovado: o vídeo de hoje não foi produzido.", cid)
                feito.append(f"{cid}: sem aprovado")
            else:
                _grava(cid, "p", dia=dia, etapa="fila", desde=agora().isoformat(), aviso="")
                feito.append(_produzir(cid, "p"))
        elif p.get("etapa") in ("fila", "produzindo"):
            feito += _acompanhar_producao(cid, "p", p)

    elif a["modo"] == "automatico":
        if dia := _vence(a["hora_producao"], p):
            feito.append(_iniciar_roteiros(cid, "p", dia))
        elif p.get("etapa") == "roteiros":
            feito += _acompanhar_roteiros(cid, "p", p, aprovar=True)
        elif p.get("etapa") in ("fila", "produzindo"):
            feito += _acompanhar_producao(cid, "p", p)
    return feito


def _iniciar_roteiros(cid: int, trilha: str, dia: str) -> str:
    try:
        rod = conteudo_roteiros.iniciar(cid)
    except ToolError as e:
        _grava(cid, trilha, dia=dia, etapa="falhou", aviso=str(e))
        _avisa("Conteúdo: automação parou", str(e), cid)
        return f"{cid}: roteiros não começaram ({e})"
    _grava(cid, trilha, dia=dia, etapa="roteiros", rodada=rod["id"], desde=agora().isoformat(), aviso="")
    return f"{cid}: roteiros {rod['id']}"


def _acompanhar_roteiros(cid: int, trilha: str, t: dict, aprovar: bool) -> list[str]:
    rod = conteudo_roteiros.estado(t["rodada"])
    if rod["status"] in ("rodando", "aguardando"):
        if agora() - datetime.fromisoformat(t["desde"]) > ESPERA_ROTEIROS:
            conteudo_roteiros.cancelar(t["rodada"])
            aviso = ("O Claude não atendeu o pedido de roteiros a tempo (abra uma sessão conectada ao Forja)."
                     if rod["status"] == "aguardando" else "A pesquisa de roteiros passou do tempo.")
            _grava(cid, trilha, etapa="falhou", aviso=aviso)
            _avisa("Conteúdo: automação parou", aviso, cid)
            return [f"{cid}: roteiros estourou o tempo"]
        return []
    if rod["status"] == "cancelado":   # quem cancelou foi o usuário: registra, sem aviso de falha no celular
        _grava(cid, trilha, etapa="falhou", aviso="Pesquisa cancelada.")
        return [f"{cid}: roteiros cancelados"]
    roteiros = [x for x in rod.get("roteiros") or [] if x["status"] == "novo"]
    if rod["status"] != "ok" or not roteiros:
        aviso = rod.get("aviso") or "A pesquisa não gerou roteiros."
        _grava(cid, trilha, etapa="falhou", aviso=aviso)
        _avisa("Conteúdo: sem roteiros hoje", aviso, cid)
        return [f"{cid}: roteiros falharam"]
    if not aprovar:
        _grava(cid, trilha, etapa="feito", aviso="")
        _avisa("Roteiros prontos para aprovar", f"{len(roteiros)} roteiro(s) novos. Aprove um até o horário da produção.", cid)
        return [f"{cid}: roteiros prontos ({len(roteiros)})"]
    melhor = max(roteiros, key=lambda x: x.get("confianca") or 0)   # empate: o primeiro, que o modelo pôs na frente
    conteudo_roteiros.marcar(rod["id"], melhor["id"], "aprovado")
    _grava(cid, trilha, etapa="fila", desde=agora().isoformat(), escolhido=melhor["titulo_youtube"] or melhor["titulo"])
    return [f"{cid}: aprovado {melhor['id']}", _produzir(cid, trilha)]


def _produzir(cid: int, trilha: str) -> str:
    try:
        prod = conteudo_producao.iniciar(cid)
    except ToolError as e:
        if "Já tem um vídeo" in str(e):   # outra produção rodando: tenta de novo no próximo tique
            return f"{cid}: produção na fila"
        _grava(cid, trilha, etapa="falhou", aviso=str(e))
        _avisa("Conteúdo: produção não começou", str(e), cid)
        return f"{cid}: produção não começou ({e})"
    _grava(cid, trilha, etapa="produzindo", producao=prod["id"])
    return f"{cid}: produção {prod['id']}"


def _acompanhar_producao(cid: int, trilha: str, t: dict) -> list[str]:
    if t["etapa"] == "fila":
        if agora() - datetime.fromisoformat(t["desde"]) > ESPERA_FILA:
            _grava(cid, trilha, etapa="falhou", aviso="Outra produção ocupou a máquina por tempo demais.")
            return [f"{cid}: fila estourou"]
        return [_produzir(cid, trilha)]
    prod = conteudo_producao.estado(t["producao"])
    if prod["status"] == "rodando":
        return []
    # o aviso de "vídeo pronto"/"não terminou" já sai da própria produção
    _grava(cid, trilha, etapa="feito" if prod["status"] == "ok" else "falhou", aviso=prod.get("aviso") or "")
    return [f"{cid}: produção {prod['status']}"]


async def vigia() -> None:
    """No lifespan: um tique a cada 30 s. O primeiro espera o app subir."""
    await asyncio.sleep(20)
    while True:
        try:
            for linha in tique():
                log.info("conteudo: %s", linha)
        except Exception:
            log.exception("conteudo: tique da agenda falhou")
        await asyncio.sleep(TIQUE)

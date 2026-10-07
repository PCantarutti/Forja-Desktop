"""E18: agendador da tela Conteúdo (as automações das especificações).

Dois modos, por especificação, nos dias da semana marcados (`dias`, 0 = segunda):
- aprovacao: no `hora_roteiros` pesquisa e escreve os roteiros e avisa o celular; em cada um dos `horarios`
  produz o roteiro que o usuário aprovou (sem aprovado, avisa e não faz nada);
- automatico: em cada um dos `horarios` pesquisa, aprova sozinho o roteiro de maior confiança e produz. Nenhuma
  pergunta no caminho: é o modo 100% automático. Com `produzir` desligado, para depois de escolher: o roteiro fica
  aprovado ("pronto") e o celular avisa; o vídeo sai quando a pessoa tocar em gerar.

Cada horário de cada dia é um "slot" e roda no máximo uma vez. O slot só dispara sozinho até TOLERANCIA depois do
horário (o laço passa a cada 30 s, então com o Forja no ar ele nunca atrasa isso). Se o Forja estava fora do ar
(PC desligado, app fechado) e voltou depois, o slot vira "perdido": o Forja avisa no celular e na tela e pergunta
se ainda roda — nada de vídeo de surpresa horas depois. Slot de outro dia não acumula.

Estado no AppSetting `conteudo_agenda`: {conv_id: {"r": {...}, "p": {...}, "perdido": {...}}}, então o app
reiniciado no meio continua. Enquanto houver automação ligada o PC não dorme: o /api/activity diz `acordado`
(o Electron segura o sono) e o próprio backend pede o mesmo ao Windows (rodando sem janela, como serviço, não há
Electron para isso).
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
from datetime import date, datetime, timedelta

from . import conteudo, conteudo_producao, conteudo_roteiros, db
from .tools import ToolError

log = logging.getLogger(__name__)

CHAVE = "conteudo_agenda"
TOLERANCIA = timedelta(minutes=15)    # quanto depois do horário ainda dispara sozinho; depois disso, pergunta
ESPERA_ROTEIROS = timedelta(hours=3)  # roteiros que não ficam prontos nisso viram falha
ESPERA_FILA = timedelta(hours=6)      # produção (ou pesquisa) esperando outra terminar
TIQUE = 30                            # segundos
OCUPADA = ("roteiros", "espera", "fila", "produzindo")


def agora() -> datetime:   # os testes trocam o relógio por aqui
    return datetime.now()


# ------------------------------------------------------------------ estado

def _todos() -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        return dict(linha.value) if linha and isinstance(linha.value, dict) else {}


def _salva(conv_id: int, mexe) -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        dados = dict(linha.value) if linha and isinstance(linha.value, dict) else {}
        conv = dict(dados.get(str(conv_id)) or {})
        mexe(conv)
        dados[str(conv_id)] = conv
        s.merge(db.AppSetting(key=CHAVE, value=dados))
        s.commit()
        return conv


def _grava(conv_id: int, trilha: str, **campos) -> dict:
    def mexe(conv):
        conv[trilha] = {**(conv.get(trilha) or {}), **campos}
    return _salva(conv_id, mexe)[trilha]


def _perdido(conv_id: int, valor: dict | None) -> None:
    def mexe(conv):
        if valor:
            conv["perdido"] = valor
        else:
            conv.pop("perdido", None)
    _salva(conv_id, mexe)


# ------------------------------------------------------------------ horários

def _horario(hora: str, dia: date | datetime) -> datetime:
    h, m = (int(x) for x in hora.split(":"))
    return datetime(dia.year, dia.month, dia.day, h, m)


def _chave(slot: datetime) -> str:
    return slot.isoformat(timespec="minutes")


def _trilhas(a: dict) -> dict[str, list[str]]:
    """Que horas cada trilha roda: r = só roteiros (aprovação), p = produção (ou tudo, no automático)."""
    if a["modo"] == "automatico":
        return {"p": a["horarios"]}
    if a["modo"] == "aprovacao":
        return {"r": [a["hora_roteiros"]], "p": a["horarios"]}
    return {}


def _slots(a: dict, horas: list[str], dia: date) -> list[datetime]:
    return sorted(_horario(h, dia) for h in horas) if dia.weekday() in a["dias"] else []


def _ativado(a: dict) -> datetime:
    try:
        return datetime.fromisoformat(a.get("ativado_em") or "")
    except ValueError:
        return datetime.min


def _devido(a: dict, horas: list[str], t: dict) -> datetime | None:
    """O último slot de hoje que já passou e ainda não foi tratado (nem rodado, nem perguntado, nem pulado)."""
    agora_ = agora()
    passados = [s for s in _slots(a, horas, agora_.date()) if s <= agora_ and s >= _ativado(a)]
    if not passados or _chave(passados[-1]) <= (t.get("slot") or ""):
        return None
    return passados[-1]


def _proxima(a: dict, horas: list[str], t: dict) -> datetime | None:
    agora_ = agora()
    for n in range(8):
        for s in _slots(a, horas, (agora_ + timedelta(days=n)).date()):
            if s > agora_ - TOLERANCIA and _chave(s) > (t.get("slot") or "") and s >= _ativado(a):
                return s
    return None


def estado(conv_id: int) -> dict:
    """O que a tela mostra: o que já rodou, quando é a próxima vez e se tem horário perdido esperando resposta."""
    spec = conteudo.especificacao(conv_id)
    a = spec["automacao"]
    e = _todos().get(str(conv_id)) or {}
    proximas = {}
    for trilha, horas in _trilhas(a).items():
        if s := _proxima(a, horas, e.get(trilha) or {}):
            proximas[trilha] = _chave(s)
    perdido = e.get("perdido") if a["modo"] != "desligada" else None
    p = e.get("p") or {}
    pronto = None   # roteiro que o automático escolheu e deixou para a pessoa gerar (some quando vira vídeo)
    if p.get("etapa") == "pronto" and (achado := conteudo_roteiros.aprovado(conv_id)):
        rid, roteiro = achado[0], achado[1]
        pronto = {"rodada": rid, "roteiro": roteiro["id"], "titulo": roteiro.get("titulo_youtube") or roteiro.get("titulo")}
    return {"modo": a["modo"], "r": e.get("r") or {}, "p": p, "proximas": proximas,
            "perdido": perdido or None, "pronto": pronto}


def ligadas() -> list[dict]:
    return [s for s in conteudo.especificacoes() if s["automacao"]["modo"] != "desligada"]


def acordado() -> bool:
    """Alguma automação ligada: o PC não pode dormir (vai no /api/activity, o Electron segura o sono)."""
    try:
        return bool(ligadas())
    except Exception:
        return False


def ocupado() -> bool:
    """Alguma automação no meio do caminho (pesquisa, fila ou produção): o serviço sem janela não pode sair."""
    return any((e.get(t) or {}).get("etapa") in OCUPADA for e in _todos().values() for t in ("r", "p"))


def _segura_sono(sim: bool) -> None:
    """O backend pede ao Windows para não dormir (ES_SYSTEM_REQUIRED; a tela pode apagar). Vale para a thread que
    chama — a do laço do app, que vive o tempo todo."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if sim else 0))
    except Exception:
        pass


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
    specs = ligadas()
    _segura_sono(bool(specs))
    for spec in specs:
        try:
            feito += _passo(spec)
        except Exception as e:   # uma especificação com problema não para as outras
            log.exception("conteudo: automação %s falhou", spec["id"])
            feito.append(f"{spec['id']}: erro {e}")
    return feito


def _passo(spec: dict) -> list[str]:
    cid, a = spec["id"], spec["automacao"]
    e = _todos().get(str(cid)) or {}
    feito = []
    for trilha, horas in _trilhas(a).items():
        t = e.get(trilha) or {}
        if t.get("etapa") in OCUPADA:
            feito += _acompanhar(spec, trilha, t)
            t = (_todos().get(str(cid)) or {}).get(trilha) or {}
        slot = _devido(a, horas, t)
        if not slot:
            continue
        chave = _chave(slot)
        if t.get("etapa") in OCUPADA:   # a vez anterior ainda está rodando: este horário fica para trás
            _grava(cid, trilha, slot=chave)
            feito.append(f"{cid}: {chave} pulado, a anterior ainda roda")
        elif agora() - slot <= TOLERANCIA:
            _perdido(cid, None)   # horário novo rodando: a pergunta sobre um anterior perde o sentido
            feito.append(_disparar(spec, trilha, chave))
        else:
            _grava(cid, trilha, slot=chave)
            _perdido(cid, {"trilha": trilha, "slot": chave, "desde": agora().isoformat(timespec="seconds")})
            hora = chave[11:16]
            o_que = "a pesquisa de roteiros" if trilha == "r" else "o vídeo"
            _avisa(f"Conteúdo: {spec['nome']}",
                   f"O Forja estava desligado às {hora} e {o_que} de hoje não rodou. Abra o Conteúdo para rodar agora ou pular.", cid)
            feito.append(f"{cid}: {chave} perdido")
    return feito


def _disparar(spec: dict, trilha: str, chave: str) -> str:
    cid, a = spec["id"], spec["automacao"]
    if trilha == "r" or a["modo"] == "automatico":
        return _iniciar_roteiros(cid, trilha, chave)
    achado = conteudo_roteiros.aprovado(cid)
    if not achado:
        _grava(cid, trilha, slot=chave, dia=chave[:10], etapa="feito", aviso="Nenhum roteiro aprovado para este horário.")
        _avisa(f"Conteúdo: {spec['nome']}", "Nenhum roteiro aprovado: o vídeo deste horário não foi produzido.", cid)
        return f"{cid}: sem aprovado"
    _grava(cid, trilha, slot=chave, dia=chave[:10], etapa="fila", desde=agora().isoformat(), aviso="", escolhido="")
    return _produzir(cid, trilha)


def responder_perdido(conv_id: int, acao: str) -> dict:
    """A resposta à pergunta do horário perdido: "rodar" agora ou "pular"."""
    if acao not in ("rodar", "pular"):
        raise ToolError("Ação inválida: rodar ou pular.")
    spec = conteudo.especificacao(conv_id)
    p = (_todos().get(str(conv_id)) or {}).get("perdido")
    if not p:
        raise ToolError("Não tem horário perdido esperando resposta.")
    _perdido(conv_id, None)
    if acao == "rodar":
        t = (_todos().get(str(conv_id)) or {}).get(p["trilha"]) or {}
        if t.get("etapa") in OCUPADA:
            raise ToolError("Esta especificação já está rodando agora.")
        log.info("conteudo: %s", _disparar(spec, p["trilha"], p["slot"]))
    return estado(conv_id)


def _pesquisa_rodando() -> bool:
    return any(r.get("status") == "rodando" for r in conteudo_roteiros._RUNS.values())


def _iniciar_roteiros(cid: int, trilha: str, chave: str, tentativa: int | None = 0) -> str:
    """tentativa: 0 = slot novo (zera as segundas chances), 1 = a 2ª pesquisa, ampliada; None = retomar da fila."""
    if tentativa is not None:
        _grava(cid, trilha, tentativa=tentativa, tentou_producao=False)
    tentativa = (_todos().get(str(cid)) or {}).get(trilha, {}).get("tentativa", 0)
    # Uma pesquisa por vez: com o modelo local, duas ao mesmo tempo dividem a GPU e as duas ficam lentas.
    if _pesquisa_rodando():
        t = (_todos().get(str(cid)) or {}).get(trilha) or {}
        desde = t.get("desde") if t.get("etapa") == "espera" else agora().isoformat()
        _grava(cid, trilha, slot=chave, dia=chave[:10], etapa="espera", desde=desde, aviso="", escolhido="")
        return f"{cid}: pesquisa na fila"
    try:
        rod = conteudo_roteiros.iniciar(cid, ampliar=bool(tentativa))
    except ToolError as e:
        # automático (trilha p): a pesquisa nem começou (modelo ausente, sem VRAM) — uma pauta guardada ainda salva o dia
        _grava(cid, trilha, slot=chave, dia=chave[:10])
        if trilha == "p" and (guardada := _pauta_guardada(cid, trilha, str(e))):
            return " · ".join(guardada)
        _grava(cid, trilha, slot=chave, dia=chave[:10], etapa="falhou", aviso=str(e))
        _avisa("Conteúdo: automação parou", str(e), cid)
        return f"{cid}: roteiros não começaram ({e})"
    _grava(cid, trilha, slot=chave, dia=chave[:10], etapa="roteiros", rodada=rod["id"], desde=agora().isoformat(),
           aviso="", escolhido="")
    return f"{cid}: roteiros {rod['id']}"


def _acompanhar(spec: dict, trilha: str, t: dict) -> list[str]:
    cid = spec["id"]
    if t["etapa"] == "espera":
        if agora() - datetime.fromisoformat(t["desde"]) > ESPERA_FILA:
            _grava(cid, trilha, etapa="falhou", aviso="Outra pesquisa ocupou a máquina por tempo demais.")
            return [f"{cid}: fila da pesquisa estourou"]
        return [] if _pesquisa_rodando() else [_iniciar_roteiros(cid, trilha, t["slot"], None)]
    if t["etapa"] == "roteiros":
        aprovar = trilha == "p"   # no automático a trilha p faz tudo; na aprovação, r só escreve
        return _acompanhar_roteiros(cid, trilha, t, aprovar)
    return _acompanhar_producao(cid, trilha, t)


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
        aviso = rod.get("aviso") or ("Os roteiros repetiam vídeos já feitos." if rod.get("roteiros") else
                                     "A pesquisa não gerou roteiros.")
        if aprovar and (plano_b := _plano_b(cid, trilha, t, aviso)):
            return plano_b
        _grava(cid, trilha, etapa="falhou", aviso=aviso)
        _avisa("Conteúdo: sem roteiros hoje", aviso, cid)
        return [f"{cid}: roteiros falharam"]
    if not aprovar:
        _grava(cid, trilha, etapa="feito", aviso="")
        _avisa("Roteiros prontos para aprovar", f"{len(roteiros)} roteiro(s) novos. Aprove um até o horário da produção.", cid)
        return [f"{cid}: roteiros prontos ({len(roteiros)})"]
    return _aprovar_e_seguir(cid, trilha, rod["id"], _melhor(cid, roteiros))


def _melhor(cid: int, roteiros: list[dict]) -> dict:
    spec = conteudo.especificacao(cid)
    limite = (agora() - timedelta(days=spec["dias"])).date().isoformat()
    recente = lambda x: (x.get("noticia") or {}).get("data", "") >= limite or not (x.get("noticia") or {}).get("data")
    minimo = spec.get("duracao_min") or 0
    # notícia dentro do período vence a antiga; depois, quem atinge a duração mínima; depois, a confiança.
    # Empate: o primeiro, que o modelo pôs na frente
    return max(roteiros, key=lambda x: (recente(x), (x.get("segundos") or 0) >= minimo, x.get("confianca") or 0))


def _plano_b(cid: int, trilha: str, t: dict, aviso: str) -> list[str] | None:
    """A pesquisa do dia não rendeu (busca vazia, nada novo, JSON ruim, tudo repetido). Antes de desistir: 1) a melhor
    pauta guardada de rodadas anteriores; 2) uma 2ª pesquisa com o dobro da janela de dias. Só no automático."""
    if (guardada := _pauta_guardada(cid, trilha, aviso)):
        return guardada
    if not t.get("tentativa"):
        return [f"{cid}: 2ª pesquisa ({aviso})", _iniciar_roteiros(cid, trilha, t["slot"], 1)]
    return None


def _pauta_guardada(cid: int, trilha: str, aviso: str) -> list[str] | None:
    guardadas = conteudo_roteiros.reserva(cid)
    if not guardadas:
        return None
    melhor = _melhor(cid, [x for _, x in guardadas])
    mid = next(m for m, x in guardadas if x["id"] == melhor["id"])
    log.info("conteudo: %s sem roteiro novo (%s); usando pauta guardada %s", cid, aviso, melhor["id"])
    return [f"{cid}: pauta guardada ({aviso})", *_aprovar_e_seguir(cid, trilha, mid, melhor, f"Pauta guardada: {aviso}")]


def _aprovar_e_seguir(cid: int, trilha: str, rodada: int, melhor: dict, nota: str = "") -> list[str]:
    conteudo_roteiros.marcar(rodada, melhor["id"], "aprovado")
    titulo = melhor["titulo_youtube"] or melhor["titulo"]
    if nota:
        _grava(cid, trilha, aviso=nota)
    if not conteudo.especificacao(cid)["automacao"].get("produzir", True):
        _grava(cid, trilha, etapa="pronto", escolhido=titulo, aviso=nota)
        _avisa("Roteiro pronto para gerar", f"{titulo} — abra o Conteúdo e toque em gerar o vídeo."
               + (f" ({nota})" if nota else ""), cid)
        return [f"{cid}: aprovado {melhor['id']}", f"{cid}: pronto para gerar"]
    _grava(cid, trilha, etapa="fila", desde=agora().isoformat(), escolhido=titulo, aviso=nota)
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
    if prod["status"] == "erro" and not t.get("tentou_producao"):
        # falha que pode ser passageira (rede, Claude ocupado): o roteiro segue aprovado, tenta mais uma vez
        _grava(cid, trilha, tentou_producao=True, aviso=f"2ª tentativa: {prod.get('aviso') or 'a 1ª não terminou'}")
        return [f"{cid}: produção falhou, 2ª tentativa", _produzir(cid, trilha)]
    # o aviso de "vídeo pronto"/"não terminou" já sai da própria produção
    _grava(cid, trilha, etapa="feito" if prod["status"] == "ok" else "falhou", aviso=prod.get("aviso") or "")
    return [f"{cid}: produção {prod['status']}"]


async def vigia() -> None:
    """No lifespan: um tique a cada 30 s. O primeiro espera o app subir."""
    from . import servico
    await asyncio.sleep(20)
    while True:
        try:
            for linha in tique():
                log.info("conteudo: %s", linha)
            servico.talvez_sair()
        except Exception:
            log.exception("conteudo: tique da agenda falhou")
        await asyncio.sleep(TIQUE)

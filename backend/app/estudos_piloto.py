"""Piloto automático do Estudos: percorre o cronograma e gera, sozinho e em sequência, o resumo e a prova de cada
tópico até a data escolhida — para deixar rodando à noite. Antes, busca provas reais de cada matéria na web (as
provas geradas passam a imitar o estilo delas).

Estado: uma mensagem `tipo:"piloto"` (user) por estudo, do objetivo inteiro (SEM_MATERIA):
  {ativo, ate, motor:{provider, model, ex_provider, ex_model}, preferencias, web, profundidade, questoes,
   buscas:{materia_id: busca_mid}, feitos:{tarefa_id: {resumo, prova, erro}}, fase, atual, aviso}
Pausar cancela o run da vez; continuar refaz aquele item do zero (ele não entrou em `feitos`). Na subida do app,
`retomar()` relança o que estava ativo: o `reap()` já marcou o run interrompido como erro, e ele é refeito.
"""
import asyncio
import copy
import logging
import threading
from datetime import date

from sqlalchemy import select

from . import db, estudos as E

log = logging.getLogger(__name__)

_TRAVA = threading.Lock()
_VIVOS: dict[int, asyncio.Task] = {}   # conv_id → o laço do piloto (referência forte: o loop só guarda fraca)
SONDA = 2.0          # segundos entre olhadas no status do run da vez
ERROS_SEGUIDOS = 3   # tantos itens falhando em fila = algo de fora (modelo fora do ar): pausa em vez de queimar a lista


# ------------------------------------------------------------------ estado

def _msg(s, conv_id: int):
    return next((m for m in s.scalars(select(db.Message).where(db.Message.conversation_id == conv_id,
                                                               db.Message.role == "user"))
                 if ((m.meta or {}).get("estudos") or {}).get("tipo") == "piloto"), None)


def ler(conv_id: int) -> dict:
    with db.session() as s:
        m = _msg(s, conv_id)
        e = copy.deepcopy(m.meta["estudos"]) if m else {}
    return {"tipo": "piloto", "ativo": False, "ate": "", "motor": {}, "preferencias": {}, "web": True,
            "profundidade": "normal", "questoes": 10, "buscas": {}, "feitos": {}, "fase": "", "atual": None,
            "aviso": "", **e}


def _mudar(conv_id: int, **campos) -> dict:
    """Grava o dict inteiro (JSON sem MutableDict) e mexe no carimbo: o PC e o celular veem na hora."""
    with _TRAVA:
        p = {**ler(conv_id), **campos}
        with db.session() as s:
            m = _msg(s, conv_id)
            if m:
                m.meta = {**(m.meta or {}), "estudos": p}
                E._tocar(s, conv_id)
                s.commit()
        if not m:
            E._save(conv_id, role="user", content="Piloto automático", status="pronto", meta={"estudos": p})
    return p


# ------------------------------------------------------------------ a fila

def _plano(conv_id: int) -> dict | None:
    from . import estudos_revisao
    return estudos_revisao._ler(conv_id).get("plano")


def tarefas(conv_id: int, ate: str) -> list[dict]:
    """As tarefas que o piloto faz, na ordem do cronograma: estudar (resumo + prova) e simulado; revisar é com a
    pessoa. `materia` é o id da matéria do tópico ("Português · Crase"), ou None no simulado geral."""
    plano = _plano(conv_id) or {}
    ms = E.materias(conv_id)
    out = []
    for d in plano.get("dias") or []:
        if d["dia"] > ate:
            break
        for t in d["tarefas"]:
            if t["tipo"] == "simulado":
                out.append({**t, "dia": d["dia"], "materia": None, "tema": ""})
            elif t["tipo"] == "estudar" and t.get("topico"):
                nome, _, resto = t["topico"].partition(" · ")
                m = next((x for x in ms if x["nome"] == nome), None) if resto else None
                if m is None and len(ms) == 1:   # objetivo de uma matéria só: o tópico vem sem prefixo
                    m, resto = ms[0], t["topico"]
                out.append({**t, "dia": d["dia"], "materia": m["id"] if m else None, "tema": resto or t["topico"]})
    return out


def resumo(conv_id: int) -> dict:
    """O que a tela mostra: estado, progresso e o vínculo tarefa → resumo/prova (para o cronograma)."""
    p = ler(conv_id)
    lista = tarefas(conv_id, p["ate"]) if p["ate"] else []
    return {"ativo": p["ativo"] and conv_id in _VIVOS, "ate": p["ate"], "fase": p["fase"], "atual": p["atual"],
            "aviso": p["aviso"], "questoes": p["questoes"], "feitos": p["feitos"],
            "total": len(lista), "prontos": sum(1 for t in lista if t["id"] in p["feitos"])}


# ------------------------------------------------------------------ o laço

async def _esperar(mid: int) -> str:
    """Até o run sair do ar; devolve o status final da mensagem."""
    while mid in E._RUNS:
        await asyncio.sleep(SONDA)
    with db.session() as s:
        m = s.get(db.Message, mid)
        return m.status if m else "erro"


async def _rodar_item(conv_id: int, materia: str | None, f, *args) -> tuple[int, str]:
    """Dispara um start do Estudos na matéria certa e espera terminar. Se a pessoa estiver usando o estudo (um
    resumo pedido à mão), espera a vez."""
    while True:
        tok = E.MATERIA.set(materia)
        try:
            msg = f(conv_id, *args)
            break
        except E.ToolError as e:
            if "rodando" not in str(e):
                raise
        finally:
            E.MATERIA.reset(tok)
        await asyncio.sleep(10)
    _mudar(conv_id, atual={**(ler(conv_id)["atual"] or {}), "mid": msg["id"]})
    return msg["id"], await _esperar(msg["id"])


def _pedido(titulo: str, cargo: str, materia: str) -> str:
    """O pedido da busca: o concurso e o cargo por extenso, e a recusa explícita a outro exame — só "Contagem"
    + "Raciocínio Lógico" trouxe apostila de princípio da contagem, e "Conhecimentos Específicos" trouxe o ENEM."""
    alvo = f"{titulo}" + (f", cargo {cargo}" if cargo and cargo.lower() not in titulo.lower() else "")
    return (f"Provas anteriores (caderno de questões e gabarito) do concurso {alvo}" + (f", parte de {materia}" if materia else "")
            + ". Só deste concurso ou da mesma banca e cargo; nada de ENEM, vestibular, apostila ou outro exame.")[:300]


def _parou(conv_id: int) -> bool:
    return not ler(conv_id)["ativo"]


async def _laco(conv_id: int) -> None:
    from . import estudos_busca, estudos_prova
    p = ler(conv_id)
    mot = p["motor"]
    motor = (mot.get("provider", ""), mot.get("model", ""), mot.get("ex_provider", ""), mot.get("ex_model", ""))
    erros = 0
    # 1. provas reais de cada matéria, uma vez cada
    with db.session() as s:
        titulo = E._conv(s, conv_id).title
    from . import estudos_edital
    cargo = ((estudos_edital.ultima(conv_id) or {}).get("cargo") or "").strip()
    for m in E.materias(conv_id) or [{"id": "", "nome": ""}]:   # sem matérias: uma busca do objetivo inteiro
        if _parou(conv_id):
            return
        if m["id"] in ler(conv_id)["buscas"]:
            continue
        _mudar(conv_id, fase=f"buscando provas anteriores{' de ' + m['nome'] if m['nome'] else ''}",
               atual={"etapa": "busca", "materia": m["id"]})
        try:
            mid, st = await _rodar_item(conv_id, m["id"] or None, estudos_busca.start, _pedido(titulo, cargo, m["nome"]), *motor)
        except Exception as e:   # busca é extra: falhou, segue para o cronograma
            log.warning("piloto %s: busca de %s falhou: %s", conv_id, m["nome"], e)
            mid, st = 0, "erro"
        if st == "cancelado" and _parou(conv_id):
            return
        _mudar(conv_id, buscas={**ler(conv_id)["buscas"], m["id"]: mid})

    # 2. o cronograma até a data: resumo e prova de cada tópico; simulado geral nos dias de simulado
    for t in tarefas(conv_id, p["ate"]):
        if _parou(conv_id):
            return
        if t["id"] in ler(conv_id)["feitos"]:
            continue
        feito: dict = {}
        try:
            if t["tipo"] == "simulado":
                _mudar(conv_id, fase=f"simulado de {t['dia'][8:]}/{t['dia'][5:7]}", atual={"etapa": "simulado", "tarefa": t["id"]})
                geral = len(E.materias(conv_id)) > 1
                cfg = {"me": p["questoes"], "dificuldade": "mista", "estilo": True,
                       **({"geral": True, "distribuicao": "fracos"} if geral else {})}
                feito["prova"], st = await _rodar_item(conv_id, None if geral else t["materia"], estudos_prova.start, cfg, *motor)
            else:
                _mudar(conv_id, fase=f"resumo: {t['tema']}", atual={"etapa": "resumo", "tarefa": t["id"], "topico": t["topico"]})
                feito["resumo"], st = await _rodar_item(conv_id, t["materia"], E.start, t["tema"], p["preferencias"],
                                                        p["web"], p["profundidade"], *motor)
                if st == "pronto":
                    if _parou(conv_id):
                        return
                    _mudar(conv_id, fase=f"prova: {t['tema']}", atual={"etapa": "prova", "tarefa": t["id"], "topico": t["topico"]})
                    cfg = {"me": p["questoes"], "dificuldade": "mista", "estilo": True,
                           "instrucoes": f"Prova só sobre o tópico: {t['tema']}."}
                    feito["prova"], st = await _rodar_item(conv_id, t["materia"], estudos_prova.start, cfg, *motor)
        except Exception as e:
            log.warning("piloto %s: %s falhou: %s", conv_id, t["topico"] or t["id"], e)
            st, feito["erro"] = "erro", str(e)[:200]
        if st == "cancelado":   # a pessoa parou o run (pelo Pausar ou pelo botão da própria aba): é pausa
            _mudar(conv_id, ativo=False, fase="pausado", atual=None)
            return
        if st != "pronto":
            feito.setdefault("erro", f"terminou como {st}")
            erros += 1
            _mudar(conv_id, aviso=f"{t['topico'] or 'simulado'}: {feito['erro']}")
            if erros >= ERROS_SEGUIDOS:
                _mudar(conv_id, ativo=False, fase="pausado", atual=None,
                       aviso=f"Pausei: {erros} itens seguidos falharam (último: {feito['erro']}). Confira o modelo e continue.")
                return
        else:
            erros = 0
        _mudar(conv_id, feitos={**ler(conv_id)["feitos"], t["id"]: feito})

    fim = resumo(conv_id)
    _mudar(conv_id, ativo=False, fase="pronto", atual=None)
    from . import mobile
    await asyncio.to_thread(mobile.avisa, f"Estudos: {titulo}"[:80],
                            f"Catálogo pronto até {p['ate'][8:]}/{p['ate'][5:7]}: {fim['prontos']} de {fim['total']} itens do cronograma.", conv_id)


def _lancar(conv_id: int) -> None:
    async def corpo():
        try:
            await _laco(conv_id)
        except Exception:
            log.exception("piloto %s caiu", conv_id)
            _mudar(conv_id, ativo=False, fase="pausado", atual=None, aviso="O piloto parou por um erro interno; continue para retomar.")
        finally:
            _VIVOS.pop(conv_id, None)
            with db.session() as s:   # o "ativo" some da tela na hora
                E._tocar(s, conv_id)
                s.commit()
    _VIVOS[conv_id] = asyncio.create_task(corpo())


# ------------------------------------------------------------------ API

def start(conv_id: int, ate: str | None = None, provider: str | None = None, model: str | None = None,
          ex_provider: str | None = None, ex_model: str | None = None, preferencias: dict | None = None,
          web: bool | None = None, profundidade: str | None = None, questoes: int | None = None) -> dict:
    """Começa ou continua (o que já está em `feitos` não se refaz). O que vier None fica como estava: o
    "Continuar" do celular não manda o modelo nem as preferências escolhidos no PC."""
    p = ler(conv_id)
    mot = p["motor"]
    if provider is None:
        provider, model, ex_provider, ex_model = (mot.get(k, "") for k in ("provider", "model", "ex_provider", "ex_model"))
    model, ex_provider, ex_model = model or "", ex_provider or "", ex_model or ""
    ate = ate or p["ate"]
    preferencias = p["preferencias"] if preferencias is None else preferencias
    web = p["web"] if web is None else web
    profundidade = profundidade or p["profundidade"]
    questoes = questoes or p["questoes"]
    try:
        date.fromisoformat(ate)
    except (TypeError, ValueError):
        raise E.ToolError("Escolha até que dia o piloto deve preparar (AAAA-MM-DD).")
    if not _plano(conv_id):
        raise E.ToolError("Monte o cronograma antes (aba Desempenho, ou pelo edital): o piloto segue ele.")
    _, _, claude = E.modelos(provider, model, ex_provider, ex_model)
    if claude:
        raise E.ToolError("O piloto roda num modelo do Forja; com o Claude, peça pelo MCP.")
    if profundidade not in E.PROFUNDIDADES:
        raise E.ToolError(f"profundidade deve ser {', '.join(E.PROFUNDIDADES)}.")
    _mudar(conv_id, ativo=True, ate=ate, motor={"provider": provider, "model": model, "ex_provider": ex_provider,
                                                 "ex_model": ex_model},
           preferencias=E._prefs(preferencias), web=bool(web), profundidade=profundidade,
           questoes=max(1, min(40, int(questoes or 10))), fase="começando", aviso="")
    if conv_id not in _VIVOS:
        _lancar(conv_id)
    return resumo(conv_id)


def pausar(conv_id: int) -> dict:
    p = _mudar(conv_id, ativo=False, fase="pausado")
    if mid := (p["atual"] or {}).get("mid"):
        E.cancelar(mid)
    return resumo(conv_id)


def retomar() -> int:
    """Na subida (depois do estudos.reap): relança os pilotos que estavam ativos."""
    with db.session() as s:
        convs = [m.conversation_id for m in s.scalars(select(db.Message).where(db.Message.role == "user"))
                 if ((m.meta or {}).get("estudos") or {}).get("tipo") == "piloto" and m.meta["estudos"].get("ativo")]
    for c in convs:
        _lancar(c)
    return len(convs)

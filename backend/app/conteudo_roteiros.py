"""E18 etapa 2: pesquisa de novidades do tema e roteiros prontos no estilo escolhido.

Uma rodada = mensagem `assistant` (name="roteiros") na conversa da especificação, com o estado em
`meta["roteiros"]`. Duas formas de rodar:

- modelo do Forja: as etapas do pesquisa.py (planejar, buscar, ler e extrair) com a persistência daqui
  (`run["gravar"]`, o mesmo gancho que o estudos.py usa), e uma etapa final que escreve N roteiros em JSON
  seguindo o arquivo do estilo;
- Claude via MCP: a mensagem fica `aguardando` e o Claude a pega por `conteudo_pedidos`, pesquisa com a
  própria busca e grava com `conteudo_salvar_roteiros` (como os pedidos da tela Estudos).

Cada roteiro tem status: novo | aprovado | descartado | produzido. Aprovar um desaprova o outro aprovado
da mesma especificação: é ele que a produção da madrugada pega (etapa 4).
"""
from __future__ import annotations

import asyncio
import re
import secrets
import time
from datetime import date

from sqlalchemy import select

from . import config, conteudo, db, pesquisa, modelctl
from .agent import _save
from .parsing import split_think
from .tools import ToolError

CHAVE = "roteiros"
NOME = "roteiros"               # Message.name das rodadas
MOTOR_CLAUDE = "claude-mcp"     # o mesmo literal de estudos.MOTOR_CLAUDE
STATUS = ("novo", "aprovado", "descartado", "produzido")
PORTE = pesquisa.PRESETS["normal"]
RODADAS = 2
TETO = 900                      # segundos de coleta; a escrita ganha pesquisa.TETO_RELATORIO a mais
PALAVRAS_POR_SEGUNDO = 2.4      # voz do estilo alerta-tech (+32%): 150 palavras ≈ 62 s
MAX_ESPERA = 100

_RUNS: dict[int, dict] = {}
_TAREFAS: set[asyncio.Task] = set()

ROTEIROS_PROMPT = """Você é roteirista de vídeos curtos (Shorts) de um canal brasileiro.
Recebe o GUIA DE ESTILO do canal, a ESPECIFICAÇÃO do tema e os ACHADOS de uma pesquisa na web, numerados [1], [2]...
Escreva exatamente {n} roteiros, seguindo à risca o guia (tom, tamanho, estrutura de cenas, regras de escrita e
checagem). Se o guia pede formato lista ("N novidades", "N coisas"), cada item da lista é uma notícia DIFERENTE dos
achados, com fonte própria — nunca uma notícia só fatiada em itens; e o número dito no gancho é o número de itens.
Se não for lista, cada roteiro é sobre uma notícia diferente. Roteiros diferentes entre si: outro ângulo ou outras
notícias.
O vídeo é {formato}. Se o guia não disser o tamanho, use 140 a 170 palavras no vertical e 900 a 1500 no
horizontal (vídeo longo, com mais contexto e mais cenas).

Regras:
- Use só fatos que estão nos achados. Número, data, nome e citação só entram se estiverem numa fonte que você cita.
- Só entra o que aconteceu nos últimos {dias} dias (hoje é {hoje}). Fato mais antigo só como contexto de uma
  notícia nova, nunca como a novidade. Data incerta: confiança no máximo 3.
- Nunca acuse pessoa ou empresa de algo que a fonte não diz; opinião vira pergunta.
- `texto` de cada cena é exatamente o que o narrador fala: sem indicação de câmera e sem marcação de fonte ([1],
  [NASA]) — fontes vão em noticia.fontes e na descrição.
- `visual` de cada cena diz o que aparece na tela, concreto: o número ou palavra em destaque, o elemento a desenhar,
  o gráfico, a comparação. É o roteiro de edição de quem monta o vídeo.

Responda SÓ com um objeto JSON, sem texto antes nem depois:
{{"roteiros": [{{
  "titulo": "nome curto interno",
  "ideia": "2 a 3 frases com o ângulo do vídeo",
  "noticia": {{"resumo": "o fato em 2 a 4 frases", "data": "AAAA-MM-DD ou vazio", "fontes": [1, 3]}},
  "cenas": [{{"id": "hook", "texto": "fala do narrador", "visual": "o que aparece na tela"}}, {{"id": "cta", "texto": "...", "visual": "..."}}],
  "titulo_youtube": "título pronto para publicar",
  "descricao": "descrição pronta para o YouTube, com as fontes no fim",
  "confianca": 4,
  "motivo_confianca": "por que a nota (1 a 5) nos fatos"
}}]}}
Ids de cena: curtos, minúsculos, sem espaço (hook, contexto, virada, cta...)."""

FONTE_NA_FALA = re.compile(r"\s*\[(?:\d+(?:\s*,\s*\d+)*|[A-Za-zÀ-ú .&/-]{2,30})\]")   # [1], [1, 3], [NASA]

REFORCO = "\n\nATENÇÃO: a resposta anterior não era um JSON válido. Responda só com o objeto JSON pedido."


# ------------------------------------------------------------------ estado

def _publico(run: dict) -> dict:
    # status não vai para o meta: ele mora na coluna Message.status (rodando/aguardando/ok/erro/cancelado)
    fora = ("cancelar", "t0", "teto", "message_id", "conv_id", "lidas", "porte", "gravar", "erro_busca",
            "anterior", "parcial", "status")
    return {k: v for k, v in run.items() if k not in fora}


def _patch(message_id: int, status: str | None = None, **campos) -> dict:
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or m.name != NOME:
            raise ToolError("Rodada de roteiros não encontrada.")
        m.meta = {**(m.meta or {}), CHAVE: {**((m.meta or {}).get(CHAVE) or {}), **campos}}
        if status is not None:
            m.status = status
        s.get(db.Conversation, m.conversation_id).updated_at = db._now()   # carimbo do /api/activity
        s.commit()
        return _dict(m)


def _gravar(run: dict) -> None:
    """Transição de etapa (as etapas do pesquisa.py chamam isto no lugar do _persistir delas)."""
    run["stats"]["segundos"] = round(time.monotonic() - run["t0"], 1)
    _patch(run["message_id"], **_publico(run))


def _dict(m: db.Message) -> dict:
    r = dict((m.meta or {}).get(CHAVE) or {})
    r["status"] = {"running": "rodando", "aguardando": "aguardando"}.get(m.status or "", m.status or "pronto")
    return {"id": m.id, "conv_id": m.conversation_id, "criado": m.created_at.isoformat() if m.created_at else None,
            **r}


def estado(message_id: int) -> dict:
    if run := _RUNS.get(message_id):
        run["stats"]["segundos"] = round(time.monotonic() - run["t0"], 1)
        return {"id": message_id, "conv_id": run["conv_id"], **_publico(run), "status": "rodando"}
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or m.name != NOME:
            raise ToolError("Rodada de roteiros não encontrada.")
        return _dict(m)


def listar(conv_id: int) -> list[dict]:
    with db.session() as s:
        ids = list(s.scalars(select(db.Message.id).where(db.Message.conversation_id == conv_id,
                                                         db.Message.name == NOME).order_by(db.Message.id.desc())))
    return [estado(i) for i in ids]


def ativos() -> list[int]:
    """Conversas com rodada viva: acendem a bolinha no /api/activity."""
    return [r["conv_id"] for r in _RUNS.values()]


def reap() -> int:
    """Na subida: rodada que ficou "running" numa queda anterior não tem quem a termine."""
    with db.session() as s:
        presos = list(s.scalars(select(db.Message).where(db.Message.name == NOME, db.Message.status == "running")))
        for m in presos:
            r = dict((m.meta or {}).get(CHAVE) or {})
            r.update(fase="pronto", aviso="Interrompido: o Forja fechou no meio da pesquisa.")
            m.meta, m.status = {**(m.meta or {}), CHAVE: r}, "erro"
        s.commit()
        return len(presos)


def cancelar(message_id: int) -> dict:
    if run := _RUNS.get(message_id):
        run["cancelar"] = True
        return {"ok": True}
    with db.session() as s:   # pedido ao Claude que ninguém pegou: cancelar é tirar da fila
        m = s.get(db.Message, message_id)
        if m and m.name == NOME and m.status == "aguardando":
            m.status = "cancelado"
            s.commit()
    return {"ok": True}


# ------------------------------------------------------------------ disparo

def _pergunta(spec: dict) -> str:
    partes = [f"Notícias e novidades dos últimos {spec['dias']} dias sobre: {spec['tema']}"]
    if spec["palavras_chave"]:
        partes.append(f"Palavras-chave: {', '.join(spec['palavras_chave'])}.")
    return " ".join(partes)


def _contexto(spec: dict) -> str:
    linhas = []
    if spec["fontes"]:
        linhas.append(f"Fontes preferidas (busque nelas também): {', '.join(spec['fontes'])}.")
    if spec["observacoes"]:
        linhas.append(spec["observacoes"])
    return "\n".join(linhas)


def iniciar(conv_id: int) -> dict:
    """Começa uma rodada para a especificação. Devolve o estado inicial."""
    spec = conteudo.especificacao(conv_id)
    if not spec["estilo"]:
        raise ToolError("Escolha o estilo da especificação antes de gerar roteiros.")
    conteudo.ler_estilo(spec["estilo"])   # estilo apagado da pasta: avisa agora, não no fim da pesquisa
    if any(r["conv_id"] == conv_id for r in _RUNS.values()):
        raise ToolError("Já tem uma pesquisa rodando nesta especificação.")
    motor = spec["motor"]
    base = {"pergunta": _pergunta(spec), "contexto": _contexto(spec), "estilo": spec["estilo"],
            "n": spec["roteiros"], "dias": spec["dias"], "formato": spec["formato"], "motor": motor, "roteiros": [], "fontes": [],
            "aviso": "", "fase": "planejando", "plano": {"perguntas": [], "buscas": []}, "rodada": 0,
            "rodadas": [], "stats": {"fontes": 0, "uteis": 0, "segundos": 0.0, "rodadas": 0, "tokens": 0,
                                     "tokens_entrada": 0, "gerando": 0.0, "chamadas": 0, "estimado": False}}

    if motor["provider"] == MOTOR_CLAUDE:
        if not config.MCP_SERVIDOR:
            raise ToolError("Para usar o Claude, ligue \"Permitir que o Claude controle o Forja\" em Configurações › MCP "
                            "e conecte o Claude Code ou o Claude Desktop.")
        msg = _save(conv_id, role="assistant", name=NOME, content="", status="aguardando",
                    meta={CHAVE: {**base, "fase": "aguardando"}})
        return estado(msg.id)

    if not (motor["provider"] and motor["model"]):
        raise ToolError("Escolha o modelo que pesquisa e escreve os roteiros (na especificação).")
    extrator, escritor = pesquisa._modelos(motor["provider"], motor["model"])
    if modelctl.gerenciavel(escritor) and modelctl.gerenciavel(extrator):
        extrator = escritor   # um llama-server só: dois modelos locais se trocariam a cada fonte lida
    base["stats"].update(extrator=extrator["model"], escritor=escritor["model"])
    msg = _save(conv_id, role="assistant", name=NOME, content="", status="running", meta={CHAVE: base})
    run = _RUNS[msg.id] = {**base, "message_id": msg.id, "conv_id": conv_id, "cancelar": False,
                           "t0": time.monotonic(), "teto": TETO, "lidas": set(), "porte": PORTE,
                           "erro_busca": "", "gravar": _gravar, "status": "rodando"}
    t = asyncio.create_task(_rodar(run, spec, extrator, escritor))
    _TAREFAS.add(t)   # o loop só guarda referência fraca: sem esta, a task pode sumir no meio
    t.add_done_callback(_TAREFAS.discard)
    return estado(msg.id)


# ------------------------------------------------------------------ execução

# Carga mais leve para quando a VRAM não deu: pesquisa não precisa de visão nem de 131k de contexto, e um slot
# basta. Vale só para esta carga (não mexe nos ajustes salvos do modelo).
CARGA_LEVE = {"mmproj": "", "parallel": 1, "ubatch": 512, "ctx": 65536}
ESPERA_VRAM = 90   # segundos: logo depois do boot, Steam/Discord/navegadores ainda estão ocupando a placa
SEM_VRAM = re.compile(r"OutOfDeviceMemory|failed to allocate|out of memory|GB de VRAM|saiu com código", re.I)


async def garante_modelo(spec: dict) -> None:
    """Modelo local (llama.cpp do Forja): carrega se não estiver no ar. Na automação ninguém clica em "carregar".

    Faltou VRAM (o PC acabou de ligar e outros programas pegaram a placa): tenta a carga leve; ainda faltando,
    espera um pouco e tenta de novo. Outro erro sobe direto."""
    if not modelctl.gerenciavel(spec):
        return
    for tentativa, temporario in enumerate((None, CARGA_LEVE, CARGA_LEVE)):
        if tentativa == 2:
            await asyncio.sleep(ESPERA_VRAM)
        try:
            async for _ in modelctl.ensure(spec, temporario=temporario):
                pass
            return
        except ToolError as e:
            if tentativa == 2 or not SEM_VRAM.search(str(e)):
                raise


async def _rodar(run: dict, spec: dict, extrator: dict, escritor: dict) -> None:
    status = "erro"
    try:
        await garante_modelo(escritor)
        await pesquisa._planejar(run, escritor, PORTE["buscas"])
        consultas = run["plano"]["buscas"]
        limite = asyncio.Semaphore(pesquisa.LEITURAS_PARALELAS)
        extrai = asyncio.Semaphore(
            1 if config.PROVIDERS.get(extrator["provider"], {}).get("type") == "llamacpp" else 3)
        for n in range(1, RODADAS + 1):
            if pesquisa._acabou(run) or not consultas:
                break
            run["rodada"] = n
            resultados = await pesquisa._buscar(run, consultas)
            novas = pesquisa._escolher(run, resultados, PORTE["fontes"])
            if not novas:
                break
            run["fontes"] += novas
            run["fase"] = "lendo"
            _gravar(run)

            async def uma(fonte: dict) -> None:
                async with limite:
                    async with extrai:
                        await pesquisa._extrair(run, fonte, extrator)

            await asyncio.gather(*(uma(f) for f in novas))
            run["stats"].update(fontes=len(run["fontes"]), rodadas=n,
                                uteis=sum(f["status"] == "util" for f in run["fontes"]))
            if n < RODADAS and not pesquisa._acabou(run):
                consultas = await pesquisa._novas_buscas(run, escritor, PORTE["buscas"])

        if run["cancelar"]:
            status, run["aviso"] = "cancelado", "Pesquisa interrompida."
        elif not any(f["status"] == "util" for f in run["fontes"]):
            run["aviso"] = run["aviso"] or (
                "Nenhuma página lida tinha novidade sobre o tema." if run["fontes"] else
                f"A busca não devolveu nada. {run['erro_busca']}".strip())
        else:
            run["fase"] = "escrevendo"
            _gravar(run)
            run["teto"] = (time.monotonic() - run["t0"]) + pesquisa.TETO_RELATORIO
            run["roteiros"] = await _escrever(run, spec, escritor)
            status = "ok" if run["roteiros"] else "erro"
            if not run["roteiros"]:
                run["aviso"] = "O modelo não devolveu roteiros num formato que dê para ler. Tente outro modelo."
    except Exception as e:   # nada pode deixar a rodada presa em "running"
        run["aviso"] = f"{e.__class__.__name__}: {e}"[:300]
    finally:
        run["fase"] = "pronto"
        run["stats"].update(fontes=len(run["fontes"]), uteis=sum(f["status"] == "util" for f in run["fontes"]),
                            segundos=round(time.monotonic() - run["t0"], 1))
        try:
            _patch(run["message_id"], status=status, **_publico(run))
        except ToolError:
            pass   # a especificação foi apagada no meio
        _RUNS.pop(run["message_id"], None)


async def _escrever(run: dict, spec: dict, escritor: dict) -> list[dict]:
    estilo = conteudo.ler_estilo(spec["estilo"])["texto"]
    uteis = [f for f in run["fontes"] if f["status"] == "util"]
    system = ROTEIROS_PROMPT.format(n=spec["roteiros"], dias=spec["dias"], hoje=date.today().isoformat(),
                                    formato=conteudo.FORMATOS[spec["formato"]]["rotulo"])
    user = (f"GUIA DE ESTILO ({spec['estilo']}):\n\n{estilo}\n\n"
            f"ESPECIFICAÇÃO:\nTema: {spec['tema']}\n" + (f"Observações: {spec['observacoes']}\n" if spec["observacoes"] else "")
            + f"\nACHADOS:\n\n{pesquisa._achados(run)}")
    for tentativa in range(2):
        texto = await pesquisa._perguntar(escritor, system + (REFORCO if tentativa else ""), user, run, "alto")
        if roteiros := normalizar(texto, [{"titulo": f["titulo"], "url": f["url"]} for f in uteis]):
            return roteiros[:spec["roteiros"]]
        if pesquisa._acabou(run):
            break
    return []


# ------------------------------------------------------------------ roteiros

def _slug(s: str, i: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")[:24] or f"cena{i + 1}"


def _texto(v, limite: int) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()[:limite]


def normalizar(bruto, fontes: list[dict]) -> list[dict]:
    """Roteiros do modelo (texto com JSON, dict ou lista) no formato da tela. Fonte citada por número
    vira {titulo, url} da lista `fontes` (1 = a primeira); URL solta também vale."""
    dados = bruto
    if isinstance(bruto, str):   # {"roteiros": [...]} ou só a lista [...]
        texto = split_think(bruto)[1]
        dados = pesquisa._json(texto)
        if not (isinstance(dados, dict) and "roteiros" in dados):
            dados = pesquisa._json(texto, list) or dados
    if isinstance(dados, dict):
        dados = dados.get("roteiros")
    if not isinstance(dados, list):
        return []
    out = []
    for r in dados:
        if not isinstance(r, dict):
            continue
        cenas, vistos = [], set()
        for i, c in enumerate(r.get("cenas") or []):
            if not isinstance(c, dict) or not _texto(c.get("texto"), 1):
                continue
            cid = _slug(c.get("id"), i)
            while cid in vistos:
                cid += "-2"
            vistos.add(cid)
            cena = {"id": cid, "texto": _texto(FONTE_NA_FALA.sub("", str(c.get("texto"))), 1200)}
            if visual := _texto(c.get("visual"), 400):
                cena["visual"] = visual
            cenas.append(cena)
        if not cenas:
            continue
        noticia = r.get("noticia") if isinstance(r.get("noticia"), dict) else {}
        citadas = []
        for f in noticia.get("fontes") or []:
            if isinstance(f, int) and 1 <= f <= len(fontes):
                citadas.append(fontes[f - 1])
            elif isinstance(f, dict) and str(f.get("url") or "").startswith("http"):
                citadas.append({"titulo": _texto(f.get("titulo") or f["url"], 200), "url": str(f["url"])[:500]})
            elif isinstance(f, str) and f.startswith("http"):
                citadas.append({"titulo": f[:200], "url": f[:500]})
        palavras = sum(len(c["texto"].split()) for c in cenas)
        try:
            confianca = max(1, min(5, int(r.get("confianca") or 3)))
        except (TypeError, ValueError):
            confianca = 3
        out.append({
            "id": secrets.token_hex(4), "status": "novo",
            "titulo": _texto(r.get("titulo"), 120) or cenas[0]["texto"][:60],
            "ideia": _texto(r.get("ideia"), 800),
            "noticia": {"resumo": _texto(noticia.get("resumo"), 1500), "data": _texto(noticia.get("data"), 20),
                        "fontes": citadas[:8]},
            "cenas": cenas[:40],   # vídeo longo tem bem mais cenas que um Short
            "titulo_youtube": _texto(r.get("titulo_youtube"), 100),
            "descricao": str(r.get("descricao") or "").strip()[:5000],
            "confianca": confianca, "motivo_confianca": _texto(r.get("motivo_confianca"), 500),
            "palavras": palavras, "segundos": round(palavras / PALAVRAS_POR_SEGUNDO),
        })
    return out


def _roteiros_da_conversa(s, conv_id: int) -> list[db.Message]:
    return list(s.scalars(select(db.Message).where(db.Message.conversation_id == conv_id, db.Message.name == NOME)))


def marcar(message_id: int, roteiro_id: str, status: str) -> dict:
    """Aprovar, descartar ou voltar a novo. Aprovar tira o aprovado anterior da mesma especificação."""
    if status not in ("novo", "aprovado", "descartado"):
        raise ToolError("Status de roteiro inválido.")
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or m.name != NOME:
            raise ToolError("Rodada de roteiros não encontrada.")
        achou = False
        for outra in _roteiros_da_conversa(s, m.conversation_id):
            r = dict((outra.meta or {}).get(CHAVE) or {})
            lista = [dict(x) for x in r.get("roteiros") or []]
            mudou = False
            for x in lista:
                if outra.id == message_id and x["id"] == roteiro_id:
                    if x["status"] == "produzido":
                        raise ToolError("Este roteiro já virou vídeo.")
                    x["status"], achou, mudou = status, True, True
                elif status == "aprovado" and x["status"] == "aprovado":
                    x["status"], mudou = "novo", True
            if mudou:
                outra.meta = {**(outra.meta or {}), CHAVE: {**r, "roteiros": lista}}
        if not achou:
            raise ToolError("Roteiro não encontrado.")
        s.get(db.Conversation, m.conversation_id).updated_at = db._now()
        s.commit()
    return estado(message_id)


def editar(message_id: int, roteiro_id: str, campos: dict) -> dict:
    """O usuário ajusta o texto antes de aprovar: título, cenas, título e descrição do YouTube."""
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or m.name != NOME:
            raise ToolError("Rodada de roteiros não encontrada.")
        r = dict((m.meta or {}).get(CHAVE) or {})
        lista = [dict(x) for x in r.get("roteiros") or []]
        x = next((x for x in lista if x["id"] == roteiro_id), None)
        if x is None:
            raise ToolError("Roteiro não encontrado.")
        if x["status"] == "produzido":
            raise ToolError("Este roteiro já virou vídeo.")
        if "cenas" in campos:
            novo = normalizar([{**x, "cenas": campos["cenas"], "noticia": {**x["noticia"], "fontes": []}}], [])
            if not novo:
                raise ToolError("O roteiro precisa de ao menos uma cena com texto.")
            x.update(cenas=novo[0]["cenas"], palavras=novo[0]["palavras"], segundos=novo[0]["segundos"])
        for k, lim in (("titulo", 120), ("titulo_youtube", 100)):
            if k in campos:
                x[k] = _texto(campos[k], lim)
        if "descricao" in campos:
            x["descricao"] = str(campos["descricao"] or "").strip()[:5000]
        m.meta = {**(m.meta or {}), CHAVE: {**r, "roteiros": lista}}
        s.get(db.Conversation, m.conversation_id).updated_at = db._now()
        s.commit()
    return estado(message_id)


def marcar_produzido(message_id: int, roteiro_id: str, video: str) -> None:
    """A produção terminou com vídeo: o roteiro sai da fila de aprovados e guarda onde ficou o arquivo."""
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or m.name != NOME:
            return
        r = dict((m.meta or {}).get(CHAVE) or {})
        lista = [dict(x) for x in r.get("roteiros") or []]
        for x in lista:
            if x["id"] == roteiro_id:
                x.update(status="produzido", video=video)
        m.meta = {**(m.meta or {}), CHAVE: {**r, "roteiros": lista}}
        s.get(db.Conversation, m.conversation_id).updated_at = db._now()
        s.commit()


def achar(message_id: int, roteiro_id: str) -> tuple[dict, dict]:
    """(rodada, roteiro) pelos ids; o roteiro é a cópia do meta."""
    rodada = estado(message_id)
    x = next((x for x in rodada.get("roteiros") or [] if x["id"] == roteiro_id), None)
    if x is None:
        raise ToolError("Roteiro não encontrado.")
    return rodada, x


def aprovado(conv_id: int) -> tuple[int, dict] | None:
    """(message_id, roteiro) aprovado da especificação, para a produção (etapa 3/4)."""
    with db.session() as s:
        for m in sorted(_roteiros_da_conversa(s, conv_id), key=lambda m: -m.id):
            for x in ((m.meta or {}).get(CHAVE) or {}).get("roteiros") or []:
                if x.get("status") == "aprovado":
                    return m.id, x
    return None


# ------------------------------------------------------------------ Claude via MCP

def pedidos() -> list[dict]:
    with db.session() as s:
        return [{"pedido_id": m.id, "conv_id": m.conversation_id, **(m.meta or {}).get(CHAVE, {})}
                for m in s.scalars(select(db.Message).where(db.Message.name == NOME, db.Message.status == "aguardando")
                                   .order_by(db.Message.id))]


def aviso_pedidos() -> str:
    if n := len(pedidos()):
        return f"\n\n[{n} pedido(s) de roteiro na tela Conteúdo esperando você: chame conteudo_pedidos.]"
    return ""


def _bloco(p: dict) -> str:
    spec = conteudo.especificacao(p["conv_id"])
    try:
        estilo = conteudo.ler_estilo(spec["estilo"])["texto"]
    except ToolError as e:
        estilo = f"(não consegui ler o estilo: {e})"
    return "\n".join([
        f"PEDIDO {p['pedido_id']} — {p['n']} roteiro(s) para a especificação \"{spec['nome']}\"",
        f"Pesquise na web (use a sua busca): {p['pergunta']}",
        *([p["contexto"]] if p.get("contexto") else []),
        "",
        f"GUIA DE ESTILO ({spec['estilo']}), siga à risca:",
        estilo,
        "",
        ROTEIROS_PROMPT.format(n=p["n"], dias=p["dias"], hoje=date.today().isoformat(),
                               formato=conteudo.FORMATOS[spec["formato"]]["rotulo"])
        .replace("ACHADOS de uma pesquisa na web, numerados [1], [2]...", "resultado da SUA pesquisa na web"),
        "",
        f"Quando terminar: conteudo_salvar_roteiros(pedido_id={p['pedido_id']}, roteiros=[...], "
        "fontes=[{titulo, url}]). Em cada roteiro, noticia.fontes são números (1 = a primeira de `fontes`).",
    ])


async def mcp_pedidos(espera: int = 60) -> str:
    fim = time.monotonic() + max(0, min(int(espera or 0), MAX_ESPERA))
    while not (lista := pedidos()) and time.monotonic() < fim:
        await asyncio.sleep(1)
    if not lista:
        return "Nenhum pedido de roteiro pendente na tela Conteúdo."
    return "\n\n==========\n\n".join(_bloco(p) for p in lista)


def mcp_salvar(pedido_id: int, roteiros: list, fontes: list | None = None, modelo: str = "") -> str:
    with db.session() as s:
        m = s.get(db.Message, pedido_id)
        if not m or m.name != NOME or m.status != "aguardando":
            return f"ERRO: o pedido {pedido_id} não está esperando roteiros (já foi atendido ou cancelado)."
    lista = [{"titulo": _texto(f.get("titulo") or f.get("url"), 200), "url": str(f.get("url"))[:500]}
             for f in (fontes or []) if isinstance(f, dict) and str(f.get("url") or "").startswith("http")]
    feitos = normalizar(roteiros, lista)
    if not feitos:
        return ("ERRO: nenhum roteiro válido. Cada roteiro precisa de `cenas` com {id, texto}; "
                "veja o formato no pedido (conteudo_pedidos).")
    _patch(pedido_id, status="ok", roteiros=feitos, fase="pronto",
           fontes=[{"id": str(i), "url": f["url"], "titulo": f["titulo"], "status": "util", "resumo": ""}
                   for i, f in enumerate(lista)],
           stats={"fontes": len(lista), "uteis": len(lista), "escritor": modelo or "Claude (MCP)"})
    return f"{len(feitos)} roteiro(s) gravado(s) no pedido {pedido_id}; já aparecem na tela Conteúdo." + aviso_pedidos()

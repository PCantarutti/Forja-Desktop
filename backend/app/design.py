"""Design: chat à esquerda, canvas à direita. A IA gera um documento HTML autocontido e cada pedido
seguinte vira uma versão nova.

Nada de tabela nova, como na pesquisa: um projeto é uma Conversation(kind="design"). Cada pedido
são duas mensagens — a do usuário e a do assistente, cujo meta["design"] guarda a versão:
{versao, html, descricao, atual, rota, stats}. `atual` marca a versão que o canvas mostra
(desfazer/refazer só mudam essa marca; restaurar cria uma nova, o histórico nunca é apagado).
Comentários são mensagens role="event" com meta["design_comentario"].

Falha nunca altera o documento: resposta inválida, erro do modelo ou cancelamento fecham a
mensagem sem versão, e a marca `atual` fica onde estava.

Com modelo local lento, cada pedido vai pela rota mais barata que o atende (rotear):
  plano      projeto vazio: plano JSON → o usuário aprova → esqueleto → uma seção por vez
  fragmento  elementos selecionados ou comentários: só o trecho vai ao modelo
  tokens     mudança global de estilo: só o bloco :root
  secao      refazer/adicionar uma seção de topo
  documento  o documento inteiro, só quando pedido explicitamente
Toda versão sai carimbada: cada elemento tem um data-fid estável, que é o endereço dele.
"""
from __future__ import annotations

import asyncio
import re
import time
from contextlib import aclosing
from html.parser import HTMLParser
from pathlib import Path

from . import config, db, design_html, design_imagens, design_sistema, llm, mirror
from .agent import _save, _stats
from .parsing import split_think
from .tools import ToolError

PROMPTS = Path(__file__).parent / "design_prompts"
TICK = 0.4   # segundos entre retratos do SSE
ROTAS = ("auto", "plano", "tokens", "secao", "documento")
ESFORCOS = ("baixo", "medio", "alto", "maximo")
# qual dos três modelos da tela cada rota usa: plano, geração (seções/documento) ou edição
ETAPA = {"plano": "plano", "etapas": "geracao", "secao": "geracao", "documento": "geracao",
         "fragmento": "edicao", "tokens": "edicao"}
MAX_COMENTARIO = 2000

_RUNS: dict[int, dict] = {}   # message_id -> geração viva
_TAREFAS: set = set()         # referência forte das tasks (o loop só guarda fraca)


def prompt(nome: str) -> str:
    return (PROMPTS / f"{nome}.md").read_text("utf-8")


# ------------------------------------------------------------------ HTML

class _Checa(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: set[str] = set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)


def extrair_html(texto: str) -> str | None:
    """O documento dentro da resposta: tira raciocínio e cerca de código, corta do <!doctype>/<html>
    ao </html>. Sem </html> a resposta veio truncada e não serve."""
    texto = split_think(texto)[1]
    ini = re.search(r"<!doctype html|<html[\s>]", texto, re.I)
    fim = None
    for fim in re.finditer(r"</html\s*>", texto, re.I):
        pass
    if not ini or not fim or fim.end() <= ini.start():
        return None
    html = texto[ini.start():fim.end()]
    p = _Checa()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return None
    return html if {"html", "body"} <= p.tags else None


# ------------------------------------------------------------------ roteamento

_DOC = re.compile(r"\b(documento|pagina|site|layout) (inteir\w*|tod[oa])\b|\bdo zero\b|"
                  r"\b(refaz\w*|reescrev\w*|regener\w*|redesenh\w*) tudo\b|\bcomec\w* de novo\b|\brecomec\w*")
_NOVA = re.compile(r"\b(adicion|inclu|cri|acrescent|insir|insere|coloc|bot|ponh|poe|p[oõ]e)\w*\b.{0,40}\bsecao\b"
                   r"|\bnova secao\b")
_NOME_NOVA = re.compile(r"secao (?:nova )?(?:de |com |para |sobre |dos |das |do |da )?(?:os |as |o |a |uns |umas )?([a-z0-9-]+)")
_TOKENS = re.compile(r"\b(cor|cores|paleta|fonte|fontes|tipografi\w*|espacament\w*|arredondad\w*|sombras?|"
                     r"tema|escur\w*|clar[oa]|contraste|tons?)\b")


def rotear(pedido: str, tem_doc: bool, secoes: list[str], fids: list[str] | None = None) -> tuple[str, str | None]:
    """Heurística simples; o usuário pode forçar outra rota na tela. (rota, seção alvo)."""
    if fids:
        return "fragmento", None
    if not tem_doc:
        return "plano", None
    t = design_html.slug(pedido).replace("-", " ")
    if _DOC.search(t):
        return "documento", None
    if _NOVA.search(t):
        m = _NOME_NOVA.search(t)
        return "secao", design_html.slug(m.group(1)) if m else "nova"
    if citada := next((s for s in secoes if re.search(rf"\b{re.escape(s.replace('-', ' '))}\b", t)), None):
        return "secao", citada
    if _TOKENS.search(t):
        return "tokens", None
    return "documento", None


# ------------------------------------------------------------------ banco

def _versoes(s, conv_id: int) -> list[db.Message]:
    msgs = s.query(db.Message).filter(db.Message.conversation_id == conv_id,
                                      db.Message.role == "assistant").order_by(db.Message.id).all()
    return [m for m in msgs if (m.meta or {}).get("design", {}).get("versao")]


def _marca_atual(s, conv_id: int, versao: int) -> None:
    """meta é JSON puro: só persiste reatribuindo o dict inteiro."""
    for m in _versoes(s, conv_id):
        d = m.meta["design"]
        if d.get("atual") != (d["versao"] == versao):
            m.meta = {**m.meta, "design": {**d, "atual": d["versao"] == versao}}


def _atual(s, conv_id: int) -> db.Message | None:
    vs = _versoes(s, conv_id)
    return next((m for m in vs if m.meta["design"].get("atual")), vs[-1] if vs else None)


def _conv(s, conv_id: int) -> db.Conversation:
    c = s.get(db.Conversation, conv_id)
    if not c or c.kind != "design":
        raise ToolError("Projeto de design não encontrado.")
    return c


def _carimbada(s, m: db.Message) -> str:
    """Versão de antes do carimbo (fase 1) ganha data-fid na primeira vez que é aberta — no próprio
    registro, porque o id só é estável se estiver salvo."""
    html = m.meta["design"]["html"]
    novo = design_html.carimbar(html)
    if novo != html:
        m.meta = {**m.meta, "design": {**m.meta["design"], "html": novo}}
        s.commit()
    return novo


def _comentarios(s, conv_id: int, html: str) -> list[dict]:
    fids = set(re.findall(r'data-fid="([^"]+)"', html))
    out = []
    for m in s.query(db.Message).filter(db.Message.conversation_id == conv_id, db.Message.role == "event") \
            .order_by(db.Message.id):
        c = (m.meta or {}).get("design_comentario")
        if c:
            out.append({"id": m.id, "texto": m.content, **c,
                        "orfao": c["status"] == "pendente" and not set(c["fids"]) <= fids})
    return out


def projeto(conv_id: int) -> dict:
    """Tudo que a tela precisa: chat (sem o HTML de cada versão), comentários, versão atual e o HTML."""
    with db.session() as s:
        c = _conv(s, conv_id)
        atual = _atual(s, conv_id)
        html = _carimbada(s, atual) if atual else ""
        mensagens = []
        for m in c.messages:
            if m.role not in ("user", "assistant"):
                continue
            d = (m.meta or {}).get("design", {})
            mensagens.append({"id": m.id, "role": m.role, "content": m.content, "status": m.status,
                              "thinking": m.thinking or "", "versao": d.get("versao"), "fids": d.get("fids") or [],
                              "entrada": d.get("entrada"), "rota": d.get("rota"), "secao": d.get("secao"),
                              "stats": d.get("stats") or [], "comentarios": d.get("comentarios") or [],
                              "plano": d.get("plano") if m.status == "plano" else None,
                              "created_at": m.created_at.isoformat()})
        return {"conv_id": conv_id, "titulo": c.title, "mensagens": mensagens,
                "total": len(_versoes(s, conv_id)),
                "atual": atual.meta["design"]["versao"] if atual else 0, "html": html,
                "secoes": [e["attrs"]["data-section"] for e in design_html.secoes(html)] if html else [],
                "comentarios": _comentarios(s, conv_id, html),
                "imagens": _imagens(conv_id, html),
                "sistema": (design_sistema.do_documento(html) or {}).get("id") if html else None,
                "rodando": next((mid for mid, r in _RUNS.items() if r["conv_id"] == conv_id), None)}


def _imagens(conv_id: int, html: str) -> dict:
    sl = design_imagens.slots(html) if html else []
    pend = [x["nome"] for x in sl if x["status"] != "pronta"]
    return {"total": len(sl), "pendentes": len(pend), "nomes": pend,
            "conversa": design_imagens.conversa(conv_id) if sl else None}


def ir_para(conv_id: int, versao: int) -> dict:
    """Desfazer/refazer/abrir do histórico: só move a marca."""
    with db.session() as s:
        _conv(s, conv_id)
        if not any(m.meta["design"]["versao"] == versao for m in _versoes(s, conv_id)):
            raise ToolError(f"Versão {versao} não existe.")
        _marca_atual(s, conv_id, versao)
        s.commit()
    return projeto(conv_id)


def restaurar(conv_id: int, versao: int) -> dict:
    """Versão antiga volta como uma versão nova, no topo do histórico."""
    with db.session() as s:
        _conv(s, conv_id)
        velha = next((m for m in _versoes(s, conv_id) if m.meta["design"]["versao"] == versao), None)
        if not velha:
            raise ToolError(f"Versão {versao} não existe.")
        html = velha.meta["design"]["html"]
    _nova_versao(conv_id, None, html, f"restaurada da v{versao}", rota="restaurar")
    return projeto(conv_id)


def _nova_versao(conv_id: int, message_id: int | None, html: str, descricao: str, **extra) -> int:
    """Grava a versão (na mensagem da geração, ou numa nova) e a marca como atual. Os slots de imagem
    saem preenchidos: provisório onde falta, a imagem pronta de antes onde o modelo devolveu sem src."""
    with db.session() as s:
        anterior = _atual(s, conv_id)
        anterior = anterior.meta["design"]["html"] if anterior else ""
    html = design_html.carimbar(design_imagens.preencher(html, anterior))
    with db.session() as s:
        n = max((m.meta["design"]["versao"] for m in _versoes(s, conv_id)), default=0) + 1
        texto = f"v{n}: {descricao}"
        if message_id is None:
            s.add(db.Message(conversation_id=conv_id, role="assistant", content=texto, status="ok",
                             meta={"design": {"versao": n, "html": html, "descricao": descricao, **extra}}))
        else:
            m = s.get(db.Message, message_id)
            antes = (m.meta or {}).get("design", {})
            m.content, m.status = texto, "ok"
            m.meta = {**(m.meta or {}), "design": {**antes, "versao": n, "html": html, "descricao": descricao, **extra}}
        s.get(db.Conversation, conv_id).updated_at = db._now()
        s.flush()
        _marca_atual(s, conv_id, n)
        s.commit()
    return n


def _fecha(message_id: int, status: str, texto: str) -> None:
    with db.session() as s:
        m = s.get(db.Message, message_id)
        m.status, m.content = status, texto
        s.commit()


def _anexa(run: dict) -> None:
    """Raciocínio e estatísticas de todas as chamadas ficam na mensagem, como no chat do agente."""
    with db.session() as s:
        m = s.get(db.Message, run["message_id"])
        m.thinking = run["raciocinio"].strip()
        d = (m.meta or {}).get("design", {})
        m.meta = {**(m.meta or {}), "design": {**d, "stats": run["stats"]}}
        s.commit()


# ------------------------------------------------------------------ comentários e texto (sem IA)

def comentar(conv_id: int, fids: list[str], texto: str) -> dict:
    texto = (texto or "").strip()
    fids = [f for f in dict.fromkeys(fids or []) if f]
    if not texto or len(texto) > MAX_COMENTARIO:
        raise ToolError(f"Comentário vazio ou com mais de {MAX_COMENTARIO} caracteres.")
    if not 1 <= len(fids) <= 20:
        raise ToolError("Selecione de 1 a 20 elementos para comentar.")
    with db.session() as s:
        _conv(s, conv_id)
        atual = _atual(s, conv_id)
        if not atual:
            raise ToolError("Não há documento para comentar.")
        html = _carimbada(s, atual)
        snap = [design_html.outer(html, f) for f in fids]
        if None in snap:
            raise ToolError("Um dos elementos não existe mais no documento.")
        s.add(db.Message(conversation_id=conv_id, role="event", content=texto, meta={"design_comentario": {
            "fids": fids, "snapshot": snap, "status": "pendente",
            "versao_criada": atual.meta["design"]["versao"], "versao_aplicada": None}}))
        s.get(db.Conversation, conv_id).updated_at = db._now()
        s.commit()
    return projeto(conv_id)


def descartar(conv_id: int, comentario_id: int) -> dict:
    with db.session() as s:
        _conv(s, conv_id)
        m = s.get(db.Message, comentario_id)
        if not m or m.conversation_id != conv_id or "design_comentario" not in (m.meta or {}):
            raise ToolError("Comentário não encontrado.")
        m.meta = {**m.meta, "design_comentario": {**m.meta["design_comentario"], "status": "descartado"}}
        s.get(db.Conversation, conv_id).updated_at = db._now()
        s.commit()
    return projeto(conv_id)


def _marca_comentarios(conv_id: int, ids: list[int], versao: int) -> None:
    with db.session() as s:
        for cid in ids:
            m = s.get(db.Message, cid)
            if m and m.conversation_id == conv_id and "design_comentario" in (m.meta or {}):
                m.meta = {**m.meta, "design_comentario": {**m.meta["design_comentario"], "status": "aplicado",
                                                          "versao_aplicada": versao}}
        s.commit()


def editar_texto(conv_id: int, fid: str, interno: str) -> dict:
    """Duplo clique no canvas: o texto novo entra direto na fonte, sem chamar o modelo."""
    with db.session() as s:
        _conv(s, conv_id)
        if any(r["conv_id"] == conv_id for r in _RUNS.values()):
            raise ToolError("Espere a geração em andamento terminar.")
        atual = _atual(s, conv_id)
        if not atual:
            raise ToolError("Não há documento.")
        html = _carimbada(s, atual)
        base = atual.meta["design"]["versao"]
    e = design_html.por_fid(design_html.indexar(html), fid)
    if not e or e["fim"] == e["fim_tag"] or e["tag"] in design_html.SEM_FID | {"style"}:
        raise ToolError("Esse elemento não tem texto editável.")
    interno = re.sub(r"""\s(contenteditable|spellcheck)(\s*=\s*("[^"]*"|'[^']*'|[^\s>]+))?""", "", interno or "")
    if re.search(r"<\s*/?\s*(script|style|html|head|body|iframe)\b", interno, re.I):
        raise ToolError("O texto editado tem tag que não pode entrar ali.")
    abre = re.findall(r"<([a-zA-Z][\w-]*)[^>]*?(?<!/)>", interno)
    fechadas = re.findall(r"</([a-zA-Z][\w-]*)\s*>", interno)
    for tag in {t.lower() for t in abre + fechadas} - design_html.VOID:   # o índice fecharia sozinho
        if sum(t.lower() == tag for t in abre) != sum(t.lower() == tag for t in fechadas):
            raise ToolError("O texto editado tem tag aberta sem fechar — nada mudou.")
    fecha = html.rfind("<", e["fim_tag"], e["fim"])
    novo = html[:e["fim_tag"]] + interno + html[fecha:]
    depois = design_html.por_fid(design_html.indexar(novo), fid)
    if not extrair_html(novo) or not depois or depois["fim"] != e["fim"] + len(novo) - len(html):
        raise ToolError("O texto editado deixaria o HTML quebrado — nada mudou.")
    trecho = " ".join(re.sub(r"<[^>]+>", " ", interno).split())
    _nova_versao(conv_id, None, novo, f"texto editado: {_descricao(trecho, 40)}", base=base,
                 patches=[fid], rota="texto")
    p = projeto(conv_id)   # o HTML salvo: carimbar de novo aqui sortearia outros ids
    return {"projeto": p, "fim": {"base": base, "patches": [{"fid": fid, "html": design_html.outer(p["html"], fid) or ""}]}}


def _sem_ia(conv_id: int, faz, descricao: str, rota: str) -> dict:
    """Mudança direta no documento (sliders, design system): versão nova, sem modelo, com patch."""
    with db.session() as s:
        _conv(s, conv_id)
        if any(r["conv_id"] == conv_id for r in _RUNS.values()):
            raise ToolError("Espere a geração em andamento terminar.")
        atual = _atual(s, conv_id)
        if not atual:
            raise ToolError("Não há documento.")
        html = _carimbada(s, atual)
        base = atual.meta["design"]["versao"]
    try:
        novo, mudou = faz(html)
    except ValueError as e:
        raise ToolError(str(e)) from e
    if novo == html:
        return {"projeto": projeto(conv_id), "fim": None}
    _nova_versao(conv_id, None, novo, descricao, base=base, patches=mudou, rota=rota)
    p = projeto(conv_id)
    return {"projeto": p, "fim": {"base": base, "status": "ok",
                                  "patches": [{"fid": f, "html": design_html.outer(p["html"], f) or ""} for f in mudou]}}


def ajustar_tokens(conv_id: int, tokens: dict) -> dict:
    """Painel de ajustes: só tokens que já existem no :root; o resto da página acompanha sozinho."""
    if not isinstance(tokens, dict) or not tokens:
        raise ToolError("Nenhum token para ajustar.")

    def faz(html: str):
        raiz = design_html.root_css(html)
        desconhecidos = [k for k in tokens if not re.search(rf"{re.escape(k)}\s*:", raiz)]
        if desconhecidos:
            raise ValueError(f"Token que não existe no :root: {', '.join(desconhecidos)}")
        return design_html.aplicar(html, {"tokens": tokens})
    nomes = ", ".join(k.removeprefix("--") for k in tokens)
    return _sem_ia(conv_id, faz, f"ajuste: {_descricao(nomes, 48)}", "ajuste")


def aplicar_sistema(conv_id: int, ds_id: str) -> dict:
    ds = design_sistema.pegar(ds_id)

    def faz(html: str):
        novo = design_sistema.aplicar(html, ds)
        return novo, []   # tokens, CSS e <meta>: o canvas recarrega
    return _sem_ia(conv_id, faz, f"design system: {ds['nome']}", "sistema")


# ------------------------------------------------------------------ geração

def _descricao(pedido: str, n: int = 60) -> str:
    linha = " ".join(pedido.split())
    return linha if len(linha) <= n else linha[:n - 3].rstrip() + "…"


def _spec(modelos: dict, modo: str) -> dict:
    spec = modelos.get(ETAPA[modo]) or {}
    if not (spec.get("provider") and spec.get("model")):
        raise ToolError(f"Escolha o modelo de {ETAPA[modo]} antes de continuar.")
    return {"provider": spec["provider"], "model": spec["model"]}


def validar_plano(d: dict) -> dict:
    if not isinstance(d, dict):
        raise ValueError("o plano não é um objeto")
    tokens = {}
    brutos = d.get("tokens") if isinstance(d.get("tokens"), dict) else {}
    for k, v in brutos.items():
        nome, valor = str(k).strip(), str(v).strip().rstrip(";")
        nome = nome if nome.startswith("--") else f"--{nome}"
        if design_html.token_valido(nome, valor):
            tokens[nome] = valor
    secoes, vistos = [], set()
    for sec in d.get("secoes") if isinstance(d.get("secoes"), list) else []:
        if not isinstance(sec, dict) or not (nome := design_html.slug(sec.get("nome"))) or nome in vistos:
            continue
        vistos.add(nome)
        secoes.append({"nome": nome, "objetivo": str(sec.get("objetivo") or "")[:400],
                       "conteudo": str(sec.get("conteudo") or "")[:2000]})
    if not secoes:
        raise ValueError("o plano não tem nenhuma seção")
    if len(secoes) > 12:
        raise ValueError("o plano tem seções demais (máximo 12)")
    return {"tipo": "slides" if d.get("tipo") == "slides" else "site",
            "titulo": str(d.get("titulo") or "Sem título").strip()[:120], "tokens": tokens, "secoes": secoes,
            "estilo_imagens": re.sub(r'["<>]', "", str(d.get("estilo_imagens") or ""))[:300],
            "sistema": str(d.get("sistema") or "")[:40]}


def _ctx_secao(titulo: str, html: str, nomes: list[str], sec: dict, atual: str | None, pedido: str | None) -> str:
    partes = [f"Página: {titulo}",
              f"Tokens (:root):\n{design_html.root_css(html) or '(nenhum)'}",
              "Classes base já existentes: .container (largura máxima centralizada, com respiro lateral).",
              f"Seções da página, em ordem: {', '.join(nomes)}",
              f'Seção a escrever: data-section="{sec["nome"]}"']
    if sec.get("objetivo"):
        partes.append(f"Objetivo: {sec['objetivo']}")
    if sec.get("conteudo"):
        partes.append(f"Conteúdo: {sec['conteudo']}")
    if atual:
        partes.append(f"HTML atual da seção (refaça a partir dele; mude o que o pedido pede):\n{atual}")
    if pedido:
        partes.append(f"Pedido: {pedido}")
    return "\n\n".join(partes)


def _novo_run(conv_id: int, message_id: int, modo: str, spec: dict, esforco: str, **extra) -> dict:
    run = _RUNS[message_id] = {"conv_id": conv_id, "message_id": message_id, "modo": modo, "spec": spec,
                               "esforco": esforco, "parcial": "", "raciocinio": "", "cancelar": False,
                               "t0": time.monotonic(), "vivos": 0, "stats": [], **extra}
    return run


def _dispara(coro) -> None:
    t = asyncio.create_task(coro)
    _TAREFAS.add(t)
    t.add_done_callback(_TAREFAS.discard)


def start(conv_id: int, pedido: str, modelos: dict, fids: list[str] | None = None, rota: str = "auto",
          secao: str = "", comentarios: list[int] | None = None, esforco: str = "baixo", ds_id: str = "") -> dict:
    pedido = (pedido or "").strip()
    if rota not in ROTAS:
        raise ToolError(f"rota deve ser {', '.join(ROTAS)}.")
    esforco = esforco if esforco in ESFORCOS else "baixo"
    with db.session() as s:
        c = _conv(s, conv_id)
        if any(r["conv_id"] == conv_id for r in _RUNS.values()):
            raise ToolError("Já tem uma geração rodando neste projeto.")
        atual = _atual(s, conv_id)
        html_base = _carimbada(s, atual) if atual else ""
        base = atual.meta["design"]["versao"] if atual else 0
        pend = []
        if comentarios:   # "aplicar agora" / "aplicar pendentes": vira edição de fragmento
            pend = [x for x in _comentarios(s, conv_id, html_base)
                    if x["id"] in comentarios and x["status"] == "pendente" and not x["orfao"]]
            if not pend:
                raise ToolError("Nenhum comentário pendente aplicável (os órfãos ficam de fora).")
        if not pedido and not pend and rota != "secao":
            raise ToolError("Descreva o design.")
        if c.title == "Nova conversa" and pedido:
            c.title = _descricao(pedido)
        s.commit()

    if pend:
        fids = [f for x in pend for f in x["fids"]]
        linhas = "\n".join(f"{i}. (elementos {', '.join(x['fids'])}) {x['texto']}" for i, x in enumerate(pend, 1))
        pedido = f"Aplique estes comentários, cada um nos elementos indicados:\n{linhas}" + (f"\n\nAlém disso: {pedido}" if pedido else "")
    fids = [f for f in dict.fromkeys(fids or []) if f]
    nomes = [e["attrs"]["data-section"] for e in design_html.secoes(html_base)] if html_base else []
    if rota == "auto":
        rota, alvo = rotear(pedido, bool(html_base), nomes, fids)
    else:   # rota forçada; na de seção, o nome ainda sai do texto se não veio
        alvo = rotear(pedido, bool(html_base), nomes)[1] if rota == "secao" else None
    modo = "fragmento" if fids else rota
    if modo not in ("plano", "documento") and not html_base:
        raise ToolError("Não há documento ainda: o primeiro pedido gera o plano.")
    spec = _spec(modelos, modo)
    extra: dict = {}

    if modo == "fragmento":
        try:
            fids = design_html.cobertura(html_base, fids)
            user = f"{design_html.contexto(html_base, fids)}\n\nPedido: {pedido}"
        except ValueError as e:
            raise ToolError(str(e)) from e
        sistema = "fragmento"
    elif modo == "tokens":
        user = f"Bloco :root atual:\n{design_html.root_css(html_base) or '(nenhum)'}\n\nPedido: {pedido}"
        sistema = "tokens"
    elif modo == "secao":
        nome = design_html.slug(secao or alvo or "") or "nova"
        existente = next((e for e in design_html.secoes(html_base) if e["attrs"]["data-section"] == nome), None)
        nova = existente is None
        if nova:
            html_base = design_html.inserir_secao(html_base, nome)
            nomes = [e["attrs"]["data-section"] for e in design_html.secoes(html_base)]
        alvo_fid = design_html.placeholder(html_base, nome) if nova else existente["attrs"]["data-fid"]
        atual_sec = None if nova else design_html.outer(html_base, alvo_fid)
        with db.session() as s:
            titulo = s.get(db.Conversation, conv_id).title
        user = _ctx_secao(titulo, html_base, nomes, {"nome": nome}, atual_sec,
                          pedido or f"Refaça a seção {nome} com um design melhor.")
        slide = design_html.e_slides(html_base)
        sistema = "slide" if slide else "secao"
        extra = {"secao": nome, "alvo": alvo_fid, "nova": nova, "slide": slide}
    elif modo == "documento" and not html_base:   # forçado num projeto vazio: tudo de uma vez
        user = f"Pedido: {pedido}"
        sistema = "documento"
    elif modo == "documento":
        user = (f"Documento atual (v{base}):\n\n{html_base}\n\nPedido de mudança: {pedido}\n\n"
                "Devolva o documento inteiro já com a mudança, mexendo só no que o pedido pede.")
        sistema = "documento"
    else:  # plano
        user = f"Pedido: {pedido}"
        sistema = "plano"
    # design system: o escolhido na tela vale para o plano; depois, o que o documento carrega
    ds = None
    if modo == "plano" and ds_id:
        ds = design_sistema.pegar(ds_id)
    elif html_base and modo in ("secao", "documento"):
        ds = design_sistema.do_documento(html_base)
    if ds:
        user += "\n\n" + design_sistema.para_prompt(ds)
    user = design_imagens.enxugar(user)   # foto embutida é base64 de centenas de KB: não vai ao modelo
    mensagens = [{"role": "system", "content": prompt(sistema)}, {"role": "user", "content": user}]

    rotulo = f"{len(pend)} comentário(s)" if pend else pedido or f"refazer a seção {extra.get('secao')}"
    _save(conv_id, role="user", content=rotulo,
          meta={"design": {"fids": fids, "rota": modo, "comentarios": [x["id"] for x in pend]}})
    msg = _save(conv_id, role="assistant", content="", status="running",
                meta={"design": {"base": base, "fids": fids, "entrada": len(user), "rota": modo,
                                 "secao": extra.get("secao"), "comentarios": [x["id"] for x in pend]}})
    run = _novo_run(conv_id, msg.id, modo, spec, esforco, base=base, html_base=html_base,
                    entrada=len(user), descricao=_descricao(rotulo),
                    comentarios=[x["id"] for x in pend], ds_id=ds["id"] if ds and modo == "plano" else "", **extra)
    _dispara(_rodar(run, mensagens))
    return msg.to_dict()


async def _chamar(run: dict, mensagens: list[dict]) -> str:
    """Uma chamada ao modelo, em streaming. Guarda raciocínio e estatísticas no run (formato do agente)."""
    spec = run["spec"]
    content, reasoning, done, t0, t_first = "", "", {}, time.monotonic(), 0.0
    run["parcial"] = ""
    async with aclosing(llm.chat_stream(spec["provider"], spec["model"], mensagens, None, config.NUM_CTX,
                                        run["esforco"])) as fluxo:
        async for kind, val in fluxo:
            if run["cancelar"]:
                break
            if kind == "content":
                t_first = t_first or time.monotonic()
                content += val
                run["parcial"] = content
                run["vivos"] += 1
            elif kind == "reasoning":
                t_first = t_first or time.monotonic()
                reasoning += val
                run["raciocinio"] += val
                run["vivos"] += 1
            elif kind == "done":
                done = val or {}
    pensou, texto = split_think(content)
    if pensou:
        run["raciocinio"] += pensou
    run["raciocinio"] += "\n\n"
    run["stats"].append(_stats(mensagens, None, content, reasoning, done, t0, t_first, None, spec["model"]))
    return texto


async def _rodar(run: dict, mensagens: list[dict]) -> None:
    mid, modo = run["message_id"], run["modo"]
    extra = {"base": run["base"], "entrada": run["entrada"], "rota": modo}
    try:
        texto = await _chamar(run, mensagens)
        if run["cancelar"]:
            _fecha(mid, "cancelado", "Cancelado — o documento não mudou.")
            return
        try:
            if modo == "plano":
                plano = validar_plano(design_html.ler_json(texto))
                if run.get("ds_id"):   # o sistema manda: tokens dele por cima dos que o modelo inventou
                    plano = {**plano, "tokens": {**plano["tokens"], **design_sistema.pegar(run["ds_id"])["tokens"]},
                             "sistema": run["ds_id"]}
                with db.session() as s:
                    m = s.get(db.Message, mid)
                    m.status, m.content = "plano", f"Plano: {plano['titulo']} · {len(plano['secoes'])} seções"
                    m.meta = {**m.meta, "design": {**m.meta["design"], "plano": plano}}
                    s.commit()
                return
            if modo == "documento":
                html = extrair_html(texto)
                if not html:
                    raise ValueError("a resposta não trouxe um documento HTML completo")
                _nova_versao(run["conv_id"], mid, html, run["descricao"], **extra)
                return
            if modo == "fragmento":
                resp = design_html.ler_json(texto)
            elif modo == "tokens":
                d = design_html.ler_json(texto)
                tokens = d.get("tokens") if isinstance(d.get("tokens"), dict) else d
                raiz = design_html.root_css(run["html_base"])
                tokens = {k: v for k, v in tokens.items() if isinstance(k, str) and re.search(rf"{re.escape(k)}\s*:", raiz)}
                if not tokens:
                    raise ValueError("nenhum token existente foi alterado")
                resp = {"tokens": tokens}   # só :root, venha o que vier
            else:  # secao
                sec, css = design_html.ler_secao(texto, run["secao"], run.get("slide", False))
                resp = {"patches": [{"fid": run["alvo"], "html": sec}], "css": css}
            html, mudou = design_html.aplicar(run["html_base"], resp)
            if not extrair_html(html):
                raise ValueError("o documento ficaria inválido")
        except ValueError as e:
            _fecha(mid, "erro", f"Edição recusada: {e} — o documento não mudou.")
            return
        # seção nova não existe no canvas: sem patches, a tela recarrega o documento
        n = _nova_versao(run["conv_id"], mid, html, run["descricao"], patches=[] if run.get("nova") else mudou, **extra)
        if run.get("comentarios"):
            _marca_comentarios(run["conv_id"], run["comentarios"], n)
    except Exception as e:   # erro do modelo não pode deixar a mensagem em "running"
        _fecha(mid, "erro", f"Erro do modelo: {e} — o documento não mudou.")
    finally:
        _anexa(run)
        _RUNS.pop(mid, None)
        mirror.write(run["conv_id"])


def aprovar(message_id: int, plano: dict, modelos: dict, esforco: str = "baixo") -> dict:
    """Plano aprovado (talvez editado no card): esqueleto e depois uma seção por vez."""
    try:
        plano = validar_plano(plano)
    except ValueError as e:
        raise ToolError(f"Plano inválido: {e}") from e
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or m.status != "plano":
            raise ToolError("Esse plano não está esperando aprovação.")
        conv_id = m.conversation_id
        _conv(s, conv_id)
        if any(r["conv_id"] == conv_id for r in _RUNS.values()):
            raise ToolError("Já tem uma geração rodando neste projeto.")
        atual = _atual(s, conv_id)
        base = atual.meta["design"]["versao"] if atual else 0
        spec = _spec(modelos, "etapas")
        m.status, m.content = "running", ""
        m.meta = {**m.meta, "design": {**m.meta["design"], "plano": plano, "rota": "etapas", "base": base}}
        s.commit()
    doc = design_html.esqueleto(plano)
    if plano.get("sistema"):
        try:
            doc = design_sistema.aplicar(doc, design_sistema.pegar(plano["sistema"]))
        except ToolError:
            pass   # o sistema foi apagado entre o plano e a aprovação: segue com os tokens do plano
    doc = design_html.carimbar(doc)
    run = _novo_run(conv_id, message_id, "etapas", spec, esforco if esforco in ESFORCOS else "baixo",
                    base=base, plano=plano, doc=doc, n=0, entrada=0,
                    secoes=[{"nome": x["nome"], "status": "fila"} for x in plano["secoes"]])
    # o raciocínio/estatísticas do plano (já gravados) continuam na mensagem
    with db.session() as s:
        m = s.get(db.Message, message_id)
        run["raciocinio"] = (m.thinking + "\n\n") if m.thinking else ""
        run["stats"] = list((m.meta or {}).get("design", {}).get("stats") or [])
    _dispara(_rodar_etapas(run))
    return {"id": message_id}


async def _rodar_etapas(run: dict) -> None:
    mid, plano = run["message_id"], run["plano"]
    nomes = [x["nome"] for x in plano["secoes"]]
    slides = plano.get("tipo") == "slides"
    erros = []
    try:
        for i, sec in enumerate(plano["secoes"]):
            if run["cancelar"]:
                break
            run["secoes"][i]["status"] = "gerando"
            try:
                alvo = design_html.placeholder(run["doc"], sec["nome"])
                user = _ctx_secao(plano["titulo"], run["doc"], nomes, sec, None, None)
                if ds := design_sistema.do_documento(run["doc"]):
                    user += "\n\n" + design_sistema.para_prompt(ds)
                user = design_imagens.enxugar(user)
                run["entrada"] += len(user)
                texto = await _chamar(run, [{"role": "system", "content": prompt("slide" if slides else "secao")},
                                            {"role": "user", "content": user}])
                if run["cancelar"]:
                    run["secoes"][i]["status"] = "fila"
                    break
                html_sec, css = design_html.ler_secao(texto, sec["nome"], slides)
                run["doc"], _ = design_html.aplicar(run["doc"], {"patches": [{"fid": alvo, "html": html_sec}], "css": css})
                run["secoes"][i]["status"] = "ok"
                run["n"] += 1
            except ValueError as e:   # uma seção ruim não derruba as outras
                run["secoes"][i]["status"] = "erro"
                erros.append(f"{sec['nome']}: {e}")
        feitas, total = run["n"], len(plano["secoes"])
        doc = design_html.limpar_placeholders(run["doc"])
        if feitas and extrair_html(doc):
            sufixo = "" if feitas == total else f" ({feitas} de {total} seções{' — cancelado' if run['cancelar'] else ''})"
            _nova_versao(run["conv_id"], mid, doc, plano["titulo"] + sufixo, base=run["base"], rota="etapas",
                         entrada=run["entrada"], avisos=erros)
        else:
            _fecha(mid, "cancelado" if run["cancelar"] else "erro",
                   ("Cancelado" if run["cancelar"] else f"Nenhuma seção gerada ({'; '.join(erros)})") + " — o documento não mudou.")
    except Exception as e:
        _fecha(mid, "erro", f"Erro do modelo: {e} — o documento não mudou.")
    finally:
        _anexa(run)
        _RUNS.pop(mid, None)
        mirror.write(run["conv_id"])


def _stats_vivo(run: dict) -> dict:
    """Linha de estatísticas enquanto gera: o que já fechou + a chamada em curso (contagem aproximada)."""
    fechados = sum(s["tokens"] for s in run["stats"])
    return {"model": run["spec"]["model"], "tokens": max(fechados, run["vivos"]), "seconds": round(time.monotonic() - run["t0"], 1),
            "tps": None, "estimated": True}


def estado(message_id: int) -> dict:
    if run := _RUNS.get(message_id):
        out = {"message_id": message_id, "status": "rodando", "modo": run["modo"],
               "parcial": run["parcial"] if run["modo"] == "documento" else "",
               "raciocinio": run["raciocinio"], "tokens": run["vivos"],
               "segundos": round(time.monotonic() - run["t0"], 1), "vivo": _stats_vivo(run)}
        if run["modo"] == "etapas":
            out.update(secoes=run["secoes"], n=run["n"], doc=run["doc"])
        return out
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or "design" not in (m.meta or {}):
            raise ToolError("Geração não encontrada.")
        d = m.meta["design"]
        # patches com o outerHTML já carimbado: o canvas troca só esses nós, sem recarregar
        patches = [{"fid": f, "html": design_html.outer(d["html"], f) or ""} for f in d.get("patches", [])]
        return {"message_id": message_id, "status": m.status or "ok", "texto": m.content, "rota": d.get("rota"),
                "versao": d.get("versao"), "base": d.get("base"), "entrada": d.get("entrada"), "patches": patches,
                "plano": d.get("plano") if m.status == "plano" else None}


def cancelar(message_id: int) -> dict:
    if run := _RUNS.get(message_id):
        run["cancelar"] = True
    return {"ok": True}

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
ROTAS = ("auto", "plano", "tokens", "secao", "documento", "tweaks", "variacoes")
ESFORCOS = ("baixo", "medio", "alto", "maximo")
# qual dos três modelos da tela cada rota usa: plano, geração (seções/documento) ou edição
ETAPA = {"plano": "plano", "perguntas": "plano", "etapas": "geracao", "secao": "geracao", "documento": "geracao",
         "fragmento": "edicao", "tokens": "edicao", "tweaks": "edicao", "variacoes": "edicao"}
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
                              "perguntas": d.get("perguntas") if m.status == "perguntas" else None,
                              "variacoes": d.get("variacoes") if m.status in ("variacoes", "ok") and d.get("variacoes") else None,
                              "escolhida": d.get("escolhida"),
                              "respostas": d.get("respostas"), "referencias": d.get("referencias") or [],
                              "mensagem": d.get("mensagem") or "", "sugestoes": d.get("sugestoes") or [],
                              "passos": d.get("passos") or [], "mais": (d.get("diff") or {}).get("mais", 0),
                              "menos": (d.get("diff") or {}).get("menos", 0),
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
    return {"total": len(sl), "pendentes": len(pend), "nomes": pend, "disponivel": design_imagens.DISPONIVEL,
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
    _nova_versao(conv_id, None, html, f"restaurada da v{versao}", rota="restaurar",
                 passos=[f"Copiou a v{versao} para o topo do histórico"])
    return projeto(conv_id)


def _nova_versao(conv_id: int, message_id: int | None, html: str, descricao: str, **extra) -> int:
    """Grava a versão (na mensagem da geração, ou numa nova) e a marca como atual. Os slots de imagem
    saem preenchidos: provisório onde falta, a imagem pronta de antes onde o modelo devolveu sem src."""
    with db.session() as s:
        anterior = _atual(s, conv_id)
        anterior = anterior.meta["design"]["html"] if anterior else ""
    html = design_html.carimbar(design_imagens.preencher(html, anterior))
    extra["diff"] = design_html.diff(anterior, html)   # "código alterado" da atividade no chat
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


def atividade(message_id: int) -> dict:
    """O diff de uma versão (fica fora do projeto(): são dezenas de KB por versão)."""
    with db.session() as s:
        m = s.get(db.Message, message_id)
        d = (m.meta or {}).get("design", {}) if m else {}
        return {"diff": d.get("diff") or {"texto": "", "mais": 0, "menos": 0}, "passos": d.get("passos") or []}


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
        passos = [*run.get("passos_extra", []), *(d.get("passos") or [])]
        m.meta = {**(m.meta or {}), "design": {**d, "stats": run["stats"], "passos": passos}}
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
                 patches=[fid], rota="texto", passos=[f"Editou o texto de <{e['tag']}> direto no canvas (sem IA)"])
    p = projeto(conv_id)   # o HTML salvo: carimbar de novo aqui sortearia outros ids
    return {"projeto": p, "fim": {"base": base, "patches": [{"fid": fid, "html": design_html.outer(p["html"], fid) or ""}]}}


def _sem_ia(conv_id: int, faz, descricao: str, rota: str, passos: list[str] | None = None) -> dict:
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
    _nova_versao(conv_id, None, novo, descricao, base=base, patches=mudou, rota=rota, passos=passos or [])
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
    return _sem_ia(conv_id, faz, f"ajuste: {_descricao(nomes, 48)}", "ajuste",
                   [f"{k} → {v} (painel de ajustes, sem IA)" for k, v in tokens.items()])


def escolher_variacao(conv_id: int, message_id: int, indice: int) -> dict:
    """Variação escolhida no card: vira versão (só tokens, sem IA) e o card lembra qual foi."""
    with db.session() as s:
        m = s.get(db.Message, message_id)
        vs = ((m.meta or {}).get("design") or {}).get("variacoes") if m and m.conversation_id == conv_id else None
        if not vs or not 0 <= indice < len(vs):
            raise ToolError("Variação não encontrada.")
        v = vs[indice]
        m.meta = {**m.meta, "design": {**m.meta["design"], "escolhida": indice}}
        s.commit()

    def faz(html: str):
        return design_html.aplicar(html, {"tokens": v["tokens"]})
    return _sem_ia(conv_id, faz, f"variação: {v['nome']}", "variacao",
                   [f"Aplicou a variação “{v['nome']}”: " + ", ".join(f"{k} → {x}" for k, x in v["tokens"].items())])


def versao_html(conv_id: int, versao: int) -> dict:
    """HTML de uma versão qualquer (miniaturas do histórico e comparação lado a lado)."""
    with db.session() as s:
        _conv(s, conv_id)
        m = next((x for x in _versoes(s, conv_id) if x.meta["design"]["versao"] == versao), None)
        if not m:
            raise ToolError(f"Versão {versao} não existe.")
        return {"versao": versao, "html": _carimbada(s, m), "descricao": m.meta["design"].get("descricao", "")}


def aplicar_sistema(conv_id: int, ds_id: str) -> dict:
    ds = design_sistema.pegar(ds_id)

    def faz(html: str):
        novo = design_sistema.aplicar(html, ds)
        return novo, []   # tokens, CSS e <meta>: o canvas recarrega
    return _sem_ia(conv_id, faz, f"design system: {ds['nome']}", "sistema",
                   [f"Aplicou os {len(ds['tokens'])} tokens do design system “{ds['nome']}”",
                    *(["Acrescentou o CSS dos componentes dele"] if ds.get("css") else [])])


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
    return {"tipo": d.get("tipo") if d.get("tipo") in ("slides", "prototipo") else "site",
            "titulo": str(d.get("titulo") or "Sem título").strip()[:120], "tokens": tokens, "secoes": secoes,
            "estilo_imagens": re.sub(r'["<>]', "", str(d.get("estilo_imagens") or ""))[:300],
            "sistema": str(d.get("sistema") or "")[:40]}


def _css(html: str) -> str:
    return "\n".join(m.group(1) for m in re.finditer(r"<style[^>]*>(.*?)</style\s*>", html, re.S | re.I))


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


def _referencias(refs: list[dict] | None) -> tuple[str, list[str], list[dict]]:
    """(texto que vai no pedido, data URIs de imagem para o modelo, o que fica no chat).
    Documento e página capturada viram texto; imagem vai como parte de imagem (modelo com visão)."""
    textos, imagens, chat = [], [], []
    for r in (refs or [])[:6]:
        tipo, nome = r.get("tipo"), str(r.get("nome") or "referência")[:120]
        if tipo == "imagem" and str(r.get("data", "")).startswith("data:image/"):
            imagens.append(r["data"])
            chat.append({"tipo": "imagem", "nome": nome, "data": r["data"] if len(r["data"]) < 400_000 else ""})
        elif tipo in ("documento", "pagina") and r.get("texto"):
            textos.append(f"--- {'Página' if tipo == 'pagina' else 'Documento'} de referência: {nome} ---\n{str(r['texto'])[:12_000]}")
            chat.append({"tipo": tipo, "nome": nome})
    bloco = ("Referências que o usuário anexou (use como base de conteúdo e estilo):\n" + "\n\n".join(textos)) if textos else ""
    if imagens:
        bloco += ("\n\n" if bloco else "") + f"{len(imagens)} imagem(ns) de referência anexada(s): siga o estilo visual delas."
    return bloco, imagens, chat


def start(conv_id: int, pedido: str, modelos: dict, fids: list[str] | None = None, rota: str = "auto",
          secao: str = "", comentarios: list[int] | None = None, esforco: str = "baixo", ds_id: str = "",
          perguntar: bool = False, respostas: list[dict] | None = None, referencias: list[dict] | None = None) -> dict:
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
        if not pedido and not pend and rota not in ("secao", "tweaks", "variacoes"):
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
    modo = rota if rota in ("tweaks", "variacoes") else "fragmento" if fids else rota
    if modo == "plano" and perguntar and not respostas:   # Claude Design pergunta antes de desenhar
        modo = "perguntas"
    if modo not in ("plano", "perguntas", "documento") and not html_base:
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
    elif modo == "variacoes":
        with db.session() as s:
            titulo = s.get(db.Conversation, conv_id).title
        user = (f"Projeto: {titulo}\nSeções: {', '.join(nomes)}\n\nBloco :root atual:\n{design_html.root_css(html_base)}\n\n"
                f"Pedido: {pedido or 'Proponha 3 direções visuais diferentes para esta página.'}")
        sistema = "variacoes"
    elif modo == "tweaks":
        foco = design_html.contexto(html_base, design_html.cobertura(html_base, fids)) if fids else (
            f"Tokens (bloco :root):\n{design_html.root_css(html_base)}\n\nSeções da página: {', '.join(nomes)}\n\n"
            f"CSS atual (início):\n{design_imagens.enxugar(_css(html_base))[:6000]}")
        user = f"{foco}\n\nPedido: {pedido or 'Crie os ajustes mais úteis para esta página.'}"
        sistema = "ajustes"
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
        slide, tela = design_html.e_slides(html_base), design_html.e_prototipo(html_base)
        sistema = "slide" if slide else "tela" if tela else "secao"
        extra = {"secao": nome, "alvo": alvo_fid, "nova": nova, "slide": slide, "tela": tela}
    elif modo == "documento" and not html_base:   # forçado num projeto vazio: tudo de uma vez
        user = f"Pedido: {pedido}"
        sistema = "documento"
    elif modo == "documento":
        user = (f"Documento atual (v{base}):\n\n{html_base}\n\nPedido de mudança: {pedido}\n\n"
                "Devolva o documento inteiro já com a mudança, mexendo só no que o pedido pede.")
        sistema = "documento"
    elif modo == "perguntas":
        user = f"Pedido: {pedido}"
        sistema = "perguntas"
    else:  # plano
        resp = "\n".join(f"- {r.get('pergunta')}: {r.get('resposta')}" for r in respostas or [] if r.get("resposta"))
        user = f"Pedido: {pedido}" + (f"\n\nRespostas do usuário às perguntas:\n{resp}" if resp else "")
        sistema = "plano"
    bloco_refs, imagens_ref, refs_chat = _referencias(referencias)
    if bloco_refs:
        user += "\n\n" + bloco_refs
    # design system: o escolhido na tela vale para o plano; depois, o que o documento carrega
    ds = None
    if modo == "plano" and ds_id:
        ds = design_sistema.pegar(ds_id)
    elif html_base and modo in ("secao", "documento"):
        ds = design_sistema.do_documento(html_base)
    if ds:
        user += "\n\n" + design_sistema.para_prompt(ds)
    user = design_imagens.enxugar(user)   # foto embutida é base64 de centenas de KB: não vai ao modelo
    conteudo = user if not imagens_ref else [{"type": "text", "text": user},
                                             *({"type": "image_url", "image_url": {"url": u}} for u in imagens_ref)]
    mensagens = [{"role": "system", "content": prompt(sistema)}, {"role": "user", "content": conteudo}]

    rotulo = (f"{len(pend)} comentário(s)" if pend else pedido or
              {"tweaks": "criar ajustes", "variacoes": "3 variações"}.get(modo) or f"refazer a seção {extra.get('secao')}")
    _save(conv_id, role="user", content=rotulo,
          meta={"design": {"fids": fids, "rota": modo, "comentarios": [x["id"] for x in pend],
                           "respostas": respostas or None, "referencias": refs_chat}})
    msg = _save(conv_id, role="assistant", content="", status="running",
                meta={"design": {"base": base, "fids": fids, "entrada": len(user), "rota": modo,
                                 "secao": extra.get("secao"), "comentarios": [x["id"] for x in pend]}})
    run = _novo_run(conv_id, msg.id, modo, spec, esforco, base=base, html_base=html_base,
                    entrada=len(user), descricao=_descricao(rotulo),
                    comentarios=[x["id"] for x in pend], ds_id=ds["id"] if ds and modo == "plano" else "",
                    pedido=pedido, **extra)
    _dispara(_rodar(run, mensagens))
    return msg.to_dict()


def _perguntas(d: dict) -> list[dict]:
    """2 a 4 perguntas curtas, cada uma com até 6 opções clicáveis (e sempre o campo livre na tela)."""
    out = []
    for q in d.get("perguntas") if isinstance(d.get("perguntas"), list) else []:
        texto = str((q or {}).get("pergunta") if isinstance(q, dict) else q or "").strip()[:200]
        if not texto:
            continue
        opcoes = [str(o).strip()[:60] for o in (q.get("opcoes") if isinstance(q, dict) and isinstance(q.get("opcoes"), list) else [])
                  if str(o).strip()][:6]
        out.append({"pergunta": texto, "opcoes": opcoes, "multipla": bool(isinstance(q, dict) and q.get("multipla"))})
    return out[:4]


def _guarda(message_id: int, status: str, texto: str, **design_extra) -> None:
    """Resposta sem versão (plano, perguntas): status e conteúdo novos, o resto no meta de design."""
    with db.session() as s:
        m = s.get(db.Message, message_id)
        m.status, m.content = status, texto
        m.meta = {**m.meta, "design": {**m.meta["design"], **design_extra}}
        s.commit()


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


def _sem_imagens(mensagens: list[dict]) -> list[dict] | None:
    """A mesma conversa sem as partes de imagem (None se não havia imagem)."""
    conteudo = mensagens[1]["content"]
    if not isinstance(conteudo, list):
        return None
    texto = next((p["text"] for p in conteudo if p.get("type") == "text"), "")
    return [mensagens[0], {"role": "user", "content": texto + "\n\n(As imagens de referência não foram enviadas: "
                                                          "este modelo não lê imagem. Use só o texto acima.)"}]


async def _chamar_com_referencias(run: dict, mensagens: list[dict]) -> str:
    """Modelo sem visão recusa a imagem de referência (HTTP 400): refaz só com o texto e avisa."""
    try:
        return await _chamar(run, mensagens)
    except llm.LLMError as e:
        so_texto = _sem_imagens(mensagens)
        if so_texto is None or "image" not in str(e).lower():
            raise
        run["passos_extra"] = [f"O modelo {run['spec']['model']} não lê imagem: seguiu só com o texto das referências"]
        mensagens[1] = so_texto[1]
        return await _chamar(run, so_texto)


async def _rodar(run: dict, mensagens: list[dict]) -> None:
    mid, modo = run["message_id"], run["modo"]
    extra = {"base": run["base"], "entrada": run["entrada"], "rota": modo}
    try:
        texto = await _chamar_com_referencias(run, mensagens)
        if run["cancelar"]:
            _fecha(mid, "cancelado", "Cancelado — o documento não mudou.")
            return
        base_html = run["html_base"]
        try:
            if modo == "variacoes":
                d = design_html.ler_json(texto)
                raiz = design_html.root_css(base_html)
                variacoes = []
                for v in d.get("variacoes") if isinstance(d.get("variacoes"), list) else []:
                    toks = {k: str(x).strip().rstrip(";") for k, x in (v.get("tokens") or {}).items()
                            if isinstance(k, str) and re.search(rf"{re.escape(k)}\s*:", raiz)
                            and design_html.token_valido(k, str(x).strip().rstrip(";"))} if isinstance(v, dict) else {}
                    if toks:
                        variacoes.append({"nome": str(v.get("nome") or f"Variação {len(variacoes) + 1}")[:40],
                                          "descricao": str(v.get("descricao") or "")[:200], "tokens": toks})
                if not variacoes:
                    raise ValueError("o modelo não propôs nenhuma variação com tokens existentes")
                _guarda(mid, "variacoes", f"{len(variacoes)} variações para escolher", variacoes=variacoes[:4],
                        mensagem=str(d.get("mensagem") or "")[:600], sugestoes=design_html.sugestoes(d.get("sugestoes")),
                        passos=[f"Propôs “{v['nome']}” ({len(v['tokens'])} tokens)" for v in variacoes[:4]])
                return
            if modo in ("plano", "perguntas"):
                d = design_html.ler_json(texto)
                conversa = {"mensagem": str(d.get("mensagem") or "")[:800], "sugestoes": design_html.sugestoes(d.get("sugestoes"))}
                if modo == "perguntas":
                    perguntas = _perguntas(d)
                    if perguntas:
                        _guarda(mid, "perguntas", conversa["mensagem"] or "Antes de desenhar, umas perguntas rápidas:",
                                perguntas=perguntas, **conversa)
                        return
                    # o pedido já diz tudo: segue direto para o plano, na mesma mensagem e sem outro clique
                    run["modo"] = modo = "plano"
                    texto = await _chamar(run, [{"role": "system", "content": prompt("plano")}, mensagens[1]])
                    if run["cancelar"]:
                        _fecha(mid, "cancelado", "Cancelado — o documento não mudou.")
                        return
                    d = design_html.ler_json(texto)
                    conversa = {"mensagem": str(d.get("mensagem") or "")[:800], "sugestoes": design_html.sugestoes(d.get("sugestoes"))}
                plano = validar_plano(d)
                if run.get("ds_id"):   # o sistema manda: tokens dele por cima dos que o modelo inventou
                    plano = {**plano, "tokens": {**plano["tokens"], **design_sistema.pegar(run["ds_id"])["tokens"]},
                             "sistema": run["ds_id"]}
                _guarda(mid, "plano", f"Plano: {plano['titulo']} · {len(plano['secoes'])} "
                                      f"{'slides' if plano['tipo'] == 'slides' else 'telas' if plano['tipo'] == 'prototipo' else 'seções'}",
                        plano=plano, **conversa)
                return
            if modo == "documento":
                html = extrair_html(texto)
                if not html:
                    raise ValueError("a resposta não trouxe um documento HTML completo")
                mensagem, sugs = design_html.rodape(texto.split("</html>")[-1])
                _nova_versao(run["conv_id"], mid, html, run["descricao"], mensagem=mensagem, sugestoes=sugs,
                             passos=["Reescreveu o documento inteiro"], **extra)
                return
            mensagem, sugs, passos = "", [], []
            if modo in ("fragmento", "tweaks"):
                d = design_html.ler_json(texto)
                mensagem, sugs = str(d.get("mensagem") or "")[:800], design_html.sugestoes(d.get("sugestoes"))
                if modo == "tweaks":
                    tweaks = design_html.tweaks_validos(d.get("tweaks"))
                    if not tweaks:
                        raise ValueError("o modelo não propôs nenhum ajuste válido")
                    base_html = design_html.por_tweaks(base_html, tweaks)
                    resp = {"css": str(d.get("css") or "")}
                    passos = [f"Criou o ajuste “{t['rotulo']}” ({t['nome']}, {t['min']:g}–{t['max']:g}{t['unidade']})" for t in tweaks]
                    if not resp["css"].strip():
                        raise ValueError("os ajustes vieram sem o CSS que os usa")
                    passos.append("Acrescentou as regras CSS que usam esses ajustes")
                else:
                    resp = d
                    els = design_html.indexar(base_html)
                    for p_ in d.get("patches") or []:
                        e = design_html.por_fid(els, str((p_ or {}).get("fid")))
                        passos.append(f"Alterou <{design_html._rotulo(e).split('[')[0]}>" if e else f"Alterou {p_.get('fid')}")
                    if str(d.get("css") or "").strip():
                        passos.append("Acrescentou regras CSS")
                    if d.get("tokens"):
                        passos.append(f"Mudou tokens: {', '.join(d['tokens'])}")
            elif modo == "tokens":
                d = design_html.ler_json(texto)
                mensagem, sugs = str(d.get("mensagem") or "")[:800], design_html.sugestoes(d.get("sugestoes"))
                tokens = d.get("tokens") if isinstance(d.get("tokens"), dict) else d
                raiz = design_html.root_css(base_html)
                tokens = {k: v for k, v in tokens.items() if isinstance(k, str) and isinstance(v, str)
                          and re.search(rf"{re.escape(k)}\s*:", raiz)}
                if not tokens:
                    raise ValueError("nenhum token existente foi alterado")
                resp = {"tokens": tokens}   # só :root, venha o que vier
                antes = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", raiz))
                passos = [f"{k}: {antes.get(k, '').strip()} → {v}" for k, v in tokens.items()]
            else:  # secao
                sec, css = design_html.ler_secao(texto, run["secao"], run.get("slide", False), run.get("tela", False))
                mensagem, sugs = design_html.rodape(texto.rsplit("</style>", 1)[-1] if "</style>" in texto else texto)
                resp = {"patches": [{"fid": run["alvo"], "html": sec}], "css": css}
                passos = [f"{'Criou' if run.get('nova') else 'Refez'} a seção “{run['secao']}”"]
                if css.strip():
                    passos.append("Escreveu o CSS dela")
            html, mudou = design_html.aplicar(base_html, resp)
            if not extrair_html(html):
                raise ValueError("o documento ficaria inválido")
        except ValueError as e:
            _fecha(mid, "erro", f"Edição recusada: {e} — o documento não mudou.")
            return
        # seção nova não existe no canvas: sem patches, a tela recarrega o documento
        n = _nova_versao(run["conv_id"], mid, html, run["descricao"], patches=[] if run.get("nova") else mudou,
                         mensagem=mensagem, sugestoes=sugs, passos=passos, **extra)
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
    telas = plano.get("tipo") == "prototipo"
    unidade = {"slides": "slide", "prototipo": "tela"}.get(plano.get("tipo"), "seção")
    erros, passos = [], [f"Montou o esqueleto: tokens do plano e {len(nomes)} lugar(es) de {unidade}"]
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
                texto = await _chamar(run, [{"role": "system", "content": prompt("slide" if slides else "tela" if telas else "secao")},
                                            {"role": "user", "content": user}])
                if run["cancelar"]:
                    run["secoes"][i]["status"] = "fila"
                    break
                html_sec, css = design_html.ler_secao(texto, sec["nome"], slides, telas)
                run["doc"], _ = design_html.aplicar(run["doc"], {"patches": [{"fid": alvo, "html": html_sec}], "css": css})
                run["secoes"][i]["status"] = "ok"
                run["n"] += 1
                passos.append(f"Escreveu {'o' if unidade != 'seção' else 'a'} {unidade} “{sec['nome']}”")
            except ValueError as e:   # uma seção ruim não derruba as outras
                run["secoes"][i]["status"] = "erro"
                erros.append(f"{sec['nome']}: {e}")
                passos.append(f"Não conseguiu {'o' if unidade != 'seção' else 'a'} {unidade} “{sec['nome']}”: {e}")
        feitas, total = run["n"], len(plano["secoes"])
        doc = design_html.limpar_placeholders(run["doc"])
        if feitas and extrair_html(doc):
            sufixo = "" if feitas == total else f" ({feitas} de {total} seções{' — cancelado' if run['cancelar'] else ''})"
            fora = [x["nome"] for x in run["secoes"] if x["status"] != "ok"]
            mensagem = (f"Pronto: **{plano['titulo']}** com {feitas} {unidade if feitas == 1 else {'seção': 'seções', 'slide': 'slides', 'tela': 'telas'}[unidade]}."
                        + (f" Ficaram de fora: {', '.join(fora)} — dá para pedir de novo cada uma." if fora else "")
                        + " Selecione qualquer elemento no canvas para ajustar, ou use as sugestões abaixo.")
            _nova_versao(run["conv_id"], mid, doc, plano["titulo"] + sufixo, base=run["base"], rota="etapas",
                         entrada=run["entrada"], avisos=erros, passos=passos, mensagem=mensagem)
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

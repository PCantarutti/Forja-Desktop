"""Receitas das tarefas faladas ao assistente F.O.R.J.A: o caminho mais curto que já deu certo para um tipo de pedido
("tocar música aleatória das curtidas no Spotify"), para a próxima vez ir direto.

Uma receita por arquivo JSON em <memória>/receitas, fora do índice da memória pessoal (que vai inteiro em todo
pedido): quem procura é o Forja, antes de a tarefa começar, e só as que combinam com o pedido entram nele.
Ciclo: o agente salva (rascunho) -> a pessoa confirma por voz (aprovada) ou corrige (nova tentativa, até
TENTATIVAS). Aprovada que falhou e foi reescrita guarda a versão anterior até a nova ser confirmada; desistiu, ela
volta. Nada de senha nem dado de conta: é o caminho, não o caso.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

from . import config
from .tools import Tool, ToolError, register

PASTA = config.PERSONAL_MEMORY_DIR / "receitas"
TENTATIVAS = 3  # correções seguidas antes de desistir e perguntar o que está errado
# posição de clique ("±690,98", "(380, 438)"): muda com a janela, e prende a receita àquela tela
COORDENADA = re.compile(r"[±~(]\s*\d{2,4}\s*[,x]\s*\d{2,4}|\b(?:x|y)\s*=\s*\d{2,4}")
GERAL = ("A receita é do TIPO de pedido, não do caso: o que muda de uma vez para outra (a música, o produto, o "
         "arquivo, o site) vira parâmetro entre < >. Pediu um teclado -> \"pesquisar um produto e abrir as lojas\", "
         "passos com <produto>; pediu uma música -> \"tocar uma música específica no Spotify\", passos com <música>. "
         "Nome, palavras e passos sem o item da vez. Uma receita por tipo de pedido: nunca junte tarefas diferentes.")
VAZIAS = {"a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das", "e", "em", "no", "na", "nos",
          "nas", "para", "pra", "pro", "por", "com", "me", "mim", "minha", "minhas", "meu", "meus", "que", "eu", "voce",
          "se", "ao", "algum", "alguma", "the", "my", "to", "and", "of", "forja", "favor", "agora", "ai", "aqui"}


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(c))


def _raizes(texto: str) -> set[str]:
    """Palavras com conteúdo, cortadas em 5 letras: "músicas"/"música" e "curtidas"/"curtida" casam."""
    return {p[:5] for p in re.findall(r"[a-z0-9]+", _sem_acento(texto)) if len(p) > 2 and p not in VAZIAS}


def _slug(nome: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", _sem_acento(nome)).strip("-")[:60]
    if not s:
        raise ToolError("A receita precisa de um nome.")
    return s


def _arquivo(rid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,60}", rid or ""):
        raise ToolError("Receita inválida.")
    return PASTA / f"{rid}.json"


def ler(rid: str) -> dict:
    try:
        return json.loads(_arquivo(rid).read_text("utf-8"))
    except FileNotFoundError:
        raise ToolError(f"Não existe a receita {rid}.") from None


def _grava(r: dict) -> dict:
    PASTA.mkdir(parents=True, exist_ok=True)
    r["atualizada"] = time.strftime("%Y-%m-%d %H:%M")
    _arquivo(r["id"]).write_text(json.dumps(r, ensure_ascii=False, indent=2), "utf-8")
    return r


def todas() -> list[dict]:
    saida = []
    for f in sorted(PASTA.glob("*.json")) if PASTA.is_dir() else ():
        try:
            saida.append(json.loads(f.read_text("utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(saida, key=lambda r: r.get("atualizada", ""), reverse=True)


def salvar(nome: str, palavras: list[str], objetivo: str, passos: list[str], conferir: str, armadilhas: str = "") -> dict:
    """Nova receita, ou a mesma (pelo nome) reescrita. Reescrever uma aprovada guarda a anterior e volta a rascunho:
    o caminho novo só vale depois que a pessoa confirmar."""
    passos = [str(p).strip()[:300] for p in (passos or []) if str(p).strip()][:15]
    if not passos or not str(conferir or "").strip():
        raise ToolError("A receita precisa dos passos e de como conferir que deu certo.")
    if any(COORDENADA.search(p) for p in passos):
        raise ToolError("Sem posição de tela na receita: diga O QUE clicar (o texto ou o botão), não onde. " + GERAL)
    rid = _slug(nome)
    try:
        velha = ler(rid)
    except ToolError:
        velha = None
    r = {"id": rid, "nome": str(nome).strip()[:80], "palavras": [str(p).strip()[:40] for p in palavras or []][:12],
         "objetivo": str(objetivo or "").strip()[:300], "passos": passos, "conferir": str(conferir).strip()[:300],
         "armadilhas": str(armadilhas or "").strip()[:500], "status": "rascunho",
         "usos": (velha or {}).get("usos", 0), "acertos": (velha or {}).get("acertos", 0),
         "falhas": (velha or {}).get("falhas", 0),
         # a anterior que funcionava: a de antes, se era aprovada; senão a que ela já guardava
         "anterior": ({k: v for k, v in velha.items() if k != "anterior"} if velha and velha["status"] == "aprovada"
                      else (velha or {}).get("anterior"))}
    return _grava(r)


def aprovar(rid: str) -> dict:
    r = ler(rid)
    r.update(status="aprovada", anterior=None, acertos=r.get("acertos", 0) + 1, usos=r.get("usos", 0) + 1)
    return _grava(r)


def acertou(rid: str) -> dict:
    """Usada e deu certo, sem perguntar (já era aprovada)."""
    r = ler(rid)
    r.update(acertos=r.get("acertos", 0) + 1, usos=r.get("usos", 0) + 1)
    return _grava(r)


def falhou(rid: str) -> dict:
    r = ler(rid)
    r.update(falhas=r.get("falhas", 0) + 1, usos=r.get("usos", 0) + 1)
    return _grava(r)


def desistir(rid: str) -> dict | None:
    """Não acertou nem com as correções: a versão que funcionava volta; sem ela, o rascunho errado sai."""
    r = ler(rid)
    if r.get("anterior"):
        return _grava({**r["anterior"], "anterior": None, "falhas": r.get("falhas", 0), "usos": r.get("usos", 0)})
    if r["status"] == "rascunho":
        _arquivo(rid).unlink(missing_ok=True)
        return None
    return r


def apagar(rid: str) -> None:
    _arquivo(rid).unlink(missing_ok=True)


def busca(pedido: str, n: int = 3) -> list[dict]:
    """As receitas que combinam com o pedido: palavras-chave valem 2, nome e objetivo 1; precisa de 2 pontos."""
    alvo = _raizes(pedido)
    if not alvo:
        return []
    notas = []
    for r in todas():
        chave = _raizes(" ".join(r.get("palavras", [])))
        texto = _raizes(f"{r.get('nome', '')} {r.get('objetivo', '')}") - chave
        pontos = 2 * len(alvo & chave) + len(alvo & texto)
        if pontos >= 2:
            notas.append((pontos, r.get("status") == "aprovada", r))
    return [r for *_, r in sorted(notas, key=lambda x: (x[0], x[1]), reverse=True)[:n]]


def texto(r: dict) -> str:
    """A receita como entra no pedido: curta, numerada."""
    status = "aprovada pela pessoa" if r["status"] == "aprovada" else "ainda não confirmada"
    linhas = [f"Receita \"{r['nome']}\" ({status}; {r.get('acertos', 0)} acerto(s), {r.get('falhas', 0)} falha(s))",
              f"Objetivo: {r['objetivo']}" if r.get("objetivo") else "",
              *[f"{i}. {p}" for i, p in enumerate(r["passos"], 1)],
              f"Conferir: {r['conferir']}", f"Armadilhas: {r['armadilhas']}" if r.get("armadilhas") else ""]
    return "\n".join(x for x in linhas if x)


def _salvar_tool(_root, args: dict) -> str:
    r = salvar(str(args.get("nome") or ""), args.get("palavras") or [], str(args.get("objetivo") or ""),
               args.get("passos") or [], str(args.get("conferir") or ""), str(args.get("armadilhas") or ""))
    return f"Receita salva: {r['id']} (rascunho: vale como certa depois que a pessoa confirmar)."


register(Tool(
    "salvar_receita",
    "Só nas tarefas pedidas por voz ao assistente F.O.R.J.A: guarda o caminho mais curto que FUNCIONOU (e que você "
    "conferiu) para ir direto na próxima vez. " + GERAL + " Prefira passos firmes (URL/URI direta como "
    "spotify:search:<música>, atalho, API) a clicar na tela, e nunca guarde posição de clique. A mesma receita que "
    "falhou: reescreva com o MESMO nome. Nada de senha, token ou dado de conta.",
    {"type": "object", "properties": {
        "nome": {"type": "string", "description": "O TIPO de pedido, ex.: 'tocar uma música específica no Spotify'"},
        "palavras": {"type": "array", "items": {"type": "string"},
                     "description": "Palavras gerais com que se pede isso (spotify, música, tocar), sem o item da vez"},
        "objetivo": {"type": "string"},
        "passos": {"type": "array", "items": {"type": "string"}, "description": "Os passos mínimos, em ordem"},
        "conferir": {"type": "string", "description": "Como ver que deu certo (o que aparece na tela/resultado)"},
        "armadilhas": {"type": "string", "description": "O que deu errado no caminho e deve ser evitado"}},
     "required": ["nome", "palavras", "passos", "conferir"]},
    _salvar_tool))

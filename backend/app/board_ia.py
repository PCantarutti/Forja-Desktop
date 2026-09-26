"""Varredura com IA do board (E15-B): o modelo lê o código e sugere cards, com prova.

- Camada 2 (código): lotes de arquivos; cada achado precisa de `arquivo:linha` e de um trecho que está
  mesmo ali. Sem isso, descartado.
- Incremental: só o que mudou desde o commit da última varredura (guardado por projeto). A primeira passa
  por tudo, pasta por pasta, e retoma de onde parou.
- Camada 3 (visual): fica de fora por enquanto, com o motivo no aviso (precisa de servidor no ar e de um
  modelo de visão junto; a revisão visual do Maestro já cobre a entrega).
- Camada 4 (triagem): junta repetidos, classifica e escreve o prompt pronto, sabendo o que o usuário já
  rejeitou. Falhando, os achados crus viram cards.
- Prioridade mínima: papel "varredura" da como_rodar, nunca troca de modelo, e para (salvando onde estava)
  quando o agente ou o Maestro precisam do modelo. Até TETO cards; o resto espera em "mais achados".
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from sqlalchemy import select

from . import board, config, db, gitops, llm, modelctl
from .codigo import LINGUAS
from .parsing import split_think

TETO = 20
POR_LOTE = 5            # achados por lote pedidos ao modelo
CHAVE = "varredura_ia"  # app_settings: {projeto: {"commit", "pendentes", "mais"}}
ACEITE_MINIMO = 0.3     # abaixo disso (com amostra), a camada fica desligada no projeto
AMOSTRA_MINIMA = 10
ESTADO: dict[str, dict] = {}

PROMPT_CODIGO = (
    "Você revisa código de um projeto e aponta problemas REAIS. Procure: bugs prováveis, tela ou ação faltando "
    "(rota sem tela, botão sem ação, formulário sem validação), código morto e ideias de melhoria concretas. "
    "Nada de estilo nem opinião. Responda só com JSON:\n"
    '{"achados":[{"tipo":"bugfix|feature|improvement|todo","titulo":"...","arquivo":"caminho/igual/ao/cabecalho",'
    '"linha":12,"trecho":"linha copiada EXATAMENTE do código","porque":"uma frase","severidade":1}]}\n'
    f"No máximo {POR_LOTE} achados, só os de maior valor; lista vazia se não houver nada sério. severidade 1 = alta, "
    "3 = baixa. Achado sem arquivo, linha e trecho copiado é descartado.")

PROMPT_TRIAGEM = (
    "Você faz a triagem de achados de uma varredura de código antes de virarem cards. Junte os repetidos, "
    "descarte os fracos e os parecidos com os que o usuário já rejeitou, e escreva para cada card um prompt "
    "pronto para outro modelo executar: contexto, arquivos, o que fazer e o critério de aceite. Responda só com "
    'JSON: {"cards":[{"ids":[1,4],"tipo":"bugfix|feature|improvement|todo","area":"frontend|backend|fullstack|'
    'testes|infra","severidade":2,"titulo":"...","prompt":"...","verify_sugerido":"comando que prova, ou vazio"}]}\n'
    "O verify_sugerido é um comando de UMA linha que só usa nomes e assinaturas que aparecem nos trechos (nada "
    "inventado); na dúvida, deixe vazio.")


# ------------------------------------------------------------------ estado por projeto (sobrevive ao reinício)

def _cfg(projeto: str) -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        tudo = dict(linha.value) if linha and isinstance(linha.value, dict) else {}
    return dict(tudo.get(projeto) or {})


def _salva(projeto: str, dados: dict) -> None:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE)
        tudo = dict(linha.value) if linha and isinstance(linha.value, dict) else {}
        tudo[projeto] = dados
        if linha:
            linha.value = tudo
        else:
            s.add(db.AppSetting(key=CHAVE, value=tudo))
        s.commit()


# ------------------------------------------------------------------ o que ler

def _git(root: Path, cmd: str) -> list[str]:
    code, out = gitops._run(root, cmd, 60)
    return [l.strip().strip('"') for l in out.splitlines() if l.strip()] if code == 0 else []


def arquivos_para_ler(root: Path, commit: str | None) -> list[str]:
    """Código rastreado pelo git (o que está no .gitignore fica de fora sozinho), só o mudado desde `commit`."""
    todos = [a for a in _git(root, "git ls-files") if Path(a).suffix.lower() in LINGUAS]
    if commit and _git(root, f"git cat-file -t {commit}"):
        mudou = set(_git(root, f"git diff --name-only {commit} HEAD"))
        mudou |= {l[3:].strip() for l in gitops._run(root, "git status --porcelain=v1", 30)[1].splitlines() if len(l) > 3}
        todos = [a for a in todos if a in mudou] + sorted(m for m in mudou if m not in todos
                                                          and Path(m).suffix.lower() in LINGUAS and (root / m).is_file())
    return sorted(dict.fromkeys(todos), key=lambda a: (str(Path(a).parent), a))  # pasta por pasta


def _lotes(root: Path, arquivos: list[str], limite: int) -> list[list[str]]:
    lotes, atual, tam = [], [], 0
    for a in arquivos:
        try:
            n = (root / a).stat().st_size
        except OSError:
            continue
        if n > limite:  # arquivo enorme: vai sozinho (e cortado)
            if atual:
                lotes.append(atual)
                atual, tam = [], 0
            lotes.append([a])
            continue
        if tam + n > limite and atual:
            lotes.append(atual)
            atual, tam = [], 0
        atual.append(a)
        tam += n
    return lotes + ([atual] if atual else [])


def _numerado(root: Path, arquivo: str, limite: int) -> str:
    try:
        linhas = (root / arquivo).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    corpo = "\n".join(f"{i + 1:>4} {l}" for i, l in enumerate(linhas))
    return f"=== {arquivo} ===\n{corpo[:limite]}"


# ------------------------------------------------------------------ validação dos achados

def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip().lower()


def valida(root: Path, a: dict) -> dict | None:
    """Achado → dados de card, ou None. Exige arquivo do projeto, linha dentro dele e o trecho de verdade perto
    da linha (±3): é o que separa achado de alucinação."""
    if not isinstance(a, dict):
        return None
    arquivo = str(a.get("arquivo") or "").replace("\\", "/").strip().lstrip("./")
    trecho = str(a.get("trecho") or "").strip()
    try:
        linha = int(a.get("linha"))
        alvo = (root / arquivo).resolve()
        alvo.relative_to(root.resolve())
        linhas = alvo.read_text(encoding="utf-8", errors="replace").splitlines()
    except (TypeError, ValueError, OSError):
        return None
    if not arquivo or not trecho or not 1 <= linha <= len(linhas):
        return None
    perto = _norm(" ".join(linhas[max(0, linha - 4): linha + 3]))
    if _norm(trecho) not in perto and not any(_norm(trecho) in _norm(l) for l in linhas[max(0, linha - 4): linha + 3]):
        return None
    tipo = a.get("tipo") if a.get("tipo") in board.TIPOS else "improvement"
    sev = a.get("severidade") if a.get("severidade") in (1, 2, 3) else 2
    titulo = str(a.get("titulo") or "").strip()[:200] or f"{tipo} em {arquivo}:{linha}"
    return {"titulo": titulo, "tipo": tipo, "severidade": sev, "area": board._area(arquivo),
            "descricao": str(a.get("porque") or "")[:1000],
            "evidencias": [{"arquivo": arquivo, "linha": linha, "trecho": trecho[:500]}],
            "impressao": board.impressao(tipo, arquivo, trecho)}


def _json(texto: str) -> dict:
    texto = split_think(texto)[1]
    for m in re.finditer(r"\{.*\}", texto, re.S):
        try:
            return json.loads(m.group(0))
        except ValueError:
            continue
    return {}


# ------------------------------------------------------------------ modelo e qualidade

def _modelo() -> dict | None:
    """O da Maestro, se configurado; senão o modelo local carregado; senão o Worker capaz."""
    for spec in ((getattr(config, "MAESTRO_MODEL", {}) or {}), modelctl._local_carregado() or {},
                 (getattr(config, "SUBAGENTS", {}) or {}).get("capaz") or {}):
        if spec.get("provider") and spec.get("model"):
            return dict(spec)
    return None


def aceite(projeto: str) -> dict:
    """Taxa de aceite dos cards da varredura com IA neste projeto, por tipo (E10)."""
    with db.session() as s:
        cards = s.scalars(select(db.Issue).where(db.Issue.projeto == projeto, db.Issue.origem == "varredura-ia")).all()
        pares = [(c.tipo, c.status) for c in cards]
    out: dict = {}
    for tipo, st in pares + [("total", st) for _, st in pares]:
        d = out.setdefault(tipo, {"aceitos": 0, "rejeitados": 0})
        if st == "rejeitado":
            d["rejeitados"] += 1
        elif st != "novo":
            d["aceitos"] += 1
    for d in out.values():
        n = d["aceitos"] + d["rejeitados"]
        d["pct"] = round(100 * d["aceitos"] / n, 1) if n else None
    return out


def _rejeitados(projeto: str) -> list[str]:
    with db.session() as s:
        return [f"{i.titulo} ({i.motivo_rejeicao or 'rejeitado'})" for i in s.scalars(
            select(db.Issue).where(db.Issue.projeto == projeto, db.Issue.status == "rejeitado")
            .order_by(db.Issue.updated_at.desc()).limit(15))]


async def _pergunta(spec: dict, rota, messages: list[dict]) -> str:
    texto = ""
    async for kind, val in llm.chat_stream(spec["provider"], spec["model"], messages, None, config.NUM_CTX, "baixo",
                                           **({"slot": rota.slot} if rota.slot is not None else {})):
        if kind == "content":
            texto += val
    return texto


# ------------------------------------------------------------------ a varredura

def ocupado() -> bool:
    """Alguém precisa do modelo (agente, Maestro, Worker, carga): a varredura cede."""
    try:
        return modelctl.em_uso()
    except Exception:
        return False


async def _varre(projeto: str, root: Path, forcar: bool) -> None:
    est = ESTADO[projeto]
    try:
        spec = _modelo()
        rota = modelctl.como_rodar("varredura", spec)
        if not rota.spec or rota.caminho in ("pular", "trocar-modelo"):
            est["avisos"].append(f"Sem modelo para varrer agora ({rota.motivo}). Carregue um modelo ou configure o "
                                 "da Maestro.")
            return
        spec = dict(rota.spec)
        t = aceite(projeto).get("total") or {}
        if not forcar and t.get("pct") is not None and t["aceitos"] + t["rejeitados"] >= AMOSTRA_MINIMA \
                and t["pct"] < ACEITE_MINIMO * 100:
            est["avisos"].append(f"A varredura com IA está desligada neste projeto: só {t['pct']}% dos cards dela "
                                 "foram aceitos. Use 'varrer mesmo assim' para rodar.")
            return
        cfg = _cfg(projeto)
        head = board._head(root)
        pendentes = cfg.get("pendentes") or arquivos_para_ler(root, cfg.get("commit"))
        janela = await llm.context_limit(spec["provider"], spec["model"], config.NUM_CTX) or config.NUM_CTX
        limite = max(4000, min(int(janela * 3 * 0.45), 60_000))  # chars por lote (~45% da janela)
        lotes = _lotes(root, pendentes, limite)
        est.update(etapa="código", total=len(pendentes), lidos=0)
        achados: list[dict] = list(cfg.get("achados_parciais") or [])
        for lote in lotes:
            if ocupado():
                est["parou"] = "o modelo foi pedido pelo agente ou pelo Maestro; retoma na próxima varredura"
                _salva(projeto, {**cfg, "pendentes": pendentes, "achados_parciais": achados})
                return
            corpo = "\n\n".join(_numerado(root, a, limite // max(1, len(lote))) for a in lote)
            try:
                texto = await _pergunta(spec, rota, [{"role": "system", "content": PROMPT_CODIGO},
                                                     {"role": "user", "content": corpo}])
            except llm.LLMError as e:
                est["avisos"].append(f"O modelo falhou num lote ({e}); seguindo.")
                texto = ""
            for a in (_json(texto).get("achados") or [])[:POR_LOTE]:
                if v := valida(root, a):
                    achados.append(v)
                else:
                    est["descartados"] += 1
            pendentes = [p for p in pendentes if p not in lote]
            est["lidos"] += len(lote)
            _salva(projeto, {**cfg, "pendentes": pendentes, "achados_parciais": achados})
        est["avisos"].append("Camada visual não roda na varredura (precisa de servidor no ar e de modelo de visão "
                             "junto); a revisão visual do Maestro cobre as entregas.")
        est["etapa"] = "triagem"
        cards = await _triagem(projeto, spec, rota, achados) if achados else []
        mais = list(cfg.get("mais") or [])
        for c in cards:
            if est["criados"] >= TETO:
                mais.append(c)
                continue
            try:
                _, novo = board.criar(projeto, c, "varredura-ia")
            except board.BoardError:
                est["descartados"] += 1
                continue
            est["criados"] += novo
        _salva(projeto, {"commit": head, "pendentes": [], "achados_parciais": [], "mais": mais})
        est["mais"] = len(mais)
    except Exception as e:  # trabalho de fundo: o erro aparece no board
        est["avisos"].append(f"A varredura com IA parou: {type(e).__name__}: {e}")
    finally:
        est.update(rodando=False, fim=time.time(), etapa="")


async def _triagem(projeto: str, spec: dict, rota, achados: list[dict]) -> list[dict]:
    """Camada 4. Devolve os dados dos cards; falhando, os achados crus (já validados)."""
    lista = "\n".join(f"{i + 1}. [{a['tipo']}, sev {a['severidade']}] {a['titulo']} — "
                      f"{a['evidencias'][0]['arquivo']}:{a['evidencias'][0]['linha']} `{a['evidencias'][0]['trecho'][:160]}` — "
                      f"{a['descricao'][:200]}"
                      for i, a in enumerate(achados))
    rej = "\n".join(f"- {r}" for r in _rejeitados(projeto)) or "(nenhum)"
    try:
        texto = await _pergunta(spec, rota, [{"role": "system", "content": PROMPT_TRIAGEM},
                                             {"role": "user", "content": f"Achados:\n{lista}\n\nJá rejeitados pelo "
                                                                         f"usuário (não repita):\n{rej}"}])
    except llm.LLMError:
        return achados
    out = []
    for c in _json(texto).get("cards") or []:
        ids = [i - 1 for i in c.get("ids") or [] if isinstance(i, int) and 0 < i <= len(achados)]
        if not ids:
            continue
        base = achados[ids[0]]
        out.append({**base,
                    "titulo": str(c.get("titulo") or base["titulo"])[:200],
                    "tipo": c.get("tipo") if c.get("tipo") in board.TIPOS else base["tipo"],
                    "area": c.get("area") if c.get("area") in board.AREAS else base["area"],
                    "severidade": c.get("severidade") if c.get("severidade") in (1, 2, 3) else base["severidade"],
                    "prompt": str(c.get("prompt") or "")[:6000],
                    "verify_sugerido": str(c.get("verify_sugerido") or "")[:500],
                    "evidencias": [e for i in ids for e in achados[i]["evidencias"]][:10]})
    return out or achados


def varrer(pasta: str, forcar: bool = False) -> dict:
    projeto = board.projeto_de(pasta)
    root = Path(projeto)
    if not gitops.is_repo(root):
        raise board.BoardError("A varredura com IA precisa de um repositório git (é o que a deixa incremental).")
    if ESTADO.get(projeto, {}).get("rodando"):
        return ESTADO[projeto]
    ESTADO[projeto] = {"rodando": True, "inicio": time.time(), "fim": None, "etapa": "preparando", "total": 0,
                       "lidos": 0, "criados": 0, "descartados": 0, "mais": 0, "parou": "", "avisos": []}
    asyncio.get_running_loop().create_task(_varre(projeto, root, forcar))
    return ESTADO[projeto]


def estado(projeto: str) -> dict | None:
    if (e := ESTADO.get(projeto)) is not None:
        return e
    mais = len(_cfg(projeto).get("mais") or [])
    return {"rodando": False, "mais": mais, "avisos": []} if mais else None


def trazer_mais(pasta: str) -> dict:
    """Os próximos TETO da fila "mais achados" viram cards."""
    projeto = board.projeto_de(pasta)
    cfg = _cfg(projeto)
    fila, criados = list(cfg.get("mais") or []), 0
    while fila and criados < TETO:
        try:
            criados += board.criar(projeto, fila.pop(0), "varredura-ia")[1]
        except board.BoardError:
            continue
    _salva(projeto, {**cfg, "mais": fila})
    if projeto in ESTADO:
        ESTADO[projeto]["mais"] = len(fila)
    return {"criados": criados, "mais": len(fila)}

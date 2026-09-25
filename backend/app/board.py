"""Board de issues do projeto (E15, parte A): cards, varredura determinística e o botão Iniciar.

Um card é um trabalho a fazer no projeto, com evidência (`arquivo:linha`, saída de comando). O que a
varredura acha cai em "novo" e só vai para o backlog quando o usuário aceita: sem essa triagem o
board vira lixo. A impressão digital (tipo + arquivo + trecho normalizado) impede que a próxima
varredura recrie o que já existe ou o que o usuário rejeitou.

Varredura sem LLM (camada 1):
- TODO/FIXME/HACK/XXX em comentário → `todo`;
- `test_command:`, `typecheck_command:` e `lint_command:` do FORJA.md → `bugfix`, um card por erro
  (tsc, pytest e eslint são lidos linha a linha; outro comando vira um card com a saída);
- `npm audit` (package-lock.json) → `seguranca`, só alta e crítica; `pip-audit`, se instalado.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from . import config, db, workspace
from .memory import _raiz_projeto

TIPOS = ("bugfix", "feature", "improvement", "visual", "todo", "seguranca")
AREAS = ("frontend", "backend", "fullstack", "testes", "infra")
STATUS = ("novo", "backlog", "andamento", "revisao", "concluido", "rejeitado")
MOTIVOS = ("nao_e_bug", "nao_quero", "duplicado")
ORIGENS = ("manual", "varredura-deterministica", "varredura-ia", "visual")
MAX_TODOS = 300           # TODOs por varredura: repo velho tem milhares, e 300 cards já não se triam
MAX_POR_COMANDO = 50      # erros de um mesmo comando (tsc de um repo quebrado cospe centenas)
TIMEOUT_COMANDO = 600
MAX_SAIDA = 4000
FRONT = (".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html", ".astro")
TESTE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_|\.(test|spec)\.")
TODO_RE = re.compile(r"(?:#|//|/\*|\*|<!--|--|;|\bREM\b)\s*(TODO|FIXME|HACK|XXX)\b[\s:(\-\]]*(.*)", re.I)
_VARREDURAS: dict[str, dict] = {}   # projeto -> {rodando, inicio, fim, criados, avisos}
_TRAVA = threading.Lock()


class BoardError(ValueError):
    pass


def _agora() -> datetime:
    return datetime.now()


# ------------------------------------------------------------------ projeto e impressão digital

def projeto_de(pasta: str) -> str:
    """Raiz do projeto (a pasta com .git acima da conversa), normalizada: é a chave do board."""
    p = workspace.resolve(pasta)
    return workspace.normalize(str(_raiz_projeto(p.resolve())))


def _normaliza(trecho: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", str(trecho or ""))).strip().lower()[:300]


def impressao(tipo: str, arquivo: str, trecho: str) -> str:
    """Números viram '#': o mesmo erro com linha ou contagem diferente é o mesmo card."""
    return hashlib.sha1(f"{tipo}|{arquivo.lower()}|{_normaliza(trecho)}".encode()).hexdigest()[:40]


def _area(arquivo: str) -> str:
    a = arquivo.replace("\\", "/").lower()
    if TESTE.search(a):
        return "testes"
    if a.endswith(FRONT) or "/frontend/" in f"/{a}" or "/components/" in a:
        return "frontend"
    if a.endswith((".yml", ".yaml", "dockerfile", ".tf")) or "/.github/" in f"/{a}":
        return "infra"
    return "backend"


def modo_sugerido(card: dict) -> str:
    """bugfix, todo, visual e segurança pequenos → agente; feature ou fullstack → Maestro."""
    return "maestro" if card["tipo"] == "feature" or card["area"] == "fullstack" else "agent"


def _dict(i: db.Issue) -> dict:
    d = {c.name: getattr(i, c.name) for c in db.Issue.__table__.columns}
    d["created_at"] = i.created_at.isoformat() if i.created_at else None
    d["updated_at"] = i.updated_at.isoformat() if i.updated_at else None
    d["modo_sugerido"] = modo_sugerido(d)
    return d


def _evento(i: db.Issue, texto: str) -> None:
    i.historico = [*(i.historico or []), {"quando": _agora().isoformat(timespec="seconds"), "texto": texto}][-50:]
    i.updated_at = _agora()


# ------------------------------------------------------------------ CRUD

def listar(projeto: str) -> list[dict]:
    acompanha()
    with db.session() as s:
        return [_dict(i) for i in s.scalars(select(db.Issue).where(db.Issue.projeto == projeto)
                                            .order_by(db.Issue.severidade, db.Issue.id.desc()))]


def _valida(dados: dict) -> dict:
    out = {}
    for k in ("titulo", "descricao", "prompt", "verify_sugerido"):
        if k in dados:
            out[k] = str(dados[k] or "").strip()[:300 if k == "titulo" else 20_000]
    if "titulo" in out and not out["titulo"]:
        raise BoardError("O card precisa de um título.")
    for k, validos in (("tipo", TIPOS), ("area", AREAS), ("status", STATUS)):
        if k in dados:
            if dados[k] not in validos:
                raise BoardError(f"'{k}' deve ser um de: {', '.join(validos)}.")
            out[k] = dados[k]
    if "severidade" in dados:
        if dados["severidade"] not in (1, 2, 3):
            raise BoardError("'severidade' é 1 (alta), 2 ou 3 (baixa).")
        out["severidade"] = dados["severidade"]
    if "evidencias" in dados:
        if not isinstance(dados["evidencias"], list):
            raise BoardError("'evidencias' é uma lista.")
        out["evidencias"] = dados["evidencias"][:20]
    return out


def criar(projeto: str, dados: dict, origem: str = "manual") -> tuple[dict, bool]:
    """(card, criado). Com impressão digital já conhecida — inclusive de card rejeitado — devolve o
    existente sem criar: é o que impede a varredura de ressuscitar o que o usuário descartou."""
    campos = _valida({"tipo": "bugfix", "area": "backend", "severidade": 2, **dados})  # fullstack = Maestro: só se pedido
    status = campos.pop("status", "backlog") if origem == "manual" else "novo"  # varredura sempre cai em Novo
    campos.pop("status", None)
    if "titulo" not in campos:
        raise BoardError("O card precisa de um título.")
    if origem != "manual" and not campos.get("evidencias"):
        raise BoardError("Card de varredura sem evidência não é criado.")
    marca = dados.get("impressao")
    with db.session() as s:
        if marca and (velho := s.scalar(select(db.Issue).where(db.Issue.projeto == projeto,
                                                                db.Issue.impressao == marca))):
            if velho.sumiu:  # voltou a aparecer: não está resolvido
                velho.sumiu = False
                s.commit()
            return _dict(velho), False
        i = db.Issue(projeto=projeto, origem=origem, impressao=marca, status=status, **campos)
        _evento(i, "criado " + ("à mão" if origem == "manual" else f"pela {origem}"))
        s.add(i)
        s.commit()
        return _dict(i), True


def atualizar(issue_id: int, dados: dict) -> dict:
    campos = _valida(dados)
    with db.session() as s:
        i = s.get(db.Issue, issue_id)
        if not i:
            raise BoardError(f"Card {issue_id} não existe.")
        if campos.get("status") and campos["status"] != i.status:
            _evento(i, f"{i.status} → {campos['status']}")
            if campos["status"] != "rejeitado":
                i.motivo_rejeicao = None
            if campos["status"] in ("backlog", "concluido"):
                i.sumiu = False
        for k, v in campos.items():
            setattr(i, k, v)
        i.updated_at = _agora()
        s.commit()
        return _dict(i)


def rejeitar(issue_id: int, motivo: str | None) -> dict:
    if motivo and motivo not in MOTIVOS:
        raise BoardError(f"Motivo deve ser um de: {', '.join(MOTIVOS)}.")
    card = atualizar(issue_id, {"status": "rejeitado"})
    with db.session() as s:
        i = s.get(db.Issue, issue_id)
        i.motivo_rejeicao = motivo
        s.commit()
        return {**card, "motivo_rejeicao": motivo}


def apagar(issue_id: int) -> None:
    with db.session() as s:
        if i := s.get(db.Issue, issue_id):
            s.delete(i)
            s.commit()


def carimbo() -> str:
    """Para o /api/activity: muda quando qualquer card muda ou uma varredura começa ou termina."""
    with db.session() as s:
        n, ultima = s.execute(select(func.count(db.Issue.id), func.max(db.Issue.updated_at))).one()
    rodando = sum(1 for v in _VARREDURAS.values() if v.get("rodando"))
    fins = max((v.get("fim") or 0 for v in _VARREDURAS.values()), default=0)
    return f"{n}-{ultima}-{rodando}-{fins:.0f}"


# ------------------------------------------------------------------ varredura determinística

def _todos(root: Path) -> list[dict]:
    from .codebusca import EXTS
    from .codigo import _analisaveis
    from .tools import _rel
    achados = []
    for p in _analisaveis(root, root, EXTS, ocultas=False):
        if len(achados) >= MAX_TODOS:
            break
        try:
            if p.stat().st_size > 400_000:
                continue
            linhas = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = _rel(root, p)
        for n, linha in enumerate(linhas, 1):
            m = TODO_RE.search(linha)
            if not m:
                continue
            marca, texto = m.group(1).upper(), m.group(2).strip().rstrip("*/-> ").strip()
            titulo = f"{marca}: {texto}"[:140] if texto else f"{marca} em {rel}:{n}"
            achados.append({
                "titulo": titulo, "tipo": "todo", "area": _area(rel), "severidade": 2 if marca == "FIXME" else 3,
                "descricao": f"Comentário {marca} deixado no código.",
                "evidencias": [{"arquivo": rel, "linha": n, "trecho": linha.strip()[:300]}],
                "impressao": impressao("todo", rel, texto or linha)})
            if len(achados) >= MAX_TODOS:
                break
    return achados


def comandos_do_projeto(root: Path) -> dict[str, str]:
    """`test_command:`, `typecheck_command:`, `lint_command:` no FORJA.md, como o `env_allow:`."""
    try:
        texto = (root / config.PROJECT_MEMORY_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for chave in ("typecheck", "lint", "test"):
        if m := re.search(rf"^\s*[-*]?\s*`?{chave}_command`?\s*:\s*`?([^`\n]+?)`?\s*$", texto, re.M | re.I):
            out[chave] = m.group(1).strip()
    return out


_TSC = re.compile(r"^(.+?)[(:](\d+)[,:](\d+)\)?:?\s*-?\s*error\s+(TS\d+):\s*(.+)$")
_PYTEST = re.compile(r"^FAILED\s+(\S+?)::(\S+)(?:\s+-\s+(.*))?$")
_ESLINT_ARQ = re.compile(r"^(\S.*\.(?:[jt]sx?|mjs|cjs|vue|svelte))$")
_ESLINT_ERR = re.compile(r"^\s+(\d+):(\d+)\s+error\s+(.+?)(?:\s{2,}(\S+))?$")


def erros_de(chave: str, comando: str, saida: str) -> list[dict]:
    """Um achado por erro reconhecido; saída que ninguém entende vira um card só, com a cauda."""
    achados, arquivo_eslint = [], None
    for linha in saida.splitlines():
        if m := _TSC.match(linha.strip()):
            arq, ln, cod, msg = m.group(1).strip(), int(m.group(2)), m.group(4), m.group(5).strip()
            achados.append({"titulo": f"{cod}: {msg}"[:140], "arquivo": arq, "linha": ln, "trecho": linha.strip(),
                            "chave": f"{cod} {msg}"})
        elif m := _PYTEST.match(linha.strip()):
            arq, teste, msg = m.group(1), m.group(2), (m.group(3) or "").strip()
            achados.append({"titulo": f"Teste falhando: {teste}"[:140], "arquivo": arq, "linha": None,
                            "trecho": linha.strip(), "chave": f"{teste}"})
        elif m := _ESLINT_ARQ.match(linha.strip()):
            arquivo_eslint = m.group(1)
        elif arquivo_eslint and (m := _ESLINT_ERR.match(linha)):
            regra = m.group(4) or ""
            achados.append({"titulo": f"Lint: {m.group(3).strip()}"[:140], "arquivo": arquivo_eslint,
                            "linha": int(m.group(1)), "trecho": linha.strip(), "chave": f"{regra} {m.group(3)}"})
        if len(achados) >= MAX_POR_COMANDO:
            break
    cards = [{
        "titulo": a["titulo"], "tipo": "bugfix", "area": "testes" if chave == "test" else _area(a["arquivo"]),
        "severidade": 1 if chave in ("test", "typecheck") else 2,
        "descricao": f"`{comando}` acusou este erro.", "verify_sugerido": comando,
        "evidencias": [{"arquivo": a["arquivo"].replace("\\", "/"), **({"linha": a["linha"]} if a["linha"] else {}),
                        "trecho": a["trecho"][:300]}],
        "impressao": impressao("bugfix", a["arquivo"].replace("\\", "/"), a["chave"])} for a in achados]
    if not cards:
        cards = [{"titulo": f"`{comando}` falhou", "tipo": "bugfix", "area": "testes" if chave == "test" else "backend",
                  "severidade": 1,
                  "descricao": f"O comando de {chave} do FORJA.md terminou com erro.", "verify_sugerido": comando,
                  "evidencias": [{"comando": comando, "saida": saida[-MAX_SAIDA:]}],
                  "impressao": impressao("bugfix", f"<{chave}>", comando)}]
    return cards


def _audit(root: Path, rodar) -> tuple[list[dict], list[str]]:
    achados, avisos = [], []
    if (root / "package-lock.json").is_file():
        _, saida = rodar("npm audit --json --omit=dev")  # exit != 0 é o normal quando há vulnerabilidade
        try:
            vulns = json.loads(saida[saida.find("{"):]).get("vulnerabilities") or {}
        except ValueError:
            vulns = {}
            avisos.append("npm audit não devolveu JSON (sem rede?).")
        for nome, v in vulns.items():
            if v.get("severity") not in ("high", "critical"):
                continue
            via = [x.get("title") for x in v.get("via") or [] if isinstance(x, dict) and x.get("title")]
            achados.append({
                "titulo": f"{nome}: vulnerabilidade {v['severity']}"[:140], "tipo": "seguranca", "area": "infra",
                "severidade": 1, "descricao": "; ".join(via[:3]) or "Apontada pelo npm audit.",
                "verify_sugerido": "npm audit --omit=dev --audit-level=high",
                "evidencias": [{"comando": "npm audit", "saida": json.dumps(
                    {"pacote": nome, "severidade": v["severity"], "faixa": v.get("range"),
                     "correcao": v.get("fixAvailable")}, ensure_ascii=False)}],
                "impressao": impressao("seguranca", "package-lock.json", f"{nome} {v['severity']}")})
    if (root / "requirements.txt").is_file():
        if shutil.which("pip-audit"):
            _, saida = rodar("pip-audit -r requirements.txt -f json")
            try:
                dados = json.loads(saida[min(i for i in (saida.find("{"), saida.find("["), len(saida)) if i >= 0):])
                deps = dados.get("dependencies", []) if isinstance(dados, dict) else dados  # formato mudou entre versões
            except ValueError:
                deps = []
                avisos.append("pip-audit não devolveu JSON.")
            for d in deps:
                for v in d.get("vulns") or []:
                    achados.append({
                        "titulo": f"{d['name']} {d.get('version', '')}: {v.get('id')}"[:140], "tipo": "seguranca",
                        "area": "infra", "severidade": 1,
                        "descricao": f"Corrigido em: {', '.join(v.get('fix_versions') or []) or 'sem correção'}",
                        "evidencias": [{"comando": "pip-audit", "saida": json.dumps(v, ensure_ascii=False)[:MAX_SAIDA]}],
                        "impressao": impressao("seguranca", "requirements.txt", f"{d['name']} {v.get('id')}")})
        else:
            avisos.append("pip-audit não está instalado: dependências Python não foram auditadas.")
    return achados, avisos


def _varre(projeto: str, root: Path) -> None:
    from .shell import executa_do_projeto
    estado = _VARREDURAS[projeto]
    rodar = lambda cmd: executa_do_projeto(root, cmd, TIMEOUT_COMANDO)  # noqa: E731
    try:
        achados = _todos(root)
        estado["etapa"] = "comandos"
        cmds = comandos_do_projeto(root)
        if not cmds:
            estado["avisos"].append("Sem test_command/typecheck_command/lint_command no FORJA.md: testes, "
                                    "tipos e lint não foram rodados.")
        for chave, cmd in cmds.items():
            code, saida = rodar(cmd)
            if code != 0:
                achados += erros_de(chave, cmd, saida)
        estado["etapa"] = "dependências"
        audit, avisos = _audit(root, rodar)
        achados += audit
        estado["avisos"] += avisos
        vistas = set()
        for a in achados:
            card, novo = criar(projeto, a, "varredura-deterministica")
            vistas.add(a["impressao"])
            estado["criados"] += novo
            estado["encontrados"] += 1
        # Some da varredura = talvez resolvido. Só entre os tipos que esta varredura olhou de fato.
        olhados = {"todo"} | ({"bugfix"} if cmds else set()) | {"seguranca"}
        with db.session() as s:
            for i in s.scalars(select(db.Issue).where(db.Issue.projeto == projeto,
                                                      db.Issue.origem == "varredura-deterministica",
                                                      db.Issue.status.in_(("novo", "backlog")))):
                sumiu = i.tipo in olhados and i.impressao not in vistas
                if sumiu != i.sumiu:
                    i.sumiu = sumiu
                    _evento(i, "a varredura não achou mais: resolvido?" if sumiu else "apareceu de novo")
            s.commit()
    except Exception as e:  # varredura é trabalho de fundo: o erro aparece no board, não derruba nada
        estado["avisos"].append(f"A varredura parou: {type(e).__name__}: {e}")
    finally:
        estado.update(rodando=False, fim=time.time(), etapa="")


def varrer(pasta: str) -> dict:
    projeto = projeto_de(pasta)
    root = Path(projeto)
    with _TRAVA:
        if _VARREDURAS.get(projeto, {}).get("rodando"):
            return _VARREDURAS[projeto]
        _VARREDURAS[projeto] = {"rodando": True, "inicio": time.time(), "fim": None, "etapa": "TODOs",
                                "criados": 0, "encontrados": 0, "avisos": []}
    threading.Thread(target=_varre, args=(projeto, root), daemon=True, name="forja-board-varredura").start()
    return _VARREDURAS[projeto]


def estado_varredura(projeto: str) -> dict | None:
    return _VARREDURAS.get(projeto)


# ------------------------------------------------------------------ Iniciar e acompanhar

def _head(root: Path) -> str | None:
    from . import gitops
    if not gitops.is_repo(root):
        return None
    try:
        return gitops._ok(root, "git rev-parse HEAD").strip()[:40] or None
    except Exception:
        return None


def prompt_do_card(c: dict) -> str:
    """O que vai como 1ª mensagem da conversa: o prompt editado, ou um montado com as evidências."""
    partes = [c["prompt"].strip() or f"{c['titulo']}\n\n{c['descricao']}".strip()]
    ev = []
    for e in c["evidencias"] or []:
        if e.get("arquivo"):
            ev.append(f"- `{e['arquivo']}{':' + str(e['linha']) if e.get('linha') else ''}`"
                      + (f": {e['trecho']}" if e.get("trecho") else ""))
        elif e.get("saida"):
            ev.append(f"- saída de `{e.get('comando', 'comando')}`:\n```\n{str(e['saida'])[-1500:]}\n```")
    if ev and not c["prompt"].strip():
        partes.append("Evidências:\n" + "\n".join(ev))
    if c["verify_sugerido"]:
        partes.append(f"Pronto quando `{c['verify_sugerido']}` passar. Rode e mostre o resultado.")
    partes.append(f"(Card #{c['id']} do board do projeto.)")
    return "\n\n".join(partes)


def _escolha(modo: str) -> dict:
    from . import mobile
    ultima = mobile.defaults() or {}
    if modo == "maestro" and (config.MAESTRO_MODEL or {}).get("model"):
        ultima = {**ultima, **{k: config.MAESTRO_MODEL[k] for k in ("provider", "model")}}
    if not ultima.get("provider") or not ultima.get("model"):
        raise BoardError("O Forja ainda não sabe que modelo usar: mande uma mensagem em qualquer conversa "
                         "primeiro (o Iniciar usa o último modelo escolhido).")
    return {"provider": ultima["provider"], "model": ultima["model"],
            "permission": ultima.get("permission") or "manual", "effort": ultima.get("effort") or "medio"}


def _dispara(conv_id: int, kind: str, texto: str, escolha: dict) -> None:
    from .agent import RUNS, Run, RunRequest, active_run
    if active_run(conv_id):
        raise BoardError("A conversa deste card já está rodando.")
    run = Run(conv_id)
    RUNS[run.id] = run
    run.start(RunRequest(mode=kind, content=texto, **escolha))


def iniciar(issue_id: int, modo: str | None = None) -> dict:
    with db.session() as s:
        i = s.get(db.Issue, issue_id)
        if not i:
            raise BoardError(f"Card {issue_id} não existe.")
        if i.status == "novo":
            raise BoardError("Aceite o card (Backlog) antes de iniciar: o que está em Novo ainda não foi triado.")
        if i.status == "andamento" and i.conversa_id:
            raise BoardError("Este card já está em andamento.")
        card = _dict(i)
    modo = modo or card["modo_sugerido"]
    if modo not in ("agent", "maestro"):
        raise BoardError("Modo deve ser 'agent' ou 'maestro'.")
    escolha = _escolha(modo)
    with db.session() as s:
        c = db.Conversation(kind=modo, workspace=card["projeto"], title=card["titulo"][:200])
        s.add(c)
        s.commit()
        conv_id = c.id
    try:
        _dispara(conv_id, modo, prompt_do_card(card), escolha)
    except Exception:
        with db.session() as s:  # sem turno, a conversa recém-criada seria só uma órfã na barra lateral
            if conv := s.get(db.Conversation, conv_id):
                s.delete(conv)
                s.commit()
        raise
    with db.session() as s:
        i = s.get(db.Issue, issue_id)
        i.status, i.conversa_id, i.commit_inicio, i.commit = "andamento", conv_id, _head(Path(card["projeto"])), None
        _evento(i, f"iniciado no modo {'Maestro' if modo == 'maestro' else 'agente'} (conversa {conv_id})")
        s.commit()
        return _dict(i)


def reabrir(issue_id: int, comentario: str) -> dict:
    """Revisão reprovada: o comentário vira mensagem na MESMA conversa, e o card volta para andamento."""
    comentario = str(comentario or "").strip()
    if not comentario:
        raise BoardError("Diga o que falta: o comentário vira a próxima mensagem da conversa.")
    with db.session() as s:
        i = s.get(db.Issue, issue_id)
        if not i or not i.conversa_id:
            raise BoardError("Este card não tem conversa para continuar: use Iniciar.")
        conv = s.get(db.Conversation, i.conversa_id)
        if not conv:
            raise BoardError("A conversa deste card foi apagada: use Iniciar de novo.")
        kind = conv.kind or "agent"
    _dispara(i.conversa_id, kind, comentario, _escolha(kind))
    with db.session() as s:
        i = s.get(db.Issue, issue_id)
        i.status = "andamento"
        _evento(i, f"reaberto: {comentario[:200]}")
        s.commit()
        return _dict(i)


def _terminou(s, i: db.Issue) -> bool:
    from .agent import active_run
    if active_run(i.conversa_id):
        return False
    conv = s.get(db.Conversation, i.conversa_id)
    if not conv:
        return False
    if conv.kind == "maestro":  # Maestro: a funcionalidade dele concluída, não só um turno
        return any(f.status == "done" for f in s.scalars(select(db.Feature).where(
            db.Feature.conversation_id == conv.id)))
    return s.scalar(select(func.count(db.Message.id)).where(
        db.Message.conversation_id == conv.id, db.Message.role == "assistant")) > 0


def _roda_verify(issue_id: int, root: Path, comando: str) -> None:
    from .shell import executa_do_projeto
    try:
        code, saida = executa_do_projeto(root, comando, TIMEOUT_COMANDO)
        texto = f"verify {'passou' if code == 0 else f'FALHOU (exit {code})'}: `{comando}`"
    except Exception as e:
        texto = f"verify não rodou: {e}"
    with db.session() as s:
        if i := s.get(db.Issue, issue_id):
            _evento(i, texto)
            s.commit()


def acompanha() -> None:
    """Card em andamento cuja conversa terminou vai para Revisão, com o commit e o verify rodado. Barato:
    é chamado pelo /api/activity a cada poucos segundos."""
    verificar = []
    with db.session() as s:
        for i in s.scalars(select(db.Issue).where(db.Issue.status == "andamento", db.Issue.conversa_id.is_not(None))):
            if not _terminou(s, i):
                continue
            i.status = "revisao"
            head = _head(Path(i.projeto))
            i.commit = head if head and head != i.commit_inicio else None
            _evento(i, "conversa terminou: pronto para revisão" + (f" (commit {i.commit[:8]})" if i.commit else ""))
            if i.verify_sugerido:
                verificar.append((i.id, Path(i.projeto), i.verify_sugerido))
        s.commit()
    for args in verificar:
        threading.Thread(target=_roda_verify, args=args, daemon=True).start()

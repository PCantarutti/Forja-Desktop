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

from . import config, convencoes, db, workspace
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

def _vinculos() -> dict[str, str]:
    with db.session() as s:
        return {v.pasta: v.projeto for v in s.scalars(select(db.BoardVinculo))}


def _raiz_natural(p: Path) -> str:
    return workspace.normalize(str(_raiz_projeto(p.resolve())))


def projeto_de(pasta: str) -> str:
    """Chave do board da pasta: o vínculo da pasta (ou de uma pasta acima dela), senão a raiz do git
    acima da conversa. /projeto/back vinculada a /projeto: conversa em /projeto/back/src usa /projeto."""
    p = workspace.resolve(pasta).resolve()
    ligados = _vinculos()
    for d in (p, *p.parents):
        if (k := workspace.normalize(str(d))) in ligados:
            return ligados[k]
    return _raiz_natural(p)


def rel(projeto: str, caminho: Path | str) -> str:
    """Caminho do jeito que fica no card: relativo à raiz do board (pode ter ../ se a pasta vinculada
    estiver fora dela)."""
    import os
    return Path(os.path.relpath(Path(caminho).resolve(), Path(projeto))).as_posix()


def vinculadas(projeto: str) -> list[str]:
    return sorted(p for p, alvo in _vinculos().items() if alvo == projeto)


def sugestoes(projeto: str) -> list[str]:
    """Subpastas com repositório próprio (até 2 níveis) ainda sem vínculo: o back e o front de /projeto."""
    raiz, ligadas, out = Path(projeto), set(_vinculos()), []
    for d in [*raiz.glob("*/"), *raiz.glob("*/*/")]:
        if d.is_dir() and not d.name.startswith(".") and d.name != "node_modules" and (d / ".git").exists():
            k = workspace.normalize(str(d))
            if k not in ligadas and _raiz_natural(d) != projeto:
                out.append(k)
    return sorted(out)[:30]


def vincular(projeto: str, pasta: str) -> list[str]:
    """Liga `pasta` ao board `projeto`. O que a pasta tinha no board próprio (cards e vínculos) vem junto,
    com os caminhos das evidências refeitos a partir da raiz nova."""
    alvo = workspace.normalize(str(workspace.resolve(pasta)))
    projeto = workspace.normalize(projeto)
    if alvo == projeto:
        raise BoardError("Essa já é a pasta do board.")
    if projeto_de(projeto) != projeto:
        raise BoardError("Esse board já está vinculado a outro: vincule a pasta ao board principal.")
    antigo = projeto_de(alvo)
    with db.session() as s:
        s.merge(db.BoardVinculo(pasta=alvo, projeto=projeto))
        for v in s.scalars(select(db.BoardVinculo).where(db.BoardVinculo.projeto == alvo)):
            v.projeto = projeto  # a pasta era board de outras: elas vêm junto
        if antigo != projeto:
            for i in s.scalars(select(db.Issue).where(db.Issue.projeto == antigo)):
                i.projeto = projeto
                i.evidencias = [{**e, "arquivo": rel(projeto, Path(antigo) / e["arquivo"])} if e.get("arquivo") else e
                                for e in i.evidencias or []]
                _evento(i, f"veio do board de {Path(antigo).name} (pasta vinculada)")
        s.commit()
    return vinculadas(projeto)


def desvincular(pasta: str) -> None:
    """A pasta volta a ter board próprio (vazio): os cards ficam no board onde já estão."""
    with db.session() as s:
        if v := s.get(db.BoardVinculo, workspace.normalize(pasta)):
            s.delete(v)
            s.commit()


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


def pega(issue_id: int) -> dict:
    with db.session() as s:
        if not (i := s.get(db.Issue, issue_id)):
            raise BoardError(f"Card {issue_id} não existe (foi apagado?).")
        return _dict(i)


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

def _abs(base: Path, arquivo: str) -> str:
    """Caminho absoluto normalizado: é ele que entra na impressão digital, para o card não duplicar quando
    a pasta passa a ser varrida a partir de outra raiz (vínculo de board)."""
    return workspace.normalize(str((base / arquivo).resolve())).lower()


def _todos(root: Path, projeto: str | None = None) -> list[dict]:
    from .codebusca import EXTS
    from .codigo import _analisaveis
    projeto = projeto or workspace.normalize(str(root))
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
        caminho = rel(projeto, p)
        for n, linha in enumerate(linhas, 1):
            m = TODO_RE.search(linha)
            if not m:
                continue
            marca, texto = m.group(1).upper(), m.group(2).strip().rstrip("*/-> ").strip()
            titulo = f"{marca}: {texto}"[:140] if texto else f"{marca} em {caminho}:{n}"
            achados.append({
                "titulo": titulo, "tipo": "todo", "area": _area(caminho), "severidade": 2 if marca == "FIXME" else 3,
                "descricao": f"Comentário {marca} deixado no código.",
                "evidencias": [{"arquivo": caminho, "linha": n, "trecho": linha.strip()[:300]}],
                "impressao": impressao("todo", _abs(root, str(p)), texto or linha)})
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


def erros_de(chave: str, comando: str, saida: str, base: Path | None = None, projeto: str | None = None) -> list[dict]:
    """Um achado por erro reconhecido; saída que ninguém entende vira um card só, com a cauda. `base` é a
    pasta onde o comando rodou (os caminhos da saída são relativos a ela)."""
    onde = (lambda a: rel(projeto or str(base), base / a)) if base else (lambda a: a.replace("\\", "/"))
    marca = (lambda a: _abs(base, a)) if base else (lambda a: a.replace("\\", "/"))
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
        "evidencias": [{"arquivo": onde(a["arquivo"]), **({"linha": a["linha"]} if a["linha"] else {}),
                        "trecho": a["trecho"][:300]}],
        "impressao": impressao("bugfix", marca(a["arquivo"]), a["chave"])} for a in achados]
    if not cards:
        cards = [{"titulo": f"`{comando}` falhou", "tipo": "bugfix", "area": "testes" if chave == "test" else "backend",
                  "severidade": 1,
                  "descricao": f"O comando de {chave} do FORJA.md terminou com erro.", "verify_sugerido": comando,
                  "evidencias": [{"comando": comando, "saida": saida[-MAX_SAIDA:]}],
                  "impressao": impressao("bugfix", f"{base or ''}<{chave}>", comando)}]
    return cards


def _audit(root: Path, rodar) -> tuple[list[dict], list[str]]:
    lock, req = _abs(root, "package-lock.json"), _abs(root, "requirements.txt")
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
                "impressao": impressao("seguranca", lock, f"{nome} {v['severity']}")})
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
                        "impressao": impressao("seguranca", req, f"{d['name']} {v.get('id')}")})
        else:
            avisos.append("pip-audit não está instalado: dependências Python não foram auditadas.")
    return achados, avisos


def _pastas_do_board(projeto: str, root: Path) -> tuple[list[Path], list[Path]]:
    """(onde procurar TODO, onde rodar comandos e auditoria). A raiz sempre; as vinculadas também, menos
    as que a varredura da raiz já cobre (subpasta comum; repo aninhado numa raiz que NÃO é repo)."""
    ligadas = [Path(v) for v in vinculadas(projeto) if Path(v).is_dir()]
    raiz_repo = (root / ".git").exists()
    fora = [v for v in ligadas if not v.resolve().is_relative_to(root.resolve()) or (raiz_repo and (v / ".git").exists())]
    return [root, *fora], [root, *ligadas]


def _varre(projeto: str, root: Path) -> None:
    from .shell import executa_do_projeto
    estado = _VARREDURAS[projeto]
    try:
        achados = []
        pastas_todo, pastas_cmd = _pastas_do_board(projeto, root)
        for base in pastas_todo:
            achados += _todos(base, projeto)
        estado["etapa"] = "comandos"
        cmds_todos = 0
        for base in pastas_cmd:
            rodar = lambda cmd, base=base: executa_do_projeto(base, cmd, TIMEOUT_COMANDO)  # noqa: E731
            cmds = comandos_do_projeto(base) or convencoes.comandos(base)  # FORJA.md manda; senão o detectado (E14)
            cmds_todos += len(cmds)
            for chave, cmd in cmds.items():
                code, saida = rodar(cmd)
                if code != 0:
                    achados += erros_de(chave, cmd, saida, base, projeto)
            estado["etapa"] = "dependências"
            audit, avisos = _audit(base, rodar)
            achados += audit
            estado["avisos"] += avisos
        cmds = cmds_todos
        if not cmds:
            estado["avisos"].append("Nenhum comando de teste, tipos ou lint (nem no package.json/pyproject, nem "
                                    "test_command no FORJA.md): só TODOs e dependências foram varridos.")
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
    if c["tipo"] == "visual":
        partes.append("Este card é VISUAL. Antes de mudar qualquer coisa, suba o app se precisar (serve_start), "
                      "abra a tela afetada no navegador do Forja (browser_navigate) e tire um browser_screenshot: é o "
                      "ANTES. Depois de corrigir, recarregue a mesma tela e tire outro: é o DEPOIS. Os dois prints "
                      "vão para o card, para a revisão comparar.")
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


def iniciar(issue_id: int, modo: str | None = None, permissao: str | None = None, quem: str = "") -> dict:
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
    if permissao:  # execução automática (board_auto): sem ninguém para aprovar, é o modo Automático
        escolha["permission"] = permissao
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
        _evento(i, f"iniciado{quem} no modo {'Maestro' if modo == 'maestro' else 'agente'} (conversa {conv_id})")
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


FERRAMENTAS_DE_PRINT = ("browser_screenshot", "visual_review", "browser_validate")


def _prints(s, conv_id: int) -> list[dict]:
    """Imagens que as ferramentas de navegador anexaram na conversa, em ordem."""
    out = []
    for m in s.scalars(select(db.Message).where(db.Message.conversation_id == conv_id, db.Message.role == "tool",
                                                db.Message.name.in_(FERRAMENTAS_DE_PRINT)).order_by(db.Message.id)):
        out += [a for a in (m.meta or {}).get("attachments") or [] if a.get("kind") == "image"]
    return out


def _anexa_prints(s, i: db.Issue) -> None:
    """Primeiro e último print da conversa viram o ANTES e o DEPOIS do card (troca os de uma rodada anterior)."""
    prints = _prints(s, i.conversa_id)
    if not prints:
        return
    escolhidos = [("antes", prints[0]), ("depois", prints[-1])] if len(prints) > 1 else [("print", prints[0])]
    i.evidencias = [e for e in i.evidencias or [] if not e.get("imagem")] + [
        {"imagem": p["path"], "conv": i.conversa_id, "rotulo": r} for r, p in escolhidos]
    _evento(i, f"{len(escolhidos)} print(s) da conversa anexado(s) ao card")


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
            _anexa_prints(s, i)
            if i.verify_sugerido:
                verificar.append((i.id, Path(i.projeto), i.verify_sugerido))
        s.commit()
    for args in verificar:
        threading.Thread(target=_roda_verify, args=args, daemon=True).start()



# ------------------------------------------------------------------ a IA cria card (E15-A, item A)

MAX_CARDS_POR_CONVERSA = 20
TIPO_APELIDO = {"bug": "bugfix", "fix": "bugfix", "erro": "bugfix", "defeito": "bugfix", "melhoria": "improvement",
                "refactor": "improvement", "refatoracao": "improvement", "refatoração": "improvement",
                "performance": "improvement", "ideia": "feature", "funcionalidade": "feature", "ui": "visual",
                "ux": "visual", "layout": "visual", "acessibilidade": "visual", "security": "seguranca",
                "segurança": "seguranca", "vulnerabilidade": "seguranca"}
PERTO = 2  # linhas: card aberto no mesmo arquivo a esta distância é o mesmo problema


def _mini(card: dict) -> dict:
    """O card como o chat desenha (CardNoChat)."""
    return {**{k: card[k] for k in ("id", "titulo", "tipo", "area", "severidade", "status", "origem", "projeto")},
            "evidencia": card["evidencias"][0] if card["evidencias"] else None}


def _perto(projeto: str, arquivo: str, linha: int | None) -> dict | None:
    """Card ainda não concluído no mesmo arquivo e quase na mesma linha. A impressão digital sozinha não
    bastou: a IA aponta a linha do `def` numa vez e a do `return` na outra, e o card repetia."""
    if not linha:
        return None
    with db.session() as s:
        for i in s.scalars(select(db.Issue).where(db.Issue.projeto == projeto, db.Issue.status != "concluido")):
            for e in i.evidencias or []:
                if e.get("arquivo") == arquivo and e.get("linha") and abs(int(e["linha"]) - linha) <= PERTO:
                    return _dict(i)
    return None
_CRIADOS: dict[int, int] = {}  # conversa -> cards que o agente já criou nela


def _trecho_confere(linhas: list[str], linha: int | None, trecho: str) -> int | None:
    """Linha onde o trecho está de verdade (perto da informada, ou em qualquer lugar sem linha). É a trava
    contra card inventado: modelo pequeno "vê" bug em linha que não existe."""
    alvo = _normaliza(trecho)
    if not alvo:
        return linha
    faixa = range(max(0, linha - 4), min(len(linhas), linha + 3)) if linha else range(len(linhas))
    return next((n + 1 for n in faixa if alvo in _normaliza(linhas[n]) or _normaliza(linhas[n]) and
                 _normaliza(linhas[n]) in alvo and len(_normaliza(linhas[n])) > 8), None)


def board_card(root: Path, args: dict) -> str:
    from .sessoes import CONV
    from .tools import ToolError, resolve_leitura
    projeto = projeto_de(str(root))
    conv = CONV.get()
    if conv is not None and _CRIADOS.get(conv, 0) >= MAX_CARDS_POR_CONVERSA:
        raise ToolError(f"Já foram criados {MAX_CARDS_POR_CONVERSA} cards nesta conversa: pare e resuma o que achou.")
    tipo = str(args.get("tipo") or "bugfix").strip().lower()
    tipo = TIPO_APELIDO.get(tipo, tipo)  # na validação o modelo mandou "bug", "melhoria"… e a chamada caía
    if tipo not in TIPOS:
        raise ToolError(f"tipo deve ser um de: {', '.join(TIPOS)}.")
    arquivo = str(args.get("arquivo") or "").strip()
    if not arquivo:
        raise ToolError("Card sem evidência não entra no board: informe 'arquivo' (e 'linha' e 'trecho').")
    try:
        caminho = resolve_leitura(root, arquivo)
    except Exception as e:
        raise ToolError(f"Arquivo não encontrado: '{arquivo}' ({e}). Confirme o caminho com tree ou glob.") from None
    if not caminho.exists():
        raise ToolError(f"Arquivo não encontrado: '{arquivo}'. Confirme o caminho com tree ou glob.")
    if not caminho.is_file():
        raise ToolError(f"'{arquivo}' não é um arquivo. Aponte o arquivo exato do problema.")
    linhas = caminho.read_text(encoding="utf-8", errors="replace").splitlines()
    linha = int(args["linha"]) if str(args.get("linha") or "").strip().isdigit() else None
    if linha is not None and not 1 <= linha <= len(linhas):
        raise ToolError(f"'{arquivo}' tem {len(linhas)} linhas; a linha {linha} não existe. Leia o arquivo de novo.")
    trecho = str(args.get("trecho") or "").strip()
    if trecho:
        achada = _trecho_confere(linhas, linha, trecho)
        if achada is None:
            raise ToolError("O trecho informado não está no arquivo" + (f" perto da linha {linha}" if linha else "")
                            + ". Copie o trecho exatamente como ele aparece (leia o arquivo com read_file).")
        linha = achada
    elif linha:
        trecho = linhas[linha - 1].strip()
    else:
        raise ToolError("Informe 'linha' ou 'trecho': a evidência precisa apontar onde está o problema.")
    caminho_card = rel(projeto, caminho)
    from .tools import EXTRA, REGISTRY
    verify = str(args.get("verify_sugerido") or "").strip()
    if verify.split("(")[0].strip() in {*REGISTRY, *EXTRA}:
        verify = ""  # na validação veio "browser_screenshot": nome de ferramenta, não comando; o verify falharia
    try:
        sev = int(args.get("severidade") or 2)
    except (TypeError, ValueError):
        sev = 2
    parecido = _perto(projeto, caminho_card, linha)
    if parecido:
        return {"text": f"Já existe o card #{parecido['id']} ({parecido['status']}) nesse ponto do código "
                        f"({parecido['titulo']}): não criei outro.", "board_card": _mini(parecido)}
    area = str(args.get("area") or "").strip().lower()
    dados = {"titulo": args.get("titulo"), "tipo": tipo, "area": area if area in AREAS else _area(caminho_card),
             "severidade": sev if sev in (1, 2, 3) else 2, "descricao": args.get("descricao") or "",
             "prompt": args.get("prompt") or "", "verify_sugerido": verify,
             "evidencias": [{"arquivo": caminho_card, "linha": linha, "trecho": trecho[:300]}],
             # a linha REAL do arquivo, não o trecho que o modelo copiou (ele copia de jeitos diferentes)
             "impressao": impressao(tipo, _abs(root, str(caminho)), linhas[linha - 1] if linha else trecho)}
    try:
        card, novo = criar(projeto, dados, "varredura-ia")
    except BoardError as e:
        raise ToolError(str(e)) from None
    mini = _mini(card)
    if not novo:
        return {"text": f"Já existe o card #{card['id']} ({card['status']}) para isso: não criei outro.",
                "board_card": mini}
    if conv is not None:
        _CRIADOS[conv] = _CRIADOS.get(conv, 0) + 1
        with db.session() as s:
            i = s.get(db.Issue, card["id"])
            _evento(i, f"criado pela IA na conversa {conv}")
            s.commit()
    return {"text": f"Card #{card['id']} criado na coluna Novo do board ({Path(projeto).name}): "
                    f"{card['titulo']} — {caminho_card}:{linha}. O usuário decide se aceita.",
            "board_card": mini}


# Interruptor do board: com board_card desligado, o agente não cria card sozinho e a ferramenta nem entra no
# catálogo (nem no prompt). Pedido explícito (skill /board, "Pedir à IA") libera só naquela conversa.
CHAVE_DESLIGADO = "board_card_desligado"
MARCA_PEDIDO = "[Pedido do board]"


def board_card_ligado(projeto: str) -> bool:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE_DESLIGADO)
        return projeto not in ((linha.value if linha else None) or [])


def define_board_card(projeto: str, ligado: bool) -> bool:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE_DESLIGADO)
        atual = set((linha.value if linha else None) or [])
        atual = atual - {projeto} if ligado else atual | {projeto}
        s.merge(db.AppSetting(key=CHAVE_DESLIGADO, value=sorted(atual)))
        s.commit()
    return ligado


def _pedido_na_conversa(conv_id: int) -> bool:
    """O usuário pediu cards nesta conversa: chamou /board ou /skill:board, ou ela nasceu do Pedir à IA."""
    with db.session() as s:
        falas = s.scalars(select(db.Message.content).where(db.Message.conversation_id == conv_id,
                                                           db.Message.role == "user"))
        return any(f and (f.lstrip().startswith(("/board", MARCA_PEDIDO)) or "/skill:board" in f) for f in falas)


def _disponivel() -> bool:
    from .sessoes import CONV
    try:
        projeto = projeto_de(str(workspace.root()))
    except Exception:
        return False
    if board_card_ligado(projeto):
        return True
    conv = CONV.get()
    return conv is not None and _pedido_na_conversa(conv)


def _registra():
    from .tools import Tool, _obj, register
    register(Tool(
        "board_card",
        "Cria um card no board do projeto (coluna Novo, para o usuário triar): um bug, melhoria, ideia de "
        "feature ou problema visual que você CONFIRMOU lendo o código. Exige evidência real: o arquivo, a linha "
        "e o trecho copiado exatamente como está (o Forja confere e recusa se não bater). Não crie card para "
        "suposição, nem repita um que já existe.",
        _obj({"titulo": {"type": "string", "description": "Curto e específico: o que está errado ou o que fazer"},
              "tipo": {"type": "string", "enum": list(TIPOS)},
              "arquivo": {"type": "string", "description": "Caminho do arquivo onde está o problema"},
              "linha": {"type": "integer", "description": "Linha do problema"},
              "trecho": {"type": "string", "description": "O código daquela linha, copiado exatamente"},
              "descricao": {"type": "string", "description": "Por que é um problema e o impacto"},
              "severidade": {"type": "integer", "description": "1 alta, 2 média, 3 baixa"},
              "area": {"type": "string", "enum": list(AREAS)},
              "prompt": {"type": "string", "description": "Pedido pronto para outra IA resolver: contexto, "
                                                          "arquivos e critério de aceite"},
              "verify_sugerido": {"type": "string", "description": "Comando de TERMINAL que prova que ficou pronto "
                                                                   "(ex.: pytest -q). Vazio se não houver"}},
             ["titulo", "tipo", "arquivo"]),
        board_card, available=_disponivel))


_registra()


# ------------------------------------------------------------------ "Pedir à IA" (E15-A, item B)

FOCOS = {
    "bugs": "bugs prováveis: erro de lógica, caso não tratado (vazio, nulo, erro de rede), condição invertida, "
            "exceção engolida, recurso que não é liberado",
    "melhorias": "melhorias: código duplicado, função grande demais, nome enganoso, código morto (ninguém importa), "
                 "desempenho óbvio",
    "features": "ideias de feature que o projeto claramente pede: rota sem tela, botão ou link sem ação, formulário "
                "sem validação, fluxo pela metade",
    "visual": "problemas visuais no código de interface: layout que quebra em tela pequena, texto sem contraste, "
              "estado de carregamento ou vazio faltando, acessibilidade básica (label, alt, foco)",
    "tudo": "bugs, melhorias, ideias de feature e problemas visuais",
}
MAX_CARDS_PEDIDO = 10


def pedido_ia(projeto: str, foco: str, subpasta: str = "") -> str:
    if foco not in FOCOS:
        raise BoardError(f"Foco deve ser um de: {', '.join(FOCOS)}.")
    with db.session() as s:
        abertos = [i.titulo for i in s.scalars(select(db.Issue).where(
            db.Issue.projeto == projeto, db.Issue.status.not_in(("concluido", "rejeitado"))).order_by(db.Issue.id.desc())
            .limit(40))]
        rejeitados = [(i.titulo, i.motivo_rejeicao or "sem motivo") for i in s.scalars(select(db.Issue).where(
            db.Issue.projeto == projeto, db.Issue.status == "rejeitado").order_by(db.Issue.updated_at.desc()).limit(15))]
    partes = [
        f"{MARCA_PEDIDO} Varredura do projeto para o board. Procure {FOCOS[foco]}"
        + (f", só dentro de `{subpasta}`" if subpasta else "") + ".",
        f"Para cada achado que você CONFIRMAR lendo o código, crie um card com a ferramenta board_card: título "
        f"curto, tipo, arquivo, linha e o trecho copiado exatamente, descrição do impacto e um prompt pronto para "
        f"outra IA resolver (contexto, arquivos, critério de aceite), mais o verify_sugerido quando houver um "
        f"comando que prove. No máximo {MAX_CARDS_PEDIDO} cards: prefira os mais importantes.",
        "Regras: NÃO altere nenhum arquivo e não rode comando que mude algo; isto é só leitura. Comece por tree e "
        "code_search, leia os trechos com read_file ou ast, e use explore para áreas grandes. Nada de card para "
        "suposição ou gosto pessoal.",
    ]
    if abertos:
        partes.append("Já existem estes cards abertos (não repita):\n" + "\n".join(f"- {t}" for t in abertos))
    if rejeitados:
        partes.append("O usuário REJEITOU estes antes (não proponha nada parecido):\n"
                      + "\n".join(f"- {t} ({m.replace('_', ' ')})" for t, m in rejeitados))
    partes.append("No fim, liste os cards que você criou, um por linha.")
    return "\n\n".join(partes)


def pedir_ia(pasta: str, foco: str, subpasta: str = "") -> dict:
    """Abre uma conversa de agente no projeto que procura e cria os cards. Permissão manual: qualquer escrita
    pede aprovação, então uma IA "ajudando demais" não mexe no código sem o usuário ver."""
    projeto = projeto_de(pasta)
    texto = pedido_ia(projeto, foco, str(subpasta or "").strip())
    escolha = {**_escolha("agent"), "permission": "manual"}
    with db.session() as s:
        c = db.Conversation(kind="agent", workspace=projeto, title=f"Board: procurar {foco}")
        s.add(c)
        s.commit()
        conv_id = c.id
    try:
        _dispara(conv_id, "agent", texto, escolha)
    except Exception:
        with db.session() as s:
            if c := s.get(db.Conversation, conv_id):
                s.delete(c)
                s.commit()
        raise
    return {"conversa_id": conv_id}

"""Preferências aprendidas do projeto (E14, partes 2 a 4).

O estado fica em `.forja/knowledge/aprendido.json` (candidatas e regras, com evidências e contador) e as
regras aparecem no `convencoes.md`, num bloco "Aprendido" entre marcadores. Apagar ou editar uma linha do
bloco vale: na próxima leitura a regra some ou muda de texto.

- Sinais: verify que falhou por lint/tipo (o código da regra), critério reprovado pelo revisor (E8) e
  correção explícita no chat ("não use classes", "sempre async/await").
- Candidata vira regra com PROMOVE ocorrências; correção explícita do usuário já é a confirmação.
- Cada regra tem área (frontend, backend, testes, geral ou um glob): o contrato leva só as que casam com os
  arquivos da tarefa (`para_contrato`).
- Checagem: quando a regra tem uma automática (lint, tsc), ela é sugerida no plano; sem checagem, a regra vai
  ao revisor da E8 como critério.
"""
from __future__ import annotations

import fnmatch
import json
import re
import time
from pathlib import Path

ARQUIVO = ".forja/knowledge/aprendido.json"
MD = ".forja/knowledge/convencoes.md"
INICIO, FIM = "<!-- forja:aprendido -->", "<!-- /forja:aprendido -->"
PROMOVE = 3
MAX_EVIDENCIAS = 5
FRONT = (".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte", ".css", ".scss", ".html")
TESTE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_|\.(test|spec)\.")

# Correção explícita no chat. Curta de propósito: "não use classes", "sempre use async/await", "evite any".
CORRECAO = re.compile(r"\b(n[ãa]o use|nunca use|sempre use|use sempre|evite usar|evite|prefira)\s+([^.!?\n]{3,80})", re.I)

# Linhas de erro de lint/tipo → (ferramenta, código). O código é o que se repete entre tarefas.
LINT = [
    ("tsc", re.compile(r"error (TS\d{4})")),
    ("ruff", re.compile(r"\b([A-Z]{1,3}\d{3,4})\b")),
    ("eslint", re.compile(r"\s(?:error|warning)\s+.+?\s{2,}(@?[\w-]+(?:/[\w-]+)?)\s*$", re.M)),
    ("mypy", re.compile(r"error: .+\[([a-z-]+)\]")),
]
CHECAGEM = {"tsc": "npx tsc --noEmit", "mypy": "mypy ."}


def area_de(arquivo: str) -> str:
    a = arquivo.replace("\\", "/").lower()
    if TESTE.search(a):
        return "testes"
    return "frontend" if a.endswith(FRONT) else "backend"


def casa(area: str, arquivos: list[str]) -> bool:
    """A regra desta área vale para a tarefa que mexe nestes arquivos?"""
    if area == "geral" or not arquivos:
        return True
    if any(c in area for c in "*?/"):
        return any(fnmatch.fnmatch(a.replace("\\", "/"), area) for a in arquivos)
    return any(area_de(a) == area for a in arquivos)


# ------------------------------------------------------------------ estado

def _carrega(root: Path) -> dict:
    try:
        d = json.loads((Path(root) / ARQUIVO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("candidatas", {})
    d.setdefault("regras", {})
    _sincroniza_md(Path(root), d)
    return d


def _grava(root: Path, d: dict) -> None:
    p = Path(root) / ARQUIVO
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        return
    _escreve_md(Path(root), d)


def _linha(rid: str, r: dict) -> str:
    chk = f"; checagem: `{r['checagem']}`" if r.get("checagem") else "; sem checagem (o revisor confere)"
    return f"- [{rid}|{r['area']}] {r['texto']} — {r['contador']}×, desde {r['desde']}{chk}"


def _escreve_md(root: Path, d: dict) -> None:
    p = root / MD
    try:
        atual = p.read_text(encoding="utf-8")
    except OSError:
        atual = "# Convenções do projeto\n"
    linhas = [INICIO, "## Aprendido",
              "_Regras que o Forja aprendeu neste projeto. Apague uma linha para esquecer a regra, ou edite o texto._", ""]
    linhas += [_linha(rid, r) for rid, r in sorted(d["regras"].items(), key=lambda x: int(x[0][1:]))] or ["(nenhuma ainda)"]
    linhas.append(FIM)
    bloco = "\n".join(linhas)
    novo = (atual[:atual.index(INICIO)] + bloco + atual[atual.index(FIM) + len(FIM):]
            if INICIO in atual and FIM in atual else atual.rstrip() + "\n\n" + bloco + "\n")
    if novo != atual:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(novo, encoding="utf-8")
        except OSError:
            pass


_LINHA = re.compile(r"^- \[(R\d+)\|([^\]]+)\] (.+?) — \d+×")


def _sincroniza_md(root: Path, d: dict) -> None:
    """O usuário apagou ou editou linhas do bloco: vale o que está no arquivo."""
    try:
        t = (root / MD).read_text(encoding="utf-8")
    except OSError:
        return
    if INICIO not in t or FIM not in t or not d["regras"]:
        return
    bloco = t[t.index(INICIO):t.index(FIM)]
    vistas = {m.group(1): (m.group(2), m.group(3)) for l in bloco.splitlines() if (m := _LINHA.match(l.strip()))}
    for rid in list(d["regras"]):
        if rid not in vistas:
            d["regras"].pop(rid)
            d.setdefault("esquecidas", []).append(rid)
        else:
            d["regras"][rid]["area"], d["regras"][rid]["texto"] = vistas[rid]


# ------------------------------------------------------------------ sinais

def _nova_regra(d: dict, chave: str, c: dict) -> str:
    n = max([int(r[1:]) for r in d["regras"]] + [0]) + 1
    rid = f"R{n}"
    d["regras"][rid] = {"chave": chave, "texto": c["texto"], "area": c["area"], "contador": c["contador"],
                        "desde": time.strftime("%Y-%m-%d"), "evidencias": c["evidencias"], "checagem": c.get("checagem")}
    return rid


def sinal(root: Path, chave: str, texto: str, area: str, evidencia: str, checagem: str | None = None,
          confirmada: bool = False) -> str | None:
    """Registra uma ocorrência. Devolve o id da regra quando ela nasce agora (ou já existia e contou de novo)."""
    root = Path(root)
    if not (root / ".forja").exists():
        return None  # projeto sem estado do Forja: nada a aprender (o agente não cria .forja/ sozinho)
    d = _carrega(root)
    for rid, r in d["regras"].items():
        if r.get("chave") == chave:
            r["contador"] += 1
            r["evidencias"] = (r["evidencias"] + [evidencia])[-MAX_EVIDENCIAS:]
            _grava(root, d)
            return rid
    c = d["candidatas"].setdefault(chave, {"texto": texto, "area": area, "contador": 0, "evidencias": [],
                                           "checagem": checagem})
    c["contador"] += 1
    c["evidencias"] = (c["evidencias"] + [evidencia])[-MAX_EVIDENCIAS:]
    rid = None
    if confirmada or c["contador"] >= PROMOVE:
        rid = _nova_regra(d, chave, d["candidatas"].pop(chave))
    _grava(root, d)
    return rid


def do_verify(root: Path, saida: str, arquivos: list[str], tarefa: str) -> list[str]:
    """Verify que falhou por lint/tipo: um sinal por código de regra (o mesmo código em tarefas diferentes
    é o que se repete)."""
    feitos = []
    area = area_de(arquivos[0]) if arquivos else "geral"
    for ferramenta, rx in LINT:
        codigos = {m.group(1) for m in rx.finditer(saida or "")}
        if ferramenta == "ruff" and "ruff" not in (saida or "").lower():
            continue
        for cod in sorted(codigos)[:5]:
            rid = sinal(root, f"lint:{ferramenta}:{cod}", f"Respeitar {cod} ({ferramenta})", area,
                        f"{tarefa}: verify falhou com {cod}", CHECAGEM.get(ferramenta))
            feitos.append(rid or f"{ferramenta}:{cod}")
    return feitos


def do_revisor(root: Path, criterios: list[dict], arquivos: list[str], tarefa: str) -> None:
    area = area_de(arquivos[0]) if arquivos else "geral"
    for c in criterios:
        if c.get("atendido") is False:
            chave = "criterio:" + re.sub(r"\W+", " ", c["criterio"].lower()).strip()[:80]
            sinal(root, chave, c["criterio"][:160], area, f"{tarefa}: {c.get('evidencia') or 'não atendido'}")


def do_chat(root: Path, texto: str) -> list[str]:
    """Correção explícita do usuário vira regra na hora (ele mesmo disse)."""
    novas = []
    for m in CORRECAO.finditer(texto or ""):
        frase = f"{m.group(1).capitalize()} {m.group(2).strip()}"
        chave = "chat:" + re.sub(r"\W+", " ", frase.lower()).strip()[:80]
        if rid := sinal(root, chave, frase, "geral", f"pedido no chat: \"{m.group(0)[:100]}\"", confirmada=True):
            novas.append(rid)
    return novas


# ------------------------------------------------------------------ uso

def regras(root: Path, arquivos: list[str] | None = None) -> list[dict]:
    d = _carrega(Path(root)) if (Path(root) / ARQUIVO).exists() else {"regras": {}}
    return [{"id": rid, **r} for rid, r in d["regras"].items() if arquivos is None or casa(r["area"], arquivos)]


def para_contrato(root: Path, arquivos: list[str], limite: int = 2000) -> str:
    """Só as regras da área dos arquivos da tarefa, até `limite` caracteres (proporcional à janela, E4)."""
    linhas = [f"- {r['texto']}" + (f" (checagem: `{r['checagem']}`)" if r.get("checagem") else "")
              for r in regras(root, arquivos)]
    texto = "\n".join(linhas)
    return texto[:limite] if texto else ""


def sem_checagem(root: Path, arquivos: list[str]) -> list[str]:
    """Regras que só o revisor (E8) pode conferir, como critérios extras."""
    return [f"(regra do projeto) {r['texto']}" for r in regras(root, arquivos) if not r.get("checagem")]


def sugestoes_de_verify(root: Path, arquivos: list[str], verify: str) -> list[str]:
    """Checagens que a área da tarefa tem e o verify ainda não roda. Só sugere (mudar config exige aprovação)."""
    return sorted({r["checagem"] for r in regras(root, arquivos) if r.get("checagem") and r["checagem"] not in (verify or "")})

"""Portão de qualidade da Maestro para projeto com tela (site, app web).

O pedido: mandar "crie um site" e a Maestro só entregar quando estiver funcionando, sem erro e com
visual coerente. Instrução no prompt não segura isso — num teste real ela validou por print, sem
build, e fechou funcionalidade com tarefa aberta. Então o que importa vira regra de código:

- **Erro no navegador vira tarefa sozinho.** `browser_validate` da Maestro com erro de console cria
  a tarefa de correção na funcionalidade (sem duplicar, com limite de rodadas).
- **Revisão visual por um modelo com visão** (`visual_review`): prints em desktop e mobile, julgados
  contra uma lista de conferência; o que ele reprovar vira tarefa.
- **Encerrar exige prova** (`faltas_para_entregar`, chamado pela session_note): depois que a
  funcionalidade entrou em validação, build passando (se o projeto tem build), `browser_validate` sem
  erro de console e revisão visual aprovada — ou indisponível, dito com todas as letras.
- **Guia visual** em .forja/knowledge/frontend.md antes do primeiro plano, e junto no contrato de
  cada Worker: é o que mantém tarefas diferentes com a mesma cara.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, db, llm, taskdb, workspace
from .tools import Tool, ToolError, register_extra

FRAMEWORKS = ("react", "vue", "svelte", "vite", "next", "nuxt", "@angular/core", "solid-js", "preact", "astro")
GUIA = ".forja/knowledge/frontend.md"
MAX_CORRECOES = 6        # tarefas de correção automáticas por funcionalidade (evita loop sem fim)
MAX_PAGINAS = 3          # páginas por revisão visual
TELAS = (("desktop", 1280, 720), ("mobile", 390, 844))
MARCA_AUTO = "[criada pelo Forja]"

CHECKLIST = (
    "Você revisa o VISUAL de uma página web pelos prints (desktop e mobile). Aponte só problema "
    "visível: elemento sobreposto ou cortado, texto estourando ou ilegível, contraste ruim, "
    "alinhamento e espaçamento inconsistentes, cores/fontes que destoam do resto, layout que quebra "
    "no mobile, área vazia ou conteúdo faltando, cara de página inacabada. Não comente código nem "
    "sugira funcionalidade nova.\n"
    "Responda em no máximo 10 linhas. A primeira é exatamente 'VEREDITO: ok' ou 'VEREDITO: ajustar'; "
    "depois, um problema por linha começando com '- ', dizendo onde (desktop/mobile, região da tela).")


def _pacote(root: Path) -> dict:
    try:
        return json.loads((root / "package.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def tem_tela(root: Path) -> bool:
    """Projeto web: package.json com framework de interface, ou um index.html servível."""
    pkg = _pacote(root)
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    if any(d in deps for d in FRAMEWORKS):
        return True
    return any((root / p).is_file() for p in ("index.html", "public/index.html", "src/index.html"))


EXTENSOES_DE_TELA = (".html", ".css", ".scss", ".tsx", ".jsx", ".vue", ".svelte", ".astro")


def plano_com_tela(tasks: list) -> bool:
    """O plano mexe em interface? Num site novo a pasta ainda está vazia quando a Maestro planeja, e
    olhar só o disco deixaria passar o primeiro plano — justamente o que define a cara do site."""
    for t in tasks if isinstance(tasks, list) else []:
        arquivos = ((t or {}).get("contract") or {}).get("relevant_files") or [] if isinstance(t, dict) else []
        if isinstance(arquivos, str):
            arquivos = arquivos.split(",")
        if any(str(a).strip().lower().endswith(EXTENSOES_DE_TELA) for a in arquivos):
            return True
    return False


def tem_build(root: Path) -> bool:
    return bool((_pacote(root).get("scripts") or {}).get("build"))


# ------------------------------------------------------------------ erro vira tarefa

def _feature_alvo(s, conv_id: int):
    """Onde entra a correção: a funcionalidade em validação, senão a ativa mais recente."""
    q = s.query(db.Feature).filter(db.Feature.conversation_id == conv_id, db.Feature.copiada_para.is_(None))
    return (q.filter(db.Feature.status == "validating").order_by(db.Feature.id.desc()).first()
            or q.filter(db.Feature.status == "active").order_by(db.Feature.id.desc()).first())


def tarefa_de_correcao(conv_id: int, titulo: str, problemas: list[str], contexto: str, tipo: str = "bugfix") -> str:
    """Cria (sem duplicar) a tarefa de correção. Devolve a frase para o resultado da ferramenta."""
    with db.session() as s:
        feat = _feature_alvo(s, conv_id)
        if not feat:
            return ""
        fid = feat.id
        auto = [t for t in s.query(db.Task).filter(db.Task.feature_id == fid)
                if MARCA_AUTO in ((t.contract or {}).get("context") or "")]
        aberta = next((t for t in auto if t.status in taskdb.OPEN and t.title == titulo), None)
        if aberta:
            return f"\n\n[Forja] Esses problemas já estão na {aberta.code}, aberta: execute-a com run_task."
        if len(auto) >= MAX_CORRECOES:
            return (f"\n\n[Forja] Esta funcionalidade já teve {len(auto)} rodadas de correção automática. "
                    "Não criei outra: pergunte ao usuário (ask_user) como seguir.")
    out = taskdb.create_feature(conv_id, "", "", [{
        "title": titulo,
        "contract": {"type": tipo, "goal": titulo, "context": f"{MARCA_AUTO} {contexto}",
                     "requirements": problemas[:taskdb.MAX_ITENS],
                     "acceptance_criteria": ["Os problemas listados em requisitos não aparecem mais."]}}], fid)
    code = out["tasks"][0]["code"]
    return f"\n\n[Forja] Criei a {code} para corrigir isso (funcionalidade {fid}). Execute com run_task e valide de novo."


def pos_validacao(conv_id: int, resultado: str, url: str) -> str:
    """Depois de um browser_validate da Maestro: erro de console vira tarefa."""
    m = re.search(r"ERROS DE CONSOLE: (\d+)\n(.*?)(?:\n\n|\Z)", resultado, re.S)
    if not m or m.group(1) == "0":
        return ""
    erros = [l.strip() for l in m.group(2).splitlines() if l.strip()][:10]
    return tarefa_de_correcao(conv_id, f"Corrigir erros de console em {url or 'a página'}", erros,
                              f"browser_validate em {url or 'a página'} mostrou {m.group(1)} erro(s) de console.")


# ------------------------------------------------------------------ revisão visual

async def _pergunta_a_visao(spec: dict, texto: str, anexos: list[dict]) -> str:
    from . import modelctl, uploads
    from .tools import vision_caps
    caps = vision_caps(await llm.capabilities(spec["provider"], spec["model"]),
                       db.get_model_setting(spec["model"])["vision"])
    if "vision" not in caps:
        raise ToolError(f"o modelo {spec['model']} não tem visão (em IA local, falta o projetor mmproj)")
    async for _ in modelctl.ensure(spec):  # local: sobe o revisor (a Maestro recarrega o dela depois)
        pass
    mensagens = [{"role": "system", "content": CHECKLIST}, uploads.user_message(texto, anexos)]
    resposta = ""
    async for tipo, valor in llm.chat_stream(spec["provider"], spec["model"], mensagens, None, config.NUM_CTX, "baixo"):
        if tipo == "content":
            resposta += valor
    return resposta.strip()


def _visao_carregada() -> dict:
    """Sem revisor configurado: o modelo local já carregado, se enxerga (no TaskBoard era o próprio Qwen3.6
    da Maestro, com mmproj, e a revisão saiu "indisponível"). Não troca modelo: usa o que está na VRAM."""
    try:
        from . import localai
        st = localai.status()
    except Exception:  # forja-web: sem IA local
        return {}
    return {"provider": config.LOCAL_PROVIDER["id"], "model": st["alias"]} if st.get("running") and st.get("vision") else {}


async def _visual_review(_root: Path, args: dict) -> dict:
    from . import browser
    conv = taskdb.CONV.get()
    spec = dict(getattr(config, "MAESTRO_VISUAL", {}) or {})
    urls = [u for u in (args.get("urls") or [])][:MAX_PAGINAS] if isinstance(args.get("urls"), list) else []
    if not urls:
        pagina = browser.current().active
        urls = [pagina.url] if pagina is not None and not pagina.is_closed() else []
    if not urls:
        raise ToolError("Informe 'urls' (as páginas a revisar), com o servidor no ar.")
    anexos, linhas = [], []
    for url in urls:
        await browser.navigate(_root, {"url": url})
        for nome, largura, altura in TELAS:
            foto = await browser.screenshot(_root, {"largura": largura, "altura": altura})
            anexos += foto["attachments"]
            linhas.append(f"{url} — {nome} {largura}x{altura}")
    if not spec.get("model"):
        spec = _visao_carregada()
    if not spec.get("model"):
        return {"text": ("REVISÃO VISUAL INDISPONÍVEL: nenhum modelo com visão em Configurações › Maestro › "
                         "Revisão visual. Os prints estão no chat para o usuário; o visual NÃO foi julgado."),
                "attachments": anexos}
    try:
        veredito = await _pergunta_a_visao(spec, "Prints:\n" + "\n".join(linhas), anexos)
    except (ToolError, llm.LLMError) as e:
        return {"text": f"REVISÃO VISUAL INDISPONÍVEL: {e}. O visual NÃO foi julgado.", "attachments": anexos}
    ok = veredito.upper().startswith("VEREDITO: OK")
    texto = f"Revisão visual ({spec['model']}):\n{veredito}"
    if not ok and conv is not None:
        problemas = [l.lstrip("- ").strip() for l in veredito.splitlines()[1:] if l.strip().startswith("-")]
        texto += tarefa_de_correcao(conv, "Ajustes visuais: " + ", ".join(urls)[:120],
                                    problemas or [veredito[:500]], "A revisão visual reprovou: " + ", ".join(urls),
                                    tipo="ui")
    return {"text": texto, "attachments": anexos}


VISUAL_REVIEW = register_extra(Tool(
    "visual_review",
    "Revisão visual da entrega: tira prints desktop e mobile das páginas e um modelo com visão julga "
    "(sobreposição, texto cortado, contraste, alinhamento, coerência, mobile). O que ele reprovar vira "
    "tarefa sozinho. Chame na validação da entrega de projeto com tela, com o servidor no ar.",
    {"type": "object", "properties": {
        "urls": {"type": "array", "items": {"type": "string"},
                 "description": f"Páginas a revisar (até {MAX_PAGINAS}); vazio = a aba atual"}}},
    _visual_review))


# ------------------------------------------------------------------ o que falta para entregar

def faltas_para_entregar(conv_id: int, desde, root: Path) -> list[str]:
    """O que ainda não foi provado, desde que a funcionalidade entrou em validação. [] = pode encerrar."""
    if not config.MAESTRO_BROWSER or not tem_tela(root):
        return []
    with db.session() as s:
        msgs = [(m.name, m.status, m.content or "", (m.meta or {}).get("arguments") or {})
                for m in s.query(db.Message).filter(db.Message.conversation_id == conv_id, db.Message.role == "tool",
                                                    db.Message.created_at >= desde).order_by(db.Message.id)]
    faltas = []
    if tem_build(root) and not any(n == "run_command" and st == "ok" and "exit code: 0" in c
                                   and "build" in str(a.get("command") or "") for n, st, c, a in msgs):
        faltas.append("rode o build (npm run build) e confira que ele passa")
    validacoes = [c for n, st, c, _ in msgs if n == "browser_validate" and st == "ok"]
    if not validacoes:
        faltas.append("abra cada página com browser_validate (e percorra os fluxos com browser_click/browser_type)")
    elif "ERROS DE CONSOLE: 0" not in validacoes[-1]:
        faltas.append("a última browser_validate ainda mostrou erros de console: corrija e valide de novo")
    revisoes = [c for n, st, c, _ in msgs if n == "visual_review" and st == "ok"]
    if not revisoes:
        faltas.append("rode visual_review nas páginas principais")
    elif not (revisoes[-1].split("\n", 1)[-1].upper().startswith("VEREDITO: OK")
              or "REVISÃO VISUAL INDISPONÍVEL" in revisoes[-1]):
        faltas.append("a revisão visual pediu ajustes: execute as tarefas criadas e rode visual_review de novo")
    return faltas


def guia_visual(root: Path) -> str:
    """Conteúdo do guia visual, ou '' (sem arquivo, ou só esqueleto)."""
    from .projstate import vazio
    try:
        texto = (root / GUIA).read_text("utf-8")
    except OSError:
        return ""
    return "" if vazio(texto) else texto

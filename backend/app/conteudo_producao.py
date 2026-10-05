"""E18 etapa 3: produção do vídeo pelo Claude Code (`claude -p`) no projeto de vídeo do usuário.

Uma produção = mensagem `assistant` (name="producao") na conversa da especificação, com o estado em
`meta["producao"]`. O pedido (roteiro + estilo + formato) é escrito em `<projeto>/.forja/producao/<id>/` e vai
pela entrada padrão do `claude -p` — nenhum texto do roteiro passa pela linha de comando.

Permissões, sem ninguém para aprovar de madrugada:
- `--permission-mode acceptEdits`: edita arquivos só dentro do projeto (e da pasta de estilos, via --add-dir);
- `--allowedTools`: leitura/escrita de arquivo e os comandos da lista da tela (npm run, npx remotion, python
  scripts...). Qualquer outra ferramenta ou comando é negado na hora e o Claude segue sem ele: nunca trava.

Uma produção por vez (é a mesma máquina que renderiza). Enquanto roda, o PC não dorme.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from . import conteudo, conteudo_roteiros, db, native
from .agent import _save
from .tools import ToolError

CHAVE = "producao"
NOME = "producao"
TETO = 3 * 3600          # segundos; um vídeo longo com render pode levar bem mais que um Short
GRAVAR_A_CADA = 4.0      # segundos entre gravações do estado (o log anda mais rápido que isso)
MAX_LOG = 300
FERRAMENTAS = ["Read", "Edit", "Write", "MultiEdit", "Glob", "Grep", "TodoWrite"]
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001

_RUNS: dict[int, dict] = {}
_TAREFAS: set[asyncio.Task] = set()
_TRAVA = threading.Lock()

PEDIDO = """Você vai produzir sozinho um vídeo completo e renderizado. Ninguém vai responder perguntas: decida e siga.

## O que produzir
- Roteiro aprovado: `{roteiro_json}` (cenas com `id`, `texto` e, quando houver, `visual` — a sugestão do que mostrar na tela). A narração usa o texto de cada cena EXATAMENTE
  como está, sem reescrever. Título, notícia e fontes também estão lá.
- Estilo: leia `{estilo_md}`{readme} e siga à risca (voz, legenda, visual, áudio).
- Formato: {formato_rotulo}, composição de {largura}x{altura}.

## Como
- Este diretório é o projeto Remotion. Antes de criar algo, veja como os vídeos anteriores foram feitos aqui
  (CLAUDE.md, README, scripts/ e src/) e reaproveite: narração, legendas, efeitos e componentes.
- Crie uma composição nova para este vídeo; não altere nem quebre as composições que já existem. Não edite
  arquivos de outras composições nem para exportar algo: para reaproveitar, importe o que já é exportado ou copie.
{midia}- Confira frames com `npx remotion still` antes do render final, salvando em `{frames}/` (nunca em `out/`);
  corrija texto cortado ou sobreposto.
- Renderize o vídeo final em `out/{slug}.mp4`.
- Se a pasta de estilos tiver um README com a tabela "Vídeos já feitos", acrescente este vídeo nela.
- Comandos de terminal permitidos (o resto é negado na hora, não insista): {comandos}.
  Rode cada um sozinho: sem `cd x &&`, sem `;` e sem pipe para comando fora da lista (`| tail`, `| head`...).
  Para ler ou procurar arquivos use as ferramentas Read, Glob e Grep, não o terminal.
- Comando negado é regra, não erro: siga sem ele (faça de outro jeito ou simplifique).

Na última linha da sua resposta final escreva só: VIDEO: out/{slug}.mp4
"""


# Com scripts/midia.py no projeto (trailers e capturas da Steam, imagens oficiais, cortes sem áudio), o Claude
# ilustra com material real em vez de só motion design. Sem ele, a regra antiga: nada da internet.
SEM_MIDIA = "- Não baixe nada da internet. Use o que já está no projeto (efeitos, fontes, logos) ou desenhe em código.\n"
MIDIA = """- Ilustre com material REAL quando ajudar (jogo, produto, lugar, pessoa pública), e motion design para números,
  gráficos, listas e texto. Para buscar mídia use SÓ `python scripts/midia.py` (veja `--help`); não baixe por outro meio:
  - `python scripts/midia.py steam "<nome do jogo>"`: trailer oficial (sem áudio) + capturas + capa da loja Steam;
  - `python scripts/midia.py nasa "<busca>"`: fotos e vídeos da biblioteca da NASA (missões, lançamentos, telescópios);
  - `python scripts/midia.py cortar <trailer> --de S --ate S`: trecho curto e mudo para a cena;
  - `python scripts/midia.py imagem <url> --pasta <assunto> --credito "<dono>"`: imagem de fonte oficial (blog, site,
    kit de imprensa da empresa); `youtube <url do canal oficial> --de S --ate S --credito "<dono>"` se estiver disponível.
  Tudo cai em `public/midia/<assunto>/` com `creditos.json`; use com `staticFile()` e `<OffthreadVideo muted>`/`<Img>`.
- Regras da mídia real: só fonte oficial (nunca vídeo de youtuber, streamer ou fã); cada trecho com até ~6 s; nunca o
  áudio original; crédito pequeno na tela enquanto aparece ("Trailer: <dono> / Steam") e todos os créditos no fim da
  descrição. Sem mídia oficial disponível (ex.: jogo só de console), siga com motion design: não invente nem improvise.
"""

# ------------------------------------------------------------------ ambiente

REVISAO = """Você vai REVISAR, sozinho, um vídeo que já foi produzido neste projeto. Ninguém vai responder perguntas.

## O vídeo
- Versão atual: `{video_atual}` (versão {versao_atual}). Pedido original: `{pedido_original}`; roteiro: `{roteiro_json}`.
- {retomada}Se não lembrar como ele foi feito, ache a composição que renderiza esse arquivo (tabela "Vídeos já feitos"
  do README de estilos e `src/Root.tsx`).

## O que mudar
{pedidos}

## Como
- Mude só o que foi pedido; o resto fica igual (inclusive a narração, salvo pedido explícito sobre ela).
- Altere os arquivos da composição DESTE vídeo; não edite arquivos de outras composições.
- Tempos são do vídeo final (em segundos); as imagens são o quadro naquele instante com a marcação em vermelho por cima.
- Confira os pontos pedidos com `npx remotion still`, salvando em `{frames}/` (nunca em `out/`).
- Renderize a nova versão em `out/{slug}.mp4`, sem sobrescrever a anterior.
- Comandos de terminal permitidos (o resto é negado na hora, não insista): {comandos}.
  Rode cada um sozinho: sem `cd x &&`, sem `;` e sem pipe para comando fora da lista. Para ler arquivos e imagens, use Read.

Na última linha da sua resposta final escreva só: VIDEO: out/{slug}.mp4
"""

USO_CHAVE = "conteudo_uso"   # AppSetting: último rate_limit_event que o Claude Code mandou


def _salvar_uso(info: dict) -> None:
    """Guarda o uso do plano (janela de 5 h e semanal) que o Claude Code informa durante cada execução."""
    janelas = {k: {"uso": float(v.get("utilization") or 0), "renova": v.get("resetsAt")}
               for k, v in (info.get("unifiedWindows") or {}).items() if isinstance(v, dict)}
    if not janelas:
        return
    dados = {"janelas": janelas, "status": info.get("status") or "", "excedente": bool(info.get("isUsingOverage")),
             "atualizado": datetime.now().isoformat(timespec="seconds")}
    with db.session() as s:
        s.merge(db.AppSetting(key=USO_CHAVE, value=dados))
        s.commit()


def uso() -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, USO_CHAVE)
        return dict(linha.value) if linha and isinstance(linha.value, dict) else {}


def _versao(texto: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", texto or "")
    return tuple(int(x) for x in m.groups()) if m else (0,)


def _candidatos() -> list[tuple[tuple[int, ...], str]]:
    """(versão, exe) de cada Claude Code do PC: o do npm (versão pelo --version) e os que o app Claude Desktop
    traz em %APPDATA%\\Claude*\\claude-code\\<versão>\\<hash>\\claude.exe (versão no caminho)."""
    out = []
    achado = shutil.which("claude") or ""
    if achado.lower().endswith((".cmd", ".ps1")) or (achado and not Path(achado).suffix):
        exe = Path(achado).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        achado = str(exe) if exe.is_file() else ""
    if achado and (achado.lower().endswith(".exe") or sys.platform != "win32"):
        try:
            r = subprocess.run([achado, "--version"], capture_output=True, timeout=20, **native.popen_kwargs())
            out.append((_versao(r.stdout.decode("utf-8", "replace")), achado))
        except (subprocess.TimeoutExpired, OSError):
            out.append(((0,), achado))
    appdata = os.environ.get("APPDATA")
    if appdata:
        for exe in Path(appdata).glob("Claude*/claude-code/*/*/claude.exe"):
            out.append((_versao(exe.parent.parent.name), str(exe)))
    return out


def achar_claude() -> str:
    """O Claude Code que a produção usa: o caminho dos Ajustes, se houver; senão o MAIS NOVO do PC.

    O do npm pode ficar velho (2.1.233 não roda o Opus 5.5, que pede 2.1.280+) enquanto o app Desktop traz um
    recente — e o caminho do app muda a cada atualização, então fixar à mão quebraria. O login é o mesmo
    (pasta da conta do Claude Code), então qualquer um deles serve."""
    escolhido = (conteudo.pastas().get("claude_cli") or "").strip()
    if escolhido:
        return escolhido if Path(escolhido).is_file() else ""
    candidatos = _candidatos()
    return max(candidatos)[1] if candidatos else ""


def regras_bash(comandos: list[str]) -> list[str]:
    """`npm run *` -> `Bash(npm run *)` e `PowerShell(npm run *)`.

    Glob, não prefixo: testado no Claude Code 2.1.233, `Bash(python scripts/:*)` NEGA `python scripts/x.py`
    (o prefixo casa por palavra inteira) e `Bash(python scripts/*)` deixa. E no Windows ele roda comando pela
    ferramenta PowerShell tanto quanto pela Bash: sem a regra das duas, metade dos comandos cai negada."""
    out = []
    for c in comandos:
        c = c.strip()
        if c:
            out += [f"Bash({c})", f"PowerShell({c})"]
    return out


def modelo_e_esforco(pastas: dict) -> list[str]:
    """--model e --effort escolhidos nos Ajustes (vazio = o padrão do Claude Code)."""
    a = []
    if pastas.get("claude_modelo"):
        a += ["--model", pastas["claude_modelo"]]
    if pastas.get("claude_esforco"):
        a += ["--effort", pastas["claude_esforco"]]
    return a


def argv(claude: str, pastas: dict) -> list[str]:
    a = [claude, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
         *modelo_e_esforco(pastas), "--allowedTools", *FERRAMENTAS, *regras_bash(pastas["comandos"])]
    if pastas.get("pasta_estilos"):
        a += ["--add-dir", pastas["pasta_estilos"]]
    return a


LOGIN_RE = re.compile(r"authenticat|oauth|/login|not logged|log in", re.I)
AVISO_LOGIN = ("O Claude Code não está logado (ou o login expirou). Abra um terminal, rode `claude`, faça o /login e "
               "depois use \"Testar Claude\" na aba Pastas.")


def ambiente_claude(pastas: dict | None = None) -> dict:
    """Ambiente do processo do Claude: sem os segredos do Forja e com a conta escolhida (CLAUDE_CONFIG_DIR).

    Pasta de conta própria = login próprio: dá para o Forja produzir com uma conta e o terminal usar outra."""
    pastas = pastas or conteudo.pastas()
    env = {k: v for k, v in os.environ.items() if not k.startswith("FORJA_") and k != "CLAUDE_CONFIG_DIR"}
    if conta := (pastas.get("claude_conta") or "").strip():
        env["CLAUDE_CONFIG_DIR"] = conta
    return env


def testar_claude() -> dict:
    """Pergunta mínima ao `claude -p`: confirma que existe e que o login vale (o que derruba a madrugada)."""
    claude = achar_claude()
    if not claude:
        return {"ok": False, "mensagem": "Claude Code não encontrado neste PC."}
    try:
        env = ambiente_claude()
        r = subprocess.run([claude, "-p", "--output-format", "stream-json", "--verbose", *modelo_e_esforco(conteudo.pastas())],
                           input="Responda só: OK".encode(),
                           capture_output=True, timeout=120, env=env, **native.popen_kwargs())
        d = {}
        for linha in r.stdout.decode("utf-8", "replace").splitlines():   # o uso do plano vem num evento à parte
            try:
                ev = json.loads(linha)
            except ValueError:
                continue
            if ev.get("type") == "rate_limit_event":
                _salvar_uso(ev.get("rate_limit_info") or {})
            elif ev.get("type") == "result":
                d = ev
        if not d:
            raise ValueError("sem evento result")
    except subprocess.TimeoutExpired:
        return {"ok": False, "mensagem": "O Claude Code não respondeu em 2 minutos."}
    except ValueError:
        return {"ok": False, "mensagem": "Resposta inesperada do Claude Code: " + r.stderr.decode("utf-8", "replace")[:200]}
    texto = str(d.get("result") or "")
    if d.get("is_error"):
        return {"ok": False, "mensagem": AVISO_LOGIN if LOGIN_RE.search(texto) else texto[:300]}
    conta = ""
    try:   # qual conta respondeu: com duas contas no PC é a primeira dúvida
        st = subprocess.run([claude, "auth", "status"], capture_output=True, timeout=30, env=env, **native.popen_kwargs())
        info = json.loads(st.stdout.decode("utf-8", "replace") or "{}")
        conta = f" com a conta {info.get('email')}" + (f" (plano {info['subscriptionType']})" if info.get("subscriptionType") else "")
        modelo = (d.get("modelUsage") or {}) and next(iter(d["modelUsage"]), "")
        if modelo:
            conta += f", modelo {modelo}"
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return {"ok": True, "mensagem": f"Claude Code respondendo{conta}."}


def _slug(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:50] or "video"


def _acordado(sim: bool) -> None:
    """Mantém o PC acordado enquanto a thread da produção vive (o estado é por thread no Windows)."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if sim else 0))


# ------------------------------------------------------------------ estado

def _publico(run: dict) -> dict:
    return {k: v for k, v in run.items() if not k.startswith("_") and k not in ("cancelar", "t0", "message_id", "conv_id")}


def _dict(m: db.Message) -> dict:
    p = dict((m.meta or {}).get(CHAVE) or {})
    p["status"] = {"running": "rodando"}.get(m.status or "", m.status or "erro")
    return {"id": m.id, "conv_id": m.conversation_id, "criado": m.created_at.isoformat() if m.created_at else None, **p}


def _patch(message_id: int, status: str | None = None, **campos) -> None:
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or m.name != NOME:
            return
        m.meta = {**(m.meta or {}), CHAVE: {**((m.meta or {}).get(CHAVE) or {}), **campos}}
        if status is not None:
            m.status = status
        s.get(db.Conversation, m.conversation_id).updated_at = db._now()
        s.commit()


def estado(message_id: int) -> dict:
    if run := _RUNS.get(message_id):
        run["segundos"] = round(time.monotonic() - run["t0"], 1)
        return {"id": message_id, "conv_id": run["conv_id"], **_publico(run), "status": "rodando"}
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or m.name != NOME:
            raise ToolError("Produção não encontrada.")
        return _dict(m)


def listar(conv_id: int) -> list[dict]:
    with db.session() as s:
        ids = list(s.scalars(select(db.Message.id).where(db.Message.conversation_id == conv_id, db.Message.name == NOME)
                             .order_by(db.Message.id.desc())))
    return [estado(i) for i in ids]


def ativos() -> list[int]:
    return [r["conv_id"] for r in _RUNS.values()]


def ocupado() -> bool:
    return bool(_RUNS)


def reap() -> int:
    with db.session() as s:
        presos = list(s.scalars(select(db.Message).where(db.Message.name == NOME, db.Message.status == "running")))
        for m in presos:
            p = dict((m.meta or {}).get(CHAVE) or {})
            p.update(fase="pronto", aviso="Interrompida: o Forja fechou no meio da produção.")
            m.meta, m.status = {**(m.meta or {}), CHAVE: p}, "erro"
        s.commit()
        return len(presos)


def cancelar(message_id: int) -> dict:
    if run := _RUNS.get(message_id):
        run["cancelar"] = True
    return {"ok": True}


# ------------------------------------------------------------------ disparo

def iniciar(conv_id: int, message_id: int | None = None, roteiro_id: str | None = None) -> dict:
    """Produz o roteiro indicado ou, sem indicação, o aprovado da especificação."""
    spec = conteudo.especificacao(conv_id)
    pastas = conteudo.pastas()
    projeto = pastas["pasta_projeto"]
    if not projeto or not Path(projeto).is_dir():
        raise ToolError("Escolha a pasta do projeto de vídeo (aba Pastas).")
    claude = achar_claude()
    if not claude:
        raise ToolError("Não achei o Claude Code neste PC. Instale com `npm i -g @anthropic-ai/claude-code` "
                        "e faça login uma vez rodando `claude` no terminal.")
    if message_id and roteiro_id:
        rodada, roteiro = conteudo_roteiros.achar(message_id, roteiro_id)
    else:
        achado = conteudo_roteiros.aprovado(conv_id)
        if not achado:
            raise ToolError("Nenhum roteiro aprovado nesta especificação.")
        message_id = achado[0]
        rodada, roteiro = conteudo_roteiros.achar(message_id, achado[1]["id"])
    if rodada["conv_id"] != conv_id:
        raise ToolError("O roteiro não é desta especificação.")
    if roteiro["status"] == "produzido":
        raise ToolError("Este roteiro já virou vídeo.")
    estilo = rodada.get("estilo") or spec["estilo"]
    conteudo.ler_estilo(estilo)   # estilo apagado: avisa agora
    formato = rodada.get("formato") or spec["formato"]

    with _TRAVA:   # duas produções ao mesmo tempo disputariam CPU, GPU e o mesmo projeto
        if _RUNS:
            raise ToolError("Já tem um vídeo sendo produzido; espere terminar ou cancele.")
        slug = f"{datetime.now():%Y%m%d}-{_slug(roteiro.get('titulo_youtube') or roteiro['titulo'])}"
        base = {"roteiro_id": roteiro["id"], "rodada_id": message_id, "titulo": roteiro.get("titulo_youtube") or roteiro["titulo"],
                "estilo": estilo, "formato": formato, "slug": slug, "fase": "preparando", "log": [], "aviso": "",
                "ferramentas": 0, "negados": [], "video": "", "entregue": "", "custo_usd": None, "turnos": None,
                "segundos": 0.0}
        msg = _save(conv_id, role="assistant", name=NOME, content="", status="running", meta={CHAVE: base})
        run = _RUNS[msg.id] = {**base, "message_id": msg.id, "conv_id": conv_id, "cancelar": False, "t0": time.monotonic()}

    job = Path(projeto) / ".forja" / "producao" / str(msg.id)
    job.mkdir(parents=True, exist_ok=True)
    (job / "roteiro.json").write_text(json.dumps({**roteiro, "estilo": estilo, "formato": formato},
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
    est_dir = Path(pastas["pasta_estilos"])
    fmt = conteudo.FORMATOS[formato]
    pedido = PEDIDO.format(
        roteiro_json=(job / "roteiro.json").relative_to(projeto).as_posix(),
        estilo_md=(est_dir / f"{estilo}.md").as_posix(),
        readme=f" e `{(est_dir / 'README.md').as_posix()}`" if (est_dir / "README.md").is_file() else "",
        formato_rotulo=fmt["rotulo"], largura=fmt["largura"], altura=fmt["altura"], slug=slug,
        comandos=", ".join(f"`{c}`" for c in pastas["comandos"]) or "(nenhum)",
        frames=(job / "frames").relative_to(projeto).as_posix(),
        midia=MIDIA if (Path(projeto) / "scripts" / "midia.py").is_file() else SEM_MIDIA)
    (job / "pedido.md").write_text(pedido, encoding="utf-8")
    run["_job"], run["_argv"], run["_projeto"], run["_pastas"] = job, argv(claude, pastas), projeto, pastas

    t = asyncio.create_task(_rodar(run, pedido))
    _TAREFAS.add(t)
    t.add_done_callback(_TAREFAS.discard)
    return estado(msg.id)


# ------------------------------------------------------------------ execução

def _resumo_evento(ev: dict) -> list[str]:
    """Linhas do log da tela a partir de um evento do stream-json."""
    out = []
    if ev.get("type") == "assistant":
        for c in (ev.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append("💬 " + c["text"].strip().replace("\n", " ")[:300])
            elif c.get("type") == "tool_use":
                arg = c.get("input") or {}
                alvo = arg.get("command") or arg.get("file_path") or arg.get("pattern") or arg.get("path") or ""
                out.append(f"🔧 {c.get('name')}: {str(alvo)[:200]}")
    return out


def _negado(ev: dict) -> str:
    """Ferramenta recusada pela lista de permissões (vem como tool_result com erro)."""
    if ev.get("type") != "user":
        return ""
    for c in (ev.get("message") or {}).get("content") or []:
        if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
            texto = c.get("content")
            texto = texto if isinstance(texto, str) else json.dumps(texto, ensure_ascii=False)
            if re.search(r"permission|permiss|not allowed|denied|requires approval|was blocked", texto, re.I):
                return texto[:200]
    return ""


def _executar(run: dict, pedido: str) -> dict:
    """Roda o claude -p numa thread (bloqueante). Devolve o evento `result` (ou {})."""
    _acordado(True)
    final: dict = {}
    bruto = open(run["_job"] / "claude.jsonl", "w", encoding="utf-8")
    erros = open(run["_job"] / "claude.err.log", "w", encoding="utf-8")
    try:
        env = ambiente_claude(run["_pastas"])   # sem o token do Forja, com a conta escolhida
        proc = subprocess.Popen(run["_argv"], cwd=run["_projeto"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=erros, env=env, **native.popen_kwargs())
        run["_proc"] = proc
        proc.stdin.write(pedido.encode("utf-8"))
        proc.stdin.close()
        run["fase"] = "trabalhando"
        ultimo = 0.0

        def vigia() -> None:   # cancelar e o teto valem mesmo com o Claude calado (render longo)
            while proc.poll() is None:
                if run["cancelar"] or time.monotonic() - run["t0"] > TETO:
                    native.kill_tree(proc)
                    return
                time.sleep(1)

        threading.Thread(target=vigia, daemon=True).start()
        for linha in proc.stdout:
            texto = linha.decode("utf-8", "replace").strip()
            if not texto:
                continue
            bruto.write(texto + "\n")
            try:
                ev = json.loads(texto)
            except ValueError:
                continue
            run["log"] = (run["log"] + _resumo_evento(ev))[-MAX_LOG:]
            if ev.get("type") == "assistant":
                run["ferramentas"] += sum(c.get("type") == "tool_use" for c in (ev.get("message") or {}).get("content") or [])
            if neg := _negado(ev):
                run["negados"] = (run["negados"] + [neg])[-20:]
            if ev.get("type") == "result":
                final = ev
            elif ev.get("type") == "rate_limit_event":
                _salvar_uso(ev.get("rate_limit_info") or {})
            if time.monotonic() - ultimo > GRAVAR_A_CADA:
                ultimo = time.monotonic()
                _patch(run["message_id"], **_publico(run))
        proc.wait()
        run["_codigo"] = proc.returncode
    finally:
        bruto.close()
        erros.close()
        _acordado(False)
    return final


def _video(run: dict, final: dict) -> Path | None:
    """O .mp4 que o Claude disse ter feito; sem a linha VIDEO:, o mais novo de out/ desde o início."""
    projeto = Path(run["_projeto"])
    m = re.search(r"VIDEO:\s*(\S+\.mp4)", str(final.get("result") or ""))
    if m:
        p = (projeto / m.group(1)).resolve()
        if p.is_file() and p.stat().st_size > 0 and projeto.resolve() in p.parents:
            return p
    inicio = time.time() - (time.monotonic() - run["t0"])
    novos = [p for p in (projeto / "out").glob("*.mp4") if p.stat().st_mtime >= inicio and p.stat().st_size > 0] \
        if (projeto / "out").is_dir() else []
    return max(novos, key=lambda p: p.stat().st_mtime) if novos else None


def _recolher_frames(run: dict) -> int:
    """Frames de conferência que o Claude salvou em out/ mesmo assim: vão para a pasta da produção.
    out/ é onde moram os vídeos; png solto ali só polui (move, nunca apaga)."""
    out, destino = Path(run["_projeto"]) / "out", run["_job"] / "frames"
    if not out.is_dir():
        return 0
    inicio = time.time() - (time.monotonic() - run["t0"])
    n = 0
    for png in out.glob("*.png"):
        if png.stat().st_mtime >= inicio:
            destino.mkdir(parents=True, exist_ok=True)
            shutil.move(str(png), str(destino / png.name))
            n += 1
    return n


def video_entregue(message_id: int) -> Path:
    """O .mp4 que a produção entregou, para o player da tela (só o arquivo que ela mesma registrou)."""
    p = estado(message_id)
    caminho = Path(p.get("entregue") or "")
    if p.get("status") != "ok" or caminho.suffix.lower() != ".mp4" or not caminho.is_file():
        raise ToolError("Vídeo não encontrado (foi movido ou apagado da pasta de entrega?).")
    return caminho


def _entregar(run: dict, video: Path) -> str:
    """Copia o vídeo e um .txt com título, descrição e fontes para a pasta de entrega."""
    saida = Path(run["_pastas"]["pasta_saida"] or video.parent)
    saida.mkdir(parents=True, exist_ok=True)
    destino = saida / f"{run['slug']}.mp4"
    # Entrega na própria pasta do render (out/): copiar o arquivo sobre ele mesmo dá WinError 32 no Windows.
    if not (destino.exists() and os.path.samefile(video, destino)):
        shutil.copy2(video, destino)
    roteiro = json.loads((run["_job"] / "roteiro.json").read_text(encoding="utf-8"))
    fontes = "\n".join(f"- {f['titulo']}: {f['url']}" for f in (roteiro.get("noticia") or {}).get("fontes") or [])
    destino.with_suffix(".txt").write_text(
        f"TÍTULO\n{roteiro.get('titulo_youtube') or roteiro.get('titulo')}\n\nDESCRIÇÃO\n{roteiro.get('descricao') or ''}\n\n"
        f"FONTES\n{fontes}\n", encoding="utf-8")
    return str(destino)


async def _rodar(run: dict, pedido: str) -> None:
    status = "erro"
    try:
        final = await asyncio.to_thread(_executar, run, pedido)
        await asyncio.to_thread(_recolher_frames, run)
        run.update(custo_usd=final.get("total_cost_usd"), turnos=final.get("num_turns"),
                   sessao=final.get("session_id") or run.get("sessao") or "")   # a revisão retoma esta sessão
        if run.get("_retomou") and final.get("is_error") and re.search(r"conversation|session", str(final.get("result")), re.I):
            # sessão do Claude que já não existe (outra conta, limpeza): refaz sem retomar — o pedido se basta
            run["log"] = run["log"] + ["↻ a sessão anterior não existe mais; refazendo sem retomar"]
            run["_argv"] = [a for i, a in enumerate(run["_argv"]) if a != "--resume" and run["_argv"][i - 1] != "--resume"]
            run["_retomou"] = False
            final = await asyncio.to_thread(_executar, run, pedido)
            await asyncio.to_thread(_recolher_frames, run)
            run.update(custo_usd=final.get("total_cost_usd"), turnos=final.get("num_turns"), sessao=final.get("session_id") or "")
        for d in final.get("permission_denials") or []:   # a lista oficial do Claude Code, no evento final
            cmd = str((d.get("tool_input") or {}).get("command") or d.get("tool_name") or "")[:200]
            if cmd and not any(cmd in n for n in run["negados"]):
                run["negados"] = (run["negados"] + [f"{d.get('tool_name')}: {cmd}"])[-20:]
        if run["cancelar"]:
            status, run["aviso"] = "cancelado", "Produção cancelada."
        elif time.monotonic() - run["t0"] > TETO:
            run["aviso"] = f"Passou do tempo máximo ({TETO // 3600} h) e foi interrompida."
        elif not final or final.get("is_error") or final.get("subtype") != "success":
            motivo = str(final.get("result") or final.get("subtype") or
                         f"saiu com código {run.get('_codigo')} sem resposta (veja claude.err.log)")
            run["aviso"] = AVISO_LOGIN if LOGIN_RE.search(motivo) else ("O Claude terminou com erro: " + motivo)[:500]
        elif not (video := _video(run, final)):
            run["aviso"] = "O Claude terminou, mas não achei o .mp4 em out/."
        else:
            run["video"] = str(video)
            run["entregue"] = await asyncio.to_thread(_entregar, run, video)
            if not run.get("revisao_de"):   # revisão: o roteiro já tinha virado vídeo na 1ª versão
                conteudo_roteiros.marcar_produzido(run["rodada_id"], run["roteiro_id"], run["entregue"])
            status = "ok"
    except Exception as e:   # nada pode deixar a produção presa em "running"
        run["aviso"] = f"{e.__class__.__name__}: {e}"[:300]
    finally:
        run["fase"] = "pronto"
        run["segundos"] = round(time.monotonic() - run["t0"], 1)
        _patch(run["message_id"], status=status, **_publico(run))
        _RUNS.pop(run["message_id"], None)
        try:
            from . import mobile
            pronto = f"Versão {run.get('versao', 1)} pronta" if run.get("revisao_de") else "Vídeo pronto"
            await asyncio.to_thread(mobile.avisa, pronto if status == "ok" else "Produção não terminou",
                                    run["titulo"] if status == "ok" else run["aviso"], run["conv_id"])
        except Exception:
            pass


# ------------------------------------------------------------------ revisão (nova versão de um vídeo pronto)

MAX_PEDIDOS = 20
MAX_IMAGEM = 8 * 1024 * 1024


def _seg(v) -> float:
    try:
        return max(0.0, round(float(v), 2))
    except (TypeError, ValueError):
        raise ToolError("Tempo inválido no pedido de mudança.")


def _tempo(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:05.2f}".replace(".", ",")


def revisar(message_id: int, pedidos: list, geral: str = "") -> dict:
    """O usuário marcou quadros (com desenho) e trechos do vídeo pronto: o Claude retoma a sessão que fez o vídeo,
    muda só isso e renderiza uma versão nova (a anterior fica)."""
    import base64

    anterior = estado(message_id)
    if anterior.get("status") != "ok" or not anterior.get("entregue"):
        raise ToolError("Só dá para revisar um vídeo que ficou pronto.")
    geral = str(geral or "").strip()[:4000]
    limpos = []
    for p in (pedidos or [])[:MAX_PEDIDOS]:
        if not isinstance(p, dict):
            continue
        comentario = str(p.get("comentario") or "").strip()[:2000]
        if not comentario:
            raise ToolError("Todo pedido de mudança precisa de um comentário dizendo o que mudar.")
        if p.get("tipo") == "quadro":
            imagem = str(p.get("imagem") or "")
            dados = base64.b64decode(imagem.split(",", 1)[-1]) if imagem else b""
            if dados and (len(dados) > MAX_IMAGEM or not dados.startswith(b"\x89PNG")):
                raise ToolError("Imagem do quadro inválida (precisa ser PNG de até 8 MB).")
            limpos.append({"tipo": "quadro", "tempo": _seg(p.get("tempo")), "comentario": comentario, "_png": dados})
        elif p.get("tipo") == "trecho":
            ini, fim = sorted((_seg(p.get("inicio")), _seg(p.get("fim"))))
            limpos.append({"tipo": "trecho", "inicio": ini, "fim": fim, "comentario": comentario})
    if not limpos and not geral:
        raise ToolError("Diga o que mudar: marque um quadro, um trecho ou escreva uma mudança geral.")

    pastas = conteudo.pastas()
    projeto = pastas["pasta_projeto"]
    claude = achar_claude()
    if not claude:
        raise ToolError("Não achei o Claude Code neste PC.")
    job_anterior = Path(projeto) / ".forja" / "producao" / str(message_id)
    if not (job_anterior / "roteiro.json").is_file():
        raise ToolError("Os arquivos da produção anterior sumiram de .forja/producao no projeto.")
    versao = int(anterior.get("versao") or 1) + 1
    raiz = re.sub(r"-v\d+$", "", anterior["slug"])
    slug = f"{raiz}-v{versao}"

    with _TRAVA:
        if _RUNS:
            raise ToolError("Já tem um vídeo sendo produzido; espere terminar ou cancele.")
        base = {k: anterior.get(k) for k in ("roteiro_id", "rodada_id", "titulo", "estilo", "formato")}
        base.update(slug=slug, versao=versao, revisao_de=message_id, sessao=anterior.get("sessao") or "",
                    pedidos=[{k: v for k, v in x.items() if not k.startswith("_")} for x in limpos], geral=geral,
                    fase="preparando", log=[], aviso="", ferramentas=0, negados=[], video="", entregue="",
                    custo_usd=None, turnos=None, segundos=0.0)
        msg = _save(anterior["conv_id"], role="assistant", name=NOME, content="", status="running", meta={CHAVE: base})
        run = _RUNS[msg.id] = {**base, "message_id": msg.id, "conv_id": anterior["conv_id"], "cancelar": False,
                               "t0": time.monotonic()}

    job = Path(projeto) / ".forja" / "producao" / str(msg.id)
    (job / "anotacoes").mkdir(parents=True, exist_ok=True)
    shutil.copy2(job_anterior / "roteiro.json", job / "roteiro.json")
    linhas = []
    for i, x in enumerate(limpos, 1):
        if x["tipo"] == "quadro":
            onde = f"[quadro em {_tempo(x['tempo'])}]"
            if x["_png"]:
                img = job / "anotacoes" / f"q{i}.png"
                img.write_bytes(x["_png"])
                onde += f" — veja `{img.relative_to(projeto).as_posix()}` (o que está em vermelho é o que mudar)"
        else:
            onde = f"[trecho de {_tempo(x['inicio'])} a {_tempo(x['fim'])}]"
        linhas.append(f"{i}. {onde}: {x['comentario']}")
    if geral:
        linhas.append(f"{len(linhas) + 1}. [no vídeo todo]: {geral}")
    video_atual = Path(anterior.get("video") or "")
    pedido = REVISAO.format(
        video_atual=(video_atual.relative_to(projeto).as_posix() if video_atual.is_file() and Path(projeto) in video_atual.parents
                     else f"out/{anterior['slug']}.mp4"),
        versao_atual=versao - 1,
        pedido_original=(job_anterior / "pedido.md").relative_to(projeto).as_posix(),
        roteiro_json=(job / "roteiro.json").relative_to(projeto).as_posix(),
        retomada="Esta conversa é a mesma em que você fez o vídeo, então você já sabe onde ficam os arquivos. " if base["sessao"] else "",
        pedidos="\n".join(linhas), frames=(job / "frames").relative_to(projeto).as_posix(), slug=slug,
        comandos=", ".join(f"`{c}`" for c in pastas["comandos"]) or "(nenhum)")
    (job / "pedido.md").write_text(pedido, encoding="utf-8")
    a = argv(claude, pastas)
    if base["sessao"]:
        i = a.index("-p")   # logo antes do -p: o executável (e o que vier antes dele) fica intacto
        a[i:i] = ["--resume", base["sessao"]]
    run["_job"], run["_argv"], run["_projeto"], run["_pastas"], run["_retomou"] = job, a, projeto, pastas, bool(base["sessao"])

    t = asyncio.create_task(_rodar(run, pedido))
    _TAREFAS.add(t)
    t.add_done_callback(_TAREFAS.discard)
    return estado(msg.id)

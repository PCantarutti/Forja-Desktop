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

from . import config, conteudo, conteudo_qc, conteudo_roteiros, db, native, tts, youtube
from .agent import _save
from .tools import ToolError

CHAVE = "producao"
NOME = "producao"
TETO = 3 * 3600          # segundos; um vídeo longo com render pode levar bem mais que um Short
GRAVAR_A_CADA = 4.0      # segundos entre gravações do estado (o log anda mais rápido que isso)
MAX_LOG = 300
# WebSearch/WebFetch: achar a imagem oficial (screenshots do site, newswire, kit de imprensa) quando o vídeo não tem a cena
FERRAMENTAS = ["Read", "Edit", "Write", "MultiEdit", "Glob", "Grep", "TodoWrite", "WebSearch", "WebFetch"]
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
{voz}{duracao}
## Como
- Este diretório é o projeto Remotion. Antes de criar algo, veja como os vídeos anteriores foram feitos aqui
  (CLAUDE.md, README, scripts/ e src/) e reaproveite: narração, legendas, efeitos e componentes.
- Crie uma composição nova para este vídeo; não altere nem quebre as composições que já existem. Não edite
  arquivos de outras composições nem para exportar algo: para reaproveitar, importe o que já é exportado ou copie.
{midia}- CAPA NO INÍCIO (regra fixa): o vídeo abre com a capa (thumb) do estilo, ~1,2 s parada, SEM narração nem legenda, e
  transição para o conteúdo; use `ComCapa` de `src/Short/capa.tsx` se existir (veja a seção "Capa no início" do estilo).
  Exporte também a capa: `npx remotion still <Composição> out/{slug}-capa.jpg --frame=15`, e uma capa ALTERNATIVA
  para teste A/B em `out/{slug}-capa-b.jpg` (mesma identidade do estilo, outra frase curta ou outro enquadramento;
  a mesma composição com uma prop, sem mudar o vídeo).
- CHECAGEM DE FATOS antes de montar: o roteiro foi escrito por outro modelo e pode errar. Confira na fonte oficial
  (WebSearch/WebFetch) os fatos objetivos que vão para a tela ou para a fala — plataformas (página oficial do jogo e
  de cada loja; nunca deduzir), datas, preços, números. Errado = corrija a cena (fala e tela) só no necessário e conte
  na resposta final, numa linha começando com "CORREÇÃO:", o que mudou e a fonte.
- Confira frames com `npx remotion still` antes do render final, salvando em `{frames}/` (nunca em `out/`);
  corrija texto cortado ou sobreposto.
- Renderize o vídeo final em `out/{slug}.mp4` em PRIMEIRO PLANO e espere terminar (nunca em segundo plano /
  run_in_background): a produção acaba quando você responde, e um render ainda rodando fica pela metade.
- Se a pasta de estilos tiver um README com a tabela "Vídeos já feitos", acrescente este vídeo nela.
- Comandos de terminal permitidos (o resto é negado na hora, não insista): {comandos}.
  Rode cada um sozinho: sem `cd x &&`, sem `;` e sem pipe para comando fora da lista (`| tail`, `| head`...).
  Para ler ou procurar arquivos use as ferramentas Read, Glob e Grep, não o terminal.
- Comando negado é regra, não erro: siga sem ele (faça de outro jeito ou simplifique).

- Escreva em `{youtube}` o texto para publicar: 1ª linha só o título (até 60 caracteres, o do roteiro ou melhor),
  linha em branco, e a descrição final pronta para colar no YouTube — a do roteiro, ajustada ao que o vídeo mostra, com
  os créditos de toda mídia real usada (ex.: "Imagens: Rockstar Games") e as fontes no fim.

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
  Tudo cai em `public/midia/<assunto>/` com `creditos.json`; use com `staticFile()` e `<OffthreadVideo muted>`/`<Img>`,
  SEMPRE com o caminho completo escrito no código (`staticFile("midia/gta-6/yt-20-25.mp4")`, nunca montado com
  variável): no fim, o Forja apaga de `public/midia/` todo arquivo que o código não cita (trailers inteiros, referências).
- Ordem para cada cena que fala de algo concreto: (1) trecho de vídeo oficial que MOSTRA aquilo; (2) não tendo, uma
  IMAGEM oficial — procure com WebSearch/WebFetch no site oficial (ex.: as screenshots e artes da página do jogo, o
  newswire ou o kit de imprensa da empresa), pegue o endereço da imagem e baixe com `midia.py imagem <url>` (no máximo 3 candidatas por cena, escolhidas pelo
  nome e pela descrição da galeria: abrir cada imagem custa caro); (3) só então o
  motion design do estilo.
- Imagem (ou vídeo) que não tem o formato do vídeo — ex.: screenshot 16:9 num vídeo vertical — NÃO estique para cobrir
  a tela (corta a maior parte e vira um borrão ampliado): mostre inteira na largura, num quadro com borda/sombra, e
  preencha o fundo com a mesma imagem desfocada e escurecida. Zoom lento de no máximo ~10%, escurecimento leve (até
  ~30%) e nada desenhado por cima que a esconda: quem vê tem que reconhecer na hora o que ela mostra. Confira no still. Imagem de site de notícia
  ou de fã não serve, a não ser que seja a própria imagem oficial republicada e você ache a original.
- Dois modos de usar mídia real; escolha um por cena:
  - ILUSTRAÇÃO DE FUNDO (o padrão, como no vídeo das novidades do GTA 6): o vídeo só ilustra o que a narração fala.
    Ele toca em tela cheia por trás da cena, escurecido (~40–50%, blur leve opcional), e o motion design do estilo
    (número, título, chips, contador, legenda) continua por cima, legível. Imagem parada aqui pede zoom lento.
  - FOCO EM JANELA (quando o vídeo É o assunto: o trailer do jogo listado, a demo do port rodando, o anúncio): o vídeo
    aparece numa janela/quadro com borda, e por trás dela o MESMO vídeo/imagem em tela cheia, bem borrado (blur forte,
    ~40 px) e escurecido, tocando junto (mesmo arquivo e mesmo ponto de início). Nunca janela sobre fundo liso ou sobre o
    fundo desenhado do estilo (como no vídeo dos 4 jogos da semana).
- Fonte: oficial ou PRIMÁRIA da notícia — a dona do jogo/produto OU quem fez a coisa noticiada (o projeto do port, o
  pesquisador, o estúdio, o perfil oficial de quem anunciou). Se a notícia é sobre algo que alguém fez (ex.: um port
  rodando no PC), mostre o vídeo de QUEM FEZ mostrando aquilo, não só o trailer original do jogo.
- Regras da mídia real: nunca reação, compilação ou reupload de youtuber, streamer ou fã; cada trecho com até ~6 s; nunca o
  áudio original; todos os créditos no fim da descrição ("Trailer: <dono> / Steam"); crédito na tela só se o estilo pedir. Sem mídia oficial disponível (ex.: jogo só de console), siga com motion design: não invente nem improvise.
"""

# Revisão que pede material real ("põe um trecho do trailer", "mostra a apresentação", um link colado no pedido).
MIDIA_REVISAO = """- Pedido que fala em vídeo, trecho, trailer, gameplay, apresentação, foto ou print "real" = buscar mídia real com
  `python scripts/midia.py`. Link no pedido: use ESSE link (`youtube <link> --de --ate` para vídeo, `imagem <link>` para
  imagem), no trecho de tempo que ele indicar (sem tempo, escolha o que mostra o que a cena fala). Sem link: procure a
  fonte oficial (`steam`, `nasa`, ou o link oficial que você souber). Se não der para conseguir (sem fonte oficial,
  yt-dlp ausente, download negado), não invente nem troque por outra coisa: mantenha a cena e explique na resposta
  final, numa linha começando com "MÍDIA:", o que faltou e o que a pessoa pode fazer (ex.: mandar o link).
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
- Renderize a nova versão em `out/{slug}.mp4`, sem sobrescrever a anterior, em PRIMEIRO PLANO e esperando terminar
  (nunca em segundo plano / run_in_background: a revisão acaba quando você responde).
{midia}- Comandos de terminal permitidos (o resto é negado na hora, não insista): {comandos}.
  Rode cada um sozinho: sem `cd x &&`, sem `;` e sem pipe para comando fora da lista. Para ler arquivos e imagens, use Read.

- Escreva em `{youtube}` o texto para publicar: 1ª linha só o título (até 60 caracteres, o do roteiro ou melhor),
  linha em branco, e a descrição final pronta para colar no YouTube — a do roteiro, ajustada ao que o vídeo mostra, com
  os créditos de toda mídia real usada (ex.: "Imagens: Rockstar Games") e as fontes no fim.

Na última linha da sua resposta final escreva só: VIDEO: out/{slug}.mp4
"""

# ------------------------------------------------------------------ voz da narração (campo "voz" da especificação)

_PAUSAS = ("  Mantenha as marcações de pausa do estilo (`[pausa]`, `[pausa longa]`) no texto que vai ao narrate.py: o script\n"
           "  converte para o motor escolhido.\n")
_SO_UMA = ("  Antes de gerar todas as cenas, gere UMA (`python scripts/narrate.py <video> elevenlabs --so=<cena>`) e confira no log\n"
           "  o custo informado; o cache por cena impede pagar duas vezes pelo mesmo texto.\n")


def instrucoes_voz(voz: dict | None, fase: str = "") -> str:
    """O trecho do pedido que diz qual voz usar. fase="validacao": ElevenLabs com validação, esta é a versão Edge."""
    v = {**conteudo._spec_padrao()["voz"], **(voz or {})}
    m = v["motor"]
    if m == "estilo":
        return ""
    if m == "edge" or (m == "elevenlabs" and fase == "validacao"):
        txt = "- Voz: Edge TTS (grátis), mesmo que o estilo diga outra: `python scripts/narrate.py <video>` (o motor padrão).\n"
        if m == "elevenlabs":
            txt += ("  Esta é a VERSÃO DE VALIDAÇÃO: a voz final com ElevenLabs vem depois, quando a pessoa aprovar este vídeo.\n"
                    "  Não gaste créditos do ElevenLabs agora. Deixe tudo pronto para trocar só a voz: mesmo texto, mesmas cenas.\n")
        return txt + _PAUSAS
    if m == "elevenlabs":
        return ("- Voz: ElevenLabs — `python scripts/narrate.py <video> elevenlabs` (a chave e a voz ficam no .env do projeto).\n"
                + _SO_UMA + _PAUSAS +
                "  Se o narrate.py não tiver o motor elevenlabs ou a chave faltar, siga com o Edge e avise numa linha \"VOZ:\".\n")
    return (f"- Voz: a voz do próprio Forja (tela Voz: modelo \"{v['modelo'] or 'o primeiro cadastrado'}\", voz \"{v['voz']}\") —\n"
            "  `python scripts/narrate.py <video> forja`. O Forja está de pé nesta máquina e o ambiente já traz FORJA_TTS_URL,\n"
            "  FORJA_TTS_TOKEN, FORJA_TTS_MODELO e FORJA_TTS_VOZ. Contrato: POST {FORJA_TTS_URL}/api/tts/falar com o header\n"
            "  `x-forja-token: <FORJA_TTS_TOKEN>` e o JSON {texto, modelo, voz, palavras: true} → {arquivo (WAV neste PC), duracao,\n"
            "  palavras: [[palavra, início_ms, fim_ms], ...]}. Uma cena leva de 30 s a 1 min: gere cada uma uma vez só.\n"
            "  Se o narrate.py não tiver o motor forja, acrescente seguindo esse contrato (grava o áudio da cena e devolve\n"
            "  as palavras com tempo, como o motor do Edge). Pausa: gere cada trecho entre marcações à parte e emende com silêncio.\n"
            + _PAUSAS)


def ambiente_voz(voz: dict | None) -> dict:
    """Variáveis que o narrate.py usa para falar com a voz do Forja (token próprio só do /api/tts/falar)."""
    v = voz or {}
    if v.get("motor") != "forja":
        return {}
    from .mcp_servidor import url_base
    return {"FORJA_TTS_URL": url_base(), "FORJA_TTS_TOKEN": tts.TOKEN_FALAR,
            "FORJA_TTS_MODELO": v.get("modelo") or "", "FORJA_TTS_VOZ": v.get("voz") or ""}


VOZ_FINAL = ("Troque a narração pela voz FINAL com ElevenLabs. Esta versão foi aprovada com o Edge TTS; agora:\n"
             "   - rode `python scripts/narrate.py <video> elevenlabs` para o MESMO texto de cada cena (com as mesmas marcações\n"
             "     de pausa). Gere primeiro UMA cena com `--so=<cena>` e confira o custo no log; depois o resto (o cache não cobra\n"
             "     de novo pelo que já foi gerado). Antes, guarde a narração do Edge numa pasta de backup (`<pasta>-edge`).\n"
             "   - a timeline muda de tempo: as animações ancoradas por palavra se ajustam; confira com stills e corrija o que sobrar;\n"
             "   - não mude visual nem texto; refaça a legenda .srt e os tempos dos capítulos da descrição.")

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
    claude = _quem_produz(pastas)
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
    voz = spec.get("voz") or {}
    validar = voz.get("motor") == "elevenlabs" and voz.get("validar_edge")

    with _TRAVA:   # duas produções ao mesmo tempo disputariam CPU, GPU e o mesmo projeto
        if _RUNS:
            raise ToolError("Já tem um vídeo sendo produzido; espere terminar ou cancele.")
        slug = f"{datetime.now():%Y%m%d}-{_slug(roteiro.get('titulo_youtube') or roteiro['titulo'])}"
        base = {"roteiro_id": roteiro["id"], "rodada_id": message_id, "titulo": roteiro.get("titulo_youtube") or roteiro["titulo"],
                "estilo": estilo, "formato": formato, "slug": slug, "fase": "preparando", "log": [], "aviso": "",
                "ferramentas": 0, "negados": [], "video": "", "entregue": "", "custo_usd": None, "turnos": None,
                "segundos": 0.0, "voz": "edge" if validar else (voz.get("motor") or "estilo"),
                "voz_final": "pendente" if validar else ""}
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
        voz=instrucoes_voz(voz, "validacao" if validar else ""),
        duracao=(f"- Duração mínima: {spec['duracao_min']} s (o vídeo final, capa incluída). Confira com ffprobe; se ficar abaixo,\n"
                 "  acrescente antes do CTA UMA cena com mais um fato verificado da notícia (narração nova no mesmo padrão)\n"
                 "  — nunca silêncio, cena parada ou fala esticada — e conte na resposta final, numa linha \"CORREÇÃO:\".\n"
                 if spec.get("duracao_min") else ""),
        comandos=", ".join(f"`{c}`" for c in pastas["comandos"]) or "(nenhum)",
        frames=(job / "frames").relative_to(projeto).as_posix(), youtube=(job / "youtube.txt").relative_to(projeto).as_posix(),
        midia=MIDIA if (Path(projeto) / "scripts" / "midia.py").is_file() else SEM_MIDIA)
    (job / "pedido.md").write_text(pedido, encoding="utf-8")
    run["_job"], run["_argv"], run["_projeto"], run["_pastas"] = job, argv(claude, pastas) if claude else [], projeto, pastas
    run["_env"] = ambiente_voz(voz)
    run["_mcp"] = not claude

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
        env.update(run.get("_env") or {})        # voz do Forja: só o token do /api/tts/falar
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
    """O .mp4 desta produção: o que o Claude disse (linha VIDEO:) ou, sem ela, o out/<slug>.mp4 pedido.
    Nunca "o mais novo de out/": em 2026-10-05 isso pegou um vídeo de OUTRO trabalho renderizado no mesmo minuto e o
    copiou por cima da v5 do Bloodborne. E só vale arquivo modificado durante esta produção."""
    projeto = Path(run["_projeto"]).resolve()
    inicio = time.time() - (time.monotonic() - run["t0"])
    candidatos = []
    m = re.search(r"VIDEO:\s*(\S+\.mp4)", str(final.get("result") or ""))
    if m:
        candidatos.append((projeto / m.group(1)).resolve())
    candidatos.append(projeto / "out" / f"{run['slug']}.mp4")
    for c in candidatos:
        if c.is_file() and c.stat().st_size > 0 and projeto in c.parents and c.stat().st_mtime >= inicio:
            return c
    return None


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


FONTES_DO_VIDEO = (".ts", ".tsx", ".js", ".jsx", ".json", ".mjs", ".css")


def limpar_midia(projeto: Path) -> tuple[int, int]:
    """Apaga de public/midia/ o que nenhum arquivo de src/ cita pelo caminho: trailers inteiros, cópias de
    referência, capturas que não entraram, sobras de download. O que a composição usa fica (o Pedir mudanças
    renderiza de novo e precisa dos trechos). Pasta que fica vazia (ou só com o creditos.json) sai junto.
    Devolve (arquivos apagados, bytes)."""
    midia = projeto / "public" / "midia"
    if not midia.is_dir():
        return 0, 0
    citado = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in (projeto / "src").rglob("*")
                       if f.is_file() and f.suffix.lower() in FONTES_DO_VIDEO)
    n = tam = 0
    for arq in sorted(midia.rglob("*")):
        if not arq.is_file() or arq.name == "creditos.json":
            continue
        if arq.relative_to(projeto / "public").as_posix() in citado:
            continue
        try:
            tam += arq.stat().st_size
            arq.unlink()
            n += 1
        except OSError:   # em uso agora (preview do Remotion aberto): fica para a próxima
            pass
    for pasta in sorted((d for d in midia.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)):
        resto = [f for f in pasta.iterdir()]
        if all(f.name == "creditos.json" for f in resto):
            for f in resto:
                f.unlink()
            pasta.rmdir()
    return n, tam


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
    for sufixo in ("-capa.jpg", "-capa-b.jpg"):   # a thumb que o Claude exporta e a alternativa do teste A/B
        capa = video.with_name(video.stem + sufixo)
        if capa.is_file() and not (saida / capa.name).exists():
            shutil.copy2(capa, saida / capa.name)
    titulo, descricao = _publicacao_do_job(run["_job"])
    destino.with_suffix(".txt").write_text(f"TÍTULO\n{titulo}\n\nDESCRIÇÃO\n{descricao}\n", encoding="utf-8")
    return str(destino)


def _publicacao_do_job(job: Path) -> tuple[str, str]:
    """(título, descrição) para o YouTube: o que o Claude escreveu em youtube.txt (com os créditos da mídia que usou);
    sem ele, o do roteiro com as fontes no fim."""
    arq = job / "youtube.txt"
    if arq.is_file():
        titulo, _, descricao = arq.read_text(encoding="utf-8").strip().partition("\n")
        if titulo.strip():
            return titulo.strip(), descricao.strip()
    roteiro = json.loads((job / "roteiro.json").read_text(encoding="utf-8"))
    descricao = (roteiro.get("descricao") or "").strip()
    fontes = [f for f in (roteiro.get("noticia") or {}).get("fontes") or [] if f.get("url") and f["url"] not in descricao]
    if fontes:
        descricao += "\n\n📚 Fontes\n" + "\n".join(f"- {f['titulo']}: {f['url']}" for f in fontes)
    return (roteiro.get("titulo_youtube") or roteiro.get("titulo") or "").strip(), descricao.strip()


def publicacao(message_id: int) -> dict:
    """Título e descrição prontos para colar no YouTube (botões de copiar no PC e no celular)."""
    p = estado(message_id)
    if p.get("status") != "ok":
        raise ToolError("Esta produção não terminou.")
    job = Path(conteudo.pastas()["pasta_projeto"]) / ".forja" / "producao" / str(message_id)
    if not (job / "roteiro.json").is_file():
        raise ToolError("Não achei o roteiro desta produção na pasta do projeto.")
    titulo, descricao = _publicacao_do_job(job)
    alternativos = json.loads((job / "roteiro.json").read_text(encoding="utf-8")).get("titulos") or []
    return {"titulo": titulo, "descricao": descricao, "titulos": [t for t in alternativos if t != titulo][:3]}


async def _rodar(run: dict, pedido: str) -> None:
    status = "erro"
    try:
        final = await (_esperar_mcp(run) if run.get("_mcp") else asyncio.to_thread(_executar, run, pedido))
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
            # mídia real pedida que não deu para conseguir: o Claude explica numa linha "MÍDIA:", que vira o aviso
            avisos = [f"{r.group(1).capitalize()}: {r.group(2).strip()}"
                      for r in re.finditer(r"^\W*(M[ÍI]DIA|CORRE[ÇC][ÃA]O):\s*(.+)$", str(final.get("result") or ""), re.M | re.I)]
            if avisos:   # mídia que faltou e fatos do roteiro corrigidos: aparecem na produção e no aviso do celular
                run["aviso"] = " · ".join(avisos)[:600]
            status = "ok"
    except Exception as e:   # nada pode deixar a produção presa em "running"
        run["aviso"] = f"{e.__class__.__name__}: {e}"[:300]
    finally:
        try:   # o que foi baixado e não ficou no vídeo não fica no PC
            n, tam = await asyncio.to_thread(limpar_midia, Path(run["_projeto"]))
            if n:
                run["log"] = run["log"] + [f"🧹 apaguei {n} arquivo(s) de mídia que não ficaram no vídeo ({tam / 2 ** 20:.0f} MB)"]
            if status == "ok":   # os quadros de conferência do Claude só serviam durante a produção
                shutil.rmtree(run["_job"] / "frames", ignore_errors=True)
        except Exception:
            pass
        run["fase"] = "pronto"
        run["segundos"] = round(time.monotonic() - run["t0"], 1)
        _patch(run["message_id"], status=status, **_publico(run))
        _RUNS.pop(run["message_id"], None)
        bom = True
        if status == "ok":
            try:
                bom = await conferir_e_corrigir(run)
            except Exception:   # a conferência é extra: nunca impede a entrega nem o aviso
                pass
            if bom:   # vídeo com problema não sobe: a versão corrigida pela conferência sobe quando ficar boa
                _publicar_sozinho(run)
        try:
            from . import mobile
            pronto = f"Versão {run.get('versao', 1)} pronta" if run.get("revisao_de") else "Vídeo pronto"
            if not bom:
                pronto += " com problema" + ("" if run.get("qc_auto") else " (corrigindo sozinho)")
            await asyncio.to_thread(mobile.avisa, pronto if status == "ok" else "Produção não terminou",
                                    (run["titulo"] + (f" · {run['aviso']}" if run.get("aviso") else "")) if status == "ok"
                                    else run["aviso"], run["conv_id"])
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
    claude = _quem_produz(pastas)
    job_anterior = Path(projeto) / ".forja" / "producao" / str(message_id)
    if not (job_anterior / "roteiro.json").is_file():
        raise ToolError("Os arquivos da produção anterior sumiram de .forja/producao no projeto.")
    versao = int(anterior.get("versao") or 1) + 1
    raiz = re.sub(r"-v\d+$", "", anterior["slug"])
    slug = f"{raiz}-v{versao}"

    with _TRAVA:
        if _RUNS:
            raise ToolError("Já tem um vídeo sendo produzido; espere terminar ou cancele.")
        base = {k: anterior.get(k) for k in ("roteiro_id", "rodada_id", "titulo", "estilo", "formato", "voz", "voz_final")}
        # Retoma a sessão só da 1ª versão: cada revisão carregava o histórico inteiro das anteriores e o custo subia
        # a cada volta (US$ 5 → 7,8 → 10,3 nas v4–v6). O pedido aponta composição, roteiro e arquivos: dá para seguir sem.
        sessao = (anterior.get("sessao") or "") if (anterior.get("versao") or 1) == 1 else ""
        base.update(slug=slug, versao=versao, revisao_de=message_id, sessao=sessao,
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
        youtube=(job / "youtube.txt").relative_to(projeto).as_posix(),
        comandos=", ".join(f"`{c}`" for c in pastas["comandos"]) or "(nenhum)",
        midia=(MIDIA + MIDIA_REVISAO) if (Path(projeto) / "scripts" / "midia.py").is_file() else SEM_MIDIA)
    (job / "pedido.md").write_text(pedido, encoding="utf-8")
    a = argv(claude, pastas) if claude else []
    if claude and base["sessao"]:
        i = a.index("-p")   # logo antes do -p: o executável (e o que vier antes dele) fica intacto
        a[i:i] = ["--resume", base["sessao"]]
    run["_job"], run["_argv"], run["_projeto"], run["_pastas"] = job, a, projeto, pastas
    run["_retomou"] = bool(claude and base["sessao"])
    spec_voz = (conteudo.especificacao(anterior["conv_id"]).get("voz") or {}) if anterior.get("conv_id") else {}
    run["_env"] = ambiente_voz(spec_voz)
    run["_mcp"] = not claude

    t = asyncio.create_task(_rodar(run, pedido))
    _TAREFAS.add(t)
    t.add_done_callback(_TAREFAS.discard)
    return estado(msg.id)


def voz_final(message_id: int) -> dict:
    """Vídeo validado com o Edge (especificação com ElevenLabs + validar): faz a próxima versão só trocando a voz."""
    anterior = estado(message_id)
    if anterior.get("voz_final") != "pendente":
        raise ToolError("Este vídeo não está esperando a voz final.")
    novo = revisar(message_id, [], VOZ_FINAL)
    _patch(novo["id"], voz="elevenlabs", voz_final="")
    _patch(message_id, voz_final="feita")
    if novo["id"] in _RUNS:
        _RUNS[novo["id"]].update(voz="elevenlabs", voz_final="")
    return estado(novo["id"])


# ------------------------------------------------------------------ conferência automática do vídeo pronto

QC_PEDIDO = ("CONFERÊNCIA AUTOMÁTICA: a medição do vídeo pronto (ffmpeg) achou estes problemas:\n{lista}\n"
             "   Corrija só isso, sem mudar texto, voz nem visual que está certo, renderize de novo e confira com\n"
             "   ffprobe/ffmpeg (volume: `python scripts/normalizar.py` se o projeto tiver).")


async def conferir_e_corrigir(run: dict) -> bool:
    """Mede o vídeo entregue (conteudo_qc, numa thread). Com problema: grava no estado e pede UMA revisão automática
    ao Claude (a revisão feita por este pedido não pede outra). Devolve se o vídeo está bom para publicar."""
    qc = await asyncio.to_thread(_medir, run)
    _patch(run["message_id"], qc=qc)
    if qc["ok"] or run.get("qc_auto"):
        return qc["ok"]
    _corrigir(run["message_id"], qc)   # no laço do asyncio: a revisão agenda a tarefa dela aqui
    return False


def _medir(run: dict) -> dict:
    try:
        roteiro = json.loads((run["_job"] / "roteiro.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        roteiro = {}
    spec_min = 0
    try:
        spec_min = conteudo.especificacao(run["conv_id"]).get("duracao_min") or 0
    except ToolError:
        pass
    return conteudo_qc.conferir(Path(run["entregue"]), run.get("formato") or "vertical", spec_min,
                                float(roteiro.get("segundos") or 0))


def _corrigir(mid: int, qc: dict) -> None:
    try:
        novo = revisar(mid, [], QC_PEDIDO.format(lista="\n".join(f"   - {x}" for x in qc["problemas"])))
        _patch(novo["id"], qc_auto=True)
        if novo["id"] in _RUNS:
            _RUNS[novo["id"]]["qc_auto"] = True
        _patch(mid, qc={**qc, "revisao": novo["id"]})
    except ToolError as e:   # sem sessão, outra produção rodando…: fica o aviso, sem revisão
        _patch(mid, qc={**qc, "aviso": f"Revisão automática não começou: {e}"[:200]})
# ------------------------------------------------------------------ publicação no YouTube

def publicar_youtube(message_id: int, privacidade: str = "private", publicar_em: str = "") -> dict:
    """Sobe o vídeo pronto para o YouTube em segundo plano; o andamento fica em estado()["youtube"]."""
    p = estado(message_id)
    video = video_entregue(message_id)
    yt = p.get("youtube") or {}
    if yt.get("status") == "enviando":
        raise ToolError("Este vídeo já está subindo para o YouTube.")
    if yt.get("status") == "ok":
        raise ToolError(f"Este vídeo já está no YouTube: {yt.get('url')}")
    if privacidade not in youtube.PRIVACIDADES:
        raise ToolError("Privacidade inválida: private, unlisted ou public.")
    if not youtube.estado()["conectado"]:
        raise ToolError("Conecte a conta do YouTube em Conteúdo › Ajustes.")
    pub = publicacao(message_id)   # também traz "titulos" (alternativos) desde as pautas
    titulo, descricao = pub["titulo"], pub["descricao"]
    _patch(message_id, youtube={"status": "enviando", "progresso": 0.0, "privacidade": privacidade})

    def rodar():
        try:
            r = youtube.enviar(video, titulo, descricao, privacidade, video.with_name(f"{video.stem}-capa.jpg"), publicar_em,
                               progresso=lambda f: _patch(message_id, youtube={"status": "enviando", "progresso": round(f, 3),
                                                                                "privacidade": privacidade}))
            _patch(message_id, youtube={"status": "ok", **r})
            aviso = ("Publicado no YouTube", f"{titulo} · {r['url']}" + (f" · {r['aviso']}" if r["aviso"] else ""))
        except Exception as e:   # nada deixa o card preso em "enviando"
            _patch(message_id, youtube={"status": "erro", "erro": str(e)[:300]})
            aviso = ("Não subiu para o YouTube", f"{titulo} · {str(e)[:200]}")
        try:
            from . import mobile
            mobile.avisa(*aviso, p.get("conv_id"))
        except Exception:
            pass
    threading.Thread(target=rodar, daemon=True, name=f"youtube-{message_id}").start()
    return estado(message_id)


def _publicar_sozinho(run: dict) -> None:
    """Especificação com "publicar no YouTube": o vídeo pronto sobe sozinho. Não sobe a versão de validação (voz do
    Edge esperando a voz final) nem a revisão de um vídeo que já foi publicado (essa a pessoa sobe pelo botão)."""
    try:
        privacidade = (conteudo.especificacao(run["conv_id"]).get("publicar") or {}).get("youtube") or ""
        if not privacidade or run.get("voz_final") == "pendente":
            return
        if run.get("revisao_de") and (estado(run["revisao_de"]).get("youtube") or {}).get("status") == "ok":
            return
        publicar_youtube(run["message_id"], privacidade)
    except Exception as e:
        _patch(run["message_id"], youtube={"status": "erro", "erro": str(e)[:300]})


# ------------------------------------------------------------------ produção por uma sessão do Claude via MCP

def _quem_produz(pastas: dict) -> str:
    """O executável do Claude Code (o Forja roda o claude -p) ou "" (modo MCP: uma sessão conectada atende)."""
    if pastas.get("produtor") == "mcp":
        if not config.MCP_SERVIDOR:
            raise ToolError("Para produzir pelo Claude conectado, ligue \"Permitir que o Claude controle o Forja\" em "
                            "Configurações › MCP e conecte o Claude Code ou o Claude Desktop.")
        return ""
    claude = achar_claude()
    if not claude:
        raise ToolError("Não achei o Claude Code neste PC. Instale com `npm i -g @anthropic-ai/claude-code` "
                        "e faça login uma vez rodando `claude` no terminal.")
    return claude


async def _esperar_mcp(run: dict) -> dict:
    """Modo MCP: a produção fica na fila até uma sessão do Claude pegar (conteudo_producoes) e entregar
    (conteudo_entregar_video). Devolve o mesmo formato do evento `result` do claude -p."""
    run["fase"] = "aguardando o Claude (MCP)"
    run["log"] = run["log"] + ["⏳ esperando uma sessão do Claude conectada ao Forja pegar o pedido (conteudo_producoes)"]
    _patch(run["message_id"], **_publico(run))
    _acordado(True)
    try:
        while not run.get("_entrega"):
            if run["cancelar"] or time.monotonic() - run["t0"] > TETO:
                return {}
            await asyncio.sleep(1)
        return run["_entrega"]
    finally:
        _acordado(False)


def _bloco_mcp(run: dict) -> str:
    pedido = (run["_job"] / "pedido.md").read_text(encoding="utf-8")
    env = run.get("_env") or {}
    variaveis = ("Antes do narrate.py, defina no terminal: " + " ".join(f"{k}={v}" for k, v in env.items()) + "\n") if env else ""
    return "\n".join([
        f"PRODUÇÃO {run['message_id']} — {run['titulo']}" + (f" (versão {run.get('versao')})" if run.get("revisao_de") else ""),
        f"Pasta do projeto (trabalhe nela; os caminhos do pedido são relativos a ela): {run['_projeto']}",
        variaveis + pedido,
        "",
        f"Quando o vídeo estiver renderizado: conteudo_entregar_video(producao_id={run['message_id']}, "
        f"video=\"out/{run['slug']}.mp4\", resumo=\"<o que fez; linhas MÍDIA: e CORREÇÃO: se houver>\"). "
        f"Se não der: conteudo_entregar_video(producao_id={run['message_id']}, erro=\"<motivo>\").",
    ])


async def mcp_producoes(espera: int = 60) -> str:
    """Produções na fila do modo MCP. Sem nenhuma, espera até `espera` s (máx. 100) por uma nova."""
    fim = time.monotonic() + max(0, min(int(espera or 0), 100))
    while True:
        novas = [r for r in _RUNS.values() if r.get("_mcp") and not r.get("_pegou") and not r.get("_entrega")]
        if novas or time.monotonic() >= fim:
            break
        await asyncio.sleep(1)
    if not novas:
        andando = [r for r in _RUNS.values() if r.get("_mcp") and r.get("_pegou") and not r.get("_entrega")]
        return ("Nenhum vídeo novo na fila." + (" Em andamento (já pegos): " + ", ".join(
            f"{r['message_id']} ({r['titulo']})" for r in andando) if andando else ""))
    for r in novas:
        r["_pegou"] = True
        r["fase"] = "o Claude (MCP) está fazendo"
        r["log"] = r["log"] + ["🤝 uma sessão do Claude pegou o pedido"]
        _patch(r["message_id"], **_publico(r))
    return "\n\n---\n\n".join(_bloco_mcp(r) for r in novas)


def mcp_entregar(producao_id: int, video: str = "", resumo: str = "", erro: str = "") -> str:
    """A sessão do Claude terminou: com o vídeo, o Forja entrega, confere e publica como numa produção normal."""
    run = _RUNS.get(producao_id)
    if not run or not run.get("_mcp"):
        return f"ERRO: a produção {producao_id} não está esperando entrega (veja conteudo_producoes)."
    resumo = str(resumo or "").strip()[:4000]
    if erro:
        run["_entrega"] = {"is_error": True, "subtype": "error", "result": str(erro)[:500]}
        return "Registrado: a produção fica como não terminada, com o motivo na tela."
    final = {"is_error": False, "subtype": "success", "result": resumo + (f"\nVIDEO: {video}" if video else "")}
    if not _video(run, final):
        return (f"ERRO: não achei o vídeo ({video or 'out/' + run['slug'] + '.mp4'}) dentro de {run['_projeto']}, "
                "renderizado depois que você pegou o pedido. Confira o caminho (relativo à pasta do projeto) e entregue de novo.")
    run["_entrega"] = final
    return ("Recebido. O Forja vai copiar para a entrega, conferir (volume, silêncio, tela preta, duração) e publicar se a "
            "especificação pedir; se a conferência achar problema, chega aqui um pedido de correção.")


def aviso_videos() -> str:
    """Linha que as ferramentas MCP da tela Conteúdo acrescentam quando há vídeo na fila do modo MCP."""
    if v := sum(1 for r in _RUNS.values() if r.get("_mcp") and not r.get("_pegou") and not r.get("_entrega")):
        return f"\n\n[{v} vídeo(s) da tela Conteúdo esperando você produzir: chame conteudo_producoes.]"
    return ""

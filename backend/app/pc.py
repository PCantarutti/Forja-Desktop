"""Controle do PC pelo assistente de voz: uma lista FECHADA de ações seguras (abrir e fechar programas, volume, saída de
som, travar a tela, janelas, mídia, pastas e achar arquivo). Nada de shell livre: o texto falado só escolhe a ação e um
nome, e o nome só escolhe entre o que o Windows listou (programas do Iniciar, janelas abertas, dispositivos de som).

entende(texto) -> (acao, arg) | None decide pelo texto (tests/test_pc.py); faz(acao, arg) executa e devolve a frase
que o assistente fala. Janelas e teclas por ctypes; volume e saída por pc_audio.cs (Core Audio), compilado uma vez."""
from __future__ import annotations

import ctypes
import difflib
import hashlib
import json
import os
import re
import subprocess
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from . import config, native


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# ------------------------------------------------------------------ o que foi pedido

PEDIDO_EDUCADO = re.compile(r"^(?:(?:por\s+favor|voce\s+pode|vc\s+pode|pode|consegue|quero\s+que\s+voce|da\s+pra|"
                            r"agora|ai|e)[\s,]+)+")
TRAVAR = re.compile(r"\b(?:trav\w*|bloque\w*)\s+(?:o\s+|a\s+|ess[ae]\s+|est[ae]\s+|meu\s+|minha\s+)?(?:pc|computador|tela|maquina|windows)\b")
NAO_PERTURBE = re.compile(r"\bnao\s+(?:perturbe|incomode)\b|\bmodo\s+foco\b")
VOLUME_N = re.compile(r"\b(?:volume|som)\b\D{0,20}?(\d{1,3})\s*(?:%|por\s*cento)?\b")
VOLUME_MAX = re.compile(r"\b(?:volume|som)\b.{0,20}\b(?:maximo|no\s+talo|todo)\b")
VOLUME_MAIS = re.compile(r"\b(?:aument\w*|sob\w*|aument\w*|levant\w*)\s+(?:um\s+pouco\s+)?(?:o\s+|a\s+)?(?:volume|som)\b|"
                         r"\b(?:volume|som)\s+mais\s+alto\b|\bmais\s+alto\b")
VOLUME_MENOS = re.compile(r"\b(?:abaix\w*|baix\w*|diminu\w*|reduz\w*)\s+(?:um\s+pouco\s+)?(?:o\s+|a\s+)?(?:volume|som)\b|"
                          r"\b(?:volume|som)\s+mais\s+baixo\b|\bmais\s+baixo\b")
MUDO = re.compile(r"\b(?:muta|mute|muda\s+o\s+som\s+pra\s+mudo|silencia\w*\s+o\s+(?:pc|som|computador)|"
                  r"tira\s+o\s+som|desliga\s+o\s+som|(?:modo\s+)?mudo|sem\s+som)\b")
DESMUDO = re.compile(r"\b(?:desmut\w*|volta\w*\s+o\s+som|liga\w*\s+o\s+som|(?:tir\w*|sai\w*)\s+(?:o\s+som\s+)?d?o\s+mudo|"
                     r"(?:deslig\w*|desativ\w*|tir\w*)\s+o\s+mudo)\b")
SAIDA = re.compile(r"\b(?:mud\w*|troc\w*|coloc\w*|pass\w*|jog\w*|poe|bota|usa)\s+(?:o\s+|a\s+)?"
                   r"(?:(?:som|audio|saida(?:\s+de\s+(?:som|audio))?)\s+)?(?:para|pra|pro|no|na)\s+(?:o\s+|a\s+|os\s+|as\s+)?(.+)$")
SAIDA_EXPLICITA = re.compile(r"\b(?:som|audio|saida)\b")
APARELHO = {"fone": ["fone", "headphone", "headset", "auricular", "earphone"],
            "headphone": ["fone", "headphone", "headset"],
            "headset": ["headset", "fone"],
            "caixa": ["alto-falante", "alto falante", "speaker", "caixa"],
            "alto falante": ["alto-falante", "alto falante", "speaker"],
            "monitor": ["hdmi", "monitor", "display"], "tela": ["hdmi", "monitor", "display"],
            "tv": ["hdmi", "tv", "display"], "televisao": ["hdmi", "tv", "display"], "hdmi": ["hdmi"]}
TOCANDO = re.compile(r"\b(?:o\s+)?que\s+(?:e\s+que\s+)?(?:esta|ta)\s+tocando\b|\bque\s+musica\s+(?:e\s+essa|e\s+esta|"
                     r"esta\s+tocando|ta\s+tocando)\b|\bqual\s+(?:e\s+)?(?:o\s+nome\s+d)?(?:a|essa)\s+musica\b")
PROXIMA = re.compile(r"^(?:pula|passa|proxima|avanca)(?:\s+(?:essa|esta))?$|\b(?:proxima\s+|pul\w*\s+(?:essa\s+|a\s+)?|pass\w*\s+(?:essa\s+|a\s+)?|"
                     r"avanc\w*\s+(?:a\s+)?)(?:musica|faixa|video)\b")
ANTERIOR = re.compile(r"\b(?:musica|faixa)\s+anterior\b|\bvolt\w*\s+(?:pr[ao]\s+|a\s+)?anterior\b|\bvolt\w*\s+(?:pr[ao]\s+|a\s+)?(?:musica|faixa)\b|^anterior\b")
PAUSA = re.compile(r"^(?:pausa|pause|despausa|continua|play|retoma|da\s+play|solta\s+o\s+som)\b|"
                   r"\bpar[ae]\s+(?:de\s+tocar|(?:a|essa|esta)\s+(?:musica|video|faixa))\b|"
                   r"\b(?:pausa\w*|pause|continu\w*|despaus\w*|retom\w*|solt\w*|da\s+play\s+n[ao])\s+(?:a\s+|o\s+|na\s+|no\s+)?"
                   r"(?:musica|video|midia|spotify|playlist|podcast|youtube)\b")
MINIMIZA_TUDO = re.compile(r"\bminimiz\w*\s+tud\w*|\bmostr\w*\s+(?:a\s+)?area\s+de\s+trabalho\b")
OUTRO_MONITOR = re.compile(r"\b(?:jog\w*|mand\w*|mov\w*|pass\w*|lev\w*|coloc\w*|poe)\s+(?:o\s+|a\s+)?(.*?)\s*"
                           r"(?:pr[oa]|para\s+[oa]|n[oa])\s+(?:outr[oa]|segund[oa]|ultim[oa])\s+(?:monitor|tela)\b")
JANELA = re.compile(r"\b(maximiz|minimiz|restaur)\w*\s+(?:o\s+|a\s+)?(.+)$")
ESSA_JANELA = re.compile(r"^(?:ess[ae]|est[ae]|a|o|isso|aqui)?\s*(?:janela|tela|programa|app|aplicativo)?$")
PASTAS = {"downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "download": "{374DE290-123F-4565-9164-39C4925E467B}",
          "documentos": "Personal", "imagens": "My Pictures", "fotos": "My Pictures", "area de trabalho": "Desktop",
          "desktop": "Desktop", "videos": "My Video", "musicas": "My Music"}
PASTA = re.compile(r"\b(?:abr\w*|mostr\w*|ve\s+)\s+(?:a\s+|as\s+|os\s+)?(?:minha\s+|minhas\s+|meus\s+)?"
                   r"(?:pasta\s+(?:de\s+|do\s+|da\s+|dos\s+|das\s+)?)?(" + "|".join(PASTAS) + r")\b")
TIPOS = {"pdf": {".pdf"}, "planilha": {".xlsx", ".xls", ".csv", ".ods"}, "documento": {".docx", ".doc", ".odt", ".pdf", ".txt", ".md", ".rtf"},
         "doc": {".docx", ".doc"}, "texto": {".txt", ".md", ".docx"}, "apresentacao": {".pptx", ".ppt", ".odp"},
         "foto": {".jpg", ".jpeg", ".png", ".heic", ".webp"}, "imagem": {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"},
         "print": {".png", ".jpg"}, "video": {".mp4", ".mkv", ".mov", ".avi", ".webm"}, "musica": {".mp3", ".flac", ".wav", ".m4a"},
         "zip": {".zip", ".rar", ".7z"}, "instalador": {".exe", ".msi"}, "arquivo": set()}
# só tipos que são claramente arquivo: "procura vídeos de yoga" é pesquisa na web (o modelo decide esses)
ARQUIVO = re.compile(r"\b(?:ach\w*|procur\w*|encontr\w*|cade|onde\s+(?:esta|ta|fica|foi\s+parar))\s+(?:o\s+|a\s+|um\s+|uma\s+|"
                     r"meu\s+|minha\s+|aquel[ea]\s+)?((?:pdf|planilha|documento|doc|apresentacao|print|zip|instalador|arquivo)s?\b.*)$")
ABRIR = re.compile(r"^(?:abr\w*|inici\w*|execut\w*|ligu?e?\s+o|liga\s+o|carreg\w*\s+o)\s+(?:o\s+|a\s+)?"
                   r"(?:app\s+|aplicativo\s+|programa\s+)?(?:do\s+|da\s+)?(.+)$")
FECHAR = re.compile(r"^(?:fech\w*|encerr\w*|mat\w*|finaliz\w*|sai\w*\s+d[oa])\s+(?:o\s+|a\s+|os\s+|as\s+)?"
                    r"(?:app\s+|aplicativo\s+|programa\s+)?(?:do\s+|da\s+)?(.+)$")


DEZENAS = {"dez": 10, "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70,
           "oitenta": 80, "noventa": 90, "cem": 100}
UNIDADES = {"um": 1, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9}


def _numeros(c: str) -> str:
    """"trinta e cinco por cento" -> "35 por cento": o Whisper às vezes escreve o número por extenso."""
    dez = "|".join(DEZENAS)
    return re.sub(rf"\b({dez})(?:\s+e\s+({'|'.join(UNIDADES)}))?\b",
                  lambda m: str(DEZENAS[m[1]] + (UNIDADES[m[2]] if m[2] else 0)), c)


def entende(comando: str) -> tuple[str, str] | None:
    """(ação, argumento) quando o pedido é uma das ações do PC; None para o resto (pergunta, Forja...)."""
    c = _numeros(PEDIDO_EDUCADO.sub("", _norm(comando).strip(" .!?,")).strip())
    c = re.sub(r"[.!?]+$", "", c).strip()
    if TRAVAR.search(c):
        return "travar", ""
    if NAO_PERTURBE.search(c):
        return "nao_perturbe", ""
    if TOCANDO.search(c):
        return "tocando", ""
    if DESMUDO.search(c):
        return "mudo", "0"
    if VOLUME_MAX.search(c):
        return "volume", "100"
    if m := VOLUME_N.search(c):
        return "volume", m.group(1)
    if VOLUME_MAIS.search(c):
        return "volume", "+10"
    if VOLUME_MENOS.search(c):
        return "volume", "-10"
    if m := OUTRO_MONITOR.search(c):
        alvo = m.group(1).strip()
        return "outro_monitor", "" if ESSA_JANELA.match(alvo) else alvo
    if m := SAIDA.search(c):
        alvo = m.group(1).strip()
        if SAIDA_EXPLICITA.search(c[:m.start(1)]) or any(re.search(rf"\b{a}", alvo) for a in APARELHO):
            return "saida", alvo
    if MUDO.search(c):
        return "mudo", "1"
    if PROXIMA.search(c):
        return "midia", "proxima"
    if ANTERIOR.search(c):
        return "midia", "anterior"
    if PAUSA.search(c):
        return "midia", "pausa"
    if MINIMIZA_TUDO.search(c):
        return "minimizar_tudo", ""
    if m := JANELA.search(c):
        alvo = m.group(2).strip()
        return {"maximiz": "maximizar", "minimiz": "minimizar", "restaur": "restaurar"}[m.group(1)], \
            "" if ESSA_JANELA.match(alvo) else alvo
    if m := PASTA.search(c):
        return "pasta", m.group(1)
    if m := ARQUIVO.search(c):
        return "arquivo", m.group(1)
    if m := FECHAR.match(c):
        return "fechar", "" if ESSA_JANELA.match(m.group(1).strip()) else m.group(1)
    if m := ABRIR.match(c):
        return "abrir", m.group(1)
    return None


ACOES = {"abrir", "fechar", "volume", "mudo", "saida", "travar", "nao_perturbe", "outro_monitor", "maximizar", "minimizar",
         "restaurar", "minimizar_tudo", "midia", "tocando", "pasta", "arquivo"}


def valida(acao: str, alvo: str, do_modelo: bool = True) -> tuple[str, str] | None:
    """A chamada da ferramenta controlar_pc (o modelo escolheu a ação): só ações da lista e argumentos no formato de
    cada uma. O alvo nunca vira comando: só escolhe entre o que o Windows listou."""
    acao, alvo = (acao or "").strip().lower(), str(alvo or "").strip()
    if acao not in ACOES:
        return None
    a = _norm(alvo)
    if acao == "volume":
        if re.search(r"max|tudo|100", a):
            return acao, "100"
        m = re.match(r"^([+-]?)\s*(\d{1,3})", a.replace("%", ""))
        if not m:
            return (acao, "+10") if re.search(r"aument|sob|mais|alto", a) else (acao, "-10") if re.search(r"abaix|diminu|menos|baix", a) \
                else None
        return acao, f"{m[1]}{min(100, int(m[2]))}"
    if acao == "mudo":
        return acao, "0" if a in ("0", "nao", "false", "desliga", "desligar") else "1"
    if acao == "midia":
        return acao, "proxima" if "prox" in a else "anterior" if "anter" in a or "volt" in a else "pausa"
    if acao == "pasta":
        a = a.replace("_", " ")
        return (acao, a) if a in PASTAS else None
    if acao == "saida" and not alvo:
        return None
    alvo = "" if ESSA_JANELA.match(a) else alvo
    if acao == "fechar" and not alvo and do_modelo:
        return None  # "limpa a tela" não pode virar "fecha a janela da frente": fechar pelo modelo só com o programa dito
    return acao, alvo


def certeza(acao: str, arg: str) -> bool:
    """O atalho por regra só vale se o alvo existe: "abre as opções de receitas" não é programa nenhum, então o pedido
    segue para o modelo (que responde, ou chama a ferramenta com o nome certo)."""
    if acao == "abrir":
        return acha_app(arg) is not None
    if acao in ("fechar", "maximizar", "minimizar", "restaurar", "outro_monitor") and arg:
        return bool(acha_janela(arg))
    return True


# ------------------------------------------------------------------ Windows

def _user32():
    return ctypes.windll.user32


VK = {"mudo": 0xAD, "proxima": 0xB0, "anterior": 0xB1, "pausa": 0xB3, "win": 0x5B, "shift": 0x10, "direita": 0x27, "d": 0x44}


def _teclas(*nomes: str) -> None:
    """Aperta as teclas juntas (atalho) e solta na ordem inversa."""
    u = _user32()
    for n in nomes:
        u.keybd_event(VK[n], 0, 0, 0)
    for n in reversed(nomes):
        u.keybd_event(VK[n], 0, 2, 0)  # KEYEVENTF_KEYUP


def _exe(pid: int) -> str:
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf, n = ctypes.create_unicode_buffer(1024), ctypes.c_ulong(1024)
        return buf.value if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        k.CloseHandle(h)


def janelas() -> list[dict]:
    """Janelas visíveis com título (as que aparecem na barra de tarefas, mais ou menos): hwnd, título, exe, pid."""
    u, saida = _user32(), []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cada(h, _):
        n = u.GetWindowTextLengthW(h)
        camuflada = ctypes.c_int(0)  # DWMWA_CLOAKED: app da Store suspenso, "visível" mas fora da tela
        ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 14, ctypes.byref(camuflada), 4)
        if n and u.IsWindowVisible(h) and not u.GetWindow(h, 4) and not camuflada.value:  # GW_OWNER: diálogo sai
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, buf, n + 1)
            pid = ctypes.c_ulong()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if buf.value not in ("Program Manager",):
                saida.append({"hwnd": h, "titulo": buf.value, "exe": _exe(pid.value), "pid": pid.value})
        return True

    u.EnumWindows(proc(cada), 0)
    return saida


def _nota(dito: str, *nomes: str) -> float:
    """Quanto o nome dito parece com um dos nomes (programa, título): palavra inteira dentro vale mais que parecido."""
    d = _norm(dito).strip()
    melhor = 0.0
    for nome in nomes:
        n = _norm(nome)
        if not n:
            continue
        if d == n:
            return 2.0
        if re.search(rf"\b{re.escape(d)}\b", n):
            melhor = max(melhor, 1.5 - min(len(n), 60) / 200)  # "spotify" em "Spotify Premium" ganha de um título longo
        compacto = re.sub(r"[^a-z0-9]", "", n)
        dc = re.sub(r"[^a-z0-9]", "", d)
        if dc and dc in compacto:
            melhor = max(melhor, 1.2)
        melhor = max(melhor, difflib.SequenceMatcher(None, dc, compacto[:len(dc) + 3]).ratio())
    return melhor


APELIDOS = {"vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
            "explorador": "explorador de arquivos", "arquivos": "explorador de arquivos",
            "word": "word", "excel": "excel", "bloco de notas": "bloco de notas", "zap": "whatsapp", "whats": "whatsapp",
            "gerenciador de tarefas": "gerenciador de tarefas", "configuracoes": "configuracoes"}


def _sem_artigo(dito: str) -> str:
    d = _norm(dito).strip(" .!?,")
    d = re.sub(r"^(?:o|a|os|as|meu|minha)\s+", "", d)
    return APELIDOS.get(d, d)


NAVEGADORES = ("chrome", "msedge", "firefox", "vivaldi", "brave", "opera", "edge")


def _navegador(d: str) -> bool:
    return d in ("navegador", "browser", "internet")


def _forja(j: dict) -> bool:
    """Janela do próprio Forja (a casca Electron é o pai deste backend): não se fecha nem se esconde por voz."""
    return j["pid"] in (os.getpid(), os.getppid()) or Path(j["exe"]).stem.lower() in ("forja", "electron")


def acha_janela(dito: str, todas: list[dict] | None = None) -> list[dict]:
    """As janelas do programa dito (todas do mesmo exe), da mais parecida para a menos; [] se nenhuma parece."""
    d = _sem_artigo(dito)
    notas = []
    for j in todas if todas is not None else janelas():
        nota = _nota(d, Path(j["exe"]).stem, j["titulo"])
        if _navegador(d) and Path(j["exe"]).stem.lower() in NAVEGADORES:
            nota = 1.5  # "fecha o navegador": o que estiver aberto (Chrome, Edge, Vivaldi...)
        if nota >= 0.75:
            notas.append((nota, j))
    if not notas:
        return []
    notas.sort(key=lambda x: -x[0])
    exe = notas[0][1]["exe"]
    return [j for _, j in notas if j["exe"] == exe] if exe and Path(exe).stem.lower() != "applicationframehost" \
        else [notas[0][1]]


_apps: dict = {"t": 0.0, "lista": []}


def apps() -> list[dict]:
    """Programas do menu Iniciar (os .lnk e os da Store) como o Windows lista: Name e AppID. Guardado por 10 min."""
    if time.time() - _apps["t"] > 600 or not _apps["lista"]:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
                           capture_output=True, timeout=30, **native.popen_kwargs())
        lista = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
        _apps.update(t=time.time(), lista=lista if isinstance(lista, list) else [lista])
    return _apps["lista"]


# programas do Iniciar que não são "o programa": desinstalar, leia-me, site...
APP_RUIM = re.compile(r"\b(?:uninstall|desinstal|readme|leia-me|help|ajuda|website|manual|documentation|release notes)\b", re.I)


def acha_app(dito: str, lista: list[dict] | None = None) -> dict | None:
    d = _sem_artigo(dito)
    melhor, nota = None, 0.0
    for a in lista if lista is not None else apps():
        if APP_RUIM.search(a.get("Name", "")):
            continue
        n = _nota(d, a.get("Name", ""))
        if _navegador(d) and any(b in _norm(a.get("Name", "")) for b in NAVEGADORES):
            n = 1.0  # "abre o navegador": o primeiro instalado da lista
        if n > nota:
            melhor, nota = a, n
    return melhor if nota >= 0.8 else None


def _cs_dll() -> Path:
    fonte = Path(__file__).with_name("pc_audio.cs")
    dll = config.DATA_DIR / "cache" / f"pc_audio-{hashlib.sha1(fonte.read_bytes()).hexdigest()[:10]}.dll"
    if not dll.exists():
        dll.parent.mkdir(parents=True, exist_ok=True)
        _ps(f"Add-Type -Path '{fonte}' -OutputAssembly '{dll}' -OutputType Library")
    return dll


def _ps(script: str, timeout: int = 30) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script],
                       capture_output=True, timeout=timeout, **native.popen_kwargs())
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip()[-300:] or f"PowerShell saiu com {r.returncode}")
    return r.stdout.decode("utf-8", "replace").strip()


def audio(*args: str) -> str:
    """volume N | mais N | mudo 1/0 | lista | saida <id> (pc_audio.cs). Os argumentos nunca são o texto falado: são
    números e ids que o próprio Windows listou."""
    lista = ",".join("'" + a.replace("'", "''") + "'" for a in args)
    return _ps(f"Add-Type -Path '{_cs_dll()}'; [ForjaAudio]::Run(@({lista}))")


def saidas() -> list[dict]:
    return [{"id": i, "nome": n, "padrao": p == "1"}
            for i, n, p in (linha.split("\t") for linha in audio("lista").splitlines() if linha.count("\t") == 2)]


def acha_saida(dito: str, lista: list[dict]) -> dict | None:
    """O dispositivo de som dito ("fone", "caixa", "monitor", ou parte do nome). Empate: o que ainda não é o padrão
    (pedir "o fone" com um fone já no ar quer o outro). Virtuais (Steam, microfone que também toca) perdem."""
    d = _sem_artigo(dito)
    chaves = next((v for k, v in APARELHO.items() if re.search(rf"\b{k}", d)), [])
    resto = [w for w in re.findall(r"[a-z0-9]+", d) if len(w) >= 3 and not any(w.startswith(k[:4]) for k in APARELHO)]
    melhor, nota = None, 0.0
    for s in lista:
        n = _norm(s["nome"])
        pontos = (1.0 if any(k in n for k in chaves) else 0.0) + sum(0.6 for w in resto if w in n)
        if not chaves and not resto:
            pontos = _nota(d, s["nome"])
        if pontos <= 0:
            continue
        pontos -= 0.5 if re.search(r"steam|virtual|microphone|microfone|cable", n) else 0
        pontos -= 0.1 if s["padrao"] else 0
        if pontos > nota:
            melhor, nota = s, pontos
    return melhor if nota > 0.3 else None


TOCANDO_PS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$tipo = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]
$m = Await ($tipo::RequestAsync()) $tipo
$s = $m.GetCurrentSession()
if ($s) { $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties,Windows.Media.Control,ContentType=WindowsRuntime])
  @{titulo=$p.Title; artista=$p.Artist; app=$s.SourceAppUserModelId; estado=[string]$s.GetPlaybackInfo().PlaybackStatus} | ConvertTo-Json -Compress }
"""


def tocando() -> dict | None:
    """O que o controle de mídia do Windows mostra agora (Spotify, YouTube no navegador...): título, artista, app."""
    saida = _ps(TOCANDO_PS)
    return json.loads(saida) if saida else None


def pasta_conhecida(nome: str) -> Path:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
        valor, _ = winreg.QueryValueEx(k, PASTAS[nome])
    return Path(os.path.expandvars(valor))


# ------------------------------------------------------------------ achar arquivo

QUANDO = {"hoje": 0, "ontem": 1, "anteontem": 2}
VAZIAS = {"de", "do", "da", "dos", "das", "o", "a", "os", "as", "um", "uma", "que", "meu", "minha", "com", "sobre", "no",
          "na", "em", "pra", "para", "esse", "essa", "aquele", "aquela", "baixei", "baixado", "salvei", "salvo", "fiz",
          "recebi", "pc", "computador", "arquivo", "arquivos"}


def busca_arquivo(dito: str, raizes: list[Path], agora: datetime | None = None) -> tuple[Path | None, str]:
    """O arquivo mais parecido com o pedido ("PDF do contrato de ontem"): tipo pela extensão, dia pela data de
    modificação, e as outras palavras no nome. Empate: o mais novo. Devolve (caminho, descrição do filtro)."""
    agora = agora or datetime.now()
    d = _norm(dito)
    tipo = next((t for t in TIPOS if re.search(rf"\b{t}s?\b", d)), "arquivo")
    exts = TIPOS[tipo]
    desde, ate = None, None
    for palavra, dias in QUANDO.items():
        if re.search(rf"\b(?:de\s+|d[ao]\s+dia\s+de\s+)?{palavra}\b", d):
            dia = (agora - timedelta(days=dias)).replace(hour=0, minute=0, second=0, microsecond=0)
            desde, ate = dia, dia + timedelta(days=1)
            d = re.sub(rf"\b(?:de\s+)?{palavra}\b", " ", d)
    if re.search(r"\b(?:dess?a|desta|essa|esta)\s+semana\b", d):
        desde = (agora - timedelta(days=agora.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        d = re.sub(r"\b(?:dess?a|desta|essa|esta)\s+semana\b", " ", d)
    if re.search(r"\bsemana\s+passada\b", d):
        inicio = (agora - timedelta(days=agora.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        desde, ate = inicio, inicio + timedelta(days=7)
        d = re.sub(r"\bsemana\s+passada\b", " ", d)
    palavras = [w for w in re.findall(r"[a-z0-9]+", re.sub(rf"\b{tipo}s?\b", " ", d)) if w not in VAZIAS and len(w) >= 3]
    melhor, nota, vistos = None, (0.0, 0.0), 0
    for raiz in raizes:
        for pasta, dirs, arqs in os.walk(raiz):
            dirs[:] = [x for x in dirs if not x.startswith((".", "$")) and x not in ("node_modules", "__pycache__", "venv")]
            if pasta[len(str(raiz)):].count(os.sep) >= 4:
                dirs[:] = []
            for a in arqs:
                vistos += 1
                if vistos > 50000:
                    break
                p = Path(pasta, a)
                if exts and p.suffix.lower() not in exts:
                    continue
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                quando = datetime.fromtimestamp(mtime)
                if (desde and quando < desde) or (ate and quando >= ate):
                    continue
                nome = _norm(p.stem)
                tokens = re.findall(r"[a-z0-9]+", nome)
                pontos = sum(1.0 if w in nome else 0.7 if difflib.get_close_matches(w, tokens, 1, 0.8) else 0 for w in palavras)
                if palavras and pontos < 0.7 * len(palavras) - 0.01:
                    continue
                if (pontos, mtime) > nota:
                    melhor, nota = p, (pontos, mtime)
    filtro = " ".join(x for x in [tipo if tipo != "arquivo" else "arquivo", ("com " + " ".join(palavras)) if palavras else "",
                                  next((k for k in QUANDO if re.search(rf"\b{k}\b", _norm(dito))), "")] if x)
    return melhor, filtro


def _quando_dito(p: Path, agora: datetime | None = None) -> str:
    agora = agora or datetime.now()
    q = datetime.fromtimestamp(p.stat().st_mtime)
    dias = (agora.date() - q.date()).days
    return "de hoje" if dias == 0 else "de ontem" if dias == 1 else f"de {q.day:02d}/{q.month:02d}"


# ------------------------------------------------------------------ executar

def faz(acao: str, arg: str = "") -> str:
    """Executa a ação e devolve a frase que o assistente fala. Erro do Windows vira frase também (não derruba)."""
    try:
        return _faz(acao, arg)
    except Exception as e:  # PowerShell sem permissão, COM recusou...: a pessoa ouve o que houve, o log guarda o resto
        print(f"Forja: pc: {acao} falhou: {e.__class__.__name__}: {e}", flush=True)
        return "Não consegui fazer isso no Windows agora."


def _faz(acao: str, arg: str) -> str:
    u = _user32()
    if acao == "travar":
        u.LockWorkStation()
        return "Travado."
    if acao == "nao_perturbe":
        # o Windows não tem API pública para ligar o Não incomodar: abre a página dele, a um clique
        os.startfile("ms-settings:notifications")
        return "O Windows não deixa um programa ligar o Não incomodar. Abri as configurações de notificação, é só ligar ali."
    if acao == "volume":
        v = audio("mais", arg) if arg[0] in "+-" else audio("volume", arg)
        return f"Volume em {v}."
    if acao == "mudo":
        audio("mudo", arg)
        return "Som desligado." if arg == "1" else "Som de volta."
    if acao == "saida":
        lista = saidas()
        s = acha_saida(arg, lista)
        if not s:
            nomes = ", ".join(re.sub(r"\s*\(.*\)$", "", x["nome"]) or x["nome"] for x in lista[:5])
            return f"Não achei uma saída de som parecida com {arg}. Tenho: {nomes}."
        if s["padrao"]:
            return f"O som já está saindo em {s['nome']}."
        audio("saida", s["id"])
        return f"Som saindo em {s['nome']}."
    if acao == "midia":
        _teclas(arg)
        return {"proxima": "Próxima.", "anterior": "Voltei.", "pausa": "Feito."}[arg]
    if acao == "tocando":
        t = tocando()
        if not t or not t.get("titulo"):
            return "Não tem nada tocando agora."
        quem = f", de {t['artista']}" if t.get("artista") else ""
        pausado = " Está pausado." if t.get("estado") == "Paused" else ""
        return f"Tocando {t['titulo']}{quem}.{pausado}"
    if acao == "minimizar_tudo":
        _teclas("win", "d")
        return "Pronto."
    if acao in ("maximizar", "minimizar", "restaurar", "outro_monitor"):
        if arg:
            alvo = [j for j in acha_janela(arg) if acao != "minimizar" or not _forja(j)][:1]
            if not alvo:
                return f"Não achei uma janela aberta de {arg}."
            h = alvo[0]["hwnd"]
        else:
            h = u.GetForegroundWindow()
        if acao == "outro_monitor":
            u.ShowWindow(h, 9)  # SW_RESTORE: maximizada também muda, mas só depois de trazer para a frente
            u.SetForegroundWindow(h)
            time.sleep(0.15)
            _teclas("win", "shift", "direita")
            return "Mudei de monitor."
        u.ShowWindow(h, {"maximizar": 3, "minimizar": 6, "restaurar": 9}[acao])
        if acao != "minimizar":
            u.SetForegroundWindow(h)
        return "Pronto."
    if acao == "pasta":
        p = pasta_conhecida(arg)
        os.startfile(p)
        return f"Abrindo {arg}."
    if acao == "arquivo":
        raizes = [pasta_conhecida(n) for n in ("downloads", "documentos", "area de trabalho", "imagens", "videos")]
        p, filtro = busca_arquivo(arg, [r for r in dict.fromkeys(raizes) if r.exists()])
        if not p:
            return f"Não achei nenhum {filtro}."
        subprocess.Popen(["explorer.exe", "/select,", str(p)])
        return f"Achei {p.name}, {_quando_dito(p)}, na pasta {p.parent.name}. Abri com ele selecionado."
    if acao == "fechar":
        if not arg:  # "fecha essa janela": a que está na frente
            h = u.GetForegroundWindow()
            todas = [j for j in janelas() if j["hwnd"] == h]
        else:
            todas = acha_janela(arg)
        if todas and all(_forja(j) for j in todas):
            return "Eu não me fecho por voz. Use o menu da bandeja."
        todas = [j for j in todas if not _forja(j)]
        if not todas:
            return f"Não achei {arg} aberto."
        for j in todas:
            u.PostMessageW(j["hwnd"], 0x0010, 0, 0)  # WM_CLOSE: o programa fecha como no X (pergunta se tem algo sem salvar)
        return f"Fechando {arg}." if arg else "Fechando."
    if acao == "abrir":
        a = acha_app(arg)
        if not a:
            return f"Não achei um programa chamado {arg}."
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{a['AppID']}"])
        return f"Abrindo {a['Name']}."
    return "Não sei fazer isso ainda."

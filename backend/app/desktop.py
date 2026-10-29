"""Controle da tela do Windows pelo agente: ver (screenshot da tela ou de uma janela), listar e focar janelas, clicar,
digitar, apertar atalhos e rolar. Para programas que não têm outra via (shell, arquivos e o navegador vêm antes).

Só ctypes e Pillow. As coordenadas que o modelo passa são as do ÚLTIMO desktop_screenshot (que sai reduzido e pode
ser de uma janela só): `_tela` guarda origem e escala para converter em pixels reais da tela.
"""
from __future__ import annotations

import ctypes
import io
import sys
import os
import time
from ctypes import wintypes
from pathlib import Path

from .tools import Tool, ToolError, register

LARGURA_MAX = 1600  # print maior que isso o modelo local demora muito para olhar (o mesmo teto do browser_screenshot)
_tela = {"x": 0, "y": 0, "escala": 1.0}  # o último print: canto superior esquerdo e real/visto
# a janela em que o agente está trabalhando (a do último desktop_focus, desktop_open ou clique): digitar e apertar
# teclas só valem nela. Em 29/10 um Ctrl+Shift+J abriu outra janela, o foco foi para o app do Claude e o JavaScript
# que era para o console do navegador foi digitado lá
_alvo = {"hwnd": 0}

VK = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "enter": 0x0D, "tab": 0x09,
      "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "space": 0x20,
      "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21,
      "pagedown": 0x22, "printscreen": 0x2C, **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def teclas(atalho: str) -> list[int]:
    """"ctrl+shift+s" -> códigos virtuais, na ordem de apertar. Letra e número viram o próprio código."""
    codigos = []
    for parte in atalho.lower().replace(" ", "").split("+"):
        if parte in VK:
            codigos.append(VK[parte])
        elif len(parte) == 1 and parte.isalnum():
            codigos.append(ord(parte.upper()))
        else:
            raise ToolError(f"Tecla desconhecida: {parte!r}. Use nomes como ctrl, alt, shift, win, enter, tab, esc, "
                            "f5, up, pagedown ou uma letra/número.")
    return codigos


def converte(x: float, y: float) -> tuple[int, int]:
    """Ponto no último print -> ponto real na tela."""
    return round(_tela["x"] + x * _tela["escala"]), round(_tela["y"] + y * _tela["escala"])


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]  # pad: o tamanho do MOUSEINPUT no x64
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _u32():
    u = ctypes.windll.user32
    u.SetProcessDPIAware()  # coordenadas em pixels reais: sem isso, com escala 125% o clique cai no lugar errado
    return u


def _janela(nome: str) -> dict:
    from . import pc
    achadas = pc.acha_janela(nome)
    if not achadas:
        abertas = ", ".join(j["titulo"][:40] for j in pc.janelas()[:15])
        raise ToolError(f"Nenhuma janela parecida com {nome!r}. Abertas: {abertas}")
    return achadas[0]


async def texto_na_tela(img, escala: float, limite: int = 200) -> str:
    """O texto da imagem pelo OCR do Windows, uma linha por item com o ponto (já na escala do print) para clicar. São os
    "olhos" de modelo sem visão: ele lê a tela e clica no texto. Na resolução cheia, antes de reduzir: letra pequena
    de um monitor grande some no print reduzido."""
    from . import ocr
    motor = ocr._motor_windows()
    if motor is None:
        return ""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    try:
        r = await ocr.reconhece_windows(buf.getvalue(), motor)
    except Exception:  # imagem grande demais para o motor, idioma sem OCR: segue só com o print
        return ""
    itens = []
    for linha in r.lines:
        caixas = [p.bounding_rect for p in linha.words]
        if not caixas or not linha.text.strip():
            continue
        x0, y0 = min(c.x for c in caixas), min(c.y for c in caixas)
        x1, y1 = max(c.x + c.width for c in caixas), max(c.y + c.height for c in caixas)
        itens.append((round((y0 + y1) / 2 / escala), round((x0 + x1) / 2 / escala), linha.text.strip()))
    itens.sort()
    return "\n".join(f"- {t} ({x}, {y})" for y, x, t in itens[:limite])


async def screenshot(_root: Path, args: dict) -> dict:
    from PIL import ImageGrab

    from . import uploads
    u = _u32()
    if nome := str(args.get("janela") or "").strip():
        foca(_root, {"janela": nome})  # o print recorta a TELA: com outra janela por cima, sairia ela
        j = _janela(nome)
        r = wintypes.RECT()
        u.GetWindowRect(j["hwnd"], ctypes.byref(r))
        img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
        origem, titulo = (r.left, r.top), f"janela '{j['titulo']}'"
    else:
        img = ImageGrab.grab()  # o monitor principal
        origem, titulo = (0, 0), "tela principal"
    escala = max(1.0, img.width / LARGURA_MAX)
    texto = await texto_na_tela(img, escala)
    if escala > 1:
        img = img.resize((round(img.width / escala), round(img.height / escala)))
    _tela.update(x=origem[0], y=origem[1], escala=escala)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=75)
    att = uploads.salvar_captura("tela.jpg", buf.getvalue())
    lido = f"\n\nTexto na tela (OCR), com o ponto de cada linha para o desktop_click:\n{texto}" if texto else ""
    return {"text": f"Screenshot da {titulo} anexado ({img.width}x{img.height}). Coordenadas de desktop_click e "
                    f"desktop_scroll são em pixels DESTE print.{lido}", "attachments": [att]}


def janelas(_root: Path, _args: dict) -> str:
    from . import pc
    lista = pc.janelas()
    return "\n".join(f"- {j['titulo']} ({Path(j['exe']).name})" for j in lista) or "Nenhuma janela aberta."


def abre(_root: Path, args: dict) -> str:
    """Abre um programa do menu Iniciar pelo "abrir" do pc.py (só o que o Windows lista, sem shell) e diz se surgiu
    janela nova: o Bloco de Notas do Windows 11 abre ABA na janela que já existe, e o "foca pelo nome" depois pegava a
    aba do documento da pessoa (em 28/10 o teste digitou dentro de um .txt dela)."""
    from . import pc
    nome = str(args.get("programa") or "").strip()
    antes = {j["hwnd"] for j in pc.janelas()}
    dito = pc.faz("abrir", nome)
    for _ in range(25):
        time.sleep(0.2)
        if novas := [j for j in pc.janelas() if j["hwnd"] not in antes]:
            _alvo["hwnd"] = novas[0]["hwnd"]
            return f"{dito} Janela nova: '{novas[0]['titulo']}'."
    return (f"{dito} Nenhuma janela NOVA apareceu: o programa pode ter aberto uma aba numa janela que já existia, e a "
            "aba da frente pode ser um documento da pessoa. Não digite sem conferir; para escrever texto num arquivo, "
            "use write_file.")


def _frente() -> str:
    u = _u32()
    h = u.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(h, buf, 512)
    return buf.value


def _traz(hwnd: int) -> bool:
    u = _u32()
    for _ in range(2):
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
        u.keybd_event(0x12, 0, 0, 0)  # um Alt solto: o Windows só deixa trazer janela para a frente depois de "entrada"
        u.keybd_event(0x12, 0, 2, 0)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        if u.GetForegroundWindow() == hwnd:
            return True
    return False


def foca(_root: Path, args: dict) -> str:
    j = _janela(str(args.get("janela") or ""))
    if not _traz(j["hwnd"]):
        raise ToolError(f"O Windows não deixou trazer '{j['titulo']}' para a frente (a da frente é '{_frente()}').")
    _alvo["hwnd"] = j["hwnd"]
    return f"Janela '{j['titulo']}' em primeiro plano."


def _confere_alvo() -> None:
    """Teclado só vai para a janela em que o agente está trabalhando, e nunca para o próprio Forja."""
    u = _u32()
    if _alvo["hwnd"] and u.GetForegroundWindow() != _alvo["hwnd"] and not _traz(_alvo["hwnd"]):
        raise ToolError(f"A janela da frente agora é '{_frente()}', não a que você estava usando: não digitei nada. "
                        "Foque a janela certa com desktop_focus.")
    pid = ctypes.c_ulong()
    u.GetWindowThreadProcessId(u.GetForegroundWindow(), ctypes.byref(pid))
    if pid.value in (os.getpid(), os.getppid()):
        raise ToolError("A janela da frente é o próprio Forja: foque o programa em que quer digitar.")


def clica(_root: Path, args: dict) -> str:
    u = _u32()
    x, y = converte(float(args["x"]), float(args["y"]))
    u.SetCursorPos(x, y)
    desce, sobe = {"direito": (0x08, 0x10), "meio": (0x20, 0x40)}.get(str(args.get("botao") or ""), (0x02, 0x04))
    for _ in range(2 if args.get("duplo") else 1):
        u.mouse_event(desce, 0, 0, 0, 0)
        u.mouse_event(sobe, 0, 0, 0, 0)
    time.sleep(0.3)
    _alvo["hwnd"] = u.GetForegroundWindow()  # clicou numa janela: é nela que se trabalha agora
    return f"Clicou em ({args['x']}, {args['y']}) do print. Tire outro desktop_screenshot para conferir."


def digita(_root: Path, args: dict) -> str:
    texto = str(args.get("texto") or "")
    if not texto:
        raise ToolError("texto vazio")
    u = _u32()
    _confere_alvo()
    eventos = []
    for c in texto.replace("\r\n", "\n"):
        if c == "\n":
            eventos += [(0x0D, 0, 0), (0x0D, 0, 2)]  # Enter de verdade: o unicode "\n" não quebra linha em todo app
            continue
        b = c.encode("utf-16-le")  # emoji e afins são dois códigos UTF-16: cada um vai como uma "tecla"
        for i in range(0, len(b), 2):
            unidade = int.from_bytes(b[i:i + 2], "little")
            eventos += [(0, unidade, 4), (0, unidade, 4 | 2)]  # KEYEVENTF_UNICODE, e o KEYUP
    arr = (_INPUT * len(eventos))(*[_INPUT(type=1, ki=_KEYBDINPUT(wVk=vk, wScan=sc, dwFlags=f)) for vk, sc, f in eventos])
    if u.SendInput(len(arr), arr, ctypes.sizeof(_INPUT)) != len(arr):
        raise ToolError("O Windows bloqueou a digitação (janela de administrador em primeiro plano?).")
    return f"Digitou {len(texto)} caracteres na janela '{_frente()}'."


def aperta(_root: Path, args: dict) -> str:
    u = _u32()
    codigos = teclas(str(args.get("teclas") or ""))
    _confere_alvo()
    for c in codigos:
        u.keybd_event(c, 0, 0, 0)
    for c in reversed(codigos):
        u.keybd_event(c, 0, 2, 0)
    time.sleep(0.2)
    return f"Apertou {args['teclas']}."


def rola(_root: Path, args: dict) -> str:
    u = _u32()
    if "x" in args and "y" in args:
        u.SetCursorPos(*converte(float(args["x"]), float(args["y"])))
    passos = int(args.get("passos") or 3)
    u.mouse_event(0x0800, 0, 0, -120 * passos if str(args.get("para") or "baixo") == "baixo" else 120 * passos, 0)
    return f"Rolou {passos} passos para {args.get('para') or 'baixo'}."


def navegador(_root: Path, args: dict) -> str:
    """Abre URLs no navegador padrão da pessoa (onde estão os logins dela), não no do Forja."""
    urls = [str(u).strip() for u in (args.get("urls") or [args.get("url")]) if str(u or "").strip()][:10]
    if not urls or any(not u.lower().startswith(("http://", "https://")) for u in urls):
        raise ToolError("Passe url ou urls começando com http:// ou https://.")
    for u in urls:
        os.startfile(u)  # noqa: S606 (só http/https: o Windows entrega ao navegador padrão)
        time.sleep(0.3)  # uma de cada vez: o navegador abre as abas na ordem
    return f"Abri {len(urls)} {'link' if len(urls) == 1 else 'links'} no navegador da pessoa."


# Todas as sessões de mídia do Windows (cada aba do navegador com vídeo é uma) e, com $acao, pausa ou toca a de
# índice $alvo pela própria sessão. A "sessão atual" e a tecla de mídia não servem: com uma aba do Instagram parada
# e o YouTube em outra, a tecla dava play no Instagram.
MIDIA_PS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$tipo = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]
$props = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties,Windows.Media.Control,ContentType=WindowsRuntime]
$m = Await ($tipo::RequestAsync()) $tipo
$lista = @($m.GetSessions())
if ('ACAO' -ne '' -and ALVO -ge 0 -and ALVO -lt $lista.Count) {
  $s = $lista[ALVO]
  if ('ACAO' -eq 'pausar') { Await ($s.TryPauseAsync()) ([bool]) | Out-Null } else { Await ($s.TryPlayAsync()) ([bool]) | Out-Null }
  Start-Sleep -Milliseconds 600
}
$i = 0
$saida = foreach ($s in $lista) { $p = Await ($s.TryGetMediaPropertiesAsync()) $props
  @{i=$i; titulo=$p.Title; app=$s.SourceAppUserModelId; estado=[string]$s.GetPlaybackInfo().PlaybackStatus}; $i++ }
ConvertTo-Json -Compress -InputObject @($saida)
"""
ESTADOS = {"Playing": "tocando", "Paused": "pausado", "Stopped": "parado"}


def _sessoes(acao: str = "", alvo: int = -1) -> list[dict]:
    import json

    from . import pc
    return json.loads(pc._ps(MIDIA_PS.replace("ACAO", acao).replace("ALVO", str(int(alvo)))) or "[]")


def escolhe_sessao(sessoes: list[dict], titulo: str) -> dict | None:
    """A sessão do título dito (pedaço, sem caixa); sem título, a que está tocando, senão a primeira."""
    t = titulo.strip().lower()
    if t:
        return next((s for s in sessoes if t in str(s.get("titulo") or "").lower()), None)
    return next((s for s in sessoes if s.get("estado") == "Playing"), sessoes[0] if sessoes else None)


def mexendo(hwnd: int, espera: float = 1.2) -> bool:
    """A janela está com imagem em movimento (vídeo tocando)? Dois prints do miolo dela, um pouco depois do outro. É o
    plano B do controle de mídia: o Vivaldi não publica as abas no Windows, e "deixei pausado" sem conferir já mentiu."""
    from PIL import ImageChops, ImageGrab, ImageStat
    r = wintypes.RECT()
    _u32().GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    caixa = (r.left + w // 5, r.top + h // 6, r.right - w // 5, r.bottom - h // 4)  # sem barras, abas e rodapé
    a = ImageGrab.grab(bbox=caixa, all_screens=True).convert("L").resize((160, 90))
    time.sleep(espera)
    b = ImageGrab.grab(bbox=caixa, all_screens=True).convert("L").resize((160, 90))
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0] > 1.5


def _pausa_na_janela(titulo: str, acao: str) -> str:
    """Sem sessão de mídia no Windows: pela janela. Traz para a frente, aperta k (YouTube) e, se não mudou, espaço
    (os outros players), conferindo pelo movimento depois de cada tecla."""
    j = _janela(titulo)
    if not _traz(j["hwnd"]):
        raise ToolError(f"O Windows não deixou trazer '{j['titulo']}' para a frente.")
    _alvo["hwnd"] = j["hwnd"]
    quer_movendo = acao == "tocar"
    if mexendo(j["hwnd"]) == quer_movendo:
        return f"'{j['titulo']}': já estava {'tocando' if quer_movendo else 'parado'} (conferido pela imagem)."
    u = _u32()
    for tecla in (0x4B, 0x20):  # k, espaço
        u.keybd_event(tecla, 0, 0, 0)
        u.keybd_event(tecla, 0, 2, 0)
        time.sleep(0.8)
        if mexendo(j["hwnd"]) == quer_movendo:
            return f"'{j['titulo']}': {'tocando' if quer_movendo else 'pausado'} (conferido pela imagem)."
    raise ToolError(f"Apertei k e espaço em '{j['titulo']}' e a imagem continua "
                    f"{'parada' if quer_movendo else 'mudando'}: não consegui {acao}.")


def midia(_root: Path, args: dict) -> str:
    """O que está tocando no PC (cada vídeo ou música aberto: abas do navegador, Spotify...) e se está pausado; pausa
    ou toca um deles pelo título, e devolve o estado conferido depois."""
    acao, titulo = str(args.get("acao") or "estado"), str(args.get("titulo") or "")
    sessoes = _sessoes()
    s = escolhe_sessao(sessoes, titulo) if sessoes else None
    if acao in ("pausar", "tocar") and not s:
        if not titulo:
            raise ToolError("Nenhuma mídia no controle do Windows: passe `titulo` (parte do título da janela do vídeo).")
        return _pausa_na_janela(titulo, acao)  # o navegador não publicou a aba: pela imagem da janela
    if not sessoes:
        return "Nenhum vídeo ou música aberto no controle de mídia do Windows."
    if acao in ("pausar", "tocar"):
        sessoes = _sessoes(acao, s["i"])
    return "\n".join(f"- '{s.get('titulo')}' ({str(s.get('app')).split('.')[0]}): {ESTADOS.get(s.get('estado'), s.get('estado'))}"
                     for s in sessoes)


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


JANELA = {"type": "string", "description": "Nome do programa ou parte do título (ex.: bloco de notas, excel, spotify)"}

if sys.platform == "win32":
    register(Tool(
        "desktop_screenshot",
        "Screenshot da tela do PC (monitor principal) ou de uma janela, com o texto que aparece nela (OCR) e o ponto de "
        "cada linha: mesmo sem visão dá para ler a tela e clicar no texto. Use antes de clicar e depois, para "
        "conferir. Para programas SEM outra via: arquivos, shell e navegador (browser_*) são mais rápidos e certos.",
        _obj({"janela": JANELA}, []), screenshot))  # como o browser_screenshot: sem visão, o print é para o usuário
    register(Tool(
        "open_in_browser", "Abre uma ou mais URLs (até 10) no navegador da PESSOA, onde estão os logins dela: para ela "
        "ver um vídeo, uma página, produtos. Diferente do browser_*, que é o navegador do Forja para você ler e testar.",
        _obj({"url": {"type": "string"}, "urls": {"type": "array", "items": {"type": "string"}}}, []), navegador,
        mutating=True))
    register(Tool(
        "desktop_media", "Cada vídeo ou música aberto no PC (abas do navegador, Spotify...) e se está tocando ou "
        "pausado; acao pausar ou tocar age no de `titulo` (um pedaço do título do vídeo ou da janela; vazio = o que "
        "está tocando) e CONFERE, pelo Windows ou pela imagem da janela. É o jeito certo de deixar um vídeo pausado: "
        "nunca diga que pausou sem ele confirmar.",
        _obj({"acao": {"type": "string", "description": "estado (padrão) | pausar | tocar"},
              "titulo": {"type": "string", "description": "Pedaço do título do vídeo ou música"}}, []), midia, mutating=True))
    register(Tool(
        "desktop_open", "Abre um programa instalado pelo nome (bloco de notas, excel, spotify, calculadora...). Diz se "
        "abriu janela nova.", _obj({"programa": {"type": "string"}}, ["programa"]), abre, mutating=True))
    register(Tool("desktop_windows", "Lista as janelas abertas no PC (título e programa).", _obj({}, []), janelas))
    register(Tool("desktop_focus", "Traz uma janela para o primeiro plano (restaura se minimizada).",
                  _obj({"janela": JANELA}, ["janela"]), foca, mutating=True))
    register(Tool(
        "desktop_click", "Clica num ponto do ÚLTIMO desktop_screenshot (coordenadas em pixels daquele print).",
        _obj({"x": {"type": "number"}, "y": {"type": "number"},
              "botao": {"type": "string", "description": "esquerdo (padrão) | direito | meio"},
              "duplo": {"type": "boolean", "description": "Clique duplo"}}, ["x", "y"]), clica, mutating=True))
    register(Tool(
        "desktop_type", "Digita texto na janela em primeiro plano (foque antes com desktop_focus ou um clique). "
        "Quebra de linha vira Enter. NUNCA digite num documento que a pessoa já tinha aberto (título com nome de "
        "arquivo dela): estraga o trabalho dela. Texto que vai para um arquivo se escreve com write_file.", _obj({"texto": {"type": "string"}}, ["texto"]), digita, mutating=True))
    register(Tool(
        "desktop_key", "Aperta uma tecla ou atalho na janela em primeiro plano: enter, esc, tab, ctrl+s, alt+f4, "
        "ctrl+shift+n, win+d, f5...", _obj({"teclas": {"type": "string"}}, ["teclas"]), aperta, mutating=True))
    register(Tool(
        "desktop_scroll", "Rola a roda do mouse, opcionalmente sobre um ponto do último print.",
        _obj({"para": {"type": "string", "description": "baixo (padrão) | cima"},
              "passos": {"type": "integer", "description": "Padrão: 3"},
              "x": {"type": "number"}, "y": {"type": "number"}}, []), rola, mutating=True))

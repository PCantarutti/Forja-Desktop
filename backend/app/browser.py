"""Navegador integrado: Chromium (Playwright) controlado pelo agente e espelhado ao vivo na UI.

Um processo Chromium compartilhado (`Manager`) e uma **sessão por conversa** (`Session` = um contexto
com suas abas). Trocar de conversa na UI troca a sessão mostrada; a chave "0" é o rascunho da tela
inicial. A sessão vive até fechar, apagar a conversa ou ficar ociosa (`BROWSER_IDLE_MINUTES`).

Dois modos, mesma API para o agente e para a UI:

- **Nativo** (Forja Desktop, `FORJA_CDP` definido): o Playwright liga por CDP ao Chromium do próprio Electron.
  Cada aba é uma `WebContentsView` dentro da janela: o usuário interage direto, sem espelho. O Electron
  não implementa `Target.createTarget`, então a aba nasce lá: o backend emite `create` no canal
  `/api/browser/host` (SSE que o main.js assina) com uma URL-marcador `about:blank?forja=CHAVE/ID`, o
  Electron cria a view carregando esse marcador e o backend acha a page pela URL. Fechar é `page.close()`
  (o Electron destrói a view sozinho). `active` diz ao Electron qual view mostrar.
- **Espelho** (web/Docker): Chromium headless; screencast CDP da aba ativa (JPEG ou PNG, `BROWSER_STREAM`)
  em SSE, e o usuário interage pelo espelho (`input`/`navigate`/abas/upload) sem aprovação: é o próprio
  usuário agindo.

Ferramentas do modelo:

- browser_navigate, browser_read, browser_console, browser_tabs, browser_scroll, browser_screenshot: leitura
- browser_click, browser_type, browser_upload: `mutating` (seguem a política de escrita)
- browser_eval: `mutating` + `always_ask` (JS arbitrário, como o run_command)

`browser_screenshot` sempre existe (o print é para o usuário ver no chat); a imagem só entra no contexto
do modelo se ele tiver a capacidade `vision` (decidido em agent._execute / build_history).
Refs `eN` (ou `f1eN` em frames) vêm do `aria_snapshot(mode="ai")` e resolvem via `aria-ref=`.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import json
import math
import re
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

from . import config, uploads
from .tools import Tool, ToolError, register, resolve_path
from .web import UNTRUSTED

NL = "\n"  # usado em f-strings/joins do browser_validate, onde a quebra é parte do formato

VIEWPORT = {"width": 1280, "height": 800}  # inicial; o painel da UI manda o tamanho real (set_viewport)
MIN_VIEWPORT, MAX_VIEWPORT = (320, 240), (3840, 2400)
NAV_TIMEOUT = 15_000
ACT_TIMEOUT = 5_000
# O print tem prazo próprio, bem maior que o de um clique: trocar o viewport obriga a página a
# refazer o layout inteiro antes da captura, e numa página de 5000px isso não cabe nos 5s do
# ACT_TIMEOUT. Medido em uso: 3 de 8 prints morriam com `Page.screenshot: Timeout 5000ms`, e os
# dois seguintes saíam no tamanho errado, com a emulação largada pela metade.
PRINT_TIMEOUT = 25_000
JPEG_QUALITY = 75  # screenshot que vai ao modelo
# O print sai em tela de desktop, e não no tamanho do painel — que é uma coluna estreita e fazia o
# site chegar ao modelo em largura de celular.
#
# 1280x720 e não 1920x1080, e isto foi medido, não escolhido: o encoder de visão de um modelo local
# degrada com o número de pixels, e a 2 MP ele desaba. No Qwen3.6-35B com projetor F16, uma única
# imagem custou:
#
#     1280x720   0.92 MP    933 tokens     8.1 s
#     1440x810   1.17 MP   1138 tokens    11.0 s
#     1600x900   1.44 MP   1413 tokens    14.7 s
#     1920x1080  2.07 MP   2053 tokens    68.1 s   <- 4.6x o tempo por 1.4x os pixels
#
# 720p é viewport de desktop de verdade, fica longe do despenhadeiro e é 8x mais rápido que 1080p.
# Quem precisar de mais detalhe pede outra medida, ou fotografa um elemento com `selector`.
PRINT_VIEWPORT = {"width": 1280, "height": 720}
# Teto da altura do print. O que custa num modelo com visão é PIXEL: a página inteira virava
# 1903x5327 (10 MP) e o encoder do llama.cpp passava mais de 100 segundos nela — o turno parecia
# travado depois de cada print. Hoje não há print de página inteira (é browser_scroll + outro
# print); o teto fica de guarda para quem pedir uma altura absurda.
ALTURA_MAX_PRINT = 2200
CAST_JPEG_QUALITY = 85  # espelho ao vivo: texto ainda nítido, frame 3-5x menor que PNG
MAX_TABS = 8
SCRATCH = "0"  # sessão do painel quando nenhuma conversa está aberta
REF_RE = re.compile(r"^(?:ref=)?((?:f\d+)?e\d+)$")  # e12 na página principal; f1e12 dentro de frame
MARKER = "about:blank?forja="  # URL inicial de uma aba nativa: identifica a view do Electron para o Playwright
MARKER_WAIT = 8.0  # segundos para o Electron criar a view depois do `create`
ERROR_PREFIXES = ("pageerror", "console.error", "requestfailed")

# Conversa dona das chamadas de ferramenta em andamento (agent.run_agent define no início do run).
CURRENT_KEY: contextvars.ContextVar[str] = contextvars.ContextVar("forja_browser_key", default=SCRATCH)


def _err(e: Exception) -> str:
    first = (str(e).splitlines() or [""])[0]
    return f"{e.__class__.__name__}: {first[:200]}"


class Session:
    """Contexto de uma conversa: abas, console, espelho e estado de interação."""

    def __init__(self, key: str, manager: "Manager"):
        self.key, self._m = key, manager
        self._ctx = None
        self.pages: list = []
        self.active = None
        self._cdp = None       # screencast da aba ativa
        self._cast_mime = "image/png"
        self._lock = asyncio.Lock()
        self.viewport = dict(VIEWPORT)  # segue o tamanho do painel na UI
        self.dpr = 1.0  # devicePixelRatio da tela onde o painel está; limita a resolução do espelho
        self.logs: deque[str] = deque(maxlen=200)
        # Espelhamento: último frame + versão; assinantes esperam a versão mudar (lento pula frames).
        self.latest: dict | None = None
        self.last_event: dict | None = None
        self.version = 0
        self.subs = 0
        self._cond = asyncio.Condition()
        self.last_used = time.monotonic()
        self.file_chooser = None  # a página pediu um arquivo; a UI oferece o upload
        self.markers: dict[int, str] = {}  # id(page) -> marcador (modo nativo)
        self._nocache: dict[int, object] = {}  # id(page) -> sessão CDP que mantém o cache desligado

    def touch(self) -> None:
        self.last_used = time.monotonic()

    # ------------------------------------------------------------ ciclo de vida

    @property
    def open(self) -> bool:
        return self.active is not None and not self.active.is_closed()

    async def ensure(self):
        """Garante contexto e uma aba ativa; devolve a page ativa."""
        self.touch()
        async with self._lock:
            if self.open:
                return self.active
            for attempt in (1, 2):
                try:
                    page = await self._new_page()
                    break
                except ToolError:
                    raise
                except Exception as e:  # contexto morto (Chromium caiu): recria uma vez
                    self._ctx, self.pages, self.active = None, [], None
                    if attempt == 2:
                        raise ToolError(f"Não consegui abrir o navegador: {_err(e)}") from e
            await self._adopt(page)
            return page

    async def _new_page(self):
        """Uma aba nova, ainda sem registrar. Nativo: pede a view ao Electron e espera a page aparecer."""
        browser = await self._m.browser()
        if self._m.native:
            if not self._m.hosts:
                raise ToolError("O app não está ligado ao navegador integrado (Electron sem conexão com o backend).")
            self._ctx = browser.contexts[0]  # todas as views caem no contexto padrão do CDP
            marker = f"{MARKER}{self.key}/{uuid.uuid4().hex[:8]}"
            self._m.host_emit({"type": "create", "key": self.key, "marker": marker})
            deadline = time.monotonic() + MARKER_WAIT
            while time.monotonic() < deadline:
                for p in self._ctx.pages:
                    if p.url == marker:
                        self.markers[id(p)] = marker
                        return p
                await asyncio.sleep(0.05)
            raise ToolError("O app não abriu a aba do navegador a tempo.")
        if self._ctx is None:
            self._ctx = await browser.new_context(viewport=dict(self.viewport), accept_downloads=False)
            self._ctx.on("page", lambda p: asyncio.ensure_future(self._adopt(p)))  # popups viram abas
        return await self._ctx.new_page()

    async def _adopt(self, page) -> None:
        """Registra uma aba (criada por nós ou popup) e a torna ativa."""
        if page in self.pages or page.is_closed():
            return
        if len(self.pages) >= MAX_TABS:
            self._log(page, f"popup fechado: limite de {MAX_TABS} abas")
            await page.close()
            return
        self.pages.append(page)
        page.on("console", lambda m: self._log(page, f"console.{m.type}: {m.text}"))
        page.on("pageerror", lambda e: self._log(page, f"pageerror: {e}"))
        page.on("requestfailed", lambda r: self._log(page, f"requestfailed: {r.method} {r.url} ({r.failure})"))
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))  # senão click trava em alert()
        if not self._m.native:  # nativo: o diálogo de arquivo do sistema abre sozinho
            page.on("filechooser", lambda fc: asyncio.ensure_future(self._on_filechooser(fc)))
        page.on("framenavigated", lambda f: f == page.main_frame and asyncio.ensure_future(self._publish_state()))
        page.on("close", lambda _: asyncio.ensure_future(self._on_page_close(page)))
        await self._disable_cache(page)
        await self._activate(page)

    async def _disable_cache(self, page) -> None:
        """Cache HTTP desligado nesta aba, como o "Disable cache" do DevTools.

        Servidor de dev sem Cache-Control (python -m http.server, serve_start) só manda Last-Modified, e o
        Chromium aplica frescor heurístico: 10% da idade do arquivo. Arquivo criado há uma hora e carregado
        agora fica 6 min "fresco" — o agente edita, chama browser_navigate na mesma URL, e a página (ou o
        script.js) volta do cache: o print não muda e ele entra em loop editando. `reload` só revalida o
        documento; isto cobre os sub-recursos também. A sessão CDP fica referenciada: ao soltar, o efeito some.
        """
        try:
            cdp = await self._ctx.new_cdp_session(page)
            await cdp.send("Network.enable")  # setCacheDisabled sozinho não vale nada
            await cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
            self._nocache[id(page)] = cdp
        except Exception:
            pass  # aba já fechou ou CDP indisponível: segue com cache, que é o comportamento antigo

    def _tab_index(self, page) -> int:
        return self.pages.index(page) + 1 if page in self.pages else 0

    def _log(self, page, line: str) -> None:
        self.logs.append(f"[aba {self._tab_index(page)}] {line}"[:500])

    async def _activate(self, page) -> None:
        if page is self.active or page.is_closed():
            return
        await self._stop_cast()
        self.active = page
        self.latest = None
        try:
            await page.bring_to_front()  # só a aba visível gera frames do screencast
        except Exception:
            pass
        if self.subs:
            await self._start_cast()
        if self._m.native:
            self._m.host_emit({"type": "active", "key": self.key, "marker": self.markers.get(id(page), "")})
        await self._publish_state()

    async def _on_page_close(self, page) -> None:
        was_active = page is self.active
        self.markers.pop(id(page), None)
        self._nocache.pop(id(page), None)
        if page in self.pages:
            self.pages.remove(page)
        if was_active:
            self.active, self._cdp, self.latest = None, None, None
            if self.pages:
                await self._activate(self.pages[-1])
                return
        await self._publish_state()

    async def close(self) -> None:
        """Fecha a sessão (botão da UI, conversa apagada ou ociosidade). O próximo uso abre outra."""
        await self._stop_cast()
        ctx, self._ctx = self._ctx, None
        pages, self.pages, self.active, self.latest, self.file_chooser = self.pages, [], None, None, None
        self.markers.clear()
        if self._m.native:  # o contexto é o do Electron, compartilhado: fecha só as nossas abas (as views somem)
            for p in pages:
                try:
                    await p.close()
                except Exception:
                    pass
            self._m.host_emit({"type": "active", "key": self.key, "marker": ""})
        elif ctx:
            try:
                await ctx.close()
            except Exception:
                pass
        await self._publish_state()

    # ------------------------------------------------------------ abas

    async def tabs(self) -> list[dict]:
        out = []
        for i, p in enumerate(self.pages, 1):
            try:
                title = await p.title()
            except Exception:
                title = ""
            out.append({"index": i, "url": p.url, "title": title, "active": p is self.active})
        return out

    def _page_at(self, index) -> object:
        try:
            i = int(index)
            if i < 1:
                raise ValueError
            return self.pages[i - 1]
        except (TypeError, ValueError, IndexError):
            raise ToolError(f"Aba {index} não existe. Abas abertas: 1 a {len(self.pages)}.") from None

    async def new_tab(self, url: str = "") -> None:
        if not self.open:
            page = await self.ensure()  # sessão fechada: a primeira aba já é a nova
        else:
            if len(self.pages) >= MAX_TABS:
                raise ToolError(f"Máximo de {MAX_TABS} abas abertas. Feche uma com browser_tabs close.")
            self.touch()
            page = await self._new_page()
            await self._adopt(page)
        if url:
            await self._goto(page, url)

    async def switch_tab(self, index) -> None:
        self.touch()
        await self._activate(self._page_at(index))

    async def close_tab(self, index) -> None:
        self.touch()
        page = self._page_at(index)
        # Contabilidade antes do close(): o evento "close" chega depois e só publica o estado.
        self.pages.remove(page)
        if page is self.active:
            self.active, self._cdp, self.latest = None, None, None
            if self.pages:
                await self._activate(self.pages[-1])
        try:
            await page.close()
        except Exception:
            pass
        if not self.pages:
            await self._publish_state()

    async def _goto(self, page, url: str) -> None:
        try:
            await page.goto(check_url(url), wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Falha ao abrir {url}: {_err(e)}. O servidor está de pé nessa porta?") from e

    # ------------------------------------------------------------ espelhamento

    def state(self) -> dict:
        return {"type": "state", "key": self.key, "open": self.open, "url": self.active.url if self.open else "",
                "title": "", "width": self.viewport["width"], "height": self.viewport["height"],
                "scale": self._m.scale or int(config.BROWSER_SCALE), "tabs": [],
                "file_chooser": self.file_chooser is not None, "native": self._m.native}

    async def state_with_title(self) -> dict:
        st = self.state()
        if self.open:
            try:
                st["title"] = await self.active.title()
            except Exception:
                pass
            st["tabs"] = await self.tabs()
        return st

    async def _publish_state(self) -> None:
        await self._publish(await self.state_with_title())

    async def _publish(self, ev: dict) -> None:
        async with self._cond:
            if ev["type"] == "frame":
                self.latest = ev
            self.last_event = ev
            self.version += 1
            self._cond.notify_all()

    async def _start_cast(self) -> None:
        if self._cdp or not self.open or self._m.native:
            return
        self._cdp = cdp = await self._ctx.new_cdp_session(self.active)
        cdp.on("Page.screencastFrame", lambda ev: asyncio.ensure_future(self._on_frame(cdp, ev)))
        # Render em N x, mas o frame nunca sai maior do que a tela consegue mostrar: em monitor 1x, um espelho
        # 2x é 4x mais pixels para decodificar sem ganho nenhum.
        scale = min(self._m.scale or 1, max(1, math.ceil(self.dpr)))
        params: dict = {"maxWidth": self.viewport["width"] * scale, "maxHeight": self.viewport["height"] * scale}
        if config.BROWSER_STREAM == "jpeg":
            params.update(format="jpeg", quality=CAST_JPEG_QUALITY)
            self._cast_mime = "image/jpeg"
        else:
            params["format"] = "png"
            self._cast_mime = "image/png"
        await cdp.send("Page.startScreencast", params)

    async def _stop_cast(self) -> None:
        cdp, self._cdp = self._cdp, None
        if cdp:
            try:
                await cdp.send("Page.stopScreencast")
                await cdp.detach()
            except Exception:
                pass

    async def _on_frame(self, cdp, ev: dict) -> None:
        try:  # ack antes de tudo, senão o Chrome para depois de 2-3 frames
            await cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:
            return
        await self._publish({"type": "frame", "mime": self._cast_mime, "data": ev["data"],
                             "url": self.active.url if self.open else ""})

    async def frames(self) -> AsyncIterator[dict]:
        """Eventos para um assinante SSE: estado atual, último frame e depois cada novidade."""
        self.subs += 1
        try:
            if self.subs == 1:
                await self._start_cast()
            yield await self.state_with_title()
            seen = self.version
            if self.latest:
                yield self.latest
            elif self.open and not self._m.native:  # sem frame ainda (página parada): uma foto para não ficar em branco
                try:
                    png = await self.active.screenshot(type="png")
                    yield {"type": "frame", "mime": "image/png", "data": base64.b64encode(png).decode(),
                           "url": self.active.url}
                except Exception:
                    pass
            while True:
                async with self._cond:
                    await self._cond.wait_for(lambda: self.version != seen)
                    seen, ev = self.version, self.last_event
                self.touch()
                yield ev
        finally:
            self.subs -= 1
            if self.subs == 0:
                await self._stop_cast()

    # ------------------------------------------------------------ interação do usuário

    async def navigate(self, url: str = "", action: str = "") -> None:
        page = await self.ensure()
        try:
            if action == "back":
                await page.go_back(wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            elif action == "forward":
                await page.go_forward(wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            elif action == "reload":
                await page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            else:
                await self._goto(page, url)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(_err(e)) from e

    async def input(self, b: dict) -> None:
        """Evento de mouse/teclado vindo do espelho, já em coordenadas de CSS do viewport."""
        page = await self.ensure()
        t, x, y = b["type"], float(b.get("x") or 0), float(b.get("y") or 0)
        try:
            if t == "click":
                await page.mouse.click(x, y, button=b.get("button") or "left")
            elif t == "dblclick":
                await page.mouse.dblclick(x, y)
            elif t == "move":
                await page.mouse.move(x, y)
            elif t == "wheel":
                await page.mouse.move(x, y)
                await page.mouse.wheel(float(b.get("delta_x") or 0), float(b.get("delta_y") or 0))
            elif t == "key":
                await page.keyboard.press(str(b.get("key") or ""))
            elif t == "text":
                await page.keyboard.type(str(b.get("text") or ""))
            else:
                raise ToolError(f"Tipo de input desconhecido: {t}")
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(_err(e)) from e

    async def set_viewport(self, width: int, height: int, dpr: float = 1.0) -> None:
        """Painel da UI redimensionou: as abas passam a ter exatamente esse tamanho (px de CSS)."""
        w = max(MIN_VIEWPORT[0], min(MAX_VIEWPORT[0], int(width)))
        h = max(MIN_VIEWPORT[1], min(MAX_VIEWPORT[1], int(height)))
        dpr = max(1.0, min(3.0, float(dpr or 1)))
        if (w, h, dpr) == (self.viewport["width"], self.viewport["height"], self.dpr):
            return
        self.viewport, self.dpr = {"width": w, "height": h}, dpr
        self.touch()
        if self.open and not self._m.native:  # nativo: a view já tem o tamanho do painel
            for p in list(self.pages):
                try:
                    await p.set_viewport_size(self.viewport)
                except Exception:
                    pass
            if self._cdp:  # screencast novo com o maxWidth/maxHeight do tamanho novo
                await self._stop_cast()
                await self._start_cast()
        await self._publish_state()

    async def _on_filechooser(self, fc) -> None:
        self.file_chooser = fc
        await self._publish_state()

    async def upload(self, paths: list[str]) -> None:
        """Responde ao seletor de arquivo que a página abriu (lista vazia = cancelar)."""
        fc, self.file_chooser = self.file_chooser, None
        if fc is None:
            raise ToolError("Nenhuma página está pedindo arquivo agora.")
        try:
            await fc.set_files(paths)
        except Exception as e:
            raise ToolError(_err(e)) from e
        await self._publish_state()


class Manager:
    """Um Chromium para todas as conversas; uma Session por conversa; varredura de ociosas."""

    def __init__(self):
        self._pw = self._browser = None
        self.scale: int | None = None  # escala com que o Chromium foi lançado
        self.sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._sweeper: asyncio.Task | None = None
        self.hosts: set[asyncio.Queue] = set()  # assinantes de /api/browser/host (o main.js do Electron)

    @property
    def native(self) -> bool:
        return bool(config.BROWSER_CDP)

    # ------------------------------------------------------------ canal com o Electron (modo nativo)

    def host_emit(self, ev: dict) -> None:
        for q in self.hosts:
            q.put_nowait(ev)

    async def host_events(self) -> AsyncIterator[dict]:
        """Eventos para o Electron: `create` (abrir view com o marcador) e `active` (qual view mostrar)."""
        q: asyncio.Queue = asyncio.Queue()
        self.hosts.add(q)
        try:
            yield {"type": "hello", "native": self.native}
            while True:
                yield await q.get()
        finally:
            self.hosts.discard(q)

    async def browser(self):
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError:  # imagem antiga sem playwright
                raise ToolError("Navegador indisponível: o Playwright não está instalado neste backend. "
                                "Reinstale o Forja, ou rode `pip install -r backend/requirements.txt` "
                                "no ambiente do backend se estiver em desenvolvimento.") from None
            if self._pw is None:
                self._pw = await async_playwright().start()
            if self.native:  # o Chromium é o do Electron; as views já estão na escala da tela
                self.scale = 1
                try:
                    self._browser = await self._pw.chromium.connect_over_cdp(config.BROWSER_CDP, timeout=10_000)
                except Exception as e:
                    raise ToolError(f"Não consegui ligar ao navegador do app ({config.BROWSER_CDP}): {_err(e)}") from e
            else:
                # Render em N x: o screencast sai com viewport*N pixels e a UI mostra no tamanho de CSS (nítido).
                self.scale = max(1, min(3, int(config.BROWSER_SCALE)))
                self._browser = await self._pw.chromium.launch(
                    headless=True, args=["--disable-dev-shm-usage", f"--force-device-scale-factor={self.scale}"])
            if self._sweeper is None:
                self._sweeper = asyncio.create_task(self._sweep())
            return self._browser

    def session(self, key: str) -> Session:
        key = str(key or SCRATCH)
        if key not in self.sessions:
            self.sessions[key] = Session(key, self)
        return self.sessions[key]

    async def close(self, key: str) -> None:
        s = self.sessions.pop(str(key), None)
        if s:
            await s.close()

    async def _sweep(self) -> None:
        """A cada minuto: fecha sessões ociosas sem ninguém assistindo; relança o Chromium se a escala mudou."""
        while True:
            await asyncio.sleep(60)
            try:
                idle = int(config.BROWSER_IDLE_MINUTES) * 60
                now = time.monotonic()
                for s in list(self.sessions.values()):
                    if idle > 0 and s.open and s.subs == 0 and now - s.last_used > idle:
                        await s.close()
                if (self._browser and not self.native and not any(s.open for s in self.sessions.values())
                        and self.scale != max(1, min(3, int(config.BROWSER_SCALE)))):
                    await self._browser.close()
                    self._browser = None
            except Exception:
                pass

    async def shutdown(self) -> None:
        if self._sweeper:
            self._sweeper.cancel()
            self._sweeper = None
        for key in list(self.sessions):
            await self.close(key)
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None


MANAGER = Manager()


def current() -> Session:
    """Sessão da conversa cujo agente está rodando (ou o rascunho)."""
    return MANAGER.session(CURRENT_KEY.get())


# ------------------------------------------------------------------ ferramentas do modelo

def check_url(url: str) -> str:
    """Só http(s) completo. file:/chrome:/javascript:/data: leriam o disco ou rodariam JS local."""
    url = (url or "").strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ToolError("Só URLs http(s) completas, ex.: http://localhost:5173/rota.")
    return url


def _locator(page, selector: str):
    m = REF_RE.match(selector.strip())
    return page.locator(f"aria-ref={m.group(1)}") if m else page.locator(selector.strip())


def _act_err(selector: str, e: Exception) -> str:
    if REF_RE.match(selector.strip()):
        return (f"Ref '{selector}' não encontrado: a página mudou desde o último browser_read. "
                "Chame browser_read de novo e use um ref atual.")
    return f"Seletor '{selector}' falhou: {_err(e)}. Use um ref do browser_read (ex.: e12) ou text=/role=/CSS."


async def _summary(page) -> str:
    try:
        title = await page.title()
    except Exception:
        title = ""
    return f"URL: {page.url}\nTítulo: {title}"


async def _settle(page) -> None:
    try:  # se o clique navegou, espera o DOM novo; se não, volta na hora
        await page.wait_for_load_state("domcontentloaded", timeout=3_000)
    except Exception:
        pass


async def navigate(_root: Path, args: dict) -> str:
    url = check_url(args["url"])
    s = current()
    page = await s.ensure()
    before = len(s.logs)
    await s._goto(page, url)
    errs = [l for l in list(s.logs)[before:] if l.split("] ", 1)[-1].startswith(ERROR_PREFIXES)]
    return f"{await _summary(page)}\nErros de console desde o load: {len(errs)}\nChame browser_read para ver a página."


async def read(_root: Path, args: dict) -> str:
    page = await current().ensure()
    selector = (args.get("selector") or "").strip()
    loc = _locator(page, selector) if selector else page.locator("body")
    try:
        snap = await loc.aria_snapshot(mode="ai", timeout=ACT_TIMEOUT)
    except Exception as e:
        raise ToolError(_act_err(selector, e) if selector else f"Falha ao ler a página: {_err(e)}") from e
    max_chars = max(1_000, min(int(args.get("max_chars") or 15_000), 100_000))
    more = (f"\n(truncado em {max_chars} de {len(snap)} caracteres; passe selector para focar numa região)"
            if len(snap) > max_chars else "")
    return f"{UNTRUSTED}{await _summary(page)}\n\n{snap[:max_chars]}{more}"


async def validate(_root: Path, args: dict) -> str:
    """Abre a página e devolve estrutura + erros de console numa resposta só.

    Existe por orçamento de contexto, não por conveniência: validar uma tela custava três chamadas
    (navigate, read, console), e cada rodada do modelo recarrega o histórico inteiro. Para a Maestro,
    que valida depois de cada tarefa, três viram uma.
    """
    s = current()
    antes = len(s.logs)
    if url := (args.get("url") or "").strip():
        await navigate(_root, {"url": url})
    page = await s.ensure()
    partes = [await _summary(page)]

    novos = list(s.logs)[antes:]
    erros = [l for l in novos if l.split("] ", 1)[-1].startswith(ERROR_PREFIXES)]
    cabeca = f"ERROS DE CONSOLE: {len(erros)}"
    partes.append(NL.join([cabeca, *erros[-20:]]) if erros else cabeca + " (nenhum)")

    estrutura = await read(_root, {"selector": args.get("selector") or "",
                                   "max_chars": args.get("max_chars") or 8_000})
    # `read` já carrega o aviso de conteúdo não confiável e o resumo; fica só o corpo.
    partes.append("ESTRUTURA DA PÁGINA" + NL + estrutura.split(NL + NL, 1)[-1])
    return UNTRUSTED + (NL + NL).join(partes)


async def click(_root: Path, args: dict) -> str:
    page = await current().ensure()
    selector = args["selector"]
    try:
        await _locator(page, selector).click(timeout=ACT_TIMEOUT)
    except Exception as e:
        raise ToolError(_act_err(selector, e)) from e
    await _settle(page)
    return await _summary(page)


async def type_text(_root: Path, args: dict) -> str:
    page = await current().ensure()
    selector = args["selector"]
    loc = _locator(page, selector)
    try:
        await loc.fill(str(args["text"]), timeout=ACT_TIMEOUT)
        if args.get("submit"):
            await loc.press("Enter", timeout=ACT_TIMEOUT)
    except Exception as e:
        raise ToolError(_act_err(selector, e)) from e
    await _settle(page)
    return await _summary(page)


async def upload(root: Path, args: dict) -> str:
    page = await current().ensure()
    selector = args["selector"]
    p = resolve_path(root, args["path"])
    if not p.is_file():
        raise ToolError(f"Arquivo não encontrado na pasta de trabalho: {args['path']}")
    try:
        await _locator(page, selector).set_input_files(str(p), timeout=ACT_TIMEOUT)
    except Exception as e:
        raise ToolError(_act_err(selector, e)) from e
    return f"Arquivo {p.name} anexado em {selector}.\n{await _summary(page)}"


async def console(_root: Path, args: dict) -> str:
    s = current()
    lines = list(s.logs)
    if args.get("clear"):
        s.logs.clear()
    if not lines:
        return "Console vazio: nenhuma mensagem, erro de página ou request falho desde a abertura da sessão."
    return UNTRUSTED + "\n".join(lines[-100:])


async def tabs(_root: Path, args: dict) -> str:
    s = current()
    action = (args.get("action") or "list").strip().lower()
    if action == "new":
        await s.new_tab(args.get("url") or "")
    elif action == "switch":
        await s.switch_tab(args.get("index"))
    elif action == "close":
        await s.close_tab(args.get("index"))
    elif action != "list":
        raise ToolError("action deve ser list, new, switch ou close.")
    lst = await s.tabs()
    if not lst:
        return "Nenhuma aba aberta. Use browser_navigate ou browser_tabs new."
    return "\n".join(f"{'*' if t['active'] else ' '} {t['index']}. {t['title'] or '(sem título)'} — {t['url']}"
                     for t in lst) + "\n(* = aba ativa; as outras ferramentas agem na aba ativa)"


async def evaluate(_root: Path, args: dict) -> str:
    page = await current().ensure()
    try:
        result = await page.evaluate(str(args["script"]))
    except Exception as e:
        raise ToolError(f"Erro no JS: {_err(e)}") from e
    text = json.dumps(result, ensure_ascii=False, default=str)
    return UNTRUSTED + (text[:20_000] + ("\n(truncado)" if len(text) > 20_000 else ""))


def eval_preview(_root: Path, args: dict) -> dict:
    s = current()
    return {"kind": "command", "path": s.active.url if s.open else "(navegador fechado)",
            "text": str(args.get("script", ""))}


@asynccontextmanager
async def _viewport_do_print(page, largura: int, altura: int):
    """Renderiza a página num viewport dado, só enquanto o print é tirado, e devolve a MEDIDA REAL.

    Quem troca é o `set_viewport_size` do Playwright, não um `Emulation.setDeviceMetricsOverride`
    solto: o próprio `screenshot()` mexe nessa emulação e restaura pelo viewport que ELE conhece,
    então o override cru era desfeito e a foto saía no tamanho do painel enquanto a resposta jurava
    1920x1080. No modo nativo a aba é uma view do Electron sem viewport de Playwright, e aí não há
    o que trocar — daí medir em vez de prometer.
    """
    anterior, sessao = page.viewport_size, None
    with contextlib.suppress(Exception):
        if anterior:
            # Espelho: o contexto tem viewport próprio. Tem que ser o `set_viewport_size`, porque o
            # `screenshot()` do Playwright mexe na emulação e a restaura pelo viewport que ELE
            # conhece — um override cru era desfeito antes da foto sair.
            await page.set_viewport_size({"width": largura, "height": altura})
        else:
            # Nativo: a aba é uma view do Electron, o contexto veio do CDP e não tem viewport, então
            # `viewport_size` é None e não haveria tamanho para repor depois. Aqui o override é cru e
            # a MESMA sessão o desfaz no fim — sem isso, tirar um print deixava o navegador do
            # usuário preso em 1920x1080.
            sessao = await page.context.new_cdp_session(page)
            await sessao.send("Emulation.setDeviceMetricsOverride",
                              {"width": largura, "height": altura, "deviceScaleFactor": 1, "mobile": False})
    # Deixa o layout assentar antes de medir e fotografar: a troca de viewport é assíncrona e uma
    # página longa leva um tempo para refluir.
    with contextlib.suppress(Exception):
        await page.wait_for_timeout(200)
    try:
        medido = await page.evaluate("[innerWidth, innerHeight]")
    except Exception:
        medido = [largura, altura]
    try:
        yield {"width": int(medido[0]), "height": int(medido[1])}
    finally:
        with contextlib.suppress(Exception):
            if sessao is not None:
                try:
                    await sessao.send("Emulation.clearDeviceMetricsOverride")
                finally:
                    await sessao.detach()
            elif anterior:
                await page.set_viewport_size(anterior)


async def scroll(_root: Path, args: dict) -> str:
    """Rola a aba ativa, ou traz um elemento para a tela.

    Existe porque sem ela o print era a única forma de ver o que está abaixo da dobra — e a saída
    que sobrava era `full_page`, que devolvia uma tira de 5000px e travava o modelo com visão por
    minutos. Com o scroll, cada print continua sendo uma tela de verdade e o modelo desce por ela.
    """
    s = current()
    page = await s.ensure()
    alvo = str(args.get("selector") or "").strip()
    try:
        if alvo:
            await _locator(page, alvo).scroll_into_view_if_needed(timeout=ACT_TIMEOUT)
        else:
            para = str(args.get("para") or "baixo").lower()
            if para in ("topo", "inicio", "início"):
                await page.evaluate("scrollTo({top: 0})")
            elif para in ("fim", "fundo"):
                await page.evaluate("scrollTo({top: document.documentElement.scrollHeight})")
            else:
                # Uma tela por vez, com uma faixa de sobreposição: sem ela o conteúdo que cai
                # exatamente na emenda fica sem aparecer em print nenhum. O passo é limitado pela
                # altura do PRINT, não pela da janela: num painel mais alto que 1080 a rolagem
                # passaria além do que a foto seguinte mostra, e abriria um buraco.
                sinal = -1 if para in ("cima", "acima") else 1
                px = int(args.get("px") or 0)
                tela = min(int(await page.evaluate("innerHeight")), ALTURA_MAX_PRINT, PRINT_VIEWPORT["height"])
                await page.evaluate("d => scrollBy({top: d})", px * sinal if px else sinal * max(200, tela - 120))
    except Exception as e:
        raise ToolError(_act_err(alvo, e) if alvo else f"Falha ao rolar: {_err(e)}") from e
    await page.wait_for_timeout(250)  # rolagem suave e conteúdo que carrega ao aparecer
    pos = await page.evaluate("[Math.round(scrollY), Math.round(document.documentElement.scrollHeight), innerHeight]")
    fim = pos[0] + pos[2] >= pos[1] - 2
    return (f"Rolou para {pos[0]}px de {pos[1]}px de página ({pos[2]}px de tela)."
            f"{' É o fim da página.' if fim else ''} Tire um browser_screenshot para ver esta parte.")


async def screenshot(_root: Path, args: dict) -> dict:
    """Uma tela, não a página inteira.

    O que custa num modelo com visão é PIXEL. A página inteira virava 1903x5327 (10 MP) e o encoder
    do llama.cpp passava mais de 100 segundos nela, com o turno parecendo travado — e ainda por cima
    mostrava tudo pequeno demais para julgar qualquer coisa. Agora o print é sempre uma tela; para
    ver o resto existe o browser_scroll, e para um detalhe existe o `selector`.
    """
    s = current()
    page = await s.ensure()
    largura = max(MIN_VIEWPORT[0], min(MAX_VIEWPORT[0], int(args.get("largura") or PRINT_VIEWPORT["width"])))
    altura = max(MIN_VIEWPORT[1], min(MAX_VIEWPORT[1], int(args.get("altura") or PRINT_VIEWPORT["height"])))
    altura = min(altura, ALTURA_MAX_PRINT)
    alvo = str(args.get("selector") or "").strip()

    try:
        async with _viewport_do_print(page, largura, altura) as real:
            # scale="css": 1 pixel por px de CSS, senão o render 2x dobra o tamanho da imagem (e os tokens).
            comum = {"type": "jpeg", "quality": JPEG_QUALITY, "scale": "css", "timeout": PRINT_TIMEOUT}
            if alvo:
                elemento = _locator(page, alvo)
                await elemento.scroll_into_view_if_needed(timeout=PRINT_TIMEOUT)
                jpg = await elemento.screenshot(**comum)
            else:
                jpg = await page.screenshot(**comum)
    except Exception as e:
        raise ToolError((_act_err(alvo, e) if alvo else f"Falha no screenshot: {_err(e)}")) from e
    try:
        att = uploads.save("browser.jpg", jpg, "image/jpeg")
    except ValueError as e:
        raise ToolError(str(e)) from e

    if alvo:
        return {"text": f"{await _summary(page)}\nScreenshot de '{alvo}' anexado.", "attachments": [att]}
    # A medida sai do que a página realmente tinha na hora da foto: prometer 1920 e entregar 380
    # é pior que entregar 380 e dizer.
    size = f"{real['width']}x{real['height']}"
    if (real["width"], real["height"]) != (largura, altura):
        size += " — o navegador não aceitou outra medida; é o tamanho do painel"
    pos = await page.evaluate("[Math.round(scrollY), Math.round(document.documentElement.scrollHeight)]")
    resto = ""
    if pos[1] > real["height"] + 8:
        resto = (f"; esta é a tela em {pos[0]}px de uma página de {pos[1]}px — browser_scroll desce "
                 "para a próxima, e o conteúdo todo sai melhor no browser_read")
    return {"text": f"{await _summary(page)}\nScreenshot anexado ({size}{resto}).", "attachments": [att]}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


SELECTOR = {"type": "string", "description": "Ref do browser_read (ex.: e12) ou seletor Playwright: "
                                             "text=Salvar, role=button[name='Salvar'], CSS #id, input[name=email]"}

register(Tool(
    "browser_navigate",
    "Abre uma URL na aba ativa do navegador integrado (o usuário vê ao vivo). Um app subido por "
    "run_command ou serve_start fica em http://localhost:PORTA — o mesmo endereço para você e para "
    "o usuário, porque tudo roda na mesma máquina.",
    _obj({"url": {"type": "string", "description": "URL http(s) completa"}}, ["url"]), navigate))
register(Tool(
    "browser_read",
    "Lê a aba ativa como árvore de acessibilidade (papel, nome e ref eN de cada elemento). "
    "Use os refs em browser_click/browser_type.",
    _obj({"max_chars": {"type": "integer", "description": "Limite (padrão 15000)"},
          "selector": {"type": "string", "description": "Opcional: ref ou seletor para ler só uma região"}}, []),
    read))
register(Tool(
    "browser_validate",
    "Valida uma página numa chamada só: abre a URL (se você passar uma), lista os erros de console e "
    "devolve a estrutura da página. Use isto para conferir uma tela depois de uma tarefa, em vez de "
    "browser_navigate + browser_console + browser_read — é o mesmo resultado numa rodada.",
    _obj({"url": {"type": "string", "description": "URL a abrir; vazio usa a aba atual"},
          "selector": {"type": "string", "description": "Opcional: ler só uma região"},
          "max_chars": {"type": "integer", "description": "Limite da estrutura (padrão 8000)"}}, []),
    validate))
register(Tool(
    "browser_click", "Clica num elemento da aba ativa.",
    _obj({"selector": SELECTOR}, ["selector"]), click, mutating=True))
register(Tool(
    "browser_type", "Preenche um campo (substitui o conteúdo) e opcionalmente aperta Enter.",
    _obj({"selector": SELECTOR, "text": {"type": "string"},
          "submit": {"type": "boolean", "description": "Apertar Enter depois. Padrão: false"}},
         ["selector", "text"]),
    type_text, mutating=True))
register(Tool(
    "browser_upload", "Anexa um arquivo da pasta de trabalho a um input[type=file] da aba ativa.",
    _obj({"selector": SELECTOR, "path": {"type": "string", "description": "Arquivo relativo à pasta de trabalho"}},
         ["selector", "path"]),
    upload, mutating=True))
register(Tool(
    "browser_tabs",
    "Lista, abre, troca ou fecha abas do navegador. As outras ferramentas agem sempre na aba ativa.",
    _obj({"action": {"type": "string", "description": "list (padrão) | new | switch | close"},
          "index": {"type": "integer", "description": "Número da aba (para switch/close)"},
          "url": {"type": "string", "description": "URL inicial (para new, opcional)"}}, []),
    tabs))
register(Tool(
    "browser_console", "Mensagens de console, erros de JS e requests falhos da sessão (todas as abas).",
    _obj({"clear": {"type": "boolean", "description": "Limpar depois de ler. Padrão: false"}}, []), console))
register(Tool(
    "browser_eval",
    "Executa JavaScript na aba ativa e devolve o resultado em JSON. Expressão (document.title) ou "
    "função (() => {...}). Use para checar estado que o browser_read não mostra.",
    _obj({"script": {"type": "string"}}, ["script"]),
    evaluate, mutating=True, preview=eval_preview, always_ask=True))
register(Tool(
    "browser_scroll",
    "Rola a aba ativa. É assim que se vê o que está abaixo da dobra: rola e tira outro "
    "browser_screenshot, uma tela por vez. Sem argumentos desce uma tela (com sobreposição, para "
    "nada ficar na emenda); `para` aceita cima, baixo, topo e fim; `selector` traz um elemento para "
    "a tela. Para LER o conteúdo de baixo, porém, o browser_read é melhor e mais barato que descer "
    "tirando print.",
    _obj({"para": {"type": "string", "description": "baixo (padrão) | cima | topo | fim"},
          "px": {"type": "integer", "description": "Quantos pixels rolar, em vez de uma tela"},
          "selector": {"type": "string", "description": "Opcional: ref eN ou seletor a trazer para a tela"}}, []),
    scroll))
register(Tool(
    "browser_screenshot",
    "Tira um screenshot de UMA TELA da aba ativa (1280x720 por padrão, seja qual for o tamanho do "
    "painel). Ele aparece no chat para o usuário; se você tiver visão, também chega a você como "
    "imagem. Não existe print de página inteira: ele sairia com milhares de pixels de altura, "
    "demoraria muito para você enxergar e mostraria tudo pequeno demais para julgar. Para ver o "
    "resto da página, browser_scroll e outro print; para um detalhe (um card, um botão torto), "
    "passe `selector` e o print sai só dele; para conferir responsividade, peça outra medida "
    "(ex.: largura 390, altura 844 de celular).",
    _obj({"selector": {"type": "string", "description": "Opcional: ref eN ou seletor — fotografa só esse elemento"},
          "largura": {"type": "integer", "description": "Largura do viewport em px de CSS. Padrão: 1280. Acima de 1600 o modelo local fica MUITO mais lento para olhar a imagem"},
          "altura": {"type": "integer", "description": "Altura do viewport em px de CSS. Padrão: 720"}}, []),
    screenshot))

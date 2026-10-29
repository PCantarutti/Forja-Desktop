"""Ouvido e boca do assistente F.O.R.J.A: microfone sempre aberto, detecção da palavra "Forja", Whisper e a voz rápida
(Kokoro, na CPU). Roda no venv do assistente (RUNTIMES/assistente/venv), chamado por assistente.py.

    python assistente_ouvido.py --modelos <pasta do Kokoro> [--mic "fifine MME"] [--whisper small] [--voz pf_dora]
    python assistente_ouvido.py --listar        # microfones, um JSON por linha, e sai

Saída no stdout, por linhas: "PRONTO <microfone>" ao subir (ou "ERRO <mensagem>" e sai); "ESTADO ouvindo|gravando|
transcrevendo|falando"; "OUVIU <json>" com {texto, comando, wake, resposta} quando a fala começa com "Forja" (ou quando
o backend pediu a próxima fala com "escuta"); "FALOU" quando a fila de fala esvazia; "LOG <texto>" para depuração.
Pedidos no stdin, um JSON por linha: {"cmd": "fala", "texto"} (Kokoro), {"cmd": "toca", "arquivo"} (WAV pronto, ex.: voz
clonada da tela Voz), {"cmd": "escuta"} (a próxima fala vale sem "Forja": resposta a uma pergunta), {"cmd": "cala"}.

Enquanto fala, o microfone é ignorado: sem cancelamento de eco, ele ouviria a própria voz.
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import sys
import threading
import time
import unicodedata
import wave
from collections import deque
from pathlib import Path

SR = 16000
BLOCO = 480  # 30 ms
PRE_ROLL = 10  # blocos guardados antes de a fala começar (300 ms): o "F" de Forja não se perde
INICIO = 3  # blocos acima do limiar para abrir um trecho (90 ms)
FIM_SILENCIO = 27  # blocos de silêncio que fecham o trecho (~800 ms)
FIM_SILENCIO_COMANDO = 55  # depois de "Forja" a pessoa pensa no pedido: ~1,65 s (com 1,2 s o pedido saía cortado)
CHECA_WAKE = 33  # blocos (1 s) até a checagem rápida de "Forja" no começo do trecho: o círculo acende antes
# frases que o Whisper inventa em silêncio, ruído ou música (legendas de vídeos do treino dele)
ALUCINACAO = re.compile(r"amara\.org|legenda|inscreva-se|obrigad[oa] por assistir|ate a proxima|tchau,? tchau|"
                        r"^\W*(obrigad[oa]|e ai|hum+|ah+)\W*$")
MAX_TRECHO = SR * 15
ESCUTA_S = 10  # depois de "escuta", quanto tempo espera a resposta
INTERROMPE_MARGEM = 0.05  # "Forja" por cima da fala dele: um pouco mais de certeza (eco sozinho fica em ~0,04)
# ponytail: palavra de ativação por transcrição (Whisper em todo trecho de fala). Gasta CPU com TV ligada; modelo de
# wake word treinado para "Forja" (openWakeWord) quando isso pesar.
WAKE = re.compile(r"^\s*(?:(?:ei|oi|ok|ola|e ai|hey)\s+)?(?:forja|forjas|forje|forge|forjar|porja|forca|forj[ao])\b"
                  r"[\s,.:;!?-]*(.*)$", re.S)
KOKORO = {"modelo": "kokoro-v1.0.int8.onnx", "vozes": "voices-v1.0.bin"}
# idioma da conversa ("vamos conversar em inglês"): o Whisper detecta sozinho fora do português (o "Forja" dito no
# meio do inglês sai certo, o que language="en" às vezes vira "Forge"), e o pedido de voz troca a do Kokoro
IDIOMA = {"stt": "pt"}
# ajustes do microfone que mudam com ele ligado ({"cmd": "ajusta"}): ganho do sinal, sensibilidade da voz (0..1, 0,5 =
# o limiar de sempre), pausa que fecha um pedido (blocos) e até quando mandar o NIVEL para o medidor da página
AJUSTE = {"ganho": 1.0, "voz": 0.5, "fim": FIM_SILENCIO_COMANDO, "medidor": 0.0}


def limiar_voz(piso: float) -> float:
    """O limiar de energia da fala: 3,5x o ruído da sala (mínimo 0,008) na sensibilidade 0,5; cada 0,5 a mais divide
    por 2 (pega voz baixa/longe), cada 0,5 a menos multiplica por 2 (ignora TV e conversa ao fundo)."""
    fator = 2 ** ((0.5 - AJUSTE["voz"]) * 2)
    return max(0.008 * fator, piso * 3.5 * fator)


_saida = threading.Lock()


def diz(tipo: str, texto: str = "") -> None:
    # uma escrita só, sob trava: o print de duas threads (fala e microfone) grudava as linhas ("FALOUESTADO escutando")
    # e o backend perdia o ESTADO, deixando o círculo no estado errado
    with _saida:
        sys.stdout.write(f"{tipo} {texto}".strip() + "\n")
        sys.stdout.flush()


def normaliza(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def sem_quase_nome(texto: str) -> str:
    """O nome já foi ouvido (detector ou checagem rápida) mas a transcrição da frase inteira saiu sem ele, ou com ele
    torto ("Força, que horas são", "Porja, ..."): tira a primeira palavra se ela lembra "Forja" e devolve o pedido."""
    import difflib
    palavras = texto.strip().split(maxsplit=1)
    if palavras and difflib.SequenceMatcher(None, normaliza(palavras[0]).strip(",.!?:;"), "forja").ratio() >= 0.6:
        return (palavras[1] if len(palavras) > 1 else "").lstrip(" ,.:;!?-").strip()
    return texto.strip()


def acorda(texto: str) -> str | None:
    """O comando depois de "Forja" ("" se só chamou), ou None se a fala não começa pelo nome. Olha o texto sem acento
    mas devolve o pedaço original (com acento), cortado na mesma posição."""
    m = WAKE.match(normaliza(texto))
    if not m:
        return None
    return texto[m.start(1):].lstrip(" ,.:;!?-").strip() if m.group(1) else ""


def frases(texto: str) -> list[str]:
    texto = re.sub(r"[*_#`>|\[\]]", "", texto)
    return [f.strip() for f in re.split(r"(?<=[.!?…;:])\s+|\n+", texto) if f.strip()]


def grunhido(texto: str, ingles: bool) -> str:
    """O espeak soletra "Hm", "Mm" e "Hmph" (agá-eme): troca pela grafia que ele lê como grunhido, "Hmm" no inglês
    ("Hmmm" sai com eco: o "həm" tônico quase não aparece no treino) e "Hum" no português."""
    return re.sub(r"\b(?:h+m+|m{2,}|hmph)\b", "Hmm" if ingles else "Hum", texto, flags=re.I)


def com_estilo(texto: str, grito: bool) -> tuple[list[tuple[bool, str]], bool]:
    """As frases com o estilo de cada uma (True = gritado), pelas marcas [grito]...[/grito] do humor. O estado segue de
    um pedido para o outro: a resposta chega frase a frase e o grito pode atravessar a divisão."""
    saida = []
    for i, p in enumerate(re.split(r"\[(/?)grito\]", texto, flags=re.I)):
        if i % 2:
            grito = not p
        else:
            saida += [(grito, f) for f in frases(p)]
    return saida, grito


def agudos(y, sr: int, db: float):
    """Prateleira de agudos: sobe de 1,5 kHz até 4 kHz e fica, no mesmo pico. A voz do Wheatley sai abafada como os
    áudios do jogo em que foi treinada (mais épocas não mudam isso)."""
    import numpy as np
    fr = np.fft.rfftfreq(len(y), 1 / sr)
    z = np.fft.irfft(np.fft.rfft(y) * 10 ** (db / 20 * np.clip((fr - 1500) / 2500, 0, 1)), len(y))
    return (z * np.abs(y).max() / max(np.abs(z).max(), 1e-9)).astype(np.float32)


class Boca:
    """Duas threads: uma sintetiza a frase seguinte enquanto a outra toca a atual."""
    eco: int | None = None  # trocador de voz: toca também neste aparelho (o retorno no seu fone)
    ao_tocar = None  # trocador de voz: chamado com o texto quando a frase começa a tocar (mede o atraso)

    def __init__(self, modelos: Path, voz: str, efeito: str = "", eco: int | None = None):
        from kokoro_onnx import Kokoro
        self.kokoro = Kokoro(str(modelos / KOKORO["modelo"]), str(modelos / KOKORO["vozes"]))
        self.voz, self.piper, self.efeito = voz, None, None
        self.eco = eco
        self.grito = False
        self.ajuste: dict = {}  # "forja" no .onnx.json: {"pausa_s", "agudos_db": {"calmo", "gritado"}}
        if voz.endswith(".onnx"):  # voz treinada no Piper: o português dela; o Kokoro fica para os outros idiomas
            try:
                from piper import PiperVoice
                self.piper = PiperVoice.load(voz)
                self.ajuste = json.loads(Path(voz + ".json").read_text("utf-8")).get("forja", {})
                self.voz = "pf_dora"
            except Exception as e:  # sem o piper-tts no venv (instalação antiga) ou modelo quebrado: fala, mas na padrão
                diz("LOG", f"voz treinada indisponível, usando a padrão: {e.__class__.__name__}: {e}")
                self.voz = "pf_dora"
        if efeito == "glados" and self.piper:
            try:
                import assistente_glados  # ao lado deste arquivo (o ouvido roda como script)
                import pyworld  # noqa: F401  (falha aqui, e não na primeira frase)
                self.efeito = assistente_glados.aplica
                # aquece aqui (thread principal): os imports do filtro (scipy, pedalboard) feitos pela thread de
                # síntese com o Whisper rodando na principal travavam no carregamento das DLLs (trocador mudo)
                import numpy as np
                self.efeito(np.concatenate([c.audio_float_array for c in self.piper.synthesize("Ok.")]),
                            self.piper.config.sample_rate)
            except Exception as e:  # sem pyworld no venv: fala com a voz treinada, sem o filtro
                diz("LOG", f"filtro GLaDOS indisponível, falando sem ele: {e.__class__.__name__}: {e}")
        self.textos: queue.Queue = queue.Queue()
        self.audios: queue.Queue = queue.Queue()
        self.falando = threading.Event()
        self.fim_fala = 0.0
        self.geracao = 0  # "cala" sobe o número: o que estava na fila de antes é descartado
        self.atual = ""   # a frase tocando agora (a interrupção por "Forja" ignora a frase que diz o nome)
        threading.Thread(target=self._sintetiza, daemon=True).start()
        threading.Thread(target=self._toca, daemon=True).start()

    def fala(self, texto: str, voz: str | None = None, lang: str | None = None) -> None:
        pedacos, self.grito = com_estilo(texto, self.grito)
        for grito, f in pedacos:
            self.falando.set()
            self.textos.put((self.geracao, f, voz, lang, grito))

    def bip(self) -> None:
        """Dois tons curtos (~0,2 s): "estou ouvindo". Curto de propósito e marcado para quase não fechar o microfone:
        a pessoa fala logo em seguida, e o "Pois não?" falado comia o começo do pedido."""
        import numpy as np
        sr = 24000
        partes = []
        for freq in (660, 880):
            t = np.arange(int(sr * 0.085)) / sr
            partes.append(np.sin(2 * np.pi * freq * t) * np.minimum(1, np.minimum(t, t[::-1]) / 0.012) * 0.22)
        self.falando.set()
        self.audios.put((self.geracao, np.concatenate(partes).astype(np.float32), sr, True, ""))

    def toca(self, arquivo: str) -> None:
        import numpy as np
        with wave.open(arquivo) as w:
            sr, n, larg = w.getframerate(), w.getnchannels(), w.getsampwidth()
            bruto = w.readframes(w.getnframes())
        tipo = {2: np.int16, 4: np.int32}.get(larg)
        if not tipo:
            diz("LOG", f"WAV com {larg * 8} bits não suportado: {arquivo}")
            return
        a = np.frombuffer(bruto, tipo).astype(np.float32) / np.iinfo(tipo).max
        self.falando.set()
        self.audios.put((self.geracao, a.reshape(-1, n), sr, False, ""))

    def diz_o_nome(self) -> bool:
        return "forja" in re.sub(r"[^a-z]", "", normaliza(self.atual))

    def cala(self) -> None:
        # só marca: quem para é o _toca, no próximo bloco. sd.stop() daqui (thread dos pedidos) com o _toca no meio do
        # sd.play derrubava o processo com access violation dentro do PortAudio (visto num "para" no meio da resposta)
        self.geracao += 1
        self.grito = False

    def _sintetiza(self) -> None:
        while True:
            g, t, voz, lang, grito = self.textos.get()
            try:
                if g == self.geracao:
                    if self.piper and not voz and not lang:
                        import numpy as np
                        from piper import SynthesisConfig
                        estilo = "gritado" if grito else "calmo"
                        # voz com estilos (Wheatley: calmo 0, gritado 1); nas de um locutor só, o grito é só texto
                        sid = self.piper.config.speaker_id_map.get(estilo) if self.piper.config.num_speakers > 1 else None
                        t = grunhido(t, self.piper.config.espeak_voice.startswith("en"))
                        amostras = np.concatenate([c.audio_float_array for c in
                                                   self.piper.synthesize(t, syn_config=SynthesisConfig(speaker_id=sid))])
                        sr = self.piper.config.sample_rate
                        if db := self.ajuste.get("agudos_db", {}).get(estilo):
                            amostras = agudos(amostras, sr, db)
                        if self.efeito:  # o filtro GLaDOS: ~0,15 s por segundo de fala, enquanto a frase anterior toca
                            amostras = self.efeito(amostras, sr)
                        if pausa := self.ajuste.get("pausa_s"):  # o Piper cola uma frase na outra
                            amostras = np.concatenate([amostras, np.zeros(int(pausa * sr), np.float32)])
                    else:
                        amostras, sr = self.kokoro.create(t, voice=voz or self.voz, speed=1.1 if not lang else 1.0,
                                                          lang=lang or "pt-br")
                    self.audios.put((g, amostras, sr, False, t))
            except Exception as e:  # frase que o fonemizador não engole não derruba a boca
                diz("LOG", f"síntese: {e}")
            finally:
                self.textos.task_done()

    def _toca(self) -> None:
        import contextlib
        import numpy as np
        import sounddevice as sd
        while True:
            g, amostras, sr, curto, texto = self.audios.get()
            if g == self.geracao:
                if not curto:
                    diz("ESTADO", "falando")
                self.atual = texto
                if self.ao_tocar and texto:
                    self.ao_tocar(texto)
                bloco = sr // 10  # 0,1 s: o tanto que o "cala" demora para valer
                canais = 1 if amostras.ndim == 1 else amostras.shape[1]
                with sd.OutputStream(samplerate=sr, channels=canais, dtype="float32") as saida,                         (sd.OutputStream(device=self.eco, samplerate=sr, channels=canais, dtype="float32")
                         if self.eco is not None else contextlib.nullcontext()) as eco:
                    for i in range(0, len(amostras), bloco):
                        if g != self.geracao:
                            break
                        pedaco = np.ascontiguousarray(amostras[i:i + bloco], dtype=np.float32)
                        saida.write(pedaco)
                        if eco:
                            eco.write(pedaco)
                self.atual = ""
            self.audios.task_done()
            if self.textos.unfinished_tasks == 0 and self.audios.unfinished_tasks == 0:
                # depois do bip o microfone reabre quase na hora (0,05 s); depois de fala, 0,4 s para o eco morrer
                self.fim_fala = time.time() - (0.35 if curto else 0)
                self.falando.clear()
                if not curto:
                    self.grito = False  # o modelo esqueceu o [/grito]: a próxima resposta não começa gritando
                    diz("FALOU")
                    diz("ESTADO", "ouvindo")


class Detector:
    """Detector treinado da palavra "Forja" (assistente_treino.py): características do openWakeWord a cada 80 ms e uma
    MLP pequena em numpy. Muito mais leve que transcrever toda fala com o Whisper; ele só confirma depois."""

    def __init__(self, npz: Path, oww: Path, sensibilidade: float):
        import numpy as np
        from openwakeword.utils import AudioFeatures
        d = np.load(npz)
        self.W1, self.b1, self.W2, self.b2 = d["W1"], d["b1"], d["W2"], d["b2"]
        self.media, self.desvio, self.quadros = d["media"], d["desvio"], int(d["quadros"])
        self.treino = float(d["limiar"])
        self.sensibiliza(sensibilidade)
        self.af = AudioFeatures(melspec_model_path=str(oww / "melspectrogram.onnx"),
                                embedding_model_path=str(oww / "embedding_model.onnx"), inference_framework="onnx")
        self.buf = np.zeros(0, np.int16)
        self.anterior, self.ultimo = 0.0, 0.0

    def sensibiliza(self, sensibilidade: float) -> None:
        # sensibilidade 0..1 (0,5 = o limiar do treino): mais alta pega voz baixa/longe e erra mais
        self.limiar = float(min(0.97, max(0.2, self.treino - (sensibilidade - 0.5) * 0.4)))

    def prob(self, x):
        import numpy as np
        h = np.maximum(0, ((x - self.media) / self.desvio) @ self.W1 + self.b1)
        return float(1 / (1 + np.exp(-(h @ self.W2 + self.b2)[0])))

    def alimenta(self, bloco, margem: float = 0.0) -> bool:
        """Um bloco do microfone (float32). True quando "Forja" acabou de ser dito: a média de 2 janelas seguidas
        (160 ms) passa do limiar, no máximo uma vez a cada 2 s. Média e não "as duas acima": a probabilidade oscila em
        volta do limiar na palavra, e uma janela de queda não pode perder o nome."""
        import numpy as np
        self.buf = np.concatenate([self.buf, (np.clip(bloco, -1, 1) * 32767).astype(np.int16)])
        disparou = False
        while len(self.buf) >= 1280:
            self.af(self.buf[:1280])
            self.buf = self.buf[1280:]
            p = self.prob(self.af.get_features(self.quadros).reshape(-1))
            if (p + self.anterior) / 2 >= min(0.99, self.limiar + margem) and time.time() - self.ultimo > 2:
                self.ultimo, disparou = time.time(), True
            self.anterior = p
        return disparou


class Escuta:
    """Janela em que a próxima fala vale sem "Forja": resposta a uma pergunta do assistente ou o atalho global.
    Conta do fim da fala da pergunta (se ele estiver falando), não de quando o pedido chegou."""

    def __init__(self, boca: "Boca"):
        self.boca = boca
        self.desde = 0.0
        self.anunciada = False
        self.suave = False

    def abre(self, suave: bool = False) -> None:
        """`suave`: a conversa contínua depois de uma resposta. Fala nela vale sem "Forja"; barulho ou nada some em
        silêncio (a janela de uma pergunta dele, ao contrário, pede para repetir)."""
        self.desde, self.anunciada, self.suave = time.time(), False, suave

    def fecha(self) -> None:
        self.desde, self.anunciada = 0.0, False

    def ativa(self) -> bool:
        return bool(self.desde) and time.time() - max(self.desde, self.boca.fim_fala) < ESCUTA_S

    def vigia(self) -> None:
        """A cada bloco do microfone: anuncia "escutando" quando a fala da pergunta acaba e expira em silêncio."""
        if not self.desde or self.boca.falando.is_set():
            return
        if not self.ativa():
            self.fecha()
            diz("ESTADO", "ouvindo")
        elif not self.anunciada:
            self.anunciada = True
            diz("ESTADO", "escutando")


def trechos(mic: str | None, boca: Boca, escuta: Escuta, verifica, detector: Detector | None = None,
            so_detector: bool = True):
    """Trechos de fala do microfone, cortados por energia com limiar que acompanha o ruído da sala. Devolve
    (áudio, acordado): acordado = a fala já é um comando (janela de escuta aberta, ou "Forja" ouvido no começo dela
    pela checagem rápida de `verifica`, que acende o "escutando" antes de a pessoa terminar de falar)."""
    import numpy as np
    import sounddevice as sd
    blocos: queue.Queue = queue.Queue()
    with sd.InputStream(device=mic, samplerate=SR, channels=1, dtype="float32", blocksize=BLOCO, latency="low",
                        callback=lambda d, *_: blocos.put(d[:, 0].copy())):
        piso, antes, atual, acima, silencio, acordado, checou = 0.003, [], None, 0, 0, False, False
        motivo = ""  # por que este trecho é pedido: "escuta" (janela aberta), "detector" ou "nome" (checagem rápida)
        detectou = 0.0  # quando o detector acendeu (pode ser um instante antes de o trecho abrir)
        por_cima, checou_por_cima = deque(maxlen=40), 0.0  # fala da pessoa por cima da resposta (1,2 s) e a última checagem
        nivel_t, pico, atrasou = 0.0, 0.0, 0.0
        while True:
            b = blocos.get()
            if AJUSTE["ganho"] != 1.0:
                b = np.clip(b * AJUSTE["ganho"], -1, 1)
            if blocos.qsize() > 10 and time.time() - atrasou > 10:  # > 300 ms de áudio esperando: algo trava a escuta
                atrasou = time.time()
                diz("LOG", f"microfone atrasado {blocos.qsize() * BLOCO * 1000 // SR} ms")
            if AJUSTE["medidor"] > time.time():  # a página F.O.R.J.A aberta: nível e limiar para o medidor, ~10 por s
                pico = max(pico, float(np.sqrt(np.mean(b * b))))
                if time.time() - nivel_t > 0.1:
                    nivel_t = time.time()
                    diz("NIVEL", json.dumps({"rms": round(pico, 4), "limiar": round(limiar_voz(piso), 4),
                                             "prob": round(detector.anterior, 3) if detector else None,
                                             "det_limiar": round(detector.limiar, 3) if detector else None}))
                    pico = 0.0
            escuta.vigia()
            if boca.falando.is_set() or time.time() - boca.fim_fala < 0.4:
                atual, antes, acima = None, [], 0  # a própria voz (e o eco dela) não vira pedido...
                # ...mas "Forja" dito por cima da resposta interrompe, nunca na frase em que ele mesmo fala o nome.
                # Dois caminhos: o detector treinado (leve, em tempo real) e, com fala forte no microfone por cima da
                # dele, a checagem rápida do Whisper (1,2 s de áudio, no máximo a cada 1,5 s)
                if not boca.falando.is_set() or boca.diz_o_nome():
                    por_cima.clear()
                    continue
                interrompe = bool(detector and detector.alimenta(b, margem=INTERROMPE_MARGEM))
                por_cima.append(b)
                com_voz = [float(np.sqrt(np.mean(x * x))) > max(0.007, piso * 3) for x in por_cima]
                # confere quando a palavra termina (o "Forja" sozinho é curto) ou quando já há fala longa por cima
                pronto = sum(com_voz) >= 25 or (sum(com_voz) >= 4 and not any(com_voz[-6:]))
                if not interrompe and pronto and time.time() - checou_por_cima > 1.5:
                    checou_por_cima = time.time()
                    interrompe = verifica(np.concatenate(list(por_cima)))
                if interrompe:
                    por_cima.clear()
                    boca.cala()
                    diz("INTERROMPEU")
                    boca.bip()
                    escuta.abre()
                    diz("ESTADO", "escutando")
                continue
            if detector and detector.alimenta(b):
                detectou = time.time()
                if atual is not None and not acordado:
                    acordado, motivo = True, "detector"
                    diz("ESTADO", "escutando")
            rms = float(np.sqrt(np.mean(b * b)))
            limiar = limiar_voz(piso)
            if atual is None:
                piso = piso * 0.995 + rms * 0.005 if rms < limiar else piso  # o ruído de fundo, devagar
                antes = (antes + [b])[-PRE_ROLL:]
                acima = acima + 1 if rms > limiar else 0
                if acima >= INICIO:
                    atual, silencio, checou = list(antes), 0, False
                    motivo = "escuta" if escuta.ativa() else "detector" if time.time() - detectou < 1.5 else ""
                    acordado = bool(motivo)
                    diz("ESTADO", "escutando" if acordado else "gravando")
                continue
            atual.append(b)
            silencio = silencio + 1 if rms < limiar else 0
            if (not detector or not so_detector) and not acordado and not checou and len(atual) >= CHECA_WAKE:
                checou = True
                if verifica(np.concatenate(atual)):
                    acordado, motivo = True, "nome"
                    diz("ESTADO", "escutando")
            fim = AJUSTE["fim"] if acordado else FIM_SILENCIO  # no meio de um pedido, a pausa pode ser maior
            if silencio >= fim or len(atual) * BLOCO >= MAX_TRECHO:
                yield np.concatenate(atual), acordado, motivo
                atual, antes, acima, acordado, motivo = None, [], 0, False, ""

def audio_16k(arquivo: str):
    """WebM/opus da tela (ou WAV) em float32 mono 16 kHz pelo PyAV. O decode_audio do faster-whisper passa um
    `metadata_errors` que o PyAV novo não aceita mais (TypeError), então a decodificação é daqui."""
    import av
    import numpy as np
    partes = []
    with av.open(arquivo) as c:
        r = av.AudioResampler(format="s16", layout="mono", rate=SR)
        for quadro in c.decode(audio=0):
            partes += [f.to_ndarray().reshape(-1) for f in r.resample(quadro)]
        partes += [f.to_ndarray().reshape(-1) for f in r.resample(None)]
    return np.concatenate(partes).astype(np.float32) / 32768 if partes else np.zeros(0, np.float32)


def dita(stt, arquivo: str) -> str:
    """Ditado de um campo de texto: o áudio gravado pela tela (webm/opus, o PyAV do faster-whisper decodifica), com
    pontuação e sem o corte de alucinação de silêncio (o VAD tira o silêncio antes)."""
    segs, _ = stt.transcribe(audio_16k(arquivo), language="pt", beam_size=5, vad_filter=True, condition_on_previous_text=False)
    # o filtro vale por trecho: um ditado que cita "legenda" não some inteiro, só o "Legendas pela comunidade..." solto
    return " ".join(s.text.strip() for s in segs if not ALUCINACAO.search(normaliza(s.text))).strip()


def dispositivo(ident: str, tipo: str) -> int | None:
    """O índice do aparelho com este id ("<nome> <API>", o de --listar). O Windows trunca nomes e dois aparelhos
    podem ficar iguais (o sounddevice recusa nome ambíguo): vale o primeiro que casa. Sumiu: erro claro agora,
    não mudo na primeira fala."""
    import sounddevice as sd
    if not ident:
        return None
    for i, d in enumerate(sd.query_devices()):
        api = sd.query_hostapis(d["hostapi"])["name"]
        canais = d["max_input_channels"] if tipo == "entrada" else d["max_output_channels"]
        if canais > 0 and f"{d['name']} {api}" == ident:
            return i
    raise RuntimeError(f"{'Microfone' if tipo == 'entrada' else 'Alto-falante'} não encontrado: {ident}. "
                       "Foi desconectado? Escolha outro na página F.O.R.J.A.")


def listar() -> int:
    """Microfones e alto-falantes, um JSON por linha: {id, nome, api, tipo: entrada|saida, padrao}."""
    import sounddevice as sd
    padrao = {"entrada": sd.query_devices(kind="input")["name"], "saida": sd.query_devices(kind="output")["name"]}
    for d in sd.query_devices():
        api = sd.query_hostapis(d["hostapi"])["name"]
        for tipo, canais in (("entrada", d["max_input_channels"]), ("saida", d["max_output_channels"])):
            if canais > 0:
                print(json.dumps({"id": f"{d['name']} {api}", "nome": d["name"], "api": api, "tipo": tipo,
                                  "padrao": d["name"] == padrao[tipo] and api == "MME"}, ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--modelos", default="")
    p.add_argument("--mic", default="")
    p.add_argument("--whisper", default="small")
    p.add_argument("--voz", default="pf_dora")
    p.add_argument("--efeito", default="")  # "glados": o filtro de assistente_glados.py sobre a voz treinada
    p.add_argument("--stt", default="pt")  # "auto": o Whisper detecta o idioma (voz que só fala inglês)
    p.add_argument("--saida", default="")  # alto-falante (pedaço do nome + API); vazio = o padrão do Windows
    p.add_argument("--listar", action="store_true")
    p.add_argument("--detector", default="")  # .npz do detector treinado de "Forja" (vazio: Whisper em toda fala)
    p.add_argument("--oww", default="")       # pasta com melspectrogram.onnx e embedding_model.onnx
    p.add_argument("--sensibilidade", type=float, default=0.5)
    p.add_argument("--com-whisper", action="store_true")  # detector + Whisper: o detector acende rápido, o Whisper não deixa passar
    p.add_argument("--transcrever", default="")  # ditado com o assistente desligado: transcreve o arquivo e sai
    a = p.parse_args()
    IDIOMA["stt"] = "" if a.stt == "auto" else a.stt
    if a.listar:
        return listar()
    if a.transcrever:
        from faster_whisper import WhisperModel
        print(json.dumps({"texto": dita(WhisperModel(a.whisper, device="cpu", compute_type="int8"), a.transcrever)},
                         ensure_ascii=False), flush=True)
        return 0
    if sys.platform == "win32":
        # acima do normal: com o modelo, o build ou um jogo puxando a CPU, o microfone e o Whisper ficavam na fila do
        # Windows e o "escutando" demorava. Não "alta": o áudio é pouco, só não pode esperar.
        import ctypes
        # -1 é o pseudo-handle do próprio processo; como int puro o ctypes o mandava em 32 bits (handle inválido no x64)
        if not ctypes.windll.kernel32.SetPriorityClass(ctypes.c_void_p(-1), 0x8000):
            diz("LOG", "não deu para subir a prioridade do ouvido")
    try:
        import os
        import sounddevice as sd
        from faster_whisper import WhisperModel
        mic = dispositivo(a.mic, "entrada")
        nome = sd.query_devices(mic, kind="input")["name"]
        if a.saida:
            sd.default.device = (None, dispositivo(a.saida, "saida"))
        stt = WhisperModel(a.whisper, device="cpu", compute_type="int8", cpu_threads=max(4, (os.cpu_count() or 8) // 2))
        boca = Boca(Path(a.modelos), a.voz, a.efeito)
        detector = Detector(Path(a.detector), Path(a.oww), a.sensibilidade) if a.detector else None
    except Exception as e:
        diz("ERRO", f"{e.__class__.__name__}: {e}")
        return 1

    escuta = Escuta(boca)

    def verifica(inicio) -> bool:
        """Checagem rápida: o começo do trecho já tem "Forja"? (só para o "escutando" aparecer na hora)"""
        segs, _ = stt.transcribe(inicio, language="pt", beam_size=1, initial_prompt="Forja.", vad_filter=False,
                                 condition_on_previous_text=False, without_timestamps=True)
        return acorda(" ".join(s.text for s in segs)) is not None

    def transcreve(q: dict) -> None:
        try:
            diz("TRANSCRITO", json.dumps({"id": q["id"], "texto": dita(stt, q["arquivo"])}, ensure_ascii=False))
        except Exception as e:
            diz("TRANSCRITO", json.dumps({"id": q["id"], "erro": f"{e.__class__.__name__}: {e}"[:300]}, ensure_ascii=False))

    def pedidos() -> None:
        for linha in sys.stdin:
            try:
                q = json.loads(linha)
                if q["cmd"] == "fala":
                    boca.fala(q["texto"], q.get("voz"), q.get("lang"))
                elif q["cmd"] == "idioma":
                    IDIOMA["stt"] = q.get("stt") or ""
                elif q["cmd"] == "toca":
                    boca.toca(q["arquivo"])
                elif q["cmd"] == "escuta":
                    escuta.abre(suave=bool(q.get("suave")))
                    if q.get("agora"):  # atalho ou botão: bip e círculo na hora, sem esperar o próximo bloco
                        boca.bip()
                        escuta.anunciada = True
                        diz("ESTADO", "escutando")
                elif q["cmd"] == "ajusta":
                    if "ganho" in q:
                        AJUSTE["ganho"] = min(4.0, max(0.5, float(q["ganho"])))
                    if "voz" in q:
                        AJUSTE["voz"] = min(1.0, max(0.0, float(q["voz"])))
                    if "pausa" in q:
                        AJUSTE["fim"] = round(min(3.0, max(0.8, float(q["pausa"]))) * SR / BLOCO)
                    if "sensibilidade" in q and detector:
                        detector.sensibiliza(min(1.0, max(0.0, float(q["sensibilidade"]))))
                elif q["cmd"] == "medidor":
                    AJUSTE["medidor"] = time.time() + float(q.get("s") or 3)
                elif q["cmd"] == "cala":
                    boca.cala()
                elif q["cmd"] == "bip":
                    boca.bip()
                elif q["cmd"] == "transcreve":  # ditado num campo do Forja: o Whisper já está quente aqui
                    threading.Thread(target=transcreve, args=(q,), daemon=True).start()
                elif q["cmd"] == "surdo":  # gravando amostra para o treino: não reage ao "Forja" dela
                    boca.fim_fala = max(boca.fim_fala, time.time() + float(q.get("s") or 3))
            except Exception as e:
                diz("LOG", f"pedido inválido: {e}")
        boca.cala()
        sys.exit(0)  # stdin fechou: o backend saiu
    threading.Thread(target=pedidos, daemon=True).start()

    diz("PRONTO", nome)
    diz("ESTADO", "ouvindo")
    so_detector = bool(detector) and not a.com_whisper
    diz("LOG", "Forja: detector treinado" + (" + Whisper" if detector and not so_detector else "") if detector
        else "Forja pelo Whisper em toda fala")
    # o microfone numa thread só dele: com o Whisper transcrevendo no mesmo laço (segundos no medium), os blocos
    # esperavam na fila e o "escutando" do atalho, o detector e o fim da fala andavam com esse atraso
    # ponytail: um worker do Whisper; a checagem rápida (modo whisper/ambos) espera a transcrição em curso. num_workers=2
    # se isso pesar (dobra a memória do modelo)
    falas: queue.Queue = queue.Queue()

    def captura() -> None:
        try:
            for t in trechos(mic, boca, escuta, verifica, detector, so_detector):
                falas.put(t)
        except Exception as e:
            diz("ERRO", f"microfone: {e.__class__.__name__}: {e}")
        falas.put(None)
    threading.Thread(target=captura, daemon=True).start()
    for audio, acordado, motivo in iter(falas.get, None):
        if so_detector and not acordado:
            diz("ESTADO", "ouvindo")
            continue  # o detector não ouviu "Forja": nem gasta o Whisper (o motivo de ter o detector)
        diz("ESTADO", "entendendo" if acordado else "transcrevendo")
        t0 = time.perf_counter()
        segs, _ = stt.transcribe(audio, language=IDIOMA["stt"] or None, beam_size=1, initial_prompt="Forja.",
                                 vad_filter=False, condition_on_previous_text=False)
        texto = " ".join(s.text for s in segs).strip()
        diz("LOG", f"Whisper {time.perf_counter() - t0:.2f} s para {len(audio) / SR:.1f} s de fala")
        if ALUCINACAO.search(normaliza(texto)):
            diz("LOG", "ignorado: alucinação do Whisper")  # sem o texto: a fala da pessoa não vai para o log
            texto = ""
        # vale a janela de quando a pessoa COMEÇOU a falar: um pedido longo depois do bip passava dos 10 s até
        # terminar de transcrever, a janela fechava no meio e o pedido sumia sem resposta nenhuma
        resposta = motivo == "escuta" or escuta.ativa()
        comando = acorda(texto)
        if texto and comando is None and motivo in ("detector", "nome"):
            # o círculo já tinha acendido (o nome foi ouvido), mas a frase inteira saiu sem "Forja" ou com ele torto:
            # antes o pedido era descartado em silêncio; agora vale, sem a palavra parecida com o nome
            comando = sem_quase_nome(texto)
            diz("LOG", "o nome foi ouvido mas não saiu na transcrição: vale o pedido")
        if texto and comando == "":
            # só "Forja" (dentro ou fora da janela): bip e escuta aqui mesmo. Nunca vira pergunta ("Forja." já foi
            # respondido como se fosse o pedido), e sem ida e volta ao backend o bip sai na hora.
            boca.bip()
            escuta.abre()
            diz("LOG", "ouviu só o nome: escutando o pedido")
            diz("ESTADO", "escutando")
            continue
        if texto and (comando is not None or resposta):
            escuta.fecha()
            diz("OUVIU", json.dumps({"texto": texto, "comando": comando if comando is not None else texto,
                                     "wake": comando is not None, "resposta": resposta}, ensure_ascii=False))
        elif acordado and motivo == "escuta" and escuta.suave:
            escuta.fecha()  # conversa contínua: tosse, porta, TV baixa... nada de "não entendi" a cada barulho
        elif acordado:
            # ouviu "Forja" (ou estava esperando a resposta) e não entendeu nada: avisa e escuta de novo, em vez de
            # ficar mudo com o círculo apagando
            diz("LOG", "acordou mas não entendeu: pediu para repetir")
            boca.fala("Não entendi. Pode repetir?")
            escuta.abre()
            continue
        diz("ESTADO", "ouvindo")
    return 0


if __name__ == "__main__":
    import faulthandler
    faulthandler.enable()  # queda nativa (PortAudio, ONNX) deixa o traceback no log do backend em vez de sumir calada
    sys.exit(main())

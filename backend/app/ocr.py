"""Ler o texto de uma imagem quando não há texto a extrair.

PDF escaneado é imagem. Com um modelo que enxerga, `preview_document` já resolvia — ele olha a
página. Sem visão (é o caso dos modelos locais menores, que é o que a maioria roda aqui) o arquivo
era simplesmente ilegível, e o agente não tinha o que responder além de "não consigo".

Nada de motor embarcado: no Windows quem reconhece é o próprio sistema — `Windows.Media.Ocr`, o
mesmo da Ferramenta de Captura, com os idiomas que o usuário já tem instalados —, e no container é
o tesseract do apt. São ~3 MB de binding no primeiro caso e um pacote do sistema no segundo, contra
dezenas de MB de modelo de qualquer OCR em Python puro. Onde não houver nenhum dos dois,
`disponivel()` é False e quem chama volta a mandar o pedido para a visão do modelo.

Self-check: python -m app.ocr
"""
from __future__ import annotations

import asyncio
import functools
import io

NL = chr(10)


# ------------------------------------------------------------------ Windows.Media.Ocr

@functools.lru_cache(maxsize=1)
def _motor_windows():
    """O OcrEngine do sistema, ou None fora do Windows / sem idioma de OCR instalado.

    Em cache porque criar o motor relê a lista de idiomas do perfil, e uma página de PDF escaneado
    vira várias chamadas seguidas.
    """
    try:
        from winrt.windows.media.ocr import OcrEngine
    except Exception:  # não é Windows, ou os bindings não foram instalados
        return None
    try:
        return OcrEngine.try_create_from_user_profile_languages()
    except Exception:
        return None


async def _ler_windows(imagem: bytes, motor) -> str:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    escritor = DataWriter(stream.get_output_stream_at(0))
    escritor.write_bytes(imagem)
    await escritor.store_async()
    decodificador = await BitmapDecoder.create_async(stream)
    bitmap = await decodificador.get_software_bitmap_async()
    resultado = await motor.recognize_async(bitmap)
    return _em_ordem_de_leitura(resultado)


def _em_ordem_de_leitura(resultado) -> str:
    """As linhas reagrupadas de cima para baixo e da esquerda para a direita.

    Nem `resultado.text` nem a ordem de `resultado.lines` servem. O Windows devolve cada bloco de
    texto como uma "linha" e, numa tabela, cada célula é um bloco — varridos coluna a coluna. Numa
    tabela de manutenção de teste isso entregava ao modelo os quatro códigos juntos, depois as
    quatro descrições, depois os quatro valores: reconhecimento perfeito e tabela errada, que é o
    pior resultado possível, porque parece certo. Aqui as células voltam a se juntar pela altura em
    que estão na página.
    """
    caixas = []
    for linha in resultado.lines:
        if not linha.words:
            continue
        topo = min(p.bounding_rect.y for p in linha.words)
        altura = max(p.bounding_rect.height for p in linha.words)
        caixas.append((topo + altura / 2, min(p.bounding_rect.x for p in linha.words),
                       altura, linha.text))
    if not caixas:
        return ""
    caixas.sort()
    # Meia altura de linha: o suficiente para juntar células da mesma fileira, que nunca saem
    # alinhadas ao pixel num documento escaneado, sem colar a fileira de baixo.
    tolerancia = sorted(c[2] for c in caixas)[len(caixas) // 2] * 0.6
    fileiras: list[list[tuple]] = [[caixas[0]]]
    for caixa in caixas[1:]:
        if caixa[0] - fileiras[-1][0][0] <= tolerancia:
            fileiras[-1].append(caixa)
        else:
            fileiras.append([caixa])
    saida = []
    for fileira in fileiras:
        fileira.sort(key=lambda c: c[1])
        # O `|` só entra onde havia mesmo mais de uma célula na fileira; texto corrido sai limpo.
        saida.append(" | ".join(c[3] for c in fileira) if len(fileira) > 1 else fileira[0][3])
    return NL.join(saida)


# ------------------------------------------------------------------ tesseract (container)

@functools.lru_cache(maxsize=1)
def _tesseract():
    """`(pytesseract, idioma)`, ou None quando o binário do tesseract não está instalado.

    O idioma é resolvido aqui, e não na hora de ler, porque pedir `por` sem o pacote
    `tesseract-ocr-por` não falha na checagem: falha lá no reconhecimento. `disponivel()` diria que
    sim, o read_file entraria confiante e estouraria com um erro do pytesseract na cara do usuário.
    """
    try:
        import pytesseract

        # `get_languages` roda o binário, então serve de checagem de instalação também.
        instalados = set(pytesseract.get_languages(config=""))
    except Exception:  # sem o pacote Python, ou sem o binário do sistema
        return None
    # `por+eng` porque documento em português tem sigla, unidade e nome próprio em inglês no meio.
    # Se nenhum dos dois veio, serve qualquer idioma instalado — pior que o certo, melhor que nada.
    idiomas = [i for i in ("por", "eng") if i in instalados] or sorted(instalados - {"osd"})[:1]
    return (pytesseract, "+".join(idiomas)) if idiomas else None


def _ler_tesseract(imagem: bytes, pytesseract, idioma: str) -> str:
    from PIL import Image

    return pytesseract.image_to_string(Image.open(io.BytesIO(imagem)), lang=idioma)


# ------------------------------------------------------------------ o que o resto do app usa

def disponivel() -> bool:
    """Se dá para fazer OCR nesta máquina. Barato: só verifica, não reconhece nada."""
    return _motor_windows() is not None or _tesseract() is not None


def de_imagens(imagens: list[bytes]) -> list[str]:
    """Texto de cada imagem, na ordem. Fatia vazia onde a página não rendeu nada."""
    motor = _motor_windows()
    if motor is not None:
        return [asyncio.run(_ler_windows(img, motor)).strip() for img in imagens]
    tess = _tesseract()
    if tess is not None:
        return [_ler_tesseract(img, *tess).strip() for img in imagens]
    return []


if __name__ == "__main__":
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 150), "white")
    ImageDraw.Draw(img).text((20, 40), "Inspecao concluida 2026", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")

    if not disponivel():
        print("sem OCR nesta maquina (nem Windows.Media.Ocr, nem tesseract) — nada a checar")
    else:
        saida = de_imagens([buf.getvalue()])
        assert len(saida) == 1, saida
        assert "2026" in saida[0], saida  # o resto varia com o motor; o ano nao
        print("ocr ok:", saida[0])

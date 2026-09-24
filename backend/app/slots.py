"""O lado "projeto" dos slots de imagem (skill gerar-imagens): o que o código aponta, o arquivo que
fica no lugar até a imagem sair, a versão leve para a web, o tamanho que o modelo sabe gerar e quem
mais está usando a GPU.

Tudo aqui mexe só em arquivo; quem decide quando chamar é lotes.py / imagegen.py.
"""
from __future__ import annotations

import csv
import io
import math
import os
import re
import subprocess
import sys
from pathlib import Path

# ------------------------------------------------------------------ o que o código aponta

TEXTO = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".js", ".mjs", ".jsx", ".ts", ".tsx",
         ".vue", ".svelte", ".astro", ".php", ".md", ".mdx", ".json"}
IGNORAR = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", ".forja", "__pycache__", ".venv", "vendor"}
MAX_ARQUIVOS = 400        # ponytail: varredura linear; índice se projeto grande deixar a ferramenta lenta
MAX_BYTES = 1_000_000     # arquivo maior que isso é gerado/minificado: não é onde a IA escreveu
REF = re.compile(r"""(?<![\w/.-])((?:\.{0,2}/)?[\w./-]*?[\w-]+\.(?:png|jpe?g|webp|gif|avif))(?![\w-])""", re.I)


def textos(root: Path) -> dict[Path, str]:
    out: dict[Path, str] = {}
    for atual, pastas, arquivos in os.walk(root):
        pastas[:] = [p for p in pastas if p not in IGNORAR]
        for nome in arquivos:
            f = Path(atual) / nome
            if f.suffix.lower() not in TEXTO:
                continue
            try:
                if f.stat().st_size > MAX_BYTES:
                    continue
                out[f] = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(out) >= MAX_ARQUIVOS:
                return out
    return out


def _nomes(caminho: str) -> tuple[str, str]:
    """O nome do arquivo do slot e o da versão web (a referência pode estar em qualquer um)."""
    p = Path(caminho)
    return p.name, p.with_suffix(".webp").name


def _cita(texto: str, nome: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(nome)}(?![\w-])", texto) is not None


def referenciados(root: Path, caminhos: list[str], cache: dict[Path, str] | None = None) -> set[str]:
    """Quais slots aparecem em algum arquivo do projeto (pelo nome do PNG ou do WebP)."""
    arquivos = cache if cache is not None else textos(root)
    return {c for c in caminhos if any(_cita(t, n) for t in arquivos.values() for n in _nomes(c))}


def conferir(root: Path, slots: list[dict]) -> list[str]:
    """Problemas entre o que o código aponta e os slots: slot que ninguém usa e imagem citada que não
    existe nem é slot. Vai de volta para a IA na hora, para ela corrigir no mesmo turno."""
    root = root.resolve()
    arquivos = textos(root)
    avisos: list[str] = []
    usados = referenciados(root, [s["caminho"] for s in slots], arquivos)
    for s in slots:
        if s["caminho"] not in usados:
            avisos.append(f"o slot {s['nome']} ({s['rel']}) não aparece em nenhum arquivo do projeto")
    dos_slots = {Path(s["caminho"]).resolve() for s in slots}
    dos_slots |= {p.with_suffix(".webp") for p in dos_slots}
    for f, t in arquivos.items():
        for ref in dict.fromkeys(REF.findall(t)):
            if ref.startswith("//") or "://" in ref:
                continue
            base = root if ref.startswith("/") else f.parent
            alvo = (base / ref.lstrip("/")).resolve()
            if alvo in dos_slots or alvo.exists():
                continue
            avisos.append(f"{f.relative_to(root).as_posix()} usa {ref}, que não existe e não é slot")
    return list(dict.fromkeys(avisos))[:15]


# ------------------------------------------------------------------ placeholder

MARCA = "forja-placeholder"


def placeholder(caminho: str, largura: int | None, altura: int | None, nome: str) -> bool:
    """PNG neutro com o nome do slot, para o site não mostrar imagem quebrada até gerar. Nunca por cima
    de arquivo que já existe. Leva uma marca no PNG: a 1ª geração o substitui sem guardar em descartadas/."""
    from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

    alvo = Path(caminho)
    if alvo.exists():
        return False
    w, h = largura or 1024, altura or 1024
    img = Image.new("RGB", (w, h), (40, 38, 36))
    d = ImageDraw.Draw(img)
    tam = max(14, min(w, h) // 14)
    try:
        fonte = ImageFont.load_default(size=tam)
    except TypeError:  # Pillow antigo: fonte bitmap sem tamanho
        fonte = ImageFont.load_default()
    for i, (texto, cor) in enumerate(((nome, (200, 190, 175)), ("imagem ainda não gerada", (120, 114, 106)))):
        caixa = d.textbbox((0, 0), texto, font=fonte)
        x = (w - (caixa[2] - caixa[0])) / 2
        y = h / 2 - tam * (1.1 if i == 0 else -0.3)
        d.text((x, y), texto, fill=cor, font=fonte)
    info = PngImagePlugin.PngInfo()
    info.add_text("forja", MARCA)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    img.save(alvo, pnginfo=info)
    return True


def eh_placeholder(caminho: str | Path) -> bool:
    from PIL import Image

    try:
        with Image.open(caminho) as img:
            return img.info.get("forja") == MARCA
    except Exception:  # não existe, não é imagem, está sendo gravado
        return False


# ------------------------------------------------------------------ versão web

QUALIDADE_WEBP = 82


def webp(png: str | Path) -> tuple[int, int]:
    """Grava o .webp ao lado do PNG (o PNG fica: é a matriz que Regerar/Usar no site trocam).
    Devolve (bytes do png, bytes do webp)."""
    from PIL import Image

    png = Path(png)
    destino = png.with_suffix(".webp")
    with Image.open(png) as img:
        img.save(destino, "WEBP", quality=QUALIDADE_WEBP, method=6)
    return png.stat().st_size, destino.stat().st_size


def trocar_referencias(root: Path, nomes: list[str]) -> list[str]:
    """No código, `nome.png` vira `nome.webp` (só os slots, só o nome exato). Devolve os arquivos alterados."""
    alterados = []
    for f, t in textos(root.resolve()).items():
        novo = t
        for n in nomes:
            novo = re.sub(rf"(?<![\w-]){re.escape(n)}\.png(?![\w-])", f"{n}.webp", novo)
        if novo != t:
            f.write_text(novo, encoding="utf-8")
            alterados.append(f.relative_to(root.resolve()).as_posix())
    return alterados


# ------------------------------------------------------------------ tamanho que o modelo sabe gerar

def area_nativa(modelo: str, sugere: dict | None) -> int | None:
    """Pixels por imagem com que o modelo foi treinado. Fora disso ele repete objetos (grande) ou perde
    detalhe (pequeno). Sem saber, None: o tamanho do slot passa como veio."""
    if sugere and sugere.get("width") and sugere.get("height"):
        return int(sugere["width"]) * int(sugere["height"])
    nome = Path(modelo).stem.lower()
    # ponytail: pelo nome do arquivo; safetensors não traz a arquitetura legível sem abrir os pesos
    if re.search(r"xl|sdxl|pony|illustrious", nome):
        return 1024 * 1024
    if re.search(r"sd[-_ ]?1\.?5|v1-5|sd15", nome):
        return 512 * 512
    return None


def ajustar(largura: int | None, altura: int | None, area: int | None) -> tuple[int | None, int | None]:
    """Mesma proporção do slot, na área nativa do modelo, em múltiplos de 64."""
    if not (largura and altura and area):
        return largura, altura
    fator = math.sqrt(area / (largura * altura))
    return max(256, round(largura * fator / 64) * 64), max(256, round(altura * fator / 64) * 64)


# ------------------------------------------------------------------ quem mais está na GPU

def _processos(exe: str) -> list[int]:
    try:
        saida = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"],
                               # a saída do tasklist vem no código de página do console (OEM), não em UTF-8:
                               # o "1.000 K" da coluna de memória derrubava a thread de leitura do subprocess
                               capture_output=True, text=True, encoding="oem", errors="replace", timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(linha[1]) for linha in csv.reader(io.StringIO(saida))
            if len(linha) >= 2 and linha[0].lower() == exe and linha[1].isdigit()]


def gpu_alheia(meu_pid: int | None) -> list[str]:
    """Quem mais está na GPU: um llama-server que não é o deste Forja, ou um sd-cli (este Forja só chama
    isto quando não está gerando, então qualquer sd-cli é de outra janela/instância). Eles seguram VRAM e o
    sd.cpp falha por falta de memória no meio do lote. Só avisa; a pessoa decide."""
    if sys.platform != "win32":
        return []  # ponytail: só Windows (onde o Forja Desktop roda); pgrep se virar preciso
    outros = [f"llama-server (PID {p})" for p in _processos("llama-server.exe") if p != meu_pid]
    return outros + [f"sd-cli gerando imagem (PID {p})" for p in _processos("sd-cli.exe")]

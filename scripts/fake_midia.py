"""Dados fake de Imagem e Vídeo no banco ISOLADO .devredesign, para testar a interface sem carregar modelo.

Rodar com o Python portátil (tem PIL): resources/python/python.exe scripts/fake_midia.py
Cria conversas novas a cada execução; o ffmpeg vem de um runtime já baixado (ajuste FFMPEG)."""
import json, math, random, sqlite3, subprocess
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

DATA = Path(r"C:\Projetos\Forja\.devredesign")
assert DATA.name == ".devredesign"  # nunca no banco de verdade
IMG, VID = DATA / "imagens", DATA / "videos"
IMG.mkdir(exist_ok=True); VID.mkdir(exist_ok=True)
FFMPEG = r"C:\Projetos\Forja\.devval-comfy\runtimes\ffmpeg\cpu\ffmpeg.exe"
M_SDXL = r"D:\Modelos-IA\fake\juggernaut-xl-v9.safetensors"
M_FLUX = r"D:\Modelos-IA\fake\flux1-schnell-Q8_0.gguf"
M_WAN = r"D:\Modelos-IA\fake\Wan2.2-TI2V-5B-Q8_0.gguf"
rnd = random.Random(7)

PALETAS = [((242, 161, 74), (40, 24, 18)), ((79, 143, 247), (12, 18, 40)), ((63, 195, 163), (8, 30, 26)),
           ((169, 139, 245), (22, 14, 44)), ((232, 84, 106), (40, 10, 16)), ((230, 220, 200), (60, 55, 50))]

def imagem(nome, w, h, seed):
    r = random.Random(seed)
    a, b = r.choice(PALETAS)
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        t = y / h
        for x in range(0, w):
            u = x / w
            k = 0.5 + 0.5 * math.sin(6 * u + 4 * t + seed % 7)
            px[x, y] = tuple(int(b[i] + (a[i] - b[i]) * (t * 0.7 + k * 0.3)) for i in range(3))
    d = ImageDraw.Draw(im)
    for _ in range(r.randint(3, 7)):  # formas soltas: dá cara de "cena"
        cx, cy, rr = r.randint(0, w), r.randint(0, h), r.randint(min(w, h) // 10, min(w, h) // 3)
        cor = tuple(min(255, c + r.randint(-40, 60)) for c in a)
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=cor)
    im = im.filter(ImageFilter.GaussianBlur(6))
    d = ImageDraw.Draw(im)
    d.text((12, h - 22), f"fake · semente {seed}", fill=(255, 255, 255))
    p = IMG / nome
    im.save(p)
    return str(p)

def video(nome, w, h, seed, segundos=3):
    p = VID / nome
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"gradients=s={w}x{h}:d={segundos}:speed=0.02:seed={seed}:n=4",
                    "-pix_fmt", "yuv420p", "-c:v", "libvpx-vp9", "-b:v", "400k", "-deadline", "realtime", str(p)], check=True)
    return str(p)

db = sqlite3.connect(DATA / "forja.db")
agora = datetime.now()
def ts(min_atras): return (agora - timedelta(minutes=min_atras)).strftime("%Y-%m-%d %H:%M:%S.%f")

def conversa(titulo, kind, min_atras):
    cur = db.execute("insert into conversations (title, kind, workspace, pinned, archived, created_at, updated_at) values (?,?,?,?,?,?,?)",
                     (titulo, kind, None, 0, 0, ts(min_atras + 5), ts(min_atras)))
    return cur.lastrowid

def msg(conv, role, content, meta, status=None, min_atras=0):
    db.execute("insert into messages (conversation_id, role, content, thinking, status, meta, created_at) values (?,?,?,?,?,?,?)",
               (conv, role, content, "", status, json.dumps(meta, ensure_ascii=False), ts(min_atras)))

def lote(conv, prompt, modelo, itens, opts, running=False, min_atras=0, video_=False):
    """itens: lista de (status, w, h, extra)."""
    nome_m = Path(modelo).stem
    base = rnd.randint(10_000, 900_000)
    msg(conv, "user", prompt, {"opts": opts, "models": [modelo], "count": len(itens), "seed": 0, "seed_mode": "incremental"}, min_atras=min_atras + 1)
    imgs = []
    for k, (st, w, h, extra) in enumerate(itens):
        seed = base + k
        stamp = (agora - timedelta(minutes=min_atras)).strftime("%Y%m%d-%H%M%S")
        ext = "webm" if video_ else "png"
        nome = f"{stamp}-{conv:03d}-{k:02d}-s{seed}.{ext}"
        pronta = st in ("pronta", "mantida", "descartada")
        if pronta:
            path = video(nome, w, h, seed % 1000) if video_ else imagem(nome, w, h, seed)
        else:
            path = str((VID if video_ else IMG) / nome)
        i = {"path": path, "seed": seed, "model": modelo, "model_name": nome_m, "status": st, "error": "",
             "width": w, "height": h}
        if pronta:
            i.update(progress=1.0, s_passo=round(rnd.uniform(0.4, 3), 2), restante=0)
        i.update(extra)
        imgs.append(i)
    msg(conv, "assistant", "", {"job": f"fake{rnd.randint(1000, 9999)}", "count": len(itens), "seed_mode": "incremental",
                                "opts": opts, "images": imgs}, status="running" if running else "pronto", min_atras=min_atras)

S = lambda w, h: {"width": w, "height": h, "steps": 28, "cfg": 5.5, "sampler": "euler_a", "descarte_dias": 7}

# ---- Imagem: mosaico com proporções misturadas e todos os estados
c = conversa("Capa do blog — bigorna", "imagem", 3)
lote(c, "an anvil on a wooden workbench, warm forge light, cinematic, shallow depth of field", M_SDXL,
     [("pronta", 768, 512, {}), ("pronta", 512, 768, {}), ("mantida", 512, 512, {}), ("pronta", 896, 512, {}),
      ("descartada", 512, 512, {}), ("pronta", 512, 640, {}), ("pronta", 768, 512, {}), ("pronta", 512, 896, {})],
     S(768, 512), min_atras=40)
lote(c, "an anvil on a wooden workbench, blue hour, cinematic, sparks flying", M_FLUX,
     [("pronta", 768, 512, {}), ("pronta", 512, 768, {}), ("pronta", 512, 512, {}),
      ("gerando", 768, 512, {"progress": 0.46, "s_passo": 1.8, "restante": 22, "fase": "passo 13/28"}),
      ("pendente", 512, 512, {}), ("pendente", 896, 512, {}),
      ("erro", 512, 512, {"error": "sd falhou (código 1): memória insuficiente na GPU\n(fake para testar a tela)"})],
     S(768, 512), running=True, min_atras=2)

c = conversa("Retratos editoriais", "imagem", 60)
lote(c, "editorial portrait of a blacksmith, rembrandt lighting, 85mm", M_FLUX,
     [("pronta", 512, 768, {}) for _ in range(6)] + [("mantida", 512, 768, {}), ("mantida", 512, 768, {})], S(512, 768), min_atras=60)

c = conversa("Texturas de metal", "imagem", 60 * 30)
lote(c, "seamless texture of hammered copper, top down, even light", M_SDXL,
     [("pronta", 512, 512, {}) for _ in range(12)], S(512, 512), min_atras=60 * 30)

# ---- Vídeo
V = lambda w, h: {"width": w, "height": h, "steps": 30, "frames": 49, "fps": 16}
c = conversa("Raposa na neve, amanhecer", "video", 1)
lote(c, "a red fox trotting through fresh snow at dawn, slow tracking shot, soft golden light", M_WAN,
     [("pronta", 832, 480, {}), ("mantida", 832, 480, {}), ("pronta", 832, 480, {}), ("descartada", 832, 480, {})],
     V(832, 480), min_atras=30, video_=True)
lote(c, "a red fox jumping into snow, slow motion, dawn", M_WAN,
     [("pronta", 832, 480, {}), ("gerando", 832, 480, {"progress": 0.6, "s_passo": 3.9, "restante": 70, "fase": "passo 18/30"}),
      ("pendente", 832, 480, {})], V(832, 480), running=True, min_atras=1, video_=True)
c = conversa("Farol ao entardecer", "video", 120)
lote(c, "a lighthouse at sunset, waves, drone shot orbiting", M_WAN,
     [("pronta", 480, 832, {}), ("pronta", 480, 832, {})], V(480, 832), min_atras=120, video_=True)

db.commit()
print("ok", list(IMG.glob("*.png")).__len__(), "png,", list(VID.glob("*.webm")).__len__(), "webm")

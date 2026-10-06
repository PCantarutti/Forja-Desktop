"""Motor da tela Voz: um processo com um modelo Fish Audio (S2-pro e afins, no formato do fish-speech) carregado,
que atende pedidos até ser derrubado. Roda no venv do motor (RUNTIMES/tts/fish/venv, com o fish-speech), chamado por tts.py.

    python tts_fish.py --modelo <dono/repo do Hugging Face | pasta local> [--dispositivo cuda|xpu|cpu] [--contexto 4096]

Carregar leva mais de um minuto; por isso o processo fica de pé e tts.py o derruba depois de um tempo parado.
Pedidos chegam no stdin, um JSON por linha: {ref, ref_texto, texto, saida, temperatura, top_p, semente}.
Respostas no stdout, por linhas: "PRONTO <dispositivo> <bf16 | int8 em N camadas>" ao subir; por pedido, "FASE <texto>",
"PROGRESSO <0..1>", "TRANSCRICAO <texto>" (referência sem texto: o Whisper transcreveu) e, no fim, "OK <segundos> <semente>"
ou "ERRO <mensagem>". Erro ao subir: "ERRO" e sai.

Memória: pesos bf16 + cache de atenção do tamanho de --contexto. Se não couber na GPU, as camadas lineares grandes do
transformer "lento" viram int8 com escala por canal, só as que faltam para caber (a parte "rápida" fica em bf16). O codec roda
na CPU. O texto é dividido aqui em pedaços (o fish-speech acumula o histórico de todos no cache).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import traceback
from pathlib import Path

PEDACO = 280  # caracteres por geração: ~15-20 s de fala, cabe com folga num contexto de 4096
MARGEM_GB = 2.5  # cache de atenção (~0,6 GB em 4096), ativações e folga; VRAM no limite dá device lost na Arc


def diz(tipo: str, texto: str = "") -> None:
    print(f"{tipo} {texto}".strip(), flush=True)


def pedacos(texto: str, limite: int = PEDACO) -> list[str]:
    """Quebra em fim de frase (depois em vírgula, por fim em espaço) sem passar do limite."""
    out, atual = [], ""
    for frase in re.split(r"(?<=[.!?…;:])\s+|\n+", texto.strip()):
        if len(frase) > limite and atual:
            out.append(atual)
            atual = ""
        while len(frase) > limite:
            corte = max(frase.rfind(",", 0, limite), frase.rfind(" ", 0, limite))
            corte = corte if corte > limite // 3 else limite
            out.append(frase[: corte + 1].strip())
            frase = frase[corte + 1:].strip()
        if atual and len(atual) + 1 + len(frase) > limite:
            out.append(atual)
            atual = frase
        else:
            atual = f"{atual} {frase}".strip()
    if atual:
        out.append(atual)
    return [p for p in out if p]


def pedidos(fila: str, ocioso: int):
    """Os pedidos, um JSON por vez. Sem `fila`: uma linha por pedido no stdin (o Forja Desktop). Com `fila`: arquivos
    <id>.json numa pasta, na ordem do nome (o Forja no Docker, que sobe o motor no Windows pelo forja-runner, sem
    stdin); cada um é anunciado com "PEDIDO <id>" no log e apagado depois de atendido. Parado `ocioso` segundos com a
    fila vazia, o processo sai sozinho (a VRAM volta)."""
    if not fila:
        for linha in sys.stdin:
            if linha.strip():
                yield json.loads(linha)
        return
    pasta, parado = Path(fila), time.time()
    while time.time() - parado < ocioso:
        prontos = sorted(pasta.glob("*.json"))
        if not prontos:
            time.sleep(0.3)
            continue
        f = prontos[0]
        try:
            q = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            time.sleep(0.2)  # ainda sendo escrito
            continue
        f.unlink(missing_ok=True)
        diz("PEDIDO", f.stem)
        yield q
        parado = time.time()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--modelo", required=True)
    p.add_argument("--dispositivo", default="")
    p.add_argument("--contexto", type=int, default=4096)
    p.add_argument("--fila", default="")  # pasta de pedidos (modo do Docker, sem stdin)
    p.add_argument("--ocioso", type=int, default=300)  # só com --fila: sai depois disso sem pedido
    a = p.parse_args()
    try:
        import soundfile as sf
        import torch
        import torchaudio

        def carregar(caminho, *_, **__):
            # o torchaudio novo lê pelo torchcodec, que não existe em todo índice do PyTorch (o XPU não tem)
            dados, sr = sf.read(str(caminho), dtype="float32", always_2d=True)
            return torch.from_numpy(dados.T.copy()), sr
        torchaudio.load = carregar
        from fish_speech.models.text2semantic.inference import (encode_audio, generate_long, init_model,
                                                                load_codec_model)

        dev = a.dispositivo or ("cuda" if torch.cuda.is_available() else
                                "xpu" if hasattr(torch, "xpu") and torch.xpu.is_available() else "cpu")
        pasta = Path(a.modelo)
        if not pasta.is_dir():
            diz("FASE", f"obtendo {a.modelo} (baixa só na 1ª vez)")
            from huggingface_hub import snapshot_download
            pasta = Path(snapshot_download(a.modelo))
        if not (pasta / "codec.pth").is_file():
            raise ValueError(f"{pasta} não parece um modelo Fish Audio (falta codec.pth).")
        pesos_gb = sum(f.stat().st_size for f in pasta.glob("*.safetensors")) / 2**30 \
            or sum(f.stat().st_size for f in pasta.glob("model*.pth")) / 2**30
        livre_gb = 1e9
        if dev != "cpu":
            try:
                livre_gb = getattr(torch, dev).mem_get_info()[0] / 2**30
            except RuntimeError:  # a XPU (Arc) não informa o livre: o total menos o que a tela e o sistema usam
                livre_gb = getattr(torch, dev).get_device_properties(0).total_memory / 2**30 * 0.80
        falta_gb = pesos_gb + MARGEM_GB - livre_gb
        diz("FASE", f"carregando o modelo em {dev}" + (f" (int8 em parte: faltam {falta_gb:.1f} GB)" if falta_gb > 0 else ""))
        model, decode = init_model(pasta, "cpu" if falta_gb > 0 else dev, torch.bfloat16)
        modo = "bf16"
        if falta_gb > 0:
            camadas = quantizar(model, torch, falta_gb)
            modo = f"int8 em {camadas} camadas"
            model = model.to(dev)
            for k in ("fixed_temperature", "fixed_top_p", "fixed_repetition_penalty"):
                setattr(model, k, getattr(model, k).to(dev))
        # o fish-speech dimensiona o cache pelo config (32k tokens = ~5 GB no S2-pro): o contexto manda
        model.config.max_seq_len = min(model.config.max_seq_len, a.contexto)
        with torch.device(dev):
            model.setup_caches(max_batch_size=1, max_seq_len=model.config.max_seq_len, dtype=torch.bfloat16)
        model._cache_setup_done = True
        if dev != "cpu":
            usado, total = getattr(torch, dev).memory_reserved(), getattr(torch, dev).get_device_properties(0).total_memory
            if usado > total * 0.9:  # em 06/10/2026 a B580 deu device lost com a VRAM quase cheia: não arrisca
                raise ValueError(f"O modelo ocupou {usado / 2**30:.1f} de {total / 2**30:.1f} GB da GPU: sem folga para gerar.")
        codec = load_codec_model(pasta / "codec.pth", "cpu", torch.float32)
        diz("PRONTO", f"{dev} {modo}")
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        diz("ERRO", f"{e.__class__.__name__}: {e}"[:500].replace("\n", " "))
        return 1

    for q in pedidos(a.fila, a.ocioso):
        try:
            t0 = time.time()
            ref_texto = q.get("ref_texto", "").strip()
            if q.get("ref") and not ref_texto:
                diz("FASE", "transcrevendo a referência")
                ref_texto = transcrever(q["ref"], sf)
                diz("TRANSCRICAO", ref_texto)
            prompt = [encode_audio(q["ref"], codec, "cpu")] if q.get("ref") else None
            semente = int(q.get("semente", -1))
            semente = semente if semente >= 0 else random.randint(0, 2**31 - 1)
            torch.manual_seed(semente)
            partes = pedacos(q["texto"])
            codes = []
            for i, parte in enumerate(partes):
                diz("FASE", f"gerando ({i + 1}/{len(partes)})" if len(partes) > 1 else "gerando")
                diz("PROGRESSO", f"{i / len(partes):.3f}")
                for r in generate_long(model=model, device=dev, decode_one_token=decode, text=parte,
                                       temperature=float(q.get("temperatura", 1.0)), top_p=float(q.get("top_p", 0.9)),
                                       chunk_length=10_000, prompt_text=[ref_texto] if prompt else None,
                                       prompt_tokens=prompt):
                    if r.action == "sample":
                        codes.append(r.codes.cpu())
                    elif r.action == "next":
                        break
            diz("FASE", "montando o áudio")
            with torch.inference_mode():
                wav = codec.from_indices(torch.cat(codes, dim=1).long()[None])[0, 0].float().numpy()
            sf.write(q["saida"], wav, codec.sample_rate)
            diz("PROGRESSO", "1")
            diz("OK", f"{len(wav) / codec.sample_rate:.2f} {semente} {time.time() - t0:.1f}")
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            diz("ERRO", f"{e.__class__.__name__}: {e}"[:500].replace("\n", " "))
        finally:
            if dev != "cpu":
                getattr(torch, dev).empty_cache()
    return 0


def transcrever(caminho: str, sf) -> str:
    """Uma vez por voz (tts.py guarda o texto), sempre na CPU: na GPU ele disputaria a VRAM com o modelo de pé."""
    from transformers import pipeline
    dados, sr = sf.read(caminho, dtype="float32", always_2d=True)
    asr = pipeline("automatic-speech-recognition", "openai/whisper-large-v3-turbo", device="cpu")
    return asr({"raw": dados.mean(axis=1), "sampling_rate": sr})["text"].strip()


def quantizar(model, torch, falta_gb: float) -> int:
    """Converte para int8 (escala por canal) as Linear das camadas do transformer lento, da última para a primeira,
    só até economizar `falta_gb`: cada camada em int8 paga a volta a bf16 em toda conta, então quanto menos, mais
    rápido. A parte rápida (fast_*) roda 10x por quadro e a cabeça de saída é enorme para dequantizar: ficam em bf16.
    Devolve quantas camadas foram convertidas."""
    class Int8Linear(torch.nn.Module):
        def __init__(self, lin):
            super().__init__()
            w = lin.weight.data.float()
            escala = w.abs().amax(dim=1, keepdim=True).clamp(min=1e-8) / 127
            self.register_buffer("q", (w / escala).round().clamp(-127, 127).to(torch.int8))
            self.register_buffer("escala", escala.to(torch.bfloat16))
            self.bias = lin.bias

        def forward(self, x):
            return torch.nn.functional.linear(x, self.q.to(x.dtype) * self.escala, self.bias)

    def trocar(m) -> int:
        poupado = 0
        for nome, filho in m.named_children():
            if isinstance(filho, torch.nn.Linear):
                poupado += filho.weight.numel()  # bf16 (2 bytes) -> int8 (1 byte)
                setattr(m, nome, Int8Linear(filho))
            else:
                poupado += trocar(filho)
        return poupado

    poupado, n = 0, 0
    for camada in reversed(model.layers):
        if poupado / 2**30 >= falta_gb:
            break
        poupado += trocar(camada)
        n += 1
    if poupado / 2**30 < falta_gb:
        raise ValueError(f"Sem memória na GPU: mesmo em int8 faltam {falta_gb - poupado / 2**30:.1f} GB. "
                         "Descarregue o que estiver na GPU.")
    return n


if __name__ == "__main__":
    sys.exit(main())

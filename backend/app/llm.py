"""Cliente de LLM local.

- LM Studio (e qualquer servidor OpenAI-compatível): POST {base}/chat/completions em streaming SSE.
- Ollama: POST {host}/api/chat (API nativa). A camada /v1 do Ollama ignora `options`, então
  `num_ctx` só é respeitado pela API nativa — era a causa do contexto de 4k truncando as ferramentas.

`chat_stream` normaliza os dois para eventos: ("content", str), ("reasoning", str),
("tool_args", {"name": str, "text": str}) enquanto os argumentos de uma tool call chegam em pedaços, e
("done", {"tool_calls": [...], "prompt_tokens": int|None, "completion_tokens": int|None}).
Mensagens de entrada/saída ficam sempre no formato OpenAI.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

from . import config, db, localai

TIMEOUT = httpx.Timeout(connect=10, read=600, write=60, pool=10)


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after  # segundos pedidos pelo provedor (cabeçalho Retry-After)


def spec(provider: str) -> dict:
    if provider not in config.PROVIDERS:
        raise LLMError(f"Provider desconhecido: {provider}")
    return config.PROVIDERS[provider]


def base_url(provider: str) -> str:
    return spec(provider)["url"].rstrip("/")


def headers(provider: str) -> dict:
    key = spec(provider).get("api_key")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _conn_error(provider: str, e: Exception) -> LLMError:
    if not isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        return LLMError(f"Conexão com {provider} interrompida ({e.__class__.__name__}). "
                        "O modelo pode ter sido descarregado/recarregado; tente de novo.")
    # A dica do Ollama depende de onde ele está: no Forja Desktop o endereço é o loopback e não há
    # nada a configurar; apontando para outra máquina (ou para o Forja em Docker), aí sim ele
    # precisa escutar em 0.0.0.0. Dizer a segunda coisa na primeira situação só confunde.
    longe = urlparse(base_url(provider)).hostname not in ("127.0.0.1", "localhost", "::1")
    if CLOUD_HOST in base_url(provider):  # nuvem: não há OLLAMA_HOST nem bandeja para conferir
        return LLMError(f"Sem resposta do Ollama Cloud ({base_url(provider)}): {e.__class__.__name__}. "
                        "Confira a internet; se ela estiver ok, o serviço pode estar instável — tente de novo "
                        "em instantes.")
    hint = {
        "ollama": ("Ollama está rodando e escutando em " + str(urlparse(base_url(provider)).hostname)
                   + "? Para aceitar conexão de fora da máquina dele, ele precisa subir com "
                     "OLLAMA_HOST=0.0.0.0." if longe
                   else "O Ollama está rodando? Procure o ícone dele na bandeja do sistema."),
        "lmstudio": "LM Studio está com o servidor ligado e 'Serve on Local Network' ativo?",
        "llamacpp": "Nenhum modelo carregado. Abra o painel IA local e carregue um .gguf.",
    }.get(spec(provider)["type"], "Confira a URL em Configurações › Provedores.")
    return LLMError(f"Não foi possível conectar em {base_url(provider)}: {e.__class__.__name__}. {hint}")


async def list_models(provider: str) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=10, headers=headers(provider)) as c:
            r = await c.get(f"{base_url(provider)}/models")
    except httpx.HTTPError as e:
        raise _conn_error(provider, e) from e
    if r.status_code >= 400:
        raise LLMError(f"/models respondeu {r.status_code}: {r.text[:300]}", r.status_code)
    return sorted(m["id"] for m in r.json().get("data", []) if "embed" not in m["id"].lower())


# ------------------------------------------------------------------ capacidades por backend (E13-A)
# O que o Forja consegue controlar em cada tipo de servidor. A política de execução (E4) lê daqui, e sem
# a capacidade o caminho é o seguro: um modelo só, auxiliares em sequência, sem trocar de modelo e sem
# cache em disco. "parcial" = dá para fazer só uma parte (ver o comentário de cada uma); nunca tentar o
# que está "nao".
CAPACIDADES: dict[str, dict[str, str]] = {
    "llamacpp": dict.fromkeys(("carregar", "vram", "slots", "cache_disco", "timings_cache", "janela",
                               "parametros_carga"), "sim"),
    # carregar: keep_alive e /api/generate vazio; vram: /api/ps dá size_vram; carga: só num_ctx por requisição
    "ollama": {"carregar": "parcial", "vram": "parcial", "slots": "nao", "cache_disco": "nao",
               "timings_cache": "parcial", "janela": "sim", "parametros_carga": "parcial"},
    # A nuvem da Ollama não tem modelo "carregado" nem VRAM nossa; a janela é a do modelo (/api/show).
    "ollama_nuvem": {"carregar": "nao", "vram": "nao", "slots": "nao", "cache_disco": "nao",
                     "timings_cache": "parcial", "janela": "sim", "parametros_carga": "nao"},
    # carregar: API REST v0 / lms; janela: loaded_context_length
    "lmstudio": {"carregar": "parcial", "vram": "nao", "slots": "nao", "cache_disco": "nao",
                 "timings_cache": "parcial", "janela": "sim", "parametros_carga": "nao"},
    # janela: max_model_len (vLLM), context_length (OpenRouter, llama-server) ou o campo manual
    "openai": {"carregar": "nao", "vram": "nao", "slots": "nao", "cache_disco": "nao",
               "timings_cache": "parcial", "janela": "parcial", "parametros_carga": "nao"},
}
ROTULOS_CAPACIDADE = {
    "carregar": "carregar e descarregar o modelo", "vram": "ler a VRAM",
    "slots": "rodar em paralelo com slots controlados pelo Forja", "cache_disco": "cache em disco",
    "timings_cache": "medir quanto do cache foi reaproveitado", "janela": "descobrir a janela sozinho",
    "parametros_carga": "mudar parâmetros de carga (contexto, KV, camadas na GPU)",
}


def tipo_capacidade(provider: str) -> str:
    s = spec(provider)
    return "ollama_nuvem" if s["type"] == "ollama" and CLOUD_HOST in s["url"] else s["type"]


def capacidade(provider: str, nome: str) -> str:
    """'sim' | 'parcial' | 'nao'. Tipo desconhecido não pode nada."""
    return CAPACIDADES.get(tipo_capacidade(provider), {}).get(nome, "nao")


def indisponiveis(tipo: str) -> dict[str, list[str]]:
    """Para a tela: o que o Forja não faz e o que faz só em parte, neste tipo de servidor."""
    caps = CAPACIDADES.get(tipo, {})
    return {n: [ROTULOS_CAPACIDADE[k] for k, v in caps.items() if v == n] for n in ("nao", "parcial")}


_JANELAS: dict[tuple[str, str], tuple[int | None, float]] = {}  # (provider, modelo) -> (janela, quando)
JANELA_TTL = 600  # /models do OpenRouter tem centenas de KB: não a cada turno
CAMPOS_JANELA = ("max_model_len", "context_length", "max_context_length", "context_window", "n_ctx")


def _janela_de(item: dict) -> int | None:
    for k in CAMPOS_JANELA:
        if isinstance(item.get(k), int) and item[k] > 0:
            return item[k]
    for sub in ("top_provider", "meta"):  # OpenRouter: top_provider.context_length
        if isinstance(item.get(sub), dict) and (n := _janela_de(item[sub])):
            return n
    return None


async def _janela_openai(provider: str, model: str) -> int | None:
    """vLLM, OpenRouter, Groq, llama-server...: cada um põe a janela num campo do /models. O
    llama-server também responde /props com a janela por slot."""
    host = base_url(provider)
    async with httpx.AsyncClient(timeout=10, headers=headers(provider)) as c:
        try:
            r = await c.get(f"{host}/models")
            itens = r.json().get("data") or [] if r.status_code < 400 else []
            if n := next((_janela_de(i) for i in itens if isinstance(i, dict) and i.get("id") == model), None):
                return n
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        try:
            r = await c.get(f"{host.removesuffix('/v1')}/props")
            if r.status_code < 400:
                j = r.json()
                return _janela_de(j.get("default_generation_settings") or {}) or _janela_de(j)
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
    return None


async def _janela_ollama(provider: str, model: str) -> int | None:
    """Local: a janela carregada de fato (/api/ps), que o Ollama pode cortar por falta de memória. Nuvem:
    a do modelo (/api/show), porque lá não há o que carregar."""
    host = base_url(provider).removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=5, headers=headers(provider)) as c:
            if CLOUD_HOST in host:
                info = (await c.post(f"{host}/api/show", json={"model": model})).json().get("model_info") or {}
                return next((v for k, v in info.items() if k.endswith(".context_length") and isinstance(v, int)), None)
            for m in (await c.get(f"{host}/api/ps")).json().get("models") or []:
                if model in (m.get("name"), m.get("model")) and isinstance(m.get("context_length"), int):
                    return m["context_length"]
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return None


async def context_limit(provider: str, model: str, num_ctx: int) -> int | None:
    """Tamanho real da janela de contexto; None = não se sabe (no tipo openai o turno recusa: ver
    `janela_obrigatoria`).

    llama.cpp: a do slot. LM Studio: a carregada. Ollama: a de /api/ps (nuvem: a do modelo); antes de o
    modelo carregar, o num_ctx que enviamos. openai: o campo manual do provedor, senão o que o servidor
    informar."""
    kind = spec(provider)["type"]
    if kind == "ollama":
        chave = (provider, model)
        if CLOUD_HOST in base_url(provider) and chave in _JANELAS and time.monotonic() - _JANELAS[chave][1] < JANELA_TTL:
            return _JANELAS[chave][0] or num_ctx
        n = await _janela_ollama(provider, model)
        if CLOUD_HOST in base_url(provider):
            _JANELAS[chave] = (n, time.monotonic())
            return n or num_ctx
        return min(n, num_ctx) if n else num_ctx  # local: pedimos num_ctx; o Ollama pode ter dado menos
    if kind == "openai":
        if manual := spec(provider).get("context_window"):
            return int(manual)
        chave = (provider, model)
        if chave not in _JANELAS or time.monotonic() - _JANELAS[chave][1] > JANELA_TTL:
            _JANELAS[chave] = (await _janela_openai(provider, model), time.monotonic())
        return _JANELAS[chave][0]
    if kind == "llamacpp":
        st = localai.status()
        return localai.ctx_por_requisicao(st.get("ctx"), st.get("params")) if st.get("running") else None
    if kind == "lmstudio":
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{base_url(provider).removesuffix('/v1')}/api/v0/models/{model}")
            info = r.json()
            return info.get("loaded_context_length") or info.get("max_context_length")
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
    return None


def janela_obrigatoria(provider: str, janela: int | None) -> str | None:
    """Mensagem para recusar o turno quando o tipo genérico não informa a janela e ninguém a preencheu.
    Supor 32k com um vLLM de 8k fazia a compactação disparar tarde e o servidor recusar no meio."""
    if janela or spec(provider)["type"] != "openai":
        return None
    return (f"O Forja não sabe o tamanho da janela de contexto de '{spec(provider).get('name') or provider}': "
            "o servidor não informa. Preencha 'Janela de contexto' desse provedor em Configurações › "
            "Provedores (no vLLM é o --max-model-len).")


def _raise_for(provider: str, status: int, body: bytes, cabecalhos=None) -> None:
    text = body.decode("utf-8", "replace")[:1000]
    try:  # só o formato em segundos; a data HTTP é rara em API de modelo e cai na espera normal
        espera = float((cabecalhos or {}).get("retry-after") or "") or None
    except ValueError:
        espera = None
    raise LLMError(f"{provider} respondeu HTTP {status}: {text}", status, espera)


# Esforço -> raciocínio do modelo. Só mandamos quando o modelo entende, senão o servidor recusa.
EFFORT_LEVEL = {"baixo": "low", "medio": "medium", "alto": "high", "maximo": "high", "extremo": "low"}
REASONING_MODELS = re.compile(r"gpt-oss|gpt-5|^o[1-4](-|$)|deepseek-r|grok|magistral", re.I)


async def _reasoning(provider: str, model: str, effort: str | None, body: dict, messages: list[dict],
                     budget_mult: float = 1.0) -> None:
    """Acrescenta o controle de raciocínio conforme o provider e o modelo."""
    level = EFFORT_LEVEL.get(effort or "")
    if not level:
        return
    kind = spec(provider)["type"]
    if kind == "ollama":
        caps = await capabilities(provider, model) or set()
        if "thinking" in caps:  # Ollama recusa `think` em modelo sem raciocínio
            body["think"] = level if REASONING_MODELS.search(model) else (effort != "baixo")
        return
    if kind in ("llamacpp", "lmstudio"):
        # Teto de pensamento desta requisição. Dois nomes porque são coisas diferentes com o mesmo
        # apelido: `reasoning_budget_tokens` é o campo que o llama-server lê por requisição (e só
        # obedece de b9982 em diante); `reasoning_budget` é como a flag de linha de comando aparece
        # em alguns forks e no LM Studio. O que o servidor não conhecer é ignorado sem erro.
        # Quem garante o teto mesmo no build velho é o --reasoning-budget do launch (localai.argv).
        body["reasoning_budget_tokens"] = body["reasoning_budget"] = _budget(effort, budget_mult)
    if REASONING_MODELS.search(model):
        body["reasoning_effort"] = level
    elif effort == "baixo" and re.search(r"qwen", model, re.I) and messages and messages[0]["role"] == "system":
        # Qwen: interruptor por texto, do próprio template
        messages[0] = {**messages[0], "content": messages[0]["content"] + "\n/no_think"}


# Amostragem por modelo (tela Inferência) -> corpo da requisição. `openai` genérico só aceita o que
# está no padrão da API; o que é do llama.cpp (top_k, min_p, repeat_penalty...) faria ele devolver 400.
OPENAI_PADRAO = {"temperature": "temperature", "top_p": "top_p", "max_tokens": "max_tokens", "stop": "stop"}
LLAMACPP_EXTRA = {"top_k": "top_k", "min_p": "min_p", "repeat_penalty": "repeat_penalty"}
OLLAMA_OPTS = {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "min_p": "min_p",
               "repeat_penalty": "repeat_penalty", "max_tokens": "num_predict", "stop": "stop"}


def _inference(provider: str, model: str, extra: dict) -> None:
    """Aplica os ajustes de amostragem salvos para este modelo. Nada salvo = padrão do servidor."""
    cfg = db.get_model_setting(model).get("inference") or {}
    cfg = {k: v for k, v in cfg.items() if not (k == "max_tokens" and not v) and not (k == "stop" and not v)}
    if not cfg:
        return
    kind = spec(provider)["type"]
    if kind == "ollama":
        opts = {destino: cfg[chave] for chave, destino in OLLAMA_OPTS.items() if chave in cfg}
        if opts:
            extra["options"] = {**extra.get("options", {}), **opts}
        if "think" in cfg and not cfg["think"]:
            extra["think"] = False
        return
    mapa = dict(OPENAI_PADRAO) if kind == "openai" else {**OPENAI_PADRAO, **LLAMACPP_EXTRA}
    for chave, destino in mapa.items():
        if chave in cfg:
            extra[destino] = cfg[chave]
    if kind in ("llamacpp", "lmstudio"):
        if "think" in cfg:  # o template do gguf decide; o Qwen3 usa enable_thinking
            extra["chat_template_kwargs"] = {**extra.get("chat_template_kwargs", {}),
                                             "enable_thinking": bool(cfg["think"])}
        if cfg.get("reasoning_budget", -1) != -1:
            extra["reasoning_budget"] = int(cfg["reasoning_budget"])


NO_THINK = chr(10) + "/no_think"   # interruptor por texto do template do Qwen3


def _budget(effort: str | None, mult: float = 1.0) -> int:
    """Teto de pensamento desta chamada, em tokens (ver config.REASONING_BUDGET).

    `mult` afrouxa para o subagente, que é quem de fato resolve a tarefa. Zero continua zero: o
    esforço Baixo é para não pensar, e multiplicar isso não faria sentido.
    """
    base = config.REASONING_BUDGET.get(effort or "medio", config.REASONING_BUDGET["medio"])
    return int(base * mult * config.REASONING_BUDGET_MULT) if base else 0


def _sem_pensar(provider: str, extra: dict, messages: list[dict]) -> tuple[dict, list[dict]]:
    """Mesma chamada com o pensamento desligado. Não existe um interruptor só: cada família usa o seu
    (`think` no Ollama, `enable_thinking` no template do Qwen3.6/GLM, `reasoning_budget_tokens` no
    llama-server, `/no_think` no texto do Qwen3). Mandamos todos: o que não for entendido é ignorado
    sem erro, e assim isto vale para o modelo que você trocar amanhã, não só para o de hoje."""
    saida, msgs = dict(extra), list(messages)
    if spec(provider)["type"] == "ollama":
        saida["think"] = False
        return saida, msgs
    saida["chat_template_kwargs"] = {**saida.get("chat_template_kwargs", {}), "enable_thinking": False}
    saida["reasoning_budget_tokens"] = saida["reasoning_budget"] = 0
    if (msgs and msgs[0]["role"] == "system" and isinstance(msgs[0].get("content"), str)
            and not msgs[0]["content"].endswith(NO_THINK)):  # _reasoning já pode ter posto no esforço baixo
        msgs[0] = {**msgs[0], "content": msgs[0]["content"] + NO_THINK}
    return saida, msgs


async def chat_stream(provider: str, model: str, messages: list[dict], tools: list[dict] | None,
                      num_ctx: int, effort: str | None = None, think: bool | None = None,
                      budget_mult: float = 1.0) -> AsyncIterator[tuple[str, object]]:
    impl = _ollama_stream if spec(provider)["type"] == "ollama" else _openai_stream
    messages = list(messages)
    extra: dict = {}
    await _reasoning(provider, model, effort, extra, messages, budget_mult)
    _inference(provider, model, extra)  # o ajuste do modelo vale mais que o esforço da conversa
    if think is False:  # chamada mecânica (compactar, titular, commit): raciocinar aqui é desperdício
        extra, messages = _sem_pensar(provider, extra, messages)
    try:
        async for ev in impl(provider, model, messages, tools, num_ctx, extra):
            yield ev
    except httpx.HTTPError as e:
        raise _conn_error(provider, e) from e


# ------------------------------------------------------------------ OpenAI-compatível

async def _openai_stream(provider, model, messages, tools, num_ctx, extra: dict | None = None):
    body: dict = {"model": model, "messages": messages, "stream": True,
                  "stream_options": {"include_usage": True}, **(extra or {})}
    if tools:
        body["tools"] = tools
        # No llama-server o padrão é false: o modelo só consegue chamar UMA ferramenta por resposta.
        # Sem isto a Maestro nunca despachava duas tarefas juntas (e o agente lia um arquivo por vez),
        # mesmo com max_workers > 1. A OpenAI já liga por padrão; outros compatíveis ficam como estão.
        if config.PROVIDERS.get(provider, {}).get("type") == "llamacpp":
            body.setdefault("parallel_tool_calls", True)
    calls: dict[int, dict] = {}
    prompt_tokens = completion_tokens = cached_tokens = None
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers(provider)) as c:
        async with c.stream("POST", f"{base_url(provider)}/chat/completions", json=body) as r:
            if r.status_code >= 400:
                _raise_for(provider, r.status_code, await r.aread(), r.headers)
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    prompt_tokens = chunk["usage"].get("prompt_tokens")
                    completion_tokens = chunk["usage"].get("completion_tokens")
                    cached_tokens = (chunk["usage"].get("prompt_tokens_details") or {}).get("cached_tokens",
                                                                                              cached_tokens)
                if chunk.get("timings"):  # llama-server: quanto do prompt veio do cache (cache_n) e quanto processou
                    t = chunk["timings"]
                    if t.get("cache_n") is not None:
                        cached_tokens = t["cache_n"]
                        prompt_tokens = prompt_tokens or (t["cache_n"] + (t.get("prompt_n") or 0))
                if chunk.get("error"):
                    raise LLMError(str(chunk["error"]))
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield "reasoning", reasoning
                    if delta.get("content"):
                        yield "content", delta["content"]
                    for tc in delta.get("tool_calls") or []:
                        acc = calls.setdefault(tc.get("index", len(calls)), {"id": None, "name": "", "arguments": ""})
                        acc["id"] = tc.get("id") or acc["id"]
                        fn = tc.get("function") or {}
                        acc["name"] += fn.get("name") or ""
                        acc["arguments"] += fn.get("arguments") or ""
                        # Escrever um arquivo grande é a maior espera do turno e nada disso aparecia:
                        # os argumentos só viravam evento no fim. Repassar o pedaço deixa a UI mostrar
                        # que o modelo continua escrevendo (e o contador de tokens continuar andando).
                        if fn.get("arguments"):
                            yield "tool_args", {"name": acc["name"], "text": fn["arguments"]}
    out = []
    for acc in calls.values():
        try:
            args = json.loads(acc["arguments"] or "{}", strict=False)
        except json.JSONDecodeError:
            args = {"__raw__": acc["arguments"]}
        out.append({"id": acc["id"] or _new_id(), "name": acc["name"], "arguments": args})
    yield "done", {"tool_calls": out, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                   "cached_tokens": cached_tokens}


# ------------------------------------------------------------------ Ollama nativo

def _split_parts(content) -> tuple[str, list[str]]:
    """content parts do formato OpenAI -> (texto, imagens em base64) para a API do Ollama."""
    if isinstance(content, str):
        return content, []
    text, images = [], []
    for part in content:
        if part.get("type") == "text":
            text.append(part["text"])
        elif part.get("type") == "image_url":
            url = part["image_url"]["url"]
            images.append(url.split(",", 1)[1] if url.startswith("data:") else url)
    return "\n".join(text), images


LOCAL_TYPES = ("ollama", "lmstudio", "llamacpp")


def is_local(provider: str) -> bool:
    """Servidor local (aceita `reasoning_content`/`thinking` de volta nas mensagens do assistente)."""
    try:
        return spec(provider)["type"] in LOCAL_TYPES
    except Exception:
        return False


def _to_ollama(messages: list[dict]) -> list[dict]:
    names: dict[str, str] = {}
    out = []
    for m in messages:
        m = dict(m)
        if m.get("reasoning_content"):  # no /api/chat o campo chama `thinking`
            m["thinking"] = m.pop("reasoning_content")
        if not isinstance(m.get("content"), str):
            m["content"], images = _split_parts(m["content"])
            if images:
                m["images"] = images
        if m.get("tool_calls"):
            tcs = []
            for tc in m["tool_calls"]:
                args = tc["function"]["arguments"]
                names[tc["id"]] = tc["function"]["name"]
                tcs.append({"function": {"name": tc["function"]["name"],
                                         "arguments": json.loads(args) if isinstance(args, str) else args}})
            m["tool_calls"] = tcs
        if m["role"] == "tool":
            m["tool_name"] = names.get(m.pop("tool_call_id", ""), "")
        out.append(m)
    return out


async def _ollama_stream(provider, model, messages, tools, num_ctx, extra: dict | None = None):
    host = base_url(provider).removesuffix("/v1")
    extra = dict(extra or {})
    opts = extra.pop("options", {})  # amostragem do modelo: entra junto com num_ctx, não por cima dele
    body: dict = {"model": model, "messages": _to_ollama(messages), "stream": True,
                  "options": {"num_ctx": num_ctx, **opts}, **extra}
    if tools:
        body["tools"] = tools
    calls, prompt_tokens, completion_tokens = [], None, None
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers(provider)) as c:
        async with c.stream("POST", f"{host}/api/chat", json=body) as r:
            if r.status_code >= 400:
                _raise_for(provider, r.status_code, await r.aread(), r.headers)
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise LLMError(str(chunk["error"]))
                msg = chunk.get("message") or {}
                if msg.get("thinking"):
                    yield "reasoning", msg["thinking"]
                if msg.get("content"):
                    yield "content", msg["content"]
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args or "{}", strict=False)
                    calls.append({"id": _new_id(), "name": fn.get("name", ""), "arguments": args})
                if chunk.get("done"):
                    prompt_tokens = chunk.get("prompt_eval_count")
                    completion_tokens = chunk.get("eval_count")
    yield "done", {"tool_calls": calls, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}


def _new_id() -> str:
    return "call_" + uuid.uuid4().hex[:12]


# ------------------------------------------------------------------ cota do Ollama Cloud

CLOUD_HOST = "ollama.com"
USAGE_TTL = 60          # a cota anda devagar; consultar a cada chamada seria desperdício
_USAGE: dict[str, tuple[float, dict | None]] = {}


def is_cloud(provider: str) -> bool:
    """Provedor que cobra cota no Ollama Cloud: nuvem da Ollama, com chave."""
    try:
        s = spec(provider)
    except LLMError:
        return False
    return s["type"] == "ollama" and CLOUD_HOST in s["url"] and bool(s.get("api_key"))


def _limites(bruto: dict) -> tuple[list[dict], list[dict]]:
    """Normaliza o `limits` do /api/usage. O plano grátis devolve só `monthly` e `models` como lista;
    o pago devolve `session`/`weekly` e, em algumas versões, `models` como objeto. Aceita os dois."""
    limites, modelos = [], []
    for nome, janela in (bruto or {}).items():
        if not isinstance(janela, dict):
            continue
        limites.append({"name": nome, "usage": float(janela.get("usage") or 0)})
        crus = janela.get("models") or []
        atual = ([{"name": k, **(v if isinstance(v, dict) else {})} for k, v in crus.items()]
                 if isinstance(crus, dict) else [m for m in crus if isinstance(m, dict)])
        if len(atual) > len(modelos):
            modelos = atual
    ordem = {"session": 0, "daily": 1, "weekly": 2, "monthly": 3}
    limites.sort(key=lambda l: (ordem.get(l["name"], 9), l["name"]))
    return limites, sorted(modelos, key=lambda m: -(m.get("request_count") or 0))


async def usage(provider: str) -> dict | None:
    """Cota consumida no Ollama Cloud, pelo GET /api/usage. None quando não se aplica ou a consulta
    falha: o endpoint não é documentado (pode mudar ou sumir), então nada aqui levanta erro."""
    if not is_cloud(provider):
        return None
    agora = time.monotonic()
    if (cache := _USAGE.get(provider)) and agora - cache[0] < USAGE_TTL:
        return cache[1]
    host = base_url(provider).removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=10, headers=headers(provider)) as c:
            r = await c.get(f"{host}/api/usage")
        dados = r.json() if r.status_code < 400 else {}
    except (httpx.HTTPError, ValueError):
        dados = {}
    limites, modelos = _limites(dados.get("limits") or {})
    saida = ({"provider": provider, "name": spec(provider)["name"], "limits": limites, "models": modelos}
             if limites else None)
    _USAGE[provider] = (agora, saida)
    return saida


# ------------------------------------------------------------------ capacidades do modelo

_CAPS: dict[tuple[str, str], set[str]] = {}  # cache só de respostas positivas (None = tentar de novo)


async def capabilities(provider: str, model: str) -> set[str] | None:
    """{"vision", ...} declarado pelo provider; None = provider não informa (vale o override do usuário).

    Ollama: POST /api/show devolve `capabilities` (desde ~0.6.5; ausente = desconhecido).
    LM Studio: GET /api/v0/models/{id} devolve `type` in llm | vlm | embeddings.
    """
    key = (provider, model)
    kind, host = spec(provider)["type"], base_url(provider).removesuffix("/v1")
    if kind == "llamacpp":
        # Do próprio modelo, e não do que está carregado agora: perguntar pelo gemma com o Qwen no ar
        # respondia pelo Qwen, e o cache guardava o erro para sempre. Barato (lê arquivos), sem cache.
        try:
            visao = localai.visao_do_alias(model)
        except Exception:
            visao = None
        if visao is None:
            visao = bool(localai.status().get("vision")) if localai.status().get("alias") == model else None
        return None if visao is None else ({"vision"} if visao else set())
    if key in _CAPS:
        return _CAPS[key]
    caps = None
    try:
        async with httpx.AsyncClient(timeout=5, headers=headers(provider)) as c:
            if kind == "ollama":
                j = (await c.post(f"{host}/api/show", json={"model": model})).json()
                caps = set(j["capabilities"]) if isinstance(j.get("capabilities"), list) else None
            elif kind == "lmstudio":
                t = (await c.get(f"{host}/api/v0/models/{model}")).json().get("type")
                caps = None if not t else ({"vision"} if t == "vlm" else set())
    except (httpx.HTTPError, ValueError, AttributeError):
        caps = None
    if caps is not None:
        _CAPS[key] = caps
    return caps

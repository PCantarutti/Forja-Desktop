"""Cliente de LLM local.

- LM Studio (e qualquer servidor OpenAI-compatível): POST {base}/chat/completions em streaming SSE.
- Ollama: POST {host}/api/chat (API nativa). A camada /v1 do Ollama ignora `options`, então
  `num_ctx` só é respeitado pela API nativa — era a causa do contexto de 4k truncando as ferramentas.

`chat_stream` normaliza os dois para eventos: ("content", str), ("reasoning", str),
("done", {"tool_calls": [...], "prompt_tokens": int|None, "completion_tokens": int|None}).
Mensagens de entrada/saída ficam sempre no formato OpenAI.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import AsyncIterator

import httpx

from . import config, db, localai

TIMEOUT = httpx.Timeout(connect=10, read=600, write=60, pool=10)


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


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
    hint = {
        "ollama": "Ollama está rodando? Ele precisa escutar em 0.0.0.0 (OLLAMA_HOST=0.0.0.0) para o Docker alcançar.",
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


async def context_limit(provider: str, model: str, num_ctx: int) -> int | None:
    """Tamanho real da janela de contexto. Ollama: o num_ctx que enviamos. LM Studio: o carregado."""
    kind = spec(provider)["type"]
    if kind == "ollama":
        return num_ctx
    if kind == "llamacpp":
        return localai.status().get("ctx")
    if kind == "lmstudio":
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{base_url(provider).removesuffix('/v1')}/api/v0/models/{model}")
            info = r.json()
            return info.get("loaded_context_length") or info.get("max_context_length")
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
    return None


def _raise_for(provider: str, status: int, body: bytes) -> None:
    text = body.decode("utf-8", "replace")[:1000]
    raise LLMError(f"{provider} respondeu HTTP {status}: {text}", status)


# Esforço -> raciocínio do modelo. Só mandamos quando o modelo entende, senão o servidor recusa.
EFFORT_LEVEL = {"baixo": "low", "medio": "medium", "alto": "high", "maximo": "high", "extremo": "high"}
REASONING_MODELS = re.compile(r"gpt-oss|gpt-5|^o[1-4](-|$)|deepseek-r|grok|magistral", re.I)


async def _reasoning(provider: str, model: str, effort: str | None, body: dict, messages: list[dict]) -> None:
    """Acrescenta o controle de raciocínio conforme o provider e o modelo."""
    level = EFFORT_LEVEL.get(effort or "")
    if not level:
        return
    kind = spec(provider)["type"]
    if kind == "ollama":
        caps = await capabilities(provider, model) or set()
        if "thinking" in caps:  # Ollama recusa `think` em modelo sem raciocínio
            body["think"] = level if REASONING_MODELS.search(model) else (effort != "baixo")
    elif REASONING_MODELS.search(model):
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


async def chat_stream(provider: str, model: str, messages: list[dict], tools: list[dict] | None,
                      num_ctx: int, effort: str | None = None) -> AsyncIterator[tuple[str, object]]:
    impl = _ollama_stream if spec(provider)["type"] == "ollama" else _openai_stream
    messages = list(messages)
    extra: dict = {}
    await _reasoning(provider, model, effort, extra, messages)
    _inference(provider, model, extra)  # o ajuste do modelo vale mais que o esforço da conversa
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
    calls: dict[int, dict] = {}
    prompt_tokens = completion_tokens = None
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers(provider)) as c:
        async with c.stream("POST", f"{base_url(provider)}/chat/completions", json=body) as r:
            if r.status_code >= 400:
                _raise_for(provider, r.status_code, await r.aread())
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
    out = []
    for acc in calls.values():
        try:
            args = json.loads(acc["arguments"] or "{}", strict=False)
        except json.JSONDecodeError:
            args = {"__raw__": acc["arguments"]}
        out.append({"id": acc["id"] or _new_id(), "name": acc["name"], "arguments": args})
    yield "done", {"tool_calls": out, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}


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


def _to_ollama(messages: list[dict]) -> list[dict]:
    names: dict[str, str] = {}
    out = []
    for m in messages:
        m = dict(m)
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
                _raise_for(provider, r.status_code, await r.aread())
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


# ------------------------------------------------------------------ capacidades do modelo

_CAPS: dict[tuple[str, str], set[str]] = {}  # cache só de respostas positivas (None = tentar de novo)


async def capabilities(provider: str, model: str) -> set[str] | None:
    """{"vision", ...} declarado pelo provider; None = provider não informa (vale o override do usuário).

    Ollama: POST /api/show devolve `capabilities` (desde ~0.6.5; ausente = desconhecido).
    LM Studio: GET /api/v0/models/{id} devolve `type` in llm | vlm | embeddings.
    """
    key = (provider, model)
    if key in _CAPS:
        return _CAPS[key]
    kind, host = spec(provider)["type"], base_url(provider).removesuffix("/v1")
    caps = None
    try:
        async with httpx.AsyncClient(timeout=5, headers=headers(provider)) as c:
            if kind == "ollama":
                j = (await c.post(f"{host}/api/show", json={"model": model})).json()
                caps = set(j["capabilities"]) if isinstance(j.get("capabilities"), list) else None
            elif kind == "lmstudio":
                t = (await c.get(f"{host}/api/v0/models/{model}")).json().get("type")
                caps = None if not t else ({"vision"} if t == "vlm" else set())
            elif kind == "llamacpp":
                # Quem carregou o modelo fomos nós: visão = tem mmproj.
                caps = {"vision"} if localai.status().get("vision") else set()
    except (httpx.HTTPError, ValueError, AttributeError):
        caps = None
    if caps is not None:
        _CAPS[key] = caps
    return caps

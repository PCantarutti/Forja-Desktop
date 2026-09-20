"""Fallback de tool calling em texto + detectores de promessa sem ação e de loop."""
from __future__ import annotations

import json
import re
from typing import Iterable

THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.S)


def split_think(text: str) -> tuple[str, str]:
    """Separa raciocínio (<think>) do texto visível. Suporta </think> sem abertura (Qwen3 com template)."""
    if "</think>" in text and "<think>" not in text:
        think, _, rest = text.partition("</think>")
        return think.strip(), rest.strip()
    thinking = "\n".join(m.strip() for m in THINK_RE.findall(text))
    return thinking, THINK_RE.sub("", text).strip()


# ------------------------------------------------------------------ parser

def _loads(s: str):
    try:
        return json.loads(s.strip(), strict=False)  # strict=False aceita \n literal dentro de strings
    except (json.JSONDecodeError, ValueError):
        return None


def _from_obj(obj, names: set[str]) -> list[dict]:
    """Aceita {"name","arguments"}, {"function":{...}}, {"tool": ..., "parameters"} ou listas disso."""
    if isinstance(obj, list):
        return [c for o in obj for c in _from_obj(o, names)]
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("function"), dict):
        obj = obj["function"]
    name = obj.get("name") or obj.get("tool")
    args = next((obj[k] for k in ("arguments", "parameters", "args", "input") if k in obj), {})
    if isinstance(args, str):
        args = _loads(args) or {}
    if name in names and isinstance(args, dict):
        return [{"name": name, "arguments": args}]
    return []


def _xml_value(v: str) -> str:
    # Formato Qwen-coder coloca \n depois da abertura e antes do fechamento.
    if v.startswith("\n"):
        v = v[1:]
    if v.endswith("\n"):
        v = v[:-1]
    return v


FUNC_RE = re.compile(r"<function=([\w\-]+)>(.*?)(?:</function>|$)", re.S)
PARAM_EQ_RE = re.compile(r"<parameter=([\w\-]+)>(.*?)</parameter>", re.S)
TOOL_ATTR_RE = re.compile(r"<(?:tool|invoke|tool_use)\s+name=[\"']([\w\-]+)[\"']\s*>(.*?)</(?:tool|invoke|tool_use)>", re.S)
PARAM_ATTR_RE = re.compile(r"<(?:param|parameter|arg)\s+name=[\"']([\w\-]+)[\"']\s*>(.*?)</(?:param|parameter|arg)>", re.S)
CHILD_RE = re.compile(r"<([\w\-]+)>(.*?)</\1>", re.S)
TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)(?:</tool_call>|$)", re.S)
FENCE_RE = re.compile(r"```(?:json|tool_call|tool)?\s*\n(.*?)```", re.S)


def _parse_xml(text: str, names: set[str]) -> tuple[list[dict], list[tuple[int, int]]]:
    calls, spans = [], []
    for m in FUNC_RE.finditer(text):
        if m.group(1) in names:
            calls.append({"name": m.group(1), "arguments": {k: _xml_value(v) for k, v in PARAM_EQ_RE.findall(m.group(2))}})
            spans.append(m.span())
    for m in TOOL_ATTR_RE.finditer(text):
        if m.group(1) in names:
            calls.append({"name": m.group(1), "arguments": {k: _xml_value(v) for k, v in PARAM_ATTR_RE.findall(m.group(2))}})
            spans.append(m.span())
    if not calls:  # XML simples: <write_file><path>a</path><content>...</content></write_file>
        for name in names:
            for m in re.finditer(rf"<{name}>(.*?)</{name}>", text, re.S):
                calls.append({"name": name, "arguments": {k: _xml_value(v) for k, v in CHILD_RE.findall(m.group(1))}})
                spans.append(m.span())
    return calls, spans


def _cut(text: str, spans: Iterable[tuple[int, int]]) -> str:
    for a, b in sorted(spans, reverse=True):
        text = text[:a] + text[b:]
    return text.strip()


def parse_text_tool_calls(text: str, names: Iterable[str]) -> tuple[list[dict], str]:
    """Extrai chamadas de ferramenta emitidas como texto. Retorna (calls, texto_sem_as_chamadas).

    Formatos: <tool_call>{json}</tool_call> (Qwen/Hermes), bloco ```json {...}```, e XML
    (<function=x><parameter=k>, <tool name="x"><param name="k">, ou <x><k>v</k></x>).
    Só aceita nomes de ferramentas conhecidas, para não confundir JSON comum com chamada.
    """
    names = set(names)
    _, text = split_think(text)

    calls, spans = [], []
    for m in TOOL_CALL_RE.finditer(text):
        inner = m.group(1)
        found = _from_obj(_loads(inner), names) or _parse_xml(inner, names)[0]
        if found:
            calls += found
            spans.append(m.span())
    if calls:
        return calls, _cut(text, spans)

    for m in FENCE_RE.finditer(text):
        found = _from_obj(_loads(m.group(1)), names)
        if found:
            calls += found
            spans.append(m.span())
    if calls:
        return calls, _cut(text, spans)

    # JSON solto: a resposta inteira é um objeto de chamada.
    found = _from_obj(_loads(text), names) if text.lstrip().startswith(("{", "[")) else []
    if found:
        return found, ""

    calls, spans = _parse_xml(text, names)
    return calls, _cut(text, spans)


# ------------------------------------------------------------------ promessa sem ação

_PT_VERBS = ("criar|escrever|editar|adicionar|corrigir|ajustar|atualizar|modificar|alterar|ler|listar|"
             "verificar|implementar|gerar|salvar|fazer|aplicar|substituir|remover|incluir")
_EN_VERBS = ("create|write|edit|add|fix|update|modify|change|read|list|check|implement|generate|save|"
             "make|apply|replace|remove|insert|open|look")
PROMISE_RE = re.compile(
    rf"(?:\b(?:vou|irei|vamos|deixa\s+eu|deixe[\s-]me|agora\s+vou)\s+(?:\w+\s+)?(?:{_PT_VERBS})\w*"
    rf"|\b(?:i'll|i\s+will|let\s+me|let's|i'm\s+going\s+to|i\s+am\s+going\s+to)\s+(?:\w+\s+)?(?:{_EN_VERBS})\b)",
    re.I)


def detect_promise(text: str) -> bool:
    """True se o texto anuncia uma ação ("vou criar", "I'll write"...). Ignora raciocínio e código."""
    _, visible = split_think(text)
    visible = re.sub(r"```.*?```", "", visible, flags=re.S)
    return bool(PROMISE_RE.search(visible.replace("’", "'")))


def looks_like_plan(text: str) -> bool:
    """True se o texto é um plano escrito na resposta em vez de ir em exit_plan_mode.

    Modelo fraco no modo Plano costuma redigir o plano e parar; o agente converte esse texto na
    chamada da ferramenta para o card de aprovação aparecer.
    """
    t = split_think(text)[1].strip()
    return len(t) >= 400 and bool(re.search(r"^\s*(?:\*\*)?#{1,4}\s|^\s*\d+[.)]\s", t, re.M))


# ------------------------------------------------------------------ loop

class LoopDetector:
    """Detecta a mesma chamada (nome + argumentos) repetida `limit` vezes seguidas."""

    def __init__(self, limit: int = 3):
        self.limit, self.last, self.count = limit, None, 0

    def record(self, name: str, args: dict) -> bool:
        key = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
        self.count = self.count + 1 if key == self.last else 1
        self.last = key
        return self.count >= self.limit

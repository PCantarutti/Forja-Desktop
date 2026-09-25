"""Configurações editáveis na UI.

Só o que o usuário muda é gravado (tabela app_settings); o resto continua vindo do .env.
`apply()` escreve os valores em `config`, então mudanças valem na próxima requisição, sem
reiniciar o container. Chaves de API nunca voltam para o frontend: só um "tem chave/final".
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from . import config, db, llm

ENV_DEFAULTS: dict[str, Any] = {
    "providers": [copy.deepcopy(p) for p in config.PROVIDERS.values()],
    "num_ctx": config.NUM_CTX,
    "max_iterations": config.MAX_ITERATIONS,
    "max_file_bytes": config.MAX_FILE_BYTES,
    "shell_timeout_max": config.SHELL_TIMEOUT_MAX,
    "compact_at": config.COMPACT_AT,
    "searxng_url": config.SEARXNG_URL,
    "disabled_tools": [],
    "custom_instructions": "",
    "auto_approve_tools": [],
    "auto_approve_commands": [],
    "trusted_hooks": [],  # pastas onde .forja/hooks.json pode rodar
    "personal_memory": config.PERSONAL_MEMORY,
    "project_memory": True,
    "project_memory_file": "FORJA.md",
    "browser_idle_minutes": config.BROWSER_IDLE_MINUTES,
    "browser_scale": config.BROWSER_SCALE,
    "browser_stream": config.BROWSER_STREAM,
    "enabled_models": {},
    "subagents": {"rapido": {"provider": "", "model": ""}, "capaz": {"provider": "", "model": ""},
                  "nuvem": {"provider": "", "model": ""}},
    "subagent_max_iterations": 15,
    # Maestro
    "maestro_max_iterations": config.MAESTRO_MAX_ITERATIONS,
    "maestro_max_attempts": config.MAESTRO_MAX_ATTEMPTS,
    "max_workers": config.MAX_WORKERS,
    "model_lifecycle": config.MODEL_LIFECYCLE,
    "maestro_model": {"provider": "", "model": ""},
    "maestro_visual": {"provider": "", "model": ""},
    "maestro_browser": True,
    "auto_review": False,  # modo Automático: o modelo revisa o risco da ação em vez de perguntar
    "workers_do_maestro": False,
    "worker_especialidades": [dict(e) for e in config.ESPECIALIDADES_PADRAO],
    "workspace_padrao": "",  # pasta de uma conversa nova de Agente/Maestro; vazio = escolher a cada conversa
    # Sandbox dos processos do agente (sandbox.py): -1 = automático, 0 = sem limite
    "sandbox_memoria_mb": config.SANDBOX_MEMORIA_MB,
    "sandbox_processos": config.SANDBOX_PROCESSOS,
    "sandbox_cpu": config.SANDBOX_CPU,
    "sandbox_isolado": config.SANDBOX_ISOLADO,
    "mcp_servidor": config.MCP_SERVIDOR,
    "mcp_permissao": config.MCP_PERMISSAO,
    "sandbox_motor": config.SANDBOX_MOTOR,
    "sandbox_wsl_distro": config.SANDBOX_WSL_DISTRO,
}
MAX_ESPECIALIDADES = 12

LISTS = ("disabled_tools", "auto_approve_tools", "auto_approve_commands", "trusted_hooks")

NUMBERS = {  # chave: (tipo, mínimo, máximo)
    "num_ctx": (int, 1024, 4_194_304),
    "max_iterations": (int, 1, 200),
    "max_file_bytes": (int, 1_000, 200_000_000),
    "shell_timeout_max": (int, 5, 3_600),
    "compact_at": (float, 0.3, 0.95),
    "browser_idle_minutes": (int, 0, 1_440),
    "browser_scale": (int, 1, 3),
    "maestro_max_iterations": (int, 10, 5_000),
    "maestro_max_attempts": (int, 1, 10),
    # Teto baixo de propósito: cada Worker é uma inferência inteira, e no local só cabe um.
    "max_workers": (int, 1, 8),
    "subagent_max_iterations": (int, 1, 100),
    "sandbox_memoria_mb": (int, -1, 262_144),  # -1 automático, 0 sem limite
    "sandbox_processos": (int, 0, 10_000),
    "sandbox_cpu": (int, 0, 100),
}
TYPES = ("ollama", "lmstudio", "openai", "llamacpp")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")


class SettingsError(ValueError):
    """Valor inválido vindo da UI."""


def _especialidades(raw, provedores: set[str]) -> list[dict]:
    """Lista de Workers especialistas. O id é o que a Maestro escreve em model_slot: não pode
    colidir com os níveis (rapido/capaz/nuvem) nem repetir."""
    if not isinstance(raw, list):
        raise SettingsError("'worker_especialidades' precisa ser uma lista.")
    if len(raw) > MAX_ESPECIALIDADES:
        raise SettingsError(f"No máximo {MAX_ESPECIALIDADES} especialidades.")
    out, vistos = [], set()
    for e in raw:
        e = e if isinstance(e, dict) else {}
        nome = str(e.get("nome") or "").strip()[:60]
        if not nome and not e.get("model"):
            continue  # linha que a pessoa adicionou e deixou em branco
        eid = str(e.get("id") or "").strip().lower() or re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")[:30]
        if not nome or not ID_RE.match(eid or "-"):
            raise SettingsError("Toda especialidade precisa de um nome (e de um id em letras minúsculas).")
        if eid in ("rapido", "capaz", "nuvem") or eid in vistos:
            raise SettingsError(f"Especialidade '{eid}' repetida ou com o nome de um nível (rápido/capaz/nuvem).")
        provider, model = str(e.get("provider") or ""), str(e.get("model") or "")
        if provider and provider not in provedores:
            raise SettingsError(f"Especialidade '{nome}': provedor '{provider}' não existe.")
        vistos.add(eid)
        out.append({"id": eid, "nome": nome, "quando": str(e.get("quando") or "").strip()[:200],
                    "provider": provider, "model": model})
    return out


def load() -> dict:
    values = copy.deepcopy(ENV_DEFAULTS)
    with db.session() as s:
        for row in s.query(db.AppSetting).all():
            values[row.key] = row.value
    # Slot de subagente novo chegando em banco antigo: a linha salva substitui a chave inteira, e sem
    # os slots que faltam a tela de Subagentes quebra ao ler o que não existe.
    values["subagents"] = {**ENV_DEFAULTS["subagents"], **(values["subagents"] or {})}
    return values


def apply(values: dict | None = None) -> dict:
    values = values or load()
    config.PROVIDERS = {p["id"]: p for p in values["providers"]}
    # O provedor local não é editável na tela de Provedores: ele existe sempre que o app existe.
    config.PROVIDERS.setdefault("local", copy.deepcopy(config.LOCAL_PROVIDER))
    config.NUM_CTX = int(values["num_ctx"])
    config.MAX_ITERATIONS = int(values["max_iterations"])
    config.MAX_FILE_BYTES = int(values["max_file_bytes"])
    config.SHELL_TIMEOUT_MAX = int(values["shell_timeout_max"])
    config.COMPACT_AT = float(values["compact_at"])
    config.SEARXNG_URL = values["searxng_url"]
    config.DISABLED_TOOLS = set(values["disabled_tools"])
    config.CUSTOM_INSTRUCTIONS = values["custom_instructions"]
    config.AUTO_APPROVE_TOOLS = list(values["auto_approve_tools"])
    config.AUTO_APPROVE_COMMANDS = list(values["auto_approve_commands"])
    config.TRUSTED_HOOKS = list(values["trusted_hooks"])
    config.PERSONAL_MEMORY = bool(values["personal_memory"])
    config.PROJECT_MEMORY = bool(values["project_memory"])
    config.PROJECT_MEMORY_FILE = values["project_memory_file"]
    config.ENABLED_MODELS = dict(values["enabled_models"])
    config.SUBAGENTS = dict(values["subagents"])
    config.SUBAGENT_MAX_ITERATIONS = int(values["subagent_max_iterations"])
    config.MAESTRO_MAX_ITERATIONS = int(values["maestro_max_iterations"])
    config.MAESTRO_MAX_ATTEMPTS = int(values["maestro_max_attempts"])
    config.MAX_WORKERS = int(values["max_workers"])
    config.MODEL_LIFECYCLE = values["model_lifecycle"]
    config.MAESTRO_MODEL = dict(values["maestro_model"])
    config.MAESTRO_VISUAL = dict(values["maestro_visual"])
    config.WORKER_ESPECIALIDADES = [dict(e) for e in values["worker_especialidades"]]
    config.MAESTRO_BROWSER = bool(values["maestro_browser"])
    config.AUTO_REVIEW = bool(values["auto_review"])
    config.WORKERS_DO_MAESTRO = bool(values["workers_do_maestro"])
    config.BROWSER_IDLE_MINUTES = int(values["browser_idle_minutes"])
    config.BROWSER_SCALE = int(values["browser_scale"])
    config.BROWSER_STREAM = values["browser_stream"]
    config.WORKSPACE_PADRAO = values["workspace_padrao"] or None
    config.SANDBOX_MEMORIA_MB = int(values["sandbox_memoria_mb"])
    config.SANDBOX_PROCESSOS = int(values["sandbox_processos"])
    config.SANDBOX_CPU = int(values["sandbox_cpu"])
    config.SANDBOX_ISOLADO = values["sandbox_isolado"]
    config.MCP_SERVIDOR = bool(values["mcp_servidor"])
    config.MCP_PERMISSAO = values["mcp_permissao"]
    config.SANDBOX_MOTOR = values["sandbox_motor"]
    config.SANDBOX_WSL_DISTRO = values["sandbox_wsl_distro"]
    return values


def public(values: dict | None = None) -> dict:
    """Mesmos valores, sem as chaves de API."""
    values = copy.deepcopy(values or load())
    for p in values["providers"]:
        key = p.pop("api_key", "") or ""
        p["has_api_key"] = bool(key)
        p["api_key_hint"] = f"…{key[-4:]}" if key else ""
    values["capacidades"] = {t: llm.indisponiveis(t) for t in llm.CAPACIDADES}  # a tela de Provedores mostra
    return values


# ------------------------------------------------------------------ validação

def _providers(new: list, old: list) -> list:
    if not isinstance(new, list) or not new:
        raise SettingsError("Defina pelo menos um provedor.")
    previous = {p["id"]: p for p in old}
    out, seen = [], set()
    for p in new:
        pid = str(p.get("id", "")).strip().lower()
        if not ID_RE.match(pid):
            raise SettingsError(f"Id inválido: '{pid}'. Use letras minúsculas, números, '-' ou '_'.")
        if pid in seen:
            raise SettingsError(f"Id repetido: '{pid}'.")
        seen.add(pid)
        url = str(p.get("url", "")).strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise SettingsError(f"URL inválida em '{pid}': precisa começar com http:// ou https://")
        if p.get("type") not in TYPES:
            raise SettingsError(f"Tipo inválido em '{pid}': use {', '.join(TYPES)}.")
        # api_key ausente = mantém a atual; "" = remove.
        key = p.get("api_key")
        if key is None:
            key = previous.get(pid, {}).get("api_key", "")
        janela = p.get("context_window")
        if janela in ("", None, 0):
            janela = None
        else:
            try:
                janela = int(janela)
            except (TypeError, ValueError):
                raise SettingsError(f"Janela de contexto de '{pid}' precisa ser um número de tokens.") from None
            if not 1024 <= janela <= 4_194_304:
                raise SettingsError(f"Janela de contexto de '{pid}' fora do intervalo 1024–4194304.")
        out.append({"id": pid, "name": str(p.get("name") or pid)[:60], "type": p["type"], "url": url,
                    "api_key": str(key), **({"context_window": janela} if janela else {})})
    return out


def validate(patch: dict, current: dict) -> dict:
    values = copy.deepcopy(current)
    for key, raw in patch.items():
        if key not in ENV_DEFAULTS:
            raise SettingsError(f"Configuração desconhecida: '{key}'.")
        if key == "providers":
            values[key] = _providers(raw, current["providers"])
        elif key in NUMBERS:
            cast, lo, hi = NUMBERS[key]
            try:
                v = cast(raw)
            except (TypeError, ValueError):
                raise SettingsError(f"'{key}' precisa ser um número.") from None
            if not lo <= v <= hi:
                raise SettingsError(f"'{key}' deve ficar entre {lo} e {hi}.")
            values[key] = v
        elif key in LISTS:
            if not isinstance(raw, list):
                raise SettingsError(f"'{key}' precisa ser uma lista.")
            values[key] = sorted({str(x).strip() for x in raw if str(x).strip()})
        elif key == "enabled_models":
            if not isinstance(raw, dict):
                raise SettingsError("'enabled_models' precisa ser um objeto {provedor: [modelos]}.")
            # null = "todos" para aquele provedor (remove a restrição)
            values[key] = {str(k): sorted({str(m) for m in v}) for k, v in raw.items() if v is not None}
        elif key == "subagents":
            if not isinstance(raw, dict):
                raise SettingsError("'subagents' precisa ser um objeto.")
            out = {}
            for slot in ("rapido", "capaz", "nuvem"):
                spec = raw.get(slot) or {}
                provider, model = str(spec.get("provider") or ""), str(spec.get("model") or "")
                if provider and provider not in {p["id"] for p in values["providers"]} | {"local"}:
                    raise SettingsError(f"Subagente '{slot}': provedor '{provider}' não existe.")
                out[slot] = {"provider": provider, "model": model}
            values[key] = out
        elif key == "worker_especialidades":
            values[key] = _especialidades(raw, {p["id"] for p in values["providers"]} | {"local"})
        elif key in ("maestro_model", "maestro_visual"):
            if not isinstance(raw, dict):
                raise SettingsError(f"'{key}' precisa ser um objeto {{provider, model}}.")
            provider, model = str(raw.get("provider") or ""), str(raw.get("model") or "")
            if provider and provider not in {p["id"] for p in values["providers"]} | {"local"}:
                raise SettingsError(f"Modelo da Maestro: provedor '{provider}' não existe.")
            values[key] = {"provider": provider, "model": model}
        elif key in ("project_memory", "personal_memory", "maestro_browser", "workers_do_maestro", "auto_review",
                     "mcp_servidor"):
            values[key] = bool(raw)
        elif key == "project_memory_file":
            name = str(raw).strip() or "FORJA.md"
            if "/" in name or "\\" in name or name.startswith("."):
                raise SettingsError("O arquivo de memória deve ser um nome simples na raiz da pasta de trabalho.")
            values[key] = name
        elif key == "model_lifecycle":
            from .modelctl import LIFECYCLES
            if raw not in LIFECYCLES:
                raise SettingsError(f"model_lifecycle deve ser um de: {', '.join(LIFECYCLES)}.")
            values[key] = raw
        elif key == "sandbox_motor":
            from .sandbox import MOTORES
            if raw not in MOTORES:
                raise SettingsError(f"sandbox_motor deve ser um de: {', '.join(MOTORES)}.")
            values[key] = raw
        elif key == "sandbox_wsl_distro":
            nome = str(raw or "").strip()
            if nome and not all(c.isalnum() or c in "-_." for c in nome):
                raise SettingsError("sandbox_wsl_distro: só o nome da distro (ex.: Ubuntu).")
            values[key] = nome
        elif key == "mcp_permissao":
            from .policy import MODES
            if raw not in MODES or raw == "plan":
                raise SettingsError(f"mcp_permissao deve ser um de: {', '.join(m for m in MODES if m != 'plan')}.")
            values[key] = raw
        elif key == "sandbox_isolado":
            from .sandbox import MODOS_ISOLADO
            if raw not in MODOS_ISOLADO:
                raise SettingsError(f"sandbox_isolado deve ser um de: {', '.join(MODOS_ISOLADO)}.")
            values[key] = raw
        elif key == "browser_stream":
            if raw not in ("png", "jpeg"):
                raise SettingsError("browser_stream deve ser png ou jpeg.")
            values[key] = raw
        elif key == "workspace_padrao":
            from . import workspace
            pasta = str(raw or "").strip()
            if pasta:
                try:
                    workspace.resolve(pasta)
                except workspace.WorkspaceError as e:
                    raise SettingsError(str(e)) from None
                pasta = workspace.normalize(pasta)
            values[key] = pasta
        elif key == "searxng_url":
            url = str(raw).strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                raise SettingsError("URL do SearXNG precisa começar com http:// ou https://")
            values[key] = url
        else:
            values[key] = str(raw)[:20_000]
    return values


def update(patch: dict) -> dict:
    values = validate(patch, load())
    with db.session() as s:
        for key in patch:
            s.merge(db.AppSetting(key=key, value=values[key]))
        s.commit()
    apply(values)
    return public(values)


def reset(keys: list[str] | None = None) -> dict:
    with db.session() as s:
        q = s.query(db.AppSetting)
        if keys:
            q = q.filter(db.AppSetting.key.in_(keys))
        q.delete(synchronize_session=False)
        s.commit()
    return public(apply())


# ------------------------------------------------------------------ mcp.json

def read_mcp_config() -> str:
    if not config.MCP_CONFIG.exists():
        return json.dumps({"mcpServers": {}}, indent=2)
    return config.MCP_CONFIG.read_text(encoding="utf-8")


def write_mcp_config(text: str) -> None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SettingsError(f"JSON inválido: {e}") from None
    if not isinstance(data.get("mcpServers", {}), dict):
        raise SettingsError("'mcpServers' precisa ser um objeto.")
    try:
        config.MCP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        config.MCP_CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        raise SettingsError(f"Não consegui gravar {config.MCP_CONFIG}: {e}") from None

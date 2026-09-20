"""Configuração.

Os valores vêm do ambiente (o Electron injeta FORJA_DATA/FORJA_WEB/FORJA_PORT ao subir o backend) e
podem ser sobrescritos em tempo de execução pela tela de Configurações (settings.py aplica os
valores salvos no banco nestes atributos). Por isso todo módulo deve ler `config.X` na hora de usar,
nunca copiar o valor no import.
"""
import os
from pathlib import Path


def data_dir() -> Path:
    """Onde ficam banco, mcp.json e logs. O Electron passa app.getPath('userData')."""
    env = os.getenv("FORJA_DATA")
    if env:
        return Path(env)
    base = os.getenv("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / "Forja"


DATA_DIR = data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Pasta padrão (conversas sem pasta escolhida). Cada conversa escolhe a sua na interface.
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT") or Path.home() / "Forja")
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
DB_PATH = os.getenv("DB_PATH") or str(DATA_DIR / "forja.db")
MCP_CONFIG = Path(os.getenv("MCP_CONFIG") or DATA_DIR / "mcp.json")
_web = os.getenv("FORJA_WEB", "").strip()
WEB_DIR = Path(_web) if _web else None  # build da interface; None = só API (modo dev com Vite)

NUM_CTX = int(os.getenv("NUM_CTX", "32768"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "25"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", "1000000"))
SHELL_TIMEOUT_MAX = int(os.getenv("SHELL_TIMEOUT_MAX", "300"))
SEARXNG_URL = os.getenv("SEARXNG_URL", "")  # vazio = web_search usa o DuckDuckGo
COMPACT_AT = float(os.getenv("COMPACT_AT", "0.8"))  # fração da janela que dispara a compactação

# Navegador integrado
BROWSER_IDLE_MINUTES = int(os.getenv("BROWSER_IDLE_MINUTES", "30"))  # 0 = nunca fechar sessão ociosa
BROWSER_SCALE = int(os.getenv("BROWSER_SCALE", "2"))                  # render 1x..3x (vale ao (re)lançar o Chromium)
# Forja Desktop: endpoint CDP do Electron (http://127.0.0.1:PORTA). As abas viram WebContentsViews nativas na
# janela e o espelho (screencast) fica desligado. Vazio = Chromium headless + espelho (modo web/Docker).
BROWSER_CDP = os.getenv("FORJA_CDP", "").strip()
BROWSER_STREAM = os.getenv("BROWSER_STREAM", "jpeg")                  # jpeg (leve, padrão) | png (sem perda, 3-5x mais pesado)

# type: ollama (API nativa, aceita num_ctx) | lmstudio (OpenAI + janela do modelo carregado) | openai
PROVIDERS = {
    "ollama": {"id": "ollama", "name": "Ollama", "type": "ollama",
               "url": os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/v1"), "api_key": ""},
    "lmstudio": {"id": "lmstudio", "name": "LM Studio", "type": "lmstudio",
                 "url": os.getenv("LMSTUDIO_URL", "http://127.0.0.1:1234/v1"), "api_key": ""},
}

# IA local (llama.cpp + stable-diffusion.cpp rodando dentro do Forja)
LOCAL_PORT = int(os.getenv("FORJA_LOCAL_PORT", "8077"))   # porta do llama-server que o Forja sobe
LOCAL_CONFIG = Path(os.getenv("LOCAL_CONFIG") or DATA_DIR / "local.json")  # pastas e params por modelo
MODELS_DIR = Path(os.getenv("MODELS_DIR") or Path.home() / "Forja" / "modelos")
# Provedor fixo do modelo local. Reinjetado em settings.apply() para não sumir de quem já salvou provedores.
LOCAL_PROVIDER = {"id": "local", "name": "IA local (llama.cpp)", "type": "llamacpp",
                  "url": f"http://127.0.0.1:{LOCAL_PORT}/v1", "api_key": ""}

PERSONAL_MEMORY = True             # memória sobre o usuário (índice no prompt, corpo sob demanda)
PERSONAL_MEMORY_DIR = Path(os.getenv("PERSONAL_MEMORY_DIR") or DATA_DIR / "memoria")

DISABLED_TOOLS: set[str] = set()   # ferramentas desligadas na tela de Configurações
CUSTOM_INSTRUCTIONS = ""           # texto extra no fim do system prompt
AUTO_APPROVE_TOOLS: list[str] = []     # globs de nomes de ferramenta que dispensam aprovação
AUTO_APPROVE_COMMANDS: list[str] = []  # globs de comandos do run_command que dispensam aprovação
PROJECT_MEMORY = True                  # ler/oferecer o arquivo de memória do projeto
PROJECT_MEMORY_FILE = "FORJA.md"
ENABLED_MODELS: dict[str, list[str]] = {}  # provedor -> modelos visíveis nos chats (ausente = todos)
SUBAGENTS: dict[str, dict] = {}            # "rapido"/"capaz" -> {"provider", "model"}
SUBAGENT_MAX_ITERATIONS = 15

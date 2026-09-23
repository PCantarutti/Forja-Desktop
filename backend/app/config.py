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
# Documento de escritório entra pelo caminho do `documentos.py`, que extrai texto em vez de
# mandar o arquivo inteiro ao modelo: o teto pode ser bem mais folgado que o do texto puro.
MAX_DOC_BYTES = int(os.getenv("MAX_DOC_BYTES", "25000000"))
SHELL_TIMEOUT_MAX = int(os.getenv("SHELL_TIMEOUT_MAX", "300"))
SEARXNG_URL = os.getenv("SEARXNG_URL", "")  # vazio = web_search usa o DuckDuckGo
COMPACT_AT = float(os.getenv("COMPACT_AT", "0.8"))  # fração da janela que dispara a compactação
# Teto de raciocínio por esforço, em tokens de pensamento. Quem corta é o servidor: ao estourar ele
# fecha o <think> e o modelo responde na MESMA geração — uma requisição só, sem o raciocínio voltar
# como entrada e sem o Forja adivinhar quando interromper.
#
# Isto não é afinação: sem teto, o llama.cpp roda o sampler de reasoning com INT_MAX em modelo com
# tag de thinking, e a fase de pensamento fica ilimitada — trava não determinística e KV cache
# enchendo até cair para a RAM. `localai.argv()` passa o maior valor daqui como --reasoning-budget no
# launch, e cada requisição manda o do seu esforço.
REASONING_BUDGET = {
    "baixo": 0,        # nem pensa: é o esforço de ir direto ao ponto
    "medio": 1024,
    "alto": 2048,
    "maximo": 4096,
    "extremo": 1536,   # o maestro delega em vez de projetar: teto curto de propósito
}
# Afrouxa (>1) ou aperta (<1) todos os tetos acima, sem mexer em código.
REASONING_BUDGET_MULT = float(os.getenv("REASONING_BUDGET_MULT", "1.0"))

# Navegador integrado
BROWSER_IDLE_MINUTES = int(os.getenv("BROWSER_IDLE_MINUTES", "30"))  # 0 = nunca fechar sessão ociosa
BROWSER_SCALE = int(os.getenv("BROWSER_SCALE", "2"))                  # render 1x..3x (vale ao (re)lançar o Chromium)
# Forja Desktop: endpoint CDP do Electron (http://127.0.0.1:PORTA). As abas viram WebContentsViews nativas na
# janela e o espelho (screencast) fica desligado. Vazio = Chromium headless + espelho (modo web/Docker).
BROWSER_CDP = os.getenv("FORJA_CDP", "").strip()
# Token da API local, gerado pelo Electron a cada execução e exigido nas rotas /api (ver main.py).
# Vazio = sem exigência: é o caso do dev com Vite e o do repo Docker, onde o nginx é a fronteira.
API_TOKEN = os.getenv("FORJA_TOKEN", "").strip()

# Chromium do Playwright: no app empacotado quem aponta é o Electron, mas o backend também roda
# sozinho (é o que o README manda fazer para mexer na interface com hot reload) e aí ninguém aponta.
# Sem isto, gerar PDF e abrir o navegador integrado morrem com "Please run playwright install".
for _candidato in (Path(__file__).resolve().parents[2] / "resources" / "ms-playwright",
                   Path(__file__).resolve().parents[2] / "ms-playwright"):
    if _candidato.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_candidato))
        break

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
TRUSTED_HOOKS: list[str] = []          # pastas onde .forja/hooks.json tem permissão de rodar
PROJECT_MEMORY = True                  # ler/oferecer o arquivo de memória do projeto
PROJECT_MEMORY_FILE = "FORJA.md"
ENABLED_MODELS: dict[str, list[str]] = {}  # provedor -> modelos visíveis nos chats (ausente = todos)
SUBAGENTS: dict[str, dict] = {}            # "rapido"/"capaz" -> {"provider", "model"}
SUBAGENT_MAX_ITERATIONS = 15

# ------------------------------------------------------------------ Maestro
# A Maestro planeja, delega e verifica; os Workers implementam. O estado do projeto fica no SQLite
# (taskdb), nunca no contexto do modelo — é o que permite descarregar um modelo local e carregar
# outro entre tarefas sem perder o trabalho.
MAESTRO_MAX_ITERATIONS = int(os.getenv("MAESTRO_MAX_ITERATIONS", "500"))  # o freio real é max_attempts
MAESTRO_MAX_ATTEMPTS = int(os.getenv("MAESTRO_MAX_ATTEMPTS", "5"))        # tentativas por tarefa
MAX_WORKERS = 1                     # 1 = sequencial (Etapa 6 abre o paralelo)
MODEL_LIFECYCLE = "persistent"      # persistent | unload_after_task (Etapa 4)
# Janela mínima (por requisição) de um modelo LOCAL em cada papel. Abaixo disso a Maestro não cabe
# junto com o histórico e o Worker não cabe junto com o contrato e os arquivos — o servidor recusa o
# prompt no meio do trabalho. Modelo de nuvem fica de fora: a janela dele não é o usuário que escolhe.
MAESTRO_MIN_CTX = int(os.getenv("MAESTRO_MIN_CTX", "32768"))
WORKER_MIN_CTX = int(os.getenv("WORKER_MIN_CTX", "16384"))

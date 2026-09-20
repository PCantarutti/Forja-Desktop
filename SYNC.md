# Sincronizar Forja (Docker) e Forja Desktop

São dois repositórios com a mesma história até `5fc50e8`:

| Repo | Pasta | O que é |
|---|---|---|
| Forja (web/Docker) | `C:\Projetos\Forja\forja-web` | versão Docker (backend + searxng + nginx), remoto `origin` no GitHub |
| Forja Desktop | `C:\Projetos\Forja\forja-desktop` | versão nativa do Windows (Electron + NSIS) |

Cada um enxerga o outro como remoto (`docker` lá, `desktop` cá), então **toda mudança compartilhada viaja por `git cherry-pick`** — não por copiar arquivo.

```powershell
# no repo que vai receber
git fetch desktop          # ou: git fetch docker
git log --oneline desktop/main -5
git cherry-pick <sha>
```

Para isso funcionar, os caminhos são iguais nos dois (`backend/app/...`, `frontend/src/...`). Não renomeie pastas de um lado só.

## Regra prática

1. Implemente **primeiro no repo onde o pedido nasceu** e commite só aquela mudança (commit pequeno = cherry-pick limpo).
2. `git fetch` + `git cherry-pick` no outro.
3. Rode os testes do lado que recebeu: `cd backend; .venv\Scripts\python -m pytest`.

Mudança que só toca os arquivos "comuns" (a maioria) aplica sem conflito. Mudança que encosta nos arquivos da tabela abaixo vai conflitar — é esperado: resolva à mão, os dois lados fazem a mesma coisa de jeitos diferentes.

## Arquivos que divergem de propósito

| Arquivo | Docker | Desktop |
|---|---|---|
| `backend/app/native.py` | não existe | shell do sistema em processo |
| `backend/app/runner.py` | cliente HTTP do forja-runner | não existe |
| `backend/app/shell.py` | escolhe host (runner) ou container (bash) | só local, sem `target` |
| `backend/app/terminal.py` | idem | só local |
| `backend/app/workspace.py` | traduz `C:/...` ⇄ `/host/c` (`HOST_MOUNTS`) | identidade |
| `backend/app/config.py` | `/data`, `/config`, `host.docker.internal` | `%APPDATA%\Forja`, `127.0.0.1` |
| `backend/app/main.py` | `/api/picker/start`, `/api/runner`; nginx serve a UI | sem esses; FastAPI serve a UI (`FORJA_WEB`) |
| `backend/app/agent.py` | bloco *Ambiente* fala de runner/container | fala só da máquina do usuário |
| `backend/app/web.py` | `web_search` via SearXNG do compose | DuckDuckGo quando `SEARXNG_URL` está vazio |
| `backend/app/gitops.py` | `worktree` traduz caminho | caminho direto |
| `frontend/src/App.tsx` | `chooseFolder` usa o forja-picker | usa `window.forja.pickFolder` |
| `frontend/src/components/InfoPanel.tsx` | linha de status do runner | "Comandos em: <sistema>" |
| `frontend/src/components/ServersPanel.tsx` | `runner` na resposta de `/api/servers` | `environment` |
| `frontend/src/components/FolderPicker.tsx` | texto sobre `forja-picker.cmd` | sem esse texto |
| `frontend/src/types.ts` | `RunnerStatus`, `exec_target` | `environment` |
| raiz | `docker-compose.yml`, `*/Dockerfile`, `nginx.conf`, `searxng/`, `tools/` | `electron/`, `scripts/`, `package.json` |

Tudo o que não está nessa lista — agente, ferramentas, aprovações, checkpoints, subagentes, MCP, navegador integrado, Settings, Sidebar, MessageView — é igual e deve continuar igual.

## Quando um arquivo divergente precisar da mesma feature

Não force o cherry-pick: implemente nos dois e cite o outro commit na mensagem (`ver <sha> no repo Docker`). É mais barato que resolver conflito em código que nasceu diferente.

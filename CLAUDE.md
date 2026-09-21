# Forja Desktop — regras para o agente

## Validar sempre numa instância real do Forja Desktop

Feature nova ou correção de bug **não está pronta** enquanto não foi exercitada numa instância do app
Electron rodando (não só testes, não só o Vite no navegador). Muita coisa só existe no Electron:
navegador nativo (WebContentsView + CDP), token da API, Python portátil, `DevToolsActivePort`.

Receita que funciona (a mesma usada em 2026-09-21):

1. `cd frontend && npm run build` (o dev serve `frontend/dist`, não o Vite).
2. Subir **destacado do console** — em background pelo shell o app morre por SIGHUP (os handlers de
   sinal do `main.js` matam a árvore). Em PowerShell:
   ```powershell
   $env:FORJA_PORT = "8799"; $env:FORJA_WEB = "C:\Projetos\Forja\forja-desktop\frontend\dist"
   Start-Process node_modules\electron\dist\electron.exe -ArgumentList "." -WorkingDirectory . -RedirectStandardError electron.err.log -PassThru
   ```
   A porta CDP sai no `electron.err.log` (`DevTools listening on ws://127.0.0.1:PORTA/...`).
3. Dirigir a interface como um usuário pelo CDP do próprio Electron (Playwright `connect_over_cdp`):
   a página da UI é a que tem URL `http://127.0.0.1:8799`. O token da API vem de
   `page.evaluate("window.forja.token")`; toda chamada a `/api` precisa do header `x-forja-token`.
4. Conferir o resultado de duas formas: estado pela API **e** captura de tela da janela
   (`user32 GetWindowRect` + `CopyFromScreen`), porque a view nativa não aparece em screenshot da page.
5. Ler `%APPDATA%\Forja\logs\backend.log` no fim. Encerrar a instância (`taskkill /T /F` no PID do
   electron.exe) só quando o usuário não pediu para deixá-la aberta.

Coisas que já enganaram:

- Em dev, o backend deve rodar no Python portátil `resources/python` (o `main.js` já prefere). Um venv
  sobre Python da Microsoft Store virtualiza `%APPDATA%` e o SQLite acusa `database disk image is
  malformed` sem nenhum banco estar corrompido.
- `DevToolsActivePort` é escrito uma vez, antes do `ready`. Nunca apagar; validar a porta ao vivo.
- O texto “Aguardando a primeira tela…” no painel Navegador significa modo espelho (headless), ou seja,
  o modo nativo NÃO está ativo: `FORJA_CDP` não chegou ao backend.
- O userData de dev é o mesmo do app instalado (`%APPDATA%\Forja`). Não rodar os dois ao mesmo tempo
  escrevendo no mesmo `forja.db`.

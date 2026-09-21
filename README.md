# Forja Desktop

Ambiente de desenvolvimento pessoal com agente de IA **local**, num app de desktop do Windows. Um modelo rodando no seu PC (Ollama ou LM Studio) lê e escreve arquivos numa pasta de trabalho, sempre com aprovação visível e sem ferramentas escondidas.

É a versão instalável do [Forja](https://github.com/) — o mesmo produto, sem Docker: o backend roda nativo, os comandos rodam no seu PowerShell e a pasta de trabalho é a pasta de verdade, sem disco montado no meio.

- **Chat** e **Agente** em seções separadas (seletor no canto superior esquerdo, barra de conversas recolhível)
- **Modos de permissão** como no Claude: Automático, Manual, Aceitar edições, Plano e Ignorar permissões
- **Esforço** (Baixo a Extremo): controla o raciocínio do modelo e quantos passos o agente pode dar. No **Extremo** ele vira multi-modelo: delega a lógica difícil a um modelo mais forte, roda o comando de verificação e revisa o diff
- **Navegador integrado**: o agente abre rotas, clica, lê o DOM e tira screenshot; você assiste ao vivo na aba *Navegador* e pode interagir também
- Tool calling nativo, com fallback para chamadas escritas em texto (`<tool_call>`, blocos ```json e XML)
- Card de aprovação com diff antes de qualquer escrita
- Painel lateral com as ferramentas **realmente enviadas** ao modelo em cada requisição
- Detecção de "promessa sem ação" (lembra o modelo até 2x) e de loop (3 chamadas idênticas seguidas)
- Execuções continuam no servidor: recarregar a interface reconecta, inclusive com aprovação pendente
- Compactação automática do contexto quando a conversa fica grande
- Tokens, tempo e tokens/s de cada resposta
- Tela de Configurações: provedores e chaves de API (inclui Ollama Cloud), liga/desliga de ferramentas, permissões, MCP e memória da IA
- Janela como a do Claude Desktop: zoom com **Ctrl +**, **Ctrl −** e **Ctrl 0** (ou Ctrl + roda do mouse), tamanho e posição lembrados, e o X podendo minimizar para a bandeja em vez de fechar
- Memória do projeto em `FORJA.md`, anexos de arquivos e imagens, editar mensagem e regenerar resposta
- **Pasta de trabalho por conversa**, escolhida em qualquer lugar do disco pelo Explorer
- **Checkpoints**: desfazer as alterações de arquivo de um turno
- **Subagentes**: o agente delega subtarefas para um modelo *Rápido* ou *Capaz*, conforme a dificuldade

## Requisitos

- Windows 10/11 (x64)
- Um modelo que saiba usar ferramentas (testado com Qwen3.6-35B-A3B). Três caminhos: a **IA local** do próprio
  Forja (llama.cpp embutido, veja abaixo), o **Ollama** ou o **LM Studio** rodando no seu PC

Opcionais, cada um só para a parte que usa: **git** (aba Alterações), **GitHub CLI** (`gh`, botão Criar PR), **Node.js** e **uv** (servidores MCP via `npx`/`uvx`). O painel *Info* mostra o que ele encontrou no PATH, e o agente é avisado do que falta.

Não precisa de Docker, WSL2 nem Python instalado: o app traz o seu próprio Python e o Chromium do navegador integrado.

## Instalação

Baixe `Forja-Setup-<versão>.exe` e instale (instalação por usuário, sem pedir administrador). Abra o Forja pelo menu Iniciar.

No topo, escolha o provider e o modelo, selecione **Agente**, clique no chip da pasta para escolher onde trabalhar e peça, por exemplo: *"crie calc.py com funções soma e multiplicacao"*.

Seus dados (conversas, configurações, chaves e `mcp.json`) ficam em `%APPDATA%\Forja`. Desinstalar **não** apaga essa pasta.

## Ferramentas

| Ferramenta | O que faz | Aprovação |
|---|---|---|
| `list_dir`, `read_file` | Lê a pasta de trabalho | não |
| `write_file`, `edit_file` | Cria e edita arquivos (card com diff) | conforme o **modo de permissão** |
| `run_command` | Comando de shell na pasta da conversa, no seu PowerShell | **sempre**, mesmo com escrita automática |
| `serve_start`, `serve_status`, `serve_stop` | Servidor de desenvolvimento em segundo plano (log em arquivo) | `serve_start` **sempre**; `serve_stop` conforme **Escrita** |
| `web_search` | Busca na web pelo DuckDuckGo (sem chave, sem conta) | não |
| `fetch_url` | Baixa uma página e devolve o texto. Bloqueia endereços da rede local. | não |
| `browser_navigate`, `browser_read`, `browser_console` | Abre uma URL no navegador integrado, lê a página como árvore de acessibilidade (com refs `eN`) e o console | não |
| `browser_click`, `browser_type`, `browser_upload` | Clica / preenche um campo / anexa arquivo da pasta de trabalho a um input[type=file] (por ref ou seletor) | conforme o **modo de permissão** |
| `browser_tabs` | Lista, abre, troca ou fecha abas da sessão | não |
| `browser_eval` | Executa JavaScript na página | **sempre** |
| `browser_screenshot` | Screenshot da página: aparece no chat para você; vai ao modelo como imagem **só se ele tiver visão** | não |
| `image_generate` | Gera uma imagem com o stable-diffusion.cpp local; o PNG vai para a pasta de trabalho e aparece no chat | conforme o **modo de permissão** |
| `mcp__<servidor>__<tool>` | Ferramentas dos servidores MCP configurados | sim, a menos que o servidor marque a ferramenta como somente leitura (`readOnlyHint`) |

`run_command`, `serve_start` e o Terminal rodam **na sua máquina**, na pasta da conversa, com `pwsh` se existir, senão `powershell`. `npm install`, venvs e servidores ficam nativos, como se você tivesse digitado. Há timeout (padrão 60 s, teto `SHELL_TIMEOUT_MAX`) e, ao estourar, a árvore inteira de processos é encerrada.

**Segurança (igual ao Claude Desktop)**: as ferramentas de arquivo ficam presas à pasta da conversa, mas o shell enxerga o sistema inteiro. A proteção é a aprovação: ele sempre pede confirmação, exceto nos comandos que você liberou em *Permissões*. Evite regras largas (`*`) e leia o comando antes de aprovar.

## Trabalhando como no Claude Desktop

- **Alterações**: aba com os arquivos que o agente mudou nesta conversa (diff do antes para o agora, abrir no editor, revelar na pasta) e o **git** da pasta: branch, arquivos alterados com diff, **Commit** (o modelo escreve a mensagem, você edita e confirma), **Criar PR** (push + `gh pr create`) e **Worktree** (branch nova num worktree irmão; a conversa passa a trabalhar lá).
- **Terminal**: seu shell na pasta da conversa. Sem PTY: comandos comuns funcionam, programas de tela cheia não. Um shell por conversa, vivo enquanto o app estiver aberto.
- **Saída ao vivo**: `run_command` mostra o que o comando imprime enquanto roda, dentro do card.
- **Tarefas**: em trabalhos com vários passos o agente mantém uma lista (`update_tasks`) que aparece no chat com o que já foi feito.
- **Fila de mensagens**: enviar durante a execução não bloqueia: a mensagem entra na fila e o agente a recebe no próximo passo.
- **Notificações e não lidas**: com a janela fora de foco, o sistema avisa quando termina ou quando há aprovação pendente; conversas que terminaram em segundo plano ganham um ponto azul na lista.
- **Comandos `/`**: digite `/` no campo. Ações do Forja (`/compactar`, `/commit`, `/pr`, `/alteracoes`) e prompts prontos (`/revisar`, `/testar`, `/explicar`). Crie os seus em `.forja/skills/<nome>.md` na pasta da conversa (cabeçalho opcional `description:`; `$ARGUMENTS` recebe o que vier depois do comando).
- **Hooks**: `.forja/hooks.json` na pasta da conversa roda comandos depois de uma ferramenta, ex.: `{"post_tool": [{"tools": ["write_file", "edit_file"], "command": "npx prettier --write \"{path}\""}]}`. A saída é anexada ao resultado para o modelo ver.
- **Compactar agora**: botão `compactar` na linha de contexto (ou `/compactar`).
- **Colar imagem**: Ctrl+V com uma imagem no clipboard vira anexo.
- **Abrir no editor / revelar**: nos cards de arquivo e no chip da pasta (abre o VS Code se estiver instalado, senão o programa padrão).
- **Conversas**: menu `⋯` em cada uma: renomear, fixar no topo, arquivar, exportar em Markdown, apagar (confirmação inline). A busca da barra lateral procura também no conteúdo das mensagens.
- **Agrupadas por pasta**: na seção Agente, a barra lateral agrupa as conversas pela pasta de trabalho (grupos recolhíveis; o lápis no cabeçalho do grupo abre uma conversa nova naquela pasta). A busca desfaz o agrupamento.
- **Seleção múltipla**: botão *Selecionar* na barra lateral; marque conversas (ou uma pasta inteira pelo cabeçalho) e aplique arquivar, desarquivar, fixar, desafixar ou apagar em lote. Conversas em execução não são apagadas.

## Navegador integrado

Os botões **Info**, **Navegador**, **Terminal**, **Alterações**, **Instâncias** e **Planos** ficam sempre visíveis no topo direito do chat. Clicar abre o painel lateral naquela aba; clicar de novo recolhe. Planos lista o que o agente propôs nesta conversa no modo Plano, com status e atalho para o card no chat. Só a aba Navegador é redimensionável pela borda; as outras têm largura fixa (Planos é mais larga). O painel lembra, por conversa, se estava aberto e em qual aba; conversa nova começa recolhida.

No **Forja Desktop** o navegador é nativo, como o painel do Claude Desktop: cada aba é uma `WebContentsView` do próprio Electron desenhada dentro da janela, na área da aba **Navegador**. Você clica, digita e rola direto na página, sem espelho nem atraso. O agente controla as mesmas abas pelas ferramentas `browser_*`: o backend (Playwright) liga por CDP ao Chromium do Electron (`--remote-debugging-port` só em 127.0.0.1, sem `--remote-allow-origins`, então página web nenhuma consegue conectar). A aba Navegador abre sozinha na primeira chamada.

No modo **web/Docker** (sem Electron) vale o esquema antigo: um Chromium headless (Playwright, `chromium-headless-shell`) roda junto com o backend e a aba Navegador mostra um espelho ao vivo (screencast) por onde você interage.

- **Uma sessão por conversa**: cada chat tem o próprio navegador (com suas abas). Trocar de chat troca o que o painel mostra; a tela inicial usa um rascunho à parte. Apagar a conversa fecha a sessão.
- **Abas**: a página que abre popup vira aba nova; você troca/fecha na faixa de abas e o agente usa `browser_tabs`. Limite de 8 por sessão. As outras ferramentas agem na aba ativa.
- **Ociosidade**: sessão sem uso e sem ninguém assistindo fecha após *Navegador: fechar sessão ociosa* (Configurações › Geral, padrão 30 min; 0 = nunca).
- **Upload**: no Desktop a página abre o seletor de arquivo do Windows normalmente. No modo web aparece uma barra no painel para você escolher ou cancelar. O agente usa `browser_upload` com um arquivo da pasta de trabalho nos dois modos.
- **Você também pode usar**: barra de URL, voltar/avançar/recarregar e a própria página (clique, teclado, roda, colar). O agente vê o estado novo no próximo `browser_read`. Downloads são bloqueados; links para fora de http(s) também.
- **Tamanho**: a página tem sempre o tamanho da área visível da aba. Redimensione a coluna pela borda e ela acompanha, como numa janela de verdade. Modais da interface por cima do painel escondem a view enquanto estão abertos.
- **Qualidade (só modo web)**: o Chromium renderiza em 2x (*escala de renderização*, 1 a 3), mas o espelho nunca sai maior do que a sua tela mostra (segue o `devicePixelRatio` do painel). O formato padrão é JPEG (leve); PNG sem perda pesa 3 a 5 vezes mais e deixa a interface lenta. Ambos em Configurações › Geral. No Desktop não há espelho, então nada disso se aplica.
- **Screenshots** do agente aparecem grandes no chat, dentro do card da ferramenta; clique para ampliar (Esc fecha).
- **Endereços**: um servidor subido por `run_command` ou `serve_start` fica em `http://localhost:PORTA` — o mesmo endereço para você e para o navegador integrado. Só `http(s)`; `file:` e afins são bloqueados.
- **Visão**: `browser_screenshot` sempre funciona (o print aparece no chat para você), mas a imagem só entra no contexto do modelo se ele tiver visão; sem visão ele recebe um aviso e valida pelo `browser_read`. O Forja detecta no Ollama (`/api/show` → `capabilities`) e no LM Studio (`type: vlm`); para outros providers, ou para forçar, use **Visão do modelo** (auto/sim/não) no painel Info. A imagem entra no contexto como mensagem do usuário; só as 2 últimas ficam como imagem, as anteriores viram texto.
- **Permissões**: `browser_click`/`browser_type` seguem o modo de permissão e aceitam regras em *Permissões* (ex.: `browser_*`). `browser_eval` sempre pergunta. Conteúdo lido da página chega ao modelo marcado como dado não confiável.
- O perfil do navegador é limpo e some ao fechar a sessão. Não peça ao agente para entrar em contas pessoais.

## Seções: Chat e Agente

O seletor no canto superior esquerdo troca entre as duas seções, e cada uma lista só as suas conversas. O botão ao lado esconde e mostra a barra de conversas.

- **Chat**: conversa comum. Nenhuma ferramenta é enviada ao modelo e não há pasta de trabalho.
- **Agente**: ferramentas, pasta de trabalho, permissões e checkpoints.

O tipo é da conversa, não um interruptor: abrir uma conversa antiga leva você para a seção dela.

## Modos de permissão

No rodapé do campo de mensagem, no Agente. `Shift+Tab` alterna, e os números 1 a 5 escolhem com o menu aberto.

| Modo | O que passa sem perguntar |
|---|---|
| **Automático** | Edições de arquivo e ações na página (clique, digitar). Shell, JavaScript e MCP perguntam |
| **Manual** | Nada. Toda alteração mostra o card |
| **Aceitar edições** | Só `write_file` e `edit_file`. O resto pergunta |
| **Plano** | Nada é alterado: o agente só lê e propõe um plano |
| **Ignorar permissões** | Tudo, inclusive shell e JavaScript. Aparece um aviso fixo no rodapé |

As regras de *Configurações › Permissões* valem em todos os modos (menos Plano) e, como sempre, o bloco da ferramenta mostra o motivo de algo ter passado sem perguntar.

### Modo Plano

O agente recebe **só as ferramentas de leitura** mais duas, `ask_user` e `exit_plan_mode`, e o painel lateral mostra exatamente isso. Ele investiga (lendo os arquivos que o plano vai tocar; com `delegate_task` ligado, varre pastas grandes por subagente), apresenta o plano num card e espera: você **aprova escolhendo o modo de execução** (por padrão Aceitar edições) ou pede mudanças, e ele replaneja. Depois de aprovado, as ferramentas de escrita voltam e o Forja avisa na conversa qual modo passou a valer.

O plano segue o mesmo esqueleto do Claude Code, imposto pelo system prompt: **Contexto** (o que ele achou no código), **Abordagem** (a escolhida e as descartadas), **Passos** numerados com arquivo e mudança, **Verificação** (como provar que funcionou) e **Riscos e dúvidas**. Sem blocos de código.

- **Plano aprovado fica no contexto**: entra no fim do system prompt até o fim do trabalho e nos turnos seguintes da conversa, sobrevivendo à compactação. Outro plano aprovado o substitui.
- **`ask_user`** existe em todos os modos do agente: quando uma decisão muda o trabalho (duas abordagens válidas, requisito ambíguo), ele pergunta num card com 2 a 4 opções e campo para outra resposta, e espera. Não é aprovação de ferramenta: é escolha sua.
- **Espelho em Markdown**: no `.md` da conversa (pasta `conversas/`) o plano aparece inteiro, com o status, e cada pergunta com a resposta dada.

## Esforço

Baixo, Médio, Alto, Máximo ou Extremo, ao lado do modo. Mexe em três coisas:

- **Raciocínio do modelo**: `think` no Ollama (só em modelo que declara suporte), `reasoning_effort` nos modelos de raciocínio via API OpenAI (gpt-oss, gpt-5, o-series, deepseek-r) e o interruptor `/no_think` nos Qwen quando o esforço é baixo.
- **Passos**: multiplica o limite de iterações (Baixo 0,4× · Médio 1× · Alto 1,6× · Máximo 3× · Extremo 4× de `MAX_ITERATIONS`).
- **Instrução**: uma linha no system prompt pedindo mais objetividade ou mais verificação.

**Extremo (multi-modelo)** vira o papel do principal: com subagentes configurados, ele **não escreve a lógica difícil nem projeta a solução** — é maestro, não autor. Ele ainda raciocina — é o que faz o pedido sair bom — mas com **teto**: passou de ~6 mil caracteres de raciocínio sem começar a responder, o Forja corta e refaz a chamada com o pensamento desligado. Sem o teto um modelo local gasta minutos projetando exatamente o que ia delegar; sem raciocínio nenhum ele delega rápido, mas inventa o enunciado. O subagente não tem teto: ele recebe esforço máximo e pensa à vontade. Ele localiza os arquivos, delega com `files` e `done_when`, integra o que voltou e responde. Uma delegação sem contexto suficiente é recusada com um exemplo de chamada correta — é erro de ferramenta, o modelo refaz. Regra de prompt sozinha não segura modelo pequeno, então escrever um arquivo grande na mão também volta uma vez, com a instrução de delegar; se for mesmo trivial, repetir a chamada passa. Faz sentido quando o modelo principal é pequeno (e barato) e o *Capaz*/*Nuvem* é bem maior; com dois modelos do mesmo tamanho, você só espera duas vezes.

## Conversa: anexos, editar e regenerar

- **Anexos**: clipe no campo de mensagem ou arraste arquivos para o chat. Eles são salvos em `.forja/uploads/` **dentro da pasta de trabalho**, então o agente abre com `read_file`/`run_command` como qualquer arquivo. **Imagens** vão para o modelo como visão (formato OpenAI `image_url`; no Ollama, campo `images`). Imagem maior que 8 MB não é enviada como imagem.
- **Editar**: passe o mouse na sua mensagem → lápis. Ao reenviar, tudo o que veio depois dela é apagado e a resposta é refeita.
- **Regenerar**: botão ⟳ embaixo da última resposta. Apaga a resposta (incluindo as chamadas de ferramenta dela) e gera outra para a mesma mensagem, com o modelo selecionado agora.
- **Editar/regenerar e arquivos**: se o agente alterou arquivos nos turnos que vão ser apagados, o Forja pergunta se também desfaz essas alterações (veja *Checkpoints*).
- **Modelo**: o seletor fica no campo de mensagem (provedor à esquerda, modelos à direita, com busca) e vale para a próxima mensagem. O painel *Modelos nesta conversa* soma tokens e t/s por modelo.

## Pasta de trabalho por conversa

Como no Claude Desktop, cada conversa tem a sua pasta. Ela aparece no chip ao lado do título, no topo. Clique nele para abrir o **seletor de pasta do Explorer**. Na tela inicial, a pasta escolhida vale para a próxima conversa criada. Trocar a pasta de uma conversa existente vale a partir da próxima mensagem.

As ferramentas de arquivo (`read_file`, `write_file`, `edit_file`, `list_dir`), os anexos, o `FORJA.md` e o cwd do `run_command` usam a pasta da conversa, e caminhos fora dela são bloqueados.

## Checkpoints (desfazer alterações)

Antes da **primeira** alteração do agente em cada arquivo, dentro de um turno, o Forja guarda como o arquivo estava, ou registra que ele não existia. Isso vale para `write_file` e `edit_file`, inclusive quando quem altera é um subagente. Embaixo da resposta aparece **desfazer N arquivos**: o botão volta os arquivos ao estado de antes daquele turno, desfazendo também os turnos seguintes (dos mais novos para os mais antigos), para não deixar estados misturados.

Mudanças feitas por **`run_command`**, servidores MCP ou pelo navegador **não** são rastreadas. Arquivos maiores que `MAX_FILE_BYTES` também não. Para esses casos, use git na sua pasta.

## Cota do Ollama Cloud

Provedor apontando para `https://ollama.com` com chave de API mostra quanto da cota já foi consumida, lida do
`GET /api/usage` (endpoint **não documentado** da Ollama: se sair do ar, a barra simplesmente não aparece; nada quebra).
São frações de 0 a 1 por janela — o plano grátis devolve `monthly`, o pago `session` (~5 h) e `weekly` — mais a
contagem de requisições por modelo. Token não é exposto pela Ollama.

Aparece em três lugares, e só onde a nuvem está em jogo:

- **Configurações › Provedores**, embaixo da chave, com as requisições por modelo.
- **No cartão da delegação** (aba Instâncias), quando o subagente roda num provedor de nuvem — é ali que a cota queima
  sem você ver.
- **No anel de contexto**, ao abrir o popover, quando o modelo da nuvem está selecionado no seletor ou já respondeu
  nesta conversa.

A consulta é uma por minuto, compartilhada pelas três telas, e o anel só pergunta com o popover aberto.

## Subagentes

Em **Configurações › Subagentes**, escolha provedor e modelo para três níveis:

- **Rápido**: modelo menor, para tarefas simples e mecânicas (buscar, listar, resumir, edições óbvias).
- **Capaz**: modelo maior e mais lento, para raciocínio difícil (depurar, projetar, código complexo).
- **Nuvem**: rede de segurança. O modelo **nunca** escolhe este nível: ele entra quando o escolhido não roda agora nesta máquina ou falha (ex.: Ollama Cloud, em Configurações › Provedores).

Com pelo menos um nível configurado, o agente principal ganha a ferramenta `delegate_task(task, level, files, done_when)` e decide sozinho quando delegar e para qual nível. Em `files` vão os arquivos relevantes — o conteúdo segue junto com a tarefa, então o subagente começa sabendo em vez de gastar iterações procurando.

**`done_when`** é o comando que prova que ficou pronto (`pytest -q ...`, `npm test`, um lint). Ele roda **depois** que o subagente para, pelo caminho normal do `run_command`: card de aprovação, políticas e globs de auto-aprovação valem igual (`pytest*` em Configurações › Permissões evita o card a cada delegação). Quem verifica é o turno principal, não o subagente — o relatório dele é palavra dele, o exit code é medição. No esforço **Extremo**, quando **não há essa prova** — sem `done_when`, ou com ele reprovando — o diff dos arquivos que ele tocou vai para uma revisão barata no nível *Rápido*, e o parecer entra no relatório como conselho, nunca como veredito. Com a verificação passando a revisão fica calada: um revisor menor que o autor gera falso-positivo, e o exit code já respondeu.

**Quando um nível não roda**: um slot que aponta para o provedor local só vale se o modelo dele for justamente o que está carregado — o Forja sobe um `llama-server` por vez e o llama.cpp ignora o campo `model` do pedido, então pedir outro alias rodaria o modelo errado calado. Nesse caso a delegação cai para a *Nuvem*, e sem ela devolve um erro dizendo o porquê, para o principal fazer sozinho. Falha de conexão no meio também cai para o próximo nível — mas só se o subagente ainda não tiver mexido em arquivo nenhum. O subagente usa as mesmas ferramentas, aprovações, permissões e pasta de trabalho, mas não pode delegar de novo. Os passos dele aparecem **dentro do bloco da delegação**, inclusive os cards de aprovação, com modelo, tokens e tempo. Enquanto ele trabalha, a delegação também aparece na aba **Instâncias** — de qualquer conversa, com nível, modelo, tempo, passos e o que ele está fazendo agora, mais os botões de abrir a conversa e parar o turno. Só o relatório final volta para a conversa, o que economiza o contexto do agente principal. O limite de passos por subagente fica na mesma tela (padrão 15).

## Memória sobre você

O agente guarda o que você contar de duradouro — como gosta de trabalhar, seu hardware, decisões suas — com a
ferramenta `remember`. Cada memória é um `.md` em `%APPDATA%\Forja\memoria`, com nome, descrição de uma linha, tipo e
data; dá para ler, editar à mão ou apagar em Configurações › Memória.

O que entra no prompt é **só o índice**: uma linha por memória. O conteúdo o modelo lê com `recall` quando o assunto
aparece, e `forget` apaga o que ficou errado. Isso é de propósito: 20 memórias custam ~470 tokens de índice em vez
dos milhares que os corpos inteiros custariam — no modelo local, prompt é tempo de prefill.

O índice é **congelado no começo de cada turno**. Se ele mudasse no meio da conversa, o llama-server jogaria fora o
prompt já processado e a resposta seguinte reprocessaria o histórico inteiro; por isso o que o agente guardar agora
só aparece na conversa seguinte.

Desligue em Configurações › Memória se não quiser. Lembre que o índice vai no prompt de **todo provedor** que você
usar — inclusive os remotos.

## Memória do projeto (`FORJA.md`)

Um arquivo na raiz da pasta de trabalho que vai junto no system prompt de toda conversa (até 8.000 caracteres). O agente é instruído a atualizá-lo quando aprende algo duradouro do projeto — decisões, convenções, comandos — e você edita em **Configurações › Memória**, onde também dá para trocar o nome do arquivo ou parar de enviar ao modelo. Como é um arquivo comum, entra no git do seu projeto se você quiser.

É diferente da memória MCP (grafo de conhecimento): o `FORJA.md` é por projeto e legível; o grafo é geral e consultado pelo modelo sob demanda.

## Permissões (auto-aprovação)

Em **Configurações › Permissões**, regras com `*` dispensam o card de aprovação:

- **Comandos** (`run_command`): comparados com o comando inteiro. Ex.: `pytest*`, `git status`, `npm run build`.
- **Ferramentas**: comparadas com o nome. Ex.: `write_file`, `browser_*`, `mcp__memoria__*`.

O card de aprovação tem **Sempre permitir** com uma sugestão pronta (ex.: `ls*`, `git status*`, `browser_eval`): cria a regra e aprova na hora. Toda execução liberada por regra mostra, no bloco da ferramenta, qual regra liberou — não existe aprovação invisível. Evite regras largas como `*`.

## MCP

Edite em **Configurações › MCP** (com validação, salvar e reconectar). O arquivo fica em `%APPDATA%\Forja\mcp.json`, no mesmo formato do Claude Desktop:

```json
{
  "mcpServers": {
    "memoria": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"] },
    "tempo":   { "command": "uvx", "args": ["mcp-server-time"] },
    "remoto":  { "url": "http://127.0.0.1:8931/mcp", "headers": { "Authorization": "Bearer ..." } },
    "desligado": { "command": "...", "disabled": true }
  }
}
```

- **stdio** (`command`): o processo roda no seu PC, então o comando precisa existir no PATH (`npx` vem do Node.js; `uvx`, do uv). Como é o seu sistema, um `.exe` do Windows também funciona.
- **HTTP** (`url`): streamable HTTP, com o endereço normal do seu PC.
- Depois de editar, clique em **recarregar** no painel *Servidores MCP*. Não precisa reiniciar o app.
- Servidor com erro não derruba o app. O erro aparece no painel.

## Configurações

Em **Configurações › Pastas** ficam os caminhos padrão: onde os modelos baixados caem, onde as imagens geradas são
salvas e (só para consultar) a pasta de dados do app. Mudar a pasta de modelos troca o destino padrão do download; as
outras pastas cadastradas no painel continuam sendo varridas.

Botão **Configurações** no rodapé da barra lateral. O que você muda ali fica no banco e vale na próxima requisição, sem reiniciar o app. "Restaurar padrões" apaga tudo o que foi salvo e volta aos padrões de fábrica.

| Aba | O que dá para fazer |
|---|---|
| **Aplicativo** | Zoom da janela, o que o X faz (fechar de verdade ou minimizar para a bandeja), abrir junto com o Windows (e direto na bandeja), versão e atalhos para a pasta de dados, o banco e as conversas em Markdown. Só aparece no app instalado — essas preferências ficam em `%APPDATA%\Forja\desktop.json`, fora do banco |
| **Geral** | Instruções personalizadas (vão no fim do system prompt, sempre), `num_ctx`, máximo de iterações, limite da compactação, tamanho máximo de arquivo, timeout do shell e URL de uma SearXNG própria (vazio = DuckDuckGo) |
| **Provedores** | Editar Ollama/LM Studio e **adicionar qualquer API compatível com OpenAI** (OpenRouter, OpenAI, Groq...) com chave. Botão *Testar conexão* lista os modelos |
| **Ferramentas** | Ligar/desligar cada ferramenta, nativa ou de MCP. O que está desligado não vai no `tools` nem é citado no prompt, e recusa ser chamado |
| **MCP** | Editar o `mcp.json` com validação, salvar e reconectar, e ver o status de cada servidor |
| **Memória** | Ver o grafo de conhecimento do servidor MCP de memória (entidades, observações, relações), buscar e apagar entidades |

**Chaves de API** ficam no SQLite (`%APPDATA%\Forja\forja.db`) e **nunca voltam para a interface**: a tela só mostra se existe chave e os 4 últimos caracteres. Como é uso pessoal na sua máquina, elas são gravadas sem criptografia; quem tiver acesso ao seu perfil do Windows lê o arquivo.

Para acrescentar uma configuração nova no futuro: adicione a chave em `ENV_DEFAULTS` (e a regra em `NUMBERS`, se for número) em `backend/app/settings.py`, aplique em `apply()` e mostre o campo na aba certa de `frontend/src/components/Settings.tsx`.

### Conversas em Markdown

Além do banco, cada conversa é espelhada num `.md` solto:

```
%APPDATA%\Forja\conversas\forja-code\0007 - Refatorar o build.md   (modo agente)
%APPDATA%\Forja\conversas\forja-chat\0008 - Duvida de SQL.md       (chat)
```

Serve para copiar entre máquinas, jogar num backup ou versionar no git — é o mesmo texto do botão
*Exportar*. O arquivo é regravado no fim de cada execução e quando a conversa é renomeada (o título
está no nome do arquivo, e o nome antigo é apagado em vez de virar duplicata). Apagar a conversa no
Forja apaga o `.md`; na subida, o app gera o que falta e varre `.md` de conversa que não existe mais.

O espelho é **só de leitura**: o `forja.db` continua sendo a fonte da verdade, então editar o `.md`
não muda nada dentro do app, e copiar um `.md` para a pasta de outro PC não importa a conversa.

### Memória da IA

A memória vem de um servidor MCP de grafo de conhecimento; qualquer servidor que exponha `read_graph` aparece na aba. O exemplo usa o `@modelcontextprotocol/server-memory` com `MEMORY_FILE_PATH` apontando para um arquivo seu (por exemplo dentro de `%APPDATA%\Forja`).

## Contexto longo: compactação

Antes de cada chamada, o Forja estima o tamanho do prompt. Se passar de `COMPACT_AT` (padrão 80%) da janela do modelo, as mensagens anteriores aos 2 últimos turnos viram um resumo escrito pelo próprio modelo. O resumo aparece na conversa como *Contexto compactado* e pode ser expandido. Nada é apagado do banco: o histórico completo continua visível. Só o que vai para o modelo encolhe.

## Apontando para Ollama ou LM Studio

**Ollama**: o Forja usa a API nativa `/api/chat` para enviar `options.num_ctx`. A camada `/v1` do Ollama ignora esse parâmetro, e é por isso que outros clientes ficam presos nos 4k de contexto. A lista de modelos vem de `/v1/models`. Como o Forja roda no seu PC, o endereço padrão `http://127.0.0.1:11434` funciona sem mexer em `OLLAMA_HOST` nem no firewall.

**Ollama Cloud**: em Configurações › Provedores, clique em **+ Ollama Cloud**, cole a chave criada em [ollama.com](https://ollama.com) → Settings → Keys e salve.

**LM Studio**: aba *Developer* → *Start Server* (porta 1234). A janela de contexto é a que você escolhe ao carregar o modelo. O painel lateral mostra o valor carregado.

**Quais modelos aparecem no seletor**: em cada provedor, **Modelos no seletor…** lista todos os disponíveis. Marque os que quer ver no chat e salve. Sem nenhuma marcação, todos aparecem. As mensagens saem do seu PC quando o provedor é remoto: não use para código que não pode ir para terceiros.

### Modo de tool calling por modelo

No painel lateral, em **Tool calling deste modelo**:

- `auto` (padrão): envia `tools` nativamente e também aceita chamadas escritas em texto. Se o servidor recusar `tools` (HTTP 400), passa sozinho para o modo texto e avisa na conversa.
- `native`: só tool calling nativo.
- `text`: não envia `tools`. O schema vai no system prompt e o modelo responde com `<tool_call>{...}</tool_call>`. Use com modelos sem suporte nativo.

## IA local (llama.cpp e stable-diffusion.cpp)

O painel **IA local** (ícone de chip, à direita) roda o modelo dentro do próprio Forja: sem Ollama, sem LM Studio, sem
servidor separado. Ele usa os binários oficiais do [llama.cpp](https://github.com/ggml-org/llama.cpp) (chat) e do
[stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) (imagem).

**Runtime sob demanda, e trocável.** Nada disso vem no instalador. Na primeira vez, o painel oferece o download:

| Backend | Tamanho | Quando usar |
|---|---|---|
| `vulkan` | ~30 MB | Padrão. Roda em NVIDIA, AMD e Intel |
| `cuda` | ~240 MB + ~370 MB do runtime da NVIDIA | Só NVIDIA; costuma ser o mais rápido |
| `cpu` | ~18 MB | Sem GPU |

Os binários ficam em `%APPDATA%\Forja\runtimes` e **convivem lado a lado**: dá para ter CPU, Vulkan e CUDA ao mesmo
tempo e trocar em **Configurações › Runtime** (ou no seletor do próprio painel) sem baixar nada de novo — útil para
comparar GPU e CPU, ou quando você troca a placa de vídeo. Sem escolha manual, vale o melhor instalado
(cuda › vulkan › cpu). A tela mostra a versão de cada um (`build 11064`) e atualiza pelo mesmo botão.

**Configurações › Hardware** mostra o processador, RAM e VRAM (total e livre) e as GPUs que o motor atual enxerga,
cada uma com um interruptor — desligar tira a placa do próximo carregamento (`--device` do llama.cpp). Ali também
ficam o padrão de cache KV na GPU, o "carregar o último modelo ao abrir" e as **proteções de carregamento**: o Forja
estima a memória antes de subir o modelo e, no nível *relaxado*, recusa o que não cabe nem somando RAM e VRAM; no
*rigoroso*, recusa também o que não couber na VRAM livre.

**Modelos.** A aba *Baixar* tem as pastas de modelos e o botão **Procurar modelos**, que abre a janela de busca do
Hugging Face. A busca sai sozinha quando você para de digitar (sem Enter) e tem ordenação: relevância — o ranking do
próprio HF para o termo —, mais downloads, mais curtidas ou atualizados recentemente. Em `imagem`, a lista traz só o
que o sd.cpp carrega: LoRA, ControlNet, embedding e repositório no formato diffusers ficam de fora, porque são
complemento ou peça solta, não modelo inteiro.

À esquerda ficam os resultados (nome, publicador, downloads, curtidas, data) e à direita a ficha do modelo —
downloads, curtidas, última atualização, PARAMS/ARCH/CTX/licença, **capacidades** (visão, ferramentas, raciocínio),
as **opções de download** e o README do modelo.

As opções vêm **do menor para o maior**, e o tamanho é colorido pelo que cabe na sua máquina — o Forja lê a VRAM do
`--list-devices` do llama.cpp e a RAM do sistema: verde cabe inteiro na GPU (com folga para contexto e buffers),
âmbar só carrega com parte das camadas na RAM (mais devagar), vermelho é maior que VRAM + RAM e não vai carregar. O
tooltip de cada um diz os números. As capacidades não são
chute: saem do template de chat e da arquitetura que o próprio Hugging Face expõe do gguf. O README vem sem as tags
HTML do card — a janela mostra markdown, e renderizar HTML de terceiros dentro do app não é uma boa ideia.

O download tem barra de progresso, **retoma de onde parou** se a rede cair ou o app fechar (o `.part` fica no disco
e a próxima tentativa continua dele), confere se há espaço antes de começar e leva junto as partes de um modelo
dividido (`00001-of-00003`). Para repositório restrito (gated), cole seu token do Hugging Face em
**Configurações › Pastas**. O campo
**Baixar para** escolhe em qual pasta o arquivo cai, e a escolha fica valendo para os próximos. Se você já baixou por fora, clique em
**Adicionar** e aponte a pasta: ela passa a ser varrida junto com a padrão (`~\Forja\modelos`), e a lista mostra em
qual pasta cada modelo está. A lixeira ao lado de um modelo **apaga o arquivo do disco** (com as outras partes, se for
dividido, e com os ajustes de carga dele) — pede confirmação, não vai para a lixeira do Windows e não dá para desfazer.
O modelo carregado não pode ser apagado: descarregue antes.

**Carregar.** Na aba *Modelos*, escolha um `.gguf`. Cada campo já vem com o padrão de verdade — os do `--help`
daquela build do llama.cpp (lote 2048, lote físico 512, checkpoints 32) e os que saem do próprio arquivo: todas as
camadas na GPU, os especialistas que o modelo usa e o contexto que ele suporta. O que você mudar fica **destacado em
azul com uma lixeira do lado** para voltar ao padrão, e só o que está fora do padrão é salvo. Cada rótulo tem um (?)
explicando o que a opção faz. Se houver um `mmproj-*.gguf` na pasta do modelo, ele entra sozinho no campo do projetor
e o modelo já carrega com visão.

A aba lista **modelos de chat e modelos de imagem em seções separadas** — quem é quem sai do cabeçalho do arquivo,
não da extensão (um `.gguf` pode ser os dois). Modelo de imagem não tem "Carregar", porque o sd.cpp sobe e desce a
cada geração; o que ele tem são **ajustes próprios** (passos, CFG, tamanho, amostrador, negativo padrão, VAE,
clip_l/t5xxl), que valem quando ele estiver escolhido na aba Imagem — o Flux não quer o mesmo CFG que o SD 1.5.

Em cima do formulário fica o **uso estimado de memória**, GPU e total, recalculado a cada ajuste: ele soma os pesos
que sobem para a GPU (respeitando os especialistas que ficam na CPU), o cache KV e os buffers de cálculo. O cache é
contado camada a camada, porque os modelos novos misturam tipos: atenção plena guarda o contexto inteiro, janela
deslizante (Gemma 4) guarda só os últimos N tokens com cabeças e dimensão próprias, e camada recorrente (Qwen3.6)
não usa cache nenhum. Num Qwen3.6-35B-A3B com 19 camadas na GPU, a estimativa deu 6,9 GB de VRAM contra 6,7 GB medidos no log
do llama.cpp.

Os parâmetros, traduzidos direto para a linha de comando do `llama-server`:

| Controle | Flag | Observação |
|---|---|---|
| Tamanho do contexto | `-c` | Quanto maior, mais memória |
| Camadas na GPU | `-ngl` | 999 = tudo que couber; diminua se faltar VRAM |
| Flash Attention | `-fa on\|off` | Precisa estar ligado para quantizar o cache KV |
| Cache K / V | `--cache-type-k/-v` | `q8_0` corta quase metade da memória do cache |
| Threads da CPU | `-t` | 0 = automático |
| Lote de avaliação / físico | `-b` / `-ub` | 0 = padrão do llama.cpp |
| Previsões simultâneas | `-np` | |
| Checkpoints de contexto | `--ctx-checkpoints` | |
| Camadas MoE na CPU | `--n-cpu-moe` | Para rodar MoE grande com pouca VRAM |
| Número de especialistas | `--override-kv` | Sobrescreve o valor do gguf; o prefixo sai da arquitetura lida do próprio arquivo (`qwen35moe.expert_used_count`) |
| Semente, RoPE base/escala | `--seed`, `--rope-freq-base/-scale` | 0 = automático |
| KV unificado / KV fora da GPU | `--kv-unified`, `--no-kv-offload` | |
| Manter na memória / Tentar mmap() | `--load-mode` (`mmap+mlock`, `mlock`, `none`) | mlock **sem** mmap faz o llama.cpp abortar se o modelo não couber de uma vez na RAM; na dúvida, deixe mmap ligado |
| Projetor multimodal | `--mmproj` | Aponte o `mmproj-*.gguf` e o modelo passa a enxergar imagens |

A lixeira no card do erro (e no log aberto) apaga o log do llama-server e esquece a falha; o X ao lado de um
download ou geração já terminado tira aquele item da lista.

Carregar um modelo **já troca o modelo do chat** para ele — quem carrega quer conversar com ele. E o llama.cpp
recebe `-fit on`: ele mesmo reduz o que não couber na memória, o que cobre a margem de erro da estimativa.

Enquanto o modelo sobe, uma barra no alto da janela mostra a porcentagem (e tem **cancelar**) (tempo decorrido sobre o estimado; o Forja
aprende a velocidade da sua máquina na primeira carga). Se falhar, o erro **fica na tela** com o fim do log do
llama-server ao lado de um "ver log" — não some no próximo refresh.

Antes de subir o servidor, o Forja lê o `--help` do binário e descarta a opção que aquela build não conhece — o
llama.cpp renomeia opção de tempos em tempos (o `--mlock`/`--no-mmap` virou `--load-mode`) e, com um nome que
ele não conhece, sai com código 1 e uma linha de erro.

Os ajustes ficam salvos **por modelo** em `%APPDATA%\Forja\local.json`. Campos em zero não viram flag: quem decide é
o llama.cpp.

Carregado, o modelo aparece no seletor do chat sob o provedor **IA local** (`llama-server` em `127.0.0.1:8077`, com
`--jinja` para o tool calling nativo sair do template do gguf). Um modelo por vez; carregar outro descarrega o anterior,
e fechar o Forja descarrega tudo.

**Inferência.** A aba *Inferência* é a amostragem do modelo, como a aba homônima do LM Studio: temperatura, top-k,
top-p, min-p, penalidade de repetição, limite da resposta, strings de parada, "pensar antes de responder"
(`enable_thinking` do template) e teto de raciocínio. O padrão de cada campo é o que o **próprio .gguf recomenda**
(`general.sampling.*`) e, na falta dele, o do llama.cpp — o Qwen3.6, por exemplo, já vem com temperatura 1,0 e
top-k 20. Como nos parâmetros de carga, só o que você muda fica salvo e destacado.

A aba vale para o modelo local carregado e, quando não há nenhum, para o **modelo escolhido no chat** — então dá
para ajustar a amostragem de um modelo do Ollama ou do LM Studio por ali também.

Esses ajustes ficam em `model_settings`, por modelo, então **valem em qualquer provedor**: o mesmo modelo servido pelo
Ollama ou pelo LM Studio usa os mesmos valores. O que é específico do llama.cpp (top-k, min-p, penalidade de
repetição) não é enviado para um provedor OpenAI genérico, que responderia HTTP 400.

Ficam de fora, de propósito: prompt de sistema, truncagem de contexto e saída estruturada — no Forja quem cuida disso
é o agente (Configurações › Instruções e a compactação automática).

**Imagem.** A aba *Imagem* gera na hora: modelo, prompt, negativo, passos, CFG, tamanho, amostrador e semente
(0 = aleatória). Embaixo do seletor, uma linha diz qual modelo está em uso, de qual pasta e com que tamanho — o
sd.cpp carrega o modelo a cada imagem e libera a memória no fim, então nada fica preso na GPU entre uma e outra.
O campo **Salvar imagens em** escolhe a pasta de saída (padrão `%APPDATA%\Forja\imagens`), e **Mostrar na pasta**
abre a imagem pronta no Explorer.

**Imagem e chat disputam a VRAM.** Com um modelo carregado, o Forja não gera a imagem escondido: ele avisa qual modelo
está na memória, explica que vai descarregá-lo e o que isso significa para uma conversa aberta — o llama-server guarda
o contexto já processado em cache, esse cache vai junto, a próxima mensagem reprocessa o histórico (primeira resposta
mais lenta) e uma resposta em andamento é cortada; o histórico em si não se perde. Só gera depois do **Descarregar e
gerar**. Sem modelo carregado, gera direto. Enquanto a imagem está sendo feita, carregar modelo fica bloqueado — pela
mesma razão. A ferramenta `image_generate` do agente nunca descarrega nada: a conversa pode estar rodando justamente
no modelo local. Os mesmos ajustes valem
para a ferramenta `image_generate`, então basta pedir *"gere uma imagem de uma raposa na neve"* na conversa; a imagem
do agente vai para a pasta de trabalho da conversa, não para a pasta do painel.

## Troubleshooting

**A janela não abre / fecha sozinha**
O app mostra o caminho do log. Ele fica em `%APPDATA%\Forja\logs\backend.log` e traz o erro do backend.

**"Não foi possível conectar em http://127.0.0.1:11434"**
O Ollama não está rodando (ícone na bandeja do sistema) ou está em outra porta. Ajuste em Configurações › Provedores.

**Modelo "esquece" as ferramentas ou responde fora de contexto**
Em geral o contexto está pequeno. No Ollama, aumente `num_ctx` em Configurações › Geral (32768 ou mais, se couber na VRAM). No LM Studio, recarregue o modelo com um *Context Length* maior. A linha **Contexto** acima do campo de mensagem mostra quanto está em uso.

**"Conexão interrompida... modelo descarregado"**
O LM Studio pode descarregar o modelo por TTL/JIT. Se a conexão cair antes do primeiro token, o Forja tenta de novo uma vez sozinho. Se cair no meio da resposta, mande a mensagem de novo.

**Servidor MCP em "error"**
Leia a mensagem no painel. Em servidores stdio, o comando precisa existir no PATH do seu PC (`npx` do Node.js, `uvx` do uv). Reabra o app depois de instalar algo novo no PATH.

**O modelo diz "vou criar o arquivo" e não cria**
O Forja manda até 2 lembretes automáticos (aparecem em azul na conversa). Se não resolver, troque o modo de tool calling do modelo para `text` no painel lateral.

## Desenvolvimento

Pré-requisitos: Node.js 22+ e Python 3.12+ do [python.org](https://www.python.org/downloads/) ou do uv. **Não use o Python da Microsoft Store**: ele roda em sandbox e o Windows redireciona as gravações em `%APPDATA%` para `LocalCache`, então o banco do app aparece noutro lugar (o backend avisa no log). O app empacotado traz o seu próprio Python e não tem esse problema.

```powershell
# backend
cd backend
py -3.12 -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest

# interface + app
cd ..\frontend; npm install; npm run build
cd ..; npm install; npm run dev
```

`npm run dev` sobe o Electron com o backend do venv e a interface de `frontend/dist`. Para mexer na interface com hot reload, rode o backend sozinho (`.venv\Scripts\python -m uvicorn app.main:app --port 8000` dentro de `backend/`) e, em outro terminal, `cd frontend; npx vite` — o Vite faz proxy de `/api` (mude o alvo com `$env:API_URL`).

### Gerar o instalador

```powershell
npm run dist
```

Se o electron-builder parar em `Cannot create symbolic link` ao extrair o `winCodeSign`, é o Windows recusando os symlinks do macOS que vêm nesse pacote. Ligue o **Modo de Desenvolvedor** (Configurações › Sistema › Para desenvolvedores) e rode de novo, ou extraia o pacote uma vez sem a parte do macOS:

```powershell
$cache = "$env:LOCALAPPDATA\electron-builder\Cache\winCodeSign"
node_modules\7zip-bin\win\x64\7za.exe x -y "-o$cache\winCodeSign-2.6.0" "$cache\winCodeSign-2.6.0.7z" "-x!darwin*"
```

`scripts/prepare.mjs` baixa o CPython portátil (python-build-standalone), instala as dependências, baixa o `chromium-headless-shell`, gera o ícone, builda a interface e copia o backend para `resources/`. Depois o electron-builder monta `dist/<versão>/Forja-Setup-<versão>.exe` (uma pasta por versão: mudou o `version` do `package.json`, a build vai para uma pasta nova e a anterior fica intacta) (~245 MB; ~900 MB instalado). Tudo em `resources/` e `build/` é gerado: pode apagar e rodar de novo.

**Sobrou um llama-server rodando**
Ao sair, o Electron mata a árvore de processos do backend e o modelo sai da memória junto. Se o backend levar um kill
seco (ou travar), o llama-server pode ficar de pé segurando a memória; na próxima vez que o Forja subir, ele encerra
esse processo órfão pelo pid que ficou anotado em `%APPDATA%\Forja\llama-server.pid`.

**A porta 8077 já está ocupada / o modelo local não carrega**
Outro `llama-server` está rodando (talvez de uma execução anterior travada). Feche-o, ou mude `FORJA_LOCAL_PORT`. Se o
carregamento falhar, o painel mostra o fim de `%APPDATA%\Forja\logs\llama-server.log`, que traz o erro do llama.cpp.

### Variáveis de ambiente

O app define o que precisa; elas existem para desenvolvimento e casos especiais.

| Variável | Padrão | Para que serve |
|---|---|---|
| `FORJA_DATA` | `%APPDATA%\Forja` | Onde ficam banco, `mcp.json` e logs |
| `FORJA_WEB` | `resources/web` | Build da interface servido pelo backend (vazio = só API, para o Vite) |
| `FORJA_PORT` | porta livre | Porta do backend em `127.0.0.1` |
| `WORKSPACE_ROOT` | `~/Forja` | Pasta **padrão** (conversas sem pasta escolhida) |
| `OLLAMA_URL` | `http://127.0.0.1:11434/v1` | Endpoint do Ollama |
| `LMSTUDIO_URL` | `http://127.0.0.1:1234/v1` | Endpoint do LM Studio |
| `NUM_CTX` | `32768` | Janela de contexto enviada ao Ollama |
| `MAX_ITERATIONS` | `25` | Máximo de passos do agente por mensagem |
| `MAX_FILE_BYTES` | `1000000` | Tamanho máximo de arquivo lido/escrito |
| `SHELL_TIMEOUT_MAX` | `300` | Teto em segundos do `run_command` |
| `COMPACT_AT` | `0.8` | Fração da janela que dispara a compactação |
| `SEARXNG_URL` | vazio | Instância SearXNG própria; vazio = DuckDuckGo |
| `BROWSER_IDLE_MINUTES` | `30` | Fecha a sessão do navegador ociosa (0 = nunca) |
| `BROWSER_SCALE` | `2` | Escala de renderização do Chromium (1 a 3) |
| `BROWSER_STREAM` | `jpeg` | Formato do espelho (modo web): `jpeg` (padrão, leve) ou `png` (sem perda, pesado) |
| `FORJA_LOCAL_PORT` | `8077` | Porta do `llama-server` da IA local |
| `MODELS_DIR` | `~/Forja/modelos` | Pasta padrão dos modelos locais (.gguf) |
| `LOCAL_CONFIG` | `%APPDATA%\Forja\local.json` | Pastas de modelo e parâmetros de carga de cada modelo |
| `FORJA_CDP` | vazio | Endpoint CDP do Electron (`http://127.0.0.1:PORTA`); o main.js define sozinho. Com ele, as abas são views nativas na janela e o espelho fica desligado |

### Estrutura

```
electron/
  main.js        sobe o backend numa porta livre, janela, ciclo de vida
  preload.js     window.forja.pickFolder (diálogo de pasta do sistema)
scripts/
  prepare.mjs    monta resources/ (Python portátil, deps, Chromium, UI, backend)
  make_icon.py   build/icon.png a partir do favicon
  dev.mjs        roda o app sem empacotar
backend/app/
  native.py      shell do sistema, árvore de processos, abrir no editor
  tools.py       registry de ferramentas + confinamento na pasta + ferramentas de arquivo
  shell.py       run_command e serve_*
  terminal.py    o shell da aba Terminal
  web.py         web_search (DuckDuckGo/SearXNG) e fetch_url
  localai.py     IA local: runtimes do llama.cpp/sd.cpp, modelos, Hugging Face e o llama-server
  imagegen.py    geração de imagem com o sd-cli + ferramenta image_generate
  downloads.py   downloads e trabalhos com progresso (runtime, modelo, imagem)
  browser.py     navegador integrado (Playwright): sessão, abas nativas via CDP do Electron ou headless + screencast, browser_*
  mcp_client.py  conexão com servidores MCP (stdio/HTTP) e registro das ferramentas
  parsing.py     parser de tool calls em texto, detector de promessa e de loop
  compact.py     compactação de contexto
  llm.py         cliente OpenAI-compatível (SSE) e Ollama nativo
  agent.py       loop do agente, execução em background (Run), aprovações
  settings.py    configurações editáveis na UI (banco + aplicação em runtime)
  policy.py      regras de auto-aprovação (Permissões)
  workspace.py   pasta de trabalho por conversa
  checkpoints.py desfazer alterações de arquivo por turno
  subagents.py   delegate_task e o loop do subagente
  uploads.py     anexos do chat (arquivos e imagens para visão)
  memory.py      leitura/limpeza da memória (via servidor MCP de grafo)
  main.py        rotas FastAPI + serviço da interface
frontend/src/    React + Tailwind (App, Sidebar, RightPanel, InfoPanel, BrowserPanel, MessageView)
```

Para adicionar uma ferramenta, registre um `Tool(name, description, parameters, handler, mutating, preview)` em `tools.py`. O loop, o painel e o card de aprovação passam a usá-la automaticamente.

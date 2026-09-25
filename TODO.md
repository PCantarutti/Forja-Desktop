# TODO: plano de entregas do agente e do Maestro

Estado em 2026-09-25. Este plano nasceu de uma revisão do código feita só por leitura, sem execução. As
notas atuais de cada sistema estão no fim do arquivo.

**Objetivo.** O Forja tem de levar um problema grande até o software pronto sem ninguém acompanhando,
mesmo com um modelo local pequeno (llama.cpp, janela de 8–16k):

```
problema grande → Maestro → ~20 tarefas pequenas → Worker (modelo pequeno)
→ teste automático → correção → integração → software completo
```

## Como usar este plano

- Cada **Entrega** é o tamanho de uma sessão de trabalho do Claude. Termina com os testes passando, uma
  validação numa instância real do app (receita no `CLAUDE.md`, sempre com `FORJA_DATA` próprio) e os
  commits.
- As tarefas de uma mesma entrega dependem umas das outras ou mexem nos mesmos arquivos, por isso andam
  juntas. Entregas diferentes só dependem das que aparecem em **Depende de**.
- Entregas sem dependência entre si podem ser feitas em qualquer ordem.
- Toda entrega, depois de validada no desktop, vai para o `forja-web` por cherry-pick (mesmo caminho
  `backend/app/`) e roda o `pytest` de lá.
- Os números de linha abaixo vieram da revisão e podem ter mudado. **Confirme cada problema no código
  antes de corrigir.** Se ele não existir, risque o item e anote o porquê.

## Mapa das dependências

```
E0 medição base (inclui cache perdido) ─────────────────────────┐
E1 integridade do Maestro ──┬── E2 integração (commit/regressão) ─┬── E7 paralelismo      (+ E4)
                            └── E3 execução (Worker/escalonador)  └── E8 revisor real      (+ E4)
E4 contexto + POLÍTICA DE EXECUÇÃO ──┬── E3 (leve: cache do Maestro durante o Worker)
  (base de tudo que chama o LLM)     ├── E7, E8
                                     └── E11 explorador só leitura (melhor depois da E5)
E5 tree / ast / imports           (independente) ── E6 busca: FTS5 + memória por projeto
E9 robustez de browser/terminal   (independente)
E10 métricas agregadas            (depende de E0; repete a medição no fim)
E12 sandbox: passos 1–2 independentes (fazer cedo) ── passo 3 (WSL/Docker) ── passo 4 (AppContainer)
    antes de rodar o Maestro autônomo (modo Automático/Ignorar permissões) por padrão
E13 backends: parte A (capacidades + janela real) ── ANTES da política de execução da E4
              parte B (cache/controle fora do llama.cpp, paridade) ── depois da E4
E14 preferências do projeto: parte 1 (detecção) independente; partes 2–4 depois de E1 e E8
E15 board de issues: parte A (MVP sem LLM) independente ── parte B (varredura com IA: E4, E5, E11, E14)
                     ── parte C (execução automática: E1, E2, E12)
E16 recuperação de loop/alucinação: parte A (placar + níveis 1–2 + filtro do raciocínio) independente
    ── parte B (nível 3: E4; nível 4: E2; juiz: E4) ── parte C (modo autônomo: combina com E12)
    ── parte D (comparar juízes: logprobs no modelo carregado × SemIf × Laya; depois de E16-B e E10)
E17 Claude como Maestro via MCP: independente para as ferramentas de tarefa; issue_* depende da E15-A
```

**Regra que vale para o plano inteiro:** nenhuma chamada auxiliar (Worker, explorador, revisor,
`visual_review`, título, resumo, compactação, embeddings) pode derrubar o cache do agente principal nem
trocar o modelo carregado por conta própria. Todas passam pela política de execução da E4. Uma entrega
que cria uma chamada nova ao LLM sem passar por ela não está pronta.

---

## E0: medição base (antes de mexer em qualquer coisa)

**Por quê:** sem um número de antes, não dá para saber se o resto do plano melhorou alguma coisa.
**Depende de:** nada.

- [ ] Criar um projeto de benchmark pequeno e fixo, versionado em `backend/tests/bench/` ou num repo à
      parte: por exemplo uma API de tarefas com CLI e testes, que dê ~10 tarefas no Maestro.
- [ ] Escrever um script (`scripts/bench_maestro.py`) que:
  - sobe o backend com `FORJA_DATA` próprio;
  - cria a conversa em modo Maestro com o pedido fixo;
  - espera terminar ou dar tempo esgotado;
  - salva um JSON com: tempo de relógio total, tempo em troca de modelo, tempo de geração, nº de
    tarefas, tentativas por tarefa, tarefas `needs_human`, se a suíte do projeto passa no fim, tokens
    de prompt e de completion, e taxa de cache;
  - **cache perdido do agente principal:** em cada volta do Maestro depois de um `run_task`, quantos
    tokens de prompt foram reprocessados (`prompt_n` − `cache_n` dos timings do llama-server,
    `llm.py:250`). É o número que mostra se a política da E4 funciona;
  - **restaurar vs reprocessar:** ver a validação V2 abaixo.
- [ ] Rodar em duas configurações:
  - (a) Maestro e Worker no **mesmo** modelo local;
  - (b) Maestro e Worker em modelos **diferentes**.

  Esta medição confirma ou derruba a suspeita de que a troca de modelo (`maestro.py:290`,
  `agent.py:1331`/`1619`) domina o tempo.
- [ ] Salvar os resultados em `docs/bench/2026-09-baseline.json`.

### Validações que decidem os padrões da E4 (cache em disco e KV)

Cada item tem método, critério e o que fazer se falhar. O resultado vai para
`docs/bench/2026-09-kvcache.json` e é citado na E4 antes de mudar qualquer padrão.

- [ ] **V1: save/restore de slot com `--kv-unified`.**
  - Método:
    1. subir o llama-server embutido com `--kv-unified --slot-save-path` e `-np` automático;
    2. mandar um prompt de ~10k tokens e salvar o slot;
    3. reiniciar o servidor e restaurar o slot;
    4. mandar o mesmo prompt mais uma frase.

    Repetir com `-np 1` sem unificado, como controle.
  - Passa se: o restore responde ok e `cache_n` da 2ª requisição ≥ 95% do prompt original.
  - Se falhar: o cache em disco fica desligado quando o KV é unificado (com o motivo no log e na tela),
    e o KV unificado continua como padrão.
- [ ] **V2: restaurar vs reprocessar.** Prompts de ~5k, ~10k e ~20k tokens: comparar o tempo de
      processar do zero com o de restaurar, e anotar o tamanho do `.bin`.
  - Passa se: restaurar custa ≤ 30% do tempo de reprocessar com 10k tokens.
  - Se falhar: o cache em disco fica desligado por padrão nesta classe de máquina e vira opção.
- [ ] **V3: ganho em modelo SWA/híbrido.** Repetir a V1 e a V2 com um modelo de janela deslizante
      (Gemma 3 ou gpt-oss) e, se couber na VRAM, um híbrido (Qwen3-Next).
  - Passa se: o ganho for ≥ 50% do ganho medido no modelo comum.
  - Se falhar: nesses modelos o cache em disco só salva e restaura o slot inteiro (sem prefixo
    parcial), e a tela avisa "ganho menor neste modelo".
- [ ] **V4: qualidade com KV `q8_0`.** Rodar o bench do Maestro (acima) e uma bateria do Comparar
      (`baterias.py`) com o modelo pequeno do bench, uma vez com o KV em `f16` e outra em `q8_0`.
  - Passa se: a taxa de sucesso das tarefas e a nota da bateria caem no máximo 2 pontos percentuais.
  - Se falhar: o padrão continua `f16`, e o `q8_0` aparece como recomendação na tela, com o ganho de
    VRAM calculado.
- [ ] **V5: troca de modelo com cache em disco.** Com Maestro e Worker em modelos diferentes, medir o
      tempo até a 1ª resposta do Maestro depois de Maestro → Worker → Maestro, com e sem o cache em
      disco.
  - Resultado: é o número que decide entre as opções A, B e C da E3.
- [ ] **V6: sessões em paralelo no mesmo modelo.** Com o mesmo modelo carregado, rodar 1, 2 e 4
      sessões gerando ao mesmo tempo, com o `ctx` padrão (8192) e com um `ctx` maior (32768).
      Medir:
  - tok/s de cada sessão e o total;
  - o tempo de processar o prompt de uma sessão enquanto outra processa um prompt de 20k;
  - a VRAM antes e durante;
  - em que ponto o pool com KV unificado começa a descartar cache ou recusar requisição.
  - Resultado: define os tetos de paralelo e o `ctx` mínimo de cada perfil (E4). Se o total com 2
    sessões não passar de 1,3× o de 1 sessão, o paralelo no mesmo modelo fica desligado no Balanced e
    no Low VRAM.

**Pronto quando:** o script roda de ponta a ponta sem intervenção, há um baseline salvo e as V1–V6 têm
resultado registrado.

---

## E1: integridade do Maestro (correções pequenas no `taskdb.py`)

**Por quê:** hoje um modelo pequeno consegue declarar pronto o que não está.
**Depende de:** nada. Tudo aqui mexe em `taskdb.py`/`maestro.py`, por isso vai numa entrega só.

- [x] **Bloquear `failed → completed` sem prova.** Tirar a transição da tabela (`taskdb.py:51`) ou só
      permiti-la se houve um `run_command` do `verify_command` que passou depois da última falha.
- [x] **"Conferida" exige evidência.** O atalho em `taskdb.py:444` não pode fechar uma tarefa
      `pending`/`needs_human` sem uma verificação posterior que passou.
- [x] **Ciclo em `depends_on`.** Detectar no `plan_feature` e no `update_task` (DFS simples) e recusar
      com a mensagem "ciclo: A → B → A". Hoje as tarefas ficam presas em `unmet_deps` sem nenhum aviso
      (`taskdb.py:539`, `305`).
- [x] **Tarefa sem `verify_command`.** No `plan_feature`, avisar e pedir um verify. Aceitar sem verify só
      com uma justificativa explícita (`verify_reason`), para a tarefa não sair "unverified" em silêncio.
- [x] **Validação final de verdade.** `encerra_validadas` (`taskdb.py:515`) hoje aceita qualquer
      `run_command`. Passar a exigir que os `verify_command` de **todas** as tarefas concluídas rodem
      juntos e passem, mais o comando de teste do projeto, se o FORJA.md declarar um.
- [x] Testes em `tests/test_maestro.py`/`test_planos.py`, um para cada brecha acima: o teste reproduz a
      brecha e falha antes da correção.

**Feito em 2026-09-25**, validado com o Maestro real (gpt-oss:120b do Ollama Cloud):
- o `plan_feature` recusou tarefas sem verify;
- o fechamento sem prova de uma tarefa com o verify falhando foi recusado;
- a entrega só encerrou depois de rodar de novo os dois verify.

Diferenças em relação ao plano:
- o ciclo é checado só no `plan_feature`, porque o `update_task` não mexe em `depends_on`;
- o "comando de teste do FORJA.md" ficou de fora, porque o FORJA.md não tem um campo estruturado para
  isso. Os verify das tarefas já cobrem a entrega.

Correções achadas na validação e feitas junto:
- Worker com erro (ex.: conexão) deixa a tarefa `failed`, não `reviewing`;
- `update_task` com contrato parcial mescla com o atual e valida tudo antes de mudar o status;
- campos do contrato soltos na tarefa do plano (fora de `contract`) são aceitos;
- a dica de erro de conexão do Ollama Cloud não fala mais em `OLLAMA_HOST`.

**Pronto quando:** nenhuma tarefa ou funcionalidade chega a `completed` sem um comando de verificação
que passou.

---

## E2: integração (commit por tarefa, regressão, rollback)

**Por quê:** é o elo mais fraco do pipeline (3/10). Tudo é escrito na mesma árvore, o diff é cumulativo
e nada roda de novo os testes antigos.
**Depende de:** E1 (a regressão usa os `verify_command` já obrigatórios).

- [x] **Commit automático por tarefa concluída.** Quando uma tarefa vira `completed`, fazer
      `git add -A && git commit -m "forja(task <código>): <título>"` no projeto.
  - Se a pasta não é git: fazer `git init` só com o aceite do usuário, e perguntar na primeira vez.
    Sem aceite, manter o comportamento atual e avisar.
  - Respeitar o `.gitignore`. Nunca fazer commit de `.env*` nem de arquivos acima de 5 MB.
- [x] **Diff por tentativa.** `_mudancas` (`maestro.py:81`) passa a comparar com o commit da tarefa
      anterior (guardar o hash de início da tentativa em `taskdb`), não com `HEAD` + árvore suja.
- [x] **Rollback automático de tentativa que falhou.** Quando a tentativa termina `failed`, voltar os
      arquivos ao hash de início (`git stash` ou `checkout`), para a próxima tentativa começar limpa.
      Guardar o diff descartado no `last_error`, para o Worker ver o que não funcionou.
- [x] **Regressão depois de cada tarefa.** Depois que o verify da tarefa N passar, rodar de novo os
      `verify_command` das tarefas já concluídas (com um teto de tempo total, por exemplo 5 min).
  - Se alguma quebrar: a tarefa N volta para `failed`, com a mensagem "quebrou a tarefa X".
  - Idéia para modelos lentos: rodar primeiro só as tarefas que tocam os mesmos arquivos e a suíte
    completa a cada 5 tarefas.
- [x] `outside_contract` (`maestro.py:141`) deixa de ser só aviso. Um arquivo fora do contrato que
      quebra a regressão aparece em destaque no resultado.
- [x] Testes com um repo git em `tmp_path`: commit criado, diff isolado, rollback, e regressão pegando
      uma tarefa que quebra a anterior.

**Feito em 2026-09-25**, validado com o Maestro real (gpt-oss:120b do Ollama Cloud) num repo git:
- TASK-001 virou o commit `48580da` e TASK-002 o `e4cb438`, cada um só com os arquivos da tarefa. O
  FORJA.md e o `.forja/` do usuário ficaram fora;
- a 1ª tentativa da TASK-002, instruída a estragar `soma`, passou no próprio verify, e a regressão
  pegou a quebra da TASK-001;
- os arquivos voltaram ao estado de antes, e o diff descartado foi para o briefing;
- a 2ª tentativa, com `strategy`, passou.

Diferenças em relação ao plano:
- **Commit:**
  - sem `git init` automático: a pasta sem git só recebe um aviso (uma vez por conversa);
  - o commit leva só os arquivos que as tentativas aceitas escreveram, e não `git add -A`, que levaria
    junto alterações do usuário.
- **Diff por tentativa:** não guarda hash. Com um commit por tarefa e a tentativa falha revertida, o
  `git diff HEAD` de cada arquivo já é o da tentativa.
- **Rollback:** usa os checkpoints (`checkpoints.restore_attempt`), que funcionam com ou sem git. O que
  o Worker mudou pelo `run_command` (ex.: `npm install`) não volta.
- **Regressão:**
  - roda a lista inteira de verify antigos, em série, com teto de 300 s;
  - a otimização "só as que tocam os mesmos arquivos" ficou para quando o tempo pesar.
- **Validação da entrega:** passou a aceitar um comando que cobre o verify, por exemplo
  `pytest -q a.py b.py` para os verify `pytest -q a.py` e `pytest -q b.py`.

**Pronto quando:** cada tarefa concluída é um commit, uma tentativa que falhou não deixa sujeira, e uma
regressão é detectada no mesmo passo.

---

## E3: execução (Worker que corrige sozinho, escalonador, custo de troca de modelo)

**Por quê:** hoje cada correção exige uma volta à Maestro. Com modelos diferentes, cada volta são duas
trocas de modelo.
**Depende de:** E1. Tem uma dependência leve da E4: o loop interno deixa o Worker mais tempo no servidor,
e sem a política de execução isso aumenta o cache perdido do Maestro. Com E2 pronta, o loop interno
também pode usar o rollback.

- [ ] **O Worker passa pela política de execução (E4).** Hoje ele roda no mesmo llama-server enquanto o
      Maestro espera e, com `-np 1`, expulsa o cache do Maestro em **toda** tarefa. O Worker usa um slot
      diferente do principal, ou espera, conforme a política.

- [x] **Loop de correção dentro do Worker.** Se o `verify` falhar e sobrarem passos (dos 15 de
      `config.py:113`), devolver a saída do teste ao Worker como mensagem ("o verify falhou: …, corrija")
      em vez de encerrar a tentativa. Teto de 2 voltas internas, e depois disso a tentativa vai para a
      Maestro como hoje.
- [x] **Compactar o histórico do Worker.** Hoje ele cresce sem limite e o resultado de ferramenta entra
      inteiro (`subagents.py:603`). Aplicar o `compact.podar` a cada passo e cortar cada resultado a uma
      fração da janela (ver E4).
- [x] **Fallback de janela no meio da tentativa.** Hoje ele só existe antes do 1º passo
      (`subagents.py:545`). Estourou no meio: podar e tentar de novo uma vez antes de dar erro.
- [x] **`task_result` mais enxuto para a Maestro.** Hoje cada `run_task` custa ~2–3k tokens (JSON com
      saída de teste de até 4000 caracteres e 5×1500 de erros, `maestro.py:25-27`). Mandar status, as
      últimas 20 linhas relevantes do teste e os arquivos tocados. O resto fica no banco, acessível por
      `list_tasks code=… detail=true`.
- [x] **Escalonador determinístico.** Uma função `proxima_pronta()` escolhe a próxima tarefa pendente com
      as dependências feitas, por `priority` e depois pela ordem do plano. A Maestro pode chamar
      `run_task` sem `code`, e o código escolhe. Assim a ordem deixa de depender do modelo pequeno.
- [ ] **Custo de troca de modelo** (a solução depende do número medido na E0). O Worker é a única
      chamada auxiliar que pode trocar de modelo, porque executa a entrega de verdade, e mesmo assim só
      pela política da E4:
  - Opção A: quando Maestro e Worker são modelos diferentes, rodar **várias tarefas prontas seguidas**
    no Worker antes de voltar à Maestro (lote), para trocar duas vezes por lote e não por tarefa.
  - Opção B: uma recomendação na UI: "mesmo modelo para Maestro e Worker é N× mais rápido nesta
    máquina".
  - Opção C: trocar de modelo **com o cache em disco** (E4): salvar o slot do Maestro, descarregar,
    carregar o Worker e restaurar o cache dele, se houver; na volta, fazer o mesmo com o Maestro. A
    troca passa a custar só o tempo de carregar mais o de restaurar, sem reprocessar. Combina com a
    opção A.
  - Decidir depois de ver o baseline.
- [x] Limite de tamanho de tarefa no `plan_feature`: avisar quando o contrato declarar mais de ~5
      arquivos ou quando o `goal` tiver mais de uma ação ("e também…"), sugerindo dividir.
- [x] Testes:
  - Worker corrige após um verify falho sem voltar à Maestro;
  - escalonador respeita dependências e prioridade;
  - `task_result` fica abaixo de um teto de caracteres;
  - o Worker nunca usa o slot fixo do principal.

**Feito em 2026-09-25 (parcial):**
- **Validado no Forja real** (gpt-oss:120b):
  - `run_task` sem `code` escolheu a tarefa pronta;
  - o resultado enxuto chegou ao Maestro;
  - a tarefa virou commit e a entrega fechou.
- **Loop de correção validado só por teste:** nas duas rodadas reais, o próprio Worker rodou o
  `pytest`, viu a falha e corrigiu dentro dos passos dele, antes do verify do Forja. O loop do Forja
  fica como rede para o Worker que não se testa (comum em modelo pequeno; conferir na E16 com modelo
  local).
- **Achados da validação, corrigidos:**
  - `update_task` com `max_attempts: 0` (o gpt-oss manda todos os campos vazios) trocava o limite da
    tarefa para 1. Agora 0 é "não mexer";
  - com a tarefa aprovada, os erros intermediários do Worker ("exit code 1… FAILURES", já corrigidos)
    iam no resumo, e o Maestro bloqueou uma tarefa certa por isso. Agora ficam só no detalhe.
- **Ficou para depois:**
  - a política de execução, porque depende da E4;
  - o custo de troca de modelo, porque depende do número da E0.
- **Poda do histórico do Worker:**
  - acima de 60% da janela, os resultados antigos são cortados a 1500 caracteres, em bloco;
  - cada resultado novo tem teto de 25% da janela;
  - se estourar no meio da tentativa, poda forte (600) e repete o passo uma vez.

**Pronto quando:** o bench da E0 mostra menos voltas à Maestro por tarefa e menos tempo de relógio.

---

## E4: contexto para janela pequena e política de execução

**Por quê:** os tetos atuais foram pensados para 32k. Com 8–16k, um único resultado de ferramenta enche
a janela, e a compactação não entra no caso mais comum. Além disso, qualquer chamada auxiliar ao mesmo
llama-server pode expulsar o cache do agente principal (hoje o backend não usa `id_slot` nem
`cache_prompt` em lugar nenhum), e cada entrega decidia isso sozinha.
**Depende de:** nada (mexe em `agent.py`, `compact.py`, `tools.py`, `shell.py`, `browser.py`, `memory.py`,
`modelctl.py`, `localai.py`, `llm.py`). **É a base** de E3, E7, E8 e E11: fazer a política de execução
primeiro, dentro desta entrega. A parte de cache em disco e dos padrões de KV depende das validações
V1–V4 da E0. O resto da E4 não depende delas. **A política de execução depende da parte A da E13**: ela
precisa saber o que cada backend deixa o Forja controlar.

### Política de execução (fazer primeiro)

- [ ] **Uma função só decide onde cada chamada roda:** `modelctl.como_rodar(papel, modelo_pedido)`, com
      `papel` ∈ `principal | worker | explorador | revisor | visual | lateral | compactar | embeddings`.
      Devolve um destes caminhos:
  - `mesmo-slot-sequencial`: o modelo carregado, esperando a vez;
  - `outro-slot`: o modelo carregado, num slot que não é o do principal;
  - `2o-modelo`: carregar um segundo modelo sem descarregar o principal;
  - `modelo-do-principal`: usar o modelo do principal em vez do pedido;
  - `trocar-modelo`: descarregar e carregar outro;
  - `nuvem`;
  - `pular`: com motivo.

  Entradas da decisão:
  - VRAM livre (`localai.hardware()["vram_free"]`, `modelctl.py:217`) e tamanho estimado do GGUF com o
    contexto;
  - tipo de slot (`--parallel`, KV unificado, `ctx_por_requisicao`, `localai.py:275`);
  - folga no pool (uso atual do principal + janela pedida);
  - opção de nuvem por papel nas configurações, **desligada** por padrão, porque o código sai da máquina.
- [ ] **Slot fixo do agente principal.** O principal (Maestro ou agente) usa sempre o mesmo `id_slot`,
      com `cache_prompt: true`. Toda chamada auxiliar usa outro slot. Com `-np 1`, ela espera o
      principal ficar ocioso e, depois dela, o custo de reprocessar é conhecido e medido (E0/E10).
      Avaliar sugerir `--parallel 2` + KV unificado como padrão quando a VRAM permitir.
- [ ] **Concorrência pelo tipo de slot:**
  - `-np 1`: estritamente sequencial.
  - Slots com KV unificado (o padrão do Forja: `parallel=0` → 4 slots, `localai.py:282`): sequencial por
    padrão. Paralelo só com teto (2) e só com folga no pool, porque com o pool cheio o cache do
    principal sai primeiro.
  - Slots sem KV unificado: a janela por slot é `ctx/N`. Abaixo do mínimo do papel, sequencial.
- [ ] **Regra geral: chamada auxiliar não troca de modelo.** Quando o modelo pedido não cabe junto na
      VRAM:
  - explorador, revisor, lateral, compactar: `modelo-do-principal`;
  - `visual_review` sem modelo de visão carregável junto: `pular`, com o aviso "revisão visual pulada:
    sem VRAM para o modelo de visão". Não trocar no meio do trabalho;
  - embeddings (E6, se um dia existirem): `pular` e usar a busca FTS5;
  - Worker: a única exceção. Pode `trocar-modelo`, mas em lote (E3), nunca por tarefa quando a E0
    mostrar que a troca domina o tempo.
- [ ] **Nuvem por papel, opt-in:** "exploração/revisão/visual podem usar nuvem", cada uma com o seu
      interruptor, todos desligados por padrão. Com a nuvem ligada, ela entra antes do
      `modelo-do-principal`.
- [ ] **Transparência:** o evento de cada chamada auxiliar mostra o caminho escolhido e o motivo
      (ex.: `explorador → modelo-do-principal (VRAM: faltam 2,1 GB)`), e a métrica registra o mesmo
      (E10).
- [ ] **Migrar quem já chama o LLM por fora** para `como_rodar`:
  - Worker (`maestro.py:290`);
  - `subagents._review` (`subagents.py:373`);
  - `visual_review` (`qualidade.py:151`);
  - título e resumo;
  - compactação (`compact.py`);
  - `delegate_task`.
- [ ] Testes com `hardware()` e os parâmetros do servidor simulados:
  - cada papel em cada cenário de VRAM e slot;
  - chamada auxiliar nunca recebe o slot do principal;
  - nuvem desligada nunca é escolhida;
  - `visual_review` pula em vez de trocar.

### Cache do prompt em disco (salvar e restaurar slot)

Hoje, um unload perde o KV cache, e a primeira resposta depois do reload reprocessa o prompt inteiro:
dezenas de segundos a minutos com 20k tokens. Com `-np 1`, alternar entre dois chats também faz cada um
expulsar o cache do outro. O llama-server consegue salvar o cache de um slot em arquivo e restaurá-lo
depois, mas o Forja não liga isso (`localai.py:1448` só passa `-np`).

- [ ] **Vale para todas as conversas**: chat, agente, Maestro, Worker e chamadas auxiliares. Só não vale
      para LM Studio e Ollama, porque o Forja não controla o processo deles. Nesses, o recurso aparece
      desligado, com a explicação.
- [ ] **Subir o llama-server com `--slot-save-path <FORJA_DATA>/kvcache/`**, sempre que o Forja for o
      dono do processo.
- [ ] **Padrões novos, ligados sem o usuário precisar configurar nada** (`localai.DEFAULT_PARAMS`,
      `localai.py:67`):
  - `kv_unified: True`, explícito. Hoje é `False`, e só vale porque `parallel=0` deixa o llama.cpp
    unificar sozinho. Cada slot enxerga a janela inteira.
  - `parallel: 0` (automático), como hoje.
  - `cache_type_k/v: "q8_0"` (hoje `f16`): metade da VRAM do KV e metade do arquivo em disco. Exige
    `flash_attn`: se o `fa` estiver desligado ou o backend não suportar, cair para `f16` sozinho e
    avisar.
  - `flash_attn: True`, como hoje.
  - `ctx_checkpoints`: manter o que já existe. Importa para modelos com janela deslizante (SWA) e
    híbridos.
  - Cache em disco **ligado**, com limite de 4 GB (ver a configuração abaixo).
- [ ] **Migração dos modelos já configurados.** Ao atualizar, aplicar os padrões novos só nos
      parâmetros que ainda estão com o valor padrão antigo. Tudo o que o usuário mudou à mão fica como
      está. Mostrar uma vez: "padrões de cache atualizados para este modelo (KV q8_0, cache em disco)",
      com um "desfazer".
- [ ] **Aviso ao mudar um parâmetro que invalida o cache** (`ctx`, `cache_type_k/v`, `kv_unified`,
      troca do GGUF): "isto descarta os N MB de cache salvo deste modelo". A regra prática, na própria
      tela: escolher os parâmetros uma vez por modelo e não mexer mais.
- [ ] **Os padrões acima só mudam depois das validações V1–V4 da E0.** O que cada resultado decide:
  - V1 falhou: cache em disco desligado quando o KV é unificado;
  - V2 falhou: cache em disco opcional, e não padrão;
  - V3 falhou: aviso de ganho menor nos modelos SWA/híbridos;
  - V4 falhou: o KV continua `f16`.

  Citar no commit o arquivo `docs/bench/2026-09-kvcache.json` com os números.
- [ ] **Salvar antes de perder.** Chamar `POST /slots/{id}?action=save` com o arquivo
      `<hash-do-modelo>/<conversa>-<papel>.bin` sempre que o cache de uma conversa for sair do slot:
  - antes de um unload ou de uma troca de modelo (`modelctl`, o Worker da E3);
  - com `-np 1`, antes de outra conversa ou chamada auxiliar tomar o slot;
  - ao fechar o app, só para as conversas abertas.
- [ ] **Restaurar ao voltar.** Antes da 1ª requisição de uma conversa num slot que não tem o cache dela,
      chamar `POST /slots/{id}?action=restore`. O servidor aproveita o prefixo que casar e processa
      só o resto.
  - Se o restore falhar (arquivo de outro modelo ou parâmetros diferentes), seguir sem cache, apagar
    o arquivo e registrar no log. Nunca travar o turno por isso.
- [ ] **Chave de validade.** O cache só vale para o mesmo GGUF (hash ou caminho + tamanho + mtime) e os
      mesmos parâmetros que mudam o KV: `ctx`, `cache-type-k/v`, KV unificado, versão do llama-server.
      Guardar isso num `.json` ao lado do `.bin`. Chave diferente: descartar sem tentar restaurar.
- [ ] **Configuração "Cache em disco"**, na tela de modelos locais:
  - interruptor ligado/desligado (padrão ligado);
  - **limite de tamanho em disco** (padrão 4 GB, mínimo 256 MB, com 0 desligando), sem teto
    artificial;
  - ao passar do limite, apagar os arquivos menos usados primeiro (LRU pelo último restore/save);
  - mostrar o uso atual ("1,8 GB de 4 GB, 23 conversas") e um botão "limpar cache";
  - apagar o cache de uma conversa quando ela for apagada, e todo o cache de um modelo quando o GGUF
    for removido.
- [ ] **Descarregar modelo ocioso** (para não ocupar a VRAM sem necessidade). Hoje só existem
      `persistent` e `unload_after_task` (`config.py:138`, `modelctl.py:34`). Não há descarga por tempo
      ocioso.
  - Configuração nova "Descarregar modelo após N minutos sem uso", na tela de modelos locais: padrão
    **15 min**, 0 = nunca, até 1440. Criar em `settings.py`, como o `browser_idle_minutes`.
  - A varredura reaproveita o padrão de ociosidade do navegador (`browser.py:595`): a cada minuto,
    fecha o que estiver ocioso.
  - **"Ocioso" é só quando não há nada usando o modelo:**
    - nenhuma requisição em andamento;
    - nenhuma execução de agente, Maestro ou Worker ativa, nem subagente em segundo plano, lote ou
      goal rodando;
    - nenhuma geração de imagem ou vídeo esperando a VRAM.

    Uma execução parada em `ask_user` ou aprovação conta como ociosa só depois do dobro do tempo.
  - **Antes de descarregar, salvar o cache** de todos os slots com conversa (a seção acima). Se o cache
    em disco estiver desligado ou falhar, descarregar mesmo assim, porque a conversa está no SQLite. O
    que se perde é só o tempo de reprocessar.
  - Descarregar pelo caminho que já espera a VRAM voltar (`modelctl.py:229`), sem só matar o processo.
  - **Recarregar sob demanda.** A próxima mensagem carrega o mesmo modelo com os mesmos parâmetros
    (`_garante_modelo`, `agent.py:1619`) e restaura o cache. A UI mostra "modelo descarregado por
    ociosidade: carrega na próxima mensagem (~N s)", com N medido na última carga.
  - **Pré-carga opcional:** ao focar a caixa de mensagem de uma conversa cujo modelo foi descarregado,
    começar a carregar já. Vem desligada por padrão, porque gasta VRAM só por clicar.
  - Vale só para o llama.cpp embutido e o stable-diffusion.cpp (processos do Forja). LM Studio e Ollama
    têm a descarga automática deles, e a tela diz isso.
  - Testes:
    - não descarrega com uma execução ativa;
    - descarrega depois de N minutos sem uso;
    - salva o cache antes de descarregar;
    - a próxima mensagem recarrega e restaura;
    - N = 0 nunca descarrega.
- [ ] **Privacidade:** o arquivo guarda a conversa codificada (código, prompts). Fica só em
      `FORJA_DATA`, nunca vai para o mirror ou o mobile, e sai junto no "limpar dados".
- [ ] **Política de execução:** a `como_rodar` passa a saber se existe cache em disco válido para
      aquele papel e modelo, e o custo estimado de trocar de modelo cai de "carregar + reprocessar"
      para "carregar + restaurar". Mesmo assim, a regra "chamada auxiliar não troca de modelo" continua
      valendo, porque carregar o modelo ainda custa.
- [ ] Testes (servidor falso, como o `lsp_falso.py`):
  - save antes do unload e restore depois do reload;
  - chave diferente descarta sem restaurar;
  - o limite de disco apaga o mais antigo;
  - restore que falha não trava o turno;
  - apagar a conversa apaga o cache.
- [ ] Validar no app real com o bench da E0: tempo da 1ª resposta depois de uma troca Maestro → Worker
      → Maestro, com e sem o cache em disco.

### Contexto em janela pequena

- [ ] **Só a última mensagem "contexto" vai ao modelo.** Hoje cada versão carrega AGENTS.md, FORJA.md,
      memórias e skills, e todas continuam no histórico com `to_model=True` (`agent.py:1026`). As versões
      antigas passam a `to_model=False`. Cuidado com o cache: a troca acontece exatamente onde a mensagem
      nova entra, então o prefixo anterior continua valendo. Atualizar `test_prefixo.py`.
- [ ] **Compactar dentro do turno.** `split_point` (`compact.py:47`) devolve `None` quando há uma única
      pergunta seguida de 40 chamadas de ferramenta. Nesse caso, resumir os pares ferramenta/resultado
      mais antigos do turno atual, mantendo os últimos N inteiros.
- [ ] **Tetos proporcionais à janela.** Criar `config.teto(fração)`, que usa o `context_limit` real, e
      aplicar em:
  - spill (`tools.py:234`, hoje 24k)
  - `MAX_OUTPUT` do `run_command` (`shell.py:21`, hoje 20k)
  - `browser_read` (`browser.py:712`, hoje 15k)
  - `read_file` (`tools.py:349`, hoje 2000 linhas)
  - cadeia de AGENTS.md (`memory.py:95`, hoje **64k**)
  - FORJA.md e o índice de memória (8k cada)
- [ ] **`run_command` sem perder o meio.** Fazer o spill antes de cortar e não apagar o log
      (`shell.py:125`). O modelo recebe cabeça e cauda mais o caminho do log completo, para ler com
      `read_file`.
- [ ] **O gatilho usa `prompt_tokens` real**, que o servidor devolve, e não chars/4. O chars/4 subestima
      português e código (~3 caracteres por token).
- [ ] **O `/compact` manual usa a janela real** (`main.py:1649` usa `config.NUM_CTX`).
- [ ] **A poda não pode deslizar a cada passo.** Hoje a janela de "últimos 4" invalida o cache a cada
      poda. Podar em blocos: quando podar, podar até um marco fixo e não mexer de novo até o próximo
      gatilho.
- [ ] **Catálogo de ferramentas mais leve.** São ~60 ferramentas com descrições longas em toda
      requisição. Medir os tokens do schema. Se passar de ~20% da janela, encurtar as descrições e
      esconder as ferramentas de nicho (mídia, documentos, goals) atrás de uma ferramenta
      `mais_ferramentas`, que as liga sob demanda. O catálogo continua estável dentro da conversa, por
      causa do cache.
- [ ] O anel de contexto (`ContextRing.tsx`) passa a contar a mensagem "contexto" como **sistema**, não
      como mensagens.
- [ ] Testes: compactação dentro de um turno longo; só a última mensagem "contexto" vai ao modelo; tetos
      mudam com a janela; spill do `run_command` preserva o log.

### Perfis de hardware (Automático, Performance, Balanced, Low VRAM)

**Ideia:** o Forja percebe sozinho quantos modelos cabem na máquina e já trabalha do jeito certo, sem o
usuário configurar nada. A detecção por chamada já é a `como_rodar` (acima). O perfil só escolhe os
**valores** que ela e o resto da E4 usam: é um conjunto de valores, sem lógica nova.

- [ ] **Seletor "Perfil de hardware"** nas configurações, com quatro opções: **Automático (padrão)**,
      Performance, Balanced e Low VRAM.
  - O Automático escolhe o perfil pela VRAM e pela RAM de `localai.hardware()` (`localai.py:630`) e
    pelo tamanho do modelo principal com o contexto. A tela mostra o escolhido e o motivo (ex.: "8 GB
    de VRAM, modelo de 7,5 GB → Low VRAM").
  - O Automático reavalia ao trocar o modelo principal ou ao ligar ou desligar uma GPU em
    Configurações › Hardware. Não reavalia no meio de uma execução.
- [ ] **Valores iniciais de cada perfil** (ponto de partida; os números finais saem da E0: V2, V4, V5 e
      V6):

  | Regra | Performance | Balanced | Low VRAM |
  |---|---|---|---|
  | Workers em paralelo (E7) | até 3 | 2, se couber | 1 (sequencial) |
  | 2º modelo carregado junto | sim, se couber | só se sobrar ≥ 20% de VRAM | nunca |
  | Sessões em paralelo no mesmo modelo | conforme V6 | conforme V6 | 1 por vez |
  | Troca de modelo (Worker, E3) | quando precisar | em lote | em lote e só com cache em disco |
  | Modelo para Maestro, Worker e explorador | o configurado | o configurado | **preferir um só para tudo** |
  | KV cache | `f16` | `q8_0` | `q8_0` |
  | Cache em disco | ligado, 4 GB | ligado, 4 GB | ligado, 8 GB |
  | Descarga por ociosidade | 30 min | 15 min | 5 min |
  | Limites de contexto | janela cheia | proporcionais | proporcionais e mais apertados |

- [ ] **Configuração manual vence o perfil.** O que o usuário mudou à mão continua valendo por cima de
      qualquer perfil, com uma marca "alterado" ao lado. Trocar de perfil não apaga esses ajustes. Um
      botão "voltar ao perfil" desfaz.
- [ ] **No Low VRAM, trocar de modelo é o último recurso.** Com 8 GB, um modelo só com cache é quase
      sempre mais rápido que dois se revezando. Se Maestro e Worker estiverem em modelos diferentes,
      avisar: "neste PC, usar o mesmo modelo para os dois é ~N× mais rápido" (N medido na V5), com um
      botão para aplicar.
- [ ] **Recomendar um modelo menor, sem trocar sozinho.** O Forja nunca troca o modelo escolhido pelo
      usuário. Quando o modelo não couber inteiro na GPU (offload na CPU), mostrar: "este modelo de 14B
      roda com parte na CPU; um de 7–8B cabe inteiro e fica ~N× mais rápido", listando os GGUF já
      baixados que cabem, com um botão.
- [ ] **Transparência:** cada decisão da `como_rodar` cita o perfil ativo no motivo (ex.: `worker →
      sequencial (Low VRAM: 1 worker)`). A tela de métricas da E10 mostra o perfil de cada execução.
- [ ] Testes com `hardware()` simulado:
  - 8 GB de VRAM com um modelo de 7 GB → Low VRAM;
  - 24 GB com o mesmo modelo → Performance;
  - ajuste manual sobrevive à troca de perfil;
  - o Automático não troca de perfil no meio de uma execução;
  - o Low VRAM nunca carrega um 2º modelo.

**Pronto quando:**
- uma conversa de agente com 40 ferramentas numa janela de 8k não estoura e não perde o cache a cada
  passo;
- no bench da E0, o cache perdido do Maestro depois de cada `run_task` cai para perto de zero
  (com slots) ou fica medido e explicado (com `-np 1`);
- nenhuma chamada auxiliar troca de modelo sozinha.

---

## E11: explorador só leitura (para o Maestro e o modo agente)

**Por quê:** hoje o Maestro não consegue delegar exploração. O `delegate_task` fica bloqueado nele
(`MAESTRO_FORA`, `agent.py:810`), e o único jeito de delegar é o `run_task`, que exige contrato e um Worker
que escreve. Para entender um repo antes de planejar, o Maestro lê tudo sozinho e enche a própria janela.
No modo agente, o `delegate_task level='rapido'` recebe **todas** as ferramentas (`subagents.py:351`), então
um modelo pequeno mandado explorar pode acabar editando arquivo.

**Depende de:** E4 (política de execução: slot, VRAM e nuvem). Fica melhor depois da E5, porque o
explorador ganha `tree`, `ast` e `imports`.

**Decisão de desenho: o explorador não é Worker nem task.** Uma task é uma entrega de código: tem
contrato, verify, tentativas, `needs_human`, commit (E2), regressão (E2), lugar na fila (E3) e entra nas
métricas de sucesso (E10). A exploração produz conhecimento, e cada um desses mecanismos a atrapalharia:
- sem verify, ela sairia sempre "unverified";
- "não achei" viraria falha;
- geraria commit vazio;
- distorceria o progresso e a taxa de sucesso.

O explorador usa o mesmo mecanismo de subagente do `subagents.py`, mas com outro papel.

- [x] **Persona embutida `explorador`.**
  - Só leitura: `read_file`, `list_dir`, `glob`, `grep`, `lsp` e, depois da E5, `tree`, `ast`,
    `imports`. Sem `run_command`, sem escrita e sem navegador.
  - O bloqueio é por lista de ferramentas, não só por instrução no prompt.
  - Usa o slot `rapido`. Se for o mesmo modelo do chamador, não há troca de modelo.
- [x] **Ferramenta `explore(pergunta, paths?)` no Maestro,** fora do `MAESTRO_FORA`.
  - Por dentro chama o mecanismo do `delegate_task` com a persona `explorador`.
  - O padrão é **um por vez, com o Maestro esperando o relatório**. O Maestro depende do resultado para
    seguir, então "segundo plano" não ganha nada. O ganho é isolar o contexto, não a velocidade.
- [ ] **Onde roda: `como_rodar("explorador", …)` da E4.** Na prática:
  - mesmo modelo: sequencial em outro slot;
  - 2º modelo só se couber na VRAM junto com o Maestro;
  - se não couber, o modelo do Maestro, **nunca uma troca de modelo**;
  - nuvem só com o interruptor de exploração ligado.

  O explorador não implementa nenhuma regra própria de slot ou VRAM.
- [x] **No modo agente:** criar `delegate_task(agent='explorador')`, ou um `level='explorar'`. Ajustar a
      regra do prompt ("para varrer muitos arquivos…", `agent.py:511` e `737`) para apontar o explorador
      em vez do `rapido` com todas as ferramentas.
- [x] **Relatório com formato fixo:** resposta direta, arquivos e linhas relevantes
      (`caminho:linha — por quê`) e o que não foi encontrado. Teto de tamanho proporcional à janela do
      chamador (E4).
- [x] **O relatório fica no estado do projeto.** Gravar em `projstate` (SQLite, como o resto do estado
      do Maestro) com a pergunta, os caminhos e a data, para sobreviver à compactação e ao reinício do
      app. Um bloco curto no prompt do Maestro lista as explorações já feitas, e o corpo é lido sob
      demanda, para não reexplorar depois de compactar.
  - Invalidar ou avisar quando um arquivo citado mudou desde a exploração (mtime).
- [x] **Reúso nas tasks:** o `plan_feature` e o `run_task` aceitam `explorations=[id]`, e o contrato do
      Worker leva os trechos relevantes. O Worker já começa sabendo onde mexer e economiza passos dos
      15.
- [x] **Lembrete automático:** quando o Maestro ou o agente fizer muitas leituras seguidas sem escrever
      (por exemplo, mais de 6 `read_file`/`grep`) ou a janela passar de ~50% com resultados de leitura,
      o código injeta uma dica curta: "use explore/delegate para varrer e fique só com a conclusão". É
      uma dica, não um bloqueio.
- [ ] **Métrica:** evento do tipo `exploracao` na tabela da E10, separado das tasks.
- [x] Testes:
  - a persona não recebe ferramenta de escrita, nem se o modelo pedir;
  - o explorador passa por `como_rodar` (os cenários de VRAM e slot são testados na E4);
  - com `-np 1` duas explorações rodam em sequência;
  - o cache do Maestro sobrevive a uma exploração quando há slot livre;
  - o Maestro consegue chamar `explore`;
  - o relatório sobrevive à compactação;
  - o aviso aparece quando um arquivo citado muda;
  - o contrato do Worker leva os trechos da exploração.

**Feito em 2026-09-25 (sem a política da E4).**

Validado no Forja real (Maestro, gpt-oss:120b, pacote `calc` com testes):
- o Maestro chamou `explore` antes de planejar;
- o relatório veio no formato (RESPOSTA / ARQUIVOS / NÃO ENCONTRADO) e foi guardado como EXP-001;
- o Maestro passou `explorations: ["EXP-001"]` no plano;
- o briefing do Worker trouxe "O QUE JÁ SE SABE DO CÓDIGO" com o relatório;
- a tarefa passou de primeira e virou commit.

Diferenças em relação ao plano:
- **Onde fica o relatório:** em `.forja/exploracoes/EXP-NNN.md`, e não numa tabela do SQLite. É a
  memória do projeto que já existia (FORJA.md e `.forja/`), sobrevive à compactação e ao reinício do
  app, e o índice entra no bloco do Project State. Desatualizada = arquivo citado com o mtime mudado.
- **Uma ferramenta `explore` para os dois modos**, e não um `level='explorar'`. No modo agente também
  existe `delegate_task(agent='explorador')`.
- **Onde roda:** não passa pela `como_rodar` (a E4 ainda não existe). Usa o slot `rapido`, com o
  fallback de sempre, e roda em sequência (primeiro plano).
- **Métrica:** fica para a E10.

Achados da validação, corrigidos:
- a 1ª rodada gravou como relatório a próxima chamada que o modelo escreveu como texto
  (`{"path": ...}`). Agora o explorador é cobrado pelo formato (até 2 vezes), e relatório sem
  `RESPOSTA:` não é guardado;
- o Maestro pôs `explorations` no nível do plano, e não em cada tarefa. Agora vale para todas as
  tarefas que não trouxerem as suas;
- o loop do subagente recusa chamada de ferramenta fora da lista dele. Vale para toda persona, não só
  para o explorador;
- caminhos do `rollback` no resumo da Maestro passam a ser relativos.

**Pronto quando:** o Maestro entende um repo desconhecido antes de planejar sem que a própria janela
passe de ~30% com leituras, e nenhum explorador consegue escrever.

---

## E5: ferramentas `tree`, `ast` e `imports`

**Por quê:** code intelligence é o ponto mais fraco do catálogo. `lsp` só funciona com um language server
instalado, e não há imports nem AST.
**Depende de:** nada. Dentro da entrega, o `imports` depende do parser do `ast`.

### `tree`
Árvore de diretórios indentada, para entender a forma de um projeto com poucos tokens.
- [x] Fica em `backend/app/tools.py`, junto do `list_dir`. Reaproveitar `IGNORED_DIRS`, `resolve_path` e
      `_rel`.
- [x] Parâmetros: `path`, `depth` (padrão 3, máx 10), `dirs_only`, `pattern` (glob; pastas sem nenhum
      arquivo que case somem).
- [x] Saída com `├──` / `└──` / `│`. Pasta com mais de ~25 filhos mostra os primeiros e resume o resto em
      `… +N arquivos`. Mostra a contagem por pasta e as linhas dos arquivos de texto pequenos (< 1 MB).
- [x] Respeitar o `.gitignore` da raiz no subconjunto simples do `fnmatch`, e ignorar também `dist`,
      `build` e `.next`. Teto de saída proporcional à janela (E4) ou de 500 linhas.
- [x] Decidir entre uma ferramenta separada e um `format: "tree"` no `list_dir`, pelo peso no catálogo
      (E4). **Recomendação:** ferramenta separada, com a descrição do `list_dir` apontando para ela.

### `ast`
Análise sintática sem language server.
- [x] Módulo novo `backend/app/codigo.py`, importado no `agent.py` junto do `lsp`.
- [x] Motor:
  - Python: `ast` da stdlib.
  - Demais linguagens: tree-sitter (`tree-sitter` + `tree-sitter-language-pack`, com wheels cp312
    win_amd64/Linux). Medir o peso no instalador; se pesar demais, usar os pacotes por linguagem.
  - Linguagens: TS, TSX, JS, JSX, Go, Rust, Java, C#, C/C++, PHP.
  - Sem o tree-sitter instalado, continua funcionando para Python.
- [x] Operações:
  - `outline`: árvore de símbolos com assinatura, intervalo de linhas e a 1ª linha da docstring. Aceita
    uma pasta também.
  - `symbol`: o fonte de um símbolo pelo nome (aceita `Classe.metodo`), no formato do `read_file`.
  - `node_at`: a cadeia de nós até uma linha e coluna.
  - `query`: S-expression do tree-sitter sobre um arquivo ou pasta, com teto de resultados.
- [x] Erro de sintaxe não é falha: devolver o que der e avisar "erro de sintaxe perto da linha N".
- [x] Cache do parse por `(caminho, mtime, tamanho)`, até ~64 arquivos.
- [x] Ajustar as descrições para dividir o uso: ast = estrutura e símbolo por nome; lsp = referências e
      tipos; grep = texto.

### `imports`
Grafo de dependências entre os arquivos do projeto.
- [x] Operações:
  - `of`: os imports de um arquivo, com a linha e o destino resolvido, marcando o que é externo e o que
    não resolveu.
  - `importers`: quem importa um arquivo, incluindo reexport.
  - `graph`: as arestas internas de uma pasta.
  - `cycles`: os ciclos encontrados, com no máximo ~20 na saída.
- [x] Resolução em Python:
  - `import x.y`, `from x import y` e os relativos `.`/`..`, usando `__init__.py`.
  - Raízes: a raiz do projeto e `src/`.
  - `import` dentro de função também conta, marcado `(local)`.
- [x] Resolução em JS/TS:
  - `import … from`, `import()`, `require()` e `export … from`.
  - Caminho relativo testando as extensões e o `index.*`.
  - `paths`/`baseUrl` do `tsconfig.json`, só o 1º nível.
- [x] Índice sob demanda com o `_arquivos()` do `busca.py`, em cache por mtime, com teto de ~5 mil
      arquivos.

### Comum
- [x] Adicionar o `tree-sitter` ao `requirements.txt` dos dois repos. Conferir que o `.pyd` entra no
      Python portátil e no instalador, e fazer o rebuild da imagem Docker.
- [x] Usar o `timeout` da `Tool`, para uma pasta grande não travar o passo.
- [x] Mostrar rótulo e ícone na UI de chamadas de ferramenta, se ela tiver um mapa por nome
      (`frontend/`, `forja-mobile/src/Chat.tsx`).
- [x] Testes em `tests/test_codigo.py` e `tests/test_busca.py`:
  - fixtures `.py` e `.ts`;
  - arquivo com erro de sintaxe;
  - tree-sitter ausente;
  - ciclo proposital;
  - alias do `tsconfig`.
- [x] Validar no app com o modelo local: "estrutura do repo", "o que importa `tools.py`?" e "mostra só a
      `list_dir`". O agente tem de escolher `tree`, `imports` e `ast` sozinho.

**Feito em 2026-09-25.**

Diferenças em relação ao plano:
- **Onde ficou:** o `tree` foi para o `codigo.py` junto com `ast` e `imports`, e não para o `tools.py`.
  Continua sendo uma ferramenta própria, e a descrição do `list_dir` aponta para ela.
- **Parser:** pacotes por linguagem (`tree-sitter` + python/javascript/typescript, ~0,55 MB, MIT). O
  language pack ficou de fora, então Go, Rust, Java e as outras linguagens ficam para depois.
- **Repositório aninhado ou worktree** (pasta com `.git` próprio, como `.claude/worktrees/*`) fica fora
  do `tree` e do índice, porque é cópia do código.
- **`cycles`** ignora import dentro de função, que é o jeito comum de quebrar um ciclo.

Validação no Forja real (modo agente, gpt-oss:120b, no próprio repositório do Forja):
- "estrutura do repo" → **`tree`** ✅;
- "quem importa `gitops.py`" → **`imports importers`** ✅. Na 1ª rodada foi `grep`, e isso mudou com a
  regra no prompt;
- "mostra só a função" → **`read_file`** do arquivo inteiro, e não `ast symbol`, mesmo com a regra. É
  preferência do gpt-oss, que pede todas as leituras de uma vez. Conferir com os modelos locais na E16.

Resta conferir:
- que o `.pyd` do tree-sitter vai no instalador. O `prepare.mjs` instala o `requirements.txt` no
  Python portátil, mas o build do instalador não foi feito;
- levar o `requirements.txt` para o `forja-web`, junto com o sync.

**Pronto quando:** as três ferramentas funcionam sem nenhum language server instalado.

---

## E6: busca e memória (FTS5 e memória por projeto)

**Por quê:** RAG é 2/10. Não há índice nenhum, e o `session_search` pode esconder resultados.
**Depende de:** E5, para indexar símbolos junto com o texto; a parte de sessões pode sair antes.

- [ ] **`session_search` com FTS5.** Criar uma tabela virtual FTS5 sobre `messages` no SQLite que já
      existe (`db.py`), com gatilhos de insert, update e delete, e ranking `bm25`. Corrigir o
      `limit(400)` que é aplicado antes do filtro de pasta (`sessoes.py:58`).
- [ ] **Busca em código com ranking.** Criar a ferramenta `code_search` (ou um modo do `grep`) sobre um
      índice FTS5 por projeto, com os arquivos e os símbolos do `ast outline`. Indexação incremental por
      mtime, feita na primeira chamada. Responde "onde se trata X" quando o nome exato não é conhecido.
- [ ] **Embeddings: só se o FTS5 não bastar.** Avaliar depois de usar. O llama.cpp embutido serve
      embeddings (`--embedding`), mas isso exige outro modelo carregado e compete com o slot. Se um dia
      existir, passa por `como_rodar("embeddings", …)` (E4): sem VRAM, pula e usa o FTS5, nunca troca
      de modelo. Deixar anotado, sem construir agora.
- [ ] **Memória por projeto.** O tipo "projeto" do `memory.py` passa a gravar sob a raiz do projeto
      (`.forja/memoria/` ou uma chave pela raiz do git), e o índice injetado mostra as memórias globais e
      as do projeto atual, não todas.
- [ ] Testes: ranking do FTS5; filtro por pasta antes do limite; memória de projeto que não vaza para
      outro projeto.

**Pronto quando:** "onde o app trata login?" acha o arquivo certo sem que o modelo saiba o nome do
arquivo.

---

## E7: paralelismo de Workers seguro

**Por quê:** hoje há 1 Worker por padrão. No modo paralelo, o lock vale só para os arquivos declarados e
não há isolamento.
**Depende de:** E2 (commits e o merge dependem do git por tarefa) e E4 (política de execução).

- [ ] **Um worktree por Worker em paralelo.** Criar com `git worktree add .forja/wt/<tarefa>` a partir do
      último commit. O Worker trabalha lá, e ao concluir o código faz o merge (ou rebase) na árvore
      principal e roda a regressão (E2).
  - Conflito de merge: a tarefa volta para `failed` com o conflito no `last_error`, e a próxima tentativa
    é sequencial.
- [ ] **Lock nos `write_file`/`edit_file`** e não só no contrato (`maestro.py:33-51`), para o caso sem
      worktree, que fica como fallback.
- [ ] **O `verify` roda no worktree da própria tarefa**, nunca na árvore que outro Worker está escrevendo.
- [ ] **Quantos Workers e em qual modelo: `como_rodar("worker", …)` da E4**, com o teto de VRAM e de
      folga no pool. Nenhum Worker usa o slot do Maestro. Se o pool não comporta N Workers mais o
      Maestro, o número de Workers cai (até 1) em vez de o cache do Maestro sair.
- [ ] **Slots do llama.cpp.** Avisar na UI quando a janela por slot (`ctx_por_requisicao`) ficar abaixo
      do mínimo do Worker (16k, `config.py:143`) com N workers. Sugerir `--kv-unified` ou menos workers.
- [ ] Testes: 2 Workers em tarefas que tocam o mesmo arquivo, com o merge limpo ou o conflito detectado.

**Pronto quando:** o bench da E0 com 2–3 Workers termina com a suíte verde e menos tempo que o
sequencial.

---

## E8: revisor (critic) de verdade

**Por quê:** 4/10. O `revisor.py` classifica risco de aprovação e não revisa código. O crítico real
(`subagents._review`, `subagents.py:373`) usa o menor modelo, só roda em tarefa "unverified" e não
bloqueia nada.
**Depende de:** E2 (revisa o diff do commit da tarefa, não a árvore suja) e E4 (política de execução).

- [ ] **Revisão depois de o verify passar**, e não só quando a tarefa sai "unverified". Recebe o diff da
      tarefa (E2), o contrato e os `acceptance_criteria`.
- [ ] **Checagem objetiva dos `acceptance_criteria`.** O revisor responde por critério
      `atendido`/`não atendido`, com uma linha de evidência (arquivo:linha). Um "não atendido" devolve a
      tarefa ao Worker, com teto de 1 volta.
- [ ] **Qual modelo usa o revisor: `como_rodar("revisor", …)` da E4.**
  - Por padrão, o modelo do Maestro.
  - Um modelo de revisão diferente só se couber na VRAM junto, **nunca uma troca de modelo por
    revisão**.
  - Nuvem só com o interruptor de revisão ligado.
- [ ] **`visual_review` também passa pela política** (`como_rodar("visual", …)`). Sem modelo de visão
      que caiba junto: pula com aviso. No portão de entrega (`qualidade.faltas_para_entregar`,
      `qualidade.py:201`), "pulada por VRAM" aparece como pendência explícita para o usuário, e não
      como aprovada.
- [ ] **Configurável:** `revisao: off | avisa | bloqueia` no FORJA.md ou nas configurações. O padrão é
      `avisa`.
- [ ] Renomear ou documentar o `revisor.py` (aprovação) para ninguém confundir com a revisão de código.
- [ ] Testes: um critério não atendido bloqueia no modo `bloqueia`; o modo `avisa` não bloqueia.

**Pronto quando:** uma tarefa que passa no teste mas ignora um critério de aceite é pega antes de virar
commit.

---

## E9: robustez de browser e terminal

**Por quê:** 7/10. Funciona, mas há casos que enganam o agente.
**Depende de:** nada. São correções pequenas e independentes, agrupadas por serem baratas.

- [x] **Sentinela no `terminal_send`.** Depois de cada comando, mandar `echo __FORJA_FIM_$LASTEXITCODE`
      (no bash, `$?`). O fim passa a ser certo e vem com o exit code, em vez de "quieto" por silêncio.
      Continua caindo no "quieto" em REPL interativo.
- [x] **Browser nativo: contexto por conversa.** Hoje todas as sessões usam `contexts[0]` e dividem
      cookies e storage (`browser.py:~160`). Criar um contexto por conversa, ou pelo menos documentar e
      oferecer um "limpar sessão do navegador".
- [x] **Limpar `%TEMP%\forja-serve`.** Apagar os logs de mais de 7 dias ao iniciar.
- [x] **O `run_command` não guarda a saída inteira em memória.** Escrever direto no arquivo e ler só
      cabeça e cauda (casa com o spill da E4).
- [x] **Tempo de ferramenta sem a espera de aprovação.** Hoje `meta["segundos"]` inclui a espera pela
      aprovação (`agent.py:1880`). Separar em `segundos` e `espera_aprovacao`.
- [x] Testes: sentinela com exit code ≠ 0; limpeza de logs antigos.

**Feito em 2026-09-25.**

Validado no Forja real (modo agente, gpt-oss:120b):
- `Start-Sleep -Seconds 4; cmd /c exit 3` no terminal voltou `[comando terminou: exit 3]`. Antes, os 4 s
  calados virariam "terminal quieto" no meio;
- a saída de 6000 linhas do `run_command` veio cortada, com o caminho do log completo, e o agente abriu
  esse arquivo com `read_file` e leu a linha 3000.

Como ficou:
- **Sentinela:** se não voltar dentro do `wait` (comando longo, ou um REPL que engoliu a linha), o
  terminal fica "pendente" e não recebe outro sentinela. O `terminal_read` avisa `[o comando anterior
  terminou: exit N]` quando o pendente aparece. Dentro de um REPL, o fim volta a ser inferido pelo
  silêncio.
- **Navegador: já estava resolvido, e a revisão errou.** Cada conversa tem a própria partição do
  Electron, em memória (`browserHost.js`, `forja-browser-<key>`), e as partições antigas em disco são
  apagadas ao abrir (`main.js`). O `contexts[0]` do Playwright é só a porta CDP, e agora um comentário
  explica isso.
- **`run_command`:** guarda só a cabeça e a cauda da saída na memória. Com a saída cortada, o log vai
  para a pasta de spill (legível pelo `read_file`), em vez de ser apagado. Isso adianta o item do spill
  da E4.
- **Limpeza:** os logs com mais de 7 dias em `%TEMP%\forja-serve` e em `spill/run-*.log` são apagados
  quando o backend sobe.
- **Tempo de ferramenta:** `segundos` não inclui mais a espera no card de aprovação. A espera vai em
  `espera_aprovacao` e aparece na Trajetória.

---

## E10: métricas agregadas e fechamento

**Por quê:** 5/10. Há números por resposta, mas nada responde "o Maestro melhorou?".
**Depende de:** E0. Idealmente é a última entrega, para repetir o bench.

- [ ] **Tabela `metricas` no SQLite**, com uma linha por evento:
  - tarefa concluída ou falha (tentativas, tempo, modelo);
  - chamada de ferramenta (nome, ok ou erro, segundos);
  - resposta (tokens de prompt, completion e cache);
  - chamada auxiliar (papel, caminho escolhido pela `como_rodar` e motivo);
  - **cache perdido por turno do principal** (`prompt_n` − `cache_n`), com qual chamada auxiliar rodou
    antes.
- [ ] **Log estruturado.** Um `logging` com JSON por linha em `FORJA_DATA/logs/agente.jsonl`, com
      rotação. Hoje só o `lotes.py` loga.
- [ ] **Tela de métricas** (ou uma aba nas configurações) com:
  - taxa de sucesso de tarefa na 1ª tentativa;
  - tentativas médias;
  - taxa de `needs_human`;
  - cache hit e cache perdido do principal, com destaque quando uma chamada auxiliar derrubou o
    contexto;
  - caminhos da `como_rodar` (quantas vezes caiu para o modelo do principal, pulou ou usou nuvem);
  - tempo em troca de modelo;
  - ferramentas que mais falham.
- [ ] **Repetir o bench da E0** nas duas configurações e salvar como `docs/bench/<data>-final.json`,
      comparando com o baseline.

**Pronto quando:** existe um número de antes e de depois para o pipeline inteiro.

---

## E12: sandbox para executar código

**Por quê:** hoje a única proteção é a aprovação. O próprio código diz: "a proteção é a aprovação
(always_ask), não um sandbox: o shell enxerga o sistema inteiro" (`shell.py:5`, e o mesmo em
`native.py` e `workspace.py`). Situação atual:
- **Arquivos:** as ferramentas de arquivo ficam presas à pasta do projeto (`resolve_path`,
  `tools.py:196`), mas o `run_command` lê e escreve em qualquer lugar do disco.
- **Processos:** há timeout e o kill mata a árvore, mas não há limite de CPU, RAM nem número de
  processos.
- **Rede:** nenhuma restrição. `curl`/`wget` só pedem aprovação por regex, e `npm install` (com os
  scripts de `postinstall`), `pip install` e `python -c "urllib…"` acessam a rede livremente.
- **Comandos perigosos:** a regex `DESTRUCTIVE` (`policy.py:63`) pede aprovação até no modo Ignorar
  permissões. Há ainda os hooks `pre_tool` e o revisor automático. Mas regex não pega
  `python -c "import shutil; shutil.rmtree(…)"` nem um script que o agente escreveu e depois executa.
- **Web:** o container monta o disco C inteiro (`HOST_MOUNTS: C=/host/c` no `docker-compose.yml`), e o
  `run_command` pode rodar no PC do usuário pelo forja-runner.

Para o Maestro fazer 20 tarefas sozinho, o usuário precisa do modo Automático ou Ignorar permissões, e
nesses modos a aprovação quase some. Um `postinstall` malicioso roda com o disco inteiro e a rede. **O
sandbox é o que torna o modo autônomo seguro.**

**Depende de:** nada para os passos 1 e 2. O passo 3 depende do 1 e do 2. O passo 4 fica para depois.
Deve estar pronto (pelo menos o passo 3) antes de o Maestro autônomo virar o fluxo recomendado.

### Passo 1: Job Object do Windows em todo processo do agente
- [x] Todo processo criado por `run_command`, `serve_start` e `terminal_open` (e pelo `verify_command`
      e pela regressão, E1/E2) entra num **Job Object** criado pelo Forja (via `ctypes`, sem
      dependência nova):
  - `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: a árvore inteira morre quando o comando termina, é cancelado
    ou o backend cai. Nada de processo órfão;
  - limite de memória por job (padrão 4 GB, ou 50% da RAM, o que for menor);
  - limite de processos ativos (padrão 64), contra fork bomb;
  - limite de CPU (`CpuRate`, padrão 80%), para o PC continuar usável.
- [x] Os limites entram nos perfis de hardware (E4) e ficam configuráveis. Estouro de limite aparece
      para o modelo como erro claro ("o comando passou do limite de memória de 4 GB do sandbox").
- [x] Linux/macOS (web e runner): o equivalente é `setrlimit` + grupo de processos (`os.setsid` +
      `killpg`).
- [x] Testes: processo filho de um filho morre com o job; o limite de memória derruba um script que
      aloca além dele; o limite de processos barra uma fork bomb.

### Passo 2: ambiente limpo para os processos
- [x] O processo filho **não herda** o ambiente do backend. Hoje ele recebe tudo:
  - fica de fora qualquer variável com `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, `FORJA_*`,
    `ANTHROPIC_*`, `OPENAI_*`, `AWS_*`, `AZURE_*`, `GITHUB_TOKEN`, `HF_TOKEN`;
  - só passa uma lista do que é necessário: `PATH`, `SystemRoot`, `TEMP`, `USERPROFILE`, `HOME`,
    `LANG`, as de toolchain (`JAVA_HOME`, `GOPATH`, …) e o que o FORJA.md do projeto declarar em
    `env_allow`.
- [x] O `.env` do projeto não é carregado pelo Forja. Se o projeto precisar, o próprio comando do
      projeto o lê.
- [x] Testes: uma variável `X_API_KEY` no backend não aparece no `env` do processo; o `env_allow` do
      FORJA.md aparece.

**Passos 1 e 2 feitos em 2026-09-25** (`app/sandbox.py`).

Validado no Forja real (modo agente, backend com `TESTE_API_KEY` no ambiente):
- `$env:TESTE_API_KEY` saiu vazio e o `PATH` continuou;
- um filho aberto com `Start-Process` morreu quando o comando terminou (o arquivo que ele escreveria
  depois de 3 s não apareceu).

Diferenças em relação ao plano:
- **Ambiente com lista de bloqueio, e não de liberação.** Nome com KEY/TOKEN/SECRET/PASSWORD/
  CREDENTIAL, ou com prefixo `FORJA_`, `ANTHROPIC_`, `OPENAI_`, `AWS_`, `AZURE_`, `HF_`… não passa.
  Uma lista de liberação quebraria toolchains que dependem de dezenas de variáveis do sistema.
  `env_allow:` no FORJA.md libera.
- **Limites padrão:**
  - memória automática, metade da RAM até 4 GB;
  - 128 processos (e não 64: builds com node e testes com navegador abrem muitos);
  - CPU a 80%;
  - os três configuráveis em Configurações ("Sandbox: …"). Entram nos perfis quando a E4 existir.
- **O processo entra no job logo depois de criado, e não suspenso.** Um filho aberto no primeiro
  milissegundo escapa, e isso está anotado no código.
- **Linux/macOS:** só o `setrlimit` de memória. O `RLIMIT_NPROC` é por usuário e não por árvore, e
  quebraria o resto do sistema.

### Passo 3: modo sandbox com WSL2 ou Docker (isolamento de verdade)
- [x] **Detecção:** se WSL2 ou Docker estiver disponível, a tela oferece "Executar comandos do agente
      em sandbox". Vem **ligado por padrão nos modos Automático, Ignorar permissões e Maestro**, e
      desligado no Manual, onde o usuário aprova cada comando. Sem WSL/Docker, avisar uma vez, com o
      link de instalação, e seguir com os passos 1 e 2.
- [x] **Arquivos:** só a pasta do projeto é montada (leitura e escrita). O resto do disco não existe
      lá dentro. O cache de pacotes (npm, pip, cargo) fica num volume próprio do Forja, para não baixar
      tudo de novo a cada comando.
- [x] **Rede por fase:**
  - `install` (npm/pnpm/yarn install, pip install, cargo fetch, …): rede **ligada**, de preferência só
    para os registros de pacotes;
  - `build`, `test`, `verify_command` e regressão: rede **desligada** (`--network none`);
  - `serve_start`: rede local só para expor a porta ao navegador do Forja.

  A fase é detectada pelo comando (lista de instaladores conhecidos) e o modelo pode pedir outra
  com um motivo. Pedido de rede fora da fase de install pede aprovação.
- [x] **Processos:** o processo roda como usuário comum (não root), sem `--privileged`, com os limites
      do passo 1 (`--memory`, `--cpus`, `--pids-limit`).
- [x] **Imagem:** base mínima com node, python e git. O projeto pode declarar a própria imagem no
      FORJA.md (`sandbox_image`). A imagem é baixada uma vez, com o tamanho mostrado antes.
- [ ] **Terminal e servidores de dev** também rodam no sandbox, e o `browser_*` acessa a porta exposta.
- [ ] **Web:** o container do backend deixa de montar o disco C inteiro por padrão (`HOST_MOUNTS` vira
      opt-in), e o forja-runner ganha o mesmo modo sandbox.
- [x] Testes:
  - o sandbox não enxerga um arquivo fora do projeto;
  - `curl` falha na fase de test;
  - `npm install` funciona na fase de install;
  - o processo roda sem root;
  - sem Docker/WSL, cai para os passos 1 e 2 com aviso.
- [ ] Validar no app real com o bench da E0 rodando inteiro no sandbox. Medir o custo de tempo contra
      rodar sem sandbox.

**Passo 3 feito em 2026-09-25 (parcial)**, com a configuração "Sandbox isolado (Docker)".

Validado no Forja real (modo agente, Docker Desktop, imagem python:3.12-bookworm):
- o comando rodou num Debian (o modelo usou bash, pela linha no contexto), como uid 1000 (não root);
- viu o arquivo do projeto, mas não o disco do Windows;
- `pip install six` funcionou (fase de instalação, com rede);
- `urlopen` fora da instalação falhou (sem rede);
- nenhum container ficou para trás.

Diferenças em relação ao plano:
- **Desligado por padrão, e não ligado nos modos autônomos.** O Docker Desktop consome RAM que um PC
  rodando IA local pode não ter. As opções são: desligado, só nos modos autônomos (Automático, Ignorar
  permissões e Maestro), e sempre. O Forja **nunca abre o Docker sozinho**: parado, o comando roda no
  Windows e a saída avisa. Imagem ausente é baixada em segundo plano, e até lá o comando roda no
  Windows com aviso.
- **O que vai para o container:** o `run_command` (do agente e do Worker, inclusive o verify) e a
  regressão da E2. O `git` e os hooks continuam no Windows.
- **Servidores de dev (`serve_start`) e o terminal continuam no Windows (item acima em aberto).**
  Assim o navegador do Forja e o celular pela tailnet seguem enxergando o servidor como hoje. Levar
  para o container exige `-p` (publicar a porta no Windows) e o servidor escutar em `0.0.0.0`, e aí o
  acesso pelo celular continua. Fica para quando o isolamento do servidor valer o custo.
- **Imagem:** a `node:22-bookworm` se houver `package.json`, senão a `python:3.12-bookworm`, ou a
  `sandbox_image:` do FORJA.md. São oficiais e maiores que uma imagem mínima (~380 MB comprimida),
  em troca de git, pip e compiladores sem build próprio.
- **Cache de pacotes:** fica em `<dados do Forja>/sandbox-cache`, montado em `/cache`. O pip instala
  no usuário (`PIP_USER`), já que o processo não é root.
- **Web:** o `HOST_MOUNTS` opt-in e o forja-runner com o mesmo modo ficam para o sync com o
  `forja-web`.

**Motor WSL (2026-09-25).** Configuração "Sandbox isolado: qual Docker": Automático (o Docker Desktop
se estiver aberto, senão o Docker Engine dentro do WSL), Docker Desktop ou Docker Engine no WSL, e a
distro do WSL. O motor WSL:
- chama `wsl.exe --exec docker …`. Com `--`, o `wsl.exe` passava a linha pelo shell do Linux e perdia
  as aspas (`$i`, `&&`, `>`), e isso foi pego na medição;
- traduz os caminhos (`C:\x` → `/mnt/c/x`);
- tem as próprias imagens, separadas das do Docker Desktop.

Na tela de Configurações há um **tutorial** para instalar o Docker das duas formas.

Validado no Forja real: com o Docker Desktop fora do ar, o Automático caiu no Engine do WSL (29.1.3). O
comando com aspas, `$i`, `>` e `&&` rodou num Debian, e os arquivos apareceram na pasta do Windows.

Medição (mediana de 5 rodadas, mesma pasta de projeto no disco C):

| Caso | Windows, sem sandbox | Docker Engine no WSL | Docker Desktop |
|---|---|---|---|
| Subir o comando (vazio) | 0,25 s | 0,46 s | — |
| 2000 arquivos (escrever e ler) | 3,52 s | 9,17 s | — |
| pytest (60 testes) | 0,52 s | 3,34 s | — |
| pip install (já em cache) | 1,06 s | 2,17 s | — |

- [ ] **Medir o Docker Desktop.** Ele não subiu nesta sessão: o backend dele caía ao recriar os sockets
      em `%LOCALAPPDATA%\Docker\run`, e isso não tem relação com o Forja. Reiniciar o Windows e rodar
      `scratchpad/bench_sandbox.py` de novo.
- [ ] **Cache de pacotes num volume do Linux.** Hoje o cache (`sandbox-cache`) fica no disco do Windows e
      é lido por `/mnt/c`, e é a maior parte dos 3,3 s do pytest. Um volume nomeado do Docker é nativo
      do Linux, mas nasce de root: precisa de um `chown` para o uid 1000 na criação.

### Passo 4 (depois): AppContainer do Windows
- [ ] Isolamento nativo sem Docker/WSL. O processo roda num **AppContainer**:
  - só a pasta do projeto e o cache de pacotes ganham ACL de acesso;
  - a rede entra como capability (`internetClient`), ligada só na fase de install.
- [ ] Vira o padrão no Windows quando estiver estável, e o Docker/WSL fica como opção para quem precisa
      de ambiente Linux.
- [ ] Riscos a avaliar antes:
  - toolchains que não funcionam em AppContainer (node-gyp, compiladores nativos, npm com links);
  - o custo de dar ACL à pasta do projeto;
  - a interação com o antivírus.

  Fazer um protótipo com `npm install && npm test` num projeto real antes de construir.

**Integração com o resto do plano:**
- **Perfis (E4):** os perfis ligam o sandbox automaticamente conforme o modo de permissão, e os limites
  do Job Object vêm do perfil.
- **Maestro (E1/E2/E3):** o `verify_command`, a regressão e o Worker rodam no sandbox, e o commit por
  tarefa (E2) continua na pasta real, que é a montada.
- **A aprovação continua existindo por cima.** O sandbox reduz o dano de um comando ruim, não substitui
  a `DESTRUCTIVE` nem o revisor.
- **Métricas (E10):** comandos barrados pelo sandbox (rede, limite, arquivo) viram evento.

**Pronto quando:** no modo Maestro autônomo, um `postinstall` que tenta ler `~/.ssh` ou mandar dados
para fora falha, e o bench da E0 continua passando dentro do sandbox.

---

## E13: vários backends locais sem depender do llama.cpp

**Por quê:** o Forja já fala com quatro tipos de servidor (`settings.py:70`):
- `llamacpp`: o embutido;
- `ollama`;
- `lmstudio`;
- `openai`: qualquer API compatível, o que cobre vLLM, `transformers serve`, TGI e servidores externos.

Mas o controle é desigual, e quase tudo que a E4 planeja só funciona no llama.cpp embutido, porque é o
único processo que o Forja controla: política de execução com VRAM e slots, cache em disco, descarga
por ociosidade, padrões de KV e perfis. Além disso, há um bug: para o tipo `openai`, `context_limit`
devolve `None` (`llm.py:96`) e o agente supõe 32768 tokens (`NUM_CTX`). Um vLLM com
`--max-model-len 8192` faz o Forja achar que tem 4× mais janela, e a compactação dispara tarde demais.

**Depende de:** nada. A parte A tem de vir **antes** da política de execução da E4. A parte B vem depois
da E4.

### Parte A: capacidades por backend e janela real (antes da E4)
- [ ] **Tabela de capacidades por tipo de backend**, num lugar só (ex.: `llm.CAPACIDADES[tipo]`), com
      o que o Forja consegue fazer em cada um:
  - carregar e descarregar modelo;
  - ler a VRAM;
  - controlar slots e paralelo;
  - salvar e restaurar o cache;
  - ler os timings de cache;
  - ler a janela real;
  - mudar parâmetros de carga.

  | Capacidade | llamacpp (embutido) | ollama | lmstudio | openai (genérico) |
  |---|---|---|---|---|
  | carregar/descarregar | sim | parcial (`keep_alive`, `/api/generate` vazio) | parcial (API `lms`/REST v0) | não |
  | ler VRAM | sim (`hardware()`) | parcial (`/api/ps` dá o tamanho em VRAM) | não | não |
  | slots/paralelo | sim | não (`OLLAMA_NUM_PARALLEL` é do servidor) | não | não |
  | cache em disco | sim | não | não | não |
  | timings de cache | sim (`cache_n`) | parcial (`prompt_eval_count`) | parcial | parcial (`cached_tokens`, se vier) |
  | janela real | sim | sim (`num_ctx` enviado / `/api/ps`) | sim (`loaded_context_length`) | **hoje não** (ver abaixo) |

  Os valores "parcial" precisam ser confirmados na versão atual de cada servidor antes de entrar na
  tabela.
- [ ] **A `como_rodar` e os perfis (E4) leem essa tabela.** Quando o backend não oferece a capacidade,
      o comportamento é o seguro: um modelo só, chamadas auxiliares em sequência, sem trocar de modelo
      e sem cache em disco. Nunca tentar uma operação que o backend não tem.
- [ ] **A tela mostra o que fica indisponível** no backend escolhido. Exemplo: "com LM Studio: sem cache
      em disco, sem paralelo controlado pelo Forja, descarga pelo próprio LM Studio".
- [ ] **Janela real no tipo `openai`:**
  - ler `max_model_len` do `/v1/models` (é o campo que o vLLM informa);
  - ler `context_length`/`max_context_length` quando o servidor informar;
  - sem nenhum dos dois, usar um campo manual **"janela de contexto"** no cadastro do servidor, que
    passa a ser obrigatório para o tipo genérico. **Nunca supor 32k.**
- [ ] **Janela real no Ollama:** conferir a janela carregada de fato em `/api/ps`, em vez de só confiar no
      `num_ctx` enviado. O Ollama pode limitar a janela pela memória.
- [ ] Testes com servidor falso de cada tipo: a janela lida corretamente; um `openai` sem janela e sem
      campo manual recusa com mensagem clara; a `como_rodar` com um backend sem slots escolhe
      sequencial.

### Parte B: controle e métricas fora do llama.cpp (depois da E4)
- [ ] **Cache perdido em qualquer backend** (E0/E10): Ollama por `prompt_eval_count` contra o tamanho do
      prompt; vLLM e outros compatíveis por `usage.prompt_tokens_details.cached_tokens`, quando vier.
- [ ] **Ollama e LM Studio com controle parcial:**
  - a descarga por ociosidade (E4) usa `keep_alive: 0` no Ollama e o unload da API do LM Studio;
  - a troca de modelo do Worker (E3) também, quando a política permitir.
- [ ] **vLLM e `transformers serve`:** documentar no README como apontar o tipo `openai` para cada um
      (URL, `--max-model-len`, se o tool calling precisa de flag). Sem tipo novo enquanto o genérico
      bastar.
- [ ] **Testes de paridade:** uma conversa curta com duas ferramentas, rodada contra um servidor falso de
      cada tipo (streaming, tool call, erro de janela cheia). Toda mudança da E4 precisa passar nos
      quatro. Reaproveitar o `test_paridade.py`.

**Pronto quando:** trocar do llama.cpp embutido para Ollama, LM Studio ou vLLM não quebra nenhum fluxo,
o Forja sabe a janela real em todos, e a tela diz o que cada backend não oferece.

---

## E14: preferências do projeto (o harness aprende o estilo do projeto)

**Por quê:** o Worker começa cada tarefa do zero, só com o contrato, o FORJA.md e o guia visual. O que o
projeto prefere precisa estar ali, senão o modelo pequeno inventa um estilo por tarefa. A base existe:
- o FORJA.md, com "convenções", injetado em toda conversa (`agent.py:898`);
- o portão do `plan_feature`, que exige FORJA.md e guia visual (`taskdb.py:772-779`);
- a pasta `.forja/knowledge/`.

Mas as convenções só entram se a LLM do Maestro lembrar de escrevê-las. Nada as extrai sozinho. Serve
principalmente ao Maestro e ao Worker. O modo agente também ganha, porque lê o mesmo arquivo.

**Depende de:** a parte 1 não depende de nada. A parte 2 depende da E1 (verify) e da E8 (revisor). A
parte 4 depende da E1.

### Parte 1: detectar pelo código, sem LLM
- [ ] Um detector (`projstate.detectar_convencoes(root)`) lê o que o projeto já declara:
  - `tsconfig.json` com `strict`/`noImplicitAny` → "TypeScript strict";
  - `tailwind.config.*` ou `@tailwind` no CSS → "Tailwind";
  - `vitest`/`jest`/`pytest` nas dependências → o framework de testes, que já sugere o `verify_command`;
  - ESLint, Prettier, Ruff, Black, `.editorconfig` → estilo e lint;
  - a estrutura de pastas (`services/`, `components/`, `hooks/`, `repositories/`) → como o código é
    separado;
  - o gerenciador de pacotes, pelo lockfile (npm/pnpm/yarn/bun, pip/uv/poetry).
- [ ] Roda ao abrir um projeto e antes do `plan_feature`. O resultado entra em
      `.forja/knowledge/convencoes.md` na seção "detectado", sempre com a origem (ex.: "TypeScript strict
      — `tsconfig.json:5`"). Quando o arquivo de origem muda, a entrada é refeita.
- [ ] Testes com projetos de fixture (TS + Tailwind + Vitest, Python + Ruff + pytest): cada convenção é
      detectada com a origem certa.

### Parte 2: aprender com o que deu errado
- [ ] Sinais que viram **candidatas**, cada uma com a evidência:
  - verify que falhou por lint ou tipo;
  - critério reprovado pelo revisor (E8);
  - o usuário editando um arquivo que o Worker acabou de entregar (diff entre o commit da tarefa, E2,
    e a edição seguinte);
  - uma correção explícita no chat ("não use classes", "sempre async/await").
- [ ] **A candidata só vira regra** depois de 2–3 ocorrências do mesmo tipo, ou quando o usuário
      confirma. Isso evita gravar como preferência algo que foi acaso.
- [ ] Na conversa, a confirmação é uma pergunta curta ("percebi X em 3 tarefas; vira regra do
      projeto?"), no máximo uma por execução, para não interromper o Maestro autônomo. No modo
      autônomo, as candidatas ficam numa lista para o usuário revisar depois.
- [ ] As regras aprendidas ficam em `convencoes.md` na seção "aprendido", com a data, as evidências e um
      contador. O usuário pode apagar ou editar à mão.

### Parte 3: mandar só o relevante no contrato
- [ ] Cada regra tem uma **área**: `frontend`, `backend`, `testes`, `geral` ou um glob (`src/**/*.tsx`).
- [ ] O contrato do Worker (`taskdb.py:135`) leva só as regras cujas áreas casam com os arquivos da
      tarefa, com um teto proporcional à janela (E4).
- [ ] O explorador (E11) também recebe as regras da área que vai ler, para o relatório já apontar os
      desvios.

### Parte 4: virar checagem sempre que der
- [ ] Com modelo pequeno, a regra escrita no prompt é ignorada com frequência, e um lint não. Para cada
      regra, o Forja tenta achar uma checagem automática:
  - "TypeScript strict" → `tsc --noEmit`;
  - "evitar classes" → regra do ESLint (`no-restricted-syntax` para `ClassDeclaration`);
  - "async/await" → `prefer-promise-reject-errors`/regra equivalente, ou um grep simples em `.then(`;
  - "testes Vitest" → `vitest run` no verify.
- [ ] A checagem é **sugerida**, nunca instalada sozinha:
  - acrescentar ao `verify_command` das tarefas daquela área (E1);
  - ou adicionar a regra ao config de lint do projeto.

  Mudar o config do projeto exige aprovação. Acrescentar ao verify, não.
- [ ] Regra sem checagem possível fica só no contrato, marcada como "sem checagem": o revisor (E8)
      confere essas regras de propósito.

- [ ] Testes: a candidata vira regra só depois de N ocorrências; o contrato de uma tarefa `.py` não leva
      regra de frontend; "TypeScript strict" sugere o `tsc --noEmit` no verify.

**Pronto quando:** depois de ~5 tarefas no bench da E0, o contrato do Worker já traz as convenções
detectadas e aprendidas, e os desvios de estilo caem nas tarefas seguintes (medido pelo revisor e pelo
lint).

---

## E15: board de issues com varredura automática do projeto

**Ideia:** uma página nova de gerenciamento do projeto, no estilo Jira ou Azure Boards. Uma IA varre a
base de código procurando bugs, TODOs, telas faltando, visual quebrado e ideias. Cada achado vira um card
no backlog, com tags (bugfix, feature, improvement, visual) e um **prompt pronto**. Um botão "Iniciar"
entrega o card ao modo agente ou ao Maestro.

**O risco principal é falso positivo.** Um modelo pequeno varrendo o código acha muito "bug" que não é.
Sem controle, o backlog vira lixo em uma semana. As três proteções abaixo são obrigatórias em todas as
partes:
- **Evidência obrigatória:** todo card gerado tem `arquivo:linha`, a saída de um comando ou um
  screenshot. Sem evidência, o card não é criado.
- **Triagem humana:** o que a varredura acha cai na coluna "Novo" e só vai para o Backlog quando o
  usuário aceita.
- **Impressão digital:** tipo + arquivo + trecho normalizado. Um card rejeitado ou já existente não é
  recriado na próxima varredura. Rejeitar tem o motivo opcional "não é bug" / "não quero" / "duplicado",
  e a varredura com IA recebe os motivos recentes para errar menos.

**Depende de:** a parte A não depende de nada. A parte B depende da E4 (política de execução), da E5, da
E11 (explorador) e da E14 (convenções). A parte C depende da E1, da E2 e da E12.

### Parte A: MVP sem LLM (board, cards e o botão Iniciar)
- [ ] **Tabela `issues` por projeto** (pela pasta raiz, não por conversa como `Feature`/`Task`,
      `db.py:114`). Campos:
  - `id`, `projeto` (raiz), `titulo`, `descricao`;
  - `tipo` (bugfix | feature | improvement | visual | todo | seguranca);
  - `area` (frontend | backend | fullstack | testes | infra);
  - `severidade` (1–3);
  - `status` (novo | backlog | andamento | revisao | concluido | rejeitado);
  - `evidencias` (JSON com `arquivo:linha`, saída, caminho do screenshot);
  - `prompt`, `verify_sugerido`, `origem` (manual | varredura-deterministica | varredura-ia | visual);
  - `impressao`, `motivo_rejeicao`, `conversa_id`, `feature_id`, `commit`, datas.
- [ ] **Tela "Projeto › Board"** (componente novo, ao lado do `MaestroView.tsx`):
  - colunas Novo → Backlog → Em andamento → Revisão → Concluído, com arrastar entre colunas;
  - filtros por tipo, área e severidade;
  - uma busca;
  - contagem por coluna.
- [ ] **Card:** título, tags coloridas por tipo e área, severidade e origem. Aberto, mostra:
  - as evidências clicáveis (abre o arquivo na linha, ou o screenshot);
  - o prompt, editável;
  - o `verify` sugerido;
  - o histórico.
- [ ] **Criar card à mão** ("+ Novo item"), com o mesmo formulário.
- [ ] **Botão "Iniciar":**
  - sugere o modo: bugfix, todo e visual pequenos → **agente**; feature, ou área fullstack →
    **Maestro**. O usuário pode trocar na hora;
  - cria uma conversa nova na pasta do projeto com o prompt do card, o `verify` e as evidências, e
    liga o card a ela (`conversa_id`);
  - o card vai para "Em andamento" e mostra o andamento da conversa (tarefas do Maestro, se houver).
  - **Fim:** quando a conversa termina (a feature do Maestro virou `done`, ou o turno do agente acabou
    com o verify passando), o card vai para "Revisão", com o commit (E2, quando existir). O usuário
    aprova e ele vai para "Concluído", ou reabre com um comentário que vira mensagem na mesma conversa.
- [ ] **Varredura determinística (camada 1)**, sem LLM, pelo botão "Varrer agora":
  - `TODO`/`FIXME`/`HACK`/`XXX` pelo `grep` (`busca.py`) → tipo `todo`, com o texto do comentário;
  - erros de `tsc --noEmit`, lint e testes que falham (comandos do FORJA.md; depois da E14, os
    detectados) → `bugfix`, com a saída;
  - `npm audit`/`pip-audit` → `seguranca`, só as severidades alta e crítica;
  - erros de console e requisições com falha nas rotas conhecidas, por `browser_validate` → `bugfix`,
    área frontend.

  Um card por problema, com impressão digital. Reexecutar não duplica, e um problema que sumiu marca
  o card "resolvido?" para o usuário confirmar.
- [ ] **Sincronizar com o celular** (regra do `CLAUDE.md`): card novo, mudança de coluna e fim da
      varredura entram no `/api/activity`. O Forja Mobile ganha a lista do board para triar pelo
      celular (aceitar, rejeitar, iniciar). Testar nos dois sentidos com o celular de verdade.
- [ ] Testes:
  - a impressão digital evita duplicado;
  - rejeitado não volta;
  - um `TODO` detectado vira card com `arquivo:linha`;
  - "Iniciar" cria a conversa no modo certo com o prompt;
  - o card segue o status da conversa.

### Parte B: varredura com IA (camadas 2 a 4)
- [ ] **Camada 2, código:** o explorador só de leitura (E11), com `tree`/`ast`/`imports` (E5), procura:
  - bugs prováveis;
  - telas faltando (rota sem tela, botão ou link sem ação, formulário sem validação);
  - código morto (`imports`: ninguém importa);
  - desvios das convenções do projeto (E14);
  - ideias de melhoria.

  Cada achado **precisa** trazer `arquivo:linha` e um trecho. Achado sem isso é descartado.
- [ ] **Varredura incremental:** só os arquivos mudados desde a última varredura (`git diff` contra o
      commit da última varredura, guardado no projeto). A primeira varredura de um repo grande é
      dividida por pasta e pode ser retomada. Varrer tudo a cada vez é inviável com modelo local.
- [ ] **Camada 3, visual:** `visual_review` (`qualidade.py:151`) nas páginas principais, em desktop e
      mobile, com o screenshot salvo como evidência. Sem modelo de visão que caiba (política da E4),
      a camada é pulada e o motivo aparece.
- [ ] **Camada 4, triagem:** uma chamada que:
  - junta achados repetidos;
  - classifica tipo, área e severidade;
  - escreve o **prompt pronto**: contexto, arquivos, critério de aceite e o `verify_command` sugerido,
    no formato que o `plan_feature` e o contrato do Worker esperam;
  - recebe os motivos de rejeição recentes do projeto, para não repetir o mesmo falso positivo.
- [ ] **Custo e momento:**
  - a varredura é um papel da `como_rodar` (E4), `varredura`, de **prioridade mínima**: só roda com o
    modelo ocioso e para (salvando onde estava) quando o usuário ou o Maestro precisam do modelo.
    Nunca troca de modelo;
  - agendamento opcional (ex.: toda noite, ou N minutos depois do último commit), mais o manual;
  - teto de cards por varredura (padrão 20). O resto fica numa fila "mais achados", para não inundar a
    triagem.
- [ ] **Qualidade medida:** a taxa de aceite dos cards da varredura com IA, por camada e tipo, entra nas
      métricas (E10). Uma camada com aceite abaixo de ~30% fica desligada por padrão naquele projeto,
      com um aviso.
- [ ] Testes: achado sem `arquivo:linha` é descartado; a varredura incremental só lê os arquivos
      mudados; a varredura para quando o principal pede o modelo; o teto de cards é respeitado.

### Parte C: execução automática ("sozinho", de ponta a ponta)
- [ ] **Opção "Executar backlog automaticamente"**, por projeto e desligada por padrão. Pega os cards
      do Backlog, por severidade e depois por ordem, e inicia um de cada vez no modo sugerido, sem
      clique.
- [ ] **Só pode ser ligada com o sandbox (E12, passo 3 ou 4) ativo** e com as travas da E1/E2 (verify
      obrigatório, commit por tarefa, regressão). Sem isso, o interruptor fica desabilitado com o
      motivo.
- [ ] **Só cards aceitos pelo usuário.** O que está em "Novo" nunca é executado sozinho, mesmo com a
      opção ligada.
- [ ] **Limites:**
  - no máximo N cards por dia (padrão 5);
  - parar na 1ª falha que acabar em `needs_human`;
  - nunca executar card `seguranca` sem aprovação.
- [ ] **Resultado sempre passa pela coluna Revisão.** Concluído só com a aprovação do usuário. O push no
      celular avisa "card X pronto para revisão".
- [ ] Testes: a opção não liga sem sandbox; um card em "Novo" não é executado; o limite diário é
      respeitado; parar em `needs_human`.

**Pronto quando:**
- Parte A: o board funciona no PC e no celular, com a varredura determinística e o Iniciar.
- Parte B: uma varredura com IA num projeto real gera cards com evidência e ≥ 50% de aceite.
- Parte C: um card aceito vira commit revisável sem nenhum clique além da aprovação final.

---

## E16: recuperação de loop e alucinação (trabalhar a noite toda sem parar à toa)

**Por quê:** hoje o problema é duplo. O modo agente **para de vez** com facilidade e exige que o usuário
peça para continuar. O Maestro **nunca para, mas também nunca se recupera**: fica girando com os mesmos
lembretes até as 500 iterações.

Onde o modo agente encerra o turno hoje:

| Onde | Quando | O que acontece |
|---|---|---|
| Limite de passos (`agent.py:1326`) | 25 passos (`MAX_ITERATIONS`) × o multiplicador de esforço (**10** no esforço baixo) | "O agente parou." Para mesmo avançando bem |
| Chamada repetida (`agent.py:1565`, `parsing.py:156`) | Mesma ferramenta e mesmos argumentos 10× seguidas | Lembretes na 3ª, 5ª e 8ª; na 10ª, interrompe |
| Turno "mudo" ou promessa sem ação (`agent.py:1475`) | Só raciocina, ou diz "vou fazer X" e não chama nada | Dois lembretes (`MAX_NUDGES=2`) e para |
| Hook `stop` | Bloqueia 3× | Para |

O raciocínio só tem um teto fixo por esforço (`REASONING_BUDGET`, `config.py:53`: 1024/2048/4096). Isso
corta quem pensa bem e deixa gastar o teto inteiro quem começou a girar cedo.

**Pontos cegos do detector:** ele só pega a mesma chamada com os mesmos argumentos em sequência. Deixa
passar:
- A, B, A, B alternando;
- o mesmo erro voltando com chamadas diferentes;
- passos que não produzem nada novo;
- sinais de alucinação: ferramenta inexistente, arquivo que não existe várias vezes, `edit_file` com
  `old_str` que não está no arquivo, ou "já testei" sem nenhum `run_command` que passou.

**Regra da entrega:** o harness só para quando tentou se recuperar e não conseguiu, e **nunca em
silêncio**.

**Depende de:** a parte A não depende de nada. Na parte B, o nível 3 usa a E4 (compactação dentro do
turno), o nível 4 usa a E2 (commit por tarefa) e o juiz usa a E4 (`como_rodar`). A parte C combina com o
sandbox (E12).

### Parte A: placar de progresso, níveis 1–2 e filtro do raciocínio (sem LLM)
- [ ] **Placar de progresso por passo.** Conta como progresso:
  - um arquivo mudou sem desfazer uma mudança anterior (comparar com o hash do conteúdo de antes);
  - um verify ou teste que falhava passou;
  - uma tarefa foi concluída;
  - um resultado de ferramenta diferente dos anteriores (hash do resultado normalizado);
  - uma mensagem do usuário.

  Guardar em `Run` junto do `LoopDetector`.
- [ ] **Detector ampliado.** Além da repetição exata:
  - ciclos curtos (período 2–4) na sequência de chamadas;
  - a mesma assinatura de erro (primeira linha do erro, sem números nem caminhos) voltando ≥ 3×;
  - N passos seguidos sem progresso (padrão 8);
  - **sinais de alucinação**, cada um somando pontos:
    - ferramenta que não existe;
    - `path` inexistente repetido;
    - `edit_file` com `old_str` não encontrado 2× no mesmo arquivo;
    - afirmação de "testei/verifiquei/passou" sem um `run_command` ok desde a última escrita (reaproveitar
      o `detect_promise` e o item "Não afirme que algo foi verificado" que já existe).
- [ ] **Nível 1, lembrete:** o que existe hoje, disparado também por 4 passos sem progresso.
- [ ] **Nível 2, intervenção:** com 5 repetições, 8 passos sem progresso ou a pontuação de alucinação
      acima do limite, o harness injeta uma mensagem estruturada:
      "Você está em loop: fez X N vezes; resultado: Y; estado: arquivos mudados, testes. Escreva em 3
      linhas o que está errado e escolha uma abordagem **diferente**."
  - **Proibir de verdade** a chamada exata pelos próximos K passos (padrão 5): se o modelo repetir, a
    ferramenta recusa com a mensagem "chamada bloqueada pela recuperação de loop; escolha outra ação".
  - O próximo turno sai com o teto de raciocínio menor, para o modelo agir em vez de pensar.
- [ ] **Filtro do raciocínio durante o streaming**, a cada ~500 tokens de raciocínio:
  - **compressão:** `zlib` nos últimos ~4k caracteres. Razão abaixo de ~0,25 = texto repetitivo;
  - **frases repetidas:** a mesma frase ou n-grama longo (≥ 12 palavras) 3× ou mais;
  - **marcadores de hesitação em série:** "wait", "actually", "hmm", "espera", "na verdade" acima de N
    por mil tokens.

  Degeneração clara: **aborta a geração** e vai para o nível 2. A geração não é pausada: o llama.cpp
  não retoma um raciocínio pela metade de forma confiável, então a análise roda sobre o texto que já
  saiu e só aborta se precisar.
- [ ] **Mediana de raciocínio por modelo:** guardar a mediana de tokens de raciocínio por turno de cada
      modelo (`model_setting`). "Pensar muito" passa a ser relativo ao modelo (> 3× a mediana), não um
      número fixo. Um modelo que normalmente pensa 3k não é suspeito aos 2k.
- [ ] Testes:
  - ciclo A,B,A,B detectado;
  - mesmo erro com chamadas diferentes detectado;
  - chamada bloqueada é recusada nos K passos;
  - texto repetitivo aborta a geração (stream falso);
  - raciocínio longo e variado **não** aborta.

### Parte B: níveis 3–5 e o juiz do raciocínio
- [ ] **Nível 3, contexto limpo:** se o loop continua depois do nível 2, o próprio histórico do loop
      está reforçando o erro. Resumir a parte repetida em poucas linhas, com a compactação dentro do
      turno da E4, e seguir a partir do resumo. Opcional: fazer essa etapa no slot `capaz`, via
      `como_rodar`, que no Low VRAM usa o mesmo modelo, sem troca.
- [ ] **Nível 4, recuo:** voltar os arquivos ao último ponto bom (commit da E2).
  - No **Maestro:** a tarefa vira `needs_human`, com o diagnóstico, e ele **segue para a próxima tarefa
    independente**. Uma tarefa travada não derruba a noite.
  - No **modo agente:** volta ao último ponto bom e tenta uma vez com outra abordagem, antes do nível 5.
- [ ] **Nível 5, estacionar:** nada funcionou, ou tudo o que resta depende do que travou. Gravar
      `session_note` (onde parou, o que tentou, o que precisa do usuário), parar de forma organizada e
      mandar **push para o celular** (`mobile.py`) com o resumo.
- [ ] **O Maestro passa a usar a mesma escada.** Os alertas de hoje ("pode estar em loop: confira",
      `agent.py:1481` e `1561`) viram os níveis 1–2, e os níveis 3–5 substituem o girar até 500.
- [ ] **Juiz do raciocínio (LLM), só quando o filtro da parte A fica em dúvida:** raciocínio acima de
      3× a mediana do modelo, sem degeneração visível.
  - Recebe: o pedido, o começo do raciocínio, um trecho do meio e os últimos ~2k caracteres.
  - Classifica em `progredindo`, `girando` ou `alucinando` (fatos inventados sobre o código, arquivos
    que não existem).
  - **Leitura das probabilidades das opções, no estilo do SemIf, sem gerar JSON.** O prompt do juiz
    termina com as três opções, cada uma começando por um token distinto. O llama-server devolve as
    probabilidades do próximo token (`n_probs`/`logprobs`, 1 token de saída), e o veredito vem com a
    confiança (ex.: `girando 0,82`). Não há texto para interpretar nem JSON para consertar, e o custo
    é praticamente só processar o prompt do juiz.
  - **Só age acima de um limite de confiança** (padrão 0,7, calibrado pela E10). Abaixo disso, conta
    como `progredindo`.
  - Backend sem logprobs (tabela de capacidades da E13): cai para uma resposta de uma palavra com
    gramática (GBNF/`json_schema`), sem confiança, e passa a exigir a concordância do filtro da parte A
    para abortar.
  - Uma linha de motivo, opcional, só quando o veredito é `girando`/`alucinando`, para ir na mensagem
    do nível 2.
  - **Roda em paralelo, sem pausar o modelo principal**, pela `como_rodar("juiz", …)` (E4). Com slots
    livres, no mesmo modelo; no Low VRAM com `-np 1` não há como rodar junto, e fica só o filtro da
    parte A mais o teto.
  - `progredindo` → **aumenta o teto de raciocínio** daquele turno (até 2× o teto do esforço). O teto
    passa a ser adaptativo.
  - `girando`/`alucinando` → aborta e vai para o nível 2.
  - Um falso positivo só custa um lembrete e um turno mais curto, então é aceitável.
- [ ] Testes:
  - nível 4 no Maestro segue para a próxima tarefa independente;
  - nível 5 grava `session_note` e manda o push;
  - o juiz `progredindo` estende o teto;
  - sem slot livre o juiz é pulado;
  - servidor falso com logprobs: a confiança abaixo do limite não aborta;
  - backend sem logprobs cai para gramática e exige o filtro.

### Parte C: modo autônomo (noite toda)
- [ ] **O limite de passos vira checkpoint, não parede.** No modo autônomo, ao chegar em
      `MAX_ITERATIONS`, olhar o placar: com progresso recente, continuar sozinho até o orçamento; sem
      progresso, entrar na escada.
- [ ] **Configuração "Trabalho autônomo"**, por conversa e global:
  - **recuperação automática** ligada/desligada. Desligada, fica como hoje;
  - **orçamento:** teto de horas, passos ou tokens. No teto, estaciona com relatório (nível 5);
  - **`ask_user` sem ninguém olhando:** escolhe a opção recomendada e registra a escolha, ou anota a
    pergunta e segue com outra tarefa. Padrão: anotar e seguir;
  - **notificar a partir do nível:** padrão nível ≥ 4, e sempre no fim, com o relatório "feito / travou
    / por quê".
- [ ] **Relatório da manhã:** ao terminar ou estacionar, uma mensagem final na conversa (e no push) com:
      tarefas feitas, níveis acionados ("3 intervenções, 1 recuo, 0 estacionamentos"), o que precisa do
      usuário e o tempo gasto.
- [ ] **Métricas (E10):** cada nível acionado, cada veredito do juiz e cada abort do filtro viram evento.
      Serve para calibrar os limites (8 passos, razão 0,25, 3× a mediana).
- [ ] Validar no app real: deixar o bench da E0 rodando em modo autônomo com um modelo pequeno e um
      pedido que força loop (ex.: teste impossível de passar). Tem de estacionar com relatório e push,
      sem ficar parado em silêncio nem girar até o fim do orçamento.

### Parte D (experimento): comparar juízes

**Padrão:** a leitura das probabilidades das opções no **modelo já carregado** (parte B), porque não
precisa de dependência nem de VRAM extra. A parte D só existe para ver se algo supera esse padrão.

**Candidatos avaliados em 2026-09-25**, com os números do comparativo do blog do Hugging Face
(`sora-2`, 2026-09-23, JevBench v1.3.0). O benchmark é ligado ao Jev e não é independente, então serve
só para escolher o que testar:

| Candidato | Ranking | Roda num PC comum? | Decisão |
|---|---|---|---|
| Jev (TypeSafe AI) | #1 (74,4) | não: API fechada na nuvem | fora, porque mandaria raciocínio e código para fora. No máximo uma opção de nuvem, desligada |
| **SemIf** (`TheoLeeCJ/SemIf-OpenJev`, MIT) | #2 (73,1), 95,2% no nível "judge", 59,5% no difícil | **sim**: tem backend llama.cpp/GGUF, usa o Qwen3.5-4B | **testar**. A técnica dele é a mesma da parte B |
| djev (`Davipar/djev-dev`) | #3 (73,0) | não: DiffusionGemma de 26B, recomendado numa NVIDIA B200, com vLLM modificado | fora |
| OpenJev (`razorback16/openjev`) | #11 (66,4) | não para 8 GB: exige uma GPU de 24 GB | fora |
| Laya (Apache 2.0) | #33 (54,4), 34,1% no difícil | sim, na CPU | testar só para o Low VRAM com `-np 1` (ver abaixo) |

- [ ] **Candidato 1: leitura de probabilidades no modelo carregado** (o padrão da parte B). É a
      referência da comparação.
- [ ] **Candidato 2: SemIf com o Qwen3.5-4B em GGUF.** Um modelo dedicado de juiz, carregado só se
      couber junto na VRAM (`como_rodar("juiz", …)`, E4). Serve para ver se um juiz separado, que não é
      o próprio modelo que está girando, julga melhor. O SemIf reporta ~1 s por decisão numa RTX 3090,
      e < 100 ms reaproveitando o prefixo. Se ganhar, a técnica já está no Forja e muda só o modelo.
- [ ] **Candidato 3: Laya na CPU**, só para o caso em que o juiz no llama.cpp não consegue rodar em
      paralelo (Low VRAM com `-np 1`). Detalhes abaixo.

**Laya** (`convaiinnovations/laya`, Apache 2.0, `pip install laya`, lançado em 2026-09-18) é uma
reprodução aberta do Jev (TypeSafe AI, fechado). É um **encoder** que não gera texto:
- ModernBERT-large com 421M de parâmetros (inglês, 512 tokens) ou mmBERT-base com 322M (multilíngue,
  1024 tokens);
- recebe um texto, uma pergunta e opções fechadas, e devolve probabilidades (`choice`, `score`, `noul`);
- ~33 ms numa T4, roda em CPU, ~1,3–1,7 GB por checkpoint, via Hugging Face transformers.

**Por que não é o juiz agora:**
- **Não é um detector de alucinação.** O "não alucina" do marketing quer dizer só que a saída sempre
  cabe nas opções dadas, não que o julgamento está certo.
- **Sem fine-tuning, o modelo base fica perto do aleatório** (~0,362 contra 0,318 aleatório). Os
  números bons são de checkpoints especializados.
- **A janela é pequena** (512/1024 tokens) para o trecho de raciocínio que o juiz precisa ver.
- Um encoder de 322–421M não sabe se um arquivo ou função do projeto existe, então não detecta
  "alucinando" no sentido de fato inventado. No máximo detecta o **padrão** de raciocínio girando, que
  o filtro `zlib` da parte A já pega de graça.
- **Custo de empacotamento:** exige PyTorch e transformers no Python portátil, o que acrescenta
  centenas de MB (CPU) a GBs (CUDA) ao instalador.

**Onde ele poderia ganhar:** roda na **CPU**, sem disputar a VRAM nem o slot do llama.cpp. No Low VRAM
com `-np 1`, onde o juiz LLM não roda, isso seria a única forma de ter um juiz em paralelo.

- [ ] **Só começar depois da parte B e da E10**, com dados. Juntar um conjunto rotulado a partir dos
      vereditos do juiz LLM e da confirmação do usuário (o lembrete ajudou? o abort era certo?): trechos
      de raciocínio `progredindo` / `girando`.
- [ ] Fazer o fine-tuning do checkpoint multilíngue (português) só para `girando` vs `progredindo`,
      sem `alucinando`.
- [ ] **Avaliar contra o filtro `zlib` + o juiz LLM** no mesmo conjunto: precisão, recall e latência
      na CPU de um PC comum (não numa T4).
- [ ] **Só entra no produto se:** ganhar do filtro da parte A em recall sem perder precisão, e rodar em
      menos de ~300 ms na CPU. Mesmo assim, fica como **complemento opcional baixado sob demanda**, num
      processo separado (ou exportado para ONNX + onnxruntime, se o repositório permitir), para não
      colocar PyTorch no backend principal.
- [ ] **Comparação final, os três no mesmo conjunto rotulado:**
  - precisão e recall em `girando` vs `progredindo`;
  - calibração (a confiança bate com o acerto?);
  - latência num PC comum;
  - VRAM e RAM extras;
  - tamanho acrescentado ao instalador.

  Resultado em `docs/bench/<data>-juizes.json`.
- [ ] **Regra de decisão:** o padrão continua sendo a leitura de probabilidades no modelo carregado, a
      menos que outro candidato ganhe em recall sem perder precisão **e** caiba no perfil de hardware.
      Se ninguém passar, registrar os números e encerrar o experimento.

**Pronto quando:**
- uma noite de trabalho autônomo com modelo pequeno termina com as tarefas feitas ou estacionada com
  relatório e push;
- nenhuma parada fica sem motivo e sem aviso;
- o bench mostra menos voltas sem progresso que o baseline da E0.

---

## E17: o Forja como servidor MCP (o Claude como Maestro e autor dos cards)

**Ideia:** o Claude (Claude Code ou Claude Desktop) se conecta ao Forja por MCP. Ele planeja, revisa e
escreve os cards do board, e o trabalho pesado fica com os Workers locais do Forja (llama.cpp).
- Consome poucos tokens do Claude e usa a assinatura do Claude Code, sem chave de API.
- Os cards saem melhores que os de um modelo local pequeno, porque o Claude Code varre o repo com as
  ferramentas dele e manda evidência com `arquivo:linha`.
- A execução continua no Forja, com aprovação, sandbox (E12), política de VRAM (E4), verify e commit
  (E1/E2).

O Forja já usa o pacote `mcp` como cliente (`mcp_client.py`). O mesmo pacote serve para criar o
servidor.

**Depende de:** nada para as ferramentas de tarefa (`taskdb` e `run_task` já existem). As ferramentas
`issue_*` dependem da tabela `issues` da E15-A. A E1 e a E2 tornam o fluxo confiável, mas não bloqueiam o
começo.

### Servidor
- [ ] Endpoint `/mcp` com transporte streamable HTTP, montado no FastAPI existente:
  - só em `127.0.0.1`;
  - autenticado pelo mesmo token (`x-forja-token`), que a tela de configurações mostra junto com o
    trecho pronto para colar no `mcp.json` do Claude Code/Desktop;
  - interruptor "Permitir que o Claude controle o Forja", **desligado por padrão**.
- [ ] Cada ferramenta MCP chama a função que já existe. Não há lógica duplicada, e as regras e portões
      da E1 valem igual.

### Ferramentas
| Ferramenta MCP | O que faz |
|---|---|
| `project_state(path)` | FORJA.md, as convenções (E14), as tarefas abertas e o resumo do board |
| `plan_feature` / `list_tasks` / `update_task` | As mesmas do `taskdb.py`, com os mesmos portões (verify, ciclo, FORJA.md) |
| `run_task(code)` | Despacha para um **Worker local** e devolve na hora um `attempt_id` (não bloqueia) |
| `task_status(attempt_id, wait_s)` | Espera até `wait_s` (máx. ~100 s, abaixo do timeout comum do MCP) e devolve o estado. Os resultados vêm já enxutos (E3) |
| `issue_create` / `issue_list` / `issue_update` | Cards do board (E15). `issue_create` **recusa sem evidência** |
| `forja_note(texto)` | Mensagem de progresso explícita do Claude para o painel |
| `forja_inbox()` | Mensagens que o usuário escreveu no Forja ou no celular para o Claude (ver abaixo) |

- [ ] As ferramentas que alteram estado (`run_task`, `update_task`, `issue_update` com mudança de
      coluna) respeitam o modo de permissão da conversa-espelho. No Manual, a aprovação aparece no
      Forja e no celular, como qualquer outra.

### As conversas do Claude aparecem no Forja (painel e Maestro)
O MCP funciona por chamadas de ferramenta: o texto que o Claude escreve para o usuário **não** chega ao
servidor sozinho. Três mecanismos, do mais garantido ao opcional, resolvem isso:

- [ ] **1. Conversa-espelho automática (garantida):** cada sessão MCP (`Mcp-Session-Id`) vira uma
      conversa no Forja, do tipo "Claude (externo)", ligada ao projeto. **Toda chamada de ferramenta que
      o Claude faz ao Forja fica registrada nela pelo próprio servidor**, sem depender do modelo:
      planos, `run_task`, resultados, cards criados. A árvore de tarefas aparece no `MaestroView`
      normalmente, porque os dados são as mesmas `Feature`/`Task`.
- [ ] **2. Hooks do Claude Code (automático, com o texto inteiro):** o Forja oferece "Instalar
      integração no Claude Code". Com a aprovação do usuário, grava os hooks no `settings.json` do
      Claude Code do projeto (`.claude/settings.json`):
  - `UserPromptSubmit` manda o pedido do usuário;
  - `Stop` manda a resposta final do turno (lida do `transcript_path` que o hook recebe);
  - `PostToolUse` manda as ferramentas que o Claude usou fora do Forja (Read, Edit, Bash), de forma
    resumida.

  Cada hook faz um POST em `127.0.0.1` com o token, e a conversa-espelho passa a mostrar o diálogo
  completo, como uma conversa normal do Forja. Antes de construir, conferir o formato atual da
  entrada de cada hook na documentação do Claude Code.
- [ ] **3. `forja_note` (opcional):** para progresso no meio de um turno longo. As instruções do servidor
      MCP pedem ao Claude que o use a cada etapa, mas ele não é obrigatório, porque os mecanismos 1 e 2
      já cobrem o essencial.
- [ ] **Do Forja para o Claude (o caminho de volta):** um servidor MCP não consegue abrir um turno novo no
      Claude Code sozinho. A solução é uma caixa de entrada:
  - o que o usuário escreve na conversa-espelho (no PC ou no celular) fica na fila;
  - a fila chega ao Claude **dentro do próximo resultado de qualquer ferramenta do Forja** (ex.:
    `task_status` traz "mensagem do usuário: …") e também pelo `forja_inbox`.

  Enquanto o Claude estiver trabalhando com o Forja, o usuário consegue redirecioná-lo pelo celular.
  Com o Claude parado, a mensagem espera o próximo turno, e a tela avisa isso.
- [ ] **Sincronizar com o celular** (regra do `CLAUDE.md`): a conversa-espelho, as tarefas e os cards
      entram no `/api/activity`. Testar nos dois sentidos com o celular de verdade.

### Testes e validação
- [ ] Testes com um cliente MCP falso (o próprio `mcp` em modo cliente):
  - sem token é recusado;
  - com o interruptor desligado, recusa;
  - `run_task` devolve na hora e o `task_status` acompanha;
  - `issue_create` sem evidência é recusado;
  - toda chamada aparece na conversa-espelho;
  - mensagem da caixa de entrada chega no próximo resultado.
- [ ] Validar com o Claude Code de verdade num projeto real: planejar uma feature pequena, despachar 2–3
      tarefas para Workers locais, criar cards com evidência, e acompanhar tudo no painel do Forja e no
      celular.

**Pronto quando:** o Claude Code planeja e despacha tarefas para os Workers locais, os cards aparecem no
board com evidência, e a conversa inteira (pedido, respostas, tarefas) aparece no painel e no Maestro do
Forja, no PC e no celular.

---

## Notas da revisão (2026-09-25, só leitura)

| Sistema | Nota | Onde o plano ataca |
|---|---|---|
| Context management | 6 | E4 (tetos e política de execução), E3 |
| Browser + terminal | 7 | E9 |
| Teste/correção automática | 6 | E1, E3 |
| Task decomposition | 5 | E1, E3 |
| Maestro/Planner | 7 | E1, E3 |
| Project memory | 6 | E6, E4 |
| Reviewer/Critic | 4 | E8 |
| Paralelismo de workers | 4 | E7 |
| RAG semântico | 2 | E6, E5 |
| Métricas/telemetria | 5 | E10, E0 |
| Integração (fora do print) | 3 | E2 |
| Code intelligence (fora do print) | — | E5 |
| Delegação de exploração (fora do print) | — | E11 |
| Sandbox (fora do print) | 2 | E12 |
| Vários backends locais (fora do print) | 6 | E13 |
| Preferências do projeto (fora do print) | 4 | E14 |
| Board de issues com varredura (ideia nova) | — | E15 |
| Recuperação de loop e alucinação (fora do print) | 3 | E16 |
| Claude como Maestro via MCP (ideia nova) | — | E17 |

**Fora de escopo por enquanto:** codemod ou refatoração por AST, grafo de chamadas (o `lsp references`
cobre), watcher de arquivos, índice vetorial persistente e embeddings (revisar depois da E6).

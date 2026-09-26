# Bench da E0: medição base (2026-09-25/26)

Máquina: Intel Arc B580 12 GB (Vulkan), 30 GB de RAM, llama-server do runtime Vulkan do Forja. Modelo
principal: Qwen3.6-35B-A3B Q4_K_M (híbrido: camadas recorrentes + atenção a cada 4), com 24 camadas de
especialistas na CPU (`--n-cpu-moe 24`), KV `q8_0` unificado, os parâmetros do `local.json` do usuário.

- `2026-09-baseline.json`: bench do Maestro (`scripts/bench_maestro.py`), um resultado por rótulo.
- `2026-09-kvcache.json`: V4 (a rodada com KV `f16`) e as validações V1–V6 (`scripts/bench_kvcache.py`).
- Pedido, projeto e suíte de aceite (escondida do agente): `backend/tests/bench/`.

## Bench do Maestro (projeto "tarefas", ~8 tarefas)

| Rodada | Relógio | Troca de modelo | Maestro reprocessando depois do Worker | Tarefas concluídas | Aceite | Suíte do projeto |
|---|---|---|---|---|---|---|
| (a) Maestro e Worker no Qwen3.6 | 55,6 min | 0,5 min (1 carga) | 41 s em 5 voltas | 5 de 8 (3 canceladas) | 4/6 | passa (73) |
| V4: a mesma, KV `f16` | 84,7 min | 0,3 min | — | 3 de 11 (1 bloqueada, 3 canceladas, 4 abertas) | 3/6 | passa |
| (b) Maestro Qwen3.6, Worker Ornith-1.5-9B | 112,7 min | 8,4 min (23 trocas) | 35,5 min em 11 voltas (33,5k tokens/volta) | 3 de 21 (15 canceladas) | 0/6 | falha |

**A troca de modelo domina, e não pela carga em si.** Em (b), carregar custou 8,4 min, mas o Maestro voltar
ao ar sem o próprio contexto custou 35,5 min: cada volta depois de um Worker reprocessou o prompt inteiro
(19k → 61k tokens) a ~170 t/s. Somados, 39% do relógio. Com o mesmo modelo (a), a perda foi de 41 s no total:
o llama.cpp mantém o contexto do Maestro na memória (checkpoints do estado recorrente) enquanto o Worker roda.

Taxa de cache do prompt: Maestro 95% em (a) e 72% em (b); Worker ~88% nas duas.

**Rodadas que travaram** (ficaram no JSON, são achados):
- Worker gemma-4-12b: uma resposta sem fim de 27,7 mil tokens em 27 min (o llama-server desloca o contexto e
  não para). O laço do Worker não tem teto de tokens de saída nem o filtro de degeneração da E16.
- Worker Ornith, 1ª vez: o Maestro pediu `Remove-Item ... -Force`; comando destrutivo pede aprovação até em
  "Ignorar permissões", e ninguém aprovou. Desde então o bench aprova e conta (`aprovacoes_pedidas`).

**Qualidade, n=1 por rodada:** o aceite oscilou de 0/6 a 4/6 e o Maestro encerrou a V4 com tarefas abertas e
girou replanejando em (b) (18 tarefas criadas, 15 canceladas). Os testes que o próprio agente escreveu
passaram onde o aceite falhou: o Worker desviou da especificação (saída do `add` com as tags, `export` sem a
linha pedida) e ninguém conferiu contra o pedido.

## Velocidade (tok/s de geração, a mesma conta da interface)

| | Maestro (Qwen3.6) | Worker Qwen3.6 | Worker Ornith-1.5-9B |
|---|---|---|---|
| Geração | 24–29 | 31 | 57 |
| Leitura do prompt | 140–170 | 205 | 1.100 |
| Ponta a ponta (gerados ÷ tempo da chamada) | 9,5–19,5 | 22,6 | 54,4 |

No Forja real do usuário (meta.stats de 430 respostas do Qwen3.6 no modo agente), a geração mediana é 19 t/s com
contexto < 16k, 12 t/s entre 16k e 40k e 7 t/s acima de 40k: metade do bench na mesma faixa, com a mesma
linha de comando. A diferença é o estado da máquina (o bench rodou com o PC ocioso): suspeita principal,
pouca RAM livre para as páginas dos especialistas mapeados (19,7 GB de arquivo em 30 GB de RAM, com Forja,
navegador e Docker/WSL abertos). O baseline acima mede o Forja numa máquina folgada.

## Validações (V1–V6)

| | Resultado | Passa? |
|---|---|---|
| V1 save/restore com `--kv-unified` (comum: qwen2.5-coder-1.5b) | 100% do cache de volta depois de reiniciar, com e sem KV unificado | sim |
| V2 restaurar × reprocessar (comum) | 5k: 12%, 10k: 6%, 20k: 3% do tempo de reprocessar; `.bin` 137/274/547 MB | sim (≤ 30%) |
| V3 SWA (gemma-4-12b) | restore responde ok, mas a requisição seguinte reprocessa tudo (razão ≈ 1,0). **Com `--swa-full`: 4,3%, cache inteiro** | só com `--swa-full` |
| V3 híbrido (Qwen3.6-35B-A3B e Qwen3.8-27B) | restore ok, cache 0, reprocessa tudo (razão ≈ 1,0), também com `--ctx-checkpoints 32` | não |
| V4 KV `q8_0` × `f16` | aceite 4/6 (q8_0) × 3/6 (f16); n=1, dentro do ruído | sim (q8_0 não piorou) |
| V5 Maestro → Worker → Maestro (Qwen3.6 ↔ gemma, prompt de 20k) | ciclo 146 s sem cache × 132 s com o restore (que não reaproveita nada no híbrido; a diferença é a carga) | — |
| V6 paralelo no Qwen3.6, `--kv-unified -np 4` | soma do tok/s: ctx 8k → 1: 30, 2: 43,5 (1,45×), 4: 52 (1,75×); ctx 32k → 1: 36, 2: 37 (1,03×), 4: 54 (1,5×) | 8k sim, 32k não |

V6, mais: um prompt curto (500 tokens) sozinho leva 1,8 s; enquanto outra sessão processa um prompt grande,
leva 16 s (9×). Quatro sessões que juntas passam da janela unificada **não** descartam cache: o servidor
recusa as quatro com HTTP 500 "Context size has been exceeded". VRAM não medida (a leitura do Vulkan por
outro processo não refletiu o uso).

## O que cada resultado decide (E4 e seguintes)

- **Política de execução (E4):** manter o Maestro e o Worker no mesmo modelo sempre que couber é o que mais
  economiza nesta máquina; trocar de modelo custa ~20 s de carga mais o reprocessamento do contexto inteiro do
  Maestro a cada volta (minutos, crescendo com a conversa).
- **Cache em disco (E4):** vale para modelo comum (restaurar ≤ 12%) e para SWA **só com `--swa-full`** (mais
  VRAM para o KV). Para os híbridos da família Qwen3.5/3.6/3.8 fica desligado, com o motivo na tela, até o
  llama.cpp restaurar o estado recorrente de um slot salvo.
- **KV `q8_0`:** segue como está (não piorou na V4); refazer com n > 1 quando a E10 existir.
- **Paralelo:** ligado só com janela por sessão pequena (≤ 8k no Qwen3.6); o Forja precisa segurar a fila antes
  de passar da janela unificada, porque o servidor recusa em vez de descartar.
- **Worker:** teto de tokens de saída e o filtro de degeneração da E16 também no laço do Worker (E16-B).
- **Autonomia:** aprovação de comando destrutivo parada em silêncio segura a noite inteira; push/alerta e
  prazo para a aprovação (E16-C). O Maestro ainda encerra com tarefas abertas e gira replanejando (E1/E16).
- **Qualidade:** conferência contra o pedido, não só contra os testes do próprio Worker (E8, revisor).

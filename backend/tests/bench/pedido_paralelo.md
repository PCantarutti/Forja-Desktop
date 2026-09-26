O pacote `tarefas` (em `tarefas/`: `modelo.py`, `armazem.py`, `cli.py`) já está implementado e funcionando.
Falta só a suíte de testes. Leia o código antes de planejar.

Crie com plan_feature exatamente TRÊS tarefas independentes entre si (sem depends_on), uma por arquivo de teste:

1. `tests/test_modelo.py`: testes do modelo (criação da tarefa, prioridades, validação e o que mais o módulo
   oferece). verify_command: `python -m pytest -q tests/test_modelo.py`
2. `tests/test_armazem.py`: testes do armazém (adicionar, obter, concluir, remover, listar com filtros,
   persistência em arquivo), usando uma pasta temporária. verify_command: `python -m pytest -q tests/test_armazem.py`
3. `tests/test_cli.py`: testes da linha de comando (cada subcomando, códigos de saída, mensagens de erro),
   chamando a CLI como o usuário chamaria. verify_command: `python -m pytest -q tests/test_cli.py`

Cada tarefa só cria o próprio arquivo de teste: não crie `tests/__init__.py` nem `tests/conftest.py` (cada
arquivo se vira sozinho, com `tmp_path` do pytest). Não altere o código de `tarefas/`.

Despache as três JUNTAS numa chamada só: run_task(codes=["TASK-001","TASK-002","TASK-003"]). Feche cada uma
conforme o resultado. Pronto quando `python -m pytest -q` passar inteiro.

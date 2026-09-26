Construa o pacote Python `tarefas`: um gerenciador de tarefas pela linha de comando, só com a biblioteca padrão
(nada de pip install). Planeje em tarefas pequenas, uma por módulo/comando, cada uma com teste, e despache para
os Workers. Siga exatamente estas interfaces (os nomes são usados por outros programas):

1. `tarefas/modelo.py`: `PRIORIDADES = ("baixa", "media", "alta")` e o dataclass `Tarefa` com os campos
   `id: int`, `titulo: str`, `feita: bool = False`, `prioridade: str = "media"`, `prazo: datetime.date | None = None`
   e `tags: list[str]` (padrão lista vazia). Título vazio ou só espaços, ou prioridade fora de `PRIORIDADES`,
   levanta `ValueError` na criação. `Tarefa.para_dict()` devolve um dict serializável em JSON (prazo como
   "AAAA-MM-DD" ou None) e `Tarefa.de_dict(d)` faz o caminho inverso.
2. `tarefas/armazem.py`: `class Armazem` com `Armazem(caminho)`, que guarda as tarefas num arquivo JSON
   (arquivo inexistente = lista vazia). Métodos:
   - `adicionar(titulo, prioridade="media", prazo=None, tags=None) -> Tarefa` (id = maior id + 1, começando em 1);
   - `obter(id) -> Tarefa` (id inexistente levanta `KeyError`);
   - `concluir(id) -> Tarefa` e `remover(id) -> None` (id inexistente: `KeyError`);
   - `listar(feita=None, prioridade=None, tag=None) -> list[Tarefa]`, filtrando pelo que não for None e
     ordenando por prioridade (alta, media, baixa), depois prazo (sem prazo por último), depois id;
   - `atrasadas(hoje: datetime.date) -> list[Tarefa]`: pendentes com prazo antes de `hoje`.
   Toda mudança salva o arquivo na hora, escrevendo num arquivo temporário e trocando (nada de arquivo pela
   metade).
3. `tarefas/cli.py` com `main(argv: list[str] | None = None) -> int` e `tarefas/__main__.py`, para rodar
   `python -m tarefas`. O arquivo de dados vem da variável `TAREFAS_ARQUIVO` (padrão `tarefas.json` na pasta
   atual). Subcomandos:
   - `add TITULO [--prioridade P] [--prazo AAAA-MM-DD] [--tag T]...`: imprime `Criada #ID: TITULO`;
   - `list [--feitas | --pendentes] [--prioridade P] [--tag T]`: uma linha por tarefa no formato
     `#ID [x] TITULO` (feita) ou `#ID [ ] TITULO` (pendente), na ordem de `Armazem.listar`;
   - `done ID`: imprime `Concluída #ID`;
   - `rm ID`: imprime `Removida #ID`;
   - `stats`: imprime quatro linhas `total: N`, `feitas: N`, `pendentes: N`, `atrasadas: N` (hoje = data atual);
   - `export --csv ARQUIVO`: grava CSV com cabeçalho `id,titulo,feita,prioridade,prazo,tags` (tags separadas
     por `;`, feita como `sim`/`nao`) e imprime `Exportadas N tarefas`.
   Id inexistente ou argumento inválido: mensagem em stderr e código de saída 1. Sucesso: 0.
4. Testes com pytest em `tests/` cobrindo o modelo, o armazém e cada subcomando da CLI.

Pronto quando `python -m pytest -q` passar inteiro.

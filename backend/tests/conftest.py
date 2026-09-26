import os
import tempfile

# db.py cria o SQLite no import; nos testes, num diretório temporário.
_TMP = tempfile.mkdtemp(prefix="forja-test-")
os.environ["DB_PATH"] = os.path.join(_TMP, "forja.db")  # nunca o banco real
# E o resto dos dados junto: sem isto, o lifespan do TestClient roda mirror.sync() e escreve o
# espelho das conversas de teste em %APPDATA%\Forja\conversas, o diretório de quem está rodando.
os.environ.setdefault("FORJA_DATA", _TMP)
# Pasta de trabalho padrão também temporária: o Maestro faz commit por tarefa, e um teste sem
# workspace próprio não pode cair num repositório git de verdade (~/Forja ou outro).
os.environ.setdefault("WORKSPACE_ROOT", os.path.join(_TMP, "ws"))
os.makedirs(os.environ["WORKSPACE_ROOT"], exist_ok=True)

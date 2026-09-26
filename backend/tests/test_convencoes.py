"""E14, parte 1: convenções detectadas pelo código, cada uma com a origem."""
import json

from app import convencoes, projstate


def _ts(raiz):
    (raiz / "src" / "components").mkdir(parents=True)
    (raiz / "src" / "hooks").mkdir()
    (raiz / "package.json").write_text(json.dumps({
        "name": "loja", "type": "module",
        "scripts": {"test": "vitest run", "lint": "eslint .", "typecheck": "tsc --noEmit", "build": "vite build"},
        "dependencies": {"react": "^19"},
        "devDependencies": {"vitest": "^3", "tailwindcss": "^4", "typescript": "^5", "eslint": "^9"}}, indent=2),
        encoding="utf-8")
    (raiz / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")
    (raiz / "tsconfig.json").write_text('{\n  // comentário\n  "compilerOptions": {\n    "target": "ES2022",\n'
                                        '    "strict": true,\n    "paths": {"@/*": ["./src/*"]},\n  },\n}\n', encoding="utf-8")
    (raiz / ".prettierrc").write_text('{"semi": false, "singleQuote": true}', encoding="utf-8")
    return raiz


def _py(raiz):
    (raiz / "app" / "services").mkdir(parents=True)
    (raiz / "tests").mkdir()
    (raiz / "pyproject.toml").write_text(
        '[project]\nname = "api"\ndependencies = [\n  "fastapi>=0.110",\n  "sqlalchemy",\n]\n\n'
        '[tool.ruff]\nline-length = 110\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    (raiz / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return raiz


def _por_texto(det):
    return {i["texto"]: i["origem"] for i in det["itens"]}


def test_typescript_tailwind_vitest(tmp_path):
    d = convencoes.detectar(_ts(tmp_path))
    itens = _por_texto(d)
    assert itens["TypeScript strict"] == "tsconfig.json:5"
    assert itens["Tailwind CSS"].startswith("package.json:") and itens["Testes com Vitest"].startswith("package.json:")
    assert itens["Gerenciador de pacotes: pnpm"] == "pnpm-lock.yaml"
    assert itens["Módulos ES (import/export, não require)"] == "package.json:3"
    assert any(t.startswith("Imports com apelido de caminho (@/*)") for t in itens)
    assert "Estilo: sem ponto e vírgula, aspas simples" in itens
    assert any(t.startswith("Código separado em components/, hooks/") for t in itens)
    assert {k: v["comando"] for k, v in d["comandos"].items()} == {
        "test": "pnpm run test", "typecheck": "pnpm run typecheck", "lint": "pnpm run lint", "build": "pnpm run build"}


def test_python_ruff_pytest(tmp_path):
    d = convencoes.detectar(_py(tmp_path))
    itens = _por_texto(d)
    assert itens["Python, pacotes com uv"] == "uv.lock"
    assert itens["FastAPI"] == "pyproject.toml:4" and itens["Lint com Ruff"] == "pyproject.toml:8"
    assert itens["Linhas de até 110 caracteres"] == "pyproject.toml:9"
    assert itens["Testes com pytest"] == "pyproject.toml:11"
    assert d["comandos"]["test"]["comando"] == "uv run pytest -q" and d["comandos"]["lint"]["comando"] == "uv run ruff check ."


def test_projeto_com_back_e_front_em_pastas(tmp_path):
    _ts(tmp_path / "front")
    _py(tmp_path / "back")
    d = convencoes.detectar(tmp_path)
    itens = _por_texto(d)
    assert itens["front: TypeScript strict"] == "front/tsconfig.json:5" and "back: FastAPI" in itens
    assert d["comandos"]["test:back"] == {"comando": "uv run pytest -q", "origem": "back/pyproject.toml:11", "pasta": "back"}
    assert "cd " not in json.dumps(d["comandos"])  # sem "cd x &&": quebra com espaço e no PowerShell 5.1
    assert convencoes.comandos(tmp_path) == {}  # na raiz não há o que rodar; o board roda por pasta vinculada


def test_arquivo_preserva_o_que_o_usuario_escreveu_e_refaz_o_detectado(tmp_path):
    _py(tmp_path)
    arq = tmp_path / convencoes.ARQUIVO
    arq.parent.mkdir(parents=True)
    arq.write_text("# Convenções do projeto\n\n## Do time\n- Nomes em português.\n", encoding="utf-8")
    convencoes.atualiza(tmp_path)
    texto = arq.read_text(encoding="utf-8")
    assert "- Nomes em português." in texto and "- FastAPI — `pyproject.toml:4`" in texto
    mtime = arq.stat().st_mtime_ns
    convencoes.atualiza(tmp_path)
    assert arq.stat().st_mtime_ns == mtime  # nada mudou: não regrava
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "api"\ndependencies = ["django"]\n', encoding="utf-8")
    convencoes.atualiza(tmp_path)
    texto = arq.read_text(encoding="utf-8")
    assert "Django" in texto and "FastAPI" not in texto and "- Nomes em português." in texto
    assert texto.count(convencoes.INICIO) == 1


def test_entra_no_prompt_da_maestro(tmp_path):
    _py(tmp_path)
    (tmp_path / projstate.PASTA).mkdir()
    convencoes.atualiza(tmp_path)
    bloco = projstate.prompt_block(tmp_path)
    assert "knowledge/convencoes.md" in bloco and "FastAPI" in bloco and "forja:detectado" not in bloco


def test_board_usa_os_comandos_detectados(tmp_path, monkeypatch):
    from app import board, shell
    _py(tmp_path)
    (tmp_path / ".git").mkdir()
    rodados = []
    monkeypatch.setattr(shell, "executa_do_projeto", lambda r, cmd, t=60: (rodados.append(cmd), (0, ""))[1])
    projeto = board.projeto_de(str(tmp_path))
    board._VARREDURAS[projeto] = {"rodando": True, "criados": 0, "encontrados": 0, "avisos": []}
    board._varre(projeto, tmp_path)
    assert "uv run pytest -q" in rodados and "uv run ruff check ." in rodados

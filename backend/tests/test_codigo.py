"""E5: tree, ast e imports sobre um mini-projeto em tmp_path."""
import pytest

from app import codigo
from app.tools import ToolError


def _escreve(root, arquivos: dict):
    for caminho, texto in arquivos.items():
        p = root / caminho
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texto, encoding="utf-8")


@pytest.fixture
def projeto(tmp_path):
    _escreve(tmp_path, {
        ".gitignore": "gerado/\n*.log\n",
        "pkg/__init__.py": "",
        "pkg/calc.py": 'import os\nfrom . import util\n\n\nclass Calc:\n    """Calculadora."""\n\n'
                       "    def soma(self, a, b):\n        return a + b\n\n\n"
                       "def fora():\n    from pkg import calc  # local\n    return calc\n",
        "pkg/util.py": "from .calc import Calc\n",          # ciclo proposital util ↔ calc
        "pkg/quebrado.py": "def ok():\n    return 1\n\ndef ruim(:\n    pass\n",
        "web/tsconfig.json": "{}",
        "tsconfig.json": '{ // comentário\n "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["web/src/*"]},},\n}',
        "web/src/index.ts": 'import { api } from "./api";\nimport React from "react";\nimport x from "@/lib/x";\n'
                            'const y = require("./lib/y");\nexport function main(n: number): number { return n }\n'
                            "export const seta = (a: string) => a;\nexport class Tela { mostra() {} }\n",
        "web/src/api/index.ts": "export const api = 1;\n",
        "web/src/lib/x.ts": "export default 1;\n",
        "web/src/lib/y.js": "module.exports = 2;\n",
        "gerado/lixo.py": "x = 1\n",
        "app.log": "log\n",
    })
    return tmp_path


# ------------------------------------------------------------------ tree

def test_tree_indenta_conta_e_respeita_o_gitignore(projeto):
    saida = codigo.tree(projeto, {"depth": 3})
    assert "├── pkg/  (4 arquivos)" in saida or "pkg/  (4 arquivos)" in saida
    assert "calc.py  14 linhas" in saida
    assert "gerado" not in saida and "app.log" not in saida      # .gitignore
    assert "│   " in saida and "└── " in saida


def test_tree_profundidade_so_pastas_e_pattern(projeto):
    assert "calc.py" not in codigo.tree(projeto, {"depth": 1})
    so = codigo.tree(projeto, {"depth": 3, "dirs_only": True})
    assert "pkg/" in so and "calc.py" not in so
    py = codigo.tree(projeto, {"depth": 4, "pattern": "*.ts"})
    assert "index.ts" in py and "pkg/" not in py                 # pasta sem nenhum .ts some


def test_tree_resume_pasta_com_muitos_filhos(tmp_path):
    for i in range(30):
        (tmp_path / f"a{i:02}.txt").write_text("x", encoding="utf-8")
    saida = codigo.tree(tmp_path, {})
    assert "a24.txt" in saida and "a25.txt" not in saida and "… +5 arquivos" in saida


# ------------------------------------------------------------------ ast

def test_outline_python_com_metodo_assinatura_e_docstring(projeto):
    saida = codigo.ast_tool(projeto, {"operation": "outline", "path": "pkg/calc.py"})
    assert "class Calc  L5–9 — Calculadora." in saida
    assert "    def soma(self, a, b)  L8–9" in saida
    assert "def fora()  L12–14" in saida


def test_outline_ts_com_funcao_seta_e_classe(projeto):
    saida = codigo.ast_tool(projeto, {"operation": "outline", "path": "web/src/index.ts"})
    assert "export function main(n: number): number" in saida or "function main(n: number): number" in saida
    assert "seta" in saida and "class Tela" in saida and "mostra()" in saida


def test_outline_de_arquivo_com_erro_de_sintaxe_avisa_e_mostra_o_que_der(projeto):
    saida = codigo.ast_tool(projeto, {"operation": "outline", "path": "pkg/quebrado.py"})
    assert "erro de sintaxe perto da linha 4" in saida and "ok" in saida


def test_symbol_devolve_so_o_fonte_do_simbolo(projeto):
    saida = codigo.ast_tool(projeto, {"operation": "symbol", "path": "pkg/calc.py", "name": "Calc.soma"})
    assert "    8\t    def soma(self, a, b):" in saida and "class Calc" not in saida
    with pytest.raises(ToolError, match="não encontrado.*Calc.soma"):
        codigo.ast_tool(projeto, {"operation": "symbol", "path": "pkg/calc.py", "name": "nada"})


def test_node_at(projeto):
    py = codigo.ast_tool(projeto, {"operation": "node_at", "path": "pkg/calc.py", "line": 9, "character": 16})
    assert py.startswith("ClassDef Calc > FunctionDef soma > Return > BinOp")
    ts = codigo.ast_tool(projeto, {"operation": "node_at", "path": "web/src/index.ts", "line": 5, "character": 17})
    assert "function_declaration main" in ts


def test_query_estrutural_numa_pasta(projeto):
    saida = codigo.ast_tool(projeto, {"operation": "query", "path": "web/src",
                                      "query": '(call_expression function: (identifier) @f (#eq? @f "require"))'})
    assert saida == "web/src/index.ts:4: @f require"


def test_sem_tree_sitter_python_continua(projeto, monkeypatch):
    monkeypatch.setattr(codigo, "_TS", {"python": None, "javascript": None, "typescript": None, "tsx": None})
    assert "class Calc" in codigo.ast_tool(projeto, {"operation": "outline", "path": "pkg/calc.py"})
    with pytest.raises(ToolError, match="tree-sitter"):
        codigo.ast_tool(projeto, {"operation": "outline", "path": "web/src/index.ts"})


# ------------------------------------------------------------------ imports

def test_imports_of_python_resolve_relativo_pacote_e_local(projeto):
    saida = codigo.imports_tool(projeto, {"operation": "of", "path": "pkg/calc.py"})
    assert "L1 os → (pacote: os)" in saida
    assert "L2 . import util → pkg/__init__.py, pkg/util.py" in saida
    assert "L13 pkg import calc → pkg/__init__.py, pkg/calc.py  (local)" in saida


def test_imports_of_ts_resolve_index_alias_require_e_pacote(projeto):
    saida = codigo.imports_tool(projeto, {"operation": "of", "path": "web/src/index.ts"})
    assert "L1 ./api → web/src/api/index.ts" in saida
    assert "L2 react → (pacote: react)" in saida
    assert "L3 @/lib/x → web/src/lib/x.ts" in saida
    assert "L4 ./lib/y → web/src/lib/y.js" in saida


def test_importers_e_ciclos(projeto):
    assert "pkg/util.py:1" in codigo.imports_tool(projeto, {"operation": "importers", "path": "pkg/calc.py"})
    ciclos = codigo.imports_tool(projeto, {"operation": "cycles", "path": "."})
    assert "pkg/calc.py → pkg/util.py → pkg/calc.py" in ciclos
    assert ciclos.count("\n") == 0                                  # o import local de fora() não conta


def test_graph_agrupa_por_pasta(projeto):
    saida = codigo.imports_tool(projeto, {"operation": "graph", "path": "web"})
    assert "web/src/\n" in saida and "  web/src/index.ts → web/src/api/index.ts" in saida


def test_repo_aninhado_ou_worktree_fica_de_fora(projeto):
    """.claude/worktrees/* são cópias do código: no tree e no índice, duplicariam tudo."""
    _escreve(projeto, {".claude/worktrees/outra/.git": "gitdir: ../../../.git/worktrees/outra",
                       ".claude/worktrees/outra/pkg/util.py": "from .calc import Calc\n"})
    assert "outra" not in codigo.tree(projeto, {"depth": 5})
    quem = codigo.imports_tool(projeto, {"operation": "importers", "path": "pkg/calc.py"})
    assert "worktrees" not in quem and "pkg/util.py:1" in quem

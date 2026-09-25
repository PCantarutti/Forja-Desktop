"""Nomes de ferramenta de outros harnesses → a ferramenta do Forja.

Cada modelo foi treinado com o catálogo de algum harness: o gpt-oss chama `search`, `browser.search` e
`container.exec`; modelos afinados em Claude Code chamam `Bash`, `Read` e `str_replace_based_edit_tool`;
em Cursor, `codebase_search` e `run_terminal_cmd`. Sem isto a chamada volta "Ferramenta desconhecida",
e uma rodada inteira (num modelo local, dezenas de segundos) vai embora. Aqui ela é renomeada antes de
qualquer checagem (permissão, lista do subagente, UI) e o resultado ensina o nome certo.

Os argumentos também: `file_path` vira `path`, `old_string` vira `old_str`... Isso vale para qualquer
chamada, mesmo com o nome certo, porque o erro de argumento é o mesmo hábito de treino.

Ferramenta nova no Forja: acrescente os nomes que outros harnesses usam para ela em APELIDOS e os
argumentos em ARGS (o teste `test_toda_ferramenta_tem_apelido` cobra).
"""
from __future__ import annotations

import re

APELIDOS: dict[str, tuple[str, ...]] = {
    "read_file": ("read", "cat", "open", "open_file", "view", "view_file", "read_text_file", "file_read",
                  "readfile", "get_file_contents", "get_file", "show_file", "read_file_content"),
    "write_file": ("write", "create_file", "write_to_file", "file_write", "save_file", "new_file", "create",
                   "writefile", "put_file"),
    "edit_file": ("edit", "str_replace", "replace", "replace_in_file", "apply_edit", "modify_file",
                  "search_replace", "update_file", "multiedit", "multi_edit", "edit_existing_file", "file_edit"),
    "run_command": ("bash", "shell", "sh", "exec", "execute", "execute_command", "run", "run_terminal_cmd",
                    "run_terminal_command", "terminal", "cmd", "powershell", "run_shell_command", "run_shell",
                    "container.exec", "local_shell", "shell_command", "command", "execute_bash"),
    "list_dir": ("ls", "list", "list_files", "list_directory", "dir", "read_dir", "listdir", "list_folder"),
    "grep": ("grep_search", "ripgrep", "rg", "search_files", "find_in_files", "search_file_content",
             "search_in_files", "text_search", "search_text"),
    "glob": ("find", "find_files", "file_search", "glob_search", "find_file", "search_file_names"),
    "code_search": ("search", "codebase_search", "search_code", "semantic_search", "search_codebase",
                    "code_search_tool", "repo_search", "search_repo"),
    "tree": ("directory_tree", "tree_view", "project_tree", "folder_tree", "file_tree"),
    "ast": ("outline", "file_outline", "symbols", "get_symbols", "document_symbols", "code_outline",
            "list_code_definition_names", "get_outline"),
    "imports": ("dependencies", "import_graph", "find_importers", "get_imports", "dependency_graph"),
    "lsp": ("language_server", "goto_definition", "go_to_definition", "find_references", "get_references"),
    "web_search": ("websearch", "browser.search", "google", "search_web", "internet_search", "bing",
                   "google_search", "search_internet", "brave_search", "tavily_search"),
    "fetch_url": ("fetch", "webfetch", "web_fetch", "browser.open", "open_url", "get_url", "read_url",
                  "url_fetch", "http_get", "fetch_webpage", "browse"),
    "browser_navigate": ("navigate", "goto", "browser_goto", "open_page", "playwright_navigate",
                         "browser_open", "go_to_url"),
    "browser_click": ("click", "playwright_click", "click_element"),
    "browser_type": ("type", "fill", "playwright_fill", "type_text", "input_text", "browser_fill"),
    "browser_screenshot": ("screenshot", "take_screenshot", "playwright_screenshot", "capture_screenshot"),
    "browser_read": ("read_page", "get_page_text", "browser_snapshot", "snapshot", "page_content",
                     "get_page_content", "accessibility_tree"),
    "browser_eval": ("evaluate", "eval_js", "javascript", "run_javascript", "execute_javascript",
                     "browser_evaluate", "playwright_evaluate"),
    "browser_scroll": ("scroll", "scroll_page"),
    "browser_console": ("console", "console_logs", "browser_console_messages", "get_console_logs"),
    "browser_tabs": ("tabs", "list_tabs", "browser_tab_list"),
    "browser_upload": ("upload", "upload_file", "browser_file_upload"),
    "browser_validate": ("validate_page", "check_page", "verify_page"),
    "delegate_task": ("task", "agent", "subagent", "dispatch_agent", "spawn_agent", "delegate",
                      "run_subagent", "new_task"),
    "explore": ("explore_code", "investigate", "research_code", "explore_codebase"),
    "update_tasks": ("todowrite", "todo_write", "update_todos", "todo", "write_todos", "update_plan",
                     "todo_list", "set_todos"),
    "remember": ("memorize", "save_memory", "add_memory", "store_memory", "create_memory", "memory_add"),
    "recall": ("read_memory", "get_memory", "retrieve_memory", "memory_get"),
    "forget": ("delete_memory", "remove_memory", "memory_delete"),
    "skill": ("use_skill", "run_skill", "load_skill", "invoke_skill"),
    "serve_start": ("start_server", "run_server", "dev_server", "background_process", "start_process",
                    "serve"),
    "serve_status": ("server_status", "check_server", "process_status", "server_logs"),
    "serve_stop": ("stop_server", "kill_server", "stop_process", "kill_process"),
    "terminal_open": ("open_terminal", "new_terminal", "create_terminal"),
    "terminal_send": ("send_keys", "terminal_write", "write_terminal", "terminal_input", "send_to_terminal"),
    "terminal_read": ("read_terminal", "terminal_output", "get_terminal_output"),
    "terminal_list": ("list_terminals",),
    "terminal_close": ("close_terminal", "kill_terminal"),
    "session_search": ("search_conversations", "search_history", "conversation_search", "search_sessions",
                       "search_chats"),
    "session_read": ("read_conversation", "read_session", "get_conversation"),
    "write_document": ("create_document", "write_docx", "create_docx", "create_word_document"),
    "edit_document": ("edit_docx", "update_document", "edit_word_document"),
    "write_spreadsheet": ("create_spreadsheet", "write_xlsx", "write_excel", "create_excel", "create_xlsx"),
    "edit_spreadsheet": ("edit_xlsx", "edit_excel", "update_spreadsheet"),
    "preview_document": ("view_document", "render_document", "preview_file"),
    "create_goal": ("set_goal", "new_goal", "start_goal"),
    "get_goal": ("read_goal", "current_goal", "show_goal"),
    "update_goal": ("finish_goal", "complete_goal", "goal_update"),
    "image_generate": ("generate_image", "create_image", "text_to_image", "image_gen", "dalle", "imagegen",
                       "make_image", "draw"),
    "video_generate": ("generate_video", "create_video", "text_to_video", "image_to_video", "video_gen"),
    "imagens_pendentes": ("batch_images", "generate_images", "image_batch", "pending_images"),
    "board_card": ("create_issue", "add_card", "create_card", "board_add", "new_issue", "report_bug",
                   "file_issue", "create_ticket", "add_issue", "issue_create", "create_task_card"),
    # Fora do REGISTRY: agent.EXIT_PLAN e agent.ASK_USER
    "exit_plan_mode": ("exitplanmode", "present_plan", "submit_plan", "finish_planning", "propose_plan"),
    "ask_user": ("askuserquestion", "ask_user_question", "ask_question", "ask_followup_question", "ask_human",
                 "request_user_input", "clarify", "human_input"),
    # Maestro (fora do REGISTRY, mas chamáveis)
    "plan_feature": ("create_plan", "make_plan", "plan_tasks", "create_tasks"),
    "list_tasks": ("get_tasks", "show_tasks", "tasks_list"),
    "update_task": ("set_task_status", "edit_task", "task_update"),
    "run_task": ("dispatch_task", "execute_task", "start_task"),
    "session_note": ("write_note", "save_note", "note", "session_summary"),
    "visual_review": ("review_screenshot", "visual_check", "check_visual"),
}

# Argumento de outro harness → argumento do Forja, por ferramenta. `_GERAL` vale para todas.
_CAMINHO = {k: "path" for k in ("file_path", "filepath", "file", "filename", "target_file", "absolute_path",
                                  "dir_path", "directory", "dir", "folder", "target_directory",
                                  "relative_workspace_path", "target_directories", "paths")}
_GERAL = {**_CAMINHO, "cmd": "command"}
ARGS: dict[str, dict[str, str]] = {
    "read_file": {"offset": "start_line", "line_start": "start_line", "start": "start_line",
                  "line_end": "end_line", "end": "end_line"},
    "write_file": {"text": "content", "contents": "content", "file_text": "content", "data": "content",
                   "body": "content"},
    "edit_file": {"old_string": "old_str", "old_text": "old_str", "search": "old_str", "find": "old_str",
                  "old": "old_str", "new_string": "new_str", "new_text": "new_str", "replace": "new_str",
                  "replacement": "new_str", "new": "new_str"},
    "run_command": {"script": "command", "commandline": "command", "command_line": "command",
                    "workdir": "cwd", "working_directory": "cwd", "run_in_background": "background",
                    "is_background": "background", "directory": "cwd", "dir": "cwd"},
    "grep": {"regex": "pattern", "query": "pattern", "search": "pattern", "glob": "include",
             "file_pattern": "include", "includes": "include", "file_glob": "include"},
    "glob": {"glob": "pattern", "query": "pattern", "name": "pattern", "file_pattern": "pattern",
             "glob_pattern": "pattern"},
    "code_search": {"q": "query", "search_term": "query", "text": "query", "question": "query",
                    "search": "query", "pattern": "query"},
    "tree": {"root": "path", "max_depth": "depth", "level": "depth", "levels": "depth"},
    "web_search": {"q": "query", "search_term": "query", "search_query": "query", "text": "query",
                   "num_results": "max_results", "topn": "max_results", "count": "max_results",
                   "limit": "max_results"},
    "fetch_url": {"link": "url", "uri": "url", "href": "url", "address": "url", "id": "url"},
    "browser_navigate": {"link": "url", "uri": "url", "href": "url"},
    "browser_type": {"value": "text", "input": "text", "keys": "text", "ref": "selector",
                     "element": "selector"},
    "browser_click": {"ref": "selector", "element": "selector", "target": "selector"},
    "browser_eval": {"code": "script", "expression": "script", "js": "script", "javascript": "script",
                     "function": "script"},
    "delegate_task": {"prompt": "task", "description": "task", "instructions": "task", "query": "task",
                      "subagent_type": "agent"},
    "explore": {"query": "question", "prompt": "question", "task": "question"},
    "update_tasks": {"todos": "tasks", "items": "tasks", "plan": "tasks"},
    "session_search": {"q": "query", "search_term": "query", "text": "query"},
    "remember": {"title": "name", "key": "name", "text": "content", "value": "content", "memory": "content"},
    "terminal_send": {"input": "command", "text": "command", "keys": "command", "terminal_id": "id",
                      "session_id": "id"},
    "serve_start": {"cmd": "command", "script": "command"},
    "board_card": {"title": "titulo", "description": "descricao", "body": "descricao", "type": "tipo",
                   "kind": "tipo", "file": "arquivo", "path": "arquivo", "file_path": "arquivo", "line": "linha",
                   "snippet": "trecho", "code": "trecho", "severity": "severidade", "priority": "severidade",
                   "verify": "verify_sugerido", "verify_command": "verify_sugerido"},
    "image_generate": {"negative_prompt": "negative", "num_inference_steps": "steps", "reference_images": "refs",
                       "images": "refs"},
    "video_generate": {"negative_prompt": "negative", "image": "imagem_inicial", "start_image": "imagem_inicial",
                       "end_image": "imagem_final", "duration": "segundos", "seconds": "segundos"},
}
# Editor multiplexado do Claude (e cópias): um nome, a operação em `command`.
_EDITOR = {"str_replace_editor", "str_replace_based_edit_tool", "text_editor"}
_EDITOR_OPS = {"view": "read_file", "create": "write_file", "str_replace": "edit_file"}

_INDICE = {apelido: alvo for alvo, nomes in APELIDOS.items() for apelido in nomes}


def normaliza(nome: str) -> str:
    """`functions.Bash`, `default_api:read-file` → `bash`, `read_file`."""
    n = str(nome or "").strip()
    n = re.sub(r"^(functions|default_api|tools|tool|mcp)[.:]", "", n, flags=re.I)
    return n.replace("-", "_").lower()


def extras(nomes) -> list[str]:
    """Apelidos cujo alvo existe entre `nomes`: o parser de chamada em texto só aceita nome conhecido."""
    nomes = set(nomes)
    return [a for a, alvo in _INDICE.items() if alvo in nomes] + sorted(_EDITOR)


def _args(alvo: str, args: dict, props: dict | None) -> dict:
    mapa = {**_GERAL, **ARGS.get(alvo, {})}
    out = dict(args)
    for k, v in args.items():
        novo = mapa.get(k) or mapa.get(k.lower())
        if not novo or novo == k or (props is not None and (k in props or novo not in props)) or novo in out:
            continue
        del out[k]
        tipo = (props or {}).get(novo, {}).get("type")
        out[novo] = v[0] if isinstance(v, list) and v and tipo == "string" else v
    return out


def resolve(call: dict, nomes, props_de=None) -> dict:
    """A chamada com nome e argumentos do Forja. `nomes`: as ferramentas disponíveis agora (o apelido só
    vale se o alvo estiver nelas). `props_de(nome)`: o schema de argumentos, para não renomear um argumento
    que a ferramenta tem de verdade. Quando o nome muda, `apelido` guarda o original."""
    nomes = set(nomes)
    nome, args = call.get("name") or "", call.get("arguments")
    if not isinstance(args, dict):
        return call
    alvo = nome if nome in nomes else None
    if not alvo:
        n = normaliza(nome)
        if n in _EDITOR and str(args.get("command")) in _EDITOR_OPS:
            alvo = _EDITOR_OPS[str(args["command"])]
            args = {k: v for k, v in args.items() if k != "command"}
            if alvo == "read_file" and isinstance(args.get("view_range"), list) and len(args["view_range"]) == 2:
                ini, fim = args.pop("view_range")
                args.update(start_line=ini, **({"end_line": fim} if fim and fim > 0 else {}))
        else:
            alvo = n if n in nomes else _INDICE.get(n)
        if alvo not in nomes:
            return call
    props = props_de(alvo) if props_de else None
    novos = _args(alvo, args, props)
    if alvo == "edit_file" and isinstance(novos.get("edits"), list):  # MultiEdit: old_string dentro da lista
        novos["edits"] = [_args("edit_file", e, None) if isinstance(e, dict) else e for e in novos["edits"]]
    if alvo == nome and novos == args:
        return call
    return {**call, "name": alvo, "arguments": novos, **({"apelido": nome} if alvo != nome else {})}


def resolve_todas(calls: list[dict], nomes, props_de=None) -> list[dict]:
    return [resolve(c, nomes, props_de) for c in calls]


def nota(call: dict) -> str:
    return (f"[Você chamou '{call['apelido']}': no Forja essa ferramenta se chama '{call['name']}', e foi ela "
            f"que rodou. Use '{call['name']}' nas próximas chamadas.]\n") if call.get("apelido") else ""

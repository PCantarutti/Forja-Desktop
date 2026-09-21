import asyncio
import json
import sys
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from fastapi.staticfiles import StaticFiles

from . import (checkpoints, compact, comparar, config, db, downloads, gitops, imagegen, llm, localai, lotes,
               mcp_client, memory, mirror, native, pesquisa, policy, relatorio, settings, shell, skills, subagents,
               terminal, uploads, workspace)
from .agent import RUNS, Run, RunRequest, _load, _save, active_run
from .browser import MANAGER
from .parsing import split_think
from .tools import REGISTRY, ToolError


settings.apply()


@asynccontextmanager
async def lifespan(_app):
    # Primeira linha do log: onde estão os dados desta execução (ajuda no suporte).
    print(f"Forja: dados em {config.DATA_DIR} · banco {config.DB_PATH} · interface {config.WEB_DIR or '(só API)'}",
          flush=True)
    if "WindowsApps" in sys.base_prefix:  # só acontece em dev: o app empacotado traz o seu próprio Python
        print("Forja: aviso — este Python é o da Microsoft Store, e o Windows redireciona as gravações em "
              "%APPDATA% para LocalCache. Os dados acima NÃO estarão no caminho impresso. Use um Python do "
              "python.org ou do uv para desenvolver.", flush=True)
    localai.reap_orphan()  # sobra de um backend que morreu sem descarregar o modelo
    lotes.limpar_descartadas()  # imagens reprovadas que já passaram do prazo
    asyncio.create_task(asyncio.to_thread(localai.load_last))  # "carregar ao iniciar", se estiver ligado
    # Espelho em Markdown: gera o que falta (banco anterior ao espelho) e limpa .md órfão.
    print(f"Forja: conversas espelhadas em {mirror.ROOT} ({mirror.sync()} arquivo(s) gerado(s))", flush=True)
    # MCP conecta em background: npx/uvx podem demorar e a API não deve esperar (o painel mostra "connecting").
    task = asyncio.create_task(mcp_client.start())
    yield
    task.cancel()
    localai.unload()  # o modelo local morre com o backend (no app o Electron já mata a árvore)
    await mcp_client.stop()
    await MANAGER.shutdown()


app = FastAPI(title="Forja", lifespan=lifespan)


@app.get("/api/config")
def get_config():
    return {"providers": [{"id": p["id"], "name": p["name"]} for p in config.PROVIDERS.values()],
            "num_ctx": config.NUM_CTX, "max_iterations": config.MAX_ITERATIONS,
            "default_workspace": workspace.label(None), "drives": [d["name"] for d in workspace.roots()],
            "subagents": {k: v for k, v in subagents.configured().items()}}


# ------------------------------------------------------------------ pastas de trabalho

@app.get("/api/fs/roots")
def fs_roots():
    """Discos montados + pastas usadas recentemente (para o seletor de pasta)."""
    with db.session() as s:
        rows = s.execute(select(db.Conversation.workspace, db.Conversation.updated_at)
                         .where(db.Conversation.workspace.is_not(None))
                         .order_by(db.Conversation.updated_at.desc())).all()
    recent: list[str] = []
    for ws, _ in rows:
        if ws not in recent:
            recent.append(ws)
    return {"drives": workspace.roots(), "recent": recent[:8], "default": workspace.label(None)}


@app.get("/api/fs/list")
def fs_list(path: str):
    try:
        return workspace.list_dirs(path)
    except workspace.WorkspaceError as e:
        raise HTTPException(400, str(e))


def _conv_root(conv_id: int | str | None):
    """Pasta (no container) da conversa; 0/None = pasta padrão."""
    if not conv_id or str(conv_id) == "0":
        return workspace.default_root()
    with db.session() as s:
        c = s.get(db.Conversation, int(conv_id))
        folder = c.workspace if c else None
    try:
        return workspace.resolve(folder)
    except workspace.WorkspaceError as e:
        raise HTTPException(400, str(e))


@app.get("/api/tools")
def get_tools():
    return [{"name": t.name, "description": t.description, "mutating": t.mutating, "always_ask": t.always_ask,
             "source": t.source, "enabled": t.name not in config.DISABLED_TOOLS} for t in REGISTRY.values()]


# ------------------------------------------------------------------ configurações

@app.get("/api/settings")
def get_settings():
    return settings.public()


@app.put("/api/settings")
def put_settings(patch: dict):
    try:
        return settings.update(patch)
    except settings.SettingsError as e:
        raise HTTPException(400, str(e))


@app.post("/api/settings/reset")
def reset_settings(body: dict | None = None):
    return settings.reset((body or {}).get("keys"))


@app.get("/api/mcp/config")
def get_mcp_config():
    return {"path": str(config.MCP_CONFIG), "text": settings.read_mcp_config()}


@app.put("/api/mcp/config")
async def put_mcp_config(body: dict):
    if any(not r.finished for r in RUNS.values()):
        raise HTTPException(409, "Espere a execução atual terminar")
    try:
        settings.write_mcp_config(body.get("text", ""))
    except settings.SettingsError as e:
        raise HTTPException(400, str(e))
    await mcp_client.start()
    return mcp_client.status()


@app.get("/api/memory")
async def get_memory():
    return await memory.read()


@app.get("/api/memory/project")
def get_project_memory():
    return memory.project_read()


@app.put("/api/memory/project")
def put_project_memory(body: dict):
    from .tools import ToolError
    try:
        return memory.project_write(body.get("content", ""))
    except ToolError as e:
        raise HTTPException(400, str(e))


class PersonalMemoryBody(BaseModel):
    name: str
    description: str = ""
    content: str = ""
    type: str = "usuario"


@app.get("/api/memory/personal")
def get_personal_memory():
    """Memórias sobre o usuário: só o índice (nome, descrição, tipo, data)."""
    return {"enabled": config.PERSONAL_MEMORY, "dir": str(memory.personal_dir()),
            "items": memory.personal_list()}


@app.get("/api/memory/personal/{name}")
def read_personal_memory(name: str):
    try:
        return memory.personal_read(name)
    except memory.MemoryError as e:
        raise HTTPException(404, str(e))


@app.put("/api/memory/personal")
def put_personal_memory(body: PersonalMemoryBody):
    try:
        return memory.personal_write(body.name, body.description, body.content, body.type)
    except memory.MemoryError as e:
        raise HTTPException(400, str(e))


@app.post("/api/memory/personal/delete")
def delete_personal_memory(body: dict):
    try:
        return {"removed": memory.personal_delete(body.get("names") or [])}
    except memory.MemoryError as e:
        raise HTTPException(400, str(e))


@app.post("/api/memory/delete")
async def delete_memory(body: dict):
    try:
        return await memory.delete(body.get("names") or [])
    except memory.MemoryError as e:
        raise HTTPException(400, str(e))


@app.get("/api/mcp")
def get_mcp():
    return mcp_client.status()


@app.post("/api/mcp/reload")
async def reload_mcp():
    if any(not r.finished for r in RUNS.values()):
        raise HTTPException(409, "Espere a execução atual terminar antes de recarregar o MCP")
    await mcp_client.start()
    return mcp_client.status()


@app.get("/api/models")
async def get_models(provider: str, all: bool = False):
    """Modelos do provedor. Sem `all`, só os marcados em Configurações › Provedores (se houver seleção)."""
    try:
        models = await llm.list_models(provider)
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    chosen = config.ENABLED_MODELS.get(provider)
    if all or not chosen:
        return {"models": models, "filtered": False, "total": len(models)}
    return {"models": [m for m in models if m in chosen], "filtered": True, "total": len(models)}


@app.get("/api/catalog")
async def catalog():
    """Provedores com os modelos habilitados de cada um (para o seletor do campo de mensagem)."""
    import asyncio

    async def one(p):
        try:
            data = await get_models(p["id"])
            return {"id": p["id"], "name": p["name"], "type": p["type"], "models": data["models"], "error": ""}
        except HTTPException as e:
            return {"id": p["id"], "name": p["name"], "type": p["type"], "models": [], "error": e.detail}

    return await asyncio.gather(*(one(p) for p in config.PROVIDERS.values()))


@app.get("/api/cloud-usage")
async def get_cloud_usage():
    """Cota consumida nos provedores do Ollama Cloud (um por provedor com chave)."""
    usos = await asyncio.gather(*(llm.usage(p) for p in config.PROVIDERS if llm.is_cloud(p)))
    return {"providers": [u for u in usos if u]}


class ModelSettingBody(BaseModel):
    model: str
    tool_mode: str | None = None  # native | text | auto
    vision: str | None = None     # auto | yes | no
    inference: dict | None = None  # amostragem: só o que saiu do padrão (tela Inferência)


@app.get("/api/model-settings")
def get_model_settings(model: str):
    return {"model": model, **db.get_model_setting(model)}


@app.put("/api/model-settings")
def put_model_settings(body: ModelSettingBody):
    if body.tool_mode is not None and body.tool_mode not in ("native", "text", "auto"):
        raise HTTPException(400, "tool_mode deve ser native, text ou auto")
    if body.vision is not None and body.vision not in ("auto", "yes", "no"):
        raise HTTPException(400, "vision deve ser auto, yes ou no")
    current = db.get_model_setting(body.model)
    try:
        # A tela Inferência manda só o que saiu do padrão; substitui o conjunto, não faz merge.
        inference = current["inference"] if body.inference is None else localai.clean_inference(body.inference)
    except ToolError as e:
        raise HTTPException(400, str(e))
    with db.session() as s:
        s.merge(db.ModelSetting(model=body.model, tool_mode=body.tool_mode or current["tool_mode"],
                                vision=body.vision or current["vision"], inference=inference))
        s.commit()
    return {"model": body.model, **db.get_model_setting(body.model)}


# ------------------------------------------------------------------ instâncias (servidores do agente)

@app.get("/api/servers")
async def get_servers():
    """Servidores iniciados por serve_start nesta sessão."""
    return {"servers": await asyncio.to_thread(shell.list_servers), "environment": native.describe()}


@app.get("/api/subagents/active")
def get_active_subagents():
    """Delegações rodando agora, em qualquer conversa (aba Instâncias)."""
    ativas = subagents.ativas()
    if ativas:
        ids = {a["conversation_id"] for a in ativas}
        with db.session() as s:
            titulos = {c.id: c.title for c in s.scalars(
                select(db.Conversation).where(db.Conversation.id.in_(ids)))}
        for a in ativas:
            a["conversation"] = titulos.get(a["conversation_id"]) or "sem título"
    return {"subagents": ativas}


@app.get("/api/activity")
async def get_activity():
    """Quem está ocupado agora: turno do agente e delegações por conversa, mais processos vivos.

    A lista de conversas usa isto para a bolinha, e o chat para o indicador de instância rodando.
    """
    por_conversa: dict[int, dict] = {}

    def entrada(conv_id: int) -> dict:
        return por_conversa.setdefault(int(conv_id),
                                       {"id": int(conv_id), "running": False, "subagents": 0, "servers": 0})

    for r in RUNS.values():
        if not r.finished:
            entrada(r.conv_id)["running"] = True
    for a in subagents.ativas():
        entrada(a["conversation_id"])["subagents"] += 1
    vivos = 0
    try:
        for s in await asyncio.to_thread(shell.list_servers):
            if not s.get("alive"):
                continue
            vivos += 1
            if str(s.get("conv") or "").isdigit():  # processo sabe de que conversa nasceu
                entrada(int(s["conv"]))["servers"] += 1
    except Exception:  # runner fora do ar não pode derrubar a barra lateral
        pass
    return {"conversations": list(por_conversa.values()), "servers": vivos}


@app.post("/api/servers/clear")
async def clear_servers():
    """Lixeira da aba Instâncias: some com os processos já terminados (não encerra nada)."""
    return {"removed": await asyncio.to_thread(shell.clear_finished)}


@app.get("/api/servers/{name}/log")
async def get_server_log(name: str, tail: int = 80):
    try:
        return {"name": name, "log": await asyncio.to_thread(shell.server_log, name, tail)}
    except ToolError as e:
        raise HTTPException(404, str(e))


@app.post("/api/servers/{name}/stop")
async def stop_server(name: str):
    try:
        await asyncio.to_thread(shell.stop_server, name)
    except ToolError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "name": name}


# ------------------------------------------------------------------ IA local (llama.cpp / sd.cpp)
# O llama-server sobe como filho deste processo; o Electron mata a árvore ao sair, então o modelo
# descarrega junto com o app.

class RuntimeBody(BaseModel):
    kind: str = "llama"      # llama | sd
    backend: str = "vulkan"  # vulkan | cpu | cuda


class LoadBody(BaseModel):
    path: str
    params: dict = {}


class DirsBody(BaseModel):
    dirs: list[str] = []


class DownloadBody(BaseModel):
    repo: str
    file: str
    folder: str = ""


class ImageBody(BaseModel):
    prompt: str = ""
    opts: dict = {}
    confirm: bool = False  # sim, pode descarregar o modelo que está na VRAM


class PathsBody(BaseModel):
    models_dir: str = ""
    image_dir: str = ""


class RuntimeChoiceBody(BaseModel):
    kind: str = "llama"
    backend: str = ""


class DeviceBody(BaseModel):
    id: str
    enabled: bool = True


class LocalPrefsBody(BaseModel):
    """Preferências da IA local que não pertencem a um modelo específico."""
    hf_token: str | None = None
    guardrail: str | None = None
    autoload: bool | None = None
    defaults: dict | None = None


@app.get("/api/local")
async def local_state():
    """Tudo que o painel IA local precisa: runtimes, modelos, servidor e downloads em andamento."""
    return await asyncio.to_thread(localai.state)


@app.post("/api/local/runtime")
async def local_runtime(body: RuntimeBody):
    try:
        return await asyncio.to_thread(localai.install_runtime, body.kind, body.backend)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/local/model")
async def local_model(body: LoadBody):
    """Metadados, padrões, o que o usuário mudou e a estimativa de memória de um modelo."""
    try:
        return await asyncio.to_thread(localai.model_view, body.path, body.params)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/local/model/delete")
async def local_model_delete(body: LoadBody):
    """Apaga o .gguf do disco (com os shards). Irreversível: a interface confirma antes."""
    try:
        return {"removed": await asyncio.to_thread(localai.remove_model, body.path)}
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.put("/api/local/runtime")
async def local_runtime_choice(body: RuntimeChoiceBody):
    """Troca o motor em uso (CPU, Vulkan, CUDA) sem baixar nada de novo."""
    try:
        return await asyncio.to_thread(localai.set_runtime, body.kind, body.backend)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.put("/api/local/device")
async def local_device(body: DeviceBody):
    """Liga ou desliga uma GPU para a IA local."""
    return {"gpus": await asyncio.to_thread(localai.set_device, body.id, body.enabled)}


@app.put("/api/local/prefs")
async def local_prefs(body: LocalPrefsBody):
    """Token do Hugging Face, proteção de carregamento, autoload e padrões de todo modelo."""
    try:
        if body.hf_token is not None:
            await asyncio.to_thread(localai.set_hf_token, body.hf_token)
        if body.guardrail is not None:
            await asyncio.to_thread(localai.set_guardrail, body.guardrail)
        if body.autoload is not None:
            await asyncio.to_thread(localai.set_autoload, body.autoload)
        if body.defaults is not None:
            await asyncio.to_thread(localai.set_defaults, body.defaults)
    except ToolError as e:
        raise HTTPException(400, str(e))
    return await asyncio.to_thread(localai.state)


@app.get("/api/local/inference")
async def local_inference(model: str, path: str = ""):
    """Ajustes de amostragem de um modelo qualquer (local, Ollama ou LM Studio)."""
    return await asyncio.to_thread(localai.inference_view, model, path)


@app.post("/api/local/cancel-load")
def local_cancel_load():
    """Desiste da carga em andamento (modelo grande demais, escolha errada...)."""
    return {"cancelled": localai.cancel_load()}


@app.post("/api/local/load")
async def local_load(body: LoadBody):
    try:
        return await asyncio.to_thread(localai.load, body.path, body.params)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/local/unload")
async def local_unload():
    await asyncio.to_thread(localai.unload)
    return {"ok": True}


@app.put("/api/local/params")
async def local_params(body: LoadBody):
    try:
        return await asyncio.to_thread(localai.save_params, body.path, body.params)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.get("/api/local/log")
async def local_log(tail: int = 80):
    return {"log": await asyncio.to_thread(localai.log, tail)}


@app.put("/api/local/dirs")
async def local_dirs(body: DirsBody):
    try:
        return {"dirs": await asyncio.to_thread(localai.set_dirs, body.dirs)}
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.get("/api/local/search")
async def local_search(q: str, kind: str = "text", sort: str = "relevancia"):
    try:
        return {"models": await asyncio.to_thread(localai.search, q, kind, 20, sort)}
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.get("/api/local/files")
async def local_files(repo: str, kind: str = "text"):
    try:
        return {"files": await asyncio.to_thread(localai.files, repo, kind)}
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.get("/api/local/repo")
async def local_repo(repo: str, kind: str = "text"):
    """Ficha do modelo no Hugging Face: números, capacidades, arquivos para baixar e README."""
    try:
        return await asyncio.to_thread(localai.repo_info, repo, kind)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/local/download")
async def local_download(body: DownloadBody):
    try:
        return await asyncio.to_thread(localai.download, body.repo, body.file, body.folder)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/local/jobs/{job_id}/dismiss")
def local_dismiss(job_id: str):
    """Tira um download/geração já terminado da lista (inclusive o que falhou)."""
    downloads.dismiss(job_id)
    return {"ok": True}


@app.post("/api/local/log/clear")
async def local_log_clear():
    """Apaga o log do llama-server e esquece o erro da última carga."""
    await asyncio.to_thread(localai.clear_error)
    return {"ok": True}


@app.post("/api/local/jobs/{job_id}/cancel")
def local_cancel(job_id: str):
    downloads.cancel(job_id)
    return {"ok": True}


@app.post("/api/local/image")
def local_image(body: ImageBody):
    try:
        return imagegen.start_job(body.prompt, body.opts, body.confirm)
    except imagegen.ModeloCarregado as e:
        # 409: a interface pergunta se pode descarregar e repete com confirm=true.
        raise HTTPException(409, str(e))
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.put("/api/local/paths")
async def local_paths(body: PathsBody):
    """Pastas padrão: modelos baixados e imagens geradas."""
    try:
        return await asyncio.to_thread(localai.set_paths, body.models_dir, body.image_dir)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.put("/api/local/image/defaults")
async def local_image_defaults(body: dict):
    try:
        return await asyncio.to_thread(localai.set_image, body)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.put("/api/local/image/model")
async def local_image_model(body: LoadBody):
    """Ajustes de um modelo de imagem (passos, CFG, VAE...). Só o que sai do padrão fica salvo."""
    try:
        return await asyncio.to_thread(localai.save_image_params, body.path, body.params)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.get("/api/local/image/file")
def local_image_file(path: str):
    """Só serve PNG gerado pelo painel (a pasta padrão ou a que a tela Imagem escolheu)."""
    f = Path(path).resolve()
    pastas = {imagegen.OUT_DIR.resolve(), imagegen.out_dir().resolve()}
    if not (pastas & set(f.parents)) or not f.is_file():
        raise HTTPException(404, "Imagem não encontrada")
    return FileResponse(f)


# ------------------------------------------------------------------ imagens (lotes)

MELHORAR_PROMPT = (
    "Você reescreve descrições para geradores de imagem (Stable Diffusion). Devolva SÓ o prompt "
    "reescrito, em inglês, numa linha, com termos visuais concretos: assunto, composição, luz, "
    "material, lente, estilo. Sem explicação, sem aspas, sem 'prompt:', sem negativos."
)


class LoteBody(BaseModel):
    prompt: str = ""
    opts: dict = {}
    models: list[str] = []
    count: int = 1
    seed: int = 0
    seed_mode: str = "incremental"  # incremental | aleatoria | fixa
    confirm: bool = False


class DecidirBody(BaseModel):
    keep: list[str] = []


class PromptBody(BaseModel):
    prompt: str
    provider: str
    model: str


@app.post("/api/imagens/{conv_id}/gerar")
async def imagens_gerar(conv_id: int, body: LoteBody):
    try:
        return await asyncio.to_thread(lotes.start, conv_id, body.prompt, body.opts, body.models,
                                       body.count, body.seed, body.seed_mode, body.confirm)
    except imagegen.ModeloCarregado as e:
        raise HTTPException(409, str(e))  # a tela pergunta se pode descarregar e repete com confirm=true
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/imagens/{message_id}/decidir")
async def imagens_decidir(message_id: int, body: DecidirBody):
    try:
        return await asyncio.to_thread(lotes.decidir, message_id, body.keep)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/imagens/{message_id}/cancelar")
def imagens_cancelar(message_id: int):
    try:
        return lotes.cancelar(message_id)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/imagens/descartadas/limpar")
async def imagens_limpar(dias: int = 0):
    """Expurgo manual da pasta descartadas/. Sem `dias`, apaga tudo o que está lá."""
    return {"apagados": await asyncio.to_thread(lotes.limpar_descartadas, dias)}


@app.post("/api/imagens/prompt")
async def imagens_prompt(body: PromptBody):
    """Passa o pedido por um LLM para virar um prompt de imagem decente. Opcional: a geração não usa."""
    if not body.prompt.strip():
        raise HTTPException(400, "Escreva alguma coisa antes de melhorar.")
    out = ""
    try:
        async for kind, val in llm.chat_stream(body.provider, body.model,
                                               [{"role": "system", "content": MELHORAR_PROMPT},
                                                {"role": "user", "content": body.prompt}], None, 8192):
            if kind == "content":
                out += val
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    texto = split_think(out)[1].strip().strip("`").strip()
    if not texto:
        raise HTTPException(400, "O modelo não devolveu um prompt.")
    return {"prompt": texto}


# ------------------------------------------------------------------ comparar modelos


class CompararBody(BaseModel):
    prompt: str = ""
    itens: list[dict] = []          # [{"provider","model"} | {"path"}]
    modo: str = "paralelo"          # paralelo | sequencial (o .gguf só entra no sequencial)
    system: str = ""
    effort: str = "medio"
    cego: bool = False
    confirm: bool = False


class VotoBody(BaseModel):
    voto: str = ""                  # id do item vencedor; o mesmo de novo desfaz


def _sse_comparar(message_id: int) -> StreamingResponse:
    """Retrato inteiro a cada tick em vez de evento por token: a comparação toda cabe num JSON,
    reconectar não precisa de cursor e recarregar a página se resolve sozinho."""
    async def stream():
        while True:
            try:
                estado = comparar.estado(message_id)
            except ToolError as e:
                yield f"data: {json.dumps({'erro': str(e)}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps(estado, ensure_ascii=False, default=str)}\n\n"
            if estado["status"] != "rodando":
                return
            await asyncio.sleep(comparar.TICK)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/comparar/{conv_id}/rodar")
async def comparar_rodar(conv_id: int, body: CompararBody):
    try:
        msg = comparar.start(conv_id, body.prompt, body.itens, body.modo, body.system, body.effort,
                             body.cego, body.confirm)
    except comparar.ModeloCarregado as e:
        raise HTTPException(409, str(e))  # a tela pergunta se pode descarregar e repete com confirm=true
    except ToolError as e:
        raise HTTPException(400, str(e))
    return _sse_comparar(msg["id"])


@app.get("/api/comparar/{message_id}/stream")
def comparar_stream(message_id: int):
    """Reconexão: acompanhar uma comparação já em andamento, ou reabrir uma do histórico."""
    return _sse_comparar(message_id)


@app.post("/api/comparar/{message_id}/cancelar")
def comparar_cancelar(message_id: int):
    return comparar.cancelar(message_id)


@app.post("/api/comparar/{message_id}/voto")
def comparar_voto(message_id: int, body: VotoBody):
    try:
        return comparar.votar(message_id, body.voto)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.get("/api/comparar/placar")
def comparar_placar():
    return comparar.placar()


# ------------------------------------------------------------------ pesquisa profunda


class PesquisaBody(BaseModel):
    pergunta: str = ""
    profundidade: str = "normal"    # rapida | normal | funda
    provider: str = ""
    model: str = ""
    contexto: str = ""              # respostas das perguntas de esclarecimento
    continuar_de: int = 0           # message_id de uma pesquisa anterior
    ex_provider: str = ""           # modelo que lê as páginas; vazio = slot "rapido" dos subagentes
    ex_model: str = ""
    formato: str = "auto"           # auto | produto | comparar | guia | checagem
    teto: int = 0                   # tempo máximo em segundos; 0 = o do preset
    rodadas: int = 0                # nº de rodadas; só vale com profundidade "personalizado"


class PerguntasBody(BaseModel):
    pergunta: str = ""
    provider: str = ""
    model: str = ""


def _sse_pesquisa(message_id: int) -> StreamingResponse:
    """Mesmo desenho do comparar: retrato inteiro por tick em vez de evento por token."""
    async def stream():
        while True:
            try:
                estado = pesquisa.estado(message_id)
            except ToolError as e:
                yield f"data: {json.dumps({'erro': str(e)}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps(estado, ensure_ascii=False, default=str)}\n\n"
            if estado["status"] != "rodando":
                return
            await asyncio.sleep(pesquisa.TICK)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/pesquisa/{conv_id}/rodar")
async def pesquisa_rodar(conv_id: int, body: PesquisaBody):
    try:
        msg = pesquisa.start(conv_id, body.pergunta, body.provider, body.model, body.profundidade,
                             body.contexto, body.continuar_de, body.ex_provider, body.ex_model,
                             body.formato, body.teto, body.rodadas)
    except ToolError as e:
        raise HTTPException(400, str(e))
    return _sse_pesquisa(msg["id"])


@app.get("/api/pesquisa/{message_id}/stream")
def pesquisa_stream(message_id: int):
    """Reconexão: acompanhar uma pesquisa em andamento ou reabrir uma do histórico."""
    return _sse_pesquisa(message_id)


@app.post("/api/pesquisa/{message_id}/cancelar")
def pesquisa_cancelar(message_id: int):
    return pesquisa.cancelar(message_id)


@app.post("/api/pesquisa/perguntas")
async def pesquisa_perguntas(body: PerguntasBody):
    """Perguntas de esclarecimento antes de sair buscando."""
    try:
        return {"perguntas": await pesquisa.perguntas(body.pergunta, body.provider, body.model)}
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.get("/api/pesquisa/{message_id}/relatorio")
def pesquisa_relatorio(message_id: int):
    """A página do relatório. O botão da aba abre isto numa janela do navegador do usuário."""
    try:
        est = pesquisa.estado(message_id)
    except ToolError as e:
        raise HTTPException(404, str(e))
    return HTMLResponse(relatorio.html_do(est, est.get("relatorio") or ""))


@app.post("/api/pesquisa/{message_id}/discutir")
def pesquisa_discutir(message_id: int):
    try:
        return pesquisa.discutir(message_id)
    except ToolError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ navegador integrado
# Uma sessão por conversa: `conv` é o id da conversa ("0" = rascunho da tela inicial).

class NavigateBody(BaseModel):
    url: str = ""
    action: str = ""  # back | forward | reload (vazio = goto url)


class InputBody(BaseModel):
    type: str  # click | dblclick | move | wheel | key | text
    x: float = 0
    y: float = 0
    button: str = "left"
    delta_x: float = 0
    delta_y: float = 0
    key: str = ""
    text: str = ""


class ViewportBody(BaseModel):
    width: int
    height: int
    dpr: float = 1.0  # devicePixelRatio da tela do painel


class TabsBody(BaseModel):
    action: str  # new | switch | close
    index: int | None = None
    url: str = ""


def _sess(conv: str):
    return MANAGER.session(conv)


@app.get("/api/browser")
async def get_browser(conv: str = "0"):
    return await _sess(conv).state_with_title()


@app.get("/api/browser/stream")
def browser_stream(conv: str = "0"):
    """SSE do espelho: evento `state` + frames do screencast enquanto houver assinante."""
    async def stream():
        async for ev in _sess(conv).frames():
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/browser/navigate")
async def browser_navigate(body: NavigateBody, conv: str = "0"):
    try:
        await _sess(conv).navigate(body.url, body.action)
    except ToolError as e:
        raise HTTPException(400, str(e))
    return await _sess(conv).state_with_title()


@app.post("/api/browser/input")
async def browser_input(body: InputBody, conv: str = "0"):
    """Mouse/teclado do usuário no espelho. Sem aprovação: é o usuário agindo, não o modelo."""
    try:
        await _sess(conv).input(body.model_dump())
    except ToolError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/browser/viewport")
async def browser_viewport(body: ViewportBody, conv: str = "0"):
    """O painel da UI redimensionou: as abas do Chromium passam a ter esse tamanho."""
    try:
        await _sess(conv).set_viewport(body.width, body.height, body.dpr)
    except ToolError as e:
        raise HTTPException(400, str(e))
    return _sess(conv).state()


@app.post("/api/browser/tabs")
async def browser_tabs(body: TabsBody, conv: str = "0"):
    s = _sess(conv)
    try:
        if body.action == "new":
            await s.new_tab(body.url)
        elif body.action == "switch":
            await s.switch_tab(body.index)
        elif body.action == "close":
            await s.close_tab(body.index)
        else:
            raise HTTPException(400, "action deve ser new, switch ou close")
    except ToolError as e:
        raise HTTPException(400, str(e))
    return await s.state_with_title()


@app.post("/api/browser/upload")
async def browser_upload(conv: str = "0", file: UploadFile | None = File(None)):
    """Responde ao seletor de arquivo aberto pela página: sem arquivo = cancelar."""
    s = _sess(conv)
    try:
        if file is None:
            await s.upload([])
        else:
            root = _conv_root(conv)
            att = uploads.save(file.filename or "arquivo", await file.read(), file.content_type, root)
            await s.upload([str(root / att["path"])])
    except (ToolError, ValueError, OSError) as e:
        raise HTTPException(400, str(e))
    return await s.state_with_title()


@app.post("/api/browser/close")
async def browser_close(conv: str = "0"):
    await MANAGER.close(conv)
    return _sess(conv).state()


@app.get("/api/browser/host")
def browser_host():
    """SSE para o Electron (modo nativo): pedidos de criar view e de qual view mostrar."""
    async def stream():
        async for ev in MANAGER.host_events():
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/browser/host/popup")
async def browser_host_popup(body: NavigateBody, conv: str = "0"):
    """Uma view nativa quis abrir janela (window.open, target=_blank): vira aba nova da mesma sessão."""
    try:
        await _sess(conv).new_tab(body.url)
    except ToolError as e:
        raise HTTPException(400, str(e))
    return await _sess(conv).state_with_title()


# ------------------------------------------------------------------ conversas

def _conv_dict(c: db.Conversation) -> dict:
    return {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat(), "kind": c.kind or "agent",
            "workspace": c.workspace, "workspace_label": workspace.label(c.workspace),
            "pinned": bool(c.pinned), "archived": bool(c.archived)}


@app.get("/api/conversations")
def list_conversations(kind: str | None = None, archived: bool = False):
    """Sem `kind`, todas; com `kind`, só as da seção (chat ou agent). Fixadas primeiro; arquivadas à parte."""
    with db.session() as s:
        q = select(db.Conversation).order_by(db.Conversation.pinned.desc(), db.Conversation.updated_at.desc())
        q = q.where(db.Conversation.archived.is_(True) if archived else db.Conversation.archived.isnot(True))
        if kind:
            q = q.where(db.Conversation.kind == kind)
        return [_conv_dict(c) for c in s.scalars(q).all()]


@app.get("/api/conversations/search")
def search_conversations(q: str, kind: str | None = None, limit: int = 20):
    """Busca no conteúdo das mensagens; devolve as conversas com um trecho de onde casou."""
    q = q.strip()
    if len(q) < 2:
        return []
    with db.session() as s:
        rows = s.execute(select(db.Message.conversation_id, db.Message.content)
                         .where(db.Message.content.ilike(f"%{q}%"), db.Message.role.in_(("user", "assistant")))
                         .order_by(db.Message.id.desc()).limit(300)).all()
        hits: dict[int, str] = {}
        for cid, content in rows:
            if cid not in hits:
                i = max(0, (content or "").lower().find(q.lower()))
                hits[cid] = (content or "")[max(0, i - 40): i + 90].replace("\n", " ").strip()
        if not hits:
            return []
        convs = s.scalars(select(db.Conversation).where(db.Conversation.id.in_(list(hits)))
                          .order_by(db.Conversation.updated_at.desc())).all()
        return [{**_conv_dict(c), "snippet": hits[c.id]} for c in convs if not kind or c.kind == kind][:limit]


@app.post("/api/conversations")
def create_conversation(body: dict | None = None):
    folder = (body or {}).get("workspace") or None
    if folder:
        try:
            workspace.resolve(folder)
            folder = workspace.normalize(folder)
        except workspace.WorkspaceError as e:
            raise HTTPException(400, str(e))
    kind = (body or {}).get("kind") or "agent"
    if kind not in ("chat", "agent", "imagem", "comparar", "pesquisa"):
        raise HTTPException(400, "kind deve ser chat, agent, imagem, comparar ou pesquisa")
    with db.session() as s:
        c = db.Conversation(workspace=folder, kind=kind)
        s.add(c)
        s.commit()
        return _conv_dict(c)


def _get_conv(s, conv_id: int) -> db.Conversation:
    c = s.get(db.Conversation, conv_id)
    if not c:
        raise HTTPException(404, "Conversa não encontrada")
    return c


@app.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: int):
    with db.session() as s:
        c = _get_conv(s, conv_id)
        return {**_conv_dict(c), "messages": [m.to_dict() for m in c.messages]}


class BulkBody(BaseModel):
    ids: list[int]
    action: str  # archive | unarchive | pin | unpin | delete


@app.post("/api/conversations/bulk")
async def bulk_conversations(body: BulkBody):
    """Ação em várias conversas de uma vez (seleção múltipla na barra lateral)."""
    if body.action not in ("archive", "unarchive", "pin", "unpin", "delete"):
        raise HTTPException(400, "action deve ser archive, unarchive, pin, unpin ou delete")
    done, skipped, closed = 0, [], []
    with db.session() as s:
        for cid in body.ids:
            c = s.get(db.Conversation, cid)
            if not c:
                continue
            if body.action == "delete":
                if active_run(cid):
                    skipped.append(cid)  # não apaga conversa com execução em andamento
                    continue
                s.query(db.Checkpoint).filter(db.Checkpoint.conversation_id == cid).delete()
                s.delete(c)
                closed.append(cid)
            elif body.action in ("archive", "unarchive"):
                c.archived = body.action == "archive"
            else:
                c.pinned = body.action == "pin"
            done += 1
        s.commit()
    for cid in closed:
        await MANAGER.close(str(cid))
        mirror.remove(cid)
    return {"ok": True, "done": done, "skipped": skipped}


class ConvPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


@app.patch("/api/conversations/{conv_id}")
def patch_conversation(conv_id: int, body: ConvPatch):
    """Renomear, fixar/desafixar, arquivar/desarquivar."""
    with db.session() as s:
        c = _get_conv(s, conv_id)
        if body.title is not None:
            c.title = body.title.strip()[:200] or c.title
        if body.pinned is not None:
            c.pinned = body.pinned
        if body.archived is not None:
            c.archived = body.archived
        s.commit()
        out = _conv_dict(c)
    if body.title is not None:
        mirror.write(conv_id)  # o título está no nome do arquivo: regrava e apaga o antigo
    return out


@app.get("/api/conversations/{conv_id}/export")
def export_conversation(conv_id: int):
    """A conversa em Markdown (download). O mesmo texto do espelho em disco."""
    with db.session() as s:
        texto = mirror.markdown(_get_conv(s, conv_id))
    return PlainTextResponse(texto, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="forja-conversa-{conv_id}.md"'})


@app.post("/api/conversations/{conv_id}/compact")
async def compact_now(conv_id: int, body: dict):
    """Compacta o histórico antigo agora (o mesmo resumo da compactação automática)."""
    if active_run(conv_id):
        raise HTTPException(409, "Espere a execução atual terminar")
    msgs = _load(conv_id)
    until = compact.split_point(msgs)
    if until is None:
        raise HTTPException(409, "Nada para compactar: a conversa só tem os últimos turnos.")
    try:
        text = compact.transcript(msgs, until, max_chars=int(config.NUM_CTX * 4 * 0.5))
        summary = await compact.summarize(body["provider"], body["model"], text, config.NUM_CTX)
    except (KeyError, llm.LLMError) as e:
        raise HTTPException(400, f"Falha ao compactar: {e}")
    if not summary:
        raise HTTPException(400, "O modelo devolveu um resumo vazio.")
    m = _save(conv_id, role="event", content=summary, meta={"kind": "summary", "covers_until": until})
    return m.to_dict()


@app.get("/api/conversations/{conv_id}/changes")
def conversation_changes(conv_id: int):
    """Arquivos que o agente alterou nesta conversa (write_file/edit_file), com diff do antes → agora."""
    from .tools import _diff
    with db.session() as s:
        rows = list(s.scalars(select(db.Checkpoint).where(db.Checkpoint.conversation_id == conv_id)
                              .order_by(db.Checkpoint.id)))
    first: dict[str, db.Checkpoint] = {}
    for cp in rows:
        first.setdefault(cp.path, cp)  # o checkpoint mais antigo é o estado original
    files = []
    for path, cp in first.items():
        p = Path(path)
        label = workspace.to_host(p) or path
        before = (cp.content or b"").decode("utf-8", "replace") if cp.existed else None
        try:
            after = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
        except OSError:
            after = None
        if before is None and after is None:
            continue
        status = ("created" if before is None else "deleted" if after is None
                  else "unchanged" if before == after else "modified")
        d = _diff(before or "", after or "", Path(label).name) if status != "unchanged" else ""
        body_lines = [l for l in d.splitlines() if not l.startswith(("+++", "---"))]
        files.append({"path": label, "status": status, "diff": d[:100_000],
                      "additions": sum(1 for l in body_lines if l.startswith("+")),
                      "deletions": sum(1 for l in body_lines if l.startswith("-"))})
    return {"files": files}


@app.get("/api/conversations/{conv_id}/skills")
def conversation_skills(conv_id: int | str):
    """Comandos `/`: ações do Forja + skills da pasta da conversa (.forja/skills/*.md)."""
    return {"skills": skills.list_for(_conv_root(conv_id))}


class OpenBody(BaseModel):
    conv: int | str | None = None
    path: str
    mode: str = "editor"  # editor | reveal


@app.post("/api/open")
async def open_in_system(body: OpenBody):
    """Abre um arquivo no editor (VS Code, senão o programa padrão) ou o revela no Explorer/Finder."""
    raw = body.path.strip()
    if len(raw) > 2 and raw[1] == ":" or raw.startswith("/") and not raw.startswith("/workspace"):
        host = workspace.normalize(raw)
    else:
        from .tools import resolve_path
        try:
            host = workspace.to_host(resolve_path(_conv_root(body.conv), raw))
        except ToolError as e:
            raise HTTPException(400, str(e))
    try:
        return {"opened": await asyncio.to_thread(native.open_path, host, body.mode)}
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ git da pasta da conversa

class CommitBody(BaseModel):
    provider: str | None = None
    model: str | None = None
    message: str | None = None
    dry: bool = False  # só gerar a mensagem


class PrBody(BaseModel):
    title: str = ""
    body: str = ""


class WorktreeBody(BaseModel):
    branch: str


def _git_root(conv_id: int) -> Path:
    if active_run(conv_id):
        raise HTTPException(409, "Espere a execução atual terminar")
    return _conv_root(conv_id)


@app.get("/api/conversations/{conv_id}/git")
async def git_status(conv_id: int):
    return await asyncio.to_thread(gitops.status, _conv_root(conv_id))


@app.get("/api/conversations/{conv_id}/git/diff")
async def git_diff(conv_id: int, path: str | None = None):
    return {"diff": await asyncio.to_thread(gitops.diff, _conv_root(conv_id), path)}


@app.post("/api/conversations/{conv_id}/git/commit")
async def git_commit(conv_id: int, body: CommitBody):
    root = _git_root(conv_id)
    try:
        message = body.message
        if not message:
            if not body.provider or not body.model:
                raise HTTPException(400, "Sem mensagem: informe provider e model para gerar uma.")
            message = await gitops.generate_message(root, body.provider, body.model)
        if body.dry:
            return {"message": message}
        return await asyncio.to_thread(gitops.commit, root, message)
    except (ToolError, llm.LLMError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/conversations/{conv_id}/git/pr")
async def git_pr(conv_id: int, body: PrBody):
    try:
        return await asyncio.to_thread(gitops.create_pr, _git_root(conv_id), body.title, body.body)
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/conversations/{conv_id}/git/worktree")
async def git_worktree(conv_id: int, body: WorktreeBody):
    """Cria um worktree numa branch nova e passa a conversa a trabalhar nele."""
    root = _git_root(conv_id)
    try:
        result = await asyncio.to_thread(gitops.worktree, root, body.branch.strip())
        workspace.resolve(result["path"])
    except (ToolError, workspace.WorkspaceError) as e:
        raise HTTPException(400, str(e))
    with db.session() as s:
        c = _get_conv(s, conv_id)
        c.workspace = result["path"]
        s.commit()
        return {**result, "conversation": _conv_dict(c)}


# ------------------------------------------------------------------ terminal do usuário

class TermStart(BaseModel):
    conv: int | str | None = None


@app.post("/api/term/start")
async def term_start(body: TermStart):
    try:
        return await asyncio.to_thread(terminal.start, _conv_root(body.conv))
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/term/{tid}/input")
async def term_input(tid: str, body: dict):
    try:
        await asyncio.to_thread(terminal.send, tid, str(body.get("text", "")))
    except ToolError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/term/{tid}/poll")
async def term_poll(tid: str, cursor: int = 0):
    try:
        return await asyncio.to_thread(terminal.poll, tid, cursor)
    except ToolError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/term/{tid}")
async def term_close(tid: str):
    await asyncio.to_thread(terminal.close, tid)
    return {"ok": True}


@app.put("/api/conversations/{conv_id}/workspace")
def set_workspace(conv_id: int, body: dict):
    """Troca a pasta de trabalho da conversa (vale a partir da próxima mensagem)."""
    folder = body.get("workspace") or None
    if active_run(conv_id):
        raise HTTPException(409, "Espere a execução atual terminar")
    if folder:
        try:
            workspace.resolve(folder)
            folder = workspace.normalize(folder)
        except workspace.WorkspaceError as e:
            raise HTTPException(400, str(e))
    with db.session() as s:
        c = _get_conv(s, conv_id)
        c.workspace = folder
        s.commit()
        return _conv_dict(c)


@app.get("/api/conversations/{conv_id}/checkpoints")
def list_checkpoints(conv_id: int):
    return {str(k): v for k, v in checkpoints.summary(conv_id).items()}


@app.post("/api/conversations/{conv_id}/checkpoints/restore")
def restore_checkpoints(conv_id: int, body: dict):
    """Desfaz as alterações de arquivo do turno indicado e dos seguintes."""
    if active_run(conv_id):
        raise HTTPException(409, "Espere a execução atual terminar")
    return {"restored": checkpoints.restore_from(conv_id, int(body["turn_id"]))}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: int):
    with db.session() as s:
        s.delete(_get_conv(s, conv_id))
        s.commit()
    mirror.remove(conv_id)  # o .md espelhado vai junto
    await MANAGER.close(str(conv_id))  # a sessão do navegador morre com a conversa
    return {"ok": True}


# ------------------------------------------------------------------ execução

class RunBody(BaseModel):
    content: str | None = None  # None = continua de onde parou (regenerar ou mensagem editada)
    provider: str
    model: str
    permission: str = "manual"  # auto | manual | edits | plan | bypass
    effort: str = "medio"       # baixo | medio | alto | maximo
    attachments: list | None = None


@app.get("/api/files")
def get_file(path: str, conv: str = "0"):
    """Serve um arquivo da pasta de trabalho (miniatura de anexo). Confinado como as ferramentas."""
    from .tools import ToolError, resolve_path
    try:
        p = resolve_path(_conv_root(conv), path)
    except ToolError as e:
        raise HTTPException(400, str(e))
    if not p.is_file():
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(p)


MAX_WALK = 20_000  # teto da varredura: repo grande não pode travar o menu do @


@app.get("/api/workspace/files")
def workspace_files(conv: str = "0", q: str = "", limit: int = 50):
    """Caminhos da pasta da conversa que casam com `q`, para o menu do @ no campo de mensagem."""
    import os

    from .tools import IGNORED_DIRS

    root = _conv_root(conv)
    alvo = q.strip().lower().replace("\\", "/")
    achados: list[str] = []
    vistos = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            vistos += 1
            rel = (Path(dirpath) / name).relative_to(root).as_posix()
            if not alvo or alvo in rel.lower():
                achados.append(rel)
                if len(achados) >= max(1, min(limit, 200)):
                    return {"files": achados}
        if vistos >= MAX_WALK:
            break
    return {"files": achados}


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...), conv: str = "0"):
    """Salva o anexo dentro da pasta de trabalho da conversa para o agente conseguir abrir."""
    try:
        return uploads.save(file.filename or "arquivo", await file.read(), file.content_type, _conv_root(conv))
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


def _sse(run: Run, cursor: int) -> StreamingResponse:
    # Desconectar só encerra esta assinatura; a execução continua em background.
    async def stream():
        async for ev in run.subscribe(cursor):
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/conversations/{conv_id}/run")
async def start_run(conv_id: int, body: RunBody):
    with db.session() as s:
        _get_conv(s, conv_id)
    if body.permission not in policy.MODES:
        raise HTTPException(400, f"permission deve ser um de {', '.join(policy.MODES)}")
    if body.effort not in ("baixo", "medio", "alto", "maximo", "extremo"):
        raise HTTPException(400, "effort deve ser baixo, medio, alto, maximo ou extremo")
    if active_run(conv_id):
        raise HTTPException(409, "Esta conversa já tem uma execução em andamento")
    with db.session() as s:
        kind = _get_conv(s, conv_id).kind or "agent"  # o tipo é da conversa, não do pedido
    run = Run(conv_id)
    RUNS[run.id] = run
    run.start(RunRequest(mode=kind, **body.model_dump()))
    return _sse(run, 0)


@app.post("/api/conversations/{conv_id}/rewind")
def rewind(conv_id: int, body: dict):
    """Apaga da mensagem indicada em diante (editar) ou tudo depois dela (regenerar)."""
    message_id = int(body.get("message_id"))
    keep = bool(body.get("keep"))  # True = mantém a própria mensagem (regenerar)
    if active_run(conv_id):
        raise HTTPException(409, "Espere a execução atual terminar")
    # Arquivos: desfazer as alterações dos turnos apagados, ou esquecer os checkpoints deles.
    restored = checkpoints.restore_from(conv_id, message_id) if body.get("restore_files") else []
    if not body.get("restore_files"):
        checkpoints.forget_from(conv_id, message_id)
    with db.session() as s:
        c = _get_conv(s, conv_id)
        removed = [m for m in c.messages if (m.id > message_id if keep else m.id >= message_id)]
        for m in removed:
            s.delete(m)
        s.commit()
        c = _get_conv(s, conv_id)
        out = {"messages": [m.to_dict() for m in c.messages], "removed": len(removed), "restored": restored}
    mirror.write(conv_id)  # turnos apagados somem do espelho também
    return out


@app.get("/api/conversations/{conv_id}/live")
async def live(conv_id: int):  # async: roda no event loop, atômico em relação ao publish()
    """Mensagens salvas + estado da execução ativa (rascunho, aprovações pendentes, cursor)."""
    with db.session() as s:
        c = _get_conv(s, conv_id)
        messages = [m.to_dict() for m in c.messages]
    run = active_run(conv_id)  # sem await entre as duas leituras: snapshot consistente
    return {"messages": messages, "run": run.snapshot() if run else None}


@app.get("/api/runs/{run_id}/stream")
def stream_run(run_id: str, cursor: int = 0):
    return _sse(_get_run(run_id), cursor)


@app.post("/api/runs/{run_id}/queue")
async def queue_message(run_id: str, body: dict):
    """Mensagem enviada durante a execução: entra como turno novo no próximo passo do agente."""
    content = str(body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "Mensagem vazia")
    run = _get_run(run_id)
    if run.finished:
        raise HTTPException(409, "A execução já terminou; envie normalmente")
    run.queue.append(content)
    await run.publish({"type": "queued", "content": content, "pending": len(run.queue)})
    return {"ok": True, "pending": len(run.queue)}


class ApproveBody(BaseModel):
    call_id: str
    approved: bool
    mode: str | None = None       # modo escolhido ao aprovar um plano
    feedback: str | None = None   # o que mudar no plano, quando não aprovado
    answer: str | None = None     # resposta a um ask_user (formato antigo, uma pergunta)
    answers: list | None = None   # respostas do ask_user em lote, na ordem das perguntas


def _get_run(run_id: str) -> Run:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Execução não está mais ativa")
    return run


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: str, body: ApproveBody):
    decision = {"approved": body.approved, "mode": body.mode, "feedback": body.feedback,
                "answer": body.answer, "answers": body.answers}
    if not _get_run(run_id).resolve(body.call_id, decision):
        raise HTTPException(409, "Nenhuma aprovação pendente para esta chamada")
    return {"ok": True}


@app.post("/api/runs/{run_id}/permission")
def change_permission(run_id: str, body: dict):
    """Troca o modo de permissão no meio da resposta; vale já na próxima ferramenta.

    Aprovações abertas que o novo modo aceita são liberadas na hora (trocar para Ignorar
    permissões com um card na tela executa aquele card em vez de deixar tudo parado).
    """
    mode = str(body.get("permission") or "")
    if mode not in policy.MODES or mode == "plan":  # entrar no Plano no meio não faz sentido
        raise HTTPException(400, f"permission deve ser um de {', '.join(m for m in policy.MODES if m != 'plan')}")
    run = _get_run(run_id)
    if run.finished:
        raise HTTPException(409, "A execução já terminou")
    freed = run.set_permission(mode)
    return {"ok": True, "permission": mode, "freed": freed}


@app.post("/api/runs/{run_id}/stop")
def stop(run_id: str):
    _get_run(run_id).stop()
    return {"ok": True}


# ------------------------------------------------------------------ interface (build do Vite)
# No app empacotado o Electron passa FORJA_WEB; em dev, o Vite serve a UI e isto fica desligado.

if config.WEB_DIR and config.WEB_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=config.WEB_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):  # rota de API inexistente: 404, e não a interface
            raise HTTPException(404, "Rota não encontrada")
        f = config.WEB_DIR / path
        return FileResponse(f if path and f.is_file() else config.WEB_DIR / "index.html")

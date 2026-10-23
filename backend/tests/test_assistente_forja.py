import asyncio

from app import agent, assistente


class RunFalso:
    def __init__(self, rid, pedidos, tarefas=()):
        self.id, self.conv_id, self.finished = rid, 7, False
        self.approvals = {f"c{i}": {"call": {"name": "run_command", "arguments": {"command": cmd}}} for i, cmd in enumerate(pedidos)}
        self.tasks, self.decisoes, self.parado = list(tarefas), [], False

    def resolve(self, cid, decisao):
        self.decisoes.append((cid, decisao["approved"]))
        self.approvals.pop(cid, None)
        return True

    def stop(self):
        self.parado = True


def _prepara(monkeypatch, *runs):
    falas = []
    monkeypatch.setattr(assistente, "fala", falas.append)
    monkeypatch.setattr(assistente, "_manda", lambda *a, **k: None)
    monkeypatch.setattr(assistente, "_titulo", lambda conv: "loja")
    monkeypatch.setattr(agent, "RUNS", {r.id: r for r in runs})
    assistente._pendente.clear()
    return falas


def test_status_do_agente(monkeypatch):
    falas = _prepara(monkeypatch, RunFalso("r1", [], [{"content": "carrinho", "status": "completed"},
                                                     {"content": "checkout", "status": "in_progress"}]))
    asyncio.run(assistente._forja_voz("agente", {"acao": "status"}))
    assert falas[-1] == "O agente de loja está trabalhando, 1 de 2 tarefas feitas, agora: checkout, sem pedido esperando."


def test_aprova_um_pedido_simples_direto(monkeypatch):
    r = RunFalso("r1", ["npm install"])
    falas = _prepara(monkeypatch, r)
    asyncio.run(assistente._forja_voz("agente", {"acao": "aprovar"}))
    assert r.decisoes == [("c0", True)] and falas[-1] == "Aprovei: rodar o comando npm install."


def test_comando_perigoso_ou_varios_pedem_confirmacao(monkeypatch):
    r = RunFalso("r1", ["rm -rf build"])
    falas = _prepara(monkeypatch, r)
    asyncio.run(assistente._forja_voz("agente", {"acao": "aprovar"}))
    assert r.decisoes == [] and falas[-1].endswith("Confirma?") and assistente._pendente["tipo"] == "aprovar"
    asyncio.run(assistente._aprovar_respondido("pode mandar"))
    assert r.decisoes == [("c0", True)]
    r2 = RunFalso("r2", ["npm test", "npm run build"])
    falas = _prepara(monkeypatch, r2)
    asyncio.run(assistente._forja_voz("agente", {"acao": "aprovar"}))
    assert r2.decisoes == [] and "Confirma?" in falas[-1]
    asyncio.run(assistente._aprovar_respondido("não"))
    assert r2.decisoes == [("c0", False), ("c1", False)]


def test_recusa_e_para(monkeypatch):
    r = RunFalso("r1", ["git push"])
    falas = _prepara(monkeypatch, r)
    asyncio.run(assistente._forja_voz("agente", {"acao": "recusar"}))
    assert r.decisoes == [("c0", False)] and falas[-1].startswith("Recusei")
    asyncio.run(assistente._forja_voz("agente", {"acao": "parar"}))
    assert r.parado and falas[-1] == "Parei o agente de loja."
    falas = _prepara(monkeypatch)
    monkeypatch.setattr(assistente, "_projetos", lambda: {})
    asyncio.run(assistente._forja_voz("agente", {"acao": "status"}))
    assert falas[-1] == "Nenhum agente está trabalhando agora."


def test_ditar_e_texto_falavel(monkeypatch):
    falas = _prepara(monkeypatch)
    n0 = assistente._ditar["n"]
    asyncio.run(assistente._forja_voz("ditar", {}))
    assert assistente._ditar["n"] == n0 + 1 and falas[-1].startswith("Pode falar")
    md = "## Pronto\n\nCriei o **carrinho**:\n\n```js\nconst a = 1;\n```\n\n- veja [o arquivo](src/a.js)\n"
    lido = assistente._texto_falavel(md)
    assert "(código na tela)" in lido and "veja o arquivo" in lido and "Criei o carrinho" in lido
    assert not any(c in lido for c in "*#`[]") and "const a" not in lido


def test_rotas_do_agente_nao_abrem_trabalho_novo():
    for frase in ["como está o agente da loja?", "aprova o que o agente pediu no forja"]:
        assert assistente.SABER.search(assistente._sem_acento(frase)), frase

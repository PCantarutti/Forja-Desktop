"""Piloto automático: a fila do cronograma, a pausa, a retomada depois de uma queda. Os geradores (resumo, prova,
busca) são falsos — o que se testa aqui é a ordem, o vínculo tarefa → resultado e o estado."""
import asyncio
from datetime import date, timedelta

import pytest

from app import db, estudos as E, estudos_busca as B, estudos_piloto as PIL, estudos_prova as P, estudos_revisao as R, mirror


@pytest.fixture(autouse=True)
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "ROOT", tmp_path / "conversas")
    monkeypatch.setattr(E, "RAIZ", tmp_path / "estudos")
    monkeypatch.setattr(PIL, "SONDA", 0.01)
    monkeypatch.setattr(E, "modelos", lambda *a: (None, None, False))
    E._RUNS.clear()
    PIL._VIVOS.clear()
    yield
    E._RUNS.clear()
    E._TAREFAS.clear()   # as tasks falsas são de um loop que já fechou


FEITO: list[tuple] = []


def _falsos(monkeypatch, status=lambda tipo, arg: "pronto", pausa=0.02):
    """Cada start falso grava a mensagem 'running', fica no _RUNS um instante e termina com `status(tipo, arg)`."""
    FEITO.clear()

    def fazer(tipo):
        def start(conv_id, arg=None, *resto):
            if E.rodando(conv_id):
                raise E.ToolError("Este estudo já está rodando.")
            FEITO.append((tipo, E.MATERIA.get(), arg if isinstance(arg, str) else (arg or {}).get("instrucoes", "geral" if (arg or {}).get("geral") else "")))
            msg = E._save(conv_id, role="assistant", content="", status="running", meta={"estudos": {"tipo": tipo}})
            run = {"message_id": msg.id, "conv_id": conv_id, "tipo": tipo, "cancelar": False}
            E._RUNS[msg.id] = run

            async def termina():
                await asyncio.sleep(pausa)
                fim = "cancelado" if run["cancelar"] else status(tipo, arg)
                with db.session() as s:
                    s.get(db.Message, msg.id).status = fim
                    s.commit()
                E._RUNS.pop(msg.id, None)
            E._TAREFAS.add(asyncio.get_running_loop().create_task(termina()))
            return msg.to_dict()
        return start

    monkeypatch.setattr(E, "start", fazer("resumo"))
    monkeypatch.setattr(P, "start", fazer("prova"))
    monkeypatch.setattr(B, "start", fazer("busca"))


def _objetivo(dias=3) -> tuple[int, dict, list[str]]:
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="Prefeitura de Contagem")
        s.add(c)
        s.commit()
        conv = c.id
    pt = E.nova_materia(conv, "Português")
    rl = E.nova_materia(conv, "Raciocínio")
    hoje = date.today()
    ds = [(hoje + timedelta(days=i)).isoformat() for i in range(dias)]
    topicos = ["Português · Crase", "Raciocínio · Porcentagem", "Português · Regência"]
    plano = {"data": ds[-1], "minutos": 60, "criado": "", "dias": [
        {"dia": d, "tarefas": [{"id": f"{d}-0", "tipo": "estudar", "texto": "", "topico": topicos[i % 3], "minutos": 36, "feito": False},
                               {"id": f"{d}-1", "tipo": "revisar", "texto": "", "topico": "", "minutos": 24, "feito": False}]}
        for i, d in enumerate(ds)]}
    plano["dias"][-1]["tarefas"] = [{"id": f"{ds[-1]}-0", "tipo": "simulado", "texto": "", "topico": "", "minutos": 60, "feito": False}]
    R._mudar(conv, lambda rev: rev.update(plano=plano))
    return conv, {"pt": pt["id"], "rl": rl["id"]}, ds


async def _ate_parar(conv):
    while conv in PIL._VIVOS:
        await asyncio.sleep(0.01)


def test_percorre_o_cronograma_ate_a_data_com_resumo_e_prova_e_busca_por_materia(monkeypatch):
    _falsos(monkeypatch)
    conv, ms, ds = _objetivo(4)

    async def main():
        PIL.start(conv, ds[1], questoes=8)
        await _ate_parar(conv)
    asyncio.run(main())
    # uma busca por matéria, na matéria dela; depois resumo → prova de cada tópico até o 2º dia (o 3º e o simulado ficam)
    assert FEITO[:2] == [("busca", ms["pt"], "Prefeitura de Contagem Português prova anterior com gabarito"),
                         ("busca", ms["rl"], "Prefeitura de Contagem Raciocínio prova anterior com gabarito")]
    assert FEITO[2:] == [("resumo", ms["pt"], "Crase"), ("prova", ms["pt"], "Prova só sobre o tópico: Crase."),
                         ("resumo", ms["rl"], "Porcentagem"), ("prova", ms["rl"], "Prova só sobre o tópico: Porcentagem.")]
    r = PIL.resumo(conv)
    assert not r["ativo"] and r["prontos"] == 2 and r["total"] == 2
    assert set(r["feitos"]) == {f"{ds[0]}-0", f"{ds[1]}-0"} and all(f["resumo"] and f["prova"] for f in r["feitos"].values())
    # continuar até o fim só faz o que falta: o 3º tópico e o simulado geral (no Tudo); sem buscar de novo
    async def mais():
        PIL.start(conv, ds[3])
        await _ate_parar(conv)
    asyncio.run(mais())
    assert FEITO[6:] == [("resumo", ms["pt"], "Regência"), ("prova", ms["pt"], "Prova só sobre o tópico: Regência."),
                         ("prova", None, "geral")]
    assert E.projeto(conv)["piloto"]["prontos"] == 4


def test_pausar_cancela_o_da_vez_e_continuar_refaz_so_ele(monkeypatch):
    _falsos(monkeypatch, pausa=0.2)
    conv, ms, ds = _objetivo(3)
    PIL._mudar(conv, buscas={ms["pt"]: 1, ms["rl"]: 2})   # buscas já feitas

    async def main():
        PIL.start(conv, ds[1])
        while not any(t == "prova" for t, *_ in FEITO):
            await asyncio.sleep(0.01)
        PIL.pausar(conv)   # no meio da prova de Crase
        await _ate_parar(conv)
    asyncio.run(main())
    p = PIL.ler(conv)
    assert not p["ativo"] and p["fase"] == "pausado" and p["feitos"] == {}
    async def continua():
        PIL.start(conv, ds[1])
        await _ate_parar(conv)
    asyncio.run(continua())
    assert [x[0] for x in FEITO] == ["resumo", "prova", "resumo", "prova", "resumo", "prova"]   # Crase refeito do zero
    assert len(PIL.ler(conv)["feitos"]) == 2


def test_retomar_depois_da_queda_refaz_o_interrompido(monkeypatch):
    _falsos(monkeypatch)
    conv, ms, ds = _objetivo(2)
    # o app caiu com o piloto ativo e o 1º tópico já feito
    PIL._mudar(conv, ativo=True, ate=ds[0], buscas={ms["pt"]: 1, ms["rl"]: 2}, motor={})

    async def main():
        assert PIL.retomar() == 1
        await _ate_parar(conv)
    asyncio.run(main())
    assert [x[0] for x in FEITO] == ["resumo", "prova"] and list(PIL.ler(conv)["feitos"]) == [f"{ds[0]}-0"]


def test_item_com_erro_nao_para_a_noite_mas_tres_seguidos_pausam(monkeypatch):
    _falsos(monkeypatch, status=lambda tipo, arg: "erro" if tipo == "resumo" and arg == "Crase" else "pronto")
    conv, ms, ds = _objetivo(3)
    PIL._mudar(conv, buscas={ms["pt"]: 1, ms["rl"]: 2})

    async def main():
        PIL.start(conv, ds[1])
        await _ate_parar(conv)
    asyncio.run(main())
    f = PIL.ler(conv)["feitos"]
    assert "erro" in f[f"{ds[0]}-0"] and "prova" not in f[f"{ds[0]}-0"] and f[f"{ds[1]}-0"]["prova"]
    _falsos(monkeypatch, status=lambda tipo, arg: "erro")
    conv2, ms2, ds2 = _objetivo(5)
    PIL._mudar(conv2, buscas={ms2["pt"]: 1, ms2["rl"]: 2})

    async def tudo_falha():
        PIL.start(conv2, ds2[4])
        await _ate_parar(conv2)
    asyncio.run(tudo_falha())
    p = PIL.ler(conv2)
    assert not p["ativo"] and p["fase"] == "pausado" and "3 itens seguidos" in p["aviso"] and len(p["feitos"]) == 2


def test_sem_cronograma_ou_data_ruim_recusa():
    with db.session() as s:
        c = db.Conversation(kind="estudos", title="X")
        s.add(c)
        s.commit()
    with pytest.raises(E.ToolError, match="cronograma"):
        PIL.start(c.id, date.today().isoformat())
    with pytest.raises(E.ToolError, match="AAAA"):
        PIL.start(c.id, "amanhã")


def test_resumos_trazem_o_tema():
    conv, ms, ds = _objetivo(2)
    tok = E.MATERIA.set(ms["pt"])
    try:
        E._save(conv, role="assistant", content="# Crase", status="pronto", meta={"estudos": {"tipo": "resumo", "tema": "Crase"}})
        assert [r["tema"] for r in E.projeto(conv)["resumos"]] == ["Crase"]
    finally:
        E.MATERIA.reset(tok)

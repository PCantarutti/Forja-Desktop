"""Pautas sem repetição: o que já virou vídeo vai no prompt, roteiro repetido sai descartado, e a reserva de pautas."""
from app import conteudo, conteudo_roteiros as R, db
from app.agent import _save


def _spec(tmp_path) -> int:
    estilos = tmp_path / "estilos"
    estilos.mkdir(exist_ok=True)
    (estilos / "alerta-tech.md").write_text("# Estilo\n", encoding="utf-8")
    conteudo.salvar_pastas({"pasta_estilos": str(estilos)})
    return conteudo.salvar_especificacao({"nome": "IA", "tema": "x", "estilo": "alerta-tech", "dias": 3})["id"]


def _rodada(cid, roteiros, status="ok") -> int:
    lista = R.normalizar(roteiros, [{"titulo": "f1", "url": "https://a.com/1"}, {"titulo": "f2", "url": "https://b.com/2"},
                                    {"titulo": "f3", "url": "https://c.com/3"}])
    return _save(cid, role="assistant", name=R.NOME, status=status, meta={R.CHAVE: {"roteiros": lista}}).id


def test_repetido_e_bloco_e_reserva(tmp_path):
    cid = _spec(tmp_path)
    mid = _rodada(cid, [{"titulo": "OpenAI lança o GPT-6", "noticia": {"fontes": [1, 2]}, "cenas": [{"id": "a", "texto": "b"}]},
                        {"titulo": "Meta mostra óculos novos", "noticia": {"fontes": [3]}, "cenas": [{"id": "a", "texto": "b"}]}])
    feito = R.estado(mid)["roteiros"][0]
    R.marcar_produzido(mid, feito["id"], "C:/v.mp4")
    assert [x["titulo"] for x in R.feitos(cid)] == ["OpenAI lança o GPT-6"]
    spec = conteudo.especificacao(cid)
    assert "JÁ VIRARAM VÍDEO" in R.bloco_feitos(spec) and "OpenAI lança o GPT-6" in R.bloco_feitos(spec)
    assert R.bloco_feitos({**spec, "tipo": "unico"}) == ""

    novos = R.normalizar([
        {"titulo": "OpenAI lança GPT-6!", "cenas": [{"id": "a", "texto": "b"}]},                         # título quase igual
        {"titulo": "Outra coisa", "noticia": {"fontes": ["https://a.com/1/"]}, "cenas": [{"id": "a", "texto": "b"}]},  # mesma fonte
        {"titulo": "Google anuncia chip", "noticia": {"fontes": ["https://g.com/x", "https://a.com/1"]},
         "cenas": [{"id": "a", "texto": "b"}]},                                                          # 1 de 2 fontes: metade
        {"titulo": "Apple e um assunto novo", "noticia": {"fontes": ["https://n.com/1", "https://n.com/2", "https://a.com/1"]},
         "cenas": [{"id": "a", "texto": "b"}]},                                                          # 1 de 3: novo
    ], [])
    R.tirar_repetidos(spec, novos)
    assert [x["status"] for x in novos] == ["descartado", "descartado", "descartado", "novo"]
    assert novos[0]["repetido"] == "OpenAI lança o GPT-6"

    # reserva: o roteiro novo que sobrou (Meta) serve; o produzido e as rodadas com erro não
    _rodada(cid, [{"titulo": "Rodada que falhou", "cenas": [{"id": "a", "texto": "b"}]}], status="erro")
    assert [x["titulo"] for _, x in R.reserva(cid)] == ["Meta mostra óculos novos"]
    antiga = _rodada(cid, [{"titulo": "Notícia velha", "noticia": {"data": "2020-01-01"}, "cenas": [{"id": "a", "texto": "b"}]}])
    assert "Notícia velha" not in [x["titulo"] for _, x in R.reserva(cid)] and antiga

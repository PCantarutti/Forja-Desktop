from app import assistente
from app.assistente_ouvido import acorda


def test_acorda_so_com_forja_no_comeco():
    assert acorda("Forja, quais são as notícias de hoje?") == "quais são as notícias de hoje?"
    assert acorda("Ei Forja crie uma imagem") == "crie uma imagem"
    assert acorda("forjá") == ""
    assert acorda("Forja.") == ""
    assert acorda("eu fui na forja ontem") is None
    assert acorda("Forjado em aço") is None


def test_rota():
    assert assistente.rota("crie uma imagem de um gato esquiando") == ("imagem", "um gato esquiando")
    assert assistente.rota("gere um vídeo de um cachorro surfando") == ("video", "um cachorro surfando")
    assert assistente.rota("no modo agente crie um site de ecommerce") == ("agente", "crie um site de ecommerce")
    assert assistente.rota("modo agente, faça uma calculadora") == ("agente", "faça uma calculadora")
    assert assistente.rota("quais são as notícias de hoje?") == ("chat", "quais são as notícias de hoje?")
    assert assistente.rota("o que é uma imagem de disco?") == ("chat", "o que é uma imagem de disco?")


def test_rota_chat_no_forja_so_quando_pedido():
    assert assistente.rota("abra um chat no Forja sobre receitas de bolo") == ("chat_forja", "receitas de bolo")
    assert assistente.rota("inicia uma conversa nova no forja desktop") == ("chat_forja", "")
    assert assistente.rota("o que é um chat?")[0] == "chat"


MODELOS = [{"provider": "local", "model": m, "nome": m} for m in
           ["Qwen3.6-35B-A3B-Q4_K_M", "Qwen3.8-27B-UD-IQ3_XXS", "gemma-4-12b-it-Q4_K_M", "gemma-4-26B-A4B-it-UD-Q4_K_M"]]
FAVS = [{"nome": "código", "provider": "local", "model": "Qwen3.8-27B-UD-IQ3_XXS"},
        {"nome": "pesquisa", "provider": "ollama-cloud", "model": "gpt-oss:120b"}]
PADRAO = {"provider": "ollama-cloud", "model": "gpt-oss:120b"}


def test_escolher_modelo():
    e = lambda r: (assistente.escolher_modelo(r, FAVS, MODELOS, PADRAO) or {}).get("model")
    assert e("o de sempre") == "gpt-oss:120b"
    assert e("meu favorito de código") == "Qwen3.8-27B-UD-IQ3_XXS"
    assert e("Pesquisa.") == "gpt-oss:120b"
    assert e("Qwen 3.6") == "Qwen3.6-35B-A3B-Q4_K_M"
    assert e("qwen três ponto oito") == "Qwen3.8-27B-UD-IQ3_XXS"
    assert e("gemma 12b") == "gemma-4-12b-it-Q4_K_M"
    assert e("gemma 26") == "gemma-4-26B-A4B-it-UD-Q4_K_M"
    assert e("llama 3") is None


def test_falavel():
    assert assistente.falavel("Qwen3.6-35B-A3B-Q4_K_M") == "Qwen3.6 35B A3B"
    assert assistente.falavel("gemma-4-26B-A4B-it-UD-Q4_K_M") == "gemma 4 26B A4B it"
    assert assistente.falavel("gpt-oss:120b") == "gpt oss:120b"


def test_nome_projeto():
    assert assistente.nome_projeto("crie um site de ecommerce") == "site-ecommerce"
    assert assistente.nome_projeto("faça uma calculadora simples") == "calculadora"
    assert assistente.nome_projeto("crie") == "projeto"


def test_nota_falada_com_humor_so_quando_escolhido():
    assert "Personalidade" not in assistente.nota_falada("nenhum")
    sarc = assistente.nota_falada("sarcastico")
    assert "J.A.R.V.I.S." in sarc and "[piada]" in sarc
    assert "GLaDOS" in assistente.nota_falada("acido")
    assert assistente.nota_falada("inexistente") == assistente.nota_falada("nenhum")
    pausa = assistente.nota_falada("sarcastico", pode_brincar=False)
    assert "J.A.R.V.I.S." not in pausa and assistente.SEM_PIADA in pausa


def test_marca_de_piada_nao_e_falada():
    assert assistente.sem_marca("Quatro. [piada] Quer a papinha?") == "Quatro. Quer a papinha?"
    assert assistente.sem_marca("[piada]") == ""


def test_pasta_parecida():
    pastas = [r"C:\Projetos\loja-ecommerce", r"C:\Projetos\blog", r"C:\Projetos\calc"]
    assert assistente.pasta_parecida("site-ecommerce", pastas) == r"C:\Projetos\loja-ecommerce"
    assert assistente.pasta_parecida("site-e-commerce-react", [r"C:\x\site-ecommerce"]) == r"C:\x\site-ecommerce"
    assert assistente.pasta_parecida("jogo-cobrinha", pastas) is None
    assert assistente.pasta_parecida("calculadora", pastas) == r"C:\Projetos\calc"  # na dúvida pergunta: é barato


def test_parar_e_cancela():
    for frase in ["para", "Para.", "cancela", "silêncio!", "chega", "deixa pra lá"]:
        assert assistente.rota(frase)[0] == "parar", frase
    assert assistente.rota("para que serve um capacitor?")[0] == "chat"


def test_alucinacao_do_whisper():
    from app.assistente_ouvido import ALUCINACAO, normaliza
    for t in ["Legendas pela comunidade Amara.org", "Obrigado por assistir!", "Obrigado."]:
        assert ALUCINACAO.search(normaliza(t)), t
    assert not ALUCINACAO.search(normaliza("Forja, obrigado pela ajuda com o código"))


def test_janela_de_escuta_conta_do_fim_da_fala():
    import threading, time as t
    from app import assistente_ouvido as o

    class Boca:
        falando = threading.Event()
        fim_fala = 0.0
    b = Boca()
    e = o.Escuta(b)
    assert not e.ativa()
    b.falando.set()                       # perguntou "Com qual modelo?" e ainda está falando
    e.abre()
    e.desde -= o.ESCUTA_S + 5             # o pedido de escuta chegou há muito tempo...
    b.fim_fala = t.time()                 # ...mas a fala da pergunta acabou agora
    b.falando.clear()
    assert e.ativa()
    b.fim_fala -= o.ESCUTA_S + 1          # ninguém respondeu dentro da janela
    assert not e.ativa()


def _ocioso(monkeypatch, alias="gemma", em_uso=False, outro_usou=False, minutos=5):
    import asyncio
    from app import llm, modelctl
    descarregou = []

    async def unload(motivo=""):
        descarregou.append(motivo)
        yield {}
    monkeypatch.setattr(assistente, "ler", lambda: {**assistente.PADRAO, "ocioso_min": minutos})
    monkeypatch.setattr(modelctl, "gerenciavel", lambda spec: True)
    monkeypatch.setattr(modelctl, "em_uso", lambda: em_uso)
    monkeypatch.setattr(modelctl, "unload", unload)
    monkeypatch.setattr(assistente.localai, "status", lambda: {"running": True, "alias": alias})
    monkeypatch.setitem(llm.ULTIMO_USO, "t", 1000.0 + (50 if outro_usou else 0))
    assistente._uso.update(spec={"provider": "local", "model": "gemma"}, t=1000.0, llm_t=1000.0)
    feito = asyncio.run(assistente.descarrega_se_ocioso(agora=1000.0 + minutos * 60 + 1))
    return feito, descarregou


def test_ocioso_descarrega_so_o_que_e_do_assistente(monkeypatch):
    assert _ocioso(monkeypatch) == (True, ["assistente ocioso"])
    assert _ocioso(monkeypatch, em_uso=True) == (False, [])        # trabalho do Forja usando
    assert _ocioso(monkeypatch, outro_usou=True) == (False, [])    # alguém usou depois da última pergunta
    assert _ocioso(monkeypatch, alias="qwen") == (False, [])       # já trocaram o modelo
    assert _ocioso(monkeypatch, minutos=0) == (False, [])          # "nunca"


def test_espera_quando_outro_trabalho_usa_o_modelo(monkeypatch):
    from app import modelctl
    monkeypatch.setattr(modelctl, "gerenciavel", lambda spec: spec["provider"] == "local")
    monkeypatch.setattr(assistente.localai, "status", lambda: {"running": True, "alias": "gemma"})
    monkeypatch.setattr(assistente.localai, "image_busy", lambda: False)
    monkeypatch.setattr(assistente.localai, "_loading", False, raising=False)
    monkeypatch.setattr(modelctl, "em_uso", lambda: True)
    assert assistente._precisa_esperar({"provider": "local", "model": "qwen"}) == "gemma"
    assert assistente._precisa_esperar({"provider": "local", "model": "gemma"}) == ""   # o mesmo: não troca nada
    assert assistente._precisa_esperar({"provider": "ollama-cloud", "model": "x"}) == ""  # nuvem não precisa da placa
    monkeypatch.setattr(modelctl, "em_uso", lambda: False)
    assert assistente._precisa_esperar({"provider": "local", "model": "qwen"}) == ""    # parado: troca na hora


def test_busca_escrita_como_texto():
    calls, resto = assistente._chamadas_em_texto('{{"name": "web_search", "arguments": {"query": "7 vezes 8"}}}')
    assert [c["arguments"]["query"] for c in calls] == ["7 vezes 8"] and resto == ""
    calls, _ = assistente._chamadas_em_texto('<tool_call>{"name": "web_search", "arguments": {"query": "dólar"}}</tool_call>')
    assert calls and calls[0]["name"] == "web_search"
    assert assistente._chamadas_em_texto("São cinquenta e seis.") == ([], "São cinquenta e seis.")


def test_salvar_conversa_por_voz():
    for frase in ["salva nossa conversa num chat", "transforma essa conversa em um chat do Forja",
                  "guarda a conversa no Forja", "vira essa conversa um chat"]:
        assert assistente.rota(frase)[0] == "salvar_conversa", frase
    assert assistente.rota("o que é uma conversa?")[0] == "chat"
    assert assistente.rota("abra um chat no forja sobre conversa fiada")[0] == "chat_forja"


def test_transcricao_so_em_memoria_e_agrupa_a_resposta(monkeypatch):
    monkeypatch.setattr(assistente, "_manda", lambda *a, **k: None)
    assistente.esquecer()
    assistente._anota("voce", "quanto é 2 mais 2?")
    assistente._atendendo[0] += 1  # durante o pedido: as frases da resposta viram um balão só
    assistente.fala("Quatro.")
    assistente.fala("Quer a papinha também?")
    assistente._atendendo[0] -= 1
    assistente.fala("A pesquisa terminou.")  # aviso que chega sozinho depois: balão próprio
    t = assistente.conversa()["transcricao"]
    assert [(x["quem"], x["texto"]) for x in t] == [("voce", "quanto é 2 mais 2?"), ("forja", "Quatro. Quer a papinha também?"),
                                                    ("forja", "A pesquisa terminou.")]
    assistente.esquecer()
    assert assistente.conversa()["transcricao"] == []


def test_salvar_sem_conversa_avisa():
    import pytest
    assistente.esquecer()
    with pytest.raises(assistente.ToolError):
        assistente.salvar_conversa()


def test_ociosidade_esquece_a_conversa_falada(monkeypatch):
    monkeypatch.setattr(assistente, "_manda", lambda *a, **k: None)
    assistente._anota("voce", "oi")
    assistente._conversa_chat["msgs"] = [{"role": "user", "content": "oi"}]
    _ocioso(monkeypatch, em_uso=True)  # mesmo sem poder descarregar (Forja usando), a conversa expira
    assert assistente.conversa()["transcricao"] == [] and not assistente._conversa_chat


def _preaquece(monkeypatch, no_ar="", em_uso=False, imagem=False):
    import asyncio
    from app import modelctl
    carregou = []

    async def ensure(spec, out=None, cancel=None):
        carregou.append(spec["model"])
        yield {}
    monkeypatch.setattr(assistente, "modelo_assistente", lambda: {"provider": "local", "model": "gemma"})
    monkeypatch.setattr(modelctl, "gerenciavel", lambda spec: True)
    monkeypatch.setattr(modelctl, "em_uso", lambda: em_uso)
    monkeypatch.setattr(modelctl, "ensure", ensure)
    monkeypatch.setattr(assistente.localai, "status", lambda: {"running": bool(no_ar), "alias": no_ar})
    monkeypatch.setattr(assistente.localai, "image_busy", lambda: imagem)
    monkeypatch.setattr(assistente.localai, "_loading", False, raising=False)
    return asyncio.run(assistente.preaquece()), carregou


def test_atalho_preaquece_o_modelo_sem_derrubar_trabalho(monkeypatch):
    assert _preaquece(monkeypatch) == ("carregado", ["gemma"])                         # placa vazia: carrega
    assert _preaquece(monkeypatch, no_ar="qwen") == ("carregado", ["gemma"])           # outro parado: troca
    assert _preaquece(monkeypatch, no_ar="qwen", em_uso=True) == ("placa ocupada", [])  # outro em uso: não mexe
    assert _preaquece(monkeypatch, no_ar="gemma") == ("já no ar", [])
    assert _preaquece(monkeypatch, imagem=True) == ("placa ocupada", [])


def test_atalho_nao_carrega_em_dobro(monkeypatch):
    import asyncio
    from app import modelctl
    monkeypatch.setattr(assistente, "modelo_assistente", lambda: {"provider": "local", "model": "gemma"})
    monkeypatch.setattr(modelctl, "gerenciavel", lambda spec: True)

    async def com_carga_em_andamento():
        async with assistente._trava_carga():  # uma pergunta falada já está carregando o modelo
            return await assistente.preaquece()
    assistente._carga.clear()
    assert asyncio.run(com_carga_em_andamento()) == "já carregando"
    assistente._carga.clear()


ROTAS = [
    ("no modo agente crie um site de ecommerce", "agente", "crie um site de ecommerce"),
    ("crie um jogo para mim no modo agente", "agente", "crie um jogo"),
    ("no modo agente, crie um jogo de nave", "agente", "crie um jogo de nave"),
    ("cria um jogo da cobrinha no modo a gente", "agente", "cria um jogo da cobrinha"),  # como o Whisper escreve
    ("pede pro agente fazer um site de receitas", "agente", "fazer um site de receitas"),
    ("usando o agente, monta uma calculadora em python", "agente", "monta uma calculadora em python"),
    ("agente, cria um script que renomeia fotos", "agente", "cria um script que renomeia fotos"),
    ("faz um app de tarefas pelo agente", "agente", "faz um app de tarefas"),
    ("com o agente do Forja crie um bot de discord", "agente", "crie um bot de discord"),
    ("modo de agente faça um jogo da velha", "agente", "faça um jogo da velha"),
    ("no maestro crie um sistema de estoque", "maestro", "crie um sistema de estoque"),
    ("usando a maestro desenvolve um site completo de loja", "maestro", "desenvolve um site completo de loja"),
    ("modo maestro, faça um jogo de plataforma", "maestro", "faça um jogo de plataforma"),
    ("faça uma pesquisa profunda pra mim sobre alíquotas de ICMS", "pesquisa", "alíquotas de ICMS"),
    ("faça uma pesquisa profunda no aplicativo Forja pra mim sobre isso", "pesquisa", "isso"),
    ("pesquise a fundo sobre carros elétricos no Brasil", "pesquisa", "carros elétricos no Brasil"),
    ("quero uma pesquisa completa sobre energia solar", "pesquisa", "energia solar"),
    ("faz um relatório completo sobre o mercado de GPUs", "pesquisa", "o mercado de GPUs"),
    ("abre um chat no Forja sobre receitas", "chat_forja", "receitas"),
    ("abre o chat no Forja sobre uma pesquisa de preços", "chat_forja", "uma pesquisa de preços"),
    ("inicia uma conversa nova no forja", "chat_forja", ""),
    ("cria um chat novo sobre viagens", "chat_forja", "viagens"),
    ("abre uma conversa no aplicativo sobre investimentos", "chat_forja", "investimentos"),
    ("crie uma imagem de um gato esquiando", "imagem", "um gato esquiando"),
    ("quero uma foto de uma praia ao pôr do sol", "imagem", "uma praia ao pôr do sol"),
    ("gera um vídeo de um cachorro surfando", "video", "um cachorro surfando"),
    ("salva nossa conversa num chat", "salvar_conversa", ""),
    ("cancela", "parar", ""),
    ("o que é um agente de IA?", "chat", None),
    ("pesquise o valor do ICMS em Minas Gerais", "chat", None),  # pesquisa simples: ele mesmo responde
    ("me explica como funciona uma pesquisa de mercado", "chat", None),
    ("a gente pode conversar depois?", "chat", None),
    ("o que é uma imagem de disco?", "chat", None),
]


def test_rota_entende_varios_jeitos_de_pedir():
    errados = [(f, assistente.rota(f)) for f, tipo, pedido in ROTAS
               if assistente.rota(f)[0] != tipo or (pedido is not None and assistente.rota(f)[1].lower() != pedido.lower())]
    assert not errados


def test_modelo_dito_no_pedido():
    assert assistente.modelo_dito("abre o chat no Forja sobre ICMS usando o modelo KEWEN3.6") == \
        ("abre o chat no Forja sobre ICMS", "KEWEN3.6")
    assert assistente.modelo_dito("no modo agente crie um jogo, com o modelo gemma 12b")[1] == "gemma 12b"
    assert assistente.modelo_dito("crie um jogo") == ("crie um jogo", "")


def test_pasta_nova_por_tipo(tmp_path, monkeypatch):
    monkeypatch.setattr(assistente, "ler", lambda: {**assistente.PADRAO, "pasta_projetos": str(tmp_path)})
    assert assistente._pasta_nova("jogo-nave") == tmp_path / "chat-agente" / "jogo-nave"
    assert assistente._pasta_nova("jogo-nave") == tmp_path / "chat-agente" / "jogo-nave-2"
    assert assistente._pasta_nova("estoque", "maestro") == tmp_path / "chat-maestro" / "estoque"
    assert assistente.nome_projeto("abra um chat sobre um jogo flip bird") == "jogo-flip-bird"


def test_nome_do_modelo_como_o_whisper_ouve():
    e = lambda r: (assistente.escolher_modelo(r, [], MODELOS, PADRAO) or {}).get("model")
    for dito in ["Comem 3.6.", "Que é WEN 3.6.", "KEWEN3.6", "usando o modelo kewen 3.6", "três ponto seis"]:
        assert e(dito) == "Qwen3.6-35B-A3B-Q4_K_M", dito
    assert e("gema.") in ("gemma-4-12b-it-Q4_K_M", "gemma-4-26B-A4B-it-UD-Q4_K_M")
    assert e("gema 26") == "gemma-4-26B-A4B-it-UD-Q4_K_M"
    assert e("o que é isso?") is None
    assert assistente._nomes_para_dizer(MODELOS) == "Qwen3.6 35B A3B, Qwen3.8 27B, gemma 4 12b e gemma 4 26B A4B"


def test_pergunta_pendente_nao_engole_pedido_novo():
    assert assistente._parece_pedido("quanto é 3 vezes 4?")
    assert assistente._parece_pedido("no modo agente crie um jogo")
    assert assistente._parece_pedido("me fala a previsão do tempo de amanhã em São Paulo")
    assert not assistente._parece_pedido("qwen 3.6")
    assert not assistente._parece_pedido("o de sempre")
    assert not assistente._parece_pedido("meu favorito de código")



def test_pra_quando_larga_a_pergunta_se_vier_pedido_novo(monkeypatch):
    import asyncio, time
    # "me lembra de X" sem horário -> "Pra quando?"; aí "anota aí: levar o carro no lava-jato" era lido como horário
    # ("Não entendi o horário", depois "Deixei pra lá") e a nota se perdia
    falas = []
    monkeypatch.setattr(assistente, "fala", falas.append)
    monkeypatch.setattr(assistente, "_manda", lambda *a, **k: None)
    vistos = []

    async def chat(texto):
        vistos.append(texto)
    monkeypatch.setattr(assistente, "_chat", chat)
    assistente._pendente.clear()
    assistente._pendente.update(tipo="lembrete", pedido="tirar o bolo", t=time.time())
    asyncio.run(assistente._atende({"comando": "anota aí: levar o carro no lava-jato", "resposta": True}))
    assert vistos == ["anota aí: levar o carro no lava-jato"] and not assistente._pendente
    criados = []

    async def lembrete(acao, arg):
        criados.append((acao, arg[1]))
    monkeypatch.setattr(assistente, "_lembrete", lembrete)
    assistente._pendente.update(tipo="lembrete", pedido="tirar o bolo", t=time.time())
    asyncio.run(assistente._atende({"comando": "amanhã às 8 da manhã, por favor", "resposta": True}))
    assert criados == [("criar", "tirar o bolo")]   # resposta com horário continua valendo, mesmo longa


def test_trocar_o_modelo_do_assistente_falando():
    casos = {
        "troca seu modelo para o qwen 3.6": "qwen 3.6",
        "muda o modelo do assistente pra gemma 12b": "gemma 12b",
        "use o modelo ornith para responder": "ornith",
        "passa a usar o modelo gemma": "gemma",
        "quero que você use o modelo qwen": "qwen",
        "troca de modelo": "",
        "volta o seu modelo para o de sempre": "de sempre",
        "volta pro modelo de sempre": "de sempre",
    }
    for frase, dito in casos.items():
        assert assistente.rota(frase) == ("trocar_modelo", dito), frase
    assert assistente.rota("qual modelo você está usando?")[0] == "qual_modelo"
    assert assistente.rota("qual é o seu modelo?")[0] == "qual_modelo"
    assert assistente.rota("qual modelo cê tá usando?")[0] == "qual_modelo"
    assert assistente.rota("com qual modelo vc tá falando comigo?")[0] == "qual_modelo"
    # "usando o modelo X" num pedido de agente/chat é o modelo DAQUELE trabalho, não troca o do assistente
    assert assistente.rota("no modo agente crie um jogo usando o modelo gemma")[0] == "agente"
    assert assistente.rota("abre um chat no forja usando o modelo qwen")[0] == "chat_forja"
    assert assistente.rota("o que é um modelo de linguagem?")[0] == "chat"


def _emprestavel(monkeypatch, alias="gemma-4-12b-it-Q4_K_M", permitidos=None, em_uso=False):
    from app import llm, modelctl
    perm = [{"provider": "local", "model": "gemma-4-12b-it-Q4_K_M"}] if permitidos is None else permitidos
    monkeypatch.setattr(assistente, "ler", lambda: {**assistente.PADRAO, "permitidos": perm})
    monkeypatch.setattr(modelctl, "em_uso", lambda: em_uso)
    monkeypatch.setattr(assistente.localai, "status", lambda: {"running": bool(alias), "alias": alias})
    monkeypatch.setattr(assistente.localai, "image_busy", lambda: False)
    monkeypatch.setattr(assistente.localai, "_loading", False, raising=False)
    monkeypatch.setattr(llm, "servidor_local_externo", lambda p: p == "lmstudio")
    monkeypatch.setattr(llm, "carregado_externo", lambda p: "qwen-lm" if p == "lmstudio" else None)
    return assistente.emprestavel()


def test_modelo_carregado_e_parado_so_se_permitido(monkeypatch):
    assert _emprestavel(monkeypatch) == {"provider": "local", "model": "gemma-4-12b-it-Q4_K_M"}
    assert _emprestavel(monkeypatch, alias="Qwen3.6-35B-A3B-Q4_K_M") is None            # carregado, mas não permitido
    assert _emprestavel(monkeypatch, em_uso=True) is None                               # permitido, mas trabalhando
    assert _emprestavel(monkeypatch, alias="", permitidos=[]) is None
    assert _emprestavel(monkeypatch, alias="", permitidos=[{"provider": "lmstudio", "model": "qwen-lm"}]) == \
        {"provider": "lmstudio", "model": "qwen-lm"}                                    # LM Studio desta máquina


def test_mesmo_modelo_no_lmstudio_e_local_prefere_o_local():
    modelos = [{"provider": "lmstudio", "model": "gemma-4-12b"}, {"provider": "local", "model": "gemma-4-12b-it-Q4_K_M"}]
    assert assistente.escolher_modelo("gemma 12b", [], modelos, {}) == {"provider": "local", "model": "gemma-4-12b-it-Q4_K_M"}


def test_nome_ouvido_mas_fora_da_transcricao():
    from app.assistente_ouvido import sem_quase_nome
    assert sem_quase_nome("Força, que horas são?") == "que horas são?"
    assert sem_quase_nome("Porja abre o chat") == "abre o chat"
    assert sem_quase_nome("que horas são?") == "que horas são?"
    assert sem_quase_nome("Forja.") == ""


def test_boca_cala_sem_mexer_no_audio_de_outra_thread(monkeypatch):
    # sd.stop() da thread dos pedidos com o _toca no meio do sd.play derrubava o ouvido (access violation no PortAudio):
    # o "cala" só marca, e o _toca para sozinho no próximo bloco de 0,1 s
    import sys, threading, queue, time, types
    from app import assistente_ouvido as o

    class Amostras(list):  # o backend não tem numpy (ele vive no venv do assistente): o bastante para o _toca
        ndim = 1
    escritos, tocando = [], threading.Event()

    class Saida:
        def __init__(self, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def write(self, x):
            escritos.append(len(x))
            tocando.set()
            time.sleep(0.01)
    falso = types.SimpleNamespace(OutputStream=Saida, stop=lambda: (_ for _ in ()).throw(AssertionError("sd.stop")))
    monkeypatch.setitem(sys.modules, "sounddevice", falso)
    monkeypatch.setitem(sys.modules, "numpy", types.SimpleNamespace(float32=None, ascontiguousarray=lambda x, dtype=None: x))
    monkeypatch.setattr(o, "diz", lambda *a: None)
    b = o.Boca.__new__(o.Boca)
    b.textos, b.audios, b.falando, b.fim_fala, b.geracao, b.atual = queue.Queue(), queue.Queue(), threading.Event(), 0.0, 0, ""
    threading.Thread(target=b._toca, daemon=True).start()
    b.falando.set()
    b.audios.put((0, Amostras([0.0] * 24000 * 10), 24000, False, "uma resposta longa"))  # 10 s = 100 blocos
    assert tocando.wait(2)
    b.cala()
    prazo = time.time() + 5
    while b.audios.unfinished_tasks and time.time() < prazo:  # sem join(): thread morta não trava a bateria
        time.sleep(0.02)
    assert 0 < len(escritos) < 100 and not b.falando.is_set()


def test_voz_treinada_no_piper_aparece_e_vai_para_o_ouvido(tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(assistente, "VOZES_PIPER", tmp_path)
    (tmp_path / "mae.onnx").write_bytes(b"x")
    (tmp_path / "mae.onnx.json").write_text("{}", "utf-8")
    (tmp_path / "sem-config.onnx").write_bytes(b"x")  # sem o .json o Piper não carrega: fica de fora
    assert assistente.vozes_kokoro()[:2] == [{"id": "piper:mae", "nome": "Mae (treinada)"},
                                             {"id": "piper:mae:glados", "nome": "GLaDOS (Mae, com filtro)"}]
    assert [v["id"] for v in assistente.vozes_kokoro()].count("piper:sem-config") == 0
    assert assistente._voz_arg("piper:mae") == str(tmp_path / "mae.onnx")
    assert assistente._voz_arg("piper:mae:glados") == str(tmp_path / "mae.onnx")  # o filtro vai no --efeito
    assert assistente._voz_arg("pf_dora") == "pf_dora"
    assert assistente._voz_arg("piper:apagada") == assistente.PADRAO["voz"]
    assert assistente._voz_arg("piper:../../x") == assistente.PADRAO["voz"]
    # escolher a voz treinada numa instalação antiga garante o piper-tts no venv (em background)
    chamadas = []
    monkeypatch.setattr(assistente, "CONFIG", tmp_path / "assistente.json")
    monkeypatch.setattr(assistente, "instalado", lambda: True)
    monkeypatch.setattr(assistente, "_vivo", lambda: False)
    monkeypatch.setattr(assistente, "_garante_piper", lambda glados=False: chamadas.append(glados))
    monkeypatch.setattr(threading, "Thread", lambda target, args=(), daemon=None: type("T", (), {"start": lambda s: target(*args)})())
    assistente.gravar({"voz": "piper:mae"})
    assistente.gravar({"voz": "piper:mae:glados"})  # com o filtro: instala também pyworld e pedalboard
    assert chamadas == [False, True] and assistente.ler()["voz"] == "piper:mae:glados"


def test_voz_so_ingles_traduz_frases_prontas(tmp_path, monkeypatch):
    # a GLaDOS do jogo só fala inglês: sem filtro na lista, e as frases prontas (em português) saem traduzidas, com cache
    monkeypatch.setattr(assistente, "VOZES_PIPER", tmp_path)
    import queue
    monkeypatch.setattr(assistente, "_traducoes_en", {})
    (tmp_path / "glados.onnx").write_bytes(b"x")
    (tmp_path / "glados.onnx.json").write_text('{"espeak": {"voice": "en-us"}}', "utf-8")
    assert assistente.vozes_kokoro()[0] == {"id": "piper:glados", "nome": "Glados (treinada, só inglês)"}
    assert "piper:glados:glados" not in [v["id"] for v in assistente.vozes_kokoro()]
    assert assistente.so_ingles("piper:glados") and not assistente.so_ingles("pf_dora")
    assert not assistente._com_filtro("piper:glados") and assistente._com_filtro("piper:mae:glados")
    monkeypatch.setattr(assistente, "ler", lambda: {**assistente.PADRAO, "voz": "piper:glados"})
    monkeypatch.setattr(assistente, "modelo_assistente", lambda: {"provider": "ollama-cloud", "model": "x"})
    pedidos = []

    async def gera(spec, slot, sistema, usuario, n=400):
        pedidos.append(usuario)
        return "Opening YouTube."
    monkeypatch.setattr(assistente, "_gera", gera)
    assert assistente._traduz_en("Abrindo o YouTube.") == "Opening YouTube."
    assert assistente._traduz_en("Abrindo o YouTube.") == "Opening YouTube." and len(pedidos) == 1  # do cache
    # tudo vai para a fila de tradução, menos a resposta do modelo (já em inglês)
    monkeypatch.setattr(assistente, "_em_ingles", fila := queue.Queue())
    assistente.fala("Pesquisando.")
    assistente._fala_resposta("Paris.")
    assert [fila.get_nowait(), fila.get_nowait()] == [("Pesquisando.", True), ("Paris.", False)]


def test_humor_glados_usa_falas_locais_como_referencia(tmp_path, monkeypatch):
    # o texto da Valve não vai para o repositório: só um arquivo local, sorteado uma vez (o prompt fica fixo, o cache vale)
    arq = tmp_path / "glados.txt"
    monkeypatch.setattr(assistente, "ESTILOS", {"acido": arq, "wheatley": tmp_path / "wheatley.txt"})
    monkeypatch.setattr(assistente, "_refs", {})
    assert "Falas reais" not in assistente.nota_falada("acido")          # sem o arquivo, só os exemplos próprios
    arq.write_text("\n".join(f"Linha de teste numero {i} com algumas palavras" for i in range(40)) + "\nxyzcurta\n", "utf-8")
    a, b = assistente.nota_falada("acido"), assistente.nota_falada("acido")
    assert "Falas reais" in a and a == b and a.count("\n- ") == 12 and "xyzcurta" not in a
    assert "Falas reais" not in assistente.nota_falada("sarcastico")
    assert "criatura" in assistente.HUMORES["acido"]["prompt"]  # proibida, e dita como proibida
    # o Wheatley tem o arquivo dele, e a marca de grito nunca é lida em voz alta
    (tmp_path / "wheatley.txt").write_text("Okay, do not panic, alright, stay calm, everything is fine\n", "utf-8")
    assert "do not panic" in assistente.nota_falada("wheatley") and "do not panic" not in a
    assert assistente.sem_marca("Não sei... [grito]Aaah! Aranha![/grito] É um fio. [piada] Sob controle.") == \
        "Não sei... Aaah! Aranha! É um fio. Sob controle."

"""Baterias de teste por especialidade de Worker, para o Comparar, e o juiz que analisa o resultado.

A pergunta que o usuário quer responder é "qual modelo local serve para frontend, qual para lógica,
qual para ler documento". Cada bateria é uma tarefa curta com resposta conferível (gabarito): é o
gabarito que deixa o juiz dizer quem acertou e quem alucinou, em vez de só gostar mais de um texto.
Na de documentos o Forja fornece o arquivo — e pergunta algo que NÃO está nele, que é onde modelo
fraco inventa.
"""
from __future__ import annotations

import re
import socket
import sys
import time

from typing import AsyncIterator

from . import config, db, llm, native, shell
from .tools import ToolError

SYSTEM = "Responda em português do Brasil. Seja direto e preciso; se algo não estiver no material, diga que não consta."

POLITICA = """# Política de Reembolso — Aurora Viagens (versão 3.2, vigente desde 01/02/2026)

## 1. Cancelamento pelo cliente
1.1. O cliente pode cancelar sem multa em até 7 (sete) dias corridos após a compra, desde que faltem
mais de 30 (trinta) dias para a data de início da viagem na data do cancelamento.
1.2. Fora da condição do item 1.1, o cancelamento tem multa de 20% sobre o valor total do pacote.
1.3. Cancelamentos a menos de 72 horas do embarque não têm reembolso; o valor pago vira crédito de 50%.

## 2. Forma e prazo do reembolso
2.1. O reembolso é feito no mesmo meio de pagamento da compra, em até 10 (dez) dias úteis.
2.2. Compras pagas por boleto são reembolsadas por PIX, em até 5 (cinco) dias úteis, para uma chave
em nome do titular da compra.
2.3. Compras no cartão de crédito parceladas são estornadas nas faturas seguintes, conforme a
operadora do cartão.

## 3. Pacotes promocionais
3.1. Pacotes marcados como "Tarifa Promocional" não são reembolsáveis.
3.2. Nesses pacotes, o cancelamento gera crédito de 70% do valor pago, válido por 12 meses, para
qualquer produto da Aurora Viagens.

## 4. Cancelamento pela Aurora Viagens
4.1. Se a Aurora cancelar a viagem, o cliente escolhe entre reembolso integral em até 10 dias úteis
ou remarcação sem custo em até 6 meses.

## 5. Contato
Pedidos de reembolso: reembolso@auroraviagens.com.br ou pelo aplicativo, em Minhas Viagens › Cancelar.
"""

CODIGO_TESTES = '''def desconto(preco: float, cupom: str | None) -> float:
    """Aplica um cupom ao preço.

    - None ou "FRETE": o preço não muda
    - "DEZ": tira 10% do preço
    - "VINTE": tira R$ 20,00, mas o preço nunca fica negativo
    - qualquer outro cupom: ValueError
    """
    if cupom is None or cupom == "FRETE":
        return preco
    if cupom == "DEZ":
        return preco - preco * 10 / 100
    if cupom == "VINTE":
        return preco - 20
    raise ValueError("cupom inválido")'''

CODIGO_GERAL = """function media(notas) {
  // média das notas, ignorando os valores null
  let soma = 0;
  for (const n of notas) if (n !== null) soma += n;
  return soma / notas.length;
}"""

BATERIAS: dict[str, dict] = {
    "logica": {
        "titulo": "Lógica e back-end",
        "mede": "raciocínio com regra de negócio, casos de borda e validação — e se o modelo calcula certo sem executar",
        "prompt": ("Escreva em Python a função `parcelas(total_centavos: int, n: int) -> list[int]` que divide um "
                   "valor em n parcelas inteiras (em centavos), com diferença de no máximo 1 centavo entre elas e as "
                   "maiores primeiro. Valide as entradas: n >= 1 e total_centavos >= 0; senão, ValueError.\n\n"
                   "Depois, SEM executar código, diga o retorno de parcelas(1000, 3), parcelas(1, 4) e parcelas(0, 2), "
                   "e a complexidade de tempo em uma frase."),
        "gabarito": ("parcelas(1000, 3) = [334, 333, 333]; parcelas(1, 4) = [1, 0, 0, 0]; parcelas(0, 2) = [0, 0]. "
                     "A soma das parcelas é sempre o total. ValueError para n < 1 ou total < 0. Complexidade O(n). "
                     "Uso de float/arredondamento que faça a soma não bater é erro."),
    },
    "frontend": {
        "titulo": "Frontend e aparência",
        "mede": "uma página de verdade: menu que vira hambúrguer, grade que muda de colunas, formulário que "
                "valida, sem rolagem horizontal no celular — e o capricho visual",
        "prompt": ("Crie UM arquivo HTML (CSS e JS embutidos, sem bibliotecas nem CDN) com a landing page da "
                   "academia 'Pulso Fit'. Seções, nesta ordem:\n"
                   "1. Cabeçalho fixo com logo e menu (Planos, Modalidades, Contato). Até 768px de largura o menu "
                   "vira um botão ☰ que abre e fecha a lista.\n"
                   "2. Destaque com título, subtítulo e botão 'Agende uma aula grátis'. No desktop, texto à esquerda "
                   "e um bloco ilustrativo à direita (um gradiente serve); no celular, um embaixo do outro.\n"
                   "3. Modalidades: 6 cards (nome e descrição curta) em 3 colunas no desktop, 2 até 1024px e 1 no "
                   "celular.\n"
                   "4. Planos: Básico R$ 89, Plus R$ 129 e Premium R$ 179, com o Plus em destaque.\n"
                   "5. Contato: formulário com nome, e-mail e mensagem que valida os campos (nenhum vazio, e-mail "
                   "válido) e mostra uma mensagem de sucesso sem recarregar a página.\n"
                   "6. Rodapé com endereço e horário de funcionamento.\n"
                   "Regras: nenhuma rolagem horizontal em 390px de largura, contraste AA, foco visível nos links e "
                   "botões, visual coerente (mesma paleta e espaçamentos em todas as seções). Imagens não são "
                   "necessárias. Responda só com o código."),
        "gabarito": ("Tem: meta viewport; menu que até 768px vira botão ☰ e abre/fecha por JS (idealmente com "
                     "aria-expanded); destaque em 2 colunas no desktop e empilhado no celular; grade de modalidades "
                     "3 → 2 → 1 colunas (media queries ou grid com auto-fit); os 3 planos com os preços exatos e o "
                     "Plus destacado; formulário com preventDefault, validação de vazio e de e-mail, mensagem de "
                     "sucesso sem recarregar; rodapé com endereço e horário; nada com largura fixa maior que a tela "
                     "(sem rolagem horizontal em 390px); foco visível; nenhum recurso externo. Erros comuns: menu que "
                     "não abre no celular, grade que continua com 3 colunas no celular, elemento de largura fixa "
                     "causando rolagem horizontal, formulário que recarrega a página, seção faltando."),
    },
    "testes": {
        "titulo": "Testes",
        "mede": "escrever testes a partir do comportamento documentado e achar o bug que eles expõem",
        "prompt": ("Escreva testes pytest para a função abaixo, cobrindo o comportamento DOCUMENTADO na docstring. "
                   "Depois diga: algum dos seus testes falha com esta implementação? Qual, e por quê? "
                   "Não invente regras que a docstring não diz.\n\n```python\n" + CODIGO_TESTES + "\n```"),
        "gabarito": ("O bug: 'VINTE' com preço menor que 20 devolve valor negativo (ex.: desconto(10, 'VINTE') "
                     "devolve -10, deveria ser 0). Testes esperados: None e 'FRETE' mantêm o preço; 'DEZ' em 100 dá "
                     "90; 'VINTE' em 50 dá 30; 'VINTE' em 10 dá 0 (este FALHA); cupom desconhecido levanta ValueError "
                     "(pytest.raises). Inventar regra (ex.: cupom em minúsculas, limite de preço) conta como erro."),
    },
    "docs": {
        "titulo": "Documentação e leitura de documentos",
        "mede": "ler um documento fornecido, resumir, aplicar a regra a um caso e NÃO inventar o que não está lá",
        "anexo": {"nome": "politica-de-reembolso.md", "texto": POLITICA},
        "prompt": ("Leia o arquivo anexo (politica-de-reembolso.md) e responda:\n"
                   "1. Um resumo em até 5 tópicos.\n"
                   "2. Comprei um pacote comum em 01/03, a viagem começa em 20/03 e cancelei em 04/03. Pago multa? "
                   "Quanto? Cite o item.\n"
                   "3. Paguei por boleto: como e em quanto tempo recebo o reembolso?\n"
                   "4. A política cobre seguro-viagem? Em que condições?"),
        "gabarito": ("2: SIM, multa de 20% (item 1.2): o cancelamento foi em até 7 dias, mas faltavam só 16 dias "
                     "para a viagem (menos de 30), então o item 1.1 não se aplica. 3: por PIX, em até 5 dias úteis, "
                     "para chave em nome do titular (item 2.2). 4: o documento NÃO fala de seguro-viagem — a resposta "
                     "certa é dizer que não consta; qualquer regra de seguro é alucinação. O resumo deve citar "
                     "cancelamento (7 dias/30 dias, multa 20%, 72h com crédito de 50%), reembolso (10 dias úteis, "
                     "boleto por PIX), promocional (crédito de 70% por 12 meses) e cancelamento pela Aurora."),
    },
    "geral": {
        "titulo": "Geral (achar e corrigir bug)",
        "mede": "ler código, achar o bug, corrigir e prever a saída — o básico de qualquer Worker",
        "prompt": ("Esta função JavaScript deveria devolver a média das notas ignorando os valores null, mas tem um "
                   "bug. Diga qual é em uma frase, mostre a versão corrigida e diga o que ela devolve para "
                   "[10, null, 7, 8] e para [].\n\n```js\n" + CODIGO_GERAL + "\n```"),
        "gabarito": ("Bug: divide pela quantidade TOTAL de itens (incluindo os null), não pela quantidade de notas "
                     "válidas. Correção: contar as não-nulas e dividir por essa contagem. [10, null, 7, 8] → 25/3 ≈ "
                     "8,33. [] (ou só nulls): a versão corrigida precisa tratar o caso (devolver 0, null ou lançar erro, "
                     "de forma explícita); dizer que dá 8,33 ou ignorar o caso vazio é erro."),
    },
}


def bateria_para(eid: str) -> str:
    """Especialidade → bateria. Níveis e especialidades próprias usam a geral."""
    return eid if eid in BATERIAS else "geral"


def publico() -> dict[str, dict]:
    """O que a interface mostra antes de rodar (o gabarito vai junto, recolhido: é do usuário)."""
    return {k: {"titulo": b["titulo"], "mede": b["mede"], "prompt": b["prompt"], "gabarito": b["gabarito"],
                "anexo": b.get("anexo")} for k, b in BATERIAS.items()}


def com_anexo(prompt: str, bateria: str) -> str:
    """O prompt que os modelos recebem: o texto visível mais o arquivo da bateria, se houver."""
    anexo = (BATERIAS.get(bateria) or {}).get("anexo")
    if not anexo:
        return prompt
    return f"{prompt}\n\n<arquivo nome=\"{anexo['nome']}\">\n{anexo['texto']}\n</arquivo>"


# ------------------------------------------------------------------ testar o código de uma resposta

TESTES_DIR = config.DATA_DIR / "testes-de-codigo"
ARQUIVO = {"html": "index.html", "python": "main.py", "javascript": "main.js"}
APELIDOS = {"html": "html", "htm": "html", "xhtml": "html", "py": "python", "python": "python",
            "python3": "python", "js": "javascript", "javascript": "javascript", "node": "javascript",
            "mjs": "javascript"}
_PORTAS: dict[str, int] = {}  # servidor de teste -> porta (reaproveitado enquanto vivo)


def linguagem_de(codigo: str, dica: str = "") -> str:
    """html | python | javascript | ''. A dica é a do bloco (```html); sem ela, olha o conteúdo."""
    if lang := APELIDOS.get((dica or "").strip().lower()):
        return lang
    inicio = codigo.lstrip()[:600].lower()
    if inicio.startswith("<!doctype") or "<html" in inicio:
        return "html"
    if re.search(r"^\s*(def |class |import |from \S+ import |print\()", codigo, re.M):
        return "python"
    if re.search(r"\b(console\.log|function\s+\w+\s*\(|const\s+\w+\s*=|let\s+\w+\s*=)", codigo):
        return "javascript"
    return ""


def _porta_livre() -> int:
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


def testar_codigo(codigo: str, dica: str, chave: str, conv: int | str | None = None) -> dict:
    """Grava o código numa pasta de teste e diz como vê-lo rodar: HTML sobe um servidor estático
    (a tela abre no navegador integrado); Python e JavaScript viram um comando para o terminal.
    Cada resposta tem a própria pasta (chave = comparação + modelo), então testar de novo reaproveita."""
    lang = linguagem_de(codigo, dica)
    if not lang:
        raise ToolError("Não reconheci o código: o teste roda HTML (abre no navegador), Python ou JavaScript "
                        "(rodam no terminal).")
    pasta = TESTES_DIR / (re.sub(r"[^A-Za-z0-9_-]+", "-", chave).strip("-")[:60] or "teste")
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / ARQUIVO[lang]
    arquivo.write_text(codigo, encoding="utf-8")
    if lang != "html":
        return {"tipo": "terminal", "linguagem": lang, "arquivo": str(arquivo),
                "comando": f'{"python" if lang == "python" else "node"} "{arquivo}"'}
    nome = f"teste-{pasta.name}"
    vivo = any(x["name"] == nome and x["alive"] for x in shell.list_servers())
    if not (vivo and nome in _PORTAS):
        _PORTAS[nome] = _porta_livre()
        # PowerShell só executa caminho entre aspas com o operador &; sem ele, erro de sintaxe calado
        exe = f'& "{sys.executable}"' if native.WINDOWS else f'"{sys.executable}"'
        # da conversa do Comparar: é nela que a aba Instâncias mostra (e deixa parar) o servidor
        token = shell.CONV.set(str(conv)) if conv is not None else None
        try:
            shell._start(nome, f"{exe} -m http.server {_PORTAS[nome]} --bind 127.0.0.1", pasta)
        finally:
            if token is not None:
                shell.CONV.reset(token)
        time.sleep(0.8)  # o http.server leva um instante para aceitar conexão
    return {"tipo": "web", "linguagem": lang, "arquivo": str(arquivo),
            "url": f"http://127.0.0.1:{_PORTAS[nome]}/{ARQUIVO[lang]}", "servidor": nome}


def html_de(resposta: str) -> str:
    """O HTML de uma resposta: o bloco ```html, ou a resposta inteira se ela já é um documento."""
    if m := re.search(r"```(?:html|htm)?\s*\n(.*?)```", resposta or "", re.S | re.I):
        if linguagem_de(m.group(1)) == "html":
            return m.group(1)
    return resposta if linguagem_de(resposta or "") == "html" else ""


# ------------------------------------------------------------------ juiz

JUIZ = """Você é um avaliador técnico imparcial. Vários modelos (identificados só por letra) responderam à
MESMA tarefa. Compare as respostas com o GABARITO e as ESTATÍSTICAS medidas pelo Forja.

Responda em português, em Markdown, nesta ordem:
1. Uma tabela comparativa com as colunas: Modelo | Acertou o essencial? | Alucinou? | Qualidade (0–10) | Velocidade (tok/s · tempo) | Observação curta.
   - "Alucinou?" = afirmou algo falso ou que não está no material/gabarito (diga o quê, curto).
   - Velocidade: copie os números das estatísticas; não invente.
2. **Vencedor geral** e, em uma linha cada: mais correto, mais rápido, melhor custo-benefício.
3. Até 3 frases de justificativa. Nada de elogio genérico.
Modelo que deu erro ou não respondeu recebe nota 0 e "sem resposta".
Se vierem PRINTS (a página de cada modelo aberta de verdade, desktop e celular), julgue também o que se
VÊ: layout quebrado, texto cortado, contraste, alinhamento, se o celular respeita a largura pedida. O
visual conta na nota e ganha uma coluna "Visual" na tabela."""

MIN_RESPOSTA = 6000     # caracteres de cada resposta mandados ao juiz, no mínimo
CHARS_POR_TOKEN = 3.0   # código: ~3 caracteres por token
FRACAO_RESPOSTAS = 0.6  # da janela do juiz para as respostas (o resto: tarefa, gabarito, prints, a análise)
CORTE = ("\n[— o Forja cortou a resposta aqui só para caber na janela do revisor; ela continua. NÃO conte "
         "isto como resposta truncada ou incompleta do modelo. —]")


def limite_por_resposta(ctx: int | None, n: int) -> int:
    """Quanto de cada resposta cabe no pedido ao juiz. Era um teto fixo de 6000 caracteres: uma página
    HTML inteira passa disso, o juiz via o código cortado e desclassificava o modelo por "truncado"."""
    if not ctx:
        return MIN_RESPOSTA * 3
    return max(MIN_RESPOSTA, int(ctx * CHARS_POR_TOKEN * FRACAO_RESPOSTAS / max(1, n)))


def com_nomes(texto: str, itens: list[dict]) -> str:
    """Fora do modo cego: a análise diz o nome do modelo, não a letra. O juiz continua vendo só letras
    (para não favorecer nome conhecido); a troca é feita depois, no texto dele."""
    nomes = {i["rotulo"]: i["nome"] for i in itens}
    texto = re.sub(r"\b[Mm][Oo][Dd][Ee][Ll][Oo] ([A-F])\b", lambda m: nomes.get(m.group(1), m.group(0)), texto)
    # primeira coluna da tabela: "| A |" ou "| **A** |"
    return re.sub(r"(?m)^(\|\s*)(\*\*)?([A-F])(\*\*)?(\s*\|)",
                  lambda m: (f"{m.group(1)}{m.group(2) or ''}{nomes[m.group(3)]}{m.group(4) or ''}{m.group(5)}"
                             if m.group(3) in nomes else m.group(0)), texto)


def pedido_ao_juiz(prompt: str, bateria: str, itens: list[dict], limite: int = MIN_RESPOSTA * 3) -> list[dict]:
    gabarito = (BATERIAS.get(bateria) or {}).get("gabarito") or "(sem gabarito: julgue pela correção técnica e pela tarefa)"
    partes = [f"TAREFA:\n{prompt}", f"GABARITO:\n{gabarito}"]
    for it in itens:
        s = it.get("stats") or {}
        medida = (f"{s.get('tokens')} tokens, {s.get('seconds')} s, {s.get('tps') or '?'} tok/s" if s
                  else "sem estatística")
        inteiro = (it.get("content") or "").strip()
        corpo = (inteiro[:limite] + (CORTE if len(inteiro) > limite else "")) if inteiro \
            else f"(sem resposta: {it.get('error') or it.get('status')})"
        partes.append(f"=== MODELO {it['rotulo']} — {medida} ===\n{corpo}")
    return [{"role": "system", "content": JUIZ}, {"role": "user", "content": "\n\n".join(partes)}]


async def julgar(message_id: int, provider: str, model: str) -> AsyncIterator[dict]:
    """Manda a comparação ao juiz e grava a análise como resposta na mesma conversa (um chat)."""
    from . import comparar, modelctl
    from .agent import _save
    m = comparar._mensagem(message_id)
    meta = m["meta"] or {}
    if m["status"] == "running":
        raise ToolError("Espere a comparação terminar.")
    with db.session() as s:
        anterior = s.query(db.Message).filter(db.Message.conversation_id == m["conversation_id"],
                                              db.Message.id < message_id, db.Message.role == "user") \
            .order_by(db.Message.id.desc()).first()
        prompt, bateria = (anterior.content or "", (anterior.meta or {}).get("bateria") or "") if anterior else ("", "")
    if not (provider and model):
        raise ToolError("Escolha o modelo que vai analisar.")
    itens = meta.get("itens") or []
    cego = bool(meta.get("cego")) and not meta.get("revelado")
    galeria: list[dict] = []  # prints tirados: vão numa tabela no fim da análise
    fotos, legendas = [], []
    # Teste de frontend e juiz com visão: cada página aberta de verdade e fotografada (desktop e
    # celular) — o juiz julga o que se vê, não só o código.
    if bateria == "frontend" and await _tem_visao(provider, model):
        async for ev in _prints(m["conversation_id"], message_id, itens, fotos, legendas, galeria):
            yield ev
    # IA local: o revisor sobe com a janela do tamanho do que vai ler (respostas inteiras, prints e a
    # análise), só para esta análise — a configuração salva do modelo não muda. Com a janela salva de
    # 131072 o Qwen3.6 enchia a placa e o encoder de visão passava 3 minutos num print de 720p.
    spec = {"provider": provider, "model": model}
    temporario = None
    if modelctl.gerenciavel(spec):
        temporario = {"ctx": janela_do_revisor(prompt, itens, len(fotos)), "parallel": 1}
        janela = f"{temporario['ctx']:,}".replace(",", ".")
        yield {"etapa": f"Janela do revisor: {janela} tokens para {len(itens)} respostas"
                        + (f" e {len(fotos)} prints" if fotos else "") + " (só nesta análise)"}
    efeito: dict = {}
    # Cada fase vira um passo visível: carregar um modelo local grande leva minutos, e sem isto a tela
    # parecia travada — não dava para saber se ele estava subindo, lendo ou alucinando.
    async for ev in modelctl.ensure(spec, efeito, temporario=temporario):  # IA local: sobe o juiz
        if texto_fase := FASES_DO_MODELO.get(ev.get("phase", "")):
            yield {"etapa": texto_fase.format(model=model, anterior=ev.get("previous") or "o modelo anterior")}
    try:
        ctx_juiz = await llm.context_limit(provider, model, config.NUM_CTX)
    except Exception:  # sem a janela, usa o mínimo generoso
        ctx_juiz = None
    mensagens = pedido_ao_juiz(prompt, bateria, itens, limite_por_resposta(ctx_juiz, len(itens)))
    if fotos:
        from . import uploads
        mensagens[-1] = uploads.user_message(
            mensagens[-1]["content"] + "\n\nPRINTS, nesta ordem:\n" + "\n".join(legendas), fotos)
    fotos_n = sum(1 for x in mensagens[-1]["content"] if isinstance(x, dict) and x.get("type") == "image_url") \
        if isinstance(mensagens[-1]["content"], list) else 0
    yield {"etapa": f"{model} lendo {len(itens)} respostas" + (f" e {fotos_n} prints" if fotos_n else " e as estatísticas")}
    texto = pensou = ""
    primeiro = True
    # Estatísticas iguais às das respostas (tokens, tok/s, tempo), medidas do mesmo jeito: o relógio
    # da geração começa no primeiro token, pensado ou não.
    t0, t_first, done = time.monotonic(), 0.0, {}
    async for tipo, valor in llm.chat_stream(provider, model, mensagens, None, config.NUM_CTX, "medio"):
        if tipo == "done":
            done = valor or {}
            continue
        if primeiro and tipo in ("content", "reasoning"):
            primeiro = False
            t_first = time.monotonic()
            yield {"etapa": "Escrevendo a análise" if tipo == "content" else "Raciocinando sobre as respostas"}
        if tipo == "content":
            texto += valor
            yield {"delta": valor}
        elif tipo == "reasoning":  # o raciocínio do revisor aparece e fica gravado, como no chat
            pensou += valor
            yield {"pensando": valor}
    from .agent import _stats
    from .parsing import split_think
    try:
        ctx = await llm.context_limit(provider, model, config.NUM_CTX)
    except Exception:  # só enfeita a estatística; não derruba a análise
        ctx = None
    stats = _stats(mensagens, None, texto, pensou, done, t0, t_first, ctx, model)
    embutido, visivel = split_think(texto)
    # Modo cego: letras, sem legenda e sem nomes na tabela de prints (revelar é o voto que faz).
    legenda = tabela_de_prints(galeria, itens, m["conversation_id"], cego)
    if not cego:
        visivel, texto = com_nomes(visivel, itens), com_nomes(texto, itens)
    msg = _save(m["conversation_id"], role="assistant", content=(visivel or texto).strip() + legenda,
                thinking=(pensou or embutido or "").strip() or None,
                meta={"julgamento": {"de": message_id, "juiz": model, "provider": provider}, "stats": stats})
    if temporario and efeito.get("swapped"):
        # Subiu com a janela do revisor: descarrega, para o próximo uso voltar à configuração salva.
        yield {"etapa": f"Descarregando {model} (o próximo uso volta à sua configuração)"}
        async for _ in modelctl.unload("fim da análise"):
            pass
    yield {"fim": msg.to_dict()}


TOKENS_POR_PRINT = 1000   # 720p no Qwen3.6 ≈ 933 tokens (browser.PRINT_VIEWPORT)
FOLGA_DA_ANALISE = 12_000  # instruções, gabarito, raciocínio e a própria análise
JANELA_MIN, JANELA_MAX = 16_384, 131_072


def janela_do_revisor(prompt: str, itens: list[dict], prints: int) -> int:
    """Contexto de que o revisor precisa: cresce com o número de modelos e o tamanho das respostas."""
    texto = len(prompt) + sum(len(i.get("content") or "") for i in itens)
    precisa = int(texto / CHARS_POR_TOKEN) + prints * TOKENS_POR_PRINT + FOLGA_DA_ANALISE
    return max(JANELA_MIN, min(JANELA_MAX, -(-precisa // 4096) * 4096))  # múltiplo de 4096


FASES_DO_MODELO = {
    "unloading": "Descarregando {anterior} para liberar a memória",
    "clearing": "Esperando a memória de vídeo liberar",
    "loading": "Carregando {model} na memória (modelo grande leva alguns minutos)",
    "ready": "{model} carregado",
}


async def _tem_visao(provider: str, model: str) -> bool:
    from .tools import vision_caps
    try:
        return "vision" in vision_caps(await llm.capabilities(provider, model), db.get_model_setting(model)["vision"])
    except Exception:  # capacidade desconhecida: julga só pelo texto
        return False


# Uma tela por print, as mesmas medidas do modo agente e da revisão visual da Maestro (1280x720 e
# 390x844). Não é gosto: o encoder de visão do modelo local degrada muito além de ~1 MP (medido no
# Qwen3.6: 8 s por imagem de 720p, 68 s em 1080p — ver browser.PRINT_VIEWPORT). Prints de 1280x1400 e
# 390x1600 somavam ~5 MP e deixaram o revisor 10 minutos "lendo" sem responder.
from .qualidade import TELAS as TELAS_JUIZ  # noqa: E402


def tabela_de_prints(galeria: list[dict], itens: list[dict], conv_id: int, cego: bool = False) -> str:
    """Os prints que o revisor viu, numa tabela Markdown (uma linha por modelo, desktop e celular)."""
    if not galeria:
        return ""
    from urllib.parse import quote

    nomes = {i["rotulo"]: i["nome"] for i in itens}
    telas = [t[0] for t in TELAS_JUIZ]
    linhas = ["", "", "### Prints que o revisor analisou", "",
              "| Modelo | " + " | ".join(t.capitalize() for t in telas) + " |", "|---" * (len(telas) + 1) + "|"]
    for rotulo in dict.fromkeys(g["rotulo"] for g in galeria):
        celulas = []
        for tela in telas:
            g = next((x for x in galeria if x["rotulo"] == rotulo and x["tela"] == tela), None)
            celulas.append(f"![{rotulo} {tela}](/api/files?path={quote(g['path'])}&conv={conv_id})" if g else "—")
        quem = f"Modelo {rotulo}" if cego else nomes.get(rotulo, rotulo)
        linhas.append(f"| **{quem}** | " + " | ".join(celulas) + " |")
    return "\n".join(linhas)


async def _prints(conv_id: int, message_id: int, itens: list[dict], fotos: list, legendas: list,
                  galeria: list) -> AsyncIterator[dict]:
    """Sobe a página de cada modelo e tira os prints no navegador desta conversa (dá para ver no painel)."""
    from . import browser
    browser.CURRENT_KEY.set(str(conv_id))
    raiz = TESTES_DIR
    for it in itens:
        html = html_de(it.get("content") or "")
        if not html:
            legendas.append(f"MODELO {it['rotulo']}: não entregou HTML (sem print)")
            continue
        r = testar_codigo(html, "html", f"{message_id}-{it['rotulo']}", conv_id)
        for nome, largura, altura in TELAS_JUIZ:
            yield {"etapa": f"Abrindo a página do modelo {it['rotulo']} ({nome}, {largura}x{altura})…"}
            try:
                await browser.navigate(raiz, {"url": r["url"]})
                foto = await browser.screenshot(raiz, {"largura": largura, "altura": altura})
            except Exception as e:  # navegador fechado, página quebrada: segue sem este print
                legendas.append(f"MODELO {it['rotulo']} — {nome}: print falhou ({e})")
                continue
            fotos += foto["attachments"]
            legendas.append(f"MODELO {it['rotulo']} — {nome} {largura}x{altura}")
            galeria.append({"rotulo": it["rotulo"], "tela": nome, "path": foto["attachments"][0]["path"]})


# ------------------------------------------------------------------ a análise roda no servidor
# Presa à conexão da tela, a análise morria ao trocar de página: o revisor parou em 81% da leitura e
# nada foi gravado. Como a comparação, ela vive numa tarefa daqui e a tela só acompanha (e reconecta).

import asyncio  # noqa: E402

_ANALISES: dict[int, dict] = {}   # message_id da comparação -> andamento da análise
_TAREFAS_ANALISE: set = set()      # referência forte (o loop só guarda uma fraca)
CHARS_POR_TOKEN_VIVO = 3.5


def iniciar_analise(message_id: int, provider: str, model: str) -> dict:
    """Começa (ou devolve a que já está rodando) a análise desta comparação."""
    atual = _ANALISES.get(message_id)
    if atual and atual["status"] == "rodando":
        return atual
    a = _ANALISES[message_id] = {"status": "rodando", "juiz": model, "passos": ["Preparando a análise"],
                                 "texto": "", "pensou": "", "stats": None, "erro": "", "fim": None,
                                 "t0": time.monotonic(), "t_first": 0.0}

    async def roda():
        try:
            async for ev in julgar(message_id, provider, model):
                if "etapa" in ev:
                    if a["passos"][-1] != ev["etapa"]:
                        a["passos"].append(ev["etapa"])
                elif "pensando" in ev:
                    a["t_first"] = a["t_first"] or time.monotonic()
                    a["pensou"] += ev["pensando"]
                elif "delta" in ev:
                    a["t_first"] = a["t_first"] or time.monotonic()
                    a["texto"] += ev["delta"]
                elif "fim" in ev:
                    fim = ev["fim"]
                    a.update(fim=fim, texto=fim["content"], pensou=fim.get("thinking") or a["pensou"],
                             stats=(fim.get("meta") or {}).get("stats"))
            a["status"] = "pronto"
        except asyncio.CancelledError:
            a["status"] = "parado"
        except Exception as e:  # modelo caiu, sem memória, modelo sem visão: vira o resultado
            a.update(status="erro", erro=str(e) or e.__class__.__name__)

    t = asyncio.create_task(roda())
    a["_task"] = t
    _TAREFAS_ANALISE.add(t)
    t.add_done_callback(_TAREFAS_ANALISE.discard)
    return a


def estado_analise(message_id: int) -> dict | None:
    """Retrato para a tela (SSE). Enquanto gera, a estatística é estimada pelos caracteres."""
    a = _ANALISES.get(message_id)
    if not a:
        return None
    out = {k: v for k, v in a.items() if not k.startswith("_") and k not in ("t0", "t_first")}
    if a["status"] == "rodando":
        agora = time.monotonic()
        tokens = round((len(a["texto"]) + len(a["pensou"])) / CHARS_POR_TOKEN_VIVO)
        gerando = agora - a["t_first"] if a["t_first"] else 0
        out["stats"] = {"tokens": tokens, "seconds": round(agora - a["t0"], 1),
                        "tps": round(tokens / gerando, 1) if gerando > 0.5 else None, "estimated": True}
    return out


def parar_analise(message_id: int) -> None:
    a = _ANALISES.get(message_id)
    if a and a["status"] == "rodando" and a.get("_task"):
        a["_task"].cancel()  # fecha o stream do modelo: o llama-server para de gerar

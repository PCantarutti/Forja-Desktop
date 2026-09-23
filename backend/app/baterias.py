"""Baterias de teste por especialidade de Worker, para o Comparar, e o juiz que analisa o resultado.

A pergunta que o usuário quer responder é "qual modelo local serve para frontend, qual para lógica,
qual para ler documento". Cada bateria é uma tarefa curta com resposta conferível (gabarito): é o
gabarito que deixa o juiz dizer quem acertou e quem alucinou, em vez de só gostar mais de um texto.
Na de documentos o Forja fornece o arquivo — e pergunta algo que NÃO está nele, que é onde modelo
fraco inventa.
"""
from __future__ import annotations

import asyncio
import re
import socket
import sys
import time

from typing import AsyncIterator

from . import config, db, llm, native, shell
from .tools import ToolError

SYSTEM = "Responda em português do Brasil. Seja direto e preciso; se algo não estiver no material, diga que não consta."

POLITICA = """# Política de Reembolso e Alterações — Aurora Viagens (versão 4.0, vigente desde 01/03/2026)

Esta versão substitui a 3.2. Na versão 3.2 a multa do item 1.2 era de 10%; ela não vale mais.

## 1. Cancelamento pelo cliente
1.1. O cliente pode cancelar sem multa em até 7 (sete) dias corridos após a compra, desde que, na data
do cancelamento, faltem mais de 30 (trinta) dias para o início de uma viagem nacional, ou mais de 60
(sessenta) dias para o início de uma viagem internacional.
1.2. Fora da condição do item 1.1, o cancelamento tem multa de 20% sobre o valor total do pacote.
1.3. Cancelamentos a menos de 72 horas do embarque não têm reembolso; o valor pago vira crédito de 50%.

## 2. Forma e prazo do reembolso
2.1. O reembolso é feito no mesmo meio de pagamento da compra, em até 10 (dez) dias úteis.
2.2. Compras pagas por boleto são reembolsadas por PIX, em até 5 (cinco) dias úteis, para uma chave em
nome do titular da compra.
2.3. Os prazos em dias úteis contam a partir do dia útil seguinte ao pedido de reembolso. Dia útil é de
segunda a sexta, exceto feriados nacionais. Feriados nacionais de 2026 considerados: 03/04 (Sexta-feira
Santa), 21/04 (Tiradentes) e 01/05 (Dia do Trabalho).

## 3. Pacotes promocionais
3.1. Pacotes marcados como "Tarifa Promocional" não são reembolsáveis.
3.2. Nesses pacotes, o cancelamento gera crédito de 70% do valor pago, válido por 12 meses, para qualquer
produto da Aurora Viagens.
3.3. Em caso de conflito com a seção 1, esta seção prevalece.

## 4. Alteração de data
4.1. Alterar a data não é cancelamento: custa uma taxa fixa de R$ 150,00 por passageiro, desde que o
pedido seja feito até 15 dias antes do embarque.
4.2. A menos de 15 dias do embarque não é possível alterar a data; só cancelar, conforme a seção 1.

## 5. Cancelamento pela Aurora Viagens
5.1. Se a Aurora cancelar a viagem, o cliente escolhe entre reembolso integral em até 10 dias úteis ou
remarcação sem custo em até 6 meses.

## 6. Contato
Pedidos de reembolso e alteração: reembolso@auroraviagens.com.br ou pelo aplicativo, em Minhas Viagens.
"""

CODIGO_TESTES = r'''import re

def minutos(texto: str) -> int:
    """Converte uma duração em minutos.

    Formatos aceitos (sem espaços): "45m", "2h", "1h30m" — horas antes de minutos.
    - Com horas, os minutos vão de 0 a 59 ("1h60m" é inválido); sozinhos, qualquer valor ("90m" = 90).
    - "0m" e "0h" valem 0.
    - Texto vazio, com espaço, número sem unidade ("30") ou fora de ordem ("30m1h"): ValueError.
    """
    m = re.fullmatch(r"(?:(\d+)h)?\s*(?:(\d+)m)?", texto)
    if not m:
        raise ValueError(texto)
    h, mi = m.groups()
    if h and mi and int(mi) > 60:
        raise ValueError(texto)
    return int(h or 0) * 60 + int(mi or 0)'''

CODIGO_GERAL = """// Recebe reservas {inicio, fim} com horários em texto "H:MM" ou "HH:MM" (fim > inicio, mesmo dia), em
// qualquer ordem, e devolve os períodos ocupados: reservas que se sobrepõem OU encostam (uma termina
// quando a outra começa) viram um período só. Resultado em ordem de início, cada item {inicio, fim} em
// texto. Não altera a lista recebida nem os objetos dela. Lista vazia: [].
function ocupados(reservas) {
  const ordenadas = reservas.slice().sort((a, b) => (a.inicio < b.inicio ? -1 : 1));
  const res = [ordenadas[0]];
  for (const r of ordenadas.slice(1)) {
    const ultimo = res[res.length - 1];
    if (r.inicio < ultimo.fim) {
      ultimo.fim = r.fim;
    } else {
      res.push(r);
    }
  }
  return res;
}"""

BATERIAS: dict[str, dict] = {
    "logica": {
        "titulo": "Lógica e back-end",
        "mede": "regra de negócio com estado que interage (FIFO de estoque com devolução), validação e se o modelo "
                "calcula certo sem executar — o Forja roda a função contra 19 casos",
        "prompt": ("Escreva em Python a função `custo_fifo(movimentos)` que calcula o custo das saídas de estoque "
                   "pelo método FIFO (o primeiro lote que entrou é o primeiro a sair).\n"
                   "- `movimentos` é uma lista de tuplas: (\"entrada\", quantidade, custo_unitario_em_centavos) ou "
                   "(\"saida\", quantidade).\n"
                   "- Devolve `(custo_total_das_saidas_em_centavos, lotes_restantes)`, com `lotes_restantes` na ordem "
                   "FIFO como lista de tuplas `(quantidade, custo_unitario)`.\n"
                   "- Uma saída consome dos lotes mais antigos e pode atravessar vários lotes; lote zerado sai da "
                   "lista.\n"
                   "- (\"devolucao\", quantidade): o cliente devolve unidades da saída MAIS RECENTE. A devolução "
                   "desfaz essa saída de trás para frente: devolve primeiro as unidades que saíram por último, e "
                   "cada unidade devolvida entra NA FRENTE de tudo o que está na fila (inclusive das devolvidas "
                   "antes dela), com o custo com que saiu — devolver a saída inteira deixa a fila exatamente como "
                   "estava antes dela. O custo das unidades devolvidas é descontado do total. No início da fila, unidades de mesmo custo lado a "
                   "lado formam um lote só. Devoluções seguidas continuam desfazendo a mesma saída.\n"
                   "- Quantidade menor ou igual a zero, saída maior que o estoque disponível, devolução maior que o "
                   "que ainda resta da última saída (ou sem saída antes) e tipo desconhecido: ValueError.\n"
                   "- Tudo em inteiros (centavos); nada de float.\n\n"
                   "Depois, SEM executar, diga o retorno de: custo_fifo([(\"entrada\", 10, 500), (\"entrada\", 5, 800), "
                   "(\"saida\", 12), (\"entrada\", 3, 700), (\"saida\", 4), (\"devolucao\", 2)])"),
        "gabarito": ("O exemplo devolve (8200, [(1, 800), (3, 700)]): a 1ª saída consome 10×500 + 2×800 = 6600 e sobra "
                     "(3, 800); entra (3, 700); a 2ª saída consome 3×800 + 1×700 = 3100 (total 9700) e sobra (2, 700); "
                     "a devolução de 2 desfaz a 2ª saída de trás para frente: volta 1×700 (junta com o (2, 700) do "
                     "início → (3, 700)) e depois 1×800 → [(1, 800), (3, 700)]; total 9700 − 700 − 800 = 8200. Erros "
                     "comuns: consumir do lote mais NOVO (LIFO), não atravessar lotes, deixar lote com quantidade 0, "
                     "devolver as primeiras unidades da saída em vez das últimas, pôr o devolvido no FIM da fila, não "
                     "juntar lotes de mesmo custo no início, esquecer o que já foi devolvido, e errar a conta manual."),
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
        "mede": "escrever testes a partir do comportamento documentado — o Forja roda os testes do modelo contra "
                "a versão certa e contra 3 versões com um bug cada, e conta quantos bugs eles pegam",
        "prompt": ("Escreva testes pytest para a função abaixo, cobrindo o comportamento DOCUMENTADO na docstring "
                   "(não o que o código faz). Depois diga quais dos seus testes falham com esta implementação e por "
                   "quê. Não invente regras que a docstring não diz. Responda com os testes num bloco python.\n\n"
                   "```python\n" + CODIGO_TESTES + "\n```"),
        "gabarito": ("A implementação tem 3 bugs contra a docstring: (1) texto vazio \"\" devolve 0 em vez de ValueError "
                     "(o regex casa vazio); (2) \"1h60m\" é aceito (compara > 60 em vez de > 59); (3) \"1h 30m\" é "
                     "aceito (o \\s* no regex permite espaço). Bons testes: \"45m\"=45, \"2h\"=120, \"1h30m\"=90, "
                     "\"90m\"=90, \"0m\"=0, \"1h59m\"=119, e ValueError para \"\", \"1h60m\", \"1h 30m\", \"30\", "
                     "\"30m1h\". Testes que exigem algo fora da docstring estão errados."),
    },
    "docs": {
        "titulo": "Documentação e leitura de documentos",
        "mede": "ler um documento com exceções, precedência entre regras e contagem de dias úteis com feriado — e "
                "dizer 'não consta' em vez de inventar",
        "anexo": {"nome": "politica-de-reembolso.md", "texto": POLITICA},
        "prompt": ("Leia o arquivo anexo (politica-de-reembolso.md) e responda, citando o item de cada resposta:\n"
                   "1. Comprei um pacote INTERNACIONAL comum em 02/03/2026, a viagem começa em 20/04/2026 e cancelei em "
                   "06/03/2026. Pago multa? De quanto?\n"
                   "2. Paguei por boleto e pedi o reembolso na segunda-feira, 30/03/2026. Como recebo e até que data?\n"
                   "3. Cancelei um pacote em \"Tarifa Promocional\" 48 horas antes do embarque. O que recebo?\n"
                   "4. Quero mudar a data de uma viagem de 2 passageiros, pedindo 20 dias antes do embarque. Quanto "
                   "pago?\n"
                   "5. Qual o telefone da central de atendimento?\n"
                   "6. A política cobre extravio de bagagem?"),
        "gabarito": ("1: SIM, multa de 20% (item 1.2): o cancelamento foi em até 7 dias da compra, mas para viagem "
                     "internacional precisam faltar mais de 60 dias e faltavam 45 (item 1.1). Os 10% da versão 3.2 NÃO "
                     "valem mais. 2: por PIX (item 2.2), até 07/04/2026 — a contagem começa no dia útil seguinte "
                     "(31/03) e pula o feriado de 03/04: 31/03, 01/04, 02/04, 06/04, 07/04 (item 2.3). 3: crédito de "
                     "70% válido por 12 meses (item 3.2), porque a seção 3 prevalece sobre o item 1.3 (item 3.3); dizer "
                     "crédito de 50% é erro. 4: R$ 300,00 — R$ 150,00 por passageiro, pedido com mais de 15 dias "
                     "(item 4.1). 5: NÃO consta telefone (só e-mail e aplicativo). 6: NÃO consta nada sobre bagagem. "
                     "Inventar telefone, regra de bagagem ou usar a multa de 10% é alucinação."),
    },
    "geral": {
        "titulo": "Geral (achar e corrigir bugs)",
        "mede": "achar TODOS os bugs de uma função curta com armadilhas sutis (texto comparado como hora, objeto "
                "alterado por referência), corrigir e prever a saída — o Forja roda a versão corrigida contra 10 casos",
        "prompt": ("Esta função JavaScript não faz o que o comentário diz. Liste todos os problemas, mostre a versão "
                   "corrigida (num bloco js, mantendo o nome ocupados) e diga, SEM executar, o que ela devolve para:\n"
                   "ocupados([{inicio: \"9:00\", fim: \"10:30\"}, {inicio: \"13:30\", fim: \"14:00\"}, "
                   "{inicio: \"10:30\", fim: \"11:00\"}, {inicio: \"13:00\", fim: \"15:00\"}, "
                   "{inicio: \"8:15\", fim: \"9:00\"}])\n\n```js\n" + CODIGO_GERAL + "\n```"),
        "gabarito": ("Problemas: (1) compara horários como TEXTO: \"9:00\" > \"10:00\", então a ordenação e as "
                     "comparações erram com hora de um dígito — tem que converter para minutos; (2) usa < e não "
                     "junta reservas que encostam (fim == início); (3) reserva contida numa maior ENCURTA o período "
                     "(fim = r.fim em vez do maior dos dois fins, comparado em minutos); (4) altera os objetos "
                     "recebidos: slice() copia só a lista, e ultimo.fim = ... muda o objeto original do chamador; "
                     "(5) lista vazia devolve [undefined]. Saída do exemplo: [{inicio: \"8:15\", fim: \"11:00\"}, "
                     "{inicio: \"13:00\", fim: \"15:00\"}]. Erros comuns: achar que o slice() resolve a mutação, "
                     "trocar para <= mas continuar comparando texto, e prever fim \"14:00\" no segundo período."),
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


def testar_codigo(codigo: str, dica: str, chave: str, conv: int | str | None = None, bateria: str = "") -> dict:
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
    from . import conferencia
    com_casos = conferencia.para_testar(bateria, lang, codigo) if bateria else None
    arquivo.write_text(com_casos[0] if com_casos else codigo, encoding="utf-8")
    if lang != "html":
        modo = com_casos[1] if com_casos else ("python" if lang == "python" else "node")
        comando = (f'python -m pytest -v -p no:cacheprovider "{arquivo}"' if modo == "pytest"
                   else f'{"python" if modo == "python" else "node"} "{arquivo}"')
        return {"tipo": "terminal", "linguagem": lang, "arquivo": str(arquivo), "comando": comando}
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
Se vier CONFERÊNCIA AUTOMÁTICA, ela é FATO: o Forja executou o código de cada modelo contra casos com
resposta certa. Use-a para decidir quem acertou; não contradiga o que foi medido. Quem passou em TODOS
os casos resolveu a tarefa: não tire nota dele por estilo ou formato. Antes de apontar defeito num
código, cite o trecho exato — defeito que você não consegue citar não existe.
Refira-se aos modelos sempre como "Modelo A", "Modelo B"… — nunca a letra sozinha.
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
    # letra sozinha em negrito ("Mais correto: **D**"), mesmo o prompt pedindo "Modelo D"
    texto = re.sub(r"\*\*([A-F])\*\*", lambda m: f"**{nomes[m.group(1)]}**" if m.group(1) in nomes else m.group(0), texto)
    # "o A não gerou código", "do B", "que o C": artigo + letra maiúscula sozinha
    texto = re.sub(r"\b([Oo]|do|ao|no|pelo) ([A-F])\b(?![\w'’-])",
                   lambda m: f"{m.group(1)} {nomes[m.group(2)]}" if m.group(2) in nomes else m.group(0), texto)
    # primeira coluna da tabela: "| A |" ou "| **A** |"
    return re.sub(r"(?m)^(\|\s*)(\*\*)?([A-F])(\*\*)?(\s*\|)",
                  lambda m: (f"{m.group(1)}{m.group(2) or ''}{nomes[m.group(3)]}{m.group(4) or ''}{m.group(5)}"
                             if m.group(3) in nomes else m.group(0)), texto)


def pedido_ao_juiz(prompt: str, bateria: str, itens: list[dict], limite: int = MIN_RESPOSTA * 3,
                   conferido: dict[str, dict] | None = None) -> list[dict]:
    gabarito = (BATERIAS.get(bateria) or {}).get("gabarito") or "(sem gabarito: julgue pela correção técnica e pela tarefa)"
    partes = [f"TAREFA:\n{prompt}", f"GABARITO:\n{gabarito}"]
    for it in itens:
        s = it.get("stats") or {}
        medida = (f"{s.get('tokens')} tokens, {s.get('seconds')} s, {s.get('tps') or '?'} tok/s" if s
                  else "sem estatística")
        inteiro = (it.get("content") or "").strip()
        corpo = (inteiro[:limite] + (CORTE if len(inteiro) > limite else "")) if inteiro \
            else f"(sem resposta: {it.get('error') or it.get('status')})"
        if conf := (conferido or {}).get(it["rotulo"]):
            corpo = f"CONFERÊNCIA AUTOMÁTICA (executado): nota {conf.get('nota')}/10 — {conf.get('resumo')}\n\n" + corpo
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
    # quem acompanha ao vivo também vê nomes (fora do modo cego), não só o texto final
    yield {"rotulos": [] if cego else [{"rotulo": i["rotulo"], "nome": i["nome"]} for i in itens]}
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
    # Conferência automática: o que dá para medir executando o código, mede-se (ver conferencia.py).
    from . import conferencia
    conferido: dict[str, dict] = {}
    if bateria in conferencia.CONFERE:
        yield {"etapa": "Conferindo: executando o código de cada modelo contra os casos do gabarito"}
        for it in itens:
            if r := await asyncio.to_thread(conferencia.conferir, bateria, it.get("content") or ""):
                conferido[it["rotulo"]] = r
    mensagens = pedido_ao_juiz(prompt, bateria, itens, limite_por_resposta(ctx_juiz, len(itens)), conferido)
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
    legenda = tabela_de_conferencia(conferido, itens, cego) + tabela_de_prints(galeria, itens, m["conversation_id"], cego)
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


def tabela_de_conferencia(conferido: dict[str, dict], itens: list[dict], cego: bool) -> str:
    """O que foi medido executando o código, numa tabela no fim da análise (fato, não opinião do revisor)."""
    if not conferido:
        return ""
    linhas = ["", "", "### Conferência automática (o Forja executou o código)", "", "| Modelo | Nota | O que foi medido |",
              "|---|---|---|"]
    for it in itens:
        if c := conferido.get(it["rotulo"]):
            quem = f"Modelo {it['rotulo']}" if cego else it["nome"]
            nota = "—" if c.get("nota") is None else f"{c['nota']}/10"
            resumo = " ".join(str(c.get("resumo") or "").split()).replace("|", "/")  # quebra de linha desmonta a tabela
            linhas.append(f"| **{quem}** | {nota} | {resumo} |")
    return "\n".join(linhas)


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
                if "rotulos" in ev:
                    a["_rotulos"] = ev["rotulos"]
                elif "etapa" in ev:
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
    if a["status"] == "rodando" and a.get("_rotulos"):
        out["texto"] = com_nomes(out["texto"], a["_rotulos"])
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

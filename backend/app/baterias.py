"""Baterias de teste por especialidade de Worker, para o Comparar, e o juiz que analisa o resultado.

A pergunta que o usuário quer responder é "qual modelo local serve para frontend, qual para lógica,
qual para ler documento". Cada bateria é uma tarefa curta com resposta conferível (gabarito): é o
gabarito que deixa o juiz dizer quem acertou e quem alucinou, em vez de só gostar mais de um texto.
Na de documentos o Forja fornece o arquivo — e pergunta algo que NÃO está nele, que é onde modelo
fraco inventa.
"""
from __future__ import annotations

from typing import AsyncIterator

from . import config, db, llm
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
        "mede": "HTML/CSS/JS de verdade: responsividade, acessibilidade, estado salvo e capricho visual",
        "prompt": ("Crie UM arquivo HTML (CSS e JS embutidos, sem bibliotecas nem CDN) com um card de assinatura: "
                   "título 'Plano Pro', preço 'R$ 49/mês', lista com 4 benefícios e botão 'Assinar'. Requisitos:\n"
                   "1. Responsivo: em 360px de largura o card ocupa a tela com margem de 16px; acima de 768px fica "
                   "centralizado com no máximo 400px.\n2. Contraste AA e foco visível no botão (teclado).\n"
                   "3. Botão de tema claro/escuro que guarda a escolha em localStorage e a aplica ao recarregar.\n"
                   "Responda só com o código."),
        "gabarito": ("Tem: meta viewport; layout com max-width 400px e margem 16px no celular (media query ou "
                     "min()/clamp); :focus-visible (ou :focus) com contorno visível; alternância de tema que lê o "
                     "localStorage AO CARREGAR e grava ao clicar; nenhum recurso externo; os 4 benefícios e o preço "
                     "exatos. Erros comuns: esquecer de ler o tema salvo no carregamento, remover o outline sem "
                     "substituto, usar CDN."),
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


# ------------------------------------------------------------------ juiz

JUIZ = """Você é um avaliador técnico imparcial. Vários modelos (identificados só por letra) responderam à
MESMA tarefa. Compare as respostas com o GABARITO e as ESTATÍSTICAS medidas pelo Forja.

Responda em português, em Markdown, nesta ordem:
1. Uma tabela comparativa com as colunas: Modelo | Acertou o essencial? | Alucinou? | Qualidade (0–10) | Velocidade (tok/s · tempo) | Observação curta.
   - "Alucinou?" = afirmou algo falso ou que não está no material/gabarito (diga o quê, curto).
   - Velocidade: copie os números das estatísticas; não invente.
2. **Vencedor geral** e, em uma linha cada: mais correto, mais rápido, melhor custo-benefício.
3. Até 3 frases de justificativa. Nada de elogio genérico.
Modelo que deu erro ou não respondeu recebe nota 0 e "sem resposta"."""

MAX_RESPOSTA = 6000  # caracteres de cada resposta mandados ao juiz


def pedido_ao_juiz(prompt: str, bateria: str, itens: list[dict]) -> list[dict]:
    gabarito = (BATERIAS.get(bateria) or {}).get("gabarito") or "(sem gabarito: julgue pela correção técnica e pela tarefa)"
    partes = [f"TAREFA:\n{prompt}", f"GABARITO:\n{gabarito}"]
    for it in itens:
        s = it.get("stats") or {}
        medida = (f"{s.get('tokens')} tokens, {s.get('seconds')} s, {s.get('tps') or '?'} tok/s" if s
                  else "sem estatística")
        corpo = (it.get("content") or "").strip()[:MAX_RESPOSTA] or f"(sem resposta: {it.get('error') or it.get('status')})"
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
    async for _ in modelctl.ensure({"provider": provider, "model": model}):  # IA local: sobe o juiz
        pass
    texto = ""
    async for tipo, valor in llm.chat_stream(provider, model, pedido_ao_juiz(prompt, bateria, meta.get("itens") or []),
                                             None, config.NUM_CTX, "medio"):
        if tipo == "content":
            texto += valor
            yield {"delta": valor}
    from .parsing import split_think
    _, visivel = split_think(texto)
    legenda = "\n\n---\n*Legenda:* " + " · ".join(f"**{i['rotulo']}** = {i['nome']}" for i in meta.get("itens") or [])
    msg = _save(m["conversation_id"], role="assistant", content=(visivel or texto).strip() + legenda,
                meta={"julgamento": {"de": message_id, "juiz": model, "provider": provider}})
    yield {"fim": msg.to_dict()}

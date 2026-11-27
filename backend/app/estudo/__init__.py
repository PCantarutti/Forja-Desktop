"""Estudos (versão 2): tudo nasce de um CORPUS de fontes.

Uma matéria é uma Conversation(kind="estudo"). O estado mora em DATA_DIR/estudo/<conv>/estado.json e o texto
integral de cada fonte (página lida, arquivo enviado, edital) em fontes/<id>.txt. O texto é picado em trechos
numerados ("F3·12"): o material cita trechos, a prova só nasce de trechos e o código confere a citação.

Fluxo: pedido/edital → programa (tópicos) → pesquisa por tópico (corpus) → material por tópico → prova ancorada →
correção → desempenho, revisão e tutor, todos do corpus.
"""

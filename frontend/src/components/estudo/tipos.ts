// Tipos da tela Estudos (v2): o GET /api/estudo/<conv> inteiro.

export type Topico = { id: string; titulo: string; materia: string; subtopicos: string[]; peso: number };

export type Fonte = {
  id: string; tipo: "web" | "arquivo" | "texto" | "edital"; titulo: string; url: string; arquivo: string;
  topicos: string[]; chars: number; trechos: number; status: "util" | "descartada"; motivo: string; criada: number;
};

export type Secao = { md: string; stats: { blocos: number; citados: number; complemento: number; removidas: number };
                      trechos: string[]; gerado: number };

export type Questao = {
  id: string; tipo: "multipla" | "certo_errado" | "discursiva"; topico: string; enunciado: string; alternativas: string[];
  correta?: string; resposta_esperada?: string; explicacao?: string; citacao?: string; fonte?: string; fonte_titulo?: string;
  // questão de simulado real: número e página no caderno, se depende de figura (o desenho só está no PDF)
  numero?: number; pagina?: number; simulado?: string; figura?: boolean; area?: string; assunto?: string;
};

export type Resultado = { resposta: string; certa: boolean; nota?: number; comentario?: string };
export type Tentativa = { quando: number; segundos: number; resultado: Record<string, Resultado>; acertos: number; total: number };

export type Prova = {
  id: string; criada: number; config: { n: number; tipo: string; dificuldade: string; topicos: string[] };
  questoes: Questao[]; descartadas: number; motivos: Record<string, number>; tentativas: Tentativa[];
  titulo?: string; simulado?: string;
};

export type Simulado = {
  id: string; titulo: string; origem: string; criado: number; status: "lendo" | "pronto" | "erro"; aviso: string;
  questoes: number; com_gabarito: number; gabarito: string; prova: string;
  bloco: { id: string; rotulo: string; motivo: string; opcoes: { id: string; rotulo: string }[] } | null;
};

export type Assunto = { assunto: string; area: string; topico: string; questoes: number; simulados: number; fracao: number };

export type Cartao = { id: string; frente: string; verso: string; fonte: string; citacao: string; topico: string;
                       caixa: number; proxima: string };

export type Erro = { prova: string; questao: string; topico: string; enunciado: string; alternativas: string[];
                     resposta: string; correta: string; explicacao: string; fonte: string; citacao: string; quando: number };

export type Duvida = { pergunta: string; resposta: string; quando: number; topico: string; sem_fonte: boolean };

export type Run = {
  tipo: "programa" | "pesquisa" | "material" | "prova" | "correcao" | "flashcards" | "simulado" | "busca"; fase: string; feitos: number;
  total: number; log: string[]; aviso: string; erro: string; status: "rodando" | "pronto" | "erro" | "cancelado";
  stats: { tokens: number; gerando: number; segundos: number; estimado: boolean; chamadas: number };
  alvo?: { prova?: string };
};

export type Estudo = {
  id: number; titulo: string; pedido: string; nivel: string; edital: { chars: number; cargo: string; link: string } | null;
  programa: Topico[]; fontes: Fonte[]; material: Record<string, Secao>; provas: Prova[]; flashcards: Cartao[];
  duvidas: Duvida[]; caderno: Erro[]; run: Run | null; simulados: Simulado[]; ranking: Assunto[];
  desempenho: { topicos: { id: string; titulo: string; acertos: number; total: number; acerto: number }[];
                geral: number | null; respondidas: number; fracos: string[] };
};

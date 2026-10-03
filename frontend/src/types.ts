export type ToolCall = { id: string; name: string; arguments: Record<string, unknown> };

/** Página trazida por web_search/fetch_url: vem em `meta.sources` do resultado da ferramenta. */
export type Source = { url: string; titulo: string; dominio: string; trecho?: string };

export type Preview = { kind: "diff" | "new" | "command"; path: string; text: string };

/** Aprovação pendente: preview é null para ferramentas sem preview (ex.: MCP); sent = decisão já enviada. */
/** Uma pergunta do ask_user: o agente manda até 4 de uma vez. */
export type AskQuestion = {
  header?: string;
  question: string;
  options: { label: string; description?: string }[];
  multi_select?: boolean;
};

export type Approval = {
  preview: Preview | null;
  suggest?: string;
  nota?: string | null; // por que pediu: revisor automático ou hook do projeto
  tool?: string;
  plan?: string; // exit_plan_mode
  questions?: AskQuestion[]; // ask_user
  sent?: boolean;
};

/** O que está rodando agora: turno do agente e delegações por conversa, e processos vivos. */
export type Activity = {
  conversations: { id: number; running: boolean; subagents: number; servers: number; waiting?: number; paused?: boolean; alertas?: number; kind?: string;
    run?: string }[];  // id do turno mais recente (rodando ou recém-terminado)
  servers: number;
  local?: boolean;  // modelo local carregado no llama-server
  local_alias?: string;  // qual: o seletor com "IA local" segue o que está carregado
  lista?: string;  // carimbo das conversas: mudou (outro aparelho), a barra lateral recarrega
  board?: string;  // carimbo do board de issues: card mudou ou varredura começou/terminou
  autonomo?: number[];  // conversas com trabalho autônomo ligado (PC e celular acompanham)
};

export type Attachment = { path: string; name: string; size: number; mime: string; kind: "image" | "text" | "file" | "video";
  fps?: number; quadros?: number }; // só em vídeo gerado (video_generate): o player conta quadros com isso

export type Message = {
  id: number;
  role: "user" | "assistant" | "tool" | "event";
  content: string;
  thinking: string;
  tool_calls: ToolCall[] | null;
  tool_call_id: string | null;
  name: string | null;
  status: "ok" | "erro" | "rejeitada" | "cancelada" | "running" | "pronto" | "cancelado" | "interrompido" | null;
  meta: Record<string, any> | null;
  created_at?: string | null;
};

export type Conversation = {
  id: number;
  title: string;
  updated_at: string;
  kind?: "chat" | "agent" | "maestro" | "imagem" | "video" | "comparar" | "pesquisa" | "design" | "estudos" | "tts";
  workspace?: string | null;
  workspace_label?: string;
  pinned?: boolean;
  archived?: boolean;
  snippet?: string; // trecho que casou na busca por conteúdo
  origem?: { conv_id: number; message_id: number } | null; // Imagens aberta pela IA (skill gerar-imagens)
};

/** Arquivo alterado pelo agente nesta conversa (checkpoints), com diff do antes para o agora. */
export type ChangeFile = { path: string; status: "created" | "modified" | "deleted" | "unchanged"; diff: string;
  additions: number; deletions: number;
  /** Nada legível para comparar (imagem, zip, PDF escaneado). Documento de escritório NÃO é binário aqui:
   *  o diff é do texto extraído dele. */
  binary?: boolean };

export type GitStatus = {
  repo: boolean;
  branch?: string;
  files?: { status: string; path: string; staged: boolean }[];
  ahead?: number | null;
  behind?: number | null;
  remote?: string;
  has_gh?: boolean;
  last_commit?: string;
};

export type Task = { text: string; status: "pending" | "doing" | "done" };

export type Skill = { name: string; kind: "action" | "prompt"; description: string; action?: string; prompt?: string; source?: string };

export type ToolsSent = {
  mode: "chat" | "agent";
  provider: string;
  model: string;
  tool_mode: string;
  via: "native" | "prompt" | "none";
  num_ctx: number | null;
  tools: { name: string; mutating: boolean }[];
  permission?: string;
  permission_label?: string;
  effort?: string;
  max_iterations?: number;
  /** Capacidades efetivas do modelo nesta requisição (ex.: "vision") e de onde veio a informação. */
  capabilities?: string[];
  capabilities_detected?: string[] | null;
  vision_source?: "detectado" | "override" | "desconhecido";
  /** Ferramentas ligadas mas não enviadas porque o modelo não tem a capacidade exigida. */
  blocked?: { name: string; missing: string[] }[];
  /** Máquina e shell onde run_command/serve_* executam nesta requisição. */
  environment?: string;
};

/** Servidor iniciado pelo agente com serve_start. No Desktop roda sempre na sua máquina. */
/** Cota consumida num provedor do Ollama Cloud (GET /api/usage). */
export type CloudUsage = {
  provider: string;
  name: string;
  limits: { name: string; usage: number }[];
  models: { name: string; request_count?: number }[];
};

/** Delegação em andamento, em qualquer conversa (aba Instâncias). */
export type SubagentActive = {
  id: string;
  conversation_id: number;
  conversation?: string;
  run_id: string;
  task: string;
  status: string;
  seconds: number;
  level: string;
  model: string;
  provider: string;
  iterations: number;
  tokens: number;
  steps: number;
};

export type ServerInfo = {
  name: string;
  pid?: number;
  alive: boolean;
  exit_code?: number | null;
  command: string;
  cwd?: string;
  log?: string;
  uptime?: number;
  conv?: string; // conversa que subiu o processo, para a aba separar por conversa
  url?: string; // endereço que o servidor anunciou no log (vazio até anunciar)
  where: "local" | "host" | "container"; // no Desktop é sempre "local": tudo roda na sua máquina
  error?: string;
};


export type BrowserTab = { index: number; url: string; title: string; active: boolean };

/** Estado da sessão do navegador de uma conversa (`key` = id da conversa, "0" = rascunho). */
export type BrowserState = {
  key?: string;
  open: boolean;
  url: string;
  title: string;
  width: number;
  height: number;
  scale?: number;
  tabs?: BrowserTab[];
  file_chooser?: boolean;
  native?: boolean; // Forja Desktop: as abas são views nativas na janela; não há frames no stream
};

export type Settings = {
  provider: string;
  model: string;
  permission: "auto" | "manual" | "edits" | "plan" | "bypass";
  effort: "baixo" | "medio" | "alto" | "maximo" | "extremo";
};

export type Stats = {
  model: string;
  prompt_tokens: number;
  tokens: number;
  estimated: boolean;
  seconds: number;
  tps: number | null;
  ctx_max: number | null;
  cached?: number | null;  // tokens do prompt que vieram do cache do servidor (llama.cpp: cache_n)
  ttft?: number | null;  // segundos até o 1º token (processar o prompt + fila/rede)
  // Processamento do prompt desta chamada: tokens processados agora, em quanto tempo e a que velocidade
  prompt_proc?: { tokens: number; seconds: number; tps: number | null; aproximado: boolean } | null;
  compartilhado?: { slots: number; total: number; usado: number } | null;  // cache KV unificado entre slots
  partes?: { sistema: number; ferramentas: number; mensagens: number };  // estimativa por tipo
};

// ------------------------------------------------------------------ comparar modelos

/** O que a tela manda para o backend: um modelo de provedor, ou um arquivo .gguf. */
export type CompararEntrada = { provider?: string; model?: string; path?: string; nome: string };

export type CompararItem = {
  id: string;
  rotulo: string; // A, B, C… é o que aparece no modo cego
  provider: string;
  model: string;
  path: string; // vazio = modelo de provedor
  nome: string;
  status: "pendente" | "carregando" | "rodando" | "pronto" | "erro" | "cancelado";
  content: string;
  reasoning: string;
  stats: Stats | null;
  error: string;
};

export type CompararEstado = {
  message_id: number;
  status: "rodando" | "pronto" | "erro" | "cancelado";
  modo: "paralelo" | "sequencial";
  cego: boolean;
  revelado: boolean;
  voto: string; // id do item vencedor
  itens: CompararItem[];
};

export type PlacarLinha = { nome: string; rodadas: number; vitorias: number; erros: number; tps: number | null };

// ------------------------------------------------------------------ pesquisa profunda

export type PesquisaFonte = {
  id: string;
  rodada: number;
  url: string;
  titulo: string;
  dominio: string;
  status: "fila" | "lendo" | "util" | "vazia" | "erro";
  erro: string;
  resumo: string;
  trecho: string;
};

export type PesquisaEstado = {
  message_id: number;
  pergunta: string;
  profundidade: PesquisaProfundidade;
  status: "rodando" | "pronto" | "erro" | "cancelado";
  fase: "planejando" | "buscando" | "lendo" | "escrevendo" | "pronto";
  contexto: string;
  plano: { perguntas: string[]; buscas: string[] };
  rodada: number;
  rodadas: { n: number; buscas: string[] }[];
  fontes: PesquisaFonte[];
  resumo: string;   // primeiro parágrafo do relatório: é o que a aba mostra
  aviso: string;
  relatorio: string;  // markdown completo; a aba não renderiza, o HTML abre fora
  formato: PesquisaFormato;
  formato_usado: string;  // o que o "auto" decidiu; vazio enquanto não decidiu
  teto_segundos: number;  // tempo máximo desta corrida
  rodadas_total: number;  // quantas rodadas esta corrida pode fazer
  stats: {
    fontes: number; uteis: number; segundos: number; rodadas: number;
    tokens: number; tokens_entrada: number; gerando: number; chamadas: number; estimado: boolean;
    extrator: string; escritor: string; extrator_provider: string; escritor_provider: string;
  };
};

export type PesquisaFormato = "auto" | "produto" | "comparar" | "guia" | "checagem";
export type PesquisaProfundidade = "rapida" | "normal" | "funda" | "personalizado";

// ------------------------------------------------------------------ estudos

export type EstudosPreferencias = {
  nivel: "iniciante" | "intermediario" | "avancado";
  objetivo: "vestibular" | "concurso" | "faculdade" | "entender";
  tom: "direto" | "didatico" | "formal";
  tamanho: "curto" | "medio" | "completo";
  extras: ("exemplos" | "mnemonicos" | "pegadinhas" | "quadro")[];
  observacoes: string;
};

export type EstudosMaterial = {
  id: number; n: number; nome: string; arquivo: string; chars: number; paginas: number; ocr: boolean;
  uso: "conteudo" | "prova";   // prova = simulado: vira o perfil do que cai, não conteúdo do resumo
  figuras?: number | null; gabarito?: boolean;     // recortadas do PDF (null = ainda não procurou; material de OCR não tem)
  materia?: string;            // id da matéria; "" = Geral (serve para todas)
};

/** Matéria do objetivo (a conversa é o objetivo: um concurso, o ENEM). acerto = % das entregas corrigidas. */
export type EstudosMateria = { id: string; nome: string; peso?: number; topicos?: string[];   // tópicos: do edital
  acerto: number | null; entregas: number };

/** Ler o edital (execução "edital"): a proposta de matérias; nada muda até aplicar. existe = id da matéria de mesmo nome. */
export type EstudosEditalItem = { nome: string; peso: number; questoes: number | null; topicos: string[]; existe: string | null };
export type EstudosEdital = {
  message_id: number; tipo: "edital"; status: EstudosEstado["status"]; etapa: string; progresso: string; aviso: string;
  cargo: string; pedacos: number; proposta: EstudosEditalItem[]; stats: PesquisaEstado["stats"];
  link?: string; anexos?: { url: string; nome: string; chars?: number; erro?: string }[];   // lido por link
  data_prova?: string;   // AAAA-MM-DD, se o edital (ou a página do concurso) disse
};

/** O "Tudo" do objetivo (GET /estudos/<conv>/visao). quadro = a "Revisão rápida" do último resumo da matéria. */
export type EstudosVisaoMateria = EstudosMateria & {
  peso: number; resumos: { message_id: number; titulo: string }[]; provas: number; topicos: number; secoes: string[]; fracos: string[];
  erros: number; cartoes: number; vencem: number; dominados: number; quadro: string;
};
export type EstudosVisao = {
  materias: EstudosVisaoMateria[]; fraca: string | null; acerto: number | null; entregas: number; vencem: number;
  plano: EstudosPainel["plano"];
};

/** Figura do PDF que uma questão usa: a imagem sai de /api/estudos-figura/<conv>/<material>/<id>. */
export type EstudosFigura = { material: number; id: string; pagina: number; descricao?: string };

export type EstudosTopico = { titulo: string; objetivo: string; pontos: string[]; status: "fila" | "escrevendo" | "pronto" | "erro" };

export type EstudosEstado = {
  message_id: number;
  tema: string;
  preferencias: EstudosPreferencias;
  web: boolean;
  profundidade: "rapida" | "normal" | "funda";
  motor: "forja" | "claude";   // claude = o pedido fica para o Claude via MCP
  status: "rodando" | "aguardando" | "pronto" | "erro" | "cancelado";
  etapa: "material" | "web" | "plano" | "escrita" | "pronto";
  aviso: string;
  titulo: string;
  perfil: { banca?: string; formato?: string; estilo?: string; topicos?: string[]; questoes?: number };
  materiais: { id: number; nome: string; uso: string; pedacos: number; feitos: number }[];
  rodada: number;
  fontes: PesquisaFonte[];
  topicos: EstudosTopico[];
  texto: string;   // o resumo em Markdown (cresce seção a seção)
  stats: PesquisaEstado["stats"];
};

/** O piloto automático do objetivo. feitos: tarefa do cronograma → o resumo e a prova que ele gerou. */
export type EstudosPiloto = {
  ativo: boolean; ate: string; fase: string; aviso: string; questoes: number; total: number; prontos: number;
  atual: { etapa: string; tarefa?: string; topico?: string; mid?: number } | null;
  feitos: Record<string, { resumo?: number; prova?: number; erro?: string }>;
};

export type EstudosProjeto = {
  id: number;
  titulo: string;
  materias: EstudosMateria[];
  materia: string | null;   // a matéria desta leitura (null = "Tudo")
  materiais: EstudosMaterial[];
  resumos: { message_id: number; titulo: string; tema: string; status: string; criado: string }[];
  resumo: EstudosEstado | null;
  rodando: number | null;
  provas: EstudosProvaResumo[];
  topicos: string[];   // os que a prova pode cobrar (do último resumo com texto)
  duvidas: Record<string, number>;   // perguntas por conversa: "geral" e "questao:<entrega>:<id>"
  revisao: EstudosPainel;
  figuras: { detectadas: number; uteis: number; olhadas: number };
  simulados: EstudosSimuladoResumo[];
  ranking: EstudosRanking | null;
  busca: EstudosBusca | null;
  edital?: EstudosEdital | null;   // a última leitura de edital do objetivo
  piloto?: EstudosPiloto;
};

/** Uma questão real do simulado, conferida: a letra da IA (às cegas) contra a oficial. */
export type EstudosQuestaoReal = {
  numero: number; pagina: number; area: string; assunto: string; oficial: string; ia: string; certa: boolean | null;
  conta: string; motivo: string; inicio: string;
  figura: "" | "vista" | "faltou";   // dependia de figura: o modelo viu, ou não tinha como ver
};
type Parte = { resolvidas: number; acertos: number };
export type EstudosPlacarSimulado = {
  questoes: number; com_gabarito: number; resolvidas: number; acertos: number; em_branco: number;
  so_texto: Parte; figura_vista: Parte; figura_faltou: Parte;
  por_area: { area: string; total: number; acertos: number }[];
};
export type EstudosRanking = {
  itens: { assunto: string; area: string; questoes: number; simulados: number; fracao: number }[];
  simulados: number; questoes: number;
};
export type EstudosSimuladoResumo = {
  message_id: number; material_id: number; material: string; status: string; etapa: string;
  placar: Partial<EstudosPlacarSimulado>; gabarito: string; prova_id: number | null; criado: string;
};
/** O bloco do gabarito usado (gabarito com vários cargos/versões): as opções vêm com quantas a IA bateu. */
export type EstudosBlocoGabarito = {
  id: string; rotulo: string; motivo: string; total: number;
  opcoes: { id: string; rotulo: string; cargo: string; prova: string; iguais: number; de: number }[];
};
export type EstudosSimulado = EstudosSimuladoResumo & {
  tipo: "simulado"; titulo: string; aviso: string; progresso: string; questoes: EstudosQuestaoReal[]; bloco?: EstudosBlocoGabarito | null;
  ranking: EstudosRanking | null; stats: PesquisaEstado["stats"];
};
export type EstudosCandidato = {
  url: string; titulo: string; trecho: string; tipo: "prova" | "gabarito"; exame: string;
  status: "fila" | "baixando" | "anexado" | "rejeitado"; motivo: string; material_id?: number; paginas?: number;
};
export type EstudosBusca = {
  message_id: number; tipo: "busca"; titulo: string; pedido: string; status: string; etapa: string; aviso: string;
  progresso: string; buscas: { busca: string; achados: number; erro?: string }[]; candidatos: EstudosCandidato[];
  anexados: { material_id: number; nome: string; tipo: string; url: string }[]; stats: PesquisaEstado["stats"];
};

/** Estado de revisão espaçada de um item (Leitner: caixa 1 a 5; acertou na 5, dominado). */
export type EstudosLeitner = { caixa: number; proxima: string; acertos: number; erros: number; dominada: boolean; vence: boolean };
export type EstudosItemRevisao = EstudosLeitner & { chave: string; topico: string } & (
  | { tipo: "erro"; questao: EstudosQuestao; resposta: number | boolean | string | null; prova: string; tentativa_id: number }
  | { tipo: "cartao"; id: string; frente: string; verso: string; origem: "resumo" | "erro" });

export type EstudosTarefa = { id: string; tipo: "estudar" | "revisar" | "simulado"; texto: string; topico: string; minutos: number; feito: boolean };
export type EstudosPlano = { data: string; minutos: number; criado: string; dias: { dia: string; tarefas: EstudosTarefa[] }[] };

export type EstudosPainel = {
  hoje: string;
  itens: EstudosItemRevisao[];
  vencem: number;
  plano: EstudosPlano | null;
  geracoes: { message_id: number; status: string; n: number; motor: "forja" | "claude"; aviso: string; criado: string }[];
};

export type EstudosFlashcards = {
  message_id: number;
  tipo: "flashcards";
  quantos: number;
  motor: "forja" | "claude";
  status: EstudosEstado["status"];
  aviso: string;
  partes: { id: string; topicos: string[]; n: number; status: "fila" | "gerando" | "ok" | "erro" }[];
  cartoes: { id: string; frente: string; verso: string; topico: string }[];
  stats: PesquisaEstado["stats"];
};

export type EstudosDesempenho = {
  entregas: { message_id: number; prova_id: number; titulo: string; nota: number; acertos: number; n: number;
              modo: "prova" | "treino"; segundos: number; criado: string }[];
  topicos: { topico: string; pontos: number; max: number; pct: number | null; ultima: number | null }[];
  fracos: string[];
  lembrete: boolean;   // há celular pareado para o aviso do cronograma (no Forja web, nunca)
  revisao: { erros: number; cartoes: number; vencem: number; dominados: number };
  plano: EstudosPlano | null;
};

export type EstudosDuvidaMsg = {
  id: number;
  role: "user" | "assistant";
  texto: string;
  status: "rodando" | "aguardando" | "pronto" | "erro" | "cancelado";
  trecho: string;   // trecho do resumo marcado ("explique de outro jeito")
  motor: string;
  aviso: string;
  modelo: string;
  criado: string;
};

export type EstudosTipoQuestao = "me" | "vf" | "disc";

export type EstudosQuestao = {
  id: string;
  tipo: EstudosTipoQuestao;
  enunciado: string;
  pontos: number;
  topico: string;
  dificuldade: "facil" | "media" | "dificil";
  alternativas?: string[];
  // o resto só vem depois da primeira entrega (a prova sai sem gabarito até lá)
  correta?: number | boolean;
  explicacao?: string;
  por_alternativa?: string[];
  resposta_modelo?: string;
  rubrica?: { criterio: string; pontos: number }[];
  pagina?: string;
  verificada?: boolean;
  figura?: EstudosFigura;
};

export type EstudosProvaConfig = {
  me: number; vf: number; disc: number;
  dificuldade: "facil" | "media" | "dificil" | "mista";
  topicos: string[];
  estilo: boolean;     // imitar a prova anexada
  tempo: number;       // minutos; 0 = sem cronômetro
  instrucoes: string;
  alternativas: number;
  figuras: number;     // quantas usam uma figura do PDF (precisa de modelo que enxerga)
  geral?: boolean;     // simulado geral do objetivo: questões de todas as matérias
  distribuicao?: "peso" | "fracos";   // pelo peso de cada matéria, ou o peso vezes o que falta acertar
};

export type EstudosPlanejada = {
  id: string; tipo: EstudosTipoQuestao; topico: string; dificuldade: string; motivo: string;
  status: "fila" | "gerando" | "verificando" | "ok" | "descartada";
  figura?: EstudosFigura;
};

export type EstudosProva = {
  message_id: number;
  tipo: "prova";
  titulo: string;
  config: EstudosProvaConfig;
  motor: "forja" | "claude";
  status: EstudosEstado["status"];
  etapa: string;
  aviso: string;
  planejadas: EstudosPlanejada[];
  questoes: EstudosQuestao[];
  revelada: boolean;
  figuras_olhadas?: string;   // "12 de 80", enquanto a etapa "figuras" olha os recortes
  stats: PesquisaEstado["stats"];
};

export type EstudosCorrecao = {
  resposta: number | boolean | string | null;
  certa: boolean | null;   // null = discursiva com nota parcial (ou ainda sendo corrigida)
  pontos: number;
  max: number;
  feedback: string;
  pendente?: boolean;
  criterios?: { criterio: string; pontos: number; max: number }[];
};

export type EstudosTentativa = {
  message_id: number;
  tipo: "tentativa";
  prova_id: number;
  titulo: string;
  segundos: number;
  modo?: "prova" | "treino";
  correcao: Record<string, EstudosCorrecao>;
  motor: "forja" | "claude";
  status: EstudosEstado["status"];
  etapa: string;
  aviso: string;
  pontos: number; max: number; nota: number; acertos: number;
  por_topico: { topico: string; pontos: number; max: number }[];
  por_materia?: { materia: string; pontos: number; max: number; acertos: number; n: number }[];   // simulado geral
  questoes: EstudosQuestao[];   // as da prova, já com gabarito
  stats: PesquisaEstado["stats"];
};

export type EstudosProvaResumo = {
  message_id: number; titulo: string; status: string; n: number; config: EstudosProvaConfig;
  motor: "forja" | "claude"; criado: string;
  tentativas: { message_id: number; status: string; nota: number; pontos: number; max: number; acertos: number;
                segundos: number; criado: string; modo?: "prova" | "treino" }[];
};

// ------------------------------------------------------------------ IA local (llama.cpp / sd.cpp)

/** Parâmetros de carga do llama-server. Zero/padrão = deixa o llama.cpp decidir. */
export type LlamaParams = {
  ctx: number;
  ngl: number;
  threads: number;
  batch: number;
  ubatch: number;
  parallel: number;
  flash_attn: boolean;
  cache_type_k: string;
  cache_type_v: string;
  kv_unified: boolean;
  swa_full?: boolean;
  no_kv_offload: boolean;
  mlock: boolean;
  mmap: boolean;
  seed: number;
  rope_freq_base: number;
  rope_freq_scale: number;
  ctx_checkpoints: number;
  n_cpu_moe: number;
  n_expert: number;
  mmproj: string;
  fit: boolean; // deixa o llama.cpp ajustar o que não couber
  spec_type?: string; // geração especulativa: "" desligada | draft-mtp | ngram-mod | draft-simple ...
  spec_draft_n_max?: number; // tokens por rascunho (0 = padrão do llama.cpp)
  spec_draft_model?: string; // GGUF rascunho (draft-simple/eagle3)
  spec_draft_ngl?: number; // camadas do rascunho na GPU (-1 = padrão)
  extra_args?: Record<string, string>; // qualquer outra opção do llama-server: {"--flag": "valor" | ""}
};

/** Uma opção do --help do llama-server (lista "todas as opções" do painel). */
export type OpcaoLlama = { flag: string; nomes: string[]; arg: string; descricao: string; controlada: boolean; env?: string };

export type LocalModel = {
  path: string;
  name: string;
  size: number;
  mtime: number;
  shards: number; // > 1 = modelo dividido em vários arquivos
  folder: string;
  kind: "chat" | "image" | "video";
  params?: ImageParams; // só nos modelos de imagem e vídeo: ajustes próprios daquele modelo
  variante?: string; // só nos de vídeo: qual Wan é (chave de REQUISITOS no backend)
  chave?: string; // caminho normalizado, o mesmo das medições de tempo
  dim?: number; // dimensão do modelo de vídeo (a LoRA precisa ter a mesma)
  req?: ImageReq | null; // GGUF só-unet (Qwen-Image, Flux): arquivos que ele precisa à parte
  falta?: string[]; // chaves de `req.precisa` sem arquivo configurado
  falta_edicao?: string[]; // idem, contando o que a edição (-r) pede a mais
  previa_auto?: "proj" | "tae" | "vae"; // o modo que a prévia automática usa neste modelo
};

/** Ajustes que um modelo de imagem pode ter por conta própria. */
export type ImageParams = {
  steps: number;
  cfg: number;
  width: number;
  height: number;
  sampler: string;
  negative: string;
  vae: string;
  clip_l: string;
  t5xxl: string;
  llm: string;
  llm_vision: string;
  offload: boolean;
  flash_attn: boolean;
  vae_tiling: boolean;
  te_cpu: "" | "gerar" | "editar" | "sempre";
  preview: "" | "none" | "proj" | "tae" | "vae";  // prévia no card enquanto gera ("" = automática)
  taesd: string;
  // vídeo (Wan)
  frames: number; // 4k+1
  fps: number;
  flow_shift: number; // 0 = automático
  clip_vision: string;
  high_noise_model: string; // Wan2.2 A14B: o par HighNoise
  high_noise_steps: number; // -1 = automático
  high_noise_cfg: number; // 0 = o mesmo CFG
  variante: string; // "" = pelo nome do arquivo
  loras: { path: string; peso: number }[]; // LoRAs aplicadas (vídeo)
};

/** Metadados lidos do cabeçalho do .gguf. */
export type ModelInfo = {
  arch: string;
  mtp?: boolean; // o GGUF traz as camadas de MTP (nextn): serve para a geração especulativa draft-mtp
  n_layer: number;
  n_head_kv: number;
  head_dim: number;
  ctx_train: number; // contexto máximo treinado
  n_expert: number;
  n_expert_used: number;
  attn_interval: number; // 1 em cada N camadas tem atenção (modelos híbridos)
  size: number;
};

/** Estimativa de memória: o que vai para a VRAM e o total com a RAM. */
export type MemoryEstimate = {
  ok: boolean;
  gpu: number;
  total: number;
  weights_gpu: number;
  weights_cpu: number;
  kv: number;
  kv_gpu: number;
  recurrent: number;
  compute_gpu: number;
  layers_gpu: number;
  n_layer: number;
  ctx_train: number;
  attn_layers: number;
};

/** Amostragem de um modelo (tela Inferência). Vale para qualquer provedor: fica em model_settings. */
export type Inference = {
  temperature: number;
  top_k: number;
  top_p: number;
  min_p: number;
  repeat_penalty: number;
  max_tokens: number; // 0 = sem limite
  stop: string[];
  think: boolean; // enable_thinking do template
  reasoning_budget: number; // -1 = sem teto
};

/** Resposta de GET /local/inference: serve para modelo local e para modelo de qualquer provedor. */
export type InferenceView = {
  model: string;
  inference: Inference;
  inference_defaults: Inference;
  inference_overrides: (keyof Inference)[];
};

/** Resposta de POST /local/model: padrões, valores atuais, o que saiu do padrão e a estimativa. */
export type ModelView = {
  path: string;
  info: ModelInfo;
  defaults: LlamaParams;
  params: LlamaParams;
  presets: { ativo: string; lista: Record<string, Partial<LlamaParams>> };
  overrides: (keyof LlamaParams)[];
  estimate: MemoryEstimate;
  model: string; // id do modelo no chat (alias do llama-server)
  inference: Inference;
  inference_defaults: Inference;
  inference_overrides: (keyof Inference)[];
};

/** Download ou geração em andamento (barra de progresso no painel). */
export type Job = {
  id: string;
  kind: "runtime" | "modelo" | "imagem" | string;
  name: string;
  done: number;
  total: number;
  status: "running" | "pronto" | "erro" | "cancelado" | string;
  error: string;
  detail: string;
  result: string | null;
};

export type ImageReq = {
  nome: string;
  doc: string;
  precisa: Record<string, [string, string]>; // chave -> [o que baixar, link]
  edita?: Record<string, [string, string]>; // só nos que editam imagem: o que a edição pede a mais
  sugere: Partial<ImageParams>;
  video?: boolean;
  modos?: ModoVideo[]; // só nos de vídeo: o que a variante sabe fazer
  multiplo?: number; // largura e altura precisam ser múltiplos disto
  resolucoes?: Record<string, [number, number]>; // de treino, na horizontal ("480p": [832, 480])
  quadros_treino?: number; // o clipe mais longo do treino
};

/** Texto → vídeo, imagem → vídeo, primeiro e último quadro (pelo número de quadros dados: 0, 1, 2). */
export type ModoVideo = "t2v" | "i2v" | "flf2v";

/** LoRA nas pastas: `dim` casa com o `dim` do modelo; `passos` > 0 = destilada (acelerador). */
export type LoraArquivo = LocalModel & { wan: boolean; dim: number; rank: number; ruido: "" | "high" | "low"; passos: number };

/** Kit de download de um Wan: o modelo e as peças que a variante pede, com o que já está no disco. */
export type VideoKit = {
  auto?: boolean; // montado sozinho da busca do Hugging Face (não é da lista curada)
  repo?: string;
  id: string;
  nome: string;
  resumo: string;
  variante: string;
  modos: ModoVideo[];
  arquivos: { repo: string; path: string; gb: number; papel: string; presente: boolean; quant?: string }[];
  quant: string; // a quantização do modelo neste kit (a maior que cabe, a do disco ou a escolhida)
  opcoes: { quant: string; gb: number; cabe: boolean | null; presente?: boolean }[];
  erro?: string; // sem Hugging Face: não dá para saber tamanhos nem baixar
  gb_modelo: number; // o maior modelo de difusão: é o que precisa caber na VRAM
  gb_total: number;
  gb_falta: number;
};

export type ImageOpts = {
  model: string;
  formato?: string; // vídeo: "mp4-av1" | "mp4-h264" | "webm-vp9" (gerados e ampliados)
  out_dir: string; // vazio = %APPDATA%/Forja/imagens
  vae: string;
  clip_l: string;
  t5xxl: string;
  llm: string;
  llm_vision: string;
  offload: boolean;
  flash_attn: boolean;
  vae_tiling: boolean;
  te_cpu: "" | "gerar" | "editar" | "sempre";
  diffusion_model: string;
  steps: number;
  cfg: number;
  width: number;
  height: number;
  sampler: string;
  negative: string;
  seed: number; // 0 = aleatória
  // alta resolução (hires fix do sd-cli): amplia e o próprio modelo redesenha por cima com o denoise
  hires?: boolean;
  hires_scale?: number;
  hires_denoise?: number;
  hires_upscaler?: string; // "Latent", "Lanczos" ou o caminho de um ESRGAN
  descarte_dias: number; // prazo das imagens reprovadas em descartadas/ (0 = guardar para sempre)
  // vídeo (Wan)
  frames: number; // 4k+1
  fps: number;
  flow_shift: number; // 0 = automático
  clip_vision: string;
  high_noise_model: string; // Wan2.2 A14B: o par HighNoise
  high_noise_steps: number; // -1 = automático
  high_noise_cfg: number; // 0 = o mesmo CFG
  variante: string; // "" = pelo nome do arquivo
  loras: { path: string; peso: number }[]; // LoRAs aplicadas (vídeo)
  ampliacao?: { origem: string; fator: number; modelo: string; suavizar: boolean; prompt?: string; forca?: number }; // tomada que é ampliação de outra (prompt/forca: redesenho)
};

/** Uma variação dentro de um lote da seção Imagens. */
export type LoteImagem = {
  path: string;
  seed: number;
  model: string;
  model_name: string;
  opts?: Partial<ImageOpts>; // acrescentada pelo Reaproveitar com os ajustes da tela: vale por cima dos do lote
  refs?: string[]; // vídeo acrescentado pelo Reaproveitar com outros quadros (outro modo): valem no lugar dos do pedido
  ampliacao?: ImageOpts["ampliacao"]; // ampliação: o método deste item (um lote pode ter vários)
  // interrompida: o app fechou no meio do lote ("Continuar" gera de novo, com a mesma semente)
  status: "pendente" | "gerando" | "pronta" | "erro" | "mantida" | "descartada" | "cancelada" | "interrompida";
  error: string;
  progress?: number; // 0..1, passo da amostragem enquanto gera
  preview?: string; // prévia do passo atual (só com o modo de prévia do modelo ligado)
  fase?: string; // ampliação SeedVR2 em andamento: "iniciando o ComfyUI", "ampliando"
  width?: number; // tamanho próprio do slot (skill gerar-imagens); nas outras vale o do lote
  height?: number;
  w?: number; // tamanho real do arquivo, quando a alta resolução mudou o do pedido
  h?: number;
  com_previa?: boolean; // o modelo gera com prévia: o card não usa o líquido, nem antes da 1ª
  s_passo?: number; // segundos por passo, lido do sd-cli
  restante?: number; // segundos até o fim da amostragem
  nome?: string; // slot da skill gerar-imagens: o arquivo mora no projeto, no caminho que o código aponta
  destino?: string; // presente = é a versão que o site mostra (o arquivo está no caminho do slot)
  slot?: string; // variação de um slot, fora do site: "Usar no site" troca com a do destino
  prompt?: string; // prompt próprio do slot/variação (o do pedido do lote vale para as demais)
  unidade?: string; // "quadro" na ampliação (s/quadro); sem ela, passo
};

/** Um slot registrado pela ferramenta imagens_pendentes (meta.imagens_pendentes do resultado). */
export type SlotImagem = { nome: string; caminho: string; rel: string; prompt: string; largura: number | null; altura: number | null };
export type SlotsPendentes = { message_id: number; slots: SlotImagem[] };

/** meta da mensagem do assistente num lote (a thread do backend vai preenchendo `images`). */
export type LoteMeta = {
  job: string;
  count: number;
  seed_mode: SeedMode;
  opts: Partial<ImageOpts>;
  images: LoteImagem[];
  variacao_de?: string; // variações de um slot: vivem no modal dele, não como lote na tela
};

/** meta da mensagem do usuário num lote. */
export type PedidoMeta = { models?: string[]; refs?: string[] };

export type SeedMode = "incremental" | "aleatoria" | "fixa";

export type RuntimeInfo = {
  installed: boolean;
  exe: string;
  backend: string; // o que está em uso
  backends: string[]; // o que dá para baixar
  available: { backend: string; exe: string; version: string }[]; // o que já está no disco
  chosen: string; // escolhido à mão em Configurações › Runtime ("" = automático)
  mb?: number; // ComfyUI: o tamanho do download (o pacote da marca da GPU)
};

/** Memória da máquina: é o que diz se um modelo cabe na GPU, na RAM, ou em lugar nenhum. */
export type Hardware = {
  gpus: { id: string; name: string; total: number; free: number; enabled: boolean }[];
  cpu: { name: string; arch: string; flags: string[]; cores: number };
  vram: number;
  vram_free: number;
  ram: number;
  ram_free: number;
};

export type LocalState = {
  aviso_padroes?: boolean;  // E4: padrões de cache novos, avisar uma vez
  runtimes: { llama: RuntimeInfo; sd: RuntimeInfo; ffmpeg: RuntimeInfo; comfy: RuntimeInfo };
  models: LocalModel[];
  server: {
    running: boolean;
    port: number;
    path?: string;
    alias?: string;
    params?: LlamaParams;
    ctx?: number | null;
    pid?: number;
    uptime?: number;
    vision?: boolean;
    /** Projetor de visão incompatível com o runtime: o encoder cai na CPU e cada print leva minutos. */
    vision_lenta?: string;
    // Carga em andamento (barra no topo da janela) e a falha da última tentativa, que fica até a próxima.
    loading?: { path: string; name: string; elapsed: number; eta: number; percent: number };
    error?: { when: number; path: string; message: string; log: string };
  };
  dirs: string[];
  download_dir: string; // para onde vão os downloads (uma das dirs)
  models_dir: string; // pasta padrão dos modelos (Configurações › Pastas)
  data_dir: string;
  image_busy: boolean; // gerando imagem: carregar modelo fica bloqueado
  hardware: Hardware;
  guardrail: "off" | "relaxado" | "rigoroso";
  autoload: boolean; // carregar o último modelo ao abrir o Forja
  hf_token: boolean; // só diz se existe; o token não volta do backend
  jobs: Job[];
  defaults: LlamaParams;
  last: string;
  image: ImageOpts;
  image_dir: string; // pasta onde as imagens do painel são salvas
  video_dir: string; // pasta dos vídeos da aba Vídeo
  image_models: LocalModel[];
  video: ImageOpts; // padrões da aba Vídeo
  video_models: LocalModel[];
  gpu_video: { nome?: string; gb?: number; folga?: number }; // a GPU que o sd.cpp usa ({} sem runtime)
  // quanto cada vídeo levou nesta máquina, por modelo e tamanho: base da estimativa
  tempos_video: { model: string; w: number; h: number; frames: number; passos: number; s_passo: number; s_total: number }[];
  loras: LoraArquivo[]; // .safetensors que são LoRA, com o que os tensores dizem deles
  ampliadores: LocalModel[]; // ESRGAN (.pth) da ampliação de vídeo
  port: number;
};

export type HfModel = {
  id: string;
  author: string;
  downloads: number;
  likes: number;
  updated: string;
  gated: boolean;
  tags: string[];
  // só na busca de vídeo
  variante?: string;
  variante_nome?: string;
  modos?: ModoVideo[];
};

/** Ficha de um repositório na janela de busca. */
export type HfRepo = {
  id: string;
  author: string;
  downloads: number;
  likes: number;
  updated: string;
  gated: boolean;
  tags: string[];
  license: string;
  params: number;
  arch: string;
  ctx_train: number;
  capabilities: { vision: boolean; tools: boolean; reasoning: boolean };
  files: HfFile[];
  readme: string;
};
export type HfFile = { path: string; size: number; quant: string; shards: number; papel?: string;
  tipo?: "esrgan" | "seedvr2" | "spandrel" | "vae"; subpasta?: string }; // tipo/subpasta: só na busca de ampliação

// ------------------------------------------------------------------ Maestro
// A Maestro planeja e verifica; os Workers implementam. O estado real vive no SQLite do backend
// (taskdb) — a árvore aqui é leitura, vinda de /api/maestro/{conv}/board.

/** Tipo da tarefa: o Worker sabe que mudança é, o roteador sabe que especialista chamar. */
export type TipoTarefa = "feature" | "bugfix" | "refactor" | "test" | "ui" | "docs" | "chore";
export const TIPOS_TAREFA: [TipoTarefa, string][] = [
  ["feature", "Funcionalidade"], ["bugfix", "Correção"], ["refactor", "Refatoração"], ["test", "Testes"],
  ["ui", "Tela / visual"], ["docs", "Documentação"], ["chore", "Manutenção"],
];

/** Worker especialista (Configurações › Maestro): o id é o que vai em model_slot. */
export type Especialidade = { id: string; nome: string; quando: string; provider: string; model: string };

export type Contract = {
  type?: TipoTarefa;
  context?: string;
  goal: string;
  relevant_files?: string[];
  requirements?: string[];
  constraints?: string[];
  do_not?: string[];
  acceptance_criteria?: string[];
  verify_command?: string;
  expected_result?: string;
};

export type TaskStatus =
  | "pending" | "queued" | "loading_model" | "implementing" | "testing" | "reviewing"
  | "completed" | "failed" | "blocked" | "needs_human" | "cancelled";

/** Protocolo Worker → Maestro. `changes` e `tests` são medição (git + comando), não autoavaliação. */
export type TaskResult = {
  type: "task_result";
  task_code: string;
  attempt: number;
  status: "completed" | "failed" | "unverified" | "error" | "cancelled";
  changes: { path: string; status: string; additions: number | null; deletions: number | null }[];
  commands: { command: string; status: string }[];
  tests: { command: string; status: string; output: string } | null;
  errors: string[];
  review: string | null;
  summary: string;
  model: string | null;
  level: string | null;
  agent?: string | null;
  seconds: number | null;
  tokens: number | null;
  iterations: number | null;
};

export type TaskAttempt = {
  n: number;
  status: string;
  worker: { level?: string; provider?: string; model?: string; agent?: string | null; rota?: string };
  strategy: string | null;
  error: string | null;
  seconds: number;
  tokens: number;
  result: TaskResult | null;
  started_at: string;
  finished_at: string | null;
  has_transcript?: boolean;
  transcript?: Message[];  // só no detalhe da tarefa: a conversa do Worker nesta tentativa
};

export type MaestroTask = {
  code: string;
  title: string;
  status: TaskStatus;
  feature_id: number;
  priority: number;
  depends_on: string[];
  model_slot: string | null;
  agent: string | null;
  attempt_count: number;
  max_attempts: number;
  blocked_reason: string | null;
  contract: Contract;
  result: TaskResult | null;
  updated_at: string;
  attempts?: TaskAttempt[];
};

export type MaestroFeature = {
  id: number;
  title: string;
  goal: string;
  status: "planning" | "active" | "validating" | "done" | "cancelled";
  copiada_para?: number | null;  // continua numa sessão nova (conversa); aqui fica só para consulta
  tasks: MaestroTask[];
};

export type MaestroBoard = {
  inicio?: string | null; // primeiro pedido da conversa (ISO UTC)
  ultima?: string | null; // última atividade: mensagem, tarefa ou tentativa
  features: MaestroFeature[];
  counts: Partial<Record<TaskStatus, number>>;
  total: number;
  done: number;
  open: number;
};

export type MaestroModels = {
  running: boolean;
  alias: string | null;
  ctx: number | null;
  vram: number | null;
  vram_free: number | null;
  ram: number | null;
  ram_free: number | null;
  lifecycle: string;
  max_workers: number;
  can_swap: boolean;
  manageable: boolean;
  min_ctx_worker: number;  // GGUF local com janela menor que isto não pode ser Worker
  slots: Record<string, { provider: string; model: string }>;
  especialidades?: Especialidade[];
  workers_do_maestro?: boolean;
  active: SubagentActive[];
};

/** Resposta em andamento. `tool` são os argumentos de uma tool call ainda chegando (write_file de
 *  arquivo grande leva minutos e, sem isto, a tela fica parada como se o modelo tivesse travado). */
export type Draft = {
  content: string;
  thinking: string;
  tool?: { name: string; path?: string; text: string; chars?: number } | null;
};

/** Passos de um subagente/Worker, agrupados pelo id da chamada que o criou. */
export type SubState = {
  status: string;
  steps: { call: any; result?: Message }[];
  // Worker de contrato (Maestro): a conversa dele no formato do chat, e a resposta em andamento.
  mensagens?: Message[];
  draft?: Draft | null;
};

/** Troca de modelo em andamento, para o cockpit mostrar por que a execução parou por uns minutos. */
export type ModelPhase = {
  phase: "unloading" | "loading" | "ready" | "unloaded" | "error" | "clearing" | "cleared" | "restarting";
  previous?: string;
  model?: string;
  reason?: string;
};

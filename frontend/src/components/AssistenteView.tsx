import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { LocalModel } from "../types";
import CartaoEstado, { botaoEstado, botaoEstadoPrimario } from "./CartaoEstado";
import { BarraTopo, Caixa } from "./ImagensView";
import { ChevronDown, Mic, X } from "./icons";
import { ACTION, Thinking } from "./MessageView";
import { useStickyBottom } from "../useStickyBottom";
import ReceitasTarefas from "./ReceitasTarefas";
import { Interruptor } from "./Settings";

type Estado = {
  ligado: boolean; instalado: boolean; instalando: string; estado: string; erro: string; mic: string; saida: string; mic_nome: string; ouviu: string;
  motor: "kokoro" | "tela"; voz: string; modelo_tts: string; voz_tts: string; whisper: string; humor: string;
  imagem: string; video: string; pasta_projetos: string; favoritos: Favorito[];
  modelo: { provider: string; model: string } | null; ocioso_min: number; atalho: string; raciocinio: string;
  permitidos: { provider: string; model: string }[];
  detector: "whisper" | "treinado" | "ambos"; sensibilidade: number; detector_treinado: boolean; treinando: string; minha_voz: number; gravacoes_novas: number;
  ganho: number; sensibilidade_voz: number; pausa_fim: number; idiomas_whisper: string[]; idiomas: { id: string; nome: string }[];
  metricas: { acerto: number; falsos: number; minha_voz_acerto: number | null; alarmes_fala_corrida: number; minutos_fala_corrida: number; vozes: number;
              minha_voz: number; quando: string } | null;
  vozes_kokoro: { id: string; nome: string; rvc?: boolean }[]; rvc: boolean; rvc_instalado: boolean; rvc_instalando: string; humores: { id: string; nome: string }[];
  lembretes: { id: string; quando: string; texto: string }[];
  resumo_hora: string; cidade: string; avisos_email: string[];
  pastas_documentos: string[]; notas: { id: string; texto: string; quando: string }[];
  conversa_continua: boolean; idioma: string; trocavoz_saida: string; trocavoz_retorno: boolean; trocavoz_pausa: number; trocavoz_whisper: string; trocavoz: { ligado: boolean; erro: string; saida: string };
  acoes: { id: string; t: number; tipo: string; resumo: string; desfeito: boolean; pode_desfazer: boolean }[];
  documentos: { arquivos: number; indexando: boolean; erro: string };
  acompanhar: { id: string; tipo: "preco" | "versao"; alvo: string; limite: number | null; base: string | null; ultimo: number | null; conferido: string | null }[];
};
type Memoria = { name: string; slug: string; description: string; type: string };
type GoogleConta = { client_id: string; tem_chave: boolean; conectado: boolean; email: string | null; pode_copiar_youtube: boolean };
type Mic = { id: string; nome: string; api: string; tipo: "entrada" | "saida"; padrao: boolean };
type Favorito = { nome: string; provider: string; model: string };
type Nivel = { rms: number; limiar: number; prob: number | null; det_limiar: number | null };
type Fala = {
  quem: "voce" | "forja"; texto: string; raciocinio?: string; ferramentas?: { nome: string; arg: string; resultado: string }[];
  conv?: { id: number; kind: string; titulo?: string; rodando?: boolean; fazendo?: string };
};
const TIPO_CONVERSA: Record<string, string> = { agent: "Agente", maestro: "Maestro", chat: "Chat", pesquisa: "Pesquisa", imagem: "Imagem", video: "Vídeo" };

/** A linha "Pensando…" das outras telas: o que a IA está fazendo agora. */
function Fazendo({ texto }: { texto: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-accent-text">
      <span className="size-3 shrink-0 animate-spin rounded-full border border-sky-400/30 border-t-sky-300" />
      {texto}
    </span>
  );
}

function FerramentaFalada({ f }: { f: { nome: string; arg: string; resultado: string } }) {
  const [aberto, setAberto] = useState(false);
  return (
    <div className="my-1.5 overflow-hidden rounded-xl border border-line bg-surface">
      <button onClick={() => setAberto(!aberto)} className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12.5px] hover:bg-raised/50">
        <span className="font-mono text-fg">{f.nome}</span>
        <span className="truncate text-faint">{f.arg}</span>
        <span className={`ml-auto shrink-0 text-[11.5px] ${f.resultado ? "text-muted" : "text-accent-text"}`}>● {f.resultado ? "ok" : "executando"}</span>
        <ChevronDown className={`size-3 shrink-0 text-faint transition-transform ${aberto ? "" : "-rotate-90"}`} />
      </button>
      {aberto && f.resultado && (
        <pre className="max-h-60 overflow-auto border-t border-line bg-code px-3 py-2 font-mono text-[11px] whitespace-pre-wrap text-muted">{f.resultado}</pre>
      )}
    </div>
  );
}

const ACAO_FALADA: Record<string, string> = { controlar_pc: "Mexeu no PC", tarefa: "Passou uma tarefa ao agente", lembrete: "Mexeu nos lembretes" };

/** Raciocínio e ferramentas de uma resposta num grupo fechado, como o ActivityGroup da tela de agente. */
function AtividadeFalada({ x, vivo }: { x: Fala; vivo: boolean }) {
  const [aberto, setAberto] = useState(false);
  const fs = x.ferramentas ?? [];
  const alvo = fs[0]?.nome === "web_search" ? fs[0].arg : "";
  const cabeca = (fs.length ? (ACTION[fs[0].nome] ?? ACAO_FALADA[fs[0].nome] ?? `Usou ${fs[0].nome}`) : "Raciocinou") + (alvo ? ` — ${alvo}` : "");
  const resumo = (vivo && !fs.length ? "Trabalhando" : fs.length > 1 ? `${cabeca}, usou ${fs.length} ferramentas` : cabeca) + (vivo ? "…" : "");
  return (
    <div className="my-2">
      <button onClick={() => setAberto(!aberto)} className="flex items-center gap-1.5 text-left text-[13.5px] text-faint transition-colors hover:text-muted">
        <span className={vivo ? "animate-pulse" : ""}>{resumo}</span>
        <ChevronDown className={`size-3.5 shrink-0 transition-transform duration-150 ${aberto ? "" : "-rotate-90"}`} />
      </button>
      {aberto && (
        <div className="mt-2 border-l border-line pl-3.5">
          {x.raciocinio && <Thinking text={x.raciocinio} live={vivo && !fs.length} />}
          {fs.map((f, j) => <FerramentaFalada key={j} f={f} />)}
        </div>
      )}
    </div>
  );
}

/** A conversa que o pedido abriu: não pula a tela, fica aqui um botão para ir até ela e o que ela está fazendo. */
function CartaoConversa({ c, onAbrir }: { c: NonNullable<Fala["conv"]>; onAbrir: (id: number, kind: string) => void }) {
  return (
    <button onClick={() => onAbrir(c.id, c.kind)} title="Ir para a conversa"
            className="mt-2 flex w-full max-w-[440px] items-center gap-3 rounded-xl border border-line bg-surface px-3.5 py-2.5 text-left transition-colors hover:border-accent">
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] text-fg"><span className="text-faint">{TIPO_CONVERSA[c.kind] ?? c.kind} · </span>{c.titulo}</div>
        <div className="mt-1">{c.rodando ? <Fazendo texto={c.fazendo || "Trabalhando"} /> : <span className="text-[12px] text-faint">Terminou</span>}</div>
      </div>
      <span className="shrink-0 text-[12px] text-muted">Abrir ›</span>
    </button>
  );
}

const pct = (rms: number) => Math.max(0, Math.min(100, (20 * Math.log10(Math.max(rms, 1e-4)) + 60) / 60 * 100));  // -60..0 dB

/** Nível do microfone ao vivo: a barra é o som, o traço é o limiar a partir do qual ele considera fala. Com o detector,
 *  uma segunda barra mostra a certeza de "Forja" e o traço dela, o ponto em que acende. */
function Medidor({ n }: { n: Nivel | null }) {
  if (!n) return <p className="text-[11.5px] text-faint">Ligue o assistente para ver o nível do microfone.</p>;
  const fala = n.rms > n.limiar;
  return (
    <div className="flex flex-col gap-1.5" aria-label="Nível do microfone">
      <div className="relative h-2 overflow-hidden rounded-full bg-line">
        <div className={`h-full rounded-full transition-[width] duration-100 ${fala ? "bg-ok" : "bg-muted"}`} style={{ width: `${pct(n.rms)}%` }} />
        <div className="absolute top-0 h-full w-[2px] bg-warn" style={{ left: `${pct(n.limiar)}%` }} title="Limiar de fala" />
      </div>
      {n.prob !== null && n.det_limiar !== null && (
        <div className="relative h-2 overflow-hidden rounded-full bg-line" title="Certeza do detector de que ouviu “Forja”">
          <div className={`h-full rounded-full transition-[width] duration-100 ${n.prob >= n.det_limiar ? "bg-accent" : "bg-muted"}`} style={{ width: `${n.prob * 100}%` }} />
          <div className="absolute top-0 h-full w-[2px] bg-warn" style={{ left: `${n.det_limiar * 100}%` }} />
        </div>
      )}
      <span className="text-[11px] text-faint">{fala ? "Ouvindo fala" : "Silêncio ou ruído"}{n.prob !== null ? ` · “Forja”: ${Math.round(n.prob * 100)}%` : ""}</span>
    </div>
  );
}
type Gravacao = { id: string; segundos_voz: number; pico: number; aviso: string; reconhecida: boolean | null };
type Catalogo = { id: string; name: string; models: string[] }[];
type Job = { id: string; detail?: string; error?: string; status: string };
type Tts = { modelos: { nome: string }[]; vozes: { id: string; nome: string }[] };

const campo = "w-full min-w-0 bg-transparent text-[12.5px] text-fg outline-none placeholder:text-faint";
const ESTADO: Record<string, string> = {
  ouvindo: "Ouvindo. Diga “Forja, …”", gravando: "Escutando você", transcrevendo: "Entendendo o que você disse",
  escutando: "Escutando seu pedido", entendendo: "Entendendo o que você disse",
  pensando: "Pensando na resposta", falando: "Falando", carregando: "Carregando o Whisper e a voz…", desligado: "Desligado",
};
/** Tutorial da página: TUDO o que o assistente sabe fazer. Feature nova do assistente entra aqui junto (CLAUDE.md). */
const CAPACIDADES: { titulo: string; exemplos: string[]; texto: string }[] = [
  { titulo: "Chamar", exemplos: ["Forja, …", "Ei Forja, …", "Forja (sozinho)"],
    texto: "Comece com “Forja” e fale o pedido (pode dar uma pausa depois do nome). Só o nome: toca um bip e ele ouve o próximo pedido, sem precisar repetir. Se o círculo acendeu e ele não entendeu o pedido, ele diz “Não entendi. Pode repetir?” e continua ouvindo. Chamou várias vezes enquanto ele ainda transcrevia? Vale só o último pedido: o que ficou para trás (mais de 12 s) é descartado, nada de pedido velho saindo depois. Liga e desliga no microfone do canto da janela, na bandeja do sistema ou aqui. O que ele mesmo falou e voltou pela caixa de som (sem fone) não vira pedido: frase quase igual a uma dele do último minuto é ignorada." },
  { titulo: "Perguntar", exemplos: ["Forja, quanto é 15% de 200?", "Forja, quais são as notícias de hoje?", "Forja, e em dólar?"],
    texto: "Responde só falando, sem abrir nem mexer em nada no Forja. Pesquisa na web quando precisa (“Pesquisando.”) e lembra a conversa enquanto o modelo dele estiver carregado, então dá para emendar perguntas. Na tela dele aparecem, como no Agente, o raciocínio e as buscas (clique para abrir) e a linha do que ele está fazendo agora." },
  { titulo: "Conversa só na memória", exemplos: ["Forja, salva nossa conversa num chat", "Salvar como chat", "Esquecer"],
    texto: "Nada do que vocês conversam vai para o disco nem para o log: some quando o modelo dele descarrega. Para guardar, peça para transformar a conversa num chat do Forja (ou use o botão Salvar como chat na tela dele). Esquecer apaga na hora." },
  { titulo: "Tela do assistente", exemplos: ["botão do microfone", "Como usar", "Configurações"],
    texto: "O círculo grande mostra o que ele está fazendo; o botão do microfone fala com ele na hora (como o atalho); embaixo, a transcrição do que você disse e do que ele respondeu, e um campo para escrever. As configurações ficam no painel da direita (que recolhe), em abas: Voz, Chamar, Modelos, Dados e Atividade." },
  { titulo: "Criar imagem", exemplos: ["Forja, crie uma imagem de um gato esquiando"],
    texto: "Cria uma conversa nova em Imagem (a tela não pula: aparece um cartão na conversa dele com o botão para ir até ela) e gera com o seu modelo favorito de imagem (em Comandos, abaixo)." },
  { titulo: "Gerar vídeo", exemplos: ["Forja, gere um vídeo de um cachorro surfando"],
    texto: "O mesmo na tela Vídeo, com o modelo favorito de vídeo." },
  { titulo: "Abrir um chat no Forja", exemplos: ["Forja, abra um chat no Forja sobre receitas de bolo", "Forja, inicia uma conversa nova"],
    texto: "Pergunta com qual modelo seguir, cria a conversa no Chat e já manda o assunto como primeira mensagem." },
  { titulo: "Trabalhar no modo agente ou na Maestro", exemplos: ["Forja, no modo agente crie um site de ecommerce", "Forja, crie um jogo para mim no modo agente", "Forja, pede pro agente fazer um script", "Forja, no maestro crie um sistema de estoque"],
    texto: "Se uma conversa já trabalha numa pasta com nome parecido, pergunta se continua nela ou cria uma pasta nova (em chat-agente/ ou chat-maestro/ dentro da pasta dos projetos). Depois pergunta o modelo e começa, sem trocar a tela: a conversa vira um cartão com o que ela está fazendo e o botão Abrir." },
  { titulo: "Pesquisa profunda", exemplos: ["Forja, faça uma pesquisa profunda sobre carros elétricos", "Forja, pesquise a fundo isso que conversamos"],
    texto: "Abre uma pesquisa na tela Pesquisa (a funda, com fontes). “Isso que conversamos” vira uma pergunta completa a partir da conversa falada. Quando termina, ele avisa e lê as conclusões em poucas frases. Pesquisa simples (“pesquise o valor do dólar”) ele mesmo responde falando." },
  { titulo: "Escolher o modelo falando", exemplos: ["o de sempre", "qwen 3.6", "gemma 12b", "meu favorito de código", "…usando o modelo qwen 3.6"],
    texto: "Quando ele pergunta “Com qual modelo?”: o nome do modelo, “o de sempre” (o do último envio no Forja) ou um dos seus favoritos por uso. Dizendo “usando o modelo X” no próprio pedido, ele nem pergunta. Não achou? Ele diz os modelos que você tem." },
  { titulo: "Microfone, alto-falante e voz", exemplos: ["Fone do headset", "Voz treinada", "GLaDOS em inglês", "Wheatley", "Arthur Morgan", "Voz rápida", "Voz clonada da tela Voz"],
    texto: "Escolha de qual microfone ele ouve e em qual alto-falante ele fala (o fone, por exemplo). As vozes “(treinada)” são de uma pessoa de verdade, treinadas no Piper com gravações dela: português nativo, sem sotaque, e falam na CPU frase a frase, mais rápido que a voz rápida. Ficam na pasta piper do runtime do assistente (o .onnx e o .onnx.json). Cada uma tem também a versão “GLaDOS (…, com filtro)”: a mesma voz com afinação robótica cravada em escala, entonação de máquina e o timbre metálico, ainda na CPU. Voz treinada em inglês (“… (treinada, só inglês)”, como a GLaDOS do jogo): fale com ela em qualquer idioma, ela sempre responde em inglês, e as frases prontas dele saem traduzidas (guardadas: da segunda vez é na hora). A do Wheatley tem dois estilos, calmo e gritado, e troca no meio da resposta quando ele leva um susto. A do Arthur Morgan (Red Dead Redemption 2) também é só inglês, com um estilo só, e combina com a personalidade dele; ela passa por um modelo RVC que limpa o timbre, com ~1 s a mais na primeira frase. Qualquer voz treinada ganha o RVC: coloque o seu <voz>.rvc.pth (e o .rvc.index, se tiver) ao lado do .onnx na pasta piper; embaixo da voz aparece “Instalar o RVC” (PyTorch e Applio, uma vez só: ~2 GB na CPU, ~4 GB com CUDA na NVIDIA) e, instalado, o interruptor que liga e desliga. O index_rate opcional fica no “rvc” do “forja” do .onnx.json (padrão 0,3). Microfone desconectado: ele tenta voltar sozinho e avisa aqui se não der." },
  { titulo: "Trocador de voz (Discord e chamadas)", exemplos: ["Configurações › Voz › Trocador de voz", "microfone “CABLE Output” no Discord"],
    texto: "Você fala no seu microfone e seus amigos ouvem a voz do assistente: cada frase é transcrita pelo Whisper e falada de novo na voz escolhida (Kokoro, treinada ou GLaDOS) num microfone virtual, ~1 s depois na velocidade rápida (a equilibrada entende melhor, ~2 s). Ligue o Retorno para ouvir a voz dele no seu fone. Se ele te corta no meio da frase, aumente a “Pausa que encerra a frase” (padrão 0,6 s); se demora para sair, diminua a pausa ou use a velocidade rápida. Precisa do VB-CABLE (grátis, vb-audio.com); no Discord, escolha o microfone “CABLE Output”. Com voz só em inglês, ele traduz o que você fala. Liga no interruptor da aba Voz e desliga sozinho ao fechar o Forja." },
  { titulo: "Personalidade", exemplos: ["Sarcástico", "GLaDOS", "Wheatley", "Arthur Morgan", "Leve", "Sem humor"],
    texto: "O humor só aparece onde cabe: pergunta boba sim, assunto sério não, e depois de uma piada as próximas respostas saem sem. GLaDOS e Wheatley seguem o jeito dos personagens de Portal: ela gelada e passivo-agressiva (às vezes te chama de monstro e de gorda, morre de medo de pássaros e não suporta batata), ele nervoso, tagarela, orgulhoso de coisas simples e convencido, como no jogo, de que você tem um leve dano cerebral (de vez em quando leva um susto e grita; com a voz dele, o grito sai gritado de verdade). O Arthur Morgan, de Red Dead Redemption 2, é seco e lacônico, debocha de canto de boca (plano mirabolante lembra o Dutch, preguiça lembra a lombalgia do Tio) e fica sincero quando o assunto é sério." },
  { titulo: "Atalho global", exemplos: ["Ctrl+Alt+F"],
    texto: "Defina uma combinação em Atalho global: apertou, ele ouve o pedido sem precisar dizer “Forja”, com o Forja minimizado ou atrás de outra janela. Falando, o atalho interrompe e já ouve. Desligado, ele liga e ouve. Enquanto você fala, o modelo dele já vai carregando (só se a placa estiver livre ou com um modelo parado: trabalho em andamento não é derrubado). Também na bandeja: “Chamar o assistente agora”." },
  { titulo: "Parar e cancelar", exemplos: ["Forja, para", "Forja, cancela", "Forja, silêncio"],
    texto: "Cala e cancela o que estiver fazendo. Um pedido novo também cancela o anterior. Enquanto ele fala, dizer “Forja” por cima interrompe na hora: ele cala, toca o bip e ouve o pedido novo (funciona melhor de headset; com caixa de som o eco atrapalha). Respondendo a uma pergunta dele (“Com qual modelo?”), “cancela” desiste dela." },
  { titulo: "Vê-lo ouvir e responder", exemplos: [],
    texto: "Um círculo no canto inferior direito da tela, mesmo com o Forja minimizado: com ondas saindo quando ele ouviu “Forja” (ou o atalho) e está escutando o pedido, mais apagado enquanto pensa, pulsando enquanto fala." },
  { titulo: "Modelo dele e da placa de vídeo", exemplos: [],
    texto: "Escolha o modelo do assistente em “Modelo do assistente” (vazio: o do último envio no Forja). Abrir chat ou agente com outro modelo tira o do assistente da placa e carrega o novo com o pedido; se outro trabalho estiver usando a placa, ele avisa e começa quando ele terminar. Parado pelo tempo escolhido, o modelo dele sai da placa sozinho. Pergunta falada nunca derruba trabalho do Forja; imagem ou vídeo com trabalho em andamento: ele pergunta antes." },
  { titulo: "Trocar o modelo dele falando", exemplos: ["Forja, troca seu modelo para o Qwen 3.6", "Forja, use o modelo Ornith para responder", "Forja, volta para o de sempre", "Forja, qual modelo você está usando?"],
    texto: "Muda o modelo do assistente na hora (o mesmo de Configurações › Modelos) e já começa a carregar. “O de sempre” volta a seguir o do último envio no Forja. Sem nome, ele pergunta e diz quais você tem." },
  { titulo: "Usar o modelo que já está carregado", exemplos: ["Pode usar se já estiver carregado"],
    texto: "Em Configurações › Modelos, escolha os modelos que ele pode usar: se um deles já estiver carregado e parado, ele responde com ele em vez de carregar o dele (mais rápido, nada troca na placa). Ocupado com trabalho do Forja, não pega." },
  { titulo: "Controlar o PC", exemplos: ["Forja, abre o Spotify", "Forja, fecha o Chrome", "Forja, volume 30", "Forja, aumenta o volume", "Forja, tira o som", "Forja, muda para o fone", "Forja, passa o som pra caixa", "Forja, tá baixinho, sobe aí", "Forja, trava o PC"],
    texto: "Abre qualquer programa do menu Iniciar, fecha um programa como no X (se tiver algo sem salvar, ele pergunta), muda o volume e a saída de som e trava a tela. Pode pedir do seu jeito: o que as regras não pegam, o modelo dele entende e escolhe uma ação da lista (nunca comando livre no Windows; fechar programa só com o nome dito). “Não perturbe” o Windows não deixa um programa ligar: ele abre a página das notificações." },
  { titulo: "Janelas e música", exemplos: ["Forja, joga essa janela para o outro monitor", "Forja, maximiza o VS Code", "Forja, minimiza tudo", "Forja, pausa a música", "Forja, dá um pause aí", "Forja, próxima música", "Forja, o que está tocando?"],
    texto: "“Essa janela” é a que está na frente. Música e vídeo pelo controle de mídia do Windows: vale para Spotify, YouTube no navegador e o que mais mostrar o player do Windows." },
  { titulo: "Pastas e arquivos", exemplos: ["Forja, abre a pasta de downloads", "Forja, acha o PDF do contrato de ontem", "Forja, procura a planilha de gastos", "Forja, onde está o print que eu tirei hoje?"],
    texto: "Procura em Downloads, Documentos, Área de trabalho, Imagens e Vídeos, pelo tipo (PDF, planilha, foto, print...), pelo dia (hoje, ontem, essa semana) e pelas palavras do nome. Abre a pasta com o arquivo selecionado; não apaga nem move nada." },
  { titulo: "Lembretes", exemplos: ["Forja, me lembre de lavar a louça às 14h", "Forja, me lembra de ligar pro João daqui a 20 minutos", "Forja, amanhã às 8 me lembre de tomar o remédio", "Forja, me dá um toque daqui a meia hora pra tirar a roupa do varal", "Forja, não me deixa esquecer de pagar a luz", "Forja, quais são meus lembretes?", "Forja, cancela o lembrete da louça"],
    texto: "Na hora, notificação no Windows, no celular (Forja Mobile) e ele fala, se estiver ligado. Sem horário, ele pergunta “Pra quando?”. Valem “às 2 da tarde”, “em meia hora”, “amanhã”, “na sexta às 10h”. Ficam guardados (somem só quando tocam ou você cancela) e aparecem em Configurações › Atividade; um perdido com o Forja fechado toca assim que ele abrir." },
  { titulo: "E-mail (Gmail)", exemplos: ["Forja, tenho e-mail importante?", "Forja, resume os e-mails do João", "Forja, lê o último do banco", "Forja, responde que eu topo a reunião na quinta", "Forja, escreve pra Ana dizendo que eu atraso 10 minutos"],
    texto: "Com a conta Google conectada (Configurações › Dados › E-mail e agenda). Ele resume falando, sem ler tudo. Para responder ou escrever, ele monta o rascunho no Gmail, lê em voz alta e pergunta “Mando?”: só envia com um sim (“manda”, “pode enviar”); “muda para sexta” reescreve; “não” deixa nos rascunhos. O que está escrito nos e-mails nunca vira ordem para ele." },
  { titulo: "Agenda (Google)", exemplos: ["Forja, o que tenho amanhã?", "Forja, tenho alguma coisa na quinta?", "Forja, marca dentista sexta às 10", "Forja, bota na agenda academia amanhã às 7 e me avisa meia hora antes"],
    texto: "Lê e marca na sua Agenda Google (evento de 1 hora; só o dia vira evento do dia todo). “Me avisa N minutos antes” cria um lembrete do Forja. Sem a conta conectada, “marca” vira lembrete do Forja." },
  { titulo: "Resumo do dia e avisos de e-mail", exemplos: ["Forja, como tá meu dia?", "Forja, resumo do dia"],
    texto: "Agenda, e-mails importantes, lembretes, o que o Forja trabalhou durante a noite, o clima da sua cidade e notícias. Escolha um horário em E-mail e agenda e ele sai sozinho todo dia (falado, no Windows e no celular). Em “Avisar quando chegar e-mail de”, ele avisa na hora os e-mails dessas pessoas ou com essas palavras no assunto." },
  { titulo: "Perguntar aos seus documentos", exemplos: ["Forja, quando vence meu contrato de aluguel?", "Forja, até quando vale a garantia da geladeira?", "Forja, o que diz minha nota sobre a viagem?"],
    texto: "Escolha as pastas em Documentos e memória (contratos, PDFs, Word, planilhas, um cofre do Obsidian). Ele lê tudo para um índice no seu PC, atualiza sozinho e responde dizendo de qual arquivo veio." },
  { titulo: "Memória pessoal", exemplos: ["Forja, lembra que meu carro é um Onix prata", "Forja, anota que a Bia prefere reunião à tarde", "Forja, qual é o meu carro?", "Forja, esquece o que eu disse do carro", "Forja, só pra você saber, meu aniversário é 14 de março", "Forja, prefiro que você me responda curtinho", "Forja, esquece aquilo de responder curtinho"],
    texto: "Só o que você pede ou conta de duradouro (“só pra você saber…”). Como você quer que ele responda vira preferência; o resto, fato sobre você. Ele nunca diz “anotado” sem guardar de verdade. Você vê e apaga em Configurações › Memória. É a mesma memória do agente do Forja: o que você ensina aqui ele também sabe. A conversa falada continua sem ir para o disco." },
  { titulo: "Notas rápidas", exemplos: ["Forja, anota: comprar café", "Forja, o que eu anotei ontem?", "Forja, apaga a nota do café"],
    texto: "Sem horário vira nota; com horário (“às 14h”) vira lembrete." },
  { titulo: "Projetos do Forja", exemplos: ["Forja, cria um card no board do Forja: bug no login", "Forja, onde o Forja trata o upload de imagem?", "Forja, o que o agente fez no projeto da loja hoje?"],
    texto: "O projeto é achado pelo nome entre as pastas do Agente e da Maestro (sem nome: o mais recente). O card entra no backlog do board; “onde” usa o índice de código do agente; o histórico junta as conversas do dia e os commits." },
  { titulo: "Sites e preços", exemplos: ["Forja, abre o site da Caixa", "Forja, abre o g1 no navegador do Forja", "Forja, vê quanto tá o PS5 nas lojas", "Forja, abre os links que você achou", "Forja, abre os links de compra", "Forja, onde eu compro esse?"],
    texto: "Acha o site oficial pela busca e fala o endereço para você conferir. Sem “do Forja”, abre no seu navegador (onde estão seus logins); com, no painel Navegador. Preço: lê as lojas agora e diz as três mais baratas. Depois de uma pesquisa falada, “abre os links” abre no seu navegador as fontes que ele usou, e “os links de compra” (ou “onde eu compro esse?”) abre três lojas do produto de que vocês falaram, sem abrir tarefa do agente. Tarefa do agente que fala de “isso”, “esse” ou “os links” leva junto o fim da conversa falada (só nesses pedidos: a tarefa fica gravada por 1 hora). Login e compra são sempre seus." },
  { titulo: "O que está na sua tela", exemplos: ["Forja, o que tem na minha tela?", "Forja, o que esse erro aqui quer dizer?", "Forja, acha produtos parecidos com esse que eu tô vendo", "Forja, onde eu compro isso mais barato?"],
    texto: "Tira um print do monitor da janela da frente e responde olhando para ele: lê erros, resume a página, explica o que aparece. Com produto na tela, descobre qual é, diz as três lojas mais baratas e abre a busca no painel Navegador. Precisa de um modelo com visão (gemma 4, por exemplo); com modelo da nuvem, o print sai do PC. Ele não fica salvo." },
  { titulo: "Acompanhar e avisar", exemplos: ["Forja, me avisa quando o Galaxy S25 baixar de 4 mil", "Forja, me fala quando sair versão nova do llama.cpp", "Forja, o que você está acompanhando?", "Forja, para de acompanhar o Galaxy"],
    texto: "Confere a cada 3 horas e avisa uma vez, falado, no Windows e no celular. Versão nova vale para projetos do GitHub. A lista fica em Configurações › Atividade." },
  { titulo: "Ditado em qualquer campo", exemplos: ["microfone ao lado do Enviar", "Forja, dita", "Forja, quero ditar uma mensagem"],
    texto: "Todo campo de chat do Forja tem um microfone ao lado do Enviar (nos outros campos ele aparece em cima, no canto): um clique grava, outro para, e o texto entra onde está o cursor. Usa o mesmo Whisper e o mesmo microfone do assistente, mesmo com ele desligado. “Forja, dita” começa no campo aberto e para sozinho quando você fica em silêncio." },
  { titulo: "O agente do Forja por voz", exemplos: ["Forja, como tá o trabalho no projeto da loja?", "Forja, ele tá pedindo alguma coisa?", "Forja, aprova o que ele pediu", "Forja, recusa", "Forja, para o agente"],
    texto: "Status (tarefas feitas e a de agora), o que ele pede para aprovar, aprovar, recusar e parar. Com mais de um pedido, ou um comando que apaga ou força algo, ele lê antes e só aprova com um sim." },
  { titulo: "Ler em voz alta", exemplos: ["Forja, lê essa resposta", "Forja, lê o que o agente respondeu"],
    texto: "Lê a última resposta da conversa aberta na tela (código vira “código na tela”), com a voz escolhida em Ouvir e falar." },
  { titulo: "Círculo e bandeja", exemplos: ["Configurações do Forja › Círculo do assistente", "bandeja do sistema"],
    texto: "Para jogar, desligue o círculo do canto da tela (ele continua ouvindo). Ligado ou desligado, o assistente lembra ao reabrir o Forja. Na bandeja: o modelo na VRAM, a VRAM e a RAM em uso, e Descarregar; o ícone ganha um ponto verde com modelo carregado." },
  { titulo: "Conversa contínua", exemplos: ["Forja, quanto é 15% de 200?", "… e de 300?"],
    texto: "Depois de responder, ele fica uns segundos ouvindo a próxima pergunta sem precisar de “Forja” (o círculo mostra que está escutando). Barulho nessa janela é ignorado. Liga e desliga em Ouvir e falar." },
  { titulo: "Outros idiomas", exemplos: ["Forja, como se diz bom dia em espanhol?", "Forja, traduz isso pro inglês", "Forja, vamos conversar em inglês pra eu treinar", "Let's go back to Portuguese"],
    texto: "Traduz e fala com uma voz nativa (inglês, espanhol, francês, italiano, japonês, chinês, hindi). Os idiomas que ele OUVE você escolhe em Configurações › Voz (Português, Inglês… um ou vários juntos): fora deles o Whisper não inventa texto em outra língua; na conversa para treinar, o idioma dela entra sozinho. Na conversa para treinar, ele ouve e responde no idioma e corrige seus erros de leve, até você pedir para voltar ao português." },
  { titulo: "Desfazer e registro", exemplos: ["Forja, desfaz", "Forja, volta o que você fez"],
    texto: "Tudo o que ele faz por voz fica em “O que ele fez” (só a ação, nunca a conversa). Dá para desfazer volume, mudo, saída de som, lembrete, nota, memória, card, acompanhamento e evento da agenda; e-mail enviado, aprovação e programa fechado não voltam, por isso esses pedem confirmação ou nome antes." },
  { titulo: "Raciocínio do modelo", exemplos: ["desligado (rápido)", "baixo", "médio", "alto"],
    texto: "Em Modelo do assistente: quanto ele pensa antes de responder. Desligado é o mais rápido; ligado ajuda em conta e lógica, mas a fala demora. O pensamento nunca é falado." },
  { titulo: "Detector treinado de “Forja”", exemplos: ["Só Whisper", "Detector", "Detector + Whisper", "Gravar minha voz"],
    texto: "Um modelo pequeno treinado só para o nome acende o círculo na hora e quase não gasta CPU. Treina sozinho com vozes sintéticas; gravações da sua voz deixam ele bem melhor (ouça e apague as ruins na lista). Gravação só conta depois de treinar de novo (a página avisa quando há gravação fora do treino); a barra de baixo mostra ao vivo a certeza de “Forja” e o traço, onde ele acende. “Detector + Whisper” acende rápido e não deixa o nome escapar." },
  { titulo: "Tarefas: faça qualquer coisa", exemplos: ["Forja, reproduza um vídeo aleatório das minhas inscrições do YouTube", "Forja, pesquise placas de vídeo de até 3800 reais e abra os links no meu navegador", "Forja, organiza minha pasta de downloads por tipo de arquivo", "Forja, vê por que meu disco C tá tão cheio", "Forja, mantém essa tarefa", "Sim, ficou certo", "Não, era das curtidas", "Forja, tá errado"],
    texto: "Pedido para fazer algo vira trabalho do agente do Forja, numa conversa temporária “🎙 …” do modo Agente, com o modelo do assistente. A tela não pula para ela: na conversa dele aparece um cartão com o que o agente está fazendo agora (“Pensando…”, “Pesquisando na web”) e o botão Abrir. Ela é só o trabalho de fundo: se apaga sozinha 1 hora depois da última atividade, a menos que você clique em Manter nela ou diga “Forja, mantém essa tarefa”. Ele dá o próprio jeito: web, sua conta do YouTube (conectada em Conteúdo), arquivos, comandos, scripts que ele mesmo escreve, o navegador do Forja e a tela (print, clique, digitação; ver a tela pede modelo com visão). O que é para você ver abre no SEU navegador. Quando demora, avisa o que está fazendo; dúvida que muda o resultado ele pergunta em voz alta e você responde falando. Comando que muda algo pede seu “sim”; comprar, pagar, enviar mensagem e digitar senha ficam sempre com você. Aprende com receitas: antes de começar, procura a receita de um pedido parecido (“spotify”, “curtidas”…) e segue o caminho curto dela. Sem receita, resolve, confere e pergunta “Ficou certo?”: com o seu sim, guarda o caminho; com “não, era das curtidas”, tenta de novo com a sua correção (até 3 vezes). Pergunta (“quanto espaço livre eu tenho?”) não vira receita nem pergunta “Ficou certo?”. Receita aprovada que funcionou não pergunta; se não estava certo, “Forja, tá errado” (até 10 minutos depois) reabre e ele corrige a receita, guardando a versão anterior até você aprovar a nova. Ficam em Configurações › Memória › Receitas de tarefas do assistente (e na aba Dados do painel desta página), para ver, aprovar ou apagar. No fim, fala o que fez; “Forja, para o agente” interrompe." },
  { titulo: "Ajustes do microfone", exemplos: ["Ganho", "Sensibilidade da voz", "Pausa que encerra o pedido", "Nível do microfone"],
    texto: "Em Ouvir e falar. A barra mostra o som ao vivo e o traço, o limiar de fala: falando normalmente a barra passa do traço com folga; em silêncio, fica abaixo. Ganho sobe o sinal do microfone baixo; sensibilidade muda o limiar; a pausa é quanto silêncio fecha o pedido. Vale na hora, sem recarregar o Whisper. O atalho e o botão do microfone dão um bip quando ele começa a ouvir." },
];

function Tutorial() {
  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="text-[17px] font-semibold text-fg">Como usar o F.O.R.J.A</h2>
        <p className="mt-1 text-[13px] text-muted">Ferramenta Operacional Relativamente Jumenta e Autônoma: tudo o que ele sabe fazer.</p>
      </div>
      <ol className="flex flex-col gap-3.5">
        {CAPACIDADES.map((c, i) => (
          <li key={c.titulo} className="grid grid-cols-[22px_minmax(0,1fr)] gap-x-2">
            <span className="font-mono text-[11px] leading-[19px] text-faint">{String(i + 1).padStart(2, "0")}</span>
            <div className="flex flex-col gap-1">
              <span className="text-[13px] font-medium text-fg">{c.titulo}</span>
              <p className="text-[12.5px] leading-relaxed text-muted">{c.texto}</p>
              {c.exemplos.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {c.exemplos.map((x) => <span key={x} className="rounded-[6px] bg-raised px-1.5 py-0.5 text-[11.5px] text-fg-2">“{x}”</span>)}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
const TECLAS: Record<string, string> = {
  ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right", Enter: "Enter", Tab: "Tab", Backspace: "Backspace",
  Insert: "Insert", Delete: "Delete", Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown", Space: "Space",
};

/** A combinação apertada no formato de acelerador do Electron ("Ctrl+Alt+F"). Pela tecla física (`code`): com Alt
 *  ou AltGr o `key` vira outro caractere. Sem modificador só vale F1–F24 (uma letra sozinha roubaria a digitação). */
function acelerador(ev: React.KeyboardEvent): string | null {
  const c = ev.code;
  const tecla = /^Key[A-Z]$/.test(c) ? c.slice(3) : /^Digit\d$/.test(c) ? c.slice(5) : /^F\d{1,2}$/.test(c) ? c
    : TECLAS[c] ?? TECLAS[ev.key] ?? null;
  if (!tecla) return null;
  const mods = [ev.ctrlKey && "Ctrl", ev.altKey && "Alt", ev.shiftKey && "Shift", ev.metaKey && "Super"].filter(Boolean) as string[];
  if (!mods.length && !/^F\d/.test(tecla)) return null;
  return [...mods, tecla].join("+");
}

const DICA_HUMOR: Record<string, string> = {
  nenhum: "Responde direto, sem gracinha.",
  sarcastico: "“Quanto é 2+2?” → “Quatro. Quer que eu prepare a papinha também?”",
  acido: "“Quanto é 2+2?” → “Quatro. Anotei no seu prontuário: completou a soma sem ajuda.”",
  leve: "Um comentário divertido de vez em quando, sem alfinetar.",
};

/** Página do assistente de voz F.O.R.J.A (Ferramenta Operacional Relativamente Jumenta e Autônoma): instalar e ligar,
 *  microfone, a voz com que ele responde (a rápida, Kokoro, ou uma voz clonada da tela Voz), o humor, os modelos
 *  favoritos dos comandos de imagem e vídeo e a pasta dos projetos novos do agente. */
export default function AssistenteView(props: { onError: (e: string) => void; onAbrirConversa: (id: number, kind: string) => void }) {
  const { onError } = props;
  const [e, setE] = useState<Estado | null>(null);
  const [mics, setMics] = useState<Mic[]>([]);
  const [local, setLocal] = useState<{ models: LocalModel[]; image_models: LocalModel[]; video_models: LocalModel[] } | null>(null);
  const [catalogo, setCatalogo] = useState<Catalogo>([]);
  const [tts, setTts] = useState<Tts>({ modelos: [], vozes: [] });
  const [job, setJob] = useState<Job | null>(null);
  const [teste, setTeste] = useState("");
  const [atalho, setAtalho] = useState<{ atual: string; ok: boolean; erro: string } | null>(null);
  const [gravandoAtalho, setGravandoAtalho] = useState(false);
  const [gravandoVoz, setGravandoVoz] = useState(false);
  const [gravs, setGravs] = useState<Gravacao[]>([]);
  const [avisoGrav, setAvisoGrav] = useState("");
  const carregaGravs = useCallback(() => api.get<Gravacao[]>("/assistente/gravacoes").then(setGravs).catch(() => {}), []);
  useEffect(() => { carregaGravs(); }, [carregaGravs]);
  const [painel, setPainel] = useState(() => { try { return localStorage.getItem("forja.assistente.painel") !== "0"; } catch { return true; } });
  const alternarPainel = () => setPainel((v) => { try { localStorage.setItem("forja.assistente.painel", v ? "0" : "1"); } catch { /* sem storage */ } return !v; });
  const [aba, setAba] = useState<Aba>(() => { try { return (localStorage.getItem("forja.assistente.aba") as Aba) || "voz"; } catch { return "voz"; } });
  const trocaAba = (a: Aba) => { setAba(a); try { localStorage.setItem("forja.assistente.aba", a); } catch { /* sem storage */ } };
  const [tutorial, setTutorial] = useState(false);
  const [memorias, setMemorias] = useState<Memoria[]>([]);
  const carregaMemorias = useCallback(() => api.get<{ items: Memoria[] }>("/memory/personal")
    .then((r) => setMemorias(r.items.filter((m) => m.type === "usuario" || m.type === "preferencia"))).catch(() => {}), []);
  useEffect(() => { carregaMemorias(); const t = setInterval(carregaMemorias, 5000); return () => clearInterval(t); }, [carregaMemorias]);
  const [vivo, setVivo] = useState("desligado");  // o estado do ouvido, rápido: o círculo acompanha a fala
  const [nivel, setNivel] = useState<Nivel | null>(null);
  const [transcricao, setTranscricao] = useState<Fala[]>([]);
  const [fazendo, setFazendo] = useState("");
  const carregaConversa = useCallback(() => api.get<{ transcricao: Fala[]; fazendo: string }>("/assistente/conversa")
    .then((r) => {
      setTranscricao((a) => (JSON.stringify(a) === JSON.stringify(r.transcricao) ? a : r.transcricao));
      setFazendo(r.fazendo || "");
    }).catch(() => {}), []);
  useEffect(() => {
    let parado = false;
    const t = setInterval(() => {
      if (document.hidden || parado) return;
      api.get<{ estado: string; nivel?: Nivel | null }>("/assistente/estado?medidor=1").then((r) => { setVivo(r.estado); setNivel(r.nivel ?? null); }).catch(() => {});
    }, 300);
    const c = setInterval(() => { if (!document.hidden) carregaConversa(); }, 500);
    carregaConversa();
    // a tela grande mostra o círculo: a sobreposição do canto some enquanto ela estiver à vista
    window.forja?.assistenteVisivel?.(true);
    return () => { parado = true; clearInterval(t); clearInterval(c); window.forja?.assistenteVisivel?.(false); };
  }, [carregaConversa]);
  const entendendo = vivo === "entendendo" || vivo === "transcrevendo";
  // cola no fim a cada resposta (a resposta cresce na mesma bolha: o tamanho da lista não muda); rolar para cima solta
  const { ref: rolagem, fim: fimConversa, onScroll: seguirConversa } = useStickyBottom<HTMLDivElement>([transcricao, fazendo, entendendo]);
  const chamar = () => api.post<{ ligado: boolean; estado: string }>("/assistente/chamar")
    .then((r) => { setVivo(r.estado); carrega(); }).catch((er) => onError(er.message));
  useEffect(() => {  // o registro é da casca (Electron): mudou a configuração, ela registra em até 5 s
    if (!window.forja?.atalho) return;
    const le = () => window.forja!.atalho!().then(setAtalho).catch(() => {});
    le();
    const t = setInterval(le, 2000);
    return () => clearInterval(t);
  }, []);

  const carrega = useCallback(() => api.get<Estado>("/assistente").then(setE).catch((er) => onError(er.message)), [onError]);
  useEffect(() => { carrega(); }, [carrega]);
  useEffect(() => {
    if (!e?.instalado) return;
    api.get<Mic[]>("/assistente/microfones").then(setMics).catch(() => {});
    api.get<{ models: LocalModel[]; image_models: LocalModel[]; video_models: LocalModel[] }>("/local").then(setLocal).catch(() => {});
    api.get<Catalogo>("/catalog").then(setCatalogo).catch(() => {});
    api.get<Tts>("/tts").then(setTts).catch(() => {});
  }, [e?.instalado]);
  const [treino, setTreino] = useState<Job & { done?: number; total?: number } | null>(null);
  const [rvcJob, setRvcJob] = useState<Job | null>(null);
  useEffect(() => {  // instalando/treinando: a barra anda pelo job; ligado: o estado (ouvindo, falando...) acompanha
    if (!e?.instalando && !e?.ligado && !e?.treinando && !e?.rvc_instalando) return;
    const t = setInterval(() => {
      carrega();
      if (e?.instalando || e?.treinando || e?.rvc_instalando) api.get<{ jobs: (Job & { done?: number; total?: number })[] }>("/local").then((r) => {
        if (e?.instalando) setJob(r.jobs.find((j) => j.id === e.instalando) ?? null);
        if (e?.treinando) setTreino(r.jobs.find((j) => j.id === e.treinando) ?? null);
        if (e?.rvc_instalando) setRvcJob(r.jobs.find((j) => j.id === e.rvc_instalando) ?? null);
      }).catch(() => {});
    }, 1500);
    return () => clearInterval(t);
  }, [e?.instalando, e?.ligado, e?.treinando, e?.rvc_instalando, carrega]);

  if (!e) return <div className="flex-1 bg-bg" />;
  const grava = (patch: Partial<Estado>) => api.put<Estado>("/assistente", patch).then((r) => setE((a) => a && { ...a, ...r })).catch((er) => onError(er.message));
  const ligar = (ligado: boolean) => api.post<Estado>("/assistente/ligar", { ligado }).then(setE).catch((er) => onError(er.message));
  const imagens = local?.image_models.filter((m) => !m.req?.edita) ?? [];
  // modelos para os favoritos: os GGUF de texto (IA local) e o que cada provedor lista; o já escolhido sempre aparece
  const opcoes: { grupo: string; provider: string; model: string }[] = [
    ...(local?.models.filter((m) => m.kind === "chat").map((m) => ({ grupo: "IA local", provider: "local", model: m.name })) ?? []),
    ...catalogo.filter((p) => p.id !== "local").flatMap((p) => p.models.map((m) => ({ grupo: p.name, provider: p.id, model: m }))),
  ];
  for (const f of e.favoritos) if (!opcoes.some((o) => o.provider === f.provider && o.model === f.model)) opcoes.push({ grupo: "Escolhidos", provider: f.provider, model: f.model });
  const grupos = [...new Set(opcoes.map((o) => o.grupo))];
  const cabo = mics.find((m) => m.tipo === "saida" && m.api === "MME" && m.nome.startsWith("CABLE Input"));
  const mudaFav = (i: number, patch: Partial<Favorito> | null) =>
    grava({ favoritos: patch === null ? e.favoritos.filter((_, j) => j !== i) : e.favoritos.map((f, j) => (j === i ? { ...f, ...patch } : f)) });

  const instalarBotao = (
    <CartaoEstado tom={job?.error ? "erro" : "neutro"} titulo="Falta instalar o ouvido e a voz"
      acoes={e.instalando ? undefined : (
        <button className={botaoEstadoPrimario} onClick={() => api.post<Job>("/assistente/instalar").then((j) => { setJob(j); carrega(); }).catch((er) => onError(er.message))}>
          {job?.error ? "Tentar de novo" : "Instalar"}
        </button>
      )}>
      {e.instalando ? <span className="font-mono text-[11.5px]">{job?.detail || "começando…"}</span>
        : job?.error ? job.error
        : "Whisper (ouvir) e Kokoro (falar) num Python próprio, na CPU: a GPU fica para o modelo. Uns 600 MB; o Whisper baixa ao ligar pela primeira vez."}
    </CartaoEstado>
  );
  const modo = !e.ligado ? "" : MODO_CIRCULO[vivo] ?? "ouvindo";
  const enviar = () => {
    if (!teste.trim()) return;
    api.post("/assistente/simular", { texto: teste }).then(() => setTeste("")).catch((er) => onError(er.message));
  };

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <BarraTopo
          contagem={0}
          unidade={["", ""]}
          esquerda={
            <button onClick={() => setTutorial((v) => !v)} aria-pressed={tutorial}
                    className={`rounded-[7px] px-2 py-1 text-xs ${tutorial ? "bg-raised text-fg" : "text-muted hover:bg-raised hover:text-fg"}`}>
              {tutorial ? "Voltar" : "Como usar"}
            </button>
          }
          status={null}
          parametros={painel}
          onParametros={alternarPainel}
        />
        <div className="flex min-h-0 flex-1 flex-col">
          {tutorial ? (
            <div className="flex-1 overflow-y-auto"><div className="mx-auto max-w-[720px] px-5 py-5"><Tutorial /></div></div>
          ) : !e.instalado ? (
            <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-5 py-8">{instalarBotao}</div>
          ) : (
            <>
            {/* círculo e microfone fixos no topo: só a conversa embaixo rola (antes subiam junto e sumiam de vista) */}
            <div className={`flex shrink-0 flex-col items-center px-5 ${transcricao.length ? "pt-4 pb-2" : "pt-[6vh] pb-4"}`}>
              <CirculoForja modo={modo} tamanho={transcricao.length ? 120 : 220} />
              <button onClick={chamar} disabled={!!e.erro && !e.ligado}
                      title={modo === "falando" ? "Interromper e falar" : "Falar com o F.O.R.J.A agora (sem dizer “Forja”)"}
                      aria-label="Falar com o assistente agora"
                      className={`${transcricao.length ? "mt-4 size-12" : "mt-7 size-14"} grid place-items-center rounded-full transition-colors ${modo === "escutando" ? "bg-accent text-accent-fg" : "border border-line-strong bg-surface text-fg hover:border-accent hover:text-accent-text"}`}>
                <Mic className="size-[22px]" />
              </button>
              <p className="mt-2.5 h-5 text-center text-[13px] text-muted">
                {e.erro ? <span className="text-diff-del-fg">{e.erro}</span>
                  : !e.ligado ? "Desligado: o botão liga e já ouve"
                  : modo === "escutando" ? "Pode falar…"
                  : modo === "ouvindo" ? `Diga “Forja, …”${e.atalho ? ` ou aperte ${e.atalho}` : ""}${e.idioma ? ` · conversa em ${e.idioma}` : ""}`
                  : ESTADO[vivo] ?? vivo}
              </p>
            </div>
            <div ref={rolagem} onScroll={seguirConversa} className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto flex max-w-[720px] flex-col items-center px-5 pb-6">
              <div className="mt-3 flex w-full flex-col gap-2.5">
                {transcricao.map((x, i) => x.quem === "voce" ? (
                  <div key={i} className="flex justify-end">
                    <p className="max-w-[80%] rounded-2xl bg-raised px-3.5 py-2 text-[13.5px] leading-relaxed text-fg">{x.texto}</p>
                  </div>
                ) : (
                  <div key={i} className="max-w-[85%] px-3.5">
                    {(x.raciocinio || !!x.ferramentas?.length) && <AtividadeFalada x={x} vivo={i === transcricao.length - 1 && !!fazendo && !x.texto} />}
                    {x.texto && <p className="py-1 text-[13.5px] leading-relaxed text-fg-2">{x.texto}</p>}
                    {x.conv && <CartaoConversa c={x.conv} onAbrir={props.onAbrirConversa} />}
                  </div>
                ))}
                {entendendo && (
                  <div className="flex justify-end"><p className="rounded-2xl bg-raised px-3.5 py-2"><Fazendo texto="Transcrevendo…" /></p></div>
                )}
                {fazendo && !entendendo && <div className="px-3.5"><Fazendo texto={fazendo} /></div>}
                <div ref={fimConversa} />
              </div>
              {transcricao.length > 0 && (
                <div className="mt-4 flex items-center gap-1.5">
                  <button className={botaoEstado} onClick={() => api.post<{ conv: number }>("/assistente/salvar-conversa").then(carregaConversa).catch((er) => onError(er.message))}
                          title="Cria um chat do Forja com esta conversa (é o único jeito de ela ficar salva)">Salvar como chat</button>
                  <button className={botaoEstado} onClick={() => api.del("/assistente/conversa").then(carregaConversa).catch((er) => onError(er.message))}
                          title="Apaga a conversa da memória dele agora">Esquecer</button>
                </div>
              )}
              {!transcricao.length && (
                <p className="mt-2 max-w-sm text-center text-[12px] leading-relaxed text-faint">
                  A conversa falada fica só na memória: some quando o modelo dele descarrega. Para guardar, diga “salva nossa
                  conversa num chat”.
                </p>
              )}
            </div>
            </div>
            </>
          )}
        </div>

        {e.instalado && !tutorial && (
          <div className="shrink-0 px-5 pb-4">
            <div className="mx-auto flex max-w-[720px] items-center gap-2 rounded-2xl border border-line bg-surface px-3.5 py-2.5 transition-colors focus-within:border-focus">
              <input value={teste} onChange={(ev) => setTeste(ev.target.value)} onKeyDown={(ev) => { if (ev.key === "Enter") enviar(); }}
                     aria-label="Escrever para o assistente" placeholder={e.ligado ? "Ou escreva para ele… (a resposta sai falada)" : "Ligue o assistente para escrever para ele"}
                     disabled={!e.ligado} className="min-w-0 flex-1 bg-transparent text-[13.5px] text-fg outline-none placeholder:text-faint disabled:opacity-50" />
              <button onClick={enviar} disabled={!teste.trim() || !e.ligado}
                      className="shrink-0 rounded-[9px] bg-accent px-3 py-1.5 text-[12.5px] font-semibold text-accent-fg hover:brightness-110 disabled:bg-raised disabled:text-faint">
                Enviar
              </button>
            </div>
          </div>
        )}
      </div>

      {painel && (
        <aside className="flex w-[380px] shrink-0 flex-col overflow-hidden border-l border-line bg-side text-xs">
          <div className="flex items-center gap-2 px-4 pt-4 pb-1.5">
            <span className="text-[13px] font-semibold text-fg">Configurações</span>
            <span className="ml-auto" />
            {e.instalado && (
              <button className={`${e.ligado ? botaoEstado : botaoEstadoPrimario} !px-2 !py-0.5`} onClick={() => ligar(!e.ligado)}>{e.ligado ? "Desligar" : "Ligar"}</button>
            )}
            <button onClick={alternarPainel} className="rounded-[7px] p-1 text-faint hover:bg-raised hover:text-fg" aria-label="Esconder as configurações">
              <X className="size-3.5" />
            </button>
          </div>
          {e.instalado && (
            <div className="mx-4 mt-2 flex rounded-[8px] border border-line bg-surface p-0.5 text-xs" role="tablist" aria-label="Seções das configurações">
              {ABAS.map(([id, rot]) => (
                <button key={id} role="tab" aria-selected={aba === id} onClick={() => trocaAba(id)}
                        className={`flex-1 rounded-[6px] py-1.5 ${aba === id ? "bg-raised font-medium text-fg" : "text-muted hover:text-fg"}`}>{rot}</button>
              ))}
            </div>
          )}
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto pb-4">
            {!e.instalado ? <p className="px-4 text-[12px] leading-relaxed text-muted">Instale o assistente para configurá-lo.</p> : (
              <>
                {aba === "modelos" && <Secao titulo="Modelo do assistente">
                    <Caixa rotulo="Responde com">
                      <select className={`${campo} cursor-pointer`} value={e.modelo ? `${e.modelo.provider}|${e.modelo.model}` : ""}
                              onChange={(ev) => { const [provider, ...m] = ev.target.value.split("|"); grava({ modelo: provider ? { provider, model: m.join("|") } : null }); }}>
                        <option value="">o do último envio no Forja</option>
                        {grupos.map((g) => (
                          <optgroup key={g} label={g}>
                            {opcoes.filter((o) => o.grupo === g).map((o) => <option key={`${o.provider}|${o.model}`} value={`${o.provider}|${o.model}`}>{o.model.split(/[\\/]/).pop()}</option>)}
                          </optgroup>
                        ))}
                      </select>
                    </Caixa>
                  <div className="grid grid-cols-2 gap-1.5">
                    <Caixa rotulo="Raciocínio">
                      <select className={`${campo} cursor-pointer`} value={e.raciocinio} onChange={(ev) => grava({ raciocinio: ev.target.value })}
                              title="Quanto o modelo pensa antes de falar. Desligado responde na hora; ligado acerta mais conta e lógica, mas a fala demora (o pensamento não é falado)">
                        <option value="desligado">desligado (rápido)</option>
                        <option value="baixo">baixo</option>
                        <option value="medio">médio</option>
                        <option value="alto">alto (lento)</option>
                      </select>
                    </Caixa>
                    <Caixa rotulo="Descarregar parado há">
                      <select className={`${campo} cursor-pointer`} value={e.ocioso_min} onChange={(ev) => grava({ ocioso_min: Number(ev.target.value) })}>
                        {[0, 2, 5, 10, 15, 30, 60].map((n) => <option key={n} value={n}>{n ? `${n} min` : "nunca"}</option>)}
                      </select>
                    </Caixa>
                  </div>
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    Um modelo pequeno e rápido aqui deixa a conversa falada ágil. Ele nunca derruba trabalho do Forja: com outro
                    modelo em uso, responde com o que estiver no ar. O que ele carregou sai da placa depois do tempo parado (se
                    ninguém mais usou). Modelo de nuvem não ocupa a placa. Modelo maior que a placa (ex.: um 35B numa de 12 GB) roda
                    parte na CPU e responde devagar mesmo sem raciocínio: para conversar, prefira um que caiba inteiro.
                  </p>
                
                    <div className="mt-1 flex flex-col gap-1.5">
                      <span className="text-[11.5px] font-medium text-fg-2">Pode usar se já estiver carregado</span>
                      {e.permitidos.map((p, i) => (
                        <div key={`${p.provider}|${p.model}`} className="flex items-center gap-2 rounded-[8px] border border-line bg-surface px-2.5 py-1.5">
                          <span className="min-w-0 flex-1 truncate text-[12px] text-fg" title={`${p.provider} · ${p.model}`}>{p.model.split(/[\\/]/).pop()}</span>
                          <span className="shrink-0 text-[10.5px] text-faint">{p.provider === "local" ? "IA local" : p.provider}</span>
                          <button aria-label={`Tirar ${p.model}`} className="text-[11.5px] text-faint hover:text-diff-del-fg"
                                  onClick={() => grava({ permitidos: e.permitidos.filter((_, j) => j !== i) })}>tirar</button>
                        </div>
                      ))}
                      <Caixa rotulo="Permitir mais um">
                        <select className={`${campo} cursor-pointer`} value="" onChange={(ev) => {
                          const [provider, ...m] = ev.target.value.split("|");
                          if (provider) grava({ permitidos: [...e.permitidos, { provider, model: m.join("|") }] });
                        }}>
                          <option value="">escolha um modelo…</option>
                          {grupos.map((g) => (
                            <optgroup key={g} label={g}>
                              {opcoes.filter((o) => o.grupo === g && !e.permitidos.some((p) => p.provider === o.provider && p.model === o.model))
                                .map((o) => <option key={`${o.provider}|${o.model}`} value={`${o.provider}|${o.model}`}>{o.model.split(/[\\/]/).pop()}</option>)}
                            </optgroup>
                          ))}
                        </select>
                      </Caixa>
                      <p className="text-[11.5px] leading-relaxed text-faint">
                        Se um destes já estiver carregado e parado, ele responde com ele em vez de carregar o dele: mais rápido e
                        sem trocar nada na placa. Também dá para trocar o modelo dele falando: “Forja, troca seu modelo para o Qwen 3.6”.
                      </p>
                    </div>
                </Secao>}

                {aba === "voz" && <Secao titulo="Ouvir e falar">
                  <Interruptor ligado={e.conversa_continua} onChange={(v) => grava({ conversa_continua: v })}
                               rotulo="Conversa contínua: depois de responder, ouve a próxima pergunta sem “Forja”" />
                  <Caixa rotulo="Microfone">
                    <select className={`${campo} cursor-pointer`} value={e.mic && mics.some((m) => m.id === e.mic) ? e.mic : ""} onChange={(ev) => grava({ mic: ev.target.value })}>
                      <option value="">Padrão do Windows</option>
                      {mics.filter((m) => m.api === "MME" && m.tipo !== "saida").map((m) => <option key={m.id} value={m.id}>{m.nome}</option>)}
                    </select>
                  </Caixa>
                  <Caixa rotulo="Precisão do Whisper (maior = entende melhor, demora mais)">
                    <select className={`${campo} cursor-pointer`} value={e.whisper} onChange={(ev) => grava({ whisper: ev.target.value })}>
                      <option value="base">base (rápido)</option>
                      <option value="small">small (equilibrado)</option>
                      <option value="medium">medium (preciso, 2 a 4 s por pedido na CPU)</option>
                    </select>
                  </Caixa>
                  <Caixa rotulo="Idiomas que você fala com ele (um só: mais rápido e sem confusão; vários: ele descobre qual, só entre estes)">
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {(e.idiomas ?? []).map((i) => {
                        const sel = (e.idiomas_whisper ?? ["pt"]).includes(i.id);
                        const lista = sel ? e.idiomas_whisper.filter((x) => x !== i.id) : [...(e.idiomas_whisper ?? []), i.id];
                        return (
                          <button key={i.id} disabled={sel && e.idiomas_whisper.length === 1} aria-pressed={sel}
                                  title={sel && e.idiomas_whisper.length === 1 ? "Ao menos um idioma" : undefined}
                                  onClick={() => grava({ idiomas_whisper: lista })}
                                  className={`rounded-full border px-2.5 py-0.5 text-[12px] transition-colors ${sel ? "border-accent bg-accent/15 text-accent-text" : "border-line text-muted hover:text-fg"}`}>
                            {i.nome}
                          </button>
                        );
                      })}
                    </div>
                  </Caixa>
                  <Caixa rotulo="Nível do microfone">
                    <Medidor n={e.ligado ? nivel : null} />
                  </Caixa>
                  <Caixa rotulo={`Ganho: ${(e.ganho ?? 1).toFixed(1).replace(".", ",")}× (aumente se a barra mal se mexe quando você fala)`}>
                    <input type="range" min={0.5} max={4} step={0.1} defaultValue={(e.ganho ?? 1)} className="w-full accent-accent"
                           onPointerUp={(ev) => grava({ ganho: Number((ev.target as HTMLInputElement).value) })}
                           onKeyUp={(ev) => grava({ ganho: Number((ev.target as HTMLInputElement).value) })} />
                  </Caixa>
                  <Caixa rotulo={`Sensibilidade da voz: ${Math.round((e.sensibilidade_voz ?? 0.5) * 100)}% (alta pega voz baixa ou de longe; baixa ignora TV e conversa ao fundo)`}>
                    <input type="range" min={0} max={1} step={0.05} defaultValue={(e.sensibilidade_voz ?? 0.5)} className="w-full accent-accent"
                           onPointerUp={(ev) => grava({ sensibilidade_voz: Number((ev.target as HTMLInputElement).value) })}
                           onKeyUp={(ev) => grava({ sensibilidade_voz: Number((ev.target as HTMLInputElement).value) })} />
                  </Caixa>
                  <Caixa rotulo={`Pausa que encerra o pedido: ${(e.pausa_fim ?? 1.65).toFixed(1).replace(".", ",")} s (aumente se ele corta você no meio)`}>
                    <input type="range" min={0.8} max={3} step={0.1} defaultValue={(e.pausa_fim ?? 1.65)} className="w-full accent-accent"
                           onPointerUp={(ev) => grava({ pausa_fim: Number((ev.target as HTMLInputElement).value) })}
                           onKeyUp={(ev) => grava({ pausa_fim: Number((ev.target as HTMLInputElement).value) })} />
                  </Caixa>
                
                  <Caixa rotulo="Alto-falante (onde ele fala)">
                    <select className={`${campo} cursor-pointer`} value={e.saida && mics.some((m) => m.id === e.saida) ? e.saida : ""} onChange={(ev) => grava({ saida: ev.target.value })}>
                      <option value="">Padrão do Windows</option>
                      {mics.filter((m) => m.api === "MME" && m.tipo === "saida").map((m) => <option key={m.id} value={m.id}>{m.nome}</option>)}
                    </select>
                  </Caixa>
                  <div className="flex rounded-[8px] border border-line bg-surface p-0.5 text-xs" role="radiogroup" aria-label="Voz do assistente">
                    {([["kokoro", "Rápida (Kokoro)", "Na CPU, começa a falar em ~1 s"],
                       ["tela", "Uma voz da tela Voz", "Clonada (F5/Fish): bem mais lenta, e tira o modelo de texto da GPU a cada resposta"]] as const).map(([id, rot, dica]) => (
                      <button key={id} role="radio" aria-checked={e.motor === id} title={dica} onClick={() => grava({ motor: id })}
                              className={`flex-1 rounded-[6px] py-1.5 ${e.motor === id ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>{rot}</button>
                    ))}
                  </div>
                  {e.motor === "kokoro" ? (<>
                    <Caixa rotulo="Voz">
                      <select className={`${campo} cursor-pointer`} value={e.voz} onChange={(ev) => grava({ voz: ev.target.value })}>
                        {e.vozes_kokoro.map((v) => <option key={v.id} value={v.id}>{v.nome}</option>)}
                      </select>
                    </Caixa>
                    {e.vozes_kokoro.find((v) => v.id === e.voz)?.rvc && (e.rvc_instalado
                      ? <Interruptor ligado={e.rvc} onChange={(v) => grava({ rvc: v })}
                                     rotulo="Limpar o timbre com o RVC desta voz (o .rvc.pth ao lado dela)" />
                      : e.rvc_instalando
                        ? <p className="font-mono text-[11.5px] text-muted">RVC: {rvcJob?.detail || "começando…"}</p>
                        : <p className="text-[11.5px] leading-relaxed text-faint">
                            Esta voz tem um modelo RVC (.rvc.pth) que limpa o timbre.{" "}
                            <button className="underline hover:text-fg" onClick={() => api.post<Job>("/assistente/instalar-rvc").then((j) => { setRvcJob(j); carrega(); }).catch((er) => onError(er.message))}>
                              Instalar o RVC</button> (PyTorch e Applio: ~2 GB na CPU, ~4 GB com CUDA na NVIDIA).
                          </p>)}
                  </>) : (
                    <>
                      <div className="grid grid-cols-2 gap-1.5">
                        <Caixa rotulo="Modelo da tela Voz">
                          <select className={`${campo} cursor-pointer`} value={e.modelo_tts} onChange={(ev) => grava({ modelo_tts: ev.target.value })}>
                            <option value="">o primeiro</option>
                            {tts.modelos.map((m) => <option key={m.nome}>{m.nome}</option>)}
                          </select>
                        </Caixa>
                        <Caixa rotulo="Voz de referência">
                          <select className={`${campo} cursor-pointer`} value={e.voz_tts} onChange={(ev) => grava({ voz_tts: ev.target.value })}>
                            <option value="">a primeira</option>
                            {tts.vozes.map((v) => <option key={v.id} value={v.id}>{v.nome}</option>)}
                          </select>
                        </Caixa>
                      </div>
                      <p className="text-[11.5px] leading-relaxed text-faint">Avisos curtos (“Pesquisando.”, “Criando a imagem…”) continuam na voz rápida; a resposta vem inteira na voz clonada, depois que o modelo termina.</p>
                    </>
                  )}
                </Secao>}

                {aba === "voz" && <Secao titulo="Personalidade">
                  <div className="grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="Humor">
                    {e.humores.map((h) => (
                      <button key={h.id} role="radio" aria-checked={e.humor === h.id} onClick={() => grava({ humor: h.id })}
                              className={`rounded-[9px] border px-3 py-2 text-left text-[12.5px] ${e.humor === h.id ? "border-accent-line bg-accent-soft text-accent-text" : "border-line bg-surface text-muted hover:text-fg"}`}>
                        {h.nome}
                      </button>
                    ))}
                  </div>
                  <p className="text-[11.5px] leading-relaxed text-faint">{DICA_HUMOR[e.humor]} Ele só brinca quando cabe: pergunta boba sim, assunto sério não, e nunca em toda resposta.</p>
                </Secao>}

                {aba === "voz" && <Secao titulo="Trocador de voz">
                  <Interruptor ligado={e.trocavoz.ligado} rotulo="Falar nas chamadas com a voz dele"
                               onChange={(v) => api.post("/assistente/trocavoz", { ligado: v }).then(carrega).catch((er) => onError(er.message))} />
                  <Caixa rotulo="Microfone virtual (onde a voz dele sai)">
                    <select className={`${campo} cursor-pointer`} value={e.trocavoz_saida && mics.some((m) => m.id === e.trocavoz_saida) ? e.trocavoz_saida : ""}
                            onChange={(ev) => grava({ trocavoz_saida: ev.target.value })}>
                      <option value="">{cabo ? `automático: ${cabo.nome}` : "automático: VB-CABLE (não instalado)"}</option>
                      {mics.filter((m) => m.api === "MME" && m.tipo === "saida").map((m) => <option key={m.id} value={m.id}>{m.nome}</option>)}
                    </select>
                  </Caixa>
                  <Caixa rotulo="Velocidade (Whisper do trocador)">
                    <select className={`${campo} cursor-pointer`} value={e.trocavoz_whisper} onChange={(ev) => grava({ trocavoz_whisper: ev.target.value })}>
                      <option value="base">rápida: ~1 s para converter (base)</option>
                      <option value="small">equilibrada: entende melhor, ~2 s (small)</option>
                      <option value="medium">precisa: 5 s ou mais, só com a CPU livre (medium)</option>
                    </select>
                  </Caixa>
                  <Caixa rotulo="Pausa que encerra a frase (mais longa: não te corta; mais curta: a voz sai antes)">
                    <select className={`${campo} cursor-pointer`} value={e.trocavoz_pausa} onChange={(ev) => grava({ trocavoz_pausa: Number(ev.target.value) })}>
                      {[0.3, 0.5, 0.6, 0.8, 1, 1.2, 1.5, 2].map((s) => <option key={s} value={s}>{s.toLocaleString("pt-BR")} s{s === 0.6 ? " (padrão)" : ""}</option>)}
                    </select>
                  </Caixa>
                  <Interruptor ligado={e.trocavoz_retorno} onChange={(v) => grava({ trocavoz_retorno: v })}
                               rotulo="Retorno: ouvir a voz dele no alto-falante de Ouvir e falar" />
                  {!cabo && (
                    <p className="text-[11.5px] leading-relaxed text-warn">
                      Falta o cabo de áudio virtual. Instale o VB-CABLE (grátis, pede administrador e reiniciar o PC){" "}
                      <button className="underline hover:text-fg" onClick={() => window.open("https://vb-audio.com/Cable/")}>em vb-audio.com</button>{" "}
                      e depois <button className="underline hover:text-fg" onClick={() => api.get<Mic[]>("/assistente/microfones").then(setMics).catch(() => {})}>procure de novo</button>.
                    </p>
                  )}
                  {e.trocavoz.erro && <p className="text-[11.5px] text-diff-del-fg">{e.trocavoz.erro}</p>}
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    No Discord (Configurações › Voz e vídeo), escolha o microfone “CABLE Output”. Fale normal no microfone de
                    Ouvir e falar: cada frase sai na voz escolhida acima logo depois de você parar de falar. Voz só em inglês
                    traduz o que você fala. Com o retorno, use fone: na caixa de som o microfone pega a voz dele de volta.
                    Desliga sozinho ao fechar o Forja.
                  </p>
                </Secao>}

                {aba === "chamar" && <Secao titulo="Atalho global">
                  <div className="flex gap-1.5">
                    <div className="flex-1">
                      <Caixa rotulo={gravandoAtalho ? "Aperte a combinação (Esc cancela)" : "Chamar sem dizer “Forja” (funciona com o Forja minimizado)"}>
                        <input readOnly className={`${campo} cursor-pointer font-mono`} value={gravandoAtalho ? "…" : e.atalho || "nenhum"}
                               onFocus={() => setGravandoAtalho(true)} onBlur={() => setGravandoAtalho(false)}
                               onKeyDown={(ev) => {
                                 ev.preventDefault();
                                 if (ev.key === "Escape") return (ev.target as HTMLInputElement).blur();
                                 const a = acelerador(ev);
                                 if (a) { grava({ atalho: a }); (ev.target as HTMLInputElement).blur(); }
                               }} />
                      </Caixa>
                    </div>
                    {e.atalho && <button className={`${botaoEstado} self-stretch`} onClick={() => grava({ atalho: "" })}>Tirar</button>}
                  </div>
                  <p className={`text-[11.5px] leading-relaxed ${atalho?.erro && atalho.atual === e.atalho ? "text-diff-del-fg" : "text-faint"}`}>
                    {atalho?.erro && atalho.atual === e.atalho ? atalho.erro
                      : !window.forja?.atalho ? "Atalho global só no Forja Desktop."
                      : "Aperte e fale o pedido direto. Com ele falando, o atalho interrompe e já ouve você (o microfone fica fechado enquanto ele fala). Desligado, ele liga e ouve. Ex.: Ctrl+Alt+F."}
                  </p>
                </Secao>}

                {aba === "chamar" && <Secao titulo="Detector de “Forja”">
                  <div className="flex rounded-[8px] border border-line bg-surface p-0.5 text-xs" role="radiogroup" aria-label="Como ele reconhece “Forja”">
                    {([["whisper", "Só Whisper", "Transcreve tudo o que o microfone pega e procura “Forja”: não precisa treinar, mas gasta CPU com TV ou conversa por perto, e o círculo demora um pouco mais"],
                       ["treinado", "Detector", "Um modelo pequeno só para “Forja”: quase não gasta CPU e acende na hora; se ele não ouvir o nome, nada acontece"],
                       ["ambos", "Detector + Whisper", "O detector acende o círculo na hora e o Whisper também confere toda fala: se o detector perder o nome, o Whisper pega (gasta CPU como o Só Whisper)"]] as const).map(([id, rot, dica]) => (
                      <button key={id} role="radio" aria-checked={e.detector === id} title={dica} disabled={id !== "whisper" && !e.detector_treinado}
                              onClick={() => grava({ detector: id })}
                              className={`flex-1 rounded-[6px] py-1.5 disabled:opacity-40 ${e.detector === id ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>{rot}</button>
                    ))}
                  </div>
                  {(e.gravacoes_novas ?? 0) > 0 && !e.treinando && (
                    <CartaoEstado tom="aviso" titulo={`${e.gravacoes_novas} ${e.gravacoes_novas > 1 ? "gravações suas ainda não entraram" : "gravação sua ainda não entrou"} no detector`}
                      acoes={<button className={botaoEstadoPrimario} onClick={() => api.post<Job>("/assistente/treinar").then(carrega).catch((er) => onError(er.message))}>Treinar agora</button>}>
                      O detector só aprende sua voz quando treina de novo. Com 20 a 30 gravações (perto e longe do microfone, baixo e alto) ele erra bem menos.
                    </CartaoEstado>
                  )}
                  {e.detector !== "whisper" && e.ligado && <Medidor n={nivel} />}
                  {e.detector !== "whisper" && (
                    <Caixa rotulo={`Sensibilidade: ${Math.round(e.sensibilidade * 100)}% (mais alta pega voz baixa ou de longe, e acende à toa mais vezes)`}>
                      <input type="range" min={0} max={1} step={0.05} defaultValue={e.sensibilidade} className="w-full accent-accent"
                             onPointerUp={(ev) => grava({ sensibilidade: Number((ev.target as HTMLInputElement).value) })}
                             onKeyUp={(ev) => grava({ sensibilidade: Number((ev.target as HTMLInputElement).value) })} />
                    </Caixa>
                  )}
                  {e.treinando ? (
                    <div className="flex flex-col gap-1">
                      <span className="truncate font-mono text-[11.5px] text-muted">{treino?.detail || "começando…"}</span>
                      <div className="h-[3px] overflow-hidden rounded-full bg-line">
                        <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(2, treino?.total ? (treino.done ?? 0) / treino.total * 100 : 2)}%` }} />
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button className={botaoEstadoPrimario} onClick={() => api.post<Job>("/assistente/treinar").then(carrega).catch((er) => onError(er.message))}>
                        {e.detector_treinado ? "Treinar de novo" : "Treinar o detector"}
                      </button>
                      <button className={botaoEstado} disabled={gravandoVoz || e.minha_voz >= 30}
                              title="Clique e diga “Forja” uma vez. Varie: perto e longe do microfone, baixo e alto, rápido e devagar. 20 a 30 gravações, depois treine de novo."
                              onClick={() => { setGravandoVoz(true); setAvisoGrav(""); api.post<{ minha_voz: number; gravacao: Gravacao }>("/assistente/gravar-voz")
                                .then((r) => { setE((a) => a && { ...a, minha_voz: r.minha_voz }); setAvisoGrav(r.gravacao.aviso ? `Gravação ${r.gravacao.id}: ${r.gravacao.aviso}. Apague e grave de novo.` : ""); carregaGravs(); })
                                .catch((er) => onError(er.message)).finally(() => setGravandoVoz(false)); }}>
                        {gravandoVoz ? "Diga “Forja” agora…" : `Gravar minha voz (${e.minha_voz})`}
                      </button>
                    </div>
                  )}
                  {avisoGrav && <p className="text-[11.5px] text-warn">{avisoGrav}</p>}
                  {gravs.length > 0 && (
                    <div className="flex max-h-[220px] flex-col gap-0.5 overflow-y-auto rounded-[10px] border border-line bg-surface p-1.5" aria-label="Suas gravações">
                      <div className="flex items-center px-1.5 pb-1 text-[10.5px] text-faint">
                        <span className="flex-1">Suas gravações ({gravs.length})</span>
                        <button className="hover:text-diff-del-fg" onClick={() => api.del<{ minha_voz: number }>("/assistente/gravar-voz")
                          .then((r) => { setE((a) => a && { ...a, minha_voz: r.minha_voz }); carregaGravs(); }).catch((er) => onError(er.message))}>apagar todas</button>
                      </div>
                      {gravs.map((g) => (
                        <div key={g.id} className="group flex items-center gap-2 rounded-[7px] px-1.5 py-1 hover:bg-raised/60">
                          <button aria-label={`Ouvir a gravação ${g.id}`} title="Ouvir" className="text-[12px] text-muted hover:text-fg"
                                  onClick={() => { new Audio(`/api/assistente/gravacoes/${g.id}/audio`).play().catch(() => {}); }}>▶</button>
                          <span className="font-mono text-[11px] text-faint">{g.id}</span>
                          <span className={`min-w-0 flex-1 truncate text-[11.5px] ${g.aviso ? "text-warn" : "text-muted"}`} title={g.aviso}>
                            {g.aviso || `${g.segundos_voz.toFixed(1).replace(".", ",")} s de voz`}
                          </span>
                          {g.reconhecida !== null && (
                            <span className={`text-[11px] ${g.reconhecida ? "text-ok" : "text-diff-del-fg"}`}
                                  title={g.reconhecida ? "O último treino reconhece esta gravação" : "O último treino NÃO reconhece esta gravação: regrave ou treine de novo"}>
                              {g.reconhecida ? "✓" : "✗"}
                            </span>
                          )}
                          <button aria-label={`Apagar a gravação ${g.id}`} title="Apagar" className="text-[12px] text-faint opacity-60 hover:text-diff-del-fg group-hover:opacity-100"
                                  onClick={() => api.del<Gravacao[]>(`/assistente/gravacoes/${g.id}`).then((r) => { setGravs(r); setE((a) => a && { ...a, minha_voz: r.length }); }).catch((er) => onError(er.message))}>🗑</button>
                        </div>
                      ))}
                    </div>
                  )}
                  {treino?.error && <p className="text-[11.5px] text-diff-del-fg">{treino.error}</p>}
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    {e.metricas
                      ? `Último treino (${e.metricas.quando}): reconheceu ${Math.round(e.metricas.acerto * 100)}% dos “Forja” de vozes que nunca ouviu, acendeu à toa em ${(e.metricas.falsos * 100).toFixed(1)}% das falas parecidas e ${e.metricas.alarmes_fala_corrida}× em ${e.metricas.minutos_fala_corrida} min de conversa corrida. ${e.metricas.vozes} vozes sintéticas${e.metricas.minha_voz ? ` e ${e.metricas.minha_voz} gravações suas (reconhece ${Math.round((e.metricas.minha_voz_acerto ?? 0) * 100)}% delas)` : ""}. `
                      : ""}
                    O treino não precisa de você: usa vozes sintéticas e 20 s do som da casa (uns 5 minutos, na CPU). Gravações da sua voz melhoram o acerto: grave e treine de novo.
                    Com o detector o Whisper ainda confere o nome, então barulho que pareça “Forja” não vira comando.
                  </p>
                </Secao>}

                {aba === "modelos" && <Secao titulo="Modelos favoritos por uso" extra={e.favoritos.length < 12 && opcoes.length > 0 && (
                  <button className="text-[11.5px] text-faint hover:text-fg"
                          onClick={() => {
                            const nome = ["código", "documento", "pesquisa", "conversa", "imagem"].find((n) => !e.favoritos.some((f) => f.nome === n)) ?? `uso ${e.favoritos.length + 1}`;
                            grava({ favoritos: [...e.favoritos, { nome, provider: opcoes[0].provider, model: opcoes[0].model }] });
                          }}>+ Adicionar</button>
                )}>
                  {e.favoritos.map((f, i) => (
                    <div key={i} className="flex flex-col gap-1.5 rounded-[10px] border border-line p-1.5">
                      <Caixa rotulo="Uso">
                        <input className={campo} defaultValue={f.nome} placeholder="código"
                               onBlur={(ev) => ev.target.value.trim() !== f.nome && mudaFav(i, { nome: ev.target.value.trim() })} />
                      </Caixa>
                      <Caixa rotulo="Modelo">
                        <select className={`${campo} cursor-pointer`} value={`${f.provider}|${f.model}`}
                                onChange={(ev) => { const [provider, ...m] = ev.target.value.split("|"); mudaFav(i, { provider, model: m.join("|") }); }}>
                          {grupos.map((g) => (
                            <optgroup key={g} label={g}>
                              {opcoes.filter((o) => o.grupo === g).map((o) => <option key={`${o.provider}|${o.model}`} value={`${o.provider}|${o.model}`}>{o.model.split(/[\\/]/).pop()}</option>)}
                            </optgroup>
                          ))}
                        </select>
                      </Caixa>
                      <button aria-label={`Remover o favorito ${f.nome || i + 1}`} onClick={() => mudaFav(i, null)}
                              className="self-end rounded-[7px] px-2 py-0.5 text-[11.5px] text-faint hover:bg-raised hover:text-diff-del-fg">Remover</button>
                    </div>
                  ))}
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    Ao pedir “Forja, abra um chat no Forja sobre…” ou um trabalho no modo agente, ele pergunta com qual modelo seguir:
                    responda o nome do modelo, “o de sempre” (o do último envio) ou um destes usos, como “código” ou “pesquisa”.
                  </p>
                </Secao>}

                {aba === "dados" && <Secao titulo="E-mail e agenda">
                  <ContaGoogle onError={onError} />
                  <div className="grid grid-cols-2 gap-1.5">
                    <Caixa rotulo="Resumo do dia, todo dia às">
                      <input type="time" className={`${campo} cursor-pointer`} value={e.resumo_hora}
                             onChange={(ev) => grava({ resumo_hora: ev.target.value })} />
                    </Caixa>
                    <Caixa rotulo="Cidade do clima">
                      <input className={campo} defaultValue={e.cidade} placeholder="ex.: Campinas"
                             onBlur={(ev) => ev.target.value.trim() !== e.cidade && grava({ cidade: ev.target.value.trim() })} />
                    </Caixa>
                  </div>
                  <span className="text-[11.5px] font-medium text-fg-2">Avisar quando chegar e-mail de</span>
                  {e.avisos_email.map((a) => (
                    <div key={a} className="flex items-center gap-2 rounded-[8px] border border-line bg-surface px-2.5 py-1.5">
                      <span className="min-w-0 flex-1 truncate text-[12px] text-fg">{a}</span>
                      <button aria-label={`Tirar ${a}`} className="text-[11.5px] text-faint hover:text-diff-del-fg"
                              onClick={() => grava({ avisos_email: e.avisos_email.filter((x) => x !== a) })}>tirar</button>
                    </div>
                  ))}
                  <Caixa rotulo="Remetente ou palavra no assunto (Enter para adicionar)">
                    <input className={campo} placeholder="ex.: chefe@empresa.com, Banco, build failed"
                           onKeyDown={(ev) => {
                             const v = ev.currentTarget.value.trim();
                             if (ev.key === "Enter" && v) { grava({ avisos_email: [...e.avisos_email, v] }); ev.currentTarget.value = ""; }
                           }} />
                  </Caixa>
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    O resumo do dia sai falado (com ele ligado), no Windows e no celular: agenda, e-mails importantes, lembretes,
                    o que o Forja trabalhou durante a noite, o clima e notícias. Os avisos de e-mail conferem a caixa a cada 3 minutos.
                  </p>
                </Secao>}

                {aba === "dados" && <Secao titulo="Receitas de tarefas">
                  <ReceitasTarefas onError={onError} />
                </Secao>}

                {aba === "dados" && <Secao titulo="Documentos e memória">
                  <span className="text-[11.5px] font-medium text-fg-2">
                    Pastas dos seus documentos {e.documentos.indexando ? "(lendo…)" : e.pastas_documentos.length ? `(${e.documentos.arquivos} arquivos)` : ""}
                  </span>
                  {e.pastas_documentos.map((p) => (
                    <div key={p} className="flex items-center gap-2 rounded-[8px] border border-line bg-surface px-2.5 py-1.5">
                      <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-fg" title={p}>{p}</span>
                      <button aria-label={`Tirar ${p}`} className="text-[11.5px] text-faint hover:text-diff-del-fg"
                              onClick={() => grava({ pastas_documentos: e.pastas_documentos.filter((x) => x !== p) })}>tirar</button>
                    </div>
                  ))}
                  <Caixa rotulo="Caminho da pasta (Enter para adicionar): contratos, notas, um cofre do Obsidian">
                    <input className={`${campo} font-mono`} placeholder="C:\Users\voce\Documents"
                           onKeyDown={(ev) => {
                             const v = ev.currentTarget.value.trim();
                             if (ev.key === "Enter" && v) { grava({ pastas_documentos: [...e.pastas_documentos, v] }); ev.currentTarget.value = ""; }
                           }} />
                  </Caixa>
                  {e.documentos.erro && <span className="text-[11.5px] text-diff-del-fg">{e.documentos.erro}</span>}
                  <span className="mt-1 text-[11.5px] font-medium text-fg-2">O que ele sabe de você</span>
                  {memorias.length === 0 && <span className="text-[11.5px] text-faint">Nada ainda. Diga “Forja, lembra que…”.</span>}
                  {memorias.map((m) => (
                    <div key={m.slug} className="flex items-center gap-2 rounded-[8px] border border-line bg-surface px-2.5 py-1.5">
                      <span className="min-w-0 flex-1 truncate text-[12px] text-fg" title={m.description}>
                        <span className="font-medium">{m.name}</span> <span className="text-muted">{m.description}</span>
                      </span>
                      <button aria-label={`Esquecer ${m.name}`} className="text-[11.5px] text-faint hover:text-diff-del-fg"
                              onClick={() => api.post("/memory/personal/delete", { names: [m.slug] }).then(carregaMemorias).catch((er) => onError(er.message))}>esquecer</button>
                    </div>
                  ))}
                  <span className="mt-1 text-[11.5px] font-medium text-fg-2">Notas</span>
                  {e.notas.length === 0 && <span className="text-[11.5px] text-faint">Nenhuma. Diga “Forja, anota: …”.</span>}
                  {[...e.notas].reverse().map((n) => (
                    <div key={n.id} className="flex items-center gap-2 rounded-[8px] border border-line bg-surface px-2.5 py-1.5">
                      <span className="shrink-0 font-mono text-[11px] text-fg-2">{quandoCurto(n.quando)}</span>
                      <span className="min-w-0 flex-1 truncate text-[12px] text-fg" title={n.texto}>{n.texto}</span>
                      <button aria-label={`Apagar ${n.texto}`} className="text-[11.5px] text-faint hover:text-diff-del-fg"
                              onClick={() => api.del(`/assistente/notas/${n.id}`).then(carrega).catch((er) => onError(er.message))}>apagar</button>
                    </div>
                  ))}
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    Tudo fica no seu PC: os documentos viram um índice local, e as memórias são as mesmas que o agente do Forja usa.
                    A conversa falada continua sem ser guardada; só vale o que você pedir para guardar.
                  </p>
                </Secao>}

                {aba === "atividade" && <Secao titulo="Acompanhando">
                  {e.acompanhar.length === 0 && <span className="text-[11.5px] text-faint">Nada. Diga “Forja, me avisa quando o PS5 baixar de 3 mil”.</span>}
                  {e.acompanhar.map((a) => (
                    <div key={a.id} className="flex items-center gap-2 rounded-[8px] border border-line bg-surface px-2.5 py-1.5">
                      <span className="min-w-0 flex-1 truncate text-[12px] text-fg">
                        {a.tipo === "preco"
                          ? <>{a.alvo} <span className="text-muted">abaixo de R$ {a.limite?.toLocaleString("pt-BR")}{a.ultimo ? ` · agora R$ ${a.ultimo.toLocaleString("pt-BR")}` : ""}</span></>
                          : <>{a.alvo} <span className="text-muted">versão nova{a.base ? ` (hoje ${a.base})` : ""}</span></>}
                      </span>
                      <button aria-label={`Parar de acompanhar ${a.alvo}`} className="text-[11.5px] text-faint hover:text-diff-del-fg"
                              onClick={() => api.del(`/assistente/acompanhar/${a.id}`).then(carrega).catch((er) => onError(er.message))}>parar</button>
                    </div>
                  ))}
                  <p className="text-[11.5px] leading-relaxed text-faint">Confere a cada 3 horas e avisa uma vez (falado, no Windows e no celular).</p>
                </Secao>}

                {aba === "atividade" && <Secao titulo="O que ele fez">
                  {e.acoes.length === 0 && <span className="text-[11.5px] text-faint">Ações por voz aparecem aqui (sem o que foi conversado).</span>}
                  {e.acoes.map((a) => (
                    <div key={a.id} className="flex items-center gap-2 rounded-[8px] border border-line bg-surface px-2.5 py-1.5">
                      <span className="shrink-0 font-mono text-[11px] text-fg-2">{new Date(a.t * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}</span>
                      <span className={`min-w-0 flex-1 truncate text-[12px] ${a.desfeito ? "text-faint line-through" : "text-fg"}`} title={a.resumo}>{a.resumo}</span>
                      {a.pode_desfazer && (
                        <button className="text-[11.5px] text-faint hover:text-fg"
                                onClick={() => api.post("/assistente/desfazer", { id: a.id }).then(carrega).catch((er) => onError(er.message))}>desfazer</button>
                      )}
                    </div>
                  ))}
                  <p className="text-[11.5px] leading-relaxed text-faint">“Forja, desfaz” volta a última que dá: volume, mudo, saída de som, lembrete, nota, memória, card, acompanhamento e evento (até 2 horas).</p>
                </Secao>}

                {aba === "atividade" && <Secao titulo="Lembretes">
                  {e.lembretes.map((l) => (
                    <div key={l.id} className="flex items-center gap-2 rounded-[8px] border border-line bg-surface px-2.5 py-1.5">
                      <span className="shrink-0 font-mono text-[11px] text-fg-2">{quandoCurto(l.quando)}</span>
                      <span className="min-w-0 flex-1 truncate text-[12px] text-fg" title={l.texto}>{l.texto || "(sem texto)"}</span>
                      <button aria-label={`Cancelar ${l.texto}`} className="text-[11.5px] text-faint hover:text-diff-del-fg"
                              onClick={() => api.del(`/assistente/lembretes/${l.id}`).then(carrega).catch((er) => onError(er.message))}>cancelar</button>
                    </div>
                  ))}
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    “Forja, me lembre de lavar a louça às 14h”: na hora, notificação no Windows, no celular (Forja Mobile) e a voz dele, se estiver ligado.
                  </p>
                </Secao>}

                {aba === "modelos" && <Secao titulo="Imagem, vídeo e projetos">
                  <Caixa rotulo="Modelo favorito de imagem (“Forja, crie uma imagem de…”)">
                    <select className={`${campo} cursor-pointer`} value={e.imagem} onChange={(ev) => grava({ imagem: ev.target.value })}>
                      <option value="">nenhum: ele avisa</option>
                      {imagens.map((m) => <option key={m.path} value={m.path}>{m.name}</option>)}
                    </select>
                  </Caixa>
                  <Caixa rotulo="Modelo favorito de vídeo (“Forja, gere um vídeo de…”)">
                    <select className={`${campo} cursor-pointer`} value={e.video} onChange={(ev) => grava({ video: ev.target.value })}>
                      <option value="">nenhum: ele avisa</option>
                      {local?.video_models.map((m) => <option key={m.path} value={m.path}>{m.name}</option>)}
                    </select>
                  </Caixa>
                  <Caixa rotulo="Pasta dos projetos novos (vazio = ~/Forja): cada um vai em chat-agente/ ou chat-maestro/">
                    <input className={`${campo} font-mono`} defaultValue={e.pasta_projetos} placeholder="C:\Projetos"
                           onBlur={(ev) => ev.target.value.trim() !== e.pasta_projetos && grava({ pasta_projetos: ev.target.value.trim() })} />
                  </Caixa>
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    “Forja, no modo agente crie um site de ecommerce”: se uma conversa já trabalha numa pasta com nome parecido, ele
                    pergunta se continua nela ou cria uma nova. Qualquer outra pergunta ele só responde falando, pesquisando na web se precisar, sem abrir nem mexer em nada no Forja.
                  </p>
                </Secao>}
              </>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}

/** A conta Google do assistente (Gmail e Agenda): o mesmo projeto do Google Cloud do YouTube serve. O login acontece no
 *  navegador do sistema; a tela confere até a conta aparecer. */
function ContaGoogle(props: { onError: (m: string) => void }) {
  const { onError } = props;
  const [c, setC] = useState<GoogleConta | null>(null);
  const [id, setId] = useState("");
  const [chave, setChave] = useState("");
  const [esperando, setEsperando] = useState(false);
  const carregar = useCallback(() => api.get<GoogleConta>("/google").then((r) => { setC(r); setId((v) => v || r.client_id); return r; }), []);
  useEffect(() => { carregar().catch(() => setC(null)); }, [carregar]);
  useEffect(() => {   // o login acontece no navegador: confere até a conta aparecer (ou 3 min)
    if (!esperando) return;
    const t = setInterval(() => carregar().then((r) => r.conectado && setEsperando(false)).catch(() => {}), 2000);
    const fim = setTimeout(() => setEsperando(false), 180_000);
    return () => { clearInterval(t); clearTimeout(fim); };
  }, [esperando, carregar]);
  if (!c) return null;
  const tenta = (fn: () => Promise<unknown>) => fn().catch((er: any) => onError(er.message));
  const botao = "rounded-[8px] border border-line px-2.5 py-1 text-[12px] text-fg hover:bg-surface-2 disabled:opacity-40";
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 rounded-[8px] border border-line bg-surface px-2.5 py-1.5">
        <span className={`size-1.5 shrink-0 rounded-full ${c.conectado ? "bg-ok" : "bg-faint"}`} />
        <span className="min-w-0 flex-1 truncate text-[12px] text-fg">
          {c.conectado ? (c.email ?? "Conectado") : esperando ? "Termine o login no navegador…" : "Conta Google não conectada"}
        </span>
        {c.conectado ? <button className={botao} onClick={() => tenta(() => api.del("/google").then(carregar))}>Desconectar</button>
          : <button className={botao} disabled={!c.client_id || !c.tem_chave}
                    onClick={() => tenta(async () => {
                      const r = await api.post<{ url: string }>("/google/conectar", {});
                      window.open(r.url);   // abre no navegador do sistema
                      setEsperando(true);
                    })}>Conectar</button>}
      </div>
      {!c.conectado && (
        <>
          {c.pode_copiar_youtube && (
            <button className={`${botao} self-start`} onClick={() => tenta(() => api.post("/google/copiar-youtube", {}).then(carregar))}>
              Usar o projeto do Google do YouTube
            </button>
          )}
          <Caixa rotulo="ID do cliente (Google Cloud)">
            <input className={`${campo} font-mono`} value={id} spellCheck={false} placeholder="….apps.googleusercontent.com"
                   onChange={(ev) => setId(ev.target.value)} />
          </Caixa>
          <Caixa rotulo="Chave secreta do cliente">
            <input className={`${campo} font-mono`} type="password" value={chave} placeholder={c.tem_chave ? "guardada (cifrada)" : "GOCSPX-…"}
                   onChange={(ev) => setChave(ev.target.value)} />
          </Caixa>
          {(id !== c.client_id || chave) && (
            <button className={`${botao} self-end`} disabled={!id}
                    onClick={() => tenta(() => api.put<GoogleConta>("/google", { client_id: id, client_secret: chave }).then((r) => { setC(r); setChave(""); }))}>
              Salvar credenciais
            </button>
          )}
          <p className="text-[11.5px] leading-relaxed text-faint">
            Uma vez só: em console.cloud.google.com, no projeto (pode ser o mesmo do YouTube), ative a “Gmail API” e a “Google
            Calendar API”; na tela de consentimento (tipo Externo) publique em produção, para o acesso não vencer em 7 dias; crie
            uma credencial OAuth “App para computador”, cole o ID e a chave aqui e conecte. O Google avisa que o app não foi
            verificado: é o seu próprio projeto, siga em “Avançado”.
          </p>
        </>
      )}
    </div>
  );
}

/** "14:00" hoje, "amanhã 8:30", "26/10 10:00". */
function quandoCurto(iso: string): string {
  const q = new Date(iso), hoje = new Date();
  const hora = q.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const dias = Math.round((new Date(q.toDateString()).getTime() - new Date(hoje.toDateString()).getTime()) / 864e5);
  return dias === 0 ? hora : dias === 1 ? `amanhã ${hora}` : `${q.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })} ${hora}`;
}

/** Abas do painel de configurações: cada uma mostra suas seções abertas, sem recolher nada. */
const ABAS = [["voz", "Voz"], ["chamar", "Chamar"], ["modelos", "Modelos"], ["dados", "Dados"], ["atividade", "Atividade"]] as const;
type Aba = (typeof ABAS)[number][0];

/** Uma seção de uma aba: título, ação opcional à direita, conteúdo sempre à vista; separadas por uma linha. */
function Secao(props: { titulo: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2 border-b border-line px-4 py-4 last:border-b-0">
      <div className="flex min-h-5 items-center gap-2">
        <h3 className="text-[12.5px] font-semibold text-fg">{props.titulo}</h3>
        <span className="ml-auto" />
        {props.extra}
      </div>
      {props.children}
    </section>
  );
}

const MODO_CIRCULO: Record<string, string> = {
  ouvindo: "ouvindo", gravando: "ouvindo", transcrevendo: "ouvindo", carregando: "ouvindo",
  escutando: "escutando", entendendo: "pensando", pensando: "pensando", falando: "falando",
};

const VELOCIDADE: Record<string, number> = { ouvindo: 0.6, escutando: 2, pensando: 0.43 };  // sobre os 6 s da forma

/** O mesmo círculo fluido da sobreposição do canto da tela, grande: parado e respirando quando ouve, com ondas quando
 *  escuta um pedido, apagado quando pensa e pulsando quando fala (estilos em index.css, .forja-circulo). */
function CirculoForja(props: { modo: string; tamanho: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // a velocidade muda pelo playbackRate, que guarda a fase: trocar o animation-duration no CSS fazia as bolhas e o
    // halo pularem de forma e brilho a cada mudança de estado (a tela "piscava")
    const g = VELOCIDADE[props.modo] ?? 1;
    ref.current?.querySelectorAll<HTMLElement>(".g, .halo").forEach((el) => el.getAnimations().forEach((a) =>
      a.updatePlaybackRate(el.classList.contains("halo") ? (props.modo === "falando" ? 2.7 : 1) : g)));
  }, [props.modo]);
  return (
    <div ref={ref} className={`forja-circulo ${props.modo || "desligado"}`} style={{ width: props.tamanho, height: props.tamanho }} aria-hidden>
      <div className="anel" /><div className="anel a2" /><div className="anel a3" />
      <div className="halo" />
      <div className="g g1" /><div className="g g2" /><div className="g g3" />
    </div>
  );
}
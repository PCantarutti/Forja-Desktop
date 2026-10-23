import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { LocalModel } from "../types";
import CartaoEstado, { botaoEstado, botaoEstadoPrimario } from "./CartaoEstado";
import { BarraTopo, Caixa } from "./ImagensView";
import { ChevronDown, Mic, X } from "./icons";

type Estado = {
  ligado: boolean; instalado: boolean; instalando: string; estado: string; erro: string; mic: string; saida: string; mic_nome: string; ouviu: string;
  motor: "kokoro" | "tela"; voz: string; modelo_tts: string; voz_tts: string; whisper: string; humor: string;
  imagem: string; video: string; pasta_projetos: string; favoritos: Favorito[];
  modelo: { provider: string; model: string } | null; ocioso_min: number; atalho: string; raciocinio: string;
  permitidos: { provider: string; model: string }[];
  detector: "whisper" | "treinado" | "ambos"; sensibilidade: number; detector_treinado: boolean; treinando: string; minha_voz: number;
  metricas: { acerto: number; falsos: number; minha_voz_acerto: number | null; alarmes_fala_corrida: number; minutos_fala_corrida: number; vozes: number;
              minha_voz: number; quando: string } | null;
  vozes_kokoro: { id: string; nome: string }[]; humores: { id: string; nome: string }[];
  lembretes: { id: string; quando: string; texto: string }[];
};
type Mic = { id: string; nome: string; api: string; tipo: "entrada" | "saida"; padrao: boolean };
type Favorito = { nome: string; provider: string; model: string };
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
    texto: "Comece com “Forja” e fale o pedido (pode dar uma pausa depois do nome). Só o nome: toca um bip e ele ouve o próximo pedido, sem precisar repetir. Se o círculo acendeu e ele não entendeu o pedido, ele diz “Não entendi. Pode repetir?” e continua ouvindo. Liga e desliga no microfone do canto da janela, na bandeja do sistema ou aqui." },
  { titulo: "Perguntar", exemplos: ["Forja, quanto é 15% de 200?", "Forja, quais são as notícias de hoje?", "Forja, e em dólar?"],
    texto: "Responde só falando, sem abrir nem mexer em nada no Forja. Pesquisa na web quando precisa (“Pesquisando.”) e lembra a conversa enquanto o modelo dele estiver carregado, então dá para emendar perguntas." },
  { titulo: "Conversa só na memória", exemplos: ["Forja, salva nossa conversa num chat", "Salvar como chat", "Esquecer"],
    texto: "Nada do que vocês conversam vai para o disco nem para o log: some quando o modelo dele descarrega. Para guardar, peça para transformar a conversa num chat do Forja (ou use o botão Salvar como chat na tela dele). Esquecer apaga na hora." },
  { titulo: "Tela do assistente", exemplos: ["botão do microfone", "Como usar", "Configurações"],
    texto: "O círculo grande mostra o que ele está fazendo; o botão do microfone fala com ele na hora (como o atalho); embaixo, a transcrição do que você disse e do que ele respondeu, e um campo para escrever. As configurações ficam no painel da direita, que recolhe." },
  { titulo: "Criar imagem", exemplos: ["Forja, crie uma imagem de um gato esquiando"],
    texto: "Abre uma conversa nova em Imagem e gera com o seu modelo favorito de imagem (em Comandos, abaixo)." },
  { titulo: "Gerar vídeo", exemplos: ["Forja, gere um vídeo de um cachorro surfando"],
    texto: "O mesmo na tela Vídeo, com o modelo favorito de vídeo." },
  { titulo: "Abrir um chat no Forja", exemplos: ["Forja, abra um chat no Forja sobre receitas de bolo", "Forja, inicia uma conversa nova"],
    texto: "Pergunta com qual modelo seguir, abre a conversa no Chat e já manda o assunto como primeira mensagem." },
  { titulo: "Trabalhar no modo agente ou na Maestro", exemplos: ["Forja, no modo agente crie um site de ecommerce", "Forja, crie um jogo para mim no modo agente", "Forja, pede pro agente fazer um script", "Forja, no maestro crie um sistema de estoque"],
    texto: "Se uma conversa já trabalha numa pasta com nome parecido, pergunta se continua nela ou cria uma pasta nova (em chat-agente/ ou chat-maestro/ dentro da pasta dos projetos). Depois pergunta o modelo e começa." },
  { titulo: "Pesquisa profunda", exemplos: ["Forja, faça uma pesquisa profunda sobre carros elétricos", "Forja, pesquise a fundo isso que conversamos"],
    texto: "Abre uma pesquisa na tela Pesquisa (a funda, com fontes). “Isso que conversamos” vira uma pergunta completa a partir da conversa falada. Ele avisa quando terminar. Pesquisa simples (“pesquise o valor do dólar”) ele mesmo responde falando." },
  { titulo: "Escolher o modelo falando", exemplos: ["o de sempre", "qwen 3.6", "gemma 12b", "meu favorito de código", "…usando o modelo qwen 3.6"],
    texto: "Quando ele pergunta “Com qual modelo?”: o nome do modelo, “o de sempre” (o do último envio no Forja) ou um dos seus favoritos por uso. Dizendo “usando o modelo X” no próprio pedido, ele nem pergunta. Não achou? Ele diz os modelos que você tem." },
  { titulo: "Microfone, alto-falante e voz", exemplos: ["Fone do headset", "Voz rápida", "Voz clonada da tela Voz"],
    texto: "Escolha de qual microfone ele ouve e em qual alto-falante ele fala (o fone, por exemplo). Microfone desconectado: ele tenta voltar sozinho e avisa aqui se não der." },
  { titulo: "Personalidade", exemplos: ["Sarcástico", "Ácido", "Leve", "Sem humor"],
    texto: "O humor só aparece onde cabe: pergunta boba sim, assunto sério não, e depois de uma piada as próximas respostas saem sem." },
  { titulo: "Atalho global", exemplos: ["Ctrl+Alt+F"],
    texto: "Defina uma combinação em Atalho global: apertou, ele ouve o pedido sem precisar dizer “Forja”, com o Forja minimizado ou atrás de outra janela. Falando, o atalho interrompe e já ouve. Desligado, ele liga e ouve. Enquanto você fala, o modelo dele já vai carregando (só se a placa estiver livre ou com um modelo parado: trabalho em andamento não é derrubado). Também na bandeja: “Chamar o assistente agora”." },
  { titulo: "Parar e cancelar", exemplos: ["Forja, para", "Forja, cancela", "Forja, silêncio"],
    texto: "Cala e cancela o que estiver fazendo. Um pedido novo também cancela o anterior. Respondendo a uma pergunta dele (“Com qual modelo?”), “cancela” desiste dela." },
  { titulo: "Vê-lo ouvir e responder", exemplos: [],
    texto: "Um círculo no canto inferior direito da tela, mesmo com o Forja minimizado: com ondas saindo quando ele ouviu “Forja” (ou o atalho) e está escutando o pedido, mais apagado enquanto pensa, pulsando enquanto fala." },
  { titulo: "Modelo dele e da placa de vídeo", exemplos: [],
    texto: "Escolha o modelo do assistente em “Modelo do assistente” (vazio: o do último envio no Forja). Abrir chat ou agente com outro modelo tira o do assistente da placa e carrega o novo com o pedido; se outro trabalho estiver usando a placa, ele avisa e começa quando ele terminar. Parado pelo tempo escolhido, o modelo dele sai da placa sozinho. Pergunta falada nunca derruba trabalho do Forja; imagem ou vídeo com trabalho em andamento: ele pergunta antes." },
  { titulo: "Trocar o modelo dele falando", exemplos: ["Forja, troca seu modelo para o Qwen 3.6", "Forja, use o modelo Ornith para responder", "Forja, volta para o de sempre", "Forja, qual modelo você está usando?"],
    texto: "Muda o modelo do assistente na hora (o mesmo de Configurações › Modelo) e já começa a carregar. “O de sempre” volta a seguir o do último envio no Forja. Sem nome, ele pergunta e diz quais você tem." },
  { titulo: "Usar o modelo que já está carregado", exemplos: ["Pode usar se já estiver carregado"],
    texto: "Em Configurações › Modelo, escolha os modelos que ele pode usar: se um deles já estiver carregado e parado, ele responde com ele em vez de carregar o dele (mais rápido, nada troca na placa). Ocupado com trabalho do Forja, não pega." },
  { titulo: "Controlar o PC", exemplos: ["Forja, abre o Spotify", "Forja, fecha o Chrome", "Forja, volume 30", "Forja, aumenta o volume", "Forja, tira o som", "Forja, muda para o fone", "Forja, passa o som pra caixa", "Forja, trava o PC"],
    texto: "Abre qualquer programa do menu Iniciar, fecha um programa como no X (se tiver algo sem salvar, ele pergunta), muda o volume e a saída de som e trava a tela. Só ações dessa lista: nada de comando livre no Windows. “Não perturbe” o Windows não deixa um programa ligar: ele abre a página das notificações." },
  { titulo: "Janelas e música", exemplos: ["Forja, joga essa janela para o outro monitor", "Forja, maximiza o VS Code", "Forja, minimiza tudo", "Forja, pausa a música", "Forja, próxima música", "Forja, o que está tocando?"],
    texto: "“Essa janela” é a que está na frente. Música e vídeo pelo controle de mídia do Windows: vale para Spotify, YouTube no navegador e o que mais mostrar o player do Windows." },
  { titulo: "Pastas e arquivos", exemplos: ["Forja, abre a pasta de downloads", "Forja, acha o PDF do contrato de ontem", "Forja, procura a planilha de gastos", "Forja, onde está o print que eu tirei hoje?"],
    texto: "Procura em Downloads, Documentos, Área de trabalho, Imagens e Vídeos, pelo tipo (PDF, planilha, foto, print...), pelo dia (hoje, ontem, essa semana) e pelas palavras do nome. Abre a pasta com o arquivo selecionado; não apaga nem move nada." },
  { titulo: "Lembretes", exemplos: ["Forja, me lembre de lavar a louça às 14h", "Forja, me lembra de ligar pro João daqui a 20 minutos", "Forja, amanhã às 8 me lembre de tomar o remédio", "Forja, quais são meus lembretes?", "Forja, cancela o lembrete da louça"],
    texto: "Na hora, notificação no Windows, no celular (Forja Mobile) e ele fala, se estiver ligado. Sem horário, ele pergunta “Pra quando?”. Valem “às 2 da tarde”, “em meia hora”, “amanhã”, “na sexta às 10h”. Ficam guardados (somem só quando tocam ou você cancela) e aparecem em Configurações › Lembretes; um perdido com o Forja fechado toca assim que ele abrir." },
  { titulo: "Raciocínio do modelo", exemplos: ["desligado (rápido)", "baixo", "médio", "alto"],
    texto: "Em Modelo do assistente: quanto ele pensa antes de responder. Desligado é o mais rápido; ligado ajuda em conta e lógica, mas a fala demora. O pensamento nunca é falado." },
  { titulo: "Detector treinado de “Forja”", exemplos: ["Só Whisper", "Detector", "Detector + Whisper", "Gravar minha voz"],
    texto: "Um modelo pequeno treinado só para o nome acende o círculo na hora e quase não gasta CPU. Treina sozinho com vozes sintéticas; gravações da sua voz deixam ele bem melhor (ouça e apague as ruins na lista). “Detector + Whisper” acende rápido e não deixa o nome escapar." },
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
export default function AssistenteView(props: { onError: (e: string) => void }) {
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
  const [tutorial, setTutorial] = useState(false);
  const [vivo, setVivo] = useState("desligado");  // o estado do ouvido, rápido: o círculo acompanha a fala
  const [transcricao, setTranscricao] = useState<{ quem: "voce" | "forja"; texto: string }[]>([]);
  const fimConversa = useRef<HTMLDivElement>(null);
  const carregaConversa = useCallback(() => api.get<{ transcricao: { quem: "voce" | "forja"; texto: string }[] }>("/assistente/conversa")
    .then((r) => setTranscricao((a) => (JSON.stringify(a) === JSON.stringify(r.transcricao) ? a : r.transcricao))).catch(() => {}), []);
  useEffect(() => {
    let parado = false;
    const t = setInterval(() => {
      if (document.hidden || parado) return;
      api.get<{ estado: string }>("/assistente/estado").then((r) => setVivo(r.estado)).catch(() => {});
    }, 300);
    const c = setInterval(() => { if (!document.hidden) carregaConversa(); }, 1000);
    carregaConversa();
    // a tela grande mostra o círculo: a sobreposição do canto some enquanto ela estiver à vista
    window.forja?.assistenteVisivel?.(true);
    return () => { parado = true; clearInterval(t); clearInterval(c); window.forja?.assistenteVisivel?.(false); };
  }, [carregaConversa]);
  useEffect(() => { fimConversa.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [transcricao.length]);
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
  useEffect(() => {  // instalando/treinando: a barra anda pelo job; ligado: o estado (ouvindo, falando...) acompanha
    if (!e?.instalando && !e?.ligado && !e?.treinando) return;
    const t = setInterval(() => {
      carrega();
      if (e?.instalando || e?.treinando) api.get<{ jobs: (Job & { done?: number; total?: number })[] }>("/local").then((r) => {
        if (e?.instalando) setJob(r.jobs.find((j) => j.id === e.instalando) ?? null);
        if (e?.treinando) setTreino(r.jobs.find((j) => j.id === e.treinando) ?? null);
      }).catch(() => {});
    }, 1500);
    return () => clearInterval(t);
  }, [e?.instalando, e?.ligado, e?.treinando, carrega]);

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
  const nomeModelo = (m: string) => (m.split(/[\\/]/).pop() ?? m).replace(/\.(gguf|safetensors)$/i, "");
  const resumoModelo = `${e.modelo ? nomeModelo(e.modelo.model) : "o do último envio"} · raciocínio ${e.raciocinio}`
    + (e.permitidos.length ? ` · +${e.permitidos.length} permitido${e.permitidos.length === 1 ? "" : "s"}` : "");
  const resumoOuvir = `${mics.find((m) => m.id === e.mic)?.nome ?? "microfone padrão"} · `
    + (e.motor === "kokoro" ? e.vozes_kokoro.find((v) => v.id === e.voz)?.nome ?? e.voz : "voz da tela Voz");
  const resumoDetector = ({ whisper: "Só Whisper", treinado: "Detector", ambos: "Detector + Whisper" } as const)[e.detector]
    + (e.minha_voz ? ` · ${e.minha_voz} gravaç${e.minha_voz === 1 ? "ão" : "ões"}` : "");
  const resumoComandos = `imagem: ${e.imagem ? nomeModelo(e.imagem) : "nenhum"} · vídeo: ${e.video ? nomeModelo(e.video) : "nenhum"}`;
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
                  : modo === "ouvindo" ? `Diga “Forja, …”${e.atalho ? ` ou aperte ${e.atalho}` : ""}`
                  : ESTADO[vivo] ?? vivo}
              </p>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto flex max-w-[720px] flex-col items-center px-5 pb-6">
              <div className="mt-3 flex w-full flex-col gap-2.5">
                {transcricao.map((x, i) => (
                  <div key={i} className={`flex ${x.quem === "voce" ? "justify-end" : "justify-start"}`}>
                    <p className={`max-w-[80%] rounded-2xl px-3.5 py-2 text-[13.5px] leading-relaxed ${x.quem === "voce" ? "bg-raised text-fg" : "text-fg-2"}`}>
                      {x.texto}
                    </p>
                  </div>
                ))}
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
          <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-3 pt-1.5 pb-4">
            {!e.instalado ? <p className="text-[12px] leading-relaxed text-muted">Instale o assistente para configurá-lo.</p> : (
              <>
                <Grupo id="modelo" titulo="Modelo" resumo={resumoModelo} abertoPadrao>
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
                </Grupo>

                <Grupo id="ouvir" titulo="Ouvir e falar" resumo={resumoOuvir}>
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
                      <option value="medium">medium (preciso, mais lento)</option>
                    </select>
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
                  {e.motor === "kokoro" ? (
                    <Caixa rotulo="Voz">
                      <select className={`${campo} cursor-pointer`} value={e.voz} onChange={(ev) => grava({ voz: ev.target.value })}>
                        {e.vozes_kokoro.map((v) => <option key={v.id} value={v.id}>{v.nome}</option>)}
                      </select>
                    </Caixa>
                  ) : (
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
                </Grupo>

                <Grupo id="personalidade" titulo="Personalidade" resumo={e.humores.find((h) => h.id === e.humor)?.nome ?? ""}>
                  <div className="grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="Humor">
                    {e.humores.map((h) => (
                      <button key={h.id} role="radio" aria-checked={e.humor === h.id} onClick={() => grava({ humor: h.id })}
                              className={`rounded-[9px] border px-3 py-2 text-left text-[12.5px] ${e.humor === h.id ? "border-accent-line bg-accent-soft text-accent-text" : "border-line bg-surface text-muted hover:text-fg"}`}>
                        {h.nome}
                      </button>
                    ))}
                  </div>
                  <p className="text-[11.5px] leading-relaxed text-faint">{DICA_HUMOR[e.humor]} Ele só brinca quando cabe: pergunta boba sim, assunto sério não, e nunca em toda resposta.</p>
                </Grupo>

                <Grupo id="atalho" titulo="Atalho global" resumo={e.atalho || "nenhum"}>
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
                </Grupo>

                <Grupo id="detector" titulo="Detector de “Forja”" resumo={resumoDetector}>
                  <div className="flex rounded-[8px] border border-line bg-surface p-0.5 text-xs" role="radiogroup" aria-label="Como ele reconhece “Forja”">
                    {([["whisper", "Só Whisper", "Transcreve tudo o que o microfone pega e procura “Forja”: não precisa treinar, mas gasta CPU com TV ou conversa por perto, e o círculo demora um pouco mais"],
                       ["treinado", "Detector", "Um modelo pequeno só para “Forja”: quase não gasta CPU e acende na hora; se ele não ouvir o nome, nada acontece"],
                       ["ambos", "Detector + Whisper", "O detector acende o círculo na hora e o Whisper também confere toda fala: se o detector perder o nome, o Whisper pega (gasta CPU como o Só Whisper)"]] as const).map(([id, rot, dica]) => (
                      <button key={id} role="radio" aria-checked={e.detector === id} title={dica} disabled={id !== "whisper" && !e.detector_treinado}
                              onClick={() => grava({ detector: id })}
                              className={`flex-1 rounded-[6px] py-1.5 disabled:opacity-40 ${e.detector === id ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>{rot}</button>
                    ))}
                  </div>
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
                              title="Clique e diga “Forja” uma vez, no seu tom normal. 5 a 10 gravações ajudam o detector com a sua voz."
                              onClick={() => { setGravandoVoz(true); setAvisoGrav(""); api.post<{ minha_voz: number; gravacao: Gravacao }>("/assistente/gravar-voz")
                                .then((r) => { setE((a) => a && { ...a, minha_voz: r.minha_voz }); setAvisoGrav(r.gravacao.aviso ? `Gravação ${r.gravacao.id}: ${r.gravacao.aviso}. Apague e grave de novo.` : ""); carregaGravs(); })
                                .catch((er) => onError(er.message)).finally(() => setGravandoVoz(false)); }}>
                        {gravandoVoz ? "Diga “Forja” agora…" : `Gravar minha voz (${e.minha_voz})`}
                      </button>
                    </div>
                  )}
                  {avisoGrav && <p className="text-[11.5px] text-warn">{avisoGrav}</p>}
                  {gravs.length > 0 && (
                    <Recolhe titulo={`Suas gravações (${gravs.length})`} resumo={`${gravs.filter((g) => g.reconhecida).length} reconhecidas no último treino`} id="gravacoes">
                      {gravs.length > 0 && (
                    <div className="flex flex-col gap-0.5 rounded-[10px] border border-line bg-surface p-1.5" aria-label="Suas gravações">
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
                    </Recolhe>
                  )}
                  {treino?.error && <p className="text-[11.5px] text-diff-del-fg">{treino.error}</p>}
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    {e.metricas
                      ? `Último treino (${e.metricas.quando}): reconheceu ${Math.round(e.metricas.acerto * 100)}% dos “Forja” de vozes que nunca ouviu, acendeu à toa em ${(e.metricas.falsos * 100).toFixed(1)}% das falas parecidas e ${e.metricas.alarmes_fala_corrida}× em ${e.metricas.minutos_fala_corrida} min de conversa corrida. ${e.metricas.vozes} vozes sintéticas${e.metricas.minha_voz ? ` e ${e.metricas.minha_voz} gravações suas (reconhece ${Math.round((e.metricas.minha_voz_acerto ?? 0) * 100)}% delas)` : ""}. `
                      : ""}
                    O treino não precisa de você: usa vozes sintéticas e 20 s do som da casa (uns 5 minutos, na CPU). Gravações da sua voz melhoram o acerto: grave e treine de novo.
                    Com o detector o Whisper ainda confere o nome, então barulho que pareça “Forja” não vira comando.
                  </p>
                </Grupo>

                <Grupo id="favoritos" titulo="Modelos favoritos por uso" resumo={`${e.favoritos.length} favorito${e.favoritos.length === 1 ? "" : "s"}`} extra={e.favoritos.length < 12 && opcoes.length > 0 && (
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
                </Grupo>

                <Grupo id="lembretes" titulo="Lembretes" resumo={e.lembretes.length ? `${e.lembretes.length} marcado${e.lembretes.length === 1 ? "" : "s"}` : "nenhum"}>
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
                </Grupo>

                <Grupo id="comandos" titulo="Imagem, vídeo e projetos" resumo={resumoComandos}>
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
                </Grupo>
              </>
            )}
          </div>
        </aside>
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

function abertoGuardado(chave: string, padrao: boolean): boolean {
  try {
    const v = localStorage.getItem(chave);
    return v === null ? padrao : v === "1";
  } catch {
    return padrao;
  }
}

/** Um grupo do painel de configurações: fechado mostra só o título e um resumo do que está escolhido; lembra se a
 *  pessoa deixou aberto (localStorage, por grupo). */
function Grupo(props: { id: string; titulo: string; resumo?: string; abertoPadrao?: boolean; extra?: React.ReactNode;
                        children: React.ReactNode }) {
  const chave = `forja.assistente.grupo.${props.id}`;
  const [aberto, setAberto] = useState(() => abertoGuardado(chave, !!props.abertoPadrao));
  const alterna = () => setAberto((v) => { try { localStorage.setItem(chave, v ? "0" : "1"); } catch { /* sem storage */ } return !v; });
  return (
    <section className={`rounded-[10px] border ${aberto ? "border-line-strong bg-surface/60" : "border-line"}`}>
      <div className="flex items-center gap-2 px-3 py-2.5">
        <button onClick={alterna} aria-expanded={aberto} className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <ChevronDown className={`size-3.5 shrink-0 text-faint transition-transform duration-200 ${aberto ? "" : "-rotate-90"}`} />
          <span className="shrink-0 text-[12.5px] font-medium text-fg">{props.titulo}</span>
          {!aberto && props.resumo && <span className="min-w-0 truncate text-[11.5px] text-faint" title={props.resumo}>{props.resumo}</span>}
        </button>
        {aberto && props.extra}
      </div>
      {aberto && <div className="flex flex-col gap-2 border-t border-line px-3 pt-2.5 pb-3">{props.children}</div>}
    </section>
  );
}

/** Recolhível dentro de um grupo (a lista de gravações, que cresce). */
function Recolhe(props: { id: string; titulo: string; resumo?: string; children: React.ReactNode }) {
  const chave = `forja.assistente.recolhe.${props.id}`;
  const [aberto, setAberto] = useState(() => abertoGuardado(chave, false));
  const alterna = () => setAberto((v) => { try { localStorage.setItem(chave, v ? "0" : "1"); } catch { /* sem storage */ } return !v; });
  return (
    <div className="rounded-[9px] border border-line bg-surface">
      <button onClick={alterna} aria-expanded={aberto} className="flex w-full items-center gap-2 px-2.5 py-2 text-left">
        <ChevronDown className={`size-3 shrink-0 text-faint transition-transform duration-200 ${aberto ? "" : "-rotate-90"}`} />
        <span className="text-[12px] text-fg-2">{props.titulo}</span>
        {props.resumo && <span className="ml-auto truncate text-[11px] text-faint">{props.resumo}</span>}
      </button>
      {aberto && <div className="border-t border-line p-1.5">{props.children}</div>}
    </div>
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
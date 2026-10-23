import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { LocalModel } from "../types";
import CartaoEstado, { botaoEstado, botaoEstadoPrimario } from "./CartaoEstado";
import { BarraTopo, Caixa, Secao } from "./ImagensView";
import { Mic, X } from "./icons";

type Estado = {
  ligado: boolean; instalado: boolean; instalando: string; estado: string; erro: string; mic: string; saida: string; mic_nome: string; ouviu: string;
  motor: "kokoro" | "tela"; voz: string; modelo_tts: string; voz_tts: string; whisper: string; humor: string;
  imagem: string; video: string; pasta_projetos: string; favoritos: Favorito[];
  modelo: { provider: string; model: string } | null; ocioso_min: number; atalho: string; raciocinio: string;
  detector: "whisper" | "treinado"; sensibilidade: number; detector_treinado: boolean; treinando: string; minha_voz: number;
  metricas: { acerto: number; falsos: number; alarmes_fala_corrida: number; minutos_fala_corrida: number; vozes: number;
              minha_voz: number; quando: string } | null;
  vozes_kokoro: { id: string; nome: string }[]; humores: { id: string; nome: string }[];
};
type Mic = { id: string; nome: string; api: string; tipo: "entrada" | "saida"; padrao: boolean };
type Favorito = { nome: string; provider: string; model: string };
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
    texto: "Comece com “Forja”. Só o nome: ele diz “Pois não?” e ouve o próximo pedido sem precisar repetir. Liga e desliga no microfone do canto da janela, na bandeja do sistema ou aqui." },
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
  { titulo: "Trabalhar no modo agente", exemplos: ["Forja, no modo agente crie um site de ecommerce"],
    texto: "Se uma conversa já trabalha numa pasta com nome parecido, pergunta se continua nela ou cria uma pasta nova (na pasta dos projetos). Depois pergunta o modelo e começa." },
  { titulo: "Escolher o modelo falando", exemplos: ["o de sempre", "qwen 3.6", "gemma 12b", "meu favorito de código"],
    texto: "Quando ele pergunta “Com qual modelo?”: o nome do modelo, “o de sempre” (o do último envio no Forja) ou um dos seus favoritos por uso." },
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
  { titulo: "Raciocínio do modelo", exemplos: ["desligado (rápido)", "baixo", "médio", "alto"],
    texto: "Em Modelo do assistente: quanto ele pensa antes de responder. Desligado é o mais rápido; ligado ajuda em conta e lógica, mas a fala demora. O pensamento nunca é falado." },
  { titulo: "Detector treinado de “Forja”", exemplos: ["Treinar o detector", "Gravar minha voz"],
    texto: "Em vez de transcrever toda fala da casa, um modelo pequeno treinado só para o nome acende o Whisper. Treina sozinho com vozes sintéticas; gravações da sua voz deixam ele melhor." },
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
        <div className="flex-1 overflow-y-auto">
          {tutorial ? (
            <div className="mx-auto max-w-[720px] px-5 py-5"><Tutorial /></div>
          ) : !e.instalado ? (
            <div className="mx-auto flex min-h-full max-w-md flex-col justify-center px-5 py-8">{instalarBotao}</div>
          ) : (
            <div className="mx-auto flex min-h-full max-w-[720px] flex-col items-center px-5 pt-[6vh] pb-6">
              <CirculoForja modo={modo} />
              <button onClick={chamar} disabled={!!e.erro && !e.ligado}
                      title={modo === "falando" ? "Interromper e falar" : "Falar com o F.O.R.J.A agora (sem dizer “Forja”)"}
                      aria-label="Falar com o assistente agora"
                      className={`mt-7 grid size-14 place-items-center rounded-full transition-colors ${modo === "escutando" ? "bg-accent text-accent-fg" : "border border-line-strong bg-surface text-fg hover:border-accent hover:text-accent-text"}`}>
                <Mic className="size-[22px]" />
              </button>
              <p className="mt-3 h-5 text-center text-[13px] text-muted">
                {e.erro ? <span className="text-diff-del-fg">{e.erro}</span>
                  : !e.ligado ? "Desligado: o botão liga e já ouve"
                  : modo === "escutando" ? "Pode falar…"
                  : modo === "ouvindo" ? `Diga “Forja, …”${e.atalho ? ` ou aperte ${e.atalho}` : ""}`
                  : ESTADO[vivo] ?? vivo}
              </p>

              <div className="mt-8 flex w-full flex-col gap-2.5">
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
          <div className="flex min-h-0 flex-1 flex-col gap-[18px] overflow-y-auto px-4 pt-1.5 pb-4">
            {!e.instalado ? <p className="text-[12px] leading-relaxed text-muted">Instale o assistente para configurá-lo.</p> : (
              <>
                <Secao titulo="Modelo do assistente">
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
                </Secao>

                <Secao titulo="Atalho global">
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
                </Secao>

                <Secao titulo="Detector de “Forja”">
                  <div className="flex rounded-[8px] border border-line bg-surface p-0.5 text-xs" role="radiogroup" aria-label="Como ele reconhece “Forja”">
                    {([["whisper", "Whisper em toda fala", "Transcreve tudo o que o microfone pega e procura “Forja”: não precisa treinar, mas gasta CPU com TV ou conversa por perto"],
                       ["treinado", "Detector treinado", "Um modelo pequeno só para “Forja”: quase não gasta CPU, e o Whisper só roda quando ele ouve o nome"]] as const).map(([id, rot, dica]) => (
                      <button key={id} role="radio" aria-checked={e.detector === id} title={dica} disabled={id === "treinado" && !e.detector_treinado}
                              onClick={() => grava({ detector: id })}
                              className={`flex-1 rounded-[6px] py-1.5 disabled:opacity-40 ${e.detector === id ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>{rot}</button>
                    ))}
                  </div>
                  {e.detector === "treinado" && (
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
                              onClick={() => { setGravandoVoz(true); api.post<{ minha_voz: number }>("/assistente/gravar-voz")
                                .then((r) => setE((a) => a && { ...a, minha_voz: r.minha_voz })).catch((er) => onError(er.message)).finally(() => setGravandoVoz(false)); }}>
                        {gravandoVoz ? "Diga “Forja” agora…" : `Gravar minha voz (${e.minha_voz})`}
                      </button>
                      {e.minha_voz > 0 && !gravandoVoz && (
                        <button className="text-[11.5px] text-faint hover:text-diff-del-fg"
                                onClick={() => api.del<{ minha_voz: number }>("/assistente/gravar-voz").then((r) => setE((a) => a && { ...a, minha_voz: r.minha_voz })).catch((er) => onError(er.message))}>apagar gravações</button>
                      )}
                    </div>
                  )}
                  {treino?.error && <p className="text-[11.5px] text-diff-del-fg">{treino.error}</p>}
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    {e.metricas
                      ? `Último treino (${e.metricas.quando}): reconheceu ${Math.round(e.metricas.acerto * 100)}% dos “Forja” de vozes que nunca ouviu, acendeu à toa em ${(e.metricas.falsos * 100).toFixed(1)}% das falas parecidas e ${e.metricas.alarmes_fala_corrida}× em ${e.metricas.minutos_fala_corrida} min de conversa corrida. ${e.metricas.vozes} vozes sintéticas${e.metricas.minha_voz ? ` e ${e.metricas.minha_voz} gravações suas` : ""}. `
                      : ""}
                    O treino não precisa de você: usa vozes sintéticas e 20 s do som da casa (uns 5 minutos, na CPU). Gravações da sua voz melhoram o acerto: grave e treine de novo.
                    Com o detector o Whisper ainda confere o nome, então barulho que pareça “Forja” não vira comando.
                  </p>
                </Secao>

                <Secao titulo="Ouvir">
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
                </Secao>

                <Secao titulo="Voz">
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
                </Secao>

                <Secao titulo="Personalidade">
                  <div className="grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="Humor">
                    {e.humores.map((h) => (
                      <button key={h.id} role="radio" aria-checked={e.humor === h.id} onClick={() => grava({ humor: h.id })}
                              className={`rounded-[9px] border px-3 py-2 text-left text-[12.5px] ${e.humor === h.id ? "border-accent-line bg-accent-soft text-accent-text" : "border-line bg-surface text-muted hover:text-fg"}`}>
                        {h.nome}
                      </button>
                    ))}
                  </div>
                  <p className="text-[11.5px] leading-relaxed text-faint">{DICA_HUMOR[e.humor]} Ele só brinca quando cabe: pergunta boba sim, assunto sério não, e nunca em toda resposta.</p>
                </Secao>

                <Secao titulo="Modelos favoritos por uso" extra={e.favoritos.length < 12 && opcoes.length > 0 && (
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
                </Secao>

                <Secao titulo="Comandos">
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
                  <Caixa rotulo="Pasta dos projetos novos do agente (vazio = ao lado da pasta padrão)">
                    <input className={`${campo} font-mono`} defaultValue={e.pasta_projetos} placeholder="C:\Projetos"
                           onBlur={(ev) => ev.target.value.trim() !== e.pasta_projetos && grava({ pasta_projetos: ev.target.value.trim() })} />
                  </Caixa>
                  <p className="text-[11.5px] leading-relaxed text-faint">
                    “Forja, no modo agente crie um site de ecommerce”: se uma conversa já trabalha numa pasta com nome parecido, ele
                    pergunta se continua nela ou cria uma nova. Qualquer outra pergunta ele só responde falando, pesquisando na web se precisar, sem abrir nem mexer em nada no Forja.
                  </p>
                </Secao>
              </>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}

const MODO_CIRCULO: Record<string, string> = {
  ouvindo: "ouvindo", gravando: "ouvindo", transcrevendo: "ouvindo", carregando: "ouvindo",
  escutando: "escutando", entendendo: "pensando", pensando: "pensando", falando: "falando",
};

/** O mesmo círculo fluido da sobreposição do canto da tela, grande: parado e respirando quando ouve, com ondas quando
 *  escuta um pedido, apagado quando pensa e pulsando quando fala (estilos em index.css, .forja-circulo). */
function CirculoForja(props: { modo: string }) {
  return (
    <div className={`forja-circulo ${props.modo || "desligado"}`} aria-hidden>
      <div className="anel" /><div className="anel a2" /><div className="anel a3" />
      <div className="halo" />
      <div className="g g1" /><div className="g g2" /><div className="g g3" />
    </div>
  );
}
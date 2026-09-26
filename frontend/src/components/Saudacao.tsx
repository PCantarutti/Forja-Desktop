import Abertura from "./Abertura";
import { LogoMark } from "./Logo";

/** Tela vazia (conversa nova, Imagens, Vídeo): logo grande, título, subtítulo e a nota da seção numa
 *  etiqueta, tudo centralizado. `children` vem embaixo (ex.: os exemplos do Vídeo). Com `animando`, a logo
 *  toca a abertura do app (o primeiro prompt da conversa acabou de sair) e chama `onFimAnimacao` no fim. */
export default function Saudacao(props: {
  titulo: string;
  sub: string;
  nota?: React.ReactNode;
  children?: React.ReactNode;
  animando?: boolean;
  onFimAnimacao?: () => void;
}) {
  const logo = "mb-6 size-28 text-fg";
  return (
    <div className="mt-[14vh] flex flex-col items-center text-center">
      {props.animando ? (
        // clique pula a animação
        <button onClick={props.onFimAnimacao} title="Pular" className="cursor-default">
          <Abertura className={logo} onFim={() => props.onFimAnimacao?.()} />
        </button>
      ) : (
        <LogoMark className={logo} title="Forja" />
      )}
      <h1 className="text-[30px] font-semibold leading-tight tracking-tight text-fg">{props.titulo}</h1>
      <p className="mt-1 max-w-xl text-[22px] leading-snug text-muted">{props.sub}</p>
      {props.nota && (
        <div className="mt-6 inline-flex max-w-xl items-center gap-2 rounded-full border border-line bg-surface px-3.5 py-1.5 text-[13px] text-fg-2">
          {props.nota}
        </div>
      )}
      {props.children}
    </div>
  );
}

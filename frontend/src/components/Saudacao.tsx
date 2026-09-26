import { LogoMark } from "./Logo";

/** Tela vazia (conversa nova, Imagens, Vídeo): logo grande, título, subtítulo e a nota da seção numa
 *  etiqueta, tudo centralizado. `children` vem embaixo (ex.: os exemplos do Vídeo). No primeiro envio a
 *  AberturaSobreposta usa o `ref` para copiar esta tela e fazer a logo ([data-logo]) voar. */
export default function Saudacao(props: {
  titulo: string;
  sub: string;
  nota?: React.ReactNode;
  children?: React.ReactNode;
  ref?: React.Ref<HTMLDivElement>;
}) {
  return (
    <div ref={props.ref} className="mt-[14vh] flex flex-col items-center text-center">
      <div data-logo className="mb-6 size-40 text-fg"><LogoMark className="size-full" title="Forja" /></div>
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

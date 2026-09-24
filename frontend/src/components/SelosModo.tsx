import type { ModoVideo } from "../types";

/** T · I · F: os modos que um Wan faz (texto → vídeo, imagem → vídeo, primeiro e último quadro). */
export default function SelosModo({ modos }: { modos: ModoVideo[] }) {
  const selos: [ModoVideo, string, string][] = [["t2v", "T", "texto → vídeo"], ["i2v", "I", "imagem → vídeo"], ["flf2v", "F", "primeiro e último quadro"]];
  return (
    <span className="flex shrink-0 gap-0.5">
      {selos.map(([m, l, d]) => (
        <span
          key={m}
          title={modos.includes(m) ? d : `não faz ${d}`}
          className={`grid size-4 place-items-center rounded text-[9px] font-semibold ${modos.includes(m) ? "bg-sky-400/15 text-sky-300" : "text-faint/40"}`}
        >
          {l}
        </span>
      ))}
    </span>
  );
}

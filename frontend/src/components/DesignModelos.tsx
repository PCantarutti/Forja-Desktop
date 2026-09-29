import { useEffect, useState } from "react";
import { api } from "../api";
import { Miniatura } from "./DesignVariacoes";
import { Trash } from "./icons";

// Galeria do projeto vazio: pontos de partida (um pedido bem escrito, que você ajusta antes de mandar)
// e os seus modelos (designs guardados: o projeto novo nasce com ele como v1, na hora e sem IA).

export type Modelo = { id: string; nome: string; tipo: string; criado: string; tamanho: number };

const PONTOS: { nome: string; tipo: string; pedido: string }[] = [
  { nome: "Landing page", tipo: "site", pedido: "Landing page de [produto/serviço] para [público]: hero com proposta de valor e botão de ação, 3 benefícios, como funciona em passos, depoimentos, preços em 3 planos, perguntas frequentes e rodapé. Tom [confiável/descontraído]." },
  { nome: "Painel (dashboard)", tipo: "site", pedido: "Painel de [área, ex.: vendas] com barra lateral de navegação, cabeçalho com busca e perfil, 4 cartões de indicadores com variação, um gráfico de linha do mês, tabela das últimas [pedidos/transações] com status e uma lista de alertas." },
  { nome: "Apresentação", tipo: "slides", pedido: "Apresentação de 8 slides sobre [tema] para [público]: capa, problema, solução, como funciona, números, comparação com alternativas, próximos passos e encerramento com contato." },
  { nome: "Protótipo de app", tipo: "prototipo", pedido: "Protótipo de app de celular para [objetivo] com as telas: entrada/login, início com lista de [itens], detalhe de um item, carrinho ou formulário, e perfil. Botões levam de uma tela para a outra." },
  { nome: "Portfólio", tipo: "site", pedido: "Portfólio de [profissão, ex.: fotógrafa] com apresentação curta e foto, grade de 6 trabalhos com título e categoria, sobre mim com experiência, clientes/logos, contato com formulário e redes sociais." },
  { nome: "Loja / produto", tipo: "site", pedido: "Página de produto de [produto]: galeria de fotos, nome, preço, variações (cor/tamanho), botão de comprar, benefícios em ícones, descrição detalhada, avaliações de clientes e produtos relacionados." },
  { nome: "Artigo / blog", tipo: "site", pedido: "Página de artigo de blog sobre [tema]: cabeçalho com título, autor e data, imagem de capa, texto com intertítulos, citação em destaque, caixa de dicas, artigos relacionados e assinatura da newsletter." },
  { nome: "Site com páginas", tipo: "paginas", pedido: "Site institucional de [empresa] com menu no topo e rodapé comuns. Página inicial com hero, serviços e chamada para contato; depois crio as páginas Sobre e Contato com o botão + Página." },
];
const TIPO: Record<string, string> = { site: "site", slides: "apresentação", prototipo: "protótipo", paginas: "várias páginas" };

export default function DesignModelos(props: { onPedido: (texto: string) => void; onUsar: (m: Modelo) => void; onErro: (e: string) => void }) {
  const [modelos, setModelos] = useState<Modelo[] | null>(null);
  const [html, setHtml] = useState<Record<string, string>>({});
  useEffect(() => {
    api.get<Modelo[]>("/design-modelos").then(setModelos).catch(() => setModelos([]));
  }, []);
  useEffect(() => {   // miniaturas: um HTML por vez (podem ter fotos embutidas)
    for (const m of modelos ?? []) {
      if (html[m.id] !== undefined) continue;
      setHtml((h) => ({ ...h, [m.id]: "" }));
      api.get<{ html: string }>(`/design-modelos/${m.id}`).then((d) => setHtml((h) => ({ ...h, [m.id]: d.html }))).catch(() => {});
    }
  }, [modelos]);   // eslint-disable-line react-hooks/exhaustive-deps

  const apagar = async (m: Modelo) => {
    try {
      setModelos(await api.del<Modelo[]>(`/design-modelos/${m.id}`));
    } catch (e: any) {
      props.onErro(e.message);
    }
  };

  return (
    <div className="mt-3 flex flex-col gap-4">
      {!!modelos?.length && (
        <section aria-label="Meus modelos">
          <div className="mb-1.5 font-mono text-[10.5px] tracking-[.08em] text-faint uppercase">Meus modelos</div>
          <div className="grid grid-cols-2 gap-2">
            {modelos.map((m) => (
              <div key={m.id} className="group relative rounded-xl border border-line bg-surface p-1.5 hover:border-accent-line">
                <button onClick={() => props.onUsar(m)} className="block w-full text-left" title="Começar este projeto a partir deste modelo (na hora, sem IA)">
                  {html[m.id] ? <Miniatura html={html[m.id]} titulo={`Miniatura de ${m.nome}`} />
                    : <div className="grid h-[120px] place-items-center rounded-lg bg-raised text-[11px] text-faint">…</div>}
                  <span className="mt-1 block truncate px-0.5 text-[12.5px] text-fg">{m.nome}</span>
                  <span className="block px-0.5 text-[11px] text-faint">{TIPO[m.tipo] ?? m.tipo}</span>
                </button>
                <button onClick={() => apagar(m)} title="Apagar este modelo" aria-label={`Apagar o modelo ${m.nome}`}
                        className="absolute top-2.5 right-2.5 grid size-6 place-items-center rounded-md bg-black/60 text-white opacity-0 group-hover:opacity-100 hover:bg-black/80">
                  <Trash className="size-3.5" />
                </button>
              </div>
            ))}
          </div>
        </section>
      )}
      <section aria-label="Pontos de partida">
        <div className="mb-1.5 font-mono text-[10.5px] tracking-[.08em] text-faint uppercase">Pontos de partida</div>
        <div className="grid grid-cols-2 gap-2">
          {PONTOS.map((p) => (
            <button key={p.nome} onClick={() => props.onPedido(p.pedido)} title="Põe o pedido no campo: troque o que está entre [colchetes] e mande"
                    className="rounded-xl border border-line bg-surface px-3 py-2 text-left hover:border-accent-line hover:bg-raised">
              <span className="block text-[13px] text-fg">{p.nome}</span>
              <span className="mt-0.5 line-clamp-2 text-[11.5px] leading-snug text-faint">{p.pedido}</span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

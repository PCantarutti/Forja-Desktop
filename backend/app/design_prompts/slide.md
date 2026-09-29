<!-- slide v1 — gera (ou refaz) UM slide de uma apresentação (fase 4). -->
Você escreve UM slide de uma apresentação em HTML. O deck já existe: `<head>`, tokens de design em
`:root`, CSS base e os outros slides. Cada slide é um `<section data-slide>` de tamanho FIXO
1920×1080 px (o canvas escala para caber na tela; na exportação cada slide vira uma página).

Formato da resposta: SÓ isto, sem texto antes nem depois e sem cerca de código:
<section data-section="NOME" data-slide> ... </section>
<style> ...regras CSS deste slide... </style>

Regras obrigatórias:
- Exatamente um `<section>` com o `data-section` pedido e o atributo `data-slide`, seguido de no
  máximo um `<style>`.
- O slide já tem 1920×1080, `overflow:hidden` e `position:relative`: não mude largura/altura dele.
  Tudo tem que caber: nada de conteúdo que role. Padding interno generoso (≥ 120px nas laterais).
- Classes com o nome do slide como prefixo (`.s3-titulo`) e seletores começando por elas.
- Cores, fontes, raios e sombras vêm de `var(--...)` dos tokens. Tamanhos de texto em px, grandes:
  título 96–140px, subtítulo 48–64px, corpo 32–40px. Nunca texto abaixo de 28px.
- Nenhum recurso externo. Imagem = bloco com gradiente dos tokens ou SVG inline simples.
- Não use `data-fid`. Se receber o HTML atual do slide para refazer, preserve os `data-fid` dos
  elementos que continuarem.

Princípios:
- Uma ideia por slide. Título curto (até ~8 palavras) que já diz a conclusão.
- No máximo 3 a 4 itens de apoio; prefira um número grande, uma frase ou um diagrama simples a
  uma lista longa.
- Muito espaço vazio, alinhamento consistente entre os slides (mesmas margens e posição do título).
- Contraste alto: texto ≥ 4.5:1 sobre o fundo do slide.

Exemplo curto (estilo, não conteúdo):

<section data-section="s2" data-slide>
  <div class="s2-caixa">
    <p class="s2-rotulo">O problema</p>
    <h2 class="s2-titulo">Metade do pão vendido é de ontem</h2>
    <p class="s2-numero">52%</p>
    <p class="s2-apoio">dos clientes compram depois das 16h, quando o forno já esfriou</p>
  </div>
</section>
<style>
.s2-caixa{position:absolute;inset:120px 160px;display:grid;align-content:center;gap:24px}
.s2-rotulo{margin:0;font-size:32px;letter-spacing:.12em;text-transform:uppercase;color:var(--cor-secundaria)}
.s2-titulo{margin:0;font-size:112px;line-height:1.05;color:var(--cor-texto);max-width:14ch}
.s2-numero{margin:0;font-size:220px;font-weight:800;line-height:1;color:var(--cor-primaria)}
.s2-apoio{margin:0;font-size:40px;color:var(--cor-texto);opacity:.8;max-width:30ch}
</style>

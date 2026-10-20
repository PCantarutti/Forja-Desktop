<!-- casca v1 — a navegação global de um protótipo, escrita UMA vez, antes das telas. -->
Você escreve a NAVEGAÇÃO GLOBAL (a "casca") de um protótipo interativo em HTML: a barra que fica igual em
todas as telas e leva de uma para outra. As telas são escritas depois, uma por vez, e NÃO terão
navegação própria: tudo que é navegação do app está aqui. Um runtime pronto troca de tela e marca o
item ativo.

Formato da resposta: SÓ isto, sem texto antes e sem cerca de código:
<section data-section="casca"> <nav aria-label="Principal"> ... </nav> </section>
<style> ...regras CSS da casca... </style>

Regras obrigatórias:
- Um item por entrada da lista "Itens da navegação", na ordem dada: `<a href="#" data-ir="NOME-DA-TELA">`
  com um ícone SVG inline de traço (viewBox 0 0 24 24, `fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round"`, paths como os do Lucide) e o rótulo
  curto. Use só os nomes de tela recebidos.
- Item ativo: o runtime põe `aria-current="page"` no item da tela atual. Estilize
  `[data-section="casca"] [aria-current="page"]` (cor primária, fundo suave ou indicador) — não marque
  nenhum item no HTML.
- POSIÇÃO não é com você: o Forja fixa a casca (embaixo, na lateral ou no topo) e reserva o espaço dela
  nas telas. Não use `position`, `top`, `bottom`, `left`, `right`, `width: 100vw`, `height: 100vh` nem
  `z-index` na raiz. Cuide do que vai dentro: alinhamento dos itens, ícone, rótulo, estados.
- Navegação "inferior" (app de celular): itens lado a lado, distribuídos (`display:flex;
  justify-content:space-around`), ícone em cima e rótulo pequeno embaixo, alvo de toque ≥ 44×44px,
  borda ou sombra fina em cima. Até 5 itens.
- Navegação "lateral" (painel, app de computador): marca do app no alto (nome com uma parte na cor
  primária), itens em coluna com ícone + rótulo, padding generoso. Abaixo de 900px o Forja leva a casca
  para baixo: com `@media (max-width: 899px)` os itens viram linha, como na inferior, e a marca some.
- Navegação "topo" (site ou app web): marca à esquerda e itens à direita numa linha, altura ~64px;
  abaixo de 720px os rótulos podem sumir ficando só os ícones.
- Classes com o prefixo `casca-`, seletores começando por `[data-section="casca"]` ou por elas.
- Tudo com `var(--...)` dos tokens; fundo `var(--cor-superficie)`. Nenhum recurso externo, nenhum
  `<script>`, nenhum `data-fid`.

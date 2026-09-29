<!-- secao v1 — gera (ou refaz) UMA seção da página (fase 3). -->
Você escreve UMA seção de uma página HTML. A página já existe: `<head>`, tokens de design em
`:root`, CSS base e as outras seções. Você recebe o plano da seção, os tokens e a lista das
outras seções (para não repetir o que elas fazem).

Formato da resposta: SÓ isto, sem texto antes nem depois e sem cerca de código:
<section data-section="NOME"> ... </section>
<style> ...regras CSS desta seção... </style>

Regras obrigatórias:
- Exatamente um `<section>` com o `data-section` pedido, seguido de no máximo um `<style>`.
- Classes com o nome da seção como prefixo (`.hero-titulo`, `.cardapio-grade`) para não colidir
  com as outras seções. Seletores do CSS sempre começando pela seção ou por essas classes.
- Todo valor de cor, fonte, tamanho de texto, espaçamento, raio e sombra vem de `var(--...)` dos
  tokens recebidos. Não crie tokens novos.
- Use `.container` (já existe: largura máxima centralizada) para o conteúdo interno.
- Nenhum recurso externo: nada de URL de imagem, fonte, ícone ou script. Imagem = bloco com
  gradiente dos tokens e um rótulo, ou SVG inline simples.
- Não use `data-fid`. Se receber o HTML atual da seção para refazer, preserve os `data-fid` que
  existirem nos elementos que continuarem.
- Conteúdo real e específico, em português. Nunca lorem ipsum.

Princípios de design:
- Hierarquia: um título dominante (`--texto-2xl`/`--texto-3xl`), apoio menor, texto confortável
  (`--texto-base`/`--texto-lg`, entrelinha 1.5–1.7, até ~70 caracteres por linha).
- Espaçamento só pelos tokens; respiro vertical generoso (`--esp-6`/`--esp-8` entre blocos).
- Contraste acessível: texto normal ≥ 4.5:1 sobre o fundo dele.
- Responsivo: grid/flex que funciona de 360px a 1440px; `@media (max-width: 720px)` quando a
  grade precisa virar coluna.
- Uma ideia principal por seção e no máximo uma chamada para ação.

Exemplos curtos de seções bem feitas (estilo, não conteúdo):

<section data-section="hero">
  <div class="container hero-grade">
    <div>
      <p class="hero-selo">Desde 1998 · Vila Madalena</p>
      <h1 class="hero-titulo">Pão de fermentação natural, saído do forno às 7h</h1>
      <p class="hero-apoio">Encomende até as 20h e retire quentinho na manhã seguinte.</p>
      <a class="hero-cta" href="#contato">Fazer encomenda</a>
    </div>
    <div class="hero-imagem" role="img" aria-label="Pães na bancada"></div>
  </div>
</section>
<style>
.hero-grade{display:grid;grid-template-columns:1.1fr .9fr;gap:var(--esp-6);align-items:center;padding-block:var(--esp-8)}
.hero-selo{color:var(--cor-secundaria);font-size:var(--texto-sm);letter-spacing:.08em;text-transform:uppercase;margin:0 0 var(--esp-2)}
.hero-titulo{font-size:var(--texto-3xl);margin:0 0 var(--esp-3)}
.hero-apoio{font-size:var(--texto-lg);max-width:34ch;margin:0 0 var(--esp-4)}
.hero-cta{display:inline-block;background:var(--cor-primaria);color:var(--cor-superficie);padding:var(--esp-3) var(--esp-4);border-radius:var(--raio-md);text-decoration:none;font-weight:600}
.hero-imagem{aspect-ratio:4/3;border-radius:var(--raio-lg);background:linear-gradient(135deg,var(--cor-primaria),var(--cor-secundaria));box-shadow:var(--sombra-md)}
@media (max-width:720px){.hero-grade{grid-template-columns:1fr}}
</style>

<section data-section="depoimentos">
  <div class="container">
    <h2 class="depoimentos-titulo">Quem prova, volta</h2>
    <div class="depoimentos-grade">
      <figure class="depoimentos-card"><blockquote>“O croissant mais leve da cidade.”</blockquote><figcaption>Marina, cliente há 6 anos</figcaption></figure>
      <figure class="depoimentos-card"><blockquote>“Encomendo o pão de sábado toda semana.”</blockquote><figcaption>Rafael, Pinheiros</figcaption></figure>
    </div>
  </div>
</section>
<style>
[data-section="depoimentos"]{background:var(--cor-superficie);padding-block:var(--esp-8)}
.depoimentos-titulo{font-size:var(--texto-2xl);text-align:center;margin:0 0 var(--esp-6)}
.depoimentos-grade{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:var(--esp-4)}
.depoimentos-card{margin:0;padding:var(--esp-4);border:1px solid var(--cor-borda);border-radius:var(--raio-md)}
.depoimentos-card blockquote{margin:0 0 var(--esp-3);font-size:var(--texto-lg)}
.depoimentos-card figcaption{color:var(--cor-secundaria);font-size:var(--texto-sm)}
</style>

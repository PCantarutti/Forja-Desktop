# Redesign da UI — checklist

Handoff: `C:\Projetos\Forja\design_handoff_forja_redesign`. Branch `feat/redesign` (worktree
`forja-desktop/.claude/worktrees/redesign`). Backend intocado nesta fase.

## Feito (front)
- [x] Tokens, temas (`data-theme`), fontes Atkinson/JetBrains/IBM Plex empacotadas (offline)
- [x] Paletas Tailwind remapeadas para os estados do design (sky=destaque, emerald=ok, red=erro, amber=atenção, violet=subagente)
- [x] Trilho de seções 60px + lista 236px (grupos com chevron em todas as seções, meta "há N min", Novo, Ctrl K)
- [x] Cabeçalho: chip da pasta mono, Chat | Trajetória, 7 ícones de painel 30px com selos
- [x] Tiles: fundo side, cabeçalho 7×12 com meta mono
- [x] Composer: caixa 18px, pílulas 9px, Enviar 36px accent; pílula única Modo · Esforço com menu de 2 colunas
- [x] Agente: balão 22px, grupo de atividade (falhas em vermelho), raciocínio inline, bloco aguardando com borda accent, diff com cores novas, estatísticas mono pt-BR, tarefas com barrinha
- [x] Configurações: 1080×760, nav agrupada (App/Máquina/Modelos/Agente/Integrações), linhas rótulo|controle, rodapé Salvar, Tema/Destaque/Fonte
- [x] Atalhos Ctrl 1–7, Ctrl , e Ctrl K
- [x] Iniciais configuráveis no pé do trilho (Configurações › Aplicativo), clique abre Configurações

## Falta (front)
- [x] Imagens: inspetor 292px, barra de decisão flutuante, marcação com anel accent
- [x] Vídeo: inspetor 300px, marcação com anel accent
- [x] Vídeo: inspetor em seções, barra de decisão flutuante, scrub accent, marcação accent
- [x] Vídeo: FORMATO com desenho da proporção (menu de Proporção)
- [x] Vídeo: DURAÇÃO em slider com a marca do limite de treino
- [x] Vídeo: formato 4:3, resoluções 480p/720p/1080p/4K sempre visíveis (as fora do treino ficam apagadas, com aviso) e personalizada com proporção travada
- [x] Imagem e Vídeo no desenho do protótipo: painel **Parâmetros** fixo à direita (liga/desliga no topo, lembra), composer novo
      (miniaturas/quadros + texto; Melhorar · + Negativo · Ampliar do PC · estimativa · Gerar N Enter), barra do topo com
      contagem/filtros e estado da GPU, cabeçalho de lote (Lote N / letra, meta mono, ação), cards com sobreposição
      (modelo · semente, check no canto, "na fila", listras no placeholder, líquido do design), hover do vídeo com
      Continuar → / Manter K / Descartar D (aceita também M/X, como o player)
- [ ] Vídeo: "Comparar 2" (A/B entre tomadas) do protótipo — funcionalidade nova, não existe hoje
- [ ] Imagem: variações 2b (triagem por teclado) e 2c (matriz modelo × semente) da referência
- [x] Comparar: voto com estrela ("Votar" / "Melhor resposta"), Refazer, rótulos mono (card PROMPT e Analisar já existiam)
- [x] Pesquisa: RESUMO com borda accent, plano recolhível e FONTES com rótulo mono
- [x] Pesquisa: coluna Fontes 300px à direita (≥ xl)
- [x] Maestro: marcas ✓ ⟳ ◆ ? ○ com as cores de estado
- [x] Maestro: legenda no rodapé da árvore
- [x] Trajetória: 3 faixas, delegação violeta, toggles no estilo novo
- [x] Imagens: galeria em mosaico pela proporção real (`mosaico.ts`, com teste)
- [x] Modais: scrim e sombra do design; rótulos caixa-alta em mono em todas as telas
- [x] IA local: barra segmentada de uso de memória e parâmetro alterado em accent — **não vista na tela**: a estimativa só aparece com o llama.cpp instalado
- [x] IA local: busca do Hugging Face com opções de download coloridas (verde cabe na GPU, âmbar parte na RAM, vermelho não carrega) e legenda
- [x] Cartões de estado 9f (`CartaoEstado.tsx`): VRAM ocupada, pergunta do agente, objetivo, contexto compactado, reconexão, ignorar permissões
- [ ] 9f restantes: atualização como cartão (hoje é modal), desfazer arquivos e apagar conversa no mesmo desenho
- [x] Esc fecha o último tile (fora de campo de texto e sem diálogo)
- [x] TasksBar recolhível no topo do composer, com barrinha de progresso
- [x] Portado para o forja-web: branch `feat/redesign` (worktree `forja-web/.claude/worktrees/redesign`), sem Vídeo/IA local/Board que o web não tem
- [ ] forja-web: validar com o Docker de pé (localhost:7001) — só vi o visual com Vite, sem dados; a Imagem do web (runner/nuvem) foi adaptada à mão e **não foi vista na tela**
- [ ] O que for feito daqui em diante no desktop: portar com `git merge-file` (base = desktop `c8e802a`)

## Dados de teste
- `scripts/fake_midia.py`: lotes fake de Imagem (proporções misturadas, todos os estados) e Vídeo no `.devredesign`, sem carregar modelo.

## Backend (depois)
- [ ] Vídeo em 1080p e 4K: a tela agora oferece (lado menor 1080/2160, no múltiplo do modelo); conferir se o backend/sd.cpp
      aceita esses tamanhos sem cortar e se a estimativa de tempo/memória avisa antes de estourar a VRAM
- [ ] Persistir `tema`, `corDestaque`, `fonte` no `/settings` (hoje em `localStorage` `forja.aparencia`) — e o celular ler daí
- [ ] Lista de conversas: miniatura (34px) em Imagem/Vídeo e modelo usado na meta ("há 12 min · Qwen3.6") — precisa vir no `GET /conversations`
- [x] Rodapé da lista: estado do runtime ("● Vulkan") — feito só no front, lendo `/local` a cada 60 s (o mesmo que Configurações › Runtime usa); se ficar pesado, virar um campo barato do `/activity`
- [ ] Trajetória: "Pensou por N s" exige a duração do raciocínio por mensagem
- [ ] Multiplicadores de esforço (0,4× · 1× · 1,6× · 3× · 4× multi) vieram do design e estão **comentados** em
      `frontend/src/components/Controls.tsx` (`MULT_ESFORCO` e o `<span>` no menu). Definir no backend o que medem
      (tokens? tempo? chamadas? relativo ao Médio), expor no `/settings` ou num endpoint barato, e descomentar lendo de lá.
- [ ] Iniciais do trilho (`forja.aparencia.iniciais`, padrão "EU") junto com tema/destaque/fonte no `/settings`

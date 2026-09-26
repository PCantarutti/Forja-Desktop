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
- [ ] Vídeo: seções do inspetor como no design (FORMATO com desenho, DURAÇÃO em slider, ACELERAR em toggle), hover com scrub accent
- [x] Comparar: voto com estrela ("Votar" / "Melhor resposta"), Refazer, rótulos mono (card PROMPT e Analisar já existiam)
- [x] Pesquisa: RESUMO com borda accent, plano recolhível e FONTES com rótulo mono
- [ ] Pesquisa: coluna Fontes 300px à direita
- [x] Maestro: marcas ✓ ⟳ ◆ ? ○ com as cores de estado
- [ ] Maestro: legenda no rodapé da árvore
- [x] Trajetória: 3 faixas, delegação violeta, toggles no estilo novo
- [ ] IA local e modais (seletor de modelo, player, máscara, ampliar, cartões de estado)
- [ ] Esc fecha o último tile
- [ ] TasksBar recolhível no topo do composer (hoje é card na conversa)
- [x] Portado para o forja-web: branch `feat/redesign` (worktree `forja-web/.claude/worktrees/redesign`), sem Vídeo/IA local/Board que o web não tem
- [ ] forja-web: validar com o Docker de pé (localhost:7001) — só vi o visual com Vite, sem dados
- [ ] O que for feito daqui em diante no desktop: portar com `git merge-file` (base = desktop `c8e802a`)

## Backend (depois)
- [ ] Persistir `tema`, `corDestaque`, `fonte` no `/settings` (hoje em `localStorage` `forja.aparencia`) — e o celular ler daí
- [ ] Lista de conversas: miniatura (34px) em Imagem/Vídeo e modelo usado na meta ("há 12 min · Qwen3.6") — precisa vir no `GET /conversations`
- [ ] Rodapé da lista: estado do runtime (ex.: "● Vulkan") num campo barato do `/activity`
- [ ] Trajetória: "Pensou por N s" exige a duração do raciocínio por mensagem
- [ ] Multiplicadores de esforço (0,4× · 1× · 1,6× · 3× · 4× multi) vieram do design e estão **comentados** em
      `frontend/src/components/Controls.tsx` (`MULT_ESFORCO` e o `<span>` no menu). Definir no backend o que medem
      (tokens? tempo? chamadas? relativo ao Médio), expor no `/settings` ou num endpoint barato, e descomentar lendo de lá.
- [ ] Iniciais do trilho (`forja.aparencia.iniciais`, padrão "EU") junto com tema/destaque/fonte no `/settings`

---
name: editar-video
description: "Use ESTA skill quando o usuário pedir para EDITAR vídeo gravado em Reel/Short/YT. Também usar quando mencionar 'edita esse vídeo', 'corta silêncios desse bruto', 'monta um reel desse vídeo', 'analisa esse vídeo pra cortar', 'faz cleanup desse vídeo de terceiro', 'edita o YT longo'. NÃO usar essa skill pra criar carrossel ou roteiro do zero. Esta skill cobre o fluxo VÍDEO BRUTO → vídeo editado final."
metadata:
  version: 2.2.2
  audience: "criadores de conteúdo em vídeo (Reels/Shorts)"
  output_formats: [reels-9x16-180s]
  resolutions: [1080x1920]
  estilo_default: 04-premium-cinematic
  caption_default: A-clean
  workflow: "v2.1 — 6 etapas + 5 gates (gate 1 = transcript, gate 2 = plano)"
---

# /editar-video — Estúdio de Gravação e Edição de Vídeo

Orquestrador que transforma **vídeo bruto** em **Reel/Short/YT editado** com workflow conversacional em 6 etapas + 5 gates de aprovação explícitos.

As 6 etapas, os 5 gates, o Production Safety Net e as regras técnicas (split 60/40, safe zone IG 280px, suppressão de legenda em título/CTA etc.) estão todos documentados neste arquivo, mais abaixo. Internamente o autor mantém uma versão estendida dessas regras num arquivo pessoal de estilo — opcional, não é necessária pra rodar a skill.

Bases consultadas internamente:
1. Frameworks de copy (quando há output pra Instagram) — opcional, arquivo pessoal do autor
2. Copy Style System pessoal (tom de voz + proibições + presets) — opcional, arquivo pessoal do autor
3. Video Design System (4 estilos visuais + mascote Clawd + 3 estilos de caption)
   - CSS modulares (bundlados nesta skill): `video-design-system/styles/{tokens,animations,titles,captions,styles}.css`
4. Library cache estilo claude-watch (re-runs instantâneos)

Saída em `edicoes/<slug>/output/` (relativo à pasta onde você rodar a skill):
- `final.mp4` (1080×1920)
- `meta.json`, `shot_list.json`, `workflow_state.json`
- `transcript-revisao.md` (Gate 1 — entrada) / `transcript-corrigido.json` (Gate 1 — saída)
- `plano-inserts.html` / `plano-inserts.json` (Gate 2)
- `master.mp4` (Gate 3)
- `master-legendado.mp4` (Gate 4)
- `quality_report.json` + `quality_report_master.json` + `quality_report_legendado.json`

---


## 🔗 Onde esta skill entra no fluxo

Recebe: `bruto.mp4` (o vídeo gravado, com ou sem roteiro prévio).
Entrega: MP4 final renderizado.

O autor mantém skills complementares próprias pra etapas vizinhas (gerar roteiro antes de gravar, montar inserts num editor externo depois, triar antes de publicar). Nenhuma delas vem neste repositório nem é necessária — sozinha, esta skill já cobre o fluxo completo de vídeo bruto até vídeo editado.

---

## Detecção v1 vs v2 (importante)

A skill detecta automaticamente qual workflow rodar a partir da presença de `workflow_state.json` na pasta de edição:

- **Edição NOVA (sem `workflow_state.json`)** → abre em **v2** (6 etapas + 5 gates). Padrão a partir de 2026-05-12.
- **Edição EXISTENTE com `workflow_state.json` ausente mas com artefatos v1 (`shot_list.json` antigo, `final.mp4` já renderizado)** → continua em **v1** (pipeline linear 7 fases). Edições antigas continuam funcionando sem rebuild.

Regra cravada: **edições antigas continuam funcionando, edições novas abrem em v2**. O user nunca precisa migrar manualmente.

---

## Fluxo v2.1.0 — 6 etapas + 5 gates conversacionais

> **Mudança v2.1 (2026-05-12):** ordem dos gates reordenada — `transcript_revisao` virou **Gate 1** (antes era Gate 3), porque Whisper erra brand names e gírias, e o plano de inserts contextual deve ser proposto a partir do transcript JÁ CORRIGIDO. Resultado: nada que dependa de texto roda em cima de Whisper bruto.

### Etapa Inicial — Config (1 mensagem só)

Antes de qualquer processamento, pergunta 3 coisas em **uma única mensagem**:

```
"Config rápida antes de começar:

1. Layout padrão dos inserts (fallback quando um insert não especifica próprio)?
   a) overlay-fullframe ⭐ (DEFAULT v2.2 — talking head 100%, cards rounded flutuam por cima)
   b) split-60-40 (legado v2.1 — top 40% insert, bottom 60% talking head)
   c) split-50-50
   d) split-70-30 / split-80-20 (talking head dominante)
   e) full-frame-puro (insert substitui talking head 100% durante o insert)

2. Estilo visual?
   01) Stepped Tutorial (didático, caixas, TT2 numerado)
   02) Clean Hand-drawn (opinião, doodles, TT1 torn paper)
   03) Split Cinematic (editorial, split 50/50, TT4 serif)
   04) Premium Cinematic ⭐ (default — B-roll + Ken Burns + serif italic)

3. Tipo de conteúdo?
   a) Tutorial (passo-a-passo, screen-share)
   b) Hot take (opinião forte, hook provocativo)
   c) Autoridade (case real, prova social)
   d) Bastidor (raw, sem produção pesada)"
```

Resposta é cravada em `style_config.json` + `workflow_state.json`. Todas as etapas downstream lêem dali. Pergunta **uma vez**, não repete.

> **Importante v2.2.0:** o item 1 define o **layout PADRÃO** (fallback). Cada insert individual pode especificar próprio `layout` no `inserts_manuais.json` — quando não especifica, herda do default global. Isso é "layout-per-insert" — um vídeo pode misturar overlay-fullframe na maior parte, split-50-50 em momentos didáticos e full-frame-puro em demos de tela.

### Schema de layouts (v2.2.0)

Valores válidos pro campo `layout` (em `style_config.json` global OU em cada insert do `inserts_manuais.json`):

| Valor | Como renderiza | Caption position auto |
|---|---|---|
| `overlay-fullframe` ⭐ | Talking head 100%, sem card (gap entre inserts) | top |
| `overlay-top-card` | Talking head 100% + card rounded white BG no topo | bottom |
| `overlay-middle-card` | Talking head 100% + card no meio (cobrindo face) | top |
| `overlay-bottom-card` ⭐ **TH-DEFAULT** | Talking head 100% + card no torso (y≈900px em 1920) | top |
| `split-60-40` | Insert top 40%, talking head bottom 60% (split fixo) | bottom |
| `split-50-50` | Split 50/50 top/bottom | bottom |
| `split-70-30` | Insert top 30%, talking head bottom 70% | bottom |
| `split-80-20` | Insert top 20%, talking head bottom 80% | bottom |
| `full-frame-puro` | Insert 100%, talking head some durante o insert | top |

**Caption position** é DERIVADA do layout (default acima), mas user pode override em `style_config.json` com `caption_position: "top"` ou `"bottom"`.

**Specs técnicas detalhadas** dos cards overlay (rounded corners, padding, max-width, face-aware) estão na tabela acima e implementadas em `orchestrator.py` — não dependem de nenhum arquivo externo.

> **REGRA CRAVADA (2026-05-18 — sessão de testes):** quando o user escolher layout 1a (`overlay-fullframe`) em vídeos de **talking head**, o card de cada insert usa `overlay-bottom-card` com **y_default = 900px** (torso). NÃO usar `overlay-top-card` em talking head — parece amador. Card width = **1040px**, rounded r=26, sombra blur=16 opacity=110.

---

### 🎨 Padrões de qualidade de inserts (cravado 2026-05-19 — sessão de testes v2)

Regras validadas na prática. Qualquer insert proposto deve seguir estes padrões:

**Duração:**
- **≤ 3s por insert** (idealmente 2–3s). Inserts longos (>5s) parecem lentos e amatadores. Vídeo de talking head é ritmo, não slideshow.
- Cobrir momentos específicos do áudio (palavra-chave ou lista citada), não "toda a seção".

**Variedade de tipos (não só screenshots):**
| Tipo | Quando usar |
|---|---|
| `screenshot_url` | Apresentação do produto (interface real) |
| `logo_cluster` | Quando o áudio cita 3+ ferramentas encadeadas (ex: "email, drive, Slack") |
| `flow_diagram` | Quando há relação de causa→efeito no áudio |
| `pill_grid` | Lista de 4 itens curtos (ex: "site, app, automação, sistema") |
| `logo_destaque` | 1 ferramenta + verbo-ação (ex: Gmail + "automático") |
| `task_cards` | 2 tarefas side-by-side com ícone + descrição |
| `comparison` | Contraste binário (ex: "1 tarde vs 1 semana") |
| `mini_icons` | 4 artefatos/categorias com emoji + label |

**Logos e ícones:**
- **SEMPRE logos reais** das ferramentas citadas — nunca fabricados/genéricos
- Fontes confiáveis (em ordem de preferência): Simple Icons via jsDelivr (`cdn.jsdelivr.net/npm/simple-icons@latest/icons/<slug>.svg`), GitHub org avatars (`avatars.githubusercontent.com/u/<id>?s=400`), gstatic Google
- **Logos GRANDES**: mínimo 68px dentro de um círculo/quadrado de 96–140px com background da cor da marca
- **PROIBIDO**: logo pequeno em caixa cinza/bege — parece placeholder

**Background dos cards:**
- **Fundo transparente (RGBA)** para inserts de logos/ícones/texto que flutuam sobre o vídeo — usar `omit_background=True` no Playwright screenshot
- Texto e ícones sobre vídeo transparente: sempre com `text-shadow` e/ou `drop-shadow` para legibilidade
- Cards opacos (branco ou escuro) apenas para screenshots reais de interface — eles precisam do contexto visual completo

**Composição ffmpeg:**
- PNGs RGBA: ffmpeg interpreta alpha channel automaticamente no overlay — sem parâmetro extra
- Transparentes em 920px largura → `x = (1080-920)/2 = 80`
- Opacos em 1040px → `x = (1080-1040)/2 = 20`
- Fade in/out 0.4s via filtro alpha em ambos os casos

### Etapas sequenciais

| # | Etapa | Tem gate? | Output principal |
|---|---|---|---|
| 0 | Config inicial | não (input do user) | `style_config.json` |
| 1 | Cuts base (silêncios + retakes) | quality check automático | `cortado.mp4` + `quality_report.json` |
| 2 | Revisão transcript | **Gate 1** — Transcript | `transcript-revisao.md` → `transcript-corrigido.json` |
| 3 | Plano de inserts (Claude contextual) | **Gate 2** — Plano-Inserts | `plano-inserts.html` + `plano-inserts.json` |
| 4 | Master (inserts compostos) | **Gate 3** — Master | `master.mp4` |
| 5 | Legenda burned-in + títulos | **Gate 4** — Legenda | `master-legendado.mp4` |
| 6 | Acelerar (speed-up final) | **Gate 5** — Acelerar | `final.mp4` |

> Nova ordem v2.1: o **Gate 1 é a revisão do transcript** porque Whisper sempre erra brand names ("Claude"→"Clod"), gírias e palavras técnicas. Toda decisão downstream (plano de inserts, legenda) depende do texto correto. Sem revisão antes = lixo entra, lixo sai.

### Formato dos gates no chat

Cada gate é uma mensagem padronizada que **PARA a skill** aguardando aprovação no chat. Antes de exibir, a skill:

1. Abre o output via `open <path>` (Claude Code CLI não renderiza file:// — precisa do `open`).
2. Lê frames críticos com Read tool (self-review obrigatório).
3. Compara com referência aprovada.
4. Conserta se errado — **NUNCA manda pro user errado e espera ele pegar**.

Formato no chat:

```
═══════════════════════════════════════════════════════════════
🛑 GATE N/5 — TÍTULO DO GATE
═══════════════════════════════════════════════════════════════

[informações do output: duração, resolução, métricas relevantes]

📄 file:///path/para/output

❓ Como prosseguir?
  a) Aprovado — segue
  b) Ajustar — [pergunta o que ajustar]
  c) Refazer — [pergunta detalhe específico]
```

A skill **não executa ação** sem o user responder no chat. Sem ambiguidade.

### 5 gates explícitos

1. **Gate 1 — Transcript** (NOVO posicionamento v2.1): logo após `cuts_base`, abre `transcript-revisao.md` no editor preferido (Cursor/VSCode), user corrige erros de Whisper (brand names, gírias, palavras técnicas) e responde "pronto" no chat. Skill parseia e atualiza `transcript-corrigido.json`.

   **CLAUDE-NO-LOOP (cravado 2026-05-12):** logo APÓS aprovar-transcript, a skill PARA e me passa a bola pra fazer análise contextual baseada no transcript já CORRIGIDO. Eu (Claude rodando a skill) leio `transcript-corrigido.json` com `Read`, analiso o conteúdo semanticamente (que UI/foto/mascote agrega a cada bloco?), escrevo `inserts_manuais.json` com schema documentado (campos `at_s`, `duration_s`, `tipo`, `subtipo`, `layout`, `label`, `termo_no_audio`, `raciocinio`), depois rodo `--mode propor-inserts` que mergeia mecânico (hook + brand exato + CTA) com meus contextuais e gera o HTML final do Gate 2.

   Princípio: a parte MECÂNICA é regex/match (script Python). A parte CONTEXTUAL é Claude pensando em cada vídeo a partir do texto correto — sem hardcode de frases, sem overfitting.
2. **Gate 2 — Plano-Inserts**: mostra `plano-inserts.html` com cards de cada insert (timestamp, tipo, asset proposto, source). User aprova lista ANTES de qualquer composição. Aprovar dispara automaticamente `compor-master`.

   **CACHE CROSS-SKILL (opcional):** se você mantiver um histórico de quais inserts funcionaram melhor em vídeos anteriores, a skill pode consultar `cache-cross/inserts-validados.json` antes de propor inserts novos — padrões com confidence ≥ 0.70 e match de tipo/contexto com o transcript atual são reaproveitados; cada uso é registrado em `historico/<slug>/inserts_aplicados.json` pra fechar o loop. Sem esse arquivo (caso mais comum), a skill segue direto pro caminho contextual normal.
3. **Gate 3 — Master**: mostra `master.mp4` (cuts base + inserts ui-mock **animados MP4** compostos no split definido, sem legenda nem título ainda). Mocks são renderizados via `animate_mock.py` (Playwright video recording de CSS animations → WebM → MP4). talking_head_mode=live = gaps entre inserts mostram a pessoa gravada falando ao vivo (não frame congelado). User aprova composição visual. Aprovar dispara automaticamente `aplicar-legenda`.
4. **Gate 4 — Legenda**: mostra `master-legendado.mp4` com legenda A burned-in + títulos TT1-TT4 + CTA. Legenda MUTADA automaticamente via `--suppress-intervals` quando título/CTA visível (v2.1: implementado). User valida safe zone (MarginV ≥ 280px, recomendado 320px).
5. **Gate 5 — Acelerar**: pergunta multiplicador final (1.0 / 1.1 / 1.15 / 1.2 / 1.25 / 1.3). Renderiza `final.mp4` com loudness target -14 LUFS.

---

## Comandos CLI

| Comando | O que faz |
|---|---|
| `/editar-video <pasta>` | Inicia workflow v2.1, para no Gate 1 (após gerar transcript-revisao.md) |
| `/editar-video <pasta> --skip-cuts` | Pula Etapa 1 (cortar_silencios). Use pra inputs já editados (DaVinci/Premiere). |
| `/editar-video aprovar-transcript` | Gate 1: parseia `transcript-revisao.md` → corrigido.json, dispara propor-inserts mecânico + ciclo Claude-no-loop |
| `/editar-video propor-inserts <pasta>` | Standalone: regenera plano-inserts (mecânico + manuais merge) |
| `/editar-video aprovar-plano` | Gate 2: aprova plano, dispara compor-master automaticamente |
| `/editar-video compor-master <pasta>` | Standalone: compõe master.mp4 via inserir_broll (ui-mocks aplicados, titulos/cta diferidos) |
| `/editar-video aprovar-master` | Gate 3: aprova master, dispara aplicar-legenda automaticamente |
| `/editar-video aplicar-legenda <pasta>` | Standalone: aplica título overlay + caption burned-in → master-legendado.mp4 |
| `/editar-video aprovar-legenda` | Gate 4: aprova legendado, libera Gate 5 |
| `/editar-video acelerar [1.0-1.3]` | Gate 5: multiplicador opcional (default 1.0), gera final.mp4 |
| `/editar-video desfaz` | Volta 1 gate (até 3 consecutivos) |
| `/editar-video versao` | Detecta versão do workflow (v1 vs v2) |

Estado entre comandos persiste em `workflow_state.json` na pasta de edição. User pode parar e voltar quando quiser.

---

## Production Safety Net v2 — 8 quality_gates automáticos

Antes de exibir QUALQUER gate ao user, a skill roda 8 checks automáticos. Se algum falhar, **re-render automático** ou **bloqueio com mensagem clara** — nunca manda pro user com defeito.

| # | Quality gate | Threshold | Ação em falha |
|---|---|---|---|
| 1 | `caption_inside_frame` | xmin ≥ 60px, xmax ≤ w-60px | re-render com `words_per_block=2` |
| 2 | `caption_not_multiline` | linhas por bloco ≤ 1 | reduz `words_per_block` |
| 3 | `caption_safe_zone_ig` | MarginV ≥ 280px (recomendado 320px) | sobe MarginV |
| 4 | `caption_face_aware` | nunca cobre rosto detectado | reposiciona acima/abaixo |
| 5 | `caption_title_coexistence` | mutada quando TT1-TT4 visível | aplica `--suppress-intervals` |
| 6 | `caption_cta_coexistence` | mutada quando caixa CTA visível | aplica `--suppress-intervals` |
| 7 | `loudness_target` | -14 LUFS (±0.5) | re-render com ajuste |
| 8 | `av_sync` | drift ≤ 40ms | bloqueia com erro técnico |

Resultados gravados em `quality_report.json`. Reportados ao user no gate só se passaram — falhas são consertadas em background.

---

## Regras de legenda (resumo cravado)

- **Safe zone IG**: MarginV mínimo 280px, recomendado 320px.
- **Coexistência**: legenda MUTADA quando título (TT1-TT4) OU caixa CTA visível. Um OU outro, nunca os dois ao mesmo tempo.
- **Words per block default = 2** (não 3, não 4). Evita word-wrap feio em frame 720px.
- **Side margin 60px**: `max_text_width = w - 120px`.
- **Face-aware**: nunca cobre rosto detectado. Sobe ou desce respeitando safe zone.
- **Suppression intervals**: `adicionar_legenda` aceita `--suppress-intervals "0-3.5,50-54"` pra blackout em hook/CTA.

Detalhe completo nas seções acima deste mesmo arquivo.

---

## 🛡️ REGRAS NÃO-NEGOCIÁVEIS

1. **Etapa Inicial pergunta UMA mensagem só** (layout padrão + estilo + tipo de conteúdo). Não fragmenta.
2. **Estilo + layout padrão são cravados em `style_config.json`** — todas etapas downstream lêem. Cada insert pode override próprio `layout` em `inserts_manuais.json`.
3. **Library cache obrigatório** — re-runs reusam transcript/scenes/face_zones.
4. **Pasta de input "1 pasta com tudo"** — `ls`, descobre, só pergunta o que faltou.
5. **Output só em `edicoes/<slug>/`** (relativo à pasta onde a skill roda) — nunca polui pasta input.
6. **5 gates explícitos PARAM a skill** — não executa sem resposta no chat.
7. **Self-review antes de cada gate** — `open` o output, lê frames, conserta se errado.
8. **NUNCA burna legenda direto do Whisper sem Gate 1** — revisão de transcript é obrigatória.
9. **Production Safety Net automático** — 8 quality_gates rodam antes de cada gate de user.
10. **Detecção v1/v2 transparente** — edições antigas continuam em v1, novas abrem em v2.
11. **Tom didático, sem grosseria, sem gírias** — Padrão #14 do `/gerar-conteudo`.
12. **`/editar-video desfaz` volta até 3 gates** — user nunca fica preso.
13. **NUNCA placeholder mock no master** — quadrado bege com label tipo "napkin-ai-home" é PROIBIDO em master/legendado/final. Se template HTML não existe e não tem live URL, skill PARA e pede orientação ao user.
14. **Mocks são MP4 animados** — renderizados via `animate_mock.py` (Playwright video recording de CSS animations). NUNCA PNG estático no master. Pipeline: HTML+CSS → Playwright grava WebM → ffmpeg converte pra MP4.
15. **talking_head_mode default = `live`** — gaps entre inserts mostram a pessoa falando ao vivo. `stillframe` congela 1 frame (só usar se user pedir explicitamente). Cravado em `style_config.json`.
16. **BRAND_MAP com aliases Whisper** — Whisper erra brand names sistematicamente ("Claude"→"Clod", "Napkin"→"Napking"). `propor_inserts.py` normaliza via aliases antes de match. Atualizar BRAND_MAP quando descobrir novo alias.
17. **Self-review de CONTEÚDO** — antes de apresentar gate, ler o output visualmente (frames, texto). Não basta passar quality gates técnicos — conteúdo errado (placeholder, texto cortado, mock sem animação) também é falha.
18. **Layout é PER-INSERT, não global** (v2.2.0) — `style_config.json` tem `layout_default` (fallback) e cada item de `inserts_manuais.json` pode especificar próprio `layout`. Quando insert não tem `layout`, herda do default. Valores válidos: ver tabela "Schema de layouts" acima.
19. **Caption position é DERIVADA do layout** — `overlay-fullframe` / `overlay-middle-card` / `overlay-bottom-card` / `full-frame-puro` → caption TOP. `split-*` / `overlay-top-card` → caption BOTTOM safe zone (MarginV ≥ 280px). User pode override com `caption_position` em `style_config.json`.
20. **Retro-compat v2.0/v2.1** — edições antigas usam `workflow_state.json` sem campo `schema_version`. Quando ausente OU `< 2.2`, skill cai pro modelo split-global antigo (60/40 default, caption sempre bottom). Edições novas (v2.2+) cravam `schema_version: "2.2.0"`.

---

## 🔧 Troubleshooting

| Sintoma | Solução |
|---|---|
| Pasta input não tem vídeo | Pergunta path correto |
| Pasta tem N vídeos sem prefixo | Trata como tomadas sequenciais |
| Pasta tem `01-frontal` + `01-lateral` | Multi-cam ATIVO (v1.1+) |
| Cache hit | Avisa "reusando análise de <data>" |
| Mediapipe falha | Avisa pra abrir issue (já testado funciona) |
| Whisper modelo não baixado | Skill baixa na 1ª execução (~150MB pra `base`) |
| Estilo desconhecido | Avisa "Disponíveis: 01/02/03/04" |
| Plus Jakarta Sans não instalada | Cai pra Helvetica Neue Bold + warning |
| Quality gate falhou | Auto-fix ou bloqueia com erro claro (nunca passa pro user) |
| User quer voltar de gate | `/editar-video desfaz` (até 3 consecutivos) |
| `workflow_state.json` corrompido | Skill detecta e oferece reset com aprovação |
| Mock estático sem animação no master | Usar `animate_mock.py` (não `render_mock.py`). Verifica se template tem CSS animations |
| Gap longo entre inserts com frame congelado | `talking_head_mode=live` em `style_config.json` (default). Gaps = talking head ao vivo |
| Placeholder bege no master | PROIBIDO. Renderizar mock real ou parar e pedir orientação ao user |
| Whisper erra brand name ("Clod", "Napking") | Adicionar alias em BRAND_MAP do `propor_inserts.py`. User corrige no Gate 1 |
| Playwright `record_video` param errado | Usar `record_video_dir` + `record_video_size` (params separados, não dict) |
| ffmpeg `-loop 1` em MP4 input | NUNCA usar `-loop 1` com MP4 (só com PNG). MP4 animado já tem duração correta |
| Insert no `inserts_manuais.json` sem `layout` | Herda `layout_default` de `style_config.json`. Comportamento esperado (v2.2.0) |
| Edição antiga abrindo com modelo novo | Skill detecta ausência de `schema_version` em `workflow_state.json` → cai pro modelo split-global (v2.1) automaticamente |
| Caption no bottom mas insert overlay-middle-card cobrindo torso | Layout em conflito. Ou troca pra `overlay-bottom-card` (caption vai pro top) ou move card pra outra área |
| Input já editado (DaVinci/Premiere) sendo cortado de novo | Skill detecta paths como `*/davinci/*`, `*/finalizados/*` e pula auto. Pra forçar, use `--skip-cuts`. |

---

## 🔌 Integração com outras skills (opcional)

O autor também usa skills próprias complementares — pra gerar o roteiro antes de gravar, buscar imagens de referência e gerar B-roll quando falta asset. Nenhuma delas vem neste repositório nem é obrigatória: sem elas, a skill usa os caminhos padrão descritos nas seções acima.

---

## 💾 Storage & Cache

Quatro pastas (relativas a onde você roda a skill) usadas pra runtime + library cache. Library cache invalida só o que mudou (estilo claude-watch).

| Pasta | Função |
|---|---|
| `roteiros-video/R###-<data>-<slug>/` | Banco de roteiros (rascunho/aprovado/gravado/editado/publicado), se você organizar roteiros assim |
| `edicoes/<YYYY-MM-DD>-<slug>-<sha4>/` | Library cache de edições (transcript + scenes + face_zones + analysis + shot_list) |
| `edicoes-styles/estilos/<estilo>/padroes.md` | Aprendizado por estilo (sem cross-contamination entre estilos) |
| `asset-bank/` | Banco local de assets (photos, videos, sfx, music-beds), se você mantiver um |

**Regra:** skill SEMPRE escreve outputs em `edicoes/<slug>/` — nunca polui pasta de input.

---

## 📝 Versionamento

**v2.2.2** (2026-09-13) — portabilidade (pré-requisito pro repo público/tutorial):
- 3 caminhos absolutos fixos no usuário original trocados por resolução portátil: `animar_titulo.py`, `render_mock.py` e `animate_mock.py` agora usam `video-design-system/` bundlado dentro da própria skill como default, com override via env var `EDITAR_VIDEO_DS_DIR` pra quem mantiver uma fonte externa
- `orchestrator.py`: `PYTHON` (interpretador usado nos subprocess) trocado do venv hardcoded pra `sys.executable` (o mesmo interpretador rodando a skill), com override via `EDITAR_VIDEO_PYTHON`
- Bundle de `video-design-system/styles/` (5 CSS, obrigatório — sem isso `animar_titulo.py` dava `sys.exit(1)`) dentro da skill. `ui-mocks/` e `assets/` NÃO foram bundlados (opcionais — `render_mock.py` cai em erro pedindo orientação, não placeholder, se faltar um template específico)
- Suíte de testes: 154 passaram sem mudança; 4 que dependiam de templates específicos do `ui-mocks/` (canva-logo, fast-company, instagram-handle) só passam com `EDITAR_VIDEO_DS_DIR` setada — não é regressão
- Motivo: preparar um mirror público (`github.com/luiscortex/editar-video-skill`) pra tutorial de instalação no hub iacortex.club — o repo de desenvolvimento (`luiscortex-editar-video`) continua privado

**v2.2.1** (2026-05-18 noite) — Catálogo Visual sincronizado:
- Expansão de "Bases consultadas" com paths cravados dos 5 CSS files do DS + ponteiro pra seção "🎨 Catálogo Visual" em `regras-edicao-video-2026.md`
- Nova seção "💾 Storage & Cache" com 4 paths cravados (roteiros-video, edicoes, edicoes-styles, asset-bank)
- Não-mudança de runtime — só doc/ponteiro pra remover ambiguidade visual

**v2.2.0** (2026-05-18) — layout-per-insert (refatoração após análise referência cindiezhu + alinhamento com PLANO-INSERTS.md original):
- **Modelo split-global → layout-per-insert**: a Etapa Inicial agora pergunta `layout_default` (fallback), e cada insert do `inserts_manuais.json` pode especificar próprio `layout`. Um vídeo pode misturar `overlay-fullframe` na maior parte, `split-50-50` em momentos didáticos e `full-frame-puro` em demos de tela
- **9 layouts cravados**: `overlay-fullframe` (default novo) / `overlay-top-card` / `overlay-middle-card` / `overlay-bottom-card` / `split-60-40` / `split-50-50` / `split-70-30` / `split-80-20` / `full-frame-puro`
- **Caption position DERIVADA do layout** — não é mais campo independente. Layouts `overlay-fullframe/middle/bottom` + `full-frame-puro` → caption TOP. `split-*` + `overlay-top-card` → caption BOTTOM safe zone
- **3 regras novas cravadas (#18, #19, #20)** — layout per-insert / caption derivada / retro-compat
- **Retro-compat v2.0/v2.1 garantida** — `workflow_state.json` ganha `schema_version`. Ausente OU `< 2.2` → modelo split-global antigo. Edições antigas continuam rodando sem migration manual
- **Origem**: um protótipo anterior já usava layout per-insert; só não tinha sido formalizado nesta skill ainda
- **✅ Renderer overlay-card IMPLEMENTADO (2026-05-18 noite)** — `orchestrator._compose_master_with_splits` agora suporta `overlay-top/middle/bottom-card` via ffmpeg `geq` filter (rounded corners SDF + alpha mask) + `overlay-fullframe` (skip = talking head 100%) + aliases v2.1 (`fullframe`→`full-frame-puro`, `60/40`→`split-60-40`). Smoke test validado com fixtures sintéticas — card rounded 612×~344 sobre talking head 720×1280, posições middle (y=512) e bottom (y=870). **Pendente**: `propor_inserts.py` respeitar campo `layout` per-insert, `adicionar_legenda.py` derivar caption_position do layout, `workflow_state.json` cravar `schema_version: "2.2.0"`

**v2.1.0** (2026-05-13) — fixes cravados da sessão smoke-test (11 erros → 11 fixes):
- Gates reordenados: **Transcript → Plano → Master → Legenda → Acelerar** (transcript ANTES de plano, porque Whisper erra brand names e o plano contextual depende do texto correto)
- Mocks agora são **MP4 animados** via `animate_mock.py` (Playwright video recording de CSS animations), não PNG estáticos
- `talking_head_mode` default = `live` (gaps entre inserts = talking head ao vivo, não frame congelado)
- Regra "NUNCA placeholder mock no master" cravada (13 regras → 17 regras não-negociáveis)
- BRAND_MAP com aliases Whisper ("Clod"→"Claude", "Napking"→"Napkin")
- Claude-no-loop: inserts contextuais são propostos por Claude (análise semântica), não por regex/hardcode
- Self-review de conteúdo (não só quality gates técnicos) antes de apresentar gate
- ffmpeg compose suporta inputs MP4 (sem `-loop 1`) e PNG (com `-loop 1`)
- 6 novos troubleshooting entries

**v2.0.0** (2026-05-12) — workflow conversacional 6 etapas + 5 gates:
- Etapa Inicial com 3 perguntas em 1 mensagem (split + estilo + tipo)
- 5 gates explícitos (Transcript, Plano-Inserts, Master, Legenda, Acelerar)
- 7 comandos CLI novos (`propor-inserts`, `aprovar-plano`, `aprovar-master`, `aprovar-transcript`, `aprovar-legenda`, `acelerar`, `desfaz`)
- Detecção automática v1 vs v2 via `workflow_state.json`
- Production Safety Net com 8 quality_gates automáticos
- `desfaz` permite voltar até 3 gates consecutivos
- Fluxo documentado neste mesmo arquivo (SKILL.md)

**v1.0.0-MVP** (2026-05-10) — primeira versão funcional (pipeline linear 7 fases):
- Sub-fluxo (a) talking head 9:16
- Estilo Premium Cinematic ⭐
- Caption A (Clean) — stub no MVP
- 8 skills filhas reais + 14 stubs
- Library cache estilo claude-watch
- Modo conversacional (5+6+7)
- FIXES P0/P1 do code review aplicados
